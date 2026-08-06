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

В журнале один эксперимент рекомендуется связывать с одной `H###` и одним или
несколькими `P###/M###`; лог диалогов в `logs/` привязывается к этим ID. Текущий
CLI принимает эти refs необязательно — точная граница описана ниже.

## Локальный declarative experiment workflow

Назначение workflow — локально зафиксировать и проверить декларативное изменение
эксперимента. Текущий development-profile ограничен stage `v2.response_writer`;
allowlist содержит только string-параметр `model`.

Каноническая последовательность: `stages → start → diff → check → report → compare`;
все команды запускаются через `python3 scripts/nmbot.py experiment ...`. Безопасно
начинать с:

```bash
python3 scripts/nmbot.py experiment stages --json
python3 scripts/nmbot.py experiment check ID --dry-run --full --json
```

`start` сохраняет полные baseline/candidate-копии prompt локально в
`tmp/nmbot_experiments` по умолчанию; `--store-dir` меняет этот каталог. `diff`
может показать prompt text с ограниченной redaction. Receipt содержит только
metadata: raw prompt, payload, output и secrets в него не попадают.

`--hypothesis`, `--prompt-version` и `--model-version` необязательны. CLI не
генерирует `H/P/M` автоматически: если аргументы не переданы, соответствующие
поля metadata/receipt сохраняются как `null`. Для traceability рекомендуется
передавать `H/P/M` вручную.

Точный flow `check`: overlay validation → static-check candidate prompt →
registered focused pytest → optional registered full scope. Static-check относится
к candidate prompt. Focused/full checks запускаются только по зарегистрированным
repository paths/scope: candidate prompt и model overlay не передаются им в argv,
поэтому эти проверки не доказывают candidate runtime/model behavior. Реальный
`check` останавливается на первом failure; последующие действия не выполняются.
`--dry-run` ничего не запускает.

`report` предпочтительно делать после `check`. `checks_not_run` появляется только
если summaries пусты; после partial failure report не добавляет отдельные skipped
summaries. Не трактуйте report как полный execution ledger. `compare` сверяет
только metadata compatibility, hashes и keys параметров, а не семантическое
качество. Локальные результаты не являются доказательством production/Jivo.

### Правило ревизии prompt

Изменение prompt начинается с полного чтения текущего текста и его контракта.
Для каждого нового наблюдения фиксируем решение:

| Ситуация | Действие |
|---|---|
| Близкое правило уже есть | Переписать существующее правило, сохранив старый и добавившийся смысл |
| Правила нет | Добавить одно правило в соответствующий раздел |
| Правило относится к другому LLM-слою | Не переносить его; изменить prompt только этого слоя |
| Есть конфликт или дубли | Сначала объединить/удалить конфликтующие формулировки, затем тестировать |

Для V2 Prompt Quality Guardian ревьюит prompt целиком и отдельно для каждого слоя:

- planner: `followup_intent_classifier.py:DIALOG_STATE_PLANNER_PROMPT`,
  `google/gemini-3.1-flash-lite-preview`;
- answer composer: `prompts/v2_response_composer.txt`,
  `google/gemini-2.5-flash`.
- optional final manager rewriter: `prompts/v2_manager_rewriter.txt`,
  `google/gemini-2.5-flash`; изолированные режимы
  `NMBOT_V2_MANAGER_REWRITER_MODE=off|shadow|publish` и
  `NMBOT_V3_MANAGER_REWRITER_MODE=off|shadow|publish`.

Для manager rewriter пользователь отдельно выбрал свободное переписывание без
смысловой поствалидации. Измеряем качество в `shadow` на одном и том же
prepared answer; технический сбой или пустой результат обязаны вернуть
prepared answer. До отдельного production-подтверждения слой считается
локально подготовленным и выключенным. Текущий one-step release готовит
`V2=off`, `V3=publish`.

После ревизии обязательны: проверка JSON/wire-контракта, focused regression,
полный regression и, для изменений клиентского поведения, live Jivo-проверка.
Версия prompt (`P###`) и версия модели (`M###`) должны быть записаны так, чтобы
было видно, что изменилось между «до» и «после».

### Локально подготовленная V3-гипотеза — AnswerBrief writer

- **Цель:** сохранить V3/V2 typed search и канонические карточки, но дать
  отдельному V3 writer-слою свободно выбрать понятный клиентский акцент,
  сравнение и один естественный следующий вопрос.
- **Изменение:** writer получает только code-built `V3_ANSWER_BRIEF`: текущий
  запрос, bounded safe dialogue, читаемые ограничения, канонические карточки,
  подтверждённые и отсутствующие факты. Raw MCP-ответы, контакты и секреты не
  передаются.
- **Граница:** V3 validator механически сохраняет identity/order/count
  карточек, фактические числа, safety и один финальный вопрос; при ошибке
  остаётся deterministic V2 answer. V2 writer contract не меняется.
- **Статус на 2026-07-30:** локальные focused regression прошли; TEST/Jivo
  evidence получено только для TEST-контура: immutable release
  `nmbot-v3-answerbrief-test-20260730-0915` прошёл полный синтетический Jivo
  путь search → phone → name → callback worker → Sheets. Безопасное
  подтверждение delivery: `lead_ref=cb_ef9abbd8f3b6667c`,
  `row_ref='Лист1'!A32:D32`. Это не production-доказательство и не оценка
  качества model prose вне этого одного диалога.

### TEST-гипотеза — selected availability через `lot_examples`

- **Цель:** проверить наличие выбранного ЖК без нового SQL/MCP-контура и без
  доверия к свободному тексту модели.
- **Изменение:** запрос `apartment_inventory` в selected-enrichment дополнительно
  запрашивает существующий структурированный `lot_examples`; fresh-факт
  появляется только при наличии лота с ID и активным/in-sale статусом.
- **Доказательство:** exact enrichment task `2451943` вернул два typed лота с
  ID и `status=2`; после подключения V3 TEST smoke
  `nmbot-v3-lot-availability-test-20260730-1015` прошёл поиск → выбор ЖК →
  проверку наличия, trace показал `selected_enrichment=1` и
  `availability_evidence.confirmation=confirmed`.
- **Граница:** это доказательство каталожных лотов на момент запроса, а не
  гарантия брони или сделки. Полная схема input `SQL_QUERY_TOOL` не считается
  доступной, пока её не подтвердит реальный adapter/fixture.

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
   - передаёт Prompt Quality Guardian полный handoff: цель и ожидаемый результат,
     `Actual / Contract / Desired`, ограничения, риски, слой и модель, полный
     текущий prompt, входной контекст и JSON/wire-контракт, normalizer/validator,
     релевантные тесты, live trace/диалог, прежние решения, их результаты и причины
     неудачных попыток. Нельзя отправлять только последнее наблюдение или одну
     ошибочную реплику.
4. После 3+ диалогов на новой версии — фиксирует outcome в `EXPERIMENTS.md` (принято/откат/нужна доработка) и ставит `status=closed` в `hypotheses.jsonl`.

---

## Hypothesis Simulation Gate

Перед изменением UX-логики Ирины ЧАТИ сначала проверяет гипотезу в read-only симуляции.

### Semantic orchestration gate

Если проблема связана с пониманием смысла сообщения клиента, сначала определяется слой:

```text
semantic user intent → stage/dialog orchestrator
mechanical input      → deterministic guard/router
```

К semantic intent относятся живые фразы вроде:

- `подбери похожие`;
- `найди похожие`;
- `ещё такие`;
- `похожие варианты`;
- `другие варианты`;
- `давай ещё`.

Их нельзя чинить расширением regex в lower-level router. Acceptance criteria для таких кейсов должны проверять, что orchestrator выбрал правильную stage/action.

Regex допустим только для механики без семантики: номер телефона. Чистый numeric choice (`1`, `2`, `3`) тоже идёт в LLM-orchestrator: он должен вернуть exact `selected_option_name` из `visible_options`.

Safety может быть code-veto после решения, но не способом распознавания смысла. Семантику отказа, согласия, смены условий, просьбы похожих вариантов и операторских намерений определяет LLM-orchestrator.

## Prod Verification Gate

Локальная проверка не считается финальной проверкой MINION.

**Жёсткое правило:** если изменение влияет на клиентские ответы, промпты, Telegram handler, dialog state, MCP/search parsing, `visible_options`, operator handoff или follow-up routing, то результат считается незавершённым до проверки на VPS.

Обязательный порядок:

1. Локально: `py_compile`, релевантные V2/Jivo regression tests и simulator/live probe.
2. Backup текущих runtime-файлов на VPS.
3. Deploy/sync runtime-файлов в `/home/neiro/novostroy-bot`.
4. Remote `py_compile` на VPS.
5. Restart `novostroy-bot-api.service` (bridge — только при изменении bridge).
6. Проверка API health, feature markers, `bot_error_events` и bridge trace на VPS.
7. Финальная проверка через Jivo widget либо privacy-safe `scripts/nmbot_jivo_client.py`.
8. `bash scripts/openrouter_balance` после live/prod проверки.

Если выполнены только локальные тесты, в отчёте обязательно писать: **«локально зелёное, prod ещё не проверен»**.

Если баг пришёл из live-лога, prod smoke обязан включать **точную пользовательскую фразу из лога**, а не только похожий общий сценарий. Пример: если в live было `подбери похожие`, проверка `похожие варианты` не считается достаточной.

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

### Scenario-by-scenario debugging practice

Практика, проверенная 2026-07-13 на MCP/scenario-аудите: большие UX-изменения не внедряются одним патчем. Сначала ЧАТИ проходит сценарии по одному и доказывает гипотезу тестами.

Порядок:

1. Взять один сценарий из аудита или live-проблемы: family, mortgage/family mortgage, selected-details, investment, rental, installment/discount и т.д.
2. Прогнать текущую версию без runtime-правок: `scripts/nmbot_scenario_sim.py --scenario <name>`, `scripts/nmbot_mcp_only_sim.py` или точечный `nmbot_test_agent` suite.
3. Читать не только pass/fail, но и сам диалог глазами клиента: звучит ли как Ирина, нет ли технических слов, не потерялись ли MCP-факты, не зовёт ли оператор слишком рано.
4. Записать результат в рабочий отчёт: вход, фактическое поведение, MCP facts, качество ответа, проблема, гипотеза патча, тест/регрессия.
5. Если в потоке видно маленькую безопасную правку, её можно сделать сразу, но только после воспроизведения и с повторным прогоном сценария.
6. Если проблема системная, не чинить точечно. Сначала собрать несколько подтверждённых кейсов, потом делать один общий патч.
7. Перед runtime-патчем добавить или обновить регрессионный тест на риск, который подтвердился.
8. После runtime-патча обязательно прогнать: `py_compile`, targeted scenario sim, `h029`, `ux_e2e`; для клиентских ответов — показать реальные тексты пользователю, а не только числа.
9. Если изменение влияет на прод-бота, завершением считается только VPS deploy + `nmbot_deploy_smoke.py` + проверка свежих error logs.

Минимальный формат итогового отчёта по сценарию:

```md
## <scenario>

- Test input: <что сказал клиент>
- Current behavior: <route / answer / state>
- MCP facts: <какие facts/near/missing пришли>
- Answer quality: <что хорошо/плохо глазами клиента>
- Problem: <подтверждённая проблема или none>
- Patch hypothesis: <минимальная гипотеза исправления>
- Regression: <какой тест должен это держать>
- Decision: patch now / include in big patch / no patch
```

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
11. **Layer decision** — semantic intent должен идти в orchestrator; regex/router только для mechanical input: телефон. Выбор `1`/`2`/`3` проверяется как orchestrator `select_option` с exact `selected_option_name`.
12. **Acceptance criteria** — что должно стать зелёным после патча: например, no `вторичка`, no empty options-summary, stage=`expand_more_options`, `selected_option` заполнен, journal status=`ok`.
13. **Exact live phrase** — если источник проблемы live-log, указать точную фразу, которую надо прогнать (`подбери похожие`, а не обобщённое `похожие варианты`).
14. **Prod gate** — если изменение влияет на ответы/routing/state, явно пометить: «локально зелёное, prod ещё не проверен» до VPS-проверки.

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
- `expand_more_options_policy` — фразы `ещё варианты`, `похожие варианты`, `другие варианты` после списка запускают свежий поиск с исключением уже показанных ЖК.
- `budget_refinement_policy` — бюджет после списка сначала применить к `last_options`.
- `no_data_policy` — если `facts=[]` и `near=[]`, не делать пустой список и не советовать вторичку; предложить расширить географию.
- `operator_handoff_policy` — просьба позвонить/связаться/обсудить детали ведёт к operator handoff.

### Принятые симуляционные фиксы 2026-07-01

- `compare_policy`: фразы `чем различаются`, `сравни`, `отличаются` до выбранного ЖК теперь дают route=`compare_others` по текущим `last_options`.
- `expand_more_options_policy`: фразы `ещё варианты`, `похожие варианты`, `другие варианты` после списка теперь дают route=`expand_more_options` и не повторяют уже показанные ЖК.
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

### H030 — Readiness gate: empty-region routing, numeric prices, canned phrase guard (2026-07-13, **открыта: prod targeted green**)
- **Причина:** полный readiness gate после проверки сценариев показал, что бот ещё нельзя считать полностью готовым. Первый полный прогон дал **114/117 pass**.
- **Провалы первого полного gate:**
  1. `golden_msk_budget` — не хватило маркера `млн`: карточка студии до 5 млн теряла видимую цену, хотя MCP/search и chat JSON содержали `min_price`/`max_price`.
  2. `golden_spb_redirect` — не хватило маркеров `москв`/`московск`: пустой out-of-region результат (`Санкт-Петербург`) превращался в generic fallback `По запросу не удалось найти информацию...`, не доходя до chat prompt.
  3. `stateful/followup_memory_select_operator` — `ux_natural_human_tone` поймал канцелярит `по вашему запросу`.
- **Контракт:** `docs/IDEAL_IRINA_UX.md` требует: для региона вне Москвы/МО честно сказать, что база только Москва/МО; не бросать клиента пустым dead-end; сохранять естественный тон без канцелярита; отдавать бота только после зелёного полного `scripts/nmbot_test_agent.py` и контрольного UX-review.
- **Локальные правки в `scripts/chat_tester_bot.py`:**
  1. Добавлен renderer числовых `min_price`/`max_price` в человекочитаемую цену с `млн`, если `price_range`/`price` отсутствуют.
  2. Добавлен conservative detector пустого out-of-region search card: `facts=[]`, `near=[]`, а `missing`/`params` явно говорят про Санкт-Петербург/вне рабочего региона/ограничение базы Москвой и МО.
  3. `OvermindClient.ask()` больше не превращает такой out-of-region empty card в `SAFE_UPSTREAM_ERROR_TEXT`, а пропускает его в answer stage, где `prompts/chat_v1.txt` уже содержит правильный ответ по Москве/МО.
  4. Добавлен финальный UX guard против фразы `по вашему запросу` и близких формулировок.
- **Проверки после правок:**
  - `python3 -m py_compile scripts/chat_tester_bot.py scripts/nmbot_test_agent.py` — pass.
  - `python3 scripts/nmbot_test_agent.py --suite golden` — **3/3 pass**.
  - `python3 scripts/nmbot_test_agent.py --suite stateful` — **1/1 pass**.
  - Полный `python3 scripts/nmbot_test_agent.py` — **115/117 pass**, то есть ещё не release-ready.
- **Дополнительная локальная проверка после static-fix (без полного дорогого gate):**
  1. `codex/non_realty_redirect` — root cause найден статически: `_search_result_payload()` возвращал `False` для не-JSON текста, хотя callers ждут `dict | None`; исправлено на `None`. Targeted `python3 scripts/nmbot_test_agent.py --suite codex` — **5/5 pass**.
  2. `golden/golden_msk_budget` — локальный renderer подтверждает вывод цены из `min_price`/`max_price` как `4.99 млн–13.8 млн`; targeted `python3 scripts/nmbot_test_agent.py --suite golden` — **3/3 pass**.
- **Prod deploy / targeted verification (VPS):**
  1. Runtime synced to `/home/neiro/novostroy-bot/scripts/chat_tester_bot.py`; valid rollback backups: `backups/deploy-20260713-175444` and `backups/deploy-20260713-180417`.
  2. `python3 -m py_compile scripts/chat_tester_bot.py scripts/nmbot_test_agent.py` on VPS — pass; `novostroy-bot.service` restarted, active/running since `2026-07-13 18:04:22 UTC`, PID `1953466`.
  3. Post-restart `bot_error_events-2026-07-13.jsonl` filter from `18:04:22Z` — `POST_RESTART_ERROR_EVENTS 0`.
  4. Targeted VPS `python3 scripts/nmbot_test_agent.py --suite codex` — **5/5 pass**.
  5. Targeted VPS `python3 scripts/nmbot_test_agent.py --suite golden` initially showed `golden_msk_budget` fail again; static root cause: deterministic stage presenter used `_extract_options()` and kept numeric `min_price` as raw value, bypassing the human `млн` formatter. Fixed generally by using `_format_item_price(item)` in `_extract_options()` for `price_range`/`price`.
  6. After second deploy/restart, targeted VPS `python3 scripts/nmbot_test_agent.py --suite golden` — **3/3 pass** (`golden_kotel_renov`, `golden_msk_budget`, `golden_spb_redirect`).
