#!/usr/bin/env python3
"""
Telegram-бот для тестирования промпта Novostroy AI (nmbot).

Использует gateway-agent + OpenRouter + MCP novostroym.

Запуск:
    cd projects/nmbot
    source .venv/bin/activate
    export $(grep -v '^#' .env | xargs)
    python scripts/chat_tester_bot.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import aiohttp

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scene_classifier
import followup_intent_classifier
from style_scenes import get_scene_rules
import text_style_tool

# ── Конфигурация ─────────────────────────────────────────────

OVERMIND_URL = os.getenv("OVERMIND_URL", "https://overmind.aiaxel.ru")
OVERMIND_TOKEN = os.getenv("OVERMIND_TOKEN") or os.getenv("GATEWAY_POLL_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API_BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "").rstrip("/")

# H024: технические ошибки Overmind/OpenRouter пишем в bot.log, клиенту — только
# безопасную человеческую фразу без 'choices', traceback, JSON и названий провайдеров.
SAFE_UPSTREAM_ERROR_TEXT = (
    "Сейчас поиск не ответил как надо. Попробуйте ещё раз через минуту, "
    "а если повторится — напишите номер, и оператор проверит варианты вручную."
)

# Experiment Loop: активная гипотеза (см. docs/EXPERIMENTS.md)
ACTIVE_H_ID: Final[str] = os.getenv("NMBOT_H_ID", "H001")
LOGS_DIR: Final[Path] = REPO_ROOT / "logs"
PROMPTS_DIR: Final[Path] = REPO_ROOT / "prompts"


def _load_prompt(name: str) -> str:
    """Читает промпт из prompts/{name}.txt. Бросает FileNotFoundError с понятным путём."""
    path = PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").rstrip("\n")


SEARCH_MODEL = "google/gemini-3.1-flash-lite-preview"
CHAT_MODEL = "google/gemini-2.5-flash"

# H001/model-lab: search model stays fixed by default; /model switches only answer model.
ANSWER_MODELS: Final[list[str]] = [
    "google/gemini-2.5-flash",
    "google/gemini-3.5-flash",
    "google/gemini-3.1-flash-lite-preview",
    "openai/gpt-5.4-mini",
    "openai/gpt-5.5",
    "openai/gpt-4o",
]

SEARCH_SYSTEM_PROMPT = _load_prompt("search_v1")
CHAT_SYSTEM_PROMPT = _load_prompt("chat_v1")

AVAILABLE_MODELS = ANSWER_MODELS
SHOW_MODEL_STATS: Final[bool] = os.getenv("NMBOT_SHOW_MODEL_STATS", "1") != "0"
STAGE_PRESENTER_ENABLED: Final[bool] = os.getenv("NMBOT_STAGE_PRESENTER", "1") == "1"
SALES_PHRASE_ENABLED: Final[bool] = os.getenv("NMBOT_SALES_PHRASE", "1") == "1"
SALES_PHRASE_MODEL: Final[str] = os.getenv("NMBOT_SALES_PHRASE_MODEL", "google/gemini-3.5-flash")
try:
    SALES_PHRASE_TEMPERATURE: Final[float] = float(os.getenv("NMBOT_SALES_PHRASE_TEMPERATURE", "0.2"))
except ValueError:
    SALES_PHRASE_TEMPERATURE = 0.2
try:
    SALES_PHRASE_TIMEOUT: Final[int] = int(os.getenv("NMBOT_SALES_PHRASE_TIMEOUT", "90"))
except ValueError:
    SALES_PHRASE_TIMEOUT = 90
OPTION_ENRICHMENT_ENABLED: Final[bool] = os.getenv("NMBOT_OPTION_ENRICHMENT", "1") == "1"
try:
    OPTION_ENRICHMENT_TIMEOUT: Final[int] = int(os.getenv("NMBOT_OPTION_ENRICHMENT_TIMEOUT", "45"))
except ValueError:
    OPTION_ENRICHMENT_TIMEOUT = 45
try:
    OPTION_ENRICHMENT_SELECT_WAIT: Final[float] = float(os.getenv("NMBOT_OPTION_ENRICHMENT_SELECT_WAIT", "2.0"))
except ValueError:
    OPTION_ENRICHMENT_SELECT_WAIT = 2.0

STYLE_TOOL_ENABLED: Final[bool] = os.getenv("NMBOT_TEXT_STYLE_TOOL", "1") != "0"
STYLE_TOOL_MODEL: Final[str] = os.getenv("NMBOT_STYLE_MODEL", "google/gemini-3.1-flash-lite-preview")

# H001/reason-layer MVP: guarded first-list explanation layer.
# Superseded by the simpler stage presenter MVP; keep as opt-in lab layer only.
REASON_LAYER_ENABLED: Final[bool] = os.getenv("NMBOT_REASON_LAYER", "0") == "1"
REASON_LAYER_MODEL: Final[str] = os.getenv("NMBOT_REASON_MODEL", "google/gemini-3.5-flash")
REASON_LAYER_FALLBACK_MODEL: Final[str] = os.getenv("NMBOT_REASON_FALLBACK_MODEL", "google/gemini-3.1-flash-lite-preview")
try:
    REASON_LAYER_TEMPERATURE: Final[float] = float(os.getenv("NMBOT_REASON_TEMPERATURE", "0.25"))
except ValueError:
    REASON_LAYER_TEMPERATURE = 0.25
try:
    REASON_LAYER_TIMEOUT: Final[int] = int(os.getenv("NMBOT_REASON_TIMEOUT", "90"))
except ValueError:
    REASON_LAYER_TIMEOUT = 90

LOGGER: Final = logging.getLogger("chat_tester_bot")


def _safe_json_preview(obj: Any, limit: int = 2000) -> str:
    """H024: компактный raw diagnostic preview для bot.log без падения на несериализуемых объектах."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)[:limit]
    except Exception as e:  # pragma: no cover - defensive logging helper
        return f"<json-preview-failed {type(e).__name__}: {e}>"


def _safe_user_error_message(_error: str | None = None) -> str:
    """H024: человекочитаемая ошибка без OpenRouter/choices/traceback/JSON для Telegram."""
    return SAFE_UPSTREAM_ERROR_TEXT


def _is_safe_upstream_fallback(text: Any) -> bool:
    """True, если наружу уже надо отдать безопасный fallback, а не прогонять его через LLM-слои."""
    normalized = str(text or "").strip()
    return normalized == SAFE_UPSTREAM_ERROR_TEXT or normalized.lower() in {"", "none", "null"}


async def _maybe_style_text(
    client: "OvermindClient",
    text: str,
    *,
    intent: str,
    scene: str,
    context: str = "",
    scene_rules: str = "",
) -> str:
    if not STYLE_TOOL_ENABLED or not text.strip():
        return text
    try:
        session = await client.ensure_session()
        styled, _meta = await text_style_tool.rewrite_text(
            session,
            text=text,
            context=context,
            intent=intent,
            tone="live",
            scene=scene,
            scene_rules=scene_rules,
            model=STYLE_TOOL_MODEL,
        )
        styled = (styled or "").strip()
        return styled or text
    except Exception:
        LOGGER.exception("text style tool failed: intent=%s scene=%s", intent, scene)
        return text


