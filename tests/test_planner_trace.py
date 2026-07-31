from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from aiohttp import web


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

API_SPEC = importlib.util.spec_from_file_location("nmbot_api_server_planner_trace", SCRIPT_DIR / "nmbot_api_server.py")
assert API_SPEC and API_SPEC.loader
api = importlib.util.module_from_spec(API_SPEC)
sys.modules[API_SPEC.name] = api
API_SPEC.loader.exec_module(api)

TRACE_SPEC = importlib.util.spec_from_file_location("planner_trace_test_mod", SCRIPT_DIR / "planner_trace.py")
assert TRACE_SPEC and TRACE_SPEC.loader
planner_trace = importlib.util.module_from_spec(TRACE_SPEC)
sys.modules[TRACE_SPEC.name] = planner_trace
TRACE_SPEC.loader.exec_module(planner_trace)

FINDER_SPEC = importlib.util.spec_from_file_location("find_planner_trace_test_mod", SCRIPT_DIR / "find_planner_trace.py")
assert FINDER_SPEC and FINDER_SPEC.loader
find_planner_trace = importlib.util.module_from_spec(FINDER_SPEC)
sys.modules[FINDER_SPEC.name] = find_planner_trace
FINDER_SPEC.loader.exec_module(find_planner_trace)


class FakeStore:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}

    async def get(self, user_id: str) -> dict[str, Any]:
        return self.states.setdefault(user_id, api._default_state())

    async def save(self, user_id: str, state: dict[str, Any]) -> None:
        self.states[user_id] = dict(state)


def make_app(client: Any) -> web.Application:
    app = web.Application()
    app["state_store"] = FakeStore()
    app["crm_callback_outbox"] = object()
    app["overmind_client"] = client
    return app


class V2SearchClient:
    def __init__(self) -> None:
        self.gateway_calls: list[dict[str, Any]] = []

    async def ensure_session(self) -> None:
        return None

    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        if request_data.get("_payload_stage") != "main_search":
            raise AssertionError("planner trace search fixtures must not invoke answer composer")
        self.gateway_calls.append(request_data)
        return json.dumps(
            {
                "facts": [{"name": "ЖК Трасса", "location": "Сокол", "price_min": 12_000_000}],
                "near": [],
                "missing": [],
                "params": {"purpose": "family", "location": ["Сокол"]},
                "diagnostics": {
                    "mcp_tool": "novostroym/get_flat_info",
                    "requested_field_priorities": [],
                    "relaxation_audit": [],
                },
            },
            ensure_ascii=False,
        ), {"ok": True}


class NoSearchClient:
    async def ensure_session(self) -> None:
        return None

    async def _run_gateway_request(self, *_args: Any, **_kwargs: Any):
        raise AssertionError("invalid planner decision must not search")


def canonical_plan(**overrides: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "dialog_action": "new_search",
        "confidence": 0.91,
        "fallback_used": False,
        "action": "search",
        "intent": "family",
        "intent_policy": "set",
        "target": "new_search",
        "search_policy": "required",
        "scope": "unknown",
        "selected_option_name": None,
        "clarification_question": "",
        "constraints_patch": {"hard": {}, "preferences": {}, "unknown": {}},
        "facets": {},
        "operator_contact": {"requested": False, "consent": "none"},
        "search_profile": "generic",
        "missing_fields": [],
        "clarification_fields": [],
        "canonical_valid": True,
        "canonical_errors": [],
    }
    plan.update(overrides)
    return plan


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def assert_no_forbidden_raw_strings(row: dict[str, Any]) -> None:
    dumped = json.dumps(row, ensure_ascii=False)
    for forbidden in (
        "site-secret",
        "chat-secret",
        "client-secret",
        "+7 999 111-22-33",
        "test@example.com",
        "token-secret",
        "payload-secret",
        "system prompt secret",
        "raw-jivo-id",
    ):
        assert forbidden not in dumped


