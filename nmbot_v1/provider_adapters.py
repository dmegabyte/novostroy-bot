from __future__ import annotations

import json
import os
from pathlib import Path
from time import monotonic
from typing import Any, Mapping

from .contracts import SCHEMA_VERSION, V1Error, V1IntentPlan, deep_thaw
from .prompt_provenance import build_prompt_provenance, identity_from_path
from .one_model_response import MODEL as ONE_MODEL_RESPONSE_MODEL, QUERY_MARKER as ONE_MODEL_QUERY_MARKER, build_one_model_input, parse_one_model_response
from .search import parse_search_provider_result
from .search_contract import V1SearchRequest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLANNER_PROMPT_SOURCE = "prompts/v1/intent_planner.txt"
SEARCH_PROMPT_SOURCE = "prompts/v1/search_mcp.txt"
ONE_MODEL_RESPONSE_PROMPT_SOURCE = "prompts/candidates/v1_one_model_gpt55_experiment_v1.txt"
PLANNER_PROMPT_PATH = REPO_ROOT / PLANNER_PROMPT_SOURCE
SEARCH_PROMPT_PATH = REPO_ROOT / SEARCH_PROMPT_SOURCE
ONE_MODEL_RESPONSE_PROMPT_PATH = REPO_ROOT / ONE_MODEL_RESPONSE_PROMPT_SOURCE
PLANNER_MODEL = "google/gemini-2.5-flash"
SEARCH_MODEL = "google/gemini-3.1-flash-lite-preview"
ONE_MODEL_RESPONSE_PAYLOAD_STAGE = "v1_one_model_gpt55_response_test"
MCP_ALIAS = "novostroym"
PLANNER_PAYLOAD_STAGE = "v1_intent_planner"
SEARCH_PAYLOAD_STAGE = "v1_search_mcp"
_SAFE_PROVIDER_CODES = {"safe_fallback", "upstream_error", "empty_response", "invalid_json", "invalid_schema", "gateway_missing"}


class V1GatewayPlannerPort:
    def __init__(self, gateway_client: Any, *, prompt_path: Path | None = None, model: str = PLANNER_MODEL, timeout_env: str = "NMBOT_V1_PLANNER_TIMEOUT") -> None:
        self.gateway_client = gateway_client
        self.prompt_path = prompt_path or PLANNER_PROMPT_PATH
        self.model = model
        self.timeout_env = timeout_env
        self.prompt_provenance = build_prompt_provenance([identity_from_path("v1.planner", PLANNER_PROMPT_SOURCE, self.prompt_path, usage="configured")], coverage="configured_only")

    def request_payload(self, planner_input: Mapping[str, Any]) -> dict[str, Any]:
        prompt = self.prompt_path.read_text(encoding="utf-8")
        self.prompt_provenance = build_prompt_provenance([identity_from_path("v1.planner", PLANNER_PROMPT_SOURCE, self.prompt_path, usage="invoked")], coverage="complete")
        return _with_external_key({
            "_payload_stage": PLANNER_PAYLOAD_STAGE,
            "query": "V1_PLANNER_INPUT=" + json.dumps(_bounded_json(planner_input), ensure_ascii=False, sort_keys=True),
            "service": "openrouter",
            "model": self.model,
            "system_prompt": prompt,
            "parameters": {"temperature": 0, "max_tokens": _int_env("NMBOT_V1_PLANNER_MAX_TOKENS", 1200, max_value=20_000)},
        })

    async def plan(self, planner_input: dict[str, Any]) -> V1IntentPlan:
        raw, meta = await _run_gateway(self.gateway_client, self.request_payload(planner_input), timeout_env=self.timeout_env, default_timeout=25)
        _raise_on_gateway_failure(raw, meta)
        data = _strict_json_object(raw)
        try:
            return V1IntentPlan.from_dict(data)
        except V1Error:
            raise
        except Exception as exc:  # pragma: no cover - defensive contract fence
            raise V1Error("invalid_schema") from exc


