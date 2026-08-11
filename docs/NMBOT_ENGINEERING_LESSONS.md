# NMBot engineering lessons

Purpose: keep verified development failures and prevention rules separate from
runtime contracts and release history. Each lesson records evidence, the proven
cause, unknowns, and a reusable prevention checklist.

## 2026-08-08 — Не добавлять второй model layer без доказанной необходимости

### Урок

Всегда сначала искать самое простое решение в существующем prompt и owner-
контракте. Если уже есть единый search prompt, который определяет action,
вызывает MCP и возвращает canonical `facts/near/missing/params`, нельзя добавлять
перед ним отдельный V6 classifier только ради маршрутизации. Это создаёт второй
модельный вызов, дублирующий semantic ownership и усложняющий provenance,
fallback, latency и release audit.

### Правило prevention

- reuse существующий prompt и canonical MCP owner;
- новый слой добавлять только после source-backed доказательства невозможности
  выполнить контракт текущим owner-ом;
- до реализации явно указать owner, новый контракт, стоимость и план удаления;
- проверять, что одна пользовательская задача не запускает два конкурирующих
  search/router контура;
- V6 считать versioned runtime/enrichment boundary, а не дополнительным
  classifier-ом.

## 2026-07-30 — Pair comparison executor must stay projection-free until presenter phase

### Evidence

- Local D.4 owner module `nmbot_v2/pair_comparison.py` was implemented without
  runtime/adapter/presenter/API/Jivo integration.
- Focused local test `tests/test_nmbot_v2_pair_comparison.py` passed and covers
  exact visible first+third pair selection, cache hit/miss behavior, concurrent
  exact `count=1` enrichment, state immutability, safe failure fallback to base
  cards, name-free metadata/attempts, and no default lot-example request.

### Lesson

Keep pair execution separate from pair presentation. The executor can safely
return exactly ordered grounded cards plus additive cache entries, but it must
not mutate state or claim client-facing comparison readiness until
`ExecutionResult`/`ResponsePlan`/presenter projection is implemented and tested.

## 2026-07-30 — V4 MCP context exhaustion

### Evidence

- TEST Gateway task `2451045` completed with model
  `google/gemini-3.6-flash`, used `49,005` tokens, and returned only 348
  characters of final text.
- The response stopped inside the first residential-complex description, before
  the closing quote and braces, so it was not valid JSON.
- Earlier complete V4 responses used approximately `15,000–25,000` tokens.

### Confirmed cause and boundary

The immediate failure was an incomplete final JSON response. The strict V4
validator correctly rejected it and returned the safe fail-closed answer. The
rich MCP cycle had accumulated substantially more context than successful V4
turns before final generation.

The exact Gateway/agent total-token ceiling is not documented. Do not treat
`50,000` tokens as a proven limit or encode that number as a contract.

### Prevention checklist

- Put a hard bound on tool calls per user turn.
- Keep every tool response compact; request only required output fields and a
  small number of scenario-relevant fields.
- Never request every optional complex or infrastructure field together.
- Stop searching as soon as enough grounded options are available.
- Reserve output budget before composing the client response; a smaller complete
  JSON response is better than another tool call or partial JSON.
- Preserve strict validation; do not repair truncated model JSON by guessing.
- After a prompt/tool-context change, run one first-failure TEST smoke and inspect
  its correlated task immediately before any batch or broader rollout.
- Persist safe V4 task ID, runtime error code, validation stage, and fallback
  marker so a failed turn can be diagnosed without another provider request.

### Sources

- Fresh TEST Gateway task `2451045`, inspected after the first failed smoke.
- `prompts/v4_flat_search.txt:15-24` — bounded tool and output contract.
- `nmbot_v4/runtime.py:49-57` — strict validation and fail-closed boundary.
- `docs/MCP_APARTMENT_REQUEST_RULES.md:767-783` — direct tool schema must not be
  invented when it is absent locally.
