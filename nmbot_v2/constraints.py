from __future__ import annotations

from typing import Any, Mapping

from .vocabulary import CONSTRAINT_ALIASES as ALIASES
from .vocabulary import CONSTRAINT_KEY_SET as ALLOW
from .vocabulary import SENSITIVE_CONSTRAINT_KEYS as SENSITIVE

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
