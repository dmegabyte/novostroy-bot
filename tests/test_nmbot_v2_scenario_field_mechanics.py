import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nmbot_v2.card_normalizer import normalize_card
from nmbot_v2.contracts import ExecutionResult, LotExample, OptionCard, ResponseBrief, ResponsePlan, SearchResult, SemanticPlan, Stage, StateDelta, to_jsonable
from nmbot_v2.response_composer import build_response_brief, load_prompt, request_payload
from nmbot_v2.scenario_field_mechanics import build_scenario_context
from nmbot_v2.state import ConversationState


def _card(name: str, **kwargs) -> OptionCard:
    return OptionCard(name=name, location="Москва", ready="сдан", finishing="без отделки", price_min=10_000_000, **kwargs)


def test_family_rotates_active_school_yard_park_anchors_without_move_keys_budget_fit() -> None:
    cards = (
        _card("Семейный", infrastructure=("школа во дворе",)),
        _card("Дворовый", infrastructure=("двор без машин",)),
        _card("Парковый", infrastructure=("парк рядом",)),
    )

    ctx = build_scenario_context(cards=cards, primary_scenario="family")

    assert ctx["primary_scenario"] == "family"
    assert [item["anchor_fact"] for item in ctx["cards"]] == ["schools", "safe_yard", "parks"]
    assert "безопаснее" in ctx["cards"][1]["forbidden_meanings"]
    assert "безопасный двор" in ctx["cards"][1]["forbidden_meanings"]
    permitted = " ".join(
        " ".join(
            [
                item["communication_goal"],
                *item["allowed_concepts"],
                *(str(value) for value in item["evidence"]),
            ]
        )
        for item in ctx["cards"]
    )
    assert "ключи" not in permitted
    assert "попадание в бюджет" not in permitted
    assert "можно сразу переехать" not in permitted

    readiness = build_scenario_context(cards=(_card("Готовый"),), primary_scenario="family")["cards"][0]
    assert readiness["anchor_fact"] == "readiness"
    assert "ключи" in readiness["forbidden_meanings"]
    assert "можно сразу переехать" in readiness["forbidden_meanings"]


def test_primary_scenarios_emit_only_active_block_and_own_first_anchor() -> None:
    cards = (_card("Актив", metro="10 минут пешком", room_formats=("студии",), sales_count=12, ads_count=4),)

    assert build_scenario_context(cards=cards, primary_scenario="life")["cards"][0]["anchor_fact"] == "metro"
    assert build_scenario_context(cards=cards, primary_scenario="investment")["cards"][0]["anchor_fact"] == "apartment_price"
    assert build_scenario_context(cards=cards, primary_scenario="rental")["cards"][0]["anchor_fact"] == "room_formats"

    investment_ctx = json.dumps(build_scenario_context(cards=cards, primary_scenario="investment"), ensure_ascii=False)
    assert '"primary_scenario": "investment"' in investment_ctx
    assert '"primary_scenario": "family"' not in investment_ctx
    assert '"primary_scenario": "rental"' not in investment_ctx


def test_raw_cards_preserve_first_anchor_for_life_investment_and_rental() -> None:
    life = normalize_card({"name": "Для жизни", "infrastructure": {"shops": True}})
    investment = normalize_card({"name": "Для вложений", "min_price": 12_400_000, "egrn_top_novos": {"sales": 7}})
    rental = normalize_card({"name": "Под аренду", "apartment_types": [{"rooms": 1}], "finishing": "с отделкой"})

    assert build_scenario_context(cards=(life,), primary_scenario="life")["cards"][0]["anchor_fact"] == "daily_services"
    assert build_scenario_context(cards=(investment,), primary_scenario="investment")["cards"][0]["anchor_fact"] == "apartment_price"
    assert build_scenario_context(cards=(rental,), primary_scenario="rental")["cards"][0]["anchor_fact"] == "room_formats"


def test_financing_overlay_coexists_with_base_scenario() -> None:
    cards = (_card("Ипотечный", infrastructure=("школа",), mortgage_terms="семейная ипотека", discount="скидка 3%"),)

    ctx = build_scenario_context(cards=cards, primary_scenario="family", overlay="financing")

    assert ctx["primary_scenario"] == "family"
    assert ctx["overlay"] == "financing"
    assert ctx["cards"][0]["anchor_fact"] == "schools"
    assert ctx["cards"][0]["overlay_angle"]["anchor_fact"] == "mortgage_terms"
    assert "одобрение кредита" in ctx["cards"][0]["overlay_angle"]["forbidden_meanings"]


