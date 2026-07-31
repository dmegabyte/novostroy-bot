from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


ENV_MODEL = "NMBOT_V0_ANSWER_WRITER_MODEL"
ENV_TIMEOUT = "NMBOT_V0_ANSWER_WRITER_TIMEOUT"
DEFAULT_MODEL = "google/gemini-3.6-flash"
DEFAULT_TIMEOUT = 60
TEMPERATURE = 0.4
MAX_TOKENS = 2000
REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = REPO_ROOT / "prompts" / "v0_answer_writer.txt"
PAYLOAD_STAGE = "conversation_answer_v0_writer_diagnostic"


def _timeout_from_env(environ: Mapping[str, str] | None = None) -> int:
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_TIMEOUT, DEFAULT_TIMEOUT)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def config_status(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = environ if environ is not None else os.environ
    return {
        "provider": "gateway",
        "model": str(env.get(ENV_MODEL) or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "timeout": _timeout_from_env(env),
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "service": "openrouter",
    }


def request_payload(assignment: dict[str, Any], *, prompt: str | None = None) -> dict[str, Any]:
    status = config_status()
    payload: dict[str, Any] = {
        "_payload_stage": PAYLOAD_STAGE,
        "query": "V0_ANSWER_WRITER_INPUT=" + json.dumps(assignment, ensure_ascii=False, sort_keys=True),
        "service": "openrouter",
        "model": status["model"],
        "system_prompt": prompt if prompt is not None else PROMPT_PATH.read_text(encoding="utf-8").strip(),
        "parameters": {"temperature": TEMPERATURE, "max_tokens": MAX_TOKENS},
    }
    api_key = os.getenv("OPENROUTER_API_KEY") or ""
    if api_key:
        payload["external_api_key"] = api_key
    return payload


def _safe_meta(meta: Mapping[str, Any], *, model: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "provider": "gateway",
        "service": "openrouter",
        "model": str(model or DEFAULT_MODEL)[:80],
        "_gateway_client_impl": "gateway_v0_answer_writer",
    }
    task_id = str(meta.get("_gateway_task_id") or "").strip()
    if task_id:
        result["_gateway_task_id"] = task_id[:80]
    return result


async def try_write(assignment: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    status = config_status()
    try:
        try:
            from scripts.nmbot_gateway_client import OvermindClient  # type: ignore
        except ImportError:  # pragma: no cover - direct scripts/ execution fallback
            from nmbot_gateway_client import OvermindClient  # type: ignore

        request_data = request_payload(assignment)
        headers = {"Authorization": f"Bearer {os.getenv('OVERMIND_TOKEN') or os.getenv('GATEWAY_POLL_TOKEN') or ''}"}
        client = OvermindClient()
        try:
            raw, meta = await client._run_gateway_request_once(request_data, headers, int(status["timeout"]))
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                await close()
        if not str(raw or "").strip():
            return "", {
                "ok": False,
                "provider": "gateway",
                "service": "openrouter",
                "error_code": "v0_answer_writer_empty_response",
                "model": status["model"],
                "_gateway_client_impl": "gateway_v0_answer_writer",
            }
        if isinstance(meta, Mapping) and (meta.get("_safe_fallback") or meta.get("_upstream_error")):
            return "", {
                "ok": False,
                "provider": "gateway",
                "service": "openrouter",
                "error_code": "v0_answer_writer_gateway_error",
                "model": status["model"],
                "_gateway_client_impl": "gateway_v0_answer_writer",
            }
        return str(raw), _safe_meta(meta if isinstance(meta, Mapping) else {}, model=str(status["model"]))
    except TimeoutError:
        return "", {"ok": False, "provider": "gateway", "service": "openrouter", "error_code": "v0_answer_writer_timeout", "model": status["model"], "_gateway_client_impl": "gateway_v0_answer_writer"}
    except Exception:
        return "", {"ok": False, "provider": "gateway", "service": "openrouter", "error_code": "v0_answer_writer_exception", "model": status["model"], "_gateway_client_impl": "gateway_v0_answer_writer"}
