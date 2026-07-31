# Multi-project memory/context harness integration plan

Status: planning-only implementation roadmap for a local developer workflow.
Phase 0/1 passive local registry mechanics and Phase 2/3/4 passive local outcome
mechanics are implemented. Phase 5 has a safely executed bounded selected-set
trial outcome, but the global phase remains incomplete. Phase 6 repaired and
locally verified MemPalace as optional diary/meta-memory only. Phase 7 has
dry-run-only passive mechanics. Four local adapters now validate for developer
routing: `nmbot`, `qapairs`, `cc2` and `mpn`. Adaptive phases and whole-repo
`cc-daemons` remain not ready. This plan changes no client runtime, prompt,
model, provider, Notebook, MemPalace, network, eval, deploy, VPS or production
behavior by itself. It does not include adaptive behavior. Owner: TBD. Rollback
owner: TBD.

This roadmap implements the contract in
`docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md`. The NMBot roadmap remains the pilot
branch in `docs/NMBOT_CONTEXT_WORKFLOW_PRODUCTION_ROADMAP.md`.

## Goal, Actual / Contract / Desired

Goal: build a portable, correctness-first, multi-project memory/context harness
that helps local developers choose evidence without turning stale memory into
source or production proof.

Actual:

- Project docs, registries, navigate, FTS cards and strict gate already exist for
  NMBot-local context work.
- NMBot NotebookLM sources are currently empty, so NotebookLM cannot be treated as
  populated NMBot project history yet.
- MemPalace was repaired from healthy SQLite after an HNSW index failure. Local
  integrity and semantic-search verification passed; it still must not act as a
  selector or project fact source.
- `/tmp` is volatile; long-term references to `/tmp/opencode` artifacts are not a
  durable outcome store.
- Adaptive selector evidence is not sufficient: H1 failed, H3 is not evaluable,
  and H4 is blocked.
- Four local adapters have static local routes: `nmbot`, `qapairs`, `cc2` and
  `mpn`. Qapairs, CC2 and MPN use owner/rollback owner `ser`/`ser`; MPN's only
  dependency is validator-only `cc-daemons` with no transitive or automatic
  second-root indexing. This is routing/navigation evidence only, not production
  proof.

Contract:

- Project docs and registries are the source of truth for approved patterns,
  owner routes, source IDs and checkable contracts.
- Current code facts require owner source and focused tests. Current production
  facts require fresh explicitly authorized live proof.
- Historical memory may help recall decisions, but it is not proof of current
  source, current config or production state.
- Failed, invalid and stale outcomes are diagnostics only and are forbidden for
  behavior.
- No raw query, raw code, raw log, payload, transcript or secret is stored.

Desired:

- Universal core schemas and gates are reused across projects.
- Per-project adapters provide project-specific owners, route resolvers and local
  checks.
- One-hop shared dependency cards allow bounded interface lookup without broad
  cross-project memory.
- Durable privacy-safe outcomes replace long-term `/tmp` references.
- Shadow and warn modes precede any local enforcement; adaptive hypotheses remain
  off until separately proven.

## Target architecture and tool roles

- Project docs / registries: source of truth and approved-pattern registry.
- Durable typed outcome store: Layer A append-only privacy-safe outcome store;
  local file `data/project_memory_outcomes.jsonl` contains no real sample records.
- NotebookLM: project history and summaries only; not current code or production
  proof. Legacy notebooks are excluded from automatic routing.
- MemPalace: agent diary and meta-memory only after repair; not selector, not
  project fact source, not behavior input.
- Passive local outcome API: `scripts/project_memory_outcomes.py` validates
  `privacy_safe_outcome.v1` and nested `privacy_safe_shadow_outcome.v1`, appends
  canonical JSONL, summarizes aggregates only and returns disabled hints only
  (`hints_disabled_by_policy`).
- `compress`: current conversation context only.
- `memory_search`: recovery hint only, never evidence and never selector truth.
- `navigate`, FTS and strict gate: evidence execution and enforcement, not memory.

## Current execution status and blockers

This section records current local evidence only. It must not be read as phase
exit acceptance, operational readiness or a completed project plan.

