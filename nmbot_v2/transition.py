from __future__ import annotations

from dataclasses import dataclass

from typing import Any

from .contracts import ExecutableTurn, IntentGoal, IntentPlanV3, SemanticPlan, Stage, TurnAction
from .fact_context import ALLOWED_FACTS
from .scenario_recipes import transition_for_reply
from .semantic_planner import validate_intent_plan_v3
from .state import ConversationState


@dataclass(frozen=True)
class TransitionDecision:
    stage: Stage
    action: TurnAction
    accepted: bool = True
    error_code: str | None = None


_SEARCH_OPS = {"search", "new_search", "refine_search", "expand_more", "lookup_object"}
_CURRENT_OPS = {"current_options", "answer_current_options", "compare_current", "answer_open_question"}
_SELECT_OPS = {"select_option", "selected_object"}
_FINANCE_OPS = {"financing", "clarify_financing"}
_OPERATOR_OPS = {"operator", "operator_handoff"}
_FREEFORM_OPS = {"freeform", "conversation"}


GOAL_TRANSITIONS: dict[IntentGoal, tuple[Stage, TurnAction]] = {
    IntentGoal.NEW_SEARCH: (Stage.FIRST_LIST, TurnAction.SEARCH),
    IntentGoal.REFINE_SEARCH: (Stage.REFINEMENT, TurnAction.SEARCH),
    IntentGoal.EXPAND_SEARCH: (Stage.REFINEMENT, TurnAction.SEARCH),
    IntentGoal.LOOKUP_OBJECT: (Stage.REFINEMENT, TurnAction.SEARCH),
    IntentGoal.ANSWER_CURRENT: (Stage.CURRENT_OPTIONS, TurnAction.ANSWER_FROM_CURRENT_OPTIONS),
    IntentGoal.COMPARE_CURRENT: (Stage.CURRENT_OPTIONS, TurnAction.ANSWER_FROM_CURRENT_OPTIONS),
    IntentGoal.RECOMMEND_CURRENT: (Stage.CURRENT_OPTIONS, TurnAction.ANSWER_FROM_CURRENT_OPTIONS),
    IntentGoal.ANSWER_OPEN_QUESTION: (Stage.CURRENT_OPTIONS, TurnAction.ANSWER_FROM_CURRENT_OPTIONS),
    IntentGoal.ANSWER_SELECTED: (Stage.SELECTED_OBJECT, TurnAction.ANSWER_SELECTED_OPTION),
    IntentGoal.OPERATOR: (Stage.OPERATOR_HANDOFF, TurnAction.OFFER_OPERATOR),
    IntentGoal.CLARIFY: (Stage.FREEFORM, TurnAction.FREEFORM),
    IntentGoal.RESUME_PENDING: (Stage.FREEFORM, TurnAction.FREEFORM),
    IntentGoal.OFF_TOPIC: (Stage.OFF_TOPIC, TurnAction.ANSWER_OFF_TOPIC),
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
    "invalid_followup_outcome",
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
    "invalid_comparison_option_names",
    "invalid_comparison_options_scope",
    "comparison_option_not_visible",
    "comparison_option_fields_conflict",
    "followup_outcome_without_pending",
    "followup_outcome_not_allowed",
}

_SAFE_TRANSITION_ERROR_CODES = {
    "selected_option_not_in_visible_list",
    "missing_named_reference",
    "malformed_operation",
}


def derive_transition_v3(plan: IntentPlanV3, state: ConversationState) -> TransitionDecision:
    goal = plan.goal

    pending_transition = transition_for_reply(state.pending_followup, plan.followup_outcome)
    if pending_transition:
        if pending_transition.requires_pending_action and state.pending_action is None:
            fallback = pending_transition.missing_pending_action_transition
            if fallback is not None:
                return TransitionDecision(fallback.stage, fallback.action)
        return TransitionDecision(pending_transition.stage, pending_transition.action)

    if goal == IntentGoal.ANSWER_SELECTED and not state.find_visible_option(plan.selected_option_name):
        return TransitionDecision(
            Stage.ERROR,
            TurnAction.SAFE_ERROR,
            accepted=False,
            error_code="selected_option_not_in_visible_list",
        )

    if goal == IntentGoal.LOOKUP_OBJECT and not plan.named_object_reference:
        return TransitionDecision(
            Stage.ERROR,
            TurnAction.SAFE_ERROR,
            accepted=False,
            error_code="missing_named_reference",
        )

    if goal == IntentGoal.OPERATOR:
        if plan.operator_consent is True:
            return TransitionDecision(Stage.OPERATOR_HANDOFF, TurnAction.ACCEPT_OPERATOR)
        if plan.operator_consent is False:
            return TransitionDecision(Stage.OPERATOR_DECLINED, TurnAction.DECLINE_OPERATOR)
        return TransitionDecision(Stage.OPERATOR_HANDOFF, TurnAction.OFFER_OPERATOR)

    transition = GOAL_TRANSITIONS.get(goal)
    if transition is None:
        return TransitionDecision(Stage.ERROR, TurnAction.SAFE_ERROR, accepted=False, error_code="malformed_operation")
    return TransitionDecision(*transition)