def test_append_trace_schema_redaction_and_mode(tmp_path: Path) -> None:
    path = tmp_path / "planner_trace.jsonl"
    event = planner_trace.append_event(
        session_key="jivo:site-secret:chat-secret:client-secret",
        plan={
            **canonical_plan(),
            "raw": "raw planner response",
            "user_text": "plan-level user text must not win",
            "assistant_answer": "ответ ассистента",
            "token": "token-secret",
            "payload": "payload-secret",
            "system_prompt": "system prompt secret",
            "raw_jivo_id": "raw-jivo-id",
            "planner_raw_response": "```json\n{\"action\":\"search\",\"reason\":\"позвонить +7 999 111-22-33 или test@example.com\"}\n```",
        },
        final_decision=api.TurnDecision(action="search", target="new_search", search_policy="required"),
        user_text="сырой текст клиента +7 999 111-22-33 и test@example.com",
        path=path,
    )

    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    rows = read_rows(path)
    assert rows == [event]
    assert rows[0]["schema_version"] == 1
    assert rows[0]["session_key_ref"].startswith("sha256:")
    assert rows[0]["conversation_ref"].startswith("sha256:")
    assert rows[0]["final_decision"] == {"action": "search", "target": "new_search", "search_policy": "required"}
    assert rows[0]["user_text"] == "сырой текст клиента [phone redacted] и [email redacted]"
    assert rows[0]["user_text_truncated"] is False
    assert rows[0]["raw_response_present"] is True
    assert rows[0]["planner_raw_response_truncated"] is False
    assert "```json" in rows[0]["planner_raw_response"]
    assert "[phone redacted]" in rows[0]["planner_raw_response"]
    assert "[email redacted]" in rows[0]["planner_raw_response"]
    assert_no_forbidden_raw_strings(rows[0])


def test_trace_raw_fields_are_limited_and_report_truncation(tmp_path: Path) -> None:
    path = tmp_path / "planner_trace.jsonl"
    event = planner_trace.append_event(
        session_key="u-limits",
        user_text="u" * (planner_trace.USER_TEXT_MAX + 5),
        plan=canonical_plan(planner_raw_response="r" * (planner_trace.PLANNER_RAW_RESPONSE_MAX + 5)),
        final_decision=api.TurnDecision(action="search", target="new_search", search_policy="required"),
        path=path,
    )

    assert len(event["user_text"]) == planner_trace.USER_TEXT_MAX
    assert event["user_text_truncated"] is True
    assert len(event["planner_raw_response"]) == planner_trace.PLANNER_RAW_RESPONSE_MAX
    assert event["planner_raw_response_truncated"] is True
    assert event["raw_response_present"] is True


