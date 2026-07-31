from __future__ import annotations

import importlib.util
import asyncio
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_shortlist_composer_live_hypothesis.py"
SPEC = importlib.util.spec_from_file_location("shortlist_live_hypothesis", SCRIPT)
live = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(live)


def test_request_is_composer_only_and_temperature_is_explicit():
    case = live.load_case(live.DEFAULT_CASE)
    request = live.build_request(case, temperature=0.4, model="test/model")

    assert request["_payload_stage"] == "conversation_answer"
    assert request["parameters"]["temperature"] == 0.4
    assert request["model"] == "test/model"
    assert "mcp_servers" not in request
    assert "external_api_key" not in request
    assert "comparison_context" in request["query"]

    repair = live.build_request(case, temperature=0.4, model="test/model", repair_errors=("bureaucratic_style",))
    assert "repair_validation_errors" in repair["query"]
    assert "живым русским языком" in repair["query"]

    repair = live.build_request(case, temperature=0.4, model="test/model", repair_errors=("recommendation_cta_repetition",))
    assert "коротким выводом" in repair["query"]

    repair = live.build_request(case, temperature=0.4, model="test/model", repair_errors=("unavailable_field_claim",))
    assert "shared_field_ids" in repair["query"]

    repair = live.build_request(case, temperature=0.4, model="test/model", repair_errors=("internal_leak",))
    assert "по известным фактам" in repair["query"]


def test_valid_fake_gateway_candidate_passes_same_isolated_validator():
    case = live.load_case(live.DEFAULT_CASE)

    async def gateway(request, timeout):
        assert timeout == 10
        assert request["parameters"]["temperature"] == 0.25
        return json.dumps(case["candidate"], ensure_ascii=False), {"ok": True, "metadata_keys": ["usage"]}

    result = asyncio.run(live.run_live_case(temperature=0.25, timeout=10, gateway_func=gateway))

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["text"].count("?") == 1
    assert result["gateway_meta"] == {"ok": True, "metadata_keys": ["usage"]}
    assert result["status"] == "primary"
    assert result["attempts"] == 1


def test_invalid_candidate_fails_closed_but_keeps_synthetic_diagnostic_candidate():
    case = live.load_case(live.DEFAULT_CASE)
    candidate = dict(case["candidate"])
    candidate["final_question"] = "Другой вопрос?"

    async def gateway(_request, _timeout):
        return json.dumps(candidate, ensure_ascii=False), {"ok": True}

    result = asyncio.run(live.run_live_case(temperature=0.4, gateway_func=gateway))

    assert result["ok"] is False
    assert "cta_mismatch" in result["errors"]
    assert result["text"] == ""
    assert result["candidate"]["final_question"] == "Другой вопрос?"
    assert result["attempts"] == 2


def test_one_bounded_semantic_repair_can_recover_candidate():
    case = live.load_case(live.DEFAULT_CASE)
    bad = json.loads(json.dumps(case["candidate"], ensure_ascii=False))
    bad["options"][0]["presentation"] = "В Бусиновском районе цена — 12 417 930 рублей. Это является самым предпочтительным вариантом."
    calls = []

    async def gateway(request, _timeout):
        calls.append(request)
        candidate = bad if len(calls) == 1 else case["candidate"]
        return json.dumps(candidate, ensure_ascii=False), {"ok": True}

    result = asyncio.run(live.run_live_case(temperature=0.4, gateway_func=gateway))

    assert result["ok"] is True
    assert result["status"] == "repaired"
    assert result["attempts"] == 2
    assert "bureaucratic_style" in result["initial_errors"]
    assert len(calls) == 2


def test_redundant_recommendation_is_safely_blank_without_second_call():
    case = live.load_case("shortlist_one_true_unique_feature")
    candidate = json.loads(json.dumps(case["candidate"], ensure_ascii=False))
    candidate["recommendation"] = "Выбор зависит от того, что для вас важнее: минимальная цена или наличие террасы."
    calls = 0

    async def gateway(_request, _timeout):
        nonlocal calls
        calls += 1
        return json.dumps(candidate, ensure_ascii=False), {"ok": True}

    result = asyncio.run(live.run_live_case("shortlist_one_true_unique_feature", temperature=0.4, gateway_func=gateway))

    assert result["ok"] is True
    assert result["status"] == "sanitized"
    assert result["attempts"] == 1
    assert result["candidate"]["recommendation"] == ""
    assert result["postprocess"] == ["blank_redundant_recommendation"]
    assert calls == 1


