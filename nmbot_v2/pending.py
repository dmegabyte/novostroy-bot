from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .contracts import StateDelta, TurnAction


class PendingKind(str, Enum):
    OPERATOR_CONSENT = "operator_consent"
    CONTACT_NAME = "contact_name"
    CONTACT_PHONE = "contact_phone"
    FINANCING_CONSENT = "financing_consent"
    SELECTED_LIVE_FACT_CONSENT = "selected_live_fact_consent"
    OTHER = "other"


@dataclass(frozen=True)
class PendingState:
    """Typed view over the legacy serialized ``pending_followup`` string.

    The stored field remains a plain string for backward compatibility, but
    runtime decisions must compare this typed view instead of guessing from the
    raw value alone.  Old states that used ``contact_name`` before explicit
    consent are read as operator-consent pending, not as active name capture.
    """

    kind: PendingKind
    key: str | None = None

    @property
    def is_contact_capture(self) -> bool:
        return self.kind in {PendingKind.CONTACT_NAME, PendingKind.CONTACT_PHONE}

    @property
    def is_operator_consent(self) -> bool:
        return self.kind in {PendingKind.OPERATOR_CONSENT, PendingKind.FINANCING_CONSENT, PendingKind.SELECTED_LIVE_FACT_CONSENT}


def normalize_pending_key(value: Any) -> str | None:
    if value in (None, "", {}, []):
        return None
    if isinstance(value, dict):
        raw = value.get("type") or value.get("id") or value.get("pending")
    else:
        raw = value
    key = str(raw or "").strip()
    aliases = {
        "phone_capture": "contact_phone",
        "awaiting_contact_phone": "contact_phone",
        "awaiting_contact_name": "contact_name",
    }
    return aliases.get(key, key) or None


def pending_state(value: Any, *, contact_consent: bool = False) -> PendingState | None:
    key = normalize_pending_key(value)
    if not key:
        return None
    if key == "contact_name":
        return PendingState(PendingKind.CONTACT_NAME if contact_consent else PendingKind.OPERATOR_CONSENT, key)
    if key == "contact_phone":
        return PendingState(PendingKind.CONTACT_PHONE, key)
    if key in {"operator_consent", "operator_offer"}:
        return PendingState(PendingKind.OPERATOR_CONSENT, key)
    if key == "financing_consent":
        return PendingState(PendingKind.FINANCING_CONSENT, key)
    if key == "selected_live_fact_consent":
        return PendingState(PendingKind.SELECTED_LIVE_FACT_CONSENT, key)
    return PendingState(PendingKind.OTHER, key)


def pending_delta_for_action(action: TurnAction, state: Any, *, default_pending: str | None = None) -> StateDelta:
    """Single owner for action -> pending-state mutations in this slice."""

    if action == TurnAction.OFFER_OPERATOR:
        if getattr(state, "operator_declined", False):
            return StateDelta(operator_offered=False)
        return StateDelta(operator_offered=True, pending_followup="contact_name")
    if action == TurnAction.ACCEPT_OPERATOR:
        return StateDelta(operator_offered=True, pending_followup="contact_phone", contact_consent=True)
    if action == TurnAction.DECLINE_OPERATOR:
        return StateDelta(operator_declined=True, operator_offered=False, clear_fields=("pending_followup",))
    return StateDelta(pending_followup=default_pending)


def is_pending_contact_name(value: Any, *, contact_consent: bool = False) -> bool:
    state = pending_state(value, contact_consent=contact_consent)
    return bool(state and state.kind == PendingKind.CONTACT_NAME)


def is_pending_contact_phone(value: Any, *, contact_consent: bool = False) -> bool:
    state = pending_state(value, contact_consent=contact_consent)
    return bool(state and state.kind == PendingKind.CONTACT_PHONE)
