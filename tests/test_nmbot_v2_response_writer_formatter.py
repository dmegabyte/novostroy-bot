import asyncio
import json

from nmbot_v2.contracts import OptionCard, ResponseBrief
from nmbot_v2.response_composer import (
    assemble_formatted_response,
    build_v3_answer_brief_payload,
    compose_response_writer_formatter_async,
    formatter_request_payload,
    v3_answer_writer_request_payload,
    validate_formatter_response,
    validate_v3_formatter_response,
    writer_request_payload,
)
from nmbot_v2.runtime import _runtime_response_composer_meta
from scripts import nmbot_runtime_adapter as adapter


def _brief() -> ResponseBrief:
    return ResponseBrief(
        answer_goal="present_search_results",
        canonical_cards=(OptionCard(name="ЖК «Первый»", location="Центр", price_min=12000000), OptionCard(name="ЖК «Второй»", location="Парк", price_min=13000000)),
        scenario_context={"content_source": "scenario_context_only", "cards": [{"name": "ЖК «Первый»"}, {"name": "ЖК «Второй»"}]},
        cta_template="Какой вариант хотите рассмотреть подробнее?",
        fallback_question="Какой вариант хотите рассмотреть подробнее?",
    )


def test_writer_payload_plain_text_instruction_and_safe_brief():
    payload = writer_request_payload(_brief())
    assert payload["model"] == "google/gemini-2.5-flash"
    assert payload["parameters"]["temperature"] == 0.25
    assert "Верни только компактный JSON" in payload["query"]
    assert "scenario_context" in payload["query"]
    assert "external_api_key" not in payload


def test_v2_writer_payload_shape_stays_v2_response_brief():
    payload = writer_request_payload(_brief())
    assert payload["_payload_stage"] == "conversation_answer_writer"
    assert payload["query"].startswith("V2_RESPONSE_BRIEF=")
    assert "V3_ANSWER_BRIEF" not in payload["query"]
    assert "prompts/v3_answer_writer" not in payload["system_prompt"]


def test_v3_answer_brief_has_readable_safe_projection_without_contact_or_raw_mcp():
    brief = ResponseBrief(
        answer_goal="present_search_results",
        user_question="Нужна двушка до 18000000, телефон +7 999 123-45-67",
        state_delta_summary=("нужны двухкомнатные", "бюджет до 18 млн"),
        canonical_cards=(OptionCard(name="ЖК «Первый»", rooms=2, location="Центр", price_min=12_000_000, metro="Парк", infrastructure=("школа", "сад")),),
        allowed_fact_fields=("name", "rooms", "location", "price_min", "metro"),
        allowed_claims=("Можно объяснять только подтверждённый факт.",),
        recent_safe_context=({"user": "мой email test@example.com", "assistant": "Показывала варианты"},),
        scenario_context={"content_source": "scenario_context_only", "raw_mcp_payload": {"secret": "x"}, "evidence": {"phone": "+7999", "location": "Центр"}},
        cta_template="Какой вариант хотите рассмотреть подробнее?",
        fallback_question="Какой вариант хотите рассмотреть подробнее?",
    )
    data = build_v3_answer_brief_payload(brief)
    dumped = json.dumps(data, ensure_ascii=False)
    assert data["current_client_request"].endswith("[контакт скрыт]")
    assert "двухкомнатные" in dumped
    assert "18 млн" in dumped
    assert data["canonical_found_cards"] == [{"name": "ЖК «Первый»", "facts": {"location": "Центр", "metro": "Парк", "price_min": "от 12 млн ₽", "rooms": "двухкомнатная квартира", "infrastructure": ["школа", "сад"]}}]
    assert "raw_mcp_payload" not in dumped
    assert "secret" not in dumped
    assert "+7999" not in dumped
    assert "test@example.com" not in dumped


def test_v3_writer_payload_uses_v3_prompt_and_answer_brief():
    payload = v3_answer_writer_request_payload(_brief())
    assert payload["_payload_stage"] == "conversation_answer_writer"
    assert payload["query"].startswith("V3_ANSWER_BRIEF=")
    assert "V2_RESPONSE_BRIEF" not in payload["query"]
    assert "V3_ANSWER_BRIEF" in payload["system_prompt"]
    assert payload["parameters"] == {"temperature": 0.2, "max_tokens": 5000}
    brief_payload = json.loads(payload["query"].split("V3_ANSWER_BRIEF=", 1)[1].split("\n", 1)[0])["answer_brief"]
    assert brief_payload["hard_rules"]["exact_cta_required"] is False


