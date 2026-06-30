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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import aiohttp

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scene_classifier
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

SEARCH_SYSTEM_PROMPT = _load_prompt("search_v1")
CHAT_SYSTEM_PROMPT = _load_prompt("chat_v1")

AVAILABLE_MODELS = [
    "google/gemini-3.1-flash-lite-preview",
    "google/gemini-2.5-flash",
    "deepseek/deepseek-v4-flash",
    "anthropic/claude-3-haiku",
    "openai/gpt-4o-mini",
]

STYLE_TOOL_ENABLED: Final[bool] = os.getenv("NMBOT_TEXT_STYLE_TOOL", "1") != "0"
STYLE_TOOL_MODEL: Final[str] = os.getenv("NMBOT_STYLE_MODEL", "google/gemini-3.1-flash-lite-preview")

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
                return _safe_user_error_message(), {}

        task_id = task.get("id")
        if not task_id:
            LOGGER.error("gateway create task returned no id: payload=%s", _safe_json_preview(task))
            return _safe_user_error_message(), {}

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
                        return _safe_user_error_message(error), metadata

                    if response_text:
                        return response_text, metadata
                    LOGGER.error(
                        "gateway task returned empty response: task_id=%s status=%s result=%s",
                        task_id,
                        status,
                        _safe_json_preview(result_obj),
                    )
                    return _safe_user_error_message(), metadata

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
    def _parse_chat_json(response_text: str) -> tuple[str, dict, list[dict]]:
        try:
            s = response_text.find("{")
            e = response_text.rfind("}") + 1
            if s >= 0 and e > s:
                data = json.loads(response_text[s:e])
                resp_text = data.get("response", response_text)
                params = data.get("params", {})
                return (
                    resp_text,
                    params if isinstance(params, dict) else {},
                    [],  # buttons намеренно игнорируем: Ирина отвечает живым текстом без inline-кнопок.
                )
        except json.JSONDecodeError:
            pass
        return response_text, {}, []

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
        # H007-A: strip markdown-обёртку ДО парсинга JSON, чтобы _parse_chat_json
        # работал с чистым текстом (без ```json ... ```).
        chat_result = _strip_markdown(chat_result)
        response_text, chat_params, chat_buttons = self._parse_chat_json(chat_result)
        chat_meta = {**chat_meta, "_buttons": chat_buttons}
        retries = 0
        # Признак невалидного JSON: не нашли JSON response/buttons/params, и это не служебная ошибка.
        parsed_ok = response_text != chat_result or bool(chat_params) or bool(chat_buttons)
        is_invalid = not parsed_ok and not response_text.startswith("❌") and not response_text.startswith("⏱️")
        while is_invalid and retries < 2:
            retries += 1
            chat_result, chat_meta = await self._run_gateway_request(chat_request_data, headers, timeout)
            chat_result = _strip_markdown(chat_result)  # H007-A
            response_text_new, chat_params, chat_buttons = self._parse_chat_json(chat_result)
            chat_meta = {**chat_meta, "_buttons": chat_buttons}
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
    return _format_numbered_list_spacing(raw)


