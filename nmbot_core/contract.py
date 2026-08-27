"""Small explicit contracts at the canonical V6 core boundary.

Network transport and model parsing stay outside this skeleton.  These types make
the permitted V6 states visible before the runtime is added.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class CoreContractError(ValueError):
    """A bounded value crossed a V6 owner boundary in an invalid shape."""

    def __init__(self, code: str, *, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


class Prompt1Action(StrEnum):
    CONTINUE = "continue"
    CLARIFY = "clarify"
    REQUEST_PHONE = "request_phone"


class Prompt2Action(StrEnum):
    REPLY = "reply"
    REQUEST_PHONE = "request_phone"


FACT_FIELDS = frozenset({
    "id", "name", "alias", "type_object", "district", "location_id", "location", "street",
    "new_building_class", "building_type", "rooms", "min_price", "max_price", "price1", "price2",
    "price3", "price4", "price_s", "price_n", "price_square", "square_min", "square_max",
    "floors_total", "delivered", "built_year", "ready_quarter", "ready", "status", "lat", "long",
    "distance_from_mkad", "rating", "count_ads", "object_site", "developer", "developer_description",
    "state", "link", "ipoteka", "fz214", "parking", "elevator", "concierge", "garage", "balcony",
    "loggia", "territory", "security", "yard_without_cars", "children_ground", "sports_ground",
    "heating_type", "conditioning_type", "finishing", "apartments", "taunhouse", "site_url",
    "utility_fee", "park_near", "water_near", "trade_in", "is_investment", "school", "kindergarten",
    "ddu_escrow", "ads_type_list", "total_area", "property_metro", "metro", "metro_line",
    "property_railway", "highway_name", "location_2.ecology_rating", "ecology_rating", "house", "ads",
    "ads.fullprice", "ads.id", "ads.price", "ads.area", "ads.rooms", "ads.floor", "ads.floors_total",
    "ads.renovation", "ads.state", "ads.status", "ads.apart", "ads.house_id", "ads_add.stat_price",
    "apartment_types", "mortgage_calc", "mortgage", "discount", "payment_by_installments",
    "apartment_inventory", "available_apartments", "flats_available", "egrn_top_novos", "egrn_contracts",
    "counter_novos", "novos.min_price", "novos.max_price", "infrastructure", "shops", "services",
    "retail", "clinic", "clinics", "pharmacy", "pharmacies", "house.finishing_list", "parking_price",
    "parking_inventory", "parking_count", "garage_price", "garage_count", "ceiling_height", "price_min",
    "price_range", "ads_count", "location_name", "is_near", "why_close", "differences", "ref",
    "object_id", "option_ref", "novos_id", "area", "floor", "fullprice", "price", "renovation",
    "title", "mortgage_programs", "zhk_name",
})
PARAM_FIELDS = frozenset({
    "purpose", "rooms", "location", "max_price", "min_price", "search_mode", "count", "facets",
    "finishing", "ready", "district", "area_min_m2", "area_max_m2", "format", "rooms_preference",
    "budget_preference", "location_preference", "infrastructure_preference", "transport_preference",
    "finance_preference", "sort_hint", "floor", "has_renovation", "name", "mortgage_type", "delivered",
    "only_with_flats", "location_name", "novos_id", "has_finishing",
})
_AMBIGUITY_PARAMETERS = frozenset({"max_price", "min_price", "rooms", "location", "name", "mortgage_type"})
_AMBIGUITY_REASONS = frozenset({"multiple_interpretations", "unparseable_critical_value", "ambiguous_object_identity"})
_MISSING_FIELDS = FACT_FIELDS | {"field", "reason_code", "reason", "details", "property_name"}
_FORBIDDEN_KEY = re.compile(r"(?:phone|телефон|email|e-mail|mail|token|secret|password|client|chat_id|site_id|sender|raw|payload|metadata|diagnostics|event_id|scenario|action|route)", re.IGNORECASE)
_PHONEISH = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,18}(?!\d)")


@dataclass(frozen=True)
class TurnInput:
    """One already-authenticated client message for the runtime."""

    message: str
    session_ref: str
    channel: str = "jivo"

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message.strip() or len(self.message) > 8_000:
            raise CoreContractError("invalid_message")
        if not isinstance(self.session_ref, str) or not self.session_ref or len(self.session_ref) > 160:
            raise CoreContractError("invalid_session_ref")
        if self.channel not in {"jivo", "api"}:
            raise CoreContractError("invalid_channel")


@dataclass(frozen=True)
class Prompt1Document:
    action: Prompt1Action
    facts: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    near: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    missing: tuple[Any, ...] = field(default_factory=tuple)
    params: dict[str, Any] = field(default_factory=dict)
    ambiguity: "Ambiguity | None" = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, Prompt1Action):
            raise CoreContractError("invalid_prompt1_action", field="action")
        if not all(isinstance(item, dict) for item in (*self.facts, *self.near)):
            raise CoreContractError("invalid_prompt1_material")
        if not isinstance(self.params, dict):
            raise CoreContractError("invalid_prompt1_params")
        if self.action is Prompt1Action.CLARIFY and self.ambiguity is None:
            raise CoreContractError("clarify_requires_ambiguity")
        if self.ambiguity is not None and not isinstance(self.ambiguity, Ambiguity):
            raise CoreContractError("invalid_ambiguity")

    def plain(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "facts": [dict(item) for item in self.facts],
            "near": [dict(item) for item in self.near],
            "missing": [dict(item) if isinstance(item, Mapping) else item for item in self.missing],
            "params": dict(self.params),
            "ambiguity": self.ambiguity.plain() if self.ambiguity else None,
        }


@dataclass(frozen=True)
class Prompt2Document:
    action: Prompt2Action
    response: str
    final_question: str

    def __post_init__(self) -> None:
        if not isinstance(self.action, Prompt2Action):
            raise CoreContractError("invalid_prompt2_action", field="action")
        if not isinstance(self.response, str) or len(self.response) > 2_000:
            raise CoreContractError("invalid_prompt2_response")
        if not isinstance(self.final_question, str) or len(self.final_question) > 2_000:
            raise CoreContractError("invalid_prompt2_final_question")
        if self.action is Prompt2Action.REPLY and not self.response.strip():
            raise CoreContractError("invalid_prompt2_response")
        if self.action is Prompt2Action.REQUEST_PHONE and (self.response or self.final_question):
            raise CoreContractError("request_phone_fields_not_empty")


@dataclass(frozen=True)
class TerminalResponse:
    """The only runtime-owned terminal intent before the API renders Jivo JSON."""

    text: str
    event: str = "BOT_MESSAGE"
    request_phone: bool = False
    handoff_to_operator: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip() or len(self.text) > 8_000:
            raise CoreContractError("invalid_terminal_text")
        if self.event not in {"BOT_MESSAGE", "INVITE_AGENT"}:
            raise CoreContractError("invalid_terminal_event")
        if self.event == "INVITE_AGENT" and not self.handoff_to_operator:
            raise CoreContractError("invite_requires_handoff")
        if self.request_phone and self.event != "BOT_MESSAGE":
            raise CoreContractError("phone_request_requires_bot_message")


@dataclass(frozen=True)
class Ambiguity:
    parameter: str
    reason_code: str

    def plain(self) -> dict[str, str]:
        return {"parameter": self.parameter, "reason_code": self.reason_code}


def _load(raw: str | Mapping[str, Any], *, maximum: int) -> Mapping[str, Any]:
    if isinstance(raw, str):
        if len(raw) > maximum:
            raise CoreContractError("output_too_large")
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CoreContractError("invalid_json") from exc
    if not isinstance(raw, Mapping):
        raise CoreContractError("root_not_object")
    try:
        rendered = json.dumps(raw, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CoreContractError("not_json_compatible") from exc
    if len(rendered) > maximum:
        raise CoreContractError("output_too_large")
    return raw


def _safe_key(key: Any, kind: str) -> str:
    if not isinstance(key, str) or not key or len(key) > 100 or _PHONEISH.search(key) or _FORBIDDEN_KEY.search(key):
        raise CoreContractError(f"invalid_{kind}_key")
    return key


def _json_value(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> Any:
    nodes = nodes if nodes is not None else [0]
    nodes[0] += 1
    if depth > 5 or nodes[0] > 200:
        raise CoreContractError("fact_value_too_large")
    if isinstance(value, str):
        if len(value) > 1000 or _PHONEISH.search(value):
            raise CoreContractError("privacy_violation")
        return value
    if value is None or type(value) in (bool, int, float):
        if isinstance(value, float) and not math.isfinite(value):
            raise CoreContractError("invalid_number")
        return value
    if isinstance(value, list) and len(value) <= 20:
        return [_json_value(item, depth=depth + 1, nodes=nodes) for item in value]
    if isinstance(value, Mapping) and len(value) <= 30:
        return {_safe_key(key, "fact"): _json_value(item, depth=depth + 1, nodes=nodes) for key, item in value.items()}
    raise CoreContractError("invalid_fact_value")


def _material(raw: Any, *, kind: str, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or len(raw) > 64:
        raise CoreContractError(f"invalid_{kind}_shape")
    result: dict[str, Any] = {}
    for key, value in raw.items():
        key = _safe_key(key, kind)
        if key not in allowed:
            raise CoreContractError(f"invalid_{kind}_field", field=key)
        result[key] = _json_value(value)
    return result


def _params(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or len(raw) > len(PARAM_FIELDS):
        raise CoreContractError("invalid_prompt1_bounds")
    result: dict[str, Any] = {}
    for key, value in raw.items():
        key = _safe_key(key, "param")
        if key not in PARAM_FIELDS:
            raise CoreContractError("invalid_param_key", field=key)
        result[key] = _json_value(value)
    return result


def parse_prompt1(raw: str | Mapping[str, Any]) -> Prompt1Document:
    root = _load(raw, maximum=12_000)
    action = root.get("action")
    if action not in {item.value for item in Prompt1Action}:
        raise CoreContractError("invalid_prompt1_action")
    if action == Prompt1Action.REQUEST_PHONE.value:
        if set(root) != {"action"}:
            raise CoreContractError("invalid_prompt1_variant_shape")
        return Prompt1Document(Prompt1Action.REQUEST_PHONE)
    if action == Prompt1Action.CLARIFY.value:
        if set(root) != {"action", "params", "ambiguity"}:
            raise CoreContractError("invalid_prompt1_variant_shape")
        ambiguity = root["ambiguity"]
        if not isinstance(ambiguity, Mapping) or set(ambiguity) != {"parameter", "reason_code"}:
            raise CoreContractError("invalid_ambiguity_shape")
        parameter, reason = ambiguity["parameter"], ambiguity["reason_code"]
        if parameter not in _AMBIGUITY_PARAMETERS or reason not in _AMBIGUITY_REASONS:
            raise CoreContractError("invalid_ambiguity")
        params = _params(root["params"])
        if parameter in params:
            raise CoreContractError("ambiguous_parameter_in_params", field=str(parameter))
        return Prompt1Document(Prompt1Action.CLARIFY, params=params, ambiguity=Ambiguity(parameter, reason))
    allowed = {"action", "facts", "near", "missing", "params", "ambiguity"}
    if set(root) - allowed or not {"action", "facts", "near", "missing", "params"} <= set(root):
        raise CoreContractError("invalid_prompt1_variant_shape")
    if root.get("ambiguity") is not None:
        raise CoreContractError("unexpected_ambiguity")
    facts, near, missing = root["facts"], root["near"], root["missing"]
    if not isinstance(facts, list) or len(facts) > 20 or not isinstance(near, list) or len(near) > 5 or not isinstance(missing, list) or len(missing) > 12:
        raise CoreContractError("invalid_prompt1_bounds")
    normalized_missing: list[Any] = []
    for item in missing:
        if isinstance(item, str) and item.strip() and len(item) <= 300 and not _PHONEISH.search(item):
            normalized_missing.append(item)
        elif isinstance(item, Mapping):
            normalized_missing.append(_material(item, kind="missing", allowed=_MISSING_FIELDS))
        else:
            raise CoreContractError("invalid_missing")
    return Prompt1Document(
        Prompt1Action.CONTINUE,
        facts=tuple(_material(item, kind="fact", allowed=FACT_FIELDS) for item in facts),
        near=tuple(_material(item, kind="near", allowed=FACT_FIELDS) for item in near),
        missing=tuple(normalized_missing),
        params=_params(root["params"]),
    )


def parse_prompt2(raw: str | Mapping[str, Any], *, allow_request_phone: bool = True, require_final_question: bool = False) -> Prompt2Document:
    root = _load(raw, maximum=2_200)
    if set(root) != {"action", "response", "final_question"}:
        raise CoreContractError("invalid_prompt2_shape")
    action, response, final_question = root["action"], root["response"], root["final_question"]
    if action not in {item.value for item in Prompt2Action} or not isinstance(response, str) or not isinstance(final_question, str):
        raise CoreContractError("invalid_prompt2")
    if _PHONEISH.search(response) or _PHONEISH.search(final_question):
        raise CoreContractError("privacy_violation")
    if action == Prompt2Action.REQUEST_PHONE.value:
        if not allow_request_phone:
            raise CoreContractError("prompt2_cannot_request_phone")
        if response or final_question:
            raise CoreContractError("request_phone_fields_not_empty")
    elif not response.strip():
        raise CoreContractError("empty_reply")
    if require_final_question and (action != Prompt2Action.REPLY.value or not final_question.strip()):
        raise CoreContractError("clarification_question_required")
    if len(response) + len(final_question) + (2 if final_question else 0) > 2000:
        raise CoreContractError("output_too_large")
    return Prompt2Document(Prompt2Action(action), response, final_question)


def build_prompt1_input(current_message: str, dialogue_history: list[dict[str, str]], *, pending_offer: str) -> dict[str, Any]:
    if pending_offer not in {"none", "specialist_contact"}:
        raise CoreContractError("invalid_dialogue_policy")
    return {"current_message": current_message, "dialogue_history": dialogue_history, "dialogue_policy": {"pending_offer": pending_offer}}


def build_prompt2_input(current_message: str, dialogue_history: list[dict[str, str]], result: Prompt1Document, *, offer_specialist_now: bool) -> dict[str, Any]:
    if type(offer_specialist_now) is not bool:
        raise CoreContractError("invalid_dialogue_policy")
    plain = result.plain()
    facts = plain["facts"][:3]
    near = plain["near"][:3 - len(facts)]
    return {
        "current_message": current_message,
        "dialogue_history": dialogue_history,
        "property_material": {"facts": facts, "near": near, "params": plain["params"]},
        "missing": list(plain["missing"]),
        "ambiguity": plain["ambiguity"],
        "dialogue_policy": {"offer_specialist_now": offer_specialist_now},
    }
