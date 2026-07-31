from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nmbot_v2.contracts import ExecutableTurn, ExecutionResult, IntentGoal, OptionCard, SafeTurnContext, SearchResult, Stage, TurnAction
from nmbot_v2.pair_comparison import execute_pair_comparison
from nmbot_v2.response_composer import build_response_brief
from nmbot_v2.runtime import TurnProcessor
from nmbot_v2.state import ConversationState
import scripts.nmbot_runtime_adapter as runtime_adapter_mod
from scripts.nmbot_runtime_adapter import _OvermindSearchAdapter, _SemanticPlannerAdapter


class Planner:
    def __init__(self, plan: ExecutableTurn) -> None:
        self.plan_value = plan

    def plan(self, context: SafeTurnContext, state: ConversationState) -> ExecutableTurn:
        return self.plan_value


class PairSearch:
    def __init__(self, *, failed: set[str] | None = None) -> None:
        self.failed = failed or set()
        self.pair_calls = 0
        self.search_calls = 0

    def search(self, plan: Any, state: ConversationState, context: SafeTurnContext | None = None) -> SearchResult:
        self.search_calls += 1
        return SearchResult(facts=state.visible_options)

    def enrich_selected(self, option: OptionCard, state: ConversationState, plan: Any) -> OptionCard:
        return option

    async def enrich_pair(self, turn: ExecutableTurn, state: ConversationState) -> Any:
        self.pair_calls += 1

        async def gateway(request_data: dict[str, Any]):
            name = _name_from_request(request_data)
            if name in self.failed:
                return "", {"ok": False, "raw_payload": f"secret {name}"}
            return _output_for(name), {"ok": True}

        return await execute_pair_comparison(turn, state, gateway)


class SequenceGatewayClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.gateway_payloads: list[dict[str, Any]] = []

    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        self.gateway_payloads.append(request_data)
        response = self.responses.pop(0)
        return json.dumps({"facts": [response], "near": [], "missing": [], "params": {}}, ensure_ascii=False), {"ok": True, "attempts": [{"provider": "fake"}]}


def _state() -> ConversationState:
    return ConversationState(
        visible_options=(
            OptionCard(name="ЖК Первый", location="Москва", price_min=10_000_000),
            OptionCard(name="ЖК Второй", location="Москва", price_min=11_000_000),
            OptionCard(name="ЖК Третий", location="Москва", price_min=12_000_000),
        )
    )


def _pair_turn(*names: str) -> ExecutableTurn:
    return ExecutableTurn(
        goal=IntentGoal.COMPARE_CURRENT,
        stage=Stage.CURRENT_OPTIONS,
        action=TurnAction.ANSWER_FROM_CURRENT_OPTIONS,
        viewpoint="life",
        intent="life",
        comparison_option_names=tuple(names),  # type: ignore[arg-type]
        facts_needed=("parking_price",),
        query_text="сравни первый и третий",
    )


def _generic_compare_turn() -> ExecutableTurn:
    return ExecutableTurn(
        goal=IntentGoal.COMPARE_CURRENT,
        stage=Stage.CURRENT_OPTIONS,
        action=TurnAction.ANSWER_FROM_CURRENT_OPTIONS,
        viewpoint="life",
        intent="life",
        query_text="сравни варианты",
    )


def _output_for(name: str) -> str:
    return json.dumps({"facts": [{"name": name, "location": "Москва", "parking_price": "от 1,9 млн"}], "near": [], "missing": [], "params": {}}, ensure_ascii=False)


def _name_from_request(request_data: dict[str, Any]) -> str:
    query = str(request_data.get("query") or "")
    for name in ("ЖК Первый", "ЖК Второй", "ЖК Третий"):
        if name in query:
            return name
    raise AssertionError("pair request has no visible canonical name")


