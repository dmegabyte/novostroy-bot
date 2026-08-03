"""V2-local injected adapter for the typed search-contract boundary.

The module is intentionally transport-free. A composition root supplies an
async invoker; this adapter only builds a safe V2 query, parses a strict result,
and returns a typed result or a bounded error code. It neither imports the
global runtime adapter nor selects a gateway, MCP client, or network library.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping

from .card_normalizer import normalize_search_result
from .contracts import SearchResult
from .search_contract import V2SearchRequest, build_query, normalize_and_validate_search_output, parse_strict_json


class V2SearchAdapterErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class V2SearchAdapterError:
    """Safe public failure shape; provider text and payloads are never retained."""

    code: V2SearchAdapterErrorCode
    retryable: bool


@dataclass(frozen=True)
class V2SearchAdapterResult:
    """Typed V2 result with an explicit, non-diagnostic failure alternative."""

    result: SearchResult | None = None
    error: V2SearchAdapterError | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and self.error is None


@dataclass(frozen=True)
class V2SearchProviderRequest:
    """Safe request handed to an injected provider implementation.

    ``payload`` is a defensive copy of the typed request's serializable data;
    it intentionally contains neither credentials nor provider configuration.
    """

    request: V2SearchRequest
    query: str
    payload: Mapping[str, Any]


V2SearchInvoker = Callable[[V2SearchProviderRequest], Awaitable[Mapping[str, Any] | str]]


def build_v2_search_provider_request(request: V2SearchRequest) -> V2SearchProviderRequest:
    """Build the provider-neutral request from an already typed V2 query."""
    if not isinstance(request, V2SearchRequest):
        raise ValueError("invalid_v2_search_request")
    return V2SearchProviderRequest(
        request=request,
        query=build_query(request),
        payload=deepcopy(request.to_payload()),
    )


class V2InjectedSearchAdapter:
    """Convert an injected async invocation into a typed V2 search result."""

    def __init__(self, invoke: V2SearchInvoker) -> None:
        if not callable(invoke):
            raise ValueError("invalid_v2_search_invoker")
        self._invoke = invoke

    async def search(self, request: V2SearchRequest) -> V2SearchAdapterResult:
        try:
            provider_request = build_v2_search_provider_request(request)
        except ValueError:
            return _error(V2SearchAdapterErrorCode.INVALID_REQUEST, retryable=False)

        try:
            raw = await self._invoke(provider_request)
        except asyncio.TimeoutError:
            return _error(V2SearchAdapterErrorCode.TIMEOUT, retryable=True)
        except Exception:
            return _error(V2SearchAdapterErrorCode.UNAVAILABLE, retryable=True)

        output = _parse_provider_output(raw)
        if output is None:
            return _error(V2SearchAdapterErrorCode.INVALID_RESPONSE, retryable=False)
        try:
            normalized, validation = normalize_and_validate_search_output(output, request)
            if not validation.get("ok"):
                return _error(V2SearchAdapterErrorCode.INVALID_RESPONSE, retryable=False)
            return V2SearchAdapterResult(result=normalize_search_result(normalized))
        except Exception:
            # A malformed provider shape must not expose exception text, output,
            # prompt content, or request data through the V2 worker boundary.
            return _error(V2SearchAdapterErrorCode.INVALID_RESPONSE, retryable=False)


def build_injected_v2_search_adapter(invoke: V2SearchInvoker) -> V2InjectedSearchAdapter:
    """Factory used by a future V2 composition root to inject transport."""
    return V2InjectedSearchAdapter(invoke)


def _parse_provider_output(raw: Mapping[str, Any] | str) -> dict[str, Any] | None:
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str):
        return None
    parsed, errors = parse_strict_json(raw)
    return parsed if parsed is not None and not errors else None


def _error(code: V2SearchAdapterErrorCode, *, retryable: bool) -> V2SearchAdapterResult:
    return V2SearchAdapterResult(error=V2SearchAdapterError(code=code, retryable=retryable))
