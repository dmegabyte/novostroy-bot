from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nmbot_v2.contracts import ExecutableTurn, IntentGoal, OptionCard, Stage, TurnAction
from nmbot_v2.pair_comparison import execute_pair_comparison
from nmbot_v2.state import ConversationState, EnrichedCardCacheEntry, enriched_card_identity


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _turn(*names: str, goal: IntentGoal = IntentGoal.COMPARE_CURRENT, facts: tuple[str, ...] = ("parking_price",)) -> ExecutableTurn:
    return ExecutableTurn(
        goal=goal,
        stage=Stage.CURRENT_OPTIONS,
        action=TurnAction.ANSWER_FROM_CURRENT_OPTIONS,
        viewpoint="life",
        comparison_option_names=tuple(names),  # type: ignore[arg-type]
        facts_needed=facts,
    )


def _state() -> ConversationState:
    return ConversationState(
        visible_options=(
            OptionCard(name="ЖК Первый", location="Москва", price_min=10_000_000),
            OptionCard(name="ЖК Второй", location="Москва", price_min=11_000_000),
            OptionCard(name="ЖК Третий", location="Москва", price_min=12_000_000),
        )
    )


def _output_for(request_data: dict[str, Any], *, name: str, extra: dict[str, Any] | None = None) -> str:
    query = str(request_data.get("query") or "")
    envelope = json.loads(query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
    item = {"name": name, "location": "Москва", "min_price": 99_000_000, "parking_price": "от 1,9 млн"}
    item.update(extra or {})
    return json.dumps(
        {
            "facts": [item],
            "near": [],
            "missing": [],
            "params": {},
            "diagnostics": {
                "mcp_tool": "novostroym/get_flat_info",
                "response_viewpoint": envelope["response_viewpoint"],
                "base_viewpoint": envelope["base_viewpoint"],
                "requested_field_priorities": list(envelope.get("available_fact_fields") or [])[:12],
                "relaxation_audit": [],
                "ignored_preferences": [],
                "notes": [],
            },
        },
        ensure_ascii=False,
    )


def _name_from_request(request_data: dict[str, Any]) -> str:
    query = str(request_data.get("query") or "")
    for name in ("ЖК Первый", "ЖК Второй", "ЖК Третий"):
        if name in query:
            return name
    raise AssertionError("canonical name is absent from exact request")


def _envelope(request_data: dict[str, Any]) -> dict[str, Any]:
    query = str(request_data.get("query") or "")
    return json.loads(query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])


def _current_request(request_data: dict[str, Any]) -> dict[str, Any]:
    query = str(request_data.get("query") or "")
    return json.loads(query.split("Текущие параметры: ", 1)[1].split("\n", 1)[0])


def test_missing_invalid_and_non_visible_pair_fail_closed_without_gateway_calls() -> None:
    async def scenario(turn: ExecutableTurn) -> tuple[Any, list[dict[str, Any]]]:
        calls: list[dict[str, Any]] = []

        async def gateway(request_data: dict[str, Any]):
            calls.append(request_data)
            return "{}", {"ok": True}

        result = await execute_pair_comparison(turn, _state(), gateway, clock=lambda: NOW)
        return result, calls

    for bad_turn in (
        _turn("ЖК Первый"),
        _turn("ЖК Первый", "ЖК Третий", goal=IntentGoal.ANSWER_CURRENT),
        _turn("ЖК Первый", "ЖК Невидимый"),
        _turn("ЖК Первый", "ЖК Первый"),
    ):
        result, calls = asyncio.run(scenario(bad_turn))
        assert calls == []
        assert result.ordered_cards == ()
        assert result.cache_additions == ()
        assert result.error_status is not None
        safe = json.dumps({"meta": result.metadata, "attempts": result.attempts, "error": result.error_status}, ensure_ascii=False)
        assert "ЖК Первый" not in safe
        assert "ЖК Невидимый" not in safe


