from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .constraints import normalize_constraints_delta, topic_from_plan
from .contracts import SemanticPlan
from .state import ConversationState


@dataclass(frozen=True)
class EffectiveRequest:
    """Актуальный запрос клиента, вычисленный из state и текущего плана.

    Это временное представление одного хода. Оно не сохраняется отдельно и не
    может стать вторым источником правды рядом с ``ConversationState``.
    """

    params: dict[str, Any] = field(default_factory=dict)
    intent: str | None = None
    selected_option_name: str | None = None
    requested_facts: tuple[str, ...] = ()
    clarification: str | None = None
    pending_followup: str | None = None


def build_effective_request(state: ConversationState, plan: SemanticPlan) -> EffectiveRequest:
    """Объединяет подтверждённый контекст диалога с изменениями текущего хода."""

    params = dict(state.params)
    params.update(normalize_constraints_delta(plan.constraints_delta))
    intent = topic_from_plan(plan.intent, params) or state.active_topic or plan.intent
    requested_facts = tuple(dict.fromkeys((*plan.requested_facts, *plan.facts_needed)))
    return EffectiveRequest(
        params=params,
        intent=intent,
        selected_option_name=plan.selected_option_name or state.selected_option_name,
        requested_facts=requested_facts,
        clarification=plan.clarification,
        pending_followup=state.pending_followup,
    )
