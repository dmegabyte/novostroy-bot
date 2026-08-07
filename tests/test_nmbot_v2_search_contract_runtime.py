from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nmbot_v2.contracts import IntentGoal, OptionCard, SafeTurnContext, SemanticPlan, Stage, TurnAction, ExecutableTurn
from nmbot_v2.constraints import normalize_constraints_delta
from nmbot_v2.search_contract import HARD_EVIDENCE_MAP, V2SearchRequest, build_candidate_retrieval_request, build_current_options_fact_check_request, build_query, build_search_request, matches_hard_constraint, normalize_and_validate_search_output, normalize_search_output, validate_current_options_fact_check_result, validate_search_output
from nmbot_v2.state import ConversationState
from scripts import nmbot_runtime_adapter as runtime_adapter


FIXTURE = Path(__file__).parent / "fixtures" / "v2_search_mcp_contract.json"
EXPECTED_CAO_DISTRICTS = [
    "Арбат", "Басманный", "Замоскворечье", "Красносельский", "Мещанский",
    "Пресненский", "Таганский", "Тверской", "Хамовники", "Якиманка",
]


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _output_for(request):
    return {
        "facts": [],
        "near": [],
        "missing": [],
        "params": dict(request.effective_hard),
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


def _compact_parts(query: str) -> tuple[dict, dict, str]:
    envelope = json.loads(query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
    params = json.loads(query.split("Текущие параметры: ", 1)[1].split("\n", 1)[0])
    client = query.split("\nКлиент: ", 1)[1].split("\n", 1)[0]
    return envelope, params, client


def test_builder_static_contract_accepts_all_15_fixture_shapes() -> None:
    assert callable(runtime_adapter._OvermindSearchAdapter.search)
    for scenario in _fixture()["scenarios"]:
        constraints = scenario["constraints"]
        plan = SemanticPlan(
            operation="search",
            intent="mortgage" if scenario["response_viewpoint"] == "financing" else scenario["response_viewpoint"],
            constraints_delta={
                "requested_hard": constraints["requested_hard"],
                "effective_hard": constraints["effective_hard"],
                "preferences": constraints["preferences"],
                "relaxation_audit": constraints["relaxation_audit"],
            },
        )
        state = ConversationState(active_topic=scenario.get("base_viewpoint"))
        request = build_search_request(plan, state, SafeTurnContext("fixture", scenario["search_goal"]["query_summary"]))
        output = _output_for(request)

        assert request.search_goal["query_summary"]
        query = build_query(request)
        assert query.count("SEARCH_CONTRACT_ENVELOPE=") == 1
        assert "Текущие параметры: " in query
        assert "\nКлиент: " in query
        assert "V2_SEARCH_INPUT" not in query
        assert validate_search_output(output, request)["ok"]


def test_compact_query_contains_envelope_current_params_and_natural_client_line() -> None:
    request = build_search_request(
        SemanticPlan(operation="search", constraints_delta={"hard": {"rooms": [2], "max_price": 18_000_000}}),
        ConversationState(),
        SafeTurnContext("u", "Нужна двушка до 18 млн, телефон +7 999 123-45-67"),
    )
    query = build_query(request)
    envelope, params, client = _compact_parts(query)

    assert query.count("SEARCH_CONTRACT_ENVELOPE=") == 1
    assert envelope["contract"] == "v2_search_mcp_contract"
    assert params["effective_hard"] == {"rooms": [2], "max_price": 18_000_000}
    assert client == "Нужна двушка до 18 млн, телефон [redacted-contact]"


def test_compact_query_excludes_legacy_input_and_full_payload_duplication() -> None:
    request = build_search_request(SemanticPlan(operation="search"), ConversationState(), SafeTurnContext("u", "найди варианты"))
    query = build_query(request)

    assert "V2_SEARCH_INPUT" not in query
    assert "V2_SEARCH_MCP_CONTRACT" not in query
    assert query.count('"output_top_level_keys"') == 1
    assert query.count('"available_fact_fields"') == 1


def test_hard_evidence_map_includes_only_active_hard_fields() -> None:
    request = build_search_request(
        SemanticPlan(operation="search", constraints_delta={"hard": {"rooms": [2], "ready": "delivered"}}),
        ConversationState(),
        SafeTurnContext("u", "готовая двушка"),
    )
    envelope, _params, _client = _compact_parts(build_query(request))

    assert set(envelope["hard_evidence_requirements"]) == {"rooms", "ready"}
    assert envelope["hard_evidence_requirements"]["rooms"] == HARD_EVIDENCE_MAP["rooms"]
    assert "max_price" not in envelope["hard_evidence_requirements"]


def test_broad_geo_maps_to_mcp_district_and_removes_broad_location() -> None:
    cases = [
        (["Москва"], "msk"),
        (["Новая Москва"], "newmsk"),
        (["Московская область"], "mo"),
        (["МО"], "mo"),
    ]
    for location, district in cases:
        request = build_search_request(
            SemanticPlan(operation="search", constraints_delta={"hard": {"location": location}}),
            ConversationState(),
            SafeTurnContext("u", str(location[0])),
        )
        assert request.requested_hard == {"location": location}
        assert request.effective_hard == {"district": district}


def test_broad_plus_specific_keeps_district_and_specific_locations_only() -> None:
    request = build_search_request(
        SemanticPlan(operation="search", constraints_delta={"hard": {"location": ["Москва", "Сокол", "Москва-Сити"]}}),
        ConversationState(),
        SafeTurnContext("u", "Москва, Сокол и Москва-Сити"),
    )

    assert request.requested_hard == {"location": ["Москва", "Сокол", "Москва-Сити"]}
    assert request.effective_hard == {"district": "msk", "location": ["Сокол", "Москва-Сити"]}


def test_center_alias_preserves_requested_and_expands_effective_location() -> None:
    request = build_search_request(
        SemanticPlan(operation="search", constraints_delta={"hard": {"location": "центр"}}),
        ConversationState(),
        SafeTurnContext("u", "центр"),
    )

    assert request.requested_hard == {"location": "центр"}
    assert request.effective_hard == {"location": EXPECTED_CAO_DISTRICTS}


def test_all_center_aliases_expand_to_cao_districts() -> None:
    for alias in ["центр", "центре", "центра", "центру", "центром", "цнетр", "цнетре", " центр  Москвы ", "ЦАО", "центральный административный округ", "center"]:
        request = build_search_request(
            SemanticPlan(operation="search", constraints_delta={"hard": {"location": alias}}),
            ConversationState(),
            SafeTurnContext("u", alias),
        )
        assert request.effective_hard == {"location": EXPECTED_CAO_DISTRICTS}


def test_center_alias_fuzzy_guard_rejects_unrelated_words() -> None:
    for location in ["централ", "метро", "левый центр"]:
        request = build_search_request(
            SemanticPlan(operation="search", constraints_delta={"hard": {"location": location}}),
            ConversationState(),
            SafeTurnContext("u", location),
        )
        assert request.effective_hard == {"location": location}


def test_mixed_center_alias_and_specific_locations_preserves_specific_without_duplicates() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "центр, Сокол и Басманный"},
        requested_hard={"location": ["центр", "Сокол", "Басманный", "Сокол"]},
        effective_hard={"location": ["центр", "Сокол", "Басманный", "Сокол"]},
    )

    assert request.requested_hard == {"location": ["центр", "Сокол", "Басманный", "Сокол"]}
    assert request.effective_hard == {"location": [*EXPECTED_CAO_DISTRICTS, "Сокол"]}


