import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nmbot_v2.contracts import ComposedOption, ComposedResponse, DialogFocus, ExecutionResult, OptionCard, PendingAction, ResponseBrief, ResponsePlan, SearchResult, SelectedEntity, SemanticPlan, Stage, StateDelta, to_jsonable
from nmbot_v2.constraints import normalize_constraints_delta
from nmbot_v2.response_composer import assemble_composed_response, build_response_brief, compose_response_one_shot_async, compose_response_sync, load_prompt, parse_composer_json, request_payload, validate_composed_response
from nmbot_v2.response import build_response_plan, render_response
from nmbot_v2.state import ConversationState, apply_state_delta


def test_json_safe_contract_serialization_and_no_raw_secret_shapes():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price": "от 12 млн рублей", "ads_count": 8, "sales_count": 3}], "params": {"rooms": 2}})
    payload = to_jsonable(result)

    assert payload["facts"][0]["name"] == "Лучи"
    assert payload["facts"][0]["ads_count"] == 8
    assert payload["facts"][0]["sales_count"] == 3
    assert payload["params"] == {"rooms": 2}


def test_open_question_brief_serializes_universal_answer_policy_fields():
    brief = ResponseBrief(
        answer_goal="answer_open_question",
        user_question="Кто застройщик у этого ЖК?",
        question_subject="застройщик",
        requested_facts=("developer",),
        available_facts=("developer: ПИК",),
        missing_facts=(),
        response_policy="answer_directly",
        response_viewpoint="neutral",
    )

    payload = to_jsonable(brief)
    assert payload["user_question"] == "Кто застройщик у этого ЖК?"
    assert payload["requested_facts"] == ["developer"]
    assert payload["available_facts"] == ["developer: ПИК"]
    assert payload["response_policy"] == "answer_directly"


def test_missing_open_question_uses_operator_consent_offer_policy():
    brief = ResponseBrief(
        answer_goal="answer_open_question",
        user_question="Сильно ли дует между корпусами?",
        requested_facts=("wind_comfort",),
        missing_facts=("wind_comfort",),
        response_policy="operator_consent_offer",
        operator_handoff_template="Точный ответ уточнит оператор.",
        cta_template="В текущих данных это не подтверждено. Оператор сможет проверить. Передать оператору запрос?",
    )

    payload = to_jsonable(brief)
    assert payload["response_policy"] == "operator_consent_offer"
    assert payload["operator_handoff_template"] == "Точный ответ уточнит оператор."
    assert "Передать оператору запрос?" in payload["cta_template"]
    assert "телефон" not in payload["cta_template"].casefold()
    assert "номер" not in payload["cta_template"].casefold()
    assert "token" not in str(payload).lower()
    assert "+7" not in str(payload)


def test_state_delta_preserves_known_fields_and_reset_contract():
    state = ConversationState(params={"location": "Сокол", "rooms": 2}, selected_option_name="Лучи")
    updated = apply_state_delta(state, StateDelta(params_update={"max_price": 20_000_000}))

    assert updated.params == {"location": "Сокол", "rooms": 2, "max_price": 20_000_000}
    assert updated.selected_option_name == "Лучи"
    assert state.params == {"location": "Сокол", "rooms": 2}

    reset = apply_state_delta(updated, StateDelta(reset=True))
    assert reset == ConversationState()


def test_dialog_focus_contract_defaults_and_roundtrip() -> None:
    plan = SemanticPlan(operation="search")
    focus = DialogFocus.from_dict({"subject": "parking", "last_intent": "price", "last_requested_facts": ["parking_price"], "last_answered_facts": ["parking"]})

    assert plan.requested_facts == ()
    assert plan.facts_needed == ()
    assert plan.requires_enrichment is False
    assert plan.focus_action == "keep"
    assert focus.subject == "parking"
    assert focus.last_requested_facts == ("parking_price",)
    assert focus.last_answered_facts == ("parking",)


def test_state_delta_clear_fields_contract_clears_selected_pending_visible_operator():
    state = ConversationState(
        pending_followup="phone_capture",
        selected_option_name="Лучи",
        visible_options=(OptionCard(name="Лучи"),),
        operator_offered=True,
        operator_declined=True,
        selected_enriched=OptionCard(name="Лучи", developer="ПИК"),
    )

    cleared = apply_state_delta(
        state,
        StateDelta(clear_fields=("pending_followup", "selected_option_name", "visible_options", "operator_offered", "operator_declined", "selected_enriched")),
    )

    assert cleared.pending_followup is None
    assert cleared.selected_option_name is None
    assert cleared.visible_options == ()
    assert cleared.operator_offered is False
    assert cleared.operator_declined is False
    assert cleared.selected_enriched is None


def test_comparison_scope_state_round_trip_and_clear_are_bounded() -> None:
    state = ConversationState.from_dict({
        "comparison_scope_option_names": ["ЖК Первый", "ЖК Третий"],
    })

    assert state.comparison_scope_option_names == ("ЖК Первый", "ЖК Третий")
    assert ConversationState.from_dict(state.to_dict()).comparison_scope_option_names == ("ЖК Первый", "ЖК Третий")
    assert apply_state_delta(state, StateDelta(clear_fields=("comparison_scope_option_names",))).comparison_scope_option_names == ()
    assert ConversationState.from_dict({"comparison_scope_option_names": ["ЖК Первый"]}).comparison_scope_option_names == ()


def test_constraints_flatten_categorized_allowlist_alias_and_drop_sensitive():
    normalized = normalize_constraints_delta(
        {
            "hard": {"budget_max": 14_000_000, "location": "центр", "rooms_count": 2, "phone": "hidden"},
            "preferences": {"purpose": "rental", "financing": "mortgage", "initial_payment": 10_000_000},
            "unknown": {"token": "bad", "foo": "bar"},
        }
    )

    assert normalized == {
        "max_price": 14_000_000,
        "location": "центр",
        "rooms": 2,
        "purpose": "rental",
        "financing": "mortgage",
        "down_payment": 10_000_000,
    }


def test_option_card_entity_fields_round_trip_without_client_render_leak() -> None:
    card = OptionCard.from_dict({"name": "Лучи", "entity_id": 42, "entity_type": "residential_complex"})

    assert OptionCard.from_dict(to_jsonable(card)) == card
    text = render_response(ResponsePlan(acknowledgement="Нашла.", cards=(card,), final_question="Смотрим?"))
    assert "42" not in text
    assert "entity_id" not in text


def test_selected_entity_and_pending_action_are_strict_and_json_safe() -> None:
    entity = SelectedEntity.from_dict({"entity_type": "residential_complex", "entity_id": 42, "display_name": "Лучи"})
    action = PendingAction.from_dict({
        "action_type": "selected_live_fact_check", "fact_keys": ["parking_price"],
        "entity_type": "residential_complex", "entity_id": 42, "status": "pending", "idempotency_key": "check-42",
    })

    assert SelectedEntity.from_dict(entity.to_dict()) == entity
    assert PendingAction.from_dict(action.to_dict()) == action
    for bad in (
        {"entity_type": "other", "entity_id": 42, "display_name": "Лучи"},
        {"entity_type": "residential_complex", "entity_id": True, "display_name": "Лучи"},
    ):
        with pytest.raises(ValueError):
            SelectedEntity.from_dict(bad)
    for field, value in (("fact_keys", ["unknown_fact"]), ("action_type", "unknown"), ("status", "done"), ("raw_payload", "no")):
        bad = action.to_dict()
        bad[field] = value
        with pytest.raises(ValueError):
            PendingAction.from_dict(bad)


