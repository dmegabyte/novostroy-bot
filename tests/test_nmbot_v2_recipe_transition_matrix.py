import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nmbot_v2.card_normalizer import normalize_card
from nmbot_v2.contracts import ExecutionResult, OptionCard, ResponsePlan, SafeTurnContext, SearchResult, SemanticPlan, Stage, StateDelta, TurnAction, to_jsonable
from nmbot_v2.fact_context import present_fact_names, split_requested_facts
from nmbot_v2.response import build_final_response_plan, build_response_plan, render_response
from nmbot_v2.response_composer import build_response_brief
from nmbot_v2.runtime import TurnProcessor
from nmbot_v2.scenario_recipes import CONTACT_NAME_FOLLOWUP, RECIPES, REPLY_CONTRACTS, SELECTED_LIVE_FACT_CONSENT_FOLLOWUP, resolve_recipe, transition_for_reply
from nmbot_v2.search_contract import available_fact_fields
from nmbot_v2.state import ConversationState
from nmbot_v2.transition import derive_transition


class Planner:
    def __init__(self, plan):
        self.plan_value = plan

    def plan(self, context, state):
        return self.plan_value


class SearchService:
    def __init__(self, selected=None, fresh=()):
        self.selected = selected
        self.last_fresh_facts = tuple(fresh)
        self.calls = 0
        self.selected_calls = 0

    def search(self, plan, state):
        self.calls += 1
        return SearchResult.from_dict({"facts": [{"name": "Новый", "price_min": 10_000_000}]})

    def enrich_selected(self, option, state, plan):
        self.selected_calls += 1
        return self.selected or option


def ctx(text="текст"):
    return SafeTurnContext(conversation_ref="local", user_text=text)


def test_registry_references_outcomes_and_unknown_combinations_fail_closed() -> None:
    assert "default_clarification" in RECIPES
    assert "off_topic" in RECIPES
    for contract in REPLY_CONTRACTS.values():
        for outcome in contract.allowed_outcomes:
            transition = transition_for_reply(contract.id, outcome)
            assert transition is not None
            assert transition.response_recipe_id in RECIPES

    resolved = resolve_recipe(stage=Stage.ERROR, action=TurnAction.SAFE_ERROR, plan=SemanticPlan(operation="bad"), state=ConversationState())
    assert resolved.recipe.id == "default_clarification"
    resume = transition_for_reply(CONTACT_NAME_FOLLOWUP, "resume_contact")
    assert resume is not None and resume.action == TurnAction.OFFER_OPERATOR


def test_all_shortlist_viewpoints_select_distinct_valid_anchors() -> None:
    cards = (
        OptionCard(name="Универсальный", metro="Сокол", infrastructure=("школа",), ready="сдан", room_formats=("1",), price_min=10_000_000, ads_count=7, sales_count=2),
    )
    expected = {
        "life": "metro",
        "family": "schools",
        "investment": "apartment_price",
        "rental": "room_formats",
    }
    for viewpoint, anchor in expected.items():
        resolved = resolve_recipe(stage=Stage.FIRST_LIST, plan=SemanticPlan(operation="search", intent=viewpoint), cards=cards)
        assert resolved.recipe.id == f"{viewpoint}_shortlist"
        assert resolved.card_directives[0].anchor_fact == anchor


def test_refinement_repeat_comparison_and_selected_anchor_chain() -> None:
    card = OptionCard(name="Лучи", ready="сдан", price_min=12_000_000)
    refined = resolve_recipe(stage=Stage.REFINEMENT, plan=SemanticPlan(operation="refine_search"), cards=(card,))
    repeat = resolve_recipe(stage=Stage.CURRENT_OPTIONS, plan=SemanticPlan(operation="current_options"), cards=(card,))
    comparison = resolve_recipe(stage=Stage.CURRENT_OPTIONS, plan=SemanticPlan(operation="current_options", facets=["price"]), cards=(card,))
    selected = resolve_recipe(stage=Stage.SELECTED_OBJECT, plan=SemanticPlan(operation="select_option", selected_option_name="Лучи"), cards=(card,))

    assert refined.recipe.id == "refined_shortlist"
    assert repeat.recipe.id == "repeat_current_options"
    assert comparison.recipe.id == "current_comparison"
    assert selected.recipe.id == "selected_ready"
    assert selected.anchor_fact == "readiness"


