"""Side-by-side NMBot V2 clean runtime.

This package intentionally has no imports from the legacy router/presenter runtime.
Adapters can be added around the ports, but the V2 turn semantics stay here.
"""

from .contracts import (
    ExecutionResult,
    ExecutableTurn,
    OptionCard,
    ResponsePlan,
    SafeTurnContext,
    SearchResult,
    SemanticPlan,
    Stage,
    StateDelta,
    TurnAction,
    TurnResult,
    TurnPlan,
)
from .runtime import TurnProcessor
from .state import ConversationState, apply_state_delta

__all__ = [
    "ConversationState",
    "ExecutionResult",
    "ExecutableTurn",
    "OptionCard",
    "ResponsePlan",
    "SafeTurnContext",
    "SearchResult",
    "SemanticPlan",
    "Stage",
    "StateDelta",
    "TurnAction",
    "TurnProcessor",
    "TurnResult",
    "TurnPlan",
    "apply_state_delta",
]
