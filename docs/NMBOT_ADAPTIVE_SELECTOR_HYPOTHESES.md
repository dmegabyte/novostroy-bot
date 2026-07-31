# NMBot adaptive selector / experience bank hypotheses

Status: authoritative documentation-only checklist for future local experiments.
This file enables no behavior and changes no runtime, gate, selector, model,
provider, prompt, registry, VPS, network, deploy, production state or eval.

Source-of-truth dependency: `docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md` owns
STOP-2 and fresh-holdout rules; `docs/NMBOT_RETRIEVAL.md` owns target routing;
`docs/NMBOT_ADAPTIVE_SELECTOR_JOURNAL.md` owns append-only evidence summaries and
the separately approved general pattern ledger.

External inspiration only: MemoHarness §2 and Appendix A
(`https://arxiv.org/html/2607.14159v1`) suggest per-case outcome records,
distilled patterns, bounded retrieval and correctness-first selection. Their
results do not transfer to this corpus without local, fresh, locked experiments.

## Purpose and boundary
Purpose: keep a sequential checklist for testing whether a bounded experience
bank can help the human/session pick the correct strict target before calling the
existing STOP-2 gate.

Boundary: this is an experiment plan and checklist only. The strict explicit-
target gate remains immutable unless a separate future task changes it. Free-
Russian target selection is currently weak: 19/22 target recall and 16/22 full
gate paths, so it must not be treated as a solved routing layer.

Immutable during these experiments: Strict gate route and stop enums; STOP-2
budgets: max two selected sources, 80 lines and 8000 characters; fail-close
behavior; and zero model/provider/VPS/network/deploy/runtime changes.

## Safe local schemas and hygiene
Outcome/pattern records may store only refs, compact route/target hints,
supporting IDs, anti-examples, holdout refs and aggregate booleans. Cards,
outcomes and patterns are navigation hints, never evidence by themselves. Use
fresh cases; keep queries, labels, outputs and scores separate; lock candidates
before scoring; smoke one first case; stop on first tool/contract/parsing failure;
record target/full-path/abstention/false-positive/budget tags. A scorer must be
separate from the selector/pattern author.

## Checklist
### H1 — bounded experience helps target selection
- Status: [x] failed on clean H1-v3; do not enable adaptive selection.
- Baseline: current session/navigate selection before strict gate.
- Variant: add bounded per-case outcome/pattern hints before selecting target.
- Metrics: target/full-path recall, false target rate, abstention correctness.
- Hard pass: target and full-path recall improve with no false-target/budget/
  fail-close regression.
- Hard fail: false target increase, full-path regression, unlabeled tuning, or
  bypass of strict target validation.

H1-v3 scorer decision: VALID FAIL. The clean lock verified hashes for all four
input files and both candidate locks. Adaptive selection improved target recall
18/22->19/22 and reduced false-target rate, but full strict hit stayed 6/22; the
required dual improvement did not occur. Artifacts:
`/tmp/opencode/nmbot_adaptive_selector_h1_v3_20260725/h1_score.json` and
`h1_report.md`. H1-v2 is invalid because label digests were missing and search
isolation was breached.

### H2 — context remains bounded
- Status: [x] passed on H1-v3 structural evidence only; no behavior enabled.
- Baseline: current STOP-2 strict gate and current selection context.
- Variant: at most five retrieved outcomes/patterns and no raw logs.
- Metrics: pattern/source/line/char counts, selected evidence, stop reasons.
- Hard pass: all cases stay within STOP-2 budgets and hints stay compact refs.
- Hard fail: budget exceedance, full-file load, raw transcript/log inclusion, or
  repeated search without new evidence.

H2 scorer decision: VALID STRUCTURAL PASS only. The H1-v3 artifact lock remained
valid; one compact pattern was within the <=5 outcome/pattern limit; hints were
reference-only IDs/hints with no raw bodies, transcripts, labels or logs; no
STOP-2 budget or fail-close regression was found. This is not a quality score
and does not override H1.

### H3 — failure examples add value beyond successes
- Status: [x] NOT_EVALUABLE; no score/quality claim and no selector behavior.
- Baseline: success-only outcome/pattern hints.
- Variant: success plus failure/anti-example hints with bounded retrieval.
- Metrics: false targets, negative abstention, near-miss corrections, recall.
- Hard pass: fewer false targets or better negative abstention without recall loss.
- Hard fail: suppressed valid targets, positive abstention increase, or unbounded
  history reading.

H3 evidence summary: H3-v1 lacked multiple candidates; H3-v2 failed preflight
partition/cardinality; H3-v3 harness validator exposed unbound locks; H3-v4
selector view leaked labels (`labels_not_read=false`). Current shared filesystem
and agent setup cannot prove blind model selection merely by instruction.

### H4 — patterns generalize only with repeated independent support
- Status: [x] blocked; no general patterns approved.
- Baseline: no distilled patterns, only current selection.
- Variant: admit candidate patterns only after at least three independent
  occurrences and a separate fresh holdout.
- Metrics: pattern precision, holdout recall, anti-example hit, stale rejection.
- Hard pass: each accepted pattern has >=3 independent supports, relevant anti-
  example checks and a separate fresh holdout pass.
- Hard fail: promotion from too few cases, training-set scoring, or missing
  holdout artifacts.

H4 is blocked because H1 failed and H3 produced no approved patterns. The general
pattern ledger still says `Approved patterns: none`; candidate/rejected patterns
are forbidden to selector, gate, prompt, runtime, provider or docs consumers.

## Resume prerequisite and closing rule
Only resume H3/H4 with a technical isolation boundary: a separate sandbox/process
with verified allowlisted inputs; or explicitly re-scope as a non-blind
deterministic rules test. Neither path is enabled now.

Only a separate scorer may change any hypothesis status from `[ ]` to `[x]` and
must cite artifact root, locks, labels, scorer/report and exact decision.

## Rollout decision matrix
- No fresh holdout or scorer artifact: documentation-only; no behavior change.
- H1/H2 pass but H3/H4 pending: shadow only, not enforced.
- Safe misses or stale patterns: warn only; human confirms target.
- Separate scorer closes all required gates: future enforce task may be considered.
- Any hard fail, budget breach or false target regression: keep strict target selection.
