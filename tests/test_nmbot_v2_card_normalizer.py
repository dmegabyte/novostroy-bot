from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nmbot_v2.card_normalizer import normalize_card, normalize_search_result
from nmbot_v2.contracts import ExecutionResult, SemanticPlan, Stage, StateDelta
from nmbot_v2.fact_context import present_fact_names, split_requested_facts
from nmbot_v2.response import build_response_plan, render_response
from nmbot_v2.state import ConversationState


ROOT = Path(__file__).resolve().parents[1]
QUALITY_GATE = ROOT / "scripts" / "nmbot_v2_quality_gate.py"


def _load_quality_gate():
    spec = importlib.util.spec_from_file_location("nmbot_v2_quality_gate_for_normalizer_test", QUALITY_GATE)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _live_family_financing_overlay_shape() -> dict:
    return {
        "facts": [
            {
                "id": 1,
                "name": "Семейный бизнес",
                "location": ["Новая Москва", "Коммунарка"],
                "district": "newmsk",
                "rooms": {"available": ["2-комнатные", {"rooms": [3, "студии"]}]},
                "min_price": 12_800_000,
                "delivered": 1,
                "finishing": 1,
                "new_building_class": "bussiness",
                "school": True,
                "kindergarten": True,
                "mortgage_calc": {"min_percent": 6.0, "min_fee": 20},
                "counter_novos": {"count_ads": 14},
                "egrn_top_novos": {"sales": 7},
            },
            {
                "id": 2,
                "name": "Белый квартал",
                "location": "Бунинские Луга",
                "district": "newmsk",
                "rooms": "2, 3",
                "min_price": 13_900_000,
                "status": "ready",
                "finishing": "white box",
                "new_building_class": "business",
                "park_near": True,
                "yard_without_cars": True,
                "payment_by_installments": {"month": 24},
            },
            {
                "id": 3,
                "name": "Новые школы",
                "location": "Коммунарка",
                "district": "newmsk",
                "rooms": [2],
                "min_price": 14_500_000,
                "state": "сдано",
                "new_building_class": "unknown_machine_enum",
                "school": True,
                "children_ground": True,
                "discount": "скидка 3%",
            },
        ],
        "near": [],
        "missing": [
            "нет подтверждения school/kindergarten",
            {"field": "mortgage_calc", "reason_code": "provider_missing"},
            "raw_internal_code_x42",
        ],
        "params": {"rooms": [2], "finance_preference": "mortgage_details"},
        "diagnostics": {},
    }


def test_card_normalizer_canonicalizes_live_family_financing_overlay_shape() -> None:
    result = normalize_search_result(_live_family_financing_overlay_shape())
    first, second, third = result.facts

    assert first.ready == "сдан"
    assert first.finishing is None
    assert first.property_class == "бизнес-класс"
    assert first.ads_count == 14
    assert first.sales_count == 7
    assert "2" in first.room_formats and "3" in first.room_formats and "студии" in first.room_formats
    assert second.finishing == "предчистовая отделка"
    assert third.property_class is None
    assert result.missing == ("family_infrastructure", "finance", "details")


def test_numeric_mcp_infrastructure_flags_are_normalized_without_treating_zero_as_true() -> None:
    card = normalize_card({
        "name": "Числовые флаги",
        "school": 1,
        "kindergarten": 1.0,
        "park_near": 1,
        "water_near": 0,
        "yard_without_cars": False,
    })

    assert card.infrastructure == ("школа", "детский сад", "парк")


def test_nested_family_infrastructure_flags_are_normalized_as_evidence() -> None:
    card = normalize_card({
        "name": "Семейный ЖК",
        "family_infrastructure": {
            "school": True,
            "kindergarten": True,
            "yard_without_cars": True,
            "children_ground": True,
            "sports_ground": False,
        },
    })

    assert card.infrastructure == ("школа", "детский сад", "двор без машин", "детская площадка")


def test_nested_generic_infrastructure_mapping_preserves_life_evidence() -> None:
    card = normalize_card({
        "name": "ЖК с инфраструктурой",
        "infrastructure": {
            "schools": True,
            "kindergartens": True,
            "parks": ["лесопарк рядом"],
            "shops": True,
            "clinics": True,
        },
    })

    assert card.infrastructure == ("школа", "детский сад", "магазины", "лесопарк рядом")
    assert card.daily_services == ("магазины",)
    assert card.healthcare == ("клиники",)


