# NMBot V6-only root

This repository contains one bot runtime: V6. Do not add another `nmbot_v<digits>`
package, selector, fallback, prompt set, or compatibility adapter.

## North star and document routes

The product outcome is: useful grounded help, one clear next step, then a
specialist only when data is insufficient or the client agrees. Product work is
bounded by `docs/NMBOT_PRODUCT_CONTRACT.md` and its five scenarios.

Open only the owner document needed for the task:

| Need | Source of truth |
|---|---|
| product outcome, scenarios, non-goals | `docs/NMBOT_PRODUCT_CONTRACT.md` |
| current runtime/component map | `docs/CURRENT_ARCHITECTURE.md` |
| Jivo, gateway, CRM and privacy boundaries | `docs/NMBOT_EXTERNAL_CONTRACTS.md` |
| branch, build, promotion and stop/go | `docs/NMBOT_VERSION_BUILD_PLAN.md` |
| operational commands and rollback | `docs/NMBOT_RUNBOOK.md` |
| accepted/superseded choices | `docs/DECISIONS.md` |

## Mandatory anti-cycle brief

Before any mutation, write one compact Change Brief: final outcome, affected
product scenario, Actual, Desired, one owner layer, impact chain, non-goals,
allowed paths, checks and measurable Definition of Done. Inspect neighbouring
stages before fixing the first visible symptom.

One product task has one scenario, one owner layer and one short branch. If the
first failure changes the owner layer or requires topology, release tooling or
incident repair, reject that release and preserve a read-only failure map. Do
not add the repair, validator, retry, script or second owner to the same branch.

Cycle budget for one product release: one source SHA, one artifact build, zero
incident repairs in the branch, one TEST smoke, then 5/5 product scenarios, and
one primary smoke. A failed gate ends that release; the next attempt uses a new
commit and release ID. Recovery worktrees are evidence, not release sources.

## Minimal implementation rules

- Optimize for readability and explicit data flow, not minimum line or function count.
- Keep one decision in one cohesive owner; do not mix routing, state, evidence,
  delivery and presentation merely to reduce function count.
- Apply DRY to shared meaning and change ownership, not every repeated syntax fragment.
- Prefer small local duplication over an abstraction that couples unrelated V6 paths.
- Do not add a future-facing interface, adapter, hook, generic helper or config
  point without a current call site and acceptance need.
- Treat the Rule of Three as a heuristic only; generalize repeated semantically
  identical cases, not superficially similar code.
- Before adding a layer, state which current duplication, dependency, state or
  failure path it removes; otherwise keep the direct path.
- Prefer explicit data structures and named steps over flags, callbacks, hidden
  globals and optional-argument matrices.
- Delete obsolete paths before adding replacements when ownership and rollback
  are proven; do not preserve code only “for later”.
- Choose the version easiest to explain, test, trace and change.

## Safety

- TEST and PROD are exact, independent profiles of the same immutable artifact.
- TEST CRM delivery is always disabled in code.
- A release ID is immutable. Never overwrite an existing artifact or extracted release.
- Prepare and verify an inactive A/B slot before changing a route. Keep the previous
  slot warm for rollback.
- Local files and tests do not prove production state. Any live check, release,
  activation, rollback, or deploy requires its own explicit authorization.
- Never print or commit secrets, dotenv values, raw dialogue, phone numbers, or Jivo
  payloads.
- Do not run eval or paid model/provider calls without explicit authorization.

## Verification

Use the smallest focused local check that proves the changed owner contract. Before a
release, build the exact artifact, run isolated startup admission, and verify identity,
profile, routes, and hashes.
