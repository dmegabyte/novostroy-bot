from __future__ import annotations

import importlib.util
import asyncio
import inspect
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nmbot_v2.contracts import OptionCard, SearchResult  # noqa: E402
from nmbot_v2.quality import build_quality_profile, evaluate_scenario  # noqa: E402
from nmbot_v2.search_contract import V2SearchRequest  # noqa: E402


SCRIPT = ROOT / "scripts" / "nmbot_v2_quality_gate.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("nmbot_v2_quality_gate", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _good_family():
    return SearchResult.from_dict(
        {
            "facts": [
                {"name": "Семейный квартал", "location": "Котельники", "price_min": 11900000, "infrastructure": ["школа", "детский сад"], "finishing": "с отделкой"},
                {"name": "Белая Дача парк", "location": "Котельники", "price_min": 12600000, "infrastructure": ["парк", "двор без машин"], "ready": "сдан"},
            ]
        }
    )


def test_good_family_scores_at_least_9() -> None:
    text = "Да, нашла два семейных варианта.\n\n1. ЖК «Семейный квартал» — Котельники, цены от 11,9 млн рублей, с отделкой, рядом: школа, детский сад.\nРядом есть школа и детский сад — это упрощает семейные будни и ежедневные маршруты.\n\n2. ЖК «Белая Дача парк» — Котельники, цены от 12,6 млн рублей, дом сдан, рядом: парк, двор без машин.\nДвор без машин даёт более спокойный ежедневный сценарий для детей, а парк добавляет понятный маршрут для прогулок.\n\nКакой вариант хотите рассмотреть подробнее?"
    report = evaluate_scenario(scenario_id="family", response_text=text, search_result=_good_family(), viewpoint="family")
    assert report.ok is True
    assert report.score >= 9


def test_hallucinated_claim_hard_fails() -> None:
    text = "ЖК «Семейный квартал» даст гарантированный рост цены и доходность 15% годовых. Какой вариант смотрим?"
    report = evaluate_scenario(scenario_id="investment", response_text=text, search_result=_good_family(), viewpoint="investment")
    assert not report.ok
    assert "unsupported_claim" in report.hard_blockers


def test_technical_leak_hard_fails() -> None:
    report = evaluate_scenario(scenario_id="family", response_text="По JSON facts[] всё ок. Какой ЖК выбрать?", search_result=_good_family(), viewpoint="family")
    assert not report.ok
    assert "technical_or_internal_leak" in report.hard_blockers

    internal = evaluate_scenario(scenario_id="family", response_text="pending_scenario: dialog_action=accept. Какой ЖК выбрать?", search_result=_good_family(), viewpoint="family")
    assert not internal.ok
    assert "technical_or_internal_leak" in internal.hard_blockers


def test_no_results_while_facts_exist_hard_fails() -> None:
    report = evaluate_scenario(scenario_id="life", response_text="Подходящих вариантов не нашла. Ослабим бюджет?", search_result=_good_family(), viewpoint="life")
    assert not report.ok
    assert "false_inventory_absence" in report.hard_blockers


def test_multiple_questions_hard_fail() -> None:
    report = evaluate_scenario(scenario_id="family", response_text="Нашла ЖК «Семейный квартал». Какой бюджет? Какой район?", search_result=_good_family(), viewpoint="family")
    assert not report.ok
    assert "question_count_not_one" in report.hard_blockers


def test_duplicate_intro_summary_hard_fails() -> None:
    text = "Да, нашла несколько вариантов.\n\nНашла 2 подходящих варианта.\n\n1. ЖК «Семейный квартал» — Котельники, рядом: школа.\nРядом есть школа — это упрощает семейные будни и ежедневные маршруты.\n\n2. ЖК «Белая Дача парк» — Котельники, рядом: парк.\nПарк рядом добавляет понятный маршрут для прогулок после школы и в выходные.\n\nКакой вариант хотите рассмотреть подробнее?"
    report = evaluate_scenario(scenario_id="family", response_text=text, search_result=_good_family(), viewpoint="family")
    assert not report.ok
    assert "duplicate_intro_summary" in report.hard_blockers


