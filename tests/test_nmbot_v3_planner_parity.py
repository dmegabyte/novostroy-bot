"""Closed V3 planner fixtures for the intentionally overlapping V2 contract."""
from __future__ import annotations

import pytest

from nmbot_v2.semantic_planner import validate_intent_plan_v3 as validate_v2
from nmbot_v2.state import ConversationState
from nmbot_v3.contracts import V3PlannerContext
from nmbot_v3.semantic_planner import validate_intent_plan_v3 as validate_v3
from nmbot_v3.transition import compile_executable_turn_v3


def _raw(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 3,
        "goal": "answer_current",
        "viewpoint": "unchanged",
        "constraints_delta": {},
        "confidence": 1.0,
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("raw", "pending", "expected"),
    [
        (_raw(goal="lookup_object", named_object_reference="ЖК Лучи"), None, (True, (), False)),
        (_raw(goal="answer_current"), None, (True, (), False)),
        (_raw(goal="recommend_current", requested_facts=["parking"]), None, (True, (), False)),
        (_raw(followup_outcome="accept"), None, (False, ("followup_outcome_without_pending",), False)),
        (_raw(followup_outcome="resume_contact"), "contact_name", (True, (), False)),
        (_raw(followup_outcome="accept"), "contact_name", (False, ("followup_outcome_not_allowed",), False)),
        (_raw(goal="delete_everything"), None, (False, ("invalid_goal",), True)),
        (_raw(unexpected_field=True), None, (False, ("unknown_field",), True)),
    ],
    ids=(
        "exact-named-reference",
        "current-options",
        "current-options-with-allowed-fact",
        "pending-outcome-without-pending",
        "pending-contact-reply",
        "pending-contact-rejects-financing-reply",
        "invalid-goal",
        "unknown-field",
    ),
)
def test_overlapping_closed_fixtures_match_v2_validation_surface(raw, pending, expected) -> None:
    """V2 is an external oracle only; this asserts no broader semantic parity."""
    v2 = validate_v2(raw, ConversationState(pending_followup=pending))
    v3 = validate_v3(raw, V3PlannerContext(pending_followup_key=pending))

    assert (v2.ok, v2.errors, v2.repairable) == expected
    assert (v3.ok, v3.errors, v3.repairable) == expected


def test_v3_named_reference_is_closed_to_lookup_and_compiles_locally() -> None:
    accepted = compile_executable_turn_v3(
        _raw(goal="lookup_object", named_object_reference="ЖК Лучи"), V3PlannerContext()
    )
    rejected = validate_v3(_raw(named_object_reference="ЖК Лучи"), V3PlannerContext())

    assert accepted.accepted is True
    assert accepted.named_object_reference == "ЖК Лучи"
    assert rejected.errors == ("invalid_named_reference_scope",)


def test_v3_current_options_remain_ref_based_not_name_based() -> None:
    reference = "550e8400-e29b-41d4-a716-446655440000"
    context = V3PlannerContext((reference,))

    assert validate_v3(_raw(goal="answer_current"), context).ok
    assert validate_v3(_raw(goal="answer_selected", selected_option_ref=reference), context).ok
    assert validate_v3(_raw(goal="answer_selected", selected_option_name="ЖК Лучи"), context).errors == (
        "selected_option_name_not_supported",
        "selected_option_not_visible",
    )
