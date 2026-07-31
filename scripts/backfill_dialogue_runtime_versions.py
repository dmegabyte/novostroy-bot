#!/usr/bin/env python3
"""Build a safe sidecar with historical dialogue runtime versions.

The canonical dialogue_journal.jsonl is append-only and is never modified here.
This script writes only a minimal sidecar keyed by 1-based journal line number.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JOURNAL = ROOT / "logs" / "dialogue_journal.jsonl"
DEFAULT_OUTPUT = ROOT / "logs" / "dialogue_runtime_versions_backfill.jsonl"
VALID_VERSIONS = {"V0", "V2", "V3"}
VERSION_BY_START_COMMAND = {"/start_0": "V0", "/start_2": "V2", "/start_3": "V3"}
LIFECYCLE_RE = re.compile(r"Сейчас\s+активна\s+версия:\s*(V0|V2|V3)\.", re.I)


def normalize_runtime_version(value: Any) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in VALID_VERSIONS else None


def normalized_text(value: Any) -> str:
    return str(value or "").strip().lower()


def explicit_start_version(row: dict[str, Any]) -> str | None:
    return VERSION_BY_START_COMMAND.get(normalized_text(row.get("text")))


def is_plain_start(row: dict[str, Any]) -> bool:
    return normalized_text(row.get("text")) in {"/start", "start"}


def lifecycle_text_version(row: dict[str, Any]) -> str | None:
    match = LIFECYCLE_RE.search(str(row.get("text") or ""))
    return match.group(1).upper() if match else None


def safe_ref(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text.startswith("sha256:") else None


def session_ref(row: dict[str, Any], line_no: int) -> str:
    return safe_ref(row.get("session_key_ref")) or safe_ref(row.get("conversation_ref")) or f"line:{line_no}"


def sidecar_refs(row: dict[str, Any]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for key in ("session_key_ref", "conversation_ref", "event_id_ref"):
        ref = safe_ref(row.get(key))
        if ref:
            refs[key] = ref
    return refs


def read_journal(path: Path) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    total = 0
    invalid = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, 1):
            total = line_no
            raw = line.strip()
            if not raw:
                rows.append({"line_no": line_no, "row": None, "invalid": True})
                invalid += 1
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                rows.append({"line_no": line_no, "row": None, "invalid": True})
                invalid += 1
                continue
            if not isinstance(parsed, dict):
                rows.append({"line_no": line_no, "row": None, "invalid": True})
                invalid += 1
                continue
            rows.append({"line_no": line_no, "row": parsed, "invalid": False})
    return rows, total, invalid


def event_attribution(rows: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    by_event: dict[str, dict[str, str]] = {}
    for item in rows:
        row = item.get("row")
        if not isinstance(row, dict):
            continue
        event_ref = safe_ref(row.get("event_id_ref"))
        if not event_ref:
            continue
        explicit = explicit_start_version(row)
        lifecycle = lifecycle_text_version(row)
        if explicit or lifecycle:
            bucket = by_event.setdefault(event_ref, {})
            if explicit:
                bucket["explicit_start"] = explicit
            if lifecycle:
                bucket.setdefault("lifecycle_text", lifecycle)
    out: dict[str, tuple[str, str]] = {}
    for event_ref, bucket in by_event.items():
        if "explicit_start" in bucket:
            out[event_ref] = (bucket["explicit_start"], "explicit_start")
        elif "lifecycle_text" in bucket:
            out[event_ref] = (bucket["lifecycle_text"], "lifecycle_text")
    return out


def build_sidecar_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    event_versions = event_attribution(rows)
    overrides: dict[str, str | None] = {}
    output: list[dict[str, Any]] = []
    counts = {
        "existing": 0,
        "explicit_start": 0,
        "lifecycle_text": 0,
        "session_override": 0,
        "insufficient_history": 0,
        "UNKNOWN": 0,
        "V0": 0,
        "V2": 0,
        "V3": 0,
    }

    for item in rows:
        line_no = int(item["line_no"])
        row = item.get("row")
        version = "UNKNOWN"
        source = "insufficient_history"
        refs: dict[str, str] = {}

        if isinstance(row, dict):
            refs = sidecar_refs(row)
            event_ref = safe_ref(row.get("event_id_ref"))
            sess = session_ref(row, line_no)
            existing = normalize_runtime_version(row.get("runtime_version"))
            event_version = event_versions.get(event_ref or "")
            direct_lifecycle = lifecycle_text_version(row)
            override_before = overrides.get(sess)

            if existing:
                version, source = existing, "existing"
            elif event_version:
                version, source = event_version
            elif direct_lifecycle:
                version, source = direct_lifecycle, "lifecycle_text"
            elif override_before:
                version, source = override_before, "session_override"

            command_version = explicit_start_version(row)
            if command_version and event_ref:
                overrides[sess] = command_version
            elif is_plain_start(row) and event_ref:
                overrides[sess] = None

        sidecar_row = {"line_no": line_no, "runtime_version": version, "source": source}
        sidecar_row.update(refs)
        output.append(sidecar_row)
        counts[source] += 1
        counts[version] += 1
    return output, counts


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe runtime_version sidecar backfill for dialogue_journal.jsonl.")
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true", help="Do not write sidecar. This is the default unless --write is set.")
    parser.add_argument("--write", action="store_true", help="Atomically write the sidecar JSONL file.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    journal = args.journal.expanduser()
    output = args.output.expanduser()
    if not journal.exists():
        print(f"Ошибка: journal не найден: {journal}", file=sys.stderr)
        return 2
    rows, total, invalid = read_journal(journal)
    sidecar_rows, counts = build_sidecar_rows(rows)
    mode = "write" if args.write else "dry-run"
    if args.write:
        write_jsonl_atomic(output, sidecar_rows)
    summary = {
        "mode": mode,
        "journal": str(journal),
        "output": str(output),
        "total_rows": total,
        "sidecar_rows": len(sidecar_rows),
        "invalid_json_rows": invalid,
        "runtime_versions": {k: counts[k] for k in ("V0", "V2", "V3", "UNKNOWN")},
        "sources": {k: counts[k] for k in ("existing", "explicit_start", "lifecycle_text", "session_override", "insufficient_history")},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