def test_white_box_anglicism_hard_fails() -> None:
    text = "Да, нашла вариант.\n\n1. ЖК «Семейный квартал» — Котельники, white box.\nОтделка уменьшает ремонтные хлопоты после ключей и оставляет больше сил на переезд.\n\nКакой вариант хотите рассмотреть подробнее?"
    report = evaluate_scenario(scenario_id="life", response_text=text, search_result=_good_family(), viewpoint="life")
    assert not report.ok
    assert "internal_enum_or_anglicism" in report.hard_blockers


def test_repeated_benefit_hard_fails() -> None:
    text = "Да, нашла два варианта.\n\n1. ЖК «Семейный квартал» — Котельники, рядом: школа.\nОтделка уменьшает ремонтные хлопоты после ключей и оставляет больше сил на сам переезд.\n\n2. ЖК «Белая Дача парк» — Котельники, рядом: парк.\nОтделка уменьшает ремонтные хлопоты после ключей и оставляет больше сил на сам переезд.\n\nКакой вариант хотите рассмотреть подробнее?"
    report = evaluate_scenario(scenario_id="family", response_text=text, search_result=_good_family(), viewpoint="family")
    assert not report.ok
    assert "repeated_identical_benefit" in report.hard_blockers


def test_ads_count_must_not_be_called_sales() -> None:
    result = SearchResult.from_dict({"facts": [{"name": "Витрина", "ads_count": 12}]})
    text = "Да, нашла вариант.\n\n1. ЖК «Витрина» — продаж: 12.\nНа витрине 12 объявлений: это показывает текущий выбор квартир, но не продажи.\n\nКакой вариант хотите рассмотреть подробнее?"
    report = evaluate_scenario(scenario_id="investment", response_text=text, search_result=result, viewpoint="investment")
    assert not report.ok
    assert "semantic_label_mismatch" in report.hard_blockers


def test_one_line_dry_cards_hard_fail() -> None:
    text = "Да, нашла два варианта.\n\n1. ЖК «Семейный квартал» — Котельники, цены от 11,9 млн рублей, школа.\n\n2. ЖК «Белая Дача парк» — Котельники, цены от 12,6 млн рублей, парк.\n\nКакой вариант хотите рассмотреть подробнее?"
    report = evaluate_scenario(scenario_id="family", response_text=text, search_result=_good_family(), viewpoint="family")
    assert not report.ok
    assert "dry_card_without_presentation_reason" in report.hard_blockers


def test_price_to_family_budget_is_a_valid_practical_benefit() -> None:
    text = "Да, нашла два варианта.\n\n1. ЖК «Семейный квартал»\nКотельники, цена от 11,9 млн рублей.\nШкола рядом упрощает семейные будни и ежедневные маршруты.\n\n2. ЖК «Белая Дача парк»\nКотельники, цена от 12,6 млн рублей, дом сдан.\nЦена помогает заранее сверить вариант с семейным бюджетом и понять доступный запас.\n\nКакой вариант хотите рассмотреть подробнее?"
    report = evaluate_scenario(scenario_id="family", response_text=text, search_result=_good_family(), viewpoint="family")

    assert "dry_card_without_presentation_reason" not in report.hard_blockers


def test_literal_ads_showcase_count_is_a_valid_investment_explanation() -> None:
    result = SearchResult.from_dict({"facts": [
        {"name": "Первый", "location": "Москва", "price_min": 7_500_000, "ready": "сдан", "ads_count": 2352},
        {"name": "Второй", "location": "Москва", "price_min": 7_800_000, "ready": "сдан", "ads_count": 4792},
    ]})
    text = "Нашла два варианта.\n\n1. Первый\nМосква, цена от 7 500 000 рублей, дом сдан, 2352 объявления.\nЦена задаёт понятный ориентир по бюджету и порогу входа.\n\n2. Второй\nМосква, цена от 7 800 000 рублей, дом сдан, 4792 объявления.\nСейчас по комплексу есть 4792 объявления — это показывает текущее количество предложений на витрине.\n\nКакой вариант рассмотреть подробнее?"
    report = evaluate_scenario(scenario_id="investment", response_text=text, search_result=result, viewpoint="investment")

    assert "dry_card_without_presentation_reason" not in report.hard_blockers


