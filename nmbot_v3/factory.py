"""V3-owned construction of the three provider adapters.

The caller owns provider selection, configuration, credentials, and network
I/O.  This factory accepts only an already-created V3 async transport and one
bounded timeout, then gives every adapter that same transport instance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import V3ContractError
from .evidence_provider import V3InjectedEvidenceSearchAdapter
from .planner_provider import V3InjectedPlannerAdapter
from .provider_invocation import V3AsyncTransport
from .writer_adapter import V3WriterAdapter


@dataclass(frozen=True)
class V3AdapterFactoryResult:
    """Coherent V3 adapters built from one injected transport instance."""

    transport: V3AsyncTransport[Any, Any]
    timeout_seconds: float
    planner: V3InjectedPlannerAdapter
    evidence: V3InjectedEvidenceSearchAdapter
    writer: V3WriterAdapter

    def __post_init__(self) -> None:
        if not callable(getattr(self.transport, "invoke", None)):
            raise V3ContractError("invalid_v3_async_transport")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 120
        ):
            raise V3ContractError("invalid_v3_transport_timeout")
        if not isinstance(self.planner, V3InjectedPlannerAdapter):
            raise V3ContractError("invalid_v3_factory_planner")
        if not isinstance(self.evidence, V3InjectedEvidenceSearchAdapter):
            raise V3ContractError("invalid_v3_factory_evidence")
        if not isinstance(self.writer, V3WriterAdapter):
            raise V3ContractError("invalid_v3_factory_writer")
        for adapter in (self.planner, self.evidence, self.writer):
            invoker = getattr(adapter, "_transport_invoker", None)
            if (
                invoker is None
                or getattr(invoker, "_transport", None) is not self.transport
                or getattr(invoker, "_timeout_seconds", None) != float(self.timeout_seconds)
            ):
                raise V3ContractError("incoherent_v3_adapter_factory_result")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


def build_v3_adapter_factory(
    transport: V3AsyncTransport[Any, Any],
    *,
    timeout_seconds: float,
) -> V3AdapterFactoryResult:
    """Build V3 planner, evidence, and writer adapters without any I/O.

    Validation deliberately mirrors ``V3TransportInvoker`` so bad dependencies
    fail at the composition boundary, before a partial adapter set is returned.
    """
    if not callable(getattr(transport, "invoke", None)):
        raise V3ContractError("invalid_v3_async_transport")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 < timeout_seconds <= 120
    ):
        raise V3ContractError("invalid_v3_transport_timeout")
    normalized_timeout = float(timeout_seconds)
    return V3AdapterFactoryResult(
        transport=transport,
        timeout_seconds=normalized_timeout,
        planner=V3InjectedPlannerAdapter(transport=transport, timeout_seconds=normalized_timeout),
        evidence=V3InjectedEvidenceSearchAdapter(transport=transport, timeout_seconds=normalized_timeout),
        writer=V3WriterAdapter(transport=transport, timeout_seconds=normalized_timeout),
    )
