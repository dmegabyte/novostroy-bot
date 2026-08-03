"""V2-owned composition boundary for injectable runtime ports."""
from __future__ import annotations

from .ports import V2RuntimePorts
from .runtime import TurnProcessor


def build_turn_processor(
    ports: V2RuntimePorts,
    *,
    response_composer_mode: str = "off",
    manager_rewriter_mode: str = "off",
) -> TurnProcessor:
    """Create a V2 processor without choosing or importing any provider adapter."""
    return TurnProcessor(
        planner=ports.planner,
        search_service=ports.search_service,
        conversation=ports.conversation,
        operator=ports.operator,
        journal=ports.journal,
        trace=ports.trace,
        response_composer=ports.response_composer,
        response_composer_mode=response_composer_mode,
        manager_rewriter=ports.manager_rewriter,
        manager_rewriter_mode=manager_rewriter_mode,
    )