def test_state_entity_action_old_payload_round_trip_apply_clear_and_reset() -> None:
    assert ConversationState.from_dict({"selected_option_name": "Лучи"}).selected_entity is None
    entity = SelectedEntity("residential_complex", 42, "Лучи")
    action = PendingAction("selected_live_fact_check", ("parking_price",), "residential_complex", 42, "pending", "check-42")
    updated = apply_state_delta(ConversationState(), StateDelta(selected_entity=entity, pending_action=action))

    assert ConversationState.from_dict(updated.to_dict()) == updated
    cleared = apply_state_delta(updated, StateDelta(clear_fields=("selected_entity", "pending_action")))
    assert cleared.selected_entity is None and cleared.pending_action is None
    assert apply_state_delta(updated, StateDelta(reset=True)) == ConversationState()


def test_semantic_finance_preference_reaches_canonical_state_field() -> None:
    normalized = normalize_constraints_delta(
        {"preferences": {"finance_preference": "семейная ипотека"}}
    )

    assert normalized == {"financing": "семейная ипотека"}


def test_renderer_max_three_cards_and_exactly_one_question():
    cards = tuple(OptionCard(name=f"ЖК {idx}", price="от 10 млн рублей") for idx in range(5))
    text = render_response(ResponsePlan(acknowledgement="Да, нашла.", cards=cards, final_question="Какой смотрим?"))

    assert text.count("ЖК «") == 3
    assert text.count("?") == 1


def test_renderer_formats_production_shaped_card_for_client():
    card = OptionCard(
        name="ЖК «Лучи»",
        location="Солнцево",
        price="11000000",
        price_min=11_000_000,
        finishing="Есть отделка",
        ready="2027 г.",
        room_formats=("2 кв.",),
    )
    text = render_response(ResponsePlan(acknowledgement="Нашла вариант.", cards=(card,), final_question="Рассказать подробнее?"))

    assert "ЖК «Лучи»" in text
    assert "ЖК «ЖК" not in text
    assert "цены от 11 млн рублей" in text
    assert "с отделкой" in text
    assert "сдача в 2027 году" in text
    assert "двухкомнатные" in text
    assert "11000000" not in text
    assert "2 кв." not in text


def test_renderer_does_not_claim_finishing_when_card_is_without_finishing():
    text = render_response(
        ResponsePlan(
            acknowledgement="Нашла вариант.",
            cards=(OptionCard(name="Без отделки", finishing="без отделки"),),
            final_question="Рассказать подробнее?",
        )
    )

    assert "С отделкой — меньше ремонта" not in text


def test_renderer_formats_quarterly_readiness_for_client():
    text = render_response(
        ResponsePlan(
            acknowledgement="Нашла варианты.",
            cards=(
                OptionCard(name="Строится", ready="4 кв. 2026"),
                OptionCard(name="Сдан", ready="Сдан (1 кв. 2025)"),
            ),
            final_question="Рассказать подробнее?",
        )
    )

    assert "сдача в IV квартале 2026 года" in text
    assert "дом сдан в I квартале 2025 года" in text
    assert "4 кв." not in text


def test_first_list_multiscenario_acknowledges_all_goals_and_honest_gaps() -> None:
    cards = (
        OptionCard(name="Южные сады", location="Москва", price_min=11_900_000, room_formats=("1-комн.",), ready="2027"),
        OptionCard(name="Северный парк", location="Москва", price="от 12,4 млн рублей", room_formats=("2-комн.",), finishing="с отделкой"),
        OptionCard(name="Речной квартал", location="Москва", price_min=13_100_000, area="42 м²", metro="Речной вокзал"),
    )
    response_plan = build_response_plan(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="rental", facets=["family", "rental", "financing"]),
        execution=ExecutionResult(ok=True, search=SearchResult(facts=cards)),
        delta=StateDelta(),
        state=ConversationState(),
    )
    text = render_response(response_plan)

    assert response_plan.scenario_needs == ("family", "rental", "financing")
    assert "для семьи сейчас" in text
    assert "будущей аренды" in text
    assert "ипотек" in text.casefold()
    assert "Школы и детскую инфраструктуру нужно отдельно проверить" in text
    assert "Точные ипотечные программы и ставки пока не подтверждаю" in text
    assert text.count("ЖК «") <= 3
    assert text.count("?") == 1
    assert text.rstrip().endswith("?")
    banned = ("оператор", "mcp", "карточ", "внутрен", "для оплаты")
    assert not any(word in text.casefold() for word in banned)


def test_first_list_multiscenario_uses_grounded_family_and_finance_without_gap_caveat() -> None:
    cards = (
        OptionCard(name="Семейный берег", location="Москва", price_min=12_000_000, infrastructure=("школа", "детский сад")),
        OptionCard(name="Арендный парк", location="Москва", price_min=11_000_000, finishing="с отделкой", metro="Сокол"),
        OptionCard(name="Финансовый квартал", location="Москва", price_min=13_000_000, discount="скидка 5%", mortgage_terms="семейная ипотека от застройщика"),
    )
    text = render_response(
        build_response_plan(
            stage=Stage.FIRST_LIST,
            plan=SemanticPlan(operation="search", intent="rental", facets=["family", "rental", "financing"]),
            execution=ExecutionResult(ok=True, search=SearchResult(facts=cards)),
            delta=StateDelta(),
            state=ConversationState(),
        )
    )

    assert "школа" in text.casefold() or "детский сад" in text.casefold()
    assert "семейная ипотека от застройщика" in text.casefold() or "скидка 5%" in text.casefold()
    assert "нужно отдельно проверить по выбранному ЖК" not in text
    assert "ипотечные программы и ставки пока не подтверждаю" not in text
    assert text.count("?") == 1


def test_refinement_acknowledgement_never_leaks_finishing_enum():
    plan = build_response_plan(
        stage=Stage.REFINEMENT,
        plan=SemanticPlan(operation="search", intent="life"),
        execution=ExecutionResult(ok=True, search=SearchResult.from_dict({"facts": [], "missing": ["finishing"], "params": {"finishing": "full"}})),
        delta=StateDelta(params_update={"finishing": "full"}),
        state=ConversationState(params={"location": ["Москва"]}),
    )
    text = render_response(plan)

    assert "ищем варианты с отделкой" in text
    assert "finishing" not in text.casefold()
    assert " full" not in text.casefold()


def test_initial_empty_search_asks_one_clarification_without_absence_or_relaxation():
    search = SearchResult.from_dict({"facts": [], "near": [], "missing": ["location", "какой-то сырой missing prose от модели"], "params": {}})
    plan = build_response_plan(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="life"),
        execution=ExecutionResult(ok=True, search=search),
        delta=StateDelta(),
        state=ConversationState(),
    )
    text = render_response(plan)

    assert "Точно таких вариантов" not in text
    assert "Ослабим" not in text
    assert "сырой missing" not in text
    assert text.count("?") == 1
    assert text.endswith("В какой локации или у какого метро искать?")