def test_center_alias_strict_matcher_accepts_cao_and_rejects_non_cao() -> None:
    expected = EXPECTED_CAO_DISTRICTS

    assert matches_hard_constraint({"location": "Басманный"}, "location", expected)
    assert matches_hard_constraint({"location": "Пресненский район"}, "location", expected)
    assert matches_hard_constraint({"location": "Москва", "district": "Басманный"}, "location", expected)
    assert not matches_hard_constraint({"location": "Лефортово"}, "location", expected)


def test_center_alias_validator_accepts_cao_fact_and_rejects_non_cao_fact() -> None:
    request = build_search_request(
        SemanticPlan(operation="search", constraints_delta={"hard": {"location": "ЦАО"}}),
        ConversationState(),
        SafeTurnContext("u", "ЦАО"),
    )
    request = V2SearchRequest(
        search_goal=request.search_goal,
        requested_hard=request.requested_hard,
        effective_hard=request.effective_hard,
        available_fact_fields=["id", "name", "location"],
    )
    accepted = _output_for(request)
    accepted["facts"] = [{"id": "cao", "name": "ЖК в ЦАО", "location": "Басманный"}]
    rejected = _output_for(request)
    rejected["facts"] = [{"id": "outside", "name": "ЖК вне ЦАО", "location": "Лефортово"}]

    assert validate_search_output(accepted, request)["ok"]
    rejected_result = validate_search_output(rejected, request)
    assert not rejected_result["ok"]
    assert "fact_0_violates_hard:location" in rejected_result["errors"]


def test_center_alias_validator_accepts_cao_district_as_location_evidence() -> None:
    request = build_search_request(
        SemanticPlan(operation="search", constraints_delta={"hard": {"location": "центр"}}),
        ConversationState(),
        SafeTurnContext("u", "центр"),
    )
    request = V2SearchRequest(
        search_goal=request.search_goal,
        requested_hard=request.requested_hard,
        effective_hard=request.effective_hard,
        available_fact_fields=["id", "name", "location", "district"],
    )
    output = _output_for(request)
    output["facts"] = [{"id": "cao", "name": "ЖК в ЦАО", "location": "Москва", "district": "Басманный"}]

    assert validate_search_output(output, request)["ok"]


def test_explicit_existing_district_remains_authoritative_for_broad_geo() -> None:
    request = build_search_request(
        SemanticPlan(operation="search", constraints_delta={"hard": {"district": "mo", "location": ["Москва", "Химки"]}}),
        ConversationState(),
        SafeTurnContext("u", "Москва или Химки"),
    )

    assert request.requested_hard == {"district": "mo", "location": ["Москва", "Химки"]}
    assert request.effective_hard == {"district": "mo", "location": ["Химки"]}


def test_base_search_has_nonempty_goal_and_empty_hard_filters() -> None:
    request = build_search_request(SemanticPlan(operation="search"), ConversationState(), SafeTurnContext("u", ""))

    assert request.search_goal["query_summary"]
    assert request.requested_hard == {}
    assert request.effective_hard == {}


def test_refinement_preserves_existing_constraints_and_applies_delta_only() -> None:
    state = ConversationState(params={"rooms": [2], "location": ["Сокол"]})
    request = build_search_request(SemanticPlan(operation="refine_search", constraints_delta={"hard": {"max_price": 18_000_000}}), state, SafeTurnContext("u", "до 18 млн"))

    assert request.effective_hard == {"rooms": [2], "location": ["Сокол"], "max_price": 18_000_000}


def test_candidate_retrieval_request_removes_only_specific_location_without_mutating_strict() -> None:
    strict = build_search_request(
        SemanticPlan(operation="search", constraints_delta={"hard": {"location": ["Зеленоград"], "rooms": [2], "max_price": 18_000_000}}),
        ConversationState(),
        SafeTurnContext("u", "В Зеленограде есть двушки до 18 млн?"),
    )

    retrieval = build_candidate_retrieval_request(strict)

    assert strict.requested_hard == {"location": ["Зеленоград"], "rooms": [2], "max_price": 18_000_000}
    assert strict.effective_hard == {"location": ["Зеленоград"], "rooms": [2], "max_price": 18_000_000}
    assert retrieval.requested_hard == {"rooms": [2], "max_price": 18_000_000}
    assert retrieval.effective_hard == {"rooms": [2], "max_price": 18_000_000}
    assert retrieval.search_goal["query_summary"] == "В Зеленограде есть двушки до 18 млн?"
    assert retrieval.search_goal["internal_candidate_retrieval"] == {"enabled": True, "field": "location", "client_relaxation": False}


