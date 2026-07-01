# nmbot — Experiment Loop

> Фиксирует **гипотезы, изменения и диалоги**, чтобы отслеживать прогресс бота версия-за-версией.

---

## Цель

Каждый раз, когда меняется:
- промпт (`SEARCH_SYSTEM_PROMPT` / `CHAT_SYSTEM_PROMPT`),
- модель (search/chat),
- формат ответа (parse_mode, длина, эмодзи),
- или сам pipeline (например, добавили шаг `summarize` между search и chat),

…нужно иметь возможность ответить на вопрос: *«что было до изменения и что стало после?»*.

---

## Идентификаторы

| Префикс | Что означает | Кто присваивает |
|---|---|---|
| `H###` | Гипотеза. Например: *«Если сократить CHAT_SYSTEM_PROMPT с 8 до 4 строк, бот будет отвечать быстрее без потери качества»*. | ЧАТИ вручную при старте гипотезы |
| `P###` | Версия промпта. Например: `P002` = `CHAT_SYSTEM_PROMPT` от 2026-06-25. | ЧАТИ при изменении текста промпта |
| `M###` | Версия модели. Например: `M001` = `gemini-3.1-flash-lite-preview`. | ЧАТИ при смене дефолта в коде |

Один эксперимент = одна `H###` + один или несколько `P###/M###`. Лог диалогов в `logs/` привязан к этим ID.

---

## Где что лежит

```
nmbot/
├── docs/
│   └── EXPERIMENTS.md     # ← этот файл: реестр гипотез и решений
├── logs/
│   ├── hypotheses.jsonl   # пары (h_id, описание, hypothesis_status)
│   ├── prompts.jsonl      # версии промптов (P###, текст, дата)
│   ├── dialogs-YYYY-MM-DD.jsonl   # сырой лог: по одной записи на сообщение
│   └── dialogs-YYYY-MM-DD.md      # человекочитаемый дубль по диалогам
└── scripts/
    └── chat_tester_bot.py # пишет в dialogs-*.jsonl и dialogs-*.md автоматически
```

---

## Схема записи в `dialogs-YYYY-MM-DD.jsonl`

Один JSON-объект на строку. `dialogs-*.jsonl` остаётся машинным источником правды; для чтения человеком рядом пишется `dialogs-YYYY-MM-DD.md` с тем же порядком диалогов.

Пример для текстового сообщения пользователя:

```json
{
  "ts": "2026-06-25T13:35:00.123Z",
  "kind": "user_message",
  "dialog_id": "d-2026-06-25-001",
  "turn_id": 1,
  "uid": 123456789,
  "h_id": "H001",
  "search_model": "google/gemini-3.1-flash-lite-preview",
  "chat_model": "google/gemini-2.5-flash",
  "mcp": true,
  "user_text": "Найди однушку до 8 млн в Москве",
  "dialog_intent": "main_search",
  "search_response": "...",
  "params_before": {"rooms": 1, "max_price": 8000000},
  "state_after": {"params": {"rooms": 1, "max_price": 8000000}},
  "duration_ms": 5400,
  "tokens_in": 1240,
  "tokens_out": 380,
  "cost": {"total_usd": 0.0089}
}
```

Внутри `user_message` теперь есть достаточно структуры, чтобы видеть вход, внутренний ход и выход:

- `dialog_id` / `turn_id` — связывают реплики в один диалог;
- `dialog_intent` и `dialog_plan` — что решила логика;
- `search_response` — сырой факт-вывод поиска;
- `state_after` / `params_*` — что изменилось после ответа;
- `response_text` / `buttons` / `cost` — что ушло пользователю.

## Практический ритм работы с логами

Если нужно быстро понять, что происходит в боте, пользуйся таким порядком:

1. `logs/dialogs-YYYY-MM-DD.md` — глазами смотри вход → внутреннее → ответ.
2. `python3 scripts/nmbot_quality.py --tail 20` — проверь последние ответы по codex-проверкам.
3. `python3 scripts/nmbot_response_model_eval.py export --limit 50` + `run/score` — если надо сравнить chat-модели на реальных кейсах.

Как читать сигнал:

- если в `dialogs-*.md` вход нормальный, а внутри ерунда — чинить routing / `dialog_intent` / `dialog_plan`;
- если внутри всё ок, а ответ кривой — чинить `chat_v1`, postprocess или формат ответа;
- если `nmbot_quality.py` ругается на greetings / links / markdown / JSON — чинить выходной формат и prompt ответчика;
- если `response_eval` показывает, что одна модель стабильно лучше — менять chat-модель, а не весь pipeline;
- если `response_eval` показывает слабость у всех моделей, проблема, скорее всего, выше — в `search_response`, facts или входной схеме лога.

Для одного кейса удобнее всего смотреть связку `dialog_id + turn_id`, а потом открывать ровно этот ход в JSONL и MD рядом.

Для команд (`/start`, `/model` и т.д.):

```json
{
  "ts": "2026-06-25T13:35:30.001Z",
  "kind": "command",
  "uid": 123456789,
  "h_id": "H001",
  "command": "/start"
}
```

Для выбора модели через inline-кнопку:

```json
{
  "ts": "2026-06-25T13:36:10.555Z",
  "kind": "callback",
  "uid": 123456789,
  "h_id": "H001",
  "callback": "model:deepseek/deepseek-v4-flash"
}
```

---

## Что ЧАТИ делает в начале каждой сессии

1. Читает последние 5-10 строк `logs/dialogs-*.jsonl` — понимает контекст.
2. Проверяет `docs/EXPERIMENTS.md` — какие гипотезы активны.
3. Если планируется изменение в коде:
   - присваивает `H###` и описывает гипотезу в `EXPERIMENTS.md`,
   - пишет в `logs/hypotheses.jsonl` строку с `h_id, opened_at, status=open`,
   - при изменении промпта — пишет `P###` в `logs/prompts.jsonl` со старым и новым текстом.
4. После 3+ диалогов на новой версии — фиксирует outcome в `EXPERIMENTS.md` (принято/откат/нужна доработка) и ставит `status=closed` в `hypotheses.jsonl`.

---

## Hypothesis Simulation Gate

Перед изменением UX-логики Ирины ЧАТИ сначала проверяет гипотезу в read-only симуляции.

## Prod Verification Gate

Локальная проверка не считается финальной проверкой MINION.

**Жёсткое правило:** если изменение влияет на клиентские ответы, промпты, Telegram handler, dialog state, MCP/search parsing, `visible_options`, operator handoff или follow-up routing, то результат считается незавершённым до проверки на VPS.

Обязательный порядок:

1. Локально: `py_compile`, `h029`, `ux_e2e`, `h028`, simulator/live probe.
2. Backup текущих runtime-файлов на VPS.
3. Deploy/sync runtime-файлов в `/home/neiro/novostroy-bot`.
4. Remote `py_compile` на VPS.
5. Restart `novostroy-bot.service`.
6. Проверка feature markers/status/logs на VPS.
7. Финальная проверка через prod/VPS MINION: Telegram/live logs или remote smoke, который импортирует именно `/home/neiro/novostroy-bot`.
8. `python3 scripts/or_cost.py` после live/prod проверки.

