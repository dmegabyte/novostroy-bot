# NMBOT runtime versions — V0/V1/V2/V3/V4 separation passport

Purpose: keep V0, V1, V2/V3 and V4 mentally and technically separate. V0, V1 and V2 are separate products/contracts, not stages of one pipeline. V3 is a selector identity for the V2 typed runtime with the `IntentPlanV3` semantic contract. V4 is an isolated one-prompt runtime with its own state and strict JSON response contract. Evidence from one version must not be counted as another version's release gate.

Strict rule: docs can describe contracts, but only the live selector says what the current process is running. Check `GET /api/runtime-version` first; use `data/nmbot_runtime_version.json` only as persisted selector state for the next/current process startup.

## Version table

| Area | V0 | V1 | V2 | V3 |
|---|---|---|---|---|
| Entry/runtime | `nmbot_v0/runtime.py`; selected through `scripts/nmbot_runtime_adapter.py` V0 path | `nmbot_v1/*`; selected through adapter/API as `V1` when enabled in TEST selector paths | `nmbot_v2/*`; selected through `scripts/nmbot_runtime_adapter.py` V2 path | `nmbot_v2/*` with `IntentPlanV3` transition contract; selected through adapter/API as `V3` |
| Planner/search/answer architecture | Independent opt-in two-prompt runtime: `scenario_search(context)` may use MCP-shaped search, then `answer(brief)` writes only from the validated brief | Independent typed planner/search/transition runtime that builds a code-owned `ResponsePlan`; optional one-model GPT-5.5 final text can replace only after strict validation | Newer typed runtime: planner + typed pending kernel + dynamic fact normalization + deterministic response contracts | V2 typed runtime plus `IntentPlanV3`: planner returns one goal/viewpoint/constraints/requested facts; runtime derives mechanical transition and deterministic response |
| State namespace | `nmbot_v0` | `nmbot_v1` | `nmbot_v2` | `nmbot_v2` |
| Prompts/contracts | `prompts/v0_scenario_search.txt`, `prompts/v0_answer_writer.txt`, `nmbot_v0/contracts.py` | V1 contracts under `nmbot_v1/*`; one-model GPT prompt ID `p_df271e92f355` was used only in controlled TEST smoke | V2 contracts under `nmbot_v2/*`, follow-up classifier, V2 search/response docs | `nmbot_v2/contracts.py::IntentPlanV3`, `nmbot_v2/semantic_planner.py`, `nmbot_v2/transition.py` |
| Client-facing name | Валерия | Татьяна | Ирина | Светлана |
| Local tests | `PYTHONPATH=. pytest tests/test_nmbot_v0_runtime.py tests/test_nmbot_v0_test_harness.py` | V1 tests and TEST Jivo smoke must prove selector identity, public projection and fallback validation; V2/V3/V4 evidence is not proof | V2 suites must exclude V0/V1, for example `PYTHONPATH=. pytest -k 'not v0 and not v1'` | V3/Jivo suites must prove selector identity plus V2 typed runtime behavior; V0/V1 tests are not proof |
| Release gate | V0 has its own focused harness, remote compile/smoke, first Jivo trace check, then restore original runtime when it was only a smoke | V1 GPT publish requires separate release-quality evidence; the 2026-07-30 GPT smoke is TEST-only fallback proof, not publish readiness | V2 release gate must not include V0/V1 tests/fixtures as proof; use V2/Jivo suites and live selector evidence | V3 gate must include live selector evidence, `schema_version=3` trace evidence and Jivo `BOT_MESSAGE`; docs alone are not proof |
| Known status | V0 is opt-in. It was not modified in the 2026-07-21 V2-only work described below | V1 has a supported TEST architecture and guarded GPT-5.5 flag path; do not infer that the current live selector is V1 from this document | V2 is the newer typed runtime. Do not infer it is live from docs; live production is determined only by `/api/runtime-version` and the persisted selector file | V3 is represented in runtime logs and `/start_3`/selector paths, but shares the V2 namespace/runtime implementation; do not treat it as a separate V0-style product. In TEST, selected availability requests exact `lot_examples` enrichment and confirms only normalized lots with ID and active/in-sale status; release `nmbot-v3-lot-availability-test-20260730-1015` produced `availability_evidence.confirmation=confirmed` in one smoke. |

