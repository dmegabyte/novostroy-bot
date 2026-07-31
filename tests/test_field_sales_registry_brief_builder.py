from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "field_sales_registry" / "v1"
SPEC = importlib.util.spec_from_file_location("brief_builder", REGISTRY / "brief_builder.py")
brief_builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(brief_builder)


def ids(brief):
    return [field["field_id"] for field in brief["fields"]]


def omitted(brief, field_id):
    return [item for item in brief["diagnostics"]["omitted_field_ids"] if item["field_id"] == field_id]


def test_family_picks_school_kindergarten_and_combination_when_max_allows():
    brief = brief_builder.build_compact_brief(
        {"school": True, "kindergarten": True, "children_ground": True},
        "family",
        max_fields=5,
        object_name="Синтетический ЖК",
    )

    assert ids(brief)[:2] == ["kindergarten", "school"]
    assert "school_plus_kindergarten" in [combo["id"] for combo in brief["combinations"]]
    assert brief["object_name"] == "Синтетический ЖК"


def test_stale_dynamic_apartment_price_omitted_without_value_leakage_in_diagnostics():
    brief = brief_builder.build_compact_brief({"apartment_price": 12345678}, "budget", fresh_mcp=False)

    assert ids(brief) == []
    assert omitted(brief, "apartment_price") == [{"field_id": "apartment_price", "reason": "stale_dynamic"}]
    assert "12345678" not in json.dumps(brief["diagnostics"], ensure_ascii=False)


def test_fresh_budget_includes_apartment_price():
    brief = brief_builder.build_compact_brief({"apartment_price": 12345678}, "budget", fresh_mcp=True)

    assert ids(brief) == ["apartment_price"]
    assert brief["fields"][0]["value"] == 12345678
    assert "allowed_benefit" in brief["fields"][0]


def test_investment_counters_stay_literal_and_registry_forbidden_claims_preserved():
    brief = brief_builder.build_compact_brief({"sales_count": 0, "ads_count": 4}, "investment", fresh_mcp=True)

    assert ids(brief) == ["ads_count", "sales_count"]
    sales = next(field for field in brief["fields"] if field["field_id"] == "sales_count")
    assert sales["value"] == 0
    assert any("ликвидность" in claim for claim in sales["forbidden_claims"])


def test_unknown_keys_ignored_name_only_diagnostics():
    brief = brief_builder.build_compact_brief(
        {
            "school": True,
            "seller_phone": "+7 secret",
            "+7 raw value disguised as key": "must not enter diagnostics",
        },
        "family",
    )

    assert ids(brief) == ["school"]
    assert brief["diagnostics"]["unknown_field_ids"] == ["seller_phone"]
    assert "+7" not in json.dumps(brief, ensure_ascii=False)


def test_non_finite_number_is_rejected():
    brief = brief_builder.build_compact_brief({"ads_count": float("nan")}, "investment", fresh_mcp=True)

    assert ids(brief) == []
    assert omitted(brief, "ads_count") == [{"field_id": "ads_count", "reason": "unsafe_value"}]


def test_boolean_does_not_cross_text_money_or_list_value_types():
    brief = brief_builder.build_compact_brief(
        {"developer": True, "apartment_price": True, "room_formats": [True]},
        "general",
        fresh_mcp=True,
        requested_fields=("developer", "apartment_price", "room_formats"),
    )

    assert ids(brief) == []


def test_raw_numeric_lot_status_rejected():
    brief = brief_builder.build_compact_brief({"lot_status": 1}, "general", fresh_mcp=True)

    assert ids(brief) == []
    assert omitted(brief, "lot_status") == [{"field_id": "lot_status", "reason": "unsafe_value"}]


def test_numeric_string_lot_status_rejected():
    brief = brief_builder.build_compact_brief({"lot_status": "01"}, "general", fresh_mcp=True)

    assert ids(brief) == []
    assert omitted(brief, "lot_status") == [{"field_id": "lot_status", "reason": "unsafe_value"}]


def test_inventory_accepts_count_and_bounded_text_only_when_fresh():
    stale = brief_builder.build_compact_brief({"apartment_inventory": "есть квартиры"}, "budget", fresh_mcp=False)
    fresh = brief_builder.build_compact_brief(
        {"apartment_inventory": "есть квартиры", "parking_inventory": 3},
        "parking",
        fresh_mcp=True,
        requested_fields=("apartment_inventory", "parking_inventory"),
        max_fields=5,
    )
    nested = brief_builder.build_compact_brief({"parking_inventory": [3]}, "parking", fresh_mcp=True)

    assert ids(stale) == []
    assert omitted(stale, "apartment_inventory") == [{"field_id": "apartment_inventory", "reason": "stale_dynamic"}]
    assert {field["field_id"]: field["value"] for field in fresh["fields"]} == {
        "apartment_inventory": "есть квартиры",
        "parking_inventory": 3,
    }
    assert omitted(nested, "parking_inventory") == [{"field_id": "parking_inventory", "reason": "unsafe_value"}]


def test_boolean_false_feature_absent_numeric_zero_counter_present():
    brief = brief_builder.build_compact_brief({"school": False, "ads_count": 0}, "investment", fresh_mcp=True)

    assert ids(brief) == ["ads_count"]
    assert omitted(brief, "school") == [{"field_id": "school", "reason": "missing"}]
    assert brief["fields"][0]["value"] == 0


def test_nested_mapping_overlong_and_list_values_bounded_or_rejected_predictably():
    long = "x" * 300
    brief = brief_builder.build_compact_brief(
        {"developer": long, "room_formats": ["a", {"bad": "nested"}, "b", "c", "d", "e", "f", "g", "h", "i"], "area": {"min": 30}},
        "general",
        fresh_mcp=True,
        requested_fields=("developer", "room_formats", "area"),
        max_fields=5,
    )

    dev = next(field for field in brief["fields"] if field["field_id"] == "developer")
    rooms = next(field for field in brief["fields"] if field["field_id"] == "room_formats")
    assert len(dev["value"]) == 240
    assert rooms["value"] == ["a", "b", "c", "d", "e", "f", "g"]
    assert omitted(brief, "area") == [{"field_id": "area", "reason": "unsafe_value"}]


def test_requested_field_can_be_included_without_matching_scenario_but_gets_no_other_benefit():
    brief = brief_builder.build_compact_brief(
        {"parking": True, "school": True},
        "family",
        requested_fields=("parking",),
        max_fields=2,
    )

    assert ids(brief) == ["parking", "school"]
    parking = brief["fields"][0]
    assert parking["field_id"] == "parking"
    assert "allowed_benefit" not in parking


def test_max_fields_stable_ordering_and_combinations_require_selected_cards():
    brief = brief_builder.build_compact_brief({"school": True, "kindergarten": True, "children_ground": True}, "family", max_fields=1)

    assert ids(brief) == ["kindergarten"]
    assert brief["combinations"] == []
    assert omitted(brief, "school") == [{"field_id": "school", "reason": "limit"}]


def test_example_brief_exactly_equals_regenerated_output():
    example_input = json.loads((REGISTRY / "example_input.json").read_text(encoding="utf-8"))
    expected = json.loads((REGISTRY / "example_brief.json").read_text(encoding="utf-8"))

    actual = brief_builder.build_compact_brief(
        example_input,
        "family",
        fresh_mcp=True,
        requested_fields=("school", "kindergarten"),
        max_fields=5,
        object_name="Синтетический ЖК",
    )
    assert actual == expected


def test_existing_registry_validator_remains_green():
    completed = subprocess.run(
        [sys.executable, str(REGISTRY / "validate_registry.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "OK" in completed.stdout
