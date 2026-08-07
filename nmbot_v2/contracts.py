from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Mapping


JsonDict = dict[str, Any]


def _tuple_str(value: Any) -> tuple[str, ...]:
    if value in (None, "", [], {}, ()): 
        return ()
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _comparison_option_names(value: Any) -> tuple[str, str]:
    if value in ((), []):
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError("comparison_option_names must be a list/tuple of exactly two non-empty strings")
    if len(value) != 2:
        raise ValueError("comparison_option_names must contain exactly two non-empty strings")

    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("comparison_option_names items must be strings")
        text = item.strip()
        if not text:
            raise ValueError("comparison_option_names items must be non-empty strings")
        out.append(text)
    if out[0] == out[1]:
        raise ValueError("comparison_option_names must contain two distinct names")
    return (out[0], out[1])


class Stage(str, Enum):
    RESET = "reset"
    FIRST_LIST = "first_list"
    REFINEMENT = "refinement"
    CURRENT_OPTIONS = "current_options"
    SELECTED_OBJECT = "selected_object"
    FINANCING_CLARIFICATION = "financing_clarification"
    SELECTED_LIVE_FACT_CLARIFICATION = "selected_live_fact_clarification"
    OPERATOR_HANDOFF = "operator_handoff"
    OPERATOR_DECLINED = "operator_declined"
    OFF_TOPIC = "off_topic"
    FREEFORM = "freeform"
    ERROR = "error"


class TurnAction(str, Enum):
    RESET = "reset"
    SEARCH = "search"
    ANSWER_FROM_CURRENT_OPTIONS = "answer_from_current_options"
    ANSWER_SELECTED_OPTION = "answer_selected_option"
    CLARIFY_FINANCING = "clarify_financing"
    CLARIFY_SELECTED_LIVE_FACT = "clarify_selected_live_fact"
    OFFER_OPERATOR = "offer_operator"
    ACCEPT_OPERATOR = "accept_operator"
    DECLINE_OPERATOR = "decline_operator"
    FREEFORM = "freeform"
    ANSWER_OFF_TOPIC = "answer_off_topic"
    SAFE_ERROR = "safe_error"


class IntentGoal(str, Enum):
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


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_str(value: Any, field_name: str) -> str:
    text = _optional_str(value)
    if text is None:
        raise ValueError(f"{field_name} is required")
    return text


def _bounded_confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("confidence must be a number in 0..1")
    confidence = float(value)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be in 0..1")
    return confidence


