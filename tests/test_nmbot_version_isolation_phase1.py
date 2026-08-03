"""Legacy public selector/wire regression tests; excluded from local V3 acceptance."""

import asyncio
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from nmbot_runtime_contract import (
    CONTRACT_VERSION,
    WireContractError,
    validate_chat_response,
    validate_router_chat_ingress,
    validate_worker_chat_request,
)
from nmbot_runtime_contract.selector import (
    SELECTOR_SCHEMA_VERSION,
    SelectorStore,
    SelectorUnavailable,
)
from scripts.nmbot_version_router import CLIENT_SESSION_KEY, SELECTOR_STORE_KEY, _forward, create_app


def request():
    return {"contract_version": CONTRACT_VERSION, "conversation_ref": "conversation:123", "trace_ref": "trace:12345678", "message": "Здравствуйте", "channel": "api", "meta": {"locale": "ru"}}


def worker_request(version="V0"):
    return {**request(), "runtime_version": version}


def response(version="V0"):
    return {"contract_version": CONTRACT_VERSION, "ok": True, "runtime_version": version, "client_answer": "Здравствуйте!", "handoff": False, "error_code": None, "diagnostics": {"code": "ok", "elapsed_ms": 1}}


def test_closed_schema_rejects_unknown_fields_and_wrong_response_version():
    payload = request()
    payload["runtime_version"] = "V3"
    with pytest.raises(WireContractError, match="unknown"):
        validate_router_chat_ingress(payload)
    worker_payload = worker_request()
    worker_payload["provider_payload"] = "no"
    with pytest.raises(WireContractError, match="unknown"):
        validate_worker_chat_request(worker_payload)
    with pytest.raises(WireContractError, match="mismatch"):
        validate_chat_response(response("V1"), expected_version="V0")


def test_router_does_not_import_runtime_packages():
    source = Path("scripts/nmbot_version_router.py").read_text(encoding="utf-8")
    imports = [node.names[0].name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import)]
    from_imports = [node.module or "" for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)]
    assert not any(name.startswith("nmbot_v") for name in imports + from_imports)
    assert "nmbot_runtime_adapter" not in source


def test_router_creates_session_only_during_startup_and_closes_it(tmp_path):
    app = create_app(selector_path=tmp_path / "selector.json", token="secret", timeout_seconds=0.25)
    assert CLIENT_SESSION_KEY not in app

    async def exercise():
        client = TestClient(TestServer(app))
        await client.start_server()
        session = app[CLIENT_SESSION_KEY]
        try:
            assert session.timeout.total == 0.25
            assert not session.closed
        finally:
            await client.close()
        assert session.closed

    asyncio.run(exercise())


def test_router_forward_fails_closed_before_startup_initializes_session(tmp_path, monkeypatch):
    async def exercise():
        app = create_app(selector_path=tmp_path / "selector.json", token="secret")
        await app[SELECTOR_STORE_KEY].set("V1")
        monkeypatch.setenv("NMBOT_V1_INTERNAL_ENDPOINT", "http://127.0.0.1:18081")
        monkeypatch.setenv("NMBOT_V1_INTERNAL_TOKEN", "worker-token")
        response = await _forward(SimpleNamespace(app=app), payload=request(), operation="chat")
        assert response.status == 503
        assert json.loads(response.body) == {"ok": False, "error_code": "runtime_unavailable"}

    asyncio.run(exercise())


def test_chat_forwards_selected_worker_token_only_to_exact_v3_endpoint(tmp_path, monkeypatch):
    async def exercise():
        calls = {"v3": 0, "v2": 0}

        async def v3_handler(incoming):
            calls["v3"] += 1
            assert incoming.headers["Authorization"] == "Bearer v3-worker-token"
            assert await incoming.json() == worker_request("V3")
            return web.json_response(response("V3"))

        async def v2_handler(incoming):
            calls["v2"] += 1
            assert incoming.headers.get("Authorization") != "Bearer v3-worker-token"
            return web.json_response(response("V2"))

        v3_app = web.Application(); v3_app.router.add_post("/api/chat", v3_handler)
        v2_app = web.Application(); v2_app.router.add_post("/api/chat", v2_handler)
        v3 = TestServer(v3_app); v2 = TestServer(v2_app)
        await v3.start_server(); await v2.start_server()
        monkeypatch.setenv("NMBOT_V3_INTERNAL_ENDPOINT", str(v3.make_url("/")).rstrip("/"))
        monkeypatch.setenv("NMBOT_V2_INTERNAL_ENDPOINT", str(v2.make_url("/")).rstrip("/"))
        monkeypatch.setenv("NMBOT_V3_INTERNAL_TOKEN", "v3-worker-token")
        monkeypatch.setenv("NMBOT_V2_INTERNAL_TOKEN", "v2-worker-token")
        router = TestClient(TestServer(create_app(selector_path=tmp_path / "selector.json", token="secret")))
        await router.start_server()
        try:
            selected = await router.put("/api/runtime-version", json={"runtime_version": "V3"}, headers={"Authorization": "Bearer secret"})
            assert selected.status == 200
            result = await router.post("/api/chat", json=request(), headers={"Authorization": "Bearer secret"})
            assert result.status == 200 and await result.json() == response("V3")
            assert calls == {"v3": 1, "v2": 0}
        finally:
            await router.close(); await v3.close(); await v2.close()
    asyncio.run(exercise())