Если выполнены только локальные тесты, в отчёте обязательно писать: **«локально зелёное, prod ещё не проверен»**.

Причина правила: 2026-07-01 пользователь написал в реальный MINION после локальных зелёных тестов, но VPS крутил старый runtime. Из-за этого повторился loop `жк южные сады → да → да`: prod не имел `operator_contact_accept`, новых prompt rules и `visible_options`.

**Зачем:** не менять боевой код вслепую. Сначала надо увидеть, как будет выглядеть диалог: первый MCP-поиск, выбор ЖК, «расскажи подробнее», смешанные фразы вроде «1, можно бронь?», операторские темы. Так слабое место видно до правки `chat_tester_bot.py` или промптов.

**Базовый инструмент:**

```bash
python3 scripts/nmbot_mcp_only_sim.py
python3 scripts/nmbot_mcp_only_sim.py \
  --turn "1, расскажи подробнее" \
  --turn "1, можно бронь?"
```

**Правило:**

1. Сформулировать гипотезу поведения.
2. Прогнать её в симуляторе на MCP-данных.
3. Найти проблемы в механике и тексте.
4. Согласовать желаемое поведение.
5. Только после этого менять `chat_tester_bot.py`, промпты или тесты.
6. После правки закрепить сценарий в `scripts/nmbot_test_agent.py`.

**Что проверять в симуляции:**

- первый ответ использует только `facts[]/near[]/missing`;
- выбранный ЖК берётся из `last_options`, без нового широкого поиска;
- «расскажи подробнее» раскрывает сохранённые MCP-данные, а не зовёт оператора сразу;
- «бронь», «наличие», «этаж», «корпус», «ипотека», «актуальная цена» не придумываются LLM, а идут в detail/availability endpoint или к оператору;
- смешанные фразы вроде «1, расскажи подробнее» и «1, можно бронь?» не ломают выбор объекта.

**Формат журнала симуляции для будущих итераций:**

Каждый прогон в `logs/sim_journal-YYYY-MM-DD.md/jsonl` должен быть карточкой гипотезы, а не короткой пометкой. Минимальный состав:

1. **Гипотеза** — какое поведение проверяем и почему. Например: «если в районе нет новостроек, Ирина не советует вторичку, а предлагает расширить поиск поблизости».
2. **Источник проблемы** — ссылка на live/local лог, скрин или тест, где это всплыло: файл, строка, дата, `uid`/`h_id`, если есть.
3. **Входные данные симуляции** — полный или сокращённый `search_response`: `facts`, `near`, `missing`, `params`; отдельно — стартовый `state`, если проверяется память (`last_options`, `selected_option`, `last_answer_kind`).
4. **Команда запуска** — точная команда симулятора или fixture path: `python3 scripts/nmbot_mcp_only_sim.py ...`.
5. **Turn пользователя** — фраза или цепочка фраз, которые прогоняли.
6. **Фактический результат** — `routing`, `bot_response`, важные изменения `state`, запись `status=ok/watch/needs_patch`.
7. **Ожидаемый результат** — пример желаемого текста или действия. Не абстрактно «ответить лучше», а конкретно: какой route, какой state, какой клиентский ответ.
8. **Расхождение** — коротко, что именно не совпало: ушёл в classifier, потерял `last_options`, упомянул запрещённый факт, сделал пустой список, позвал оператора рано и т.д.
9. **Где менять** — конкретный файл и слой: `prompts/chat_v1.txt`, `scripts/chat_tester_bot.py::_resolve_dialog_intent`, `dialog_plan executor`, formatter/postprocess, state contract.
10. **Подсказка для патча** — минимальное изменение, которое должно закрыть сценарий.
11. **Acceptance criteria** — что должно стать зелёным после патча: например, no `вторичка`, no empty options-summary, route=`compare_others`, `selected_option` заполнен, journal status=`ok`.
12. **Prod gate** — если изменение влияет на ответы/routing/state, явно пометить: «локально зелёное, prod ещё не проверен» до VPS-проверки.

**Канонический шаблон записи:**

```md
## <timestamp> — MCP-only simulator run

Hypothesis: <что проверяем>
Source: <VPS/local log / screen / test>
Input:
- search_response: <facts/near/missing/params>
- state: <last_options/selected_option/last_answer_kind if relevant>
- command: <точная команда или fixture>

Turns:
- <user turn> → <routing>
- <user turn> → <routing>

Expected:
- <ожидаемый route / text / state>

Actual:
- <фактический route / text / state>

Mismatch:
- <что именно не совпало>

Patch:
- where: <file + function/branch>
- hint: <минимальный фикс>

Acceptance:
- <критерий зелёного результата>
```

**Как вести журнал проблем перед правкой бота:**

1. **Сначала зафиксировать проблему** в 1–2 фразах: что именно сломалось и в каком типе диалога.
2. **Найти реальный диалог** в `logs/dialogs-YYYY-MM-DD.jsonl` или VPS log и выписать ссылку/строку/uid.
3. **Проверить гипотезу в симуляции** через `scripts/nmbot_mcp_only_sim.py` или fixture, не правя код сразу.
4. **Записать в журнал**: input, turns, expected, actual, mismatch, patch, acceptance.
5. **Собрать patch map**: точный файл и функция/ветка, а не общий совет.
6. **Только после этого менять код** и повторять ту же симуляцию до status=`ok`.
7. **Если поведение влияет на ответы/routing/state** — держать пометку `локально зелёное, prod ещё не проверен` до VPS-проверки.

**Правило для повторных проблем:** если один и тот же класс ошибки повторяется, в журнале он получает отдельный тег/название гипотезы, а не размазывается по общему `watch`.

Пример короткой карточки:

```md
### SIM-HYP: no_results_area_expansion
- Гипотеза: если `facts=[]`, `near=[]`, но указан район, Ирина предлагает близкие районы/варианты поблизости и не советует вторичку.
- Источник: VPS `logs/dialogs-2026-07-01.jsonl:85`, Ясенево.
- Вход: `facts=[]`, `near=[]`, `missing="В Ясенево не найдено актуальных новостроек"`, `params={"district":"Ясенево"}`.
- Команда: `python3 scripts/nmbot_mcp_only_sim.py --search-json /tmp/... --turn "Подскажите, когда будет застройка в Ясенево?"`.
- Факт: bot_response=`Нашла несколько вариантов... Какой ЖК хотите рассмотреть подробнее?`, status=`needs_patch`.
- Ожидание: `По Ясенево сейчас не вижу актуальных новостроек от застройщика. Могу посмотреть близкие районы или варианты поблизости. Показать?`
- Расхождение: пустой options-summary при `facts=[]/near=[]`; возможное упоминание вторички.
- Где менять: `prompts/chat_v1.txt` no-results branch + first search formatting в симуляторе.
- Patch hint: добавить сценарий `facts=[] + near=[] + район указан`; запретить «вторичный рынок».
- Acceptance: нет слова `вторичк`, нет «нашла несколько вариантов», есть «поблизости/соседние районы», status=`ok`.
```

**Критерий перехода к коду:** симуляция показывает понятную механику и ожидаемый текст, а оставшиеся дефекты уже ясно мапятся на конкретные функции/промпты/тесты.

---

## 2026-07-01 — Scenario cards and deploy notes

