# NMBOT / Ирина — как устроен бот

Дата: 2026-07-02

Статус: архитектурная документация. Документ описывает текущую рабочую схему Ирины: поиск через MCP, stage-presenter, sales_phrase, операторскую воронку и правила deploy.

## 1. Один источник правды

Продовая версия бота живёт на VPS:

```text
server:  neiro@193.107.155.236:1905
path:    /home/neiro/novostroy-bot
service: novostroy-bot.service
```

Локальный рабочий стенд проекта:

```text
/tmp/opencode-run-nmbot/project
```

В локальном стенде основные UX-правки и проверки идут через:

```text
scripts/chat_tester_bot.py
```

Важно: если правка влияет на ответы Ирины, routing, state, MCP/search parsing, visible options или операторскую воронку, она считается незавершённой, пока не проверена на VPS.

## 2. Главные компоненты

```text
Telegram
  → bot handler
  → state / dialog memory
  → stage orchestrator
  → MCP/search через Overmind + OpenRouter
  → normalizer
  → stage presenter
  → sales_phrase layer
  → validator
  → Telegram response
```

## 3. Внешние сервисы

### 3.1 Telegram

Telegram — клиентский канал. Бот получает сообщения клиента, хранит состояние диалога и отправляет ответы.

### 3.2 Overmind gateway

Все LLM/MCP-запросы идут через gateway-agent Overmind:

```text
Overmind gateway → OpenRouter → model / MCP novostroym
```

### 3.3 OpenRouter

Через OpenRouter вызываются модели:

```text
search model: google/gemini-3.1-flash-lite-preview
default chat model: google/gemini-2.5-flash
sales phrase model: google/gemini-3.5-flash
```

В тестах также использовались:

```text
openai/gpt-5.4-mini
openai/gpt-5.5
openai/gpt-4o
google/gemini-3.1-flash-lite-preview
```

Вывод по моделям: для live-бота оптимальный баланс скорости и качества сейчас даёт `google/gemini-3.5-flash` в роли `sales_phrase` модели. Более умные GPT-модели давали хорошие углы, но были медленнее для живого Telegram.

### 3.4 MCP novostroym

MCP `novostroym` — источник фактов о новостройках.

Бот не должен придумывать факты. Всё, что попадает в ответ клиенту, должно прийти из MCP/search или быть безопасным смыслом из этих фактов.

Пример:

```text
MCP fact: рядом Мещерский парк и Чоботовский лес
Allowed comment: будет проще чаще гулять с детьми на свежем воздухе
```

Нельзя:

```text
MCP не дал парк → бот всё равно пишет “рядом парк”
```

## 4. Состояние диалога

Бот хранит state по пользователю.

Ключевые поля:

```json
{
  "params": {},
  "last_search_response": {},
  "last_options": [],
  "visible_options": [],
  "selected_option": null,
  "last_offer_type": null,
  "last_answer_kind": null,
  "awaiting_phone": false,
  "search_model": "google/gemini-3.1-flash-lite-preview",
  "chat_model": "google/gemini-2.5-flash"
}
```

### Что значит каждое поле

| Поле | Зачем нужно |
|---|---|
| `params` | текущие параметры клиента: район, бюджет, комнатность, цель |
| `last_search_response` | полный ответ MCP/search |
| `last_options` | нормализованные ЖК из последнего поиска |
| `visible_options` | ЖК, реально показанные клиенту в последнем списке |
| `selected_option` | ЖК, который клиент выбрал |
| `awaiting_phone` | бот ждёт номер телефона |
| `last_offer_type` | что бот предложил на прошлом шаге |
| `last_answer_kind` | тип прошлого ответа |

## 5. Основной поток первого подбора

Пример запроса:

```text
нужна двушка для семьи
```

Поток:

```text
1. Handler получает текст.
2. Orchestrator определяет stage = first_list.
3. Search model вызывает MCP novostroym.
4. MCP возвращает facts[] / near[] / params / missing.
5. Bot сохраняет last_search_response и last_options.
6. Stage presenter берёт максимум 3 ЖК.
7. Bot запускает background enrichment для top-3 ЖК (MCP/search по точному ЖК + сценарию) и кладёт результат в `state['enriched_options']`.
8. Sales phrase layer делает короткий комментарий к каждому ЖК.
9. Validator проверяет факты и стиль.
10. Bot отправляет нумерованный список и вопрос выбора ЖК.
```

