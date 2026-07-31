from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import nmbot_v2_search_mcp_probe as probe


def _fixture_and_case(case_id: str = "family"):
    fixture = probe.load_fixture()
    return fixture, probe.scenarios_by_id(fixture)[case_id]


def _valid_output(scenario: dict) -> dict:
    return {
        "facts": [],
        "near": [],
        "missing": [],
        "params": dict(scenario["constraints"]["effective_hard"]),
        "diagnostics": {
            "mcp_tool": probe.MCP_TOOL,
            "response_viewpoint": scenario["response_viewpoint"],
            "base_viewpoint": scenario.get("base_viewpoint"),
            "requested_field_priorities": list(scenario.get("expected_field_priorities_include", [])) + list(scenario.get("also_expected_overlay_preserves", [])),
            "relaxation_audit": list(scenario["constraints"].get("relaxation_audit", [])),
            "ignored_preferences": [],
            "notes": [],
        },
    }


def test_build_request_data_includes_prompt_structured_input_model_and_mcp_alias() -> None:
    fixture, scenario = _fixture_and_case("family_financing_overlay")
    request = probe.build_request_data(fixture, scenario, prompt="PROMPT")

    assert request["_payload_stage"] == "main_search"
    assert request["service"] == "openrouter"
    assert request["model"] == probe.SEARCH_MODEL
    assert request["mcp_servers"] == [probe.MCP_ALIAS]
    assert request["system_prompt"] == "PROMPT"
    assert request["query"].count("SEARCH_CONTRACT_ENVELOPE=") == 1
    assert "Текущие параметры: " in request["query"]
    assert "\nКлиент: " in request["query"]
    assert "V2_SEARCH_MCP_CONTRACT=" not in request["query"]
    assert "V2_SEARCH_INPUT=" not in request["query"]
    assert "external_api_key" not in request
    assert "task_id" not in json.dumps(request)

    envelope_part = request["query"].split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0]
    params_part = request["query"].split("Текущие параметры: ", 1)[1].split("\n", 1)[0]
    envelope = json.loads(envelope_part)
    payload = json.loads(params_part)
    assert payload["search_goal"] == scenario["search_goal"]
    assert payload["requested_hard"] == scenario["constraints"]["requested_hard"]
    assert payload["effective_hard"] == scenario["constraints"]["effective_hard"]
    assert envelope["response_viewpoint"] == "financing"
    assert envelope["base_viewpoint"] == "family"
    assert envelope["count"] == scenario["count"]
    assert "mortgage_calc" in envelope["available_fact_fields"]
    assert "school" in envelope["available_fact_fields"]
    assert probe.HARD_EVIDENCE_MAP is probe.core_contract.HARD_EVIDENCE_MAP


def test_parse_strict_json_rejects_markdown_or_trailing_text() -> None:
    parsed, errors = probe.parse_strict_json('{"facts": []}')
    assert parsed == {"facts": []}
    assert errors == []

    parsed, errors = probe.parse_strict_json('```json\n{"facts": []}\n```')
    assert parsed is None
    assert errors


def test_gateway_task_diagnostics_allowlists_transport_fields_without_model_text() -> None:
    diagnostics = probe._safe_gateway_task_diagnostics(
        task_id="task-123",
        terminal_status="completed",
        result_obj={
            "response": "private model output",
            "metadata": {
                "finish_reason": "length",
                "usage": {"prompt_tokens": 12, "completion_tokens": 34, "secret": "do-not-leak"},
                "tokens_used": 46,
                "processing_time": 1.5,
                "query": "do-not-leak",
            },
        },
    )

    assert diagnostics == {
        "task_id": "task-123",
        "terminal_status": "completed",
        "result_keys": ["metadata", "response"],
        "metadata_keys": ["finish_reason", "processing_time", "query", "tokens_used", "usage"],
        "finish_reason": "length",
        "usage": {"prompt_tokens": 12, "completion_tokens": 34},
        "tokens_used": 46,
        "processing_time": 1.5,
    }
    rendered = json.dumps(diagnostics, ensure_ascii=False)
    assert "private model output" not in rendered
    assert "do-not-leak" not in rendered

    parsed, errors = probe.parse_strict_json('{"facts": []}\nextra')
    assert parsed is None
    assert errors