def test_candidate_retrieval_generic_expand_uses_safe_location_query_from_state() -> None:
    strict = build_search_request(
        SemanticPlan(operation="search", fresh_search=True),
        ConversationState(params={"location": ["Зеленоград"]}),
        SafeTurnContext("u", "ищи лучше"),
    )

    retrieval = build_candidate_retrieval_request(strict)

    assert retrieval.effective_hard == {}
    assert retrieval.search_goal["query_summary"] == "Новостройки в локации «Зеленоград»: какие варианты есть?"


def test_candidate_retrieval_canonicalizes_cao_only_observed_center_flow() -> None:
    strict = build_search_request(
        SemanticPlan(operation="search", constraints_delta={"hard": {"location": "center"}}),
        ConversationState(),
        SafeTurnContext("u", "квартиру в цнетре"),
    )

    retrieval = build_candidate_retrieval_request(strict)
    query = build_query(retrieval)

    assert strict.effective_hard == {"location": EXPECTED_CAO_DISTRICTS}
    assert retrieval.effective_hard == {}
    assert "ЦАО" in query
    assert "цнетре" not in query
    assert "center" not in query


def test_candidate_retrieval_keeps_mixed_center_and_specific_query_behavior() -> None:
    strict = V2SearchRequest(
        search_goal={"query_summary": "квартиру в цнетре и Соколе"},
        requested_hard={"location": ["center", "Сокол"]},
        effective_hard={"location": ["center", "Сокол"]},
    )

    retrieval = build_candidate_retrieval_request(strict)

    assert strict.effective_hard == {"location": [*EXPECTED_CAO_DISTRICTS, "Сокол"]}
    assert retrieval.effective_hard == {}
    assert retrieval.search_goal["query_summary"] == "квартиру в цнетре и Соколе"


def test_candidate_first_does_not_apply_to_broad_alias_named_lookup_or_current_options() -> None:
    broad_alias = build_search_request(SemanticPlan(operation="search", constraints_delta={"hard": {"location": ["МО"]}}), ConversationState(), SafeTurnContext("u", "в МО"))
    named = build_search_request(SemanticPlan(operation="lookup_object", reference="ЖК Лучи", constraints_delta={"hard": {"location": ["Зеленоград"]}}), ConversationState(), SafeTurnContext("u", "ЖК Лучи"))
    current = build_current_options_fact_check_request((OptionCard(name="ЖК Один"),), ("location",), "life")

    assert build_candidate_retrieval_request(broad_alias) is broad_alias
    assert build_candidate_retrieval_request(named) is named
    assert build_candidate_retrieval_request(current) is current


def test_expand_search_null_location_preserves_state_for_v2_and_v3_but_refine_can_change_location() -> None:
    state = ConversationState(params={"location": ["Зеленоград"], "rooms": [2]})
    v2 = SemanticPlan(operation="search", fresh_search=True, constraints_delta={"hard": {"location": None}})
    v3 = ExecutableTurn(goal=IntentGoal.EXPAND_SEARCH, stage=Stage.REFINEMENT, action=TurnAction.SEARCH, constraints_delta={"hard": {"location": None}}, fresh_search=True)
    refine = SemanticPlan(operation="refine_search", constraints_delta={"hard": {"location": ["Сокол"]}})

    assert normalize_constraints_delta(v2.constraints_delta) == {}
    assert build_search_request(v2, state, SafeTurnContext("u", "ищи лучше")).effective_hard == {"location": ["Зеленоград"], "rooms": [2]}
    assert build_search_request(v3, state, SafeTurnContext("u", "ищи лучше")).effective_hard == {"location": ["Зеленоград"], "rooms": [2]}
    assert build_search_request(refine, state, SafeTurnContext("u", "теперь Сокол")).effective_hard == {"location": ["Сокол"], "rooms": [2]}


def test_financing_overlay_preserves_family_base_viewpoint() -> None:
    state = ConversationState(active_topic="family", params={"rooms": [2]})
    request = build_search_request(SemanticPlan(operation="search", intent="mortgage", constraints_delta={"preferences": {"finance_preference": "mortgage_details"}}), state, SafeTurnContext("u", "а ипотека есть?"))

    assert request.response_viewpoint == "financing"
    assert request.base_viewpoint == "family"
    assert "school" in request.available_fact_fields
    assert "mortgage_calc" in request.available_fact_fields


def test_h054_scenario_needs_union_fields_without_hard_or_params() -> None:
    request = build_search_request(
        SemanticPlan(operation="search", intent="family", facets=["family", "rental", "financing"], constraints_delta={"hard": {"rooms": [2]}}),
        ConversationState(active_topic="family"),
        SafeTurnContext("u", "для семьи под аренду и в ипотеку, двушка"),
    )
    normalized = normalize_search_output({"facts": [], "near": [], "missing": [], "params": {}, "diagnostics": {}}, request)
    query = build_query(request)
    _envelope, params, _client = _compact_parts(query)

    assert request.response_viewpoint == "family"
    assert request.scenario_needs == ("family", "rental", "financing")
    assert "school" in request.available_fact_fields
    assert "ads" in request.available_fact_fields
    assert "mortgage_calc" in request.available_fact_fields
    assert request.effective_hard == {"rooms": [2]}
    assert "scenario_needs" not in params
    assert "family" not in params["effective_hard"]
    priorities = normalized["diagnostics"]["requested_field_priorities"]
    assert priorities.index("school") < priorities.index("ads") < priorities.index("mortgage_calc")
    assert "для семьи под аренду и в ипотеку" in query