def test_family_investment_rental_life_financing_overlays_are_checked() -> None:
    harness = _load_harness()
    for case in ["family", "investment", "rental", "life", "family_financing_overlay"]:
        result = harness.run_case(case)
        assert result["ok"], (case, result["report"])
        assert result["score"] >= 9


def test_all_15_offline_scenarios_exercise_search_response_and_report_structure() -> None:
    harness = _load_harness()
    records = harness.quality_records()
    assert len(records) == 15
    results = [harness.run_case(record["id"]) for record in records]
    assert all(item["ok"] for item in results), [item for item in results if not item["ok"]]
    for item in results:
        report = item["report"]
        assert set(["scenario", "search_mcp", "card", "response", "dimensions", "score", "verdict", "layer_to_fix"]) <= set(report)
        assert item["response_text"].count("?") == 1


def test_quality_prompt_provenance_distinguishes_configured_and_invoked_sets() -> None:
    harness = _load_harness()

    offline = harness.quality_prompt_provenance(live=False)
    assert offline["coverage"] == "configured_only"
    assert {item["usage"] for item in offline["prompts"]} == {"configured"}
    assert {item["stage"] for item in offline["prompts"]} == {
        "search", "response_composer", "response_writer", "response_formatter",
    }

    live = harness.quality_prompt_provenance(live=True)
    assert live["coverage"] == "complete"
    assert {item["usage"] for item in live["prompts"]} == {"invoked"}
    assert {item["stage"] for item in live["prompts"]} == {"search", "response_composer"}


def test_cli_default_json_summary_and_first_failure_mode() -> None:
    completed = subprocess.run([sys.executable, str(SCRIPT), "--case", "family"], cwd=ROOT, text=True, capture_output=True, check=True)
    data = json.loads(completed.stdout)
    assert data["network"] is False
    assert data["ok"] is True
    assert data["scores"]["family"] >= 9
    assert data["results"][0]["quality_profile"]["evidence"] == "offline"
    assert data["results"][0]["quality_profile"]["scores"]["latency"] is None


def _quality_search_output(case_id: str = "family") -> dict:
    fixture = json.loads((ROOT / "tests" / "fixtures" / "nmbot_v2_quality_scenarios.json").read_text(encoding="utf-8"))
    return next(item for item in fixture["records"] if item["id"] == case_id)["search_output"]


def _family_structured_response() -> dict:
    return {
        "intro": "Да, нашла семейные варианты.",
        "options": [
            {"name": "Семейный квартал", "facts": "Котельники, цены от 11,9 млн рублей, с отделкой, рядом школа и детский сад.", "description": "Это удобно для семейных будней: школа и сад закрывают ежедневные маршруты рядом с домом."},
            {"name": "Белая Дача парк", "facts": "Котельники, цены от 12,6 млн рублей, дом сдан, рядом парк и двор без машин.", "description": "Готовый дом помогает планировать переезд, а двор без машин добавляет спокойствия для детей."},
            {"name": "Лесной берег", "facts": "Котельники, цены от 13,2 млн рублей, с отделкой, рядом спортивная площадка и вода.", "description": "Это полезно семье, если важны активные прогулки после школы."},
        ],
        "missing_note": "",
        "final_question": "Какой вариант хотите рассмотреть подробнее?",
    }


