from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from aiohttp import web
from nmbot_v2.contracts import SemanticPlan


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPT_DIR / "nmbot_api_server.py"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("nmbot_api_server_callback_flow", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

from scripts import nmbot_runtime_adapter as adapter
from scripts.nmbot_runtime_adapter import _extract_phone_v2, _normalize_phone_v2


class FakeStore:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}

    async def get(self, user_id: str) -> dict[str, Any]:
        return self.states.setdefault(user_id, mod._canonical_reset_state())

    async def save(self, user_id: str, state: dict[str, Any]) -> None:
        self.states[user_id] = json.loads(json.dumps(state, ensure_ascii=False))


class NoLLMClient:
    async def ensure_session(self) -> None:
        raise AssertionError("contact flow must not call planner")

    async def ask(self, *args: Any, **kwargs: Any):
        raise AssertionError("contact flow must not call LLM")


def make_app(tmp_path: Path) -> web.Application:
    app = web.Application()
    app["state_store"] = FakeStore()
    app["crm_callback_outbox"] = mod.LocalCallbackOutbox(tmp_path / "outbox")
    app["overmind_client"] = NoLLMClient()
    app["jivo_session_locks"] = mod.SessionLockRegistry()
    app["jivo_dedup_cache"] = mod.JivoDedupCache(ttl_sec=60, max_entries=32)
    return app


def outbox_records(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted((tmp_path / "outbox").glob("*.json"))]


def draft_records(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "outbox" / "_drafts").glob("*.json"))


def test_phone_parser_and_normalizer_share_canonical_contract() -> None:
    expected = "+79991234567"
    for value in ("9991234567", "8 999 123-45-67", "7 (999) 123-45-67", "+7 999 123-45-67"):
        assert _extract_phone_v2(value) == expected
        assert _normalize_phone_v2(value) == expected

    assert _extract_phone_v2("+1 650 555 0100") == "+16505550100"
    assert _normalize_phone_v2("+1 650 555 0100") == "+16505550100"
    assert _extract_phone_v2("1 650 555 0100") is None
    assert _normalize_phone_v2("1 650 555 0100") == ""
    assert _extract_phone_v2("7988888888") is None
    assert _normalize_phone_v2("7988888888") == ""
    assert _extract_phone_v2("9888888888") == "+79888888888"
    assert _normalize_phone_v2("9888888888") == "+79888888888"
    assert _extract_phone_v2("+1 234 567 890 123456") is None
    assert _normalize_phone_v2("+1 234 567 890 123456") == ""