### Принцип: глобальные правила отдельно, сценарные карточки отдельно

Чтобы не раздувать один общий prompt, поведение Ирины теперь проектируется в два слоя:

1. **Global Policy** — всегда действует для всех ответов: роль Ирины, только MCP/search facts, живой короткий стиль, запрет технических утечек, запрет фактов вне `facts[]/near[]`, один следующий шаг.
2. **Scenario Card** — только сценарное поведение без дубля глобальных правил: когда использовать, цель ответа, что обязательно сделать, чем закончить, хороший/плохой пример.

Канонические scenario cards для симуляций:

- `first_help_policy` — первый полезный подбор: до 3 вариантов и один вопрос выбора.
- `selected_complex_policy` — клиент выбрал/назвал конкретный ЖК: короткая карточка из MCP-фактов.
- `selected_complex_ready_to_handoff_policy` — выбран ЖК и клиент показывает интерес: вести к оператору, а не продолжать допрос.
- `compare_policy` — сравнить текущие сохранённые варианты, не запускать новый широкий поиск.
- `budget_refinement_policy` — бюджет после списка сначала применить к `last_options`.
- `no_data_policy` — если `facts=[]` и `near=[]`, не делать пустой список и не советовать вторичку; предложить расширить географию.
- `operator_handoff_policy` — просьба позвонить/связаться/обсудить детали ведёт к operator handoff.

### Принятые симуляционные фиксы 2026-07-01

- `compare_policy`: фразы `чем различаются`, `сравни`, `отличаются` до выбранного ЖК теперь дают route=`compare_others` по текущим `last_options`.
- `budget_refinement_policy`: фразы `до 15 млн`, `бюджет 15 млн` после списка дают route=`sort_price_asc` с `budget_limit` и сортировкой/фильтрацией сохранённых вариантов.
- `filter_finish`: `с отделкой` после списка обрабатывается до generic classifier и даёт route=`filter_finish`.
- `selected_complex_ready_to_handoff_policy`: выбранный ЖК + показанная карточка + `интересно/что дальше/подходит` даёт route=`operator_for_selected`.
- `no_data_policy`: no-results по району говорит честно, что актуальных новостроек от застройщика нет в переданных данных, и предлагает посмотреть поблизости.
- `selected_complex_formatting_policy`: карточка одного ЖК и detail-ответ больше не пишутся одним плотным абзацем; факты разбиты на короткие блоки, финальный вопрос отдельным абзацем.
- `non_text_silence`: non-text Telegram updates больше не должны уходить в тишину; handler отвечает безопасным fallback и пишет `kind="non_text_message"`.

### Prod verification 2026-07-01

Для formatting/routing batch выполнен prod gate:

- backup на VPS: `backups/deploy-20260701-154110`;
- sync runtime/docs/test files в `/home/neiro/novostroy-bot`;
- remote `py_compile` — ok;
- remote `python3 scripts/nmbot_test_agent.py --suite h029 --json` — `29/29 pass`;
- remote `python3 scripts/nmbot_test_agent.py --suite ux_e2e --json` — `9/9 pass`;
- `novostroy-bot.service` restart — service `active (running)`;
- remote targeted sim: `ЖК Южные Сады → расскажи подробнее → интересно, что дальше` — карточка и detail в коротких абзацах, последний turn route=`operator_for_selected`;
- OpenRouter после проверки: today `$1.96`, total `$32.39`.

NotebookLM source note: `Session 2026-07-01 — selected complex formatting deployed`, note id `3ba8fa8be82e`.

---

## Реестр гипотез

### H019 — Расширить `facts[]`: копировать в JSON ВСЕ доступные поля из MCP (2026-06-26, **закрыта: accepted**)
- **Гипотеза:** user feedback 12:20: «ты должна как бы презентовать квартиры, описать ее преимущества». User intent 12:25: «а мы не можем сразу по объекту данные тянуть?». Triage: в `logs/dialogs-2026-06-25.jsonl:30` видно, что MCP novostroym **уже** отдаёт `metro: "м. Новокосино (15 минут пешком)"`, `area: "от 16.3 м²"`, `ready: "сдан"`, `link: "..."` — но LLM-search их **не кладёт в `facts[]`**, потому что `search_v1.txt:4-5` просит только `{name, location, price_range, finishing, why_close}`. Ирина остаётся без этих данных и вынуждена отвечать абстрактно. Рассматривались 3 варианта: (A) расширить search-промпт — дёшево, без нового кода, 1 правка в промпте; (B) новый `fetch_object_details` в OvermindClient — дорого, ещё один OpenRouter-вызов на каждый ответ; (A+B) оба. Выбран **A** (user confirm 12:28).
- **Что планируется:**
  1. `prompts/search_v1.txt` (P007-search) — попросить LLM-search копировать в `facts[]` **все** доступные поля из MCP (metro, area, ready, link, developer — что MCP реально вернул). Near-варианты тоже получают расширенный набор.
  2. `prompts/chat_v1.txt` (P008-chat) — разрешить озвучивать metro, area, ready, link, developer, price_range **если** они есть в `facts[]`. Запрет выдумывать уточнён: «не ВЫДУМЫВАЙ, но если поле в facts[] — ОЗВУЧИВАЙ».
  3. `docs/CODEX.md` §7 — расщепить «Выдумывать данные» на два пункта: ❌ «выдумывать» и ✅ «использовать metro/area/ready/link/dev, если есть в facts[] от search». Ссылка в `link` остаётся **непроизносимой** (CODEX §7 ссылок не даём) — её использует оператор при передаче.
  4. `logs/prompts.jsonl` — P007-search + P008-chat.
- **Критерии приёмки:**
  - `nmbot_test_agent` 12+ тестов pass без регрессий по codex (`no_greetings`, `no_sorry_empty`, `no_links`).
  - 3 baseline-теста (широкий / узкий-found / пустой) показывают `metro`/`area`/`ready` в `response_text` Ирины.
  - `logs/dialogs-2026-06-26.jsonl`: новый `user_message` имеет `search_response_len > среднего за 25.06` (факт: search-промпт стал просить больше полей).
  - НЕ сломан codex: response без «к сожалению» / обращений / ссылок при empty.
  - Latency не выросла >15% (H016 baseline ~13с).
- **Риски:** MCP может не отдавать `developer` (застройщик) — но LLM-search сам разберётся, какие поля есть, и не будет выдумывать отсутствующие. Chat-фаза работает на `gemini-2.5-flash` без MCP — расширение `facts[]` безопасно, chat-модель видит новые поля и просто использует.
- **Связанные:** P007-search, P008-chat. **Не трогает** H018 (живой диалог v2 — эмодзи/HTML/postprocessor) и H014 (split /start).
- **Результат:** ✅ **принята**. `nmbot_test_agent` 12/12 pass (codex 5/5, h016 4/4, golden 3/3 после фикса маркера). Latency +14% (13.4с → 15.3с), в пределах допуска.
- **Ключевая находка при закрытии:** первый прогон golden показал fail `golden_kotel_renov` — реальный ответ «ЖК «Дюна» стоимость от 10 905 590 до 25 300 120 руб.» не содержит маркер `млн`. Triage: **это не регрессия H019, это улучшение**. P007 копирует полную цену min_price/max_price из MCP, P008-chat её озвучивает. Маркер устарел — заменён на `руб` в `scripts/nmbot_test_agent.py:290`. Также golden_msk_budget прошёл со старым маркером `млн` — gemini-2.5-flash сама решает формат (короткие цены округляет, длинные выдаёт полностью).
- **Файлы финальные:** `prompts/search_v1.txt` (P007), `prompts/chat_v1.txt` (P008), `docs/CODEX.md §7` (расщеплён), `logs/prompts.jsonl` (P007 + P008), `logs/hypotheses.jsonl` (H019 closed), `scripts/nmbot_test_agent.py:290` (golden marker fix).
- **Статус:** ✅ **закрыта: accepted**.

