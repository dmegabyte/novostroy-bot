from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "prompts" / "v2_search_mcp.txt"
FIXTURE = ROOT / "tests" / "fixtures" / "v2_search_mcp_contract.json"


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _prompt_text() -> str:
    return PROMPT.read_text(encoding="utf-8")


def test_v2_search_prompt_declares_unified_mcp_contract_without_dialog_routing() -> None:
    text = _prompt_text()
    lowered = text.lower()

    required_patterns = [
        r"novostroym/get_flat_info",
        r"response_viewpoint",
        r"investment\|rental\|family\|life\|financing",
        r"search_goal",
        r"requested_hard",
        r"effective_hard",
        r"relaxation_audit",
        r"base_viewpoint",
        r"ignored_preferences",
        r"available_fact_fields",
        r"facts",
        r"near",
        r"missing",
        r"params",
        r"diagnostics",
        r"district.*msk.*mo.*newmsk",
        r"location.*отдельно",
        r"financing.*overlay",
        r"later enrichment",
        r"нельзя придумывать доходность",
    ]
    for pattern in required_patterns:
        assert re.search(pattern, lowered, flags=re.S), pattern

    forbidden_generation_patterns = [
        r"верни[^\n]+\"action\"",
        r"верни[^\n]+\"target\"",
        r"верни[^\n]+\"response\"",
        r"clarification_question.*строка",
        r"operator_contact",
        r"recover_dialogue",
        r"answer_current_options",
        r"выбери\s+сценар",
        r"реши\s+маршрут",
    ]
    for pattern in forbidden_generation_patterns:
        assert not re.search(pattern, lowered, flags=re.S), pattern


def test_v2_search_prompt_bounds_mcp_work_and_terminal_response_size() -> None:
    text = _prompt_text().lower()

    assert "ровно один раз" in text
    assert "запрашивай только поля" in text
    assert "не более `count` объектов и не более 3 объектов" in text
    assert "не делай новых вызовов инструмента" in text
    assert "сразу верни терминальный строгий json" in text


def test_v2_search_fixture_has_exactly_15_scenarios_covering_required_cases() -> None:
    data = _load_fixture()
    assert data["observed_wire_shapes"]["delivered"] == [True, False, 1, 0]
    assert "2-комнатные" in data["observed_wire_shapes"]["rooms"]
    assert ["Сокол", "Аэропорт"] in data["observed_wire_shapes"]["location"]
    scenarios = data["scenarios"]

    assert len(scenarios) == 15
    assert [scenario["id"] for scenario in scenarios] == [
        "base_search",
        "family",
        "family_financing_overlay",
        "investment",
        "rental",
        "life",
        "rooms_budget_location",
        "ready_finishing",
        "district_location_separation",
        "exact_facts_vs_near",
        "missing_data",
        "one_actual_constraint_relaxation",
        "scenario_field_priority",
        "unknown_preference_ignored",
        "broad_candidates_later_enrichment",
    ]

    viewpoints = {scenario["response_viewpoint"] for scenario in scenarios}
    assert {"investment", "rental", "family", "life", "financing"} <= viewpoints


def test_v2_search_fixture_shape_assertions_are_contract_not_phrase_tests() -> None:
    data = _load_fixture()
    allowed = set(data["output_top_level_keys"])
    forbidden = set(data["forbidden_top_level_keys"])
    assert allowed == {"facts", "near", "missing", "params", "diagnostics"}
    assert forbidden.isdisjoint(allowed)

    allowed_assertions = {
        "only_allowed_top_level_keys",
        "facts_preserve_allowed_mcp_fields",
        "params_only_normalized_constraints",
        "viewpoint_not_hard_filter",
        "missing_for_unconfirmed_scenario_fields",
        "financing_overlay_not_replacement",
        "no_unconfirmed_rate_or_fee",
        "no_unsupported_financial_claim",
        "egrn_and_counters_only_if_returned",
        "no_rent_yield_or_demand_without_mcp",
        "district_is_mcp_region_code",
        "location_is_separate",
        "facts_match_all_hard_constraints",
        "near_not_mixed_with_facts",
        "near_requires_differences_and_why_close",
        "no_invented_infrastructure",
        "search_goal_required",
        "effective_hard_controls_exact_matching",
        "hard_evidence_required",
        "relaxation_audit_recorded",
        "agent_cannot_decide_relaxation",
        "financing_base_overlay_explicit",
        "controlled_preferences_only",
        "unknown_preference_safely_ignored",
        "no_absence_claim_without_evidence",
        "only_one_hard_constraint_relaxed",
        "broad_results_allow_later_enrichment",
    }

    for scenario in data["scenarios"]:
        assert scenario["response_viewpoint"] in data["scenario_field_priorities"]
        assert isinstance(scenario["search_goal"], dict)
        assert scenario["search_goal"]["entity_type"] == "new_building_flat"
        assert scenario["search_goal"]["query_summary"]
        assert isinstance(scenario["search_goal"]["explicit_terms"], list)
        assert {"requested_hard", "effective_hard", "preferences", "relaxation_audit"} <= set(scenario["constraints"])
        assert "hard" not in scenario["constraints"]
        assert "relaxation" not in scenario["constraints"]
        assert scenario["count"] > 0
        assert set(scenario["shape_assertions"]) <= allowed_assertions
        assert scenario["expected_field_priorities_include"]
        assert "expected_text_contains" not in scenario
        assert "expected_phrase" not in scenario


def test_v2_search_viewpoint_priorities_do_not_create_hard_filters() -> None:
    data = _load_fixture()

    for scenario in data["scenarios"]:
        hard = scenario["constraints"]["effective_hard"]
        assert "response_viewpoint" not in hard
        assert "purpose" not in hard
        assert scenario["response_viewpoint"] not in hard.values()

    financing = next(item for item in data["scenarios"] if item["id"] == "family_financing_overlay")
    assert financing["response_viewpoint"] == "financing"
    assert financing["base_viewpoint"] == "family"
    assert "base_viewpoint" not in financing["constraints"]["preferences"]
    assert financing["also_expected_overlay_preserves"]


def test_v2_search_prompt_and_fixture_share_field_priorities() -> None:
    text = _prompt_text()
    data = _load_fixture()

    for viewpoint, fields in data["scenario_field_priorities"].items():
        assert viewpoint in text
        for field in fields:
            assert field in text, f"{viewpoint}: {field}"

    for scenario in data["scenarios"]:
        priority_set = set(data["scenario_field_priorities"][scenario["response_viewpoint"]])
        expected = set(scenario["expected_field_priorities_include"])
        if scenario["id"] in {
            "rooms_budget_location",
            "ready_finishing",
            "district_location_separation",
            "exact_facts_vs_near",
            "one_actual_constraint_relaxation",
        }:
            assert expected
        else:
            assert expected <= priority_set or scenario["id"] == "family_financing_overlay"