def test_live_case_with_fake_gateway_renders_and_scores_good_output() -> None:
    harness = _load_harness()
    output = _quality_search_output("family")

    async def fake_gateway(_request_data, _timeout):
        return json.dumps(output, ensure_ascii=False), {"ok": True, "task_id": "must-not-leak"}

    async def fake_composer(_request_data, _timeout, **_kwargs):
        return json.dumps(_family_structured_response(), ensure_ascii=False), {"ok": True}

    result = asyncio.run(harness.run_live_case("family", timeout=3, gateway_func=fake_gateway, composer_func=fake_composer))

    assert result["ok"], result
    assert result["counts"] == {"facts": 3, "near": 0, "missing": 0}
    assert result["score"] >= 9
    assert result["quality_profile"]["composer_status"] == "primary"
    assert result["quality_profile"]["scores"] == {
        "search_accuracy": 10,
        "data_integrity": 10,
        "presentation_quality": 10,
        "language_quality": 10,
        "scenario_fit": 10,
        "dialogue_continuity": 10,
        "reliability": 10,
        "latency": 10,
    }
    assert result["quality_profile"]["overall"] == 10
    assert result["quality_profile"]["maturity"] == "production_candidate"
    assert result["quality_profile"]["gate_pass"] is True
    assert "Семейный квартал" in result["response_text"]
    assert result["composer_attempts"][0]["attempt_kind"] == "primary"
    assert "task_id" not in json.dumps(result, ensure_ascii=False)


def test_live_quality_fake_path_enriches_before_composer() -> None:
    harness = _load_harness()
    output = _quality_search_output("family")
    calls = {"search": 0, "enrichment": 0}

    async def fake_gateway(request_data, _timeout):
        query = str(request_data.get("query") or "")
        if "full_card" not in query:
            calls["search"] += 1
            return json.dumps(output, ensure_ascii=False), {"ok": True}
        calls["enrichment"] += 1
        name = query.split("канонического ЖК «", 1)[1].split("»", 1)[0]
        enriched = dict(output)
        base_fact = next((dict(item) for item in output.get("facts", []) if item.get("name") == name), {"name": name, "location": "Котельники", "min_price": 11_900_000})
        base_fact["developer"] = "ПИК"
        enriched["facts"] = [base_fact]
        enriched["near"] = []
        enriched["missing"] = []
        enriched["params"] = {}
        return json.dumps(enriched, ensure_ascii=False), {"ok": True}

    async def composer(request_data, _timeout, **_kwargs):
        input_part = request_data["query"].split("V2_RESPONSE_BRIEF=", 1)[1].split("\n", 1)[0]
        brief = json.loads(input_part)["brief"]
        assert brief["canonical_cards"]
        return json.dumps(_family_structured_response(), ensure_ascii=False), {"ok": True}

    result = asyncio.run(harness.run_live_case("family", timeout=3, gateway_func=fake_gateway, composer_func=composer))

    assert result["ok"], result
    assert calls == {"search": 1, "enrichment": 3}
    assert result["enrichment"]["applied_count"] == 3


def test_quality_can_evaluate_post_recovery_result_without_rejecting_initial_wire_again() -> None:
    request = V2SearchRequest(
        search_goal={"entity_type": "new_building_flat", "query_summary": "family search", "explicit_terms": ["family"]},
        requested_hard={"rooms": [2]},
        effective_hard={"rooms": [2]},
        preferences={},
        response_viewpoint="family",
        available_fact_fields=["name", "rooms"],
        count=3,
    )
    recovered = SearchResult(facts=(), near=(), missing=("rooms",), params={"rooms": [2]})

    report = evaluate_scenario(
        scenario_id="family",
        response_text="Не удалось подтвердить двухкомнатные варианты. Показать близкие варианты без подтверждённой комнатности?",
        search_result=recovered,
        search_output=None,
        search_request=request,
        viewpoint="family",
    )

    assert "search_contract_invalid" not in report.hard_blockers


