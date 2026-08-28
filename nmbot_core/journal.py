"""Private append-only V6 journal with opaque identifiers only."""

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
_PHONE = re.compile(r"(?:\+?\d[\s().-]*){10,15}")
_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_ROLE = frozenset({"user", "bot", "system"})
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_DIAGNOSTIC_STATUS = frozenset({"completed", "safe_failure", "failed"})
_TRACE_STAGE = frozenset({"prompt1", "mcp", "prompt2", "state", "bot_message"})
_TRACE_STATUS = frozenset({"accepted", "not_called", "failed", "prepared"})


class JournalError(ValueError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", "replace")).hexdigest()


def _text_identity(text: Any) -> dict[str, Any]:
    value = _EMAIL.sub("[contact]", _PHONE.sub("[contact]", str(text or "")[:4000]))
    value = " ".join(value.split())
    return {"text_chars": len(value), "text_sha256": _digest(value)} if value else {"text_chars": 0}


def _code(value: Any, field: str) -> str:
    result = str(value or "").strip().lower()
    if not _CODE.fullmatch(result):
        raise JournalError(f"invalid_{field}")
    return result


def _runtime_diagnostic(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Project the bounded receipt required by strict smoke, never turn data."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) - {"status", "state_commit", "trace", "error_code"}:
        raise JournalError("invalid_runtime_diagnostic")
    status = str(value.get("status") or "").strip().lower()
    if status not in _DIAGNOSTIC_STATUS or not isinstance(value.get("state_commit"), bool):
        raise JournalError("invalid_runtime_diagnostic")
    trace = value.get("trace")
    if not isinstance(trace, Mapping) or set(trace) - {"stages", "url_card"}:
        raise JournalError("invalid_runtime_diagnostic")
    stages = trace.get("stages")
    if not isinstance(stages, list) or len(stages) > 5:
        raise JournalError("invalid_runtime_diagnostic")
    safe_stages: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in stages:
        if not isinstance(item, Mapping) or set(item) != {"stage", "status"}:
            raise JournalError("invalid_runtime_diagnostic")
        stage, stage_status = str(item["stage"]), str(item["status"])
        if stage not in _TRACE_STAGE or stage_status not in _TRACE_STATUS or stage in seen:
            raise JournalError("invalid_runtime_diagnostic")
        seen.add(stage)
        safe_stages.append({"stage": stage, "status": stage_status})
    safe_trace: dict[str, Any] = {"stages": safe_stages}
    url_card = trace.get("url_card")
    if url_card is not None:
        if not isinstance(url_card, Mapping) or set(url_card) != {"status"} or url_card.get("status") not in {"accepted", "fetch_failed"}:
            raise JournalError("invalid_runtime_diagnostic")
        safe_trace["url_card"] = {"status": str(url_card["status"])}
    result: dict[str, Any] = {"status": status, "state_commit": value["state_commit"], "trace": safe_trace}
    if value.get("error_code"):
        result["error_code"] = _code(value["error_code"], "error_code")
    return result


def append_event(session_key: str, role: str, text: Any = "", *, event_id: str = "", refs: Mapping[str, Any] | None = None, answer_kind: str = "", event_type: str = "", source: str = "api", release_id: str = "", runtime_diagnostic: Mapping[str, Any] | None = None, path: Path | str) -> dict[str, Any]:
    """Persist one safe row. Never persist message text, contact data, or payload."""

    if not str(session_key or ""):
        raise JournalError("session_key_required")
    role = str(role or "").strip().lower()
    if role not in _ROLE:
        raise JournalError("invalid_role")
    row: dict[str, Any] = {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "runtime_version": "V6",
        "role": role,
        "session_ref": "session_" + _digest(session_key)[:20],
        **_text_identity(text),
    }
    if event_id:
        row["event_ref"] = "event_" + _digest(event_id)[:20]
    for name, value in (refs or {}).items():
        if name in {"site_id", "chat_id", "client_id"} and value not in (None, ""):
            row[f"{name[:-3]}_ref"] = f"{name[:-3]}_{_digest(value)[:20]}"
    for name, value in (("answer_kind", answer_kind), ("event_type", event_type), ("source", source)):
        if value:
            row[name] = _code(value, name)
    if release_id:
        safe_release = str(release_id).strip()
        if not _RELEASE.fullmatch(safe_release) or safe_release == "UNKNOWN":
            raise JournalError("invalid_release_id")
        row["release_id"] = safe_release
    diagnostic = _runtime_diagnostic(runtime_diagnostic)
    if diagnostic is not None:
        row["runtime_diagnostic"] = diagnostic
    target = Path(path).expanduser()
    if target.exists() and (target.is_dir() or target.is_symlink()):
        raise JournalError("unsafe_journal_path")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    payload = (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, payload)
        os.fsync(fd)
        os.fchmod(fd, 0o600)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    return row
