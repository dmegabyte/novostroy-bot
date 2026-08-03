from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from nmbot_v3.contracts import IntentGoalV3, IntentPlanV3, V3PlannerContext, V3SemanticAction, V3SemanticStage
from nmbot_v3.semantic_planner import validate_intent_plan_v3
from nmbot_v3.transition import GOAL_TRANSITIONS_V3, compile_executable_turn_v3, derive_transition_v3


def raw(**overrides):
    value = {"schema_version": 3, "goal": "recommend_current", "viewpoint": "unchanged", "constraints_delta": {}, "confidence": 1.0}
    value.update(overrides)
    return value


REF_ONE = "550e8400-e29b-41d4-a716-446655440000"
REF_TWO = "550e8400-e29b-41d4-a716-446655440001"


def context(*refs, pending=None, action=False):
    return V3PlannerContext(tuple(refs), pending, action)


@pytest.mark.parametrize(("goal", "stage", "action"), [(goal, *transition) for goal, transition in GOAL_TRANSITIONS_V3.items()])
def test_all_non_operator_goal_transitions(goal, stage, action):
    values = {"goal": goal.value}
    if goal is IntentGoalV3.LOOKUP_OBJECT: values["named_object_reference"] = "ЖК Лучи"
    if goal is IntentGoalV3.ANSWER_SELECTED: values["selected_option_ref"] = REF_ONE
    if goal is IntentGoalV3.CLARIFY: values["clarification"] = "Уточните район"
    turn = compile_executable_turn_v3(raw(**values), context(REF_ONE))
    assert (turn.stage, turn.action, turn.accepted) == (stage, action, True)


@pytest.mark.parametrize(("consent", "stage", "action"), [(None, V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.OFFER_OPERATOR), (True, V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.ACCEPT_OPERATOR), (False, V3SemanticStage.OPERATOR_DECLINED, V3SemanticAction.DECLINE_OPERATOR)])
def test_operator_consent_transition(consent, stage, action):
    decision = derive_transition_v3(IntentPlanV3(**raw(goal="operator", operator_consent=consent)), context())
    assert (decision.stage, decision.action) == (stage, action)


def test_exact_visibility_lookup_and_clarification_rules():
    assert validate_intent_plan_v3(raw(goal="answer_selected", selected_option_ref=REF_TWO), context(REF_ONE)).errors == ("selected_option_not_visible",)
    assert validate_intent_plan_v3(raw(goal="lookup_object"), context()).errors == ("missing_named_reference",)
    assert validate_intent_plan_v3(raw(goal="clarify"), context()).errors == ("missing_clarification",)


def test_requested_facts_comparison_and_pending_scope():
    ctx = context(REF_ONE, REF_TWO, pending="financing_consent")
    assert validate_intent_plan_v3(raw(requested_facts=["parking"]), ctx).ok
    assert validate_intent_plan_v3(raw(requested_facts=["secret"]), ctx).errors == ("invalid_requested_fact",)
    assert validate_intent_plan_v3(raw(goal="compare_current", comparison_option_refs=[REF_ONE, REF_TWO]), ctx).ok
    assert validate_intent_plan_v3(raw(goal="compare_current", comparison_option_refs=[REF_ONE, "550e8400-e29b-41d4-a716-446655440002"]), ctx).errors == ("comparison_option_not_visible",)
    assert validate_intent_plan_v3(raw(goal="compare_current", selected_option_ref=REF_ONE, comparison_option_refs=[REF_ONE, REF_TWO]), ctx).errors == ("comparison_option_fields_conflict",)
    assert validate_intent_plan_v3(raw(goal="new_search", comparison_option_refs=[REF_ONE, REF_TWO]), ctx).errors == ("invalid_comparison_options_scope",)
    assert validate_intent_plan_v3(raw(followup_outcome="accept"), context()).errors == ("followup_outcome_without_pending",)
    assert validate_intent_plan_v3(raw(followup_outcome="resume_contact"), ctx).errors == ("followup_outcome_not_allowed",)