class V1GatewaySearchPort:
    def __init__(self, gateway_client: Any, *, prompt_path: Path | None = None, model: str = SEARCH_MODEL, timeout_env: str = "NMBOT_V1_SEARCH_TIMEOUT") -> None:
        self.gateway_client = gateway_client
        self.prompt_path = prompt_path or SEARCH_PROMPT_PATH
        self.model = model
        self.timeout_env = timeout_env
        self.prompt_provenance = build_prompt_provenance([identity_from_path("v1.search", SEARCH_PROMPT_SOURCE, self.prompt_path, usage="configured")], coverage="configured_only")

    def request_payload(self, request: V1SearchRequest) -> dict[str, Any]:
        prompt = self.prompt_path.read_text(encoding="utf-8")
        self.prompt_provenance = build_prompt_provenance([identity_from_path("v1.search", SEARCH_PROMPT_SOURCE, self.prompt_path, usage="invoked")], coverage="complete")
        envelope = request.to_dict()
        return _with_external_key({
            "_payload_stage": SEARCH_PAYLOAD_STAGE,
            "query": "V1_SEARCH_REQUEST=" + json.dumps(_bounded_json(envelope), ensure_ascii=False, sort_keys=True) + "\nВерни только строгий JSON по V1 schema_version/cards/attempts.",
            "service": "openrouter",
            "model": self.model,
            "system_prompt": prompt,
            "parameters": {"temperature": 0.1, "max_tokens": _int_env("NMBOT_V1_SEARCH_MAX_TOKENS", 3500, max_value=20_000)},
            "mcp_servers": [MCP_ALIAS],
        })

    async def search(self, request: V1SearchRequest) -> dict[str, Any]:
        payload = self.request_payload(request)
        deadline = monotonic() + _int_env(self.timeout_env, 90)
        initial_timeout = _remaining_timeout_seconds(deadline)
        if initial_timeout is None:
            raise V1Error("upstream_error")
        raw, meta = await _run_gateway(
            self.gateway_client,
            payload,
            timeout_env=self.timeout_env,
            default_timeout=90,
            timeout=initial_timeout,
        )
        try:
            data = _validated_search_data(raw, meta, request)
            retry_attempts: list[dict[str, Any]] = []
        except V1Error as exc:
            if str(exc) != "invalid_json":
                raise
            retry_payload = dict(payload)
            retry_payload["query"] = str(payload["query"]) + "\nFORMAT_RECOVERY=previous output was invalid JSON; return one compact complete JSON object and nothing else."
            remaining_timeout = _remaining_timeout_seconds(deadline)
            if remaining_timeout is None:
                raise V1Error("upstream_error")
            retry_raw, retry_meta = await _run_gateway(
                self.gateway_client,
                retry_payload,
                timeout_env=self.timeout_env,
                default_timeout=90,
                timeout=remaining_timeout,
            )
            data = _validated_search_data(retry_raw, retry_meta, request)
            retry_attempts = [{"status": "failed", "code": "invalid_json", "model": self.model}]
        cards = data.get("cards") if isinstance(data.get("cards"), list) else []
        return {"schema_version": SCHEMA_VERSION, "cards": cards, "attempts": [*retry_attempts, *_safe_attempts(data.get("attempts"))][:5]}