- **Остаток после prod targeted green:** полный `python3 scripts/nmbot_test_agent.py` после static-fix/deploy сознательно **не запускался**: пользовательское ограничение — не гонять весь gate, только проблемные кейсы. Поэтому статус не равен full release-ready.
- **Cost/process guard:** после первого нового full-gate результата нельзя повторно запускать дорогие Overmind/model suites без локального разбора первого failure. Сначала inspect уже полученный stdout/logs/code, затем только минимальный таргетированный smoke.
- **Model/fallback guard:** перед изменением моделей, fallback, retry или stage-routing сначала фиксировать `Actual / Contract / Desired` и слой (`main_search`, `chat`, `operator`, `transport` или `fallback`). Если `payload_stage` не подтверждён логом, разрешена только диагностика; search-fallback нельзя подменять chat-fallback'ом.
- **Статус:** **prod targeted green**. Проблемные кейсы закрыты на VPS targeted suites, service жив, post-restart error-events пустые. Нельзя говорить «бот полностью готов», потому что полный readiness gate после deploy не запускался по явному ограничению пользователя.

### H031 — Deterministic H4 initial shortlist renderer from `facts[]` (2026-07-17, **закрыта: prod API verified, Jivo E2E pending**)
- **Причина:** H4 initial shortlist для семейного подбора должен быть детерминированным: клиентский текст строится кодом из `search_response.facts[]`, а не финальной прозой LLM. Это убирает риск выдуманных преимуществ и сохраняет только факты, которые реально пришли из search/MCP.
- **Actual:** успешный H4 matched shortlist мог доходить до `four_layer_presenter_v2`, где финальная клиентская проза снова авторилась LLM. Даже при хорошем `facts[]` это оставляло риск лишних полей, отсутствующих преимуществ или слишком свободного текста.
- **Contract:** наружу можно выводить только allowlisted facts из `facts[]`; максимум три нумерованные карточки; каждая карточка даёт `name/location/price` и один benefit, который подтверждён фактом карточки (`schools`, `kindergartens`, `parks`, `infrastructure` и похожие семейные поля); отсутствующие поля не додумываются; в конце ровно один вопрос.
- **Desired:** H4 successful initial shortlist рендерится кодом из matched `facts[]`: без LLM final prose, с безопасным allowlist, короткими карточками и одним следующим вопросом.
- **Локальные проверки:**
  - `python3 -m py_compile scripts/chat_tester_bot.py scripts/nmbot_mcp_only_sim.py` — pass.
  - `pytest -q tests/test_four_layer_runtime_contract.py` — **20/20 pass**.
  - `python3 scripts/nmbot_mcp_only_sim.py --search-json logs/sim_fixture_four_layer_family_cards.json --no-journal` — fixture simulation pass: deterministic 3-card family shortlist with parks/schools/infrastructure and exactly one final question.
- **VPS deploy / production API verification:**
  1. Backup на VPS: `/home/neiro/novostroy-bot/backups/deploy-20260717-063605/chat_tester_bot.py`.
  2. Runtime file synced to `/home/neiro/novostroy-bot/scripts/chat_tester_bot.py`.
  3. VPS `py_compile` — pass.
  4. `novostroy-bot-api.service` restarted and active since `2026-07-17 06:36:10 UTC`, PID `3112056`; API health OK.
  5. Real private `/api/chat` VPS smoke returned rich deterministic 3-card `main_search` response with exactly one question. This proves the production API renderer.
- **Caveat / not green:** это **не** full Jivo E2E green. Public bridge smoke used an incomplete synthetic Jivo payload: bridge/upstream returned 200, but Jivo send returned HTTP 400 because `site_id`, `chat_id` and `client_id` were null. Browser Jivo test was blocked by the visitor contact form: phone field inaccessible, no actual webhook and no delivered `BOT_MESSAGE` observed. Честный статус: production API verified, browser Jivo E2E pending contact-gate.
- **Статус:** **закрыта как `prod_api_verified_jivo_e2e_pending`**. Production API renderer доказан; Jivo browser E2E остаётся pending из-за contact-gate.

### H032 — Единый production-журнал Jivo (2026-07-17, **prod journal verified; recovery snapshot recorded**)
- **Причина:** свежие Jivo-диалоги находились в runtime `data/nmbot_api_state.json`, а поисковый инструмент смотрел только архивные `logs/dialogs-*.jsonl`. Из-за этого один и тот же production-диалог можно было ошибочно принять за отсутствующий или старый.
- **Actual:** Jivo API сохранял короткое окно `dialog_window` в runtime state; bridge structured log хранил только технические события и длины сообщений; `scripts/find_dialog.py` не искал Jivo session state.
- **Contract:** нужен один append-only источник production-диалогов с устойчивым `session_key`, UTC-временем, ролями user/bot, event type и безопасными ref-хэшами; секреты, raw payload и телефоны в журнал не попадают; повторный Jivo event не должен создавать дубли.
- **Desired:** `/home/neiro/novostroy-bot/logs/dialogue_journal.jsonl` — канонический журнал Jivo; `data/nmbot_api_state.json` остаётся только runtime state/cache. Поиск выполняется через `scripts/find_journal.py --prod --q ...`.
- **Локальная реализация:** `scripts/dialogue_journal.py` пишет атомарные JSONL-события с `schema_version=1`, UTC `ts`, phone redaction и hashed refs; `_process_jivo_client_message_uncached` пишет user+bot после Jivo dedup boundary; `scripts/find_journal.py` поддерживает AND/OR-поиск и JSON-вывод; добавлен Jivo dedup/redaction test.
- **Локальная проверка:** `py_compile` трёх скриптов — pass; `pytest -q tests/test_nmbot_api_jivo_p1.py` — **33/33 pass**; missing-path search probe корректно вернул no-match.
- **VPS deploy / live verification:** backups `deploy-20260717-065525`, `deploy-20260717-065954` и privacy backup `logs/dialogue_journal.jsonl.before-safe-20260717-070240`; `dialogue_journal.py`, `find_journal.py`, `import_state_to_journal.py` и API синхронизированы; VPS `py_compile` прошёл; `novostroy-bot-api.service` restarted and active (PID `3125160`, since `2026-07-17 06:59:58 UTC`). Первый smoke с неверным ключом `event_name` был остановлен как payload-shape failure; корректный `event=CLIENT_MESSAGE` создал две валидные journal rows (`user` + `bot`) с hashed refs и redacted text. State migration импортировала 103 строки, затем journal был санитизирован: `rows 105 unsafe_keys 0 unsafe_raw False`. После privacy deploy новый API smoke добавил ещё две строки; итоговый журнал содержит 107 обычных строк плюс recovery snapshot.
- **Восстановление конкретного диалога:** к моменту миграции live `nmbot_api_state.json` уже переиспользовал тот же Jivo session ref для другого диалога, поэтому исходная последовательность про `30 млн` не была найдена в state backup (резервной копии state-файла не было). Ранее подтверждённые шесть сообщений были добавлены отдельно с `source=state_snapshot_recovery` и `event_type=recovered_state_snapshot`; это не притворяется обычной live-записью. Production search `scripts/find_journal.py --prod --q '30 млн' --q 'Новой Москве'` теперь находит все шесть строк по conversation group.
- **Статус:** production journal verified; runtime state остаётся cache, canonical search — `scripts/find_journal.py --prod --q ...`; новые Jivo state windows импортируются с `source=state_migration` и дедупликацией, а вручную восстановленные исторические фрагменты всегда помечаются `state_snapshot_recovery`. Все journal rows проверены на отсутствие raw `session_key`/`conversation_id`.

