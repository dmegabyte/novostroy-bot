from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "field_sales_registry" / "v1"
SPEC = importlib.util.spec_from_file_location("answer_composer_simulator", REGISTRY / "answer_composer_simulator.py")
composer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(composer)


def example_input():
    return json.loads((REGISTRY / "example_answer_composer_input.json").read_text(encoding="utf-8"))


def example_candidate():
    return json.loads((REGISTRY / "example_answer_composer_candidate.json").read_text(encoding="utf-8"))


def result_for(candidate, data=None):
    return composer.simulate(data or example_input(), candidate)


def assert_error(code, candidate, data=None):
    result = result_for(candidate, data)
    assert result["valid"] is False
    assert code in result["errors"]
    assert result["text"] == ""
    assert "intro" not in json.dumps(result, ensure_ascii=False)


def test_model_input_strips_diagnostics_and_contains_only_allowed_projection():
    package = composer.build_model_input(example_input())
    dumped = json.dumps(package, ensure_ascii=False)

    assert package["input"]["scenario"] == "family"
    assert "system_prompt" in package
    assert "output_contract" in package
    assert "diagnostics" not in json.dumps(package["input"], ensure_ascii=False)
    assert "seller_phone" not in json.dumps(package["input"], ensure_ascii=False)
    assert "required_evidence" not in dumped
    assert {field["field_id"] for field in package["input"]["fields"]} == {"school", "kindergarten", "children_ground"}


def test_model_input_rejects_invalid_deep_brief_shape_and_bad_cta():
    data = example_input()
    data["brief"]["fields"][0]["unexpected"] = "nope"
    with pytest.raises(ValueError, match="invalid composer input"):
        composer.build_model_input(data)

    data = example_input()
    data["cta_template"] = "Первый вопрос? Второй вопрос?"
    with pytest.raises(ValueError, match="invalid composer input"):
        composer.build_model_input(data)


def test_valid_family_example_passes_and_result_regenerates_exactly():
    expected = json.loads((REGISTRY / "example_answer_composer_result.json").read_text(encoding="utf-8"))

    assert result_for(example_candidate()) == expected


def test_unknown_field_and_combination_ids_rejected():
    candidate = example_candidate()
    candidate["used_field_ids"] = ["school", "unknown_field"]
    candidate["used_combination_ids"] = ["unknown_combo"]

    result = result_for(candidate)
    assert "unknown_field_id" in result["errors"]
    assert "unknown_combination_id" in result["errors"]


def test_unknown_number_rejected_and_known_formatted_numeric_value_accepted():
    data = example_input()
    data["brief"]["scenario"] = "budget"
    data["brief"]["fields"] = [
        {
            "field_id": "apartment_price",
            "label": "цена от",
            "value": 12300000,
            "literal_meaning": "Минимальная цена в карточке.",
            "allowed_benefit": "Можно сравнивать по входному бюджету без обещания наличия.",
            "strength": "strong",
            "required_evidence": ["fresh MCP response"],
            "forbidden_claims": ["Не обещать наличие или сохранение цены."],
            "rendering_rules": ["Писать цену буквально."],
        }
    ]
    data["brief"]["combinations"] = []
    candidate = {
        "intro": "Да, по бюджету есть понятная точка для сравнения.",
        "fact_summary": "Цена от 12 300 000 рублей.",
        "benefit": "Можно сравнивать по входному бюджету без обещания наличия.",
        "caveat": "Цена требует свежей проверки перед решением.",
        "final_question": data["cta_template"],
        "used_field_ids": ["apartment_price"],
        "used_combination_ids": [],
    }
    assert result_for(candidate, data)["valid"] is True
    candidate["fact_summary"] = "Цена от 13 300 000 рублей."
    assert_error("unknown_number", candidate, data)


def test_boolean_feature_must_be_anchored_by_label():
    candidate = example_candidate()
    candidate["fact_summary"] = "В Синтетический семейный ЖК отмечены семейные признаки."
    candidate["benefit"] = "Можно отметить семейную логистику, если школа подтверждена в карточке."
    candidate["used_field_ids"] = ["kindergarten"]
    candidate["used_combination_ids"] = []

    assert_error("ungrounded_field", candidate)


def test_internal_terms_contact_url_code_fence_and_braces_rejected():
    for text in ("MCP сказал хороший вариант.", "Позвоните +7 999 123-45-67.", "Смотрите http://example.test.", "```json", "{payload}"):
        candidate = example_candidate()
        candidate["caveat"] = text
        result = result_for(candidate)
        assert result["valid"] is False
        assert set(result["errors"]) & {"internal_leak", "contact_or_url"}


def test_second_question_question_outside_final_and_rephrased_cta_rejected():
    candidate = example_candidate()
    candidate["intro"] = "Подойдёт?"
    assert_error("question_contract", candidate)

    candidate = example_candidate()
    candidate["final_question"] = "Разобрать этот вариант подробнее?"
    assert_error("cta_mismatch", candidate)


def test_unsupported_claim_patterns_rejected():
    bad_phrases = [
        "Это гарантированно идеальный вариант.",
        "Можно ждать доходность и окупаемость.",
        "Будет ликвидность и рост цены.",
        "Там высокий спрос.",
        "Одобрение ипотеки почти гарантировано.",
        "Будет лучшая ставка без переплаты.",
        "Можно сразу переехать и получить ключи.",
        "Места в школе точно будут.",
    ]
    for phrase in bad_phrases:
        candidate = example_candidate()
        candidate["benefit"] = phrase
        assert_error("unsupported_claim", candidate)


