from __future__ import annotations

import asyncio
from typing import Any

from . import RUNTIME_VERSION
from .composition import V3CompositionInput, V3CompositionRoot
from .contracts import V3ContractError, V3TurnResult
from .ports import V3RedactedText
from .state import V3ConversationState


_SAFE_TEXT = "Не могу надёжно подтвердить информацию, поэтому не буду гадать."


async def run_turn(
    user_text: str,
    state_dict: dict[str, Any] | None,
    planner_port: Any,
    evidence_port: Any,
    writer_port: Any = None,
) -> V3TurnResult:
    """Run the injected V3 composition root; rejected work is a strict state no-op."""
    state = V3ConversationState.clean()
    try:
        state = V3ConversationState.from_dict(state_dict) if state_dict is not None else state
        root = V3CompositionRoot(planner_port, evidence_port, writer_port)
        result = await root.run(
            state,
            V3CompositionInput(V3RedactedText(user_text), state.planner_context),
        )
        if not result.ok:
            return _rejected(state, "v3_composition_rejected")
        next_state = result.state
        return V3TurnResult(
            RUNTIME_VERSION,
            next_state.stage.value,
            next_state.last_action.value if next_state.last_action is not None else "clarify",
            result.public_response,
            next_state.to_dict(),
        )
    except (AttributeError, TypeError, V3ContractError, ValueError):
        return _rejected(state, "v3_composition_invalid")
    except Exception:
        return _rejected(state, "v3_runtime_error")


def _rejected(state: V3ConversationState, code: str) -> V3TurnResult:
    return V3TurnResult(RUNTIME_VERSION, "reset", "clarify", _SAFE_TEXT, state.to_dict(), code)


def run_turn_sync(*args: Any, **kwargs: Any) -> V3TurnResult:
    return asyncio.run(run_turn(*args, **kwargs))
