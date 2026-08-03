"""Isolated local NMBot V0 runtime.

V0 is opt-in and standalone: it exposes a tiny processor with injected model
ports and intentionally does not import or mutate the V2 runtime adapter.
"""

RUNTIME_VERSION = "V0"

from .contracts import LotExample, OptionCard, SearchResult, V0Answer, V0State, V0TurnResult
from .runtime import V0TurnProcessor

__all__ = ["LotExample", "OptionCard", "RUNTIME_VERSION", "SearchResult", "V0Answer", "V0State", "V0TurnProcessor", "V0TurnResult"]
