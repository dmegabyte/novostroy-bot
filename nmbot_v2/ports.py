from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from typing import Any

from .contracts import ExecutableTurn, ExecutionResult, OptionCard, ResponseBrief, SafeTurnContext, SearchResult, SemanticPlan, TurnPlan, TurnResult
from .state import ConversationState

if TYPE_CHECKING:
    from .search_adapter import V2SearchAdapterResult
    from .search_contract import V2SearchRequest


class SemanticPlannerPort(Protocol):
    def plan(self, context: SafeTurnContext, state: ConversationState) -> TurnPlan: ...


class SearchServicePort(Protocol):
    def search(self, plan: TurnPlan, state: ConversationState, context: SafeTurnContext | None = None) -> SearchResult: ...
    def enrich_selected(self, option: OptionCard, state: ConversationState, plan: TurnPlan) -> OptionCard: ...
    def enrich_pair(self, turn: ExecutableTurn, state: ConversationState) -> Any: ...


class V2SearchAdapterPort(Protocol):
    """Typed, provider-neutral V2 search boundary for a future worker.

    This is deliberately narrower than ``SearchServicePort``: it accepts an
    already-built V2 search request and has no dialogue state, runtime adapter,
    or transport selection responsibility.
    """

    async def search(self, request: V2SearchRequest) -> V2SearchAdapterResult: ...


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


@dataclass(frozen=True)
class V2RuntimePorts:
    """All executable V2 dependencies supplied by a composition root.

    V2 owns this boundary; transport/global adapters may implement these ports,
    but must not be imported by a V2 worker to construct a turn processor.
    """

    planner: SemanticPlannerPort
    search_service: SearchServicePort | None = None
    conversation: ConversationPort | None = None
    operator: OperatorPort | None = None
    journal: JournalPort | None = None
    trace: TracePort | None = None
    response_composer: ResponseComposerPort | None = None
    manager_rewriter: ManagerRewriterPort | None = None