# H013: дефолтный state пользователя + динамические quick-actions
def _default_state() -> dict[str, Any]:
    return {
        "search_model": SEARCH_MODEL,
        "chat_model": CHAT_MODEL,
        "mcp": True,
        "params": {},
        "last_result": {},  # {found, exact_count, near_count, scenario}
        "last_options": [],  # H016: последние варианты для «второй»/«подешевле»
        "selected_option": None,  # PRODUCT_TZ: выбранный ЖК/вариант для follow-up и operator_context
        "turns_after_results": 0,
        "last_search_response": {},  # H026: полный структурированный MCP/search JSON для follow-up без нового MCP
        "asked_questions": [],  # список заданных уточнений (чтобы не повторять)
        "last_buttons": [],  # последние реально отправленные inline-кнопки для полного dialog log
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
        price = item.get("price") or item.get("min_price") or item.get("price_range") or item.get("cost")
        opt = {
            "idx": len(options) + 1,
            "name": item.get("name") or item.get("title") or "вариант",
            "location": item.get("location") or item.get("district") or "",
            "price": price or "",
            "price_min": _price_min(price),
            "finishing": item.get("finishing") or item.get("renovation") or "",
            "area": item.get("area") or item.get("square") or item.get("площадь") or "",
            "ready": item.get("ready") or item.get("status") or item.get("deadline") or "",
            "developer": item.get("developer") or item.get("dev") or item.get("застройщик") or "",
            "metro": item.get("metro") or "",
            "why_close": item.get("why_close") or "",
            "raw": item,
        }
        options.append(opt)
    return options[:8]


def _resolve_dialog_intent(text: str, state: dict) -> dict[str, Any]:
    """H016: быстрый resolver follow-up сообщений, которые ссылаются на прошлый список."""
    t = text.lower().replace("ё", "е")
    options = state.get("last_options") or []
    selected = state.get("selected_option")

    if selected and options and re.search(r"(похож|сравн|друг|еще|ещё|альтернатив)", t):
        def _compact_name(value: Any) -> str:
            return re.sub(r"[^а-яa-z0-9]+", " ", str(value or "").lower().replace("ё", "е")).strip()

        selected_name = _compact_name(selected.get("name"))
        other_options = [
            option for option in options
            if _compact_name(option.get("name")) != selected_name
        ]
        return {"intent": "compare_others", "options": other_options[:3]}

    if selected and _needs_operator_for_selected_option(t):
        return {"intent": "operator_for_selected", "option": selected}

    # PRODUCT_TZ: «да», «интересно», «подходит» после выбора варианта — это подтверждение
    # интереса к выбранному ЖК, а не повод заново спрашивать бюджет/комнаты.
    # Но «подробнее»/«расскажи» после уже раскрытой карточки ведём к оператору выше:
    # новых подтверждённых данных в памяти нет, повторять ту же карточку нельзя.
    if selected and re.search(r"(^|\s)(да|интересно|подходит|хочу|ок|ага)(\s|$)", t):
        return {"intent": "select_option", "option": selected}

    if not options:
        return {"intent": "new_search"}

    # H026: follow-up по названию ЖК из памяти: «что по белой даче»,
    # «расскажи про дюну» — отвечаем из last_options, без нового MCP.
    compact_t = re.sub(r"[^а-яa-z0-9]+", " ", t).strip()
    for option in options:
        name = str(option.get("name") or "").lower().replace("ё", "е")
        name_words = [w for w in re.sub(r"[^а-яa-z0-9]+", " ", name).split() if len(w) >= 4]
        # Игнорируем общие слова, чтобы «жк» не матчило всё подряд.
        name_words = [w for w in name_words if w not in ("жилой", "квартал", "комплекс")]
        if name_words and any(w in compact_t for w in name_words):
            return {"intent": "select_option", "option": option}

    ordinal = {
        "перв": 1, "1": 1, "один": 1,
        "втор": 2, "2": 2, "два": 2,
        "трет": 3, "3": 3, "три": 3,
    }
    for key, idx in ordinal.items():
        if re.search(rf"(^|\s){re.escape(key)}", t):
            if 1 <= idx <= len(options):
                return {"intent": "select_option", "option": options[idx - 1]}

    if "дешев" in t or "подеш" in t:
        sorted_opts = sorted(options, key=lambda o: o.get("price_min") or 10**18)
        if "ремонт" in t or "отдел" in t:
            with_finish = [
                o for o in sorted_opts
                if "отдел" in str(o.get("finishing", "")).lower()
                and "без отдел" not in str(o.get("finishing", "")).lower()
            ]
            sorted_opts = with_finish or sorted_opts
        return {"intent": "sort_price_asc", "options": sorted_opts[:3]}

    if "ремонт" in t or "отдел" in t:
        with_finish = [
            o for o in options
            if "отдел" in str(o.get("finishing", "")).lower()
            and "без отдел" not in str(o.get("finishing", "")).lower()
        ]
        if with_finish:
            return {"intent": "filter_finish", "options": with_finish[:3]}

    return {"intent": "new_search"}


def _needs_operator_for_selected_option(text_l: str) -> bool:
    """После выбора ЖК живые данные не придумываем: наличие/бронь/показ/ипотека — к оператору."""
    triggers = (
        "налич", "актуаль", "брон", "заброни", "показ", "посмотреть",
        "ипотек", "ставк", "скид", "торг", "этаж", "корпус", "квартир",
        "планиров", "платеж", "платёж", "первонач", "звон", "оператор", "менеджер",
        "подробнее", "расскажи", "детал", "подробн",
    )
    return any(trig in text_l for trig in triggers)


def _format_operator_handoff_for_option(option: dict[str, Any]) -> str:
    name = option.get("name") or "этот вариант"
    known: list[str] = []
    if option.get("price"):
        known.append(f"по цене вижу {option['price']}")
    if option.get("area"):
        known.append(f"по площади вижу {option['area']}")
    if option.get("finishing"):
        known.append(f"по отделке: {option['finishing']}")
    if option.get("ready"):
        known.append(f"по готовности: {option['ready']}")
    known_text = "; ".join(known) if known else "по нему есть только короткая карточка без дополнительных деталей"
    return (
        f"По {name} {known_text}.\n\n"
        "Больше подтверждённой информации прямо сейчас не добавлю, чтобы не выдумывать. "
        "Актуальное наличие, бронь, этаж, корпус, скидки и условия лучше проверить у оператора.\n\n"
        "Хотите оставить номер для связи?"
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


def _phone_log_meta(phone: str) -> dict[str, Any]:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return {"phone_len": len(digits), "phone_last4": digits[-4:] if len(digits) >= 4 else ""}


def _build_known_option_prompt(option: dict[str, Any], client_request: str) -> str:
    """Контекст для LLM: раскрыть выбранный ЖК по уже известным данным, без нового поиска и выдумок."""
    safe_option = {
        key: value
        for key, value in option.items()
        if key in {"idx", "name", "location", "price", "area", "finishing", "ready", "developer", "metro", "why_close"}
        and not _looks_missing(value)
    }
    return (
        "Клиент уже выбрал вариант из предыдущего списка. Новый широкий поиск не нужен.\n"
        f"Запрос/действие клиента: {client_request}\n\n"
        "Доступные подтверждённые данные по выбранному варианту:\n"
        f"{json.dumps(safe_option, ensure_ascii=False, indent=2)}\n\n"
        "Сформулируй новый живой ответ Ирины по этим данным: расскажи максимум полезного, что подтверждено в карточке. "
        "Не повторяй дословно предыдущую карточку. Не придумывай метро, инфраструктуру, скидки, ипотеку, наличие, бронь, этажи или корпуса. "
        "Так как клиент уже выбрал конкретный ЖК и попросил подробнее, в конце мягко предложи оставить контакт, чтобы оператор проверил актуальные квартиры, наличие и условия. "
        "Если клиент просит актуальное наличие, бронь, конкретную квартиру, этаж, корпус, ипотеку, скидку или показ — не подтверждай это сама, а объясни, что это проверит оператор. "
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
        or text in {"нет", "не указан", "не указано", "информация отсутствует", "none", "null"}
        or "отсутств" in text
    )


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
        return "он может быть хорошей отправной точкой по бюджету среди найденных вариантов"
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
        return "хорошая отправная точка для семейного выбора: сразу понятен бюджет входа"
    if not _looks_missing(option.get("location")):
        return "интересный вариант для семьи в этой локации — можно подобрать подходящий формат квартиры"
    return "вариант стоит рассмотреть для семьи: можно подобрать подходящую квартиру и уточнить детали покупки"


def _format_option_response(option: dict[str, Any], purpose: Any = None) -> str:
    name = option.get("name") or "этот вариант"
    intro = f"{_option_ordinal(option.get('idx'))} вариант — {name}."
    facts: list[str] = []
    if not _looks_missing(option.get("location")):
        facts.append(f"локация — {option['location']}")
    if not _looks_missing(option.get("price")):
        facts.append(f"цена — {option['price']}")
    if not _looks_missing(option.get("area")):
        facts.append(f"площадь — {option['area']}")
    if not _looks_missing(option.get("finishing")):
        facts.append(f"отделка — {option['finishing']}")
    if not _looks_missing(option.get("ready")):
        facts.append(f"готовность — {option['ready']}")

    if facts:
        fact_text = "По нему вижу: " + "; ".join(facts[:3]) + "."
    else:
        fact_text = "По нему есть только короткая карточка без дополнительных подтверждённых деталей."
    benefit = _option_benefit(option)
    nuance = f" Важно: {option['why_close']}." if not _looks_missing(option.get("why_close")) else ""
    family_note = ""
    if str(purpose or "").lower() == "family":
        family_note = f" {_family_reason_from_facts(option).capitalize()}."
    body = f"{fact_text}\n\nПоэтому {benefit}.{family_note}{nuance}"
    return f"{intro}\n\n{body}\n\nХотите сравнить этот вариант с похожими?"


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
        price = f", {o['price']}" if o.get("price") else ""
        loc = f" ({o['location']})" if o.get("location") else ""
        finish = f", отделка: {o['finishing']}" if o.get("finishing") else ""
        chunks.append(f"{idx}. {o['name']}{loc}{price}{finish}")
    return _format_numbered_list_spacing(f"{lead}:\n" + "\n".join(chunks) + f"\n{question}")


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
    state["last_options"] = _extract_options(search_text)
    facts = search_resp.get("facts", []) if isinstance(search_resp, dict) else []
    near = search_resp.get("near", []) if isinstance(search_resp, dict) else []
    state["last_result"] = {
        "found": bool(facts) or bool(near),
        "exact_count": len(facts) if isinstance(facts, list) else 0,
        "near_count": len(near) if isinstance(near, list) else 0,
        "geo_mismatch": bool(search_resp.get("missing") and not facts and not near and state.get("params", {}).get("district") in (None,)),
    }


# ── Experiment Loop logging ─────────────────────────────────


def _log_event(event: dict[str, Any]) -> None:
    """Append one JSONL line to logs/dialogs-YYYY-MM-DD.jsonl.

    Schema see docs/EXPERIMENTS.md.
    Best-effort: never raise into the bot's request path.
    """
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = LOGS_DIR / f"dialogs-{datetime.now(timezone.utc).date().isoformat()}.jsonl"
        event.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
        event.setdefault("h_id", ACTIVE_H_ID)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:  # pragma: no cover - logging must never break the bot
        LOGGER.warning("Failed to write dialog log: %s", e)


# ── Telegram Bot ────────────────────────────────────────────


def build_menu_markup(models: list[str], current: str, mcp_on: bool) -> list[list[dict]]:
    kb: list[list[dict]] = []
    for m in models:
        name = m.split("/")[-1]
        marker = " ✅" if m == current else ""
        kb.append([{"text": f"{name}{marker}", "callback_data": f"model:{m}"}])
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
        kb = build_menu_markup(AVAILABLE_MODELS, state["search_model"], state["mcp"])
        await update.message.reply_text(
            "Выбери модель поиска:",
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
        elif query.data.startswith("model:"):
            state["search_model"] = query.data[6:]
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
        if not update.message or not update.message.text:
            return

        uid = update.effective_user.id if update.effective_user else 0
        state = user_state.setdefault(uid, _default_state())
        text = update.message.text.strip()

        # H009: если клиент только что нажал «Связаться с оператором», трактуем текст как номер телефона
        if state.pop("awaiting_phone", None):
            phone = _normalize_phone(text)
            if 10 <= len(phone) <= 15:
                _log_event({"kind": "phone_captured", "uid": uid, "source": "text", **_phone_log_meta(phone)})
                await update.message.reply_text(_phone_captured_farewell(), reply_markup=ReplyKeyboardRemove())
                return
            else:
                await update.message.reply_text("Похоже, это не номер. Напишите телефон в формате +7XXXXXXXXXX или просто продиктуйте цифрами.")
                return

        if not text:
            return

        params_before = dict(state.get("params", {}))

        # H016: короткие follow-up сообщения («второй», «подешевле») решаем из памяти,
        # без нового общего поиска через Overmind.
        dialog_intent = _resolve_dialog_intent(text, state)
        if dialog_intent.get("intent") == "select_option":
            state["selected_option"] = dialog_intent["option"]
            response = _prepare_response_text(_format_option_response(dialog_intent["option"], state.get("params", {}).get("purpose")))
            response = await _maybe_style_text(
                client,
                response,
                intent="select_option",
                scene="followup_selected_option",
                context=str(dialog_intent["option"].get("name") or ""),
            )
            idx = int(dialog_intent["option"].get("idx") or 1)
            kb_rows = _selected_option_rows(idx)
            _log_event({
                "kind": "user_message",
                "uid": uid,
                "user_text": text,
                "dialog_intent": "select_option",
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
            response = _prepare_response_text(_format_operator_handoff_for_option(option))
            response = await _maybe_style_text(
                client,
                response,
                intent="operator_handoff",
                scene="followup_operator",
                context=str(option.get("name") or ""),
            )
            kb_rows = [[{"text": "📞 Связаться с оператором", "callback_data": "action:operator"}]]
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
            response, new_params, search_meta, chat_meta = await client.ask(
                query=text, search_model=state["search_model"], chat_model=state["chat_model"], use_mcp=state["mcp"],
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

        response = _prepare_response_text(_strip_markdown(response))
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

        # H013/H028: заполним last_result/options, затем берём buttons[] из chat-контракта.
        _refresh_search_state(state, search_meta)
        if state.get("last_result", {}).get("found"):
            state["turns_after_results"] = int(state.get("turns_after_results") or 0) + 1
        scenario = _infer_scenario(state, search_meta)
        kb_rows = _markup_from_chat_buttons(chat_meta, state, response, scenario)
        markup: dict | None = {"inline_keyboard": kb_rows} if kb_rows else None
        state["last_buttons"] = kb_rows

        # Experiment Loop: фиксируем вход + результат
        _log_event({
            "kind": "user_message",
            "uid": uid,
            "user_text": text,
            "search_model": state["search_model"],
            "chat_model": state["chat_model"],
            "mcp": state["mcp"],
            "params_before": params_before,
            "params_after": dict(state.get("params", {})),
            "params_delta": new_params,
            "state_after": _dialog_state_preview(state),
            "response_text": response,
            "response_len": len(response),
            "buttons": _button_log_preview(kb_rows),
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
        phone = _normalize_phone(update.message.contact.phone_number)
        if 10 <= len(phone) <= 15:
            was_awaiting = bool(state.pop("awaiting_phone", None))
            _log_event({
                "kind": "phone_captured",
                "uid": uid,
                "source": "contact",
                "was_awaiting_phone": was_awaiting,
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
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 nmbot запущен. Нажми Ctrl+C для остановки.")
    LOGGER.info("nmbot started")

    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        asyncio.run(client.close())


if __name__ == "__main__":
    main()
