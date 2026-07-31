from __future__ import annotations

import json
import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import nmbot_four_layer_e2e as e2e


@pytest.mark.parametrize("scenario", ["exact_budget", "hard_constraints", "unsupported_claim", "no_match"])
def test_dry_scenario_e2e_passes_without_network(monkeypatch: pytest.MonkeyPatch, scenario: str) -> None:
    async def no_gateway(*args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        raise AssertionError("dry mode must not call network")

    monkeypatch.setattr(e2e, "gateway_request", no_gateway)
    result = asyncio.run(e2e.run_scenario(scenario))
    assert result["status"] == "ok"
    assert result["invariant_checks"]["ok"] is True


def test_rejected_candidates_absent_from_decision_context_and_presenter() -> None:
    result = asyncio.run(e2e.run_scenario("hard_constraints"))
    dctx = result["stages"]["validator"]["decision_context"]
    presenter_items = result["stages"]["presenter"]["response"]["visible_options"]
    assert [item["option_id"] for item in dctx["matched"]] == ["exact_1"]
    assert "reject_location" not in json.dumps(dctx, ensure_ascii=False)
    assert "reject_budget" not in {item["option_id"] for item in presenter_items}
    assert result["counts"]["rejected"] == 2


def test_matched_decision_context_contains_only_safe_normalized_fact_values() -> None:
    result = asyncio.run(e2e.run_scenario("hard_constraints"))
    matched = result["stages"]["validator"]["decision_context"]["matched"]
    assert matched[0]["facts"] == {"location": "Сокол", "price_min": 17_500_000.0}
    assert set(matched[0]["facts"]) == {"location", "price_min"}


def test_exact_budget_20_dry_exits_ok_and_presenter_sees_only_match(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_gateway(*args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        raise AssertionError("dry mode must not call network")

    monkeypatch.setattr(e2e, "gateway_request", no_gateway)

    code = asyncio.run(e2e.async_main(["--scenario", "exact_budget_20"]))
    result = asyncio.run(e2e.run_scenario("exact_budget_20"))
    dctx = result["stages"]["validator"]["decision_context"]
    presenter_items = result["stages"]["presenter"]["response"]["visible_options"]

    assert code == 0
    assert result["status"] == "ok"
    assert e2e.SCENARIOS["exact_budget_20"]["planner"]["constraints_patch"]["hard"]["price"] == 20_000_000
    assert [item["option_id"] for item in dctx["matched"]] == ["synthetic_exact_budget_20_match"]
    assert [item["option_id"] for item in presenter_items] == ["synthetic_exact_budget_20_match"]
    assert "synthetic_exact_budget_20_reject" not in json.dumps(dctx, ensure_ascii=False)
    assert result["counts"]["rejected"] == 1


def test_forbidden_claim_checker_catches_violation() -> None:
    search_json = e2e.SCENARIOS["unsupported_claim"]["search"]
    plan = e2e.SCENARIOS["unsupported_claim"]["planner"]
    validation, status = e2e.validate_stage(search_json, plan)
    assert status == "ok"
    bad = {"response": {"message": "Этот вариант очень ликвидный и с хорошей доходностью.", "items": [{"option_id": "claim_limited_1"}], "final_question": "Разобрать?"}}
    checks = e2e.check_invariants(validation["decision_context"], bad)
    assert checks["ok"] is False
    assert "forbidden_claim_keyword" in checks["failures"]


def test_no_match_has_no_items_and_relaxation_question() -> None:
    result = asyncio.run(e2e.run_scenario("no_match"))
    dctx = result["stages"]["validator"]["decision_context"]
    response = result["stages"]["presenter"]["response"]
    assert dctx["matched"] == []
    assert dctx["near_match"] == []
    assert dctx["relaxation_needed"] is True
    assert response["visible_options"] == []
    assert response["final_question"]


def test_near_is_not_used_as_exact_candidate() -> None:
    result = asyncio.run(e2e.run_scenario("hard_constraints"))
    dctx = result["stages"]["validator"]["decision_context"]
    all_ids = json.dumps(dctx, ensure_ascii=False)
    assert "near_ignored" not in all_ids
    assert result["counts"]["near"] == 1


def test_redaction_removes_unsafe_sources() -> None:
    unsafe = {
        "headers": {"Authorization": "Bearer secret-token"},
        "client_id": "client-123",
        "message": "call +7 999 111 22 33 with api_key=abc",
        "nested": {"ok": "safe"},
    }
    redacted = e2e.safe_value(unsafe)
    dumped = json.dumps(redacted, ensure_ascii=False).lower()
    assert "authorization" not in dumped
    assert "client-123" not in dumped
    assert "+7 999" not in dumped
    assert "api_key=abc" not in dumped
    assert redacted["nested"]["ok"] == "safe"


def test_non_search_planner_blocks_mcp_and_presenter() -> None:
    async def planner(user_text: str, scenario: dict[str, Any], timeout: int) -> dict[str, Any]:
        return {"action": "clarify", "target": "none", "search_policy": "forbidden", "confidence": 0.9, "constraints_patch": {}, "facets": {}, "missing_fields": ["location"], "clarification_fields": ["location"], "canonical_valid": True, "canonical_errors": []}

    async def forbidden_search(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("non-search planner must block MCP")

    async def forbidden_presenter(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("non-search planner must block presenter")

    result = asyncio.run(e2e.run_scenario("hard_constraints", planner_func=planner, search_func=forbidden_search, presenter_func=forbidden_presenter))
    assert result["status"] == "not_searchable"
    assert "search_mcp" not in result["stages"]
    assert "presenter" not in result["stages"]


def test_missing_structured_proof_becomes_insufficient() -> None:
    async def search(user_text: str, plan: dict[str, Any], scenario: dict[str, Any], timeout: int) -> dict[str, Any]:
        return {"facts": [{"id": "weak", "name": "ЖК Без чисел", "location": "Сокол", "price": "до 18 млн"}], "near": []}

    result = asyncio.run(e2e.run_scenario("hard_constraints", search_func=search))
    assert result["status"] == "insufficient_structured_facts"
    assert "presenter" not in result["stages"]


def test_structured_price_range_normalizes_for_budget_validation() -> None:
    plan = e2e.SCENARIOS["exact_budget"]["planner"]
    search_json = {
        "facts": [
            {"id": "within", "name": "ЖК В лимите", "location": "Москва", "price_range": "от 28,5 млн руб."},
            {"id": "outside", "name": "ЖК Выше", "location": "Москва", "price_range": "от 31 млн руб."},
        ],
        "near": [],
    }

    validation, status = e2e.validate_stage(search_json, plan)

    assert status == "ok"
    assert [item["option_id"] for item in validation["decision_context"]["matched"]] == ["within"]
    assert validation["validated"]["summary"]["rejected"] == 1


def test_ambiguous_generic_price_stays_unknown_for_budget_validation() -> None:
    plan = e2e.SCENARIOS["exact_budget"]["planner"]
    search_json = {"facts": [{"id": "ambiguous", "name": "ЖК Неясная цена", "price": "до 30 млн"}], "near": []}

    validation, status = e2e.validate_stage(search_json, plan)

    assert status == "insufficient_structured_facts"
    assert validation["validated"]["summary"]["unknown"] == 1


def test_near_only_becomes_safe_no_match_and_calls_presenter() -> None:
    seen = {"presenter": 0}

    async def search(user_text: str, plan: dict[str, Any], scenario: dict[str, Any], timeout: int) -> dict[str, Any]:
        return {"facts": [], "near": [{"id": "near_only", "name": "ЖК Почти", "why_close": "рядом"}]}

    async def presenter(decision_ctx: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        seen["presenter"] += 1
        assert decision_ctx["matched"] == []
        assert decision_ctx["relaxation_needed"] is True
        assert "near_only" not in json.dumps(decision_ctx, ensure_ascii=False)
        return {"response": "Точных совпадений нет.", "params": {}, "visible_options": [], "final_question": "Какое условие можно смягчить?"}

    result = asyncio.run(e2e.run_scenario("hard_constraints", search_func=search, presenter_func=presenter))
    assert result["status"] == "no_exact_matches"
    assert result["stages"]["validator"]["status"] == "no_exact_matches"
    assert result["invariant_checks"]["ok"] is True
    assert seen["presenter"] == 1


def test_exact_facts_absent_with_near_search_diagnostic_is_counts_only() -> None:
    diagnostic = e2e.structured_search_diagnostic(
        {
            "facts": [],
            "near": [{"id": "near_1", "name": "ЖК Почти", "phone": "+7 999 111 22 33"}],
            "raw": "model raw text",
            "headers": {"Authorization": "Bearer token"},
            "query": "client asked for private data",
        }
    )

    assert diagnostic == {
        "counts": {"facts": 0, "near": 1},
        "parsed_top_level_keys": ["facts", "near"],
        "classification": "exact_facts_absent_with_near",
    }


def test_no_candidates_search_diagnostic_classification() -> None:
    diagnostic = e2e.structured_search_diagnostic({"facts": [], "near": []})
    assert diagnostic["classification"] == "no_structured_candidates"


def test_legacy_params_delta_constraints_reject_wrong_location_and_budget() -> None:
    plan = {
        "dialog_action": "new_search",
        "confidence": 0.9,
        "canonical_valid": False,
        "canonical_errors": ["canonical_fields_absent"],
        "params_delta": {"locations": ["Сокол"], "max_price": 18_000_000},
    }
    search_json = {
        "facts": [
            {"id": "exact", "name": "ЖК Подходит", "location": "Сокол", "price_min": 17_500_000},
            {"id": "wrong_location", "name": "ЖК Не там", "location": "Печатники", "price_min": 16_000_000},
            {"id": "overbudget", "name": "ЖК Дорого", "location": "Сокол", "price_min": 19_000_000},
        ],
        "near": [],
    }

    validation, status = e2e.validate_stage(search_json, plan)
    dctx = validation["decision_context"]

    assert status == "ok"
    assert [item["option_id"] for item in dctx["matched"]] == ["exact"]
    assert validation["validated"]["summary"]["rejected"] == 2
    assert "wrong_location" not in json.dumps(dctx, ensure_ascii=False)
    assert "overbudget" not in json.dumps(dctx, ensure_ascii=False)
    assert validation["diagnostic"] == {"rejected_by_constraint_field": {"location": 1, "price": 1}}


def test_rejection_diagnostic_is_aggregate_only() -> None:
    plan = e2e.SCENARIOS["exact_budget"]["planner"]
    search_json = {
        "facts": [
            {"id": "private_candidate", "name": "ЖК Скрытый", "location": "Москва", "price_min": 31_000_000},
        ],
        "near": [],
    }

    validation, status = e2e.validate_stage(search_json, plan)
    diagnostic = validation["diagnostic"]

    assert status == "ok"
    assert diagnostic == {"rejected_by_constraint_field": {"price": 1}}
    assert "private_candidate" not in json.dumps(diagnostic, ensure_ascii=False)
    assert "ЖК Скрытый" not in json.dumps(diagnostic, ensure_ascii=False)
    assert "31" not in json.dumps(diagnostic, ensure_ascii=False)


def test_validator_stage_exposes_aggregate_rejection_diagnostic() -> None:
    result = asyncio.run(e2e.run_scenario("exact_budget"))
    assert result["stages"]["validator"]["diagnostic"] == {"rejected_by_constraint_field": {"price": 1}}


def test_search_model_flag_is_live_only() -> None:
    with pytest.raises(SystemExit, match="--search-model is only used with --live"):
        asyncio.run(e2e.async_main(["--scenario", "exact_budget", "--search-model", "google/gemini-3.5-flash"]))


def test_search_prompt_flag_is_live_only() -> None:
    with pytest.raises(SystemExit, match="--search-prompt is only used with --live"):
        asyncio.run(e2e.async_main(["--scenario", "exact_budget", "--search-prompt", "four_layer_search_v2"]))


def test_live_terminal_non_ok_status_returns_nonzero(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    async def insufficient(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "scenario": "hard_constraints",
            "mode": "live",
            "status": "insufficient_structured_facts",
            "counts": {},
            "timings": {},
            "invariant_checks": {"ok": True, "failures": []},
        }

    monkeypatch.setattr(e2e, "run_scenario", insufficient)

    code = asyncio.run(e2e.async_main(["--live", "--scenario", "hard_constraints"]))

    assert code == 1
    assert "status: insufficient_structured_facts" in capsys.readouterr().out


def test_live_no_structured_facts_status_returns_nonzero(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    async def no_structured(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "scenario": "hard_constraints",
            "mode": "live",
            "status": "no_structured_facts",
            "counts": {"facts": 0, "near": 3},
            "timings": {},
            "invariant_checks": {"ok": True, "failures": []},
        }

    monkeypatch.setattr(e2e, "run_scenario", no_structured)

    code = asyncio.run(e2e.async_main(["--live", "--scenario", "hard_constraints"]))

    assert code == 1
    assert "status: no_structured_facts" in capsys.readouterr().out


def test_presenter_prompt_flag_is_live_only() -> None:
    with pytest.raises(SystemExit, match="--presenter-prompt is only used with --live"):
        asyncio.run(e2e.async_main(["--presenter-prompt", "four_layer_presenter_v2"]))


def test_live_presenter_prompt_candidate_is_loaded_without_calling_network(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_gateway(request_data: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
        captured.update(request_data)
        payload = {"response": "Нашла ЖК Северный квартал.", "params": {}, "visible_options": [{"option_id": "exact_1", "name": "ЖК Северный квартал"}], "final_question": "Разобрать подробнее?"}
        return json.dumps(payload, ensure_ascii=False), {"ok": True}

    monkeypatch.setattr(e2e, "gateway_request", fake_gateway)

    result = asyncio.run(
        e2e.run_scenario(
            "hard_constraints",
            live=True,
            planner_func=e2e.dry_planner,
            search_func=e2e.dry_search,
            presenter_prompt="four_layer_presenter_v2",
        )
    )

    assert result["status"] == "ok"
    assert "presenter-слой" in captured["system_prompt"]
    assert result["stages"]["presenter"]["candidate"] == "four_layer_presenter_v2"


def test_live_search_prompt_candidate_is_loaded_without_calling_network(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_gateway(request_data: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
        captured.update(request_data)
        payload = {"facts": [{"id": "exact_1", "name": "ЖК Северный квартал", "location": "Сокол", "price_min": 17_500_000}], "near": []}
        return json.dumps(payload, ensure_ascii=False), {"ok": True}

    monkeypatch.setattr(e2e, "gateway_request", fake_gateway)

    result = asyncio.run(
        e2e.run_scenario(
            "hard_constraints",
            live=True,
            planner_func=e2e.dry_planner,
            presenter_func=e2e.dry_presenter,
            search_prompt="four_layer_search_v2",
        )
    )

    assert result["status"] == "ok"
    assert "four-layer" not in captured["system_prompt"].lower()
    assert "SEARCH_CONTRACT_ENVELOPE" in captured["system_prompt"]
    assert "search_v1" not in json.dumps(result, ensure_ascii=False)


def test_search_profile_composition_is_allowlisted() -> None:
    composed = e2e.compose_search_prompt_with_profile("BASE", {"profile": "family", "overlays": ["family", "mortgage", "../secret"]})
    assert composed.startswith("BASE")
    assert "Профиль MCP-поиска: family." in composed
    assert "Профиль MCP-поиска: mortgage." in composed
    assert "../secret" not in composed


def test_search_profile_flag_is_live_only() -> None:
    with pytest.raises(SystemExit, match="--search-profile is only used with --live"):
        asyncio.run(e2e.async_main(["--scenario", "exact_budget", "--search-profile", "family"]))


def test_default_search_prompt_still_loads_production_v1() -> None:
    assert e2e.load_search_prompt() == (ROOT / "prompts" / "search_v1.txt").read_text(encoding="utf-8")
