#!/usr/bin/env python3
"""Sanitized callback summary input and deterministic summary fallback."""

from __future__ import annotations

import re
import asyncio
import json
from collections.abc import Callable
from typing import Any, Protocol


PHONE_RE = re.compile(r"(?:\+?\d[\s()\-.]*){10,15}")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
NAME_PHRASE_RE = re.compile(r"\b(?:меня\s+зовут|мо[её]\s+имя|имя)\s+([А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z-]{1,40})", re.I)
JIVO_KEY_RE = re.compile(r"jivo|client_id|chat_id|site_id|sender|payload|raw|token|secret|phone|contact", re.I)


class SummaryProvider(Protocol):
    def summarize(self, snapshot: dict[str, Any]) -> str: ...


def _redact_text(text: Any, forbidden: tuple[str, ...] = ()) -> str:
    value = str(text or "")[:1000]
    value = PHONE_RE.sub("", value)
    value = EMAIL_RE.sub("", value)
    value = NAME_PHRASE_RE.sub("", value)
    for item in forbidden:
        item = str(item or "").strip()
        if item:
            value = value.replace(item, "")
    return " ".join(value.split())[:500]


def _sanitize(value: Any, *, depth: int = 0, forbidden: tuple[str, ...] = ()) -> Any:
    if depth > 5:
        return None
    if isinstance(value, str):
        return _redact_text(value, forbidden)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return None if len(str(abs(value))) >= 10 else value
    if isinstance(value, float):
        return value
    if isinstance(value, list):
        cleaned = [_sanitize(item, depth=depth + 1, forbidden=forbidden) for item in value[:10]]
        return [item for item in cleaned if item not in (None, "", [], {})]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)[:80]
            if JIVO_KEY_RE.search(key_text):
                continue
            cleaned = _sanitize(item, depth=depth + 1, forbidden=forbidden)
            if cleaned not in (None, "", [], {}):
                result[key_text] = cleaned
        return result
    return _redact_text(value, forbidden)


def build_sanitized_summary_input(record: dict[str, Any]) -> dict[str, Any]:
    contact = record.get("contact") if isinstance(record.get("contact"), dict) else {}
    forbidden = (
        str(contact.get("phone") or ""),
        str(contact.get("name") or ""),
        str(record.get("lead_ref") or ""),
        str(record.get("session_ref") or ""),
        str(record.get("event_ref") or ""),
    )
    source = record.get("summary_input") if isinstance(record.get("summary_input"), dict) else record.get("context")
    sanitized = _sanitize(source if isinstance(source, dict) else {}, forbidden=forbidden)
    return sanitized if isinstance(sanitized, dict) else {}


def deterministic_summary_fallback(snapshot: dict[str, Any]) -> str:
    params = snapshot.get("params") if isinstance(snapshot.get("params"), dict) else {}
    selected = snapshot.get("selected_option") if isinstance(snapshot.get("selected_option"), dict) else {}
    options = snapshot.get("current_options") if isinstance(snapshot.get("current_options"), list) else []
    parts: list[str] = []
    if selected.get("name"):
        parts.append(f"Клиент просит связаться по варианту: {selected['name']}.")
    elif options:
        names = [str(opt.get("name")) for opt in options if isinstance(opt, dict) and opt.get("name")]
        if names:
            parts.append("Клиент просит связаться по текущей подборке: " + ", ".join(names[:3]) + ".")
    if params:
        safe_pairs = [f"{k}: {v}" for k, v in sorted(params.items()) if v not in (None, "", [], {})]
        if safe_pairs:
            parts.append("Известные параметры: " + "; ".join(safe_pairs[:6]) + ".")
    if snapshot.get("last_bot_question"):
        parts.append("Последний контекст: " + str(snapshot["last_bot_question"])[:180] + ".")
    if not parts:
        parts.append("Клиент оставил заявку на обратный звонок; детали нужно уточнить в переписке.")
    return " ".join(parts)[:1000]


class DeterministicSummaryProvider:
    def summarize(self, snapshot: dict[str, Any]) -> str:
        return deterministic_summary_fallback(snapshot)


def _sheet_safe_text(text: Any, *, limit: int = 1000) -> str:
    value = _redact_text(text, forbidden=())
    return " ".join(value.replace("\n", " ").replace("\r", " ").split())[:limit]


def _json_object_from_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        if first_nl > 0:
            raw = raw[first_nl + 1 :]
        if raw.endswith("```"):
            raw = raw[:-3].rstrip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _as_safe_list(value: Any, *, limit: int = 4) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:limit]:
        text = _sheet_safe_text(item, limit=180)
        if text:
            result.append(text)
    return result