### H033 — Стандарт troubleshooting для MCP/search constraints (2026-07-17, **accepted: reusable standard**)
- **Когда применять:** бот сообщает `0` вариантов после изменения бюджета, района, комнат или другого ограничения, особенно если результат противоречит известному ассортименту.
- **Actual / Contract / Desired:** отдельно зафиксировать, что реально распарсил бот; затем сверить это с контрактом canonical search plan и envelope; только после этого сформулировать ожидаемое поведение. Не называть причину багом, пока Actual и Contract не сопоставлены с Desired.
- **Шаг 1 — цепочка данных:** прочитать нормализацию параметров (`_canonical_search_constraints_patch`, `_params_with_canonical_search_constraints`, `_search_hard_constraints_for_ask`) и фактический outgoing `constraints.hard/preferences/unknown`. Проверить, что бюджет, комнаты и география попали в разрешённые поля и не стали лишним hard-фильтром.
- **Шаг 2 — read-only replay:** запускать probes последовательно, по одному. После каждого первого результата сразу читать counts, normalized params и structured response; при ошибке остановить batch. Production-код до завершения replay не менять.
- **Шаг 3 — альтернативные кодировки:** сравнить одну и ту же задачу в разных допустимых представлениях (`location=["Новая Москва"]` против `district="newmsk"`, не меняя бюджет и комнаты). Так отделяется ошибка значения ограничения от ошибки его кодировки.
- **Шаг 4 — evidence gate:** `facts[]` трактовать как точные результаты, `near[]` — как альтернативы. Проверять, что ответ действительно содержит evidence-поля для каждого заявленного ограничения; если MCP не возвращает `rooms`, нельзя утверждать, что room-фильтр доказан.
- **Пример H033:** при `max_price=30000000` Москва дала `facts=20`, а `district=newmsk` — `facts=0`; та же Новая Москва через `location=["Новая Москва"]` дала `facts=9`. Бюджет распарсился корректно; подозрение локализовано на кодировке географии, но исходный MCP payload исторического диалога был утрачен, поэтому вывод помечен как replay-based, не как доказанный production root cause.
- **Приёмка стандарта:** сохранены Actual/Contract/Desired, outgoing envelope и результаты последовательных probes; явно указано, какие claims подтверждены, а какие поля отсутствуют; production change допускается только после воспроизводимого replay.
- **Связанные источники:** `scripts/nmbot_api_server.py`, `scripts/nmbot_four_layer_e2e.py:501–526`, `scripts/nmbot_search_mini_probe.py`, `tests/test_search_hard_constraints_contract.py`.

### H034 — Нормализация `newmsk` перед search-envelope (2026-07-17, **local + production-shaped replay accepted; VPS deploy pending**)
- **Цель:** не отправлять в MCP внутренний alias `district=newmsk`, если рабочая поисковая форма — `location=["Новая Москва"]`.
- **Изменение:** `_normalize_hard_constraints()` в `scripts/chat_tester_bot.py` переводит `district=newmsk` в `location=["Новая Москва"]`; при явном `location` пользователя сохраняет его и удаляет `district`/`districts`. `max_price` и `rooms` не изменяются. Автоматический повторный поиск при нуле не добавлялся.
- **Тесты:** добавлены проверки alias mapping, отсутствия одновременных `district`/`location`, сохранения бюджета/комнат и приоритета явного `location`; `tests/test_search_hard_constraints_contract.py` — **7/7 pass**, `tests/test_nmbot_api_jivo_p1.py` — **33/33 pass**, `py_compile` — pass.
- **Replay:** первый изолированный `nmbot_four_layer_e2e.py` replay был нерепрезентативен для production path и вернул `facts=0` с `district=newmsk`. Настоящий `OvermindClient.ask()` после фикса показал query с `location=["Новая Москва"]`, без `district`, и вернул варианты до 30 млн для 1–2 комнат; room exactness по-прежнему не доказана без `rooms` в MCP evidence.
- **Статус:** локальный контракт и production-shaped replay приняты; VPS deploy и Jivo smoke ещё не выполнены. Автоматический retry пустого результата не добавлялся.

### H035 — Конкретные first-list карточки без канцелярита (2026-07-17, **local accepted; VPS pending**)
- **Actual:** production-ответ после повышения бюджета до 60 млн показал два найденных ЖК, но потерял цену и метро, оставил `white box` и заменил доступную инфраструктуру общими фразами «приятный плюс» / «добавляет удобства».
- **Contract:** `docs/IDEAL_IRINA_UX.md:37–45,63–78` требует до трёх коротких карточек с подтверждёнными фактами и отдельной фактической пользой; `docs/BOT_ARCHITECTURE.md:190–207` запрещает внешние claims и требует sales phrase из фактов.
- **Desired:** JSON остаётся единственным источником истины; карточка сохраняет цену и метро, `white box` нормализуется в «предчистовая отделка», а benefit прямо называет доступные школы, детские сады, парк, двор, безопасность или спортивные площадки без внутренних слов вроде «подтверждённые детали».
- **Изменение:** обновлены `_client_finishing_fact`, порядок `_stage_option_fact_parts` для self-use и детерминированный `_stage_option_benefit`; официальное название `ЖК «Лужники Collection»` сохраняется. При sparse facts renderer использует только имеющиеся срок, отделку, цену или метро и ничего не выдумывает.
- **Проверка:** regression fixture воспроизводит `ЖК «Событие»` / `ЖК «Лужники Collection»`; отдельный sparse case проверяет отсутствие выдуманной инфраструктуры. `py_compile` — pass; целевые four-layer pytest — **61/61 pass**; новый renderer-case и sparse-case в H029 — pass. Общий H029 — **57/58**, единственный fail существующий и не связанный с renderer: `phone_captured_has_human_farewell` ожидает слово «оператор», а текущий farewell использует «специалист». Production deploy и Jivo E2E не выполнялись.

### H036 — Jivo input boundary: безопасная нормализация текста (2026-07-17, **local accepted; VPS pending**)
- **Actual:** smoke с текстом в неправильном `payload.text` давал пустой user turn и fallback; malformed JSON падал HTTP 500 через `request.json()`.
- **Contract:** канонический текст находится в `message.text`; входной boundary не должен передавать пустое сообщение в `run_chat`.
- **Desired:** принимать канонический `message.text`, безопасно поддерживать legacy `payload.text`, а malformed JSON/отсутствующий текст возвращать контролируемым HTTP 400 без пустого журнала и без запуска поиска.
- **Изменение:** `handle_jivo` ловит JSON parsing errors; `_normalize_jivo_payload` нормализует оба формата и fail-closed при пустом тексте. Добавлены regression-тесты.
- **Проверка:** API Jivo tests — **35/35 pass** после добавления malformed JSON case; production deploy pending.

### H037 — Единый deploy/status инструмент (2026-07-17, **local accepted**)
- **Цель:** убрать ручное расхождение между deploy, restart, health и проверкой версии runtime.
- **Инструмент:** `scripts/nmbot_release.py status` показывает service/PID/время запуска, health и SHA-256 локальных и VPS-файлов; `scripts/nmbot_release.py deploy` делает backup, SCP выбранных runtime-файлов, удалённый `py_compile`, restart `novostroy-bot-api.service` и повторный status. По умолчанию используются `scripts/chat_tester_bot.py` и `scripts/nmbot_api_server.py`.
- **Проверка:** `py_compile`, `--help` и live `status` прошли; локальный и VPS SHA-256 совпали, сервис `active/running`, health `ok=true`. Секреты не выводятся. Инструмент не меняет VPS при команде `status`.

### H039 — Структурное подтверждение комнатности (2026-07-17, **prod deployed; full Jivo scenario pending**)
- **Actual:** MCP иногда возвращал проекты без структурных полей `rooms` / `apartment_types.rooms`, хотя свободный текст модели мог утверждать наличие комнат. При жёстком фильтре `rooms=1,2` выдача могла стать пустой.
- **Contract:** комнатность считается подтверждённой только структурными MCP-полями `rooms`, `room_types`, `apartment_types.*.rooms` или `ads.*.rooms`; свободный prose не является evidence. Неподтверждённый вариант нельзя выдавать как подходящий и нельзя утверждать, что квартир нет.
- **Изменение:** `scripts/chat_tester_bot.py` фильтрует first-list по подтверждённой комнатности, скрывает неподтверждённые ЖК, сообщает о невозможности подтверждения и предлагает передать запрос менеджеру только через отдельное согласие. Телефон не запрашивается в том же сообщении.
- **Проверка:** локальные API/four-layer тесты — **61/61 pass** до последующего stateful набора; production runtime синхронизирован через unified release tool вместе с H040.
- **Статус:** код развернут на VPS; полноценный Jivo-сценарий с реальным MCP room evidence ещё требуется отдельно проверить.

