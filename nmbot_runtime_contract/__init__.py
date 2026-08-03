"""Closed, transport-neutral wire contract for isolated NMBot runtimes."""

from .wire import (
    CONTRACT_VERSION,
    SUPPORTED_RUNTIME_VERSIONS,
    WireContractError,
    make_worker_chat_request,
    make_worker_reset_request,
    validate_chat_response,
    validate_router_chat_ingress,
    validate_router_reset_ingress,
    validate_reset_response,
    validate_worker_chat_request,
    validate_worker_reset_request,
)

__all__ = [
    "CONTRACT_VERSION",
    "SUPPORTED_RUNTIME_VERSIONS",
    "WireContractError",
    "make_worker_chat_request",
    "make_worker_reset_request",
    "validate_chat_response",
    "validate_router_chat_ingress",
    "validate_router_reset_ingress",
    "validate_reset_response",
    "validate_worker_chat_request",
    "validate_worker_reset_request",
]
