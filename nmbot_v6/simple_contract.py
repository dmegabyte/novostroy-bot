"""Strict mechanical contracts for the isolated V6-simple prompt pair."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

COMMON_FACT_FIELDS = frozenset({
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
    "property_railway", "highway_name", "location_2.ecology_rating", "ecology_rating", "house", "ads", "ads.fullprice",
    "ads.id", "ads.price", "ads.area", "ads.rooms", "ads.floor", "ads.floors_total", "ads.renovation", "ads.state", "ads.status", "ads.apart", "ads.house_id",
    "ads_add.stat_price", "apartment_types", "mortgage_calc", "mortgage", "discount",
    "payment_by_installments", "apartment_inventory", "available_apartments", "flats_available", "egrn_top_novos", "egrn_contracts", "counter_novos",
    "novos.min_price", "novos.max_price", "infrastructure", "shops", "services", "retail", "clinic", "clinics", "pharmacy", "pharmacies", "house.finishing_list", "parking_price", "parking_inventory", "parking_count", "garage_price", "garage_count", "ceiling_height",
})


class SimpleContractError(ValueError):
    def __init__(self, code: str, *, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


# H108 returns literal MCP material. V6 owns the complete source-backed common
# vocabulary; these additions are keys observed in the H108 projection fixtures.
FACT_FIELDS = frozenset(COMMON_FACT_FIELDS | {
    "price_min", "price_range", "ads_count", "location_name", "is_near", "why_close",
    "differences", "ref", "object_id", "option_ref", "novos_id", "new_building_class",
    "area", "floor", "floors_total", "fullprice", "price", "renovation", "status", "title",
    "mortgage_programs", "zhk_name",
})
# Finite H108 source-backed query vocabulary.  These values remain request
# context only; Prompt 2 must not treat them as evidence about any property.
PARAM_FIELDS = frozenset({
    "purpose", "rooms", "location", "max_price", "min_price", "search_mode",
    "count", "facets", "finishing", "ready", "district", "area_min_m2",
    "area_max_m2", "format", "rooms_preference", "budget_preference",
    "location_preference", "infrastructure_preference", "transport_preference",
    "finance_preference", "sort_hint", "floor", "has_renovation", "name",
    "mortgage_type", "delivered", "only_with_flats", "location_name", "novos_id", "has_finishing",
})
AMBIGUITY_PARAMETERS = frozenset({
    "max_price", "min_price", "rooms", "location", "name", "mortgage_type",
})
AMBIGUITY_REASON_CODES = frozenset({
    "multiple_interpretations", "unparseable_critical_value", "ambiguous_object_identity",
})
_MISSING_FIELDS = FACT_FIELDS | {"field", "reason_code", "reason", "details", "property_name"}
_FORBIDDEN_KEY = re.compile(
    r"(?:phone|телефон|email|e-mail|mail|token|secret|password|client|chat_id|"
    r"site_id|sender|raw|payload|metadata|diagnostics|event_id|scenario|action|route)",
    re.IGNORECASE,
)
PHONEISH = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,18}(?!\d)")

# The URL-card parser is allowed to return a richer page envelope, but Prompt
# 2 receives only this bounded, source-backed projection. In particular,
# transport/source metadata and raw HTML never cross the model boundary.
URL_CARD_FIELDS = frozenset({
    "object_type", "complex_name", "developer", "area_m2", "floor", "floors_total",
    "price_rub", "previous_price_rub", "price_history", "price_per_m2_rub",
    "mortgage_from_rub_per_month", "completion", "construction_stage", "finishing",
    "location", "address", "building", "section", "metro", "railway_station",
    "highway", "listing_number", "payment_terms", "installment_terms", "special_offers",
})
URL_CARD_DERIVED_FIELDS = frozenset({"price_difference_rub", "price_difference_is_not_a_promotion"})


@dataclass(frozen=True)
class Ambiguity:
    parameter: str
    reason_code: str

    def plain(self) -> dict[str, str]:
        return {"parameter": self.parameter, "reason_code": self.reason_code}


@dataclass(frozen=True)
class Prompt1Document:
    action: str
    facts: tuple[dict[str, Any], ...]
    near: tuple[dict[str, Any], ...]
    missing: tuple[Any, ...]
    params: dict[str, Any]
    ambiguity: Ambiguity | None

    def plain(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "facts": [dict(item) for item in self.facts],
            "near": [dict(item) for item in self.near],
            "missing": [dict(item) if isinstance(item, Mapping) else item for item in self.missing],
            "params": dict(self.params),
            "ambiguity": self.ambiguity.plain() if self.ambiguity else None,
        }


@dataclass(frozen=True)
class Prompt2Document:
    action: str
    response: str
    final_question: str


def _load(raw: str | Mapping[str, Any], *, max_chars: int) -> Mapping[str, Any]:
    if isinstance(raw, str):
        if len(raw) > max_chars:
            raise SimpleContractError("output_too_large")
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SimpleContractError("invalid_json") from exc
    if not isinstance(raw, Mapping):
        raise SimpleContractError("root_not_object")
    try:
        rendered = json.dumps(raw, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SimpleContractError("not_json_compatible") from exc
    if len(rendered) > max_chars:
        raise SimpleContractError("output_too_large")
    return raw


def _text(value: Any, name: str, *, maximum: int, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise SimpleContractError(f"invalid_{name}")
    if PHONEISH.search(value):
        raise SimpleContractError("privacy_violation")
    return value


def _safe_key(key: Any, name: str) -> str:
    if not isinstance(key, str) or not key or len(key) > 100 or PHONEISH.search(key) or _FORBIDDEN_KEY.search(key):
        raise SimpleContractError(f"invalid_{name}_key")
    return key


def _json_value(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> Any:
    nodes = nodes if nodes is not None else [0]
    nodes[0] += 1
    if depth > 5 or nodes[0] > 200:
        raise SimpleContractError("fact_value_too_large")
    if isinstance(value, str):
        return _text(value, "fact_value", maximum=1000, empty=True)
    if value is None or type(value) in (bool, int, float):
        if isinstance(value, float) and not math.isfinite(value):
            raise SimpleContractError("invalid_number")
        return value
    if isinstance(value, list) and len(value) <= 20:
        return [_json_value(item, depth=depth + 1, nodes=nodes) for item in value]
    if isinstance(value, Mapping) and len(value) <= 30:
        result = {}
        for key, item in value.items():
            result[_safe_key(key, "fact")] = _json_value(item, depth=depth + 1, nodes=nodes)
        return result
    raise SimpleContractError("invalid_fact_value")


def _exact_keys(value: Any, keys: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise SimpleContractError(f"invalid_{name}_shape")
    return value


def _literal_item(raw: Any, *, name: str, allowed_keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or len(raw) > 64:
        raise SimpleContractError(f"invalid_{name}_shape")
    result = {}
    for key, value in raw.items():
        key = _safe_key(key, name)
        if key not in allowed_keys:
            raise SimpleContractError(f"invalid_{name}_field", field=key)
        result[key] = _json_value(value)
    return result


def parse_prompt1(raw: str | Mapping[str, Any]) -> Prompt1Document:
    root = _load(raw, max_chars=12_000)
    if "action" not in root:
        raise SimpleContractError("invalid_prompt1_shape")
    action = root["action"]
    if action not in {"continue", "clarify", "request_phone"}:
        raise SimpleContractError("invalid_prompt1_action")

    # Prompt 1 may omit the nullable continue-only field. Normalize it before
    # variant validation so every internal document still has ambiguity=None.
    if action == "continue" and "ambiguity" not in root:
        root = {**root, "ambiguity": None}

    optional = {"diagnostics"}
    if action == "request_phone":
        if set(root) - ({"action"} | optional):
            raise SimpleContractError("invalid_prompt1_variant_shape")
        return Prompt1Document(action, (), (), (), {}, None)

    if action == "clarify":
        required = {"action", "ambiguity", "params"}
        if set(root) - (required | optional) or not required <= set(root):
            raise SimpleContractError("invalid_prompt1_variant_shape")
        params_raw = root["params"]
        if not isinstance(params_raw, Mapping) or len(params_raw) > len(PARAM_FIELDS):
            raise SimpleContractError("invalid_prompt1_bounds")
        params = {}
        for key, value in params_raw.items():
            key = _safe_key(key, "param")
            if key not in PARAM_FIELDS:
                raise SimpleContractError("invalid_param_key", field=key)
            params[key] = _json_value(value)
        ambiguity_raw = _exact_keys(root["ambiguity"], {"parameter", "reason_code"}, "ambiguity")
        parameter, reason_code = ambiguity_raw["parameter"], ambiguity_raw["reason_code"]
        if not isinstance(parameter, str) or parameter not in AMBIGUITY_PARAMETERS:
            raise SimpleContractError("invalid_ambiguity_parameter")
        if not isinstance(reason_code, str) or reason_code not in AMBIGUITY_REASON_CODES:
            raise SimpleContractError("invalid_ambiguity_reason_code")
        if parameter in params:
            raise SimpleContractError("ambiguous_parameter_in_params", field=parameter)
        return Prompt1Document(action, (), (), (), params, Ambiguity(parameter, reason_code))

    required = {"action", "facts", "near", "missing", "params", "ambiguity"}
    if set(root) - (required | optional) or not required <= set(root):
        raise SimpleContractError("invalid_prompt1_variant_shape")
    facts_raw, near_raw, missing_raw, params_raw = root["facts"], root["near"], root["missing"], root["params"]
    if (not isinstance(facts_raw, list) or len(facts_raw) > 20 or not isinstance(near_raw, list)
            or len(near_raw) > 5 or not isinstance(missing_raw, list) or len(missing_raw) > 12
            or not isinstance(params_raw, Mapping) or len(params_raw) > len(PARAM_FIELDS)):
        raise SimpleContractError("invalid_prompt1_bounds")
    facts = [_literal_item(item, name="fact", allowed_keys=FACT_FIELDS) for item in facts_raw]
    near = [_literal_item(item, name="near", allowed_keys=FACT_FIELDS) for item in near_raw]
    missing: list[Any] = []
    for raw_item in missing_raw:
        if isinstance(raw_item, str):
            missing.append(_text(raw_item, "missing", maximum=300))
            continue
        missing.append(_literal_item(raw_item, name="missing", allowed_keys=_MISSING_FIELDS))
    params = {}
    for key, value in params_raw.items():
        key = _safe_key(key, "param")
        if key not in PARAM_FIELDS:
            raise SimpleContractError("invalid_param_key", field=key)
        params[key] = _json_value(value)
    if root["ambiguity"] is not None:
        raise SimpleContractError("unexpected_ambiguity")
    return Prompt1Document(action, tuple(facts), tuple(near), tuple(missing), params, None)


def parse_prompt2(
    raw: str | Mapping[str, Any], *, allow_request_phone: bool = True,
    require_final_question: bool = False,
) -> Prompt2Document:
    root = _exact_keys(_load(raw, max_chars=2_200), {"action", "response", "final_question"}, "prompt2")
    action, response, final_question = root["action"], root["response"], root["final_question"]
    if action not in {"reply", "request_phone"} or not isinstance(response, str) or not isinstance(final_question, str):
        raise SimpleContractError("invalid_prompt2")
    if action == "request_phone" and not allow_request_phone:
        raise SimpleContractError("prompt2_cannot_request_phone")
    if PHONEISH.search(response) or PHONEISH.search(final_question):
        raise SimpleContractError("privacy_violation")
    if action == "reply" and not response.strip():
        raise SimpleContractError("empty_reply")
    if require_final_question and (action != "reply" or not final_question.strip()):
        raise SimpleContractError("clarification_question_required")
    if action == "request_phone" and (response != "" or final_question != ""):
        raise SimpleContractError("request_phone_fields_not_empty")
    published_length = len(response) + len(final_question) + (2 if final_question else 0)
    if published_length > 2000:
        raise SimpleContractError("output_too_large")
    return Prompt2Document(action, response, final_question)


def project_url_card_for_prompt2(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the bounded URL-card fields that Prompt 2 may use."""

    if not isinstance(raw, Mapping) or not isinstance(raw.get("card"), Mapping):
        raise SimpleContractError("invalid_url_card_shape")
    card_raw = raw["card"]
    card = {
        key: _json_value(card_raw[key])
        for key in URL_CARD_FIELDS
        if key in card_raw
    }
    if not card:
        raise SimpleContractError("invalid_url_card_shape")

    missing_raw = raw.get("missing", [])
    if not isinstance(missing_raw, list) or len(missing_raw) > 12:
        raise SimpleContractError("invalid_url_card_missing")
    missing = [_text(item, "url_card_missing", maximum=300) for item in missing_raw]

    derived_raw = raw.get("derived")
    if not isinstance(derived_raw, Mapping):
        raise SimpleContractError("invalid_url_card_derived")
    if derived_raw.get("price_difference_is_not_a_promotion") is not True:
        raise SimpleContractError("invalid_url_card_derived")
    derived = {
        key: _json_value(derived_raw[key])
        for key in URL_CARD_DERIVED_FIELDS
        if key in derived_raw
    }
    if derived.get("price_difference_is_not_a_promotion") is not True:
        raise SimpleContractError("invalid_url_card_derived")

    result: dict[str, Any] = {"card": card, "missing": missing, "derived": derived}
    page_updated = raw.get("page_updated")
    if page_updated is not None:
        result["page_updated"] = _text(page_updated, "url_card_page_updated", maximum=100)
    return result


