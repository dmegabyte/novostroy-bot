from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


SAFE_SESSION_ID = re.compile(r"^ses_[A-Za-z0-9_-]{1,96}$")
SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
SAFE_MODE = {"baseline", "learning", "memory", "scorer"}
SAFE_PHASE = {"B0", "L", "M1", "S"}


class SessionMetricsError(ValueError):
    pass


def _safe_id(value: str, regex: re.Pattern[str], name: str) -> str:
    if not isinstance(value, str) or not regex.fullmatch(value):
        raise SessionMetricsError(f"unsafe {name}: {value!r}")
    return value


def opencode_db_path() -> Path:
    result = subprocess.run(["opencode", "db", "path"], shell=False, text=True, capture_output=True, check=True, timeout=10)
    path = result.stdout.strip().splitlines()[-1].strip()
    if not path:
        raise SessionMetricsError("opencode db path returned empty output")
    return Path(path).expanduser().resolve()


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_model_identity(raw: Any) -> str:
    if raw is None or raw == "":
        raise SessionMetricsError("missing model identity")
    if not isinstance(raw, str):
        raise SessionMetricsError("malformed model value")
    text = raw.strip()
    if text[:1] in "[{":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SessionMetricsError("malformed model JSON") from exc
        if isinstance(parsed, dict):
            provider = parsed.get("providerID", parsed.get("provider_id", parsed.get("provider")))
            model = parsed.get("modelID", parsed.get("model_id", parsed.get("model", parsed.get("id"))))
            variant = parsed.get("variant")
            canonical = {"provider_id": provider, "model_id": model, "variant": variant}
            if not all(isinstance(value, str) and value for value in canonical.values()):
                raise SessionMetricsError("model identity requires provider_id, model_id and variant strings")
            return json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        raise SessionMetricsError("malformed model JSON")
    raise SessionMetricsError("plain string model identity is insufficient without provider_id and variant")


def _nonnegative_number(value: Any, name: str) -> int | float:
    if value is None:
        return 0
    if not isinstance(value, (int, float)) or value < 0:
        raise SessionMetricsError(f"negative or malformed metric: {name}")
    return value


def collect_session_metrics(*, session_id: str, task_id: str, mode: str, phase: str, expected_parent_id: str | None = None, db_path: Path | None = None) -> dict[str, Any]:
    session_id = _safe_id(session_id, SAFE_SESSION_ID, "session_id")
    task_id = _safe_id(task_id, SAFE_TASK_ID, "task_id")
    if mode not in SAFE_MODE:
        raise SessionMetricsError(f"unsafe mode: {mode!r}")
    if phase not in SAFE_PHASE:
        raise SessionMetricsError(f"unsafe phase: {phase!r}")
    if expected_parent_id is not None:
        _safe_id(expected_parent_id, SAFE_SESSION_ID, "expected_parent_id")
    db_path = Path(db_path).resolve() if db_path else opencode_db_path()
    if not db_path.is_file():
        raise SessionMetricsError("db path does not exist")

    with _connect_ro(db_path) as conn:
        rows = conn.execute(
            "SELECT id, parent_id, agent, model, cost, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, time_created, time_updated FROM session WHERE id = ?",
            (session_id,),
        ).fetchall()
        if len(rows) != 1:
            raise SessionMetricsError("missing or duplicate session")
        row = rows[0]
        parent_id = row["parent_id"]
        if expected_parent_id is not None and parent_id != expected_parent_id:
            raise SessionMetricsError("parent mismatch")
        if not parent_id or not SAFE_SESSION_ID.fullmatch(str(parent_id)):
            raise SessionMetricsError("missing or unsafe parent_id")
        actual_agent = row["agent"]
        if not isinstance(actual_agent, str) or not actual_agent:
            raise SessionMetricsError("missing actual agent")
        actual_model_identity = _parse_model_identity(row["model"])
        created = _nonnegative_number(row["time_created"], "time_created")
        updated = _nonnegative_number(row["time_updated"], "time_updated")
        wall_ms = updated - created
        if wall_ms < 0:
            raise SessionMetricsError("negative wall_ms")

        def count_parts(where: str, params: tuple[Any, ...] = ()) -> int:
            value = conn.execute(f"SELECT COUNT(*) FROM part WHERE session_id = ? AND {where}", (session_id, *params)).fetchone()[0]
            return int(value)

        tool_calls = count_parts("json_extract(data, '$.type') = 'tool'")
        failed_tool_calls = count_parts("json_extract(data, '$.type') = 'tool' AND json_extract(data, '$.state.status') = 'error'")
        model_calls = count_parts("json_extract(data, '$.type') = 'step-finish'")
        retries = count_parts("json_extract(data, '$.type') = 'retry'")

    cache_read = _nonnegative_number(row["tokens_cache_read"], "tokens_cache_read")
    cache_write = _nonnegative_number(row["tokens_cache_write"], "tokens_cache_write")
    resources = {
        "wall_ms": wall_ms,
        "input_tokens": _nonnegative_number(row["tokens_input"], "tokens_input"),
        "output_tokens": _nonnegative_number(row["tokens_output"], "tokens_output"),
        "reasoning_tokens": _nonnegative_number(row["tokens_reasoning"], "tokens_reasoning"),
        "tokens_cache_read": cache_read,
        "tokens_cache_write": cache_write,
        "cached_tokens": cache_read + cache_write,
        "total_tokens": _nonnegative_number(row["tokens_input"], "tokens_input") + _nonnegative_number(row["tokens_output"], "tokens_output") + _nonnegative_number(row["tokens_reasoning"], "tokens_reasoning"),
        "estimated_provider_cost": _nonnegative_number(row["cost"], "cost"),
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "failed_tool_calls": failed_tool_calls,
        "retries": retries,
    }
    return {
        "schema_version": 1,
        "source": "opencode_db_normalized_aggregates",
        "task_id": task_id,
        "mode": mode,
        "phase": phase,
        "session_id": session_id,
        "parent_id": parent_id,
        "fresh_subagent_session": True,
        "actual_agent": actual_agent,
        "actual_model_identity": actual_model_identity,
        "diagnostics": {"time_created_ms": created, "time_updated_ms": updated},
        "resources": resources,
        "coverage": {"present": ["wall", "tokens", "model_calls", "tools"], "missing": ["retrieval", "memory"]},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only OpenCode DB aggregate collector")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--expected-parent-id")
    parser.add_argument("--db-path")
    args = parser.parse_args(argv)
    try:
        summary = collect_session_metrics(session_id=args.session_id, task_id=args.task_id, mode=args.mode, phase=args.phase, expected_parent_id=args.expected_parent_id, db_path=Path(args.db_path).resolve() if args.db_path else None)
        print(json.dumps(summary, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
