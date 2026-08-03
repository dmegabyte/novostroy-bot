"""Pure V3 turn orchestration, intentionally detached from every runtime owner."""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import IntentGoalV3, IntentPlanV3, V3Action, V3PlannerContext, V3Stage
from .evidence_contract import EvidenceResult
from .presentation import V3WriterBriefInput
from .renderer import V3ResponsePlan, render_v3_response
from .semantic_planner import validate_intent_plan_v3
from .state import V3ConversationState, V3StateDelta, apply_v3_state_delta
from .transition import derive_transition_v3


_SAFE_TEXT = "Не могу надёжно подтвердить информацию, поэтому не буду гадать."
_SAFE_QUESTION = "Уточните, пожалуйста, что для вас важнее всего?"


@dataclass(frozen=True)
class V3OrchestrationResult:
    """Closed output of a local V3 turn; errors are codes, never source data."""

    ok: bool
    state_delta: V3StateDelta
    state: V3ConversationState
    response: V3ResponsePlan
    errors: tuple[str, ...] = ()

    @property
    def public_response(self) -> str:
        return self.response.public_text


def orchestrate_v3_turn(
    state: V3ConversationState,
    plan: IntentPlanV3,
    evidence: EvidenceResult,
    writer_input: V3WriterBriefInput,
    *,
    planner_context: V3PlannerContext | None = None,
) -> V3OrchestrationResult:
    """Reduce already-local V3 inputs without I/O, provider calls, or V2 fallback."""

    if not isinstance(state, V3ConversationState):
        return _rejected(_safe_code("invalid_v3_state"), state)
    if not isinstance(plan, IntentPlanV3):
        return _rejected(("invalid_intent_plan",), state)
    if not isinstance(evidence, EvidenceResult):
        return _rejected(("invalid_evidence_result",), state)
    if not isinstance(writer_input, V3WriterBriefInput):
        return _rejected(("invalid_writer_input",), state)

    if planner_context is not None and not isinstance(planner_context, V3PlannerContext):
        return _rejected(("invalid_planner_context",), state)
    context = planner_context or state.planner_context
    validation = validate_intent_plan_v3(plan, context)
    if not validation.ok or validation.plan is None:
        return _rejected(validation.errors or ("invalid_intent_plan",), state)

    decision = derive_transition_v3(validation.plan, context)
    if not decision.accepted:
        return _rejected((decision.error_code or "transition_rejected",), state)

    rendered = render_v3_response(
        validation.plan, evidence, writer_input,
        pending_followup_key=context.pending_followup_key,
    )
    if not rendered.ok:
        return _rejected(rendered.errors or ("render_failed",), state)

    delta = V3StateDelta(1, V3Stage.ANSWERED, V3Action.RESPOND, _next_planner_context(validation.plan, evidence, context))
    return V3OrchestrationResult(
        True,
        delta,
        apply_v3_state_delta(state, delta),
        rendered.response,
    )


def _next_planner_context(plan: IntentPlanV3, evidence: EvidenceResult, context: V3PlannerContext) -> V3PlannerContext:
    """Persist only evidence-proven UUID option refs and closed pending markers."""
    refs = context.visible_option_refs
    if plan.goal in {
        IntentGoalV3.NEW_SEARCH,
        IntentGoalV3.REFINE_SEARCH,
        IntentGoalV3.EXPAND_SEARCH,
        IntentGoalV3.LOOKUP_OBJECT,
    }:
        refs = tuple(card.canonical_ref for card in evidence.facts if card.canonical_ref is not None)
    pending_key = context.pending_followup_key
    has_pending_action = context.has_pending_action
    if plan.followup_outcome == "decline":
        pending_key = None
        has_pending_action = False
    elif (
        context.pending_followup_key == "selected_live_fact_consent"
        and plan.followup_outcome == "accept"
    ):
        # This stores only a closed marker; V3 has no contact fields.
        pending_key = "contact_phone"
        has_pending_action = False
    elif plan.followup_outcome == "resume_contact":
        pending_key = None
        has_pending_action = False
    return V3PlannerContext(refs, pending_key, has_pending_action)


def _rejected(errors: tuple[str, ...], state: object) -> V3OrchestrationResult:
    safe_state = state if isinstance(state, V3ConversationState) else V3ConversationState.clean()
    response = V3ResponsePlan(_SAFE_TEXT, (), "", _SAFE_QUESTION)
    return V3OrchestrationResult(False, V3StateDelta(), safe_state, response, tuple(sorted(set(errors))))


def _safe_code(code: str) -> tuple[str, ...]:
    return (code,)
