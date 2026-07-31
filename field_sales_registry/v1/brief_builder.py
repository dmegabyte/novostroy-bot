#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parent
MODULE_FILES = (
    "project.json",
    "apartments.json",
    "readiness.json",
    "transport.json",
    "family.json",
    "yard_safety.json",
    "parking.json",
    "financing.json",
    "investment.json",
    "lots.json",
)
SCENARIOS = {"family", "commute", "budget", "comfort", "safety", "investment", "parking", "readiness", "general"}
STRENGTH_RANK = {"strong": 0, "supporting": 1, "neutral": 2, "weak": 3}
MAX_FIELDS_CAP = 12
MAX_STRING_LENGTH = 240
MAX_LIST_ITEMS = 8
MAX_DIAGNOSTIC_ITEMS = 100
OMIT_REASONS = {"missing", "stale_dynamic", "unsafe_value", "not_relevant", "limit"}
SAFE_FIELD_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _load_json(name: str) -> Any:
    with (ROOT / name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_cards() -> dict[str, dict[str, Any]]:
    cards: dict[str, dict[str, Any]] = {}
    for name in MODULE_FILES:
        data = _load_json(name)
        for card in data.get("cards", []):
            if isinstance(card, dict) and isinstance(card.get("field_id"), str):
                cards[card["field_id"]] = card
    return cards


def _load_combinations() -> list[dict[str, Any]]:
    data = _load_json("combinations.json")
    combinations = data.get("combinations", []) if isinstance(data, dict) else []
    return [item for item in combinations if isinstance(item, dict)]


def _is_present(value: Any, value_type: str) -> bool:
    if value is None:
        return False
    if value is False and value_type == "boolean":
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    if isinstance(value, (list, tuple, dict)) and not value:
        return False
    return True


def _bounded_string(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    return text[:MAX_STRING_LENGTH]


def _safe_primitive(value: Any) -> Any | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    if isinstance(value, str):
        return _bounded_string(value)
    return None


def _safe_value(field_id: str, value: Any, value_type: str) -> Any | None:
    if isinstance(value, Mapping):
        return None
    if field_id == "house_link":
        return None
    if field_id == "lot_status" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return None
    if field_id == "lot_status" and isinstance(value, str) and value.strip().isdigit():
        return None
    if value_type == "boolean":
        return True if value is True else None
    if value_type == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return value
        return None
    if value_type == "percentage":
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            return value
        return None
    if value_type == "months":
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return None
    if value_type == "inventory":
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return value
        if isinstance(value, str):
            return _bounded_string(value)
        return None
    if value_type == "list":
        if not isinstance(value, (list, tuple)):
            return None
        safe_items = []
        for item in value[:MAX_LIST_ITEMS]:
            safe = _safe_primitive(item)
            if safe is not None:
                safe_items.append(safe)
        return safe_items or None
    if value_type in {"text", "money", "area", "floor", "link"}:
        return _safe_primitive(value)
    return None


def _angle_for(card: Mapping[str, Any], scenario: str) -> dict[str, Any] | None:
    angles = [angle for angle in card.get("scenario_angles", []) if isinstance(angle, dict)]
    exact = next((angle for angle in angles if angle.get("scenario") == scenario), None)
    if exact is not None:
        return exact
    return next((angle for angle in angles if angle.get("scenario") == "general"), None)


def _has_exact_or_general(card: Mapping[str, Any], scenario: str) -> bool:
    if scenario == "general":
        return True
    return _angle_for(card, scenario) is not None


def _field_entry(card: Mapping[str, Any], value: Any, angle: Mapping[str, Any] | None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "field_id": card["field_id"],
        "label": card["client_label"],
        "value": value,
        "literal_meaning": card["literal_meaning"],
        "strength": (angle or {}).get("strength") or card["sales_strength"],
        "required_evidence": list(card["required_evidence"]),
        "forbidden_claims": list(card["forbidden_claims"]),
        "rendering_rules": list(card["rendering_rules"]),
    }
    benefit = (angle or {}).get("benefit")
    if benefit:
        entry["allowed_benefit"] = benefit
    return entry


def build_compact_brief(
    facts: Mapping[str, Any],
    scenario: str,
    *,
    fresh_mcp: bool = False,
    requested_fields: Iterable[str] = (),
    max_fields: int = 5,
    object_name: str | None = None,
) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ValueError(f"unsupported scenario: {scenario}")
    if not isinstance(facts, Mapping):
        raise TypeError("facts must be a mapping")

    cards = _load_cards()
    requested_order: dict[str, int] = {}
    for item in requested_fields:
        if isinstance(item, str) and item in cards and item not in requested_order:
            requested_order[item] = len(requested_order)

    selected_limit = max(0, min(int(max_fields), MAX_FIELDS_CAP))
    unknown = sorted(
        {
            text
            for key in facts
            if (text := str(key)) not in cards and SAFE_FIELD_ID.fullmatch(text)
        }
    )[:MAX_DIAGNOSTIC_ITEMS]
    omitted: list[dict[str, str]] = []
    eligible: list[tuple[tuple[int, int, int, str], str, dict[str, Any]]] = []

    for field_id, card in cards.items():
        if field_id not in facts:
            continue
        value_type = str(card.get("value_type", ""))
        raw_value = facts[field_id]
        if not _is_present(raw_value, value_type):
            omitted.append({"field_id": field_id, "reason": "missing"})
            continue
        if card.get("freshness", {}).get("policy") == "dynamic_mcp_required" and not fresh_mcp:
            omitted.append({"field_id": field_id, "reason": "stale_dynamic"})
            continue
        safe_value = _safe_value(field_id, raw_value, value_type)
        if safe_value is None:
            omitted.append({"field_id": field_id, "reason": "unsafe_value"})
            continue
        requested_rank = requested_order.get(field_id)
        requested = requested_rank is not None
        if not requested and not _has_exact_or_general(card, scenario):
            omitted.append({"field_id": field_id, "reason": "not_relevant"})
            continue
        angle = _angle_for(card, scenario) if (requested or _has_exact_or_general(card, scenario)) else None
        relevance_rank = 0
        if scenario != "general":
            if angle and angle.get("scenario") == scenario:
                relevance_rank = 0
            elif angle and angle.get("scenario") == "general":
                relevance_rank = 1
            else:
                relevance_rank = 2
        strength = str((angle or {}).get("strength") or card.get("sales_strength"))
        sort_key = (
            requested_rank if requested_rank is not None else 10_000,
            relevance_rank,
            STRENGTH_RANK.get(strength, 99),
            field_id,
        )
        eligible.append((sort_key, field_id, _field_entry(card, safe_value, angle)))

    eligible.sort(key=lambda item: item[0])
    selected = eligible[:selected_limit]
    selected_ids = {field_id for _, field_id, _ in selected}
    for _, field_id, _ in eligible[selected_limit:]:
        omitted.append({"field_id": field_id, "reason": "limit"})

    combinations: list[dict[str, Any]] = []
    for combo in _load_combinations():
        required_cards = combo.get("required_cards")
        if isinstance(required_cards, list) and all(str(card_id) in selected_ids for card_id in required_cards):
            combinations.append(
                {
                    "id": combo["id"],
                    "client_meaning": combo["client_meaning"],
                    "required_cards": list(required_cards),
                    "required_evidence": list(combo["required_evidence"]),
                    "safe_phrasing": combo["safe_phrasing"],
                    "forbidden_leap": combo["forbidden_leap"],
                }
            )

    clean_omitted = [item for item in omitted if item.get("reason") in OMIT_REASONS]
    result: dict[str, Any] = {
        "schema_version": 1,
        "scenario": scenario,
        "object_name": _bounded_string(object_name) if object_name else None,
        "fresh_mcp": bool(fresh_mcp),
        "fields": [entry for _, _, entry in selected],
        "combinations": combinations,
        "constraints": {
            "facts_are_canonical": True,
            "no_new_facts": True,
            "registry_version": "v1",
        },
        "diagnostics": {
            "unknown_field_ids": unknown,
            "omitted_field_ids": sorted(
                clean_omitted,
                key=lambda item: (item["reason"], item["field_id"]),
            )[:MAX_DIAGNOSTIC_ITEMS],
        },
    }
    return result


def _parse_requested(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a compact read-only brief from canonical field facts.")
    parser.add_argument("input", help="JSON file with normalized canonical facts")
    parser.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    parser.add_argument("--fresh-mcp", action="store_true", help="Allow dynamic MCP-required fields")
    parser.add_argument("--requested", default="", help="Comma-separated known field_ids to prioritize")
    parser.add_argument("--max-fields", type=int, default=5)
    parser.add_argument("--object-name", default=None)
    args = parser.parse_args(argv)

    with Path(args.input).open("r", encoding="utf-8") as fh:
        facts = json.load(fh)
    brief = build_compact_brief(
        facts,
        args.scenario,
        fresh_mcp=args.fresh_mcp,
        requested_fields=_parse_requested(args.requested),
        max_fields=args.max_fields,
        object_name=args.object_name,
    )
    json.dump(brief, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
