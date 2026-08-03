"""Strict JSON-safe boundary between the version router and one runtime.

The contract intentionally contains opaque conversation and trace references only.
Runtime implementations must never put source payloads, provider output, or IDs in
the response envelope.
"""
from __future__ import annotations

import re
from typing import Any

CONTRACT_VERSION = "nmbot.runtime-wire.v1"
SUPPORTED_RUNTIME_VERSIONS = frozenset({"V0", "V1", "V2", "V3"})
_REF_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_CODE_RE = re.compile(r"^[a-z0-9_:-]{1,64}$")
_CHANNELS = frozenset({"api", "jivo", "internal", "telegram", "test"})
_META_KEYS = frozenset({"entrypoint", "locale", "session_label"})


class WireContractError(ValueError):
    """Raised when a payload does not satisfy the closed wire contract."""


def _object(value: Any, *, fields: frozenset[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WireContractError(f"{name}_must_be_object")
    unknown = set(value) - fields
    if unknown:
        raise WireContractError(f"{name}_unknown_field")
    return value


def _string(value: Any, *, name: str, minimum: int = 0, maximum: int, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise WireContractError(f"invalid_{name}")
    if pattern is not None and not pattern.fullmatch(value):
        raise WireContractError(f"invalid_{name}")
    return value


def _version(value: Any) -> str:
    version = _string(value, name="runtime_version", minimum=2, maximum=2)
    if version not in SUPPORTED_RUNTIME_VERSIONS:
        raise WireContractError("unsupported_runtime_version")
    return version


def _router_ingress_request(payload: Any, *, reset: bool) -> dict[str, Any]:
    """Validate the public router request, which deliberately has no selector."""
    fields = frozenset({"contract_version", "conversation_ref", "trace_ref"})
    if not reset:
        fields |= {"message", "channel", "meta"}
    data = _object(payload, fields=fields, name="request")
    if data.get("contract_version") != CONTRACT_VERSION:
        raise WireContractError("unsupported_contract_version")
    normalized = {
        "contract_version": CONTRACT_VERSION,
        "conversation_ref": _string(data.get("conversation_ref"), name="conversation_ref", minimum=8, maximum=128, pattern=_REF_RE),
        "trace_ref": _string(data.get("trace_ref"), name="trace_ref", minimum=8, maximum=128, pattern=_REF_RE),
    }
    if reset:
        return normalized
    normalized["message"] = _string(data.get("message"), name="message", minimum=1, maximum=4000)
    channel = _string(data.get("channel"), name="channel", minimum=1, maximum=16)
    if channel not in _CHANNELS:
        raise WireContractError("invalid_channel")
    normalized["channel"] = channel
    meta = _object(data.get("meta", {}), fields=_META_KEYS, name="meta")
    normalized["meta"] = {
        key: _string(value, name=f"meta_{key}", minimum=1, maximum=120)
        for key, value in meta.items()
    }
    return normalized


def validate_router_chat_ingress(payload: Any) -> dict[str, Any]:
    """Validate the public ``/api/chat`` request accepted by the router."""
    return _router_ingress_request(payload, reset=False)


def validate_router_reset_ingress(payload: Any) -> dict[str, Any]:
    """Validate the public ``/api/reset`` request accepted by the router."""
    return _router_ingress_request(payload, reset=True)


def _worker_request(payload: Any, *, reset: bool) -> dict[str, Any]:
    """Validate the private router-to-selected-runtime wire payload."""
    fields = frozenset({"contract_version", "runtime_version", "conversation_ref", "trace_ref"})
    if not reset:
        fields |= {"message", "channel", "meta"}
    data = _object(payload, fields=fields, name="worker_request")
    version = _version(data.get("runtime_version"))
    ingress = _router_ingress_request(
        {key: value for key, value in data.items() if key != "runtime_version"}, reset=reset
    )
    return {"contract_version": CONTRACT_VERSION, "runtime_version": version, **ingress}


def validate_worker_chat_request(payload: Any) -> dict[str, Any]:
    """Validate a private selected-runtime chat request."""
    return _worker_request(payload, reset=False)


def validate_worker_reset_request(payload: Any) -> dict[str, Any]:
    """Validate a private selected-runtime reset request."""
    return _worker_request(payload, reset=True)


def make_worker_chat_request(ingress: Any, *, runtime_version: str) -> dict[str, Any]:
    """Inject the router-owned selector into a closed private chat payload."""
    return validate_worker_chat_request({**validate_router_chat_ingress(ingress), "runtime_version": runtime_version})


def make_worker_reset_request(ingress: Any, *, runtime_version: str) -> dict[str, Any]:
    """Inject the router-owned selector into a closed private reset payload."""
    return validate_worker_reset_request({**validate_router_reset_ingress(ingress), "runtime_version": runtime_version})


def _response(payload: Any, *, expected_version: str, reset: bool) -> dict[str, Any]:
    fields = frozenset({"contract_version", "ok", "runtime_version", "error_code", "diagnostics"})
    fields |= {"reset"} if reset else {"client_answer", "handoff"}
    data = _object(payload, fields=fields, name="response")
    if data.get("contract_version") != CONTRACT_VERSION:
        raise WireContractError("unsupported_contract_version")
    if _version(data.get("runtime_version")) != expected_version:
        raise WireContractError("runtime_version_mismatch")
    if not isinstance(data.get("ok"), bool):
        raise WireContractError("invalid_ok")
    error = data.get("error_code")
    if error is not None:
        _string(error, name="error_code", minimum=1, maximum=64, pattern=_CODE_RE)
    diagnostics = _object(data.get("diagnostics", {}), fields=frozenset({"code", "elapsed_ms"}), name="diagnostics")
    if "code" in diagnostics:
        _string(diagnostics["code"], name="diagnostic_code", minimum=1, maximum=64, pattern=_CODE_RE)
    if "elapsed_ms" in diagnostics and (not isinstance(diagnostics["elapsed_ms"], int) or isinstance(diagnostics["elapsed_ms"], bool) or not 0 <= diagnostics["elapsed_ms"] <= 120000):
        raise WireContractError("invalid_elapsed_ms")
    normalized = {"contract_version": CONTRACT_VERSION, "ok": data["ok"], "runtime_version": expected_version, "error_code": error, "diagnostics": dict(diagnostics)}
    if reset:
        if not isinstance(data.get("reset"), bool):
            raise WireContractError("invalid_reset")
        normalized["reset"] = data["reset"]
    else:
        normalized["client_answer"] = _string(data.get("client_answer"), name="client_answer", maximum=8000)
        if not isinstance(data.get("handoff"), bool):
            raise WireContractError("invalid_handoff")
        normalized["handoff"] = data["handoff"]
    return normalized


def validate_chat_response(payload: Any, *, expected_version: str) -> dict[str, Any]:
    return _response(payload, expected_version=expected_version, reset=False)


def validate_reset_response(payload: Any, *, expected_version: str) -> dict[str, Any]:
    return _response(payload, expected_version=expected_version, reset=True)
