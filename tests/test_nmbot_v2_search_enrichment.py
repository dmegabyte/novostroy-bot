from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nmbot_v2.contracts import OptionCard, SearchResult
from nmbot_v2.search_contract import V2SearchRequest, build_query
from nmbot_v2.search_enrichment import build_option_enrichment_repair_request, build_option_enrichment_request, build_recovery_search_request, enrich_search_result_top_options, fetch_enriched_option_v2, merge_option_cards, validate_with_bounded_enrichment


def _output_for(request_data: dict[str, Any], *, name: str = "ЖК Клиент", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    query = str(request_data.get("query") or "")
    envelope = json.loads(query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
    item = {"name": name, "location": "Москва", "developer": "ПИК", "school": True, "min_price": 12_000_000}
    item.update(extra or {})
    return {
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
    }


def test_enrichment_request_uses_compact_envelope_and_exact_client_name() -> None:
    request = build_option_enrichment_request(OptionCard(name="ЖК Клиент", location="Москва"), "family")

    assert request.count == 1
    assert request.preferences == {"format": "full_card"}
    assert request.requested_hard == {}
    assert request.effective_hard == {}
    assert "ЖК Клиент" in request.search_goal["query_summary"]
    assert "full_card" in request.search_goal["explicit_terms"]


def test_selected_enrichment_request_keeps_exact_count_one_and_bounded_facts() -> None:
    request = build_option_enrichment_request(OptionCard(name="Мичуринский парк"), "life", facts_needed=("parking_price", "bad_fact"))

    assert request.count == 1
    assert "Мичуринский парк" in request.search_goal["query_summary"]
    assert "parking_price" in request.search_goal["query_summary"]
    assert "parking_price" in request.search_goal["explicit_terms"]
    assert "bad_fact" not in request.search_goal["explicit_terms"]
    assert "parking_price" in request.available_fact_fields


def test_selected_enrichment_request_maps_lot_examples_to_wire_fields() -> None:
    request = build_option_enrichment_request(OptionCard(name="Томилинский бульвар"), "rental", facts_needed=("lot_examples",))

    assert "не создавай отдельное поле lot_examples" in request.search_goal["query_summary"]
    assert "ads.fullprice" in request.search_goal["explicit_terms"]
    assert "lot_examples" not in request.search_goal["explicit_terms"]
    assert "lot_examples" not in request.available_fact_fields
    assert "ads" in request.available_fact_fields
    assert "ads.fullprice" in request.available_fact_fields
    assert "ads.floor" in request.available_fact_fields
    assert "ads.floors_total" in request.available_fact_fields
    assert "house" in request.available_fact_fields


def test_selected_enrichment_request_carries_rooms_as_lot_hard_not_complex_hard() -> None:
    request = build_option_enrichment_request(OptionCard(name="Новое Видное"), "life", facts_needed=("lot_examples",), lot_hard={"rooms": 2})
    query = build_query(request)
    envelope = json.loads(query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
    current = json.loads(query.split("Текущие параметры: ", 1)[1].split("\n", 1)[0])

    assert request.requested_hard == {}
    assert request.effective_hard == {}
    assert request.lot_hard == {"rooms": 2}
    assert current["effective_hard"] == {}
    assert current["lot_hard"] == {"rooms": 2}
    assert envelope["hard_evidence_requirements"] == {}
    assert envelope["lot_hard_evidence_requirements"] == {"rooms": ["ads.rooms"]}


def test_exact_identity_accepted_and_canonical_fields_merged() -> None:
    base = OptionCard(name="ЖК Клиент", location="Москва", is_near=True)
    seen: list[dict[str, Any]] = []

    async def gateway(request_data: dict[str, Any]):
        seen.append(request_data)
        return json.dumps(_output_for(request_data), ensure_ascii=False), {"ok": True, "task_id": "hidden"}

    enriched, meta = asyncio.run(fetch_enriched_option_v2(base, "family", gateway))

    assert enriched.name == "ЖК Клиент"
    assert enriched.is_near is True
    assert enriched.developer == "ПИК"
    assert enriched.price_min == 12_000_000
    assert meta == {"applied": True, "source": "v2_search_enrichment"}
    assert "SEARCH_CONTRACT_ENVELOPE=" in seen[0]["query"]
    assert "Клиент: Найди полную структурированную карточку ровно для канонического ЖК «ЖК Клиент»" in seen[0]["query"]


def test_pair_comparison_hypothesis_enriches_only_two_exact_visible_cards() -> None:
    visible = SearchResult(
        facts=(
            OptionCard(name="Левел Лесной", location="Москва"),
            OptionCard(name="Томилинский бульвар", location="Томилино"),
            OptionCard(name="Третий ЖК", location="Москва"),
        )
    )
    seen: list[dict[str, Any]] = []

    async def gateway(request_data: dict[str, Any]):
        seen.append(request_data)
        query = str(request_data.get("query") or "")
        envelope = json.loads(query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
        name = next(name for name in ("Левел Лесной", "Томилинский бульвар") if name in query)
        return json.dumps(_output_for(request_data, name=name), ensure_ascii=False), {"ok": True}

    enriched, meta = asyncio.run(
        enrich_search_result_top_options(
            visible,
            "life",
            gateway,
            max_options=2,
            facts_needed=("parking_price",),
        )
    )

    assert [card.name for card in enriched.shortlist(3)] == ["Левел Лесной", "Томилинский бульвар", "Третий ЖК"]
    assert meta["count"] == 2
    assert meta["applied_count"] == 2, meta["items"]
    assert len(seen) == 2
    assert all('"count": 1' in request["query"] for request in seen)
    assert {name for request in seen for name in ("Левел Лесной", "Томилинский бульвар", "Третий ЖК") if name in request["query"]} == {
        "Левел Лесной",
        "Томилинский бульвар",
    }


def test_exact_selected_enrichment_preserves_lot_examples_base_identity_price_and_location() -> None:
    base = OptionCard(name="Томилинский бульвар", location="Томилино", price_min=7_500_000)

    async def gateway(request_data: dict[str, Any]):
        return json.dumps(
            _output_for(
                request_data,
                name="Томилинский бульвар",
                extra={
                    "location": "другая локация",
                    "min_price": 99_000_000,
                    "ads": [
                        {"id": 6375479, "rooms": "s", "area": 19, "floor": 6, "floors_total": 25, "fullprice": 8_133_900, "renovation": "с отделкой", "status": 2},
                        {"id": 5976219, "rooms": "1", "area": 32.8, "floor": 17, "floors_total": 25, "fullprice": 10_318_880, "renovation": "с отделкой", "status": 2},
                    ],
                    "house": [{"id": 5, "name": "5-8"}],
                },
            ),
            ensure_ascii=False,
        ), {"ok": True}

    enriched, meta = asyncio.run(fetch_enriched_option_v2(base, "rental", gateway, facts_needed=("lot_examples",)))

    assert meta["applied"] is True
    assert enriched.name == "Томилинский бульвар"
    assert enriched.location == "Томилино"
    assert enriched.price_min == 7_500_000
    assert len(enriched.lot_examples) == 2
    assert enriched.lot_examples[0].full_price == 8_133_900
    assert enriched.lot_examples[0].house_name is None


def test_exact_selected_lot_hard_rooms_filters_only_active_matching_lots() -> None:
    base = OptionCard(name="Новое Видное", location="Видное")

    async def gateway(request_data: dict[str, Any]):
        return json.dumps(
            _output_for(
                request_data,
                name="Новое Видное",
                extra={
                    "rooms": "1,3",
                    "ads": [
                        {"id": 101, "rooms": "2", "area": 54.2, "floor": 3, "floors_total": 17, "fullprice": 12_100_000, "status": "2"},
                        {"id": 102, "rooms": 2, "area": 58, "floor": 9, "floors_total": 17, "fullprice": 12_900_000, "status": 2},
                        {"id": 103, "rooms": "1", "area": 39, "fullprice": 9_900_000, "status": 2},
                        {"id": 104, "rooms": "2", "area": 60, "fullprice": 13_500_000, "status": 1},
                    ],
                },
            ),
            ensure_ascii=False,
        ), {"ok": True}

    enriched, meta = asyncio.run(fetch_enriched_option_v2(base, "life", gateway, facts_needed=("lot_examples",), lot_hard={"rooms": 2}))

    assert meta["applied"] is True
    assert enriched.name == "Новое Видное"
    assert [lot.id for lot in enriched.lot_examples] == [101, 102]
    assert all(str(lot.rooms) == "2" for lot in enriched.lot_examples)
    assert all(str(lot.status) == "2" for lot in enriched.lot_examples)


def test_exact_selected_lot_hard_without_matching_active_lot_falls_back_to_base() -> None:
    base = OptionCard(name="Новое Видное")

    async def gateway(request_data: dict[str, Any]):
        return json.dumps(
            _output_for(
                request_data,
                name="Новое Видное",
                extra={
                    "ads": [
                        {"id": 201, "rooms": "1", "fullprice": 9_900_000, "status": 2},
                        {"id": 202, "rooms": "2", "fullprice": 12_900_000, "status": 1},
                    ]
                },
            ),
            ensure_ascii=False,
        ), {"ok": True}

    enriched, meta = asyncio.run(fetch_enriched_option_v2(base, "life", gateway, facts_needed=("lot_examples",), lot_hard={"rooms": 2}))

    assert enriched == base
    assert meta["skipped"] == "empty_result"
    assert meta["empty_reason"] == "lot_hard_no_match"


def test_selected_correctable_parse_failure_retries_once_exact_same_constraints() -> None:
    base = OptionCard(name="Новое Видное", location="Видное")
    seen: list[dict[str, Any]] = []

    async def gateway(request_data: dict[str, Any]):
        seen.append(request_data)
        if len(seen) == 1:
            return "not json", {"ok": True}
        return json.dumps(
            _output_for(
                request_data,
                name="Новое Видное",
                extra={"ads": [{"id": 301, "rooms": "2", "fullprice": 12_900_000, "status": 2}]},
            ),
            ensure_ascii=False,
        ), {"ok": True}

    enriched, meta = asyncio.run(fetch_enriched_option_v2(base, "life", gateway, facts_needed=("lot_examples",), lot_hard={"rooms": 2}))

    assert enriched != base
    assert len(seen) == 2
    first_query = str(seen[0]["query"])
    second_query = str(seen[1]["query"])
    first_envelope = json.loads(first_query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
    second_envelope = json.loads(second_query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
    first_current = json.loads(first_query.split("Текущие параметры: ", 1)[1].split("\n", 1)[0])
    second_current = json.loads(second_query.split("Текущие параметры: ", 1)[1].split("\n", 1)[0])
    assert first_current["effective_hard"] == second_current["effective_hard"] == {}
    assert first_current["lot_hard"] == second_current["lot_hard"] == {"rooms": 2}
    assert first_envelope["count"] == second_envelope["count"] == 1
    assert "Новое Видное" in second_query
    assert "broad_candidate_collection" not in second_query
    assert meta["recovery"] == {"attempted": True, "count": 1, "classes": ["parse"], "final": "applied"}


def test_selected_valid_empty_no_retry_and_no_technical_masking() -> None:
    base = OptionCard(name="ЖК Клиент")
    calls = 0

    async def gateway(request_data: dict[str, Any]):
        nonlocal calls
        calls += 1
        data = _output_for(request_data, name="ЖК Клиент")
        data["facts"] = []
        return json.dumps(data, ensure_ascii=False), {"ok": True}

    enriched, meta = asyncio.run(fetch_enriched_option_v2(base, "family", gateway, facts_needed=("parking_price",)))

    assert calls == 1
    assert enriched == base
    assert meta["skipped"] == "empty_result"
    assert "recovery" not in meta


def test_selected_identity_mismatch_and_invalid_json_remain_not_publishable() -> None:
    base = OptionCard(name="ЖК Клиент")

    async def wrong_project(request_data: dict[str, Any]):
        return json.dumps(_output_for(request_data, name="ЖК Другой"), ensure_ascii=False), {"ok": True}

    async def invalid_then_invalid(_request_data: dict[str, Any]):
        return "{bad json", {"ok": True}

    wrong, wrong_meta = asyncio.run(fetch_enriched_option_v2(base, "life", wrong_project))
    invalid, invalid_meta = asyncio.run(fetch_enriched_option_v2(base, "life", invalid_then_invalid))

    assert wrong == base
    assert wrong_meta["skipped"] == "identity_mismatch"
    assert "recovery" not in wrong_meta
    assert invalid == base
    assert invalid_meta["skipped"] == "parse"
    assert invalid_meta["recovery"]["count"] == 1


def test_selected_repair_request_preserves_exact_request_and_broad_recovery_builder_unchanged() -> None:
    request = build_option_enrichment_request(OptionCard(name="Новое Видное"), "life", facts_needed=("lot_examples",), lot_hard={"rooms": 2})
    repaired = build_option_enrichment_repair_request(request, "contract")

    assert repaired.count == request.count == 1
    assert repaired.effective_hard == request.effective_hard == {}
    assert repaired.requested_hard == request.requested_hard == {}
    assert repaired.lot_hard == request.lot_hard == {"rooms": 2}
    assert "selected_exact_repair" in repaired.search_goal["explicit_terms"]
    assert "broad_candidate_collection" not in repaired.search_goal["explicit_terms"]


def test_wrong_project_rejected_without_fuzzy_match() -> None:
    base = OptionCard(name="ЖК Клиент", location="Москва")

    async def gateway(request_data: dict[str, Any]):
        return json.dumps(_output_for(request_data, name="ЖК Клиентский парк"), ensure_ascii=False), {"ok": True}

    enriched, meta = asyncio.run(fetch_enriched_option_v2(base, "family", gateway))

    assert enriched == base
    assert meta["skipped"] == "identity_mismatch"


def test_timeout_provider_invalid_json_and_contract_failure_return_base() -> None:
    base = OptionCard(name="ЖК Клиент", location="Москва")

    async def slow(_request_data: dict[str, Any]):
        await asyncio.sleep(0.05)
        return "{}", {"ok": True}

    async def provider(_request_data: dict[str, Any]):
        return "{}", {"ok": False, "task_id": "hidden"}

    async def invalid(_request_data: dict[str, Any]):
        return "not json", {"ok": True}

    async def contract(_request_data: dict[str, Any]):
        return json.dumps({"facts": [{"name": "ЖК Клиент", "secret": "x"}], "near": [], "missing": [], "params": {}, "diagnostics": {}}, ensure_ascii=False), {"ok": True}

    cases = [(slow, "timeout", 0.001), (provider, "provider", None), (invalid, "parse", None), (contract, "empty_enrichment", None)]
    for gateway, skipped, timeout in cases:
        enriched, meta = asyncio.run(fetch_enriched_option_v2(base, "family", gateway, timeout=timeout))
        assert enriched == base
        assert meta["skipped"] == skipped
        assert "task_id" not in json.dumps(meta, ensure_ascii=False)


def test_top3_order_preserved_and_limited() -> None:
    result = SearchResult(facts=(OptionCard(name="ЖК A"), OptionCard(name="ЖК B"), OptionCard(name="ЖК C"), OptionCard(name="ЖК D")))
    calls: list[str] = []

    async def gateway(request_data: dict[str, Any]):
        query = str(request_data.get("query") or "")
        name = query.split("канонического ЖК «", 1)[1].split("»", 1)[0]
        calls.append(name)
        return json.dumps(_output_for(request_data, name=name, extra={"developer": f"dev-{name}"}), ensure_ascii=False), {"ok": True}

    enriched, meta = asyncio.run(enrich_search_result_top_options(result, "life", gateway, max_options=3))

    assert calls == ["ЖК A", "ЖК B", "ЖК C"]
    assert [card.name for card in enriched.facts] == ["ЖК A", "ЖК B", "ЖК C", "ЖК D"]
    assert [card.developer for card in enriched.facts] == ["dev-ЖК A", "dev-ЖК B", "dev-ЖК C", None]
    assert meta["applied_count"] == 3


def test_top_options_enrichment_keeps_exact_and_near_containers_separate() -> None:
    result = SearchResult(
        facts=(OptionCard(name="ЖК Точный"),),
        near=(OptionCard(name="ЖК Почти один", is_near=True), OptionCard(name="ЖК Почти два", is_near=True)),
    )

    async def gateway(request_data: dict[str, Any]):
        query = str(request_data.get("query") or "")
        name = query.split("канонического ЖК «", 1)[1].split("»", 1)[0]
        return json.dumps(_output_for(request_data, name=name, extra={"school": True}), ensure_ascii=False), {"ok": True}

    enriched, meta = asyncio.run(enrich_search_result_top_options(result, "family", gateway, max_options=3))

    assert [card.name for card in enriched.facts] == ["ЖК Точный"]
    assert [card.name for card in enriched.near] == ["ЖК Почти один", "ЖК Почти два"]
    assert all(card.is_near for card in enriched.near)
    assert meta["applied_count"] == 3


def test_merge_preserves_validated_base_location_and_price() -> None:
    base = OptionCard(name="Полар", location="Северное Медведково", price_min=12_200_000)
    enriched = OptionCard(name="Полар", location="другая локация", price="от 36 млн", price_min=36_000_000, metro="Медведково")
    merged = merge_option_cards(base, enriched)
    assert merged.location == "Северное Медведково"
    assert merged.price_min == 12_200_000
    assert merged.price is None
    assert merged.metro == "Медведково"


def test_enrichment_reconciles_missing_only_when_all_cards_have_evidence() -> None:
    result = SearchResult(
        facts=(OptionCard(name="ЖК A"), OptionCard(name="ЖК B")),
        missing=("ads", "sales", "rooms"),
    )

    async def gateway(request_data: dict[str, Any]):
        query = str(request_data.get("query") or "")
        name = query.split("канонического ЖК «", 1)[1].split("»", 1)[0]
        extra = {"count_ads": 10, "rooms": "1,2"}
        if name == "ЖК A":
            extra["egrn_top_novos"] = {"sales": 5}
        return json.dumps(_output_for(request_data, name=name, extra=extra), ensure_ascii=False), {"ok": True}

    enriched, _meta = asyncio.run(enrich_search_result_top_options(result, "investment", gateway, max_options=2))

    assert "ads" not in enriched.missing
    assert "rooms" not in enriched.missing
    assert "sales" in enriched.missing


def _request_with_rooms() -> V2SearchRequest:
    return V2SearchRequest(
        search_goal={"entity_type": "new_building_flat", "query_summary": "Нужны двушки в Москве до 18 млн", "explicit_terms": ["rooms", "budget", "location"]},
        requested_hard={"rooms": [2], "location": ["Москва"], "max_price": 18_000_000},
        effective_hard={"rooms": [2], "district": "msk", "max_price": 18_000_000},
        preferences={"sort_hint": "family"},
        response_viewpoint="family",
        available_fact_fields=["name", "district", "location", "rooms", "min_price", "price1", "school", "developer", "why_close"],
        count=3,
    )


def test_recovery_query_removes_room_filter_from_every_executable_channel() -> None:
    request = _request_with_rooms()

    recovery = build_recovery_search_request(request, {"rooms"})
    executable = build_query(recovery).casefold()
    envelope = json.loads(executable.split("search_contract_envelope=", 1)[1].split("\n", 1)[0])
    current = json.loads(executable.split("текущие параметры: ", 1)[1].split("\n", 1)[0])
    client_line = executable.split("клиент: ", 1)[1].split("\n", 1)[0]

    assert "двуш" not in executable
    assert "rooms" not in current["requested_hard"]
    assert "rooms" not in current["effective_hard"]
    assert "rooms" not in envelope["hard_evidence_requirements"]
    assert "rooms=" not in client_line
    assert "max_price=18000000" in executable
    assert "district=msk" in executable
    assert recovery.relaxation_audit[-1]["original_requested_hard"]["rooms"] == [2]


def _valid_output(request: V2SearchRequest, facts: list[dict[str, Any]], *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "facts": facts,
        "near": [],
        "missing": ["rooms"],
        "params": params if params is not None else dict(request.effective_hard),
        "diagnostics": {
            "mcp_tool": "novostroym/get_flat_info",
            "response_viewpoint": request.response_viewpoint,
            "base_viewpoint": request.base_viewpoint,
            "requested_field_priorities": list(request.available_fact_fields)[:12],
            "relaxation_audit": list(request.relaxation_audit),
            "ignored_preferences": list(request.ignored_preferences),
            "notes": [],
        },
    }


def test_recovery_broad_search_enriches_five_and_returns_only_confirmed_original_hard() -> None:
    request = _request_with_rooms()
    initial = _valid_output(request, [{"name": "ЖК Строгий", "district": "msk", "location": "Москва", "min_price": 12_000_000, "school": True}])
    calls: list[dict[str, Any]] = []

    async def gateway(request_data: dict[str, Any]):
        calls.append(request_data)
        query = str(request_data.get("query") or "")
        envelope = json.loads(query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
        current = json.loads(query.split("Текущие параметры: ", 1)[1].split("\n", 1)[0])
        if "broad_candidate_collection" in query:
            assert envelope["count"] == 5
            assert current["effective_hard"] == {"district": "msk", "max_price": 18_000_000}
            assert current["preferences"] == {"sort_hint": "family"}
            assert "not client relaxation" in query
            facts = [{"name": f"ЖК {idx}", "district": "msk", "location": "Москва", "min_price": 12_000_000 + idx, "school": True} for idx in range(1, 6)]
            return json.dumps(_valid_output(V2SearchRequest(response_viewpoint="family", base_viewpoint=None, search_goal={}, requested_hard={"location": ["Москва"], "max_price": 18_000_000}, effective_hard={"district": "msk", "max_price": 18_000_000}, preferences={"sort_hint": "family"}, relaxation_audit=current["relaxation_audit"], available_fact_fields=request.available_fact_fields), facts, params={"district": "msk", "max_price": 18_000_000, "sort_hint": "family"}), ensure_ascii=False), {"ok": True}
        name = query.split("канонического ЖК «", 1)[1].split("»", 1)[0]
        rooms_by_name = {"ЖК 1": 2, "ЖК 2": 2, "ЖК 3": 1, "ЖК 4": None, "ЖК 5": None}
        extra: dict[str, Any] = {"district": "msk", "location": "Москва", "min_price": 12_000_000, "developer": "dev"}
        if rooms_by_name[name] is not None:
            extra["rooms"] = rooms_by_name[name]
        else:
            extra["developer_description"] = "В описании встречается 2 комнаты, но структурного поля rooms нет"
        return json.dumps(_output_for(request_data, name=name, extra=extra), ensure_ascii=False), {"ok": True}

    result, validation, meta = asyncio.run(validate_with_bounded_enrichment(initial, request, gateway, max_options=3))

    assert validation["ok"] is True
    assert result is not None
    assert [card.name for card in result.facts] == ["ЖК 1", "ЖК 2"]
    assert all(card.rooms == "2" for card in result.facts)
    assert result.near == ()
    assert result.params == request.effective_hard
    assert len([call for call in calls if "full_card" in str(call.get("query"))]) == 5
    assert meta["confirmed_count"] == 2


def test_recovery_zero_confirmed_returns_valid_empty_result() -> None:
    request = _request_with_rooms()
    initial = _valid_output(request, [{"name": "ЖК Строгий", "district": "msk", "location": "Москва", "min_price": 12_000_000}])

    async def gateway(request_data: dict[str, Any]):
        query = str(request_data.get("query") or "")
        if "broad_candidate_collection" in query:
            facts = [{"name": f"ЖК Нет {idx}", "district": "msk", "location": "Москва", "min_price": 12_000_000} for idx in range(1, 3)]
            return json.dumps(_valid_output(V2SearchRequest(response_viewpoint="family", search_goal={}, requested_hard={"location": ["Москва"], "max_price": 18_000_000}, effective_hard={"district": "msk", "max_price": 18_000_000}, preferences={"sort_hint": "family"}, available_fact_fields=request.available_fact_fields), facts, params={"district": "msk", "max_price": 18_000_000, "sort_hint": "family"}), ensure_ascii=False), {"ok": True}
        name = query.split("канонического ЖК «", 1)[1].split("»", 1)[0]
        return json.dumps(_output_for(request_data, name=name, extra={"district": "msk", "location": "Москва", "min_price": 12_000_000, "rooms": 1}), ensure_ascii=False), {"ok": True}

    result, validation, _meta = asyncio.run(validate_with_bounded_enrichment(initial, request, gateway, max_options=3))

    assert validation["ok"] is True
    assert result is not None
    assert result.facts == ()
    assert result.near == ()
    assert result.params == request.effective_hard


def test_non_recoverable_hard_violation_fails_without_gateway_call() -> None:
    request = _request_with_rooms()
    output = _valid_output(request, [{"name": "ЖК Дорогой", "district": "msk", "location": "Москва", "min_price": 22_000_000, "rooms": 2}])
    called = False

    async def gateway(_request_data: dict[str, Any]):
        nonlocal called
        called = True
        return "{}", {"ok": True}

    result, validation, meta = asyncio.run(validate_with_bounded_enrichment(output, request, gateway))

    assert result is None
    assert "fact_0_violates_hard:max_price" in validation["errors"]
    assert called is False
    assert meta["reason"] == "non_enrichable_contract_error"


def test_recovery_gateway_timeout_is_safe() -> None:
    request = _request_with_rooms()
    initial = _valid_output(request, [{"name": "ЖК Строгий", "district": "msk", "location": "Москва", "min_price": 12_000_000}])

    async def gateway(_request_data: dict[str, Any]):
        await asyncio.sleep(0.05)
        return "{}", {"ok": True}

    result, validation, meta = asyncio.run(validate_with_bounded_enrichment(initial, request, gateway, timeout=0.001))

    assert result is None
    assert validation["errors"] == ["recovery_gateway_timeout"]
    assert meta["skipped"] == "timeout"


def test_card_quality_enrichment_cannot_overwrite_already_validated_rooms() -> None:
    request = _request_with_rooms()
    initial = _valid_output(
        request,
        [{"name": "ЖК Валидный", "district": "msk", "location": "Москва", "min_price": 12_000_000, "rooms": 2}],
    )

    async def gateway(request_data: dict[str, Any]):
        return json.dumps(
            _output_for(
                request_data,
                name="ЖК Валидный",
                extra={"district": "msk", "location": "Москва", "min_price": 30_000_000, "rooms": 1, "developer": "ПИК"},
            ),
            ensure_ascii=False,
        ), {"ok": True}

    result, validation, meta = asyncio.run(validate_with_bounded_enrichment(initial, request, gateway, max_options=3))

    assert validation["ok"] is True
    assert result is not None
    assert result.facts[0].rooms == "2"
    assert result.facts[0].price_min == 12_000_000
    assert result.facts[0].developer == "ПИК"
    assert meta["trigger"] == "card_quality"
