from __future__ import annotations

from typing import Any, Protocol

from .contracts import V1IntentPlan
from .search_contract import V1SearchRequest


class SemanticPlannerPort(Protocol):
    def plan(self, planner_input: dict[str, Any]) -> V1IntentPlan | dict[str, Any]: ...


class SearchPort(Protocol):
    def search(self, request: V1SearchRequest) -> dict[str, Any]: ...


class PresenterPort(Protocol):
    def present(self, response_plan: dict[str, Any], safe_context: dict[str, Any]) -> str: ...


class JournalPort(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...


class TracePort(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...
