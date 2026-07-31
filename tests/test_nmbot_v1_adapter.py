from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from aiohttp import web

from nmbot_v1.contracts import V1Action, V1IntentPlan, V1Stage
from nmbot_v1.prompt_provenance import build_prompt_provenance, identity_from_text
from nmbot_v1.state import V1ConversationState

import scripts.nmbot_runtime_adapter as adapter
import scripts.nmbot_egress_policy as egress_policy


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
SCRIPT = SCRIPT_DIR / "nmbot_api_server.py"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("nmbot_api_server_v1_adapter_test", SCRIPT)
api = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["nmbot_api_server_v1_adapter_test"] = api
spec.loader.exec_module(api)


def plan(goal: str, **kw: Any) -> V1IntentPlan:
    data = {"schema_version": 1, "goal": goal, "viewpoint": "buyer", "constraints_delta": {"hard": {}, "preferences": {}}, "selected_option_ref": None, "selected_lot_ref": None, "requested_facts": [], "operator_intent": "none", "clarification": None, "confidence": 1}
    data.update(kw)
    return V1IntentPlan.from_dict(data)


class Store:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.states = {"u": dict(initial or {})}
        self.saved: list[tuple[str, dict[str, Any]]] = []

    async def get(self, user_id: str) -> dict[str, Any]:
        return self.states.setdefault(user_id, {})

    async def save(self, user_id: str, state: dict[str, Any]) -> None:
        self.states[user_id] = dict(state)
        self.saved.append((user_id, dict(state)))


class RuntimeVersionStore:
    def __init__(self, version: str = "V2") -> None:
        self.version = version

    async def get(self) -> str:
        return self.version


class Planner:
    def __init__(self, result: V1IntentPlan) -> None:
        self.result = result
        self.calls = 0
        self.prompt_provenance = build_prompt_provenance([identity_from_text("v1.planner", "prompts/v1/intent_planner.txt", "planner prompt")])

    def plan(self, planner_input: dict[str, Any]) -> V1IntentPlan:
        self.calls += 1
        return self.result


class AsyncPlanner(Planner):
    async def plan(self, planner_input: dict[str, Any]) -> V1IntentPlan:
        return super().plan(planner_input)


class Search:
    def __init__(self) -> None:
        self.calls = 0
        self.prompt_provenance = build_prompt_provenance([identity_from_text("v1.search", "prompts/v1/search_mcp.txt", "search prompt")])

    def search(self, request: Any) -> dict[str, Any]:
        self.calls += 1
        return {"schema_version": 1, "cards": [{"ref": "p1", "name": "ЖК Первый", "facts": {"token": "SECRET_FACT"}, "evidence": {"location": "Москва", "max_price": 10}}], "attempts": [{"status": "ok", "token": "SECRET_ATTEMPT"}]}


class AsyncSearch(Search):
    async def search(self, request: Any) -> dict[str, Any]:
        return super().search(request)


class Presenter:
    def __init__(self) -> None:
        self.calls = 0

    def present(self, *_args: Any) -> str:
        self.calls += 1
        return "SECRET presenter candidate"


def make_app(*, version: str = "V1", initial: dict[str, Any] | None = None, planner: Any = None, search: Any = None, presenter: Any = None) -> web.Application:
    app = web.Application()
    app["state_store"] = Store(initial)
    app["runtime_version_store"] = RuntimeVersionStore(version)
    if planner is not None:
        app["v1_planner_port"] = planner
    if search is not None:
        app["v1_search_port"] = search
    if presenter is not None:
        app["v1_presenter_port"] = presenter
        app["v1_presenter_mode"] = "publish"
    return app


def test_v1_selector_dispatch_uses_only_injected_ports_and_saves_nmbot_v1() -> None:
    async def scenario() -> None:
        planner = AsyncPlanner(plan("search", constraints_delta={"hard": {"location": "Москва", "max_price": 12}, "preferences": {}}))
        search = AsyncSearch()
        presenter = Presenter()
        initial = {"nmbot_v0": {"keep": "v0"}, "nmbot_v2": {"keep": "v2"}}
        app = make_app(initial=initial, planner=planner, search=search, presenter=presenter)

        result = await adapter.run_runtime_turn(app, user_id="u", message="ищу SECRET", channel="jivo")

        assert result["ok"] is True
        assert result["meta"]["runtime"] == "v1"
        assert result["intent"] == "search_results"
        assert result["answer_kind"] == "search_results"
        assert result["turn_decision"] == {"stage": "first_search", "action": "search", "answer_kind": "search_results"}
        assert result["buttons"] == []
        assert result["handoff_to_operator"] is False
        assert presenter.calls == 1
        state = app["state_store"].states["u"]
        assert state["nmbot_v0"] == {"keep": "v0"}
        assert state["nmbot_v2"] == {"keep": "v2"}
        assert state["nmbot_v1"]["revision"] == 1
        assert state["nmbot_v1"]["visible_options"] == [{"ref": "p1", "name": "ЖК Первый", "facts": {"location": "Москва", "price": 10}}]
        dumped = json.dumps({"result": result, "state": state}, ensure_ascii=False)
        assert "SECRET" not in dumped
        assert "+7 999" not in dumped
        assert "111-22-33" not in dumped

    asyncio.run(scenario())


