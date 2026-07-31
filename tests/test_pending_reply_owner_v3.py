from __future__ import annotations

import asyncio

import pytest

import followup_intent_classifier as planner

from nmbot_v2.contracts import OptionCard, PendingAction, SafeTurnContext, SelectedEntity
from nmbot_v2.pending_action import offer_pending_action
from nmbot_v2.runtime import TurnProcessor
from nmbot_v2.state import ConversationState
from nmbot_v2.transition import compile_executable_turn_v3
from scripts.nmbot_runtime_adapter import _semantic_plan_from_intent_plan_v3


class _Planner:
    def __init__(self, plan):
        self.plan_value = plan

    def plan(self, context, state):
        return self.plan_value


class _CapabilityService:
    def __init__(self):
        self.calls = 0

    def verify_selected_capability(self, card, request):
        from nmbot_v2.evidence_resolver import bind_evidence

        self.calls += 1
        evidence = bind_evidence(request, {"facts": [{"id": 42, "state": 2, "mortgage_calc": [{"novos_id": 42, "state": 2, "min_percent": 6.2, "min_fee": 20, "credit_month": 360}]}]})
        return OptionCard(name=card.name, entity_id=42, entity_type="residential_complex", mortgage_terms="ставка от 6.2%"), evidence, {"status": "selected_capability_evidence_complete"}


def _state() -> ConversationState:
    card = OptionCard(name="Лучи", entity_id=42, entity_type="residential_complex")
    entity = SelectedEntity("residential_complex", 42, card.name)
    action = PendingAction("verify_selected_facts", ("mortgage_terms",), entity.entity_type, entity.entity_id, "pending", "verify-mortgage-42")
    return offer_pending_action(ConversationState(visible_options=(card,), selected_option_name=card.name, selected_entity=entity, pending_followup="financing_consent"), action).state


def _finance_pending(*, outcomes=("accept", "decline", "ask_or_clarify", "unexpected")):
    return {
        "id": "financing_consent",
        "allowed_reply_outcomes": list(outcomes),
        "context": {
            "scope": "one",
            "goals_by_scope": {"one": "answer_selected", "all": "answer_current"},
            "viewpoint": "financing",
            "selected_option_name": "Лучи",
            "requested_facts": ["mortgage_terms"],
        },
    }


def _live_fact_pending(*, outcomes=("accept", "decline", "ask_or_clarify", "unexpected")):
    return {
        "id": "selected_live_fact_consent",
        "allowed_reply_outcomes": list(outcomes),
        "context": {
            "scope": "one",
            "goals_by_scope": {"one": "answer_selected"},
            "viewpoint": "unchanged",
            "selected_option_name": "Лучи",
            "requested_facts": ["parking"],
        },
    }


def test_pending_owner_resolves_clear_financing_accept_without_provider() -> None:
    plan = asyncio.run(planner.plan_intent_v3(None, user_text="да", pending_scenario=_finance_pending(), allowed_facts=["mortgage_terms"]))

    assert plan["followup_outcome"] == "accept"
    assert plan["goal"] == "answer_selected"
    assert plan["selected_option_name"] == "Лучи"
    assert plan["requested_facts"] == ["mortgage_terms"]


def test_pending_owner_declines_only_when_allowed_and_leaves_ambiguous_or_disallowed_to_model() -> None:
    assert planner._pending_reply_intent_plan_v3("нет", pending_scenario=_finance_pending(), allowed_facts=["mortgage_terms"])["followup_outcome"] == "decline"
    assert planner._pending_reply_intent_plan_v3("да наверное", pending_scenario=_finance_pending(), allowed_facts=["mortgage_terms"]) is None
    assert planner._pending_reply_intent_plan_v3("да", pending_scenario=_finance_pending(outcomes=("decline",)), allowed_facts=["mortgage_terms"]) is None
    assert planner._pending_reply_intent_plan_v3("да", pending_scenario=None, allowed_facts=["mortgage_terms"]) is None


@pytest.mark.parametrize(
    ("user_text", "pending", "allowed_facts", "expected"),
    [
        ("да", _finance_pending(), ["mortgage_terms"], ("answer_selected", "financing", "accept")),
        ("нет", _finance_pending(), ["mortgage_terms"], ("answer_selected", "financing", "decline")),
        ("да", _live_fact_pending(), ["parking"], ("answer_selected", "unchanged", "accept")),
        ("нет", _live_fact_pending(), ["parking"], ("answer_selected", "unchanged", "decline")),
        ("да", _finance_pending(outcomes=("decline",)), ["mortgage_terms"], None),
        ("да", None, ["mortgage_terms"], None),
        ("да наверное", _finance_pending(), ["mortgage_terms"], None),
        ("да", {**_finance_pending(), "context": {**_finance_pending()["context"], "selected_option_name": ""}}, ["mortgage_terms"], None),
        ("да", {**_finance_pending(), "context": {**_finance_pending()["context"], "requested_facts": ["not_a_fact"]}}, ["mortgage_terms"], None),
    ],
)
def test_pending_reply_resolver_contract_matrix(user_text, pending, allowed_facts, expected) -> None:
    resolved = planner._pending_reply_intent_plan_v3(user_text, pending_scenario=pending, allowed_facts=allowed_facts)

    if expected is None:
        assert resolved is None
    else:
        assert resolved is not None
        assert (resolved["goal"], resolved["viewpoint"], resolved["followup_outcome"]) == expected


def test_v3_adapter_and_runtime_preserve_accept_and_fetch_capability_once() -> None:
    state = _state()
    raw = planner._pending_reply_intent_plan_v3("да", pending_scenario=_finance_pending(), allowed_facts=["mortgage_terms"])
    assert raw is not None
    semantic = _semantic_plan_from_intent_plan_v3(dict(raw), state, query_text="да")
    executable = compile_executable_turn_v3(raw, state, query_text="да", allowed_facts=("mortgage_terms",))
    service = _CapabilityService()
    result = TurnProcessor(planner=_Planner(executable), search_service=service).process(SafeTurnContext("test", "да"), state)

    assert semantic.followup_outcome == "accept"
    assert executable.followup_outcome == "accept"
    assert service.calls == 1
    assert result.state["pending_action"]["status"] == "completed"
    assert result.state.get("pending_followup") is None
    assert "42" not in result.response_text
