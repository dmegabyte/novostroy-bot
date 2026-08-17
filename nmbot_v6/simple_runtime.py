"""Linear phone guard -> Prompt 1 -> Prompt 2 runtime."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .phone import PhoneMetadataBackend, PrivatePhone, parse_phone
from .simple_contract import SimpleContractError, build_prompt1_input, build_prompt2_input, parse_prompt1, parse_prompt2
from .simple_state import SimpleState

TECHNICAL_TEXT = "Сейчас не получилось обработать запрос. Попробуйте, пожалуйста, ещё раз."
SPECIALIST_OFFER_ON_FAILURE = "Сейчас не удалось проверить базу по вашему запросу. Хотите, чтобы этот запрос проверил специалист?"
SPECIALIST_CTA = "Хотите, чтобы специалист проверил актуальные варианты по вашему запросу?"
CLARIFICATION_FAILURE = "Не получилось уточнить запрос.\n\nМожете уточнить этот параметр поиска?"
PHONE_QUESTION = "На какой номер вам позвонить?"
MULTIPLE_PHONES_TEXT = "Пришлите, пожалуйста, один номер для связи."
INVALID_PHONE_TEXT = "Похоже, номер указан неверно. Пришлите его, пожалуйста, в формате +7 999 123-45-67."
URL_CARD_FAILURE_TEXT = "Не удалось открыть карточку по этой ссылке. Проверьте ссылку и попробуйте ещё раз."
_CANDIDATE = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,18}(?!\d)")
_SHORT_PHONE_ATTEMPT = re.compile(r"(?=.*\d)[\d\s().+-]+")
_CONTACT_EDGE = " \t\r\n.,!?;:\"'«»"
_LIVE_OPERATOR_COMMANDS = tuple(re.compile(pattern) for pattern in (
    r"(?:позови|позовите|пригласи|пригласите|подключи|подключите)\s+(?:мне\s+)?(?:оператора|менеджера)",
    r"(?:оператора|менеджера)\s+(?:позови|позовите|пригласи|пригласите|подключи|подключите)",
    r"(?:соедини|соедините)\s+(?:меня\s+)?с\s+(?:(?:живым|реальным)\s+)?(?:оператором|менеджером|человеком)",
))
_CALLBACK_COMMANDS = tuple(re.compile(pattern) for pattern in (
    r"(?:перезвони|перезвоните|позвони|позвоните)(?:\s+мне)?(?:\s+пожалуйста)?",
    r"(?:закажи|закажите)\s+(?:мне\s+)?обратный\s+звонок",
    r"(?:хочу|нужен)\s+обратный\s+звонок",
))


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
    url_card_status: str | None = None


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
    if plausible:
        return PhoneGuardResult("invalid")
    return PhoneGuardResult("none")


def _is_short_phone_attempt(text: str) -> bool:
    """Recognize incomplete phone-only input while a phone is awaited."""
    return bool(_SHORT_PHONE_ATTEMPT.fullmatch(text)) and sum(char.isdigit() for char in text) < 10


def classify_contact_intent(text: str) -> str:
    """Classify only standalone contact commands, never questions/explanations."""
    normalized = str(text or "").casefold().replace("ё", "е")
    normalized = re.sub(r"[,;:]", " ", normalized).strip(_CONTACT_EDGE)
    normalized = re.sub(r"(?:^|\s)пожалуйста(?:\s|$)", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if any(pattern.fullmatch(normalized) for pattern in _CALLBACK_COMMANDS):
        return "callback"
    if any(pattern.fullmatch(normalized) for pattern in _LIVE_OPERATOR_COMMANDS):
        return "live_operator"
    return "none"


class SimpleRuntime:
    def __init__(
        self,
        prompt1: Any,
        prompt2: Any,
        *,
        phone_backend: PhoneMetadataBackend | None = None,
        url_card_fetcher: Callable[[str], Mapping[str, Any]] | None = None,
        url_card_extractor: Callable[[str], str | None] | None = None,
    ) -> None:
        self.prompt1, self.prompt2, self.phone_backend = prompt1, prompt2, phone_backend
        self.url_card_fetcher, self.url_card_extractor = url_card_fetcher, url_card_extractor

    @staticmethod
    def _url_card_failure(
        state: SimpleState,
        message: str,
        *,
        code: str,
        status: str,
    ) -> SimpleRuntimeResult:
        try:
            remembered = state.accepted(
                message,
                URL_CARD_FAILURE_TEXT,
                awaiting_phone=False,
                pending_offer="none",
                advance_client_turn=False,
            )
        except Exception:
            return SimpleRuntimeResult("failed", state, TECHNICAL_TEXT, failure_stage="state", url_card_status=status)
        return SimpleRuntimeResult(
            "safe_failure",
            remembered,
            URL_CARD_FAILURE_TEXT,
            failure_stage="url_card",
            material_status="url_card_failed",
            material_source="url_card_tool",
            tool_observation="not_called",
            error_code=code,
            url_card_status=status,
        )

    @staticmethod
    def _url_card_prompt2_failure(
        state: SimpleState,
        message: str,
        *,
        code: str,
        calls: int,
        p2_attempt: str | None,
    ) -> SimpleRuntimeResult:
        try:
            remembered = state.accepted(
                message,
                SPECIALIST_OFFER_ON_FAILURE,
                awaiting_phone=False,
                pending_offer="specialist_contact",
                specialist_offer_published=True,
            )
        except Exception:
            return SimpleRuntimeResult(
                "failed",
                state,
                TECHNICAL_TEXT,
                failure_stage="state",
                model_calls=calls,
                prompt2_attempt_ref=p2_attempt,
                material_status="url_card_accepted",
                material_source="url_card_tool",
                tool_observation="url_card",
                url_card_status="prompt2_failed",
            )
        return SimpleRuntimeResult(
            "safe_failure",
            remembered,
            SPECIALIST_OFFER_ON_FAILURE,
            failure_stage="prompt2",
            model_calls=calls,
            prompt2_attempt_ref=p2_attempt,
            material_status="url_card_accepted",
            material_source="url_card_tool",
            tool_observation="url_card",
            error_code=code,
            url_card_status="prompt2_failed",
        )

    async def _run_url_card_turn(
        self,
        source_url: str,
        message: str,
        history: list[dict[str, str]],
        state: SimpleState,
    ) -> SimpleRuntimeResult:
        try:
            raw_card = await asyncio.to_thread(self.url_card_fetcher, source_url)
        except Exception:
            return self._url_card_failure(
                state,
                message,
                code="url_card_fetch_failed",
                status="fetch_failed",
            )

        offer_specialist_now = state.client_turn_count + 1 == 3
        try:
            p2_payload = build_prompt2_input(
                message,
                history,
                None,
                offer_specialist_now=offer_specialist_now,
                url_card=raw_card,
            )
        except SimpleContractError as exc:
            return self._url_card_failure(
                state,
                message,
                code=exc.code,
                status="invalid_card",
            )

        calls = 0
        p2_attempt: str | None = None
        try:
            calls += 1
            p2_result = await self.prompt2.run(p2_payload)
        except Exception:
            return self._url_card_prompt2_failure(
                state,
                message,
                code="transport_failure",
                calls=calls,
                p2_attempt=p2_attempt,
            )
        try:
            p2 = parse_prompt2(p2_result.output, allow_request_phone=False)
        except SimpleContractError:
            try:
                calls += 1
                p2_result = await self.prompt2.run(p2_payload, repair=True)
                p2 = parse_prompt2(p2_result.output, allow_request_phone=False)
            except Exception as exc:
                code = exc.code if isinstance(exc, SimpleContractError) else "prompt2_failure"
                return self._url_card_prompt2_failure(
                    state,
                    message,
                    code=code,
                    calls=calls,
                    p2_attempt=p2_attempt,
                )
        p2_attempt = p2_result.attempt_ref
        final_question = SPECIALIST_CTA if offer_specialist_now else p2.final_question
        text = p2.response + ("\n\n" + final_question if final_question else "")
        if len(text) > 2000:
            return self._url_card_prompt2_failure(
                state,
                message,
                code="output_too_large",
                calls=calls,
                p2_attempt=p2_attempt,
            )
        try:
            next_state = state.accepted(
                message,
                text,
                awaiting_phone=False,
                pending_offer="specialist_contact" if offer_specialist_now else "none",
                specialist_offer_published=offer_specialist_now,
            )
        except Exception:
            return SimpleRuntimeResult(
                "failed",
                state,
                TECHNICAL_TEXT,
                failure_stage="state",
                model_calls=calls,
                prompt2_attempt_ref=p2_attempt,
                material_status="url_card_accepted",
                material_source="url_card_tool",
                tool_observation="url_card",
                url_card_status="accepted",
            )
        return SimpleRuntimeResult(
            "completed",
            next_state,
            text,
            model_calls=calls,
            prompt2_attempt_ref=p2_attempt,
            material_status="url_card_accepted",
            material_source="url_card_tool",
            tool_observation="url_card",
            url_card_status="accepted",
        )

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
        if state.awaiting_phone and (guard.status == "invalid" or _is_short_phone_attempt(current_message)):
            return SimpleRuntimeResult("invalid_phone", state, INVALID_PHONE_TEXT)
        # A plausible but invalid candidate is not allowed beyond the privacy
        # boundary. Proven non-phone numbers do not match this candidate shape.
        message = _CANDIDATE.sub("[phone_redacted]", current_message)[:4000]
        history = state.plain()["history"]
        calls = 0
        p1_attempt = None

        contact_intent = classify_contact_intent(message)
        if contact_intent == "live_operator":
            return SimpleRuntimeResult("operator_handoff", state, "")
        if contact_intent == "callback":
            try:
                next_state = state.accepted(
                    message, PHONE_QUESTION, awaiting_phone=True, pending_offer="none",
                )
            except Exception:
                return SimpleRuntimeResult(
                    "failed", state, TECHNICAL_TEXT, failure_stage="state", request_phone=True,
                )
            return SimpleRuntimeResult(
                "completed", next_state, PHONE_QUESTION, request_phone=True,
            )

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

        if self.url_card_fetcher is not None and self.url_card_extractor is not None:
            try:
                source_url = self.url_card_extractor(current_message)
            except Exception:
                return self._url_card_failure(
                    state,
                    message,
                    code="url_card_extractor_failure",
                    status="extractor_failed",
                )
            if source_url:
                return await self._run_url_card_turn(source_url, message, history, state)

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
