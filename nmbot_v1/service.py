"""Private V1 runtime worker. This module owns only V1 ports and state."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from time import monotonic
from typing import Any

import aiohttp
from aiohttp import web

from nmbot_runtime_contract.wire import CONTRACT_VERSION, validate_chat_response
from nmbot_runtime_service_host.http import ServiceTurn, create_app as create_host_app

from . import RUNTIME_VERSION
from .provider_adapters import V1GatewayOneModelResponsePort, V1GatewayPlannerPort, V1GatewaySearchPort
from .runtime import run_turn
from .state import PHONE_RE, V1ConversationState


class V1GatewayClient:
    """Minimal V1 gateway transport: no cross-version retry or fallback policy."""
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("OVERMIND_URL") or "").rstrip("/")
        self.session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
        if not self.base_url:
            return "", {"_safe_fallback": True, "_upstream_error": True}
        self.session = self.session if self.session and not self.session.closed else aiohttp.ClientSession()
        deadline = monotonic() + timeout

        def request_timeout() -> aiohttp.ClientTimeout:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            return aiohttp.ClientTimeout(total=remaining)

        try:
            async with self.session.post(f"{self.base_url}/api/v1/tasks/api", json={"agent_name": "gateway-agent", "endpoint": "/process", "request_data": request_data, "timeout_seconds": timeout, "max_retries": 0}, headers=headers, timeout=request_timeout()) as response:
                task = await response.json()
                if response.status not in (200, 201) or not task.get("id"):
                    return "", {"_safe_fallback": True, "_upstream_error": True}
            while monotonic() < deadline:
                async with self.session.get(f"{self.base_url}/api/v1/tasks/api/{task['id']}/status", headers=headers, timeout=request_timeout()) as response:
                    status = await response.json()
                if status.get("status") in {"completed", "failed", "cancelled"}:
                    async with self.session.get(f"{self.base_url}/api/v1/tasks/api/{task['id']}/result", headers=headers, timeout=request_timeout()) as response:
                        result = await response.json()
                    value = result.get("result") or result
                    if not isinstance(value, dict) or status.get("status") != "completed":
                        return "", {"_safe_fallback": True, "_upstream_error": True}
                    return str(value.get("response") or value.get("text") or ""), {}
                await asyncio.sleep(min(1, max(0, deadline - monotonic())))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return "", {"_safe_fallback": True, "_upstream_error": True}
        return "", {"_safe_fallback": True, "_upstream_error": True}


def _safe_response(*, ok: bool, answer: str, code: str | None, elapsed_ms: int = 0) -> dict[str, Any]:
    return validate_chat_response({"contract_version": CONTRACT_VERSION, "ok": ok, "runtime_version": RUNTIME_VERSION,
        "client_answer": answer, "handoff": False, "error_code": code,
        "diagnostics": {"code": code or "ok", "elapsed_ms": max(0, min(elapsed_ms, 120000))}}, expected_version=RUNTIME_VERSION)


def build_turn(*, planner_port: Any = None, search_port: Any = None, response_model_port: Any = None,
               response_model_mode: str = "off"):
    async def turn(payload: dict[str, Any], state_before: dict[str, Any] | None) -> ServiceTurn:
        # Callback CRM behavior is monolith-owned and has not been migrated to this worker.
        if PHONE_RE.search(payload["message"]):
            return ServiceTurn(_safe_response(ok=False, answer="Не могу безопасно принять номер в этом контуре. Попробуйте позже.", code="v1_phone_flow_unmigrated"))
        if planner_port is None:
            return ServiceTurn(_safe_response(ok=False, answer="Сейчас не получилось безопасно запустить подбор. Условия не меняла.", code="missing_v1_planner_port"))
        started = monotonic()
        result = await run_turn(payload["message"], state_before, planner_port, search_port,
                                response_model_port=response_model_port,
                                response_model_mode=response_model_mode if response_model_mode in {"shadow", "publish"} else "off")
        safe_code = str(result.trace.get("safe_code") or "")
        if result.stage == "safe_error":
            return ServiceTurn(_safe_response(ok=False, answer=result.response_text, code=safe_code or "v1_runtime_error",
                                              elapsed_ms=int((monotonic() - started) * 1000)))
        return ServiceTurn(_safe_response(ok=True, answer=result.response_text, code=None,
                                          elapsed_ms=int((monotonic() - started) * 1000)), result.state)
    return turn


def create_app(*, state_path: Path, journal_path: Path, token: str, release_identity: str,
               planner_port: Any = None, search_port: Any = None, response_model_port: Any = None) -> web.Application:
    gateway = None
    if planner_port is None and os.getenv("NMBOT_V1_GATEWAY_ENABLED") == "1":
        gateway = V1GatewayClient()
        planner_port, search_port = V1GatewayPlannerPort(gateway), V1GatewaySearchPort(gateway)
        if os.getenv("NMBOT_V1_ONE_MODEL_GPT55_MODE") in {"shadow", "publish"}:
            response_model_port = V1GatewayOneModelResponsePort(gateway)
    app = create_host_app(runtime_version=RUNTIME_VERSION, token=token, release_identity=release_identity,
                          state_path=state_path, journal_path=journal_path,
                          turn=build_turn(planner_port=planner_port, search_port=search_port, response_model_port=response_model_port,
                                          response_model_mode=os.getenv("NMBOT_V1_ONE_MODEL_GPT55_MODE", "off")),
                          reset=lambda: V1ConversationState.clean().to_dict())
    if gateway:
        app.on_cleanup.append(lambda _app: gateway.close())
    return app
