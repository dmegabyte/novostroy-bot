"""Small deterministic turn owner: phone guard, Prompt1, then Prompt2."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .contract import CoreContractError, Prompt1Action, build_prompt1_input, build_prompt2_input, parse_prompt1, parse_prompt2
from .phone import PhoneMetadataBackend, PrivatePhone, parse_phone
from .state import CoreState


TECHNICAL_TEXT = "Сейчас не получилось обработать запрос. Попробуйте, пожалуйста, ещё раз."
SPECIALIST_OFFER_ON_FAILURE = "Сейчас не удалось проверить базу по вашему запросу. Хотите, чтобы этот запрос проверил специалист?"
SPECIALIST_CTA = "Хотите, чтобы специалист проверил актуальные варианты по вашему запросу?"
CLARIFICATION_FAILURE = "Не получилось уточнить запрос.\n\nМожете уточнить этот параметр поиска?"
PHONE_QUESTION = "На какой номер вам позвонить?"
MULTIPLE_PHONES_TEXT = "Пришлите, пожалуйста, один номер для связи."
INVALID_PHONE_TEXT = "Похоже, номер указан неверно. Пришлите его, пожалуйста, в формате +7 999 123-45-67."
_CANDIDATE = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,18}(?!\d)")
_SHORT_PHONE = re.compile(r"(?=.*\d)[\d\s().+-]+")
_CONTACT = re.compile(r"(?:позови(?:те)?|пригласи(?:те)?|подключи(?:те)?|соедини(?:те)?|перезвони(?:те)?|позвони(?:те)?|обратн(?:ый|ого)\s+звон(?:ок|ка))", re.IGNORECASE)


@dataclass(frozen=True)
class RuntimeResult:
    status: str
    state: CoreState
    text: str
    private_phone: PrivatePhone | None = None
    failure_stage: str | None = None
    model_calls: int = 0
    prompt1_attempt_ref: str | None = None
    prompt2_attempt_ref: str | None = None
    mcp_call_count: int = 0
    material_status: str | None = None
    tool_observation: str | None = None
    error_code: str | None = None
    request_phone: bool = False


def _contact_intent(text: str) -> bool:
    return bool(_CONTACT.search(text)) and len(text.split()) <= 6


def _phone_guard(text: str, backend: PhoneMetadataBackend | None) -> tuple[str, PrivatePhone | None]:
    phones: dict[str, PrivatePhone] = {}
    unavailable = False
    for match in _CANDIDATE.finditer(text):
        parsed = parse_phone(match.group(), backend)
        unavailable |= parsed.code == "dependency_unavailable"
        if parsed.private_phone:
            phones[parsed.private_phone.reveal_for_private_storage()] = parsed.private_phone
    if len(phones) > 1:
        return "multiple", None
    if phones:
        return "single", next(iter(phones.values()))
    if unavailable:
        return "dependency_failure", None
    return ("invalid", None) if _CANDIDATE.search(text) else ("none", None)


class CoreRuntime:
    def __init__(self, prompt1: Any, prompt2: Any, *, phone_backend: PhoneMetadataBackend | None = None) -> None:
        self._prompt1, self._prompt2, self._phone_backend = prompt1, prompt2, phone_backend

    async def run(self, message: str, state: CoreState) -> RuntimeResult:
        if not isinstance(message, str) or not message.strip() or not isinstance(state, CoreState):
            return RuntimeResult("failed", state, TECHNICAL_TEXT, failure_stage="input")
        phone_status, phone = _phone_guard(message, self._phone_backend)
        if phone_status == "single":
            return RuntimeResult("phone", state, "", private_phone=phone)
        if phone_status == "multiple":
            return RuntimeResult("multiple_phones", state, MULTIPLE_PHONES_TEXT)
        if phone_status == "dependency_failure":
            return RuntimeResult("failed", state, TECHNICAL_TEXT, failure_stage="phone")
        if state.awaiting_phone and (phone_status == "invalid" or (_SHORT_PHONE.fullmatch(message) and sum(c.isdigit() for c in message) < 10)):
            return RuntimeResult("invalid_phone", state, INVALID_PHONE_TEXT)
        public_message = _CANDIDATE.sub("[phone_redacted]", message)[:4000]
        if _contact_intent(public_message):
            try:
                next_state = state.accepted(public_message, PHONE_QUESTION, awaiting_phone=True)
            except CoreContractError:
                return RuntimeResult("failed", state, TECHNICAL_TEXT, failure_stage="state", request_phone=True)
            return RuntimeResult("completed", next_state, PHONE_QUESTION, request_phone=True)
        history = state.plain()["history"]
        calls = 0
        p1_attempt: str | None = None

        def failure(stage: str, code: str, *, p2_attempt: str | None = None, mcp_calls: int = 0, material: str | None = None) -> RuntimeResult:
            try:
                remembered = state.accepted(public_message, SPECIALIST_OFFER_ON_FAILURE, awaiting_phone=False, pending_offer="specialist_contact", specialist_offer_published=True)
            except CoreContractError:
                remembered = state
            return RuntimeResult("safe_failure", remembered, SPECIALIST_OFFER_ON_FAILURE, failure_stage=stage, model_calls=calls, prompt1_attempt_ref=p1_attempt, prompt2_attempt_ref=p2_attempt, mcp_call_count=mcp_calls, material_status=material, error_code=code)

        try:
            calls += 1
            p1_result = await self._prompt1.run(build_prompt1_input(public_message, history, pending_offer=state.pending_offer))
            p1_attempt = p1_result.attempt_ref
            p1 = parse_prompt1(p1_result.output)
        except CoreContractError as exc:
            return failure("prompt1", exc.code)
        except Exception:
            return failure("prompt1", "transport_failure")
        if p1.action is Prompt1Action.REQUEST_PHONE:
            try:
                next_state = state.accepted(public_message, PHONE_QUESTION, awaiting_phone=True)
            except CoreContractError:
                return RuntimeResult("failed", state, TECHNICAL_TEXT, failure_stage="state", model_calls=calls, prompt1_attempt_ref=p1_attempt, request_phone=True)
            return RuntimeResult("completed", next_state, PHONE_QUESTION, model_calls=calls, prompt1_attempt_ref=p1_attempt, request_phone=True)
        clarification = p1.action is Prompt1Action.CLARIFY
        trace = p1_result.tool_trace
        mcp_calls = trace.call_count if trace else 0
        material = "clarification_required" if clarification else ("accepted_nonempty" if p1.facts or p1.near else "accepted_empty")
        try:
            payload = build_prompt2_input(public_message, history, p1, offer_specialist_now=not clarification and state.client_turn_count + 1 == 3)
            calls += 1
            p2_result = await self._prompt2.run(payload)
            p2 = parse_prompt2(p2_result.output, allow_request_phone=False, require_final_question=clarification)
        except CoreContractError:
            try:
                calls += 1
                p2_result = await self._prompt2.run(payload, repair=True)
                p2 = parse_prompt2(p2_result.output, allow_request_phone=False, require_final_question=clarification)
            except CoreContractError as exc:
                if clarification:
                    return self._clarification_failure(state, public_message, calls, p1_attempt, mcp_calls, exc.code)
                return failure("prompt2", exc.code, mcp_calls=mcp_calls, material=material)
            except Exception:
                if clarification:
                    return self._clarification_failure(state, public_message, calls, p1_attempt, mcp_calls, "prompt2_failure")
                return failure("prompt2", "prompt2_failure", mcp_calls=mcp_calls, material=material)
        except Exception:
            if clarification:
                return self._clarification_failure(state, public_message, calls, p1_attempt, mcp_calls, "transport_failure")
            return failure("prompt2", "transport_failure", mcp_calls=mcp_calls, material=material)
        offer = not clarification and state.client_turn_count + 1 == 3
        question = SPECIALIST_CTA if offer else p2.final_question
        text = p2.response + ("\n\n" + question if question else "")
        if len(text) > 2000:
            return failure("prompt2", "output_too_large", p2_attempt=p2_result.attempt_ref, mcp_calls=mcp_calls, material=material)
        try:
            next_state = state.accepted(public_message, text, awaiting_phone=False, pending_offer="specialist_contact" if offer else "none", advance_client_turn=not clarification, specialist_offer_published=offer)
        except CoreContractError:
            return RuntimeResult("failed", state, TECHNICAL_TEXT, failure_stage="state", model_calls=calls, prompt1_attempt_ref=p1_attempt, prompt2_attempt_ref=p2_result.attempt_ref, mcp_call_count=mcp_calls, material_status=material)
        return RuntimeResult("completed", next_state, text, model_calls=calls, prompt1_attempt_ref=p1_attempt, prompt2_attempt_ref=p2_result.attempt_ref, mcp_call_count=mcp_calls, material_status=material, tool_observation="observed_exact" if trace else "unavailable")

    @staticmethod
    def _clarification_failure(state: CoreState, message: str, calls: int, p1_attempt: str | None, mcp_calls: int, code: str) -> RuntimeResult:
        try:
            remembered = state.accepted(message, CLARIFICATION_FAILURE, awaiting_phone=False, advance_client_turn=False)
        except CoreContractError:
            remembered = state
        return RuntimeResult("safe_failure", remembered, CLARIFICATION_FAILURE, failure_stage="prompt2", model_calls=calls, prompt1_attempt_ref=p1_attempt, mcp_call_count=mcp_calls, material_status="clarification_required", error_code=code)