- Phase 0/1 mechanics are implemented locally: `config/project_memory_registry.json`
  resolves routable `nmbot` and pilot-ready `qapairs`, `cc2` and `mpn`. NMBot owner and rollback-owner fields remain the literal value `TBD`; Qapairs, CC2 and MPN are assigned to `ser`/`ser`
  for these local developer adapters. Temporary user delegation for local technical
  execution does not assign a permanent operational owner for the whole harness.
- Phase 2/3/4 mechanics are implemented locally: policy bundle, D1-D6 taxonomy,
  append-only v1/v2 outcome validation and bounded list/summary/hints API exist.
  The outcome store `data/project_memory_outcomes.jsonl` is empty, `--hints`
  returns `hints_disabled_by_policy`, and no adaptive behavior is enabled.
- Phase 5 canonical NotebookLM route had a bounded user-authorized selected-set
  trial run on 2026-07-26 against the historical pre-fix inspected 38-record
  candidate set: those 38 records were dispositioned; 4 unique safe historical
  summaries were added canonical-only to NMBot with one-by-one metadata SHA
  verification; Qapairs writes were 0; 23 content-policy exclusions were not
  written; 11 records were retained without write as duplicates,
  dedupe-uncertain or already canonical. This is not a full corpus migration.
  After Qapairs pilot routing, the safe v3 manifest selects 38 records, holds 323
  and excludes 3 sensitive, but execution remains blocked without a new write
  authorization. Global Phase 5 exit is still blocked by unresolved corpus-
  wide authorization/evidence. Phase 6 MemPalace recovery and
  local health verification passed after rebuilding the HNSW index from SQLite;
  its allowed scope remains agent diary/meta-memory only, with selector/project-
  fact/behavior use disabled. Phase 7 NMBot adapter is dry-run only; there are no
  actual fresh shadow tasks or real shadow outcomes.
- Local registry/evidence scan now has three eligible heterogeneous local projects:
  `qapairs`, `cc2` and `mpn` are pilot-ready for local developer navigation/context only. The n8n
  audit, opencode and novostroy candidates are ownership-map proposals in
  `docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md`, without a confirmed local owner
  source, docs scope and check chain in this repository.
- Therefore Phase 0/1 have mechanical validation only; operational phase exits are
  not claimed. Phase 5-7 exits are not claimed. Phase 8 has a local adapter but
  no phase-exit claim. Phase 9-13 remain blocked until there are fresh real
  developer shadow tasks, multi-project isolation results, and eventually a human
  governance owner. Do not claim this project plan complete.

## Universal core, adapters and shared dependencies

- Universal core: schema validation, privacy rules, append-only writes, fail-close
  status, D1-D6 diagnosis, bounded query API, phase gates, rollback markers.
- Per-project adapters: project registry row, canonical notebook route, owner docs,
  route resolver, local checks, telemetry field mapping and allowed evidence types.
- One-hop shared dependencies: explicit dependency card from consumer to owner;
  depth one, exact metadata filters, interface-only records and no transitive walk.

## Versioned schemas and checklists

All schemas are versioned. Owner fields remain `TBD` until assigned. They store
stable IDs and aggregate values only.