- `docs/NOVOSTROYM_MCP_SCHEMA.md:158-186` — documented lot-to-complex relation.
- `docs/NMBOT_RUNBOOK.md:171-175` — first-failure TEST smoke rule.

### Validation after the mitigation

- TEST release `nmbot-v4-bounded-context-test-20260729-2253` overlaid only
  `prompts/v4_flat_search.txt`; its health, identity, preflight, and deploy
  receipt were successful.
- The first actual synthetic Jivo smoke returned a terminal `BOT_MESSAGE` with
  strict JSON, three positive numeric IDs, and three separately numbered
  residential-complex descriptions for the family two-bedroom request up to
  30 million rubles.
- The initial two smoke wrappers failed locally before reading `.env` or making
  an HTTP request (`SyntaxError`, then `NameError`). They were diagnostic-tool
  defects, not bot results. Prevention: compile an ephemeral smoke runner
  locally before any live Jivo call; do not classify an unexecuted wrapper as a
  runtime failure and do not repeat a sent request blindly.

## 2026-07-30 — Prompt limits can constrain MCP loops only when explicit and robust

### Evidence

- Fresh TEST Gateway task `2451048` completed without a top-level provider
  error, used `85,045` tokens, and returned only 93 characters.
- The response ended immediately after the opening quote of `message`; V4
  therefore received incomplete JSON and correctly failed closed.
- The request allowed `max_tokens=1800`, so the confirmed failure was not a
  shortage of configured final-output tokens. The tool/agent cycle consumed the
  large context before the final response was completed.
- Fresh TEST Gateway task `2451234` completed, used `8,512` tokens, and returned
  `0` response characters. V4 received an empty final answer and the strict
  validator failed closed immediately.

### Confirmed boundary

A prompt can constrain the MCP/tool loop, but only when the instruction is
explicit, model-robust, and terminal after the first tool result. The previous
wording was not stable enough for `google/gemini-3.6-flash`: it still allowed a
large or incomplete tool/final-output path in the observed tasks.

Current safe Gateway metadata and local contracts cannot independently prove the
exact MCP call count for these tasks. Do not claim a precise call count from this
telemetry alone, and do not invent a runtime tool-iteration parameter.

For this stability pass, V4 must ask for exactly one compact `get_flat_info`
result per apartment-search turn, request only `limit:10` plus compact basic
fields, and state that after the tool result no second tool is allowed under any
condition. Even an empty result must produce a complete strict JSON response.

### Prevention checklist

- Do not describe telemetry-only evidence as proof of exact MCP call count.
- Make prompt-only tool limits explicit enough to constrain the model loop:
  one apartment-search turn, one compact `get_flat_info`, then terminal JSON.
- Use one compact MCP request and select up to three grounded complexes from that
  result; return fewer complexes rather than starting another search.
- Do not request optional family/scenario/infrastructure fields during this
  stability pass; keep fields compact enough to leave room for final JSON.
- Keep strict JSON validation and inspect the exact Gateway task after the first
  failed turn instead of repeating the request.
- Add code-level task/tool observability before claiming a proven exact
  iteration count.

### Sources

- Fresh TEST Gateway task `2451048`, correlated with the failed V4 Jivo turn.
- Fresh TEST Gateway task `2451234`, completed with empty final response and
  immediate validator fail-closed.
- `nmbot_v4/provider_adapter.py:24-58` — one Gateway task and current payload.
- `scripts/nmbot_gateway_client.py:534-603` — safe task/result metadata boundary.
- `prompts/v4_flat_search.txt:15-24` — current single-call behavioral contract.

### Follow-up evidence

- Fresh TEST Gateway task `2451236` completed with
  `google/gemini-3.6-flash`, used `8,402` tokens, and returned zero response
  characters after the compact single-call prompt release.
- The current gateway-agent path cannot be given OpenRouter `response_format`
  as a workaround: the recorded live gateway investigation on 2026-07-27
  established that this main-search parameter is rejected downstream. Do not
  add undocumented provider or tool-control parameters to V4.
