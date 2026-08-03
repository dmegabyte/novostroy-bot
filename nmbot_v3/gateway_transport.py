"""V3-owned gateway task transport at the outer host boundary.

Only the task envelope proven by the legacy client is shared as a source
contract.  This module owns its V3 request mapping, has one attempt only, and
never imports a legacy client or its retry/fallback policy.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import math
import os
import re
from time import monotonic
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from .evidence_provider import V3EvidenceProviderRequest
from .planner_provider import V3PlannerProviderRequest
from .provider_invocation import V3InvocationOperation, V3TransportRequest, V3TransportResponse
from .writer_adapter import (
    V3StructuredWriterRequest,
    V3_WRITER_GATEWAY_RESULT_MARKER,
    V3_WRITER_GATEWAY_RESULT_OUTPUT_FIELD,
    V3WriterGatewayResult,
    build_v3_writer_gateway_request,
)


_ENV_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,127}\Z")
_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}\Z")
_TERMINAL = frozenset({"completed", "failed", "cancelled"})


class V3GatewayConfigurationError(ValueError):
    """Stable startup error; its text intentionally contains no secret value."""


class V3GatewayProtocolError(RuntimeError):
    """Untrusted gateway data did not meet the small V3 transport contract."""


@dataclass(frozen=True)
class V3GatewayConfig:
    base_url: str
    gateway_token_env: str
    provider_api_key_env: str
    planner_model: str
    evidence_model: str
    writer_model: str
    task_timeout_seconds: float
    poll_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if not isinstance(self.base_url, str) or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise V3GatewayConfigurationError("invalid_v3_gateway_base_url")
        if any(not isinstance(name, str) or not _ENV_NAME_RE.fullmatch(name) for name in (self.gateway_token_env, self.provider_api_key_env)):
            raise V3GatewayConfigurationError("invalid_v3_gateway_credential_env_name")
        if any(not isinstance(model, str) or not model.strip() or len(model) > 200 for model in (self.planner_model, self.evidence_model, self.writer_model)):
            raise V3GatewayConfigurationError("invalid_v3_gateway_model")
        for value, code in ((self.task_timeout_seconds, "invalid_v3_gateway_timeout"), (self.poll_interval_seconds, "invalid_v3_gateway_poll_interval")):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise V3GatewayConfigurationError(code)
        if self.task_timeout_seconds > 120 or self.poll_interval_seconds > self.task_timeout_seconds:
            raise V3GatewayConfigurationError("invalid_v3_gateway_timeout")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        object.__setattr__(self, "planner_model", self.planner_model.strip())
        object.__setattr__(self, "evidence_model", self.evidence_model.strip())
        object.__setattr__(self, "writer_model", self.writer_model.strip())
        object.__setattr__(self, "task_timeout_seconds", float(self.task_timeout_seconds))
        object.__setattr__(self, "poll_interval_seconds", float(self.poll_interval_seconds))

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> "V3GatewayConfig":
        source = os.environ if environ is None else environ

        def required(name: str) -> str:
            value = source.get(name)
            if not isinstance(value, str) or not value.strip():
                raise V3GatewayConfigurationError(f"missing_v3_gateway_config:{name}")
            return value.strip()

        def number(name: str, default: str | None = None) -> float:
            try:
                return float(required(name) if default is None else source.get(name, default))
            except (TypeError, ValueError) as exc:
                raise V3GatewayConfigurationError(f"invalid_v3_gateway_config:{name}") from exc

        return cls(
            base_url=required("NMBOT_V3_GATEWAY_URL"),
            gateway_token_env=required("NMBOT_V3_GATEWAY_TOKEN_ENV"),
            provider_api_key_env=required("NMBOT_V3_PROVIDER_API_KEY_ENV"),
            planner_model=required("NMBOT_V3_PLANNER_MODEL"),
            evidence_model=required("NMBOT_V3_EVIDENCE_MODEL"),
            writer_model=required("NMBOT_V3_WRITER_MODEL"),
            task_timeout_seconds=number("NMBOT_V3_GATEWAY_TIMEOUT_SECONDS"),
            poll_interval_seconds=number("NMBOT_V3_GATEWAY_POLL_INTERVAL_SECONDS", "1"),
        )


class V3GatewayTaskTransport:
    """One V3 task lifecycle over an injected aiohttp-compatible session."""

    def __init__(self, config: V3GatewayConfig, *, gateway_token: str, provider_api_key: str, session_factory: Callable[[], Any]) -> None:
        if not isinstance(config, V3GatewayConfig):
            raise V3GatewayConfigurationError("invalid_v3_gateway_config")
        if not isinstance(gateway_token, str) or not gateway_token or not isinstance(provider_api_key, str) or not provider_api_key:
            raise V3GatewayConfigurationError("missing_v3_gateway_credential")
        if not callable(session_factory):
            raise V3GatewayConfigurationError("invalid_v3_gateway_session_factory")
        self._config = config
        self._gateway_token = gateway_token
        self._provider_api_key = provider_api_key
        self._session_factory = session_factory
        self._session: Any = None

    @classmethod
    def from_environ(cls, *, environ: Mapping[str, str] | None = None, session_factory: Callable[[], Any]) -> "V3GatewayTaskTransport":
        source = os.environ if environ is None else environ
        config = V3GatewayConfig.from_environ(source)
        gateway_token = source.get(config.gateway_token_env, "")
        provider_api_key = source.get(config.provider_api_key_env, "")
        return cls(config, gateway_token=gateway_token, provider_api_key=provider_api_key, session_factory=session_factory)

    async def close(self) -> None:
        session, self._session = self._session, None
        if session is not None and not getattr(session, "closed", False):
            result = session.close()
            if hasattr(result, "__await__"):
                await result

    async def invoke(self, request: V3TransportRequest[Any]) -> V3TransportResponse[Any]:
        if not isinstance(request, V3TransportRequest):
            raise V3GatewayProtocolError("invalid_v3_gateway_request")
        request_data = self._map_request(request)
        deadline = monotonic() + self._config.task_timeout_seconds
        session = self._get_session()
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._gateway_token}"}
        envelope = {"agent_name": "gateway-agent", "endpoint": "/process", "request_data": request_data,
                    "timeout_seconds": int(math.ceil(self._config.task_timeout_seconds)), "max_retries": 0}
        async with session.post(f"{self._config.base_url}/api/v1/tasks/api", json=envelope, headers=headers) as response:
            task = await self._json(response)
            if getattr(response, "status", None) not in (200, 201):
                raise V3GatewayProtocolError("v3_gateway_task_create_failed")
        task_id = task.get("id") if isinstance(task, dict) else None
        if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
            raise V3GatewayProtocolError("v3_gateway_task_id_invalid")
        while monotonic() < deadline:
            async with session.get(f"{self._config.base_url}/api/v1/tasks/api/{task_id}/status", headers=headers) as response:
                status_data = await self._json(response)
                if getattr(response, "status", None) != 200:
                    raise V3GatewayProtocolError("v3_gateway_status_failed")
            status = status_data.get("status") if isinstance(status_data, dict) else None
            if status in _TERMINAL:
                if status != "completed":
                    raise V3GatewayProtocolError("v3_gateway_task_not_completed")
                async with session.get(f"{self._config.base_url}/api/v1/tasks/api/{task_id}/result", headers=headers) as response:
                    result = await self._json(response)
                    if getattr(response, "status", None) != 200:
                        raise V3GatewayProtocolError("v3_gateway_result_failed")
                return V3TransportResponse(request.request_id, self._parse_result(result, request.operation))
            if not isinstance(status, str):
                raise V3GatewayProtocolError("v3_gateway_status_invalid")
            await asyncio.sleep(min(self._config.poll_interval_seconds, max(0.0, deadline - monotonic())))
        raise TimeoutError("v3_gateway_task_timeout")

    def _get_session(self) -> Any:
        if self._session is None or getattr(self._session, "closed", False):
            self._session = self._session_factory()
        return self._session

    async def _json(self, response: Any) -> Any:
        try:
            return await response.json()
        except Exception as exc:
            raise V3GatewayProtocolError("v3_gateway_json_invalid") from exc

    def _map_request(self, request: V3TransportRequest[Any]) -> dict[str, Any]:
        payload = request.payload
        if request.operation is V3InvocationOperation.PLANNER and isinstance(payload, V3PlannerProviderRequest):
            return self._provider_request(payload.prompt, payload.payload, payload.response_schema, self._config.planner_model, 900)
        if request.operation is V3InvocationOperation.EVIDENCE and isinstance(payload, V3EvidenceProviderRequest):
            return self._provider_request(payload.prompt, payload.payload, payload.response_schema, self._config.evidence_model, 1600)
        if request.operation is V3InvocationOperation.WRITER and isinstance(payload, V3StructuredWriterRequest):
            writer_request = build_v3_writer_gateway_request(payload)
            return self._provider_request(
                self._writer_prompt(), writer_request.to_payload(),
                writer_request.to_payload()["response_schema"], self._config.writer_model, 1800,
            )
        raise V3GatewayProtocolError("v3_gateway_operation_payload_mismatch")

    def _provider_request(self, prompt: str, payload: Mapping[str, Any], response_schema: Mapping[str, Any], model: str, max_tokens: int) -> dict[str, Any]:
        return {
            "query": json.dumps({"v3_request": dict(payload), "response_schema": dict(response_schema)}, ensure_ascii=False, separators=(",", ":")),
            "service": "openrouter", "model": model, "system_prompt": prompt,
            "parameters": {"temperature": 0.0, "max_tokens": max_tokens}, "external_api_key": self._provider_api_key,
        }

    @staticmethod
    def _writer_prompt() -> str:
        """Load the V3-owned prompt at the host boundary, never from env/V2."""
        from pathlib import Path

        return (Path(__file__).with_name("prompts") / "answer_writer.txt").read_text(encoding="utf-8")

    @staticmethod
    def _parse_result(result: Any, operation: V3InvocationOperation) -> Any:
        result_obj = result.get("result", result) if isinstance(result, dict) else result
        if not isinstance(result_obj, dict) or result_obj.get("error"):
            raise V3GatewayProtocolError("v3_gateway_result_invalid")
        response = result_obj.get("response")
        if operation is V3InvocationOperation.WRITER:
            if not isinstance(response, Mapping):
                raise V3GatewayProtocolError("v3_gateway_writer_result_invalid")
            if response.get("result_marker") != V3_WRITER_GATEWAY_RESULT_MARKER:
                raise V3GatewayProtocolError("v3_gateway_writer_result_invalid")
            output = response.get(V3_WRITER_GATEWAY_RESULT_OUTPUT_FIELD)
            if not isinstance(output, Mapping):
                raise V3GatewayProtocolError("v3_gateway_writer_result_invalid")
            return V3WriterGatewayResult(V3_WRITER_GATEWAY_RESULT_MARKER, output)
        if isinstance(response, (str, dict)):
            return response
        raise V3GatewayProtocolError("v3_gateway_response_invalid")
