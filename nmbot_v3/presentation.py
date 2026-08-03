"""Closed V3-owned DTOs for an optional client-prose writer.

This module is deliberately a pure presentation boundary.  It accepts only the
V3 input DTOs below, performs no I/O, and does not import a legacy runtime,
card, state, prompt loader, or provider client.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
import re
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import V3ContractError


_INTERNAL_TERM_RE = re.compile(
    r"\b(?:mcp|json|regex|traceback|openrouter|gateway|overmind|payload|"
    r"diagnostics?|optioncard|enum|prompt|api[_ -]?key|token|secret|"
    r"internal(?:\s+field)?|внутренн\w*|пейлоад|диагностик\w*)\b|```|[{}\[\]]",
    re.I,
)


def _text(value: Any, name: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise V3ContractError(f"invalid_{name}")
    text = " ".join(value.split())
    if len(text) > maximum or (not allow_empty and not text):
        raise V3ContractError(f"invalid_{name}")
    return text


def _text_tuple(value: Any, name: str, *, maximum_items: int, item_maximum: int) -> tuple[str, ...]:
    if value in (None, (), []):
        return ()
    if not isinstance(value, (tuple, list)) or len(value) > maximum_items:
        raise V3ContractError(f"invalid_{name}")
    return tuple(_text(item, name, maximum=item_maximum) for item in value)


def _freeze_evidence(value: Any, name: str, *, depth: int = 0) -> Any:
    if depth > 2:
        raise V3ContractError(f"invalid_{name}")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        if len(value) > 20 or any(not isinstance(key, str) or not key.strip() for key in value):
            raise V3ContractError(f"invalid_{name}")
        return MappingProxyType({key.strip(): _freeze_evidence(item, name, depth=depth + 1) for key, item in value.items()})
    if isinstance(value, (tuple, list)) and len(value) <= 12:
        return tuple(_freeze_evidence(item, name, depth=depth + 1) for item in value)
    raise V3ContractError(f"invalid_{name}")


def _reject_unknown(data: Mapping[str, Any], allowed: set[str]) -> None:
    if not isinstance(data, Mapping):
        raise V3ContractError("expected_object")
    if set(data) - allowed:
        raise V3ContractError("unknown_field")


@dataclass(frozen=True)
class V3PresentationCard:
    """One already-confirmed card, projected before any prose is requested."""

    name: str
    confirmed_facts: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "card_name", maximum=160))
        if not isinstance(self.confirmed_facts, Mapping):
            raise V3ContractError("invalid_confirmed_facts")
        object.__setattr__(self, "confirmed_facts", _freeze_evidence(self.confirmed_facts, "confirmed_facts"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "V3PresentationCard":
        _reject_unknown(data, {field.name for field in fields(cls)})
        return cls(**dict(data))


@dataclass(frozen=True)
class V3WriterBriefInput:
    """The complete V3-owned input to the pure writer-brief builder."""

    client_request: str
    answer_goal: str
    cards: tuple[V3PresentationCard, ...] = ()
    confirmed_facts: Mapping[str, Any] = field(default_factory=dict)
    missing_facts: tuple[str, ...] = ()
    allowed_claims: tuple[str, ...] = ()
    forbidden_inferences: tuple[str, ...] = ()
    mandatory_cta: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_request", _text(self.client_request, "client_request", maximum=500))
        object.__setattr__(self, "answer_goal", _text(self.answer_goal, "answer_goal", maximum=80))
        if not isinstance(self.cards, (tuple, list)) or len(self.cards) > 3:
            raise V3ContractError("invalid_cards")
        cards = tuple(card if isinstance(card, V3PresentationCard) else V3PresentationCard.from_dict(card) for card in self.cards)
        if len({card.name for card in cards}) != len(cards):
            raise V3ContractError("duplicate_card_name")
        object.__setattr__(self, "cards", cards)
        if not isinstance(self.confirmed_facts, Mapping):
            raise V3ContractError("invalid_confirmed_facts")
        object.__setattr__(self, "confirmed_facts", _freeze_evidence(self.confirmed_facts, "confirmed_facts"))
        object.__setattr__(self, "missing_facts", _text_tuple(self.missing_facts, "missing_facts", maximum_items=12, item_maximum=120))
        object.__setattr__(self, "allowed_claims", _text_tuple(self.allowed_claims, "allowed_claims", maximum_items=12, item_maximum=240))
        object.__setattr__(self, "forbidden_inferences", _text_tuple(self.forbidden_inferences, "forbidden_inferences", maximum_items=12, item_maximum=240))
        cta = self.mandatory_cta
        object.__setattr__(self, "mandatory_cta", None if cta is None else _text(cta, "mandatory_cta", maximum=240))


@dataclass(frozen=True)
class V3WriterBrief:
    """The stable, writer-facing projection of :class:`V3WriterBriefInput`."""

    client_request: str
    answer_goal: str
    cards: tuple[V3PresentationCard, ...]
    confirmed_facts: Mapping[str, Any]
    missing_facts: tuple[str, ...]
    allowed_claims: tuple[str, ...]
    forbidden_inferences: tuple[str, ...]
    card_names_in_order: tuple[str, ...]
    mandatory_cta: str | None
    exactly_one_final_question: bool = True


def build_v3_writer_brief(source: V3WriterBriefInput) -> V3WriterBrief:
    """Build a writer DTO from V3-owned data only; no legacy adaptation occurs."""
    if not isinstance(source, V3WriterBriefInput):
        raise TypeError("source must be V3WriterBriefInput")
    return V3WriterBrief(
        client_request=source.client_request,
        answer_goal=source.answer_goal,
        cards=source.cards,
        confirmed_facts=source.confirmed_facts,
        missing_facts=source.missing_facts,
        allowed_claims=source.allowed_claims,
        forbidden_inferences=source.forbidden_inferences,
        card_names_in_order=tuple(card.name for card in source.cards),
        mandatory_cta=source.mandatory_cta,
    )


@dataclass(frozen=True)
class V3WriterCardOutput:
    name: str
    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "output_card_name", maximum=160))
        object.__setattr__(self, "text", _text(self.text, "output_card_text", maximum=1200))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "V3WriterCardOutput":
        _reject_unknown(data, {field.name for field in fields(cls)})
        return cls(**dict(data))


@dataclass(frozen=True)
class V3WriterOutput:
    intro: str
    cards: tuple[V3WriterCardOutput, ...] = ()
    recommendation: str = ""
    missing_note: str = ""
    final_question: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "intro", _text(self.intro, "intro", maximum=1200, allow_empty=True))
        if not isinstance(self.cards, (tuple, list)):
            raise V3ContractError("invalid_output_cards")
        object.__setattr__(self, "cards", tuple(card if isinstance(card, V3WriterCardOutput) else V3WriterCardOutput.from_dict(card) for card in self.cards))
        object.__setattr__(self, "recommendation", _text(self.recommendation, "recommendation", maximum=1200, allow_empty=True))
        object.__setattr__(self, "missing_note", _text(self.missing_note, "missing_note", maximum=1200, allow_empty=True))
        object.__setattr__(self, "final_question", _text(self.final_question, "final_question", maximum=300))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "V3WriterOutput":
        _reject_unknown(data, {field.name for field in fields(cls)})
        return cls(**dict(data))


def validate_v3_writer_output(output: V3WriterOutput, brief: V3WriterBrief) -> tuple[str, ...]:
    """Return deterministic contract errors; never repair or reinterpret prose."""
    if not isinstance(output, V3WriterOutput) or not isinstance(brief, V3WriterBrief):
        raise TypeError("output and brief must use V3 presentation DTOs")
    errors: list[str] = []
    output_names = tuple(card.name for card in output.cards)
    if len(output.cards) > 3:
        errors.append("too_many_cards")
    if output_names != brief.card_names_in_order:
        errors.append("card_order_or_names_mismatch")
    if brief.mandatory_cta and output.final_question.rstrip("?.! ") + "?" != brief.mandatory_cta.rstrip("?.! ") + "?":
        errors.append("mandatory_cta_mismatch")
    if output.final_question.count("?") != 1 or not output.final_question.rstrip().endswith("?"):
        errors.append("final_question_count_not_one")
    all_text = "\n".join((output.intro, *(card.name + " " + card.text for card in output.cards), output.recommendation, output.missing_note, output.final_question))
    if all_text.count("?") != 1:
        errors.append("question_outside_final_question")
    if _INTERNAL_TERM_RE.search(all_text):
        errors.append("internal_term_leak")
    return tuple(dict.fromkeys(errors))
