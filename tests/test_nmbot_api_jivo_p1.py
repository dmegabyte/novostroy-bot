from __future__ import annotations

import asyncio
import ast
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from aiohttp import web


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPT_DIR / "nmbot_api_server.py"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("nmbot_api_server", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["nmbot_api_server"] = mod
spec.loader.exec_module(mod)


class FakeStore:
    def __init__(self) -> None:
        self.reset_calls: list[str] = []
        self.states: dict[str, dict[str, Any]] = {}
        self.saved: list[tuple[str, dict[str, Any]]] = []

    async def get(self, user_id: str) -> dict[str, Any]:
        return self.states.setdefault(user_id, mod._default_state())

    async def save(self, user_id: str, state: dict[str, Any]) -> None:
        self.states[user_id] = dict(state)
        self.saved.append((user_id, dict(state)))

    async def reset(self, user_id: str) -> None:
        self.reset_calls.append(user_id)

    async def reset_canonical(self, user_id: str) -> None:
        self.reset_calls.append(user_id)
        self.states[user_id] = mod._canonical_reset_state()


class FakeRuntimeVersionStore:
    def __init__(self, version: str = "V2") -> None:
        self.version = version

    async def get(self) -> str:
        return self.version

    async def set(self, version: str) -> str:
        self.version = mod._normalize_runtime_version(version)
        return self.version


def make_app(tmp_path: Path | None = None) -> web.Application:
    app = web.Application()
    app["state_store"] = FakeStore()
    app["runtime_version_store"] = FakeRuntimeVersionStore()
    app["crm_callback_outbox"] = mod.LocalCallbackOutbox(tmp_path / "outbox" if tmp_path else Path("/tmp/nmbot-test-callback-outbox"))
    app["jivo_session_locks"] = mod.SessionLockRegistry()
    app["jivo_dedup_cache"] = mod.JivoDedupCache(ttl_sec=60, max_entries=32)
    return app


def payload(*, event_id: str | None, chat_id: str = "chat1", text: str = "Привет") -> dict[str, Any]:
    data: dict[str, Any] = {
        "event": "CLIENT_MESSAGE",
        "site_id": "site1",
        "client_id": f"client-{chat_id}",
        "chat_id": chat_id,
        "agents_online": True,
        "message": {"type": "TEXT", "text": text},
    }
    if event_id is not None:
        data["id"] = event_id
    return data


def test_run_chat_v1_does_not_replace_canonical_overmind_client_source_contract() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    run_chat_v1 = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_chat_v1")

    forbidden_assignments = []
    legacy_key_seen = False
    for node in ast.walk(run_chat_v1):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "app":
            key_node = node.slice
            if isinstance(key_node, ast.Constant) and key_node.value == "legacy_overmind_client":
                legacy_key_seen = True
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "app"
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "overmind_client"
                ):
                    forbidden_assignments.append(target)

    assert not forbidden_assignments
    assert legacy_key_seen


def test_jivo_payload_normalizer_accepts_canonical_and_legacy_text_shapes():
    canonical = mod._normalize_jivo_payload({"event": "CLIENT_MESSAGE", "message": {"type": "TEXT", "text": "  Привет  "}})
    assert canonical and canonical["message"]["text"] == "Привет"

    legacy = mod._normalize_jivo_payload({"event": "CLIENT_MESSAGE", "payload": {"type": "text", "text": "  Подбери квартиру  "}})
    assert legacy and legacy["message"] == {"type": "TEXT", "text": "Подбери квартиру"}

    assert mod._normalize_jivo_payload({"event": "CLIENT_MESSAGE", "message": {"type": "TEXT", "text": "   "}}) is None


def test_jivo_handler_rejects_invalid_json_without_500():
    class BadJsonRequest:
        match_info = {"provider_token": "configured"}

        async def json(self):
            raise ValueError("malformed body")

    async def scenario() -> None:
        previous = os.environ.get("JIVO_PROVIDER_TOKEN")
        os.environ["JIVO_PROVIDER_TOKEN"] = "configured"
        try:
            response = await mod.handle_jivo(BadJsonRequest())
            assert response.status == 400
            assert json.loads(response.body) == {"ok": False, "error": "invalid_json"}
        finally:
            if previous is None:
                os.environ.pop("JIVO_PROVIDER_TOKEN", None)
            else:
                os.environ["JIVO_PROVIDER_TOKEN"] = previous

    asyncio.run(scenario())


def test_jivo_handler_returns_json_bot_message_when_runtime_raises(monkeypatch):
    class Request:
        match_info = {"provider_token": "configured"}
        app = make_app()

        async def json(self):
            return payload(event_id="event-failure")

    async def fail(*args: Any, **kwargs: Any):
        raise KeyError("data")

    logged: list[dict[str, Any]] = []
    journaled: list[dict[str, Any]] = []
    monkeypatch.setattr(mod, "process_jivo_client_message", fail)
    monkeypatch.setattr(mod, "_log_error_event", logged.append)
    monkeypatch.setattr(mod, "append_journal_event", lambda **kwargs: journaled.append(kwargs))

    async def scenario() -> None:
        previous = os.environ.get("JIVO_PROVIDER_TOKEN")
        os.environ["JIVO_PROVIDER_TOKEN"] = "configured"
        try:
            response = await mod.handle_jivo(Request())
            body = json.loads(response.body)
            assert response.status == 200
            assert body["event"] == "BOT_MESSAGE"
            assert body["message"]["text"] == mod.SAFE_UPSTREAM_ERROR_TEXT
            assert len(logged) == 1
            assert logged[0]["error_type"] == "jivo_handler_exception"
            assert logged[0]["stage"] == "process_client_message"
            assert logged[0]["exception_type"] == "KeyError"
            assert logged[0]["stack"][-1]["function"] == "fail"
            assert journaled[0]["error_summary"] == {
                "status": "failed",
                "codes": ["jivo_handler_exception"],
                "stages": ["jivo_handler"],
                "fallback": True,
            }
        finally:
            if previous is None:
                os.environ.pop("JIVO_PROVIDER_TOKEN", None)
            else:
                os.environ["JIVO_PROVIDER_TOKEN"] = previous

    asyncio.run(scenario())


def test_jivo_trace_header_is_hashed_and_invalid_header_ignored(monkeypatch):
    raw_uuid = "123e4567-e89b-12d3-a456-426614174000"
    safe_ref = "trace_" + hashlib.sha256(raw_uuid.encode("utf-8")).hexdigest()[:12]

    class Request:
        match_info = {"provider_token": "configured"}
        app = make_app()
        headers = {"X-NMBOT-Trace-ID": raw_uuid}

        async def json(self):
            return payload(event_id="event-trace")

    seen: list[dict[str, Any]] = []

    async def fake_process(app: Any, payload: dict[str, Any], trace_ref: str | None = None):
        seen.append({"trace_ref": trace_ref})
        return mod.build_jivo_bot_message(payload, "ok"), 200

    monkeypatch.setattr(mod, "process_jivo_client_message", fake_process)

    async def scenario() -> None:
        previous = os.environ.get("JIVO_PROVIDER_TOKEN")
        os.environ["JIVO_PROVIDER_TOKEN"] = "configured"
        try:
            response = await mod.handle_jivo(Request())
            assert response.status == 200
            assert seen == [{"trace_ref": safe_ref}]
            assert raw_uuid not in json.dumps(seen, ensure_ascii=False)

            class BadHeaderRequest(Request):
                headers = {"X-NMBOT-Trace-ID": "not-a-uuid"}

            await mod.handle_jivo(BadHeaderRequest())
            assert seen[-1] == {"trace_ref": None}
        finally:
            if previous is None:
                os.environ.pop("JIVO_PROVIDER_TOKEN", None)
            else:
                os.environ["JIVO_PROVIDER_TOKEN"] = previous

    asyncio.run(scenario())


def patch_planner(monkeypatch, plan: dict[str, Any], calls: list[dict[str, Any]] | None = None) -> None:
    async def fake_plan(session: Any, **kwargs: Any) -> dict[str, Any]:
        if calls is not None:
            calls.append(kwargs)
        return plan

    monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)


def test_same_event_is_deduped_after_completed_response(monkeypatch):
    async def scenario() -> None:
        app = make_app()
        calls = 0

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"ok": True, "answer": f"Ответ {calls}", "handoff_to_operator": False}

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        first = await mod.process_jivo_client_message(app, payload(event_id="event-1"))
        second = await mod.process_jivo_client_message(app, payload(event_id="event-1"))

        assert calls == 1
        assert second == first

    asyncio.run(scenario())


def test_jivo_canonical_journal_writes_user_and_bot_once(tmp_path, monkeypatch):
    async def scenario() -> None:
        app = make_app(tmp_path)
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "answer": "Нашла вариант. Оставьте +7 999 111-22-33?", "intent": "main_search", "handoff_to_operator": False}

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        first = await mod.process_jivo_client_message(app, payload(event_id="event-1", text="Есть вариант? Мой номер +7 999 123-45-67"))
        second = await mod.process_jivo_client_message(app, payload(event_id="event-1", text="Есть вариант? Мой номер +7 999 123-45-67"))

        assert first == second
        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [row["role"] for row in rows] == ["user", "bot"]
        assert all(row["session_key_ref"].startswith("sha256:") for row in rows)
        assert all("session_key" not in row and "conversation_id" not in row for row in rows)
        assert all("site1" not in json.dumps(row) and "chat1" not in json.dumps(row) for row in rows)
        assert all("999" not in row["text"] for row in rows)
        assert all(row["site_id_ref"].startswith("sha256:") for row in rows)
        assert rows[1]["error_summary"] == {
            "status": "ok",
            "codes": [],
            "stages": [],
            "fallback": False,
        }
        readable = (tmp_path / "dialogue-readable.log").read_text(encoding="utf-8")
        assert "Клиент: Есть вариант? Мой номер [phone redacted]" in readable
        assert "Ирина: Нашла вариант. Оставьте [phone redacted]?" in readable
        assert "session_key" not in readable and "site1" not in readable and "chat1" not in readable

    asyncio.run(scenario())


def test_jivo_uses_v4_client_answer_without_parsing_internal_json(tmp_path, monkeypatch):
    async def scenario() -> None:
        app = make_app(tmp_path)
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))
        human = "Нашла три варианта. Какой показать подробнее?"
        internal = json.dumps({"data": [1, 2, 3], "message": human}, ensure_ascii=False, separators=(",", ":"))

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "answer": internal,
                "client_answer": human,
                "intent": "flat_search_json",
                "answer_kind": "v4_strict_json",
                "handoff_to_operator": False,
                "meta": {"runtime": "v4"},
            }

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        response, status = await mod.process_jivo_client_message(app, payload(event_id="event-v4-client", text="Нужна двушка для семьи"))

        assert status == 200
        assert response["event"] == "BOT_MESSAGE"
        assert response["message"]["text"] == human
        assert response["message"]["text"] != internal
        assert not response["message"]["text"].strip().startswith("{")
        assert "\\n" not in response["message"]["text"]
        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        assert rows[1]["text"] == human

    asyncio.run(scenario())


def test_jivo_without_client_answer_keeps_existing_answer_behavior(monkeypatch):
    async def scenario() -> None:
        app = make_app()

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "answer": "Старый ответ", "intent": "main_search", "handoff_to_operator": False}

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        response, status = await mod.process_jivo_client_message(app, payload(event_id="event-old", text="Привет"))

        assert status == 200
        assert response["event"] == "BOT_MESSAGE"
        assert response["message"]["text"] == "Старый ответ"

    asyncio.run(scenario())


def test_jivo_journal_writes_same_release_id_for_user_and_bot(tmp_path, monkeypatch):
    async def scenario() -> None:
        app = make_app(tmp_path)
        identity = tmp_path / "identity.json"
        identity.write_text(json.dumps({"schema": "nmbot.release_identity.v1", "release_id": "rel-jivo", "tracked_files": []}), encoding="utf-8")
        monkeypatch.setenv("NMBOT_RELEASE_IDENTITY_FILE", str(identity))
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "answer": "Ответ", "intent": "main_search", "handoff_to_operator": False}

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        await mod.process_jivo_client_message(app, payload(event_id="event-release", text="Привет"))

        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [row.get("release_id") for row in rows] == ["rel-jivo", "rel-jivo"]

    asyncio.run(scenario())


