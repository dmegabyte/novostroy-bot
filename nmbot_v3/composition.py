"""Pure, injectable V3 planner-to-evidence composition root.

This owner deliberately has no runtime, selector, state-path, prompt-loader,
or provider-client dependency. Callers supply both V3 ports explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import IntentGoalV3, IntentPlanV3, V3ContractError, V3PlannerContext
from .evidence_contract import EvidenceModeV3, EvidenceRequest, EvidenceResult
from .orchestration import V3OrchestrationResult, orchestrate_v3_turn
from .ports import (
    V3EvidenceSearchPort,
    V3PlannerPort,
    V3PlannerRequest,
    V3ProviderError,
    V3RedactedText,
)
from .presentation import V3PresentationCard, V3WriterBriefInput
from .renderer import render_v3_writer_publication
from .state import V3ConversationState
from .writer_adapter import V3WriterAdapterResult, V3WriterPublicationPort


_SAFE_TEXT = "Не могу надёжно подтвердить информацию, поэтому не буду гадать."
_SAFE_QUESTION = "Уточните, пожалуйста, что для вас важнее всего?"


@dataclass(frozen=True)
class V3CompositionInput:
    """Closed input for one V3 turn; it contains no state path or raw text."""

    user_text: V3RedactedText
    planner_context: V3PlannerContext
    answer_goal: str = "present_search_results"
    mandatory_cta: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.user_text, V3RedactedText):
            raise V3ContractError("invalid_composition_user_text")
        if not isinstance(self.planner_context, V3PlannerContext):
            raise V3ContractError("invalid_composition_planner_context")
        if not isinstance(self.answer_goal, str) or not self.answer_goal.strip() or len(self.answer_goal) > 80:
            raise V3ContractError("invalid_composition_answer_goal")
        if self.mandatory_cta is not None and (not isinstance(self.mandatory_cta, str) or not self.mandatory_cta.strip()):
            raise V3ContractError("invalid_composition_mandatory_cta")


class V3CompositionRoot:
    """Compose injected V3 ports into one fail-closed, non-persisting turn.

    ``writer`` is optional.  Without it the accepted deterministic response is
    published unchanged.  With it, writer prose is a post-decision replacement
    only: it cannot affect the state delta or acceptance of the turn.
    """

    def __init__(
        self,
        planner: V3PlannerPort,
        evidence: V3EvidenceSearchPort,
        writer: V3WriterPublicationPort | None = None,
    ) -> None:
        if not callable(getattr(planner, "plan", None)):
            raise V3ContractError("invalid_v3_planner_port")
        if not callable(getattr(evidence, "search", None)):
            raise V3ContractError("invalid_v3_evidence_port")
        if writer is not None and not callable(getattr(writer, "write", None)):
            raise V3ContractError("invalid_v3_writer_port")
        self._planner = planner
        self._evidence = evidence
        self._writer = writer

    async def run(self, state: V3ConversationState, turn: V3CompositionInput) -> V3OrchestrationResult:
        if not isinstance(state, V3ConversationState):
            return _rejected(state, "invalid_v3_state")
        if not isinstance(turn, V3CompositionInput):
            return _rejected(state, "invalid_composition_input")

        try:
            planned = await self._planner.plan(V3PlannerRequest(turn.user_text, turn.planner_context))
        except Exception:
            return _rejected(state, "planner_unavailable")
        if isinstance(planned, V3ProviderError):
            return _rejected(state, f"planner_{planned.code.value}")
        if not isinstance(planned, IntentPlanV3):
            return _rejected(state, "invalid_planner_port_result")

        try:
            request = _evidence_request(planned, turn.planner_context)
        except V3ContractError:
            return _rejected(state, "invalid_evidence_request")
        try:
            found = await self._evidence.search(request)
        except Exception:
            return _rejected(state, "evidence_unavailable")
        if isinstance(found, V3ProviderError):
            return _rejected(state, f"evidence_{found.code.value}")
        if not isinstance(found, EvidenceResult):
            return _rejected(state, "invalid_evidence_port_result")

        presentation = _presentation(turn, found)
        deterministic = orchestrate_v3_turn(
            state,
            planned,
            found,
            presentation,
            planner_context=turn.planner_context,
        )
        if not deterministic.ok or self._writer is None:
            return deterministic

        try:
            published = await self._writer.write(presentation)
        except Exception:
            return deterministic
        if not isinstance(published, V3WriterAdapterResult) or not published.ok:
            return deterministic
        rendered = render_v3_writer_publication(published.output, presentation)
        if not rendered.ok:
            return deterministic
        return V3OrchestrationResult(
            True,
            deterministic.state_delta,
            deterministic.state,
            rendered.response,
        )


def _evidence_request(plan: IntentPlanV3, context: V3PlannerContext) -> EvidenceRequest:
    if plan.goal is IntentGoalV3.LOOKUP_OBJECT:
        return EvidenceRequest(EvidenceModeV3.NAMED_OBJECT, plan.requested_facts, plan.constraints_delta, plan.named_object_reference, count=1)
    if plan.goal is IntentGoalV3.ANSWER_SELECTED:
        return EvidenceRequest(
            EvidenceModeV3.CURRENT_OPTIONS_FACT_CHECK,
            plan.requested_facts,
            plan.constraints_delta,
            current_option_refs=(plan.selected_option_ref,),
            count=1,
        )
    if plan.goal in {
        IntentGoalV3.ANSWER_CURRENT,
        IntentGoalV3.COMPARE_CURRENT,
        IntentGoalV3.RECOMMEND_CURRENT,
        IntentGoalV3.ANSWER_OPEN_QUESTION,
    }:
        return EvidenceRequest(
            EvidenceModeV3.CURRENT_OPTIONS_FACT_CHECK,
            plan.requested_facts,
            plan.constraints_delta,
            current_option_refs=context.visible_option_refs,
            count=min(3, len(context.visible_option_refs)),
        )
    return EvidenceRequest(EvidenceModeV3.BROAD, plan.requested_facts, plan.constraints_delta)


def _presentation(turn: V3CompositionInput, evidence: EvidenceResult) -> V3WriterBriefInput:
    cards = tuple(V3PresentationCard(card.name, card.fields) for card in (*evidence.facts, *evidence.near))
    return V3WriterBriefInput(
        client_request=turn.user_text.text,
        answer_goal=turn.answer_goal,
        cards=cards,
        missing_facts=evidence.missing_facts,
        mandatory_cta=turn.mandatory_cta,
    )


def _rejected(state: object, code: str) -> V3OrchestrationResult:
    safe_state = state if isinstance(state, V3ConversationState) else V3ConversationState.clean()
    from .renderer import V3ResponsePlan
    from .state import V3StateDelta

    return V3OrchestrationResult(
        False,
        V3StateDelta(),
        safe_state,
        V3ResponsePlan(_SAFE_TEXT, (), "", _SAFE_QUESTION),
        (code,),
    )
