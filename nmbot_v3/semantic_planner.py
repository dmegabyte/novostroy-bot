"""V3 semantic-plan parsing and validation; intentionally self-contained."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import IntentGoalV3, IntentPlanV3, V3_ALLOWED_FACTS, V3_FOLLOWUP_OUTCOMES, V3_VIEWPOINTS, V3PlannerContext


@dataclass(frozen=True)
class IntentPlanValidationV3:
    ok: bool
    plan: IntentPlanV3 | None
    errors: tuple[str, ...]
    repairable: bool


_OPERATOR_ALLOWED = {IntentGoalV3.OPERATOR, IntentGoalV3.RESUME_PENDING}
# The V3 pending protocol is deliberately closed: unknown keys do not acquire behavior.
_PENDING_OUTCOMES = {
    "contact_name": frozenset({"resume_contact"}),
    "contact_phone": frozenset({"resume_contact"}),
    "financing_consent": frozenset({"accept", "decline", "ask_or_clarify", "unexpected"}),
    "selected_live_fact_consent": frozenset({"accept", "decline", "ask_or_clarify", "unexpected"}),
}


def validate_intent_plan_v3(raw_or_plan: Any, context: V3PlannerContext, *, allowed_facts: frozenset[str] | tuple[str, ...] | list[str] = V3_ALLOWED_FACTS) -> IntentPlanValidationV3:
    try:
        plan = raw_or_plan if isinstance(raw_or_plan, IntentPlanV3) else IntentPlanV3.from_dict(raw_or_plan) if isinstance(raw_or_plan, Mapping) else None
    except Exception as exc:
        return IntentPlanValidationV3(False, None, (_parse_code(exc),), True)
    if plan is None:
        return IntentPlanValidationV3(False, None, ("invalid_shape",), True)
    errors: list[str] = []
    facts = frozenset(str(item).strip() for item in allowed_facts)
    if any(fact not in facts for fact in plan.requested_facts): errors.append("invalid_requested_fact")
    if plan.viewpoint not in V3_VIEWPOINTS: errors.append("invalid_viewpoint")
    if plan.comparison_option_names:
        errors.append("comparison_option_names_not_supported")
    if plan.selected_option_name is not None:
        errors.append("selected_option_name_not_supported")
    if plan.comparison_option_refs:
        if plan.goal != IntentGoalV3.COMPARE_CURRENT: errors.append("invalid_comparison_options_scope")
        else:
            if plan.selected_option_ref or plan.named_object_reference: errors.append("comparison_option_fields_conflict")
            if any(not context.has_visible_option_ref(reference) for reference in plan.comparison_option_refs): errors.append("comparison_option_not_visible")
    if plan.goal == IntentGoalV3.ANSWER_SELECTED:
        if not context.has_visible_option_ref(plan.selected_option_ref): errors.append("selected_option_not_visible")
    elif plan.selected_option_ref and not plan.comparison_option_refs:
        errors.append("invalid_selected_option_scope")
    if plan.goal == IntentGoalV3.LOOKUP_OBJECT:
        if not plan.named_object_reference: errors.append("missing_named_reference")
    elif plan.named_object_reference: errors.append("invalid_named_reference_scope")
    if plan.goal == IntentGoalV3.CLARIFY:
        if not plan.clarification: errors.append("missing_clarification")
    elif plan.clarification: errors.append("clarification_on_non_clarify")
    if plan.operator_consent is not None and plan.goal not in _OPERATOR_ALLOWED: errors.append("invalid_operator_consent_scope")
    if plan.followup_outcome is not None:
        allowed = _PENDING_OUTCOMES.get(context.pending_followup_key or "")
        if allowed is None: errors.append("followup_outcome_without_pending")
        elif plan.followup_outcome not in allowed: errors.append("followup_outcome_not_allowed")
    return IntentPlanValidationV3(not errors, plan, tuple(sorted(set(errors))), False)


def _parse_code(exc: BaseException) -> str:
    code = str(exc)
    known = {"unknown_field", "invalid_schema_version", "invalid_intentgoalv3", "missing_viewpoint", "invalid_constraints_delta", "invalid_operator_consent", "invalid_explicit_operator_request", "invalid_followup_outcome", "invalid_confidence", "invalid_comparison_option_names"}
    if code == "invalid_intentgoalv3": return "invalid_goal"
    return code if code in known else "invalid_schema"