def test_arbitrary_first_and_third_visible_pair_preserves_declared_order_and_excludes_second() -> None:
    async def scenario() -> tuple[Any, list[dict[str, Any]]]:
        calls: list[dict[str, Any]] = []

        async def gateway(request_data: dict[str, Any]):
            calls.append(request_data)
            name = _name_from_request(request_data)
            return _output_for(request_data, name=name), {"ok": True}

        result = await execute_pair_comparison(_turn("ЖК Третий", "ЖК Первый"), _state(), gateway, clock=lambda: NOW)
        return result, calls

    result, calls = asyncio.run(scenario())

    assert [card.name for card in result.ordered_cards] == ["ЖК Третий", "ЖК Первый"]
    assert "ЖК Второй" not in [card.name for card in result.ordered_cards]
    assert len(calls) == 2
    assert { _name_from_request(call) for call in calls } == {"ЖК Первый", "ЖК Третий"}


def test_two_cache_hits_make_zero_gateway_calls_and_merge_with_base_cards() -> None:
    base = _state().visible_options
    entries = tuple(
        EnrichedCardCacheEntry(
            identity=enriched_card_identity(card),
            name=card.name,
            card=OptionCard(name=card.name, location="другая", price_min=99_000_000, parking_price=f"cache-{idx}"),
            scenario="life",
            loaded_facts=("parking_price",),
            fetched_at=NOW.isoformat(),
        )
        for idx, card in enumerate((base[0], base[2]), start=1)
    )
    state = ConversationState(visible_options=base, enriched_card_cache=entries)

    async def scenario() -> tuple[Any, list[dict[str, Any]]]:
        calls: list[dict[str, Any]] = []

        async def gateway(request_data: dict[str, Any]):
            calls.append(request_data)
            return "{}", {"ok": True}

        result = await execute_pair_comparison(_turn("ЖК Первый", "ЖК Третий"), state, gateway, clock=lambda: NOW)
        return result, calls

    result, calls = asyncio.run(scenario())

    assert calls == []
    assert [card.parking_price for card in result.ordered_cards] == ["cache-1", "cache-2"]
    assert [card.location for card in result.ordered_cards] == ["Москва", "Москва"]
    assert result.cache_additions == ()
    assert result.metadata["cache_hit_count"] == 2


def test_one_cache_hit_makes_exactly_one_exact_request() -> None:
    base = _state().visible_options
    entry = EnrichedCardCacheEntry(
        identity=enriched_card_identity(base[0]),
        name=base[0].name,
        card=OptionCard(name=base[0].name, parking_price="cached"),
        scenario="life",
        loaded_facts=("parking_price",),
        fetched_at=NOW.isoformat(),
    )
    state = ConversationState(visible_options=base, enriched_card_cache=(entry,))

    async def scenario() -> tuple[Any, list[dict[str, Any]]]:
        calls: list[dict[str, Any]] = []

        async def gateway(request_data: dict[str, Any]):
            calls.append(request_data)
            return _output_for(request_data, name=_name_from_request(request_data)), {"ok": True}

        result = await execute_pair_comparison(_turn("ЖК Первый", "ЖК Третий"), state, gateway, clock=lambda: NOW)
        return result, calls

    result, calls = asyncio.run(scenario())

    assert len(calls) == 1
    assert _name_from_request(calls[0]) == "ЖК Третий"
    assert _envelope(calls[0])["count"] == 1
    assert [card.name for card in result.ordered_cards] == ["ЖК Первый", "ЖК Третий"]
    assert len(result.cache_additions) == 1


def test_zero_cache_hits_make_two_concurrent_count_one_requests_and_never_request_third() -> None:
    async def scenario() -> tuple[Any, list[dict[str, Any]], bool]:
        calls: list[dict[str, Any]] = []
        both_started = asyncio.Event()
        concurrent = False

        async def gateway(request_data: dict[str, Any]):
            nonlocal concurrent
            calls.append(request_data)
            if len(calls) == 2:
                concurrent = True
                both_started.set()
            else:
                await both_started.wait()
            return _output_for(request_data, name=_name_from_request(request_data)), {"ok": True}

        result = await execute_pair_comparison(_turn("ЖК Первый", "ЖК Третий"), _state(), gateway, clock=lambda: NOW)
        return result, calls, concurrent

    result, calls, concurrent = asyncio.run(scenario())

    assert concurrent is True
    assert len(calls) == 2
    assert [_envelope(call)["count"] for call in calls] == [1, 1]
    assert { _name_from_request(call) for call in calls } == {"ЖК Первый", "ЖК Третий"}
    assert "ЖК Второй" not in "\n".join(str(call.get("query") or "") for call in calls)
    assert result.error_status is None


