"""Private, local, idempotent callback outbox; it performs no delivery I/O."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "nmbot.callback_outbox.v1"
_PHONE = re.compile(r"(?:\+?\d[\s().-]*){10,15}")
_SENSITIVE = re.compile(r"phone|телефон|contact|client_id|chat_id|site_id|sender|token|secret|raw|payload", re.IGNORECASE)


@dataclass(frozen=True)
class OutboxResult:
    status: str
    lead_ref: str

    def public(self) -> dict[str, str]:
        return {"status": self.status, "lead_ref": self.lead_ref}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_value(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if isinstance(value, str):
        result = value.strip()[:500]
        return "" if _PHONE.search(result) else result
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return None if len(str(abs(value))) >= 10 else value
    if isinstance(value, float):
        return value
    if isinstance(value, list):
        return [_safe_value(item, depth + 1) for item in value[:8]]
    if isinstance(value, Mapping):
        return {str(key)[:80]: cleaned for key, item in value.items() if not _SENSITIVE.search(str(key)) and (cleaned := _safe_value(item, depth + 1)) not in (None, "", [], {})}
    rendered = str(value)[:200]
    return "" if _PHONE.search(rendered) else rendered


class LocalCallbackOutbox:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    @staticmethod
    def deterministic_key(*, session_key: str, event_id: str, normalized_phone: str) -> str:
        event = str(event_id or "").strip()
        material = f"event\0{session_key}\0{event}" if event else f"phone\0{session_key}\0{''.join(c for c in normalized_phone if c.isdigit())}"
        return _sha(material)

    def enqueue(self, *, session_key: str, event_id: str, normalized_phone: str, context: Mapping[str, Any]) -> OutboxResult:
        if not str(session_key or "") or not str(normalized_phone or ""):
            raise ValueError("session_key_and_phone_required")
        key = self.deterministic_key(session_key=session_key, event_id=event_id, normalized_phone=normalized_phone)
        lead_ref, target = "cb_" + key[:16], self.root / f"{key}.json"
        if target.exists():
            return OutboxResult("duplicate", lead_ref)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        record = {
            "schema": SCHEMA, "created_at": now, "lead_ref": lead_ref, "idempotency_key_sha256": key,
            "session_ref": "session_" + _sha(session_key)[:16],
            "event_ref": "event_" + _sha(event_id)[:16] if event_id else "",
            "contact": {"phone": normalized_phone}, "context": _safe_value(context) or {},
            "delivery": {"status": "pending"},
        }
        fd, temporary = tempfile.mkstemp(prefix=f".{key[:12]}.", suffix=".tmp", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                os.chmod(temporary, 0o600)
                json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush(); os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
                os.chmod(target, 0o600)
            except FileExistsError:
                return OutboxResult("duplicate", lead_ref)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return OutboxResult("queued", lead_ref)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
