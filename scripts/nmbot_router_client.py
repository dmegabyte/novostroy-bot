"""Local-only client seam for the isolated NMBot version router.

This module owns neither router state nor configuration discovery.  A caller
must supply both the loopback URL and token, and may either supply an aiohttp
session or let this client create and later close one.
"""
from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from nmbot_runtime_contract import (
    SUPPORTED_RUNTIME_VERSIONS,
    WireContractError,
    validate_chat_response,
    validate_reset_response,
    validate_router_chat_ingress,
    validate_router_reset_ingress,
)


_MIN_TIMEOUT_SECONDS = 0.05
_MAX_TIMEOUT_SECONDS = 30.0
_SELECTOR_FIELDS = frozenset({"ok", "runtime_version"})


class RouterClientError(RuntimeError):
    """A stable, redacted local-client failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _validated_base_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid_router_base_url")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or not ipaddress.ip_address(parsed.hostname).is_loopback
        ):
            raise ValueError("invalid_router_base_url")
        # Accessing port validates malformed and out-of-range ports.
        _ = parsed.port
    except (TypeError, ValueError):
        raise ValueError("invalid_router_base_url") from None
    return value.rstrip("/")


@dataclass(frozen=True)
class RouterClientConfig:
    """Explicit local-router connection data; no environment lookup occurs."""

    base_url: str
    token: str
    timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        normalized_url = _validated_base_url(self.base_url)
        if not isinstance(self.token, str) or not self.token or len(self.token) > 1024 or "\r" in self.token or "\n" in self.token:
            raise ValueError("invalid_router_token")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not _MIN_TIMEOUT_SECONDS <= float(self.timeout_seconds) <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("invalid_router_timeout")
        object.__setattr__(self, "base_url", normalized_url)
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


def _selector_response(payload: Any, *, expected_version: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _SELECTOR_FIELDS or payload.get("ok") is not True:
        raise WireContractError("invalid_selector_response")
    version = payload.get("runtime_version")
    if version not in SUPPORTED_RUNTIME_VERSIONS:
        raise WireContractError("invalid_selector_response")
    if expected_version is not None and version != expected_version:
        raise WireContractError("runtime_version_mismatch")
    return {"ok": True, "runtime_version": version}


class NMBotRouterClient:
    """One-shot-only client for a loopback version router.

    A supplied session remains caller-owned and is never closed here.  Without
    one, the client creates an owned session on its first call; call ``aclose``
    (or use ``async with``) to close that session.
    """

    def __init__(self, config: RouterClientConfig, *, session: aiohttp.ClientSession | None = None) -> None:
        self.config = config
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> "NMBotRouterClient":
        await self._get_session()
        return self

    async def __aexit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close only the session created by this instance."""
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds))
        if self._session.closed:
            raise RouterClientError("router_unavailable")
        return self._session

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        session = await self._get_session()
        try:
            async with session.request(
                method,
                self.config.base_url + path,
                json=payload,
                headers={"Authorization": "Bearer " + self.config.token},
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise RouterClientError("router_unavailable")
                body = await response.json(content_type=None)
        except RouterClientError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError, ValueError):
            raise RouterClientError("router_unavailable") from None
        if not isinstance(body, dict):
            raise RouterClientError("router_unavailable")
        return body

    async def chat(self, payload: Any, *, expected_runtime_version: str) -> dict[str, Any]:
        """Send one validated chat request; selector expectation stays out of its wire body."""
        try:
            request = validate_router_chat_ingress(payload)
            if expected_runtime_version not in SUPPORTED_RUNTIME_VERSIONS:
                raise WireContractError("unsupported_runtime_version")
        except WireContractError:
            raise RouterClientError("invalid_request") from None
        try:
            return validate_chat_response(await self._request("POST", "/api/chat", request), expected_version=expected_runtime_version)
        except WireContractError:
            raise RouterClientError("invalid_router_response") from None

    async def reset(self, payload: Any, *, expected_runtime_version: str) -> dict[str, Any]:
        """Send one validated reset request; no selector fallback is attempted."""
        try:
            request = validate_router_reset_ingress(payload)
            if expected_runtime_version not in SUPPORTED_RUNTIME_VERSIONS:
                raise WireContractError("unsupported_runtime_version")
        except WireContractError:
            raise RouterClientError("invalid_request") from None
        try:
            return validate_reset_response(await self._request("POST", "/api/reset", request), expected_version=expected_runtime_version)
        except WireContractError:
            raise RouterClientError("invalid_router_response") from None

    async def get_selector(self) -> str:
        """Read the router-owned selected runtime without taking ownership of it."""
        try:
            return _selector_response(await self._request("GET", "/api/runtime-version"))["runtime_version"]
        except WireContractError:
            raise RouterClientError("invalid_router_response") from None

    async def set_selector(self, runtime_version: str) -> str:
        """Mutate the selector using PUT only and validate its exact acknowledgement."""
        if runtime_version not in SUPPORTED_RUNTIME_VERSIONS:
            raise RouterClientError("invalid_request")
        try:
            response = _selector_response(
                await self._request("PUT", "/api/runtime-version", {"runtime_version": runtime_version}),
                expected_version=runtime_version,
            )
            return response["runtime_version"]
        except WireContractError:
            raise RouterClientError("invalid_router_response") from None
