"""Minimal V6-owned, phone-safe conversation state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import SCHEMA_VERSION, STATE_NAMESPACE
from .contracts import ContractError
from .privacy import PHONEISH, immutable_safe_copy

_MAX_OPTION_REFS = 20
_MAX_CURRENT_CARDS = 3
_MAX_CARD_NODES = 300
_MAX_CARD_BYTES = 20_000
_SECRET_KEY = re.compile(r"token|secret|password|authorization|api[_-]?key|credential|prompt|metadata|mcp[_-]?servers", re.I)
_SECRET_VALUE = re.compile(r"(?:bearer\s+\S+|(?:sk|pk|ghp|xox[baprs]?)[_-][A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]{16,})", re.I)
_STATE_PROVENANCE_KEY = re.compile(
    r"(?:^|[_-])(?:task[_-]*ref|payload|raw)(?:$|[_-])", re.I
)
_CARD_KEYS = frozenset({
    "name", "ref", "id", "object_id", "option_ref", "district", "location", "price",
    "price_min", "price_max", "price_range", "finishing", "metro", "area", "rooms",
    "ready", "developer", "link", "infrastructure", "family_infrastructure", "schools",
    "kindergartens", "parks", "shops", "clinics", "yards", "transport", "why_family", "why_close",
    "novos_id", "ads", "apartment_types", "house", "lot_examples", "ads_add",
})

_FIELDS = {
    "namespace", "schema_version", "revision", "pending_phone", "safe_context",
    "option_refs", "selected_option_ref",
    "current_cards",
    "pending_interaction",
}
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_QUESTION_GOALS = frozenset({
    "continue_search", "learn_about_complex", "choose_complex",
    "offer_layouts_or_viewing", "answer_viewing_request", "operator_contact",
})
_INTERACTION_KINDS = frozenset({"offer", "selection"})
_INTERACTION_ACTIONS = frozenset({
    "show_stored_details", "show_layouts", "select_subject", "normal_prompt1", "clear_pending",
})


def _validate_ref(value: str, name: str) -> None:
    if not isinstance(value, str) or not _REF.fullmatch(value) or value.isdigit():
        raise ContractError(f"{name} must be an opaque reference")
    if PHONEISH.search(value):
        raise ContractError(f"{name} must not contain a phone number")


@dataclass(frozen=True)
class PendingInteraction:
    """Bounded code-owned interpretation of the last completed bot question."""

    kind: str
    question_goal: str
    accept_action: str
    reject_action: str
    subject_refs: tuple[str, ...]
    created_revision: int

    def __post_init__(self) -> None:
        if self.kind not in _INTERACTION_KINDS:
            raise ContractError("pending interaction kind is invalid")
        if self.question_goal not in _QUESTION_GOALS:
            raise ContractError("pending interaction question goal is invalid")
        if self.accept_action not in _INTERACTION_ACTIONS or self.reject_action != "clear_pending":
            raise ContractError("pending interaction action is invalid")
        if type(self.subject_refs) not in (list, tuple) or len(self.subject_refs) > _MAX_OPTION_REFS:
            raise ContractError("pending interaction subject refs are invalid")
        for ref in self.subject_refs:
            _validate_ref(ref, "pending interaction subject ref")
        if len(set(self.subject_refs)) != len(self.subject_refs):
            raise ContractError("pending interaction subject refs must be unique")
        if isinstance(self.created_revision, bool) or not isinstance(self.created_revision, int) \
                or self.created_revision < 0:
            raise ContractError("pending interaction revision is invalid")
        object.__setattr__(self, "subject_refs", tuple(self.subject_refs))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PendingInteraction":
        fields = {
            "kind", "question_goal", "accept_action", "reject_action",
            "subject_refs", "created_revision",
        }
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise ContractError("pending interaction fields are invalid")
        return cls(**dict(raw))

    def safe_projection(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "question_goal": self.question_goal,
            "accept_action": self.accept_action,
            "reject_action": self.reject_action,
            "subject_refs": list(self.subject_refs),
            "created_revision": self.created_revision,
        }


def _charge_card(budget: dict[str, int], amount: int) -> None:
    budget["nodes"] += 1
    budget["bytes"] += amount
    if budget["nodes"] > _MAX_CARD_NODES or budget["bytes"] > _MAX_CARD_BYTES:
        raise ContractError("current card aggregate budget exceeded")


def _reject_state_provenance_keys(value: Any) -> None:
    """Reject transport provenance only at the persisted V6 state boundary."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or _STATE_PROVENANCE_KEY.search(key):
                raise ContractError("V6 state contains a forbidden provenance key")
            _reject_state_provenance_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_state_provenance_keys(item)