```text
project_registry.v1: project_id, canonical_notebook, owner_tbd, docs_refs,
  registry_refs, route_resolvers, local_checks, legacy_notebooks_excluded,
  write_policy, rollback_owner_tbd
policy_bundle.v1: policy_version, policy_delta, project_id, allowed_routes,
  deny_rules, threshold_profile, effective_from, owner_tbd
privacy_budget: hard budget for both legacy v1 and shadow v2 records;
  max_candidate_ids=8, max_selected_source_count=2, max_lines_loaded=80,
  max_chars_loaded=8000
safe_case_features.v1: case_fingerprint, project_id, route, evidence_type,
  target_kind, candidate_count, selected_source_count, lines_loaded, chars_loaded,
  verifier_result, no_raw_query_code_log_secret=true
safe_shadow_features.v1: task_fingerprint, project_id, policy_version, phase,
  candidate_ids, selected_target_id, confirmed_or_corrected_target_id,
  gate_result, route, evidence_type, stop_reason, lines_loaded, chars_loaded,
  latency_ms, verifier_result, no_raw_query_code_log_secret=true
d1_d6_taxonomy.v1: D1 project identity, D2 route selection, D3 evidence target,
  D4 budget/privacy, D5 dependency/isolation, D6 verifier/rollback
privacy_safe_outcome.v1: outcome_id, case_fingerprint, project_id,
  policy_version, policy_delta, diagnosis_d1_d6, result, gate_status,
  failure_source, artifact_ref_id, created_at
privacy_safe_shadow_outcome.v1: outcome_id, task_fingerprint, project_id,
  policy_version, policy_delta, features, diagnosis_d1_d6, result, gate_status,
  failure_source, artifact_ref_id, created_at
bank_snapshot.v1: bank_snapshot_id, policy_version, included_outcome_ids,
  excluded_failed_invalid_ids, frozen_at, scorer_owner_tbd
dependency_card.v1: scope_id, owner_project, consumer_project, contract_ref,
  allowed_query_types, max_depth=1, max_records=2, rollback_owner_tbd
approved_pattern_record.v1: pattern_id, source_outcomes, anti_examples,
  fresh_holdout_ref, scorer_approval_ref, policy_version, owner_tbd
```

Forbidden storage fields: raw query, raw source code, source body, raw log,
payload, transcript, label text, secret, `.env` value, provider response body.

## Phase sequence

Do not start a phase until the previous phase exit gate is satisfied and recorded.

### Phase 0 — Freeze scope, owners and rollback

Status: partial. The local non-goal boundary is frozen in docs and registry
fields preserve literal `TBD`, but owner and rollback-owner confirmation are not
assigned, so Phase 0 is not exited.

Purpose: make the work planning-only and assign decision points.
Dependencies: protocol contract reviewed.
Todos:
- [ ] Confirm owner: TBD.
- [ ] Confirm rollback owner: TBD.
- [x] Freeze non-goals: no runtime/Notebook mutation/network/model/eval/deploy.
- [ ] Name the manual rollback path back to project docs -> navigate -> confirmed target -> strict gate.
Deliverables: frozen scope note, owner table, rollback note.
Expected result: every later phase has a named owner or explicit `TBD`.
Verification: static doc check finds planning-only boundaries and rollback path.
Stop conditions: owner ambiguity, runtime/config request, or deletion request.
Exit gate: Phase 0 checklist complete and reviewed.

### Phase 1 — Project registry and validators

Status: partial. `config/project_memory_registry.json`,
`scripts/project_memory_registry.py` and wrapper command `memory-registry` provide
local fail-closed validation/resolution for NMBot and a non-routable pending
Qapairs row. Owner confirmation and Phase 1 acceptance are still pending; do not
claim Phase 1 exit or start Phase 2 from this alone.

Purpose: make project identity explicit and fail closed.
Dependencies: Phase 0 exit gate.
Todos:
- [x] Create `project_registry.v1` draft.
- [x] Mark canonical, shared and legacy notebooks for NMBot and pending Qapairs.
- [x] Add validators for required project_id and legacy exclusion.
- [ ] Record NMBot NotebookLM sources as currently empty.
Deliverables: registry schema, sample rows for NMBot and adapter candidates.
Expected result: unknown or ambiguous project stops before search.
Verification: unit/static tests for required fields and legacy exclusion.
Stop conditions: implicit broad notebook default or missing owner docs.
Exit gate: registry validates at least two project rows without broad fallback.

### Phase 2 — Durable append-only outcome store

Status: partial mechanically. `config/project_memory_policy_bundles.json`,
`data/project_memory_outcomes.jsonl` and `scripts/project_memory_outcomes.py`
implement local append-only v1/v2 validation for NMBot only. The store has no
real outcome sample, owner remains `TBD`, and Phase 2 is not exited.

