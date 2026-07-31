# NMBOT developer baseline

This is the reproducible local baseline for the current Jivo project. It is not
production evidence and does not call Jivo, VPS, providers or external APIs.

## Canonical local gate

The only dispatcher is `scripts/nmbot_check.py`; the command manifest is
`tests/nmbot_check_manifest.yaml`. The manifest forbids network, SSH, secrets,
model calls, deploy and service restarts.

```bash
python3 scripts/nmbot_check.py docs
python3 scripts/nmbot_check.py runtime
python3 scripts/nmbot_check.py quality
```

The performance measurement wraps that same dispatcher rather than implementing
another test framework:

```bash
python3 scripts/nmbot_check_benchmark.py docs --runs 5 --warmup 1 --json
python3 scripts/nmbot_check_benchmark.py runtime --runs 5 --warmup 1 --json
```

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