- The next bounded prompt iteration uses explicit finite phases: one tool
  action, then one terminal assistant JSON action. This is a behavioural
  constraint to test, not a proof that upstream will always return text.

### UX-gate failure evidence

- Fresh TEST Gateway task `2451264` completed for the V4 family-search turn
  and exposed a 143-character final response. It was not a valid JSON envelope,
  so V4 correctly returned the safe fail-closed client answer before the
  client UX gate could evaluate a shortlist.
- The observed metadata does not expose raw MCP calls or a proven token/context
  cause. Treat the upstream truncation mechanism as unknown; do not attribute it
  to a fixed token ceiling or a specific number of tool calls.
- The next prompt contract keeps one compact tool call, requests at most one
  supported family field, limits the result to six IDs/two per confirmed complex,
  caps the message at 900 characters, and serializes the complete JSON directly
  after the tool result. This is an empirical mitigation to verify with one
  first-failure TEST smoke.

## 2026-07-30 — Contact capture belongs before the model

### Contract

The client phone is business input for the callback pipeline, not search-model
context. Capture and normalize it in code before Gemini or MCP, enqueue it in the
existing private outbox, and let the separate worker append the four-column row
to Google Sheets. A successful apartment search is not a prerequisite for
queueing a confirmed callback.

V4 keeps its strict wire contract for contact turns:
`{"data":[],"message":"..."}`. Persisted V4 state may contain a safe contact-flow
marker, safe name, redacted phone marker, consent, and opaque callback reference,
but the model-facing state projection must contain only search-continuity fields.

### Prevention checklist

- Run phone capture before any provider call; assert provider call count is zero.
- Never place a raw phone in public output, ordinary runtime state, logs, model
  payload, or engineering reports.
- Keep callback state in `nmbot_v4`; do not borrow the `nmbot_v2` namespace.
- Reuse `LocalCallbackOutbox`, the callback worker, Google Sheets adapter, and
  private delivery ledger rather than writing to Sheets from the Jivo request.
- Prove delivery by opaque `lead_ref`, `sheet_delivered`, and bounded `row_ref`,
  without printing the phone, credentials, or spreadsheet link.

### Sources

- `scripts/nmbot_runtime_adapter.py:372-462,2480-2642` — V4 pre-model capture and
  shared callback/outbox contract.
- `nmbot_v4/contracts.py:20-98` and `nmbot_v4/runtime.py:40-45` — persisted state
  versus model-facing projection.
- `scripts/nmbot_callback_sheet_worker.py:43-117` — durable worker delivery.
- `scripts/nmbot_google_sheets.py:57-97,218-277` — validated Sheets adapter and
  private delivery ledger.

## 2026-07-30 — Transport success is not client UX acceptance

### Evidence

- A complete TEST dialogue reached `sheet_delivered`, but the Jivo screenshot
  still showed the internal `{data,message}` JSON envelope and literal `\\n`
  escapes to the client.
- The same answer used masculine `Я подобрал`, presented only two thin catalog
  blocks for a family request, contained decimal dots, and asked about two
  refinement axes at once.

### Contract and prevention

- Keep V4 `answer` as strict internal JSON for validation and state, but expose a
  separate validated `client_answer` containing only `message`. Jivo must send
  the latter without parsing arbitrary JSON at the API boundary.
- A release smoke is accepted only when both layers pass: internal wire JSON and
  client-visible UX. Sheets delivery alone proves callback transport, not answer
  quality.
- The deterministic client gate rejects raw JSON, literal escapes, masculine
  persona, decimal dots, multiple questions, missing top-three blocks, thin
  blocks, and a family answer with neither grounded family wording nor an honest
  missing-facts boundary.
- Lexical family checks do not prove MCP grounding. Exact claims and every
  `ads_id -> ЖК` relation still require raw structured tool evidence or a
  gateway contract that exposes it safely.

### Sources

