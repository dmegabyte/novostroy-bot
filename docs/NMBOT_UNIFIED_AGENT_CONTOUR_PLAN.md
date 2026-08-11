# NMBot — план единого agent-контура для всех обычных сообщений

Дата: 2026-08-07
Статус: local implementation; production этим документом не изменён
Первый кандидат для пилота: V5 поверх существующего V2 engine

## 1. Решение

Нужен один наблюдаемый контур, через который проходят все **обычные диалоговые
реплики**: новый поиск, уточнение, вопрос по текущим вариантам, согласие на
следующий шаг и восстановление непонятного диалога.

Нельзя делать production-входом `scripts/nmbot_legacy_prompt_quality.py`. Это
диагностический harness: он не владеет state, callback, Jivo delivery и
production fallback. Его `--request-mcp-evidence` изменяет prompt до MCP-вызова
и уже наблюдаемо менял tool arguments (`only_with_flats=true`).

Целевой путь:

```text
Jivo/API message
  → code-owned ingress safeguards
  → safe UnifiedTurnInput
  → UnifiedAgentPort
      → Overmind gateway-agent → OpenRouter
      → MCP novostroym, только когда search_policy=required
  → strict UnifiedTurnOutput validator
  → code-owned reducer/state commit
  → code-owned delivery or deterministic fallback
```

Единый контур означает один контракт и один transport owner, а не передачу
модели всех полномочий.

## 2. Actual / Contract / Desired

### Actual

- V2/V3/V5 имеют typed planner/state/search/ResponsePlan и deterministic
  fallback.
- Отдельные answer-provider adapters, включая Bluesminds, относятся только к
  формированию ответа и не являются unified/search transport.
- V1 уже имеет one-model final-response candidate с `off|shadow|publish`,
  строгой проверкой и обходом terminal contact flow.
- V4 уже имеет isolated one-prompt gateway request и strict JSON validator, но
  его контракт `{data,message}` слишком узок для полного диалога и не даёт
  validator-у raw MCP proof.
- Prompt-quality contour умеет подставлять любой prompt и возвращать task ID,
  но остаётся диагностикой, а не runtime.

### Contract

- Модель не коммитит state, не пишет callback/outbox, не принимает телефон и не
  управляет Jivo delivery.
- Search facts приходят только из MCP/evidence. Model memory не является
  источником фактов.
- Любой model/provider результат проходит strict parsing и validation.
- При timeout, empty, invalid JSON, unsupported fact или provider error
  публикуется существующий deterministic fallback.
- Режимы публикации: `off → shadow → publish`; неизвестное значение fail closed.
- Production-доказательство требует immutable release и correlated Jivo trace.

### Desired

- Все обычные сообщения текущего runtime проходят через один
  `UnifiedAgentPort`.
- Один отчёт показывает payload stage, task ID, transport/agent/upstream/model,
  fallback и безопасный MCP trace.
- Модель внутри OpenRouter можно менять отдельной конфигурацией; transport
  первого пилота остаётся Gateway-only.
- Старый runtime остаётся мгновенным rollback-путём.

## 3. Что переиспользуем

| Наработка | Что берём | Что не переносим как есть |
|---|---|---|
| V1 one-model | safe input, strict candidate validator, `off|shadow|publish`, terminal bypass | отдельный V1 state/response contract |
| V4 one-prompt | single gateway task, fail-closed parsing, task/model trace | узкий `{data,message}` и отсутствие raw MCP proof |
| V2/V3/V5 | typed state, canonical cards, search/evidence, reducer, deterministic fallback | дублирующие model-owned решения |
| Existing answer-provider adapters | принципы bounded metadata и deterministic fallback | Bluesminds как unified/search transport |
| Prompt-quality contour | arbitrary prompt, prompt SHA, task ID, local diagnostic UX | diagnostic suffix как runtime-инструкцию |

## 4. Граница полномочий

### Unified agent может предложить

- semantic action;
- новый поиск или ответ по текущим вариантам;
- hard constraints и requested facts;
- один короткий клиентский ответ;
- visible option references только из validated evidence;
- следующий нетерминальный шаг.

### Только код может