def test_empty_search_with_real_constraints_relaxes_one_existing_parameter():
    search = SearchResult.from_dict({"facts": [], "near": [], "missing": ["точные варианты"], "params": {}})
    plan = build_response_plan(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="life"),
        execution=ExecutionResult(ok=True, search=search),
        delta=StateDelta(),
        state=ConversationState(params={"max_price": 12_000_000}),
    )
    text = render_response(plan)

    assert "Точно таких вариантов сейчас не вижу" in text
    assert text.endswith("Ослабим бюджет?")


def test_initial_canonical_location_does_not_look_like_user_changed_criteria():
    search = SearchResult.from_dict({"facts": [], "near": [], "missing": ["точные варианты"], "params": {"location": "Зеленоград"}})
    plan = build_response_plan(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="life"),
        execution=ExecutionResult(ok=True, search=search),
        delta=StateDelta(params_update={"location": "Зеленоград"}),
        state=ConversationState(),
    )

    text = render_response(plan)

    assert "Условия поиска: локация — Зеленоград." in text
    assert "Теперь ищем так" not in text
    assert "локация теперь" not in text
    assert text.endswith("Ослабим локацию?")


def test_refinement_still_acknowledges_real_location_change():
    search = SearchResult.from_dict({"facts": [], "near": [], "missing": ["точные варианты"], "params": {"location": "Зеленоград"}})
    plan = build_response_plan(
        stage=Stage.REFINEMENT,
        plan=SemanticPlan(operation="search", intent="life"),
        execution=ExecutionResult(ok=True, search=search),
        delta=StateDelta(params_update={"location": "Зеленоград"}),
        state=ConversationState(params={"location": "Москва"}),
    )

    text = render_response(plan)

    assert "Теперь ищем так: локация теперь Зеленоград." in text


def test_missing_room_evidence_does_not_claim_inventory_absence():
    search = SearchResult.from_dict({"facts": [], "near": [], "missing": ["rooms"], "params": {"rooms": [2]}})
    plan = build_response_plan(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="family"),
        execution=ExecutionResult(ok=True, search=search),
        delta=StateDelta(params_update={"rooms": [2]}),
        state=ConversationState(active_topic="family"),
    )
    text = render_response(plan)

    assert "Пока не вижу точного совпадения" in text
    assert "Точно таких вариантов" not in text
    assert "Ослабим" not in text
    assert text.count("?") == 1


def test_investment_fallback_does_not_infer_choice_or_fast_deal_from_ads_and_ready():
    card = OptionCard(name="Лучи", location="Москва", price_min=12_000_000, ready="сдан", ads_count=12)
    text = render_response(
        ResponsePlan(
            acknowledgement="Нашла вариант.",
            cards=(card,),
            viewpoint="investment",
            final_question="Рассказать подробнее?",
        )
    )

    assert "текущий выбор" not in text.casefold()
    assert "быстрее перейти к сделке" not in text.casefold()
    assert "на витрине указано 12 объявлений" in text.casefold()


def test_near_empty_search_stays_near_presentation():
    search = SearchResult.from_dict({"facts": [], "near": [{"name": "Почти", "location": "Динамо", "why_close": "рядом"}], "missing": []})
    plan = build_response_plan(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="life"),
        execution=ExecutionResult(ok=True, search=search),
        delta=StateDelta(),
        state=ConversationState(),
    )
    text = render_response(plan)

    assert "Ближайший вариант: ЖК «Почти»" in text
    assert "Показываю ближайшие варианты" in text


def test_renderer_normalizes_duplicate_punctuation_generically():
    text = render_response(ResponsePlan(acknowledgement="Поняла..", caveat="Проверю!!", final_question="Продолжим?!"))

    assert ".." not in text
    assert "!!" not in text
    assert "?!" not in text


def test_response_brief_is_safe_compact_and_uses_canonical_cards_only():
    search = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000, "location": "Москва"}], "missing": ["finance"], "params": {"location": "Москва"}})
    plan = build_response_plan(stage=Stage.FIRST_LIST, plan=SemanticPlan(operation="search", intent="life"), execution=ExecutionResult(ok=True, search=search), delta=StateDelta(), state=ConversationState())
    brief = build_response_brief(stage=Stage.FIRST_LIST, plan=SemanticPlan(operation="search", intent="life"), execution=ExecutionResult(ok=True, search=search), delta=StateDelta(), state=ConversationState(), response_plan=plan)
    payload = to_jsonable(brief)

    assert payload["canonical_cards"][0]["name"] == "Лучи"
    assert payload["canonical_missing_summary"] == []
    assert "price_min" in payload["allowed_fact_fields"]
    assert "mcp" not in str(payload).casefold()
    assert "token" not in str(payload).casefold()


def test_first_list_search_missing_diagnostics_do_not_create_client_missing_note() -> None:
    search = SearchResult.from_dict(
        {
            "facts": [{"name": "Лучи", "price_min": 12_000_000, "location": "Москва"}],
            "missing": ["finance", "ads"],
        }
    )
    response_plan = build_response_plan(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="family"),
        execution=ExecutionResult(ok=True, search=search),
        delta=StateDelta(),
        state=ConversationState(active_topic="life"),
    )
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="family"),
        execution=ExecutionResult(ok=True, search=search),
        delta=StateDelta(),
        state=ConversationState(active_topic="life"),
        response_plan=response_plan,
    )

    assert brief.canonical_missing_summary == ()
    assert brief.missing_facts == ()
    assert "missing_note_required" not in validate_composed_response(
        ComposedResponse(
            intro="Нашла вариант.",
            options=(ComposedOption(name="Лучи", facts="Цена от 12 млн рублей.", description="Это подтверждённый ориентир по бюджету."),),
            missing_note="",
            final_question="Какой вариант смотрим подробнее?",
        ),
        brief,
    )


def test_facts_needed_without_requested_facts_stays_internal_for_missing_note() -> None:
    card = OptionCard(name="Лучи", price_min=12_000_000)
    plan = SemanticPlan(operation="answer_open_question", facts_needed=("mortgage_terms",), resolved_subject="ипотека")
    brief = build_response_brief(
        stage=Stage.CURRENT_OPTIONS,
        plan=plan,
        execution=ExecutionResult(ok=True, selected=card),
        delta=StateDelta(),
        state=ConversationState(selected_option_name="Лучи", visible_options=(card,)),
        response_plan=ResponsePlan(acknowledgement="Смотрю по текущему варианту.", cards=(card,), viewpoint="financing", final_question="Передать вопрос оператору?"),
    )

    assert brief.requested_facts == ()
    assert brief.missing_facts == ()
    assert brief.canonical_missing_summary == ()
    assert brief.response_policy == ""
    assert "missing_note_required" not in validate_composed_response(
        ComposedResponse(intro="По текущей карточке отвечаю только подтверждёнными данными.", missing_note="", final_question="Передать вопрос оператору?"),
        brief,
    )

    def composer(_brief, *, repair_errors=(), model=None):
        return {
            "intro": "По текущей карточке отвечаю подтверждёнными данными.",
            "options": [],
            "missing_note": "Условия ипотеки не подтверждены.",
            "final_question": "Что ещё проверить?",
        }

    result = compose_response_sync(brief, fallback_text="fallback", composer=composer)
    assert result.status == "primary"
    assert "ипотек" not in result.text.casefold()