def test_live_case_contract_failure_returns_search_layer_without_fabricated_response() -> None:
    harness = _load_harness()
    bad_output = {"response": "not allowed"}

    async def fake_gateway(_request_data, _timeout):
        return json.dumps(bad_output, ensure_ascii=False), {"ok": True}

    result = asyncio.run(harness.run_live_case("family", timeout=3, gateway_func=fake_gateway))

    assert not result["ok"]
    assert result["score"] == 0
    assert result["layer_to_fix"] == "search"
    assert result["response_text"] == ""
    assert "search_contract_invalid" in result["hard_blockers"]
    assert result["quality_profile"]["scores"]["search_accuracy"] == 0
    assert result["quality_profile"]["gate_pass"] is False


def test_live_case_customer_response_quality_failure_is_response_layer(monkeypatch) -> None:
    harness = _load_harness()
    output = _quality_search_output("family")

    async def fake_gateway(_request_data, _timeout):
        return json.dumps(output, ensure_ascii=False), {"ok": True}

    async def bad_composer(_request_data, _timeout, **_kwargs):
        return json.dumps({"intro": "По JSON facts[] всё ок", "options": [], "missing_note": "", "final_question": "Какой ЖК выбрать?"}, ensure_ascii=False), {"ok": True}

    result = asyncio.run(harness.run_live_case("family", timeout=3, gateway_func=fake_gateway, composer_func=bad_composer))

    assert result["ok"] is False
    assert result["score"] <= 8
    assert "По JSON facts[] всё ок" in result["response_text"]
    assert "composer_degraded_fallback" not in result["hard_blockers"]
    assert "internal_or_raw_wire_leak" in result["composer_attempts"][0]["validation_warnings"]
    assert result["quality_profile"]["composer_status"] == "primary"
    assert result["quality_profile"]["scores"]["reliability"] == 10
    assert result["quality_profile"]["gate_pass"] is False
    assert result["composer_attempts"]


def test_live_case_invalid_composer_is_repaired_once() -> None:
    harness = _load_harness()
    output = _quality_search_output("family")
    async def fake_gateway(_request_data, _timeout):
        return json.dumps(output, ensure_ascii=False), {"ok": True}

    async def repairing_composer(request_data, _timeout, **_kwargs):
        if "repair_validation_errors" in request_data.get("query", ""):
            return json.dumps(_family_structured_response(), ensure_ascii=False), {"ok": True}
        return "not json", {"ok": True}

    result = asyncio.run(harness.run_live_case("family", timeout=3, gateway_func=fake_gateway, composer_func=repairing_composer))

    assert result["ok"] is True, result
    assert result["quality_profile"]["composer_status"] == "repaired"
    assert result["quality_profile"]["scores"]["reliability"] == 7
    assert result["quality_profile"]["gate_pass"] is True
    assert "composer_degraded_fallback" not in result["hard_blockers"]
    assert [item["attempt_kind"] for item in result["composer_attempts"]] == ["primary", "repair"]


def test_live_case_empty_response_falls_back_without_provider_retry() -> None:
    harness = _load_harness()
    output = _quality_search_output("family")
    calls = []

    async def fake_gateway(_request_data, _timeout):
        return json.dumps(output, ensure_ascii=False), {"ok": True}

    async def empty_then_retry(request_data, _timeout, **_kwargs):
        calls.append(request_data)
        if len(calls) == 1:
            return "", {"ok": True}
        return _family_structured_response(), {"ok": True}

    result = asyncio.run(harness.run_live_case("family", timeout=3, gateway_func=fake_gateway, composer_func=empty_then_retry))

    assert result["quality_profile"]["composer_status"] == "fallback"
    assert result["quality_profile"]["scores"]["reliability"] == 3
    assert len(calls) == 1
    assert "repair_validation_errors" not in calls[0]["query"]
    assert calls[0]["model"] == "google/gemini-2.5-flash"
    assert [item["attempt_kind"] for item in result["composer_attempts"]] == ["primary"]


def test_quality_harness_keeps_composer_orchestration_but_runtime_does_not() -> None:
    assert "compose_response_async" in inspect.getsource(_load_harness().compose_case_live)
    assert "compose_response_async" not in (ROOT / "nmbot_v2" / "runtime.py").read_text(encoding="utf-8")


