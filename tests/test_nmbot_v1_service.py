from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer


from nmbot_runtime_contract.wire import CONTRACT_VERSION
from nmbot_runtime_service_host.http import ConversationLockRegistry, ConversationLockUnavailable, ServiceTurn, create_app as create_host_app
from nmbot_v1.contracts import V1IntentPlan
from nmbot_v1.service import V1GatewayClient, build_turn, create_app
from nmbot_v1.state import V1ConversationState


def request(message: str = "подбери квартиру") -> dict[str, Any]:
    return {"contract_version": CONTRACT_VERSION, "runtime_version": "V1", "conversation_ref": "conversation:123",
            "trace_ref": "trace:12345678", "message": message, "channel": "api", "meta": {}}


class Planner:
    def plan(self, _input: dict[str, Any]) -> V1IntentPlan:
        return V1IntentPlan.from_dict({"schema_version": 1, "goal": "search", "viewpoint": "buyer",
            "constraints_delta": {"hard": {"location": "Москва"}, "preferences": {}}, "selected_option_ref": None,
            "selected_lot_ref": None, "requested_facts": [], "operator_intent": "none", "clarification": None, "confidence": 1})


class Search:
    def search(self, _request: Any) -> dict[str, Any]:
        return {"schema_version": 1, "cards": [{"ref": "p1", "name": "ЖК Первый", "facts": {"location": "Москва", "price": 10}, "evidence": {"location": "Москва"}}], "attempts": []}


def test_service_import_closure_is_v1_only() -> None:
    source = Path("nmbot_v1/service.py").read_text(encoding="utf-8")
    imports = [node.names[0].name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import)]
    from_imports = [node.module or "" for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)]
    banned = ("nmbot_v0", "nmbot_v2", "nmbot_v3", "nmbot_runtime_adapter", "nmbot_api_server")
    assert not any(name.startswith(banned) for name in imports + from_imports)


def test_missing_provider_and_phone_fail_closed_without_mutation() -> None:
    async def scenario() -> None:
        initial = V1ConversationState.clean().to_dict()
        missing = await build_turn()(request(), initial)
        assert missing.state is None and missing.response["error_code"] == "missing_v1_planner_port"
        phone = await build_turn(planner_port=Planner(), search_port=Search())(request("мой телефон +7 999 111-22-33"), initial)
        assert phone.state is None and phone.response["error_code"] == "v1_phone_flow_unmigrated"
        assert "+7" not in json.dumps(phone.response, ensure_ascii=False)
    asyncio.run(scenario())


