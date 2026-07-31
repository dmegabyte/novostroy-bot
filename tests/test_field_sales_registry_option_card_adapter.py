from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "field_sales_registry" / "v1"
SPEC = importlib.util.spec_from_file_location("option_card_adapter", REGISTRY / "option_card_adapter.py")
adapter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(adapter)


def fact_ids(wrapper):
    return [field["field_id"] for field in wrapper["brief"]["fields"]]


def test_mapping_input_direct_fields_and_exact_infrastructure_split():
    result = adapter.adapt_option_card(
        {
            "name": "ЖК Тестовый",
            "developer": "Девелопер",
            "property_class": "комфорт",
            "location": "Рядом с метро",
            "price_min": 12_000_000,
            "room_formats": ["1", "2"],
            "infrastructure": [
                "школа",
                "детский сад",
                "лесопарк",
                "река",
                "двор без машин",
                "детская площадка",
                "спортивная площадка",
                "охрана",
            ],
            "sales_count": 0,
            "ads_count": 0,
        }
    )

    assert result["object_name"] == "ЖК Тестовый"
    assert result["facts"]["developer"] == "Девелопер"
    assert result["facts"]["apartment_price"] == 12_000_000
    assert result["facts"]["room_formats"] == ["1", "2"]
    for field_id in (
        "school",
        "kindergarten",
        "park_near",
        "water_near",
        "yard_without_cars",
        "children_ground",
        "sports_ground",
        "security",
    ):
        assert result["facts"][field_id] is True
    assert result["facts"]["sales_count"] == 0
    assert result["facts"]["ads_count"] == 0


def test_structural_object_works_and_adapter_source_has_no_runtime_contract_imports():
    sys.path.insert(0, str(ROOT))
    from nmbot_v2.contracts import OptionCard

    card = OptionCard(name="ЖК Объект", developer="Дев", price_min=10, infrastructure=("school",))
    result = adapter.adapt_option_card(card)
    source = (REGISTRY / "option_card_adapter.py").read_text(encoding="utf-8")

    assert result["facts"]["developer"] == "Дев"
    assert result["facts"]["school"] is True
    assert "nmbot_v2" not in source
    assert "__dict__" not in source
    assert "asdict" not in source


def test_finance_string_does_not_emit_structured_finance_or_raw_leakage():
    text = "ипотека от 3%, взнос от 20%, рассрочка до 12 месяцев"
    result = adapter.adapt_option_card({"name": "ЖК", "mortgage_terms": text})
    dumped = json.dumps(result, ensure_ascii=False)

    assert "mortgage_rate" not in result["facts"]
    assert "down_payment" not in result["facts"]
    assert "installment_months" not in result["facts"]
    assert result["diagnostics"]["unmapped_field_ids"] == ["mortgage_rate", "down_payment", "installment_months"]
    assert "3%" not in dumped
    assert "20%" not in dumped
    assert "12 месяцев" not in dumped


def test_lot_index_none_does_not_emit_lot_facts():
    result = adapter.adapt_option_card(
        {"name": "ЖК", "lot_examples": [{"full_price": 10, "area_m2": 20, "floor": 3, "rooms": "студия"}]}
    )

    assert result["diagnostics"]["lot_examples_available"] == 1
    assert result["diagnostics"]["lot_selection"] == "not_requested"
    assert all(not key.startswith("lot_") for key in result["facts"])


def test_explicit_lot_index_selects_one_coherent_lot_without_cross_mix():
    card = {
        "name": "ЖК",
        "lot_examples": [
            {"full_price": 10, "area_m2": 20, "floor": 3, "floors_total": 9, "rooms": "студия", "renovation": "white box"},
            {"full_price": 30, "area_m2": 40, "floor": 7, "floors_total": 17, "rooms": "2", "renovation": "без отделки"},
        ],
    }

    first = adapter.adapt_option_card(card, lot_index=0)
    second = adapter.adapt_option_card(card, lot_index=1)

    assert first["facts"]["lot_full_price"] == 10
    assert first["facts"]["lot_area"] == 20
    assert first["facts"]["lot_floor"] == "3 из 9"
    assert first["facts"]["lot_rooms"] == "студия"
    assert first["facts"]["lot_renovation"] == "white box"
    assert second["facts"]["lot_full_price"] == 30
    assert second["facts"]["lot_area"] == 40
    assert second["facts"]["lot_floor"] == "7 из 17"
    assert second["facts"]["lot_rooms"] == "2"
    assert second["facts"]["lot_renovation"] == "без отделки"


