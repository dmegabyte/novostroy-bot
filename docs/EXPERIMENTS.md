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

## Канонический реестр выводов гипотез

`docs/EXPERIMENTS.md` — долговременный, отслеживаемый Git-источник решений по
гипотезам. Логи, receipts, NotebookLM и MemPalace помогают искать evidence, но
не заменяют запись здесь. Нельзя закрыть гипотезу только строкой `pass`, числом
тестов или успешным health-check.

### Поля жизненного цикла

- **Stage** — где находится проверка:
  `hypothesis` → `confirmed_in_test` → `candidate_green` → `regression_green`
  → `release_ready` → `deployed_unverified` → `verified_live`.
- **Status** — вывод проверки: `open`, `accepted`, `rejected`, `partial`,
  `inconclusive`, `blocked` или `superseded`.
- **Evidence level** — максимальный доказанный контур:
  `local`, `TEST`, `full regression`, `live verified`.

`accepted` означает принятие только в пределах указанного `Evidence level`;
он не означает production без `live verified`. `blocked` означает, что
переход дальше запрещён. Если гипотеза уточняется, старая запись сохраняется,
а новая `H###` указывает `supersedes`/`superseded by`.

### Обязательная карточка H###

```md
### H### — <название>

- Opened: <UTC date/time>
- Stage: hypothesis | confirmed_in_test | candidate_green | regression_green | release_ready | deployed_unverified | verified_live
- Status: open | accepted | rejected | partial | inconclusive | blocked | superseded
- Evidence level: local | TEST | full regression | live verified
- Hypothesis: <одно проверяемое утверждение>
- Actual / Contract / Desired: <факт / контракт / желаемый результат>
- Owner-layer: <точный слой, файл и функция если известны>
- Baseline: <версия, snapshot, prompt/model IDs и одинаковый input/payload>
- Acceptance: <измеримый pass/fail критерий>
- RED evidence: <команда, fixture, первая ошибка или ссылка на trace>
- Change: <минимальное изменение и почему оно принадлежит owner-layer>
- GREEN evidence: <focused, negative, stateful/integration checks>
- Comparison/regression: <baseline vs candidate и полный regression>
- Result: <что фактически произошло>
- Conclusion: <главный доказанный вывод>
- Boundary: <чего этот эксперимент не доказал>
- Reusable rule: <что применять в следующих задачах>
- Do not repeat: <какой подход не повторять и почему>
- Remaining unknowns: <непроверенные риски>
- Next hypothesis: <H### или none>
- Supersedes / superseded by: <H### или none>
- Evidence: <точные файлы, строки, команды, task/release IDs>
- Closed: <UTC date/time или pending>
```

### Conclusion Gate

Перед следующей гипотезой, commit или release карточка должна быть обновлена
после последней проверки. Минимальный порядок:

1. записать RED и найти owner дефекта;
2. записать GREEN и границы сравнения;
3. записать полный regression, включая failures;
4. выбрать `accepted`, `rejected`, `partial`, `inconclusive` или `blocked`;
5. сформулировать `Conclusion`, `Boundary` и `Reusable rule` обычным языком;
6. сохранить конкретные evidence и следующую гипотезу.

`25 passed / 10 failed` должно остаться карточкой со статусом `blocked`, даже
если focused tests зелёные. Успешная новая гипотеза не переписывает старый
неуспешный результат: она добавляется отдельной записью с явной связью.

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
   - присваивает `H###` и заполняет обязательную карточку в
     `docs/EXPERIMENTS.md`,
   - при необходимости пишет вспомогательную строку в
     `logs/hypotheses.jsonl` с `h_id, opened_at, status=open`,
   - при изменении промпта — пишет `P###` в `logs/prompts.jsonl` со старым и новым текстом.
   - передаёт Prompt Quality Guardian полный handoff: цель и ожидаемый результат,
     `Actual / Contract / Desired`, ограничения, риски, слой и модель, полный
     текущий prompt, входной контекст и JSON/wire-контракт, normalizer/validator,
     релевантные тесты, live trace/диалог, прежние решения, их результаты и причины
     неудачных попыток. Нельзя отправлять только последнее наблюдение или одну
     ошибочную реплику.
4. После focused/negative/full checks и, для UX-гипотез, достаточного числа
   representative dialogues — закрывает Conclusion Gate в карточке: записывает
   `Result`, `Conclusion`, `Boundary`, `Reusable rule`, `Remaining unknowns` и
   конкретные `Evidence`. Одной строки `status=closed` в JSONL недостаточно.
5. Старые выводы не удаляет и не переписывает: уточнение оформляет новым
   `H###` с `supersedes`/`superseded by`.

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

### H056 — Isolated V6 logical-response hypothesis contour (2026-08-11, **local deterministic mechanics accepted; model evidence pending**)

- **Actual:** V6 `parse_prompt2()` rejects malformed output, internal metadata,
  phones, unsafe card indices and more than one question, but does not establish
  semantic relevance or factual claim grounding. The legacy `chat_v1` evaluator
  cannot prove this V6 Prompt2 behavior.
- **Contract:** the orchestrator owns scenario/route; payload owns available and
  missing facts; V6 runtime owns phone/operator state; Prompt2 only writes the
  response under its supplied route and `question_policy`. Each hypothesis must
  use the same payload for baseline/candidate, record a receipt and stop on the
  first unexplained failure or regression.
- **Desired:** a TEST-only queue that can distinguish payload identity,
  structural Prompt2 output, and code-owned phone/operator bypass without
  touching the primary pipeline or calling a provider by default.
- **Owner layer:** verification for replay identity; Prompt2 for phrasing-only
  cases; orchestrator/payload for route and assertability; runtime for phone and
  operator bypass.
- **Baseline / RED:** first focused run exposed two contour defects: CLI imports
  missed the project root, and the negative-output fixture failed a required
  phrase before its intended forbidden-claim check.
- **Minimal change:** added `scripts/nmbot_v6_logic_contour.py`, manifest
  `tests/fixtures/nmbot_v6_logic_contour.json`, and focused tests. The runner
  accepts only `payload_identity`, `prompt2_output`, and `runtime_owner` cases;
  it has no transport, provider, MCP, network or production-write path. It
  accepts a separately supplied raw-output artifact only for local parsing and
  writes no raw model response into receipts.
- **GREEN / comparison:** 7 focused tests passed. The manifest validates 5
  ordered hypotheses / 12 cases. C002 now includes a trusted-card-index replay.
  Deterministic C001 replay identity and C005 phone/operator ownership are
  source-executed. C002–C004 baseline text is a fixture contract, not
  model-quality proof; it proves only that a future raw artifact will be checked
  and stop at the first failed assertion.
- **Result:** local deterministic mechanics accepted; no model/eval/provider,
  MCP, SSH/VPS/Jivo or production call was made. Receipt safety reports zero for
  model/provider/MCP/network/production writes and excludes raw outputs/private
  phones.
- **Conclusion:** do not change Prompt2 or the main pipeline from these fixture
  results. The next evidence must be a bounded real Prompt2 TEST batch using the
  exact V6 transport and the same payload hashes; its first unexplained failure
  blocks any next behavioral hypothesis.
- **Boundary:** unknown/conflicting fields and operator consent cannot be
  assigned to Prompt2 by this contour alone. Phone extraction/handoff remain
  code-owned. A `FreeAPI` provider was not found in project source/docs or
  configured `.env` key names; do not invent an endpoint or bypass the V6
  transport contract.
- **Reusable rule:** a semantic prompt hypothesis needs two layers of evidence:
  deterministic invariants first, then independently captured model artifacts;
  structural JSON validity is not semantic relevance.
- **Do not repeat:** do not use fixture-written baseline prose as a claim that a
  real model improved. Do not store raw client/model text in durable receipts.
- **Unknowns:** a stable, human-labelled golden set is still needed; no current
  V6 raw Prompt2 output has been collected; live Jivo behavior remains unknown.
- **Next hypothesis:** C002 real-artifact relevance replay, only when a permitted
  V6 TEST transport execution mechanism is available.

### H057 — Prompt2 TEST candidate: policy fidelity and claim boundary (2026-08-11, **open; baseline RED captured**)

- **Opened / stage / evidence:** 2026-08-11; isolated V6 Prompt2 TEST batch;
  real TEST-webhook artifacts, deterministic receipt and hashes. No Jivo,
  production, MCP or Promptfoo/eval execution.
- **Hypothesis:** A minimal Prompt2-only instruction that treats
  `question_policy` as the authoritative next-question contract, copies the
  exact route-provided `search_result.response` where required, and refuses to
  turn absent or conflicting fields into prose claims will improve policy
  fidelity without changing route, state, phone handling or the JSON wire shape.
- **Actual:** The baseline batch sent 9 Prompt2 cases through the documented
  `/webhook/openrouter-direct-test` using model
  `google/gemini-3.1-flash-lite-preview` and the current V6 gateway. All 9
  returned usable JSON. The deterministic queue stopped at C002 /
  `current_question_relevance` because the returned question did not match the
  fixture policy question. Per-case inspection found the same exact-question
  miss in the three C002 cases and required-phrase misses in all six C003–C005
  cases. Structural JSON, trusted-index and forbidden-claim checks passed.
  One grounded-card answer additionally stated comfort class, walking distance
  and infrastructure although those values were not present in trusted facts;
  this is a semantic grounding risk not caught by the current parser.
- **Contract:** The orchestrator-selected route and code-owned state remain
  final. Prompt2 may only write `intro`, `cards`, and one `question` from its
  supplied `search_result`, `trusted_mcp` and `question_policy`; it must not
  select a route, extract a phone, infer operator consent, resolve conflicting
  facts or invent unavailable fields. Baseline and candidate must use identical
  dynamic user/state/plan/trusted-facts input; only the TEST prompt variant may
  differ.
- **Desired:** On the same 9 cases, retain strict JSON and all existing safety
  checks, follow the route-provided question policy, preserve exact clarification
  questions, and make no unsupported factual claims. A candidate is not accepted
  for merely changing wording or producing a plausible answer.
- **Owner layer:** Prompt2 instruction for presentation and claim refusal;
  parser/validator remains code-owned for structural safety; route/state/phone
  behavior remains outside this hypothesis.
- **Baseline input / payload:** The exact case plans and trusted-facts objects
  from `tests/fixtures/nmbot_v6_logic_contour.json`, with baseline payload hashes
  recorded in `/tmp/opencode/v6-prompt2-batch-20260811T153942Z-13251/`.
  Candidate rerun must report matching dynamic query/input hashes for all cases
  and a distinct prompt hash only if the TEST override is active.
- **Acceptance:** Candidate returns 9/9 responses with no transport failure;
  all deterministic structural/trusted-index/forbidden-claim checks pass;
  current-question and stale-context cases satisfy their policy assertions;
  no candidate output contains a claim forbidden by its fixture. Any change in
  route/state/operator/phone behavior, payload mismatch, missing receipt or
  unexplained transport failure is a regression and blocks the hypothesis.
- **Baseline / RED:** Current baseline fails the deterministic queue at C002
  `exact_question`; full per-case matrix also records C002 question misses and
  C003–C005 required-phrase misses. This is an explained baseline behavior
  failure, not a transport failure.
- **Minimal candidate change:** Create a TEST-only prompt override and runner
  support for selecting it; do not edit `prompts/v6_answer_writer.txt`,
  `nmbot_v6/gateway.py`, runtime/state or deployment artifacts. Keep raw model
  responses ephemeral and receipts hash-only.
- **Comparison:** The candidate batch returned 9/9 with no transport failure.
  Offline preflight and the report prove equal stage, model, parameters, dynamic
  input and query hashes for every case; only the prompt hash changed. The
  candidate deterministic matrix had exactly the same nine diagnostic failures
  as baseline: three `exact_question` misses and six `required_phrase_0`
  misses. It introduced no structural, trusted-index, forbidden-claim, route or
  state regression. In the grounded-card raw artifact, the candidate removed
  the baseline's unsupported class/distance/infrastructure additions and kept
  only the supplied object facts; this is a qualitative partial improvement,
  not a pass of the full hypothesis.
- **Result:** `partial` — transport and same-payload comparison passed; claim
  restraint improved in the observed grounded-card case; question-policy
  fidelity did not improve under the current payload contract. Candidate report:
  `/tmp/opencode/v6-prompt2-candidate-20260811T154512Z-13251/batch-report.json`.
  Candidate receipt:
  `/tmp/opencode/v6-prompt2-candidate-20260811T154512Z-13251/deterministic-contour-receipt.json`.
- **Conclusion:** Prompt-only wording is insufficient to make Prompt2 produce a
  fixture-specific exact next question when the payload supplies only
  `question_policy.question_goal`; the model selected plausible but different
  questions. The minimal grounding instruction is useful for claim restraint,
  but adding more phrasing rules would be prompt overfitting until a canonical
  question contract exists in the payload. Exact fixture questions and required
  phrases remain diagnostic labels unless the product contract confirms them.
- **Boundary:** This evidence concerns isolated Prompt2 presentation only. It
  does not prove live Jivo behavior, semantic grounding for every factual claim,
  or production readiness. The parser still cannot mechanically prove prose
  grounding. No main prompt/runtime/state/VPS/Jivo change was made.
- **Reusable rule:** A question goal is not a canonical question. If exact
  wording is required, the owner layer must supply an explicit bounded field;
  Prompt2 can copy it but should not infer it from a goal. Prompt-only claim
  restraint can reduce unsupported prose but cannot repair missing payload
  semantics.
- **Do not repeat:** Do not keep adding generic prompt wording to force an
  exact question absent from the dynamic payload. Do not call the candidate
  better from one qualitative output or aggregate fixture counts.
- **Unknowns:** Whether a TEST-only explicit `question_policy.next_question`
  field improves exact-question fidelity; whether those fixture questions are
  product-canonical; and whether a deterministic claim-grounding guard is
  required beyond the current parser.
- **Next hypothesis:** H058 — TEST-only bounded `next_question` payload field
  plus one prompt instruction to copy it exactly, using H057 candidate as the
  baseline and changing no route/state/phone behavior.

### H058 — TEST-only canonical next-question field (2026-08-11, **accepted narrow; H057 partial**)

- **Opened / stage / evidence:** 2026-08-11; isolated V6 Prompt2 TEST batch;
  H057's candidate is the baseline. This is a payload+prompt diagnostic only;
  no main pipeline, runtime, state, production, Jivo, MCP or Promptfoo/eval
  execution is allowed.
- **Hypothesis:** Supplying one explicit `question_policy.next_question` in
  the TEST payload and instructing Prompt2 to copy it exactly will remove the
  question-policy misses without changing route, state, cards, phone handling
  or the strict JSON wire shape.
- **Actual:** H057 showed that `question_policy.question_goal` alone does not
  determine the fixture's expected question. The current builder exposes goal,
  mode, card count and dialogue step but no canonical next-question text.
- **Contract:** The orchestrator/payload owner may provide a bounded next-step
  question; Prompt2 may present it but may not choose route/state/operator/phone
  behavior or create facts. This hypothesis uses H057's already-tested prompt
  claim boundary as its starting point and adds only an explicit TEST overlay
  field plus its consumption instruction.
- **Desired:** On the same 9 cases, preserve the H057 claim-restraint behavior,
  return the supplied exact question where present, retain parser/trusted-index
  guards, and show no route/state/operator/phone drift.
- **Owner layer:** payload contract plus Prompt2 presentation instruction;
  parser/validator remains code-owned and is not expanded here.
- **Baseline / input:** H057 candidate prompt and its 9 case payloads/outputs;
  candidate overlay may change only `question_policy.next_question` and the
  TEST prompt's explicit copy instruction. The report must show exactly which
  dynamic field changed and retain stage/model/parameters equality.
- **Acceptance:** 9/9 TEST responses, no transport failure; explicit
  next-question cases satisfy their supplied question; structural/trusted-index/
  forbidden checks pass; H057's grounded-card claim restraint is not regressed;
  any route/state/operator/phone drift, payload change outside the declared
  field, or unsupported claim blocks H058.
- **GREEN / comparison:** The bounded candidate batch returned 9/9 with no
  transport failure. The report proves `declared_field_overlay_ab` with only
  `question_policy.next_question` added: base dynamic input, stage, model and
  parameters remained equal; the prompt hash changed as declared. The full
  per-case matrix passes `exact_question`, structural Prompt2, trusted-index and
  forbidden checks for all 9 cases. The grounded-card output retained the H057
  claim restraint and did not repeat the observed unsupported class/distance/
  infrastructure additions.
- **Result:** `accepted (narrow)` — all 9 supplied next questions were copied
  exactly, with no route/state/operator/phone or transport regression. The
  contour still reports six `required_phrase_0` diagnostic misses in C003-C005;
  these are independent of H058 because those routes require empty `intro` and
  the phrases are not supplied by the payload contract.
- **Conclusion:** An explicit bounded `question_policy.next_question` field is
  sufficient for exact-question fidelity; a question goal alone is not. H058
  proves a payload-contract requirement and a minimal Prompt2 copy rule, not a
  production prompt change. H057 claim restraint was preserved in the observed
  grounded-card case, but prose grounding remains outside the parser contract.
- **Boundary:** This is isolated Prompt2 TEST evidence only. It does not prove
  live Jivo behavior, full semantic grounding, production readiness or that the
  nine fixture questions are product-canonical. Main prompt, gateway,
  runtime/state, VPS/Jivo and production were untouched; no MCP/eval/Promptfoo
  call was made.
- **Reusable rule:** If exact next-question wording matters, the owner layer
  must provide one bounded canonical string and Prompt2 must copy it; do not
  try to infer exact wording from an abstract goal or keep adding generic prompt
  prose.
- **Do not repeat:** Do not classify H058 as a full 9/9 dialogue-quality pass
  because the unrelated advisory phrase diagnostics remain. Do not turn those
  fixture phrases into a new static Prompt2 contract without product evidence.
- **Unknowns:** Whether an explicit field should become part of the real
  `build_question_policy` contract; which questions are product-canonical; and
  whether a deterministic claim-grounding guard is required beyond the current
  parser.
- **Next hypothesis:** H059 — separate blocking contract assertions from
  advisory style phrases so clarify/recover routes do not fail on intro text
  that their own contract forbids.

### H059 — TEST contour contract: blocking vs advisory phrases (2026-08-11, **accepted; H058 accepted narrow**)

- **Opened / stage / evidence:** 2026-08-11; isolated V6 Prompt2 TEST contour;
  H058 real artifact batch and receipt are the baseline. No model, MCP, eval,
  Promptfoo, production, VPS or Jivo execution is part of this hypothesis.
- **Hypothesis:** Required-intro phrases that are absent from the dynamic payload
  and conflict with the `clarify`/`recover_dialogue` empty-intro contract should
  be recorded as advisory diagnostics, not blocking failures. Separating them
  will make the contour's first-failure gate measure the actual route/output
  contract instead of an incompatible fixture prose preference.
- **Actual:** H058 copied all 9 canonical questions exactly and passed the
  structural/trusted/forbidden checks, but C003-C005 stopped on six phrase checks.
  H058's candidate prompt explicitly requires empty `intro` for those routes;
  the phrases only occur in fixture `baseline_output` and are not in the
  payload's `clarification_question` or `question_policy`.
- **Contract:** For `clarify` and `recover_dialogue`, the answer contract owns
  empty `intro`, empty `cards` and exact `clarification_question`; optional
  style observations must not override that contract. Blocking checks remain
  exact question, wire shape, trusted indices, forbidden phrases/claims and
  owner invariants.
- **Desired:** Preserve the six phrases as visible advisory results while
  allowing an otherwise contract-valid H058 artifact to complete the queue.
  No prompt/model/runtime/main-pipeline change is allowed.
- **Owner layer:** TEST contour/fixture contract; not Prompt2 behavior.
- **Acceptance:** Manifest validates; advisory phrase misses are emitted but do
  not set `first_failure`; the H058 raw artifact runs through all five
  hypotheses with no blocking failure; raw output remains absent from the
  receipt; focused tests stay green.
- **Minimal change:** Added optional `advisory_phrases` to Prompt2 fixture
  assertions and surfaced `advisory_checks` in receipts without including them
  in `_first_failure`. Moved the six noncanonical intro expectations out of
  blocking `required_phrases`; no Prompt2/main source was changed.
- **GREEN / comparison:** The existing H058 raw artifacts replayed through the
  updated contour across all 5 hypotheses / 12 cases with `exit_code=0` and no
  blocking failures. All six former phrase misses remain visible as advisory
  misses. Focused tests: 10 passed; compile and manifest validation passed. The
  immutable replay receipt is
  `/tmp/opencode/v6-prompt2-h058-20260811T160112Z-13251/v6-logic-receipt-d14591fae7217ab9362a.json`.