def test_rendered_live_shape_has_no_wire_values_raw_codes_or_repeated_benefits() -> None:
    search = normalize_search_result(_live_family_financing_overlay_shape())
    plan = build_response_plan(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="financing"),
        execution=ExecutionResult(ok=True, search=search),
        delta=StateDelta(),
        state=ConversationState(active_topic="family"),
    )
    text = render_response(plan)

    forbidden = ["bussiness", "unknown_machine_enum", "raw_internal_code_x42", "mortgage_calc", "school/kindergarten"]
    assert not any(item in text for item in forbidden)
    assert "бизнес-класс" in text
    assert "предчистовая отделка" in text
    assert "finishing" not in text.casefold()
    assert "с отделкой" not in text
    assert text.count("?") == 1
    benefit_lines = [line for line in text.splitlines() if line and not line[0].isdigit() and not line.startswith(("Да,", "В данных"))]
    assert len(benefit_lines) == len(set(benefit_lines))


def test_quality_gate_and_runtime_delegate_to_same_card_normalizer() -> None:
    harness = _load_quality_gate()
    raw_card = _live_family_financing_overlay_shape()["facts"][0]
    quality_card = harness._card_item(raw_card)

    from scripts.nmbot_runtime_adapter import _option_from_v2_fact

    runtime_card = _option_from_v2_fact(raw_card)
    direct_card = normalize_card(raw_card)

    assert quality_card["property_class"] == direct_card.property_class == runtime_card.property_class == "бизнес-класс"
    assert quality_card.get("finishing") is None
    assert runtime_card.finishing is None
    assert quality_card["ads_count"] == direct_card.ads_count == runtime_card.ads_count == 14


def test_unknown_machine_enums_and_prose_do_not_leak_to_renderer() -> None:
    search = normalize_search_result(
        {
            "facts": [{"name": "Чистый ЖК", "new_building_class": "strange_enum", "finishing": "raw_finish_code", "min_price": 9_000_000}],
            "near": [],
            "missing": ["модель сказала: нет красивого текста по секретному raw_finish_code"],
            "params": {},
        }
    )
    text = render_response(
        build_response_plan(
            stage=Stage.FIRST_LIST,
            plan=SemanticPlan(operation="search", intent="life"),
            execution=ExecutionResult(ok=True, search=search),
            delta=StateDelta(),
            state=ConversationState(),
        )
    )

    assert "strange_enum" not in text
    assert "raw_finish_code" not in text
    assert "модель сказала" not in text


def test_nonpositive_price_and_room_like_area_are_omitted() -> None:
    card = normalize_card({"name": "Тест", "min_price": 0, "area": 2, "rooms": 2})
    assert card.price is None
    assert card.price_min is None
    assert card.area is None
    assert card.room_formats == ("2",)


def test_multiple_enriched_room_formats_suppress_conflicting_single_room_token() -> None:
    card = normalize_card({"name": "ЖК Форматы", "rooms": 3, "apartment_types": [{"rooms": 1}, {"rooms": 2}, {"rooms": 3}]})

    assert card.rooms is None
    assert card.room_formats == ("1", "2", "3")


def test_structured_min_price_wins_over_conflicting_text_range() -> None:
    card = normalize_card({"name": "Тест", "min_price": 12_400_000, "price_range": "от 36 до 50 млн рублей"})
    assert card.price_min == 12_400_000
    assert card.price is None


def test_numeric_area_is_canonical_m2() -> None:
    card = normalize_card({"name": "Тест", "area": 42.5})
    assert card.area == "42,5 м²"


def test_live_nested_metro_developer_and_numeric_ready_are_client_safe() -> None:
    card = normalize_card({
        "name": "Бусиновский парк",
        "square_min": 20,
        "square_max": 89.3,
        "ready": 2,
        "delivered": 1,
        "metro": [{"metro_name": "Ховрино", "on_foot": 20, "on_transport": 16, "by_car": 6}],
        "developer": [{"id": 8, "name": "ПИК"}],
    })
    assert card.ready == "сдан"
    assert card.area == "20–89,3 м²"
    assert card.metro == "Ховрино — 20 минут пешком"
    assert card.developer == "ПИК"
    rendered = str(card)
    assert "{" not in rendered and "[" not in rendered


def test_numeric_string_ready_wire_code_is_not_client_readiness() -> None:
    card = normalize_card({"name": "Бунинская набережная", "ready": "4"})
    assert card.ready is None


