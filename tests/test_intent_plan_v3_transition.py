from __future__ import annotations

import pytest

from nmbot_v2.contracts import IntentGoal, IntentPlanV3, OptionCard, Stage, TurnAction
from nmbot_v2.state import ConversationState
from nmbot_v2.transition import compile_executable_turn_v3, derive_transition_v3


def _plan(goal: IntentGoal, **overrides) -> IntentPlanV3:
    data = {
        "schema_version": 3,
        "goal": goal,
        "viewpoint": "unchanged",
    }
    data.update(overrides)
    return IntentPlanV3(**data)


def _state(*names: str, pending_followup: str | None = None, active_topic: str | None = None) -> ConversationState:
    return ConversationState(
        visible_options=tuple(OptionCard(name=name) for name in names),
        pending_followup=pending_followup,
        active_topic=active_topic,
    )


@pytest.mark.parametrize(
    ("goal", "overrides", "expected_stage", "expected_action"),
    [
        (IntentGoal.NEW_SEARCH, {}, Stage.FIRST_LIST, TurnAction.SEARCH),
        (IntentGoal.REFINE_SEARCH, {}, Stage.REFINEMENT, TurnAction.SEARCH),
        (IntentGoal.EXPAND_SEARCH, {}, Stage.REFINEMENT, TurnAction.SEARCH),
        (IntentGoal.LOOKUP_OBJECT, {"named_object_reference": "ЖК Лучи"}, Stage.REFINEMENT, TurnAction.SEARCH),
        (IntentGoal.ANSWER_CURRENT, {}, Stage.CURRENT_OPTIONS, TurnAction.ANSWER_FROM_CURRENT_OPTIONS),
        (IntentGoal.COMPARE_CURRENT, {}, Stage.CURRENT_OPTIONS, TurnAction.ANSWER_FROM_CURRENT_OPTIONS),
        (IntentGoal.RECOMMEND_CURRENT, {}, Stage.CURRENT_OPTIONS, TurnAction.ANSWER_FROM_CURRENT_OPTIONS),
        (IntentGoal.ANSWER_SELECTED, {"selected_option_name": "ЖК Лучи"}, Stage.SELECTED_OBJECT, TurnAction.ANSWER_SELECTED_OPTION),
        (IntentGoal.OPERATOR, {}, Stage.OPERATOR_HANDOFF, TurnAction.OFFER_OPERATOR),
        (IntentGoal.CLARIFY, {"clarification": "Уточните район"}, Stage.FREEFORM, TurnAction.FREEFORM),
        (IntentGoal.RESUME_PENDING, {}, Stage.FREEFORM, TurnAction.FREEFORM),
        (IntentGoal.OFF_TOPIC, {}, Stage.OFF_TOPIC, TurnAction.ANSWER_OFF_TOPIC),
    ],
)
def test_intent_plan_v3_goal_transition_table(goal, overrides, expected_stage, expected_action) -> None:
    decision = derive_transition_v3(_plan(goal, **overrides), _state("ЖК Лучи"))

    assert decision.stage is expected_stage
    assert decision.action is expected_action
    assert decision.accepted is True
    assert decision.error_code is None


def test_answer_selected_typo_is_rejected_even_when_similar_visible_name_exists() -> None:
    decision = derive_transition_v3(
        _plan(IntentGoal.ANSWER_SELECTED, selected_option_name="ЖК Лучи!"),
        _state("ЖК Лучи"),
    )

    assert decision.stage is Stage.ERROR
    assert decision.action is TurnAction.SAFE_ERROR
    assert decision.accepted is False
    assert decision.error_code == "selected_option_not_in_visible_list"


def test_lookup_object_missing_named_reference_is_rejected() -> None:
    decision = derive_transition_v3(_plan(IntentGoal.LOOKUP_OBJECT), _state())

    assert decision.stage is Stage.ERROR
    assert decision.action is TurnAction.SAFE_ERROR
    assert decision.accepted is False
    assert decision.error_code == "missing_named_reference"


@pytest.mark.parametrize(
    ("consent", "expected_stage", "expected_action"),
    [
        (True, Stage.OPERATOR_HANDOFF, TurnAction.ACCEPT_OPERATOR),
        (False, Stage.OPERATOR_DECLINED, TurnAction.DECLINE_OPERATOR),
        (None, Stage.OPERATOR_HANDOFF, TurnAction.OFFER_OPERATOR),
    ],
)
def test_operator_consent_controls_only_operator_goal(consent, expected_stage, expected_action) -> None:
    decision = derive_transition_v3(_plan(IntentGoal.OPERATOR, operator_consent=consent), _state())

    assert decision.stage is expected_stage
    assert decision.action is expected_action
    assert decision.accepted is True