def test_live_case_slow_response_lowers_latency_score(monkeypatch) -> None:
    harness = _load_harness()
    output = _quality_search_output("family")

    class FakeTime:
        def __init__(self):
            self.calls = 0

        def monotonic(self):
            self.calls += 1
            return 0.0 if self.calls == 1 else 95.0

    monkeypatch.setattr(harness, "time", FakeTime())

    async def fake_gateway(_request_data, _timeout):
        return json.dumps(output, ensure_ascii=False), {"ok": True}

    async def fake_composer(_request_data, _timeout, **_kwargs):
        return json.dumps(_family_structured_response(), ensure_ascii=False), {"ok": True}

    result = asyncio.run(harness.run_live_case("family", timeout=3, gateway_func=fake_gateway, composer_func=fake_composer))

    assert result["elapsed"] == 95.0
    assert result["quality_profile"]["scores"]["latency"] == 2
    assert result["quality_profile"]["overall"] == 9.6


def test_hard_blocker_overrides_high_quality_profile_gate() -> None:
    profile = build_quality_profile(
        dimensions={"facts": 2, "completeness": 2, "beauty": 2, "scenario_fit": 2, "dialogue": 2},
        hard_blockers=["unsupported_claim"],
        search_ok=True,
        evidence="live",
        composer_status="primary",
        latency_seconds=10,
    )

    assert profile["overall"] == 10
    assert profile["maturity"] == "failed_gate"
    assert profile["gate_pass"] is False


def test_live_all_stops_on_first_failure_with_fake_gateway(monkeypatch) -> None:
    harness = _load_harness()
    calls: list[str] = []

    async def fake_run_live_case(case_id, *, timeout=90, gateway_func=None):
        calls.append(case_id)
        return harness._failure_result(case_id, started=0.0, layer="search", blockers=["search_contract_invalid"])

    monkeypatch.setattr(harness, "run_live_case", fake_run_live_case)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--live", "--all"])

    code = asyncio.run(harness.async_main())

    assert code == 1
    assert calls == ["base_search"]


def test_live_transport_failure_safe_output_does_not_leak_secret_payload() -> None:
    harness = _load_harness()

    async def fake_gateway(_request_data, _timeout):
        raise RuntimeError("secret TOKEN raw payload task_id provider response")

    result = asyncio.run(harness.run_live_case("family", timeout=3, gateway_func=fake_gateway))
    rendered = json.dumps(result, ensure_ascii=False)

    assert not result["ok"]
    assert result["score"] == 0
    assert result["layer_to_fix"] == "transport/search"
    assert "gateway_network_failed" in result["hard_blockers"]
    assert "TOKEN" not in rendered
    assert "raw payload" not in rendered
    assert "task_id" not in rendered


