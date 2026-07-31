from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nmbot_v2.contracts import OptionCard


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class V0State:
    """Minimal local V0 dialogue state.

    Only client-safe normalized cards are stored in ``visible_options``.
    """

    params: JsonDict = field(default_factory=dict)
    visible_options: tuple[OptionCard, ...] = ()
    selected_option_name: str | None = None
    active_topic: str | None = None
    has_greeted: bool = False
    last_answer_kind: str | None = None
    last_assistant_question: str | None = None
    previous_assistant_message: str | None = None
    answered_facts: tuple[str, ...] = ()
    pending_action: str | None = None
    pending_subject: str | None = None
    pending_topic: str | None = None


@dataclass(frozen=True)
class V0Answer:
    answer_kind: str
    scope: str
    intro: str
    options: tuple[JsonDict, ...] = ()
    recommendation: str = ""
    missing_note: str = ""
    final_question: str = ""

    def text(self) -> str:
        chunks = [self.intro]
        for option in self.options:
            lines = [str(line).rstrip() for line in option.get("lines", ()) if str(line).strip()]
            chunks.extend(lines)
        chunks.extend(part for part in (self.recommendation, self.missing_note, self.final_question) if part)
        return "\n".join(part for part in chunks if part)


@dataclass(frozen=True)
class V0TurnResult:
    ok: bool
    state: V0State
    answer: V0Answer | None = None
    message: str = ""
    error_code: str | None = None
    diagnostics: JsonDict = field(default_factory=dict)
