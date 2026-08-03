from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nmbot_v3.contracts import IntentPlanV3, V3ContractError
from nmbot_v3.evidence_contract import CanonicalCard, EvidenceResult
from nmbot_v3.orchestration import orchestrate_v3_turn
from nmbot_v3.presentation import V3PresentationCard, V3WriterBriefInput
from nmbot_v3.state import V3ConversationState, V3StateDelta, apply_v3_state_delta


def _plan(**overrides: object) -> IntentPlanV3:
    values: dict[str, object] = {"schema_version": 3, "goal": "new_search", "viewpoint": "life"}
    values.update(overrides)
    return IntentPlanV3(**values)


def _writer(card: CanonicalCard | None = None) -> V3WriterBriefInput:
    cards = () if card is None else (V3PresentationCard(card.name, card.fields),)
    return V3WriterBriefInput(
        client_request="Подберите квартиру",
        answer_goal="present_search_results",
        cards=cards,
        mandatory_cta="Какой вариант показать подробнее?",
    )


def test_v3_reducer_is_immutable_explicit_and_persistable() -> None:
    state = V3ConversationState.clean(4)
    delta = V3StateDelta(1, "answered", "respond")

    updated = apply_v3_state_delta(state, delta)

    assert state.to_dict() == {"schema_version": "V3", "revision": 4, "stage": "reset", "last_action": None,
                                "visible_option_refs": [], "pending_followup_key": None, "has_pending_action": False}
    assert updated.to_dict() == {"schema_version": "V3", "revision": 5, "stage": "answered", "last_action": "respond",
                                  "visible_option_refs": [], "pending_followup_key": None, "has_pending_action": False}
    assert V3ConversationState.from_dict(updated.to_dict()) == updated
    assert apply_v3_state_delta(state, delta, accepted=False) is state
    with pytest.raises(V3ContractError, match="action_without_stage"):
        V3StateDelta(last_action="respond")


def test_v3_state_round_trip_reconstructs_only_closed_planner_context() -> None:
    state = V3ConversationState(
        revision=2,
        visible_option_refs=("550e8400-e29b-41d4-a716-446655440000",),
        pending_followup_key="financing_consent",
        has_pending_action=True,
    )

    restored = V3ConversationState.from_dict(state.to_dict())

    assert restored.planner_context.visible_option_refs == ("550e8400-e29b-41d4-a716-446655440000",)
    assert restored.planner_context.pending_followup_key == "financing_consent"
    assert restored.planner_context.has_pending_action is True
    assert set(restored.to_dict()) == {
        "schema_version", "revision", "stage", "last_action", "visible_option_refs",
        "pending_followup_key", "has_pending_action",
    }
    for private_label in ("+7 999 123-45-67", "client@example.com", "Иван Иванов"):
        with pytest.raises(V3ContractError, match="invalid_visible_option_refs"):
            V3ConversationState(visible_option_refs=(private_label,))
    with pytest.raises(V3ContractError, match="unknown_field"):
        V3ConversationState.from_dict({
            **state.to_dict(),
            "visible_option_names": ["ЖК Лучи", "client@example.com"],
        })


def test_v3_orchestration_success_returns_response_delta_and_new_state() -> None:
    card = CanonicalCard("ЖК Лучи", {"metro": "Солнцево"}, "550e8400-e29b-41d4-a716-446655440000")
    result = orchestrate_v3_turn(V3ConversationState.clean(), _plan(), EvidenceResult(facts=(card,)), _writer(card))

    assert result.ok is True
    assert result.state_delta == V3StateDelta(
        1, "answered", "respond", result.state.planner_context
    )
    assert result.state.to_dict()["revision"] == 1
    assert result.state.to_dict()["visible_option_refs"] == ["550e8400-e29b-41d4-a716-446655440000"]
    assert result.response.cards[0].name == "ЖК Лучи"
    assert result.public_response.endswith("Какой вариант показать подробнее?")


def test_v3_unidentified_evidence_is_ephemeral_and_does_not_create_current_options() -> None:
    card = CanonicalCard("ЖК Без UUID", {"metro": "Солнцево"})

    result = orchestrate_v3_turn(V3ConversationState.clean(), _plan(), EvidenceResult(facts=(card,)), _writer(card))

    assert result.ok is True
    assert result.state.visible_option_refs == ()


def test_v3_renders_confirmed_current_apartment_inventory_without_ads_substitution() -> None:
    card = CanonicalCard("ЖК Лучи", {"apartment_inventory": 5, "ads_count": 99})

    result = orchestrate_v3_turn(
        V3ConversationState.clean(),
        _plan(requested_facts=("apartment_inventory",)),
        EvidenceResult(facts=(card,)),
        _writer(card),
    )

    assert result.ok is True
    assert result.response.cards[0].text == "актуальное наличие квартир: 5; объявления: 99"


def test_v3_orchestration_invalid_plan_or_evidence_fails_closed_without_state_change() -> None:
    state = V3ConversationState.clean(8)
    invalid_plan = _plan(requested_facts=("secret_fact",))
    card = CanonicalCard("ЖК Лучи", {"metro": "Солнцево"})

    invalid = orchestrate_v3_turn(state, invalid_plan, EvidenceResult(facts=(card,)), _writer(card))
    bad_evidence = orchestrate_v3_turn(state, _plan(), EvidenceResult(facts=(card,)), _writer())

    assert invalid.ok is False and invalid.state is state and invalid.state_delta.is_empty
    assert invalid.errors == ("invalid_requested_fact",)
    assert bad_evidence.ok is False and bad_evidence.state is state and bad_evidence.state_delta.is_empty
    assert bad_evidence.errors == ("presentation_card_order_or_names_mismatch",)
    assert all("ЖК Лучи" not in result.public_response for result in (invalid, bad_evidence))


def test_v3_orchestration_privacy_failure_does_not_persist_or_leak_source() -> None:
    state = V3ConversationState.clean()
    private = "+7 999 123-45-67"
    card = CanonicalCard("ЖК Лучи", {"metro": private})

    result = orchestrate_v3_turn(state, _plan(), EvidenceResult(facts=(card,)), _writer(card))

    assert result.ok is False and result.state is state
    assert result.errors == ("unsafe_evidence",)
    assert private not in result.public_response
    assert "ЖК Лучи" not in result.public_response


def test_v3_orchestration_import_closure_has_no_cross_version_or_runtime_fallback() -> None:
    paths = (Path("nmbot_v3/state.py"), Path("nmbot_v3/orchestration.py"))
    banned = ("nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4", "scripts", "runtime", "service", "requests", "aiohttp", "http", "socket")
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        from_imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(name.startswith(banned) for name in imports + from_imports)
