# NMBot V1 — техническое задание новой версии бота

Дата: 2026-07-28  
Статус: **approved; Stages A-D completed for isolated TEST; Stage E not started**  
Область: новая независимая runtime-версия `V1` для Jivo/API.

## 1. Главное определение

`V1` — новая версия NMBot, равноправная `V0`, `V2` и `V3`.

Это не:

- Documentation V1 Lite из `docs/DOCUMENTATION_V1_TZ.md`;
- старый Telegram runtime `scripts/chat_tester_bot.py`;
- режим или переименование V0/V2/V3;
- оболочка над V2 с другим клиентским именем;
- разрешение менять поведение существующих версий.

V1 получает собственные runtime-контракты, namespace состояния, prompts,
fixtures, quality baseline и release gate. Общую инфраструктуру можно
переиспользовать только там, где она не владеет семантикой диалога.

## 2. Цель продукта

V1 должна вести короткий живой диалог о новостройках Москвы и Московской
области:

1. понять свободный запрос клиента, включая опечатки;
2. дать полезный ответ из подтверждённых MCP-фактов;
3. показать не более трёх подходящих вариантов;
4. помнить условия и уже показанные варианты;
5. отвечать по текущему списку или выбранному объекту без лишнего поиска;
6. честно отделять точные варианты от близких;
7. после выбора ЖК перейти к конкретным лотам, если evidence это позволяет;
8. предложить оператора только после собственной пользы или по прямой просьбе;
9. передать оператору безопасный контекст и подтверждённый контакт.

Короткая продуктовая формула:

```text
сначала польза → затем выбор/уточнение → затем конкретный объект/лот
→ только потом оператор, когда нужен человек
```

Клиентское имя V1: **Татьяна**.
Технический идентификатор всегда остаётся `V1`.

## 3. Непереговорные границы

1. Не изменять семантику, state, prompts, fixtures и baseline V0/V2/V3.
2. Не импортировать runtime, state или planner из `nmbot_v0` и `nmbot_v2`.
3. Не использовать `scripts/chat_tester_bot.py` как engine новой V1.
4. Не делать regex/набор `if` главным распознавателем живого языка.
5. Не позволять LLM менять state, выбирать endpoint, ослаблять hard-фильтры или
   подтверждать факты без evidence.
6. Не передавать Presenter сырой provider/MCP payload.
7. Не публиковать model-generated ответ без схемной и механической проверки.
8. Не считать локальные tests, docs или selector file доказательством production.
9. Не менять production selector и не запускать deploy/eval без отдельного
   разрешения.
10. Любое изменение общего transport/selector/search boundary обязано доказать,
    что V0/V2/V3 остались неизменными.

## 4. Что переиспользуем

### 4.1. Переиспользуем как общую инфраструктуру

| Компонент | Решение для V1 | Условие |
|---|---|---|
| Jivo webhook, bridge и API transport | reuse as-is | Вход остаётся `CLIENT_MESSAGE`, терминальный выход — один `BOT_MESSAGE` |
| Per-session lock и deduplication | reuse as-is | V1-turn выбирается до runtime и сериализуется тем же transport layer |
| Runtime selector и protected version API | additive reuse | Добавляется `V1`; fallback неизвестной версии не меняется |
| Callback private outbox и Sheets worker | reuse through existing contract | V1 передаёт только подтверждённый consent и privacy-safe context |
| Dialogue/error journals | additive reuse | Каждая строка получает effective `runtime_version=V1` и V1 execution path |
| Release identity и atomic release tooling | reuse as-is | Artifact включает V1-owned files; release ID не считается quality proof |
| Local check dispatcher | additive reuse | Появляется отдельный scope `v1`; старые scopes сохраняются |

### 4.2. Переиспользуем как проверенный принцип, но реализуем в V1 заново

| Проверенный принцип | Новая V1-owned реализация |
|---|---|
| Typed immutable state + accepted delta | `nmbot_v1/state.py` |
| Planner владеет смыслом, код — переходом | `nmbot_v1/planner.py`, `nmbot_v1/transition.py` |
| Port interfaces для model/search/operator/journal | `nmbot_v1/ports.py` |
| Strict facts/near/missing contract | `nmbot_v1/search_contract.py` |
| Exact-first shortlist, near only as labelled fallback | V1 search validator and shortlist policy |
| Response plan before client prose | `nmbot_v1/response.py` |
| Model wording with deterministic safe fallback | V1 Presenter boundary |
| Prompt SHA/set provenance | `nmbot_v1/prompt_provenance.py` |
| Selected ЖК → bounded lot enrichment | V1-owned stage/state/lot contracts |

