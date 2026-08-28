"""Synthetic product scenarios exercised against the canonical runtime only."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nmbot_core import CoreRuntime, CoreState, GatewayResult, ToolTrace


FIXTURE = Path(__file__).with_name("fixtures") / "v6_product_scenarios.json"


class PhoneBackend:
    def parse(self, candidate, region): return candidate
    def is_possible_number(self, parsed): return True
    def is_valid_number(self, parsed): return True
    def format_e164(self, parsed): return "+79991234567"


class Port:
    def __init__(self, *outputs, trace: ToolTrace | None = None):
        self.outputs, self.calls, self.trace = list(outputs), [], trace

    async def run(self, payload, *, repair=False):
        self.calls.append((payload, repair))
        return GatewayResult(self.outputs.pop(0), "attempt-1", self.trace)


def _p1(*, facts=None, params=None):
    return {"action": "continue", "facts": facts or [], "near": [], "missing": [], "params": params or {}, "ambiguity": None}


def _p2(text: str, question: str = "Какой вариант уточнить?"):
    return {"action": "reply", "response": text, "final_question": question}


def _run(runtime: CoreRuntime, messages: list[str], state: CoreState = CoreState()):
    results = []
    for message in messages:
        result = asyncio.run(runtime.run(message, state))
        results.append(result)
        state = result.state
    return results


def test_fixture_is_exactly_the_five_authored_scenarios():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["kind"] == "synthetic"
    assert [row["id"] for row in fixture["scenarios"]] == [
        "new_search", "refine_search", "selected_property", "finance_consultation", "direct_specialist",
    ]


def test_new_search_and_refinement_preserve_bounded_context():
    p1 = Port(
        _p1(facts=[{"name": "ЖК Люблино", "rooms": 2}], params={"rooms": 2, "location": "Люблино"}),
        _p1(facts=[{"name": "ЖК Люблино", "finishing": "есть"}], params={"finishing": "есть"}),
        trace=ToolTrace("attempt-1", "novostroym", "get_flat_info", 1),
    )
    p2 = Port(_p2("Есть до трёх вариантов."), _p2("Оставил варианты с отделкой."))
    runtime = CoreRuntime(p1, p2, phone_backend=PhoneBackend())
    results = _run(runtime, ["Ищу двухкомнатную квартиру в Люблино, бюджет до 18 миллионов рублей.", "Покажи только варианты с отделкой."])
    assert all(result.status == "completed" for result in results)
    assert results[-1].state.client_turn_count == 2
    assert p1.calls[1][0]["dialogue_history"]
    assert len(p2.calls[1][0]["property_material"]["facts"]) <= 3


def test_selected_property_does_not_substitute_identity():
    p1 = Port(_p1(facts=[{"name": "ЖК Северный тест", "developer": "Тестовый девелопер"}]))
    p2 = Port(_p2("По ЖК Северный тест доступны условия покупки."))
    result = _run(CoreRuntime(p1, p2, phone_backend=PhoneBackend()), ["Расскажи про условия покупки в ЖК Северный тест."])[0]
    assert result.status == "completed"
    assert p2.calls[0][0]["property_material"]["facts"][0]["name"] == "ЖК Северный тест"


def test_finance_consultation_avoids_min_fee_and_asks_phone_only_after_consent():
    finance = {"finance_preference": "mortgage_details"}
    p1 = Port(_p1(params=finance), _p1(params=finance), {"action": "request_phone"})
    p2 = Port(_p2("Специалист поможет рассчитать ипотеку."), _p2("Можно сравнить варианты финансирования."))
    results = _run(CoreRuntime(p1, p2, phone_backend=PhoneBackend()), [
        "Рассматриваю покупку квартиры в ипотеку, хочу понять доступный бюджет.",
        "Первоначальный взнос планирую около 30 процентов, важно снизить ежемесячный платёж.",
        "Да",
    ])
    assert all("min_fee" not in payload[0]["property_material"]["params"] for payload in p2.calls)
    assert all("finance_preference" in payload[0]["property_material"]["params"] for payload in p2.calls)
    assert not results[0].request_phone and not results[1].request_phone
    assert results[2].request_phone and results[2].model_calls == 1
    assert len(p2.calls) == 2


def test_direct_specialist_bypasses_both_prompts_and_requests_phone():
    p1, p2 = Port(), Port()
    result = _run(CoreRuntime(p1, p2, phone_backend=PhoneBackend()), ["Хочу поговорить со специалистом."])[0]
    assert result.status == "completed" and result.request_phone
    assert not p1.calls and not p2.calls
