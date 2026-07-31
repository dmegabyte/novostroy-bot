from __future__ import annotations

import asyncio
import os
from typing import Any, Mapping


ENV_ENABLED = "NMBOT_V3_MANAGER_REWRITER_BLUESMINDS_ENABLED"
ENV_MODEL = "NMBOT_V3_MANAGER_REWRITER_BLUESMINDS_MODEL"
ENV_TIMEOUT = "NMBOT_V3_MANAGER_REWRITER_BLUESMINDS_TIMEOUT"

DEFAULT_MODEL = "gpt-5.2-chat"
DEFAULT_TIMEOUT = 60
TRUTHY_VALUES = {"1", "true", "yes", "on", "enabled"}


def is_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get(ENV_ENABLED, "")).strip().lower() in TRUTHY_VALUES


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
        "enabled": is_enabled(env),
        "model": str(env.get(ENV_MODEL) or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "timeout": _timeout_from_env(env),
    }


def _extract_content(response: Any) -> str:
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    return str(message.get("content") or "").strip()
                return str(first.get("text") or "").strip()
    return ""


async def try_rewrite(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    status = config_status()
    if not status["enabled"]:
        return "", {"ok": False, "error_code": "bluesminds_manager_rewriter_disabled", "_bluesminds_skipped": True}

    try:
        from bluesminds_client import BluesmindsClient  # type: ignore

        messages = [
            {"role": "system", "content": str(payload.get("system_prompt") or "")},
            {"role": "user", "content": str(payload.get("query") or "")},
        ]
        client = BluesmindsClient()
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.chat,
                model=status["model"],
                messages=messages,
                temperature=0.35,
                max_tokens=1800,
            ),
            timeout=status["timeout"],
        )
        content = _extract_content(response)
        if not content:
            return "", {
                "ok": False,
                "error_code": "bluesminds_manager_rewriter_empty_response",
                "_upstream_error": True,
                "_gateway_client_impl": "bluesminds_manager_rewriter",
                "_fallback_to_gateway": True,
                "model": status["model"],
            }
        return content, {
            "ok": True,
            "_gateway_client_impl": "bluesminds_manager_rewriter",
            "_primary_provider": True,
            "model": status["model"],
        }
    except Exception:
        return "", {
            "ok": False,
            "error_code": "bluesminds_manager_rewriter_exception",
            "_upstream_error": True,
            "_gateway_client_impl": "bluesminds_manager_rewriter",
            "_fallback_to_gateway": True,
            "model": status["model"],
        }
