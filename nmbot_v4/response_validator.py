from __future__ import annotations

import json
import re
from typing import Any

from .contracts import V4Error


MAX_IDS = 9
MAX_MESSAGE_CHARS = 1000
EXACT_KEYS = {"data", "message"}
RUSSIAN_LETTER_RE = re.compile(r"[А-Яа-яЁё]")
HORIZONTAL_WHITESPACE_RE = re.compile(r"[^\S\r\n]+")
EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")


def validate_response_text(raw: Any) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not (text.startswith("{") and text.endswith("}")):
        raise V4Error("invalid_json_envelope")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise V4Error("invalid_json") from exc
    if not isinstance(parsed, dict):
        raise V4Error("not_object")
    if set(parsed) != EXACT_KEYS:
        raise V4Error("invalid_top_level_keys")
    data = parsed.get("data")
    message = parsed.get("message")
    if not isinstance(data, list):
        raise V4Error("data_not_list")
    if len(data) > MAX_IDS:
        raise V4Error("too_many_ids")
    ids: list[int] = []
    seen: set[int] = set()
    for item in data:
        if not isinstance(item, int) or isinstance(item, bool):
            raise V4Error("ads_id_not_positive_integer")
        if item <= 0:
            raise V4Error("ads_id_not_positive_integer")
        if item not in seen:
            seen.add(item)
            ids.append(item)
    if not isinstance(message, str):
        raise V4Error("message_not_string")
    clean_message = _normalize_message(message)
    if not clean_message:
        raise V4Error("empty_message")
    if len(clean_message) > MAX_MESSAGE_CHARS:
        raise V4Error("message_too_long")
    if not RUSSIAN_LETTER_RE.search(clean_message):
        raise V4Error("message_not_russian")
    return {"data": ids, "message": clean_message}


def _normalize_message(message: str) -> str:
    text = message.replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [HORIZONTAL_WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    return EXCESS_BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def compact_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
