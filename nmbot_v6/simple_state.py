"""Plain bounded JSON state for V6-simple."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .simple_contract import PHONEISH, SimpleContractError

SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
PENDING_OFFERS = frozenset({"none", "specialist_contact"})
TRUNCATION_MARKER = "\n[...текст сокращён...]\n"


def _truncate(text: str, maximum: int = 2000) -> str:
    if len(text) <= maximum:
        return text
    room = maximum - len(TRUNCATION_MARKER)
    left = room // 2
    return text[:left] + TRUNCATION_MARKER + text[-(room - left):]


def bound_history(items: list[dict[str, str]]) -> list[dict[str, str]]:
    clean = []
    for item in items:
        if not isinstance(item, Mapping) or set(item) != {"role", "text"} or item.get("role") not in {"user", "assistant"}:
            raise SimpleContractError("invalid_history_item")
        text = item.get("text")
        if not isinstance(text, str) or not text or PHONEISH.search(text):
            raise SimpleContractError("invalid_history_text")
        clean.append({"role": item["role"], "text": _truncate(text)})
    if len(clean) % 2 or any(clean[i]["role"] != "user" or clean[i + 1]["role"] != "assistant" for i in range(0, len(clean), 2)):
        raise SimpleContractError("history_requires_complete_pairs")
    clean = clean[-12:]
    while clean and sum(len(item["text"]) for item in clean) > 12_000:
        clean = clean[2:]
    return clean


@dataclass(frozen=True)
class SimpleState:
    schema_version: int = SCHEMA_VERSION
    revision: int = 0
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)
    awaiting_phone: bool = False
    client_turn_count: int = 0
    pending_offer: str = "none"

    def __post_init__(self) -> None:
        if (self.schema_version != SCHEMA_VERSION or type(self.revision) is not int or self.revision < 0
                or type(self.awaiting_phone) is not bool or type(self.client_turn_count) is not int
                or self.client_turn_count < 0 or self.pending_offer not in PENDING_OFFERS):
            raise SimpleContractError("invalid_state")
        object.__setattr__(self, "history", tuple(bound_history(list(self.history))))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SimpleState":
        if not isinstance(raw, Mapping):
            raise SimpleContractError("invalid_state_shape")
        legacy_keys = {"schema_version", "revision", "history", "awaiting_phone"}
        current_keys = legacy_keys | {"client_turn_count", "pending_offer"}
        if set(raw) not in {frozenset(legacy_keys), frozenset(current_keys)}:
            raise SimpleContractError("invalid_state_shape")
        if not isinstance(raw.get("history"), list):
            raise SimpleContractError("invalid_state_history")
        if set(raw) == legacy_keys:
            if raw["schema_version"] != LEGACY_SCHEMA_VERSION:
                raise SimpleContractError("invalid_state")
            history = bound_history(raw["history"])
            # Legacy state retained at most six complete pairs, so migration
            # deliberately derives a bounded count from the retained evidence.
            return cls(
                revision=raw["revision"], history=tuple(history), awaiting_phone=raw["awaiting_phone"],
                client_turn_count=len(history) // 2, pending_offer="none",
            )
        return cls(
            schema_version=raw["schema_version"], revision=raw["revision"], history=tuple(raw["history"]),
            awaiting_phone=raw["awaiting_phone"], client_turn_count=raw["client_turn_count"],
            pending_offer=raw["pending_offer"],
        )

    def plain(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "revision": self.revision,
            "history": [dict(item) for item in self.history], "awaiting_phone": self.awaiting_phone,
            "client_turn_count": self.client_turn_count, "pending_offer": self.pending_offer,
        }

    def accepted(self, user: str, assistant: str, *, awaiting_phone: bool, pending_offer: str = "none") -> "SimpleState":
        history = bound_history([*self.plain()["history"], {"role": "user", "text": user}, {"role": "assistant", "text": assistant}])
        return SimpleState(
            revision=self.revision + 1, history=tuple(history), awaiting_phone=awaiting_phone,
            client_turn_count=self.client_turn_count + 1, pending_offer=pending_offer,
        )

    def phone_accepted(self) -> "SimpleState":
        return SimpleState(
            revision=self.revision + 1, history=self.history, awaiting_phone=False,
            client_turn_count=self.client_turn_count + 1, pending_offer="none",
        )
