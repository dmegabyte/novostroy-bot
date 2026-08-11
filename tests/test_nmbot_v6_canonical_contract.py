import asyncio
import json

from nmbot_v6.canonical_card import build_answer_contract
from nmbot_v6.gateway import Prompt2Gateway, TransportResponse
from nmbot_v6.prompt1_contract import parse_prompt1
from nmbot_v6.provider import TrustedMcpEnvelope


class CaptureTransport:
    def __init__(self):
        self.payload = None

    async def complete(self, payload):
        self.payload = payload
        return TransportResponse('{"intro":"","cards":[],"question":"Что уточнить?"}')


def plan(facts, missing=(), requested_claims=()):
    facts = [
        {"name": "Янила Форест", "location": "Янино-1", "district": "mo", **fact}
        for fact in facts
    ]
    return parse_prompt1({
        "action": "search", "target": "new_search", "search_policy": "required",
        "clarification_question": "", "response": "", "facts": facts, "near": [],
        "missing": list(missing), "requested_claims": list(requested_claims), "params": {"count": 1},
    })


def test_contract_excludes_unmapped_values_and_exposes_conflict():
    result = build_answer_contract(
        plan([{"name": "Янила Форест", "location": "Янино-1", "district": "mo", "price_range": "от 4.5 млн", "price_from": "от 5 млн", "backend_note": "рассрочка"}], missing=("installment_terms",)),
        TrustedMcpEnvelope(None, None, None, 1),
        question_policy={"question_goal": "operator_contact"},
    )
    assert "backend_note" not in result["cards"][0]["canonical"]
    assert "backend_note" not in result
    assert tuple(result["missing_claims"]) == ("installment_terms",)
    assert "project_price" in result["conflicts"]["0"]
    assert "project_price" not in result["cards"][0]["canonical"]


def test_prompt2_payload_contains_answer_contract_not_raw_unmapped():
    transport = CaptureTransport()
    gateway = Prompt2Gateway(transport)
    asyncio.run(gateway.run(
        "Какая рассрочка?",
        {"revision": 0},
            plan([{"name": "Янила Форест", "location": "Янино-1", "district": "mo", "price_range": "от 4.5 млн", "backend_note": "условия"}], missing=("installment_terms",)),
        TrustedMcpEnvelope(None, None, None, 1),
    ))
    query = json.loads(transport.payload["query"].removeprefix("V6_ANSWER_INPUT="))
    contract = query["answer_contract"]
    assert contract["missing_claims"] == ["installment_terms"]
    assert "backend_note" not in json.dumps(contract, ensure_ascii=False)
    assert "answer_contract" in query


def test_project_price_cannot_satisfy_room_price_without_lot():
    result = build_answer_contract(
        plan([{"name": "Янила Форест", "location": "Янино-1", "district": "mo", "price_range": "от 4.5 млн"}], missing=("room_price",)),
        TrustedMcpEnvelope(None, None, None, 1),
        question_policy={"question_goal": "operator_contact"},
    )
    assert "room_price" in result["missing_claims"]
    assert "lots" not in result["cards"][0]["canonical"]


def test_requested_claims_survive_as_typed_contract():
    result = build_answer_contract(
        plan([{"name": "Янила Форест", "price_range": "от 4.5 млн"}], requested_claims=("room_price", "installment_terms")),
        TrustedMcpEnvelope(None, None, None, 1),
        question_policy={"question_goal": "continue_search"},
    )
    assert tuple(result["requested_claims"]) == ("room_price", "installment_terms")
