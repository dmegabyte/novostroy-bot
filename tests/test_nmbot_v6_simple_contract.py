import pytest

from nmbot_v6.simple_contract import (
    FACT_FIELDS, PARAM_FIELDS, SimpleContractError, build_prompt1_input, build_prompt2_input,
    parse_prompt1, parse_prompt2,
)
from nmbot_v6.simple_state import SimpleState


def test_exact_payloads_and_empty_p1():
    p1 = parse_prompt1({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}})
    history = [{"role": "user", "text": "старое"}, {"role": "assistant", "text": "ответ"}]
    assert build_prompt1_input("текущее", history, pending_offer="specialist_contact")["dialogue_policy"] == {"pending_offer": "specialist_contact"}
    assert build_prompt2_input("текущее", history, p1, offer_specialist_now=True)["dialogue_policy"] == {"offer_specialist_now": True}
    assert "текущее" not in [item["text"] for item in history]


@pytest.mark.parametrize("raw", [
    {"cards": [], "missing": []},
    {"action": "reply", "response": "x"},
    {"action": "continue", "facts": [{"guessed": "x"}], "near": [], "missing": [], "params": {}},
    {"action": "continue", "facts": [{"name": "+7 999 123-45-67"}], "near": [], "missing": [], "params": {}},
])
def test_p1_rejects_shape_and_guessed_fields(raw):
    with pytest.raises(SimpleContractError):
        parse_prompt1(raw)


def test_allowlist_includes_source_backed_h108_keys_only():
    assert {"name", "ads", "price_min", "location_name", "is_near", "why_close", "differences", "object_id"} <= FACT_FIELDS
    assert "guessed" not in FACT_FIELDS


def test_h108_material_is_preserved_literal_and_diagnostics_dropped():
    raw = {"action": "continue", "facts": [{"name": "ЖК А", "location": ["ЦАО"], "rooms": [2], "price_min": 10,
                      "ads": [{"rooms": 2, "price": 10}]}],
           "near": [{"name": "ЖК Б", "is_near": True, "why_close": "рядом", "differences": ["цена"]}],
           "missing": ["срок сдачи"], "params": {"purpose": "life", "location": ["ЦАО"], "min_price": 10},
           "diagnostics": {"ignored": True}}
    p1 = parse_prompt1(raw)
    handoff = build_prompt2_input("x", [], p1, offer_specialist_now=False)
    assert handoff["property_material"] == {"facts": raw["facts"], "near": raw["near"], "params": raw["params"]}
    assert handoff["missing"] == raw["missing"] and "diagnostics" not in str(handoff)


def test_h124_c01_returned_location_name_is_preserved_literal():
    raw = {
        "action": "continue", "facts": [{"id": 2332, "name": "ЖК А", "location_name": "Западное Дегунино", "min_price": 13_605_280}],
        "near": [],
        "missing": ["метро"],
        "params": {"district": "msk", "max_price": 18_000_000, "rooms": "2"},
    }
    document = parse_prompt1(raw)
    assert build_prompt2_input("x", [], document, offer_specialist_now=False)["property_material"]["facts"] == raw["facts"]


def test_h125_c02_returned_only_with_flats_stays_non_factual_param():
    raw = {
        "action": "continue", "facts": [{"id": 2332, "name": "ЖК А", "location_name": "Западное Дегунино", "new_building_class": "business"}],
        "near": [],
        "missing": [],
        "params": {"district": "msk", "only_with_flats": True},
    }
    document = parse_prompt1(raw)
    handoff = build_prompt2_input("x", [], document, offer_specialist_now=False)
    assert handoff["property_material"]["params"] == raw["params"]
    assert "only_with_flats" not in handoff["property_material"]["facts"][0]


