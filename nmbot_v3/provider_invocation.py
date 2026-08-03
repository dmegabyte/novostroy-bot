"""V3-local typed transport seam for already-closed provider requests.

This module deliberately contains no provider choice, configuration, client, or
network code.  Adapters supply their own closed request DTO as ``payload``;
the invoker adds an opaque request identity and converts transport failures into
small, redacted result codes.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Protocol, TypeVar
from uuid import UUID, uuid4

from .contracts import V3ContractError


RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")


class V3InvocationOperation(str, Enum):
    PLANNER = "planner"
    EVIDENCE = "evidence"
    WRITER = "writer"


class V3InvocationErrorCode(str, Enum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True)
class V3TransportRequest(Generic[RequestT]):
    """One transport request with a generated, opaque, V3-owned identity."""

    request_id: str
    operation: V3InvocationOperation
    payload: RequestT

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str):
            raise V3ContractError("invalid_v3_transport_request_id")
        try:
            UUID(self.request_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise V3ContractError("invalid_v3_transport_request_id") from exc
        try:
            object.__setattr__(self, "operation", V3InvocationOperation(self.operation))
        except (TypeError, ValueError) as exc:
            raise V3ContractError("invalid_v3_transport_operation") from exc
        if self.payload is None:
            raise V3ContractError("invalid_v3_transport_payload")


@dataclass(frozen=True)
class V3TransportResponse(Generic[ResponseT]):
    """A transport response must echo the exact request identity."""

    request_id: str
    payload: ResponseT

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id:
            raise V3ContractError("invalid_v3_transport_response_id")


class V3AsyncTransport(Protocol[RequestT, ResponseT]):
    """The sole async transport protocol; implementations stay outside V3."""

    async def invoke(self, request: V3TransportRequest[RequestT]) -> V3TransportResponse[ResponseT]: ...


@dataclass(frozen=True)
class V3InvocationResult(Generic[ResponseT]):
    """Redacted transport outcome: never carries an exception or diagnostics."""

    payload: ResponseT | None = None
    error: V3InvocationErrorCode | None = None

    def __post_init__(self) -> None:
        if (self.payload is None) == (self.error is None):
            raise V3ContractError("invalid_v3_invocation_result")
        if self.error is not None:
            try:
                object.__setattr__(self, "error", V3InvocationErrorCode(self.error))
            except (TypeError, ValueError) as exc:
                raise V3ContractError("invalid_v3_invocation_error") from exc

    @property
    def ok(self) -> bool:
        return self.error is None


class V3TransportInvoker(Generic[RequestT, ResponseT]):
    """Invoke once with an identity check, finite timeout, and redacted errors."""

    def __init__(
        self,
        transport: V3AsyncTransport[RequestT, ResponseT],
        operation: V3InvocationOperation,
        *,
        timeout_seconds: float,
    ) -> None:
        if not callable(getattr(transport, "invoke", None)):
            raise V3ContractError("invalid_v3_async_transport")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 120:
            raise V3ContractError("invalid_v3_transport_timeout")
        try:
            normalized_operation = V3InvocationOperation(operation)
        except (TypeError, ValueError) as exc:
            raise V3ContractError("invalid_v3_transport_operation") from exc
        self._transport = transport
        self._operation = normalized_operation
        self._timeout_seconds = float(timeout_seconds)

    async def invoke(self, payload: RequestT) -> V3InvocationResult[ResponseT]:
        request = V3TransportRequest(str(uuid4()), self._operation, payload)
        try:
            response = await asyncio.wait_for(self._transport.invoke(request), timeout=self._timeout_seconds)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return V3InvocationResult(error=V3InvocationErrorCode.TIMEOUT)
        except Exception:
            return V3InvocationResult(error=V3InvocationErrorCode.UNAVAILABLE)
        if (
            not isinstance(response, V3TransportResponse)
            or response.request_id != request.request_id
            or response.payload is None
        ):
            return V3InvocationResult(error=V3InvocationErrorCode.INVALID_RESPONSE)
        return V3InvocationResult(payload=response.payload)
