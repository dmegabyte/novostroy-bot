#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


MAX_STRING_LENGTH = 240
MAX_LIST_ITEMS = 8
MAX_DIAGNOSTIC_ITEMS = 100

CARD_FIELDS = {
    "name",
    "developer",
    "property_class",
    "location",
    "price_min",
    "price",
    "room_formats",
    "rooms",
    "area",
    "apartment_inventory",
    "ready",
    "finishing",
    "metro",
    "ecology_rating",
    "parking",
    "parking_price",
    "parking_inventory",
    "discount",
    "sales_count",
    "ads_count",
    "infrastructure",
    "mortgage_terms",
    "lot_examples",
}
LOT_FIELDS = {
    "full_price",
    "area_m2",
    "floor",
    "floors_total",
    "rooms",
    "renovation",
    "status",
    "house_id",
    "house_name",
}

DIRECT_MAPPINGS = (
    ("developer", "developer", "text"),
    ("property_class", "property_class", "text"),
    ("location", "location", "text"),
    ("area", "area", "positive"),
    ("apartment_inventory", "apartment_inventory", "inventory"),
    ("readiness", "ready", "text"),
    ("finishing", "finishing", "text"),
    ("metro", "metro", "text"),
    ("ecology_rating", "ecology_rating", "scalar"),
    ("parking_price", "parking_price", "positive"),
    ("parking_inventory", "parking_inventory", "inventory"),
    ("discount", "discount", "text"),
    ("sales_count", "sales_count", "number"),
    ("ads_count", "ads_count", "number"),
)
FINANCE_UNMAPPED = ("mortgage_rate", "down_payment", "installment_months")
ADAPTER_REACHABLE_FIELD_IDS = tuple(
    sorted(
        {
            "apartment_price",
            "room_formats",
            "school",
            "kindergarten",
            "park_near",
            "water_near",
            "yard_without_cars",
            "children_ground",
            "sports_ground",
            "security",
            "parking",
            "lot_full_price",
            "lot_area",
            "lot_floor",
            "lot_rooms",
            "lot_renovation",
            "lot_status",
            *(field_id for field_id, _source, _kind in DIRECT_MAPPINGS),
        }
    )
)

OMIT_REASONS = {"missing", "unsafe_value", "unmapped", "not_requested", "out_of_range"}
LOT_SELECTIONS = {"not_requested", "selected", "out_of_range"}


def _load_brief_builder():
    try:
        from .brief_builder import build_compact_brief  # type: ignore

        return build_compact_brief
    except Exception:
        path = Path(__file__).resolve().with_name("brief_builder.py")
        spec = importlib.util.spec_from_file_location("field_sales_registry_v1_brief_builder", path)
        if spec is None or spec.loader is None:
            raise ImportError("cannot load sibling brief_builder")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.build_compact_brief


def _read_allowed(source: Mapping[str, Any] | object, key: str, allowed: set[str]) -> Any:
    if key not in allowed:
        return None
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _bounded_string(value: Any) -> str | None:
    if value is None or isinstance(value, bool) or isinstance(value, (Mapping, list, tuple, set)):
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    return text[:MAX_STRING_LENGTH]


def _safe_scalar(value: Any) -> Any | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return _bounded_string(value)


def _safe_text(value: Any) -> str | None:
    return _bounded_string(value)


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def _safe_inventory(value: Any) -> int | float | str | None:
    number = _safe_number(value)
    if number is not None:
        return number
    return _bounded_string(value)


def _safe_positive_scalar(value: Any) -> int | float | str | None:
    number = _safe_number(value)
    if number is not None:
        return number if number > 0 else None
    text = _bounded_string(value)
    if not text:
        return None
    for token in re.findall(r"\d+(?:[\s\u00a0]\d{3})*(?:[,.]\d+)?|\d+", text):
        try:
            if float(token.replace(" ", "").replace("\u00a0", "").replace(",", ".")) > 0:
                return text
        except ValueError:
            continue
    return None


def _safe_list(value: Any) -> list[Any] | None:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    out: list[Any] = []
    for item in list(value)[:MAX_LIST_ITEMS]:
        safe = _safe_scalar(item)
        if safe is not None:
            out.append(safe)
    return out or None


def _add_fact(facts: dict[str, Any], omitted: list[dict[str, str]], field_id: str, value: Any, kind: str = "scalar") -> None:
    if kind == "text":
        safe = _safe_text(value)
    elif kind == "number":
        safe = _safe_number(value)
    elif kind == "inventory":
        safe = _safe_inventory(value)
    elif kind == "positive":
        safe = _safe_positive_scalar(value)
    elif kind == "list":
        safe = _safe_list(value)
    else:
        safe = _safe_scalar(value)
    if safe is None:
        if value is not None:
            omitted.append({"field_id": field_id, "reason": "unsafe_value"})
        return
    facts[field_id] = safe


