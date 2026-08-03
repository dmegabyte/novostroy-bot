# Jivo/nmbot диагностика

## Правило источника live-диагностики

Текущий Jivo-статус диагностируется **только через read-only SSH к VPS**.
Перед выводами «бот не работает», «поиск перестал работать», «релиз не
применился» или «ответ не доставлен» сначала выполняется:

```bash
bash scripts/nmbot_diag.sh --vps --json
```

Он получает live runtime endpoint и состояния API/bridge непосредственно с
VPS. Локальные `logs/`, `scripts/nmbot.py diagnose` и fixtures остаются
полезными для поиска причины и локальной разработки, но считаются
историческим/локальным evidence, пока не сверены с VPS. SSH-недоступность
означает только «текущий статус не подтверждён», а не сбой Jivo.

## Быстрые команды

Единая production-диагностика текущего Jivo-контура:

```bash
bash scripts/nmbot_diag.sh --vps --json
```

Тонкий локальный dispatcher для безопасного copy/paste-резюме по одному
идентификатору:

```bash
python3 scripts/nmbot.py diagnose
python3 scripts/nmbot.py diagnose --trace TRACE_ID
python3 scripts/nmbot.py diagnose --trace TRACE_ID --evidence-chain
python3 scripts/nmbot.py diagnose --task TASK_ID
python3 scripts/nmbot.py diagnose --latest
python3 scripts/nmbot.py diagnose --latest --date YYYY-MM-DD --logs-dir PATH
python3 scripts/nmbot.py diagnose --recent 20
python3 scripts/nmbot.py diagnose --recent 20 --date YYYY-MM-DD --logs-dir PATH
python3 scripts/nmbot.py diagnose --trace TRACE_ID --timeline
python3 scripts/nmbot.py diagnose --latest --timeline --date YYYY-MM-DD --logs-dir PATH
python3 scripts/nmbot.py diagnose --summary 1h
python3 scripts/nmbot.py diagnose --summary 1h --date YYYY-MM-DD --logs-dir PATH --human
python3 scripts/nmbot.py diagnose --human
python3 scripts/nmbot.py diagnose --plan --json
python3 scripts/nmbot.py diagnose --plan --human
python3 scripts/nmbot.py tools
python3 scripts/nmbot.py tools --human
```

По умолчанию `diagnose` печатает нормализованный bounded JSON (`--json` можно
указать явно). Если selector не указан, это то же самое, что `--latest`.
`--human` вместо JSON печатает одну безопасную строку с фиксированными полями и
не пересказывает raw child output.

После нормализации `diagnose` добавляет fail-closed owner card из локального
machine source of truth `config/nmbot_stage_map.json`: `owner_source`,
`owner_symbol`, `contract_doc`, `focused_test`, `next_check` и
`owner_confidence`. Эти поля только помогают быстро перейти от ошибки к месту
проверки; они не меняют исходные diagnostic поля. Если stage неизвестен, map
невалиден или task/provider layer нельзя однозначно связать со stage, owner-поля
остаются `null`, а `owner_confidence` — `unknown`. `next_check` содержит только
безопасную локальную команду вроде focused pytest или
`python3 scripts/nmbot_check.py <scope> --dry-run`: без task id, shell
interpolation, secrets, VPS или live fetch.

`diagnose --plan` поверх тех же local-only selectors добавляет к JSON
безопасный `edit_plan`: bounded stage, problem code, список repo-relative файлов
`read_first`, поверхность предполагаемой ручной правки и ровно один локальный
`verification_command` из owner card. Если stage/owner map неизвестны или
неоднозначны, план получает статус `blocked`, не угадывает владельца и оставляет
`suggested_change_surface` пустым. В `--plan --human` вывод остаётся одной
безопасной строкой и добавляет только компактные поля `plan_status`, `surface` и
`verify`.

План — это навигация для handoff, а не разрешение на автоматическую правку или
deploy. `diagnose --plan` сам не изменяет файлы, не запускает тесты/check, не
ходит в VPS/network/Jivo/model и не даёт deployment authorization; после ручной
правки всё равно нужен обычный impact chain и явная верификация.

Каждый обычный single-result `diagnose` и `--recent` дополнительно содержит
bounded `diagnostic_envelope` со схемой `nmbot.diagnostic.v1`: только
`evidence_scope`, bounded status/runtime/stage/error/owner/duration, два boolean
correlation-флага (`trace_present`, `task_present`) и safety-флаги. Envelope не
содержит raw ids, task/trace ids, timestamps, prompts, payloads, contacts,
exceptions, tokens или raw child output. Если evidence не хватает, поля остаются
`null`/`UNKNOWN`, wrapper не угадывает.

Обычный `diagnose --trace` читает только локальные файлы
`logs/n8n_bridge_structured.jsonl` и `logs/dialogue_journal.jsonl` через
существующий Jivo dialogue diagnoser.

`diagnose --trace TRACE_ID --evidence-chain` — отдельный bounded read-only
маршрут со схемой `nmbot.evidence_chain.v1`. Он находит точный ход в
`dialogue_journal.jsonl`, берёт primary gateway task из безопасного runtime
summary, читает его status/result и проверяет не более шести следующих task ID
как кандидатов shortlist/pair enrichment. Child принимается только при
совпадении одной карточки с primary по ID/имени и допустимому времени. Отчёт
показывает primary/accepted child tasks, добавленные и нормализованные поля,
поддержку публичных claims, candidate conflicts, первую точку расхождения и
предполагаемый owner. Корреляция child по соседним ID — эвристика, а не
authoritative lineage; такие находки нельзя автоматически называть багами.

