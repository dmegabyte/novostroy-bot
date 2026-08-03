"""Focused local V2/V3 evidence overlap fixtures, not full search parity."""
from __future__ import annotations

from nmbot_v2.search_contract import _same_named_object as v2_same_named_object
from nmbot_v2.search_contract import build_current_options_fact_check_request, validate_current_options_fact_check_result
from nmbot_v2.contracts import OptionCard
from nmbot_v3.evidence_contract import CanonicalCard, EvidenceRequest, EvidenceResult, validate_evidence_result


FIRST = "550e8400-e29b-41d4-a716-446655440000"
SECOND = "550e8400-e29b-41d4-a716-446655440001"
FOREIGN = "550e8400-e29b-41d4-a716-446655440002"


def test_differential_fixture_preserves_canonical_named_object_identity_only() -> None:
    raw_name = "ЖК «Лучи»"
    v3_request = EvidenceRequest(mode="named_object", exact_name="Лучи", count=1)
    v3_result = EvidenceResult(facts=(CanonicalCard(raw_name),))

    assert v2_same_named_object({"name": raw_name}, "Лучи") is True
    assert validate_evidence_result(v3_request, v3_result).ok is True


def test_differential_fixture_matches_closed_current_option_scope_accept_reject_surface() -> None:
    v2_request = build_current_options_fact_check_request(
        (OptionCard(name="ЖК Первый"), OptionCard(name="ЖК Второй")), ("metro",), "life"
    )
    v3_request = EvidenceRequest(
        mode="current_options_fact_check", current_option_refs=(FIRST, SECOND), requested_facts=("metro",), count=2
    )
    accepted_v2 = {"facts": [{"name": "ЖК Первый"}], "near": [{"name": "ЖК Второй"}]}
    accepted_v3 = EvidenceResult(
        facts=(CanonicalCard("ЖК Первый", {"metro": "Сокол"}, FIRST),),
        near=(CanonicalCard("ЖК Второй", {}, SECOND, True, ("metro",)),),
    )
    rejected_v2 = {"facts": [{"name": "Посторонний"}], "near": []}
    rejected_v3 = EvidenceResult(facts=(CanonicalCard("Посторонний", {"metro": "Сокол"}, FOREIGN),))

    assert validate_current_options_fact_check_result(accepted_v2, v2_request)["ok"] is True
    assert validate_evidence_result(v3_request, accepted_v3).ok is True
    assert validate_current_options_fact_check_result(rejected_v2, v2_request)["ok"] is False
    assert validate_evidence_result(v3_request, rejected_v3).errors == ("current_option_ref_not_exact",)


def test_differential_fixture_requires_hard_evidence_for_facts_but_not_near() -> None:
    request = EvidenceRequest(hard_constraints={"rooms": [2]})
    result = EvidenceResult(
        facts=(CanonicalCard("ЖК Без комнат"),),
        near=(CanonicalCard("ЖК Рядом", {}, is_near=True, differences=("rooms",)),),
    )

    assert validate_evidence_result(request, result).errors == ("fact_0_missing_hard_evidence:rooms",)
