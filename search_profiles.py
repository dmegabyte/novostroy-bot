from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SEARCH_PROFILES = {"generic", "family", "investment", "mortgage", "none"}
PROFILE_OVERLAYS = {"family", "investment", "mortgage"}


@dataclass(frozen=True)
class SearchProfileSelection:
    profile: str
    overlays: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return {"profile": self.profile, "overlays": list(self.overlays)}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "да", "mortgage", "family", "investment"}
    return False


def _has_mortgage_facet(plan: dict[str, Any]) -> bool:
    facets = plan.get("facets") if isinstance(plan.get("facets"), dict) else {}
    if any(str(key).strip().lower() in {"mortgage", "family_mortgage", "finance"} and _truthy(value) for key, value in facets.items()):
        return True
    constraints = plan.get("constraints_patch") if isinstance(plan.get("constraints_patch"), dict) else {}
    for category in ("hard", "preferences", "unknown"):
        fields = constraints.get(category) if isinstance(constraints.get(category), dict) else {}
        for key in ("mortgage", "mortgage_type", "family_mortgage"):
            if _truthy(fields.get(key)):
                return True
    return False


def _clean_profile(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    return raw if raw in SEARCH_PROFILES else None


def select_search_profile(plan: dict[str, Any] | None) -> SearchProfileSelection | None:
    """Choose MCP search profile from typed planner fields only, never raw text."""
    if not isinstance(plan, dict):
        return None
    if plan.get("canonical_valid") is False:
        return None
    if not (
        str(plan.get("action") or "") == "search"
        and str(plan.get("target") or "") == "new_search"
        and str(plan.get("search_policy") or "") == "required"
    ):
        return None

    intent = str(plan.get("intent") or "").strip().lower()
    requested = _clean_profile(plan.get("search_profile"))
    has_mortgage = _has_mortgage_facet(plan)

    if intent == "family":
        profile = "family"
    elif intent == "investment":
        profile = "investment"
    elif intent == "mortgage":
        profile = "mortgage"
    elif intent in {"life", "compare", "unknown", ""}:
        if requested in {"family", "investment"}:
            profile = requested
        elif requested == "mortgage" or has_mortgage:
            profile = "mortgage"
        else:
            profile = "generic"
    else:
        profile = "generic"

    overlays: list[str] = []
    if profile in PROFILE_OVERLAYS:
        overlays.append(profile)
    if has_mortgage and profile != "mortgage":
        overlays.append("mortgage")
    return SearchProfileSelection(profile=profile, overlays=tuple(dict.fromkeys(overlays)))


def safe_search_profile_payload(value: Any) -> SearchProfileSelection | None:
    """Validate caller-provided profile payload before prompt composition."""
    if value is None:
        return None
    if isinstance(value, SearchProfileSelection):
        return value
    if isinstance(value, str):
        profile = _clean_profile(value)
        if profile in (None, "none"):
            return None
        overlays = (profile,) if profile in PROFILE_OVERLAYS else ()
        return SearchProfileSelection(profile=profile, overlays=overlays)
    if isinstance(value, dict):
        profile = _clean_profile(value.get("profile")) or "generic"
        if profile == "none":
            return None
        raw_overlays = value.get("overlays") if isinstance(value.get("overlays"), list) else []
        overlays = tuple(dict.fromkeys(item for item in (_clean_profile(v) for v in raw_overlays) if item in PROFILE_OVERLAYS))
        if not overlays and profile in PROFILE_OVERLAYS:
            overlays = (profile,)
        return SearchProfileSelection(profile=profile, overlays=overlays)
    return None