### V4 local supplement — isolated one-prompt runtime (Марина)

| Area | V4 |
|---|---|
| Entry/runtime | `nmbot_v4/*`; selected through `scripts/nmbot_runtime_adapter.py` V4 path |
| Architecture | One gateway-agent request per turn to `google/gemini-3.6-flash`; the model may call `novostroym/get_flat_info` inside that request |
| State namespace | `nmbot_v4` |
| Prompt/response contract | `prompts/v4_flat_search.txt`; final answer is strict JSON with exactly numeric `data` IDs and Russian `message` |
| Safety boundary | Code validates shape, ID types/deduplication/limit, Russian message and fail-closed JSON; raw MCP tool evidence is not exposed by the current gateway result contract |
| Local gate | Focused V4 selector/runtime plus V1/Jivo regressions; read-only review |
| Known status | Locally implemented and reviewed on 2026-07-30; not provider-, MCP-, VPS- or Jivo-verified and not proven live |

## Architecture approach by version

All five passport versions use the same Jivo transport, per-session lock, deduplication
and terminal `BOT_MESSAGE` delivery. The selector chooses one version before a
turn; the chosen version owns the dialogue semantics and client-facing text.
The shared transport must not be used as a reason to mix version contracts.

### V0 — isolated two-prompt runtime (Валерия)

```text
Jivo turn
  → V0 scenario_search(context)
  → validated scenario/search brief
  → canonical V0 answer material
  → optional V0 Answer Writer plain text / deterministic fallback
  → BOT_MESSAGE
```

- V0 uses its own `nmbot_v0` namespace, prompts and contracts.
- Its model-facing stages are `scenario_search` and the optional Answer Writer;
  the writer receives only validated material, current client meaning and
  bounded conversational context, not raw search/provider output.
- The Answer Writer speaks as Валерия and may improve wording only. Scenario,
  facts, selected object, action, CTA, operator routing and state remain
  code-owned. Empty/error/overlong writer output uses deterministic fallback.
- V0 has no V2/V3 `response_composer` publication path. V0 changes require V0
  fixtures and a V0 Jivo smoke; V2/V3 evidence does not cover it.

### V1 — independent typed TEST runtime (Татьяна)

```text
Jivo/API turn
  → V1 typed planner/search/transition
  → code-owned ResponsePlan
  → deterministic public projection
  → optional GPT-5.5 final text candidate
  → strict validation or deterministic fallback
  → BOT_MESSAGE
```

- V1 owns its own `nmbot_v1` namespace, planner/search/transition contracts and
  `ResponsePlan`. It is not V2/V3 and not V4.
- `NMBOT_V1_ONE_MODEL_GPT55_MODE=off|shadow|publish` is the only supported V1
  GPT-5.5 publication flag. Default is `off`. The flag values are exact; other
  values must fail closed.
- In `publish`, GPT-5.5 can replace only the already-built final public text and
  only after strict validation. Empty output, validation failure, timeout,
  provider error or exception preserves the deterministic text.
- Phone terminal callback flow is code-owned and bypasses the model. The model
  cannot own callback state, phone capture, terminal delivery or operator path.
- This section describes supported TEST architecture. It is not a claim that the
  current selector is V1 and not a claim that GPT publish is release-ready.

### V2 — typed runtime and executable response plan (Ирина)

```text
Jivo turn
  → semantic planner
  → typed state + pending-action transition
  → search/enrichment + canonical OptionCard normalization
  → scenario recipe → ResponsePlan
  → deterministic renderer
  → optional validated response_composer → BOT_MESSAGE
```

- The planner owns the semantic interpretation; code owns state transition,
  search route, card order, recipe, anchors and CTA.
- The deterministic renderer is always built first and is the safe fallback.
- `NMBOT_V2_RESPONSE_COMPOSER_MODE=off|shadow|publish` only controls wording:
  `shadow` observes a valid candidate without publishing it; `publish` may
  replace the deterministic text only after mechanical validation. The composer
  never owns route, recipe, option order, anchor or CTA.

### V3 — V2 typed runtime plus IntentPlanV3 semantic contract (Светлана)

```text
Jivo turn
  → IntentPlanV3 (one goal, viewpoint, constraints, requested facts)
  → typed validation
  → mechanical goal → stage/action transition
  → shared V2 search/cards/ResponsePlan/renderer path
  → optional validated response_composer → BOT_MESSAGE
```