def test_service_http_state_reset_journal_and_release_identity(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_path, journal_path = tmp_path / "state.json", tmp_path / "journal.jsonl"
        client = TestClient(TestServer(create_app(state_path=state_path, journal_path=journal_path, token="internal", release_identity="v1-test-release", planner_port=Planner(), search_port=Search())))
        await client.start_server()
        headers = {"Authorization": "Bearer internal"}
        try:
            health = await client.get("/health")
            assert await health.json() == {"ok": True, "runtime_version": "V1", "release_identity": "v1-test-release"}
            response = await client.post("/api/chat", json=request("ищу SECRET"), headers=headers)
            body = await response.json()
            assert response.status == 200 and body["ok"] is True and body["runtime_version"] == "V1"
            stored = json.loads(state_path.read_text(encoding="utf-8"))
            assert set(stored) == {"conversation:123"}
            assert "nmbot_v2" not in json.dumps(stored) and stored["conversation:123"]["revision"] == 1
            reset = await client.post("/api/reset", json={key: value for key, value in request().items() if key not in {"message", "channel", "meta"}}, headers=headers)
            assert (await reset.json())["reset"] is True
            assert json.loads(state_path.read_text(encoding="utf-8"))["conversation:123"] == V1ConversationState.clean().to_dict()
            journal = journal_path.read_text(encoding="utf-8")
            assert "SECRET" not in journal and "provider_payload" not in journal and '"event":"chat"' in journal
        finally:
            await client.close()
    asyncio.run(scenario())


def test_shared_host_rejects_turn_failures_before_state_or_journal_commit(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_path, journal_path = tmp_path / "state.json", tmp_path / "journal.jsonl"
        original_state = {"conversation:123": {"revision": 7}}
        state_path.write_text(json.dumps(original_state), encoding="utf-8")

        async def malformed(_payload: dict[str, Any], _state: dict[str, Any] | None) -> ServiceTurn:
            return ServiceTurn({"ok": True}, {"revision": 1})

        client = TestClient(TestServer(create_host_app(runtime_version="V1", token="internal", release_identity="v1-test-release",
            state_path=state_path, journal_path=journal_path, turn=malformed, reset=lambda: {"revision": 0})))
        await client.start_server()
        try:
            failed = await client.post("/api/chat", json=request(), headers={"Authorization": "Bearer internal"})
            assert failed.status == 503 and await failed.json() == {"ok": False, "error_code": "runtime_failure"}
            assert json.loads(state_path.read_text(encoding="utf-8")) == original_state
            assert not journal_path.exists()
        finally:
            await client.close()
    asyncio.run(scenario())


def test_shared_host_turn_exception_version_mismatch_and_reset_write_error_fail_closed(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        headers = {"Authorization": "Bearer internal"}
        for name, turn in (
            ("exception", raising_turn),
            ("wrong-version", wrong_version_turn),
        ):
            state_path, journal_path = tmp_path / f"{name}.json", tmp_path / f"{name}.journal"
            client = TestClient(TestServer(create_host_app(runtime_version="V1", token="internal", release_identity="v1-test-release",
                state_path=state_path, journal_path=journal_path, turn=turn, reset=lambda: {"revision": 0})))
            await client.start_server()
            try:
                failed = await client.post("/api/chat", json=request(), headers=headers)
                assert failed.status == 503 and await failed.json() == {"ok": False, "error_code": "runtime_failure"}
                assert not state_path.exists() and not journal_path.exists()
            finally:
                await client.close()

        state_path, journal_path = tmp_path / "reset.json", tmp_path / "reset.journal"
        client = TestClient(TestServer(create_host_app(runtime_version="V1", token="internal", release_identity="v1-test-release",
            state_path=state_path, journal_path=journal_path, turn=wrong_version_turn, reset=lambda: {"revision": 0})))
        await client.start_server()
        try:
            from nmbot_runtime_service_host.http import AtomicStateStore
            monkeypatch.setattr(AtomicStateStore, "_write", lambda _self, _data: (_ for _ in ()).throw(OSError("write failed")))
            reset = await client.post("/api/reset", json={key: value for key, value in request().items() if key not in {"message", "channel", "meta"}}, headers=headers)
            assert reset.status == 503 and await reset.json() == {"ok": False, "error_code": "runtime_failure"}
            assert not journal_path.exists()
        finally:
            await client.close()
    asyncio.run(scenario())


async def raising_turn(_payload: dict[str, Any], _state: dict[str, Any] | None) -> ServiceTurn:
    raise RuntimeError("must not leak")


async def wrong_version_turn(_payload: dict[str, Any], _state: dict[str, Any] | None) -> ServiceTurn:
    return ServiceTurn({"contract_version": CONTRACT_VERSION, "ok": True, "runtime_version": "V2", "client_answer": "no", "handoff": False,
                        "error_code": None, "diagnostics": {"code": "ok"}}, {"revision": 1})


def test_shared_host_rejects_invalid_runtime_and_release_identity(tmp_path: Path) -> None:
    with __import__("pytest").raises(ValueError):
        create_host_app(runtime_version="V4", token="internal", release_identity="v1-test-release", state_path=tmp_path / "state", journal_path=tmp_path / "journal", turn=wrong_version_turn, reset=dict)
    with __import__("pytest").raises(ValueError):
        create_host_app(runtime_version="V1", token="internal", release_identity="../unsafe", state_path=tmp_path / "state", journal_path=tmp_path / "journal", turn=wrong_version_turn, reset=dict)
    for placeholder in ("", "local-v1", "local-v42", "replace-with-immutable-release-id"):
        with __import__("pytest").raises(ValueError):
            create_host_app(runtime_version="V1", token="internal", release_identity=placeholder, state_path=tmp_path / "state", journal_path=tmp_path / "journal", turn=wrong_version_turn, reset=dict)


def test_v1_entrypoint_rejects_missing_or_placeholder_release_id_before_binding(monkeypatch) -> None:
    script = Path("scripts/nmbot_v1_service.py")
    spec = importlib.util.spec_from_file_location("nmbot_v1_entrypoint_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for value in (None, "", "local-v1", "replace-with-immutable-release-id"):
        if value is None:
            monkeypatch.delenv("NMBOT_V1_RELEASE_ID", raising=False)
        else:
            monkeypatch.setenv("NMBOT_V1_RELEASE_ID", value)
        called = False

        def must_not_bind(*_args, **_kwargs):
            nonlocal called
            called = True

        monkeypatch.setattr(module.web, "run_app", must_not_bind)
        with __import__("pytest").raises(ValueError, match="invalid_release_identity"):
            module.main()
        assert called is False


def test_shared_host_serializes_same_conversation_without_lost_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        async def increment(_payload: dict[str, Any], before: dict[str, Any] | None) -> ServiceTurn:
            await asyncio.sleep(0.02)
            revision = int((before or {}).get("revision", 0)) + 1
            return ServiceTurn(success_response(), {"revision": revision})

        client = TestClient(TestServer(create_host_app(runtime_version="V1", token="internal", release_identity="v1-test-release",
            state_path=tmp_path / "state.json", journal_path=tmp_path / "journal.jsonl", turn=increment, reset=lambda: {"revision": 0})))
        await client.start_server()
        try:
            responses = await asyncio.gather(*[client.post("/api/chat", json=request(), headers={"Authorization": "Bearer internal"}) for _ in range(2)])
            assert [response.status for response in responses] == [200, 200]
            assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["conversation:123"] == {"revision": 2}
        finally:
            await client.close()
    asyncio.run(scenario())


def test_shared_host_allows_different_conversations_to_overlap(tmp_path: Path) -> None:
    async def scenario() -> None:
        both_started, release = asyncio.Event(), asyncio.Event()
        active = 0

        async def blocking_turn(_payload: dict[str, Any], before: dict[str, Any] | None) -> ServiceTurn:
            nonlocal active
            active += 1
            if active == 2:
                both_started.set()
            await release.wait()
            return ServiceTurn(success_response(), {"revision": int((before or {}).get("revision", 0)) + 1})

        client = TestClient(TestServer(create_host_app(runtime_version="V1", token="internal", release_identity="v1-test-release",
            state_path=tmp_path / "state.json", journal_path=tmp_path / "journal.jsonl", turn=blocking_turn, reset=lambda: {"revision": 0})))
        await client.start_server()
        try:
            first = client.post("/api/chat", json=request(), headers={"Authorization": "Bearer internal"})
            second_payload = request()
            second_payload["conversation_ref"] = "conversation:456"
            second = client.post("/api/chat", json=second_payload, headers={"Authorization": "Bearer internal"})
            pending = asyncio.gather(first, second)
            await asyncio.wait_for(both_started.wait(), timeout=0.5)
            release.set()
            assert [response.status for response in await pending] == [200, 200]
        finally:
            await client.close()
    asyncio.run(scenario())


def test_conversation_lock_registry_removes_idle_entries_and_bounds_active_ones() -> None:
    async def scenario() -> None:
        registry = ConversationLockRegistry(max_entries=1)
        async with registry.acquire("conversation:one"):
            assert len(registry._entries) == 1
            with __import__("pytest").raises(ConversationLockUnavailable):
                async with registry.acquire("conversation:two"):
                    pass
        assert registry._entries == {}
        async with registry.acquire("conversation:two"):
            assert len(registry._entries) == 1
    asyncio.run(scenario())


def test_v1_gateway_uses_one_deadline_for_each_hanging_request() -> None:
    async def scenario() -> None:
        for stage in ("post", "status", "result"):
            app = __import__("aiohttp").web.Application()

            async def post(_request):
                if stage == "post":
                    await asyncio.sleep(1)
                if stage in {"status", "result"}:
                    await asyncio.sleep(0.04)
                return __import__("aiohttp").web.json_response({"id": "task"})

            async def status(_request):
                if stage == "status":
                    await asyncio.sleep(1)
                if stage == "result":
                    await asyncio.sleep(0.04)
                return __import__("aiohttp").web.json_response({"status": "completed"})

            async def result(_request):
                if stage == "result":
                    await asyncio.sleep(1)
                return __import__("aiohttp").web.json_response({"result": {"response": "ok"}})

            app.router.add_post("/api/v1/tasks/api", post)
            app.router.add_get("/api/v1/tasks/api/task/status", status)
            app.router.add_get("/api/v1/tasks/api/task/result", result)
            server = TestServer(app)
            await server.start_server()
            client = V1GatewayClient(str(server.make_url("/")).rstrip("/"))
            started = asyncio.get_running_loop().time()
            try:
                text, meta = await client._run_gateway_request({}, {}, timeout=0.08)
                assert text == "" and meta == {"_safe_fallback": True, "_upstream_error": True}
                assert asyncio.get_running_loop().time() - started < 0.13
            finally:
                await client.close()
                await server.close()
    asyncio.run(scenario())


def success_response() -> dict[str, Any]:
    return {"contract_version": CONTRACT_VERSION, "ok": True, "runtime_version": "V1", "client_answer": "ok", "handoff": False,
            "error_code": None, "diagnostics": {"code": "ok"}}