Purpose: replace long-term `/tmp` references with durable privacy-safe Layer A.
Dependencies: Phase 1 exit gate.
Todos:
- [x] Define append-only `privacy_safe_outcome.v1`.
- [x] Define stable artifact refs without raw body storage.
- [x] Add migration note for volatile `/tmp` experiment roots.
- [x] Add recovery read path and rollback marker.
Deliverables: durable outcome store design and validator checklist.
Expected result: outcomes survive session cleanup without storing raw data.
Verification: durability/recovery test with no raw query/code/log/secret fields.
Stop conditions: raw data field, mutable overwrite, or `/tmp` as sole durable ref.
Exit gate: append-only dry-run passes and rollback leaves data intact.

### Phase 3 — Privacy-safe telemetry and D1-D6 diagnosis

Status: partial mechanically. `config/project_memory_diagnosis_taxonomy.json`
defines D1-D6 and allowed failure sources; outcome validation stores only IDs,
counts and safe enums, including shadow phase/verifier/stop/gate enums.
Governance owner and Phase 3 acceptance remain pending.

Purpose: record why a case passed, failed or stopped without leaking content.
Dependencies: Phase 2 exit gate.
Todos:
- [x] Freeze D1-D6 taxonomy.
- [x] Map each verifier failure to one D-dimension.
- [x] Add failure source values: agent_tool, external, contract, unknown.
- [x] Confirm telemetry stores IDs/counts only.
Deliverables: taxonomy, telemetry checklist, failure-source table.
Expected result: every miss is diagnosable without raw task text.
Verification: privacy/security tests and taxonomy coverage tests.
Stop conditions: ambiguous diagnosis, privacy leak, or unclassified failure.
Exit gate: all sample outcomes classify into D1-D6 and pass privacy checks.

### Phase 4 — Bounded memory query API

Status: partial mechanically. `--list` returns at most five safe aggregate rows;
`--summary` returns aggregate counts and budgets only; `--hints` always abstains
with `hints_disabled_by_policy`. No behavior hints, adaptive behavior or Phase 5
work is enabled.

Purpose: make memory retrieval a bounded optional hint, not behavior.
Dependencies: Phase 3 exit gate.
Todos:
- [x] Default to no hints.
- [x] Limit hints/list output to max 5.
- [x] Require exact metadata filters: project_id, policy_version, route/evidence type.
- [x] Forbid failed, invalid and stale outcomes from behavior.
Deliverables: query API contract and denial reasons.
Expected result: retrieval can abstain and cannot broaden across projects.
Verification: isolation, max5, filter and fail-close tests.
Stop conditions: semantic broadening, invalid/fail-open hint, or missing filter.
Exit gate: bounded API returns only eligible hints or explicit abstention.

### Phase 5 — NotebookLM canonical routing and summary writer dry-run

Status: partial. The canonical NotebookLM route and summary writer safeguards
were exercised through a bounded user-authorized selected-set trial on
2026-07-26, but Phase 5 exit is not claimed. The run completed only the
historical pre-fix inspected 38-record set: 4 unique safe historical summaries
were added to the NMBot canonical notebook, Qapairs writes were 0, 23 records
were blocked by content policy and not written, and 11 were retained without
write as duplicate, dedupe-uncertain or already canonical. The corrected current
v2 manifest excludes 12 pending/non-routable Qapairs records from selection and
has 26 selected, 335 held and 3 sensitive records. The whole historical corpus is
still incomplete, and no fresh policy-blocked-vs-retained breakdown is claimed
for the corrected 26-record set. Global ownership and rollback ownership remain
`TBD`, so broader migration remains blocked.

Purpose: keep NotebookLM as project history/summaries, not current proof.
Dependencies: Phase 4 exit gate.
Todos:
- [ ] Route only to canonical project notebook by project_id.
- [ ] Exclude legacy notebooks from automatic routing.
- [x] Execute bounded selected-set trial with canonical-only writes under explicit
  user authorization.
- [ ] Generalize beyond the bounded selected set only after owner/rollback owner
  and per-record authorization are confirmed.
- [ ] Record boundary: sources/history only, not current source/prod proof.
Deliverables: canonical routing spec and dry-run output examples.
Expected result: NotebookLM use is explicit, project-scoped and non-mutating.
Verification: static tests for legacy exclusion and no write behavior.
Stop conditions: Notebook mutation, legacy automatic search, or production claim.
Exit gate: dry-run shows intended writes without performing them.