### H040 — Stateful context и естественная инвестиционная подача (2026-07-17, **prod deployed; contextual Jivo scenario pending**)
- **Actual:** planner получал неполный предыдущий ответ; из-за этого `а что за кроштатский` и короткое `да` могли приводить к повторному вопросу о цели покупки. Инвестиционный renderer также выдавал внутреннюю фразу «проверяемые опоры» и допускал неподтверждённые claims о росте/доходности.
- **Contract:** предыдущий ответ, последний вопрос, текущие варианты, primary intent и безопасный search snapshot должны передаваться в следующий turn; ответы должны строиться только по MCP-фактам.
- **Изменение:** `scripts/nmbot_api_server.py` теперь передаёт в planner `last_response_text`, `visible_response_text`, primary intent и bounded search snapshot; сохраняет known intent, обрабатывает contextual `да` и использует безопасный уникальный fuzzy fallback для опечаток в названии ЖК. `/start` обновлён до приветствия Ирины. `scripts/chat_tester_bot.py` убирает технические investment/rental phrases и sanitizes unsupported growth/income/liquidity claims.
- **Локальная проверка:** `py_compile` и `pytest -q tests/test_nmbot_api_jivo_p1.py tests/test_four_layer_runtime_contract.py` — **67 passed**; API smoke — pass.
- **VPS deploy:** `scripts/nmbot_release.py deploy scripts/nmbot_api_server.py scripts/chat_tester_bot.py`; backup `/home/neiro/novostroy-bot/backups/deploy-20260717-085656`; `novostroy-bot-api.service` active, PID `3148500`, since `2026-07-17 08:57:06 UTC`; health `ok=true`; local/VPS SHA-256 совпадают.
- **Live smoke:** корректный Jivo `CLIENT_MESSAGE` с `message.text=/start` вернул HTTP 200 `BOT_MESSAGE`; journal записал `start_reset`; свежих ошибок service journal нет. Полный production multi-turn сценарий `список → опечатка → да` пока не прогонялся.

### H041 — Enriched top-3 и сценарные карточки в стиле консультанта (2026-07-17, **prod Jivo verified**)
- **Actual:** первый список формировался до enrichment и терял подробности ЖК; broad-география `Москва` дополнительно давала ложный no-results, когда MCP возвращал район в `location`, но не копировал региональный код `district=msk`. После первого production-прогона также обнаружились две ошибки оформления: разрыв фразы `Подобрала три варианта / для инвестиций` и абзац, начинающийся со строчной `цены`.
- **Contract:** `docs/BOT_ARCHITECTURE.md:184–194` требует сначала shortlist, затем enrichment top-3; `docs/IDEAL_IRINA_UX.md:17–23,37–45,63–78` разрешает только факты MCP, максимум три карточки и один итоговый вопрос. `docs/NOVOSTROYM_MCP_SCHEMA.md:23–24` разделяет региональный `district=msk|mo|newmsk` и район/локацию.
- **Изменение:** top-3 обогащаются параллельно с общим timeout до первого ответа; порядок сохраняется, ошибки отдельных enrichment-вызовов откатываются к базовой карточке. Детерминированный renderer выбирает разные подтверждённые стратегии и связывает факт с пользой/компромиссом для `investment`, `family`, `rental`, `self_use`. Search prompt требует сохранять `district` отдельно от `location`; broad validator доверяет exact `facts[]` без district, если есть конкретная районная локация, но продолжает отвергать явный конфликт региона и сохраняет строгие budget/rooms/specific-area проверки.
- **Copy fix:** `_format_paragraph_spacing` теперь отделяет `Для инвестиций...` только после настоящей границы предложения; price-benefit начинает новый абзац с заглавной буквы. Добавлен regression test на обе production-фразы.
- **Локальная проверка:** `py_compile` и `pytest -q tests/test_h041_scenario_enrichment.py tests/test_four_layer_runtime_contract.py tests/test_search_hard_constraints_contract.py` — **46 passed**. Тесты проверяют top-3 timeout/cache/fallback, разные fact-backed стратегии, отсутствие внутренних labels и неподтверждённых claims, broad geo без false-negative, максимум три карточки и один вопрос.
- **VPS deploy:** geo/prompt release — backup `backups/deploy-20260717-103145`, API PID `3166123`; copy release — backup `backups/deploy-20260717-103722`, `novostroy-bot-api.service` active/running PID `3167181` since `2026-07-17 10:37:31 UTC`; health OK; local/VPS SHA-256 `scripts/chat_tester_bot.py` совпадает (`8bc21948099204353ba2cf2ae51d11b403be19dbd089155b912743310269a0f4`).
- **Production Jivo evidence:** synthetic `CLIENT_MESSAGE` `Ищу квартиру в Москве под инвестицию до 60 млн` вернул HTTP 200 и три карточки: «Символ», «Матвеевский парк», «Кронштадтский 9». Canonical journal `2026-07-17T10:38:59.338630Z`: `cards=3`, `questions=1`, `broken_intro=false`, `lowercase_price_paragraph=false`; API service после запроса остался active.
- **Статус:** production Jivo path подтверждён для broad investment shortlist; enrichment остаётся best-effort per item и не разрешает выдумывать отсутствующие факты.

### H042 — Room recovery без скрытого повторного hard-фильтра (2026-07-17, **prod Jivo verified**)
- **Симптом:** после `/start` запрос `Двушка в москве под инвестицию` возвращал generic operator fallback `По запросу не удалось найти информацию`, хотя broad inventory существовал.
- **Actual:** первый hard-room поиск вернул `facts=0`; recovery формально удалял `rooms` из `SEARCH_CONTRACT_ENVELOPE`, но повторно передавал комнатность через исходную фразу `Двушка...` и `dialog_context.params.rooms=2`. Search model продолжал трактовать второй вызов как room-filter и снова возвращал пустой список. Live state до фикса: `last_result.found=false`, `snapshot facts=0`, `district=msk`, `rooms=2`.
- **Contract:** второй поиск должен быть широким по всем остальным hard-условиям; нужная комнатность в нём является только полем для сбора structured evidence. Исходный room-фильтр не должен попадать ни в envelope, ни в query, ни в safe context. Финальный H4 validator по-прежнему показывает только ЖК, где нужный формат подтверждён структурными полями.
- **Изменение:** `_room_broad_evidence_query` больше не повторяет исходный клиентский текст и прямо запрещает применять нужный формат как search-фильтр; `_without_room_constraint_keys` рекурсивно удаляет `rooms`, `room_type`, `room_types` из recovery context. Location/purpose/budget и другие hard-условия сохраняются.
- **Regression:** test проверяет два gateway-вызова, отсутствие `rooms` в recovery envelope/context, отсутствие исходной фразы `Двушка в москве под инвестицию`, наличие broad-инструкции `до 5 ЖК`, и финальную публикацию только room-confirmed ЖК. `py_compile` и четыре targeted suites — **86 passed**.
- **Deploy:** `scripts/nmbot_release.py deploy scripts/chat_tester_bot.py`; backup `backups/deploy-20260717-104524`; `novostroy-bot-api.service` active/running PID `3168758` since `2026-07-17 10:45:30 UTC`; health OK; local/VPS hash `dda8ce927f3345a1ae0dadd4777b8c0af332bed50c032cc7351cbef13e7dd613` совпадает.
- **Production Jivo evidence:** точный запрос `Двушка в москве под инвестицию` вернул три room-confirmed карточки: «Бусиновский парк», «Лосиноостровский парк», «Мичуринский парк». Canonical journal `2026-07-17T10:46:06.129007Z`: `cards=3`, `questions=1`, generic no-info отсутствует. State: `found=true`, `exact_count=3`, `near_count=0`, `visible_count=3`, snapshot `facts=3`; service active после запроса.
- **Статус:** production Jivo room-search path подтверждён; generic fallback больше не возникает из-за повторной скрытой room-фильтрации.