### H018 — Живой диалог: эмодзи-маркеры + HTML-разметка + codex v2 (2026-06-26, **закрыта: accepted**)
- **Гипотеза:** user feedback «сделать диалог более живым — добавить разметку, эмодзи». Текущий codex (CODEX.md §1/§7 + chat_v1.txt) это прямо запрещает. Решение: ослабить codex, разрешить 0-2 эмодзи по контексту как **маркеры состояния** (👋/🔎/✅/🤷/🙂), HTML-разметку `<b>` для имён ЖК и цен, нумерованные списки для вариантов. LLM генерирует plain text, postprocessor в `chat_tester_bot.py` оборачивает regex-паттерны в `<b>` и экранирует спецсимволы — так модель не может «забыть закрыть тег». 6 golden-диалогов (4 старых + 2 новых).
- **Что сделано (2026-06-26):**
  1. `prompts/chat_v1.txt` (P006-chat) — переписан под 5+1 сценариев (широкий/near-match/exact 1/exact 2+/пустой/не-недвижимость) с эмодзи-маркерами и нумерованными списками. Запрет «Уважаемый/Дорогой» сохранён. Добавлена явная инструкция: «пиши plain text, HTML делает postprocessor».
  2. `docs/CODEX.md` §1 — разрешены 0-2 эмодзи (только маркеры состояния: 👋🔎✅🤷🙂).
  3. `docs/CODEX.md` §2 — расщеплено: 1 вариант → абзац; 2+ → нумерованный список 1./2./3.
  4. `docs/CODEX.md` §3 — добавлена проверка ширины запроса (узкий → «Точно таких нет», широкий → обычный рассказ).
  5. `docs/CODEX.md` §7 — расщеплено: ❌ тире/буллиты/```json → ✅ 1./2./3. для facts/near → ✅ `<b>`/`<i>` через postprocessor.
  6. `docs/GOLDEN_DIALOGS.md` — добавлены 2 эталона: Пример 5 (2+ вариантов с ✅ и нумерованным списком), Пример 6 (`/start` приветствие). Анти-паттерны расширены: ✅ 1./2./3., ✅ 0-2 эмодзи, ✅ plain text + postprocessor, ✅ широкий запрос ≠ «Точно таких нет», ❌ 3+ эмодзи, ❌ HTML в LLM-ответе.
  7. `scripts/chat_tester_bot.py` — функция `_to_html(text)` после `_strip_markdown` (line 268): HTML-escape `&/<,>` + regex `«([^»\n]{2,80})»` → `<b>«...»</b>` + regex `\b\d[\d\s.,]*\s?(млн|тыс|руб|рублей|млрд)\b` → `<b>...</b>`. Применена к 5 точкам: line 741 (indicator edit_text), 794+818 (operator funnel — добавлен `parse_mode="HTML"`), 919+921 (streaming edit).
  8. `scripts/nmbot_test_agent.py` — 2 новые проверки в базовый набор: `_check_html_safe` (regex `r"[<>]|&(?!amp;|lt;|gt;|quot;|#)"` — нет сырых HTML/&-entity) + `_check_single_emoji_per_msg` (Unicode emoji range, ≤2 на сообщение). Применены к codex (базовый набор) + h016 + golden.
- **Verification:** `nmbot_test_agent --suite all` ✅ **12/12 pass** (codex 5/5, h016 4/4, golden 3/3). 0 регрессий. Latency 13.0с (median), -7% к baseline 14.0с. Новые проверки: `html_safe` = 0 нарушений (LLM пишет plain), `single_emoji_per_msg` = 0 нарушений (5 codex + 4 h016 + 3 golden).
- **Ключевая находка при закрытии:** Markdown-проверка (`_check_no_md`) остаётся в базовом codex-наборе как защита от ` ```json ` обёрток (она работает по `_raw_response`, не по `response_text`).
- **Связанные:** P006-chat (active). H014 — закрыт 2026-06-26 (reconciliation). H015a (shutdown) — partial accepted. H019 (facts[] все поля MCP) — closed 2026-06-26. H020b (reply keyboard) — открыт, низкий приоритет.
- **Статус:** **закрыта: accepted (2026-06-26T14:25)**.

### H017 — nmbot_test_agent (2026-06-25, **закрыта: accepted**)
- **Гипотеза:** User request — «агент который сам задаёт вопросы и проверяет соблюдение правил». CLI-агент прогоняет сценарии через `OvermindClient` напрямую, проверяет codex + H016 + golden.
- **Что сделано:**
  - `scripts/nmbot_test_agent.py` (новый, ~500 строк): async прогон, 12 сценариев, проверки по чек-листу, JSON + human-readable отчёт, exit 0/1.
  - `logs/hypotheses.jsonl` — H017 closed.
- **Сценарии (12, по 4 в каждом suite):**
  - CODEX: no_greetings, no_links, valid_json, operator_funnel_soft, non_realty_redirect
  - H016: setup_options, select_option_second, sort_price_cheaper_with_renov, new_search_fallback
  - GOLDEN: golden_kotel_renov, golden_msk_budget, golden_spb_redirect
