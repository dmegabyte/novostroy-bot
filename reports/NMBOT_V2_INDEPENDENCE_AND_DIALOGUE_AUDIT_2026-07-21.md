# NMBot V2 independence, simplification and dialogue audit

> **HISTORICAL SNAPSHOT (2026-07-21).** Этот отчёт не обновляет текущий
> production-контракт. Актуальные route, selector и composer mode проверяются
> по source и свежему Jivo/VPS evidence.

Date: 2026-07-21

Status: in progress. H054 is production deployed and tested, but remains open;
phase 7 is still `[in_progress]`. This file is the persistent evidence report
for the V2 separation and quality programme. Conclusions marked `confirmed` are
backed by project code/tests or production evidence; proposed changes remain
proposals until implementation and verification are recorded here.

## 1. Requested outcome

Make Jivo V2 a product independent from V1, reduce duplicated and stale layers,
keep semantic work with the LLM while code owns only contracts/safety/state,
improve diagnostics and documentation, and verify that dialogue remains natural,
grounded and useful across simple, compound and multi-scenario requests.

## 2. Current authoritative product route

`Jivo widget → n8n bridge → scripts/nmbot_n8n_bridge_server.py →
scripts/nmbot_api_server.py → scripts/nmbot_runtime_adapter.py → semantic planner
→ nmbot_v2 transition/search/state/response → callback outbox / Jivo BOT_MESSAGE`.

Sources: `README.md:5-50`, `scripts/nmbot_n8n_bridge_server.py:150-235,406-489`,
`scripts/nmbot_api_server.py:2539-2715`, `nmbot_v2/runtime.py:30-131`.

## 3. Initial Actual / Contract / Desired

| Area | Actual | Contract | Desired |
|---|---|---|---|
| Runtime selection | Adapter defaults to V1 and API always supplies `_run_chat_v1`; live V2 depends on external env | Jivo V2 is the only current product; Telegram is rollback/legacy | A direct V2 entrypoint whose default cannot silently become V1; rollback lives outside the V2 product |
| Planner | One semantic planner call, but shared root module and legacy-shaped state projection | LLM owns semantic understanding; code validates bounded output | V2-owned planner interface and prompt with canonical V2 state vocabulary |
| State | Canonical `nmbot_v2` nested inside legacy envelope with import/mirroring | Canonical state is source of truth; private data bounded | V2-native store/envelope; explicit one-way migration tool, no per-turn legacy mirroring |
| Response | Deterministic `ResponsePlan` renderer; offline composer remains elsewhere | Grounded, one question, natural sales presentation | Keep one authoritative response route; prove whether deterministic or one existing LLM step performs better before adding calls |
| Multi-scenario | Multiple facts/facets, one scenario/viewpoint; cross-turn switching covered | A user may combine several needs in one natural request | Explicit compound-intent contract and tests; preserve all requested needs without exploding routes |
| Diagnostics | Three partially overlapping tools and disconnected trace views | Production status requires API+bridge+timestamp and first-failure evidence | One V2 diagnostic entrypoint and one bounded correlated turn report |
| Docs/release | Current and legacy instructions coexist; release manifest is V1-oriented | Docs are source of truth | Current V2 docs separated from clearly labelled historical V1 material; truthful manifest |

Evidence: `scripts/nmbot_runtime_adapter.py:43-68,175-250,640-722`,
`scripts/nmbot_api_server.py:107-127,733-765,2160+`,
`nmbot_v2/semantic_planner.py:49-99`, `nmbot_v2/contracts.py:249-298`,
`scripts/nmbot_release.py:22-31`.

## 4. Confirmed strengths

- One planner call and a linear deterministic transition/runtime.
- Strict MCP/search contract and canonical `OptionCard` grounding.
- State changes accepted only after valid transition/execution.
- Safe selected enrichment with bounded outcomes and no unbounded retries.
- Strong lexical/structural quality blockers: unsupported facts, internal leaks,
  repeated benefits, dry cards and multiple questions.
- Stateful tests cover refinements, exact selection, named objects, financing,
  operator consent, contact interruption and topic switching.

Sources: `nmbot_v2/runtime.py:42-89`, `nmbot_v2/search_contract.py:14-50`,
`tests/test_nmbot_v2_quality.py:42-139`,
`tests/test_nmbot_runtime_adapter.py:1449-1569`.

## 5. Confirmed gaps

1. Physical V1 reachability remains in the production API source.
2. The repository lacks checked-in API/bridge units proving the live selector.
3. V2 state and planner context still use compatibility vocabulary/envelopes.
4. No one-turn independent multi-scenario executable contract was found.
5. The quality fixture is first-list only and V2 has no golden dialogue corpus.
6. Current live diagnostics cannot show route, call count, state delta, grounding,
   stage latency and quality blockers together for one turn.