- **Result:** `accepted` for the TEST-gate correction. The contour now measures
  the actual route/output contract while preserving incompatible style
  expectations as diagnostics.
- **Conclusion:** A false blocking assertion can make a valid hypothesis look
  like a first failure. Required facts, exact route questions, wire shape,
  trusted indices and forbidden claims remain blocking; optional prose style
  remains advisory unless the product contract explicitly promotes it.
- **Boundary:** This fixes the test contour only. It does not make the six
  phrases product requirements, improve the model, prove semantic prose
  grounding or authorize a main-pipeline change. The H058 raw artifacts remain
  ephemeral; the receipt stores hashes and advisory booleans only.
- **Reusable rule:** Every assertion must declare whether it is contract,
  safety, or advisory style evidence. First-failure must stop only on contract
  or safety failures; advisory misses must be reported without advancing a
  false RED.
- **Do not repeat:** Do not use a baseline-output phrase as a blocking product
  requirement when the dynamic payload and route contract do not provide it.
- **Unknowns:** A real canonical `next_question` field is still only tested in
  the TEST overlay; semantic claim grounding remains unvalidated beyond the
  observed H057/H058 card artifact; fixture questions still need product-owner
  confirmation before any source change.
- **Next hypothesis:** H060 — TEST-only semantic claim-grounding guard for
  selected trusted-card fields, or stop if no source-backed guard can be made
  general without overfitting.

### H060 — TEST grounding guard feasibility (2026-08-11, **blocked/inconclusive**)

- **Opened / stage / evidence:** 2026-08-11; read-only analysis of the existing
  baseline, H057 and H058 Prompt2 artifacts. No new model/eval/MCP/Jivo request
  was made.
- **Question:** Can a general deterministic guard prove that every prose claim
  in a Prompt2 card is supported by the selected trusted facts, without a
  growing regex/denylist or a second semantic judge?
- **Evidence:** The original baseline grounded-card response contained observed
  unsupported markers for comfort class and infrastructure, while H057 and
  H058 did not repeat those markers. All three runs retained the trusted object
  name/metro signal. This is useful regression evidence, but marker presence is
  not a general claim proof.
- **Source-backed boundary:** Existing validation can prove typed card identity
  and exact field/value containment in trusted evidence, but Prompt2 returns
  free prose. The project rule explicitly rejects modeling natural dialogue as
  an expanding list of words, regexes and phrase exceptions. A deterministic
  prose checker would therefore either miss paraphrased claims or overreject
  valid language.
- **Result:** `blocked/inconclusive`. No safe general H060 guard was added. The
  observed H057/H058 improvement remains qualitative and case-local; it cannot
  authorize a production validator or prompt change.
- **Conclusion:** To make grounding mechanically testable, the owner layer must
  provide typed claims/field references (or a separately approved semantic
  adjudicator). Prompt2 prose plus current trusted-card indices is insufficient
  for proof. Do not continue with a denylist-based H060 variant.
- **Boundary:** Main prompt, gateway, runtime/state and production remain
  untouched. Raw outputs remain ephemeral; only safe boolean diagnostics were
  used.
- **Reusable rule:** Typed evidence can be validated deterministically; free
  prose requires separate semantic adjudication. A parser pass is not grounding
  proof.
- **Next hypothesis:** Stop behavioral Prompt2 iterations until product/owner
  contract defines typed claim references or explicitly accepts qualitative
  semantic review. No further automatic TEST hypothesis is justified from the
  current evidence.

### H061 — TEST-only Prompt2 model A/B (2026-08-11, **blocked: transport**)

- **Opened / stage / evidence:** 2026-08-11; isolated V6 Prompt2 TEST batch;
  model-only diagnostic. Baseline is the H058 candidate prompt and its 9-case
  payload set. Candidate model: `google/gemini-2.5-flash`, which is already
  documented in project chat/presenter and model-comparison sources; this is
  not a V6 production-model change.
- **Hypothesis:** Some observed logical-response failures may be model-specific.
  With identical Prompt2 prompt, dynamic input, stage, parameters and cases,
  changing only the model may change exact-question fidelity, claim restraint
  or structural validity.
- **Acceptance:** At most 9 requests per model; documented TEST webhook only;
  prompt/parameters/base dynamic hashes equal; model hash/value is the sole
  declared difference; no transport failure; compare all returned outputs with
  the same deterministic contour and advisory rules. No production inference
  is allowed.
- **Runner / preflight:** Added TEST-only model override and field-level
  comparison. Local focused suite after the change: 14 passed; model overlay
  preflight proved equal prompt, parameters, base dynamic input and query, with
  only the model changed.
- **Attempted batch:** Candidate run used 9 requested cases and the documented
  TEST webhook with `google/gemini-2.5-flash`. The first transport attempt
  failed with `ProbeError` before any response was returned; the safe report
  contains zero returned calls and no raw output. Report:
  `/tmp/opencode/v6-prompt2-h061-gemini25-20260811T-164521/batch-report.json`.
- **Retry diagnostic:** Added a TEST-runner-only allowlist for transport
  fallback metadata; it exposes only boolean `_upstream_error` and
  `_safe_fallback` flags, never secrets or upstream details. Local focused tests
  remained 14 passed. The retry again failed on the first case before any
  response, now with both flags true. Retry report:
  `/tmp/opencode/v6-prompt2-h061-retry-20260811T165352Z/batch-report.json`.
- **Result:** `blocked` — acceptance is not met because no candidate artifact
  exists and no model A/B comparison is possible. The repeated safe-fallback
  flags identify an upstream TEST transport failure, not evidence that Gemini
  2.5 is better or worse.
- **Conclusion:** The TEST contour can isolate a model change correctly, but the
  available TEST transport failed twice before returning a model response. H061
  is conclusively transport-blocked for this session; do not infer model quality
  or continue blind retries.
- **Boundary:** No production, Jivo, VPS, MCP, eval or main-pipeline call was
  made. Existing pre-dirty main files were not touched.
- **Next step:** Resume only after the exact TEST transport failure is explained
  and a fresh approved bounded run is possible. Until then, the model-root-cause
  question remains unknown.

### H062 — TEST-only V6 Prompt2 model smoke: Gemini 2.5 Flash Lite (2026-08-11, **blocked: transport**)

- **Opened / stage / evidence:** 2026-08-11; isolated one-case Prompt2 TEST
  smoke. Candidate `google/gemini-2.5-flash-lite` is explicitly named as the
  separate V6 Prompt2 model in `docs/NMBOT_V6_INDEPENDENT_RUNTIME_TZ.md:284-297`;
  this is source-backed availability, not proof of current transport health.
- **Hypothesis:** The previous `google/gemini-2.5-flash` fallback may be specific
  to that model route. The V6-documented Flash Lite candidate may return a
  response through the same TEST webhook.
- **Acceptance:** One request for the same H058 case/prompt and parameters;
  no safe upstream fallback; valid response artifact. If smoke succeeds, a
  bounded 9-case model A/B may be considered. If it fails, stop without model
  quality inference.
- **Boundary:** TEST-only; no prompt, gateway, runtime/state, VPS/Jivo,
  production, MCP, eval or Promptfoo changes.
- **Attempt / result:** One bounded smoke request was sent with the same case and
  H058 prompt. It stopped before any response with
  `_upstream_error=true` and `_safe_fallback=true`; `request_count=0`. Report:
  `/tmp/opencode/v6-prompt2-smoke-gemini25lite-20260811T170113Z/batch-report.json`.
  The candidate did not reach a model-quality comparison.
- **Conclusion:** H062 is transport-blocked. Together with H061, both
  `google/gemini-2.5-flash` and the V6-documented
  `google/gemini-2.5-flash-lite` fail at the TEST route while current Gemini 3.1
  succeeds. This narrows the issue to the candidate model/provider route, but
  does not prove either model's answer quality. Stop further blind model retries.

### H063 — TEST-only Prompt2 gateway smoke: Gemini 3.5 Flash (2026-08-11, **blocked: transport**)

- **Actual:** Current `google/gemini-3.1-flash-lite-preview` returns through the
  Prompt2 gateway TEST route; both tested Gemini 2.5 IDs return safe fallback
  before model output.
- **Contract:** Model smoke changes only the model ID and uses the same isolated
  Prompt2 case, H058 candidate prompt, parameters and gateway transport. Exact
  project-backed candidate ID is `google/gemini-3.5-flash`.
- **Desired / acceptance:** One request returns a usable Prompt2 artifact with
  no `_upstream_error`/`_safe_fallback`. Success proves route availability only;
  quality requires a later same-payload batch. Failure stops the hypothesis.
- **Boundary:** TEST-only; no main prompt/runtime/state, MCP, Jivo, VPS,
  production, deploy, eval or Promptfoo mutation.
- **Result:** The single bounded request stopped before model output with
  `_upstream_error=true`, `_safe_fallback=true`, and `request_count=0`.
  Report: `/tmp/opencode/v6-prompt2-smoke-gemini35-20260811T173206Z/batch-report.json`.
- **Owner diagnostic:** A correlated owner-first reproduction proved that the
  n8n TEST webhook itself returned HTTP `500`, status `submit_http_error`, body
  `{code: 0, message: "No item to return was found"}` and no `task_id`. The
  request therefore failed before Overmind task creation; this evidence does
  not establish an OpenRouter/model rejection.
- **Conclusion:** `google/gemini-3.5-flash` is not currently reachable through
  this Prompt2 TEST gateway route. This is transport/provider-route evidence,
  not an answer-quality result. Do not run the 9-case A/B until route ownership
  explains or fixes the rejection.

### H064 — Safe owner error in Prompt2 TEST report (2026-08-11, **accepted**)

- **Actual:** Prompt2 TEST reports retained only generic safe-fallback flags, so
  the n8n owner error required an extra correlated diagnostic reproduction.
- **Contract:** Ordinary reports may contain only bounded safe owner fields:
  task ID/status, HTTP status and structured error code/message. They must not
  contain request payload, model output, credentials or unrestricted response.
- **Desired:** Capture these allowlisted fields from the gateway forensic event
  in memory and include them in `transport_failure` on the first failed request.
- **Boundary:** TEST runner only; no transport, retry, prompt, runtime, n8n,
  Overmind, provider or production behavior change.
- **Result:** `_TransportMetadataProbe` now captures only bounded
  `task_id`/`task_status`, integer `http_status`, and structured scalar
  `code|error_code|error_message|message|status`. Request payload, unrestricted
  body, model output and credentials are excluded. The original forensic logger
  is restored after each request.
- **Verification:** Focused TEST suites: `16 passed`; `py_compile` and
  `git diff --check` passed. Synthetic coverage proves HTTP 500 + n8n message is
  retained while a private request field is absent.
- **Conclusion / reusable rule:** First-failure TEST reports must expose the
  bounded immediate-owner error on the first attempt. Restricted forensic logs
  are reserved for details not representable by this safe allowlist.

### H065 — Direct gateway-agent Prompt2 TEST transport (2026-08-11, **accepted**)

- **Actual:** H061-H063 used the n8n `/webhook/openrouter-direct-test` adapter.
  That is not the direct runtime gateway-agent contour and H063 failed in n8n
  before Overmind task creation.
- **Contract:** Direct contour submits to Overmind `/api/v1/tasks/api` with
  `agent_name=gateway-agent`, `endpoint=/process`, then polls task status/result.
  Prompt2 payload/model/parameters and all TEST safety limits remain unchanged.
- **Desired:** Add an explicit TEST runner transport mode that bypasses n8n and
  re-run one Gemini 3.5 smoke through direct gateway-agent. Report must state
  route and safe first-owner diagnostics.
- **Boundary:** TEST runner only; no main gateway/runtime/prompt/state, n8n,
  Overmind, provider, Jivo, VPS, production, deploy, eval or Promptfoo changes.
- **Result:** Direct one-case Gemini 3.5 smoke returned a model artifact through
  `/api/v1/tasks/api`; `request_count=1`, `transport_failure=null`. The remaining
  `exact_question` diagnostic miss is answer behavior because the smoke omitted
  the H058 question overlay, not transport failure. Report:
  `/tmp/opencode/v6-prompt2-direct-gateway-gemini35-20260811T174333Z/batch-report.json`.
- **Verification:** Direct proxy hides `_run_test_webhook_request_once`, invokes
  only `_run_gateway_request_once`; focused suites `17 passed`, `py_compile` and
  diff-check passed.
- **Conclusion:** Direct gateway-agent is the correct model-comparison contour.
  H061-H063 n8n transport failures do not describe candidate model availability
  on this direct route.

### H066 — Direct gateway-agent Prompt2 Gemini 3.1 vs 3.5 (2026-08-11, **partial: 3.5 cleaner but slower**)

- **Hypothesis:** On the same 9 H058 Prompt2 cases, prompt, explicit
  `next_question`, parameters and direct gateway-agent transport, Gemini 3.5 may
  improve or preserve logical answer quality relative to current Gemini 3.1.
- **Baseline / candidate:** `google/gemini-3.1-flash-lite-preview` versus
  `google/gemini-3.5-flash`; one 9-request batch per model.
- **Acceptance:** 9/9 returned per batch, no transport failure, fingerprints
  prove only model differs, blocking deterministic contracts do not regress,
  and semantic claim observations are reported separately.
- **Boundary:** Isolated TEST only; no main pipeline, prompt/runtime/state,
  n8n, Jivo, VPS, production, deploy, eval or Promptfoo mutation.
- **Result:** Both direct gateway-agent batches returned `9/9`, with no
  transport failure and no blocking deterministic failure. Comparison proves
  equal prompt, parameters, query and dynamic input for every case; only model
  differs. Reports:
  - baseline 3.1: `/tmp/opencode/v6-prompt2-direct-baseline31-20260811T174420Z/batch-report.json`;
  - candidate 3.5: `/tmp/opencode/v6-prompt2-direct-candidate35-20260811T174500Z/batch-report.json`.
- **Semantic comparison:** Eight cases are semantically equivalent. In the
  grounded-card case, Gemini 3.1 added unsupported `в пешей доступности`, while
  Gemini 3.5 stayed within supplied Moscow/metro facts. No candidate forbidden
  claim, route, question, phone/operator or structural regression was observed.
- **Latency observation:** The 9-case baseline batch took about 33 seconds;
  Gemini 3.5 took about 71 seconds on this run. This is a batch observation, not
  a stable latency benchmark.
- **Conclusion:** Gemini 3.5 is reachable and at least contract-equivalent on
  this small Prompt2 set, with one observed grounding improvement, but roughly
  twice the batch latency. The evidence is insufficient to replace the current
  model; expand the human-labelled logical-response set before any main-source
  decision.
- **Reusable rule:** Model comparisons must use direct gateway-agent for this
  architecture. Do not infer candidate availability from the n8n TEST adapter.

### H067 — TEST runner failure signaling and raw lifecycle (2026-08-11, **accepted**)

- **Actual:** The isolated runner could report `first_failure` while returning
  process exit code `0`; raw response files and `outputs.json` also remained in
  the selected work directory after a run.
- **Contract:** A blocking deterministic failure must be visible to automation;
  raw model responses are ephemeral and must not remain in the safe report
  directory. TEST-only behavior, direct gateway-agent selection and model
  comparison hashes remain unchanged.
- **Desired:** Return non-zero for transport or contour failure and remove raw
  response files after the batch, including the failure path, while retaining
  only hash-based receipts/reports.
- **Boundary:** TEST runner and focused tests only; no main pipeline, prompt,
  runtime/state, provider, Jivo, VPS, production, eval or Promptfoo changes.
- **Result:** The runner now returns exit code `2` for either transport failure
  or blocking deterministic contour failure. Raw response files are removed in
  the runner `finally` path, and the raw `outputs.json` artifact is no longer
  written; reports remain hash-only.
- **Verification:** Focused TEST suites: `19 passed`; both TEST scripts compile;
  `git diff --check` passed. Added coverage for failure exit signaling and raw
  directory cleanup.
- **Conclusion / reusable rule:** A TEST batch is successful only when transport
  and deterministic contour both pass. Raw outputs must not survive the batch;
  retain only safe reports and receipts.

### H068 — Historical real-dialogue golden set (2026-08-11, **accepted: fixture prepared**)

- **Actual:** The existing historical `data/response_eval/cases.jsonl` contains
  real development cases covering wrong context, missing data, unsupported
  regions, off-topic input, near matches and unsupported claims.
- **Desired:** Preserve ten redacted scenario cases as TEST-only labels for the
  V6 Prompt2 contour, without copying raw model outputs or secrets.
- **Result:** Added
  `tests/fixtures/nmbot_v6_real_dialogue_golden.json` with 10 source-linked
  cases, expected behavior, owner layer and forbidden behavior. The fixture is
  a labeled source pack, not yet a V6 runtime payload and not a model result.
- **Boundary:** Historical evidence only; no production status inference and
  no main pipeline/prompt/runtime change.
- **Next step:** Map these labels to privacy-safe V6 typed payload fixtures,
  then run at most 10 direct `gateway-agent` cases with Gemini 3.1.

### H069 — Correct historical golden projection for no-result cases (2026-08-11, **accepted: TEST batch supported**)

- **Actual:** The first 10-case direct `gateway-agent` batch returned 10/10
  responses with no transport failure, but stopped on `real_geo_empty_001` because
  the fixture required an explanatory intro while its `clarify` action contract
  requires an empty intro.
- **Contract:** A completed search with no exact result must remain a `search`
  presentation case so Prompt2 may state the boundary and ask one question.
  `clarify` cases must keep empty `intro`/`cards` and only copy the question.
- **Minimal TEST change:** Reclassify no-result cases `real_geo_empty_001` and
  `real_missing_constraints_010` as `search`; keep off-topic and region-switch
  cases as `clarify` without intro-required assertions.
- **Boundary:** TEST manifest/fixture only; no main prompt, gateway, runtime/state,
  production, Jivo, MCP, n8n, eval or Promptfoo changes.
- **Next:** Run one replacement 10-case direct Gemini 3.1 batch and record the
  deterministic result. The first batch report remains evidence of transport
  success and a fixture-contract mismatch, not a model-quality conclusion.
- **Result:** Reclassified the two no-result cases as `search` and kept
  clarify-only cases free of blocking intro phrases. Replacement batch returned
  `10/10`, with no transport failure and no blocking deterministic failure.
  Report: `/tmp/opencode/v6-prompt2-real-golden-rerun2-20260811T181248Z/batch-report.json`.
- **Conclusion:** The TEST contour supports all 10 historical projections for
  JSON contract, expected action, exact question, trusted indices and forbidden
  claims. Advisory wording is not a semantic proof; raw output was deleted by
  the runner, so human adjudication still requires a separately approved safe
  retention mechanism or a typed claim contract.

### H070 — Boundary between MCP facts and unrelated questions (2026-08-11, **open: fixture prepared**)

- **Actual:** Historical logs contain both property-specific follow-ups with
  explicit evidence (`Можно ли забронировать?`, `Точно есть двор без машин?`)
  and questions whose needed evidence is absent (`А ипотека по ним есть?`), plus
  an off-topic/general question (`Расскажи анекдот`) during a search context.
- **Desired:** Classify the question before deciding the answer source: trusted
  MCP facts, current dialogue context, a separately allowed harmless-general
  answer, or an honest boundary/route response.
- **Result:** Added
  `tests/fixtures/nmbot_v6_answer_boundary_golden.json` with 8 redacted,
  source-linked historical cases. It contains no raw model outputs and is not a
  production or semantic-quality claim.
- **Boundary:** TEST-only fixture and ownership hypothesis; no prompt, gateway,
  runtime/state, MCP, Jivo, VPS, production, eval or Promptfoo change.
- **Next:** Obtain PromptMaster design for the smallest typed relevance/scope
  contract, then run a bounded TEST batch. Do not let Prompt2 use model memory as
  evidence for property facts or call MCP/search itself.

### H071 — Typed answer basis for boundary decisions (2026-08-11, **partial: output contract still fails**)

- **Actual:** Prompt2 receives facts and policy, but no typed indication of
  whether the current question is answerable from property facts, dialogue
  context, general knowledge or neither. The existing parser cannot verify that
  distinction semantically.
- **Contract:** Property claims remain MCP/card-only. Route/state/search remain
  orchestrator-owned. General answers are not product-approved yet; until that
  decision, unrelated questions are `unsupported` and must receive an honest
  boundary response.
- **Minimal TEST artifact:** Added
  `tests/fixtures/nmbot_v6_answer_boundary_basis.json` with one enum field
  `answer_basis`: `property_facts | dialogue_context | general | unsupported`.
  The sunlight/windows example is explicitly marked `manual_synthetic`, because
  no matching historical dialogue was found.
