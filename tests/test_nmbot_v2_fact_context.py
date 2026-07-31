from nmbot_v2.contracts import OptionCard
from nmbot_v2.fact_context import normalize_facts, present_fact_names, split_requested_facts
from nmbot_v2.constraints import normalize_constraints_delta


def test_fact_context_maps_card_fields_without_inventing_parking_price() -> None:
    card = OptionCard(name="Мичуринский парк", price_min=14_000_000, location="Москва", metro="Озёрная", ready="2027", finishing="с отделкой", infrastructure=("паркинг", "школа"))

    assert set(present_fact_names(card)) >= {"apartment_price", "location", "metro", "readiness", "finishing", "parking", "schools"}
    assert "parking_price" not in present_fact_names(card)

    split = split_requested_facts(("parking", "parking_price", "bad_fact"), card)
    assert split.available == ("parking",)
    assert split.missing == ("parking_price",)
    assert split.dynamic == ("parking_price",)
    assert split.unsupported == ("bad_fact",)


def test_dynamic_facts_need_refresh_even_when_cached_value_exists() -> None:
    card = OptionCard(name="Мичуринский парк", infrastructure=("паркинг",), parking_price="от 1,8 млн")

    assert "parking_price" in present_fact_names(card)
    split = split_requested_facts(("parking_price",), card)

    assert split.available == ()
    assert split.missing == ("parking_price",)
    assert split.dynamic == ("parking_price",)

    fresh = split_requested_facts(("parking_price",), card, fresh_facts=("parking_price",))
    assert fresh.available == ("parking_price",)
    assert fresh.missing == ()


def test_apartment_inventory_present_only_for_safe_scalar_values() -> None:
    unsafe_cards = (
        OptionCard(name="dict", apartment_inventory={"houses": []}),
        OptionCard(name="list", apartment_inventory=[{"id": 1}]),
        OptionCard(name="repr", apartment_inventory="{'houses': []}"),
        OptionCard(name="missing", apartment_inventory="not available"),
    )
    for card in unsafe_cards:
        assert "apartment_inventory" not in present_fact_names(card)

    assert "apartment_inventory" in present_fact_names(OptionCard(name="count", apartment_inventory=3))
    assert "apartment_inventory" in present_fact_names(OptionCard(name="text", apartment_inventory="есть варианты"))


def test_dynamic_placeholder_values_are_not_present_even_on_direct_option_card() -> None:
    card = OptionCard(
        name="2-й Иртышский",
        parking="паркинг не предусмотрен",
        parking_price="Данные о стоимости парковочных мест не предоставлены в структурированном виде.",
        parking_inventory="parking inventory unavailable",
    )

    present = present_fact_names(card)
    assert "parking" not in present
    assert "parking_price" not in present
    assert "parking_inventory" not in present
    assert split_requested_facts(("parking", "parking_price", "parking_inventory"), card, fresh_facts=("parking_price", "parking_inventory")).missing == ("parking", "parking_price", "parking_inventory")


def test_vocabulary_backed_normalizers_keep_existing_outputs() -> None:
    assert normalize_facts(["metro", "bad", "metro", "parking_price"]) == ("metro", "parking_price")
    assert normalize_constraints_delta({"budget_max": 10, "phone": "secret", "unsupported": "drop"}) == {"max_price": 10}
