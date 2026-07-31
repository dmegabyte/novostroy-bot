from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Mapping

from .contracts import DialogFocus, OptionCard, RetrySearchContext, SearchResult, StateDelta, to_jsonable
from .pending import PendingState, normalize_pending_key, pending_state


@dataclass(frozen=True)
class EnrichedCardCacheEntry:
    """Persistent, per-dialogue full-card enrichment cache entry.

    ``identity`` is derived from canonical card fields because OptionCard does
    not expose a stable provider ID. It prevents a same-name card from another
    location/price point from silently reusing cached detail.
    """

    identity: str
    name: str
    card: OptionCard
    scenario: str
    loaded_facts: tuple[str, ...] = ()
    fetched_at: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EnrichedCardCacheEntry":
        return cls(
            identity=str(data.get("identity") or ""),
            name=str(data.get("name") or ""),
            card=OptionCard.from_dict(data.get("card") or {}),
            scenario=str(data.get("scenario") or "life"),
            loaded_facts=tuple(str(item) for item in data.get("loaded_facts", []) if str(item).strip()),
            fetched_at=str(data.get("fetched_at") or ""),
        )


def enriched_card_identity(card: OptionCard) -> str:
    def compact(value: Any) -> str:
        return " ".join(str(value or "").casefold().replace("ё", "е").split())

    return "|".join(part for part in (compact(card.name), compact(card.location), compact(card.price or card.price_min)) if part)