def test_jivo_bot_journal_persists_only_safe_response_composer_marker(tmp_path, monkeypatch):
    async def scenario() -> None:
        app = make_app(tmp_path)
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "answer": "Ответ по безопасным данным.",
                "intent": "main_search",
                "handoff_to_operator": False,
                "meta": {
                    "trace": {
                        "response_composer": {
                            "composer_used": False,
                            "fallback_reason": "validation_failed",
                            "validation_stage": "schema",
                            "validation_codes": ["recipe_cta_mismatch", "raw response secret"],
                            "attempts": 2,
                            "prompt": "V2_RESPONSE_BRIEF=raw prompt secret",
                            "raw_model_output": "raw response secret",
                            "cards": [{"name": "Секретный ЖК", "phone": "+7 999 123-45-67"}],
                            "client_id": "client-chat1",
                            "sender_name": "Иван",
                        }
                    },
                    "offer_type": "safe_offer",
                },
            }

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        await mod.process_jivo_client_message(app, payload(event_id="event-composer", text="Хочу квартиру, я Иван, телефон +7 999 123-45-67"))

        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        assert rows[0]["role"] == "user"
        assert "response_composer" not in rows[0]
        bot = rows[1]
        assert bot["role"] == "bot"
        assert bot["response_composer"] == {
            "composer_used": False,
            "fallback_reason": "validation_failed",
            "validation_stage": "schema",
            "validation_codes": ["recipe_cta_mismatch"],
            "attempts": 2,
        }
        assert bot["error_summary"] == {
            "status": "degraded",
            "codes": ["composer_validation_failed", "recipe_cta_mismatch"],
            "stages": ["composer"],
            "fallback": True,
        }
        dumped = json.dumps(bot, ensure_ascii=False)
        for forbidden in ["V2_RESPONSE_BRIEF", "raw response", "Секретный ЖК", "+7 999", "client-chat1", "Иван", "secret"]:
            assert forbidden not in dumped

    asyncio.run(scenario())


def test_jivo_bot_journal_persists_safe_runtime_summary_only_on_bot_row(tmp_path, monkeypatch):
    async def scenario() -> None:
        app = make_app(tmp_path)
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "answer": "Ответ по безопасным данным.",
                "intent": "main_search",
                "handoff_to_operator": False,
                "meta": {
                    "trace": {
                        "runtime_summary": {
                            "stage": "first_list",
                            "action": "search",
                            "answer_kind": "search_results",
                            "timing_ms": {"planner": 1, "execution": 2, "response": 3, "total": 6, "raw": 999},
                            "call_counts": {"planner": 1, "search": 1, "selected_enrichment": 0, "gateway_attempts": 8, "provider_retries": 99},
                            "state_before": {"param_keys": ["rooms", "phone:+79991234567"], "visible_options_count": 99, "selected_present": False, "pending_followup": "", "active_topic": "family", "raw_params": {"rooms": 2}},
                            "state_after": {"param_keys": ["rooms"], "visible_options_count": 3, "selected_present": True, "pending_followup": "financing_consent", "active_topic": "family", "selected_name": "Секретный ЖК"},
                            "question_count": 1,
                            "final_question_at_end": True,
                            "quality_blockers": ["search_without_cards", "raw secret"],
                            "grounding_scope": "grounded_true",
                            "option_enrichment": {
                                "availability_evidence": {
                                    "requested": True,
                                    "confirmation": "confirmed",
                                    "source": "gateway",
                                    "gateway_task_id": "task-2386206/unsafe suffix",
                                    "inventory_value": 5242,
                                    "raw_mcp_text": "секретный MCP payload",
                                    "query": "наличие квартир secret",
                                },
                                "count": 1,
                                "items": [{"name": "Секретный ЖК"}],
                            },
                            "intent_transition": {
                                "goal": "compare_current",
                                "intent_validation": "accepted",
                                "validation_error_codes": ["invalid_selected_option_scope", "raw secret"],
                                "transition": {"accepted": True, "error_code": "raw secret", "selected_option_name": "Секретный ЖК"},
                                "fallback_used": False,
                                "raw_plan": {"query_text": "сравни с томилиским бульваром"},
                            },
                            "prompt": "raw prompt secret",
                            "cards": [{"name": "Секретный ЖК"}],
                        }
                    }
                },
            }

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        await mod.process_jivo_client_message(app, payload(event_id="event-runtime", text="Хочу квартиру, телефон +7 999 123-45-67"))

        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        assert "runtime_summary" not in rows[0]
        runtime = rows[1]["runtime_summary"]
        assert runtime["stage"] == "first_list"
        assert runtime["action"] == "search"
        assert runtime["call_counts"] == {"planner": 1, "search": 1, "selected_enrichment": 0, "gateway_attempts": 5, "scenario_search": 0, "answer": 0}
        assert runtime["timing_ms"] == {"planner": 1, "execution": 2, "response": 3, "total": 6}
        assert runtime["state_after"]["param_keys"] == ["rooms"]
        assert runtime["option_enrichment"] == {
            "availability_evidence": {
                "requested": True,
                "confirmation": "confirmed",
                "source": "gateway",
                "gateway_task_id": "task-2386206_unsafe_suffix",
            }
        }
        assert set(runtime["option_enrichment"]) == {"availability_evidence"}
        assert set(runtime["option_enrichment"]["availability_evidence"]) == {"requested", "confirmation", "source", "gateway_task_id"}
        assert runtime["grounding_scope"] == "canonical_response_plan"
        assert runtime["intent_transition"] == {
            "goal": "compare_current",
            "intent_validation": "accepted",
            "validation_error_codes": ["invalid_selected_option_scope"],
            "transition": {"accepted": True, "error_code": None},
            "fallback_used": False,
        }
        dumped = json.dumps(rows[1], ensure_ascii=False)
        for forbidden in ["raw prompt", "Секретный ЖК", "+7999", "+7 999", "grounded_true", "provider_retries", "raw_params", "5242", "raw_mcp", "query", "items", "raw secret", "raw_plan", "selected_option_name", "томилиским"]:
            assert forbidden not in dumped

    asyncio.run(scenario())


def test_jivo_bot_journal_persists_safe_v4_gateway_trace_without_raw_sensitive_fields(tmp_path, monkeypatch):
    async def scenario() -> None:
        app = make_app(tmp_path)
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "answer": '{"data":[101],"message":"Нашла вариант."}',
                "client_answer": "Нашла вариант.",
                "intent": "flat_search_json",
                "handoff_to_operator": False,
                "meta": {
                    "runtime": "v4",
                    "trace": {
                        "runtime_summary": {
                            "stage": "v4_gateway",
                            "action": "one_prompt",
                            "answer_kind": "v4_strict_json",
                            "call_counts": {"gateway_attempts": 1, "provider_retries": 9},
                            "gateway_attempt_details": [
                                {
                                    "stage": "gateway_attempt",
                                    "gateway_task_id": "task_v4_1",
                                    "model": "openai/gpt-5.5",
                                    "gateway_status": "completed",
                                    "response_chars": 43,
                                    "response_parse": "valid_json",
                                    "data_count": 1,
                                    "message_chars": 14,
                                    "call_attempted": True,
                                    "request_shape": {"family_query": True, "rooms_mentioned": True, "raw_text": "Анна +7 999"},
                                    "raw_prompt": "secret prompt",
                                    "raw_mcp_text": "секретный MCP payload",
                                }
                            ],
                        }
                    },
                },
            }

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        await mod.process_jivo_client_message(app, payload(event_id="event-v4-trace", text="Анна ищет двушку для семьи, телефон +7 999 123-45-67"))

        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        assert "runtime_summary" not in rows[0]
        attempt = rows[1]["runtime_summary"]["gateway_attempt_details"][0]
        assert attempt == {
            "stage": "gateway_attempt",
            "model": "openai_gpt-5.5",
            "gateway_task_id": "task_v4_1",
            "duration_ms": 0,
            "gateway_status": "completed",
            "response_parse": "valid_json",
            "response_chars": 43,
            "message_chars": 14,
            "data_count": 1,
            "call_attempted": True,
            "request_shape": {"family_query": True, "rooms_mentioned": True},
        }
        dumped = json.dumps(rows[1], ensure_ascii=False).lower()
        for forbidden in ("raw_prompt", "raw_mcp", "secret", "payload", "анна", "+7 999", "9991234567", "provider_retries"):
            assert forbidden not in dumped

    asyncio.run(scenario())


def test_jivo_bot_journal_persists_safe_v1_response_model_trace(tmp_path, monkeypatch):
    async def scenario() -> None:
        app = make_app(tmp_path)
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "answer": "Ответ по безопасным данным.",
                "intent": "search_results",
                "handoff_to_operator": False,
                "meta": {
                    "runtime": "v1",
                    "trace": {
                        "response_model": {
                            "mode": "publish",
                            "status": "fallback",
                            "model": "openai/gpt-5.5",
                            "published": False,
                            "reason": "one_model_validation_failed:unknown_project_mention:Секретный ЖК +7 999",
                            "candidate_sha256": "a" * 64,
                            "candidate_chars": 77,
                            "raw_candidate": "Секретный ЖК",
                        }
                    },
                },
            }

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        await mod.process_jivo_client_message(app, payload(event_id="event-v1-model", text="секретный запрос"))

        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        assert "response_model" not in rows[0]
        assert rows[1]["response_model"] == {
            "mode": "publish",
            "status": "fallback",
            "published": False,
            "model": "openai/gpt-5.5",
            "reason": "one_model_validation_failed:unknown_project_mention",
        }
        dumped = json.dumps(rows[1], ensure_ascii=False)
        for forbidden in ["candidate_sha256", "candidate_chars", "raw_candidate", "Секретный ЖК", "+7 999", "secret"]:
            assert forbidden not in dumped

    asyncio.run(scenario())


def test_jivo_bot_journal_persists_safe_v0_field_trace_names_only(tmp_path, monkeypatch):
    async def scenario() -> None:
        app = make_app(tmp_path)
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "answer": "Нашла три подходящих варианта.",
                "intent": "v0_turn",
                "handoff_to_operator": False,
                "meta": {
                    "runtime": "v0",
                    "trace": {
                        "runtime_summary": {
                            "stage": "v0_turn",
                            "action": "search",
                            "answer_kind": "search_many",
                            "call_counts": {"planner": 1, "search": 1, "selected_enrichment": 0, "gateway_attempts": 2, "scenario_search": 1, "answer": 1},
                            "field_trace": {
                                "cards": [
                                    {
                                        "raw_fields": ["name", "location", "min_price", "client_text", "phone"],
                                        "normalized_fields": ["name", "location", "price_min", "secret"],
                                        "raw_values": {"name": "Секретный ЖК", "location": "Москва"},
                                    }
                                ],
                                "query": "секретный запрос",
                            },
                        }
                    },
                },
            }

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        await mod.process_jivo_client_message(app, payload(event_id="event-v0-field-trace", text="секретный запрос"))

        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        trace = rows[1]["runtime_summary"]["field_trace"]["cards"][0]
        assert trace == {"raw_fields": ["name", "location", "min_price"], "normalized_fields": ["name", "location", "price_min"]}
        assert rows[1]["runtime_summary"]["call_counts"] == {"planner": 1, "search": 1, "selected_enrichment": 0, "gateway_attempts": 2, "scenario_search": 1, "answer": 1}
        dumped = json.dumps(rows[1]["runtime_summary"], ensure_ascii=False)
        for forbidden in ["Секретный ЖК", "Москва", "секретный запрос", "client_text", "phone", "raw_values"]:
            assert forbidden not in dumped

    asyncio.run(scenario())


def test_jivo_runtime_failure_logs_only_safe_v2_trace(tmp_path, monkeypatch):
    async def scenario() -> None:
        app = make_app(tmp_path)
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))
        logged: list[dict[str, Any]] = []

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "ok": False,
                "answer": mod.SAFE_UPSTREAM_ERROR_TEXT,
                "intent": "safe_upstream_fallback",
                "error_type": "unsafe error with client text",
                "handoff_to_operator": False,
                "meta": {
                    "runtime": "v2",
                    "trace": {
                        "error_code": "search_TimeoutError",
                        "stage": "selection",
                        "action": "search",
                        "timing_ms": {"planner": 12.8, "execution": 301, "total": 314, "secret": 99},
                        "raw_payload": {"phone": "+7 999 123-45-67"},
                    },
                },
            }

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        monkeypatch.setattr(mod, "_log_error_event", logged.append)
        await mod.process_jivo_client_message(app, payload(event_id="event-runtime-failure", text="секретный запрос"))

        assert logged == [{
            "error_type": "v2_runtime_failure",
            "stage": "runtime_execution",
            "error_code": "search_TimeoutError",
            "runtime_stage": "selection",
            "action": "search",
            "timing_ms": {"planner": 12, "execution": 301, "total": 314},
        }]
        dumped = json.dumps(logged, ensure_ascii=False)
        assert "секретный запрос" not in dumped
        assert "+7 999" not in dumped
        assert "raw_payload" not in dumped

    asyncio.run(scenario())


