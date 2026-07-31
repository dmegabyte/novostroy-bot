"""Безопасные границы для текста, который уходит клиенту."""

from __future__ import annotations

import re


_INTERNAL_MARKERS = re.compile(
    r"(?:pending[_-]scenario|pending[_-]followup|canonical[_-](?:error|valid|fields)|"
    r"dialog[_-]action|search[_-]policy|runtime[_-]stage|error[_-]code|"
    r"operator[_-]reason|(?:^|\W)(?:mcp|json|traceback|payload|planner|state)(?:$|\W))",
    re.IGNORECASE,
)

_PUBLIC_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brenovation\b", re.IGNORECASE), "с отделкой"),
)


def safe_client_question(value: str | None, fallback: str) -> str:
    """Возвращает вопрос planner-а или безопасную замену без internal enum."""

    text = str(value or "").strip()
    if not text or _INTERNAL_MARKERS.search(text):
        return fallback
    return text


def safe_client_message(value: str | None, fallback: str) -> str:
    """Не выпускает служебный текст в обычную клиентскую реплику."""

    text = str(value or "").strip()
    if not text or _INTERNAL_MARKERS.search(text):
        return fallback
    return text


def safe_client_output(value: str | None, fallback: str) -> str:
    """Последняя дешёвая граница для уже собранного клиентского ответа."""

    text = str(value or "").strip()
    for pattern, replacement in _PUBLIC_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    if not text or _INTERNAL_MARKERS.search(text):
        return fallback
    return text