def test_unknown_preferences_are_dropped_and_diagnosed() -> None:
    request = build_search_request(SemanticPlan(operation="search", constraints_delta={"preferences": {"sort_hint": "price", "unsupported_sensitive_hint": "raw"}}), ConversationState(), SafeTurnContext("u", "найди"))

    assert request.preferences == {"sort_hint": "price"}
    assert request.ignored_preferences == ["unsupported_sensitive_hint"]


def test_missing_evidence_does_not_validate_inventory_absence_claim() -> None:
    request = build_search_request(SemanticPlan(operation="search", constraints_delta={"hard": {"rooms": [3], "ready": "delivered", "finishing": True}}), ConversationState(), SafeTurnContext("u", "готовое с отделкой"))
    output = _output_for(request)
    output["missing"] = [{"field": "ready", "reason_code": "absence_claim"}]

    result = validate_search_output(output, request)

    assert not result["ok"]
    assert "absence_claim_without_hard_evidence" in result["errors"]


def test_normalize_search_output_repairs_family_financing_overlay_diagnostics_only() -> None:
    request = build_search_request(
        SemanticPlan(
            operation="search",
            intent="mortgage",
            constraints_delta={
                "hard": {"rooms": [2]},
                "preferences": {"finance_preference": "mortgage_details", "unsupported_sensitive_hint": "raw-client-value"},
                "relaxation_audit": [{"field": "max_price", "from": 12_000_000, "to": 13_000_000}],
            },
        ),
        ConversationState(active_topic="family"),
        SafeTurnContext("u", "а ипотека есть для семейного варианта?"),
    )
    raw = {
        "facts": [{"id": "ok", "name": "Семейный ЖК", "rooms": [2], "mortgage_calc": {"payment": 100000}, "school": True}],
        "near": [],
        "missing": [],
        "params": {"rooms": [2], "finance_preference": "mortgage_details"},
        "diagnostics": {
            "mcp_tool": "wrong/tool",
            "response_viewpoint": "life",
            "base_viewpoint": "investment",
            "requested_field_priorities": [],
            "relaxation_audit": [],
            "ignored_preferences": [],
            "notes": ["model note"],
        },
        "response": "forbidden top-level model text",
    }

    normalized = normalize_search_output(raw, request)
    validation = validate_search_output(normalized, request)

    assert validation["ok"], validation["errors"]
    assert set(normalized) == {"facts", "near", "missing", "params", "diagnostics"}
    assert normalized["facts"] == raw["facts"]
    assert normalized["near"] == raw["near"]
    assert normalized["missing"] == raw["missing"]
    assert normalized["params"] == {"rooms": [2], "finance_preference": "mortgage_details"}
    diagnostics = normalized["diagnostics"]
    assert diagnostics["mcp_tool"] == "novostroym/get_flat_info"
    assert diagnostics["response_viewpoint"] == "financing"
    assert diagnostics["base_viewpoint"] == "family"
    assert diagnostics["relaxation_audit"] == request.relaxation_audit
    assert diagnostics["ignored_preferences"] == ["unsupported_sensitive_hint"]
    assert "mortgage_calc" in diagnostics["requested_field_priorities"]
    assert "school" in diagnostics["requested_field_priorities"]


def test_runtime_owns_params_and_drops_model_inferred_geo() -> None:
    request = V2SearchRequest(
        search_goal={"entity_type": "new_building_flat", "query_summary": "family search", "explicit_terms": ["family"]},
        requested_hard={"rooms": [2]},
        effective_hard={"rooms": [2]},
        preferences={},
        response_viewpoint="family",
        available_fact_fields=["name", "rooms", "district"],
        count=3,
    )
    raw = {
        "facts": [],
        "near": [],
        "missing": [],
        "params": {"rooms": [2], "district": "msk"},
        "diagnostics": {},
    }

    normalized = normalize_search_output(raw, request)

    assert normalized["params"] == {"rooms": [2]}
    assert "ignored_param:district" in normalized["diagnostics"]["notes"]


def test_normalize_search_output_removes_unknown_fact_fields_and_degrades() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "найди"},
        available_fact_fields=["id", "name", "location"],
    )
    raw = {
        "facts": [{"id": "a", "name": "ЖК А", "location": "Москва", "secret_score": "raw"}],
        "near": [],
        "missing": [],
        "params": {},
        "diagnostics": {},
    }

    normalized = normalize_search_output(raw, request)
    validation = validate_search_output(normalized, request)

    assert validation["ok"] is True
    assert validation["status"] == "degraded"
    assert validation["warnings"] == ["unknown_fact_fields_removed"]
    assert normalized["facts"] == [{"id": "a", "name": "ЖК А", "location": "Москва"}]


def test_normalize_search_output_reports_missing_hard_evidence_without_demoting() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "двушка"},
        requested_hard={"rooms": [2]},
        effective_hard={"rooms": [2]},
        available_fact_fields=["id", "name", "rooms", "location"],
    )
    raw = {
        "facts": [{"id": "a", "name": "ЖК А", "location": "Москва"}],
        "near": [],
        "missing": [],
        "params": {},
        "diagnostics": {},
    }

    normalized = normalize_search_output(raw, request)
    validation = validate_search_output(normalized, request)

    assert validation["ok"] is False
    assert validation["status"] == "invalid"
    assert "fact_missing_hard_evidence_reported" in validation["warnings"]
    assert "fact_0_missing_hard_evidence:rooms" in validation["errors"]
    assert normalized["facts"][0]["name"] == "ЖК А"
    assert normalized["near"] == []


