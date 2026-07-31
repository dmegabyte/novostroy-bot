from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, Mapping

from .contracts import V4_FAIL_CLOSED_OBJECT, V4_MODEL, V4_PAYLOAD_STAGE, V4State
from .response_validator import compact_json, validate_response_text


def fail_closed_object() -> dict[str, Any]:
    return copy.deepcopy(V4_FAIL_CLOSED_OBJECT)


async def run_turn(
    text: str,
    state: V4State | Mapping[str, Any] | None,
    *,
    provider_port: Any,
    channel: str,
    conversation_ref: str,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state_before = state if isinstance(state, V4State) else V4State.from_dict(state if isinstance(state, Mapping) else None)
    user_text = str(text or "").strip()
    base_meta: dict[str, Any] = {
        "runtime": "v4",
        "model": V4_MODEL,
        "payload_stage": V4_PAYLOAD_STAGE,
        "call_count": 0,
        "prompt_provenance": _safe_prompt_provenance(getattr(provider_port, "prompt_provenance", None)),
    }
    if not user_text:
        obj = fail_closed_object()
        return _public(False, obj, state_before, channel=channel, meta={**base_meta, "error_code": "empty_message"})
    if provider_port is None:
        obj = fail_closed_object()
        return _public(False, obj, state_before, channel=channel, meta={**base_meta, "error_code": "missing_v4_provider_port"})
    gateway_trace: dict[str, Any] | None = None
    try:
        raw, provider_meta = await provider_port.complete({
            "message": user_text,
            "channel": str(channel or "")[:40],
            "conversation_ref": str(conversation_ref or "")[:120],
            "state": state_before.to_model_dict(),
        })
        gateway_trace = _v4_gateway_trace(raw=raw, provider_meta=provider_meta, data_obj=None, user_text=user_text, call_attempted=True)
        base_meta["call_count"] = 1 if isinstance(provider_meta, Mapping) and provider_meta.get("_v4_gateway_call_attempted") else 0
        base_meta["prompt_provenance"] = _safe_prompt_provenance(getattr(provider_port, "prompt_provenance", None))
        base_meta["provider"] = _safe_provider_meta(provider_meta)
        if isinstance(provider_meta, Mapping) and (provider_meta.get("_safe_fallback") or provider_meta.get("safe_fallback") or provider_meta.get("_upstream_error") or provider_meta.get("upstream_error")):
            raise ValueError("provider_safe_fallback")
        obj = validate_response_text(raw)
        gateway_trace = _v4_gateway_trace(raw=raw, provider_meta=provider_meta, data_obj=obj, user_text=user_text, call_attempted=True)
    except Exception as exc:
        obj = fail_closed_object()
        attempted = getattr(exc, "call_attempted", None)
        if attempted == 1:
            base_meta["call_count"] = 1
        if gateway_trace is None:
            exc_trace = getattr(exc, "v4_gateway_trace", None)
            gateway_trace = exc_trace if isinstance(exc_trace, dict) else _v4_gateway_trace(raw="", provider_meta={}, data_obj=None, user_text=user_text, call_attempted=base_meta.get("call_count") == 1)
        return _public(False, obj, state_before, channel=channel, meta=_with_v4_trace({**base_meta, "error_code": _safe_error_code(exc)}, gateway_trace, ok=False))
    state_after = replace(state_before, last_valid_ids=tuple(obj["data"][:20]), last_message_summary=str(obj["message"])[:240])
    return _public(True, obj, state_after, channel=channel, meta=_with_v4_trace(base_meta, gateway_trace, ok=True))


def _public(ok: bool, obj: dict[str, Any], state: V4State, *, channel: str, meta: dict[str, Any]) -> dict[str, Any]:
    client_answer = str(obj.get("message") or "").strip()
    return {
        "ok": bool(ok),
        **({"error": "v4_runtime_error", "error_type": str(meta.get("error_code") or "v4_runtime_error")} if not ok else {}),
        "answer": compact_json(obj),
        "client_answer": client_answer,
        "intent": "flat_search_json" if ok else "safe_error",
        "answer_kind": "v4_strict_json",
        "handoff_to_operator": False,
        "buttons": [],
        "state": state,
        "meta": {"channel": channel, **meta},
    }


def _safe_prompt_provenance(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"schema_version": 1, "owner": "nmbot_v4", "coverage": "missing", "prompts": []}
    prompts = value.get("prompts") if isinstance(value.get("prompts"), list) else []
    safe_prompts = []
    for item in prompts[:3]:
        if not isinstance(item, Mapping):
            continue
        safe_prompts.append({
            "id": str(item.get("id") or "")[:80],
            "source": str(item.get("source") or "")[:160],
            "sha256": str(item.get("sha256") or "")[:64],
            "usage": str(item.get("usage") or "")[:40],
        })
    return {"schema_version": 1, "owner": "nmbot_v4", "coverage": str(value.get("coverage") or "")[:40], "prompts": safe_prompts}


def _safe_provider_meta(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    allowed: dict[str, Any] = {}
    for key in ("_payload_stage", "_gateway_task_id", "_gateway_timeout", "_safe_fallback", "_upstream_error", "_provider_error_code", "_v4_gateway_call_attempted"):
        if key in value:
            item = value.get(key)
            allowed[key] = item if isinstance(item, bool) else str(item)[:120]
    return allowed


def _with_v4_trace(meta: dict[str, Any], gateway_trace: dict[str, Any] | None, *, ok: bool) -> dict[str, Any]:
    if not gateway_trace:
        return meta
    call_count = 1 if bool(gateway_trace.get("call_attempted")) else 0
    summary = {
        "stage": "v4_gateway",
        "action": "one_prompt",
        "answer_kind": "v4_strict_json",
        "call_counts": {"gateway_attempts": call_count},
        "gateway_attempt_details": [gateway_trace],
        "model_usage": {"answer": [V4_MODEL]},
        "quality_blockers": [] if ok else ["runtime_error"],
        "grounding_scope": "canonical_response_plan",
    }
    return {**meta, "trace": {"runtime_summary": summary}}


def _v4_gateway_trace(*, raw: Any, provider_meta: Any, data_obj: dict[str, Any] | None, user_text: str, call_attempted: bool) -> dict[str, Any]:
    meta = provider_meta if isinstance(provider_meta, Mapping) else {}
    raw_text = str(raw or "")
    parse = _v4_response_parse(raw_text, data_obj=data_obj)
    trace: dict[str, Any] = {
        "model": V4_MODEL,
        "gateway_status": _v4_gateway_status(meta, raw_text=raw_text, call_attempted=call_attempted),
        "response_chars": min(len(raw_text), 20_000),
        "response_parse": parse,
        "message_chars": _v4_message_chars(data_obj) if data_obj is not None else 0,
        "call_attempted": bool(call_attempted),
        "request_shape": _v4_request_shape(user_text),
    }
    task_id = _safe_gateway_task_id(meta.get("_gateway_task_id"))
    if task_id:
        trace["gateway_task_id"] = task_id
    if data_obj is not None and parse == "valid_json":
        data = data_obj.get("data") if isinstance(data_obj.get("data"), list) else []
        trace["data_count"] = min(len(data), 20)
    return trace


def _v4_response_parse(raw_text: str, *, data_obj: dict[str, Any] | None) -> str:
    if data_obj is not None:
        return "valid_json"
    return "empty" if not raw_text.strip() else "invalid_json"


def _v4_gateway_status(meta: Mapping[str, Any], *, raw_text: str, call_attempted: bool) -> str:
    if not call_attempted:
        return "unknown"
    if bool(meta.get("_gateway_timeout") or meta.get("gateway_timeout")):
        return "timeout"
    if bool(meta.get("_safe_fallback") or meta.get("safe_fallback") or meta.get("_upstream_error") or meta.get("upstream_error")):
        return "error"
    return "completed" if raw_text.strip() else "unknown"


def _v4_message_chars(data_obj: dict[str, Any] | None) -> int:
    if not isinstance(data_obj, dict):
        return 0
    return min(len(str(data_obj.get("message") or "")), 20_000)


def _v4_request_shape(user_text: str) -> dict[str, bool]:
    text = str(user_text or "").lower()
    return {
        "family_query": any(word in text for word in ("сем", "дет", "реб", "школ", "садик", "сад ")),
        "rooms_mentioned": any(word in text for word in ("студи", "однок", "двух", "двуш", "трех", "трёх", "комнат", "1к", "2к", "3к")),
    }


def _safe_gateway_task_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not all(ch.isalnum() or ch in "_.:-" for ch in text):
        return None
    return text[:80]


def _safe_error_code(exc: Exception) -> str:
    text = str(exc or "").strip().lower()
    if text and all(ch.isalnum() or ch == "_" for ch in text) and len(text) <= 80:
        return text
    return exc.__class__.__name__
