from __future__ import annotations

from pathlib import Path

from nmbot_v0.answer_writer import MAX_CANDIDATE_CHARS, build_assignment, candidate_from_raw, fixed_output_from_answer, normalize_fixed_output_for_response_job, validate_candidate_against_assignment
from nmbot_v0.contracts import V0Answer


ROOT = Path(__file__).resolve().parents[1]


def _answer() -> V0Answer:
    return V0Answer(
        answer_kind="search_many",
        scope="shortlist",
        intro="Нашла два варианта.",
        options=(
            {"lines": ("1. ЖК «Первый»: Москва, цены от 10 млн рублей.",)},
            {"lines": ("2. ЖК «Второй»: Москва, цены от 11 млн рублей.",)},
        ),
        recommendation="Можно сравнить по цене.",
        missing_note="По отделке не всё известно.",
        final_question="Какой вариант разобрать подробнее?",
    )


def test_assignment_has_only_compact_expected_fields_and_bounded_text() -> None:
    fixed = fixed_output_from_answer(_answer(), deterministic_text="детерминированный черновик")

    assignment = build_assignment(
        client_message="к" * 2500,
        previous_assistant_message="п" * 2500,
        response_job={"answer_kind": "search_many", "scope": "shortlist", "decision_action": "answer"},
        fixed_output=fixed,
    )

    assert set(assignment) == {"client_message", "previous_assistant_message", "response_job", "material"}
    assert len(assignment["client_message"]) == 2000
    assert len(assignment["previous_assistant_message"]) == 2000
    assert assignment["material"] == {
        "intro": "Нашла два варианта.",
        "card_lines": ["1. ЖК «Первый»: Москва, цены от 10 млн рублей.", "2. ЖК «Второй»: Москва, цены от 11 млн рублей."],
        "recommendation": "Можно сравнить по цене.",
        "missing_note": "По отделке не всё известно.",
        "final_question": "Какой вариант разобрать подробнее?",
    }
    assert "deterministic_draft" not in assignment["material"]
    assert fixed.deterministic_text == "детерминированный черновик"


def test_plain_nonempty_candidate_accepted_exactly() -> None:
    text = "Поняла, собрала варианты живым текстом. Какой посмотрим подробнее?"

    candidate = candidate_from_raw(text)

    assert candidate.validation.ok is True
    assert candidate.text == text


def test_empty_whitespace_and_too_long_candidate_rejected() -> None:
    empty = candidate_from_raw("   \n  ")
    too_long = candidate_from_raw("а" * (MAX_CANDIDATE_CHARS + 1))

    assert empty.validation.ok is False
    assert empty.validation.errors == ("empty_candidate",)
    assert too_long.validation.ok is False
    assert too_long.validation.errors == ("candidate_too_long",)


def test_scope_normalization_filters_no_cards_one_card_and_shortlist_fail_closed() -> None:
    fixed = fixed_output_from_answer(_answer())
    no_cards, no_errors = normalize_fixed_output_for_response_job(fixed, {"answer_kind": "off_topic", "scope": "no_cards"})
    shortlist, short_errors = normalize_fixed_output_for_response_job(fixed, {"answer_kind": "search_many", "scope": "shortlist"})
    selected, selected_errors = normalize_fixed_output_for_response_job(fixed, {"answer_kind": "selected_object", "scope": "one_card", "selected_option_name": "ЖК Первый"})
    failed, failed_errors = normalize_fixed_output_for_response_job(fixed, {"answer_kind": "selected_object", "scope": "one_card", "selected_option_name": "ЖК Третий"})

    assert no_cards.card_lines == ()
    assert no_errors == ()
    assert shortlist.card_lines == fixed.card_lines[:3]
    assert short_errors == ()
    assert selected.card_lines == ("1. ЖК «Первый»: Москва, цены от 10 млн рублей.",)
    assert selected_errors == ()
    assert failed.card_lines == ()
    assert failed_errors == ("one_card_selection_failed_closed",)


