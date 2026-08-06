from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import re
from typing import Mapping

from .contracts import OptionCard, SemanticPlan, Stage, TurnAction
from .fact_context import DYNAMIC_FACTS, split_requested_facts
from .state import ConversationState


@dataclass(frozen=True)
class RecipeCardDirective:
    card_name: str
    anchor_fact: str
    allowed_benefit: str = ""
    card_mode: str = "normal"


@dataclass(frozen=True)
class OutcomeTransition:
    stage: Stage
    action: TurnAction
    response_recipe_id: str
    next_pending: str | None = None
    clear_pending: bool = False


@dataclass(frozen=True)
class ReplyContractSpec:
    id: str
    allowed_outcomes: tuple[str, ...]
    planner_context: Mapping[str, object]
    outcome_transitions: Mapping[str, OutcomeTransition]


@dataclass(frozen=True)
class RecipeSpec:
    id: str
    stages: tuple[Stage, ...]
    viewpoints: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    card_mode: str = "normal"
    fact_priority: tuple[str, ...] = ()
    benefits: Mapping[str, str] = None  # type: ignore[assignment]
    forbidden: tuple[str, ...] = ()
    fallback_recipe_id: str | None = None
    cta_template: str = ""
    reply_contract_id: str | None = None
    composition_mode: str = "bounded"

    def benefit_for(self, fact: str) -> str:
        return dict(self.benefits or {}).get(fact, "")


@dataclass(frozen=True)
class ResolvedRecipe:
    recipe: RecipeSpec
    card_directives: tuple[RecipeCardDirective, ...] = ()
    anchor_fact: str = ""
    allowed_benefit: str = ""
    cta_template: str = ""
    forbidden_inferences: tuple[str, ...] = ()


FINANCING_CONSENT_FOLLOWUP = "financing_consent"
SELECTED_LIVE_FACT_CONSENT_FOLLOWUP = "selected_live_fact_consent"
OPERATOR_CONSENT_FOLLOWUP = "operator_consent"
CONTACT_NAME_FOLLOWUP = "contact_name"
CONTACT_PHONE_FOLLOWUP = "contact_phone"

_FINANCE_FORBIDDEN = ("ставка", "первоначальный взнос", "аккредитация", "одобрение", "наличие", "срок обратной связи", "имя", "номер")
_LIVE_FACT_FORBIDDEN = ("наличие квартиры", "бронь", "этаж", "цена другого ЖК", "контакт", "телефон", "имя")