7. Authoritative and historical documentation are interleaved and contradictory.

## 6. External best practices

- Anthropic, “Building effective agents”:
  https://www.anthropic.com/research/building-effective-agents
  — begin with the simplest composable workflow; add autonomous loops or
  evaluator layers only after measured need.
- OpenAI, “A practical guide to building agents”:
  https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/
  — prefer one agent with clear instructions and well-defined tools; define exit
  conditions and human handoff.
- OpenAI, “Evaluation best practices”:
  https://developers.openai.com/api/docs/guides/evaluation-best-practices
  — evaluate intent, tool selection/arguments, answer and state separately; use
  production failures and compound requests as dataset cases.
- OpenAI, “Integrations and observability”:
  https://developers.openai.com/api/docs/guides/agents/integrations-observability
  — correlate model/tool/handoff/guardrail spans in one trace.
- OpenAI, “Conversation state”:
  https://developers.openai.com/api/docs/guides/conversation-state
  — preserve needed messages, tool calls and outputs; manage context explicitly.
- Google Cloud, “General agent design best practices”:
  https://cloud.google.com/dialogflow/cx/docs/concept/agent-design
  — use natural production utterances, acknowledge understood parameters and ask
  one question at a time.

## 7. Proposed sequence before code changes

1. Measure current coupling and baseline dialogue behavior.
2. Create a direct V2 application boundary and truthful deployment manifest.
3. Separate one-way legacy migration from normal V2 reads/writes.
4. Move shared semantic planner/context under a V2-owned interface without
   changing the model call or prompt semantics.
5. Add compound multi-scenario contracts and a V2 golden dialogue corpus.
6. Consolidate diagnostics around one correlated per-turn report.
7. Update current docs and mark V1 material historical.
8. Run local gates, deploy with backup and conduct several production dialogues.

### Impact chain for the first separation slice

```text
Jivo CLIENT_MESSAGE
  → scripts/nmbot_n8n_bridge_server.py (transport/dedup)
  → scripts/nmbot_api_server.py:run_chat
  → scripts/nmbot_runtime_adapter.py:run_runtime_turn
  → _run_v2_authoritative
  → JsonStateStore record
  → _legacy_to_v2_state (canonical read or one-way migration)
  → semantic planner / MCP / deterministic response
  → canonical V2 state envelope save
  → API dialogue journal / bridge BOT_MESSAGE
```

Readers affected by runtime-selector removal:

- production `run_chat()`;
- adapter selector tests;
- explicit V1 API compatibility tests;
- release/status documentation and systemd environment.

Writers affected by state-envelope normalisation:

- successful V2 turn save;
- failed-search retry save;
- V2 contact capture save;
- `/start` and `/api/reset`;
- migration tests and any diagnostic that reads root legacy fields.

Validators required before deployment:

1. direct-script `--smoke` import without Telegram module;
2. V2 API/Jivo/callback/state tests;
3. one-way legacy-record migration test;
4. full suite with V1 suites either moved to explicit legacy scope or retained
   without defining production runtime selection;
5. VPS unit environment, hash, API/bridge health and isolated Jivo dialogues.

Sources: `scripts/nmbot_api_server.py:37-68,107-162,733-765,2160-2164`,
`scripts/nmbot_runtime_adapter.py:43-167,640-732`,
`tests/test_nmbot_runtime_adapter.py:225-459`,
`tests/test_nmbot_api_phase7.py:25-119`.

## 8. Work log and final verdict

### H054 separation implementation — first slice

- `run_runtime_turn()` is V2-only; runtime env and `legacy_runner` were removed.
- API `run_chat()` no longer references `_run_chat_v1`.
- V2 saves canonical `{nmbot_v2: ...}` envelopes; legacy-only records retain a
  bounded one-way migration reader.
- New/reset API records are canonical V2.
- Release default manifest now enumerates active V2/Jivo runtime and preserves
  relative backup paths.
- A broader callback gate exposed and closed proactive phone capture as a real V2
  capability gap. Full phone remains only in the private outbox/draft, and
  substantive property text is not swallowed.

Current local evidence: adapter 70 passed; callback 4 passed; release/API focused
tests passed. Broad Jivo suite migration continues under first-failure triage;
legacy V1 exact-copy tests are not copied into V2 unless they protect an active
product invariant.

After first-failure migration and removal of an attempted fuzzy token router, the
complete local repository suite initially passed: `646 passed` with 631 existing aiohttp
warnings. Typo selection remains an LLM semantic responsibility; code accepts
only a canonical visible option. This was superseded by later H054 deployment and
production testing; H054 still remains open pending residual gates.

