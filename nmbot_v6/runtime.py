"""Minimal injected-port orchestration for the standalone V6 flow."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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
    meta: Mapping[str, Any] = field(default_factory=dict)


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
        gateway_attempts: list[dict[str, Any]] = []
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
        flow_state = _followup_flow_state(state, resolution, exact_detail)
        model_state = _model_state_projection(flow_state, exact_detail)

        try:
            gateway_result = await self._prompt1.run(user_text, model_state)
        except Exception:
            gateway_attempts.append(_gateway_attempt("v6_search_agent", gateway_status="error", parse_status="missing", validator_status="missing"))
            return _failure(state, "provider_failure", RuntimeFailureStage.PROMPT1, gateway_attempts=gateway_attempts)
        prompt1_attempt = _gateway_attempt("v6_search_agent", gateway_status="completed")
        if getattr(gateway_result, "tool_trace", None) is not None:
            prompt1_attempt["gateway_task_id_present"] = True
        gateway_attempts.append(prompt1_attempt)
        try:
            plan = parse_prompt1(gateway_result.output)
            prompt1_attempt["parse_status"] = "ok"
        except ContractError as first_violation:
            prompt1_attempt["parse_status"] = "invalid_json"
            prompt1_attempt["validator_status"] = "missing"
            violation_code = _prompt1_violation_code(first_violation)
            retry = getattr(self._prompt1, "retry", None)
            if not callable(retry):
                return _failure(state, "prompt1_contract_violation", RuntimeFailureStage.PROMPT1, gateway_attempts=gateway_attempts)
            try:
                gateway_result = await retry(user_text, model_state, violation_code)
            except Exception:
                gateway_attempts.append(_gateway_attempt("v6_search_agent", gateway_status="error", parse_status="missing", validator_status="missing"))
                return _failure(state, "provider_failure", RuntimeFailureStage.PROMPT1, gateway_attempts=gateway_attempts)
            prompt1_attempt = _gateway_attempt("v6_search_agent", gateway_status="completed")
            if getattr(gateway_result, "tool_trace", None) is not None:
                prompt1_attempt["gateway_task_id_present"] = True
            gateway_attempts.append(prompt1_attempt)
            try:
                plan = parse_prompt1(gateway_result.output)
                prompt1_attempt["parse_status"] = "ok"
                validate_prompt1_state(plan, model_state)
                prompt1_attempt["validator_status"] = "ok"
            except ContractError:
                if prompt1_attempt.get("parse_status") != "ok":
                    prompt1_attempt.update(parse_status="invalid_json", validator_status="missing")
                else:
                    prompt1_attempt["validator_status"] = "contract_violation"
                return _failure(state, "prompt1_contract_violation", RuntimeFailureStage.PROMPT1, gateway_attempts=gateway_attempts)
        try:
            validate_prompt1_state(plan, model_state)
            prompt1_attempt["validator_status"] = "ok"
        except ContractError as first_violation:
            prompt1_attempt["validator_status"] = "contract_violation"
            violation_code = _prompt1_violation_code(first_violation)
            retry = getattr(self._prompt1, "retry", None)
            if not callable(retry):
                return _failure(
                    state, "prompt1_contract_violation", RuntimeFailureStage.PROMPT1,
                    gateway_attempts=gateway_attempts,
                )
            try:
                gateway_result = await retry(user_text, model_state, violation_code)
            except Exception:
                gateway_attempts.append(_gateway_attempt("v6_search_agent", gateway_status="error", parse_status="missing", validator_status="missing"))
                return _failure(state, "provider_failure", RuntimeFailureStage.PROMPT1, gateway_attempts=gateway_attempts)
            prompt1_attempt = _gateway_attempt("v6_search_agent", gateway_status="completed")
            if getattr(gateway_result, "tool_trace", None) is not None:
                prompt1_attempt["gateway_task_id_present"] = True
            gateway_attempts.append(prompt1_attempt)
            try:
                plan = parse_prompt1(gateway_result.output)
                prompt1_attempt["parse_status"] = "ok"
            except ContractError:
                prompt1_attempt.update(parse_status="invalid_json", validator_status="missing")
                return _failure(state, "prompt1_contract_violation", RuntimeFailureStage.PROMPT1, gateway_attempts=gateway_attempts)
            try:
                validate_prompt1_state(plan, model_state)
                prompt1_attempt["validator_status"] = "ok"
            except ContractError:
                prompt1_attempt["validator_status"] = "contract_violation"
                return _failure(state, "prompt1_contract_violation", RuntimeFailureStage.PROMPT1, gateway_attempts=gateway_attempts)
        plan = _repair_ambiguous_operator_contact(plan, user_text, flow_state)
        plan = _force_exact_detail_plan(plan, exact_detail)
        plan = _force_consent_clarification(plan, resolution, state, exact_detail)
        try:
            validate_prompt1_state(plan, model_state)
        except ContractError:
            return _failure(
                state, "prompt1_contract_violation", RuntimeFailureStage.PROMPT1,
                gateway_attempts=gateway_attempts,
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
                    state, "provider_failure", RuntimeFailureStage.STATE, plan=plan,
                    gateway_attempts=gateway_attempts,
                )
            return _with_runtime_summary(_checked(RuntimeResult(
                RuntimeStatus.COMPLETED,
                next_state,
                text="На какой номер вам позвонить?",
                plan=plan,
                evidence=evidence,
            )), gateway_attempts, action=plan.action.value)
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
                state, "mcp_contract_violation", RuntimeFailureStage.MCP, plan=plan,
                gateway_attempts=gateway_attempts,
            )
        except Exception:
            return _failure(
                state, "provider_failure", RuntimeFailureStage.MCP, plan=plan,
                gateway_attempts=gateway_attempts,
            )
        prompt2_attempt = _gateway_attempt("v6_answer_writer", gateway_status="unknown", parse_status="missing", validator_status="missing")
        gateway_attempts.append(prompt2_attempt)
        try:
            raw_prompt2 = await self._prompt2.run(user_text, model_state, plan, evidence)
            prompt2_attempt["gateway_status"] = "completed"
        except Exception:
            prompt2_attempt["gateway_status"] = "error"
            raw_prompt2 = None
        if raw_prompt2 is not None:
            try:
                text = parse_prompt2(raw_prompt2, evidence)
                prompt2_attempt["parse_status"] = "ok"
                prompt2_attempt["validator_status"] = "ok"
            except Exception:
                prompt2_attempt["parse_status"] = "invalid_json"
        if raw_prompt2 is None or prompt2_attempt["parse_status"] != "ok":
            try:
                retry_attempt = _gateway_attempt("v6_answer_writer", gateway_status="unknown", parse_status="missing", validator_status="missing")
                gateway_attempts.append(retry_attempt)
                retry = getattr(self._prompt2, "retry", None)
                raw_prompt2 = await (retry(user_text, model_state, plan, evidence) if callable(retry) else self._prompt2.run(user_text, model_state, plan, evidence))
                retry_attempt["gateway_status"] = "completed"
            except Exception:
                retry_attempt["gateway_status"] = "error"
                return _failure(
                    state, "provider_failure", RuntimeFailureStage.PROMPT2,
                    plan=plan, evidence=evidence,
                    gateway_attempts=gateway_attempts,
                )
            try:
                text = parse_prompt2(raw_prompt2, evidence)
                retry_attempt["parse_status"] = "ok"
                retry_attempt["validator_status"] = "ok"
            except Exception:
                retry_attempt["parse_status"] = "invalid_json"
                return _failure(
                    state, "provider_failure", RuntimeFailureStage.PROMPT2,
                    plan=plan, evidence=evidence,
                    gateway_attempts=gateway_attempts,
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
                gateway_attempts=gateway_attempts,
            )
        return _with_runtime_summary(_checked(RuntimeResult(
            RuntimeStatus.COMPLETED,
            next_state,
            text=text,
            plan=plan,
            evidence=evidence,
        )), gateway_attempts, action=plan.action.value)


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
    flow_state = _followup_flow_state(state, resolution, exact_detail)
    model_state = _model_state_projection(flow_state, exact_detail)

    try:
        plan = parse_prompt1(prompt1(user_text, model_state))
        validate_prompt1_state(plan, model_state)
    except ContractError:
        return _failure(state, "prompt1_contract_violation", RuntimeFailureStage.PROMPT1)
    except Exception:
        return _failure(state, "provider_failure", RuntimeFailureStage.PROMPT1)
    plan = _repair_ambiguous_operator_contact(plan, user_text, flow_state)
    plan = _force_exact_detail_plan(plan, exact_detail)
    plan = _force_consent_clarification(plan, resolution, state, exact_detail)
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


def _followup_flow_state(
    state: V6State,
    resolution: Any,
    exact_detail: Mapping[str, Any] | None,
) -> V6State:
    if exact_detail is not None:
        subject_ref = exact_detail.get("subject_ref")
        selected = subject_ref if subject_ref in state.option_refs else state.selected_option_ref
        return replace(state, selected_option_ref=selected, pending_interaction=None)
    pending = state.pending_interaction
    if resolution.kind is FollowupKind.ACCEPT and pending is not None \
            and pending.kind == "selection" and len(pending.subject_refs) > 1:
        context = dict(state.safe_context)
        context["followup_clarification"] = {
            "reason": "ambiguous_consent",
            "subject_count": len(pending.subject_refs),
        }
        return replace(state, safe_context=context)
    return state


def _force_exact_detail_plan(
    plan: Prompt1Result,
    exact_detail: Mapping[str, Any] | None,
) -> Prompt1Result:
    """Retain named-object mode for a typed exact follow-up omitted by Prompt 1."""
    if exact_detail is None:
        return plan
    constraints = exact_detail.get("lot_constraints")
    params = dict(constraints) if isinstance(constraints, Mapping) else {}
    params.update(plan.params)
    params.update({"search_mode": "named_object", "count": 1})
    return replace(plan, params=params)


def _force_consent_clarification(
    plan: Prompt1Result,
    resolution: Any,
    state: V6State,
    exact_detail: Mapping[str, Any] | None,
) -> Prompt1Result:
    """Recover when accepted consent has no bounded actionable subject."""
    pending = state.pending_interaction
    if resolution.kind is not FollowupKind.ACCEPT or pending is None or exact_detail is not None:
        return plan
    if pending.kind == "selection" and len(pending.subject_refs) > 1:
        question = "Какой из вариантов хотите рассмотреть подробнее?"
    elif pending.kind == "offer" and len(pending.subject_refs) == 1 \
            and pending.accept_action == "normal_prompt1":
        question = "Что именно хотите уточнить по этому ЖК?"
    else:
        return plan
    return replace(
        plan,
        action=SearchAction.RECOVER_DIALOGUE,
        target=SearchTarget.NONE,
        search_policy=SearchPolicy.FORBIDDEN,
        clarification_question=question,
        response="",
        facts=(),
        near=(),
        missing=(),
        params={},
    )


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
    gateway_attempts: list[dict[str, Any]] | None = None,
) -> RuntimeResult:
    result = _checked(RuntimeResult(
        RuntimeStatus.FAILED,
        state,
        failure_code=code,
        failure_stage=stage,
        plan=plan,
        evidence=evidence,
    ))
    return _with_runtime_summary(result, gateway_attempts or (), action=plan.action.value if plan else "unknown")


def _gateway_attempt(
    payload_stage: str,
    *,
    gateway_status: str | None = None,
    parse_status: str | None = None,
    validator_status: str | None = None,
) -> dict[str, Any]:
    attempt: dict[str, Any] = {"stage": "gateway_attempt", "_payload_stage": payload_stage, "call_attempted": True}
    if gateway_status is not None:
        attempt["gateway_status"] = gateway_status
    if parse_status is not None:
        attempt["parse_status"] = parse_status
    if validator_status is not None:
        attempt["validator_status"] = validator_status
    return attempt


def _with_runtime_summary(
    result: RuntimeResult,
    attempts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    action: str,
) -> RuntimeResult:
    if not attempts:
        return result
    return replace(result, meta={"trace": {"runtime_summary": {
        "stage": "v6_runtime",
        "action": action,
        "call_counts": {"gateway_attempts": min(len(attempts), 5)},
        "gateway_attempt_details": [dict(item) for item in attempts[:5]],
    }}})
