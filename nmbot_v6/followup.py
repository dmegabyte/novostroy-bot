"""Small deterministic resolver/dispatcher for code-owned V6 follow-ups."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping

from .state import PendingInteraction, V6State


class FollowupKind(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    SELECT = "select"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class FollowupResolution:
    kind: FollowupKind
    subject_ref: str | None = None


@dataclass(frozen=True)
class FollowupExecution:
    state: V6State
    text: str
    action: str


_ACCEPT = frozenset({"да", "ага", "угу", "хорошо", "конечно", "давай"})
_REJECT = frozenset({"нет", "не надо", "не хочу", "отмена", "пока нет"})
_SELECT_WORDS = {"первый": 1, "второй": 2, "третий": 3}
_EDGE_PUNCTUATION = re.compile(r"^[\s.,!?;:…—–-]+|[\s.,!?;:…—–-]+$")
_INDEX = re.compile(r"(?:вариант\s*)?([1-9]|1\d|20)")
_CARD_SLOT = re.compile(r"card:([0-2])\Z")
_DETAIL_ACCEPT = re.compile(r"да[,]?\s+расскаж(?:и|ите)\s+(?:всё|все)\Z")


class PendingInteractionResolver:
    """Classify only universal standalone replies against a valid pending offer."""

    def resolve(self, user_text: str, state: V6State) -> FollowupResolution:
        pending = state.pending_interaction
        if pending is None or not self._is_current(pending, state):
            return FollowupResolution(FollowupKind.UNRESOLVED)
        normalized = _normalize(user_text)
        if normalized in _ACCEPT or _DETAIL_ACCEPT.fullmatch(normalized):
            return FollowupResolution(FollowupKind.ACCEPT)
        if normalized in _REJECT:
            return FollowupResolution(FollowupKind.REJECT)
        index = _SELECT_WORDS.get(normalized)
        if index is None:
            match = _INDEX.fullmatch(normalized)
            index = int(match.group(1)) if match else None
        if index is not None and pending.kind == "selection" and index <= len(pending.subject_refs):
            return FollowupResolution(FollowupKind.SELECT, pending.subject_refs[index - 1])
        return FollowupResolution(FollowupKind.UNRESOLVED)

    @staticmethod
    def _is_current(pending: PendingInteraction, state: V6State) -> bool:
        if pending.created_revision != state.revision:
            return False
        available = set(state.option_refs)
        if any(not _subject_ref_exists(ref, available, state.current_cards) for ref in pending.subject_refs):
            return False
        if pending.accept_action == "show_stored_details":
            return _subject_card(state, pending.subject_refs[0] if len(pending.subject_refs) == 1 else None) is not None
        return True


def dispatch_followup(
    resolution: FollowupResolution,
    state: V6State,
) -> FollowupExecution | None:
    """Execute only bounded code-owned actions; unsupported actions stay unresolved."""

    pending = state.pending_interaction
    if pending is None:
        return None
    if resolution.kind is FollowupKind.REJECT and pending.reject_action == "clear_pending":
        return FollowupExecution(
            _completed_state(state, "reject"),
            "Хорошо. Что хотите подобрать или уточнить?",
            "reject",
        )
    if resolution.kind is FollowupKind.ACCEPT and pending.accept_action == "show_stored_details":
        card = _subject_card(state, pending.subject_refs[0] if len(pending.subject_refs) == 1 else None)
        if card is None:
            return None
        return FollowupExecution(_completed_state(state, "accept"), _detail_text(card), "accept")
    if resolution.kind is FollowupKind.SELECT and pending.kind == "selection" \
            and resolution.subject_ref in pending.subject_refs:
        return FollowupExecution(
            _completed_state(state, "select", selected=resolution.subject_ref),
            "Выбрала этот вариант. Что хотите узнать о нём?",
            "select",
        )
    return None


def exact_detail_context(
    resolution: FollowupResolution,
    state: V6State,
) -> Mapping[str, Any] | None:
    """Bind an accepted/selected pending offer to one saved canonical card."""

    pending = state.pending_interaction
    if pending is None:
        return None
    subject_ref = None
    if resolution.kind is FollowupKind.ACCEPT and pending.kind == "offer" \
            and len(pending.subject_refs) == 1:
        subject_ref = pending.subject_refs[0]
    elif resolution.kind is FollowupKind.SELECT and pending.kind == "selection" \
            and resolution.subject_ref in pending.subject_refs:
        subject_ref = resolution.subject_ref
    if subject_ref is None:
        return None
    card = _subject_card(state, subject_ref)
    if card is None or not isinstance(card.get("name"), str) or not card["name"].strip():
        return None
    constraints = _saved_lot_constraints(state, card)
    return MappingProxyType({
        "subject_ref": subject_ref,
        "canonical_name": card["name"],
        "canonical_card": card,
        "lot_constraints": constraints,
        "pending_action": pending.accept_action,
        "pending_question_goal": pending.question_goal,
    })


def _normalize(text: str) -> str:
    return " ".join(_EDGE_PUNCTUATION.sub("", str(text or "").casefold()).split())


def _subject_card(state: V6State, subject_ref: str | None) -> Mapping[str, Any] | None:
    if not subject_ref:
        return None
    for card in state.current_cards:
        if any(card.get(key) == subject_ref for key in ("ref", "id", "object_id", "option_ref")):
            return card
    slot = _CARD_SLOT.fullmatch(subject_ref)
    if slot:
        index = int(slot.group(1))
        return state.current_cards[index] if index < len(state.current_cards) else None
    return None


def _subject_ref_exists(
    subject_ref: str,
    option_refs: set[str],
    cards: tuple[Mapping[str, Any], ...],
) -> bool:
    if subject_ref in option_refs:
        return True
    slot = _CARD_SLOT.fullmatch(subject_ref)
    if slot:
        return int(slot.group(1)) < len(cards)
    return False


def _saved_lot_constraints(
    state: V6State,
    card: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Copy only lot constraints already exposed by V6 state/card contracts."""

    result: dict[str, Any] = {}
    sources: list[Mapping[str, Any]] = []
    context = state.safe_context
    if isinstance(context, Mapping):
        sources.append(context)
        for key in ("params", "constraints", "effective_constraints", "lot_constraints"):
            nested = context.get(key)
            if isinstance(nested, Mapping):
                sources.append(nested)
    sources.append(card)
    aliases = {
        "rooms": "rooms",
        "min_price": "min_price",
        "price_min": "min_price",
        "max_price": "max_price",
        "price_max": "max_price",
    }
    for source in sources:
        for source_key, target_key in aliases.items():
            value = source.get(source_key)
            if type(value) in (int, float) and value >= 0:
                result[target_key] = value
    return MappingProxyType(result)


