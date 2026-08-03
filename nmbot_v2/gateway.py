"""Small V2-owned gateway client with no legacy retry or fallback behaviour.

The client speaks only the task-gateway transport required by the local worker.
It returns stable public error codes, owns its aiohttp session, and never exposes
gateway payloads, response text, credentials, or exception messages.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

import aiohttp


class V2GatewayErrorCode(str, Enum):
    TIMEOUT = "v2_gateway_timeout"
    UNAVAILABLE = "v2_gateway_unavailable"
    INVALID_RESPONSE = "v2_gateway_invalid_response"
    UPSTREAM_FAILURE = "v2_gateway_upstream_failure"


@dataclass(frozen=True)
class V2GatewayResult:
    text: str = ""
    error_code: V2GatewayErrorCode | None = None

    @property
    def ok(self) -> bool:
        return self.error_code is None and bool(self.text.strip())


@dataclass(frozen=True)
class V2GatewayConfig:
    """Explicit gateway settings; callers obtain values from their own config seam."""

    base_url: str
    token: str
    request_timeout_seconds: float = 25.0
    poll_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not str(self.base_url or "").strip() or not str(self.token or "").strip():
            raise ValueError("v2_gateway_config_required")
        if not 0.1 <= float(self.request_timeout_seconds) <= 120.0:
            raise ValueError("v2_gateway_timeout_invalid")
        if not 0.05 <= float(self.poll_interval_seconds) <= 10.0:
            raise ValueError("v2_gateway_poll_interval_invalid")


class V2GatewayClient:
    """One-shot V2 task invocation; deliberately no retries or model fallback."""

    def __init__(self, config: V2GatewayConfig, *, session: aiohttp.ClientSession | None = None) -> None:
        self._config = config
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> "V2GatewayClient":
        await self._get_session()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def invoke(self, request_data: Mapping[str, Any], *, timeout_seconds: float | None = None) -> V2GatewayResult:
        timeout = self._bounded_timeout(timeout_seconds)
        try:
            async with asyncio.timeout(timeout):
                return await self._invoke_once(dict(request_data), timeout)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return V2GatewayResult(error_code=V2GatewayErrorCode.TIMEOUT)
        except aiohttp.ClientError:
            return V2GatewayResult(error_code=V2GatewayErrorCode.UNAVAILABLE)
        except (TypeError, ValueError, KeyError):
            return V2GatewayResult(error_code=V2GatewayErrorCode.INVALID_RESPONSE)
        except Exception:
            return V2GatewayResult(error_code=V2GatewayErrorCode.UNAVAILABLE)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None))
            self._owns_session = True
        return self._session

    async def _invoke_once(self, request_data: dict[str, Any], timeout: float) -> V2GatewayResult:
        session = await self._get_session()
        base = self._config.base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {self._config.token}"}
        envelope = {
            "agent_name": "gateway-agent",
            "endpoint": "/process",
            "request_data": request_data,
            "timeout_seconds": max(1, int(timeout)),
            "max_retries": 0,
        }
        async with session.post(f"{base}/api/v1/tasks/api", json=envelope, headers=headers) as response:
            created = await _json_object(response)
            if response.status not in (200, 201):
                return V2GatewayResult(error_code=V2GatewayErrorCode.UPSTREAM_FAILURE)
        task_id = _task_id(created.get("id"))
        if task_id is None:
            return V2GatewayResult(error_code=V2GatewayErrorCode.INVALID_RESPONSE)
        while True:
            async with session.get(f"{base}/api/v1/tasks/api/{task_id}/status", headers=headers) as response:
                status_data = await _json_object(response)
                if response.status != 200:
                    return V2GatewayResult(error_code=V2GatewayErrorCode.UPSTREAM_FAILURE)
            status = str(status_data.get("status") or "").lower()
            if status in {"failed", "cancelled"}:
                return V2GatewayResult(error_code=V2GatewayErrorCode.UPSTREAM_FAILURE)
            if status == "completed":
                async with session.get(f"{base}/api/v1/tasks/api/{task_id}/result", headers=headers) as response:
                    result = await _json_object(response)
                    if response.status != 200:
                        return V2GatewayResult(error_code=V2GatewayErrorCode.UPSTREAM_FAILURE)
                return _result_text(result)
            if status not in {"queued", "pending", "created", "running"}:
                return V2GatewayResult(error_code=V2GatewayErrorCode.INVALID_RESPONSE)
            await asyncio.sleep(float(self._config.poll_interval_seconds))

    def _bounded_timeout(self, value: float | None) -> float:
        candidate = self._config.request_timeout_seconds if value is None else value
        try:
            return min(120.0, max(0.1, float(candidate)))
        except (TypeError, ValueError):
            return float(self._config.request_timeout_seconds)


async def _json_object(response: aiohttp.ClientResponse) -> dict[str, Any]:
    value = await response.json(content_type=None)
    return dict(value) if isinstance(value, Mapping) else {}


def _task_id(value: object) -> str | None:
    candidate = str(value or "").strip()
    return candidate if candidate and len(candidate) <= 120 and candidate.replace("-", "").replace("_", "").replace(".", "").isalnum() else None


def _result_text(result: Mapping[str, Any]) -> V2GatewayResult:
    outer = result.get("result") if isinstance(result.get("result"), Mapping) else result
    if not isinstance(outer, Mapping) or outer.get("error"):
        return V2GatewayResult(error_code=V2GatewayErrorCode.UPSTREAM_FAILURE)
    response = outer.get("response")
    if isinstance(response, Mapping):
        response = next((response.get(key) for key in ("response", "text", "content", "answer") if isinstance(response.get(key), str)), "")
    if not isinstance(response, str) or not response.strip():
        return V2GatewayResult(error_code=V2GatewayErrorCode.INVALID_RESPONSE)
    return V2GatewayResult(text=response)
