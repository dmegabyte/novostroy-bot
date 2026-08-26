"""Mechanical application adapter for the isolated V6-simple runtime."""

from __future__ import annotations

import copy
import re
from dataclasses import replace
from typing import Any, Mapping

from nmbot_v6.simple_runtime import SimpleRuntime, TECHNICAL_TEXT
from nmbot_v6.simple_state import SimpleState

PHONE_SUCCESS = "Спасибо, специалист свяжется с вами."
PHONE_SAVE_FAILURE = "Не удалось сохранить номер. Пожалуйста, отправьте его ещё раз."
OPERATOR_UNAVAILABLE_CALLBACK = "Сейчас оператор не в сети. Оставьте, пожалуйста, полный номер в формате +7 999 123-45-67, чтобы оформить заявку на обратный звонок."
_SAFE_ATTEMPT_REF = re.compile(r"[A-Za-z0-9._:-]{1,200}")
_PHONEISH_REF = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,18}(?!\d)")


def simple_envelope(existing: Mapping[str, Any] | None, state: SimpleState) -> dict[str, Any]:
    envelope = {key: copy.deepcopy(value) for key, value in (existing.items() if isinstance(existing, Mapping) else ())}
    envelope["nmbot_v6"] = state.plain()
    return envelope


def safe_outbox_context(state: SimpleState, channel: str) -> dict[str, Any]:
    excerpt = [dict(item) for item in state.history[-6:]]
    return {"runtime": "v6_simple", "channel": str(channel or "")[:40], "dialogue_excerpt": excerpt}


async def establish_v6_unavailable_fallback(
    app: Any,
    *,
    user_id: str,
    source_message: str | None = None,
) -> dict[str, Any]:
    """Persist the V6 phone fallback used by offline and unavailable operators."""
    get = app.get if hasattr(app, "get") else lambda key, default=None: default
    store = get("state_store")
    if store is None:
        return _public(False, TECHNICAL_TEXT, "configuration_failure", v6_trace=_trace("not_called", "not_called", "not_called", "not_called"))
    try:
        envelope_value = await store.get(user_id)
        envelope = dict(envelope_value) if isinstance(envelope_value, Mapping) else {}
        raw = envelope.get("nmbot_v6")
        state = SimpleState.from_mapping(raw) if isinstance(raw, Mapping) else SimpleState()
        # Phone-shaped text is intentionally forbidden in public V6 history.
        # Persist the typed transition without copying the format example into it.
        next_state = replace(
            state,
            revision=state.revision + 1,
            awaiting_phone=True,
            client_turn_count=state.client_turn_count + (1 if isinstance(source_message, str) and source_message.strip() else 0),
            pending_offer="none",
        )
        await store.save(user_id, simple_envelope(envelope, next_state))
    except Exception:
        return _public(False, TECHNICAL_TEXT, "state_save_failure", v6_trace=_trace("not_called", "not_called", "not_called", "failed"))
    return _public(
        True,
        OPERATOR_UNAVAILABLE_CALLBACK,
        "operator_unavailable_callback",
        awaiting_phone=True,
        state_commit=True,
        v6_trace=_trace("not_called", "not_called", "not_called", "accepted"),
    )