def render_sheet_summary_from_client_card(data: dict[str, Any], snapshot: dict[str, Any]) -> str:
    parts: list[str] = []
    request = _sheet_safe_text(data.get("client_request_summary"), limit=280)
    if request:
        parts.append(request.rstrip("."))
    selected = _sheet_safe_text(data.get("selected_complex"), limit=120)
    if selected:
        parts.append(f"Интерес: {selected}")
    criteria = _as_safe_list(data.get("client_criteria"), limit=5)
    if criteria:
        parts.append("Критерии: " + "; ".join(criteria))
    discussed = _as_safe_list(data.get("discussed_options"), limit=4)
    if discussed:
        parts.append("Обсуждали: " + "; ".join(discussed))
    context = _as_safe_list(data.get("important_context"), limit=3)
    if context:
        parts.append("Контекст: " + "; ".join(context))
    tasks = _as_safe_list(data.get("operator_tasks"), limit=4)
    unknowns = _as_safe_list(data.get("unknowns"), limit=3)
    if tasks or unknowns:
        parts.append("Оператору: " + "; ".join([*tasks, *unknowns]))
    text = ". ".join(part for part in parts if part).strip()
    if text:
        return _sheet_safe_text(text.rstrip(".") + ".")
    return deterministic_summary_fallback(snapshot)


def _build_gateway_summary_prompt(snapshot: dict[str, Any]) -> str:
    safe_snapshot = _sanitize(snapshot)
    if not isinstance(safe_snapshot, dict):
        safe_snapshot = {}
    payload = {
        "dialog_window": safe_snapshot.get("dialog_window") or [],
        "params": safe_snapshot.get("params") or {},
        "selected_option": safe_snapshot.get("selected_option") or {},
        "visible_options": safe_snapshot.get("visible_options") or safe_snapshot.get("current_options") or [],
        "last_options": safe_snapshot.get("last_options") or [],
        "operator_context": safe_snapshot.get("operator_context") or safe_snapshot.get("scenario_context") or {},
        "last_bot_question": safe_snapshot.get("last_bot_question") or "",
    }
    input_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)[:6000]
    return (
        "Сделай короткое summary заявки клиента для оператора по недвижимости.\n"
        "Используй только безопасный INPUT ниже. В INPUT не должно быть имени, телефона, Jivo id, payload или token; "
        "если вдруг видишь такие данные — не повторяй их.\n"
        "Не выдумывай цены, сроки, корпуса, школы, метро и условия. Если данных не хватает, добавь это в operator_tasks/unknowns.\n"
        "Верни строгий JSON без markdown в формате:\n"
        '{"client_request_summary":"1-3 предложения", "client_criteria":["..."], "selected_complex":"", '
        '"discussed_options":["..."], "operator_tasks":["..."], "important_context":["..."], "unknowns":["..."]}\n\n'
        f"INPUT:\n{input_json}"
    )[:7500]


class GatewayOvermindSummaryProvider:
    """Synchronous wrapper around OvermindClient.summarize_client_card.

    The worker is a sync CLI, so production uses asyncio.run only when no event
    loop is already running. Client import/construction is lazy to keep config
    validation and --diagnose network-free.
    """

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any] | None = None,
        model: str | None = None,
        timeout: int = 180,
    ) -> None:
        self.client_factory = client_factory
        self.model = model
        self.timeout = timeout

    def _make_client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory()
        from chat_tester_bot import OvermindClient  # lazy: imports aiohttp/config only for real summarize

        return OvermindClient()

    async def _summarize_async(self, snapshot: dict[str, Any]) -> str:
        safe_snapshot = _sanitize(snapshot)
        if not isinstance(safe_snapshot, dict):
            safe_snapshot = {}
        prompt = _build_gateway_summary_prompt(safe_snapshot)
        client = self._make_client()
        try:
            kwargs: dict[str, Any] = {"prompt": prompt, "timeout": self.timeout}
            if self.model:
                kwargs["model"] = self.model
            raw_summary, _meta = await client.summarize_client_card(**kwargs)
            parsed = _json_object_from_text(str(raw_summary or ""))
            if parsed:
                return render_sheet_summary_from_client_card(parsed, safe_snapshot)
            text = _sheet_safe_text(raw_summary)
            return text or deterministic_summary_fallback(safe_snapshot)
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    def summarize(self, snapshot: dict[str, Any]) -> str:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._summarize_async(snapshot))
        raise RuntimeError("gateway_summary_provider_requires_sync_context")
