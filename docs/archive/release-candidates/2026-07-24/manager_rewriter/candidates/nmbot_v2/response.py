from __future__ import annotations

from collections.abc import Mapping
import re
from dataclasses import replace
from typing import Any

from .card_normalizer import missing_text
from .client_text import safe_client_message, safe_client_output, safe_client_question
from .contracts import ExecutableTurn, ExecutionResult, IntentGoal, OptionCard, ResponsePlan, SemanticPlan, Stage, StateDelta, TurnPlan
from .effective_request import EffectiveRequest, build_effective_request
from .fact_context import DYNAMIC_FACTS, family_education_evidence, present_fact_names, split_requested_facts
from .pending import is_pending_contact_name, is_pending_contact_phone
from .state import ConversationState
from . import scenario_recipes


def _constraint_phrases(delta: StateDelta, *, changed: bool = True) -> tuple[str, ...]:
    names = {
        "location": "локация",
        "district": "регион",
        "metro": "метро",
        "property_metro": "метро",
        "rooms": "комнатность",
        "max_price": "бюджет",
        "min_price": "минимальный бюджет",
        "purpose": "сценарий",
        "financing": "условия оплаты",
        "down_payment": "первоначальный взнос",
        "ready": "готовность дома",
        "finishing": "отделка",
    }
    phrases: list[str] = []
    for key, value in delta.params_update.items():
        if value is None:
            continue
        if key == "purpose":
            purpose = {
                "investment": "для инвестиций",
                "rental": "под аренду",
                "family": "для семьи",
                "life": "для жизни",
                "self_use": "для жизни",
            }.get(str(value).casefold(), str(value))
            phrases.append(f"ищем квартиру {purpose}")
            continue
        formatted = _format_constraint_value(key, value)
        if key == "max_price":
            phrases.append(f"бюджет до {formatted}")
            continue
        if key == "min_price":
            phrases.append(f"бюджет от {formatted}")
            continue
        if key == "rooms":
            phrases.append(f"нужны {formatted}")
            continue
        if key == "finishing":
            phrases.append(f"ищем варианты {formatted}")
            continue
        if key == "ready" and formatted == "дом сдан":
            phrases.append("нужен сданный дом")
            continue
        if key == "financing":
            phrases.append(f"рассматриваем {formatted}")
            continue
        if key == "down_payment":
            phrases.append(f"первоначальный взнос {formatted}")
            continue
        separator = " теперь " if changed else " — "
        phrases.append(f"{names.get(key, key)}{separator}{formatted}")
    return tuple(phrases)


def _initial_search_context(delta: StateDelta) -> str:
    phrases = _constraint_phrases(delta, changed=False)
    return "Условия поиска: " + "; ".join(phrases) + ". " if phrases else ""


def _search_principle(request: EffectiveRequest, result_count: int, *, near: bool = False) -> str:
    """Описывает фактический принцип поиска без внутренних терминов и догадок."""

    params = request.params
    rooms = params.get("rooms")
    if isinstance(rooms, (list, tuple)) and len(rooms) == 1:
        rooms = rooms[0]
    room_text = {
        1: "однокомнатные квартиры",
        2: "двухкомнатные квартиры",
        3: "трёхкомнатные квартиры",
        4: "четырёхкомнатные квартиры",
        "studio": "студии",
        "студия": "студии",
    }.get(rooms, "квартиры")
    purpose = {
        "family": "для семьи",
        "rental": "для аренды",
        "investment": "для инвестиций",
        "life": "для жизни",
        "self_use": "для жизни",
    }.get(str(params.get("purpose") or request.intent or "").casefold())

    filters = [room_text]
    if purpose:
        filters.append(purpose)
    min_price = params.get("min_price")
    max_price = params.get("max_price")
    if min_price is not None and max_price is not None:
        filters.append(f"в бюджете от {_format_constraint_value('min_price', min_price)} до {_format_constraint_value('max_price', max_price)}")
    elif max_price is not None:
        filters.append(f"в бюджете до {_format_constraint_value('max_price', max_price)}")
    elif min_price is not None:
        filters.append(f"в бюджете от {_format_constraint_value('min_price', min_price)}")
    metro = params.get("metro") or params.get("property_metro")
    district = params.get("district")
    location = params.get("location")
    if metro:
        filters.append(f"рядом с метро {_display_constraint_list(metro)}")
    elif district:
        filters.append(f"в районе {_display_constraint_list(district)}")
    elif location:
        filters.append(f"в локации {_display_constraint_list(location)}")

    missing_limits: list[str] = []
    if not any(params.get(key) not in (None, "", [], {}) for key in ("location", "district", "metro", "property_metro")):
        missing_limits.append("район")
    if min_price is None and max_price is None:
        missing_limits.append("бюджет")

    found = f"{_result_count_text(result_count).split(' ', 1)[0]} ближайший вариант" if near and result_count == 1 else f"{_result_count_text(result_count).split(' ', 1)[0]} ближайших варианта" if near and 2 <= result_count <= 4 else f"{_result_count_text(result_count).split(' ', 1)[0]} ближайших вариантов" if near else _result_count_text(result_count)
    first = "Искала " + " ".join(filters) + "."
    if missing_limits:
        limits = " и ".join(missing_limits)
        return f"{first} {limits.capitalize()} пока не ограничивала — нашла {found}."
    return f"{first} Нашла {found}."


def _display_constraint_list(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value if item)
    return str(value)


def _result_count_text(count: int) -> str:
    last_two = count % 100
    last = count % 10
    noun = "вариант" if last == 1 and last_two != 11 else "варианта" if last in {2, 3, 4} and not 12 <= last_two <= 14 else "вариантов"
    words = {1: "один", 2: "два", 3: "три"}
    return f"{words.get(count, count)} {noun}"


_SCENARIO_NEED_ALIASES = {
    "family": "family",
    "rental": "rental",
    "rent": "rental",
    "investment": "investment",
    "invest": "investment",
    "life": "life",
    "self_use": "life",
    "purchase": "life",
    "financing": "financing",
    "finance": "financing",
    "mortgage": "financing",
    "ипотека": "financing",
}
_SCENARIO_NEED_LABELS = {
    "family": "для семьи",
    "rental": "под аренду",
    "investment": "для инвестиций",
    "life": "для жизни",
    "financing": "с учётом ипотеки",
}


def _scenario_needs_from_facets(facets: object) -> tuple[str, ...]:
    if facets in (None, "", [], {}, ()): 
        return ()
    if isinstance(facets, dict):
        raw_items = [key for key, enabled in facets.items() if enabled]
    elif isinstance(facets, str):
        raw_items = [facets]
    elif isinstance(facets, (list, tuple, set)):
        raw_items = list(facets)
    else:
        raw_items = [facets]
    out: list[str] = []
    for item in raw_items:
        text = str(item or "").strip().casefold()
        normalized = _SCENARIO_NEED_ALIASES.get(text)
        if normalized and normalized not in out:
            out.append(normalized)
        if len(out) >= 5:
            break
    return tuple(out)


def _join_human(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return " и ".join(items)
    return ", ".join(items[:-1]) + " и " + items[-1]


def _multi_scenario_acknowledgement(needs: tuple[str, ...], count: int, *, near: bool = False) -> str:
    if needs == ("family", "rental", "financing"):
        goal_text = "для семьи сейчас, с возможностью будущей аренды и с учётом ипотеки"
    else:
        goal_text = _join_human([_SCENARIO_NEED_LABELS[need] for need in needs if need in _SCENARIO_NEED_LABELS])
    found = _result_count_text(count)
    prefix = "ближайших " if near else ""
    return f"Ищу варианты {goal_text}. Нашла {prefix}{found}."


def _has_family_evidence(card: OptionCard) -> bool:
    return bool(card.infrastructure) and _infra(card, "школ", "сад", "дет", "двор без машин", "охран", "безопас", "парк", "вода", "спорт")


def _has_financing_evidence(card: OptionCard) -> bool:
    return bool(card.mortgage_terms or card.discount)


def _scenario_gap_caveat(needs: tuple[str, ...], cards: tuple[OptionCard, ...]) -> str | None:
    notes: list[str] = []
    if "family" in needs and cards and not any(_has_family_evidence(card) for card in cards):
        notes.append("Школы и детскую инфраструктуру нужно отдельно проверить по выбранному ЖК.")
    if "financing" in needs and cards and not any(_has_financing_evidence(card) for card in cards):
        notes.append("Точные ипотечные программы и ставки пока не подтверждаю и не обещаю.")
    return " ".join(notes) or None


def _selected_scenario_context(needs: tuple[str, ...], card: OptionCard, requested: tuple[str, ...]) -> str:
    if len(needs) < 2:
        return ""
    goal_text = "для семьи, будущей аренды и ипотеки" if needs == ("family", "rental", "financing") else _join_human([_SCENARIO_NEED_LABELS[need] for need in needs if need in _SCENARIO_NEED_LABELS])
    used: set[str] = set()
    notes = [_benefit_reason(card, need, None, used, (card,)) for need in needs if need != "financing"]
    gap_needs = tuple(need for need in needs if need != "financing" or "mortgage_terms" not in requested)
    gap = _scenario_gap_caveat(gap_needs, (card,))
    details = " ".join((*[note for note in notes if note], *([gap] if gap else [])))
    return f"Если смотреть сразу {goal_text}: {details}" if details else f"Проверяю этот ЖК сразу {goal_text}, но подтверждённых деталей по этим сценариям пока недостаточно."


def _append_caveat(existing: str | None, extra: str | None) -> str | None:
    if existing and extra:
        return f"{existing} {extra}"
    return existing or extra


def _format_money(value: int | float) -> str:
    if value >= 1_000_000:
        millions = value / 1_000_000
        rendered = f"{millions:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{rendered} млн"
    return f"{int(value):,} ₽".replace(",", " ")


def _format_constraint_value(key: str, value) -> str:
    if key in {"max_price", "min_price", "down_payment"} and isinstance(value, (int, float)):
        return _format_money(value)
    if key == "rooms" and value == 1:
        return "однокомнатные"
    if key == "rooms" and value == 2:
        return "двухкомнатные"
    if key == "rooms" and value == 3:
        return "трёхкомнатные"
    if key == "location" and isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value if item)
    if key == "finishing":
        normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
        if value is True or normalized in {"full", "with_finishing", "finished", "renovation", "с_отделкой", "есть_отделка"}:
            return "с отделкой"
        if normalized in {"white_box", "предчистовая", "предчистовая_отделка"}:
            return "с предчистовой отделкой"
        if value is False or normalized in {"none", "without_finishing", "без_отделки"}:
            return "без отделки"
    if key == "ready":
        normalized = str(value or "").strip().casefold()
        if value is True or normalized in {"delivered", "ready", "сдан", "готов"}:
            return "дом сдан"
    if key == "financing":
        normalized = str(value or "").strip().casefold()
        return {"mortgage": "ипотека", "installment": "рассрочка"}.get(normalized, str(value))
    return str(value)


