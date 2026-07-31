#!/usr/bin/env python3
"""One-turn, local-only smoke harness for the nmbot Jivo bridge.

This is a diagnostic utility, not a runtime client. It deliberately sends at
most one non-sensitive synthetic CLIENT_MESSAGE, and only when --live is set.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request
from urllib.parse import quote


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8093
DEFAULT_TIMEOUT_SEC = 10.0
PROVIDER_TOKEN_ENV = "JIVO_PROVIDER_TOKEN"
BRIDGE_TOKEN_ENV = "NMBOT_N8N_BRIDGE_TOKEN"
SAFE_TRACE_REF_LENGTH = 18  # ``trace_`` plus the bridge's 12-character digest.


class SafeSmokeError(Exception):
    """An expected failure which must not disclose request data or secrets."""


@dataclass(frozen=True)
class SmokeConfig:
    host: str
    port: int
    timeout: float
    live: bool
    delivery_trace: bool


def _loopback_host(value: str) -> str:
    host = str(value).strip()
    # Literal loopback only: DNS names could resolve outside the local machine.
    if host not in {"127.0.0.1", "::1"}:
        raise argparse.ArgumentTypeError("host must be a literal loopback address")
    return host


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not 0 < timeout <= 30:
        raise argparse.ArgumentTypeError("timeout must be greater than 0 and at most 30 seconds")
    return timeout


def parse_args(argv: list[str] | None = None) -> SmokeConfig:
    parser = argparse.ArgumentParser(description="Send one explicit local bridge smoke request.")
    parser.add_argument("--host", type=_loopback_host, default=DEFAULT_HOST)
    parser.add_argument("--port", type=_port, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=_timeout, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--live", action="store_true", help="Allow exactly one local HTTP POST.")
    parser.add_argument(
        "--delivery-trace",
        action="store_true",
        help="Print the read-only command for terminal-delivery confirmation; makes no request.",
    )
    args = parser.parse_args(argv)
    if args.delivery_trace and args.live:
        parser.error("--delivery-trace cannot be combined with --live")
    return SmokeConfig(args.host, args.port, args.timeout, bool(args.live), bool(args.delivery_trace))


def _opaque_ref() -> str:
    """Generate an unpredictable synthetic identifier with no user data."""
    return secrets.token_hex(16)


def build_payload() -> dict[str, Any]:
    ref = _opaque_ref()
    return {
        "event": "CLIENT_MESSAGE",
        "id": _opaque_ref(),
        "site_id": ref,
        "chat_id": _opaque_ref(),
        "client_id": _opaque_ref(),
        "agents_online": True,
        "sender": {"id": _opaque_ref(), "name": "nmbot smoke", "has_contacts": False},
        "message": {"type": "TEXT", "text": "nmbot bridge smoke"},
        "channel": {"id": _opaque_ref(), "type": "widget"},
    }


def endpoint_url(host: str, port: int, provider_token: str) -> str:
    checked_host = _loopback_host(host)
    checked_port = _port(str(port))
    authority = f"[{checked_host}]" if checked_host == "::1" else checked_host
    return f"http://{authority}:{checked_port}/jivo/{quote(provider_token, safe='')}"


def _safe_trace_ref(response: dict[str, Any]) -> str | None:
    candidate = response.get("trace_ref")
    if candidate is None and isinstance(response.get("meta"), dict):
        candidate = response["meta"].get("trace_ref")
    value = str(candidate or "")
    if len(value) == SAFE_TRACE_REF_LENGTH and value.startswith("trace_") and all(c in "0123456789abcdef" for c in value[6:]):
        return value
    return None


def post_once(
    config: SmokeConfig,
    *,
    environ: dict[str, str],
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if not config.live:
        raise SafeSmokeError("live flag required")
    provider_token = str(environ.get(PROVIDER_TOKEN_ENV, "")).strip()
    bridge_token = str(environ.get(BRIDGE_TOKEN_ENV, "")).strip()
    if not provider_token or not bridge_token:
        raise SafeSmokeError("required environment token missing")

    body = json.dumps(build_payload(), separators=(",", ":")).encode("utf-8")
    req = request.Request(
        endpoint_url(config.host, config.port, provider_token),
        data=body,
        headers={"Content-Type": "application/json", "X-NMBOT-Bridge-Token": bridge_token},
        method="POST",
    )
    try:
        with (opener or request.urlopen)(req, timeout=config.timeout) as response:
            status = int(getattr(response, "status", 200))
            raw = response.read()
    except (TimeoutError, error.HTTPError, error.URLError, OSError) as exc:
        raise SafeSmokeError("request failed") from exc

    accepted = False
    trace_ref = None
    if 200 <= status < 300:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {}
        if isinstance(parsed, dict):
            trace_ref = _safe_trace_ref(parsed)
            accepted = trace_ref is not None
    return {"http_status": status, "accepted_async": accepted, "trace_ref": trace_ref}


def _print_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))


def main(
    argv: list[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
    opener: Callable[..., Any] | None = None,
) -> int:
    config = parse_args(argv)
    if config.delivery_trace:
        print("bash scripts/nmbot_jivo_audit.sh --delivery-trace")
        return 0
    try:
        result = post_once(config, environ=environ if environ is not None else os.environ, opener=opener)
    except SafeSmokeError:
        _print_result({"http_status": None, "accepted_async": False, "trace_ref": None})
        return 2
    _print_result(result)
    return 0 if result["accepted_async"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
