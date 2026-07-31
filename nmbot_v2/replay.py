from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .card_normalizer import normalize_search_result
from .contracts import SafeTurnContext, SearchResult, SemanticPlan, TurnResult
from .conversation import build_native_conversation_answer
from .runtime import TurnProcessor
from .state import ConversationState


@dataclass
class ReplayReport:
    dialogue_id: str
    turns: list[TurnResult]


class FixturePlanner:
    def __init__(self, plans: list[dict[str, Any]]):
        self._plans = plans
        self._idx = 0

    def plan(self, context: SafeTurnContext, state: ConversationState) -> SemanticPlan:
        data = self._plans[self._idx]
        self._idx += 1
        return SemanticPlan(**data)


class FixtureSearch:
    def __init__(self, outcomes: list[dict[str, Any] | None]):
        self._outcomes = outcomes
        self._idx = 0
        self.last_attempts: tuple[dict[str, Any], ...] = ()

    def search(self, plan: SemanticPlan, state: ConversationState) -> SearchResult:
        data = self._outcomes[self._idx]
        self._idx += 1
        attempts = list((data or {}).get("attempts", []))
        self.last_attempts = tuple(attempts)
        if attempts:
            last = attempts[-1]
            if not last.get("ok"):
                raise RuntimeError(str(last.get("error", "provider_error")))
            return normalize_search_result(last.get("search") or {})
        if data and data.get("raise"):
            raise RuntimeError(data["raise"])
        return normalize_search_result(data or {})

    def enrich_selected(self, option, state: ConversationState, plan: SemanticPlan):
        return option


class FixtureConversation:
    def answer(self, plan: SemanticPlan, state: ConversationState):
        from .contracts import ExecutionResult

        return ExecutionResult(ok=True, message=build_native_conversation_answer(plan, state))


def load_dialogues(path: str | Path) -> Iterable[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def run_dialogue(record: dict[str, Any]) -> ReplayReport:
    turns = record["turns"]
    planner = FixturePlanner([t["plan"] for t in turns])
    search = FixtureSearch([t.get("search") for t in turns if t["plan"]["operation"] in {"search", "new_search", "refine_search", "expand_more"}])
    processor = TurnProcessor(planner=planner, search_service=search, conversation=FixtureConversation())
    state = ConversationState.from_dict(record.get("initial_state"))
    results: list[TurnResult] = []
    for idx, turn in enumerate(turns):
        _assert_transport_events(turn.get("transport_events", []))
        result = processor.process(SafeTurnContext(conversation_ref=record["id"], user_text=turn.get("user", f"turn {idx}")), state)
        _assert_structure(result)
        _assert_expectations(result, turn.get("expect", {}))
        state = ConversationState.from_dict(result.state)
        results.append(result)
    return ReplayReport(dialogue_id=record["id"], turns=results)


def run_corpus(path: str | Path) -> list[ReplayReport]:
    return [run_dialogue(record) for record in load_dialogues(path)]


def _assert_structure(result: TurnResult) -> None:
    assert len(result.response_plan.cards) <= 3
    assert result.response_text.count("?") == 1
    forbidden = ("router", "presenter", "fallback", "MCP", "JSON", "traceback")
    assert not any(word in result.response_text for word in forbidden)


def _assert_expectations(result: TurnResult, expect: dict[str, Any]) -> None:
    if "stage" in expect:
        assert result.stage.value == expect["stage"]
    if "action" in expect:
        assert result.action.value == expect["action"]
    if "state_unchanged" in expect and expect["state_unchanged"]:
        assert result.state_delta.is_empty
    if "contains" in expect:
        for text in expect["contains"]:
            assert text in result.response_text
    if "not_contains" in expect:
        for text in expect["not_contains"]:
            assert text not in result.response_text
    if "selected" in expect:
        assert result.state.get("selected_option_name") == expect["selected"]
    if "active_topic" in expect:
        assert result.state.get("active_topic") == expect["active_topic"]
    if "params" in expect:
        for key, value in expect["params"].items():
            assert result.state.get("params", {}).get(key) == value
    if "retry_count" in expect:
        assert result.execution.retry_count == expect["retry_count"]
    if "attempt_statuses" in expect:
        statuses = [x.get("status") for x in result.execution.attempts]
        assert statuses == expect["attempt_statuses"]
    if "near_selected" in expect:
        assert result.execution.selected is not None and result.execution.selected.is_near is expect["near_selected"]


def _assert_transport_events(events: list[dict[str, Any]]) -> None:
    if not events:
        return
    kinds = [x.get("event") for x in events]
    assert kinds[0] == "accepted_async"
    assert "status" in kinds
    assert kinds.count("final") == 1
    for event in events:
        if event.get("event") == "status":
            assert event.get("mutates_business_state") is False
