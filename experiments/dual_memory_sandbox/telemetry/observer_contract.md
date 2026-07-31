# Observer contract — task-subagent DB aggregates

- Future order is fixed: B0 on six holdouts with empty memory, L on nine learning
  tasks, sealed advisory memory, M1 on the same six holdouts, then a separate
  read-only scorer/reviewer.
- The future execution unit is a fresh task-subagent session in the current
  OpenCode. No separate OpenCode launch, callback plugin, container, relay or
  network-isolation proof is part of this design.
- The task API does not guarantee an exact model selector here. Actual `agent` and
  model identity are collected after the run from the local OpenCode DB.
- `telemetry/session_metrics.py` is stdlib-only and read-only. It obtains the DB
  path via `opencode db path` with `shell=False`, or an explicit `--db-path`, opens
  SQLite as `mode=ro`, and uses parameterized SQL. Its CLI prints JSON to stdout
  only. The controlled `sandbox_ctl.py collect-session` wrapper writes only
  `<run_dir>/observer/session_summary.json` for a prepared run under `RUNS_ROOT`.
- Allowed session columns are only `id`, `parent_id`, `agent`, `model`, `cost`,
  normalized token columns and timestamps. Allowed `part` access is aggregate
  counting for `tool`, failed `tool`, `step-finish` and `retry` rows for one exact
  session ID.
- Forbidden output remains prompt/body/code, message bodies, tool args, tool
  output, raw logs, secrets, private labels, expected answers and hidden
  assertions.
- A B0/M1 pair is evaluable only with different non-empty child session IDs,
  `fresh_subagent_session=true`, and one non-empty experiment `parent_id`, one
  actual agent and one actual model identity across all 12 holdout sessions, plus
  matching task/fixture/memory contracts.
- Quality is checked before resource claims. A scored B0 quality failure is
  baseline evidence and does not by itself stop authorized L/M1 continuation.
  `baseline_pass=false` to `memory_pass=true` is reportable as `improved`;
  `baseline_pass=true` to `memory_pass=false` is `regressed` and blocks any
  resource claim. `still_failed` also blocks resource reporting because M1 must
  pass all six holdouts. Any memory false success, safety regression or budget
  regression blocks the claim. Retrieval and memory hint metrics may be not
  evaluable; primary OpenCode DB metrics can still support the paired pilot claim
  only after the M1 all-holdout quality gate passes.
- Finalization has one path: guarded `seal-run --quality-result <path>`. It merges
  prepared metadata, `session_summary.json`, and a closed sealed quality result;
  it does not fall back to process summaries or event logs.
