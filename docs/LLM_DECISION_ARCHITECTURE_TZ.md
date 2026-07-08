# ТЗ: новая архитектура принятия решений Ирины

Дата: 2026-07-03

Статус: проект архитектуры для чтения и обсуждения. Это **не инструкция к немедленному deploy**, а целевая схема, как перестроить принятие решений так, чтобы LLM было проще и безопаснее отвечать клиентам.

## 1. Зачем нужна новая архитектура

Последние живые диалоги показали несколько повторяющихся проблем:

1. Бот иногда показывает клиенту сырой технический объект, например словарь инфраструктуры.
2. `near[]` иногда звучит как точное совпадение, хотя это только близкий вариант с отличием.
3. На широкий запрос без бюджета бот может несколько раз подряд просить бюджет вместо того, чтобы честно показать широкий стартовый подбор.
4. На вопрос “по каким критериям сравнивал?” бот может снова показать список, хотя клиент спрашивает именно логику сравнения.
5. По финансовым темам вроде “без ПВ” или “траншевая ипотека” бот рискует отвечать общими банковскими советами, а не фактами из MCP/search.
6. Нестандартные вопросы могут провоцировать выдумки: например, объяснение названия ЖК без факта в данных.

Корневая причина: LLM получает слишком много сырого контекста и сама должна одновременно:

- понять намерение клиента;
- разобрать MCP/search;
- отличить точные варианты от близких;
- вспомнить UX-запреты;
- выбрать сценарий;
- написать живой ответ.

Новая архитектура должна разделить эти задачи.

## 2. Главный принцип

LLM не должна быть парсером сырого MCP и хранителем всех правил.

Она должна получать короткую, безопасную карточку ситуации:

```json
{
  "user_message": "по каким критериям сравнивал?",
  "stage": "comparison_followup",
  "search_summary": {
    "facts_count": 0,
    "near_count": 1,
    "has_exact": false,
    "has_near": true
  },
  "visible_options": [
    {
      "name": "ЖК Лучи",
      "source": "near",
      "match_status": "near_only",
      "client_facts": [
        "Солнцево",
        "метро 5 минут пешком",
        "есть отделка"
      ],
      "why_close": "двухкомнатные начинаются выше бюджета 15 млн"
    }
  ],
  "risk_flags": ["near_only", "must_explain_difference"],
  "allowed_actions": ["explain_comparison_criteria", "show_near_with_difference"],
  "recommended_action": "explain_comparison_criteria"
}
```

То есть LLM видит не “весь мир”, а подготовленную задачу.

## 3. Целевая схема

```text
Клиент пишет сообщение
        ↓
Dialog Memory / State
        ↓
Intent Planner LLM
        ↓
Search Decision
        ↓
MCP/search novostroym, если нужен
        ↓
Normalizer
        ↓
Decision Context Builder
        ↓
Action Resolver
        ↓
Presenter
        ↓
Safety Validator
        ↓
Ответ Ирины в Telegram
```

## 4. Компоненты

### 4.1. Dialog Memory / State

State хранит техническую память диалога:

```json
{
  "params": {},
  "last_search_response": {},
  "last_options": [],
  "visible_options": [],
  "selected_option": null,
  "awaiting_phone": false,
  "last_offer_type": null,
  "last_answer_kind": null,
  "operator_context": null
}
```

Правило: state не отдаётся клиенту и не должен напрямую превращаться в текст. Это техническая память.

### 4.2. Intent Planner LLM

Planner LLM отвечает только на вопрос: **что клиент сейчас хочет сделать?**

Она не пишет клиентский ответ.

Пример ответа planner:

```json
{
  "intent": "explain_comparison_criteria",
  "confidence": 0.92,
  "needs_search": false,
  "reason": "Клиент спрашивает, по каким критериям сравнивались варианты"
}
```

Допустимые intent-группы:

| Intent | Когда |
|---|---|
| `new_search` | новый квартирный запрос |
| `wide_search` | клиент разрешил широкий поиск без бюджета/района |
| `update_search` | клиент уточняет параметры |
| `select_option` | клиент выбрал ЖК по номеру или названию |
| `expand_more_options` | клиент просит похожие/другие варианты |
| `recommend_options` | клиент спрашивает совет |
| `compare_options` | клиент просит сравнить ЖК |
| `explain_comparison_criteria` | клиент спрашивает, почему/по каким критериям выбраны варианты |
| `operator_request` | клиент просит оператора/звонок/показ/наличие |
| `finance_terms` | без ПВ, рассрочка, траншевая ипотека, ипотечные условия |
| `real_estate_related_unknown` | около недвижимости, но сценарий нестандартный |
| `off_topic` | совсем не по теме бота |
| `unclear` | непонятная фраза, шум, опечатка |

Важно: семантику живой фразы определяет planner LLM, а не regex в коде.

### 4.3. Search Decision

После planner система решает, нужен ли MCP/search.

Правила:

| Ситуация | MCP/search |
|---|---|
| Новый квартирный запрос | обязателен |
| Широкий поиск без бюджета | обязателен, по доступным параметрам |
| Уточнение, которое нельзя закрыть памятью | нужен |
| “по каким критериям сравнивал?” | не нужен, ответ из памяти |
| “твой совет?” при наличии visible_options | не нужен |
| selected ЖК уже есть в памяти | не нужен, кроме точечного enrich |
| вопрос вне темы | не нужен |
| финансовая тема без конкретного ЖК | поиск по программам/условиям только если MCP это поддерживает; иначе честно сказать, что подтверждения нет |

Жёсткое правило: если это запрос о квартире, новостройке, ЖК или подборе вариантов, данные берутся только из MCP/search или уже сохранённой памяти, которая ранее пришла из MCP/search.

### 4.4. MCP/search

Search возвращает только структурированный результат:

```json
{
  "facts": [],
  "near": [],
  "missing": [],
  "params": {}
}
```

Если модель поиска упомянула ЖК в свободном тексте, но не положила его в `facts[]` или `near[]`, этот ЖК нельзя показывать клиенту как вариант.

### 4.5. Normalizer

Normalizer превращает сырой MCP/search в безопасные option-карточки.

Каждая option-карточка обязана иметь:

```json
{
  "name": "ЖК Лучи",
  "source": "facts",
  "match_status": "exact",
  "location": "Солнцево",
  "price": "от 10,9 млн",
  "area": "от 22,5 до 86,5 м²",
  "ready": "дом сдан",
  "finishing": "есть отделка",
  "metro": "5 минут пешком",
  "why_close": null,
  "client_facts": [
    "Солнцево",
    "метро 5 минут пешком",
    "есть отделка",
    "дом сдан"
  ],
  "missing": [],
  "do_not_say": []
}
```

Для `near[]`:

```json
{
  "name": "ЖК Лучи",
  "source": "near",
  "match_status": "near_only",
  "why_close": "двухкомнатные начинаются от 19 млн, выше бюджета 15 млн",
  "client_facts": [
    "Солнцево",
    "метро 5 минут пешком",
    "есть отделка"
  ],
  "missing": [
    "нет подтверждения двухкомнатных до 15 млн"
  ],
  "do_not_say": [
    "точно подходит под бюджет",
    "есть двухкомнатные до 15 млн"
  ]
}
```

Ключевые поля:

- `source`: откуда вариант — `facts` или `near`;
- `match_status`: точность совпадения;
- `why_close`: чем близкий вариант отличается;
- `client_facts`: готовые безопасные фразы для клиента;
- `missing`: чего не хватает;
- `do_not_say`: что нельзя утверждать.

### 4.6. Decision Context Builder

Это главный новый слой.

Он получает:

- user message;
- planner intent;
- state;
- normalized options;
- search_response;
- UX-правила.

И собирает компактный контекст:

```json
{
  "user_message": "Я пока без бюджета посмотрю",
  "stage": "wide_search",
  "params": {
    "budget": null,
    "rooms": null,
    "district": null
  },
  "search_summary": {
    "facts_count": 3,
    "near_count": 0,
    "has_exact": true,
    "has_near": false,
    "has_missing": true
  },
  "visible_options": [],
  "selected_option": null,
  "risk_flags": [
    "budget_missing",
    "wide_search_allowed"
  ],
  "allowed_actions": [
    "show_wide_starting_options",
    "ask_one_clarification"
  ],
  "recommended_action": "show_wide_starting_options",
  "final_question_policy": "one_question_only"
}
```

