from __future__ import annotations

from dataclasses import dataclass, replace

from .contracts import V1Action, V1AnswerKind, V1Goal, V1IntentPlan, V1OperatorIntent, V1Stage
from .contracts import deep_thaw
from .state import V1ConversationState, normalize_contact_name, redact_contact_phone


@dataclass(frozen=True)
class V1Transition:
    accepted: bool
    action: V1Action
    stage: V1Stage
    answer_kind: V1AnswerKind
    state: V1ConversationState
    needs_search: bool = False
    reason: str | None = None


def transition(state: V1ConversationState, plan: V1IntentPlan) -> V1Transition:
    if plan.goal == V1Goal.RESET:
        return V1Transition(True, V1Action.RESET, V1Stage.RESET, V1AnswerKind.CLARIFY, V1ConversationState.clean(state.revision + 1), False)
    if plan.operator_intent == V1OperatorIntent.ACCEPT and plan.confidence < 0.7:
        return V1Transition(False, V1Action.SAFE_ERROR, V1Stage.SAFE_ERROR, V1AnswerKind.SAFE_ERROR, state, False, "low_confidence_operator_accept")
    if plan.operator_intent == V1OperatorIntent.ACCEPT:
        if not state.operator_offered:
            return V1Transition(False, V1Action.SAFE_ERROR, V1Stage.SAFE_ERROR, V1AnswerKind.SAFE_ERROR, state, False, "operator_accept_without_offer")
        ns = replace(state, revision=state.revision + 1, stage=V1Stage.CONTACT_NAME, contact_consent=True, pending_action=V1Action.ACCEPT_OPERATOR)
        return V1Transition(True, V1Action.ACCEPT_OPERATOR, V1Stage.CONTACT_NAME, V1AnswerKind.OPERATOR, ns)
    if plan.operator_intent == V1OperatorIntent.DECLINE:
        ns = replace(state, revision=state.revision + 1, stage=V1Stage.OPERATOR_DECLINED, operator_declined=True, pending_action=V1Action.DECLINE_OPERATOR)
        return V1Transition(True, V1Action.DECLINE_OPERATOR, V1Stage.OPERATOR_DECLINED, V1AnswerKind.OPERATOR, ns)
    if plan.operator_intent == V1OperatorIntent.REQUEST or plan.goal == V1Goal.OFFER_OPERATOR:
        ns = replace(state, revision=state.revision + 1, stage=V1Stage.OPERATOR_OFFER, operator_offered=True, pending_action=V1Action.OFFER_OPERATOR)
        return V1Transition(True, V1Action.OFFER_OPERATOR, V1Stage.OPERATOR_OFFER, V1AnswerKind.OPERATOR, ns)
    if plan.goal == V1Goal.CAPTURE_NAME:
        name = normalize_contact_name(plan.contact_name)
        if not (state.contact_consent and state.stage == V1Stage.CONTACT_NAME and name):
            return V1Transition(False, V1Action.SAFE_ERROR, V1Stage.SAFE_ERROR, V1AnswerKind.SAFE_ERROR, state, False, "invalid_contact_name")
        ns = replace(state, revision=state.revision + 1, stage=V1Stage.CONTACT_PHONE, contact_name=name, pending_action=V1Action.CAPTURE_NAME)
        return V1Transition(True, V1Action.CAPTURE_NAME, V1Stage.CONTACT_PHONE, V1AnswerKind.OPERATOR, ns)
    if plan.goal == V1Goal.CAPTURE_PHONE:
        phone = redact_contact_phone(plan.contact_phone)
        if not (state.contact_consent and state.stage == V1Stage.CONTACT_PHONE and phone):
            return V1Transition(False, V1Action.SAFE_ERROR, V1Stage.SAFE_ERROR, V1AnswerKind.SAFE_ERROR, state, False, "invalid_contact_phone")
        ns = replace(state, revision=state.revision + 1, stage=V1Stage.CONTACT_PHONE, contact_phone_redacted=phone, pending_action=V1Action.CAPTURE_PHONE)
        return V1Transition(True, V1Action.CAPTURE_PHONE, V1Stage.CONTACT_PHONE, V1AnswerKind.OPERATOR, ns)
    if plan.goal == V1Goal.SEARCH:
        stg = V1Stage.FIRST_SEARCH if not state.visible_options else V1Stage.REFINE_SEARCH
        return V1Transition(True, V1Action.SEARCH, stg, V1AnswerKind.SEARCH_RESULTS, state, True)
    if plan.goal == V1Goal.REFINE_SEARCH:
        return V1Transition(True, V1Action.SEARCH, V1Stage.REFINE_SEARCH, V1AnswerKind.SEARCH_RESULTS, state, True)
    if plan.goal == V1Goal.EXPAND_SEARCH:
        return V1Transition(True, V1Action.SEARCH, V1Stage.EXPAND_SEARCH, V1AnswerKind.SEARCH_RESULTS, state, True)
    if plan.goal == V1Goal.ANSWER_CURRENT:
        return V1Transition(True, V1Action.ANSWER_CURRENT, V1Stage.CURRENT_OPTIONS, V1AnswerKind.CURRENT_OPTIONS, replace(state, revision=state.revision + 1, stage=V1Stage.CURRENT_OPTIONS, pending_action=V1Action.ANSWER_CURRENT))
    if plan.goal == V1Goal.SELECT_PROJECT:
        card = _find_ref(state.visible_options, plan.selected_option_ref)
        if not card:
            return V1Transition(False, V1Action.SAFE_ERROR, V1Stage.SAFE_ERROR, V1AnswerKind.SAFE_ERROR, state, False, "invalid_visible_selection")
        ns = replace(state, revision=state.revision + 1, stage=V1Stage.SELECTED_PROJECT, selected_project=card, pending_action=V1Action.SELECT_PROJECT)
        return V1Transition(True, V1Action.SELECT_PROJECT, V1Stage.SELECTED_PROJECT, V1AnswerKind.PROJECT_SELECTED, ns)
    if plan.goal == V1Goal.SEARCH_LOTS:
        if not state.selected_project:
            return V1Transition(False, V1Action.SAFE_ERROR, V1Stage.SAFE_ERROR, V1AnswerKind.SAFE_ERROR, state, False, "no_selected_project")
        return V1Transition(True, V1Action.SEARCH_LOTS, V1Stage.SELECTED_LOT_SEARCH, V1AnswerKind.LOTS, state, True)
    if plan.goal == V1Goal.SELECT_LOT:
        if not state.selected_project:
            return V1Transition(False, V1Action.SAFE_ERROR, V1Stage.SAFE_ERROR, V1AnswerKind.SAFE_ERROR, state, False, "no_selected_project")
        card = _find_ref(state.visible_options, plan.selected_lot_ref)
        if not card:
            return V1Transition(False, V1Action.SAFE_ERROR, V1Stage.SAFE_ERROR, V1AnswerKind.SAFE_ERROR, state, False, "invalid_visible_lot_selection")
        ns = replace(state, revision=state.revision + 1, stage=V1Stage.SELECTED_LOT, selected_lot=card, pending_action=V1Action.SELECT_LOT)
        return V1Transition(True, V1Action.SELECT_LOT, V1Stage.SELECTED_LOT, V1AnswerKind.LOTS, ns)
    if plan.goal == V1Goal.FACT_CHECK:
        ns = replace(state, revision=state.revision + 1, stage=V1Stage.FACT_CHECK, pending_action=V1Action.FACT_CHECK)
        return V1Transition(True, V1Action.FACT_CHECK, V1Stage.FACT_CHECK, V1AnswerKind.FACTS, ns)
    if plan.goal == V1Goal.OFF_TOPIC:
        return V1Transition(True, V1Action.OFF_TOPIC, V1Stage.OFF_TOPIC, V1AnswerKind.OFF_TOPIC, replace(state, revision=state.revision + 1, stage=V1Stage.OFF_TOPIC))
    return V1Transition(False, V1Action.SAFE_ERROR, V1Stage.SAFE_ERROR, V1AnswerKind.SAFE_ERROR, state, False, "safe_error")


def _find_ref(cards, ref):
    for card in cards:
        if card.get("ref") == ref:
            return deep_thaw(card)
    return None