def test_requested_current_missing_fact_preserves_operator_consent_contract() -> None:
    card = OptionCard(name="Лучи", price_min=12_000_000)
    plan = SemanticPlan(operation="answer_open_question", requested_facts=("mortgage_terms",), facts_needed=("mortgage_terms",), resolved_subject="ипотека")
    brief = build_response_brief(
        stage=Stage.CURRENT_OPTIONS,
        plan=plan,
        execution=ExecutionResult(ok=True, selected=card),
        delta=StateDelta(),
        state=ConversationState(selected_option_name="Лучи", visible_options=(card,)),
        response_plan=ResponsePlan(acknowledgement="Смотрю по текущему варианту.", cards=(card,), viewpoint="financing", final_question="Передать вопрос оператору?"),
    )

    assert brief.missing_facts == ("mortgage_terms",)
    assert brief.canonical_missing_summary == ("mortgage_terms",)
    assert brief.response_policy == "operator_consent_offer"
    assert "Передать оператору запрос?" in brief.cta_template
    assert "телефон" not in brief.cta_template.casefold()
    assert "номер" not in brief.cta_template.casefold()
    errors = validate_composed_response(
        ComposedResponse(intro="По текущей карточке не вижу подтверждённых условий ипотеки.", missing_note="", final_question="Передать оператору запрос?"),
        brief,
    )
    assert "missing_note_required" in errors
    assert "operator_handoff_template_mismatch" in errors


def test_requested_current_available_fact_does_not_require_missing_note() -> None:
    card = OptionCard(name="Лучи", price_min=12_000_000, mortgage_terms="ипотека от застройщика")
    plan = SemanticPlan(operation="answer_open_question", requested_facts=("mortgage_terms",), facts_needed=("mortgage_terms",), resolved_subject="ипотека")
    brief = build_response_brief(
        stage=Stage.CURRENT_OPTIONS,
        plan=plan,
        execution=ExecutionResult(ok=True, selected=card),
        delta=StateDelta(),
        state=ConversationState(selected_option_name="Лучи", visible_options=(card,)),
        response_plan=ResponsePlan(acknowledgement="Смотрю по текущему варианту.", cards=(card,), viewpoint="financing", final_question="Передать вопрос оператору?"),
    )

    assert brief.available_facts == ("mortgage_terms",)
    assert brief.missing_facts == ()
    assert brief.canonical_missing_summary == ()
    assert brief.response_policy == ""
    assert brief.operator_handoff_template == ""
    assert "missing_note_required" not in validate_composed_response(
        ComposedResponse(intro="По текущей карточке ипотека от застройщика подтверждена.", missing_note="", final_question="Передать вопрос оператору?"),
        brief,
    )


def test_generic_missing_reason_without_explicit_requested_field_never_surfaces() -> None:
    search = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}], "missing": ["provider_unavailable"]})
    response_plan = build_response_plan(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="life"),
        execution=ExecutionResult(ok=True, search=search),
        delta=StateDelta(),
        state=ConversationState(),
    )
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="life"),
        execution=ExecutionResult(ok=True, search=search),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=response_plan,
    )

    payload = to_jsonable(brief)
    assert payload["canonical_missing_summary"] == []
    assert payload["missing_facts"] == []
    assert "provider_unavailable" not in str(payload)


def test_composer_validator_rejects_unknown_name_number_and_internal_terms():
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="investment"),
        execution=ExecutionResult(ok=True, search=SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла.", cards=(OptionCard(name="Лучи", price_min=12_000_000),), final_question="Какой смотрим?"),
    )
    bad = ComposedResponse(
        intro="По JSON facts[] нашла вариант",
        options=(ComposedOption(name="Секрет", facts="ЖК «Секрет» за 99 млн", description="Доходность 15%"),),
        final_question="Какой смотрим?",
    )

    errors = validate_composed_response(bad, brief)

    assert "option_name_not_allowed" in errors
    assert "unknown_number_or_sensitive_claim" in errors
    assert "internal_or_raw_wire_leak" in errors
    assert "unsupported_sensitive_claim" in errors


@pytest.mark.parametrize(
    "phrase",
    [
        "безопасный контекст",
        "без карточек и личных данных",
        "подтверждённые данные",
        "подтверждённые детали",
        "не буду считать это согласием",
        "текущий контекст",
        "сохранённые данные",
        "сохранённых фактах",
    ],
)
def test_composer_validator_bans_technical_customer_phrasing(phrase):
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="life"),
        execution=ExecutionResult(ok=True, search=SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла вариант.", cards=(OptionCard(name="Лучи", price_min=12_000_000),), final_question="Какой смотрим?"),
    )
    composed = ComposedResponse(
        intro=f"Нашла вариант, {phrase} не показываю клиенту.",
        options=(ComposedOption(name="Лучи", facts="Цена от 12 млн рублей.", description="Цена помогает сравнить вариант с бюджетом."),),
        missing_note="",
        final_question="Какой смотрим?",
    )

    assert "internal_or_raw_wire_leak" in validate_composed_response(composed, brief)


def test_deterministic_v2_fallback_uses_human_phrasing_and_keeps_cta_contracts():
    banned = (
        "безопасный контекст",
        "без карточек",
        "личных данных",
        "подтверждённые данные",
        "подтверждённые детали",
        "не буду считать это согласием",
        "текущий контекст",
        "сохранённые данные",
        "сохранённых данных",
        "сохранённых фактах",
        "Учла изменение",
    )
    scenarios = []
    scenarios.append(render_response(build_response_plan(stage=Stage.FIRST_LIST, plan=SemanticPlan(operation="search"), execution=ExecutionResult(ok=False), delta=StateDelta(), state=ConversationState())))
    scenarios.append(render_response(build_response_plan(stage=Stage.REFINEMENT, plan=SemanticPlan(operation="refine_search"), execution=ExecutionResult(ok=False), delta=StateDelta(), state=ConversationState(params={"location": "Сокол"}, visible_options=(OptionCard(name="Сокол"),)))))
    scenarios.append(render_response(build_response_plan(stage=Stage.OFF_TOPIC, plan=SemanticPlan(operation="off_topic"), execution=ExecutionResult(ok=True), delta=StateDelta(), state=ConversationState())))
    scenarios.append(render_response(build_response_plan(stage=Stage.SELECTED_OBJECT, plan=SemanticPlan(operation="select_option", selected_option_name="Лучи"), execution=ExecutionResult(ok=True, selected=OptionCard(name="Лучи", ready="сдан", price_min=12_000_000)), delta=StateDelta(), state=ConversationState())))
    finance_text = render_response(build_response_plan(stage=Stage.FINANCING_CLARIFICATION, plan=SemanticPlan(operation="financing", followup_outcome="unexpected"), execution=ExecutionResult(ok=True), delta=StateDelta(), state=ConversationState(selected_option_name="Лучи")))
    live_fact_text = render_response(build_response_plan(stage=Stage.SELECTED_LIVE_FACT_CLARIFICATION, plan=SemanticPlan(operation="select_option", requested_facts=("parking_price",)), execution=ExecutionResult(ok=True), delta=StateDelta(), state=ConversationState(selected_option_name="Лучи")))
    scenarios.extend([finance_text, live_fact_text])

    joined = "\n".join(scenarios).casefold()
    for phrase in banned:
        assert phrase.casefold() not in joined
    assert "Сейчас не могу надёжно проверить нужную информацию" in scenarios[0]
    assert scenarios[0].endswith("Передать оператору запрос?")
    assert "Сейчас не могу надёжно проверить нужную информацию" in scenarios[1]
    assert scenarios[1].endswith("Передать оператору запрос?")
    assert "С этим я не подскажу" in scenarios[2]
    assert "Проверить условия по этому ЖК?" in finance_text
    assert "Проверить точную актуальность по этому ЖК?" in live_fact_text