def test_jivo_runtime_failure_logs_safe_v0_validation_errors(tmp_path, monkeypatch):
    async def scenario() -> None:
        app = make_app(tmp_path)
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))
        logged: list[dict[str, Any]] = []

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "ok": False,
                "answer": mod.SAFE_UPSTREAM_ERROR_TEXT,
                "intent": "safe_upstream_fallback",
                "error_type": "invalid_answer_output",
                "handoff_to_operator": False,
                "meta": {
                    "runtime": "v0",
                    "trace": {
                        "error_code": "invalid_answer_output",
                        "decision_action": "search",
                        "call_counts": {"scenario_search": 1, "answer": 1, "raw": 99},
                        "validation_errors": [
                            "answer_option_0_unknown_card: Секретный ЖК +7 999 123-45-67 https://secret.example/x",
                            "raw client secret text +7 999 123-45-67",
                            *[f"answer_extra_{idx}" for idx in range(20)],
                        ],
                        "raw_model_output": "Секретный ЖК +7 999 123-45-67",
                        "query": "секретный запрос",
                    },
                },
            }

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        monkeypatch.setattr(mod, "_log_error_event", logged.append)
        await mod.process_jivo_client_message(app, payload(event_id="event-v0-runtime-failure", text="секретный запрос"))

        assert logged == [{
            "error_type": "v0_runtime_failure",
            "stage": "runtime_execution",
            "runtime": "v0",
            "error_code": "invalid_answer_output",
            "decision_action": "search",
            "call_counts": {"scenario_search": 1, "answer": 1},
            "validation_errors": ["answer_option_0_unknown_card", "validation_error", *[f"answer_extra_{idx}" for idx in range(10)]],
        }]
        assert len(logged[0]["validation_errors"]) == 12
        dumped = json.dumps(logged, ensure_ascii=False)
        for forbidden in ["секретный запрос", "Секретный ЖК", "+7 999", "secret.example", "raw_model_output", "query", "raw client secret text"]:
            assert forbidden not in dumped

    asyncio.run(scenario())


def test_jivo_success_logs_safe_v2_search_validation_report(tmp_path, monkeypatch):
    async def scenario() -> None:
        app = make_app(tmp_path)
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))
        logged: list[dict[str, Any]] = []

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "answer": "Нашла варианты.",
                "intent": "main_search",
                "handoff_to_operator": False,
                "meta": {
                    "runtime": "v3",
                    "trace": {
                        "search_validation": {
                            "stage": "search_validation_report",
                            "status": "invalid",
                            "errors": ["fact_0_violates_hard:location Зеленоград Химки +7 999 https://secret.example"],
                            "warnings": ["unknown_fact_fields_removed"],
                            "counts": {"facts": 2, "near": 1, "missing": 0, "errors": 1, "warnings": 1},
                            "raw_payload": {"name": "ЖК Секрет"},
                        },
                        "query": "секретный запрос",
                    },
                },
            }

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        monkeypatch.setattr(mod, "_log_error_event", logged.append)
        await mod.process_jivo_client_message(app, payload(event_id="event-v2-search-report", text="секретный запрос"))

        assert logged == [{
            "error_type": "search_validation_report",
            "stage": "search_validation",
            "runtime": "v3",
            "status": "invalid",
            "errors": ["fact_0_violates_hard"],
            "warnings": ["unknown_fact_fields_removed"],
            "counts": {"facts": 2, "near": 1, "missing": 0, "errors": 1, "warnings": 1},
        }]
        dumped = json.dumps(logged, ensure_ascii=False)
        for forbidden in ["секретный запрос", "Зеленоград", "Химки", "+7 999", "secret.example", "ЖК Секрет", "raw_payload", "query"]:
            assert forbidden not in dumped

    asyncio.run(scenario())


def test_jivo_success_logs_safe_v0_search_validation_report(tmp_path, monkeypatch):
    async def scenario() -> None:
        app = make_app(tmp_path)
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))
        logged: list[dict[str, Any]] = []

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "answer": "Нашла вариант.",
                "intent": "search",
                "handoff_to_operator": False,
                "meta": {
                    "runtime": "v0",
                    "trace": {
                        "search_validation": {
                            "status": "invalid",
                            "errors": ["fact_0_missing_hard_evidence:location Секретный ЖК +7 999"],
                            "warnings": [],
                            "counts": {"facts": 1, "near": 0, "missing": 0, "errors": 1, "warnings": 0},
                        }
                    },
                },
            }

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        monkeypatch.setattr(mod, "_log_error_event", logged.append)
        await mod.process_jivo_client_message(app, payload(event_id="event-v0-search-report", text="секретный запрос"))

        assert logged == [{
            "error_type": "search_validation_report",
            "stage": "search_validation",
            "runtime": "v0",
            "status": "invalid",
            "errors": ["fact_0_missing_hard_evidence"],
            "warnings": [],
            "counts": {"facts": 1, "near": 0, "missing": 0, "errors": 1, "warnings": 0},
        }]
        dumped = json.dumps(logged, ensure_ascii=False)
        assert "секретный запрос" not in dumped
        assert "Секретный ЖК" not in dumped
        assert "+7 999" not in dumped

    asyncio.run(scenario())


def test_same_session_different_events_are_serialized(monkeypatch):
    async def scenario() -> None:
        app = make_app()
        active = 0
        max_active = 0

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"ok": True, "answer": "Ответ", "handoff_to_operator": False}

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        await asyncio.gather(
            mod.process_jivo_client_message(app, payload(event_id="event-1", chat_id="same")),
            mod.process_jivo_client_message(app, payload(event_id="event-2", chat_id="same")),
        )

        assert max_active == 1

    asyncio.run(scenario())


def test_different_sessions_may_run_concurrently(monkeypatch):
    async def scenario() -> None:
        app = make_app()
        active = 0
        max_active = 0

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"ok": True, "answer": "Ответ", "handoff_to_operator": False}

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        await asyncio.gather(
            mod.process_jivo_client_message(app, payload(event_id="event-1", chat_id="one")),
            mod.process_jivo_client_message(app, payload(event_id="event-2", chat_id="two")),
        )

        assert max_active == 2

    asyncio.run(scenario())


def test_blank_event_id_is_not_deduped(monkeypatch):
    async def scenario() -> None:
        app = make_app()
        calls = 0

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"ok": True, "answer": f"Ответ {calls}", "handoff_to_operator": False}

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        await mod.process_jivo_client_message(app, payload(event_id=""))
        await mod.process_jivo_client_message(app, payload(event_id=""))

        assert calls == 2

    asyncio.run(scenario())


def test_start_reset_is_serialized_and_deduped() -> None:
    async def scenario() -> None:
        app = make_app()
        first = await mod.process_jivo_client_message(app, payload(event_id="start-1", text="/start"))
        second = await mod.process_jivo_client_message(app, payload(event_id="start-1", text="/start"))

        assert second == first
        assert first[0]["message"]["text"] == mod.JIVO_START_GREETING + "\n\nСейчас активна версия: V2."
        assert app["state_store"].states["jivo:site1:chat1:client-chat1"]["nmbot_v2"] == mod.ConversationState().to_dict()

    asyncio.run(scenario())


def test_api_reset_writes_canonical_v2_state() -> None:
    async def scenario() -> None:
        app = make_app()
        class Request:
            async def json(self):
                return {"user_id": "api:user-1"}

        request = Request()
        request.app = app
        response = await mod.handle_api_reset(request)

        assert response.status == 200
        assert app["state_store"].states["api:user-1"]["nmbot_v2"] == mod.ConversationState().to_dict()

    asyncio.run(scenario())


def test_api_reset_resets_only_active_namespace_and_preserves_inactive() -> None:
    async def scenario() -> None:
        app = make_app()
        user_id = "api:user-preserve"
        app["state_store"].states[user_id] = {
            "nmbot_v2": {"params": {"rooms": 2}},
            "nmbot_v0": {"params": {"rooms": 1}},
        }
        await app["runtime_version_store"].set("V0")

        class Request:
            def __init__(self, app):
                self.app = app

            async def json(self):
                return {"user_id": user_id}

        response = await mod.handle_api_reset(Request(app))

        assert response.status == 200
        state = app["state_store"].states[user_id]
        assert state["nmbot_v0"] == mod._canonical_v0_envelope()["nmbot_v0"]
        assert state["nmbot_v2"] == {"params": {"rooms": 2}}

    asyncio.run(scenario())


def test_runtime_version_store_defaults_v2_and_persists_reload(tmp_path) -> None:
    async def scenario() -> None:
        path = tmp_path / "runtime-version.json"
        first = mod.RuntimeVersionStore(path)
        assert await first.get() == "V2"
        assert not path.exists()
        assert await first.set("V0") == "V0"
        second = mod.RuntimeVersionStore(path)
        assert await second.get() == "V0"
        assert await second.set("V2") == "V2"
        assert await mod.RuntimeVersionStore(path).get() == "V2"

    asyncio.run(scenario())


def test_runtime_version_admin_endpoint_is_protected_and_changes_version(monkeypatch) -> None:
    class Request:
        def __init__(self, app, headers: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> None:
            self.app = app
            self.headers = headers or {}
            self._body = body or {}

        async def json(self):
            return self._body

    async def scenario() -> None:
        monkeypatch.setenv("NMBOT_API_TOKEN", "secret-token")
        app = make_app()

        unauthorized = await mod.handle_api_runtime_version(Request(app))
        assert unauthorized.status == 401

        authorized = Request(app, headers={"Authorization": "Bearer secret-token"})
        status = await mod.handle_api_runtime_version(authorized)
        assert json.loads(status.body) == {"ok": True, "runtime_version": "V2"}

        changed = await mod.handle_api_runtime_version_set(Request(app, headers={"X-NMBOT-API-Token": "secret-token"}, body={"runtime_version": "V0"}))
        assert json.loads(changed.body) == {"ok": True, "runtime_version": "V0", "previous_runtime_version": "V2"}

        changed_v3 = await mod.handle_api_runtime_version_set(Request(app, headers={"Authorization": "Bearer secret-token"}, body={"runtime_version": "V3"}))
        assert json.loads(changed_v3.body) == {"ok": True, "runtime_version": "V3", "previous_runtime_version": "V0"}

        invalid = await mod.handle_api_runtime_version_set(Request(app, headers={"Authorization": "Bearer secret-token"}, body={"runtime_version": "V9"}))
        assert invalid.status == 400

    asyncio.run(scenario())


def test_start_reset_uses_active_v0_namespace_and_dynamic_greeting() -> None:
    async def scenario() -> None:
        app = make_app()
        user_id = "jivo:site1:chat1:client-chat1"
        app["state_store"].states[user_id] = {
            "nmbot_v2": {"params": {"rooms": 2}},
            "nmbot_v0": {"params": {"rooms": 1}},
        }
        await app["runtime_version_store"].set("V0")
        response, status = await mod.process_jivo_client_message(app, payload(event_id="start-v0", text="/start"))

        assert status == 200
        assert response["event"] == "BOT_MESSAGE"
        assert response["message"]["text"] == mod.V0_START_GREETING + "\n\nСейчас активна версия: V0."
        state = app["state_store"].states["jivo:site1:chat1:client-chat1"]
        assert state["nmbot_v0"] == mod._canonical_v0_envelope()["nmbot_v0"]
        assert state["nmbot_v2"] == {"params": {"rooms": 2}}

    asyncio.run(scenario())


def test_start_version_commands_override_only_current_jivo_session() -> None:
    async def scenario() -> None:
        app = make_app()
        user_id = "jivo:site1:chat1:client-chat1"
        app["state_store"].states[user_id] = {
            "nmbot_v2": {"params": {"rooms": 2}},
            "nmbot_v0": {"params": {"rooms": 1}},
        }

        response, status = await mod.process_jivo_client_message(app, payload(event_id="start-0", text="/start_0"))
        assert status == 200
        assert response["message"]["text"].endswith("Сейчас активна версия: V0.")
        assert response["message"]["text"].startswith("Здравствуйте! Меня зовут Валерия")
        state = app["state_store"].states[user_id]
        assert state["runtime_version_override"] == "V0"
        assert state["nmbot_v0"] == mod._canonical_v0_envelope()["nmbot_v0"]
        assert state["nmbot_v2"] == {"params": {"rooms": 2}}

        response, status = await mod.process_jivo_client_message(app, payload(event_id="start-2", text="/start_2"))
        assert status == 200
        assert response["message"]["text"].endswith("Сейчас активна версия: V2.")
        assert response["message"]["text"].startswith("Здравствуйте! Меня зовут Ирина")
        state = app["state_store"].states[user_id]
        assert state["runtime_version_override"] == "V2"
        assert state["nmbot_v2"] == mod.ConversationState().to_dict()

        app["state_store"].states[user_id]["nmbot_v2"] = {"params": {"rooms": 3}}
        response, status = await mod.process_jivo_client_message(app, payload(event_id="start-3", text="/start_3"))
        assert status == 200
        assert response["message"]["text"].startswith("Здравствуйте! Меня зовут Светлана")
        assert response["message"]["text"].endswith("Сейчас активна версия: V3.")
        state = app["state_store"].states[user_id]
        assert state["runtime_version_override"] == "V3"
        assert state["nmbot_v2"] == mod.ConversationState().to_dict()

        response, status = await mod.process_jivo_client_message(app, payload(event_id="start-default", text="/start"))
        assert status == 200
        assert "runtime_version_override" not in app["state_store"].states[user_id]

    asyncio.run(scenario())


def test_start_2_journal_attributes_user_and_lifecycle_to_v2(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))
        await app["runtime_version_store"].set("V0")

        response, status = await mod.process_jivo_client_message(app, payload(event_id="start-2-journal", text="/start_2"))

        assert status == 200
        assert response["message"]["text"].endswith("Сейчас активна версия: V2.")
        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [(row["role"], row["event_type"], row.get("runtime_version")) for row in rows] == [
            ("user", "turn", "V2"),
            ("bot", "lifecycle", "V2"),
        ]

    asyncio.run(scenario())