def enriched_cache_entry_is_fresh(entry: EnrichedCardCacheEntry, *, now: datetime | None = None, ttl_seconds: int = 900) -> bool:
    try:
        fetched = datetime.fromisoformat(entry.fetched_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return fetched + timedelta(seconds=max(1, ttl_seconds)) >= (now or datetime.now(timezone.utc))


def merge_enriched_card_cache(
    entries: tuple[EnrichedCardCacheEntry, ...], entry: EnrichedCardCacheEntry, *, limit: int = 12
) -> tuple[EnrichedCardCacheEntry, ...]:
    retained = [item for item in entries if item.identity != entry.identity]
    return tuple([entry, *retained][:max(1, limit)])


def merge_enriched_card_cache_entries(
    entries: tuple[EnrichedCardCacheEntry, ...], additions: tuple[EnrichedCardCacheEntry, ...]
) -> tuple[EnrichedCardCacheEntry, ...]:
    merged = entries
    for entry in additions:
        merged = merge_enriched_card_cache(merged, entry)
    return merged


@dataclass(frozen=True)
class ConversationState:
    params: dict[str, Any] = field(default_factory=dict)
    pending_followup: str | None = None
    selected_option_name: str | None = None
    visible_options: tuple[OptionCard, ...] = ()
    previous_options: tuple[OptionCard, ...] = ()
    last_search: SearchResult | None = None
    operator_offered: bool = False
    operator_declined: bool = False
    active_topic: str | None = None
    dialog_focus: DialogFocus = field(default_factory=DialogFocus)
    selected_enriched: OptionCard | None = None
    enriched_card_cache: tuple[EnrichedCardCacheEntry, ...] = ()
    recent_turns: tuple[dict[str, str], ...] = ()
    dialogue_turns: tuple[dict[str, str], ...] = ()
    last_assistant_question: str | None = None
    last_answer_kind: str | None = None
    already_asked: tuple[str, ...] = ()
    answered: tuple[str, ...] = ()
    contact_name: str | None = None
    contact_phone_redacted: str | None = None
    contact_consent: bool = False
    callback_ref: str | None = None
    retry_search: RetrySearchContext | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ConversationState":
        if not data:
            return cls()
        recent_turns = tuple(dict(x) for x in data.get("recent_turns", []))
        raw_dialogue = data.get("dialogue_turns", None)
        dialogue_turns = tuple(dict(x) for x in raw_dialogue) if isinstance(raw_dialogue, list) else recent_turns
        return cls(
            params=dict(data.get("params", {})),
            pending_followup=normalize_pending_key(data.get("pending_followup")),
            selected_option_name=data.get("selected_option_name"),
            visible_options=tuple(OptionCard.from_dict(x) for x in data.get("visible_options", [])),
            previous_options=tuple(OptionCard.from_dict(x) for x in data.get("previous_options", [])),
            last_search=SearchResult.from_dict(data["last_search"]) if data.get("last_search") else None,
            operator_offered=bool(data.get("operator_offered", False)),
            operator_declined=bool(data.get("operator_declined", False)),
            active_topic=data.get("active_topic"),
            dialog_focus=DialogFocus.from_dict(data.get("dialog_focus")),
            selected_enriched=OptionCard.from_dict(data["selected_enriched"]) if data.get("selected_enriched") else None,
            enriched_card_cache=tuple(
                EnrichedCardCacheEntry.from_dict(item)
                for item in data.get("enriched_card_cache", [])
                if isinstance(item, Mapping) and item.get("card")
            ),
            recent_turns=recent_turns,
            dialogue_turns=tuple(_redact_turn(x) for x in dialogue_turns),
            last_assistant_question=data.get("last_assistant_question"),
            last_answer_kind=data.get("last_answer_kind"),
            already_asked=tuple(data.get("already_asked", [])),
            answered=tuple(data.get("answered", [])),
            contact_name=data.get("contact_name"),
            contact_phone_redacted=data.get("contact_phone_redacted"),
            contact_consent=bool(data.get("contact_consent", False)),
            callback_ref=data.get("callback_ref"),
            retry_search=RetrySearchContext.from_dict(data.get("retry_search")),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def option_names(self) -> set[str]:
        return {x.name for x in self.visible_options} | {x.name for x in self.previous_options}

    @property
    def pending_state(self) -> PendingState | None:
        return pending_state(self.pending_followup, contact_consent=self.contact_consent)

    def find_visible_option(self, exact_name: str | None) -> OptionCard | None:
        if not exact_name:
            return None
        for option in self.visible_options:
            if option.name == exact_name:
                return option
        return None


def apply_state_delta(state: ConversationState, delta: StateDelta, *, accepted: bool = True) -> ConversationState:
    """Apply an accepted transition without mutating the original state.

    The runtime calls this only after planner, deterministic transition and provider/action
    execution are accepted. Failed providers therefore cannot corrupt business state.
    """
    if not accepted:
        return state
    if delta.reset:
        state = ConversationState()

    params = dict(state.params)
    for key, value in delta.params_update.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value

    def keep_or(new_value, current, field_name: str):
        if field_name in delta.clear_fields:
            if isinstance(current, tuple):
                return ()
            if isinstance(current, bool):
                return False
            return None
        return new_value if new_value is not None else current

    recent = state.recent_turns
    if "recent_turns" in delta.clear_fields:
        recent = ()
    if delta.append_recent_turn:
        recent = (*recent, _redact_turn(delta.append_recent_turn))[-6:]

    dialogue = state.dialogue_turns
    if "dialogue_turns" in delta.clear_fields:
        dialogue = ()
    if delta.append_dialogue_turn:
        dialogue = (*dialogue, _redact_turn(delta.append_dialogue_turn))

    already_asked = state.already_asked
    if "already_asked" in delta.clear_fields:
        already_asked = ()
    already_asked = tuple(dict.fromkeys((*already_asked, *delta.already_asked_add)))
    answered = state.answered
    if "answered" in delta.clear_fields:
        answered = ()
    answered = tuple(dict.fromkeys((*answered, *delta.answered_add)))
    dialog_focus = state.dialog_focus
    if "dialog_focus" in delta.clear_fields:
        dialog_focus = DialogFocus()
    elif delta.dialog_focus is not None:
        dialog_focus = delta.dialog_focus

    return replace(
        state,
        params=params,
        pending_followup=keep_or(delta.pending_followup, state.pending_followup, "pending_followup"),
        selected_option_name=keep_or(delta.selected_option_name, state.selected_option_name, "selected_option_name"),
        visible_options=keep_or(delta.visible_options, state.visible_options, "visible_options"),
        previous_options=keep_or(delta.previous_options, state.previous_options, "previous_options"),
        last_search=keep_or(delta.last_search, state.last_search, "last_search"),
        operator_offered=keep_or(delta.operator_offered, state.operator_offered, "operator_offered"),
        operator_declined=keep_or(delta.operator_declined, state.operator_declined, "operator_declined"),
        active_topic=keep_or(delta.active_topic, state.active_topic, "active_topic"),
        dialog_focus=dialog_focus,
        selected_enriched=keep_or(delta.selected_enriched, state.selected_enriched, "selected_enriched"),
        enriched_card_cache=keep_or(delta.enriched_card_cache, state.enriched_card_cache, "enriched_card_cache"),
        recent_turns=recent,
        dialogue_turns=dialogue,
        last_assistant_question=keep_or(delta.last_assistant_question, state.last_assistant_question, "last_assistant_question"),
        last_answer_kind=keep_or(delta.last_answer_kind, state.last_answer_kind, "last_answer_kind"),
        already_asked=already_asked,
        answered=answered,
        contact_name=keep_or(delta.contact_name, state.contact_name, "contact_name"),
        contact_phone_redacted=keep_or(delta.contact_phone_redacted, state.contact_phone_redacted, "contact_phone_redacted"),
        contact_consent=keep_or(delta.contact_consent, state.contact_consent, "contact_consent"),
        callback_ref=keep_or(delta.callback_ref, state.callback_ref, "callback_ref"),
        retry_search=keep_or(delta.retry_search, state.retry_search, "retry_search"),
    )


def _redact_turn(turn: Mapping[str, Any]) -> dict[str, str]:
    text = {"user": str(turn.get("user", ""))[:500], "assistant": str(turn.get("assistant", ""))[:1000]}
    redacted: dict[str, str] = {}
    for key, value in text.items():
        value = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[redacted-contact]", value)
        value = re.sub(r"[\w.+-]+@[\w.-]+", "[redacted-email]", value)
        value = re.sub(r"(?i)(token|secret|password)\s*[:=]\s*\S+", r"\1=[redacted]", value)
        redacted[key] = value
    return redacted
