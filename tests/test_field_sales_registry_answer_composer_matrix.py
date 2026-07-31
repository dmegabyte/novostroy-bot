from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "field_sales_registry" / "v1"
REPORT_MD = ROOT / "reports" / "FIELD_SALES_REGISTRY_ANSWER_COMPOSER_MATRIX_20260721.md"
SPEC = importlib.util.spec_from_file_location("answer_composer_matrix", REGISTRY / "run_answer_composer_matrix.py")
matrix_runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(matrix_runner)

SIM_SPEC = importlib.util.spec_from_file_location("answer_composer_simulator", REGISTRY / "answer_composer_simulator.py")
composer = importlib.util.module_from_spec(SIM_SPEC)
assert SIM_SPEC.loader is not None
SIM_SPEC.loader.exec_module(composer)

CONTACT_RE = re.compile(r"(?:https?://|www\.|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|(?:\+?\d[\s().-]*){10,})", re.IGNORECASE)
INTERNAL_RE = re.compile(r"\b(?:MCP|JSON|payload|diagnostics|field_id|source_field|evidence|canonical|model|schema|trace|OptionCard|enum|карточк\w*|данн\w*|контекст\w*|подтвержд[её]н\w*)\b", re.IGNORECASE)


def matrix():
    return json.loads((REGISTRY / "answer_composer_matrix.json").read_text(encoding="utf-8"))


def report():
    return json.loads((REGISTRY / "answer_composer_matrix_report.json").read_text(encoding="utf-8"))


def case(case_id: str):
    return next(item for item in matrix() if item["case_id"] == case_id)


def text(case_id: str) -> str:
    return next(item for item in report() if item["case_id"] == case_id)["text"]


def test_matrix_has_exactly_five_expected_cases():
    assert [item["case_id"] for item in matrix()] == [
        "answer_composer_family",
        "answer_composer_financing",
        "answer_composer_parking",
        "answer_composer_investment",
        "answer_composer_lot",
    ]


def test_all_cases_simulate_valid_and_report_regenerates_exactly():
    generated = matrix_runner.generate_report(matrix())

    assert generated == report()
    assert all(item["valid"] is True for item in generated)
    assert all(item["manual_review_required"] is True for item in generated)


def test_expected_ids_and_combinations_are_exact():
    for item in matrix():
        result = composer.simulate(item["input"], item["candidate"])
        assert result["metadata"]["used_field_ids"] == item["expected_used_field_ids"]
        assert result["metadata"]["used_combination_ids"] == item["expected_used_combination_ids"]


def test_finance_has_structured_fields_and_no_approval_or_guarantee_promise():
    item = case("answer_composer_financing")
    assert item["expected_used_field_ids"][:3] == ["mortgage_rate", "down_payment", "installment_months"]
    assert item["input"]["brief"]["fields"][0]["value"] == 6.0
    assert item["input"]["brief"]["fields"][1]["value"] == 20.0
    assert item["input"]["brief"]["fields"][2]["value"] == 18
    assert item["input"]["brief"]["fields"][3]["value"] == 12000000
    assert not re.search(r"одобрено|гарант", text("answer_composer_financing"), re.IGNORECASE)


def test_parking_has_no_booking_or_guaranteed_availability_claims():
    parking = text("answer_composer_parking")

    assert "1 500 000" in parking
    assert "7 машиномест" in parking
    assert not re.search(r"брон|гарант|точно\s+есть|место\s+сохран", parking, re.IGNORECASE)


def test_investment_has_literal_counters_and_no_forbidden_inferences():
    investment = text("answer_composer_investment")

    assert "0 сделок" in investment
    assert "7 объявлений" in investment
    assert not re.search(r"спрос|ликвидност|доходност|рост", investment, re.IGNORECASE)


def test_lot_has_six_field_ids_and_no_booking_availability_or_quality_claim():
    item = case("answer_composer_lot")
    lot = text("answer_composer_lot")

    assert item["expected_used_field_ids"] == ["lot_full_price", "lot_area", "lot_floor", "lot_rooms", "lot_renovation", "lot_status"]
    for fragment in ("10 100 000", "35,2", "3 из 12", "студия", "предчистовая отделка", "в продаже"):
        assert fragment in lot
    assert not re.search(r"брон|гарант|качество|точно\s+есть|сохран", lot, re.IGNORECASE)


def test_all_text_has_one_final_question_and_manual_review_true():
    for item in report():
        assert item["manual_review_required"] is True
        assert item["text"].count("?") == 1
        source = case(item["case_id"])
        assert item["text"].rstrip().endswith(source["candidate"]["final_question"])


def test_report_has_no_contact_url_raw_keys_or_internal_terms_in_visible_text():
    dumped = json.dumps(report(), ensure_ascii=False)
    assert not CONTACT_RE.search(dumped)
    assert "raw" not in dumped.casefold()
    for item in report():
        assert not INTERNAL_RE.search(item["text"])


def test_markdown_report_is_deterministic_and_sanitized():
    md = REPORT_MD.read_text(encoding="utf-8")

    assert md == matrix_runner.markdown_report(report())
    assert "answer_composer_matrix.json" in md
    assert "manual_review_required=true" in md
    assert not CONTACT_RE.search(md)
    assert "raw" not in md.casefold()