def test_initial_empty_search_relaxation_and_absence_fail_quality_gate() -> None:
    request = V2SearchRequest(search_goal={"query": "base_search"}, requested_hard={}, effective_hard={})
    output = {"facts": [], "near": [], "missing": ["location"], "params": {}, "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "response_viewpoint": "life", "base_viewpoint": None, "requested_field_priorities": [], "relaxation_audit": [], "ignored_preferences": [], "notes": []}}
    text = "Точно таких вариантов сейчас не вижу. Ослабим один параметр?"

    report = evaluate_scenario(scenario_id="base_search", response_text=text, search_output=output, search_request=request, viewpoint="life")

    assert not report.ok
    assert "initial_empty_search_relaxation" in report.hard_blockers
    assert "initial_empty_false_absence" in report.hard_blockers


def test_initial_clarification_question_does_not_invent_metro_fact() -> None:
    harness = _load_harness()
    request = harness.request_from_contract(harness.scenario_map()["base_search"])
    output = {
        "facts": [],
        "near": [],
        "missing": ["request needs search parameters"],
        "params": {},
        "diagnostics": {
            "mcp_tool": "novostroym/get_flat_info",
            "response_viewpoint": "life",
            "base_viewpoint": None,
            "requested_field_priorities": [],
            "relaxation_audit": [],
            "ignored_preferences": [],
            "notes": [],
        },
    }
    response = "Поняла, начнём с одного уточнения.\n\nВ какой локации или у какого метро искать?"
    report = evaluate_scenario(
        scenario_id="base_search",
        response_text=response,
        search_result=SearchResult(),
        search_output=output,
        search_request=request,
        viewpoint="life",
    )
    assert "invented_fact" not in report.hard_blockers
    assert report.ok


def test_missing_security_caveat_is_not_a_security_claim() -> None:
    card = OptionCard(name="Семейный", location="Москва", price_min=12_000_000, ready="сдан")
    response = (
        "Да, нашла вариант.\n\n"
        "1. ЖК «Семейный» — Москва, цены от 12 млн рублей, дом сдан.\n"
        "Готовый дом позволяет планировать переезд без ожидания стройки.\n\n"
        "Не хватает подтверждения части данных по безопасности.\n\n"
        "Рассказать подробнее?"
    )
    report = evaluate_scenario(
        scenario_id="family_financing_overlay",
        response_text=response,
        search_result=SearchResult(facts=(card,), missing=("security",)),
        viewpoint="financing",
        base_viewpoint="family",
    )
    assert "invented_fact" not in report.hard_blockers


def test_unknown_complex_absent_from_facts_and_near_still_hard_fails() -> None:
    result = SearchResult.from_dict(
        {
            "facts": [{"name": "Котельники Старт", "location": "Котельники", "min_price": 8_900_000}],
            "near": [{"name": "Белая Дача парк", "location": "Котельники", "min_price": 9_600_000, "why_close": "чуть выше бюджета"}],
        }
    )
    text = (
        "Да, нашла варианты.\n\n"
        "1. ЖК «Котельники Старт» — Котельники, цены от 8,9 млн рублей.\n"
        "Цена попадает в заданный бюджет.\n\n"
        "2. ЖК «Несуществующий парк» — Котельники, цены от 9,6 млн рублей.\n"
        "Это близкая альтернатива, но выше бюджета.\n\n"
        "Какой вариант хотите рассмотреть подробнее?"
    )

    report = evaluate_scenario(scenario_id="exact_facts_vs_near", response_text=text, search_result=result, viewpoint="life")

    assert not report.ok
    assert "invented_fact" in report.hard_blockers
    assert "unknown_complex:Несуществующий парк" in report.issues


def test_missing_sales_caveat_is_not_positive_sales_claim() -> None:
    card = OptionCard(name="Лучи", ads_count=12, location="Москва", price_min=12_000_000)
    text = "Нашла вариант.\n\n1. ЖК «Лучи» — Москва, цены от 12 млн рублей, на витрине 12 объявлений.\nЭто буквальный счётчик объявлений.\n\nНет подтверждённых данных о продажах.\n\nРассказать подробнее?"

    report = evaluate_scenario(scenario_id="investment", search_result=SearchResult(facts=(card,), missing=("sales",)), response_text=text)

    assert "semantic_label_mismatch" not in report.hard_blockers


def test_raw_missing_prose_and_duplicate_punctuation_fail_quality_gate() -> None:
    request = V2SearchRequest(search_goal={"query": "base_search"}, requested_hard={}, effective_hard={})
    raw_missing = "нет данных по фильтрам из-за неопределённого запроса"
    output = {"facts": [], "near": [], "missing": [raw_missing], "params": {}, "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "response_viewpoint": "life", "base_viewpoint": None, "requested_field_priorities": [], "relaxation_audit": [], "ignored_preferences": [], "notes": []}}
    text = f"Уточню один момент.. В данных не хватает подтверждения: {raw_missing}. В какой локации искать?"

    report = evaluate_scenario(scenario_id="base_search", response_text=text, search_output=output, search_request=request, viewpoint="life")

    assert not report.ok
    assert "raw_missing_leak" in report.hard_blockers
    assert "duplicate_punctuation" in report.hard_blockers