def test_validate_output_accepts_exact_allowed_top_level_shape() -> None:
    fixture, scenario = _fixture_and_case("family")
    result = probe.validate_output(_valid_output(scenario), fixture, scenario)
    assert result["ok"], result["errors"]


def test_validate_output_rejects_forbidden_and_extra_top_level_keys() -> None:
    fixture, scenario = _fixture_and_case("family")
    output = _valid_output(scenario)
    output["response"] = "must not be here"
    result = probe.validate_output(output, fixture, scenario)
    assert not result["ok"]
    assert "top_level_keys_mismatch" in result["errors"]
    assert any(error.startswith("forbidden_top_level_keys") for error in result["errors"])


def test_validate_facts_vs_near_distinction_is_structural() -> None:
    fixture, scenario = _fixture_and_case("exact_facts_vs_near")
    output = _valid_output(scenario)
    output["facts"] = [{"id": "same", "rooms": 2, "max_price": 10_000_000, "location": "Котельники", "district": "mo"}]
    output["near"] = [{"id": "same", "why_close": "рядом", "differences": ["цена выше"]}]
    result = probe.validate_output(output, fixture, scenario)
    assert not result["ok"]
    assert "near_duplicates_facts" in result["errors"]
    assert "fact_0_violates_hard:rooms" in result["errors"]


def test_effective_hard_not_requested_hard_controls_exact_matching() -> None:
    fixture, scenario = _fixture_and_case("one_actual_constraint_relaxation")
    output = _valid_output(scenario)
    output["facts"] = [{"id": "relaxed", "max_price": 10_500_000, "location": "Сокол", "district": "msk"}]
    output["params"] = {"max_price": 10_500_000, "location": ["Сокол"]}
    result = probe.validate_output(output, fixture, scenario)
    assert not result["ok"]
    assert "params_not_effective_hard:max_price" in result["errors"]


def test_location_separation_accepts_list_location_without_hashing() -> None:
    fixture, scenario = _fixture_and_case("district_location_separation")
    output = _valid_output(scenario)
    output["params"] = {"district": "newmsk", "location": ["Коммунарка"]}

    result = probe.validate_output(output, fixture, scenario)

    assert result["ok"], result["errors"]


def test_location_separation_rejects_region_code_in_list_location() -> None:
    fixture, scenario = _fixture_and_case("district_location_separation")
    output = _valid_output(scenario)
    output["params"] = {"district": "newmsk", "location": ["newmsk"]}

    result = probe.validate_output(output, fixture, scenario)

    assert not result["ok"]
    assert "params_location_repeats_district_code" in result["errors"]


def test_location_separation_rejects_region_code_in_string_location() -> None:
    fixture, scenario = _fixture_and_case("district_location_separation")
    output = _valid_output(scenario)
    output["params"] = {"district": "newmsk", "location": "newmsk"}

    result = probe.validate_output(output, fixture, scenario)

    assert not result["ok"]
    assert "params_location_repeats_district_code" in result["errors"]


def test_unknown_preference_is_ignored_without_raw_value_leak() -> None:
    fixture, scenario = _fixture_and_case("unknown_preference_ignored")
    output = _valid_output(scenario)
    output["params"] = {"sort_hint": "price"}
    output["diagnostics"]["ignored_preferences"] = ["unsupported_sensitive_hint"]
    result = probe.validate_output(output, fixture, scenario)
    assert result["ok"], result["errors"]

    output["params"]["unsupported_sensitive_hint"] = "redacted-by-validator"
    result = probe.validate_output(output, fixture, scenario)
    assert not result["ok"]
    assert any(error.startswith("params_extra_keys") for error in result["errors"])
    assert "unknown_preference_raw_value_leaked" in result["errors"]


def test_run_live_case_normalizes_failed_family_financing_overlay_diagnostics() -> None:
    fixture, scenario = _fixture_and_case("family_financing_overlay")
    output = _valid_output(scenario)
    output["diagnostics"] = {
        "mcp_tool": "model/override",
        "response_viewpoint": "life",
        "base_viewpoint": "investment",
        "requested_field_priorities": [],
        "relaxation_audit": [{"field": "rooms", "model": "changed"}],
        "ignored_preferences": ["model_changed"],
        "notes": ["safe note"],
    }

    async def fake_gateway(_request_data, _timeout):
        return json.dumps(output, ensure_ascii=False), {"ok": True, "metadata_keys": ["safe"]}

    result = asyncio.run(probe.run_live_case("family_financing_overlay", timeout=3, gateway_func=fake_gateway))

    assert result["ok"], result["errors"]