async def run_v6_simple_turn(app: Any, *, user_id: str, message: str, channel: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    get = app.get if hasattr(app, "get") else lambda key, default=None: default
    store = get("state_store")
    prompt1, prompt2 = get("v6_simple_prompt1_port"), get("v6_simple_prompt2_port")
    if store is None or prompt1 is None or prompt2 is None:
        return _public(False, TECHNICAL_TEXT, "configuration_failure", v6_trace=_trace("not_called", "not_called", "not_called", "not_called"))
    try:
        envelope_value = await store.get(user_id)
        envelope = dict(envelope_value) if isinstance(envelope_value, Mapping) else {}
        raw = envelope.get("nmbot_v6")
        state = SimpleState.from_mapping(raw) if isinstance(raw, Mapping) else SimpleState()
    except Exception:
        return _public(False, TECHNICAL_TEXT, "state_load_failure", v6_trace=_trace("not_called", "not_called", "not_called", "failed"))
    url_card_fetcher = get("v6_url_card_fetcher")
    url_card_extractor = get("v6_url_card_extractor")
    result = await SimpleRuntime(
        prompt1,
        prompt2,
        phone_backend=get("v6_phone_backend"),
        url_card_fetcher=url_card_fetcher if callable(url_card_fetcher) else None,
        url_card_extractor=url_card_extractor if callable(url_card_extractor) else None,
    ).run(str(message or ""), state)
    if result.status == "operator_handoff":
        if (meta or {}).get("agents_online") is False:
            return await establish_v6_unavailable_fallback(
                app, user_id=user_id, source_message=str(message or ""),
            )
        return _public(
            True, "", "operator_handoff", handoff_to_operator=True,
            v6_trace=_trace("not_called", "not_called", "not_called", "not_called"),
        )
    if result.status == "phone":
        outbox = get("v6_callback_outbox")
        if outbox is None or result.private_phone is None:
            return _public(False, PHONE_SAVE_FAILURE, "outbox_missing", v6_trace=_trace("not_called", "not_called", "not_called", "not_called"))
        event_id = str((meta or {}).get("event_id") or "")[:200]
        try:
            queued = outbox.enqueue(
                session_key=user_id,
                event_id=event_id,
                normalized_phone=result.private_phone.reveal_for_private_storage(),
                context=safe_outbox_context(state, channel),
            )
            status = str(getattr(queued, "status", queued if isinstance(queued, str) else ""))
        except Exception:
            return _public(False, PHONE_SAVE_FAILURE, "outbox_failure", v6_trace=_trace("not_called", "not_called", "not_called", "not_called"))
        if status not in {"queued", "duplicate"}:
            return _public(False, PHONE_SAVE_FAILURE, "outbox_not_accepted", v6_trace=_trace("not_called", "not_called", "not_called", "not_called"))
        next_state = state.phone_accepted()
        try:
            await store.save(user_id, simple_envelope(envelope, next_state))
        except Exception:
            return _public(False, PHONE_SAVE_FAILURE, "state_save_failure", v6_trace=_trace("not_called", "not_called", "not_called", "failed"))
        return _public(True, PHONE_SUCCESS, "phone_accepted", awaiting_phone=False, state_commit=True, outbox_enqueue=status, v6_trace=_trace("not_called", "not_called", "not_called", "accepted"))
    if result.status in {"multiple_phones", "invalid_phone"}:
        return _public(True, result.text, result.status, awaiting_phone=state.awaiting_phone, v6_trace=_trace("not_called", "not_called", "not_called", "not_called"))
    if result.status == "safe_failure":
        try:
            await store.save(user_id, simple_envelope(envelope, result.state))
        except Exception:
            return _public(False, TECHNICAL_TEXT, "state_save_failure", awaiting_phone=state.awaiting_phone, v6_trace=_runtime_trace(result, state_status="failed"))
        return _public(True, result.text, "safe_failure", awaiting_phone=False, state_commit=True, failure_stage=result.failure_stage, error_code=result.error_code, error_field=result.error_field, v6_trace=_runtime_trace(result, state_status="accepted"))
    if result.status != "completed":
        return _public(False, result.text, "technical_failure", awaiting_phone=state.awaiting_phone, failure_stage=result.failure_stage, v6_trace=_runtime_trace(result, state_status="not_called"))
    if result.failure_stage == "prompt1":
        return _public(True, result.text, "completed_after_prompt1_failure", awaiting_phone=result.request_phone, failure_stage=result.failure_stage, error_code=result.error_code, error_field=result.error_field, model_calls=result.model_calls, v6_trace=_runtime_trace(result, state_status="not_called"))
    try:
        await store.save(user_id, simple_envelope(envelope, result.state))
    except Exception:
        return _public(False, TECHNICAL_TEXT, "state_save_failure", awaiting_phone=state.awaiting_phone, v6_trace=_runtime_trace(result, state_status="failed"))
    return _public(True, result.text, "completed", awaiting_phone=result.state.awaiting_phone, state_commit=True, model_calls=result.model_calls, v6_trace=_runtime_trace(result, state_status="accepted"))


def _stage(stage: str, status: str, *, attempt_ref: str | None = None, call_count: int | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"stage": stage, "status": status}
    if isinstance(attempt_ref, str) and _SAFE_ATTEMPT_REF.fullmatch(attempt_ref) and not _PHONEISH_REF.search(attempt_ref):
        value["attempt_ref"] = attempt_ref
    if type(call_count) is int and 0 <= call_count <= 3:
        value["call_count"] = call_count
    return value


def _trace(prompt1: str, mcp: str, prompt2: str, state: str, *, p1_ref: str | None = None, p2_ref: str | None = None, mcp_calls: int | None = None) -> dict[str, Any]:
    return {"schema_version": 1, "stages": [
        _stage("prompt1", prompt1, attempt_ref=p1_ref),
        _stage("mcp", mcp, attempt_ref=p1_ref if mcp == "observed_exact" else None, call_count=mcp_calls if mcp == "observed_exact" else None),
        _stage("prompt2", prompt2, attempt_ref=p2_ref),
        _stage("state", state),
        _stage("bot_message", "prepared"),
    ]}


def _runtime_trace(result: Any, *, state_status: str) -> dict[str, Any]:
    failed = result.failure_stage
    url_card_status = getattr(result, "url_card_status", None)
    if url_card_status:
        prompt2_status = (
            "failed" if failed == "prompt2"
            else "not_called" if failed == "url_card"
            else "accepted"
        )
        trace = _trace(
            "not_called",
            "not_called",
            prompt2_status,
            "failed" if failed == "state" else state_status,
            p2_ref=result.prompt2_attempt_ref,
        )
        trace["url_card"] = {"status": url_card_status, "route": "prompt2_direct"}
        if result.failure_stage:
            trace["failure_stage"] = result.failure_stage
        if result.error_code:
            trace["error_code"] = result.error_code
        if result.error_field:
            trace["error_field"] = result.error_field
        if result.material_status:
            trace["material_status"] = result.material_status
            trace["material_source"] = result.material_source
            trace["tool_observation"] = result.tool_observation
        return trace
    if result.model_calls == 0 and (result.status == "operator_handoff" or result.request_phone):
        trace = _trace(
            "not_called", "not_called", "not_called",
            "failed" if result.failure_stage == "state" else state_status,
        )
        if result.failure_stage:
            trace["failure_stage"] = result.failure_stage
        return trace
    before_prompt1 = failed in {"input", "phone"}
    p1_status = "not_called" if before_prompt1 else ("failed" if result.prompt1_failed else "accepted")
    recovered_p1 = failed == "prompt1" and result.status == "completed"
    p1_status = "failed" if recovered_p1 else p1_status
    mcp_status = "not_called" if before_prompt1 or failed == "prompt1" else ("observed_exact" if result.tool_observation == "observed_exact" else "unknown")
    p1_phone_short_path = result.request_phone is True and result.model_calls == 1 and result.prompt2_attempt_ref is None
    p2_status = "accepted" if recovered_p1 else ("not_called" if before_prompt1 or failed == "prompt1" or p1_phone_short_path else ("failed" if failed == "prompt2" else "accepted"))
    trace = _trace(p1_status, mcp_status, p2_status, "failed" if failed == "state" else state_status, p1_ref=result.prompt1_attempt_ref, p2_ref=result.prompt2_attempt_ref, mcp_calls=result.mcp_call_count)
    if result.failure_stage:
        trace["failure_stage"] = result.failure_stage
    if result.error_code:
        trace["error_code"] = result.error_code
    if result.error_field:
        trace["error_field"] = result.error_field
    if result.material_status:
        trace["material_status"] = result.material_status
        trace["material_source"] = result.material_source
        trace["tool_observation"] = result.tool_observation
    return trace


def _public(ok: bool, answer: str, status: str, *, awaiting_phone: bool = False, handoff_to_operator: bool = False, state_commit: bool = False, **safe: Any) -> dict[str, Any]:
    return {
        "ok": ok,
        "answer": answer,
        "intent": "v6",
        "awaiting_phone": awaiting_phone,
        "handoff_to_operator": handoff_to_operator,
        "buttons": [],
        "meta": {"runtime": "v6", "engine": "v6_simple", "status": status, "state_commit": state_commit, **safe},
    }