def test_one_card_normalization_preserves_full_selected_option_group_without_stale_option_lines() -> None:
    answer = V0Answer(
        answer_kind="selected_object",
        scope="one_card",
        intro="По выбранному варианту.",
        options=(
            {"lines": ("1. ЖК «Первый» — корпус 1.", "Срок сдачи — четвертый квартал, цены от 10 млн рублей.")},
            {"lines": ("2. ЖК «Второй» — старая карточка.", "Срок сдачи — второй квартал, цены от 14 млн рублей.")},
        ),
        final_question="Проверить актуальные квартиры в этом ЖК?",
    )
    fixed = fixed_output_from_answer(answer)

    selected, errors = normalize_fixed_output_for_response_job(
        fixed,
        {"answer_kind": "selected_object", "scope": "one_card", "selected_option_name": "ЖК Первый"},
    )

    assert errors == ()
    assert selected.card_lines == (
        "1. ЖК «Первый» — корпус 1.",
        "Срок сдачи — четвертый квартал, цены от 10 млн рублей.",
    )


def test_candidate_contract_rejects_disallowed_cards_extra_questions_and_cta_substitution() -> None:
    fixed = fixed_output_from_answer(_answer())
    normalized, _ = normalize_fixed_output_for_response_job(fixed, {"answer_kind": "selected_object", "scope": "one_card", "selected_option_name": "ЖК Первый"})
    assignment = build_assignment(
        client_message="первый",
        previous_assistant_message="",
        response_job={"answer_kind": "selected_object", "scope": "one_card", "selected_option_name": "ЖК Первый"},
        fixed_output=normalized,
    )

    ok = validate_candidate_against_assignment("1. ЖК «Первый»: Москва, цены от 10 млн рублей. Какой вариант разобрать подробнее?", assignment, original_card_lines=fixed.card_lines)
    bad_card = validate_candidate_against_assignment("ЖК Первый нормальный, а ЖК Второй дешевле. Какой вариант разобрать подробнее?", assignment, original_card_lines=fixed.card_lines)
    bad_questions = validate_candidate_against_assignment("ЖК Первый. Что по цене? Что по срокам?", assignment, original_card_lines=fixed.card_lines)
    bad_cta = validate_candidate_against_assignment("ЖК Первый. Что разберём первым?", assignment, original_card_lines=fixed.card_lines)

    assert ok.ok is True
    assert "candidate_mentions_disallowed_card" in bad_card.errors
    assert "candidate_too_many_questions" in bad_questions.errors
    assert "candidate_cta_mismatch" in bad_cta.errors


def test_candidate_contract_requires_all_shortlist_card_lines_verbatim_in_order() -> None:
    fixed = fixed_output_from_answer(_answer())
    normalized, _ = normalize_fixed_output_for_response_job(fixed, {"answer_kind": "search_many", "scope": "shortlist"})
    assignment = build_assignment(
        client_message="подбери",
        previous_assistant_message="",
        response_job={"answer_kind": "search_many", "scope": "shortlist"},
        fixed_output=normalized,
    )

    valid = validate_candidate_against_assignment(
        "Вот что есть:\n"
        "1. ЖК «Первый»: Москва, цены от 10 млн рублей.\n"
        "2. ЖК «Второй»: Москва, цены от 11 млн рублей.\n"
        "Какой вариант разобрать подробнее?",
        assignment,
        original_card_lines=fixed.card_lines,
    )
    omitted = validate_candidate_against_assignment(
        "Вот что есть:\n1. ЖК «Первый»: Москва, цены от 10 млн рублей.\nКакой вариант разобрать подробнее?",
        assignment,
        original_card_lines=fixed.card_lines,
    )
    mutated = validate_candidate_against_assignment(
        "Вот что есть:\n"
        "1. ЖК «Первый»: Москва, цена от 10 млн рублей.\n"
        "2. ЖК «Второй»: Москва, цены от 11 млн рублей.\n"
        "Какой вариант разобрать подробнее?",
        assignment,
        original_card_lines=fixed.card_lines,
    )
    reordered = validate_candidate_against_assignment(
        "Вот что есть:\n"
        "2. ЖК «Второй»: Москва, цены от 11 млн рублей.\n"
        "1. ЖК «Первый»: Москва, цены от 10 млн рублей.\n"
        "Какой вариант разобрать подробнее?",
        assignment,
        original_card_lines=fixed.card_lines,
    )

    assert valid.ok is True
    assert "candidate_omits_required_card_line" in omitted.errors
    assert "candidate_omits_required_card_line" in mutated.errors
    assert "candidate_reorders_required_card_lines" in reordered.errors