def test_normalize_search_output_reports_hard_violation_without_dropping_fact() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "до 18 млн"},
        requested_hard={"max_price": 18_000_000},
        effective_hard={"max_price": 18_000_000},
        available_fact_fields=["id", "name", "min_price"],
    )
    raw = {
        "facts": [{"id": "expensive", "name": "ЖК Дорогой", "min_price": 25_000_000}],
        "near": [],
        "missing": [],
        "params": {},
        "diagnostics": {},
    }

    normalized = normalize_search_output(raw, request)
    validation = validate_search_output(normalized, request)

    assert validation["ok"] is False
    assert validation["status"] == "invalid"
    assert "fact_violates_hard_reported" in validation["warnings"]
    assert "fact_0_violates_hard:max_price" in validation["errors"]
    assert normalized["facts"][0]["id"] == "expensive"


def test_normalize_search_output_malformed_list_container_is_invalid() -> None:
    request = V2SearchRequest(search_goal={"query_summary": "найди"}, available_fact_fields=["id", "name"])
    raw = {"facts": {"id": "bad"}, "near": [], "missing": [], "params": {}, "diagnostics": {}}

    normalized = normalize_search_output(raw, request)
    validation = validate_search_output(normalized, request)

    assert validation["ok"] is False
    assert validation["status"] == "invalid"
    assert validation["errors"] == ["facts_must_be_list"]


def test_normalize_search_output_valid_shortlist_stays_valid() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "двушка"},
        requested_hard={"rooms": [2]},
        effective_hard={"rooms": [2]},
        available_fact_fields=["id", "name", "rooms", "location"],
    )
    raw = {
        "facts": [{"id": "ok", "name": "ЖК Ок", "rooms": [1, 2], "location": "Москва"}],
        "near": [],
        "missing": [],
        "params": {},
        "diagnostics": {},
    }

    normalized = normalize_search_output(raw, request)
    validation = validate_search_output(normalized, request)

    assert validation["ok"] is True
    assert validation["status"] == "valid"
    assert validation["warnings"] == []
    assert normalized["facts"][0]["name"] == "ЖК Ок"


def test_normalize_and_validate_search_output_matches_direct_composition_for_valid_case() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "двушка"},
        requested_hard={"rooms": [2]},
        effective_hard={"rooms": [2]},
        available_fact_fields=["id", "name", "rooms", "location"],
    )
    raw = {
        "facts": [{"id": "ok", "name": "ЖК Ок", "rooms": [1, 2], "location": "Москва"}],
        "near": [],
        "missing": [],
        "params": {},
        "diagnostics": {},
    }

    direct_normalized = normalize_search_output(raw, request)
    direct_validation = validate_search_output(direct_normalized, request)
    adapter_normalized, adapter_validation = normalize_and_validate_search_output(raw, request)

    assert adapter_normalized == direct_normalized
    assert adapter_validation == direct_validation
    assert adapter_validation["ok"] is True


def test_normalize_and_validate_search_output_matches_direct_composition_for_legacy_missing_semantic_failure() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "двушка"},
        requested_hard={"rooms": [2]},
        effective_hard={"rooms": [2]},
        available_fact_fields=["id", "name", "rooms", "location"],
    )
    raw = {
        "facts": {"id": "legacy-container", "name": "ЖК Legacy"},
        "near": [],
        "missing": ["hard evidence missing", {"field": "rooms", "reason": "legacy missing"}],
        "params": {},
        "diagnostics": {},
    }

    direct_normalized = normalize_search_output(raw, request)
    direct_validation = validate_search_output(direct_normalized, request)
    adapter_normalized, adapter_validation = normalize_and_validate_search_output(raw, request)

    assert adapter_normalized == direct_normalized
    assert adapter_validation == direct_validation
    assert adapter_validation["ok"] is False
    assert "facts_must_be_list" in adapter_validation["errors"]
    assert adapter_normalized["missing"] == ["hard_evidence_missing", {"field": "rooms", "reason_code": "requested_but_unconfirmed"}]


def _rooms_request(expected_rooms):
    return build_search_request(
        SemanticPlan(operation="search", constraints_delta={"hard": {"rooms": expected_rooms}}),
        ConversationState(),
        SafeTurnContext("u", "нужна квартира по комнатам"),
    )


def _validate_single_room_fact(actual_rooms, expected_rooms=None):
    expected_rooms = [2] if expected_rooms is None else expected_rooms
    request = _rooms_request(expected_rooms)
    output = _output_for(request)
    output["facts"] = [{"id": "rooms-evidence", "rooms": actual_rooms}]
    return validate_search_output(output, request)


def test_room_hard_match_accepts_scalar_string_room() -> None:
    result = _validate_single_room_fact("2")

    assert result["ok"], result["errors"]


def test_room_hard_match_accepts_comma_string_with_studios() -> None:
    result = _validate_single_room_fact("1, 2, 3, студии")

    assert result["ok"], result["errors"]


def test_room_hard_match_accepts_russian_two_room_label() -> None:
    result = _validate_single_room_fact("2-комнатные")

    assert result["ok"], result["errors"]


def test_room_hard_match_accepts_mixed_nested_structured_rooms() -> None:
    result = _validate_single_room_fact(["студии", {"available": ["2-комнатные", 3]}, 10])

    assert result["ok"], result["errors"]


def test_room_hard_match_rejects_studios_only_for_two_room_request() -> None:
    result = _validate_single_room_fact("студии", [2])

    assert not result["ok"]
    assert "fact_0_violates_hard:rooms" in result["errors"]


def test_room_hard_match_does_not_use_substrings_or_prose_as_evidence() -> None:
    ten_rooms = _validate_single_room_fact("10-комнатные", [1])
    request = _rooms_request([2])
    output = _output_for(request)
    output["facts"] = [{"id": "prose-only", "description": "есть 2-комнатные"}]
    prose_only = validate_search_output(output, request)

    assert not ten_rooms["ok"]
    assert "fact_0_violates_hard:rooms" in ten_rooms["errors"]
    assert not prose_only["ok"]
    assert "fact_0_violates_hard:rooms" in prose_only["errors"]


