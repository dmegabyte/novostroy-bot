# NMBot context workflow production roadmap

Status: authoritative checklist for productionizing the local developer context
workflow only. This roadmap changes no runtime, gate, selector, prompt, model,
provider, registry, config, network, VPS, deploy, production state or eval by
itself.

This roadmap is the NMBot pilot branch of the multi-project plan in
`docs/MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md`; the shared contract
remains `docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md`.

## Scope boundary

This roadmap applies only to the local agent/developer workflow for choosing and
validating project context before source reading. It is not an NMBot client-facing
runtime feature, not production VPS behavior, and not permission for autonomous
code edits.

Allowed scope: local navigation, privacy-safe telemetry, supervised target
confirmation, strict STOP-2 gate validation, local acceptance evidence and
rollback to the current manual `navigate -> strict gate` workflow.

Out of scope: customer chat runtime, Jivo/VPS behavior, model/provider selection,
prompt changes, runtime gates, selector activation in production, config or
registry migration, network calls, deploy, eval, and automated code modification.

## Current baseline

| Area | Current status | Exact journal metrics / boundary |
|---|---|---|
| Strict explicit target | Ready for local developer work when a session names an exact stage, symbol or owner docs anchor | `26/26` valid explicit targets passed; `2/2` invalid targets failed closed; four long source spans stopped at the 80-line envelope instead of claiming false completion |
| Free Russian target selection | Supervised only | `19/22` candidate targets were found; `16/22` full gate paths were correct; not autopilot and not a Russian-language classifier |
| FTS cards and source-card variants | Candidate-only | Source-card blind v3 baseline/card routes both had `H@1/H@3 0.300` and `MRR 0.325`; contextual FTS changed baseline `H@1/H@3/R@8 76.923/92.308/78.205%, MRR 83.333%` to `69.231/92.308/82.051%, MRR 78.205%`, so no quality gain is proven |
| Adaptive selector / experience bank | Forbidden for behavior | H1-v3 valid fail: target recall improved `18/22 -> 19/22`, full strict hits stayed `6/22`; H2 structural pass only; H3 not evaluable; H4 blocked; approved general patterns: none |

## Phases and checklist

### P0 — Freeze contracts and instrumentation

- [ ] Reconfirm `docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md` as the STOP-2,
  strict target, privacy and fail-closed source of truth.
- [ ] Reconfirm `docs/NMBOT_RETRIEVAL.md` as the deterministic navigate, FTS and
  candidate-only route source of truth.
- [ ] Freeze telemetry schema below before any fresh task collection.
- [ ] Add only local documentation/test instrumentation needed to verify fields;
  do not alter runtime, gate, selector, config, prompt, model, network or deploy.
- [ ] Define one rollback flag or command path that restores the current manual
  `navigate -> strict gate` workflow without migration or deletion.

### P1 — Shadow rollout on fresh real developer tasks

- Status note: passive `privacy_safe_shadow_outcome.v1` mechanics are locally
  implemented for append/validate/list/summary only, but P1 still has `0` fresh
  real developer tasks and no selector activation.
- [ ] Collect at least `30` fresh real developer tasks, separate from previous
  observed sets.
- [ ] Do not invoke the gate automatically; record what the selector would have
  proposed, then let the human/session continue normally.
- [ ] Store only privacy-safe outcome references using the telemetry schema; do
  not store query text, task body, labels, source bodies, raw logs, payloads or
  secrets.
- [ ] Stop shadow rollout immediately on the first false completion, privacy leak
  or budget breach.

### P2 — Warn mode with confirmation

- [ ] Selector may propose exactly one target with confidence and a short reason.
- [ ] User/session must confirm or correct the proposed target before source
  reading.
- [ ] The strict gate must always validate the confirmed target and fail closed on
  invalid, ambiguous, foreign or over-budget requests.
- [ ] Confidence bands must be calibrated on fresh tasks collected for this phase;
  do not invent thresholds from intuition or old observed sets.
- [ ] Continue writing privacy-safe outcome refs and evidence-journal misses.

### P3 — Enforce local workflow only after thresholds pass

- [ ] Enforce only in the local developer workflow, never in client-facing runtime
  or production VPS paths.
- [ ] Require all acceptance thresholds below on fresh tasks.
- [ ] Keep manual rollback available as one flag/command.
- [ ] Stop enforcement on the first false completion, privacy leak, invalid
  fail-open, or budget breach.

### P4 — Optional adaptive learning after sandbox and H-gates

- [ ] Resume adaptive learning only after a technical sandbox/process with
  verified allowlisted inputs exists.
- [ ] Require H1, H3 and H4 to pass through a separate scorer on fresh locked
  artifacts before any approved pattern can influence selection.
- [ ] Keep failed and invalid outcomes as diagnostics only; never promote them to
  behavior rules.
- [ ] Keep the approved ledger empty until H4 approves a pattern with the required
  evidence.

## Acceptance thresholds

