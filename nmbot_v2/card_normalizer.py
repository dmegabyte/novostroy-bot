from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .contracts import LotExample, OptionCard, SearchResult


@dataclass(frozen=True)
class MissingSummary:
    """Canonical client-safe missing-data categories.

    This is the single boundary between MCP wire field names/prose and the
    renderer.  Raw fields such as ``mortgage_calc`` or model prose never cross
    this object; only stable category tokens do.
    """

    categories: tuple[str, ...] = ()

    @classmethod
    def from_wire(cls, values: Any) -> "MissingSummary":
        return cls(_missing_categories(values))


MISSING_LABELS: dict[str, str] = {
    "location": "локации",
    "budget": "бюджета",
    "rooms": "комнатности",
    "family_infrastructure": "семейной инфраструктуры",
    "walk_infrastructure": "инфраструктуры для прогулок",
    "safety_infrastructure": "части данных по безопасности",
    "finance": "условий оплаты",
    "sales": "продаж по ЕГРН",
    "ads": "количества объявлений",
    "readiness": "срока готовности",
    "finishing": "отделки",
    "details": "части уточняющих данных",
}

REGION_NAMES = {"msk": "Москва", "mo": "Московская область", "newmsk": "Новая Москва"}
MAX_DYNAMIC_SCALAR_TEXT = 120
MISSING_DYNAMIC_RE = re.compile(
    r"("
    r"не\s+предоставлен|не\s+представлен|не\s+указан|не\s+найден|не\s+подтвержд|"
    r"нет\s+(?:данн|информац|сведен)|отсутств|уточня|недоступ|unknown|unavailable|"
    r"not\s+(?:available|provided|specified|found|confirmed)|no\s+(?:data|information|info)|"
    r"none|null|n/?a|not\s+in\s+structured\s+form"
    r")",
    re.I,
)
PARKING_MISSING_RE = re.compile(
    r"(нет\s+(?:паркинг|парков|машино|мест)|паркинг\s+(?:не\s+)?(?:предоставлен|предусмотрен|найден|подтвержд)|"
    r"parking\s+(?:not\s+)?(?:available|provided|found|confirmed)|no\s+parking)",
    re.I,
)
INVENTORY_POINTER_RE = re.compile(
    r"(?:данн(?:ые|ых)|информац(?:ия|ии))\s+доступн(?:а|ы)?\s+через\s+"
    r"(?:поиск|запрос)|available\s+(?:through|via)\s+(?:search|lookup)",
    re.I,
)
PROPERTY_CLASS = {
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
FINISHING = {
    "white box": "предчистовая отделка",
    "white_box": "предчистовая отделка",
    "вайт бокс": "предчистовая отделка",
    "предчистовая": "предчистовая отделка",
    "предчистовая отделка": "предчистовая отделка",
    "есть отделка": "с отделкой",
    "с отделкой": "с отделкой",
    "отделка": "с отделкой",
    "без отделки": "без отделки",
}
READY = {
    "ready": "сдан",
    "delivered": "сдан",
    "сдан": "сдан",
    "сдано": "сдан",
    "готов": "сдан",
    "готово": "сдан",
    "готовый": "сдан",
}


def normalize_search_result(output: Mapping[str, Any] | None) -> SearchResult:
    source = output if isinstance(output, Mapping) else {}
    return SearchResult(
        facts=tuple(normalize_card(item) for item in _dict_items(source.get("facts"))[:3]),
        near=tuple(normalize_card(item, is_near=True) for item in _dict_items(source.get("near"))[:3]),
        missing=MissingSummary.from_wire(source.get("missing")).categories,
        params=dict(source.get("params") if isinstance(source.get("params"), Mapping) else {}),
        summary=str(source.get("summary")) if source.get("summary") else None,
    )


def normalize_card(item: Mapping[str, Any], *, is_near: bool | None = None) -> OptionCard:
    raw = item if isinstance(item, Mapping) else {}
    price, price_min = _price(raw)
    explicit_formats = _explicit_formats(raw.get("room_formats"))
    if explicit_formats:
        room_formats = explicit_formats
    else:
        collected = tuple(dict.fromkeys(
            (*_room_formats(raw.get("rooms")), *_room_formats(raw.get("apartment_types")), *_room_formats(raw.get("ads")))
        ))
        order = {"студии": 0, "1": 1, "2": 2, "3": 3, "4": 4}
        room_formats = tuple(sorted(collected, key=lambda value: (order.get(value, 99), value)))[:5]
    primary_room = _primary_room(raw.get("rooms")) if len(room_formats) <= 1 else None
    normalized = {
        "name": _text(raw.get("name") or raw.get("alias") or raw.get("title") or raw.get("label") or "Вариант") or "Вариант",
        "district": _district(raw.get("district")),
        "location": _location(raw),
        "price": price,
        "price_min": price_min,
        "rooms": primary_room,
        "finishing": _finishing(raw.get("finishing")) or _finishing(_first_from_nested(raw, "renovation")) or _finishing(_first_from_nested(raw, "finishing")),
        "area": _area(raw),
        "ready": _ready(raw),
        "metro": _metro(raw.get("metro") or raw.get("property_metro")),
        "developer": _developer(raw.get("developer")),
        "property_class": _property_class(raw.get("new_building_class") or raw.get("property_class") or raw.get("class") or raw.get("building_type")),
        "ecology_rating": _ecology_rating(raw),
        "infrastructure": _infrastructure(raw),
        "daily_services": _daily_services(raw),
        "healthcare": _healthcare(raw),
        "ads_count": _ads_count(raw),
        "sales_count": _sales_count(raw),
        "sales_date": _text(raw.get("sales_date")),
        "discount": _discount_text(raw),
        "parking": _parking(raw),
        "parking_price": _parking_price(raw),
        "parking_inventory": _parking_inventory(raw),
        "apartment_inventory": _apartment_inventory(raw),
        "mortgage_terms": _finance_text(raw),
        "mortgage_rate": _mortgage_rate(raw),
        "mortgage_down_payment": _mortgage_down_payment(raw),
        "mortgage_term": _mortgage_term(raw),
        "installment_months": _installment_months(raw),
        "transport_access": _transport_access(raw),
        "room_prices": _room_prices(raw),
        "price_square": _price_square(raw),
        "recurring_costs": _recurring_costs(raw),
        "purchase_terms": _purchase_terms(raw),
        "building_profile": _building_profile(raw),
        "property_formats": _property_formats(raw),
        "room_formats": room_formats,
        "lot_examples": _lot_examples(raw),
        "why_close": _text(raw.get("why_close")),
        "is_near": bool(raw.get("is_near", False)) if is_near is None else is_near,
    }
    return OptionCard.from_dict({k: v for k, v in normalized.items() if v not in (None, (), "")})


def missing_text(categories: Iterable[str]) -> str:
    labels = [MISSING_LABELS.get(str(item), MISSING_LABELS["details"]) for item in categories]
    labels = list(dict.fromkeys(label for label in labels if label))
    if len(labels) > 4:
        joined = " ".join(labels)
        groups: list[str] = []
        if re.search(r"оплат|ипот|скид|рассроч", joined):
            groups.append(MISSING_LABELS["finance"])
        if re.search(r"семейн|инфраструктур|прогул|безопас", joined):
            groups.append("части инфраструктуры")
        groups.append(MISSING_LABELS["details"])
        labels = list(dict.fromkeys(groups))
    return ", ".join(labels)


def _dict_items(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = " ".join(str(value).strip().split())
    return text or None


def _safe_dynamic_text(value: Any, *, max_len: int = MAX_DYNAMIC_SCALAR_TEXT) -> str | None:
    if value is None or isinstance(value, bool) or isinstance(value, (Mapping, list, tuple, set)):
        return None
    text = " ".join(str(value).strip().split())
    if not text or len(text) > max_len or any(token in text for token in ("{", "}", "[", "]")):
        return None
    if MISSING_DYNAMIC_RE.search(text):
        return None
    return text


def _positive_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if value > 0 else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _text_has_positive_number(text: str) -> bool:
    # Accept decimal comma/dot and grouped prices, but not placeholders with no
    # price evidence. Any positive numeric token is enough; zero-only text is not.
    matches = re.findall(r"\d+(?:[\s\u00a0]\d{3})*(?:[,.]\d+)?|\d+", text)
    for raw in matches:
        cleaned = raw.replace(" ", "").replace("\u00a0", "").replace(",", ".")
        try:
            if float(cleaned) > 0:
                return True
        except ValueError:
            continue
    return False


def _safe_price_scalar(value: Any) -> str | int | float | None:
    number = _positive_number(value)
    if number is not None:
        return number
    text = _safe_dynamic_text(value)
    if not text or not _text_has_positive_number(text):
        return None
    return text


def _safe_inventory_scalar(value: Any) -> str | int | None:
    integer = _positive_int(value)
    if integer is not None:
        return integer
    text = _safe_dynamic_text(value)
    if not text:
        return None
    # A pointer to another lookup is not inventory evidence.  Treat it as
    # missing so the selected-object flow can retry or offer an operator
    # instead of presenting internal MCP guidance as an availability fact.
    if INVENTORY_POINTER_RE.search(text):
        return None
    if re.fullmatch(r"0+(?:[,.]0+)?", text):
        return None
    return text


def safe_dynamic_price_scalar(value: Any) -> str | int | float | None:
    return _safe_price_scalar(value)


def safe_dynamic_inventory_scalar(value: Any) -> str | int | None:
    return _safe_inventory_scalar(value)


def safe_dynamic_text(value: Any) -> str | None:
    return _safe_dynamic_text(value)


def _machine(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _price(raw: Mapping[str, Any]) -> tuple[str | None, int | None]:
    numeric: int | None = None
    for key in ("price_min", "min_price", "novos.min_price", "price1", "price_s", "price_n"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            candidate = int(value)
            if candidate > 0:
                numeric = candidate
                break
    # A positive structured minimum is the canonical entry price.  Textual
    # project ranges may describe another slice of inventory and must not
    # override the value already validated by the search contract.
    if numeric is not None:
        return None, numeric
    for key in ("price", "price_range", "price_text", "ads.fullprice", "max_price"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value <= 0:
            continue
        if value not in (None, "", "0", "0.0"):
            return str(value), numeric
    nested = _first_from_nested(raw, "fullprice") or _first_from_nested(raw, "price")
    if isinstance(nested, (int, float)) and not isinstance(nested, bool):
        return (None, int(nested)) if nested > 0 else (None, None)
    return (str(nested), None) if nested not in (None, "") else (None, None)


def _location(raw: Mapping[str, Any]) -> str | None:
    value = raw.get("location") or raw.get("location_id")
    if isinstance(value, list):
        parts = [_text(item) for item in value]
        return ", ".join(item for item in parts if item) or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = None
    text = _text(value)
    if text:
        return text
    district = _machine(raw.get("district"))
    return REGION_NAMES.get(district)


def _district(value: Any) -> str | None:
    normalized = _machine(value)
    return normalized if normalized in REGION_NAMES else None


def _property_class(value: Any) -> str | None:
    if isinstance(value, (int, float, bool)):
        return None
    normalized = _machine(value)
    if normalized in PROPERTY_CLASS:
        return PROPERTY_CLASS[normalized]
    text = _text(value)
    if text and re.fullmatch(r"[a-z0-9_.-]+", text, flags=re.I):
        return None
    return text


def _ecology_rating(raw: Mapping[str, Any]) -> str | int | float | None:
    value = raw.get("ecology_rating")
    if value is None and isinstance(raw.get("location_2"), Mapping):
        value = raw["location_2"].get("ecology_rating")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = _text(value)
    return text if text and not re.fullmatch(r"[a-z_]+", text, flags=re.I) else None


def _finishing(value: Any) -> str | None:
    if value is True:
        return "с отделкой"
    if value is False or value is None or isinstance(value, (int, float)):
        return None
    normalized = str(value).strip().casefold().replace("-", " ").replace("_", " ")
    return FINISHING.get(normalized, _text(value) if not re.fullmatch(r"[a-z0-9_.-]+", str(value), flags=re.I) else None)


def _ready(raw: Mapping[str, Any]) -> str | None:
    for key in ("ready", "state", "status"):
        value = raw.get(key)
        if value not in (None, ""):
            if isinstance(value, (bool, int, float)):
                continue
            text = _text(value)
            if text:
                # MCP can expose compact numeric status enums as strings.
                # They are wire codes, not a client-facing readiness date.
                if re.fullmatch(r"[0-4]", text):
                    continue
                return READY.get(text.casefold().replace("ё", "е"), text)
    delivered = raw.get("delivered")
    if delivered is True or (isinstance(delivered, (int, float)) and not isinstance(delivered, bool) and int(delivered) == 1):
        return "сдан"
    quarter = _text(raw.get("ready_quarter"))
    # A bare quarter number is another wire enum, not a meaningful deadline.
    return None if quarter and re.fullmatch(r"[1-4]", quarter) else quarter


def _room_formats(value: Any) -> tuple[str, ...]:
    out: list[str] = []
    for token in _flatten_rooms(value):
        text = _room_label(token)
        if text and text not in out:
            out.append(text)
    return tuple(out[:5])


def _explicit_formats(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    out = [str(item).strip() for item in value if str(item or "").strip()]
    return tuple(dict.fromkeys(out[:5]))


def _primary_room(value: Any) -> int | str | None:
    formats = _room_formats(value)
    return formats[0] if len(formats) == 1 else None


def _flatten_rooms(value: Any) -> list[Any]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, Mapping):
        return sum((_flatten_rooms(v) for k, v in value.items() if str(k) in {"rooms", "room", "available", "types", "values", "apartment_types"}), [])
    if isinstance(value, (list, tuple, set)):
        return sum((_flatten_rooms(v) for v in value), [])
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,|;/]", value) if part.strip()]
    return [value]


def _room_label(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value)) if float(value).is_integer() else None
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.casefold().replace("ё", "е")
    if lowered in {"s", "st", "studio", "studios", "ст", "студия", "студии", "студию"} or "студи" in lowered:
        return "студии"
    match = re.search(r"(?<!\d)([1-4])(?!\d)", lowered)
    return match.group(1) if match else text


def _lot_examples(raw: Mapping[str, Any]) -> tuple[LotExample, ...]:
    ads = raw.get("ads")
    if isinstance(ads, Mapping):
        rows = [ads]
    elif isinstance(ads, list):
        rows = [item for item in ads if isinstance(item, Mapping)]
    else:
        rows = []
    if not rows:
        return ()
    houses = _houses_by_id(raw.get("house"))
    out: list[LotExample] = []
    for ad in rows:
        lot = _lot_example(ad, houses)
        if lot is not None:
            out.append(lot)
        if len(out) >= 2:
            break
    return tuple(out)


def _lot_example(ad: Mapping[str, Any], houses: Mapping[str, str]) -> LotExample | None:
    full_price = _positive_intish(ad.get("fullprice"))
    area = _positive_float(ad.get("area"))
    rooms = _lot_room_label(ad.get("rooms"))
    floor = _positive_intish(ad.get("floor"))
    floors_total = _positive_intish(ad.get("floors_total"))
    renovation = _finishing(ad.get("renovation")) or _text(ad.get("renovation"))
    house_id = ad.get("house_id") if ad.get("house_id") not in (None, "") else ad.get("house") if ad.get("house") not in (None, "") else None
    house_name = houses.get(str(house_id)) if house_id is not None else None
    lot_id = ad.get("id") if ad.get("id") not in (None, "") else None
    status = ad.get("status") if ad.get("status") not in (None, "") else None
    state = ad.get("state") if ad.get("state") not in (None, "") else None
    source = _text(ad.get("source")) or "ads"
    living_space = _positive_float(ad.get("living_space") or ad.get("life_area"))
    kitchen_area = _positive_float(ad.get("kitchen_area") or ad.get("kitchen_space"))
    balcony = _safe_lot_feature(ad.get("balcony") or ad.get("loggia") or ad.get("terrace"))
    bathroom = _safe_lot_feature(ad.get("bathroom") or ad.get("bathrooms"))
    ceiling_height = _safe_dynamic_text(ad.get("ceiling_height")) or _positive_float(ad.get("ceiling_height"))
    window_view = _safe_lot_feature(ad.get("window_view") or ad.get("view"))
    layout_features = _layout_features(ad)
    if not any(value is not None and value != () for value in (lot_id, rooms, area, floor, floors_total, full_price, renovation, status, state, house_id, house_name, living_space, kitchen_area, balcony, bathroom, ceiling_height, window_view, layout_features)):
        return None
    return LotExample(
        id=lot_id,
        rooms=rooms,
        area_m2=area,
        floor=floor,
        floors_total=floors_total,
        full_price=full_price,
        renovation=renovation,
        status=status,
        house_id=house_id,
        house_name=house_name,
        source=source,
        living_space=living_space,
        kitchen_area=kitchen_area,
        balcony=balcony,
        bathroom=bathroom,
        ceiling_height=ceiling_height,
        window_view=window_view,
        layout_features=layout_features,
        state=state,
    )


def _safe_lot_feature(value: Any) -> str | None:
    if value is True:
        return "есть"
    if value is False:
        return None
    return _safe_dynamic_text(value, max_len=80)


def _layout_features(ad: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    for key, label in (("balcony", "балкон"), ("loggia", "лоджия"), ("terrace", "терраса")):
        if ad.get(key) is True or _binary_flag_enabled(ad.get(key)):
            out.append(label)
    for source_key in ("apartment_types", "ads_add"):
        for text in _flatten_text_values(ad.get(source_key)):
            low = text.casefold().replace("ё", "е")
            if any(token in low for token in ("балкон", "лодж", "террас", "сануз", "гардероб", "кладов")):
                out.append(text[:80])
    return tuple(dict.fromkeys(out[:5]))


def _houses_by_id(value: Any) -> dict[str, str]:
    rows = value if isinstance(value, list) else [value] if isinstance(value, Mapping) else []
    out: dict[str, str] = {}
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        house_id = item.get("id") if item.get("id") not in (None, "") else item.get("house_id")
        name = _text(item.get("name") or item.get("title"))
        if house_id is not None and name:
            out[str(house_id)] = name
    return out


def _lot_room_label(value: Any) -> str | int | None:
    label = _room_label(value)
    return "студия" if label == "студии" else label


def _positive_float(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value) if float(value).is_integer() else float(value)


def _positive_intish(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value > 0 else None
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _area(raw: Mapping[str, Any]) -> str | None:
    if raw.get("area"):
        return _canonical_area(raw.get("area"))
    if raw.get("total_area"):
        return _canonical_area(raw.get("total_area"))
    nested = _first_from_nested(raw, "area")
    if nested:
        return _canonical_area(nested)
    mn, mx = raw.get("square_min"), raw.get("square_max")
    if mn and mx:
        return f"{_decimal_text(mn)}–{_decimal_text(mx)} м²"
    if mn:
        return f"от {_decimal_text(mn)} м²"
    return None


def _canonical_area(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < 5:
            return None
        rendered = f"{value:g}".replace(".", ",")
        return f"{rendered} м²"
    text = _text(value)
    if not text:
        return None
    if re.fullmatch(r"[1-4](?:[.,]0+)?", text):
        return None
    if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        return text + " м²"
    return text


def _decimal_text(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:g}".replace(".", ",")
    return str(value).replace(".", ",")


def _metro(value: Any) -> str | None:
    values = value if isinstance(value, list) else [value]
    rendered: list[tuple[float, str]] = []
    for item in values:
        if isinstance(item, Mapping):
            name = _text(item.get("metro_name") or item.get("name") or item.get("title"))
            if not name:
                continue
            foot = item.get("on_foot")
            transport = item.get("on_transport")
            if isinstance(foot, (int, float)) and not isinstance(foot, bool) and foot > 0:
                text = f"{name} — {_decimal_text(foot)} минут пешком"
                rank = float(foot)
            elif isinstance(transport, (int, float)) and not isinstance(transport, bool) and transport > 0:
                text = f"{name} — {_decimal_text(transport)} минут транспортом"
                rank = 1000 + float(transport)
            else:
                text = name
                rank = 2000
            rendered.append((rank, text))
        else:
            text = _text(item)
            if text:
                rendered.append((2000, text))
    if not rendered:
        return None
    unique: list[str] = []
    for _, text in sorted(rendered, key=lambda pair: pair[0]):
        if text not in unique:
            unique.append(text)
    return "; ".join(unique[:2])


def _developer(value: Any) -> str | None:
    values = value if isinstance(value, list) else [value]
    names: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            text = _text(item.get("name") or item.get("title") or item.get("developer_name"))
        else:
            text = _text(item)
        if text and text not in names:
            names.append(text)
    return ", ".join(names[:2]) or None


def _first_from_nested(raw: Mapping[str, Any], key: str) -> Any:
    for container in ("ads", "apartment_types", "house", "ads_add"):
        values = raw.get(container)
        if isinstance(values, Mapping) and values.get(key) is not None:
            return values.get(key)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, Mapping) and value.get(key) is not None:
                    return value.get(key)
    return None


def _infrastructure(raw: Mapping[str, Any]) -> tuple[str, ...]:
    names = {
        "school": "школа", "kindergarten": "детский сад", "park_near": "парк", "water_near": "вода рядом",
        "yard_without_cars": "двор без машин", "children_ground": "детская площадка", "sports_ground": "спортивная площадка",
        "security": "охрана", "territory": "благоустроенная территория", "parking": "паркинг", "elevator": "лифт",
        "schools": "школа", "kindergartens": "детский сад", "shops": "магазины",
    }
    family = raw.get("family_infrastructure")
    family_source = family if isinstance(family, Mapping) else {}
    infrastructure = raw.get("infrastructure")
    infrastructure_source = infrastructure if isinstance(infrastructure, Mapping) else {}
    sources = (raw, family_source, infrastructure_source)
    out = [
        label
        for key, label in names.items()
        if any(_binary_flag_enabled(source.get(key)) for source in sources)
    ]
    values = infrastructure
    if isinstance(values, list):
        for item in values:
            if isinstance(item, Mapping):
                text = _text(item.get("name") or item.get("title") or item.get("label"))
            else:
                text = _text(item)
            if text:
                out.append(text)
    elif isinstance(values, Mapping):
        for key in ("parks", "park", "green_zones"):
            out.extend(_flatten_text_values(values.get(key)))
    return tuple(dict.fromkeys(out[:5]))


def _flatten_text_values(value: Any) -> list[str]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, Mapping):
        preferred = value.get("name") or value.get("title") or value.get("label") or value.get("type")
        values = [preferred] if preferred is not None else list(value.values())
        return sum((_flatten_text_values(item) for item in values), [])
    if isinstance(value, (list, tuple, set)):
        return sum((_flatten_text_values(item) for item in value), [])
    text = _safe_dynamic_text(value)
    return [text] if text else []


def _service_source_values(raw: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("infrastructure", "shops", "services", "retail", "pharmacy", "pharmacies", "commerce"):
        values.extend(_flatten_text_values(raw.get(key)))
    infrastructure = raw.get("infrastructure")
    if isinstance(infrastructure, Mapping):
        for key, label in (("shops", "магазины"), ("shop", "магазины"), ("pharmacy", "аптека"), ("pharmacies", "аптеки")):
            if _binary_flag_enabled(infrastructure.get(key)):
                values.append(label)
    return values


def _daily_services(raw: Mapping[str, Any]) -> tuple[str, ...]:
    tokens = ("магазин", "магазины", "сервис", "сервисы", "ритейл", "аптек", "commerce", "retail", "shop", "service")
    out = [text for text in _service_source_values(raw) if _has_text_token(text, tokens)]
    return tuple(dict.fromkeys(out[:3]))


def _healthcare(raw: Mapping[str, Any]) -> tuple[str, ...]:
    tokens = ("клиник", "поликлиник", "аптек", "медицин", "clinic", "clinics", "polyclinic", "pharmacy", "pharmacies", "medical")
    values = _service_source_values(raw)
    values.extend(_flatten_text_values(raw.get("clinic")))
    values.extend(_flatten_text_values(raw.get("clinics")))
    infrastructure = raw.get("infrastructure")
    if isinstance(infrastructure, Mapping):
        for key, label in (("clinic", "клиника"), ("clinics", "клиники"), ("pharmacy", "аптека"), ("pharmacies", "аптеки")):
            if _binary_flag_enabled(infrastructure.get(key)):
                values.append(label)
    out = [text for text in values if _has_text_token(text, tokens)]
    return tuple(dict.fromkeys(out[:3]))


def _has_text_token(value: Any, tokens: tuple[str, ...]) -> bool:
    low = str(value or "").casefold().replace("ё", "е")
    return any(token in low for token in tokens)


def _binary_flag_enabled(value: Any) -> bool:
    """Accept MCP boolean flags without treating arbitrary truthy values as facts."""

    if value is True:
        return True
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 1


def _ads_count(raw: Mapping[str, Any]) -> int | None:
    for value in (raw.get("ads_count"), raw.get("count_ads"), raw.get("counter_novos")):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, Mapping) and isinstance(value.get("count_ads"), int):
            return int(value["count_ads"])
    return None


def _sales_count(raw: Mapping[str, Any]) -> int | None:
    for value in (raw.get("sales_count"), raw.get("sales"), raw.get("egrn_top_novos")):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, Mapping) and isinstance(value.get("sales"), int):
            return int(value["sales"])
    return None


def _finance_text(raw: Mapping[str, Any]) -> str | None:
    bits: list[str] = []
    mortgages: list[Mapping[str, Any]] = []
    for key in ("mortgage_calc", "mortgage"):
        value = raw.get(key)
        if isinstance(value, Mapping):
            mortgages.append(value)
        elif isinstance(value, list):
            mortgages.extend(item for item in value if isinstance(item, Mapping))
    for mortgage in mortgages[:5]:
        if mortgage.get("min_percent") is not None:
            bits.append(f"ипотека от {mortgage['min_percent']}%")
        if mortgage.get("min_fee") is not None:
            bits.append(f"первоначальный взнос от {mortgage['min_fee']}%")
    installment = raw.get("payment_by_installments") if isinstance(raw.get("payment_by_installments"), Mapping) else {}
    if installment.get("month") is not None:
        bits.append(f"рассрочка до {installment['month']} мес.")
    return "; ".join(dict.fromkeys(bits)) or None


def _mortgage_items(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for key in ("mortgage_calc", "mortgage"):
        value = raw.get(key)
        if isinstance(value, Mapping):
            out.append(value)
        elif isinstance(value, list):
            out.extend(item for item in value if isinstance(item, Mapping))
    return out[:5]


def _first_safe_price_from_mortgage(raw: Mapping[str, Any], keys: tuple[str, ...]) -> str | int | float | None:
    for item in _mortgage_items(raw):
        for key in keys:
            value = item.get(key)
            scalar = _safe_price_scalar(value)
            if scalar is not None:
                return scalar
    return None


def _mortgage_rate(raw: Mapping[str, Any]) -> str | int | float | None:
    return _first_safe_price_from_mortgage(raw, ("min_percent", "rate", "percent"))


def _mortgage_down_payment(raw: Mapping[str, Any]) -> str | int | float | None:
    return _first_safe_price_from_mortgage(raw, ("min_fee", "down_payment", "first_payment"))


def _mortgage_term(raw: Mapping[str, Any]) -> str | int | None:
    for item in _mortgage_items(raw):
        for key in ("max_year", "years", "term", "term_years"):
            scalar = _safe_inventory_scalar(item.get(key))
            if scalar is not None:
                return scalar
    return None


def _installment_months(raw: Mapping[str, Any]) -> str | int | None:
    installment = raw.get("payment_by_installments") if isinstance(raw.get("payment_by_installments"), Mapping) else {}
    for key in ("month", "months", "term_months"):
        scalar = _safe_inventory_scalar(installment.get(key))
        if scalar is not None:
            return scalar
    return None


def _transport_access(raw: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    railway = _safe_dynamic_text(raw.get("property_railway"))
    highway = _safe_dynamic_text(raw.get("highway_name"))
    mkad = raw.get("distance_from_mkad")
    if railway:
        out.append(f"ж/д ориентир: {railway}")
    if highway:
        out.append(f"шоссе: {highway}")
    if isinstance(mkad, (int, float)) and not isinstance(mkad, bool) and mkad >= 0:
        out.append(f"{_decimal_text(mkad)} км от МКАД")
    elif isinstance(mkad, str):
        text = _safe_dynamic_text(mkad)
        if text and _text_has_positive_number(text):
            out.append(f"от МКАД: {text}")
    return tuple(dict.fromkeys(out[:3]))


def _room_prices(raw: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    labels = (("price_s", "студии"), ("price1", "1"), ("price2", "2"), ("price3", "3"), ("price4", "4"), ("price_n", "нежилые"))
    out: list[dict[str, Any]] = []
    for key, label in labels:
        scalar = _safe_price_scalar(raw.get(key))
        if scalar is not None:
            out.append({"rooms": label, "value": scalar})
    return tuple(out[:6])


def _price_square(raw: Mapping[str, Any]) -> str | int | float | None:
    return _safe_price_scalar(raw.get("price_square") or _first_from_nested(raw, "stat_price"))


def _recurring_costs(raw: Mapping[str, Any]) -> str | int | float | None:
    return _safe_price_scalar(raw.get("utility_fee"))


def _purchase_terms(raw: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    if _binary_flag_enabled(raw.get("trade_in")):
        out.append("trade-in")
    if _binary_flag_enabled(raw.get("ddu_escrow")):
        out.append("ДДУ/эскроу")
    if _binary_flag_enabled(raw.get("fz214")):
        out.append("214-ФЗ")
    return tuple(out)


def _building_profile(raw: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    floors = _positive_intish(raw.get("floors_total") or _first_from_nested(raw, "floors_total"))
    building_type = _safe_dynamic_text(raw.get("building_type"))
    ceiling = _safe_dynamic_text(raw.get("ceiling_height") or _first_from_nested(raw, "ceiling_height"))
    if floors:
        out.append(f"{floors} этажей")
    if raw.get("elevator") is True or _binary_flag_enabled(raw.get("elevator")):
        out.append("лифт")
    if building_type:
        out.append(building_type)
    if ceiling and _text_has_positive_number(ceiling):
        out.append(f"потолки {ceiling}")
    return tuple(dict.fromkeys(out[:4]))


def _property_formats(raw: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    if _binary_flag_enabled(raw.get("apartments")) or raw.get("apartments") is True:
        out.append("апартаменты")
    if _binary_flag_enabled(raw.get("taunhouse")) or raw.get("taunhouse") is True:
        out.append("таунхаусы")
    for text in _flatten_text_values(raw.get("ads_type_list")):
        if text:
            out.append(text)
    return tuple(dict.fromkeys(out[:5]))


def _discount_text(raw: Mapping[str, Any]) -> str | None:
    value = raw.get("discount")
    return _safe_dynamic_text(value) if isinstance(value, str) else None


def _parking(raw: Mapping[str, Any]) -> bool | str | None:
    value = raw.get("parking") if raw.get("parking") is not None else raw.get("garage")
    if isinstance(value, bool):
        return value
    text = _text(value)
    if text and (MISSING_DYNAMIC_RE.search(text) or PARKING_MISSING_RE.search(text)):
        return None
    return text if text else None


def _parking_price(raw: Mapping[str, Any]) -> str | int | float | None:
    for key in ("parking_price", "parking.min_price", "garage_price"):
        value = raw.get(key)
        scalar = _safe_price_scalar(value)
        if scalar is not None:
            return scalar
    parking = raw.get("parking")
    if isinstance(parking, Mapping):
        for key in ("price", "min_price", "cost"):
            value = parking.get(key)
            scalar = _safe_price_scalar(value)
            if scalar is not None:
                return scalar
    return None


def _parking_inventory(raw: Mapping[str, Any]) -> str | int | None:
    for key in ("parking_inventory", "parking_count", "garage_count"):
        value = raw.get(key)
        scalar = _safe_inventory_scalar(value)
        if scalar is not None:
            return scalar
    parking = raw.get("parking")
    if isinstance(parking, Mapping):
        for key in ("count", "available", "inventory"):
            value = parking.get(key)
            scalar = _safe_inventory_scalar(value)
            if scalar is not None:
                return scalar
    return None


def _apartment_inventory(raw: Mapping[str, Any]) -> str | int | bool | None:
    for key in ("apartment_inventory", "available_apartments", "flats_available", "inventory"):
        value = raw.get(key)
        if isinstance(value, Mapping):
            for nested_key in ("total_available", "available", "count", "inventory"):
                scalar = _safe_inventory_scalar(value.get(nested_key))
                if scalar is not None:
                    return scalar
        if value not in (None, "", 0, "0"):
            scalar = _safe_inventory_scalar(value)
            if scalar is not None:
                return scalar
    inventory = raw.get("apartments")
    if isinstance(inventory, Mapping):
        for key in ("available", "count", "inventory"):
            value = inventory.get(key)
            if value not in (None, "", 0, "0"):
                scalar = _safe_inventory_scalar(value)
                if scalar is not None:
                    return scalar
    return None


def _missing_categories(values: Any) -> tuple[str, ...]:
    source = values if isinstance(values, list) else [values] if values else []
    categories: list[str] = []
    for item in source:
        text = _missing_source_text(item)
        low = text.casefold().replace(".", "_")
        if any(token in low for token in ("mortgage", "discount", "installment", "payment", "ипот", "скид", "рассроч")):
            categories.append("finance")
        elif any(token in low for token in ("school", "kindergarten", "children", "дет", "школ", "сад")):
            categories.append("family_infrastructure")
        elif any(token in low for token in ("park", "water", "sports", "ecology", "парк", "вод", "спорт", "эколог")):
            categories.append("walk_infrastructure")
        elif any(token in low for token in ("security", "yard_without_cars", "охран", "безопас", "двор")):
            categories.append("safety_infrastructure")
        elif any(token in low for token in ("sales", "egrn", "егрн", "продаж")):
            categories.append("sales")
        elif any(token in low for token in ("ads", "count_ads", "объяв")):
            categories.append("ads")
        elif any(token in low for token in ("location", "district", "metro", "локац", "район", "метро")):
            categories.append("location")
        elif any(token in low for token in ("budget", "price", "max_price", "min_price", "бюдж", "цен")):
            categories.append("budget")
        elif "room" in low or "комнат" in low:
            categories.append("rooms")
        elif any(token in low for token in ("ready", "delivered", "срок", "готов", "сдач")):
            categories.append("readiness")
        elif "finishing" in low or "отдел" in low:
            categories.append("finishing")
        elif text:
            categories.append("details")
    return tuple(dict.fromkeys(categories))


def _missing_source_text(item: Any) -> str:
    if isinstance(item, Mapping):
        parts = [str(item.get(key) or "") for key in ("category", "field", "code", "reason_code")]
        return " ".join(parts)
    return str(item or "")