- **Результат:** 12/12 pass, 0 fail. Exit 0.
- **Ключевые находки при разработке (diary):**
  1. `OvermindClient.ask()` возвращает `(response_text: str, params, search_meta, chat_meta)`. response_text — это уже распарсенный чат-ответ (строка), а не JSON-обёртка `{response, params}`. Сырой search JSON — в `search_meta["_response_text"]` (добавлен в H016).
  2. Markdown-проверка только для чат-ответа. Search-фаза возвращает JSON и может быть обёрнут в ```` ``` ```` — это норма для служебного JSON, не наружу.
  3. golden_msk_budget: Ирина на «Студия в Москве до 5 млн» выдаёт near-match (МФК Wellbe 4.98 млн), а не оператора. Маркер `["млн"]` корректен.
  4. H016 intent «new_search» (т.е. не сработал резолвер) — это дефолт, не ошибка.
- **Использование:**
  ```bash
  python3 scripts/nmbot_test_agent.py              # все 12
  python3 scripts/nmbot_test_agent.py --suite codex
  python3 scripts/nmbot_test_agent.py --json       # для CI
  ```
- **Статус:** **закрыта (accepted)**.

### H016 — Dialog memory + operator funnel (2026-06-25, **закрыта: accepted**)
- **Гипотеза:** короткие follow-up сообщения («второй», «подешевле с ремонтом») должны обрабатываться из памяти последнего списка вариантов, а не запускать новый широкий поиск. Операторская воронка должна появляться мягко: сначала польза, потом предложение оставить номер.
- **Что сделано:**
  1. `chat_tester_bot.py`: добавлен `state["last_options"]` и сохранение вариантов из `search_response.facts + near`.
  2. Добавлены helper'ы `_resolve_dialog_intent`, `_format_option_response`, `_format_cheaper_response`, `_extract_options`, `_price_min`.
  3. `handle_message`: до Overmind-поиска ловит `select_option` и `sort_price_asc`, отвечает из памяти и не делает новый общий поиск.
  4. `docs/CODEX.md`: раздел «Сначала польза, потом оператор».
  5. `prompts/chat_v1.txt`: смягчена операторская формулировка; запрещены обещания «я уточню/передам оператору» без согласия и номера.
- **Проверки:** `py_compile` OK; helper smoke tests OK: «второй» выбирает 2-й вариант, «подешевле с ремонтом» сортирует по цене и отфильтровывает «без отделки».
- **Рестарт:** бот поднят через `setsid bash scripts/run_bot.sh`, PID `22458`, `getUpdates 200 OK`.
- **Статус:** **закрыта (accepted)**.

### H012 — Wide vs narrow query logic + search indicator (2026-06-25, **закрыта: accepted**)
- **Гипотеза:** (1) «точно таких нет, но близко» звучит плохо для широких запросов без конкретных параметров (только район/город). Правильная логика: «точно таких нет» только когда клиент назвал конкретные параметры (rooms/max_price/has_renovation/floor). (2) Telegram-юзер не видит «typing» action короткий — нужен видимый индикатор «🔎 Осуществляю поиск...» с editMessageText на финальный ответ.
- **Дата:** 2026-06-25 15:35 → 15:42
- **Что сделано:**
  1. **`prompts/chat_v1.txt`** (P006-chat): добавлена логика «„точно таких нет, но близко“ ТОЛЬКО для узких запросов». Широкие (район/город) — рассказываем как обычный facts.
  2. Добавлен few-shot пример 1b «Квартира в котельниках» (широкий запрос, 2 ЖК, без «точно таких нет»).
  3. **`scripts/chat_tester_bot.py:handle_message`**: добавлен видимый «🔎 Осуществляю поиск...» с `editMessageText` на финальный ответ вместо дубля `reply_text`.
  4. Бот перезапущен (PID 18496).
- **Результат (4/4 теста):**

| # | Тип | Запрос | Ответ |
|---|---|---|---|
| 1 | широкий | «квартира в котельниках» | «В Котельниках есть два ЖК с отделкой: «Белая Дача парк» 7.35-20.56 млн, «Кузьминский лес» 8.60-21.79 млн. Какой бюджет и кол-во комнат?» |
| 2 | узкий пустой | «однушка до 3 млн в Мск» | «Подходящих вариантов не нашла. Могу передать запрос оператору — напишите номер.» |
| 3 | узкий найденный | «двушка с отделкой в Мск» | «Есть двушки в ЖК «Лучи» (Солнцево, от 10.5 млн), «Южные Сады» (Южное Бутово, от 11.4 млн), «Амурский парк» (Гольяново, от 11.5 млн). Какой район?» |
| 4 | не-недвижимость | «расскажи анекдот» | «Я консультирую только по недвижимости. Могу помочь с подбором новостройки в Москве или Московской области.» |

- **Критерии приёмки:** выполнены. wide_query_no_apology 1/1, narrow_empty_to_operator 1/1, narrow_found_listed 1/1, non_realty_exact_phrase 1/1, no_greetings 4/4, valid_json 4/4.
- **Статус:** **закрыта (accepted)**.

### H014 — Split `/start` into system + Irina intro (2026-06-25 → 2026-06-26, **закрыта: accepted**)
- **Гипотеза:** `/start` смешивает технические детали (модели, MCP, команды) и человеческое приветствие. Разделить на 2 сообщения: (1) системный блок (модели, MCP, команды), (2) приветствие от Ирины с примерами.
- **Дата:** открыта 2026-06-25 12:57 → закрыта 2026-06-26 13:25.
- **Что сделано:**
  1. **`scripts/chat_tester_bot.py:start_command`** (строки 575-600): первый `await update.message.reply_text(...)` — системный блок (модели, MCP, команды, `parse_mode="HTML"`); второй `await update.message.reply_text(...)` — приветствие Ирины с 3 примерами запросов + обещание оператора, тоже `parse_mode="HTML"`. Разделены комментарием `# H014:`.
- **Triage / расхождение реестров:** код и `CHANGELOG.md:41` (✅) были синхронизированы ещё при имплементации (2026-06-25), но `logs/hypotheses.jsonl:14` оставался `status: "open"`, в `EXPERIMENTS.md` не было раздела, а `CHANGELOG.md:127` (сводка) перечислял H014 среди открытых. Закрыто в рамках reconciliation 2026-06-26 — теперь `hypotheses.jsonl:14` = closed (accepted), сводка = «1 открытый: H018».
- **Критерии приёмки:** выполнены. `/start` шлёт 2 сообщения подряд (доказано: `chat_tester_bot.py:590-600`); второе — от Ирины с примерами («Например: ...»). Дополнительная проверка в проде 2026-06-26: `dialogs-2026-06-26.jsonl:2` = `/start` команда зафиксирована.
- **Связанные:** H015a (shutdown, partial accepted 2026-06-26 — workaround `setsid` стабилен, код-фикс signal handler отложен). H020b (reply keyboard) — открыт, низкий приоритет, не блокирует.
- **Статус:** **закрыта (accepted)**.

### H015a — Shutdown stability: SIGTERM workaround (2026-06-25 → 2026-06-26, **закрыта: partial_accepted**)
- **Гипотеза:** бот падает на SIGTERM с `RuntimeError: Cannot close a running event loop`. Цель: либо пофиксить в коде (signal handler), либо зафиксировать workaround `setsid bash scripts/run_bot.sh` как стандартный способ запуска.
- **Дата:** открыта 2026-06-25 ~13:00 → закрыта 2026-06-26 13:35.
- **Что сделано:**
  1. **Workaround:** запуск бота делается через `setsid bash scripts/run_bot.sh` в терминале оператора (не в самом `run_bot.sh` — там `exec python` без `setsid`).
  2. **Результат:** PID 22458 (запущен 2026-06-25, uptime 36+ часов на момент закрытия), `getUpdates 200 OK`. Бот в проде стабилен.
  3. **Код-фикс signal handler** в `scripts/chat_tester_bot.py` **не сделан** — отложен. Понадобится при переносе на systemd / Docker.
- **Triage:** H015 изначально был «Pending: shutdown stability + Reply Keyboard» (CHANGELOG.md:37-39, 2026-06-25). Это **две разные задачи** с разным scope и приоритетом. Разнесены 2026-06-26:
  - **H015a (этот раздел)** = shutdown stability → closed (partial_accepted).
  - **H020b** = Reply Keyboard с кнопкой `/start` → open (низкий приоритет).
- **Критерии приёмки:** выполнены частично. Бот не падает на SIGTERM при `setsid`-запуске = ✅ (uptime 36+ часов). Код-фикс signal handler = ❌ (отложен).
- **Статус:** **закрыта (partial_accepted)**. Workaround устойчив, код-фикс не блокирует.