def test_jivo_journal_uses_session_override_v2_for_user_and_bot(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        user_id = "jivo:site1:chat1:client-chat1"
        app["state_store"].states[user_id] = {"runtime_version_override": "V2", "nmbot_v2": {}}
        await app["runtime_version_store"].set("V0")
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "answer": "Ответ V2", "intent": "main_search", "handoff_to_operator": False}

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        await mod.process_jivo_client_message(app, payload(event_id="override-v2", text="Подбери квартиру"))

        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [row.get("runtime_version") for row in rows] == ["V2", "V2"]

    asyncio.run(scenario())


def test_jivo_journal_uses_global_v0_for_ordinary_turn(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        await app["runtime_version_store"].set("V0")
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "answer": "Ответ V0", "intent": "v0_turn", "handoff_to_operator": False}

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        await mod.process_jivo_client_message(app, payload(event_id="global-v0", text="Подбери квартиру"))

        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [row.get("runtime_version") for row in rows] == ["V0", "V0"]

    asyncio.run(scenario())


def test_jivo_api_prepare_marker_is_journaled_after_bot_message_is_built(monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app()
        order: list[str] = []
        journaled: list[dict[str, Any]] = []
        original_builder = mod.build_jivo_bot_message

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "answer": "Ответ",
                "intent": "main_search",
                "handoff_to_operator": False,
                "meta": {
                    "trace": {
                        "execution_path": {
                            "schema": "nmbot.execution_path.v1",
                            "path_id": "v2.turn.v1",
                            "stages": [{"stage_id": "v2.runtime_finalize", "status": "completed"}],
                        }
                    }
                },
            }

        def wrapped_builder(*args: Any, **kwargs: Any) -> dict[str, Any]:
            order.append("build_bot_message")
            return original_builder(*args, **kwargs)

        def fake_append_journal_event(**kwargs: Any) -> None:
            order.append(f"journal:{kwargs.get('role')}:{kwargs.get('event_type')}")
            journaled.append(kwargs)

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        monkeypatch.setattr(mod, "build_jivo_bot_message", wrapped_builder)
        monkeypatch.setattr(mod, "append_journal_event", fake_append_journal_event)

        response, status = await mod.process_jivo_client_message(app, payload(event_id="prepare-order", text="Подбери"))

        assert status == 200
        assert response["event"] == "BOT_MESSAGE"
        assert order == ["journal:user:turn", "build_bot_message", "journal:bot:turn"]
        bot_row = journaled[-1]
        assert bot_row["execution_path"]["path_id"] == "jivo.v2.turn.v1"
        assert bot_row["execution_path"]["stages"][-1] == {"stage_id": "jivo.api.prepare", "status": "completed"}

    asyncio.run(scenario())


def test_jivo_public_conversation_cannot_switch_runtime_version(monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app()
        calls: list[dict[str, Any]] = []

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"ok": True, "answer": "Остаюсь в обычном диалоге.", "intent": "test", "handoff_to_operator": False}

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        response, status = await mod.process_jivo_client_message(app, payload(event_id="public-switch", text="Переключи runtime на V0"))

        assert status == 200
        assert response["event"] == "BOT_MESSAGE"
        assert response["message"]["text"] == "Остаюсь в обычном диалоге."
        assert await app["runtime_version_store"].get() == "V2"
        assert calls and calls[0]["message"] == "Переключи runtime на V0"

    asyncio.run(scenario())

    asyncio.run(scenario())


def test_idle_session_lock_is_removed(monkeypatch):
    async def scenario() -> None:
        app = make_app()

        async def fake_run_chat(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "answer": "Ответ", "handoff_to_operator": False}

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)
        await mod.process_jivo_client_message(app, payload(event_id="event-1"))

        assert app["jivo_session_locks"].size == 0

    asyncio.run(scenario())