def _bounded_card(value: Mapping[str, Any], depth: int = 0, budget: dict[str, int] | None = None) -> Mapping[str, Any]:
    if budget is None:
        budget = {"nodes": 0, "bytes": 0}
    if depth > 3 or not isinstance(value, Mapping) or len(value) > 32 or (depth == 0 and set(value) - _CARD_KEYS):
        raise ContractError("current card is not bounded or allowlisted")
    _charge_card(budget, len(value))
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 80 or _SECRET_KEY.search(key):
            raise ContractError("current card contains a forbidden key")
        _charge_card(budget, len(key.encode("utf-8")))
        if isinstance(item, Mapping):
            result[key] = _bounded_card(item, depth + 1, budget)
        elif isinstance(item, (list, tuple)):
            if len(item) > 20:
                raise ContractError("current card list is too large")
            _charge_card(budget, len(item))
            result[key] = tuple(
                _bounded_card(child, depth + 1, budget) if isinstance(child, Mapping)
                else _bounded_card_value(child, depth + 1, budget)
                for child in item
            )
        elif isinstance(item, str):
            if len(item) > 500 or PHONEISH.search(item) or _SECRET_VALUE.search(item):
                raise ContractError("current card text is too long")
            _charge_card(budget, len(item.encode("utf-8")))
            result[key] = item
        elif item is None or isinstance(item, (bool, int, float)):
            _charge_card(budget, 16)
            result[key] = item
        else:
            raise ContractError("current card value is invalid")
    return result


def _bounded_card_value(value: Any, depth: int, budget: dict[str, int]) -> Any:
    if isinstance(value, Mapping):
        return _bounded_card(value, depth, budget)
    if isinstance(value, (list, tuple)):
        if depth > 3 or len(value) > 20:
            raise ContractError("nested current card value is too large")
        _charge_card(budget, len(value))
        return tuple(_bounded_card_value(item, depth + 1, budget) for item in value)
    if isinstance(value, str):
        if len(value) > 500 or PHONEISH.search(value) or _SECRET_VALUE.search(value):
            raise ContractError("nested current card text is too long")
        _charge_card(budget, len(value.encode("utf-8")))
        return value
    if value is None or isinstance(value, (bool, int, float)):
        _charge_card(budget, 16)
        return value
    raise ContractError("nested current card value is invalid")