REPLY_CONTRACTS: dict[str, ReplyContractSpec] = {
    OPERATOR_CONSENT_FOLLOWUP: ReplyContractSpec(
        id=OPERATOR_CONSENT_FOLLOWUP,
        allowed_outcomes=("accept", "decline", "ask_or_clarify", "unexpected"),
        planner_context={"offered_action": "collect_contact_phone"},
        outcome_transitions={
            "accept": OutcomeTransition(Stage.OPERATOR_HANDOFF, TurnAction.ACCEPT_OPERATOR, "operator_handoff_phone_capture", clear_pending=True),
            "decline": OutcomeTransition(Stage.OPERATOR_DECLINED, TurnAction.DECLINE_OPERATOR, "selected_live_fact_declined", clear_pending=True),
            "ask_or_clarify": OutcomeTransition(Stage.OPERATOR_HANDOFF, TurnAction.OFFER_OPERATOR, "operator_handoff_name_capture", next_pending=OPERATOR_CONSENT_FOLLOWUP),
            "unexpected": OutcomeTransition(Stage.OPERATOR_HANDOFF, TurnAction.OFFER_OPERATOR, "operator_handoff_name_capture", next_pending=OPERATOR_CONSENT_FOLLOWUP),
        },
    ),
    CONTACT_NAME_FOLLOWUP: ReplyContractSpec(
        id=CONTACT_NAME_FOLLOWUP,
        allowed_outcomes=("resume_contact",),
        planner_context={"offered_action": "collect_contact"},
        outcome_transitions={
            "resume_contact": OutcomeTransition(Stage.OPERATOR_HANDOFF, TurnAction.OFFER_OPERATOR, "operator_handoff_name_capture", next_pending=CONTACT_NAME_FOLLOWUP),
        },
    ),
    CONTACT_PHONE_FOLLOWUP: ReplyContractSpec(
        id=CONTACT_PHONE_FOLLOWUP,
        allowed_outcomes=("resume_contact",),
        planner_context={"offered_action": "collect_contact_phone"},
        outcome_transitions={
            "resume_contact": OutcomeTransition(Stage.OPERATOR_HANDOFF, TurnAction.OFFER_OPERATOR, "operator_handoff_phone_capture", next_pending=CONTACT_PHONE_FOLLOWUP),
        },
    ),
    FINANCING_CONSENT_FOLLOWUP: ReplyContractSpec(
        id=FINANCING_CONSENT_FOLLOWUP,
        allowed_outcomes=("accept", "decline", "ask_or_clarify", "unexpected"),
        planner_context={"offered_action": "verify_financing_conditions"},
        outcome_transitions={
            "accept": OutcomeTransition(Stage.OPERATOR_HANDOFF, TurnAction.ACCEPT_OPERATOR, "operator_handoff_phone_capture", clear_pending=True),
            "decline": OutcomeTransition(Stage.OPERATOR_DECLINED, TurnAction.DECLINE_OPERATOR, "financing_declined", clear_pending=True),
            "ask_or_clarify": OutcomeTransition(Stage.FINANCING_CLARIFICATION, TurnAction.CLARIFY_FINANCING, "financing_consent_clarification", next_pending=FINANCING_CONSENT_FOLLOWUP),
            "unexpected": OutcomeTransition(Stage.FINANCING_CLARIFICATION, TurnAction.CLARIFY_FINANCING, "financing_consent_recovery", next_pending=FINANCING_CONSENT_FOLLOWUP),
        },
    ),
    SELECTED_LIVE_FACT_CONSENT_FOLLOWUP: ReplyContractSpec(
        id=SELECTED_LIVE_FACT_CONSENT_FOLLOWUP,
        allowed_outcomes=("accept", "decline", "ask_or_clarify", "unexpected"),
        planner_context={"scope": "one", "offered_action": "verify_selected_live_facts"},
        outcome_transitions={
            "accept": OutcomeTransition(Stage.OPERATOR_HANDOFF, TurnAction.ACCEPT_OPERATOR, "operator_handoff_phone_capture", clear_pending=True),
            "decline": OutcomeTransition(Stage.OPERATOR_DECLINED, TurnAction.DECLINE_OPERATOR, "selected_live_fact_declined", clear_pending=True),
            "ask_or_clarify": OutcomeTransition(Stage.SELECTED_LIVE_FACT_CLARIFICATION, TurnAction.CLARIFY_SELECTED_LIVE_FACT, "selected_live_fact_consent_clarification", next_pending=SELECTED_LIVE_FACT_CONSENT_FOLLOWUP),
            "unexpected": OutcomeTransition(Stage.SELECTED_LIVE_FACT_CLARIFICATION, TurnAction.CLARIFY_SELECTED_LIVE_FACT, "selected_live_fact_consent_recovery", next_pending=SELECTED_LIVE_FACT_CONSENT_FOLLOWUP),
        },
    ),
}