Decision Context Builder не пишет ответ. Он подготавливает условия, чтобы ответ был безопасным.

### 4.7. Action Resolver

Action Resolver проверяет, допустим ли выбранный action.

Примеры:

| LLM выбрала | Но в контексте | Resolver делает |
|---|---|---|
| `show_exact_options` | `facts_count=0`, `near_count>0` | меняет на `show_near_with_difference` |
| `offer_operator` | это первый ответ и варианты уже есть | меняет на `show_options` или `ask_one_clarification` |
| `compare_options` | клиент спросил “по каким критериям” | меняет на `explain_comparison_criteria` |
| `answer_from_context` | нужного факта нет | меняет на `say_missing_or_offer_operator` |

Это не перенос семантики в код. Семантику выбрала LLM. Код только проверяет безопасность.

### 4.8. Presenter

Presenter получает финальное действие и готовые facts.

Основные presenters:

| Presenter | Что делает |
|---|---|
| `render_first_list` | 2–3 точных варианта |
| `render_near_only` | честно показывает близкие варианты с отличиями |
| `render_selected_object` | карточка выбранного ЖК |
| `render_recommendation` | советует один вариант из видимых |
| `render_comparison_criteria` | объясняет критерии сравнения |
| `render_wide_search` | широкий список без бюджета |
| `render_finance_terms` | честный ответ по без ПВ/траншевой ипотеке |
| `render_operator_handoff` | просит телефон и сохраняет контекст |
| `render_off_topic_boundary` | мягко возвращает в рамки бота |
| `render_unclear_clarification` | задаёт один уточняющий вопрос |

Часть presenters может быть полностью deterministic. LLM нужна только там, где важно написать более живую фразу, но не там, где нужна строгая безопасность.

### 4.9. Safety Validator

Перед отправкой ответа validator проверяет:

```text
☐ нет сырого JSON/dict: { 'schools': ... }
☐ если source=near, в ответе есть отличие / why_close
☐ если facts=[] и near=[], нет названий ЖК из markdown
☐ максимум 3 ЖК в первом списке
☐ ровно один финальный вопрос
☐ нет “лучший”, “идеальный”, “выгодный”, если это не подтверждено
☐ нет обещаний актуального наличия квартир
☐ нет оператора слишком рано
☐ нет финансовых утверждений без MCP-факта
☐ нет технических слов MCP/search/JSON для клиента
```

Если проверка не проходит, ответ не отправляется как есть. Система использует safe fallback.

## 5. Как будут работать нестандартные вопросы

Нестандартные вопросы не нужно прописывать по одному.

Нужно классифицировать их по типу:

| Тип | Пример | Поведение |
|---|---|---|
| `real_estate_related_unknown` | “почему ЖК Дюна так называется?” | ответить только если факт есть; иначе честно сказать, что в данных нет |
| `operator_or_process` | “куда звонить?”, “как посмотреть?” | оператор или минимальное уточнение контекста |
| `off_topic` | “по чём рубероид в Одессе?” | коротко вернуть в рамку Москвы/МО и новостроек |
| `unclear` | “мне что встать?” | один уточняющий вопрос |

Пример:

```text
Клиент: в жк дюна зыбучие пески, поэтому он так называется?
```

Плохой ответ:

```text
Название связано с маркетинговой концепцией проекта.
```

Почему плохо: если MCP/search этого не дал, это выдумка.

Хороший ответ:

```text
В данных по ЖК я не вижу объяснения названия. Могу рассказать, что по самому проекту известно: район, срок, цены и отделка. Хотите?
```

## 6. Как это исправляет найденные ошибки

### 6.1. Сырой dict в ответе

Normalizer превращает словари инфраструктуры в `client_facts`. Safety Validator запрещает `{}`, `'schools'`, `'kindergartens'` в клиентском тексте.

### 6.2. `near[]` как точное совпадение

Option получает `source=near`, `match_status=near_only`, `why_close`. Presenter `render_near_only` обязан сказать отличие.

### 6.3. Широкий запрос без бюджета

Planner выбирает `wide_search`, Decision Context ставит `wide_search_allowed`, Presenter показывает широкий стартовый список и честно предупреждает, что цены будут сильно разные.