- принять/reject предложенный plan;
- выбрать реальный MCP route и проверить его результат;
- применить state transition;
- подтвердить наличие квартиры;
- принять имя/телефон и записать callback/outbox;
- выполнить terminal operator handoff;
- отправить `BOT_MESSAGE`;
- решить, публиковать candidate или fallback.

Телефон, callback и уже начатый terminal operator flow обходят unified model и
обрабатываются существующим code-owned путём. Это сознательное исключение из
слова «все».

## 5. Предлагаемый контракт

### 5.1. Вход `UnifiedTurnInput`

```json
{
  "schema_version": 1,
  "message": "bounded client text",
  "runtime_version": "V5",
  "state_summary": {},
  "previous_assistant_message": "",
  "current_options": [],
  "selected_object": null,
  "pending_action": null,
  "allowed_actions": [],
  "safety_policy": {},
  "evidence_policy": {}
}
```

В input не передаются secrets, raw Jivo metadata, контакты, raw MCP payload или
неограниченный transcript.

### 5.2. Выход `UnifiedTurnOutput`

```json
{
  "schema_version": 1,
  "action": "search|answer_current_options|clarify|operator_contact|recover_dialogue",
  "target": "new_search|current_options|none",
  "search_policy": "required|forbidden",
  "constraints": {},
  "requested_facts": [],
  "response": "",
  "visible_option_refs": [],
  "next_action": "none|inspect_option|clarify_search|offer_operator"
}
```

Это proposal, не state patch. Reducer принимает только allowlisted поля и сам
строит canonical transition. Search facts и lot evidence не должны копироваться
из model-authored output без отдельной MCP validation boundary.

## 6. Transport и MCP capabilities

Bluesminds — отдельный answer-provider и не является gateway-agent или
search/MCP transport. Первый unified pilot не имеет provider selector.

| Capability | gateway/OpenRouter | прямой Bluesminds |
|---|---|---|
| обычный model answer | да | да |
| `novostroym` tool cycle | текущий canonical путь | не доказано |
| gateway task ID | да | нет gateway task ID; нужен provider request ID |
| authoritative MCP trace | требует gateway/Overmind поддержки | невозможно без отдельного MCP bridge |

Поэтому первый unified pilot использует только Overmind `gateway-agent` с
upstream `openrouter`. Прямой provider допускается для search-required turn
только после отдельного source-backed MCP-capable adapter contract. Bluesminds
остаётся в существующих response-only границах и этим пилотом не вызывается.

## 7. Конфигурация пилота

Чтобы не смешивать версии, сначала нужен V5-specific переключатель:

```text
NMBOT_V5_UNIFIED_AGENT_MODE=off|shadow|publish
```

- `off`: существующий V5 путь без новых вызовов;
- `shadow`: unified agent вызывается для обычных turns, но клиент получает старый
  ответ; state не меняется по candidate;
- `publish`: candidate публикуется только после validation и reducer acceptance;
- неизвестное значение режима: `off`;
- answer-provider selectors не должны неявно включать unified contour.

После подтверждённого V5-пилота можно решить, нужен ли version-neutral selector.

### Реализованная первая фаза: V5 single-agent shadow

В isolated source snapshot реализован отдельный fail-closed переключатель:

```text
NMBOT_V5_SINGLE_AGENT_MODE=off|shadow
```

- `off`, неизвестное значение и преждевременный `publish` не создают нового
  gateway task;
- `shadow` после code-owned contact capture отправляет обычную V5-реплику в
  один Gateway/OpenRouter/MCP-capable candidate;
- candidate использует `prompts/v5_single_agent.txt`: это сохранённый
  `prompts/search_v1.txt` с финальным правилом `Выведи только ЖК с квартирами в
  продаже`;
- dynamic input содержит только bounded/redacted client message, предыдущий
  ответ, до трёх canonical visible names, selected option и safe pending/offer
  tokens;
- результат проходит отдельный strict legacy-action validator и попадает только
  в safe trace metadata; raw prompt, query и model output клиенту не выдаются;
- старый V5 planner/search остаётся единственным владельцем ответа и state в
  shadow. Поэтому эта фаза измеряет расхождения, но ещё не устраняет planner из
  публичного пути.