def test_v1_missing_planner_fails_closed_without_state_mutation_or_provider_call() -> None:
    async def scenario() -> None:
        search = Search()
        initial = {"nmbot_v1": V1ConversationState.clean().to_dict(), "nmbot_v2": {"params": {"rooms": 2}}}
        app = make_app(initial=initial, search=search)

        result = await adapter.run_runtime_turn(app, user_id="u", message="подбери", channel="jivo")

        assert result["ok"] is False
        assert result["error_type"] == "missing_v1_planner_port"
        assert result["meta"]["runtime"] == "v1"
        assert result["turn_decision"]["stage"] == "safe_error"
        assert search.calls == 0
        assert app["state_store"].states["u"] == initial
        assert app["state_store"].saved == []

    asyncio.run(scenario())


def test_v1_pre_model_phone_callback_queues_without_planner_or_v2_mutation(tmp_path: Path) -> None:
    async def scenario() -> None:
        planner = AsyncPlanner(plan("search"))
        initial = {"nmbot_v1": V1ConversationState.clean().to_dict(), "nmbot_v2": {"keep": "v2"}}
        app = make_app(initial=initial, planner=planner)
        app["crm_callback_outbox"] = api.LocalCallbackOutbox(tmp_path / "outbox")

        result = await adapter.run_runtime_turn(app, user_id="u", message="+7 999 123-45-67", channel="jivo", meta={"event_id": "phone-v1"})

        assert result["ok"] is True
        assert result["intent"] == "callback_queued"
        assert result["crm_callback"]["status"] == "queued"
        assert planner.calls == 0
        state = app["state_store"].states["u"]
        assert state["nmbot_v2"] == {"keep": "v2"}
        assert state["nmbot_v1"]["contact_phone_redacted"] == "***4567"
        assert state["nmbot_v1"]["callback_ref"] == result["crm_callback"]["lead_ref"]
        dumped = json.dumps({"result": result, "state": state}, ensure_ascii=False)
        assert "+7 999" not in dumped
        assert "123-45-67" not in dumped
        assert len(list((tmp_path / "outbox").glob("*.json"))) == 1

    asyncio.run(scenario())


def test_v1_surrounding_text_phone_callback_queues_before_planner(tmp_path: Path) -> None:
    async def scenario() -> None:
        planner = AsyncPlanner(plan("search"))
        initial = {"nmbot_v1": V1ConversationState.clean().to_dict(), "nmbot_v2": {"keep": "v2"}}
        app = make_app(initial=initial, planner=planner)
        app["crm_callback_outbox"] = api.LocalCallbackOutbox(tmp_path / "outbox")

        result = await adapter.run_runtime_turn(app, user_id="u", message="Позвоните мне, мой номер +7 999 123-45-67", channel="jivo", meta={"event_id": "phone-v1-text"})

        assert result["ok"] is True
        assert result["intent"] == "callback_queued"
        assert result["crm_callback"]["status"] == "queued"
        assert planner.calls == 0
        state = app["state_store"].states["u"]
        assert state["nmbot_v2"] == {"keep": "v2"}
        assert state["nmbot_v1"]["contact_phone_redacted"] == "***4567"
        dumped = json.dumps({"result": result, "state": state}, ensure_ascii=False)
        assert "+7 999" not in dumped
        assert "123-45-67" not in dumped
        assert len(list((tmp_path / "outbox").glob("*.json"))) == 1

    asyncio.run(scenario())


