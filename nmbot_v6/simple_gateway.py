"""Direct H108 transport adapter for the V6-simple pair."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

PROMPT1_MODEL = "google/gemini-3.1-flash-lite-preview"
PROMPT2_MODEL = "google/gemini-3.1-flash-lite-preview"
MCP_SERVER = "novostroym"
MCP_TOOL = "get_flat_info"
ROOT = Path(__file__).resolve().parents[1]
P1_PROMPT = ROOT / "prompts" / "v6_simple_search_agent.txt"
P2_PROMPT = ROOT / "prompts" / "v6_simple_answer_writer.txt"
REPAIR = "Исправь только формат ответа по той же схеме. Не расширяй поиск. Верни только JSON."
_ATTEMPT_REF = re.compile(r"[A-Za-z0-9._:-]{1,200}")


@dataclass(frozen=True)
class SimpleToolTrace:
    attempt_ref: str
    server: str
    tool: str
    call_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_ref, str) or not _ATTEMPT_REF.fullmatch(self.attempt_ref):
            raise ValueError("invalid_tool_attempt_ref")
        if self.server != MCP_SERVER or self.tool != MCP_TOOL:
            raise ValueError("invalid_tool_identity")
        if type(self.call_count) is not int or not 0 <= self.call_count <= 3:
            raise ValueError("invalid_tool_call_count")


@dataclass(frozen=True)
class SimpleGatewayResult:
    output: str | Mapping[str, Any]
    attempt_ref: str
    tool_trace: SimpleToolTrace | None = None


class DirectTransport:
    def __init__(self, client: Any, timeout: int = 90) -> None:
        self.client, self.timeout = client, timeout

    async def complete(self, payload: Mapping[str, Any]) -> SimpleGatewayResult:
        output, meta = await self.client._run_gateway_request_once(dict(payload), {"Authorization": f"Bearer {os.getenv('OVERMIND_TOKEN') or os.getenv('GATEWAY_POLL_TOKEN') or ''}"}, self.timeout)
        if isinstance(meta, Mapping) and meta.get("_upstream_error") is True:
            raise RuntimeError("upstream_error")
        attempt_value = meta.get("_gateway_task_id") if isinstance(meta, Mapping) else None
        if not isinstance(attempt_value, str) or not _ATTEMPT_REF.fullmatch(attempt_value):
            raise RuntimeError("missing_attempt_ref")
        attempt = attempt_value
        trace = None
        raw_trace = meta.get("v6_tool_trace") if isinstance(meta, Mapping) else None
        if raw_trace is not None:
            if not isinstance(raw_trace, Mapping) or set(raw_trace) != {"actual_server", "actual_tool", "call_count"}:
                raw_trace = None
            try:
                if raw_trace is not None:
                    trace = SimpleToolTrace(attempt, raw_trace["actual_server"], raw_trace["actual_tool"], raw_trace["call_count"])
            except (TypeError, ValueError) as exc:
                trace = None
        return SimpleGatewayResult(output, attempt, trace)


class SimpleGateway:
    def __init__(self, transport: Any, stage: str) -> None:
        if stage not in {"prompt1", "prompt2"}:
            raise ValueError("invalid stage")
        self.transport, self.stage = transport, stage

    async def run(self, model_input: Mapping[str, Any], *, repair: bool = False) -> SimpleGatewayResult:
        p1 = self.stage == "prompt1"
        prompt = (P1_PROMPT if p1 else P2_PROMPT).read_text(encoding="utf-8")
        if repair:
            prompt += "\n\n" + REPAIR
        payload = {
            "_payload_stage": "v6_simple_prompt1" if p1 else "v6_simple_prompt2",
            "query": json.dumps(dict(model_input), ensure_ascii=False, separators=(",", ":")),
            "service": "openrouter", "model": PROMPT1_MODEL if p1 else PROMPT2_MODEL,
            "system_prompt": prompt,
            "parameters": {"temperature": 0 if p1 else 0.2, "max_tokens": 1800},
            "external_api_key": os.getenv("OPENROUTER_API_KEY", ""),
        }
        if p1:
            payload["mcp_servers"] = [MCP_SERVER]
        result = await self.transport.complete(payload)
        if not isinstance(result, SimpleGatewayResult):
            raise RuntimeError("invalid_transport_result")
        if not p1 and result.tool_trace is not None:
            raise RuntimeError("prompt2_tool_trace")
        return result