### H021 — Inline-кнопки budget из price_min в last_options (2026-06-26, **закрыта: accepted**)
- **Гипотеза:** user feedback 13:00 «мне не понравилось что появились инлайн кнопки, я на них нажал а он сказал ничего такого нет. почему инлайн выдает данные которых нет в поиске». Triage: `_pick_quick_actions` (chat_tester_bot.py) генерирует кнопки бюджета жёстко по сценарию — `[5, 8, 12] млн`, если `max_price` не указан. **Actual ≠ Contract:** CODEX §7 «не выдумывать» + §9 «сначала польза» — кнопка = обещание кликабельного результата. Если цена ниже минимума в `last_options` — обман. **Desired:** кнопки из `min(price_min)`.
- **Что сделано (2026-06-26):**
  1. `scripts/chat_tester_bot.py:439-498` — функция `_pick_quick_actions` переписана:
     - Добавлен модуль `_BUDGET_THRESHOLDS_MLN = [3, 5, 7, 8, 10, 12, 15, 20]`.
     - Добавлен helper `_budget_buttons_from_options(state, max_count=3)`: берёт `min(price_min)` из `state["last_options"]`, фильтрует thresholds `>= floor_mln`, берёт первые 3, fallback `[15]`. Если `last_options` пуст — fallback `[5, 8, 12]` (безопасный дефолт).
     - В `G-first-step`: `[budget_buttons + "без лимита"]` (4 кнопки). В `A-found-some`: `[budget_buttons]` (3 кнопки).
  2. `scripts/nmbot_test_agent.py` — добавлен `suite="h021"` с 3 unit-тестами (прямой вызов `_pick_quick_actions`, без Overmind):
     - `budget_buttons_from_min_price_a_found`: state с `price_min=7.4M` → ожидаем `[budget:8m, budget:10m, budget:12m]`.
     - `budget_buttons_g_first_step_with_options`: state с `price_min=3.5M` → ожидаем `[budget:5m, budget:7m, budget:8m, budget:none]`.
     - `budget_buttons_fallback_when_empty`: state с `last_options=[]` → ожидаем `[budget:5m, budget:8m, budget:12m]` (безопасный дефолт).
- **Verification:** `nmbot_test_agent` ✅ **15/15 pass** (h021 3/3 + codex 5/5 + h016 4/4 + golden 3/3). 0 регрессий. Latency 13.0с (медиана) — в норме.
- **Triage / архитектурная находка:** `price_min` уже есть в `last_options` благодаря H019 (`_extract_options` + `_price_min` helper). H021 только добавляет потребителя — никаких изменений в поиске/prompts, чисто клиент-сайд.
- **Связанные:** H019 (closed) — дал `price_min` в last_options. H018 (closed) — postprocessor `_to_html()` не влияет на кнопки (кнопки генерирует код, не LLM). H020b (reply keyboard) — открыт, низкий приоритет.
- **Статус:** **закрыта: accepted (2026-06-26T14:35)**. User pain снят: в реальном диалоге «двушка в зеленограде» теперь покажет кнопки `[до 8, до 10, до 12]`, а не `[до 5, до 8, до 12]`. Кликабельный результат = реальный результат.

### H020b — Reply Keyboard с кнопкой `/start` (2026-06-26, **открыта**)
- **Гипотеза:** Persistent Reply Keyboard внизу чата с кнопкой `/start` ускоряет рестарт сессии. Сейчас пользователь вводит `/start` руками или через inline-кнопки.
- **Дата:** открыта 2026-06-26 13:35 (выделена из H015).
- **Scope:** `scripts/chat_tester_bot.py:start_command` (добавить `ReplyKeyboardMarkup([[KeyboardButton('/start')]])`), плюс `handle_message` — Reply Keyboard должен сохраняться между сообщениями.
- **Когда делать:** низкий приоритет — inline-кнопки уже покрывают основной сценарий. Делать в последнюю очередь или вообще отложить, если не будет user demand.
- **Критерии приёмки (если делать):**
  1. `ReplyKeyboardMarkup` с кнопкой `/start` отображается у всех пользователей.
  2. Нажатие на кнопку отправляет команду `/start` боту.
  3. Кнопка видна и после `/reset`, и в обычной беседе (не исчезает после каждого сообщения).
- **Статус:** **открыта (planned)**. Не блокирует.

### H011 — Restore _chat_with_retry to OvermindClient (2026-06-25, **закрыта: accepted**)
- **Гипотеза:** `AttributeError: 'OvermindClient' object has no attribute '_chat_with_retry'` в работающем боте (15:27). Метод случайно вложен внутрь `_strip_markdown` при правке H004-bug-fix (потеря отступа).
- **Что сделано:**
  1. AST-анализ: до фикса OvermindClient имел 7 методов (без `_chat_with_retry`).
  2. Вырезан `_chat_with_retry` + дублирующийся `close()` из тела `_strip_markdown`.
  3. Вставлен `_chat_with_retry` заново как **метод класса OvermindClient** (4 пробела, между `_parse_chat_json` и `# Experiment Loop logging`).
  4. AST-чек: 8 методов в OvermindClient ✅.
  5. Бот перезапущен, live test 15:32: «нужна квартира в котельниках» → ответ по codex (Белая Дача парк, Кузьминский лес).
- **Урок:** `py_compile` не ловит потерю отступа внутри класса (синтаксис валидный, но runtime-семантика неправильная). Нужен AST-чек в preflight (`scripts/run_bot.sh`).
- **Статус:** **закрыта (accepted)**.

### H010 — Few-shot golden dialogs in chat prompt (2026-06-25, **закрыта: accepted**)
- **Гипотеза:** Codex v1 (H009) дал правила в абстрактной форме. flash-2.5 иногда игнорирует абстрактные правила, но следует few-shot примерам. Встраивание 4 golden-диалогов + анти-паттернов в `chat_v1.txt`.
- **Дата:** 2026-06-25 14:35 → 14:45
- **Что сделано:**
  1. **`prompts/chat_v1.txt`** (P005-chat): добавлена секция «ПРИМЕРЫ ХОРОШЕГО ОТВЕТА» с 4 few-shot диалогами (near-match / СПб / facts / не-недвижимость) + секция «ЧЕГО НЕ ПИСАТЬ» с анти-паттернами. Размер промпта ~3.5KB / ~875 токенов.
  2. **`docs/GOLDEN_DIALOGS.md`**: 4 эталона + анти-паттерны + правила обновления.
- **Результат (3/3 теста):**

| # | Запрос | Ответ | Codex + golden |
|---|---|---|---|
| 1 | однушка ≤8 млн Мск | «Есть однушка в Жилом доме «2-й Тушинский пр-д, д. 1» в Покровском-Стрешнево. 7.78 млн. Вас интересует?» | ✅ Точный match, без «Уважаемый» |
| 2 | студия СПб | «По СПб в базе нет. В МО есть студии: ЖК «Горки Парк» в Коробово от 4.9 млн. Передать оператору?» | ✅ Near + оператор в конце (как golden B) |
| 3 | двушка с отделкой МКАД | «Есть двушки в ЖК «Лучи» в Солнцево, 10.6-31.6 млн. Какой бюджет?» | ✅ Один лучший + уточняющий вопрос |