def test_v3_writer_prompt_pins_literal_facts_and_grounded_practical_benefit():
    prompt = v3_answer_writer_request_payload(_brief())["system_prompt"]

    assert "Сравнения и выводы можно формулировать только так" in prompt
    assert "Стартовая цена ЖК" in prompt
    assert "Не округляй" in prompt
    assert "не добавляй неавторизованную выгоду" in prompt


def test_formatter_payload_model_temp_schema_and_no_raw_state(monkeypatch):
    monkeypatch.setenv("NMBOT_RESPONSE_FORMATTER_MODEL", "custom/formatter")
    payload = formatter_request_payload("текст", _brief())
    assert payload["model"] == "custom/formatter"
    assert payload["parameters"]["temperature"] == 0
    assert '"cards"' in payload["query"]
    assert "visible_options" not in payload["query"]
    assert "params" not in payload["query"]


def test_valid_gemini_json_one_call_trace_and_duplicate_heading_stripped():
    calls = []

    async def writer(brief, *, model):
        calls.append(("writer", model))
        return json.dumps(
            {
                "intro": "Нашла два варианта.",
                "cards": [
                    {"name": "ЖК «Первый»", "text": "ЖК «Первый»: Центр, от 12 млн."},
                    {"name": "ЖК «Второй»", "text": "Парк, от 13 млн."},
                ],
                "recommendation": "",
                "missing_note": "",
                "final_question": "Какой вариант хотите рассмотреть подробнее?",
            },
            ensure_ascii=False,
        ), {"ok": True}

    async def formatter(brief, *, writer_text, model):
        calls.append(("formatter", model))
        return json.dumps(
            {
                "intro": "Нашла два варианта.",
                "cards": [
                    {"name": "ЖК «Первый»", "text": "ЖК «Первый»: Центр, от 12 млн."},
                    {"name": "ЖК «Второй»", "text": "Парк, от 13 млн."},
                ],
                "recommendation": "",
                "missing_note": "",
                "final_question": "Какой вариант хотите рассмотреть подробнее?",
            },
            ensure_ascii=False,
        ), {"ok": True}

    result = asyncio.run(compose_response_writer_formatter_async(_brief(), fallback_text="fallback", writer=writer, formatter=formatter))
    assert calls == [("writer", "google/gemini-2.5-flash")]
    assert result.status == "primary"
    assert result.attempts == 1
    assert "1. ЖК «Первый»\nЦентр" in result.text
    meta = result.to_meta()
    assert meta["pipeline"] == "gemini_json_with_formatter_fallback"
    assert meta["attempts"] == 1
    assert "Нашла" not in json.dumps(meta, ensure_ascii=False)


def test_fenced_valid_gemini_json_accepted_without_formatter():
    calls = []

    async def writer(brief, *, model):
        calls.append("writer")
        return "```json\n" + json.dumps(
            {
                "intro": "Нашла два варианта.",
                "cards": [{"name": "ЖК «Первый»", "text": "Центр, от 12 млн."}, {"name": "ЖК «Второй»", "text": "Парк, от 13 млн."}],
                "recommendation": "",
                "missing_note": "",
                "final_question": "Какой вариант хотите рассмотреть подробнее?",
            },
            ensure_ascii=False,
        ) + "\n```", {"ok": True}

    async def formatter(brief, *, writer_text, model):
        calls.append("formatter")
        return "{}", {"ok": True}

    result = asyncio.run(compose_response_writer_formatter_async(_brief(), fallback_text="fallback", writer=writer, formatter=formatter))
    assert result.status == "primary"
    assert result.attempts == 1
    assert calls == ["writer"]


def test_empty_writer_fallback_without_formatter_call():
    calls = []

    async def writer(brief, *, model):
        calls.append("writer")
        return "", {"ok": True}

    async def formatter(brief, *, writer_text, model):
        calls.append("formatter")
        return "{}", {"ok": True}

    result = asyncio.run(compose_response_writer_formatter_async(_brief(), fallback_text="fallback", writer=writer, formatter=formatter))
    assert result.text == "fallback"
    assert result.error_code == "writer_empty"
    assert calls == ["writer"]


