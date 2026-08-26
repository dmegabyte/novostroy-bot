#!/usr/bin/env python3
"""Read-only CRM delivery receipt by an opaque Jivo session reference."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SESSION_SUFFIX_RE = re.compile(r"^(?:sha256:|session_)?([0-9a-f]{12,16})$")
_OPAQUE_RECEIPT_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


def _opaque_receipt(value: object) -> str:
    receipt = str(value or "")
    return receipt if _OPAQUE_RECEIPT_RE.fullmatch(receipt) else ""


def _safe_result(record: dict[str, Any]) -> dict[str, object]:
    delivery = record.get("crm_delivery")
    delivery = delivery if isinstance(delivery, dict) else {}
    return {
        "found": True,
        "schema": "nmbot.crm_delivery_receipt.v1",
        "lead_ref": str(record.get("lead_ref") or "")[:32],
        "crm_status": str(delivery.get("status") or "")[:80],
        "attempts": min(max(int(delivery.get("attempts") or 0), 0), 99),
        "error_class": str(delivery.get("last_error_class") or "")[:80],
        "receipt": _opaque_receipt(delivery.get("receipt")),
        "delivered_at_utc": str(delivery.get("delivered_at_utc") or "")[:40],
    }


def _created_order(record: dict[str, Any]) -> tuple[int, str]:
    return int(record.get("created_at") or 0), str(record.get("created_at_utc") or "")


def _since_epoch(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit("--since-utc must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp())


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    latest = len(args) == 3 and args[0] == "--latest" and args[1] == "--since-utc"
    if not latest and len(args) != 1:
        raise SystemExit("usage: nmbot_callback_crm_delivery_check.py SESSION_REF | --latest --since-utc ISO_TIMESTAMP")
    suffix = ""
    since = 0
    if latest:
        since = _since_epoch(args[2])
    else:
        match = _SESSION_SUFFIX_RE.fullmatch(args[0].strip())
        if not match:
            raise SystemExit("SESSION_REF must be an opaque 12-16-character hash reference")
        suffix = match.group(1)
    root_value = str(os.getenv("NMBOT_CALLBACK_OUTBOX_DIR") or "").strip()
    if not root_value:
        print(json.dumps({"found": False, "reason": "outbox_unavailable"}))
        return 2
    matches: list[dict[str, Any]] = []
    for path in Path(root_value).expanduser().glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        if latest:
            if int(record.get("created_at") or 0) >= since and isinstance(record.get("crm_delivery"), dict):
                matches.append(record)
        elif str(record.get("session_ref") or "").endswith(suffix):
            matches.append(record)
    if not matches:
        print(json.dumps({"found": False, "schema": "nmbot.crm_delivery_receipt.v1"}))
        return 3
    if not latest and len(matches) != 1:
        print(json.dumps({"found": False, "schema": "nmbot.crm_delivery_receipt.v1", "reason": "ambiguous_session_ref"}))
        return 4
    record = max(matches, key=_created_order) if latest else matches[0]
    result = _safe_result(record)
    if latest:
        result["created_at_utc"] = str(record.get("created_at_utc") or "")[:40]
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
