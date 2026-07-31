# Dual memory sandbox — completed v1 pilot plus isolated mechanism-v2 preparation

Status: **V1_PILOT_COMPLETED_OBSERVATIONAL** for the historical B0→L→M1
pilot, and **MECHANISM_V2_PREPARED_NOT_RUN** only for the new isolated design in
`mechanism_v2/`.

This directory separates public preparation materials from private labels,
hidden verifiers and observer data. Documentation/static maintenance must not
run an agent, model, provider, fixture, hidden verifier, scorer, eval, reviewer,
network, VPS command or production path.

## Historical completed v1 pilot — factual result

The v1 B0→L→M1 pilot completed with the same parent session, agent and model.
These numbers are factual observations only; they are not causal proof and not
container-isolation proof.

- Common parent session: `ses_06157690effeAT4aTz243Gwux3`.
- Actual agent: `executor`.
- Actual model identity: `openai/gpt-5.5/default`.
- Quality: B0 `5/6` → M1 `6/6`; `bound-holdout-01` changed
  `false→true`.
- Wall time: `600731` ms → `287988` ms; delta `-312743` ms.
- Total tokens: `45821` → `51428`; delta `+5607`.
- Tool calls: `42` → `58`; delta `+16`.
- Failed tools: `2` → `1`.
- Claim strength: observational, not causal/container proof.
- Existing telemetry marks retrieval/memory coverage missing. Do not say advice
  consumption was measured.

Transparent conclusion: the completed v1 pilot observed quality restoration and
lower wall time, while tokens and tool calls increased. The mechanism is unknown
because memory/retrieval coverage was absent. This completed pilot must not be
described as `PREPARED_NOT_RUN`.

## Historical v1 design boundary

The simplified v1 design used fresh task-subagent sessions inside the current
OpenCode process:

1. **B0**: six baseline subagents on the holdout IDs with empty memory.
2. **L**: nine learning subagents. Only these could feed advisory memory.
3. Seal canonical learning memory snapshot with no raw prompts, code, logs,
   secrets, private labels, or holdout outcomes.
4. **M1**: six memory subagents on the same holdout IDs with sealed advisory
   memory.
5. A separate read-only scorer/reviewer checked quality first, then resource
   deltas.

The resource claim remains a paired subagent pilot resource comparison. It is
observational evidence only. `time_created_ms` and `time_updated_ms` are
diagnostics, not resource deltas. `total_tokens` is input plus output plus
reasoning tokens; cache is reported separately. There is no artificial composite
score.

## Mechanism-v2 preparation

`experiments/dual_memory_sandbox/mechanism_v2/` is a new isolated
`PREPARED_NOT_RUN` design. It does not modify old v1 execution logic or old
fixtures. It adds B0/M1/S1 controls and a sealed receipt schema to better test
whether memory guides route selection rather than merely adding context. Under
the current task API it still cannot prove causality, because there is no
seed/model lock and receipt data is self-report.

## Allowed static checks now

```bash
PYTHONDONTWRITEBYTECODE=1 python3 experiments/dual_memory_sandbox/sandbox_ctl.py validate-layout
PYTHONDONTWRITEBYTECODE=1 python3 experiments/dual_memory_sandbox/mechanism_v2/validate_layout.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_dual_memory_sandbox_layout.py tests/test_dual_memory_mechanism_v2_layout.py -q
```

These checks are static. They do not start agents, models, providers, hidden
verifiers, scorers, evals, fixtures, network calls, VPS commands or production
code.

Mechanism-v2 run workspaces must be created only by the arm-sliced preparer in
`mechanism_v2/prepare_run.py`. The static public arm map remains source data for
validation only and is not agent-facing.
