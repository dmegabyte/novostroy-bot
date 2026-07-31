# NMBOT Four-Layer Hypothesis — 2026-07-16

Source refs:
- `scripts/nmbot_four_layer_hypothesis.py`
- `tests/test_nmbot_four_layer_hypothesis.py`
- live probe outputs: `pty_d2407ed2`, `pty_71af7562`, `pty_4cd96757`

## Hypothesis

Proposed simplification: 4 logical layers
1. planner
2. MCP search
3. deterministic validator
4. presenter

Expected benefit: fewer duplicated prompt responsibilities, stricter constraint enforcement, safer presenter output.

## Live probe results

All three presenter-only live scenarios passed invariants.

| Scenario | Result | Notes |
|---|---|---|
| hard_constraints | pass | one exact option, 1 final question, no leaked option ids, no forbidden claims |
| unsupported_claim | pass | presenter did not surface `liquidity/demand/yield`, 1 final question |
| no_match | pass | empty items, 1 final question, relaxation question only |

Observed latencies:
- hard_constraints: ~4.98s
- unsupported_claim: ~16.20s
- no_match: ~9.56s

## What is confirmed

- The presenter layer can render a safe `DecisionContext` without inventing extra options.
- Forbidden claims are suppressible when they are already encoded in `do_not_say`.
- No-match can remain honest and concise.

## What is still unproven

- End-to-end user quality for full dialogues.
- Whether the four-layer split is enough for all current runtime branches.
- Cost/latency improvement versus the current pipeline.

## Conclusion

The hypothesis is **promising at the presenter boundary** and is safe for the tested classes.
It is **not yet fully proven end-to-end**; the missing evidence is a broader A/B run and full dialogue regressions.