Evidence-chain может выполнять read-only запросы к Overmind и требует ровно
один явный `--trace`. Он несовместим с `--task`, `--latest`, `--recent`,
`--summary`, `--plan` и `--timeline`. Raw gateway response, URL/link-поля,
prompt, payload, контакты, tokens и произвольный hidden text в отчёт не
попадают. Команда локализует evidence chain, но сама не доказывает активный
process cwd/release, planner semantics, Jivo delivery или production health.

`diagnose --task` вызывает read-only Overmind status/result diagnoser и печатает
нормализованный bounded JSON. Это не доказательство production-health: без свежей
VPS/Jivo-проверки вывод показывает только локальные/diagnoser evidence. Wrapper не
печатает raw payload, model output, prompt text, tokens, contacts или secrets.
Для альтернативного каталога логов можно добавить `--logs-dir PATH`, для task —
ещё `--date YYYY-MM-DD`.

`diagnose --latest` выбирает latest actionable local error event из локального
`logs/bot_error_events-YYYY-MM-DD.jsonl` (по умолчанию — текущая UTC-дата) и
переиспользует тот же безопасный route: сначала `task_id`/`gateway_task_id` или
`task.id`/`task.task_id`, иначе только raw `trace_id`. `trace_ref`, conversation
и session refs не считаются raw trace selector. Команда не делает live fetch,
не печатает raw journal row, raw trace id, exception, preview, model, prompt,
contacts, tokens или произвольные поля; если локальных evidence нет, она
возвращает bounded `no_evidence` с next command `bash scripts/nmbot_diag.sh --logs`.

`diagnose --recent N` строит только локальную read-only сводку по последним `N`
parseable JSON-строкам того же `bot_error_events-YYYY-MM-DD.jsonl`, где `N` —
десятичное число от 1 до 100. Команда не вызывает child diagnostic, Overmind,
VPS, Jivo, provider, модели, тесты или checks: она только читает выбранный JSONL,
игнорирует malformed lines и группирует безопасные коды из `error_code`, затем
`error_type`, затем `category` вместе с безопасным `stage` и историческим
`runtime_version` из самого event. Поддерживаются только точные значения
`V0|V2|V3` без учёта регистра; отсутствующее, невалидное или non-scalar значение
показывается как `UNKNOWN` с `runtime_version_source=insufficient_event_evidence`.
Known versions получают `runtime_version_source=journal_event`. JSON-схема:
`nmbot.diagnose.recent.v1`; root-поле `runtime_version_scope` всегда равно
`historical_event_evidence_not_current_process`, а `runtime_versions` даёт
счётчики по actionable events в порядке `V0`, `V2`, `V3`, `UNKNOWN`. `--human`
печатает одну строку максимум по пяти группам и показывает версию рядом с кодом,
например `main_search_timeout [V2] (7; owner: search)`, затем рекомендует
`bash scripts/nmbot_diag.sh --logs`. `--recent` нельзя совмещать с `--trace`,
`--task`, `--latest` или `--plan`: это агрегированная сводка, а не edit plan.
Поля вроде model, prompt, text, task/trace id, exception, raw row, payload,
contacts, token и timestamps не выводятся.

`diagnose --timeline` можно совмещать ровно с одним selector
`--trace TRACE_ID`, `--task TASK_ID`, `--latest` или с implicit latest. Он
несовместим с `--recent` и `--plan`. Timeline переиспользует только уже
нормализованные child data и печатает additive поле `timeline` со схемой
`nmbot.diagnose.timeline.v1`: bounded `steps[]` (`stage`, `status`,
`duration_ms`, `owner_layer`, `error_code`), `first_failed_stage`,
`last_successful_stage` и boolean `correlation_coverage`. Raw ids, raw child
output, prompts, payloads и timestamps не выводятся. Для latest wrapper берёт
идентификатор только из выбранного локального error event: `task_id` /
`gateway_task_id` / `task.id` / `task.task_id`, иначе raw `trace_id`; `trace_ref`,
session и conversation не угадываются. Если evidence нет, возвращается bounded
`no_evidence`. Task route остаётся read-only diagnostic route; не используйте его
как production-health proof.

Для trace-route timeline разворачивает безопасные bridge `evidence[]` и, когда
audit-корреляция найдена, добавляет bounded main-search gateway attempts. У
attempt допустимы только stage/status, model, `ok/empty/safe`, `duration_ms`,
`parse_status=ok|invalid_json|missing` и безопасный gateway task ID. Query,
prompt, provider response и raw payload в timeline не попадают.
Gateway error events также содержат только allowlisted status/stage/error code,
exception type, safe task ID, duration/parse status и bounded shape metadata.
Raw exception text, provider/model response, query, prompt, payload, headers,
contacts и tokens не сохраняются.