def test_malformed_formatter_fallback_and_name_order_cta_enforced():
    async def writer(brief, *, model):
        return '{"intro":"Нашла.","cards":[{"name":"ЖК «Первый»","text":"Центр, от 12 млн."},{"name":"ЖК «Второй»","text":"Парк, от 13 млн."}],"recommendation":"","missing_note":"","final_question":"Какой вариант хотите рассмотреть подробнее?"', {"ok": True}

    async def formatter(brief, *, writer_text, model):
        return '{"intro":"x","cards":[{"name":"ЖК «Второй»","text":"x"}],"recommendation":"","missing_note":"","final_question":"Другой вопрос?"}', {"ok": True}

    result = asyncio.run(compose_response_writer_formatter_async(_brief(), fallback_text="fallback", writer=writer, formatter=formatter))
    assert result.text == "fallback"
    assert "formatter_card_count_mismatch" in result.errors
    assert "formatter_card_order_mismatch" in result.errors
    assert "recipe_cta_mismatch" in result.errors


def test_malformed_but_content_complete_gemini_json_uses_one_ling_call():
    calls = []
    raw = '{"intro":"Нашла два варианта.","cards":[{"name":"ЖК «Первый»","text":"Центр, от 12 млн."},{"name":"ЖК «Второй" ,"text":"Парк, от 13 млн."}],"recommendation":"","missing_note":"","final_question":"Какой вариант хотите рассмотреть подробнее?"'

    async def writer(brief, *, model):
        calls.append("writer")
        return raw, {"ok": True}

    async def formatter(brief, *, writer_text, model):
        calls.append("formatter")
        assert writer_text == raw
        return json.dumps(
            {
                "intro": "Нашла два варианта.",
                "cards": [{"name": "ЖК «Первый»", "text": "Центр, от 12 млн."}, {"name": "ЖК «Второй»", "text": "Парк, от 13 млн."}],
                "recommendation": "",
                "missing_note": "",
                "final_question": "Какой вариант хотите рассмотреть подробнее?",
            },
            ensure_ascii=False,
        ), {"ok": True}

    result = asyncio.run(compose_response_writer_formatter_async(_brief(), fallback_text="fallback", writer=writer, formatter=formatter))
    assert calls == ["writer", "formatter"]
    assert result.status == "repaired"
    assert result.attempts == 2
    assert "1. ЖК «Первый»" in result.text


def test_transport_gemini_failure_no_ling_deterministic_fallback():
    calls = []

    async def writer(brief, *, model):
        calls.append("writer")
        return "", {"ok": False, "error_code": "gateway_not_ok"}

    async def formatter(brief, *, writer_text, model):
        calls.append("formatter")
        return "{}", {"ok": True}

    result = asyncio.run(compose_response_writer_formatter_async(_brief(), fallback_text="fallback", writer=writer, formatter=formatter))
    assert result.text == "fallback"
    assert result.attempts == 1
    assert calls == ["writer"]


def test_invalid_v3_writer_result_keeps_deterministic_fallback():
    async def writer(brief, *, model):
        return json.dumps({"intro": "Нашла.", "cards": [{"name": "ЖК «Второй»", "text": "Парк."}], "recommendation": "", "missing_note": "", "final_question": "Другой вопрос?"}, ensure_ascii=False), {"ok": True}

    async def formatter(brief, *, writer_text, model):
        raise AssertionError("invalid complete writer content must not be repaired when card/order/cta are unsafe")

    result = asyncio.run(compose_response_writer_formatter_async(_brief(), fallback_text="fallback", writer=writer, formatter=formatter))
    assert result.text == "fallback"
    assert result.error_code == "formatter_card_count_mismatch"