@dataclass(frozen=True)
class IntentPlanV3:
    schema_version: int
    goal: IntentGoal
    viewpoint: str
    selected_option_name: str | None = None
    named_object_reference: str | None = None
    comparison_option_names: tuple[str, str] = ()
    requested_facts: tuple[str, ...] = ()
    constraints_delta: JsonDict = field(default_factory=dict)
    operator_consent: bool | None = None
    explicit_operator_request: bool = False
    clarification: str | None = None
    confidence: float = 1.0
    query_text: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version != 3:
            raise ValueError("schema_version must be 3")
        try:
            goal = self.goal if isinstance(self.goal, IntentGoal) else IntentGoal(str(self.goal or "").strip())
        except ValueError as exc:
            raise ValueError("invalid IntentPlanV3 goal") from exc
        if not isinstance(self.constraints_delta, Mapping):
            raise ValueError("constraints_delta must be a mapping")
        if self.operator_consent is not None and not isinstance(self.operator_consent, bool):
            raise ValueError("operator_consent must be boolean or null")
        if not isinstance(self.explicit_operator_request, bool):
            raise ValueError("explicit_operator_request must be boolean")
        object.__setattr__(self, "goal", goal)
        object.__setattr__(self, "viewpoint", _required_str(self.viewpoint, "viewpoint"))
        object.__setattr__(self, "selected_option_name", _optional_str(self.selected_option_name))
        object.__setattr__(self, "named_object_reference", _optional_str(self.named_object_reference))
        object.__setattr__(self, "comparison_option_names", _comparison_option_names(self.comparison_option_names))
        object.__setattr__(self, "requested_facts", _tuple_str(self.requested_facts))
        object.__setattr__(self, "constraints_delta", deepcopy(dict(self.constraints_delta)))
        object.__setattr__(self, "clarification", _optional_str(self.clarification))
        object.__setattr__(self, "confidence", _bounded_confidence(self.confidence))
        object.__setattr__(self, "query_text", _optional_str(self.query_text))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IntentPlanV3":
        if not isinstance(data, Mapping):
            raise ValueError("IntentPlanV3 input must be a mapping")

        allowed = set(cls.__dataclass_fields__)
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown IntentPlanV3 fields: {', '.join(sorted(str(x) for x in unknown))}")
        constraints_delta = data.get("constraints_delta", {})
        if constraints_delta is None:
            constraints_delta = {}

        return cls(
            schema_version=data.get("schema_version"),
            goal=data.get("goal"),
            viewpoint=data.get("viewpoint"),
            selected_option_name=data.get("selected_option_name"),
            named_object_reference=data.get("named_object_reference"),
            comparison_option_names=data.get("comparison_option_names", ()),
            requested_facts=data.get("requested_facts", ()),
            constraints_delta=constraints_delta,
            operator_consent=data.get("operator_consent"),
            explicit_operator_request=data.get("explicit_operator_request", False),
            clarification=data.get("clarification"),
            confidence=data.get("confidence", 1.0),
            query_text=data.get("query_text"),
        )

    def to_dict(self) -> JsonDict:
        return {
            "schema_version": self.schema_version,
            "goal": self.goal.value,
            "viewpoint": self.viewpoint,
            "selected_option_name": self.selected_option_name,
            "named_object_reference": self.named_object_reference,
            "comparison_option_names": list(self.comparison_option_names),
            "requested_facts": list(self.requested_facts),
            "constraints_delta": deepcopy(self.constraints_delta),
            "operator_consent": self.operator_consent,
            "explicit_operator_request": self.explicit_operator_request,
            "clarification": self.clarification,
            "confidence": self.confidence,
            "query_text": self.query_text,
        }


@dataclass(frozen=True)
class LotExample:
    id: str | int | None = None
    rooms: str | int | None = None
    area_m2: int | float | None = None
    floor: int | None = None
    floors_total: int | None = None
    full_price: int | float | None = None
    renovation: str | None = None
    status: str | int | None = None
    house_id: str | int | None = None
    house_name: str | None = None
    source: str | None = None
    living_space: int | float | None = None
    kitchen_area: int | float | None = None
    balcony: str | None = None
    bathroom: str | None = None
    ceiling_height: int | float | str | None = None
    window_view: str | None = None
    layout_features: tuple[str, ...] = ()
    state: str | int | None = None
    ready: str | int | bool | None = None
    delivered: str | int | bool | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LotExample":
        allowed = cls.__dataclass_fields__.keys()
        values = {k: data[k] for k in allowed if k in data}
        if "layout_features" in values and isinstance(values["layout_features"], list):
            values["layout_features"] = tuple(str(x) for x in values["layout_features"])
        return cls(**values)


@dataclass(frozen=True)
class OptionCard:
    name: str
    district: str | None = None
    location: str | None = None
    price: str | None = None
    price_min: int | None = None
    rooms: int | str | None = None
    finishing: str | None = None
    area: str | None = None
    ready: str | None = None
    metro: str | None = None
    developer: str | None = None
    property_class: str | None = None
    ecology_rating: str | int | float | None = None
    infrastructure: tuple[str, ...] = ()
    daily_services: tuple[str, ...] = ()
    healthcare: tuple[str, ...] = ()
    ads_count: int | None = None
    sales_count: int | None = None
    sales_date: str | None = None
    discount: str | None = None
    parking: bool | str | None = None
    parking_price: str | int | float | None = None
    parking_inventory: str | int | None = None
    apartment_inventory: str | int | bool | None = None
    mortgage_terms: str | None = None
    mortgage_rate: str | int | float | None = None
    mortgage_down_payment: str | int | float | None = None
    mortgage_term: str | int | None = None
    installment_months: str | int | None = None
    transport_access: tuple[str, ...] = ()
    room_prices: tuple[JsonDict, ...] = ()
    price_square: str | int | float | None = None
    recurring_costs: str | int | float | None = None
    purchase_terms: tuple[str, ...] = ()
    building_profile: tuple[str, ...] = ()
    property_formats: tuple[str, ...] = ()
    room_formats: tuple[str, ...] = ()
    lot_examples: tuple[LotExample, ...] = ()
    why_close: str | None = None
    is_near: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OptionCard":
        allowed = cls.__dataclass_fields__.keys()
        values = {k: data[k] for k in allowed if k in data}
        for key in ("infrastructure", "daily_services", "healthcare", "transport_access", "purchase_terms", "building_profile", "property_formats", "room_formats"):
            if key in values and isinstance(values[key], list):
                values[key] = tuple(str(x) for x in values[key])
        if "room_prices" in values and isinstance(values["room_prices"], list):
            values["room_prices"] = tuple(dict(x) for x in values["room_prices"] if isinstance(x, Mapping))
        if "lot_examples" in values:
            raw_lots = values["lot_examples"]
            if isinstance(raw_lots, Mapping):
                raw_lots = [raw_lots]
            values["lot_examples"] = tuple(
                item if isinstance(item, LotExample) else LotExample.from_dict(item)
                for item in (raw_lots or ())
                if isinstance(item, (Mapping, LotExample))
            )
        return cls(**values)