- V3 deliberately reuses the `nmbot_v2` state namespace, card mechanics,
  search engine and response contracts; it is not a copy of V0.
- `IntentPlanV3` owns semantic intent only. It does not mutate state, choose an
  MCP endpoint or write the client answer; typed code validates it and performs
  the transition.
- `NMBOT_V3_RESPONSE_COMPOSER_MODE=off|shadow|publish` is independent from
  the V2 flag. Its publication/fallback rules are identical to V2, but V3 must
  still be smoke-tested through V3 selector evidence and a V3 trace.
- In the V3 writer path, a code-built readable `V3_ANSWER_BRIEF` may carry the
  current request, bounded safe dialogue context, human-readable constraints,
  canonical cards, confirmed and missing facts. It is derived only from the
  validated V2 response brief; raw MCP payloads, contacts and secrets are not
  writer input.
- The V3 writer may freely choose factual emphasis, comparison and a natural
  final question. It cannot alter canonical card identity/order/count, invent
  facts or numbers, start another search, or change routing. A V3-only
  mechanical validator and deterministic V2 renderer remain the publication
  boundary and fallback.
- For selected availability, V3 reuses the existing exact-name enrichment owner:
  `apartment_inventory` is mapped to `facts_needed=("lot_examples",)` for the
  request, while the public semantic fact name is preserved. Only normalized
  `LotExample` records with a positive ID and active/in-sale status can mark
  availability as fresh; model-produced inventory and unknown statuses remain
  `not_confirmed`.

### V4 — isolated one-prompt runtime (Марина)

```text
Jivo/API turn
  → V4 selector and isolated `nmbot_v4` state
  → one gateway-agent request with one system prompt
  → Gemini tool cycle with `novostroym/get_flat_info`
  → strict code validation of `{data,message}`
  → BOT_MESSAGE
```

- V4 calls `_run_gateway_request_once`; shared model retry/fallback is not used.
- Prompt loading is lazy per V4 turn, so a missing prompt fails closed without
  preventing the API or other runtimes from starting.
- The current gateway response contract does not provide raw MCP results to the
  V4 validator. Therefore V4 may record prompt/model provenance, but must not
  claim code-level proof that returned IDs appeared in raw tool output.
- Local tests and review are development evidence only. A separately approved
  first diagnostic request must stop on its first failure and cannot establish
  production status without the normal VPS/Jivo release proof.

### Composer and fallback boundary for V1/V2/V3

V1, V2 and V3 each have their own publication flag and validation boundary. V1
uses `NMBOT_V1_ONE_MODEL_GPT55_MODE`; V2 uses its scenario-bound
writer/formatter contract. V3 may use its dedicated `V3_ANSWER_BRIEF` and
`prompts/v3_answer_writer.txt` contract; this does not mean any version
automatically publishes model prose. The effective
per-version mode is selected by the adapter for the current turn.
When the mode is `off`, the composer is not called; in `shadow`, its candidate
is retained only in safe diagnostics; in `publish`, a non-empty result is
published only after eligibility plus JSON/mechanical validation. Any timeout,
provider error, empty result, exception or contract violation preserves the
already-built deterministic response.

To establish the **current live** mode, check the VPS runtime marker and the
terminal dialogue trace; this document describes supported architecture, not a
substitute for live evidence.

## Quality baselines

Эталоны версий независимы и не взаимозаменяемы:

- V0: `docs/NMBOT_V0_QUALITY_BASELINE.md` — `v0-baseline-20260721`;
- V2: `docs/NMBOT_V2_QUALITY_BASELINE.md` —
  `v2-baseline-20260721-family-enrichment-repair`.

При сравнении новой версии сначала выбирается её собственный baseline. Результат
V0 не повышает и не понижает V2-оценку; результат V2 не повышает и не понижает
V0-оценку.

## Display names

- `V0` → **Валерия**;
- `V1` → **Татьяна**;
- `V2` → **Ирина**;
- `V3` → **Светлана**;
- `V4` → **Марина**.

Это только имя версии в prompt-facing тексте. Технические идентификаторы,
namespace и команды переключения не меняются.

Note: if the selector file is absent, code may fall back to V2. That fallback is not the same thing as a live production status claim.