RECIPES: dict[str, RecipeSpec] = {
    "life_shortlist": RecipeSpec("life_shortlist", (Stage.FIRST_LIST, Stage.REFINEMENT), viewpoints=("life",), fact_priority=("metro", "readiness", "finishing", "apartment_price", "location"), benefits={"metro": "Метро рядом упрощает ежедневный маршрут.", "readiness": "Готовность помогает понять горизонт ожидания.", "finishing": "Отделка уменьшает ремонтные хлопоты.", "apartment_price": "Цена даёт понятный ориентир по бюджету.", "location": "Локация помогает сравнить ежедневный маршрут."}, cta_template="Какой вариант хотите рассмотреть подробнее?"),
    "family_shortlist": RecipeSpec("family_shortlist", (Stage.FIRST_LIST, Stage.REFINEMENT), viewpoints=("family",), fact_priority=("schools", "readiness", "finishing", "apartment_price", "location"), benefits={"schools": "Школа или детская инфраструктура упрощает семейные будни.", "readiness": "Готовность помогает семье планировать переезд.", "finishing": "Отделка уменьшает бытовую нагрузку после ключей.", "apartment_price": "Цена помогает сверить вариант с семейным бюджетом."}, cta_template="Какой вариант хотите рассмотреть подробнее?"),
    "investment_shortlist": RecipeSpec("investment_shortlist", (Stage.FIRST_LIST, Stage.REFINEMENT), viewpoints=("investment",), fact_priority=("apartment_price", "readiness", "sales_count", "ads_count", "finishing"), benefits={"apartment_price": "Цена задаёт буквальный порог входа.", "readiness": "Готовность убирает ожидание стройки, без прогноза результата сделки.", "sales_count": "Продажи ЕГРН можно использовать только как фактический счётчик.", "ads_count": "Объявления — только текущий счётчик витрины."}, forbidden=("доходность", "окупаемость", "рост цены", "спрос"), cta_template="Какой вариант хотите рассмотреть подробнее?"),
    "rental_shortlist": RecipeSpec("rental_shortlist", (Stage.FIRST_LIST, Stage.REFINEMENT), viewpoints=("rental",), fact_priority=("room_formats", "finishing", "readiness", "metro", "apartment_price"), benefits={"room_formats": "Формат помогает оценить подготовку квартиры.", "finishing": "Отделка сокращает подготовку после покупки.", "readiness": "Срок помогает понять, когда можно переходить к следующему шагу.", "metro": "Метро рядом делает маршрут понятнее."}, forbidden=("доход", "спрос", "арендатор"), cta_template="Какой вариант хотите рассмотреть подробнее?"),
    "refined_shortlist": RecipeSpec("refined_shortlist", (Stage.REFINEMENT,), fact_priority=("apartment_price", "location", "readiness"), cta_template="Какой вариант хотите рассмотреть подробнее?"),
    "repeat_current_options": RecipeSpec("repeat_current_options", (Stage.CURRENT_OPTIONS,), cta_template="Какой из этих ЖК хотите рассмотреть подробнее?"),
    "current_comparison": RecipeSpec("current_comparison", (Stage.CURRENT_OPTIONS,), cta_template="Какой ЖК хотите разобрать точнее?"),
    "why_shortlist": RecipeSpec("why_shortlist", (Stage.CURRENT_OPTIONS,), cta_template="Какой критерий важнее: бюджет, локация или срок?"),
    "selected_ready": RecipeSpec("selected_ready", (Stage.SELECTED_OBJECT,), scopes=("one",), fact_priority=("readiness",), cta_template="Хотите посмотреть цену квартиры или расположение этого ЖК?"),
    "selected_metro": RecipeSpec("selected_metro", (Stage.SELECTED_OBJECT,), scopes=("one",), fact_priority=("metro",), cta_template="Хотите посмотреть цену квартиры или расположение этого ЖК?"),
    "selected_finishing": RecipeSpec("selected_finishing", (Stage.SELECTED_OBJECT,), scopes=("one",), fact_priority=("finishing",), cta_template="Хотите посмотреть цену квартиры или расположение этого ЖК?"),
    "selected_price": RecipeSpec("selected_price", (Stage.SELECTED_OBJECT,), scopes=("one",), fact_priority=("apartment_price",), cta_template="Хотите посмотреть цену квартиры или расположение этого ЖК?"),
    "selected_location": RecipeSpec("selected_location", (Stage.SELECTED_OBJECT,), scopes=("one",), fact_priority=("location",), cta_template="Хотите посмотреть цену квартиры или расположение этого ЖК?"),
    "selected_details": RecipeSpec("selected_details", (Stage.SELECTED_OBJECT,), scopes=("one",), fact_priority=("readiness", "apartment_price", "location", "metro", "finishing"), cta_template="Хотите сравнить его с другим ЖК или проверить актуальное наличие?"),
    "selected_fact_confirmed": RecipeSpec("selected_fact_confirmed", (Stage.SELECTED_OBJECT,), scopes=("one",), fact_priority=("parking", "parking_price", "parking_inventory", "apartment_price", "apartment_inventory", "mortgage_terms"), cta_template="Хотите посмотреть цену квартиры или расположение этого ЖК?"),
    "selected_fact_not_confirmed": RecipeSpec("selected_fact_not_confirmed", (Stage.SELECTED_OBJECT,), scopes=("one",), fact_priority=("parking", "parking_price", "parking_inventory", "apartment_inventory", "mortgage_terms"), cta_template="Проверить точную актуальность по этому ЖК?"),
    "selected_live_fact_check": RecipeSpec("selected_live_fact_check", (Stage.SELECTED_OBJECT,), scopes=("one",), fact_priority=DYNAMIC_FACTS, forbidden=_LIVE_FACT_FORBIDDEN, cta_template="Проверить точную актуальность по этому ЖК?", reply_contract_id=SELECTED_LIVE_FACT_CONSENT_FOLLOWUP),
    "selected_live_fact_declined": RecipeSpec("selected_live_fact_declined", (Stage.OPERATOR_DECLINED,), cta_template="Хотите продолжить подбор по бюджету, району или отделке?"),
    "selected_live_fact_consent_clarification": RecipeSpec("selected_live_fact_consent_clarification", (Stage.SELECTED_LIVE_FACT_CLARIFICATION,), forbidden=_LIVE_FACT_FORBIDDEN, cta_template="Проверить точную актуальность по этому ЖК?", reply_contract_id=SELECTED_LIVE_FACT_CONSENT_FOLLOWUP),
    "selected_live_fact_consent_recovery": RecipeSpec("selected_live_fact_consent_recovery", (Stage.SELECTED_LIVE_FACT_CLARIFICATION,), forbidden=_LIVE_FACT_FORBIDDEN, cta_template="Проверить точную актуальность по этому ЖК?", reply_contract_id=SELECTED_LIVE_FACT_CONSENT_FOLLOWUP),
    "selected_financing": RecipeSpec("selected_financing", (Stage.SELECTED_OBJECT, Stage.FINANCING_CLARIFICATION), viewpoints=("financing", "mortgage"), scopes=("one",), forbidden=_FINANCE_FORBIDDEN, cta_template="Проверить условия по этому ЖК?", reply_contract_id=FINANCING_CONSENT_FOLLOWUP),
    "current_options_financing": RecipeSpec("current_options_financing", (Stage.FINANCING_CLARIFICATION,), viewpoints=("financing", "mortgage"), scopes=("all", "unknown"), forbidden=_FINANCE_FORBIDDEN, cta_template="Проверить условия по всем этим ЖК?", reply_contract_id=FINANCING_CONSENT_FOLLOWUP),
    "financing_declined": RecipeSpec("financing_declined", (Stage.OPERATOR_DECLINED,), cta_template="Хотите сузить варианты по бюджету, району или отделке?"),
    "financing_consent_clarification": RecipeSpec("financing_consent_clarification", (Stage.FINANCING_CLARIFICATION,), forbidden=_FINANCE_FORBIDDEN, cta_template="Проверить условия по этому ЖК?", reply_contract_id=FINANCING_CONSENT_FOLLOWUP),
    "financing_consent_recovery": RecipeSpec("financing_consent_recovery", (Stage.FINANCING_CLARIFICATION,), forbidden=_FINANCE_FORBIDDEN, cta_template="Проверить условия по этому ЖК?", reply_contract_id=FINANCING_CONSENT_FOLLOWUP),
    "operator_handoff_name_capture": RecipeSpec("operator_handoff_name_capture", (Stage.OPERATOR_HANDOFF,), cta_template="Как к вам обращаться?"),
    "operator_handoff_phone_capture": RecipeSpec("operator_handoff_phone_capture", (Stage.OPERATOR_HANDOFF,), cta_template="На какой номер вам удобно позвонить?", reply_contract_id=CONTACT_PHONE_FOLLOWUP),
    "near_results": RecipeSpec("near_results", (Stage.FIRST_LIST, Stage.REFINEMENT), card_mode="near", cta_template="Ослабим один параметр?"),
    "no_results": RecipeSpec("no_results", (Stage.FIRST_LIST, Stage.REFINEMENT), card_mode="none", cta_template="Ослабим один параметр?"),
    "off_topic": RecipeSpec("off_topic", (Stage.OFF_TOPIC,), cta_template="Вернёмся к подбору квартиры?", composition_mode="deterministic"),
    "default_clarification": RecipeSpec("default_clarification", (Stage.FREEFORM, Stage.ERROR, Stage.RESET), cta_template="Что смотрим дальше?", composition_mode="deterministic"),
}


