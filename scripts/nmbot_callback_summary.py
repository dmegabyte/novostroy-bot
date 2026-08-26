#!/usr/bin/env python3
"""Sanitized callback summary input and deterministic summary fallback."""

from __future__ import annotations

import re
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