def test_evidence_is_bounded_before_model_payload() -> None:
    long_text = "парк " + "очень " * 80
    card = _card(
        "Ограниченный",
        infrastructure=(long_text, "лес", "парк", "сквер"),
        room_formats=("студия", "однокомнатная", "двухкомнатная", "трёхкомнатная"),
    )

    family = build_scenario_context(cards=(card,), primary_scenario="family")["cards"][0]
    rental = build_scenario_context(cards=(card,), primary_scenario="rental")["cards"][0]

    assert len(family["evidence"]) <= 3
    assert all(len(str(value)) <= 160 for value in family["evidence"])
    assert len(rental["evidence"]) <= 3


def test_equal_shared_location_readiness_finishing_once_and_not_card_anchors() -> None:
    cards = (
        _card("Первый", infrastructure=("школа",)),
        _card("Второй", infrastructure=("парк",)),
        _card("Третий", metro="5 минут пешком"),
    )

    ctx = build_scenario_context(cards=cards, primary_scenario="family")

    shared = {item["fact"] for item in ctx["shared_facts"]}
    anchors = {item["anchor_fact"] for item in ctx["cards"]}

    assert {"location", "readiness", "finishing"}.issubset(shared)
    assert not anchors & {"location", "readiness", "finishing"}


def test_price_min_evidence_is_project_lower_bound_safe_not_budget_fit() -> None:
    ctx = build_scenario_context(cards=(_card("Цена"),), primary_scenario="investment")

    card = ctx["cards"][0]
    assert card["anchor_fact"] == "apartment_price"
    assert card["evidence"] == [
        {"kind": "project_lower_bound", "value": 10_000_000, "safe_meaning": "нижняя цена проекта, не цена нужной комнатности и не попадание в бюджет"}
    ]
    assert "попадание в бюджет" in card["forbidden_meanings"]


def test_base_facts_are_preselected_and_do_not_compete_with_anchor() -> None:
    card = _card("Семейный", infrastructure=("школа",), metro="7 минут пешком")

    planned = build_scenario_context(cards=(card,), primary_scenario="family")["cards"][0]

    assert planned["anchor_fact"] == "schools"
    assert [item["fact"] for item in planned["base_facts"]] == ["location", "apartment_price"]
    assert "metro" not in json.dumps(planned["base_facts"], ensure_ascii=False)


def test_response_brief_payload_has_scenario_context_and_no_old_allowed_benefit_prose() -> None:
    cards = (_card("Семейный", infrastructure=("школа",)),)
    plan = SemanticPlan(operation="search", intent="family", facets=["schools"])
    response_plan = ResponsePlan(
        acknowledgement="",
        cards=cards,
        viewpoint="family",
        recipe_cards=({"card_name": "Семейный", "anchor_fact": "readiness", "allowed_benefit": "Готовность помогает семье планировать переезд.", "card_mode": "bounded"},),
        allowed_benefit="Готовность помогает семье планировать переезд.",
    )

    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=plan,
        execution=ExecutionResult(ok=True, search=SearchResult(facts=cards)),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=response_plan,
    )
    payload = request_payload(brief)
    payload_text = payload["query"]
    brief_json = to_jsonable(brief)

    assert brief_json["scenario_context"]["primary_scenario"] == "family"
    assert brief_json["scenario_context"]["cards"][0]["anchor_fact"] == "schools"
    assert brief_json["allowed_benefit"] == ""
    assert "allowed_benefit" not in brief_json["recipe_cards"][0]
    assert "планировать переезд" not in payload_text.casefold()

    model_payload = json.loads(payload_text.removeprefix("V2_RESPONSE_BRIEF=").split("\n", 1)[0])["brief"]
    assert model_payload["canonical_cards"] == [{"name": "Семейный"}]
    assert model_payload["user_question"] == ""
    assert model_payload["scenario_context"]["content_source"] == "scenario_context_only"
    assert [item["fact"] for item in model_payload["scenario_context"]["cards"][0]["base_facts"]] == ["location", "apartment_price"]
    assert "finishing" not in json.dumps(model_payload["canonical_cards"], ensure_ascii=False)


