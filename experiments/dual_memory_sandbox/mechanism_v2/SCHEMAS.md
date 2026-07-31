# mechanism-v2 schemas

Status: `PREPARED_NOT_RUN`. These schemas are contracts only; they do not run a
model, scorer, verifier or fixture.

## Public task card

Closed keys:

- `task_id`
- `partition`: `learning` or `holdout`
- `family_id`: one of `normalize`, `cache`, `boundary`
- `task_kind`: `support` or `holdout`
- `public_problem`
- `route_scope`: holdout cards only; closed natural-language scope prose that
  states in-scope and out-of-scope route dimensions without private terms,
  advice IDs, check-code IDs or hidden answer wording
- `public_artifacts`
- `allowed_check_codes`
- `allowed_advice_codes`: learning cards use the family list; holdout cards use
  an arm-specific map with keys `B0`, `M1`, `S1`
- `forbidden_actions`

Public task cards contain no expected answers, private labels, raw old fixtures,
hidden assertions or outcome data. Holdout `route_scope` is public
disambiguation prose only; it must not be an exact list of private check pairs.

For holdouts, `allowed_advice_codes` must match the schedule-derived receipt
allowlist in `experiment.json`: B0 is empty, M1 is exactly the relevant task
family advice, and S1 is exactly the scheduled disjoint sham-family advice.

## Private label

Closed keys:

- `task_id`
- `family_id`
- `expected_family_route`
- `quality_label_placeholder`
- `blind_scorer_notes`
- `private_expected_check_codes`
- `private_source`: always `safe_synthetic_placeholder`

The private labels are placeholders for future blind scoring. They are not
agent-facing.

## Sealed receipt

Closed keys only:

- `task_id`
- `arm`: `B0`, `M1`, or `S1`
- `consulted_advice_codes`: controlled advice IDs only
- `selected_check_codes`: controlled check IDs only
- `receipt_version`: `mechanism-v2-receipt-1`

The receipt records declared route/check selection. It must not contain hidden
reasoning, raw prompts, raw code, logs, tool arguments, tool outputs, labels,
expected answers, secrets, provider packets or production data.

## Agent route-only result

Future task subagents must return JSON only, with exactly these closed keys:

- `task_id`
- `arm`: `B0`, `M1`, or `S1`
- `selected_check_codes`: subset of the current arm-sliced task checks
- `route_summary`: brief natural language route/check summary only
- `receipt`: exactly the five safe receipt keys listed above

No edits, remediation patches, code, prompt text, hidden thoughts, logs, tool
arguments/outputs, private labels, expected answers or outcome fields are
allowed. The top-level `selected_check_codes` must exactly equal
`receipt.selected_check_codes`. `receipt.consulted_advice_codes` must exactly
match the current run's scheduled allowlist: empty for B0, relevant-family M1
codes for M1, and scheduled unrelated sham codes for S1.

## Sealed run artifact

`seal_result.py` validates a supplied future candidate JSON against the prepared
`run_manifest.json`, immutable source hashes, and current-arm receipt/check
allowlists. It writes only `sealed_result.json` directly under the prepared run
workspace and never accepts an arbitrary output path.

Closed safe contents:

- route-only agent result
- immutable source hashes
- `status=sealed_route_only_result`
- supplied fresh OpenCode session id
- diagnostic timestamps and candidate hash

The sealer does not create any process execution command and does not run an
agent, provider, scorer, fixture, network call or repair path.

## Blind route score artifact

`blind_route_scorer.py` accepts a sealed artifact path and reads private labels
only from the fixed internal `private/labels.jsonl` path. The assessment object
contains only `task_id`, `selected_check_codes`, and `route_summary`; arm and
receipt advice are deliberately excluded so arm cannot influence quality. The
score output contains safe booleans only and never prints private expected
check-code values.

## Aggregate comparison artifact

`aggregate_compare.py` consumes exactly all 18 sealed run summaries plus their
`blind_score.json` files and a supplied JSON list of read-only normalized
OpenCode session aggregates. It validates exact cohort coverage, unique session
ids, common parent/agent/model identity, B0 empty advice, M1/S1 scheduled
receipt allowlists, complete metric coverage, and quality-before-resource pair
comparisons. Missing coverage is `not_evaluable`, never zero. The only permitted
claim is observational.

## Agent-facing prepared packet

Hard prerequisite before any future run: the orchestrator must call the
arm-sliced preparer for exactly one valid `(task_id, arm)`. Public holdout cards
may retain an arm-specific map for static validation, but that map must never be
copied whole to the agent workspace.

Closed top-level keys:

- `packet_version`
- `experiment_id`
- `task`: only public task fields plus the selected arm; holdouts preserve their
  own `route_scope`; B0 omits advice fields,
  M1/S1 include only the current-arm receipt advice allowlist
- `advisory_payload`: `null` for B0, current M1 or S1 payload slice only
- `receipt_contract`: safe closed receipt keys and current-arm/check allowlists
- `preparation_boundary`: `execution_allowed=false` plus current no-run boundary

Closed workspace files:

- `agent_packet.json`
- `run_manifest.json`
- `RECEIPT_SCHEMA.json`

Forbidden from agent-facing prepared packets: private labels, other-arm payloads,
full arm maps, schedule, hidden outcomes, raw prompts, thought, logs and code.

## Static advisory payload manifest

Closed keys only:

- `payload_id`
- `task_id`
- `arm`: `M1` or `S1`; B0 has no advisory payload
- `task_family`
- `advice_family`
- `schedule_role`: `relevant` or `scheduled_unrelated_sham`
- `entries`: each entry has only `code`, `family`, and `safe_summary`

The artifact is `private/advisory_payloads.jsonl`, hash-locked by
`experiment.json`. It must contain exactly one M1 and one S1 row for each
scheduled holdout. M1 entries must equal the relevant-family allowlist, S1
entries must equal the scheduled disjoint sham-family allowlist, entry counts
must match, and the approximate controlled-code-length difference for each
M1/S1 pair must stay within the pre-launch validator bound.

## Mechanism interpretation

Mechanism evidence is `evaluable` only if all are true:

1. Quality scorer is blind to arm.
2. M1 improves quality/wall versus B0.
3. M1 improves quality/wall versus S1.
4. M1 receipt contains relevant allowed family advice and check codes.
5. Receipt coverage exists for the required scheduled runs.

If receipt coverage is missing, record `mechanism_not_evaluable`; do not treat
it as zero use. No composite metric is defined.
