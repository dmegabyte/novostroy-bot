#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


MAX_STRING_LENGTH = 240
MAX_DIAGNOSTIC_ITEMS = 100
FIELD_IDS = ("mortgage_rate", "down_payment", "installment_months")
SOURCE_ALLOWLIST = {
    "mortgage_rate": frozenset(("mortgage_calc.min_percent", "mortgage.year_percent")),
    "down_payment": frozenset(("mortgage_calc.min_fee", "mortgage.min_fee")),
    "installment_months": frozenset(("payment_by_installments.month",)),
}
PERCENTAGE_FIELDS = frozenset(("mortgage_rate", "down_payment"))
OMIT_REASONS = frozenset(("missing", "stale", "invalid_value", "invalid_source"))
MERGE_REASONS = frozenset(("merged", "stale_wrapper", "stale_finance", "object_scope_mismatch", "missing_object_scope"))


def _load_sibling(module_name: str, file_name: str):
    try:
        return __import__(module_name, fromlist=["*"])
    except Exception:
        path = Path(__file__).resolve().with_name(file_name)
        spec = importlib.util.spec_from_file_location(f"field_sales_registry_v1_{module_name}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load sibling {module_name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def _bounded_string(value: Any) -> str | None:
    if value is None or isinstance(value, bool) or isinstance(value, (Mapping, list, tuple, set)):
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    return text[:MAX_STRING_LENGTH]


def _safe_percentage(value: Any) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return value
    return None


def _safe_months(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _has_strict_extra_keys(payload: Mapping[str, Any]) -> bool:
    if set(payload) != {"schema_version", "object_name", "fresh_mcp", "facts"}:
        return True
    facts = payload.get("facts")
    if not isinstance(facts, Mapping):
        return True
    if any(key not in FIELD_IDS for key in facts):
        return True
    for item in facts.values():
        if not isinstance(item, Mapping) or set(item) != {"value", "source_field"}:
            return True
    return False


def _source_field(item: Mapping[str, Any]) -> str | None:
    source = item.get("source_field")
    return source if isinstance(source, str) else None


def _omission(field_id: str, reason: str, source_field: str | None = None) -> dict[str, str]:
    out = {"field_id": field_id, "reason": reason if reason in OMIT_REASONS else "invalid_value"}
    if source_field in SOURCE_ALLOWLIST[field_id]:
        out["source_field"] = source_field
    return out


def _empty_result(object_name: str | None, fresh_mcp: bool, reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "object_name": object_name,
        "fresh_mcp": bool(fresh_mcp),
        "facts": {},
        "diagnostics": {
            "accepted_field_ids": [],
            "omitted_field_ids": [_omission(field_id, reason) for field_id in FIELD_IDS],
        },
    }


def adapt_structured_finance(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")

    object_name = _bounded_string(payload.get("object_name"))
    fresh_mcp = payload.get("fresh_mcp") is True
    if payload.get("schema_version") != 1 or object_name is None or not isinstance(payload.get("fresh_mcp"), bool):
        return _empty_result(object_name, fresh_mcp, "invalid_source")
    if _has_strict_extra_keys(payload):
        return _empty_result(object_name, fresh_mcp, "invalid_source")
    if not fresh_mcp:
        return _empty_result(object_name, fresh_mcp, "stale")

    facts_in = payload.get("facts")
    facts: dict[str, Any] = {}
    accepted: list[str] = []
    omitted: list[dict[str, str]] = []
    assert isinstance(facts_in, Mapping)

    for field_id in FIELD_IDS:
        item = facts_in.get(field_id)
        if item is None:
            omitted.append(_omission(field_id, "missing"))
            continue
        assert isinstance(item, Mapping)
        source = _source_field(item)
        if source not in SOURCE_ALLOWLIST[field_id]:
            omitted.append(_omission(field_id, "invalid_source", source))
            continue
        value = _safe_percentage(item.get("value")) if field_id in PERCENTAGE_FIELDS else _safe_months(item.get("value"))
        if value is None:
            omitted.append(_omission(field_id, "invalid_value", source))
            continue
        facts[field_id] = value
        accepted.append(field_id)

    return {
        "schema_version": 1,
        "object_name": object_name,
        "fresh_mcp": True,
        "facts": facts,
        "diagnostics": {
            "accepted_field_ids": accepted[:MAX_DIAGNOSTIC_ITEMS],
            "omitted_field_ids": omitted[:MAX_DIAGNOSTIC_ITEMS],
        },
    }


def build_brief_with_structured_finance(
    card: Mapping[str, Any] | object,
    finance_payload: Mapping[str, Any],
    scenario: str,
    *,
    fresh_mcp: bool = False,
    requested_fields: Sequence[str] = (),
    max_fields: int = 5,
    lot_index: int | None = None,
) -> dict[str, Any]:
    option_card_adapter = _load_sibling("option_card_adapter", "option_card_adapter.py")
    brief_builder = _load_sibling("brief_builder", "brief_builder.py")

    card_adaptation = option_card_adapter.adapt_option_card(card, lot_index=lot_index)
    finance_adaptation = adapt_structured_finance(finance_payload)

    card_name = _bounded_string(card_adaptation.get("object_name"))
    finance_name = _bounded_string(finance_adaptation.get("object_name"))
    scope_match = bool(card_name and finance_name and card_name == finance_name)
    merge_reason = "merged"
    merged_facts = dict(card_adaptation["facts"])
    if not fresh_mcp:
        merge_reason = "stale_wrapper"
    elif finance_adaptation.get("fresh_mcp") is not True:
        merge_reason = "stale_finance"
    elif not card_name or not finance_name:
        merge_reason = "missing_object_scope"
    elif not scope_match:
        merge_reason = "object_scope_mismatch"
    else:
        merged_facts.update(finance_adaptation.get("facts", {}))

    brief = brief_builder.build_compact_brief(
        merged_facts,
        scenario,
        fresh_mcp=bool(fresh_mcp),
        requested_fields=requested_fields,
        max_fields=max_fields,
        object_name=card_adaptation.get("object_name"),
    )
    reason = merge_reason if merge_reason in MERGE_REASONS else "object_scope_mismatch"
    return {
        "card_adaptation": card_adaptation,
        "finance_adaptation": finance_adaptation,
        "brief": brief,
        "merge_diagnostics": {"scope_match": scope_match, "reason": reason},
    }


__all__ = ["adapt_structured_finance", "build_brief_with_structured_finance"]
