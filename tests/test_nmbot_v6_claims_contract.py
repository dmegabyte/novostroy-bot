import asyncio
import json

import pytest

from nmbot_v6.canonical_card import build_answer_contract
from nmbot_v6.contracts import ContractError
from nmbot_v6.gateway import Prompt1Gateway, Prompt2Gateway, TransportResponse
from nmbot_v6.phone import PhoneParseResult
from nmbot_v6.prompt1_contract import overlay_explicit_request, parse_prompt1
from nmbot_v6.provider import (
    TRUSTED_MCP_SERVER,
    TRUSTED_MCP_TOOL,
    TransportToolTrace,
    TrustedMcpEnvelope,
    _TRACE_TOKEN,
)
from nmbot_v6.runtime import RuntimeStatus, V6Runtime
from nmbot_v6.state import V6State


def _raw_plan(**changes):
    value = {
        "action": "search",
        "target": "new_search",
        "search_policy": "required",
        "clarification_question": "",
        "response": "",
        "facts": [{
            "name": "Янила Форест",
            "location": "Янино",
            "district": "mo",
            "price_range": "от 5 млн ₽",
            "ref": "yanila-1",
        }],
        "near": [],
        "missing": [],
        "params": {"search_mode": "named_object", "count": 1},
    }
    value.update(changes)
    return value


def test_requested_claims_parse_is_optional_nullable_and_allowlisted():
    assert parse_prompt1(_raw_plan()).requested_claims == ()
    assert parse_prompt1(_raw_plan(requested_claims=None)).requested_claims == ()
    assert parse_prompt1(
        _raw_plan(requested_claims=["room_price", "installment_terms"])
    ).requested_claims == ("room_price", "installment_terms")
    with pytest.raises(ContractError, match="unknown claim"):
        parse_prompt1(_raw_plan(requested_claims=["project_price"]))


def test_explicit_yanila_request_overlays_claims_and_typed_rooms():
    plan = parse_prompt1(_raw_plan(requested_claims=[]))
    overlaid = overlay_explicit_request(
        plan,
        "Интересует квартира в Янила Форест в рассрочку, стоимость на 2 квартиру",
    )
    assert overlaid.requested_claims == ("installment_terms", "room_price")
    assert overlaid.params["rooms"] == 2
    assert type(overlaid.params["rooms"]) is int


def test_project_price_never_satisfies_room_price_or_installment():
    plan = parse_prompt1(_raw_plan(
        requested_claims=["room_price", "installment_terms"],
        params={"rooms": 2, "search_mode": "named_object", "count": 1},
    ))
    contract = build_answer_contract(
        plan,
        TrustedMcpEnvelope(None, None, None, 0),
        question_policy={},
    )
    assert "project_price" in contract["allowed_claims"]
    assert "room_price" not in contract["allowed_claims"]
    assert "installment_terms" not in contract["allowed_claims"]
    assert set(contract["missing_claims"]) == {"room_price", "installment_terms"}


def test_missing_and_conflicting_claims_are_excluded_from_allowed_facts():
    facts = [{
        "name": "Янила Форест",
        "location": "Янино",
        "district": "mo",
        "payment_by_installments": "12 месяцев",
        "installment": "24 месяца",
        "metro_distance": "20 минут",
    }]
    plan = parse_prompt1(_raw_plan(
        facts=facts,
        requested_claims=["installment_terms", "metro_distance"],
        missing=["metro_distance"],
    ))
    contract = build_answer_contract(
        plan, TrustedMcpEnvelope(None, None, None, 0), question_policy={}
    )
    assert "installment_terms" not in contract["allowed_claims"]
    assert "metro_distance" not in contract["allowed_claims"]
    assert set(contract["missing_claims"]) == {"installment_terms", "metro_distance"}
    assert "installment_terms" in contract["conflicts"]["0"]
    canonical = contract["cards"][0]["canonical"]
    assert "installment_terms" not in canonical
    assert "metro_distance" in canonical  # diagnostic card value is present but not assertable


class _CaptureTransport:
    def __init__(self, output):
        self.output = output
        self.payloads = []

    async def complete(self, payload):
        self.payloads.append(payload)
        return TransportResponse(self.output)


def test_actual_prompt2_payload_contains_answer_contract():
    plan = parse_prompt1(_raw_plan(
        requested_claims=["room_price"],
        params={"rooms": 2, "search_mode": "named_object", "count": 1},
    ))
    evidence = TrustedMcpEnvelope(None, None, None, 0)
    transport = _CaptureTransport('{"intro":"","cards":[],"question":"Что уточнить?"}')
    asyncio.run(Prompt2Gateway(transport).run("цена двушки", {}, plan, evidence))
    query = transport.payloads[0]["query"]
    payload = json.loads(query.removeprefix("V6_ANSWER_INPUT="))
    assert payload["answer_contract"]["requested_claims"] == ["room_price"]
    assert payload["answer_contract"]["missing_claims"] == ["room_price"]


class _Prompt1Transport:
    async def complete(self, payload):
        query = json.loads(payload["query"].removeprefix("V6_SEARCH_INPUT="))
        user_text = query["user_text"]
        card = {
            "name": "Янила Форест", "location": "Янино", "district": "mo",
            "price_range": "от 5 млн ₽", "ref": "yanila-1",
        }
        raw = _raw_plan(facts=[card], requested_claims=[])
        trace = TransportToolTrace(
            task_ref="task-claims",
            actual_server=TRUSTED_MCP_SERVER,
            actual_tool=TRUSTED_MCP_TOOL,
            call_count=1,
            safe_facts={"facts": [card], "near": [], "missing": [], "params": raw["params"]},
            effective_constraints=raw["params"],
            visible_refs=["yanila-1"],
            _token=_TRACE_TOKEN,
        )
        assert user_text
        return TransportResponse(raw, trace)


class _Prompt2Transport:
    def __init__(self):
        self.answer_inputs = []

    async def complete(self, payload):
        data = json.loads(payload["query"].removeprefix("V6_ANSWER_INPUT="))
        self.answer_inputs.append(data)
        if data["question_policy"].get("operator_escalation_required"):
            question = "Передать этот вопрос специалисту?"
        else:
            question = "Показать больше информации?"
        return TransportResponse(json.dumps({
            "intro": "Нашёл подтверждённый вариант.",
            "cards": [],
            "question": question,
        }, ensure_ascii=False))


def test_three_turn_runtime_preserves_state_and_claim_overlay():
    async def scenario():
        answer_transport = _Prompt2Transport()
        runtime = V6Runtime(
            Prompt1Gateway(_Prompt1Transport()), Prompt2Gateway(answer_transport),
            phone_parser=lambda text, backend=None: PhoneParseResult(False),
        )
        state = V6State()
        texts = (
            "Интересует квартира в Янила Форест в рассрочку",
            "стоимость на 2 квартиру",
            "какое расстояние до метро",
        )
        results = []
        for text in texts:
            result = await runtime.run(text, state)
            assert result.status is RuntimeStatus.COMPLETED
            assert result.state.revision == state.revision + 1
            state = result.state
            results.append(result)
        return results, answer_transport.answer_inputs

    results, inputs = asyncio.run(scenario())
    assert results[0].plan.requested_claims == ("installment_terms",)
    assert results[1].plan.requested_claims == ("room_price",)
    assert results[1].plan.params["rooms"] == 2
    assert results[2].plan.requested_claims == ("metro_distance",)
    assert len(inputs) == 3
    assert all("answer_contract" in item for item in inputs)