def test_missing_timeout_invalid_response_and_reset_are_fail_closed_or_isolated(tmp_path, monkeypatch):
    async def exercise():
        reset_calls = []

        async def handler(incoming):
            if incoming.path.endswith("reset"):
                reset_calls.append((await incoming.json())["runtime_version"])
                return web.json_response({"contract_version": CONTRACT_VERSION, "ok": True, "runtime_version": "V1", "reset": True, "error_code": None, "diagnostics": {}})
            await asyncio.sleep(0.05)
            return web.json_response(response("V2"))

        downstream_app = web.Application(); downstream_app.router.add_post("/api/chat", handler); downstream_app.router.add_post("/api/reset", handler)
        downstream = TestServer(downstream_app); await downstream.start_server()
        monkeypatch.setenv("NMBOT_V1_INTERNAL_ENDPOINT", str(downstream.make_url("/")).rstrip("/"))
        monkeypatch.setenv("NMBOT_V1_INTERNAL_TOKEN", "v1-worker-token")
        router = TestClient(TestServer(create_app(selector_path=tmp_path / "isolated.json", token="secret", timeout_seconds=0.01)))
        await router.start_server()
        headers = {"Authorization": "Bearer secret"}
        try:
            missing = await router.post("/api/chat", json=request(), headers=headers)
            assert missing.status == 503
            selected = await router.put("/api/runtime-version", json={"runtime_version": "V1"}, headers=headers)
            assert selected.status == 200
            timeout = await router.post("/api/chat", json=request(), headers=headers)
            assert timeout.status == 503
            reset = await router.post("/api/reset", json={key: value for key, value in request().items() if key not in {"message", "channel", "meta"}}, headers=headers)
            assert reset.status == 200 and reset_calls == ["V1"]
            assert json.loads((tmp_path / "isolated.json").read_text(encoding="utf-8")) == {"schema_version": SELECTOR_SCHEMA_VERSION, "runtime_version": "V1"}
        finally:
            await router.close(); await downstream.close()
    asyncio.run(exercise())


def test_router_rejects_invalid_downstream_response(tmp_path, monkeypatch):
    async def exercise():
        async def invalid_handler(incoming):
            return web.json_response(response("V2"))

        downstream_app = web.Application(); downstream_app.router.add_post("/api/chat", invalid_handler)
        downstream = TestServer(downstream_app); await downstream.start_server()
        monkeypatch.setenv("NMBOT_V1_INTERNAL_ENDPOINT", str(downstream.make_url("/")).rstrip("/"))
        monkeypatch.setenv("NMBOT_V1_INTERNAL_TOKEN", "v1-worker-token")
        router = TestClient(TestServer(create_app(selector_path=tmp_path / "state.json", token="secret")))
        await router.start_server()
        try:
            selected = await router.put("/api/runtime-version", json={"runtime_version": "V1"}, headers={"Authorization": "Bearer secret"})
            assert selected.status == 200
            result = await router.post("/api/chat", json=request(), headers={"Authorization": "Bearer secret"})
            assert result.status == 503
            assert (await result.json())["error_code"] == "runtime_unavailable"
        finally:
            await router.close(); await downstream.close()
    asyncio.run(exercise())


