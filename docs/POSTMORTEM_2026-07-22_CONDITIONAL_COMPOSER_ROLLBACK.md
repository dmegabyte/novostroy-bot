# Postmortem: conditional composer rollout and rollback — 2026-07-22

## Status

- Incident date: 2026-07-22.
- Production channel: Jivo API + n8n bridge.
- Final state: conditional composer bundle rolled back; V3 composer returned to `shadow`; V0, V2 and V3 search succeeded on the first post-rollback smoke; API and bridge healthy.
- Eval was not run.

## User-visible symptoms

1. V3 model-written shortlist became poorer than the deterministic answer:
   - project names remained;
   - location, price and other useful card facts disappeared;
   - the answer kept mostly scenario benefits.
2. V0 twice answered with an operator handoff instead of a rental shortlist.
3. V2 and V3 rental searches returned a safe upstream fallback or did not finish promptly.

## Confirmed timeline and evidence

- Production backup before the conditional composer bundle:
  `/home/neiro/novostroy-bot/backups/deploy-20260722T141223Z-conditional-composer-10files`.
- V0 failures at approximately 14:21 and 14:22 UTC:
  `malformed_scenario_output`, `invalid_strict_json`.
- V2 failures at approximately 14:23–14:25 UTC:
  `v2_search_parse_failed`, then gateway timeout and exhausted search fallback.
- V3 rental failure at approximately 14:26 UTC occurred in the shared V2/V3 search path.
- A similar V2 gateway timeout had already occurred at approximately 14:01 UTC, before the conditional composer deployment.
- After full rollback, the exact request `двушка под сдачу` succeeded on the first smoke in V0, V2 and V3.
- After rollback:
  - API active, health endpoint returned `ok`;
  - bridge remained active and was not restarted;
  - fresh non-validation error count was zero.

Production sources:

- `/home/neiro/novostroy-bot/logs/dialogue_journal.jsonl`
- `/home/neiro/novostroy-bot/logs/bot_error_events-2026-07-22.jsonl`
- backup path above

## Actual / Contract / Desired

| Layer | Actual | Contract | Desired |
|---|---|---|---|
| V3 presentation | Model answer preserved names and scenario angles but omitted location, price and useful card facts | First shortlist must contain grounded real fields and a useful mini-benefit | Keep name, location, price when present and one distinct scenario angle |
| V0 search | Scenario model returned malformed strict JSON twice | V0 search must either return structured cards or a controlled fallback | A presentation rollout must not alter or endanger the V0 search path |
| V2/V3 search | Parse failure, timeout and fallback exhaustion occurred before answer composition | Search and answer composition are separate runtime layers | Presentation changes must leave search/gateway behavior unchanged |
| Release scope | One bundle crossed presentation, shared runtime, gateway and operational configuration | Change surface must match the owning layer | Deploy an isolated presentation-only delta from the production baseline |

Product contracts:

- `docs/IDEAL_IRINA_UX.md:63-78`
- `docs/NMBOT_RUNTIME_VERSIONS.md:43-59`
- `docs/NMBOT_V2_ANSWER_QUALITY_GATE.md:130-144`

## Causality assessment

### Definitely caused by the composer publication

- V3 client copy lost location, price and other useful facts.
- The model-facing material and writer behavior favoured one scenario angle over a self-contained factual card.
- The published answer therefore violated the minimum card contract even though it was visually cleaner.

### Independent upstream failures

- V0 cannot invoke the V2/V3 composer; its malformed scenario JSON was upstream of presentation.
- V2 composer mode was off; its parse failure and gateway timeout occurred in search.
- V3 uses the shared V2/V3 search path; its rental failure occurred before composition.
- The pre-deployment V2 timeout proves that the composer deployment was not the sole cause of search instability.

### Correlation that is not proven as causation

- The rollout replaced shared `runtime_adapter` and `gateway_client` files, so it increased the risk surface for every version.
- The error codes themselves point to model/gateway output instability, not to a proven deterministic bug in the composer.
- The first successful post-rollback requests show recovery, but do not by themselves prove that every upstream failure was introduced by the rolled-back bundle.

## Process failures

1. **Mixed ownership in one bundle.**
   A presentation change included shared runtime, gateway and environment tooling.
2. **Deployment from an accumulated shadow workspace.**
   The bundle required `bluesminds_answer_interceptor.py`, which was absent on production. This proved the package was not a clean presentation-only delta.
3. **Shadow checked status, not candidate quality.**
   `composer_used=true` did not prove that the hidden answer retained mandatory client facts.
4. **Insufficient scenario matrix.**
   Publish was attempted without an exact V0/V2/V3 × family/rental × first-list/selected gate.
5. **Acceptance criteria were weakened during rollout.**
   Readable prose was accepted even though location and price were missing.
6. **The release manifest was broader than the requested feature.**
   File hashes were checked, but the semantic ownership of every included file was not independently approved.

## What the rollback removed

- V3 publication of model-written responses.
- Conditional Gemini/Ling response formatting path.
- New writer and formatter prompts.
- Shared runtime/transport wiring included in the deployment bundle.
- Documentation specific to that conditional publication path.

## What remained

- `nmbot_v2/scenario_field_mechanics.py`.
- Extended `OptionCard` contract.
- Card normalizer and search contract.
- Scenario mechanics and WritingPlan design.
- The local implementation and production backup remain available for a clean redesign; they must not be redeployed as the same broad bundle.

## Mandatory rules for the next rollout

1. **Presentation changes must not modify search or gateway behaviour.**
2. Start from the exact production baseline, not from the accumulated shadow workspace.
3. Produce an incident-specific manifest grouped by ownership:
   - presentation-only;
   - shared runtime;
   - search;
   - gateway;
   - operations/configuration.
4. A presentation rollout is blocked if the manifest contains search/gateway files without a separate approved reason.
5. V0 remains untouched until it has its own safe model-facing adapter.
6. Shadow must create a private deterministic-versus-candidate comparison containing:
   - required facts present/missing;
   - card count and order;
   - final CTA;
   - safety warnings;
   - candidate text in a protected diagnostic artifact.
7. Required card floor:
   - exact project name;
   - location when present;
   - safe price when present;
   - one grounded scenario angle;
   - no unsupported availability, keys, move, repair, budget-fit, demand or yield claims.
8. Before publish, run sequentially and stop on first failure:
   - V0 family first-list;
   - V0 rental first-list;
   - V2 family first-list and selected object;
   - V2 rental first-list and selected object;
   - V3 family first-list and selected object;
   - V3 rental first-list and selected object.
9. Publish only the target version. Other versions must retain their prior hashes and visible behaviour.
10. Immediate rollback triggers:
    - any search regression in an untouched version;
    - loss of mandatory card facts;
    - new startup dependency;
    - first fresh runtime error;
    - candidate text worse than deterministic baseline.

## Recommended next route

1. Freeze current production on the restored deterministic path.
2. Create a clean presentation-only integration from the production baseline.
3. Keep search, gateway, state and V0 outside the change surface.
4. Add a protected shadow comparison report before any publish attempt.
5. Pass the complete version/scenario/stage matrix above.
6. Obtain a separate user approval after showing the exact candidate responses.
7. Only then enable V3 publish; consider V2 separately afterward.

## Final lesson

A better-written answer is not a successful release if it loses useful facts or expands the failure surface of unrelated versions. Presentation quality, search reliability and deployment isolation are separate gates; all three must be green independently.
