from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nmbot_v3.contracts import V3ContractError
from nmbot_v3.presentation import (
    V3PresentationCard,
    V3WriterBriefInput,
    V3WriterOutput,
    build_v3_writer_brief,
    validate_v3_writer_output,
)


def _brief(*, cta: str | None = "Какой ЖК показать подробнее?"):
    return build_v3_writer_brief(V3WriterBriefInput(
        client_request="Покажите варианты рядом с метро",
        answer_goal="present_search_results",
        cards=(
            V3PresentationCard("ЖК Первый", {"metro": "Рядом с метро"}),
            V3PresentationCard("ЖК Второй", {"price": "от 12 млн ₽"}),
        ),
        mandatory_cta=cta,
    ))


def _output(**overrides):
    value = {
        "intro": "Нашла два варианта для сравнения.",
        "cards": (
            {"name": "ЖК Первый", "text": "Можно начать с маршрута до метро."},
            {"name": "ЖК Второй", "text": "Здесь подтверждён стартовый бюджет."},
        ),
        "recommendation": "",
        "missing_note": "",
        "final_question": "Какой ЖК показать подробнее?",
    }
    value.update(overrides)
    return V3WriterOutput(**value)


def test_v3_writer_brief_is_built_only_from_v3_input_and_preserves_order():
    brief = _brief()
    assert brief.card_names_in_order == ("ЖК Первый", "ЖК Второй")
    assert brief.cards[0].confirmed_facts["metro"] == "Рядом с метро"
    with pytest.raises(TypeError, match="V3WriterBriefInput"):
        build_v3_writer_brief(object())
    with pytest.raises(TypeError):
        brief.cards[0].confirmed_facts["metro"] = "другое"


def test_v3_writer_output_accepts_exact_cards_and_mandatory_cta():
    assert validate_v3_writer_output(_output(), _brief()) == ()


@pytest.mark.parametrize(
    ("output", "error"),
    [
        (_output(cards=(_output().cards[1], _output().cards[0])), "card_order_or_names_mismatch"),
        (_output(cards=(_output().cards[0],)), "card_order_or_names_mismatch"),
        (_output(final_question="Что ещё показать?"), "mandatory_cta_mismatch"),
        (_output(intro="Что важно? Нашла варианты."), "question_outside_final_question"),
        (_output(final_question="Какой ЖК показать подробнее??"), "final_question_count_not_one"),
        (_output(missing_note="Данные пришли из MCP."), "internal_term_leak"),
    ],
)
def test_v3_writer_validator_rejects_mechanical_contract_violations(output, error):
    assert error in validate_v3_writer_output(output, _brief())


def test_v3_writer_card_limit_and_closed_output_schema():
    brief = build_v3_writer_brief(V3WriterBriefInput(
        client_request="Подберите варианты", answer_goal="present_search_results",
        cards=(), mandatory_cta=None,
    ))
    output = V3WriterOutput(
        intro="Варианты готовы.",
        cards=tuple({"name": f"ЖК {number}", "text": "Описание."} for number in range(4)),
        final_question="Что посмотреть?",
    )
    assert "too_many_cards" in validate_v3_writer_output(output, brief)
    with pytest.raises(V3ContractError, match="unknown_field"):
        V3WriterOutput.from_dict({"intro": "", "cards": [], "recommendation": "", "missing_note": "", "final_question": "Что дальше?", "raw_payload": {}})


def test_v3_presentation_import_closure_and_prompt_are_local_only():
    source_path = Path("nmbot_v3/presentation.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    from_imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = ("nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4", "scripts", "prompts")
    assert not any(name.startswith(banned) for name in imports + from_imports)
    prompt = Path("nmbot_v3/prompts/answer_writer.txt").read_text(encoding="utf-8")
    assert "V3WriterBrief" in prompt and "V3WriterOutput" in prompt
