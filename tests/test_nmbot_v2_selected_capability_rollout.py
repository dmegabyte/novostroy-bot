from __future__ import annotations

import asyncio

from nmbot_v2.capability_registry import compile_capability_request
from nmbot_v2.card_normalizer import normalize_card
from nmbot_v2.contracts import OptionCard, PendingAction, SafeTurnContext, SelectedEntity, SemanticPlan, StateDelta
from nmbot_v2.evidence_resolver import EvidenceStatus, bind_evidence
from nmbot_v2.pending_action import offer_pending_action
from nmbot_v2.runtime import TurnProcessor
from nmbot_v2.selected_capability import build_selected_capability_request, fetch_selected_capability
from nmbot_v2.state import ConversationState, apply_state_delta


class Planner:
    def __init__(self, plan): self.plan_value = plan
    def plan(self, context, state): return self.plan_value


class CapabilityService:
    def __init__(self):
        self.calls = 0
        self.requests = []
    def verify_selected_capability(self, card, request):
        self.calls += 1
        self.requests.append(request)
        result = bind_evidence(request, {"facts": [{"id": 42, "state": 2, "mortgage_calc": [{"novos_id": 42, "state": 2, "min_percent": 6.2, "min_fee": 20, "credit_month": 360}]}]})
        return OptionCard(name=card.name, entity_id=request.entity_id, entity_type=request.entity_type, mortgage_rate=6.2, mortgage_down_payment=20, mortgage_term=360, mortgage_terms="ставка от 6.2%, взнос от 20%, срок до 360 мес."), result, {"status": "selected_capability_evidence_complete", "accepted_rows": 1, "rejected_rows": 0}


def _state() -> ConversationState:
    card = OptionCard(name="Лучи", entity_id=42, entity_type="residential_complex")
    entity = SelectedEntity("residential_complex", 42, "Лучи")
    action = PendingAction("verify_selected_facts", ("mortgage_terms",), "residential_complex", 42, "pending", "verify-mortgage-42")
    return offer_pending_action(ConversationState(visible_options=(card,), selected_option_name=card.name, selected_entity=entity, pending_followup="financing_consent"), action).state


def test_request_is_exactly_identity_bound_and_bounded() -> None:
    state = _state()
    confirmed = __import__("nmbot_v2.pending_action", fromlist=["confirm_pending_action"]).confirm_pending_action(state, "verify-mortgage-42").state
    request = compile_capability_request(confirmed)
    wire = build_selected_capability_request(confirmed.visible_options[0], request)
    assert wire.count == 1 and "42" in wire.search_goal["explicit_terms"]
    assert "Лучи" in wire.search_goal["explicit_terms"] and set(request.need) <= set(wire.search_goal["explicit_terms"])


def test_binder_rejects_foreign_or_inactive_facts_wrapper() -> None:
    state = _state()
    confirmed = __import__("nmbot_v2.pending_action", fromlist=["confirm_pending_action"]).confirm_pending_action(state, "verify-mortgage-42").state
    request = compile_capability_request(confirmed)
    for fact in ({"id": 43, "state": 2, "mortgage_calc": [{"novos_id": 42, "state": 2, "min_percent": 1}]}, {"id": 42, "state": 1, "mortgage_calc": [{"novos_id": 42, "state": 2, "min_percent": 1}]}):
        assert bind_evidence(request, {"facts": [fact]}).status == EvidenceStatus.EVIDENCE_REJECTED


def test_binder_requires_one_matching_active_selected_root() -> None:
    state = _state()
    confirmed = __import__("nmbot_v2.pending_action", fromlist=["confirm_pending_action"]).confirm_pending_action(state, "verify-mortgage-42").state
    request = compile_capability_request(confirmed)
    calc = {"novos_id": 42, "state": 2, "min_percent": 6.2}
    assert bind_evidence(request, {"mortgage_calc": calc}).status == EvidenceStatus.EVIDENCE_REJECTED
    assert bind_evidence(request, {"facts": []}).status == EvidenceStatus.EVIDENCE_REJECTED
    assert bind_evidence(request, {"facts": [{"id": 43, "state": 2, "mortgage_calc": calc}]}).status == EvidenceStatus.EVIDENCE_REJECTED
    assert bind_evidence(request, {"facts": [{"id": 42, "state": 1, "mortgage_calc": calc}]}).status == EvidenceStatus.EVIDENCE_REJECTED
    root = {"id": 42, "state": 2, "mortgage_calc": calc}
    assert bind_evidence(request, {"facts": [root, root]}).status == EvidenceStatus.EVIDENCE_REJECTED


