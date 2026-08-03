"""Pure V3 evidence DTOs and validation, intentionally detached from transport."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import (
    EMAIL_RE,
    PHONE_RE,
    V3_ALLOWED_FACTS,
    V3ContractError,
    _canonical_ref_tuple,
    _optional_text,
    _reject_unknown,
    _string_tuple,
)


# This is intentionally smaller than the V2 search vocabulary.  It describes
# only the closed V3 fields that can prove availability of V3 hard filters; it
# does not attempt to reproduce V2's transport-specific matching semantics.
V3_HARD_EVIDENCE_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "rooms": ("rooms", "room_formats"),
    "max_price": ("price_min", "price", "price_range"),
    "min_price": ("price_min", "price", "price_range"),
    "area_min_m2": ("area",),
    "area_max_m2": ("area",),
    "location": ("location", "district"),
    "district": ("district",),
    "ready": ("ready",),
    "finishing": ("finishing",),
})


class EvidenceModeV3(str, Enum):
    BROAD = "broad"
    NAMED_OBJECT = "named_object"
    CURRENT_OPTIONS_FACT_CHECK = "current_options_fact_check"

    @classmethod
    def coerce(cls, value: Any) -> "EvidenceModeV3":
        try:
            return value if isinstance(value, cls) else cls(value)
        except (TypeError, ValueError) as exc:
            raise V3ContractError("invalid_evidence_mode") from exc


def _freeze(value: Any, name: str) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise V3ContractError(f"invalid_{name}")
        return MappingProxyType({key: _freeze(item, name) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item, name) for item in value)
    raise V3ContractError(f"invalid_{name}")


def _exact_name(value: Any, name: str) -> str:
    text = _optional_text(value, name, maximum=200)
    if text is None:
        raise V3ContractError(f"invalid_{name}")
    return text


def _name_key(name: str) -> str:
    text = name.casefold().replace("ё", "е")
    text = re.sub(r"\b(?:жк|жилой комплекс)\b", " ", text)
    return re.sub(r"[^a-zа-я0-9]+", "", text)


def _has_available_field(card: "CanonicalCard", field: str) -> bool:
    value = card.fields.get(field)
    return value not in (None, "", (), [])


def _fact_is_available(result: "EvidenceResult", fact: str) -> bool:
    return any(
        _has_available_field(card, fact)
        and (fact != "apartment_inventory" or _is_safe_apartment_inventory(card.fields.get(fact)))
        for card in result.facts
    )


_INVENTORY_POINTER_RE = re.compile(
    r"(?:данн(?:ые|ых)|информац(?:ия|ии))\s+доступн(?:а|ы)?\s+через\s+"
    r"(?:поиск|запрос)|available\s+(?:through|via)\s+(?:search|lookup)",
    re.I,
)
_CREDENTIAL_RE = re.compile(r"\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*\S+", re.I)


def _is_safe_apartment_inventory(value: Any) -> bool:
    """Accept only a small client-safe, current-invocation inventory scalar.

    V3 evidence is retrieved for the present turn, but a provider may not turn
    an ad count, lookup instruction, contact, or nested source payload into an
    availability claim. This mirrors the V2 normalizer's inventory boundary
    without importing it or its transport vocabulary.
    """

    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    if not isinstance(value, str):
        return False
    text = " ".join(value.split())
    if not text or len(text) > 120 or text in {"0", "0.0"}:
        return False
    if _INVENTORY_POINTER_RE.search(text) or _CREDENTIAL_RE.search(text):
        return False
    return not PHONE_RE.search(text) and not EMAIL_RE.search(text)


@dataclass(frozen=True)
class EvidenceRequest:
    """V3-owned query intent; it carries no client text, port, or state path."""

    mode: EvidenceModeV3 = EvidenceModeV3.BROAD
    requested_facts: tuple[str, ...] = ()
    hard_constraints: Mapping[str, Any] = field(default_factory=dict)
    exact_name: str | None = None
    current_option_refs: tuple[str, ...] = ()
    excluded_names: tuple[str, ...] = ()
    count: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", EvidenceModeV3.coerce(self.mode))
        requested_facts = _string_tuple(self.requested_facts, "requested_facts")
        if any(fact not in V3_ALLOWED_FACTS for fact in requested_facts):
            raise V3ContractError("unknown_requested_fact")
        object.__setattr__(self, "requested_facts", requested_facts)
        if not isinstance(self.hard_constraints, Mapping):
            raise V3ContractError("invalid_hard_constraints")
        object.__setattr__(self, "hard_constraints", _freeze(self.hard_constraints, "hard_constraints"))
        exact_name = _optional_text(self.exact_name, "exact_name", maximum=200)
        object.__setattr__(self, "exact_name", exact_name)
        current_refs = _canonical_ref_tuple(self.current_option_refs, "current_option_refs")
        excluded_names = _string_tuple(self.excluded_names, "excluded_names")
        if len(excluded_names) != len({_name_key(name) for name in excluded_names}):
            raise V3ContractError("duplicate_excluded_name")
        object.__setattr__(self, "current_option_refs", current_refs)
        object.__setattr__(self, "excluded_names", excluded_names)
        if type(self.count) is not int or not 1 <= self.count <= 3:
            raise V3ContractError("invalid_evidence_count")
        if self.mode is EvidenceModeV3.NAMED_OBJECT:
            if exact_name is None or current_refs or self.count != 1:
                raise V3ContractError("invalid_named_object_request")
        elif exact_name is not None:
            raise V3ContractError("invalid_exact_name_scope")
        if self.mode is EvidenceModeV3.CURRENT_OPTIONS_FACT_CHECK:
            if not current_refs or len(current_refs) > 3:
                raise V3ContractError("invalid_current_options_request")
        elif current_refs:
            raise V3ContractError("invalid_current_option_refs_scope")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceRequest":
        _reject_unknown(data, {"mode", "requested_facts", "hard_constraints", "exact_name", "current_option_refs", "excluded_names", "count"})
        return cls(**dict(data))


@dataclass(frozen=True)
class CanonicalCard:
    """Evidence card with an optional, UUID-only canonical identity."""

    name: str
    fields: Mapping[str, Any] = field(default_factory=dict)
    canonical_ref: str | None = None
    is_near: bool = False
    differences: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _exact_name(self.name, "card_name"))
        refs = _canonical_ref_tuple((self.canonical_ref,) if self.canonical_ref is not None else (), "canonical_ref")
        object.__setattr__(self, "canonical_ref", refs[0] if refs else None)
        if not isinstance(self.fields, Mapping):
            raise V3ContractError("invalid_card_fields")
        if any(not isinstance(key, str) or not key.strip() for key in self.fields):
            raise V3ContractError("invalid_card_fields")
        object.__setattr__(self, "fields", _freeze(self.fields, "card_fields"))
        if not isinstance(self.is_near, bool):
            raise V3ContractError("invalid_is_near")
        differences = _string_tuple(self.differences, "differences")
        if self.is_near != bool(differences):
            raise V3ContractError("invalid_near_differences")
        object.__setattr__(self, "differences", differences)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CanonicalCard":
        _reject_unknown(data, {"name", "canonical_ref", "fields", "is_near", "differences"})
        return cls(**dict(data))


@dataclass(frozen=True)
class EvidenceResult:
    """Canonical evidence split: exact matches never share a container with near."""

    facts: tuple[CanonicalCard, ...] = ()
    near: tuple[CanonicalCard, ...] = ()
    missing_facts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        facts = _card_tuple(self.facts, "facts")
        near = _card_tuple(self.near, "near")
        if any(card.is_near for card in facts):
            raise V3ContractError("fact_marked_near")
        if any(not card.is_near for card in near):
            raise V3ContractError("near_not_marked")
        names = [_name_key(card.name) for card in (*facts, *near)]
        if len(names) != len(set(names)):
            raise V3ContractError("duplicate_evidence_card")
        refs = [card.canonical_ref for card in (*facts, *near) if card.canonical_ref is not None]
        if len(refs) != len(set(refs)):
            raise V3ContractError("duplicate_evidence_ref")
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "near", near)
        object.__setattr__(self, "missing_facts", _string_tuple(self.missing_facts, "missing_facts"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceResult":
        _reject_unknown(data, {"facts", "near", "missing_facts"})
        return cls(**dict(data))


def _card_tuple(value: Any, name: str) -> tuple[CanonicalCard, ...]:
    if value in (None, (), []):
        return ()
    if not isinstance(value, (tuple, list)):
        raise V3ContractError(f"invalid_{name}")
    cards: list[CanonicalCard] = []
    for item in value:
        cards.append(item if isinstance(item, CanonicalCard) else CanonicalCard.from_dict(item) if isinstance(item, Mapping) else _invalid_card(name))
    return tuple(cards)


def _invalid_card(name: str) -> CanonicalCard:
    raise V3ContractError(f"invalid_{name}")


@dataclass(frozen=True)
class EvidenceValidation:
    ok: bool
    result: EvidenceResult | None
    errors: tuple[str, ...]
    repairable: bool


def normalize_evidence_result(request: EvidenceRequest, raw_or_result: Any) -> EvidenceResult:
    """Return a V3-only canonical result with deterministic missing-fact order.

    Only exact ``facts`` prove fact availability.  ``near`` cards are explicitly
    alternatives and therefore cannot suppress a requested fact from
    ``missing_facts``.  Input card and container order is preserved.
    """

    if not isinstance(request, EvidenceRequest):
        raise V3ContractError("invalid_evidence_request")
    result = raw_or_result if isinstance(raw_or_result, EvidenceResult) else EvidenceResult.from_dict(raw_or_result) if isinstance(raw_or_result, Mapping) else _invalid_result()
    unknown_missing = set(result.missing_facts) - set(request.requested_facts)
    if unknown_missing:
        raise V3ContractError("unknown_missing_fact")
    missing_facts = tuple(
        fact for fact in request.requested_facts
        if not _fact_is_available(result, fact)
    )
    return EvidenceResult(result.facts, result.near, missing_facts)


def _invalid_result() -> EvidenceResult:
    raise V3ContractError("invalid_evidence_result")


def validate_evidence_result(request: EvidenceRequest, raw_or_result: Any) -> EvidenceValidation:
    """Validate a received result without coercing names, reordering cards, or I/O."""

    if not isinstance(request, EvidenceRequest):
        raise V3ContractError("invalid_evidence_request")
    try:
        result = raw_or_result if isinstance(raw_or_result, EvidenceResult) else EvidenceResult.from_dict(raw_or_result) if isinstance(raw_or_result, Mapping) else None
    except V3ContractError as exc:
        return EvidenceValidation(False, None, (_validation_code(exc),), True)
    if result is None:
        return EvidenceValidation(False, None, ("invalid_evidence_result",), True)

    errors: list[str] = []
    if len(result.facts) > request.count or len(result.near) > request.count:
        errors.append("evidence_count_exceeded")
    all_cards = (*result.facts, *result.near)
    excluded = {_name_key(name) for name in request.excluded_names}
    if any(_name_key(card.name) in excluded for card in all_cards):
        errors.append("excluded_name_returned")
    if request.mode is EvidenceModeV3.NAMED_OBJECT:
        if any(_name_key(card.name) != _name_key(request.exact_name or "") for card in all_cards):
            errors.append("exact_name_mismatch")
    if request.mode is EvidenceModeV3.CURRENT_OPTIONS_FACT_CHECK:
        positions = {reference: index for index, reference in enumerate(request.current_option_refs)}
        result_positions: list[int] = []
        for card in all_cards:
            position = positions.get(card.canonical_ref)
            if position is None:
                errors.append("current_option_ref_not_exact")
                continue
            result_positions.append(position)
        if result_positions != sorted(result_positions):
            errors.append("current_option_order_changed")
    requested = set(request.requested_facts)
    missing = set(result.missing_facts)
    if missing - requested:
        errors.append("unknown_missing_fact")
    expected_missing = tuple(fact for fact in request.requested_facts if not _fact_is_available(result, fact))
    if tuple(result.missing_facts) != expected_missing:
        errors.append("missing_facts_not_normalized")
    for index, card in enumerate(result.facts):
        for constraint, evidence_fields in V3_HARD_EVIDENCE_FIELDS.items():
            if constraint in request.hard_constraints and not any(_has_available_field(card, field) for field in evidence_fields):
                errors.append(f"fact_{index}_missing_hard_evidence:{constraint}")
        if "apartment_inventory" in card.fields and not _is_safe_apartment_inventory(card.fields["apartment_inventory"]):
            errors.append(f"fact_{index}_invalid_apartment_inventory")
    return EvidenceValidation(not errors, result, tuple(sorted(set(errors))), False)


def _validation_code(exc: V3ContractError) -> str:
    known = {
        "unknown_field", "invalid_facts", "invalid_near", "invalid_card_fields",
        "invalid_card_name", "invalid_is_near", "invalid_near_differences",
        "fact_marked_near", "near_not_marked", "duplicate_evidence_card", "duplicate_evidence_ref",
        "unknown_missing_fact",
    }
    return str(exc) if str(exc) in known else "invalid_evidence_result"