def test_redirected_downstream_is_fail_closed_without_following_target(tmp_path, monkeypatch):
    async def exercise():
        calls = {"redirector": 0, "external": 0}

        async def external_handler(incoming):
            calls["external"] += 1
            return web.json_response(response("V1"))

        external_app = web.Application(); external_app.router.add_route("*", "/external", external_handler)
        external = TestServer(external_app); await external.start_server()

        async def redirect_handler(incoming):
            calls["redirector"] += 1
            raise web.HTTPFound(location=str(external.make_url("/external")))

        redirector_app = web.Application(); redirector_app.router.add_post("/api/chat", redirect_handler)
        redirector = TestServer(redirector_app); await redirector.start_server()
        monkeypatch.setenv("NMBOT_V1_INTERNAL_ENDPOINT", str(redirector.make_url("/")).rstrip("/"))
        monkeypatch.setenv("NMBOT_V1_INTERNAL_TOKEN", "v1-worker-token")
        router = TestClient(TestServer(create_app(selector_path=tmp_path / "state.json", token="secret")))
        await router.start_server()
        headers = {"Authorization": "Bearer secret"}
        try:
            selected = await router.put("/api/runtime-version", json={"runtime_version": "V1"}, headers=headers)
            assert selected.status == 200
            result = await router.post("/api/chat", json=request(), headers=headers)
            assert result.status == 503
            assert calls == {"redirector": 1, "external": 0}
        finally:
            await router.close(); await redirector.close(); await external.close()
    asyncio.run(exercise())


def test_selector_persistence_and_health_hide_endpoints(tmp_path, monkeypatch):
    async def exercise():
        monkeypatch.setenv("NMBOT_V0_INTERNAL_ENDPOINT", "http://127.0.0.1:18080")
        monkeypatch.setenv("NMBOT_V0_INTERNAL_TOKEN", "v0-worker-token")
        state = tmp_path / "router-selector.json"
        headers = {"Authorization": "Bearer secret"}
        first = TestClient(TestServer(create_app(selector_path=state, token="secret"))); await first.start_server()
        try:
            changed = await first.put("/api/runtime-version", json={"runtime_version": "V3"}, headers=headers)
            assert changed.status == 200
            health = await first.get("/health")
            body = await health.json()
            assert body["selector_ready"] is True
            assert body["routes"]["V0"] == {"ready": True}
            assert "127.0.0.1" not in str(body)
        finally:
            await first.close()
        second = TestClient(TestServer(create_app(selector_path=state, token="secret"))); await second.start_server()
        try:
            current = await second.get("/api/runtime-version", headers=headers)
            assert (await current.json())["runtime_version"] == "V3"
        finally:
            await second.close()
    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("contents", "unreadable"),
    [
        (None, False),
        ("not-json", False),
        (json.dumps({"schema_version": SELECTOR_SCHEMA_VERSION, "runtime_version": "V4"}), False),
        (json.dumps({"schema_version": SELECTOR_SCHEMA_VERSION, "runtime_version": "V1"}), True),
    ],
    ids=("missing", "malformed", "unsupported", "unreadable"),
)
def test_selector_state_fail_closed_until_protected_initialization(tmp_path, monkeypatch, contents, unreadable):
    async def exercise():
        state = tmp_path / "selector.json"
        if contents is not None:
            state.write_text(contents, encoding="utf-8")
        store = SelectorStore(state)
        if unreadable:
            original_open = Path.open

            def unreadable_open(path, *args, **kwargs):
                if path == state:
                    raise PermissionError("denied")
                return original_open(path, *args, **kwargs)

            monkeypatch.setattr(Path, "open", unreadable_open)
            with pytest.raises(SelectorUnavailable):
                await store.get()
        elif contents is not None:
            with pytest.raises(SelectorUnavailable):
                await store.get()

        router = TestClient(TestServer(create_app(selector_path=state, token="secret")))
        await router.start_server()
        headers = {"Authorization": "Bearer secret"}
        try:
            for path, payload in (
                ("/api/chat", request()),
                ("/api/reset", {key: value for key, value in request().items() if key not in {"message", "channel", "meta"}}),
            ):
                response = await router.post(path, json=payload, headers=headers)
                assert response.status == 503
                assert await response.json() == {"ok": False, "error_code": "selector_unavailable"}
            current = await router.get("/api/runtime-version", headers=headers)
            assert current.status == 503
            assert await current.json() == {"ok": False, "error_code": "selector_unavailable"}
            health = await router.get("/health")
            health_body = await health.json()
            assert health_body["selector_ready"] is False
            assert str(state) not in json.dumps(health_body)

            initialized = await router.put("/api/runtime-version", json={"runtime_version": "V1"}, headers=headers)
            assert initialized.status == 200
            if unreadable:
                with original_open(state, "r", encoding="utf-8") as state_file:
                    read_state = state_file.read()
            else:
                read_state = state.read_text(encoding="utf-8")
            assert json.loads(read_state) == {"schema_version": SELECTOR_SCHEMA_VERSION, "runtime_version": "V1"}
        finally:
            await router.close()
    asyncio.run(exercise())


