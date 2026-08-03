"""Local, injectable boundary for a closed V3 structured prose writer.

The adapter deliberately owns neither provider selection nor transport.  A
caller injects a small ``write(request)`` port, receives a JSON-safe request,
and gets either a mechanically valid V3 writer output or the fixed local
fallback.  Provider exceptions and provider text never cross this boundary.
"""
from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol, TypeAlias

from .contracts import EMAIL_RE, PHONE_RE, V3ContractError
from .presentation import (
    V3WriterBrief,
    V3WriterBriefInput,
    V3WriterOutput,
    build_v3_writer_brief,
    validate_v3_writer_output,
)
from .provider_invocation import V3AsyncTransport, V3InvocationErrorCode, V3InvocationOperation, V3TransportInvoker


_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:token|secret|password|api[_-]?key)\s*[:=]\s*\S+|"
    r"\bauthorization\s*:\s*bearer\s+\S+"
)
_FALLBACK_TEXT = "Не могу надёжно подтвердить информацию, поэтому не буду гадать."
_FALLBACK_QUESTION = "Уточните, пожалуйста, что для вас важнее всего?"


def _json_safe(value: Any) -> Any:
    """Copy a closed DTO into ordinary JSON values while redacting contacts."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = _CREDENTIAL_RE.sub("[redacted-credential]", value)
        text = EMAIL_RE.sub("[redacted-email]", text)
        return PHONE_RE.sub("[redacted-contact]", text)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    raise TypeError("writer_request_contains_non_json_value")


@dataclass(frozen=True)
class V3StructuredWriterRequest:
    """Closed request sent to an injected provider, with no provider settings."""

    brief: V3WriterBrief

    def __post_init__(self) -> None:
        if not isinstance(self.brief, V3WriterBrief):
            raise V3ContractError("invalid_structured_writer_request")

    def to_payload(self) -> Mapping[str, Any]:
        brief = self.brief
        return MappingProxyType({
            "schema_version": "v3_writer_request_v1",
            "writer_brief": _json_safe({
                "client_request": brief.client_request,
                "answer_goal": brief.answer_goal,
                "cards": [
                    {"name": card.name, "confirmed_facts": card.confirmed_facts}
                    for card in brief.cards
                ],
                "confirmed_facts": brief.confirmed_facts,
                "missing_facts": brief.missing_facts,
                "allowed_claims": brief.allowed_claims,
                "forbidden_inferences": brief.forbidden_inferences,
                "card_names_in_order": brief.card_names_in_order,
                "mandatory_cta": brief.mandatory_cta,
                "exactly_one_final_question": brief.exactly_one_final_question,
            }),
            "response_schema": dict(V3_WRITER_GATEWAY_RESPONSE_SCHEMA),
        })

    def to_json(self) -> str:
        return json.dumps(dict(self.to_payload()), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


V3_WRITER_GATEWAY_OWNER = "nmbot_v3.structured_writer"
V3_WRITER_GATEWAY_REQUEST_MARKER = "nmbot.v3.structured_writer.gateway.request.v1"
V3_WRITER_GATEWAY_RESULT_MARKER = "nmbot.v3.structured_writer.gateway.result.v1"
V3_WRITER_GATEWAY_RESULT_OUTPUT_FIELD = "output"
V3_WRITER_GATEWAY_RESPONSE_SCHEMA = MappingProxyType({
    "type": "object",
    "additionalProperties": False,
    "required": ["result_marker", V3_WRITER_GATEWAY_RESULT_OUTPUT_FIELD],
    "properties": {
        "result_marker": {"type": "string", "const": V3_WRITER_GATEWAY_RESULT_MARKER},
        V3_WRITER_GATEWAY_RESULT_OUTPUT_FIELD: {
            "type": "object",
            "additionalProperties": False,
            "required": ["intro", "cards", "recommendation", "missing_note", "final_question"],
            "properties": {
                "intro": {"type": "string"},
                "cards": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "text"],
                        "properties": {"name": {"type": "string"}, "text": {"type": "string"}},
                    },
                },
                "recommendation": {"type": "string"},
                "missing_note": {"type": "string"},
                "final_question": {"type": "string"},
            },
        },
    },
})


@dataclass(frozen=True)
class V3WriterGatewayRequest:
    """Versioned V3-owned wire request for the gateway-hosted writer."""

    writer_request: V3StructuredWriterRequest

    def __post_init__(self) -> None:
        if not isinstance(self.writer_request, V3StructuredWriterRequest):
            raise V3ContractError("invalid_v3_writer_gateway_request")

    def to_payload(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "owner": V3_WRITER_GATEWAY_OWNER,
            "request_marker": V3_WRITER_GATEWAY_REQUEST_MARKER,
            "schema_version": "v1",
            "writer_request": dict(self.writer_request.to_payload()),
            "response_schema": dict(V3_WRITER_GATEWAY_RESPONSE_SCHEMA),
        })


@dataclass(frozen=True)
class V3WriterGatewayResult:
    """Closed successful result; output stays subject to adapter validation."""

    request_marker: str
    output: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.request_marker != V3_WRITER_GATEWAY_RESULT_MARKER:
            raise V3ContractError("invalid_v3_writer_gateway_result_marker")
        if not isinstance(self.output, Mapping):
            raise V3ContractError("invalid_v3_writer_gateway_result")
        object.__setattr__(self, "output", MappingProxyType(dict(self.output)))


def build_v3_writer_gateway_request(request: V3StructuredWriterRequest) -> V3WriterGatewayRequest:
    return V3WriterGatewayRequest(request)


V3StructuredWriterRawResult: TypeAlias = str | Mapping[str, Any] | V3WriterGatewayResult


class V3StructuredWriterPort(Protocol):
    async def write(self, request: V3StructuredWriterRequest) -> V3StructuredWriterRawResult: ...


@dataclass(frozen=True)
class V3WriterAdapterResult:
    """Safe result: errors are stable codes and never include provider details."""

    ok: bool
    output: V3WriterOutput
    errors: tuple[str, ...] = ()

    @property
    def public_text(self) -> str:
        parts = [self.output.intro]
        parts.extend(f"{card.name}: {card.text}" for card in self.output.cards)
        if self.output.recommendation:
            parts.append(self.output.recommendation)
        if self.output.missing_note:
            parts.append(self.output.missing_note)
        parts.append(self.output.final_question)
        return "\n\n".join(part for part in parts if part)


class V3WriterPublicationPort(Protocol):
    """Optional post-decision publication port used by the private worker."""

    async def write(self, source: V3WriterBriefInput) -> V3WriterAdapterResult: ...


def build_v3_structured_writer_request(source: V3WriterBriefInput) -> V3StructuredWriterRequest:
    """Construct the only provider-facing request from V3 presentation data."""
    return V3StructuredWriterRequest(build_v3_writer_brief(source))


def deterministic_v3_writer_fallback() -> V3WriterOutput:
    return V3WriterOutput(intro=_FALLBACK_TEXT, final_question=_FALLBACK_QUESTION)


class V3WriterAdapter:
    """Invoke one injected writer attempt and fail closed without retry or I/O."""

    def __init__(self, writer: V3StructuredWriterPort | None = None, *, transport: V3AsyncTransport[V3StructuredWriterRequest, V3StructuredWriterRawResult] | None = None, timeout_seconds: float = 10.0) -> None:
        if (writer is None) == (transport is None):
            raise V3ContractError("invalid_structured_writer_port")
        self._writer = writer
        self._transport_invoker = None if transport is None else V3TransportInvoker(
            transport, V3InvocationOperation.WRITER, timeout_seconds=timeout_seconds,
        )

    async def write(self, source: V3WriterBriefInput) -> V3WriterAdapterResult:
        try:
            request = build_v3_structured_writer_request(source)
        except (TypeError, ValueError):
            return _fallback("invalid_writer_input")
        try:
            if self._transport_invoker is not None:
                invoked = await self._transport_invoker.invoke(request)
                if not invoked.ok:
                    return _transport_fallback(invoked.error)
                raw = invoked.payload
            else:
                assert self._writer is not None
                raw = self._writer.write(request)
                if inspect.isawaitable(raw):
                    raw = await raw
        except Exception:
            return _fallback("writer_unavailable")
        output = _parse_closed_output(raw)
        if output is None:
            return _fallback("writer_invalid_output")
        errors = validate_v3_writer_output(output, request.brief)
        if _contains_unsafe_output(output):
            errors = (*errors, "unsafe_writer_output")
        if errors:
            return _fallback("writer_invalid_output")
        return V3WriterAdapterResult(True, output)


def _parse_closed_output(raw: Any) -> V3WriterOutput | None:
    try:
        if isinstance(raw, str):
            if not raw.strip():
                return None
            raw = json.loads(raw)
        if isinstance(raw, V3WriterGatewayResult):
            output = raw.output
        elif isinstance(raw, Mapping):
            output = V3WriterGatewayResult(
                raw.get("result_marker"), raw.get(V3_WRITER_GATEWAY_RESULT_OUTPUT_FIELD),
            ).output
        else:
            return None
        return V3WriterOutput.from_dict(output)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _contains_unsafe_output(output: V3WriterOutput) -> bool:
    text = "\n".join((
        output.intro,
        *(card.name + " " + card.text for card in output.cards),
        output.recommendation,
        output.missing_note,
        output.final_question,
    ))
    return bool(PHONE_RE.search(text) or EMAIL_RE.search(text) or _CREDENTIAL_RE.search(text))


def _fallback(error: str) -> V3WriterAdapterResult:
    return V3WriterAdapterResult(False, deterministic_v3_writer_fallback(), (error,))


def _transport_fallback(error: V3InvocationErrorCode | None) -> V3WriterAdapterResult:
    if error is V3InvocationErrorCode.TIMEOUT:
        return _fallback("writer_timeout")
    if error is V3InvocationErrorCode.INVALID_RESPONSE:
        return _fallback("writer_invalid_output")
    return _fallback("writer_unavailable")