`diagnose --summary 1h [--date YYYY-MM-DD] [--logs-dir PATH] [--human|--json]`
читает только локальные `dialogue_journal.jsonl` и
`bot_error_events-YYYY-MM-DD.jsonl`. Другие окна, selectors, `--recent`, `--plan`
и `--timeline` rejected fail-closed. Окно считается детерминированно: если в
fixtures есть parseable timestamps, берётся последний timestamp из выбранных
inputs и час назад от него; wall clock не используется. Если timestamps нет,
статус `no_evidence`. JSON-схема: `nmbot.diagnose.summary.v1`; выводит только
traffic counts (`user_turns`, `bot_turns`), actionable error count/rate,
fallback count/rate если безопасно inferable, latency count/p50/p95/p99 только из
числового `total_ms`, counts runtime versions `V0|V2|V3|UNKNOWN`, и saturation
всегда `{status:"unavailable", reason:"not_present_in_local_journals"}`. Raw
rows/text/ids/timestamps/models/prompts не выводятся. `--human` — одна bounded
строка.

Latency берётся из канонического journal-контракта
`runtime_summary.timing_ms.total`; top-level `total_ms` и прежний
`runtime_summary.timing.total_ms` поддерживаются только для совместимости.

`python3 scripts/nmbot.py tools [--human|--json]` читает только локальный
machine-readable registry `config/nmbot_diagnostic_tools.json` и ничего не
запускает. Registry fail-closed валидируется wrapper'ом и маркирует current,
specialized и legacy diagnostic tools, включая network/side-effect границы,
canonical wrapper и replacement notes. Legacy `nmbot_health.py`,
`nmbot_env_check.py`, `nmbot_deploy_smoke.py` и `find_dialog.py` перечислены для
видимости, но не являются normal routes и не вызываются wrapper'ом.

Тонкие namespace aliases сохраняют child exit code и не копируют бизнес-логику:

```bash
python3 scripts/nmbot.py trace analyze ...
python3 scripts/nmbot.py trace dialogue ...
python3 scripts/nmbot.py dialogue report ...
python3 scripts/nmbot.py planner find ...
python3 scripts/nmbot.py runtime compare ...
python3 scripts/nmbot.py release identity read
python3 scripts/nmbot.py release identity show
python3 scripts/nmbot.py release status ...
python3 scripts/nmbot.py architecture ...
```

Unknown/missing namespace subcommands are rejected before subprocess. The
`release identity` wrapper alias is deliberately read-only: it accepts only
`read` or `show` and rejects missing subcommand, `create`, `--write` and extra
args before subprocess. Direct `scripts/nmbot_release_identity.py create --write`
is mutating and is not exposed through the alias. Atomic release/deploy,
env_check, health, deploy_smoke, backfill and legacy find_dialog routes are
intentionally not aliased.

Некоторые specialist tools выглядят локальными только в default-режиме, но их
границы шире: `dialogue report` и `planner find` могут читать production logs по
SSH при явном `--prod`, а `runtime compare` сам ходит по SSH к защищённым
loopback runtime-version endpoints. Registry поэтому маркирует их как
`network=true` и `evidence_scope=mixed`. Legacy `nmbot_env_check.py` создаёт и
удаляет probe-файл в logs dir, а legacy `find_dialog.py` при `--prod` читает
production logs по SSH и в local fast mode может создать/обновить SQLite FTS
index, поэтому у него `network=true`, `evidence_scope=mixed` и
`side_effects=true`.

Важно: `--recent` помогает отличить повторяющуюся локальную ошибку от единичной
и показывает только per-event historical runtime evidence. Это не current process
truth: нельзя выводить активную версию процесса из error journal, selector-файла,
`.env` или текущего локального runtime. Чтобы доказать текущую активную версию,
используйте fresh VPS diagnostic:

```bash
bash scripts/nmbot_diag.sh --vps --json
```

Только его `current_runtime_version` подходит для вывода о current production
runtime. Для production-вывода всё равно нужны свежие VPS/Jivo evidence по
runbook.

Текущий production runtime — это `novostroy-bot-api.service` (`scripts/nmbot_api_server.py`, localhost `:8088`) и `novostroy-bot-n8n-bridge.service` (`scripts/nmbot_n8n_bridge_server.py`, localhost `:8093`). Legacy `novostroy-bot.service` / `scripts/chat_tester_bot.py` относится к Telegram rollback-истории и не является current Jivo production.

Проверка активной версии процесса:

```bash
curl -fsS -H "Authorization: Bearer $NMBOT_API_TOKEN" \
  http://127.0.0.1:8088/api/runtime-version
```

Проверка сохранённого selector-файла:

```bash
cat data/nmbot_runtime_version.json
```

Эти значения могут кратко отличаться только во время restart/controlled switch.
Для вывода о текущем процессе источники имеют строгий приоритет:

1. `GET /api/runtime-version` — единственное доказательство active runtime;
2. `data/nmbot_runtime_version.json` — только сохранённый selector/fallback,
   не доказательство уже запущенного процесса;
3. `dialogue_journal.jsonl` и его audit summary — исторические версии отдельных
   turn/session (включая override), не версия текущего production-процесса.

`bash scripts/nmbot_diag.sh --vps --json` возвращает endpoint отдельно как
`current_runtime_version`. Если он `unverified` или `unknown`, нельзя подменять
его значением selector-файла или audit-журнала.

На 2026-07-30 текущий `nmbot_diag.sh --vps --json` ещё не умеет подтвердить V1
selector на live endpoint и возвращает `unsupported_or_missing_runtime_version`.
Пока это ограничение действует, не делайте обратный вывод, что активен V2
fallback: отсутствие V1-подтверждения в diagnostics — это diagnostic gap, а не
доказательство текущей версии процесса.

