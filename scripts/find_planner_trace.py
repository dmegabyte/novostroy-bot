#!/usr/bin/env python3
"""Search sanitized planner trace JSONL locally or on production."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from dialogue_journal import _ref
except ImportError:  # pragma: no cover
    from .dialogue_journal import _ref  # type: ignore


DEFAULT_DIR = Path(__file__).resolve().parent.parent / "logs"
PROD_HOST = "neiro@193.107.155.236"
PROD_PORT = "1905"
PROD_ROOT = "/home/neiro/novostroy-bot"
PROD_DIR = "/home/neiro/novostroy-bot/logs"
SAFE_PRINT_KEYS = {
    "schema_version", "ts", "channel", "source", "session_key_ref", "conversation_ref",
    "action", "dialog_action", "intent", "intent_policy", "target", "search_policy", "scope",
    "confidence", "canonical_errors", "canonical_error_codes", "canonical_valid", "fallback_used",
    "repair_attempted", "repair_applied", "final_decision", "planner_exception_code", "path", "line",
    "user_text_truncated", "planner_raw_response_truncated", "raw_response_present",
}
RAW_PRINT_KEYS = {"user_text", "planner_raw_response"}


def trace_file(base_dir: Path, date: str) -> Path:
    return base_dir / f"planner_trace-{date}.jsonl"


def _get_nested(row: dict[str, Any], dotted: str) -> Any:
    current: Any = row
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _parse_field(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise ValueError(f"field filter must be key=value: {raw}")
    key, value = raw.split("=", 1)
    key = key.strip()
    if not key:
        raise ValueError("field filter key is empty")
    return key, value.strip()


def _matches(row: dict[str, Any], filters: list[tuple[str, str]], ref: str | None, query: str | None) -> bool:
    if ref:
        expected = _ref(ref)
        if row.get("conversation_ref") != expected and row.get("session_key_ref") != expected:
            return False
    for key, expected in filters:
        actual = _get_nested(row, key)
        if isinstance(actual, bool):
            if expected.lower() not in {"true", "false"} or actual is not (expected.lower() == "true"):
                return False
        elif str(actual) != expected:
            return False
    if query:
        needle = query.casefold()
        haystack = "\n".join(str(row.get(key) or "") for key in ("user_text", "planner_raw_response"))
        if needle not in haystack.casefold():
            return False
    return True


def _sanitize(row: dict[str, Any], *, show_raw: bool = False) -> dict[str, Any]:
    allowed = SAFE_PRINT_KEYS | (RAW_PRINT_KEYS if show_raw else set())
    return {key: value for key, value in row.items() if key in allowed}


def search(path: Path, *, fields: list[str], ref: str | None = None, limit: int = 20, query: str | None = None, show_raw: bool = False) -> list[dict[str, Any]]:
    filters = [_parse_field(item) for item in fields]
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        row = {"path": str(path), "line": line_no, **row}
        if _matches(row, filters, ref, query):
            rows.append(_sanitize(row, show_raw=show_raw))
            if limit and len(rows) >= limit:
                break
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="UTC date, YYYY-MM-DD")
    parser.add_argument("--field", action="append", default=[], help="safe exact filter, e.g. action=search or final_decision.action=search")
    parser.add_argument("--ref", help="raw local session key to hash before matching; never printed")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--query", help="case-insensitive substring search in redacted user_text/planner_raw_response")
    parser.add_argument("--show-raw", action="store_true", help="print redacted user_text and planner raw response fields")
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--prod", action="store_true", help="read production trace over SSH; no secret args accepted")
    args = parser.parse_args(argv)

    if args.prod:
        remote_args = ["python3", "-", "--date", args.date, "--dir", PROD_DIR, "--limit", str(args.limit)]
        for field in args.field:
            remote_args += ["--field", field]
        if args.ref:
            remote_args += ["--ref", args.ref]
        if args.json:
            remote_args.append("--json")
        if args.query:
            remote_args += ["--query", args.query]
        if args.show_raw:
            remote_args.append("--show-raw")
        remote_command = (
            f"cd {shlex.quote(PROD_ROOT)} && "
            "PYTHONPATH=scripts "
            + " ".join(shlex.quote(value) for value in remote_args)
        )
        cmd = ["ssh", "-p", PROD_PORT, PROD_HOST, remote_command]
        script = Path(__file__).read_text(encoding="utf-8")
        return subprocess.run(cmd, input=script, text=True).returncode

    rows = search(trace_file(args.dir, args.date), fields=args.field, ref=args.ref, limit=args.limit, query=args.query, show_raw=args.show_raw)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