## Shared boundary

Only these layers are intentionally shared:

- transport/API: `scripts/nmbot_api_server.py`;
- runtime selector/adapter: `scripts/nmbot_runtime_adapter.py`;
- search-result safety normalization and report-only business validation:
  `nmbot_v2/search_contract.py` is called by V0 and by the common V2/V3 search
  engine; policy changes here require explicit V0, V2 and V3 regression gates;
- protected runtime-version endpoints: `GET /api/runtime-version` and `POST /api/runtime-version`;
- one active version per API process.

Everything after the selector is version-owned except the shared search boundary
listed above. V0 files, prompts, tests and fixtures must not be changed for V2
work. A shared search-policy change is allowed only when the task explicitly
names all affected versions and runs each version's release gate. Other V2
pending/evidence/card-normalizer changes must not be copied into V0.

## Per-session commands

Jivo can select a runtime for the current chat without changing the global
selector:

- `/start_0` — reset the current chat's `nmbot_v0` namespace and use V0 for
  subsequent turns in that chat;
- `/start_1` — reset the current chat's `nmbot_v1` namespace and use V1 for
  subsequent turns in that chat when the TEST selector path is enabled;
- `/start_2` — reset the current chat's `nmbot_v2` namespace and use V2 for
  subsequent turns in that chat;
- `/start_3` — reset the current chat's `nmbot_v2` namespace and use V3 with
  the `IntentPlanV3` semantic contract for subsequent turns in that chat;
- `/start_4` — reset the current chat's `nmbot_v4` namespace and use the
  isolated V4 one-prompt runtime for subsequent turns in that chat;
- `/start` — reset the namespace of the global active version and clear the
  per-session override.

The override is stored in the current session envelope as
`runtime_version_override`. It does not change `data/nmbot_runtime_version.json`
and cannot switch other chats. The global protected API remains the only
mechanism for changing the default version for new/non-overridden sessions.

## Runtime attribution in logs

Every new canonical Jivo journal event records the effective
`runtime_version` (`V0`, `V1`, `V2` or `V3`) selected before that turn. User and bot
rows for one event receive the same version; explicit `/start_N` lifecycle rows
use the target version.

Historical rows remain immutable because `dialogue_journal.jsonl` is append-only.
`scripts/backfill_dialogue_runtime_versions.py --write` creates the separate
`logs/dialogue_runtime_versions_backfill.jsonl` index. Only deterministic
evidence is used. Rows without enough selector history are labelled `UNKNOWN`,
not assigned to a version by guesswork. `scripts/nmbot_dialogue_report.py`
automatically merges this sidecar and prints both `runtime_version` and
`runtime_version_source`.

Each new journal row also records `release_id`: the immutable source-bundle
identity from `data/nmbot_release_identity.json`. It is intentionally separate
from `runtime_version`: one release can support several runtime versions and a
session override can select a different runtime without changing the deployed
source bundle. See `docs/NMBOT_RELEASE_IDENTITY.md`.

## Switching protocol

1. Backup the current selector file and touched runtime files before switching.
2. Read the active process version:

   ```bash
   curl -fsS -H "Authorization: Bearer $NMBOT_API_TOKEN" \
     http://127.0.0.1:8088/api/runtime-version
   ```

3. Read persisted selector state:

   ```bash
   cat data/nmbot_runtime_version.json
   ```

4. Use `POST /api/runtime-version` only with explicit approval for a controlled smoke. A Jivo client message cannot switch runtime.
5. After the first request/message, inspect the first trace/log immediately. If it fails, stop the batch and debug that layer first.
6. Restore the original version after an isolated smoke unless the task explicitly says to leave the switch in place.

Live endpoint wins for the current process. The persisted file may briefly differ around restart or controlled switch moments.

## Evidence from 2026-07-21 session

- V2-only suite: 780 passed, 20 V0 tests deselected.
- Isolated V2 Jivo smoke passed the exact double-yes contact flow.
- Live VPS persisted runtime was explicitly checked as V0 in that session, while the V2 smoke was isolated separately.
- V0 files/prompts/tests were not modified.
- Live Jivo smoke: `/start_2` reported V2 and completed a V2 search; `/start_0`
  then reported V0 in the same chat; global selector remained V0 and no fresh
  error events appeared.
