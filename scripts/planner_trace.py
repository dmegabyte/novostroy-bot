#!/usr/bin/env python3
"""Safe append-only canonical planner trace for Jivo turns."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dialogue_journal import _ref, redact_contact_values
except ImportError:  # pragma: no cover - package-style import fallback
    from .dialogue_journal import _ref, redact_contact_values  # type: ignore


SCHEMA_VERSION = 1
DEFAULT_TRACE_DIR = Path(__file__).resolve().parent.parent / "logs"
TRACE_FIELD_ALLOWLIST = {
    "schema_version",
    "ts",
    "channel",
    "source",
    "session_key_ref",
    "conversation_ref",
    "action",
    "dialog_action",
    "intent",
    "intent_policy",
    "target",
    "search_policy",
    "scope",
    "confidence",
    "canonical_errors",
    "canonical_error_codes",
    "canonical_valid",
    "fallback_used",
    "repair_attempted",
    "repair_applied",
    "final_decision",
    "planner_exception_code",
    "user_text",
    "user_text_truncated",
    "planner_raw_response",
    "planner_raw_response_truncated",
    "raw_response_present",
}
_ENUM_MAX = 80
_ERROR_MAX = 12
_ERROR_LEN_MAX = 120
USER_TEXT_MAX = 4000
PLANNER_RAW_RESPONSE_MAX = 12000


def trace_path(now: datetime | None = None) -> Path:
    configured_file = os.getenv("NMBOT_PLANNER_TRACE_FILE", "").strip()
    if configured_file:
        return Path(configured_file).expanduser()
    base = Path(os.getenv("NMBOT_PLANNER_TRACE_DIR", str(DEFAULT_TRACE_DIR))).expanduser()
    day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
    return base / f"planner_trace-{day}.jsonl"


def _safe_enum(value: Any, *, default: str = "") -> str:
    text = str(value or default).strip()
    if not text:
        return default
    return text[:_ENUM_MAX]


def _safe_bool(value: Any) -> bool:
    return bool(value)


def _safe_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    if confidence < 0:
        return 0.0
    if confidence > 1:
        return 1.0
    return round(confidence, 3)


def _safe_errors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        out.append(text[:_ERROR_LEN_MAX])
        if len(out) >= _ERROR_MAX:
            break
    return sorted(set(out))


def _error_codes(errors: list[str]) -> list[str]:
    return sorted({str(error).split(":", 1)[0][:_ERROR_LEN_MAX] for error in errors if str(error).strip()})[:_ERROR_MAX]


def _bounded_redacted_text(value: Any, limit: int) -> tuple[str, bool]:
    text = redact_contact_values(value)
    truncated = len(text) > limit
    return text[:limit], truncated


def _decision_public(decision: Any) -> dict[str, str]:
    return {
        "action": _safe_enum(getattr(decision, "action", "recover_dialogue"), default="recover_dialogue"),
        "target": _safe_enum(getattr(decision, "target", "none"), default="none"),
        "search_policy": _safe_enum(getattr(decision, "search_policy", "forbidden"), default="forbidden"),
    }


def build_event(*, session_key: str, plan: dict[str, Any] | None, final_decision: Any, source: str = "api", exception_code: str | None = None, user_text: str = "") -> dict[str, Any]:
    plan = plan if isinstance(plan, dict) else {}
    errors = _safe_errors(plan.get("canonical_validation_errors") or plan.get("canonical_errors") or plan.get("source_canonical_errors") or plan.get("repair_source_errors") or [])
    safe_user_text, user_text_truncated = _bounded_redacted_text(user_text, USER_TEXT_MAX)
    raw_response_present = isinstance(plan.get("planner_raw_response"), str) and bool(str(plan.get("planner_raw_response")))
    safe_raw_response, raw_response_truncated = _bounded_redacted_text(plan.get("planner_raw_response") if raw_response_present else "", PLANNER_RAW_RESPONSE_MAX)
    canonical_valid = (bool(plan.get("canonical_valid")) if "canonical_valid" in plan else True) and not bool(errors)
    event = {
        "schema_version": SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "channel": "jivo",
        "source": _safe_enum(source, default="api"),
        "session_key_ref": _ref(session_key),
        "conversation_ref": _ref(session_key),
        "action": _safe_enum(plan.get("action"), default="recover_dialogue"),
        "dialog_action": _safe_enum(plan.get("dialog_action")),
        "intent": _safe_enum(plan.get("intent"), default="unknown"),
        "intent_policy": _safe_enum(plan.get("intent_policy"), default="keep"),
        "target": _safe_enum(plan.get("target"), default="none"),
        "search_policy": _safe_enum(plan.get("search_policy"), default="forbidden"),
        "scope": _safe_enum(plan.get("scope"), default="unknown"),
        "confidence": _safe_confidence(plan.get("confidence")),
        "canonical_errors": errors,
        "canonical_error_codes": _error_codes(errors),
        "canonical_valid": canonical_valid,
        "fallback_used": _safe_bool(plan.get("fallback_used")) or bool(exception_code),
        "repair_attempted": _safe_bool(plan.get("repair_attempted")),
        "repair_applied": _safe_bool(plan.get("repair_applied")),
        "final_decision": _decision_public(final_decision),
        "user_text": safe_user_text,
        "user_text_truncated": user_text_truncated,
        "raw_response_present": raw_response_present,
        "planner_raw_response_truncated": raw_response_truncated,
    }
    if raw_response_present:
        event["planner_raw_response"] = safe_raw_response
    if exception_code:
        event["planner_exception_code"] = _safe_enum(exception_code)
    return {key: value for key, value in event.items() if key in TRACE_FIELD_ALLOWLIST}


def append_event(*, session_key: str, plan: dict[str, Any] | None, final_decision: Any, source: str = "api", exception_code: str | None = None, path: Path | None = None, user_text: str = "") -> dict[str, Any]:
    """Append exactly one sanitized planner event using O_APPEND and 0600 mode."""
    event = build_event(session_key=session_key, plan=plan, final_decision=final_decision, source=source, exception_code=exception_code, user_text=user_text)
    out_path = path or trace_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)
    return event