### H051 — MCP wire-shape normalization for V2 search (2026-07-19, **accepted locally; live probe partial**)
- **Гипотеза:** MCP возвращает факты корректно, но значения wire-формата нельзя сравнивать только с одним Python-типом. Иначе валидатор будет ошибочно отбрасывать реальные ЖК.
- **Actual:** live `v2_search_mcp` probes подтвердили: `rooms` приходит строкой (`"2-комнатные"`, перечень комнат), `delivered` приходит числовым флагом `1/0`, `location` может быть списком. В `family_financing_overlay` facts были валидны, но модель не повторила служебные diagnostics; это исправлено runtime-нормализацией diagnostics. В `rooms_budget_location` и `ready_finishing` сначала обнаружились ошибки сопоставления типов.
- **Contract:** `district` — только `msk|mo|newmsk`; `location` — отдельная строка/список локаций; комнатность подтверждается только структурным `rooms`; `delivered=1` означает сданный дом, `delivered=0` — нет; будущий квартал не равен сдаче. `facts[]`, `near[]`, `missing`, `params` не смешиваются с runtime diagnostics.
- **Изменение:** добавлены канонические token-нормализации `rooms`, готовности и числового `delivered`; diagnostics достраиваются из входного typed request, а не принимаются на веру от модели; probe-validator безопасно обрабатывает списки `location` и region-коды.
- **Regression:** contract покрывает строки/списки комнат, `2-комнатные`, studios-only mismatch, `1` против `10`, `delivered=true/false/1/0`, будущие сроки, списковую `location`, diagnostics overlay. Offline fixture: все 15 сценариев зелёные; полный локальный набор после правок — **397 passed**.
- **Live probe:** `base_search` — strict JSON, `facts=0`, `missing=1`, 3.05 сек; `family_financing_overlay` после runtime-normalization — `facts=3`, `missing=5`, 7.28 сек; `rooms_budget_location` после room-normalization — `facts=2`; `ready_finishing` после `delivered=1` normalization — `facts=3`. Batch остановился позже на probe-validator TypeError для списковой `location`; исправление локализовано, повторный live batch ожидает отдельного запуска.
- **Следующий gate:** не подключать V2 search к продакшену до завершения оставшихся live-сценариев после последней probe-validator правки и проверки Jivo/VPS.

### H052 — Offline V2 answer quality gate and first-failure harness (2026-07-19, **local only**)
- **Actual:** V2 search contract already had 15 fixture scenarios, but there was no single local standard that evaluated the full chain `search output → normalized card → rendered answer` against UX 9–10/10. Existing checks were useful, but split across docs/tests and did not produce one row with layer-to-fix routing.
- **Contract:** `docs/IDEAL_IRINA_UX.md:17–23,37–45,63–78,191–228`, `docs/LLM_SCENARIO_EVAL_RUBRIC.md:289–454`, `docs/CARD_PRESENTATION_RULE.md` and the V2 MCP contract require grounded facts, max three cards, exactly one final question, no technical leaks, no unsupported investment/rental/finance claims, no early operator, and layered reporting.
- **Desired:** deterministic offline quality gate with no network/model/MCP calls. It must stop on first failure by default, rerun a single case, optionally write a Markdown report, and route failures to search prompt/contract, normalization, response renderer, or state/planner.
- **Local implementation:** added `docs/NMBOT_V2_ANSWER_QUALITY_GATE.md`, `nmbot_v2/quality.py`, fixture `tests/fixtures/nmbot_v2_quality_scenarios.json` with 15 deterministic records, CLI `scripts/nmbot_v2_quality_gate.py`, and focused pytest coverage. The harness builds a V2SearchRequest from the existing contract fixture, normalizes MCP-like output, maps it into `SearchResult/OptionCard`, renders via `build_response_plan/render_response`, then evaluates hard blockers and 0–10 scoring.
- **Local verification only:** `python3 -m py_compile nmbot_v2/quality.py nmbot_v2/response.py scripts/nmbot_v2_quality_gate.py` — pass; targeted pytest suite for V2 quality/search/runtime — **67 passed**; `python3 scripts/nmbot_v2_quality_gate.py --all` — **15/15 pass** in default First-Failure mode; explicit `--report` wrote `logs/nmbot_v2_quality_gate.md`. No SSH, deploy, restart, live model call, MCP call, promptfoo or eval was run.
- **Status:** local quality harness standard. It is not live/prod readiness; final customer copy still requires manual reading of report samples.
- **2026-07-19 addendum after manual false-green audit:** hardened the quality standard and deterministic evaluator against duplicate intro/summary, `white box`/internal enum leaks, repeated identical benefits, dry one-line cards, and `count_ads`→sales semantic conflation. `OptionCard` now separates `ads_count` from `sales_count`; renderer presents cards as separate two-line blocks with scenario-specific fact→difference→benefit reasons and localized finance/finishing copy. Local verification only: first-failure gate initially stopped on `family`, then `family_financing_overlay`, `investment`, `rooms_budget_location`, and `missing_data`; after generic fixes reran failing cases and full offline-15 passed. No SSH, deploy, restart, live model/MCP call, promptfoo or eval was run.
- **2026-07-19 live quality and maturity addendum:** added `docs/NMBOT_V2_PROJECT_QUALITY_SCORECARD.md` and machine-readable `quality_profile` with eight 0–10 dimensions: search accuracy, data integrity, presentation, language, scenario fit, dialogue continuity, reliability and latency. Weights are 20/20/15/10/10/10/10/5. Live `base_search`, `family` and `family+financing` reached customer-quality gates after generic contract fixes; `life` remained a FAIL because the primary response composer and repair returned invalid JSON and the safe deterministic fallback had to answer. Current evidence-based project estimate is **7.3/10 (beta)**, but hard gate is **not canary-ready** until primary structured output works without repair/fallback and remaining live/Jivo scenarios pass.
- **2026-07-19 V2 structured composer and V1-safe infrastructure addendum:** V2 composer now uses a strict structured response contract (`intro`, per-option `{name,facts,description}`, `missing_note`, `final_question`); runtime assembles customer text and validates each section independently. The migration also adopted V1-proven infrastructure without importing V1 business routing: bounded top-3 enrichment cache with exact identity/structured merge/fallback-to-base, stateful search→refine→select→operator regression, and compare-current routing. V1 presenters, `OvermindClient.ask`, fuzzy business routing and background response mutation remain excluded. Any hard blocker, including `composer_degraded_fallback`, now sets scorecard maturity to `failed_gate` even if weighted score is high. Local regression gate: **141 passed** for V2 runtime/adapter/contracts/normalizer/quality/replay/search/provider retry. Production/Jivo verification remains required.

### H053 — Compact MCP query envelope for V2 search (2026-07-19, **hypothesis accepted; runtime integration pending**)
- **Гипотеза:** V2 system prompt должен получать компактный per-turn envelope и отдельную естественную строку `Клиент: ...`; повтор полного длинного `V2_SEARCH_INPUT` рядом с system contract перегружает модель и может давать валидный, но пустой `facts[]`.
- **Матрица:** при одинаковых model/MCP/query V2 prompt + compact envelope дал 3 strict-valid facts; V2 prompt + большой contract query дал 0 facts; minimal prompt переизвлекал до 15 объектов и терял поля; V1 prompt находил ЖК, но синтезировал устаревший `price_range` и нарушал V2 wire contract.
- **Переносимость:** целевой compact shape проверен на family, family+financing, rooms+budget+location, delivered+finishing, district+location. Evidence map исправил room-hard публикацию, но повторный financing run снова показал карточку без `rooms`; следовательно prompt не заменяет enrichment/validator.
- **Контракт:** правила зафиксированы в `docs/NMBOT_V2_MCP_PROMPT_BUILD_RULES.md`: system prompt владеет постоянной schema/grounding; query содержит короткий envelope, текущие параметры и естественный клиентский запрос; hard evidence map runtime-owned; broad geography нормализуется в `district`; room/finance/lot evidence требует bounded enrichment; probes импортируют allowlist из `nmbot_v2/search_contract.py`.
- **Артефакты:** `scripts/nmbot_v2_mcp_prompt_matrix.py`, `scripts/nmbot_v2_mcp_winning_prompt_series.py`; production остаётся V1. Подключение compact builder к V2 runtime запрещено до contract regressions и повторного live Jivo gate.

### H054 — Independent Jivo V2 product boundary (2026-07-21, **open; production deployed/tested, phase 7 in progress**)

- **Hypothesis:** если убрать environment-dependent V1 default/legacy runner из
  authoritative Jivo route, нормализовать сохранение в canonical V2 envelope и
  сделать release/diagnostics V2-aware, продукт станет проще и надёжнее без
  изменения моделей, количества LLM/MCP-вызовов или клиентского контракта.
- **Actual:** `run_chat()` всегда передаёт `_run_chat_v1`; adapter по умолчанию
  выбирает V1; tests закрепляют этот default. Canonical `nmbot_v2` сохраняется
  внутри legacy envelope и diagnostics могут ошибочно читать пустые root-поля.
  Release manifest по умолчанию перечисляет legacy `chat_tester_bot.py`, а не
  полный V2 runtime.
