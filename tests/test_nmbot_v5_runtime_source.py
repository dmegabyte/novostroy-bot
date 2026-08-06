from __future__ import annotations

import json

from nmbot_v2.contracts import OptionCard, ResponseBrief, SafeTurnContext, SearchResult, SemanticPlan
from nmbot_v2.manager_rewriter import (
    _v5_card_payload,
    _v5_operator_offer_failsafe,
    manager_rewriter_request_payload,
)
from nmbot_v2.runtime import TurnProcessor, _v5_operator_offer_fallback
from nmbot_v2.state import ConversationState
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


def test_v5_card_projection_keeps_bounded_area_finishing_and_price() -> None:
    card = OptionCard(
        name="ЖК Полный",
        area="55–60 м²",
        finishing="с отделкой",
        price_min=12_000_000,
        room_prices=({"rooms": 2, "price": "12 млн", "area": "55 м²"},),
    )
    projected = _v5_card_payload(card, rank=1, requested_rooms=2)
    fields = {item["field"]: item["value"] for item in projected["facts"]}
    assert fields["area"] == "55–60 м²"
    assert fields["finishing"] == "с отделкой"
    assert fields["room_prices"][0]["price"] == "12 млн"


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


class _Planner:
    def plan(self, context, state):
        return SemanticPlan(operation="search", intent="life")


class _Search:
    def search(self, plan, state):
        return SearchResult.from_dict({"facts": [{"name": "ЖК Тестовый", "location": "Люблино"}]})


class _V5Rewriter:
    runtime_version = "v5"

    def __init__(self, text: str | None = "Обычный ответ.", fail: bool = False):
        self.text = text
        self.fail = fail

    def rewrite_manager_answer(self, **kwargs):
        if self.fail:
            raise RuntimeError("gateway timeout")
        return self.text


def _third_turn_state() -> ConversationState:
    return ConversationState(
        dialogue_turns=(
            {"user": "двушка в Люблино", "assistant": "нашла"},
            {"user": "до 30 млн", "assistant": "подбираю"},
        )
    )


def test_v5_processor_publishes_third_turn_offer_and_persists_once() -> None:
    turn = TurnProcessor(
        planner=_Planner(),
        search_service=_Search(),
        manager_rewriter=_V5Rewriter(),
        manager_rewriter_mode="publish",
    ).process(SafeTurnContext(conversation_ref="test", user_text="готовое"), _third_turn_state())

    assert "менеджер" in turn.response_text.lower()
    assert turn.trace["manager_rewriter"]["operator_offer"] is True
    assert turn.state["operator_offered"] is True


def test_v5_processor_timeout_uses_offer_fallback_without_false_success() -> None:
    turn = TurnProcessor(
        planner=_Planner(),
        search_service=_Search(),
        manager_rewriter=_V5Rewriter(fail=True),
        manager_rewriter_mode="publish",
    ).process(SafeTurnContext(conversation_ref="test", user_text="готовое"), _third_turn_state())

    manager_meta = turn.trace["manager_rewriter"]
    assert "менеджер" in turn.response_text.lower()
    assert manager_meta["operator_offer"] is True
    assert manager_meta["operator_offer_fallback"] is True
    assert manager_meta["published"] is False
    assert turn.state["operator_offered"] is True


def test_v5_fourth_turn_does_not_repeat_offer() -> None:
    state = ConversationState(
        operator_offered=True,
        dialogue_turns=(
            {"user": "первый", "assistant": "ответ"},
            {"user": "второй", "assistant": "ответ"},
            {"user": "третий", "assistant": "подключу менеджера"},
        ),
    )
    turn = TurnProcessor(
        planner=_Planner(),
        search_service=_Search(),
        manager_rewriter=_V5Rewriter("Обычный четвёртый ответ."),
        manager_rewriter_mode="publish",
    ).process(SafeTurnContext(conversation_ref="test", user_text="четвёртый"), state)

    assert turn.response_text == "Обычный четвёртый ответ."
    assert turn.trace["manager_rewriter"].get("operator_offer") is False