def test_prompt_is_generic_without_hardcoded_scenario_priorities() -> None:
    prompt = load_prompt().casefold()

    assert "recipe_cards" not in prompt
    assert "allowed_benefit" not in prompt
    assert "presentation_scope" in prompt
    assert "detail_facts" in prompt
    assert "requested_facts" in prompt
    for scenario_name in ("family", "rental", "investment", "life", "семейн", "аренд", "инвест"):
        assert scenario_name not in prompt


def test_family_third_card_daily_services_beats_price_anchor() -> None:
    cards = (
        _card("Школа", infrastructure=("школа",)),
        _card("Двор", infrastructure=("двор без машин",)),
        _card("Быт", daily_services=("магазины и сервисы",)),
    )

    ctx = build_scenario_context(cards=cards, primary_scenario="family")

    assert [item["anchor_fact"] for item in ctx["cards"]] == ["schools", "safe_yard", "daily_services"]
    assert ctx["cards"][2]["evidence"] == ["магазины и сервисы"]


def test_normalizer_recognizes_daily_services_and_healthcare_aliases_safely() -> None:
    card = normalize_card({
        "name": "Инфра",
        "shops": ["магазины и сервисы"],
        "clinics": [{"name": "поликлиника"}],
        "pharmacies": ["аптека"],
    })

    assert "магазины и сервисы" in card.daily_services
    assert "поликлиника" in card.healthcare
    assert "аптека" in card.healthcare
    ctx = build_scenario_context(cards=(card,), primary_scenario="family")
    assert ctx["cards"][0]["anchor_fact"] in {"daily_services", "healthcare"}
    assert "пешком" in ctx["cards"][0]["forbidden_meanings"]


def test_ecology_mechanic_is_rating_only() -> None:
    card = _card("Эко", ecology_rating=7)

    item = build_scenario_context(cards=(card,), primary_scenario="family", facets=("eco",))["cards"][0]

    assert item["anchor_fact"] == "ecology_rating"
    assert item["evidence"][0]["safe_meaning"] == "только значение рейтинга, без вывода о здоровье или безопасности"
    assert "экологически чисто" in item["forbidden_meanings"]


def test_financing_overlay_uses_separate_structured_fields_first() -> None:
    card = _card("Финансы", infrastructure=("школа",), mortgage_terms="ипотека от 6%", mortgage_rate=6, mortgage_down_payment=20, mortgage_term=30, installment_months=12)

    item = build_scenario_context(cards=(card,), primary_scenario="family", overlay="financing")["cards"][0]

    assert item["overlay_angle"]["anchor_fact"] == "mortgage_rate"
    assert item["overlay_angle"]["evidence"] == [{"kind": "mortgage_rate", "value": 6}]
    assert "ежемесячный платёж" in item["overlay_angle"]["forbidden_meanings"]


def test_shortlist_excludes_selected_only_details_and_selected_scope_includes_them() -> None:
    card = _card(
        "Детальный",
        parking=True,
        parking_price=1_000_000,
        parking_inventory=5,
        recurring_costs="80 руб/м²",
        purchase_terms=("214-ФЗ",),
        building_profile=("25 этажей", "лифт"),
        property_formats=("апартаменты",),
        lot_examples=(LotExample(rooms="2", area_m2=55, floor=7, floors_total=25, kitchen_area=12, balcony="балкон"),),
    )

    shortlist = build_scenario_context(cards=(card,), primary_scenario="life")
    selected = build_scenario_context(cards=(card,), primary_scenario="life", presentation_scope="selected")

    assert shortlist["presentation_scope"] == "shortlist"
    assert "detail_facts" not in shortlist["cards"][0]
    assert selected["presentation_scope"] == "selected"
    detail_facts = {item["fact"] for item in selected["cards"][0]["detail_facts"]}
    assert {"parking_price", "parking_inventory", "recurring_costs", "purchase_terms", "building_profile"}.issubset(detail_facts)
    for detail in selected["cards"][0]["detail_facts"]:
        assert detail["communication_goal"]
        assert detail["allowed_concepts"]
        assert detail["forbidden_meanings"]