Анализ production trace-лога без вызова Jivo или LLM:

```bash
bash scripts/nmbot_jivo_audit.sh --last 200
```

## Один локальный bridge smoke

`scripts/nmbot_bridge_smoke.py` — отдельный diagnostic harness, не runtime
клиент и не часть обычного production-потока. Он предназначен только для
явно разрешённой локальной проверки пути API → bridge → Jivo: без `--live`
сеть не вызывается, а с ним выполняется ровно один synthetic
`CLIENT_MESSAGE` в literal loopback bridge (`127.0.0.1:8093` по умолчанию).
Удалённые host'ы, URL в `--host` и небезопасные port'ы отклоняются.

Перед запуском в окружении процесса должны быть только два нужных секрета:
`JIVO_PROVIDER_TOKEN` и `NMBOT_N8N_BRIDGE_TOKEN`. Скрипт не читает `.env`, не
принимает секреты в аргументах и не печатает/сохраняет токены, URL, payload,
headers, synthetic IDs или текст. Его единственный результат — bounded JSON:
HTTP status, `accepted_async` и `trace_ref`, только если bridge сам его вернул.
`accepted_async` означает лишь приём bridge, а не terminal delivery.

```bash
python3 scripts/nmbot_bridge_smoke.py --live
```

После единственного smoke подтверждайте terminal delivery отдельно, read-only
audit-маршрутом (он читает delivery trace с VPS, но не вызывает Jivo):

```bash
bash scripts/nmbot_jivo_audit.sh --delivery-trace
```

Для copy/paste этой postcondition-команды без HTTP-запроса есть также:

```bash
python3 scripts/nmbot_bridge_smoke.py --delivery-trace
```

Строгая проверка полной lifecycle-цепочки допустима только для
`jivo_delivery_trace.jsonl`:

```bash
python3 scripts/nmbot_jivo_trace_analyze.py /path/to/jivo_delivery_trace.jsonl --strict
```

`n8n_bridge_structured.jsonl` — legacy технический журнал: анализируйте его
только без `--strict`, он не доказывает полный terminal lifecycle.

Статическая проверка архитектурных контрактов:

```bash
python3 scripts/nmbot_architecture_preflight.py --strict
```

## Карта этапов runtime trace

Новые V2-записи могут содержать компактное поле `execution_path`: `path_id` и
упорядоченный список `stage_id`, `status`, а для финального слоя — `published`.
Trace не дублирует архитектуру. Чтобы по найденному `path_id` открыть владельца,
source, prompt, payload stage, документ и focused test каждого этапа, используйте:

```bash
python3 scripts/nmbot_response_path.py --path-id jivo.v2.turn.v1
python3 scripts/nmbot_response_path.py --path-id jivo.v2.turn.v1 --json
```

Для локального active path по версии:

```bash
python3 scripts/nmbot_response_path.py --version v2
```

Machine source of truth для runtime stage ownership и owner card —
`config/nmbot_stage_map.json`. Старые journal rows без `execution_path` остаются
валидными. Статус этапа доказывает только runtime/API границу: terminal delivery
вынесен в отдельный `jivo.bridge.delivery.v1`, потому что без bridge correlation
`TurnResult` не может честно утверждать, что Jivo уже доставил сообщение
клиенту.

## Пошаговый отчёт диалога

Чтобы одним вызовом найти production-диалог и собрать timeline из
`dialogue_journal.jsonl` + `planner_trace`, используйте:

```bash
python3 scripts/nmbot_dialogue_report.py --prod \
  --q 'Ново-Молоково' --q 'наличие' --date 2026-07-21 --show-text
```

Все `--q` по умолчанию должны совпасть в одной session key. Для альтернативных
совпадений добавьте `--any`; для известной сессии используйте `--session`.
Скрипт показывает безопасные planner/runtime-поля, отмечает соседние session
keys и детерминированные находки вроде `operator_topic_mismatch`.
Телефоны, email, токены и идентификаторы редактируются до вывода. Источник —
только read-only production journal и planner trace; LLM и CRM не вызываются.

Каждая новая canonical Jivo-запись содержит явное
`runtime_version=V0|V1|V2|V3`. Для старых append-only строк используется безопасный
sidecar, сам `dialogue_journal.jsonl` не переписывается:

```bash
# Сначала только оценка
python3 scripts/backfill_dialogue_runtime_versions.py

# Атомарно записать sidecar
python3 scripts/backfill_dialogue_runtime_versions.py --write
```

Sidecar: `logs/dialogue_runtime_versions_backfill.jsonl`. Версия восстанавливается
только из явного `/start_0|1|2|3`, lifecycle-текста или действующего session
override. Если история global selector недоказуема, отчёт честно показывает
`runtime_version=UNKNOWN` и `runtime_version_source=insufficient_history`, а не
угадывает V0/V2. `nmbot_dialogue_report.py` подхватывает sidecar автоматически;
альтернативный файл можно передать через `--runtime-backfill PATH`.

Новые строки также содержат `release_id` — идентификатор source bundle, из
которого был дан ответ. В `nmbot_dialogue_report.py` он выводится рядом с
`runtime_version`. Старые строки честно получают `release_id=UNKNOWN`; это не
следует восстанавливать по догадке. Правила создания manifest и rollback — в
`docs/NMBOT_RELEASE_IDENTITY.md`.