def test_missing_hard_evidence_keeps_item_out_of_exact_facts() -> None:
    fixture, scenario = _fixture_and_case("ready_finishing")
    output = _valid_output(scenario)
    output["facts"] = [{"id": "no-evidence", "name": "No evidence"}]
    result = probe.validate_output(output, fixture, scenario)
    assert not result["ok"]
    assert "fact_0_missing_hard_evidence:ready" in result["errors"]
    assert "fact_0_missing_hard_evidence:finishing" in result["errors"]


def test_diagnose_adds_bounded_ready_hard_mismatch_fields_only() -> None:
    fixture, scenario = _fixture_and_case("ready_finishing")
    output = _valid_output(scenario)
    long_ready = "строится, подробное рекламное описание корпуса " + ("x" * 120)
    output["facts"] = [
        {
            "id": "raw-id-must-not-leak",
            "name": "raw name must not leak",
            "ready": long_ready,
            "delivered": False,
            "state": "construction",
            "status": "active",
            "finishing": True,
        }
    ]

    plain = probe.validate_output(output, fixture, scenario)
    assert "fact_0_violates_hard:ready" in plain["errors"]
    assert "hard_match_diagnostics" not in plain

    diagnosed = probe.validate_output(output, fixture, scenario, diagnose=True)
    diag = diagnosed["hard_match_diagnostics"]
    assert diag == [
        {
            "error": "fact_0_violates_hard:ready",
            "fact_index": 0,
            "fields": {
                "ready": long_ready[: probe.MAX_DIAGNOSTIC_VALUE_CHARS],
                "delivered": False,
                "state": "construction",
                "status": "active",
            },
        }
    ]
    rendered = json.dumps(diag, ensure_ascii=False)
    assert "raw-id-must-not-leak" not in rendered
    assert "raw name must not leak" not in rendered
    assert len(diag[0]["fields"]["ready"]) == probe.MAX_DIAGNOSTIC_VALUE_CHARS


def test_absent_evidence_must_not_be_inventory_absence_claim() -> None:
    fixture, scenario = _fixture_and_case("missing_data")
    output = _valid_output(scenario)
    output["missing"] = [{"field": "rooms", "reason_code": "absence_claim"}]
    result = probe.validate_output(output, fixture, scenario)
    assert not result["ok"]
    assert "absence_claim_without_hard_evidence" in result["errors"]


def test_fixture_only_validates_all_15_without_network(monkeypatch) -> None:
    fixture = probe.load_fixture()
    called = False

    async def fake_gateway(*_args, **_kwargs):
        nonlocal called
        called = True
        return "{}", {"ok": True}

    monkeypatch.setattr(probe, "gateway_request", fake_gateway)
    results = [probe.validate_fixture_case(fixture, scenario) for scenario in fixture["scenarios"]]
    assert len(results) == 15
    assert all(item["ok"] for item in results)
    assert all(item["network"] is False for item in results)
    assert called is False


def test_selected_cases_allows_fixture_only_single_case() -> None:
    fixture = probe.load_fixture()

    class Args:
        case = "family"
        all = False
        fixture_only = True

    assert probe._selected_cases(Args(), fixture) == ["family"]


def test_run_live_case_uses_injected_gateway_and_returns_safe_summary() -> None:
    fixture, scenario = _fixture_and_case("base_search")
    output = _valid_output(scenario)
    seen = {}

    async def fake_gateway(request_data, timeout):
        seen["request_data"] = request_data
        seen["timeout"] = timeout
        return json.dumps(output, ensure_ascii=False), {"ok": True, "metadata_keys": ["safe"]}

    result = asyncio.run(probe.run_live_case("base_search", timeout=3, gateway_func=fake_gateway))
    rendered = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is True
    assert result["network"] is True
    assert result["gateway_meta"] == {"ok": True, "metadata_keys": ["safe"]}
    assert seen["timeout"] == 3
    assert seen["request_data"]["mcp_servers"] == [probe.MCP_ALIAS]
    assert "external_api_key" not in rendered
    assert "task_id" not in rendered
    assert "V2_SEARCH_INPUT" not in rendered