### H054 compound/multi-scenario implementation

- The semantic planner now has a bounded `scenario_needs` field for
  `family`, `rental`, `investment`, `life` and `financing`. One primary
  `response_viewpoint` still owns routing; financing remains an overlay.
- The production adapter merges normalized needs through the existing transient
  `SemanticPlan.facets` carrier. No second router, model call or persisted prose
  state was introduced.
- The search request unions all scenario field priorities while keeping scenario
  needs out of hard constraints and returned params.
- Native current-options presentation acknowledges combined goals, limits output
  to three grounded ЖК and one final question.
- One isolated live planner probe for family + future rental + mortgage preserved
  all three needs. It also emitted a non-essential multi-parameter clarification.
  Prompt wording alone did not fix that provider behavior, so runtime consistency
  now gives explicit new-object intent precedence, clears the conflicting question
  for FIRST_LIST, and retains genuine selected-reference clarification.

Evidence: planner fixture `16/16`; focused semantic/search/dialogue gate
`67 passed`; complete local suite `654 passed` with 631 existing aiohttp warnings.
This local evidence was later followed by production deploy and live repair;
H054 is no longer deployment-pending, but still open.

### H054 production deploy and live repairs

- H054 was deployed and production tested through a sequence of backups:
  `deploy-20260720-235222`, `deploy-20260720-235811`,
  `deploy-20260721-000206`, `deploy-20260721-000521`,
  `deploy-20260721-001008`, `deploy-20260721-001504`,
  `deploy-20260721-001840`, `deploy-20260721-002040`,
  `deploy-20260721-002216`; final deploy `deploy-20260721-002839`.
- Current services after the final deploy: API PID `176985`, bridge PID `176987`,
  both active, health green, deployed hashes matched.
- First production FIRST_LIST failure was a wrapper boundary. Raw planner had
  `scenario_needs=family,rental,financing`, but canonical output exposed the
  needs only nested. Runtime consumed top-level needs, so it normalized none.
  Added additive top-level `scenario_needs` passthrough in
  `followup_intent_classifier.py`. Exact live response after the fix acknowledged
  family, future rental and mortgage, presented three cards and asked one final
  question. Error events stayed `239 -> 239`.
- Selected finishing gap: runtime selected-object fact contracts did not include
  `finishing` in `ALLOWED_FACTS`, present-fact detection or renderer. Added the
  finishing contract and renderer branch. Live selected-object answer now covers
  finishing honestly when missing, plus metro and mortgage. A separate selected
  four-fact turn covers readiness, apartment price, parking and parking price.
- Selected multi-scenario interruption: renderer previously collapsed the answer
  to mortgage. It now adds grounded selected scenario context using existing
  benefit/caveat helpers, covering family, future rental and mortgage without
  treating the property question as a contact name.
- Contact flow preservation: substantive selected financing/fact turns no longer
  overwrite pending `contact_name`/`contact_phone` with `financing_consent`.
- Resume variability: prompt-only `resolved_intent` was unstable. Added bounded
  `CONTACT_NAME_FOLLOWUP` reply contract with sole outcome `resume_contact`, a
  planner contact envelope, semantic signal and existing transition mapping.
  Ordinary property questions with null/invalid outcome continue through normal
  semantic routing. Final production resume `Вернёмся к заявке.` produced a
  neutral operator request for Томилинский бульвар and asked
  `Как к вам обращаться?`, with no mortgage wording; errors stayed `239 -> 239`.
- Final verification: local full suite **670 passed** with 641 existing aiohttp
  warnings. Important targeted H054 gates included **176 passed** for recipe,
  adapter, semantic and runtime coverage.

Sources: `logs/planner_trace-2026-07-20.jsonl:208-210`,
`logs/planner_trace-2026-07-21.jsonl:1-20`; later resume trace is in the same
production trace file but no exact line is asserted here. Production command
outputs from the session provide the deploy, health, PID, hash and error-count
evidence.

### H054 diagnostics consolidation

- `TurnResult.trace.runtime_summary` records only safe aggregates: stage/action,
  answer kind, semantic call counts, canonical state shape before/after, stage
  timing, question count, bounded quality blockers and
  `grounding_scope=canonical_response_plan` (not a claim that prose grounding was
  independently validated).
- Adapter, API and journal independently sanitize the summary; raw text, option
  names, state values, prompts, payloads and contacts are excluded.
- Existing `nmbot_jivo_dialogue_diagnose.py` gained `--audit-only`, so production
  journal metrics are usable without pretending bridge and journal share a ref.
