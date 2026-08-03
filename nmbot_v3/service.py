"""Private V3 worker: no provider, search, composer, or cross-version fallback."""
from __future__ import annotations

from pathlib import Path
from time import monotonic
from typing import Any

from aiohttp import web

from nmbot_runtime_contract.wire import CONTRACT_VERSION, validate_chat_response
from nmbot_runtime_service_host.http import ServiceTurn, create_app as create_host_app

from . import RUNTIME_VERSION
from .runtime import run_turn
from .contracts import PHONE_RE
from .state import V3ConversationState


def _safe_response(*, ok: bool, answer: str, code: str | None, elapsed_ms: int = 0) -> dict[str, Any]:
    return validate_chat_response({"contract_version": CONTRACT_VERSION, "ok": ok, "runtime_version": RUNTIME_VERSION,
        "client_answer": answer, "handoff": False, "error_code": code,
        "diagnostics": {"code": code or "ok", "elapsed_ms": max(0, min(elapsed_ms, 120000))}}, expected_version=RUNTIME_VERSION)


def build_turn(*, planner_port: Any = None, evidence_port: Any = None, writer_port: Any = None):
    async def turn(payload: dict[str, Any], state_before: dict[str, Any] | None) -> ServiceTurn:
        if PHONE_RE.search(payload["message"]):
            return ServiceTurn(_safe_response(ok=False, answer="Не могу безопасно принять номер в этом контуре. Попробуйте позже.", code="v3_phone_flow_unmigrated"))
        if planner_port is None:
            return ServiceTurn(_safe_response(ok=False, answer="Сейчас не получилось безопасно запустить V3. Условия не меняла.", code="missing_v3_planner_port"))
        if evidence_port is None:
            return ServiceTurn(_safe_response(ok=False, answer="Сейчас не получилось безопасно запустить V3. Условия не меняла.", code="missing_v3_evidence_port"))
        started = monotonic()
        result = await run_turn(payload["message"], state_before, planner_port, evidence_port, writer_port)
        if result.safe_code:
            return ServiceTurn(_safe_response(ok=False, answer=result.response_text, code=result.safe_code,
                elapsed_ms=int((monotonic() - started) * 1000)))
        return ServiceTurn(_safe_response(ok=True, answer=result.response_text, code=None,
            elapsed_ms=int((monotonic() - started) * 1000)), result.state)
    return turn


def create_app(*, state_path: Path, journal_path: Path, token: str, release_identity: str,
                planner_port: Any = None, evidence_port: Any = None, writer_port: Any = None) -> web.Application:
    return create_host_app(runtime_version=RUNTIME_VERSION, token=token, release_identity=release_identity,
        state_path=state_path, journal_path=journal_path,
        turn=build_turn(planner_port=planner_port, evidence_port=evidence_port, writer_port=writer_port),
        reset=lambda: V3ConversationState.clean().to_dict())