def test_literal_sales_and_ads_counts_accepted_only_without_demand_inference():
    data = example_input()
    data["brief"]["scenario"] = "investment"
    data["brief"]["fields"] = [
        {"field_id": "sales_count", "label": "сделки ЕГРН", "value": 0, "literal_meaning": "Буквальный счётчик.", "allowed_benefit": "Можно назвать только сам счётчик сделок ЕГРН как справочный факт.", "strength": "weak", "required_evidence": ["fresh"], "forbidden_claims": ["Не обещать ликвидность."], "rendering_rules": ["Буквально."]},
        {"field_id": "ads_count", "label": "объявления на витрине", "value": 7, "literal_meaning": "Буквальный счётчик.", "allowed_benefit": "Можно назвать только количество объявлений как справочный факт по витрине.", "strength": "weak", "required_evidence": ["fresh"], "forbidden_claims": ["Не выводить спрос."], "rendering_rules": ["Буквально."]},
    ]
    data["brief"]["combinations"] = []
    candidate = {"intro": "По счётчикам могу назвать только факты.", "fact_summary": "Указаны сделки ЕГРН 0 и объявления на витрине 7.", "benefit": "Можно назвать только количество объявлений как справочный факт по витрине.", "caveat": "Это не прогноз.", "final_question": data["cta_template"], "used_field_ids": ["sales_count", "ads_count"], "used_combination_ids": []}
    assert result_for(candidate, data)["valid"] is True
    candidate["benefit"] = "Это показывает высокий спрос."
    assert_error("unsupported_claim", candidate, data)


def test_object_name_mismatch_rejected():
    candidate = example_candidate()
    candidate["intro"] = "Да, ЖК «Чужой» можно рассмотреть для семейного сценария."

    assert_error("object_name_mismatch", candidate)

    candidate = example_candidate()
    candidate["intro"] = "Да, ЖК Чужой можно рассмотреть для семейного сценария."
    assert_error("object_name_mismatch", candidate)


def test_internal_customer_language_is_rejected():
    for text in ("По карточке есть школа.", "По данным есть школа.", "Контекст подтверждён.", "Evidence получен из source_field."):
        candidate = example_candidate()
        candidate["caveat"] = text
        assert_error("internal_leak", candidate)


def test_literal_booking_status_is_allowed_but_booking_promise_is_not():
    data = example_input()
    data["brief"]["scenario"] = "general"
    data["brief"]["fields"] = [
        {
            "field_id": "lot_status",
            "label": "статус лота",
            "value": "бронь",
            "literal_meaning": "Человекочитаемый статус конкретного лота.",
            "allowed_benefit": "Статус помогает понять, что именно нужно перепроверить перед следующим шагом.",
            "strength": "weak",
            "required_evidence": ["fresh"],
            "forbidden_claims": ["Не обещать сохранение брони."],
            "rendering_rules": ["Писать статус буквально."],
        }
    ]
    data["brief"]["combinations"] = []
    candidate = {
        "intro": "По конкретному лоту есть короткое уточнение.",
        "fact_summary": "Статус лота — бронь.",
        "benefit": "Статус помогает понять, что именно нужно перепроверить перед следующим шагом.",
        "caveat": "Актуальность статуса лучше проверить перед решением.",
        "final_question": data["cta_template"],
        "used_field_ids": ["lot_status"],
        "used_combination_ids": [],
    }
    assert result_for(candidate, data)["valid"] is True
    candidate["caveat"] = "Бронь точно сохранится."
    assert_error("unsupported_claim", candidate, data)


def test_used_combination_requires_safe_phrase_or_all_required_labels():
    candidate = example_candidate()
    candidate["benefit"] = "Можно отметить семейную логистику, если школа подтверждена в карточке."

    assert_error("combination_not_grounded", candidate)


def test_invalid_result_text_empty_and_does_not_echo_candidate():
    candidate = example_candidate()
    candidate["intro"] = "MCP payload"
    result = result_for(candidate)

    assert result["valid"] is False
    assert result["text"] == ""
    assert "MCP payload" not in json.dumps(result, ensure_ascii=False)


def test_duplicate_sections_rejected():
    candidate = example_candidate()
    candidate["caveat"] = candidate["intro"]

    assert_error("duplicate_text", candidate)


def test_candidate_schema_strict_extra_keys_rejected():
    candidate = example_candidate()
    candidate["extra"] = "nope"

    assert_error("candidate_schema", candidate)


def test_cli_validate_and_print_model_input_modes():
    valid = subprocess.run(
        [sys.executable, str(REGISTRY / "answer_composer_simulator.py"), "--input", str(REGISTRY / "example_answer_composer_input.json"), "--candidate", str(REGISTRY / "example_answer_composer_candidate.json")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert valid.returncode == 0, valid.stdout + valid.stderr
    assert json.loads(valid.stdout)["valid"] is True

    package = subprocess.run(
        [sys.executable, str(REGISTRY / "answer_composer_simulator.py"), "--input", str(REGISTRY / "example_answer_composer_input.json"), "--print-model-input"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert package.returncode == 0, package.stdout + package.stderr
    assert "diagnostics" not in json.dumps(json.loads(package.stdout)["input"], ensure_ascii=False)


def test_source_has_no_runtime_v2_network_imports_or_calls():
    source = (REGISTRY / "answer_composer_simulator.py").read_text(encoding="utf-8")

    denied = ["nmbot_v2", "requests", "urllib", "httpx", "socket", "subprocess", "importlib"]
    assert not any(token in source for token in denied)
