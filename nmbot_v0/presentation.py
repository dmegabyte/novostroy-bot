from __future__ import annotations

import re
from typing import Any, Mapping

from .contracts import LotExample, OptionCard

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


def _exact_money(value: int | float) -> str:
    return f"{int(value):,} ₽".replace(",", " ")


def _format_area_m2(value: int | float) -> str:
    rendered = f"{float(value):.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{rendered} м²"


def _format_lot_rooms(value: object | None) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().casefold()
    names = {
        "0": "студия",
        "ст": "студия",
        "студия": "студия",
        "1": "однокомнатная квартира",
        "2": "двухкомнатная квартира",
        "3": "трёхкомнатная квартира",
        "4": "четырёхкомнатная квартира",
    }
    return names.get(text, str(value).strip())


def _format_selected_lot(index: int, lot: LotExample) -> str | None:
    details: list[str] = []
    rooms = _format_lot_rooms(lot.rooms)
    if rooms:
        details.append(rooms)
    if isinstance(lot.area_m2, (int, float)) and not isinstance(lot.area_m2, bool):
        details.append(_format_area_m2(lot.area_m2))
    if isinstance(lot.floor, int) and isinstance(lot.floors_total, int):
        details.append(f"{lot.floor} этаж из {lot.floors_total}")
    elif isinstance(lot.floor, int):
        details.append(f"{lot.floor} этаж")
    if isinstance(lot.full_price, (int, float)) and not isinstance(lot.full_price, bool):
        details.append(f"полная цена {_exact_money(lot.full_price)}")
    finishing = _format_finishing(lot.renovation)
    if finishing:
        details.append(f"отделка — {finishing}")
    if lot.house_name:
        details.append(f"корпус/дом — {lot.house_name}")
    if not details:
        return None
    return f"Квартира {index}: " + ", ".join(details) + "."


def _selected_lot_comparison(lots: tuple[LotExample, ...]) -> str:
    if len(lots) != 2:
        return ""
    first, second = lots
    first_label, second_label = "первая", "вторая"
    parts: list[str] = []
    if isinstance(first.full_price, (int, float)) and isinstance(second.full_price, (int, float)) and first.full_price != second.full_price:
        cheaper = first_label if first.full_price < second.full_price else second_label
        diff = abs(int(first.full_price) - int(second.full_price))
        parts.append(f"{cheaper} дешевле на {_exact_money(diff)}")
    if isinstance(first.area_m2, (int, float)) and isinstance(second.area_m2, (int, float)) and first.area_m2 != second.area_m2:
        bigger = first_label if first.area_m2 > second.area_m2 else second_label
        diff_area = abs(float(first.area_m2) - float(second.area_m2))
        parts.append(f"{bigger} больше на {_format_area_m2(diff_area)}")
    if not parts:
        return ""
    return "Если сравнивать только эти два подтверждённых объявления, " + "; ".join(parts) + "."


def render_selected_lot_lines(card: OptionCard) -> tuple[str, ...]:
    """Render up to two MCP-normalized lot examples for selected V0 card."""

    lots = tuple(card.lot_examples[:2])
    rendered = tuple(
        (lot, line)
        for index, lot in enumerate(lots, 1)
        if (line := _format_selected_lot(index, lot))
    )
    if not rendered:
        return ()
    lines = ["По объявлениям вижу такие подтверждённые квартиры:"]
    lines.extend(line for _, line in rendered)
    comparison = _selected_lot_comparison(tuple(lot for lot, _ in rendered))
    if comparison:
        lines.append(comparison)
    return tuple(lines)


def _shared_scalar(cards: tuple[OptionCard, ...], getter) -> object | None:
    if len(cards) < 2:
        return None
    values = [getter(card) for card in cards]
    if any(value in (None, "", ()) for value in values):
        return None
    first = values[0]
    return first if all(value == first for value in values[1:]) else None