### 6.4. `facts=[]`, но ЖК названы в markdown

Normalizer берёт только `facts[]` и `near[]`. Validator запрещает презентовать ЖК, которых нет в structured options.

### 6.5. “по каким критериям сравнивал?”

Planner выбирает `explain_comparison_criteria`, Presenter объясняет критерии по текущему списку: цена, отделка, срок, локация, метро, инфраструктура — только если эти поля есть.

### 6.6. Без ПВ / траншевая ипотека

Planner выбирает `finance_terms`. Decision Context указывает, есть ли MCP-подтверждение программ. Если нет, Presenter говорит: “в данных нет подтверждённой программы”, и предлагает оператора или подбор ЖК с акциями/рассрочкой.

## 7. Минимальный MVP внедрения

Чтобы не ломать текущий бот, внедрять по этапам.

### Этап 1. Context fields без изменения поведения

Добавить в option:

```json
{
  "source": "facts|near",
  "match_status": "exact|near_only|weak",
  "client_facts": [],
  "missing": [],
  "do_not_say": []
}
```

Логировать это в trace, но пока не менять ответы.

### Этап 2. Safety fixes

Исправить безопасные вещи:

- raw dict formatter;
- phone/history sanitizer;
- запрет названий ЖК из неструктурированного markdown;
- обязательный `why_close` для near.

### Этап 3. Decision Context Builder MVP

Добавить функцию:

```python
build_decision_context(user_text, state, intent, search_response, options) -> dict
```

Покрыть 5 сценариев:

- first_list;
- near_only;
- wide_search;
- comparison_criteria;
- finance_terms.

### Этап 4. Safe presenters

Добавить presenters:

- `render_near_only_response`;
- `render_comparison_criteria_response`;
- `render_wide_search_response`;
- `render_finance_terms_response`;
- `render_unknown_question_response`.

### Этап 5. Validator

Добавить минимальный validator клиентского текста.

Если fail — использовать safe fallback и писать причину в trace.

### Этап 6. Tests and prod gate

Regression tests:

1. raw dict never shown;
2. near always says difference;
3. wide search without budget does not ask budget again;
4. criteria question explains criteria;
5. no ЖК from markdown when `facts=[]` and `near=[]`;
6. finance terms do not invent bank rules;
7. unknown real-estate question does not hallucinate;
8. history sanitizer preserves prices but masks phones.

Проверка:

```text
py_compile → h029 → targeted scenario tests → VPS deploy → prod smoke → or_cost
```

## 8. Новые файлы

Целевой вариант:

```text
scripts/dialog_context.py
scripts/response_presenters.py
scripts/response_validators.py
```

Переходный вариант с меньшим риском:

```text
scripts/chat_tester_bot.py
```

Сначала добавить функции туда, где уже живёт stage presenter, а после стабилизации вынести в отдельные файлы.

## 9. Логирование

Каждый ответ должен сохранять в trace:

```json
{
  "planner_intent": "...",
  "decision_context": {...},
  "resolved_action": "...",
  "validator": {
    "passed": true,
    "warnings": []
  },
  "source_options": ["facts", "near"]
}
```

Это позволит в публичной истории видеть не только “что ответил бот”, но и “почему он так решил”.

## 10. Граница ответственности LLM и кода

LLM отвечает за:

- понимание живой фразы клиента;
- выбор intent/action из разрешённого списка;
- живую формулировку там, где это безопасно.

Код отвечает за:

- вызов MCP/search;
- нормализацию фактов;
- source/match статус;
- запреты `do_not_say`;
- safety validation;
- защиту от сырого JSON;
- сохранение trace;
- operator/phone state.

Коротко:

```text
LLM понимает смысл.
Код защищает факты и формат.
Presenter делает ответ человеческим.
Validator не выпускает опасный ответ.
```

## 11. Критерий готовности

Архитектура считается внедрённой, когда:

- в trace есть `decision_context` и `resolved_action`;
- `near[]` никогда не звучит как точное совпадение;
- неизвестные вопросы не вызывают выдумок;
- широкие запросы без бюджета не зацикливаются на бюджете;
- вопрос о критериях сравнения объясняет критерии;
- публичная история показывает action/decision context;
- все regression tests проходят локально и на VPS.