### Phase 6 — MemPalace repair, health and isolation

Status: local repair and health verification passed on 2026-07-26. SQLite was
healthy; the failed component was the HNSW vector index. The index was rebuilt
from SQLite with the previous active path preserved as rollback, and local
SQLite/status/repair-status/semantic-search/wake-up checks passed. MemPalace
remains disabled for selector, project facts and behavior; only agent diary and
meta-memory are allowed. This is not an operational Phase 6 exit for adaptive or
project-memory behavior.

Purpose: make MemPalace optional and safe after repair.
Dependencies: Phase 5 exit gate.
Todos:
- [ ] Keep MemPalace disabled for selector/project facts.
- [x] Define and run local health checks for integrity and vector availability.
- [ ] Define project isolation checks before any future use.
- [x] Limit allowed use to agent diary/meta memory after pass.
Deliverables: repair checklist, health gate, isolation gate.
Expected result: an unhealthy path fails closed; after verified repair, only
agent diary/meta-memory remain available.
Verification: `scripts/project_memory_mempalace_verify.py` passed local SQLite
integrity, status, repair-status, semantic search and wake-up checks on the
active path.
Stop conditions: selector use, fact-source use, or failed health gate.
Exit gate: optional MemPalace path remains non-blocking and disabled until pass.

### Phase 7 — NMBot adapter and passive shadow

Status: partial dry-run mechanics only. The NMBot adapter is dry-run only and has
no actual fresh developer shadow tasks or real privacy-safe shadow outcomes.
Phase 7 exit is not claimed.

Purpose: test the core on NMBot without changing behavior.
Dependencies: Phase 6 exit gate.
Todos:
- [ ] Create NMBot adapter from existing docs/registries.
- [ ] Freeze policy bundle and `bank_snapshot_id` before shadow.
- [ ] Run passive shadow only; no automatic gate invocation.
- [ ] Record outcomes in Layer A only.
Deliverables: NMBot adapter, frozen policy bundle, passive shadow report.
Expected result: NMBot produces comparable privacy-safe outcomes.
Verification: frozen-bank experiment and no behavior-change checks.
Stop conditions: policy drift, automatic enforcement, or privacy leak.
Exit gate: shadow report passes thresholds for data quality, not enforcement.

### Phase 8 — Heterogeneous local project adapters

Status: partial local pilot. Qapairs, CC2 and MPN are heterogeneous local adapters
and are route-eligible for local developer navigation/context only. CC2 acceptance
metrics are 15/15 positive, 17/17 negative, false selections 0, max 1/80/3515, 4
honest clipped stops and unsafe claims 0. MPN acceptance metrics are 8/8 positive,
20/20 negative, false selections 0, max 1/80/3540, 8 honest clipped stops,
cross-project leakage 0 and unsafe claims 0. No runtime, production, NotebookLM
write, CRM, Sheets, VPS, network or adaptive behavior is enabled. Whole-repo
`cc-daemons` indexing is not ready; MPN may only use its explicit validator-only
dependency card. n8n audit/opencode/novostroy candidates remain ownership
proposals only.

Purpose: test portability outside NMBot through local adapters, not production behavior.
Dependencies: Phase 7 exit gate.
Todos:
- [x] Choose Qapairs, CC2 and MPN local adapter owners: ser; rollback owner: ser.
- [x] Add adapter rows and local evidence routes.
- [x] Add project-specific checks without changing runtime.
- [ ] Compare outcome schema compatibility with NMBot.
Deliverables: second adapter and portability notes.
Expected result: universal core works with a different project shape.
Verification: multi-project portability tests and adapter static checks.
Stop conditions: NMBot-only assumptions or missing owner contract.
Exit gate: second adapter passes core validators without special-case hacks.

### Phase 9 — Multi-project isolation, dependency and portability benchmark

Status: blocked, not implemented. Requires Phase 8 plus fresh real developer
shadow tasks.