def test_selected_anchor_recipes_cover_ready_finishing_price_and_details_fallback() -> None:
    cases = (
        (OptionCard(name="Готовый", ready="сдан"), "selected_ready", "readiness"),
        (OptionCard(name="С отделкой", finishing="готовая"), "selected_finishing", "finishing"),
        (OptionCard(name="По цене", price_min=12_000_000), "selected_price", "apartment_price"),
        (OptionCard(name="Без якорных фактов"), "selected_details", ""),
    )

    for card, expected_recipe, expected_anchor in cases:
        resolved = resolve_recipe(
            stage=Stage.SELECTED_OBJECT,
            plan=SemanticPlan(operation="select_option", selected_option_name=card.name),
            cards=(card,),
        )

        assert resolved.recipe.id == expected_recipe
        assert resolved.anchor_fact == expected_anchor


def test_selected_fact_static_present_missing_dynamic_fresh_and_timeout_matrix() -> None:
    base = ConversationState(visible_options=(OptionCard(name="Мичуринский парк", infrastructure=("паркинг",), parking_price="от 1,8 млн"),), selected_option_name="Мичуринский парк")

    static_present = TurnProcessor(planner=Planner(SemanticPlan(operation="select_option", selected_option_name="Мичуринский парк", requested_facts=("parking",), resolved_subject="parking")), search_service=SearchService()).process(ctx("паркинг есть?"), base)
    assert static_present.state.get("pending_followup") is None
    assert static_present.state["dialog_focus"]["last_answered_facts"] == ["parking"]

    static_missing = TurnProcessor(planner=Planner(SemanticPlan(operation="select_option", selected_option_name="Мичуринский парк", requested_facts=("location",), facts_needed=("location",), resolved_subject="location")), search_service=SearchService()).process(ctx("где он?"), base)
    assert static_missing.state["pending_followup"] == SELECTED_LIVE_FACT_CONSENT_FOLLOWUP
    assert "Передать оператору запрос" in static_missing.response_text

    dynamic_fresh = TurnProcessor(planner=Planner(SemanticPlan(operation="select_option", selected_option_name="Мичуринский парк", requested_facts=("parking_price",), facts_needed=("parking_price",), resolved_subject="parking")), search_service=SearchService(selected=OptionCard(name="Мичуринский парк", infrastructure=("паркинг",), parking_price="от 1,9 млн"), fresh=("parking_price",))).process(ctx("сколько машиноместо?"), base)
    assert dynamic_fresh.state.get("pending_followup") is None
    assert dynamic_fresh.state["dialog_focus"]["last_answered_facts"] == ["parking_price"]

    dynamic_cached = TurnProcessor(planner=Planner(SemanticPlan(operation="select_option", selected_option_name="Мичуринский парк", requested_facts=("parking_price",), facts_needed=("parking_price",), resolved_subject="parking")), search_service=SearchService()).process(ctx("сколько машиноместо?"), base)
    assert dynamic_cached.state["pending_followup"] == SELECTED_LIVE_FACT_CONSENT_FOLLOWUP
    assert "Передать оператору запрос по паркингу" in dynamic_cached.response_text


def test_live_fact_reply_accept_decline_and_invalid_never_implicit_accept() -> None:
    base = ConversationState(pending_followup=SELECTED_LIVE_FACT_CONSENT_FOLLOWUP, selected_option_name="Лучи", visible_options=(OptionCard(name="Лучи"),))

    accept = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="accept"))).process(ctx("да"), base)
    decline = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="decline"))).process(ctx("нет"), base)
    invalid = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="может"))).process(ctx("может"), base)
    missing = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform"))).process(ctx("да наверное"), base)

    assert accept.action == TurnAction.ACCEPT_OPERATOR
    assert accept.state["pending_followup"] == "contact_phone"
    assert decline.action == TurnAction.DECLINE_OPERATOR
    assert decline.state.get("pending_followup") is None
    assert invalid.action == TurnAction.CLARIFY_SELECTED_LIVE_FACT
    assert missing.action == TurnAction.FREEFORM
    assert invalid.state.get("contact_consent") is False
    assert missing.state.get("contact_consent") is False


