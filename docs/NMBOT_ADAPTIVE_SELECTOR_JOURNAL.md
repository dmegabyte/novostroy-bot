# NMBot adaptive selector dual-memory journal

Status: authoritative persistent documentation-only journal for adaptive selector
experiments. This file enables no behavior and changes no runtime, gate,
selector, model, provider, prompt, network, VPS, deploy, production state or eval.

Source links: `docs/NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md` owns the hypothesis
checklist; `docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md` owns STOP-2, strict
target validation, bounded retrieval, privacy and fresh-holdout rules;
`docs/NMBOT_CONTEXT_WORKFLOW_JOURNAL.md` owns the cumulative aggregate
context/navigation results journal across adaptive and non-adaptive work.

Raw case records live only in `/tmp/opencode` experiment roots. They are not
copied into project docs. This journal stores artifact references and aggregate
metrics only; it never stores queries, labels, bodies, secrets, raw logs or user
payloads.

## Layer A — Evidence journal

Append-only immutable experiment summaries. Add a dated correction rather than
rewriting old verdicts.

### 2026-07-25 — H1-v2 — INVALID

- Hypothesis: H1 bounded experience helps target selection.
- Artifact root: `/tmp/opencode/nmbot_adaptive_selector_h1_v2_20260725/`.
- Lock/integrity status: invalid; label digests were absent from the lock
  manifest, and the search actor saw holdout cards before scoring isolation was
  proven.
- Verdict: INVALID; not evidence for target selection or pattern quality.
- Exclusions: never reuse this run's candidate, rejected or implied patterns.

### 2026-07-25 — H1-v3 — VALID FAIL

- Hypothesis: H1 bounded experience helps target selection.
- Artifact root: `/tmp/opencode/nmbot_adaptive_selector_h1_v3_20260725/`.
- Lock/integrity status: valid; scorer reported verified hashes for all four
  input files and both candidate locks.
- Baseline metrics: adaptive target recall baseline 18/22; full strict hits 6/22.
- Variant metrics: adaptive target recall 19/22; full strict hits 6/22; false
  target rate lower than baseline.
- Verdict: VALID FAIL. The hard pass required both target recall and full strict
  path recall to improve without false-target regression; full strict hits stayed
  unchanged, so no adaptive behavior is enabled.
- Exclusions: do not enable selector behavior from H1 and do not promote H1-v3
  candidate/rejected patterns.

### 2026-07-25 — H2 on H1-v3 — VALID STRUCTURAL PASS

- Hypothesis: H2 context remains bounded.
- Artifact root: `/tmp/opencode/nmbot_adaptive_selector_h1_v3_20260725/`.
- Lock/integrity status: valid for structural evidence; `h2_report.md` reports
  matching SHA-256 values for the six locked H1-v3 files and complete candidate
  locks.
- Variant metrics: exactly one compact pattern was used, within the <=5 limit;
  hints are reference-only IDs/hints with no raw bodies, transcripts, labels,
  logs, secrets or user payloads; negative rows still selected `null`.
- Verdict: VALID STRUCTURAL PASS for H2 only. No STOP-2 budget regression and no
  fail-close regression were found. This does not assess quality, does not
  override H1, and enables no adaptive selector behavior.
- Exclusions: the compact pattern is not approved or reusable.

### 2026-07-25 — H3-v1..v4 — NOT_EVALUABLE

- Hypothesis: H3 failure examples add value beyond successes.
- Artifact roots: `/tmp/opencode/nmbot_adaptive_selector_h3_20260725/`,
  `/tmp/opencode/nmbot_adaptive_selector_h3_v2_20260725/`,
  `/tmp/opencode/nmbot_adaptive_selector_h3_v3_20260725/`, and
  `/tmp/opencode/nmbot_adaptive_selector_h3_v4_20260725/`.
- Lock/integrity status: not evaluable. H3-v1 lacked multiple candidates; H3-v2
  failed preflight partition/cardinality; H3-v3 validator exposed unbound locks;
  H3-v4 selector view leaked labels (`labels_not_read=false`).
- Verdict: NOT_EVALUABLE. No score/quality claim may be made.
- Exclusions: no H3 pattern, candidate, rejected pattern or selector output is
  approved for selector, gate, prompt, runtime or provider use.

### 2026-07-25 — H4 — BLOCKED

- Hypothesis: H4 patterns generalize only with repeated independent support.
- Artifact root: none; H4 was not run.
- Reason: no H3-approved patterns exist, and H1 failed.
- Verdict: BLOCKED. No general patterns are approved.
- Exclusions: candidate/rejected patterns remain forbidden to selector consumers.

### 2026-07-26 — Passive shadow telemetry v2 — EVIDENCE RECORDS ONLY

- Hypothesis: future P0/P1 shadow tasks can be journaled as privacy-safe evidence
  records without behavior use.
- Artifact root: local repository only; no permanent sample records were appended.
- Lock/integrity status: mechanics only. `privacy_safe_shadow_outcome.v1` and
  `safe_shadow_features.v1` validate IDs, enums, budgets, duplicate outcome IDs
  and aggregate summaries.
- Verdict: evidence-record mechanics only. This is not an H4 pattern approval and
  not independent rollout evidence.
- Exclusions: no selector, gate, prompt, runtime component, model policy or
  provider policy may consume these records as behavior rules.

## Layer B — General pattern ledger

Only patterns that meet H4 requirements may appear here: at least three
independent supporting cases, a separate fresh holdout, and a separate scorer
approval specifically for H4. Candidate and rejected patterns are not approved
patterns.

Approved patterns: none.

No selector, gate, prompt, runtime component, model policy or provider policy may
consume candidate or rejected patterns from any experiment root. Until a separate
scorer records an H4 pass, this ledger intentionally exposes no reusable pattern
content.

## Resume prerequisite

Only resume H3/H4 with a technical isolation boundary: a separate sandbox/process
with verified allowlisted inputs. Alternative: re-scope explicitly as a non-blind
deterministic rules test. Neither path is enabled now.

## Write protocol

1. Keep raw records in `/tmp/opencode` artifact roots only. Do not copy queries,
   labels, bodies, secrets, raw logs or user payloads into project docs.
2. Update Layer B only when a separate scorer records an H4 pass with at least
   three independent supports and a separate fresh holdout.
