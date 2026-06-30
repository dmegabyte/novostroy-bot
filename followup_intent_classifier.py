from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import aiohttp

DEFAULT_INTENT = "clarify"
ALLOWED_INTENTS = {
    "choose_option",
    "compare_selected",
    "operator_for_selected",
    "explain_operator_reason",
    "continue_selection",
    "update_search_params",
    "new_search",
    "clarify",
    "reject_offer",
}

FOLLOWUP_INTENT_PROMPT = """
Ты определяешь, что клиент хочет сделать в продолжении диалога с Ириной.

Ирина помогает подбирать новостройки Москвы и Московской области.

Твоя задача — НЕ писать ответ клиенту, а выбрать действие для кода.
Смотри не только на последнюю фразу клиента, а на короткую историю диалога,
последний вопрос Ирины и состояние подбора.

Верни строго JSON:
{
  "intent": "...",
  "confidence": 0.0,
  "target": "коротко, если есть выбранный ЖК/вариант",
  "params_delta": {},
  "clarification_question": "короткий вопрос, если нужно уточнить",
  "reason": "коротко почему"
}

Допустимые intent:
- choose_option — клиент выбрал вариант из видимого списка;
- compare_selected — клиент согласился сравнить выбранный ЖК с похожими;
- operator_for_selected — клиент хочет оператора/актуальное наличие/бронь/этаж/показ/детали, которых нет в карточке;
- explain_operator_reason — клиент спрашивает зачем нужен оператор / почему нельзя ответить здесь;
- continue_selection — клиент хочет продолжить подбор здесь, посмотреть другие варианты или вернуться к списку;
- update_search_params — клиент уточнил или отверг параметр поиска;
- new_search — клиент начал новый поиск или сильно поменял запрос;
- reject_offer — клиент отказался от последнего предложенного действия;
- clarify — по ответу клиента нельзя надёжно понять, чего он хочет.

Правила:
- "да", "нет", "наверное", "возможно", "хочу" всегда понимай через last_bot_question и last_offer_type.
- Если последний вопрос был про сравнение, согласие означает compare_selected, отказ — reject_offer.
- Если последний вопрос был про оператора, согласие означает operator_for_selected, отказ — reject_offer.
- Если последний вопрос был про оператора, а клиент спрашивает "зачем", "почему", "для чего" — explain_operator_reason.
- Если клиент после предложения оператора пишет "продолжить", "подбор", "давай дальше", "еще варианты" — continue_selection.
- Если последний вопрос был уточнением "передать оператору или продолжить подбор", выбор продолжить подбор — continue_selection.
- Если бот спросил про параметр поиска, например "подойдёт последний этаж?", ответ "нет" должен стать update_search_params с params_delta.
- Если не уверен — intent=clarify и один короткий clarification_question.
- Не придумывай факты о ЖК, ценах, районах, этажах или наличии.
""".strip()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _required_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


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


def _fallback(reason: str, question: str | None = None) -> dict[str, Any]:
    return {
        "intent": DEFAULT_INTENT,
        "confidence": 0.0,
        "target": "",
        "params_delta": {},
        "clarification_question": question or "Уточните, пожалуйста: продолжить подбор или изменить условия?",
        "reason": reason,
        "fallback_used": True,
    }


def normalize_intent(intent: str) -> str:
    value = str(intent or "").strip()
    return value if value in ALLOWED_INTENTS else DEFAULT_INTENT


async def classify_followup_intent(
    session: aiohttp.ClientSession,
    *,
    user_text: str,
    dialog_window: list[dict[str, str]] | None = None,
    state: dict[str, Any] | None = None,
    model: str | None = None,
    timeout: int = 45,
) -> dict[str, Any]:
    """Возвращает безопасное действие для короткого follow-up.

    Классификатор не отвечает клиенту и не меняет state сам. Он только предлагает
    intent/params_delta, а код валидирует и применяет результат.
    """
    if _env("NMBOT_FOLLOWUP_CLASSIFIER", "1") == "0":
        return _fallback("followup classifier disabled")

    threshold = float(_env("NMBOT_FOLLOWUP_CONFIDENCE", "0.7"))
    payload = {
        "user_text": user_text,
        "dialog_window": dialog_window or [],
        "state": state or {},
        "allowed_intents": sorted(ALLOWED_INTENTS),
    }
    request_data = {
        "query": _trim(payload),
        "service": "openrouter",
        "model": model or _env("NMBOT_FOLLOWUP_MODEL", "google/gemini-3.1-flash-lite-preview"),
        "system_prompt": FOLLOWUP_INTENT_PROMPT,
        "parameters": {"temperature": 0.0, "max_tokens": 700},
        "external_api_key": _required_env("OPENROUTER_API_KEY"),
    }

    try:
        token = _required_env("OVERMIND_TOKEN")
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
                intent = normalize_intent(str(data.get("intent") or ""))
                confidence = float(data.get("confidence") or 0.0)
                question = str(data.get("clarification_question") or "").strip()
                if intent == DEFAULT_INTENT or confidence < threshold:
                    return _fallback(str(data.get("reason") or "low confidence"), question or None) | {"confidence": confidence}
                params_delta = data.get("params_delta") if isinstance(data.get("params_delta"), dict) else {}
                return {
                    "intent": intent,
                    "confidence": confidence,
                    "target": str(data.get("target") or ""),
                    "params_delta": params_delta,
                    "clarification_question": question,
                    "reason": str(data.get("reason") or ""),
                    "fallback_used": False,
                }
            await asyncio.sleep(1)
    except Exception as e:
        return _fallback(f"{type(e).__name__}: {e}")

    return _fallback("timeout")
