"""Pure state transitions for selected-entity evidence verification.

This module deliberately does not know which fact is being verified or how an
adapter executes it.  It only protects the confirmation and idempotency gate.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .contracts import PendingAction, SelectedEntity
from .state import ConversationState


@dataclass(frozen=True)
class PendingActionResult:
    state: ConversationState
    changed: bool
    execute: bool = False
    reason: str | None = None


def offer_pending_action(state: ConversationState, action: PendingAction) -> PendingActionResult:
    """Install the newest action; a new offer intentionally supersedes the old."""
    if not state.selected_entity or not _same_entity(state.selected_entity, action.entity_type, action.entity_id):
        return PendingActionResult(state, False, reason="selected_entity_required")
    return PendingActionResult(replace(state, pending_action=action), True)


def select_entity(state: ConversationState, entity: SelectedEntity) -> PendingActionResult:
    """Selecting another object clears an action scoped to the old object."""
    pending = state.pending_action
    stale = pending is not None and not _same_entity(entity, pending.entity_type, pending.entity_id)
    return PendingActionResult(
        replace(state, selected_entity=entity, pending_action=None if stale else pending),
        state.selected_entity != entity or stale,
        reason="stale_pending_cancelled" if stale else None,
    )


def confirm_pending_action(state: ConversationState, idempotency_key: str) -> PendingActionResult:
    action = state.pending_action
    if not action or action.status != "pending" or action.idempotency_key != str(idempotency_key):
        return PendingActionResult(state, False, reason="no_current_pending_action")
    if not state.selected_entity or not _same_entity(state.selected_entity, action.entity_type, action.entity_id):
        return PendingActionResult(replace(state, pending_action=replace(action, status="cancelled")), True, reason="stale_pending_action")
    return PendingActionResult(replace(state, pending_action=replace(action, status="confirmed")), True, execute=True)


def complete_pending_action(state: ConversationState, idempotency_key: str) -> PendingActionResult:
    action = state.pending_action
    if not action or action.status != "confirmed" or action.idempotency_key != str(idempotency_key):
        return PendingActionResult(state, False, reason="action_not_confirmed")
    return PendingActionResult(replace(state, pending_action=replace(action, status="completed")), True)


def cancel_pending_action(state: ConversationState, idempotency_key: str) -> PendingActionResult:
    action = state.pending_action
    if not action or action.status not in {"pending", "confirmed"} or action.idempotency_key != str(idempotency_key):
        return PendingActionResult(state, False, reason="action_not_cancellable")
    return PendingActionResult(replace(state, pending_action=replace(action, status="cancelled")), True)


def pending_action_belongs_to_current_offer(state: ConversationState) -> bool:
    """Return whether an action is still bound to the active offer and entity.

    ``PendingAction`` intentionally has no scenario/offer identifier.  The
    lifecycle reducer therefore makes a pending follow-up the ownership
    boundary: an action may only be used while that offer is active and while
    its entity is still selected.  Fact keys are not evidence of ownership.
    """
    action = state.pending_action
    return bool(
        action
        and state.pending_followup
        and state.selected_entity
        and _same_entity(state.selected_entity, action.entity_type, action.entity_id)
    )


def reconcile_pending_action_lifecycle(
    before: ConversationState,
    after: ConversationState,
    *,
    pending_followup_replaced: bool,
    fresh_pending_action: bool,
) -> ConversationState:
    """Enforce ownership when an offer ends or is replaced.

    A new offer must carry a newly bound action.  Otherwise an active action
    from a prior offer is either cancelled (when the offer ends) or cleared
    (when another offer replaces it).  A completed action is retained only on
    the terminal successful path, where it suppresses duplicate consent; it is
    never executable because confirmation only accepts ``pending`` actions.
    """
    if fresh_pending_action:
        # Completion is the terminal success transition: retain it for
        # duplicate suppression even though it has just closed the offer.
        if after.pending_action and after.pending_action.status in {"pending", "confirmed"} and not pending_action_belongs_to_current_offer(after):
            return replace(after, pending_action=replace(after.pending_action, status="cancelled"))
        return after
    if not pending_followup_replaced:
        return after

    action = after.pending_action
    if action is None:
        return after
    if after.pending_followup:
        # A replacement offer without a newly-bound action must never inherit
        # an action, including a completed action from an earlier offer.
        return replace(after, pending_action=None)
    if action.status in {"pending", "confirmed"}:
        return replace(after, pending_action=replace(action, status="cancelled"))
    return after


def _same_entity(entity: SelectedEntity, entity_type: str, entity_id: str | int) -> bool:
    return entity.entity_type == entity_type and str(entity.entity_id) == str(entity_id)
