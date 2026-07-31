from __future__ import annotations

from dataclasses import dataclass, field, fields
import re
from typing import Any, Mapping

from .contracts import SCHEMA_VERSION, V1Error, _reject_unknown, _optional_str, deep_freeze, deep_thaw


HARD_ALLOWLIST = {"location", "max_price", "min_price", "rooms", "finishing", "completion", "ready"}
PUBLIC_EVIDENCE_KEYS = {"location", "max_price", "min_price", "price", "rooms", "finishing", "completion", "ready"}
SECRET_RE = re.compile(r"(secret|token|api[_-]?key|bearer|password|raw_payload|payload)", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
INTERNAL_SOURCE_RE = re.compile(r"(?i)\b(?:novos|lot|lots|project|projects|inventory|schema|query|filter)\.[A-Za-z_][\w.]*\b")
INTERNAL_OPERATOR_RE = re.compile(r"(?i)(?:\bcontains\b|\b(?:equals?|not_equals?|gte|lte|gt|lt|not_in|in|like|ilike|where|select|from)\b|[=!<>]=|&&|\|\|)")


@dataclass(frozen=True)
class V1SearchRequest:
    schema_version: int = SCHEMA_VERSION
    hard_constraints: Mapping[str, Any] = field(default_factory=dict)
    preferences: Mapping[str, Any] = field(default_factory=dict)
    viewpoint: str = "buyer"
    selected_project_ref: str | None = None
    requested_facts: tuple[str, ...] = ()

    def __post_init__(self):
        if self.schema_version != SCHEMA_VERSION:
            raise V1Error("schema_version must be 1")
        if not isinstance(self.viewpoint, str) or not self.viewpoint.strip():
            raise V1Error("viewpoint must be string")
        _optional_str(self.selected_project_ref, "selected_project_ref")
        requested = tuple(self.requested_facts or ())
        if any(not isinstance(v, str) or not v for v in requested):
            raise V1Error("requested_facts must be strings")
        object.__setattr__(self, "hard_constraints", deep_freeze(self.hard_constraints or {}))
        validate_supported_hard(self.hard_constraints)
        object.__setattr__(self, "preferences", deep_freeze(self.preferences or {}))
        object.__setattr__(self, "requested_facts", requested)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "hard_constraints": deep_thaw(self.hard_constraints), "preferences": deep_thaw(self.preferences), "viewpoint": self.viewpoint, "selected_project_ref": self.selected_project_ref, "requested_facts": list(self.requested_facts)}


@dataclass(frozen=True)
class V1OptionCard:
    ref: str
    name: str
    facts: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.ref, str) or not self.ref.strip():
            raise V1Error("card ref must be string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise V1Error("card name must be string")
        facts = {} if self.facts is None else self.facts
        evidence = {} if self.evidence is None else self.evidence
        if not isinstance(facts, Mapping) or not isinstance(evidence, Mapping):
            raise V1Error("card facts/evidence must be objects")
        object.__setattr__(self, "facts", deep_freeze(facts))
        object.__setattr__(self, "evidence", deep_freeze(evidence))

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "name": self.name, "facts": deep_thaw(self.facts), "evidence": deep_thaw(self.evidence)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "V1OptionCard":
        _reject_unknown(data, {f.name for f in fields(cls)})
        return cls(**dict(data))


@dataclass(frozen=True)
class V1LotCard(V1OptionCard):
    pass


@dataclass(frozen=True)
class V1SearchResult:
    schema_version: int
    exact: tuple[V1OptionCard, ...] = ()
    near: tuple[V1OptionCard, ...] = ()
    missing: tuple[str, ...] = ()
    effective_hard: Mapping[str, Any] = field(default_factory=dict)
    attempts: tuple[Mapping[str, Any], ...] = ()
    error_code: str | None = None

    def __post_init__(self):
        if self.schema_version != SCHEMA_VERSION:
            raise V1Error("schema_version must be 1")
        object.__setattr__(self, "exact", tuple(self.exact or ()))
        object.__setattr__(self, "near", tuple(self.near or ()))
        object.__setattr__(self, "missing", tuple(self.missing or ()))
        object.__setattr__(self, "effective_hard", deep_freeze(self.effective_hard or {}))
        object.__setattr__(self, "attempts", tuple(deep_freeze(a) for a in (self.attempts or ())))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "exact": [c.to_dict() for c in self.exact], "near": [c.to_dict() for c in self.near], "missing": list(self.missing), "effective_hard": deep_thaw(self.effective_hard), "attempts": [deep_thaw(a) for a in self.attempts], "error_code": self.error_code}

    @classmethod
    def from_provider_dict(cls, data: Mapping[str, Any], effective_hard: Mapping[str, Any]) -> "V1SearchResult":
        validate_supported_hard(effective_hard)
        _reject_unknown(data, {"schema_version", "cards", "attempts"})
        if data.get("schema_version") != SCHEMA_VERSION:
            raise V1Error("schema_version must be 1")
        attempts = data.get("attempts", [])
        if not isinstance(attempts, list) or any(not isinstance(a, Mapping) for a in attempts):
            raise V1Error("attempts must be list of objects")
        cards = data.get("cards", [])
        if not isinstance(cards, list):
            raise V1Error("cards must be list")
        exact: list[V1OptionCard] = []
        near: list[V1OptionCard] = []
        missing: set[str] = set()
        for raw in cards:
            card = V1OptionCard.from_dict(raw)
            ok, card_missing = validate_hard_evidence(card, effective_hard)
            if ok:
                exact.append(card)
            else:
                missing.update(card_missing)
                near.append(card)
        return cls(SCHEMA_VERSION, tuple(exact), tuple(near), tuple(sorted(missing)), dict(effective_hard), tuple(attempts))


