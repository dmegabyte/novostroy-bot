"""One local-only boundary proof for the prepared Wave 3 router package."""
from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from nmbot_runtime_contract import CONTRACT_VERSION
from scripts.nmbot_router_client import NMBotRouterClient, RouterClientConfig, RouterClientError
from scripts.nmbot_version_router import create_app


def _chat_request() -> dict[str, object]:
    return {"contract_version": CONTRACT_VERSION, "conversation_ref": "conversation:wave3", "trace_ref": "trace:wave3-0001", "message": "Здравствуйте", "channel": "api", "meta": {"locale": "ru"}}


def _chat_response(version: str) -> dict[str, object]:
    return {"contract_version": CONTRACT_VERSION, "ok": True, "runtime_version": version, "client_answer": "Здравствуйте!", "handoff": False, "error_code": None, "diagnostics": {"code": "ok", "elapsed_ms": 1}}


def test_wave3_local_boundary_proves_selection_isolated_and_cutover_stops(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        calls = 0
        fail_worker = False

        async def v3_worker(request: web.Request) -> web.Response:
            nonlocal calls, fail_worker
            calls += 1
            assert request.headers["Authorization"] == "Bearer v3-worker-token"
            assert await request.json() == {**_chat_request(), "runtime_version": "V3"}
            return web.Response(status=500) if fail_worker else web.json_response(_chat_response("V3"))

        worker_app = web.Application()
        worker_app.router.add_post("/api/chat", v3_worker)
        worker = TestServer(worker_app)
        await worker.start_server()
        monkeypatch.setenv("NMBOT_V3_INTERNAL_ENDPOINT", str(worker.make_url("/")).rstrip("/"))
        monkeypatch.setenv("NMBOT_V3_INTERNAL_TOKEN", "v3-worker-token")
        router = TestServer(create_app(selector_path=tmp_path / "selector.json", token="router-token"))
        await router.start_server()
        session = ClientSession()
        client = NMBotRouterClient(RouterClientConfig(str(router.make_url("/")).rstrip("/"), "router-token"), session=session)
        try:
            assert await client.set_selector("V3") == "V3"
            assert (tmp_path / "selector.json").read_text(encoding="utf-8") == '{"schema_version":"nmbot.runtime-selector.v1","runtime_version":"V3"}'
            assert await client.get_selector() == "V3"
            assert await client.chat(_chat_request(), expected_runtime_version="V3") == _chat_response("V3")
            assert calls == 1

            fail_worker = True
            try:
                await client.chat(_chat_request(), expected_runtime_version="V3")
            except RouterClientError as error:
                assert error.code == "router_unavailable"
            else:
                raise AssertionError("worker failure must fail closed")
            assert calls == 2
        finally:
            await client.aclose()
            await session.close()
            await router.close()
            await worker.close()

    asyncio.run(scenario())
