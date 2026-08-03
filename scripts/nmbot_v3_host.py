"""Outer V3 host composition boundary.

This module joins the isolated V3 worker either to an explicitly injected
transport or to the V3-only gateway transport factory.  It never imports a
global client, selector, Jivo adapter, or V2 retry/fallback policy.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp import web

from nmbot_runtime_service_host.http import validate_release_identity
from nmbot_v3.factory import build_v3_adapter_factory
from nmbot_v3.gateway_transport import V3GatewayTaskTransport
from nmbot_v3.provider_invocation import V3AsyncTransport
from nmbot_v3.service import create_app


class V3HostConfigurationError(RuntimeError):
    """A stable fail-before-bind error for absent outer host configuration."""


def create_v3_host_app(
    *,
    transport: V3AsyncTransport[Any, Any],
    timeout_seconds: float,
    state_path: Path,
    journal_path: Path,
    token: str,
    release_identity: str,
) -> web.Application:
    """Compose a V3 worker from explicitly injected dependencies only.

    ``transport`` is caller-owned: this host neither constructs nor closes it.
    Building adapters happens before constructing the HTTP app, so a missing or
    invalid transport fails before any listener can be bound.
    """
    adapters = build_v3_adapter_factory(transport, timeout_seconds=timeout_seconds)
    app = create_app(
        state_path=state_path,
        journal_path=journal_path,
        token=token,
        release_identity=release_identity,
        planner_port=adapters.planner,
        evidence_port=adapters.evidence,
        writer_port=adapters.writer,
    )
    close = getattr(transport, "close", None)
    if callable(close):
        async def close_transport(_app: web.Application) -> None:
            result = close()
            if hasattr(result, "__await__"):
                await result
        app.on_cleanup.append(close_transport)
    return app


def create_v3_host_app_from_environ(*, environ: dict[str, str] | None = None, session_factory: Any = None) -> web.Application:
    """Create the standalone V3 host only when all V3 gateway config is present."""
    import os
    import aiohttp

    source = os.environ if environ is None else environ
    transport = V3GatewayTaskTransport.from_environ(
        environ=source,
        session_factory=aiohttp.ClientSession if session_factory is None else session_factory,
    )
    return create_v3_host_app(
        transport=transport,
        timeout_seconds=transport._config.task_timeout_seconds,
        state_path=Path(source.get("NMBOT_V3_STATE_PATH", "data/nmbot-v3-state.json")),
        journal_path=Path(source.get("NMBOT_V3_JOURNAL_PATH", "logs/nmbot-v3-runtime.jsonl")),
        token=source.get("NMBOT_V3_INTERNAL_TOKEN", ""),
        release_identity=source.get("NMBOT_V3_RELEASE_ID", "").strip(),
    )


def main() -> None:
    """Bind only the separate V3 worker; it does not change public routing."""
    import os

    validate_release_identity(os.getenv("NMBOT_V3_RELEASE_ID", "").strip())
    app = create_v3_host_app_from_environ()
    web.run_app(app, host="127.0.0.1", port=int(os.getenv("NMBOT_V3_PORT", "18083")))