def test_normalizer_does_not_promote_foreign_or_inactive_nested_mortgage() -> None:
    for row in ({"novos_id": 43, "state": 2, "min_percent": 1}, {"novos_id": 42, "state": 1, "min_percent": 1}):
        card = normalize_card({"id": 42, "state": 2, "name": "Лучи", "mortgage_calc": [row]})
        assert card.mortgage_rate is None and card.mortgage_terms is None


def test_consent_fetches_once_completes_and_never_shows_id() -> None:
    service = CapabilityService()
    plan = SemanticPlan(operation="freeform", followup_outcome="accept", selected_option_name="Лучи", requested_facts=("mortgage_terms",), facts_needed=("mortgage_terms",), intent="mortgage")
    context = SafeTurnContext("test", "да")
    first = TurnProcessor(planner=Planner(plan), search_service=service).process(context, _state())
    second = TurnProcessor(planner=Planner(plan), search_service=service).process(context, ConversationState.from_dict(first.state))
    assert service.calls == 1
    assert first.state["pending_action"]["status"] == "completed" and first.state.get("pending_followup") is None
    assert "6.2%" in first.response_text and "42" not in first.response_text
    assert "ориентир" not in first.response_text.lower() and first.response_text.count("?") == 1
    assert second.state.get("pending_followup") is None


def test_consent_rejects_another_visible_entity_before_capability_fetch() -> None:
    service = CapabilityService()
    state = _state()
    other = OptionCard(name="Другой ЖК", entity_id=99, entity_type="residential_complex")
    state = ConversationState.from_dict({
        **state.to_dict(),
        "visible_options": [state.visible_options[0].__dict__, other.__dict__],
    })
    plan = SemanticPlan(
        operation="freeform",
        followup_outcome="accept",
        selected_option_name=other.name,
        requested_facts=("mortgage_terms",),
        facts_needed=("mortgage_terms",),
        intent="mortgage",
    )

    result = TurnProcessor(planner=Planner(plan), search_service=service).process(SafeTurnContext("test", "да"), state)

    assert service.calls == 0
    assert result.execution.error_code == "selected_capability_entity_mismatch"
    assert "не удалось подтвердить" in result.response_text
    assert result.state["selected_option_name"] == "Лучи"
    assert result.state["selected_entity"] == state.selected_entity.to_dict()


def test_transport_failure_offers_operator_without_phone_capture() -> None:
    async def broken(_): raise TimeoutError()
    state = _state()
    confirmed = __import__("nmbot_v2.pending_action", fromlist=["confirm_pending_action"]).confirm_pending_action(state, "verify-mortgage-42").state
    card, evidence, meta = asyncio.run(fetch_selected_capability(confirmed.visible_options[0], compile_capability_request(confirmed), broken))
    assert card.name == "Лучи" and evidence.status == EvidenceStatus.EVIDENCE_EMPTY and meta["status"] == "selected_capability_timeout"


def test_binder_rejects_invalid_finance_bounds_and_non_finite_values() -> None:
    state = _state()
    confirmed = __import__("nmbot_v2.pending_action", fromlist=["confirm_pending_action"]).confirm_pending_action(state, "verify-mortgage-42").state
    request = compile_capability_request(confirmed)
    invalid_values = (
        {"min_percent": float("nan")}, {"min_percent": float("inf")}, {"min_percent": -1}, {"min_percent": 101},
        {"min_fee": float("nan")}, {"min_fee": float("inf")}, {"min_fee": -1}, {"min_fee": 101},
        {"credit_month": True}, {"credit_month": float("nan")}, {"credit_month": float("inf")}, {"credit_month": 0}, {"credit_month": -1}, {"credit_month": 1.5}, {"credit_month": 1201},
    )
    for values in invalid_values:
        row = {"novos_id": 42, "state": 2, **values}
        assert bind_evidence(request, {"facts": [{"id": 42, "state": 2, "mortgage_calc": row}]}).status == EvidenceStatus.EVIDENCE_REJECTED