def test_v1_pending_phone_callback_duplicate_terminal_success(tmp_path: Path) -> None:
    async def scenario() -> None:
        initial_v1 = V1ConversationState.clean().to_dict()
        initial_v1.update({"stage": V1Stage.CONTACT_PHONE.value, "contact_consent": True, "contact_name": "Иван", "pending_action": V1Action.CAPTURE_NAME.value})
        app = make_app(initial={"nmbot_v1": initial_v1})
        app["crm_callback_outbox"] = api.LocalCallbackOutbox(tmp_path / "outbox")

        first = await adapter.run_runtime_turn(app, user_id="u", message="+7 999 123-45-67", channel="jivo", meta={"event_id": "same"})
        second = await adapter.run_runtime_turn(app, user_id="u", message="+7 999 123-45-67", channel="jivo", meta={"event_id": "same"})

        assert first["crm_callback"]["status"] == "queued"
        assert second["ok"] is True
        assert second["crm_callback"]["status"] == "duplicate"
        assert second["answer_kind"] == "callback_queued"
        assert len(list((tmp_path / "outbox").glob("*.json"))) == 1

    asyncio.run(scenario())


def test_v1_missing_search_port_fails_closed_and_preserves_state() -> None:
    async def scenario() -> None:
        initial = {"nmbot_v1": V1ConversationState.clean().to_dict(), "nmbot_v0": {"x": 1}}
        app = make_app(initial=initial, planner=Planner(plan("search")))

        result = await adapter.run_runtime_turn(app, user_id="u", message="ищу", channel="jivo")

        assert result["ok"] is False
        assert result["error_type"] == "missing_search_port"
        assert app["state_store"].states["u"] == initial
        assert app["state_store"].saved == []

    asyncio.run(scenario())


