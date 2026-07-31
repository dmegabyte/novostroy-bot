"""Read-only TEST callback delivery check by opaque Jivo event reference."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path


def _env_value(key: str) -> str:
    for line in Path("/home/neiro/novostroy-bot/.env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"missing {key}")


def _event_ref(event_id: str) -> str:
    return "event_" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:16]


def _safe_result(record: dict[str, object], event_ref: str) -> dict[str, object]:
    delivery = record.get("sheet_delivery")
    delivery = delivery if isinstance(delivery, dict) else {}
    return {
        "found": True,
        "event_ref": event_ref,
        "lead_ref": str(record.get("lead_ref") or "")[:32],
        "delivery_status": str(delivery.get("status") or "")[:80],
        "row_ref": str(delivery.get("sheet_row_ref") or "")[:80],
    }


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if len(args) != 1:
        raise SystemExit("usage: nmbot_v4_callback_delivery_check.py EVENT_ID")
    event_ref = _event_ref(args[0])
    outbox = Path(_env_value("NMBOT_CALLBACK_OUTBOX_DIR")).expanduser()
    deadline = time.monotonic() + 45
    while True:
        for path in outbox.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict) and record.get("event_ref") == event_ref:
                result = _safe_result(record, event_ref)
                print(json.dumps(result, ensure_ascii=False))
                return 0 if result["delivery_status"] == "sheet_delivered" and result["row_ref"] else 2
        if time.monotonic() >= deadline:
            print(json.dumps({"found": False, "event_ref": event_ref}, ensure_ascii=False))
            return 3
        time.sleep(3)


if __name__ == "__main__":
    main()