def test_h127_full_source_backed_params_vocabulary_and_unknown_rejection():
    expected = {
        "rooms", "max_price", "min_price", "district", "location", "floor",
        "has_renovation", "count", "purpose", "facets", "mortgage_type",
        "delivered", "search_mode", "ready", "finishing", "area_min_m2",
        "area_max_m2", "format", "rooms_preference", "budget_preference",
        "location_preference", "infrastructure_preference", "transport_preference",
        "finance_preference", "sort_hint", "only_with_flats", "location_name",
    }
    assert expected <= PARAM_FIELDS
    raw = {"action": "continue", "facts": [], "near": [], "missing": [], "params": {key: True for key in expected}}
    assert parse_prompt1(raw).params == raw["params"]
    with pytest.raises(SimpleContractError):
        parse_prompt1({"action": "continue", "facts": [], "near": [], "missing": [], "params": {"not_source_backed": True}})


def test_h128_named_query_context_is_non_factual_and_unknown_remains_rejected():
    raw = {
        "action": "continue", "facts": [{"id": 2319, "name": "Резиденция Сокольники", "min_price": 34_788_000}],
        "near": [],
        "missing": ["Не уточнено, какой именно ЖК «Сокол» интересует пользователя."],
        "params": {"name": "Сокол"},
    }
    handoff = build_prompt2_input("А сколько там стоит двушка?", [], parse_prompt1(raw), offer_specialist_now=False)
    assert handoff["property_material"]["params"] == {"name": "Сокол"}
    assert handoff["property_material"]["facts"] == raw["facts"]
    assert handoff["property_material"]["facts"][0]["name"] != handoff["property_material"]["params"]["name"]
    with pytest.raises(SimpleContractError):
        parse_prompt1({"action": "continue", "facts": [], "near": [], "missing": [], "params": {"query_name_untrusted": "Сокол"}})


def test_h137_novos_id_is_accepted_and_unknown_error_is_safe_and_structured():
    raw = {"action": "continue", "facts": [], "near": [], "missing": [], "params": {"novos_id": 2319}}
    assert parse_prompt1(raw).params == {"novos_id": 2319}
    with pytest.raises(SimpleContractError) as caught:
        parse_prompt1({"action": "continue", "facts": [], "near": [], "missing": [], "params": {"unknown_source_key": "private-value"}})
    assert str(caught.value) == caught.value.code == "invalid_param_key"
    assert caught.value.field == "unknown_source_key"
    assert "private-value" not in repr(caught.value)


def test_h139_unknown_fact_field_keeps_only_the_field_name():
    with pytest.raises(SimpleContractError) as caught:
        parse_prompt1({"action": "continue", "facts": [{"name": "ЖК А", "unknown_fact_key": "private-value"}], "near": [], "missing": [], "params": {}})
    assert caught.value.code == "invalid_fact_field"
    assert caught.value.field == "unknown_fact_key"
    assert "private-value" not in repr(caught.value)


def test_h140_observed_h108_project_and_unit_objects_are_literal_material():
    raw = {
        "action": "continue", "facts": [
            {"id": 1, "name": "Люблинский парк", "district": "ЮВАО", "location_name": "Люблино", "min_price": 10, "max_price": 20, "rooms": [2], "new_building_class": "comfort", "delivered": True},
            {"id": 2, "title": "2-комнатная квартира", "novos_id": 1, "rooms": 2, "price": 12, "fullprice": 12, "area": 50, "floor": 5, "floors_total": 17, "renovation": "есть", "status": 2},
        ],
        "near": [], "missing": [], "params": {"novos_id": 1, "rooms": 2},
    }
    assert parse_prompt1(raw).plain() == raw


def test_h144_observed_mortgage_identity_and_finishing_fields_are_bounded():
    raw = {
        "action": "continue", "facts": [{"name": "Бусиновский парк", "zhk_name": "Бусиновский парк", "mortgage_programs": ["family"]}],
        "near": [], "missing": [], "params": {"has_finishing": True},
    }
    assert parse_prompt1(raw).plain() == raw


