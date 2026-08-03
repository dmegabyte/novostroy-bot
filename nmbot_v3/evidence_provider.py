"""V3-owned evidence gateway adapter with an injected async invocation seam.

The adapter has no gateway, MCP, HTTP, or provider dependency.  A composition
root may supply one later.  Until then, this module is independently testable:
it serializes only the closed :class:`EvidenceRequest`, parses one strict JSON
object, and converts every malformed or failed provider interaction into a
non-diagnostic ``V3ProviderError``.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping

from .contracts import V3ContractError
from .evidence_contract import EvidenceRequest, EvidenceResult, normalize_evidence_result, validate_evidence_result
from .ports import V3EvidenceSearchPortResult, V3ProviderError, V3ProviderErrorCode
from .provider_invocation import V3AsyncTransport, V3InvocationErrorCode, V3InvocationOperation, V3TransportInvoker


V3_EVIDENCE_PROMPT = """
Ты — V3 evidence search executor для консультанта по новостройкам.
Исполни только переданный evidence request. Не отвечай клиенту, не меняй state,
не выбирай provider и не добавляй diagnostics.

Верни ровно один JSON object без markdown и свободного текста. В нём только:
facts, near, missing_facts. Каждый объект facts/near содержит только name,
canonical_ref, fields, is_near, differences. canonical_ref — UUID стабильной
идентичности объекта либо null. facts содержит только точные подтверждённые
объекты; near — только близкие альтернативы с непустыми differences. Соблюдай
count, excluded_names, exact_name и порядок current_option_refs из request.
""".strip()

_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ("facts", "near", "missing_facts"),
    "properties": {
        "facts": {"type": "array", "maxItems": 3},
        "near": {"type": "array", "maxItems": 3},
        "missing_facts": {"type": "array", "maxItems": 200, "uniqueItems": True},
    },
}
_RESPONSE_KEYS = frozenset(_RESPONSE_SCHEMA["required"])


@dataclass(frozen=True)
class V3EvidenceProviderRequest:
    """Safe, provider-agnostic request built only from a V3 evidence DTO."""

    prompt: str
    payload: Mapping[str, Any]
    response_schema: Mapping[str, Any]


V3EvidenceInvoker = Callable[[V3EvidenceProviderRequest], Awaitable[Mapping[str, Any] | str]]


def v3_evidence_response_schema() -> dict[str, Any]:
    """Return a mutation-safe response schema for an injected provider."""
    return deepcopy(_RESPONSE_SCHEMA)


def build_v3_evidence_provider_request(request: EvidenceRequest) -> V3EvidenceProviderRequest:
    """Build a provider request from the closed V3 evidence request only."""
    if not isinstance(request, EvidenceRequest):
        raise V3ContractError("invalid_evidence_request")
    payload = {
        "mode": request.mode.value,
        "requested_facts": tuple(request.requested_facts),
        "hard_constraints": _thaw(request.hard_constraints),
        "exact_name": request.exact_name,
        "current_option_refs": tuple(request.current_option_refs),
        "excluded_names": tuple(request.excluded_names),
        "count": request.count,
    }
    return V3EvidenceProviderRequest(V3_EVIDENCE_PROMPT, payload, v3_evidence_response_schema())


class V3InjectedEvidenceSearchAdapter:
    """Adapt a fakeable async gateway invoker to ``V3EvidenceSearchPort``."""

    def __init__(self, invoke: V3EvidenceInvoker | None = None, *, transport: V3AsyncTransport[V3EvidenceProviderRequest, Mapping[str, Any] | str] | None = None, timeout_seconds: float = 10.0) -> None:
        if (invoke is None) == (transport is None):
            raise V3ContractError("invalid_v3_evidence_invoker")
        self._invoke = invoke
        self._transport_invoker = None if transport is None else V3TransportInvoker(
            transport, V3InvocationOperation.EVIDENCE, timeout_seconds=timeout_seconds,
        )

    async def search(self, request: EvidenceRequest) -> V3EvidenceSearchPortResult:
        try:
            provider_request = build_v3_evidence_provider_request(request)
            if self._transport_invoker is not None:
                invoked = await self._transport_invoker.invoke(provider_request)
                if not invoked.ok:
                    return _transport_error(invoked.error)
                raw = invoked.payload
            else:
                assert self._invoke is not None
                raw = await self._invoke(provider_request)
        except asyncio.TimeoutError:
            return V3ProviderError(V3ProviderErrorCode.TIMEOUT, retryable=True)
        except V3ContractError:
            return V3ProviderError(V3ProviderErrorCode.INVALID_RESPONSE, retryable=False)
        except Exception:
            return V3ProviderError(V3ProviderErrorCode.UNAVAILABLE, retryable=True)

        result_input = _parse_provider_evidence(raw)
        if result_input is None:
            return V3ProviderError(V3ProviderErrorCode.INVALID_RESPONSE, retryable=False)
        try:
            normalized = normalize_evidence_result(request, result_input)
        except V3ContractError:
            return V3ProviderError(V3ProviderErrorCode.INVALID_RESPONSE, retryable=False)
        validation = validate_evidence_result(request, normalized)
        if not validation.ok or validation.result is None:
            return V3ProviderError(V3ProviderErrorCode.INVALID_RESPONSE, retryable=False)
        return validation.result


def _parse_provider_evidence(raw: Mapping[str, Any] | str) -> Mapping[str, Any] | None:
    """Accept exactly one JSON object and never retain raw provider diagnostics."""
    if isinstance(raw, Mapping):
        parsed = dict(raw)
        return parsed if set(parsed) == _RESPONSE_KEYS else None
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, Mapping):
        return None
    result = dict(parsed)
    return result if set(result) == _RESPONSE_KEYS else None


def _thaw(value: Any) -> Any:
    """Copy frozen V3 contract values without leaking mutable contract internals."""
    if isinstance(value, MappingProxyType):
        value = dict(value)
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_thaw(item) for item in value)
    return value


def _transport_error(error: V3InvocationErrorCode | None) -> V3ProviderError:
    if error is V3InvocationErrorCode.TIMEOUT:
        return V3ProviderError(V3ProviderErrorCode.TIMEOUT, retryable=True)
    if error is V3InvocationErrorCode.INVALID_RESPONSE:
        return V3ProviderError(V3ProviderErrorCode.INVALID_RESPONSE, retryable=False)
    return V3ProviderError(V3ProviderErrorCode.UNAVAILABLE, retryable=True)
