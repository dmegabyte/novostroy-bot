from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import pytest

from nmbot_v3.evidence_contract import (
    CanonicalCard,
    EvidenceModeV3,
    EvidenceRequest,
    EvidenceResult,
    normalize_evidence_result,
    validate_evidence_result,
)


def card(name: str, *, near: bool = False) -> CanonicalCard:
    return CanonicalCard(name=name, fields={"price_min": 12_000_000}, is_near=near, differences=("price",) if near else ())


def test_dtos_freeze_nested_values_and_do_not_mutate_sources() -> None:
    hard = {"rooms": [2], "price": {"max": 18_000_000}}
    fields = {"metro": {"name": "Сокол"}}
    before_hard, before_fields = deepcopy(hard), deepcopy(fields)

    request = EvidenceRequest(hard_constraints=hard)
    result = EvidenceResult(facts=(CanonicalCard("ЖК Лучи", fields),))

    assert hard == before_hard and fields == before_fields
    assert request.hard_constraints["rooms"] == (2,)
    assert result.facts[0].fields["metro"]["name"] == "Сокол"
    with pytest.raises(TypeError): request.hard_constraints["rooms"] = ()
    with pytest.raises(TypeError): result.facts[0].fields["metro"]["name"] = "Динамо"


def test_facts_and_near_are_structurally_separate_and_deduplicated() -> None:
    with pytest.raises(ValueError, match="fact_marked_near"):
        EvidenceResult(facts=(card("ЖК Лучи", near=True),))
    with pytest.raises(ValueError, match="near_not_marked"):
        EvidenceResult(near=(card("ЖК Лучи"),))
    with pytest.raises(ValueError, match="duplicate_evidence_card"):
        EvidenceResult(facts=(card("ЖК Лучи"),), near=(card("жк лучи", near=True),))
    with pytest.raises(ValueError, match="duplicate_evidence_ref"):
        EvidenceResult(
            facts=(CanonicalCard("ЖК Лучи", canonical_ref="550e8400-e29b-41d4-a716-446655440000"),),
            near=(CanonicalCard("ЖК Другой", canonical_ref="550e8400-e29b-41d4-a716-446655440000", is_near=True, differences=("price",)),),
        )


def test_named_request_is_exact_and_is_bounded_to_one() -> None:
    request = EvidenceRequest(mode="named_object", exact_name="ЖК Лучи", count=1)
    assert validate_evidence_result(request, EvidenceResult(facts=(card("ЖК Лучи"),))).ok
    mismatch = validate_evidence_result(request, EvidenceResult(facts=(card("ЖК Лучи 2"),)))
    assert mismatch.errors == ("exact_name_mismatch",)
    with pytest.raises(ValueError, match="invalid_named_object_request"):
        EvidenceRequest(mode="named_object", exact_name="ЖК Лучи", count=2)


def test_current_option_result_requires_exact_names_and_request_order() -> None:
    first, second, third = (
        "550e8400-e29b-41d4-a716-446655440000",
        "550e8400-e29b-41d4-a716-446655440001",
        "550e8400-e29b-41d4-a716-446655440002",
    )
    request = EvidenceRequest(
        mode=EvidenceModeV3.CURRENT_OPTIONS_FACT_CHECK,
        current_option_refs=(first, second, third),
    )
    ordered = EvidenceResult(facts=(CanonicalCard("ЖК Первый", canonical_ref=first),), near=(CanonicalCard("ЖК Третий", canonical_ref=third, is_near=True, differences=("price",)),))
    assert validate_evidence_result(request, ordered).ok
    reordered = EvidenceResult(facts=(CanonicalCard("ЖК Третий", canonical_ref=third), CanonicalCard("ЖК Первый", canonical_ref=first)))
    assert validate_evidence_result(request, reordered).errors == ("current_option_order_changed",)
    foreign = EvidenceResult(facts=(CanonicalCard("ЖК Другой", canonical_ref=second),))
    assert validate_evidence_result(request, foreign).ok