def test_room_hard_match_accepts_canonical_room_formats_only() -> None:
    assert matches_hard_constraint({"room_formats": ["1", "2", "3", "студии"]}, "rooms", [2])
    assert not matches_hard_constraint({"room_formats": ["1", "студии"]}, "rooms", [2])


def test_finishing_hard_match_uses_only_recognized_structured_evidence() -> None:
    assert matches_hard_constraint({"finishing": True}, "finishing", True)
    assert matches_hard_constraint({"finishing": "white box"}, "finishing", True)
    assert matches_hard_constraint({"ads": [{"renovation": "с отделкой"}]}, "finishing", True)
    assert matches_hard_constraint({"house": {"finishing_list": ["предчистовая"]}}, "finishing", True)
    assert not matches_hard_constraint({"finishing": 1}, "finishing", True)
    assert not matches_hard_constraint({"finishing": "без отделки"}, "finishing", True)
    assert not matches_hard_constraint({"finishing": "raw_enum"}, "finishing", True)


def _ready_request(expected="delivered"):
    return build_search_request(
        SemanticPlan(operation="search", constraints_delta={"hard": {"ready": expected}}),
        ConversationState(),
        SafeTurnContext("u", "нужен готовый дом"),
    )


def _validate_single_ready_fact(fact: dict, expected="delivered"):
    request = _ready_request(expected)
    output = _output_for(request)
    output["facts"] = [fact]
    return validate_search_output(output, request)


def test_ready_delivered_hard_match_accepts_structured_strings_bool_state_and_status() -> None:
    facts = [
        {"id": "ru-sdan", "ready": "сдан"},
        {"id": "ru-dom-sdan", "ready": "дом сдан"},
        {"id": "ru-gotov", "ready": "готов"},
        {"id": "en-delivered", "ready": "delivered"},
        {"id": "bool-delivered", "delivered": True},
        {"id": "numeric-delivered", "delivered": 1},
        {"id": "state-delivered", "state": "сдано"},
        {"id": "status-ready", "status": "ready"},
    ]

    for fact in facts:
        result = _validate_single_ready_fact(fact)
        assert result["ok"], (fact, result["errors"])


def test_ready_delivered_hard_match_rejects_future_quarter_and_construction_states() -> None:
    facts = [
        {"id": "future-year", "ready": "2027 г., 2 квартал"},
        {"id": "quarter-only", "ready": "2 квартал"},
        {"id": "under-construction", "ready": "строится"},
        {"id": "planned-status", "status": "planned"},
        {"id": "future-delivery", "state": "будет сдан в 2027"},
        {"id": "false-delivered", "delivered": False},
        {"id": "numeric-not-delivered", "delivered": 0},
    ]

    for fact in facts:
        result = _validate_single_ready_fact(fact)
        assert not result["ok"], fact
        assert "fact_0_violates_hard:ready" in result["errors"]


def test_ready_delivered_hard_match_ignores_unstructured_prose() -> None:
    result = _validate_single_ready_fact({"id": "prose", "description": "дом сдан"})

    assert not result["ok"]
    assert "fact_0_violates_hard:ready" in result["errors"]


def test_typed_request_always_normalizes_broad_new_moscow_geography() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "квартира в Новой Москве"},
        requested_hard={"location": ["Новая Москва"]},
        effective_hard={"location": ["Новая Москва"]},
    )

    assert request.requested_hard == {"location": ["Новая Москва"]}
    assert request.effective_hard == {"district": "newmsk"}


def test_ready_non_delivered_value_preserves_exact_existing_behavior() -> None:
    exact = _validate_single_ready_fact({"id": "exact", "ready": "строится"}, expected="строится")
    bool_only = _validate_single_ready_fact({"id": "bool", "delivered": True}, expected="строится")

    assert exact["ok"], exact["errors"]
    assert not bool_only["ok"]
    assert "fact_0_violates_hard:ready" in bool_only["errors"]


def test_fresh_search_excludes_visible_and_previous_options_in_prompt_and_output() -> None:
    state = ConversationState(
        visible_options=(OptionCard(name="Лосиноостровский парк"), OptionCard(name="Мичуринский парк")),
        previous_options=(OptionCard(name="Бусиновский парк"),),
    )
    request = build_search_request(
        SemanticPlan(operation="search", fresh_search=True),
        state,
        SafeTurnContext("u", "покажи другие, эти не повторяй"),
    )

    assert request.excluded_names == (
        "Лосиноостровский парк",
        "Мичуринский парк",
        "Бусиновский парк",
    )
    query = build_query(request)
    assert '"excluded_names"' in query
    assert "Не возвращай объекты из excluded_names" in query

    normalized = normalize_search_output(
        {
            "facts": [
                {"id": "old", "name": "ЖК «Лосиноостровский парк»"},
                {"id": "new", "name": "Кронштадтский 9"},
            ],
            "near": [{"id": "old-near", "name": "Мичуринский парк"}],
            "missing": [],
            "params": {},
            "diagnostics": {},
        },
        request,
    )

    assert [item["name"] for item in normalized["facts"]] == ["ЖК «Лосиноостровский парк»", "Кронштадтский 9"]
    assert [item["name"] for item in normalized["near"]] == ["Мичуринский парк"]
    assert "contract_warning:previous_option_reported" in normalized["diagnostics"]["notes"]


def test_near_output_gets_deterministic_structured_difference() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "студия до 9 млн с отделкой"},
        requested_hard={"rooms": "studio", "max_price": 9_000_000, "finishing": True},
        effective_hard={"rooms": "studio", "max_price": 9_000_000, "finishing": True},
        available_fact_fields=["id", "name", "rooms", "min_price", "finishing"],
    )
    normalized = normalize_search_output(
        {
            "facts": [],
            "near": [{"id": "near", "name": "Ближайший", "rooms": [1], "min_price": 8_000_000}],
            "missing": [],
            "params": {},
            "diagnostics": {},
        },
        request,
    )

    assert normalized["near"][0]["why_close"] == "другая комнатность; отделка не подтверждена"
    assert normalized["near"][0]["differences"] == ["rooms", "finishing"]


