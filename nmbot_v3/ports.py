"""Pure, V3-owned provider boundary contracts.

These types deliberately do not select, configure, or call a provider.  They
are the only data that a future V3 adapter may exchange with the semantic
planner, search, and writer owners.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Protocol, TypeAlias

from .contracts import EMAIL_RE, PHONE_RE, ExecutableTurnV3, IntentPlanV3, V3ContractError, V3PlannerContext
from .evidence_contract import EvidenceRequest, EvidenceResult


_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:token|secret|password|api[_-]?key)\s*[:=]\s*\S+|"
    r"\bauthorization\s*:\s*bearer\s+\S+"
)
_REFERENCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class V3ProviderErrorCode(str, Enum):
    INVALID_RESPONSE = "invalid_response"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class V3ProviderError:
    """A stable, non-diagnostic provider failure safe to cross the boundary."""

    code: V3ProviderErrorCode
    retryable: bool

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "code", V3ProviderErrorCode(self.code))
        except (TypeError, ValueError) as exc:
            raise V3ContractError("invalid_provider_error_code") from exc
        if not isinstance(self.retryable, bool):
            raise V3ContractError("invalid_provider_error_retryable")


@dataclass(frozen=True)
class V3RedactedText:
    """Text that is normalized and stripped of contacts and credentials."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise V3ContractError("invalid_redacted_text")
        text = _CREDENTIAL_RE.sub("[redacted-credential]", self.text)
        text = EMAIL_RE.sub("[redacted-email]", text)
        text = PHONE_RE.sub("[redacted-contact]", text)
        text = " ".join(text.split())
        if not text or len(text) > 2_000:
            raise V3ContractError("invalid_redacted_text")
        object.__setattr__(self, "text", text)


@dataclass(frozen=True)
class V3PlannerRequest:
    user_text: V3RedactedText
    context: V3PlannerContext

    def __post_init__(self) -> None:
        if not isinstance(self.user_text, V3RedactedText) or not isinstance(self.context, V3PlannerContext):
            raise V3ContractError("invalid_planner_request")


@dataclass(frozen=True)
class V3SearchRequest:
    turn: ExecutableTurnV3

    def __post_init__(self) -> None:
        if not isinstance(self.turn, ExecutableTurnV3):
            raise V3ContractError("invalid_search_request")


@dataclass(frozen=True)
class V3SearchHit:
    reference: str
    title: V3RedactedText
    summary: V3RedactedText

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not _REFERENCE_RE.fullmatch(self.reference):
            raise V3ContractError("invalid_search_reference")
        if not isinstance(self.title, V3RedactedText) or not isinstance(self.summary, V3RedactedText):
            raise V3ContractError("invalid_search_hit")


@dataclass(frozen=True)
class V3SearchResult:
    hits: tuple[V3SearchHit, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.hits, tuple) or any(not isinstance(hit, V3SearchHit) for hit in self.hits):
            raise V3ContractError("invalid_search_result")
        if len(self.hits) > 20 or len({hit.reference for hit in self.hits}) != len(self.hits):
            raise V3ContractError("invalid_search_result")


@dataclass(frozen=True)
class V3WriterRequest:
    turn: ExecutableTurnV3
    search_result: V3SearchResult

    def __post_init__(self) -> None:
        if not isinstance(self.turn, ExecutableTurnV3) or not isinstance(self.search_result, V3SearchResult):
            raise V3ContractError("invalid_writer_request")


@dataclass(frozen=True)
class V3WriterResult:
    answer: V3RedactedText

    def __post_init__(self) -> None:
        if not isinstance(self.answer, V3RedactedText):
            raise V3ContractError("invalid_writer_result")


V3PlannerPortResult: TypeAlias = IntentPlanV3 | V3ProviderError
V3SearchPortResult: TypeAlias = V3SearchResult | V3ProviderError
V3EvidenceSearchPortResult: TypeAlias = EvidenceResult | V3ProviderError
V3WriterPortResult: TypeAlias = V3WriterResult | V3ProviderError


class V3PlannerPort(Protocol):
    async def plan(self, request: V3PlannerRequest) -> V3PlannerPortResult: ...


class V3SearchPort(Protocol):
    async def search(self, request: V3SearchRequest) -> V3SearchPortResult: ...


class V3EvidenceSearchPort(Protocol):
    """V3 evidence boundary, intentionally separate from runtime search hits."""

    async def search(self, request: EvidenceRequest) -> V3EvidenceSearchPortResult: ...


class V3WriterPort(Protocol):
    async def write(self, request: V3WriterRequest) -> V3WriterPortResult: ...
