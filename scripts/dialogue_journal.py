#!/usr/bin/env python3
"""Private V6-only dialogue journal with no raw dialogue or contact data."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "nmbot.dialogue_journal.v1"
RUNTIME_VERSION = "V6"
SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SAFE_RELEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SAFE_ATTEMPT_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
PHONE_RE = re.compile(r"(?:\+?\d[\s().-]*){10,15}")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
ROLES = frozenset({"user", "bot", "system"})
REF_FIELDS = frozenset({"site_id", "chat_id", "client_id"})
TRACE_STAGES = frozenset({"prompt1", "mcp", "prompt2", "state", "bot_message", "url_card"})
TRACE_STATUSES = frozenset({"not_called", "accepted", "failed", "observed_exact", "unknown", "prepared", "degraded"})


class DialogueJournalError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", "replace")).hexdigest()


def _safe_code(value: Any, *, field: str, required: bool = False) -> str:
    code = str(value or "").strip().lower()
    if not code:
        if required:
            raise DialogueJournalError(f"{field} is required")
        return ""
    if not SAFE_CODE_RE.fullmatch(code):
        raise DialogueJournalError(f"{field} is invalid")
    return code


def _journal_path(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    configured = str(os.getenv("NMBOT_DIALOGUE_JOURNAL") or "").strip()
    if configured:
        return Path(configured).expanduser()
    profile = str(os.getenv("NMBOT_CONTOUR_PROFILE") or "TEST").strip().upper()
    safe_profile = profile.lower() if profile in {"TEST", "PROD"} else "test"
    return Path.home() / ".local" / "state" / "nmbot-v6" / safe_profile / "dialogue" / "dialogue.jsonl"


def _release_id() -> str:
    identity_path = str(os.getenv("NMBOT_RELEASE_IDENTITY_FILE") or "").strip()
    if not identity_path:
        return ""
    try:
        payload = json.loads(Path(identity_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    release_id = str(payload.get("release_id") or "") if isinstance(payload, dict) else ""
    return release_id if SAFE_RELEASE_RE.fullmatch(release_id) and release_id != "UNKNOWN" else ""


def _text_identity(text: Any) -> dict[str, Any]:
    value = str(text or "")[:4000]
    redacted = PHONE_RE.sub("[contact]", value)
    redacted = EMAIL_RE.sub("[contact]", redacted)
    normalized = " ".join(redacted.split())
    if not normalized:
        return {"text_chars": 0}
    return {
        "text_chars": len(normalized),
        "text_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def _safe_error_summary(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DialogueJournalError("error_summary is invalid")
    status = str(value.get("status") or "").strip().lower()
    if status not in {"failed", "degraded"}:
        raise DialogueJournalError("error_summary status is invalid")
    codes = [_safe_code(item, field="error code", required=True) for item in list(value.get("codes") or ())[:8]]
    stages = [_safe_code(item, field="error stage", required=True) for item in list(value.get("stages") or ())[:8]]
    return {
        "status": status,
        "codes": codes,
        "stages": stages,
        "fallback": bool(value.get("fallback")),
    }


def _optional_code(value: Any, *, allowed: frozenset[str] | None = None) -> str:
    try:
        code = _safe_code(value, field="runtime diagnostic")
    except DialogueJournalError:
        return ""
    if allowed is not None and code not in allowed:
        return ""
    return code


def _safe_runtime_diagnostic(value: Any) -> dict[str, Any] | None:
    """Project code-owned trace fields only; arbitrary/raw keys are discarded."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DialogueJournalError("runtime_diagnostic is invalid")

    diagnostic: dict[str, Any] = {}
    for key in (
        "status",
        "failure_stage",
        "error_code",
        "material_status",
        "material_source",
        "tool_observation",
        "outbox_enqueue",
    ):
        code = _optional_code(value.get(key))
        if code:
            diagnostic[key] = code
    if value.get("error_field") not in (None, ""):
        diagnostic["error_field_ref"] = "field_" + _digest(value.get("error_field"))[:20]
    for key in ("awaiting_phone", "state_commit", "handoff_to_operator"):
        if type(value.get(key)) is bool:
            diagnostic[key] = value[key]
    if type(value.get("model_calls")) is int and 0 <= value["model_calls"] <= 3:
        diagnostic["model_calls"] = value["model_calls"]

    trace_value = value.get("v6_trace")
    if isinstance(trace_value, Mapping) and trace_value.get("schema_version") == 1:
        trace: dict[str, Any] = {"schema_version": 1, "stages": []}
        raw_stages = trace_value.get("stages")
        if isinstance(raw_stages, list):
            for item in raw_stages[:8]:
                if not isinstance(item, Mapping):
                    continue
                stage = _optional_code(item.get("stage"), allowed=TRACE_STAGES)
                status = _optional_code(item.get("status"), allowed=TRACE_STATUSES)
                if not stage or not status:
                    continue
                safe_stage: dict[str, Any] = {"stage": stage, "status": status}
                attempt_ref = str(item.get("attempt_ref") or "")
                if SAFE_ATTEMPT_RE.fullmatch(attempt_ref) and not PHONE_RE.search(attempt_ref):
                    safe_stage["attempt_ref"] = attempt_ref
                if type(item.get("call_count")) is int and 0 <= item["call_count"] <= 3:
                    safe_stage["call_count"] = item["call_count"]
                trace["stages"].append(safe_stage)
        url_card = trace_value.get("url_card")
        if isinstance(url_card, Mapping):
            status = _optional_code(url_card.get("status"))
            route = _optional_code(url_card.get("route"))
            if status or route:
                trace["url_card"] = {key: item for key, item in (("status", status), ("route", route)) if item}
        diagnostic["trace"] = trace
    return diagnostic


