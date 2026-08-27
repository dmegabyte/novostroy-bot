"""Bounded, private, JSON-compatible dialogue state for canonical V6."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contract import CoreContractError

SCHEMA_VERSION = 2
_PENDING_OFFERS = frozenset({"none", "specialist_contact"})
_TRUNCATION_MARKER = "\n[...текст сокращён...]\n"


def _truncate(text: str, maximum: int = 2_000) -> str:
    if len(text) <= maximum:
        return text
    room = maximum - len(_TRUNCATION_MARKER)
    left = room // 2
    return text[:left] + _TRUNCATION_MARKER + text[-(room - left):]


def bound_history(items: list[Mapping[str, str]]) -> tuple[dict[str, str], ...]:
    clean: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, Mapping) or set(item) != {"role", "text"} or item.get("role") not in {"user", "assistant"}:
            raise CoreContractError("invalid_history_item")
        text = item.get("text")
        if not isinstance(text, str) or not text:
            raise CoreContractError("invalid_history_text")
        clean.append({"role": str(item["role"]), "text": _truncate(text)})
    if len(clean) % 2 or any(clean[index]["role"] != "user" or clean[index + 1]["role"] != "assistant" for index in range(0, len(clean), 2)):
        raise CoreContractError("history_requires_complete_pairs")
    clean = clean[-12:]
    while clean and sum(len(item["text"]) for item in clean) > 12_000:
        clean = clean[2:]
    return tuple(clean)


@dataclass(frozen=True)
class CoreState:
    schema_version: int = SCHEMA_VERSION
    revision: int = 0
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)
    awaiting_phone: bool = False
    client_turn_count: int = 0
    pending_offer: str = "none"

    def __post_init__(self) -> None:
        if (
            self.schema_version != SCHEMA_VERSION
            or type(self.revision) is not int
            or self.revision < 0
            or type(self.awaiting_phone) is not bool
            or type(self.client_turn_count) is not int
            or self.client_turn_count < 0
            or self.pending_offer not in _PENDING_OFFERS
        ):
            raise CoreContractError("invalid_state")
        object.__setattr__(self, "history", bound_history(list(self.history)))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CoreState":
        expected = {"schema_version", "revision", "history", "awaiting_phone", "client_turn_count", "pending_offer"}
        if not isinstance(raw, Mapping) or set(raw) != expected or not isinstance(raw.get("history"), list):
            raise CoreContractError("invalid_state_shape")
        return cls(
            schema_version=raw["schema_version"],
            revision=raw["revision"],
            history=tuple(raw["history"]),
            awaiting_phone=raw["awaiting_phone"],
            client_turn_count=max(raw["client_turn_count"], 3) if raw["pending_offer"] == "specialist_contact" else raw["client_turn_count"],
            pending_offer=raw["pending_offer"],
        )

    def plain(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "history": [dict(item) for item in self.history],
            "awaiting_phone": self.awaiting_phone,
            "client_turn_count": self.client_turn_count,
            "pending_offer": self.pending_offer,
        }
