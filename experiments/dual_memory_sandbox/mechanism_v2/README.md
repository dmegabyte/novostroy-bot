# Dual memory mechanism-v2 — PREPARED_NOT_RUN

Status: **PREPARED_NOT_RUN**.

This is a static, isolated design. It does not run agents, models, providers,
fixtures, hidden verifiers, scorers, evals, network calls, VPS commands or
production code. It does not modify the historical v1 execution logic or v1
fixtures.

## Purpose

The design tests the observational hypothesis: relevant sealed advisory memory
may guide route/check selection, not merely add extra context. It cannot prove
causality under the current task API because there is no seed/model lock and no
hard guarantee that the model used the supplied memory. Receipt data is declared
route selection only, not hidden reasoning or proof of use.

## Arms

Each of six fresh holdouts is pre-registered for the same three arms:

- **B0** — empty memory.
- **M1** — relevant sealed advisory memory for the holdout family.
- **S1** — sham sealed advisory memory with the same entry count/shape and only
  approximate bounded-code-length matching, but advice from unrelated families.

Arm order is randomized/counterbalanced in `experiment.json` before any result.
No task sees its own outcome in memory. Prompt/token lengths are not claimed to
be exactly equal.

Receipt advice is arm-specific and schedule-derived: B0 permits no advice, M1
permits exactly the scheduled relevant-family advice codes, and S1 permits
exactly the scheduled unrelated sham-family advice codes. Holdout public cards
therefore expose an arm map, not a task-family-only promise.

## Data

- Six fresh holdouts across three safe synthetic repair families:
  `mech-normalize-holdout-01/02`, `mech-cache-holdout-01/02`, and
  `mech-boundary-holdout-01/02`.
- Nine learning supports:
  `mech-<family>-learn-01/02/03`.
- Public task cards are data-only and contain no hidden expected answers.
- Each holdout public card includes `route_scope`: short natural-language
  in-scope and out-of-scope symptom/check dimensions so route selection is
  publicly decidable without exposing private labels or exact hidden check pairs.
- Private labels are safe synthetic placeholders for future blind scoring.
- Static advisory payloads are sealed in
  `private/advisory_payloads.jsonl` and hash-locked from `experiment.json`.
  They contain only safe controlled advice entries, family and arm/task mapping;
  they contain no raw prompt, thought, code body, logs, hidden labels or holdout
  outcomes. There is one M1 and one S1 payload for each scheduled holdout run,
  and B0 has none.

## Receipt schema

Every future run would seal a closed JSON receipt with only:
`task_id`, `arm`, `consulted_advice_codes`, `selected_check_codes`, and
`receipt_version`. These are controlled IDs. The receipt records declared route
selection, not hidden reasoning, raw prompt, code, logs, tool output or proof of
actual use.

## Minimal route-only pilot pipeline

The executable-but-not-yet-executed static pipeline is intentionally split into
safe local commands:

1. `prepare_run.py` creates one arm-sliced workspace only. It writes
   `agent_packet.json`, `run_manifest.json`, and `RECEIPT_SCHEMA.json`, and keeps
   `execution_allowed=false`.
2. A later Chati orchestrator may launch a normal task subagent from that packet.
   The task subagent must return JSON only with exactly
   `task_id`, `arm`, `selected_check_codes`, `route_summary`, and `receipt`.
   No code repair, edit, prompt/body dump, private label, tool output or outcome
   field is allowed.
3. `seal_result.py --workspace ... --candidate ... --session-id ses_...`
   validates that JSON against the arm-sliced manifest, binds it to immutable
   source hashes, records the fresh session id/timestamp diagnostics, and writes
   only `sealed_result.json` inside the prepared workspace.
4. `blind_route_scorer.py --sealed .../sealed_result.json` reads private labels
   only through its fixed internal path, hides expected values, excludes arm from
   the assessment input, and writes safe booleans to `blind_score.json`.
5. After the first future run, inspect the sealed result, score, and read-only
   session aggregate before launching the rest of the batch. Do not run all 18
   if the first packet, receipt, score or metric contract fails.
6. `aggregate_compare.py --runs-root ... --metrics-json ...` is only for the
   completed 18-run cohort. It checks exact coverage, common parent/agent/model,
   receipt relevance, quality-before-resource comparisons, and reports only an
   observational claim.

The normal task API's actual model and seed cannot be locked or pinned here, so
any future comparison remains observational rather than causal.

## Pre-registered outcomes

The future quality scorer must be blind to arm. Primary outcomes are quality
pass and wall time. Secondary outcomes are tokens, tool calls and failed tools.
Compare M1 vs B0 and M1 vs S1. Mechanism evidence is evaluable only if M1
improves quality/wall versus both controls and relevant receipt codes align with
the pre-registered allowed family advice. Missing receipt coverage means
`mechanism_not_evaluable`, never zero. There is no composite metric.

## Static safety rules

- Route-manipulation checks are pre-registered in `experiment.json`.
- Public task contents must not leak private labels or hidden expected answers.
- Holdout `route_scope` prose must be nonempty, include both in-scope and
  out-of-scope dimensions, and avoid private/hidden terminology, advice IDs and
  check-code IDs.
- Future launch packets must be prepared through `prepare_run.py` for exactly one
  valid `(task_id, arm)`. The public holdout arm map is kept only as immutable
  source data for static validation and must never be copied whole into an agent
  workspace.
- Agent-facing packets are arm-sliced: B0 has no advice payload or advice codes;
  M1 contains only the current task plus the current relevant payload; S1 contains
  only the current task plus the current sham payload. Private labels, other-arm
  payloads, the counterbalanced schedule, hidden outcomes, raw prompts, thought,
  logs and code are forbidden.
- Preparation requires hash-locked payloads, an explicit absolute runs root and a
  fresh workspace. It writes only `agent_packet.json`, `run_manifest.json` and
  `RECEIPT_SCHEMA.json`; execution is denied by default.
- Relevant and sham advice families must be disjoint for each scheduled run.
- M1/S1 advisory payload pairs must have equal entry count and bounded
  approximate controlled-code-length parity.
- Stop on the first infrastructure/contract failure in any future execution.
- Static validators only are allowed now.

Safe preparation command shape for a later orchestrator:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 experiments/dual_memory_sandbox/mechanism_v2/prepare_run.py \
  --task-id mech-normalize-holdout-01 --arm M1 --runs-root /tmp/mechanism-v2-runs
```

This command prepares files only; it does not execute the run.

Future sealing command shape after a separate normal task subagent has already
returned candidate JSON and Chati has supplied the actual fresh session id:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 experiments/dual_memory_sandbox/mechanism_v2/seal_result.py \
  --workspace /tmp/mechanism-v2-runs/mech-normalize-holdout-01--M1 \
  --candidate /tmp/agent-result.json \
  --session-id ses_actualFreshSubagentSession
```

This validates and seals data only. It does not start or repair anything.
