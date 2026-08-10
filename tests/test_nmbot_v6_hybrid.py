import asyncio
import json

from nmbot_v6.followup import PendingInteractionResolver
from nmbot_v6.gateway import Prompt1GatewayResult, V6OvermindTransport, build_question_policy
from nmbot_v6.phone import PhoneParseResult
from nmbot_v6.runtime import RuntimeStatus, V6Runtime
from nmbot_v6.state import PendingInteraction, V6State


def _no_phone(_text, _backend=None):
    return PhoneParseResult(False)


def _card(name, ref, **extra):
    return {"name": name, "ref": ref, "location": "Москва", "district": "msk", **extra}


def _output(*, facts, params, near=None):
    return {"action": "search", "target": "new_search", "search_policy": "required", "clarification_question": "", "response": "", "facts": facts, "near": near or [], "missing": [], "params": params}


class Prompt1Stub:
    def __init__(self, output): self.output, self.states = output, []
    async def run(self, _text, state):
        self.states.append(state)
        trace = V6OvermindTransport._model_projection_trace(self.output, {"_gateway_task_id": "task-hybrid"})
        return Prompt1GatewayResult(self.output, trace)


class Prompt2Stub:
    def __init__(self): self.calls = []
    async def run(self, _text, state, plan, evidence):
        self.calls.append((state, plan, evidence))
        cards = [{"index": 0, "text": "Детали подтверждены."}] if plan.facts else []
        return json.dumps({"intro": "", "cards": cards, "question": "Показать планировки?"})


def _run(state, text, output):
    prompt1, prompt2 = Prompt1Stub(output), Prompt2Stub()
    result = asyncio.run(V6Runtime(prompt1, prompt2, phone_parser=_no_phone).run(text, state))
    return result, prompt1, prompt2


def _offer_state(card, *, goal="learn_about_complex", action="show_stored_details"):
    return V6State(revision=7, option_refs=(card["ref"],), current_cards=(card,), pending_interaction=PendingInteraction("offer", goal, action, "clear_pending", (card["ref"],), 7))


def test_cta_accept_projects_exact_context_and_forces_expanded_detail():
    card = _card("Люблинский парк", "complex:lp", rooms=2, price_max=18_000_000)
    result, prompt1, prompt2 = _run(_offer_state(card), "да, расскажи всё", _output(facts=[card], params={"rooms": 2, "max_price": 18_000_000, "count": 1}))
    assert result.status is RuntimeStatus.COMPLETED
    assert prompt1.states[0]["safe_context"]["exact_detail"]["canonical_name"] == "Люблинский парк"
    assert result.plan.params["search_mode"] == "named_object"
    assert build_question_policy("да", prompt2.calls[0][0], result.plan)["answer_mode"] == "expanded_detail"


def test_offer_layouts_accept_uses_exact_pipeline():
    card = _card("Саларьево парк", "complex:sp")
    state = _offer_state(card, goal="offer_layouts_or_viewing", action="normal_prompt1")
    result, prompt1, prompt2 = _run(state, "да", _output(facts=[card], params={"count": 1, "rooms": 2}))
    assert result.status is RuntimeStatus.COMPLETED
    assert prompt1.states[0]["safe_context"]["exact_detail"]["pending_action"] == "show_layouts"
    assert result.plan.params["search_mode"] == "named_object"
    assert len(prompt2.calls) == 1


def test_offer_layouts_preserves_rooms_and_selected_identity():
    card = _card("Люблинский парк", "complex:lp", novos_id=2018, rooms=2)
    state = V6State(
        revision=7,
        option_refs=(card["ref"],),
        current_cards=(card,),
        safe_context={"effective_constraints": {"rooms": 2}},
        pending_interaction=PendingInteraction(
            "offer", "offer_layouts_or_viewing", "show_layouts", "clear_pending", (card["ref"],), 7
        ),
    )
    result, prompt1, _ = _run(state, "да", _output(facts=[card], params={"count": 1, "search_mode": "named_object", "rooms": 2}))
    assert result.status is RuntimeStatus.COMPLETED
    assert prompt1.states[0]["safe_context"]["effective_constraints"]["rooms"] == 2
    assert prompt1.states[0]["current_cards"][0]["novos_id"] == 2018


def test_multiple_cards_bare_consent_clarifies():
    cards = (_card("Первый", "complex:first"), _card("Второй", "complex:second"))
    state = V6State(revision=3, option_refs=tuple(c["ref"] for c in cards), current_cards=cards, pending_interaction=PendingInteraction("selection", "choose_complex", "normal_prompt1", "clear_pending", tuple(c["ref"] for c in cards), 3))
    result, prompt1, prompt2 = _run(state, "да", _output(facts=list(cards), params={"count": 2, "search_mode": "broad"}))
    assert result.status is RuntimeStatus.COMPLETED
    assert prompt1.states[0]["safe_context"]["followup_clarification"]["reason"] == "ambiguous_consent"
    assert result.plan.action.value == "clarify" and not result.plan.facts and not result.plan.near
    assert len(prompt2.calls) == 1


def test_compound_consent_is_not_bare_accept():
    card = _card("Люблинский парк", "complex:lp")
    state = _offer_state(card)
    result, prompt1, _ = _run(state, "да, но до 18 млн", _output(facts=[card], params={"max_price": 18_000_000, "count": 1, "search_mode": "broad"}))
    assert PendingInteractionResolver().resolve("да, но до 18 млн", state).kind.value == "unresolved"
    assert result.status is RuntimeStatus.COMPLETED
    assert "exact_detail" not in prompt1.states[0]["safe_context"]
    assert result.plan.params["max_price"] == 18_000_000
