"""V6-owned boundary between MCP transport traces and trusted evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import ContractError
from .privacy import immutable_safe_copy

_OPAQUE_REF = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")
_FORBIDDEN_PROVENANCE_KEY = re.compile(r"model|assistant|prompt|metadata|mcp[_-]?servers", re.I)
_TRACE_TOKEN = object()
TRUSTED_MCP_SERVER = "novostroym"
TRUSTED_MCP_TOOL = "get_flat_info"


@dataclass(frozen=True, init=False, slots=True)
class TransportToolTrace:
    """Typed result created by a transport after an actual tool call."""

    task_ref: str
    actual_server: str
    actual_tool: str
    call_count: int
    safe_facts: Mapping[str, Any]
    effective_constraints: Mapping[str, Any]
    visible_refs: tuple[str, ...]
    provenance: str

    def __init__(
        self,
        task_ref: str,
        actual_server: str,
        actual_tool: str,
        call_count: int,
        safe_facts: Mapping[str, Any],
        effective_constraints: Mapping[str, Any],
        visible_refs: tuple[str, ...] | list[str],
        provenance: str = "transport_trace",
        *,
        _token: object,
    ) -> None:
        if _token is not _TRACE_TOKEN:
            raise ContractError("transport results may only be created at the transport boundary")
        if not isinstance(safe_facts, Mapping) or not isinstance(effective_constraints, Mapping):
            raise ContractError("transport facts and constraints must be mappings")
        if type(visible_refs) not in (tuple, list):
            raise ContractError("visible refs must be a list or tuple")
        object.__setattr__(self, "task_ref", task_ref)
        object.__setattr__(self, "actual_server", actual_server)
        object.__setattr__(self, "actual_tool", actual_tool)
        object.__setattr__(self, "call_count", call_count)
        object.__setattr__(self, "safe_facts", immutable_safe_copy(safe_facts))
        object.__setattr__(self, "effective_constraints", immutable_safe_copy(effective_constraints))
        object.__setattr__(self, "visible_refs", tuple(visible_refs))
        object.__setattr__(self, "provenance", provenance)
        _validate_trace_fields(self)

@dataclass(frozen=True)
class TrustedMcpEnvelope:
    task_ref: str | None
    actual_server: str | None
    actual_tool: str | None
    call_count: int
    safe_facts: Mapping[str, Any] = field(default_factory=dict)
    effective_constraints: Mapping[str, Any] = field(default_factory=dict)
    visible_refs: tuple[str, ...] = ()
    evidence_source: str = "transport_trace"

    def __post_init__(self) -> None:
        object.__setattr__(self, "safe_facts", immutable_safe_copy(self.safe_facts))
        object.__setattr__(self, "effective_constraints", immutable_safe_copy(self.effective_constraints))
        object.__setattr__(self, "visible_refs", tuple(self.visible_refs))


def build_trusted_envelope(
    *,
    search_required: bool,
    requested_tool: str | None,
    trace: TransportToolTrace | None,
) -> TrustedMcpEnvelope:
    """Promote only a typed, actual transport trace to trusted evidence."""

    if type(search_required) is not bool:
        raise ContractError("search_required must be boolean")
    if trace is None:
        if search_required:
            raise ContractError("search requires an actual tool trace")
        envelope = TrustedMcpEnvelope(None, None, None, 0)
    else:
        if type(trace) is not TransportToolTrace:
            raise ContractError("untrusted or model-authored evidence is forbidden")
        _validate_trace_fields(trace)
        if trace.call_count < 1:
            raise ContractError("an actual tool trace must contain a call")
        if (
            not requested_tool
            or trace.actual_server != TRUSTED_MCP_SERVER
            or trace.actual_tool != requested_tool
            or trace.actual_tool != TRUSTED_MCP_TOOL
        ):
            raise ContractError("actual server and tool do not match the trusted transport target")
        envelope = TrustedMcpEnvelope(
            trace.task_ref,
            trace.actual_server,
            trace.actual_tool,
            trace.call_count,
            trace.safe_facts,
            trace.effective_constraints,
            trace.visible_refs,
            trace.provenance,
        )
    from .validation import validate_trusted_envelope

    validate_trusted_envelope(envelope, search_required=search_required, requested_tool=requested_tool)
    return envelope


def trusted_envelope_projection(envelope: TrustedMcpEnvelope) -> Mapping[str, Any]:
    """Return the exact safe allowlist accepted by the Prompt 2 gateway."""

    if type(envelope) is not TrustedMcpEnvelope:
        raise ContractError("trusted MCP projection requires a validated envelope")
    return MappingProxyType({
        "task_ref": envelope.task_ref,
        "actual_server": envelope.actual_server,
        "actual_tool": envelope.actual_tool,
        "call_count": envelope.call_count,
        "safe_facts": envelope.safe_facts,
        "effective_constraints": envelope.effective_constraints,
        "visible_refs": envelope.visible_refs,
        "evidence_source": envelope.evidence_source,
    })


def _validate_trace_fields(trace: TransportToolTrace) -> None:
    if not isinstance(trace.task_ref, str) or not _OPAQUE_REF.fullmatch(trace.task_ref):
        raise ContractError("task_ref must be an opaque reference")
    if not isinstance(trace.actual_server, str) or not trace.actual_server:
        raise ContractError("actual_server must be non-empty")
    if not isinstance(trace.actual_tool, str) or not trace.actual_tool:
        raise ContractError("actual_tool must be non-empty")
    if isinstance(trace.call_count, bool) or not isinstance(trace.call_count, int):
        raise ContractError("call_count must be an integer")
    if _contains_model_evidence_key(trace.safe_facts) or _contains_model_evidence_key(
        trace.effective_constraints
    ):
        raise ContractError("model-authored transport fields are forbidden")
    if any(not isinstance(ref, str) or not _OPAQUE_REF.fullmatch(ref) for ref in trace.visible_refs):
        raise ContractError("visible refs must be opaque references")


def _contains_model_evidence_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            not isinstance(key, str)
            or _FORBIDDEN_PROVENANCE_KEY.search(key)
            or _contains_model_evidence_key(item)
            for key, item in value.items()
        )
    if isinstance(value, tuple):
        return any(_contains_model_evidence_key(item) for item in value)
    return False