- **Сравнение H009 vs H010:** H010 даёт более стабильные паттерны. Тест 1 в H010 — точный match (когда MCP его возвращает), а в H009 был near (когда MCP не нашёл). Тест 2 в H010 = golden B (near+оператор), в H009 = вариант A (только оператор).
- **Критерии приёмки:** выполнены. no_greetings 3/3, no_sorry_empty 3/3, matches_golden 3/3, valid_json 3/3, prompt_size < 4000 токенов.
- **Статус:** **закрыта (accepted)**.

### H009 — Dialog codex (2026-06-25, **закрыта: accepted**)
- **Гипотеза:** User feedback: «Уважаемый клиент» — плохо, «к сожалению, не нашлось» без альтернативы — плохо, ссылок novostroy-m.ru в чате не даём. Кодекс: нейтральный тон + near-match fallback + оператор с захватом номера.
- **Дата:** 2026-06-25 14:20 → 14:30
- **Что сделано:**
  1. **`prompts/search_v1.txt`** (P004-search): добавлено поле `near` (приближённые с `why_close`). Разделение `facts` (точные) vs `near` (1-2 отличия) vs `missing`.
  2. **`prompts/chat_v1.txt`** (P004-chat): запрет обращений, запрет ссылок, три ветки (facts/near/empty), при empty — просьба номера.
  3. **`docs/CODEX.md`** — свод правил диалога (8 разделов).
  4. **`scripts/chat_tester_bot.py:handle_message`**: добавлен inline-кнопка «📞 Связаться с оператором» (триггеры: «оператор», «живой человек», «менеджер», «перезвоните», или `передам`+`оператор` в ответе).
  5. **`button_handler`**: `request_operator` → `state["awaiting_phone"]=True`.
  6. **`handle_message`**: следующее текстовое сообщение трактуется как номер, валидация 10-15 цифр, запись в лог `phone_captured`.
- **Результат (3/3 теста):**

| # | Запрос | Ответ | Codex |
|---|---|---|---|
| 1 | однушка ≤8 млн, Мск | «Точно таких нет, но близко: МФК Ситимикс Новокосино — апартаменты с отделкой, 6.6-8.0 млн. Это апартаменты, не квартира. Вас интересуют апартаменты?» | ✅ near-match с why_close, без «Уважаемый» |
| 2 | студия СПб ≤5 млн | «Подходящих вариантов не нашла. Могу передать запрос оператору — напишите номер телефона для связи.» | ✅ не «к сожалению», а сразу оператор + захват номера |
| 3 | двушка с отделкой МКАД | «Есть двухкомнатная с отделкой в ЖК «Лучи» в Солнцево, 10.6-31.6 млн. Какой бюджет рассматриваете?» | ✅ один лучший + уточняющий вопрос, без «Уважаемый» |

- **Критерии приёмки:** выполнены. no_greetings 3/3, near_match_offered 1/1 (тест 1), no_sorry_empty 3/3, no_novostroy_links 3/3, operator_button + phone_capture добавлены.
- **Статус:** **закрыта (accepted)**.

### H007-A — Strip markdown BEFORE _parse_chat_json (2026-06-25, **закрыта: accepted**)
- **Гипотеза:** H006 убрал markdown в логе, но обёртка всё равно проходит через `json.loads` (парсер устойчив, но это лишний риск регрессии). Strip ДО парсинга = чистота кода.
- **Дата:** 2026-06-25 14:05 → 14:10
- **Что сделано:**
  1. `chat_tester_bot.py:241` (внутри `_chat_with_retry`): `chat_result = _strip_markdown(chat_result)` ДО `_parse_chat_json`. То же в retry-цикле.
  2. `chat_cli.py:230` (внутри `ask_two_stage`): `chat_response = _strip_markdown(chat_response)` сразу после `ask_overmind`. То же в retry-цикле.
  3. Прогон 3 baseline-тестов с `NMBOT_H_ID=H007-A --chat-max-tokens 10000`.
- **Результат (3/3 теста):**

| # | Запрос | Длительность | JSON без markdown? | params |
|---|---|---|---|---|
| 1 | однушка ≤8 млн, Москва | 13.8с | ✅ | `{rooms:1, max_price:8000000, district:msk}` |
| 2 | студия СПб ≤5 млн | 13.7с | ✅ | `{rooms:s}` |
| 3 | 2-комн. с отделкой, МКАД | 13.6с | ✅ | `{rooms:2, district:msk, has_renovation:true}` |

- **Критерии приёмки:** выполнены. has_md=0/3, json_completeness=3/3, errors=0, avg_dur=13.6с (в норме).
- **Изменение в коде:** strip markdown теперь в **двух точках** (до парсинга + в логе). Парсер по-прежнему устойчив (страховка).
- **Статус:** **закрыта (accepted)**.

### H006 — Strip markdown in log writes (2026-06-25, **закрыта: accepted**)
- **Гипотеза:** Telegram получает очищенный JSON (через `_parse_chat_json`), а в `dialogs.jsonl` пишется сырой `chat_result` от Overmind с markdown-обёрткой. Helper `_strip_markdown(text)` в обоих скриптах устранит расхождение.
- **Дата:** 2026-06-25 13:55 → 14:00
- **Что сделано:**
  1. `chat_tester_bot.py`: добавлен `_strip_markdown` на module-level (рядом с `_log_event`). В `handle_message` строка 478: `response_text: _strip_markdown(response)`.
  2. `chat_cli.py`: добавлен `_strip_markdown` (рядом с `_log_event`). В `main()` строка 369: `response_text: _strip_markdown(chat_response)`.
  3. Прогон 3 baseline-тестов с `NMBOT_H_ID=H006 --chat-max-tokens 10000`.
- **Результат (3/3 теста):**

| # | Запрос | Длительность | markdown в логе? |
|---|---|---|---|
| 1 | однушка ≤8 млн, Москва | 13.9с | ❌ нет |
| 2 | студия СПб ≤5 млн | 10.4с | ❌ нет |
| 3 | 2-комн. с отделкой, МКАД | 10.5с | ❌ нет |

- **Критерии приёмки:** выполнены. `has_md_count = 0/3`. Среднее время 11.6с.
- **Triage:** в первом прогоне `chat_cli.py` упал с `NameError: name '_strip_markdown' is not defined` (функция была только в чат-боте). Исправлено — добавлен helper в обоих скриптах на module-level.
- **Статус:** **закрыта (accepted)**.

### H003 — Increase chat max_tokens 5000 → 10000 (2026-06-25, **закрыта: accepted**)
- **Гипотеза:** обрезание JSON в chat-стадии (H002: 2/3 тестов) вызвано лимитом `max_tokens=5000`. Увеличение до 10000 устранит проблему без существенного роста latency.
- **Дата:** 2026-06-25 13:45 → 13:48
- **Что сделано:**
  1. `chat_cli.py`: добавлен флаг `--chat-max-tokens`, параметр пробрасывается в `ask_overmind` и `ask_two_stage` (search — без изменений).
  2. `chat_tester_bot.py:126`: `max_tokens 5000 → 10000` с комментарием про H003.
  3. Прогон 3 baseline-тестов с `NMBOT_H_ID=H003 --chat-max-tokens 10000`.