def test_evidence_normalization_keeps_fact_near_order_and_derives_missing_requested_facts() -> None:
    request = EvidenceRequest(requested_facts=("metro", "parking", "schools"))
    raw = {
        "facts": [{"name": "ЖК Факт", "fields": {"metro": "Сокол", "schools": True}, "is_near": False, "differences": []}],
        "near": [{"name": "ЖК Рядом", "fields": {"parking": True}, "is_near": True, "differences": ["location"]}],
        "missing_facts": ["schools"],
    }

    normalized = normalize_evidence_result(request, raw)

    assert tuple(card.name for card in normalized.facts) == ("ЖК Факт",)
    assert tuple(card.name for card in normalized.near) == ("ЖК Рядом",)
    assert normalized.missing_facts == ("parking",)
    assert validate_evidence_result(request, normalized).ok


def test_validation_fails_closed_for_exclusions_missing_hard_evidence_and_non_normalized_missing() -> None:
    request = EvidenceRequest(
        requested_facts=("metro", "parking"),
        hard_constraints={"rooms": [2], "max_price": 18_000_000},
        excluded_names=("ЖК «Лучи»",),
    )
    result = EvidenceResult(
        facts=(CanonicalCard("Лучи", {"rooms": [2], "metro": "Солнцево"}),),
        missing_facts=("metro",),
    )

    validation = validate_evidence_result(request, result)

    assert validation.ok is False
    assert validation.errors == (
        "excluded_name_returned",
        "fact_0_missing_hard_evidence:max_price",
        "missing_facts_not_normalized",
    )


def test_request_rejects_unknown_closed_fact_without_restricting_opaque_hard_values() -> None:
    with pytest.raises(ValueError, match="unknown_requested_fact"):
        EvidenceRequest(requested_facts=("unbounded_provider_field",))
    assert EvidenceRequest(hard_constraints={"provider_score": {"opaque": True}}).hard_constraints["provider_score"]["opaque"] is True


def test_current_apartment_inventory_requires_a_safe_scalar_and_never_uses_ads_count() -> None:
    request = EvidenceRequest(requested_facts=("apartment_inventory",))

    confirmed = EvidenceResult(facts=(CanonicalCard("ЖК Лучи", {"apartment_inventory": 5, "ads_count": 99}),))
    ads_only = EvidenceResult(facts=(CanonicalCard("ЖК Витрина", {"ads_count": 99}),))
    pointer = EvidenceResult(facts=(CanonicalCard("ЖК Указатель", {"apartment_inventory": "данные доступны через поиск"}),))
    private = EvidenceResult(facts=(CanonicalCard("ЖК Личный", {"apartment_inventory": "+7 999 123-45-67"}),))

    assert validate_evidence_result(request, confirmed).ok
    assert normalize_evidence_result(request, ads_only).missing_facts == ("apartment_inventory",)
    assert validate_evidence_result(request, pointer).errors == ("fact_0_invalid_apartment_inventory", "missing_facts_not_normalized")
    assert validate_evidence_result(request, private).errors == ("fact_0_invalid_apartment_inventory", "missing_facts_not_normalized")


def test_request_scope_and_result_parse_errors_fail_closed_with_safe_codes() -> None:
    with pytest.raises(ValueError, match="invalid_exact_name_scope"):
        EvidenceRequest(exact_name="ЖК Лучи")
    with pytest.raises(ValueError, match="invalid_current_options_request"):
        EvidenceRequest(mode="current_options_fact_check")
    request = EvidenceRequest()
    validation = validate_evidence_result(request, {"facts": [{"name": "ЖК Лучи", "is_near": True, "differences": ["price"]}]})
    assert validation.result is None
    assert validation.errors == ("fact_marked_near",)
    assert validation.repairable is True


def test_evidence_contract_import_closure_never_reaches_v2_or_transport_layers() -> None:
    source = Path("nmbot_v3/evidence_contract.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    from_imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = ("nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4", "aiohttp", "requests", "http", "socket", "gateway", "mcp", "cache")
    assert not any(name.startswith(banned) for name in imports + from_imports)
