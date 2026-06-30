from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parent
PROMPT = (ROOT / "prompts" / "text_style_v1.txt").read_text(encoding="utf-8").strip()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _required_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def _overmind_token() -> str:
    token = _env("OVERMIND_TOKEN") or _env("GATEWAY_POLL_TOKEN")
    if not token:
        raise RuntimeError("OVERMIND_TOKEN/GATEWAY_POLL_TOKEN is not set")
    return token


def _strip_markdown(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl > 0:
            t = t[first_nl + 1 :]
        if t.endswith("```"):
            t = t[:-3].rstrip()
    return t


async def _create_task(session: aiohttp.ClientSession, request_data: dict[str, Any], timeout: int) -> dict[str, Any]:
    token = _overmind_token()
    overmind_url = _env("OVERMIND_URL", "https://overmind.aiaxel.ru").rstrip("/")
    payload = {
        "agent_name": "gateway-agent",
        "endpoint": "/process",
        "request_data": request_data,
        "timeout_seconds": timeout,
        "max_retries": 0,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    async with session.post(f"{overmind_url}/api/v1/tasks/api", json=payload, headers=headers) as resp:
        result = await resp.json()
        if resp.status not in (200, 201):
            raise RuntimeError(f"create task failed: http={resp.status} body={result}")
        return result


async def _poll_task(session: aiohttp.ClientSession, task_id: int, timeout: int) -> dict[str, Any]:
    token = _overmind_token()
    overmind_url = _env("OVERMIND_URL", "https://overmind.aiaxel.ru").rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        async with session.get(f"{overmind_url}/api/v1/tasks/api/{task_id}/status", headers=headers) as resp:
            status_data = await resp.json()
        status = status_data.get("status")
        if status in {"completed", "failed", "cancelled"}:
            async with session.get(f"{overmind_url}/api/v1/tasks/api/{task_id}/result", headers=headers) as resp:
                return await resp.json()
        await asyncio.sleep(2)
    raise TimeoutError(f"task {task_id} timeout after {timeout}s")


async def rewrite_text(
    session: aiohttp.ClientSession,
    *,
    text: str,
    context: str = "",
    intent: str = "",
    tone: str = "live",
    scene: str = "general",
    scene_rules: str = "",
    model: str | None = None,
    timeout: int = 90,
    max_tokens: int = 1200,
) -> tuple[str, dict[str, Any]]:
    """Переписывает текст без изменения смысла.

    Возвращает (styled_text, metadata). При ошибке кидает исключение.
    """

    if not text.strip():
        return text, {}

    request_data = {
        "query": json.dumps(
            {
                "text": text,
                "context": context,
                "intent": intent,
                "tone": tone,
                "scene": scene,
                "scene_rules": scene_rules,
            },
            ensure_ascii=False,
        ),
        "service": "openrouter",
        "model": model or _env("NMBOT_STYLE_MODEL", "google/gemini-3.1-flash-lite-preview"),
        "system_prompt": PROMPT,
        "parameters": {"temperature": float(_env("NMBOT_STYLE_TEMPERATURE", "0.2")), "max_tokens": max_tokens},
        "external_api_key": _required_env("OPENROUTER_API_KEY"),
    }

    task = await _create_task(session, request_data, timeout)
    task_id = task.get("id")
    if not task_id:
        raise RuntimeError(f"task_id missing: {task}")

    result = await _poll_task(session, int(task_id), timeout)
    result_obj = result.get("result") or result
    if not isinstance(result_obj, dict):
        return _strip_markdown(json.dumps(result, ensure_ascii=False)), result

    raw = _strip_markdown(str(result_obj.get("response", ""))).strip()
    return (raw or text), result_obj
