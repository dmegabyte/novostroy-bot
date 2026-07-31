from __future__ import annotations

import json
import re
from typing import Any


JSON_ENVELOPE_KEYS = {"data", "message"}
NUMBERED_BLOCK_RE = re.compile(r"(?m)^\s*(\d+)\.\s*ЖК\b")
DECIMAL_DOT_RE = re.compile(r"\d\.\d")
SENTENCE_END_RE = re.compile(r"[.!?…]")
FAMILY_GROUNDED_RE = re.compile(r"(подтвержд|есть|рядом|данн|удобн|плюс|спокой).{0,80}\b(семь|дет|школ|сад|двор|парк|безопас|спорт|площадк|коляс|инфраструктур)\w*|\b(семь|дет|школ|сад|двор|парк|безопас|спорт|площадк|коляс|инфраструктур)\w*.{0,80}(подтвержд|есть|рядом|данн|удобн|плюс|спокой)", re.IGNORECASE | re.DOTALL)
FAMILY_MISSING_RE = re.compile(r"(семейн\w+|для семьи|детск\w+|школ\w+|сад\w+|двор\w+|парк\w+).{0,80}(не подтвержд|нет подтвержд|не нашла подтвержд|не указа)", re.IGNORECASE | re.DOTALL)


def check_client_ux(
    text: Any,
    *,
    expected_blocks: int | None = None,
    family_query: bool = False,
) -> dict[str, Any]:
    """Small deterministic gate for V4 client-visible text."""

    value = str(text or "")
    codes: list[str] = []
    stripped = value.strip()
    if not stripped:
        codes.append("empty_text")
    if _looks_like_json_envelope(stripped):
        codes.append("json_envelope")
    if "\\n" in value:
        codes.append("literal_backslash_n")
    if "я подобрал" in value.lower():
        codes.append("masculine_ya_podobral")
    question_marks = value.count("?")
    if question_marks != 1:
        codes.append("question_count_not_one")
    blocks = _numbered_blocks(value)
    block_count = len(blocks)
    if expected_blocks is not None and block_count != expected_blocks:
        codes.append("block_count_mismatch")
    for index, block in enumerate(blocks, start=1):
        if _sentence_count(_drop_question_sentences(block)) < 2:
            codes.append(f"block_{index}_too_thin")
    family_evidence_text = _drop_question_sentences(value)
    if family_query and not (FAMILY_GROUNDED_RE.search(family_evidence_text) or FAMILY_MISSING_RE.search(family_evidence_text)):
        codes.append("family_grounding_missing")
    if DECIMAL_DOT_RE.search(value):
        codes.append("decimal_dot")
    return {
        "ok": not codes,
        "codes": codes,
        "metrics": {
            "question_marks": question_marks,
            "numbered_blocks": block_count,
            "expected_blocks": expected_blocks,
        },
    }


def _looks_like_json_envelope(text: str) -> bool:
    if not (text.startswith("{") and text.endswith("}")):
        return False
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and JSON_ENVELOPE_KEYS.issubset(parsed.keys())


def _numbered_blocks(text: str) -> list[str]:
    matches = list(NUMBERED_BLOCK_RE.finditer(text))
    blocks: list[str] = []
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        blocks.append(text[match.start():end].strip())
    return blocks


def _sentence_count(text: str) -> int:
    without_numbering = NUMBERED_BLOCK_RE.sub("ЖК", text, count=1)
    return len(SENTENCE_END_RE.findall(without_numbering))


def _drop_question_sentences(text: str) -> str:
    parts = re.split(r"(?<=[.!?…])\s+", text)
    return " ".join(part for part in parts if "?" not in part)