def test_out_of_range_negative_and_bool_index_fail_closed_predictably():
    card = {"name": "ЖК", "lot_examples": [{"full_price": 10}]}

    for bad_index in (1, -1, True):
        result = adapter.adapt_option_card(card, lot_index=bad_index)
        assert result["diagnostics"]["lot_selection"] == "out_of_range"
        assert result["lot_index"] is None
        assert "lot_full_price" not in result["facts"]
        assert {"field_id": "lot_full_price", "reason": "out_of_range"} in result["diagnostics"]["omitted_field_ids"]


def test_numeric_and_numeric_string_lot_status_omitted_human_status_allowed():
    numeric = adapter.adapt_option_card({"name": "ЖК", "lot_examples": [{"status": 1}]}, lot_index=0)
    numeric_string = adapter.adapt_option_card({"name": "ЖК", "lot_examples": [{"status": "01"}]}, lot_index=0)
    human = adapter.adapt_option_card({"name": "ЖК", "lot_examples": [{"status": "в продаже"}]}, lot_index=0)

    assert "lot_status" not in numeric["facts"]
    assert "lot_status" not in numeric_string["facts"]
    assert human["facts"]["lot_status"] == "в продаже"


def test_house_linkage_never_appears_in_facts_diagnostics_bool_only():
    result = adapter.adapt_option_card(
        {"name": "ЖК", "lot_examples": [{"full_price": 10, "house_id": 123, "house_name": "Корпус 1"}]},
        lot_index=0,
    )
    dumped_facts = json.dumps(result["facts"], ensure_ascii=False)

    assert "house_link" not in result["facts"]
    assert "house_id" not in dumped_facts
    assert "house_name" not in dumped_facts
    assert "Корпус" not in dumped_facts
    assert result["diagnostics"]["house_link_available"] is True


def test_inventory_integer_and_canonical_string_reach_brief_only_when_fresh():
    card = {"name": "ЖК", "apartment_inventory": "есть квартиры", "parking_inventory": 2}

    fresh = adapter.build_brief_from_option_card(
        card,
        "parking",
        fresh_mcp=True,
        requested_fields=("apartment_inventory", "parking_inventory"),
        max_fields=5,
    )
    stale = adapter.build_brief_from_option_card(
        card,
        "parking",
        fresh_mcp=False,
        requested_fields=("apartment_inventory", "parking_inventory"),
        max_fields=5,
    )

    assert {field["field_id"]: field["value"] for field in fresh["brief"]["fields"]} == {
        "apartment_inventory": "есть квартиры",
        "parking_inventory": 2,
    }
    assert fact_ids(stale) == []
    assert {item["field_id"] for item in stale["brief"]["diagnostics"]["omitted_field_ids"]} == {
        "apartment_inventory",
        "parking_inventory",
    }


def test_exact_infra_terms_do_not_infer_from_name_territory_or_generic_safety():
    result = adapter.adapt_option_card(
        {
            "name": "ЖК Лесной парк",
            "infrastructure": ["благоустроенная территория", "безопасный двор", "закрытая территория"],
        }
    )

    assert "park_near" not in result["facts"]
    assert "security" not in result["facts"]


def test_parking_token_does_not_infer_park():
    result = adapter.adapt_option_card({"name": "ЖК", "infrastructure": ["паркинг"]})

    assert result["facts"]["parking"] is True
    assert "park_near" not in result["facts"]


def test_boolean_and_non_positive_values_do_not_cross_typed_boundaries():
    result = adapter.adapt_option_card(
        {
            "name": "ЖК",
            "price_min": True,
            "area": False,
            "sales_count": True,
            "parking_price": -1,
            "lot_examples": [{"full_price": -10, "area_m2": 0}],
        },
        lot_index=0,
    )

    for field_id in ("apartment_price", "area", "sales_count", "parking_price", "lot_full_price", "lot_area"):
        assert field_id not in result["facts"]


def test_zero_sales_count_and_ads_count_preserved():
    result = adapter.adapt_option_card({"name": "ЖК", "sales_count": 0, "ads_count": 0})

    assert result["facts"]["sales_count"] == 0
    assert result["facts"]["ads_count"] == 0


def test_wrapper_result_uses_object_name_and_current_compact_brief_without_runtime_import():
    result = adapter.build_brief_from_option_card(
        {"name": "ЖК Обёртка", "developer": "Дев", "school": True, "infrastructure": ["школа"]},
        "family",
        requested_fields=("school", "developer"),
        max_fields=2,
    )
    source = (REGISTRY / "option_card_adapter.py").read_text(encoding="utf-8")

    assert set(result) == {"adaptation", "brief"}
    assert result["adaptation"]["object_name"] == "ЖК Обёртка"
    assert result["brief"]["object_name"] == "ЖК Обёртка"
    assert fact_ids(result) == ["school", "developer"]
    assert "nmbot_runtime" not in source