- **Boundary:** TEST fixture only; no prompt, gateway, runtime/state, model,
  MCP, Jivo, VPS, production, eval or Promptfoo change.
- **Next:** Add the typed basis to a TEST payload projection and run a bounded
  RED→GREEN comparison. Do not enable `general` behavior until product policy
  explicitly approves it.
- **Result:** Added the TEST-only answer-basis overlay and ran two bounded direct
  batches of 10 Gemini 3.1 requests. Transport returned `10/10` both times.
  The first case passed; `real_unsupported_region_002` failed deterministically
  with the safe parse error `ContractError: Prompt 2 card shape is invalid`.
  Exact raw output was not retained. Report:
  `/tmp/opencode/v6-prompt2-h071-basis-rerun-20260811T184113Z/batch-report.json`.
- **Conclusion:** Typed `answer_basis` is not enough by itself: the model still
  produced an output that violated the existing `cards` JSON contract in a
  boundary case. This is a TEST finding, not a reason to change the main
  pipeline. Keep the typed boundary design as a candidate and investigate a
  separate output-contract/failure-repair hypothesis only after preserving raw
  output through an explicitly approved safe review path.

### H073 — Exact Prompt2 card object schema (2026-08-11, **accepted: TEST supported**)

- **Actual:** H071 had 10/10 transport responses but one response failed the
  existing parser with `ContractError: Prompt 2 card shape is invalid`.
- **Contract:** Every card must contain exactly two keys: `index` and `text`;
  `index` is a non-negative integer and `text` is a non-empty string.
- **Hypothesis:** The H071 prompt did not state the exact card key set. Adding
  that one explicit instruction in a TEST-only candidate may remove this
  structural failure without changing answer-basis, route, state or parser.
- **Acceptance:** Same 10 cases, direct `gateway-agent`, no transport failure,
  all outputs pass `prompt2_contract`, trusted indices, exact questions and
  forbidden checks. Any failure remains TEST evidence only.
- **Boundary:** TEST prompt fixture and `docs/EXPERIMENTS.md` only; no main
  prompt, gateway, runtime/state, production, Jivo, MCP, eval or Promptfoo.
- **Result:** The TEST-only candidate returned `10/10` responses through direct
  `gateway-agent`, with no transport failure. All blocking checks passed:
  Prompt2 JSON contract, exact card indices, exact questions and forbidden
  claims. Report:
  `/tmp/opencode/v6-prompt2-h073-card-schema-20260811T184942Z/batch-report.json`.
- **Conclusion:** Explicitly stating the existing exact card schema removed the
  observed structural failure in this 10-case TEST batch. This supports the
  TEST hypothesis only; it does not prove universal semantic grounding or
  justify changing the main prompt without a broader regression set.

### H074 — Semantic answer-boundary regression set (2026-08-11, **accepted: fixture prepared**)

- **Actual:** H073 proves only the JSON/card shape. It does not prove that the
  answer addresses the user question or that every property claim has evidence.
- **Desired:** Check the semantic boundary separately: property questions use
  MCP facts, dialogue questions use only curated dialogue context, unsupported
  questions do not receive invented answers, and route-owned actions are not
  silently performed by Prompt2.
- **Result:** Added
  `tests/fixtures/nmbot_v6_semantic_answer_cases.json` with 10 redacted cases,
  source links, expected `answer_basis`, allowed source classes, required
  evidence markers and forbidden claims. It includes the synthetic sunlight/
  windows case because no matching historical log was found.
- **Boundary:** TEST fixture and local assertions only; no main prompt, gateway,
  runtime/state, model, MCP, Jivo, VPS, production, eval or Promptfoo change.
- **Acceptance:** The fixture is structurally valid, contains no raw outputs, and
  each case has an explicit owner/source/boundary. Model execution is a later
  bounded hypothesis; passing JSON alone must not mark semantic grounding green.
- **Verification:** Fixture contains 10 cases, including the manual synthetic
  sunlight/windows case marked `unsupported`; raw outputs are absent. TEST/local
  suites pass `22`; `py_compile` and `git diff --check` pass.
- **Conclusion:** The semantic test set is ready. It separates property facts,
  dialogue context and unsupported questions. A future model run can now fail
  for a meaningful reason (wrong source, invented claim or wrong owner), not just
  because the JSON format is malformed.

### H072 — Единый файл результатов TEST-контура (2026-08-11, **accepted**)

- **Правило:** все выводы, результаты гипотез, ошибки, границы и следующие
  шаги TEST-контура записываются в этот файл: `docs/EXPERIMENTS.md`.
- **Не меняется:** основной pipeline, production, Jivo и runtime-код.
- **Технические отчёты:** `/tmp/opencode/...` используются только как
  первичные receipts запуска; итог всегда переносится сюда.
- **Безопасность:** raw-ответы моделей не переносятся в журнал; сохраняются
  только безопасные hashes, статусы, owner и выводы.

### H075 — Executable semantic boundary batch (2026-08-11, **accepted: blocking checks supported**)

- **Actual:** H074 has 10 typed semantic cases, but its assertion fixture is
  not directly consumable by the V6 Prompt2 runner.
- **Desired:** Project the same cases into V6 `plan`/`trusted_facts` payloads,
  preserve the typed `answer_basis`, and run all 10 through direct
  `gateway-agent` without changing route/runtime/main Prompt2.
- **Boundary:** TEST manifest, overlays and receipts only; no n8n, MCP, Jivo,
  production, eval or Promptfoo calls.
- **Acceptance:** 10/10 returned, no transport failure, existing JSON/card/
  index/question/forbidden checks pass. Semantic claims remain separately
  advisory until a typed claim verifier exists.
- **Result:** Direct `gateway-agent` batch returned `10/10` Gemini 3.1 responses,
  with no transport failure and no blocking deterministic failure. All three
  hypotheses and all 10 cases were supported by the existing JSON/card/index/
  exact-question/forbidden checks. Report:
  `/tmp/opencode/v6-prompt2-h075-semantic-20260811T190229Z/batch-report.json`.
- **Conclusion:** The typed boundary cases are executable and the current TEST
  prompt preserved the mechanical contract for property facts, missing mortgage
  evidence, route-owned requests, the synthetic sunlight case and near-match
  distinction. This is not proof of general semantic grounding: raw output is
  deleted and the natural-language evidence markers remain advisory.

### H076 — TEST raw-response retention policy (2026-08-11, **accepted: retention default**)

- **Actual:** The TEST runner previously deleted raw model responses after every
  batch, which prevented exact response review.
- **Contract:** TEST raw responses are retained in the caller-selected workdir
  by default with directory mode `0700` and file mode `0600`. Reports and this
  durable ledger contain only hashes, statuses and the relative raw directory;
  raw text is never copied into them.
- **Change:** Added explicit `--cleanup-raw` as the only deletion switch. Without
  it, raw responses remain available for review. Main pipeline and production
  logs are untouched.
- **Boundary:** TEST runner only; no prompt, gateway, runtime/state, Jivo, MCP,
  n8n, VPS, production, eval or Promptfoo change.
- **Result:** Re-ran the 10-case semantic batch with default retention through
  direct `gateway-agent` using Gemini 3.1. All `10/10` responses returned, with
  no transport failure and no blocking deterministic failure. Raw files remain
  in `/tmp/opencode/v6-prompt2-h076-retained-20260811T190804Z/raw/`; the report
  contains no raw text.
- **Conclusion:** Exact response review is now possible in TEST without changing
  the main pipeline. Deletion is opt-in only via `--cleanup-raw`.

### H077 — Sales wording for unconfirmed mortgage conditions (2026-08-11, **accepted: wording rule**)

- **Problem:** The technically honest phrase «Проверить ипотечные условия по
  конкретному ЖК?» sounded dry and did not present the object’s value.
- **Contract:** Mortgage, rate, down payment, bank approval and availability are
  client-facing facts only when confirmed by MCP. Do not say «я уточню» or make
  an asynchronous promise. The assistant may offer specialist handoff only when
  the current route/payload authorizes that action.
- **Reusable sales formula:** First show the confirmed value of the object, then
  name the missing mortgage confirmation, then offer one concrete next step.
- **Recommended wording:** «По самому ЖК вариант подходит, а условия семейной
  ипотеки нужно подтвердить отдельно. Подключить специалиста?»
- **Alternatives:**
  - «Сам ЖК подходит вам по условиям. По семейной ипотеке нужна отдельная
    проверка. Передать этот вариант специалисту?»
  - «Этот ЖК можно рассмотреть. Осталось проверить, подходит ли семейная
    ипотека. Подключить специалиста?»
- **Boundary:** Do not use «карточка», internal system terms, invented rates,
  banks, down payments or approval. If specialist handoff is unavailable, use a
  truthful bounded question instead of promising a check.
- **Evidence:** Project UX requires honest boundary → useful action and forbids
  «я уточню»/«потом сообщу»; HubSpot consultative-selling guidance supports
  concise relevant insight, conversational tone and a specific next question:
  `https://blog.hubspot.com/sales/consultative-selling`.
- **Change:** Documentation and NotebookLM note only; no main prompt, gateway,
  runtime/state, production or Jivo change.

### H078 — Historical dialogue review expansion (2026-08-11, **accepted: cases collected**)

- **Actual:** The historical development file
  `data/response_eval/cases.jsonl` contains additional boundary cases beyond
  the first semantic set: no exact result, missing price for near matches,
  vague location requests, long result lists, exact-vs-near separation,
  premature operator handoff and missing budget.
- **Result:** Added
  `tests/fixtures/nmbot_v6_dialogue_review_cases.json` with 10 redacted,
  source-linked TEST cases. Each records the user question, available data,
  observed problem, owner layer, desired behavior and forbidden claims.
  Raw historical responses are not copied.
- **Coverage:** cases 0013, 0015, 0019, 0020, 0021, 0022, 0024, 0025,
  0026 and 0027 from the historical development set.
- **Boundary:** Historical evidence only; this is not current production proof.
  Main prompt, gateway, runtime/state, Jivo, MCP, VPS, production, eval and
  Promptfoo remain untouched.
- **Next:** Project these labels into executable V6 payloads in batches of at
  most 10, keeping natural-language evidence checks advisory until a typed
  claim-to-source contract exists.

### H079 — TEST Prompt2 sales and relevance wording (2026-08-11, **open: review required**)

- **Actual:** H076/H075 boundary outputs are technically safe, but some are dry
  or too implicit: missing mortgage confirmation, missing window orientation,
  near apartments and selection rationale. Historical cases also show vague
  requests, long lists, no exact result and premature operator handoff.
- **Desired:** A TEST-only Prompt2 candidate should answer the current question,
  use only the allowed evidence basis, explain a missing fact in plain customer
  language, preserve the exact question/route contract and sound like a useful
  real-estate consultant rather than an internal checker.
- **Hypothesis:** Explicit instructions for relevance, evidence boundary,
  concise benefit-led presentation and one concrete next step improve semantic
  usefulness without inventing property, mortgage or availability facts.
- **Owner boundary:** Orchestrator owns route/search/filter/operator decisions;
  Prompt2 owns presentation, relevance to the supplied route, honest missing-data
  wording and sales tone; parser owns JSON/card/phone/internal guards.
- **Acceptance:** PromptMaster review first; then a TEST-only candidate prompt and
  comparable batch of at most 10 cases. Blocking contract checks must not regress;
  natural-language semantic claims remain separately labeled and advisory.
- **Boundary:** No edit to `prompts/v6_answer_writer.txt`, gateway, runtime/state,
  model, production, Jivo, VPS, MCP, eval or Promptfoo.

### H080 — TEST A/B baseline fingerprint preserves declared overlays (2026-08-11, **accepted: runner fix**)

- **Actual:** H079 A/B was blocked before any candidate model call because the
  runner replayed the baseline prompt without the same `question_policy` and
  `answer_basis` overlays used to create the baseline report.
- **Contract:** A prompt A/B must compare identical payload projections; every
  declared TEST overlay used by the baseline must be present in its offline
  fingerprint replay.
- **Minimal change:** TEST runner only: when a prompt candidate is compared,
  apply the selected question overlay to the baseline fingerprint replay. No
  main gateway, prompt, runtime or state change.
- **Boundary:** TEST infrastructure only; no production, Jivo, MCP, n8n, eval or
  Promptfoo change.
- **Result:** Baseline replay now retains the selected question overlay for
  prompt-only comparisons; local focused tests remained green.

### H081 — TEST prompt A/B with unchanged overlays (2026-08-11, **accepted: runner fix**)

- **Actual:** After H080, the runner still classified an unchanged question
  overlay as a dynamic candidate change during prompt-only A/B.
- **Contract:** If `--prompt-override` is used with the same question/answer-basis
  overlays, comparison must allow only the prompt hash to differ.
- **Minimal change:** TEST comparison classification only; do not alter payload
  construction, main prompt, gateway, runtime, state or production.
- **Result:** Prompt-only H079 comparison now treats overlays as unchanged and
  proves the only intended change is the prompt hash.

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
### H100 — isolated Prompt2 question-only refinement (2026-08-12, **deployed off; enable blocked by audit**)

- **Actual:** H096 is restored and search works. H099-r2 removed the contour
  coupling, but its candidate prompt still generates `intro`, `cards` and
  `question` together, so the experiment can still alter presentation and does
  not prove that only the next question changed.
- **Contract:** The first Prompt2 call remains the exact H096 baseline and owns
  `intro` plus trusted card selection/text. A second optional model call may
  return only `{question}`. Prompt1, MCP/search, route, state, transport profile,
  baseline answer and card indices remain unchanged.
- **Desired:** With `NMBOT_V6_PROMPT2_QUESTION_REFINEMENT=question_only`, validate the
  baseline Prompt2 response first, ask a question-only prompt for one improved
  question, validate it, and replace only the baseline `question`. On any
  question-call transport, JSON or validation error, return the untouched H096
  response. With the flag off, execute the exact H096 path with no extra call.
- **Owner layer:** static question wording only. Intro/cards, facts/near,
  Prompt1/MCP, route/action/target/search policy, state, phone/operator activation
  and contour remain baseline/code-owned.
- **RED / acceptance:** prove before implementation that current H099 can change
  intro/cards. GREEN requires deterministic tests for off-path call/payload
  identity, intro/cards byte-equivalent JSON values, question-only successful
  replacement, invalid/phone/internal/extra-question fallback, no MCP/tool
  evidence and unchanged Prompt1/trusted-MCP projections.
- **Release boundary:** no TEST deploy until focused and compatible H096 baseline
  gates are green. Any immutable release is deployed with the flag off first;
  live enablement requires separate confirmation and same-dialogue verification.
- **RED result:** the H099 full-output path accepted a valid replacement object
  containing different `intro`, `cards` and `question`; therefore it could not
  prove question-only ownership and was superseded without deployment.
- **GREEN result:** isolated source
  `/tmp/opencode/nmbot-h100-question-only-prepared`, commit
  `66f59c7058a4979f02aadb5518e86592f1f90d72`, keeps H099 full-prompt
  substitution disabled and adds an independently gated second call only after
  baseline validation. Focused H100 tests passed `17/17`; compatible V6 tests
  passed `35/35`; compile and diff checks passed. The guards prove no extra call
  when off, unchanged baseline Prompt2 payload, question-only replacement,
  baseline fallback on transport/JSON/shape/internal/phone/multiple-question
  failures, no MCP/tool evidence and baseline-owned state/operator semantics.
- **Dry-run receipt:** immutable build `v6-test-h100-66f59c7-20260812` succeeded
  without deployment. Archive SHA256
  `65cb3cccbe73b952a49bb1d4bcbc5589c11d9664cb5271dc92a6d8f098a30fc6`;
  manifest SHA256
  `f320b617997f3bbdd3f737c512a744bda9ebbd40e5441f1d030bd4eeef59ac6a`;
  fresh TEST source snapshot `vps-source-20260812-120848-24b9092580b0`,
  manifest SHA256
  `3ad5f20516903d94f2c05209a71bb800c704902024fd411cbf09cd9acc7706dc`.
  H096 remained active, healthy and release-matched after the dry-run.
- **Integration audit:** full review approved TEST deploy only with H100 absent/off.
  Enablement remains blocked until semantic compatibility with `question_goal`,
  a privacy-safe refiner attempt/status trace, a bounded question-length guard,
  and a guarded env helper/status/rollback path are implemented and tested.
- **H100-r2 acceptance before enablement:** the refined question must remain in
  the code-owned `question_goal` class and must fail closed for incompatible
  search/viewing/selection/layout/operator wording; operator/phone questions are
  locked to the baseline. The parser must enforce an explicit length bound.
  Runtime evidence must expose only bounded refiner fields (`called`, gateway,
  parse/validator and fallback reason) and include the extra call in the safe
  attempt count without storing prompt/output text. The TEST env route must
  allow only `off|question_only`, retain a backup, atomically replace `.env`,
  restart only the API, and verify health/release/readback with rollback on the
  first failure. Same-dialogue baseline and enabled checks must preserve
  Prompt1/MCP projections, cards, state and search results.
- **TEST deploy-off receipt:** immutable release
  `v6-test-h100-66f59c7-20260812` was deployed from commit
  `66f59c7058a4979f02aadb5518e86592f1f90d72` after privacy-safe readback
  reported `NMBOT_V6_PROMPT2_QUESTION_REFINEMENT` absent. Fresh deploy snapshot
  `vps-source-20260812-121523-f06a6a667b1a` had manifest SHA256
  `9380512efc798e6ee753896e5cdc4e83583b06e62adeb0ce39f9ecfab82a87d8`.
  Post-cutover status was V6 publish, API active/healthy, release identity matched,
  and two remote baseline smokes returned HTTP 200 with release gate accepted.
  H100 remained absent/off and H096 release was retained for atomic rollback.

### H101 — V6 central-location preservation (2026-08-12, **open: RED first**)

- **Actual:** live V6 Prompt1 loses `центр Москвы` / `ЦАО` before MCP: the
  observed safe MCP projection contains only `rooms=2, district=msk`, with
  `facts=[]` and `near=[]`. The isolated Prompt1 parser rejects a valid
  `params.location` field, while the existing search normalizer already maps
  `ЦАО` and `центр Москвы` to the central districts.
- **Contract:** preserve explicit location separately from the broad region:
  `district=msk`, `location=ЦАО`. Do not change H100, Prompt2, MCP transport or
  result presentation. Existing locations such as Люблино must remain unchanged.
- **Desired:** `центр Москвы`, `ЦАО` and obvious center wording reach MCP as a
  supported location constraint; follow-up turns retain `location` instead of
  collapsing back to `district=msk`.
- **Owner layer:** Prompt1 params schema, Prompt1 instructions and V6 trusted
  constraint overlay. The search normalizer remains the owner of expanding
  `ЦАО` into supported central districts.
- **RED / acceptance:** parser rejection of `params.location` and live trace
  loss of center are the RED evidence. GREEN requires deterministic acceptance
  of short `location`, canonical center instruction, state overlay retention,
  `ЦАО` normalization, unchanged Люблино behavior, compatible V6 tests and
  compile/diff checks. No TEST deploy until these checks pass.
- **GREEN result:** isolated source `/tmp/opencode/nmbot-h100-r2-audit-guards`
  accepts bounded `location` strings/lists, preserves `location` through the
  trusted V6 overlay, and instructs Prompt1 to emit `district=msk` separately
  from `location=ЦАО`. Focused H101 tests passed `10/10`; compatible V6
  constraint/exact-detail/H100 tests passed `57/57`; compile and diff checks
  passed. No TEST mutation or H100 enablement was performed for H101.

### H102 — V6 center normalization before MCP (2026-08-12, **open: RED first**)

- **Actual:** H101 schema and prompt are present in the deployed TEST artifact,
  but the live Prompt1 model still returns only `rooms=2, district=msk` for
  `двушка в центре Москвы`; H100 is not called and MCP receives no location.
- **Contract:** when the user explicitly says `центр Москвы`/`ЦАО` (including
  the existing bounded center aliases), code must add only `location=ЦАО`
  while preserving `district=msk`; the existing search normalizer expands it
  to supported central districts. Other locations and user/model constraints
  remain unchanged.
- **Desired:** the real V6 Prompt1→MCP path retains center location even when
  Prompt1 omits the optional location field.
- **Owner layer:** V6 runtime before MCP request, using existing
  `nmbot_v2.search_contract._is_cao_alias`; no Prompt2/H100/transport change.
- **RED / acceptance:** local same-payload path must show Prompt1 output with
  only `district=msk` becomes MCP constraints with `location=ЦАО` and the
  normalizer produces central districts; non-center locations remain unchanged.
  Then compatible tests, immutable deploy and live trace must prove the same
  constraint before any client-facing claim.

### H103 — Prompt1 center JSON contract only (2026-08-12, **open: RED first**)