def _payload_count(request_data: dict[str, Any]) -> int:
    query = str(request_data.get("query") or "")
    envelope = json.loads(query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
    return int(envelope["count"])


def test_runtime_pair_projection_uses_exact_first_and_third_cards_and_merges_cache() -> None:
    search = PairSearch()
    turn = TurnProcessor(planner=Planner(_pair_turn("ЖК Первый", "ЖК Третий")), search_service=search).process(SafeTurnContext("u", "сравни первый и третий"), _state())

    assert search.pair_calls == 1
    assert [card.name for card in turn.execution.comparison_cards] == ["ЖК Первый", "ЖК Третий"]
    assert [card.name for card in turn.response_plan.cards] == ["ЖК Первый", "ЖК Третий"]
    assert "ЖК Второй" not in turn.response_text
    assert [item["name"] for item in turn.state["visible_options"]] == ["ЖК Первый", "ЖК Второй", "ЖК Третий"]
    assert len(turn.state["enriched_card_cache"]) == 2
    assert "ЖК Первый" not in json.dumps(turn.trace["runtime_summary"], ensure_ascii=False)
    assert turn.trace["runtime_summary"]["pair_comparison"]["requested_count"] == 2

    brief = build_response_brief(stage=turn.stage, plan=turn.semantic_plan, execution=turn.execution, delta=turn.state_delta, state=ConversationState.from_dict(turn.state), response_plan=turn.response_plan)
    assert [card.name for card in brief.canonical_cards] == ["ЖК Первый", "ЖК Третий"]


def test_generic_compare_without_pair_field_keeps_all_current_options_behavior() -> None:
    search = PairSearch()
    turn = TurnProcessor(planner=Planner(_generic_compare_turn()), search_service=search).process(SafeTurnContext("u", "сравни варианты"), _state())

    assert search.pair_calls == 0
    assert turn.execution.comparison_cards == ()
    assert [card.name for card in turn.response_plan.cards] == []
    assert [item["name"] for item in turn.state["visible_options"]] == ["ЖК Первый", "ЖК Второй", "ЖК Третий"]


def test_semantic_adapter_mocked_v3_pair_plan_routes_to_pair_path_and_generic_stays_all_list(monkeypatch) -> None:
    async def fake_plan_intent_v3(_session: Any, **kwargs: Any) -> dict[str, Any]:
        if "перв" in kwargs["user_text"]:
            return {
                "schema_version": 3,
                "goal": "compare_current",
                "viewpoint": "unchanged",
                "selected_option_name": None,
                "named_object_reference": None,
                "comparison_option_names": ["ЖК Первый", "ЖК Третий"],
                "requested_facts": [],
                "constraints_delta": {},
                "operator_consent": None,
                "explicit_operator_request": False,
                "clarification": None,
                "confidence": 1.0,
            }
        return {
            "schema_version": 3,
            "goal": "compare_current",
            "viewpoint": "unchanged",
            "selected_option_name": None,
            "named_object_reference": None,
            "comparison_option_names": [],
            "requested_facts": [],
            "constraints_delta": {},
            "operator_consent": None,
            "explicit_operator_request": False,
            "clarification": None,
            "confidence": 1.0,
        }

    monkeypatch.setattr(runtime_adapter_mod, "_intent_plan_version", lambda: "v3")
    monkeypatch.setattr(runtime_adapter_mod.followup_intent_classifier, "plan_intent_v3", fake_plan_intent_v3)

    state = _state()
    pair_search = PairSearch()
    pair_turn = TurnProcessor(
        planner=_SemanticPlannerAdapter({"overmind_client": object()}),
        search_service=pair_search,
    ).process(SafeTurnContext("u", "сравни первый и третий"), state)

    assert pair_search.pair_calls == 1
    assert [card.name for card in pair_turn.execution.comparison_cards] == ["ЖК Первый", "ЖК Третий"]
    assert [card.name for card in pair_turn.response_plan.cards] == ["ЖК Первый", "ЖК Третий"]
    assert "ЖК Второй" not in pair_turn.response_text

    generic_search = PairSearch()
    generic_turn = TurnProcessor(
        planner=_SemanticPlannerAdapter({"overmind_client": object()}),
        search_service=generic_search,
    ).process(SafeTurnContext("u", "сравни варианты"), state)

    assert generic_search.pair_calls == 0
    assert generic_turn.semantic_plan.comparison_option_names == ()
    assert generic_turn.execution.comparison_cards == ()
    assert [item["name"] for item in generic_turn.state["visible_options"]] == ["ЖК Первый", "ЖК Второй", "ЖК Третий"]


def test_adapter_enrich_pair_invokes_two_count_one_requests_and_no_third() -> None:
    async def scenario() -> tuple[Any, SequenceGatewayClient]:
        client = SequenceGatewayClient([
            {"name": "ЖК Первый", "location": "Москва", "parking_price": "от 1,9 млн"},
            {"name": "ЖК Третий", "location": "Москва", "parking_price": "от 2,1 млн"},
        ])
        adapter = _OvermindSearchAdapter({"overmind_client": client})
        result = await adapter.enrich_pair(_pair_turn("ЖК Первый", "ЖК Третий"), _state())
        return result, client

    result, client = asyncio.run(scenario())

    assert [card.name for card in result.ordered_cards] == ["ЖК Первый", "ЖК Третий"]
    assert len(client.gateway_payloads) == 2
    assert [_payload_count(payload) for payload in client.gateway_payloads] == [1, 1]
    assert "ЖК Второй" not in "\n".join(str(payload.get("query") or "") for payload in client.gateway_payloads)


def test_partial_pair_failure_keeps_two_cards_and_honest_caveat_without_global_fallback() -> None:
    search = PairSearch(failed={"ЖК Первый"})
    turn = TurnProcessor(planner=Planner(_pair_turn("ЖК Первый", "ЖК Третий")), search_service=search).process(SafeTurnContext("u", "сравни первый и третий"), _state())

    assert turn.execution.ok is True
    assert turn.execution.error_code == "partial_enrichment_failed"
    assert [card.name for card in turn.response_plan.cards] == ["ЖК Первый", "ЖК Третий"]
    assert turn.response_plan.caveat
    assert "Передать оператору запрос?" not in turn.response_text
    assert "ЖК Второй" not in turn.response_text


def test_both_pair_failures_still_bound_to_pair_cards_without_invented_operator_flow() -> None:
    search = PairSearch(failed={"ЖК Первый", "ЖК Третий"})
    turn = TurnProcessor(planner=Planner(_pair_turn("ЖК Первый", "ЖК Третий")), search_service=search).process(SafeTurnContext("u", "сравни первый и третий"), _state())

    assert turn.execution.ok is True
    assert turn.execution.error_code == "all_enrichment_failed"
    assert [card.name for card in turn.response_plan.cards] == ["ЖК Первый", "ЖК Третий"]
    assert turn.response_plan.operator_prompt is False
    assert "ЖК Второй" not in turn.response_text
