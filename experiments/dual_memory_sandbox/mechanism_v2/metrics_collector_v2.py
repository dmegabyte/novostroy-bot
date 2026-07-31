#!/usr/bin/env python3
"""Read-only OpenCode DB aggregate metrics collector for mechanism-v2.

It accepts an explicit SQLite DB path and one already-known future task session
id.  It never launches OpenCode, never shells out, never reads prompt/body/code,
tool arguments or tool outputs, and emits only the closed normalized aggregate
JSON consumed by aggregate_compare.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


SAFE_SESSION_ID = re.compile(r"^ses_[A-Za-z0-9_-]{1,96}$")
SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
SAFE_AGENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+=-]{0,160}$")
ARMS = {"B0", "M1", "S1"}
MODES = {"B0": "baseline", "M1": "memory", "S1": "sham"}


class MetricsV2Error(ValueError):
    pass


def _safe(value: Any, regex: re.Pattern[str], name: str) -> str:
    if not isinstance(value, str) or not value.strip() or not regex.fullmatch(value):
        raise MetricsV2Error(f"missing or unsafe {name}")
    return value


def _nonnegative(value: Any, name: str) -> int | float:
    if value is None:
        raise MetricsV2Error(f"missing required metric: {name}")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise MetricsV2Error(f"malformed required metric: {name}")
    return value


def _parse_model_identity(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise MetricsV2Error("missing model identity")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MetricsV2Error("model identity must be JSON") from exc
    if not isinstance(parsed, dict):
        raise MetricsV2Error("model identity must be an object")
    provider = parsed.get("providerID", parsed.get("provider_id", parsed.get("provider")))
    model = parsed.get("modelID", parsed.get("model_id", parsed.get("model", parsed.get("id"))))
    variant = parsed.get("variant")
    canonical = {"model_id": model, "provider_id": provider, "variant": variant}
    for key, value in canonical.items():
        _safe(value, SAFE_AGENT, f"model identity {key}")
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_absolute() or not db_path.is_file():
        raise MetricsV2Error("db path must be an existing absolute file")
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def collect_metrics(*, db_path: Path, session_id: str, task_id: str, arm: str, expected_parent_id: str | None = None) -> dict[str, Any]:
    session_id = _safe(session_id, SAFE_SESSION_ID, "session_id")
    task_id = _safe(task_id, SAFE_TASK_ID, "task_id")
    if arm not in ARMS:
        raise MetricsV2Error("arm must be B0/M1/S1")
    if expected_parent_id is not None:
        expected_parent_id = _safe(expected_parent_id, SAFE_SESSION_ID, "expected_parent_id")

    with _connect_ro(db_path.resolve()) as conn:
        rows = conn.execute(
            "SELECT id, parent_id, agent, model, cost, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, time_created, time_updated FROM session WHERE id = ?",
            (session_id,),
        ).fetchall()
        if len(rows) != 1:
            raise MetricsV2Error("missing or duplicate session")
        row = rows[0]
        if row["id"] != session_id:
            raise MetricsV2Error("db session identity mismatch")
        parent_id = _safe(row["parent_id"], SAFE_SESSION_ID, "parent_id")
        if expected_parent_id is not None and parent_id != expected_parent_id:
            raise MetricsV2Error("parent mismatch")
        actual_agent = _safe(row["agent"], SAFE_AGENT, "actual_agent")
        actual_model_identity = _parse_model_identity(row["model"])
        created = _nonnegative(row["time_created"], "time_created")
        updated = _nonnegative(row["time_updated"], "time_updated")
        wall_ms = updated - created
        if wall_ms < 0:
            raise MetricsV2Error("negative wall_ms")

        part_counts = conn.execute(
            "SELECT json_extract(data, '$.type') AS part_type, json_extract(data, '$.state.status') AS part_status, COUNT(*) AS n FROM part WHERE session_id = ? GROUP BY part_type, part_status",
            (session_id,),
        ).fetchall()
        counts = {(r["part_type"], r["part_status"]): int(r["n"]) for r in part_counts}
        tool_calls = sum(n for (ptype, _status), n in counts.items() if ptype == "tool")
        failed_tool_calls = counts.get(("tool", "error"), 0)
        model_calls = sum(n for (ptype, _status), n in counts.items() if ptype == "step-finish")
        retries = sum(n for (ptype, _status), n in counts.items() if ptype == "retry")

    input_tokens = _nonnegative(row["tokens_input"], "tokens_input")
    output_tokens = _nonnegative(row["tokens_output"], "tokens_output")
    reasoning_tokens = _nonnegative(row["tokens_reasoning"], "tokens_reasoning")
    cache_read = _nonnegative(row["tokens_cache_read"], "tokens_cache_read")
    cache_write = _nonnegative(row["tokens_cache_write"], "tokens_cache_write")
    return {
        "schema_version": 1,
        "source": "opencode_db_normalized_aggregates",
        "task_id": task_id,
        "mode": MODES[arm],
        "phase": arm,
        "session_id": session_id,
        "parent_id": parent_id,
        "fresh_subagent_session": True,
        "actual_agent": actual_agent,
        "actual_model_identity": actual_model_identity,
        "diagnostics": {"time_created_ms": created, "time_updated_ms": updated},
        "resources": {
            "wall_ms": wall_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "tokens_cache_read": cache_read,
            "tokens_cache_write": cache_write,
            "cached_tokens": cache_read + cache_write,
            "total_tokens": input_tokens + output_tokens + reasoning_tokens,
            "estimated_provider_cost": _nonnegative(row["cost"], "cost"),
            "model_calls": model_calls,
            "tool_calls": tool_calls,
            "failed_tool_calls": failed_tool_calls,
            "retries": retries,
        },
        "coverage": {"status": "complete", "present": ["wall", "tokens", "cost", "model_calls", "tools", "retries"], "missing": []},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only mechanism-v2 OpenCode DB aggregate collector")
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--arm", required=True, choices=sorted(ARMS))
    parser.add_argument("--expected-parent-id")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(collect_metrics(db_path=args.db_path, session_id=args.session_id, task_id=args.task_id, arm=args.arm, expected_parent_id=args.expected_parent_id), sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