class V1GatewayOneModelResponsePort:
    def __init__(self, gateway_client: Any, *, prompt_path: Path | None = None, model: str = ONE_MODEL_RESPONSE_MODEL, timeout_env: str = "NMBOT_V1_ONE_MODEL_GPT55_TIMEOUT") -> None:
        self.gateway_client = gateway_client
        self.prompt_path = prompt_path or ONE_MODEL_RESPONSE_PROMPT_PATH
        self.model = model
        self.timeout_env = timeout_env
        self.prompt_provenance = build_prompt_provenance([identity_from_path("v1.one_model_gpt55_response", ONE_MODEL_RESPONSE_PROMPT_SOURCE, self.prompt_path, usage="configured")], coverage="configured_only")

    def request_payload(self, model_input: Mapping[str, Any]) -> dict[str, Any]:
        prompt = self.prompt_path.read_text(encoding="utf-8")
        self.prompt_provenance = build_prompt_provenance([identity_from_path("v1.one_model_gpt55_response", ONE_MODEL_RESPONSE_PROMPT_SOURCE, self.prompt_path, usage="invoked")], coverage="complete")
        return _with_external_key({
            "_payload_stage": ONE_MODEL_RESPONSE_PAYLOAD_STAGE,
            "query": ONE_MODEL_QUERY_MARKER + json.dumps(_bounded_json(model_input), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "service": "openrouter",
            "model": self.model,
            "system_prompt": prompt,
            "parameters": {"temperature": 0.3, "max_tokens": _int_env("NMBOT_V1_ONE_MODEL_GPT55_MAX_TOKENS", 1800, max_value=20_000)},
        })

    async def present(self, model_input: Mapping[str, Any]) -> dict[str, Any]:
        if self.model != ONE_MODEL_RESPONSE_MODEL:
            raise V1Error("one_model_must_be_gpt55")
        raw, meta = await _run_gateway(self.gateway_client, self.request_payload(model_input), timeout_env=self.timeout_env, default_timeout=90)
        _raise_on_gateway_failure(raw, meta)
        return parse_one_model_response(raw, model_input)


async def _run_gateway(client: Any, request_data: dict[str, Any], *, timeout_env: str, default_timeout: int, timeout: int | None = None) -> tuple[str, dict[str, Any]]:
    if client is None or not hasattr(client, "_run_gateway_request"):
        raise V1Error("gateway_missing")
    headers = {"Authorization": f"Bearer {os.getenv('OVERMIND_TOKEN') or os.getenv('GATEWAY_POLL_TOKEN') or ''}"}
    timeout = timeout if timeout is not None else _int_env(timeout_env, default_timeout)
    raw, meta = await client._run_gateway_request(request_data, headers, timeout)
    return str(raw or ""), meta if isinstance(meta, dict) else {}


def _remaining_timeout_seconds(deadline: float) -> int | None:
    remaining = deadline - monotonic()
    timeout = min(int(remaining), 300)
    if timeout < 1:
        return None
    return timeout


def _raise_on_gateway_failure(raw: Any, meta: Mapping[str, Any]) -> None:
    if meta.get("_safe_fallback") or meta.get("safe_fallback"):
        raise V1Error("safe_fallback")
    if meta.get("_upstream_error") or meta.get("upstream_error"):
        raise V1Error("upstream_error")
    if not str(raw or "").strip():
        raise V1Error("empty_response")


def _validated_search_data(raw: Any, meta: Mapping[str, Any], request: V1SearchRequest) -> dict[str, Any]:
    _raise_on_gateway_failure(raw, meta)
    data = _strict_json_object(raw)
    parse_search_provider_result(data, dict(request.hard_constraints))
    return data


def _strict_json_object(text: Any) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```json\n") and raw.endswith("\n```"):
        raw = raw[len("```json\n") : -len("\n```")].strip()
    if not (raw.startswith("{") and raw.endswith("}")):
        raise V1Error("invalid_json")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise V1Error("invalid_json") from exc
    if not isinstance(data, dict):
        raise V1Error("invalid_json")
    return data


def _safe_attempts(value: Any) -> list[dict[str, Any]]:
    attempts = value if isinstance(value, list) else []
    safe: list[dict[str, Any]] = []
    for item in attempts[:5]:
        if not isinstance(item, Mapping):
            continue
        out: dict[str, Any] = {}
        status = str(item.get("status") or "").strip().lower()
        if status in {"ok", "failed", "empty", "partial"}:
            out["status"] = status
        code = str(item.get("code") or item.get("error_code") or "").strip().lower()
        if code in _SAFE_PROVIDER_CODES:
            out["code"] = code
        model = str(item.get("model") or "").strip()
        if model in {PLANNER_MODEL, SEARCH_MODEL}:
            out["model"] = model
        duration = item.get("duration_ms")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            out["duration_ms"] = max(0, min(int(duration), 300_000))
        if out:
            safe.append(out)
    return safe


def _with_external_key(data: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("OPENROUTER_API_KEY") or ""
    if api_key:
        data["external_api_key"] = api_key
    return data


def _int_env(name: str, default: int, *, max_value: int = 300) -> int:
    try:
        return max(1, min(int(os.getenv(name, str(default))), max_value))
    except (TypeError, ValueError):
        return default


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if isinstance(value, Mapping):
        return {str(k)[:80]: _bounded_json(v, depth=depth + 1) for k, v in list(value.items())[:80]}
    if isinstance(value, (list, tuple)):
        return [_bounded_json(v, depth=depth + 1) for v in list(value)[:80]]
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return deep_thaw(value)