Копировать существующий код целиком запрещено. Допустимо перенести небольшой
доказанно общий алгоритм с указанием источника, новым V1 API и собственными
tests. После переноса V1 владеет своей копией и не зависит от будущих изменений
V0/V2/V3.

### 4.3. Не переиспользуем

- `nmbot_v0/*` runtime и prompts;
- `nmbot_v2/*` runtime, contracts, state, planner, transition и response recipes;
- существующий `nmbot_v2` state namespace;
- V0/V2/V3 composer modes и client identities;
- Telegram handlers и Telegram release evidence;
- исторические quality scores как baseline V1;
- evaluator prompts как production prompts.

## 5. Целевая архитектура V1

```text
Jivo CLIENT_MESSAGE
  → shared transport: auth → normalize → lock → dedup → selector
  → V1 adapter
  → V1 Semantic Planner
  → typed V1IntentPlan validation
  → deterministic V1 Transition/Reducer
  → V1 Evidence Executor
       → MCP request builder
       → strict response parser
       → hard-evidence validator
       → exact / near / missing classification
       → bounded enrichment when required
  → V1 ResponsePlan
  → V1 Presenter from allowed material only
  → mechanical output validator
  → validated prose or deterministic safe fallback
  → state commit only after accepted execution
  → journal attribution
  → one terminal Jivo BOT_MESSAGE
```

Обычный полезный поиск целится в два model calls и один MCP path:

1. Semantic Planner;
2. MCP-backed search/evidence;
3. Presenter.

Enrichment разрешён только как bounded дополнительный evidence step для
выбранного ЖК, лота или отсутствующего обязательного hard evidence.

## 6. Владение решений по слоям

| Решение | Единственный владелец | Что ему запрещено |
|---|---|---|
| Смысл реплики | V1 Semantic Planner | менять state, вызывать MCP, писать ответ |
| Допустимость перехода | V1 Transition/Reducer | додумывать intent или факты |
| Параметры поиска | V1 Search Request Builder | брать новые hard-поля из догадки модели |
| Фактическое соответствие | V1 Evidence Validator | молча ослаблять условия клиента |
| State mutation | V1 State Reducer | применять delta после rejected/failed turn |
| Состав ответа, cards и CTA | V1 ResponsePlan | писать свободную клиентскую прозу |
| Живой язык | V1 Presenter | менять route, факты, порядок cards или CTA |
| Передача человеку | V1 Handoff Policy + existing outbox | принимать consent по догадке |

## 7. V1 contracts

### 7.1. `V1IntentPlan`

Минимальные поля:

```text
schema_version = 1
goal
viewpoint
constraints_delta {hard, preferences}
selected_option_ref?
selected_lot_ref?
requested_facts[]
operator_intent = none | request | accept | decline
clarification?
confidence
```

Planner возвращает ровно один goal. Неизвестные поля запрещены. Low-confidence
опасное действие превращается в один короткий уточняющий вопрос.

### 7.2. Stages

Минимальный набор:

- `reset`;
- `first_search`;
- `refine_search`;
- `expand_search`;
- `current_options`;
- `selected_project`;
- `selected_lot_search`;
- `selected_lot`;
- `fact_check`;
- `operator_offer`;
- `contact_name`;
- `contact_phone`;
- `operator_declined`;
- `off_topic`;
- `safe_error`.

Каждый stage обязан быть наблюдаемым в trace. Stage и client answer kind — разные
поля.

### 7.3. `V1ConversationState`

Namespace в session envelope: только `nmbot_v1`.

Минимальное состояние:

```text
schema_version
revision
stage
hard_constraints
preferences
active_viewpoint
visible_options
previous_option_refs
selected_project
selected_lot
last_search_summary
pending_action
already_asked
answered_facts
operator_offered
operator_declined
contact_consent
contact_name
contact_phone_redacted
callback_ref
recent_safe_turns
```

Правила:

- `/start_1` сбрасывает только `nmbot_v1`;
- переключение на другую версию не мигрирует и не смешивает state;
- raw MCP/provider payload, tokens и полный телефон в state не сохраняются;
- failed/rejected execution не меняет бизнес-state;
- single-process V1 использует существующий per-session lock и atomic file
  replacement; multi-worker promotion блокируется до отдельного CAS-контракта.

### 7.4. Search/evidence

V1 использует собственные schema, types и validator.

Hard rules:

1. `effective_hard` строится кодом из подтверждённого plan/state.
2. Natural user text помогает понять цель, но не создаёт новые hard constraints
   внутри search model.
3. `facts` содержат только варианты с подтверждённым hard evidence.
4. Не прошедшие hard validation варианты могут стать только явно обозначенными
   `near` или быть отброшены.
5. `near` не смешивается с `facts`, если точные варианты есть.
6. Ослабление одного условия допускается только как отдельное объяснённое
   действие, а не как скрытая модельная догадка.
7. Parse/validation failure не равен «вариантов нет».
8. Один end-to-end search deadline ограничивает primary, fallback, supplement и
   enrichment; каждая попытка получает остаток общего бюджета.

Per-turn search input остаётся компактным:

```text
stable V1 search system contract
+ short typed envelope
+ current effective constraints
+ redacted natural client query
```

### 7.5. ResponsePlan и Presenter

`V1ResponsePlan` фиксирует:

- answer kind;
- до трёх exact cards или до двух labelled near cards;
- допустимые буквальные факты и вычислимые сравнения;
- missing-fact boundary;
- один CTA/следующий вопрос;
- operator eligibility;
- deterministic fallback text.

Presenter получает только V1ResponsePlan и короткий safe conversation context.
Он может менять формулировку, но не facts, card order, match class, цифры, CTA или
operator decision.

Mode:

```text
NMBOT_V1_PRESENTER_MODE=off|shadow|publish
```

Default до V1 quality baseline: `off`. Переход `shadow → publish` требует
отдельного review и Jivo gate. Любая ошибка Presenter сохраняет уже построенный
deterministic fallback.

### 7.6. Operator handoff

Оператор предлагается, когда:

- клиент прямо попросил человека;
- нужен live fact: актуальное наличие, бронь, конкретная квартира, показ или
  неподтверждённые динамические условия;
- V1 уже дала доступную пользу, но evidence закончился.

После согласия порядок фиксирован:

```text
consent → name → phone → private callback outbox → confirmation
```

Передаются только исходная задача, подтверждённые условия, выбранный ЖК/лот,
запрошенный live fact и privacy-safe references.

## 8. Prompts V1

Новые файлы живут отдельно:

```text
prompts/v1/intent_planner.txt
prompts/v1/search_mcp.txt
prompts/v1/presenter.txt
```

При необходимости позже добавляются `prompts/v1/scenarios/` и
`prompts/v1/facets/`, но только когда fixtures докажут необходимость. Пустую
иерархию заранее не создаём.

Каждый prompt обязан определить:

```text
Purpose → Inputs → Output schema → Priority rules
→ Forbidden claims → Owner layer → Validation
```

Существующие flat-файлы `prompts/*_v1.txt` не считаются prompts новой runtime V1:
суффикс в их имени исторически обозначает ревизию prompt. V1 runtime использует
только новый directory и фиксирует точные SHA-256 identities.

## 9. Selector и transport integration

Общие файлы получают только аддитивные изменения:

1. `V1` добавляется в supported runtime identity registry.
2. `scripts/nmbot_runtime_adapter.py` получает отдельную ветку `_run_v1_*`.
3. `scripts/nmbot_api_server.py` принимает protected global `V1` и `/start_1`
   там, где session overrides уже разрешены.
4. Client-production policy для per-session commands не ослабляется.
5. Journal/report/diagnostic validators принимают `V1`.
6. Unknown/malformed version продолжает использовать текущий безопасный fallback;
   добавление V1 не меняет fallback старых версий.

Обязательная regression-инварианта:

```text
тот же input + V0/V2/V3 selector
→ тот же owner runtime, namespace, public identity и version attribution,
как до добавления V1
```

## 10. План файлов

### Новые V1-owned files

```text
nmbot_v1/__init__.py
nmbot_v1/contracts.py
nmbot_v1/state.py
nmbot_v1/ports.py
nmbot_v1/planner.py
nmbot_v1/transition.py
nmbot_v1/search_contract.py
nmbot_v1/search.py
nmbot_v1/response.py
nmbot_v1/runtime.py
nmbot_v1/prompt_provenance.py
prompts/v1/intent_planner.txt
prompts/v1/search_mcp.txt
prompts/v1/presenter.txt
tests/test_nmbot_v1_contracts.py
tests/test_nmbot_v1_state.py
tests/test_nmbot_v1_runtime.py
tests/test_nmbot_v1_search.py
tests/test_nmbot_v1_quality.py
tests/fixtures/nmbot_v1_quality_scenarios.json
docs/NMBOT_V1_QUALITY_BASELINE.md
```

