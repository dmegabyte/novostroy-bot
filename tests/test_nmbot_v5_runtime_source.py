from __future__ import annotations

import json

from nmbot_v2.contracts import OptionCard, ResponseBrief
from nmbot_v2.manager_rewriter import (
    _v5_operator_offer_failsafe,
    manager_rewriter_request_payload,
)
from nmbot_v2.runtime import _v5_operator_offer_fallback
from scripts.nmbot_api_server import _is_start_command, _start_command_version
from scripts.nmbot_runtime_adapter import _normalize_runtime_version


def _brief() -> ResponseBrief:
    return ResponseBrief(
        answer_goal="list_options",
        canonical_cards=(
            OptionCard(
                name="ЖК Тестовый",
                location="Люблино",
                room_prices=({"rooms": 2, "price": "10 млн"},),
            ),
        ),
    )


def test_v5_payload_has_structured_operator_policy(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-enter-v5-payload")
    request = manager_rewriter_request_payload(
        transcript=(
            {"user": "двушка в Люблино", "assistant": "нашла вариант"},
            {"user": "до 30 млн", "assistant": "подбираю"},
            {"user": "готовое", "assistant": ""},
        ),
        current_question="готовое",
        prepared_answer="Подготовленный ответ",
        brief=_brief(),
        model="deepseek/test",
        runtime_version="v5",
    )

    assert request["model"] == "deepseek/test"
    assert request["query"].startswith("V5_MANAGER_REWRITER_INPUT=")
    assert "external_api_key" not in request
    payload = json.loads(request["query"].split("=", 1)[1])
    assert payload["operator_policy"]["offer"] is True
    assert payload["operator_policy"]["reason"] == "third_client_question"
    assert payload["mcp_evidence"]["cards"][0]["name"] == "ЖК Тестовый"


def test_v2_payload_marker_and_external_key_remain_unchanged(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "configured-for-v2")
    request = manager_rewriter_request_payload(
        transcript=(),
        current_question="вопрос",
        prepared_answer="ответ",
        brief=_brief(),
        model="google/test",
    )

    assert request["query"].startswith("V2_MANAGER_REWRITER_INPUT=")
    assert request["model"] == "google/test"
    assert request["external_api_key"] == "configured-for-v2"


def test_operator_failsafe_preserves_empty_failure_and_rewrites_non_operator_text() -> None:
    transcript = (
        {"user": "первый", "assistant": "ответ"},
        {"user": "второй", "assistant": "ответ"},
        {"user": "третий", "assistant": ""},
    )
    assert _v5_operator_offer_failsafe("", transcript=transcript, current_question="третий") == ""
    result = _v5_operator_offer_failsafe("Обычный ответ.", transcript=transcript, current_question="готовое")
    assert "менеджер" in result.lower()
    assert result.endswith("?")
    assert _v5_operator_offer_failsafe("Подключу оператора.", transcript=transcript, current_question="готовое") == "Подключу оператора."


def test_runtime_v5_selector_and_start_command() -> None:
    assert _normalize_runtime_version("v5") == "V5"
    assert _normalize_runtime_version("unknown") == "V2"
    assert _is_start_command("/start_5") is True
    assert _start_command_version("/start_5") == "V5"


def test_timeout_fallback_offer_is_contextual_and_single_cta() -> None:
    text = _v5_operator_offer_fallback("готовое жильё")
    assert "менеджер" in text.lower()
    assert text.count("?") == 1