def test_composer_validator_requires_exactly_one_final_question_at_end():
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search"),
        execution=ExecutionResult(ok=True, search=SearchResult.from_dict({"facts": [{"name": "Лучи"}]})),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла.", cards=(OptionCard(name="Лучи"),), final_question="Какой смотрим?"),
    )

    errors = validate_composed_response(
        ComposedResponse(intro="ЖК «Лучи» подходит. Какой бюджет?", options=(), final_question="Какой район?"),
        brief,
    )

    assert "question_count_not_one" in errors


def test_composer_validator_rejects_question_marks_outside_final_question():
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search"),
        execution=ExecutionResult(ok=True, search=SearchResult.from_dict({"facts": [{"name": "Лучи"}]})),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла.", cards=(OptionCard(name="Лучи"),), final_question="Какой смотрим?"),
    )
    composed = ComposedResponse(
        intro="Нашла вариант?",
        options=(ComposedOption(name="Лучи", facts="Карточка подтверждена.", description="Это можно сравнить."),),
        final_question="Какой смотрим?",
    )

    errors = validate_composed_response(composed, brief)

    assert "section_question_mark" in errors
    assert "question_count_not_one" in errors


def test_response_composer_payload_has_conversation_stage_and_strict_prompt_contract(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search"),
        execution=ExecutionResult(ok=True, search=SearchResult.from_dict({"facts": [{"name": "Лучи"}]})),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла.", cards=(OptionCard(name="Лучи"),), final_question="Какой смотрим?"),
    )
    payload = request_payload(brief, prompt="system", model="chat-model")

    assert payload["_payload_stage"] == "conversation_answer"
    assert payload["model"] == "chat-model"
    assert payload["external_api_key"] == "test-openrouter-key"
    assert "test-openrouter-key" not in payload["query"]
    assert '"canonical_missing_summary": []' in payload["query"]
    assert "V2_RESPONSE_BRIEF=" in payload["query"]
    assert "strict" not in payload["query"].casefold()
    assert "response_format" not in payload
    assert "provider" not in payload
    assert "response_format" not in payload["parameters"]
    assert "provider" not in payload["parameters"]
    assert payload["parameters"] == {"temperature": 0.25, "max_tokens": 1800}


def test_composer_parse_accepts_already_parsed_structured_response():
    composed, errors = parse_composer_json({"intro": "Нашла вариант.", "options": [{"name": "Лучи", "facts": "Цена от 12 млн рублей.", "description": "Это понятная точка для сравнения."}], "missing_note": "", "final_question": "Какой смотрим?"})

    assert errors == []
    assert composed and composed.options[0].name == "Лучи"


def test_one_shot_composer_drops_unrequested_missing_note():
    brief = ResponseBrief(
        answer_goal="present_search_results",
        canonical_cards=(OptionCard(name="Лучи"),),
        canonical_missing_summary=("infrastructure",),
        recipe_cards=({"card_name": "Лучи"},),
        fallback_question="Какой вариант смотрим?",
    )

    async def composer(*_args, **_kwargs):
        return {
            "intro": "Нашла вариант.",
            "options": [{"name": "Лучи", "facts": "Проект в Москве.", "description": "Подходит для сравнения."}],
            "recommendation": "",
            "missing_note": "Не нашла инфраструктуру.",
            "final_question": "Какой вариант смотрим?",
        }

    result = asyncio.run(compose_response_one_shot_async(brief, fallback_text="fallback", composer=composer))

    assert result.status == "primary"
    assert "Не нашла инфраструктуру" not in result.text


