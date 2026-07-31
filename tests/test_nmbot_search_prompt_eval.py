from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / "eval" / "nmbot-search-prompt"


def _load_runner():
    spec = importlib.util.spec_from_file_location("nmbot_search_prompt_eval", EVAL_ROOT / "run_eval.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cases_json_contains_exact_five_prompt_cases() -> None:
    cases = json.loads((EVAL_ROOT / "cases.json").read_text(encoding="utf-8"))

    assert [case["id"] for case in cases] == [
        "family",
        "family_financing_overlay",
        "rooms_budget_location",
        "ready_finishing",
        "district_location_separation",
    ]
    assert [case["fixture_scenario_id"] for case in cases] == [case["id"] for case in cases]
    assert [case["min_candidates"] for case in cases] == [1, 1, 1, 1, 1]
    assert [case["query"] for case in cases] == [
        "двухкомнатная квартира для семьи",
        "двухкомнатная квартира для семьи в ипотеку",
        "двухкомнатная квартира на Соколе до 18 млн",
        "готовая квартира с отделкой под аренду",
        "квартира в Коммунарке, Новая Москва",
    ]


def test_eval_uses_full_production_prompt_as_single_source_of_truth() -> None:
    runner = _load_runner()

    assert runner.PROMPT_PATH == ROOT / "prompts" / "v2_search_mcp.txt"
    assert runner.PROMPT_PATH.exists()
    assert "novostroym/get_flat_info" in runner.load_prompt()
    assert "## Реальные формы значений MCP" in runner.load_prompt()


def test_request_uses_fixed_model_mcp_stage_and_case_query() -> None:
    runner = _load_runner()
    case = runner.cases_by_id()["rooms_budget_location"]
    request_data = runner.build_request_data_for_case(case, prompt="PROMPT")

    assert request_data["_payload_stage"] == "main_search"
    assert request_data["model"] == "google/gemini-3.1-flash-lite-preview"
    assert request_data["mcp_servers"] == ["novostroym"]
    assert request_data["system_prompt"] == "PROMPT"
    assert "двухкомнатная квартира на Соколе до 18 млн" in request_data["query"]
    assert "external_api_key" not in request_data


def test_fixture_only_static_validation_makes_no_network() -> None:
    runner = _load_runner()

    status = asyncio.run(runner.async_main(["--fixture-only"]))

    assert status == 0


def test_run_case_recovers_missing_room_evidence_with_bounded_enrichment(monkeypatch) -> None:
    runner = _load_runner()
    case = runner.cases_by_id()["family"]
    calls: list[str] = []
    original_normalize = runner.contract.normalize_search_output

    def normalize_with_initial_gap(output, request):
        normalized = original_normalize(output, request)
        facts = output.get("facts") if isinstance(output, dict) else []
        if isinstance(facts, list) and facts and facts[0].get("name") == "ЖК Without Rooms":
            normalized["facts"] = [dict(facts[0])]
            normalized["near"] = []
        return normalized

    monkeypatch.setattr(runner.contract, "normalize_search_output", normalize_with_initial_gap)

    def payload(facts, *, missing=None, params=None):
        return json.dumps(
            {
                "facts": facts,
                "near": [],
                "missing": missing or [],
                "params": params or {},
                "diagnostics": {},
            },
            ensure_ascii=False,
        )

    async def fake_gateway(request_data, timeout):
        assert timeout == 1
        assert request_data["_payload_stage"] == "main_search"
        query = str(request_data["query"])
        if "full_card" in query:
            name = query.split("канонического ЖК «", 1)[1].split("»", 1)[0]
            calls.append(f"exact:{name}")
            return payload([{"name": name, "location": "Москва", "rooms": 2, "min_price": 12_000_000}]), {"ok": True}
        if "broad_candidate_collection" in query:
            calls.append("recovery")
            facts = [{"name": f"ЖК Recovery {idx}", "location": "Москва", "min_price": 12_000_000 + idx} for idx in range(1, 7)]
            return payload(facts, params={}), {"ok": True}
        calls.append("initial")
        return payload([{"name": "ЖК Without Rooms", "location": "Москва", "min_price": 12_000_000}], missing=["rooms"], params={"rooms": [2]}), {"ok": True}

    result = asyncio.run(runner.run_case(case, timeout=1, gateway_func=fake_gateway))

    assert result["ok"], result
    assert result["counts"] == {"facts": 5, "near": 0, "missing": 1}
    assert result["found_options"] is True
    assert result["enrichment"]["enabled"] is True
    assert result["enrichment"]["recovery"] is True
    assert result["enrichment"]["trigger"] == "hard_evidence_gap"
    assert result["enrichment"]["confirmed_count"] == 5
    assert result["enrichment"]["fields"] == ["rooms"]
    assert result["enrichment"]["count"] == runner.MAX_ENRICHMENT_OPTIONS
    assert len(result["enrichment"]["items"]) == runner.MAX_ENRICHMENT_OPTIONS
    assert len(calls) == 7
    assert calls[0:2] == ["initial", "recovery"]
    assert sorted(calls[2:]) == [f"exact:ЖК Recovery {idx}" for idx in range(1, 6)]


def test_all_fail_fast_with_fake_gateway() -> None:
    runner = _load_runner()
    calls: list[str] = []

    async def fake_gateway(request_data, timeout):
        assert request_data["_payload_stage"] == "main_search"
        client_query = request_data["query"].split("\nКлиент: ", 1)[1].split("\n", 1)[0]
        calls.append(client_query)
        return json.dumps({"facts": [], "near": [], "missing": ["requested_but_unconfirmed"], "params": {}, "diagnostics": {}}), {"ok": True}

    status = asyncio.run(runner.async_main(["--all", "--timeout", "1"], gateway_func=fake_gateway))

    assert status == 1
    assert calls == ["двухкомнатная квартира для семьи"]