def test_run_chat_standalone_phone_queues_callback_without_handoff_or_llm(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        ask_calls = 0
        planner_calls: list[dict[str, Any]] = []
        patch_planner(monkeypatch, {"dialog_action": "new_search", "confidence": 1.0}, planner_calls)
        state = mod._default_state()
        state["params"] = {"budget": 12000000, "phone": "+7 999 000-00-00", "nested": {"token": "secret", "ok": "family", "maybe_number": 79990000000}}
        state["selected_option"] = {"name": "ЖК Тайный", "price_range": "от 12 млн", "why_family": "позвонить +7 999 111-11-11", "link": "https://example.test/jk"}
        state["visible_options"] = [{"name": "ЖК Видимый", "raw_id": "raw-1", "price_range": "от 13 млн", "metro": {"phone": "+7 999 222-22-22", "name": "Сокол"}}]
        state["last_bot_question"] = "оставьте +7 999 333-33-33"
        app["state_store"].states["u-phone"] = state

        class FakeClient:
            async def ensure_session(self) -> None:
                raise AssertionError("phone callback must not call planner session")

            async def ask(self, *args: Any, **kwargs: Any):
                nonlocal ask_calls
                ask_calls += 1
                raise AssertionError("phone privacy guard must bypass ask")

        app["overmind_client"] = FakeClient()
        result = await mod.run_chat(
            app,
            user_id="u-phone",
            message="+7 999 123-45-67",
            channel="jivo",
            meta={"event_id": "event-phone", "chat_id": "raw-chat", "client_id": "raw-client", "sender_name": "Иван"},
        )

        assert result["ok"] is True
        assert result["intent"] == "callback_queued"
        assert result["awaiting_phone"] is False
        assert result["handoff_to_operator"] is False
        assert result["crm_callback"]["status"] == "queued"
        assert "+7" not in repr(result)
        assert "9991234567" not in repr(result)
        assert ask_calls == 0
        assert planner_calls == []
        state = app["state_store"].states["u-phone"]
        assert set(state) == {"nmbot_v2"}
        assert state["nmbot_v2"].get("contact_consent") is True
        assert state["nmbot_v2"].get("pending_followup") is None
        assert "last_phone" not in repr(state)

        files = list((tmp_path / "outbox").glob("*.json"))
        assert len(files) == 1
        assert not any("999" in path.name for path in files)
        assert oct(os.stat(tmp_path / "outbox").st_mode & 0o777) == "0o700"
        assert oct(os.stat(files[0]).st_mode & 0o777) == "0o600"
        stored = json.loads(files[0].read_text(encoding="utf-8"))
        assert stored["phone"] == "+79991234567"
        assert stored["contact"] == {"name": "Иван", "phone": "+79991234567"}
        assert "+7 999" not in repr(stored["context"])
        assert "raw-1" not in repr(stored["context"])
        assert "secret" not in repr(stored["context"])
        assert stored["context"]["runtime"] == "v2"
        assert stored["context"]["channel"] == "jivo"
        # The durable callback context must not retain raw Jivo metadata.  Its
        # presence/absence is represented by safe booleans in the outbox
        # contract, so an empty compatibility ``meta`` object is not required.
        assert "meta" not in stored["context"]

    asyncio.run(scenario())


def test_phone_callback_duplicate_and_jivo_never_invites_agent(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        planner_calls: list[dict[str, Any]] = []
        patch_planner(monkeypatch, {"dialog_action": "new_search", "confidence": 1.0}, planner_calls)

        class FakeClient:
            async def ensure_session(self) -> None:
                raise AssertionError("phone callback must not call planner session")

            async def ask(self, *args: Any, **kwargs: Any):
                raise AssertionError("phone callback must not call LLM")

        app["overmind_client"] = FakeClient()
        first_result = await mod.run_chat(app, user_id="u-phone", message="+7 999 123-45-67", channel="jivo", meta={"event_id": "same-event", "sender_name": "Иван"})
        second_result = await mod.run_chat(app, user_id="u-phone", message="+7 999 123-45-67", channel="jivo", meta={"event_id": "same-event", "sender_name": "Иван"})
        assert first_result["crm_callback"]["status"] == "queued"
        assert second_result["crm_callback"]["status"] == "duplicate"
        assert len(list((tmp_path / "outbox").glob("*.json"))) == 1

        response_payload, status = await mod._process_jivo_client_message_uncached(
            app,
            payload(event_id="jivo-phone", text="+7 999 123-45-67"),
            "jivo:site1:chat1:client-chat1",
        )
        assert status == 200
        assert response_payload["event"] == "BOT_MESSAGE"
        assert response_payload["event"] != "INVITE_AGENT"
        assert planner_calls == []

    asyncio.run(scenario())


def test_run_chat_budget_like_short_digits_reaches_overmind(monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app()
        seen: list[dict[str, Any]] = []
        planner_calls: list[dict[str, Any]] = []
        patch_planner(monkeypatch, {"operation": "search", "constraints_delta": {"hard": {"max_price": 200000}}, "confidence": 0.95}, planner_calls)

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
                seen.append(request_data)
                if request_data.get("_payload_stage") == "conversation_answer":
                    return json.dumps({"intro": "Вот подборка.", "options": [{"name": "Лучи", "facts": "до 200к."}], "missing_note": "", "final_question": "Показать подробнее?"}, ensure_ascii=False), {"ok": True}
                return json.dumps({"facts": [{"name": "Лучи", "min_price": 200000}], "near": [], "missing": [], "params": {"max_price": 200000}, "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "requested_field_priorities": [], "relaxation_audit": []}}, ensure_ascii=False), {"ok": True}

        app["overmind_client"] = FakeClient()
        result = await mod.run_chat(app, user_id="u-budget", message="до 200к", channel="jivo")

        assert result["ok"] is True
        assert result["intent"] == "main_search"
        assert len(planner_calls) == 1
        assert [item.get("_payload_stage") for item in seen] == ["main_search", "main_search", "main_search"]
        assert "до 200к" in seen[0]["query"]
        assert '"count": 2' in seen[1]["query"]
        assert '"excluded_names": ["Лучи"]' in seen[1]["query"]
        third_envelope = json.loads(seen[2]["query"].split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
        third_params = json.loads(seen[2]["query"].split("Текущие параметры: ", 1)[1].split("\n", 1)[0])
        assert third_envelope["count"] == 1
        assert third_params["preferences"] == {"format": "full_card"}

    asyncio.run(scenario())


def test_run_chat_room_check_offer_does_not_start_phone_collection(monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app()
        calls: list[dict[str, Any]] = []
        patch_planner(monkeypatch, {"operation": "search", "constraints_delta": {"hard": {"rooms": 2}}, "confidence": 0.95}, calls)
        gateway_payloads: list[dict[str, Any]] = []

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
                gateway_payloads.append(request_data)
                return json.dumps(
                    {
                        "facts": [],
                        "near": [],
                        "missing": ["rooms"],
                        "params": {"rooms": 2},
                        "diagnostics": {
                            "mcp_tool": "novostroym/get_flat_info",
                            "requested_field_priorities": [],
                            "relaxation_audit": [],
                        },
                    },
                    ensure_ascii=False,
                ), {"ok": True}

        app["overmind_client"] = FakeClient()
        result = await mod.run_chat(app, user_id="u-room-offer", message="нужна двушка", channel="jivo")

        assert result["ok"] is True
        assert result["intent"] == "main_search"
        assert result["awaiting_phone"] is False
        assert result["handoff_to_operator"] is False
        assert calls
        assert [payload.get("_payload_stage") for payload in gateway_payloads] == ["main_search"]
        assert "двушка" in gateway_payloads[0]["query"]
        assert result["answer"].strip()
        assert "телефон" not in result["answer"].lower()
        for forbidden in ("mcp", "структурн", "карточ", "evidence"):
            assert forbidden not in result["answer"].lower()
        state = app["state_store"].states["u-room-offer"]
        assert set(state) == {"nmbot_v2"}
        assert state["nmbot_v2"].get("pending_followup") is None
        assert state["nmbot_v2"]["params"] == {"rooms": 2}
        assert state["nmbot_v2"].get("visible_options") in (None, [])

    asyncio.run(scenario())


def test_run_chat_operator_contact_action_sets_awaiting_phone(monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app()
        patch_planner(monkeypatch, {"operation": "operator", "confidence": 0.95})

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def ask(self, query: str, **kwargs: Any):
                raise AssertionError("operator_live_check must not run main_search")

        app["overmind_client"] = FakeClient()
        result = await mod.run_chat(app, user_id="u-operator", message="куда звонить?", channel="jivo")

        assert result["ok"] is True
        assert result["intent"] == "operator_offer"
        assert result["awaiting_phone"] is False
        assert result["handoff_to_operator"] is False
        state = app["state_store"].states["u-operator"]
        assert set(state) == {"nmbot_v2"}
        assert state["nmbot_v2"]["pending_followup"] == "contact_name"
        assert state["nmbot_v2"]["operator_offered"] is True

    asyncio.run(scenario())


def test_run_chat_passes_only_safe_dialog_context_for_operator_acceptance(monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app()
        state = mod._default_state()
        state["last_bot_question"] = "Хотите, чтобы специалист проверил условия?"
        state["last_offer_type"] = "operator_for_selected"
        state["params"] = {"phone": "+79991234567", "rooms": 2}
        app["state_store"].states["u-context"] = state
        planner_calls: list[dict[str, Any]] = []
        patch_planner(monkeypatch, {"operation": "operator", "confidence": 0.96}, planner_calls)
        ask_calls = 0

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def ask(self, query: str, **kwargs: Any):
                nonlocal ask_calls
                ask_calls += 1
                raise AssertionError("operator_live_check is forbidden and must not run main_search")

        app["overmind_client"] = FakeClient()
        result = await mod.run_chat(app, user_id="u-context", message="уточни", channel="jivo")

        assert result["intent"] == "operator_offer"
        assert result["turn_decision"]["action"] == "offer_operator"
        assert ask_calls == 0
        assert len(planner_calls) == 1
        planner_state = planner_calls[0]["state"]
        assert planner_state["last_bot_question"] == "Хотите, чтобы специалист проверил условия?"
        assert "+799" not in repr(planner_state)
        assert "9991234567" not in repr(planner_state)
        assert "client-secret" not in repr(planner_state)
        saved = app["state_store"].states["u-context"]
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"]["pending_followup"] == "contact_name"

    asyncio.run(scenario())


def test_recover_dialogue_increments_repeats_and_resets_after_success(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        planner_calls: list[dict[str, Any]] = []
        plans = [
            {"operation": "freeform", "confidence": 0.9, "clarification_question": "Подскажите, квартиру смотрим для жизни, семьи или инвестиций?"},
            {"operation": "freeform", "confidence": 0.9, "clarification_question": "Подскажите, квартиру смотрим для жизни, семьи или инвестиций?"},
            {"operation": "search", "constraints_delta": {"hard": {"rooms": 2}}, "confidence": 0.95},
        ]

        async def fake_plan(session: Any, **kwargs: Any) -> dict[str, Any]:
            planner_calls.append(kwargs)
            return plans.pop(0)

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)
        gateway_calls: list[dict[str, Any]] = []

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
                gateway_calls.append(request_data)
                if request_data.get("_payload_stage") == "conversation_answer":
                    return json.dumps({"intro": "Вот подборка.", "options": [{"name": "Лучи", "facts": "двухкомнатная."}], "missing_note": "", "final_question": "Показать подробнее?"}, ensure_ascii=False), {"ok": True}
                return json.dumps({"facts": [{"name": "Лучи", "rooms": 2, "min_price": 12_000_000}], "near": [], "missing": [], "params": {"rooms": 2}, "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "requested_field_priorities": [], "relaxation_audit": []}}, ensure_ascii=False), {"ok": True}

        app["overmind_client"] = FakeClient()
        first = await mod.run_chat(app, user_id="u-recover", message="не понял", channel="jivo")
        second = await mod.run_chat(app, user_id="u-recover", message="что", channel="jivo")
        third = await mod.run_chat(app, user_id="u-recover", message="двушка", channel="jivo")

        assert first["intent"] == "freeform"
        assert second["intent"] == "freeform"
        assert third["intent"] == "main_search"
        assert len(planner_calls) == 3
        assert [payload.get("_payload_stage") for payload in gateway_calls] == ["main_search", "main_search", "main_search"]
        assert '"count": 2' in gateway_calls[1]["query"]
        assert '"excluded_names": ["Лучи"]' in gateway_calls[1]["query"]
        third_envelope = json.loads(gateway_calls[2]["query"].split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
        third_params = json.loads(gateway_calls[2]["query"].split("Текущие параметры: ", 1)[1].split("\n", 1)[0])
        assert third_envelope["count"] == 1
        assert third_params["preferences"] == {"format": "full_card"}
        saved = app["state_store"].states["u-recover"]
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"]["params"] == {"rooms": 2}
        assert saved["nmbot_v2"].get("pending_followup") is None

    asyncio.run(scenario())


def test_current_options_family_mortgage_keeps_options_and_forbids_new_search(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        state = mod._default_state()
        visible = [
            {"name": "ЖК Семейный", "price_range": "от 12 млн", "client_id": "raw-client", "phone": "+79991234567"},
            {"name": "ЖК Парковый", "price_range": "от 13 млн", "raw_id": "secret-2"},
        ]
        state["visible_options"] = visible
        state["last_options"] = list(visible)
        state["dialog_window"] = [
            {"role": "user", "text": "лишняя старая реплика"},
            {"role": "bot", "text": "Вот два варианта: ЖК Семейный и ЖК Парковый."},
            {"role": "user", "text": "мой телефон +79991234567 и почта raw@example.com"},
        ]
        app["state_store"].states["u-current"] = state
        planner_calls: list[dict[str, Any]] = []
        patch_planner(monkeypatch, {"dialog_action": "consultation_answer", "mode": "conversation", "confidence": 0.94, "visible_options_policy": "keep"}, planner_calls)
        ask_calls = 0

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def ask(self, query: str, **kwargs: Any):
                nonlocal ask_calls
                ask_calls += 1
                raise AssertionError("current-options forbidden decision must not run main_search")

        app["overmind_client"] = FakeClient()
        result = await mod.run_chat(app, user_id="u-current", message="а для семейной ипотеки подойдут?", channel="jivo")

        assert result["intent"] == "answer_current_options"
        assert result["turn_decision"] == {"stage": "current_options", "action": "answer_from_current_options"}
        assert len(planner_calls) == 1
        assert ask_calls == 0
        saved = app["state_store"].states["u-current"]
        assert set(saved) == {"nmbot_v2"}
        assert [option["name"] for option in saved["nmbot_v2"]["visible_options"]] == ["ЖК Семейный", "ЖК Парковый"]
        assert "Семейный" in result["answer"] or "Парковый" in result["answer"]
        safe_state = planner_calls[0]["state"]
        assert "+79991234567" not in repr(safe_state)
        assert "raw@example.com" not in repr(safe_state)
        assert "raw-client" not in repr(safe_state)
        assert "secret-2" not in repr(safe_state)
        assert [option["name"] for option in safe_state["visible_options"]] == ["ЖК Семейный", "ЖК Парковый"]
        assert safe_state["current_options_scope"] == "all"

    asyncio.run(scenario())


def test_current_options_reject_operator_keeps_selected_context_without_search(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        state = mod._default_state()
        state["primary_intent"] = "life"
        state["params"] = {"purpose": "life", "rooms": 2}
        state["selected_option"] = {"name": "ЖК Береговой", "price_range": "от 17 млн", "client_id": "raw-client"}
        state["visible_options"] = [dict(state["selected_option"])]
        state["last_options"] = [dict(state["selected_option"])]
        state["last_bot_question"] = "Хотите, позову оператора проверить актуальные квартиры по ЖК Береговой?"
        state["last_offer_type"] = "operator_for_selected"
        state["last_answer_kind"] = "operator_handoff"
        state["pending_followup"] = {"type": "operator_offer", "option_name": "ЖК Береговой", "raw_payload": "secret"}
        state["dialog_window"] = [
            {"role": "bot", "text": "Могу позвать оператора по ЖК Береговой. Хотите оставить номер?"},
        ]
        app["state_store"].states["u-no-operator"] = state
        planner_calls: list[dict[str, Any]] = []
        patch_planner(monkeypatch, {"dialog_action": "consultation_answer", "mode": "conversation", "confidence": 0.91, "visible_options_policy": "keep"}, planner_calls)
        ask_calls = 0

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def ask(self, *args: Any, **kwargs: Any):
                nonlocal ask_calls
                ask_calls += 1
                raise AssertionError("rejecting pending operator offer must not run main_search")

        app["overmind_client"] = FakeClient()
        result = await mod.run_chat(app, user_id="u-no-operator", message="нет", channel="jivo")

        assert result["intent"] == "answer_current_options"
        assert result["turn_decision"]["action"] == "answer_from_current_options"
        assert ask_calls == 0
        safe_state = planner_calls[0]["state"]
        assert safe_state["pending_followup"] == {"type": "operator_offer"}
        assert safe_state["selected_option"]["name"] == "ЖК Береговой"
        assert "secret" not in repr(safe_state)
        assert "raw-client" not in repr(safe_state)
        saved = app["state_store"].states["u-no-operator"]
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"]["selected_option_name"] == "ЖК Береговой"
        assert [option["name"] for option in saved["nmbot_v2"]["visible_options"]] == ["ЖК Береговой"]

    asyncio.run(scenario())


def test_current_options_operator_why_question_gets_recent_context_and_selected_card(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        state = mod._default_state()
        state["primary_intent"] = "life"
        state["selected_option"] = {"name": "ЖК Событие", "price_range": "от 19 млн", "phone": "+79990001122"}
        state["visible_options"] = [dict(state["selected_option"])]
        state["last_options"] = [dict(state["selected_option"])]
        state["pending_followup"] = {"type": "operator_offer", "option_name": "ЖК Событие"}
        state["dialog_window"] = [
            {"role": "user", "text": "расскажи про ЖК Событие"},
            {"role": "bot", "text": "По ЖК Событие могу позвать оператора проверить наличие."},
        ]
        app["state_store"].states["u-why-operator"] = state
        planner_calls: list[dict[str, Any]] = []
        patch_planner(monkeypatch, {"dialog_action": "consultation_answer", "mode": "conversation", "confidence": 0.93, "visible_options_policy": "keep"}, planner_calls)
        ask_calls = 0

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def ask(self, *args: Any, **kwargs: Any):
                nonlocal ask_calls
                ask_calls += 1
                raise AssertionError("operator explanation must stay in answer_current_options")

        app["overmind_client"] = FakeClient()
        result = await mod.run_chat(app, user_id="u-why-operator", message="А зачем мне оператор?", channel="jivo")

        assert result["intent"] == "answer_current_options"
        assert ask_calls == 0
        safe_state = planner_calls[0]["state"]
        assert safe_state["selected_option"]["name"] == "ЖК Событие"
        assert safe_state["pending_followup"] == {"type": "operator_offer"}
        assert "+79990001122" not in repr(safe_state)
        saved = app["state_store"].states["u-why-operator"]
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"]["selected_option_name"] == "ЖК Событие"
        assert [option["name"] for option in saved["nmbot_v2"]["visible_options"]] == ["ЖК Событие"]

    asyncio.run(scenario())


def test_planner_fallback_on_current_options_is_conservative_recovery(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        state = mod._default_state()
        visible = [{"name": "ЖК Семейный", "price_range": "от 12 млн"}]
        state["visible_options"] = visible
        state["last_options"] = list(visible)
        app["state_store"].states["u-invalid"] = state
        planner_calls: list[dict[str, Any]] = []
        patch_planner(
            monkeypatch,
            {"dialog_action": "new_search", "confidence": 0.1, "fallback_used": True, "clarification_question": "Уточните, что именно проверить по текущим вариантам?"},
            planner_calls,
        )
        ask_calls = 0
        presenter_calls = 0

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def ask(self, query: str, **kwargs: Any):
                nonlocal ask_calls
                ask_calls += 1
                raise AssertionError("fallback planner must not rebuild current options")

            async def explain_consultation_followup(self, **kwargs: Any):
                nonlocal presenter_calls
                presenter_calls += 1
                raise AssertionError("fallback planner is recovery, not consultation")

        app["overmind_client"] = FakeClient()
        result = await mod.run_chat(app, user_id="u-invalid", message="а для семейной ипотеки подойдут?", channel="jivo")

        assert result["intent"] == "freeform"
        assert result["turn_decision"] == {"stage": "freeform", "action": "freeform"}
        assert len(planner_calls) == 1
        assert ask_calls == 0
        assert presenter_calls == 0
        saved = app["state_store"].states["u-invalid"]
        assert set(saved) == {"nmbot_v2"}
        assert [option["name"] for option in saved["nmbot_v2"]["visible_options"]] == ["ЖК Семейный"]

    asyncio.run(scenario())


def test_compare_current_options_uses_v2_current_options_without_search(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        state = mod._default_state()
        state["visible_options"] = [{"name": "ЖК Первый"}, {"name": "ЖК Второй"}]
        app["state_store"].states["u-compare"] = state
        patch_planner(monkeypatch, {"dialog_action": "compare_options", "confidence": 0.95, "visible_options_policy": "keep"})

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def ask(self, *args: Any, **kwargs: Any):
                raise AssertionError("compare_options over current list must not search")

            async def explain_consultation_followup(self, **kwargs: Any):
                return "Сравнение текущих вариантов", {}

        app["overmind_client"] = FakeClient()
        result = await mod.run_chat(app, user_id="u-compare", message="сравни их", channel="jivo")
        assert result["intent"] == "answer_current_options"
        assert "Первый" in result["answer"]
        assert "Второй" in result["answer"]
        saved = app["state_store"].states["u-compare"]
        assert set(saved) == {"nmbot_v2"}
        assert [option["name"] for option in saved["nmbot_v2"]["visible_options"]] == ["ЖК Первый", "ЖК Второй"]

    asyncio.run(scenario())


def test_exact_current_option_name_resolves_selected_before_planner_and_preserves_options(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        state = mod._default_state()
        visible = [
            {"name": "ЖК Небо", "price_range": "от 14 млн", "client_id": "raw-client"},
            {"name": "ЖК Берег", "price_range": "от 16 млн"},
        ]
        state["visible_options"] = visible
        state["last_options"] = list(visible)
        app["state_store"].states["u-exact-option"] = state
        planner_states: list[dict[str, Any]] = []
        ask_calls = 0

        async def fake_plan(session: Any, **kwargs: Any) -> dict[str, Any]:
            planner_states.append(kwargs["state"])
            assert kwargs["state"]["selected_option"]["name"] == "ЖК Небо"
            return TestCanonicalPlannerContract.canonical_plan(
                action="answer_current_options",
                dialog_action="select_option",
                intent="family",
                intent_policy="keep",
                target="current_options",
                search_policy="forbidden",
                scope="one",
                selected_option_name="ЖК Небо",
                clarification_question="",
            )

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def ask(self, *args: Any, **kwargs: Any):
                nonlocal ask_calls
                ask_calls += 1
                raise AssertionError("current option reference must not force main_search")

            async def explain_consultation_followup(self, **kwargs: Any):
                return "По ЖК Небо проверю актуальные трёшки и школу в рамках текущей подборки.", {}

        app["overmind_client"] = FakeClient()
        result = await mod.run_chat(app, user_id="u-exact-option", message="А в ЖК «Небо» есть сейчас 3-комнатные и школа подтверждена?", channel="jivo")

        assert result["intent"] == "selected_object"
        assert result["turn_decision"]["action"] == "answer_selected_option"
        assert result["selected_option"] == "ЖК Небо"
        assert ask_calls == 0
        assert len(planner_states) == 1
        saved = app["state_store"].states["u-exact-option"]
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"]["selected_option_name"] == "ЖК Небо"
        assert [option["name"] for option in saved["nmbot_v2"]["visible_options"]] == ["ЖК Небо", "ЖК Берег"]

    asyncio.run(scenario())


def test_ambiguous_partial_or_too_short_option_reference_does_not_select() -> None:
    state = mod._default_state()
    state["visible_options"] = [{"name": "ЖК Семейный"}, {"name": "ЖК Парковый"}]

    mod._resolve_exact_current_option_reference(state, "Семейный актуален?")
    assert state.get("selected_option") in (None, {})

    mod._resolve_exact_current_option_reference(state, "Расскажи про ЖК Семейный и ЖК Парковый")
    assert state.get("selected_option") in (None, {})

    short_state = mod._default_state()
    short_state["visible_options"] = [{"name": "ЖК A"}]
    mod._resolve_exact_current_option_reference(short_state, "ЖК A подходит?")
    assert short_state.get("selected_option") in (None, {})


def test_typo_unique_current_option_reference_selects_safely() -> None:
    state = mod._default_state()
    state["visible_options"] = [
        {"name": "Кронштадтский 9", "price_range": "от 18 млн"},
        {"name": "ЖК Береговой", "price_range": "от 21 млн"},
    ]

    mod._resolve_exact_current_option_reference(state, "а что за кроштатский")

    assert state["selected_option"]["name"] == "Кронштадтский 9"
    assert state["pending_followup"]["match"] == "fuzzy"


def test_planner_exception_recovers_without_search(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)

        async def broken_plan(*args: Any, **kwargs: Any):
            raise RuntimeError("planner unavailable")

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", broken_plan)

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def ask(self, *args: Any, **kwargs: Any):
                raise AssertionError("planner failure must not fall through to search")

        app["overmind_client"] = FakeClient()
        result = await mod.run_chat(app, user_id="u-planner-fail", message="что-то непонятное", channel="jivo")
        assert result["ok"] is False
        assert result["error"] == "runtime_config_error"
        assert result["error_code"] == "v2_runtime_exception"
        assert result["meta"]["runtime_adapter"]["detail"] == "RuntimeError"
        assert app["state_store"].saved == []

    asyncio.run(scenario())


class TestCanonicalPlannerContract:
    @staticmethod
    def canonical_plan(**overrides: Any) -> dict[str, Any]:
        plan: dict[str, Any] = {
            "dialog_action": "ask_clarification",
            "mode": "search_action",
            "confidence": 0.95,
            "params_delta": {},
            "selected_option_action": "keep",
            "selected_option_name": None,
            "rejected_options_add": [],
            "visible_options_policy": "keep",
            "numeric_choice_policy": "reject",
            "mcp_request_patch": None,
            "clarification": "Уточните, что для вас важнее?",
            "clarification_question": "Уточните, что для вас важнее?",
            "reason": "typed test",
            "fallback_used": False,
            "action": "recover_dialogue",
            "intent": "unknown",
            "intent_policy": "keep",
            "target": "none",
            "search_policy": "forbidden",
            "constraints_patch": {"hard": {}, "preferences": {}, "unknown": {}},
            "facets": {},
            "operator_contact": {"requested": False, "consent": "none"},
            "search_profile": "none",
            "missing_fields": [],
            "clarification_fields": [],
            "canonical_valid": True,
            "canonical_errors": [],
        }
        plan.update(overrides)
        if plan.get("action") == "search":
            if plan.get("dialog_action") == "ask_clarification":
                plan["dialog_action"] = "new_search"
            plan.setdefault("scope", "unknown")
            plan["scope"] = "unknown"
            if plan.get("intent_policy") == "keep":
                plan["intent_policy"] = "set"
            if plan.get("intent") == "unknown":
                plan["intent"] = "family"
            if plan.get("search_profile") == "none":
                plan["search_profile"] = "generic"
        if plan.get("action") == "answer_current_options" and "scope" not in overrides:
            plan["scope"] = "all"
            if plan.get("dialog_action") == "ask_clarification":
                plan["dialog_action"] = "consultation_answer"
        return plan

    def test_known_investment_purpose_reasked_fails_closed_without_search(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            state = mod._default_state()
            state["params"] = {"purpose": "investment", "rooms": 2}
            app["state_store"].states["u-canonical-known"] = state
            patch_planner(
                monkeypatch,
                self.canonical_plan(action="clarify", intent="investment", intent_policy="keep", target="none", search_policy="forbidden", clarification_fields=["purpose"]),
            )

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def ask(self, *args: Any, **kwargs: Any):
                    raise AssertionError("known typed clarification must fail closed before client.ask")

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-canonical-known", message="для инвестиций", channel="jivo")
            assert result["intent"] == "freeform"
            assert result["turn_decision"] == {"stage": "freeform", "action": "freeform"}
            saved = app["state_store"].states["u-canonical-known"]
            assert set(saved) == {"nmbot_v2"}
            assert saved["nmbot_v2"]["params"] == {"purpose": "investment", "rooms": 2}

        asyncio.run(scenario())

    def test_known_family_purpose_is_primary_intent_and_known_field_in_planner_state(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            state = mod._default_state()
            state["params"] = {"purpose": "family", "rooms": 3}
            app["state_store"].states["u-family-known"] = state
            planner_calls: list[dict[str, Any]] = []
            patch_planner(
                monkeypatch,
                self.canonical_plan(action="recover_dialogue", intent="family", target="none", search_policy="forbidden"),
                planner_calls,
            )

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def ask(self, *args: Any, **kwargs: Any):
                    raise AssertionError("recovery must not search")

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-family-known", message="а школа рядом есть?", channel="jivo")

            assert result["intent"] == "freeform"
            planner_state = planner_calls[0]["state"]
            assert planner_state["primary_intent"] == "family"
            assert "primary_intent" in planner_state["known_fields"]
            assert "purpose" in planner_state["known_fields"]
            saved = app["state_store"].states["u-family-known"]
            assert set(saved) == {"nmbot_v2"}
            assert saved["nmbot_v2"]["params"]["purpose"] == "family"

        asyncio.run(scenario())

    def test_canonical_off_topic_executes_without_search_and_preserves_state(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            state = mod._default_state()
            state["params"] = {"location": "Сокол", "rooms": 2}
            selected = {"name": "ЖК Событие", "price_range": "от 19 млн"}
            state["selected_option"] = dict(selected)
            state["visible_options"] = [dict(selected)]
            state["last_options"] = [dict(selected)]
            state["pending_followup"] = {"type": "operator_offer", "option_name": "ЖК Событие", "raw_payload": "secret"}
            app["state_store"].states["u-offtopic"] = state
            planner_calls: list[dict[str, Any]] = []
            patch_planner(
                monkeypatch,
                self.canonical_plan(
                    operation="off_topic",
                    action="off_topic",
                    dialog_action="conversation_answer",
                    target="none",
                    search_policy="forbidden",
                    scope="unknown",
                    intent="unknown",
                    intent_policy="keep",
                    search_profile="none",
                    constraints_patch={"hard": {}, "preferences": {}, "unknown": {}},
                    clarification_question="",
                ),
                planner_calls,
            )

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def ask(self, *args: Any, **kwargs: Any):
                    raise AssertionError("off_topic must not call main search")

                async def explain_consultation_followup(self, **kwargs: Any):
                    raise AssertionError("off_topic must not call current-options presenter")

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-offtopic", message="расскажи анекдот", channel="jivo")

            assert result["ok"] is True
            assert result["intent"] == "off_topic"
            assert result["turn_decision"] == {"stage": "off_topic", "action": "answer_off_topic"}
            assert result["answer"].endswith("Вернёмся к подбору квартиры?")
            saved = app["state_store"].states["u-offtopic"]
            assert set(saved) == {"nmbot_v2"}
            assert saved["nmbot_v2"]["params"] == {"location": "Сокол", "rooms": 2}
            assert saved["nmbot_v2"]["selected_option_name"] == "ЖК Событие"
            assert [option["name"] for option in saved["nmbot_v2"]["visible_options"]] == ["ЖК Событие"]
            assert saved["nmbot_v2"].get("pending_followup") in (None, {})
            assert len(planner_calls) == 1

        asyncio.run(scenario())

    def test_hard_location_budget_search_reaches_client_ask_with_existing_context(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            state = mod._default_state()
            state["params"] = {"purpose": "family", "rooms": 2}
            app["state_store"].states["u-canonical-search"] = state
            patch_planner(
                monkeypatch,
                self.canonical_plan(
                    operation="search",
                    dialog_action="ask_clarification",
                    action="search",
                    intent="family",
                    intent_policy="keep",
                    target="new_search",
                    search_policy="required",
                    constraints_patch={"hard": {"location": ["Сокол"], "max_price": 18_000_000}, "preferences": {}, "unknown": {}},
                    clarification_question="",
                ),
            )
            seen: list[dict[str, Any]] = []

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
                    seen.append(request_data)
                    return json.dumps(
                        {
                            "facts": [{"name": "ЖК Сокол", "location": "Сокол", "rooms": 2, "price_min": 17_000_000, "max_price": 17_000_000}],
                            "near": [],
                            "missing": [],
                            "params": {"purpose": "family", "rooms": 2, "location": ["Сокол"], "max_price": 18_000_000},
                            "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "requested_field_priorities": [], "relaxation_audit": []},
                        },
                        ensure_ascii=False,
                    ), {"ok": True}

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-canonical-search", message="на Соколе до 18", channel="jivo")
            assert result["intent"] in {"main_search", "near_results"}
            assert len(seen) == 3
            def search_payload(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
                query = request["query"]
                envelope = json.loads(query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
                params = json.loads(query.split("Текущие параметры: ", 1)[1].split("\n", 1)[0])
                return envelope, params

            # Specific location uses internal candidate retrieval: the request to
            # MCP omits location, but keeps its no-client-relaxation audit marker.
            expected_retrieval_hard = {"max_price": 18_000_000, "rooms": 2}
            first_envelope, first_params = search_payload(seen[0])
            assert seen[0]["_payload_stage"] == "main_search"
            assert first_params["requested_hard"] == expected_retrieval_hard
            assert first_params["effective_hard"] == expected_retrieval_hard
            assert first_params["relaxation_audit"] == [{"field": "location", "mode": "internal_candidate_retrieval", "client_relaxation": False}]
            assert first_envelope["response_viewpoint"] == "family"
            assert seen[1]["_payload_stage"] == "main_search"
            second_envelope, second_params = search_payload(seen[1])
            assert second_envelope["count"] == 2
            assert second_params["excluded_names"] == ["ЖК Сокол"]
            # The third request is family-card enrichment after the search and
            # supplemental search complete. It is an exact-card lookup, not a
            # relaxation of the original user search.
            assert seen[2]["_payload_stage"] == "main_search"
            third_envelope, third_params = search_payload(seen[2])
            assert third_envelope["count"] == 1
            assert third_params["excluded_names"] == []
            assert third_params["requested_hard"] == {}
            assert third_params["effective_hard"] == {}
            assert third_params["preferences"] == {"format": "full_card"}
            assert app["state_store"].states["u-canonical-search"]["nmbot_v2"]["params"] == {"purpose": "family", "rooms": 2, "location": ["Сокол"], "max_price": 18_000_000}

        asyncio.run(scenario())

    def test_router_profile_flag_passes_valid_family_mortgage_profile(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            state = mod._default_state()
            app["state_store"].states["u-profile"] = state
            monkeypatch.setattr(mod, "ROUTER_PROFILES_ENABLED", True)
            patch_planner(
                monkeypatch,
                self.canonical_plan(
                    operation="search",
                    action="search",
                    intent="family",
                    target="new_search",
                    search_policy="required",
                    facets={"mortgage": True},
                    search_profile="mortgage",
                    clarification_question="",
                ),
            )
            seen: list[dict[str, Any]] = []

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
                    seen.append(request_data)
                    return json.dumps(
                        {
                            "facts": [{"name": "ЖК Семейный", "location": "Москва", "min_price": 12_000_000}],
                            "near": [],
                            "missing": [],
                            "params": {},
                            "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "requested_field_priorities": [], "relaxation_audit": []},
                        },
                        ensure_ascii=False,
                    ), {"ok": True}

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-profile", message="для семьи и по ипотеке", channel="jivo")
            assert result["intent"] == "main_search"
            assert seen[0]["_payload_stage"] == "main_search"
            assert '"response_viewpoint": "financing"' in seen[0]["query"]
            assert '"base_viewpoint": "life"' in seen[0]["query"]
            assert "family" in seen[0]["query"]
            assert "financing" in seen[0]["query"]

        asyncio.run(scenario())

    def test_successful_search_persists_canonical_primary_intent(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            app["state_store"].states["u-persist-intent"] = mod._default_state()
            patch_planner(
                monkeypatch,
                self.canonical_plan(
                    operation="search",
                    action="search",
                    intent="family",
                    intent_policy="set",
                    target="new_search",
                    search_policy="required",
                    clarification_question="",
                ),
            )

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
                    return json.dumps(
                        {
                            "facts": [{"name": "ЖК Семейный", "location": "Москва", "rooms": 3, "min_price": 12_000_000}],
                            "near": [],
                            "missing": [],
                            "params": {"rooms": 3},
                            "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "requested_field_priorities": [], "relaxation_audit": []},
                        },
                        ensure_ascii=False,
                    ), {"ok": True}

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-persist-intent", message="ищу для семьи", channel="jivo")

            assert result["intent"] == "main_search"
            saved = app["state_store"].states["u-persist-intent"]
            assert set(saved) == {"nmbot_v2"}
            assert saved["nmbot_v2"]["active_topic"] == "family"

        asyncio.run(scenario())

    def test_unknown_planner_intent_does_not_overwrite_known_investment_and_recovery_keeps_context(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            state = mod._default_state()
            state["primary_intent"] = "investment"
            state["params"] = {"purpose": "investment"}
            state["visible_options"] = [{"name": "Кронштадтский 9", "price_range": "от 18 млн"}]
            app["state_store"].states["u-invest-recover"] = state
            patch_planner(
                monkeypatch,
                self.canonical_plan(
                    action="clarify",
                    intent="unknown",
                    intent_policy="keep",
                    target="none",
                    search_policy="forbidden",
                    clarification_question="Вы ищете для жизни, для семьи или как инвестицию?",
                ),
            )

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def ask(self, *args: Any, **kwargs: Any):
                    raise AssertionError("known investment recovery must not search")

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-invest-recover", message="а что дальше", channel="jivo")

            assert result["intent"] == "freeform"
            saved = app["state_store"].states["u-invest-recover"]
            assert set(saved) == {"nmbot_v2"}
            assert saved["nmbot_v2"]["params"]["purpose"] == "investment"
            assert "для жизни" not in result["answer"].lower()

        asyncio.run(scenario())

    def test_yes_after_selected_option_routes_contextually_without_new_search(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            state = mod._default_state()
            state["primary_intent"] = "investment"
            state["params"] = {"purpose": "investment"}
            state["selected_option"] = {"name": "Кронштадтский 9", "price_range": "от 18 млн"}
            state["visible_options"] = [dict(state["selected_option"])]
            state["last_bot_question"] = "Разобрать этот ЖК подробнее?"
            app["state_store"].states["u-yes-selected"] = state
            planner_calls: list[dict[str, Any]] = []

            async def fake_plan(session: Any, **kwargs: Any) -> dict[str, Any]:
                planner_calls.append(kwargs)
                return self.canonical_plan(
                    operation="current_options",
                    action="answer_current_options",
                    dialog_action="consultation_answer",
                    intent="investment",
                    intent_policy="keep",
                    target="current_options",
                    search_policy="forbidden",
                    scope="one",
                    selected_option_name="Кронштадтский 9",
                )

            monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)
            ask_calls = 0

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def ask(self, *args: Any, **kwargs: Any):
                    nonlocal ask_calls
                    ask_calls += 1
                    raise AssertionError("contextual yes must not run main_search")

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-yes-selected", message="да", channel="jivo")

            assert result["intent"] == "selected_object"
            assert result["turn_decision"]["action"] == "answer_selected_option"
            assert ask_calls == 0
            assert len(planner_calls) == 1
            saved = app["state_store"].states["u-yes-selected"]
            assert set(saved) == {"nmbot_v2"}
            assert saved["nmbot_v2"]["params"]["purpose"] == "investment"
            assert saved["nmbot_v2"]["selected_option_name"] == "Кронштадтский 9"

        asyncio.run(scenario())

    def test_planner_receives_bounded_safe_prior_context_and_search_snapshot_is_saved(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            state = mod._default_state()
            state["dialog_window"] = [{"role": "bot", "text": "Вот варианты: Кронштадтский 9."}]
            state["visible_options"] = [{"name": "Кронштадтский 9", "client_id": "raw-client", "price_range": "от 18 млн"}]
            app["state_store"].states["u-safe-snapshot"] = state
            planner_calls: list[dict[str, Any]] = []

            async def fake_plan(session: Any, **kwargs: Any) -> dict[str, Any]:
                planner_calls.append(kwargs)
                return self.canonical_plan(operation="search", action="search", intent="investment", intent_policy="set", target="new_search", search_policy="required", clarification_question="")

            monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
                    search_payload = {
                        "facts": [{"name": "Кронштадтский 9", "price_range": "от 18 млн", "client_id": "raw-client", "min_price": 18_000_000}],
                        "near": [{"name": "Почти", "chat_id": "raw-chat", "min_price": 20_000_000}],
                        "params": {"purpose": "investment"},
                        "missing": ["rooms"],
                        "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "requested_field_priorities": [], "relaxation_audit": []},
                    }
                    return json.dumps(search_payload, ensure_ascii=False), {"ok": True}

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-safe-snapshot", message="ещё варианты", channel="jivo")

            assert result["intent"] in {"main_search", "near_results"}
            assert planner_calls[0]["last_response_text"] == "Вот варианты: Кронштадтский 9."
            assert "Кронштадтский 9" in planner_calls[0]["visible_response_text"]
            saved = app["state_store"].states["u-safe-snapshot"]
            snapshot = saved["nmbot_v2"]["last_search"]
            snapshot_text = json.dumps(snapshot, ensure_ascii=False)
            assert "facts" in snapshot
            assert "near" in snapshot
            assert snapshot["params"] == {}
            assert saved["nmbot_v2"]["active_topic"] == "investment"
            assert snapshot["missing"] == ["rooms"]
            assert "raw-client" not in snapshot_text
            assert "raw-chat" not in snapshot_text

        asyncio.run(scenario())

    def test_current_options_mortgage_avoids_search_and_preserves_options(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            state = mod._default_state()
            visible = [{"name": "ЖК Семейный"}, {"name": "ЖК Парковый"}]
            state["visible_options"] = visible
            state["last_options"] = list(visible)
            app["state_store"].states["u-canonical-current"] = state
            patch_planner(
                monkeypatch,
                self.canonical_plan(
                    operation="current_options",
                    action="answer_current_options",
                    dialog_action="consultation_answer",
                    intent="mortgage",
                    intent_policy="keep",
                    target="current_options",
                    search_policy="forbidden",
                    scope="all",
                    facets={"mortgage": True},
                    clarification_question="",
                ),
            )

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def ask(self, *args: Any, **kwargs: Any):
                    raise AssertionError("canonical current-options must not call client.ask")

                async def explain_consultation_followup(self, **kwargs: Any):
                    return "По текущим вариантам ипотеку надо проверять отдельно.", {}

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-canonical-current", message="а ипотека по ним есть?", channel="jivo")
            assert result["intent"] == "financing"
            assert result["turn_decision"]["action"] == "answer_from_current_options"
            assert result["turn_decision"] == {"stage": "current_options", "action": "answer_from_current_options"}
            saved = app["state_store"].states["u-canonical-current"]
            assert set(saved) == {"nmbot_v2"}
            assert [option["name"] for option in saved["nmbot_v2"]["visible_options"]] == ["ЖК Семейный", "ЖК Парковый"]

        asyncio.run(scenario())

    def test_known_investment_mortgage_facet_uses_consultation_context_not_renderer(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            state = mod._default_state()
            state["primary_intent"] = "investment"
            state["params"] = {"purpose": "investment", "primary_intent": "investment"}
            visible = [{"name": "ЖК Инвест", "price_range": "от 14 млн", "raw_id": "secret-1"}]
            state["visible_options"] = visible
            state["last_options"] = list(visible)
            app["state_store"].states["u-invest-mortgage"] = state
            patch_planner(
                monkeypatch,
                self.canonical_plan(
                    operation="current_options",
                    action="answer_current_options",
                    dialog_action="consultation_answer",
                    intent="mortgage",
                    intent_policy="keep",
                    target="current_options",
                    search_policy="forbidden",
                    facets={"mortgage": True},
                    scope="all",
                    clarification_question="",
                ),
            )
            ask_calls = 0

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def ask(self, *args: Any, **kwargs: Any):
                    nonlocal ask_calls
                    ask_calls += 1
                    raise AssertionError("mortgage facet over current options must not search")

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-invest-mortgage", message="ну может с ипотекой", channel="jivo")

            assert result["intent"] == "financing"
            assert result["turn_decision"]["action"] == "answer_from_current_options"
            assert ask_calls == 0
            assert "ипотек" in result["answer"].lower()
            assert "ЖК «Инвест»" in result["answer"]
            saved = app["state_store"].states["u-invest-mortgage"]
            assert set(saved) == {"nmbot_v2"}
            assert saved["nmbot_v2"]["params"]["purpose"] == "investment"
            assert [option["name"] for option in saved["nmbot_v2"]["visible_options"]] == ["ЖК Инвест"]
            assert "secret-1" not in repr(saved)

        asyncio.run(scenario())

    def test_cash_on_hand_then_mortgage_asks_clarification_without_mutating_budget(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            state = mod._default_state()
            state["primary_intent"] = "investment"
            state["params"] = {"purpose": "investment", "primary_intent": "investment", "max_price": 10_000_000}
            state["visible_options"] = [{"name": "ЖК Первый", "price_range": "от 12 млн"}]
            state["last_options"] = list(state["visible_options"])
            state["dialog_window"] = [
                {"role": "user", "text": "ну как то дорого, у меня 10 млн на руках"},
                {"role": "bot", "text": "Поняла, держим бюджет 10 млн."},
            ]
            app["state_store"].states["u-cash-mortgage"] = state
            patch_planner(
                monkeypatch,
                self.canonical_plan(
                    operation="current_options",
                    action="answer_current_options",
                    dialog_action="consultation_answer",
                    intent="mortgage",
                    intent_policy="keep",
                    target="current_options",
                    search_policy="forbidden",
                    facets={"mortgage": True},
                    scope="all",
                    clarification_question="",
                ),
            )
            ask_calls = 0

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def ask(self, *args: Any, **kwargs: Any):
                    nonlocal ask_calls
                    ask_calls += 1
                    raise AssertionError("ambiguous financing follow-up must not search")

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-cash-mortgage", message="с ипотекой", channel="jivo")

            assert result["intent"] == "financing"
            assert result["turn_decision"]["action"] == "answer_from_current_options"
            assert ask_calls == 0
            assert result["answer"].count("?") == 1
            assert "ЖК Первый" not in result["answer"]
            saved = app["state_store"].states["u-cash-mortgage"]
            assert set(saved) == {"nmbot_v2"}
            saved_params = saved["nmbot_v2"]["params"]
            assert saved_params["max_price"] == 10_000_000
            assert "down_payment" not in saved_params

        asyncio.run(scenario())

    def test_current_options_response_mode_priority_table(self) -> None:
        decision = mod.TurnDecision(action="answer_current_options", target="current_options", search_policy="forbidden")
        base = mod._default_state()
        base["primary_intent"] = "investment"
        base["visible_options"] = [{"name": "ЖК Один"}, {"name": "ЖК Два"}]

        operator_state = dict(base)
        operator_state["pending_followup"] = {"type": "operator_offer", "option_name": "ЖК Один"}
        assert mod._resolve_current_options_response_mode(state=operator_state, decision=decision, dialog_plan=self.canonical_plan(action="answer_current_options", dialog_action="reject_operator", intent="investment", intent_policy="keep"), user_text="нет").public()["reason"] == "operator_decline_or_pending_question"

        selected_state = dict(base)
        selected_state["selected_option"] = {"name": "ЖК Один"}
        selected_state["current_options_scope"] = "one"
        assert mod._resolve_current_options_response_mode(state=selected_state, decision=decision, dialog_plan=self.canonical_plan(action="answer_current_options", dialog_action="select_option", intent="investment", intent_policy="keep", scope="one", selected_option_name="ЖК Один"), user_text="про первый").public()["mode"] == "deterministic"

        assert mod._resolve_current_options_response_mode(state=base, decision=decision, dialog_plan=self.canonical_plan(action="answer_current_options", dialog_action="consultation_answer", intent="mortgage", intent_policy="keep", facets={"mortgage": True}), user_text="с ипотекой").public()["reason"] == "financing_facet"

        rental_state = dict(base)
        rental_state["primary_intent"] = "rental"
        assert mod._resolve_current_options_response_mode(state=rental_state, decision=decision, dialog_plan=self.canonical_plan(action="answer_current_options", intent="rental", intent_policy="keep"), user_text="все под аренду").public() == {"mode": "deterministic", "reason": "scenario_only_current_options", "scenario": "rental"}

        family_state = dict(base)
        family_state["primary_intent"] = "family"
        assert mod._resolve_current_options_response_mode(state=family_state, decision=decision, dialog_plan=self.canonical_plan(action="answer_current_options", intent="family", intent_policy="keep"), user_text="что по школам?").public()["mode"] == "consultation"

    def test_api_current_open_question_missing_sets_operator_consent_not_contact_capture(self) -> None:
        async def scenario() -> None:
            state = mod._default_state()
            state["visible_options"] = [{"name": "Мичуринский парк", "price": "от 14 млн рублей"}]
            state["selected_option"] = {"name": "Мичуринский парк", "price": "от 14 млн рублей"}
            decision = mod.TurnDecision(action="answer_current_options", target="current_options", search_policy="forbidden")
            answer, meta = await mod._answer_current_options(
                object(),
                user_text="Есть свободные квартиры?",
                state=state,
                decision=decision,
                dialog_plan={"open_question": True, "requested_facts": ["apartment_inventory"], "resolved_subject": "наличии"},
                fallback_text="",
            )

            assert meta["renderer"] == "open_question_operator_consent"
            assert answer.rstrip().endswith("В текущих данных это не подтверждено. Оператор сможет проверить. Передать оператору запрос?")
            assert "телефон" not in answer.casefold()
            assert "номер" not in answer.casefold()
            assert state["pending_followup"] == "selected_live_fact_consent"
            assert state["contact_flow"] == "normal"
            assert state["awaiting_phone"] is False
            assert state["operator_offered"] is True
            assert state["contact_consent"] is False

        asyncio.run(scenario())

    def test_unclear_recover_avoids_client_ask(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            patch_planner(monkeypatch, self.canonical_plan(action="recover_dialogue", intent="unknown", target="none", search_policy="forbidden"))

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def ask(self, *args: Any, **kwargs: Any):
                    raise AssertionError("canonical recover must not call client.ask")

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-canonical-recover", message="ну это самое", channel="jivo")
            assert result["intent"] == "freeform"

        asyncio.run(scenario())

    def test_malformed_canonical_fields_fail_closed_without_search(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            patch_planner(
                monkeypatch,
                self.canonical_plan(action="search", intent="investment", target="new_search", search_policy="required", constraints_patch={"hard": []}, canonical_valid=False, canonical_errors=["invalid_constraints_category"]),
            )

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def ask(self, *args: Any, **kwargs: Any):
                    raise AssertionError("invalid canonical plan must fail closed before client.ask")

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-canonical-invalid", message="ищу инвестиции", channel="jivo")
            assert result["intent"] == "freeform"
            assert result["turn_decision"] == {"stage": "freeform", "action": "freeform"}

        asyncio.run(scenario())

    def test_canonical_constraint_merge_precedence_and_preserves_existing(self) -> None:
        state = {"params": {"purpose": "family", "rooms": 2}}
        decision = mod.TurnDecision(action="search", target="new_search", search_policy="required")
        plan = self.canonical_plan(
            action="search",
            intent="family",
            target="new_search",
            search_policy="required",
            constraints_patch={
                "unknown": {"max_price": 30_000_000, "location": ["unknown"]},
                "preferences": {"max_price": 25_000_000, "mortgage": True},
                "hard": {"max_price": 18_000_000, "location": ["Сокол"]},
            },
        )

        merged = mod._params_with_canonical_search_constraints(state["params"], plan, decision, state)

        assert merged == {"purpose": "family", "rooms": 2, "max_price": 18_000_000, "mortgage": True, "location": ["Сокол"]}
        assert state["params"] == {"purpose": "family", "rooms": 2}

    def test_canonical_constraint_merge_rejects_sensitive_nested_and_unallowlisted(self) -> None:
        state = {"params": {"purpose": "life"}}
        decision = mod.TurnDecision(action="search", target="new_search", search_policy="required")
        plan = self.canonical_plan(
            action="search",
            intent="life",
            target="new_search",
            search_policy="required",
            constraints_patch={
                "hard": {
                    "location": ["Сокол"],
                    "phone": "+7 999 123-45-67",
                    "client_id": "raw-client",
                    "custom_nested": {"metro": "Сокол"},
                    "metro": {"name": "Сокол"},
                    "districts": ["САО", {"bad": "nested"}],
                },
                "preferences": {},
                "unknown": {},
            },
        )

        merged = mod._params_with_canonical_search_constraints(state["params"], plan, decision, state)

        assert merged == {"purpose": "life", "location": ["Сокол"]}

    def test_canonical_constraints_forbidden_decision_do_not_mutate_or_apply(self) -> None:
        state = {"params": {"purpose": "family"}}
        decision = mod.TurnDecision(action="recover_dialogue", target="none", search_policy="forbidden")
        plan = self.canonical_plan(
            action="recover_dialogue",
            target="none",
            search_policy="forbidden",
            constraints_patch={"hard": {"max_price": 18_000_000}, "preferences": {}, "unknown": {}},
        )

        merged = mod._params_with_canonical_search_constraints(state["params"], plan, decision, state)

        assert merged == {"purpose": "family"}
        assert state["params"] == {"purpose": "family"}

    def test_canonical_semantic_pair_mismatch_fails_closed(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            patch_planner(monkeypatch, self.canonical_plan(action="search", target="current_options", search_policy="forbidden"))

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def ask(self, *args: Any, **kwargs: Any):
                    raise AssertionError("mismatched canonical action/target/search_policy must not search")

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-canonical-pair", message="подбери", channel="jivo")
            assert result["intent"] == "freeform"
            assert result["turn_decision"] == {"stage": "freeform", "action": "freeform"}

        asyncio.run(scenario())

    def test_legacy_current_plan_without_canonical_still_searches(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            patch_planner(monkeypatch, {"dialog_action": "new_search", "confidence": 0.95, "visible_options_policy": "rebuild"})
            seen: list[dict[str, Any]] = []

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
                    seen.append(request_data)
                    return json.dumps(
                        {
                            "facts": [{"name": "ЖК Legacy", "location": "Москва", "min_price": 12_000_000}],
                            "near": [],
                            "missing": [],
                            "params": {},
                            "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "requested_field_priorities": [], "relaxation_audit": []},
                        },
                        ensure_ascii=False,
                    ), {"ok": True}

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-legacy-search", message="подбери", channel="jivo")
            assert result["intent"] == "main_search"
            assert [item.get("_payload_stage") for item in seen] == ["main_search", "main_search", "main_search"]
            assert '"count": 2' in seen[1]["query"]
            assert '"excluded_names": ["ЖК Legacy"]' in seen[1]["query"]
            third_envelope = json.loads(seen[2]["query"].split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
            third_params = json.loads(seen[2]["query"].split("Текущие параметры: ", 1)[1].split("\n", 1)[0])
            assert third_envelope["count"] == 1
            assert third_params["preferences"] == {"format": "full_card"}

        asyncio.run(scenario())

    def test_legacy_search_passes_compatible_empty_hard_constraints(self, tmp_path, monkeypatch) -> None:
        async def scenario() -> None:
            app = make_app(tmp_path)
            state = mod._default_state()
            state["params"] = {"rooms": 2, "max_price": 18_000_000, "client_id": "secret-client"}
            app["state_store"].states["u-legacy-hard"] = state
            patch_planner(monkeypatch, {"dialog_action": "new_search", "confidence": 0.95, "visible_options_policy": "rebuild"})
            seen: list[dict[str, Any]] = []

            class FakeClient:
                async def ensure_session(self) -> None:
                    return None

                async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
                    seen.append(request_data)
                    return json.dumps(
                        {
                            "facts": [{"name": "ЖК Hard", "rooms": 2, "max_price": 17_000_000, "min_price": 12_000_000}],
                            "near": [],
                            "missing": [],
                            "params": {"rooms": 2, "max_price": 18_000_000},
                            "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "requested_field_priorities": [], "relaxation_audit": []},
                        },
                        ensure_ascii=False,
                    ), {"ok": True}

            app["overmind_client"] = FakeClient()
            result = await mod.run_chat(app, user_id="u-legacy-hard", message="подбери", channel="jivo")
            assert result["intent"] == "main_search"
            assert '"requested_hard": {"max_price": 18000000, "rooms": 2}' in seen[0]["query"]
            assert "secret-client" not in seen[0]["query"]

        asyncio.run(scenario())
