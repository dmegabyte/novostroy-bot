from __future__ import annotations

from copy import deepcopy

import pytest

from nmbot_v2.contracts import IntentGoal, IntentPlanV3, OptionCard
from nmbot_v2.semantic_planner import validate_intent_plan_v3
from nmbot_v2.state import ConversationState


def _state(*names: str, pending_followup: str | None = None) -> ConversationState:
    return ConversationState(
        visible_options=tuple(OptionCard(name=name) for name in names),
        pending_followup=pending_followup,
    )


def _raw(**overrides):
    data = {
        "schema_version": 3,
        "goal": "recommend_current",
        "viewpoint": "unchanged",
        "constraints_delta": {},
        "confidence": 1.0,
    }
    data.update(overrides)
    return data


def test_valid_recommend_current_remains_same_goal_with_visible_options() -> None:
    result = validate_intent_plan_v3(_raw(goal="recommend_current", requested_facts=["parking"]), _state("Первый ЖК"))

    assert result.ok is True
    assert result.plan is not None
    assert result.plan.goal is IntentGoal.RECOMMEND_CURRENT
    assert result.errors == ()


def test_answer_selected_requires_exact_visible_name() -> None:
    state = _state("Мичуринский парк")

    ok = validate_intent_plan_v3(_raw(goal="answer_selected", selected_option_name="Мичуринский парк"), state)
    typo = validate_intent_plan_v3(_raw(goal="answer_selected", selected_option_name="Мичуринский парк!"), state)

    assert ok.ok is True
    assert ok.plan is not None and ok.plan.selected_option_name == "Мичуринский парк"
    assert typo.ok is False
    assert typo.errors == ("selected_option_not_visible",)


def test_missing_named_reference_and_clarification_fail() -> None:
    lookup = validate_intent_plan_v3(_raw(goal="lookup_object"), _state())
    clarify = validate_intent_plan_v3(_raw(goal="clarify"), _state())

    assert lookup.ok is False
    assert lookup.errors == ("missing_named_reference",)
    assert clarify.ok is False
    assert clarify.errors == ("missing_clarification",)


def test_requested_facts_must_be_allowlisted() -> None:
    result = validate_intent_plan_v3(_raw(requested_facts=["parking", "secret_fact"]), _state())

    assert result.ok is False
    assert result.errors == ("invalid_requested_fact",)


def test_parks_is_valid_requested_fact_for_current_goals() -> None:
    result = validate_intent_plan_v3(_raw(goal="recommend_current", requested_facts=["parks"]), _state("Первый ЖК"))

    assert result.ok is True
    assert result.plan is not None
    assert result.plan.requested_facts == ("parks",)


def test_scope_errors_are_stable_safe_codes() -> None:
    result = validate_intent_plan_v3(
        _raw(
            goal="new_search",
            selected_option_name="Первый ЖК",
            named_object_reference="Северный берег",
            operator_consent=True,
            clarification="Уточните район?",
            viewpoint="unsupported",
        ),
        _state("Первый ЖК"),
    )

    assert result.ok is False
    assert result.errors == (
        "clarification_on_non_clarify",
        "invalid_named_reference_scope",
        "invalid_operator_consent_scope",
        "invalid_selected_option_scope",
        "invalid_viewpoint",
    )


def test_malformed_raw_returns_no_plan_and_repairable_safe_error() -> None:
    result = validate_intent_plan_v3(_raw(goal="delete_everything", selected_option_name="RAW SECRET"), _state())

    assert result.ok is False
    assert result.plan is None
    assert result.errors == ("invalid_goal",)
    assert result.repairable is True
    assert "RAW SECRET" not in repr(result.errors)


def test_valid_plan_does_not_mutate_state_or_raw_constraints() -> None:
    state = _state("Первый ЖК")
    before_state = state.to_dict()
    raw = _raw(constraints_delta={"hard": {"rooms": 2}}, query_text=None)
    before_raw = deepcopy(raw)

    result = validate_intent_plan_v3(raw, state)

    assert result.ok is True
    assert state.to_dict() == before_state
    assert raw == before_raw
    assert result.plan is not None
    assert result.plan.constraints_delta == {"hard": {"rooms": 2}}
    assert result.plan.query_text is None