Переход к `publish` намеренно не реализован. До него нужно доказать
authoritative MCP tool evidence, определить reducer mapping для `params` и
current-options refs и подтвердить, что invalid candidate всегда возвращает
старый V5 fallback без изменения callback/state/delivery.

## 8. Observability

Каждый agent attempt должен дать безопасный отчёт:

```json
{
  "contour": "unified_agent",
  "mode": "shadow",
  "payload_stage": "v5_unified_turn",
  "task_id": "gateway-task-id",
  "provider_meta": {
    "transport": "gateway",
    "agent": "gateway-agent",
    "upstream_provider": "openrouter",
    "fallback": false
  },
  "model": "model-id",
  "prompt_provenance": {"source": "...", "sha256": "..."},
  "validation": "accepted|rejected|fallback",
  "fallback_reason": null
}
```

Для MCP нужны реальные tool name/arguments/result metadata из
gateway/Overmind trace. Model-authored `mcp_audit` не является доказательством.
SQL не реконструируется. Secrets, raw prompt, телефон и полный provider payload
в отчёт не попадают.

### V5 search evidence switch

Для V5 предусмотрен отдельный fail-closed переключатель:

```bash
NMBOT_V5_SEARCH_MCP_EVIDENCE=off  # default
NMBOT_V5_SEARCH_MCP_EVIDENCE=on   # также true/1
```

При `on` к фактическому V5 search prompt добавляется диагностический addendum.
Он просит модель вернуть необязательный `mcp_audit`, чтобы проверить, какие
аргументы и признаки наличия квартир она видела. Это может повлиять на выбор
MCP-инструмента и сузить поиск, поэтому режим не является нейтральным
наблюдением и по умолчанию выключен. V2/V3 этот addendum не получают.

В addendum дословно закреплено согласованное условие проектной цены:
`n.price_mod="def" and (n.price1>0 or n.price2>0 or n.price3>0 or n.price4>0 or n.price_n>0 or n.price_s>0)`.
Оно само по себе не доказывает наличие конкретной квартиры; для shortlist
нужны активные объявления с подтверждёнными `ads.id`, `ads.state=2` и
`ads.status=2`.

`mcp_audit` извлекается до нормализации search-ответа, bounded/sanitized и
пишется во внутренний INFO-log как событие `v5_mcp_evidence_audit`. Он не
попадает в `facts`, `near`, `missing`, `params`, публичный `answer` или
client-visible `meta.trace`. При отсутствии audit пишется только безопасный
маркер `audit_missing=true`. SQL не сохраняется и не реконструируется.

Audit остаётся model-authored диагностикой: он не доказывает фактический
MCP/Overmind tool trace, выполнение SQL или наличие активного объявления.

## 9. Фазы реализации

### Фаза 0 — заморозить baseline

1. Зафиксировать набор turn-классов: search, current options, clarification,
   selected object, operator offer/consent, recover dialogue.
2. Сохранить текущие deterministic outputs/state transitions как fixtures.
3. Зафиксировать latency, gateway calls и fallback rate текущего V5.

Выход: baseline позволяет доказать, что shadow ничего публично не изменил.

### Фаза 1 — contracts без provider

1. Добавить typed `UnifiedTurnInput/Output`.
2. Добавить strict parser и validator.
3. Добавить code-owned eligibility/terminal bypass.
4. Добавить reducer adapter, который не принимает произвольный state patch.

Выход: offline tests покрывают malformed JSON, неизвестные action/refs, новые
ЖК/числа, больше одного вопроса, phone/contact leakage и unsupported facts.

### Фаза 2 — один adapter и один prompt

1. Добавить `UnifiedAgentPort` в существующую ports boundary.
2. Реализовать gateway adapter поверх существующего `OvermindClient`.
3. Использовать один отдельный prompt с SHA provenance.
4. Сохранять task ID сразу после создания gateway task.

Выход: isolated diagnostic request возвращает валидный contract и отчёт, но не
меняет production и state.

### Фаза 3 — V5 shadow

