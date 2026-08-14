#!/usr/bin/env python3
"""TEST-only authenticated V6 Jivo smoke runner.

Run on the Jivo VPS. Sends /start_6 first so the session uses V6, then one
ordinary message. Tokens are read locally and never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ENV_PATH = Path("/home/neiro/novostroy-bot/.env")
JOURNAL_PATH = Path("/home/neiro/novostroy-bot/logs/dialogue_journal.jsonl")


def _env() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _send(
    *,
    base_url: str,
    provider_token: str,
    bridge_token: str,
    text: str,
    base_payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    payload = {
        **base_payload,
        "event": "CLIENT_MESSAGE",
        "id": str(uuid.uuid4()),
        "message": {"type": "TEXT", "text": text, "timestamp": int(time.time() * 1000)},
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/jivo/{provider_token}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-NMBOT-Bridge-Token": bridge_token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        return {"http_status": int(exc.code), "ok": False, "error": "http_error"}
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        body = {}
    return {
        "http_status": status,
        "ok": status < 400,
        "event": body.get("event") if isinstance(body, dict) else None,
        "accepted": body.get("accepted") if isinstance(body, dict) else None,
    }


def _runtime_request(api_token: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:8088/api/runtime-version",
        data=body,
        headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("runtime selector returned invalid JSON")
    return value


def _chat_ref(chat_id: str) -> str:
    return "sha256:" + hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:16]


def _read_chat_events(chat_id: str, *, journal: Path = JOURNAL_PATH) -> list[dict[str, Any]]:
    """Read only bounded, already-sanitized journal events for this smoke chat."""
    if not journal.exists():
        return []
    ref = _chat_ref(chat_id)
    events: list[dict[str, Any]] = []
    for raw in journal.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(event, dict) and event.get("chat_id_ref") == ref:
            events.append(event)
    return events


def _v6_simple_trace_accepted(event: dict[str, Any]) -> bool:
    trace = event.get("v6_trace") if isinstance(event.get("v6_trace"), dict) else {}
    stages = trace.get("stages") if isinstance(trace.get("stages"), list) else []
    statuses = {
        item.get("stage"): item.get("status")
        for item in stages
        if isinstance(item, dict) and isinstance(item.get("stage"), str)
    }
    normal_route = all(
        statuses.get(stage) == expected
        for stage, expected in {
            "prompt1": "accepted",
            "prompt2": "accepted",
            "state": "accepted",
            "bot_message": "returned",
        }.items()
    )
    url_card = trace.get("url_card") if isinstance(trace.get("url_card"), dict) else {}
    direct_url_route = (
        url_card.get("status") == "accepted"
        and statuses.get("prompt1") == "not_called"
        and statuses.get("mcp") == "not_called"
        and statuses.get("prompt2") == "accepted"
        and statuses.get("state") == "accepted"
        and statuses.get("bot_message") == "returned"
    )
    return normal_route or direct_url_route


def _is_query_bot_event(event: dict[str, Any]) -> bool:
    if event.get("role") != "bot":
        return False
    if event.get("answer_kind") == "v6":
        return True
    response_model = event.get("response_model") if isinstance(event.get("response_model"), dict) else {}
    return bool(response_model) or _v6_simple_trace_accepted(event)


def evaluate_release_smoke(*, query_result: dict[str, Any], events: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Require an accepted published result, not only fallback delivery."""
    failures: list[str] = []
    if not query_result.get("ok") or int(query_result.get("http_status") or 0) >= 400:
        failures.append("query_http")
    bot_events = [event for event in events if event.get("role") == "bot"]
    if not bot_events:
        return False, ["missing_bot_event"]
    latest = bot_events[-1]
    response_model = latest.get("response_model") if isinstance(latest.get("response_model"), dict) else {}
    accepted = (response_model.get("status") == "valid" and response_model.get("published") is True) or _v6_simple_trace_accepted(latest)
    if not accepted:
        failures.append("runtime_not_accepted")
    composer = latest.get("response_composer") if isinstance(latest.get("response_composer"), dict) else {}
    if composer.get("fallback_reason"):
        failures.append("composer_fallback")
    error_summary = latest.get("error_summary") if isinstance(latest.get("error_summary"), dict) else {}
    if error_summary.get("status") in {"failed", "degraded"}:
        failures.append("runtime_error")
    runtime_summary = latest.get("runtime_summary") if isinstance(latest.get("runtime_summary"), dict) else {}
    blockers = runtime_summary.get("quality_blockers") if isinstance(runtime_summary.get("quality_blockers"), list) else []
    if blockers:
        failures.append("quality_blocker")
    return not failures, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Authenticated V6 Jivo smoke")
    parser.add_argument("--query", default="двушка в мкр Люблино")
    parser.add_argument("--base-url", default="http://127.0.0.1:8093")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--start-settle-seconds", type=float, default=3.0)
    parser.add_argument("--journal-wait-seconds", type=float, default=15.0)
    parser.add_argument("--require-accepted", action="store_true", help="fail unless the journal proves an accepted published result")
    parser.add_argument("--activate-v6", action="store_true", help="temporarily set active selector to V6 and restore it")
    args = parser.parse_args()

    values = _env()
    provider_token = values.get("JIVO_PROVIDER_TOKEN", "").strip()
    bridge_token = values.get("NMBOT_N8N_BRIDGE_TOKEN", "").strip()
    api_token = values.get("NMBOT_API_TOKEN", "").strip()
    if not provider_token or not bridge_token or (args.activate_v6 and not api_token):
        raise SystemExit("required token shape missing")

    suffix = uuid.uuid4().hex[:12]
    base_payload = {
        "site_id": f"test-only-v6-site-{suffix}",
        "chat_id": f"test-only-v6-chat-{suffix}",
        "client_id": f"test-only-v6-client-{suffix}",
        "agents_online": True,
        "sender": {"id": f"test-only-v6-sender-{suffix}", "name": "Тестовый клиент V6", "has_contacts": False},
        "channel": {"id": f"test-only-v6-channel-{suffix}", "type": "widget"},
    }
    previous_runtime = None
    try:
        if args.activate_v6:
            current = _runtime_request(api_token, "GET")
            previous_runtime = str(current.get("runtime_version") or "").strip().upper()
            if not previous_runtime:
                raise RuntimeError("active runtime selector is empty")
            _runtime_request(api_token, "POST", {"runtime_version": "V6"})
        start = _send(
            base_url=args.base_url,
            provider_token=provider_token,
            bridge_token=bridge_token,
            text="/start_6",
            base_payload=base_payload,
            timeout=args.timeout,
        )
        if not start.get("ok"):
            print(json.dumps({"start": start, "query": None}, ensure_ascii=False, sort_keys=True))
            return 2
        time.sleep(max(0.0, args.start_settle_seconds))
        query = _send(
            base_url=args.base_url,
            provider_token=provider_token,
            bridge_token=bridge_token,
            text=args.query,
            base_payload=base_payload,
            timeout=args.timeout,
        )
        events: list[dict[str, Any]] = []
        if args.require_accepted:
            deadline = time.monotonic() + max(0.0, args.journal_wait_seconds)
            while time.monotonic() < deadline:
                events = _read_chat_events(base_payload["chat_id"])
                if any(_is_query_bot_event(event) for event in events):
                    break
                time.sleep(0.5)
            accepted, failures = evaluate_release_smoke(query_result=query, events=events)
        else:
            accepted, failures = bool(start.get("ok") and query.get("ok")), []
        print(json.dumps({"start": start, "query": query, "query_text_length": len(args.query), "activated_v6": bool(args.activate_v6), "previous_runtime": previous_runtime, "journal_event_count": len(events), "release_gate": {"accepted": accepted, "failures": failures}}, ensure_ascii=False, sort_keys=True))
        return 0 if accepted and start.get("ok") else 2
    finally:
        if previous_runtime:
            _runtime_request(api_token, "POST", {"runtime_version": previous_runtime})


if __name__ == "__main__":
    raise SystemExit(main())
