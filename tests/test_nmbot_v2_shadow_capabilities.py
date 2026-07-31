from dataclasses import replace

import pytest

from nmbot_v2.capability_registry import CapabilitySpec, CapabilityStatus, compile_capability_request
from nmbot_v2.contracts import PendingAction, SelectedEntity
from nmbot_v2.evidence_resolver import EvidenceStatus, bind_evidence
from nmbot_v2.pending_action import (
    cancel_pending_action,
    complete_pending_action,
    confirm_pending_action,
    offer_pending_action,
    select_entity,
)
from nmbot_v2.state import ConversationState
from nmbot_v2.vocabulary import FACT_KEYS


def _entity(entity_id=42):
    return SelectedEntity("residential_complex", entity_id, "Лучи")


def _action(*facts, key="one"):
    return PendingAction("verify_selected_facts", facts or ("mortgage_terms",), "residential_complex", 42, "pending", key)


def _confirmed(*facts):
    state = ConversationState(params={"max_price": 12_000_000}, selected_entity=_entity(), pending_followup="financing_consent")
    state = offer_pending_action(state, _action(*facts)).state
    return confirm_pending_action(state, "one").state


def test_reducer_preserves_params_replaces_newest_and_is_idempotent():
    state = ConversationState(params={"rooms": 2, "max_price": 10_000_000}, selected_entity=_entity())
    first = offer_pending_action(state, _action("mortgage_terms", key="old")).state
    second = offer_pending_action(first, _action("parking", key="new")).state
    assert second.params == state.params and second.pending_action.idempotency_key == "new"
    assert not confirm_pending_action(second, "old").changed
    once = confirm_pending_action(second, "new")
    assert once.execute and once.state.pending_action.status == "confirmed"
    assert not confirm_pending_action(once.state, "new").changed
    done = complete_pending_action(once.state, "new")
    assert done.changed and not complete_pending_action(done.state, "new").changed


def test_reducer_requires_current_selection_and_invalidates_stale_action():
    state = ConversationState(selected_entity=_entity())
    state = offer_pending_action(state, _action()).state
    switched = select_entity(state, _entity(99))
    assert switched.state.pending_action is None and switched.reason == "stale_pending_cancelled"
    assert not confirm_pending_action(switched.state, "one").execute
    assert offer_pending_action(ConversationState(), _action()).reason == "selected_entity_required"
    assert not cancel_pending_action(switched.state, "one").changed


def test_every_known_fact_has_an_explicit_compilation_outcome():
    for fact in FACT_KEYS:
        compiled = compile_capability_request(_confirmed(fact))
        assert compiled.status in {CapabilityStatus.READY, CapabilityStatus.CAPABILITY_MISSING}
        assert compiled.fact_keys == (fact,)


def test_compile_fails_closed_without_confirmed_matching_action():
    pending = ConversationState(selected_entity=_entity(), pending_action=_action())
    assert compile_capability_request(pending).status == CapabilityStatus.PREREQUISITE_MISSING
    confirmed = _confirmed()
    mismatched = replace(confirmed, selected_entity=_entity(43))
    assert compile_capability_request(mismatched).status == CapabilityStatus.PREREQUISITE_MISSING


def test_mortgage_evidence_matching_active_full_is_complete_and_bounded():
    request = compile_capability_request(_confirmed("mortgage_terms"))
    result = bind_evidence(request, {"facts": [{"id": 42, "state": 2, "mortgage_calc": [{"novos_id": 42, "state": 2, "min_percent": 6.2, "min_fee": 20, "credit_month": 360}]}]})
    assert result.status == EvidenceStatus.EVIDENCE_COMPLETE
    assert result.data == {"min_percent": 6.2, "min_fee": 20, "credit_month": 360}
    assert "novos_id" not in result.data and "mortgage_calc" not in result.data


def test_mortgage_evidence_partial_and_rejects_foreign_or_inactive_rows():
    request = compile_capability_request(_confirmed("mortgage_terms"))
    partial = bind_evidence(request, {"facts": [{"id": 42, "state": 2, "mortgage_calc": {"novos_id": "42", "state": "2", "min_percent": 7.0}}]})
    assert partial.status == EvidenceStatus.EVIDENCE_PARTIAL
    assert partial.data == {"min_percent": 7.0}
    for row in ({"novos_id": 43, "state": 2, "min_percent": 5}, {"novos_id": 42, "state": 1, "min_percent": 5}):
        assert bind_evidence(request, {"facts": [{"id": 42, "state": 2, "mortgage_calc": row}]}).status == EvidenceStatus.EVIDENCE_REJECTED


def test_mortgage_evidence_uses_only_linked_active_program_and_matching_rows():
    request = compile_capability_request(_confirmed("mortgage_terms"))
    raw = {
        "facts": [{
            "id": 42, "state": 2,
            "mortgage_calc": [
                {"novos_id": 9, "state": 2, "min_percent": 1},
                {"novos_id": 42, "state": 2, "mortgage_id": 7, "min_percent": 6, "credit_month": 240},
                {"novos_id": 42, "state": 2, "mortgage_id": 8, "min_percent": 4},
            ],
            "mortgage": [{"id": 7, "state": 2, "year_percent": 6}, {"id": 8, "state": 1, "year_percent": 4}],
        }],
    }
    result = bind_evidence(request, raw)
    assert result.status == EvidenceStatus.EVIDENCE_PARTIAL
    assert result.data == {"min_percent": 6, "credit_month": 240}
    # An unlinked mortgage is not itself evidence.
    assert bind_evidence(request, {"facts": [{"id": 42, "state": 2, "mortgage": [{"id": 7, "state": 2, "year_percent": 1}]}]}).status == EvidenceStatus.EVIDENCE_EMPTY


def test_unknown_contract_values_fail_closed():
    with pytest.raises(ValueError):
        _action("unknown_fact")
    with pytest.raises(ValueError):
        PendingAction("unknown_action", ("mortgage_terms",), "residential_complex", 42, "pending", "x")
    with pytest.raises(ValueError):
        PendingAction("verify_selected_facts", ("mortgage_terms",), "residential_complex", 42, "unknown_status", "x")


def test_future_capability_specs_can_declare_closed_root_and_evidence_requirements():
    spec = CapabilitySpec(
        "parking", ("parking",), executable=True,
        required_root_fields=("id", "state"), required_evidence_fields=("parking",),
    )

    assert spec.required_root_fields == ("id", "state")
    assert spec.required_evidence_fields == ("parking",)
    with pytest.raises(ValueError):
        CapabilitySpec("parking", ("parking",), required_root_fields=("raw_secret",))
