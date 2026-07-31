#!/usr/bin/env python3
"""Search the canonical Jivo dialogue journal locally or on production."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_PATH = Path(__file__).resolve().parent.parent / "logs" / "dialogue_journal.jsonl"
PROD_HOST = "neiro@193.107.155.236"
PROD_PORT = "1905"
PROD_PATH = "/home/neiro/novostroy-bot/logs/dialogue_journal.jsonl"


def search(path: Path, queries: list[str], any_query: bool, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    groups: dict[str, list[dict[str, Any]]] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = row.get("conversation_ref") or row.get("session_key_ref") or f"line:{line_no}"
        groups.setdefault(str(key), []).append({"path": str(path), "line": line_no, **row})

    out = []
    for rows in groups.values():
        haystack = "\n".join(json.dumps(row, ensure_ascii=False).lower() for row in rows)
        matches = [q.lower() in haystack for q in queries]
        if queries and ((any_query and not any(matches)) or (not any_query and not all(matches))):
            continue
        for row in rows:
            out.append(row)
            if limit and len(out) >= limit:
                return out
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--q", action="append", default=[])
    p.add_argument("--any", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.add_argument("--path", type=Path, default=DEFAULT_PATH)
    p.add_argument("--prod", action="store_true")
    args = p.parse_args()
    if args.prod:
        remote_args = ["python3", "-", "--path", PROD_PATH, "--limit", str(args.limit)]
        for q in args.q:
            remote_args += ["--q", q]
        if args.any:
            remote_args.append("--any")
        if args.json:
            remote_args.append("--json")
        remote_command = " ".join(shlex.quote(value) for value in remote_args)
        cmd = ["ssh", "-p", PROD_PORT, PROD_HOST, remote_command]
        script = Path(__file__).read_text(encoding="utf-8")
        return subprocess.run(cmd, input=script, text=True).returncode
    rows = search(args.path, args.q, args.any, args.limit)
    print(json.dumps(rows, ensure_ascii=False, indent=2) if args.json else "\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
