from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .contracts import V4_MCP_SERVER, V4_MODEL, V4_PAYLOAD_STAGE, V4_PROMPT_SOURCE, V4Error
from .prompt_provenance import build_prompt_provenance, prompt_identity


REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = REPO_ROOT / V4_PROMPT_SOURCE


class V4GatewayOnePromptPort:
    def __init__(self, gateway_client: Any, *, prompt_path: Path | None = None, model: str = V4_MODEL, timeout_env: str = "NMBOT_V4_TIMEOUT") -> None:
        self.gateway_client = gateway_client
        self.prompt_path = prompt_path or PROMPT_PATH
        self.model = model
        self.timeout_env = timeout_env
        self.prompt_provenance = build_prompt_provenance({"id": "v4.flat_search", "source": V4_PROMPT_SOURCE, "usage": "configured"}, coverage="configured_only")

    def request_payload(self, turn_input: Mapping[str, Any]) -> dict[str, Any]:
        prompt = self.prompt_path.read_text(encoding="utf-8")
        self.prompt_provenance = build_prompt_provenance(prompt_identity(prompt_id="v4.flat_search", source=V4_PROMPT_SOURCE, path=self.prompt_path, usage="invoked"), coverage="complete")
        payload = {
            "_payload_stage": V4_PAYLOAD_STAGE,
            "query": "V4_USER_TURN=" + json.dumps(_bounded_json(turn_input), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "service": "openrouter",
            "model": self.model,
            "system_prompt": prompt,
            "parameters": {"temperature": _float_env("NMBOT_V4_TEMPERATURE", 0.1), "max_tokens": _int_env("NMBOT_V4_MAX_TOKENS", 1800, max_value=20_000)},
            "mcp_servers": [V4_MCP_SERVER],
        }
        api_key = os.getenv("OPENROUTER_API_KEY") or ""
        if api_key:
            payload["external_api_key"] = api_key
        return payload

    async def complete(self, turn_input: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        if self.model != V4_MODEL:
            raise V4Error("v4_model_mismatch")
        client = self.gateway_client
        if client is None or not hasattr(client, "_run_gateway_request_once"):
            raise V4Error("gateway_once_missing")
        headers = {"Authorization": f"Bearer {os.getenv('OVERMIND_TOKEN') or os.getenv('GATEWAY_POLL_TOKEN') or ''}"}
        payload = self.request_payload(turn_input)
        timeout = _int_env(self.timeout_env, 90, max_value=600)
        try:
            raw, meta = await client._run_gateway_request_once(payload, headers, timeout)
        except Exception as exc:
            wrapped = V4Error("gateway_once_exception")
            setattr(wrapped, "call_attempted", 1)
            setattr(wrapped, "v4_gateway_trace", {
                "gateway_task_id": None,
                "model": self.model,
                "gateway_status": "error",
                "response_chars": 0,
                "response_parse": "empty",
                "message_chars": 0,
                "call_attempted": True,
            })
            raise wrapped from exc
        safe_meta = dict(meta) if isinstance(meta, dict) else {}
        safe_meta["_v4_gateway_call_attempted"] = True
        return str(raw or ""), safe_meta


def _int_env(name: str, default: int, *, max_value: int = 300) -> int:
    try:
        return max(1, min(int(os.getenv(name, str(default))), max_value))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.0, min(float(os.getenv(name, str(default))), 1.0))
    except (TypeError, ValueError):
        return default


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return None
    if isinstance(value, Mapping):
        return {str(k)[:80]: _bounded_json(v, depth=depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [_bounded_json(v, depth=depth + 1) for v in list(value)[:40]]
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:200]