1. Подключить adapter после ingress safeguards, параллельно текущему V5 пути.
2. Не публиковать candidate и не применять его state proposal.
3. Сравнивать action, target, constraints, visible options и client response.
4. Отдельно считать provider/validation/latency/fallback показатели.

Выход: все обычные turn-классы имеют shadow evidence; callback и terminal flow
не изменились.

### Фаза 4 — ограниченный publish

Сначала разрешить только низкорисковые классы:

1. `recover_dialogue`;
2. `clarify` без фактов;
3. `answer_current_options` только по canonical current evidence.

Search publish включать только после доказанного MCP trace и inventory gate.

Выход: targeted TEST Jivo dialogue, deterministic fallback и rollback проверены.

### Фаза 5 — search publish

1. Gateway остаётся единственным MCP-capable provider.
2. Canonical search validator подтверждает IDs/status/evidence.
3. Model result без positive lot evidence не заявляет наличие квартиры.
4. Underfilled, empty, timeout и invalid result возвращаются в старый search path.

Выход: full TEST dialogue от первого запроса до selected lot/operator flow.

### Фаза 6 — production release

Только после отдельного stop/go:

1. fresh VPS source snapshot и compare;
2. isolated worktree/source copy;
3. immutable artifact и полный preflight;
4. API-only atomic deploy;
5. fresh health/error/bridge evidence;
6. correlated Jivo trace с task ID/provider metadata;
7. terminal outcome;
8. немедленный rollback на `off` при первой ошибке.

## 10. Предполагаемые компоненты

Минимальный change set, без нового framework:

- `nmbot_v2/ports.py` — `UnifiedAgentPort`;
- `nmbot_v2/unified_agent.py` — input/output, parser, validator;
- `scripts/nmbot_runtime_adapter.py` — V5-specific mode и Gateway wiring;
- существующий gateway client — transport и task ID;
- `prompts/v5_unified_agent.txt` — отдельный prompt;
- focused tests для contract, adapter, runtime, Jivo terminal bypass;
- runtime/provider/external-contract docs и `EXPERIMENTS.md` при реализации.

Точный owner path подтверждается ещё раз перед кодом. Не создаются второй state,
второй planner или новый delivery stack.

## 11. Основные риски

| Риск | Снижение |
|---|---|
| Агент меняет search semantics | typed action/constraints + code-owned executor |
| Diagnostic prompt влияет на tool call | не включать audit suffix; trace получать после вызова |
| Provider не умеет MCP | capability gate; search только через gateway |
| State drift | model выдаёт proposal; reducer коммитит allowlisted transition |
| Поломка callback | terminal bypass + существующие callback tests |
| Новые ЖК/цены | evidence grounding validator |
| Рост latency/cost | один primary task, bounded timeout, shadow metrics |
| Незаметный fallback | task/provider/fallback metadata в каждом отчёте |
| Смешение runtime-версий | V5-specific flag и независимые baselines |

## 12. Definition of done

Пилот считается готовым к stop/go, когда:

1. Все обычные V5 turn-классы проходят unified shadow-контур.
2. Shadow не меняет публичный ответ, state, callback или Jivo delivery.
3. Strict validator fail closed на malformed/unsupported output.
4. Каждая попытка имеет mode, payload stage, task/request ID, model,
   transport/agent/upstream/fallback и prompt SHA.
5. Search facts подтверждены canonical MCP/evidence validator-ом, а не
   `mcp_audit` модели.
6. Terminal phone/operator tests проходят без model ownership.
7. Timeout/error/empty всегда сохраняют старый deterministic ответ.
8. Rollback сводится к одному flag `off` и проверен в TEST.
9. Focused suites и full runtime/contracts gates зелёные.
10. Production не включается без fresh snapshot, immutable release и одного
    correlated Jivo terminal smoke.

## 13. Принятые решения пилота

1. Первый пилот выполняется только на V5, параллельно старому пути.
2. Все обычные turns могут проходить unified contour; телефон, callback,
   terminal operator flow, state commit и delivery остаются code-owned.
3. Transport пилота — только Overmind `gateway-agent` → OpenRouter. Bluesminds
   не является частью unified/search маршрута.
