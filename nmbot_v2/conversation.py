from __future__ import annotations

import re

from .contracts import ExecutableTurn, IntentGoal, OptionCard, SemanticPlan, TurnPlan
from .client_text import safe_client_message, safe_client_question
from .fact_context import fact_availability, family_education_evidence, present_fact_names
from .response import _display_name, _format_card_price, _format_finishing, _format_money, _format_ready, _format_room_formats
from .state import ConversationState


SCENARIO_NEEDS_ORDER = ("family", "rental", "investment", "life", "financing")
SCENARIO_NEED_LABELS = {
    "family": "семьи",
    "rental": "аренды",
    "investment": "инвестиций",
    "life": "жизни",
    "financing": "оплаты",
}
OPEN_QUESTION_HANDOFF_TEMPLATE = "Точный ответ уточнит оператор."
OPEN_QUESTION_OPERATOR_CONSENT_CTA = "В текущих данных это не подтверждено. Оператор сможет проверить. Передать оператору запрос?"
# Backward-compatible symbol name: callers may still import it, but the actual
# open-question CTA must ask for operator consent, not for contact data.
OPEN_QUESTION_PHONE_CTA = OPEN_QUESTION_OPERATOR_CONSENT_CTA


def _fact_note(topic: str, card) -> str:
    if topic == "financing":
        if card.mortgage_terms:
            return "По этому варианту есть отдельные данные по условиям оплаты, но их всё равно нужно проверять с менеджером."
        if card.discount:
            return "Указанная скидка относится к фактам по проекту, а конкретные условия оплаты лучше проверять отдельно."
        if card.price or card.price_min:
            return "Начальная цена помогает прикинуть рамку оплаты, но ипотечные условия без банковской проверки не обещаю."
    if topic == "rental":
        if card.ready:
            return "Срок готовности помогает понять, когда квартирой можно будет пользоваться."
        if card.finishing:
            return "Отделка помогает оценить объём подготовки квартиры."
        if card.metro:
            return "Метро удобно учитывать при сравнении ежедневных маршрутов."
    if topic == "investment":
        if card.sales_count:
            return f"По проекту указано {card.sales_count} сделок — это фактический показатель для сравнения."
        if card.price or card.price_min:
            return "Цена помогает сопоставить вариант с вашим бюджетом."
        if card.ready:
            return "Срок готовности показывает возможный горизонт ожидания."
    if topic == "family":
        if card.infrastructure:
            return "Указанная инфраструктура помогает оценить повседневные маршруты семьи."
        if card.ready:
            return "Готовность дома помогает планировать переезд."
    if topic in {"life", "purchase"}:
        if card.metro:
            return "Метро удобно учитывать для ежедневных поездок."
        if card.ready:
            return "Готовность дома помогает планировать переезд."
    return "Можно выбрать этот вариант и запросить подробную информацию по доступным квартирам."


