from __future__ import annotations

from typing import Any, Mapping


SENSITIVE = {"phone", "token", "secret", "password", "jivo_id", "raw_id", "client_id"}
ALIASES = {
    "budget_max": "max_price",
    "price_max": "max_price",
    "max_budget": "max_price",
    "budget": "max_price",
    "room_count": "rooms",
    "rooms_count": "rooms",
    "initial_payment": "down_payment",
    "finance_preference": "financing",
}
ALLOW = {
    "location",
    "rooms",
    "max_price",
    "min_price",
    "purpose",
    "financing",
    "down_payment",
    "ready",
    "finishing",
    "area_min_m2",
    "area_max_m2",
}


def normalize_constraints_delta(delta: Mapping[str, Any] | None) -> dict[str, Any]:
    """Flatten semantic constraints into safe state params.

    Supports both flat fixtures and categorized planner shape:
    {hard:{...}, preferences:{...}, unknown:{...}}. Unknown and sensitive keys are dropped.
    """
    if not delta:
        return {}
    source: dict[str, Any] = {}
    if any(k in delta for k in ("hard", "preferences", "unknown")):
        for section in ("hard", "preferences"):
            value = delta.get(section)
            if isinstance(value, Mapping):
                source.update(value)
    else:
        source.update(delta)
    out: dict[str, Any] = {}
    for raw_key, value in source.items():
        key = ALIASES.get(str(raw_key), str(raw_key))
        if key in SENSITIVE or any(s in key.lower() for s in SENSITIVE):
            continue
        if key not in ALLOW:
            continue
        if value in (None, "", [], {}):
            continue
        out[key] = value
    return out


def topic_from_plan(intent: str | None, params: Mapping[str, Any]) -> str | None:
    if params.get("financing") or params.get("down_payment") or intent in {"mortgage", "financing"}:
        return "financing"
    if intent in {"rental", "investment", "family", "life", "purchase"}:
        return intent
    if params.get("purpose") in {"rental", "investment", "family", "life"}:
        return str(params["purpose"])
    return None