def test_v3_valid_grounded_prose_without_prior_recipe_cta_can_publish():
    brief = ResponseBrief(
        answer_goal="present_search_results",
        canonical_cards=(OptionCard(name="ЖК «Первый»", location="Центр", price_min=12_000_000),),
        scenario_context={"content_source": "scenario_context_only"},
        recipe_cards=({"card_name": "ЖК «Другой»", "anchor_fact": "metro", "card_mode": "bounded"},),
        cta_template="Какой вариант хотите рассмотреть подробнее?",
        fallback_question="Какой вариант хотите рассмотреть подробнее?",
    )
    raw = json.dumps(
        {
            "intro": "Нашла один подходящий вариант.",
            "cards": [{"name": "ЖК «Первый»", "text": "Локация — Центр, цена от 12 млн рублей. Можно начать сравнение с бюджета и расположения."}],
            "recommendation": "",
            "missing_note": "",
            "final_question": "Показать подробности по этому ЖК?",
        },
        ensure_ascii=False,
    )

    async def writer(_brief, *, model):
        return raw, {"ok": True}

    async def formatter(_brief, *, writer_text, model):
        raise AssertionError("valid v3 JSON should publish without formatter")

    result = asyncio.run(compose_response_writer_formatter_async(brief, fallback_text="fallback", writer=writer, formatter=formatter, validation_mode="v3"))
    assert result.status == "primary"
    assert result.text.endswith("Показать подробности по этому ЖК?")
    assert "Эти факты помогают спокойно сравнить" not in result.text


def test_v2_formatter_validation_outcome_keeps_recipe_cta_strict_but_v3_allows_free_question():
    brief = _brief()
    formatted = {
        "intro": "Нашла два варианта.",
        "cards": [{"name": "ЖК «Первый»", "text": "Центр, цена от 12 млн рублей."}, {"name": "ЖК «Второй»", "text": "Парк, цена от 13 млн рублей."}],
        "recommendation": "",
        "missing_note": "",
        "final_question": "С какого варианта начнём?",
    }
    assert "recipe_cta_mismatch" in validate_formatter_response(formatted, brief, writer_text="")
    assert "recipe_cta_mismatch" not in validate_v3_formatter_response(formatted, brief, writer_text="")


def test_v3_hard_safety_rejects_bad_number_extra_question_and_operator_template():
    brief = ResponseBrief(
        answer_goal="answer_selected",
        canonical_cards=(OptionCard(name="ЖК «Первый»", location="Центр", price_min=12_000_000),),
        missing_facts=("mortgage_terms",),
        response_policy="operator_consent_offer",
        operator_handoff_template="Точные условия лучше проверит менеджер.",
        cta_template="Передать оператору запрос?",
        fallback_question="Передать оператору запрос?",
    )
    formatted = {
        "intro": "Нашла вариант?",
        "cards": [{"name": "ЖК «Первый»", "text": "Центр, цена от 99 млн рублей. Комфортная жизнь."}],
        "recommendation": "",
        "missing_note": "Уточню позже.",
        "final_question": "Оставьте номер телефона?",
    }
    errors = validate_v3_formatter_response(formatted, brief, writer_text="")
    assert "unknown_number_or_sensitive_claim" in errors
    assert any(error.startswith("unsupported_marketing_claim:") for error in errors)
    assert "section_question_mark" in errors
    assert "operator_handoff_template_mismatch" in errors
    assert "recipe_cta_mismatch" in errors


def test_v3_invalid_operator_template_falls_back():
    brief = ResponseBrief(
        answer_goal="answer_selected",
        canonical_cards=(OptionCard(name="ЖК «Первый»", location="Центр", price_min=12_000_000),),
        missing_facts=("mortgage_terms",),
        response_policy="operator_consent_offer",
        operator_handoff_template="Точные условия лучше проверит менеджер.",
        cta_template="Передать оператору запрос?",
        fallback_question="Передать оператору запрос?",
    )

    async def writer(_brief, *, model):
        return json.dumps({"intro": "Нашла.", "cards": [{"name": "ЖК «Первый»", "text": "Центр, цена от 12 млн рублей."}], "recommendation": "", "missing_note": "", "final_question": "Передать оператору запрос?"}, ensure_ascii=False), {"ok": True}

    async def formatter(_brief, *, writer_text, model):
        return writer_text, {"ok": True}

    result = asyncio.run(compose_response_writer_formatter_async(brief, fallback_text="fallback", writer=writer, formatter=formatter, validation_mode="v3"))
    assert result.text == "fallback"
    assert result.error_code in {"missing_note_required", "validation_failed"}
    assert "missing_note_required" in result.errors