- **Contract:** текущий production-продукт — только Jivo API+bridge V2. Telegram
  и V1 остаются историческим rollback, но не могут быть неявным fallback текущего
  продукта. Existing legacy records мигрируются bounded one-way reader; новые
  turn writes канонические. LLM остаётся владельцем semantic decision; код не
  получает новый scenario router.
- **Desired:** один прямой V2 runtime path, отсутствие silent V1 switch, truthful
  V2 release manifest, canonical-first diagnostics, отдельные compound/multi-
  scenario regressions и несколько production Jivo dialogue checks.
- **Baseline:** три production dialogue sessions, 11 turns, zero fresh errors.
  Selected four-fact compound request passed; family+rental+financing collapsed
  to one viewpoint; one three-fact follow-up omitted finishing; contact
  interruption was safe but operator reason drifted on resume.
- **Success criteria:** direct API smoke imports no Telegram/V1 module; V1 is not
  selectable through Jivo adapter; canonical state migration and contact/search
  saves pass; targeted/full suites green; diagnostics report canonical state and
  stage call counts; docs agree on current route; post-deploy compound dialogues
  preserve all requested needs or explicitly name missing ones.
- **Evidence report:**
  `reports/NMBOT_V2_INDEPENDENCE_AND_DIALOGUE_AUDIT_2026-07-21.md`.
- **2026-07-21 local multi-scenario addendum:** introduced bounded
  `scenario_needs` (`family|rental|investment|life|financing`) while retaining one
  primary `response_viewpoint`. Existing `SemanticPlan.facets` carries the needs;
  search unions their field priorities but does not make them hard filters.
  Native current-options copy acknowledges combined goals with grounded facts and
  one CTA. One isolated live planner case preserved
  `family+rental+financing`. The model also returned a non-essential clarification;
  runtime consistency now gives explicit `requests_new_objects=true` precedence,
  clears that question for FIRST_LIST, and preserves genuine ambiguity handling.
  Local evidence: planner fixture `16/16`, focused gate `67 passed`, full suite
  `654 passed` with 631 existing aiohttp warnings. At that moment production
  deploy remained open; the production deployment addendum below supersedes this
  local-only status.
- **2026-07-21 production deployment addendum:** H054 is deployed and production
  tested, but remains **open**. Phase 7 is still `[in_progress]`; do not close the
  todo/phase. First production failure was not that Gemini lost the compound
  meaning: raw planner had `scenario_needs=family,rental,financing`, but the
  canonical wrapper exposed the needs only nested. Runtime normalized only
  top-level needs, so the fix was additive top-level `scenario_needs` passthrough
  in `followup_intent_classifier.py`. Exact live retry acknowledged family +
  future rental + mortgage, returned three cards and one final question; errors
  stayed `239 -> 239`. Source anchors:
  `logs/planner_trace-2026-07-20.jsonl:208-210`,
  `logs/planner_trace-2026-07-21.jsonl:1-20`.
- **Selected-object H054 addendum:** selected `finishing` was absent from runtime
  `ALLOWED_FACTS`, `present_fact_names` and renderer. Added finishing contract and
  renderer support. Live selected turn now covers finishing with honest missing
  copy, metro and mortgage; a separate four-fact selected turn covers readiness,
  apartment price, parking and parking price. Errors stayed unchanged.
- **Selected multi-scenario/contact addendum:** selected-scope interruption used
  to answer only mortgage. Renderer now uses existing benefit/caveat helpers for
  grounded selected scenario context, so family, future rental and mortgage are
  all represented and the property question is not swallowed as a contact name.
  Contact state is preserved: substantive selected financing/fact turns no
  longer overwrite pending `contact_name`/`contact_phone` with
  `financing_consent`.
- **Resume variability addendum:** prompt-only `resolved_intent` was unstable.
  Added bounded `CONTACT_NAME_FOLLOWUP` reply contract with sole outcome
  `resume_contact`, planner contact envelope, semantic signal and existing
  transition mapping. Ordinary property questions with null/invalid outcome keep
  the semantic route. Final production resume `Вернёмся к заявке.` returned
  neutral operator wording for Томилинский бульвар plus `Как к вам обращаться?`,
  no mortgage wording, errors `239 -> 239`.
- **Verification/deploy evidence:** final local full suite **670 passed** with 641
  existing aiohttp warnings. Important targeted H054 gates included **176 passed**
  across recipe, adapter, semantic and runtime coverage. Deployment backups in
  this session: `deploy-20260720-235222`, `235811`, `deploy-20260721-000206`,
  `000521`, `001008`, `001504`, `001840`, `002040`, `002216`; final deploy
  `deploy-20260721-002839`. Current services after final deploy: API PID
  `176985`, bridge PID `176987`, both active, health green, hashes matched.
- **Residual diagnostics/risk:** direct `/api/chat` turns do not persist to
  `dialogue_journal.jsonl`; audit-only journal output can show an older webhook
  ref and must not be used as direct API smoke evidence. Direct API evidence here
  is answer text, planner trace and error count. Remaining gates: independent
  shortlist → typo reference → `да` scenario and real widget/bridge delivery
  confirmation. Do not claim final readiness.

### H055 — V1 GPT-5.5 TEST smoke and guarded publication path (2026-07-30, **TEST-only evidence**)

- **Public-field fix release:** `nmbot-v1-public-fields-fix-test-20260729-2105` fixed raw V1 DSL leakage in public output by filtering DSL markers in the V1 public projection/model evidence. One SSH Jivo V1 smoke passed. GPT mode was off in that smoke.
- **Safe flag-helper release:** `nmbot-v1-gpt55-flag-helper-test-20260730-0015`; archive SHA-256 `4be66ce5dcd02450cb73abeb9184912c2103457c49b0b243635e8755dea6a98e`, manifest SHA-256 `b0d02db8cf6e2ce3f74f67d56cb0873dd3d722242d268073645ce065f831508a`. The fresh source snapshot was `vps-source-20260729-211501-e239904c8616`, manifest SHA-256 `fcc3cf8c0eba0aa1e2fdd5f70b029e50cf46bf0af03f52c90f29b03d43a7bf1c`. The release deployed only `scripts/nmbot_env_secrets.py` through a TEST-only policy allowlist.
- **Mode boundary:** `NMBOT_V1_ONE_MODEL_GPT55_MODE` accepts exactly `off|shadow|publish`. During the controlled smoke it was set to `publish` once, then restored to `off` after the first safe fallback.
- **First controlled GPT smoke:** `openai/gpt-5.5` was invoked at stage `v1_one_model_gpt55_response` with one-model prompt `p_df271e92f355`; VPS metrics recorded this in `model_payload_metrics-2026-07-29.jsonl`, and the dialogue journal had the corresponding 21:17:24 turn. The client saw deterministic fallback text because the model candidate failed validation. Raw model text was not retained.
- **Status / limitation:** GPT publish is restored to `off`. This smoke proves only the TEST guard path and fallback safety; it is not release-quality proof for publishing GPT prose.
- **Fallback telemetry candidate:** a telemetry artifact was built but not deployed, because test-release preflight uncovered unrelated local `nmbot_v4` import drift. Candidate source snapshot: `vps-source-20260729-213615-de8e2d131c84`, manifest SHA-256 `af4d13770301c3e2da03aadd88a7e4b8bdc4364506365ab2c9f6f3e79bbc974d`. Treat it as candidate-only: no live claims.

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

### H-MAIN-SEARCH-20260727 — Gemini 3.5 main-search matrix (2026-07-27, **остановлена: transport contract blocked**)

- **Слой:** только `main_search`/MCP. Это не IntentPlan planner и не модель,
  которая пишет клиентский ответ.
- **Цель:** сравнить `google/gemini-3.1-flash-lite-preview` и
  `google/gemini-3.5-flash` на одинаковом typed V2 search request, strict JSON,
  `facts[]`/`near[]`, hard constraints и времени ответа.
- **Исправление методики:** первоначальный diagnostic runner вызывал
  `_run_gateway_request()` и тем самым смешивал выбранную модель с production
  fallback race. Исправленный изолированный runner вызывает ровно один
  `_run_gateway_request_once()` для каждой модели. Production source/config не
  изменялись.
- **Первый сценарий — центр/ЦАО, 2 комнаты, готовый дом:**
  - `google/gemini-3.1-flash-lite-preview` — технически валидный strict JSON за
    7.314 с, параметры запроса корректны, но `facts=0`, `near=0`; это quality
    failure, а не transport failure;
  - `google/gemini-3.5-flash` — timeout/safe fallback за 91.446 с, strict JSON и
    карточки отсутствуют; First-Failure gate остановил остальные восемь calls.
