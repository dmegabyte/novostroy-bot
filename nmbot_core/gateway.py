"""Direct, bounded gateway boundary for the canonical V6 prompt pair."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping

from aiohttp import ClientSession, ClientTimeout


PROMPT1_STAGE = "v6_simple_prompt1"
PROMPT2_STAGE = "v6_simple_prompt2"
MCP_SERVER = "novostroym"
MCP_TOOL = "get_flat_info"
DEFAULT_MODEL = "google/gemini-3.1-flash-lite-preview"
_ATTEMPT_REF = re.compile(r"[A-Za-z0-9._:-]{1,200}")
_TASK_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")


@dataclass(frozen=True)
class ToolTrace:
    attempt_ref: str
    server: str
    tool: str
    call_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_ref, str) or not _ATTEMPT_REF.fullmatch(self.attempt_ref):
            raise ValueError("invalid_tool_attempt_ref")
        if (self.server, self.tool) != (MCP_SERVER, MCP_TOOL):
            raise ValueError("invalid_tool_identity")
        if type(self.call_count) is not int or not 0 <= self.call_count <= 3:
            raise ValueError("invalid_tool_call_count")


@dataclass(frozen=True)
class GatewayResult:
    output: str | Mapping[str, Any]
    attempt_ref: str
    tool_trace: ToolTrace | None = None


class GatewayHttpClient:
    """Minimal owner for exactly one gateway create/poll/result task."""

    def __init__(self, base_url: str, *, poll_interval: float = 3.0) -> None:
        base = str(base_url).rstrip("/")
        if not re.fullmatch(r"https?://[A-Za-z0-9._:-]+", base) or not 0 < poll_interval <= 10:
            raise ValueError("invalid_gateway_endpoint")
        self._base, self._poll_interval = base, poll_interval

    async def _run_gateway_request_once(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
        if type(timeout) is not int or not 1 <= timeout <= 180:
            raise ValueError("invalid_gateway_timeout")
        stage = str(request_data.pop("_payload_stage", ""))
        if stage not in {PROMPT1_STAGE, PROMPT2_STAGE}:
            raise ValueError("invalid_payload_stage")
        model = str(request_data.get("model") or "")
        if os.getenv("NMBOT_OPENROUTER_EXCLUDE_REASONING", "").strip().lower() in {"1", "true", "yes", "on"} and model.startswith("google/gemini"):
            request_data.setdefault("reasoning", {"exclude": True})
        payload = {"agent_name": "gateway-agent", "endpoint": "/process", "request_data": request_data, "timeout_seconds": timeout, "max_retries": 0}
        started = time.monotonic()
        async with ClientSession(timeout=ClientTimeout(total=min(timeout + 10, 190))) as session:
            async with session.post(f"{self._base}/api/v1/tasks/api", json=payload, headers=headers) as response:
                if response.status not in {200, 201}:
                    raise RuntimeError("gateway_create_failed")
                created = await response.json()
            task_id = created.get("id") if isinstance(created, Mapping) else None
            if not isinstance(task_id, str) or not _TASK_REF.fullmatch(task_id):
                raise RuntimeError("gateway_missing_task_id")
            while time.monotonic() - started < timeout:
                async with session.get(f"{self._base}/api/v1/tasks/api/{task_id}/status", headers=headers) as response:
                    if response.status != 200: raise RuntimeError("gateway_status_failed")
                    status_data = await response.json()
                status = str(status_data.get("status") or "").lower() if isinstance(status_data, Mapping) else ""
                if status in {"failed", "cancelled"}: raise RuntimeError("gateway_task_failed")
                if status == "completed":
                    async with session.get(f"{self._base}/api/v1/tasks/api/{task_id}/result", headers=headers) as response:
                        if response.status != 200: raise RuntimeError("gateway_result_failed")
                        result = await response.json()
                    result_obj = result.get("result") if isinstance(result, Mapping) else None
                    result_obj = result_obj if isinstance(result_obj, Mapping) else result
                    if not isinstance(result_obj, Mapping) or result_obj.get("error"):
                        raise RuntimeError("gateway_task_failed")
                    response_payload = result_obj.get("response")
                    text = response_payload if isinstance(response_payload, str) else next((value for key in ("response", "text", "content", "answer") if isinstance(response_payload, Mapping) and isinstance((value := response_payload.get(key)), str) and value.strip()), "")
                    if not isinstance(text, str) or not text.strip(): raise RuntimeError("gateway_empty_response")
                    metadata = result_obj.get("metadata")
                    safe_meta = {"_gateway_task_id": task_id}
                    if isinstance(metadata, Mapping) and isinstance(metadata.get("v6_tool_trace"), Mapping): safe_meta["v6_tool_trace"] = dict(metadata["v6_tool_trace"])
                    return text, safe_meta
                await asyncio.sleep(self._poll_interval)
        raise RuntimeError("gateway_timeout")


class DirectTransport:
    """One gateway task only; retries and provider fallback are not owned here."""

    def __init__(self, client: Any, timeout: int = 90) -> None:
        self._client, self._timeout = client, timeout

    async def complete(self, payload: Mapping[str, Any]) -> GatewayResult:
        token = os.getenv("OVERMIND_TOKEN") or os.getenv("GATEWAY_POLL_TOKEN") or ""
        output, meta = await self._client._run_gateway_request_once(
            dict(payload), {"Authorization": f"Bearer {token}"}, self._timeout,
        )
        if not isinstance(meta, Mapping) or meta.get("_upstream_error") is True:
            raise RuntimeError("upstream_error")
        attempt = meta.get("_gateway_task_id")
        if not isinstance(attempt, str) or not _ATTEMPT_REF.fullmatch(attempt):
            raise RuntimeError("missing_attempt_ref")
        raw_trace = meta.get("v6_tool_trace")
        trace = None
        if isinstance(raw_trace, Mapping) and set(raw_trace) == {"actual_server", "actual_tool", "call_count"}:
            try:
                trace = ToolTrace(attempt, raw_trace["actual_server"], raw_trace["actual_tool"], raw_trace["call_count"])
            except (TypeError, ValueError):
                pass
        return GatewayResult(output, attempt, trace)


class PromptGateway:
    """Payload builder for a single V6 prompt stage."""

    def __init__(self, transport: Any, stage: str, *, system_prompt: str, model: str) -> None:
        if stage not in {PROMPT1_STAGE, PROMPT2_STAGE}:
            raise ValueError("invalid_gateway_stage")
        if not isinstance(system_prompt, str) or not system_prompt.strip() or not isinstance(model, str) or not model:
            raise ValueError("invalid_gateway_configuration")
        self._transport, self._stage = transport, stage
        self._system_prompt, self._model = system_prompt, model

    async def run(self, model_input: Mapping[str, Any], *, repair: bool = False) -> GatewayResult:
        prompt = self._system_prompt + ("\n\nИсправь только формат ответа по той же схеме. Верни только JSON." if repair else "")
        payload: dict[str, Any] = {
            "_payload_stage": self._stage,
            "query": json.dumps(dict(model_input), ensure_ascii=False, separators=(",", ":")),
            "service": "openrouter",
            "model": self._model,
            "system_prompt": prompt,
            "parameters": {"temperature": 0 if self._stage == PROMPT1_STAGE else 0.2, "max_tokens": 1800},
            "external_api_key": os.getenv("OPENROUTER_API_KEY", ""),
        }
        if self._stage == PROMPT1_STAGE:
            payload["mcp_servers"] = [MCP_SERVER]
        result = await self._transport.complete(payload)
        if not isinstance(result, GatewayResult):
            raise RuntimeError("invalid_transport_result")
        if self._stage == PROMPT2_STAGE and result.tool_trace is not None:
            raise RuntimeError("prompt2_tool_trace")
        return result


def build_prompt_pair(*, base_url: str, system_prompt1: str, system_prompt2: str, model: str = DEFAULT_MODEL, timeout: int = 90) -> tuple[PromptGateway, PromptGateway]:
    transport = DirectTransport(GatewayHttpClient(base_url), timeout=timeout)
    return (
        PromptGateway(transport, PROMPT1_STAGE, system_prompt=system_prompt1, model=model),
        PromptGateway(transport, PROMPT2_STAGE, system_prompt=system_prompt2, model=model),
    )
