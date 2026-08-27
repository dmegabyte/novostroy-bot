"""Direct, bounded gateway boundary for the canonical V6 prompt pair."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping


PROMPT1_STAGE = "v6_simple_prompt1"
PROMPT2_STAGE = "v6_simple_prompt2"
MCP_SERVER = "novostroym"
MCP_TOOL = "get_flat_info"
_ATTEMPT_REF = re.compile(r"[A-Za-z0-9._:-]{1,200}")


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