class OvermindClient:
    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def ensure_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def ask(
        self,
        query: str,
        search_model: str = SEARCH_MODEL,
        chat_model: str = CHAT_MODEL,
        use_mcp: bool = True,
        timeout: int = 600,
        params: dict | None = None,
    ) -> tuple[str, dict, dict, dict]:
        """Возвращает (ответ модели, обновлённые параметры, search_metadata, chat_metadata)."""
        session = await self.ensure_session()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OVERMIND_TOKEN}",
        }

        # Добавляем параметры в запрос
        full_query = query
        if params:
            full_query = f"Текущие параметры: {json.dumps(params, ensure_ascii=False)}\n\nКлиент: {query}"

        search_request_data = {
            "query": full_query,
            "service": "openrouter",
            "model": search_model,
            "system_prompt": SEARCH_SYSTEM_PROMPT,
            "parameters": {
                "temperature": 0.3,
                "max_tokens": 5000,
            },
            "external_api_key": OPENROUTER_API_KEY,
        }
        if use_mcp:
            search_request_data["mcp_servers"] = ["novostroym"]

        search_result, search_meta = await self._run_gateway_request(search_request_data, headers, timeout)
        search_meta = {**search_meta, "_response_text": search_result}
        if search_meta.get("_safe_fallback") or _is_safe_upstream_fallback(search_result):
            return SAFE_UPSTREAM_ERROR_TEXT, {}, {**search_meta, "_safe_fallback": True}, {}
        if search_result.startswith("❌") or search_result.startswith("⏱️"):
            return search_result, {}, search_meta, {}

        new_params = self._extract_params(search_result)
        chat_query = (
            f"Запрос клиента: {query}\n\n"
            f"Текущие параметры: {json.dumps(params or {}, ensure_ascii=False)}\n"
            f"Обновления параметров: {json.dumps(new_params, ensure_ascii=False)}\n\n"
            f"Найденные факты, которыми можно пользоваться:\n{search_result}"
        )
        chat_request_data = {
            "query": chat_query,
            "service": "openrouter",
            "model": chat_model,
            "system_prompt": CHAT_SYSTEM_PROMPT,
            "parameters": {
                "temperature": 0.3,
                "max_tokens": 10000,  # H003: поднят с 5000 — flash обрезал JSON в 2/3 тестов
            },
            "external_api_key": OPENROUTER_API_KEY,
        }
        # H004: chat-стадия с retry — _chat_with_retry делает первый запрос сам,
        # при невалидном JSON повторяет до 2 раз. uid=0 — в OvermindClient.ask нет uid;
        # реальный uid проставляется в handle_message при финальной записи user_message.
        response_text, chat_params, retries, chat_meta = await self._chat_with_retry(
            chat_request_data, headers, timeout, uid=0
        )
        return response_text, {**new_params, **chat_params}, search_meta, chat_meta

    async def explain_known_option(
        self,
        option: dict[str, Any],
        client_request: str,
        chat_model: str = CHAT_MODEL,
        timeout: int = 600,
    ) -> tuple[str, dict]:
        """Chat-only presenter: новый живой ответ по уже загруженной карточке, без нового MCP-поиска."""
        session = await self.ensure_session()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OVERMIND_TOKEN}",
        }
        chat_query = _build_known_option_prompt(option, client_request)
        chat_request_data = {
            "query": chat_query,
            "service": "openrouter",
            "model": chat_model,
            "system_prompt": CHAT_SYSTEM_PROMPT,
            "parameters": {
                "temperature": 0.3,
                "max_tokens": 4000,
            },
            "external_api_key": OPENROUTER_API_KEY,
        }
        response_text, _params, _retries, chat_meta = await self._chat_with_retry(
            chat_request_data, headers, timeout, uid=0
        )
        return response_text, chat_meta

    async def explain_negation_followup(
        self,
        *,
        intent: str,
        user_text: str,
        state: dict[str, Any],
        meta: dict[str, Any] | None = None,
        chat_model: str = CHAT_MODEL,
        timeout: int = 600,
    ) -> tuple[str, dict]:
        """Chat-only presenter: живой ответ на отрицание/отказ без нового MCP-поиска."""
        session = await self.ensure_session()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OVERMIND_TOKEN}",
        }
        chat_query = _build_negation_response_prompt(intent=intent, user_text=user_text, state=state, meta=meta or {})
        chat_request_data = {
            "query": chat_query,
            "service": "openrouter",
            "model": chat_model,
            "system_prompt": CHAT_SYSTEM_PROMPT,
            "parameters": {
                "temperature": 0.2,
                "max_tokens": 1600,
            },
            "external_api_key": OPENROUTER_API_KEY,
        }
        response_text, _params, _retries, chat_meta = await self._chat_with_retry(
            chat_request_data, headers, timeout, uid=0
        )
        return response_text, chat_meta

    async def comparative_reason_angles(
        self,
        payload: dict[str, Any],
        *,
        model: str = REASON_LAYER_MODEL,
        timeout: int = REASON_LAYER_TIMEOUT,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """H001/reason-layer MVP: ask model for comparative angle+tone only.

        The model must not write final client text. Code renders the final phrase.
        """
        await self.ensure_session()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OVERMIND_TOKEN}",
        }
        system_prompt = (
            "Ты помогаешь выбрать, чем отличаются 2-3 ЖК в одном ответе Ирины. "
            "Используй только факты из INPUT: цена, метро, отделка, срок сдачи, площадь, локация. "
            "Не пиши финальный текст клиенту. Не добавляй доходность, аренду, ликвидность, рост цены, "
            "школы, парки, дворы, видовые квартиры, скидки, ипотеку или обещания выгоды. "
            "Не используй рекламные слова: лучший, идеальный, выгодный, перспективный, "
            "инвестиционно привлекательный, премиальный, статусный, максимально. "
            "Для каждого варианта верни короткие поля angle и tone: 3-8 слов, без повторов. "
            "Верни строго JSON без markdown: {\"items\":[{\"idx\":1,\"angle\":\"...\",\"tone\":\"...\"}]}"
        )
        request_data = {
            "query": json.dumps(payload, ensure_ascii=False),
            "service": "openrouter",
            "model": model,
            "system_prompt": system_prompt,
            "parameters": {
                "temperature": REASON_LAYER_TEMPERATURE,
                "max_tokens": 1200,
            },
            "external_api_key": OPENROUTER_API_KEY,
        }
        raw_text, meta = await self._run_gateway_request(request_data, headers, timeout)
        return _json_from_text(raw_text), {**meta, "_response_text": raw_text, "model": model}

    async def sales_phrases(
        self,
        payload: dict[str, Any],
        *,
        model: str = SALES_PHRASE_MODEL,
        timeout: int = SALES_PHRASE_TIMEOUT,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """MVP stage presenter: ask model only for short benefit phrases.

        Code keeps the answer structure, facts and final questions. The model only
        makes one human sales benefit per semantic card.
        """
        await self.ensure_session()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OVERMIND_TOKEN}",
        }
        system_prompt = (
            "Сформулируй короткую пользу для клиента по каждой карточке ЖК. "
            "Используй только INPUT: facts и allowed_angles. Не добавляй новые факты. "
            "Пиши живо, но спокойно: без рекламы, обещаний и будущей выгоды. "
            "Не упоминай то, чего нет во входе: школы, парки, двор, вид, этажи, наличие, "
            "ипотеку, скидки, аренду, доходность, рост цены. "
            "Не пиши приветствие, вопрос, markdown или эмодзи. "
            "Для каждого item верни одно предложение до 150 символов. "
            "Верни только JSON: {\"items\":[{\"idx\":1,\"benefit\":\"...\"}]}"
        )
        request_data = {
            "query": json.dumps(payload, ensure_ascii=False),
            "service": "openrouter",
            "model": model,
            "system_prompt": system_prompt,
            "parameters": {
                "temperature": SALES_PHRASE_TEMPERATURE,
                "max_tokens": 1200,
            },
            "external_api_key": OPENROUTER_API_KEY,
        }
        raw_text, meta = await self._run_gateway_request(request_data, headers, timeout)
        return _json_from_text(raw_text), {**meta, "_response_text": raw_text, "model": model}

    async def enrich_option_search(
        self,
        query: str,
        *,
        model: str = SEARCH_MODEL,
        timeout: int = OPTION_ENRICHMENT_TIMEOUT,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Targeted MCP/search enrichment for one selected ЖК.

        This is not a chat-answer call: it asks MCP/search for a richer factual JSON card.
        """
        await self.ensure_session()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OVERMIND_TOKEN}",
        }
        request_data = {
            "query": query,
            "service": "openrouter",
            "model": model,
            "system_prompt": SEARCH_SYSTEM_PROMPT,
            "parameters": {
                "temperature": 0.2,
                "max_tokens": 5000,
            },
            "external_api_key": OPENROUTER_API_KEY,
            "mcp_servers": ["novostroym"],
        }
        raw_text, meta = await self._run_gateway_request(request_data, headers, timeout)
        return _json_from_text(raw_text), {**meta, "_response_text": raw_text, "model": model}

    async def _run_gateway_request(self, request_data: dict, headers: dict, timeout: int) -> tuple[str, dict]:
        """Возвращает (response_text, metadata). При ошибке возвращает (error_text, {})."""
        session = await self.ensure_session()
        payload = {
            "agent_name": "gateway-agent",
            "endpoint": "/process",
            "request_data": request_data,
            "timeout_seconds": timeout,
            "max_retries": 0,
        }

        url = f"{OVERMIND_URL.rstrip('/')}/api/v1/tasks/api"
        async with session.post(url, json=payload, headers=headers) as resp:
            task = await resp.json()
            if resp.status not in (200, 201):
                LOGGER.error(
                    "gateway create task failed: status=%s payload=%s",
                    resp.status,
                    _safe_json_preview(task),
                )
                return _safe_user_error_message(), {"_upstream_error": True, "_safe_fallback": True}

        task_id = task.get("id")
        if not task_id:
            LOGGER.error("gateway create task returned no id: payload=%s", _safe_json_preview(task))
            return _safe_user_error_message(), {"_upstream_error": True, "_safe_fallback": True}

        base = OVERMIND_URL.rstrip("/")
        start = time.time()

        while time.time() - start < timeout:
            async with session.get(f"{base}/api/v1/tasks/api/{task_id}/status", headers=headers) as resp:
                status_data = await resp.json()

            status = status_data.get("status")
            if status in ("completed", "failed", "cancelled"):
                async with session.get(f"{base}/api/v1/tasks/api/{task_id}/result", headers=headers) as resp:
                    result = await resp.json()

                result_obj = result.get("result") or result
                if isinstance(result_obj, dict):
                    response_text = result_obj.get("response", "")
                    error = result_obj.get("error", "")
                    metadata = result_obj.get("metadata", {}) or {}
                    if error:
                        LOGGER.error(
                            "gateway task returned error: task_id=%s error=%s result=%s",
                            task_id,
                            error,
                            _safe_json_preview(result_obj),
                        )
                        return _safe_user_error_message(error), {**metadata, "_upstream_error": True, "_safe_fallback": True}

                    if response_text:
                        return response_text, metadata
                    LOGGER.error(
                        "gateway task returned empty response: task_id=%s status=%s result=%s",
                        task_id,
                        status,
                        _safe_json_preview(result_obj),
                    )
                    return _safe_user_error_message(), {**metadata, "_upstream_error": True, "_safe_fallback": True}

            await asyncio.sleep(3)

        return "⏱️ Таймаут — задача не завершилась за отведённое время", {}

    @staticmethod
    def _extract_params(response_text: str) -> dict:
        try:
            s = response_text.find("{")
            e = response_text.rfind("}") + 1
            if s >= 0 and e > s:
                data = json.loads(response_text[s:e])
                params = data.get("params", {})
                return params if isinstance(params, dict) else {}
        except json.JSONDecodeError:
            pass
        return {}

    @staticmethod
    def _parse_chat_json(response_text: str) -> tuple[str, dict, list[dict], list[dict]]:
        try:
            s = response_text.find("{")
            e = response_text.rfind("}") + 1
            if s >= 0 and e > s:
                data = json.loads(response_text[s:e])
                resp_text = data.get("response", response_text)
                params = data.get("params", {})
                visible_options = data.get("visible_options", [])
                return (
                    resp_text,
                    params if isinstance(params, dict) else {},
                    [],  # buttons намеренно игнорируем: Ирина отвечает живым текстом без inline-кнопок.
                    visible_options if isinstance(visible_options, list) else [],
                )
        except json.JSONDecodeError:
            pass
        return response_text, {}, [], []

    async def _chat_with_retry(
        self,
        chat_request_data: dict,
        headers: dict,
        timeout: int,
        uid: int,
    ) -> tuple[str, dict, int, dict]:
        """H004: chat-стадия с retry на невалидный JSON. До 2 повторов.
        Возвращает (response_text, chat_params, retries_count, chat_meta)."""
        chat_result, chat_meta = await self._run_gateway_request(chat_request_data, headers, timeout)
        if chat_meta.get("_safe_fallback") or _is_safe_upstream_fallback(chat_result):
            return SAFE_UPSTREAM_ERROR_TEXT, {}, 0, {**chat_meta, "_safe_fallback": True}
        # H007-A: strip markdown-обёртку ДО парсинга JSON, чтобы _parse_chat_json
        # работал с чистым текстом (без ```json ... ```).
        chat_result = _strip_markdown(chat_result)
        response_text, chat_params, chat_buttons, chat_visible_options = self._parse_chat_json(chat_result)
        chat_meta = {**chat_meta, "_buttons": chat_buttons, "_visible_options": chat_visible_options}
        retries = 0
        # Признак невалидного JSON: не нашли JSON response/buttons/params, и это не служебная ошибка.
        parsed_ok = response_text != chat_result or bool(chat_params) or bool(chat_buttons)
        is_invalid = not parsed_ok and not response_text.startswith("❌") and not response_text.startswith("⏱️")
        while is_invalid and retries < 2:
            retries += 1
            chat_result, chat_meta = await self._run_gateway_request(chat_request_data, headers, timeout)
            chat_result = _strip_markdown(chat_result)  # H007-A
            response_text_new, chat_params, chat_buttons, chat_visible_options = self._parse_chat_json(chat_result)
            chat_meta = {**chat_meta, "_buttons": chat_buttons, "_visible_options": chat_visible_options}
            parsed_ok = response_text_new != chat_result or bool(chat_params) or bool(chat_buttons)
            _log_event({
                "kind": "user_message_retry",
                "uid": uid,
                "stage": "chat",
                "attempt": retries,
                "recovered": parsed_ok,
                "raw_len": len(chat_result),
            })
            if parsed_ok:
                response_text = response_text_new
            is_invalid = not parsed_ok and not response_text.startswith("❌") and not response_text.startswith("⏱️")
        return response_text, chat_params, retries, chat_meta


def _strip_markdown(text: str) -> str:
    """H006: снимает markdown-обёртку ```json ... ``` (или ``` ... ```) вокруг JSON-блока.
    Если обёртки нет — возвращает текст как есть. Только для записи в лог."""
    t = text.strip()
    if t.startswith("```"):
        # Срезаем первую строку ```json или ```
        first_nl = t.find("\n")
        if first_nl > 0:
            t = t[first_nl + 1 :]
        # Срезаем замыкающий ```
        if t.endswith("```"):
            t = t[:-3].rstrip()
    return t


# H018: postprocessor для parse_mode=HTML. LLM пишет plain text (без HTML-тегов),
# код оборачивает: имена ЖК в «...» → <b>, цены с единицами (млн/тыс/руб) → <b>.
# HTML-escape делается ДО обёртки, чтобы не сломать «», «» и спецсимволы.
import re as _re_html  # локальный алиас чтобы не плодить импорт наверху

_HTML_ESCAPE_TABLE = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
}


def _to_html(text: str) -> str:
    """H018: оборачивает имена ЖК («...») и цены (млн/тыс/руб) в <b>...</b>.
    Безопасно для Telegram parse_mode=HTML. Не оборачивает уже-теги, не трогает эмодзи.
    """
    if not text:
        return text
    # 1) HTML-escape &, <, > (но не «, », пробелы, цифры)
    out = text
    for ch, esc in _HTML_ESCAPE_TABLE.items():
        out = out.replace(ch, esc)
    # 2) Оборачиваем имена ЖК в «...» → <b>«...»</b>
    #    «...» — кириллические кавычки. Не зацикливаем на уже-обёрнутом: после шага 1 < и > экранированы.
    out = _re_html.sub(r"«([^»\n]{2,80})»", r"<b>«\1»</b>", out)
    # 3) Оборачиваем цены: 1234.5 млн, 7.8 млн, 6.6-8.0 млн, 10 905 590 руб, от 4.9 млн
    #    Шаблон: число с пробелами/точками/запятыми + (млн|тыс|руб|рублей|млрд)
    out = _re_html.sub(
        r"(\b(?:от\s+|до\s+)?\d[\d\s.,]*?\s?(?:млн|тыс|руб|рублей|млрд)\b)",
        r"<b>\1</b>",
        out,
    )
    return out


def _format_numbered_list_spacing(text: str) -> str:
    """Добавляет воздух вокруг списка и финального вопроса для читаемости в Telegram."""
    if not text:
        return text
    lines = text.splitlines()
    out: list[str] = []
    seen_numbered_item = False
    for line in lines:
        is_item = bool(re.match(r"^\s*\d+\.\s+", line))
        is_question = bool(line.strip().endswith("?"))
        # Перед первым пунктом списка тоже нужен отступ, а не только между 1/2/3.
        if is_item and out and out[-1] != "":
            out.append("")
        # Финальный вопрос после списка читается лучше отдельным абзацем.
        if is_question and seen_numbered_item and out and out[-1] != "":
            out.append("")
        out.append(line)
        if is_item:
            seen_numbered_item = True
    return "\n".join(out)


def _format_paragraph_spacing(text: str) -> str:
    """Добавляет базовые абзацы в одиночных LLM-презентациях, где нет списка."""
    if not text:
        return text
    out = str(text or "").strip()
    out = re.sub(r"\s+(Хотите\b)", r"\n\n\1", out)
    out = re.sub(r"\s+(Оставите\b)", r"\n\n\1", out)
    out = re.sub(r"\s+(Что\s+(?:для вас|вам|из этого)\b)", r"\n\n\1", out, flags=re.I)
    out = re.sub(r"\s+(Какой\s+(?:вариант|ЖК)\b)", r"\n\n\1", out, flags=re.I)
    out = re.sub(r"\s+(Для инвестиц[а-яё]*\b)", r"\n\n\1", out, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", out)


def _prepare_response_text(text: str) -> str:
    """Финальный postprocess перед логом/Telegram.

    Защитный слой: если модель всё-таки вернула JSON строкой, пользователю уходит
    только поле response. params/buttons остаются внутренними и в Telegram не
    показываются.
    """
    raw = _strip_markdown(str(text or "")).strip()
    try:
        s = raw.find("{")
        e = raw.rfind("}") + 1
        if s >= 0 and e > s:
            data = json.loads(raw[s:e])
            if isinstance(data, dict) and isinstance(data.get("response"), str):
                raw = data["response"]
    except json.JSONDecodeError:
        pass
    raw = _format_paragraph_spacing(_fix_complex_name_artifacts(raw))
    return _format_numbered_list_spacing(raw)


def _fix_complex_name_artifacts(text: str) -> str:
    """Чистит типовые артефакты склейки названий ЖК/ГК в LLM-ответах."""
    cleaned = str(text or "")
    cleaned = re.sub(r"ЖК\s+«(ЖК|ГК)\s+«([^»]+)»»", r"\1 «\2»", cleaned)
    cleaned = re.sub(r"ЖК\s+«([^»]+)»»", r"ЖК «\1»", cleaned)
    return cleaned


def _display_complex_name(name: Any) -> str:
    value = _fix_complex_name_artifacts(str(name or "").strip())
    if not value:
        return "этому варианту"
    if re.match(r"^(жк|гк|мфк|премиум[-\s]?квартал)\b", value, re.I):
        return value
    return f"ЖК «{value}»"


_UNSUPPORTED_CLASS_RE = re.compile(
    r"\b(комфорт[-\s]?класс|бизнес[-\s]?класс|премиум[-\s]?класс|премиум|элитн\w*|массов\w+\s+сегмент\w*)\b",
    re.I,
)

_CLASS_AS_VALUE_RE = re.compile(r"^(comfort|business|premium|elite|комфорт|бизнес|премиум|элитн\w*)$", re.I)


def _strip_unsupported_complex_claims(text: str, option: dict[str, Any] | None = None) -> str:
    """Post-check для LLM: убираем класс/сегмент ЖК, если его нет в safe facts.

    Это не стилистическая заплатка под слово «комфорт-класс», а защитный слой
    grounding: класс/сегмент относится к чувствительным фактам и не должен
    появляться из общих знаний модели.
    """
    # Пока MCP-схема не даёт отдельного доверенного поля class/segment,
    # любые упоминания класса/сегмента в клиентском тексте считаем неподтверждёнными.
    paragraphs: list[str] = []
    for paragraph in re.split(r"\n\s*\n", str(text or "")):
        lines: list[str] = []
        for line in paragraph.splitlines() or [paragraph]:
            if not _UNSUPPORTED_CLASS_RE.search(line):
                lines.append(line)
                continue
            # Для пунктов списка не удаляем весь ЖК, а вырезаем только неподтверждённый класс/сегмент.
            if re.match(r"\s*\d+\.\s+", line):
                line = re.sub(r"\b(это\s+)?(комфорт[-\s]?класс|бизнес[-\s]?класс|премиум[-\s]?класс|премиум|элитн\w*|массов\w+\s+сегмент\w*)\b,?\s*", "", line, flags=re.I)
                line = re.sub(r"\s{2,}", " ", line).strip()
                if line:
                    lines.append(line)
                continue
            kept_sentences = [
                sentence
                for sentence in re.split(r"(?<!\d)(?<=[.!?])\s+(?=[А-ЯA-ZЁ])", line)
                if not _UNSUPPORTED_CLASS_RE.search(sentence)
            ]
            cleaned_line = " ".join(sentence.strip() for sentence in kept_sentences if sentence.strip())
            if cleaned_line:
                lines.append(cleaned_line)
        block = "\n".join(line for line in lines if line.strip()).strip()
        if block:
            paragraphs.append(block)
    return _format_numbered_list_spacing(re.sub(r"\n{3,}", "\n\n", "\n\n".join(paragraphs).strip()))


_UNREQUESTED_LIVE_DATA_RE = re.compile(
    r"(наличие\s+конкретн|актуальн\w*\s+цен|по\s+свежим\s+данн|конкретн\w*\s+планировк|проверить\s+по\s+актуальн)",
    re.I,
)

_DIRECT_LIVE_DATA_REQUEST_RE = re.compile(
    r"(налич|актуаль|брон|заброни|этаж|корпус|ипотек|ставк|скид|показ|посмотреть|торг|плат[её]ж|первонач)",
    re.I,
)

_GENERIC_SELECTED_QUESTION_RE = re.compile(
    r"(?:что\s+именно\s+в\s+этом\s+проекте\s+вам\s+(?:хотелось\s+бы\s+)?(?:обсудить|разобрать)\s+подробнее\?|какой\s+аспект\s+этого\s+жк\s+вам\s+(?:было\s+бы\s+)?интересно\s+разобрать\s+подробнее\?)",
    re.I,
)


def _strip_unrequested_live_data_cta(text: str, request_text: str = "") -> str:
    """CTA timing guard: не уводим в live-наличие, если клиент этого не просил."""
    request_l = str(request_text or "").lower().replace("ё", "е")
    # Не используем _needs_operator_for_selected_option: там есть широкий триггер
    # «квартир», который срабатывает на обычное «ищу квартиру».
    if _DIRECT_LIVE_DATA_REQUEST_RE.search(request_l):
        return text
    blocks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", str(text or "")):
        lines: list[str] = []
        for line in paragraph.splitlines() or [paragraph]:
            if _UNREQUESTED_LIVE_DATA_RE.search(line):
                kept = [
                    sentence.strip()
                    for sentence in re.split(r"(?<!\d)(?<=[.!?])\s+(?=[А-ЯA-ZЁ])", line)
                    if sentence.strip() and not _UNREQUESTED_LIVE_DATA_RE.search(sentence)
                ]
                if kept:
                    lines.append(" ".join(kept))
                continue
            lines.append(line)
        block = "\n".join(line for line in lines if line.strip()).strip()
        if block:
            blocks.append(block)
    return _format_numbered_list_spacing("\n\n".join(blocks).strip())


def _soften_layout_overclaim(text: str) -> str:
    """Не называем площади «планировками»: MCP обычно даёт area, а не планировочные решения."""
    cleaned = str(text or "")
    cleaned = re.sub(r"\bвыбор\s+планировок\s+большой\b", "диапазон площадей большой", cleaned, flags=re.I)
    cleaned = re.sub(r"\bможно\s+подобрать\s+подходящ(?:ую|ее)\s+планировк\w*\b", "можно подобрать подходящую площадь", cleaned, flags=re.I)
    cleaned = re.sub(r"\bразобрать\s+подробнее\s+планировк\w*\s+и\s+цен\w*\b", "коротко разобрать цену, срок и отделку", cleaned, flags=re.I)
    cleaned = re.sub(r"\bпланировк\w*\s+и\s+цен\w*\b", "цену, срок и отделку", cleaned, flags=re.I)
    return cleaned


def _soften_generic_selected_question(text: str) -> str:
    """Убирает роботический финальный вопрос у выбранного ЖК."""
    replacement = "Хотите сравнить его с другими вариантами или коротко разобрать цену, срок и отделку?"
    return _GENERIC_SELECTED_QUESTION_RE.sub(replacement, str(text or ""))


_SELECTED_INVESTMENT_COMPARE_QUESTION_RE = re.compile(
    r"Хотите\s+(?:сравнить[^?]+|(?:подробнее\s+)?разобрать\s+(?:цены|цену|стоимость)[^?]*|коротко\s+разобрать[^?]+)\?",
    re.I,
)


def _operator_cta_for_selected_investment(text: str, option: dict[str, Any] | None = None, purpose: Any = None) -> str:
    """После выбора инвестиционного ЖК интерес уже проявлен — следующий шаг оператор/live lots."""
    purpose_l = str(purpose or "").lower()
    if "инвест" not in purpose_l and purpose_l not in {"investment", "invest"}:
        return text
    if not text:
        return text
    name = _display_complex_name((option or {}).get("name") if isinstance(option, dict) else "")
    cta = (
        f"Хотите оставить номер — оператор проверит по {name} актуальные квартиры, цены входа и условия покупки?"
        if name else
        "Хотите оставить номер — оператор проверит актуальные квартиры, цены входа и условия покупки?"
    )
    if _SELECTED_INVESTMENT_COMPARE_QUESTION_RE.search(text):
        return _SELECTED_INVESTMENT_COMPARE_QUESTION_RE.sub(cta, text)
    if "оставить номер" in text.lower() or "оператор" in text.lower():
        return text
    return f"{text.rstrip()}\n\n{cta}"


def _compact_name_key(value: Any) -> str:
    return re.sub(r"[^a-zа-я0-9]+", " ", str(value or "").lower().replace("ё", "е")).strip()


def _rejected_option_keys(state: dict[str, Any]) -> set[str]:
    return {_compact_name_key(name) for name in (state.get("rejected_option_names") or []) if _compact_name_key(name)}


def _is_rejected_option(option: dict[str, Any], state: dict[str, Any]) -> bool:
    rejected = _rejected_option_keys(state)
    if not rejected:
        return False
    name_key = _compact_name_key(option.get("name"))
    return bool(name_key and name_key in rejected)


def _remember_rejected_selected_option(state: dict[str, Any]) -> None:
    option = state.get("selected_option")
    if not isinstance(option, dict) or not option.get("name"):
        return
    rejected = state.setdefault("rejected_option_names", [])
    if option["name"] not in rejected:
        rejected.append(option["name"])


def _filter_rejected_options(options: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    return [option for option in (options or []) if not _is_rejected_option(option, state)]


def _strip_rejected_options_from_response(text: str, state: dict[str, Any]) -> str:
    """Убирает из видимого списка ЖК, которые клиент уже отверг.

    MCP/search может снова вернуть тот же проект после уточнения бюджета. Диалоговая
    память клиента важнее: не показываем отвергнутый ЖК повторно в следующем списке.
    """
    rejected = _rejected_option_keys(state)
    if not rejected:
        return text
    blocks = re.split(r"\n\s*\n", str(text or ""))
    kept: list[str] = []
    for block in blocks:
        block_key = _compact_name_key(block)
        if any(name and name in block_key for name in rejected):
            continue
        kept.append(block)
    cleaned = "\n\n".join(kept).strip()
    counter = 0
    def repl(match: re.Match[str]) -> str:
        nonlocal counter
        counter += 1
        return f"{match.group(1)}{counter}. "
    return re.sub(r"(^|\n)\s*\d+\.\s+", repl, cleaned)


# H013: дефолтный state пользователя + динамические quick-actions
def _default_state() -> dict[str, Any]:
    return {
        "search_model": SEARCH_MODEL,
        "chat_model": CHAT_MODEL,
        "mcp": True,
        "params": {},
        "last_result": {},  # {found, exact_count, near_count, scenario}
        "last_options": [],  # H016: последние варианты для «второй»/«подешевле»
        "enriched_options": {},  # выбранные/top-3 ЖК, раскрытые точечным MCP/search
        "visible_options": [],  # порядок вариантов в последнем видимом клиенту списке
        "selected_option": None,  # PRODUCT_TZ: выбранный ЖК/вариант для follow-up и operator_context
        "turns_after_results": 0,
        "last_search_response": {},  # H026: полный структурированный MCP/search JSON для follow-up без нового MCP
        "asked_questions": [],  # список заданных уточнений (чтобы не повторять)
        "last_buttons": [],  # последние реально отправленные inline-кнопки для полного dialog log
        "dialog_window": [],  # последние реплики user/bot для понимания «да/нет/возможно» в контексте
        "last_bot_question": "",
        "last_offer_type": "",
        "last_answer_kind": "",
        "selected_option_card_shown_count": 0,
    }


def _reset_dialog_state_preserve_settings(state: dict[str, Any]) -> dict[str, Any]:
    """H023: /start начинает новый подбор, но сохраняет выбранные модель/MCP.

    До H023 /start делал setdefault и оставлял старые params. Из-за этого после
    нажатия budget:5m следующий новый запрос наследовал max_price=5_000_000.
    """
    fresh = _default_state()
    fresh["search_model"] = state.get("search_model", fresh["search_model"])
    fresh["chat_model"] = state.get("chat_model", fresh["chat_model"])
    fresh["mcp"] = state.get("mcp", fresh["mcp"])
    return fresh


def _append_dialog_turn(state: dict[str, Any], role: str, text: str, limit: int = 6) -> None:
    """Храним короткое окно диалога, чтобы «да/нет/возможно» понимались в контексте."""
    window = list(state.get("dialog_window") or [])
    window.append({"role": role, "text": str(text or "")[:1200]})
    state["dialog_window"] = window[-limit:]


def _extract_last_question(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if "?" in line:
            return line
    return ""


def _remember_bot_response(state: dict[str, Any], text: str, *, offer_type: str = "", answer_kind: str = "") -> None:
    _append_dialog_turn(state, "bot", text)
    question = _extract_last_question(text)
    if question:
        state["last_bot_question"] = question
    if offer_type:
        state["last_offer_type"] = offer_type
    if answer_kind:
        state["last_answer_kind"] = answer_kind


def _followup_state_payload(state: dict[str, Any]) -> dict[str, Any]:
    selected = state.get("selected_option") or {}
    return {
        "params": dict(state.get("params") or {}),
        "selected_option": selected.get("name") if isinstance(selected, dict) else None,
        "visible_options": [o.get("name") for o in (state.get("visible_options") or [])[:3]],
        "last_bot_question": state.get("last_bot_question") or "",
        "last_offer_type": state.get("last_offer_type") or "",
        "last_answer_kind": state.get("last_answer_kind") or "",
        "selected_option_card_shown_count": int(state.get("selected_option_card_shown_count") or 0),
    }


def _last_bot_text(state: dict[str, Any]) -> str:
    for turn in reversed(state.get("dialog_window") or []):
        if isinstance(turn, dict) and turn.get("role") == "bot":
            return str(turn.get("text") or "")
    return ""


def _dialog_planner_state_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Компактный state для LLM-orchestrator без сырых больших объектов."""
    selected = state.get("selected_option") if isinstance(state.get("selected_option"), dict) else {}
    return {
        "params": dict(state.get("params") or {}),
        "selected_option": _safe_option_payload(selected),
        "visible_options": [_safe_option_payload(o) for o in (state.get("visible_options") or [])[:5] if isinstance(o, dict)],
        "last_options": [_safe_option_payload(o) for o in (state.get("last_options") or [])[:5] if isinstance(o, dict)],
        "rejected_option_names": [str(x) for x in (state.get("rejected_option_names") or []) if str(x).strip()],
        "last_bot_question": state.get("last_bot_question") or "",
        "last_offer_type": state.get("last_offer_type") or "",
        "last_answer_kind": state.get("last_answer_kind") or "",
        "numeric_choice_policy": state.get("numeric_choice_policy") or "accept",
    }


def _find_option_by_name(state: dict[str, Any], name: Any) -> dict[str, Any] | None:
    compact_name = _compact_option_text(name)
    if not compact_name:
        return None
    for option in (state.get("visible_options") or []) + (state.get("last_options") or []):
        if _compact_option_text(option.get("name")) == compact_name:
            return option
    return None


def _apply_dialog_plan_to_state(state: dict[str, Any], plan: dict[str, Any], *, user_text: str = "") -> dict[str, Any]:
    """Применяет LLM dialog_plan как безопасный state-patch.

    LLM предлагает план; код не принимает новые ЖК на веру и применяет только
    разрешённые действия: clear/set выбранного объекта из памяти, rejected list,
    visible/numeric policies и params_delta.
    """
    applied: dict[str, Any] = {"applied": []}
    if not isinstance(plan, dict):
        return applied

    rejected = state.setdefault("rejected_option_names", [])
    known_names = {
        _compact_option_text(o.get("name")): str(o.get("name"))
        for o in (state.get("visible_options") or []) + (state.get("last_options") or [])
        if isinstance(o, dict) and o.get("name")
    }
    selected = state.get("selected_option") if isinstance(state.get("selected_option"), dict) else {}
    selected_name = str(selected.get("name") or "")
    selected_key = _compact_option_text(selected_name)
    # Семантику отказа/выбора определяет только LLM-orchestrator.
    # Код здесь не распознаёт смысл текста regex'ами: он лишь безопасно применяет
    # план к уже известным ЖК и не принимает новые названия на веру.
    visible_policy = str(plan.get("visible_options_policy") or "")
    params_delta = plan.get("params_delta") if isinstance(plan.get("params_delta"), dict) else {}
    params_delta = _normalize_followup_params_delta(params_delta)
    selected_action = str(plan.get("selected_option_action") or "")
    list_will_change = visible_policy == "clear" or bool(params_delta) or selected_action == "clear"
    allow_rejected_update = bool(plan.get("rejected_options_add")) and list_will_change
    for raw_name in plan.get("rejected_options_add") or []:
        if not allow_rejected_update:
            continue
        key = _compact_option_text(raw_name)
        safe_name = known_names.get(key) or (selected_name if key and key == selected_key else "")
        if safe_name and safe_name not in rejected:
            rejected.append(safe_name)
            applied["applied"].append("rejected_options_add")

    numeric_choice_is_blocked = state.get("numeric_choice_policy") == "reject" and _pure_option_choice_index(user_text) is not None

    if selected_action == "clear":
        state["selected_option"] = None
        state["last_offer_type"] = ""
        applied["applied"].append("selected_option_clear")
    elif selected_action == "set":
        option = _find_option_by_name(state, plan.get("selected_option_name"))
        if option and numeric_choice_is_blocked:
            applied["applied"].append("selected_option_set_blocked_by_numeric_policy")
        elif option:
            state["selected_option"] = option
            applied["applied"].append("selected_option_set")

    if visible_policy == "clear":
        state["visible_options"] = []
        applied["applied"].append("visible_options_clear")

    numeric_policy = str(plan.get("numeric_choice_policy") or "")
    if numeric_policy in {"accept", "reject"}:
        if numeric_policy == "accept" and numeric_choice_is_blocked:
            applied["applied"].append("numeric_choice_accept_blocked_by_numeric_policy")
        elif numeric_policy == "reject" and state.get("visible_options") and visible_policy != "clear" and not params_delta and not allow_rejected_update:
            # Planner может предложить reject для «покажи другие»/общих follow-up,
            # но если надёжный видимый список всё ещё на экране и мы реально не
            # меняли поиск/не чистили список/не фиксировали отказ, не ломаем выбор 1/2/3.
            applied["applied"].append("numeric_choice_reject_ignored_visible_list_still_valid")
        else:
            state["numeric_choice_policy"] = numeric_policy
            applied["applied"].append(f"numeric_choice_{numeric_policy}")

    if params_delta:
        state["params"] = {**state.get("params", {}), **params_delta}
        applied["params_delta"] = params_delta
        applied["applied"].append("params_delta")

    return applied


def _clarification_from_followup(meta: dict[str, Any], state: dict[str, Any]) -> str:
    question = str(meta.get("clarification_question") or "").strip()
    if question:
        return question
    offer = str(state.get("last_offer_type") or "")
    if offer == "compare_selected":
        return "Уточните, пожалуйста: сравнить этот ЖК с похожими или проверить актуальные квартиры у оператора?"
    if offer == "operator_for_selected":
        return "Уточните, пожалуйста: передать оператору или продолжить подбор здесь?"
    if offer == "choose_option":
        return "Какой вариант посмотрим подробнее — первый, второй или третий?"
    return "Уточните, пожалуйста, что сделать дальше: продолжить подбор или изменить условия?"


def _local_followup_intent(text: str, state: dict[str, Any]) -> str:
    """Semantic fallback disabled: смысл follow-up определяет LLM-orchestrator."""
    return ""


def _reject_operator_response(state: dict[str, Any]) -> str:
    selected = state.get("selected_option") or {}
    name = selected.get("name") if isinstance(selected, dict) else "этот ЖК"
    options = state.get("visible_options") or state.get("last_options") or []
    other_count = max(0, len(options) - 1) if selected else len(options)
    tail = "Могу сравнить его с другими вариантами или продолжить подбор здесь."
    if other_count:
        tail = "Можем сравнить его с другими вариантами из подборки или поменять условия поиска."
    return f"Хорошо, тогда остаёмся здесь. По {name} можем спокойно продолжить без звонка.\n\n{tail}\n\nЧто удобнее: сравнить варианты или изменить условия?"


def _reject_selected_option_response(state: dict[str, Any]) -> str:
    selected = state.get("selected_option") or {}
    selected_name = _compact_option_text(selected.get("name")) if isinstance(selected, dict) else ""
    options = state.get("visible_options") or state.get("last_options") or []
    remaining = [
        option for option in options
        if _compact_option_text(option.get("name")) != selected_name
    ][:3]
    if remaining:
        return _format_options_summary_response(
            remaining,
            "Поняла, этот ЖК убираем. Из оставшихся можно посмотреть",
            "Что именно не подошло в прошлом варианте — цена, район, срок или формат проекта?",
        )
    return "Поняла, этот ЖК убираем. Что именно не подошло — цена, район, срок сдачи или сам формат проекта?"


def _reject_similar_options_response(state: dict[str, Any]) -> str:
    return "Хорошо, похожие варианты не показываю. Можем либо подробнее разобрать выбранный ЖК, либо поменять условия поиска — бюджет, район, срок или отделку. Что важнее изменить?"


def _negation_clarification_response(meta: dict[str, Any], state: dict[str, Any]) -> str:
    question = str(meta.get("clarification_question") or "").strip()
    if question:
        return question
    selected = state.get("selected_option") or {}
    if isinstance(selected, dict) and selected.get("name"):
        return "Поняла. Что именно не подошло в этом варианте — цена, район, срок сдачи, отделка или сам формат ЖК?"
    return "Поняла. Что меняем в подборе — район, бюджет, отделку, срок сдачи или формат квартиры?"


def _is_short_yes_to_contact_offer(text: str, state: dict[str, Any]) -> bool:
    """Понимает «да/хочу» именно как согласие оставить контакт, а не как новый запрос про ЖК."""
    t = text.lower().replace("ё", "е").strip()
    if not re.fullmatch(r"(да|ага|угу|ок|хорошо|давай|хочу|можно|готов|готова)", t):
        return False
    offer = str(state.get("last_offer_type") or "")
    if offer not in {"selected_option_details", "operator_for_selected"}:
        return False
    question = str(state.get("last_bot_question") or "").lower().replace("ё", "е")
    return bool(re.search(r"(остав|напиш|дать|передать).{0,30}(номер|телефон|контакт)|номер.{0,30}(связ|остав)|контакт", question))


def _operator_contact_request_text() -> str:
    return "Отлично, напишите номер для связи текстом — передам оператору этот ЖК и ваш запрос вместе с контекстом диалога."


def _safe_option_payload(option: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(option, dict):
        return {}
    allowed = {"idx", "name", "location", "price", "area", "finishing", "ready", "developer", "metro", "why_close"}
    safe: dict[str, Any] = {}
    for key, value in option.items():
        if key not in allowed or _looks_missing(value):
            continue
        # MCP иногда отдаёт класс проекта в поле developer (`comfort`/`business`).
        # Не передаём это LLM как застройщика: класс/сегмент — чувствительный факт.
        if key == "developer" and _CLASS_AS_VALUE_RE.search(str(value).strip()):
            continue
        safe[key] = value
    return safe


def _build_negation_response_prompt(
    *,
    intent: str,
    user_text: str,
    state: dict[str, Any],
    meta: dict[str, Any] | None = None,
) -> str:
    """Структурированный контракт для LLM-ответа на отрицание: код выбирает intent, LLM пишет текст."""
    selected = state.get("selected_option") if isinstance(state.get("selected_option"), dict) else {}
    selected_name = _compact_option_text(selected.get("name")) if isinstance(selected, dict) else ""
    options = state.get("visible_options") or state.get("last_options") or []
    rejected_names = [str(name) for name in (state.get("rejected_option_names") or [])]
    last_options = [
        _safe_option_payload(option)
        for option in options[:5]
        if isinstance(option, dict) and _compact_option_text(option.get("name")) != selected_name
    ]
    intent_rules = {
        "reject_operator": "Клиент отказался от оператора/звонка. Не проси номер и не объясняй оператора. Не продавай выбранный ЖК заново. Коротко прими отказ и скажи, что продолжим здесь. Финальный вопрос: сравнить варианты или изменить условия.",
        "reject_phone": "Клиент отказался оставить номер/контакт. Не проси номер повторно. Скажи, что продолжим здесь. Финальный вопрос: что сделать дальше в подборе.",
        "reject_selected_option": "Клиент отверг выбранный ЖК. Не продавай его снова. Скажи, что убираем этот ЖК из фокуса. Если в USER_TEXT есть новое условие, отрази его и спроси один уточняющий вопрос перед новым поиском. Можно коротко предложить 1-2 оставшихся варианта из LAST_OPTIONS, кроме SELECTED_OPTION, только если это уместно.",
        "reject_similar_options": "Клиент не хочет похожие/другие варианты. Не показывай похожие варианты. Спроси, что лучше сделать вместо этого: изменить условия или подробнее разобрать текущий ЖК.",
        "clarify_negation": "Клиент что-то отрицает, но смысл неясен. Не предлагай другие ЖК сам. Не делай вид, что он отверг выбранный ЖК. Задай один короткий вопрос, что именно убрать или не учитывать. Если в тексте есть бронь, прими, что бронь не нужна, и предложи продолжить без неё.",
    }
    payload = {
        "user_text": user_text,
        "negation_intent": intent,
        "intent_specific_rule": intent_rules.get(intent, intent_rules["clarify_negation"]),
        "selected_option": _safe_option_payload(selected),
        "last_options_except_selected": [option for option in last_options if option],
        "rejected_option_names": rejected_names,
        "classifier_meta": meta or {},
        "last_bot_question": state.get("last_bot_question"),
        "last_offer_type": state.get("last_offer_type"),
    }
    return (
        "ROLE:\n"
        "Ты Ирина, живой консультант по новостройкам. Отвечай тепло, коротко и по-человечески.\n\n"
        "SITUATION:\n"
        "Клиент уже видел подборку или выбранный ЖК. Сейчас он написал отрицание, отказ или изменение условия. "
        "Новый MCP-поиск не делай и не говори, что уже ищешь.\n\n"
        "NEGATION_CONTEXT:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "GLOBAL_RULES:\n"
        "- Код уже выбрал NEGATION_INTENT; не меняй действие сам.\n"
        "- Не повторяй презентацию выбранного ЖК, если клиент его отверг.\n"
        "- Если клиент отказался от оператора или номера — не проси номер и не предлагай звонок в этом ответе.\n"
        "- Если клиент отказался от похожих вариантов — не перечисляй похожие варианты.\n"
        "- Если смысл отрицания неясен — задай один короткий уточняющий вопрос.\n"
        "- Не добавляй факты о ЖК, которых нет в SELECTED_OPTION или LAST_OPTIONS.\n"
        "- Запрещённые клиентские фразы: MCP, JSON, база, подтверждённые данные, не удалось подтвердить, чтобы не выдумывать, в режиме реального времени.\n"
        "- Не обещай звонок, бронь, скидку, наличие, этажи, корпуса или ипотеку.\n"
        "- Ответ 1-3 коротких абзаца, в конце ровно один вопрос.\n\n"
        "OUTPUT_JSON:\n"
        "Верни валидный JSON только с полями response, params, buttons. buttons всегда []."
    )


def _pure_option_choice_index(text: str) -> int | None:
    """Возвращает номер варианта только для чистого выбора без другого смысла.

    Важно: `15 млн`, `1 но дорого`, `2 если с отделкой` — не выбор варианта.
    Такие фразы должен понимать LLM follow-up router по контексту вопроса.
    """
    t = text.lower().replace("ё", "е").strip()
    t = re.sub(r"\s+", " ", t)
    mapping = {
        1: (r"1\.?", r"перв(ый|ого)?( вариант)?"),
        2: (r"2\.?", r"втор(ой|ого)?( вариант)?"),
        3: (r"3\.?", r"трет(ий|ьего)?( вариант)?"),
    }
    for idx, patterns in mapping.items():
        if any(re.fullmatch(pattern, t) for pattern in patterns):
            return idx
    return None


def _match_option_from_text(text: str, options: list[dict[str, Any]]) -> dict[str, Any] | None:
    compact_t = _compact_option_text(text)
    for option in options:
        name = _compact_option_text(option.get("name"))
        if name and name in compact_t:
            return option
        name_words = [w for w in name.split() if len(w) >= 4]
        name_words = [w for w in name_words if w not in ("жилой", "квартал", "комплекс")]
        if name_words and all(w in compact_t for w in name_words[:2]):
            return option
    return None


def _is_selected_option_explain_request(text_l: str) -> bool:
    """Запрос описания выбранного ЖК из уже сохранённых MCP-данных."""
    return bool(re.search(r"подробнее|расскажи|детал|подробн|что по|чем хорош|почему подходит", text_l))


def _split_choice_and_action(text: str, options: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """Выделяет объект и остаток фразы из смешанных сообщений.

    Примеры:
    - «1» → (option_1, "")
    - «1, можно бронь?» → (option_1, "можно бронь?")
    - «ЖК Лучи, расскажи подробнее» → (option_luchi, "расскажи подробнее")

    Остаток не интерпретируем здесь целиком: опасные темы идут в operator,
    понятное «расскажи» — в explain, остальное остаётся followup_classifier.
    """
    stripped = text.strip()
    if not stripped or not options:
        return None, stripped

    m = re.match(r"^\s*(\d{1,2})\s*[,.:;\-–—]?\s*(.*)$", stripped)
    if m:
        idx = int(m.group(1))
        if 1 <= idx <= len(options):
            return options[idx - 1], m.group(2).strip()

    compact_text = _compact_option_text(stripped)
    for option in options:
        name = str(option.get("name") or "")
        compact_name = _compact_option_text(name)
        short_name = _compact_option_text(
            name.replace("ЖК", "").replace("жилой квартал", "").replace("жилой комплекс", "")
        )
        if not compact_name:
            continue
        if compact_text == compact_name or (short_name and compact_text == short_name):
            return option, ""
        if compact_name in compact_text or (short_name and short_name in compact_text):
            remaining = re.sub(re.escape(name), "", stripped, flags=re.I).strip(" ,.;:-—–")
            return option, remaining or stripped
    return None, stripped


def _normalize_followup_params_delta(delta: dict[str, Any]) -> dict[str, Any]:
    """Приводит LLM params_delta к ключам, которые уже понимает поиск.

    LLM может вернуть `budget: 15000000`, а текущий state/search ожидает
    `max_price`. Нормализуем здесь, не заставляя classifier угадывать внутренний
    нейминг идеально.
    """
    out = dict(delta or {})
    budget = out.pop("budget", None) or out.pop("max_budget", None)
    if budget is not None and not out.get("max_price"):
        try:
            if isinstance(budget, str):
                parsed = _price_min(budget)
                out["max_price"] = parsed if parsed else budget
            else:
                out["max_price"] = int(budget)
        except Exception:
            out["max_price"] = budget
    return out


def _operator_reason_response(state: dict[str, Any]) -> str:
    selected = state.get("selected_option") or {}
    name = selected.get("name") if isinstance(selected, dict) else "этот ЖК"
    return (
        f"Оператора предлагаю не вместо ответа, а чтобы проверить по {name} то, чего нет в подтверждённых фактах: "
        "актуальные квартиры, корпуса, этажи, бронь, скидки и условия покупки.\n\n"
        "Здесь я могу продолжить подбор и сравнить варианты, но не буду выдумывать наличие или условия.\n\n"
        "Продолжим подбор здесь или передать этот ЖК оператору?"
    )


def _continue_selection_response(state: dict[str, Any]) -> str:
    selected = state.get("selected_option") or {}
    selected_name = _compact_option_text(selected.get("name")) if isinstance(selected, dict) else ""
    options = state.get("visible_options") or state.get("last_options") or []
    remaining = [
        option for option in options
        if _compact_option_text(option.get("name")) != selected_name
    ][:3]
    if remaining:
        return _format_options_summary_response(
            remaining,
            "Хорошо, продолжим подбор. Из похожих вариантов ещё можно посмотреть",
            "Какой из них разобрать дальше?",
        )
    return "Хорошо, продолжим подбор. Что поменять в условиях: бюджет, район, срок сдачи или количество комнат?"


def _followup_expansion_option_names(state: dict[str, Any]) -> list[str]:
    options = state.get("visible_options") or state.get("last_options") or []
    names: list[str] = []
    for option in options[:3]:
        name = _compact_option_text(option.get("name"))
        if name and name not in names:
            names.append(name)
    selected = state.get("selected_option")
    if isinstance(selected, dict):
        selected_name = _compact_option_text(selected.get("name"))
        if selected_name and selected_name not in names:
            names.append(selected_name)
    return names


def _build_followup_expansion_query(text: str, state: dict[str, Any]) -> tuple[str, list[str]]:
    excluded_names = _followup_expansion_option_names(state)
    parts = [
        str(text or "").strip(),
        "",
        "Клиент просит ещё похожие варианты.",
        "Сделай свежую подборку по тем же или максимально близким условиям, но не повторяй уже показанные ЖК.",
    ]
    if excluded_names:
        parts.extend([
            "",
            "Внутреннее ограничение диалога: не показывай снова эти ЖК: " + ", ".join(excluded_names) + ".",
        ])
    return "\n".join(parts).strip(), excluded_names


def _parse_budget_callback_value(value: str) -> int | None:
    """H023: parse budget callback values like '5m', '10m', '15m', or 'none'."""
    if value == "none":
        return None
    if value.endswith("m") and value[:-1].isdigit():
        return int(value[:-1]) * 1_000_000
    return None


def _json_from_text(text: str) -> dict:
    """Достаёт JSON-объект из ответа модели, даже если вокруг есть текст/```json."""
    try:
        clean = _strip_markdown(text)
        s = clean.find("{")
        e = clean.rfind("}") + 1
        if s >= 0 and e > s:
            data = json.loads(clean[s:e])
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _price_min(value: Any) -> int | None:
    """Грубый min-price parser для строк вроде 'от 3 009 000 руб.' или '7.35-20.56 млн'."""
    if value is None:
        return None
    text = str(value).lower().replace(",", ".")
    nums = re.findall(r"\d+(?:\.\d+)?", text.replace(" ", ""))
    if not nums:
        return None
    try:
        n = float(nums[0])
    except ValueError:
        return None
    if "млн" in text or n < 1000:
        return int(n * 1_000_000)
    return int(n)


def _budget_limit_from_text(text: str) -> int | None:
    """Понимает уточнения бюджета вроде «до 15 млн» после уже показанного списка."""
    t = str(text or "").lower().replace(",", ".").replace("ё", "е")
    if not re.search(r"\b(до|бюджет|лимит|млн|миллион|тыс|к)\b", t):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(млн|миллион|миллиона|миллионов|тыс|тысяч|к)?", t)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2) or ""
    if unit in {"тыс", "тысяч", "к"}:
        return int(value * 1_000)
    # В клиентских бюджетах голое «15» почти всегда означает миллионы.
    if unit or value < 1000:
        return int(value * 1_000_000)
    return int(value)


def _compact_option_text(value: Any) -> str:
    """Нормализует название ЖК/строку выбора для безопасного сопоставления."""
    return re.sub(
        r"[^а-яa-z0-9]+",
        " ",
        str(value or "").lower().replace("ё", "е"),
    ).strip()


def _extract_options(search_text: str) -> list[dict[str, Any]]:
    """H016: превращает facts+near в индексированный список вариантов для follow-up."""
    data = _json_from_text(search_text)
    raw: list[Any] = []
    for key in ("facts", "near"):
        items = data.get(key, []) if isinstance(data, dict) else []
        if isinstance(items, list):
            raw.extend(items)

    options: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        price_range = item.get("price_range") or item.get("prices") or ""
        price = price_range or item.get("price") or item.get("cost") or item.get("min_price")
        opt = {
            "idx": len(options) + 1,
            "name": item.get("name") or item.get("title") or "вариант",
            "location": item.get("location") or item.get("district") or "",
            "price": price or "",
            "price_range": price_range or "",
            "price_min": _price_min(price),
            "finishing": item.get("finishing") or item.get("renovation") or "",
            "area": item.get("area") or item.get("square") or item.get("площадь") or "",
            "ready": item.get("ready") or item.get("status") or item.get("deadline") or "",
            "developer": item.get("developer") or item.get("dev") or item.get("застройщик") or "",
            "metro": item.get("metro") or "",
            "transport": item.get("transport") or item.get("walk_minutes") or item.get("транспорт") or "",
            "rooms": item.get("rooms") or item.get("room_types") or item.get("комнатность") or "",
            "why_close": item.get("why_close") or "",
            "infrastructure": _join_fact_values(item.get("infrastructure"), item.get("infrastructure_family"), item.get("family_infrastructure")),
            "schools": _join_fact_values(item.get("schools"), item.get("school"), item.get("школы"), item.get("школа")),
            "kindergartens": _join_fact_values(item.get("kindergartens"), item.get("kindergarten"), item.get("детские_сады"), item.get("детский_сад")),
            "parks": _join_fact_values(item.get("parks"), item.get("park"), item.get("green_area"), item.get("forest"), item.get("embankment"), item.get("парки"), item.get("парк"), item.get("лес"), item.get("набережная")),
            "clinics": _join_fact_values(item.get("clinics"), item.get("clinic"), item.get("polyclinic"), item.get("pharmacies"), item.get("поликлиника"), item.get("аптеки")),
            "yards": _join_fact_values(item.get("yards"), item.get("yard_without_cars"), item.get("playgrounds"), item.get("двор"), item.get("площадки")),
            "shops": _join_fact_values(item.get("shops"), item.get("services"), item.get("retail"), item.get("магазины"), item.get("сервисы")),
            "raw": item,
        }
        options.append(opt)
    return options[:8]


def _visible_options_from_response(response_text: str, options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Собирает порядок вариантов именно из видимого пользователю нумерованного списка.

    После отказа от inline-кнопок клиент выбирает текстом: «1», «второй» или
    присылает строку «1. ЖК ...». Поэтому индекс должен соответствовать не
    сырому порядку MCP, а тому списку, который реально увидел клиент.
    """
    if not response_text or not options:
        return []

    by_name: list[tuple[str, dict[str, Any]]] = []
    for option in options:
        compact_name = _compact_option_text(option.get("name"))
        if compact_name:
            by_name.append((compact_name, option))

    visible: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    numbered_lines = re.findall(r"(?m)^\s*(\d{1,2})\.\s*(.+)$", response_text)
    for _idx_raw, line in numbered_lines:
        compact_line = _compact_option_text(line)
        matched: dict[str, Any] | None = None
        # Сначала точное/почти точное попадание названия в строку.
        for compact_name, option in by_name:
            if compact_name and compact_name in compact_line:
                matched = option
                break
        if matched is None:
            # Потом пробуем по словам названия: «Южные Сады» может быть без «ЖК».
            for compact_name, option in by_name:
                words = [w for w in compact_name.split() if len(w) >= 4 and w not in ("жилой", "квартал", "комплекс")]
                if words and all(w in compact_line for w in words[:2]):
                    matched = option
                    break
        if matched is None:
            continue
        name_key = _compact_option_text(matched.get("name"))
        if name_key in seen_names:
            continue
        visible.append({**matched, "visible_idx": len(visible) + 1})
        seen_names.add(name_key)
    return visible[:3]


def _visible_options_from_chat_meta(chat_meta: dict[str, Any], options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Берёт порядок вариантов из структурного поля chat JSON: visible_options[].

    Это основной путь: LLM явно сообщает, какие варианты она показала клиенту.
    Код только сопоставляет эти имена с уже известными MCP options и не принимает
    придуманные ЖК. Старый парсинг текста остаётся fallback'ом.
    """
    raw_visible = (chat_meta or {}).get("_visible_options") or []
    if not isinstance(raw_visible, list) or not options:
        return []

    visible: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_visible[:3]:
        if isinstance(item, dict):
            raw_name = item.get("name") or item.get("option_name") or item.get("title") or item.get("complex")
            raw_idx = item.get("idx")
            if raw_idx is None:
                raw_idx = item.get("source_idx")
            if raw_idx is None:
                raw_idx = item.get("option_idx")
        else:
            raw_name = str(item or "")
            raw_idx = None

        matched: dict[str, Any] | None = None
        # Имя — главный ключ. Индекс у LLM может быть 0-based или 1-based,
        # поэтому используем его только как fallback. Так не перепутаем похожие
        # ЖК вроде «Ситимикс Новокосино» и «Ситимикс».
        if raw_name:
            matched = _match_option_from_text(str(raw_name), options)
        try:
            idx = int(raw_idx) if raw_idx is not None and str(raw_idx).strip() else 0
        except (TypeError, ValueError):
            idx = 0
        if matched is None and idx:
            for option in options:
                try:
                    opt_idx = int(option.get("idx") or 0)
                except (TypeError, ValueError):
                    opt_idx = 0
                if opt_idx and idx == opt_idx:
                    matched = option
                    break
        if matched is None and idx >= 0:
            # Fallback for models that send 0-based indexes.
            pos = idx if idx == 0 else idx - 1
            if 0 <= pos < len(options):
                matched = options[pos]
        if matched is None:
            continue

        key = _compact_option_text(matched.get("name"))
        if not key or key in seen:
            continue
        visible.append({**matched, "visible_idx": len(visible) + 1})
        seen.add(key)
    return visible


def _visible_options_from_chat_or_response(chat_meta: dict[str, Any], response_text: str, options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    structured = _visible_options_from_chat_meta(chat_meta, options)
    return structured or _visible_options_from_response(response_text, options)


def _numbered_option_count(response_text: str) -> int:
    return len(re.findall(r"(?m)^\s*\d{1,2}\.\s+", str(response_text or "")))


def _numeric_choice_policy_from_response(response_text: str, visible_options: list[dict[str, Any]]) -> str:
    """Цифровой выбор безопасен только если видимый нумерованный список надёжно распарсен.

    Если LLM показала 3 пункта, а мы смогли сопоставить только 2 — лучше переспросить,
    чем выбрать неправильный ЖК. Это safety-veto над LLM/парсером.
    """
    numbered = _numbered_option_count(response_text)
    if numbered >= 2:
        return "accept" if len(visible_options or []) == numbered else "reject"
    return "accept" if numbered == 1 and len(visible_options or []) == 1 else "reject"


def _resolve_dialog_intent(text: str, state: dict) -> dict[str, Any]:
    """Решает только вход в LLM-orchestrator или новый поиск.

    По правилу проекта код не распознаёт семантику regex'ами. Телефон ловится
    выше отдельным phone guard; всё остальное после памяти диалога отдаётся LLM.
    """
    memory_options = state.get("last_options") or []
    visible_options = state.get("visible_options") or []
    options = visible_options or memory_options
    selected = state.get("selected_option")
    has_dialog_memory = bool(
        options
        or selected
        or state.get("last_bot_question")
        or state.get("last_answer_kind")
        or state.get("last_search_response")
    )
    if has_dialog_memory:
        return {"intent": "followup_classifier"}
    return {"intent": "new_search"}


def _needs_operator_for_selected_option(text_l: str) -> bool:
    """После выбора ЖК живые данные не придумываем: наличие/бронь/показ/ипотека — к оператору."""
    triggers = (
        "налич", "актуаль", "брон", "заброни", "показ", "посмотреть",
        "ипотек", "ставк", "скид", "торг", "этаж", "корпус", "квартир",
        "планиров", "платеж", "платёж", "первонач", "звон", "оператор", "менеджер",
    )
    return any(trig in text_l for trig in triggers)


def _shows_handoff_readiness_for_selected(text_l: str, state: dict[str, Any]) -> bool:
    """Клиент уже выбрал ЖК и показывает интерес — пора вести к оператору, а не уточнять бесконечно."""
    if not state.get("selected_option"):
        return False
    has_seen_selected_card = bool(
        int(state.get("selected_option_card_shown_count") or 0) > 0
        or str(state.get("last_answer_kind") or "") in {"selected_option_card", "selected_option_details"}
    )
    if not has_seen_selected_card:
        return False
    return bool(re.search(r"интерес|подходит|что\s+дальше|дальше|готов|беру|устраивает", text_l))


def _format_operator_handoff_for_option(option: dict[str, Any]) -> str:
    name = _display_complex_name(option.get("name") if isinstance(option, dict) else "")
    return (
        f"С этажами, бронью и конкретными квартирами по {name} лучше не гадать — это зависит от свободных вариантов прямо сейчас. "
        "Оператор посмотрит, что реально доступно, и подскажет, можно ли это забронировать.\n\n"
        "Хотите оставить номер для связи?"
    )


def _format_operator_handoff_for_context(state: dict[str, Any], user_text: str = "") -> str:
    options = (state.get("visible_options") or state.get("last_options") or [])[:3]
    names = [
        _display_complex_name(option.get("name"))
        for option in options
        if isinstance(option, dict) and option.get("name")
    ]
    params = state.get("params") if isinstance(state.get("params"), dict) else {}
    context_bits: list[str] = []
    if names:
        context_bits.append("варианты: " + ", ".join(names))
    purpose = params.get("purpose") or params.get("scenario")
    if purpose:
        context_bits.append(f"сценарий: {purpose}")
    if user_text:
        context_bits.append(f"последний вопрос: {user_text}")

    if context_bits:
        context = "; ".join(context_bits)
        return (
            "Да, можно связаться с оператором. Передам ему текущий контекст подбора: "
            f"{context}.\n\n"
            "Напишите номер для связи — оператор посмотрит актуальные квартиры и вернётся уже с конкретикой."
        )
    return (
        "Да, можно связаться с оператором. Напишите номер для связи — передам ваш запрос, "
        "и оператор уточнит актуальные варианты и условия."
    )


def _operator_funnel_sentence() -> str:
    return "Хотите, предложу оставить номер для связи?"


def _phone_captured_farewell() -> str:
    return (
        "Спасибо, номер получила. Передам оператору ваш запрос вместе с тем, что уже обсудили, "
        "чтобы не начинать всё заново. Он свяжется с вами и проверит актуальные варианты, наличие и условия."
    )


def _normalize_phone(raw: Any) -> str:
    """Оставляем только безопасную форму номера для валидации; в логи полный номер не пишем."""
    return "".join(ch for ch in str(raw or "") if ch.isdigit() or ch == "+")


def _phone_digits(phone: Any) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def _extract_phone_from_text(raw: Any) -> str:
    phone = _normalize_phone(raw)
    digits = _phone_digits(phone)
    if 10 <= len(digits) <= 15:
        return phone
    return ""


def _looks_like_phone_text(raw: Any) -> bool:
    """Похоже на попытку оставить телефон, но не обязательно валидно.

    Важно не путать бюджетные фразы вроде «до 200к» с телефоном: там мало цифр.
    """
    text = str(raw or "")
    digits = _phone_digits(text)
    if len(digits) >= 10:
        return True
    phone_words = re.search(r"\b(?:телефон|номер|контакт|связ[ьи]|whatsapp|ватсап|вот мой)\b", text, re.I)
    return bool(phone_words and len(digits) >= 7)


def _has_phone_capture_context(state: dict[str, Any]) -> bool:
    if state.get("awaiting_phone"):
        return True
    if state.get("operator_context"):
        return True
    if state.get("selected_option"):
        return True
    offer = str(state.get("last_offer_type") or "")
    return offer in {"operator_for_selected", "selected_option_details", "awaiting_phone"}


def _phone_needs_context_response() -> str:
    return (
        "Вижу номер, но не понимаю, по какому ЖК или запросу его передать. "
        "Напишите, какой ЖК интересует, или сначала выберите вариант из подборки."
    )


def _phone_log_meta(phone: str) -> dict[str, Any]:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return {"phone_len": len(digits), "phone_last4": digits[-4:] if len(digits) >= 4 else ""}


def _non_text_message_type(message: Any) -> str:
    """Короткий тип Telegram-сообщения без text, чтобы не молчать и нормально логировать."""
    if getattr(message, "contact", None):
        return "contact"
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "document", None):
        return "document"
    if getattr(message, "sticker", None):
        return "sticker"
    if getattr(message, "location", None):
        return "location"
    return "unknown"


def _non_text_fallback_response(message_type: str) -> str:
    if message_type == "contact":
        return "Контакт получила, но номер не разобрала. Напишите телефон текстом в формате +7XXXXXXXXXX."
    return "Пока я понимаю только текстовые запросы. Напишите, что ищете: район, бюджет, комнатность или ЖК."


def _build_known_option_prompt(option: dict[str, Any], client_request: str) -> str:
    """Контекст для LLM: раскрыть выбранный ЖК по уже известным данным, без нового поиска и выдумок."""
    safe_option = _safe_option_payload(option)
    # Смысл запроса не выводим substring/regex'ами. Этот prompt только передаёт
    # LLM безопасные факты; intent/purpose должны приходить из orchestrator/search state.
    client_intent = "selected_option_detail"
    client_purpose = "unknown"
    allowed_inferences = {
        "price": "можно говорить про понятный бюджет входа и ценовой ориентир",
        "finishing": "если есть отделка — меньше ремонта на старте; НЕ добавляй аренду, перепродажу или доходность, если таких фактов нет в SAFE_FACTS",
        "ready": "можно объяснить горизонт ожидания или близость сдачи, но не обещать ключи без факта",
        "location": "можно назвать район/локацию и объяснить, где находится проект",
        "area": "можно назвать диапазон площадей, но не обещать конкретную квартиру",
        "developer": "можно назвать застройщика только если он есть в SAFE_FACTS",
        "metro": "можно назвать метро только если оно есть в SAFE_FACTS",
    }
    return (
        "ROLE:\n"
        "Ты Ирина, живой консультант по новостройкам. Пишешь тепло, просто и по делу.\n\n"
        "SITUATION:\n"
        "Клиент уже выбрал вариант из предыдущего списка. Новый широкий поиск не нужен. "
        "Нужно раскрыть выбранный ЖК по безопасным фактам ниже, без выдумок и без технических объяснений.\n\n"
        "USER_ACTION:\n"
        f"{client_request}\n\n"
        "CLIENT_INTENT:\n"
        f"{client_intent}\n\n"
        "CLIENT_PURPOSE:\n"
        f"{client_purpose}\n\n"
        "SAFE_FACTS:\n"
        f"{json.dumps(safe_option, ensure_ascii=False, indent=2)}\n\n"
        "ALLOWED_INFERENCES:\n"
        f"{json.dumps(allowed_inferences, ensure_ascii=False, indent=2)}\n\n"
        "MISSING_OR_LIVE_ONLY:\n"
        "Актуальное наличие, актуальные квартиры, бронь, конкретные квартиры, этажи, корпуса, скидки, ипотека, показ и условия покупки "
        "нельзя подтверждать самой. Если клиент просит это — скажи по-человечески, что оператор посмотрит актуальные варианты. "
        "Не формулируй как ошибку данных и не говори «этого нет в MCP». "
        "Если CLIENT_INTENT = selected_option_detail и клиент не просил live-детали, не предлагай оператора, контакт, бронь или проверку актуального наличия в этом ответе.\n\n"
        "FORBIDDEN_FACTS:\n"
        "Не придумывай метро, инфраструктуру, скидки, ипотеку, наличие, бронь, этажи, корпуса, планировки, школы, парки, сроки, класс или сегмент ЖК. "
        "Не придумывай инвестиционные выводы: аренду, перепродажу, доходность, ликвидность, рост цены или перспективность района, если этого нет в SAFE_FACTS. "
        "Не называй ЖК «комфорт-класс», «бизнес-класс», «премиум», если этого нет в SAFE_FACTS. "
        "Не оценивай район как «отличный», «удобный», «перспективный» без такого факта. "
        "Не обещай универсальную пригодность: не пиши «для любого состава семьи», «подойдёт всем», «идеально для всех» без прямого факта. "
        "Если отделки нет, не обещай «любой дизайн-проект» — говори спокойнее: ремонт можно планировать под себя, но это отдельные вложения.\n\n"
        "FORBIDDEN_PHRASES_FOR_CLIENT:\n"
        "Запрещённые клиентские фразы: MCP; JSON; «подтверждённые данные»; «в MCP-данных»; «не удалось подтвердить»; "
        "«чтобы не выдумывать»; «в режиме реального времени»; «доходность и ликвидность нужно проверять отдельно»; "
        "«Больше подтверждённой информации прямо сейчас не добавлю».\n\n"
        "RESPONSE_SHAPE:\n"
        "Сформулируй новый живой ответ Ирины по этим данным: расскажи максимум полезного, что подтверждено в карточке, "
        "но человеческим языком, как консультант, а не как выгрузка из системы. Не повторяй дословно предыдущую карточку.\n"
        "- не пиши одним плотным абзацем. Используй 2-4 коротких абзаца: что за ЖК/где; цена/срок/отделка; польза; следующий шаг. Финальный вопрос всегда отдельным абзацем;\n"
        "- начинай тепло и просто: «По этому ЖК картина такая...» / «Да, расскажу подробнее...» / «Если смотреть его как вариант для покупки...»;\n"
        "- объясняй пользу из фактов: цена → понятен бюджет входа; отделка → меньше ремонта на старте; "
        "готовность/срок → понятнее горизонт ожидания; локация → понятно, где находится проект;\n"
        "- если в USER_ACTION есть инвестиционный мотив, не уводи ответ в переезд/жизнь. Говори только про факты карточки: бюджет входа, отделку, срок, площадь; не добавляй аренду/перепродажу/доходность без SAFE_FACTS;\n"
        "- если CLIENT_PURPOSE = self_use, не уводи ответ в инвестиции, аренду или перепродажу. Говори про жизнь, переезд, ремонт и понятный срок;\n"
        "- если CLIENT_PURPOSE = family, не выдумывай школы/парки/дворы. Используй только реальные факты: площадь, отделка, готовность, бюджет;\n"
        "- для инвестиционного мотива не пиши сухо «доходность и ликвидность нужно проверить». Лучше: "
        "«как инвестиционный вариант его можно рассматривать от понятного бюджета, а дальше уже выбирать конкретную планировку и цену входа»;\n"
        "- если данных о наличии/этажах/корпусах/броне нет, не перечисляй это как провал данных. Скажи мягко: "
        "«по конкретным квартирам и брони лучше отдельно посмотреть актуальные варианты»;\n"
        "- если CLIENT_INTENT = selected_option_detail и CLIENT_PURPOSE = investment, интерес уже проявлен: не уводи в бесконечное сравнение, а мягко веди к оператору для проверки актуальных квартир, цены входа и условий;\n"
        "- если CLIENT_INTENT = selected_option_detail и CLIENT_PURPOSE НЕ investment, это не запрос на бронь/наличие. Не зови оператора и не спрашивай про контакт;\n"
        "- не заканчивай шаблонным вопросом «какой аспект этого ЖК разобрать подробнее» и не спрашивай про конкретные планировки, если клиент не просил live-детали;\n"
        "- в конце задай один живой следующий вопрос. Для инвестиционного выбранного ЖК спрашивай про следующий шаг к оператору: оставить номер, чтобы проверить актуальные квартиры, цены входа и условия. "
        "Для неинвестиционного первичного выбора можно спросить: сравнить с другими вариантами или коротко разобрать цену, срок и отделку. Если клиент просит live-детали — мягко предложи проверить актуальные квартиры/оставить контакт.\n\n"
        "STYLE_EXAMPLES:\n"
        "Плохо: «В MCP-данных вижу ЖК Лучи. Не удалось подтвердить наличие студий».\n"
        "Хорошо: «По ЖК «Лучи» уже виден понятный ориентир: Солнцево, квартиры с отделкой и цена от 10.6 млн. "
        "Для инвестиции это удобно как понятный бюджет входа: отделка снижает объём ремонта на старте. "
        "А конкретные студии и бронь лучше проверить по актуальному наличию».\n"
        "Плохо: «Больше подтверждённой информации не добавлю, чтобы не выдумывать».\n"
        "Хорошо: «По покупке важны уже живые детали — какие квартиры сейчас свободны, какие этажи есть и можно ли поставить бронь. "
        "Это лучше быстро проверить у оператора».\n\n"
        "OUTPUT_JSON:\n"
        "Верни валидный JSON только с полями response и params. Inline-кнопки не формируй. Следующий шаг предложи живым текстом."
    )


def _button_log_preview(rows: list[list[dict]] | None) -> list[list[dict[str, str]]]:
    """Безопасный preview отправленных кнопок для dialogs-jsonl: только text/callback_data."""
    preview: list[list[dict[str, str]]] = []
    for row in rows or []:
        safe_row: list[dict[str, str]] = []
        for button in row:
            safe_row.append({
                "text": str(button.get("text") or "")[:80],
                "callback_data": str(button.get("callback_data") or "")[:120],
            })
        if safe_row:
            preview.append(safe_row)
    return preview


def _callback_button_text(rows: list[list[dict]] | None, callback_data: str) -> str:
    for row in rows or []:
        for button in row:
            if str(button.get("callback_data") or "") == callback_data:
                return str(button.get("text") or "")
    return ""


def _dialog_state_preview(state: dict[str, Any]) -> dict[str, Any]:
    selected = state.get("selected_option") or {}
    return {
        "params": dict(state.get("params") or {}),
        "last_options": [o.get("name") for o in (state.get("last_options") or [])[:4]],
        "selected_option": selected.get("name") if isinstance(selected, dict) else None,
        "turns_after_results": state.get("turns_after_results"),
        "awaiting_phone": bool(state.get("awaiting_phone")),
        "last_bot_question": state.get("last_bot_question"),
        "last_offer_type": state.get("last_offer_type"),
        "last_answer_kind": state.get("last_answer_kind"),
    }


def _selected_option_rows(idx: int) -> list[list[dict]]:
    return []


def _option_ordinal(idx: Any) -> str:
    labels = {1: "Первый", 2: "Второй", 3: "Третий"}
    try:
        num = int(idx)
    except (TypeError, ValueError):
        num = 0
    return labels.get(num, f"{num}-й" if num else "Этот")


def _looks_missing(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return (
        not text
        or text in {"нет", "не указан", "не указано", "информация отсутствует", "none", "null", "уточняется"}
        or "не указан" in text
        or "не указано" in text
        or "отсутств" in text
        or "уточн" in text
    )


def _format_location_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.lower().replace("ё", "е")
    mapping = {
        "msk": "Москва",
        "мск": "Москва",
        "moscow": "Москва",
        "mo": "Московская область",
        "мо": "Московская область",
        "moscow oblast": "Московская область",
    }
    return mapping.get(normalized, text)


def _format_price_value(value: Any, price_min: Any = None) -> str:
    text = str(value or "").strip()
    if _looks_missing(text) and not price_min:
        return ""
    if text and not re.fullmatch(r"\d+(?:\.\d+)?", text.replace(" ", "")):
        return text
    parsed = _price_min(price_min or text)
    if not parsed:
        return text
    mln = parsed / 1_000_000
    pretty = f"{mln:.1f}".replace(".", ",")
    if pretty.endswith(",0"):
        pretty = pretty[:-2]
    return f"от {pretty} млн рублей"


def _extract_year(value: Any) -> int | None:
    match = re.search(r"\b(20\d{2})\b", str(value or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _format_ready_sentence(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    low = text.lower().replace("ё", "е")
    if "сдан" in low or "готов" in low:
        return f"по срокам это готовый вариант: {text}"
    year = _extract_year(text)
    current_year = datetime.now(timezone.utc).year
    if year and year < current_year:
        return f"срок сдачи — {text}, то есть по срокам объект уже должен быть сдан"
    if year and year == current_year:
        return f"срок сдачи — {text}, то есть это ближайший срок без долгого ожидания"
    if year and year > current_year:
        return f"срок сдачи — {text}; это вариант с ожиданием до сдачи"
    return f"срок/готовность — {text}"


def _selected_option_fact_sentences(option: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    name = option.get("name") or "варианту"
    if not _looks_missing(option.get("price")):
        facts.append(f"По цене вижу ориентир {_format_price_value(option['price'], option.get('price_min'))}.")
    if not _looks_missing(option.get("location")):
        facts.append(f"По локации вижу: {_format_location_value(option['location'])}.")
    if not _looks_missing(option.get("ready")):
        facts.append(_format_ready_sentence(option["ready"]).capitalize() + ".")
    if not _looks_missing(option.get("area")):
        facts.append(f"По площади есть ориентир: {option['area']}.")
    if not _looks_missing(option.get("finishing")):
        facts.append(f"По отделке указано: {option['finishing']}.")
    if not _looks_missing(option.get("metro")):
        facts.append(f"По транспорту вижу метро: {option['metro']}.")
    if not _looks_missing(option.get("developer")):
        facts.append(f"Застройщик: {option['developer']}.")
    if not facts:
        facts.append(f"По {name} вижу только короткую карточку без дополнительных подтверждённых деталей.")
    return facts


def _investment_note_from_facts(option: dict[str, Any]) -> str:
    ready_sentence = _format_ready_sentence(option.get("ready"))
    why_close = str(option.get("why_close") or "").strip()
    if ready_sentence and any(word in ready_sentence.lower() for word in ("сдан", "готов", "должен быть сдан", "ближайший срок")):
        return "Для инвестиции это полезно тем, что не нужно закладывать долгий срок ожидания до готовности."
    if why_close:
        return f"Для инвестиционного сценария это стоит учитывать: {why_close}."
    if option.get("price_min"):
        return "Для инвестиции здесь понятен бюджет входа: дальше уже важно выбрать конкретную планировку и цену, с которой комфортно заходить в сделку."
    return "Для инвестиции этот ЖК можно рассмотреть как стартовый вариант, а конкретную квартиру и цену входа лучше выбирать отдельно."


def _option_benefit(option: dict[str, Any]) -> str:
    """Короткая польза для клиента только из известных полей, без выдумок."""
    ready = str(option.get("ready") or "").lower()
    finishing = str(option.get("finishing") or "").lower()
    price_min = option.get("price_min")
    if "сдан" in ready or "готов" in ready:
        return "его стоит смотреть, если хочется готовый корпус без ожидания"
    if "отдел" in finishing and "без отдел" not in finishing:
        return "его удобно рассматривать, если не хочется начинать с чернового ремонта"
    if price_min:
        return "по нему уже понятен бюджет входа"
    if option.get("area"):
        return "по нему уже есть понятный ориентир по площади"
    return "по нему можно быстро проверить актуальные квартиры"


def _family_reason_from_facts(option: dict[str, Any]) -> str:
    """Продающая причина для family-сценария только из подтверждённых полей."""
    raw_text = json.dumps(option.get("raw") or {}, ensure_ascii=False).lower()
    if "закрыт" in raw_text or "двор без машин" in raw_text:
        return (
            "отличный вариант для семьи: закрытый двор или двор без машин даёт ребёнку больше "
            "пространства для прогулок, а родителям — спокойствия"
        )
    if "детск" in raw_text:
        return (
            "хороший выбор для семьи благодаря детской инфраструктуре — "
            "повседневные вопросы с ребёнком будет проще закрывать рядом с домом"
        )
    if "школ" in raw_text or "сад" in raw_text:
        return (
            "удобный вариант для семей с детьми: школа или детский сад помогают сделать "
            "ежедневные маршруты проще"
        )
    if "парк" in raw_text:
        return (
            "подойдёт семье, которая ценит прогулки и отдых рядом с домом: "
            "парк или зелёная зона добавляют больше сценариев для жизни с ребёнком"
        )
    ready = str(option.get("ready") or "").lower()
    finishing = str(option.get("finishing") or "").lower()
    if "сдан" in ready or "готов" in ready:
        return "практичный вариант для семьи: готовый корпус проще планировать под переезд и обустройство"
    if "отдел" in finishing and "без отдел" not in finishing:
        return "удобный вариант для быстрого переезда: отделка экономит время и силы на ремонте"
    if not _looks_missing(option.get("area")):
        return "стоит рассмотреть семье, которой важно подобрать комфортную площадь под свой образ жизни"
    if option.get("price_min"):
        return "для семейного выбора уже понятен бюджет входа"
    if not _looks_missing(option.get("location")):
        return "интересный вариант для семьи в этой локации — можно подобрать подходящий формат квартиры"
    return "вариант стоит рассмотреть для семьи: можно подобрать подходящую квартиру и уточнить детали покупки"


def _format_option_response(option: dict[str, Any], purpose: Any = None) -> str:
    name = option.get("name") or "этот вариант"
    intro = f"{_option_ordinal(option.get('idx'))} вариант — {name}."
    facts = _selected_option_fact_sentences(option)[:5]
    # Для Telegram карточка выбранного ЖК должна читаться как несколько коротких
    # абзацев, а не как плотная простыня. Первые 2-3 факта — основной блок,
    # остальные факты — отдельный короткий блок ниже.
    fact_text = " ".join(facts[:3])
    extra_fact_text = " ".join(facts[3:])
    purpose_low = str(purpose or "").lower()

    if purpose_low == "family":
        scenario_note = _family_reason_from_facts(option).capitalize() + "."
    elif purpose_low in {"investment", "invest", "инвестиции", "инвест", "инвестиций"}:
        scenario_note = _investment_note_from_facts(option)
    else:
        scenario_note = f"Поэтому { _option_benefit(option) }."

    nuance = ""
    if not _looks_missing(option.get("why_close")) and purpose_low not in {"investment", "invest", "инвестиции", "инвест", "инвестиций"}:
        nuance = f"\n\nВажно: {option['why_close']}."

    check_next = (
        "Если нужно перейти к покупке, отдельно проверим актуальное наличие квартир, "
        "конкретные корпуса, этажи и условия."
    )
    question = "Хотите, расскажу подробнее по этому ЖК или сравним его с другими вариантами?"
    blocks = [intro, fact_text]
    if extra_fact_text:
        blocks.append(extra_fact_text)
    blocks.append(f"{scenario_note}{nuance}".strip())
    blocks.extend([check_next, question])
    return "\n\n".join(block for block in blocks if block)


def _format_cheaper_response(options: list[dict[str, Any]]) -> str:
    if not options:
        return f"По последнему списку не вижу вариантов дешевле. {_operator_funnel_sentence()}"
    return _format_options_summary_response(
        options,
        "Из более доступных по последнему списку вижу",
        "Какой из этих вариантов рассмотреть подробнее?",
    )


def _format_options_summary_response(options: list[dict[str, Any]], lead: str, question: str) -> str:
    chunks = []
    for idx, o in enumerate(options[:3], start=1):
        price = f", {_format_price_value(o.get('price'), o.get('price_min'))}" if not _looks_missing(o.get("price")) else ""
        loc = f" ({_format_location_value(o['location'])})" if not _looks_missing(o.get("location")) else ""
        finish = f", отделка: {o['finishing']}" if not _looks_missing(o.get("finishing")) else ""
        chunks.append(f"{idx}. {o['name']}{loc}{price}{finish}")
    return _format_numbered_list_spacing(f"{lead}:\n" + "\n".join(chunks) + f"\n{question}")


def _client_ready_fact(value: Any) -> str:
    text = str(value or "").strip()
    if _looks_missing(text):
        return ""
    low = text.lower().replace("ё", "е")
    if "сдан" in low or "готов" in low:
        return "дом уже сдан"
    year = _extract_year(text)
    if year:
        current_year = datetime.now(timezone.utc).year
        if year <= current_year:
            return "дом уже сдан"
        return f"сдача запланирована на {year} год"
    return f"срок: {text}"


def _client_finishing_fact(value: Any) -> str:
    text = str(value or "").strip()
    if _looks_missing(text):
        return ""
    low = text.lower().replace("ё", "е")
    if "без отдел" in low:
        return "без отделки"
    if "отдел" in low or low in {"есть", "да", "true", "1"}:
        return "есть квартиры с отделкой"
    return text


def _has_positive_finishing(value: Any) -> bool:
    finishing = _client_finishing_fact(value)
    return bool(finishing) and "без отдел" not in finishing.lower()


def _client_price_fact(option: dict[str, Any]) -> str:
    raw_price = option.get("price_range") or option.get("price")
    price = _format_price_value(raw_price, option.get("price_min")) if not _looks_missing(raw_price) else ""
    if not price and option.get("price_min"):
        price = _format_price_value("", option.get("price_min"))
    if not price:
        return ""
    raw = str(raw_price or "")
    nums = re.findall(r"\d[\d\s]{5,}(?:[.,]\d+)?", raw)
    if len(nums) >= 2 and "млн" not in raw.lower():
        vals: list[float] = []
        for n in nums[:2]:
            try:
                vals.append(float(n.replace(" ", "").replace(",", ".")) / 1_000_000)
            except ValueError:
                vals = []
                break
        if len(vals) == 2:
            pretty = []
            for value in vals:
                item = f"{value:.2f}".replace(".", ",").rstrip("0").rstrip(",")
                pretty.append(item)
            return f"цены от {pretty[0]} до {pretty[1]} млн рублей"
    price = re.sub(r"(\d)\.(\d)", r"\1,\2", str(price)).replace(" - ", " до ").replace(" – ", " до ")
    if " до " in price and not price.lower().startswith("от "):
        price = f"от {price}"
    return f"цены {price}" if str(price).startswith("от ") else f"цены {price}"


def _client_area_fact(value: Any) -> str:
    if _looks_missing(value):
        return ""
    text = str(value).strip()
    text = re.sub(r"(\d)\.(\d)", r"\1,\2", text)
    text = text.replace(" - ", " до ").replace(" – ", " до ")
    if re.search(r"\d\s*до\s*\d", text) and not text.lower().startswith("от "):
        text = f"от {text}"
    return f"площади {text}"


def _join_fact_values(*values: Any) -> str:
    chunks: list[str] = []
    seen: set[str] = set()
    for value in values:
        if _looks_missing(value):
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            text = str(item).strip()
            if not text:
                continue
            compact = _compact_option_text(text)
            if compact and compact not in seen:
                seen.add(compact)
                chunks.append(text)
    return "; ".join(chunks)


def _infra_text(option: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in ("infrastructure", "schools", "kindergartens", "parks", "clinics", "yards", "shops"):
        value = option.get(key)
        if _looks_missing(value):
            continue
        if isinstance(value, list):
            value = ", ".join(str(v).strip() for v in value if str(v).strip())
        boolish = str(value).strip().lower().strip(" .,!;:")
        if isinstance(value, bool) or boolish in {"1", "true", "да", "yes"}:
            text = "двор без машин" if key == "yards" else ""
        else:
            text = str(value).strip()
        if text:
            chunks.append(text)
    return "; ".join(chunks)


def _has_fact_kind(option: dict[str, Any], kind: str) -> bool:
    hay = " ".join(str(option.get(k) or "") for k in (
        "infrastructure", "schools", "kindergartens", "parks", "clinics", "yards", "shops", "metro", "why_close"
    )).lower().replace("ё", "е")
    patterns = {
        "park": r"парк|лес|зелен|набережн|водоем",
        "school": r"школ",
        "kindergarten": r"детск\w*\s+сад|садик|садов",
        "clinic": r"поликлиник|клиник|аптек",
        "yard": r"двор|площадк|без\s+машин",
        "metro": r"\bметро\b|м\.\s*[а-яa-z]|мцд|бкл",
    }
    pattern = patterns.get(kind)
    return bool(pattern and re.search(pattern, hay))


def _stage_option_fact_parts(option: dict[str, Any], scenario: str = "self_use") -> list[str]:
    base: dict[str, str] = {}
    if not _looks_missing(option.get("location")):
        base["location"] = _format_location_value(option.get("location"))
    ready = _client_ready_fact(option.get("ready") or option.get("status") or option.get("delivered"))
    if ready:
        base["ready"] = ready
    finish = _client_finishing_fact(option.get("finishing"))
    if finish:
        base["finishing"] = finish
    area = _client_area_fact(option.get("area"))
    if area:
        base["area"] = area
    price = _client_price_fact(option)
    if price:
        base["price"] = price
    if not _looks_missing(option.get("metro")):
        base["metro"] = f"метро: {option['metro']}"

    infra_parts = {
        "schools": option.get("schools"),
        "kindergartens": option.get("kindergartens"),
        "parks": option.get("parks"),
        "clinics": option.get("clinics"),
        "yards": option.get("yards"),
        "shops": option.get("shops"),
        "infrastructure": option.get("infrastructure"),
    }

    def add_infra(parts: list[str], key: str) -> None:
        value = infra_parts.get(key)
        if _looks_missing(value):
            return
        text = _join_fact_values(value)
        if text:
            parts.append(text)

    parts: list[str] = []
    if scenario == "family":
        for key in ("schools", "kindergartens", "parks", "clinics", "yards", "infrastructure"):
            add_infra(parts, key)
        for key in ("location", "price", "ready", "finishing", "area", "metro"):
            if base.get(key):
                parts.append(base[key])
    elif scenario == "investment":
        for key in ("price", "area", "ready", "finishing", "location", "metro"):
            if base.get(key):
                parts.append(base[key])
    elif scenario == "fast_move":
        for key in ("ready", "finishing", "price", "location", "area", "metro"):
            if base.get(key):
                parts.append(base[key])
    else:
        for key in ("location", "ready", "finishing", "area", "price", "metro"):
            if base.get(key):
                parts.append(base[key])
        infra = _infra_text(option)
        if infra:
            parts.append(infra)
    return parts


def _stage_option_benefit(option: dict[str, Any], scenario: str, used: set[str]) -> str:
    ready = _client_ready_fact(option.get("ready") or option.get("status") or option.get("delivered"))
    finishing = _client_finishing_fact(option.get("finishing")) if _has_positive_finishing(option.get("finishing")) else ""
    area = _client_area_fact(option.get("area")).replace("площади ", "", 1)
    price = _client_price_fact(option)
    metro = "" if _looks_missing(option.get("metro")) else str(option.get("metro"))
    infra = _infra_text(option)
    schools = _join_fact_values(option.get("schools"))
    kindergartens = _join_fact_values(option.get("kindergartens"))
    parks = _join_fact_values(option.get("parks"))
    clinics = _join_fact_values(option.get("clinics"))
    yards = _join_fact_values(option.get("yards"))

    candidates: list[tuple[str, str]] = []
    if scenario == "family":
        edu = _join_fact_values(schools, kindergartens)
        if edu:
            candidates.append((f"edu_family:{_compact_option_text(edu)}", f"Для семьи это удобно: {edu} помогают закрыть ежедневную рутину рядом с домом."))
        if parks:
            candidates.append((f"park_family:{_compact_option_text(parks)}", f"Для прогулок с детьми рядом есть {parks}."))
        if clinics:
            candidates.append((f"clinic_family:{_compact_option_text(clinics)}", f"{clinics} рядом — полезный плюс, когда важно быстро решать бытовые вопросы семьи."))
        if yards:
            candidates.append((f"yard_family:{_compact_option_text(yards)}", f"{yards} добавляют удобства для прогулок и игр рядом с домом."))
        if ready == "дом уже сдан":
            candidates.append(("ready_family", "Готовый дом проще планировать для переезда семьи."))
        if finishing:
            candidates.append(("finish_family", "С отделкой меньше ремонтных хлопот после покупки."))
        if area:
            candidates.append(("area_family", f"Диапазон площадей {area} помогает подобрать формат под семью."))
        if price:
            candidates.append(("price_family", "По цене сразу понятно, с какого бюджета смотреть этот вариант."))
    elif scenario == "investment":
        if price:
            candidates.append(("price_invest", "По цене сразу понятно, с чем сравнивать этот вариант."))
        if finishing:
            candidates.append(("finish_invest", "Отделка уменьшает объём работ и вложений после покупки."))
        if ready == "дом уже сдан":
            candidates.append(("ready_invest", "Готовый дом проще оценивать без долгого ожидания сдачи."))
    elif scenario == "metro_access":
        if metro:
            candidates.append(("metro", "Метро рядом — это удобно для ежедневных поездок."))
        if price:
            candidates.append(("price_metro", "Цена помогает сразу сравнить варианты по бюджету и локации."))
    else:
        if _has_fact_kind(option, "park"):
            candidates.append(("park_self", "Зелёная зона рядом — приятный плюс для прогулок и повседневной жизни."))
        if _has_fact_kind(option, "clinic") or _has_fact_kind(option, "school") or _has_fact_kind(option, "kindergarten"):
            candidates.append(("infra_self", "Инфраструктура рядом помогает проще решать повседневные дела."))
        if _has_fact_kind(option, "yard"):
            candidates.append(("yard_self", "Дворовая инфраструктура добавляет удобства рядом с домом."))
        if ready == "дом уже сдан":
            candidates.append(("ready", "Готовый дом проще планировать для переезда."))
        if finishing:
            candidates.append(("finish", "С отделкой меньше ремонтных хлопот после покупки."))
        if area:
            candidates.append(("area", f"По площади есть ориентир {area}, проще выбрать подходящий формат."))
        if price:
            candidates.append(("price", "По цене сразу понятен стартовый бюджет."))
        if metro:
            candidates.append(("metro", "Метро рядом — удобно для ежедневных поездок."))

    for key, phrase in candidates:
        if key not in used:
            used.add(key)
            return phrase
    return candidates[0][1] if candidates else "Можно выбрать этот вариант и дальше проверить конкретные квартиры."


_SALES_PHRASE_BAD_RE = re.compile(
    r"(?:лучш\w*|идеальн\w*|выгодн\w*|перспективн\w*|премиальн\w*|отличн\w*|"
    r"хороший\s+вариант|сценари\w*|сдача/готовность|верхняя\s+точка|по\s+данным|в\s+базе|"
    r"доходност\w*|аренд\w*|ликвидност\w*|рост\s+цен\w*|прибыл\w*|скидк\w*|ипотек\w*)",
    re.IGNORECASE,
)


def _stage_sales_allowed_angles(option: dict[str, Any], scenario: str) -> list[str]:
    ready = _client_ready_fact(option.get("ready") or option.get("status") or option.get("delivered"))
    finishing = _client_finishing_fact(option.get("finishing")) if _has_positive_finishing(option.get("finishing")) else ""
    area = _client_area_fact(option.get("area")).replace("площади ", "", 1)
    price = _client_price_fact(option)
    metro = "" if _looks_missing(option.get("metro")) else str(option.get("metro"))
    angles: list[str] = []
    if scenario == "family":
        infra_angles: list[str] = []
        if _has_fact_kind(option, "park"):
            infra_angles.append("рядом есть место для прогулок с детьми на свежем воздухе")
        if _has_fact_kind(option, "school") or _has_fact_kind(option, "kindergarten"):
            infra_angles.append("школы и детские сады рядом упрощают семейную рутину")
        if _has_fact_kind(option, "clinic"):
            infra_angles.append("поликлиника или аптеки рядом помогают быстрее решать бытовые вопросы")
        if _has_fact_kind(option, "yard"):
            infra_angles.append("дворовая инфраструктура делает прогулки рядом с домом удобнее")
        if infra_angles:
            return infra_angles[:4]
        if ready == "дом уже сдан":
            angles.append("готовый дом проще планировать для переезда семьи")
        if finishing:
            angles.append("отделка уменьшает ремонтные хлопоты после покупки")
        if area:
            angles.append("диапазон площадей помогает подобрать формат под семью")
        if price:
            angles.append("цена даёт понятный ориентир для семейного бюджета")
    elif scenario == "investment":
        if price:
            angles.append("цена даёт понятную точку входа для сравнения")
        if finishing:
            angles.append("отделка уменьшает объём работ после покупки")
        if ready == "дом уже сдан":
            angles.append("готовый дом проще оценивать без ожидания сдачи")
        angles.append("не обещать доходность, аренду или рост цены")
    elif scenario == "metro_access":
        if metro:
            angles.append("ежедневные поездки проще, когда метро рядом")
        if price:
            if metro:
                angles.append("цена помогает сравнить варианты рядом с метро")
            else:
                angles.append("цена помогает сравнить варианты по бюджету и локации")
        if ready:
            angles.append("срок можно учитывать при выборе")
    elif scenario == "budget":
        if price:
            angles.append("цена помогает понять, попадает ли вариант в бюджет")
        if finishing:
            angles.append("отделка снижает стартовые ремонтные хлопоты")
        if area:
            angles.append("площадь помогает оценить, подходит ли формат")
    else:
        infra_angles: list[str] = []
        if _has_fact_kind(option, "park"):
            infra_angles.append("рядом есть место для прогулок и повседневного отдыха")
        if _has_fact_kind(option, "clinic") or _has_fact_kind(option, "school") or _has_fact_kind(option, "kindergarten"):
            infra_angles.append("инфраструктура рядом помогает в повседневных делах")
        if _has_fact_kind(option, "yard"):
            infra_angles.append("дворовая инфраструктура добавляет удобства рядом с домом")
        angles.extend(infra_angles[:2])
        if ready == "дом уже сдан":
            angles.append("готовый дом проще планировать для переезда")
        if finishing:
            angles.append("отделка уменьшает ремонтные хлопоты")
        if area:
            angles.append("площадь помогает выбрать подходящий формат")
        if price:
            angles.append("цена даёт понятный ориентир для сравнения")
        if metro:
            angles.append("метро рядом удобно для ежедневных поездок")
    return angles[:4] or ["коротко объяснить пользу только из перечисленных фактов"]


def _build_sales_phrase_payload(options: list[dict[str, Any]], scenario: str) -> dict[str, Any]:
    return {
        "task": "Write one short human benefit for each item. Code will assemble the final answer.",
        "scenario": scenario,
        "items": [
            {
                "idx": idx,
                "object": option.get("name") or f"вариант {idx}",
                "facts": _stage_option_fact_parts(option, scenario),
                "allowed_angles": _stage_sales_allowed_angles(option, scenario),
            }
            for idx, option in enumerate(options[:3], start=1)
        ],
    }


def _sales_phrase_claims_allowed(benefit: str, option: dict[str, Any]) -> bool:
    low = benefit.lower().replace("ё", "е")
    checks = [
        (r"парк|лес|зелен|набережн|водоем", "park"),
        (r"школ", "school"),
        (r"детск\w*\s+сад|садик|садов", "kindergarten"),
        (r"поликлиник|клиник|аптек", "clinic"),
        (r"двор|площадк|без\s+машин", "yard"),
        (r"\bметро\b|м\.\s*[а-яa-z]|мцд|бкл", "metro"),
    ]
    for pattern, kind in checks:
        if re.search(pattern, low) and not _has_fact_kind(option, kind):
            return False
    return True


def _validate_sales_phrase_items(data: dict[str, Any], options_count: int, options: list[dict[str, Any]] | None = None) -> dict[int, str]:
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != options_count:
        return {}
    out: dict[int, str] = {}
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            return {}
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            return {}
        benefit = str(item.get("benefit") or "").strip()
        compact = _compact_option_text(benefit)
        if idx < 1 or idx > options_count or not benefit:
            return {}
        if len(benefit) > 180 or "?" in benefit or "*" in benefit or "•" in benefit:
            return {}
        if _SALES_PHRASE_BAD_RE.search(benefit):
            return {}
        if options and idx <= len(options) and not _sales_phrase_claims_allowed(benefit, options[idx - 1]):
            return {}
        if compact in seen:
            return {}
        seen.add(compact)
        out[idx] = _ensure_sentence_period(benefit)
    return out if len(out) == options_count else {}


def _stage_lead_for_first_list(scenario: str, count: int, options: list[dict[str, Any]] | None = None) -> str:
    word = "три варианта" if count >= 3 else "несколько вариантов"
    if scenario == "family":
        return f"Подобрала {word} для семьи."
    if scenario == "investment":
        return f"Подобрала {word} под инвестицию."
    if scenario == "metro_access":
        metro_count = sum(1 for option in (options or [])[:3] if not _looks_missing(option.get("metro")))
        if metro_count >= min(2, count):
            return f"Нашла {word} рядом с метро."
        return f"Нашла {word}, которые можно сравнить по цене и локации."
    if scenario == "budget":
        return f"Нашла {word} по бюджету."
    return f"Нашла {word} для сравнения."


async def _sales_phrases_for_stage(
    client: OvermindClient,
    options: list[dict[str, Any]],
    scenario: str,
) -> tuple[dict[int, str], dict[str, Any]]:
    if not SALES_PHRASE_ENABLED or not options:
        return {}, {"enabled": SALES_PHRASE_ENABLED, "applied": False}
    payload = _build_sales_phrase_payload(options, scenario)
    try:
        data, meta = await client.sales_phrases(payload)
        benefits = _validate_sales_phrase_items(data, min(len(options), 3), options[:3])
        if benefits:
            return benefits, {
                "enabled": True,
                "applied": True,
                "model": meta.get("model") or SALES_PHRASE_MODEL,
                "meta": meta,
            }
        LOGGER.warning("sales_phrase invalid output: %s", _safe_json_preview({"data": data, "meta": meta}))
        return {}, {"enabled": True, "applied": False, "skipped": "invalid_model_output", "meta": meta}
    except Exception:
        LOGGER.exception("sales_phrase failed")
        return {}, {"enabled": True, "applied": False, "skipped": "exception"}


def _render_stage_first_list(options: list[dict[str, Any]], scenario: str, sales_benefits: dict[int, str] | None = None) -> str:
    visible = options[:3]
    used: set[str] = set()
    sales_benefits = sales_benefits or {}
    blocks = [_stage_lead_for_first_list(scenario, len(visible), visible)]
    for idx, option in enumerate(visible, start=1):
        facts = ", ".join(_stage_option_fact_parts(option, scenario)[:5])
        benefit = sales_benefits.get(idx) or _stage_option_benefit(option, scenario, used)
        name = option.get("name") or f"вариант {idx}"
        line = f"{idx}. {name}"
        if facts:
            line += f" — {facts}"
        # Комментарий к ЖК должен быть отдельным абзацем, а не «прилипшей» строкой
        # под пунктом списка. Так Telegram-ответ читается как карточка: факт → польза.
        blocks.append(f"{_ensure_sentence_period(line)}\n\n{benefit}")
    blocks.append("Какой ЖК хотите рассмотреть подробнее?")
    return _format_numbered_list_spacing("\n\n".join(blocks))


def _recommendation_score(option: dict[str, Any], scenario: str) -> int:
    score = 0
    if scenario == "family":
        weights = {
            "schools": 4,
            "kindergartens": 4,
            "parks": 3,
            "clinics": 2,
            "yards": 2,
            "infrastructure": 1,
        }
        for key, weight in weights.items():
            if not _looks_missing(option.get(key)):
                score += weight
        if _client_ready_fact(option.get("ready") or option.get("status") or option.get("delivered")) == "дом уже сдан":
            score += 1
        return score
    if scenario == "fast_move":
        if _client_ready_fact(option.get("ready") or option.get("status") or option.get("delivered")) == "дом уже сдан":
            score += 5
        if _has_positive_finishing(option.get("finishing")):
            score += 3
        if _client_price_fact(option):
            score += 1
        return score
    if scenario == "investment":
        if _client_price_fact(option):
            score += 4
        if _client_area_fact(option.get("area")):
            score += 2
        if _has_positive_finishing(option.get("finishing")):
            score += 1
        if not _looks_missing(option.get("metro")):
            score += 1
        return score
    return len(_stage_option_fact_parts(option, scenario))


def _render_stage_recommendation(options: list[dict[str, Any]], scenario: str) -> str:
    visible = [option for option in options[:3] if isinstance(option, dict)]
    if not visible:
        return "Могу подсказать, но мне нужен текущий список вариантов. Хотите, сначала подберу 2–3 ЖК под ваши условия?"

    ranked = sorted(enumerate(visible), key=lambda item: (_recommendation_score(item[1], scenario), -item[0]), reverse=True)
    best_idx, best = ranked[0]
    name = best.get("name") or f"вариант {best_idx + 1}"
    benefit = _stage_option_benefit(best, scenario, set())
    facts = _stage_option_fact_parts(best, scenario)[:4]
    fact_text = ", ".join(facts)
    blocks = [f"Если выбирать из этих вариантов, я бы сначала смотрела {name}."]
    if fact_text:
        blocks.append(f"Почему: {fact_text}.")
    blocks.append(benefit)

    if len(ranked) > 1:
        second = ranked[1][1]
        second_name = second.get("name") or "второй вариант"
        second_facts = _stage_option_fact_parts(second, scenario)[:2]
        if second_facts:
            blocks.append(f"Вторым я бы держала {second_name}: там тоже есть {', '.join(second_facts)}.")
        else:
            blocks.append(f"Вторым я бы держала {second_name}, если первый не подойдёт по квартире или бюджету.")

    blocks.append(f"Хотите, разберу {name} подробнее?")
    return _format_numbered_list_spacing("\n\n".join(_ensure_sentence_period(block) if not block.endswith("?") else block for block in blocks))


def _render_stage_selected_object(option: dict[str, Any], scenario: str = "self_use", sales_benefit: str | None = None) -> str:
    name = option.get("name") or "этот ЖК"
    main_parts: list[str] = []
    if not _looks_missing(option.get("developer")):
        main_parts.append(f"проект {option.get('developer')}")
    if not _looks_missing(option.get("location")):
        main_parts.append(_format_location_value(option.get("location")))
    if not _looks_missing(option.get("metro")):
        main_parts.append(f"метро: {option.get('metro')}")
    elif not _looks_missing(option.get("transport")):
        main_parts.append(f"транспорт: {option.get('transport')}")
    if not _looks_missing(option.get("rooms")):
        main_parts.append(f"типы квартир: {option.get('rooms')}")
    area_fact = _client_area_fact(option.get("area"))
    if area_fact:
        main_parts.append(area_fact)
    price_fact = _client_price_fact(option)
    if price_fact:
        main_parts.append(price_fact)
    finish_fact = _client_finishing_fact(option.get("finishing"))
    if finish_fact:
        main_parts.append(finish_fact)
    ready_fact = _client_ready_fact(option.get("ready") or option.get("status") or option.get("delivered"))
    if ready_fact:
        main_parts.append(ready_fact)
    main_facts = ", ".join(main_parts[:8])
    ready = _client_ready_fact(option.get("ready") or option.get("status") or option.get("delivered"))
    finishing = _client_finishing_fact(option.get("finishing"))
    area = "" if _looks_missing(option.get("area")) else str(option.get("area"))
    used: set[str] = set()
    intro = f"{name}"
    if main_facts:
        intro += f" — {main_facts}"
    intro = _ensure_sentence_period(intro)
    if sales_benefit:
        benefit = sales_benefit.strip()
    elif scenario == "family" and ready == "дом уже сдан" and finishing:
        benefit = "Для семьи это удобно: готовый дом проще планировать для переезда, а отделка уменьшает ремонтные хлопоты."
    elif scenario == "family" and area:
        benefit = f"Для семьи это удобно: по площади есть ориентир {area}, проще выбрать подходящий формат."
    else:
        benefit = _stage_option_benefit(option, scenario, used)
    infra_parts: list[str] = []
    full_infra = _infra_text(option)
    if full_infra:
        infra_parts.append(full_infra)
    else:
        if _has_fact_kind(option, "school") or _has_fact_kind(option, "kindergarten"):
            schools = []
            if not _looks_missing(option.get("schools")):
                schools.append(str(option.get("schools")).strip())
            if not _looks_missing(option.get("kindergartens")):
                schools.append(str(option.get("kindergartens")).strip())
            infra_parts.append("; ".join(schools) if schools else "есть школы и детские сады")
        if _has_fact_kind(option, "yard"):
            infra_parts.append(str(option.get("yards") or "игровые и спортивные площадки / дворовая инфраструктура").strip())
        if _has_fact_kind(option, "clinic"):
            infra_parts.append("поликлиники, аптеки или сервисы рядом")
        if _has_fact_kind(option, "park"):
            infra_parts.append(str(option.get("parks") or "есть зелёные зоны для прогулок").strip())
    infra_paragraph = ""
    if infra_parts:
        infra_text = "; ".join(dict.fromkeys(infra_parts[:3]))
        infra_text = re.sub(r"\s*;\s*", ". ", infra_text)
        infra_text = re.sub(r"\.{2,}", ".", infra_text).strip(" .")
        infra_paragraph = "Для семьи и повседневной жизни здесь важна инфраструктура: " + infra_text + "."
    question = f"Хотите, позвать оператора проверить актуальные квартиры по {name}?"
    parts = [intro, benefit.strip()]
    if infra_paragraph and infra_paragraph not in parts:
        parts.append(infra_paragraph)
    parts.append(question)
    return "\n\n".join(parts)


def _render_stage_clarification(scenario: str) -> str:
    if scenario == "metro_access":
        return "Поняла, будем искать с удобным метро. Уточните, пожалуйста, район или бюджет — от этого зависит нормальная подборка."
    if scenario == "budget":
        return "Поняла по бюджету. Уточните, пожалуйста, район или комнатность — так я смогу подобрать варианты точнее."
    if scenario == "family":
        return "Поняла, ищем вариант для семьи. Уточните, пожалуйста, район или бюджет — тогда подберу более точные ЖК."
    if scenario == "investment":
        return "Поняла, смотрим под инвестицию. Уточните, пожалуйста, бюджет или район — так подборка будет точнее."
    return "Поняла задачу. Уточните, пожалуйста, район или бюджет — и я подберу более точные варианты."


_REASON_LAYER_FORBIDDEN_RE = re.compile(
    r"(?:доходност\w*|аренд\w*|ликвидност\w*|рост\s+цен\w*|окупаемост\w*|выгодн\w*|"
    r"инвестиционно\s+привлекательн\w*|лучший|идеальн\w*|максимальн\w*|"
    r"премиальн\w*|статусн\w*|статус\s+район\w*|садовое\s+кольцо|видов\w*|скидк\w*|ипотек\w*)",
    re.IGNORECASE,
)


def _strip_sentence_punct(value: Any) -> str:
    return str(value or "").strip().rstrip(".。!！?？")


def _ensure_sentence_period(value: str) -> str:
    text = str(value or "").strip()
    return text if not text or text.endswith((".", "!", "?")) else f"{text}."


def _model_response_time_sec(meta: dict[str, Any] | None, fallback_duration_ms: int | None = None) -> float | None:
    meta = meta or {}
    raw = meta.get("response_time") or meta.get("latency") or meta.get("duration_sec")
    try:
        if raw is not None:
            return round(float(raw), 2)
    except (TypeError, ValueError):
        pass
    if fallback_duration_ms is not None:
        return round(float(fallback_duration_ms) / 1000.0, 2)
    return None


def _append_model_stats_footer(
    response: str,
    *,
    state: dict[str, Any],
    chat_meta: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    reason_layer_meta: dict[str, Any] | None = None,
) -> str:
    """Test-lab footer: show which answer model produced the current reply."""
    if not SHOW_MODEL_STATS:
        return response
    reason_layer_meta = reason_layer_meta or {}
    if reason_layer_meta.get("applied"):
        model = str(reason_layer_meta.get("model") or state.get("chat_model") or CHAT_MODEL)
        meta = reason_layer_meta.get("meta") if isinstance(reason_layer_meta.get("meta"), dict) else {}
    else:
        meta = chat_meta or {}
        model = str(meta.get("model") or state.get("chat_model") or CHAT_MODEL)
    sec = _model_response_time_sec(meta, duration_ms)
    speed = f"{sec:.2f}с" if sec is not None else "н/д"
    return f"{response.rstrip()}\n\n🧪 Модель ответа: {model} · {speed}"


def _ensure_final_next_question(response: str, *, selected: bool = False) -> str:
    """Every useful bot answer must end with a next step question.

    LLM presenters sometimes finish selected ЖК cards with a factual period. UX contract
    requires the dialog to keep moving toward compare/check availability/operator.
    """
    text = str(response or "").strip()
    if not text:
        return text
    tail = "\n".join(text.splitlines()[-3:])
    if "?" in tail:
        return text
    question = (
        "Хотите, сравню его с другими вариантами или позвать оператора проверить актуальные квартиры?"
        if selected
        else "Какой вариант хотите рассмотреть подробнее, или позвать оператора проверить актуальные квартиры?"
    )
    return f"{text}\n\n{question}"


def _angle_mentions_higher_price(text: str) -> bool:
    return bool(re.search(r"\b(?:дороже|дорогой|дорогая|дорогие|высок\w*|верх\w*)\b", text, re.IGNORECASE))


def _angle_mentions_lower_price(text: str) -> bool:
    return bool(re.search(r"\b(?:ниже|низк\w*|бюджетн\w*|миним\w*)\b", text, re.IGNORECASE))


def _reason_layer_scenario(user_text: str, params: dict[str, Any] | None) -> str:
    """H001/reason-layer MVP: map current query/params to a safe presentation scenario."""
    p = params or {}
    purpose = str(p.get("purpose") or "").lower()
    text = str(user_text or "").lower().replace("ё", "е")
    if purpose in {"investment", "invest", "инвестиции", "инвест", "инвестиций"} or "инвест" in text:
        return "investment"
    if purpose == "family" or any(w in text for w in ("семь", "ребен", "дет")):
        return "family"
    if "метро" in text or "пешком" in text:
        return "metro_access"
    if any(w in text for w in ("до ", "бюджет", "дешев", "подешев", "недорог")):
        return "budget"
    if any(w in text for w in ("сдан", "готов", "ключ", "переезд", "засел")):
        return "fast_move"
    return "self_use"


def _reason_layer_lead_question(scenario: str) -> tuple[str, str]:
    if scenario == "investment":
        return "Подобрала три понятных варианта под инвестицию", "Какой ЖК посмотреть подробнее или позвать оператора проверить актуальные квартиры?"
    if scenario == "metro_access":
        return "Нашла несколько вариантов рядом с метро", "Какой ЖК посмотреть подробнее или позвать оператора проверить актуальные квартиры?"
    if scenario == "budget":
        return "Нашла несколько вариантов по бюджету", "Какой вариант посмотреть подробнее или позвать оператора проверить актуальные квартиры?"
    if scenario == "fast_move":
        return "Нашла варианты, где проще планировать переезд", "Какой ЖК посмотреть подробнее или позвать оператора проверить актуальные квартиры?"
    if scenario == "family":
        return "Подобрала несколько вариантов для семьи", "Какой ЖК посмотреть подробнее или позвать оператора проверить актуальные квартиры?"
    return "Нашла несколько понятных вариантов", "Какой ЖК посмотреть подробнее или позвать оператора проверить актуальные квартиры?"


def _reason_layer_fact_payload(option: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for key in ("location", "price", "price_min", "finishing", "ready", "area", "metro", "why_close"):
        value = option.get(key)
        if not _looks_missing(value):
            facts[key] = value
    return facts


def _ready_year(value: Any) -> int | None:
    text = str(value or "")
    m = re.search(r"\b(20\d{2})\b", text)
    return int(m.group(1)) if m else None


def _reason_layer_comparison_facts(options: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Compute simple across-options facts so model/renderer see the whole set."""
    subset = options[:3]
    out: dict[int, dict[str, Any]] = {i: {} for i in range(1, len(subset) + 1)}

    price_pairs = [
        (i, opt.get("price_min"))
        for i, opt in enumerate(subset, start=1)
        if isinstance(opt.get("price_min"), int) and opt.get("price_min") > 0
    ]
    if len(price_pairs) >= 2:
        sorted_prices = sorted(price_pairs, key=lambda x: x[1])
        out[sorted_prices[0][0]]["price_rank"] = "lowest"
        out[sorted_prices[-1][0]]["price_rank"] = "highest"
        for i, _price in sorted_prices[1:-1]:
            out[i]["price_rank"] = "middle"

    ready_pairs = [
        (i, _ready_year(opt.get("ready")))
        for i, opt in enumerate(subset, start=1)
    ]
    ready_pairs = [(i, year) for i, year in ready_pairs if year]
    if len(ready_pairs) >= 2:
        sorted_ready = sorted(ready_pairs, key=lambda x: x[1])
        earliest = sorted_ready[0][1]
        latest = sorted_ready[-1][1]
        for i, year in sorted_ready:
            if year == earliest:
                out[i]["ready_rank"] = "earliest"
            elif year == latest:
                out[i]["ready_rank"] = "latest"
            else:
                out[i]["ready_rank"] = "middle"

    metros = [not _looks_missing(opt.get("metro")) for opt in subset]
    if len(subset) >= 2 and all(metros):
        for i in out:
            out[i]["metro_rank"] = "all_have_metro"
    elif any(metros):
        for i, has_metro in enumerate(metros, start=1):
            out[i]["metro_rank"] = "has_metro" if has_metro else "no_metro_fact"

    finishings = [
        (i, str(opt.get("finishing") or "").lower())
        for i, opt in enumerate(subset, start=1)
    ]
    for i, finishing in finishings:
        if "отдел" in finishing and "без отдел" not in finishing:
            out[i]["finishing_rank"] = "has_finishing"
    return out


def _build_reason_layer_payload(
    *,
    user_text: str,
    scenario: str,
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    comparison = _reason_layer_comparison_facts(options)
    return {
        "scenario": scenario,
        "user_query": user_text,
        "task": "Return comparative angle + tone only; code will render final client text.",
        "allowed_fact_types": ["price", "price_min", "price_range", "metro", "finishing", "ready", "status", "delivered", "area", "location", "why_close"],
        "forbidden_claims": [
            "доходность", "аренда", "ликвидность", "рост цены", "окупаемость",
            "выгодная инвестиция", "инвестиционно привлекательный", "лучший", "идеальный",
            "школы", "парки", "дворы", "видовые квартиры", "скидки", "ипотека",
        ],
        "options": [
            {
                "idx": i,
                "name": o.get("name") or f"вариант {i}",
                "facts": _reason_layer_fact_payload(o),
                "comparison_facts": comparison.get(i, {}),
            }
            for i, o in enumerate(options[:3], start=1)
        ],
    }


def _validate_reason_layer_items(data: dict[str, Any], options_count: int) -> list[dict[str, str]]:
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != options_count:
        return []
    out: list[dict[str, str]] = []
    seen_idx: set[int] = set()
    seen_angles: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            return []
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            return []
        if idx < 1 or idx > options_count or idx in seen_idx:
            return []
        angle = str(item.get("angle") or "").strip()
        tone = str(item.get("tone") or "").strip()
        if not angle or not tone:
            return []
        if len(angle) > 120 or len(tone) > 120:
            return []
        if _REASON_LAYER_FORBIDDEN_RE.search(f"{angle} {tone}"):
            return []
        angle_key = _compact_option_text(angle)
        if angle_key in seen_angles:
            return []
        seen_idx.add(idx)
        seen_angles.add(angle_key)
        out.append({"idx": str(idx), "angle": angle, "tone": tone})
    return sorted(out, key=lambda x: int(x["idx"]))


def _format_option_line_for_reason_layer(idx: int, option: dict[str, Any]) -> str:
    parts: list[str] = []
    price = _format_price_value(option.get("price"), option.get("price_min")) if not _looks_missing(option.get("price")) else ""
    if price:
        parts.append(str(price).strip())
    if not _looks_missing(option.get("finishing")):
        finish = str(option.get("finishing"))
        parts.append(str("с отделкой" if "отдел" in finish.lower() and not finish.lower().startswith("с ") else finish).strip())
    if not _looks_missing(option.get("ready")):
        ready_fact = _client_ready_fact(option.get("ready"))
        if ready_fact:
            parts.append(ready_fact)
    suffix = f" — {', '.join(parts)}" if parts else ""
    return _ensure_sentence_period(f"{idx}. {option.get('name') or 'вариант'}{suffix}")


def _render_reason_from_angle(
    option: dict[str, Any],
    angle: str,
    tone: str,
    comparison: dict[str, Any] | None = None,
    used_reason_keys: set[str] | None = None,
) -> str:
    """Render final client phrase from short model angle+tone using only option facts."""
    comparison = comparison or {}
    used_reason_keys = used_reason_keys if used_reason_keys is not None else set()
    a = f"{angle} {tone}".lower().replace("ё", "е")
    metro = "" if _looks_missing(option.get("metro")) else str(option.get("metro"))
    location = "" if _looks_missing(option.get("location")) else _format_location_value(str(option.get("location")))
    ready = "" if _looks_missing(option.get("ready")) else str(option.get("ready"))
    finishing = "" if _looks_missing(option.get("finishing")) else str(option.get("finishing"))
    price = _format_price_value(option.get("price"), option.get("price_min")) if not _looks_missing(option.get("price")) else ""
    area = "" if _looks_missing(option.get("area")) else str(option.get("area"))

    def use(key: str, phrase: str) -> str | None:
        if key in used_reason_keys:
            return None
        used_reason_keys.add(key)
        return phrase

    price_rank = str(comparison.get("price_rank") or "")
    ready_rank = str(comparison.get("ready_rank") or "")
    metro_rank = str(comparison.get("metro_rank") or "")
    finishing_rank = str(comparison.get("finishing_rank") or "")

    # First priority: objective comparison across all visible options. This avoids
    # three identical "metro nearby" / "with finishing" reasons in one answer.
    if metro and metro_rank in {"all_have_metro", "has_metro"}:
        if ready and ready_rank == "earliest":
            phrase = use("metro_ready_earliest", "Метро рядом, и по сроку сдачи это самый близкий вариант в подборке.")
            if phrase:
                return phrase
        if price and price_rank == "highest":
            phrase = use("metro_price_highest", "Метро тоже рядом, но бюджет здесь выше — это другой ценовой уровень.")
            if phrase:
                return phrase
        if price and price_rank == "lowest":
            phrase = use("metro_price_lowest", "Метро рядом, а старт по цене самый низкий среди этих вариантов.")
            if phrase:
                return phrase
        if price and price_rank == "middle":
            phrase = use("metro_price_middle", "Метро рядом, а по цене это середина между соседними вариантами.")
            if phrase:
                return phrase

    # If model picked a non-price angle (family/self-use often does), respect it
    # before generic price ranking so every ЖК gets a meaningful, not just numeric,
    # description.
    if ready and any(w in a for w in ("срок", "сдач", "готов", "ключ")):
        if ready_rank == "earliest":
            phrase = use("ready_earliest", "По сроку сдачи это самый близкий вариант.")
            if phrase:
                return phrase
        if ready_rank != "latest":
            phrase = use("ready", f"По сроку видно {_strip_sentence_punct(ready)}, поэтому вариант проще сравнить по ожиданию.")
            if phrase:
                return phrase
    if area and any(w in a for w in ("площад", "простор", "диапазон")):
        phrase = use("area", f"По площади есть ориентир {area}, поэтому формат проще сравнить с другими вариантами.")
        if phrase:
            return phrase
    if location and any(w in a for w in ("локац", "район", "располож", "место")):
        phrase = use("location", f"Локация — {location}, это удобно сразу учитывать при семейном выборе.")
        if phrase:
            return phrase
    if finishing and any(w in a for w in ("отдел", "ремонт")):
        phrase = use("finishing", "С отделкой меньше ремонтных хлопот после покупки.")
        if phrase:
            return phrase

    if price and price_rank == "lowest":
        phrase = use("price_lowest", "Это самый доступный старт по цене среди этих вариантов.")
        if phrase:
            return phrase
    if price and price_rank == "highest":
        phrase = use("price_highest", "Бюджет здесь выше, поэтому этот вариант стоит сравнивать уже как более дорогой.")
        if phrase:
            return phrase
    if price and price_rank == "middle":
        phrase = use("price_middle", "По цене он между соседними вариантами — удобно держать для сравнения.")
        if phrase:
            return phrase

    if ready and ready_rank == "earliest":
        phrase = use("ready_earliest", "По сроку сдачи это самый близкий вариант.")
        if phrase:
            return phrase
    if finishing and finishing_rank == "has_finishing":
        phrase = use("finishing", "С отделкой меньше ремонтных хлопот после покупки.")
        if phrase:
            return phrase

    if any(w in a for w in ("метро", "станци", "пешком")) and metro:
        metro_clean = _strip_sentence_punct(metro)
        price_clean = str(price or "").strip()
        ready_clean = _strip_sentence_punct(ready)
        if _angle_mentions_higher_price(a) and price_clean:
            return _ensure_sentence_period(f"До метро около минуты, но бюджет здесь выше: {price_clean}")
        if _angle_mentions_lower_price(a) and price_clean:
            return _ensure_sentence_period(f"Метро рядом, при этом старт по цене ниже: {price_clean}")
        if any(w in a for w in ("сред", "между", "серед")) and price_clean:
            return _ensure_sentence_period(f"Метро рядом, а по бюджету это промежуточный вариант: {price_clean}")
        if "срок" in a and ready_clean:
            return f"Метро рядом: {metro_clean}, а по сроку видно {ready_clean}."
        if "срок" in a and ready:
            return f"Метро рядом: {metro_clean}, а по сроку видно {ready_clean}."
        return f"Метро рядом: {metro_clean} — удобно для ежедневных поездок."
    if any(w in a for w in ("срок", "сдач", "готов", "ключ")) and ready:
        return f"По сроку видно {_strip_sentence_punct(ready)}, поэтому вариант проще сравнить по ожиданию."
    if any(w in a for w in ("отдел", "ремонт")) and finishing:
        return "С отделкой меньше ремонтных хлопот после покупки."
    if any(w in a for w in ("низк", "доступ", "цен", "бюджет", "вход", "дорог", "сегмент")) and price:
        price_clean = str(price or "").strip()
        if _angle_mentions_higher_price(a):
            return _ensure_sentence_period(f"Бюджет здесь выше: {price_clean}, это более дорогой вариант для сравнения")
        if any(w in a for w in ("сред", "между", "серед")):
            return _ensure_sentence_period(f"По цене это промежуточный вариант: {price_clean}")
        return _ensure_sentence_period(f"Здесь понятный старт по цене: {price_clean}, удобно сравнить с другими ЖК")
    if area:
        return f"По площади есть ориентир {area}, поэтому формат проще сравнить с другими вариантами."
    return "Этот вариант удобно держать в сравнении с остальными по найденным фактам."


def _format_options_summary_with_reasons(
    options: list[dict[str, Any]],
    lead: str,
    question: str,
    reason_items: list[dict[str, str]],
) -> str:
    reasons_by_idx = {int(item["idx"]): item for item in reason_items}
    comparison = _reason_layer_comparison_facts(options)
    used_reason_keys: set[str] = set()
    chunks: list[str] = []
    for idx, option in enumerate(options[:3], start=1):
        line = _format_option_line_for_reason_layer(idx, option)
        item = reasons_by_idx.get(idx)
        reason = _render_reason_from_angle(
            option,
            item.get("angle", "") if item else "",
            item.get("tone", "") if item else "",
            comparison=comparison.get(idx, {}),
            used_reason_keys=used_reason_keys,
        )
        chunks.append(f"{line}\n   {reason}")
    return _format_numbered_list_spacing(f"{lead}.\n" + "\n\n".join(chunks) + f"\n\n{question}")


async def _maybe_apply_reason_layer(
    client: OvermindClient,
    *,
    user_text: str,
    state: dict[str, Any],
    fallback_response: str,
) -> tuple[str, dict[str, Any]]:
    """Guarded first-list rewrite. Fallbacks silently to current response on any issue."""
    if not REASON_LAYER_ENABLED:
        return fallback_response, {"enabled": False}
    if state.get("selected_option"):
        return fallback_response, {"enabled": True, "skipped": "selected_option"}
    options = list(state.get("last_options") or [])[:3]
    if len(options) < 2:
        return fallback_response, {"enabled": True, "skipped": "not_enough_options"}
    scenario = _reason_layer_scenario(user_text, state.get("params") or {})
    payload = _build_reason_layer_payload(user_text=user_text, scenario=scenario, options=options)
    try:
        model_chain = [m for m in (REASON_LAYER_MODEL, REASON_LAYER_FALLBACK_MODEL) if m]
        seen_models: set[str] = set()
        last_meta: dict[str, Any] = {}
        for model in model_chain:
            if model in seen_models:
                continue
            seen_models.add(model)
            data, meta = await client.comparative_reason_angles(payload, model=model)
            items = _validate_reason_layer_items(data, len(options))
            if not items:
                LOGGER.warning("reason layer invalid output: model=%s data=%s meta=%s", model, _safe_json_preview(data), _safe_json_preview(meta))
                last_meta = meta
                continue
            lead, question = _reason_layer_lead_question(scenario)
            response = _format_options_summary_with_reasons(options, lead, question, items)
            return response, {"enabled": True, "applied": True, "scenario": scenario, "model": model, "items": items, "meta": meta}
        return fallback_response, {"enabled": True, "skipped": "invalid_model_output", "meta": last_meta}
    except Exception:
        LOGGER.exception("reason layer failed")
        return fallback_response, {"enabled": True, "skipped": "exception"}


def _option_select_rows(options: list[dict[str, Any]], max_count: int = 3) -> list[list[dict]]:
    return []


# H021: кнопки бюджета генерируются из реальных цен в last_options.
# Если кликабельная кнопка обещает цену ниже реальной — это обман по UX.
_BUDGET_THRESHOLDS_MLN: list[int] = [3, 5, 7, 8, 10, 12, 15, 20]


def _budget_buttons_from_options(state: dict, max_count: int = 3) -> list[dict]:
    """H021: бюджетные кнопки опираются на min(price_min) в last_options.
    Если результат пуст — fallback на безопасный дефолт [5, 8, 12] (как раньше)."""
    price_mins = [
        opt.get("price_min")
        for opt in state.get("last_options", [])
        if opt.get("price_min")
    ]
    if not price_mins:
        return [
            {"text": "до 5 млн", "callback_data": "budget:5m"},
            {"text": "до 8 млн", "callback_data": "budget:8m"},
            {"text": "до 12 млн", "callback_data": "budget:12m"},
        ][:max_count]
    floor_mln = min(price_mins) / 1_000_000
    candidates = [t for t in _BUDGET_THRESHOLDS_MLN if t >= floor_mln][:max_count]
    if not candidates:
        candidates = [15]
    return [
        {"text": f"до {t} млн", "callback_data": f"budget:{t}m"}
        for t in candidates
    ]


_BUTTON_TECH_WORDS = re.compile(
    r"\b(?:mcp|json|facts|near|search_response|last_options|prompt|model|traceback|openrouter|overmind)\b",
    re.IGNORECASE,
)


def _option_by_index(state: dict, idx: int) -> dict[str, Any] | None:
    options = state.get("last_options") or []
    if 1 <= idx <= len(options):
        return options[idx - 1]
    return None


def _operator_button_allowed(state: dict, response_text: str) -> bool:
    """Операторская кнопка не должна появляться в первом полезном ответе с вариантами."""
    result = state.get("last_result") or {}
    has_results = bool(result.get("found"))
    has_selected = bool(state.get("selected_option"))
    response_l = response_text.lower()
    response_offers_operator = "оператор" in response_l or "номер" in response_l
    return (not has_results) or has_selected or response_offers_operator


def _callback_from_contract_button(button: dict[str, Any], state: dict, response_text: str) -> str | None:
    action = str(button.get("action") or "").strip()
    value = button.get("value") if isinstance(button.get("value"), dict) else {}

    if action in ("details", "select_option"):
        idx_raw = value.get("option_index") or button.get("option_index")
        if idx_raw is None and len(state.get("last_options") or []) == 1:
            idx_raw = 1
        try:
            idx = int(idx_raw)
        except (TypeError, ValueError):
            return None
        if not _option_by_index(state, idx):
            return None
        return f"action:details:{idx}" if action == "details" else f"option:{idx}"

    if action == "filter":
        field = str(value.get("field") or "").strip()
        raw = value.get("value")
        if field == "rooms":
            if raw in (0, "0", "s", "studio", "студия"):
                return "rooms:s"
            if str(raw) in ("1", "2", "3"):
                return f"rooms:{raw}"
            if str(raw).lower() in ("3plus", "4", "больше 2 комнат"):
                return "rooms:3"
        if field in ("max_price", "budget"):
            if raw is None:
                return "budget:none"
            try:
                mln = round(float(raw) / 1_000_000)
            except (TypeError, ValueError):
                return None
            return f"budget:{mln}m" if mln > 0 else None
        if field in ("finish", "has_renovation", "renovation"):
            return "renovation:yes" if bool(raw) else "renovation:no"
        if field == "district" and raw:
            district = str(raw).strip()
            if district in ("msk", "newmsk", "mo", "any"):
                return f"district:{district}"

    if action == "show_near":
        return "action:show_near"
    if action == "expand_search":
        return "action:expand_district"
    if action == "operator" and _operator_button_allowed(state, response_text):
        return "action:operator"
    return None


def _contract_buttons_to_rows(buttons: Any, state: dict, response_text: str) -> list[list[dict]]:
    """Inline-кнопки отключены: даже валидный buttons[] из модели не отправляем."""
    return []


def _limit_button_rows(rows: list[list[dict]], max_buttons: int = 4) -> list[list[dict]]:
    """PRODUCT_TZ: максимум 4 кнопки в одном ответе, даже для fallback."""
    limited: list[list[dict]] = []
    count = 0
    for row in rows:
        clean_row: list[dict] = []
        for button in row:
            if count >= max_buttons:
                break
            text = str(button.get("text") or "").strip()
            callback = str(button.get("callback_data") or "").strip()
            if not text or not callback or _BUTTON_TECH_WORDS.search(text):
                continue
            clean_row.append({"text": text, "callback_data": callback})
            count += 1
        if clean_row:
            limited.append(clean_row)
        if count >= max_buttons:
            break
    return limited


def _markup_from_chat_buttons(chat_meta: dict, state: dict, response_text: str, scenario: str) -> list[list[dict]]:
    return []


def _pick_quick_actions(state: dict, scenario: str) -> list[list[dict]]:
    """H013: вернуть inline-клавиатуру по сценарию.
    Сценарии: A-found-some, B-found-many, C-narrow-empty, D-wide-empty, E-geo-mismatch, F-non-realty, G-first-step.
    H021: бюджетные кнопки генерируются из min(price_min) в last_options, не хардкод."""
    p = state.get("params", {})
    asked = set(state.get("asked_questions", []))
    rows: list[list[dict]] = []

    if scenario == "G-first-step":
        # первый запрос без параметров — помоги структурировать
        rows.append([
            {"text": "Студия", "callback_data": "rooms:s"},
            {"text": "1-к", "callback_data": "rooms:1"},
            {"text": "2-к", "callback_data": "rooms:2"},
            {"text": "Больше 2 комнат", "callback_data": "rooms:3"},
        ])
    elif scenario == "A-found-some":
        # нашли 1-2 ЖК, спросить бюджет/комнаты если не указаны
        if "max_price" not in p and "budget" not in asked:
            rows.append(_budget_buttons_from_options(state, max_count=3))
        if "rooms" not in p and "rooms_q" not in asked:
            rows.append([
                {"text": "Студия", "callback_data": "rooms:s"},
                {"text": "1-к", "callback_data": "rooms:1"},
                {"text": "2-к", "callback_data": "rooms:2"},
            ])
    elif scenario == "C-narrow-empty":
        # узкий пустой: показать near или оператора
        rows.append([
            {"text": "Расширить бюджет", "callback_data": "budget:none"},
            {"text": "Смотреть МО", "callback_data": "district:mo"},
            {"text": "Похожие варианты", "callback_data": "action:show_near"},
            {"text": "📞 Оператор", "callback_data": "action:operator"},
        ])
    elif scenario == "D-wide-empty":
        # широкий пустой (СПб и т.п.)
        rows.append([
            {"text": "Москва", "callback_data": "district:msk"},
            {"text": "Новая Москва", "callback_data": "district:newmsk"},
            {"text": "Подмосковье", "callback_data": "district:mo"},
        ])
        rows.append([
            {"text": "📞 Оператор", "callback_data": "action:operator"},
        ])
    elif scenario == "E-geo-mismatch":
        # запрос вне зоны (СПб и т.п.) — уточнить регион
        rows.append([
            {"text": "Москва", "callback_data": "district:msk"},
            {"text": "Подмосковье", "callback_data": "district:mo"},
        ])
        rows.append([
            {"text": "📞 Оператор", "callback_data": "action:operator"},
        ])
    return rows


def _infer_scenario(state: dict, search_meta: dict) -> str:
    """H013: выбрать сценарий по state + результату search."""
    p = state.get("params", {})
    asked = set(state.get("asked_questions", []))
    # гео-мисматч (вне Москвы/МО)
    if state.get("last_result", {}).get("geo_mismatch"):
        return "E-geo-mismatch"
    # первый шаг (ничего не спрошено)
    if not p and not asked:
        return "G-first-step"
    # узкий пустой: есть параметры, но ничего не нашли
    if state.get("last_result", {}).get("found") is False and (p.get("max_price") or p.get("rooms") or p.get("has_renovation") is not None):
        return "C-narrow-empty"
    # широкий пустой: район задан, ничего не нашли
    if state.get("last_result", {}).get("found") is False and p.get("district") in ("msk", "newmsk", "mo"):
        return "D-wide-empty"
    # нашли 1-2 ЖК
    cnt = state.get("last_result", {}).get("exact_count", 0) + state.get("last_result", {}).get("near_count", 0)
    if 0 < cnt <= 2:
        return "A-found-some"
    return ""


def _refresh_search_state(state: dict, search_meta: dict) -> None:
    """Обновляет память по последнему поиску: raw search_response, options, counters."""
    search_text = search_meta.get("_response_text") or ""
    search_resp = _json_from_text(search_text)
    state["last_search_response"] = search_resp if isinstance(search_resp, dict) else {}
    state["last_options"] = _filter_rejected_options(_extract_options(search_text), state)
    facts = search_resp.get("facts", []) if isinstance(search_resp, dict) else []
    near = search_resp.get("near", []) if isinstance(search_resp, dict) else []
    state["last_result"] = {
        "found": bool(facts) or bool(near),
        "exact_count": len(facts) if isinstance(facts, list) else 0,
        "near_count": len(near) if isinstance(near, list) else 0,
        "geo_mismatch": bool(search_resp.get("missing") and not facts and not near and state.get("params", {}).get("district") in (None,)),
    }


def _option_enrichment_key(option: dict[str, Any], scenario: str) -> str:
    name = _compact_name_key(option.get("name"))
    link = _compact_name_key(option.get("link"))
    base = link or name
    return f"{base}::{scenario or 'self_use'}" if base else ""


def _build_option_enrichment_query(option: dict[str, Any], scenario: str) -> str:
    name = _display_complex_name(option.get("name") or "")
    location = _format_location_value(option.get("location")) if not _looks_missing(option.get("location")) else ""
    scenario_label = {
        "family": "семья с детьми",
        "investment": "инвестиционный выбор без обещаний доходности",
        "metro_access": "важна транспортная доступность и метро",
        "budget": "важен бюджет и честные компромиссы",
        "fast_move": "важно быстрее переехать",
    }.get(scenario, "покупка для себя")
    common_fields = [
        "район / локация",
        "цены и минимальная цена",
        "площади",
        "типы квартир / комнатность",
        "отделка",
        "готовность / сдан ли дом",
        "метро, расстояние до метро и транспорт",
        "застройщик",
    ]
    scenario_fields = {
        "family": [
            "школы",
            "детские сады",
            "парки, лес, зелёные зоны, набережные",
            "поликлиники и аптеки",
            "дворы без машин",
            "игровые и спортивные площадки",
            "магазины и сервисы на первых этажах",
        ],
        "investment": [
            "транспортная доступность",
            "готовность",
            "отделка",
            "ценовой диапазон без прогнозов доходности",
        ],
        "metro_access": ["метро", "пешая доступность", "транспорт", "цены рядом с метро"],
        "budget": ["бюджетные ограничения", "почему вариант близок", "честные отличия и компромиссы"],
        "fast_move": ["готовые корпуса", "выдача ключей", "отделка", "что помогает быстрее переехать"],
    }.get(scenario, ["инфраструктура", "магазины", "сервисы", "парки", "клиники"])
    fields = common_fields + scenario_fields
    loc_line = f"\nЛокация из текущей карточки: {location}." if location else ""
    return (
        f"Раскрой подробно {name} для сценария: {scenario_label}.{loc_line}\n\n"
        "Нужны только реальные MCP-факты по полям:\n"
        + "\n".join(f"- {field};" for field in fields)
        + "\n\nВерни JSON facts/near/missing/params. "
        "Не выдумывай. Если поля нет — не добавляй. "
        "Для facts[0] скопируй все доступные поля MCP, включая infrastructure/infrastructure_family."
    )


def _merge_option_cards(base: dict[str, Any], enriched: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in (enriched or {}).items():
        if key == "raw":
            continue
        if _looks_missing(value):
            continue
        if key in {"price", "price_range"}:
            base_price = str(merged.get("price") or "")
            if key == "price_range" and merged.get("price_range"):
                base_price = str(merged.get("price_range") or base_price)
            new_price = str(value or "")
            base_has_range = bool(re.search(r"\bдо\b|[-–]", base_price.lower()))
            new_has_range = bool(re.search(r"\bдо\b|[-–]", new_price.lower()))
            if base_has_range and not new_has_range:
                continue
        merged[key] = value
    raw = dict((base or {}).get("raw") or {})
    raw.update(dict((enriched or {}).get("raw") or {}))
    if raw:
        merged["raw"] = raw
    return merged


def _best_enriched_option(parsed: dict[str, Any], base_option: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(parsed, dict):
        return None
    raw = json.dumps(parsed, ensure_ascii=False)
    options = _extract_options(raw)
    if not options:
        return None
    base_key = _compact_name_key(base_option.get("name"))
    for option in options:
        if base_key and base_key in _compact_name_key(option.get("name")):
            return option
    return options[0]


async def _enrich_option(
    client: OvermindClient,
    option: dict[str, Any],
    scenario: str,
    *,
    timeout: int = OPTION_ENRICHMENT_TIMEOUT,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not OPTION_ENRICHMENT_ENABLED or not isinstance(option, dict) or not option.get("name"):
        return None, {"enabled": OPTION_ENRICHMENT_ENABLED, "applied": False}
    query = _build_option_enrichment_query(option, scenario)
    try:
        parsed, meta = await client.enrich_option_search(query, timeout=timeout)
        enriched = _best_enriched_option(parsed, option)
        if not enriched:
            return None, {"enabled": True, "applied": False, "skipped": "no_enriched_option", "meta": meta}
        return _merge_option_cards(option, enriched), {"enabled": True, "applied": True, "meta": meta}
    except asyncio.CancelledError:
        raise
    except Exception:
        LOGGER.exception("option enrichment failed")
        return None, {"enabled": True, "applied": False, "skipped": "exception"}


async def _prefetch_options_enrichment(
    client: OvermindClient,
    state: dict[str, Any],
    options: list[dict[str, Any]],
    scenario: str,
) -> None:
    if not OPTION_ENRICHMENT_ENABLED or not options:
        return
    cache = state.setdefault("enriched_options", {})
    tasks: list[tuple[str, dict[str, Any], asyncio.Task]] = []
    for option in options[:3]:
        key = _option_enrichment_key(option, scenario)
        if not key or key in cache:
            continue
        task = asyncio.create_task(_enrich_option(client, option, scenario))
        tasks.append((key, option, task))
    for key, _option, task in tasks:
        try:
            enriched, meta = await task
            if enriched:
                cache[key] = {"option": enriched, "meta": meta, "ts": datetime.now(timezone.utc).isoformat()}
                LOGGER.info("option enrichment cached: key=%s name=%s", key, enriched.get("name"))
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("option enrichment prefetch failed: key=%s", key)


async def _get_or_fetch_enriched_option(
    client: OvermindClient,
    state: dict[str, Any],
    option: dict[str, Any],
    scenario: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = _option_enrichment_key(option, scenario)
    cache = state.setdefault("enriched_options", {})
    cached = cache.get(key) if key else None
    if isinstance(cached, dict) and isinstance(cached.get("option"), dict):
        return cached["option"], {"enabled": True, "applied": True, "source": "cache", "key": key, "meta": cached.get("meta") or {}}
    try:
        enriched, meta = await asyncio.wait_for(
            _enrich_option(client, option, scenario, timeout=OPTION_ENRICHMENT_TIMEOUT),
            timeout=max(0.5, OPTION_ENRICHMENT_SELECT_WAIT),
        )
        if enriched:
            if key:
                cache[key] = {"option": enriched, "meta": meta, "ts": datetime.now(timezone.utc).isoformat()}
            return enriched, {**meta, "source": "sync_short_wait", "key": key}
        return option, {**meta, "source": "fallback_short_card", "key": key}
    except asyncio.TimeoutError:
        return option, {"enabled": True, "applied": False, "skipped": "select_wait_timeout", "source": "fallback_short_card", "key": key}


# ── Experiment Loop logging ─────────────────────────────────


_DIALOG_SESSIONS: dict[int, dict[str, Any]] = {}


def _new_dialog_session(uid: int) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return {"dialog_id": f"d-{stamp}-{uid}-{uuid4().hex[:6]}", "turn_id": 0}


def _dialog_session(uid: int, *, reset: bool = False) -> dict[str, Any]:
    if reset or uid not in _DIALOG_SESSIONS:
        _DIALOG_SESSIONS[uid] = _new_dialog_session(uid)
    return _DIALOG_SESSIONS[uid]


def _compact_trace_value(value: Any, limit: int = 1400) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str, indent=2)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _build_dialog_trace(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "input": {
            "user_text": event.get("user_text", ""),
            "params_before": event.get("params_before", {}),
        },
        "internal": {
            "dialog_intent": event.get("dialog_intent"),
            "dialog_plan": event.get("dialog_plan"),
            "search_response": _compact_trace_value(event.get("search_response", "")),
            "state_after": event.get("state_after"),
        },
        "output": {
            "response_text": event.get("response_text", ""),
            "buttons": event.get("buttons", []),
            "cost": event.get("cost", {}),
        },
    }


def _append_dialog_markdown(event: dict[str, Any]) -> None:
    dialog_id = event.get("dialog_id") or "-"
    turn_id = event.get("turn_id") or "-"
    ts = event.get("ts") or ""
    date_stamp = str(ts)[:10] if ts else datetime.now(timezone.utc).date().isoformat()
    path = LOGS_DIR / f"dialogs-{date_stamp}.md"
    trace = event.get("trace") or _build_dialog_trace(event)
    lines = [
        f"## {ts} · {dialog_id} · turn {turn_id}",
        "",
        "### Вход",
        f"- user: {event.get('user_text', '')}",
        f"- params_before: `{_compact_trace_value(event.get('params_before', {}), 800)}`",
        "",
        "### Внутри",
        f"- dialog_intent: `{event.get('dialog_intent', '')}`",
        f"- dialog_plan: `{_compact_trace_value(trace.get('internal', {}).get('dialog_plan'), 900)}`",
        "- search_response:",
        "```json",
        _compact_trace_value(trace.get('internal', {}).get('search_response'), 1200),
        "```",
        f"- state_after: `{_compact_trace_value(event.get('state_after'), 900)}`",
        "",
        "### Ответ",
        event.get("response_text", ""),
        "",
        f"- buttons: `{_compact_trace_value(event.get('buttons', []), 800)}`",
        f"- cost: `{_compact_trace_value(event.get('cost', {}), 800)}`",
        "",
    ]
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:  # pragma: no cover - best effort companion log
        LOGGER.warning("Failed to write dialog markdown log: %s", e)


def _dialog_log_dates(days: int = 3) -> list[str]:
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(max(1, days))]


def _load_user_dialog_history(uid: int, *, limit: int = 5, days: int = 3) -> list[dict[str, Any]]:
    """Read recent user_message events for one Telegram user from dialogs-jsonl logs."""
    events: list[dict[str, Any]] = []
    for date_stamp in _dialog_log_dates(days):
        path = LOGS_DIR / f"dialogs-{date_stamp}.jsonl"
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception as e:  # pragma: no cover - history must be best effort
            LOGGER.warning("Failed to read dialog history %s: %s", path, e)
            continue
        for raw in reversed(lines):
            if len(events) >= limit:
                break
            try:
                event = json.loads(raw)
            except Exception:
                continue
            if event.get("kind") != "user_message":
                continue
            try:
                event_uid = int(event.get("uid") or 0)
            except Exception:
                event_uid = 0
            if event_uid != uid:
                continue
            events.append(event)
        if len(events) >= limit:
            break
    return list(reversed(events))


def _history_search_preview(event: dict[str, Any], limit: int = 900) -> str:
    trace = event.get("trace") if isinstance(event.get("trace"), dict) else {}
    internal = trace.get("internal") if isinstance(trace.get("internal"), dict) else {}
    raw = event.get("search_response") or internal.get("search_response") or ""
    return _compact_trace_value(raw, limit)


def _format_history_event(event: dict[str, Any], idx: int) -> str:
    trace = event.get("trace") if isinstance(event.get("trace"), dict) else {}
    internal = trace.get("internal") if isinstance(trace.get("internal"), dict) else {}
    output = trace.get("output") if isinstance(trace.get("output"), dict) else {}
    input_trace = trace.get("input") if isinstance(trace.get("input"), dict) else {}
    user_text = str(event.get("user_text") or input_trace.get("user_text") or "").strip()
    response_text = str(event.get("response_text") or output.get("response_text") or "").strip()
    dialog_plan = event.get("dialog_plan") or internal.get("dialog_plan") or ""
    search_preview = _history_search_preview(event)
    cost = event.get("cost") or output.get("cost") or {}
    buttons = event.get("buttons") or output.get("buttons") or []
    parts = [
        f"#{idx} · {event.get('ts', '-')}",
        f"Вы: {_compact_trace_value(user_text, 500)}",
        f"Бот: {_compact_trace_value(response_text, 900)}",
        f"intent: {_compact_trace_value(event.get('dialog_intent') or internal.get('dialog_intent') or '-', 500)}",
    ]
    if dialog_plan:
        parts.append(f"plan: {_compact_trace_value(dialog_plan, 700)}")
    if search_preview:
        parts.append("MCP/search_response:")
        parts.append(_compact_trace_value(search_preview, 900))
    if buttons:
        parts.append(f"buttons: {_compact_trace_value(buttons, 400)}")
    if cost:
        parts.append(f"cost: {_compact_trace_value(cost, 300)}")
    return "\n".join(parts)


def _format_history_response(uid: int, *, limit: int = 5) -> str:
    events = _load_user_dialog_history(uid, limit=limit)
    if not events:
        return (
            "Истории пока нет. Напишите обычный запрос боту, и после ответа здесь появятся "
            "последние сообщения, ответ Ирины и MCP/search trace."
        )
    blocks = [_format_history_event(event, i) for i, event in enumerate(events, start=1)]
    return "🧾 История последних ответов бота\n\n" + "\n\n---\n\n".join(blocks)


def _telegram_chunks(text: str, limit: int = 3800) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n\n---\n\n", 0, limit)
        if cut < 500:
            cut = limit
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest.strip():
        chunks.append(rest.strip())
    return chunks


def _log_event(event: dict[str, Any]) -> None:
    """Append one JSONL line to logs/dialogs-YYYY-MM-DD.jsonl.

    Schema see docs/EXPERIMENTS.md.
    Best-effort: never raise into the bot's request path.
    """
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = LOGS_DIR / f"dialogs-{datetime.now(timezone.utc).date().isoformat()}.jsonl"
        uid = int(event.get("uid") or 0)
        kind = event.get("kind")
        if kind == "command" and event.get("command") in {"/start", "/reset"}:
            session = _dialog_session(uid, reset=True)
        else:
            session = _dialog_session(uid)
        if kind == "user_message":
            session["turn_id"] = int(session.get("turn_id") or 0) + 1
            event.setdefault("dialog_id", session["dialog_id"])
            event.setdefault("turn_id", session["turn_id"])
            event.setdefault("trace", _build_dialog_trace(event))
        event.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
        event.setdefault("h_id", ACTIVE_H_ID)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        if kind == "user_message":
            _append_dialog_markdown(event)
    except Exception as e:  # pragma: no cover - logging must never break the bot
        LOGGER.warning("Failed to write dialog log: %s", e)


# ── Telegram Bot ────────────────────────────────────────────


def build_menu_markup(models: list[str], current: str, mcp_on: bool) -> list[list[dict]]:
    kb: list[list[dict]] = []
    for m in models:
        name = m.split("/")[-1]
        marker = " ✅" if m == current else ""
        kb.append([{"text": f"{name}{marker}", "callback_data": f"chat_model:{m}"}])
    mcp_status = "✅ Вкл" if mcp_on else "❌ Выкл"
    kb.append([{"text": f"MCP: {mcp_status}", "callback_data": "toggle_mcp"}])
    return kb


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("[ERROR] TELEGRAM_BOT_TOKEN не задан")
        sys.exit(1)
    if not OVERMIND_TOKEN:
        print("[ERROR] OVERMIND_TOKEN не задан")
        sys.exit(1)
    if not OPENROUTER_API_KEY:
        print("[ERROR] OPENROUTER_API_KEY не задан")
        sys.exit(1)

    try:
        from telegram import ReplyKeyboardRemove, Update
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )
    except ImportError:
        print("[ERROR] Установите python-telegram-bot: pip install python-telegram-bot>=21")
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8"),
        ],
    )

    client = OvermindClient()
    user_state: dict[int, dict] = {}

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = update.effective_user.id if update.effective_user else 0
        # H023: /start = новый подбор. Сохраняем модели/MCP, но сбрасываем params,
        # last_options и asked_questions, чтобы не протекал старый max_price.
        state = _reset_dialog_state_preserve_settings(user_state.get(uid, _default_state()))
        user_state[uid] = state
        search_model = state["search_model"]
        chat_model = state["chat_model"]
        mcp = state["mcp"]
        _log_event({"kind": "command", "uid": uid, "command": "/start",
                    "search_model": search_model, "chat_model": chat_model, "mcp": mcp})
        await update.message.reply_text(
            f"🤖 <b>Тестовый чат-бот Novostroy AI (nmbot)</b>\n\n"
            f"Отвечаю на вопросы о новостройках через:\n"
            f"• <b>Поиск:</b> {search_model}\n"
            f"• <b>Общение:</b> {chat_model}\n"
            f"• <b>MCP novostroym:</b> {'✅ Вкл' if mcp else '❌ Выкл'}\n\n"
            f"Команды:\n"
            f"• /model — сменить модель\n"
            f"• /mcp — вкл/выкл MCP\n"
            f"• /history — последние ответы и MCP/search trace\n"
            f"• /hisotry — то же самое, алиас с опечаткой\n"
            f"• /reset — сбросить настройки\n"
            f"• /status — текущие настройки",
            parse_mode="HTML",
        )
        # H014: приветствие от Ирины — отдельным сообщением, чтобы не смешивать с системным блоком
        # H018: оборачиваем цены и имена ЖК в <b> через postprocessor
        await update.message.reply_text(
            _to_html(
                "Привет! Я Ирина, помогу подобрать квартиру в новостройке.\n\n"
                "Могу искать по району, бюджету, количеству комнат и отделке. "
                "Например:\n"
                "• «двушка с отделкой в Солнцево до 15 млн»\n"
                "• «квартира в Котельниках»\n"
                "• «студия в пределах МКАД»\n\n"
                "Напишите, что ищете, а я покажу подходящие варианты и помогу выбрать следующий шаг."
            ),
            parse_mode="HTML",
        )

    async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = update.effective_user.id if update.effective_user else 0
        state = user_state.setdefault(uid, _default_state())
        _log_event({"kind": "command", "uid": uid, "command": "/status",
                    "search_model": state["search_model"], "chat_model": state["chat_model"], "mcp": state["mcp"]})
        await update.message.reply_text(
            f"📋 <b>Текущие настройки</b>\n"
            f"• Поиск: {state['search_model']}\n"
            f"• Общение: {state['chat_model']}\n"
            f"• MCP: {'✅ Вкл' if state['mcp'] else '❌ Выкл'}\n"
            f"• Движок: gateway-agent → OpenRouter",
            parse_mode="HTML",
        )

    async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = update.effective_user.id if update.effective_user else 0
        state = user_state.setdefault(uid, _default_state())
        _log_event({"kind": "command", "uid": uid, "command": "/model",
                    "search_model": state["search_model"]})
        kb = build_menu_markup(AVAILABLE_MODELS, state["chat_model"], state["mcp"])
        await update.message.reply_text(
            "Выбери модель ответа. Модель поиска не меняется:",
            reply_markup={"inline_keyboard": kb},
        )

    async def mcp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = update.effective_user.id if update.effective_user else 0
        state = user_state.setdefault(uid, _default_state())
        state["mcp"] = not state["mcp"]
        _log_event({"kind": "command", "uid": uid, "command": "/mcp",
                    "mcp_new": state["mcp"]})
        await update.message.reply_text(
            f"MCP novostroym: {'✅ Включён' if state['mcp'] else '❌ Выключен'}"
        )

    async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = update.effective_user.id if update.effective_user else 0
        user_state[uid] = _default_state()
        _log_event({"kind": "command", "uid": uid, "command": "/reset"})
        await update.message.reply_text(
            f"✅ Настройки сброшены. Поиск: {SEARCH_MODEL}, общение: {CHAT_MODEL}, MCP: включён"
        )

    async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = update.effective_user.id if update.effective_user else 0
        command_text = (update.message.text.split()[0] if update.message and update.message.text else "/history")
        limit = 5
        if context.args:
            try:
                limit = max(1, min(10, int(context.args[0])))
            except Exception:
                limit = 5
        _log_event({"kind": "command", "uid": uid, "command": command_text, "limit": limit})
        history_text = _format_history_response(uid, limit=limit)
        for chunk in _telegram_chunks(history_text):
            await update.message.reply_text(_to_html(chunk), parse_mode="HTML")

    async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer("Обрабатываю...")
        if not query.data:
            return

        uid = update.effective_user.id if update.effective_user else 0
        state = user_state.setdefault(uid, _default_state())
        state_before_callback = _dialog_state_preview(state)
        button_text = _callback_button_text(state.get("last_buttons"), query.data)

        if query.data.startswith("action:details:"):
            raw_idx = query.data.rsplit(":", 1)[-1]
            try:
                idx = int(raw_idx)
            except ValueError:
                return
            option = _option_by_index(state, idx)
            if not option:
                await query.edit_message_text("Этот вариант уже не вижу в текущем списке. Напишите запрос ещё раз — обновлю подборку.")
                return
            state["selected_option"] = option
            await query.edit_message_text("Раскрываю подробнее...")
            try:
                response, chat_meta = await client.explain_known_option(
                    option=option,
                    client_request="Клиент нажал кнопку «Да, подробнее» по выбранному ЖК.",
                    chat_model=state["chat_model"],
                )
                response = _prepare_response_text(response)
                response = _strip_unsupported_complex_claims(response, option)
                response = await _maybe_style_text(
                    client,
                    response,
                    intent="details_known_option",
                    scene="selected_option_details",
                    context=str(option.get("name") or ""),
                )
                kb_rows = _markup_from_chat_buttons(chat_meta, state, response, "selected-option-details")
                markup = {"inline_keyboard": kb_rows} if kb_rows else None
            except Exception:
                LOGGER.exception("details callback chat failed")
                response = _prepare_response_text(_format_option_response(option, state.get("params", {}).get("purpose")))
                kb_rows = _selected_option_rows(idx)
                markup = None
            _log_event({"kind": "callback", "uid": uid, "callback": query.data,
                        "button_text": button_text,
                        "dialog_intent": "details_known_option", "selected_option": option.get("name"),
                        "state_before": state_before_callback,
                        "state_after": _dialog_state_preview(state),
                        "response_text": response, "buttons": _button_log_preview(kb_rows)})
            state["last_buttons"] = kb_rows
            await query.edit_message_text(_to_html(response), parse_mode="HTML", reply_markup=markup)
            return

        if query.data.startswith("option:"):
            raw_idx = query.data.rsplit(":", 1)[-1]
            try:
                idx = int(raw_idx)
            except ValueError:
                return
            option = _option_by_index(state, idx)
            if not option:
                await query.edit_message_text("Этот вариант уже не вижу в текущем списке. Напишите запрос ещё раз — обновлю подборку.")
                return
            state["selected_option"] = option
            response = _prepare_response_text(_format_option_response(option, state.get("params", {}).get("purpose")))
            response = await _maybe_style_text(
                client,
                response,
                intent="select_option",
                scene="selected_option",
                context=str(option.get("name") or ""),
            )
            kb_rows = _selected_option_rows(idx)
            _log_event({"kind": "callback", "uid": uid, "callback": query.data,
                        "button_text": button_text,
                        "dialog_intent": "select_option", "selected_option": option.get("name"),
                        "state_before": state_before_callback,
                        "state_after": _dialog_state_preview(state),
                        "response_text": response, "buttons": _button_log_preview(kb_rows)})
            state["last_buttons"] = kb_rows
            await query.edit_message_text(_to_html(response), parse_mode="HTML")
            return

        # H013: кнопки выбора параметров — обновляем state и повторяем ask
        if query.data.startswith("budget:"):
            val = query.data[7:]
            state["params"]["max_price"] = _parse_budget_callback_value(val)
            state["asked_questions"].append("budget")
        elif query.data.startswith("rooms:"):
            state["params"]["rooms"] = query.data[6:]
            state["asked_questions"].append("rooms_q")
        elif query.data.startswith("renovation:"):
            state["params"]["has_renovation"] = query.data[11:] == "yes"
            state["asked_questions"].append("renovation")
        elif query.data.startswith("district:"):
            state["params"]["district"] = None if query.data[9:] == "any" else query.data[9:]
            state["asked_questions"].append("district")
        elif query.data == "action:show_near":
            # ослабим бюджет и этаж, чтобы получить near
            state["params"].pop("max_price", None)
            state["params"].pop("min_price", None)
        elif query.data == "action:expand_district":
            state["params"]["district"] = None
        elif query.data in ("action:operator", "request_operator"):
            state["awaiting_phone"] = True
            _log_event({"kind": "operator_requested", "uid": uid, "trigger": "button",
                        "callback": query.data, "button_text": button_text,
                        "state_before": state_before_callback,
                        "state_after": _dialog_state_preview(state)})
            state["last_buttons"] = []
            await query.edit_message_text("Напишите номер для связи текстом — передам запрос оператору вместе с контекстом диалога.")
            await query.message.reply_text("Так оператор сможет быстрее связаться и проверить актуальные варианты.")
            return
        elif query.data == "toggle_mcp":
            state["mcp"] = not state["mcp"]
        elif query.data.startswith("chat_model:"):
            model = query.data.split(":", 1)[1]
            if model not in AVAILABLE_MODELS:
                await query.edit_message_text("Эта модель сейчас не разрешена для теста.")
                return
            state["chat_model"] = model
            _log_event({"kind": "callback", "uid": uid, "callback": query.data,
                        "button_text": button_text,
                        "state_before": state_before_callback,
                        "chat_model_new": model,
                        "search_model": state["search_model"]})
            await query.edit_message_text(
                f"✅ Модель ответа: {model}\n"
                f"🔎 Модель поиска не менялась: {state['search_model']}"
            )
            return
        else:
            await query.edit_message_text("Эта кнопка уже неактуальна. Напишите запрос ещё раз — обновлю подборку.")
            return

        _log_event({"kind": "callback", "uid": uid, "callback": query.data,
                    "button_text": button_text,
                    "state_before": state_before_callback,
                    "params": state.get("params", {})})

        # H013: повторяем ask с обновлёнными params
        indicator = await query.edit_message_text("🔎 Осуществляю поиск...")
        try:
            response, new_params, search_meta, chat_meta = await client.ask(
                query="(уточнено кнопками)", search_model=state["search_model"],
                chat_model=state["chat_model"], use_mcp=state["mcp"],
                params=state.get("params", {}),
            )
            if new_params:
                state["params"] = {**state.get("params", {}), **new_params}
            response = _prepare_response_text(_strip_markdown(response))
            response = await _maybe_style_text(
                client,
                response,
                intent="callback_search",
                scene="callback_search",
                context=json.dumps(state.get("params", {}), ensure_ascii=False),
            )
            _refresh_search_state(state, search_meta)
            state["visible_options"] = _visible_options_from_chat_or_response(chat_meta, response, state.get("last_options") or [])
            state["numeric_choice_policy"] = _numeric_choice_policy_from_response(response, state.get("visible_options") or [])
            scenario = _infer_scenario(state, search_meta)
            kb_rows = _markup_from_chat_buttons(chat_meta, state, response, scenario)
            markup = {"inline_keyboard": kb_rows} if kb_rows else None
            _log_event({"kind": "callback_response", "uid": uid, "callback": query.data,
                        "button_text": button_text,
                        "state_before": state_before_callback,
                        "state_after": _dialog_state_preview(state),
                        "response_text": response, "buttons": _button_log_preview(kb_rows)})
            state["last_buttons"] = kb_rows
            await indicator.edit_text(_to_html(response), parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            LOGGER.exception("button_handler ask failed")
            await indicator.edit_text(f"❌ Ошибка: {e}")

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        uid = update.effective_user.id if update.effective_user else 0
        state = user_state.setdefault(uid, _default_state())
        if not update.message.text:
            message_type = _non_text_message_type(update.message)
            response = _non_text_fallback_response(message_type)
            _log_event({
                "kind": "non_text_message",
                "uid": uid,
                "message_type": message_type,
                "response_text": response,
                "state_after": _dialog_state_preview(state),
            })
            await update.message.reply_text(response, reply_markup=ReplyKeyboardRemove())
            return

        text = update.message.text.strip()

        # Phone capture lives above LLM/search: если клиент прислал валидный телефон,
        # сразу фиксируем контакт и отвечаем шаблоном. Не отправляем номер в LLM.
        phone = _extract_phone_from_text(text)
        if phone:
            was_awaiting = bool(state.pop("awaiting_phone", None))
            had_context = was_awaiting or _has_phone_capture_context(state)
            _log_event({
                "kind": "phone_captured",
                "uid": uid,
                "source": "text",
                "was_awaiting_phone": was_awaiting,
                "had_phone_context": had_context,
                "state_after": _dialog_state_preview(state),
                **_phone_log_meta(phone),
            })
            await update.message.reply_text(_phone_captured_farewell(), reply_markup=ReplyKeyboardRemove())
            return

        if state.get("awaiting_phone"):
            if _looks_like_phone_text(text):
                await update.message.reply_text("Похоже, номер неполный. Напишите телефон в формате +7XXXXXXXXXX или просто цифрами.")
                return
            state.pop("awaiting_phone", None)
            await update.message.reply_text("Похоже, это не номер. Напишите телефон в формате +7XXXXXXXXXX или просто продиктуйте цифрами.")
            return

        if not text:
            return

        params_before = dict(state.get("params", {}))
        _append_dialog_turn(state, "user", text)
        await update.message.chat.send_action(action="typing")

        # H016: короткие follow-up сообщения («второй», «подешевле») решаем из памяти,
        # без нового общего поиска через Overmind.
        dialog_intent = _resolve_dialog_intent(text, state)
        if dialog_intent.get("intent") == "followup_classifier":
            followup_meta = await followup_intent_classifier.classify_followup_intent(
                await client.ensure_session(),
                user_text=text,
                dialog_window=state.get("dialog_window") or [],
                state=_followup_state_payload(state),
            )
            _log_event({
                "kind": "followup_intent",
                "uid": uid,
                "user_text": text,
                "meta": followup_meta,
                "state": _followup_state_payload(state),
            })
            dialog_plan = await followup_intent_classifier.plan_dialog_state(
                await client.ensure_session(),
                user_text=text,
                state=_dialog_planner_state_payload(state),
                last_response_text=_last_bot_text(state),
                search_response_text=json.dumps(state.get("last_search_response") or {}, ensure_ascii=False),
            )
            applied_plan = _apply_dialog_plan_to_state(state, dialog_plan, user_text=text)
            _log_event({
                "kind": "dialog_plan",
                "uid": uid,
                "user_text": text,
                "plan": dialog_plan,
                "applied": applied_plan,
                "state": _dialog_planner_state_payload(state),
            })
            if dialog_plan.get("clarification_question") and not followup_meta.get("clarification_question"):
                followup_meta["clarification_question"] = dialog_plan.get("clarification_question")
            followup_intent = followup_meta.get("intent")
            dialog_action = str(dialog_plan.get("dialog_action") or "")
            visible_policy = str(dialog_plan.get("visible_options_policy") or "")
            planner_confidence = float(dialog_plan.get("confidence") or 0.0)
            planner_ready = bool(dialog_action and not dialog_plan.get("fallback_used") and planner_confidence >= 0.7)
            if planner_ready:
                followup_meta["orchestrator_action"] = dialog_action
                if dialog_plan.get("selected_option_name"):
                    followup_meta["target"] = str(dialog_plan.get("selected_option_name") or "")
                if dialog_action == "select_option":
                    followup_intent = "choose_option"
                elif dialog_action == "expand_more_options" or (dialog_action == "update_search" and visible_policy == "rebuild" and (state.get("visible_options") or state.get("last_options"))):
                    followup_intent = "expand_more_options"
                elif dialog_action == "compare_options":
                    followup_intent = "compare_options"
                elif dialog_action == "recommend_options":
                    followup_intent = "recommend_options"
                elif dialog_action == "update_search":
                    followup_intent = "update_search_params"
                    followup_meta["params_delta"] = dialog_plan.get("params_delta") if isinstance(dialog_plan.get("params_delta"), dict) else {}
                elif dialog_action == "new_search":
                    followup_intent = "new_search"
                elif dialog_action == "continue_from_memory":
                    followup_intent = "continue_selection"
                elif dialog_action == "operator_live_check":
                    followup_intent = "operator_for_selected"
                elif dialog_action in {"reject_offer", "reject_operator", "reject_phone", "reject_selected_option", "reject_similar_options", "clarify_negation"}:
                    followup_intent = dialog_action
                followup_meta["intent"] = followup_intent
            selected = state.get("selected_option")
            if dialog_plan.get("dialog_action") == "ask_clarification" and dialog_plan.get("clarification_question"):
                response = _prepare_response_text(str(dialog_plan.get("clarification_question") or ""))
                _remember_bot_response(state, response, offer_type="clarify", answer_kind="dialog_plan_clarification")
                _log_event({
                    "kind": "user_message", "uid": uid, "user_text": text,
                    "dialog_intent": "dialog_plan_clarification", "search_model": state["search_model"],
                    "chat_model": state["chat_model"], "mcp": state["mcp"],
                    "params_before": params_before, "params_after": dict(state.get("params", {})),
                    "params_delta": {}, "response_text": response, "response_len": len(response),
                    "buttons": [], "duration_ms": 0, "is_error": False, "error": None, "cost": {},
                    "dialog_plan": dialog_plan,
                })
                await update.message.reply_text(_to_html(response), parse_mode="HTML")
                return
            if followup_intent in {"compare_selected", "compare_options"} and selected:
                selected_name = _compact_option_text(selected.get("name"))
                options = state.get("visible_options") or state.get("last_options") or []
                dialog_intent = {
                    "intent": "compare_others",
                    "options": [
                        option for option in options
                        if _compact_option_text(option.get("name")) != selected_name
                    ][:3],
                }
            elif followup_intent == "compare_options":
                dialog_intent = {"intent": "compare_others", "options": (state.get("visible_options") or state.get("last_options") or [])[:3]}
            elif followup_intent == "recommend_options":
                dialog_intent = {"intent": "recommend_options", "options": (state.get("visible_options") or state.get("last_options") or [])[:3]}
            elif followup_intent == "operator_for_selected" and selected:
                dialog_intent = {"intent": "operator_for_selected", "option": selected}
            elif followup_intent == "operator_for_selected":
                dialog_intent = {"intent": "operator_for_context", "options": (state.get("visible_options") or state.get("last_options") or [])[:3]}
            elif followup_intent == "operator_contact_accept" and selected:
                dialog_intent = {"intent": "operator_contact_accept", "option": selected}
            elif followup_intent in {"reject_operator", "reject_phone"}:
                dialog_intent = {"intent": "reject_operator", "option": selected}
            elif followup_intent == "reject_selected_option" and selected:
                dialog_intent = {"intent": "reject_selected_option", "option": selected}
            elif followup_intent == "reject_similar_options":
                dialog_intent = {"intent": "reject_similar_options", "option": selected}
            elif followup_intent == "clarify_negation":
                dialog_intent = {"intent": "clarify_negation", "meta": followup_meta, "option": selected}
            elif followup_intent == "reject_offer":
                # LLM classifier can label phrases like «не надо бронь» as generic reject_offer.
                # For negated live-data topics this is not a plain no; answer via negation presenter
                # and clarify what to do next instead of falling into generic clarify/fallback.
                dialog_intent = {"intent": "clarify_negation", "meta": followup_meta, "option": selected}
            elif followup_intent == "choose_option":
                target = str(followup_meta.get("target") or dialog_plan.get("selected_option_name") or "")
                options = state.get("visible_options") or state.get("last_options") or []
                matched = state.get("selected_option") if isinstance(state.get("selected_option"), dict) else None
                if matched and target and _compact_option_text(matched.get("name")) != _compact_option_text(target):
                    matched = None
                matched = matched or _match_option_from_text(target, options)
                if matched:
                    dialog_intent = {"intent": "select_option", "option": matched}
                else:
                    response = _prepare_response_text(_clarification_from_followup(followup_meta, state))
                    _remember_bot_response(state, response, offer_type="clarify", answer_kind="clarification")
                    _log_event({
                        "kind": "user_message", "uid": uid, "user_text": text,
                        "dialog_intent": "clarify", "search_model": state["search_model"],
                        "chat_model": state["chat_model"], "mcp": state["mcp"],
                        "params_before": params_before, "params_after": dict(state.get("params", {})),
                        "params_delta": {}, "response_text": response, "response_len": len(response),
                        "buttons": [], "duration_ms": 0, "is_error": False, "error": None, "cost": {},
                    })
                    await update.message.reply_text(_to_html(response), parse_mode="HTML")
                    return
            elif followup_intent == "explain_operator_reason" and selected:
                response = _prepare_response_text(_operator_reason_response(state))
                _remember_bot_response(state, response, offer_type="operator_for_selected", answer_kind="operator_explanation")
                _log_event({
                    "kind": "user_message",
                    "uid": uid,
                    "user_text": text,
                    "dialog_intent": "explain_operator_reason",
                    "search_model": state["search_model"],
                    "chat_model": state["chat_model"],
                    "mcp": state["mcp"],
                    "params_before": params_before,
                    "params_after": dict(state.get("params", {})),
                    "params_delta": {},
                    "response_text": response,
                    "response_len": len(response),
                    "buttons": [],
                    "duration_ms": 0,
                    "is_error": False,
                    "error": None,
                    "cost": {},
                })
                await update.message.reply_text(_to_html(response), parse_mode="HTML")
                return
            elif followup_intent == "continue_selection":
                response = _prepare_response_text(_continue_selection_response(state))
                _remember_bot_response(state, response, offer_type="choose_option", answer_kind="options_summary")
                _log_event({
                    "kind": "user_message",
                    "uid": uid,
                    "user_text": text,
                    "dialog_intent": "continue_selection",
                    "search_model": state["search_model"],
                    "chat_model": state["chat_model"],
                    "mcp": state["mcp"],
                    "params_before": params_before,
                    "params_after": dict(state.get("params", {})),
                    "params_delta": {},
                    "response_text": response,
                    "response_len": len(response),
                    "buttons": [],
                    "duration_ms": 0,
                    "is_error": False,
                    "error": None,
                    "cost": {},
                })
                await update.message.reply_text(_to_html(response), parse_mode="HTML")
                return
            elif followup_intent == "update_search_params":
                params_delta = followup_meta.get("params_delta") if isinstance(followup_meta.get("params_delta"), dict) else {}
                params_delta = _normalize_followup_params_delta(params_delta)
                if params_delta:
                    state["params"] = {**state.get("params", {}), **params_delta}
                    LOGGER.info("User %d: params updated from followup: %s", uid, state["params"])
                state["numeric_choice_policy"] = "reject"
                dialog_intent = {"intent": "new_search"}
            elif followup_intent == "expand_more_options":
                dialog_intent = {"intent": "expand_more_options", "options": state.get("visible_options") or state.get("last_options") or []}
            elif followup_intent == "new_search":
                state["numeric_choice_policy"] = "reject"
                dialog_intent = {"intent": "new_search"}
            else:
                response = _prepare_response_text(_clarification_from_followup(followup_meta, state))
                _remember_bot_response(state, response, offer_type="clarify", answer_kind="clarification")
                _log_event({
                    "kind": "user_message",
                    "uid": uid,
                    "user_text": text,
                    "dialog_intent": "clarify",
                    "search_model": state["search_model"],
                    "chat_model": state["chat_model"],
                    "mcp": state["mcp"],
                    "params_before": params_before,
                    "params_after": dict(state.get("params", {})),
                    "params_delta": {},
                    "response_text": response,
                    "response_len": len(response),
                    "buttons": [],
                    "duration_ms": 0,
                    "is_error": False,
                    "error": None,
                    "cost": {},
                })
                await update.message.reply_text(_to_html(response), parse_mode="HTML")
                return
        if dialog_intent.get("intent") == "operator_contact_accept":
            option = dialog_intent.get("option") or state.get("selected_option") or {}
            if isinstance(option, dict) and option:
                state["selected_option"] = option
                state["operator_context"] = {
                    "selected_option": option.get("name"),
                    "known_facts": option,
                    "client_question": text,
                    "reason": "selected_option_contact_accept",
                }
            state["awaiting_phone"] = True
            response = _prepare_response_text(_operator_contact_request_text())
            _log_event({
                "kind": "operator_contact_requested",
                "uid": uid,
                "user_text": text,
                "selected_option": option.get("name") if isinstance(option, dict) else None,
                "reason": "contact_offer_accept",
                "response_text": response,
            })
            _remember_bot_response(state, response, offer_type="awaiting_phone", answer_kind="operator_contact_request")
            await update.message.reply_text(_to_html(response), parse_mode="HTML")
            return

        if dialog_intent.get("intent") in {"reject_operator", "reject_phone"}:
            try:
                response, chat_meta = await client.explain_negation_followup(
                    intent=dialog_intent.get("intent") or "reject_operator",
                    user_text=text,
                    state=state,
                    chat_model=state["chat_model"],
                )
                response = _prepare_response_text(response)
                response = await _maybe_style_text(
                    client,
                    response,
                    intent="negation_reject_operator",
                    scene="followup_negation",
                    context=str((state.get("selected_option") or {}).get("name") or ""),
                )
            except Exception:
                LOGGER.exception("negation reject_operator chat failed")
                chat_meta = {}
                response = _prepare_response_text(_reject_operator_response(state))
            _log_event({
                "kind": "user_message",
                "uid": uid,
                "user_text": text,
                "dialog_intent": "reject_operator",
                "search_model": state["search_model"],
                "chat_model": state["chat_model"],
                "mcp": state["mcp"],
                "params_before": params_before,
                "params_after": dict(state.get("params", {})),
                "params_delta": {},
                "response_text": response,
                "response_len": len(response),
                "buttons": [],
                "duration_ms": 0,
                "is_error": False,
                "error": None,
                "cost": {},
                "chat_meta": chat_meta,
            })
            _remember_bot_response(state, response, offer_type="continue_selection", answer_kind="reject_operator")
            await update.message.reply_text(_to_html(response), parse_mode="HTML")
            return

        if dialog_intent.get("intent") == "reject_selected_option":
            option = dialog_intent.get("option") or state.get("selected_option") or {}
            if isinstance(option, dict) and option.get("name"):
                rejected = state.setdefault("rejected_option_names", [])
                name = option.get("name")
                if name not in rejected:
                    rejected.append(name)
            try:
                response, chat_meta = await client.explain_negation_followup(
                    intent="reject_selected_option",
                    user_text=text,
                    state=state,
                    chat_model=state["chat_model"],
                )
                response = _prepare_response_text(response)
                response = await _maybe_style_text(
                    client,
                    response,
                    intent="negation_reject_selected_option",
                    scene="followup_negation",
                    context=str(option.get("name") if isinstance(option, dict) else ""),
                )
            except Exception:
                LOGGER.exception("negation reject_selected_option chat failed")
                chat_meta = {}
                response = _prepare_response_text(_reject_selected_option_response(state))
            state["selected_option"] = None
            _log_event({
                "kind": "user_message",
                "uid": uid,
                "user_text": text,
                "dialog_intent": "reject_selected_option",
                "search_model": state["search_model"],
                "chat_model": state["chat_model"],
                "mcp": state["mcp"],
                "params_before": params_before,
                "params_after": dict(state.get("params", {})),
                "params_delta": {},
                "response_text": response,
                "response_len": len(response),
                "buttons": [],
                "duration_ms": 0,
                "is_error": False,
                "error": None,
                "cost": {},
                "chat_meta": chat_meta,
            })
            _remember_bot_response(state, response, offer_type="choose_option", answer_kind="reject_selected_option")
            await update.message.reply_text(_to_html(response), parse_mode="HTML")
            return

        if dialog_intent.get("intent") == "reject_similar_options":
            try:
                response, chat_meta = await client.explain_negation_followup(
                    intent="reject_similar_options",
                    user_text=text,
                    state=state,
                    chat_model=state["chat_model"],
                )
                response = _prepare_response_text(response)
                response = await _maybe_style_text(
                    client,
                    response,
                    intent="negation_reject_similar_options",
                    scene="followup_negation",
                    context=str((state.get("selected_option") or {}).get("name") or ""),
                )
            except Exception:
                LOGGER.exception("negation reject_similar_options chat failed")
                chat_meta = {}
                response = _prepare_response_text(_reject_similar_options_response(state))
            _log_event({
                "kind": "user_message",
                "uid": uid,
                "user_text": text,
                "dialog_intent": "reject_similar_options",
                "search_model": state["search_model"],
                "chat_model": state["chat_model"],
                "mcp": state["mcp"],
                "params_before": params_before,
                "params_after": dict(state.get("params", {})),
                "params_delta": {},
                "response_text": response,
                "response_len": len(response),
                "buttons": [],
                "duration_ms": 0,
                "is_error": False,
                "error": None,
                "cost": {},
                "chat_meta": chat_meta,
            })
            _remember_bot_response(state, response, offer_type="clarify", answer_kind="reject_similar_options")
            await update.message.reply_text(_to_html(response), parse_mode="HTML")
            return

        if dialog_intent.get("intent") == "clarify_negation":
            negation_meta = dialog_intent.get("meta") or {}
            try:
                response, chat_meta = await client.explain_negation_followup(
                    intent="clarify_negation",
                    user_text=text,
                    state=state,
                    meta=negation_meta,
                    chat_model=state["chat_model"],
                )
                response = _prepare_response_text(response)
                response = await _maybe_style_text(
                    client,
                    response,
                    intent="negation_clarify",
                    scene="followup_negation",
                    context=str((state.get("selected_option") or {}).get("name") or ""),
                )
            except Exception:
                LOGGER.exception("negation clarify chat failed")
                chat_meta = {}
                response = _prepare_response_text(_negation_clarification_response(negation_meta, state))
            _log_event({
                "kind": "user_message",
                "uid": uid,
                "user_text": text,
                "dialog_intent": "clarify_negation",
                "search_model": state["search_model"],
                "chat_model": state["chat_model"],
                "mcp": state["mcp"],
                "params_before": params_before,
                "params_after": dict(state.get("params", {})),
                "params_delta": {},
                "response_text": response,
                "response_len": len(response),
                "buttons": [],
                "duration_ms": 0,
                "is_error": False,
                "error": None,
                "cost": {},
                "chat_meta": chat_meta,
            })
            _remember_bot_response(state, response, offer_type="clarify", answer_kind="clarify_negation")
            await update.message.reply_text(_to_html(response), parse_mode="HTML")
            return

        if dialog_intent.get("intent") == "select_option":
            option = dialog_intent["option"]
            state["selected_option"] = option
            state["selected_option_card_shown_count"] = int(state.get("selected_option_card_shown_count") or 0) + 1
            client_request = (
                "Клиент выбрал этот вариант из списка. "
                f"Контекст клиента: {state.get('params', {}).get('purpose') or 'не указан'}. "
                "Дай первичную живую презентацию выбранного ЖК без нового поиска."
            )
            if STAGE_PRESENTER_ENABLED:
                chat_meta = {"presenter": "stage_selected_object"}
                scenario_for_stage = _reason_layer_scenario(text, state.get("params", {}))
                if scenario_for_stage == "self_use" and state.get("params", {}).get("purpose"):
                    scenario_for_stage = str(state.get("params", {}).get("purpose") or "self_use")
                option_for_answer, enrichment_meta = await _get_or_fetch_enriched_option(client, state, option, scenario_for_stage)
                state["selected_option"] = option_for_answer
                sales_benefits, sales_meta = await _sales_phrases_for_stage(client, [option_for_answer], scenario_for_stage)
                chat_meta = {**chat_meta, "sales_phrase": sales_meta, "enrichment": enrichment_meta}
                response = _prepare_response_text(_render_stage_selected_object(option_for_answer, scenario_for_stage, sales_benefits.get(1)))
            else:
                try:
                    response, chat_meta = await client.explain_known_option(
                        option=option,
                        client_request=client_request,
                        chat_model=state["chat_model"],
                    )
                    response = _prepare_response_text(response)
                    response = await _maybe_style_text(
                        client,
                        response,
                        intent="select_option_presenter",
                        scene="followup_selected_option",
                        context=str(option.get("name") or ""),
                    )
                    response = _strip_unsupported_complex_claims(response, option)
                    response = _strip_unrequested_live_data_cta(response, client_request)
                    response = _soften_layout_overclaim(response)
                    response = _soften_generic_selected_question(response)
                    response = _operator_cta_for_selected_investment(response, option, state.get("params", {}).get("purpose"))
                    response = _ensure_final_next_question(response, selected=True)
                except Exception:
                    LOGGER.exception("selected option initial LLM presenter failed")
                    chat_meta = {}
                    response = _prepare_response_text(_format_option_response(option, state.get("params", {}).get("purpose")))
                    response = _strip_unsupported_complex_claims(response, option)
                    response = _strip_unrequested_live_data_cta(response)
                    response = _soften_layout_overclaim(response)
                    response = _soften_generic_selected_question(response)
                    response = _operator_cta_for_selected_investment(response, option, state.get("params", {}).get("purpose"))
                    response = _operator_cta_for_selected_investment(response, option, state.get("params", {}).get("purpose"))
                    response = await _maybe_style_text(
                        client,
                        response,
                        intent="select_option_fallback",
                        scene="followup_selected_option",
                        context=str(option.get("name") or ""),
                    )
                    response = _strip_unsupported_complex_claims(response, option)
                    response = _strip_unrequested_live_data_cta(response)
                    response = _soften_layout_overclaim(response)
                    response = _soften_generic_selected_question(response)
                    response = _ensure_final_next_question(response, selected=True)
            if not STAGE_PRESENTER_ENABLED:
                pass
            elif "?" not in "\n".join(response.splitlines()[-3:]):
                response = _ensure_final_next_question(response, selected=True)
            option = state.get("selected_option") if isinstance(state.get("selected_option"), dict) else option
            idx = int(option.get("idx") or 1)
            kb_rows = _selected_option_rows(idx)
            _log_event({
                "kind": "user_message",
                "uid": uid,
                "user_text": text,
                "dialog_intent": "select_option",
                "selected_option": option.get("name"),
                "search_model": state["search_model"],
                "chat_model": state["chat_model"],
                "mcp": state["mcp"],
                "params_before": params_before,
                "params_after": dict(state.get("params", {})),
                "params_delta": {},
                "response_text": response,
                "response_len": len(response),
                "buttons": _button_log_preview(kb_rows),
                "duration_ms": 0,
                "is_error": False,
                "error": None,
                "cost": {},
                "chat_meta": chat_meta,
            })
            state["last_buttons"] = kb_rows
            _remember_bot_response(state, response, offer_type="selected_option", answer_kind="selected_option_card")
            await update.message.reply_text(_to_html(response), parse_mode="HTML")
            return

        if dialog_intent.get("intent") == "explain_selected_option":
            option = dialog_intent["option"]
            state["selected_option"] = option
            if STAGE_PRESENTER_ENABLED:
                chat_meta = {"presenter": "stage_selected_object_details"}
                scenario_for_stage = _reason_layer_scenario(text, state.get("params", {}))
                if scenario_for_stage == "self_use" and state.get("params", {}).get("purpose"):
                    scenario_for_stage = str(state.get("params", {}).get("purpose") or "self_use")
                option_for_answer, enrichment_meta = await _get_or_fetch_enriched_option(client, state, option, scenario_for_stage)
                state["selected_option"] = option_for_answer
                sales_benefits, sales_meta = await _sales_phrases_for_stage(client, [option_for_answer], scenario_for_stage)
                chat_meta = {**chat_meta, "sales_phrase": sales_meta, "enrichment": enrichment_meta}
                response = _prepare_response_text(_render_stage_selected_object(option_for_answer, scenario_for_stage, sales_benefits.get(1)))
                response = _ensure_final_next_question(response, selected=True)
                kb_rows = []
            else:
                try:
                    response, chat_meta = await client.explain_known_option(
                        option=option,
                        client_request=(
                            f"{text}\n"
                            f"Контекст клиента: {state.get('params', {}).get('purpose') or 'не указан'}."
                        ),
                        chat_model=state["chat_model"],
                    )
                    response = _prepare_response_text(response)
                    response = _strip_unsupported_complex_claims(response, option)
                    response = _strip_unrequested_live_data_cta(response, text)
                    response = _soften_layout_overclaim(response)
                    response = await _maybe_style_text(
                        client,
                        response,
                        intent="details_known_option",
                        scene="followup_selected_option_details",
                        context=str(option.get("name") or ""),
                    )
                    response = _strip_unsupported_complex_claims(response, option)
                    response = _strip_unrequested_live_data_cta(response, text)
                    response = _soften_layout_overclaim(response)
                    response = _soften_generic_selected_question(response)
                    response = _ensure_final_next_question(response, selected=True)
                    kb_rows = _markup_from_chat_buttons(chat_meta, state, response, "selected-option-details")
                except Exception:
                    LOGGER.exception("selected option explain chat failed")
                    chat_meta = {}
                    response = _prepare_response_text(_format_option_response(option, state.get("params", {}).get("purpose")))
                    response = _strip_unsupported_complex_claims(response, option)
                    response = _strip_unrequested_live_data_cta(response, text)
                    response = _soften_layout_overclaim(response)
                    response = _soften_generic_selected_question(response)
                    response = _ensure_final_next_question(response, selected=True)
                    kb_rows = []
            _log_event({
                "kind": "user_message",
                "uid": uid,
                "user_text": text,
                "dialog_intent": "explain_selected_option",
                "selected_option": option.get("name"),
                "search_model": state["search_model"],
                "chat_model": state["chat_model"],
                "mcp": state["mcp"],
                "params_before": params_before,
                "params_after": dict(state.get("params", {})),
                "params_delta": {},
                "response_text": response,
                "response_len": len(response),
                "buttons": _button_log_preview(kb_rows),
                "duration_ms": 0,
                "is_error": False,
                "error": None,
                "cost": {},
                "chat_meta": chat_meta,
            })
            state["last_buttons"] = kb_rows
            _remember_bot_response(state, response, offer_type="selected_option_details", answer_kind="selected_option_details")
            await update.message.reply_text(_to_html(response), parse_mode="HTML")
            return

        if dialog_intent.get("intent") == "operator_for_selected":
            option = dialog_intent["option"]
            state["selected_option"] = option
            state["operator_context"] = {
                "selected_option": option.get("name"),
                "known_facts": option,
                "client_question": text,
                "reason": "selected_option_live_details",
            }
            # Если клиент уже дошёл до стадии оператора / актуальных квартир,
            # не продаём операторский шаг повторно — сразу просим номер.
            state["awaiting_phone"] = True
            response = _prepare_response_text(_format_operator_handoff_for_option(option))
            kb_rows = []
            _log_event({
                "kind": "operator_handoff_ready",
                "uid": uid,
                "user_text": text,
                "selected_option": option.get("name"),
                "reason": "selected_option_live_details",
                "response_text": response,
                "buttons": _button_log_preview(kb_rows),
            })
            state["last_buttons"] = kb_rows
            _remember_bot_response(state, response, offer_type="awaiting_phone", answer_kind="operator_contact_request")
            await update.message.reply_text(_to_html(response), parse_mode="HTML")
            return

        if dialog_intent.get("intent") == "operator_for_context":
            options = dialog_intent.get("options") or state.get("visible_options") or state.get("last_options") or []
            state["operator_context"] = {
                "selected_option": None,
                "known_facts": {"options": options[:3], "params": state.get("params", {})},
                "client_question": text,
                "reason": "operator_context_request",
            }
            state["awaiting_phone"] = True
            response = _prepare_response_text(_format_operator_handoff_for_context(state, text))
            kb_rows = []
            _log_event({
                "kind": "operator_handoff_ready",
                "uid": uid,
                "user_text": text,
                "selected_option": None,
                "reason": "operator_context_request",
                "response_text": response,
                "buttons": _button_log_preview(kb_rows),
            })
            state["last_buttons"] = kb_rows
            _remember_bot_response(state, response, offer_type="awaiting_phone", answer_kind="operator_contact_request")
            await update.message.reply_text(_to_html(response), parse_mode="HTML")
            return

        if dialog_intent.get("intent") == "recommend_options":
            options = dialog_intent.get("options") or state.get("visible_options") or state.get("last_options") or []
            stage_scenario = _reason_layer_scenario(text, state.get("params", {}))
            response = _prepare_response_text(_render_stage_recommendation(options, stage_scenario))
            kb_rows = _option_select_rows(options)
            _log_event({
                "kind": "user_message",
                "uid": uid,
                "user_text": text,
                "dialog_intent": "recommend_options",
                "search_model": state["search_model"],
                "chat_model": state["chat_model"],
                "mcp": state["mcp"],
                "params_before": params_before,
                "params_after": dict(state.get("params", {})),
                "params_delta": {},
                "response_text": response,
                "response_len": len(response),
                "buttons": _button_log_preview(kb_rows),
                "duration_ms": 0,
                "is_error": False,
                "error": None,
                "cost": {},
            })
            state["last_buttons"] = kb_rows
            _remember_bot_response(state, response, offer_type="selected_option_details", answer_kind="recommend_options")
            await update.message.reply_text(_to_html(response), parse_mode="HTML")
            return

        if dialog_intent.get("intent") == "compare_others":
            options = dialog_intent.get("options", [])
            response = _prepare_response_text(_format_options_summary_response(
                options,
                "Сравню с другими вариантами из последнего списка",
                "Какой из них раскрыть подробнее?",
            ))
            response = await _maybe_style_text(
                client,
                response,
                intent="compare_others",
                scene="followup_compare",
                context=", ".join(str(o.get("name") or "") for o in options[:3]),
            )
            kb_rows = _option_select_rows(options)
            _log_event({
                "kind": "user_message",
                "uid": uid,
                "user_text": text,
                "dialog_intent": "compare_others",
                "search_model": state["search_model"],
                "chat_model": state["chat_model"],
                "mcp": state["mcp"],
                "params_before": params_before,
                "params_after": dict(state.get("params", {})),
                "params_delta": {},
                "response_text": response,
                "response_len": len(response),
                "buttons": _button_log_preview(kb_rows),
                "duration_ms": 0,
                "is_error": False,
                "error": None,
                "cost": {},
            })
            state["last_buttons"] = kb_rows
            _remember_bot_response(state, response, offer_type="choose_option", answer_kind="options_summary")
            await update.message.reply_text(_to_html(response), parse_mode="HTML")
            return

        if dialog_intent.get("intent") == "expand_more_options":
            query_for_search, excluded_names = _build_followup_expansion_query(text, state)
            try:
                response, new_params, search_meta, chat_meta = await client.ask(
                    query=query_for_search,
                    search_model=state["search_model"],
                    chat_model=state["chat_model"],
                    use_mcp=state["mcp"],
                    params=state.get("params", {}),
                )
                if new_params:
                    state["params"] = {**state.get("params", {}), **new_params}
                    LOGGER.info("User %d: params updated from expanded followup: %s", uid, state["params"])
                _refresh_search_state(state, search_meta)
                temp_state = dict(state)
                temp_state["rejected_option_names"] = excluded_names
                state["last_options"] = _filter_rejected_options(state.get("last_options") or [], temp_state)
                stage_scenario = _reason_layer_scenario(text, state.get("params", {}))
                stage_options = state.get("last_options") or []
                if len(stage_options) >= 2:
                    sales_benefits, sales_meta = await _sales_phrases_for_stage(client, stage_options, stage_scenario)
                    response = _render_stage_first_list(stage_options, stage_scenario, sales_benefits)
                    state["visible_options"] = stage_options[:3]
                    state["numeric_choice_policy"] = _numeric_choice_policy_from_response(response, state.get("visible_options") or [])
                    if OPTION_ENRICHMENT_ENABLED:
                        asyncio.create_task(_prefetch_options_enrichment(client, state, stage_options[:3], stage_scenario))
                    turn_meta = {
                        "enabled": True,
                        "applied": True,
                        "stage": "expanded_first_list",
                        "scenario": stage_scenario,
                        "options_count": len(state.get("visible_options") or []),
                        "sales_phrase": sales_meta,
                        "excluded_names": excluded_names,
                    }
                elif len(stage_options) == 1:
                    option_for_answer, enrichment_meta = await _get_or_fetch_enriched_option(client, state, stage_options[0], stage_scenario)
                    sales_benefits, sales_meta = await _sales_phrases_for_stage(client, [option_for_answer], stage_scenario)
                    response = _render_stage_selected_object(option_for_answer, stage_scenario, sales_benefits.get(1))
                    state["visible_options"] = []
                    state["selected_option"] = option_for_answer
                    turn_meta = {
                        "enabled": True,
                        "applied": True,
                        "stage": "expanded_selected_object",
                        "scenario": stage_scenario,
                        "options_count": 1,
                        "sales_phrase": sales_meta,
                        "enrichment": enrichment_meta,
                        "excluded_names": excluded_names,
                    }
                else:
                    response = _prepare_response_text("Пока не вижу новых вариантов без расширения условий. Могу посмотреть соседние районы или чуть поднять бюджет.")
                    state["visible_options"] = []
                    turn_meta = {
                        "enabled": True,
                        "applied": False,
                        "stage": "expanded_empty",
                        "scenario": stage_scenario,
                        "options_count": 0,
                        "excluded_names": excluded_names,
                    }
                response = await _maybe_style_text(
                    client,
                    response,
                    intent="expand_more_options",
                    scene="followup_expand_more_options",
                    context=", ".join(str(o.get("name") or "") for o in (state.get("visible_options") or [])[:3]),
                )
                kb_rows = _option_select_rows(state.get("visible_options") or [])
                state["last_buttons"] = kb_rows
                _remember_bot_response(state, response, offer_type="choose_option", answer_kind="options_summary")
                _log_event({
                    "kind": "user_message",
                    "uid": uid,
                    "user_text": text,
                    "dialog_intent": "expand_more_options",
                    "search_model": state["search_model"],
                    "chat_model": state["chat_model"],
                    "mcp": state["mcp"],
                    "params_before": params_before,
                    "params_after": dict(state.get("params", {})),
                    "params_delta": new_params if 'new_params' in locals() else {},
                    "response_text": response,
                    "response_len": len(response),
                    "buttons": _button_log_preview(kb_rows),
                    "duration_ms": 0,
                    "is_error": False,
                    "error": None,
                    "cost": {},
                    "turn_meta": turn_meta,
                })
                await update.message.reply_text(_to_html(response), reply_markup={"inline_keyboard": kb_rows} if kb_rows else None, parse_mode="HTML")
                return
            except Exception as e:
                LOGGER.exception("Error handling expanded followup")
                response = _prepare_response_text(f"❌ Ошибка при расширении подбора: {e}")
                await update.message.reply_text(_to_html(response), parse_mode="HTML")
                return

        if dialog_intent.get("intent") == "sort_price_asc":
            options = dialog_intent.get("options", [])
            response = _prepare_response_text(_format_cheaper_response(options))
            response = await _maybe_style_text(
                client,
                response,
                intent="sort_price_asc",
                scene="followup_cheaper",
                context=", ".join(str(o.get("name") or "") for o in options[:3]),
            )
            kb_rows = _option_select_rows(options)
            _log_event({
                "kind": "user_message",
                "uid": uid,
                "user_text": text,
                "dialog_intent": "sort_price_asc",
                "search_model": state["search_model"],
                "chat_model": state["chat_model"],
                "mcp": state["mcp"],
                "params_before": params_before,
                "params_after": dict(state.get("params", {})),
                "params_delta": {},
                "response_text": response,
                "response_len": len(response),
                "buttons": _button_log_preview(kb_rows),
                "duration_ms": 0,
                "is_error": False,
                "error": None,
                "cost": {},
            })
            kb = {"inline_keyboard": kb_rows} if kb_rows else None
            state["last_buttons"] = kb_rows
            _remember_bot_response(state, response, offer_type="choose_option", answer_kind="options_summary")
            await update.message.reply_text(_to_html(response), reply_markup=kb, parse_mode="HTML")
            return

        if dialog_intent.get("intent") == "filter_finish":
            options = dialog_intent.get("options", [])
            response = _prepare_response_text(_format_options_summary_response(
                options,
                "С отделкой по последнему списку вижу",
                "Какой вариант раскрыть подробнее?",
            ))
            response = await _maybe_style_text(
                client,
                response,
                intent="filter_finish",
                scene="followup_finish",
                context=", ".join(str(o.get("name") or "") for o in options[:3]),
            )
            kb_rows = _option_select_rows(options)
            _log_event({
                "kind": "user_message",
                "uid": uid,
                "user_text": text,
                "dialog_intent": "filter_finish",
                "search_model": state["search_model"],
                "chat_model": state["chat_model"],
                "mcp": state["mcp"],
                "params_before": params_before,
                "params_after": dict(state.get("params", {})),
                "params_delta": {},
                "response_text": response,
                "response_len": len(response),
                "buttons": _button_log_preview(kb_rows),
                "duration_ms": 0,
                "is_error": False,
                "error": None,
                "cost": {},
            })
            kb = {"inline_keyboard": kb_rows} if kb_rows else None
            state["last_buttons"] = kb_rows
            _remember_bot_response(state, response, offer_type="choose_option", answer_kind="options_summary")
            await update.message.reply_text(_to_html(response), reply_markup=kb, parse_mode="HTML")
            return

        # Печатает... индикатор
        await update.message.chat.send_action(action="typing")
        # H012: видимый индикатор «ищу», потом editMessageText на финальный ответ
        indicator = await update.message.reply_text("🔎 Осуществляю поиск...")

        LOGGER.info("User %d: search_model=%s, chat_model=%s, mcp=%s, params=%s, query=%s",
                     uid, state["search_model"], state["chat_model"], state["mcp"], state.get("params", {}), text[:100])

        t0 = time.monotonic()
        error_text: str | None = None
        response: str = ""
        new_params: dict = {}
        search_meta: dict = {}
        chat_meta: dict = {}
        try:
            query_for_search = text
            rejected_names = [str(name) for name in (state.get("rejected_option_names") or []) if str(name).strip()]
            if rejected_names:
                query_for_search = (
                    f"{text}\n\n"
                    "Внутреннее ограничение диалога: клиент уже отверг эти ЖК, не показывай их снова в подборке: "
                    f"{', '.join(rejected_names)}."
                )
            response, new_params, search_meta, chat_meta = await client.ask(
                query=query_for_search, search_model=state["search_model"], chat_model=state["chat_model"], use_mcp=state["mcp"],
                params=state.get("params", {})
            )
            # Обновляем параметры
            if new_params:
                state["params"] = {**state.get("params", {}), **new_params}
                LOGGER.info("User %d: params updated: %s", uid, state["params"])
        except Exception as e:
            LOGGER.exception("Error handling message")
            error_text = repr(e)
            response = f"❌ Ошибка: {e}"

        duration_ms = int((time.monotonic() - t0) * 1000)

        # Cost: H007-B' — Overmind не отдаёт tokens_in/out и cost_usd, только tokens_used.
        # cost_usd оставляем None; для биллинга использовать scripts/or_cost.py.
        def _meta_cost(meta: dict) -> tuple[float, int, int, int]:
            cost = float(meta.get("cost_usd") or meta.get("usage_cost") or 0.0)
            t_in = int(meta.get("tokens_in") or meta.get("input_tokens") or meta.get("prompt_tokens") or 0)
            t_out = int(meta.get("tokens_out") or meta.get("completion_tokens") or meta.get("output_tokens") or 0)
            t_used = int(meta.get("tokens_used") or 0)
            return cost, t_in, t_out, t_used

        s_cost, s_in, s_out, s_used = _meta_cost(search_meta)
        c_cost, c_in, c_out, c_used = _meta_cost(chat_meta)

        upstream_failed = bool(search_meta.get("_safe_fallback") or chat_meta.get("_safe_fallback"))
        if upstream_failed or _is_safe_upstream_fallback(response):
            response = SAFE_UPSTREAM_ERROR_TEXT
            _refresh_search_state(state, search_meta)
            state["visible_options"] = []
            state["numeric_choice_policy"] = "reject"
            reason_layer_meta: dict[str, Any] = {"enabled": REASON_LAYER_ENABLED, "skipped": "safe_upstream_fallback"}
            stage_presenter_meta: dict[str, Any] = {"enabled": STAGE_PRESENTER_ENABLED, "skipped": "safe_upstream_fallback"}
            duration_ms = int((time.monotonic() - t0) * 1000)
            response = _append_model_stats_footer(
                response,
                state=state,
                chat_meta=chat_meta,
                duration_ms=duration_ms,
                reason_layer_meta=reason_layer_meta,
            )
            kb_rows: list[list[dict[str, Any]]] = []
            markup = None
            state["last_buttons"] = kb_rows
            _remember_bot_response(state, response, offer_type="", answer_kind="main_search_error")
            _log_event({
                "kind": "user_message",
                "uid": uid,
                "user_text": text,
                "dialog_intent": "main_search",
                "search_model": state["search_model"],
                "chat_model": state["chat_model"],
                "mcp": state["mcp"],
                "search_response": search_meta.get("_response_text", ""),
                "params_before": params_before,
                "params_after": dict(state.get("params", {})),
                "params_delta": new_params,
                "state_after": _dialog_state_preview(state),
                "response_text": response,
                "response_len": len(response),
                "buttons": [],
                "reason_layer": reason_layer_meta,
                "stage_presenter": stage_presenter_meta,
                "duration_ms": duration_ms,
                "is_error": True,
                "error": error_text or "safe_upstream_fallback",
                "cost": {
                    "search_usd": round(s_cost, 6) or None,
                    "chat_usd": round(c_cost, 6) or None,
                    "total_usd": round(s_cost + c_cost, 6) or None,
                    "search_tokens_used": s_used or None,
                    "chat_tokens_used": c_used or None,
                    "total_tokens_used": (s_used + c_used) or None,
                },
            })
            await update.message.reply_text(_to_html(response), parse_mode="HTML", reply_markup=markup)
            return

        response = _prepare_response_text(_strip_markdown(response))
        response = _strip_unsupported_complex_claims(response)
        response = _strip_unrequested_live_data_cta(response, text)
        response = _soften_layout_overclaim(response)
        response = _strip_rejected_options_from_response(response, state)
        scene_meta = await scene_classifier.classify_scene(
            await client.ensure_session(),
            user_text=text,
            search_response=search_meta.get("_response_text", ""),
            memory={
                "params": state.get("params", {}),
                "selected_option": state.get("selected_option"),
                "last_options": state.get("last_options", [])[:3],
            },
            draft_response=response,
        )
        style_scene = str(scene_meta.get("scene") or "default_safe_reply")
        response = await _maybe_style_text(
            client,
            response,
            intent="main_search",
            scene=style_scene,
            scene_rules=get_scene_rules(style_scene),
            context=json.dumps(
                {
                    "params": state.get("params", {}),
                    "query": text,
                    "search_response": search_meta.get("_response_text", ""),
                },
                ensure_ascii=False,
            ),
        )
        response = _strip_unsupported_complex_claims(response)
        response = _strip_unrequested_live_data_cta(response, text)
        response = _soften_layout_overclaim(response)
        response = _strip_rejected_options_from_response(response, state)

        # H013/H028: заполним last_result/options, затем берём buttons[] из chat-контракта.
        _refresh_search_state(state, search_meta)
        state["visible_options"] = _visible_options_from_chat_or_response(chat_meta, response, state.get("last_options") or [])
        state["numeric_choice_policy"] = _numeric_choice_policy_from_response(response, state.get("visible_options") or [])
        if state.get("last_result", {}).get("found"):
            state["turns_after_results"] = int(state.get("turns_after_results") or 0) + 1
        scenario = _infer_scenario(state, search_meta)

        stage_presenter_meta: dict[str, Any] = {"enabled": STAGE_PRESENTER_ENABLED}
        if STAGE_PRESENTER_ENABLED:
            stage_scenario = _reason_layer_scenario(text, state.get("params", {}))
            stage_options = state.get("last_options") or []
            if len(stage_options) >= 2:
                sales_benefits, sales_meta = await _sales_phrases_for_stage(client, stage_options, stage_scenario)
                response = _render_stage_first_list(stage_options, stage_scenario, sales_benefits)
                state["visible_options"] = stage_options[:3]
                state["numeric_choice_policy"] = _numeric_choice_policy_from_response(response, state.get("visible_options") or [])
                if OPTION_ENRICHMENT_ENABLED:
                    asyncio.create_task(_prefetch_options_enrichment(client, state, stage_options[:3], stage_scenario))
                stage_presenter_meta = {
                    "enabled": True,
                    "applied": True,
                    "stage": "first_list",
                    "scenario": stage_scenario,
                    "options_count": len(state.get("visible_options") or []),
                    "sales_phrase": sales_meta,
                    "enrichment_prefetch": {"enabled": OPTION_ENRICHMENT_ENABLED, "scheduled": OPTION_ENRICHMENT_ENABLED, "count": min(len(stage_options), 3)},
                }
            elif len(stage_options) == 1:
                option_for_answer, enrichment_meta = await _get_or_fetch_enriched_option(client, state, stage_options[0], stage_scenario)
                sales_benefits, sales_meta = await _sales_phrases_for_stage(client, [option_for_answer], stage_scenario)
                response = _render_stage_selected_object(option_for_answer, stage_scenario, sales_benefits.get(1))
                state["visible_options"] = []
                state["selected_option"] = option_for_answer
                stage_presenter_meta = {
                    "enabled": True,
                    "applied": True,
                    "stage": "selected_object",
                    "scenario": stage_scenario,
                    "options_count": 1,
                    "sales_phrase": sales_meta,
                    "enrichment": enrichment_meta,
                }
            elif "?" in response and response.count("?") > 1:
                response = _render_stage_clarification(stage_scenario)
                state["visible_options"] = []
                stage_presenter_meta = {
                    "enabled": True,
                    "applied": True,
                    "stage": "clarification",
                    "scenario": stage_scenario,
                    "options_count": 0,
                    "sales_phrase": {"enabled": SALES_PHRASE_ENABLED, "applied": False},
                }

        # H001/reason-layer MVP: optional guarded rewrite for first 2-3 option lists.
        # It is disabled by default and falls back to current response on any issue.
        reason_layer_meta: dict[str, Any] = {"enabled": REASON_LAYER_ENABLED}
        if not stage_presenter_meta.get("applied"):
            response, reason_layer_meta = await _maybe_apply_reason_layer(
                client,
                user_text=text,
                state=state,
                fallback_response=response,
            )
            if reason_layer_meta.get("applied"):
                state["visible_options"] = state.get("last_options", [])[:3]
                state["numeric_choice_policy"] = _numeric_choice_policy_from_response(response, state.get("visible_options") or [])

        duration_ms = int((time.monotonic() - t0) * 1000)

        response = _append_model_stats_footer(
            response,
            state=state,
            chat_meta=chat_meta,
            duration_ms=duration_ms,
            reason_layer_meta=reason_layer_meta,
        )

        kb_rows = _markup_from_chat_buttons(chat_meta, state, response, scenario)
        markup: dict | None = {"inline_keyboard": kb_rows} if kb_rows else None
        state["last_buttons"] = kb_rows
        if state.get("visible_options"):
            _remember_bot_response(state, response, offer_type="choose_option", answer_kind="options_summary")
        else:
            _remember_bot_response(state, response, offer_type="", answer_kind="main_search")

        # Experiment Loop: фиксируем вход + результат
        _log_event({
            "kind": "user_message",
            "uid": uid,
            "user_text": text,
            "dialog_intent": "main_search",
            "search_model": state["search_model"],
            "chat_model": state["chat_model"],
            "mcp": state["mcp"],
            "search_response": search_meta.get("_response_text", ""),
            "params_before": params_before,
            "params_after": dict(state.get("params", {})),
            "params_delta": new_params,
            "state_after": _dialog_state_preview(state),
            "response_text": response,
            "response_len": len(response),
            "buttons": _button_log_preview(kb_rows),
            "reason_layer": reason_layer_meta,
            "stage_presenter": stage_presenter_meta,
            "duration_ms": duration_ms,
            "is_error": response.startswith(("❌", "⏱️")),
            "error": error_text,
            "cost": {
                "search_usd": round(s_cost, 6) or None,
                "chat_usd": round(c_cost, 6) or None,
                "total_usd": round(s_cost + c_cost, 6) or None,
                "search_tokens_used": s_used or None,
                "chat_tokens_used": c_used or None,
                "total_tokens_used": (s_used + c_used) or None,
            },
        })

        # H009: операторская воронка теперь без кнопок — просим номер обычным текстом.
        wants_operator = (
            any(trig in text.lower() for trig in ("оператор", "живой человек", "менеджер", "перезвоните"))
            or ("передам" in response.lower() and "оператор" in response.lower())
        )
        if wants_operator and not error_text:
            state["awaiting_phone"] = True
            await update.message.reply_text("Если хотите, напишите номер для связи текстом — передам запрос оператору с контекстом диалога.")

        # Telegram лимит 4096 символов на сообщение.
        # H027: обычный короткий ответ тоже обязан заменить индикатор поиска.
        if len(response) > 4000:
            chunks = [response[i:i + 3900] for i in range(0, len(response), 3900)]
            await indicator.edit_text(_to_html(chunks[0]), parse_mode="HTML")
            for chunk in chunks[1:-1]:
                await update.message.reply_text(_to_html(chunk), parse_mode="HTML")
            if len(chunks) > 1:
                await update.message.reply_text(_to_html(chunks[-1]), parse_mode="HTML", reply_markup=markup)
            LOGGER.info("User %d: final response sent in %d chunks", uid, len(chunks))
        else:
            await indicator.edit_text(_to_html(response), parse_mode="HTML", reply_markup=markup)
            LOGGER.info("User %d: final response sent", uid)

    async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.contact:
            return

        uid = update.effective_user.id if update.effective_user else 0
        state = user_state.setdefault(uid, _default_state())
        phone = _extract_phone_from_text(update.message.contact.phone_number)
        if phone:
            was_awaiting = bool(state.pop("awaiting_phone", None))
            had_context = was_awaiting or _has_phone_capture_context(state)
            _log_event({
                "kind": "phone_captured",
                "uid": uid,
                "source": "contact",
                "was_awaiting_phone": was_awaiting,
                "had_phone_context": had_context,
                "state_after": _dialog_state_preview(state),
                **_phone_log_meta(phone),
            })
            await update.message.reply_text(_phone_captured_farewell(), reply_markup=ReplyKeyboardRemove())
            return

        await update.message.reply_text(
            "Контакт пришёл без понятного номера. Напишите телефон текстом в формате +7XXXXXXXXXX.",
        )

    builder = Application.builder().token(TELEGRAM_BOT_TOKEN)
    if TELEGRAM_API_BASE_URL:
        builder = builder.base_url(TELEGRAM_API_BASE_URL)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("model", model_command))
    app.add_handler(CommandHandler("mcp", mcp_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("hisotry", history_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(~filters.COMMAND & ~filters.CONTACT, handle_message))

    print("🤖 nmbot запущен. Нажми Ctrl+C для остановки.")
    LOGGER.info("nmbot started")

    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        asyncio.run(client.close())


if __name__ == "__main__":
    main()