def compile_executable_turn_v3(
    raw_or_plan: Any,
    state: ConversationState,
    *,
    query_text: str = "",
    allowed_facts: tuple[str, ...] | list[str] | set[str] = ALLOWED_FACTS,
) -> ExecutableTurn:
    """Validate IntentPlanV3 and derive its transition exactly once."""

    validation = validate_intent_plan_v3(raw_or_plan, state, allowed_facts=allowed_facts)
    validation_meta = {
        "ok": validation.ok,
        "errors": list(validation.errors),
        "repairable": validation.repairable,
    }
    if not validation.ok or validation.plan is None:
        return _safe_executable_turn_v3(
            reason="validation_failed:" + ",".join(validation.errors),
            validation_meta=validation_meta,
            intent_transition_meta=_intent_transition_diagnostic(
                plan=validation.plan,
                validation_meta=validation_meta,
                transition_meta=None,
                fallback_used=True,
            ),
            clarification=str(getattr(raw_or_plan, "clarification", "") or (raw_or_plan.get("clarification") if isinstance(raw_or_plan, dict) else "") or "").strip() or None,
            query_text=query_text,
        )

    plan = validation.plan
    decision = derive_transition_v3(plan, state)
    transition_meta = _transition_decision_to_dict(decision)
    if not decision.accepted:
        return _safe_executable_turn_v3(
            reason="transition_rejected:" + str(decision.error_code or "unknown"),
            validation_meta=validation_meta,
            transition_meta=transition_meta,
            intent_transition_meta=_intent_transition_diagnostic(
                plan=plan,
                validation_meta=validation_meta,
                transition_meta=transition_meta,
                fallback_used=True,
            ),
            clarification=plan.clarification,
            query_text=query_text,
            error_code=decision.error_code,
        )

    requested_facts = tuple(fact for fact in plan.requested_facts if fact in set(allowed_facts))
    viewpoint = str(plan.viewpoint or "unchanged").strip()
    facets = [] if viewpoint == "unchanged" else [viewpoint]
    intent = "mortgage" if viewpoint == "financing" else (viewpoint if viewpoint != "unchanged" else None)
    selected = plan.selected_option_name
    named = plan.named_object_reference
    return ExecutableTurn(
        goal=plan.goal,
        stage=decision.stage,
        action=decision.action,
        accepted=decision.accepted,
        error_code=decision.error_code,
        query_text=_redacted_query(query_text or plan.query_text),
        viewpoint=viewpoint,
        intent=intent,
        constraints_delta=dict(plan.constraints_delta),
        reference=named or selected,
        selected_option_name=selected,
        named_object_reference=named,
        comparison_option_names=plan.comparison_option_names,
        scope="one" if selected or named else ("all" if plan.goal in {IntentGoal.ANSWER_CURRENT, IntentGoal.COMPARE_CURRENT, IntentGoal.RECOMMEND_CURRENT, IntentGoal.ANSWER_OPEN_QUESTION} else "unknown"),
        operator_consent=plan.operator_consent,
        explicit_operator_request=plan.explicit_operator_request,
        operator_reason="explicit_operator_request" if plan.explicit_operator_request else None,
        followup_outcome=plan.followup_outcome or ("resume_contact" if plan.goal == IntentGoal.RESUME_PENDING else None),
        requested_facts=requested_facts,
        facts_needed=requested_facts,
        requires_enrichment=bool(requested_facts and plan.goal == IntentGoal.ANSWER_SELECTED),
        focus_action="switch" if selected or named else "keep",
        domain_relation="off_topic" if plan.goal == IntentGoal.OFF_TOPIC else "in_domain",
        confidence=plan.confidence,
        clarification=plan.clarification,
        facets=facets,
        fresh_search=plan.goal == IntentGoal.EXPAND_SEARCH,
        trace_metadata={
            "intent_plan_v3_validation": validation_meta,
            "intent_plan_v3_transition": transition_meta,
            "intent_transition": _intent_transition_diagnostic(
                plan=plan,
                validation_meta=validation_meta,
                transition_meta=transition_meta,
                fallback_used=False,
            ),
        },
    )