Целевой формат:

```text
Подобрала три варианта для семьи.

1. ЖК «Лучи» — Солнцево, дом уже сдан, есть квартиры с отделкой, цены от 10,89 млн рублей.
   Рядом Мещерский парк и Чоботовский лес — будет проще чаще гулять с детьми на свежем воздухе.

2. ...

Какой ЖК хотите рассмотреть подробнее?
```

## 6. Stage orchestrator

Orchestrator решает не текст ответа, а стадию диалога.

Жёсткое правило слоя: **вся семантика пользовательского намерения распознаётся оркестратором**.
Нельзя чинить смысловые сценарии расширением regex в нижнем router/classifier.

Regex / deterministic guards допустимы только для механики, где нет смысла для распознавания:

- ввод номера телефона;

Всё остальное — LLM-orchestrator. Даже если фраза выглядит простой (`1`, `второй`, `подбери похожие`, `не надо`, `давай другой`, `хочу оператора`), это уже смысловой сценарий, а не кодовый regex.
Выбор варианта по номеру или названию тоже решает orchestrator: он должен вернуть exact `selected_option_name` из `visible_options`.

Фразы вроде `подбери похожие`, `найди похожие`, `ещё такие`, `похожие варианты`, `другие варианты` — это не механика. Это семантический запрос на новый сценарий подбора, поэтому его должен выбрать LLM-orchestrator.

```text
message + state → stage decision → presenter
```

Основные стадии:

| Stage | Когда | Presenter |
|---|---|---|
| `first_list` | клиент просит подобрать квартиру | `render_first_list` |
| `selected_object` | клиент выбрал ЖК из списка | `render_selected_object` |
| `operator_handoff` | клиент просит наличие, этажи, бронь, ипотеку, оператора | `render_operator_handoff` |
| `phone_capture` | клиент прислал телефон | code-level capture, без LLM |
| `refinement` | клиент уточняет бюджет, район, отделку | `render_refinement` или новый MCP-search |
| `comparison` | клиент просит сравнить варианты | `render_comparison` |
| `expand_more_options` | клиент семантически просит ещё похожие/другие варианты после shortlist | свежий MCP/search + `render_first_list` |
| `freeform_assist` | нестандартное сообщение | ограниченный LLM-ответ + validator |

Важно: внутри follow-up routing есть две разные логики, и их нельзя смешивать. Но выбрать между ними должен **orchestrator**, а не regex по отдельным фразам:

- `compare_others` — явное сравнение текущего сохранённого списка, без нового широкого поиска;
- `expand_more_options` — запрос на «ещё/похожие/другие варианты», который запускает свежий MCP/search и выкидывает уже показанные ЖК из результата.

Если lower-level router видит смысловую фразу, но orchestrator не выбрал stage/action, правильная реакция — считать это ошибкой orchestration contract, а не добавлять ещё один regex.

## 7. Stage presenter

Stage presenter собирает клиентский ответ по правилам конкретной стадии.

### 7.1 first_list

Задача: показать 2–3 ЖК и привести клиента к выбору одного ЖК.

Правила:

- максимум 3 ЖК;
- только список `1./2./3.`;
- каждый пункт: факты + одна польза;
- один финальный вопрос;
- без оператора, если варианты уже найдены.

Финальный вопрос:

```text
Какой ЖК хотите рассмотреть подробнее?
```

### 7.2 selected_object

Задача: клиент уже заинтересовался конкретным ЖК, значит надо коротко презентовать объект и вести к оператору.

Правила:

- не делать новый широкий MCP-поиск;
- использовать `selected_option` / `last_options`;
- если в `state['enriched_options']` уже есть обогащённая карточка, использовать её;
- если enrichment ещё не готов, выполнить короткий точечный enrichment по выбранному ЖК;
- дать 2–3 коротких абзаца;
- закончить операторским вопросом.

### 7.2.1 follow-up expansion after first_list

Если клиент после первого списка просит `подбери похожие`, `найди похожие`, `ещё такие`, `ещё варианты`, `похожие варианты`, `другие варианты` или `альтернативы`, бот не должен повторять тот же shortlist.

Это решение принимает stage orchestrator: он должен вернуть stage/action `expand_more_options` и `needs_mcp_search=true`. Нижний router не должен угадывать эту семантику regex’ом.

Правила:

- сделать свежий MCP/search с теми же или близкими условиями;
- исключить уже показанные ЖК из `visible_options` / `last_options`;
- показать новый shortlist максимум из 3 вариантов;
- если найден ровно 1 новый ЖК — можно перейти в `selected_object`;
- если клиент пишет `сравни` / `чем отличаются` / `разница` — это отдельная ветка `comparison`, а не expansion.

Цель:

- не крутить пользователя по кругу;
- расширять выбор, когда человек явно просит ещё похожие варианты;
- сравнение оставить только для уже показанных ЖК.

Fail-кейс:

- `подбери похожие` → clarification “продолжить подбор или изменить условия?”;
- повторный `подбери похожие` → тот же shortlist.

Почему fail: intent был понятен из контекста. Оркестратор должен выбрать fresh expansion, а не `continue_selection`.

Финальный вопрос:

```text
Хотите, позвать оператора проверить актуальные квартиры по этому ЖК?
```

### 7.3 operator_handoff

Задача: если клиент спрашивает наличие, этажи, корпуса, планировки, бронь, ипотеку, скидки или прямо просит оператора — не выдумывать, а вести к человеку.

Пример:

```text
Это уже лучше проверить по актуальной базе. Оператор сможет посмотреть конкретные квартиры, наличие и условия.

Оставите номер телефона?
```

### 7.4 phone_capture

Если клиент прислал номер телефона, бот не отправляет его в LLM.

Поток:

```text
message → phone detector → save phone → farewell
```

Ответ:

```text
Спасибо, номер получила. Передам оператору ваш запрос вместе с тем, что уже обсудили, чтобы не начинать всё заново.
```

## 7.5 option enrichment / selected ЖК enrichment

Это дополнительный слой между first_list и selected_object.

Идея:

```text
first_list показал top-3 ЖК
  → bot фонит enrichment по каждому top-3 ЖК
  → selected_object берёт enriched card, если она уже готова
  → иначе делает короткий точечный enrichment перед ответом
```

Что кладём в enriched card:

- developer;
- location;
- metro / transport;
- rooms;
- area;
- price range;
- finishing;
- readiness;
- infrastructure (школы, сады, парки, двор без машин, магазины, аптеки, сервисы, если MCP их дал).

Что это даёт:

- selected-object ответ становится богаче, чем короткая карточка из first_list;
- бот может показать именно те факты, которые важны для семьи / метро / бюджета;
- если enrichment не успел прийти, бот всё равно отвечает безопасно по базовой карточке.

## 8. Sales phrase layer

`sales_phrase` — маленький LLM-слой, который пишет только одну короткую пользу по semantic card.

Он не пишет весь ответ.

Поток:

```text
option facts + scenario + allowed angles
  → sales_phrase model
  → benefit sentence
  → validator
  → presenter inserts benefit into answer
```

### Конфиг

```text
NMBOT_SALES_PHRASE=1
NMBOT_SALES_PHRASE_MODEL=google/gemini-3.5-flash
NMBOT_SALES_PHRASE_TEMPERATURE=0.2
```

### Что получает модель

```json
{
  "scenario": "family",
  "items": [
    {
      "idx": 1,
      "object": "ЖК «Лучи»",
      "facts": [
        "Солнцево",
        "дом уже сдан",
        "есть квартиры с отделкой",
        "рядом Мещерский парк и Чоботовский лес"
      ],
      "allowed_angles": [
        "рядом есть место для прогулок с детьми",
        "отделка уменьшает ремонтные хлопоты",
        "готовый дом проще планировать для переезда"
      ]
    }
  ]
}
```

### Что возвращает модель

```json
{
  "items": [
    {
      "idx": 1,
      "benefit": "Рядом Мещерский парк и Чоботовский лес — будет проще чаще гулять с детьми на свежем воздухе."
    }
  ]
}
```

## 9. Scenario comment enrichment

Это следующий слой поверх `sales_phrase`.

Он описан отдельно:

```text
docs/SCENARIO_COMMENT_ENRICHMENT_TZ.md
```

Идея:

```text
scenario + MCP fact → allowed meaning → короткий клиентский комментарий
```

Пример:

```text
family + park/forest → будет проще гулять с детьми на свежем воздухе
investment + min_price → понятная точка входа для сравнения
metro_access + metro_walk_minutes → удобно ездить каждый день без машины
```

## 10. MCP/search

Search-фаза должна вернуть структурированный JSON:

```json
{
  "facts": [],
  "near": [],
  "missing": "",
  "params": {}
}
```

Главное правило: search должен копировать все полезные MCP-поля, а не только `name/location/price`.

Полезные поля:

```text
name
location
price_range
min_price
max_price
finishing
ready
delivered
area
metro
why_close
infrastructure
infrastructure_family
schools
kindergartens
parks
clinics
playgrounds
shops
services
```

Если поле не пришло — bot не использует его в ответе.

## 11. Validator

Validator нужен, чтобы модель не испортила ответ.

Проверки:

- нет фактов вне MCP/search;
- нет технических слов `MCP`, `в базе`, `по данным`, `сдача/готовность`;
- нет рекламных слов `лучший`, `идеальный`, `выгодный`, `перспективный`, `премиальный`;
- нет инвестиционных обещаний `доходность`, `аренда`, `ликвидность`, `рост цены`, `окупаемость`;
- если комментарий говорит про парк — парк должен быть во входных фактах;
- если говорит про школу/сад/поликлинику/метро — эти факты должны быть во входе;
- в first_list ровно один финальный вопрос;
- в selected_object должен быть операторский вопрос;
- не больше 3 ЖК в первом списке;
- у каждого ЖК отдельная польза.

Если validator отклоняет LLM-фразу, presenter использует безопасный fallback из карты фактов.

## 12. Модели и настройки

### Search

```text
google/gemini-3.1-flash-lite-preview
```

Задача: вызвать MCP и вернуть факты.

### Chat / default

```text
google/gemini-2.5-flash
```

Используется как базовая модель общения в старом контуре.

### Sales phrase

```text
google/gemini-3.5-flash
temperature: 0.2
```

Задача: одна короткая польза по semantic card.

## 13. Команды и диагностика

### Жёсткое правило MCP/search

Для любого клиентского запроса о квартире, новостройке, ЖК или подборе вариантов бот не должен отвечать «из головы».

```text
квартирный запрос → обязательный MCP/search → нормализация фактов → ответ Ирины
```

Это правило фиксируется на уровне search prompt: все цены, площади, сроки, отделка, инфраструктура и варианты должны приходить из инструментального поиска. Если запрос неполный, поиск всё равно выполняется по доступным параметрам, а недостающие условия возвращаются как missing/params.

### Визуальная карта сценариев

```text
docs/BOT_SCENARIO_MAP.html
```

Это standalone HTML-схема для человека: блоки, стрелки, сценарии Stage 0/1/2/3/4/4.5/5/6, ветки `recommend_options` и operator handoff, реальные MCP/search поля, state-память, рабочие промты и примеры ответов.

Документ нужен, чтобы быстро понять текущую логику бота без чтения всего `scripts/chat_tester_bot.py`.

### История диалога в Telegram

### Публичный веб‑обзор проекта

Для просмотра проекта целиком используется тот же публичный статический сервис, где уже лежит MPN quality dashboard:

```text
http://193.107.155.236:8765/
```

Сборщик:

```text
scripts/build_public_overview.py
```

Он собирает:

- общий список сервисов `/index.html`;
- раздел NMBOT `/nmbot-project-7f3a9c/index.html`;
- вкладку новой архитектуры `/nmbot-project-7f3a9c/architecture-v2.html` по ТЗ `docs/LLM_DECISION_ARCHITECTURE_TZ.md`;
- встроенную блок‑схему `/nmbot-project-7f3a9c/map.html`.
- вкладку истории `/nmbot-project-7f3a9c/index.html#history`, которая читает `/nmbot-project-7f3a9c/history.json` и обновляется в браузере каждые 10 секунд.

В NMBOT overview попадают только файлы из allow-list: ТЗ, архитектура, сценарии, активные промты и release/checklist docs. Секреты, `.env`, логи и backups не публикуются.

Публичная история диалогов собирается отдельным санитайзером:

```text
scripts/publish_public_history.py
```

Он читает `logs/dialogs-YYYY-MM-DD.jsonl`, публикует только компактные поля `user`, `bot`, `intent`, `plan`, `MCP/search`, `buttons`, `cost`, маскирует телефоны/email/токены и обрезает длинные значения.

```text
/history [N]
/hisotry [N]
```

Обе команды показывают последние ответы бота для текущего Telegram-пользователя. `/hisotry` оставлен как alias с опечаткой, потому что пользователь попросил именно такую команду.

