from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from nmbot_runtime_contract import CONTRACT_VERSION
from scripts.nmbot_router_client import NMBotRouterClient, RouterClientConfig, RouterClientError


def chat_request() -> dict[str, object]:
    return {"contract_version": CONTRACT_VERSION, "conversation_ref": "conversation:123", "trace_ref": "trace:12345678", "message": "Здравствуйте", "channel": "api", "meta": {"locale": "ru"}}


def chat_response(version: str = "V1") -> dict[str, object]:
    return {"contract_version": CONTRACT_VERSION, "ok": True, "runtime_version": version, "client_answer": "Здравствуйте", "handoff": False, "error_code": None, "diagnostics": {"code": "ok", "elapsed_ms": 1}}


def reset_request() -> dict[str, object]:
    return {key: value for key, value in chat_request().items() if key not in {"message", "channel", "meta"}}


def reset_response(version: str = "V1") -> dict[str, object]:
    return {"contract_version": CONTRACT_VERSION, "ok": True, "runtime_version": version, "reset": True, "error_code": None, "diagnostics": {"code": "reset"}}


def test_config_is_http_loopback_only_and_bounded() -> None:
    assert RouterClientConfig("http://127.0.0.1:18080/", "router-secret", 0.1).base_url == "http://127.0.0.1:18080"
    for url in ("https://127.0.0.1", "http://example.test", "http://user:pass@127.0.0.1", "http://127.0.0.1/?q=1", "http://127.0.0.1/#part", "http://127.0.0.1/prefix"):
        with pytest.raises(ValueError, match="invalid_router_base_url"):
            RouterClientConfig(url, "router-secret")
    for timeout in (0, 30.1, True):
        with pytest.raises(ValueError, match="invalid_router_timeout"):
            RouterClientConfig("http://127.0.0.1", "router-secret", timeout)


def test_typed_calls_use_exact_method_payload_and_authorization_only() -> None:
    async def scenario() -> None:
        seen: list[tuple[str, str, dict[str, str], object]] = []

        async def handler(request: web.Request) -> web.Response:
            payload = await request.json() if request.can_read_body else None
            seen.append((request.method, request.path, dict(request.headers), payload))
            if request.path == "/api/chat":
                return web.json_response(chat_response())
            if request.path == "/api/reset":
                return web.json_response(reset_response())
            if request.method == "GET":
                return web.json_response({"ok": True, "runtime_version": "V1"})
            return web.json_response({"ok": True, "runtime_version": "V2"})

        app = web.Application(); app.router.add_route("*", "/api/{tail:.*}", handler)
        server = TestServer(app); await server.start_server()
        session = ClientSession()
        client = NMBotRouterClient(RouterClientConfig(str(server.make_url("/")).rstrip("/"), "router-secret"), session=session)
        try:
            assert (await client.chat(chat_request(), expected_runtime_version="V1"))["client_answer"] == "Здравствуйте"
            assert (await client.reset(reset_request(), expected_runtime_version="V1"))["reset"] is True
            assert await client.get_selector() == "V1"
            assert await client.set_selector("V2") == "V2"
            assert [item[:2] for item in seen] == [("POST", "/api/chat"), ("POST", "/api/reset"), ("GET", "/api/runtime-version"), ("PUT", "/api/runtime-version")]
            for _, _, headers, _ in seen:
                assert headers["Authorization"] == "Bearer router-secret"
                assert "X-Api-Key" not in headers and "Cookie" not in headers
            assert seen[0][3] == chat_request() and seen[1][3] == reset_request()
            assert seen[3][3] == {"runtime_version": "V2"}
        finally:
            await client.aclose()
            assert not session.closed
            await session.close(); await server.close()
    asyncio.run(scenario())


def test_owned_session_closes_and_caller_session_does_not() -> None:
    async def scenario() -> None:
        async def selector(_: web.Request) -> web.Response:
            return web.json_response({"ok": True, "runtime_version": "V1"})
        app = web.Application(); app.router.add_get("/api/runtime-version", selector)
        server = TestServer(app); await server.start_server()
        config = RouterClientConfig(str(server.make_url("/")).rstrip("/"), "secret")
        owned = NMBotRouterClient(config)
        try:
            assert await owned.get_selector() == "V1"
            session = owned._session
            await owned.aclose()
            assert session is not None and session.closed
        finally:
            await server.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 401, 500])