_EXPLICIT_ALL_CURRENT_RE = re.compile(
    r"\b(?:все|всё|кажд(?:ый|ую|ое|ого|ому|ым|ом)|по\s+всем|оба|обе)\b.{0,40}\b(?:проверь|проверить|проверим|посмотр(?:и|еть|им)|уточн(?:и|ить|им))\b|"
    r"\b(?:проверь|проверить|проверим|посмотр(?:и|еть|им)|уточн(?:и|ить|им))\b.{0,40}\b(?:все|всё|кажд(?:ый|ую|ое|ого|ому|ым|ом)|по\s+всем|оба|обе)\b",
    re.I,
)


def is_explicit_all_current_financing_request(plan: SemanticPlan | None) -> bool:
    """Only a safe per-turn user text can make all-current financing explicit.

    `scope=all` is the normal current-options default, so it must not imply
    consent to check every visible ЖК.
    """

    text = str(getattr(plan, "query_text", None) or "").casefold().replace("ё", "е")
    return bool(text and _EXPLICIT_ALL_CURRENT_RE.search(text))


def reply_contract_for_pending(pending: str | None) -> ReplyContractSpec | None:
    return REPLY_CONTRACTS.get(str(pending or ""))


def transition_for_reply(pending: str | None, outcome: str | None) -> OutcomeTransition | None:
    contract = reply_contract_for_pending(pending)
    if not contract:
        return None
    selected = str(outcome or "").strip()
    if selected not in contract.allowed_outcomes:
        if "unexpected" not in contract.allowed_outcomes:
            return None
        selected = "unexpected"
    return contract.outcome_transitions[selected]