def build_shortlist_comparison_context(cards: tuple[OptionCard, ...], viewpoint: str = "life") -> dict[str, Any]:
    """Derive deterministic shared facts used by V0 shortlist presentation."""

    safe_cards = tuple(cards[:3])
    if len(safe_cards) < 2:
        return {"summary": "", "shared_fields": frozenset(), "shared_infrastructure": frozenset(), "used_benefit_keys": frozenset()}

    shared_fields: set[str] = set()
    summary_parts: list[str] = []
    used_benefit_keys: set[str] = set()

    scalar_specs = (
        ("location", lambda card: card.location, lambda value: f"одна локация — {value}"),
        ("price", lambda card: card.price_min if isinstance(card.price_min, (int, float)) and not isinstance(card.price_min, bool) else None, lambda value: f"одинаковая стартовая цена — {_exact_money(value)}"),
        ("finishing", lambda card: _format_finishing(card.finishing), lambda value: str(value)),
        ("ready", lambda card: _format_ready(card.ready), lambda value: str(value)),
        ("metro", lambda card: card.metro, lambda value: f"метро {value}"),
        ("developer", lambda card: card.developer, lambda value: f"застройщик {value}"),
        ("property_class", lambda card: _format_property_class(card.property_class), lambda value: str(value)),
        ("room_formats", lambda card: _format_room_formats(card.room_formats), lambda value: str(value)),
    )
    for field_id, getter, formatter in scalar_specs:
        value = _shared_scalar(safe_cards, getter)
        if value is not None:
            shared_fields.add(field_id)
            summary_parts.append(formatter(value))

    infra_sets = [{str(item).strip().casefold() for item in card.infrastructure if str(item).strip()} for card in safe_cards]
    common_infra = set.intersection(*infra_sets) if infra_sets and all(infra_sets) else set()
    ordered_common_infra = tuple(item for item in safe_cards[0].infrastructure if str(item).strip().casefold() in common_infra)
    if ordered_common_infra:
        summary_parts.append("рядом указаны " + ", ".join(ordered_common_infra[:3]))
        if any(re.search(r"школ|сад|дет", item, re.I) for item in ordered_common_infra):
            used_benefit_keys.add("family_daily")
        if any(re.search(r"двор без машин", item, re.I) for item in ordered_common_infra):
            used_benefit_keys.add("family_safe_yard")
        if any(re.search(r"охран|безопас", item, re.I) for item in ordered_common_infra):
            used_benefit_keys.add("family_safe")
        if any(re.search(r"парк|вода|спорт", item, re.I) for item in ordered_common_infra):
            used_benefit_keys.add("family_walk")
        used_benefit_keys.add("life_infra")

    if "ready" in shared_fields:
        used_benefit_keys.update({"family_ready", "life_ready", "rent_ready", "invest_ready"})
    if "finishing" in shared_fields:
        used_benefit_keys.update({"family_finish", "life_finish", "rent_finish", "invest_finish"})
    if "metro" in shared_fields:
        used_benefit_keys.update({"rent_metro", "life_metro"})
    if "location" in shared_fields:
        used_benefit_keys.add("life_location")
        for card in safe_cards:
            used_benefit_keys.update({f"invest_location:{card.location.casefold()}", f"rent_location:{card.location.casefold()}"})

    summary = "Общее для всех вариантов: " + ", ".join(summary_parts) + "." if summary_parts else ""
    return {
        "summary": summary,
        "shared_fields": frozenset(shared_fields),
        "shared_infrastructure": frozenset(common_infra),
        "used_benefit_keys": frozenset(used_benefit_keys),
        "viewpoint": viewpoint,
    }


def _format_ready(value: str | None) -> str | None:
    text = str(value or "").strip()
    month = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])", text)
    if month:
        month_names = {
            "01": "январе", "02": "феврале", "03": "марте", "04": "апреле",
            "05": "мае", "06": "июне", "07": "июле", "08": "августе",
            "09": "сентябре", "10": "октябре", "11": "ноябре", "12": "декабре",
        }
        return f"сдача в {month_names[month.group(2)]} {month.group(1)} года"
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