### Shared additive consumers

Перед implementation каждый путь подтверждается повторным impact-map чтением.
Ожидаемые владельцы:

- `scripts/nmbot_runtime_adapter.py`;
- `scripts/nmbot_api_server.py`;
- `scripts/dialogue_journal.py`;
- `scripts/nmbot_dialogue_report.py`;
- `scripts/backfill_dialogue_runtime_versions.py`;
- `scripts/nmbot.py`;
- client-production preflight/runtime validators;
- `tests/nmbot_check_manifest.yaml`;
- focused selector/API/callback/journal/release tests;
- runtime registry/version/operations docs.

Исторические release bundles, archives и old journal rows не переписываются.

## 11. Реализация по этапам

### Stage A — V1 core, полностью локально

- статус на 2026-07-28: реализовано локально; focused tests `14 passed`;
  ordinary review `ses_05794f6ceffe5NxYwrAtVHTxeC` — `pass`, risk `low`;
- V1 types, state, planner port, transition, search contract, response plan;
- fake ports и deterministic fixtures;
- ни одного общего runtime-файла не менять;
- результат: V1 engine проходит собственные tests вне selector/Jivo.

### Stage B — additive selector integration

- статус на 2026-07-28: реализовано и проверено локально;
- `python3 scripts/nmbot_check.py v1` — `39 passed`;
- ordinary review `ses_0577841a0ffef5icFhBUTLmRME` — `pass`, risk `low`,
  critical/high/medium findings отсутствуют;
- отдельная V1 adapter branch;
- `V1`, `/start_1`, namespace reset и attribution;
- regression V0/V2/V3;
- новый offline scope `python3 scripts/nmbot_check.py v1`.

### Stage C — quality baseline

- статус на 2026-07-28: реализовано и проверено локально; Stage D завершён
  только для isolated TEST;
- final fixture: 15 records, 16 required classes, SHA-256
  `d434e9e9b70965856a4bae55a955ad5edf0914d4bf65861cae7b772d8296bae1`;
- closed schema + canonical `V1IntentPlan`/`V1OptionCard` validation; malformed
  read/UTF-8/JSON/schema returns bounded JSON without traceback;
- manual language replay was inspected and fixed at the V1 `ResponsePlan`/render
  boundary and fixture evidence layer, not with regex/runtime mega-if branches;
- final checks: focused runtime+quality `15 passed`; direct replay `15/15`;
  `python3 scripts/nmbot_check.py v1` compile + `46 passed` + replay; broad
  adapter/API `214 passed`;
- reviews: `ses_0575b6a62ffeLxvSXfG3LlWFgo` pass_with_findings;
  `ses_057555663ffeuQWxuqKBDjJP2h` pass/low; final
  `ses_0574c6311ffeDMQa4nlYBNhNuC` pass/low/no material findings;
- limitations remain: no repeat filtering claim, no standalone prompt-injection
  classifier claim, and fact-check without evidence only returns an honest absence
  boundary;
- минимум сценариев: first search, refinement, expand without repeats, current
  options, selected ЖК, lot funnel, fact check, exact/near, empty result,
  operator accept/decline, contact, off-topic, prompt injection, provider error;
- ручная проверка живого языка;
- собственный baseline V1, не сравниваемый с baseline других версий.

### Stage D — isolated TEST rollout

- статус на 2026-07-28: завершено только для isolated TEST; это не
  client-production readiness;
- fresh TEST snapshot `vps-source-20260728-131932-407f9ac372ee`, manifest
  SHA-256 `352e8b8b50fb4321d65410f70b1312d93abd06143933f50b0486110bb294f04c`;
- immutable release `nmbot-v1-stage-d-test-20260728-190000`; local preflight:
  `87 files`, `59 py_compile`, `16 import modules`;
- pre-deploy review `ses_056b5eee1ffey1J8GqgyRayGlA`: нет
  critical/high/medium blockers; low finding — readable journal ещё подписывает
  bot turns как `Ирина`, canonical JSONL при этом хранит `runtime_version=V1`;
- TEST deploy и fresh recon прошли: health reachable/ok, current symlink,
  release identity и canonical systemd guards совпали с release;
- выполнен ровно один synthetic Jivo `/start_1`: HTTP `200`, один terminal
  `BOT_MESSAGE`, приветствие `Татьяна`, effective `V1`, latency `129 ms`;
- canonical JSONL correlation `event_id_ref=sha256:9b486bcf4544467b`:
  user turn `V1` + bot lifecycle `start_reset`, `V1`;