- User-provided Jivo screenshot from 2026-07-30.
- `scripts/nmbot_api_server.py:2989-3035` — client presenter boundary.
- `nmbot_v4/runtime.py:62-75` — separate strict `answer` and `client_answer`.
- `nmbot_v4/client_ux.py:16-86` — deterministic client acceptance checks.
- `docs/IDEAL_IRINA_UX.md:25-45,63-78` and
  `docs/LLM_SCENARIO_EVAL_RUBRIC.md:542-571` — target shortlist UX.

## 2026-07-30 — Availability needs an evidence status, not a boolean answer

### Evidence

- A selected V3 complex received a separate selected-enrichment Gateway pass
  after the client asked to check availability.
- The runtime deliberately does not treat a model-produced apartment inventory
  as fresh confirmation when the Gateway does not expose raw MCP evidence.
  Therefore the client answer may honestly say that availability is not yet
  confirmed; it must not be read as proof that no apartments exist.

### Prevention checklist

- Persist only `availability_evidence`: whether availability was requested,
  `confirmed|not_confirmed|not_requested`, `gateway|cache|base|unknown`, and an
  optional bounded Gateway task ID.
- Never persist the inventory value/count, raw MCP/tool content, prompt, client
  request, contact, or secret in this diagnostic field.
- Say "not confirmed" rather than "not found" unless canonical evidence proves
  inventory absence.
- A Gateway attempt alone proves neither a concrete MCP tool call nor its raw
  result; do not overstate it in diagnostics or client messaging.

### Sources

- `scripts/nmbot_runtime_adapter.py:1654-1662` — unproven inventory is excluded
  from fresh facts.
- `scripts/nmbot_runtime_adapter.py:1681-1724,3956-4013` — safe availability
  evidence trace and sanitizer.

## 2026-07-30 — Selected availability uses typed lot evidence

### Evidence

- The existing exact-name enrichment path was run once in TEST for a selected
  complex with `facts_needed=("lot_examples",)`. It returned two normalized
  `LotExample` records with bounded apartment fields, positive IDs and
  `status=2` (in sale).
- The minimal runtime fix maps a requested `apartment_inventory` fact to
  `lot_examples` for the enrichment request, while preserving the original
  user-facing fact name.
- V3 confirms availability only when a normalized lot has a non-empty ID and
  an active/in-sale status. A model-only inventory scalar, missing status, or
  non-sale lot remains `not_confirmed`.

### Prevention checklist

- Reuse the existing selected exact-enrichment owner; do not add a second SQL
  or direct MCP contour for this case.
- Keep `lot_examples` as the structured evidence and retain the conservative
  unknown path when parsing, identity or status validation fails.
- Treat catalogue availability as evidence of listed lots, not as a booking or
  reservation guarantee.

### Sources

- `scripts/nmbot_runtime_adapter.py:1628-1673,3473-3506` — mapping and
  conservative confirmation.
- `nmbot_v2/search_enrichment.py:37-120` — exact enrichment and structured
  `ads`/lot normalization.
- `nmbot_v2/contracts.py:177-246` — `LotExample` and `OptionCard` contracts.
- `docs/NMBOT_SELECTED_ZHK_LOT_FUNNEL.md:99-121,265-274` — lot evidence and
  honest missing-data UX.
- TEST enrichment task `2451943` — safe result summary: two structured lots,
  IDs present, `status=2`; raw MCP/model content was not retained.

## 2026-07-30 — Selected constraints and human recovery are scope-bound

### Evidence

- An isolated exact selected-enrichment probe for «Новое Видное» with a hard
  `rooms=2` constraint returned two normalized active/in-sale lots whose
  `rooms` values were both `2`, but the former complex-level validator rejected
  the whole card as `fact_0_violates_hard:rooms`.
- The cause is an evidence-scope mismatch: a selected ЖК can contain several
  room formats, while room count, lot price, area, floor and lot status belong
  to `LotExample`, not to the whole `OptionCard`.
- The local candidate introduces `lot_hard` for selected enrichment. Only
  source-proven lot field `rooms` is supported now. It keeps exact name, strict
  JSON, ID/status checks and complex-level validation; broad-search constraints
  are not weakened.
