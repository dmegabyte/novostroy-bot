# NMBot version boundary map

**Status:** reference navigation map. It is not a live-selector or release-readiness
claim. Exact contracts remain in
[`NMBOT_RUNTIME_VERSIONS.md`](NMBOT_RUNTIME_VERSIONS.md) and dated selector evidence
in [`NMBOT_RUNTIME_REGISTRY.md`](NMBOT_RUNTIME_REGISTRY.md).

## One rule

All versions share only the Jivo transport, per-session locking, deduplication and
terminal delivery. The selector chooses a version **before** a turn. Everything
after selection — state semantics, search/answer contract, prompts and publication
gate — is owned by that version.

Do not use a successful test, prompt, trace or release from one row as proof for
another row.

## Boundary table

| Version | Owns | Intentional reuse | Must not borrow | State | Minimum proof after a change |
|---|---|---|---|---|---|
| **V0 — Валерия** | isolated two-prompt search/answer flow and its prompts | shared transport only | V1/V2/V3/V4 planner, cards, composer or state | `nmbot_v0` | V0-focused tests and an approved V0 Jivo smoke |
| **V1 — Татьяна** | independent typed TEST planner/search/transition and optional validated final writer | shared transport and code-owned callback flow | V0/V2/V3/V4 state, prompts or publication rules | `nmbot_v1` | V1-focused tests, selector identity and approved V1 smoke |
| **V2 — Ирина** | typed planner, search, canonical cards, ResponsePlan and deterministic renderer | shared transport; optional validated V2 composer only changes wording | V0/V1/V4 semantics or state | `nmbot_v2` | V2 owner tests; release additionally needs fresh selector and correlated Jivo evidence |
| **V3 — Светлана** | `IntentPlanV3`, V3 writer projection and V3 publication mode | V2 engine, `nmbot_v2` state, canonical cards, search and deterministic fallback — deliberately | a copied V2 runtime, a separate state namespace, or authority to reorder cards/search | `nmbot_v2` | V3 selector + V3 trace + V3-focused tests; Jivo evidence for a release claim |
| **V4 — Марина** | isolated one-prompt runtime, prompt, strict JSON validation and fail-closed response | shared transport only | V0–V3 cards, state, writer/composer or release evidence | `nmbot_v4` | V4-focused tests and separately approved V4 diagnostic/smoke evidence |

## Route by task

| If you are changing… | Start here | Keep out of scope |
|---|---|---|
| V0 prompts or fallback | V0 row in `NMBOT_RUNTIME_VERSIONS.md` | V1–V4 tests as evidence |
| V1 typed flow or final writer | V1 row in `NMBOT_RUNTIME_VERSIONS.md` | V2/V3 composer modes and V4 |
| Search, cards, recipes or deterministic answer | V2 row and `nmbot_v2/` owner source | V0/V1/V4 contracts |
| IntentPlanV3 or free V3 writer wording | V3 row and V3-specific composer/validator path | changing V2 card/search ownership |
| One-prompt model/tool cycle | V4 row and `nmbot_v4/` owner source | V2/V3 card/result semantics |
| Jivo delivery, locking, deduplication or callback transport | `NMBOT_OPERATIONS_MAP.md` and API/bridge owners | using transport proof as a version-release gate |

## Four quick checks before editing

1. Which version did the selector choose for this turn?
2. Does the changed file belong to that version, or to intentionally shared
   transport?
3. Which state namespace may be read or written?
4. Which focused tests and which version-specific smoke can prove the result?

If any answer is unclear, stop at the runtime passport and owner source. Do not
infer the active version from this map, local files, old logs or a prior TEST
release.

Sources: [`NMBOT_RUNTIME_VERSIONS.md`](NMBOT_RUNTIME_VERSIONS.md#version-table),
[`NMBOT_RUNTIME_VERSIONS.md`](NMBOT_RUNTIME_VERSIONS.md#architecture-approach-by-version),
[`NMBOT_RUNTIME_REGISTRY.md`](NMBOT_RUNTIME_REGISTRY.md),
[`NMBOT_OPERATIONS_MAP.md`](NMBOT_OPERATIONS_MAP.md).