- **Actual:** H101 prompt mentions `location=ЦАО`, but live Prompt1 still
  returned only `district=msk`; async transport exposes no code-owned MCP trace
  that can repair this after the model call.
- **Contract:** Prompt1 must return `params.location="ЦАО"` together with
  `params.district="msk"` for explicit center-Moscow requests. This hypothesis
  changes only static Prompt1 instructions; runtime, gateway, MCP, state, H100
  and transport remain untouched.
- **Desired:** a stricter JSON contract and minimal examples make the model
  preserve the human location separately from the regional district code.
- **Owner layer:** `prompts/v6_search_agent.txt` only.
- **RED / acceptance:** the same Prompt1 payload must produce both fields for
  center wording, preserve Люблино/Новую Москву/МО as non-center locations, and
  reject `district=msk` without `location=ЦАО` in the local contract fixture.
  If model behavior is not proven, do not deploy H103.

### H104 — code-owned center constraint in Prompt1 input (2026-08-12, **open: RED first**)

- **Actual:** H103 strengthened static Prompt1 instructions, but live V6 still
  emitted only `district=msk`; H103 was deployed and rolled back. H102 already
  carries `explicit_search_constraints` in JSON, but the model still omitted
  location, so JSON-only emphasis was insufficient.
- **Contract:** explicit center Moscow input carries code-owned
  `district=msk, location=ЦАО`; the model may preserve these values but must
  not omit or reinterpret them. Prompt2/H100/search/state/transport stay
  unchanged.
- **Desired:** real V6 Prompt1 output retains the code-owned center location
  without relying on general prose alone.
- **Owner layer:** Prompt1 input/prompt boundary only.
- **Acceptance:** same-payload test proves the constraint is present in the
  model-facing input; candidate model probe returns both fields twice;
  compatible tests pass; then immutable TEST deploy and exact center smoke.
  If live MCP still omits location, rollback immediately.

### H099-r2 — isolated Prompt2 next-question decision (2026-08-12, **superseded by H100 before deploy**)

- **Actual:** H099 first deployment coupled the Prompt2 experiment to the shared
  `NMBOT_CONTOUR_PROFILE=test` setting. Enabling that setting correlated with a
  search regression; H099 was disabled and TEST was atomically restored to
  `v6-test-h096-budget-criteria-20260812`.
- **Contract:** Prompt1, MCP/search, transport, state, contour profile and
  release mode remain unchanged. Prompt2 may present only confirmed
  `search_result`/`trusted_mcp` data and select only the next user question.
- **Desired:** H099-r2 uses only the independent
  `NMBOT_V6_PROMPT2_DECISION_MODE=next_question` opt-in. `off` is byte-equivalent
  to baseline Prompt2 payload construction; `api_production` remains unchanged.
- **Owner layer:** static Prompt2 presentation and question wording only. Route,
  action, target, search policy, MCP, state, phone/operator activation and
  contour are code-owned and untouched.
- **Acceptance:** same-payload RED → minimal owner-layer change → GREEN; verify
  exact Prompt1 projection and trusted MCP projection are identical between
  baseline and candidate, candidate remains fail-closed when off, and an
  `api_production + next_question` diagnostic payload does not alter search
  inputs. No promptfoo/eval or deploy flag enablement without explicit approval.
- **Boundary:** candidate is not production proof. H099-r2 must first be built
  on the verified H096 baseline, deployed with the flag off, and compared via
  TEST smoke before any enablement.

### H105 addendum — advisory location assistance (2026-08-12, **open: local GREEN first**)

- **Actual:** the bounded local location dictionary resolves a few known aliases,
  but currently gives Prompt1 no useful location text for unknown explicit places.
- **Contract:** resolved dictionary results remain code-owned hints and may be
  applied by the existing sync merge. Unknown explicit locations remain Prompt1's
  responsibility; they must not be copied by code into MCP params. Ambiguous
  geographies must not select one location automatically.
- **Desired:** pass a bounded, user-text-derived advisory `location_hint` for
  unknown explicit locations. Prompt1 may use it to produce its own bounded
  `params.location`; runtime must never overlay the hint or overwrite model params.
- **Owner layer:** local location payload plus Prompt1 instruction; no n8n,
  transport, MCP trace, Prompt2/H100 or active release changes.
- **Acceptance:** known center/Люблино/New Moscow/MO remain resolved; unknown
  Раменки/Сокол is visible only as an advisory hint; generic metro and conflicts
  produce no forced constraint; model `location` is preserved for unresolved
  hints; oversized or untrusted text is omitted; focused tests and local preview
  pass. This does not prove MCP execution or live search because n8n returns no
  authoritative MCP trace.

### H136 — Register V6-simple adapter as release-owned API source (2026-08-14, **deploy-tool allowlist only**)

- **Actual:** H134 immutable artifact preflight stopped before deploy because `scripts/nmbot_v6_simple_adapter.py` was omitted by the fixed `API_RUNTIME_SCRIPT_FILES` allowlist; importing `scripts.nmbot_runtime_adapter` then failed. VPS remained unchanged and H108 stayed active.
- **Contract:** Add exactly `scripts/nmbot_v6_simple_adapter.py` to the existing atomic API release allowlist and focused release test. Do not weaken exclusions, allow arbitrary scripts, change candidate behavior, service, env, release identity or deployment target.
- **Desired:** A full H134 artifact contains the imported simple adapter and passes existing atomic preflight. This infrastructure correction is verified separately before rebuilding/deploying the unchanged candidate.
- **Status:** opened before release-tool edit; no deploy performed.

### H135 — V6-simple final practical baseline rerun (2026-08-14, **isolated TEST diagnostic; no deploy/production run**)

- **Actual:** H134 locally freezes a shorter identity-first prompt pair after H133 reached practical 9/10 with only C07 blocked.
- **Contract:** Rerun the unchanged effective v2 ten-case batch with H134 prompts, unchanged code/runtime/gateway/phone/outbox, max 30 model transports / 10 P1. Practical GREEN requires C07 no wrong named-object answer and preservation of H133 C01/C02/C03/C04/C05/C06/C08/C09/C10. Minor style defects are non-blocking.
- **Boundary:** isolated temporary source only, authorization `conversation:m0586` and practical threshold `conversation:m0760`; no service/release/production mutation, holdout or eval.
- **Status:** prepared; no H135 external call yet.

### H134 — V6-simple concise identity-first prompt pair (2026-08-14, **isolated atomic prompt revision; no model/TEST/deploy run**)

- **Actual:** H133 meets the practical baseline in 9/10 cases, but C07 repeats the same hard named-object substitution despite long identity rules in both prompts. The payload exposes `params.name="Семейный"` and fact name `Семейный Дом «Олива»`; no parser/code guard is permitted by the owner boundary.
- **Contract:** Keep identity semantic ownership exclusively in P1/P2. Replace both prompts once with the shorter PromptMaster pair `ses_000b7ca17ffed1nnu6afgs000e`, placing the named-object invariant first and removing duplicated low-priority wording. Preserve schemas, payloads, runtime, models, one P1 attempt, P2 repair, broad/strict scope, max three/one question and operator C09/C10 behavior. No code validator/router/classifier.
- **Desired:** Close C07 without changing any non-prompt owner or regressing the practical 9/10 pass set.
- **Boundary:** isolated `/tmp/opencode/nmbot-v6-simple-H134/source`; zero external calls during revision. Rerun requires a separate card/budget.
- **Local result:** exact shorter P1/P2 replacements applied only to both H134 prompt files; 62 focused tests and compileall pass; every non-prompt file matches H132. Prompt hashes: P1 `61793bddf1aa24803ada04c772a655008f5bf7cdcbe314e6a4d4eab415d85837`, P2 `54c63ded8c384eb1cbc8425b8fe8c82f24bb9d1c79afbb3d55fea0c00c09cfc6`; tree SHA `b27549229c7445ef963bcb226017887bbacba5f7f66644c42f80507ae6336824`. Practical rerun remains required.

### H133 — V6-simple practical baseline rerun after H132 (2026-08-14, **isolated TEST diagnostic; no deploy/production run**)

- **Actual:** H132 is locally green but model behavior is unmeasured. The owner explicitly prioritizes a credible working bot over endless stylistic perfection: small wording defects are acceptable, while technical failure, wrong named object, invented facts, phone request without consent and broken operator conversion are blockers.
- **Contract:** Rerun the same effective ten-case v2 batch with H132 prompts and unchanged runtime/gateway/parser/state/phone/outbox. Maximum 30 model transports / 10 MCP-enabled P1; stop on hard failure. Practical pass requires no technical failures, no named-object substitution, no unsupported central factual claim, no phone request inside reply, C09/C10 request_phone, maximum three shown objects and one question. Conservative or slightly dry wording is non-blocking.
- **Boundary:** isolated temporary copy only under user authorization `conversation:m0586` and practical-threshold clarification `conversation:m0760`; no service/release/production mutation, holdout access or Promptfoo/eval.
- **Result:** completed 10/10 with 20 calls and no hard stop. Practical passes: C01/C02/C03/C04/C05/C06/C08/C09/C10; C04 no longer asks for a phone in `reply`, C06/C08 no longer promise transfer, and C09/C10 preserve exact `request_phone`. The only release-blocking semantic RED is C07: P1 returns `Семейный Дом «Олива»` as a fact for named `ЖК Семейный`, and P2 repeats the substitution. Evidence: `/tmp/opencode/nmbot-v6-simple-H133/evidence/remote-result/`. No service/release/production mutation.

### H132 — V6-simple operator consent and independent identity safeguard (2026-08-14, **isolated atomic prompt revision; no model/TEST/deploy run**)

- **Actual:** H131 completed 10/10 mechanically and improved C01/C02/C03/C05 while preserving C09/C10, but C04 asks for a phone inside `reply`, C06/C08 use premature connection wording, and C07 still substitutes a longer similar-name project for the named ЖК in both prompts.
- **Contract:** Apply exactly one full PromptMaster replacement event to P1 and P2 only. P1 enforces literal full determining-name identity; P2 independently repeats identity/scope checks. `reply` never asks for a phone or claims connect/transfer/call; it may make at most one optional specialist offer as the only question. Only a direct specialist request or related consent returns `request_phone` with empty model response. Preserve all H130 payload/schema/runtime/gateway/model/repair/state/phone/outbox/selector contracts.
- **Desired:** Close C04/C06/C07/C08 while preserving C01/C02/C03/C05/C09/C10 and all mechanical gates.
- **Owner/boundary:** static P1+P2 pair only, PromptMaster response `ses_000c8817cffezDF6mcmi7y1At5`; isolated candidate `/tmp/opencode/nmbot-v6-simple-H132/source`; zero model/MCP/network/eval/TEST/deploy/production calls during revision. A rerun requires a separate card and budget.
- **Acceptance:** exact full replacements and hashes frozen; every non-prompt file equals H130; focused tests/compile/static checks pass; no semantic/live/production claim before rerun.
- **Stop:** any partial/private patch, non-prompt diff, test failure, external call, holdout exposure or deployment/production mutation.
- **Local result:** exact full P1/P2 replacements applied only to the two H132 prompt files; 62 focused tests and compileall pass; every non-prompt file matches H130. Prompt hashes: P1 `885013129d27caca572cbff54fb702d89df0a913c84da14bd49dec6536c9e5b8`, P2 `fdfbf113128e829f340cf4f715b812da5a810e33ed6ea470e9feb63705491411`; candidate tree SHA `df226689d3d235ae7025ce4ea8c1bccaf5ac0cd0e86dd18f248d5500725af23e`. This proves local contract only; practical model quality requires rerun.

### H131 — V6-simple same-batch rerun after atomic H130 prompt revision (2026-08-14, **isolated TEST diagnostic; no deploy/production run**)

- **Actual:** H130 froze one coherent P1/P2 pair revision after H128 source-linked semantic REDs and H129 corrected the defective future C01 oracle. Local contract checks are green, but no model behavior has been measured against the revised pair.
- **Contract:** Run only the frozen ten-case development batch with H129 C01 v2 substituted for v1 C01, H130 prompt hashes, unchanged H130 runtime/gateway/parser/state/phone/outbox and a fresh max 30 model transports / 10 MCP-enabled P1 turns. Hard payload/privacy/source/transport/state/terminal/budget failure stops immediately; safe semantic outcomes are recorded for the whole authorized batch. No prompt/code/service/release/production mutation.
- **Desired:** Measure whether the coherent revision closes the H128 identity, scope, max-three, suitability and internal-wording clusters without regressing direct operator/related consent.
- **Boundary:** User authorization `conversation:m0586`; isolated temporary source only; separate dry-run must match C01 v2, remaining v1 cases and H130 prompt hashes before calls. TEST service files, release selector and production remain untouched. Holdout remains external to writer scope.
- **Acceptance:** one new batch receipt/ledger; no more than 30/10 calls; source-linked semantic review against v2 oracle; no claim of TEST or production GREEN from this diagnostic.
- **Stop:** hash mismatch, failed dry-run, any hard failure, external-budget overrun, holdout exposure, deployment or production mutation.
- **Result:** H131 completed all 10 cases with 20 calls (10 P1, 10 P2), no repair, hard stop or budget overrun. Source-linked review preserves C01/C02/C03/C05/C09/C10 as anti-regression passes and records semantic REDs: C04 names project-level options for an unconfirmed three-room/budget match and asks for a phone inside `reply` without consent; C07 substitutes `Семейный Дом «Олива»` for named `ЖК Семейный`; C06/C08 use premature live-transfer wording (`соединить/свяжем`) rather than an explicit optional specialist offer. This is diagnostic evidence only; no service/release/production mutation and no semantic GREEN claim. Evidence: `/tmp/opencode/nmbot-v6-simple-H131/evidence/06-promptmaster-pack.json` and `remote-result/`.

### H130 — V6-simple atomic P1/P2 identity and scope revision (2026-08-14, **isolated prompt revision; no model/MCP/TEST/deploy run**)

- **Actual:** H128 completed 10/10 mechanics but source-linked review found independent P1 and P2 semantic failures: exact-vs-alternative and named-object identity (C03/C05/C07), project-to-unit price/scope and max-three limits (C01/C04/C05), unsupported family suitability (C02), and internal third-step wording (C08). H129 corrected only the defective C01 future oracle; it does not reclassify H128.
- **Contract:** Apply exactly one coherent PromptMaster replacement event to both static prompt files in a new isolated candidate. Keep exact schemas/payloads, H108 gateway, one P1 research attempt, P2-only format repair, models, parser, state, phone/outbox, selector, no-INVITE_AGENT contract and call budget unchanged. Prompt 1 owns literal identity/exact-versus-alternative selection; Prompt 2 owns bounded grounded answer/scope/operator decision. No code, payload, tool, retry, model, TEST service, release or production change.
- **Desired:** Ensure similar names or locations do not replace a named object; literal project price does not become a requested apartment price/availability; broad search shows at most three project-level orientations; no unsupported family suitability or internal counter text; direct specialist/related consent stays `request_phone`.
- **Owner layer:** static P1+P2 prompt pair only, supported by PromptMaster response `ses_0010c799affewQKHu3cyBZCUdH` after H129 correction.
- **Baseline/provenance:** H128 candidate `/tmp/opencode/nmbot-v6-simple-H128/source`; H128 safe batch evidence/ledger/updated PromptMaster pack; H129 correction `/tmp/opencode/nmbot-v6-simple-H129/evidence/04-development-corpus-v2-correction.json`; PromptMaster full replacements are the only prompt input.
- **Authorization/boundary:** isolated candidate only; no model/provider/MCP/network/eval/TEST/production calls in H130. A same-batch rerun requires a new explicit run card/budget and must use the H129 effective C01 v2 oracle. Holdout remains untouched outside writer scope.
- **Acceptance:** exact full P1/P2 replacements are frozen with before/after hashes; no runtime/payload/parser/gateway/state/phone/outbox diff; focused tests/compile/static prompt-schema check pass; C09/C10 operator/consent anti-regressions remain local-green. No semantic, TEST or production GREEN is claimed before rerun.
- **Stop:** any change outside both prompt files/evidence, prompt replacement fragment/private exception, payload/schema drift, test failure, external call, holdout exposure or claim of semantic/live/production GREEN.
- **Local result:** exact full P1/P2 PromptMaster replacements were applied only in `/tmp/opencode/nmbot-v6-simple-H130/source/prompts/`. H130 preserves every non-prompt file byte-for-byte from H128; 62 focused V6-simple tests and compileall pass. Prompt hashes: P1 `1512bfe76c93f714eff0c9c5657d8fe02522879938d07ec86c05c7ee65c9de30`, P2 `6105b6e443fd73276af1f0d3f4de64bdcd33181dd191919bc4b5fdd61e8df5c8`; candidate tree SHA `9bd60c40bbcc7d077d2664a13626b2dbafca4908c70b683e2c0b4c9acde14443`. No external call. This is local prompt-contract verification only; semantic rerun, holdout, TEST and production remain unproven.

### H129 — V6-simple C01 oracle correction and corpus v2 freeze (2026-08-14, **fixture/oracle only; no model/MCP/TEST/deploy run**)

- **Actual:** H128 completed the frozen v1 batch mechanically, but C01's current message is a broad Moscow two-room search while its v1 fixture/oracle requires an answer about synthetic ЖК Сокол. The real H128 C01 returned different literal material, so the v1 oracle cannot truthfully judge prompt behavior.
- **Contract:** Preserve H122 corpus v1 and every H124-H128 receipt unchanged. Create a separately identified v2 corpus plus correction record: C01 evaluates only returned material, at most three shown facts, no claim that an entry/project price proves a two-room match, no metro or availability invention, and at most one useful question. No prompt, parser, runtime, gateway, model, state, phone/outbox, service or production change.
- **Desired:** Restore a source-linked, non-contradictory oracle before any PromptMaster prompt decision, without tuning an oracle to the observed client text.
- **Owner layer:** fixture/oracle only.
- **Baseline/provenance:** immutable v1 `/tmp/opencode/nmbot-v6-simple-H122/evidence/04-development-corpus.json`; H128 C01 safe same-attempt evidence `/tmp/opencode/nmbot-v6-simple-H128/evidence/private/sanitized-case-evidence/C01.json`; H128 failure/pass ledger.
- **Authorization/boundary:** offline H129 correction under owner authorization `conversation:m0586`; model/provider/MCP/network/eval/TEST/production calls equal zero. Holdout remains outside writer scope and is not created or read.
- **Acceptance:** correction records old/new case content, reason, source refs and both corpus hashes; v2 retains 10 cases and coverage; C01 oracle has no hard-coded project/location and does not convert params/project entry price into unit or availability evidence; JSON/privacy preflight passes.
- **Stop:** any prompt/code/model/corpus-v1 rewrite, private-data leak, invented expected fact, coverage regression, external call or claim of semantic/live/production GREEN.
- **Status:** correction started; no external call.

### H128 — V6-simple named query-context parameter reconciliation (2026-08-14, **isolated interface revision; no additional external call**)

- **Actual:** H127 froze the complete finite union known before its batch and C01-C04 reached Prompt 2. C05 then stopped because the working H108 gateway returned `params.name="Сокол"` for a named property context. The value is not property evidence and is not in H127's old-H108/V2 params lists, but the key `name` is already source-backed in canonical `COMMON_FACT_FIELDS` and the safe C05 receipt proves current gateway use.
- **Contract:** Add only literal `name` to `PARAM_FIELDS` as bounded non-factual query context. Preserve existing PII/internal-key, size/depth and JSON validation. Prompt 2 must not treat any `params`, including `name`, as proof that a returned fact is the named property. Do not add a router, named-object state, synthetic normalization, prompt/model/gateway/runtime/phone/outbox/corpus change or a retry.
- **Desired:** Let the exact H127 C05 material reach Prompt 2, which can honestly explain that several similarly named objects were returned and ask one clarifying question, instead of emitting a technical error.
- **Owner layer:** mechanical Prompt 1 material parser/interface only.
- **Baseline/provenance:** H127 candidate `/tmp/opencode/nmbot-v6-simple-H127/source`; H127 C05 safe receipt `/tmp/opencode/nmbot-v6-simple-H127/evidence/private/sanitized-case-evidence/C05.json`; canonical `nmbot_v2/search_contract.py:65-81` includes `name` in `COMMON_FACT_FIELDS`; named-object query source uses an explicit entity reference in `nmbot_v2/search_contract.py:1029-1042`.
- **Authorization/boundary:** isolated candidate `/tmp/opencode/nmbot-v6-simple-H128/source`; user authorization `conversation:m0586`. This revision consumes zero external calls. A separate batch, if locally green, retains its own 30 model/10 MCP-enabled Prompt 1 maximum.
- **Acceptance:** C05 parses and reaches exact Prompt 2 handoff; `params.name` remains under `property_material.params` and not `facts`; unknown/PII/internal/oversized params still fail; prompts, gateway, runtime, state, phone/outbox and corpus remain unchanged; focused tests stay green.
- **Stop:** any key outside stated provenance, factual use of params, semantic normalization, privacy regression, unexpected candidate diff, test failure or external call.
- **Local result:** bounded named-context correction is green in the isolated candidate: 62 focused V6-simple tests and compileall passed; the saved H127 C05 material now parses and reaches the exact Prompt 2 handoff, where `params.name` remains separate from returned facts. Prompts, gateway, runtime, adapter and phone/outbox paths are byte-identical to H127. A separate frozen H128 batch has its own 30 model/10 MCP-enabled Prompt 1 maximum and receipt; no H128 external call has occurred yet.
- **Live batch result:** batch completed with 10/10 mechanical P1→P2 turns, 20 calls (10 P1, 10 P2), no hard stop, no retry/deploy/production mutation. Semantic source-linked review is RED/unknown: P1 loses exact-vs-alternative/named-object identity in C03/C07; P2 exceeds three options and overstates project price scope in C01/C05, overstates family suitability in C02 and exposes an internal third-step phrase in C08; C04 is mixed no-result handling; C06 repeat-exclusion is unproven because the frozen history lacks shown names. Operator C09 and related-consent C10 pass exact request_phone/fixed-question gates. Receipt: `/tmp/opencode/nmbot-v6-simple-H128/evidence/04-red-batch-report.json`; full ledger/PromptMaster pack are stored beside it.