def append_event(
    session_key: str,
    role: str,
    text: Any = "",
    *,
    event_id: str | None = None,
    meta: Mapping[str, Any] | None = None,
    answer_kind: str | None = None,
    runtime_version: str = RUNTIME_VERSION,
    event_type: str | None = None,
    error_summary: Mapping[str, Any] | None = None,
    runtime_diagnostic: Mapping[str, Any] | None = None,
    source: str | None = None,
    release_id: str | None = None,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Append one bounded opaque event and return the persisted safe row."""
    if str(runtime_version or "").strip().upper() != RUNTIME_VERSION:
        raise DialogueJournalError("runtime_version must be exactly V6")
    normalized_role = str(role or "").strip().lower()
    if normalized_role not in ROLES:
        raise DialogueJournalError("role is invalid")
    if not str(session_key or ""):
        raise DialogueJournalError("session_key is required")

    row: dict[str, Any] = {
        "schema": SCHEMA,
        "created_at": _utc_now(),
        "runtime_version": RUNTIME_VERSION,
        "role": normalized_role,
        "session_ref": "session_" + _digest(session_key)[:20],
        **_text_identity(text),
    }
    if event_id:
        row["event_ref"] = "event_" + _digest(event_id)[:20]
    for key, value in (meta or {}).items():
        if key in REF_FIELDS and value not in (None, ""):
            row[f"{key[:-3]}_ref"] = f"{key[:-3]}_{_digest(value)[:20]}"
        elif key == "trace_ref" and re.fullmatch(r"trace_[0-9a-f]{12,64}", str(value or "")):
            row["trace_ref"] = str(value)
    for key, value in (
        ("answer_kind", answer_kind),
        ("event_type", event_type),
        ("source", source or "api"),
    ):
        safe = _safe_code(value, field=key)
        if safe:
            row[key] = safe
    safe_release = str(release_id or _release_id()).strip()
    if safe_release and SAFE_RELEASE_RE.fullmatch(safe_release) and safe_release != "UNKNOWN":
        row["release_id"] = safe_release
    safe_error = _safe_error_summary(error_summary)
    if safe_error is not None:
        row["error_summary"] = safe_error
    safe_diagnostic = _safe_runtime_diagnostic(runtime_diagnostic)
    if safe_diagnostic is not None:
        row["runtime_diagnostic"] = safe_diagnostic

    target = _journal_path(path)
    if target.exists() and (target.is_dir() or target.is_symlink()):
        raise DialogueJournalError("journal path must be a regular non-symlink file")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
        os.fchmod(fd, 0o600)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    return row