def test_one_shot_composer_retries_same_prompt_before_fallback():
    brief = ResponseBrief(
        answer_goal="present_search_results",
        canonical_cards=(OptionCard(name="Лучи"),),
        recipe_cards=({"card_name": "Лучи"},),
        fallback_question="Какой вариант смотрим?",
    )
    calls = []

    async def composer(*_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return "not json", {"ok": True}
        return {
            "intro": "Нашла вариант.",
            "options": [{"name": "Лучи", "facts": "Проект в Москве.", "description": "Подходит для сравнения."}],
            "recommendation": "Лучи — первый вариант для сравнения.",
            "missing_note": "",
            "final_question": "Какой вариант смотрим?",
        }

    result = asyncio.run(compose_response_one_shot_async(brief, fallback_text="fallback", composer=composer))

    assert result.status == "provider_retry"
    assert result.attempts == 2
    assert [item["attempt_kind"] for item in result.attempt_summaries] == ["primary", "same_prompt_retry"]
    assert calls == [{"repair_errors": ()}, {"repair_errors": ()}]
    assert result.text != "fallback"


def test_recommend_current_requires_separate_recommendation_field():
    brief = ResponseBrief(answer_goal="recommend_current")
    composed = ComposedResponse(
        intro="Нашла подходящие варианты.",
        final_question="Какой показать подробнее?",
    )

    assert "recommendation_required" in validate_composed_response(composed, brief)


def test_composer_prompt_keeps_base_card_facts_separate_from_scenario_anchor():
    prompt = load_prompt()

    assert "используй только значения из `base_facts` этой карточки" in prompt
    assert "для shortlist" in prompt
    assert "выбери из них только самые важные для сравнения" in prompt
    assert "не больше 3–4 фактов" in prompt
    assert "не добавляй другие поля из" in prompt
    assert "`anchor_fact` задаёт главный акцент" in prompt
    assert "Не повторяй один факт в `facts` и `description`" in prompt
    assert "Не придумывай собственные правила сценария" in prompt
    assert "для family сначала" not in prompt


def test_composer_prompt_forbids_investment_demand_inferences_from_proxy_facts():
    prompt = load_prompt()

    assert "`ads_count` — только число объявлений на витрине" in prompt
    assert "не спрос, не продажи" in prompt
    assert "доходность, окупаемость, спрос" in prompt


def test_composer_prompt_requires_all_canonical_cards_and_exact_identity_facts():
    prompt = load_prompt()

    assert "все переданные карточки" in prompt
    assert "без пропусков и перестановок" in prompt
    assert "`options[].name` копируй дословно" in prompt
    assert "`answer_goal` не равен `answer_open_question`" in prompt
    assert "обычно верни `options=[]`" in prompt
    assert "`response_policy=operator_consent_offer`" in prompt
    assert "не отправляй" in prompt
    assert "точный `operator_handoff_template`" in prompt


def test_composer_validator_accepts_generic_jk_phrase_and_canonical_en_dash_range():
    card = OptionCard(name="Зелёный парк", location="Крюково", price_min=7_827_900, area="19,6–75,2 м²", ads_count=4792)
    brief = ResponseBrief(answer_goal="present_search_results", response_viewpoint="investment", canonical_cards=(card,))
    composed = ComposedResponse(
        intro="Нашла инвестиционный вариант.",
        options=(ComposedOption(
            name="Зелёный парк",
            facts="Крюково, цена от 7 827 900 рублей, площадь 19,6–75,2 м², на витрине 4792 объявления.",
            description="Сейчас по этому ЖК есть 4792 объявления — это буквальный счётчик текущей витрины.",
        ),),
        final_question="Рассмотреть вариант подробнее?",
    )

    errors = validate_composed_response(composed, brief)
    assert "unknown_option_name" not in errors
    assert "unknown_number_or_sensitive_claim" not in errors


def test_composer_rejects_old_flat_response_schema():
    composed, errors = parse_composer_json({"response": "ЖК «Лучи» подходит. Какой смотрим?", "option_names": ["Лучи"], "final_question": "Какой смотрим?"})

    assert composed is None
    assert errors == ["schema_required_field_missing"]


def test_composer_parser_tolerates_extra_and_incomplete_option_fields_for_advisory_validation():
    composed, errors = parse_composer_json({
        "intro": "Нашла варианты.",
        "options": [
            {"name": "Первый", "facts": "Москва.", "description": "", "extra_model_note": "ignored"},
            {"name": "Второй"},
            {"name": "Третий"},
            {"name": "Четвёртый"},
        ],
        "recommendation": "",
        "missing_note": "",
        "final_question": "Что посмотреть подробнее?",
        "extra_root_field": "ignored",
    })

    assert errors == []
    assert composed is not None
    assert len(composed.options) == 4
    assert composed.options[1].facts == ""


def test_composer_assembled_layout_is_deterministic():
    composed = ComposedResponse(
        intro="Да, нашла два варианта под семейный сценарий.",
        options=(
            ComposedOption(name="Семейный квартал", facts="Котельники, цены от 11,9 млн рублей, рядом школа.", description="Школа рядом упрощает ежедневные маршруты семьи."),
            ComposedOption(name="Белая Дача парк", facts="Котельники, цены от 12,6 млн рублей, рядом парк.", description="Парк добавляет понятный сценарий прогулок."),
        ),
        missing_note="Не хватает подтверждения по условиям оплаты.",
        final_question="Какой вариант хотите рассмотреть подробнее?",
    )

    assert assemble_composed_response(composed) == (
        "Да, нашла два варианта под семейный сценарий.\n\n"
        "1. Семейный квартал\n"
        "Котельники, цены от 11,9 млн рублей, рядом школа.\n"
        "Школа рядом упрощает ежедневные маршруты семьи.\n\n"
        "2. Белая Дача парк\n"
        "Котельники, цены от 12,6 млн рублей, рядом парк.\n"
        "Парк добавляет понятный сценарий прогулок.\n\n"
        "Не хватает подтверждения по условиям оплаты.\n\n"
        "Какой вариант хотите рассмотреть подробнее?"
    )


def test_composer_empty_response_is_not_invalid_json():
    composed, errors = parse_composer_json("")

    assert composed is None
    assert errors == ["empty_response"]


def test_composer_gateway_null_sentinels_are_empty_responses():
    for raw in ("None", "null", " NULL "):
        composed, errors = parse_composer_json(raw)
        assert composed is None
        assert errors == ["empty_response"]


def test_composer_accepts_one_clean_json_code_fence_only():
    raw = '''```json
{"intro":"Нашла вариант.","options":[{"name":"Лучи","facts":"Цена от 12 млн рублей.","description":"Это понятная точка для сравнения."}],"missing_note":"","final_question":"Какой смотрим?"}
```'''
    composed, errors = parse_composer_json(raw)
    assert errors == []
    assert composed and composed.options[0].name == "Лучи"


def test_composer_rejects_prose_around_json_code_fence():
    raw = '''Вот ответ:
```json
{"intro":"Нашла вариант.","options":[],"missing_note":"","final_question":"Какой смотрим?"}
```'''
    composed, errors = parse_composer_json(raw)
    assert composed is None
    assert errors and errors[0].startswith("invalid_json:")


def test_composer_rejects_ungrounded_marketing_from_ads_count():
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="life"),
        execution=ExecutionResult(ok=True, search=SearchResult.from_dict({"facts": [{"name": "Лучи", "ads_count": 12}]})),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла вариант.", cards=(OptionCard(name="Лучи", ads_count=12),), final_question="Какой смотрим?"),
    )
    composed = ComposedResponse(
        intro="Нашла вариант.",
        options=(ComposedOption(name="Лучи", facts="На витрине 12 объявлений.", description="Широкий выбор позволит вам найти идеальную квартиру."),),
        missing_note="",
        final_question="Рассказать подробнее?",
    )
    assert any(error.startswith("unsupported_marketing_claim:") for error in validate_composed_response(composed, brief))


def test_life_response_brief_hides_investment_counters_from_composer() -> None:
    card = OptionCard(
        name="Лучи",
        location="Москва",
        price_min=12_000_000,
        metro="Солнцево",
        ads_count=12,
        sales_count=3,
        sales_date="2026-07-01",
        discount="скидка 2%",
    )
    search = SearchResult(facts=(card,))
    execution = ExecutionResult(ok=True, search=search)
    plan = SemanticPlan(operation="search", intent="life")
    response_plan = build_response_plan(
        stage=Stage.FIRST_LIST,
        plan=plan,
        execution=execution,
        delta=StateDelta(),
        state=ConversationState(),
    )
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=plan,
        execution=execution,
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=response_plan,
    )

    projected = brief.canonical_cards[0]
    assert projected.metro == "Солнцево"
    assert projected.ads_count is None
    assert projected.sales_count is None
    assert projected.sales_date is None
    assert projected.discount is None


def test_composer_rejects_ready_legal_promises_and_ads_choice_inference():
    card = OptionCard(name="Лучи", ready="сдан", ads_count=12, location="Солнцево", price="от 12 млн рублей")
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search", intent="life"),
        execution=ExecutionResult(ok=True, search=SearchResult(facts=(card,))),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла вариант.", cards=(card,), final_question="Рассказать подробнее?"),
    )
    composed = ComposedResponse(
        intro="Нашла вариант.",
        options=(ComposedOption(name="Лучи", facts="Солнцево, цена от 12 млн рублей, дом сдан, 12 объявлений.", description="Можно сразу оформить собственность, риски исключены, а объявления показывают, что есть выбор."),),
        missing_note="",
        final_question="Рассказать подробнее?",
    )

    assert any(error.startswith("unsupported_marketing_claim:") for error in validate_composed_response(composed, brief))


def test_composer_rejects_rental_income_and_tenant_demand_without_explicit_fields():
    card = OptionCard(name="Лучи", ready="сдан", ads_count=12, location="Солнцево", price="от 12 млн рублей")
    brief = ResponseBrief(
        answer_goal="present_search_results",
        response_viewpoint="investment",
        canonical_cards=(card,),
        fallback_question="Какой вариант смотрим?",
    )
    composed = ComposedResponse(
        intro="Нашла вариант.",
        options=(ComposedOption(name="Лучи", facts="Солнцево, цена от 12 млн рублей, дом сдан.", description="Вариант привлекателен для арендаторов и позволит быстро получать доход от аренды."),),
        missing_note="",
        final_question="Рассказать подробнее?",
    )

    errors = validate_composed_response(composed, brief)
    assert "unsupported_sensitive_claim" in errors
    assert any(error.startswith("unsupported_marketing_claim:rental_or_income_inference") for error in errors)