def resolve_recipe(*, stage: Stage, action: TurnAction | None = None, plan: SemanticPlan | None = None, state: ConversationState | None = None, cards: tuple[OptionCard, ...] = (), has_near: bool = False, has_no_results: bool = False, fresh_facts: tuple[str, ...] = ()) -> ResolvedRecipe:
    plan = plan or SemanticPlan(operation="freeform")
    state = state or ConversationState()
    scope = "one" if plan.selected_option_name or stage == Stage.SELECTED_OBJECT else str(plan.scope or "all" if cards else "unknown")
    viewpoint = str(plan.intent or state.active_topic or "life").strip().lower()
    requested = tuple(plan.requested_facts or plan.facts_needed)

    if stage == Stage.OFF_TOPIC:
        return _resolved(RECIPES["off_topic"], ())
    if state.pending_followup and transition_for_reply(state.pending_followup, plan.followup_outcome):
        trans = transition_for_reply(state.pending_followup, plan.followup_outcome)
        return _resolved(RECIPES.get(trans.response_recipe_id, RECIPES["default_clarification"]), cards)
    if stage == Stage.OPERATOR_HANDOFF:
        return _resolved(RECIPES["operator_handoff_name_capture"], ())
    if stage == Stage.FINANCING_CLARIFICATION or viewpoint in {"mortgage", "financing"} or "mortgage_terms" in requested:
        recipe_id = "selected_financing" if scope == "one" else "current_options_financing"
        if recipe_id == "current_options_financing" and not is_explicit_all_current_financing_request(plan):
            selection_first = replace(
                RECIPES[recipe_id],
                cta_template="По какому ЖК проверить условия ипотеки?",
                reply_contract_id=None,
            )
            return _resolved(selection_first, cards)
        return _resolved(RECIPES[recipe_id], cards[:1] if recipe_id == "selected_financing" else cards)
    if stage == Stage.SELECTED_OBJECT and requested:
        split = split_requested_facts(requested, cards[0] if cards else None, fresh_facts=fresh_facts or getattr(plan, "fresh_facts", ()))
        if any(fact in DYNAMIC_FACTS for fact in split.missing):
            return _resolved(RECIPES["selected_live_fact_check"], cards[:1], requested=requested)
        if split.available:
            return _resolved(RECIPES["selected_fact_confirmed"], cards[:1], requested=requested)
        return _resolved(RECIPES["selected_fact_not_confirmed"], cards[:1], requested=requested)
    if stage == Stage.SELECTED_OBJECT:
        for fact, recipe_id in (("readiness", "selected_ready"), ("metro", "selected_metro"), ("finishing", "selected_finishing"), ("apartment_price", "selected_price"), ("location", "selected_location")):
            if cards and fact in _present_recipe_facts(cards[0]):
                return _resolved(RECIPES[recipe_id], cards[:1])
        return _resolved(RECIPES["selected_details"], cards[:1])
    if has_no_results:
        return _resolved(RECIPES["no_results"], ())
    if has_near:
        return _resolved(RECIPES["near_results"], cards)
    if stage == Stage.REFINEMENT:
        return _resolved(RECIPES["refined_shortlist"], cards)
    if stage in {Stage.FIRST_LIST, Stage.REFINEMENT}:
        return _resolved(RECIPES.get(f"{viewpoint}_shortlist", RECIPES["life_shortlist"]), cards)
    if stage == Stage.CURRENT_OPTIONS:
        return _resolved(RECIPES["current_comparison" if plan.facets else "repeat_current_options"], cards)
    return _resolved(RECIPES["default_clarification"], cards)