@dataclass(frozen=True)
class DialogFocus:
    subject: str | None = None
    last_intent: str | None = None
    last_requested_facts: tuple[str, ...] = ()
    last_answered_facts: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "DialogFocus":
        if not isinstance(data, Mapping):
            return cls()
        return cls(
            subject=str(data.get("subject") or "").strip() or None,
            last_intent=str(data.get("last_intent") or "").strip() or None,
            last_requested_facts=_tuple_str(data.get("last_requested_facts")),
            last_answered_facts=_tuple_str(data.get("last_answered_facts")),
        )


@dataclass(frozen=True)
class SafeTurnContext:
    conversation_ref: str
    user_text: str
    channel: str = "local"
    locale: str = "ru"
    metadata: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticPlan:
    operation: str
    # Safe per-turn original request. Search adapters must prefer this over
    # intent/reference so the current user turn is not accidentally replaced by
    # a planner label such as "investment".
    query_text: str | None = None
    intent: str | None = None
    constraints_delta: JsonDict = field(default_factory=dict)
    reference: str | None = None
    selected_option_name: str | None = None
    scope: str | None = None
    operator_consent: bool | None = None
    explicit_operator_request: bool = False
    operator_reason: str | None = None
    followup_outcome: str | None = None
    resolved_subject: str | None = None
    resolved_intent: str | None = None
    requested_facts: tuple[str, ...] = ()
    facts_needed: tuple[str, ...] = ()
    requires_enrichment: bool = False
    focus_action: str = "keep"
    domain_relation: str = "unknown"
    confidence: float = 1.0
    clarification: str | None = None
    facets: list[str] = field(default_factory=list)
    # Только текущий ход: клиент явно просит новые варианты без повторов.
    # Флаг не хранится в ConversationState и не влияет на обычный refinement.
    fresh_search: bool = False


@dataclass(frozen=True)
class ExecutableTurn:
    """Compiled V3 turn contract.

    Unlike ``SemanticPlan`` this is already validated and transitioned.  V3
    downstream must use ``goal``/``stage``/``action`` directly and must not
    route through a legacy operation string.
    """

    goal: IntentGoal
    stage: Stage
    action: TurnAction
    accepted: bool = True
    error_code: str | None = None
    query_text: str | None = None
    viewpoint: str = "unchanged"
    intent: str | None = None
    constraints_delta: JsonDict = field(default_factory=dict)
    reference: str | None = None
    selected_option_name: str | None = None
    named_object_reference: str | None = None
    comparison_option_names: tuple[str, str] = ()
    scope: str | None = None
    operator_consent: bool | None = None
    explicit_operator_request: bool = False
    operator_reason: str | None = None
    followup_outcome: str | None = None
    resolved_subject: str | None = None
    resolved_intent: str | None = None
    requested_facts: tuple[str, ...] = ()
    facts_needed: tuple[str, ...] = ()
    requires_enrichment: bool = False
    focus_action: str = "keep"
    domain_relation: str = "unknown"
    confidence: float = 1.0
    clarification: str | None = None
    facets: list[str] = field(default_factory=list)
    fresh_search: bool = False
    trace_metadata: JsonDict = field(default_factory=dict)


