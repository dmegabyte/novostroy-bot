#!/usr/bin/env python3
"""Publish sanitized recent bot dialog history for the public overview page.

The public web server is static, so this script converts private JSONL dialog
logs into a small sanitized `history.json`. It intentionally exposes only a
compact allow-listed subset and masks obvious personal contacts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = ROOT / "logs"
DEFAULT_OUT = ROOT / "public_site" / "nmbot-project-7f3a9c" / "history.json"

PHONE_RE = re.compile(r"(?<!\d)(?:\+?7|8)?[\s\-()]*(?:\d[\s\-()]*){10,}(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TOKEN_RE = re.compile(r"\b(?:sk-or-v1|xoxb|ghp|bot)[A-Za-z0-9_:\-]{12,}\b", re.I)


def sanitize_text(value: Any, *, limit: int = 1200) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    value = PHONE_RE.sub("[телефон скрыт]", value)
    value = EMAIL_RE.sub("[email скрыт]", value)
    value = TOKEN_RE.sub("[токен скрыт]", value)
    value = value.strip()
    if len(value) > limit:
        return value[:limit] + f"… [обрезано, всего {len(value)} символов]"
    return value


def log_dates(days: int) -> list[str]:
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(max(1, days))]


def compact_search(event: dict[str, Any]) -> str:
    trace = event.get("trace") if isinstance(event.get("trace"), dict) else {}
    internal = trace.get("internal") if isinstance(trace.get("internal"), dict) else {}
    value = event.get("search_response") or internal.get("search_response")
    return sanitize_text(value, limit=1800)


def compact_plan(event: dict[str, Any]) -> str:
    trace = event.get("trace") if isinstance(event.get("trace"), dict) else {}
    internal = trace.get("internal") if isinstance(trace.get("internal"), dict) else {}
    return sanitize_text(event.get("dialog_plan") or internal.get("dialog_plan"), limit=900)


def public_item(event: dict[str, Any]) -> dict[str, Any]:
    trace = event.get("trace") if isinstance(event.get("trace"), dict) else {}
    input_trace = trace.get("input") if isinstance(trace.get("input"), dict) else {}
    output_trace = trace.get("output") if isinstance(trace.get("output"), dict) else {}
    internal = trace.get("internal") if isinstance(trace.get("internal"), dict) else {}
    cost = event.get("cost") or output_trace.get("cost") or {}
    return {
        "ts": sanitize_text(event.get("ts") or event.get("timestamp"), limit=80),
        "dialog_id": sanitize_text(event.get("dialog_id"), limit=80),
        "turn_id": event.get("turn_id"),
        "user": sanitize_text(event.get("user_text") or input_trace.get("user_text"), limit=900),
        "bot": sanitize_text(event.get("response_text") or output_trace.get("response_text"), limit=1200),
        "intent": sanitize_text(event.get("dialog_intent") or internal.get("dialog_intent"), limit=500),
        "plan": compact_plan(event),
        "mcp_search": compact_search(event),
        "buttons": sanitize_text(event.get("buttons") or output_trace.get("buttons"), limit=500),
        "cost": sanitize_text(cost, limit=500),
    }


def load_history(*, days: int, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for stamp in log_dates(days):
        path = LOGS_DIR / f"dialogs-{stamp}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("kind") == "user_message":
                events.append(event)
    events.sort(key=lambda item: str(item.get("ts") or item.get("timestamp") or ""), reverse=True)
    return [public_item(event) for event in events[: max(1, limit)]]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish sanitized public NMBOT dialog history")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output history.json path")
    parser.add_argument("--days", type=int, default=3, help="How many UTC days of logs to scan")
    parser.add_argument("--limit", type=int, default=30, help="How many latest turns to publish")
    args = parser.parse_args()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "logs/dialogs-YYYY-MM-DD.jsonl",
        "privacy": "phones/emails/tokens masked; long fields truncated",
        "items": load_history(days=args.days, limit=args.limit),
    }
    out = Path(args.out)
    write_json(out, payload)
    print(f"published {out} items={len(payload['items'])}")


if __name__ == "__main__":
    main()
