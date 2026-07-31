#!/usr/bin/env python3
"""Private durable callback outbox for Jivo callback requests.

This module performs no CRM, Google, LLM or network I/O.  It is the local
source of truth for confirmed callback leads and short-lived private contact
drafts while the bot is collecting name+phone.

Privacy contract: raw phone/name may exist only in private JSON records with
0600 permissions. Public return values and filenames contain only opaque refs,
statuses and counters.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo


CallbackStatus = Literal["queued", "duplicate"]
LeaseStatus = Literal["leased", "busy", "missing", "terminal"]
SCHEMA_V2 = "nmbot.callback_sheet_outbox.v2"
DRAFT_SCHEMA = "nmbot.callback_contact_draft.v1"
MSK_TZ = ZoneInfo("Europe/Moscow")
TERMINAL_DELIVERY_STATUSES = {"sheet_delivered", "failed", "append_uncertain"}
SENSITIVE_KEY_RE = re.compile(r"phone|телефон|contact|client_id|chat_id|site_id|sender|token|secret|raw|payload", re.I)
PHONE_LIKE_RE = re.compile(r"(?:\+?\d[\s()\-.]*){10,15}")


@dataclass(frozen=True)
class CallbackOutboxResult:
    status: CallbackStatus
    lead_ref: str

    def public(self) -> dict[str, str]:
        return {"status": self.status, "lead_ref": self.lead_ref}


@dataclass(frozen=True)
class LeaseResult:
    status: LeaseStatus
    record: dict[str, Any] | None = None


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _phone_digits(phone: Any) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_msk(dt: datetime) -> str:
    return dt.astimezone(MSK_TZ).replace(microsecond=0).isoformat()


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text[:-1] + "+00:00")
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _safe_text(value: Any, *, limit: int = 500) -> str:
    text = str(value or "").strip()
    if PHONE_LIKE_RE.search(text):
        return ""
    return text[:limit]


def _safe_nested_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if isinstance(value, str):
        text = value.strip()[:500]
        return "" if PHONE_LIKE_RE.search(text) else text
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return None if len(str(abs(value))) >= 10 else value
    if isinstance(value, float):
        return value
    if isinstance(value, list):
        return [_safe_nested_value(item, depth=depth + 1) for item in value[:8]]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)[:80]
            if SENSITIVE_KEY_RE.search(key_text):
                continue
            cleaned = _safe_nested_value(item, depth=depth + 1)
            if cleaned not in (None, "", [], {}):
                safe[key_text] = cleaned
        return safe
    text = str(value)[:200]
    return "" if PHONE_LIKE_RE.search(text) else text


def _safe_option(option: Any) -> dict[str, Any]:
    if not isinstance(option, dict):
        return {}
    allowed = {
        "name", "location", "district", "price", "price_range", "min_price",
        "area", "rooms", "finishing", "ready", "deadline", "metro", "developer",
        "link", "why_close", "why_family", "why_investment", "why_rental",
    }
    safe: dict[str, Any] = {}
    for key, value in option.items():
        if key not in allowed or value in (None, "", [], {}) or SENSITIVE_KEY_RE.search(str(key)):
            continue
        cleaned = _safe_nested_value(value)
        if cleaned not in (None, "", [], {}):
            safe[str(key)] = cleaned
    return safe


def build_callback_lead_context(state: dict[str, Any], *, channel: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a safe lead context without full phone/name or raw Jivo payload."""
    options = state.get("visible_options") or state.get("last_options") or []
    selected = state.get("selected_option") if isinstance(state.get("selected_option"), dict) else {}
    params = state.get("params") if isinstance(state.get("params"), dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    return {
        "channel": _safe_text(channel, limit=50),
        "params": _safe_nested_value(params),
        "selected_option": _safe_option(selected),
        "current_options": [_safe_option(option) for option in options[:5] if isinstance(option, dict)],
        "last_bot_question": _safe_text(state.get("last_bot_question"), limit=300),
        "last_offer_type": _safe_text(state.get("last_offer_type"), limit=80),
        "last_answer_kind": _safe_text(state.get("last_answer_kind"), limit=80),
        "client_request_context": {
            "site_id_present": bool(meta.get("site_id")),
            "chat_id_present": bool(meta.get("chat_id")),
            "client_id_present": bool(meta.get("client_id")),
            "sender_has_contacts": bool(meta.get("sender_has_contacts")),
        },
    }


class LocalCallbackOutbox:
    """Durable idempotent local outbox for callback requests."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.drafts_root = self.root / "_drafts"
        self.drafts_root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.drafts_root, 0o700)

    @staticmethod
    def deterministic_key(*, session_key: str, event_id: str, normalized_phone: str, contact_name: str = "") -> str:
        event = str(event_id or "").strip()
        if event:
            material = f"event\0{session_key}\0{event}"
        else:
            material = f"phone\0{session_key}\0{_phone_digits(normalized_phone)}\0{str(contact_name or '').strip().casefold()}"
        return _sha256_hex(material)

    @staticmethod
    def draft_key(*, session_key: str) -> str:
        return _sha256_hex(f"draft\0{session_key}")

    def _path_for_key(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def _draft_path(self, session_key: str) -> Path:
        return self.drafts_root / f"{self.draft_key(session_key=session_key)}.json"

    def _atomic_write_json(self, path: Path, record: dict[str, Any], *, create_only: bool = False) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                os.chmod(tmp_name, 0o600)
                json.dump(record, fh, ensure_ascii=False, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            if create_only:
                try:
                    os.link(tmp_name, path)
                    os.chmod(path, 0o600)
                except FileExistsError:
                    return False
            else:
                os.replace(tmp_name, path)
                os.chmod(path, 0o600)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            return True
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

    def _read_json_path(self, path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def enqueue(
        self,
        *,
        session_key: str,
        event_id: str,
        normalized_phone: str,
        context: dict[str, Any],
    ) -> CallbackOutboxResult:
        return self.enqueue_callback(
            session_key=session_key,
            event_id=event_id,
            contact_name="",
            normalized_phone=normalized_phone,
            context=context,
        )

    def enqueue_callback(
        self,
        *,
        session_key: str,
        event_id: str,
        contact_name: str,
        normalized_phone: str,
        context: dict[str, Any],
        summary_input: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> CallbackOutboxResult:
        key = self.deterministic_key(
            session_key=session_key,
            event_id=event_id,
            normalized_phone=normalized_phone,
            contact_name=contact_name,
        )
        lead_ref = f"cb_{key[:16]}"
        final_path = self._path_for_key(key)
        if final_path.exists():
            return CallbackOutboxResult(status="duplicate", lead_ref=lead_ref)

        now = created_at or _utc_now()
        safe_summary_input = _safe_nested_value(summary_input if summary_input is not None else context) or {}
        if isinstance(summary_input, dict) and isinstance(safe_summary_input, dict):
            for canonical_key, empty_value in (
                ("params", {}),
                ("selected_option", {}),
                ("current_options", []),
                ("visible_options", []),
                ("last_bot_question", ""),
            ):
                if canonical_key in summary_input and canonical_key not in safe_summary_input:
                    safe_summary_input[canonical_key] = empty_value
        record = {
            "schema": SCHEMA_V2,
            "created_at": int(now.timestamp()),  # v1 compatibility
            "created_at_utc": _iso_utc(now),
            "created_at_msk": _iso_msk(now),
            "lead_ref": lead_ref,
            "idempotency_key_sha256": key,
            "session_ref": f"session_{_sha256_hex(str(session_key))[:16]}",
            "event_ref": f"event_{_sha256_hex(str(event_id or ''))[:16]}" if event_id else "",
            "phone": normalized_phone,  # v1 compatibility; still private file only
            "phone_digits_len": len(_phone_digits(normalized_phone)),
            "contact": {"name": str(contact_name or "").strip(), "phone": normalized_phone},
            "context": context,
            "summary_input": safe_summary_input,
            "summary": {"status": "pending", "text": "", "attempts": 0},
            "delivery": {"status": "pending_summary"},  # v1-ish compatibility
            "sheet_delivery": {
                "status": "pending_summary",
                "attempts": 0,
                "next_attempt_at": _iso_utc(now),
                "sheet_row_ref": "",
                "lease_owner": "",
                "lease_until": "",
                "last_error_class": "",
            },
        }
        created = self._atomic_write_json(final_path, record, create_only=True)
        return CallbackOutboxResult(status="queued" if created else "duplicate", lead_ref=lead_ref)

    def save_contact_draft(self, *, session_key: str, normalized_phone: str, event_id: str = "") -> None:
        now = _utc_now()
        record = {
            "schema": DRAFT_SCHEMA,
            "created_at_utc": _iso_utc(now),
            "updated_at_utc": _iso_utc(now),
            "session_ref": f"session_{_sha256_hex(str(session_key))[:16]}",
            "event_ref": f"event_{_sha256_hex(str(event_id or ''))[:16]}" if event_id else "",
            "phone": normalized_phone,
            "phone_digits_len": len(_phone_digits(normalized_phone)),
        }
        self._atomic_write_json(self._draft_path(session_key), record)

    def load_contact_draft_phone(self, *, session_key: str) -> str:
        record = self._read_json_path(self._draft_path(session_key)) or {}
        return str(record.get("phone") or "")

    def clear_contact_draft(self, *, session_key: str) -> None:
        try:
            self._draft_path(session_key).unlink()
        except FileNotFoundError:
            return

    def iter_due_records(self, *, now: datetime | None = None, owner: str = "") -> list[dict[str, Any]]:
        current = now or _utc_now()
        records: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            record = self._read_json_path(path)
            if not record or record.get("schema") != SCHEMA_V2:
                continue
            delivery = record.get("sheet_delivery") if isinstance(record.get("sheet_delivery"), dict) else {}
            if str(delivery.get("status") or "") in TERMINAL_DELIVERY_STATUSES:
                continue
            next_at = _parse_time(delivery.get("next_attempt_at")) or datetime.fromtimestamp(0, timezone.utc)
            lease_until = _parse_time(delivery.get("lease_until"))
            if lease_until and lease_until > current and str(delivery.get("lease_owner") or "") != owner:
                continue
            if next_at <= current:
                record["_path"] = str(path)
                records.append(record)
        return records

    def lease_record(self, *, lead_ref: str, owner: str, ttl_seconds: int = 60, now: datetime | None = None) -> LeaseResult:
        current = now or _utc_now()
        for path in sorted(self.root.glob("*.json")):
            record = self._read_json_path(path)
            if not record or record.get("lead_ref") != lead_ref:
                continue
            delivery = record.setdefault("sheet_delivery", {})
            if str(delivery.get("status") or "") in TERMINAL_DELIVERY_STATUSES:
                return LeaseResult(status="terminal", record=record)
            lease_until = _parse_time(delivery.get("lease_until"))
            if lease_until and lease_until > current and delivery.get("lease_owner") not in ("", owner):
                return LeaseResult(status="busy", record=record)
            delivery["lease_owner"] = owner
            delivery["lease_until"] = _iso_utc(current + timedelta(seconds=ttl_seconds))
            self._atomic_write_json(path, record)
            record["_path"] = str(path)
            return LeaseResult(status="leased", record=record)
        return LeaseResult(status="missing")

    def update_record(self, record: dict[str, Any]) -> None:
        path_text = str(record.get("_path") or "")
        path = Path(path_text) if path_text else self._path_for_key(str(record.get("idempotency_key_sha256") or ""))
        clean = {k: v for k, v in record.items() if k != "_path"}
        self._atomic_write_json(path, clean)

    def schedule_retry(self, record: dict[str, Any], *, error_class: str, delay_seconds: int, now: datetime | None = None) -> None:
        current = now or _utc_now()
        delivery = record.setdefault("sheet_delivery", {})
        attempts = int(delivery.get("attempts") or 0) + 1
        delivery.update({
            "status": "retrying",
            "attempts": attempts,
            "next_attempt_at": _iso_utc(current + timedelta(seconds=max(0, delay_seconds))),
            "lease_owner": "",
            "lease_until": "",
            "last_error_class": str(error_class or "error")[:80],
        })
        self.update_record(record)

    def mark_failed(self, record: dict[str, Any], *, error_class: str) -> None:
        delivery = record.setdefault("sheet_delivery", {})
        delivery.update({"status": "failed", "lease_owner": "", "lease_until": "", "last_error_class": str(error_class or "error")[:80]})
        self.update_record(record)

    def mark_append_uncertain(self, record: dict[str, Any], *, row_ref: str) -> None:
        """Stop automatic retries after a possibly successful Sheets append.

        Without a durable delivery receipt, retrying could create a duplicate
        callback row. An operator must reconcile this private outbox record.
        """
        delivery = record.setdefault("sheet_delivery", {})
        delivery.update({
            "status": "append_uncertain",
            "sheet_row_ref": str(row_ref or ""),
            "lease_owner": "",
            "lease_until": "",
            "last_error_class": "delivery_ledger_write_failed",
        })
        record["delivery"] = {"status": "append_uncertain", "sheet_row_ref": str(row_ref or "")}
        self.update_record(record)

    def mark_delivered(self, record: dict[str, Any], *, row_ref: str, delivered_at: datetime | None = None) -> None:
        current = delivered_at or _utc_now()
        delivery = record.setdefault("sheet_delivery", {})
        delivery.update({
            "status": "sheet_delivered",
            "sheet_row_ref": str(row_ref or ""),
            "delivered_at_utc": _iso_utc(current),
            "lease_owner": "",
            "lease_until": "",
            "last_error_class": "",
        })
        record["delivery"] = {"status": "sheet_delivered", "sheet_row_ref": str(row_ref or "")}
        self.update_record(record)