def test_operator_consent_does_not_rewrite_resume_pending_goal() -> None:
    decision = derive_transition_v3(_plan(IntentGoal.RESUME_PENDING, operator_consent=True), _state())

    assert decision.stage is Stage.FREEFORM
    assert decision.action is TurnAction.FREEFORM
    assert decision.accepted is True


def test_pending_followup_does_not_rewrite_recommend_current() -> None:
    decision = derive_transition_v3(
        _plan(IntentGoal.RECOMMEND_CURRENT),
        _state("ЖК Лучи", pending_followup="financing_consent"),
    )

    assert decision.stage is Stage.CURRENT_OPTIONS
    assert decision.action is TurnAction.ANSWER_FROM_CURRENT_OPTIONS


def test_pending_financing_consent_keeps_operator_goal_in_clarification_loop() -> None:
    decision = derive_transition_v3(
        _plan(IntentGoal.OPERATOR),
        _state("ЖК Лучи", pending_followup="financing_consent"),
    )

    assert decision.stage is Stage.FINANCING_CLARIFICATION
    assert decision.action is TurnAction.CLARIFY_FINANCING


@pytest.mark.parametrize("pending", ("financing_consent", "selected_live_fact_consent"))
@pytest.mark.parametrize("goal", (IntentGoal.OPERATOR, IntentGoal.CLARIFY, IntentGoal.RESUME_PENDING))
def test_pending_owned_goals_without_outcome_stay_in_consent_loop(pending, goal) -> None:
    decision = derive_transition_v3(
        _plan(goal),
        _state("ЖК Лучи", pending_followup=pending),
    )

    expected = (
        (Stage.FINANCING_CLARIFICATION, TurnAction.CLARIFY_FINANCING)
        if pending == "financing_consent"
        else (Stage.SELECTED_LIVE_FACT_CLARIFICATION, TurnAction.CLARIFY_SELECTED_LIVE_FACT)
    )
    assert (decision.stage, decision.action) == expected


@pytest.mark.parametrize("goal", [IntentGoal.RECOMMEND_CURRENT, IntentGoal.REFINE_SEARCH])
def test_visible_options_and_active_topic_do_not_reroute_stable_goals(goal) -> None:
    state = ConversationState(
        visible_options=(OptionCard(name="ЖК Лучи"),),
        pending_followup="contact_name",
        active_topic="financing",
        params={"rooms": 2},
    )

    decision = derive_transition_v3(_plan(goal), state)

    if goal == IntentGoal.RECOMMEND_CURRENT:
        assert decision.stage is Stage.CURRENT_OPTIONS
        assert decision.action is TurnAction.ANSWER_FROM_CURRENT_OPTIONS
    else:
        assert decision.stage is Stage.REFINEMENT
        assert decision.action is TurnAction.SEARCH


def test_invalid_compare_shaped_intent_plan_v3_trace_contains_only_safe_codes() -> None:
    turn = compile_executable_turn_v3(
        {
            "schema_version": 3,
            "goal": "compare_current",
            "viewpoint": "unchanged",
            "selected_option_name": "Томилинский бульвар SECRET +7 999 123-45-67",
            "requested_facts": ["secret_fact"],
            "query_text": "сравни с томилиским бульваром",
            "confidence": 0.7,
        },
        _state("ЖК Лучи"),
        query_text="сравни с томилиским бульваром",
    )

    diagnostic = turn.trace_metadata["intent_transition"]
    assert diagnostic == {
        "goal": "compare_current",
        "intent_validation": "failed",
        "validation_error_codes": ["invalid_requested_fact"],
        "transition": {"accepted": False, "error_code": None},
        "fallback_used": True,
    }
    dumped = str(diagnostic)
    for forbidden in ["selected_option_name", "constraints_delta", "requested_facts", "confidence", "query_text", "Томилинский", "+7 999", "clarification"]:
        assert forbidden not in dumped


def test_accepted_compare_current_intent_plan_v3_trace_marks_transition_accepted() -> None:
    turn = compile_executable_turn_v3(
        {"schema_version": 3, "goal": "compare_current", "viewpoint": "unchanged"},
        _state("ЖК Лучи", "Томилинский бульвар"),
    )

    assert turn.trace_metadata["intent_transition"] == {
        "goal": "compare_current",
        "intent_validation": "accepted",
        "validation_error_codes": [],
        "transition": {"accepted": True, "error_code": None},
        "fallback_used": False,
    }


def test_compare_current_pair_compiles_as_typed_data_without_changing_transition() -> None:
    turn = compile_executable_turn_v3(
        {"schema_version": 3, "goal": "compare_current", "viewpoint": "unchanged", "comparison_option_names": ["Первый ЖК", "Третий ЖК"]},
        _state("Первый ЖК", "Второй ЖК", "Третий ЖК"),
    )

    assert turn.accepted is True
    assert turn.goal is IntentGoal.COMPARE_CURRENT
    assert turn.stage is Stage.CURRENT_OPTIONS
    assert turn.action is TurnAction.ANSWER_FROM_CURRENT_OPTIONS
    assert turn.scope == "all"
    assert turn.comparison_option_names == ("Первый ЖК", "Третий ЖК")
    assert turn.selected_option_name is None
    assert turn.named_object_reference is None


