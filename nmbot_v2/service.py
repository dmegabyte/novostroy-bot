"""Private local V2 worker with V2-owned state and injected ports only."""
from __future__ import annotations

import re
from pathlib import Path
from time import monotonic
from typing import Any

from aiohttp import web

from nmbot_runtime_contract.wire import CONTRACT_VERSION, validate_chat_response
from nmbot_runtime_service_host.http import ServiceTurn, create_app as create_host_app

from . import RUNTIME_VERSION
from .composition import build_turn_processor
from .ports import V2RuntimePorts
from .state import ConversationState
from .contracts import SafeTurnContext


_SAFE_CODE_RE = re.compile(r"^[a-z0-9_:-]{1,64}$")


def _safe_response(*, ok: bool, answer: str, code: str | None, elapsed_ms: int = 0) -> dict[str, Any]:
    return validate_chat_response({
        "contract_version": CONTRACT_VERSION,
        "ok": ok,
        "runtime_version": RUNTIME_VERSION,
        "client_answer": answer,
        "handoff": False,
        "error_code": code,
        "diagnostics": {"code": code or "ok", "elapsed_ms": max(0, min(elapsed_ms, 120000))},
    }, expected_version=RUNTIME_VERSION)


def _safe_code(value: object, *, fallback: str) -> str:
    code = str(value or "")
    return code if _SAFE_CODE_RE.fullmatch(code) else fallback


def build_turn(*, planner_port: Any = None, search_port: Any = None,
               response_composer_port: Any = None, response_composer_mode: str = "off",
               manager_rewriter_port: Any = None, manager_rewriter_mode: str = "off"):
    """Build a V2 turn from ports supplied by this worker's composition root.

    This module deliberately has no environment-to-provider adapter.  An absent
    planner is a closed failure; an injected search port remains optional because
    V2 has valid state-only turns that do not search.
    """
    async def turn(payload: dict[str, Any], state_before: dict[str, Any] | None) -> ServiceTurn:
        if planner_port is None:
            return ServiceTurn(_safe_response(
                ok=False,
                answer="Сейчас не получилось безопасно запустить V2. Условия не меняла.",
                code="missing_v2_planner_port",
            ))
        started = monotonic()
        try:
            state = ConversationState.from_dict(state_before)
            result = await build_turn_processor(
                V2RuntimePorts(
                    planner=planner_port,
                    search_service=search_port,
                    response_composer=response_composer_port,
                    manager_rewriter=manager_rewriter_port,
                ),
                response_composer_mode=response_composer_mode,
                manager_rewriter_mode=manager_rewriter_mode,
            ).process_async(SafeTurnContext(
                conversation_ref=payload["conversation_ref"],
                user_text=payload["message"],
            ), state)
        except Exception:
            return ServiceTurn(_safe_response(
                ok=False,
                answer="Сейчас не получилось безопасно обработать запрос. Условия не меняла.",
                code="v2_runtime_failure",
                elapsed_ms=int((monotonic() - started) * 1000),
            ))
        accepted = bool(result.trace.get("accepted_state"))
        code = None if result.execution.ok else _safe_code(result.execution.error_code, fallback="v2_runtime_error")
        return ServiceTurn(
            _safe_response(
                ok=result.execution.ok,
                answer=result.response_text,
                code=code,
                elapsed_ms=int((monotonic() - started) * 1000),
            ),
            result.state if accepted else None,
        )
    return turn


def create_app(*, state_path: Path, journal_path: Path, token: str, release_identity: str,
               planner_port: Any = None, search_port: Any = None,
               response_composer_port: Any = None, response_composer_mode: str = "off",
               manager_rewriter_port: Any = None, manager_rewriter_mode: str = "off") -> web.Application:
    """Create a V2-only private worker; all executable dependencies are injected."""
    return create_host_app(
        runtime_version=RUNTIME_VERSION,
        token=token,
        release_identity=release_identity,
        state_path=state_path,
        journal_path=journal_path,
        turn=build_turn(
            planner_port=planner_port,
            search_port=search_port,
            response_composer_port=response_composer_port,
            response_composer_mode=response_composer_mode,
            manager_rewriter_port=manager_rewriter_port,
            manager_rewriter_mode=manager_rewriter_mode,
        ),
        reset=lambda: ConversationState().to_dict(),
    )