def test_ads_normalize_to_two_structured_lot_examples_with_fullprice() -> None:
    card = normalize_card({
        "name": "Томилинский бульвар",
        "ads": [
            {"id": 6375479, "rooms": "s", "area": 19, "floor": 6, "floors_total": 25, "fullprice": 8_133_900, "price": 1, "renovation": "с отделкой", "status": 2},
            {"id": 5976219, "rooms": "1", "area": 32.8, "floor": 17, "floors_total": 25, "fullprice": 10_318_880, "renovation": "с отделкой", "status": 2},
        ],
    })

    assert len(card.lot_examples) == 2
    first, second = card.lot_examples
    assert first.id == 6375479
    assert first.rooms == "студия"
    assert first.area_m2 == 19
    assert first.floor == 6
    assert first.floors_total == 25
    assert first.full_price == 8_133_900
    assert first.renovation == "с отделкой"
    assert second.rooms == "1"
    assert second.area_m2 == 32.8


def test_lot_examples_are_bounded_to_two_and_do_not_use_ambiguous_price() -> None:
    card = normalize_card({
        "name": "ЖК Лоты",
        "ads": [
            {"id": 1, "rooms": "1", "area": 30, "fullprice": 9_000_000},
            {"id": 2, "rooms": "2", "area": 40, "fullprice": 12_000_000},
            {"id": 3, "rooms": "3", "area": 70, "fullprice": 18_000_000},
            {"id": 4, "rooms": "1", "area": 31, "price": 1_000_000},
        ],
    })

    assert [lot.id for lot in card.lot_examples] == [1, 2]
    assert [lot.full_price for lot in card.lot_examples] == [9_000_000, 12_000_000]


def test_lot_example_does_not_claim_house_without_per_ad_house_id() -> None:
    card = normalize_card({
        "name": "Томилинский бульвар",
        "house": [{"id": 5, "name": "5-8", "finishing_list": "final"}],
        "ads": [{"id": 6375479, "rooms": "s", "area": 19, "floor": 6, "floors_total": 25, "fullprice": 8_133_900}],
    })

    assert card.lot_examples[0].house_id is None
    assert card.lot_examples[0].house_name is None


def test_lot_example_claims_house_only_when_per_ad_house_id_matches() -> None:
    card = normalize_card({
        "name": "ЖК Дом",
        "house": [{"id": 5, "name": "5-8"}],
        "ads": [{"id": 1, "rooms": "1", "area": 32, "fullprice": 10_000_000, "house_id": 5}],
    })

    assert card.lot_examples[0].house_id == 5
    assert card.lot_examples[0].house_name == "5-8"


def test_ecology_rating_flat_and_nested_aliases_are_canonical() -> None:
    assert normalize_card({"name": "A", "ecology_rating": 7}).ecology_rating == 7
    assert normalize_card({"name": "B", "location_2": {"ecology_rating": "хорошая"}}).ecology_rating == "хорошая"


def test_nested_houses_are_not_apartment_inventory() -> None:
    card = normalize_card({"name": "ЖК Домики", "apartments": {"houses": [{"id": 1}, {"id": 2}]}})

    assert card.apartment_inventory is None

    top_level = normalize_card({"name": "ЖК Домики", "apartment_inventory": {"houses": [{"id": 1}]}})
    assert top_level.apartment_inventory is None

    repr_value = normalize_card({"name": "ЖК Домики", "apartment_inventory": "{'houses': [{'id': 1}]}"})
    assert repr_value.apartment_inventory is None


def test_structured_apartment_inventory_uses_total_available() -> None:
    card = normalize_card({
        "name": "Мичуринский парк",
        "apartment_inventory": {
            "total_available": 283,
            "min_price_lot": 14_307_660,
            "max_price_lot": 15_691_350,
        },
    })

    assert card.apartment_inventory == 283
    assert "apartment_inventory" in present_fact_names(card)

    response_plan = build_response_plan(
        stage=Stage.SELECTED_OBJECT,
        plan=SemanticPlan(
            operation="select_option",
            selected_option_name="Мичуринский парк",
            requested_facts=("apartment_inventory",),
            facts_needed=("apartment_inventory",),
        ),
        execution=ExecutionResult(
            ok=True,
            selected=card,
            fresh_facts=("apartment_inventory",),
        ),
        delta=StateDelta(),
        state=ConversationState(
            visible_options=(card,),
            selected_option_name="Мичуринский парк",
        ),
    )
    assert "283 квартиры в наличии" in render_response(response_plan)


