from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JsonDict = dict[str, Any]


def _bounded_text(value: Any, *, field_name: str, maximum: int = 200) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name}_must_be_string")
    value = " ".join(value.split())
    if not value or len(value) > maximum:
        raise ValueError(f"invalid_{field_name}")
    return value


def _bounded_context_text(value: Any) -> str | None:
    """Return the client-safe persisted assistant context, capped at 2000 chars."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("previous_assistant_message_must_be_string")
    return value[:2000] or None


def _bounded_tuple(value: Any, *, field_name: str, maximum: int = 10) -> tuple[str, ...]:
    if value in (None, ()):
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name}_must_be_array")
    if len(value) > maximum:
        raise ValueError(f"too_many_{field_name}")
    result: list[str] = []
    for item in value:
        text = _bounded_text(item, field_name=field_name)
        if text and text not in result:
            result.append(text)
    return tuple(result)


@dataclass(frozen=True)
class LotExample:
    id: str | int | None = None
    rooms: str | int | None = None
    area_m2: int | float | None = None
    floor: int | None = None
    floors_total: int | None = None
    full_price: int | float | None = None
    renovation: str | None = None
    status: str | int | None = None
    house_id: str | int | None = None
    house_name: str | None = None
    source: str | None = None
    living_space: int | float | None = None
    kitchen_area: int | float | None = None
    balcony: str | None = None
    bathroom: str | None = None
    ceiling_height: int | float | str | None = None
    window_view: str | None = None
    layout_features: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Any) -> "LotExample":
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            raise ValueError("invalid_lot_example")
        values = dict(data)
        values["layout_features"] = _bounded_tuple(values.get("layout_features", ()), field_name="layout_features")
        return cls(**values)

    def to_dict(self) -> JsonDict:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        result["layout_features"] = list(self.layout_features)
        return result


@dataclass(frozen=True)
class OptionCard:
    name: str
    entity_id: str | int | None = None
    entity_type: str | None = None
    district: str | None = None
    location: str | None = None
    price: str | None = None
    price_min: int | None = None
    rooms: int | str | None = None
    finishing: str | None = None
    area: str | None = None
    ready: str | None = None
    metro: str | None = None
    developer: str | None = None
    property_class: str | None = None
    ecology_rating: str | int | float | None = None
    infrastructure: tuple[str, ...] = ()
    daily_services: tuple[str, ...] = ()
    healthcare: tuple[str, ...] = ()
    ads_count: int | None = None
    sales_count: int | None = None
    sales_date: str | None = None
    discount: str | None = None
    parking: bool | str | None = None
    parking_price: str | int | float | None = None
    parking_inventory: str | int | None = None
    apartment_inventory: str | int | bool | None = None
    mortgage_terms: str | None = None
    mortgage_rate: str | int | float | None = None
    mortgage_down_payment: str | int | float | None = None
    mortgage_term: str | int | None = None
    installment_months: str | int | None = None
    transport_access: tuple[str, ...] = ()
    room_prices: tuple[JsonDict, ...] = ()
    price_square: str | int | float | None = None
    recurring_costs: str | int | float | None = None
    purchase_terms: tuple[str, ...] = ()
    building_profile: tuple[str, ...] = ()
    property_formats: tuple[str, ...] = ()
    room_formats: tuple[str, ...] = ()
    lot_examples: tuple[LotExample, ...] = ()
    why_close: str | None = None
    is_near: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> "OptionCard":
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            raise ValueError("invalid_option_card")
        values = dict(data)
        values["name"] = _bounded_text(values.get("name"), field_name="option_name")
        if values["name"] is None:
            raise ValueError("option_name_required")
        for key in ("infrastructure", "daily_services", "healthcare", "transport_access", "purchase_terms", "building_profile", "property_formats", "room_formats"):
            values[key] = _bounded_tuple(values.get(key, ()), field_name=key)
        raw_lots = values.get("lot_examples", ())
        if isinstance(raw_lots, dict):
            raw_lots = [raw_lots]
        if not isinstance(raw_lots, (list, tuple)) or len(raw_lots) > 2:
            raise ValueError("invalid_lot_examples")
        values["lot_examples"] = tuple(item if isinstance(item, LotExample) else LotExample.from_dict(item) for item in raw_lots)
        room_prices = values.get("room_prices", ())
        if not isinstance(room_prices, (list, tuple)) or len(room_prices) > 10 or not all(isinstance(item, dict) for item in room_prices):
            raise ValueError("invalid_room_prices")
        values["room_prices"] = tuple(dict(item) for item in room_prices)
        if not isinstance(values.get("is_near", False), bool):
            raise ValueError("invalid_is_near")
        return cls(**values)

    def to_dict(self) -> JsonDict:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        for key in ("infrastructure", "daily_services", "healthcare", "transport_access", "purchase_terms", "building_profile", "property_formats", "room_formats"):
            result[key] = list(result[key])
        result["room_prices"] = [dict(item) for item in self.room_prices]
        result["lot_examples"] = [item.to_dict() for item in self.lot_examples]
        return result


@dataclass(frozen=True)
class SearchResult:
    facts: tuple[OptionCard, ...] = ()
    near: tuple[OptionCard, ...] = ()
    missing: tuple[str, ...] = ()
    params: JsonDict = field(default_factory=dict)
    summary: str | None = None

    def shortlist(self, limit: int = 3) -> tuple[OptionCard, ...]:
        source = self.facts if self.facts else self.near
        seen: set[str] = set()
        result: list[OptionCard] = []
        for card in source:
            key = " ".join(card.name.casefold().replace("ё", "е").split())
            if key and key not in seen:
                seen.add(key)
                result.append(card)
            if len(result) >= max(0, int(limit)):
                break
        return tuple(result)

    @classmethod
    def from_dict(cls, data: Any) -> "SearchResult":
        if not isinstance(data, dict) or set(data) - {"facts", "near", "missing", "params", "summary"}:
            raise ValueError("invalid_search_result")
        facts = data.get("facts", [])
        near = data.get("near", [])
        if not isinstance(facts, list) or not isinstance(near, list) or len(facts) > 3 or len(near) > 3:
            raise ValueError("invalid_search_cards")
        return cls(
            facts=tuple(OptionCard.from_dict(item) for item in facts),
            near=tuple(OptionCard.from_dict({**item, "is_near": True}) for item in near),
            missing=_bounded_tuple(data.get("missing", ()), field_name="missing", maximum=20),
            params=dict(data.get("params", {})) if isinstance(data.get("params", {}), dict) else (_ for _ in ()).throw(ValueError("invalid_search_params")),
            summary=_bounded_text(data.get("summary"), field_name="summary", maximum=500),
        )

    def to_dict(self) -> JsonDict:
        return {"facts": [item.to_dict() for item in self.facts], "near": [item.to_dict() for item in self.near], "missing": list(self.missing), "params": dict(self.params), "summary": self.summary}


@dataclass(frozen=True)
class V0State:
    """Minimal local V0 dialogue state.

    Only client-safe normalized cards are stored in ``visible_options``.
    """

    params: JsonDict = field(default_factory=dict)
    visible_options: tuple[OptionCard, ...] = ()
    selected_option_name: str | None = None
    active_topic: str | None = None
    has_greeted: bool = False
    last_answer_kind: str | None = None
    last_assistant_question: str | None = None
    previous_assistant_message: str | None = None
    answered_facts: tuple[str, ...] = ()
    pending_action: str | None = None
    pending_subject: str | None = None
    pending_topic: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "V0State":
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            raise ValueError("invalid_v0_state")
        values = dict(data)
        if not isinstance(values.get("params", {}), dict):
            raise ValueError("invalid_v0_state_params")
        cards = values.get("visible_options", ())
        if not isinstance(cards, (list, tuple)) or len(cards) > 3:
            raise ValueError("invalid_v0_visible_options")
        values["visible_options"] = tuple(item if isinstance(item, OptionCard) else OptionCard.from_dict(item) for item in cards)
        values["answered_facts"] = _bounded_tuple(values.get("answered_facts", ()), field_name="answered_facts", maximum=30)
        for key in ("selected_option_name", "active_topic", "last_answer_kind", "last_assistant_question", "pending_action", "pending_subject", "pending_topic"):
            values[key] = _bounded_text(values.get(key), field_name=key)
        values["previous_assistant_message"] = _bounded_context_text(values.get("previous_assistant_message"))
        if not isinstance(values.get("has_greeted", False), bool):
            raise ValueError("invalid_v0_has_greeted")
        return cls(**values)

    def to_dict(self) -> JsonDict:
        return {"params": dict(self.params), "visible_options": [card.to_dict() for card in self.visible_options], "selected_option_name": self.selected_option_name, "active_topic": self.active_topic, "has_greeted": self.has_greeted, "last_answer_kind": self.last_answer_kind, "last_assistant_question": self.last_assistant_question, "previous_assistant_message": _bounded_context_text(self.previous_assistant_message), "answered_facts": list(self.answered_facts), "pending_action": self.pending_action, "pending_subject": self.pending_subject, "pending_topic": self.pending_topic}


@dataclass(frozen=True)
class V0Answer:
    answer_kind: str
    scope: str
    intro: str
    options: tuple[JsonDict, ...] = ()
    recommendation: str = ""
    missing_note: str = ""
    final_question: str = ""

    def text(self) -> str:
        chunks = [self.intro]
        for option in self.options:
            lines = [str(line).rstrip() for line in option.get("lines", ()) if str(line).strip()]
            chunks.extend(lines)
        chunks.extend(part for part in (self.recommendation, self.missing_note, self.final_question) if part)
        return "\n".join(part for part in chunks if part)


@dataclass(frozen=True)
class V0TurnResult:
    ok: bool
    state: V0State
    answer: V0Answer | None = None
    message: str = ""
    error_code: str | None = None
    diagnostics: JsonDict = field(default_factory=dict)
