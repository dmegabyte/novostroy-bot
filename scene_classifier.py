from __future__ import annotations

import json
import os
import time
import asyncio
from typing import Any

import aiohttp

from style_scenes import ALLOWED_SCENES, DEFAULT_SCENE, normalize_scene

SCENE_CLASSIFIER_PROMPT = """
Ты определяешь сценарий ответа Ирины.

Ирина помогает с подбором новостроек Москвы и Московской области.

Твоя задача — выбрать один scene из списка.
Не пиши ответ клиенту.
Не придумывай новые scene.

Верни строго JSON:
{
  "scene": "...",
  "confidence": 0.0,
  "reason": "коротко почему"
}

Допустимые scene:
- unsupported_region
- broad_request
- family_lifestyle_request
- investment_request
- move_in_soon_request
- budget_pressure
- no_exact_match
- specific_complex_question
- compare_complexes
- ready_to_handoff
- default_safe_reply

Правила:
- если клиент просит город не Москва и не Московская область → unsupported_region;
- если клиент явно просит оператора, бронь, просмотр, скидку или контакт → ready_to_handoff;
- если клиент спрашивает про конкретный ЖК или выбранный вариант → specific_complex_question;
- если клиент просит сравнить варианты → compare_complexes;
- если клиент ищет для семьи или жизни → family_lifestyle_request;
- если клиент ищет под аренду, инвестицию или перепродажу → investment_request;
- если клиенту важен быстрый въезд, сдача или ключи → move_in_soon_request;
- если запрос слишком общий → broad_request;
- если после поиска точного совпадения нет, но есть близкие варианты → no_exact_match;
- если после поиска видно, что запрос упирается в бюджет или жёсткие условия → budget_pressure;
- если не уверен → default_safe_reply.
""".strip()


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


def _trim(value: Any, limit: int = 6000) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        if first_nl > 0:
            raw = raw[first_nl + 1 :]
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        raw = raw[start:end]
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _fallback(reason: str) -> dict[str, Any]:
    return {"scene": DEFAULT_SCENE, "confidence": 0.0, "reason": reason, "fallback_used": True}


async def classify_scene(
    session: aiohttp.ClientSession,
    *,
    user_text: str,
    search_response: Any = "",
    memory: dict[str, Any] | None = None,
    draft_response: str = "",
    model: str | None = None,
    timeout: int = 45,
) -> dict[str, Any]:
    """Возвращает безопасный scene для style-router.

    Классификатор не влияет на факты и бизнес-логику. При любой ошибке
    возвращает default_safe_reply.
    """
    if _env("NMBOT_SCENE_CLASSIFIER", "1") == "0":
        return _fallback("scene classifier disabled")

    threshold = float(_env("NMBOT_SCENE_CONFIDENCE", "0.7"))
    payload = {
        "user_text": user_text,
        "search_response": _trim(search_response),
        "memory": memory or {},
        "draft_response": _trim(draft_response, limit=3000),
        "allowed_scenes": sorted(ALLOWED_SCENES),
    }
    request_data = {
        "query": json.dumps(payload, ensure_ascii=False, default=str),
        "service": "openrouter",
        "model": model or _env("NMBOT_SCENE_MODEL", "google/gemini-3.1-flash-lite-preview"),
        "system_prompt": SCENE_CLASSIFIER_PROMPT,
        "parameters": {"temperature": 0.0, "max_tokens": 500},
        "external_api_key": _required_env("OPENROUTER_API_KEY"),
    }

    try:
        token = _overmind_token()
        overmind_url = _env("OVERMIND_URL", "https://overmind.aiaxel.ru").rstrip("/")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        task_payload = {
            "agent_name": "gateway-agent",
            "endpoint": "/process",
            "request_data": request_data,
            "timeout_seconds": timeout,
            "max_retries": 0,
        }
        async with session.post(f"{overmind_url}/api/v1/tasks/api", json=task_payload, headers=headers) as resp:
            task = await resp.json()
        task_id = task.get("id")
        if not task_id:
            return _fallback("task_id missing")

        start = time.monotonic()
        poll_headers = {"Authorization": f"Bearer {token}"}
        while time.monotonic() - start < timeout:
            async with session.get(f"{overmind_url}/api/v1/tasks/api/{task_id}/status", headers=poll_headers) as resp:
                status_data = await resp.json()
            status = status_data.get("status")
            if status in {"completed", "failed", "cancelled"}:
                async with session.get(f"{overmind_url}/api/v1/tasks/api/{task_id}/result", headers=poll_headers) as resp:
                    result = await resp.json()
                result_obj = result.get("result") or result
                raw = result_obj.get("response", "") if isinstance(result_obj, dict) else str(result_obj)
                data = _extract_json(str(raw))
                scene = normalize_scene(str(data.get("scene") or ""))
                confidence = float(data.get("confidence") or 0.0)
                if scene == DEFAULT_SCENE or confidence < threshold:
                    return {
                        "scene": DEFAULT_SCENE,
                        "confidence": confidence,
                        "reason": str(data.get("reason") or "low confidence"),
                        "fallback_used": True,
                    }
                return {
                    "scene": scene,
                    "confidence": confidence,
                    "reason": str(data.get("reason") or ""),
                    "fallback_used": False,
                }
            await asyncio.sleep(1)
    except Exception as e:
        return _fallback(f"{type(e).__name__}: {e}")

    return _fallback("timeout")