def test_formatter_numeric_and_project_name_introduction_rejected():
    formatted = {
        "intro": "Нашла варианты.",
        "cards": [{"name": "ЖК «Первый»", "text": "Цена 99 млн рядом с ЖК «Чужой»."}, {"name": "ЖК «Второй»", "text": "Парк."}],
        "recommendation": "",
        "missing_note": "",
        "final_question": "Какой вариант хотите рассмотреть подробнее?",
    }
    errors = validate_formatter_response(formatted, _brief(), writer_text="Нашла варианты. 12 млн. 13 млн. Какой вариант хотите рассмотреть подробнее?")
    assert "unknown_number_or_sensitive_claim" in errors
    assert "formatter_project_name_introduced" in errors


def test_v3_semantic_failure_records_content_free_writer_formatter_category():
    async def writer(_brief, *, model):
        return json.dumps(
            {
                "intro": "Нашла вариант.",
                "cards": [{"name": "ЖК «Первый»", "text": "Центр, от 99 млн."}, {"name": "ЖК «Второй»", "text": "Парк, от 13 млн."}],
                "recommendation": "",
                "missing_note": "",
                "final_question": "Какой вариант хотите рассмотреть подробнее?",
            },
            ensure_ascii=False,
        ), {"ok": True}

    async def formatter(_brief, *, writer_text, model):
        return json.dumps(
            {
                "intro": "Нашла вариант.",
                "cards": [{"name": "ЖК «Первый»", "text": "Центр, от 98 млн, доходность гарантирована."}, {"name": "ЖК «Второй»", "text": "Парк, от 13 млн."}],
                "recommendation": "",
                "missing_note": "",
                "final_question": "Какой вариант хотите рассмотреть подробнее?",
            },
            ensure_ascii=False,
        ), {"ok": True}

    result = asyncio.run(compose_response_writer_formatter_async(_brief(), fallback_text="fallback", writer=writer, formatter=formatter, validation_mode="v3"))

    assert result.status == "fallback"
    assert result.to_meta()["semantic_diagnostics"] == [
        {"stage": "formatter", "categories": ["numeric_price_not_in_canonical", "sensitive_claim"]}
    ]
    dumped = json.dumps(result.to_meta(), ensure_ascii=False)
    for forbidden in ("99", "98", "доходность", "гарантирована"):
        assert forbidden not in dumped


def test_v3_semantic_numeric_categories_are_local_context_only():
    async def writer(_brief, *, model):
        return json.dumps(
            {
                "intro": "Нашла варианты.",
                "cards": [{"name": "ЖК «Первый»", "text": "Центр, от 12 млн, до метро 16 минут."}, {"name": "ЖК «Второй»", "text": "Парк, от 13 млн."}],
                "recommendation": "",
                "missing_note": "",
                "final_question": "Какой вариант хотите рассмотреть подробнее?",
            },
            ensure_ascii=False,
        ), {"ok": True}

    async def formatter(_brief, *, writer_text, model):
        return json.dumps(
            {
                "intro": "Нашла варианты.",
                "cards": [{"name": "ЖК «Первый»", "text": "Центр, от 99 млн, до метро 17 минут."}, {"name": "ЖК «Второй»", "text": "Парк, от 13 млн."}],
                "recommendation": "",
                "missing_note": "",
                "final_question": "Какой вариант хотите рассмотреть подробнее?",
            },
            ensure_ascii=False,
        ), {"ok": True}

    result = asyncio.run(compose_response_writer_formatter_async(_brief(), fallback_text="fallback", writer=writer, formatter=formatter, validation_mode="v3"))

    assert result.to_meta()["semantic_diagnostics"] == [{"stage": "formatter", "categories": ["numeric_price_not_in_canonical", "numeric_transit_not_in_canonical"]}]
    dumped = json.dumps(result.to_meta(), ensure_ascii=False)
    for forbidden in ("99", "17", "метро", "Центр", "position", "span"):
        assert forbidden not in dumped


