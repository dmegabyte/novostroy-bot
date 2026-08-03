"""Closed local V3 parity fixtures for the compatible financing consent slice."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nmbot_v3.contracts import IntentPlanV3, V3PlannerContext, V3SemanticAction, V3SemanticStage
from nmbot_v3.evidence_contract import EvidenceResult
from nmbot_v3.orchestration import orchestrate_v3_turn
from nmbot_v3.presentation import V3WriterBriefInput
from nmbot_v3.state import V3ConversationState
from nmbot_v3.transition import derive_transition_v3


_REF = "550e8400-e29b-41d4-a716-446655440000"


def _plan(**overrides: object) -> IntentPlanV3:
    values: dict[str, object] = {
        "schema_version": 3,
        "goal": "answer_selected",
        "viewpoint": "financing",
        "selected_option_ref": _REF,
        "requested_facts": ("mortgage_terms",),
    }
    values.update(overrides)
    return IntentPlanV3(**values)


def _writer() -> V3WriterBriefInput:
    return V3WriterBriefInput(
        client_request="Уточните ипотеку",
        answer_goal="answer_selected",
        cards=(),
        mandatory_cta="Какой вариант хотите рассмотреть подробнее?",
    )


@pytest.mark.parametrize(
    ("outcome", "stage", "action"),
    (
        ("accept", V3SemanticStage.SELECTED_OBJECT, V3SemanticAction.ANSWER_SELECTED),
        ("decline", V3SemanticStage.OPERATOR_DECLINED, V3SemanticAction.DECLINE_OPERATOR),
        ("ask_or_clarify", V3SemanticStage.FINANCING_CLARIFICATION, V3SemanticAction.CLARIFY_FINANCING),
        ("unexpected", V3SemanticStage.FINANCING_CLARIFICATION, V3SemanticAction.CLARIFY_FINANCING),
        (None, V3SemanticStage.FINANCING_CLARIFICATION, V3SemanticAction.CLARIFY_FINANCING),
    ),
)
def test_financing_consent_closed_transition_matrix(
    outcome: str | None, stage: V3SemanticStage, action: V3SemanticAction
) -> None:
    decision = derive_transition_v3(
        _plan(followup_outcome=outcome),
        V3PlannerContext((_REF,), "financing_consent", True),
    )

    assert (decision.stage, decision.action, decision.accepted) == (stage, action, True)


def test_financing_accept_without_pending_action_fails_closed_to_operator_handoff() -> None:
    decision = derive_transition_v3(
        _plan(followup_outcome="accept"), V3PlannerContext((_REF,), "financing_consent")
    )

    assert (decision.stage, decision.action) == (
        V3SemanticStage.OPERATOR_HANDOFF, V3SemanticAction.ACCEPT_OPERATOR
    )


@pytest.mark.parametrize(
    ("outcome", "expected_question", "expected_pending", "expected_action"),
    (
        (None, "Проверить условия по этому ЖК?", "financing_consent", True),
        ("ask_or_clarify", "Проверить условия по этому ЖК?", "financing_consent", True),
        ("decline", "Хотите сузить варианты по бюджету, району или отделке?", None, False),
        ("accept", "Какой вариант хотите рассмотреть подробнее?", "financing_consent", True),
    ),
)
def test_financing_pending_orchestration_is_single_question_and_never_persists_contact_data(
    outcome: str | None, expected_question: str, expected_pending: str | None, expected_action: bool
) -> None:
    state = V3ConversationState(
        visible_option_refs=(_REF,), pending_followup_key="financing_consent", has_pending_action=True,
    )
    result = orchestrate_v3_turn(state, _plan(followup_outcome=outcome), EvidenceResult(), _writer())

    assert result.ok is True
    assert result.public_response.endswith(expected_question)
    assert result.public_response.count("?") == 1
    assert result.state.pending_followup_key == expected_pending
    assert result.state.has_pending_action is expected_action
    persisted = result.state.to_dict()
    assert set(persisted) == {
        "schema_version", "revision", "stage", "last_action", "visible_option_refs",
        "pending_followup_key", "has_pending_action",
    }
    assert "phone" not in str(persisted).casefold()
    assert "email" not in str(persisted).casefold()


def test_v3_financing_slice_has_no_v2_or_network_imports() -> None:
    paths = (Path("nmbot_v3/transition.py"), Path("nmbot_v3/renderer.py"), Path("nmbot_v3/orchestration.py"))
    banned = ("nmbot_v2", "requests", "aiohttp", "http", "socket", "gateway", "runtime", "service")
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        from_imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(name.startswith(banned) for name in imports + from_imports)