def _resolved(spec: RecipeSpec, cards: tuple[OptionCard, ...], *, requested: tuple[str, ...] = ()) -> ResolvedRecipe:
    directives = _directives(spec, cards, requested=requested)
    first = directives[0] if directives else RecipeCardDirective("", spec.fact_priority[0] if spec.fact_priority else "", spec.benefit_for(spec.fact_priority[0]) if spec.fact_priority else "")
    return ResolvedRecipe(spec, directives, first.anchor_fact, first.allowed_benefit, spec.cta_template, spec.forbidden)


def _directives(spec: RecipeSpec, cards: tuple[OptionCard, ...], *, requested: tuple[str, ...]) -> tuple[RecipeCardDirective, ...]:
    used: set[str] = set()
    priority = tuple(dict.fromkeys((*requested, *spec.fact_priority)))
    out: list[RecipeCardDirective] = []
    for card in cards[:3]:
        present = _present_recipe_facts(card)
        anchor = next((fact for fact in priority if fact in present and fact not in used), None) or next((fact for fact in priority if fact in present), "")
        if anchor:
            used.add(anchor)
        out.append(RecipeCardDirective(card.name, anchor, spec.benefit_for(anchor), spec.card_mode))
    return tuple(out)


def _present_recipe_facts(card: OptionCard) -> tuple[str, ...]:
    from .fact_context import present_fact_names

    names = list(present_fact_names(card))
    if card.finishing:
        names.append("finishing")
    if card.room_formats:
        names.append("room_formats")
    if card.ads_count is not None:
        names.append("ads_count")
    if card.sales_count is not None:
        names.append("sales_count")
    return tuple(dict.fromkeys(names))


def _validate_registry() -> None:
    if len(RECIPES) != len(set(RECIPES)):
        raise RuntimeError("duplicate_recipe_id")
    if len(REPLY_CONTRACTS) != len(set(REPLY_CONTRACTS)):
        raise RuntimeError("duplicate_reply_contract_id")
    for recipe in RECIPES.values():
        if recipe.fallback_recipe_id and recipe.fallback_recipe_id not in RECIPES:
            raise RuntimeError(f"unknown_recipe_fallback:{recipe.id}")
        if recipe.reply_contract_id and recipe.reply_contract_id not in REPLY_CONTRACTS:
            raise RuntimeError(f"unknown_reply_contract:{recipe.id}")
        if recipe.reply_contract_id and not recipe.cta_template:
            raise RuntimeError(f"reply_recipe_without_cta:{recipe.id}")
    for contract in REPLY_CONTRACTS.values():
        if not set(contract.outcome_transitions) >= set(contract.allowed_outcomes):
            raise RuntimeError(f"reply_contract_missing_outcome:{contract.id}")
        for trans in contract.outcome_transitions.values():
            if trans.response_recipe_id not in RECIPES:
                raise RuntimeError(f"unknown_transition_recipe:{contract.id}:{trans.response_recipe_id}")
            if not isinstance(trans.stage, Stage) or not isinstance(trans.action, TurnAction):
                raise RuntimeError(f"invalid_transition_enum:{contract.id}")


_validate_registry()