def _safe_executable_turn_v3(
    *,
    reason: str,
    validation_meta: dict[str, Any],
    transition_meta: dict[str, Any] | None = None,
    intent_transition_meta: dict[str, Any] | None = None,
    clarification: str | None = None,
    query_text: str = "",
    error_code: str | None = None,
) -> ExecutableTurn:
    trace_metadata: dict[str, Any] = {
        "intent_plan_v3_validation": validation_meta,
        "intent_plan_v3_adapter": {"fallback_used": True, "reason": reason},
    }
    if transition_meta:
        trace_metadata["intent_plan_v3_transition"] = transition_meta
    if intent_transition_meta:
        trace_metadata["intent_transition"] = intent_transition_meta
    return ExecutableTurn(
        goal=IntentGoal.CLARIFY,
        stage=Stage.ERROR,
        action=TurnAction.SAFE_ERROR,
        accepted=False,
        error_code=error_code or reason.split(":", 1)[0],
        query_text=_redacted_query(query_text),
        scope="all",
        confidence=0.0,
        clarification=clarification,
        trace_metadata=trace_metadata,
    )


def _intent_transition_diagnostic(
    *,
    plan: IntentPlanV3 | None,
    validation_meta: dict[str, Any],
    transition_meta: dict[str, Any] | None,
    fallback_used: bool,
) -> dict[str, Any]:
    goal = plan.goal.value if isinstance(plan, IntentPlanV3) and isinstance(plan.goal, IntentGoal) else None
    validation_ok = validation_meta.get("ok") is True
    raw_errors = validation_meta.get("errors") if isinstance(validation_meta.get("errors"), list) else []
    transition = transition_meta if isinstance(transition_meta, dict) else {}
    transition_error = str(transition.get("error_code") or "")
    return {
        "goal": goal if goal in {item.value for item in IntentGoal} else None,
        "intent_validation": "accepted" if validation_ok else "failed",
        "validation_error_codes": [
            str(code)
            for code in raw_errors
            if str(code) in _SAFE_INTENT_VALIDATION_ERROR_CODES
        ][:8],
        "transition": {
            "accepted": bool(transition.get("accepted")) if transition else False,
            "error_code": transition_error if transition_error in _SAFE_TRANSITION_ERROR_CODES else None,
        },
        "fallback_used": bool(fallback_used),
    }


def _transition_decision_to_dict(decision: TransitionDecision) -> dict[str, Any]:
    return {
        "stage": decision.stage.value,
        "action": decision.action.value,
        "accepted": decision.accepted,
        "error_code": decision.error_code,
    }


def _redacted_query(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:500] or None

def derive_transition(plan: SemanticPlan, state: ConversationState) -> TransitionDecision:
    op = (plan.operation or "").strip()
    if plan.domain_relation == "off_topic" or op == "off_topic":
        return TransitionDecision(Stage.OFF_TOPIC, TurnAction.ANSWER_OFF_TOPIC)
    if op == "reset":
        return TransitionDecision(Stage.RESET, TurnAction.RESET)
    pending_transition = transition_for_reply(state.pending_followup, plan.followup_outcome)
    if pending_transition:
        if pending_transition.requires_pending_action and state.pending_action is None:
            fallback = pending_transition.missing_pending_action_transition
            if fallback is not None:
                return TransitionDecision(fallback.stage, fallback.action)
        return TransitionDecision(pending_transition.stage, pending_transition.action)
    if op in _SEARCH_OPS:
        return TransitionDecision(
            Stage.REFINEMENT if state.visible_options or state.params else Stage.FIRST_LIST,
            TurnAction.SEARCH,
        )
    if op in _CURRENT_OPS:
        return TransitionDecision(Stage.CURRENT_OPTIONS, TurnAction.ANSWER_FROM_CURRENT_OPTIONS)
    if op in _SELECT_OPS:
        if not plan.selected_option_name or not state.find_visible_option(plan.selected_option_name):
            return TransitionDecision(Stage.ERROR, TurnAction.SAFE_ERROR, accepted=False, error_code="selected_option_not_in_visible_list")
        return TransitionDecision(Stage.SELECTED_OBJECT, TurnAction.ANSWER_SELECTED_OPTION)
    if op in _FINANCE_OPS:
        return TransitionDecision(Stage.FINANCING_CLARIFICATION, TurnAction.CLARIFY_FINANCING)
    if op in _OPERATOR_OPS:
        if state.operator_declined and plan.operator_consent is None and not plan.explicit_operator_request:
            return TransitionDecision(Stage.FREEFORM, TurnAction.FREEFORM)
        if plan.operator_consent is True:
            return TransitionDecision(Stage.OPERATOR_HANDOFF, TurnAction.ACCEPT_OPERATOR)
        if plan.operator_consent is False:
            return TransitionDecision(Stage.OPERATOR_DECLINED, TurnAction.DECLINE_OPERATOR)
        return TransitionDecision(Stage.OPERATOR_HANDOFF, TurnAction.OFFER_OPERATOR)
    if op in _FREEFORM_OPS:
        return TransitionDecision(Stage.FREEFORM, TurnAction.FREEFORM)
    return TransitionDecision(Stage.ERROR, TurnAction.SAFE_ERROR, accepted=False, error_code="malformed_operation")