- Presenter остался `off`; model call, client-production switch, eval и второй
  Jivo request не выполнялись;
- project evidence note: NotebookLM `16a859d501bc`.

### Stage E — publish decision

Только отдельным approval:

- Presenter `publish` candidate;
- full V1 regression + unchanged V0/V2/V3 gates;
- fresh selector, health, prompt/release identity and terminal Jivo evidence;
- explicit rollback to previous selector/release.

## 12. Обязательные проверки

`nmbot check v1` должен быть local, offline, no-model, no-network и включать:

1. compile V1-owned modules and shared adapter;
2. contracts/state/transition tests;
3. strict search parser and hard-evidence tests;
4. response plan/presenter validation tests;
5. adapter/selector `/start_1` tests;
6. journal, callback and privacy tests;
7. deterministic V1 quality fixture replay.

Общий runtime scope дополнительно доказывает неизменность V0/V2/V3 routes.

Eval/promptfoo не входит в автоматический gate и запускается только после личного
разрешения пользователя.

## 13. Acceptance criteria V1

V1 готова к isolated TEST, когда:

1. существует отдельный `nmbot_v1` без runtime imports из V0/V2;
2. V1 использует только `nmbot_v1` state namespace;
3. selector route `V1` не меняет V0/V2/V3 routing;
4. `/start_1` сбрасывает только V1 state;
5. every turn имеет V1 stage/action/answer-kind/execution-path attribution;
6. failed provider/parse/validation turn не портит state;
7. hard mismatch не публикуется как exact fact;
8. near ясно отличается от exact;
9. первый ответ содержит максимум три варианта и один следующий вопрос;
10. follow-up использует memory и не перезапускает подбор без причины;
11. selected project не заменяется широким поиском;
12. dynamic availability/booking не подтверждается без live evidence;
13. operator handoff требует consent и сохраняет privacy-safe context;
14. prompt/provider/internal fields не видны клиенту;
15. deterministic fallback остаётся человеческим и терминальным;
16. V1 имеет отдельные fixtures и quality baseline;
17. `nmbot check v1` и общий runtime regression зелёные;
18. isolated TEST Jivo trace подтверждает effective V1 и один terminal
    `BOT_MESSAGE`.

Client-production готовность требует отдельного решения и свежего live gate.

## 14. Stop conditions

Работа останавливается, если:

- реализация требует менять семантику V0/V2/V3;
- V1 начинает импортировать их runtime/state/planner;
- shared change не имеет regression для всех consumers;
- parser/validator не отличает технический failure от empty inventory;
- нет собственного V1 baseline;
- первый TEST request завершился ошибкой;
- live evidence нельзя связать с exact release, prompt set и effective V1;
- для продолжения требуется неутверждённый deploy, eval или production switch.

## 15. Решения, которые нужны до prompt/rollout

Не блокируют создание core contracts, но должны быть утверждены до публичного
поведения:

1. допустимый target latency первого поиска и follow-up;
2. provider/model candidates для Planner, Search и Presenter;
3. начальный набор client-facing viewpoints/scenarios;
4. Presenter rollout policy после `off`: нужен ли обязательный `shadow`;
5. разрешён ли `/start_1` только на TEST или также в обычном non-client contour.

## 16. Источники проекта

- Version separation/shared boundary: `docs/NMBOT_RUNTIME_VERSIONS.md:1-25,126-168`.
- Product funnel and UX: `docs/IDEAL_IRINA_UX.md:17-45,63-178,193-247`.
- Architecture ownership: `docs/NMBOT_PROJECT_SIMPLIFICATION_PLAN.md:31-43,62-71`.
- Jivo/callback/journal contracts: `docs/NMBOT_EXTERNAL_CONTRACTS.md:5-15`.
- Scenario/evidence rules: `docs/SCENARIO_MCP_CONTRACT.md:13-22,42-57,81-90`.
- Compact MCP prompt boundary: `docs/NMBOT_V2_MCP_PROMPT_BUILD_RULES.md:25-50,71-129`.
- Selected project/lot funnel: `docs/NMBOT_SELECTED_ZHK_LOT_FUNNEL.md:21-59,61-147`.
- Prompt identity: `docs/NMBOT_PROMPT_PROVENANCE.md:7-32,51-56`.
- Existing selector route: `scripts/nmbot_runtime_adapter.py:265-306`.
- Current version literals/commands: `scripts/nmbot_api_server.py:642-665,2311-2334`.