def validate_hard_evidence(card: V1OptionCard, hard: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    validate_supported_hard(hard)
    missing: list[str] = []
    for key, expected in hard.items():
        if not _is_public_constraint_value(expected):
            missing.append(key)
            continue
        if key not in card.evidence:
            missing.append(key)
            continue
        actual = card.evidence.get(key)
        if not _is_public_constraint_value(actual):
            missing.append(key)
            continue
        if key == "max_price" and not (_is_number(actual) and _is_number(expected) and actual <= expected):
            return False, tuple(missing)
        if key == "min_price" and not (_is_number(actual) and _is_number(expected) and actual >= expected):
            return False, tuple(missing)
        if key not in {"max_price", "min_price"} and actual != expected:
            return False, tuple(missing)
    return not missing, tuple(missing)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_supported_hard(hard: Mapping[str, Any]) -> None:
    unsupported = sorted(set(hard) - HARD_ALLOWLIST)
    if unsupported:
        raise V1Error("unsupported hard constraints: " + ",".join(unsupported))


def project_public_card(card: V1OptionCard | Mapping[str, Any]) -> dict[str, Any]:
    source = card.to_dict() if isinstance(card, V1OptionCard) else dict(card)
    ref = _safe_text(source.get("ref"), fallback="")
    if not ref:
        raise V1Error("card ref must be safe string")
    name = _safe_text(source.get("name"), fallback="вариант")
    evidence = source.get("evidence", {}) or {}
    if not isinstance(evidence, Mapping):
        raise V1Error("card evidence must be object")
    facts: dict[str, Any] = {}
    for key in PUBLIC_EVIDENCE_KEYS:
        if key not in evidence:
            continue
        value = _safe_scalar(evidence[key])
        if value is None:
            continue
        public_key = "price" if key == "max_price" else key
        facts[public_key] = value
    return {"ref": ref, "name": name, "facts": facts}


def _safe_text(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    if not value or _has_blocked_public_text(value):
        return fallback
    return value[:120]


def _safe_scalar(value: Any) -> Any:
    return public_safe_scalar(value)


def public_safe_scalar(value: Any) -> Any:
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        v = value.strip()
        if not v or _has_blocked_public_text(v):
            return None
        return v[:120]
    return None


def _is_public_constraint_value(value: Any) -> bool:
    return public_safe_scalar(value) is not None


def _has_blocked_public_text(value: str) -> bool:
    return bool(
        SECRET_RE.search(value)
        or EMAIL_RE.search(value)
        or PHONE_RE.search(value)
        or INTERNAL_SOURCE_RE.search(value)
        or INTERNAL_OPERATOR_RE.search(value)
    )