All thresholds apply to fresh tasks, not old observed runs.

- Target recall: at least `95%`.
- Full target-to-gate correctness: at least `90%`.
- False completion: exactly `0`.
- Invalid/fail-close behavior: `100%`.
- Budget/privacy compliance: `100%`.
- Confidence bands: calibrated on fresh tasks with recorded outcomes; never
  invented or copied from non-independent runs.

## Error-learning loop and dual memory

Every miss enters Layer A, the evidence journal, as a privacy-safe diagnostic
reference. Candidate pattern promotion requires all of the following before it can
enter Layer B, the approved ledger:

1. at least three independent supporting cases;
2. at least one relevant anti-example;
3. a fresh holdout separate from the support cases;
4. approval by a scorer separate from the selector/pattern author.

Only the approved ledger may influence selector behavior. Failed, invalid,
candidate and rejected outcomes remain useful diagnostics, but they are forbidden
as behavior rules for selector, gate, prompt, runtime, model or provider policy.

## Artifacts and telemetry schema

Telemetry must use stable IDs and aggregate counts only. It must not include query
text, task body, labels, source bodies, raw logs, payloads, user text or secrets.

Required fields:

```text
privacy_safe_shadow_outcome.v1 top fields: schema, outcome_id,
task_fingerprint, project_id, policy_version, policy_delta, features,
diagnosis_d1_d6, result, gate_status, failure_source, artifact_ref_id, created_at
safe_shadow_features.v1 fields: schema, task_fingerprint, project_id,
policy_version, phase, candidate_ids, selected_target_id,
confirmed_or_corrected_target_id, gate_result, route, evidence_type,
stop_reason, lines_loaded, chars_loaded, latency_ms, verifier_result,
no_raw_query_code_log_secret
```

The current policy allows only `phase=shadow`, max eight unique lowercase SHA-256
candidate IDs, nullable selected/corrected target IDs where semantically needed,
STOP-2 canonical stop reasons, gate/pass consistency, and the 80-line/8000-char
local NMBot budget. `--summary` reports aggregate counts only. This schema is
evidence telemetry only and is not independent benchmark proof, selector input,
gate policy, runtime behavior, prompt behavior, model policy or deploy signal.
Semantic invariants are part of the passive journal contract: selections must be
members of candidates, verifier classes constrain selected/corrected IDs plus
result/gate status, empty candidates force a null selection, and concurrent append
uses an exclusive local lock around existing-store validation, duplicate detection
and one-line fsynced append. The permanent store remains empty until fresh P1 tasks
are explicitly collected.

## Rollback and stop rules

Rollback must be one flag or command path back to the current manual
`navigate -> strict gate` workflow. Rollback must not migrate, delete or rewrite
existing docs, notebooks, artifacts or telemetry.

Shadow, warn and enforce phases stop on the first false completion, privacy leak,
invalid fail-open or budget breach. After a stop, record the failure source and do
not resume until the contract/tool/payload issue is understood and corrected.

## Definition of Done

- [ ] Scope boundary is documented and enforced as local developer workflow only.
- [ ] P0 instrumentation and rollback path are frozen.
- [ ] P1 has at least `30` fresh real developer tasks with privacy-safe outcome
  refs and no automatic gate invocation.
- [ ] P2 warn mode has calibrated confidence bands from fresh tasks.
- [ ] P3 acceptance thresholds pass on fresh tasks.
- [ ] P4 remains disabled unless sandbox plus H1/H3/H4 scorer gates pass.
- [ ] Static tests verify roadmap ownership links, safety boundaries, telemetry
  privacy fields, thresholds and rollback rules.

## Remaining work

- [ ] Decide the exact local rollback flag/command name.
- [ ] Define the privacy-safe `task_fingerprint` function without storing task
  text.
- [ ] Define verifier result values and scorer ownership.
- [ ] Collect fresh P1 tasks after P0 freeze.
- [ ] Calibrate confidence bands from fresh warn-mode outcomes.
- [ ] Keep adaptive approved ledger empty until H4 passes.

## Research references

The official MemoHarness preprint (`https://arxiv.org/html/2607.14159v1`) is
hypothesis inspiration only. It is not project evidence and does not justify any
selector, runtime, prompt, provider, model, gate, VPS, deploy or production change
without fresh local locked experiments.

## Source references

- `docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md` — STOP-2, strict gate, privacy,
  rollback and benchmark boundaries.
- `docs/NMBOT_RETRIEVAL.md` — deterministic navigate, FTS cards and candidate-only
  retrieval behavior.
- `docs/NMBOT_CONTEXT_WORKFLOW_JOURNAL.md` — aggregate baseline metrics and
  current decisions.
- `docs/NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md` — adaptive H1/H2/H3/H4 checklist
  and MemoHarness boundary.
- `docs/NMBOT_ADAPTIVE_SELECTOR_JOURNAL.md` — dual-memory evidence journal and
  approved-pattern ledger rules.