def _format_room_formats(values: tuple[str, ...]) -> str | None:
    normalized: list[str] = []
    names = {"1": "однокомнатные", "2": "двухкомнатные", "3": "трёхкомнатные", "4": "четырёхкомнатные"}
    for value in values:
        match = re.fullmatch(r"\s*([1-4])\s*(?:кв\.?|комн(?:атные)?)?\s*", str(value), flags=re.IGNORECASE)
        label = names.get(match.group(1)) if match else str(value).strip()
        if label and label not in normalized:
            normalized.append(label)
    return ", ".join(normalized[:3]) or None

def _has_ready(card: OptionCard) -> bool:
    return bool(card.ready and re.search(r"сдан|готов", str(card.ready), re.I))


def _infra(card: OptionCard, *needles: str) -> bool:
    hay = " ".join(card.infrastructure).casefold()
    return any(needle in hay for needle in needles)


def _headline_details(card: OptionCard, viewpoint: str, comparison_context: Mapping[str, Any] | None = None) -> str:
    context = comparison_context or {}
    shared_fields = set(context.get("shared_fields", ()))
    shared_infrastructure = set(context.get("shared_infrastructure", ()))
    prefix = "Ближайший вариант: " if card.is_near else ""
    details = [card.location if "location" not in shared_fields else None, _format_card_price(card) if "price" not in shared_fields else None]
    finishing = _format_finishing(card.finishing)
    ready = _format_ready(card.ready)
    if finishing and "finishing" not in shared_fields:
        details.append(finishing)
    if ready and "ready" not in shared_fields:
        details.append(ready)
    if card.area:
        details.append(card.area)
    if card.metro and "metro" not in shared_fields:
        details.append(f"метро {card.metro}")
    if card.developer and "developer" not in shared_fields:
        details.append(f"застройщик {card.developer}")
    property_class = _format_property_class(card.property_class)
    if property_class and "property_class" not in shared_fields:
        details.append(property_class)
    room_formats = _format_room_formats(card.room_formats)
    if room_formats and "room_formats" not in shared_fields:
        details.append(room_formats)
    if card.infrastructure:
        unique_infra = [item for item in card.infrastructure if str(item).strip().casefold() not in shared_infrastructure]
        if unique_infra:
            details.append("рядом: " + ", ".join(unique_infra[:3]))
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
        return f"По оплате вижу условия: {finance}; по базовой пользе лучше сравнить этот вариант с вашими условиями."
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


def _comparison_fallback(card: OptionCard, cards: tuple[OptionCard, ...]) -> str:
    prices = [(item, item.price_min) for item in cards if isinstance(item.price_min, (int, float)) and not isinstance(item.price_min, bool)]
    if len(prices) > 1 and len({price for _item, price in prices}) > 1 and isinstance(card.price_min, (int, float)) and not isinstance(card.price_min, bool):
        ordered = sorted(prices, key=lambda item: (item[1], item[0].name))
        rank = next((index for index, (item, _price) in enumerate(ordered) if item is card), None)
        if rank == 0:
            return "Среди этих вариантов здесь самый низкий подтверждённый старт по цене."
        if rank is not None and 0 < rank < len(ordered) - 1:
            nearest = min((abs(card.price_min - price) for item, price in prices if item is not card), default=0)
            return f"По стартовой цене это середина подборки; ближайший вариант отличается на {_exact_money(nearest)}."
        if rank == len(ordered) - 1:
            if card.location and len({item.location for item in cards if item.location}) > 1:
                return f"Ценового преимущества здесь нет; смысл выбора — если вам нужна локация {card.location}."
            return "По стартовой цене это самый дорогой вариант; отдельного подтверждённого преимущества сейчас не видно."
    if len(prices) == len(cards) and len({price for _item, price in prices}) == 1:
        if card.location and len({item.location for item in cards if item.location}) > 1:
            return f"Отдельного преимущества по подтверждённым фактам нет; выбор зависит от нужной локации — {card.location}."
        return "Отдельного преимущества по подтверждённым фактам сейчас не видно."
    return ""