def test_runtime_semantic_diagnostics_keep_only_allowlisted_categories():
    meta = _runtime_response_composer_meta(
        {
            "used": False,
            "status": "fallback",
            "reason": "validation_failed",
            "error_category": "semantic",
            "semantic_diagnostics": [
                {"stage": "formatter", "categories": ["numeric_price_not_in_canonical", "numeric_transit_not_in_canonical", "raw secret"], "matched_text": "99 млн", "position": 1},
                {"stage": "other", "categories": ["numeric_area_not_in_canonical"]},
            ],
        },
        mode="shadow",
        published=False,
        elapsed_ms=1,
    )

    assert meta["semantic_diagnostics"] == [{"stage": "formatter", "categories": ["numeric_price_not_in_canonical", "numeric_transit_not_in_canonical"]}]
    dumped = json.dumps(meta, ensure_ascii=False)
    for forbidden in ("secret", "99", "position", "matched_text"):
        assert forbidden not in dumped


def test_formatter_content_preservation_allows_only_formatting_numbering_and_heading_strip():
    writer_text = "Нашла два варианта.\n\nЖК «Первый»: Центр, от 12 млн. Маршрут простой.\n\n2. ЖК «Второй» — Парк, от 13 млн. Рядом зелёная зона.\n\nКакой вариант хотите рассмотреть подробнее?"
    formatted = {
        "intro": "Нашла два варианта.",
        "cards": [
            {"name": "ЖК «Первый»", "text": "ЖК «Первый»: Центр, от 12 млн. Маршрут простой."},
            {"name": "ЖК «Второй»", "text": "Парк, от 13 млн. Рядом зелёная зона."},
        ],
        "recommendation": "",
        "missing_note": "",
        "final_question": "Какой вариант хотите рассмотреть подробнее?",
    }
    assert validate_formatter_response(formatted, _brief(), writer_text=writer_text) == []


def test_formatter_content_preservation_rejects_omitted_sentence():
    writer_text = "Нашла два варианта.\n\nЖК «Первый»: Центр, от 12 млн. Маршрут простой.\n\nЖК «Второй»: Парк, от 13 млн. Рядом зелёная зона.\n\nКакой вариант хотите рассмотреть подробнее?"
    formatted = {
        "intro": "Нашла два варианта.",
        "cards": [
            {"name": "ЖК «Первый»", "text": "Центр, от 12 млн."},
            {"name": "ЖК «Второй»", "text": "Парк, от 13 млн. Рядом зелёная зона."},
        ],
        "recommendation": "",
        "missing_note": "",
        "final_question": "Какой вариант хотите рассмотреть подробнее?",
    }
    assert "formatter_content_mismatch" in validate_formatter_response(formatted, _brief(), writer_text=writer_text)


def test_formatter_content_preservation_rejects_paraphrase():
    writer_text = "Нашла два варианта.\n\nЖК «Первый»: Центр, от 12 млн. Маршрут простой.\n\nЖК «Второй»: Парк, от 13 млн. Рядом зелёная зона.\n\nКакой вариант хотите рассмотреть подробнее?"
    formatted = {
        "intro": "Нашла пару вариантов.",
        "cards": [
            {"name": "ЖК «Первый»", "text": "Центр, от 12 млн. Маршрут удобный."},
            {"name": "ЖК «Второй»", "text": "Парк, от 13 млн. Рядом зелёная зона."},
        ],
        "recommendation": "",
        "missing_note": "",
        "final_question": "Какой вариант хотите рассмотреть подробнее?",
    }
    assert "formatter_content_mismatch" in validate_formatter_response(formatted, _brief(), writer_text=writer_text)


def test_formatter_content_preservation_rejects_sentence_reordering():
    writer_text = "Нашла два варианта.\n\nЖК «Первый»: Центр, от 12 млн. Маршрут простой.\n\nЖК «Второй»: Парк, от 13 млн. Рядом зелёная зона.\n\nКакой вариант хотите рассмотреть подробнее?"
    formatted = {
        "intro": "Нашла два варианта.",
        "cards": [
            {"name": "ЖК «Первый»", "text": "Маршрут простой. Центр, от 12 млн."},
            {"name": "ЖК «Второй»", "text": "Парк, от 13 млн. Рядом зелёная зона."},
        ],
        "recommendation": "",
        "missing_note": "",
        "final_question": "Какой вариант хотите рассмотреть подробнее?",
    }
    assert "formatter_content_mismatch" in validate_formatter_response(formatted, _brief(), writer_text=writer_text)


