"""Small explicit contracts at the canonical V6 core boundary.

Network transport and model parsing stay outside this skeleton.  These types make
the permitted V6 states visible before the runtime is added.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class CoreContractError(ValueError):
    """A bounded value crossed a V6 owner boundary in an invalid shape."""

    def __init__(self, code: str, *, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


class Prompt1Action(StrEnum):
    CONTINUE = "continue"
    CLARIFY = "clarify"
    REQUEST_PHONE = "request_phone"


class Prompt2Action(StrEnum):
    REPLY = "reply"
    REQUEST_PHONE = "request_phone"


@dataclass(frozen=True)
class TurnInput:
    """One already-authenticated client message for the runtime."""

    message: str
    session_ref: str
    channel: str = "jivo"

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or not self.message.strip() or len(self.message) > 8_000:
            raise CoreContractError("invalid_message")
        if not isinstance(self.session_ref, str) or not self.session_ref or len(self.session_ref) > 160:
            raise CoreContractError("invalid_session_ref")
        if self.channel not in {"jivo", "api"}:
            raise CoreContractError("invalid_channel")


@dataclass(frozen=True)
class Prompt1Document:
    action: Prompt1Action
    facts: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    near: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    missing: tuple[Any, ...] = field(default_factory=tuple)
    params: dict[str, Any] = field(default_factory=dict)
    ambiguity: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, Prompt1Action):
            raise CoreContractError("invalid_prompt1_action", field="action")
        if not all(isinstance(item, dict) for item in (*self.facts, *self.near)):
            raise CoreContractError("invalid_prompt1_material")
        if not isinstance(self.params, dict):
            raise CoreContractError("invalid_prompt1_params")
        if self.action is Prompt1Action.CLARIFY and self.ambiguity is None:
            raise CoreContractError("clarify_requires_ambiguity")
        if self.ambiguity is not None and (
            not isinstance(self.ambiguity, Mapping)
            or set(self.ambiguity) != {"parameter", "reason_code"}
            or not all(isinstance(value, str) and value for value in self.ambiguity.values())
        ):
            raise CoreContractError("invalid_ambiguity")


@dataclass(frozen=True)
class Prompt2Document:
    action: Prompt2Action
    response: str
    final_question: str

    def __post_init__(self) -> None:
        if not isinstance(self.action, Prompt2Action):
            raise CoreContractError("invalid_prompt2_action", field="action")
        if not isinstance(self.response, str) or not self.response.strip() or len(self.response) > 8_000:
            raise CoreContractError("invalid_prompt2_response")
        if not isinstance(self.final_question, str) or len(self.final_question) > 1_000:
            raise CoreContractError("invalid_prompt2_final_question")


@dataclass(frozen=True)
class TerminalResponse:
    """The only runtime-owned terminal intent before the API renders Jivo JSON."""

    text: str
    event: str = "BOT_MESSAGE"
    request_phone: bool = False
    handoff_to_operator: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip() or len(self.text) > 8_000:
            raise CoreContractError("invalid_terminal_text")
        if self.event not in {"BOT_MESSAGE", "INVITE_AGENT"}:
            raise CoreContractError("invalid_terminal_event")
        if self.event == "INVITE_AGENT" and not self.handoff_to_operator:
            raise CoreContractError("invite_requires_handoff")
        if self.request_phone and self.event != "BOT_MESSAGE":
            raise CoreContractError("phone_request_requires_bot_message")