def test_capability_failure_sets_operator_fallback_pending_without_phone_capture() -> None:
    class FailedCapabilityService:
        def verify_selected_capability(self, card, request):
            return card, bind_evidence(request, {}), {"status": "selected_capability_timeout"}

    plan = SemanticPlan(operation="freeform", followup_outcome="accept", selected_option_name="Лучи", requested_facts=("mortgage_terms",), facts_needed=("mortgage_terms",), intent="mortgage")
    turn = TurnProcessor(planner=Planner(plan), search_service=FailedCapabilityService()).process(SafeTurnContext("test", "да"), _state())

    assert turn.state["pending_action"]["status"] == "cancelled"
    assert turn.state["pending_followup"] == "selected_live_fact_consent"
    assert turn.state["operator_offered"] is True
    assert "Передать запрос оператору?" in turn.response_text


def test_decline_cancels_current_capability_action() -> None:
    result = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="decline"))).process(
        SafeTurnContext("test", "нет"), _state()
    )

    assert result.state.get("pending_followup") is None
    assert result.state["pending_action"]["status"] == "cancelled"


def test_replacement_followup_without_action_clears_stale_action() -> None:
    replaced = apply_state_delta(_state(), StateDelta(pending_followup="selected_live_fact_consent"))
    same_key_replaced = apply_state_delta(_state(), StateDelta(pending_followup="financing_consent", replaces_pending_offer=True))

    assert replaced.pending_followup == "selected_live_fact_consent"
    assert replaced.pending_action is None
    assert same_key_replaced.pending_action is None


def test_declined_action_cannot_execute_new_legacy_financing_consent() -> None:
    service = CapabilityService()
    declined = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="decline"))).process(
        SafeTurnContext("test", "нет"), _state()
    )
    legacy = apply_state_delta(ConversationState.from_dict(declined.state), StateDelta(pending_followup="financing_consent"))
    result = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="accept")), search_service=service).process(
        SafeTurnContext("test", "да"), legacy
    )

    assert service.calls == 0
    assert result.action.value == "accept_operator"


def test_new_bound_action_executes_only_new_selected_entity_once() -> None:
    service = CapabilityService()
    declined = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="decline"))).process(
        SafeTurnContext("test", "нет"), _state()
    )
    other = OptionCard(name="Другой ЖК", entity_id=99, entity_type="residential_complex")
    entity = SelectedEntity(other.entity_type, other.entity_id, other.name)
    new_action = PendingAction("verify_selected_facts", ("mortgage_terms",), entity.entity_type, entity.entity_id, "pending", "verify-mortgage-99")
    next_offer = apply_state_delta(
        ConversationState.from_dict(declined.state),
        StateDelta(visible_options=(other,), selected_option_name=other.name, selected_entity=entity, pending_followup="financing_consent", pending_action=new_action),
    )
    result = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="accept", selected_option_name=other.name)), search_service=service).process(
        SafeTurnContext("test", "да"), next_offer
    )

    assert service.calls == 1
    assert service.requests[0].entity_id == 99
    assert result.state["selected_entity"]["entity_id"] == 99
    assert result.state["pending_action"]["status"] == "completed"


def test_completed_action_duplicate_consent_stays_non_executable_and_reset_clears_all() -> None:
    service = CapabilityService()
    plan = SemanticPlan(operation="freeform", followup_outcome="accept", selected_option_name="Лучи", intent="mortgage")
    completed = TurnProcessor(planner=Planner(plan), search_service=service).process(SafeTurnContext("test", "да"), _state())
    duplicate = TurnProcessor(planner=Planner(plan), search_service=service).process(SafeTurnContext("test", "да"), ConversationState.from_dict(completed.state))
    reset = TurnProcessor(planner=Planner(SemanticPlan(operation="reset"))).process(
        SafeTurnContext("test", "/start"), ConversationState.from_dict(completed.state)
    )

    assert service.calls == 1
    assert duplicate.state["pending_action"]["status"] == "completed"
    assert reset.state.get("pending_action") is None and reset.state.get("pending_followup") is None
