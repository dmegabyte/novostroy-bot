from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "field_sales_registry" / "v1"
SPEC = importlib.util.spec_from_file_location("structured_finance_adapter", REGISTRY / "structured_finance_adapter.py")
adapter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(adapter)


def valid_payload(**overrides):
    payload = {
        "schema_version": 1,
        "object_name": "Синтетический финансовый ЖК",
        "fresh_mcp": True,
        "facts": {
            "mortgage_rate": {"value": 6.0, "source_field": "mortgage_calc.min_percent"},
            "down_payment": {"value": 20.0, "source_field": "mortgage_calc.min_fee"},
            "installment_months": {"value": 18, "source_field": "payment_by_installments.month"},
        },
    }
    payload.update(overrides)
    return payload


def omitted(result, field_id):
    return [item for item in result["diagnostics"]["omitted_field_ids"] if item["field_id"] == field_id]


def field_ids(wrapper):
    return [field["field_id"] for field in wrapper["brief"]["fields"]]


def test_valid_three_field_envelope_produces_numeric_canonical_facts():
    result = adapter.adapt_structured_finance(valid_payload())

    assert result["facts"] == {"mortgage_rate": 6.0, "down_payment": 20.0, "installment_months": 18}
    assert result["diagnostics"]["accepted_field_ids"] == ["mortgage_rate", "down_payment", "installment_months"]


def test_exact_source_paths_are_field_specific_and_docs_year_percent_only():
    payload = valid_payload(
        facts={
            "mortgage_rate": {"value": 7.1, "source_field": "mortgage.year_percent"},
            "down_payment": {"value": 10.0, "source_field": "mortgage.min_fee"},
            "installment_months": {"value": 12, "source_field": "payment_by_installments.month"},
        }
    )
    accepted = adapter.adapt_structured_finance(payload)
    rejected = adapter.adapt_structured_finance(
        valid_payload(facts={"mortgage_rate": {"value": 7.1, "source_field": "mortgage.min_percent"}})
    )

    assert accepted["facts"] == {"mortgage_rate": 7.1, "down_payment": 10.0, "installment_months": 12}
    assert "mortgage_rate" not in rejected["facts"]
    assert omitted(rejected, "mortgage_rate") == [{"field_id": "mortgage_rate", "reason": "invalid_source"}]


def test_mortgage_credit_month_never_becomes_installment_months():
    result = adapter.adapt_structured_finance(
        valid_payload(facts={"installment_months": {"value": 240, "source_field": "mortgage.credit_month"}})
    )

    assert result["facts"] == {}
    assert omitted(result, "installment_months") == [{"field_id": "installment_months", "reason": "invalid_source"}]


def test_percentage_zero_accepted_negative_non_finite_bool_and_string_rejected():
    assert adapter.adapt_structured_finance(valid_payload(facts={"mortgage_rate": {"value": 0, "source_field": "mortgage_calc.min_percent"}}))["facts"] == {"mortgage_rate": 0}
    for bad in (-1, float("nan"), float("inf"), True, "6.0"):
        result = adapter.adapt_structured_finance(valid_payload(facts={"mortgage_rate": {"value": bad, "source_field": "mortgage_calc.min_percent"}}))
        assert result["facts"] == {}
        assert omitted(result, "mortgage_rate") == [{"field_id": "mortgage_rate", "reason": "invalid_value", "source_field": "mortgage_calc.min_percent"}]


def test_months_positive_int_accepted_zero_negative_float_bool_and_string_rejected():
    assert adapter.adapt_structured_finance(valid_payload(facts={"installment_months": {"value": 1, "source_field": "payment_by_installments.month"}}))["facts"] == {"installment_months": 1}
    for bad in (0, -1, 1.5, True, "18"):
        result = adapter.adapt_structured_finance(valid_payload(facts={"installment_months": {"value": bad, "source_field": "payment_by_installments.month"}}))
        assert result["facts"] == {}
        assert omitted(result, "installment_months") == [{"field_id": "installment_months", "reason": "invalid_value", "source_field": "payment_by_installments.month"}]


def test_stale_finance_emits_no_facts_and_diagnostics_do_not_leak_values():
    result = adapter.adapt_structured_finance(valid_payload(fresh_mcp=False))
    dumped = json.dumps(result["diagnostics"], ensure_ascii=False, allow_nan=False)

    assert result["facts"] == {}
    assert {item["reason"] for item in result["diagnostics"]["omitted_field_ids"]} == {"stale"}
    assert "6.0" not in dumped and "20.0" not in dumped and "18" not in dumped