def test_plan_dialog_state_preserves_raw_model_response_before_json_extraction(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setenv("OVERMIND_TOKEN", "test-overmind-token")
        raw_model_response = "```json\n" + json.dumps(canonical_plan(reason="raw boundary"), ensure_ascii=False) + "\n```"

        class FakeResponse:
            def __init__(self, payload: dict[str, Any]) -> None:
                self.payload = payload

            async def __aenter__(self) -> "FakeResponse":
                return self

            async def __aexit__(self, *_args: Any) -> None:
                return None

            async def json(self) -> dict[str, Any]:
                return self.payload

        class FakeSession:
            def post(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
                return FakeResponse({"id": "task-1"})

            def get(self, url: str, **_kwargs: Any) -> FakeResponse:
                if url.endswith("/status"):
                    return FakeResponse({"status": "completed"})
                return FakeResponse({"result": {"response": raw_model_response}})

        plan = await api.followup_intent_classifier.plan_dialog_state(FakeSession(), user_text="текст", state={})
        assert plan["reason"] == "raw boundary"
        assert plan["planner_raw_response"] == raw_model_response

    asyncio.run(scenario())


def test_canonical_adapter_raw_response_reaches_api_trace(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        trace_file = tmp_path / "trace.jsonl"
        monkeypatch.setenv("NMBOT_PLANNER_TRACE_FILE", str(trace_file))
        raw = "adapter raw says call +7 999 111-22-33 or test@example.com"

        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return api.followup_intent_classifier._with_canonical_fields(
                {},
                canonical_plan(reason="adapter boundary", clarification="", planner_raw_response=raw),
            )

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)

        client = V2SearchClient()
        result = await api.run_chat(make_app(client), user_id="u-adapter-raw", message="подбери", channel="jivo")
        assert result["intent"] == "main_search"
        assert [call["_payload_stage"] for call in client.gateway_calls] == ["main_search", "main_search"]
        assert '"count": 2' in client.gateway_calls[1]["query"]
        assert '"excluded_names": ["ЖК Трасса"]' in client.gateway_calls[1]["query"]
        row = read_rows(trace_file)[0]
        assert row["raw_response_present"] is True
        assert row["planner_raw_response"] == "adapter raw says call [phone redacted] or [email redacted]"
        assert row["planner_raw_response_truncated"] is False
        assert_no_forbidden_raw_strings(row)

    asyncio.run(scenario())


def test_normal_accepted_plan_writes_one_trace_event(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        trace_file = tmp_path / "trace.jsonl"
        monkeypatch.setenv("NMBOT_PLANNER_TRACE_FILE", str(trace_file))

        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return canonical_plan(
                constraints_patch={"hard": {"location": ["Сокол"]}, "preferences": {}, "unknown": {}},
                planner_raw_response="raw model says call +7 999 111-22-33 or test@example.com",
            )

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)

        client = V2SearchClient()
        app = make_app(client)
        result = await api.run_chat(app, user_id="jivo:site-secret:chat-secret:client-secret", message="сырой текст клиента test@example.com", channel="jivo")
        assert result["intent"] == "main_search"
        assert [call["_payload_stage"] for call in client.gateway_calls] == ["main_search", "main_search"]
        assert '"count": 2' in client.gateway_calls[1]["query"]
        assert '"excluded_names": ["ЖК Трасса"]' in client.gateway_calls[1]["query"]
        rows = read_rows(trace_file)
        assert len(rows) == 1
        assert rows[0]["action"] == "search"
        assert rows[0]["canonical_valid"] is True
        assert rows[0]["final_decision"]["action"] == "search"
        assert rows[0]["user_text"] == "сырой текст клиента [email redacted]"
        assert rows[0]["planner_raw_response"] == "raw model says call [phone redacted] or [email redacted]"
        assert_no_forbidden_raw_strings(rows[0])

    asyncio.run(scenario())


def test_rejected_plan_trace_keeps_validation_errors(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        trace_file = tmp_path / "trace.jsonl"
        monkeypatch.setenv("NMBOT_PLANNER_TRACE_FILE", str(trace_file))
        bad_plan = canonical_plan(
            canonical_valid=False,
            canonical_errors=["invalid_constraints_category"],
            constraints_patch={"hard": []},
        )

        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return bad_plan

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)

        result = await api.run_chat(make_app(NoSearchClient()), user_id="u-rejected", message="сырой текст клиента", channel="jivo")
        assert result["intent"] == "freeform"
        assert result["turn_decision"] == {"stage": "freeform", "action": "freeform"}
        row = read_rows(trace_file)[0]
        assert row["final_decision"]["action"] == "recover_dialogue"
        assert "invalid_constraints_category" in row["canonical_error_codes"]
        assert row["canonical_valid"] is False
        assert row["user_text"] == "сырой текст клиента"
        assert_no_forbidden_raw_strings(row)

    asyncio.run(scenario())


def test_repair_trace_fields_include_source_error_and_applied_flag(tmp_path: Path, monkeypatch) -> None:
    trace_file = tmp_path / "trace.jsonl"
    row = planner_trace.append_event(
        session_key="u-repair",
        plan=canonical_plan(
            repair_attempted=True,
            repair_applied=True,
            repair_source_errors=["invalid_action_target_search_policy", "search_requires_new_search_required"],
            planner_raw_response="original raw response before repair",
        ),
        final_decision=api.TurnDecision(action="search", target="new_search", search_policy="required"),
        path=trace_file,
    )

    assert read_rows(trace_file) == [row]
    assert row["repair_attempted"] is True
    assert row["repair_applied"] is True
    assert "invalid_action_target_search_policy" in row["canonical_error_codes"]
    assert "search_requires_new_search_required" in row["canonical_error_codes"]
    assert row["final_decision"]["action"] == "search"
    assert row["planner_raw_response"] == "original raw response before repair"


def test_advisory_plan_trace_keeps_errors_and_skips_repair(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        trace_file = tmp_path / "trace.jsonl"
        monkeypatch.setenv("NMBOT_PLANNER_TRACE_FILE", str(trace_file))
        repair_calls = 0

        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            plan = canonical_plan(
                scope="all",
                constraints_patch={"hard": {"location": ["Сокол"]}, "preferences": {}, "unknown": {}},
                planner_raw_response='{"action":"search","scope":"all"}',
            )
            plan["canonical_validation_errors"] = ["search_scope_must_be_unknown"]
            return plan

        async def fake_repair(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            nonlocal repair_calls
            repair_calls += 1
            raise AssertionError("advisory errors must not be repaired")

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)
        monkeypatch.setattr(api.followup_intent_classifier, "repair_canonical_plan", fake_repair)

        client = V2SearchClient()
        result = await api.run_chat(make_app(client), user_id="u-advisory", message="подбери", channel="jivo")
        assert result["intent"] == "main_search"
        assert [call["_payload_stage"] for call in client.gateway_calls] == ["main_search", "main_search"]
        assert '"count": 2' in client.gateway_calls[1]["query"]
        assert '"excluded_names": ["ЖК Трасса"]' in client.gateway_calls[1]["query"]
        assert repair_calls == 0
        row = read_rows(trace_file)[0]
        assert row["canonical_valid"] is False
        assert row["canonical_error_codes"] == ["search_scope_must_be_unknown"]
        assert row["final_decision"] == {"action": "search", "target": "none", "search_policy": "required"}
        assert row["raw_response_present"] is True

    asyncio.run(scenario())


def test_planner_exception_writes_safe_fallback_trace(tmp_path: Path, monkeypatch) -> None:
    trace_file = tmp_path / "trace.jsonl"
    row = planner_trace.append_event(
        session_key="u-exception",
        plan={"fallback_used": True},
        final_decision=api.TurnDecision(action="recover_dialogue", target="none", search_policy="forbidden"),
        exception_code="RuntimeError",
        user_text="сырой текст клиента",
        path=trace_file,
    )

    assert read_rows(trace_file) == [row]
    assert row["fallback_used"] is True
    assert row["planner_exception_code"] == "RuntimeError"
    assert row["raw_response_present"] is False
    assert "planner_raw_response" not in row
    assert row["user_text"] == "сырой текст клиента"
    assert row["final_decision"] == {"action": "recover_dialogue", "target": "none", "search_policy": "forbidden"}
    assert_no_forbidden_raw_strings(row)
    assert "/secret/path" not in json.dumps(row, ensure_ascii=False)


def test_find_planner_trace_local_search_by_fields_and_ref(tmp_path: Path) -> None:
    trace_dir = tmp_path / "logs"
    trace_dir.mkdir()
    path = trace_dir / "planner_trace-2026-07-17.jsonl"
    planner_trace.append_event(
        session_key="jivo:site-secret:chat-secret:client-secret",
        plan=canonical_plan(action="answer_current_options", dialog_action="consultation_answer", target="current_options", search_policy="forbidden", search_profile="none"),
        final_decision=api.TurnDecision(action="answer_current_options", target="current_options", search_policy="forbidden"),
        user_text="найди по скрытому тексту",
        path=path,
    )
    planner_trace.append_event(
        session_key="other-session",
        plan=canonical_plan(action="search", planner_raw_response="raw contains searchable marker"),
        final_decision=api.TurnDecision(action="search", target="new_search", search_policy="required"),
        path=path,
    )

    rows = find_planner_trace.search(
        path,
        fields=["final_decision.action=answer_current_options"],
        ref="jivo:site-secret:chat-secret:client-secret",
        limit=10,
    )

    assert len(rows) == 1
    assert rows[0]["final_decision"]["action"] == "answer_current_options"
    assert "path" in rows[0] and "line" in rows[0]
    assert "user_text" not in rows[0]
    assert "planner_raw_response" not in rows[0]
    assert_no_forbidden_raw_strings(rows[0])

    hidden_match = find_planner_trace.search(path, fields=[], query="searchable marker", limit=10)
    assert len(hidden_match) == 1
    assert "planner_raw_response" not in hidden_match[0]
    shown = find_planner_trace.search(path, fields=[], query="searchable marker", limit=10, show_raw=True)
    assert shown[0]["planner_raw_response"] == "raw contains searchable marker"
