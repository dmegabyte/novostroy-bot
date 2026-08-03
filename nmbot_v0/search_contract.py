"""Closed, V0-owned search boundary.

This is deliberately limited to the request and output semantics consumed by
``nmbot_v0.runtime``.  It never imports another runtime's contracts or gateway.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


OUTPUT_TOP_LEVEL_KEYS = {"facts", "near", "missing", "params", "diagnostics"}
DIAGNOSTIC_KEYS = {"mcp_tool", "response_viewpoint", "base_viewpoint", "requested_field_priorities", "relaxation_audit", "ignored_preferences", "notes"}
HARD_KEYS = {"district", "location", "rooms", "max_price", "min_price", "ready", "finishing", "area_min_m2", "area_max_m2", "name"}
HARD_EVIDENCE = {
    "rooms": ("rooms", "ads.rooms"), "max_price": ("min_price", "max_price", "price1", "price2", "price3", "price4", "price_s", "price_n", "ads.fullprice", "ads.price", "novos.min_price"),
    "min_price": ("min_price", "max_price", "price1", "price2", "price3", "price4", "price_s", "price_n", "ads.fullprice", "ads.price", "novos.max_price"),
    "area_min_m2": ("square_min", "square_max", "ads.area"), "area_max_m2": ("square_min", "square_max", "ads.area"),
    "district": ("district",), "location": ("location", "location_id", "district"), "ready": ("ready", "delivered", "state", "status"), "finishing": ("finishing", "ads.renovation", "house.finishing_list"), "name": ("name", "alias"),
}
_SENSITIVE = re.compile(r"phone|телефон|email|mail|token|secret|password|client|chat_id|raw|payload", re.I)


@dataclass(frozen=True)
class V0SearchRequest:
    search_goal: dict[str, Any]
    requested_hard: dict[str, Any] = field(default_factory=dict)
    effective_hard: dict[str, Any] = field(default_factory=dict)
    preferences: dict[str, Any] = field(default_factory=dict)
    response_viewpoint: str = "life"
    base_viewpoint: str | None = None
    available_fact_fields: list[str] = field(default_factory=list)
    count: int = 3

    def __post_init__(self) -> None:
        if not isinstance(self.search_goal, dict) or not isinstance(self.requested_hard, dict) or not isinstance(self.effective_hard, dict):
            raise ValueError("invalid_v0_search_request")
        if not isinstance(self.count, int) or not 1 <= self.count <= 3:
            raise ValueError("invalid_v0_search_count")
        if self.response_viewpoint not in {"life", "family", "rental", "investment", "financing"}:
            raise ValueError("invalid_v0_search_viewpoint")
        if not isinstance(self.available_fact_fields, list) or not self.available_fact_fields:
            raise ValueError("invalid_v0_available_fact_fields")
        object.__setattr__(self, "requested_hard", _safe_hard(self.requested_hard))
        object.__setattr__(self, "effective_hard", _safe_hard(self.effective_hard))
        object.__setattr__(self, "preferences", _safe_mapping(self.preferences))
        object.__setattr__(self, "available_fact_fields", list(dict.fromkeys(str(x) for x in self.available_fact_fields if isinstance(x, str) and x and not _SENSITIVE.search(x))))

    def to_payload(self) -> dict[str, Any]:
        return {"search_goal": dict(self.search_goal), "constraints": {"requested_hard": dict(self.requested_hard), "effective_hard": dict(self.effective_hard), "preferences": dict(self.preferences)}, "response_viewpoint": self.response_viewpoint, "base_viewpoint": self.base_viewpoint, "available_fact_fields": list(self.available_fact_fields), "count": self.count}


def parse_strict_json(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        data = json.loads(str(text or "").strip())
    except json.JSONDecodeError as exc:
        return None, [f"invalid_strict_json:{exc.msg}"]
    return (data, []) if isinstance(data, dict) else (None, ["json_root_must_be_object"])


def normalize_search_output(output: Mapping[str, Any], request: V0SearchRequest) -> dict[str, Any]:
    source = output if isinstance(output, Mapping) else {}
    notes: list[str] = []
    facts = _sanitize_cards(source.get("facts"), request, near=False, notes=notes)
    near = _sanitize_cards(source.get("near"), request, near=True, notes=notes)
    missing = [_safe_missing(item) for item in source.get("missing", [])[:20]] if isinstance(source.get("missing"), list) else []
    return {"facts": facts, "near": near, "missing": [item for item in missing if item], "params": {**request.effective_hard, **request.preferences}, "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "response_viewpoint": request.response_viewpoint, "base_viewpoint": request.base_viewpoint, "requested_field_priorities": list(request.available_fact_fields), "relaxation_audit": [], "ignored_preferences": [], "notes": notes}}


def validate_search_output(output: Mapping[str, Any], request: V0SearchRequest) -> dict[str, Any]:
    errors: list[str] = []
    if set(output) != OUTPUT_TOP_LEVEL_KEYS:
        errors.append("top_level_keys_mismatch")
    for key, expected in (("facts", list), ("near", list), ("missing", list), ("params", dict), ("diagnostics", dict)):
        if not isinstance(output.get(key), expected):
            errors.append(f"{key}_must_be_{'object' if expected is dict else 'list'}")
    diagnostics = output.get("diagnostics") if isinstance(output.get("diagnostics"), Mapping) else {}
    if set(diagnostics) != DIAGNOSTIC_KEYS:
        errors.append("diagnostics_keys_mismatch")
    for index, card in enumerate(output.get("facts") if isinstance(output.get("facts"), list) else []):
        if not isinstance(card, Mapping):
            errors.append(f"fact_{index}_must_be_object")
            continue
        if set(card) - set(request.available_fact_fields):
            errors.append(f"fact_{index}_has_non_whitelisted_fields")
        for field, expected in request.effective_hard.items():
            if not _evidence(card, field): errors.append(f"fact_{index}_missing_hard_evidence:{field}")
            elif not _matches(card, field, expected): errors.append(f"fact_{index}_violates_hard:{field}")
    for index, card in enumerate(output.get("near") if isinstance(output.get("near"), list) else []):
        if not isinstance(card, Mapping) or card.get("is_near") is not True or not str(card.get("why_close") or "").strip():
            errors.append(f"near_{index}_invalid")
    return {"ok": not errors, "status": "valid" if not errors else "invalid", "errors": errors, "warnings": [], "counts": {"facts": len(output.get("facts", [])), "near": len(output.get("near", [])), "missing": len(output.get("missing", [])), "warnings": 0}}


def load_prompt(path: Path | None = None) -> str:
    root = Path(__file__).resolve().parents[1]
    return (path or root / "prompts" / "v0_scenario_search.txt").read_text(encoding="utf-8")


def _safe_hard(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _safe_value(item) for key, item in value.items() if str(key) in HARD_KEYS and _safe_value(item) not in (None, "", [], {})}


def _safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _safe_value(item) for key, item in value.items() if not _SENSITIVE.search(str(key)) and _safe_value(item) not in (None, "", [], {})}


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping): return _safe_mapping(value)
    if isinstance(value, (list, tuple)): return [_safe_value(item) for item in value[:10] if _safe_value(item) not in (None, "", [], {})]
    if isinstance(value, str): return " ".join(value.split())[:200]
    return value if isinstance(value, (int, float, bool)) or value is None else str(value)[:200]


def _sanitize_cards(value: Any, request: V0SearchRequest, *, near: bool, notes: list[str]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    if not isinstance(value, list):
        notes.append("contract_blocker:cards_must_be_list")
        return cards
    for raw in value[:3]:
        if not isinstance(raw, Mapping): continue
        card = {str(key): _safe_value(item) for key, item in raw.items() if str(key) in request.available_fact_fields and _safe_value(item) not in (None, "", [], {})}
        if not (card.get("id") or card.get("alias") or card.get("name")): continue
        if near:
            card["is_near"] = True
            card["why_close"] = _near_reason(card, request)
            card["differences"] = [card["why_close"]]
        cards.append(card)
    return cards


def _safe_missing(value: Any) -> str | None:
    text = str(value.get("field") if isinstance(value, Mapping) else value or "").strip()
    return None if not text or _SENSITIVE.search(text) else text[:120]


def _nested(item: Mapping[str, Any], path: str) -> list[Any]:
    current = [item]
    for part in path.split("."):
        following = []
        for node in current:
            if isinstance(node, Mapping) and part in node: following.append(node[part])
            elif isinstance(node, list): following.extend(child.get(part) for child in node if isinstance(child, Mapping) and part in child)
        current = following
    return current


def _evidence(item: Mapping[str, Any], field: str) -> bool:
    return any(value not in (None, "", [], {}) for path in HARD_EVIDENCE.get(field, (field,)) for value in _nested(item, path))


def _numbers(values: list[Any]) -> list[float]:
    return [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]


def _matches(item: Mapping[str, Any], field: str, expected: Any) -> bool:
    if field in {"max_price", "min_price"}:
        values = _numbers([x for path in HARD_EVIDENCE[field] for x in _nested(item, path)])
        return bool(values) and isinstance(expected, (int, float)) and (min(values) <= expected if field == "max_price" else max(values) >= expected)
    if field in {"area_min_m2", "area_max_m2"}:
        values = _numbers([x for path in HARD_EVIDENCE[field] for x in _nested(item, path)])
        return bool(values) and isinstance(expected, (int, float)) and (max(values) >= expected if field == "area_min_m2" else min(values) <= expected)
    if field == "rooms": return str(expected) in re.findall(r"\d+", " ".join(map(str, _nested(item, "rooms"))))
    if field == "location": return any(str(expected).casefold() in str(value).casefold() or str(value).casefold() in str(expected).casefold() for path in HARD_EVIDENCE[field] for value in _nested(item, path))
    if field == "name": return any(str(expected).casefold() == str(value).casefold() for path in HARD_EVIDENCE[field] for value in _nested(item, path))
    return any(value == expected for path in HARD_EVIDENCE.get(field, (field,)) for value in _nested(item, path))


def _near_reason(card: Mapping[str, Any], request: V0SearchRequest) -> str:
    for field, expected in request.effective_hard.items():
        if not _evidence(card, field): return "неполное подтверждение условий"
        if not _matches(card, field, expected): return "условие не совпадает"
    return "близкий вариант"
