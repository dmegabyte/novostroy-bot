from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

from .card_normalizer import safe_dynamic_inventory_scalar, safe_dynamic_price_scalar, safe_dynamic_text
from .contracts import OptionCard


ALLOWED_SUBJECTS: tuple[str, ...] = (
    "parking", "apartment", "mortgage", "location", "transport", "infrastructure", "readiness", "developer",
)
ALLOWED_FACTS: tuple[str, ...] = (
    "parking", "parking_price", "parking_inventory",
    "apartment_price", "apartment_inventory", "mortgage_terms",
    "location", "metro", "schools", "readiness", "finishing", "parks", "developer",
    "lot_examples",
)
FOCUS_ACTIONS: tuple[str, ...] = ("keep", "switch", "clear", "clarify")
DYNAMIC_FACTS: tuple[str, ...] = ("parking_price", "parking_inventory", "apartment_inventory", "mortgage_terms")
PARKING_MISSING_TEXT_RE = re.compile(r"нет\s+(?:паркинг|парков|машино|мест)|паркинг\s+не\s+(?:предусмотрен|найден|подтвержд)|parking\s+not\s+(?:available|provided|found|confirmed)|no\s+parking", re.I)
SUBJECT_FACT_MAP: dict[str, tuple[str, ...]] = {
    "parking": ("parking", "parking_price", "parking_inventory"),
    "apartment": ("apartment_price", "apartment_inventory", "finishing"),
    "mortgage": ("mortgage_terms",),
    "location": ("location",),
    "transport": ("metro",),
    "infrastructure": ("schools", "parking", "parks"),
    "readiness": ("readiness",),
    "developer": ("developer",),
}

SAFE_COMPARATIVE_FACTS: tuple[str, ...] = ("apartment_price", "readiness", "metro", "finishing", "location")


@dataclass(frozen=True)
class FactAvailability:
    available_counts: dict[str, int]
    missing_facts: tuple[str, ...] = ()
    present_by_card: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class FactSplit:
    available: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    dynamic: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()


