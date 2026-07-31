from __future__ import annotations

from typing import Protocol

from typing import Any

from .contracts import ExecutableTurn, ExecutionResult, OptionCard, ResponseBrief, SafeTurnContext, SearchResult, SemanticPlan, TurnPlan, TurnResult
from .state import ConversationState


class SemanticPlannerPort(Protocol):
    def plan(self, context: SafeTurnContext, state: ConversationState) -> TurnPlan: ...


class SearchServicePort(Protocol):
    def search(self, plan: TurnPlan, state: ConversationState, context: SafeTurnContext | None = None) -> SearchResult: ...
    def enrich_selected(self, option: OptionCard, state: ConversationState, plan: TurnPlan) -> OptionCard: ...
    def enrich_pair(self, turn: ExecutableTurn, state: ConversationState) -> Any: ...


class ConversationPort(Protocol):
    def answer(self, plan: TurnPlan, state: ConversationState) -> ExecutionResult: ...


class OperatorPort(Protocol):
    def prepare(self, plan: TurnPlan, state: ConversationState) -> ExecutionResult: ...


class JournalPort(Protocol):
    def append(self, result: TurnResult) -> None: ...


class TracePort(Protocol):
    def record(self, event: dict) -> None: ...


class ResponseComposerPort(Protocol):
    def compose_response(self, brief: ResponseBrief, *, fallback_text: str) -> object: ...


class ManagerRewriterPort(Protocol):
    def rewrite_manager_answer(self, *, transcript: tuple[dict[str, str], ...], current_question: str, prepared_answer: str, brief: ResponseBrief) -> object: ...
