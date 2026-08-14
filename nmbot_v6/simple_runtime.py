"""Linear phone guard -> Prompt 1 -> Prompt 2 runtime."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .phone import PhoneMetadataBackend, PrivatePhone, parse_phone
from .simple_contract import SimpleContractError, build_prompt1_input, build_prompt2_input, parse_prompt1, parse_prompt2
from .simple_state import SimpleState

TECHNICAL_TEXT = "Сейчас не получилось обработать запрос. Попробуйте, пожалуйста, ещё раз."
SPECIALIST_OFFER_ON_FAILURE = "Сейчас не удалось проверить базу по вашему запросу. Хотите, чтобы этот запрос проверил специалист?"
SPECIALIST_CTA = "Хотите, чтобы специалист проверил актуальные варианты по вашему запросу?"
CLARIFICATION_FAILURE = "Не получилось уточнить запрос.\n\nМожете уточнить этот параметр поиска?"
PHONE_QUESTION = "На какой номер вам позвонить?"
MULTIPLE_PHONES_TEXT = "Пришлите, пожалуйста, один номер для связи."
_CANDIDATE = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,18}(?!\d)")


@dataclass(frozen=True)
class PhoneGuardResult:
    status: str
    phone: PrivatePhone | None = None


@dataclass(frozen=True)
class SimpleRuntimeResult:
    status: str
    state: SimpleState
    text: str
    private_phone: PrivatePhone | None = None
    failure_stage: str | None = None
    model_calls: int = 0
    prompt1_attempt_ref: str | None = None
    prompt2_attempt_ref: str | None = None
    mcp_call_count: int = 0
    mcp_required: bool = False
    material_status: str | None = None
    material_source: str | None = None
    tool_observation: str | None = None
    error_code: str | None = None
    error_field: str | None = None
    prompt1_failed: bool = False
    request_phone: bool = False


def guard_phone(text: str, backend: PhoneMetadataBackend | None = None) -> PhoneGuardResult:
    normalized: dict[str, PrivatePhone] = {}
    plausible = list(_CANDIDATE.finditer(text))
    dependency_failed = False
    for match in plausible:
        parsed = parse_phone(match.group(), backend)
        if parsed.code == "dependency_unavailable":
            dependency_failed = True
        if parsed.recognized and parsed.private_phone is not None:
            key = parsed.private_phone.reveal_for_private_storage()
            normalized[key] = parsed.private_phone
    if len(normalized) > 1:
        return PhoneGuardResult("multiple")
    if len(normalized) == 1:
        return PhoneGuardResult("single", next(iter(normalized.values())))
    if dependency_failed:
        return PhoneGuardResult("dependency_failure")
    return PhoneGuardResult("none")


class SimpleRuntime:
    def __init__(self, prompt1: Any, prompt2: Any, *, phone_backend: PhoneMetadataBackend | None = None) -> None:
        self.prompt1, self.prompt2, self.phone_backend = prompt1, prompt2, phone_backend

    async def run(self, current_message: str, state: SimpleState) -> SimpleRuntimeResult:
        if not isinstance(current_message, str) or not current_message.strip() or not isinstance(state, SimpleState):
            return SimpleRuntimeResult("failed", state, TECHNICAL_TEXT, failure_stage="input")
        guard = guard_phone(current_message, self.phone_backend)
        if guard.status == "single":
            return SimpleRuntimeResult("phone", state, "", private_phone=guard.phone)
        if guard.status == "multiple":
            return SimpleRuntimeResult("multiple_phones", state, MULTIPLE_PHONES_TEXT)
        if guard.status == "dependency_failure":
            return SimpleRuntimeResult("failed", state, TECHNICAL_TEXT, failure_stage="phone")
        # A plausible but invalid candidate is not allowed beyond the privacy
        # boundary. Proven non-phone numbers do not match this candidate shape.
        message = _CANDIDATE.sub("[phone_redacted]", current_message)[:4000]
        history = state.plain()["history"]
        calls = 0
        p1_attempt = None

        def safe_failure(*, stage: str, code: str, field: str | None = None, prompt1_failed: bool = False, p2_attempt: str | None = None, mcp_calls: int = 0, material_status: str | None = None, observation: str | None = None) -> SimpleRuntimeResult:
            """Persist only the fixed public offer so related consent remains model-owned."""
            try:
                remembered = state.accepted(
                    message, SPECIALIST_OFFER_ON_FAILURE, awaiting_phone=False,
                    pending_offer="specialist_contact", specialist_offer_published=True,
                )
            except Exception:
                remembered = state
            return SimpleRuntimeResult(
                "safe_failure", remembered, SPECIALIST_OFFER_ON_FAILURE,
                failure_stage=stage, model_calls=calls, prompt1_attempt_ref=p1_attempt,
                prompt2_attempt_ref=p2_attempt, mcp_call_count=mcp_calls,
                material_status=material_status, material_source="gateway_returned_projection" if material_status else None,
                tool_observation=observation, error_code=code, error_field=field,
                prompt1_failed=prompt1_failed,
            )

        def clarification_failure(*, code: str, p2_attempt: str | None = None,
                                  mcp_calls: int = 0, observation: str | None = None) -> SimpleRuntimeResult:
            try:
                remembered = state.accepted(
                    message, CLARIFICATION_FAILURE, awaiting_phone=False,
                    pending_offer="none", advance_client_turn=False,
                )
            except Exception:
                remembered = state
            return SimpleRuntimeResult(
                "safe_failure", remembered, CLARIFICATION_FAILURE,
                failure_stage="prompt2", model_calls=calls,
                prompt1_attempt_ref=p1_attempt, prompt2_attempt_ref=p2_attempt,
                mcp_call_count=mcp_calls, material_status="clarification_required",
                tool_observation=observation, error_code=code,
            )
        try:
            calls += 1
            p1_result = await self.prompt1.run(
                build_prompt1_input(message, history, pending_offer=state.pending_offer)
            )
            p1_attempt = p1_result.attempt_ref
            try:
                p1 = parse_prompt1(p1_result.output)
            except SimpleContractError as exc:
                return safe_failure(stage="prompt1", code=exc.code, field=exc.field, prompt1_failed=True)
        except Exception:
            return safe_failure(stage="prompt1", code="transport_failure", prompt1_failed=True)
        if p1.action == "request_phone":
            try:
                next_state = state.accepted(
                    message, PHONE_QUESTION, awaiting_phone=True, pending_offer="none",
                )
            except Exception:
                return SimpleRuntimeResult(
                    "failed", state, TECHNICAL_TEXT, failure_stage="state", model_calls=calls,
                    prompt1_attempt_ref=p1_attempt, request_phone=True,
                )
            return SimpleRuntimeResult(
                "completed", next_state, PHONE_QUESTION, model_calls=calls,
                prompt1_attempt_ref=p1_attempt, request_phone=True,
            )
        is_clarification = p1.action == "clarify"
        material_status = "clarification_required" if is_clarification else ("accepted_nonempty" if p1.facts or p1.near else "accepted_empty")
        observation = "observed_exact" if p1_result.tool_trace is not None else "unavailable"
        mcp_calls = p1_result.tool_trace.call_count if p1_result.tool_trace else 0
        offer_specialist_now = (
            not is_clarification
            and state.client_turn_count + 1 == 3
        )
        p2_payload = build_prompt2_input(
            message, history, p1, offer_specialist_now=offer_specialist_now,
        )
        try:
            calls += 1
            p2_result = await self.prompt2.run(p2_payload)
        except Exception:
            if is_clarification:
                return clarification_failure(code="transport_failure", mcp_calls=mcp_calls, observation=observation)
            return safe_failure(stage="prompt2", code="transport_failure", mcp_calls=mcp_calls, material_status=material_status, observation=observation)
        try:
            p2 = parse_prompt2(
                p2_result.output, allow_request_phone=False,
                require_final_question=is_clarification,
            )
        except SimpleContractError:
            try:
                calls += 1
                p2_result = await self.prompt2.run(p2_payload, repair=True)
                p2 = parse_prompt2(
                    p2_result.output, allow_request_phone=False,
                    require_final_question=is_clarification,
                )
            except Exception as exc:
                code = exc.code if isinstance(exc, SimpleContractError) else "prompt2_failure"
                if is_clarification:
                    return clarification_failure(code=code, mcp_calls=mcp_calls, observation=observation)
                return safe_failure(stage="prompt2", code=code, mcp_calls=mcp_calls, material_status=material_status, observation=observation)
        p2_attempt = p2_result.attempt_ref
        # On turn three the typed policy owns the sole published question.
        # Replacing rather than inspecting P2 prose keeps pending_offer truthful.
        final_question = SPECIALIST_CTA if offer_specialist_now else p2.final_question
        text = p2.response + ("\n\n" + final_question if final_question else "")
        if len(text) > 2000:
            return safe_failure(stage="prompt2", code="output_too_large", p2_attempt=p2_attempt, mcp_calls=mcp_calls, material_status=material_status, observation=observation)
        try:
            next_state = state.accepted(
                message, text, awaiting_phone=False,
                pending_offer="specialist_contact" if offer_specialist_now else "none",
                advance_client_turn=not is_clarification,
                specialist_offer_published=offer_specialist_now,
            )
        except Exception:
            return SimpleRuntimeResult("failed", state, TECHNICAL_TEXT, failure_stage="state", model_calls=calls, prompt1_attempt_ref=p1_attempt, prompt2_attempt_ref=p2_attempt, mcp_call_count=mcp_calls, material_status=material_status, material_source="gateway_returned_projection", tool_observation=observation)
        return SimpleRuntimeResult("completed", next_state, text, model_calls=calls, prompt1_attempt_ref=p1_attempt, prompt2_attempt_ref=p2_attempt, mcp_call_count=mcp_calls, material_status=material_status, material_source="gateway_returned_projection", tool_observation=observation)