def test_selector_keeps_v0_v2_v3_and_unknown_fallback_routes(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[str] = []

        async def fake_v0(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("v0")
            return {"ok": True, "answer": "v0", "meta": {"runtime": "v0"}}

        async def fake_v2(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("v2")
            return {"ok": True, "answer": "v2", "meta": {"runtime": _kwargs.get("runtime_version", "v2")}}

        monkeypatch.setattr(adapter, "_run_v0_authoritative", fake_v0)
        monkeypatch.setattr(adapter, "_run_v2_authoritative", fake_v2)

        assert (await adapter.run_runtime_turn(make_app(version="V0"), user_id="u", message="x", channel="jivo"))["answer"] == "v0"
        assert (await adapter.run_runtime_turn(make_app(version="V2"), user_id="u", message="x", channel="jivo"))["answer"] == "v2"
        v3 = await adapter.run_runtime_turn(make_app(version="V3"), user_id="u", message="x", channel="jivo")
        assert v3["meta"]["runtime"] == "v3"
        unknown = await adapter.run_runtime_turn(make_app(version="V9"), user_id="u", message="x", channel="jivo")
        assert unknown["meta"]["runtime"] == "v2"
        assert calls == ["v0", "v2", "v2", "v2"]

    asyncio.run(scenario())


def test_api_start_1_reset_journal_and_client_production_block(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))
        app = api.make_app() if hasattr(api, "make_app") else web.Application()
        app["state_store"] = api.JsonStateStore(tmp_path / "state.json")
        app["runtime_version_store"] = api.RuntimeVersionStore(tmp_path / "runtime.json")
        app["crm_callback_outbox"] = api.LocalCallbackOutbox(tmp_path / "outbox")
        app["jivo_session_locks"] = api.SessionLockRegistry()
        app["jivo_dedup_cache"] = api.JivoDedupCache(ttl_sec=60, max_entries=32)
        session = "jivo:site1:chat1:client-chat1"
        await app["state_store"].save(session, {"nmbot_v0": {"old": 0}, "nmbot_v2": {"old": 2}})

        response, status = await api.process_jivo_client_message(app, {"event": "CLIENT_MESSAGE", "site_id": "site1", "client_id": "client-chat1", "chat_id": "chat1", "agents_online": True, "id": "s1", "message": {"type": "TEXT", "text": "/start_1"}})

        assert status == 200
        assert response["message"]["text"].startswith("Здравствуйте! Меня зовут Татьяна")
        assert response["message"]["text"].endswith("Сейчас активна версия: V1.")
        state = await app["state_store"].get(session)
        assert state["runtime_version_override"] == "V1"
        assert state["nmbot_v1"] == V1ConversationState.clean().to_dict()
        assert state["nmbot_v0"] == {"old": 0}
        assert state["nmbot_v2"] == {"old": 2}
        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        readable = (tmp_path / "dialogue-readable.log").read_text(encoding="utf-8")
        assert "Татьяна:" in readable
        assert "Ирина:" not in readable
        assert [(row["role"], row["event_type"], row.get("runtime_version"), row.get("answer_kind")) for row in rows] == [("user", "turn", "V1", None), ("bot", "lifecycle", "V1", "start_reset")]

        monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_production")
        app2 = web.Application()
        app2["state_store"] = api.JsonStateStore(tmp_path / "state-prod.json")
        app2["runtime_version_store"] = api.RuntimeVersionStore(tmp_path / "runtime-prod.json")
        app2["crm_callback_outbox"] = api.LocalCallbackOutbox(tmp_path / "outbox-prod")
        app2["jivo_session_locks"] = api.SessionLockRegistry()
        app2["jivo_dedup_cache"] = api.JivoDedupCache(ttl_sec=60, max_entries=32)
        response2, status2 = await api.process_jivo_client_message(app2, {"event": "CLIENT_MESSAGE", "site_id": "site1", "client_id": "client-chat1", "chat_id": "chat1", "agents_online": True, "id": "s2", "message": {"type": "TEXT", "text": "/start_1"}})
        assert status2 == 200
        assert response2["message"]["text"].startswith("Здравствуйте! Меня зовут Ирина")
        prod_state = await app2["state_store"].get(session)
        assert "runtime_version_override" not in prod_state
        assert "nmbot_v1" not in prod_state
        assert prod_state["nmbot_v2"] == api.ConversationState().to_dict()

    asyncio.run(scenario())


def test_v1_turn_journal_keeps_jivo_v1_execution_path(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue_journal.jsonl"))
        monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "dialogue-readable.log"))
        app = web.Application()
        app["state_store"] = api.JsonStateStore(tmp_path / "state.json")
        app["runtime_version_store"] = api.RuntimeVersionStore(tmp_path / "runtime.json")
        app["crm_callback_outbox"] = api.LocalCallbackOutbox(tmp_path / "outbox")
        app["jivo_session_locks"] = api.SessionLockRegistry()
        app["jivo_dedup_cache"] = api.JivoDedupCache(ttl_sec=60, max_entries=32)
        app["v1_planner_port"] = Planner(plan("search", constraints_delta={"hard": {"location": "Москва", "max_price": 12}, "preferences": {}}))
        app["v1_search_port"] = Search()
        session = "jivo:site1:chat1:client-chat1"
        await app["state_store"].save(session, {"runtime_version_override": "V1"})

        response, status = await api.process_jivo_client_message(app, {"event": "CLIENT_MESSAGE", "site_id": "site1", "client_id": "client-chat1", "chat_id": "chat1", "agents_online": True, "id": "v1-turn", "message": {"type": "TEXT", "text": "ищу"}})

        assert status == 200
        assert response["event"] == "BOT_MESSAGE"
        rows = [json.loads(line) for line in (tmp_path / "dialogue_journal.jsonl").read_text(encoding="utf-8").splitlines()]
        bot = rows[-1]
        path = bot["execution_path"]
        assert bot["runtime_version"] == "V1"
        assert path["schema"] == "nmbot.execution_path.v1"
        assert path["path_id"] == "jivo.v1.turn.v1"
        assert [item["stage_id"] for item in path["stages"]] == [
            "v1.planner",
            "v1.transition",
            "v1.search",
            "v1.response_plan",
            "v1.deterministic_render",
            "v1.presenter",
            "v1.runtime_finalize",
            "jivo.api.prepare",
        ]
        assert path["stages"][-1] == {"stage_id": "jivo.api.prepare", "status": "completed"}
        provenance = bot["prompt_provenance"]
        assert provenance["schema"] == "nmbot.prompt_provenance.v1"
        assert [p["source"] for p in provenance["prompts"]] == ["prompts/v1/intent_planner.txt", "prompts/v1/search_mcp.txt"]
        assert "SECRET" not in json.dumps(bot, ensure_ascii=False)

    asyncio.run(scenario())


def test_v1_client_production_egress_strips_version_and_blocks_start_command() -> None:
    decorated = egress_policy.sanitize_client_text(
        "Подбор готов.\n\nСейчас активна версия: V1.",
        profile="client_production",
    )
    assert decorated.text == "Подбор готов."
    assert decorated.blocked is False

    blocked = egress_policy.sanitize_client_text(
        "Введите /start_1 для переключения.",
        profile="client_production",
    )
    assert blocked.blocked is True
    assert blocked.blocker_code == "start_version_marker"
    assert blocked.text == egress_policy.SAFE_CLIENT_FALLBACK_TEXT