Источник данных — существующие dialog logs:

```text
logs/dialogs-YYYY-MM-DD.jsonl
```

В ответ попадает компактный trace:

```text
Вы: клиентский запрос
Бот: ответ Ирины
intent: выбранный dialog_intent
plan: dialog planner state patch, если был
MCP/search_response: компактный search/MCP ответ
buttons: отправленные кнопки, если были
cost: cost/debug мета, если была
```

Вывод ограничивается и режется на Telegram-safe chunks, чтобы длинный MCP/search JSON не ломал сообщение.

### Локальная проверка синтаксиса

```bash
python3 -m py_compile scripts/chat_tester_bot.py
```

### Стоимость OpenRouter

```bash
python3 scripts/or_cost.py
```

### Продовый статус

```bash
ssh -p 1905 neiro@193.107.155.236 \
  "systemctl --user status novostroy-bot.service --no-pager"
```

### Продовый лог

```bash
ssh -p 1905 neiro@193.107.155.236 \
  "tail -30 /home/neiro/novostroy-bot/logs/bot.log"
```

## 14. Deploy gate

Для любых изменений в ответах Ирины:

```text
1. Локально: py_compile.
2. Локально: smoke на реальных сценариях.
3. Backup runtime-файлов на VPS.
4. Upload на VPS.
5. Remote py_compile.
6. Restart novostroy-bot.service.
7. Проверить markers/status/logs на VPS.
8. Проверить prod smoke или live Telegram.
9. Показать scripts/or_cost.py.
```

Запрещено говорить “готово”, если проверена только локальная версия.

## 15. Основные документы

| Документ | Что описывает |
|---|---|
| `docs/IDEAL_IRINA_UX.md` | эталон UX Ирины |
| `docs/IRINA_DIALOGUE_MAP_V1.md` | стадии диалога и presenters |
| `docs/SCENARIO_COMMENT_ENRICHMENT_TZ.md` | сценарные комментарии по MCP-фактам + enrichment selected ЖК |
| `docs/reason_layer_hypothesis_conclusions_2026-07-02.md` | выводы по reason-layer гипотезам |
| `docs/EXPERIMENTS.md` | журнал гипотез, правил deploy и проверок |
| `prompts/search_v1.txt` | правила MCP/search фазы |
| `prompts/chat_v1.txt` | базовые правила chat-фазы |

## 16. Что сейчас считается хорошим ответом

### Первый список

```text
Подобрала три варианта для семьи.

1. ЖК «Лучи» — Солнцево, дом уже сдан, есть квартиры с отделкой, цены от 10,89 млн рублей.
   Рядом Мещерский парк и Чоботовский лес — будет проще чаще гулять с детьми на свежем воздухе.

2. ...

Какой ЖК хотите рассмотреть подробнее?
```

### Выбранный ЖК

```text
ЖК «Лучи» — Солнцево, дом уже сдан. Есть квартиры с отделкой, площади от 22,5 до 86,5 м².

Рядом Мещерский парк и Чоботовский лес — будет проще чаще гулять с детьми на свежем воздухе.

Хотите, позвать оператора проверить актуальные квартиры по этому ЖК?
```

### Телефон

```text
Спасибо, номер получила. Передам оператору ваш запрос вместе с тем, что уже обсудили, чтобы не начинать всё заново.
```

## 17. Что запрещено

- `сдача/готовность` в клиентском тексте;
- `верхняя точка бюджета`;
- `по данным`, `в базе`, `MCP`;
- “лучший”, “идеальный”, “выгодный”, “перспективный”, “премиальный”;
- доходность, аренда, ликвидность, рост цены;
- парк, школа, сад, поликлиника, метро, если этого нет в facts;
- обещание наличия, этажа, корпуса, скидки, ипотеки без оператора;
- больше одного финального вопроса;
- оператор в первом списке, если варианты уже найдены.

## 18. Куда развивать дальше

1. Довести `scenario comment enrichment` до кода.
2. Расширить `search_v1.txt`, чтобы broad family/infrastructure queries чаще возвращали инфраструктуру.
3. Добавить тесты на сценарии:
   - family + park/school/clinic;
   - investment без доходности;
   - metro only when metro fact exists;
   - selected_object → operator;
   - phone capture without LLM.
4. Добавить quality report по live dialogs: структура, отступы, польза, финальный вопрос, operator funnel.