def test_candidate_contract_allows_one_card_group_with_multiple_supporting_lines_when_retained_exactly() -> None:
    answer = V0Answer(
        answer_kind="selected_object",
        scope="one_card",
        intro="По выбранному варианту.",
        options=(
            {"lines": ("1. ЖК «Первый» — корпус 1.", "Срок сдачи — четвертый квартал, цены от 10 млн рублей.")},
            {"lines": ("2. ЖК «Второй» — корпус 2.",)},
        ),
        final_question="Проверить актуальные квартиры в этом ЖК?",
    )
    fixed = fixed_output_from_answer(answer)
    normalized, errors = normalize_fixed_output_for_response_job(
        fixed,
        {"answer_kind": "selected_object", "scope": "one_card", "selected_option_name": "ЖК Первый"},
    )
    assignment = build_assignment(
        client_message="первый",
        previous_assistant_message="",
        response_job={"answer_kind": "selected_object", "scope": "one_card", "selected_option_name": "ЖК Первый"},
        fixed_output=normalized,
    )

    result = validate_candidate_against_assignment(
        "По нему вижу так:\n"
        "1. ЖК «Первый» — корпус 1.\n"
        "Срок сдачи — четвертый квартал, цены от 10 млн рублей.\n"
        "Проверить актуальные квартиры в этом ЖК?",
        assignment,
        original_card_lines=fixed.card_lines,
    )

    assert errors == ()
    assert result.ok is True


def test_candidate_contract_rejects_one_card_material_with_multiple_card_identities() -> None:
    fixed = fixed_output_from_answer(_answer())
    assignment = build_assignment(
        client_message="первый",
        previous_assistant_message="",
        response_job={"answer_kind": "selected_object", "scope": "one_card", "selected_option_name": "ЖК Первый"},
        fixed_output=fixed,
    )

    result = validate_candidate_against_assignment(
        "1. ЖК «Первый»: Москва, цены от 10 млн рублей.\n"
        "2. ЖК «Второй»: Москва, цены от 11 млн рублей.\n"
        "Какой вариант разобрать подробнее?",
        assignment,
        original_card_lines=fixed.card_lines,
    )

    assert result.ok is False
    assert "one_card_material_has_multiple_cards" in result.errors


def test_candidate_contract_rejects_pure_greeting_no_cards_search_miss_claim() -> None:
    fixed = fixed_output_from_answer(V0Answer(answer_kind="off_topic", scope="no_cards", intro="", final_question="Что ищете?"))
    assignment = build_assignment(
        client_message="Здравствуйте",
        previous_assistant_message="",
        response_job={"answer_kind": "off_topic", "scope": "no_cards"},
        fixed_output=fixed,
    )

    result = validate_candidate_against_assignment("Пока ничего не нашла. Что ищете?", assignment)

    assert result.ok is False
    assert "unsearched_no_cards_search_claim" in result.errors


def test_v0_answer_writer_prompt_static_promptmaster_requirements() -> None:
    prompt = (ROOT / "prompts" / "v0_answer_writer.txt").read_text(encoding="utf-8")
    canonical = (ROOT / "prompts" / "candidates" / "v0_answer_writer_promptmaster_v10.txt").read_text(encoding="utf-8")
    lowered = prompt.lower()

    assert prompt == canonical
    assert "используй только данные из material" in lowered
    assert "каждое утверждение должно быть прямым пересказом" in lowered
    assert "не делай выводов и не добавляй оценок" in lowered
    assert "покажи все переданные card_lines" in lowered
    assert "каждую строку целиком, дословно и отдельной строкой" in lowered
    assert "не склоняй, не пересказывай, не сокращай" in lowered
    assert "для scope=one_card показывай только переданные card_lines" in lowered
    assert "не добавляй другие варианты" in lowered
    assert "для scope=no_cards не упоминай карточки, варианты, цены или результаты поиска" in lowered
    assert "если final_question непустой, заверши ровно одним конкретным вопросом из него" in lowered
    assert "без json" in lowered
    assert "markdown" in lowered
