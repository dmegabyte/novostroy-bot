from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Mapping


ENV_MODEL = "NMBOT_V0_ANSWER_WRITER_MODEL"
ENV_TIMEOUT = "NMBOT_V0_ANSWER_WRITER_TIMEOUT"
DEFAULT_MODEL = "gpt-5.2-chat"
DEFAULT_TIMEOUT = 60
TEMPERATURE = 0.4
MAX_TOKENS = 700
REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = REPO_ROOT / "prompts" / "v0_answer_writer.txt"


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
        "model": str(env.get(ENV_MODEL) or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "timeout": _timeout_from_env(env),
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
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


async def try_write(assignment: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    status = config_status()
    try:
        try:
            from scripts.bluesminds_client import BluesmindsClient  # type: ignore
        except ImportError:  # pragma: no cover - direct scripts/ execution fallback
            from bluesminds_client import BluesmindsClient  # type: ignore

        system_prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(assignment, ensure_ascii=False, sort_keys=True)},
        ]
        client = BluesmindsClient()
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.chat,
                model=status["model"],
                messages=messages,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
            ),
            timeout=status["timeout"],
        )
        content = _extract_content(response)
        if not content:
            return "", {
                "ok": False,
                "error_code": "v0_answer_writer_empty_response",
                "_upstream_error": True,
                "_gateway_client_impl": "bluesminds_v0_answer_writer",
                "model": status["model"],
            }
        return content, {
            "ok": True,
            "_gateway_client_impl": "bluesminds_v0_answer_writer",
            "_primary_provider": True,
            "model": status["model"],
        }
    except asyncio.TimeoutError:
        return "", {
            "ok": False,
            "error_code": "v0_answer_writer_timeout",
            "_upstream_error": True,
            "_gateway_client_impl": "bluesminds_v0_answer_writer",
            "model": status["model"],
        }
    except Exception:
        return "", {
            "ok": False,
            "error_code": "v0_answer_writer_exception",
            "_upstream_error": True,
            "_gateway_client_impl": "bluesminds_v0_answer_writer",
            "model": status["model"],
        }