### Экспорт диалогов в Google Sheet

Для человекочитаемой выгрузки canonical Jivo dialogue journal используется
systemd timer `nmbot-dialogue-sheet-export.timer`. Он запускает exporter раз в
час. Exporter читает redacted append-only dialogue journal и upsert-ит стабильные
dialogue IDs в Google Sheet tab `Диалоги`, не печатая raw payload, токены,
телефоны или полный текст секретных полей.

Кодовые anchors: `scripts/nmbot_dialogue_sheet_exporter.py:1-7,23-41,401-423`.
Systemd anchors: `deploy/systemd/nmbot-dialogue-sheet-export.service:1-12` и
`deploy/systemd/nmbot-dialogue-sheet-export.timer:1-10`.

### Правило оценки строк «Диалоги»

Строка оценивает путь клиентского запроса, а не только качество видимого
текста. Для каждого вывода нужно указывать доказательность:
`подтверждено`, `частично` или `недостаточно данных`. Первой причиной считают
первый подтверждённый сбой; если evidence не хватает, владельца сбоя не
угадывают.

| Проверка | Primary evidence | Допустимый вывод |
|---|---|---|
| Сегмент | ID и текст journal | Одиночный `/start_*` — артефакт, не клиентский диалог |
| Понимание и план | `planner_trace` (`final_decision`, `canonical_valid`, errors) | запрос распознан / проблема planner |
| Поиск и данные | runtime summary, gateway-task; для точности — `facts/near/missing` и effective constraints | поиск не стартовал, timeout, неверные данные либо `недостаточно данных` |
| Ответ | опубликованный текст + quality blockers / answer gate | проблема фактов, структуры или языка |
| Контекст | `state_before` → `state_after` | параметры, выбранный объект или тема сохранены / потеряны |
| Доставка и скорость | Jivo bridge trace | terminal outcome и end-to-end latency; journal сам по себе не доказывает доставку |

Нельзя утверждать, что поиск дал неверный результат без `facts/near` и
effective constraints; что клиент получил ответ без bridge trace; или что
planner неверно понял запрос без planner trace. Безопасный fallback защищает
клиента, но не подтверждает основной путь как успешный.

| Колонки Sheet | Как формируются |
|---|---|
| A `ID диалога`, B `Дата и время`, G `Дата и время последнего обновления` | Стабильный segment ID и первое/последнее событие canonical journal |
| C `Статус` | Эвристика exporter: error, незавершён, handoff или активен; это не delivery verdict |
| D `Диалог` | Redacted последовательность сообщений пользователя и бота из journal |
| E–F `Модель ответа` / `Модель поиска` | `runtime_summary.model_usage` и безопасные gateway-attempt details |
| H `Ошибка обработки` | Allowlisted `error_summary`: status, codes, stages, fallback |
| I `Версия runtime` | Явная runtime version из journal/runtime summary |
| J `Backend/MCP summary` | Search/enrichment call counts и безопасные названия полей; не доказательство качества результатов |
| K `Memory/state summary` | Safe before/after state: param keys, visible options, selected flag |
| L `Исполнение` | Короткая фактическая цепочка runtime/models/call counts; сложный случай дополняют planner, gateway-task и bridge evidence |
| M `Итог диагностики` | Первый подтверждённый сбойный этап и уверенность; не общее впечатление о диалоге |
| N `Аналитика` | 2–4 фразы: запрос → решение системы → первый подтверждённый сбой → граница доказательств |
| O `Решение / следующий шаг` | Только действие, связанное с подтверждённой причиной; при неизвестной причине — следующий диагностический сбор данных |

Текущий exporter автоматически пишет A:N. Колонка O добавлена вручную для
пилотной строки; перед массовым заполнением её нужно включить в exporter и
добавить безопасные correlation-поля planner/gateway/bridge.

## Инварианты одного turn

Один входящий turn должен иметь ровно один terminal outcome:

- финальный ответ;
- явный handoff оператору;
- явная ошибка/timeout.

`accepted_async` — промежуточное подтверждение приёма, а не ответ клиенту. Само по себе оно не доказывает, что Jivo показал клиенту временную реплику: для UX-вывода нужна корреляция с конкретным диалогом и Jivo-side delivery/rendering evidence.

## Повторные статусы ожидания

Код bridge поддерживает опциональный, выключенный по умолчанию цикл статусов.
При включённом `NMBOT_BRIDGE_STATUS_UPDATES_ENABLED=1` первый клиентский статус
отправляется через три секунды (либо через значение
`NMBOT_BRIDGE_STATUS_INTERVAL_SECONDS`), следующие — через тот же интервал до
получения финального ответа. Шаблоны берутся из
`NMBOT_BRIDGE_STATUS_TEMPLATES` и разделяются символом `|`; пустое значение
использует безопасный встроенный набор.

Безопасная подготовка `.env` без печати значений:

```bash
python3 scripts/nmbot_env_secrets.py --env .env \
  --key NMBOT_BRIDGE_STATUS_INTERVAL_SECONDS --value '3'
python3 scripts/nmbot_env_secrets.py --env .env \
  --key NMBOT_BRIDGE_STATUS_TEMPLATES \
  --value 'Уже работаю над вашим запросом.|Проверяю нужную информацию.|Уточняю детали, чтобы ответить точнее.|Ещё немного — готовлю ответ.'

# Включать только при отдельном согласованном production deploy:
python3 scripts/nmbot_env_secrets.py --env .env \
  --key NMBOT_BRIDGE_STATUS_UPDATES_ENABLED --value '1'
```