def test_formatter_content_mismatch_is_schema_category():
    async def writer(brief, *, model):
        return "Нашла два варианта. ЖК «Первый»: Центр, от 12 млн. Маршрут простой. ЖК «Второй»: Парк, от 13 млн. Рядом зелёная зона. Какой вариант хотите рассмотреть подробнее?", {"ok": True}

    async def formatter(brief, *, writer_text, model):
        return json.dumps(
            {
                "intro": "Нашла два варианта.",
                "cards": [
                    {"name": "ЖК «Первый»", "text": "Центр, от 12 млн."},
                    {"name": "ЖК «Второй»", "text": "Парк, от 13 млн. Рядом зелёная зона."},
                ],
                "recommendation": "",
                "missing_note": "",
                "final_question": "Какой вариант хотите рассмотреть подробнее?",
            },
            ensure_ascii=False,
        ), {"ok": True}

    result = asyncio.run(compose_response_writer_formatter_async(_brief(), fallback_text="fallback", writer=writer, formatter=formatter))
    assert result.text == "fallback"
    assert result.error_code == "formatter_content_mismatch"
    assert result.error_category == "schema"


def test_v2_v3_gates_are_separate_and_v0_off(monkeypatch):
    monkeypatch.delenv("NMBOT_V2_RESPONSE_COMPOSER_MODE", raising=False)
    monkeypatch.delenv("NMBOT_V3_RESPONSE_COMPOSER_MODE", raising=False)
    assert adapter._runtime_response_composer_mode("v2") == "off"
    assert adapter._runtime_response_composer_mode("v3") == "off"
    assert adapter._runtime_response_composer_mode("v0") == "off"


def test_manager_rewriter_gate_is_v2_v3_only_and_defaults_off(monkeypatch):
    monkeypatch.delenv("NMBOT_MANAGER_REWRITER_MODE", raising=False)
    monkeypatch.delenv("NMBOT_V2_MANAGER_REWRITER_MODE", raising=False)
    monkeypatch.delenv("NMBOT_V3_MANAGER_REWRITER_MODE", raising=False)
    assert adapter._runtime_manager_rewriter_mode("v2") == "off"
    assert adapter._runtime_manager_rewriter_mode("v3") == "off"
    assert adapter._runtime_manager_rewriter_mode("v0") == "off"
    monkeypatch.setenv("NMBOT_MANAGER_REWRITER_MODE", "publish")
    assert adapter._runtime_manager_rewriter_mode("v2") == "off"
    assert adapter._runtime_manager_rewriter_mode("v3") == "off"
    assert adapter._runtime_manager_rewriter_mode("v0") == "off"
    monkeypatch.setenv("NMBOT_V3_MANAGER_REWRITER_MODE", "publish")
    assert adapter._runtime_manager_rewriter_mode("v2") == "off"
    assert adapter._runtime_manager_rewriter_mode("v3") == "publish"
    monkeypatch.setenv("NMBOT_V2_MANAGER_REWRITER_MODE", "shadow")
    monkeypatch.setenv("NMBOT_V3_MANAGER_REWRITER_MODE", "bad")
    assert adapter._runtime_manager_rewriter_mode("v2") == "shadow"
    assert adapter._runtime_manager_rewriter_mode("v3") == "off"
    monkeypatch.setenv("NMBOT_V2_RESPONSE_COMPOSER_MODE", "publish")
    monkeypatch.setenv("NMBOT_V3_RESPONSE_COMPOSER_MODE", "shadow")
    assert adapter._runtime_response_composer_mode("v2") == "publish"
    assert adapter._runtime_response_composer_mode("v3") == "shadow"
    assert adapter._runtime_response_composer_mode("v0") == "off"


def test_assembled_cards_are_blank_line_separated():
    text = assemble_formatted_response(
        {"intro": "Intro", "cards": [{"name": "ЖК «Первый»", "text": "ЖК «Первый» — Body"}], "recommendation": "", "missing_note": "", "final_question": "Финал?"}
    )
    assert text == "Intro\n\n1. ЖК «Первый»\nBody\n\nФинал?"