@pytest.mark.parametrize("raw", [
    {"action": "continue", "facts": [{"field": "name", "scope": "project", "value": "ЖК А"}], "near": [], "missing": [], "params": {}},
    {"action": "continue", "facts": [{"name": "ЖК А", "metadata": {"x": 1}}], "near": [], "missing": [], "params": {}},
    {"action": "continue", "facts": [{"name": "ЖК А", "ads": [{"email": "x@example.test"}]}], "near": [], "missing": [], "params": {}},
    {"action": "continue", "facts": [], "near": [], "missing": [{"field": "name", "scope": "project"}], "params": {}},
    {"action": "continue", "facts": [], "near": [], "missing": [], "params": {"scenario": "life"}},
])
def test_p1_rejects_old_shape_and_internal_material(raw):
    with pytest.raises(SimpleContractError):
        parse_prompt1(raw)


def test_p1_rejects_oversized_deep_and_malformed_values():
    base = {"action": "continue", "facts": [], "near": [], "missing": [], "params": {}}
    with pytest.raises(SimpleContractError):
        parse_prompt1({**base, "facts": [{"name": "x" * 2001}]})
    nested = value = {}
    for _ in range(7):
        child = {}
        value["name"] = child
        value = child
    with pytest.raises(SimpleContractError):
        parse_prompt1({**base, "facts": [nested]})
    with pytest.raises(SimpleContractError):
        parse_prompt1("{not json")


@pytest.mark.parametrize(("raw", "ok"), [
    ({"action": "reply", "response": "Ответ", "final_question": ""}, True),
    ({"action": "request_phone", "response": "", "final_question": ""}, True),
    ({"action": "reply", "response": ""}, False),
    ({"action": "reply", "response": "" , "final_question": ""}, False),
    ({"action": "request_phone", "response": "текст", "final_question": ""}, False),
    ({"action": "request_phone", "response": "", "final_question": "Вопрос?"}, False),
    ({"action": "other", "response": "текст", "final_question": ""}, False),
    ({"action": "reply", "response": "Ответ", "final_question": "", "extra": 1}, False),
    ({"action": "reply", "response": "+7 999 123-45-67", "final_question": ""}, False),
    ({"action": "reply", "response": "Ответ", "final_question": "+7 999 123-45-67"}, False),
])
def test_p2_exact_contract(raw, ok):
    if ok:
        assert parse_prompt2(raw).action == raw["action"]
    else:
        with pytest.raises(SimpleContractError):
            parse_prompt2(raw)


def test_p2_combined_publication_limit_and_no_semantic_validation():
    document = parse_prompt2({"action": "reply", "response": "x" * 1_995, "final_question": "или"})
    assert document.final_question == "или"
    with pytest.raises(SimpleContractError, match="output_too_large"):
        parse_prompt2({"action": "reply", "response": "x" * 1_998, "final_question": "x"})


def test_state_v1_migrates_to_exact_v2_and_plain_is_v2():
    legacy = {
        "schema_version": 1, "revision": 1,
        "history": [{"role": "user", "text": "вопрос"}, {"role": "assistant", "text": "ответ"}],
        "awaiting_phone": False,
    }
    state = SimpleState.from_mapping(legacy)
    assert state.client_turn_count == 1 and state.pending_offer == "none"
    assert state.plain() == {
        "schema_version": 2, "revision": 1, "history": legacy["history"], "awaiting_phone": False,
        "client_turn_count": 1, "pending_offer": "none",
    }


@pytest.mark.parametrize("raw", [
    {"schema_version": 9, "revision": 0, "history": [], "awaiting_phone": False},
    {"schema_version": 2, "revision": 0, "history": [], "awaiting_phone": False, "client_turn_count": 0, "pending_offer": "unknown"},
    {"schema_version": 2, "revision": 0, "history": [], "awaiting_phone": False, "client_turn_count": 0, "pending_offer": "none", "extra": True},
    {"schema_version": 1, "revision": 0, "history": [{"role": "user", "text": "incomplete"}], "awaiting_phone": False},
])
def test_state_rejects_malformed_or_unknown_shapes(raw):
    with pytest.raises(SimpleContractError):
        SimpleState.from_mapping(raw)
