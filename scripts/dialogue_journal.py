#!/usr/bin/env python3
"""Canonical append-only journal for production customer dialogues."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from nmbot_v0.field_contract import V0_PRESENTATION_TRACE_FIELDS
    from nmbot_v1.execution_path import sanitize_execution_path as sanitize_v1_execution_path
    from nmbot_v1.prompt_provenance import sanitize_prompt_provenance as sanitize_v1_prompt_provenance
    from nmbot_v2.contracts import OptionCard
    from nmbot_v2.execution_path import sanitize_execution_path
    from nmbot_v2.prompt_provenance import sanitize_prompt_provenance
except ImportError:  # pragma: no cover - standalone diagnostic fallback
    OptionCard = None  # type: ignore[assignment]
    V0_PRESENTATION_TRACE_FIELDS = frozenset()  # type: ignore[assignment]
    sanitize_v1_execution_path = None  # type: ignore[assignment]
    sanitize_v1_prompt_provenance = None  # type: ignore[assignment]
    sanitize_execution_path = None  # type: ignore[assignment]
    sanitize_prompt_provenance = None  # type: ignore[assignment]


DEFAULT_JOURNAL_FILE = Path(__file__).resolve().parent.parent / "logs" / "dialogue_journal.jsonl"
DEFAULT_READABLE_JOURNAL_DIR = Path(__file__).resolve().parent.parent / "logs"
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?7|8)[\s().-]*\d(?:[\s().-]*\d){9,10}(?!\w)")
_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)
_CONTACT_VALUE_RE = re.compile(
    r"(?i)\b(?:telegram|телеграм|tg|whatsapp|ватсап|wa)\s*[:=]?\s*@?[A-Z0-9_.-]{3,32}\b"
)
_SAFE_TRACE_REF_RE = re.compile(r"^trace_[0-9a-f]{12}$")


def journal_path() -> Path:
    return Path(os.getenv("NMBOT_DIALOGUE_JOURNAL", str(DEFAULT_JOURNAL_FILE))).expanduser()


def readable_journal_path(now: datetime | None = None) -> Path:
    """Daily human-facing dialogue log: text only, no runtime metadata."""
    stamp = now or datetime.now(timezone.utc)
    default = DEFAULT_READABLE_JOURNAL_DIR / f"jivo_dialogue-{stamp.strftime('%Y-%m-%d')}.log"
    return Path(os.getenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(default))).expanduser()


def redact_contact_values(value: Any) -> str:
    text = str(value or "").strip()
    text = _PHONE_RE.sub("[phone redacted]", text)
    text = _EMAIL_RE.sub("[email redacted]", text)
    text = _CONTACT_VALUE_RE.sub("[contact redacted]", text)
    return text


def _safe_text(value: Any) -> str:
    return redact_contact_values(value)[:4000]


def _ref(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def append_event(*, session_key: str, role: str, text: str = "", event_type: str = "turn",
                   event_id: str | None = None, meta: dict[str, Any] | None = None,
                    answer_kind: str | None = None, offer_type: str | None = None,
                    response_composer: dict[str, Any] | None = None,
                    prompt_provenance: dict[str, Any] | None = None,
                    execution_path: dict[str, Any] | None = None,
                    response_model: dict[str, Any] | None = None,
                    error_summary: dict[str, Any] | None = None,
                    runtime_summary: dict[str, Any] | None = None,
                    runtime_version: str | None = None,
                    release_id: str | None = None,
                    source: str = "api", journal: Path | None = None) -> dict[str, Any]:
    """Append one redacted event. The line append is O_APPEND-safe across workers."""
    meta = meta or {}
    now = datetime.now(timezone.utc)
    event = {
        "schema_version": 1,
        "ts": now.isoformat().replace("+00:00", "Z"),
        "channel": "jivo",
        "source": str(source)[:80] or "api",
        "event_type": event_type,
        "role": role,
        "session_key_ref": _ref(session_key),
        "conversation_ref": _ref(session_key),
        "event_id_ref": _ref(event_id),
        "site_id_ref": _ref(meta.get("site_id")),
        "chat_id_ref": _ref(meta.get("chat_id")),
        "client_id_ref": _ref(meta.get("client_id")),
        "text": _safe_text(text),
    }
    trace_ref = _safe_trace_ref(meta.get("trace_ref"))
    if trace_ref:
        event["trace_ref"] = trace_ref
    if answer_kind:
        event["answer_kind"] = str(answer_kind)[:120]
    if offer_type:
        event["offer_type"] = str(offer_type)[:120]
    safe_response_composer = _safe_response_composer(response_composer)
    if safe_response_composer:
        event["response_composer"] = safe_response_composer
    safe_prompt_provenance = _safe_prompt_provenance(prompt_provenance)
    if safe_prompt_provenance:
        event["prompt_provenance"] = safe_prompt_provenance
    safe_execution_path = _safe_execution_path(execution_path)
    if safe_execution_path:
        event["execution_path"] = safe_execution_path
    safe_response_model = _safe_response_model(response_model)
    if safe_response_model:
        event["response_model"] = safe_response_model
    safe_error_summary = _safe_error_summary(error_summary)
    if safe_error_summary:
        event["error_summary"] = safe_error_summary
    safe_runtime_summary = _safe_runtime_summary(runtime_summary)
    if safe_runtime_summary:
        event["runtime_summary"] = safe_runtime_summary
    safe_runtime_version = _safe_runtime_version(runtime_version)
    if safe_runtime_version:
        event["runtime_version"] = safe_runtime_version
    safe_release_id = _safe_release_id(release_id)
    if safe_release_id:
        event["release_id"] = safe_release_id
    path = journal or journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)
    _append_readable_turn(event, now=now)
    return event


def _safe_prompt_provenance(value: Any) -> dict[str, Any] | None:
    if sanitize_v1_prompt_provenance is not None:
        safe_v1 = sanitize_v1_prompt_provenance(value)
        if safe_v1:
            return safe_v1
    if sanitize_prompt_provenance is None:
        return None
    return sanitize_prompt_provenance(value)


def _safe_execution_path(value: Any) -> dict[str, Any] | None:
    if sanitize_v1_execution_path is not None:
        safe_v1 = sanitize_v1_execution_path(value)
        if safe_v1:
            return safe_v1
    if sanitize_execution_path is None:
        return None
    return sanitize_execution_path(value)


def _safe_runtime_version(value: Any) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in {"V0", "V1", "V2", "V3", "V5"} else None


def _safe_response_model(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    mode = str(value.get("mode") or "").strip().lower()
    status = str(value.get("status") or "").strip().lower()
    if mode not in {"shadow", "publish"} or status not in {"valid", "fallback"}:
        return None
    out: dict[str, Any] = {"mode": mode, "status": status, "published": bool(value.get("published"))}
    model = str(value.get("model") or "").strip()
    if model == "openai/gpt-5.5":
        out["model"] = model
    reason = _safe_response_model_reason(value.get("reason"))
    if reason:
        out["reason"] = reason
    if status == "fallback" and not reason:
        return None
    return out


def _safe_response_model_reason(value: Any) -> str | None:
    text = str(value or "").strip()
    if text in {"invalid_json", "wrong_keys", "provider_or_validation_failed"}:
        return text
    prefix = "one_model_validation_failed:"
    if text.startswith(prefix):
        code = text[len(prefix):].split(":", 1)[0].strip()
        if code and re.fullmatch(r"[a-z0-9_]{1,80}", code):
            return prefix + code
    return None


def _safe_trace_ref(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if _SAFE_TRACE_REF_RE.fullmatch(text) else None


def _safe_release_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if text == "UNKNOWN":
        return text
    if text in {"", ".", ".."} or text.startswith("-") or "/" in text or "\\" in text:
        return None
    return text if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", text) else None


_SAFE_RESPONSE_COMPOSER_FALLBACK_REASONS = {
    "composer_error",
    "validation_failed",
    "composer_missing",
    "not_customer_composer_stage",
    "deterministic_renderer",
    "other",
}

_SAFE_RESPONSE_COMPOSER_VALIDATION_CODES = {
    "empty_response", "invalid_json", "json_root_must_be_object", "schema_required_field_missing",
    "schema_additional_properties", "schema_invalid_options",
    "too_many_cards", "option_name_not_allowed", "option_order_mismatch", "empty_option_section",
    "required_location_missing", "required_price_missing", "scenario_fact_benefit_missing",
    "scenario_viewpoint_mismatch", "intro_empty", "missing_note_required",
    "financing_missing_note_required", "final_question_empty", "recipe_cta_mismatch",
    "contact_before_financing_consent", "selected_financing_card_scope_invalid",
    "section_question_mark", "question_count_not_one", "final_question_not_at_end",
    "final_question_contract_mismatch", "missing_context_acknowledgement", "duplicate_answer",
    "repeated_identical_benefit", "unknown_option_name", "unknown_number_or_sensitive_claim",
    "internal_or_raw_wire_leak", "unsupported_sensitive_claim", "unsupported_marketing_claim",
}
_SAFE_RESPONSE_COMPOSER_VALIDATION_STAGES = {"provider", "transport", "schema", "semantic"}
_SAFE_SEMANTIC_DIAGNOSTIC_STAGES = {"writer", "formatter"}
_SAFE_SEMANTIC_DIAGNOSTIC_CATEGORIES = {"numeric_not_in_canonical", "sensitive_claim"}
_SAFE_ERROR_SUMMARY_STAGES = {"runtime", "composer", "search_validation", "jivo_handler", "bridge_upstream", "bridge_delivery"}
_SAFE_ERROR_SUMMARY_CODES = {
    "runtime_failure",
    "jivo_handler_exception",
    "search_validation_error",
    "composer_error",
    "composer_validation_failed",
    * _SAFE_RESPONSE_COMPOSER_VALIDATION_CODES,
    "runtime_error",
    "question_count_not_one",
    "final_question_not_at_end",
    "search_without_cards",
    "enrichment_error",
    "bridge_hard_timeout",
    "bridge_upstream_exception",
    "bridge_status_delivery_error",
    "bridge_delivery_error",
    "bridge_async_exception",
}


def _safe_error_summary(value: Any) -> dict[str, Any] | None:
    """Keep terminal turn diagnostics enumerable and free of raw exception text."""
    if not isinstance(value, dict):
        return None
    status = str(value.get("status") or "").strip().lower()
    if status not in {"ok", "degraded", "failed"}:
        return None
    codes = [
        str(code)
        for code in (value.get("codes") if isinstance(value.get("codes"), list) else [])
        if str(code) in _SAFE_ERROR_SUMMARY_CODES
    ]
    stages = [
        str(stage)
        for stage in (value.get("stages") if isinstance(value.get("stages"), list) else [])
        if str(stage) in _SAFE_ERROR_SUMMARY_STAGES
    ]
    if status == "ok" and (codes or stages or bool(value.get("fallback"))):
        return None
    if status in {"degraded", "failed"} and not codes:
        return None
    return {
        "status": status,
        "codes": list(dict.fromkeys(codes))[:8],
        "stages": list(dict.fromkeys(stages))[:4],
        "fallback": bool(value.get("fallback")),
    }


def _safe_response_composer_attempt_diagnostic(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    raw_type = str(value.get("raw_type") or "").strip().lower()
    if raw_type in {"empty", "string", "mapping", "other"}:
        out["raw_type"] = raw_type
    raw_length = value.get("raw_length")
    if isinstance(raw_length, (int, float)):
        out["raw_length"] = max(0, min(int(raw_length), 200_000))
    for key in ("starts_object", "starts_fence", "ends_object"):
        if isinstance(value.get(key), bool):
            out[key] = bool(value.get(key))
    return out or None


def _safe_semantic_diagnostics(value: Any, *, reason: str | None, validation_stage: str | None) -> list[dict[str, Any]]:
    if reason != "validation_failed" or validation_stage != "semantic" or not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:2]:
        if not isinstance(item, dict):
            continue
        stage = str(item.get("stage") or "")
        if stage not in _SAFE_SEMANTIC_DIAGNOSTIC_STAGES:
            continue
        categories = [
            str(category)
            for category in (item.get("categories") if isinstance(item.get("categories"), list) else [])
            if str(category) in _SAFE_SEMANTIC_DIAGNOSTIC_CATEGORIES
        ]
        if categories:
            out.append({"stage": stage, "categories": list(dict.fromkeys(categories))[:2]})
    return out


def _bounded_token(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", text)[:80].strip("_") or None

_SAFE_RUNTIME_QUALITY_BLOCKERS = {
    "runtime_error",
    "question_count_not_one",
    "final_question_not_at_end",
    "search_without_cards",
    "enrichment_error",
}
_SAFE_RUNTIME_GROUNDING_SCOPES = {"canonical_response_plan"}


def _safe_response_composer(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict) or "composer_used" not in value:
        return {}
    composer_used = bool(value.get("composer_used"))
    reason = None
    if not composer_used:
        raw_reason = str(value.get("fallback_reason") or "").strip()
        reason = raw_reason if raw_reason in _SAFE_RESPONSE_COMPOSER_FALLBACK_REASONS else "other"
    validation_codes = [
        str(code)
        for code in (value.get("validation_codes") if isinstance(value.get("validation_codes"), list) else [])
        if str(code) in _SAFE_RESPONSE_COMPOSER_VALIDATION_CODES
    ]
    validation_stage = None
    if reason == "validation_failed":
        raw_stage = str(value.get("validation_stage") or "").strip()
        if raw_stage in _SAFE_RESPONSE_COMPOSER_VALIDATION_STAGES:
            validation_stage = raw_stage
    attempts = value.get("attempts")
    out = {
        "composer_used": composer_used,
        "fallback_reason": reason,
        "validation_stage": validation_stage,
        "validation_codes": list(dict.fromkeys(validation_codes))[:3],
        "attempts": int(attempts) if isinstance(attempts, int) and 1 <= attempts <= 2 else None,
    }
    attempt_diagnostic = _safe_response_composer_attempt_diagnostic(value.get("attempt_diagnostic"))
    if attempt_diagnostic:
        out["attempt_diagnostic"] = attempt_diagnostic
    semantic_diagnostics = _safe_semantic_diagnostics(
        value.get("semantic_diagnostics"), reason=reason, validation_stage=validation_stage
    )
    if semantic_diagnostics:
        out["semantic_diagnostics"] = semantic_diagnostics
    return out


def _safe_runtime_summary(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    stage = _bounded_token(value.get("stage"))
    action = _bounded_token(value.get("action"))
    if not stage or not action:
        return {}
    blockers = [
        str(item)
        for item in (value.get("quality_blockers") if isinstance(value.get("quality_blockers"), list) else [])
        if str(item) in _SAFE_RUNTIME_QUALITY_BLOCKERS
    ]
    summary = {
        "stage": stage,
        "action": action,
        "answer_kind": _bounded_token(value.get("answer_kind")),
        "timing_ms": _safe_timing_ms(value.get("timing_ms")),
        "call_counts": _safe_call_counts(value.get("call_counts")),
        "state_before": _safe_runtime_state_summary(value.get("state_before")),
        "state_after": _safe_runtime_state_summary(value.get("state_after")),
        "question_count": _bounded_int(value.get("question_count"), 0, 20),
        "final_question_at_end": bool(value.get("final_question_at_end")),
        "quality_blockers": list(dict.fromkeys(blockers))[:5],
        "grounding_scope": str(value.get("grounding_scope")) if str(value.get("grounding_scope")) in _SAFE_RUNTIME_GROUNDING_SCOPES else "canonical_response_plan",
    }
    field_trace = _safe_field_trace(value.get("field_trace"))
    if field_trace:
        summary["field_trace"] = field_trace
    gateway_attempt_details = _safe_gateway_attempt_details(value.get("gateway_attempt_details"))
    if gateway_attempt_details:
        summary["gateway_attempt_details"] = gateway_attempt_details
    option_enrichment = _safe_option_enrichment(value.get("option_enrichment"))
    if option_enrichment:
        summary["option_enrichment"] = option_enrichment
    model_usage = _safe_model_usage(value.get("model_usage"))
    if model_usage:
        summary["model_usage"] = model_usage
    intent_transition = _safe_intent_transition(value.get("intent_transition"))
    if intent_transition:
        summary["intent_transition"] = intent_transition
    shadow = value.get("card_reformatter_shadow")
    if isinstance(shadow, dict) and str(shadow.get("mode") or "") == "shadow":
        safe_cards = []
        for item in shadow.get("cards") if isinstance(shadow.get("cards"), list) else []:
            if isinstance(item, dict):
                safe_cards.append({
                    "idx": item.get("idx"),
                    "name": str(item.get("name") or "")[:180],
                    "mandatory_text": str(item.get("mandatory_text") or "")[:500],
                    "card_mode": str(item.get("card_mode") or "")[:20],
                    "anchor_fact": str(item.get("anchor_fact") or "")[:80],
                })
        if safe_cards:
            summary["card_reformatter_shadow"] = {"mode": "shadow", "card_count": min(len(safe_cards), 3), "cards": safe_cards[:3]}
    return summary


_SAFE_INTENT_GOALS = {
    "new_search",
    "refine_search",
    "expand_search",
    "lookup_object",
    "answer_current",
    "compare_current",
    "recommend_current",
    "answer_selected",
    "answer_open_question",
    "operator",
    "clarify",
    "resume_pending",
    "off_topic",
}
_SAFE_INTENT_VALIDATION_ERROR_CODES = {
    "invalid_shape",
    "unknown_field",
    "invalid_schema_version",
    "invalid_goal",
    "missing_viewpoint",
    "invalid_constraints_delta",
    "invalid_operator_consent",
    "invalid_explicit_operator_request",
    "invalid_confidence",
    "invalid_schema",
    "invalid_requested_fact",
    "invalid_viewpoint",
    "selected_option_not_visible",
    "invalid_selected_option_scope",
    "missing_named_reference",
    "invalid_named_reference_scope",
    "missing_clarification",
    "clarification_on_non_clarify",
    "invalid_operator_consent_scope",
}
_SAFE_TRANSITION_ERROR_CODES = {
    "selected_option_not_in_visible_list",
    "missing_named_reference",
    "malformed_operation",
}


def _safe_intent_transition(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    goal = str(value.get("goal") or "").strip()
    validation = str(value.get("intent_validation") or "").strip()
    transition = value.get("transition") if isinstance(value.get("transition"), dict) else {}
    error_code = str(transition.get("error_code") or "").strip()
    return {
        "goal": goal if goal in _SAFE_INTENT_GOALS else None,
        "intent_validation": validation if validation in {"accepted", "failed"} else "failed",
        "validation_error_codes": [
            str(code)
            for code in (value.get("validation_error_codes") if isinstance(value.get("validation_error_codes"), list) else [])
            if str(code) in _SAFE_INTENT_VALIDATION_ERROR_CODES
        ][:8],
        "transition": {
            "accepted": bool(transition.get("accepted")),
            "error_code": error_code if error_code in _SAFE_TRANSITION_ERROR_CODES else None,
        },
        "fallback_used": bool(value.get("fallback_used")),
    }


def _safe_option_enrichment(value: Any) -> dict[str, Any]:
    enrichment = value if isinstance(value, dict) else {}
    evidence = _safe_availability_evidence(enrichment.get("availability_evidence"))
    return {"availability_evidence": evidence} if evidence else {}


def _safe_availability_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    confirmation = str(value.get("confirmation") or "").strip().lower()
    source = str(value.get("source") or "").strip().lower()
    out = {
        "requested": bool(value.get("requested")),
        "confirmation": confirmation if confirmation in {"not_requested", "confirmed", "not_confirmed"} else "not_confirmed",
        "source": source if source in {"gateway", "cache", "base", "unknown"} else "unknown",
    }
    task_id = _bounded_token(value.get("gateway_task_id"))
    if task_id:
        out["gateway_task_id"] = task_id
    return out


def _safe_gateway_attempt_details(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        attempt: dict[str, Any] = {}
        stage = _bounded_token(item.get("stage"))
        if stage == "gateway_attempt":
            attempt["stage"] = stage
        model = _bounded_token(item.get("model"))
        if model:
            attempt["model"] = model
        role = str(item.get("model_role") or "").strip().lower()
        if role in {"search", "answer"}:
            attempt["model_role"] = role
        if isinstance(item.get("ok"), bool):
            attempt["ok"] = bool(item.get("ok"))
        if isinstance(item.get("empty"), bool):
            attempt["empty"] = bool(item.get("empty"))
        if isinstance(item.get("safe"), bool):
            attempt["safe"] = bool(item.get("safe"))
        task_id = _bounded_token(item.get("gateway_task_id"))
        if task_id:
            attempt["gateway_task_id"] = task_id
        duration_ms = _bounded_int(item.get("duration_ms"), 0, 10 * 60 * 1000)
        attempt["duration_ms"] = duration_ms
        parse_status = str(item.get("parse_status") or "").strip()
        if parse_status in {"ok", "invalid_json", "missing"}:
            attempt["parse_status"] = parse_status
        gateway_status = str(item.get("gateway_status") or "").strip()
        if gateway_status in {"completed", "timeout", "error", "unknown"}:
            attempt["gateway_status"] = gateway_status
        response_parse = str(item.get("response_parse") or "").strip()
        if response_parse in {"valid_json", "invalid_json", "empty"}:
            attempt["response_parse"] = response_parse
        for key in ("response_chars", "message_chars"):
            if key in item:
                attempt[key] = _bounded_int(item.get(key), 0, 20_000)
        if "data_count" in item:
            attempt["data_count"] = _bounded_int(item.get("data_count"), 0, 20)
        if isinstance(item.get("call_attempted"), bool):
            attempt["call_attempted"] = bool(item.get("call_attempted"))
        request_shape = item.get("request_shape") if isinstance(item.get("request_shape"), dict) else {}
        safe_shape = {key: bool(request_shape.get(key)) for key in ("family_query", "rooms_mentioned") if isinstance(request_shape.get(key), bool)}
        if safe_shape:
            attempt["request_shape"] = safe_shape
        if attempt:
            out.append(attempt)
    return out


def _safe_model_usage(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for role in ("answer", "search"):
        raw = value.get(role)
        if isinstance(raw, str):
            raw_items = [raw]
        elif isinstance(raw, list):
            raw_items = raw
        else:
            raw_items = []
        models = [model for model in (_bounded_token(item) for item in raw_items) if model]
        if models:
            out[role] = list(dict.fromkeys(models))[:3]
    return out


def _safe_field_trace(value: Any) -> dict[str, Any]:
    trace = value if isinstance(value, dict) else {}
    cards = trace.get("cards") if isinstance(trace.get("cards"), list) else []
    allowed = set(V0_PRESENTATION_TRACE_FIELDS)
    if OptionCard is not None:
        allowed.update(OptionCard.__dataclass_fields__)

    def safe_fields(raw: Any) -> list[str]:
        source = raw if isinstance(raw, list) else []
        out: list[str] = []
        for item in source:
            text = str(item or "").strip()
            if text in allowed and not any(part in text.lower() for part in ("phone", "email", "client", "chat", "token", "secret", "payload")):
                out.append(text[:80])
        return list(dict.fromkeys(out))[:20]

    safe_cards = []
    for item in cards[:3]:
        if isinstance(item, dict):
            safe_cards.append({"raw_fields": safe_fields(item.get("raw_fields")), "normalized_fields": safe_fields(item.get("normalized_fields"))})
    return {"cards": safe_cards} if safe_cards else {}


def _safe_timing_ms(value: Any) -> dict[str, int]:
    timing = value if isinstance(value, dict) else {}
    return {key: _bounded_int(timing.get(key), 0, 10 * 60 * 1000) for key in ("planner", "execution", "response", "total")}


def _safe_call_counts(value: Any) -> dict[str, int]:
    counts = value if isinstance(value, dict) else {}
    return {
        "planner": _bounded_int(counts.get("planner"), 0, 3),
        "search": _bounded_int(counts.get("search"), 0, 1),
        "selected_enrichment": _bounded_int(counts.get("selected_enrichment"), 0, 1),
        "gateway_attempts": _bounded_int(counts.get("gateway_attempts"), 0, 5),
        "scenario_search": _bounded_int(counts.get("scenario_search"), 0, 3),
        "answer": _bounded_int(counts.get("answer"), 0, 3),
    }


def _safe_runtime_state_summary(value: Any) -> dict[str, Any]:
    state = value if isinstance(value, dict) else {}
    raw_keys = state.get("param_keys") if isinstance(state.get("param_keys"), list) else []
    return {
        "param_keys": sorted(dict.fromkeys(key for key in (_safe_param_key(item) for item in raw_keys) if key))[:20],
        "visible_options_count": _bounded_int(state.get("visible_options_count"), 0, 20),
        "selected_present": bool(state.get("selected_present")),
        "pending_followup": _bounded_token(state.get("pending_followup")),
        "active_topic": _bounded_token(state.get("active_topic")),
    }


def _bounded_token(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return re.sub(r"[^a-zA-Z0-9_.:-]", "_", text)[:80]


def _safe_param_key(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if any(part in text for part in ("phone", "тел", "email", "client", "chat", "token", "secret", "+7", "7999")):
        return None
    return _bounded_token(text)


def _bounded_int(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(number, high))


def _append_readable_turn(event: dict[str, Any], *, now: datetime) -> None:
    """Append only customer-visible text; canonical JSONL remains the audit log."""
    if event.get("role") not in {"user", "bot"} or not str(event.get("text") or "").strip():
        return
    speaker = "Клиент" if event["role"] == "user" else _readable_bot_speaker(event.get("runtime_version"))
    text = str(event["text"]).replace("\r\n", "\n").replace("\r", "\n").strip()
    time_text = now.strftime("%H:%M:%S UTC")
    suffix = "\n\n" if event["role"] == "bot" else "\n"
    path = readable_journal_path(now)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time_text}] {speaker}: {text}{suffix}".encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


def _readable_bot_speaker(runtime_version: Any) -> str:
    """Keep historical labels stable while attributing the independent V1 bot."""
    return "Татьяна" if _safe_runtime_version(runtime_version) == "V1" else "Ирина"
