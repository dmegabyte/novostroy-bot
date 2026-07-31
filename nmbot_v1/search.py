from __future__ import annotations

from typing import Any

from .contracts import SCHEMA_VERSION, V1Error
from .search_contract import V1SearchRequest, V1SearchResult


def build_search_request(state, plan) -> V1SearchRequest:
    hard = dict(state.hard_constraints)
    hard.update(plan.constraints_delta.hard)
    prefs = dict(state.preferences)
    prefs.update(plan.constraints_delta.preferences)
    return V1SearchRequest(SCHEMA_VERSION, hard, prefs, plan.viewpoint or state.active_viewpoint, (state.selected_project or {}).get("ref"), plan.requested_facts)


def parse_search_provider_result(raw: Any, effective_hard: dict[str, Any]) -> V1SearchResult:
    if not isinstance(raw, dict):
        raise V1Error("provider top level must be object")
    return V1SearchResult.from_provider_dict(raw, effective_hard)


def safe_search_error(code: str = "search_provider_error") -> V1SearchResult:
    return V1SearchResult(SCHEMA_VERSION, error_code=code, attempts=({"status": "failed", "code": code},))