def _infra_texts(value: Any) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return []
    out: list[str] = []
    for item in list(value)[:MAX_LIST_ITEMS]:
        text = _bounded_string(item)
        if text:
            out.append(text)
    return out


def _contains_any(text: str, *tokens: str) -> bool:
    low = text.casefold()
    return any(token.casefold() in low for token in tokens)


def _apply_infrastructure(facts: dict[str, Any], infra: Any) -> None:
    for text in _infra_texts(infra):
        low = text.casefold()
        if re.search(r"(?:^|\W)(?:школ\w*|school)(?:$|\W)", low):
            facts.setdefault("school", True)
        if re.search(r"(?:детский\s+сад|детсад|kindergarten)", low):
            facts.setdefault("kindergarten", True)
        if re.search(r"(?:^|\W)(?:лесопарк|парк|лес|сквер|park|forest)(?:$|\W)|green[\s-]+zone", low):
            facts.setdefault("park_near", True)
        if re.search(r"(?:водо[её]м|набереж\w*|вода|река|озеро|пруд|water|river|lake)", low):
            facts.setdefault("water_near", True)
        if low in {"двор без машин", "yard without cars"}:
            facts.setdefault("yard_without_cars", True)
        if re.search(r"(?:детская\s+площадка|children[\s-]+playground)", low):
            facts.setdefault("children_ground", True)
        if re.search(r"(?:спортивная\s+площадка|sports[\s-]+playground)", low):
            facts.setdefault("sports_ground", True)
        if re.search(r"(?:^|\W)(?:охрана|security)(?:$|\W)", low):
            facts.setdefault("security", True)
        if re.search(r"(?:паркинг|парковк\w*|parking)", low):
            facts.setdefault("parking", True)


def _safe_parking_presence(value: Any) -> bool:
    if value is True:
        return True
    if value in (None, False, "") or isinstance(value, (Mapping, list, tuple, set)):
        return False
    text = _bounded_string(value)
    if not text:
        return False
    low = text.casefold()
    if any(token in low for token in ("нет", "не предусмотр", "отсутств", "no parking")):
        return False
    return True


def _lot_list(value: Any) -> list[Any]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        return []
    return list(value)[:100]