def test_contact_resume_does_not_replace_original_request_with_current_financing_topic() -> None:
    state = ConversationState(
        pending_followup="contact_name",
        active_topic="financing",
        selected_option_name="Лучи",
        visible_options=(OptionCard(name="Лучи"),),
    )
    plan = SemanticPlan(operation="operator", resolved_intent="resume_contact", followup_outcome="resume_contact", explicit_operator_request=True)

    turn = TurnProcessor(planner=Planner(plan)).process(ctx("вернёмся к заявке"), state)

    assert "Как к вам обращаться?" in turn.response_text
    assert "ипотек" not in turn.response_text.casefold()


def test_financing_replaces_generic_selected_intro_and_uses_registry() -> None:
    state = ConversationState(visible_options=(OptionCard(name="Лучи", price_min=12_000_000),), selected_option_name="Лучи")
    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="select_option", intent="mortgage", selected_option_name="Лучи", requested_facts=("mortgage_terms",), facts_needed=("mortgage_terms",)))).process(ctx("ипотека есть?"), state)
    assert turn.state["pending_followup"] == "financing_consent"
    assert "Проверить условия по этому ЖК?" in turn.response_text
    assert "сейчас покажу самое полезное" not in turn.response_text
    assert transition_for_reply("financing_consent", "accept").action == TurnAction.ANSWER_SELECTED_OPTION

    legacy_state = ConversationState(pending_followup="financing_consent")
    assert derive_transition(SemanticPlan(operation="freeform", followup_outcome="accept"), legacy_state).action == TurnAction.ACCEPT_OPERATOR


def test_selected_compound_finishing_metro_and_mortgage_covers_every_requested_fact() -> None:
    card = OptionCard(name="Бусиновский парк", entity_id=17, entity_type="residential_complex", finishing="с отделкой", metro="Ховрино", ready="сдан")
    state = ConversationState(visible_options=(card,), selected_option_name=card.name)
    plan = SemanticPlan(
        operation="select_option",
        selected_option_name=card.name,
        requested_facts=("finishing", "metro", "mortgage_terms"),
        facts_needed=("finishing", "metro", "mortgage_terms"),
        requires_enrichment=True,
        facets=["family", "rental", "financing"],
    )

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService()).process(ctx("проверь отделку, метро и ипотеку"), state)

    assert "Отделка — с отделкой" in turn.response_text
    assert "Метро — Ховрино" in turn.response_text
    assert "условия ипотеки сначала подтвержу" in turn.response_text
    assert turn.state["pending_followup"] == "financing_consent"
    assert turn.state["pending_action"]["fact_keys"] == ["mortgage_terms"]
    assert "для семьи, будущей аренды и ипотеки" in turn.response_text
    assert "Готовый дом удобен семье" in turn.response_text
    assert "можно не ждать стройку" in turn.response_text
    assert "проверке квартиры, ремонту или меблировке" in turn.response_text


def test_off_topic_no_search_cards_operator_preserves_selection_and_clears_pending() -> None:
    state = ConversationState(pending_followup="financing_consent", selected_option_name="Лучи", visible_options=(OptionCard(name="Лучи", price_min=12_000_000),), params={"location": "Сокол"})
    search = SearchService()
    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", domain_relation="off_topic")), search_service=search).process(ctx("как сварить борщ?"), state)

    assert turn.stage == Stage.OFF_TOPIC
    assert turn.action == TurnAction.ANSWER_OFF_TOPIC
    assert search.calls == 0 and search.selected_calls == 0
    assert turn.state["selected_option_name"] == "Лучи"
    assert turn.state["visible_options"][0]["name"] == "Лучи"
    assert turn.state.get("pending_followup") is None
    assert turn.response_text.endswith("Вернёмся к подбору квартиры?")


