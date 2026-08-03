"""V3-only deterministic overlap fixtures; these do not execute or import V2."""
from __future__ import annotations

import pytest

from nmbot_v3.contracts import IntentPlanV3
from nmbot_v3.evidence_contract import CanonicalCard, EvidenceResult
from nmbot_v3.presentation import V3PresentationCard, V3WriterBriefInput
from nmbot_v3.renderer import render_v3_response


def _plan(**overrides: object) -> IntentPlanV3:
    values: dict[str, object] = {"schema_version": 3, "goal": "new_search", "viewpoint": "life"}
    values.update(overrides)
    return IntentPlanV3(**values)


def _presentation(cards: tuple[CanonicalCard, ...], *, cta: str = "Какой вариант хотите рассмотреть подробнее?") -> V3WriterBriefInput:
    return V3WriterBriefInput(
        client_request="Подберите квартиру",
        answer_goal="present_search_results",
        cards=tuple(V3PresentationCard(card.name, card.fields) for card in cards),
        mandatory_cta=cta,
    )


@pytest.mark.parametrize(
    ("name", "plan", "evidence", "expected_intro", "expected_cards", "expected_missing"),
    (
        (
            "named-confirmed",
            _plan(goal="lookup_object", named_object_reference="Лучи"),
            EvidenceResult(facts=(CanonicalCard("Лучи", {"ready": "2027"}),)),
            "Нашла подтверждённые данные по ЖК «Лучи».",
            ("Лучи",),
            "",
        ),
        (
            "named-missing",
            _plan(goal="lookup_object", named_object_reference="Лучи"),
            EvidenceResult(),
            "По ЖК «Лучи» пока не нашла подтверждённой информации.",
            (),
            "",
        ),
        (
            "current-order-and-missing-fact",
            _plan(goal="compare_current", comparison_option_names=("Первый", "Второй")),
            EvidenceResult(
                facts=(CanonicalCard("Первый", {"metro": "А"}), CanonicalCard("Второй", {"metro": "Б"})),
                missing_facts=("finishing",),
            ),
            "Сравню только текущие варианты по подтверждённым данным.",
            ("Первый", "Второй"),
            "Пока не подтверждены: отделка.",
        ),
    ),
)
def test_v3_overlap_diff_fixtures_are_closed_and_deterministic(
    name: str,
    plan: IntentPlanV3,
    evidence: EvidenceResult,
    expected_intro: str,
    expected_cards: tuple[str, ...],
    expected_missing: str,
) -> None:
    result = render_v3_response(plan, evidence, _presentation(tuple((*evidence.facts, *evidence.near))))

    assert result.ok is True, name
    assert result.response.intro == expected_intro
    assert tuple(card.name for card in result.response.cards) == expected_cards
    assert result.response.missing_note == expected_missing
    assert result.response.public_text.count("?") == 1


@pytest.mark.parametrize("unsafe", ("MCP payload", "+7 999 123-45-67", "agent@example.test"))
def test_v3_overlap_diff_fixtures_fail_closed_for_internal_or_pii(unsafe: str) -> None:
    card = CanonicalCard("Лучи", {"metro": unsafe})
    result = render_v3_response(_plan(), EvidenceResult(facts=(card,)), _presentation((card,)))

    assert result.ok is False
    assert result.errors == ("unsafe_evidence",)
    assert result.response.cards == ()
    assert result.response.public_text.count("?") == 1
