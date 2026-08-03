# NMBot version process isolation

Status: target architecture; Phase 1 is local-only and is not connected to Jivo
or the production API.

Scope: V0, V1, V2 and V3. V4 intentionally remains on the legacy route until
it receives a separate migration decision and release gate. The Phase 1 router
must reject V4; it must never silently redirect V4 to another runtime.

## Actual / Contract / Desired

### Actual

- `scripts/nmbot_api_server.py` creates one state store, provider client,
  selector, locks and callback outbox for every runtime.
- `scripts/nmbot_runtime_adapter.py` imports and dispatches V0, V1, V2, V3 and
  V4 in one Python process.
- V3 uses the V2 engine and `nmbot_v2` state namespace.
- V0 imports parts of the V2 search contract.
- All versions write to common state and journal files.

### Contract

V0, V1, V2 and V3 are independent products. Evidence, tests and release gates
from one version do not prove another version. The Jivo bridge and a minimal
version selector may be shared; dialogue semantics, state, prompts, provider
configuration and release identity are version-owned.

### Desired

Each version runs as an independent process with its own package/import closure,
entrypoint, port, environment, state file, logs, immutable release and rollback.
A thin router selects exactly one endpoint before a turn. It never imports a
runtime package and never falls back to a different version.

## Target topology

```text
Jivo
  -> NMBot bridge
  -> version router
       -> V0 service
       -> V1 service
       -> V2 service
       -> V3 service
```

The shared internal boundary is `nmbot.runtime-wire.v1`. Endpoint addresses and
authentication values are configuration, not model or runtime state.

The selector has no implicit default. A missing, unreadable or malformed
selector state makes chat/reset unavailable until an authorized selector PUT
atomically initializes a supported V0–V3 value.

## Ownership

| Resource | Router | V0 | V1 | V2 | V3 |
|---|---|---|---|---|---|
| Version selection | owns | no | no | no | no |
| Dialogue semantics | no | owns | owns | owns | owns |
| State | selector only | own file | own file | own file | own file |
| Prompts/models | no | owns | owns | owns | owns |
| MCP/provider client | no | owns | owns | owns | owns |
| Runtime logs | routing only | own dir | own dir | own dir | own dir |
| Release/rollback | own release | own release | own release | own release | own release |

Forbidden coupling:

- a runtime importing another runtime's business package;
- shared mutable state or a common dialogue journal;
- silent fallback from one version endpoint to another;
- one version's health, tests or release identity proving another version;
- V3 continuing to persist state in the V2 namespace after migration.

Allowed shared code is limited to stable technical contracts: wire validation,
redaction primitives and transport-neutral error codes. Search, normalization,
planner, response and evidence policy are not shared merely for convenience.

## Migration phases

1. **Wire and router (local-only).** Introduce the closed runtime contract and
   router. Keep the current API/Jivo route unchanged.
2. **Independent service shells.** Add one entrypoint, state file, log directory,
   health endpoint and release identity per version. Initially they may wrap the
   existing version behavior, but an import-closure test must show which common
   modules remain.
3. **V1 extraction.** V1 already owns most of its contracts and is the smallest
   process-isolation candidate.
4. **V0 extraction.** Move V0 search normalization into V0 ownership and remove
   business imports from `nmbot_v2`.
5. **V2 extraction.** Move the current typed V2 runtime and its adapters into the
   V2 service package.
6. **V3 fork.** Create an independent `nmbot_v3` package, state schema, prompts,
   search/evidence owners and migration adapter. No V2 state file is reused.
7. **Shadow routing.** Start services without client traffic; compare closed
   contract responses and state effects with the legacy API.
8. **One-version cutover.** Route one version at a time with its own immutable
   rollback. Never cut over all versions in one release.
9. **Legacy removal.** Remove the common dispatcher only after all four services
   have independent live evidence.

## Acceptance criteria

- Stopping or deploying V3 does not restart or modify V0/V1/V2.
- Every service has a unique state path, log path, port, environment file and
  release identity.