### H127 — V6-simple complete source-backed H108 parameter vocabulary (2026-08-14, **isolated interface revision; no additional external call**)

- **Actual:** H125 and H126 proved the literal facts path and stopped only because real H108 `params` used `only_with_flats` then `location_name` outside the hand-curated simple list. Continuing one key per model result would be an invalid patch loop.
- **Contract:** Freeze one finite mechanical `PARAM_FIELDS` union from: (1) old immutable H108 `_PARAM_FIELDS` (`nmbot_v6/prompt1_contract.py:28-42`), (2) V2 response schema `params.propertyNames` (`schemas/v2_search_mcp_response.schema.json:13`), and (3) observed returned H108 keys `only_with_flats` and `location_name` in H125/H126 receipts. Existing safe key, value size/depth and PII/internal-key checks still apply. `params` stay non-factual request context under Prompt 2.
- **Desired:** Accept the full known working H108 parameter vocabulary in one bounded contract, while unknown parameter keys still fail closed and no scenario/semantic normalizer is added.
- **Owner layer:** mechanical Prompt 1 material parser/interface only.
- **Baseline/provenance:** H126 candidate `/tmp/opencode/nmbot-v6-simple-H126/source`; H125/H126 reports and receipts; immutable H108 `nmbot_v6/prompt1_contract.py:28-42`; local V2 response schema `schemas/v2_search_mcp_response.schema.json:13`.
- **Authorization/boundary:** isolated candidate `/tmp/opencode/nmbot-v6-simple-H127/source`; user authorization `conversation:m0586`. This revision uses zero external calls. Any H127 diagnostic batch uses a separate 30 model/10 MCP-enabled Prompt 1 maximum and receipt.
- **Acceptance:** every key in the frozen union passes mechanical params validation; unknown/PII/internal/oversized keys still fail; C01-C03 returned material parses and reaches P2; no prompts/models/gateway/runtime/state/phone/outbox/corpus diff; focused suite remains green.
- **Stop:** any key outside stated sources, factual use of params, semantic normalization, privacy regression, unexpected candidate diff, test failure or external call.
- **Local result:** finite vocabulary correction is green in the isolated candidate: 61 focused V6-simple tests and compileall passed; saved real H124 C01, H125 C02 and H126 C03 material all parse and reach the exact Prompt 2 handoff. Prompts, gateway, runtime, adapter and phone/outbox paths are byte-identical to H126. A separate frozen H127 batch has its own 30 model/10 MCP-enabled Prompt 1 maximum and receipt; no H127 external call has occurred yet.
- **Live result:** **HARD STOP** after C01-C04 completed P1→P2 and C05 used one Prompt 1 call. Total 9 calls (5 P1, 4 P2), no retry/deploy/production mutation. C05 parser failure is `invalid_param_key` for literal gateway `params.name="Сокол"`. This is a bounded named-context interface issue; H128 must prove its treatment before another batch. Receipt: `/tmp/opencode/nmbot-v6-simple-H127/evidence/04-red-batch-report.json`.

### H126 — V6-simple returned H108 parameter allowlist correction (2026-08-14, **isolated interface revision; no additional external call**)

- **Actual:** H125 made C01 complete through Prompt 1 → Prompt 2, then stopped at C02. The real H108 Prompt 1 material had allowed literal fact fields but returned `params.only_with_flats=true`; H121's bounded `PARAM_FIELDS` omitted that source-backed search parameter and rejected the envelope as `invalid_param_key`.
- **Contract:** Add only `only_with_flats` to `PARAM_FIELDS`. It remains an input/search parameter passed to Prompt 2 only as non-factual request context; it does not prove any project matches or availability. Keep all fact allowlists, PII/internal-key denial, prompts, models, gateway, runtime, state, phone/outbox and corpus unchanged.
- **Desired:** Preserve the working H108 literal result envelope including its observed search parameter, while retaining fail-closed behavior for all unknown params and without creating a semantic normalizer.
- **Owner layer:** mechanical Prompt 1 material parser/interface only.
- **Baseline/provenance:** H125 candidate `/tmp/opencode/nmbot-v6-simple-H125/source`; H125 C02 receipt `/tmp/opencode/nmbot-v6-simple-H125/evidence/private/sanitized-case-evidence/C02.json`; H125 report `/tmp/opencode/nmbot-v6-simple-H125/evidence/04-red-batch-report.json`.
- **Authorization/boundary:** isolated candidate `/tmp/opencode/nmbot-v6-simple-H126/source`; user authorization `conversation:m0586`. This revision consumes no further external call. Any separate H126 frozen batch retains its own 30 model/10 MCP-enabled Prompt 1 maximum and receipt.
- **Acceptance:** real H125 C02 material parses and reaches the exact Prompt 2 handoff; P2 prompt still treats params as non-factual context; unknown/PII/internal/oversized params remain rejected; focused tests remain green; no prompt/runtime/gateway/phone/outbox diff.
- **Stop:** any additional param invention, factual use of params, semantic normalization, privacy regression, prompt/runtime/gateway change, unexplained local failure or external call.
- **Status:** isolated correction started; no H126 external call.
- **Live result:** **HARD STOP** after C01 and C02 passed P1→P2 and C03 used one Prompt 1 call. Total 5 calls (3 P1, 2 P2), no retry/deploy/production mutation. C03 parser failure is `invalid_param_key` for literal H108 `location_name`; a complete source-backed parameter vocabulary is required before another batch. Receipt: `/tmp/opencode/nmbot-v6-simple-H126/evidence/04-red-batch-report.json`.

### H125 — V6-simple returned H108 field allowlist correction (2026-08-14, **isolated interface revision; no additional external call**)

- **Actual:** H124 stopped on its first authorized Prompt 1 call. The unchanged H108 gateway returned valid literal material, but H121 rejected the source-backed `location_name` field because its mechanical `FACT_FIELDS` union did not include that real H108 result key. `id` was already allowed; no Prompt 2 call, retry or semantic review ran.
- **Contract:** Add only `location_name` to the candidate's bounded literal material allowlist, with existing JSON depth/size and PII/internal-key rejection unchanged. Do not normalize values, add route/state semantics, change prompts/models/gateway/runtime, or relax any denylist. The field is an observed H108 result field and has a local source mapping in `nmbot_v2/semantic_planner.py` (`location_name` → `location`).
- **Desired:** Accept the same literal H108 material that the working gateway already returns, then let the unchanged Prompt 2 grounding policy use it; retain fail-closed behavior for all other unknown keys.
- **Owner layer:** mechanical Prompt 1 material parser/interface only.
- **Baseline/provenance:** H121 candidate `/tmp/opencode/nmbot-v6-simple-H121/source`; H124 C01 safe receipt `/tmp/opencode/nmbot-v6-simple-H124/evidence/private/sanitized-case-evidence/C01.json`; H124 report `/tmp/opencode/nmbot-v6-simple-H124/evidence/04-red-batch-report.json`; source mapping `nmbot_v2/semantic_planner.py`.
- **Authorization/boundary:** isolated candidate `/tmp/opencode/nmbot-v6-simple-H125/source`; user authorization `conversation:m0586`. This revision consumes no further model/MCP/network/eval/TEST/production calls. H124 is closed as a hard stop and will not be rerun from the old baseline.
- **Acceptance:** literal C01 H108 output parses and reaches the exact Prompt 2 material handoff; unknown/PII/internal/oversized keys remain rejected; focused V6-simple and parser tests remain green; prompts, gateway, runtime and operator/phone/outbox flow remain byte-identical to H121.
- **Stop:** any additional field invention, semantic normalization, prompt/runtime/gateway change, privacy regression, unexplained local failure or external call.
- **Local result:** allowlist correction is green in the isolated candidate: 59 focused V6-simple tests and compileall passed; the saved real H124 C01 gateway result now parses and reaches the exact Prompt 2 handoff. Prompts, models, gateway, runtime and phone/outbox paths are unchanged. A separate frozen H125 batch may use the existing owner authorization `conversation:m0586` and the same maximum 30 model/10 MCP-enabled Prompt 1 turns; it is not a continuation of H124 and must retain its own receipt/ledger.
- **Live result:** **HARD STOP** after H125 C01 passed P1→P2 and C02 used one Prompt 1 call. Total 3 calls (2 P1, 1 P2), no retry/deploy/production mutation. C02 parser failure is `invalid_param_key` for literal H108 `only_with_flats`; all returned fact fields were already allowed. Receipt: `/tmp/opencode/nmbot-v6-simple-H125/evidence/04-red-batch-report.json`.

### H124 — V6-simple frozen 10-case live development RED batch (2026-08-14, **authorized TEST-only diagnostic; no deploy/production mutation**)

- **Actual:** H121 is locally verified and H122 has ten source-backed integrated cases, but no model-quality evidence exists. The user explicitly ordered work to start after the exact proposed budget of up to 30 model calls and 10 MCP-enabled Prompt 1 turns; TEST and production have not been changed.
- **Contract:** Run the unchanged H121 prompt pair and runtime against exactly the frozen H122 case content. Maximum 10 Prompt 1 calls, 20 Prompt 2 calls including at most one P2 format repair per case, 30 total model transports and 10 MCP-enabled Prompt 1 turns. Hard privacy, source/hash, model/route, payload/schema, transport, state or terminal-integrity failure stops further calls immediately. Safe semantic RED is recorded across the already authorized fixed batch without patching prompts or fixtures.
- **Desired:** Produce one privacy-safe failure/pass ledger that attributes each failure to Prompt 1, Prompt 2, interface/payload, non-prompt, fixture/oracle, mixed or unknown, while preserving all passes as anti-regression contracts for a later PromptMaster decision.
- **Owner layer:** diagnostic batch only. Prompt, parser, runtime, model IDs, transport, corpus cases, TEST services and production are immutable during H124.
- **Baseline/provenance:** H121 isolated candidate `/tmp/opencode/nmbot-v6-simple-H121/source`, tree manifest SHA `933782f7fef197ddd7aa5c17a5ec5b365bd108f2cc882a724f0a3c25e1f2c8ca`; H122 corpus `/tmp/opencode/nmbot-v6-simple-H122/evidence/04-development-corpus.json`; frozen current `cases` canonical SHA `fa6e8842d80a071c5662991cea675ec3b7542630540f369afdb6000651e1f44e`; H121 prompt hashes P1 `8714263b7844ad7a15360227276461f33f5c14abdcc570b4f1d013bfb3205014`, P2 `2b7e6f0a0254caec84b6251fa167a262b582d80ab35cf2807f007ad2002678b4`.
- **Authorization/boundary:** owner confirmation `conversation:m0586`, following the explicit budget proposed in `conversation:m0579`; TEST-only diagnostic model/MCP/network calls are authorized within the stated limits. Promptfoo/eval, source mutation, service restart, release switch, TEST deploy and every production mutation remain forbidden in H124.
- **Acceptance:** offline dry-run validates hashes, exact case IDs, privacy and budgets before network; each live case records bounded sanitized per-stage input/output references, model calls, optional exact tool observation, parser/state/terminal status and oracle acceptance IDs; aggregate report preserves every failure and pass; no raw secrets, phone values or real customer data are stored.
- **Stop:** first hard failure, any call-budget overrun, prompt/corpus/model/source drift, private data, unexpected fallback/extra model, ambiguous transport, state corruption, fixture mutation or attempt to patch from one output.
- **Result:** **HARD STOP** after C01: 1 Prompt 1 call, 0 Prompt 2 calls, 1/30 total budget used. The gateway returned literal H108 material, but the H121 parser rejected source-backed `location_name` as `invalid_fact_field`. This is an interface allowlist failure, not a prompt-quality result. No retry, deploy, service mutation or production action occurred. Receipt: `/tmp/opencode/nmbot-v6-simple-H124/evidence/04-red-batch-report.json`.

### H123 — Reconcile V6-simple TZ with working H108 material contract (2026-08-14, **docs-only reconciliation; no model/MCP/TEST/deploy run**)

- **Actual:** H122 oracle preparation is structurally green for ten integrated cases, but model execution is blocked by a contradiction between the active TZ and the proven H108 result path. The TZ still requires normalized `cards`, correlated MCP trace for non-empty results and a possible P1 format-repair call; H121 uses the literal H108 result `facts / near / missing / params`, has no source-backed exact trace producer and intentionally makes one P1 gateway attempt.
- **Contract:** For the isolated H121/H122 candidate lineage, the existing H108 gateway result is the only reusable research material boundary. Prompt 1 returns a bounded literal `{facts,near,missing,params}` envelope; Prompt 2 receives separate `current_message`, `dialogue_history`, `property_material={facts,near,params}` and `missing`. Optional exact transport trace is observation-only; `mcp=unknown` is honest and does not reject returned material. Prompt 1 performs at most one research attempt and has no research retry; Prompt 2 may use the single format-repair budget. No synthetic `scope/requested/actual/provenance`, scenario/router/follow-up/refiner/fallback or fourth semantic owner is introduced.
- **Desired:** Make the documented candidate contract match the working contour without restoring H108's old semantic graph, so the fixed ten-case batch can be compared against the actual H121 payload and prompt pair rather than an impossible schema.
- **Owner layer:** documentation contract/interface reconciliation only. No runtime, prompt, model, transport or production mutation is authorized by H123.
- **Baseline/provenance:** H121 isolated candidate `/tmp/opencode/nmbot-v6-simple-H121/source`; H122 corpus `/tmp/opencode/nmbot-v6-simple-H122/evidence/04-development-corpus.json`, SHA `006e603bd9cc82b9f09548e557ffa28c92fc4ce61810bea78f333d474b02b2fb`; immutable H108 source manifest SHA `33f5eddae133908196859beed3f2f109fc9e45a4a99a720a625cf99360cac066`.
- **Authorization/boundary:** docs-only H123 amendment under owner confirmation `conversation:m0452`; model/provider/MCP/network/eval/TEST/production calls equal zero. H121 prompt hashes and H122 corpus remain frozen. Holdout remains `not_created` outside writer scope.
- **Acceptance:** The active TZ names one authoritative candidate material envelope, does not require unavailable trace metadata for business material, separates operational material from MCP provenance, aligns P1/P2 payload names, and marks H122 eligible for a later separately authorized model batch only after holdout isolation and exact call budget.
- **Stop:** Any code/prompt/model change, call, holdout exposure, silent historical rewrite, or claim of semantic/live/production GREEN.
- **Status:** reconciliation recorded; implementation/model execution remains blocked by missing exact model-call authorization and untouched holdout.

### H121 — V6-simple literal H108 material handoff (2026-08-14, **isolated revision; no model/MCP/TEST/deploy run**)

- **Actual:** H120 removed the mistaken `v6_tool_trace` gate, but its parser and fixtures still required synthetic `{field, scope, value}` items. The working H108 result is literal `facts / near / missing / params`: facts are named property objects, near objects carry literal `is_near`, `why_close` and `differences`, and nested values such as `ads` may be present.
- **Contract:** Preserve the bounded literal H108 material without deriving `scope`, `requested`, `actual`, provenance or semantic claims. Prompt 2 receives exactly `current_message`, `dialogue_history`, `property_material={facts,near,params}`, and `missing`. Optional exact transport trace is observation-only; absent trace is `mcp=unknown` and never blocks material. Reject PII/internal keys, oversized values, old scenario/card envelopes and malformed JSON. Keep exactly the simple semantic owners and H118 operator/phone/outbox mechanics.
- **Desired:** The simple candidate accepts the same useful material shape as the working bot and sends it intact to Prompt 2, without restoring H108 action/target/search-policy/follow-up/refiner/fallback layers or inventing a new transport.
- **Owner layer:** mixed Prompt 1 contract, Prompt 1→Prompt 2 payload and static prompt pair. Phone guard, state, outbox, selector and terminal mechanics are preserved.
- **Baseline/provenance:** H120 candidate `/tmp/opencode/nmbot-v6-simple-H120/source`; immutable H108 release `v6-test-h108-direct-gateway-cdf3cb1-20260812`; source manifest SHA `33f5eddae133908196859beed3f2f109fc9e45a4a99a720a625cf99360cac066`.
- **Authorization/boundary:** H121 candidate `/tmp/opencode/nmbot-v6-simple-H121/source`; offline-only revision, model/provider/MCP/network/eval/TEST/production calls equal zero. PromptMaster advisory is recorded in the H121 evidence pack; no live quality or production claim is allowed.
- **Acceptance:** Real H108-shaped fixtures with named facts, nested ads, near differences and missing string/category/object forms pass and reach P2 literally; diagnostics are dropped; synthetic field/scope/value cards and old scenario roots remain rejected; empty material reaches P2; malformed/PII/oversized material fails closed; operator/phone/outbox/selector focused regressions remain green; simple path contains no legacy semantic graph.
- **Stop:** Any external call, prompt/model/schema change outside this revision, field invention, privacy regression, old scenario layer, unexplained focused-test failure or source-integrity drift.
- **Status:** **local verified**; 58 focused tests passed, compileall/static/prompt-contract checks passed, real H108-shaped literal handoff/privacy check passed, and the full P1/P2 replacement is frozen in `08-revision-receipt.json`. This proves only the isolated offline contract/mechanics; model quality, TEST behavior and production readiness remain unproven. H120 RED was fixture/contract incompatibility, not evidence that the H108 gateway lacks usable material.

### H122 — V6-simple frozen development corpus and oracle preflight (2026-08-14, **offline preparation; no model/MCP/TEST/deploy run**)

- **Actual:** H121 has a locally verified literal H108 material contract and a coherent full P1/P2 prompt replacement, but no model-quality evidence exists. The TZ requires a fixed integrated 10-case development batch before any prompt quality conclusion.
- **Contract:** Freeze exactly 10 redacted integrated P1→P2 cases, with source-backed oracle expectations, acceptance IDs, coverage tags, bounded H108-shaped material fixtures and no client secrets. Do not create or inspect the independent holdout in this writer-owned workspace.
- **Desired:** A deterministic corpus/oracle manifest that can be run later only with a separately authorized exact model/MCP call budget; no one-case prompt patching and no claim of GREEN from fixture validation alone.
- **Owner layer:** `fixture/oracle` preparation only. Prompt, runtime, transport, model and production owners are unchanged.
- **Baseline/provenance:** H121 candidate `/tmp/opencode/nmbot-v6-simple-H121/source`; H108 source manifest SHA `33f5eddae133908196859beed3f2f109fc9e45a4a99a720a625cf99360cac066`; H121 prompt hashes are frozen in `/tmp/opencode/nmbot-v6-simple-H121/evidence/08-revision-receipt.json`.
- **Authorization/boundary:** Offline-only H122 card and evidence root `/tmp/opencode/nmbot-v6-simple-H122/evidence/`; model/provider/MCP/network/eval/TEST/production calls equal zero. Holdout is `not_created` and must be supplied later by the owner/custodian outside this root.
- **Acceptance:** Exactly 10 cases, at least 5 with history and at least 3 multi-turn; aggregate coverage includes first search, one/multiple ЖК, exact/near/no-result, named follow-up, changed conditions/more, missing fact, third substantive answer, direct specialist request, related/unrelated agreement and off-topic continuity. Oracle assertions are split into contract/safety/semantic/advisory and contradictions block the case.
- **Stop:** Any oracle contradiction, leaked/private data, unspecified expected behavior, model/network/eval call, holdout exposure or attempt to alter prompts/code from H122.
- **Status:** **prepared after H123 reconciliation, blocked before model calls**; 10/10 fixtures parse against H121, coverage is 8 history/4 multi-turn, privacy/JSON preflight is green, corpus hash is `006e603bd9cc82b9f09548e557ffa28c92fc4ce61810bea78f333d474b02b2fb`. The exact model-call budget and isolated owner/custodian holdout are still absent. No model-quality, TEST or production claim.