- Exact selected enrichment makes one bounded repair only for parse/contract
  failures and preserves the same ЖК, requested facts and all hard constraints.
  Valid empty/no-match and identity mismatch do not trigger blind retries.
- Main search already owns provider retry and main-search fallback attempts.
  After an exhausted technical failure, V2/V3 use the existing operator
  consent/contact flow with human text instead of telling the client to retry.

### Prevention checklist

- Declare the evidence scope of every new hard constraint before validating it:
  complex, building or lot.
- Do not disable strict validation globally to accommodate a nested fact.
- Do not reuse a cached post-filtered selected card for a different `lot_hard`
  scope.
- Do not add a second blind runtime retry above Gateway/search recovery.
- Keep valid absence, missing evidence and technical failure distinct. Only the
  last one may conclude with an operator offer after bounded recovery.

### Sources

- `nmbot_v2/contracts.py:177-246` — separate `LotExample` and `OptionCard`
  contracts.
- `nmbot_v2/search_contract.py:456-501` — complex `effective_hard` versus
  lot-scoped `lot_hard` validation.
- `nmbot_v2/search_enrichment.py:84-195` — exact repair and no-match boundary.
- `scripts/nmbot_runtime_adapter.py:1621-1713,3435-3468` — selected ownership,
  cache scope and safe trace.
- `scripts/nmbot_gateway_client.py:410-432,434-532` — provider and main-search
  recovery boundary.
- Local focused regression: 36 passed; no TEST deployment or live claim.

## 2026-07-30 — V3 comparison cue must stay inside the visible shortlist

### Evidence

- The redacted historical V3 planner output for “сравни с Томилинским
  бульваром” used `goal=compare_current`, a selected option and a
  `named_object_reference`; both names were already present in
  `visible_options`.
- V3 previously rejected every named reference outside `lookup_object` with
  `invalid_named_reference_scope`, so the turn fell into recovery before
  search or the current-options presenter.

### Confirmed boundary

For `compare_current`, a named reference that exactly matches a visible option
is an in-list comparison cue, not an external object lookup. The normalized V3
turn clears object-specific fields and keeps `scope=all`, so the existing
current-options comparison presenter owns the shortlist. A name outside the
visible list remains rejected; this change does not widen named-object lookup.

### Prevention checklist

- Validate object references against the owner scope before routing them to
  search or lookup.
- Do not introduce a second comparison-pair contract when the presenter owns a
  shortlist comparison contract.
- Preserve safe transition telemetry: it may record the validation category,
  but never object names or raw plans.

### Sources

- `nmbot_v2/semantic_planner.py:154-184` — visible-list validation and
  normalization boundary.
- `scripts/nmbot_runtime_adapter.py:2116-2172,2294-2364` — V3-to-V2
  conversion and selected-scope inheritance boundary.
- `tests/test_intent_plan_v3_transition.py:171-229` and
  `tests/test_nmbot_runtime_adapter.py:4878-4918` — local regression coverage.

## 2026-07-30 — V3 transition failures need a safe intent trace

### Evidence

- A historical V3 turn asking to compare an already visible ЖК with another
  visible ЖК reached neither search nor MCP: the execution path recorded a
  transition validation failure while the terminal text also violated the
  one-final-question quality rule.
- The existing journal deliberately omitted raw IntentPlan, so it could not
  distinguish an invalid model plan from a rejected transition after the fact.
- Local trace now preserves only `intent_transition`: known goal, validation
  status, allowlisted validation codes, transition accepted/error status and
  fallback flag. It excludes query text, names, constraints, requested facts,
  confidence, raw model text and contacts.

### Prevention checklist

- Diagnose compare/selected routing from the safe intent-transition trace before
  changing MCP, search or prompts.
- Do not infer an MCP failure when the trace says search was skipped.
- Keep the exact IntentPlan private; validation category is enough to route a
  repair safely.

### Sources