def normalize_subject(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in ALLOWED_SUBJECTS else None


def normalize_focus_action(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in FOCUS_ACTIONS else "keep"


def normalize_facts(value: Any) -> tuple[str, ...]:
    raw = value if isinstance(value, (list, tuple, set)) else ([] if value in (None, "") else [value])
    out: list[str] = []
    for item in raw:
        fact = str(item or "").strip().lower()
        if fact in ALLOWED_FACTS and fact not in out:
            out.append(fact)
    return tuple(out)


def facts_for_subject(subject: str | None) -> tuple[str, ...]:
    return SUBJECT_FACT_MAP.get(str(subject or ""), ())


def present_fact_names(card: OptionCard | None) -> tuple[str, ...]:
    if card is None:
        return ()
    facts: list[str] = []
    if card.price or card.price_min is not None:
        facts.append("apartment_price")
    if card.lot_examples:
        facts.append("lot_examples")
    if card.location:
        facts.append("location")
    if card.metro:
        facts.append("metro")
    if card.ready:
        facts.append("readiness")
    if card.finishing:
        facts.append("finishing")
    if _is_safe_parking_presence(card.parking) or _infra_has(card, "паркинг", "парков", "parking"):
        facts.append("parking")
    if safe_dynamic_price_scalar(card.parking_price) is not None:
        facts.append("parking_price")
    if safe_dynamic_inventory_scalar(card.parking_inventory) is not None:
        facts.append("parking_inventory")
    if _is_safe_apartment_inventory(card.apartment_inventory):
        facts.append("apartment_inventory")
    if card.mortgage_terms or card.discount:
        facts.append("mortgage_terms")
    if card.developer:
        facts.append("developer")
    if family_education_evidence(card):
        facts.append("schools")
    if _infra_has_park_evidence(card):
        facts.append("parks")
    return tuple(dict.fromkeys(facts))


def present_fact_names_for_cards(cards: tuple[OptionCard, ...] | list[OptionCard]) -> tuple[tuple[str, ...], ...]:
    return tuple(present_fact_names(card) for card in (cards or ()))


def family_education_evidence(card: OptionCard) -> tuple[str, ...]:
    return tuple(
        item
        for item in card.infrastructure
        if re.search(r"школ|school|дет(?:ск(?:ий|ого|ому|им|ом)?\s*)?сад|детсад|kindergarten", item, re.I)
    )


def fact_availability(cards: tuple[OptionCard, ...] | list[OptionCard], requested_facts: Any) -> FactAvailability:
    requested = normalize_facts(requested_facts)
    present_by_card = present_fact_names_for_cards(cards or ())
    counts: dict[str, int] = {fact: 0 for fact in requested}
    for present in present_by_card:
        present_set = set(present)
        for fact in requested:
            if fact in present_set:
                counts[fact] += 1
    return FactAvailability(
        available_counts=counts,
        missing_facts=tuple(fact for fact in requested if counts.get(fact, 0) <= 0),
        present_by_card=present_by_card,
    )


def evidence_sufficient(goal: Any, cards: tuple[OptionCard, ...] | list[OptionCard], requested_facts: Any) -> bool:
    requested = normalize_facts(requested_facts)
    goal_value = getattr(goal, "value", goal)
    goal_text = str(goal_value or "").strip().lower()
    present_by_card = present_fact_names_for_cards(cards or ())
    if not present_by_card:
        return False
    if not requested:
        safe_counts = sum(1 for present in present_by_card if set(present) & set(SAFE_COMPARATIVE_FACTS))
        return safe_counts >= (2 if goal_text in {"compare_current", "recommend_current"} else 1)
    availability = fact_availability(cards, requested)
    threshold = 2 if goal_text in {"compare_current", "recommend_current"} else 1
    return all(availability.available_counts.get(fact, 0) >= threshold for fact in requested)


def split_requested_facts(requested: Any, card: OptionCard | None, *, fresh_facts: tuple[str, ...] | list[str] | set[str] = ()) -> FactSplit:
    raw = requested if isinstance(requested, (list, tuple, set)) else ([] if requested in (None, "") else [requested])
    available_now = set(present_fact_names(card))
    fresh_now = {str(item).strip().lower() for item in fresh_facts if str(item).strip().lower() in ALLOWED_FACTS}
    available: list[str] = []
    missing: list[str] = []
    dynamic: list[str] = []
    unsupported: list[str] = []
    for item in raw:
        fact = str(item or "").strip().lower()
        if fact not in ALLOWED_FACTS:
            if fact:
                unsupported.append(fact)
            continue
        if fact in DYNAMIC_FACTS and fact in available_now and fact in fresh_now:
            available.append(fact)
        elif fact in DYNAMIC_FACTS:
            missing.append(fact)
            dynamic.append(fact)
        elif fact in available_now:
            available.append(fact)
        else:
            missing.append(fact)
    return FactSplit(
        available=tuple(dict.fromkeys(available)),
        missing=tuple(dict.fromkeys(missing)),
        dynamic=tuple(dict.fromkeys(dynamic)),
        unsupported=tuple(dict.fromkeys(unsupported)),
    )


def answered_facts(requested: Any, card: OptionCard | None, *, fresh_facts: tuple[str, ...] | list[str] | set[str] = ()) -> tuple[str, ...]:
    return split_requested_facts(requested, card, fresh_facts=fresh_facts).available


def _infra_has(card: OptionCard, *tokens: str) -> bool:
    hay = " ".join(str(item) for item in card.infrastructure).casefold()
    return any(token.casefold() in hay for token in tokens)


def _is_safe_parking_presence(value: Any) -> bool:
    if value is True:
        return True
    if value in (None, False, "") or isinstance(value, (Mapping, list, tuple, set)):
        return False
    text = safe_dynamic_text(value)
    return bool(text and not PARKING_MISSING_TEXT_RE.search(text))


PARK_INFRA_RE = re.compile(
    r"парк|лес|лесопарк|зел[её]н|сквер|набереж|вод[аыо]|река|озер|пруд|park|green[\s-]*zone|forest|water",
    re.I,
)


def _infra_has_park_evidence(card: OptionCard) -> bool:
    # Evidence boundary: only infrastructure tokens are considered. A ЖК name
    # like "Мичуринский парк" is not evidence that there is a park nearby.
    return any(PARK_INFRA_RE.search(str(item or "")) for item in card.infrastructure)


def _is_safe_apartment_inventory(value: Any) -> bool:
    return safe_dynamic_inventory_scalar(value) is not None
