from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "field_sales_registry" / "v1"
REPORT_MD = ROOT / "reports" / "FIELD_SALES_REGISTRY_COVERAGE_20260721.md"
SPEC = importlib.util.spec_from_file_location("coverage_audit", REGISTRY / "coverage_audit.py")
coverage_audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(coverage_audit)


DENIED_KEY_RE = re.compile(r"(?:phone|email|contact|seller|callback|prompt|model|payload|user_text)", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?\d[\s().-]*){10,}")


def walk(value):
    yield value
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def report():
    return coverage_audit.generate_report()


def test_corpus_has_no_denied_keys_or_pii_like_contact_values():
    corpus = json.loads((REGISTRY / "coverage_corpus.json").read_text(encoding="utf-8"))

    coverage_audit.assert_no_denied_corpus_content(corpus)
    for item in walk(corpus):
        if isinstance(item, str):
            assert not EMAIL_RE.search(item)
            assert not PHONE_RE.search(item)
        else:
            assert not DENIED_KEY_RE.search(str(item))


def test_registry_total_35_and_expected_gap_exactly_4():
    generated = report()

    assert generated["registry_field_count"] == 35
    assert generated["expected_unreachable"] == {
        "down_payment": "structured_finance_missing",
        "house_link": "provenance_only",
        "installment_months": "structured_finance_missing",
        "mortgage_rate": "structured_finance_missing",
    }
    assert len(generated["expected_unreachable"]) == 4


def test_observed_union_exactly_31_reachable_ids():
    generated = report()

    assert len(generated["reachable_contract_field_ids"]) == 31
    assert generated["observed_corpus_field_ids"] == generated["reachable_contract_field_ids"]


def test_no_unexpected_uncovered_or_emitted_ids():
    generated = report()

    assert generated["unexpected_uncovered_field_ids"] == []
    assert generated["unexpected_field_ids"] == []


def test_coverage_percentages_are_stable():
    generated = report()

    assert generated["reachable_coverage_percent"] == 100.0
    assert generated["registry_reachability_percent"] == 88.6


def test_domain_coverage_consistent_with_registry_partition():
    generated = report()
    domain = generated["domain_coverage"]

    assert sum(item["registry_count"] for item in domain.values()) == 35
    assert sum(item["reachable_count"] for item in domain.values()) == 31
    assert sum(item["observed_count"] for item in domain.values()) == 31
    assert domain["financing"]["expected_unreachable_field_ids"] == [
        "down_payment",
        "installment_months",
        "mortgage_rate",
    ]
    assert domain["lots"]["expected_unreachable_field_ids"] == ["house_link"]


def test_finance_case_reports_three_unmapped_names_and_no_raw_mortgage_text():
    generated = report()
    finance = next(case for case in generated["cases"] if case["case_id"] == "finance_unstructured")
    dumped = json.dumps(generated, ensure_ascii=False)

    assert finance["adapter_diagnostics"]["unmapped_field_ids"] == [
        "down_payment",
        "installment_months",
        "mortgage_rate",
    ]
    assert "ипотечные условия" not in dumped
    assert "mortgage_terms" not in dumped


def test_lots_zero_and_one_are_coherent_and_house_link_is_diagnostic_only():
    generated = report()
    first = next(case for case in generated["cases"] if case["case_id"] == "lot_first_with_house_diagnostic")
    second = next(case for case in generated["cases"] if case["case_id"] == "lot_second_without_house_diagnostic")

    expected = ["lot_full_price", "lot_area", "lot_floor", "lot_rooms", "lot_renovation", "lot_status"]
    assert first["selected_brief_field_ids"] == expected
    assert second["selected_brief_field_ids"] == expected
    assert first["lot_index"] == 0
    assert second["lot_index"] == 1
    assert first["adapter_diagnostics"]["house_link_available"] is True
    assert second["adapter_diagnostics"]["house_link_available"] is False
    assert "house_link" not in first["adapted_field_ids"]
    assert "house_link" not in second["adapted_field_ids"]


def test_family_yard_safe_combinations_selected_within_max_fields():
    generated = report()
    family = next(case for case in generated["cases"] if case["case_id"] == "family_yard_complete")

    assert set(family["combination_ids"]) == {
        "park_plus_water",
        "school_plus_kindergarten",
        "yard_without_cars_plus_security",
    }
    assert len(family["selected_brief_field_ids"]) == 9
    school = next(item for item in family["brief_descriptors"] if item["field_id"] == "school")
    assert school["label"] == "школа рядом"
    assert "семейную логистику" in school["allowed_benefit"]
    assert all(item["safe_phrasing"] for item in family["safe_combinations"])


def test_report_json_exactly_equals_regeneration():
    stored = json.loads((REGISTRY / "coverage_report.json").read_text(encoding="utf-8"))

    assert stored == report()


def test_markdown_contains_summary_gaps_source_refs_and_no_raw_payload_or_contacts():
    text = REPORT_MD.read_text(encoding="utf-8")

    assert "Registry fields: 35" in text
    assert "Reachable coverage: 100.0%" in text
    assert "`mortgage_rate`" in text
    assert "`house_link`" in text
    assert "field_sales_registry/v1/coverage_audit.py" in text
    assert "Brief descriptor `school` — школа рядом" in text
    assert "Safe phrasing `school_plus_kindergarten`" in text
    assert "seller_phone" not in text
    assert "raw payload" not in text.casefold()
    assert not EMAIL_RE.search(text)
    assert not PHONE_RE.search(text)


def test_markdown_is_deterministic_from_regeneration():
    assert REPORT_MD.read_text(encoding="utf-8") == coverage_audit.markdown_report(report())
