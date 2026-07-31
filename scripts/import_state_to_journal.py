#!/usr/bin/env python3
"""Idempotently import retained Jivo dialog windows into the canonical journal."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from .dialogue_journal import append_event, journal_path, _ref
except ImportError:  # direct execution: python3 scripts/import_state_to_journal.py
    from dialogue_journal import append_event, journal_path, _ref


def _identity(session_key: str, role: str, text: str) -> tuple[str | None, str, str]:
    return _ref(session_key), role, text.strip()


def _existing(path: Path) -> set[tuple[str, str, str]]:
    identities: set[tuple[str, str, str]] = set()
    if not path.exists():
        return identities
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_ref = row.get("session_key_ref") or row.get("session_key")
        identities.add((str(session_ref) if session_ref else None, str(row.get("role", "")), str(row.get("text", ""))))
    return identities


def import_state(state_path: Path, journal: Path) -> int:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    existing = _existing(journal)
    imported = 0
    for session_key, value in state.items():
        if not str(session_key).startswith("jivo:") or not isinstance(value, dict):
            continue
        window = value.get("dialog_window") or []
        meta = {"site_id": session_key.split(":")[1] if session_key.count(":") >= 3 else ""}
        for index, turn in enumerate(window):
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role", "")).strip()
            text = str(turn.get("text", "")).strip()
            if role not in {"user", "bot"} or not text:
                continue
            identity = _identity(session_key, role, text)
            if identity in existing:
                continue
            event_id = "state-migration:" + hashlib.sha256(f"{session_key}:{index}:{role}:{text}".encode()).hexdigest()[:24]
            append_event(session_key=session_key, role=role, text=text, event_type="state_migration",
                         event_id=event_id, meta=meta, source="state_migration", journal=journal)
            existing.add(identity)
            imported += 1
    return imported


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--journal", type=Path, default=journal_path())
    args = parser.parse_args()
    print(f"imported={import_state(args.state, args.journal)} journal={args.journal}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