- `nmbot_v2/transition.py:46-76,88-112` — V3 transition owner.
- `nmbot_v2/runtime.py:657-694` — runtime summary boundary.
- `scripts/nmbot_runtime_adapter.py` safe trace sanitizer;
  `scripts/nmbot_api_server.py` and `scripts/dialogue_journal.py` journal
  allowlists.
- Local focused regression: 36 passed; no TEST deployment or live claim.

## 2026-07-30 — V3 pair comparison contract must not reuse legacy object fields

### Evidence

- Local D.1/D.3 groundwork added `comparison_option_names` only to
  `IntentPlanV3` and `ExecutableTurn`; `SemanticPlan` intentionally has no pair
  field, so the future executor owns projection.
- The parser treats an absent or empty list as no pair intent; a non-empty value
  must be a list/tuple of exactly two non-empty strings. It trims each item,
  preserves order, rejects duplicate normalized values and maps parse failures
  to safe code `invalid_comparison_option_names`.
- Validation allows a non-empty pair only for `compare_current`, requires both
  exact names to be visible, and rejects simultaneous legacy
  `selected_option_name` or `named_object_reference` with
  `comparison_option_fields_conflict` instead of choosing one representation.

### Prevention checklist

- Keep pair intent separate from selected/named object fields; mixed
  representations must fail closed until one owner projects them.
- Preserve the temporary no-pair legacy compare workaround separately: visible
  named references still clear object fields and keep `scope=all`.
- Safe traces may expose validation categories such as
  `comparison_option_not_visible`, but never the compared object names.

### Sources

- `nmbot_v2/contracts.py:94-174,331-368` — pair field and serialization.
- `nmbot_v2/semantic_planner.py:146-184,187-222` — validation and parse mapping.
- `nmbot_v2/transition.py:46-66,155-198,233-258` — safe trace allowlist and
  carry-through into `ExecutableTurn`.
- Local focused evidence: `PYTHONPATH=. python3 -m pytest
  tests/test_intent_plan_v3_validation.py tests/test_intent_plan_v3_transition.py
  tests/test_followup_canonical_contract.py -q` → `83 passed in 0.32s`; no
  TEST/VPS/deploy claim.

## 2026-07-30 — Pair enrichment needs an isolated per-turn owner

### Evidence

- `execute_pair_comparison()` resolves only the two exact names declared by the
  compiled V3 pair; it does not select the first two visible cards or fuzzy-match
  a name.
- Each side independently uses a fresh, sufficient state-cache entry or the
  existing bounded exact enrichment. Cache misses run concurrently and retain
  their base card on an enrichment failure.
- The executor returns cache additions rather than mutating dialogue state, and
  its metadata records only counts, indexes and status categories.

### Prevention checklist

- Keep the pair result per-turn until the response projection has an explicit
  two-card contract; do not write it into `selected_enriched`.
- Reuse `fetch_enriched_option_v2()` rather than adding a second enrichment or
  retry policy.
- A partial enrichment failure is not permission to remove the other grounded
  card or invent the missing facts.

### Sources

- `nmbot_v2/pair_comparison.py:33-251` — isolated executor and safe output.
- `nmbot_v2/search_enrichment.py:38-189` — exact-enrichment contract reused by
  the executor.
- `tests/test_nmbot_v2_pair_comparison.py:85-299` — local pair/cache/failure
  regressions.

## 2026-07-30 — Pair cards need a per-turn response projection

### Evidence

- A pair executor alone is not client-visible: `ExecutionResult`, response plan
  and composer must carry the exact two-card projection, otherwise a generic
  shortlist fallback can reintroduce a third ЖК.
- The local D.5 chain invokes the pair executor only for a compiled V3 pair,
  keeps generic compare unchanged, merges only additive cache entries and leaves
  the visible shortlist and `selected_enriched` untouched.
- On a partial or full exact-enrichment failure, the local response preserves
  the grounded base cards and states the limitation. It does not retry broadly
  or invent a pair-specific operator handoff.