def _has_distinct_non_price_fact(card: OptionCard, cards: tuple[OptionCard, ...], comparison_context: Mapping[str, Any] | None) -> bool:
    context = comparison_context or {}
    shared_fields = set(context.get("shared_fields", ()))
    shared_infrastructure = set(context.get("shared_infrastructure", ()))
    scalar_values = {
        "finishing": _format_finishing(card.finishing),
        "ready": _format_ready(card.ready),
        "metro": card.metro,
        "developer": card.developer,
        "property_class": _format_property_class(card.property_class),
        "room_formats": _format_room_formats(card.room_formats),
        "area": card.area,
        "mortgage_terms": card.mortgage_terms,
        "discount": card.discount,
    }
    if any(value not in (None, "", ()) and field_id not in shared_fields for field_id, value in scalar_values.items()):
        return True
    return any(str(item).strip().casefold() not in shared_infrastructure for item in card.infrastructure)


def _has_useful_price_comparison(cards: tuple[OptionCard, ...]) -> bool:
    prices = [card.price_min for card in cards if isinstance(card.price_min, (int, float)) and not isinstance(card.price_min, bool)]
    return len(prices) == len(cards) and len(cards) > 1


def shortlist_level_sparse_note(cards: tuple[OptionCard, ...], comparison_context: Mapping[str, Any] | None) -> str:
    safe_cards = tuple(cards[:3])
    if len(safe_cards) < 2 or _has_useful_price_comparison(safe_cards):
        return ""
    if any(_has_distinct_non_price_fact(card, safe_cards, comparison_context) for card in safe_cards):
        return ""
    return "По этим ЖК пока подтверждены только общие характеристики; цены и планировки лучше сравнить отдельно."


def _card_block(index: int, card: OptionCard, viewpoint: str, base_viewpoint: str | None, used: set[str], cards: tuple[OptionCard, ...], scenario_needs: tuple[str, ...] = (), comparison_context: Mapping[str, Any] | None = None) -> str:
    headline = f"{index}. {_headline_details(card, viewpoint, comparison_context)}"
    comparison = _comparison_fallback(card, cards)
    if comparison_context and not _has_distinct_non_price_fact(card, cards, comparison_context):
        benefit = comparison
    else:
        benefit = _multi_scenario_benefit_reason(card, viewpoint, base_viewpoint, used, cards, scenario_needs, index)
    if not benefit and len(cards) > 1:
        benefit = comparison
    return f"{headline}\n{benefit}" if benefit else headline


def render_grounded_card_block(
    index: int,
    card: OptionCard,
    *,
    viewpoint: str = "life",
    base_viewpoint: str | None = None,
    used_benefits: set[str] | None = None,
    cards: tuple[OptionCard, ...] = (),
    scenario_needs: tuple[str, ...] = (),
    comparison_context: Mapping[str, Any] | None = None,
) -> str:
    """Public additive wrapper for deterministic, OptionCard-only presentation.

    Existing V2 rendering still calls the private helpers directly. This wrapper
    exposes the same grounded card/benefit machinery to V0 without adding another
    prose model path or changing V2 output.
    """

    safe_used = used_benefits if used_benefits is not None else set()
    safe_cards = tuple(cards) or (card,)
    return _card_block(index, card, viewpoint, base_viewpoint, safe_used, safe_cards, scenario_needs, comparison_context)


def selected_object_grounded_acknowledgement(card: OptionCard, *, viewpoint: str = "life") -> str:
    """Public additive wrapper around V2 selected-object grounded acknowledgement."""

    return _selected_object_acknowledgement(card, viewpoint)

def _normalize_punctuation(text: str) -> str:
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"([!?]){2,}", r"\1", text)
    text = re.sub(r"\.([!?])", r"\1", text)
    return text
