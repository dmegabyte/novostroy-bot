"""Internal-only NMBot version router.  It contains no runtime implementation."""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from nmbot_runtime_contract import (
    SUPPORTED_RUNTIME_VERSIONS,
    WireContractError,
    validate_chat_response,
    make_worker_chat_request,
    make_worker_reset_request,
    validate_router_chat_ingress,
    validate_router_reset_ingress,
    validate_reset_response,
)
from nmbot_runtime_contract.selector import (
    InvalidRuntimeVersion,
    SelectorStore,
    SelectorUnavailable,
)

REQUEST_TIMEOUT_SECONDS = 3
ENV_PREFIX = "NMBOT_"


def _safe_error(code: str, *, status: int) -> web.Response:
    return web.json_response({"ok": False, "error_code": code}, status=status)


def _is_internal_endpoint(value: str) -> bool:
    try:
        parsed = urlparse(value)
        if parsed.scheme != "http" or not parsed.hostname or not parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


SELECTOR_STORE_KEY: web.AppKey[SelectorStore] = web.AppKey("selector_store", SelectorStore)
ROUTER_TOKEN_KEY: web.AppKey[str] = web.AppKey("router_token", str)
CLIENT_SESSION_KEY: web.AppKey[ClientSession] = web.AppKey("client_session", ClientSession)
CLIENT_TIMEOUT_SECONDS_KEY: web.AppKey[float] = web.AppKey("client_timeout_seconds", float)


def _endpoint_for(version: str, operation: str) -> str | None:
    base = os.getenv(f"{ENV_PREFIX}{version}_INTERNAL_ENDPOINT", "").strip()
    if not _is_internal_endpoint(base):
        return None
    return base.rstrip("/") + ("/api/chat" if operation == "chat" else "/api/reset")


def _worker_token_for(version: str) -> str | None:
    """Return only the selected worker's private token, never the ingress token."""
    token = os.getenv(f"{ENV_PREFIX}{version}_INTERNAL_TOKEN", "").strip()
    return token or None


def _authorized(request: web.Request) -> bool:
    expected = request.app[ROUTER_TOKEN_KEY]
    provided = request.headers.get("Authorization", "")
    return bool(expected) and hmac.compare_digest(provided, f"Bearer {expected}")


async def _forward(request: web.Request, *, payload: dict[str, Any], operation: str) -> web.Response:
    try:
        version = await request.app[SELECTOR_STORE_KEY].get()
    except SelectorUnavailable:
        return _safe_error("selector_unavailable", status=503)
    endpoint = _endpoint_for(version, operation)
    worker_token = _worker_token_for(version)
    if endpoint is None or worker_token is None:
        return _safe_error("runtime_unavailable", status=503)
    session = request.app.get(CLIENT_SESSION_KEY)
    if session is None or session.closed:
        return _safe_error("runtime_unavailable", status=503)
    worker_payload = (
        make_worker_chat_request(payload, runtime_version=version)
        if operation == "chat"
        else make_worker_reset_request(payload, runtime_version=version)
    )
    try:
        async with session.post(
            endpoint,
            json=worker_payload,
            headers={"Authorization": f"Bearer {worker_token}"},
            allow_redirects=False,
        ) as downstream:
            if downstream.status != 200:
                return _safe_error("runtime_unavailable", status=503)
            response_payload = await downstream.json(content_type=None)
        validator = validate_chat_response if operation == "chat" else validate_reset_response
        response = validator(response_payload, expected_version=version)
        return web.json_response(response, status=200)
    except (asyncio.TimeoutError, ClientError, ValueError, WireContractError):
        return _safe_error("runtime_unavailable", status=503)


async def handle_chat(request: web.Request) -> web.Response:
    if not _authorized(request):
        return _safe_error("unauthorized", status=401)
    try:
        payload = validate_router_chat_ingress(await request.json())
    except (WireContractError, json.JSONDecodeError, ValueError):
        return _safe_error("invalid_request", status=400)
    return await _forward(request, payload=payload, operation="chat")


async def handle_reset(request: web.Request) -> web.Response:
    if not _authorized(request):
        return _safe_error("unauthorized", status=401)
    try:
        payload = validate_router_reset_ingress(await request.json())
    except (WireContractError, json.JSONDecodeError, ValueError):
        return _safe_error("invalid_request", status=400)
    return await _forward(request, payload=payload, operation="reset")


async def handle_selector_get(request: web.Request) -> web.Response:
    if not _authorized(request):
        return _safe_error("unauthorized", status=401)
    try:
        version = await request.app[SELECTOR_STORE_KEY].get()
    except SelectorUnavailable:
        return _safe_error("selector_unavailable", status=503)
    return web.json_response({"ok": True, "runtime_version": version})


async def handle_selector_set(request: web.Request) -> web.Response:
    if not _authorized(request):
        return _safe_error("unauthorized", status=401)
    try:
        payload = await request.json()
        version = payload.get("runtime_version") if isinstance(payload, dict) else None
        if version not in SUPPORTED_RUNTIME_VERSIONS or set(payload) != {"runtime_version"}:
            raise ValueError("invalid")
        await request.app[SELECTOR_STORE_KEY].set(version)
        return web.json_response({"ok": True, "runtime_version": version})
    except (InvalidRuntimeVersion, ValueError, json.JSONDecodeError):
        return _safe_error("invalid_request", status=400)


async def handle_health(request: web.Request) -> web.Response:
    routes = {
        version: {"ready": _endpoint_for(version, "chat") is not None and _worker_token_for(version) is not None}
        for version in sorted(SUPPORTED_RUNTIME_VERSIONS)
    }
    try:
        await request.app[SELECTOR_STORE_KEY].get()
        selector_ready = True
    except SelectorUnavailable:
        selector_ready = False
    return web.json_response({"ok": True, "service": "nmbot-version-router", "selector_ready": selector_ready, "routes": routes})


async def _create_session(app: web.Application) -> None:
    app[CLIENT_SESSION_KEY] = ClientSession(timeout=ClientTimeout(total=app[CLIENT_TIMEOUT_SECONDS_KEY]))


async def _close_session(app: web.Application) -> None:
    session = app.get(CLIENT_SESSION_KEY)
    if session is not None and not session.closed:
        await session.close()


def create_app(*, selector_path: Path | None = None, token: str | None = None, timeout_seconds: float = REQUEST_TIMEOUT_SECONDS) -> web.Application:
    app = web.Application()
    path = selector_path or Path(os.getenv("NMBOT_VERSION_ROUTER_SELECTOR_STATE", "data/nmbot_version_router_selector.json"))
    app[SELECTOR_STORE_KEY] = SelectorStore(path)
    app[ROUTER_TOKEN_KEY] = token if token is not None else os.getenv("NMBOT_VERSION_ROUTER_TOKEN", "")
    app[CLIENT_TIMEOUT_SECONDS_KEY] = timeout_seconds
    app.on_startup.append(_create_session)
    app.on_cleanup.append(_close_session)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/runtime-version", handle_selector_get)
    app.router.add_put("/api/runtime-version", handle_selector_set)
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_post("/api/reset", handle_reset)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=int(os.getenv("NMBOT_VERSION_ROUTER_PORT", "8091")))
