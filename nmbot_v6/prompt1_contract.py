"""Strict immutable parser for the verified V6 search-agent result."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .contracts import ContractError
from .privacy import immutable_safe_copy

_ROOT_FIELDS = {
    "action",
    "target",
    "search_policy",
    "clarification_question",
    "response",
    "facts",
    "near",
    "missing",
    "params",
}
_OPTIONAL_ROOT_FIELDS = {"mcp_audit", "requested_claims"}
_PARAM_FIELDS = {
    "rooms",
    "max_price",
    "min_price",
    "district",
    "floor",
    "has_renovation",
    "count",
    "purpose",
    "facets",
    "mortgage_type",
    "delivered",
    "search_mode",
}
_NUMERIC_PARAM_FIELDS = frozenset({"rooms", "floor", "count", "min_price", "max_price"})
_DISTRICTS = {"msk", "mo", "newmsk"}
_FACT_REQUIRED_FIELDS = {"name", "location", "district"}
_NEAR_REQUIRED_FIELDS = {
    "name",
    "location",
    "district",
    "price_range",
    "finishing",
    "why_close",
}
_MAX_FACTS = 20
_MAX_NEAR = 3
_MAX_CARD_FIELDS = 64
_MAX_NESTED_FIELDS = 32
_MAX_CARD_LIST = 20
_MAX_CARD_KEY = 80
_MAX_CARD_STRING = 2_000
_MAX_IDENTITY_STRING = 300
_MAX_PARAM_STRING = 100
_MAX_FACETS = 10
_AUDIT_PHONEISH = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)")
_AUDIT_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


class SearchAction(str, Enum):
    SEARCH = "search"
    CLARIFY = "clarify"
    OPERATOR_CONTACT = "operator_contact"
    RECOVER_DIALOGUE = "recover_dialogue"
    ANSWER_CURRENT_OPTIONS = "answer_current_options"


class SearchTarget(str, Enum):
    NEW_SEARCH = "new_search"
    CURRENT_OPTIONS = "current_options"
    NONE = "none"


class SearchPolicy(str, Enum):
    REQUIRED = "required"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class Prompt1Result:
    action: SearchAction
    target: SearchTarget
    search_policy: SearchPolicy
    clarification_question: str
    response: str
    facts: tuple[Mapping[str, Any], ...]
    near: tuple[Mapping[str, Any], ...]
    missing: tuple[str, ...]
    params: Mapping[str, Any]
    mcp_audit: Mapping[str, Any] | None = None
    requested_claims: tuple[str, ...] = ()


def parse_prompt1(raw: str | Mapping[str, Any]) -> Prompt1Result:
    """Parse exactly the JSON shape emitted by ``v6_search_agent.txt``."""

    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ContractError("Prompt 1 must be one JSON object") from exc
    if (
        not isinstance(data, dict)
        or not set(data).issubset(_ROOT_FIELDS | _OPTIONAL_ROOT_FIELDS)
        or not _ROOT_FIELDS.issubset(data)
    ):
        raise ContractError("Prompt 1 fields do not match the allowlist")

    try:
        action = SearchAction(data["action"])
        target = SearchTarget(data["target"])
        policy = SearchPolicy(data["search_policy"])
    except (TypeError, ValueError) as exc:
        raise ContractError("unknown action, target, or search policy") from exc

    question = _string(data["clarification_question"], "clarification_question")
    response = _string(data["response"], "response")
    facts = _object_list(data["facts"], "facts")
    near = _object_list(data["near"], "near")
    missing = _string_list(data["missing"], "missing")
    params = _params(data["params"])
    mcp_audit = _validate_audit(data.get("mcp_audit"))
    requested_claims = tuple(_string_list(data.get("requested_claims", []), "requested_claims"))

    _validate_options(facts, near)
    _validate_consistency(action, target, policy, question, facts, near, missing, params)
    return Prompt1Result(
        action,
        target,
        policy,
        question,
        response,
        immutable_safe_copy(facts),
        immutable_safe_copy(near),
        immutable_safe_copy(missing),
        immutable_safe_copy(params, allowed_numeric_fields=_NUMERIC_PARAM_FIELDS),
        mcp_audit,
        requested_claims,
    )


def _validate_audit(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        if os.getenv("MCP_AUDIT", "OFF").strip().upper() == "ON":
            raise ContractError("mcp_audit is required when MCP_AUDIT is ON")
        return None
    root = {
        "tool", "arguments", "result_count", "returned_objects", "selected_objects",
        "condition_audit", "truncated", "missing_evidence", "sql_audit",
    }
    if not isinstance(value, Mapping) or set(value) != root:
        raise ContractError("mcp_audit fields are invalid")
    result: dict[str, Any] = {}
    for key in ("tool", "arguments"):
        item = value.get(key)
        if item is not None and (not isinstance(item, str) or len(item) > 2_000):
            raise ContractError(f"mcp_audit.{key} is invalid")
        if isinstance(item, str) and (_AUDIT_PHONEISH.search(item) or _AUDIT_EMAIL.search(item)):
            raise ContractError(f"mcp_audit.{key} contains private data")
        result[key] = item
    sql = value.get("sql_audit")
    if sql is not None:
        if not isinstance(sql, Mapping) or set(sql) != {"query", "parameters"}:
            raise ContractError("mcp_audit.sql_audit is invalid")
        query = sql.get("query")
        parameters = sql.get("parameters")
        if not isinstance(query, str) or len(query) > 2_000 or _AUDIT_PHONEISH.search(query) or _AUDIT_EMAIL.search(query):
            raise ContractError("mcp_audit.sql_audit.query is invalid")
        if not isinstance(parameters, Mapping) or len(parameters) > 32:
            raise ContractError("mcp_audit.sql_audit.parameters is invalid")
        result["sql_audit"] = {"query": query, "parameters": immutable_safe_copy(parameters)}
    else:
        result["sql_audit"] = None
    result_count = value.get("result_count")
    if result_count is not None and (type(result_count) not in (int, str) or len(str(result_count)) > 100):
        raise ContractError("mcp_audit.result_count is invalid")
    result["result_count"] = result_count
    objects = value.get("returned_objects", [])
    if not isinstance(objects, list) or len(objects) > 20:
        raise ContractError("mcp_audit.returned_objects is invalid")
    clean_objects = []
    for item in objects:
        if not isinstance(item, Mapping) or not set(item).issubset({"id", "name", "price_mod", "price1", "price2", "price3", "price4", "price_n", "price_s", "ads"}):
            raise ContractError("mcp_audit object fields are invalid")
        clean = {}
        for key, scalar in item.items():
            if key == "ads":
                if not isinstance(scalar, list) or len(scalar) > 20 or not all(
                    isinstance(ad, Mapping)
                    and set(ad).issubset({"id", "state", "status"})
                    and all(value is None or isinstance(value, (str, int, float, bool)) and len(str(value)) <= 300 for value in ad.values())
                    for ad in scalar
                ):
                    raise ContractError("mcp_audit.ads is invalid")
                clean[key] = tuple({k: ad.get(k) for k in ("id", "state", "status")} for ad in scalar)
            elif scalar is not None and (not isinstance(scalar, (str, int, float, bool)) or len(str(scalar)) > 500):
                raise ContractError("mcp_audit scalar is invalid")
            else:
                if isinstance(scalar, str) and (_AUDIT_PHONEISH.search(scalar) or _AUDIT_EMAIL.search(scalar)):
                    raise ContractError("mcp_audit scalar contains private data")
                clean[key] = scalar
        clean_objects.append(clean)
    result["returned_objects"] = tuple(clean_objects)
    selected = value.get("selected_objects", [])
    missing = value.get("missing_evidence", [])
    if not isinstance(selected, list) or len(selected) > 20 or not all(isinstance(x, str) and len(x) <= 300 for x in selected):
        raise ContractError("mcp_audit.selected_objects is invalid")
    if not isinstance(missing, list) or len(missing) > 20 or not all(isinstance(x, str) and len(x) <= 300 for x in missing):
        raise ContractError("mcp_audit.missing_evidence is invalid")
    result["selected_objects"] = tuple(selected)
    result["missing_evidence"] = tuple(missing)
    condition = value.get("condition_audit", {})
    condition_fields = {"requested_in_prompt", "visible_in_tool_arguments", "visible_in_tool_response", "application_confirmed"}
    if not isinstance(condition, Mapping) or set(condition) != condition_fields:
        raise ContractError("mcp_audit.condition_audit is invalid")
    if any(type(condition.get(key)) is not bool for key in condition):
        raise ContractError("mcp_audit.condition_audit values are invalid")
    result["condition_audit"] = dict(condition)
    truncated = value.get("truncated", False)
    if type(truncated) is not bool:
        raise ContractError("mcp_audit.truncated is invalid")
    result["truncated"] = truncated
    if "sql_audit" not in result:
        result["sql_audit"] = None
    return result


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be a string")
    immutable_safe_copy(value)
    return value


def _object_list(value: Any, field: str) -> list[Mapping[str, Any]]:
    if type(value) is not list or any(not isinstance(item, Mapping) for item in value):
        raise ContractError(f"{field} must be a list of objects")
    immutable_safe_copy(value)
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if type(value) is not list or any(not isinstance(item, str) for item in value):
        raise ContractError(f"{field} must be a list of strings")
    immutable_safe_copy(value)
    return value


def _params(value: Any) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise ContractError("params must be an object")
    value = dict(value)
    for field in ("rooms", "floor", "count"):
        item = value.get(field)
        if isinstance(item, str) and item.strip().isdigit():
            value[field] = int(item.strip())
    if not set(value).issubset(_PARAM_FIELDS):
        raise ContractError("params fields do not match the allowlist")
    for field, item in value.items():
        _validate_param(field, item)
    if "min_price" in value and "max_price" in value and value["min_price"] > value["max_price"]:
        raise ContractError("min_price must not exceed max_price")
    immutable_safe_copy(value, allowed_numeric_fields=_NUMERIC_PARAM_FIELDS)
    return value


def _validate_param(field: str, value: Any) -> None:
    integer_ranges = {
        "rooms": (0, 20),
        "floor": (1, 300),
        "count": (1, _MAX_FACTS),
    }
    if field in integer_ranges:
        low, high = integer_ranges[field]
        if type(value) is not int or not low <= value <= high:
            raise ContractError(f"{field} must be a bounded integer")
    elif field in {"min_price", "max_price"}:
        if type(value) is int:
            valid_price = 0 <= value <= 1_000_000_000_000
        elif type(value) is float:
            valid_price = math.isfinite(value) and 0 <= value <= 1_000_000_000_000
        else:
            valid_price = False
        if not valid_price:
            raise ContractError(f"{field} must be a sensible finite price")
    elif field == "district":
        if not isinstance(value, str) or value not in _DISTRICTS:
            raise ContractError("params district must be an MCP region code")
    elif field == "has_renovation":
        if type(value) is not bool:
            raise ContractError("has_renovation must be boolean")
    elif field == "delivered":
        if value is not None and type(value) is not bool:
            raise ContractError("delivered must be boolean or null")
    elif field == "facets":
        if type(value) is not list or not 1 <= len(value) <= _MAX_FACETS:
            raise ContractError("facets must be a bounded non-empty list")
        if any(not _bounded_text(item, _MAX_PARAM_STRING) for item in value):
            raise ContractError("facets must contain short non-empty strings")
    elif field in {"purpose", "mortgage_type"}:
        if not _bounded_text(value, _MAX_PARAM_STRING):
            raise ContractError(f"{field} must be a short non-empty string")
    elif field == "search_mode":
        if value not in {"broad", "named_object"}:
            raise ContractError("search_mode must be broad or named_object")


def _bounded_text(value: Any, maximum: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _validate_options(
    facts: list[Mapping[str, Any]],
    near: list[Mapping[str, Any]],
) -> None:
    if len(facts) > _MAX_FACTS:
        raise ContractError("facts exceeds the bounded result size")
    if len(near) > _MAX_NEAR:
        raise ContractError("near may contain at most three options")
    for fact in facts:
        if not _FACT_REQUIRED_FIELDS.issubset(fact):
            raise ContractError("facts card is missing required fields")
        _validate_card(fact, "facts")
        for field in _FACT_REQUIRED_FIELDS - {"district"}:
            if not _bounded_text(fact[field], _MAX_IDENTITY_STRING):
                raise ContractError(f"facts {field} must be a short non-empty string")
        if not isinstance(fact["district"], str) or fact["district"] not in _DISTRICTS:
            raise ContractError("facts district must be an MCP region code")
    for option in near:
        if not _NEAR_REQUIRED_FIELDS.issubset(option):
            raise ContractError("near option is missing required fields")
        _validate_card(option, "near")
        for field in _NEAR_REQUIRED_FIELDS - {"district"}:
            if not _bounded_text(option[field], _MAX_IDENTITY_STRING):
                raise ContractError(f"near {field} must be a short non-empty string")
        if not isinstance(option["district"], str) or option["district"] not in _DISTRICTS:
            raise ContractError("near district must be an MCP region code")


def _validate_card(card: Mapping[str, Any], field: str) -> None:
    if not _bounded_text(card.get("name"), _MAX_IDENTITY_STRING):
        raise ContractError(f"{field} card name must be a short non-empty string")
    if len(card) > _MAX_CARD_FIELDS:
        raise ContractError(f"{field} card has too many fields")
    for key, value in card.items():
        if not _bounded_text(key, _MAX_CARD_KEY):
            raise ContractError(f"{field} card keys must be short non-empty strings")
        _validate_card_value(value, field, nested=False)


def _validate_card_value(value: Any, field: str, *, nested: bool) -> None:
    if value is None or type(value) in (bool, int, float):
        return
    if isinstance(value, str):
        if len(value) > _MAX_CARD_STRING:
            raise ContractError(f"{field} card strings are too long")
        return
    if type(value) in (list, tuple):
        if len(value) > _MAX_CARD_LIST:
            raise ContractError(f"{field} card lists are too long")
        for item in value:
            if isinstance(item, Mapping) or type(item) in (list, tuple):
                raise ContractError(f"{field} card lists must contain scalars")
            _validate_card_value(item, field, nested=True)
        return
    if isinstance(value, Mapping) and not nested:
        if len(value) > _MAX_NESTED_FIELDS:
            raise ContractError(f"{field} nested card object has too many fields")
        for key, item in value.items():
            if not _bounded_text(key, _MAX_CARD_KEY) or isinstance(item, Mapping):
                raise ContractError(f"{field} nested card shape is too deep")
            _validate_card_value(item, field, nested=True)
        return
    raise ContractError(f"{field} card contains an unsupported nested value")


def _validate_consistency(
    action: SearchAction,
    target: SearchTarget,
    policy: SearchPolicy,
    question: str,
    facts: list[Mapping[str, Any]],
    near: list[Mapping[str, Any]],
    missing: list[str],
    params: Mapping[str, Any],
) -> None:
    targets = {
        SearchAction.SEARCH: SearchTarget.NEW_SEARCH,
        SearchAction.ANSWER_CURRENT_OPTIONS: SearchTarget.CURRENT_OPTIONS,
        SearchAction.CLARIFY: SearchTarget.NONE,
        SearchAction.OPERATOR_CONTACT: SearchTarget.NONE,
        SearchAction.RECOVER_DIALOGUE: SearchTarget.NONE,
    }
    policies = {
        SearchAction.SEARCH: SearchPolicy.REQUIRED,
        SearchAction.CLARIFY: SearchPolicy.REQUIRED,
        SearchAction.OPERATOR_CONTACT: SearchPolicy.FORBIDDEN,
        SearchAction.RECOVER_DIALOGUE: SearchPolicy.FORBIDDEN,
        SearchAction.ANSWER_CURRENT_OPTIONS: SearchPolicy.FORBIDDEN,
    }
    if target is not targets[action] or policy is not policies[action]:
        raise ContractError("action, target, and search policy disagree")

    if action in {SearchAction.SEARCH, SearchAction.ANSWER_CURRENT_OPTIONS}:
        if question:
            raise ContractError("this action requires an empty clarification question")
    elif not question or len(question) > 300 or (
        action is not SearchAction.OPERATOR_CONTACT and question.count("?") != 1
    ):
        raise ContractError("this action requires exactly one short question")

    if action in {
        SearchAction.CLARIFY,
        SearchAction.OPERATOR_CONTACT,
        SearchAction.RECOVER_DIALOGUE,
        SearchAction.ANSWER_CURRENT_OPTIONS,
    } and (facts or near):
        raise ContractError("this action must not contain property options")
    if action in {SearchAction.OPERATOR_CONTACT, SearchAction.ANSWER_CURRENT_OPTIONS} and params:
        raise ContractError("this action must not change search params")
    if action is SearchAction.ANSWER_CURRENT_OPTIONS and missing:
        raise ContractError("current-options answer must not contain missing search data")
    if params.get("search_mode") == "named_object" and (
        action is not SearchAction.SEARCH
        or policy is not SearchPolicy.REQUIRED
        or params.get("count") != 1
        or len(facts) != 1
        or bool(near)
    ):
        raise ContractError(
            "named_object requires one exact search fact, count one, and no alternatives"
        )