- `nmbot_diag.sh` now targets current API+bridge units, health endpoints, error and
  structured bridge logs, plus audit-only runtime summary. README and
  `docs/JIVO_DIAGNOSTICS.md` now label Telegram paths as historical.

Updated local evidence: shell syntax and diagnoser self-test pass; privacy/API
diagnostic checks `5 passed`; complete suite `662 passed` with 635 existing aiohttp
warnings. This diagnostics-local statement is historical; H054 was later deployed
and tested as recorded above.

### Residual diagnostics and risks after production deploy

- Direct `/api/chat` turns do not persist to `dialogue_journal.jsonl`. An
  audit-only journal view can show an older webhook ref and must not be used as
  evidence for a direct API smoke. Direct API evidence in this H054 production
  cycle is the answer text, planner trace and error-event count.
- Remaining gates: independent shortlist → typo reference → `да` scenario, and
  real widget/bridge delivery confirmation.
- Direct API proves runtime/state behavior only. Do not claim final readiness.

### Baseline production dialogues before separation changes

Cutoff: `2026-07-20T21:44:17Z`. Three isolated synthetic Jivo sessions produced
no new records in `logs/bot_error_events-2026-07-20.jsonl`.

#### Compound family + rental + financing request

Input combined a two-room budget search, family use, finishing/metro, future
rental and family mortgage. The planner emitted `intent=rental`,
`response_viewpoint=rental`, `requested_facts=[apartment_price,mortgage_terms]`.
The answer presented three grounded rental cards but did not preserve the family
axis or discuss the requested mortgage boundary.

On the next selected-object turn the planner requested `finishing`, `metro` and
`mortgage_terms`; the answer gave metro and an honest mortgage gap but omitted
finishing entirely.

Sources: VPS `logs/planner_trace-2026-07-20.jsonl:196-197`,
`logs/dialogue_journal.jsonl:848-851`,
`logs/model_payload_metrics-2026-07-20.jsonl:559-562`.

#### Selected-object compound facts

The request asked in one turn for readiness, minimum apartment price, parking and
parking price. All four facts were represented: readiness and price were grounded;
parking and parking price were explicitly missing; one concrete operator CTA was
shown. This is a strong existing behavior and should be preserved.

Sources: VPS `logs/planner_trace-2026-07-20.jsonl:198-200`,
`logs/dialogue_journal.jsonl:852-857`,
`logs/model_payload_metrics-2026-07-20.jsonl:563-568`.

#### Contact interruption and multi-scenario follow-up

The bot reached contact-name capture after a parking live-check consent. A new
family + future-rental + mortgage property question was correctly routed as a
domain question rather than saved as a name. The answer, however, covered only
mortgage. An explicit return phrase resumed `Как к вам обращаться?`, but the
operator reason changed from parking to mortgage.

Sources: VPS `logs/planner_trace-2026-07-20.jsonl:201-206`,
`logs/dialogue_journal.jsonl:858-869`,
`logs/model_payload_metrics-2026-07-20.jsonl:569-577`.

#### Baseline verdict

| Criterion | Result |
|---|---|
| Operational stability | Pass: zero fresh errors |
| Grounding | Pass in inspected responses |
| One final question | Pass |
| Compound facts within one selected subject | Pass in the four-fact case; fail in finishing+metro+mortgage case |
| Independent scenarios in one request | Gap: one viewpoint wins and other needs can disappear |
| Contact interruption | Pass for not capturing a property question as a name |
| Contact context resume | Partial: flow resumes, original operator reason drifts |
| Per-turn call/state diagnostics | Insufficient; first audit read legacy root instead of canonical `nmbot_v2`, and selected enrichment is labelled `main_search` |

State reinspection confirmed persistence is healthy in all three sessions. For
example, scenario B retained two-room/max-price constraints, selected
`Бусиновский парк`, all three visible options, pending
`selected_live_fact_consent`, and requested/answered compound facts under the
canonical `nmbot_v2` namespace. The initial empty-state statement came from
reading legacy top-level fields. Diagnostics must prefer canonical V2 state and
label legacy root only as compatibility data.

Sources: `scripts/nmbot_api_server.py:844-848,2652-2698`,
`scripts/nmbot_runtime_adapter.py:108-116,152-154,680-695`; bounded VPS session
refs `sha256:a568c1322542e416`, `sha256:870be726eadf360c`,
`sha256:48320915a9830f9c`.

Final readiness verdict remains pending residual H054 gates: independent
shortlist → typo reference → `да`, plus real widget/bridge delivery confirmation.
Deployment itself is no longer pending; H054 is deployed and tested but still
open.