def test_http_failures_fail_closed_and_do_not_leak_secrets(status: int) -> None:
    async def scenario() -> None:
        calls = 0
        async def handler(_: web.Request) -> web.Response:
            nonlocal calls; calls += 1
            return web.Response(status=status, text="router-secret http://127.0.0.1/private")
        app = web.Application(); app.router.add_post("/api/chat", handler)
        server = TestServer(app); await server.start_server()
        client = NMBotRouterClient(RouterClientConfig(str(server.make_url("/")).rstrip("/"), "router-secret"))
        try:
            with pytest.raises(RouterClientError) as raised:
                await client.chat(chat_request(), expected_runtime_version="V1")
            assert str(raised.value) == "router_unavailable"
            assert "secret" not in str(raised.value) and "127.0.0.1" not in str(raised.value)
            assert calls == 1
        finally:
            await client.aclose(); await server.close()
    asyncio.run(scenario())


def test_timeout_malformed_version_mismatch_and_redirect_fail_closed_once() -> None:
    async def scenario() -> None:
        calls = {"slow": 0, "bad": 0, "redirect": 0, "target": 0}
        mode = "slow"
        async def target(_: web.Request) -> web.Response:
            calls["target"] += 1; return web.json_response(chat_response())
        target_app = web.Application(); target_app.router.add_route("*", "/target", target)
        target_server = TestServer(target_app); await target_server.start_server()
        async def handler(_: web.Request) -> web.Response:
            if mode == "slow":
                calls["slow"] += 1; await asyncio.sleep(0.1); return web.json_response({"ok": True, "runtime_version": "V1"})
            if mode == "bad":
                calls["bad"] += 1; return web.Response(text="not-json")
            calls["redirect"] += 1
            raise web.HTTPFound(str(target_server.make_url("/target")))
        app = web.Application(); app.router.add_get("/api/runtime-version", handler)
        server = TestServer(app); await server.start_server()
        async def assert_code(code: str) -> None:
            client = NMBotRouterClient(RouterClientConfig(str(server.make_url("/")).rstrip("/"), "router-secret", 0.05))
            try:
                with pytest.raises(RouterClientError, match=code):
                    await client.get_selector()
            finally:
                await client.aclose()
        try:
            await assert_code("router_unavailable")
            mode = "bad"; await assert_code("router_unavailable")
            mode = "redirect"; await assert_code("router_unavailable")
            assert calls == {"slow": 1, "bad": 1, "redirect": 1, "target": 0}
        finally:
            await server.close(); await target_server.close()
    asyncio.run(scenario())


def test_invalid_wire_and_response_are_redacted_and_import_closure_is_small() -> None:
    async def scenario() -> None:
        async def mismatched(_: web.Request) -> web.Response:
            return web.json_response(chat_response("V2"))
        app = web.Application(); app.router.add_post("/api/chat", mismatched)
        server = TestServer(app); await server.start_server()
        client = NMBotRouterClient(RouterClientConfig(str(server.make_url("/")).rstrip("/"), "router-secret"))
        try:
            with pytest.raises(RouterClientError, match="invalid_router_response"):
                await client.chat(chat_request(), expected_runtime_version="V1")
            with pytest.raises(RouterClientError, match="invalid_request"):
                await client.chat({"token": "router-secret"}, expected_runtime_version="V1")
        finally:
            await client.aclose(); await server.close()
    asyncio.run(scenario())
    source = Path("scripts/nmbot_router_client.py").read_text(encoding="utf-8")
    imports = [name.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import) for name in node.names]
    imported_from = [node.module or "" for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)]
    prohibited = ("os", "nmbot_api_server", "nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v3", "jivo", "bridge", "requests", "httpx", "socket")
    assert not any(part in name.lower() for name in imports + imported_from for part in prohibited)