Purpose: prove isolation and one-hop behavior before warn mode.
Dependencies: Phase 8 exit gate.
Todos:
- [ ] Build fresh benchmark cases for at least two projects.
- [ ] Include positive, negative, stale, dependency and rollback cases.
- [ ] Lock labels and outputs before scoring.
- [ ] Stop at first failed preflight or first-case failure.
Deliverables: benchmark plan, locked artifacts, score report.
Expected result: isolation and dependency limits are measurable.
Verification: isolation, one-hop, stale-vs-current and rollback matrix.
Stop conditions: label leak, broad search, first failure, or score mutation.
Exit gate: benchmark meets correctness and fail-close thresholds.

### Phase 10 — Warn mode

Status: blocked, not implemented. Requires completed shadow/benchmark gates and
explicit human confirmation.

Purpose: offer local hints only after benchmark gates.
Dependencies: Phase 9 exit gate.
Todos:
- [ ] Present at most one proposed target plus uncertainty.
- [ ] Require human/session confirmation before source reading.
- [ ] Keep strict gate as the enforcement point.
- [ ] Stop on false completion, privacy leak, invalid fail-open or budget breach.
Deliverables: warn-mode contract and stop-rule log.
Expected result: hints improve workflow without autonomy.
Verification: warn-mode acceptance tests and first-failure logs.
Stop conditions: unconfirmed read, false confidence, or gate bypass.
Exit gate: warn mode passes fresh cases with zero critical failures.

### Phase 11 — Adaptive hypotheses H1/H3/H4 separately

Status: blocked, not implemented. H1 is failed, H3 is not evaluable and H4 is
blocked; adaptive behavior remains off.

Purpose: keep adaptive learning outside the implementation path until proven.
Dependencies: Phase 10 exit gate.
Todos:
- [ ] Treat current H1 as failed.
- [ ] Treat current H3 as not evaluable.
- [ ] Treat current H4 as blocked.
- [ ] Design separate fresh locked tests before any adaptive behavior.
Deliverables: separate hypothesis test plans only.
Expected result: adaptive remains off and cannot influence selection.
Verification: tests show failed/invalid/candidate outcomes forbidden for behavior.
Stop conditions: automatic D1-D6 edits, semantic similarity selector, or pattern promotion without H4.
Exit gate: adaptive stays disabled unless new independent gates pass later.

### Phase 12 — Enforce local developer workflow only after thresholds

Status: blocked, not implemented. No enforcement threshold has been proven on
fresh real tasks.

Purpose: make strict local workflow mandatory only if correctness gates pass.
Dependencies: Phase 11 exit gate and separate approval.
Todos:
- [ ] Confirm thresholds on fresh tasks.
- [ ] Keep scope local developer workflow only.
- [ ] Keep rollback command available.
- [ ] Record uncertainty per project.
Deliverables: enforcement decision record.
Expected result: local enforcement is correctness-first and reversible.
Verification: acceptance suite plus rollback drill.
Stop conditions: threshold miss, production/runtime coupling, or rollback failure.
Exit gate: owner approves local-only enforcement with passing fresh evidence.

### Phase 13 — Operations, governance, retention and rollback

Status: blocked, not implemented. Human governance owner is still `TBD`.

Purpose: keep the harness safe over time.
Dependencies: Phase 12 exit gate.
Todos:
- [ ] Define retention policy for outcomes and summaries.
- [ ] Define periodic registry/policy drift review.
- [ ] Define no-deletion rollback and stale-memory invalidation notes.
- [ ] Define owner handoff for each project adapter.
Deliverables: operations guide, retention schedule, rollback drill record.
Expected result: the system remains auditable and reversible.
Verification: governance checklist and rollback test.
Stop conditions: ownerless adapter, deletion-based rollback, or stale proof claim.
Exit gate: operations checklist signed off by owner TBD.

## Testing matrix

| Area | Required checks |
|---|---|
| Contract/unit | schema validators, required owner fields, phase gates |
| Security/privacy | no raw query/code/log/secret, IDs/counts only |
| Isolation | project_id required, legacy excluded, foreign default denied |
| Durability/recovery | append-only store, `/tmp` not durable, rollback no deletion |
| Stale memory vs current source | memory handoff, source/test required for current code |
| One-hop dependency | max depth one, max two records, interface-only |
| Context budget | max five hints, strict evidence budgets, budget stop reasons |
| Multi-project portability | NMBot plus Qapairs, CC2 and MPN adapters |
| Frozen-bank experiment | `bank_snapshot_id`, policy freeze, no tuning on observed set |
| Rollback | project docs -> navigate -> confirmed target -> strict gate |