Проверка перед интеграцией:

```bash
python3 -m py_compile scripts/nmbot_n8n_bridge_server.py scripts/nmbot_env_secrets.py
PYTHONPATH=. python3 -m pytest -q \
  tests/test_nmbot_n8n_bridge_transport_timeout.py \
  tests/test_nmbot_jivo_trace_analyze.py -x
```

После согласованного deploy меняется только bridge-контур. Старый ручной
backup/sync файла больше не используется: применяйте bridge-prefixed immutable
маршрут из `docs/NMBOT_ATOMIC_RELEASES.md` (`snapshot-vps-bridge-source` →
`prepare-bridge-worktree` → `build-bridge-from-worktree` → `bridge-preflight` →
`bridge-deploy`). После deploy проверить `:8093/health`, затем выполнить один
Jivo turn. В trace должны появиться несколько
`stage=status_update` и соответствующие `jivo_response_returned` с
`delivery_role=status`, после них — один финальный `terminal_delivery` с
`terminal_event=BOT_MESSAGE` или `INVITE_AGENT`. `accepted_async` — только
промежуточный транспортный ack. `terminal_send_accepted` подтверждает, что
Jivo API принял финальное событие, но не доказывает его показ клиенту: для
этого нужно отдельное Jivo-side evidence.

Откат конфигурации без удаления кода:

```bash
python3 scripts/nmbot_env_secrets.py --env .env \
  --key NMBOT_BRIDGE_STATUS_UPDATES_ENABLED --value '0'
systemctl --user restart novostroy-bot-n8n-bridge.service
```

Trace должен позволять связать безопасные `trace_id`/`event_id_ref` с этапами:

```text
bridge_accepted -> api_completed|api_failed -> terminal_selected
-> jivo_send_attempted -> jivo_response -> terminal_delivery

terminal_delivery=terminal_send_accepted + client_delivery_unconfirmed
не следует называть delivery_complete без независимого Jivo-side receipt.
```

Скрипты намеренно не печатают текст клиента, payload, токены и Authorization.

## Runtime summary by version

### V2 runtime_summary (H054 phase 6)

API/V2 добавляет в безопасную meta-трассу и в `dialogue_journal.jsonl` агрегированное поле `runtime_summary`. Это V2 diagnostic surface, not a generic proof that both runtimes expose the same semantics. Поле предназначено для диагностики продукта без раскрытия клиентских данных.

V0 использует тот же безопасный envelope и дополнительно публикует точные
`call_counts.scenario_search` и `call_counts.answer`. Для V0 `planner` отражает
вызов объединённого scenario/search prompt, `search=1` ставится только на
search-action, а `gateway_attempts` равен сумме двух V0 port-вызовов. Поля
`state_before/state_after` показывают только ключи параметров, число вариантов,
наличие выбранного объекта, pending action и active topic.

Разрешённая форма:

- `stage`, `action`, `answer_kind` — короткие bounded enum/string;
- `timing_ms.planner/execution/response/total` — целые миллисекунды, ограничены сверху;
- `call_counts.planner/search/selected_enrichment/gateway_attempts` — семантические счётчики. `search=1` означает один runtime search action, provider retries не считаются отдельными semantic search calls;
- `state_before` и `state_after` — только `param_keys` без значений, `visible_options_count`, `selected_present`, `pending_followup`, `active_topic`;
- `question_count`, `final_question_at_end`;
- `quality_blockers` — только allowlist: `runtime_error`, `question_count_not_one`, `final_question_not_at_end`, `search_without_cards`, `enrichment_error`;
- `grounding_scope="canonical_response_plan"` — честная маркировка области проверки. Это не `grounded=true`: отдельной evidence-to-answer валидации тут нет.

### Terminal error summary

Каждый terminal bot turn получает `error_summary`, в том числе чистый turn:

- `status`: `ok`, `degraded` или `failed`;
- `codes`: только allowlist-коды (`runtime_failure`, composer validation, runtime blockers или `search_validation_error`);
- `stages`: только `runtime`, `composer`, `search_validation`, `jivo_handler`, `bridge_upstream`, `bridge_delivery`;
- `fallback`: был ли показан безопасный fallback.

`ok` всегда хранится как пустые `codes` и `stages`. Необработанное исключение
Jivo получает terminal fallback с `jivo_handler_exception`, а bridge добавляет
отдельный безопасный `system` event при timeout/upstream/delivery error. Поэтому
ошибка не теряется из привязки к диалогу. Поле — краткая навигация к отдельному error
journal, а не дамп исключения: traceback, request/payload, текст клиента,
контакты, prompt, provider response и неизвестные ключи отбрасываются.

Запрещено и sanitizer обязан отбрасывать: текст клиента, текст ответа как диагностическое поле, названия/ID вариантов, raw params/values, телефоны, prompt, payload, provider response, traceback, произвольные вложенные ключи.

Для production `dialogue_journal.jsonl`, где обычно есть `conversation_ref`, `session_key_ref` и `event_id_ref`, но нет bridge `trace_ref/turn_ref`, используйте audit-only режим:

```bash
python3 scripts/nmbot_jivo_dialogue_diagnose.py \
  --audit-log logs/dialogue_journal.jsonl --audit-only --last 20
```

Audit-only не требует bridge log и не пытается выдумать корреляцию. Он читает только sanitized audit records, выбирает строки с `runtime_summary`, печатает безопасные opaque refs (`conversation_ref`, `session_key_ref`, `event_id_ref`), `ts`, safe `answer_kind`, per-turn runtime actual и агрегаты: total turns, суммарные calls, blocker counts, timing p50/p95. Текст клиента/бота, полный телефон, prompt, payload, provider response и названия карточек не выводятся.

Обычный bridge-correlation режим `scripts/nmbot_jivo_dialogue_diagnose.py <bridge-log> --audit-log logs/dialogue_journal.jsonl` тоже умеет читать вложенный `runtime_summary` и добавляет в `actual` агрегаты `runtime_stage`, `runtime_action`, `runtime_answer_kind`, `runtime_call_counts`, `runtime_state_before/after`, `runtime_timing_ms`, `runtime_question_count`, `runtime_final_question_at_end`, `runtime_quality_blockers`, `runtime_grounding_scope`. Корреляция остаётся честной: bridge-события связываются с audit только по имеющимся `trace_ref`/`turn_ref`; если journal даёт только `conversation_ref/session_key_ref`, bridge-mode оставляет coverage gap и не фальсифицирует match.

Новый bridge передаёт сгенерированный им UUID в API только по внутреннему
заголовку `X-NMBOT-Trace-ID`. API принимает только canonical UUID, сразу
преобразует его в `trace_<sha256[:12]>` и передаёт дальше только safe ref.
Dialogue journal хранит только этот safe `trace_ref`; raw UUID не сохраняется в
journal/runtime meta. Старые строки без `trace_ref` остаются корректным
historical evidence и по-прежнему показывают coverage gap, а не выдуманный
match. Runtime summary дополнительно может содержать до пяти allowlisted
`gateway_attempt_details`, используемых trace timeline.
Bridge/audit diagnoser принимает из входных событий только canonical
`trace_[0-9a-f]{12}`. Любой произвольный `trace_ref` отбрасывается; safe ref
заново вычисляется из внутреннего raw bridge trace ID и сам raw ID не печатается.

### V0 diagnostics

V0 диагностируется отдельно: сначала подтвердите активную версию через `GET /api/runtime-version`, затем используйте V0 harness и первый Jivo trace. Не интерпретируйте V2 `runtime_summary` как V0 contract.

```bash
PYTHONPATH=. pytest tests/test_nmbot_v0_runtime.py tests/test_nmbot_v0_test_harness.py
python3 scripts/nmbot_v0_test_harness.py --scenario all --json
```

После первого V0 smoke сразу смотрите trace/log. Если первый turn упал, batch останавливается до разбора слоя: selector, payload shape, V0 scenario/search, V0 answer или Jivo delivery.

## Historical: P1 staging-проверка Jivo adapter

На 2026-07-16 в staging добавлены per-session serialization и bounded event-id dedup для `CLIENT_MESSAGE`. Синхронизация потребовала также обновить `chat_tester_bot.py` и `followup_intent_classifier.py`: прежний staging-набор не содержал импортов, необходимых `nmbot_api_server.py`.

Проверенный минимум:

- `py_compile` трёх staging-файлов;
- изолированный localhost API: два одинаковых синтетических `/start` события вернули одинаковый `200 BOT_MESSAGE` без вызова LLM, Jivo или legacy Telegram;
- legacy `novostroy-bot-staging.service` не используется как release gate Jivo.

Это не production deploy и не Jivo end-to-end проверка. Перед переносом в production нужны отдельные Jivo staging/dialog/regression evidence и явное подтверждение на deploy.

## Historical: внешний HTTP smoke staging

После localhost-проверки был кратковременно открыт отдельный staging-only HTTP listener на VPS. Один synthetic `/start` прошёл путь `external HTTP -> staging bridge -> staging API -> mock receiver` и дал один финальный `BOT_MESSAGE`. Повтор того же event-id был подавлен bridge-слоем и не создал второй outbound message.

Listener после проверки остановлен; production API и bridge не менялись. Это доказывает доступность внешнего HTTP-транспорта и duplicate suppression, но не является Jivo E2E: Jivo требует публичный HTTPS endpoint и provider configuration.

## Historical: P1 production rollout

На 2026-07-16 P1 перенесён в production после staging-проверок:

- API использует per-session serialization и bounded event-id response dedup;
- bridge использует bounded duplicate suppression перед повторной отправкой финального `BOT_MESSAGE`;
- TTL обоих защитных кэшей — 600 секунд, лимит bridge-кэша — 1024 записи;
- rollback-бэкап: `/home/neiro/novostroy-bot/backups/p1-dedup-20260716-104338/`.

После рестарта `novostroy-bot-api.service` и `novostroy-bot-n8n-bridge.service` оба health-check прошли. Два одинаковых локальных synthetic `/start` события вернули равные `200 BOT_MESSAGE` без вызова LLM, n8n или Jivo; bridge guard статически подтвердил один dispatch на event-id.

Это подтверждает защиту от повторной обработки, но не подтверждает исходный UX-симптом: реальный Jivo delivery и отображение финального ответа клиенту не запускались и требуют коррелированного диалога.