def _display_name(name: str) -> str:
    value = str(name or "").strip()
    value = re.sub(r"^ЖК\s+", "", value, flags=re.IGNORECASE)
    value = value.replace("«", "").replace("»", "").strip()
    return f"ЖК «{value}»" if value else "ЖК"


def _format_card_price(card: OptionCard) -> str | None:
    value = card.price or card.price_min
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return f"цены от {_format_money(value)} рублей"
    text = str(value).strip()
    if text.isdigit():
        return f"цены от {_format_money(int(text))} рублей"
    text = re.sub(r"^диапазон цены:\s*", "", text, flags=re.IGNORECASE)
    if re.match(r"^\d+(?:[.,]\d+)?\s*(?:млн|миллион)", text, flags=re.IGNORECASE):
        text = "от " + text
    if text.lower().startswith("от ") and "руб" not in text.lower():
        text += " рублей"
    return text


def _format_ready(value: str | None) -> str | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"(20\d{2})(?:\s*г(?:оду)?\.?)?", text, flags=re.IGNORECASE)
    if match:
        return f"сдача в {match.group(1)} году"
    quarter_names = {"1": "I", "2": "II", "3": "III", "4": "IV"}
    quarter = re.fullmatch(r"([1-4])\s*кв\.?(?:артал)?\s*(20\d{2})", text, flags=re.IGNORECASE)
    if quarter:
        return f"сдача в {quarter_names[quarter.group(1)]} квартале {quarter.group(2)} года"
    delivered = re.fullmatch(r"сдан\s*\(([1-4])\s*кв\.?(?:артал)?\s*(20\d{2})\)", text, flags=re.IGNORECASE)
    if delivered:
        return f"дом сдан в {quarter_names[delivered.group(1)]} квартале {delivered.group(2)} года"
    if text.casefold() == "сдан":
        return "дом сдан"
    return text or None


def _format_finishing(value: object | None) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None
    text = str(value or "").strip()
    lowered = text.casefold().replace("-", " ")
    if lowered in {"white box", "вайт бокс", "предчистовая", "предчистовая отделка"}:
        return "предчистовая отделка"
    if lowered in {"есть отделка", "с отделкой"}:
        return "с отделкой"
    return text or None


def _format_property_class(value: object | None) -> str | None:
    text = str(value or "").strip()
    normalized = text.casefold().replace("-", "_").replace(" ", "_")
    names = {
        "bussiness": "бизнес-класс",
        "business": "бизнес-класс",
        "business_class": "бизнес-класс",
        "comfort": "комфорт-класс",
        "comfort_class": "комфорт-класс",
        "premium": "премиум-класс",
        "premium_class": "премиум-класс",
        "economy": "эконом-класс",
        "economy_class": "эконом-класс",
    }
    if normalized in names:
        return names[normalized]
    if re.fullmatch(r"[a-z0-9_.-]+", text, flags=re.IGNORECASE):
        return None
    return text or None