def test_parking_price_inventory_stay_separate_from_parking_fact() -> None:
    card = _card("Паркинг", parking=True, parking_price=900_000, parking_inventory=3)
    selected = build_scenario_context(cards=(card,), primary_scenario="life", facets=("parking",), presentation_scope="selected")["cards"][0]

    assert selected["anchor_fact"] == "parking"
    assert selected["evidence"][0]["kind"] == "parking_exists"
    detail = {item["fact"]: item["evidence"] for item in selected["detail_facts"]}
    assert detail["parking_price"][0]["kind"] == "parking_price"
    assert detail["parking_inventory"][0]["kind"] == "parking_inventory"


def test_selected_detail_corridors_cover_each_detail_fact() -> None:
    card = _card(
        "Все детали",
        parking_price=1_000_000,
        parking_inventory=2,
        room_prices=({"rooms": "2", "value": 12_000_000},),
        price_square=250_000,
        recurring_costs="90 руб/м²",
        purchase_terms=("ДДУ/эскроу",),
        building_profile=("25 этажей",),
        property_formats=("апартаменты",),
        lot_examples=(LotExample(rooms="2", area_m2=55),),
    )

    selected = build_scenario_context(cards=(card,), primary_scenario="life", presentation_scope="selected")["cards"][0]

    assert len(selected["detail_facts"]) == 5
    for item in selected["detail_facts"]:
        assert {"fact", "evidence", "communication_goal", "allowed_concepts", "forbidden_meanings"}.issubset(item)
        assert len(item["evidence"]) <= 3


def test_requested_selected_detail_fact_is_prioritized_before_safe_order_limit() -> None:
    card = _card(
        "Много деталей",
        parking_price=1_000_000,
        parking_inventory=2,
        room_prices=({"rooms": "2", "value": 12_000_000},),
        price_square=250_000,
        recurring_costs="90 руб/м²",
        purchase_terms=("ДДУ/эскроу",),
        building_profile=("25 этажей",),
        property_formats=("апартаменты",),
        lot_examples=(LotExample(rooms="2", area_m2=55),),
    )

    lot_first = build_scenario_context(cards=(card,), primary_scenario="life", presentation_scope="selected", requested_facts=("lot_examples",))["cards"][0]["detail_facts"]
    building_first = build_scenario_context(cards=(card,), primary_scenario="life", presentation_scope="selected", requested_facts=("building_profile",))["cards"][0]["detail_facts"]
    broad_parking = build_scenario_context(cards=(card,), primary_scenario="life", presentation_scope="selected", requested_facts=("parking",))["cards"][0]["detail_facts"]

    assert len(lot_first) == 5
    assert lot_first[0]["fact"] == "lot_examples"
    assert building_first[0]["fact"] == "building_profile"
    assert broad_parking[0]["fact"] == "parking_price"


def test_room_prices_and_price_square_are_distinct_from_project_lower_bound() -> None:
    card = normalize_card({"name": "Цены", "min_price": 8_000_000, "price1": 9_000_000, "price2": 12_000_000, "price_square": 250_000})
    selected = build_scenario_context(cards=(card,), primary_scenario="investment", presentation_scope="selected")

    assert selected["cards"][0]["anchor_fact"] == "apartment_price"
    assert selected["cards"][0]["evidence"][0]["value"] == 8_000_000
    detail = {item["fact"]: item["evidence"] for item in selected["cards"][0]["detail_facts"]}
    assert detail["room_specific_price"][0] == {"kind": "room_price", "rooms": "1", "value": 9000000}
    assert detail["price_per_m2"] == [{"kind": "price_per_m2", "value": 250000}]


def test_transport_access_is_bounded_literal_geography() -> None:
    card = normalize_card({"name": "Маршрут", "property_railway": "МЦД", "highway_name": "Минское шоссе", "distance_from_mkad": 12})
    item = build_scenario_context(cards=(card,), primary_scenario="life", facets=("highway",))["cards"][0]

    assert item["anchor_fact"] == "transport_access"
    assert item["evidence"] == ["ж/д ориентир: МЦД", "шоссе: Минское шоссе", "12 км от МКАД"]
    assert "lat" not in json.dumps(item, ensure_ascii=False).casefold()
    assert "long" not in json.dumps(item, ensure_ascii=False).casefold()