### H120 — V6-simple reuse of working H108 material path (2026-08-14, **isolated revision; no model/MCP/TEST/deploy run**)

- **Actual:** The H118 simple candidate rejects non-empty Prompt 1 results unless the gateway supplies a new `v6_tool_trace`. The verified H108 contour does not expose that field: it returns the usable Prompt 1 JSON material `facts / near / missing / params`, and the existing H108 runtime projects that material before passing it to Prompt 2. The new candidate therefore blocked ordinary search by imposing a contract absent from the working contour.
- **Contract:** Reuse the existing H108 gateway request/result path. Prompt 1 may produce only bounded material from the returned H108 JSON; Prompt 2 receives that material plus bounded dialogue history. Do not add a transport, do not restore H108 scenario/follow-up/refiner/fallback layers, and do not claim that gateway task metadata proves an authoritative MCP call count or raw backend trace. Empty/invalid material remains an honest Prompt 2 input or a technical safe failure according to the simple contract.
- **Desired:** Make the simple candidate use the same operational material path as the working bot while preserving exactly three semantic owners: phone guard, Prompt 1, Prompt 2. Ordinary factual search must no longer be blocked solely by absent `v6_tool_trace`; operator/phone/outbox guarantees remain unchanged.
- **Owner layer:** mixed Prompt 1/result projection and Prompt 1→Prompt 2 interface. No new transport, classifier, scenario layer, model fallback, semantic runtime validator or production change.
- **Baseline/provenance:** H118 candidate `/tmp/opencode/nmbot-v6-simple-H118/source`, tree manifest SHA `2accd3c275194d3a75bdffba8c6dc7f39a8e909ece30b5e4b4faaeb1d1607413`; immutable H108 release `v6-test-h108-direct-gateway-cdf3cb1-20260812`; source manifest SHA `33f5eddae133908196859beed3f2f109fc9e45a4a99a720a625cf99360cac066`.
- **Authorization/boundary:** H120 candidate `/tmp/opencode/nmbot-v6-simple-H120/source`; owner confirmation `conversation:m4064`; model/provider/MCP/network/eval/TEST/production call budget is zero for this revision. PromptMaster advisory is required before changing model-facing prompts or payload semantics. H108, canonical runtime and production remain untouched.
- **Acceptance:** Existing H108-shaped `facts / near / missing / params` fixtures reach the new P2 input without a `v6_tool_trace`; cards/material remain bounded and privacy-safe; no model-derived field is presented as authoritative transport telemetry; focused operator/phone/outbox tests remain green; no legacy scenario graph re-enters the V6-simple call path.
- **Stop:** missing source-backed H108 result shape, accidental prompt/model change, third semantic owner, phone/privacy regression, or any external call. A semantic quality claim requires a later frozen development batch and separate model-call authorization under the TZ cycle.
- **Status:** revision started; no live or production claim.

### H119 — V6-simple MCP trace producer evidence blocker (2026-08-14, **offline evidence only; no code/model/MCP/TEST/deploy run**)

- **Actual:** H118 proves the simple candidate accepts only an exact transport-owned `v6_tool_trace`, but the immutable H108 gateway result path returns only response text, gateway task identity and generic result metadata. No local source or saved artifact proves a producer of typed MCP trace/material; the historical artifact explicitly records that the former TEST path used model projection.
- **Contract:** Never treat gateway task/status metadata, diagnostics, prompt text or model output as MCP provenance. Do not invent a tool, arguments, result schema or per-field types. Keep non-empty factual cards fail-closed until a backend/Overmind trace contract or real sanitized request/response fixture is available.
- **Desired:** Establish whether a source-backed transport seam exists without external calls. If it does not, record the blocker precisely and preserve the verified operator → phone → durable outbox path instead of creating a false GREEN search path.
- **Owner layer:** `non-prompt` evidence/transport boundary only. No prompt, model, schema, semantic state, phone or legacy-runtime change is allowed in H119.
- **Baseline/provenance:** H118 candidate tree manifest SHA `2accd3c275194d3a75bdffba8c6dc7f39a8e909ece30b5e4b4faaeb1d1607413`; immutable H108 source manifest SHA `33f5eddae133908196859beed3f2f109fc9e45a4a99a720a625cf99360cac066`; H118 receipt `/tmp/opencode/nmbot-v6-simple-H118/evidence/08-revision-receipt.json`.
- **Authorization/boundary:** Offline-only H119 card `/tmp/opencode/nmbot-v6-simple-H119/evidence/00-run-card.json`; model/MCP/network/eval/TEST/production calls equal zero. No semantic corpus or holdout is created. A real producer contract or sanitized fixture requires a new exact owner authorization before code or live work.
- **Acceptance:** `_run_gateway_request_once` and saved H108 manifest are inspected; authoritative local MCP documentation is reconciled; generic/model-derived metadata is rejected; H118 operator/phone/outbox mechanical GREEN remains reusable; no runtime mutation is made to unblock search by assumption.
- **Result:** **BLOCKED** for ordinary factual search: no source-backed transport-owned producer or typed material contract was found. H118 operator/phone/outbox mechanics remain the only verified candidate capability. H119 changed no application source, prompts, schemas or models; no external call occurred. Receipt: `/tmp/opencode/nmbot-v6-simple-H119/evidence/08-revision-receipt.json`.

### H118 — V6-simple mechanical graph/trace repair (2026-08-14, **isolated revision; no model/MCP/TEST/deploy run**)

- **Actual:** The isolated H117 candidate has a new linear V6-simple dispatcher, but the adapter file still contains a dead old `_run_v6_authoritative` graph and old consent/state imports. The simple direct gateway also has no verified producer of the transport-owned `v6_tool_trace`; accepting model output or generic gateway metadata would violate the simple contract.
- **Contract:** Remove only the unreachable old V6 semantic graph while preserving V0/V1/V2/V3/V4/V5 dispatch and namespaces. Keep ordinary factual cards fail-closed until a real transport-owned `novostroym/get_flat_info` trace/material producer is proven. Do not fabricate provenance from model output, prompt text, generic task metadata or diagnostics. No prompt/model/schema semantic revision, fallback, MCP/network call, TEST or production mutation.
- **Desired:** Candidate import/call graph contains the simple V6 path without dead legacy V6 semantic owners; mechanical tests prove operator/phone/outbox behavior remains isolated and the ordinary-search blocker is explicit rather than falsely GREEN.
- **Owner layer:** `non-prompt` mechanical adapter/import graph and transport boundary only. Prompt1, Prompt2, phone semantics, legacy runtime implementations and production are out of scope.
- **Baseline/provenance:** H117 candidate tree manifest SHA `2f6ef0448f3485f1687ba1362935132ad29d1820974479f969944942fbe56ca5`; immutable H108 release `v6-test-h108-direct-gateway-cdf3cb1-20260812`; source snapshot manifest SHA `33f5eddae133908196859beed3f2f109fc9e45a4a99a720a625cf99360cac066`.
- **Authorization/boundary:** Isolated candidate only at `/tmp/opencode/nmbot-v6-simple-H117/source`; exact H118 revision card is under `/tmp/opencode/nmbot-v6-simple-H118/`; model/MCP/eval/network/TEST/production call budget is zero. No prompt replacement or semantic RED/GREEN claim is authorized by this entry. A real MCP producer and any model/TEST proof require a new exact owner authorization.
- **Acceptance:** Candidate V6 call graph excludes `_run_v6_authoritative`, `_is_phone_consent`, old V6 state/runtime/fallback/refiner imports and calls; V0/V1/V2/V3/V4/V5 selector tests remain green; transport trace tests accept only a transport-owned typed trace and reject model/generic metadata; operator direct/mixed/consent/phone/outbox/replay tests remain green; ordinary non-empty factual search remains explicitly `blocked` until producer evidence exists.
- **Result:** H118 mechanical acceptance is independently GREEN in the isolated candidate: 51 focused tests passed; compileall passed; AST checks show the dead old V6 graph is absent and V0–V6 dispatch remains reachable; a separate adapter test passed operator request → phone capture → durable queued outbox → duplicate replay with no phone in safe context/trace. The gateway source still has no verified producer of `v6_tool_trace` or typed transport-owned property material, so ordinary factual search remains fail-closed and H118 promotion is blocked. No model/MCP/network/eval/TEST/production call occurred. Receipt: `/tmp/opencode/nmbot-v6-simple-H118/evidence/08-revision-receipt.json`.

### H117 — V6-simple operator-first candidate from immutable H108 (2026-08-13, **offline intake; no model/TEST/deploy run**)

- **Actual:** The H108-derived V6 path contains multiple scenario/follow-up/state owners and model fallback branches. Recent TEST dialogues returned HTTP success but failed in later Prompt1/Prompt2 turns, so ordinary answers were not reliable and the operator path was not a universal blocking guarantee.
- **Contract:** Build a separate candidate with exactly `phone guard → Prompt 1 → Prompt 2 → state/publish`. Prompt1 returns bounded grounded `{cards,missing}` from the existing MCP boundary; Prompt2 receives separate current message/history/cards/missing and returns only `{action: reply|request_phone, response}`. Code owns only phone privacy, mechanical validation, state, durable callback outbox and fixed phone texts. Direct operator request, valid semantic consent, valid phone, replay and outbox failure are blocking acceptance paths. No scenario router, classifier, third semantic model, provider fallback, hidden legacy path or production mutation.
- **Desired:** A candidate that gives useful grounded ordinary replies when data exists, states limits honestly when it does not, and reliably reaches phone capture for every supported operator entry path without inventing facts or duplicating callbacks.
- **Owner layer:** Initial intake is `unknown|mixed`; the intended semantic owners are only Prompt1, Prompt2 and the pre-model phone guard. Mechanical state/outbox/transport work is not allowed to become a fourth semantic owner.
- **Baseline/provenance:** Immutable H108 release `v6-test-h108-direct-gateway-cdf3cb1-20260812`, baseline commit `cdf3cb1ab0f082d42820cb2b9da80f56df2f0f23`, source snapshot manifest SHA `33f5eddae133908196859beed3f2f109fc9e45a4a99a720a625cf99360cac066`, archive SHA `cce2f22069908884e565622e7621baa8937b936d470c4f0a473170eb1dad40a9`. H108 is a source/transport baseline only; its old semantic scenario/fallback behavior is not inherited.
- **Offline gate:** Start card `H117` is recorded under `/tmp/opencode/nmbot-v6-simple-H117/` with `authorized_action=offline_only`, maximum model/MCP/eval/TEST/production calls equal to zero. No development batch or holdout exists yet. This entry does not claim model quality, live TEST behavior, production readiness or deployment.
- **Next boundary:** Before any model/MCP/TEST call, freeze the source-backed corpus, holdout and exact budget under the TZ §13 process; any prompt revision requires the exact PromptMaster pack/response contract and one atomic revision event.

### H115 — modular operator-flow baseline from stable H108 (2026-08-13, **local GREEN; deploy not run**)

- **Actual:** Operator-contact helpers are split between V6 state, runtime and adapter, so a small behavioral change repeatedly creates divergent producer/consumer contracts and long review cycles.
- **Contract:** Refactor only. Extract pure bounded operator-flow helpers into one module while preserving the exact H108 state projection, consent vocabularies and runtime/adapter behavior. Do not change prompts, models, state schema, gateway, search, MCP, transport, n8n, deployment or client-visible behavior.
- **Desired:** A stable modular baseline where future operator hypotheses change one owner module and its consumers, while each release remains one full immutable artifact.
- **Owner layer:** `nmbot_v6/operator_flow.py` with minimal imports from `nmbot_v6/state.py`, `nmbot_v6/runtime.py` and `scripts/nmbot_runtime_adapter.py`.
- **GREEN:** Isolated candidate `/tmp/opencode/nmbot-h115-modular`, final commit `709747ef1aa00d4eab16d49975401fd5ca0bf616`; exact H108 helper and real runtime/adapter consumer parity focused **9 passed**, compatible suite **134 passed**, compileall/diff-check GREEN, tracked worktree clean. Final integration review PASS, low risk, no findings.
- **Boundary:** H115 intentionally preserves known H108 operator semantics, including its distinct short-consent sets and current offer-state projection. It does not claim to fix offer → consent → phone behavior. No TEST deploy or live smoke performed.

### H114 — clean operator offer/consent state contract (2026-08-13, **local GREEN; deploy not run**)

- **Actual:** The active H108 baseline has no isolated operator-consent transition. Prior H112/H113 work mixed offer and phone-waiting semantics and accumulated unrelated lineage.
- **Contract:** `operator_offer` is persisted with `pending_phone=false`; only a current `operator_contact` offer plus a short code-owned consent transitions atomically to `pending_phone=true`, clears the pending interaction, increments revision and asks for the phone. Unrelated `pending_phone` state goes through ordinary runtime. Direct phone remains private and code-owned.
- **Desired:** A clean H108-based candidate with only the state/adapter contract, no Prompt1/MCP/Prompt2/search/transport/n8n changes.
- **Owner layer:** `nmbot_v6/state.py`, `scripts/nmbot_runtime_adapter.py`, focused operator test.
- **RED/GREEN:** Initial implementation RED: consent branch passed `awaiting_phone` to a baseline public helper that did not accept it. Fixed locally; clean candidate gate is **129 passed**, compileall and diff-check GREEN.
- **Provenance:** Candidate `/tmp/opencode/nmbot-operator-clean`, base H108 `cdf3cb1ab0f082d42820cb2b9da80f56df2f0f23`, baseline release `v6-test-h108-direct-gateway-cdf3cb1-20260812`, snapshot manifest SHA `33f5eddae133908196859beed3f2f109fc9e45a4a99a720a625cf99360cac066`.
- **Boundary/unknowns:** No TEST deploy or live operator smoke yet; callback enqueue and real phone flow require a separate API-only verification. This candidate does not repair MCP/search.

### H112 — operator consent survives safe MCP fallback (2026-08-13, **open: RED first**)

- **Actual:** H110 correctly stops Prompt2 when required MCP returns an accepted empty envelope and publishes a safe question offering an operator, but the adapter leaves the old state unchanged. A following `да` therefore has no durable operator-consent context and can re-enter the model path.
- **Contract:** If the published safe fallback asks `Передать оператору запрос?`, the next short consent (`да`, `ага`, `хорошо`, `давайте`) must deterministically request a phone number without Prompt1/P​​rompt2. Direct phone input remains code-owned. Consent must not trigger this path when the last bot question was unrelated.
- **Desired:** Preserve the operator-offer pending state on this specific safe fallback and add a history/state fallback guard; callback is created only after a valid phone.
- **Owner layer:** V6 publish adapter/state, with focused runtime/adapter tests. Prompt1 is a diagnostic fallback only, not the phone-flow owner.
- **Acceptance:** RED reproduces empty MCP → safe operator question → `да` without phone request; GREEN returns the exact phone question with no gateway calls, unrelated `да` does not enter phone flow, direct phone remains unchanged, H110 empty-evidence and operator regressions pass. No Prompt2/search/transport/n8n change.
- **Boundary:** This does not repair empty MCP retrieval or claim center availability; it repairs only the state/consent transition after the safe fallback.

### H113 — isolate non-operator pending phone consent (2026-08-13, **open: RED first**)

- **Actual:** H112 correctly handles a current operator offer, but its fallback
  branch can still answer a short consent with a phone request when `pending_phone`
  belongs to an unrelated question.
- **Contract:** Only a current, state-validated `operator_contact` offer may
  transition consent to phone capture. An unrelated pending interaction must
  return to the ordinary runtime path without a phone request or state transition.
- **Desired:** Close this consent boundary without changing Prompt1, MCP, Prompt2,
  search, transport, callback privacy or the H112 operator-offer path.
- **Owner layer:** `scripts/nmbot_runtime_adapter.py` and its focused test.
- **Acceptance:** RED reproduces unrelated `pending_phone + да` as a phone
  request; GREEN routes it through the ordinary runtime, keeps `awaiting_phone`
  false, performs no consent transition, and preserves all H112/H110 tests.
- **Boundary:** This does not repair empty MCP retrieval and does not change the
  valid operator offer → consent → phone flow.

### H111 — V6 Prompt2 literal grounding boundary (2026-08-13, **local/model GREEN; deploy not run**)

- **Actual:** exact H110 Prompt2 (`0794093`, prompt SHA256
  `e830118c09b0706e529482145063d9debffa8e049748c5fbbdd16e5a34a22f26`)
  was called through the direct gateway for ten source-linked historical cases
  plus two semantic boundary cases. All twelve calls completed and passed the
  JSON/card-index contract. Eight cases were fully green. Three historical
  cases failed only a test-owned exact-question comparison although runtime
  supplies an abstract `question_goal`, not locked wording. One boundary case
  made an unsupported safety inference from `yard_without_cars`.
- **Contract:** ordinary search questions remain Prompt2-owned natural wording
  constrained by code-owned `question_goal`; exact copying is required only
  when code supplies a locked clarification/recovery/operator question. Card
  prose may restate confirmed fields and their literal meaning, but must not
  convert an amenity or boolean attribute into safety, quality, availability or
  another unsupported conclusion. Prompt1, MCP, state, H105, H106 fallback,
  H108 direct transport and H110 empty-evidence guard remain unchanged.
- **Desired:** minimally strengthen the static Prompt2 grounding instruction and
  re-run the same twelve model payloads against the exact candidate. All
  structural, trusted-index and forbidden-claim checks must pass without
  treating byte-exact ordinary-question wording as a grounding requirement.
- **Owner layer:** static V6 Prompt2 grounding instruction. No parser regex,
  runtime route, question policy, provider, state, search or transport change.
- **RED evidence:** exhaustive receipts under
  `/tmp/opencode/nmbot-h111-v6-batch/real-10-exhaustive/` and
  `/tmp/opencode/nmbot-h111-v6-batch/boundary-2-exhaustive/`: three
  `exact_question`-only failures and one unsupported `безопасность` claim from
  `yard_without_cars`; transport and parse failures were absent.
- **Acceptance:** `yard_without_cars` is presented literally and never as proof
  of safety; the same 10+2 direct-gateway batch passes structural,
  trusted-index and forbidden-claim checks; compatible V6 tests, compile/diff
  and automatic integration review pass. No deploy without a separate decision
  after immutable dry-run. Question-goal and byte-exact locked-question checks
  are explicitly outside this grounding hypothesis.
- **Boundary:** this experiment proves Prompt2 behavior only. It does not prove
  Prompt1, MCP retrieval, state evolution, Jivo delivery or production status.
- **Result:** exact candidate prompt SHA256
  `4f22ba73ef5495968c2a4515b0b2ba18dd94670aba778d683719dd8215ec45f4`
  passed the same ten historical plus two semantic direct-gateway Prompt2 cases
  (`10/10` and `2/2`, no transport or parse failure). The grounding boundary
  case now states the literal «двор без машин», does not infer safety and keeps
  trusted card indices. The initial broader gate passed `91` tests; after
  question ownership was explicitly removed from H111 scope, final reviewed
  commit `aa7ad1e96146d2dba60c9c65ffcf071bc81e626f` passed `89` focused and
  compatible tests;
  compile/diff checks passed; integration review returned
  `pass_with_findings` with no code finding. Raw model outputs were removed;
  privacy-safe reports remain under
  `/tmp/opencode/nmbot-h111-v6-batch/h111-real-10-r2/` and
  `/tmp/opencode/nmbot-h111-v6-batch/h111-boundary-2-r2/`.
- **Reusable rule:** a confirmed amenity or boolean field supports its literal
  statement, not an inferred safety, quality, availability, suitability,
  benefit or causal claim. Structural/grounding checks and runtime-owned
  question checks are separate gates.
- **Remaining unknowns:** three historical model calls differed only in ordinary
  question wording; this is test-policy drift and is not counted as a grounding
  RED. Code-owned byte-exact enforcement for `clarify/recover` conflicts with
  the older optional H100 fixture contract and remains a separate unresolved
  runtime hypothesis. Full Prompt1→MCP→Prompt2→state→Jivo behavior remains
  untested until a separately approved immutable TEST deploy.