- **Результат (3/3 теста):**

| # | Запрос | Длительность | JSON полный? | params |
|---|---|---|---|---|
| 1 | однушка ≤8 млн, Москва | 13.7с | ✅ | `{rooms:1, max_price:8000000, district:msk}` |
| 2 | студия СПб ≤5 млн | 13.5с | ✅ | `{rooms:s, max_price:5000000}` |
| 3 | 2-комн. с отделкой, МКАД | 13.6с | ✅ | `{rooms:2, district:msk, has_renovation:true}` |

- **Критерии приёмки:** выполнены.
  - Валидных JSON: **3/3** (vs H002 1/3, vs H001 3/3).
  - Среднее время: **13.6с** (+1.6с к H001, +0.9с к H002 — приемлемо).
- **Triage-уточнение:** H001 имел 3/3 валидных JSON при `max_tokens=5000`. H002 с тем же `max_tokens=5000` дал 1/3. Это значит, что **обрезание не было стабильно воспроизводимой регрессией** от H002 (где мы унифицировали промпты) — скорее flash флактуирует, и `max_tokens=5000` находится на грани (иногда хватает, иногда нет). Увеличение до 10000 убрало флактуацию.
- **Изменение, которое остаётся в коде:** `chat_tester_bot.py:126` — `max_tokens = 10000`. `chat_cli.py` имеет `--chat-max-tokens` (default 5000 для совместимости, baseline-тесты передают 10000).
- **Статус:** **закрыта (accepted)**.

### H002 — Prompts DRY + cost tracking (2026-06-25, **закрыта: partial_accepted**)
- **Гипотеза:** вынести промпты в `prompts/*.txt` (single source of truth) и начать логировать cost/tokens из Overmind metadata.
- **Дата:** 2026-06-25 13:35 → 13:42
- **Что сделано:**
  1. `prompts/search_v1.txt` + `prompts/chat_v1.txt` — единый источник промптов.
  2. `chat_cli.py` и `chat_tester_bot.py` — оба читают `SEARCH_SYSTEM_PROMPT`/`CHAT_SYSTEM_PROMPT` из файлов.
  3. `ask_overmind` и `_run_gateway_request` возвращают `(text, metadata)`.
  4. В `dialogs-*.jsonl` пишется `cost: {search_usd, chat_usd, total_usd, search_tokens_in/out, chat_tokens_in/out, total_tokens_in/out}` и `overmind_meta: {tokens_used, response_time, model, service}`.
  5. Перепрогон 3 тестов из H001 с `NMBOT_H_ID=H002`.
- **Результат:**
  - **DRY промптов: принято.** Оба скрипта используют одни и те же файлы. Правка = один файл, не два.
  - **Cost tracking: заблокировано инфраструктурно.** Overmind возвращает в `metadata` поля `tokens_used` (одно число, не разделено на in/out), `response_time`, `model`, `service`. Полей `tokens_in/tokens_out/cost_usd` **нет в API Overmind**. Мы логируем то, что есть. Чтобы считать `cost_usd`, нужно либо менять Overmind (вне скоупа nmbot), либо считать самим по прайсу OpenRouter + провайдить входные токены.
  - **Search-промпт улучшился:** 3/3 тестов вернули `params: {rooms, max_price, district, has_renovation}`. H001 — 0/3.
  - **Среднее время: 12.7с** (vs H001 11.7с, в пределах флактуации).
  - **Флактуация flash:** 2/3 тестов вернули обрезанный JSON-блок (89 и 73 зн.). Тест 3 — полный (347 зн.). Промпт не менялся относительно H001 chat (chat_v1.txt побайтно = H001 chat из бота). Гипотеза: flash нестабилен при длинных search_response. Требует наблюдения в H003+.
- **Критерии приёмки:** частично. DRY — да. Cost — нет (см. blocked_by_overmind). Search-payload улучшился.
- **Следующие шаги (для H003+):**
  - H003: попробовать `max_tokens` явно увеличить до 8000 для chat-стадии (если flash обрезает — это лимит токенов).
  - H003-альтернатива: добавить retry в Overmind, если JSON невалиден.
  - Вне скоупа: дописать Overmind чтобы он возвращал `tokens_in/tokens_out` (задача для основного репо).

### H001 — Baseline (2026-06-25, **закрыта**)
- **Гипотеза:** текущее поведение бота (промпты `P001/P001` от 24.06, модели `M001/M001`) — это **baseline**, относительно которого измеряем все будущие изменения.
- **Способ проверки:** прогон 3 разных запросов через `chat_cli.py` и фиксация ответов/времени/стоимости.
- **Дата фиксации:** 2026-06-25 13:30
- **Критерии приёмки:** baseline зафиксирован в `EXPERIMENTS.md` с примерами ответов — выполнено.

**Baseline-метрики (3 теста, search=gemini-3.1-flash-lite-preview, chat=gemini-2.5-flash, MCP=ON):**

| # | Запрос | Длительность | Длина ответа | Результат |
|---|---|---|---|---|
| 1 | "Найди однушку до 8 млн в Москве" | 11.1с | 577 зн. | Найден 1 ЖК "Зелёный парк" (7.83 млн) в Крюково. Честно сказано, что других вариантов в базе нет. Задан уточняющий вопрос про локацию. |
| 2 | "Студия в Санкт-Петербурге до 5 млн" | 7.3с | 208 зн. | Базы по СПб нет. Бот корректно отказал и предложил альтернативу в Москве/МО. |
| 3 | "Двухкомнатная квартира с отделкой в Москве в пределах МКАД" | 16.7с | 523 зн. | Найдены 2 ЖК (Квартал Домашний 16.14-40.99 млн, Лучи 10.58-31.58 млн), оба с отделкой. Бот честно сказал, что наличие двушек на текущий момент не подтверждено. |

**Среднее время ответа:** ~11.7с (lite + flash, polling ~3с между статусами).
**Стоимость:** не зафиксирована автоматически (chat_cli.py не парсит `metadata.cost` от Overmind — TODO для H002+).
**Ошибки:** 0/3.

**Наблюдения для будущих гипотез:**
- Lite стабильно отдаёт JSON `{facts, missing}` (структура — все 3 теста).
- Flash не выдумывает, если факты не подтверждены (тест 1 — "других вариантов нет", тест 3 — "наличие не подтверждено").
- Flash всегда задаёт **ровно один** уточняющий вопрос в конце (соответствует P001-chat).
- На длинные составные запросы (тест 3, 3 условия) время вырастает с ~7с до ~17с — вероятно, больше фактов в `search_response`.
- **Triage flag:** промпт `P001-search` в `chat_cli.py:35-42` отличается от `chat_tester_bot.py:36-45` (нет упоминания JSON-схемы с `params` в CLI-версии). Нужно синхронизировать — отдельная задача.

- **Статус:** **закрыта** (baseline зафиксирован).

---

## Реестр версий промптов

| ID | Файл | Дата | Примечание |
|---|---|---|---|
| P001-search | chat_tester_bot.py:36-45 | 2026-06-24 | поиск с MCP, JSON `{facts, missing, params}` |
| P001-chat   | chat_tester_bot.py:47-54 | 2026-06-24 | «Ирина», 2-4 предложения, JSON `{response, params}` |