TurnPlan = SemanticPlan | ExecutableTurn


@dataclass(frozen=True)
class RetrySearchContext:
    """Bounded semantic envelope for retrying a failed V2 search.

    This is intentionally not a conversation transcript: it must never contain
    raw user text, prompts, provider payloads, result cards or contacts.
    """

    viewpoint: str | None = None
    intent: str | None = None
    hard_constraints: JsonDict = field(default_factory=dict)
    preferences: JsonDict = field(default_factory=dict)
    error_code: str | None = None
    attempt_kind: str = "initial"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "RetrySearchContext | None":
        if not isinstance(data, Mapping):
            return None
        attempt = str(data.get("attempt_kind") or "initial").strip()
        if attempt not in {"initial", "refresh"}:
            attempt = "initial"
        hard = data.get("hard_constraints") if isinstance(data.get("hard_constraints"), Mapping) else {}
        prefs = data.get("preferences") if isinstance(data.get("preferences"), Mapping) else {}
        return cls(
            viewpoint=str(data.get("viewpoint") or "").strip() or None,
            intent=str(data.get("intent") or "").strip() or None,
            hard_constraints=dict(hard),
            preferences=dict(prefs),
            error_code=str(data.get("error_code") or "").strip() or None,
            attempt_kind=attempt,
        )


@dataclass(frozen=True)
class SearchResult:
    facts: tuple[OptionCard, ...] = ()
    near: tuple[OptionCard, ...] = ()
    missing: tuple[str, ...] = ()
    params: JsonDict = field(default_factory=dict)
    summary: str | None = None

    def shortlist(self, limit: int = 3) -> tuple[OptionCard, ...]:
        """Canonical visible/renderable search cards.

        Exact facts are the exclusive visible source when present; near
        alternatives are rendered only for near-only results.  Selection keeps
        stable name-based deduplication and never modifies raw ``facts`` or
        ``near``, so a near-only card keeps its ``is_near`` marker.
        """

        try:
            max_items = max(0, int(limit))
        except (TypeError, ValueError):
            max_items = 3
        if max_items <= 0:
            return ()
        seen: set[str] = set()
        cards: list[OptionCard] = []
        source = self.facts if self.facts else self.near
        for card in source:
            key = _normalized_option_name(card.name)
            if not key or key in seen:
                continue
            seen.add(key)
            cards.append(card)
            if len(cards) >= max_items:
                break
        return tuple(cards)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SearchResult":
        return cls(
            facts=tuple(OptionCard.from_dict(x) for x in data.get("facts", [])),
            near=tuple(OptionCard.from_dict({**x, "is_near": True}) for x in data.get("near", [])),
            missing=tuple(data.get("missing", [])),
            params=dict(data.get("params", {})),
            summary=data.get("summary"),
        )


def _normalized_option_name(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().replace("ё", "е").split())


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    search: SearchResult | None = None
    selected: OptionCard | None = None
    comparison_cards: tuple[OptionCard, ...] = ()
    comparison_cache_additions: tuple[Any, ...] = ()
    comparison_metadata: JsonDict = field(default_factory=dict)
    fresh_facts: tuple[str, ...] = ()
    message: str | None = None
    error_code: str | None = None
    retry_count: int = 0
    attempts: tuple[JsonDict, ...] = ()
    bridge_status: str | None = None


@dataclass(frozen=True)
class StateDelta:
    params_update: JsonDict = field(default_factory=dict)
    reset: bool = False
    pending_followup: str | None = None
    selected_option_name: str | None = None
    visible_options: tuple[OptionCard, ...] | None = None
    previous_options: tuple[OptionCard, ...] | None = None
    last_search: SearchResult | None = None
    operator_offered: bool | None = None
    operator_declined: bool | None = None
    active_topic: str | None = None
    dialog_focus: DialogFocus | None = None
    selected_enriched: OptionCard | None = None
    enriched_card_cache: tuple[Any, ...] | None = None
    last_assistant_question: str | None = None
    last_answer_kind: str | None = None
    last_offer: JsonDict | None = None
    already_asked_add: tuple[str, ...] = ()
    answered_add: tuple[str, ...] = ()
    contact_name: str | None = None
    contact_phone_redacted: str | None = None
    contact_consent: bool | None = None
    callback_ref: str | None = None
    retry_search: RetrySearchContext | None = None
    append_recent_turn: JsonDict | None = None
    append_dialogue_turn: JsonDict | None = None
    clear_fields: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self == StateDelta()


