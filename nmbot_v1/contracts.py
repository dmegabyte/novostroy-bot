from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


SCHEMA_VERSION = 1


class V1Error(ValueError):
    pass


class StrictEnum(str, Enum):
    @classmethod
    def coerce(cls, value: Any):
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except Exception as exc:
            raise V1Error(f"invalid {cls.__name__}") from exc


class V1Goal(StrictEnum):
    RESET = "reset"
    SEARCH = "search"
    REFINE_SEARCH = "refine_search"
    EXPAND_SEARCH = "expand_search"
    ANSWER_CURRENT = "answer_current"
    SELECT_PROJECT = "select_project"
    SEARCH_LOTS = "search_lots"
    SELECT_LOT = "select_lot"
    FACT_CHECK = "fact_check"
    OFFER_OPERATOR = "offer_operator"
    CAPTURE_NAME = "capture_name"
    CAPTURE_PHONE = "capture_phone"
    OFF_TOPIC = "off_topic"
    SAFE_ERROR = "safe_error"


class V1OperatorIntent(StrictEnum):
    NONE = "none"
    REQUEST = "request"
    ACCEPT = "accept"
    DECLINE = "decline"


class V1Stage(StrictEnum):
    RESET = "reset"
    FIRST_SEARCH = "first_search"
    REFINE_SEARCH = "refine_search"
    EXPAND_SEARCH = "expand_search"
    CURRENT_OPTIONS = "current_options"
    SELECTED_PROJECT = "selected_project"
    SELECTED_LOT_SEARCH = "selected_lot_search"
    SELECTED_LOT = "selected_lot"
    FACT_CHECK = "fact_check"
    OPERATOR_OFFER = "operator_offer"
    CONTACT_NAME = "contact_name"
    CONTACT_PHONE = "contact_phone"
    OPERATOR_DECLINED = "operator_declined"
    OFF_TOPIC = "off_topic"
    SAFE_ERROR = "safe_error"


class V1Action(StrictEnum):
    RESET = "reset"
    SEARCH = "search"
    ANSWER_CURRENT = "answer_current"
    SELECT_PROJECT = "select_project"
    SEARCH_LOTS = "search_lots"
    SELECT_LOT = "select_lot"
    FACT_CHECK = "fact_check"
    OFFER_OPERATOR = "offer_operator"
    ACCEPT_OPERATOR = "accept_operator"
    DECLINE_OPERATOR = "decline_operator"
    CAPTURE_NAME = "capture_name"
    CAPTURE_PHONE = "capture_phone"
    OFF_TOPIC = "off_topic"
    SAFE_ERROR = "safe_error"


class V1AnswerKind(StrictEnum):
    SEARCH_RESULTS = "search_results"
    CURRENT_OPTIONS = "current_options"
    PROJECT_SELECTED = "project_selected"
    LOTS = "lots"
    FACTS = "facts"
    CLARIFY = "clarify"
    OPERATOR = "operator"
    OFF_TOPIC = "off_topic"
    SAFE_ERROR = "safe_error"


@dataclass(frozen=True)
class V1ConstraintsDelta:
    hard: Mapping[str, Any] = field(default_factory=dict)
    preferences: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "hard", deep_freeze(_mapping(self.hard or {}, "hard")))
        object.__setattr__(self, "preferences", deep_freeze(_mapping(self.preferences or {}, "preferences")))

    def to_dict(self) -> dict[str, Any]:
        return {"hard": deep_thaw(self.hard), "preferences": deep_thaw(self.preferences)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "V1ConstraintsDelta":
        _reject_unknown(data, {"hard", "preferences"})
        return cls(hard=_mapping(data.get("hard", {}), "hard"), preferences=_mapping(data.get("preferences", {}), "preferences"))


@dataclass(frozen=True)
class V1IntentPlan:
    schema_version: int
    goal: V1Goal
    viewpoint: str = "buyer"
    constraints_delta: V1ConstraintsDelta = field(default_factory=V1ConstraintsDelta)
    selected_option_ref: str | None = None
    selected_lot_ref: str | None = None
    requested_facts: tuple[str, ...] = ()
    operator_intent: V1OperatorIntent = V1OperatorIntent.NONE
    clarification: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    confidence: float = 1.0

    def __post_init__(self):
        if self.schema_version != SCHEMA_VERSION:
            raise V1Error("schema_version must be 1")
        object.__setattr__(self, "goal", V1Goal.coerce(self.goal))
        object.__setattr__(self, "operator_intent", V1OperatorIntent.coerce(self.operator_intent))
        _optional_str(self.selected_option_ref, "selected_option_ref")
        _optional_str(self.selected_lot_ref, "selected_lot_ref")
        _optional_str(self.clarification, "clarification")
        _optional_str(self.contact_name, "contact_name")
        _optional_str(self.contact_phone, "contact_phone")
        if not isinstance(self.viewpoint, str) or not self.viewpoint.strip():
            raise V1Error("viewpoint must be string")
        if not isinstance(self.constraints_delta, V1ConstraintsDelta):
            object.__setattr__(self, "constraints_delta", V1ConstraintsDelta.from_dict(self.constraints_delta))
        requested = tuple(self.requested_facts or ())
        if any(not isinstance(v, str) or not v for v in requested):
            raise V1Error("requested_facts must be strings")
        object.__setattr__(self, "requested_facts", requested)
        if isinstance(self.confidence, bool):
            raise V1Error("confidence must be numeric")
        c = float(self.confidence)
        if not 0 <= c <= 1:
            raise V1Error("confidence must be 0..1")
        object.__setattr__(self, "confidence", c)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "goal": self.goal.value,
            "viewpoint": self.viewpoint,
            "constraints_delta": self.constraints_delta.to_dict(),
            "selected_option_ref": self.selected_option_ref,
            "selected_lot_ref": self.selected_lot_ref,
            "requested_facts": list(self.requested_facts),
            "operator_intent": self.operator_intent.value,
            "clarification": self.clarification,
            "contact_name": self.contact_name,
            "contact_phone": self.contact_phone,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "V1IntentPlan":
        _reject_unknown(data, {f.name for f in fields(cls)})
        return cls(**dict(data))


def _reject_unknown(data: Mapping[str, Any], allowed: set[str]) -> None:
    if not isinstance(data, Mapping):
        raise V1Error("expected object")
    extra = set(data) - allowed
    if extra:
        raise V1Error("unknown fields: " + ",".join(sorted(extra)))


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V1Error(f"{name} must be object")
    return dict(value)


def _optional_str(value: Any, name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise V1Error(f"{name} must be string or null")


def deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): deep_freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(v) for v in value)
    return value


def deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): deep_thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [deep_thaw(v) for v in value]
    if isinstance(value, list):
        return [deep_thaw(v) for v in value]
    return value


def dataclass_to_dict(obj: Any) -> Any:
    if isinstance(obj, StrictEnum):
        return obj.value
    if is_dataclass(obj):
        return {f.name: dataclass_to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, tuple):
        return [dataclass_to_dict(v) for v in obj]
    if isinstance(obj, list):
        return [dataclass_to_dict(v) for v in obj]
    if isinstance(obj, Mapping):
        return {str(k): dataclass_to_dict(v) for k, v in obj.items()}
    return obj