- **Дополнительные изолированные гипотезы:** Flash с `max_tokens=2000` ответил за
  54.993 с malformed JSON; `google/gemini-3.5-flash-lite` — за 16.498 с
  malformed JSON. Уменьшение token budget проблему не устранило.
- **Structured output:** OpenRouter документирует `response_format` для strict
  structured output, и обе Gemini 3.5 модели заявляют поддержку. Однако текущий
  gateway-agent task contract отклоняет добавление `response_format` как
  `Invalid task data for agent`. Локальный NMBot transport передаёт
  `request_data` дальше и не является владельцем этого allowlist.
- **Контракт:** malformed JSON нельзя принимать или чинить ослаблением parser;
  primary и fallback обязаны пройти один strict parsing/validation contract.
  Источник: `docs/MCP_APARTMENT_REQUEST_RULES.md:676-700`.
- **Вывод:** победитель main-search моделей **не выбран**. NMBot-only model swap
  не исправляет подтверждённый downstream blocker. До продолжения матрицы нужно
  read-only проверить активный n8n WF1 `OpenRouter Direct Test`
  (`Zhl8N3oEIleCtCNB`) и gateway-agent schema, затем отдельно согласовать
  additive forwarding `response_format`. Никаких TEST/production deploy или
  переключений модели по этому эксперименту не было.
- **Артефакт диагностики:**
  `/tmp/opencode/nmbot-main-search-model-matrix-20260727/results.json`.
- **Официальный внешний источник:**
  https://openrouter.ai/docs/features/structured-outputs

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

## 2026-07-27 — V0 Answer Writer: Gemini 3.6 и телефонная воронка

- **Слой:** отдельный V0 `conversation_answer` writer после code-owned scenario/search и canonical answer material; V2/V3 composer и manager-rewriter не менялись.
- **Модель:** `google/gemini-3.6-flash` через gateway/OpenRouter, temperature `0.4`, `max_tokens=2000`.
- **Prompt:** `prompts/v0_answer_writer.txt`; writer получает текущую реплику, bounded previous assistant message, response job и validated material и возвращает plain Russian text. Роль — Валерия, тёплый консультант по недвижимости.
- **Routing contract:** субъективное сомнение по текущей подборке остаётся `open_question`/`answer_directly`; harmless off-topic получает короткий ответ в роли Валерии и возвращается к сохранённому real-estate context.
- **Phone contract:** после согласия на операторскую проверку code-owned capture сразу просит фактический номер цифрами. Повторное `да` не повторяет операторский CTA; valid phone queues callback. Decline/new-question/off-topic проходят semantic routing с сохранением выбранного ЖК, topic и options.
- **TEST proof:** release `nmbot-v0-phone-valeria-test-20260727-163545`; Jivo smoke `/start_0 → двушка для семьи → пельмени → Мичуринский парк → да → да → да` прошёл без runtime/provider/operator loop. API/bridge healthy; bridge не перезапускался; client-production не затронут.

## 2026-07-29 — V0 Answer Writer V10: TEST-релиз и граница telemetry

- **Release:** `nmbot-v0-answer-writer-v10-test-20260729-0940`; атомарно изменены только `prompts/v0_answer_writer.txt` и `nmbot_v0/answer_writer.py`. Bridge, client-production, V2/V3, env, модели и CRM не менялись.
- **Live backup:** до изменения снят snapshot `vps-source-20260729-084608-2670e41a3b23`, 88 файлов, manifest SHA-256 `1f5a3280bf43674864714f2841bced6a14ccad846435199d7b6718c54a09a1c3`. Непосредственно перед успешной мутацией atomic release дополнительно снял snapshot `vps-source-20260729-090227-060b61fde29f`.
- **Artifact:** archive SHA-256 `a8daa51623c6f1b08bcac7ba36c4e1c0c64389d6371fab3566db6468fefdaf7e`; manifest SHA-256 `c3a7aa29cef15d23efcbc672858c922e0eb99fbcfcfc8ae4f0514f72b2ec0a52`.
- **Checks:** 209 selected V0/runtime/callback/replay/risk tests и 145 atomic-release tests прошли; integration review после исправлений — `pass` без critical/high findings.
- **Smoke:** свежие V0 lifecycle и search turns прошли с `error_summary.status=ok`; search вернул три карточки, один финальный вопрос и пустой список quality blockers. API health/identity зелёные, bridge отдельно проверен и не изменился.
- **Writer invocation:** свежая metadata-only telemetry содержит stage `conversation_answer_v0_writer_diagnostic` с моделью `google/gemini-3.6-flash`; live-конфигурация подтверждает `publish` mode и gateway provider.
- **Telemetry boundary:** это доказывает вызов Writer, но не доказывает, что его кандидат был опубликован. Runtime пишет итог `published|fallback` в `trace.answer_writer`, однако текущий dialogue journal сохраняет только bounded `runtime_summary` и отбрасывает `answer_writer`. Поэтому до добавления безопасных полей `status`, `reason`, `model` и prompt hash нельзя по журналу различить публикацию Writer и deterministic fallback.

## 2026-07-29 — V0 empty-search recovery: TEST-релиз

- **Release:** `nmbot-v0-empty-search-recovery-test-20260729-0952`; атомарно изменены только `nmbot_v0/runtime.py` и `scripts/nmbot_runtime_adapter.py`. Prompt, bridge, client-production, V2/V3, env, модели и CRM не менялись.
- **Live backup:** до подготовки снят snapshot `vps-source-20260729-092057-09f9f46c443f`, 88 файлов, manifest SHA-256 `13eba6c13a6a4a43a197c920915c8893671bd70978fd641ac9225c8021d0d056`. Перед фактической мутацией atomic release дополнительно снял `vps-source-20260729-095127-995c56e313ff`.
- **Artifact:** archive SHA-256 `4dbf17b97bee7fcf9cd5e4a6e508bcb5e5228e138bd3bd98cca9c7f4e74c4afc`; manifest SHA-256 `7228ef5e1fd78da48b9099246e19746ad83011174895593fa8898e2b656b086e`.
- **Behavior:** после валидного пустого первого V0 search выполняется не более одного bounded recovery search; сохраняются жёсткие параметры, ослабляются только разрешённые неподтверждённые семантические ограничения. При повторной пустоте формируется честный operator-phone путь без ложного shortlist CTA. Call budget, exact-first facts/near semantics, recovered field trace и counts покрыты тестами.
- **Checks:** runtime/adapter/callback suites — 252 passed; atomic-release suite — 145 passed; release preflight и integration review — без блокирующих findings.
- **Live smoke:** свежий `/start_0 → двушка для семьи` на этом release прошёл с `error_summary.status=ok`, `visible_options_count=3`, пустыми `quality_blockers` и одним финальным вопросом; bridge отдельно healthy и не менялся. Этот smoke получил непустой первый search, поэтому именно recovery-ветка live-smoke не доказана; она подтверждена локальными регрессиями.

## 2026-07-30 — V4 one-prompt runtime: локальная реализация

- **Слой:** отдельный runtime `nmbot_v4` с собственным namespace и командой `/start_4`; V0/V1/V2/V3 и глобальный selector не переключались.
- **Модель и prompt:** `google/gemini-3.6-flash`, `prompts/v4_flat_search.txt`, payload stage `nmbot_v4_one_prompt`, один вызов gateway-agent на пользовательский turn без model retry/fallback.
- **MCP/выход:** внутри model tool cycle доступен `novostroym/get_flat_info`; наружу допускается только строгий JSON `{data,message}`. Код проверяет числовые ID, дедупликацию, предел 20, русский `message` и fail-closed ветки.
- **Граница доказательств:** gateway не отдаёт валидатору сырой MCP transcript, поэтому code-level grounding IDs не заявляется. Реальные Gemini/OpenRouter/MCP, VPS и Jivo не проверялись.
- **Local proof:** focused suite — `92 passed`; обычное read-only review — `pass`, без critical/high/medium findings, report `ses_05037179bffeYGNFG7dDJfJ6b1`.
- **Статус:** локально принято; diagnostic provider request и любой deploy требуют отдельного разрешения и first-failure gate.

## Реестр версий промптов

| ID | Файл | Дата | Примечание |
|---|---|---|---|
| P001-search | chat_tester_bot.py:36-45 | 2026-06-24 | поиск с MCP, JSON `{facts, missing, params}` |
| P001-chat   | chat_tester_bot.py:47-54 | 2026-06-24 | «Ирина», 2-4 предложения, JSON `{response, params}` |