def test_pending_state_does_not_rewrite_goal() -> None:
    plan = IntentPlanV3(schema_version=3, goal=IntentGoal.RECOMMEND_CURRENT, viewpoint="unchanged")

    result = validate_intent_plan_v3(plan, _state("Первый ЖК", pending_followup="operator_offer"))

    assert result.ok is True
    assert result.plan is plan
    assert result.plan.goal is IntentGoal.RECOMMEND_CURRENT


def test_comparison_option_names_round_trip_preserves_order_and_serializes_list() -> None:
    plan = IntentPlanV3.from_dict(_raw(goal="compare_current", comparison_option_names=[" Второй ЖК ", "Первый ЖК"]))

    assert plan.comparison_option_names == ("Второй ЖК", "Первый ЖК")
    assert plan.to_dict()["comparison_option_names"] == ["Второй ЖК", "Первый ЖК"]


def test_absent_comparison_option_names_defaults_to_serialized_empty_list() -> None:
    plan = IntentPlanV3.from_dict(_raw(goal="compare_current"))

    assert plan.comparison_option_names == ()
    assert plan.to_dict()["comparison_option_names"] == []


@pytest.mark.parametrize(
    "value",
    [
        "Первый ЖК, Второй ЖК",
        {"first": "Первый ЖК", "second": "Второй ЖК"},
        {},
        ["Первый ЖК"],
        ("Первый ЖК",),
        ["Первый ЖК", "Второй ЖК", "Третий ЖК"],
        ["Первый ЖК", 2],
        ["Первый ЖК", "   "],
        [" Первый ЖК ", "Первый ЖК"],
    ],
)
def test_malformed_comparison_option_names_raise_specific_value_error(value) -> None:
    with pytest.raises(ValueError, match="comparison_option_names"):
        IntentPlanV3.from_dict(_raw(goal="compare_current", comparison_option_names=value))


@pytest.mark.parametrize("value", ["Первый ЖК, Второй ЖК", ["Первый ЖК"], ["Первый ЖК", " "]])
def test_malformed_comparison_option_names_are_repairable_safe_parse_errors(value) -> None:
    result = validate_intent_plan_v3(_raw(goal="compare_current", comparison_option_names=value), _state("Первый ЖК", "Второй ЖК"))

    assert result.ok is False
    assert result.plan is None
    assert result.errors == ("invalid_comparison_option_names",)
    assert result.repairable is True


def test_comparison_option_names_validation_accepts_arbitrary_visible_pair_order() -> None:
    result = validate_intent_plan_v3(
        _raw(goal="compare_current", comparison_option_names=["Первый ЖК", "Третий ЖК"]),
        _state("Первый ЖК", "Второй ЖК", "Третий ЖК"),
    )

    assert result.ok is True
    assert result.plan is not None
    assert result.plan.comparison_option_names == ("Первый ЖК", "Третий ЖК")


def test_comparison_option_names_scope_visible_and_conflict_rules() -> None:
    state = _state("Первый ЖК", "Второй ЖК")

    assert validate_intent_plan_v3(
        _raw(goal="new_search", comparison_option_names=["Первый ЖК", "Второй ЖК"]),
        state,
    ).errors == ("invalid_comparison_options_scope",)
    assert validate_intent_plan_v3(
        _raw(goal="compare_current", comparison_option_names=["Первый ЖК", "Невидимый ЖК"]),
        state,
    ).errors == ("comparison_option_not_visible",)
    assert validate_intent_plan_v3(
        _raw(goal="compare_current", selected_option_name="Первый ЖК", comparison_option_names=["Первый ЖК", "Второй ЖК"]),
        state,
    ).errors == ("comparison_option_fields_conflict",)
    assert validate_intent_plan_v3(
        _raw(goal="compare_current", named_object_reference="Второй ЖК", comparison_option_names=["Первый ЖК", "Второй ЖК"]),
        state,
    ).errors == ("comparison_option_fields_conflict",)