def _selected_lot(lots: list[Any], lot_index: int | None) -> tuple[Any | None, str]:
    if lot_index is None:
        return None, "not_requested"
    if isinstance(lot_index, bool) or not isinstance(lot_index, int) or lot_index < 0 or lot_index >= len(lots):
        return None, "out_of_range"
    return lots[lot_index], "selected"


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _lot_status(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None
    text = _bounded_string(value)
    if not text or text.strip().isdigit():
        return None
    return text


def _apply_lot(facts: dict[str, Any], omitted: list[dict[str, str]], lot: Any | None) -> bool:
    if lot is None:
        return False
    full_price = _read_allowed(lot, "full_price", LOT_FIELDS)
    area = _read_allowed(lot, "area_m2", LOT_FIELDS)
    floor = _read_allowed(lot, "floor", LOT_FIELDS)
    floors_total = _read_allowed(lot, "floors_total", LOT_FIELDS)
    rooms = _read_allowed(lot, "rooms", LOT_FIELDS)
    renovation = _read_allowed(lot, "renovation", LOT_FIELDS)
    status = _read_allowed(lot, "status", LOT_FIELDS)

    _add_fact(facts, omitted, "lot_full_price", full_price, "positive")
    _add_fact(facts, omitted, "lot_area", area, "positive")
    floor_value: int | str | None = _positive_int(floor)
    total = _positive_int(floors_total)
    if floor_value is not None and total is not None:
        floor_value = f"{floor_value} из {total}"
    _add_fact(facts, omitted, "lot_floor", floor_value, "scalar")
    _add_fact(facts, omitted, "lot_rooms", rooms, "text")
    _add_fact(facts, omitted, "lot_renovation", renovation, "text")
    safe_status = _lot_status(status)
    if safe_status is not None:
        facts["lot_status"] = safe_status
    elif status is not None:
        omitted.append({"field_id": "lot_status", "reason": "unsafe_value"})
    return bool(_read_allowed(lot, "house_id", LOT_FIELDS) not in (None, "") and _bounded_string(_read_allowed(lot, "house_name", LOT_FIELDS)))


def _clean_omitted(items: list[dict[str, str]]) -> list[dict[str, str]]:
    out = []
    for item in items:
        field_id = item.get("field_id")
        reason = item.get("reason")
        if isinstance(field_id, str) and isinstance(reason, str) and reason in OMIT_REASONS:
            out.append({"field_id": field_id, "reason": reason})
    return sorted(out, key=lambda item: (item["reason"], item["field_id"]))[:MAX_DIAGNOSTIC_ITEMS]


def validate_adaptation_shape(envelope: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {"schema_version", "object_name", "facts", "lot_index", "diagnostics"}
    if set(envelope) != required:
        errors.append("root_keys")
    if envelope.get("schema_version") != 1:
        errors.append("schema_version")
    if envelope.get("object_name") is not None and not isinstance(envelope.get("object_name"), str):
        errors.append("object_name")
    facts = envelope.get("facts")
    if not isinstance(facts, Mapping):
        errors.append("facts")
    else:
        for key, value in facts.items():
            if not isinstance(key, str) or not key:
                errors.append("fact_key")
            if value is not True and _safe_scalar(value) is None and _safe_list(value) is None:
                errors.append(f"fact_value:{key}")
    lot_index = envelope.get("lot_index")
    if lot_index is not None and (isinstance(lot_index, bool) or not isinstance(lot_index, int) or lot_index < 0):
        errors.append("lot_index")
    diagnostics = envelope.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        errors.append("diagnostics")
    else:
        if diagnostics.get("lot_selection") not in LOT_SELECTIONS:
            errors.append("lot_selection")
        if not isinstance(diagnostics.get("lot_examples_available"), int):
            errors.append("lot_examples_available")
        if not isinstance(diagnostics.get("house_link_available"), bool):
            errors.append("house_link_available")
        for key in ("unmapped_field_ids", "omitted_field_ids"):
            if not isinstance(diagnostics.get(key), list):
                errors.append(key)
    return errors


def reachable_field_ids() -> tuple[str, ...]:
    """Return canonical registry field IDs this adapter can emit as facts."""

    return ADAPTER_REACHABLE_FIELD_IDS


def adapt_option_card(card: Mapping[str, Any] | object, *, lot_index: int | None = None) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    omitted: list[dict[str, str]] = []

    object_name = _safe_text(_read_allowed(card, "name", CARD_FIELDS))
    price_min = _read_allowed(card, "price_min", CARD_FIELDS)
    price = _read_allowed(card, "price", CARD_FIELDS)
    _add_fact(facts, omitted, "apartment_price", price_min if price_min is not None else price, "positive")

    for field_id, source, kind in DIRECT_MAPPINGS:
        _add_fact(facts, omitted, field_id, _read_allowed(card, source, CARD_FIELDS), kind)

    room_formats = _safe_list(_read_allowed(card, "room_formats", CARD_FIELDS))
    if room_formats is None:
        room = _safe_scalar(_read_allowed(card, "rooms", CARD_FIELDS))
        room_formats = [room] if room is not None else None
    if room_formats:
        facts["room_formats"] = room_formats

    if _safe_parking_presence(_read_allowed(card, "parking", CARD_FIELDS)):
        facts["parking"] = True
    _apply_infrastructure(facts, _read_allowed(card, "infrastructure", CARD_FIELDS))

    unmapped: list[str] = []
    if _safe_text(_read_allowed(card, "mortgage_terms", CARD_FIELDS)):
        unmapped.extend(FINANCE_UNMAPPED)

    lots = _lot_list(_read_allowed(card, "lot_examples", CARD_FIELDS))
    lot, selection = _selected_lot(lots, lot_index)
    house_link_available = _apply_lot(facts, omitted, lot)
    if selection == "out_of_range":
        omitted.append({"field_id": "lot_full_price", "reason": "out_of_range"})

    envelope = {
        "schema_version": 1,
        "object_name": object_name,
        "facts": facts,
        "lot_index": lot_index if selection == "selected" else None,
        "diagnostics": {
            "unmapped_field_ids": list(dict.fromkeys(unmapped))[:MAX_DIAGNOSTIC_ITEMS],
            "omitted_field_ids": _clean_omitted(omitted),
            "lot_examples_available": len(lots),
            "lot_selection": selection,
            "house_link_available": bool(house_link_available),
        },
    }
    errors = validate_adaptation_shape(envelope)
    if errors:
        raise ValueError(f"invalid adaptation envelope: {', '.join(errors[:5])}")
    return envelope


def build_brief_from_option_card(
    card: Mapping[str, Any] | object,
    scenario: str,
    *,
    fresh_mcp: bool = False,
    requested_fields: Sequence[str] = (),
    max_fields: int = 5,
    lot_index: int | None = None,
) -> dict[str, Any]:
    adaptation = adapt_option_card(card, lot_index=lot_index)
    build_compact_brief = _load_brief_builder()
    brief = build_compact_brief(
        adaptation["facts"],
        scenario,
        fresh_mcp=fresh_mcp,
        requested_fields=requested_fields,
        max_fields=max_fields,
        object_name=adaptation["object_name"],
    )
    return {"adaptation": adaptation, "brief": brief}


__all__ = ["adapt_option_card", "build_brief_from_option_card", "reachable_field_ids", "validate_adaptation_shape"]
