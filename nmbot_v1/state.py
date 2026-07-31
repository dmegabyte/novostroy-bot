from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from typing import Any, Mapping

from .contracts import SCHEMA_VERSION, V1Action, V1Error, V1Stage, _reject_unknown, deep_freeze, deep_thaw
from .search_contract import project_public_card


PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
SAFE_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\- ]{1,59}$")


def redact_phone(text: str | None) -> str | None:
    if text is None:
        return None
    def repl(m):
        digits = re.sub(r"\D", "", m.group(0))
        return "***" + digits[-4:] if len(digits) >= 4 else "***"
    return PHONE_RE.sub(repl, text)


def normalize_contact_name(text: str | None) -> str | None:
    if not isinstance(text, str):
        return None
    name = " ".join(text.strip().split())
    if not SAFE_NAME_RE.fullmatch(name):
        return None
    return name


def redact_contact_phone(text: str | None) -> str | None:
    if not isinstance(text, str) or not PHONE_RE.fullmatch(text.strip()):
        return None
    return redact_phone(text.strip())


@dataclass(frozen=True)
class V1ConversationState:
    schema_version: int = SCHEMA_VERSION
    revision: int = 0
    stage: V1Stage = V1Stage.RESET
    hard_constraints: Mapping[str, Any] = field(default_factory=dict)
    preferences: Mapping[str, Any] = field(default_factory=dict)
    active_viewpoint: str = "buyer"
    visible_options: tuple[Mapping[str, Any], ...] = ()
    previous_option_refs: tuple[str, ...] = ()
    selected_project: Mapping[str, Any] | None = None
    selected_lot: Mapping[str, Any] | None = None
    last_search_summary: Mapping[str, Any] | None = None
    pending_action: V1Action | None = None
    already_asked: tuple[str, ...] = ()
    answered_facts: Mapping[str, Any] = field(default_factory=dict)
    operator_offered: bool = False
    operator_declined: bool = False
    contact_consent: bool = False
    contact_name: str | None = None
    contact_phone_redacted: str | None = None
    callback_ref: str | None = None
    recent_safe_turns: tuple[str, ...] = ()

    def __post_init__(self):
        if self.schema_version != SCHEMA_VERSION:
            raise V1Error("schema_version must be 1")
        object.__setattr__(self, "stage", V1Stage.coerce(self.stage))
        pa = self.pending_action
        object.__setattr__(self, "pending_action", None if pa is None else V1Action.coerce(pa))
        object.__setattr__(self, "hard_constraints", deep_freeze(self.hard_constraints or {}))
        object.__setattr__(self, "preferences", deep_freeze(self.preferences or {}))
        object.__setattr__(self, "visible_options", tuple(deep_freeze(_state_safe_card(v)) for v in (self.visible_options or ())))
        object.__setattr__(self, "previous_option_refs", tuple(self.previous_option_refs or ()))
        object.__setattr__(self, "selected_project", None if self.selected_project is None else deep_freeze(_state_safe_card(self.selected_project)))
        object.__setattr__(self, "selected_lot", None if self.selected_lot is None else deep_freeze(_state_safe_card(self.selected_lot)))
        object.__setattr__(self, "last_search_summary", None if self.last_search_summary is None else deep_freeze(self.last_search_summary))
        object.__setattr__(self, "already_asked", tuple(self.already_asked or ()))
        object.__setattr__(self, "answered_facts", deep_freeze(self.answered_facts or {}))
        object.__setattr__(self, "contact_phone_redacted", redact_phone(self.contact_phone_redacted))
        object.__setattr__(self, "recent_safe_turns", tuple(redact_phone(t) or "" for t in (self.recent_safe_turns or ())))

    @classmethod
    def clean(cls, revision: int = 0) -> "V1ConversationState":
        return cls(revision=revision)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "revision": self.revision, "stage": self.stage.value,
            "hard_constraints": deep_thaw(self.hard_constraints), "preferences": deep_thaw(self.preferences), "active_viewpoint": self.active_viewpoint,
            "visible_options": [deep_thaw(v) for v in self.visible_options], "previous_option_refs": list(self.previous_option_refs),
            "selected_project": None if self.selected_project is None else deep_thaw(self.selected_project), "selected_lot": None if self.selected_lot is None else deep_thaw(self.selected_lot),
            "last_search_summary": None if self.last_search_summary is None else deep_thaw(self.last_search_summary), "pending_action": None if self.pending_action is None else self.pending_action.value,
            "already_asked": list(self.already_asked), "answered_facts": deep_thaw(self.answered_facts), "operator_offered": self.operator_offered,
            "operator_declined": self.operator_declined, "contact_consent": self.contact_consent, "contact_name": self.contact_name,
            "contact_phone_redacted": self.contact_phone_redacted, "callback_ref": self.callback_ref, "recent_safe_turns": list(self.recent_safe_turns),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "V1ConversationState":
        _reject_unknown(data, {f.name for f in fields(cls)})
        return cls(**dict(data))


def _state_safe_card(card: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(card, Mapping):
        raise V1Error("state card must be object")
    if "evidence" in card:
        return project_public_card(card)
    projected = {"ref": card.get("ref"), "name": card.get("name"), "evidence": card.get("facts", {})}
    safe = project_public_card(projected)
    return safe
