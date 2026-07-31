from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPT_DIR / "chat_tester_bot.py"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("chat_tester_bot", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["chat_tester_bot"] = mod
spec.loader.exec_module(mod)


def test_valid_clarify_payload_returns_question_without_chat(monkeypatch):
    async def scenario() -> None:
        client = mod.OvermindClient()
        gateway_calls: list[dict[str, Any]] = []
        chat_calls = 0
        payload = {
            "action": "clarify",
            "clarification_question": "Подскажите, в каком районе смотреть квартиру?",
            "facts": [],
            "near": [],
            "missing": ["district"],
            "params": {"rooms": 2, "purpose": "family"},
        }

        async def fake_ensure_session() -> None:
            return None

        async def fake_gateway(request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            gateway_calls.append(request_data)
            return json.dumps(payload, ensure_ascii=False), {"task_id": "task-1"}

        async def fake_chat(*args: Any, **kwargs: Any):
            nonlocal chat_calls
            chat_calls += 1
            raise AssertionError("chat stage must not run for valid clarify payload")

        monkeypatch.setattr(client, "ensure_session", fake_ensure_session)
        monkeypatch.setattr(client, "_run_gateway_request", fake_gateway)
        monkeypatch.setattr(client, "_chat_with_retry", fake_chat)

        response_text, params, search_meta, chat_meta = await client.ask("Нужна двушка для семьи")

        assert response_text == "Подскажите, в каком районе смотреть квартиру?"
        assert params == {"rooms": 2, "purpose": "family"}
        assert search_meta["_planner_action"] == "clarify"
        assert search_meta["_search_attempt"] == 1
        assert "_response_text" not in search_meta
        assert chat_meta == {}
        assert len(gateway_calls) == 1
        assert gateway_calls[0]["_payload_stage"] == "main_search"
        assert chat_calls == 0

    asyncio.run(scenario())


def test_clarify_helper_rejects_invalid_or_missing_action() -> None:
    assert mod._search_clarification_question({"action": "clarify", "clarification_question": "Район?"}) == "Район?"
    assert mod._search_clarification_question({"clarification_question": "Район?"}) is None
    assert mod._search_clarification_question({"action": "unknown", "clarification_question": "Район?"}) is None
    assert mod._search_clarification_question({"action": "clarify", "clarification_question": "   "}) is None
    assert mod._search_clarification_question({"action": "clarify", "clarification_question": "x" * 301}) is None


def test_operator_contact_helper_accepts_only_strict_short_question() -> None:
    assert (
        mod._search_operator_contact_question(
            {"action": "operator_contact", "clarification_question": "Пришлите номер телефона для связи со специалистом."}
        )
        == "Пришлите номер телефона для связи со специалистом."
    )
    assert mod._search_operator_contact_question({"action": "operator_contact", "clarification_question": "   "}) is None
    assert mod._search_operator_contact_question({"action": "operator_contact", "clarification_question": "x" * 301}) is None
    assert mod._search_operator_contact_question({"action": "clarify", "clarification_question": "Номер?"}) is None


def test_operator_contact_payload_returns_question_without_chat(monkeypatch):
    async def scenario() -> None:
        client = mod.OvermindClient()
        chat_calls = 0
        payload = {
            "action": "operator_contact",
            "clarification_question": "Пришлите номер телефона — специалист продолжит с вами.",
            "facts": [],
            "near": [],
            "missing": [],
            "params": {},
        }

        async def fake_ensure_session() -> None:
            return None

        async def fake_gateway(request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            return json.dumps(payload, ensure_ascii=False), {"task_id": "task-operator"}

        async def fake_chat(*args: Any, **kwargs: Any):
            nonlocal chat_calls
            chat_calls += 1
            raise AssertionError("chat stage must not run for operator_contact")

        monkeypatch.setattr(client, "ensure_session", fake_ensure_session)
        monkeypatch.setattr(client, "_run_gateway_request", fake_gateway)
        monkeypatch.setattr(client, "_chat_with_retry", fake_chat)

        response_text, params, search_meta, chat_meta = await client.ask("Куда звонить?")

        assert response_text == "Пришлите номер телефона — специалист продолжит с вами."
        assert params == {}
        assert search_meta["_planner_action"] == "operator_contact"
        assert "_response_text" not in search_meta
        assert chat_meta == {}
        assert chat_calls == 0

    asyncio.run(scenario())


def test_safe_dialog_context_is_allowlisted_in_search_query(monkeypatch):
    async def scenario() -> None:
        client = mod.OvermindClient()
        seen_queries: list[str] = []
        payload = {
            "action": "operator_contact",
            "clarification_question": "Оставьте номер телефона, и специалист уточнит детали.",
            "facts": [],
            "near": [],
            "missing": [],
            "params": {},
        }

        async def fake_ensure_session() -> None:
            return None

        async def fake_gateway(request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            seen_queries.append(request_data["query"])
            return json.dumps(payload, ensure_ascii=False), {"task_id": "task-context"}

        monkeypatch.setattr(client, "ensure_session", fake_ensure_session)
        monkeypatch.setattr(client, "_run_gateway_request", fake_gateway)

        response_text, _, search_meta, _ = await client.ask(
            "уточни",
            dialog_context={
                "last_bot_question": "Хотите, чтобы специалист проверил условия?",
                "last_offer_type": "operator_for_selected",
                "phone": "+79991234567",
                "dialog_window": [{"role": "user", "text": "secret"}],
                "client_id": "client-secret",
            },
        )

        assert response_text == "Оставьте номер телефона, и специалист уточнит детали."
        assert search_meta["_planner_action"] == "operator_contact"
        assert len(seen_queries) == 1
        query = seen_queries[0]
        assert "last_bot_question" in query
        assert "last_offer_type" in query
        assert "Хотите, чтобы специалист проверил условия?" in query
        assert "operator_for_selected" in query
        assert "+79991234567" not in query
        assert "dialog_window" not in query
        assert "client-secret" not in query

    asyncio.run(scenario())


def test_recover_dialogue_typed_payload_is_strict() -> None:
    valid = {
        "action": "recover_dialogue",
        "target": "none",
        "search_policy": "forbidden",
        "clarification_question": "Подскажите, квартиру смотрим для жизни, семьи или инвестиций?",
        "facts": [],
        "near": [],
        "params": {},
    }
    assert mod._search_turn_decision_payload(valid) == {
        "action": "recover_dialogue",
        "target": "none",
        "search_policy": "forbidden",
        "response": "Подскажите, квартиру смотрим для жизни, семьи или инвестиций?",
    }
    assert mod._search_turn_decision_payload({**valid, "search_policy": "required"}) is None
    assert mod._search_turn_decision_payload({**valid, "clarification_question": "x" * 301}) is None
    assert mod._search_turn_decision_payload({**valid, "facts": [{"name": "ЖК"}]}) is None
    assert mod._search_turn_decision_payload({**valid, "action": "unknown"}) is None


def test_answer_current_options_typed_payload_forbids_search() -> None:
    valid = {
        "action": "answer_current_options",
        "target": "current_options",
        "search_policy": "forbidden",
        "response": "По текущим вариантам семейную ипотеку нужно проверять по банку и объекту.",
        "facts": [],
        "near": [],
        "params": {},
    }
    assert mod._search_turn_decision_payload(valid) == {
        "action": "answer_current_options",
        "target": "current_options",
        "search_policy": "forbidden",
        "response": "По текущим вариантам семейную ипотеку нужно проверять по банку и объекту.",
    }
    assert mod._search_turn_decision_payload({**valid, "target": "new_search"}) is None
    assert mod._search_turn_decision_payload({**valid, "search_policy": "required"}) is None