- **Deploy-ready receipt:** isolated source commit
  `aa7ad1e96146d2dba60c9c65ffcf071bc81e626f`; immutable dry-run release
  `v6-test-h111-grounding-aa7ad1e-20260813`; archive SHA256
  `687b7ff64154bf60ba02953d2fac799b6e03e5906b84b94d0d0139e23b96aa1f`;
  manifest SHA256
  `a2285c68d1ef0d8f7a5c6ba1dd1df667c3af24bbe68271efef8c2eb0f5db9790`;
  source snapshot `vps-source-20260813-075748-a17f5804db72`, manifest SHA256
  `eeaeb8f1740a3c65fb1b569354859821ec01eeeddc62a03541b4a0a255c773a3`.
  Portable patch `/tmp/opencode/nmbot-h111-grounding-aa7ad1e.patch`, SHA256
  `cdd11e93f2641806998c587a6dfa2d0b6dceb6853f42eb07c1a6fa00bd41c3c2`.
  Dry-run only: no cutover, restart or TEST/Jivo behavior claim.

### H110 — overnight twelve-case first-failure gate and safe publication (2026-08-13, **local GREEN; deploy not run**)

- **Actual:** the redacted historical dialogue set contains twelve recurring
  failure classes across location/state persistence, hard constraints,
  exact-vs-near separation, empty evidence, Prompt2 presentation and provider
  failure. Existing offline gates validate contracts, but do not execute one
  bounded V6 first-failure report over the real source-linked cases.
- **Contract:** run independent deterministic cases in parallel, classify the
  first unmet stage as `prompt1`, `mcp`, `state`, `prompt2`, `provider` or
  `operator`, and keep historical replay separate from live evidence. Facts,
  hard constraints, exact/near labels, operator consent and safe fallback stay
  code-owned; payload text is not MCP evidence. A safety patch may prevent an
  unsupported publication, but must not claim to repair an unavailable
  upstream MCP result.
- **Desired:** produce a privacy-safe JSON receipt with all twelve case
  outcomes, owner-layer hypotheses and reproducible local checks; prove or
  refute each minimal hypothesis; prepare one isolated, immutable deployable
  patch from the known-good H108 source without changing TEST, production,
  n8n, Promptfoo/eval or external gateway contracts.
- **Owner layer:** offline first-failure runner plus the smallest proven V6
  publication boundary. State/request, search-contract, Prompt2 parser and
  operator tests remain separate hypotheses and are not silently folded into
  one patch.
- **Acceptance:** twelve independent cases run with stable IDs and no raw
  outputs; each result has first stage, evidence status, owner and hypothesis;
  empty required evidence makes zero normal Prompt2 calls; non-empty evidence
  preserves the existing Gemini→GPT fallback; focused, compatible, compile and
  diff checks pass; automatic review is GREEN; no deployment occurs in this
  experiment unless separately requested after the receipt is inspected.
- **Boundary:** historical/offline results are not live TEST proof. No n8n or
  MCP trace invention, no production mutation, no secrets/raw client data in
  receipts, and no unrelated runtime/model/prompt changes.
- **Result:** twelve independent offline cases completed with stable IDs and
  `network=false`; all twelve contract projections were green, while runtime
  first-failure status remained explicitly unproven because replay does not call
  V6/MCP/provider. The isolated H110 candidate from H108 passed 20 focused and
  105 compatible tests, compile/diff checks, and automatic integration review
  (`pass`, low risk). Required search with an accepted envelope and empty
  `facts/near` now stops before Prompt2, preserves state and `state_commit=false`,
  while near-only evidence and legacy MCP contract failures retain their prior
  paths. `mcp_contract_violation` survives adapter/API/journal sanitization.
- **Reusable rule:** this patch prevents unsupported Prompt2 publication; it does
  not repair or prove the upstream MCP result. TEST deployment remains a separate
  decision requiring a fresh immutable dry-run and live behavioral evidence.
- **Remaining unknowns:** external MCP retrieval, provider output and Jivo
  behavior were not exercised in this offline cycle; no production or TEST
  mutation was performed.

### H109 — empty evidence must not publish invented options (2026-08-12, **open: RED first**)

- **Actual:** H108 direct gateway reached Prompt1/MCP/Prompt2, but the concrete
  `двушка в центре Москвы` turn had empty `facts/near` and Prompt2 published a
  Зеленоград answer not supported by evidence.
- **Contract:** when a required search has no trusted facts or near options,
  V6 must not call/publish a normal option-presenting Prompt2 answer. It must
  return the existing safe no-results/clarification fallback. Location helper,
  Prompt1, MCP transport and H105 remain unchanged.
- **Desired:** `/start` → `двушка в центре Москвы` produces either grounded
  central options or an honest no-results clarification; it never invents
  another location. Follow-up `давай зеленоград` is then handled as a new
  user request, not inferred from the previous empty answer.
- **Owner layer:** V6 runtime publication gate before Prompt2; no n8n, trace,
  Prompt1 or H105 changes.
- **Acceptance:** local deterministic scenario with empty trusted evidence
  makes zero Prompt2 calls and returns safe failure; non-empty evidence keeps
  existing Prompt2 Gemini→GPT fallback behavior; no unrelated location appears
  in published text; focused and regression gates pass before deployment.
- **Boundary:** no Promptfoo/eval and no TEST deploy before automatic review-gate
  GREEN plus immutable dry-run.

### H106 — Prompt2 Gemini → GPT-4.1-mini fallback (2026-08-12, **open: RED first**)

- **Actual:** live H105 evidence shows Prompt2 can return invalid JSON twice on
  the same Gemini route; the second call is currently another Gemini retry.
- **Contract:** call Gemini once for `v6_answer_writer`; on transport failure or
  structured Prompt2 parse failure, call `openai/gpt-4.1-mini` once. Parse both
  responses with the unchanged Prompt2 contract. If GPT-4.1-mini also fails,
  preserve the existing safe runtime fallback; make no third call.
- **Desired:** reduce Prompt2 failures without changing Prompt1, MCP, H105
  location assistance, state, schema, or safe fallback behavior.
- **Owner layer:** Prompt2 gateway and async/sync runtime orchestration only.
- **Acceptance:** valid Gemini means exactly one call; invalid/transport Gemini
  means exactly Gemini then GPT-4.1-mini; invalid/transport fallback means safe
  failure with exactly two calls; captured payloads prove model order and the
  same prompt/schema; Prompt1/MCP/H105 regression suites remain green.
- **Boundary:** no H100 enablement, no n8n/transport changes, no Promptfoo/eval,
  and no TEST deploy before focused GREEN plus integration review.

### H107 — direct gateway path for GPT-4.1-mini fallback (2026-08-12, **open: RED first**)

- **Actual:** a direct TEST gateway diagnostic with `openai/gpt-4.1-mini`
  returned successfully, while the deployed H106 fallback through the TEST
  webhook ended in transport error. The H106 route therefore did not prove the
  fallback model itself unavailable.
- **Contract:** keep Gemini Prompt2 and Prompt1 on the existing documented TEST
  webhook. Only the GPT-4.1-mini fallback uses the direct gateway request path;
  it keeps the same Prompt2 query, prompt, schema and parameters. Parser and
  safe fallback remain unchanged.
- **Desired:** make the already reachable GPT fallback usable without changing
  search, MCP, H105 location assistance, state or n8n.
- **Owner layer:** V6 transport selection for the Prompt2 fallback only.
- **Acceptance:** primary Gemini uses the TEST webhook; fallback GPT uses direct
  gateway once; valid fallback completes; failed fallback produces the existing
  safe response with no third call; H105/location regressions stay green.
- **Boundary:** no active release change until automatic review-gate and a
  TEST deploy with mandatory stage/fallback diagnostics are green.

### H108 — direct gateway path for all V6 calls (2026-08-12, **open: RED first**)

- **Actual:** the TEST webhook path returns Prompt2 fallback transport errors;
  a direct gateway request with GPT-4.1-mini succeeded. V6 currently selects
  the TEST webhook adapter whenever it is available.
- **Contract:** Prompt1, MCP-bearing Prompt1 transport, primary Gemini Prompt2
  and GPT-4.1-mini fallback all use the direct gateway request path. No V6 call
  uses the TEST n8n webhook. Prompt1/MCP/H105 semantics and Prompt2 parsing stay
  unchanged; no trace is invented.
- **Desired:** remove the transport split so local and TEST calls use one
  reachable gateway path and fallback failures become diagnosable.
- **Owner layer:** V6 transport adapter selection only.
- **Acceptance:** every V6 payload uses `_run_gateway_request_once`; Prompt1 and
  both Prompt2 models preserve payload/schema; tool evidence remains absent unless
  an actual typed trace exists; focused and H105 regressions pass; no third call.
- **Boundary:** no n8n changes, no H100 enablement, no Promptfoo/eval, and no
  TEST deploy before automatic review-gate and dry-run GREEN.

### H082 — TEST Prompt2 operator handoff and sales presentation (2026-08-11, **open: candidate preparation**)

- **Actual:** H079 improved relevance, near-match labeling and missing-fact wording,
  but booking still exposed internal «карточка», unsupported mortgage/sunlight
  branches did not offer a clear operator step, and route-owned search/refine
  replies sounded bureaucratic. The current main prompt also requires empty
  `intro` for clarify/recover and forbids operator presentation, while the new
  product request requires an operator offer when confirmed data is insufficient.
- **Contract:** Prompt2 presents only the final route and supplied `search_result`/
  `trusted_mcp` facts. It must not change route/state, capture a phone, or invent
  mortgage, booking, availability, property or general-knowledge claims. Operator
  wording must be permitted by payload and must not itself mean client consent.
- **Desired:** In a TEST-only candidate, preserve the user's exact rule: «Если для
  ответа клиенту не хватает подтверждённых данных, не придумывай ответ: коротко
  назови, чего не хватает, и предложи передать вопрос оператору.» Also improve
  benefit-led, natural sales wording, near-match clarity, missing-data explanation,
  route-owned search/refine acknowledgement, and removal of internal terms.
- **Owner layer:** Prompt2 presentation only. A future `operator_offer_allowed`
  flag and exact `next_question` belong to payload/orchestrator; phone/state
  activation remains code-owned and is not changed by this hypothesis.
- **Change:** Add a new TEST-only candidate derived from H079. Do not edit the
  main prompt, gateway, runtime, state, parser, model or production contour.
- **Acceptance:** PromptMaster handoff is reflected; same 10-case payloads can be
  compared with H079; strict JSON/card/index/question/forbidden checks do not
  regress; unsupported branches name the missing fact and use only an authorized
  operator CTA; confirmed facts are connected to the client's criterion; near
  differences are explicit; no internal language, promises or invented claims.
- **Boundary:** This hypothesis is not production proof and cannot authorize a
  main-pipeline prompt edit. A real batch requires the old question overlay to be
  reconciled with the new operator CTA before execution.
- **Result:** Two independent 10-case direct `gateway-agent` smoke batches were
  run in parallel with the same H082 manifest, answer-basis overlay, operator
  overlay and question overlay. Baseline H079 returned `10/10`, no transport
  failure and no blocking deterministic failure. H082 returned `10/10`, no
  transport failure, but stopped at the first blocking failure in
  `semantic_fact_check_001`: the card said the car-free courtyard «обеспечивает
  безопасность и тишину», while MCP only confirmed `yard_without_cars`.
  Reports: `/tmp/opencode/v6-prompt2-h082-baseline-20260811T1945Z/batch-report.json`
  and `/tmp/opencode/v6-prompt2-h082-candidate-20260811T1945Z/batch-report.json`.
- **Conclusion:** H082 is **RED / not accepted**. The operator CTA and more
  explicit missing-data wording worked in booking, mortgage and sunlight cases,
  but the sales-benefit rule is too permissive: it allowed a causal lifestyle
  claim not present in MCP. The candidate must not be promoted or used for a
  main-prompt change. The next minimal hypothesis must constrain benefit wording
  to a direct restatement or explicitly mapped field, not infer safety, quietness,
  comfort or other consequences from a property fact.

### H083 — Prompt2 operator offer → consent → phone flow (2026-08-11, **open: RED first**)

- **Owner decision:** The owner accepts H082 sales wording, including natural
  interpretations such as safety/quietness, for the separate TEST contour. This
  supersedes only H082's wording rejection; it does not relax MCP rules for
  prices, mortgage, booking, availability or other concrete property facts.
- **Actual:** Runtime detects operator words in Prompt2 text and sets
  `pending_phone=true`, but the pending interaction is still created from the
  ordinary `question_goal` and may retain `accept_action="normal_prompt1"`.
- **Contract:** A visible operator offer must persist one typed pending action.
  The next standalone consent (`да`) must resolve to `operator_contact`; Prompt2
  must be skipped and runtime must ask «На какой номер вам позвонить?». A visible
  offer is not itself consent and must not enqueue or expose a phone.
- **Desired:** Prove the full two-turn flow with one deterministic same-flow test:
  Prompt2 operator CTA → pending `operator_contact` → client `да` → code-owned
  phone question. Only after focused regression and review may a clean immutable
  TEST artifact be committed and deployed.
- **Boundary:** No production/Jivo deploy before GREEN. No regex scenario routing;
  the existing bounded operator-question detector may only persist typed pending
  intent after the model-visible CTA.
- **RED:** The same-flow test reached the real defect: after Prompt2 returned
  «Передать вопрос об ипотеке оператору?», runtime completed the turn with
  `pending_phone=true` but `pending_interaction.accept_action="normal_prompt1"`.
  Therefore the next standalone `да` was not typed as operator consent.
- **Minimal change:** In both V6 runtime entrypoints, reuse the existing bounded
  `_question_requests_operator()` result and, after normal completed-state
  evolution, call the existing `mark_operator_pending()` helper. This preserves
  the completed dialogue state while replacing only the pending interaction with
  typed `operator_contact`; it does not treat the visible offer as consent.
- **GREEN:** `tests/test_nmbot_v6_operator_offer_flow.py` proves the full sequence:
  Prompt2 CTA → `pending_phone=true` and typed `operator_contact` → client `да` →
  Prompt1 sees `dialogue_context.pending_action="operator_contact"` → Prompt2 is
  skipped → runtime asks exactly «На какой номер вам позвонить?». Focused result:
  `1 passed`.
- **Regression status:** The remaining V6 selection produced `63 passed`; three
  pre-existing main-prompt literal-contract failures remain around
  `expanded_detail`. Full collection also remains blocked by the unrelated
  existing `SelectedEntity` import mismatch in `nmbot_runtime_adapter.py`.
  Neither blocker was changed under H083. Git/deploy remain gated on isolated
  review and a clean immutable source artifact.
### H149 — V6-simple bounded near material and three-object Prompt2 projection (2026-08-14, **TEST candidate**)

- **Actual:** H148's full callback matrix found a real Prompt1 failure on the follow-up `да` after a response naming five Lubertsy projects. In two of three identical direct TEST calls Prompt1 returned five grounded `near` alternatives; the mechanical `near<=3` bound rejected the document with `invalid_prompt1_bounds` before MCP or Prompt 2, producing a safe fallback. The same history also produced a valid zero-near document once, so the defect was a bounded material-shape failure rather than a proven search failure.
- **Contract:** Prompt1 may retain up to five literal, grounded `near` alternatives in best-first order as internal bounded material. The mechanical parser accepts `near<=5` but continues to reject `near>5`, unknown fields, privacy violations and invalid variants. The existing Prompt1→Prompt2 payload boundary projects at most three objects total: exact `facts` first, then the first `near` alternatives in Prompt1 order. Prompt 2 remains the owner of public wording and one logical question.
- **Desired:** A valid follow-up never becomes a technical fallback because four or five safe alternatives were returned internally. The client sees no more than three grounded objects, with facts preferred over alternatives. Preserve state v2, third-turn specialist policy, callback/outbox, phone privacy and rollback contracts without adding a runtime layer, retry, router or regex.
- **Owner layer:** Prompt1 ordering/selection plus the existing mechanical parser and payload boundary. Runtime/state/gateway/adapter are unchanged.
- **Acceptance:** near=4/5 accepted and near=6 rejected; facts-first projection totals at most three objects; repeated Lubertsy follow-up reaches Prompt 2 without `invalid_prompt1_bounds` or safe fallback; ordinary Moscow, ambiguity clarification/follow-up, specialist→phone and callback/outbox/privacy regressions remain green.
- **Result:** Isolated candidate passed **120 tests** and compileall. Canonical focused contract/runtime/phone scopes passed **106 tests** and compileall. H149 has not yet been deployed; TEST smoke is required before marking verified.

### H148 — V6-simple continue nullable-ambiguity normalization (2026-08-14, **TEST verified**)

- **Actual:** After H147 deployment, ordinary TEST requests such as `двушка в Москве` sometimes returned a valid semantic `action=continue` document without the optional wire key `ambiguity`. The narrow parser rejected that harmless serialization variant with `invalid_prompt1_variant_shape` before MCP or Prompt 2, causing a safe fallback. Direct gateway refs `2541433`, `2541434`, `2541437` reproduced the omission; candidate-parser reruns `2541440`, `2541441`, `2541442` accepted the same outputs.
- **Contract:** For `action=continue` only, an absent `ambiguity` key is mechanically canonicalized to `null`; an explicitly present value remains valid only when it is `null`. `clarify` keeps the strict `{action,ambiguity,params}` variant, and `request_phone` remains action-only. All bounds, allowlists, privacy and extra-key checks remain strict.
- **Desired:** Do not turn an equivalent nullable-field serialization into a runtime failure. Preserve Prompt 1 semantic ownership, the existing linear Prompt 1 → MCP/Prompt 2 path, state v2, operator/phone/outbox and rollback contracts. No retry, semantic parser, router or new layer.
- **Owner layer:** Existing mechanical parser boundary only; Prompt 1 still owns the semantic action and search material.
- **Acceptance:** Candidate passed **115 tests** and compileall; canonical focused contract/runtime/phone scopes passed **101 tests** and compileall. Fresh pre-release TEST snapshot `vps-source-20260814-171118-abfaec999fc0` has manifest SHA-256 `c5d04fc7e255c3f562d12874482f3dc0007faf7db67f89b0cc1930a1309dba4a`.
- **Result:** Commit `653a918b4b31c891d8f4dc10fcb5adce1d59cfec` was deployed atomically to TEST as `v6-test-continue-null-normalization-20260814t2011`. The immutable archive SHA-256 is `1b08f283d6aaf4d586e835b4efeec924a30b2cc012c0de1cb10cb323dcfd5d69`; artifact manifest SHA-256 is `0a00c9079ca50ca6feb219e3f199ed2bc9b523c39d022881bbcfbacd15f9dd69`; fresh internal snapshot `vps-source-20260814-171635-84e8f09a4a1b` has manifest SHA-256 `356c76f76a14a3e6fa97f9df238e41f2e433305ed589887a7cba3469bf5de882`. Exact runtime overlay contained only `nmbot_v6/simple_contract.py`; preflight, health, identity, post-smoke recon and canonical systemd guards passed.
- **TEST smoke:** Strict smoke passed. Sequential ordinary/clarification matrix passed 3/3: `двушка в мкр Люблино` and `двушка в Москве` reached Prompt 1, Prompt 2, state and BOT_MESSAGE without safe fallback; the ambiguous budget produced one neutral question without specialist or phone. The follow-up `18 млн` resumed the ordinary path. Sequential third-turn state regression passed: specialist CTA appeared once, consent produced exactly `На какой номер вам позвонить?`, Prompt 2 was not called on the phone path, and no phone was sent. Public `mcp=unknown` remains a provenance limitation, so search correctness or zero MCP calls are not claimed. Production was untouched.

### H147 — V6-simple Prompt1 discriminated clarification contract (2026-08-14, **TEST verified**)