def build_prompt1_input(current_message: str, dialogue_history: list[dict[str, str]], *, pending_offer: str) -> dict[str, Any]:
    if pending_offer not in {"none", "specialist_contact"}:
        raise SimpleContractError("invalid_dialogue_policy")
    return {
        "current_message": current_message, "dialogue_history": dialogue_history,
        "dialogue_policy": {"pending_offer": pending_offer},
    }


def build_prompt2_input(
    current_message: str,
    dialogue_history: list[dict[str, str]],
    result: Prompt1Document | None,
    *,
    offer_specialist_now: bool,
    url_card: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if type(offer_specialist_now) is not bool:
        raise SimpleContractError("invalid_dialogue_policy")
    if result is None and url_card is None:
        raise SimpleContractError("missing_prompt2_material")
    plain = result.plain() if result is not None else {
        "facts": [], "near": [], "params": {}, "missing": [], "ambiguity": None,
    }
    facts = plain["facts"][:3]
    near = plain["near"][:3 - len(facts)]
    property_material: dict[str, Any] = {"facts": facts, "near": near, "params": plain["params"]}
    missing = list(plain["missing"])
    if url_card is not None:
        projected_url_card = project_url_card_for_prompt2(url_card)
        property_material["url_card"] = projected_url_card
        missing = [*missing, *projected_url_card["missing"]][:12]
    return {
        "current_message": current_message,
        "dialogue_history": dialogue_history,
        "property_material": property_material,
        "missing": missing,
        "ambiguity": plain["ambiguity"],
        "dialogue_policy": {"offer_specialist_now": offer_specialist_now},
    }