@dataclass(frozen=True)
class ResponsePlan:
    acknowledgement: str
    changed_constraints: tuple[str, ...] = ()
    result_summary: str | None = None
    cards: tuple[OptionCard, ...] = ()
    caveat: str | None = None
    final_question: str = "Что смотрим дальше?"
    operator_prompt: bool = False
    answer_kind: str = "generic"
    viewpoint: str | None = None
    base_viewpoint: str | None = None
    scenario_needs: tuple[str, ...] = ()
    recipe_id: str = "default_clarification"
    recipe_cards: tuple[JsonDict, ...] = ()
    anchor_fact: str = ""
    allowed_benefit: str = ""
    forbidden_inferences: tuple[str, ...] = ()
    cta_template: str = ""
    composition_mode: str = "bounded"
    reply_contract_id: str | None = None


@dataclass(frozen=True)
class ResponseBrief:
    """Safe model-facing answer contract.

    This is the only customer-composition payload the communication model may
    see.  It contains canonical cards and bounded dialogue context, never MCP
    wire fields, raw provider payloads, credentials or internal routing enums.
    """

    answer_goal: str
    user_question: str = ""
    question_subject: str = ""
    requested_facts: tuple[str, ...] = ()
    available_facts: tuple[str, ...] = ()
    missing_facts: tuple[str, ...] = ()
    response_policy: str = ""
    operator_handoff_template: str = ""
    response_viewpoint: str = "life"
    base_viewpoint: str | None = None
    acknowledgement: str = ""
    state_delta_summary: tuple[str, ...] = ()
    canonical_cards: tuple[OptionCard, ...] = ()
    canonical_missing_summary: tuple[str, ...] = ()
    selected_scope: str = "all"
    current_scope: str = "unknown"
    allowed_fact_fields: tuple[str, ...] = ()
    allowed_claims: tuple[str, ...] = ()
    recent_safe_context: tuple[JsonDict, ...] = ()
    scenario_context: JsonDict = field(default_factory=dict)
    recipe_id: str = "default"
    anchor_fact: str = ""
    allowed_benefit: str = ""
    forbidden_inferences: tuple[str, ...] = ()
    cta_template: str = ""
    recipe_cards: tuple[JsonDict, ...] = ()
    exactly_one_question_policy: str = "exactly_one_final_question"
    fallback_question: str = "Какой вариант хотите рассмотреть подробнее?"


@dataclass(frozen=True)
class ComposedOption:
    name: str
    facts: str
    description: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ComposedOption":
        return cls(
            name=str(data.get("name") or "").strip(),
            facts=str(data.get("facts") or "").strip(),
            description=str(data.get("description") or "").strip(),
        )


@dataclass(frozen=True)
class ComposedResponse:
    intro: str
    options: tuple[ComposedOption, ...] = ()
    recommendation: str = ""
    missing_note: str = ""
    final_question: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ComposedResponse":
        options = data.get("options") if isinstance(data.get("options"), list) else []
        return cls(
            intro=str(data.get("intro") or "").strip(),
            options=tuple(ComposedOption.from_dict(x) for x in options if isinstance(x, Mapping)),
            recommendation=str(data.get("recommendation") or "").strip(),
            missing_note=str(data.get("missing_note") or "").strip(),
            final_question=str(data.get("final_question") or "").strip(),
        )


@dataclass(frozen=True)
class TurnResult:
    context: SafeTurnContext
    semantic_plan: TurnPlan
    stage: Stage
    action: TurnAction
    execution: ExecutionResult
    state_delta: StateDelta
    response_plan: ResponsePlan
    response_text: str
    state: JsonDict
    trace: JsonDict = field(default_factory=dict)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {k: to_jsonable(v) for k, v in asdict(value).items() if v is not None}
    if isinstance(value, tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items() if v is not None}
    return value