## Metrics and correctness-first policy

Correctness and fail-close beat token/context cost. Metrics are per-project and
must include uncertainty. Existing local thresholds are pilot thresholds, not
universal truths: target recall >=95%, full target-to-gate >=90%, false
completion 0, invalid/fail-close 100%, budget/privacy 100%.

## Rollout and rollback

Rollout order: passive foundation -> shadow -> warn -> enforce. Rollback returns
to project docs, navigate, confirmed target and strict gate. Rollback does not
delete notebooks, outcomes, docs, telemetry or artifacts.

## Definition of Done

- [ ] Owner fields are present as assigned or `TBD`.
- [ ] Phases 0-13 have purpose, dependencies, todos, deliverables, expected result,
  verification, stop conditions and exit gate.
- [ ] Universal schemas forbid raw query/code/log/secret storage.
- [ ] At least two project adapters validate.
- [ ] NotebookLM and MemPalace boundaries are enforced.
- [ ] Correctness-first thresholds are recorded as pilot, not universal.
- [ ] Rollback drill succeeds without deletion.
- [ ] Adaptive behavior remains off unless separate H1/H3/H4 gates pass.

## Master checklist by phase

- [ ] Phase 0 owner/scope/rollback freeze — owner: TBD.
- [ ] Phase 1 registry validators — owner: TBD.
- [ ] Phase 2 durable Layer A store — owner: TBD.
- [ ] Phase 3 telemetry and D1-D6 diagnosis — owner: TBD.
- [ ] Phase 4 bounded memory query API — owner: TBD.
- [ ] Phase 5 NotebookLM canonical dry-run — owner: TBD.
- [ ] Phase 6 MemPalace repair gate — owner: TBD.
- [ ] Phase 7 NMBot passive shadow — owner: TBD.
- [ ] Phase 8 Qapairs, CC2 and MPN adapters — owner: TBD.
- [ ] Phase 9 isolation/dependency benchmark — owner: TBD.
- [ ] Phase 10 warn mode — owner: TBD.
- [ ] Phase 11 adaptive hypotheses — owner: TBD.
- [ ] Phase 12 local-only enforcement decision — owner: TBD.
- [ ] Phase 13 operations/governance/retention — owner: TBD.

## Risks and non-goals

Risks: stale memory treated as evidence, accidental cross-project leakage,
privacy leak, `/tmp` data loss, NMBot-only overfitting, owner ambiguity,
thresholds copied as universal truth, and adaptive behavior enabled too early.

Non-goals: client runtime, production VPS behavior, model/provider changes,
prompt changes, Notebook mutation, MemPalace selector use, network calls, eval,
deploy, automatic code edits, full trajectory storage, raw trace storage, and
automatic D1-D6 policy edits.

## MemoHarness mapping

Adapted: typed experience, dual layer separation, bounded structured retrieval,
frozen bank snapshots and correctness-first evaluation.

Not transferred without local evidence: automatic D1-D6 edits, semantic
similarity retrieval as selector, paper constants, cache cost assumptions, full
trajectories and raw traces.

## Exact sources

- MemoHarness preprint: `https://arxiv.org/html/2607.14159v1`, sections 2.2-2.6
  and Limitations, used as hypothesis inspiration only.
- `docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md` — authoritative retrieval and
  isolation contract.
- `docs/NMBOT_CONTEXT_WORKFLOW_PRODUCTION_ROADMAP.md` — NMBot pilot branch.
- `docs/NMBOT_CONTEXT_WORKFLOW_JOURNAL.md` — local aggregate facts.
- `docs/NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md` and
  `docs/NMBOT_ADAPTIVE_SELECTOR_JOURNAL.md` — H1/H2/H3/H4 and approved-pattern
  status.
- Tool/output facts supplied in the implementation request: NMBot NotebookLM
  sources empty, MemPalace integrity-failed/vector-disabled, `/tmp` volatile.
