import asyncio
import json

from nmbot_v6.canonical_card import build_answer_contract
from nmbot_v6.gateway import Prompt2Gateway, TransportResponse
from nmbot_v6.prompt1_contract import overlay_explicit_request, parse_prompt1
from nmbot_v6.provider import TrustedMcpEnvelope


def plan(**changes):
    value = {
        "action": "search", "target": "new_search", "search_policy": "required",
        "clarification_question": "", "response": "", "facts": [{
            "name": "Янила Форест", "location": "Янино", "district": "mo",
            "price_range": "от 4.5 млн руб.",
        }], "near": [], "missing": [],
        "params": {"search_mode": "named_object", "count": 1},
    }
    value.update(changes)
    return parse_prompt1(value)


def test_explicit_yanila_claims_and_rooms_are_recovered():
    result = overlay_explicit_request(
        plan(), "Интересует квартира в Янила Форест в рассрочку, стоимость на 2 квартиру"
    )
    assert result.requested_claims == ("installment_terms", "room_price")
    assert result.params["rooms"] == 2


def test_project_price_does_not_satisfy_room_price_or_installment():
    contract = build_answer_contract(
        plan(requested_claims=["room_price", "installment_terms"]),
        TrustedMcpEnvelope(None, None, None, 0), question_policy={},
    )
    assert set(contract["missing_claims"]) == {"room_price", "installment_terms"}
    assert "project_price" in contract["allowed_claims"]


class CaptureTransport:
    def __init__(self):
        self.payload = None

    async def complete(self, payload):
        self.payload = payload
        return TransportResponse('{"intro":"","cards":[],"question":"Что уточнить?"}')


def test_prompt2_receives_compact_answer_contract():
    transport = CaptureTransport()
    asyncio.run(Prompt2Gateway(transport).run(
        "стоимость на 2 квартиру",
        {},
        plan(requested_claims=["room_price"], params={"rooms": 2, "count": 1}),
        TrustedMcpEnvelope(None, None, None, 0),
    ))
    payload = json.loads(transport.payload["query"].removeprefix("V6_ANSWER_INPUT="))
    assert payload["answer_contract"]["requested_claims"] == ["room_price"]
    assert payload["answer_contract"]["missing_claims"] == ["room_price"]