def test_selector_initialization_enables_route_and_rejects_v4(tmp_path, monkeypatch):
    async def exercise():
        calls = 0

        async def handler(incoming):
            nonlocal calls
            calls += 1
            assert await incoming.json() == worker_request("V1")
            return web.json_response(response("V1"))

        downstream_app = web.Application(); downstream_app.router.add_post("/api/chat", handler)
        downstream = TestServer(downstream_app); await downstream.start_server()
        monkeypatch.setenv("NMBOT_V1_INTERNAL_ENDPOINT", str(downstream.make_url("/")).rstrip("/"))
        monkeypatch.setenv("NMBOT_V1_INTERNAL_TOKEN", "v1-worker-token")
        router = TestClient(TestServer(create_app(selector_path=tmp_path / "selector.json", token="secret")))
        await router.start_server()
        headers = {"Authorization": "Bearer secret"}
        try:
            rejected = await router.put("/api/runtime-version", json={"runtime_version": "V4"}, headers=headers)
            assert rejected.status == 400
            initialized = await router.put("/api/runtime-version", json={"runtime_version": "V1"}, headers=headers)
            assert initialized.status == 200
            result = await router.post("/api/chat", json=request(), headers=headers)
            assert result.status == 200 and await result.json() == response("V1")
            assert calls == 1
        finally:
            await router.close(); await downstream.close()
    asyncio.run(exercise())


def test_internal_selector_keeps_put_canonical_and_has_no_post_alias(tmp_path):
    async def exercise():
        router = TestClient(TestServer(create_app(selector_path=tmp_path / "selector.json", token="secret")))
        await router.start_server()
        headers = {"Authorization": "Bearer secret"}
        try:
            post = await router.post("/api/runtime-version", json={"runtime_version": "V1"}, headers=headers)
            assert post.status == 405
            put = await router.put("/api/runtime-version", json={"runtime_version": "V1"}, headers=headers)
            assert put.status == 200
            current = await router.get("/api/runtime-version", headers=headers)
            assert current.status == 200
            assert await current.json() == {"ok": True, "runtime_version": "V1"}
        finally:
            await router.close()

    asyncio.run(exercise())


def test_missing_or_wrong_worker_token_fails_closed_and_health_hides_secrets(tmp_path, monkeypatch):
    async def exercise():
        calls = 0

        async def handler(incoming):
            nonlocal calls
            calls += 1
            assert incoming.headers["Authorization"] == "Bearer correct-worker-token"
            return web.json_response(response("V1"))

        downstream_app = web.Application(); downstream_app.router.add_post("/api/chat", handler)
        downstream = TestServer(downstream_app); await downstream.start_server()
        monkeypatch.setenv("NMBOT_V1_INTERNAL_ENDPOINT", str(downstream.make_url("/")).rstrip("/"))
        router = TestClient(TestServer(create_app(selector_path=tmp_path / "selector.json", token="ingress-token")))
        await router.start_server()
        headers = {"Authorization": "Bearer ingress-token"}
        try:
            await router.put("/api/runtime-version", json={"runtime_version": "V1"}, headers=headers)
            missing = await router.post("/api/chat", json=request(), headers=headers)
            assert missing.status == 503 and calls == 0
            health = await router.get("/health")
            assert (await health.json())["routes"]["V1"] == {"ready": False}

            monkeypatch.setenv("NMBOT_V1_INTERNAL_TOKEN", "wrong-worker-token")
            wrong = await router.post("/api/chat", json=request(), headers=headers)
            assert wrong.status == 503 and calls == 1

            monkeypatch.setenv("NMBOT_V1_INTERNAL_TOKEN", "correct-worker-token")
            good = await router.post("/api/chat", json=request(), headers=headers)
            assert good.status == 200 and calls == 2
            body = await (await router.get("/health")).json()
            assert "correct-worker-token" not in json.dumps(body)
            assert str(downstream.make_url("/")) not in json.dumps(body)
        finally:
            await router.close(); await downstream.close()
    asyncio.run(exercise())
