from __future__ import annotations

import asyncio

from aiohttp import web
from aiohttp.test_utils import TestServer

from nmbot_core import DirectTransport, GatewayHttpClient, PromptGateway


def test_direct_gateway_http_transport_preserves_single_task_contract(monkeypatch):
    received = []

    async def create(request):
        received.append((request.headers.get("Authorization"), await request.json()))
        return web.json_response({"id": "task-1"})

    async def status(request): return web.json_response({"status": "completed"})
    async def result(request): return web.json_response({"result": {"response": {"content": '{"action":"reply"}'}, "metadata": {"v6_tool_trace": {"actual_server": "novostroym", "actual_tool": "get_flat_info", "call_count": 0}}}})

    async def run():
        app = web.Application(); app.router.add_post("/api/v1/tasks/api", create); app.router.add_get("/api/v1/tasks/api/task-1/status", status); app.router.add_get("/api/v1/tasks/api/task-1/result", result)
        async with TestServer(app) as server:
            monkeypatch.setenv("OVERMIND_TOKEN", "test-token")
            gateway = PromptGateway(DirectTransport(GatewayHttpClient(str(server.make_url("/")).rstrip("/")), timeout=5), "v6_simple_prompt1", system_prompt="prompt", model="test-model")
            reply = await gateway.run({"message": "test"})
            assert reply.output == '{"action":"reply"}' and reply.attempt_ref == "task-1"
            assert reply.tool_trace is not None and reply.tool_trace.call_count == 0
        assert received == [("Bearer test-token", {"agent_name": "gateway-agent", "endpoint": "/process", "timeout_seconds": 5, "max_retries": 0, "request_data": {"query": '{"message":"test"}', "service": "openrouter", "model": "test-model", "system_prompt": "prompt", "parameters": {"temperature": 0, "max_tokens": 1800}, "external_api_key": "", "mcp_servers": ["novostroym"]}})]
    asyncio.run(run())


def test_gateway_rejects_invalid_endpoint_and_task_id():
    try: GatewayHttpClient("ftp://unsafe")
    except ValueError as exc: assert str(exc) == "invalid_gateway_endpoint"
    else: raise AssertionError("invalid endpoint accepted")