@pytest.mark.parametrize(
    ("pending", "outcome", "has_action", "stage", "action"),
    [
        ("contact_name", "resume_contact", False, V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.OFFER_OPERATOR),
        ("contact_phone", "resume_contact", False, V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.OFFER_OPERATOR),
        ("financing_consent", "accept", True, V3SemanticStage.SELECTED_OBJECT, V3SemanticAction.ANSWER_SELECTED),
        ("financing_consent", "accept", False, V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.ACCEPT_OPERATOR),
        ("financing_consent", "decline", False, V3SemanticStage.OPERATOR_DECLINED, V3SemanticAction.DECLINE_OPERATOR),
        ("financing_consent", "ask_or_clarify", False, V3SemanticStage.FINANCING_CLARIFICATION, V3SemanticAction.CLARIFY_FINANCING),
        ("financing_consent", "unexpected", False, V3SemanticStage.FINANCING_CLARIFICATION, V3SemanticAction.CLARIFY_FINANCING),
        ("selected_live_fact_consent", "accept", False, V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.ACCEPT_OPERATOR),
        ("selected_live_fact_consent", "accept", True, V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.ACCEPT_OPERATOR),
        ("selected_live_fact_consent", "decline", False, V3SemanticStage.OPERATOR_DECLINED, V3SemanticAction.DECLINE_OPERATOR),
        ("selected_live_fact_consent", "ask_or_clarify", False, V3SemanticStage.SELECTED_LIVE_FACT_CLARIFICATION, V3SemanticAction.CLARIFY_SELECTED_LIVE_FACT),
        ("selected_live_fact_consent", "unexpected", False, V3SemanticStage.SELECTED_LIVE_FACT_CLARIFICATION, V3SemanticAction.CLARIFY_SELECTED_LIVE_FACT),
    ],
)
def test_every_pending_outcome_has_source_parity_transition(pending, outcome, has_action, stage, action):
    turn = compile_executable_turn_v3(
        raw(goal="answer_selected", selected_option_ref=REF_ONE, followup_outcome=outcome),
        context(REF_ONE, pending=pending, action=has_action),
    )
    assert (turn.stage, turn.action, turn.accepted, turn.error_code) == (stage, action, True, None)


def test_parse_is_safe_immutable_and_trace_has_no_query_or_phone():
    source = raw(goal="nope", query_text="secret +7 999 123-45-67")
    before = deepcopy(source)
    result = validate_intent_plan_v3(source, context())
    assert result.errors == ("invalid_goal",) and result.repairable
    assert source == before
    turn = compile_executable_turn_v3(raw(requested_facts=["secret"]), context(), query_text="compare +7 999 123-45-67")
    assert turn.query_text == "compare [phone]"
    trace = str(turn.trace_metadata)
    assert "compare" not in trace and "+7" not in trace and "query_text" not in trace
    assert turn.trace_metadata["intent_transition"] == {
        "goal": "recommend_current",
        "intent_validation": "failed",
        "validation_error_codes": ("invalid_requested_fact",),
        "transition": {"accepted": False, "error_code": None},
        "fallback_used": True,
    }


@pytest.mark.parametrize("schema_version", [True, 3.0, "3"])
def test_schema_version_rejects_non_integer_equivalents(schema_version):
    result = validate_intent_plan_v3(raw(schema_version=schema_version), context())
    assert result.errors == ("invalid_schema_version",)


def test_nested_contract_mappings_are_immutable():
    plan = IntentPlanV3(**raw(constraints_delta={"hard": {"rooms": [1, 2]}}))
    with pytest.raises(TypeError):
        plan.constraints_delta["hard"] = {}
    with pytest.raises(TypeError):
        plan.constraints_delta["hard"]["rooms"] = ()

    turn = compile_executable_turn_v3(raw(), context())
    with pytest.raises(TypeError):
        turn.trace_metadata["intent_transition"] = {}
    with pytest.raises(TypeError):
        turn.trace_metadata["intent_transition"]["transition"] = {}


def test_closed_context_and_runtime_shell_remain_unwired():
    for malformed in (("ЖК Лучи", None), ("ЖК Лучи", 7), ("ЖК Лучи", "ЖК Лучи")):
        with pytest.raises(Exception):
            V3PlannerContext(malformed)
    with pytest.raises(Exception):
        V3PlannerContext(pending_followup_key="operator_offer")
    with pytest.raises(Exception):
        V3PlannerContext(pending_followup_key=7)
    runtime = Path("nmbot_v3/runtime.py").read_text(encoding="utf-8")
    assert "compile_executable_turn_v3" not in runtime