### Prevention checklist

- Keep comparison cards per turn; never repurpose selected-card state for two
  objects.
- Make response and composer choose the pair projection before their normal
  shortlist fallback.
- Treat pair-specific operator escalation as an explicit product contract, not
  an accidental consequence of a technical fallback.

## 2026-07-30 — Pair telemetry needs a separate public allowlist

### Evidence

- Full integration audit found that pair validation categories stopped at the
  adapter sanitizer and that public `runtime_summary` omitted pair execution
  aggregates.
- The remediation adds only four already-safe transition categories and a
  separate `pair_comparison` allowlist: status plus bounded counts. It excludes
  compared names, query text, raw payload and contacts.
- Focused sanitizer tests passed (`4 passed`); pair/runtime/adapter regression
  batch passed (`177 passed`); `py_compile scripts/nmbot_runtime_adapter.py`
  passed. Focused re-review returned PASS with no critical/high findings.

### Prevention checklist

- Never project pair metadata by copying the executor object into public trace.
- Add a new public pair field only through an explicit allowlist and a no-leak
  regression that asserts names/query/raw payload remain absent.

### Sources

- `scripts/nmbot_runtime_adapter.py:3937,3951` — public sanitizer and safe
  validation-category allowlist.
- `nmbot_v2/runtime.py:686-704` — runtime-private safe pair metadata boundary.
- `tests/test_nmbot_runtime_adapter.py:143,172` — focused no-leak regressions.

### Sources

- `nmbot_v2/runtime.py:295-318,410-420` — pair dispatch and additive cache
  delta.
- `nmbot_v2/response.py:907-920,1462-1475` and
  `nmbot_v2/response_composer.py:201-232` — two-card response/composer scope.
- `tests/test_nmbot_v2_pair_projection.py:116-181` — local projection,
  third-card exclusion and failure regressions.

## 2026-07-30 — Pair routing needs schema and prompt alignment

### Evidence

- `comparison_option_names` is now a required V3 JSON field: an empty list
  preserves generic comparison, while a two-name list is the only model-owned
  pair signal.
- The prompt directs an explicit visible A/B comparison into that ordered pair
  and keeps external names out of the pair; typed validation remains the owner
  of exact visible-name checks and conflict rejection.

### Boundary

Local tests prove schema, prompt text and mocked planner-to-runtime wiring only.
They do not prove that a live provider will produce the pair for natural client
wording; that requires the later approved TEST first-smoke gate.

### Sources

- `followup_intent_classifier.py:73-190,1369-1423` — V3 prompt, schema and
  provider payload.
- `tests/test_followup_canonical_contract.py` and
  `tests/test_nmbot_v2_pair_projection.py` — deterministic local coverage.

## 2026-07-30 — Generic comparison must override an ungrounded model pair

### Evidence

- In a live V3 session, an explicit first+third comparison succeeded, but the
  following generic «Сравни их» initially repeated that pair although state
  still contained three visible options.
- The planner model had selected `compare_current`; the stale pair signal had
  to be normalized before typed compilation. Prompt wording alone was not a
  sufficient contract guard.
- The final TEST release keeps a pair only when the client text grounds both
  canonical names, selected+other name, or two visible ordinal references.
  Otherwise `comparison_option_names` becomes empty and generic-all is used.
- Final live replay returned two cards for explicit first+third and all three
  cards for the subsequent generic request. Both terminal turns had
  `error_summary.status=ok` and no fallback.

### Prevention checklist

- Never infer pair scope only from the model field; verify it against current
  client-text evidence and visible state before compiling the transition.
- Preserve ordinal references across common Russian case/gender forms.
- Test explicit pair followed by generic compare in the same stateful session.

### Sources

- `nmbot_v2/semantic_planner.py` — pair-evidence normalization.
- `scripts/nmbot_runtime_adapter.py` — normalization before V3 compilation.
- TEST session `2ffaf1263895`; release
  `nmbot-v3-generic-compare-guard-test-20260730-1`.