- Router sends one request to exactly one version and performs no retry to a
  different version.
- Runtime response version must equal the requested version or the router fails
  closed.
- Import-closure tests reject `nmbot_vX -> nmbot_vY` business imports.
- Per-version tests, health and smoke evidence are reported separately.
- Rollback of one version changes only that version's endpoint/release.

## Phase 1 files

- `nmbot_runtime_contract/wire.py`
- `scripts/nmbot_version_router.py`
- `config/nmbot_version_routes.json`
- `tests/test_nmbot_version_isolation_phase1.py`

These files are not yet part of the production route.

## Phase 2 status

V1 is the first extracted service shell. It is local-only and is not registered
in the production router.

- entrypoint: `scripts/nmbot_v1_service.py`;
- business owner: `nmbot_v1/service.py` and `nmbot_v1/*`;
- shared technical host: `nmbot_runtime_service_host/http.py`;
- dedicated target root: `/home/neiro/novostroy-bot-v1`;
- dedicated state: `/home/neiro/novostroy-bot-v1/data/state.json`;
- dedicated journal: `/home/neiro/novostroy-bot-v1/logs/runtime.jsonl`;
- dedicated unit: `nmbot-v1-runtime.service`;
- loopback port: `18081`.

The V1 worker does not import the common runtime adapter or another version's
business package. Its phone/callback path deliberately fails closed with
`v1_phone_flow_unmigrated`; therefore V1 is not ready for traffic cutover yet.
This gap must be migrated and tested inside V1 ownership before shadow routing.

V0 now has a local-only independent package and service shell:

- V0 owns `OptionCard`, `LotExample`, `SearchResult` and its search validation
  in `nmbot_v0`; `nmbot_v0` has no business imports from V1/V2/V3;
- entrypoint: `scripts/nmbot_v0_service.py`;
- dedicated target root: `/home/neiro/novostroy-bot-v0`;
- dedicated state and journal under that root;
- dedicated unit: `nmbot-v0-runtime.service`;
- loopback port: `18080`.

V0 is also not ready for traffic cutover. Its production gateway task protocol,
optional answer writer and CRM phone callback still need migration into V0
ownership. Until then gateway/phone paths remain disabled or fail closed. The
isolated V0 suite and all eight deterministic harness scenarios pass locally;
this is not production or cutover evidence.

V3 now has a local-only independent process shell for Phase 2.3a:

- entrypoint: `scripts/nmbot_v3_service.py`;
- business owner: `nmbot_v3/*`, with a V3-only contract and direct V3 state;
- dedicated target root: `/home/neiro/novostroy-bot-v3`;
- dedicated state and journal under that root;
- dedicated unit: `nmbot-v3-runtime.service`;
- loopback port: `18083`.

The shell accepts only an injected `plan_v3` planner port and fails closed for
phone, missing planner and malformed planner output. Provider, search,
composer, callback and behavior parity have not migrated; V3 is not cutover
ready. Phase 2.3b remains responsible for those owners and any parity evidence.

Phase 2.3b-1 additionally extracts the semantic core into V3 ownership:

- `nmbot_v3/contracts.py` owns `IntentPlanV3`, semantic goal/stage/action types
  and a closed privacy-safe planner context;
- `nmbot_v3/semantic_planner.py` owns schema and state validation without
  changing the planner goal;
- `nmbot_v3/transition.py` owns the mechanical goal and pending-reply transition
  matrices;
- `tests/test_nmbot_v3_planner_core.py` records the isolated parity contract.

This semantic core is deliberately not connected to `nmbot_v3/runtime.py` yet.
Connecting it before V3 owns evidence/search and presentation would create a
partly executable contract. Provider, search, composer and callback remain
unmigrated, so this increment does not establish behavior parity or cutover
readiness.

## Sources

- `docs/NMBOT_RUNTIME_VERSIONS.md:1-18,105-123,205-221`
- `scripts/nmbot_runtime_adapter.py:18-49,303-353`
- `scripts/nmbot_api_server.py:2790-2801,2937-3024,3607-3628`
