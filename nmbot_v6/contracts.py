"""Minimal shared contracts for the V6 foundation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ContractError(ValueError):
    """A fail-closed V6 contract violation."""


class Scenario(str, Enum):
    SEARCH_NEW = "search_new"
    SEARCH_REFINE = "search_refine"
    SELECTED_OBJECT_DETAILS = "selected_object_details"
    LIVE_FACT = "live_fact"
    ANSWER_FROM_STATE = "answer_from_state"
    CLARIFY = "clarify"
    REQUEST_PHONE = "request_phone"


class AnswerMode(str, Enum):
    GROUNDED_SEARCH = "grounded_search"
    STATE_ONLY = "state_only"
    CLARIFY = "clarify"
    REQUEST_PHONE = "request_phone"


@dataclass(frozen=True)
class Prompt1Plan:
    scenario: Scenario
    mcp_required: bool
    mcp_request: Mapping[str, Any] | None
    answer_mode: AnswerMode