def test_invalid_pending_phone_keeps_v2_state_without_planner_or_records(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app["state_store"].states["u"] = {"nmbot_v2": {"pending_followup": "contact_phone", "contact_name": "Анна", "contact_consent": True}}

        result = await mod.run_chat(app, user_id="u", message="99999", channel="jivo", meta={"event_id": "bad-v2"})

        assert result["intent"] == "collect_contact_phone"
        assert result["awaiting_phone"] is True
        assert "неполным или неверным" in result["answer"]
        assert "+7 999 123-45-67" in result["answer"]
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "contact_phone"
        assert outbox_records(tmp_path) == []
        assert draft_records(tmp_path) == []

    asyncio.run(scenario())


def test_invalid_pending_phone_keeps_v3_state_without_planner_or_records(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app["state_store"].states["u"] = {"runtime_version_override": "V3", "nmbot_v2": {"pending_followup": "contact_phone", "contact_name": "Анна", "contact_consent": True}}

        result = await mod.run_chat(app, user_id="u", message="99999", channel="jivo", meta={"event_id": "bad-v3"})

        assert result["intent"] == "collect_contact_phone"
        assert result["awaiting_phone"] is True
        assert result["meta"] == {"runtime": "v3", "callback_ref": None, "engine": "v2"}
        assert "неполным или неверным" in result["answer"]
        assert "+7 999 123-45-67" in result["answer"]
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "contact_phone"
        assert outbox_records(tmp_path) == []
        assert draft_records(tmp_path) == []

    asyncio.run(scenario())


def test_invalid_pending_phone_keeps_v0_state_without_planner_or_records(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app["state_store"].states["u"] = {"runtime_version_override": "V0", "nmbot_v0": {"pending_action": "contact_phone", "last_assistant_question": "Оставите номер телефона?"}}

        result = await mod.run_chat(app, user_id="u", message="99999", channel="jivo", meta={"event_id": "bad-v0"})

        assert result["intent"] == "collect_contact_phone"
        assert result["awaiting_phone"] is True
        assert result["meta"]["runtime"] == "v0"
        assert "неполным или неверным" in result["answer"]
        assert "+7 999 123-45-67" in result["answer"]
        assert app["state_store"].states["u"]["nmbot_v0"]["pending_action"] == "contact_phone"
        assert outbox_records(tmp_path) == []
        assert draft_records(tmp_path) == []

    asyncio.run(scenario())


def test_v0_pending_phone_to_name_completes_callback_without_planner(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app["state_store"].states["u"] = {"runtime_version_override": "V0", "nmbot_v0": {"pending_action": "contact_phone", "last_assistant_question": "Оставите номер телефона?"}}

        first = await mod.run_chat(app, user_id="u", message="99999", channel="jivo", meta={"event_id": "bad-v0"})
        assert first["intent"] == "collect_contact_phone"
        assert first["awaiting_phone"] is True
        assert app["state_store"].states["u"]["nmbot_v0"]["pending_action"] == "contact_phone"
        assert outbox_records(tmp_path) == []
        assert draft_records(tmp_path) == []

        second = await mod.run_chat(app, user_id="u", message="8 (999) 123-45-67", channel="jivo", meta={"event_id": "phone-v0"})
        assert second["intent"] == "collect_contact_name"
        assert second["awaiting_phone"] is False
        assert app["state_store"].states["u"]["nmbot_v0"]["pending_action"] == "contact_name"
        assert outbox_records(tmp_path) == []
        assert len(draft_records(tmp_path)) == 1

        third = await mod.run_chat(app, user_id="u", message="Иван", channel="jivo", meta={"event_id": "name-v0"})
        assert third["intent"] == "callback_queued"
        assert third["meta"]["runtime"] == "v0"
        records = outbox_records(tmp_path)
        assert len(records) == 1
        assert records[0]["contact"] == {"name": "Иван", "phone": "+79991234567"}
        assert draft_records(tmp_path) == []
        state = app["state_store"].states["u"]["nmbot_v0"]
        assert state.get("pending_action") is None
        assert state.get("pending_subject") is None
        assert state.get("pending_topic") is None
        assert state.get("previous_assistant_message") == third["answer"]

    asyncio.run(scenario())


def test_valid_pending_phone_forms_are_canonical_and_queue_single_record(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app["state_store"].states["u"] = {"nmbot_v2": {"pending_followup": "contact_phone", "contact_name": "Анна", "contact_consent": True}}

        result = await mod.run_chat(app, user_id="u", message="8 (999) 123-45-67", channel="jivo", meta={"event_id": "good-phone"})

        assert result["intent"] == "callback_queued"
        assert result["crm_callback"]["status"] == "queued"
        records = outbox_records(tmp_path)
        assert len(records) == 1
        assert records[0]["contact"] == {"name": "Анна", "phone": "+79991234567"}
        assert draft_records(tmp_path) == []

    asyncio.run(scenario())


def test_operator_accept_is_phone_first_public_contract(tmp_path: Path, monkeypatch) -> None:
    async def fake_plan(self, context, state):
        return SemanticPlan(operation="operator", operator_consent=True, explicit_operator_request=True, query_text=context.user_text)

    async def scenario() -> None:
        monkeypatch.setattr(adapter._SemanticPlannerAdapter, "plan", fake_plan)
        app = make_app(tmp_path)

        result = await adapter.run_runtime_turn(app, user_id="u", message="позови оператора", channel="jivo", meta={"event_id": "accept"})

        assert result["intent"] == "collect_contact_phone"
        assert result["awaiting_phone"] is True
        assert result["turn_decision"] == {"stage": "operator_handoff", "action": "accept_operator"}
        assert "На какой номер вам удобно позвонить?" in result["answer"]
        assert "Как к вам обращаться" not in result["answer"]
        state = app["state_store"].states["u"]["nmbot_v2"]
        assert state["pending_followup"] == "contact_phone"
        assert state["contact_consent"] is True

    asyncio.run(scenario())


def test_profile_name_and_phone_queue_once_without_public_phone_or_invite(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        result = await mod.run_chat(
            app,
            user_id="jivo:s:c:u",
            message="мой номер +7 999 123-45-67",
            channel="jivo",
            meta={"event_id": "evt-1", "sender_name": "Мария", "client_id": "raw-client"},
        )

        assert result["intent"] == "callback_queued"
        assert result["crm_callback"]["status"] == "queued"
        assert result["handoff_to_operator"] is False
        assert "INVITE_AGENT" not in repr(result)
        assert "+7 999" not in repr(result)
        assert "9991234567" not in repr(result)
        state = app["state_store"].states["jivo:s:c:u"]
        assert "9991234567" not in repr(state)
        assert set(state) == {"nmbot_v2"}
        assert state["nmbot_v2"].get("pending_followup") is None
        assert state["nmbot_v2"].get("contact_consent") is True
        records = outbox_records(tmp_path)
        assert len(records) == 1
        assert records[0]["contact"] == {"name": "Мария", "phone": "+79991234567"}
        assert "9991234567" not in repr(records[0]["summary_input"])
        assert "Мария" not in repr(records[0]["summary_input"])
        assert "raw-client" not in repr(records[0]["summary_input"])
        assert records[0]["summary_input"]["runtime"] == "v2"
        assert "params" in records[0]["summary_input"]
        assert "selected_option" in records[0]["summary_input"]
        assert "current_options" in records[0]["summary_input"]
        assert "last_bot_question" in records[0]["summary_input"]

    asyncio.run(scenario())


def test_pending_phone_queues_with_safe_profile_name_without_asking_name(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app["state_store"].states["u"] = {"nmbot_v2": {"pending_followup": "contact_phone", "contact_consent": True}}

        result = await mod.run_chat(app, user_id="u", message="8 (999) 123-45-67", channel="jivo", meta={"event_id": "good-profile", "sender_name": "Мария"})

        assert result["intent"] == "callback_queued"
        assert result["awaiting_phone"] is False
        assert "как к вам обращаться" not in result["answer"].casefold()
        assert outbox_records(tmp_path)[0]["contact"] == {"name": "Мария", "phone": "+79991234567"}

    asyncio.run(scenario())


def test_pending_phone_queues_without_profile_as_anonymous_without_asking_name(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app["state_store"].states["u"] = {"nmbot_v2": {"pending_followup": "contact_phone", "contact_consent": True}}

        result = await mod.run_chat(app, user_id="u", message="8 (999) 123-45-67", channel="jivo", meta={"event_id": "good-anon", "sender_name": "synthetic-nmbot"})

        assert result["intent"] == "callback_queued"
        assert "как к вам обращаться" not in result["answer"].casefold()
        assert outbox_records(tmp_path)[0]["contact"] == {"name": "Без имени", "phone": "+79991234567"}

    asyncio.run(scenario())


def test_pending_phone_substantive_question_is_not_swallowed(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    state = adapter.ConversationState.from_dict({"pending_followup": "contact_phone", "contact_consent": True})

    result = adapter._try_capture_contact(app, user_id="u", text="а какие цены во втором ЖК?", channel="jivo", meta={}, state=state, runtime_version="v2")

    assert result is None


def test_v0_profile_name_and_phone_queue_without_planner_or_public_pii(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app["state_store"].states["u"] = {
            "runtime_version_override": "V0",
            "nmbot_v0": {
                "params": {"rooms": 2, "budget": 12000000},
                "visible_options": [{"name": "ЖК Первый", "location": "Москва"}],
                "selected_option_name": "ЖК Первый",
                "active_topic": "life",
                "last_assistant_question": "Хотите, чтобы специалист перезвонил?",
            },
        }

        result = await mod.run_chat(app, user_id="u", message="мой номер +7 999 123-45-67", channel="jivo", meta={"event_id": "v0-phone", "sender_name": "Мария"})

        assert result["intent"] == "callback_queued"
        assert result["meta"]["runtime"] == "v0"
        assert "+7 999" not in repr(result)
        state = app["state_store"].states["u"]
        assert "9991234567" not in repr(state)
        assert set(state) == {"runtime_version_override", "nmbot_v0"}
        records = outbox_records(tmp_path)
        assert len(records) == 1
        snapshot = records[0]["summary_input"]
        assert snapshot["runtime"] == "v0"
        assert snapshot["params"] == {"rooms": 2, "budget": 12000000}
        assert snapshot["selected_option"]["name"] == "ЖК Первый"
        assert snapshot["current_options"][0]["name"] == "ЖК Первый"
        assert snapshot["last_bot_question"] == "Хотите, чтобы специалист перезвонил?"
        assert "Мария" not in repr(snapshot)
        assert "9991234567" not in repr(snapshot)

    asyncio.run(scenario())


def test_v3_contact_queue_records_v3_runtime_and_v2_engine(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app["state_store"].states["u"] = {"runtime_version_override": "V3", "nmbot_v2": {"params": {"rooms": 1}}}

        result = await mod.run_chat(app, user_id="u", message="мой номер +7 999 123-45-67", channel="jivo", meta={"event_id": "v3-phone", "sender_name": "Анна"})

        assert result["intent"] == "callback_queued"
        assert result["meta"]["runtime"] == "v3"
        assert result["meta"]["engine"] == "v2"
        records = outbox_records(tmp_path)
        assert len(records) == 1
        assert records[0]["summary_input"]["runtime"] == "v3"
        assert records[0]["summary_input"]["engine"] == "v2"

    asyncio.run(scenario())


def test_v3_pending_phone_queues_callback_with_v2_engine_and_anonymous_name(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app["state_store"].states["u"] = {"runtime_version_override": "V3", "nmbot_v2": {"pending_followup": "contact_phone", "contact_consent": True}}

        result = await mod.run_chat(app, user_id="u", message="+7 999 123-45-67", channel="jivo", meta={"event_id": "v3-pending-phone"})

        assert result["intent"] == "callback_queued"
        assert result["meta"]["runtime"] == "v3"
        assert result["meta"]["engine"] == "v2"
        assert outbox_records(tmp_path)[0]["contact"] == {"name": "Без имени", "phone": "+79991234567"}

    asyncio.run(scenario())


def test_phone_first_private_draft_then_name_confirms(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        first = await mod.run_chat(app, user_id="u", message="+7 999 123-45-67", channel="jivo", meta={"event_id": "phone"})
        assert first["intent"] == "collect_contact_name"
        assert "crm_callback" not in first
        assert outbox_records(tmp_path) == []
        assert len(list((tmp_path / "outbox" / "_drafts").glob("*.json"))) == 1

        second = await mod.run_chat(app, user_id="u", message="Иван", channel="jivo", meta={"event_id": "name"})
        assert second["intent"] == "callback_queued"
        assert second["crm_callback"]["status"] == "queued"
        records = outbox_records(tmp_path)
        assert len(records) == 1
        assert records[0]["contact"]["name"] == "Иван"
        assert records[0]["contact"]["phone"] == "+79991234567"
        assert list((tmp_path / "outbox" / "_drafts").glob("*.json")) == []

    asyncio.run(scenario())


def test_name_first_then_phone_confirms(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app["state_store"].states["u"] = {"nmbot_v2": {"pending_followup": "contact_name"}}
        second = await mod.run_chat(app, user_id="u", message="Анна", channel="jivo")
        assert second["intent"] == "collect_contact_phone"
        third = await mod.run_chat(app, user_id="u", message="+7 912 000-00-01", channel="jivo")
        assert third["intent"] == "callback_queued"
        assert third["crm_callback"]["status"] == "queued"
        records = outbox_records(tmp_path)
        assert len(records) == 1
        assert records[0]["contact"] == {"name": "Анна", "phone": "+79120000001"}

    asyncio.run(scenario())


def test_duplicate_inbound_event_returns_same_bot_message_and_single_private_record(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        payload = {
            "event": "CLIENT_MESSAGE",
            "id": "same-event",
            "site_id": "site",
            "chat_id": "chat",
            "client_id": "client",
            "agents_online": True,
            "sender": {"name": "Иван"},
            "message": {"type": "TEXT", "text": "+7 999 123-45-67"},
        }
        first = await mod.process_jivo_client_message(app, payload)
        second = await mod.process_jivo_client_message(app, payload)
        assert second == first
        assert first[0]["event"] == "BOT_MESSAGE"
        assert len(outbox_records(tmp_path)) == 1

    asyncio.run(scenario())
