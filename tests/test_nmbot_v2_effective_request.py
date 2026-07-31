from nmbot_v2.contracts import SemanticPlan
from nmbot_v2.effective_request import build_effective_request
from nmbot_v2.state import ConversationState


def test_effective_request_combines_previous_context_with_current_delta() -> None:
    state = ConversationState(params={"rooms": 2, "purpose": "family"}, active_topic="family")
    plan = SemanticPlan(
        operation="refine_search",
        constraints_delta={"hard": {"max_price": 15_000_000}},
    )

    request = build_effective_request(state, plan)

    assert request.params == {"rooms": 2, "purpose": "family", "max_price": 15_000_000}
    assert request.intent == "family"


def test_effective_request_keeps_ambiguous_value_as_clarification_not_filter() -> None:
    state = ConversationState(params={"rooms": 2, "purpose": "family"})
    plan = SemanticPlan(
        operation="financing",
        intent="mortgage",
        clarification="10 млн — это весь бюджет или первоначальный взнос?",
    )

    request = build_effective_request(state, plan)

    assert "max_price" not in request.params
    assert "down_payment" not in request.params
    assert request.clarification == "10 млн — это весь бюджет или первоначальный взнос?"