def _scenario_needs_from_facets(facets: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    aliases = {"mortgage": "financing", "finance": "financing"}
    values = {aliases.get(str(item or "").strip().lower(), str(item or "").strip().lower()) for item in (facets or [])}
    return tuple(scenario for scenario in SCENARIO_NEEDS_ORDER if scenario in values)


def _combined_intro(needs: tuple[str, ...]) -> str | None:
    if len(needs) < 2:
        return None
    labels = [SCENARIO_NEED_LABELS[need] for need in needs]
    if len(labels) == 2:
        joined = " и ".join(labels)
    else:
        joined = ", ".join(labels[:-1]) + " и " + labels[-1]
    return f"Смотрю текущие варианты сразу для {joined}: отмечу только то, что подтверждено по каждому ЖК."


def _combined_fact_note(needs: tuple[str, ...], card) -> str:
    for need in needs:
        note = _fact_note(need, card)
        if not note.startswith("Можно выбрать"):
            return note
    return _fact_note(needs[0] if needs else "life", card)


def _numeric_price(card) -> int | None:
    if isinstance(card.price_min, (int, float)):
        return int(card.price_min)
    if isinstance(card.price, (int, float)):
        return int(card.price)
    text = str(card.price or "").replace(" ", "").replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(млн)?", text, flags=re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    if match.group(2):
        value *= 1_000_000
    return int(value) if value >= 1_000_000 else None


def _ready_order(value: str | None) -> tuple[int, int] | None:
    text = str(value or "").casefold()
    year = re.search(r"(20\d{2})", text)
    if not year:
        return None
    quarter = re.search(r"([1-4])\s*кв", text)
    return int(year.group(1)), int(quarter.group(1)) if quarter else 4


def _next_missing_search_question(state: ConversationState) -> str:
    params = state.params
    if not params.get("max_price"):
        return "Какой бюджет ориентировочно держим?"
    if not (params.get("location") or params.get("locations") or params.get("district")):
        return "В каком районе или части Москвы хотите смотреть?"
    if not (params.get("rooms") or params.get("room_type")):
        return "Сколько комнат нужно?"
    return "Какой параметр важнее уточнить дальше: район, бюджет или готовность дома?"


def _rental_comparison_notes(cards) -> list[str]:
    """Return distinct fact-grounded notes for the current comparison."""
    prices = [_numeric_price(card) for card in cards]
    known_prices = [value for value in prices if value is not None]
    ready_values = [_ready_order(card.ready) for card in cards]
    known_ready = [value for value in ready_values if value is not None]
    min_price = min(known_prices) if known_prices else None
    max_price = max(known_prices) if known_prices else None
    earliest = min(known_ready) if known_ready else None
    notes: list[str] = []
    used: set[str] = set()

    for idx, card in enumerate(cards):
        price = prices[idx]
        ready = ready_values[idx]
        candidates: list[tuple[str, str]] = []
        if price is not None and min_price is not None and price == min_price and known_prices.count(min_price) == 1:
            candidates.append(("lowest_price", f"Это самая низкая начальная цена среди показанных вариантов — {_format_money(price)} рублей."))
        if ready is not None and earliest is not None and ready == earliest and known_ready.count(earliest) == 1:
            candidates.append(("earliest_ready", "Этот дом планируют сдать раньше остальных показанных вариантов."))
        if price is not None and max_price is not None and price == max_price and known_prices.count(max_price) == 1:
            candidates.append(("highest_price", "Это самый высокий бюджет входа среди показанных вариантов, поэтому важно отдельно проверить доступные площади и отделку."))
        if price is not None and len(set(known_prices)) >= 3:
            candidates.append(("middle_price", "Это промежуточный вариант по начальному бюджету среди показанных ЖК."))
        if card.finishing and "без" not in str(card.finishing).casefold():
            candidates.append(("finishing", "Отделка помогает сократить объём подготовки квартиры перед использованием."))
        if card.metro:
            candidates.append(("metro", "Метро удобно учитывать при сравнении ежедневных маршрутов будущего жильца."))
        if card.ready:
            candidates.append(("ready", "Срок готовности помогает понять, когда квартирой можно будет пользоваться."))
        if price is not None:
            candidates.append(("price", "Начальная цена помогает сопоставить вариант с вашим бюджетом покупки."))

        chosen = next(((key, text) for key, text in candidates if key not in used), None)
        if chosen:
            used.add(chosen[0])
            notes.append(chosen[1])
        else:
            notes.append("По этому варианту можно запросить планировки, отделку и актуальные цены для более точного сравнения.")
    return notes


def build_native_conversation_answer(plan: TurnPlan, state: ConversationState, user_text: str = "") -> str:
    if _is_freeform_clarification(plan) and plan.clarification:
        return safe_client_question(plan.clarification, "Какой вопрос по подбору уточнить?")
    topic = plan.intent or state.active_topic or "purchase"
    scenario_needs = _scenario_needs_from_facets(plan.facets)
    cards = tuple(state.visible_options[:3])
    if not cards:
        return safe_client_message(
            plan.clarification,
            _next_missing_search_question(state),
        )
    if _is_current_fact_answer(plan):
        return _answer_open_question(plan, state, user_text)
    topic_intro = {
        "rental": "Рассмотрю текущие варианты под аренду по цене, готовности, отделке и расположению — только там, где эти данные указаны.",
        "investment": "Рассмотрю текущие варианты для инвестиционной покупки по цене, сроку готовности и фактическим данным по проектам.",
        "family": "Рассмотрю текущие варианты для семьи по расположению, готовности и указанной инфраструктуре.",
        "life": "Рассмотрю текущие варианты для жизни по району, готовности, метро и указанной инфраструктуре.",
        "mortgage": "По ипотеке без банковской проверки не обещаю условия, но могу сравнить текущие варианты по фактам.",
        "financing": "По оплате без проверки программы не обещаю условия, но текущие варианты держу в контексте.",
    }.get(topic, "Отвечаю по текущему списку без нового поиска.")
    lines = [_combined_intro(scenario_needs) or topic_intro]
    comparison_notes = _rental_comparison_notes(cards) if topic == "rental" else []
    for idx, card in enumerate(cards, 1):
        bits = [
            card.location,
            _format_card_price(card),
            _format_ready(card.ready),
            _format_finishing(card.finishing),
            _format_room_formats(card.room_formats),
            f"метро {card.metro}" if card.metro else None,
        ]
        clean = ", ".join(str(x) for x in bits if x)
        lines.append(f"{idx}. {_display_name(card.name)}" + (f" — {clean}." if clean else "."))
        lines.append(comparison_notes[idx - 1] if comparison_notes else _combined_fact_note(scenario_needs, card) if len(scenario_needs) >= 2 else _fact_note(topic, card))
    if topic in {"mortgage", "financing"}:
        lines.append("Если речь про первоначальный взнос, скажите сумму — я не буду путать её с общим бюджетом.")
    elif plan.clarification:
        lines.append(safe_client_question(plan.clarification, "Какой вопрос по подбору уточнить?").rstrip("?.!") + ".")
    else:
        lines.append("Какой из этих ЖК хотите рассмотреть подробнее?")
    return "\n\n".join(lines)


def _is_freeform_clarification(plan: TurnPlan) -> bool:
    return isinstance(plan, ExecutableTurn) and plan.goal in {IntentGoal.CLARIFY, IntentGoal.RESUME_PENDING} or isinstance(plan, SemanticPlan) and plan.operation in {"freeform", "conversation"}


def _is_current_fact_answer(plan: TurnPlan) -> bool:
    return isinstance(plan, ExecutableTurn) and plan.goal in {IntentGoal.ANSWER_CURRENT, IntentGoal.ANSWER_OPEN_QUESTION} and bool(plan.requested_facts or plan.facts_needed) or isinstance(plan, SemanticPlan) and plan.operation == "answer_open_question"


def _answer_open_question(plan: TurnPlan, state: ConversationState, user_text: str) -> str:
    requested = tuple(dict.fromkeys(str(fact).strip().lower() for fact in (*plan.requested_facts, *plan.facts_needed) if fact))
    selected = state.find_visible_option(plan.selected_option_name) or state.selected_enriched
    cards = (selected,) if selected else tuple(state.visible_options[:3])
    availability = fact_availability(cards, requested)
    missing = tuple(fact for fact in requested if fact in availability.missing_facts)
    question = " ".join(str(user_text or plan.query_text or "этот вопрос").split())[:240]
    if missing:
        if missing == ("developer",):
            missing_text = "Пока не нашла подтверждённой информации о застройщике."
        else:
            topic = ", ".join(_human_fact_name(fact) for fact in missing[:3]) or "этому вопросу"
            missing_text = f"Пока не нашла подтверждённой информации по вопросу о {topic}."
        return (
            f"«{question}» — {missing_text[0].lower() + missing_text[1:]} "
            f"{OPEN_QUESTION_HANDOFF_TEMPLATE}\n\n{OPEN_QUESTION_PHONE_CTA}"
        )
    parts = [f"По вопросу «{question}» отвечаю по текущим данным, без нового поиска."]
    for card in cards:
        facts = _open_question_fact_lines(card, requested)
        if facts:
            parts.append(f"{_display_name(card.name)}: " + "; ".join(facts) + ".")
    if len(parts) == 1:
        parts.append("По текущим карточкам есть базовые данные, но по формулировке вопроса не вижу отдельного подтверждённого факта.")
    parts.append("Что ещё проверить по этим вариантам?")
    return "\n\n".join(parts)


def _open_question_fact_lines(card: OptionCard, requested: tuple[str, ...]) -> list[str]:
    present = set(present_fact_names(card))
    facts = requested or tuple(present)
    lines: list[str] = []
    for fact in facts:
        if fact not in present:
            continue
        if fact == "apartment_price":
            price = _format_card_price(card)
            if price:
                lines.append(price)
        elif fact == "location" and card.location:
            lines.append(f"локация — {card.location}")
        elif fact == "metro" and card.metro:
            lines.append(f"метро — {card.metro}")
        elif fact == "readiness":
            ready = _format_ready(card.ready)
            if ready:
                lines.append(ready)
        elif fact == "finishing":
            finishing = _format_finishing(card.finishing)
            if finishing:
                lines.append(f"отделка — {finishing}")
        elif fact == "parking":
            lines.append("паркинг указан")
        elif fact == "parking_price" and card.parking_price not in (None, "", 0):
            lines.append(f"стоимость паркинга — {card.parking_price}")
        elif fact == "parking_inventory" and card.parking_inventory not in (None, "", 0):
            lines.append(f"места в паркинге — {card.parking_inventory}")
        elif fact == "apartment_inventory" and card.apartment_inventory not in (None, "", 0, False):
            lines.append(f"наличие квартир — {card.apartment_inventory}")
        elif fact == "mortgage_terms" and (card.mortgage_terms or card.discount):
            lines.append(f"условия оплаты — {card.mortgage_terms or card.discount}")
        elif fact == "developer" and card.developer:
            lines.append(f"застройщик — {card.developer}")
        elif fact == "schools":
            family_places = family_education_evidence(card)
            if family_places:
                lines.append("школы и детские сады рядом: " + ", ".join(family_places[:3]))
        elif fact == "parks":
            parks = [item for item in card.infrastructure if re.search(r"парк|лес|сквер|набереж|река|озер|пруд", item, re.I)]
            if parks:
                lines.append("зелёные зоны рядом: " + ", ".join(parks[:3]))
    return lines


def _human_fact_name(fact: str) -> str:
    return {
        "parking": "паркинг",
        "parking_price": "стоимость паркинга",
        "parking_inventory": "наличие мест в паркинге",
        "apartment_price": "цена квартиры",
        "apartment_inventory": "актуальное наличие квартир",
        "mortgage_terms": "условия оплаты",
        "location": "расположение",
        "metro": "метро",
        "schools": "школы",
        "readiness": "срок готовности",
        "finishing": "отделка",
        "parks": "парки рядом",
        "developer": "застройщик",
    }.get(str(fact), str(fact))
