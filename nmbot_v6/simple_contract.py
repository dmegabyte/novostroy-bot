"""Strict mechanical contracts for the isolated V6-simple prompt pair."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from nmbot_v2.search_contract import COMMON_FACT_FIELDS


class SimpleContractError(ValueError):
    def __init__(self, code: str, *, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


# H108 returns literal MCP material.  The canonical common vocabulary is the
# base; these additions are keys observed in the H108 projection fixtures.
FACT_FIELDS = frozenset(COMMON_FACT_FIELDS | {
    "price_min", "price_range", "ads_count", "location_name", "is_near", "why_close",
    "differences", "ref", "object_id", "option_ref", "novos_id", "new_building_class",
    "area", "floor", "floors_total", "fullprice", "price", "renovation", "status", "title",
    "mortgage_programs", "zhk_name",
})
# Finite H108/V2 source-backed query vocabulary.  These values remain request
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
    required = {"action", "facts", "near", "missing", "params", "ambiguity"}
    if set(root) - (required | {"diagnostics"}) or not required <= set(root):
        raise SimpleContractError("invalid_prompt1_shape")
    action = root["action"]
    if action not in {"continue", "clarify", "request_phone"}:
        raise SimpleContractError("invalid_prompt1_action")
    facts_raw, near_raw, missing_raw, params_raw = root["facts"], root["near"], root["missing"], root["params"]
    if (not isinstance(facts_raw, list) or len(facts_raw) > 20 or not isinstance(near_raw, list)
            or len(near_raw) > 3 or not isinstance(missing_raw, list) or len(missing_raw) > 12
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
    ambiguity_raw = root["ambiguity"]
    ambiguity = None
    if action == "clarify":
        ambiguity_raw = _exact_keys(ambiguity_raw, {"parameter", "reason_code"}, "ambiguity")
        parameter, reason_code = ambiguity_raw["parameter"], ambiguity_raw["reason_code"]
        if not isinstance(parameter, str):
            raise SimpleContractError("invalid_ambiguity_parameter")
        if not isinstance(reason_code, str):
            raise SimpleContractError("invalid_ambiguity_reason_code")
        if parameter not in AMBIGUITY_PARAMETERS:
            raise SimpleContractError("invalid_ambiguity_parameter")
        if reason_code not in AMBIGUITY_REASON_CODES:
            raise SimpleContractError("invalid_ambiguity_reason_code")
        if facts or near or missing:
            raise SimpleContractError("clarify_material_not_empty")
        if parameter in params:
            raise SimpleContractError("ambiguous_parameter_in_params", field=parameter)
        ambiguity = Ambiguity(parameter, reason_code)
    elif ambiguity_raw is not None:
        raise SimpleContractError("unexpected_ambiguity")
    return Prompt1Document(action, tuple(facts), tuple(near), tuple(missing), params, ambiguity)


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


def build_prompt1_input(current_message: str, dialogue_history: list[dict[str, str]], *, pending_offer: str) -> dict[str, Any]:
    if pending_offer not in {"none", "specialist_contact"}:
        raise SimpleContractError("invalid_dialogue_policy")
    return {
        "current_message": current_message, "dialogue_history": dialogue_history,
        "dialogue_policy": {"pending_offer": pending_offer},
    }


def build_prompt2_input(current_message: str, dialogue_history: list[dict[str, str]], result: Prompt1Document, *, offer_specialist_now: bool) -> dict[str, Any]:
    if type(offer_specialist_now) is not bool:
        raise SimpleContractError("invalid_dialogue_policy")
    plain = result.plain()
    return {"current_message": current_message, "dialogue_history": dialogue_history,
            "property_material": {key: plain[key] for key in ("facts", "near", "params")}, "missing": plain["missing"],
            "ambiguity": plain["ambiguity"],
            "dialogue_policy": {"offer_specialist_now": offer_specialist_now}}