def test_both_success_returns_two_cache_additions_and_does_not_mutate_state() -> None:
    state = _state()

    async def scenario() -> Any:
        async def gateway(request_data: dict[str, Any]):
            return _output_for(request_data, name=_name_from_request(request_data)), {"ok": True}

        return await execute_pair_comparison(_turn("ЖК Первый", "ЖК Третий"), state, gateway, clock=lambda: NOW)

    result = asyncio.run(scenario())

    assert len(result.cache_additions) == 2
    assert [entry.name for entry in result.cache_additions] == ["ЖК Первый", "ЖК Третий"]
    assert all(entry.loaded_facts == ("parking_price",) for entry in result.cache_additions)
    assert all(entry.scenario == "life" for entry in result.cache_additions)
    assert all(entry.fetched_at == NOW.isoformat() for entry in result.cache_additions)
    assert state.enriched_card_cache == ()
    assert [card.name for card in state.visible_options] == ["ЖК Первый", "ЖК Второй", "ЖК Третий"]


def test_one_and_both_enrichment_failures_keep_grounded_base_cards_and_safe_status() -> None:
    async def scenario(failed_names: set[str]) -> Any:
        async def gateway(request_data: dict[str, Any]):
            name = _name_from_request(request_data)
            if name in failed_names:
                return "", {"ok": False, "raw_payload": "ЖК Первый secret", "query": name}
            return _output_for(request_data, name=name), {"ok": True}

        return await execute_pair_comparison(_turn("ЖК Первый", "ЖК Третий"), _state(), gateway, clock=lambda: NOW)

    partial = asyncio.run(scenario({"ЖК Первый"}))
    total = asyncio.run(scenario({"ЖК Первый", "ЖК Третий"}))

    assert [card.name for card in partial.ordered_cards] == ["ЖК Первый", "ЖК Третий"]
    assert partial.ordered_cards[0].parking_price is None
    assert partial.ordered_cards[1].parking_price == "от 1,9 млн"
    assert partial.error_status == "partial_enrichment_failed"
    assert len(partial.cache_additions) == 1
    assert [card.name for card in total.ordered_cards] == ["ЖК Первый", "ЖК Третий"]
    assert [card.parking_price for card in total.ordered_cards] == [None, None]
    assert total.error_status == "all_enrichment_failed"
    assert total.cache_additions == ()

    for result in (partial, total):
        safe = json.dumps({"meta": result.metadata, "attempts": result.attempts, "error": result.error_status}, ensure_ascii=False)
        assert "ЖК Первый" not in safe
        assert "ЖК Третий" not in safe
        assert "secret" not in safe
        assert "raw_payload" not in safe


def test_no_lot_examples_requested_by_default() -> None:
    calls: list[dict[str, Any]] = []

    async def scenario() -> Any:
        async def gateway(request_data: dict[str, Any]):
            calls.append(request_data)
            return _output_for(request_data, name=_name_from_request(request_data)), {"ok": True}

        return await execute_pair_comparison(_turn("ЖК Первый", "ЖК Третий", facts=()), _state(), gateway, clock=lambda: NOW)

    result = asyncio.run(scenario())

    assert result.error_status is None
    assert len(calls) == 2
    joined = "\n".join(str(call.get("query") or "") for call in calls)
    assert "lot_examples" not in joined
    assert '"lot_hard": {}' in joined
    for call in calls:
        envelope = _envelope(call)
        current = _current_request(call)
        assert "lot_examples" not in current["search_goal"]["explicit_terms"]
        assert "ads.fullprice" not in current["search_goal"]["explicit_terms"]
        assert envelope["lot_hard"] == {}