def test_financing_over_life_rejects_rental_and_income_viewpoint_drift() -> None:
    card = OptionCard(name="Саларьево парк", location="Коммунарка", price_min=11_800_000, ready="сдан", metro="Саларьево — 3 минуты пешком")
    brief = ResponseBrief(
        answer_goal="present_search_results",
        response_viewpoint="financing",
        base_viewpoint="life",
        canonical_cards=(card,),
        canonical_missing_summary=("finance",),
        fallback_question="Какой вариант смотрим?",
    )
    composed = ComposedResponse(
        intro="Нашла вариант.",
        options=(ComposedOption(
            name="Саларьево парк",
            facts="Коммунарка, цена от 11,8 млн рублей, дом сдан, метро Саларьево — 3 минуты пешком.",
            description="Близость к метро повысит привлекательность для арендаторов, а сданный дом сократит срок до получения дохода.",
        ),),
        missing_note="Условия финансирования пока не подтверждены.",
        final_question="Рассказать подробнее?",
    )

    errors = validate_composed_response(composed, brief)
    assert "scenario_viewpoint_mismatch" in errors
    assert "unsupported_sensitive_claim" in errors


def test_investment_description_must_link_to_supported_investment_fact() -> None:
    card = OptionCard(name="Лучи", location="Солнцево", metro="Солнцево", price_min=12_000_000)
    brief = ResponseBrief(
        answer_goal="present_search_results",
        response_viewpoint="investment",
        canonical_cards=(card,),
        fallback_question="Какой вариант смотрим?",
    )
    generic = ComposedResponse(
        intro="Нашла вариант.",
        options=(ComposedOption(name="Лучи", facts="Солнцево, цена от 12 млн рублей.", description="Метро рядом удобно для поездок по городу."),),
        missing_note="",
        final_question="Рассказать подробнее?",
    )
    grounded = ComposedResponse(
        intro="Нашла вариант.",
        options=(ComposedOption(name="Лучи", facts="Солнцево, цена от 12 млн рублей.", description="Цена задаёт понятный порог входа для сравнения с бюджетом."),),
        missing_note="",
        final_question="Рассказать подробнее?",
    )

    assert "scenario_fact_benefit_missing" in validate_composed_response(generic, brief)
    assert "scenario_fact_benefit_missing" not in validate_composed_response(grounded, brief)


def test_repair_payload_explains_semantic_validator_codes_without_raw_output():
    brief = ResponseBrief(
        answer_goal="compare",
        response_viewpoint="investment",
        canonical_cards=(OptionCard(name="Лучи", ready="сдан", price_min=12_000_000),),
        fallback_question="Какой вариант смотрим?",
    )

    payload = request_payload(brief, repair_errors=("unsupported_marketing_claim:immediate_move", "unsupported_sensitive_claim"))
    query = payload["query"]

    assert "repair_instructions" in query
    assert "готовность означает только отсутствие ожидания" in query
    assert "спросе, доходности, ликвидности" in query


def test_composer_does_not_require_missing_note_for_broad_financing_search_gap():
    card = OptionCard(name="Лучи", location="Солнцево", price="от 12 млн рублей")
    brief = ResponseBrief(
        answer_goal="ипотека для семьи",
        response_viewpoint="financing",
        base_viewpoint="family",
        canonical_cards=(card,),
        canonical_missing_summary=("финансовых условий", "семейной инфраструктуры"),
        fallback_question="Рассказать подробнее?",
    )
    composed = ComposedResponse(
        intro="Нашла вариант.",
        options=(ComposedOption(name="Лучи", facts="Солнцево, цена от 12 млн рублей.", description="Это помогает сравнить бюджет покупки."),),
        missing_note="",
        final_question="Рассказать подробнее?",
    )

    errors = validate_composed_response(composed, brief)
    assert "missing_note_required" not in errors
    assert "financing_missing_note_required" not in errors


def test_composer_requires_grounded_missing_note_for_explicit_financing_gap():
    card = OptionCard(name="Лучи", location="Солнцево", price="от 12 млн рублей")
    brief = ResponseBrief(
        answer_goal="answer_open_question",
        response_viewpoint="financing",
        base_viewpoint="family",
        requested_facts=("mortgage_terms",),
        missing_facts=("mortgage_terms",),
        response_policy="operator_consent_offer",
        operator_handoff_template="Точный ответ уточнит оператор.",
        canonical_cards=(card,),
        canonical_missing_summary=("mortgage_terms",),
        fallback_question="Передать оператору запрос?",
    )
    composed = ComposedResponse(
        intro="Нашла вариант.",
        options=(ComposedOption(name="Лучи", facts="Солнцево, цена от 12 млн рублей.", description="Это помогает сравнить бюджет покупки."),),
        missing_note="",
        final_question="Передать оператору запрос?",
    )

    errors = validate_composed_response(composed, brief)
    assert "missing_note_required" in errors
    assert "financing_missing_note_required" in errors


def test_selected_financing_brief_is_scoped_and_requires_consent_cta():
    selected = OptionCard(name="Бусиновский парк", location="Западное Дегунино", price="от 12,4 млн рублей")
    other = OptionCard(name="Мичуринский парк", location="Очаково-Матвеевское", price="от 14,3 млн рублей")
    state = ConversationState(visible_options=(selected, other), selected_option_name=selected.name)
    brief = build_response_brief(
        stage=Stage.FINANCING_CLARIFICATION,
        plan=SemanticPlan(operation="financing", intent="mortgage", selected_option_name=selected.name, scope="one"),
        execution=ExecutionResult(ok=True),
        delta=StateDelta(pending_followup="financing_consent"),
        state=state,
        response_plan=ResponsePlan(
            acknowledgement="По ЖК условия проверяются предметно.",
            final_question="Проверить условия по этому ЖК?",
            viewpoint="financing",
            base_viewpoint="life",
            recipe_id="selected_financing",
            cta_template="Проверить условия по этому ЖК?",
        ),
    )

    assert brief.recipe_id == "selected_financing"
    assert [card.name for card in brief.canonical_cards] == [selected.name]
    assert brief.cta_template == "Проверить условия по этому ЖК?"
    composed = ComposedResponse(
        intro="По Бусиновскому парку условия лучше смотреть предметно.",
        options=(),
        missing_note="Точные условия зависят от программы банка или застройщика и конкретной квартиры.",
        final_question="Как к вам обращаться?",
    )
    errors = validate_composed_response(composed, brief)
    assert "recipe_cta_mismatch" in errors
    assert "contact_before_financing_consent" in errors


def test_current_options_financing_brief_uses_all_options_consent_cta():
    cards = (OptionCard(name="Лучи"), OptionCard(name="Саларьево парк"))
    brief = build_response_brief(
        stage=Stage.FINANCING_CLARIFICATION,
        plan=SemanticPlan(operation="financing", query_text="все проверь", intent="mortgage", scope="all"),
        execution=ExecutionResult(ok=True),
        delta=StateDelta(pending_followup="financing_consent"),
        state=ConversationState(visible_options=cards),
        response_plan=ResponsePlan(
            acknowledgement="Условия смотрим отдельно.",
            final_question="Проверить условия по всем этим ЖК?",
            viewpoint="financing",
            recipe_id="current_options_financing",
            cta_template="Проверить условия по всем этим ЖК?",
        ),
    )

    assert brief.recipe_id == "current_options_financing"
    assert brief.cta_template == "Проверить условия по всем этим ЖК?"


