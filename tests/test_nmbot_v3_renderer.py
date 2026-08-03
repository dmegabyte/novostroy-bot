from __future__ import annotations

import ast
from pathlib import Path

from nmbot_v3.contracts import IntentPlanV3
from nmbot_v3.evidence_contract import CanonicalCard, EvidenceResult
from nmbot_v3.presentation import V3PresentationCard, V3WriterBriefInput
from nmbot_v3.renderer import render_v3_response


def _plan(**overrides: object) -> IntentPlanV3:
    values: dict[str, object] = {
        "schema_version": 3,
        "goal": "new_search",
        "viewpoint": "life",
    }
    values.update(overrides)
    return IntentPlanV3(**values)


def _presentation(*cards: V3PresentationCard, cta: str | None = "Какой вариант показать подробнее?") -> V3WriterBriefInput:
    return V3WriterBriefInput(
        client_request="Подберите квартиру",
        answer_goal="present_search_results",
        cards=cards,
        mandatory_cta=cta,
    )


def test_renderer_builds_deterministic_normal_public_response() -> None:
    evidence = EvidenceResult(facts=(CanonicalCard("ЖК Лучи", {"metro": "Солнцево", "price_min": "12 млн ₽"}),))
    result = render_v3_response(_plan(), evidence, _presentation(V3PresentationCard("ЖК Лучи", evidence.facts[0].fields)))

    assert result.ok is True
    assert result.response.public_text == (
        "Нашла подтверждённые варианты.\n\n"
        "ЖК Лучи: метро: Солнцево; цена от: 12 млн ₽\n\n"
        "Какой вариант показать подробнее?"
    )


def test_renderer_fails_closed_for_zero_or_invalid_evidence() -> None:
    empty = render_v3_response(_plan(), EvidenceResult(), _presentation())
    mismatch = render_v3_response(
        _plan(), EvidenceResult(facts=(CanonicalCard("ЖК Лучи", {"metro": "Солнцево"}),)), _presentation()
    )

    assert empty.ok is True
    assert empty.response.cards == ()
    assert mismatch.ok is False
    assert mismatch.errors == ("presentation_card_order_or_names_mismatch",)
    assert "mismatch" not in mismatch.response.public_text


def test_renderer_preserves_named_and_current_evidence_card_order() -> None:
    named_card = CanonicalCard("ЖК Лучи", {"ready": "2027"})
    named = render_v3_response(
        _plan(goal="lookup_object", named_object_reference="ЖК Лучи"),
        EvidenceResult(facts=(named_card,)),
        _presentation(V3PresentationCard("ЖК Лучи", named_card.fields)),
    )
    first, second = CanonicalCard("ЖК Первый", {"metro": "А"}), CanonicalCard("ЖК Второй", {"metro": "Б"})
    current = render_v3_response(
        _plan(goal="compare_current", comparison_option_names=("ЖК Первый", "ЖК Второй")),
        EvidenceResult(facts=(first, second)),
        _presentation(V3PresentationCard("ЖК Первый", first.fields), V3PresentationCard("ЖК Второй", second.fields)),
    )

    assert named.ok is True
    assert named.response.intro == "Нашла подтверждённые данные по ЖК «ЖК Лучи»."
    assert tuple(card.name for card in current.response.cards) == ("ЖК Первый", "ЖК Второй")


def test_renderer_rejects_current_or_selected_option_evidence_that_does_not_match_intent() -> None:
    first, second = CanonicalCard("ЖК Первый", {"metro": "А"}), CanonicalCard("ЖК Второй", {"metro": "Б"})
    current = render_v3_response(
        _plan(goal="compare_current", comparison_option_names=("ЖК Второй", "ЖК Первый")),
        EvidenceResult(facts=(first, second)),
        _presentation(V3PresentationCard("ЖК Первый", first.fields), V3PresentationCard("ЖК Второй", second.fields)),
    )
    selected = render_v3_response(
        _plan(goal="answer_selected", selected_option_name="ЖК Второй"),
        EvidenceResult(facts=(first,)),
        _presentation(V3PresentationCard("ЖК Первый", first.fields)),
    )

    assert current.ok is False and current.errors == ("current_option_evidence_mismatch",)
    assert selected.ok is False and selected.errors == ("selected_option_evidence_mismatch",)


def test_renderer_enforces_one_cta_question_and_rejects_internal_or_pii_evidence() -> None:
    safe_card = CanonicalCard("ЖК Лучи", {"metro": "Солнцево"})
    bad_cta = render_v3_response(
        _plan(), EvidenceResult(facts=(safe_card,)),
        _presentation(V3PresentationCard("ЖК Лучи", safe_card.fields), cta="Что важно? Какой вариант выбрать?"),
    )
    pii_card = CanonicalCard("ЖК Лучи", {"metro": "+7 999 123-45-67"})
    pii = render_v3_response(
        _plan(), EvidenceResult(facts=(pii_card,)), _presentation(V3PresentationCard("ЖК Лучи", pii_card.fields))
    )
    internal_card = CanonicalCard("ЖК Лучи", {"metro": "MCP source"})
    internal = render_v3_response(
        _plan(), EvidenceResult(facts=(internal_card,)), _presentation(V3PresentationCard("ЖК Лучи", internal_card.fields))
    )

    assert bad_cta.ok is False and "invalid_renderer_input" in bad_cta.errors
    assert pii.ok is False and pii.errors == ("unsafe_evidence",)
    assert internal.ok is False and internal.errors == ("unsafe_evidence",)
    assert all(result.response.public_text.count("?") == 1 for result in (bad_cta, pii, internal))


def test_renderer_import_closure_excludes_legacy_runtime_and_network_layers() -> None:
    tree = ast.parse(Path("nmbot_v3/renderer.py").read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    from_imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = ("nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4", "requests", "aiohttp", "http", "socket", "gateway", "mcp", "runtime", "service")
    assert not any(name.startswith(banned) for name in imports + from_imports)