- **Actual:** H146's first live clarification probe correctly understood `Ищу двушку в Москве до 8 или 18 млн` as `action=clarify` with `ambiguity.max_price=multiple_interpretations`, but added an irrelevant `missing` item. The flat parser rejected the semantically correct decision with `clarify_material_not_empty`, so Prompt 2 was never called and the user received a safe fallback. This exposed a Prompt-1/payload contract contradiction, not a search or runtime routing defect.
- **Contract:** Prompt 1 now returns one of three mutually exclusive JSON variants: `continue` with search material, `clarify` with only `action`, typed `ambiguity` and unambiguous `params`, or action-only `request_phone`. The existing parser remains a mechanical JSON/bounds/privacy/allowlist boundary; it does not judge whether ambiguity is genuine. A parsed clarification is normalized at the existing boundary to empty material for Prompt 2. No new runtime layer, semantic router, regex or repair loop was added.
- **Desired:** Genuine ambiguity reaches Prompt 2, which asks one neutral question about only the ambiguous parameter. Optional missing preferences and uniquely normalizable typos remain ordinary `continue`; state v2, third-turn specialist policy, phone/privacy/outbox and rollback contracts remain unchanged.
- **Owner layer:** Prompt 1 semantic decision plus the existing mechanical contract boundary. Prompt 2 remains the sole owner of client prose and the clarification question.
- **Acceptance:** continue/request_phone regressions, minimal union clarify parsing, rejection of mixed-variant fields, Prompt 2 payload normalization, privacy/outbox and third-turn tests; direct TEST model probe with the original ambiguous budget phrase; then immutable TEST release and Jivo clarification smoke.
- **Result:** Isolated candidate passed **108 tests** and compileall. Direct TEST probe task `2541380` returned exactly the `clarify` variant and the candidate parser accepted it, normalizing material to empty. H147 was deployed atomically to TEST as `v6-test-prompt1-union-20260814t164410` from commit `3b044281bdf01b82bbb04f189bcbb30988f56e97`. The immutable archive SHA-256 is `be631b864419b2b414e4cda4c688c56d34ed136bdb93946544b6b460dd1190e7`; artifact manifest SHA-256 is `0980424b9c1d49bc3a39d73616b35173d9bc2bfcc69e9c88edff6979139d8037`; fresh internal source snapshot `vps-source-20260814-164422-2285a48cc9dd` has manifest SHA-256 `caf0cb0ac904ca98213b4eb7e22f8b7d7bbc0d2cf85a3b861b9641ca494a9a27`. Exact release diff contained only `nmbot_v6/simple_contract.py` and `prompts/v6_simple_search_agent.txt`; preflight, health, identity and canonical systemd guards passed.
- **TEST smoke:** Strict Jivo smoke passed with HTTP 200 and `release_gate.accepted=true`. The ambiguous request `Ищу двушку в Москве до 8 или 18 млн` produced one neutral budget question, without a specialist offer, phone or categorical no-result; Prompt 1, Prompt 2, state and BOT_MESSAGE stages were accepted. The answer `18 млн` resumed the normal Prompt-1 → Prompt-2 path without safe fallback or phone flow. A sequential third-turn regression also passed: the specialist CTA was published once, the next semantic `да` produced exactly `На какой номер вам позвонить?`, Prompt 2 was not called on the phone path, and no phone was sent. Public transport trace reports `mcp=unknown`; therefore this result proves accepted publication and UX behavior, not MCP call count or search correctness. Production was untouched.

### H146 — V6-simple typed search clarification (2026-08-14, **reviewed local candidate**)

- **Actual:** A reported V6 reply claimed that no two-room flats matched `д о509 млн`. A bounded TEST-only Prompt-1 comparison showed that both the clean and spaced forms normalized identically to `rooms=2` and `max_price=509000000`, while `facts/near` remained empty and transport-owned tool trace was unavailable. The typo therefore was not a proven ambiguity, and the empty result still cannot be attributed to extraction, invocation, backend or projection from current evidence.
- **Contract:** Prompt 1 owns semantic search interpretation. It may return typed `action=clarify` only when a critical constraint has at least two incompatible interpretations or cannot be safely typed and bounded search would be meaningless. Prompt 1 returns no client prose; Prompt 2 asks exactly one clarification about the typed parameter. Missing optional preferences and uniquely normalizable typos remain `continue`. Clarification is not an empty search result and cannot be replaced by the third-turn specialist CTA.
- **Desired:** Never turn genuine ambiguity into a categorical no-result. Ask one useful question, retain the answer in bounded history, then retry the normal Prompt-1 search route. Preserve request-phone, privacy, one-question, grounding, third-turn specialist and rollback contracts.
- **Owner layer:** Prompt-1 typed decision plus the existing Prompt-2 publication boundary and mechanical parser/runtime routing. No regex, private typo rule, extra classifier or semantic runtime judge.
- **State/rollback:** State remains schema v2. A clarification pair is persisted without advancing the specialist-policy turn counter; the next eligible non-clarify answer emits the deferred specialist CTA once. Fixed specialist fallbacks consume that policy immediately. Candidate state was read successfully by the current canonical v2 reader.
- **Acceptance:** strict clarify cross-field schema; typed finite ambiguity allowlists; nonempty clarification `final_question`; neutral clarification failure; no repeated specialist offer; existing consent/phone/outbox flows; focused V6-simple tests; compileall; ordinary review; then immutable TEST release and controlled semantic smoke only after explicit approval.
- **Result:** Local candidate passed **107 tests**, compileall and static prompt checks. Ordinary review returned `approve` with low residual risk after rollback, repeated-offer, clarification-fallback and type-validation fixes. Real-model clarification behavior and search-tool provenance remain unproven until TEST smoke; production was not changed.

### H145 — V6-simple one-goal final-question revision (2026-08-14, **isolated prompt repair**)

- **Actual:** H144 removed the observed H108 parser hard stops and corrected the invalid phone fixture. The focused rerun T02/T04/T06/T10 had no technical failure and T10 outbox passed, but T02/T04/T06 still produced final questions with two independent goals joined by `или`.
- **Contract:** Prompt 2 must choose one next action from the current dialogue meaning. `final_question` contains one question with one goal; it must never offer two independent directions through `или/либо`. This remains Prompt-2-owned semantics, not a parser detector or code scenario.
- **Desired:** For a missing fact or alternatives, select one most useful next step; do not ask the client to choose between two different goals in the same question. Preserve answer body, proposition linkage, grounding, operator and phone behavior.
- **Owner layer:** Prompt 2 only; full PromptMaster replacement required. H144 parser/phone/runtime mechanics stay unchanged.
- **Baseline:** H144 TEST `v6-test-h144-observed-fields-20260814t115240z`; H143 rollback retained.
- **Acceptance:** same T02/T04/T06 outputs have no `или/либо` and exactly one logical next goal; T10 remains queued/confirmed with no phone leak; no technical failures.
- **Stop:** any parser/phone/operator regression or prompt change that introduces a new semantic layer.
- **Result:** H145 full PromptMaster replacement passed the local **79-test** suite, compileall, static prompt checks and isolated P2 linkage probe. Full isolated P1→P2 T02/T04/T06 probe completed without technical failures, but semantic acceptance remained RED: T04 still emitted two independent final-question directions joined by `или` on both constraint turns. T02/T06 also showed mechanical `или` matches in answer/proposition text, so the gate was not promoted to TEST. No deploy; H144 TEST remains active baseline and rollback target.

### H144 — V6-simple observed H108 fields and corrected phone regression (2026-08-14, **TEST technical GREEN; semantic RED**)

- **Actual:** The ten-case H143 TEST batch stopped on real H108 material fields not in the finite candidate vocabulary: `mortgage_programs`, `zhk_name`, and query parameter `has_finishing`. The phone fixture `70000000001` is not a valid Russian number (`not_found`) and must not weaken the phone parser. The batch runner also failed to classify missing phone confirmation as a hard failure.
- **Contract:** Add only these observed safe H108 keys: `mortgage_programs` and `zhk_name` to literal facts/near, `has_finishing` to bounded request params. Preserve privacy, depth, size and unknown-key rejection. Correct the regression fixture to a valid Russian number and require durable outbox confirmation. No arbitrary allowlist, semantic parser, classifier, router or prompt change.
- **Desired:** T02/T04/T06 reach Prompt 2 when their material is otherwise valid; unknown fields remain honest specialist failures with safe key-only diagnostics; T10 accepts only a valid phone and proves one durable outbox outcome.
- **Owner layer:** mechanical H108 material contract, safe diagnostics and test runner only.
- **Baseline:** H143 active TEST `v6-test-h143-final-question-20260814t102754z`; H138/H108 rollback retained.
- **Acceptance:** focused local tests, safe parser replay, valid-phone/invalid-phone tests, corrected T02/T04/T06/T10 TEST rerun. No production action.
- **Stop:** arbitrary key acceptance, privacy leak, phone parser weakening, missing outbox proof, or any hard transport/schema failure.
- **Result:** H144 local suite **79 passed**. Rerun T02/T04/T06/T10 completed without technical failures; the new fact/param vocabulary was accepted and valid `79990000001` produced one confirmed outbox record without public leak. T02/T04/T06 remain semantic RED because Prompt 2 still emitted two-goal final questions using `или`. H145 owns the prompt-only revision. No production action.

### H143 — V6-simple Prompt2 answer/question separation (2026-08-14, **TEST active baseline**)

- **Actual:** H142 mechanically separates `response` and `final_question`, but H141 Prompt 2 still describes the old two-key output and does not yet own the new body/question split.
- **Contract:** Replace Prompt 2 as a whole. It returns exact `{action,response,final_question}`; `response` contains only the grounded answer body, `final_question` contains one semantically selected next question or empty string. No two independent goals joined by «или». Short replies continue the meaning of the last relevant proposition; only specialist-contact acceptance returns `request_phone`.
- **Desired:** Visually separate answer and question without adding a semantic code layer, while preserving grounding, identity, max three objects, operator timing and phone privacy.
- **Owner layer:** static Prompt 2 only; H142 parser/runtime interface is fixed and unchanged.
- **Baseline:** H142 isolated interface candidate; H141 TEST release remains active until H143 passes its isolated gates.
- **Acceptance:** full PromptMaster replacement, local contract regression, isolated P2 linkage fixtures, same-payload model batch and then TEST deploy only after GREEN.
- **Stop:** any payload/schema drift, question in `response`, two goals in `final_question`, phone/privacy regression or operator regression.
- **Result:** H143 ten-case regression later exposed bounded H108 vocabulary gaps and an invalid bare-phone fixture; those are isolated under H144. H143 remains the rollback baseline during H144.
- **Result:** H143 full Prompt 2 replacement passed local **77 focused tests** and compileall. Isolated live P2 probe passed details/layout acceptance, explicit specialist acceptance, direct specialist request, refusal and new topic: exact three-key JSON, `response` without a question, one `final_question` or empty, no `или/либо`. Immutable TEST release `v6-test-h143-final-question-20260814t102754z` deployed from fresh snapshot. Fresh Jivo TEST proved visual separation, exactly one question, ordinary `Да` continues the information topic without phone, direct specialist request asks for phone, valid phone confirms and remains absent from public record. No production action or production proof.

### H142 — V6-simple separated response and final question contract (2026-08-14, **isolated interface revision**)

- **Actual:** H141 Prompt 2 returns `{action,response}`. The response contains both the answer and the final question, so the renderer cannot reliably separate them and the model can combine two next steps with «или».
- **Contract:** Prompt 2 output becomes exact `{action,response,final_question}`. `response` is the answer body without a client question; `final_question` is one question or `""`. For `request_phone`, both text fields are `""`; runtime publishes the fixed phone question. Parser remains mechanical: shape, types, size and privacy only.
- **Desired:** Publish answer and final question as separate visual blocks while Prompt 2 semantically chooses one next step. No code question counter, `или` detector, consent classifier, scenario router or semantic validator.
- **Owner layer:** interface/parser/runtime publication first; Prompt 2 full replacement is a subsequent linked revision after this contract is green.
- **Baseline:** H141 TEST release `v6-test-h141-semantic-linkage-20260814t095748z`; isolated source `/tmp/opencode/nmbot-v6-simple-H141/source`.
- **Acceptance:** exact three-key parser; empty final question accepted; request_phone has both fields empty; runtime publishes `response`, blank line, `final_question`; phone/privacy/operator/grounding regressions remain green.
- **Stop:** any schema ambiguity, phone leak, state/history regression, broken request_phone, or semantic policy moved into code blocks the revision.
- **Budget:** offline/local only until the interface revision is green; PromptMaster prompt replacement, same-payload batch and TEST deploy are separate gates.

### H141 — V6-simple semantic question-answer linkage (2026-08-14, **TEST deployed**)

- **Actual:** H140 makes the ordinary Люблинский парк query reach Prompt 2, but live TEST showed the next plain `Да` after the ordinary question `Подсказать вам подробнее…?` returned `request_phone`. This is an unsafe false-positive: no specialist was offered.
- **Contract:** Prompt 2 must interpret a short answer against the last still-relevant assistant question and continue that question's meaning. Acceptance of a details question means provide details; acceptance of a layouts question means provide layouts; acceptance of an explicit specialist-contact offer means `request_phone`. This is one semantic dialogue rule, not a list of phrases, routes or scenarios. `request_phone` remains Prompt-2-owned and is allowed only for a direct current specialist/call request or semantic acceptance of a relevant specialist-contact offer. Code must not add consent vocabulary, regex, classifier, question taxonomy or state machine.
- **Desired:** every short answer follows the proposition it answers. Ordinary answer → `Да` continues the requested information as `reply`; technical specialist offer → `Да` returns `request_phone`; direct specialist request and valid phone/outbox behavior remain unchanged.
- **Owner layer:** Prompt 2 only; prompt review/full replacement required. No payload, parser, runtime, state, gateway, model, phone/outbox, selector or legacy change.
- **Baseline:** H140 failed TEST release retained; active rollback H138 `v6-test-h138-remember-offer-20260814t092720z` is healthy.
- **Acceptance:** source-linked P2 fixtures cover multiple different preceding questions and prove that the same `Да` continues each question's meaning rather than matching a phrase. Live isolated probes show ordinary detail-question `Да` remains `reply`, fixed specialist-offer `Да` becomes `request_phone`, and refusal/new topic follow their own meaning. Direct request/phone privacy regressions stay green.
- **Stop:** any phone request after a generic question, false code classifier, phone leak, terminal failure or test regression blocks TEST deploy.
- **Result:** full Prompt-2 replacement passed six isolated live semantic fixtures under one general proposition-linkage rule: ordinary details/layout acceptance, refusal and new topic returned `reply`; explicit specialist offer acceptance and direct specialist request returned `request_phone`. Full isolated two-turn P1→P2 dialogue also kept `Да` after an ordinary question in `reply`. Immutable TEST release `v6-test-h141-semantic-linkage-20260814t095748z` deployed. Fresh Jivo TEST proved: Люблинский парк query returned three literal two-room options; following `Да` continued information dialogue without phone; direct specialist request returned the fixed phone question; valid synthetic phone produced fixed confirmation with no phone in public journal. API/bridge healthy, V6 active, no new error event. No production action or production proof.

### H140 — V6-simple observed H108 project-and-unit fact vocabulary (2026-08-14, **rolled back from TEST**)

- **Actual:** H139 safe shape probe for `двушка в люблинском парке` exposed two literal H108 result shapes. Alongside project facts, it returns a unit-level object with safe keys `area`, `floor`, `floors_total`, `fullprice`, `id`, `novos_id`, `price`, `renovation`, `rooms`, `status`, `title`. H138 rejects the first unknown key `title` and publishes the specialist boundary before Prompt 2.
- **Contract:** Add this complete observed finite H108 project-and-unit key set as literal fact vocabulary in one bounded change. Existing size/depth/privacy/internal-key rejection stays. These fields are material only; Prompt 2 remains responsible for scope and must not infer availability or matching from unrelated project/unit fields.
- **Desired:** The tested Люблинский парк result parses and reaches Prompt 2. Future unknown keys remain fail-closed with a safe key-only receipt rather than raw output or generic dead-end.
- **Owner layer:** mechanical parser vocabulary/diagnostics only. No identity guard, semantic classifier, prompt, gateway, model, state, phone, outbox, selector or legacy change.
- **Baseline:** active TEST `v6-test-h138-remember-offer-20260814t092720z`; H108 rollback retained.
- **Acceptance:** all observed project/unit objects parse literally; unknown key is safe-key-only; H138 operator consent→phone→outbox tests remain green; isolated P1 probe accepts material before a new immutable TEST deploy.
- **Stop:** broadened arbitrary-key acceptance, privacy leak, operator/phone regression, or P1 probe failure blocks deploy.
- **Result:** isolated P1→P2 probe and live TEST ordinary query returned three literal Люблинский парк two-room options. But live TEST then interpreted generic `Да` after an ordinary question as `request_phone`; H140 was atomically rolled back to H138. H141 owns Prompt-2-only correction. No production action.

### H139 — V6-simple observed H108 fact vocabulary and safe field receipt (2026-08-14, **isolated probe complete**)

- **Actual:** H138 makes the technical fallback and related consent path work on live TEST, but the same first query still reaches that fallback because its H108 Prompt 1 material includes the observed safe fact key `new_building_class`, absent from the finite literal fact allowlist. The `invalid_fact_field` receipt did not carry the key name.
- **Contract:** Add only the observed source result key `new_building_class` to the finite literal H108 fact vocabulary. When a literal fact/near key is rejected, retain only its safe key name as `error_field`; never retain its value, raw model output, client text, phone or identifiers. No semantic identity guard, query router, prompt/model/gateway/state/outbox change.
- **Desired:** The tested Люблинский парк response may reach Prompt 2 when its material is otherwise valid; any later unknown fact key produces an honest specialist offer plus a safe journal field receipt for the next bounded hypothesis.
- **Owner layer:** mechanical contract/diagnostics only.
- **Baseline:** active TEST `v6-test-h138-remember-offer-20260814t092720z`, H108 rollback retained.
- **Acceptance:** focused parser accepts `new_building_class`; unknown fact key returns `invalid_fact_field` with key only; privacy tests, operator consent→phone→outbox remain green. Fresh isolated probe first; immutable TEST deploy only after green.
- **Stop:** any privacy leak, broad allowlist, semantic filter, parser/phone/operator regression or failed probe blocks deploy and rolls TEST to H108 on live failure.
- **Result:** local contract exposes safe rejected key name. One isolated P1 shape probe found the full safe key sets recorded in H140; no H139 deploy, TEST release or production action.

### H138 — V6-simple remembered technical specialist offer (2026-08-14, **TEST deployed**)

- **Actual:** H137 correctly publishes the fixed technical specialist offer on a live TEST first query, but deliberately does not commit failed turns to dialogue history. Consequently the immediately related client answer `Да` reaches Prompt 2 without the preceding offer and is treated as unrelated, so it does not return `request_phone`.
- **Contract:** Preserve the three semantic owners. Store only the already-published fixed technical specialist offer as bounded assistant dialogue history, with no intent/consent classifier, no operator state machine and no automatic phone request. Prompt 2 alone interprets a related consent from this visible history. Technical failure still does not commit property material or a successful semantic answer.
- **Desired:** On TEST: technical failure → fixed offer → related `Да` → exact code-owned phone question → valid phone → durable outbox/confirmation; unrelated content remains an ordinary Prompt 1 → Prompt 2 turn.
- **Owner layer:** mechanical history persistence/publication only. No prompt, gateway, selector, model, phone parser, outbox schema, semantic routing or legacy-runtime change.
- **Baseline:** active TEST `v6-test-h137-failure-offer-20260814t091343z`; immutable H108 rollback remains available.
- **Acceptance:** focused runtime/adapter tests prove history contains only the public fixed offer and current safe input, `Да` is forwarded to Prompt 2 with that history, no P1 material commits, and phone/outbox replay/privacy regressions remain green. Fresh TEST synthetic consent→phone→outbox smoke is required before any readiness claim.
- **Stop:** any phone leak, automatic phone request without Prompt 2 `request_phone`, state/history serialization failure, or terminal failure rolls TEST back to H108.

### H137 — V6-simple technical failure business fallback (2026-08-14, **TEST deployed**)

- **Actual:** H134 failed on the first real `двушка в люблинском парке` turn. Prompt 1 returned a useful H108-shaped result but added the source-backed `params.novos_id`; the parser rejected the whole result as `invalid_param_key`, skipped Prompt 2, and published a generic technical text. The public journal retained only `runtime_failure`, not safe `stage/error_code/field`.
- **Contract:** Keep semantic ownership in Prompt 1/Prompt 2. Add `novos_id` to the finite source-backed parameter contract. On Prompt 1 technical failure, do not claim no results: pass an accepted empty material plus an explicit technical boundary to Prompt 2. If Prompt 2 is also unavailable, use one fixed honest specialist-offer text; no automatic phone request. Persist safe diagnostic fields only: stage, error code, field name, no values/raw output/PII.
- **Desired:** Every technical search failure gives a useful next step (specialist offer) instead of a dead-end generic error; accepted empty/no-result remains distinct from technical failure; operator consent still leads to phone/outbox.
- **Owner layer:** contract/diagnostics plus mechanical failure publication; no semantic classifier, parser identity guard, router or new scenario layer.
- **Baseline:** H134 candidate tree; active TEST rollback `v6-test-h108-direct-gateway-cdf3cb1-20260812`.
- **Result:** local focused tests `69 passed`; immutable TEST release `v6-test-h137-failure-offer-20260814t091343z` deployed. Fresh TEST query `двушка в люблинском парке` published the fixed specialist offer rather than the generic dead-end. H137 did not prove the related-consent transition; H138 owns that repair. No production action.
