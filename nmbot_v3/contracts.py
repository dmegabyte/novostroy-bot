"""Closed, V3-owned shell contracts; this is not production-parity behavior."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Mapping


SCHEMA_VERSION = "V3"
# Russian numbers: +7/7/8 plus ten national digits, or a bare 10-digit mobile
# beginning with 9. Separators are allowed, but short and price-like amounts are not.
PHONE_RE = re.compile(
    r"(?<!\d)(?:(?:\+7|7|8)[\s().-]*(?:\d[\s().-]*){10}|9(?:[\s().-]*\d){9})(?!\d)"
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


class V3ContractError(ValueError):
    pass


class _StrictEnum(str, Enum):
    @classmethod
    def coerce(cls, value: Any):
        try:
            return value if isinstance(value, cls) else cls(value)
        except (TypeError, ValueError) as exc:
            raise V3ContractError(f"invalid_{cls.__name__.lower()}") from exc


class V3Stage(_StrictEnum):
    RESET = "reset"
    CLARIFY = "clarify"
    ANSWERED = "answered"


class V3Action(_StrictEnum):
    CLARIFY = "clarify"
    RESPOND = "respond"


class IntentGoalV3(_StrictEnum):
    NEW_SEARCH = "new_search"
    REFINE_SEARCH = "refine_search"
    EXPAND_SEARCH = "expand_search"
    LOOKUP_OBJECT = "lookup_object"
    ANSWER_CURRENT = "answer_current"
    COMPARE_CURRENT = "compare_current"
    RECOMMEND_CURRENT = "recommend_current"
    ANSWER_SELECTED = "answer_selected"
    ANSWER_OPEN_QUESTION = "answer_open_question"
    OPERATOR = "operator"
    CLARIFY = "clarify"
    RESUME_PENDING = "resume_pending"
    OFF_TOPIC = "off_topic"


class V3SemanticStage(_StrictEnum):
    ERROR = "error"
    FIRST_LIST = "first_list"
    REFINEMENT = "refinement"
    CURRENT_OPTIONS = "current_options"
    SELECTED_OBJECT = "selected_object"
    FINANCING_CLARIFICATION = "financing_clarification"
    SELECTED_LIVE_FACT_CLARIFICATION = "selected_live_fact_clarification"
    OPERATOR_HANDOFF = "operator_handoff"
    OPERATOR_DECLINED = "operator_declined"
    FREEFORM = "freeform"
    OFF_TOPIC = "off_topic"


class V3SemanticAction(_StrictEnum):
    SEARCH = "search"
    ANSWER_CURRENT = "answer_from_current_options"
    ANSWER_SELECTED = "answer_selected_option"
    CLARIFY_FINANCING = "clarify_financing"
    CLARIFY_SELECTED_LIVE_FACT = "clarify_selected_live_fact"
    OFFER_OPERATOR = "offer_operator"
    ACCEPT_OPERATOR = "accept_operator"
    DECLINE_OPERATOR = "decline_operator"
    FREEFORM = "freeform"
    ANSWER_OFF_TOPIC = "answer_off_topic"
    SAFE_ERROR = "safe_error"


V3_ALLOWED_FACTS = frozenset({
    "name", "location", "district", "price", "price_min", "price_range",
    "rooms", "room_formats", "area", "ready", "finishing", "metro", "developer",
    "property_class", "infrastructure", "schools", "kindergartens", "parks", "yards",
    "playgrounds", "clinics", "sales_count", "sales_date", "ads_count", "discount",
    "parking", "mortgage_terms", "apartment_inventory",
})
V3_VIEWPOINTS = frozenset({"family", "life", "rental", "investment", "financing", "unchanged"})
V3_FOLLOWUP_OUTCOMES = frozenset({"accept", "decline", "ask_or_clarify", "unexpected", "resume_contact"})
V3_PENDING_FOLLOWUP_KEYS = frozenset({
    "contact_name",
    "contact_phone",
    "financing_consent",
    "selected_live_fact_consent",
})


def _optional_text(value: Any, name: str, *, maximum: int = 200) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise V3ContractError(f"invalid_{name}")
    text = " ".join(value.split())
    if not text or len(text) > maximum:
        raise V3ContractError(f"invalid_{name}")
    return text


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    if value in (None, (), []):
        return ()
    if not isinstance(value, (tuple, list)):
        raise V3ContractError(f"invalid_{name}")
    out: list[str] = []
    for item in value:
        text = _optional_text(item, name)
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _freeze_json(value: Any, name: str) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise V3ContractError(f"invalid_{name}")
        return MappingProxyType({key: _freeze_json(item, name) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, name) for item in value)
    raise V3ContractError(f"invalid_{name}")


@dataclass(frozen=True)
class V3PlannerContext:
    """Closed semantic context: opaque canonical refs and pending-state only."""

    visible_option_refs: tuple[str, ...] = ()
    pending_followup_key: str | None = None
    has_pending_action: bool = False

    def __post_init__(self) -> None:
        refs = _canonical_ref_tuple(self.visible_option_refs, "visible_option_refs")
        object.__setattr__(self, "visible_option_refs", refs)
        pending_key = _optional_text(self.pending_followup_key, "pending_followup_key", maximum=80)
        if pending_key is not None and pending_key not in V3_PENDING_FOLLOWUP_KEYS:
            raise V3ContractError("invalid_pending_followup_key")
        object.__setattr__(self, "pending_followup_key", pending_key)
        if not isinstance(self.has_pending_action, bool):
            raise V3ContractError("invalid_has_pending_action")

    def has_visible_option_ref(self, reference: str | None) -> bool:
        return isinstance(reference, str) and reference in self.visible_option_refs


def _canonical_ref_tuple(value: Any, name: str) -> tuple[str, ...]:
    """Accept only UUID identities, never a label which could be client PII."""
    import uuid

    if value in (None, (), []):
        return ()
    if not isinstance(value, (tuple, list)):
        raise V3ContractError(f"invalid_{name}")
    refs: list[str] = []
    for item in value:
        text = _optional_text(item, name, maximum=36)
        try:
            reference = str(uuid.UUID(text or ""))
        except (ValueError, AttributeError) as exc:
            raise V3ContractError(f"invalid_{name}") from exc
        if reference != text or reference in refs:
            raise V3ContractError(f"invalid_{name}")
        refs.append(reference)
    return tuple(refs)


@dataclass(frozen=True)
class IntentPlanV3:
    schema_version: int
    goal: IntentGoalV3
    viewpoint: str
    selected_option_name: str | None = None
    selected_option_ref: str | None = None
    named_object_reference: str | None = None
    comparison_option_names: tuple[str, str] = ()
    comparison_option_refs: tuple[str, str] = ()
    requested_facts: tuple[str, ...] = ()
    constraints_delta: Mapping[str, Any] = field(default_factory=dict)
    operator_consent: bool | None = None
    explicit_operator_request: bool = False
    followup_outcome: str | None = None
    clarification: str | None = None
    confidence: float = 1.0
    query_text: str | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 3:
            raise V3ContractError("invalid_schema_version")
        object.__setattr__(self, "goal", IntentGoalV3.coerce(self.goal))
        viewpoint = _optional_text(self.viewpoint, "viewpoint", maximum=40)
        if viewpoint is None:
            raise V3ContractError("missing_viewpoint")
        object.__setattr__(self, "viewpoint", viewpoint)
        object.__setattr__(self, "selected_option_name", _optional_text(self.selected_option_name, "selected_option_name"))
        selected_ref = _canonical_ref_tuple((self.selected_option_ref,) if self.selected_option_ref is not None else (), "selected_option_ref")
        object.__setattr__(self, "selected_option_ref", selected_ref[0] if selected_ref else None)
        object.__setattr__(self, "named_object_reference", _optional_text(self.named_object_reference, "named_object_reference"))
        pair = self.comparison_option_names
        if pair in ((), []):
            pair = ()
        if pair and (not isinstance(pair, (tuple, list)) or len(pair) != 2):
            raise V3ContractError("invalid_comparison_option_names")
        pair = tuple(_optional_text(item, "comparison_option_names") for item in pair)
        if pair and (None in pair or pair[0] == pair[1]):
            raise V3ContractError("invalid_comparison_option_names")
        object.__setattr__(self, "comparison_option_names", pair)
        ref_pair = self.comparison_option_refs
        if ref_pair in ((), []):
            ref_pair = ()
        if ref_pair and (not isinstance(ref_pair, (tuple, list)) or len(ref_pair) != 2):
            raise V3ContractError("invalid_comparison_option_refs")
        ref_pair = _canonical_ref_tuple(ref_pair, "comparison_option_refs")
        if ref_pair and len(ref_pair) != 2:
            raise V3ContractError("invalid_comparison_option_refs")
        object.__setattr__(self, "comparison_option_refs", ref_pair)
        object.__setattr__(self, "requested_facts", _string_tuple(self.requested_facts, "requested_facts"))
        if not isinstance(self.constraints_delta, Mapping):
            raise V3ContractError("invalid_constraints_delta")
        object.__setattr__(self, "constraints_delta", _freeze_json(self.constraints_delta, "constraints_delta"))
        if self.operator_consent is not None and not isinstance(self.operator_consent, bool):
            raise V3ContractError("invalid_operator_consent")
        if not isinstance(self.explicit_operator_request, bool):
            raise V3ContractError("invalid_explicit_operator_request")
        outcome = _optional_text(self.followup_outcome, "followup_outcome", maximum=40)
        if outcome is not None and outcome not in V3_FOLLOWUP_OUTCOMES:
            raise V3ContractError("invalid_followup_outcome")
        object.__setattr__(self, "followup_outcome", outcome)
        object.__setattr__(self, "clarification", _optional_text(self.clarification, "clarification", maximum=500))
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)) or not 0 <= float(self.confidence) <= 1:
            raise V3ContractError("invalid_confidence")
        object.__setattr__(self, "confidence", float(self.confidence))
        object.__setattr__(self, "query_text", _optional_text(self.query_text, "query_text", maximum=500))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IntentPlanV3":
        _reject_unknown(data, {field.name for field in fields(cls)})
        return cls(**dict(data))


@dataclass(frozen=True)
class ExecutableTurnV3:
    goal: IntentGoalV3
    stage: V3SemanticStage
    action: V3SemanticAction
    accepted: bool = True
    error_code: str | None = None
    query_text: str | None = None
    selected_option_name: str | None = None
    named_object_reference: str | None = None
    comparison_option_names: tuple[str, str] = ()
    requested_facts: tuple[str, ...] = ()
    followup_outcome: str | None = None
    trace_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_metadata", _freeze_json(self.trace_metadata, "trace_metadata"))


def _text(value: Any, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise V3ContractError(f"invalid_{name}")
    text = value.strip()
    if PHONE_RE.search(text):
        raise V3ContractError(f"unsafe_{name}")
    return text


def _reject_unknown(data: Mapping[str, Any], allowed: set[str]) -> None:
    if not isinstance(data, Mapping):
        raise V3ContractError("expected_object")
    if set(data) - allowed:
        raise V3ContractError("unknown_field")


@dataclass(frozen=True)
class V3PlannerResult:
    """The sole result accepted from the injected V3 planner port."""

    schema_version: str
    stage: V3Stage
    action: V3Action
    client_answer: str

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise V3ContractError("invalid_schema_version")
        object.__setattr__(self, "stage", V3Stage.coerce(self.stage))
        object.__setattr__(self, "action", V3Action.coerce(self.action))
        object.__setattr__(self, "client_answer", _text(self.client_answer, "client_answer", maximum=8000))
        allowed = {(V3Stage.CLARIFY, V3Action.CLARIFY), (V3Stage.ANSWERED, V3Action.RESPOND)}
        if (self.stage, self.action) not in allowed:
            raise V3ContractError("invalid_stage_action")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "V3PlannerResult":
        _reject_unknown(data, {field.name for field in fields(cls)})
        return cls(**dict(data))


@dataclass(frozen=True)
class V3TurnResult:
    runtime_version: str
    stage: str
    action: str
    response_text: str
    state: dict[str, Any]
    safe_code: str | None = None