def test_compare_current_non_visible_pair_fails_safe_trace_without_object_names() -> None:
    turn = compile_executable_turn_v3(
        {"schema_version": 3, "goal": "compare_current", "viewpoint": "unchanged", "comparison_option_names": ["Первый ЖК", "Секретный ЖК"]},
        _state("Первый ЖК", "Второй ЖК"),
    )

    assert turn.accepted is False
    assert turn.error_code == "validation_failed"
    diagnostic = turn.trace_metadata["intent_transition"]
    assert diagnostic == {
        "goal": "compare_current",
        "intent_validation": "failed",
        "validation_error_codes": ["comparison_option_not_visible"],
        "transition": {"accepted": False, "error_code": None},
        "fallback_used": True,
    }
    dumped = str(diagnostic)
    assert "Первый ЖК" not in dumped
    assert "Секретный ЖК" not in dumped


def test_compare_current_pair_scope_conflict_codes_are_safe_in_trace() -> None:
    scoped = compile_executable_turn_v3(
        {"schema_version": 3, "goal": "new_search", "viewpoint": "unchanged", "comparison_option_names": ["Первый ЖК", "Второй ЖК"]},
        _state("Первый ЖК", "Второй ЖК"),
    )
    selected_conflict = compile_executable_turn_v3(
        {"schema_version": 3, "goal": "compare_current", "viewpoint": "unchanged", "selected_option_name": "Первый ЖК", "comparison_option_names": ["Первый ЖК", "Второй ЖК"]},
        _state("Первый ЖК", "Второй ЖК"),
    )
    named_conflict = compile_executable_turn_v3(
        {"schema_version": 3, "goal": "compare_current", "viewpoint": "unchanged", "named_object_reference": "Второй ЖК", "comparison_option_names": ["Первый ЖК", "Второй ЖК"]},
        _state("Первый ЖК", "Второй ЖК"),
    )

    assert scoped.trace_metadata["intent_transition"]["validation_error_codes"] == ["invalid_comparison_options_scope"]
    assert selected_conflict.trace_metadata["intent_transition"]["validation_error_codes"] == ["comparison_option_fields_conflict"]
    assert named_conflict.trace_metadata["intent_transition"]["validation_error_codes"] == ["comparison_option_fields_conflict"]


def test_historical_v3_compare_plan_accepts_visible_named_reference_as_list_cue() -> None:
    """Redacted planner trace from 2026-07-30; no model or network call."""
    turn = compile_executable_turn_v3(
        {
            "schema_version": 3,
            "goal": "compare_current",
            "viewpoint": "unchanged",
            "selected_option_name": "Левел Лесной",
            "named_object_reference": "Томилинский бульвар",
            "requested_facts": [],
            "constraints_delta": {},
            "operator_consent": None,
            "explicit_operator_request": False,
            "clarification": None,
            "confidence": 1.0,
        },
        _state("Левел Лесной", "Томилинский бульвар"),
    )

    assert turn.accepted is True
    assert turn.goal is IntentGoal.COMPARE_CURRENT
    assert turn.stage is Stage.CURRENT_OPTIONS
    assert turn.action is TurnAction.ANSWER_FROM_CURRENT_OPTIONS
    assert turn.scope == "all"
    assert turn.reference is None
    assert turn.selected_option_name is None
    assert turn.named_object_reference is None
    assert turn.comparison_option_names == ()
    assert turn.trace_metadata["intent_transition"] == {
        "goal": "compare_current",
        "intent_validation": "accepted",
        "validation_error_codes": [],
        "transition": {"accepted": True, "error_code": None},
        "fallback_used": False,
    }
    dumped = str(turn.trace_metadata["intent_transition"])
    assert "Левел" not in dumped
    assert "Томилинский" not in dumped


def test_compare_current_named_reference_outside_visible_options_stays_rejected() -> None:
    turn = compile_executable_turn_v3(
        {
            "schema_version": 3,
            "goal": "compare_current",
            "viewpoint": "unchanged",
            "selected_option_name": "Левел Лесной",
            "named_object_reference": "Невидимый ЖК",
        },
        _state("Левел Лесной", "Томилинский бульвар"),
    )

    assert turn.accepted is False
    assert turn.trace_metadata["intent_transition"] == {
        "goal": "compare_current",
        "intent_validation": "failed",
        "validation_error_codes": ["invalid_named_reference_scope"],
        "transition": {"accepted": False, "error_code": None},
        "fallback_used": True,
    }