@dataclass(frozen=True)
class V6State:
    namespace: str = STATE_NAMESPACE
    schema_version: int = SCHEMA_VERSION
    revision: int = 0
    pending_phone: bool = False
    safe_context: Mapping[str, Any] = field(default_factory=dict)
    option_refs: tuple[str, ...] = ()
    selected_option_ref: str | None = None
    current_cards: tuple[Mapping[str, Any], ...] = ()
    pending_interaction: PendingInteraction | None = None

    def __post_init__(self) -> None:
        if self.namespace != STATE_NAMESPACE or self.schema_version != SCHEMA_VERSION:
            raise ContractError("wrong V6 state identity")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ContractError("revision must be a non-negative integer")
        if type(self.pending_phone) is not bool:
            raise ContractError("pending_phone must be boolean")
        if not isinstance(self.safe_context, Mapping):
            raise ContractError("safe_context must be an object")
        _reject_state_provenance_keys(self.safe_context)
        if type(self.option_refs) not in (list, tuple):
            raise ContractError("option_refs must be an array")
        for ref in self.option_refs:
            _validate_ref(ref, "option ref")
        if self.selected_option_ref is not None:
            _validate_ref(self.selected_option_ref, "selected option ref")
            if self.selected_option_ref not in self.option_refs:
                raise ContractError("selected option ref must be present in option_refs")
        if type(self.current_cards) not in (list, tuple) or len(self.current_cards) > _MAX_CURRENT_CARDS:
            raise ContractError("current_cards must be a bounded array")
        _reject_state_provenance_keys(self.current_cards)
        pending = self.pending_interaction
        if isinstance(pending, Mapping):
            pending = PendingInteraction.from_mapping(pending)
        if pending is not None and type(pending) is not PendingInteraction:
            raise ContractError("pending_interaction must be typed")
        budget = {"nodes": 0, "bytes": 0}
        bounded_cards = tuple(_bounded_card(card, budget=budget) for card in self.current_cards)
        object.__setattr__(self, "safe_context", immutable_safe_copy(self.safe_context))
        object.__setattr__(self, "option_refs", tuple(self.option_refs))
        object.__setattr__(self, "current_cards", tuple(immutable_safe_copy(bounded_cards)))
        object.__setattr__(self, "pending_interaction", pending)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "V6State":
        if not isinstance(raw, Mapping) or set(raw) - _FIELDS:
            raise ContractError("unknown V6 state field")
        return cls(**dict(raw))

    def safe_projection(self) -> dict[str, Any]:
        def plain(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {str(key): plain(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [plain(item) for item in value]
            return value

        return {
            "namespace": self.namespace,
            "schema_version": self.schema_version,
            "revision": self.revision,
            "pending_phone": self.pending_phone,
            "safe_context": plain(self.safe_context),
            "option_refs": list(self.option_refs),
            "selected_option_ref": self.selected_option_ref,
            "current_cards": [plain(card) for card in self.current_cards],
            "pending_interaction": (
                self.pending_interaction.safe_projection() if self.pending_interaction else None
            ),
        }


def evolve_completed_state(
    state: V6State,
    plan: Any,
    evidence: Any,
    *,
    pending_phone: bool | None = None,
    question_goal: str | None = None,
) -> V6State:
    """Advance state only from validated structural Prompt 1 and transport data."""

    from .prompt1_contract import Prompt1Result, SearchAction, SearchPolicy
    from .provider import TrustedMcpEnvelope

    if type(state) is not V6State or type(plan) is not Prompt1Result \
            or type(evidence) is not TrustedMcpEnvelope:
        raise ContractError("completed state inputs have the wrong type")
    if plan.search_policy is SearchPolicy.REQUIRED:
        option_refs = tuple(evidence.visible_refs[:_MAX_OPTION_REFS])
        raw_cards = evidence.safe_facts.get("facts", []) if isinstance(evidence.safe_facts, Mapping) else []
        if not isinstance(raw_cards, (list, tuple)):
            raw_cards = []
        if not raw_cards and isinstance(evidence.safe_facts, Mapping):
            raw_cards = evidence.safe_facts.get("cards", [])
        if not isinstance(raw_cards, (list, tuple)):
            raw_cards = []
        current_cards = tuple(
            {key: value for key, value in card.items() if key in _CARD_KEYS}
            for card in raw_cards[:_MAX_CURRENT_CARDS]
            if isinstance(card, Mapping)
        )
    else:
        option_refs = state.option_refs[:_MAX_OPTION_REFS]
        current_cards = state.current_cards[:_MAX_CURRENT_CARDS]
    selected = state.selected_option_ref if state.selected_option_ref in option_refs else None
    context = {"last_action": plan.action.value}
    if isinstance(evidence.effective_constraints, Mapping) and evidence.effective_constraints:
        context["effective_constraints"] = immutable_safe_copy(evidence.effective_constraints)
    if question_goal is not None:
        if question_goal not in _QUESTION_GOALS:
            raise ContractError("question_goal is invalid")
        context["last_question_goal"] = question_goal
        context["last_offer_type"] = question_goal
    next_revision = state.revision + 1
    pending_interaction = _pending_for_question(
        question_goal, option_refs, selected, current_cards, next_revision
    )
    return V6State(
        revision=next_revision,
        pending_phone=(
            plan.action is SearchAction.OPERATOR_CONTACT
            if pending_phone is None else pending_phone
        ),
        safe_context=context,
        option_refs=option_refs,
        selected_option_ref=selected,
        current_cards=current_cards,
        pending_interaction=pending_interaction,
    )


def _pending_for_question(
    question_goal: str | None,
    option_refs: tuple[str, ...],
    selected_option_ref: str | None,
    current_cards: tuple[Mapping[str, Any], ...],
    revision: int,
) -> PendingInteraction | None:
    if question_goal is None:
        return None
    if question_goal == "learn_about_complex":
        refs = _single_card_subject_refs(current_cards, option_refs)
        if not refs:
            return None
        return PendingInteraction(
            "offer", question_goal, "show_stored_details", "clear_pending", refs, revision
        )
    if question_goal == "offer_layouts_or_viewing":
        refs = _single_card_subject_refs(current_cards, option_refs)
        if not refs:
            return None
        return PendingInteraction(
            "offer", question_goal, "show_layouts", "clear_pending", refs, revision
        )
    if question_goal == "choose_complex":
        if not option_refs:
            return None
        return PendingInteraction(
            "selection", question_goal, "normal_prompt1", "clear_pending",
            option_refs, revision,
        )
    refs = (selected_option_ref,) if selected_option_ref else _single_card_subject_refs(
        current_cards, option_refs
    )
    return PendingInteraction(
        "offer", question_goal, "normal_prompt1", "clear_pending", refs, revision
    )


def _single_card_subject_refs(
    cards: tuple[Mapping[str, Any], ...], option_refs: tuple[str, ...]
) -> tuple[str, ...]:
    if len(cards) != 1:
        return ()
    for key in ("ref", "id", "object_id", "option_ref"):
        value = cards[0].get(key)
        if isinstance(value, str) and value in option_refs:
            return (value,)
    return ("card:0",)