def test_current_options_financing_selection_composer_merges_missing_note_into_intro_without_tail_duplicate():
    brief = ResponseBrief(
        answer_goal="answer_financing_without_inventing_terms",
        response_viewpoint="financing",
        acknowledgement="По ипотеке точных условий пока нет.",
        canonical_cards=(OptionCard(name="Лучи", price="от 12 млн"), OptionCard(name="Саларьево парк", price="от 13 млн")),
        canonical_missing_summary=("ипотека/условия оплаты не подтверждены",),
        recipe_id="current_options_financing",
        cta_template="По какому ЖК проверить условия ипотеки?",
    )
    composed = ComposedResponse(
        intro="Сначала честно: по ипотеке точных условий в карточках нет.",
        options=(
            ComposedOption(name="Лучи", facts="Цена от 12 млн.", description="Можно проверить условия предметно."),
            ComposedOption(name="Саларьево парк", facts="Цена от 13 млн.", description="Можно проверить условия предметно."),
        ),
        missing_note="Ипотека и условия оплаты не подтверждены: они зависят от банка, застройщика и конкретной квартиры.",
        final_question="По какому ЖК проверить условия ипотеки?",
    )

    text = assemble_composed_response(composed, brief)
    paragraphs = text.split("\n\n")

    assert "не подтверждены" in paragraphs[0]
    assert paragraphs[-1] == "По какому ЖК проверить условия ипотеки?"
    assert not any("не подтверждены" in paragraph for paragraph in paragraphs[1:-1])


def test_composer_orchestration_primary_success_and_no_duplicate_calls():
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search"),
        execution=ExecutionResult(ok=True, search=SearchResult.from_dict({"facts": [{"name": "Лучи"}]})),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла.", cards=(OptionCard(name="Лучи"),), final_question="Какой смотрим?"),
    )
    calls = []

    def composer(_brief, *, repair_errors=(), model=None):
        calls.append((repair_errors, model))
        return {"intro": "Нашла вариант.", "options": [{"name": "Лучи", "facts": "Подтверждённая карточка.", "description": "Это можно сравнить с другими вариантами."}], "missing_note": "", "final_question": "Какой смотрим?"}

    result = compose_response_sync(brief, fallback_text="fallback", composer=composer)

    assert result.status == "primary"
    assert len(calls) == 1
    assert result.text.startswith("Нашла вариант.")
    assert result.attempt_summaries[0]["raw_type"] == "mapping"


def test_composer_semantic_malformed_falls_back_after_primary():
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search"),
        execution=ExecutionResult(ok=True, search=SearchResult.from_dict({"facts": [{"name": "Лучи"}]})),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла.", cards=(OptionCard(name="Лучи"),), final_question="Какой смотрим?"),
    )
    calls = []

    def composer(_brief, *, repair_errors=(), model=None):
        calls.append((repair_errors, model))
        if repair_errors:
            return {"intro": "Нашла вариант.", "options": [{"name": "Лучи", "facts": "Подтверждённая карточка.", "description": "Это можно сравнить с другими вариантами."}], "missing_note": "", "final_question": "Какой смотрим?"}
        return "не json"

    result = compose_response_sync(brief, fallback_text="fallback", composer=composer)

    assert result.status == "repaired"
    assert "Лучи" in result.text
    assert result.attempts == 2
    assert calls[0] == ((), "google/gemini-2.5-flash")
    assert calls[1][1] == "google/gemini-2.5-flash"
    assert calls[1][0][0].startswith("invalid_json:")
    assert [item["attempt_kind"] for item in result.attempt_summaries] == ["primary", "repair"]


def test_composer_attempt_summary_keeps_shape_and_gateway_task_id_without_raw_text():
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search"),
        execution=ExecutionResult(ok=True, search=SearchResult.from_dict({"facts": [{"name": "Лучи"}]})),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла.", cards=(OptionCard(name="Лучи"),), final_question="Какой смотрим?"),
    )
    raw = '  {"intro":"x","options":[]'

    def composer(_brief, *, repair_errors=(), model=None):
        return raw, {"_gateway_task_id": "task-2386206/unsafe suffix", "query": "secret query", "response": raw}

    result = compose_response_sync(brief, fallback_text="fallback", composer=composer)

    summary = result.to_meta()["attempt_summaries"][0]
    assert summary["raw_type"] == "string"
    assert summary["raw_length"] == len(raw)
    assert summary["starts_object"] is True
    assert summary["starts_fence"] is False
    assert summary["ends_object"] is False
    assert summary["gateway_task_id"].startswith("task-2386206")
    dumped = json.dumps(summary, ensure_ascii=False)
    assert raw not in dumped
    assert "secret query" not in dumped


def test_composer_semantic_validation_is_advisory_and_does_not_replace_response():
    brief = ResponseBrief(
        answer_goal="present_search_results",
        canonical_cards=(OptionCard(name="Разрешённый ЖК", location="Москва", price_min=12_000_000),),
    )

    def composer(_brief, *, repair_errors=(), model=None):
        return {
            "intro": "Нашла вариант.",
            "options": [{"name": "Другой ЖК", "facts": "Без обязательной цены.", "description": "Свободное описание."}],
            "recommendation": "",
            "missing_note": "",
            "final_question": "Посмотреть подробнее?",
        }

    result = compose_response_sync(brief, fallback_text="fallback", composer=composer)

    assert result.status == "primary"
    assert result.text != "fallback"
    assert result.errors == ()
    assert "option_name_not_allowed" in result.warnings
    assert result.to_meta()["warnings"] == list(result.warnings)
    assert result.attempt_summaries[0]["validation_warnings"] == list(result.warnings)


def test_composer_empty_response_falls_back_without_provider_retry():
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search"),
        execution=ExecutionResult(ok=True, search=SearchResult.from_dict({"facts": [{"name": "Лучи"}]})),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла.", cards=(OptionCard(name="Лучи"),), final_question="Какой смотрим?"),
    )
    calls = []

    def composer(_brief, *, repair_errors=(), model=None):
        calls.append((repair_errors, model))
        if len(calls) == 1:
            return ""
        return {"intro": "Нашла вариант.", "options": [{"name": "Лучи", "facts": "Подтверждённая карточка.", "description": "Это можно сравнить с другими вариантами."}], "missing_note": "", "final_question": "Какой смотрим?"}

    result = compose_response_sync(brief, fallback_text="fallback", composer=composer, provider_retry_model="retry-model")

    assert result.status == "fallback"
    assert result.text == "fallback"
    assert result.attempts == 1
    assert calls == [((), "google/gemini-2.5-flash")]
    assert [item["attempt_kind"] for item in result.attempt_summaries] == ["primary"]


def test_composer_provider_schema_error_falls_back_after_primary_fail():
    brief = build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=SemanticPlan(operation="search"),
        execution=ExecutionResult(ok=True, search=SearchResult.from_dict({"facts": [{"name": "Лучи"}]})),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла.", cards=(OptionCard(name="Лучи"),), final_question="Какой смотрим?"),
    )
    calls = []

    def composer(_brief, *, repair_errors=(), model=None):
        calls.append((repair_errors, model))
        return "", {"_upstream_error": True, "_provider_error_code": "provider_invalid_argument"}

    result = compose_response_sync(brief, fallback_text="fallback", composer=composer, provider_retry_model="retry-model")

    assert result.status == "fallback"
    assert result.text == "fallback"
    assert result.error_category == "provider"
    assert result.error_code == "provider_invalid_argument"
    assert result.attempts == 1
    assert calls == [((), "google/gemini-2.5-flash")]
    assert [item["attempt_kind"] for item in result.to_meta()["attempt_summaries"]] == ["primary"]
    assert "raw text" not in str(result.to_meta()).lower()