def _format_percent(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        number = match.group(1).replace(".", ",")
        if number.endswith(",0"):
            number = number[:-2]
        return number + "%"
    return re.sub(r"(\d+(?:[\.,]\d+)?)\s*%", repl, value)


def _format_finance_text(value: str | None) -> str | None:
    text = _format_percent(str(value or "").strip())
    if not text:
        return None
    text = re.sub(r"\bмес\.", "месяцев", text, flags=re.IGNORECASE)
    text = re.sub(r"скидка\s*:\s*", "скидка ", text, flags=re.IGNORECASE)
    return text


def _financing_topic(params: dict[str, object]) -> str:
    preference = str(params.get("finance_preference") or params.get("financing") or "").casefold()
    return "семейной ипотеке" if "family" in preference or "семейн" in preference else "ипотеке"


def _down_payment_text(value: object) -> str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    return _format_money(value) + " рублей"


def _format_missing_fields(values: tuple[str, ...]) -> str:
    return missing_text(values)


_MISSING_CLIENT_TOPICS = {
    "location": "локации",
    "budget": "бюджете",
    "rooms": "комнатности",
    "family_infrastructure": "семейной инфраструктуре",
    "walk_infrastructure": "местах для прогулок",
    "safety_infrastructure": "безопасности территории",
    "finance": "условиях оплаты",
    "sales": "продажах по ЕГРН",
    "ads": "количестве объявлений",
    "readiness": "сроке готовности",
    "finishing": "отделке",
}

_MISSING_BY_VIEWPOINT = {
    "family": {"family_infrastructure", "walk_infrastructure", "safety_infrastructure"},
    "life": {"walk_infrastructure", "safety_infrastructure"},
    "rental": {"ads", "location", "readiness", "finishing"},
    "investment": {"sales", "ads", "finance", "readiness", "finishing"},
    "financing": {"finance"},
}

_MISSING_FOR_REQUESTED_FACT = {
    "mortgage_terms": "finance",
    "schools": "family_infrastructure",
    "location": "location",
    "metro": "location",
    "readiness": "readiness",
    "apartment_price": "budget",
    "apartment_inventory": "ads",
    "ads_count": "ads",
    "sales_count": "sales",
}


def _missing_caveat(
    values: tuple[str, ...],
    cards: tuple[OptionCard, ...] = (),
    *,
    viewpoint: str | None = None,
    requested_facts: tuple[str, ...] = (),
) -> str | None:
    # В shortlist не перечисляем все необязательные scenario gaps: клиент не
    # спрашивал о ЕГРН/объявлениях/инфраструктуре только потому, что search их
    # попытался собрать. Исключения — явный requested fact и finance-viewpoint,
    # где граница неподтверждённых условий является частью самого ответа.
    allowed = {"finance"} if str(viewpoint or "").strip().lower() == "financing" else set()
    allowed.update(
        category
        for fact in requested_facts
        if (category := _MISSING_FOR_REQUESTED_FACT.get(str(fact).strip().lower()))
    )
    visible = set(values) & allowed
    if any(card.room_formats for card in cards):
        visible.discard("rooms")
    if any(card.location for card in cards):
        visible.discard("location")
    if any(card.ready for card in cards):
        visible.discard("readiness")
    if any(card.finishing for card in cards):
        visible.discard("finishing")
    if any(card.ads_count is not None for card in cards):
        visible.discard("ads")
    if any(card.sales_count is not None for card in cards):
        visible.discard("sales")
    topics = tuple(dict.fromkeys(
        _MISSING_CLIENT_TOPICS[item]
        for item in values
        if item in visible
        if item in _MISSING_CLIENT_TOPICS
    ))[:3]
    if not topics:
        return None
    if len(topics) == 1:
        rendered = topics[0]
    else:
        rendered = ", ".join(topics[:-1]) + " и " + topics[-1]
    preposition = "об " if rendered[:1].casefold() in {"а", "э", "о", "у", "ы", "и"} else "о "
    return "Пока нет подтверждённой информации " + preposition + rendered + "."


_CONSTRAINT_KEYS = {"location", "district", "metro", "property_metro", "rooms", "max_price", "min_price", "budget", "ready", "finishing"}
_RELAX_ORDER = ("location", "district", "metro", "property_metro", "max_price", "min_price", "budget", "rooms", "ready", "finishing")


def _has_value(value) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _constraint_sources(search, delta: StateDelta, state: ConversationState) -> dict[str, object]:
    sources: dict[str, object] = {}
    for mapping in (state.params, delta.params_update, search.params if search else {}):
        if not isinstance(mapping, dict):
            continue
        for key, value in mapping.items():
            if key in _CONSTRAINT_KEYS and _has_value(value):
                sources[key] = value
            elif key in {"requested_hard", "effective_hard"} and isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    if nested_key in _CONSTRAINT_KEYS and _has_value(nested_value):
                        sources[nested_key] = nested_value
    return sources


def _relax_question(constraints: dict[str, object]) -> str:
    labels = {
        "location": "локацию",
        "district": "район",
        "metro": "метро",
        "property_metro": "метро",
        "max_price": "бюджет",
        "min_price": "бюджет",
        "budget": "бюджет",
        "rooms": "комнатность",
        "ready": "срок сдачи",
        "finishing": "отделку",
    }
    for key in _RELAX_ORDER:
        if key in constraints:
            return f"Ослабим {labels[key]}?"
    return "Ослабим один из заданных параметров?"


def _near_relax_question(cards: tuple[OptionCard, ...], constraints: dict[str, object]) -> str:
    differences = " ".join(str(card.why_close or "").casefold() for card in cards)
    if "бюджет" in differences or "цена" in differences:
        return "Поднять бюджет или оставить текущий предел?"
    if "комнат" in differences:
        return "Рассмотреть другую комнатность?"
    if "отдел" in differences:
        return "Рассмотреть варианты без подтверждённой отделки?"
    if "готов" in differences or "срок" in differences:
        return "Рассмотреть дома с другим сроком готовности?"
    if "локац" in differences or "район" in differences:
        return "Расширить локацию поиска?"
    return _relax_question(constraints)


def _initial_clarification_question(missing: tuple[str, ...]) -> str:
    lowered = " ".join(str(x).casefold() for x in missing)
    if any(token in lowered for token in ("location", "district", "metro", "локац", "район", "метро")):
        return "В какой локации или у какого метро искать?"
    if any(token in lowered for token in ("budget", "price", "max_price", "min_price", "бюдж")):
        return "Какой бюджет держим за верхнюю границу?"
    if any(token in lowered for token in ("rooms", "комнат")):
        return "Сколько комнат рассматриваете?"
    return "В какой локации или у какого метро искать?"


def _missing_hard_evidence(missing: tuple[str, ...], constraints: dict[str, object]) -> list[str]:
    rendered = " ".join(str(item).casefold() for item in missing)
    aliases = {
        "rooms": ("rooms", "комнат"),
        "ready": ("ready", "сдан", "готов", "срок"),
        "finishing": ("finishing", "отдел"),
    }
    return [key for key, markers in aliases.items() if key in constraints and any(marker in rendered for marker in markers)]


def _evidence_gap_response(fields: list[str]) -> tuple[str, str]:
    labels = {"rooms": "комнатность", "ready": "готовность дома", "finishing": "отделку"}
    names = [labels[field] for field in fields if field in labels]
    joined = ", ".join(names) if names else "один из заданных параметров"
    return (
        f"Пока не вижу точного совпадения по параметру: {joined}. Актуальные цены по подходящим квартирам тоже лучше проверить отдельно.",
        "Показать близкие варианты, где этот параметр пока требует отдельной проверки?",
    )


def _format_room_formats(values: tuple[str, ...]) -> str | None:
    normalized: list[str] = []
    names = {"1": "однокомнатные", "2": "двухкомнатные", "3": "трёхкомнатные", "4": "четырёхкомнатные"}
    for value in values:
        match = re.fullmatch(r"\s*([1-4])\s*(?:кв\.?|комн(?:атные)?)?\s*", str(value), flags=re.IGNORECASE)
        label = names.get(match.group(1)) if match else str(value).strip()
        if label and label not in normalized:
            normalized.append(label)
    return ", ".join(normalized[:3]) or None


def _short_summary(options: tuple[OptionCard, ...], previous: tuple[OptionCard, ...]) -> str | None:
    if not options:
        return None
    if previous:
        prev_prices = [x.price_min for x in previous if isinstance(x.price_min, int)]
        new_prices = [x.price_min for x in options if isinstance(x.price_min, int)]
        if prev_prices and new_prices:
            new_min = _format_money(min(new_prices))
            prev_min = _format_money(min(prev_prices))
            return f"В новом списке цены от {new_min} рублей, раньше в показанном списке было от {prev_min} рублей."
        return "Сравнила новый список с предыдущим и не повторяю старые варианты без необходимости."
    return f"Нашла {len(options)} подходящих варианта."


def _single_question_answer(message: str | None, clarification: str | None) -> tuple[str, str]:
    body = str(message or "").strip()
    requested = str(clarification or "").strip()
    if requested:
        normalized_body = body.rstrip("?.! ").casefold()
        normalized_requested = requested.rstrip("?.! ").casefold()
        acknowledgement = "Уточню один момент." if normalized_body == normalized_requested else body
        return acknowledgement or "Уточню один момент.", requested
    if "?" in body:
        before, question_and_after = body.rsplit("?", 1)
        question_start = before.rsplit("\n", 1)[-1].strip()
        acknowledgement = before[: len(before) - len(question_start)].strip()
        question = question_start or "Какой следующий шаг сделаем"
        trailing = question_and_after.strip()
        if trailing:
            acknowledgement = " ".join(x for x in (acknowledgement, trailing) if x).strip()
        return acknowledgement or "Поняла.", question
    return body or "Поняла.", "Какой следующий шаг сделаем?"


def build_response_plan(
    *,
    stage: Stage,
    plan: TurnPlan,
    execution: ExecutionResult,
    delta: StateDelta,
    state: ConversationState,
) -> ResponsePlan:
    if not execution.ok:
        if stage == Stage.FIRST_LIST and not state.visible_options:
            return ResponsePlan(
                acknowledgement="Похоже, подбор сейчас не открылся.",
                caveat="Ваши условия сохранила, можно повторить поиск.",
                final_question="Повторить поиск?",
            )
        return ResponsePlan(
            acknowledgement="Не получилось обновить подбор.",
            caveat="Оставлю прежние условия как есть, чтобы ничего не потерять.",
            final_question="Попробовать ещё раз по тем же условиям?",
        )

    if stage == Stage.RESET:
        return ResponsePlan(
            acknowledgement="Хорошо, начнём подбор заново.",
            final_question="Что ищем: район, бюджет или комнатность уже знаете?",
        )

    if stage == Stage.OFF_TOPIC:
        return ResponsePlan(
            acknowledgement="С этим я не подскажу, зато помогу с выбором новостройки.",
            final_question="Вернёмся к подбору квартиры?",
            answer_kind="off_topic",
        )

    if stage in {Stage.FIRST_LIST, Stage.REFINEMENT}:
        search = execution.search
        cards = search.shortlist(3) if search else ()
        scenario_needs = _scenario_needs_from_facets(plan.facets)
        if _is_lookup_object(plan):
            reference = str(plan.reference or "этот ЖК").strip()
            if not cards:
                return ResponsePlan(
                    acknowledgement=f"По ЖК «{reference.strip('«»')}» пока не нашла подтверждённой информации.",
                    caveat="Похожий объект вместо него подставлять не буду.",
                    final_question="Проверим написание названия или назовёте другой ЖК?",
                    answer_kind="named_object_not_found",
                    viewpoint=plan.intent or state.active_topic,
                )
            selected = cards[0]
            fact_answer, budget_clarification = _named_object_fact_summary(selected, plan, state)
            if plan.clarification:
                return ResponsePlan(
                    acknowledgement=fact_answer or _selected_object_acknowledgement(selected, plan.intent or state.active_topic or "life"),
                    cards=() if fact_answer else (selected,),
                    final_question=safe_client_question(plan.clarification, "Что именно проверить по этому ЖК?"),
                    answer_kind="named_object_lookup",
                    viewpoint=plan.intent or state.active_topic,
                )
            if fact_answer:
                return ResponsePlan(
                    acknowledgement=fact_answer,
                    cards=(selected,),
                    final_question=budget_clarification or "Что ещё проверить по этому ЖК?",
                    answer_kind="named_object_budget_clarification" if budget_clarification else "named_object_lookup",
                    viewpoint=plan.intent or state.active_topic,
                )
            return ResponsePlan(
                acknowledgement=f"Нашла подтверждённые данные по ЖК «{_display_name(selected.name)}».",
                cards=(selected,),
                final_question="Что именно проверить по этому ЖК?",
                answer_kind="named_object_lookup",
                viewpoint=plan.intent or state.active_topic,
            )
        caveat = None
        if search and search.missing:
            caveat = _missing_caveat(
                search.missing,
                cards,
                viewpoint=plan.intent or state.active_topic,
                requested_facts=tuple(plan.requested_facts or plan.facts_needed),
            )
        if cards and any(card.is_near for card in cards):
            caveat = (caveat + " " if caveat else "") + "Показываю ближайшие варианты — они не полностью совпадают с запросом."
        effective_request = build_effective_request(state, plan)
        ack = _search_principle(effective_request, len(cards), near=bool(cards and any(card.is_near for card in cards))) if cards else ("Готово, обновила подбор." if stage == Stage.REFINEMENT else "Нашла несколько вариантов.")
        if cards and len(scenario_needs) >= 2:
            ack = _multi_scenario_acknowledgement(scenario_needs, len(cards), near=bool(any(card.is_near for card in cards)))
            caveat = _append_caveat(caveat, _scenario_gap_caveat(scenario_needs, cards))
        if not cards:
            constraints = _constraint_sources(search, delta, state)
            changed_constraints = _constraint_phrases(delta) if stage == Stage.REFINEMENT else ()
            initial_context = _initial_search_context(delta) if stage == Stage.FIRST_LIST else ""
            if not constraints:
                return ResponsePlan(
                    acknowledgement="Поняла, начнём с одного уточнения.",
                    final_question=_initial_clarification_question(search.missing if search else ()),
                )
            evidence_fields = _missing_hard_evidence(search.missing if search else (), constraints)
            if evidence_fields:
                acknowledgement, question = _evidence_gap_response(evidence_fields)
                return ResponsePlan(
                    acknowledgement=initial_context + acknowledgement,
                    changed_constraints=changed_constraints,
                    final_question=question,
                )
            return ResponsePlan(
                acknowledgement=initial_context + "Точно таких вариантов сейчас не вижу.",
                changed_constraints=changed_constraints,
                caveat=caveat,
                final_question=_relax_question(constraints),
            )
        return ResponsePlan(
            acknowledgement=ack,
            changed_constraints=(),
            result_summary=_short_summary(cards, state.visible_options) if state.visible_options else None,
            cards=cards,
            caveat=caveat,
            final_question=_near_relax_question(cards, _constraint_sources(search, delta, state)) if any(card.is_near for card in cards) else "Какой вариант хотите рассмотреть подробнее?",
            answer_kind="near_results" if any(card.is_near for card in cards) else "main_search",
            viewpoint=plan.intent or state.active_topic,
            base_viewpoint=state.active_topic if plan.intent == "financing" else None,
            scenario_needs=scenario_needs if len(scenario_needs) >= 2 else (),
        )

    if stage == Stage.SELECTED_OBJECT and execution.selected:
        selected_viewpoint = plan.intent or state.active_topic or "life"
        requested = tuple(plan.requested_facts or plan.facts_needed)
        if requested and str(execution.error_code or "").startswith("selected_enrichment_"):
            return ResponsePlan(
                acknowledgement=(
                    f"Не получилось обновить сведения {_selected_fact_topic(requested)} для "
                    f"{_display_name(execution.selected.name)}. Поэтому не буду говорить, что информации нет."
                ),
                cards=(),
                final_question="Попробовать проверить ещё раз?",
                answer_kind="selected_enrichment_recovery",
                viewpoint=selected_viewpoint,
            )
        fact_answer = _selected_fact_acknowledgement(execution.selected, plan, fresh_facts=execution.fresh_facts)
        if fact_answer:
            missing_facts = split_requested_facts(requested, execution.selected, fresh_facts=execution.fresh_facts).missing
            if "mortgage_terms" in requested:
                params = dict(state.params)
                params.update(delta.params_update)
                down_payment = _down_payment_text(params.get("down_payment"))
                selected_name = _display_name(execution.selected.name)
                if down_payment:
                    topic = _financing_topic(params)
                    return ResponsePlan(
                        acknowledgement=f"Поняла: {down_payment} — это первоначальный взнос. {fact_answer}",
                        cards=(),
                        final_question=f"Передать оператору запрос по {topic} для {selected_name} с первоначальным взносом {down_payment}?",
                        operator_prompt=True,
                        answer_kind="financing_down_payment",
                        viewpoint="financing",
                        base_viewpoint=state.active_topic if selected_viewpoint == "financing" else None,
                    )
                return ResponsePlan(
                    acknowledgement=fact_answer,
                    cards=(),
                    final_question="Проверить условия по этому ЖК?",
                    operator_prompt=True,
                    answer_kind="financing",
                    viewpoint="financing",
                    base_viewpoint=state.active_topic if selected_viewpoint == "financing" else None,
                )
            return ResponsePlan(
                acknowledgement=fact_answer,
                cards=(),
                final_question=(
                    f"Передать оператору запрос {_selected_fact_topic(missing_facts)} для {_display_name(execution.selected.name)}?"
                    if missing_facts else "Что ещё хотите узнать по этому ЖК?"
                ),
                viewpoint=selected_viewpoint,
            )
        return ResponsePlan(
            acknowledgement=_selected_object_acknowledgement(execution.selected, selected_viewpoint),
            cards=(execution.selected,),
            caveat="Если хотите, дальше можно отдельно проверить квартиры, площадь или бронь именно по нему.",
            final_question="Хотите сравнить его с другим ЖК или проверить актуальное наличие?",
            answer_kind="selected_object",
            viewpoint=selected_viewpoint,
        )

    if stage == Stage.FINANCING_CLARIFICATION:
        if plan.clarification:
            return ResponsePlan(
                acknowledgement="Уточню один момент, чтобы правильно учесть сумму.",
                    final_question=safe_client_question(plan.clarification, "Подскажите, пожалуйста: эта сумма — весь бюджет или первоначальный взнос?"),
                answer_kind="financing_clarification",
                viewpoint="financing",
            )
        selected_name = state.selected_option_name or (state.selected_enriched.name if state.selected_enriched else None)
        params = dict(state.params)
        params.update(delta.params_update)
        down_payment = _down_payment_text(params.get("down_payment"))
        if selected_name and down_payment:
            topic = _financing_topic(params)
            selected_display = _display_name(selected_name)
            return ResponsePlan(
                acknowledgement=(
                    f"Поняла: {down_payment} — это первоначальный взнос. "
                    f"Точные условия по {topic} зависят от банка, застройщика и конкретной квартиры."
                ),
                final_question=f"Передать оператору запрос по {topic} для {selected_display} с первоначальным взносом {down_payment}?",
                operator_prompt=True,
                answer_kind="financing_down_payment",
                viewpoint="financing",
            )
        followup_outcome = str(plan.followup_outcome or "").strip()
        explicit_all = scenario_recipes.is_explicit_all_current_financing_request(plan)
        if selected_name:
            if followup_outcome == "ask_or_clarify":
                acknowledgement = (
                    f"По ЖК «{selected_name}» точные условия зависят от банка, застройщика и конкретной квартиры. "
                    "Чтобы проверить их, нужно ваше согласие."
                )
            elif followup_outcome == "unexpected":
                acknowledgement = (
                    f"Чтобы передать запрос по ЖК «{selected_name}», мне нужно ваше явное согласие. "
                    "Точные условия зависят от банка, застройщика и конкретной квартиры."
                )
            else:
                acknowledgement = (
                    f"По ЖК «{selected_name}» точные условия зависят от банка, застройщика и конкретной квартиры."
                )
            result_summary = "Проверка будет именно по этому ЖК."
            final_question = "Проверить условия по этому ЖК?"
        else:
            if followup_outcome == "ask_or_clarify":
                acknowledgement = "Точные условия по этим ЖК зависят от банка, застройщика и конкретной квартиры. Чтобы проверить их, нужно ваше согласие."
            elif followup_outcome == "unexpected":
                acknowledgement = "Чтобы передать запрос, мне нужно ваше явное согласие. Точные условия по этим ЖК зависят от банка, застройщика и конкретной квартиры."
            elif explicit_all:
                acknowledgement = "Точные условия по этим ЖК зависят от банка, застройщика и конкретной квартиры."
            else:
                acknowledgement = "По ипотеке и условиям оплаты точных подтверждённых условий по этим ЖК пока нет: они зависят от банка, застройщика и конкретной квартиры."
            result_summary = "Проверим условия по каждому варианту отдельно." if explicit_all or followup_outcome else None
            final_question = "Проверить условия по всем этим ЖК?" if explicit_all or followup_outcome else "По какому ЖК проверить условия ипотеки?"
        return ResponsePlan(
            acknowledgement=acknowledgement,
            changed_constraints=_constraint_phrases(delta),
            result_summary=result_summary,
            final_question=final_question,
            operator_prompt=bool(selected_name or explicit_all or followup_outcome),
            answer_kind="financing",
        )

    if stage == Stage.SELECTED_LIVE_FACT_CLARIFICATION:
        selected_name = state.selected_option_name or (state.selected_enriched.name if state.selected_enriched else None)
        requested = ", ".join(_human_fact_name(fact) for fact in (plan.requested_facts or plan.facts_needed) if fact)
        target = f" по ЖК «{selected_name}»" if selected_name else " по выбранному ЖК"
        return ResponsePlan(
            acknowledgement=f"Могу передать на проверку только {requested or 'этот вопрос'}{target}, если вы согласны.",
            final_question="Проверить точную актуальность по этому ЖК?",
            operator_prompt=True,
            answer_kind="selected_live_fact_check",
        )

    if stage == Stage.OPERATOR_HANDOFF:
        selected_name = state.selected_option_name or (state.selected_enriched.name if state.selected_enriched else None)
        # operator_reason — внутреннее planner-поле. Оно может содержать
        # служебные названия state/scenario и никогда не должно попадать в
        # клиентский текст. Контекст для клиента строим только из канонического
        # выбранного объекта.
        params = dict(state.params)
        down_payment = _down_payment_text(params.get("down_payment"))
        financing = state.active_topic == "financing" or bool(params.get("finance_preference") or params.get("financing") or down_payment)
        if financing and plan.resolved_intent != "resume_contact":
            topic = _financing_topic(params)
            target = f" для {_display_name(selected_name)}" if selected_name else " по текущим вариантам"
            payment = f" с первоначальным взносом {down_payment}" if down_payment else ""
            acknowledgement = f"Хорошо. Чтобы передать оператору запрос по {topic}{target}{payment}, сначала уточню контакт."
        else:
            target = f" по {_display_name(selected_name)}" if selected_name else " по текущему подбору"
            acknowledgement = f"Хорошо. Чтобы передать оператору запрос{target}, сначала уточню контакт."
        return ResponsePlan(
            acknowledgement=acknowledgement,
            final_question="Как к вам обращаться?",
            operator_prompt=True,
            answer_kind="operator_offer",
        )

    if stage == Stage.OPERATOR_DECLINED:
        return ResponsePlan(
            acknowledgement="Хорошо, без оператора.",
            result_summary="Можем продолжить подбор здесь.",
            final_question="Хотите сузить варианты по бюджету, району или отделке?",
            answer_kind="operator_declined",
        )

    if stage == Stage.CURRENT_OPTIONS and (plan.intent in {"mortgage", "financing"} or "mortgage" in tuple(getattr(plan, "facets", ()) or ())):
        cards = tuple(state.visible_options[:3])
        explicit_all = scenario_recipes.is_explicit_all_current_financing_request(plan)
        if explicit_all:
            acknowledgement = "Точные условия по этим ЖК зависят от банка, застройщика и конкретной квартиры."
            result_summary = "Проверим условия по каждому варианту отдельно."
            final_question = "Проверить условия по всем этим ЖК?"
        else:
            acknowledgement = "По ипотеке и условиям оплаты точных подтверждённых условий по этим ЖК пока нет: они зависят от банка, застройщика и конкретной квартиры."
            result_summary = None
            final_question = "По какому ЖК проверить условия ипотеки?"
        return ResponsePlan(
            acknowledgement=acknowledgement,
            result_summary=result_summary,
            cards=cards,
            final_question=final_question,
            operator_prompt=explicit_all,
            answer_kind="financing",
            viewpoint="financing",
            base_viewpoint=state.active_topic if state.active_topic != "financing" else None,
        )

    safe_clarification = (
        safe_client_question(plan.clarification, "Какой следующий шаг сделать?")
        if plan.clarification
        else None
    )
    acknowledgement, final_question = _single_question_answer(
        safe_client_message(execution.message, "Поняла."),
        safe_clarification,
    )
    return ResponsePlan(
        acknowledgement=acknowledgement,
        final_question=final_question,
        operator_prompt=_is_current_fact_answer(plan) and delta.pending_followup == scenario_recipes.SELECTED_LIVE_FACT_CONSENT_FOLLOWUP,
        answer_kind=_fallback_answer_kind(plan),
    )


def _is_lookup_object(plan: TurnPlan) -> bool:
    return (isinstance(plan, ExecutableTurn) and plan.goal == IntentGoal.LOOKUP_OBJECT) or (isinstance(plan, SemanticPlan) and plan.operation == "lookup_object")


def _is_current_fact_answer(plan: TurnPlan) -> bool:
    return (
        isinstance(plan, ExecutableTurn)
        and plan.goal in {IntentGoal.ANSWER_CURRENT, IntentGoal.ANSWER_OPEN_QUESTION}
        and bool(plan.requested_facts or plan.facts_needed)
    ) or (isinstance(plan, SemanticPlan) and plan.operation == "answer_open_question")


def _fallback_answer_kind(plan: TurnPlan) -> str:
    if isinstance(plan, ExecutableTurn):
        if _is_current_fact_answer(plan):
            return "answer_open_question"
        if plan.goal in {IntentGoal.COMPARE_CURRENT, IntentGoal.RECOMMEND_CURRENT, IntentGoal.ANSWER_CURRENT, IntentGoal.ANSWER_SELECTED, IntentGoal.OPERATOR, IntentGoal.OFF_TOPIC}:
            return plan.goal.value
        return "generic"
    return "answer_open_question" if plan.operation == "answer_open_question" else "generic"


def _has_ready(card: OptionCard) -> bool:
    return bool(card.ready and re.search(r"сдан|готов", str(card.ready), re.I))


def _infra(card: OptionCard, *needles: str) -> bool:
    hay = " ".join(card.infrastructure).casefold()
    return any(needle in hay for needle in needles)


def _headline_details(card: OptionCard, viewpoint: str) -> str:
    prefix = "Ближайший вариант: " if card.is_near else ""
    details = [card.location, _format_card_price(card)]
    finishing = _format_finishing(card.finishing)
    ready = _format_ready(card.ready)
    if finishing:
        details.append(finishing)
    if ready:
        details.append(ready)
    if card.area:
        details.append(card.area)
    if card.metro:
        details.append(f"метро {card.metro}")
    if card.developer:
        details.append(f"застройщик {card.developer}")
    property_class = _format_property_class(card.property_class)
    if property_class:
        details.append(property_class)
    room_formats = _format_room_formats(card.room_formats)
    if room_formats:
        details.append(room_formats)
    if card.infrastructure:
        details.append("рядом: " + ", ".join(card.infrastructure[:3]))
    if viewpoint == "investment" and card.sales_count is not None:
        details.append(f"продаж: {card.sales_count}")
    if viewpoint == "investment" and card.ads_count is not None:
        details.append(f"на витрине {card.ads_count} объявлений")
    if viewpoint == "investment" and card.sales_date:
        details.append(f"дата данных: {card.sales_date}")
    if viewpoint == "financing" and card.discount:
        finance_text = _format_finance_text(card.discount)
        if finance_text:
            details.append(finance_text)
    clean = [str(x) for x in details if x]
    return f"{prefix}{_display_name(card.name)}" + (" — " + ", ".join(clean) if clean else "") + "."


def _selected_object_acknowledgement(card: OptionCard, viewpoint: str) -> str:
    name = _display_name(card.name)
    if viewpoint == "rental":
        if _has_ready(card):
            return f"Вы выбрали готовый вариант для сценария под сдачу: {name} уже сдан, поэтому можно не ждать окончания стройки."
        if card.price_min is not None or card.price:
            return f"Вы выбрали {name}. Стартовая цена помогает быстро оценить бюджет входа перед проверкой площади и планировок."
        if card.location:
            return f"Вы выбрали {name}. Локация — первый ориентир, который стоит сопоставить с маршрутом будущего жильца."
    if viewpoint in {"life", "family"}:
        if _has_ready(card):
            return f"По {name} могу рассказать вот что. Дом уже сдан, поэтому не нужно ждать окончания стройки."
        if card.price_min is not None or card.price:
            return f"По {name} могу рассказать вот что. Стартовая цена помогает быстро понять, насколько он вписывается в бюджет."
        if card.location:
            return f"По {name} могу рассказать вот что. Локация помогает оценить, насколько удобно будет жить именно здесь."
        return f"По {name} могу рассказать вот что."
    if viewpoint == "financing":
        return f"По {name} разберём оплату предметно, без лишней теории."
    return f"По {name} могу рассказать вот что."


def _selected_fact_acknowledgement(card: OptionCard, plan: SemanticPlan, *, fresh_facts: tuple[str, ...] = ()) -> str | None:
    requested = tuple(plan.requested_facts or plan.facts_needed)
    if not requested:
        return None
    name = _display_name(card.name)
    present = set(present_fact_names(card))
    fresh = {str(item).strip().lower() for item in fresh_facts}
    parts: list[str] = []
    for fact in dict.fromkeys(requested):
        if fact == "mortgage_terms":
            if card.mortgage_terms:
                parts.append(f"По ипотеке есть ориентир: {card.mortgage_terms}. Точные условия лучше перепроверить по этому ЖК.")
            else:
                parts.append("Точных условий по ипотеке сейчас нет — ставку и одобрение лучше уточнить отдельно.")
        elif fact == "parking_price":
            if card.parking_price not in (None, "", 0):
                if "parking_price" in fresh:
                    parts.append(f"сейчас вижу стоимость машиноместа: {card.parking_price}.")
                else:
                    parts.append(f"вижу ориентир по стоимости машиноместа: {card.parking_price}. Его лучше перепроверить.")
            elif "parking" in present:
                parts.append("паркинг есть, но стоимость машиноместа сейчас не вижу.")
            else:
                parts.append("стоимость машиноместа сейчас не вижу.")
        elif fact == "parking":
            parts.append("паркинг есть." if "parking" in present else "наличие паркинга сейчас не вижу подтверждённым.")
        elif fact == "apartment_inventory":
            # Availability is dynamic.  A value carried by the model/card is
            # publishable only when the current lookup marked this exact fact
            # fresh; otherwise it has no raw MCP provenance.
            inventory = _format_apartment_inventory(card.apartment_inventory) if "apartment_inventory" in fresh else None
            if inventory:
                parts.append(f"Актуальное наличие квартир: {inventory}.")
            else:
                parts.append("Актуальное наличие квартир пока не подтверждено.")
        elif fact == "apartment_price":
            price = _format_card_price(card)
            parts.append(f"вижу {price}." if price else "цена квартиры пока не подтверждена.")
        elif fact == "readiness":
            ready = _format_ready(card.ready)
            parts.append(f"{ready}." if ready else "срок готовности пока не подтверждён.")
        elif fact == "location":
            parts.append(f"Локация — {card.location}." if card.location else "Локация пока не подтверждена.")
        elif fact == "metro":
            parts.append(f"Метро — {card.metro}." if card.metro else "Информация о метро пока не подтверждена.")
        elif fact == "finishing":
            finishing = _format_finishing(card.finishing)
            parts.append(f"Отделка — {finishing}." if finishing else "Информация об отделке пока не подтверждена.")
        elif fact == "schools":
            family_places = family_education_evidence(card)
            parts.append(
                "Рядом указаны: " + ", ".join(family_places) + "."
                if family_places
                else "Информация о школах и детских садах пока не подтверждена."
            )
    if not parts:
        return None
    answer = f"По {name}: " + "; ".join(part.rstrip(".") for part in parts) + "."
    scenario_context = _selected_scenario_context(_scenario_needs_from_facets(plan.facets), card, requested)
    return f"{answer} {scenario_context}" if scenario_context else answer


def _named_object_fact_summary(card: OptionCard, plan: SemanticPlan, state: ConversationState) -> tuple[str | None, str | None]:
    """Собрать все запрошенные факты по явно названному ЖК, не обрываясь на первом."""

    requested = tuple(plan.requested_facts or plan.facts_needed)
    if not requested:
        return None, None

    name = _display_name(card.name)
    effective = build_effective_request(state, plan)
    budget = effective.params.get("max_price")
    price = card.price_min if isinstance(card.price_min, (int, float)) else card.price if isinstance(card.price, (int, float)) else None
    parts: list[str] = []

    if "apartment_price" in requested:
        if price is not None and isinstance(budget, (int, float)):
            if price > budget:
                parts.append(
                    f"Если {_format_money(budget)} рублей — весь бюджет, {name} не укладывается: "
                    f"квартиры начинаются от {_format_money(price)} рублей."
                )
            else:
                parts.append(
                    f"По стартовой цене {name} укладывается в {_format_money(budget)} рублей: "
                    f"квартиры начинаются от {_format_money(price)} рублей."
                )
        elif price is not None:
            parts.append(f"В {name} квартиры начинаются от {_format_money(price)} рублей.")
        else:
            parts.append(f"По {name} цену квартиры сейчас не вижу, поэтому наугад её не назову.")

    if "mortgage_terms" in requested:
        if card.mortgage_terms:
            parts.append(f"По ипотеке есть ориентир: {card.mortgage_terms}. Точные условия лучше перепроверить по этому ЖК.")
        else:
            parts.append("Точных условий семейной ипотеки в данных сейчас нет — ставку и одобрение нужно уточнять отдельно.")

    if not parts:
        fallback = _selected_fact_acknowledgement(card, plan)
        return fallback, None

    query = str(plan.query_text or "").casefold()
    ambiguous_money = (
        "mortgage_terms" in requested
        and isinstance(budget, (int, float))
        and bool(re.search(r"на руках|накоп|сбереж|денег.{0,20}(?:есть|мало)|\bвсего\s+\d", query))
    )
    question = f"{_format_money(budget)} рублей — это весь бюджет или первоначальный взнос?" if ambiguous_money else None
    return " ".join(parts), question


def _human_fact_name(fact: str) -> str:
    return {
        "parking_price": "стоимость машиноместа",
        "parking_inventory": "наличие мест в паркинге",
        "apartment_inventory": "актуальное наличие квартир",
        "mortgage_terms": "условия оплаты",
        "apartment_price": "цена квартиры",
        "parking": "наличие паркинга",
        "finishing": "отделка",
        "readiness": "срок готовности",
        "location": "расположение",
        "metro": "метро",
        "schools": "школы",
    }.get(str(fact), str(fact))


def _selected_fact_topic(facts: tuple[str, ...]) -> str:
    requested = tuple(dict.fromkeys(str(fact) for fact in facts if fact))
    if any(fact.startswith("parking") for fact in requested):
        return "по паркингу"
    if "apartment_inventory" in requested:
        return "по наличию квартир"
    if "mortgage_terms" in requested:
        return "по условиям оплаты"
    if "finishing" in requested:
        return "по отделке"
    if "readiness" in requested:
        return "по сроку готовности"
    if "location" in requested:
        return "по расположению"
    if "metro" in requested:
        return "по метро"
    if "schools" in requested:
        return "по школам"
    if "apartment_price" in requested:
        return "по цене квартиры"
    if requested:
        label = _human_fact_name(requested[0])
        return f"по вопросу «{label}»" if label != requested[0] else "по этому вопросу"
    return "по этому вопросу"


def _format_apartment_inventory(value: Any) -> str | None:
    if value in (None, "", 0, "0", False):
        return None
    if isinstance(value, bool):
        return "есть предложения" if value else None
    if isinstance(value, int):
        return f"{value} {_plural_ru(value, 'квартира', 'квартиры', 'квартир')} в наличии" if value > 0 else None
    if isinstance(value, (Mapping, list, tuple, set)):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "0" or len(text) > 80 or any(token in text for token in ("{", "}", "[", "]")):
            return None
        return text
    return None


def _plural_ru(number: int, one: str, few: str, many: str) -> str:
    mod100 = abs(number) % 100
    mod10 = abs(number) % 10
    if 11 <= mod100 <= 14:
        return many
    if mod10 == 1:
        return one
    if 2 <= mod10 <= 4:
        return few
    return many


def _has_lowest_entry_price(card: OptionCard, cards: tuple[OptionCard, ...]) -> bool:
    prices = [item.price_min for item in cards if isinstance(item.price_min, (int, float))]
    return bool(
        isinstance(card.price_min, (int, float))
        and len(prices) > 1
        and len(set(prices)) > 1
        and card.price_min == min(prices)
    )


def _has_middle_entry_price(card: OptionCard, cards: tuple[OptionCard, ...]) -> bool:
    prices = sorted({item.price_min for item in cards if isinstance(item.price_min, (int, float))})
    return bool(
        isinstance(card.price_min, (int, float))
        and len(prices) >= 3
        and card.price_min == prices[len(prices) // 2]
    )


def _benefit_reason(
    card: OptionCard,
    viewpoint: str,
    base_viewpoint: str | None,
    used: set[str],
    cards: tuple[OptionCard, ...] = (),
) -> str:
    finishing = _format_finishing(card.finishing)
    vp = base_viewpoint if viewpoint == "financing" and base_viewpoint else viewpoint
    candidates: list[tuple[str, str]] = []
    if vp == "family":
        if _infra(card, "школ", "сад", "дет"):
            family_bits = []
            if _infra(card, "школ"):
                family_bits.append("школа")
            if _infra(card, "сад"):
                family_bits.append("детский сад")
            if _infra(card, "дет") and not family_bits:
                family_bits.append("детская инфраструктура")
            label = ", ".join(dict.fromkeys(family_bits)) or "семейная инфраструктура"
            candidates.append(("family_daily", f"Рядом есть {label} — это упрощает семейные будни и ежедневные маршруты."))
        if _infra(card, "двор без машин"):
            candidates.append(("family_safe_yard", "Двор без машин даёт более спокойный ежедневный сценарий для детей, а не просто красивую территорию."))
        if _infra(card, "охран", "безопас"):
            candidates.append(("family_safe", "Охрана и признаки безопасности добавляют спокойствия в ежедневном семейном сценарии."))
        if _infra(card, "парк", "вода", "спорт"):
            walk_bits = []
            if _infra(card, "парк"):
                walk_bits.append("парк")
            if _infra(card, "вода"):
                walk_bits.append("вода рядом")
            if _infra(card, "спорт"):
                walk_bits.append("спортплощадка")
            label = ", ".join(walk_bits) or "инфраструктура для прогулок"
            verb = "добавляют" if len(walk_bits) > 1 else "добавляет"
            candidates.append(("family_walk", f"{label.capitalize()} {verb} семье понятный маршрут для прогулок после учёбы и в выходные."))
        if _has_ready(card):
            candidates.append(("family_ready", "Готовый дом удобен семье, если хочется планировать переезд без долгого ожидания стройки."))
        elif card.ready:
            candidates.append(("family_deadline", "Срок сдачи помогает семье заранее спланировать переезд и бытовые маршруты."))
        if finishing and "без" not in finishing.casefold():
            candidates.append(("family_finish", "Отделка уменьшает ремонтные хлопоты после ключей и оставляет больше сил на сам переезд."))
    elif vp == "investment":
        if card.sales_count is not None:
            candidates.append(("invest_sales", f"ЕГРН показывает {card.sales_count} продаж — это фактический ориентир для сравнения, без прогнозов и обещаний."))
        if card.ads_count is not None:
            candidates.append(("invest_ads", f"Сейчас на витрине указано {card.ads_count} объявлений — это ориентир, какие форматы представлены."))
        if _has_lowest_entry_price(card, cards):
            candidates.append(("invest_lowest_entry", "Среди этих вариантов здесь самый низкий старт по цене — с него проще начинать сравнение бюджета входа."))
        if _has_middle_entry_price(card, cards):
            candidates.append(("invest_middle_entry", "По стартовой цене это середина текущей тройки — удобный ориентир между самым доступным и более дорогим входом."))
        if card.price_min is not None or card.price:
            candidates.append(("invest_entry", "Сильная сторона здесь — понятный порог входа по цене; дальше стоит сравнить площадь и корпус."))
        if _has_ready(card):
            candidates.append(("invest_ready", "Дом уже сдан — не нужно ждать окончания строительства."))
        if finishing and "без" not in finishing.casefold():
            candidates.append(("invest_finish", "Отделка уменьшает объём вложений после покупки и делает старт более предсказуемым."))
        if card.location:
            candidates.append((f"invest_location:{card.location.casefold()}", f"Локация — {card.location}: её стоит отдельно сравнить с другими вариантами по маршруту и конкретным планировкам."))
    elif vp == "rental":
        if _has_ready(card):
            candidates.append(("rent_ready", "Дом уже сдан — это готовый вариант для следующего шага: можно не ждать стройку и переходить к проверке квартиры, ремонту или меблировке."))
        if finishing and "без" not in finishing.casefold():
            candidates.append(("rent_finish", "С отделкой проще перейти от покупки к подготовке квартиры под сдачу — меньше стартовых ремонтных хлопот."))
        if card.metro:
            candidates.append(("rent_metro", f"Метро {card.metro} — понятный ориентир для будущего жильца и его ежедневного маршрута."))
        if card.area or _format_room_formats(card.room_formats):
            candidates.append(("rent_compact", "Компактный формат проще запускать под сдачу: меньше лишней площади и понятнее бюджет входа."))
        if card.property_class or card.infrastructure:
            profile_bits = []
            if card.property_class:
                profile_bits.append(_format_property_class(card.property_class))
            if card.infrastructure:
                profile_bits.append("есть " + ", ".join(card.infrastructure[:2]))
            profile = "; ".join(bit for bit in profile_bits if bit)
            location = f" Локация — {card.location}." if card.location else ""
            display_profile = profile.replace("; есть ", " и ")
            if card.infrastructure:
                reason = "Такой формат удобно учитывать, если важны не только цена входа, но и дополнительные характеристики объекта."
            else:
                reason = "Вариант стоит рассмотреть, если вам подходит эта локация; дальше важно сравнить планировки и маршрут будущего жильца."
            candidates.append((f"rent_profile:{card.name.casefold()}", f"{display_profile.capitalize()}.{location} {reason}"))
        if _has_lowest_entry_price(card, cards):
            candidates.append(("rent_lowest_entry", "Здесь самый доступный вход среди показанных вариантов — удобно начать сравнение под сдачу с меньшего бюджета."))
        if _has_middle_entry_price(card, cards):
            candidates.append(("rent_middle_entry", "По стартовой цене это средний вариант в текущей тройке — компромисс между самым доступным входом и более дорогим предложением."))
        if card.location:
            candidates.append((f"rent_location:{card.location.casefold()}", f"Локация — {card.location}. Такой вариант стоит выбирать, если этот район подходит под маршрут будущего жильца; дальше проверим площади и планировки."))
    elif vp == "financing":
        if card.mortgage_terms:
            candidates.append((f"finance_terms:{str(card.mortgage_terms).casefold()}", f"По оплате указаны условия: {card.mortgage_terms}. Это факт по ЖК, без обещания одобрения или ставки."))
        if card.discount:
            finance = _format_finance_text(card.discount)
            candidates.append((f"finance_discount:{str(card.discount).casefold()}", f"По оплате вижу условие: {finance}. Точные программы всё равно нужно проверять отдельно."))
        if _has_lowest_entry_price(card, cards):
            candidates.append(("finance_lowest_entry", "Здесь самый низкий старт по цене среди этих вариантов — это буквальный ориентир для расчёта бюджета и первого взноса."))
        if card.price_min is not None or card.price:
            candidates.append((f"finance_price:{card.price_min or card.price}", "Цена даёт понятную границу для расчёта бюджета; одобрение, ставку и программу по ней не обещаю."))
    else:
        if card.metro:
            candidates.append(("life_metro", "Метро рядом — это простая ежедневная польза: легче планировать дорогу на работу и обратно."))
        if _has_ready(card):
            candidates.append(("life_ready", "Дом уже сдан — можно не ждать окончания стройки и спокойнее сравнить его с вариантами, которые ещё строятся."))
        elif card.ready:
            candidates.append(("life_deadline", "Срок сдачи помогает понять горизонт ожидания и спокойно сравнить вариант с готовыми домами."))
        if finishing and "без" not in finishing.casefold():
            candidates.append(("life_finish", "Отделка снимает часть ремонтных хлопот и помогает быстрее представить переезд."))
        if card.infrastructure:
            candidates.append(("life_infra", "Инфраструктура рядом делает вариант практичным для повседневных дел, а не только для просмотра."))
        if card.location:
            candidates.append(("life_location", f"Локация — {card.location}: это отдельный ориентир для сравнения ежедневного маршрута с другими вариантами из списка."))
    if card.why_close:
        candidates.insert(0, ("why_close", f"Отличие: {card.why_close}. Это помогает понять, чем вариант отличается от точного совпадения."))
    finance = _format_finance_text(card.discount) if viewpoint == "financing" and card.discount else None
    for key, text in candidates:
        if key not in used:
            used.add(key)
            return f"{text} По оплате вижу условия: {finance}." if finance else text
    if finance:
        return f"По оплате вижу условия: {finance}; по базовой пользе лучше сравнить карточку с вашими условиями."
    # Если сильного и ещё не использованного факта нет, лучше оставить короткую
    # grounded-карточку, чем добавлять одинаковую маркетинговую заглушку.
    return ""


def _rotated_scenario_needs(needs: tuple[str, ...], index: int) -> tuple[str, ...]:
    if len(needs) < 2:
        return needs
    offset = (index - 1) % len(needs)
    return needs[offset:] + needs[:offset]


def _multi_scenario_benefit_reason(
    card: OptionCard,
    viewpoint: str,
    base_viewpoint: str | None,
    used: set[str],
    cards: tuple[OptionCard, ...],
    scenario_needs: tuple[str, ...],
    index: int,
) -> str:
    if len(scenario_needs) < 2:
        return _benefit_reason(card, viewpoint, base_viewpoint, used, cards)
    for need in _rotated_scenario_needs(scenario_needs, index):
        benefit = _benefit_reason(card, need, base_viewpoint if need == "financing" else None, used, cards)
        if benefit:
            return benefit
    return _benefit_reason(card, viewpoint, base_viewpoint, used, cards)


def _card_block(index: int, card: OptionCard, viewpoint: str, base_viewpoint: str | None, used: set[str], cards: tuple[OptionCard, ...], scenario_needs: tuple[str, ...] = ()) -> str:
    headline = f"{index}. {_headline_details(card, viewpoint)}"
    benefit = _multi_scenario_benefit_reason(card, viewpoint, base_viewpoint, used, cards, scenario_needs, index)
    return f"{headline}\n{benefit}" if benefit else headline


def build_final_response_plan(
    *,
    stage: Stage,
    plan: TurnPlan,
    execution: ExecutionResult,
    delta: StateDelta,
    state: ConversationState,
) -> ResponsePlan:
    """Собрать окончательный план ответа вместе с единственным recipe/CTA."""

    response_plan = build_response_plan(stage=stage, plan=plan, execution=execution, delta=delta, state=state)
    pending_consent = delta.contact_consent if delta.contact_consent is not None else state.contact_consent
    if is_pending_contact_phone(delta.pending_followup, contact_consent=pending_consent):
        return replace(response_plan, final_question="На какой номер вам удобно позвонить?", cta_template="На какой номер вам удобно позвонить?")
    if is_pending_contact_name(delta.pending_followup, contact_consent=pending_consent):
        return replace(response_plan, final_question="Как к вам обращаться?", cta_template="Как к вам обращаться?")
    cards = _recipe_cards_for_resolution(stage, plan, execution, state, response_plan)
    resolved = scenario_recipes.resolve_recipe(
        stage=stage,
        action=None,
        plan=plan,
        state=state,
        cards=cards,
        has_near=bool(execution.search and execution.search.near and not execution.search.facts),
        has_no_results=bool(execution.search and not execution.search.facts and not execution.search.near),
        fresh_facts=execution.fresh_facts,
    )
    response_owns_question = (
        response_plan.answer_kind.startswith("named_object_")
        or response_plan.answer_kind in {"financing_down_payment", "selected_enrichment_recovery"}
        or (stage == Stage.SELECTED_OBJECT and delta.pending_followup == scenario_recipes.SELECTED_LIVE_FACT_CONSENT_FOLLOWUP)
    )
    strict_cta = resolved.cta_template if _requires_registry_cta(resolved, stage) and not plan.clarification and not response_owns_question else ""
    acknowledgement = response_plan.acknowledgement
    if resolved.recipe.id == "selected_live_fact_declined":
        acknowledgement = "Хорошо, не передаю этот вопрос оператору. Продолжим здесь по тому, что уже известно."
    elif resolved.recipe.id == "financing_declined":
        acknowledgement = "Хорошо, без проверки условий через оператора. Продолжим подбор здесь по тому, что уже известно."
    return replace(
        response_plan,
        acknowledgement=acknowledgement,
        final_question=strict_cta or response_plan.final_question,
        recipe_id=resolved.recipe.id,
        recipe_cards=tuple({"card_name": item.card_name, "anchor_fact": item.anchor_fact, "allowed_benefit": item.allowed_benefit, "card_mode": item.card_mode} for item in resolved.card_directives),
        anchor_fact=resolved.anchor_fact,
        allowed_benefit=resolved.allowed_benefit,
        forbidden_inferences=resolved.forbidden_inferences,
        cta_template=strict_cta,
        composition_mode=resolved.recipe.composition_mode,
        reply_contract_id=resolved.recipe.reply_contract_id,
    )


def _recipe_cards_for_resolution(stage: Stage, plan: TurnPlan, execution: ExecutionResult, state: ConversationState, response_plan: ResponsePlan) -> tuple[OptionCard, ...]:
    cards = tuple(response_plan.cards[:3])
    if not cards and execution.search:
        cards = execution.search.shortlist(3)
    if execution.selected and not cards:
        cards = (execution.selected,)
    if not cards and stage == Stage.FINANCING_CLARIFICATION:
        selected_name = state.selected_option_name or plan.selected_option_name
        selected_card = state.find_visible_option(selected_name) or state.selected_enriched
        if selected_card:
            cards = (selected_card,)
    if not cards and state.visible_options:
        cards = tuple(state.visible_options[:3])
    return cards


def _requires_registry_cta(resolved, stage: Stage) -> bool:
    if not resolved.cta_template:
        return False
    if resolved.recipe.reply_contract_id or stage == Stage.OFF_TOPIC:
        return True
    return resolved.recipe.id in {"selected_live_fact_declined", "financing_declined"}


def render_response(plan: ResponsePlan) -> str:
    cards = tuple(plan.cards[:3])
    parts = [plan.acknowledgement]
    if plan.changed_constraints:
        parts.append("Теперь ищем так: " + "; ".join(plan.changed_constraints) + ".")
    if plan.result_summary:
        parts.append(plan.result_summary)
    if _is_selected_lot_card_response(plan, cards):
        card = cards[0]
        lots = tuple(card.lot_examples[:2])
        parts.append(_selected_lot_examples_block(lots))
        final_question = _selected_lot_examples_question(lots)
        body = "\n\n".join(x.strip() for x in parts if x and x.strip())
        rendered = _normalize_punctuation(f"{body}\n\n{final_question}")
        return safe_client_output(
            rendered,
            "Не получилось корректно сформулировать ответ. Повторить последний вопрос?",
        )
    if cards:
        used: set[str] = set()
        viewpoint = plan.viewpoint or plan.answer_kind or "life"
        parts.extend(_card_block(i, card, viewpoint, plan.base_viewpoint, used, cards, plan.scenario_needs) for i, card in enumerate(cards, 1))
    if plan.caveat:
        parts.append(plan.caveat)
    final_question = plan.final_question.rstrip("?.!") + "?"
    body = "\n\n".join(x.strip() for x in parts if x and x.strip())
    rendered = _normalize_punctuation(f"{body}\n\n{final_question}" if body else final_question)
    return safe_client_output(
        rendered,
        "Не получилось корректно сформулировать ответ. Повторить последний вопрос?",
    )


def _is_selected_lot_card_response(plan: ResponsePlan, cards: tuple[OptionCard, ...]) -> bool:
    return bool(
        len(cards) == 1
        and cards[0].lot_examples
        and plan.answer_kind in {"selected_object", "selected", "answer_selected"}
    )


def _selected_lot_examples_block(lots: tuple[Any, ...]) -> str:
    lines = ["Нашла два предложения с отделкой." if len(lots) >= 2 else "Нашла одно конкретное предложение."]
    for lot in lots:
        lines.append(_lot_example_sentence(lot))
    if len(lots) >= 2:
        comparison = _lot_examples_comparison(lots[0], lots[1])
        if comparison:
            lines.append(comparison)
    return "\n".join(lines)


def _lot_example_sentence(lot: Any) -> str:
    label = _lot_label(lot)
    details: list[str] = []
    area = _format_lot_area(getattr(lot, "area_m2", None))
    if area:
        details.append(area)
    floor = getattr(lot, "floor", None)
    floors_total = getattr(lot, "floors_total", None)
    if floor is not None and floors_total is not None:
        details.append(f"{floor}-й этаж из {floors_total}")
    elif floor is not None:
        details.append(f"{floor}-й этаж")
    price = getattr(lot, "full_price", None)
    if isinstance(price, (int, float)) and not isinstance(price, bool):
        details.append("стоимость " + _format_exact_rubles(price))
    renovation = _format_finishing(getattr(lot, "renovation", None))
    if renovation:
        details.append(renovation)
    house = getattr(lot, "house_name", None)
    if house:
        details.append(f"корпус {house}")
    return f"{label.capitalize()}: " + ", ".join(details) + "." if details else f"{label.capitalize()}."


def _lot_label(lot: Any) -> str:
    value = str(getattr(lot, "rooms", None) or "вариант").strip().casefold().replace("ё", "е")
    labels = {
        "s": "студия",
        "studio": "студия",
        "студии": "студия",
        "студия": "студия",
        "1": "однокомнатная квартира",
        "2": "двухкомнатная квартира",
        "3": "трёхкомнатная квартира",
        "4": "четырёхкомнатная квартира",
    }
    return labels.get(value, str(getattr(lot, "rooms", None) or "вариант"))


def _lot_accusative_label(lot: Any) -> str:
    labels = {
        "студия": "студию",
        "однокомнатная квартира": "однокомнатную квартиру",
        "двухкомнатная квартира": "двухкомнатную квартиру",
        "трёхкомнатная квартира": "трёхкомнатную квартиру",
        "четырёхкомнатная квартира": "четырёхкомнатную квартиру",
    }
    label = _lot_label(lot)
    return labels.get(label, label)


def _format_exact_rubles(value: int | float) -> str:
    return f"{int(round(value)):,} рублей".replace(",", " ")


def _format_lot_area(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return f"{value:g}".replace(".", ",") + " м²"
    return None


def _lot_disambiguated_label(lot: Any, *, accusative: bool = False) -> str:
    label = _lot_accusative_label(lot) if accusative else _lot_label(lot)
    area = _format_lot_area(getattr(lot, "area_m2", None))
    return f"{label} площадью {area}" if area else label


def _lot_examples_comparison(first: Any, second: Any) -> str:
    parts: list[str] = []
    same_format = _lot_label(first) == _lot_label(second)
    first_price = getattr(first, "full_price", None)
    second_price = getattr(second, "full_price", None)
    if isinstance(first_price, (int, float)) and isinstance(second_price, (int, float)) and first_price != second_price:
        cheaper_lot = first if first_price < second_price else second
        difference = abs(first_price - second_price)
        cheaper_label = _lot_disambiguated_label(cheaper_lot, accusative=True) if same_format else _lot_accusative_label(cheaper_lot)
        parts.append(f"Если хочется начать с меньшего бюджета, стоит посмотреть {cheaper_label}: разница в цене — {_format_exact_rubles(difference)}")
    first_area = getattr(first, "area_m2", None)
    second_area = getattr(second, "area_m2", None)
    if isinstance(first_area, (int, float)) and isinstance(second_area, (int, float)) and first_area != second_area:
        larger_lot = first if first_area > second_area else second
        difference = abs(first_area - second_area)
        larger_label = _lot_disambiguated_label(larger_lot) if same_format else _lot_label(larger_lot)
        parts.append(f"Если важнее дополнительное пространство — {larger_label}: она больше на {_format_lot_area(difference)}")
    return ". ".join(parts) + "." if parts else ""


def _selected_lot_examples_question(lots: tuple[Any, ...]) -> str:
    if len(lots) >= 2:
        if _lot_label(lots[0]) == _lot_label(lots[1]):
            first = _lot_disambiguated_label(lots[0], accusative=True)
            second = _lot_disambiguated_label(lots[1], accusative=True)
            if first == second:
                return "Какой вариант показать подробнее: первый или второй?"
            return f"Какой вариант показать подробнее: {first} или {second}?"
        return f"Какой вариант показать подробнее: {_lot_accusative_label(lots[0])} или {_lot_accusative_label(lots[1])}?"
    return f"Показать {_lot_accusative_label(lots[0])} подробнее?" if lots else "Что показать подробнее?"


def _normalize_punctuation(text: str) -> str:
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"([!?]){2,}", r"\1", text)
    text = re.sub(r"\.([!?])", r"\1", text)
    return text
