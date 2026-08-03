"""Outer V2 host composition boundary for the isolated private worker.

Only this module turns explicitly named V2 environment settings into the
gateway client owned by ``build_v2_outer_app``.  It never imports a selector,
legacy runtime adapter, public API bridge, or Jivo integration.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from aiohttp import web

from nmbot_runtime_service_host.http import validate_release_identity
from nmbot_v2.gateway import V2GatewayConfig
from nmbot_v2.outer_composition import V2OuterCompositionConfig, build_v2_outer_app
from nmbot_v2.planner_gateway_contract import require_v2_planner_gateway_contract


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODES = frozenset({"off", "shadow", "publish"})
_DEFAULT_STATE_PATH = "data/nmbot-v2-state.json"
_DEFAULT_JOURNAL_PATH = "logs/nmbot-v2-runtime.jsonl"
_DEFAULT_PORT = 18082


class V2HostConfigurationError(ValueError):
    """Stable configuration error raised before an app or listener exists."""


@dataclass(frozen=True)
class V2HostSettings:
    """Validated non-secret settings consumed by the standalone V2 host."""

    port: int
    state_path: Path
    journal_path: Path
    token: str
    release_identity: str
    gateway_config: V2GatewayConfig
    planner_model: str
    planner_timeout_seconds: float
    response_composer_mode: str
    manager_rewriter_mode: str


def create_v2_host_app(*, settings: V2HostSettings, session: Any = None) -> web.Application:
    """Compose the V2 worker from already-validated host settings.

    The outer composition creates and owns the gateway client.  An optional
    session exists strictly for fake-session tests and embedding hosts.
    """
    if not isinstance(settings, V2HostSettings):
        raise TypeError("v2_host_settings_required")
    return build_v2_outer_app(
        config=V2OuterCompositionConfig(
            gateway_config=settings.gateway_config,
            gateway_session=session,
            gateway_session_owned=session is not None,
            planner_model=settings.planner_model,
            planner_timeout_seconds=settings.planner_timeout_seconds,
            response_composer_mode=settings.response_composer_mode,
            manager_rewriter_mode=settings.manager_rewriter_mode,
        ),
        state_path=settings.state_path,
        journal_path=settings.journal_path,
        token=settings.token,
        release_identity=settings.release_identity,
    )


def settings_from_environ(*, environ: Mapping[str, str] | None = None) -> V2HostSettings:
    """Validate every V2 host input before constructing the application."""
    source = os.environ if environ is None else environ
    release_identity = validate_release_identity(_required(source, "NMBOT_V2_RELEASE_ID"))
    token = _required(source, "NMBOT_V2_INTERNAL_TOKEN")
    gateway_url = _gateway_url(_required(source, "NMBOT_V2_GATEWAY_URL"))
    credential_env = _required(source, "NMBOT_V2_GATEWAY_TOKEN_ENV")
    if not _ENV_NAME.fullmatch(credential_env):
        raise V2HostConfigurationError("invalid_v2_gateway_token_env")
    credential = str(source.get(credential_env, "")).strip()
    if not credential:
        raise V2HostConfigurationError(f"missing_v2_gateway_credential:{credential_env}")
    planner_model = _model(_required(source, "NMBOT_V2_PLANNER_MODEL"), "NMBOT_V2_PLANNER_MODEL")
    return V2HostSettings(
        port=_port(source.get("NMBOT_V2_PORT", str(_DEFAULT_PORT))),
        state_path=_path(source.get("NMBOT_V2_STATE_PATH", _DEFAULT_STATE_PATH), "NMBOT_V2_STATE_PATH"),
        journal_path=_path(source.get("NMBOT_V2_JOURNAL_PATH", _DEFAULT_JOURNAL_PATH), "NMBOT_V2_JOURNAL_PATH"),
        token=token,
        release_identity=release_identity,
        gateway_config=V2GatewayConfig(
            base_url=gateway_url,
            token=credential,
            request_timeout_seconds=_timeout(source.get("NMBOT_V2_GATEWAY_REQUEST_TIMEOUT_SECONDS", "25"), "NMBOT_V2_GATEWAY_REQUEST_TIMEOUT_SECONDS"),
            poll_interval_seconds=_timeout(source.get("NMBOT_V2_GATEWAY_POLL_INTERVAL_SECONDS", "1"), "NMBOT_V2_GATEWAY_POLL_INTERVAL_SECONDS", minimum=0.05, maximum=10.0),
        ),
        planner_model=planner_model,
        planner_timeout_seconds=_timeout(source.get("NMBOT_V2_PLANNER_TIMEOUT_SECONDS", "10"), "NMBOT_V2_PLANNER_TIMEOUT_SECONDS"),
        response_composer_mode=_mode(source.get("NMBOT_V2_RESPONSE_COMPOSER_MODE", "off"), "NMBOT_V2_RESPONSE_COMPOSER_MODE"),
        manager_rewriter_mode=_mode(source.get("NMBOT_V2_MANAGER_REWRITER_MODE", "off"), "NMBOT_V2_MANAGER_REWRITER_MODE"),
    )


def create_v2_host_app_from_environ(*, environ: Mapping[str, str] | None = None, session: Any = None) -> web.Application:
    """Create a standalone V2 worker only after the complete config is valid."""
    return create_v2_host_app(settings=settings_from_environ(environ=environ), session=session)


def main() -> None:
    """Bind only the isolated V2 worker to loopback after all validation."""
    require_v2_planner_gateway_contract()
    settings = settings_from_environ()
    app = create_v2_host_app(settings=settings)
    web.run_app(app, host="127.0.0.1", port=settings.port)


def _required(source: Mapping[str, str], key: str) -> str:
    value = str(source.get(key, "")).strip()
    if not value:
        raise V2HostConfigurationError(f"missing_v2_config:{key}")
    return value


def _gateway_url(value: str) -> str:
    """Accept only a credential-free HTTP(S) gateway base URL."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise V2HostConfigurationError("invalid_v2_config:NMBOT_V2_GATEWAY_URL") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise V2HostConfigurationError("invalid_v2_config:NMBOT_V2_GATEWAY_URL")
    return value


def _timeout(value: object, key: str, *, minimum: float = 0.1, maximum: float = 120.0) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        timeout = 0.0
    if not minimum <= timeout <= maximum:
        raise V2HostConfigurationError(f"invalid_v2_config:{key}")
    return timeout


def _port(value: object) -> int:
    try:
        port = int(str(value))
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        raise V2HostConfigurationError("invalid_v2_config:NMBOT_V2_PORT")
    return port


def _path(value: object, key: str) -> Path:
    path = str(value or "").strip()
    if not path:
        raise V2HostConfigurationError(f"invalid_v2_config:{key}")
    return Path(path)


def _model(value: str, key: str) -> str:
    model = value.strip()
    if not model or len(model) > 160:
        raise V2HostConfigurationError(f"invalid_v2_config:{key}")
    return model


def _mode(value: object, key: str) -> str:
    mode = str(value or "").strip().lower()
    if mode not in _MODES:
        raise V2HostConfigurationError(f"invalid_v2_config:{key}")
    return mode


if __name__ == "__main__":
    main()
