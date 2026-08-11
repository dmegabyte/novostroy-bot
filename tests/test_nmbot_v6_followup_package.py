import asyncio
import json

from nmbot_v6.followup import FollowupKind, PendingInteractionResolver, exact_detail_context
from nmbot_v6.gateway import Prompt1GatewayResult, V6OvermindTransport, build_question_policy
from nmbot_v6.phone import PhoneParseResult
from nmbot_v6.runtime import RuntimeStatus, V6Runtime
from nmbot_v6.state import PendingInteraction, V6State


def _no_phone(_text, _backend=None):
    return PhoneParseResult(False)


def _card(name, ref, **extra):
    return {
        "name": name,
        "ref": ref,
        "location": "Москва",
        "district": "msk",
        **extra,
    }


def _output(*, facts=(), params=None, action="search", question=""):
    return {
        "action": action,
        "target": "none" if action in {"clarify", "recover_dialogue"} else "new_search",
        "search_policy": "forbidden" if action == "recover_dialogue" else "required",
        "clarification_question": question,
        "response": "",
        "facts": list(facts),
        "near": [],
        "missing": [],
        "params": dict(params or {}),
    }


class Prompt1Stub:
    def __init__(self, output):
        self.output = output
        self.states = []

    async def run(self, _text, state):
        self.states.append(state)
        trace = None
        if self.output["search_policy"] == "required":
            trace = V6OvermindTransport._model_projection_trace(
                self.output, {"_gateway_task_id": "task-v6-followup-package"}
            )
            assert trace is not None
        return Prompt1GatewayResult(self.output, trace)


class Prompt2Stub:
    def __init__(self):
        self.calls = []

    async def run(self, _text, state, plan, evidence):
        self.calls.append((state, plan, evidence))
        cards = [{"index": index, "text": "Данные подтверждены."} for index, _ in enumerate(plan.facts)]
        operator = build_question_policy(_text, state, plan).get("operator_escalation_required") is True
        question = "Передать текущий вопрос оператору?" if operator else "Что уточнить дальше?"
        return json.dumps({"intro": "", "cards": cards, "question": question})


def _run(state, text, output):
    prompt1 = Prompt1Stub(output)
    prompt2 = Prompt2Stub()
    result = asyncio.run(V6Runtime(prompt1, prompt2, phone_parser=_no_phone).run(text, state))
    return result, prompt1, prompt2


def _pending_offer(card, *, question_goal="offer_layouts_or_viewing", accept_action="show_layouts"):
    return V6State(
        revision=7,
        option_refs=(card["ref"],),
        current_cards=(card,),
        safe_context={"effective_constraints": {"rooms": 2}},
        pending_interaction=PendingInteraction(
            "offer", question_goal, accept_action, "clear_pending", (card["ref"],), 7
        ),
    )


def test_expanded_detail_with_only_apartment_types_does_not_open_layout_offer_or_repeat_on_yes():
    card = _card("Люблинский парк", "complex:lp", apartment_types=("двухкомнатные",))
    first, _, _ = _run(
        V6State(),
        "Расскажите подробно про Люблинский парк",
        _output(facts=(card,), params={"search_mode": "named_object", "count": 1}),
    )
    assert first.status is RuntimeStatus.COMPLETED

    second, second_prompt1, _ = _run(
        first.state,
        "да",
        _output(
            action="recover_dialogue",
            question="Что именно хотите уточнить?",
        ),
    )

    violations = []
    pending = first.state.pending_interaction
    if pending is not None and pending.accept_action == "show_layouts":
        violations.append("expanded detail without ads/lot evidence opened show_layouts")
    if "exact_detail" in second_prompt1.states[0]["safe_context"]:
        violations.append("bare yes rebound the same object as exact_detail")
    if second.plan is not None and second.plan.params.get("search_mode") == "named_object":
        violations.append("bare yes forced an exact named-object repeat")
    assert not violations, "; ".join(violations)


def test_bare_yes_for_multi_card_selection_is_code_owned_forbidden_recovery():
    cards = (
        _card("Первый", "complex:first"),
        _card("Второй", "complex:second"),
    )
    state = V6State(
        revision=3,
        option_refs=tuple(card["ref"] for card in cards),
        current_cards=cards,
        pending_interaction=PendingInteraction(
            "selection",
            "choose_complex",
            "normal_prompt1",
            "clear_pending",
            tuple(card["ref"] for card in cards),
            3,
        ),
    )

    result, _, _ = _run(
        state,
        "да",
        _output(facts=cards, params={"search_mode": "broad", "count": 2}),
    )

    assert result.status is RuntimeStatus.COMPLETED
    assert result.plan.action.value == "recover_dialogue"
    assert result.plan.target.value == "none"
    assert result.plan.search_policy.value == "forbidden"
    assert result.plan.clarification_question == "Какой из вариантов хотите рассмотреть подробнее?"
    assert result.evidence.call_count == 0


def test_podrobnee_accepts_one_actionable_pending_subject_and_builds_exact_detail():
    card = _card("Саларьево парк", "complex:sp", lot_examples=("lot:sp-1",), rooms=2)
    state = _pending_offer(card)

    resolution = PendingInteractionResolver().resolve("подробнее", state)
    detail = exact_detail_context(resolution, state)

    assert resolution.kind is FollowupKind.ACCEPT
    assert detail is not None
    assert detail["subject_ref"] == "complex:sp"
    assert detail["canonical_name"] == "Саларьево парк"
    assert detail["pending_action"] == "show_layouts"


def test_compound_accept_keeps_exact_subject_and_rooms_but_allows_new_max_price():
    card = _card(
        "Люблинский парк",
        "complex:lp",
        novos_id=2018,
        lot_examples=("lot:lp-1",),
        rooms=2,
    )
    state = _pending_offer(card)
    result, prompt1, _ = _run(
        state,
        "да, но до 18 млн",
        _output(
            facts=(card,),
            params={
                "search_mode": "broad",
                "count": 3,
                "rooms": 2,
                "max_price": 18_000_000,
            },
        ),
    )

    detail = prompt1.states[0]["safe_context"].get("exact_detail")
    assert detail is not None
    assert detail["subject_ref"] == "complex:lp"
    assert detail["canonical_name"] == "Люблинский парк"
    assert detail["lot_constraints"]["rooms"] == 2
    assert result.status is RuntimeStatus.COMPLETED
    assert result.plan.params["rooms"] == 2
    assert result.plan.params["max_price"] == 18_000_000
    assert result.plan.params["search_mode"] == "named_object"
    assert result.plan.params["count"] == 1
    assert len(result.plan.facts) == 1
    assert not result.plan.near