def _completed_state(state: V6State, action: str, *, selected: str | None = None) -> V6State:
    context = dict(state.safe_context)
    context.update({"last_followup_resolution": "resolved", "last_followup_action": action})
    return V6State(
        revision=state.revision + 1,
        pending_phone=state.pending_phone,
        safe_context=context,
        option_refs=state.option_refs,
        selected_option_ref=selected if selected is not None else state.selected_option_ref,
        current_cards=state.current_cards,
        pending_interaction=None,
    )


def _detail_text(card: Mapping[str, Any]) -> str:
    name = str(card.get("name") or "этот комплекс")
    labels = (
        ("developer", "застройщик"), ("location", "локация"), ("district", "район"),
        ("price_range", "цены"), ("price", "цена"), ("finishing", "отделка"),
        ("ready", "готовность"), ("metro", "метро"), ("area", "площадь"),
        ("rooms", "комнаты"), ("infrastructure", "инфраструктура"),
    )
    facts = []
    for key, label in labels:
        value = card.get(key)
        if value in (None, "", (), []):
            continue
        if isinstance(value, (list, tuple)):
            rendered = ", ".join(str(item) for item in value[:5])
        elif isinstance(value, Mapping):
            continue
        else:
            rendered = str(value)
        facts.append(f"{label}: {rendered}")
        if len(facts) == 5:
            break
    if facts:
        return f"По сохранённым данным о «{name}»: " + "; ".join(facts) + ". Что ещё уточнить?"
    return f"По сохранённым данным есть комплекс «{name}», но подробные характеристики не сохранены. Что ещё уточнить?"