def test_extra_raw_prose_and_mortgage_terms_keys_fail_closed_without_leakage():
    payload = valid_payload(raw="секрет", facts={"mortgage_terms": {"value": "ипотека от 3%", "source_field": "mortgage_terms"}})
    result = adapter.adapt_structured_finance(payload)
    dumped = json.dumps(result, ensure_ascii=False, allow_nan=False)

    assert result["facts"] == {}
    assert {item["reason"] for item in result["diagnostics"]["omitted_field_ids"]} == {"invalid_source"}
    assert "секрет" not in dumped and "ипотека" not in dumped and "3%" not in dumped and "mortgage_terms" not in dumped


def test_combined_exact_object_scope_and_both_freshness_flags_merge_and_budget_brief_selects_finance():
    wrapper = adapter.build_brief_with_structured_finance(
        {"name": "Синтетический финансовый ЖК", "price_min": 12_000_000},
        valid_payload(),
        "budget",
        fresh_mcp=True,
        requested_fields=("mortgage_rate", "down_payment", "installment_months", "apartment_price"),
        max_fields=5,
    )

    assert wrapper["merge_diagnostics"] == {"scope_match": True, "reason": "merged"}
    assert field_ids(wrapper)[:4] == ["mortgage_rate", "down_payment", "installment_months", "apartment_price"]
    values = {field["field_id"]: field["value"] for field in wrapper["brief"]["fields"]}
    assert values == {"mortgage_rate": 6.0, "down_payment": 20.0, "installment_months": 18, "apartment_price": 12_000_000}


def test_object_mismatch_blocks_all_finance_facts():
    wrapper = adapter.build_brief_with_structured_finance(
        {"name": "Другой ЖК", "price_min": 12_000_000},
        valid_payload(),
        "budget",
        fresh_mcp=True,
        requested_fields=("mortgage_rate", "down_payment", "installment_months", "apartment_price"),
    )

    assert wrapper["merge_diagnostics"] == {"scope_match": False, "reason": "object_scope_mismatch"}
    assert field_ids(wrapper) == ["apartment_price"]


def test_wrapper_fresh_mcp_false_blocks_finance_even_when_payload_is_fresh():
    wrapper = adapter.build_brief_with_structured_finance(
        {"name": "Синтетический финансовый ЖК", "price_min": 12_000_000},
        valid_payload(),
        "budget",
        fresh_mcp=False,
        requested_fields=("mortgage_rate", "down_payment", "installment_months", "apartment_price"),
    )

    assert wrapper["merge_diagnostics"] == {"scope_match": True, "reason": "stale_wrapper"}
    assert field_ids(wrapper) == []


def test_option_card_apartment_price_coexists_with_percentage_down_payment_not_cash_amount():
    wrapper = adapter.build_brief_with_structured_finance(
        {"name": "Синтетический финансовый ЖК", "price_min": 9_000_000},
        valid_payload(facts={"down_payment": {"value": 30, "source_field": "mortgage_calc.min_fee"}}),
        "budget",
        fresh_mcp=True,
        requested_fields=("down_payment", "apartment_price"),
    )

    values = {field["field_id"]: field["value"] for field in wrapper["brief"]["fields"]}
    assert values == {"down_payment": 30, "apartment_price": 9_000_000}


def test_adaptation_output_example_exactly_regenerates():
    payload = json.loads((REGISTRY / "example_structured_finance_input.json").read_text(encoding="utf-8"))
    expected = json.loads((REGISTRY / "example_structured_finance_output.json").read_text(encoding="utf-8"))

    assert adapter.adapt_structured_finance(payload) == expected


def test_schema_and_registry_validator_green():
    schema = json.loads((REGISTRY / "structured_finance_schema.json").read_text(encoding="utf-8"))
    assert schema["$defs"]["mortgage_rate_fact"]["properties"]["source_field"]["enum"] == [
        "mortgage_calc.min_percent",
        "mortgage.year_percent",
    ]
    assert schema["$defs"]["down_payment_fact"]["properties"]["source_field"]["enum"] == [
        "mortgage_calc.min_fee",
        "mortgage.min_fee",
    ]

    completed = subprocess.run(
        [sys.executable, str(REGISTRY / "validate_registry.py")],
        cwd=REGISTRY,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "OK" in completed.stdout


def test_source_has_no_runtime_imports_or_mortgage_terms_parsing():
    source = (REGISTRY / "structured_finance_adapter.py").read_text(encoding="utf-8")

    assert "nmbot_v2" not in source
    assert "card_normalizer" not in source
    assert "contracts" not in source
    assert "mortgage_terms" not in source
    assert "requests" not in source
    assert "http" not in source.casefold()