## Current search/callback status (2026-07-16)

- В search-пути exact `facts` теперь отделены от `near`: primary shortlist строится только из exact facts, `near` остаются альтернативами.
- Callback-сценарий реализован как `name + phone -> private callback outbox -> summary worker -> Google Sheets`; для него больше не используется автоматический `INVITE_AGENT`.
- В live Jivo-проверке transport-цепочка уже показывает `status_update` при долгом поиске и финальный `BOT_MESSAGE` приходит автоматически; это отдельный транспортный слой, не баг.
- Оставшийся известный live caveat — семантическая нормализация района/метро для отдельных вариантов (например, когда объект связан с одной локацией, а административный район в карточке подписан иначе). Это проблема content/normalizer слоя, а не transport.

## Historical: Four-layer validator rollout

Целевая поисковая цепочка состоит из четырёх логических слоёв:

```text
Planner LLM -> MCP/Search -> deterministic Validator -> Presenter LLM
```

Роли разделены так:

- Planner выбирает typed action, intent, target и ограничения;
- MCP/Search возвращает структурированные `facts`, `near`, `missing`, `params`;
- Validator кодом определяет `matched`, `near`, `rejected`, `unknown`, hard-constraint failures и `do_not_say`;
- Presenter пишет клиентский текст только из безопасного `DecisionContext`.

Текущий production-режим: `NMBOT_FOUR_LAYER_RUNTIME=1` и
`NMBOT_FOUR_LAYER_ENFORCE=0`. Это **shadow mode**: validator считает только безопасные агрегаты (`facts/near/matched/rejected/unknown/status`), но legacy `main_answer` продолжает формировать ответ. Поэтому включение shadow mode не меняет UX клиента.

Статусы shadow-проверки включают `ok`, `no_exact_matches` и консервативные fallback-статусы при отсутствии или неполноте structured facts. Shadow-диагностика не должна печатать raw payload, текст клиента, названия/ID вариантов, телефоны, токены или Authorization.

Restricted Presenter (`NMBOT_FOUR_LAYER_ENFORCE=1`) пока не считается production-ready: перед его включением нужны отдельные Jivo-регрессии и подтверждение, что hard constraints, unsupported claims, no-match и current-options follow-up проходят глазами клиента.

## Historical: LLM-first уточнение после поиска

На 2026-07-16 search-контракт расширен двумя полями: `action` (`search` или
`clarify`) и `clarification_question`. Search LLM по-прежнему сначала вызывает
MCP. Если после поиска она считает условий недостаточными для полезного
shortlist, то возвращает только `action="clarify"` и один короткий вопрос без
фактов, цен, вариантов или раннего предложения оператора.

`OvermindClient.ask()` принимает только валидный `clarify`: непустую строку не
длиннее 300 символов. Тогда вопрос LLM уходит клиенту напрямую, а fallback-race
и chat-stage не запускаются. Неизвестный или невалидный action сохраняет
прежний путь обработки.

Production rollout затронул только `prompts/search_v1.txt` и
`scripts/chat_tester_bot.py`; rollback-бэкап:
`/home/neiro/novostroy-bot/backups/deploy-llm-clarify-20260716-112711/`.
После рестарта `novostroy-bot-api.service` health-check прошёл. Реальный Jivo
повтор широкого запроса после `/start` показал клиенту уточняющий вопрос без
второго сообщения клиента; trace завершился
`accepted_async -> upstream -> sent` (upstream 11 086 мс). Это подтверждает
данный сценарий, но не заменяет регрессионные проверки для других типов поиска.

## Глубокая диагностика одного диалога

Для разбора безопасной цепочки конкретного Jivo turn используйте локальный
read-only инструмент:

```bash
python3 scripts/nmbot_jivo_dialogue_diagnose.py \
  /path/to/jivo_delivery_trace.jsonl --trace <trace_ref> --strict
```

Для legacy `n8n_bridge_structured.jsonl` используйте тот же инструмент только
без `--strict`: этот журнал полезен для технического контекста, но не доказывает
полную delivery lifecycle.

Для агрегированной runtime-сводки только по `dialogue_journal.jsonl`, без bridge log:

```bash
python3 scripts/nmbot_jivo_dialogue_diagnose.py \
  --audit-log logs/dialogue_journal.jsonl --audit-only --last 20
```

Опциональный `--audit-log /path/to/sanitized_turn_audit.jsonl` обогащает вывод
только разрешёнными полями: анонимные refs, intent, факт поиска и число
результатов, handoff, terminal event, latency, безопасная phone-мета
(`phone_detected`, длина, last4/ref) и вложенный `runtime_summary` по allowlist
выше. Текст, полный телефон, payload, token, URL, client/chat id, prompt,
provider response и названия карточек инструмент не выводит.

Диагноз показывает `Actual / Contract / Desired`, этап (`delivery_complete`,
`upstream_failure`, `delivery_missing`, `api_safe_fallback`, `main_search`,
`main_search_clarify`, `operator_handoff`, `phone_captured`, `chat_closed` или
`coverage_gap`) и следующий проверочный шаг. `accepted_async` — нормальный
transport-ack и не является ошибкой сам по себе. Без явного Desired инструмент
не называет расхождение багом; при успешной доставке без связанного audit-event
он сообщает о пробеле наблюдаемости.
