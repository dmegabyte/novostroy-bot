# NMBOT developer baseline

This is the reproducible local baseline for the current Jivo project. It is not
production evidence and does not call Jivo, VPS, providers or external APIs.

## Canonical local gate

The only dispatcher is `scripts/nmbot_check.py`; the command manifest is
`tests/nmbot_check_manifest.yaml`. The manifest forbids network, SSH, secrets,
model calls, deploy and service restarts.

```bash
python3 scripts/nmbot_check.py docs
python3 scripts/nmbot_check.py v0       # or v1 / v2 / v3
python3 scripts/nmbot_check.py runtime
python3 scripts/nmbot_check.py quality
python3 scripts/nmbot_check.py release  # full local pre-release aggregate
```

Use three levels instead of one broad default:

1. **Component:** run the focused `related_test` returned by `nmbot.py navigate`.
2. **Version:** run exactly one owning scope: `v0`, `v1`, `v2`, `v3`, or
   `runtime` for the shared legacy adapter.
3. **Release:** run `release`, which aggregates docs, external contracts, all
   version owners (V0/V1/V2/V3), runtime, audit, quality, worker-artifact and
   isolated-router gates.

V0 owns `test_nmbot_v0_runtime.py` and `test_nmbot_v0_answer_writer.py`; V2
owns `test_nmbot_v2_runtime.py` and `test_nmbot_v2_replay.py`. The runtime
adapter suite has one owner: `runtime`. Router/artifact/cutover checks stay
outside daily component and version work.

The performance measurement wraps that same dispatcher rather than implementing
another test framework:

```bash
python3 scripts/nmbot_check_benchmark.py docs --runs 5 --warmup 1 --json
python3 scripts/nmbot_check_benchmark.py runtime --runs 5 --warmup 1 --json
python3 scripts/nmbot_check_benchmark.py release --runs 3 --warmup 0 --json
```

For context-workflow measurement, verbose route outputs are locked to a new
directory before scoring:

```bash
python3 scripts/nmbot_check_benchmark.py --context-benchmark \
  --output-dir /tmp/opencode/nmbot-context-benchmark-$(date +%s) --json
```

This benchmark reports route quality separately from context/resource use. A
small context is not a success when the owner is wrong. Vague questions remain
supervised: navigate first, choose one of at most five candidates, then run the
strict context gate. The machine contract is `selection_required=true` plus
stable candidate IDs `c1..c5`; `--select cN` records the explicit choice.
Automatic gating is allowed only when navigation emits
`selection_required=false` and one `selected_target_spec`. Exact standalone
symbols and stage IDs may therefore go directly to STOP-2, while symbol words
embedded in natural-language questions remain supervised candidates. When a
selected symbol target includes `start_line`/`end_line`, pass that pair through
`--target-start-line`/`--target-end-line`; the gate verifies it against the
fresh registry before reading source.

The report contains end-to-end `p50`/`p95`, all measured samples and an explicit
no network/no-secret/no-model boundary. It stops after the first failed run.
Numbers are machine-specific: compare measurements only on the same environment
and record the date, Python version, scopes, run count and warmup count.

### Recorded local baseline — 2026-07-23

Environment: current development host, Python 3.12.3, five measured runs after
one warmup. These numbers describe local gate execution only:

| Scope | p50 | p95 | Result |
|---|---:|---:|---|
| `docs` | 1251.201 ms | 1284.123 ms | passed |
| `runtime` | 4163.781 ms | 4304.599 ms | passed |

Raw samples remain available in the benchmark command output. Re-run on the
same host before treating a later difference as a regression.

## CI

GitHub Actions workflow `.github/workflows/nmbot-local-fast-gate.yml` runs the
existing `docs contracts quality` gate on push, pull request and manual dispatch
using Python 3.12. The `quality` scope replays 15 fixed scenarios, including
family, investment, rental and life, through the deterministic offline quality
evaluator. It cannot call a model, MCP, VPS or Jivo: the dispatcher only allows
`scripts/nmbot_v2_quality_gate.py --all` and rejects `--live`.

This is a regression gate for contracts, grounding and stable answer heuristics.
It does not prove the quality of a newly generated model answer and does not
replace an explicitly approved isolated model probe or live Jivo evidence.

## Boundaries

- Fast/targeted local gate: no network, secrets, model calls or production writes.
- `nmbot_diag.sh --vps`: read-only network diagnostics, outside this benchmark.
- Full pytest, deploy, release verification and Jivo smoke: separate explicit gates.
- A local timing regression is investigation evidence, not proof of production latency.
