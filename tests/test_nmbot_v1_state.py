import dataclasses

import pytest

from nmbot_v1.contracts import V1IntentPlan
from nmbot_v1.runtime import run_turn_sync
from nmbot_v1.state import V1ConversationState, redact_phone


class BadSearchPlanner:
    def plan(self, _):
        return V1IntentPlan.from_dict({
            "schema_version": 1, "goal": "select_project", "viewpoint": "buyer",
            "constraints_delta": {"hard": {}, "preferences": {}}, "selected_option_ref": "missing",
            "selected_lot_ref": None, "requested_facts": [], "operator_intent": "none",
            "clarification": None, "confidence": 1,
        })


def test_state_roundtrip_immutability_and_redaction():
    state = V1ConversationState.clean()
    state = V1ConversationState.from_dict({**state.to_dict(), "contact_phone_redacted": "+7 999 123-45-67", "recent_safe_turns": ["телефон 89991234567"]})
    assert state.contact_phone_redacted == "***4567"
    assert state.recent_safe_turns == ("телефон ***4567",)
    assert V1ConversationState.from_dict(state.to_dict()).to_dict() == state.to_dict()
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.revision = 5
    assert redact_phone("мой номер +7 900 111-22-33") == "мой номер ***2233"
    frozen = V1ConversationState.from_dict({**state.to_dict(), "hard_constraints": {"location": "Москва"}, "visible_options": [{"ref": "p1", "facts": {"price": 10}}]})
    with pytest.raises(TypeError):
        frozen.hard_constraints["location"] = "СПб"
    with pytest.raises(TypeError):
        frozen.visible_options[0]["ref"] = "p2"
    with pytest.raises(TypeError):
        frozen.visible_options[0]["facts"]["price"] = 11


def test_rejected_execution_preserves_state_and_revision():
    state = V1ConversationState.clean()
    result = run_turn_sync("выбери тайный ref", state.to_dict(), BadSearchPlanner())
    assert result.stage == "safe_error"
    assert result.state == state.to_dict()
    assert result.state["revision"] == 0
