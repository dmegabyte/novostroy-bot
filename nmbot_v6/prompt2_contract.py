"""Minimal plain-text output contract for Prompt 2."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from .contracts import ContractError
from .provider import TrustedMcpEnvelope

_INTERNAL = re.compile(
    r"\b(?:mcp|novostroym|provider|schema_version|scenario|answer_mode|mcp_request|"
    r"state[_ -]?patch|pending_phone|safe_context|option_refs|tool[_ -]?call|diagnostic)\b",
    re.IGNORECASE,
)
_PHONEISH = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)")


def _plain_text(value: str) -> str:
    value = value.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"(?m)^\s*#{1,6}\s*", "", value).strip()


def parse_prompt2(
    raw: str,
    evidence: TrustedMcpEnvelope | None = None,
    *,
    expected_mode: str = "normal",
) -> str:
    if not isinstance(raw, str):
        raise ContractError("Prompt 2 must return plain text")
    text = raw.strip()
    if not text:
        raise ContractError("Prompt 2 text is empty")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError("Prompt 2 must return JSON") from exc
    if not isinstance(value, dict) or set(value) != {"intro", "cards", "question"}:
        raise ContractError("Prompt 2 JSON shape is invalid")
    intro, cards, question = value["intro"], value["cards"], value["question"]
    if not isinstance(intro, str) or not isinstance(question, str):
        raise ContractError("Prompt 2 text fields are invalid")
    if not isinstance(cards, list) or len(cards) > 3:
        raise ContractError("Prompt 2 cards are invalid")
    indices, card_texts = [], []
    for card in cards:
        if not isinstance(card, dict) or set(card) != {"index", "text"}:
            raise ContractError("Prompt 2 card shape is invalid")
        if type(card["index"]) is not int or card["index"] < 0 or not isinstance(card["text"], str):
            raise ContractError("Prompt 2 card shape is invalid")
        indices.append(card["index"])
        card_texts.append(_plain_text(card["text"]))
    if len(set(indices)) != len(indices) or any(not text for text in card_texts):
        raise ContractError("Prompt 2 cards are invalid")
    intro, question = _plain_text(intro), _plain_text(question)
    combined: list[Mapping[str, object]] = []
    if evidence is not None and isinstance(evidence.safe_facts, Mapping):
        for group in (evidence.safe_facts.get("facts", []), evidence.safe_facts.get("near", [])):
            if isinstance(group, (list, tuple)):
                combined.extend(card for card in group if isinstance(card, Mapping))
        if not combined and isinstance(evidence.safe_facts.get("cards"), (list, tuple)):
            combined.extend(card for card in evidence.safe_facts["cards"] if isinstance(card, Mapping))
    if _INTERNAL.search(intro) or _INTERNAL.search(question) or any(_INTERNAL.search(text) for text in card_texts):
        raise ContractError("internal metadata is forbidden")
    if _PHONEISH.search(intro) or _PHONEISH.search(question) or any(_PHONEISH.search(text) for text in card_texts):
        raise ContractError("phone numbers are forbidden")
    if (intro + " " + question).count("?") != 1:
        raise ContractError("Prompt 2 must contain exactly one follow-up question")
    if expected_mode not in {"normal", "operator_offer"}:
        raise ContractError("Prompt 2 expected mode is invalid")
    if expected_mode == "operator_offer":
        lowered = question.casefold()
        if not re.search(r"\b(?:оператор\w*|специалист\w*)\b", lowered):
            raise ContractError("operator offer question is missing")
        if re.search(
            r"друг(?:ой|ие)\s+район|продолж(?:ить|им)\s+поиск|"
            r"расшир(?:ить|им)\s+географ|подобр(?:ать|ём)\s+вариант|"
            r"альтернатив|ближайш|соседн|измен(?:ить|им)\s+услов|"
            r"планиров|просмотр|телефон|номер",
            lowered,
        ):
            raise ContractError("operator offer contains a forbidden CTA")
    rendered = [intro] if intro else []
    for index, card_text in zip(indices, card_texts):
        if index >= len(combined):
            raise ContractError("Prompt 2 card index is outside trusted facts")
        rendered.append(card_text)
    rendered.append(question)
    return "\n\n".join(rendered)
