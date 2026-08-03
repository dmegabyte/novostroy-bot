"""V3-only goal-to-transition compiler; no runtime wiring occurs here."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .contracts import (
    ExecutableTurnV3,
    IntentGoalV3,
    IntentPlanV3,
    V3_ALLOWED_FACTS,
    V3SemanticAction,
    V3SemanticStage,
    V3PlannerContext,
)
from .semantic_planner import IntentPlanValidationV3, validate_intent_plan_v3


@dataclass(frozen=True)
class TransitionDecisionV3:
    stage: V3SemanticStage
    action: V3SemanticAction
    accepted: bool = True
    error_code: str | None = None


GOAL_TRANSITIONS_V3 = {
    IntentGoalV3.NEW_SEARCH: (V3SemanticStage.FIRST_LIST, V3SemanticAction.SEARCH),
    IntentGoalV3.REFINE_SEARCH: (V3SemanticStage.REFINEMENT, V3SemanticAction.SEARCH),
    IntentGoalV3.EXPAND_SEARCH: (V3SemanticStage.REFINEMENT, V3SemanticAction.SEARCH),
    IntentGoalV3.LOOKUP_OBJECT: (V3SemanticStage.REFINEMENT, V3SemanticAction.SEARCH),
    IntentGoalV3.ANSWER_CURRENT: (V3SemanticStage.CURRENT_OPTIONS, V3SemanticAction.ANSWER_CURRENT),
    IntentGoalV3.COMPARE_CURRENT: (V3SemanticStage.CURRENT_OPTIONS, V3SemanticAction.ANSWER_CURRENT),
    IntentGoalV3.RECOMMEND_CURRENT: (V3SemanticStage.CURRENT_OPTIONS, V3SemanticAction.ANSWER_CURRENT),
    IntentGoalV3.ANSWER_OPEN_QUESTION: (V3SemanticStage.CURRENT_OPTIONS, V3SemanticAction.ANSWER_CURRENT),
    IntentGoalV3.ANSWER_SELECTED: (V3SemanticStage.SELECTED_OBJECT, V3SemanticAction.ANSWER_SELECTED),
    IntentGoalV3.CLARIFY: (V3SemanticStage.FREEFORM, V3SemanticAction.FREEFORM),
    IntentGoalV3.RESUME_PENDING: (V3SemanticStage.FREEFORM, V3SemanticAction.FREEFORM),
    IntentGoalV3.OFF_TOPIC: (V3SemanticStage.OFF_TOPIC, V3SemanticAction.ANSWER_OFF_TOPIC),
}

# This closed table is V3-owned parity for the four legacy pending contracts.
# Pending resolution deliberately precedes the plan goal, matching the source.
_PENDING_TRANSITIONS = {
    ("contact_name", "resume_contact"): (V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.OFFER_OPERATOR, False),
    ("contact_phone", "resume_contact"): (V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.OFFER_OPERATOR, False),
    ("financing_consent", "accept"): (V3SemanticStage.SELECTED_OBJECT, V3SemanticAction.ANSWER_SELECTED, True),
    ("financing_consent", "decline"): (V3SemanticStage.OPERATOR_DECLINED, V3SemanticAction.DECLINE_OPERATOR, False),
    ("financing_consent", "ask_or_clarify"): (V3SemanticStage.FINANCING_CLARIFICATION, V3SemanticAction.CLARIFY_FINANCING, False),
    ("financing_consent", "unexpected"): (V3SemanticStage.FINANCING_CLARIFICATION, V3SemanticAction.CLARIFY_FINANCING, False),
    ("selected_live_fact_consent", "accept"): (V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.ACCEPT_OPERATOR, False),
    ("selected_live_fact_consent", "decline"): (V3SemanticStage.OPERATOR_DECLINED, V3SemanticAction.DECLINE_OPERATOR, False),
    ("selected_live_fact_consent", "ask_or_clarify"): (V3SemanticStage.SELECTED_LIVE_FACT_CLARIFICATION, V3SemanticAction.CLARIFY_SELECTED_LIVE_FACT, False),
    ("selected_live_fact_consent", "unexpected"): (V3SemanticStage.SELECTED_LIVE_FACT_CLARIFICATION, V3SemanticAction.CLARIFY_SELECTED_LIVE_FACT, False),
}
_FINANCING_MISSING_ACTION = TransitionDecisionV3(V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.ACCEPT_OPERATOR)


def derive_transition_v3(plan: IntentPlanV3, context: V3PlannerContext) -> TransitionDecisionV3:
    pending_key = context.pending_followup_key
    outcome = plan.followup_outcome
    # A consent prompt remains the active turn owner until it receives one of
    # its closed outcomes.  Missing output is ambiguity, never implicit
    # consent or a new freeform request.
    if outcome is None and pending_key in {"financing_consent", "selected_live_fact_consent"}:
        outcome = "unexpected"
    if outcome:
        pending = _PENDING_TRANSITIONS.get((pending_key, outcome))
        if pending is not None:
            stage, action, requires_pending_action = pending
            if requires_pending_action and not context.has_pending_action:
                return _FINANCING_MISSING_ACTION
            return TransitionDecisionV3(stage, action)

    if plan.goal == IntentGoalV3.ANSWER_SELECTED and not context.has_visible_option_ref(plan.selected_option_ref):
        return TransitionDecisionV3(V3SemanticStage.ERROR, V3SemanticAction.SAFE_ERROR, False, "selected_option_not_in_visible_list")
    if plan.goal == IntentGoalV3.LOOKUP_OBJECT and not plan.named_object_reference:
        return TransitionDecisionV3(V3SemanticStage.ERROR, V3SemanticAction.SAFE_ERROR, False, "missing_named_reference")
    if plan.goal == IntentGoalV3.OPERATOR:
        if plan.operator_consent is True:
            return TransitionDecisionV3(V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.ACCEPT_OPERATOR)
        if plan.operator_consent is False:
            return TransitionDecisionV3(V3SemanticStage.OPERATOR_DECLINED, V3SemanticAction.DECLINE_OPERATOR)
        return TransitionDecisionV3(V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.OFFER_OPERATOR)

    transition = GOAL_TRANSITIONS_V3.get(plan.goal)
    if transition is None:
        return TransitionDecisionV3(V3SemanticStage.ERROR, V3SemanticAction.SAFE_ERROR, False, "malformed_operation")
    return TransitionDecisionV3(*transition)


def compile_executable_turn_v3(
    raw_or_plan: Any,
    context: V3PlannerContext,
    *,
    query_text: str = "",
    allowed_facts: tuple[str, ...] | list[str] | frozenset[str] = V3_ALLOWED_FACTS,
) -> ExecutableTurnV3:
    validation = validate_intent_plan_v3(raw_or_plan, context, allowed_facts=allowed_facts)
    safe_trace = _safe_trace(validation)
    if not validation.ok or validation.plan is None:
        return ExecutableTurnV3(
            IntentGoalV3.CLARIFY, V3SemanticStage.ERROR, V3SemanticAction.SAFE_ERROR,
            False, "validation_failed", _redact_query(query_text), trace_metadata={"intent_transition": safe_trace},
        )

    decision = derive_transition_v3(validation.plan, context)
    safe_trace["transition"] = {"accepted": decision.accepted, "error_code": decision.error_code}
    if not decision.accepted:
        safe_trace["fallback_used"] = True
        return ExecutableTurnV3(
            IntentGoalV3.CLARIFY, V3SemanticStage.ERROR, V3SemanticAction.SAFE_ERROR,
            False, decision.error_code, _redact_query(query_text), trace_metadata={"intent_transition": safe_trace},
        )

    plan = validation.plan
    return ExecutableTurnV3(
        plan.goal, decision.stage, decision.action,
        query_text=_redact_query(query_text or plan.query_text),
        selected_option_name=plan.selected_option_name,
        named_object_reference=plan.named_object_reference,
        comparison_option_names=plan.comparison_option_names,
        requested_facts=plan.requested_facts,
        followup_outcome=plan.followup_outcome,
        trace_metadata={"intent_transition": safe_trace},
    )


def _safe_trace(validation: IntentPlanValidationV3) -> dict[str, Any]:
    return {
        "goal": validation.plan.goal.value if validation.plan is not None else None,
        "intent_validation": "accepted" if validation.ok else "failed",
        "validation_error_codes": list(validation.errors)[:8],
        "transition": {"accepted": False, "error_code": None},
        "fallback_used": not validation.ok,
    }


def _redact_query(value: Any) -> str | None:
    text = str(value or "").strip()[:500]
    if not text:
        return None
    return re.sub(
        r"(?<!\d)(?:(?:\+7|7|8)[\s().-]*(?:\d[\s().-]*){10}|9(?:[\s().-]*\d){9})(?!\d)",
        "[phone]",
        text,
    )
