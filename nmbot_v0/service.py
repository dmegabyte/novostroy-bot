"""Private V0 worker with V0-only state, prompt and gateway ownership."""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from time import monotonic
from typing import Any, Awaitable, Callable, Mapping

import aiohttp
from aiohttp import web

from nmbot_runtime_contract.wire import CONTRACT_VERSION, validate_chat_response
from nmbot_runtime_service_host.http import ServiceTurn, create_app as create_host_app

from . import RUNTIME_VERSION
from .contracts import V0State
from .runtime import V0TurnProcessor
from .search_contract import load_prompt


_PHONE_RE = re.compile(r"(?<!\d)(?:\+?7|8)?[\s().-]*\d(?:[\s().-]*\d){8,}(?!\d)")
ScenarioPort = Callable[[dict[str, Any]], Mapping[str, Any] | str | Awaitable[Mapping[str, Any] | str]]


class V0GatewayClient:
    """Single-deadline V0 scenario transport; no fallback or retry policy."""
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("NMBOT_V0_GATEWAY_URL") or "").rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def scenario(self, context: dict[str, Any], *, deadline: float) -> Mapping[str, Any] | str:
        if not self.base_url:
            raise RuntimeError("missing_v0_scenario_gateway")
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError
        self._session = self._session if self._session and not self._session.closed else aiohttp.ClientSession()
        payload = {"contract": "nmbot_v0_scenario_gateway", "prompt": load_prompt(), "context": context, "max_retries": 0}
        try:
            async with self._session.post(f"{self.base_url}/process", json=payload, timeout=aiohttp.ClientTimeout(total=remaining)) as response:
                if response.status != 200:
                    raise RuntimeError("v0_scenario_gateway_unavailable")
                result = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise RuntimeError("v0_scenario_gateway_unavailable") from exc
        value = result.get("response", result) if isinstance(result, Mapping) else result
        if not isinstance(value, (str, Mapping)):
            raise RuntimeError("v0_scenario_gateway_invalid_response")
        return value


def _response(*, ok: bool, answer: str, code: str | None, elapsed_ms: int = 0) -> dict[str, Any]:
    return validate_chat_response({"contract_version": CONTRACT_VERSION, "ok": ok, "runtime_version": RUNTIME_VERSION,
        "client_answer": answer, "handoff": False, "error_code": code,
        "diagnostics": {"code": code or "ok", "elapsed_ms": max(0, min(elapsed_ms, 120000))}}, expected_version=RUNTIME_VERSION)


def build_turn(*, scenario_port: ScenarioPort | None = None, total_timeout_seconds: float = 20.0):
    async def turn(payload: dict[str, Any], state_before: dict[str, Any] | None) -> ServiceTurn:
        # CRM callback was not migrated into V0 ownership: never collect a phone.
        if _PHONE_RE.search(payload["message"]):
            return ServiceTurn(_response(ok=False, answer="Не могу безопасно принять номер в этом контуре. Попробуйте позже.", code="v0_phone_flow_unmigrated"))
        if scenario_port is None:
            return ServiceTurn(_response(ok=False, answer="Сейчас не получилось безопасно запустить подбор. Условия не меняла.", code="missing_v0_scenario_gateway"))
        try:
            state = V0State.from_dict(state_before) if state_before else V0State()
        except ValueError:
            return ServiceTurn(_response(ok=False, answer="Не получилось безопасно прочитать предыдущий диалог. Условия не меняла.", code="v0_state_invalid"))
        started = monotonic()
        deadline = started + max(0.01, min(float(total_timeout_seconds), 120.0))

        async def bounded_port(context: dict[str, Any]) -> Mapping[str, Any] | str:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            result = scenario_port(context)
            if hasattr(result, "__await__"):
                return await asyncio.wait_for(result, timeout=remaining)
            return result

        try:
            result = await V0TurnProcessor(scenario_search=bounded_port).process_async(payload["message"], state, conversation_ref=payload["conversation_ref"])
        except asyncio.TimeoutError:
            return ServiceTurn(_response(ok=False, answer="Подбор не успел ответить. Условия не меняла.", code="v0_scenario_timeout", elapsed_ms=int((monotonic() - started) * 1000)))
        except Exception:
            return ServiceTurn(_response(ok=False, answer="Сейчас не получилось безопасно запустить подбор. Условия не меняла.", code="v0_scenario_gateway_failed", elapsed_ms=int((monotonic() - started) * 1000)))
        elapsed = int((monotonic() - started) * 1000)
        if not result.ok:
            code = result.error_code or "v0_runtime_failed"
            return ServiceTurn(_response(ok=False, answer=result.message, code=code, elapsed_ms=elapsed))
        return ServiceTurn(_response(ok=True, answer=result.message, code=None, elapsed_ms=elapsed), result.state.to_dict())
    return turn


def create_app(*, state_path: Path, journal_path: Path, token: str, release_identity: str,
               scenario_port: ScenarioPort | None = None, total_timeout_seconds: float = 20.0) -> web.Application:
    gateway: V0GatewayClient | None = None
    if scenario_port is None and os.getenv("NMBOT_V0_GATEWAY_ENABLED") == "1":
        gateway = V0GatewayClient()
        async def scenario_port(context: dict[str, Any]) -> Mapping[str, Any] | str:
            return await gateway.scenario(context, deadline=monotonic() + total_timeout_seconds)
    app = create_host_app(runtime_version=RUNTIME_VERSION, token=token, release_identity=release_identity,
                          state_path=state_path, journal_path=journal_path,
                          turn=build_turn(scenario_port=scenario_port, total_timeout_seconds=total_timeout_seconds),
                          reset=lambda: V0State().to_dict())
    if gateway:
        app.on_cleanup.append(lambda _app: gateway.close())
    return app