def test_unknown_raw_sensitive_fields_never_reach_model_facing_payload() -> None:
    card = normalize_card({"name": "Сырой", "developer_description": "секрет", "object_site": "https://example.test", "lat": 1, "long": 2, "infrastructure": ["магазины"]})
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="family"),
        execution=ExecutionResult(ok=True, search=SearchResult(facts=(card,))),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="", cards=(card,), viewpoint="family"),
    )
    payload = request_payload(brief)["query"]

    assert "developer_description" not in payload
    assert "object_site" not in payload
    assert '"lat"' not in payload
    assert '"long"' not in payload
    model_payload = json.loads(payload.removeprefix("V2_RESPONSE_BRIEF=").split("\n", 1)[0])["brief"]
    assert model_payload["canonical_cards"] == [{"name": "Сырой"}]
    assert model_payload["scenario_context"]["content_source"] == "scenario_context_only"


def test_recommend_current_payload_identity_only_with_selected_detail_facts() -> None:
    card = _card("Выбранный", parking_price=900_000, building_profile=("25 этажей",))
    scenario_context = build_scenario_context(cards=(card,), primary_scenario="life", presentation_scope="selected")
    brief = ResponseBrief(answer_goal="recommend_current", canonical_cards=(card,), scenario_context=scenario_context, user_question="расскажи подробнее")

    payload = request_payload(brief)["query"]
    model_payload = json.loads(payload.removeprefix("V2_RESPONSE_BRIEF=").split("\n", 1)[0])["brief"]

    assert model_payload["canonical_cards"] == [{"name": "Выбранный"}]
    assert model_payload["scenario_context"]["presentation_scope"] == "selected"
    assert model_payload["scenario_context"]["cards"][0]["detail_facts"]
    assert "parking_price" in json.dumps(model_payload["scenario_context"], ensure_ascii=False)
    assert "finishing" not in json.dumps(model_payload["canonical_cards"], ensure_ascii=False)


def test_build_response_brief_passes_requested_facts_to_selected_scenario_context() -> None:
    card = _card(
        "Выбранный",
        parking_price=900_000,
        parking_inventory=3,
        room_prices=({"rooms": "1", "value": 9_000_000},),
        price_square=240_000,
        recurring_costs="80 руб/м²",
        building_profile=("25 этажей",),
        lot_examples=(LotExample(rooms="1", area_m2=38),),
    )

    brief = build_response_brief(
        stage=Stage.SELECTED_OBJECT,
        plan=SemanticPlan(operation="answer_selected", selected_option_name="Выбранный", requested_facts=("lot_examples",)),
        execution=ExecutionResult(ok=True, selected=card),
        delta=StateDelta(),
        state=ConversationState(selected_option_name="Выбранный"),
        response_plan=ResponsePlan(acknowledgement="", cards=(card,), viewpoint="life"),
    )

    assert brief.scenario_context["presentation_scope"] == "selected"
    assert brief.scenario_context["cards"][0]["detail_facts"][0]["fact"] == "lot_examples"


def test_answer_open_question_payload_keeps_full_canonical_cards_for_current_contract() -> None:
    card = _card("Открытый", parking_price=900_000)
    scenario_context = build_scenario_context(cards=(card,), primary_scenario="life", presentation_scope="selected")
    brief = ResponseBrief(answer_goal="answer_open_question", requested_facts=("finishing",), canonical_cards=(card,), scenario_context=scenario_context)

    payload = request_payload(brief)["query"]
    model_payload = json.loads(payload.removeprefix("V2_RESPONSE_BRIEF=").split("\n", 1)[0])["brief"]

    assert model_payload["canonical_cards"][0]["name"] == "Открытый"
    assert model_payload["canonical_cards"][0]["finishing"] == "без отделки"
    assert model_payload["canonical_cards"][0]["parking_price"] == 900000


def test_shortlist_payload_has_no_selected_detail_facts_or_selected_only_payload_fields() -> None:
    card = _card("Короткий", parking_price=900_000, building_profile=("25 этажей",))
    brief = ResponseBrief(answer_goal="present_search_results", canonical_cards=(card,), scenario_context=build_scenario_context(cards=(card,), primary_scenario="life"))

    payload = request_payload(brief)["query"]
    model_payload = json.loads(payload.removeprefix("V2_RESPONSE_BRIEF=").split("\n", 1)[0])["brief"]

    assert model_payload["canonical_cards"] == [{"name": "Короткий"}]
    assert "detail_facts" not in json.dumps(model_payload["scenario_context"], ensure_ascii=False)
    assert "parking_price" not in json.dumps(model_payload, ensure_ascii=False)
    assert "building_profile" not in json.dumps(model_payload, ensure_ascii=False)