def test_near_output_replaces_model_why_close_with_structured_difference() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "двушка"},
        requested_hard={"rooms": [2]},
        effective_hard={"rooms": [2]},
        available_fact_fields=["id", "name", "rooms"],
    )

    normalized = normalize_search_output(
        {
            "facts": [],
            "near": [{
                "id": "near",
                "name": "Почти",
                "rooms": [1],
                "why_close": "Подходит, но комнатность не подтверждена в MCP-карточке.",
                "differences": [],
            }],
            "missing": [],
            "params": {},
            "diagnostics": {},
        },
        request,
    )
    validation = validate_search_output(normalized, request)

    assert validation["ok"], validation["errors"]
    assert normalized["near"][0]["why_close"] == "другая комнатность"
    assert normalized["near"][0]["differences"] == ["rooms"]


def test_near_without_structured_difference_gets_safe_runtime_reason() -> None:
    request = V2SearchRequest(search_goal={"query_summary": "подбор"}, available_fact_fields=["id", "name"])

    normalized = normalize_search_output(
        {"facts": [], "near": [{"id": "near", "name": "Почти"}], "missing": [], "params": {}, "diagnostics": {}},
        request,
    )

    assert normalized["near"] == [{"id": "near", "name": "Почти", "is_near": True, "why_close": "неполное подтверждение условий", "differences": ["неполное подтверждение условий"]}]
    assert "contract_warning:near_without_structured_difference_reported" in normalized["diagnostics"]["notes"]


def test_min_price_hard_evidence_and_boundary_matching_use_numeric_prices_only() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "от 15 млн"},
        requested_hard={"min_price": 15_000_000},
        effective_hard={"min_price": 15_000_000},
        available_fact_fields=["id", "name", "min_price", "max_price", "ads"],
    )
    output = _output_for(request)
    output["facts"] = [
        {"id": "range", "name": "Есть верхняя граница", "min_price": 12_000_000, "max_price": 18_000_000},
        {"id": "ad", "name": "Есть объявление", "ads": [{"price": 16_000_000}]},
    ]
    low = _output_for(request)
    low["facts"] = [{"id": "low", "name": "Ниже", "min_price": 10_000_000, "max_price": 14_000_000}]
    prose = _output_for(request)
    prose["facts"] = [{"id": "prose", "name": "Текст", "description": "цены от 16 млн"}]

    assert validate_search_output(output, request)["ok"]
    assert "fact_0_violates_hard:min_price" in validate_search_output(low, request)["errors"]
    assert "fact_0_missing_hard_evidence:min_price" in validate_search_output(prose, request)["errors"]


def test_area_min_max_evidence_and_boundary_matching_are_structured_only() -> None:
    min_request = V2SearchRequest(
        search_goal={"query_summary": "от 55 метров"},
        requested_hard={"area_min_m2": 55},
        effective_hard={"area_min_m2": 55},
        available_fact_fields=["id", "name", "square_min", "square_max", "ads", "apartment_types"],
    )
    min_ok = _output_for(min_request)
    min_ok["facts"] = [{"id": "area", "name": "Площадь", "square_min": 40, "square_max": 60}]
    min_bad = _output_for(min_request)
    min_bad["facts"] = [{"id": "small", "name": "Мало", "square_min": 30, "square_max": 50}]

    max_request = V2SearchRequest(
        search_goal={"query_summary": "до 45 метров"},
        requested_hard={"area_max_m2": 45},
        effective_hard={"area_max_m2": 45},
        available_fact_fields=["id", "name", "square_min", "square_max", "ads", "apartment_types"],
    )
    max_ok = _output_for(max_request)
    max_ok["facts"] = [{"id": "lot", "name": "Лот", "ads": [{"area": 44}]}]
    max_bad = _output_for(max_request)
    max_bad["facts"] = [{"id": "large", "name": "Много", "apartment_types": [{"area": 50}]}]
    prose = _output_for(max_request)
    prose["facts"] = [{"id": "prose", "name": "Текст", "description": "площади до 40 м2"}]

    assert validate_search_output(min_ok, min_request)["ok"]
    assert "fact_0_violates_hard:area_min_m2" in validate_search_output(min_bad, min_request)["errors"]
    assert validate_search_output(max_ok, max_request)["ok"]
    assert "fact_0_violates_hard:area_max_m2" in validate_search_output(max_bad, max_request)["errors"]
    assert "fact_0_missing_hard_evidence:area_max_m2" in validate_search_output(prose, max_request)["errors"]


def test_broad_complex_rooms_hard_validation_does_not_accept_ads_only_match() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "двушка"},
        requested_hard={"rooms": [2]},
        effective_hard={"rooms": [2]},
        available_fact_fields=["id", "name", "rooms", "ads"],
    )
    output = _output_for(request)
    output["facts"] = [{"id": "complex", "name": "ЖК", "rooms": "1,3", "ads": [{"id": 1, "rooms": "2", "status": 2}]}]

    validation = validate_search_output(output, request)

    assert not validation["ok"]
    assert "fact_0_violates_hard:rooms" in validation["errors"]


def test_selected_lot_hard_rooms_accepts_mixed_complex_room_formats_with_matching_active_ads() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "Новое Видное двушки"},
        requested_hard={},
        effective_hard={},
        lot_hard={"rooms": 2},
        available_fact_fields=["id", "name", "rooms", "ads"],
    )
    output = _output_for(request)
    output["facts"] = [{"id": "complex", "name": "Новое Видное", "rooms": "1,3", "ads": [{"id": 1, "rooms": "2", "status": "2"}]}]

    assert validate_search_output(output, request)["ok"]


