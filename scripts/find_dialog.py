#!/usr/bin/env python3
"""Fast search in nmbot dialog JSONL logs.

Examples:
  python3 scripts/find_dialog.py --date 2026-07-02 --q 'кварира для инвестуий' --prod
  python3 scripts/find_dialog.py --q 'Южные Сады' --context 1
  python3 scripts/find_dialog.py --uid 433017095 --since 2026-07-02T08:30 --until 2026-07-02T08:40 --prod --json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_LOCAL_LOGS = Path(__file__).resolve().parents[1] / "logs"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"
DEFAULT_INDEX_DB = DEFAULT_CACHE_DIR / "dialog_search.sqlite"
DEFAULT_PROD_HOST = "neiro@193.107.155.236"
DEFAULT_PROD_PORT = "1905"
DEFAULT_PROD_LOGS = "/home/neiro/novostroy-bot/logs"


@dataclass
class Hit:
    path: str
    line_no: int
    obj: dict[str, Any]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.fromisoformat(s + ":00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _event_dt(obj: dict[str, Any]) -> datetime | None:
    ts = obj.get("ts")
    if not isinstance(ts, str):
        return None
    try:
        return _parse_dt(ts)
    except Exception:
        return None


def _haystack(obj: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("user_text", "response_text", "search_response", "dialog_intent", "h_id"):
        val = obj.get(key)
        if val is not None:
            parts.append(str(val))
    return "\n".join(parts).lower()


def _matches(obj: dict[str, Any], args: argparse.Namespace) -> bool:
    if args.uid is not None and str(obj.get("uid")) != str(args.uid):
        return False

    dt = _event_dt(obj)
    since = _parse_dt(args.since)
    until = _parse_dt(args.until)
    if since and (dt is None or dt < since):
        return False
    if until and (dt is None or dt > until):
        return False

    queries = [q.lower() for q in args.q]
    if queries:
        text = _haystack(obj)
        if args.any:
            return any(q in text for q in queries)
        return all(q in text for q in queries)

    return True


def _paths(logs_dir: Path, date: str | None) -> list[Path]:
    if date:
        return [logs_dir / f"dialogs-{date}.jsonl"]
    return sorted(logs_dir.glob("dialogs-*.jsonl"))


def _log_text(obj: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in obj.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            parts.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
        else:
            parts.append(str(value))
    return "\n".join(parts).lower()


def _index_needs_rebuild(db_path: Path, paths: list[Path]) -> bool:
    if not db_path.exists():
        return True
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT value FROM meta WHERE key='logs_signature'").fetchone()
        conn.close()
    except Exception:
        return True
    if not row:
        return True
    current = "|".join(f"{p.name}:{int(p.stat().st_mtime_ns)}:{p.stat().st_size}" for p in paths if p.exists())
    return row[0] != current


def _build_index(db_path: Path, paths: list[Path]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("DROP TABLE IF EXISTS dialogs")
        conn.execute("DROP TABLE IF EXISTS dialogs_fts")
        conn.execute("DROP TABLE IF EXISTS meta")
        conn.execute(
            """
            CREATE TABLE dialogs (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL,
                line_no INTEGER NOT NULL,
                ts TEXT,
                uid TEXT,
                h_id TEXT,
                kind TEXT,
                payload TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE VIRTUAL TABLE dialogs_fts USING fts5(payload, content='dialogs', content_rowid='id', tokenize='unicode61')"
        )
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

        rows: list[tuple[str, int, str | None, str | None, str | None, str | None, str]] = []
        for path in paths:
            if not path.exists():
                continue
            for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                rows.append(
                    (
                        str(path),
                        line_no,
                        obj.get("ts") if isinstance(obj.get("ts"), str) else None,
                        str(obj.get("uid")) if obj.get("uid") is not None else None,
                        str(obj.get("h_id")) if obj.get("h_id") is not None else None,
                        str(obj.get("kind")) if obj.get("kind") is not None else None,
                        _log_text(obj),
                    )
                )

        conn.executemany("INSERT INTO dialogs(path, line_no, ts, uid, h_id, kind, payload) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        conn.execute("INSERT INTO dialogs_fts(rowid, payload) SELECT id, payload FROM dialogs")
        signature = "|".join(f"{p.name}:{int(p.stat().st_mtime_ns)}:{p.stat().st_size}" for p in paths if p.exists())
        conn.execute("INSERT INTO meta(key, value) VALUES ('logs_signature', ?)", (signature,))
        conn.commit()
    finally:
        conn.close()


def _ensure_index(logs_dir: Path, date: str | None, index_db: Path) -> Path:
    paths = _paths(logs_dir, date)
    if _index_needs_rebuild(index_db, paths):
        _build_index(index_db, paths)
    return index_db


def _fts_query(args: argparse.Namespace) -> str:
    if not args.q:
        return ""

    def esc(s: str) -> str:
        return s.replace('"', '""').strip()

    clauses = [f'"{esc(q)}"' for q in args.q if q.strip()]
    if not clauses:
        return ""
    joiner = " OR " if args.any else " AND "
    return joiner.join(clauses)


def search_local_fast(args: argparse.Namespace, logs_dir: Path, index_db: Path) -> list[Hit]:
    if not args.q:
        return search_local(args, logs_dir)

    db_path = _ensure_index(logs_dir, args.date, index_db)
    query = _fts_query(args)
    if not query:
        return search_local(args, logs_dir)

    since = _parse_dt(args.since)
    until = _parse_dt(args.until)
    rows: list[tuple[str, int]] = []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = [
            "SELECT d.path, d.line_no, d.ts, d.uid, d.h_id, d.kind, bm25(dialogs_fts) AS score",
            "FROM dialogs_fts",
            "JOIN dialogs d ON d.id = dialogs_fts.rowid",
            "WHERE dialogs_fts MATCH ?",
        ]
        params: list[Any] = [query]
        if args.uid is not None:
            sql.append("AND d.uid = ?")
            params.append(str(args.uid))
        if since is not None:
            sql.append("AND d.ts >= ?")
            params.append(since.isoformat())
        if until is not None:
            sql.append("AND d.ts <= ?")
            params.append(until.isoformat())
        sql.append("ORDER BY score ASC, d.ts DESC, d.id DESC")
        if args.limit:
            sql.append("LIMIT ?")
            params.append(int(args.limit))
        for row in conn.execute(" ".join(sql), params):
            rows.append((row["path"], int(row["line_no"])))
    finally:
        conn.close()

    if not rows:
        return []

    # Rehydrate the exact JSON rows from the matching files so output stays compatible.
    wanted: dict[str, set[int]] = {}
    for path, line_no in rows:
        wanted.setdefault(path, set()).add(line_no)

    hits: list[Hit] = []
    for path in sorted(wanted):
        p = Path(path)
        if not p.exists():
            continue
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        for line_no in sorted(wanted[path]):
            if 1 <= line_no <= len(lines):
                try:
                    obj = json.loads(lines[line_no - 1])
                except Exception:
                    continue
                if isinstance(obj, dict):
                    hits.append(Hit(path, line_no, obj))
    return hits


def search_local(args: argparse.Namespace, logs_dir: Path) -> list[Hit]:
    hits: list[Hit] = []
    for path in _paths(logs_dir, args.date):
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        matched_indexes: set[int] = set()
        parsed: list[dict[str, Any] | None] = []

        for idx, line in enumerate(lines):
            try:
                obj = json.loads(line)
            except Exception:
                parsed.append(None)
                continue
            parsed.append(obj)
            if _matches(obj, args):
                start = max(0, idx - args.context)
                end = min(len(lines), idx + args.context + 1)
                matched_indexes.update(range(start, end))

        for idx in sorted(matched_indexes):
            obj = parsed[idx]
            if obj is None:
                continue
            hits.append(Hit(str(path), idx + 1, obj))
            if args.limit and len(hits) >= args.limit:
                return hits
    return hits


def _shorten(value: Any, max_len: int) -> Any:
    if not isinstance(value, str):
        return value
    if max_len <= 0 or len(value) <= max_len:
        return value
    return value[:max_len] + f"… <trimmed {len(value) - max_len} chars>"


def _compact(obj: dict[str, Any], max_len: int) -> dict[str, Any]:
    keys = [
        "kind",
        "uid",
        "dialog_id",
        "turn_id",
        "user_text",
        "dialog_intent",
        "params_before",
        "params_after",
        "params_delta",
        "response_text",
        "response_len",
        "duration_ms",
        "is_error",
        "error",
        "ts",
        "h_id",
        "search_response",
        "search_response_len",
        "visible_options",
        "buttons",
    ]
    return {k: _shorten(obj[k], max_len) for k in keys if k in obj}


def print_hits(hits: list[Hit], args: argparse.Namespace) -> None:
    if args.json:
        rows = [{"path": h.path, "line": h.line_no, **h.obj} for h in hits]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    if not hits:
        print("No dialog matches found.")
        return

    for h in hits:
        print(f"--- {h.path}:{h.line_no} ---")
        print(json.dumps(_compact(h.obj, args.max_field_len), ensure_ascii=False, indent=2))


def run_prod(args: argparse.Namespace) -> int:
    remote_args = [
        "python3",
        "-",
        "--root",
        DEFAULT_PROD_LOGS,
    ]
    for q in args.q:
        remote_args += ["--q", q]
    for name in ("date", "uid", "since", "until", "context", "limit", "max_field_len"):
        val = getattr(args, name)
        if val is not None:
            remote_args += ["--" + name.replace("_", "-"), str(val)]
    if args.any:
        remote_args.append("--any")
    if args.json:
        remote_args.append("--json")

    script = Path(__file__).read_text(encoding="utf-8")
    cmd = ["ssh", "-p", args.prod_port, args.prod_host, " ".join(_shell_quote(x) for x in remote_args)]
    proc = subprocess.run(cmd, input=script, text=True)
    return int(proc.returncode)


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fast search in nmbot dialogs-YYYY-MM-DD.jsonl logs")
    p.add_argument("--q", action="append", default=[], help="Text fragment to search. Repeat for AND search; use --any for OR.")
    p.add_argument("--any", action="store_true", help="Match any --q instead of all --q values.")
    p.add_argument("--date", help="Date in YYYY-MM-DD; limits search to dialogs-YYYY-MM-DD.jsonl.")
    p.add_argument("--uid", help="Telegram uid filter.")
    p.add_argument("--since", help="UTC-ish ISO lower bound, e.g. 2026-07-02T08:30 or ...Z.")
    p.add_argument("--until", help="UTC-ish ISO upper bound, e.g. 2026-07-02T08:40 or ...Z.")
    p.add_argument("--context", type=int, default=0, help="Include N neighboring JSONL records around each hit.")
    p.add_argument("--limit", type=int, default=20, help="Maximum rows to print; 0 means unlimited.")
    p.add_argument("--max-field-len", type=int, default=4000, help="Trim long string fields in pretty output; 0 disables trim.")
    p.add_argument("--json", action="store_true", help="Print full raw JSON rows.")
    p.add_argument("--root", type=Path, default=DEFAULT_LOCAL_LOGS, help="Logs directory with dialogs-*.jsonl.")
    p.add_argument("--index-db", type=Path, default=DEFAULT_INDEX_DB, help="SQLite FTS index for faster local search.")
    p.add_argument("--slow", action="store_true", help="Disable the SQLite index and scan JSONL directly.")
    p.add_argument("--prod", action="store_true", help="Search production VPS logs over SSH.")
    p.add_argument("--prod-host", default=DEFAULT_PROD_HOST, help="SSH host for --prod.")
    p.add_argument("--prod-port", default=DEFAULT_PROD_PORT, help="SSH port for --prod.")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.prod:
        return run_prod(args)
    if args.slow:
        hits = search_local(args, args.root)
    else:
        hits = search_local_fast(args, args.root, args.index_db)
    print_hits(hits, args)
    return 0 if hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