def test_apartment_inventory_lookup_pointer_is_not_evidence() -> None:
    card = normalize_card({
        "name": "Мичуринский парк",
        "apartment_inventory": "Данные доступны через поиск объявлений по ID ЖК.",
    })

    assert card.apartment_inventory is None
    assert "apartment_inventory" not in present_fact_names(card)
    assert split_requested_facts(
        ("apartment_inventory",),
        card,
        fresh_facts=("apartment_inventory",),
    ).missing == ("apartment_inventory",)


def test_mortgage_program_list_is_normalized_separately_from_discount() -> None:
    card = normalize_card({
        "name": "ЖК Финансовый",
        "discount": "скидка 3%",
        "mortgage_calc": [
            {"min_percent": 6.0, "min_fee": 20},
            {"min_percent": 6.2},
        ],
    })

    assert card.discount == "скидка 3%"
    assert card.mortgage_terms is not None
    assert "ипотека от 6.0%" in card.mortgage_terms
    assert "первоначальный взнос от 20%" in card.mortgage_terms
    assert "ипотека от 6.2%" in card.mortgage_terms
    assert "скидка" not in card.mortgage_terms


def test_dynamic_parking_price_rejects_missing_prose_and_accepts_numeric_evidence() -> None:
    observed = normalize_card({"name": "2-й Иртышский", "parking_price": "Данные о стоимости парковочных мест не предоставлены в структурированном виде."})
    english = normalize_card({"name": "2-й Иртышский", "parking": {"price": "Parking price not available"}})
    zero = normalize_card({"name": "2-й Иртышский", "parking_price": 0})
    prose = normalize_card({"name": "2-й Иртышский", "parking_price": "стоимость уточняется у застройщика"})

    assert observed.parking_price is None
    assert english.parking_price is None
    assert zero.parking_price is None
    assert prose.parking_price is None
    assert "parking_price" not in present_fact_names(observed)
    assert split_requested_facts(("parking_price",), observed, fresh_facts=("parking_price",)).missing == ("parking_price",)

    assert normalize_card({"name": "ЖК", "parking_price": 1_900_000}).parking_price == 1_900_000
    assert normalize_card({"name": "ЖК", "parking_price": "от 1,9 млн рублей"}).parking_price == "от 1,9 млн рублей"
    assert normalize_card({"name": "ЖК", "parking": {"min_price": "1 500 000 ₽"}}).parking_price == "1 500 000 ₽"


def test_dynamic_inventory_rejects_unavailable_and_malformed_but_keeps_safe_values() -> None:
    unavailable_ru = normalize_card({"name": "ЖК", "parking_inventory": "Данные о количестве машиномест не предоставлены"})
    unavailable_en = normalize_card({"name": "ЖК", "parking_inventory": "not available"})
    malformed = normalize_card({"name": "ЖК", "apartment_inventory": "{'houses': [{'id': 1}]}"})
    overlong = normalize_card({"name": "ЖК", "apartment_inventory": "есть " + "очень " * 40})

    assert unavailable_ru.parking_inventory is None
    assert unavailable_en.parking_inventory is None
    assert malformed.apartment_inventory is None
    assert overlong.apartment_inventory is None
    assert "parking_inventory" not in present_fact_names(unavailable_ru)
    assert "apartment_inventory" not in present_fact_names(malformed)

    assert normalize_card({"name": "ЖК", "parking_inventory": 12}).parking_inventory == 12
    assert normalize_card({"name": "ЖК", "parking": {"available": "есть свободные машиноместа"}}).parking_inventory == "есть свободные машиноместа"
    assert normalize_card({"name": "ЖК", "apartment_inventory": 5}).apartment_inventory == 5
    assert normalize_card({"name": "ЖК", "apartment_inventory": "есть варианты"}).apartment_inventory == "есть варианты"


def test_parking_presence_rejects_missing_prose() -> None:
    no_parking = normalize_card({"name": "ЖК", "parking": "паркинг не предусмотрен"})
    unavailable = normalize_card({"name": "ЖК", "garage": "parking not available"})
    present = normalize_card({"name": "ЖК", "parking": "подземный паркинг"})

    assert no_parking.parking is None
    assert unavailable.parking is None
    assert "parking" not in present_fact_names(no_parking)
    assert present.parking == "подземный паркинг"
    assert "parking" in present_fact_names(present)