def test_selected_lot_hard_rooms_rejects_missing_or_invalid_status_ads() -> None:
    request = V2SearchRequest(
        search_goal={"query_summary": "Новое Видное двушки"},
        lot_hard={"rooms": 2},
        available_fact_fields=["id", "name", "ads"],
    )
    output = _output_for(request)
    output["facts"] = [{"id": "complex", "name": "Новое Видное", "ads": [{"id": 1, "rooms": "2", "status": 1}]}]

    validation = validate_search_output(output, request)

    assert not validation["ok"]
    assert "fact_0_violates_lot_hard:rooms" in validation["errors"]


@pytest.mark.xfail(
    strict=True,
    reason="Current contract gap: search_contract.validate_search_output has no inventory gate for lot-scoped price evidence.",
)
def test_inventory_gate_rejects_project_price_without_qualifying_ad() -> None:
    # Future owner: nmbot_v2.search_contract.validate_search_output inventory gate.
    # price_mod is project-level metadata, not evidence for a qualifying ad.
    request = V2SearchRequest(
        search_goal={"query_summary": "двушка до 10 млн"},
        lot_hard={"rooms": 2, "max_price": 10_000_000},
        available_fact_fields=["id", "name", "min_price", "price_mod", "ads"],
    )
    output = _output_for(request)
    output["facts"] = [{
        "id": "project-only-price",
        "name": "ЖК с ценой проекта",
        "min_price": 9_500_000,
        "price_mod": 9_500_000,
        "ads": [],
    }]

    validation = validate_search_output(output, request)

    assert not validation["ok"]
    assert "fact_0_violates_inventory_gate:max_price" in validation["errors"]


def test_selected_followup_phrase_extracts_lot_scoped_rooms_constraint() -> None:
    plan = SemanticPlan(operation="select_option", selected_option_name="Новое Видное", query_text="двушки есть?", requested_facts=("apartment_inventory",), facts_needed=("apartment_inventory",))

    assert runtime_adapter._selected_lot_hard_constraints(plan) == {"rooms": 2}


def test_missing_normalization_accepts_known_fields_dicts_and_unknown_strings() -> None:
    request = V2SearchRequest(search_goal={"query_summary": "подбор"}, available_fact_fields=["id", "name"])
    normalized = normalize_search_output(
        {
            "facts": [],
            "near": [],
            "missing": [
                "school",
                "источник не ответил",
                "совсем неизвестная строка",
                {"field": "ads.fullprice", "reason_code": "unknown_reason", "details": "нет в ответе"},
            ],
            "params": {},
            "diagnostics": {},
        },
        request,
    )
    validation = validate_search_output(normalized, request)

    assert validation["ok"], validation["errors"]
    assert normalized["missing"] == [
        "school",
        "provider_unavailable",
        "requested_but_unconfirmed",
        {"field": "ads.fullprice", "reason_code": "requested_but_unconfirmed", "details": "нет в ответе"},
    ]


def test_validate_search_output_flags_raw_unknown_missing_values() -> None:
    request = V2SearchRequest(search_goal={"query_summary": "подбор"}, available_fact_fields=["id", "name"])
    output = _output_for(request)
    output["missing"] = ["inventory_absent", {"field": "unknown", "reason_code": "unknown"}]

    result = validate_search_output(output, request)

    assert not result["ok"]
    assert "missing_0_unknown_value" in result["errors"]
    assert "missing_1_unknown_value" in result["errors"]


def test_current_options_fact_check_request_is_scoped_to_three_exact_names_and_facts() -> None:
    cards = [
        OptionCard(name="ЖК Первый"),
        OptionCard(name="ЖК Второй"),
        OptionCard(name="ЖК Третий"),
        OptionCard(name="ЖК Четвёртый"),
    ]

    request = build_current_options_fact_check_request(cards, ["parks", "parks", "secret"], "family", base_viewpoint="life")
    query = build_query(request)
    envelope, params, client = _compact_parts(query)

    assert request.search_mode == "current_options_fact_check"
    assert request.count == 3
    assert request.current_option_names == ("ЖК Первый", "ЖК Второй", "ЖК Третий")
    assert request.facts_needed == ("parks",)
    assert request.requested_hard == {}
    assert request.effective_hard == {}
    assert "park_near" in request.available_fact_fields
    assert envelope["current_option_names"] == ["ЖК Первый", "ЖК Второй", "ЖК Третий"]
    assert envelope["facts_needed"] == ["parks"]
    assert params["current_option_names"] == ["ЖК Первый", "ЖК Второй", "ЖК Третий"]
    assert "ЖК Четвёртый" not in query
    assert client == "Проверь только запрошенные факты по текущим ЖК: ЖК Первый, ЖК Второй, ЖК Третий"


def test_current_options_fact_check_validator_rejects_foreign_objects_but_keeps_near_distinction() -> None:
    request = build_current_options_fact_check_request(
        [OptionCard(name="ЖК Первый"), OptionCard(name="ЖК Второй")],
        ["parks"],
        "life",
    )
    result = {
        "facts": [{"name": "ЖК Первый", "park_near": True}],
        "near": [{"name": "Посторонний ЖК", "park_near": True}],
        "missing": [],
        "params": {},
    }

    validation = validate_current_options_fact_check_result(result, request)

    assert validation["ok"] is False
    assert validation["errors"] == ("near_0_foreign_object",)
    assert validation["counts"] == {"facts": 1, "near": 1, "current_option_names": 2}


def test_current_options_fact_check_validator_accepts_fact_and_near_within_scope() -> None:
    request = build_current_options_fact_check_request(
        [OptionCard(name="ЖК Первый"), OptionCard(name="ЖК Второй")],
        ["parks"],
        "life",
    )
    result = {
        "facts": [{"name": "ЖК Первый", "park_near": True}],
        "near": [{"name": "ЖК Второй", "water_near": True}],
    }

    validation = validate_current_options_fact_check_result(result, request)

    assert validation["ok"] is True
    assert validation["errors"] == ()
