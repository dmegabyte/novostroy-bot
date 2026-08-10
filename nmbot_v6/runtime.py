"""Minimal injected-port orchestration for the standalone V6 flow."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .contracts import ContractError
from .phone import PhoneMetadataBackend, PhoneParseResult, PrivatePhone, parse_phone
from .gateway import build_question_policy
from .privacy import NUMERIC_PARAM_FIELDS, immutable_safe_copy
from .prompt1_contract import (
    Prompt1Result, SearchAction, SearchPolicy, SearchTarget, parse_prompt1,
)
from .prompt2_contract import parse_prompt2
from .provider import TransportToolTrace, TrustedMcpEnvelope, build_trusted_envelope
from .state import V6State, evolve_completed_state
from .validation import validate_prompt1_state
from .followup import (
    FollowupKind, PendingInteractionResolver, dispatch_followup, exact_detail_context,
)


class Prompt1Port(Protocol):
    def __call__(self, user_text: str, state: Mapping[str, Any]) -> str | Mapping[str, Any]: ...


class McpPort(Protocol):
    def execute(self, request: Mapping[str, Any]) -> TransportToolTrace: ...


class PhoneParser(Protocol):
    def __call__(
        self,
        text: str,
        backend: PhoneMetadataBackend | None = None,
    ) -> PhoneParseResult: ...


class Prompt2Port(Protocol):
    def __call__(
        self,
        user_text: str,
        state: Mapping[str, Any],
        plan: Prompt1Result,
        evidence: TrustedMcpEnvelope,
    ) -> str: ...


class RuntimeStatus(str, Enum):
    PHONE_BYPASS = "phone_bypass"
    COMPLETED = "completed"
    FAILED = "failed"


class RuntimeFailureStage(str, Enum):
    INPUT = "input"
    PHONE = "phone"
    PROMPT1 = "prompt1"
    MCP = "mcp"
    PROMPT2 = "prompt2"
    STATE = "state"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RuntimeResult:
    status: RuntimeStatus
    state: V6State
    text: str | None = None
    private_phone: PrivatePhone | None = None
    failure_code: str | None = None
    plan: Prompt1Result | None = None
    evidence: TrustedMcpEnvelope | None = None
    failure_stage: RuntimeFailureStage | None = None


class V6Runtime:
    """Async V6 flow using only V6-owned injected prompt gateways."""

    def __init__(
        self,
        prompt1: Any,
        prompt2: Any,
        *,
        phone_backend: PhoneMetadataBackend | None = None,
        phone_parser: PhoneParser = parse_phone,
    ) -> None:
        self._prompt1 = prompt1
        self._prompt2 = prompt2
        self._phone_backend = phone_backend
        self._phone_parser = phone_parser

    async def run(self, user_text: str, state: V6State) -> RuntimeResult:
        if not isinstance(user_text, str) or not user_text.strip() or not isinstance(state, V6State):
            return _failure(state, "invalid_input", RuntimeFailureStage.INPUT)

        try:
            phone = self._phone_parser(user_text, self._phone_backend)
        except Exception:
            return _failure(state, "provider_failure", RuntimeFailureStage.PHONE)
        if phone.recognized:
            return _checked(
                RuntimeResult(RuntimeStatus.PHONE_BYPASS, state, private_phone=phone.private_phone)
            )
        if phone.code == "dependency_unavailable":
            return _failure(state, "phone_dependency_unavailable", RuntimeFailureStage.PHONE)

        resolution = PendingInteractionResolver().resolve(user_text, state)
        exact_detail = exact_detail_context(resolution, state)
        if resolution.kind is not FollowupKind.UNRESOLVED:
            if exact_detail is None:
                execution = dispatch_followup(resolution, state)
                if execution is not None:
                    return _checked(RuntimeResult(
                        RuntimeStatus.COMPLETED, execution.state, text=execution.text
                    ))
        flow_state = _exact_detail_flow_state(state, exact_detail)
        model_state = _model_state_projection(flow_state, exact_detail)

        try:
            gateway_result = await self._prompt1.run(user_text, model_state)
        except Exception:
            return _failure(state, "provider_failure", RuntimeFailureStage.PROMPT1)
        try:
            plan = parse_prompt1(gateway_result.output)
            validate_prompt1_state(plan, model_state)
        except ContractError as first_violation:
            violation_code = _prompt1_violation_code(first_violation)
            retry = getattr(self._prompt1, "retry", None)
            if not callable(retry):
                return _failure(
                    state, "prompt1_contract_violation", RuntimeFailureStage.PROMPT1
                )
            try:
                gateway_result = await retry(user_text, model_state, violation_code)
            except Exception:
                return _failure(state, "provider_failure", RuntimeFailureStage.PROMPT1)
            try:
                plan = parse_prompt1(gateway_result.output)
                validate_prompt1_state(plan, model_state)
            except ContractError:
                return _failure(
                    state, "prompt1_contract_violation", RuntimeFailureStage.PROMPT1
                )
        plan = _repair_ambiguous_operator_contact(plan, user_text, flow_state)
        try:
            validate_prompt1_state(plan, model_state)
        except ContractError:
            return _failure(
                state, "prompt1_contract_violation", RuntimeFailureStage.PROMPT1
            )
        if plan.action.value == "operator_contact":
            try:
                evidence = build_trusted_envelope(
                    search_required=False, requested_tool=None, trace=None
                )
                next_state = evolve_completed_state(
                    flow_state, plan, evidence, pending_phone=True, question_goal="operator_contact"
                )
            except Exception:
                return _failure(
                    state, "provider_failure", RuntimeFailureStage.STATE, plan=plan
                )
            return _checked(RuntimeResult(
                RuntimeStatus.COMPLETED,
                next_state,
                text="На какой номер вам позвонить?",
                plan=plan,
                evidence=evidence,
            ))
        try:
            evidence = build_trusted_envelope(
                search_required=plan.search_policy is SearchPolicy.REQUIRED,
                requested_tool=(
                    "get_flat_info" if plan.search_policy is SearchPolicy.REQUIRED else None
                ),
                trace=(gateway_result.tool_trace if plan.search_policy is SearchPolicy.REQUIRED else None),
            )
            from .validation import validate_publication_precondition

            validate_publication_precondition(plan, evidence, exact_detail)
        except ContractError:
            return _failure(
                state, "mcp_contract_violation", RuntimeFailureStage.MCP, plan=plan
            )
        except Exception:
            return _failure(
                state, "provider_failure", RuntimeFailureStage.MCP, plan=plan
            )
        try:
            raw_prompt2 = await self._prompt2.run(user_text, model_state, plan, evidence)
            text = parse_prompt2(raw_prompt2, evidence)
        except Exception:
            try:
                retry = getattr(self._prompt2, "retry", None)
                text = parse_prompt2(
                    await (
                        retry(user_text, model_state, plan, evidence)
                        if callable(retry)
                        else self._prompt2.run(user_text, model_state, plan, evidence)
                    ),
                    evidence,
                )
            except Exception:
                return _failure(
                    state, "provider_failure", RuntimeFailureStage.PROMPT2,
                    plan=plan, evidence=evidence,
                )
        try:
            next_state = evolve_completed_state(
                flow_state, plan, evidence,
                pending_phone=(plan.action.value == "operator_contact" or _question_requests_operator(text)),
                question_goal=build_question_policy(user_text, model_state, plan)["question_goal"],
            )
        except Exception:
            return _failure(
                state, "provider_failure", RuntimeFailureStage.STATE,
                plan=plan, evidence=evidence,
            )
        return _checked(RuntimeResult(
            RuntimeStatus.COMPLETED,
            next_state,
            text=text,
            plan=plan,
            evidence=evidence,
        ))


def run_v6(
    user_text: str,
    state: V6State,
    *,
    prompt1: Prompt1Port,
    mcp: McpPort,
    prompt2: Prompt2Port,
    phone_backend: PhoneMetadataBackend | None = None,
    phone_parser: PhoneParser = parse_phone,
) -> RuntimeResult:
    """Run one immutable V6 turn; callers own persistence and publication."""

    if not isinstance(user_text, str) or not user_text.strip() or not isinstance(state, V6State):
        return _failure(state, "invalid_input", RuntimeFailureStage.INPUT)

    try:
        phone = phone_parser(user_text, phone_backend)
    except Exception:
        return _failure(state, "provider_failure", RuntimeFailureStage.PHONE)
    if phone.recognized:
        return _checked(RuntimeResult(RuntimeStatus.PHONE_BYPASS, state, private_phone=phone.private_phone))
    if phone.code == "dependency_unavailable":
        return _failure(state, "phone_dependency_unavailable", RuntimeFailureStage.PHONE)

    resolution = PendingInteractionResolver().resolve(user_text, state)
    exact_detail = exact_detail_context(resolution, state)
    if resolution.kind is not FollowupKind.UNRESOLVED:
        if exact_detail is None:
            execution = dispatch_followup(resolution, state)
            if execution is not None:
                return _checked(RuntimeResult(
                    RuntimeStatus.COMPLETED, execution.state, text=execution.text
                ))
    flow_state = _exact_detail_flow_state(state, exact_detail)
    model_state = _model_state_projection(flow_state, exact_detail)

    try:
        plan = parse_prompt1(prompt1(user_text, model_state))
        validate_prompt1_state(plan, model_state)
    except ContractError:
        return _failure(state, "prompt1_contract_violation", RuntimeFailureStage.PROMPT1)
    except Exception:
        return _failure(state, "provider_failure", RuntimeFailureStage.PROMPT1)
    plan = _repair_ambiguous_operator_contact(plan, user_text, flow_state)
    try:
        validate_prompt1_state(plan, model_state)
    except ContractError:
        return _failure(state, "prompt1_contract_violation", RuntimeFailureStage.PROMPT1)
    try:
        if plan.search_policy is SearchPolicy.REQUIRED:
            request = MappingProxyType({
                "tool": "get_flat_info",
                "params": immutable_safe_copy(
                    plan.params,
                    allowed_numeric_fields=NUMERIC_PARAM_FIELDS,
                ),
            })
            trace = mcp.execute(request)
            evidence = build_trusted_envelope(
                search_required=True,
                requested_tool="get_flat_info",
                trace=trace,
            )
        else:
            evidence = build_trusted_envelope(
                search_required=False,
                requested_tool=None,
                trace=None,
            )
        from .validation import validate_publication_precondition

        validate_publication_precondition(plan, evidence, exact_detail)
    except ContractError:
        return _failure(
            state, "mcp_contract_violation", RuntimeFailureStage.MCP, plan=plan
        )
    except Exception:
        return _failure(
            state, "provider_failure", RuntimeFailureStage.MCP, plan=plan
        )
    try:
        raw_prompt2 = prompt2(user_text, model_state, plan, evidence)
        text = parse_prompt2(raw_prompt2, evidence)
    except Exception:
        try:
            text = parse_prompt2(
                prompt2(user_text, model_state, plan, evidence), evidence
            )
        except Exception:
            return _failure(
                state, "provider_failure", RuntimeFailureStage.PROMPT2,
                plan=plan, evidence=evidence,
            )
    try:
        next_state = evolve_completed_state(
            flow_state, plan, evidence,
            pending_phone=(plan.action.value == "operator_contact" or _question_requests_operator(text)),
            question_goal=build_question_policy(user_text, model_state, plan)["question_goal"],
        )
    except Exception:
        return _failure(
            state, "provider_failure", RuntimeFailureStage.STATE,
            plan=plan, evidence=evidence,
        )
    return _checked(RuntimeResult(
        RuntimeStatus.COMPLETED,
        next_state,
        text=text,
        plan=plan,
        evidence=evidence,
    ))


def _repair_ambiguous_operator_contact(
    plan: Prompt1Result,
    user_text: str,
    state: V6State,
) -> Prompt1Result:
    """Do not turn a bare consent into a phone flow without contact context."""
    if plan.action is not SearchAction.OPERATOR_CONTACT or not _is_short_consent(user_text):
        return plan
    context = state.safe_context if isinstance(state.safe_context, Mapping) else {}
    if context.get("last_question_goal") == "operator_contact":
        return plan
    return replace(
        plan,
        action=SearchAction.ANSWER_CURRENT_OPTIONS,
        target=SearchTarget.CURRENT_OPTIONS,
        search_policy=SearchPolicy.FORBIDDEN,
        clarification_question="",
        response="",
        facts=tuple(state.current_cards),
        near=(),
        missing=(),
        params={},
    )


def _is_short_consent(text: str) -> bool:
    return str(text or "").strip().casefold() in {
        "да", "ага", "угу", "хорошо", "конечно", "давай", "согласен", "согласна",
    }


def _checked(result: RuntimeResult) -> RuntimeResult:
    from .validation import validate_runtime_result

    validate_runtime_result(result)
    return result


_OPERATOR_QUESTION = re.compile(
    r"оператор|специалист|перезвон|позвонить|связаться",
    re.IGNORECASE,
)


def _question_requests_operator(text: str) -> bool:
    """Persist only the contact intent, never generated text itself."""
    return bool(isinstance(text, str) and _OPERATOR_QUESTION.search(text))


def _model_state_projection(
    state: V6State,
    exact_detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep the pre-existing model payload shape; pending actions remain code-owned."""
    projection = state.safe_projection()
    projection.pop("pending_interaction", None)
    if exact_detail is not None:
        context = dict(projection.get("safe_context", {}))
        context["exact_detail"] = dict(exact_detail)
        projection["safe_context"] = context
    return projection


def _exact_detail_flow_state(
    state: V6State,
    exact_detail: Mapping[str, Any] | None,
) -> V6State:
    if exact_detail is None:
        return state
    subject_ref = exact_detail.get("subject_ref")
    selected = subject_ref if subject_ref in state.option_refs else state.selected_option_ref
    return replace(state, selected_option_ref=selected, pending_interaction=None)


def _prompt1_violation_code(exc: ContractError) -> str:
    if "answer_current_options requires stored current cards" in str(exc):
        return "current_options_without_stored_cards"
    return "invalid_prompt1_contract"


def _failure(
    state: V6State,
    code: str,
    stage: RuntimeFailureStage,
    *,
    plan: Prompt1Result | None = None,
    evidence: TrustedMcpEnvelope | None = None,
) -> RuntimeResult:
    return _checked(RuntimeResult(
        RuntimeStatus.FAILED,
        state,
        failure_code=code,
        failure_stage=stage,
        plan=plan,
        evidence=evidence,
    ))