def test_known_internal_wording_is_safely_replaced_and_revalidated():
    case = live.load_case("shortlist_no_unique_advantage_same_values")
    candidate = json.loads(json.dumps(case["candidate"], ensure_ascii=False))
    candidate["options"][0]["presentation"] = candidate["options"][0]["presentation"].replace("Отдельного преимущества", "Подтвержденного преимущества")
    calls = 0

    async def gateway(_request, _timeout):
        nonlocal calls
        calls += 1
        return json.dumps(candidate, ensure_ascii=False), {"ok": True}

    result = asyncio.run(live.run_live_case("shortlist_no_unique_advantage_same_values", temperature=0.4, gateway_func=gateway))

    assert result["ok"] is True
    assert result["attempts"] == 1
    assert result["status"] == "sanitized"
    assert "явного преимущества" in result["text"]
    assert "подтвержден" not in result["text"].lower()
    assert result["postprocess"] == ["sanitize_internal_wording"]
    assert calls == 1


def test_exact_own_name_prefix_is_removed_then_candidate_is_revalidated():
    case = live.load_case(live.DEFAULT_CASE)
    candidate = json.loads(json.dumps(case["candidate"], ensure_ascii=False))
    first = candidate["options"][0]
    first["presentation"] = first["object_name"] + " расположен в Бусиновском районе, цена квартиры — 12 417 930 рублей. Это самый доступный вариант."
    calls = 0

    async def gateway(_request, _timeout):
        nonlocal calls
        calls += 1
        return json.dumps(candidate, ensure_ascii=False), {"ok": True}

    result = asyncio.run(live.run_live_case(temperature=0.4, gateway_func=gateway))

    assert result["ok"] is True
    assert result["attempts"] == 1
    assert result["status"] == "sanitized"
    assert result["candidate"]["options"][0]["presentation"].startswith("Расположен")
    assert result["postprocess"] == ["strip_repeated_option_name_prefix"]
    assert calls == 1


def test_known_bureaucratic_phrase_is_replaced_and_revalidated():
    case = live.load_case("shortlist_parking_vs_lower_price")
    candidate = json.loads(json.dumps(case["candidate"], ensure_ascii=False))
    candidate["options"][0]["presentation"] = candidate["options"][0]["presentation"].replace("могут быть причиной смотреть его", "делают так, что этот вариант может быть предпочтительнее")
    calls = 0

    async def gateway(_request, _timeout):
        nonlocal calls
        calls += 1
        return json.dumps(candidate, ensure_ascii=False), {"ok": True}

    result = asyncio.run(live.run_live_case("shortlist_parking_vs_lower_price", temperature=0.4, gateway_func=gateway))

    assert result["ok"] is True
    assert result["status"] == "sanitized"
    assert "этот вариант стоит рассмотреть" in result["text"].lower()
    assert "предпочтитель" not in result["text"].lower()
    assert result["postprocess"] == ["sanitize_known_bureaucratic_wording"]
    assert calls == 1


def test_investment_counter_recommendation_is_removed_then_internal_wording_sanitized():
    case = live.load_case("shortlist_investment_literal_counters")
    candidate = json.loads(json.dumps(case["candidate"], ensure_ascii=False))
    candidate["intro"] = "Счётчики продаж и объявлений — буквальные данные без рыночного или финансового прогноза."
    candidate["recommendation"] = "Если счётчики для вас приоритетны, второй вариант интереснее."
    calls = 0

    async def gateway(_request, _timeout):
        nonlocal calls
        calls += 1
        return json.dumps(candidate, ensure_ascii=False), {"ok": True}

    result = asyncio.run(live.run_live_case("shortlist_investment_literal_counters", temperature=0.4, gateway_func=gateway))

    assert result["ok"] is True
    assert result["candidate"]["recommendation"] == ""
    assert "буквальными числами" in result["text"]
    assert result["postprocess"] == ["blank_investment_counter_recommendation", "sanitize_internal_wording"]
    assert calls == 1


def test_parse_candidate_accepts_json_fence_and_rejects_non_object():
    parsed, errors = live.parse_candidate('```json\n{"intro":"x"}\n```')
    assert parsed == {"intro": "x"}
    assert errors == []
    assert live.parse_candidate("[]") == (None, ["json_root_not_object"])