def test_apartment_inventory_full_pipeline_and_ads_count_never_inventory() -> None:
    card = normalize_card({"name": "Лучи", "apartment_inventory": 5, "count_ads": 99, "min_price": 12_000_000})
    ads_only = normalize_card({"name": "Витрина", "count_ads": 99, "min_price": 12_000_000})

    assert card.apartment_inventory == 5
    assert "apartment_inventory" in present_fact_names(card)
    assert split_requested_facts(("apartment_inventory",), card).missing == ("apartment_inventory",)
    assert split_requested_facts(("apartment_inventory",), card, fresh_facts=("apartment_inventory",)).available == ("apartment_inventory",)
    assert ads_only.ads_count == 99
    assert ads_only.apartment_inventory is None
    assert "apartment_inventory" not in present_fact_names(ads_only)
    assert "apartment_inventory" in available_fact_fields("life")
    assert to_jsonable(card)["apartment_inventory"] == 5

    unproven_plan = build_response_plan(
        stage=Stage.SELECTED_OBJECT,
        plan=SemanticPlan(operation="select_option", selected_option_name="Лучи", requested_facts=("apartment_inventory",), facts_needed=("apartment_inventory",)),
        execution=ExecutionResult(ok=True, selected=card, fresh_facts=()),
        delta=StateDelta(),
        state=ConversationState(visible_options=(card,), selected_option_name="Лучи"),
    )
    unproven_text = render_response(unproven_plan)
    assert "5 квартир" not in unproven_text
    assert "Актуальное наличие квартир пока не подтверждено" in unproven_text


def test_malformed_apartment_inventory_is_missing_and_never_rendered_raw() -> None:
    card = OptionCard(name="Лучи", apartment_inventory={"houses": [{"id": 1}]})
    response_plan = build_response_plan(
        stage=Stage.SELECTED_OBJECT,
        plan=SemanticPlan(operation="select_option", selected_option_name="Лучи", requested_facts=("apartment_inventory",), facts_needed=("apartment_inventory",)),
        execution=ExecutionResult(ok=True, selected=card, fresh_facts=("apartment_inventory",)),
        delta=StateDelta(),
        state=ConversationState(visible_options=(card,), selected_option_name="Лучи"),
    )
    text = render_response(response_plan)

    assert "Актуальное наличие квартир пока не подтверждено" in text
    assert "{" not in text and "houses" not in text


def test_missing_finishing_operator_cta_uses_public_topic_not_enum() -> None:
    card = OptionCard(name="Лучи")
    response_plan = build_response_plan(
        stage=Stage.SELECTED_OBJECT,
        plan=SemanticPlan(operation="select_option", selected_option_name="Лучи", requested_facts=("finishing",), facts_needed=("finishing",)),
        execution=ExecutionResult(ok=True, selected=card),
        delta=StateDelta(),
        state=ConversationState(visible_options=(card,), selected_option_name="Лучи"),
    )
    text = render_response(response_plan)

    assert "Передать оператору запрос по отделке" in text
    assert "finishing" not in text


def test_fallback_and_composer_brief_share_recipe_directives_and_cta() -> None:
    card = OptionCard(name="Лучи", price_min=12_000_000)
    state = ConversationState(visible_options=(card,), selected_option_name="Лучи")
    plan = SemanticPlan(operation="financing", intent="mortgage", selected_option_name="Лучи", scope="one")
    response_plan = build_final_response_plan(stage=Stage.FINANCING_CLARIFICATION, plan=plan, execution=ExecutionResult(ok=True), delta=StateDelta(pending_followup="financing_consent"), state=state)
    fallback = render_response(response_plan)
    brief = build_response_brief(stage=Stage.FINANCING_CLARIFICATION, plan=plan, execution=ExecutionResult(ok=True), delta=StateDelta(pending_followup="financing_consent"), state=state, response_plan=response_plan)
    resolved = resolve_recipe(stage=Stage.FINANCING_CLARIFICATION, plan=plan, state=state, cards=(card,))

    assert brief.recipe_id == resolved.recipe.id
    assert brief.recipe_cards[0]["anchor_fact"] == resolved.card_directives[0].anchor_fact
    assert brief.cta_template == resolved.cta_template
    assert fallback.endswith(brief.cta_template)
