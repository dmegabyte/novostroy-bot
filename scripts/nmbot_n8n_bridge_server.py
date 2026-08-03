#!/usr/bin/env python3
"""Small public bridge for n8n -> local nmbot Jivo API.

Public side:  http://0.0.0.0:8093/jivo/<provider_token>
Private side: http://127.0.0.1:8088/jivo/<provider_token>
Jivo side:   https://bot.jivosite.com/webhooks/<provider_id>/<provider_token>

The bridge requires X-NMBOT-Bridge-Token and never prints token values.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from aiohttp import ClientSession, web

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from dialogue_journal import append_event as append_journal_event
from nmbot_egress_policy import SAFE_CLIENT_FALLBACK_TEXT, guard_jivo_event, is_client_production


LATEST_CHAT_EVENTS: dict[str, str] = {}
# The duplicate guard owns the response correlation alongside its TTL.  Keeping
# only the timestamp would make a later duplicate acknowledgement mint a new,
# untraceable reference.
DISPATCHED_CHAT_EVENTS: dict[str, tuple[float, str]] = {}
DISPATCHED_CHAT_EVENTS_TTL_SEC = 10 * 60
DISPATCHED_CHAT_EVENTS_MAX_ENTRIES = 1024
APP_TASKS_KEY = "nmbot_bridge_tasks"

DEFAULT_STATUS_UPDATE_TEMPLATES = (
    "Уже работаю над вашим запросом.",
    "Проверяю нужную информацию.",
    "Уточняю детали, чтобы ответить точнее.",
    "Ещё немного — готовлю ответ.",
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


STRUCTURED_LOG_PATH = Path(_env("NMBOT_BRIDGE_STRUCTURED_LOG", "/home/neiro/novostroy-bot/logs/n8n_bridge_structured.jsonl"))
DELIVERY_TRACE_PATH = Path(_env("NMBOT_BRIDGE_DELIVERY_TRACE", "/home/neiro/novostroy-bot/logs/jivo_delivery_trace.jsonl"))
DELIVERY_TRACE_SCHEMA = "nmbot.jivo.delivery_trace.v1"
DELIVERY_TRACE_STAGES = frozenset({"bridge_accepted", "api_completed", "api_failed", "terminal_selected", "jivo_send_attempted", "jivo_response", "terminal_delivery"})
DELIVERY_TRACE_OUTCOMES = frozenset({"accepted", "completed", "failed", "selected", "attempted", "accepted_by_jivo", "rejected_by_jivo", "post_exception", "terminal_send_accepted", "not_sent"})
DELIVERY_TRACE_EVENTS = frozenset({"BOT_MESSAGE", "INVITE_AGENT", "NONE"})
DELIVERY_TRACE_ERRORS = frozenset({"none", "api_exception", "api_http_error", "hard_timeout", "stale_event", "invalid_terminal", "provider_config", "jivo_http_error", "jivo_exception", "cancelled"})
DELIVERY_TRACE_MAX_LATENCY_MS = 3_600_000


def _safe_trace_ref(raw: object) -> str:
    digest = hashlib.sha256(str(raw).encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"trace_{digest}"


def _bounded_trace_int(value: object, *, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return min(max(number, 0), maximum)


def _append_delivery_trace(
    trace_id: str,
    stage: str,
    outcome: str,
    *,
    terminal_event: str = "NONE",
    error_class: str = "none",
    api_status: object = None,
    jivo_status: object = None,
    api_latency_ms: object = None,
    jivo_latency_ms: object = None,
    e2e_latency_ms: object = None,
) -> None:
    """Append one privacy-safe, closed-schema delivery projection event."""
    if stage not in DELIVERY_TRACE_STAGES or outcome not in DELIVERY_TRACE_OUTCOMES:
        return
    record = {
        "schema": DELIVERY_TRACE_SCHEMA,
        "ts": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "trace_ref": _safe_trace_ref(trace_id),
        "stage": stage,
        "outcome": outcome,
        "terminal_event": terminal_event if terminal_event in DELIVERY_TRACE_EVENTS else "NONE",
        # This projection has no Jivo-side client receipt evidence. A terminal
        # HTTP success therefore remains explicitly unconfirmed at the client.
        "client_delivery_status": "client_delivery_unconfirmed" if stage == "terminal_delivery" and outcome == "terminal_send_accepted" else None,
        "error_class": error_class if error_class in DELIVERY_TRACE_ERRORS else "none",
        "api_status": _bounded_trace_int(api_status, maximum=599),
        "jivo_status": _bounded_trace_int(jivo_status, maximum=599),
        "api_latency_ms": _bounded_trace_int(api_latency_ms, maximum=DELIVERY_TRACE_MAX_LATENCY_MS),
        "jivo_latency_ms": _bounded_trace_int(jivo_latency_ms, maximum=DELIVERY_TRACE_MAX_LATENCY_MS),
        "e2e_latency_ms": _bounded_trace_int(e2e_latency_ms, maximum=DELIVERY_TRACE_MAX_LATENCY_MS),
    }
    try:
        DELIVERY_TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DELIVERY_TRACE_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        # Diagnostics are best-effort and must never affect delivery.
        return


@asynccontextmanager
async def _jivo_post_or_exception(session: ClientSession, url: str, payload: bytes):
    """Yield a response or a fixed error class without exposing exception text."""
    try:
        request = session.post(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=10,
        )
    except Exception:
        yield None, "jivo_exception"
        return
    response_yielded = False
    try:
        async with request as response:
            response_yielded = True
            yield response, None
    except Exception:
        # Only convert failures while opening the outbound request. Exceptions
        # from the caller's response processing must keep their original flow.
        if response_yielded:
            raise
        yield None, "jivo_exception"


def _append_bridge_error_to_journal(
    body: bytes,
    *,
    code: str,
    stage: str,
    fallback: bool,
    trace_ref: str | None = None,
) -> None:
    """Attach a safe bridge failure to the same opaque dialogue event when possible."""
    if code not in {
        "bridge_hard_timeout", "bridge_upstream_exception", "bridge_status_delivery_error",
        "bridge_delivery_error", "bridge_async_exception",
    } or stage not in {"bridge_upstream", "bridge_delivery"}:
        return
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    site_id = str(payload.get("site_id") or "").strip()
    chat_id = str(payload.get("chat_id") or "").strip()
    client_id = str(payload.get("client_id") or "").strip()
    if not site_id or not chat_id or not client_id:
        return
    try:
        append_journal_event(
            session_key=f"jivo:{site_id}:{chat_id}:{client_id}",
            role="system",
            event_type="delivery_error",
            event_id=str(payload.get("id") or "").strip() or None,
            meta={"site_id": site_id, "chat_id": chat_id, "client_id": client_id, **({"trace_ref": trace_ref} if trace_ref else {})},
            error_summary={
                "status": "failed" if code != "bridge_status_delivery_error" else "degraded",
                "codes": [code],
                "stages": [stage],
                "fallback": fallback,
            },
            source="bridge",
        )
    except Exception:
        # Journal diagnostics must not interfere with the independently safe bridge fallback.
        return


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "nmbot-n8n-bridge"})


def _jivo_fast_fallback(body: bytes, reason: str, *, text: str | None = None) -> web.Response:
    """Return a valid Jivo BOT_MESSAGE when upstream is too slow/unavailable.

    Jivo Bot API waits only a few seconds for webhook response. Returning a
    short valid message is better than letting n8n/Jivo timeout the request.
    """
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        payload = {}

    if payload.get("event") == "CHAT_CLOSED":
        return web.json_response({"ok": True, "event": "CHAT_CLOSED"})

    text = text or _env(
        "NMBOT_BRIDGE_FALLBACK_TEXT",
        "Запрос ещё обрабатывается, пожалуйста, не отправляйте его повторно. Я вернусь с вариантами в ближайшее время.",
    )
    return web.json_response(
        {
            "id": str(uuid.uuid4()),
            "event": "BOT_MESSAGE",
            "client_id": payload.get("client_id"),
            "chat_id": payload.get("chat_id"),
            "message": {
                "type": "TEXT",
                "text": text,
                "timestamp": int(time.time() * 1000),
            },
        }
    )


def _bridge_timeout_config() -> tuple[float, float]:
    """Return validated (status_timeout, hard_timeout) seconds.

    Status timeout is the user-visible "still working" threshold. Hard timeout
    is the bounded terminal deadline for the same upstream task.
    """
    default_status = 90.0
    default_hard = 600.0

    try:
        status_timeout = float(_env("NMBOT_BRIDGE_TIMEOUT_SECONDS", str(int(default_status))))
        if status_timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        status_timeout = default_status

    try:
        hard_timeout = float(_env("NMBOT_BRIDGE_HARD_TIMEOUT_SECONDS", str(int(default_hard))))
        if hard_timeout < status_timeout:
            raise ValueError
    except (TypeError, ValueError):
        hard_timeout = default_hard if default_hard >= status_timeout else status_timeout

    return status_timeout, hard_timeout


def _bridge_status_updates_config() -> tuple[bool, float, tuple[str, ...]]:
    enabled = _env("NMBOT_BRIDGE_STATUS_UPDATES_ENABLED").casefold() in {"1", "true", "yes", "on"}
    try:
        interval = float(_env("NMBOT_BRIDGE_STATUS_INTERVAL_SECONDS", "3"))
        if interval <= 0:
            raise ValueError
    except (TypeError, ValueError):
        interval = 3.0

    configured = tuple(
        item.strip()
        for item in _env("NMBOT_BRIDGE_STATUS_TEMPLATES").split("|")
        if item.strip()
    )
    return enabled, interval, configured or DEFAULT_STATUS_UPDATE_TEMPLATES


async def _await_upstream_with_status(
    upstream_coro,
    *,
    status_timeout: float,
    hard_timeout: float,
    send_status,
    repeat_interval: float | None = None,
) -> tuple[object | None, str, bool]:
    """Wait for upstream without cancelling it at status timeout.

    Returns (result, outcome, status_sent). `send_status` failures are isolated
    so they do not cancel the upstream task or suppress the final answer.
    """
    task = asyncio.create_task(upstream_coro)
    started = time.monotonic()
    status_sent = False
    try:
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=status_timeout)
            return result, "upstream", status_sent
        except asyncio.TimeoutError:
            try:
                sent = await send_status()
                status_sent = True if sent is None else bool(sent)
            except Exception:
                # Caller logs details inside send_status. Transport must keep
                # waiting for the original upstream task.
                pass

        while True:
            remaining = max(0.0, hard_timeout - (time.monotonic() - started))
            if remaining <= 0:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                return None, "hard_timeout", status_sent

            wait_timeout = remaining if repeat_interval is None else min(repeat_interval, remaining)
            try:
                result = await asyncio.wait_for(asyncio.shield(task), timeout=wait_timeout)
                return result, "upstream_after_status", status_sent
            except asyncio.TimeoutError:
                if repeat_interval is None or wait_timeout >= remaining:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    return None, "hard_timeout", status_sent
                try:
                    sent = await send_status()
                    status_sent = status_sent or (True if sent is None else bool(sent))
                except Exception:
                    # A status update is nonterminal and must never cancel the answer.
                    pass
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        raise
    except Exception:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        raise


async def handle_proxy(request: web.Request) -> web.Response:
    request_started = time.monotonic()
    trace_id = str(uuid.uuid4())
    expected = _env("NMBOT_N8N_BRIDGE_TOKEN")
    got = request.headers.get("X-NMBOT-Bridge-Token", "").strip()
    if not expected:
        _log_structured(trace_id, "error", outcome="bridge_token_not_configured", http_status=503)
        return web.json_response({"ok": False, "error": "bridge_token_not_configured"}, status=503)
    if not hmac.compare_digest(got, expected):
        _log_structured(trace_id, "error", outcome="unauthorized", http_status=401)
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    body = await request.read()
    trace = _request_trace(body)
    provider_token = request.match_info.get("provider_token", "").strip()
    configured_provider_token = _env("JIVO_PROVIDER_TOKEN")
    if not configured_provider_token:
        _log_structured(trace_id, "error", **trace, outcome="jivo_token_not_configured", http_status=503)
        return web.json_response({"ok": False, "error": "jivo_token_not_configured"}, status=503)
    if not hmac.compare_digest(provider_token, configured_provider_token):
        _log_structured(trace_id, "error", **trace, provider_token_ref=_safe_ref(provider_token), outcome="provider_token_mismatch", http_status=401)
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    event_key = _event_key(trace)
    event_id = _event_id(body)
    if event_key and event_id:
        LATEST_CHAT_EVENTS[event_key] = event_id

    _log_structured(
        trace_id,
        "request_received",
        **trace,
        provider_token_ref=_safe_ref(provider_token),
        http_status=None,
        outcome="accepted_for_async_processing",
    )
    trace_ref = _safe_trace_ref(trace_id)
    claimed, original_trace_ref = _claim_dispatch_event(event_key, event_id, trace_ref)
    if not claimed:
        latency_ms = int((time.monotonic() - request_started) * 1000)
        _log_structured(trace_id, "duplicate_suppressed", **trace, http_status=200, latency_ms=latency_ms, outcome="duplicate_suppressed")
        print(
            "bridge_request",
            json.dumps({**trace, "status": 200, "result": "duplicate_suppressed"}, ensure_ascii=False),
            flush=True,
        )
        # Return exactly the reference created by the first accepted callback.
        # A duplicate must never mint a safe-looking reference with no trace.
        return web.json_response({"trace_ref": original_trace_ref})
    latency_ms = int((time.monotonic() - request_started) * 1000)
    _append_delivery_trace(trace_id, "bridge_accepted", "accepted", e2e_latency_ms=latency_ms)
    _track_task(
        request.app,
        _send_to_jivo_after_bot(provider_token, body, trace, event_key, event_id, trace_id, request_started=request_started),
        trace_id,
        trace,
    )
    _log_structured(trace_id, "jivo_response_returned", **trace, http_status=200, latency_ms=latency_ms, outcome="accepted_async")
    print(
        "bridge_request",
        json.dumps({**trace, "status": 200, "result": "accepted_async"}, ensure_ascii=False),
        flush=True,
    )
    # The synchronous reply proves only that the bridge accepted the work.
    # Its opaque reference is the sole correlation value safe to return.
    return web.json_response({"trace_ref": trace_ref})


def _claim_dispatch_event(event_key: str | None, event_id: str | None, trace_ref: str) -> tuple[bool, str]:
    """Claim an inbound delivery, retaining its safe correlation reference.

    P1 bridge guard: bounded in-memory lifecycle, no payload text or tokens logged.
    """
    if not event_key or not event_id:
        return True, trace_ref
    now = time.monotonic()
    stale = [key for key, entry in DISPATCHED_CHAT_EVENTS.items() if now - entry[0] > DISPATCHED_CHAT_EVENTS_TTL_SEC]
    for key in stale:
        DISPATCHED_CHAT_EVENTS.pop(key, None)
    while len(DISPATCHED_CHAT_EVENTS) >= DISPATCHED_CHAT_EVENTS_MAX_ENTRIES:
        oldest_key = min(DISPATCHED_CHAT_EVENTS, key=lambda item: DISPATCHED_CHAT_EVENTS[item][0])
        DISPATCHED_CHAT_EVENTS.pop(oldest_key, None)
    key = f"{event_key}:{event_id}"
    if key in DISPATCHED_CHAT_EVENTS:
        return False, DISPATCHED_CHAT_EVENTS[key][1]
    DISPATCHED_CHAT_EVENTS[key] = (now, trace_ref)
    return True, trace_ref


async def _send_to_jivo_after_bot(
    provider_token: str,
    body: bytes,
    trace: dict[str, object],
    event_key: str | None,
    event_id: str | None,
    trace_id: str,
    *,
    request_started: float | None = None,
) -> None:
    """Ask local bot API, then send BOT_MESSAGE/INVITE_AGENT to Jivo API.

    This follows Jivo support guidance: acknowledge the incoming webhook fast,
    then send the actual bot event as a separate Bot-Provider -> Jivo request.
    Never logs provider_token, bridge token, API token, or message text.
    """
    upstream_base = _env("NMBOT_BRIDGE_UPSTREAM", "http://127.0.0.1:8088")
    upstream_url = upstream_base.rstrip("/") + "/jivo/" + provider_token
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-NMBOT-Trace-ID": trace_id,
    }
    api_token = _env("NMBOT_API_TOKEN")
    if api_token:
        headers["X-NMBOT-API-Token"] = api_token

    status_timeout, hard_timeout = _bridge_timeout_config()
    status_updates_enabled, status_interval, status_templates = _bridge_status_updates_config()
    if status_updates_enabled:
        status_timeout = status_interval

    terminal_delivery_recorded = False

    def _mark_terminal_delivery() -> None:
        nonlocal terminal_delivery_recorded
        terminal_delivery_recorded = True

    def _record_terminal_delivery(outcome: str, **fields: object) -> None:
        nonlocal terminal_delivery_recorded
        if terminal_delivery_recorded:
            return
        _mark_terminal_delivery()
        _append_delivery_trace(trace_id, "terminal_delivery", outcome, **fields)

    try:
        async with ClientSession(timeout=None) as session:
            upstream_started = time.monotonic()
            _log_structured(
                trace_id,
                "upstream_request_start",
                **trace,
                upstream_ref=_safe_url_ref(upstream_url),
                timeout_seconds=status_timeout,
                hard_timeout_seconds=hard_timeout,
                outcome="started",
            )

            async def _call_upstream() -> tuple[int, bytes]:
                async with session.post(upstream_url, data=body, headers=headers, timeout=hard_timeout) as response:
                    response_payload = await response.read()
                    return response.status, _normalize_jivo_response(response_payload)

            status_update_index = 0
            status_sent = False

            async def _send_status_fallback() -> bool:
                nonlocal status_update_index, status_sent
                status_text = None
                if status_updates_enabled:
                    status_text = status_templates[status_update_index % len(status_templates)]
                    status_update_index += 1
                fallback = _jivo_fast_fallback(body, "upstream_timeout", text=status_text)
                status_payload = fallback.body or b"{}"
                if _is_stale_event(event_key, event_id):
                    _log_structured(
                        trace_id,
                        "jivo_response_returned",
                        **trace,
                        http_status=None,
                        latency_ms=int((time.monotonic() - upstream_started) * 1000),
                        outcome="fallback_status_stale_skipped",
                    )
                    return False
                try:
                    status_jivo_status, status_jivo_error = await _post_event_to_jivo(
                        session,
                        provider_token,
                        status_payload,
                        trace_id,
                        trace,
                        delivery_role="status",
                    )
                    _log_structured(
                        trace_id,
                        "status_update",
                        **trace,
                        http_status=status_jivo_status,
                        latency_ms=int((time.monotonic() - upstream_started) * 1000),
                        response_event=_payload_event_kind(status_payload),
                        error_class=status_jivo_error,
                        outcome="fallback_status_sent" if status_jivo_error is None else "fallback_status_jivo_error",
                    )
                    if status_jivo_error is not None:
                        _append_bridge_error_to_journal(
                            body,
                            code="bridge_status_delivery_error",
                            stage="bridge_delivery",
                            fallback=False,
                            trace_ref=_safe_trace_ref(trace_id),
                        )
                    status_sent = status_sent or status_jivo_error is None
                    return status_jivo_error is None
                except Exception:
                    _log_structured(
                        trace_id,
                        "status_update",
                        **trace,
                        latency_ms=int((time.monotonic() - upstream_started) * 1000),
                        error_class="status_post_exception",
                        outcome="fallback_status_post_failed",
                    )
                    raise

            try:
                upstream_result_value, upstream_result, status_sent = await _await_upstream_with_status(
                    _call_upstream(),
                    status_timeout=status_timeout,
                    hard_timeout=hard_timeout,
                    send_status=_send_status_fallback,
                    repeat_interval=status_interval if status_updates_enabled else None,
                )
                if upstream_result == "hard_timeout":
                    fallback = _jivo_fast_fallback(body, "upstream_hard_timeout")
                    payload = fallback.body or b"{}"
                    upstream_status = None
                    upstream_result = "fallback_hard_timeout"
                    _append_delivery_trace(
                        trace_id,
                        "api_failed",
                        "failed",
                        error_class="hard_timeout",
                        api_latency_ms=int((time.monotonic() - upstream_started) * 1000),
                        e2e_latency_ms=int((time.monotonic() - request_started) * 1000) if request_started is not None else None,
                    )
                    _log_structured(
                        trace_id,
                        "timeout",
                        **trace,
                        latency_ms=int((time.monotonic() - upstream_started) * 1000),
                        error_class="HardTimeoutError",
                        outcome="upstream_hard_timeout",
                    )
                    _append_bridge_error_to_journal(
                        body,
                        code="bridge_hard_timeout",
                        stage="bridge_upstream",
                        fallback=not status_sent,
                        trace_ref=_safe_trace_ref(trace_id),
                    )
                else:
                    upstream_status, payload = upstream_result_value  # type: ignore[misc]
                    api_latency_ms = int((time.monotonic() - upstream_started) * 1000)
                    _append_delivery_trace(
                        trace_id,
                        "api_completed",
                        "completed" if upstream_status < 400 else "failed",
                        error_class="none" if upstream_status < 400 else "api_http_error",
                        api_status=upstream_status,
                        api_latency_ms=api_latency_ms,
                        e2e_latency_ms=int((time.monotonic() - request_started) * 1000) if request_started is not None else None,
                    )
                    _log_structured(
                        trace_id,
                        "upstream_response",
                        **trace,
                        http_status=upstream_status,
                        latency_ms=int((time.monotonic() - upstream_started) * 1000),
                        response_event=_payload_event_kind(payload),
                        response_bytes=len(payload or b""),
                        outcome=upstream_result,
                    )
            except Exception:
                _append_delivery_trace(
                    trace_id, "api_failed", "failed", error_class="api_exception",
                    api_latency_ms=int((time.monotonic() - upstream_started) * 1000),
                    e2e_latency_ms=int((time.monotonic() - request_started) * 1000) if request_started is not None else None,
                )
                if "status_sent" in locals() and status_sent:
                    _record_terminal_delivery(
                        "not_sent",
                        error_class="api_exception",
                        api_latency_ms=int((time.monotonic() - upstream_started) * 1000),
                        e2e_latency_ms=int((time.monotonic() - request_started) * 1000) if request_started is not None else None,
                    )
                    _log_structured(
                        trace_id,
                        "error",
                        **trace,
                        latency_ms=int((time.monotonic() - upstream_started) * 1000),
                        error_class="upstream_exception_after_status",
                        outcome="upstream_exception_after_status",
                    )
                    _append_bridge_error_to_journal(
                        body,
                        code="bridge_upstream_exception",
                        stage="bridge_upstream",
                        fallback=False,
                        trace_ref=_safe_trace_ref(trace_id),
                    )
                    print(
                        "bridge_async_send",
                        json.dumps(
                            {
                                **trace,
                                "upstream_status": None,
                                "upstream_result": "upstream_exception_after_status",
                                "jivo_status": None,
                                "jivo_error": "status_already_sent",
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    return
                fallback = _jivo_fast_fallback(body, "upstream_unavailable")
                payload = fallback.body or b"{}"
                upstream_status = 200
                upstream_result = "fallback_unavailable"
                _log_structured(
                    trace_id,
                    "error",
                    **trace,
                    latency_ms=int((time.monotonic() - upstream_started) * 1000),
                    error_class="upstream_exception",
                    outcome=upstream_result,
                )
                _append_bridge_error_to_journal(
                    body,
                    code="bridge_upstream_exception",
                    stage="bridge_upstream",
                    fallback=True,
                    trace_ref=_safe_trace_ref(trace_id),
                )

            terminal_event = _payload_event_kind(payload)
            is_terminal_event = terminal_event in {"BOT_MESSAGE", "INVITE_AGENT"}
            if is_terminal_event:
                _append_delivery_trace(
                    trace_id, "terminal_selected", "selected", terminal_event=terminal_event,
                    api_status=upstream_status,
                    e2e_latency_ms=int((time.monotonic() - request_started) * 1000) if request_started is not None else None,
                )
            if _is_stale_event(event_key, event_id):
                _record_terminal_delivery(
                    "not_sent",
                    terminal_event=terminal_event if is_terminal_event else "NONE", error_class="stale_event",
                    api_status=upstream_status,
                    e2e_latency_ms=int((time.monotonic() - request_started) * 1000) if request_started is not None else None,
                )
                _log_structured(trace_id, "jivo_response_returned", **trace, http_status=None, outcome="stale_event_skipped")
                print(
                    "bridge_async_send",
                    json.dumps(
                        {
                            **trace,
                            "upstream_status": upstream_status,
                            "upstream_result": upstream_result,
                            "jivo_status": None,
                            "jivo_error": "stale_event_skipped",
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return

            jivo_status, jivo_error = await _post_event_to_jivo(
                session,
                provider_token,
                payload,
                trace_id,
                trace,
                delivery_role="final",
                api_status=upstream_status,
                request_started=request_started,
                terminal_delivery_callback=_mark_terminal_delivery,
            )
            if jivo_error is not None:
                _append_bridge_error_to_journal(
                    body,
                    code="bridge_delivery_error",
                    stage="bridge_delivery",
                    fallback=upstream_result.startswith("fallback"),
                    trace_ref=_safe_trace_ref(trace_id),
                )
            print(
                "bridge_async_send",
                json.dumps(
                    {
                        **trace,
                        "upstream_status": upstream_status,
                        "upstream_result": upstream_result,
                        "jivo_status": jivo_status,
                        "jivo_error": jivo_error,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    except asyncio.CancelledError:
        _record_terminal_delivery(
            "not_sent", error_class="cancelled",
            e2e_latency_ms=int((time.monotonic() - request_started) * 1000) if request_started is not None else None,
        )
        _log_structured(trace_id, "error", **trace, error_class="cancelled", outcome="cancelled")
        raise
    except Exception:
        _record_terminal_delivery(
            "failed", error_class="jivo_exception",
            e2e_latency_ms=int((time.monotonic() - request_started) * 1000) if request_started is not None else None,
        )
        _log_structured(trace_id, "error", **trace, error_class="async_exception", outcome="async_exception")
        _append_bridge_error_to_journal(
            body,
            code="bridge_async_exception",
            stage="bridge_upstream",
            fallback=True,
            trace_ref=_safe_trace_ref(trace_id),
        )
        print(
            "bridge_async_send",
            json.dumps({**trace, "upstream_status": None, "upstream_result": "async_exception", "jivo_status": None}, ensure_ascii=False),
            flush=True,
        )


async def _post_event_to_jivo(
    session: ClientSession,
    provider_token: str,
    payload: bytes,
    trace_id: str,
    trace: dict[str, object],
    *,
    delivery_role: str = "final",
    api_status: int | None = None,
    request_started: float | None = None,
    terminal_delivery_callback=None,
) -> tuple[int | None, str | None]:
    def _record_terminal(outcome: str, **fields: object) -> None:
        _append_delivery_trace(trace_id, "terminal_delivery", outcome, **fields)
        if terminal_delivery_callback is not None:
            terminal_delivery_callback()

    provider_id = _env("JIVO_PROVIDER_ID")
    if not provider_id:
        if delivery_role == "final":
            _record_terminal("failed", error_class="provider_config", api_status=api_status)
        _log_structured(trace_id, "error", **trace, error_class="config", outcome="provider_id_missing")
        return None, "provider_id_missing"
    try:
        event = json.loads(payload.decode("utf-8")) if payload else {}
    except Exception:
        if delivery_role == "final":
            _record_terminal("failed", error_class="invalid_terminal", api_status=api_status)
        _log_structured(trace_id, "error", **trace, error_class="payload_not_json", outcome="payload_not_json")
        return None, "payload_not_json"
    if not isinstance(event, dict) or event.get("event") not in {"BOT_MESSAGE", "INVITE_AGENT", "INIT_RATE"}:
        if delivery_role == "final":
            _record_terminal("failed", error_class="invalid_terminal", api_status=api_status)
        _log_structured(trace_id, "jivo_response_returned", **trace, http_status=None, response_event=_payload_event_kind(payload), outcome="event_not_sendable")
        return None, "event_not_sendable"
    if event.get("event") == "BOT_MESSAGE":
        try:
            guarded_event, guard_result = guard_jivo_event(event)
            event = guarded_event
            payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            _log_structured(
                trace_id,
                "egress_guard",
                **trace,
                response_event="BOT_MESSAGE",
                outcome="blocked" if guard_result and guard_result.blocked else "passed",
                blocker_code=guard_result.blocker_code if guard_result and guard_result.blocked else None,
                delivery_role=delivery_role,
            )
        except Exception:
            if is_client_production():
                message = event.get("message") if isinstance(event.get("message"), dict) else {}
                event = {**event, "message": {**message, "type": "TEXT", "text": SAFE_CLIENT_FALLBACK_TEXT}}
                payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                _log_structured(trace_id, "egress_guard", **trace, response_event="BOT_MESSAGE", outcome="fail_closed", blocker_code="guard_exception", delivery_role=delivery_role)
            else:
                _log_structured(trace_id, "egress_guard", **trace, response_event="BOT_MESSAGE", outcome="guard_exception", blocker_code="guard_exception", delivery_role=delivery_role)

    endpoint_base = _env("JIVO_API_ENDPOINT_BASE", "https://bot.jivosite.com/webhooks")
    url = endpoint_base.rstrip("/") + "/" + provider_id + "/" + provider_token
    started = time.monotonic()
    event_kind = str(event.get("event") or "")
    is_terminal_event = delivery_role == "final" and event_kind in {"BOT_MESSAGE", "INVITE_AGENT"}
    # Delivery-trace v1 is a closed terminal lifecycle. Status updates retain
    # their structured-log observability below, but must not add intermediate
    # send/response records to the terminal projection.
    if delivery_role == "final":
        _append_delivery_trace(
            trace_id,
            "jivo_send_attempted",
            "attempted",
            terminal_event=event_kind if is_terminal_event else "NONE",
            api_status=api_status,
            e2e_latency_ms=int((time.monotonic() - request_started) * 1000) if request_started is not None else None,
        )
    _log_structured(
        trace_id,
        "jivo_request_start",
        **trace,
        jivo_endpoint_ref=_safe_url_ref(url),
        response_event=_payload_event_kind(payload),
        response_bytes=len(payload or b""),
        outcome="started",
    )
    response = None
    post_error = None
    try:
        async with _jivo_post_or_exception(session, url, payload) as (response, post_error):
            response_body = await response.read() if response is not None else b""
    except Exception:
        # The request opened, but consuming or closing its response failed. Log
        # the closed lifecycle record before preserving the original exception
        # flow for the caller's terminal-failure handler. Do not expose a
        # partially observed response status or exception details.
        if delivery_role == "final":
            _append_delivery_trace(
                trace_id,
                "jivo_response",
                "post_exception",
                terminal_event=event_kind if is_terminal_event else "NONE",
                error_class="jivo_exception",
                api_status=api_status,
                jivo_status=None,
                jivo_latency_ms=int((time.monotonic() - started) * 1000),
                e2e_latency_ms=int((time.monotonic() - request_started) * 1000) if request_started is not None else None,
            )
        raise

    error = post_error
    if error is None and response is not None and response.status >= 400:
        error = _safe_jivo_error(response_body)
    terminal_delivery = is_terminal_event
    # A sub-400 response proves Jivo accepted the send, not client render
    # or delivery. That requires independent Jivo-side evidence.
    delivery_status = ("status_send_accepted" if delivery_role == "status" else "terminal_send_accepted") if error is None else "jivo_error"
    jivo_latency_ms = int((time.monotonic() - started) * 1000)
    if delivery_role == "final":
        _append_delivery_trace(
            trace_id,
            "jivo_response",
            "accepted_by_jivo" if error is None else ("post_exception" if post_error else "rejected_by_jivo"),
            terminal_event=event_kind if is_terminal_event else "NONE",
            error_class="none" if error is None else ("jivo_exception" if post_error else "jivo_http_error"),
            api_status=api_status,
            jivo_status=response.status if response is not None else None,
            jivo_latency_ms=jivo_latency_ms,
            e2e_latency_ms=int((time.monotonic() - request_started) * 1000) if request_started is not None else None,
        )
    if delivery_role == "final":
        _record_terminal(
            "terminal_send_accepted" if error is None and is_terminal_event else "failed",
            terminal_event=event_kind if is_terminal_event else "NONE",
            error_class="none" if error is None and is_terminal_event else ("jivo_exception" if post_error else ("jivo_http_error" if error is not None else "invalid_terminal")),
            api_status=api_status,
            jivo_status=response.status if response is not None else None,
            jivo_latency_ms=jivo_latency_ms,
            e2e_latency_ms=int((time.monotonic() - request_started) * 1000) if request_started is not None else None,
        )
    _log_structured(
        trace_id,
        "jivo_response_returned",
        **trace,
        http_status=response.status if response is not None else None,
        latency_ms=jivo_latency_ms,
        response_event=event_kind,
        outcome="jivo_post_exception" if post_error else delivery_status,
        delivery_role=delivery_role,
        delivery_status="jivo_post_exception" if post_error else delivery_status,
        client_delivery_status="client_delivery_unconfirmed" if error is None and is_terminal_event else None,
        terminal=terminal_delivery,
        error_class=error,
    )
    return response.status if response is not None else None, error


def _safe_jivo_error(body: bytes) -> str:
    """Return only a fixed safe error class/code from Jivo response."""
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        return "non_json_error"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(error.get("code") or "").strip())[:80]
        return f"jivo_error:{code}" if code else "jivo_error"
    return "error"


def _normalize_jivo_response(body: bytes) -> bytes:
    """Keep Jivo responses strict and add required ids when missing."""
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        return body
    if not isinstance(payload, dict):
        return body
    if payload.get("event") in {"BOT_MESSAGE", "INVITE_AGENT", "INIT_RATE"} and not payload.get("id"):
        payload["id"] = str(uuid.uuid4())
    message = payload.get("message") if isinstance(payload.get("message"), dict) else None
    if message and "timestamp" in message:
        try:
            timestamp = int(message["timestamp"])
            if timestamp < 10_000_000_000:
                timestamp *= 1000
            message["timestamp"] = timestamp
        except Exception:
            message["timestamp"] = int(time.time() * 1000)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _request_trace(body: bytes) -> dict[str, object]:
    """Trace routing metadata only. Never log tokens or message text."""
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        payload = {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
    text = message.get("text") if isinstance(message.get("text"), str) else ""
    return {
        "event_id_ref": _safe_ref(payload.get("id")),
        "event": payload.get("event"),
        "site_id_ref": _safe_ref(payload.get("site_id")),
        "chat_id_ref": _safe_ref(payload.get("chat_id")),
        "client_id_ref": _safe_ref(payload.get("client_id")),
        "message_length": len(text),
        "body_bytes": len(body or b""),
        "message_type": message.get("type"),
        "channel_type": channel.get("type"),
    }


def _log_structured(trace_id: str, stage: str, **fields: object) -> None:
    """Append one safe JSONL diagnostic event. Never include tokens, text, or raw payloads."""
    record = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "trace_id": trace_id,
        "event": "nmbot_jivo_n8n_bridge",
        "stage": stage,
    }
    record.update({k: v for k, v in fields.items() if v is not None})
    try:
        STRUCTURED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with STRUCTURED_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        # Diagnostics must never break webhook handling.
        pass


def _safe_ref(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    digest = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:12]
    return f"sha256:{digest}"


def _safe_url_ref(url: str) -> str:
    # Hash the full URL; expose only scheme/host-ish prefix without path tokens.
    digest = hashlib.sha256(url.encode("utf-8", "ignore")).hexdigest()[:12]
    prefix = url.split("/", 3)[:3]
    return f"{'/'.join(prefix)}#sha256:{digest}"


def _payload_event_kind(body: bytes) -> str | None:
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        return "non_json"
    if isinstance(payload, dict):
        value = payload.get("event")
        return str(value)[:80] if value else None
    return "non_object"


def _event_key(trace: dict[str, object]) -> str | None:
    """Return a stable dialog key for stale-response protection."""
    chat_id = trace.get("chat_id_ref")
    client_id = trace.get("client_id_ref")
    if not chat_id or not client_id:
        return None
    return f"{chat_id}:{client_id}"


def _event_id(body: bytes) -> str | None:
    """Return Jivo event id when present; generated ids are not logged as secrets."""
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        return None
    event_id = payload.get("id") if isinstance(payload, dict) else None
    return str(event_id) if event_id else None


def _is_stale_event(event_key: str | None, event_id: str | None) -> bool:
    """Skip sending an old bot answer if a newer message arrived in same chat."""
    if not event_key or not event_id:
        return False
    return LATEST_CHAT_EVENTS.get(event_key) != event_id


def create_app() -> web.Application:
    app = web.Application()
    app[APP_TASKS_KEY] = set()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/jivo/{provider_token}", handle_proxy)
    app.on_cleanup.append(_cleanup_tasks)
    return app


def _track_task(app: web.Application, coro, trace_id: str, trace: dict[str, object]) -> asyncio.Task:
    tasks = app.setdefault(APP_TASKS_KEY, set())
    task = asyncio.create_task(coro)
    tasks.add(task)

    def _done(done: asyncio.Task) -> None:
        tasks.discard(done)
        try:
            exc = done.exception()
        except asyncio.CancelledError:
            _log_structured(trace_id, "task_done", **trace, outcome="cancelled")
            return
        if exc is None:
            _log_structured(trace_id, "task_done", **trace, outcome="completed")
        else:
            _log_structured(trace_id, "task_done", **trace, outcome="failed", error_class=type(exc).__name__)

    task.add_done_callback(_done)
    return task


async def _cleanup_tasks(app: web.Application) -> None:
    tasks = set(app.get(APP_TASKS_KEY, set()))
    if not tasks:
        return
    done, pending = await asyncio.wait(tasks, timeout=2.0)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    _log_structured("cleanup", "task_cleanup", completed=len(done), cancelled=len(pending), outcome="completed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=_env("NMBOT_N8N_BRIDGE_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(_env("NMBOT_N8N_BRIDGE_PORT", "8093")))
    args = parser.parse_args()
    host, port = ("0.0.0.0", 8193) if is_client_production() else (args.host, args.port)
    web.run_app(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
