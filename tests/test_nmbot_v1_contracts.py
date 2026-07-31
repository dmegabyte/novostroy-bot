import dataclasses

import pytest

from nmbot_v1.contracts import V1Error, V1Goal, V1IntentPlan, V1OperatorIntent


def test_intent_plan_strict_unknown_schema_confidence_roundtrip():
    raw = {
        "schema_version": 1,
        "goal": "search",
        "viewpoint": "buyer",
        "constraints_delta": {"hard": {"location": "Москва"}, "preferences": {"finishing": "whitebox"}},
        "selected_option_ref": None,
        "selected_lot_ref": None,
        "requested_facts": ["price"],
        "operator_intent": "none",
        "clarification": None,
        "contact_name": None,
        "contact_phone": None,
        "confidence": 0.8,
    }
    plan = V1IntentPlan.from_dict(raw)
    assert plan.goal is V1Goal.SEARCH
    assert plan.operator_intent is V1OperatorIntent.NONE
    assert plan.to_dict() == raw
    assert "raw_query" not in plan.to_dict()
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.viewpoint = "x"
    with pytest.raises(V1Error):
        V1IntentPlan.from_dict({**raw, "extra": 1})
    with pytest.raises(V1Error):
        V1IntentPlan.from_dict({**raw, "schema_version": 2})
    with pytest.raises(V1Error):
        V1IntentPlan.from_dict({**raw, "confidence": 1.5})
    with pytest.raises(V1Error):
        V1IntentPlan.from_dict({**raw, "confidence": True})
    with pytest.raises(V1Error):
        V1IntentPlan.from_dict({**raw, "viewpoint": 7})
    with pytest.raises(V1Error):
        V1IntentPlan.from_dict({**raw, "selected_option_ref": 7})
    with pytest.raises(TypeError):
        plan.constraints_delta.hard["location"] = "СПб"


def test_all_required_stages_and_actions_exist():
    from nmbot_v1.contracts import V1Action, V1Stage

    assert {s.value for s in V1Stage} == {
        "reset", "first_search", "refine_search", "expand_search", "current_options",
        "selected_project", "selected_lot_search", "selected_lot", "fact_check",
        "operator_offer", "contact_name", "contact_phone", "operator_declined",
        "off_topic", "safe_error",
    }
    assert {"reset", "search", "select_project", "select_lot", "offer_operator", "capture_name", "capture_phone", "safe_error"} <= {a.value for a in V1Action}
