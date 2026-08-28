#!/usr/bin/env python3
"""Authenticated V6 Jivo smoke with an explicit, release-bound target."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ENV_PATH = Path("/home/neiro/novostroy-bot/.env")
PRIMARY_JOURNAL_PATH = Path("/home/neiro/novostroy-bot/logs/dialogue_journal.jsonl")
CANONICAL_TEST_ROOT = Path("/home/neiro/.local/state/nmbot-v6-release")
CANONICAL_TEST_ROUTE = CANONICAL_TEST_ROOT / "routes" / "test.json"
CANONICAL_TEST_DATA_ROOT = CANONICAL_TEST_ROOT / "profiles" / "test" / "data"
BRIDGE_LOG_PATH = Path("/home/neiro/novostroy-bot/logs/n8n_bridge_structured.jsonl")
BRIDGE_LOG_DEFAULT = Path("/home/neiro/.local/state/nmbot-v6/prod/bridge/n8n_bridge_structured.jsonl")
PRIMARY_API_BASE = "http://127.0.0.1:8088"
CANONICAL_TEST_UPSTREAMS = {"http://127.0.0.1:18088", "http://127.0.0.1:18089"}
BRIDGE_BASE = "http://127.0.0.1:8093"
SAFE_RELEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
MAX_NEW_LOG_BYTES = 2 * 1024 * 1024


class SmokeError(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetContract:
    name: str
    api_base: str
    profile: str
    journal: Path
    journal_root: Path
    expected_release: str | None


def _env() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _release_id(value: str | None, *, required: bool) -> str | None:
    release_id = str(value or "").strip()
    if not release_id:
        if required:
            raise SmokeError("expected_release_required")
        return None
    if not SAFE_RELEASE_RE.fullmatch(release_id) or release_id in {".", "..", "UNKNOWN"}:
        raise SmokeError("expected_release_invalid")
    return release_id


def _canonical_test_target(expected_release: str) -> tuple[str, str]:
    try:
        details = CANONICAL_TEST_ROUTE.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise SmokeError("test_route_unsafe")
        route = json.loads(CANONICAL_TEST_ROUTE.read_text(encoding="utf-8"))
    except SmokeError:
        raise
    except Exception as exc:
        raise SmokeError("test_route_unreadable") from exc
    active = route.get("active") if isinstance(route, dict) else None
    if (
        not isinstance(route, dict)
        or route.get("schema") != "nmbot.active_route.v1"
        or route.get("profile") != "TEST"
        or not isinstance(active, dict)
        or set(active) != {"slot", "release_id", "upstream"}
        or active.get("slot") not in {"A", "B"}
        or active.get("release_id") != expected_release
        or active.get("upstream") not in CANONICAL_TEST_UPSTREAMS
    ):
        raise SmokeError("test_route_invalid")
    return str(active["upstream"]), str(active["slot"])


def target_contract(target: str, expected_release: str | None) -> TargetContract:
    if target == "primary":
        return TargetContract(
            name=target,
            api_base=PRIMARY_API_BASE,
            profile="PROD",
            journal=PRIMARY_JOURNAL_PATH,
            journal_root=PRIMARY_JOURNAL_PATH.parent,
            expected_release=_release_id(expected_release, required=False),
        )
    if target == "isolated-test":
        release_id = _release_id(expected_release, required=True)
        assert release_id is not None
        api_base, _slot = _canonical_test_target(release_id)
        return TargetContract(
            name=target,
            api_base=api_base,
            profile="TEST",
            journal=CANONICAL_TEST_DATA_ROOT / "dialogue" / "dialogue.jsonl",
            journal_root=CANONICAL_TEST_DATA_ROOT,
            expected_release=release_id,
        )
    raise SmokeError("target_not_allowed")


def _bridge_base(value: str) -> str:
    if str(value or "").strip().rstrip("/") != BRIDGE_BASE:
        raise SmokeError("bridge_base_not_allowed")
    return BRIDGE_BASE


def _bridge_log_paths(values: dict[str, str]) -> tuple[Path, ...]:
    configured = str(values.get("NMBOT_BRIDGE_STRUCTURED_LOG") or "").strip()
    configured_path = Path(configured).expanduser() if configured else None
    if configured_path is not None and configured_path not in {BRIDGE_LOG_PATH, BRIDGE_LOG_DEFAULT}:
        raise SmokeError("bridge_log_path_not_allowed")
    candidates = (configured_path, BRIDGE_LOG_PATH, BRIDGE_LOG_DEFAULT)
    return tuple(dict.fromkeys(path for path in candidates if path is not None))


def _health(contract: TargetContract, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(contract.api_base + "/health", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=min(timeout, 15.0)) as response:
            body = json.loads(response.read(65536).decode("utf-8"))
            status = int(response.status)
    except Exception as exc:
        raise SmokeError("target_health_unavailable") from exc
    if not isinstance(body, dict):
        raise SmokeError("target_health_invalid")
    observed_release = str(body.get("release_id") or "").strip()
    verified = (
        status == 200
        and body.get("ok") is True
        and body.get("runtime") == "V6"
        and body.get("profile") == contract.profile
        and (contract.expected_release is None or observed_release == contract.expected_release)
    )
    if not verified:
        raise SmokeError("target_identity_mismatch")
    return {
        "target": contract.name,
        "runtime": "V6",
        "profile": contract.profile,
        "release_id": observed_release or None,
        "verified": True,
    }


def _send(
    *,
    base_url: str,
    provider_token: str,
    bridge_token: str,
    text: str,
    base_payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    event_id = str(uuid.uuid4())
    payload = {
        **base_payload,
        "event": "CLIENT_MESSAGE",
        "id": event_id,
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
        return {"http_status": int(exc.code), "ok": False, "error": "http_error", "event_id_ref": _bridge_ref(event_id)}
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        body = {}
    return {
        "http_status": status,
        "ok": status < 400,
        "event": body.get("event") if isinstance(body, dict) else None,
        "accepted": body.get("accepted") if isinstance(body, dict) else None,
        "event_id_ref": _bridge_ref(event_id),
    }


def _chat_ref(chat_id: str) -> str:
    return "chat_" + hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:20]


def _bridge_ref(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _safe_url_ref(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"{'/'.join(url.split('/', 3)[:3])}#sha256:{digest}"


def _regular_file(path: Path, *, root: Path | None = None, allow_missing: bool = False) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return False
        raise SmokeError("evidence_file_missing")
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise SmokeError("evidence_file_unsafe")
    if root is not None:
        try:
            resolved_root = root.resolve(strict=True)
            resolved_path = path.resolve(strict=True)
        except OSError as exc:
            raise SmokeError("evidence_path_unresolved") from exc
        if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
            raise SmokeError("evidence_path_outside_target")
    return True


def _file_offset(path: Path, *, root: Path | None = None) -> int:
    if not _regular_file(path, root=root, allow_missing=True):
        return 0
    return path.stat().st_size


def _new_lines(path: Path, *, offset: int, root: Path | None = None) -> list[str]:
    if not _regular_file(path, root=root, allow_missing=True):
        return []
    size = path.stat().st_size
    if offset < 0 or offset > size:
        raise SmokeError("evidence_log_rotated")
    with path.open("rb") as handle:
        handle.seek(offset)
        payload = handle.read(MAX_NEW_LOG_BYTES + 1)
    if len(payload) > MAX_NEW_LOG_BYTES:
        raise SmokeError("evidence_window_too_large")
    return payload.decode("utf-8", errors="replace").splitlines()


def _read_chat_events(chat_id: str, *, journal: Path, offset: int = 0, root: Path | None = None) -> list[dict[str, Any]]:
    ref = _chat_ref(chat_id)
    events: list[dict[str, Any]] = []
    for raw in _new_lines(journal, offset=offset, root=root):
        try:
            event = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(event, dict) and event.get("chat_ref") == ref:
            events.append(event)
    return events


def _read_bridge_events(*, event_id_ref: str, chat_id: str, offset: int, path: Path = BRIDGE_LOG_PATH) -> list[dict[str, Any]]:
    chat_ref = _bridge_ref(chat_id)
    events: list[dict[str, Any]] = []
    for raw in _new_lines(path, offset=offset, root=path.parent):
        try:
            event = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            isinstance(event, dict)
            and event.get("event_id_ref") == event_id_ref
            and event.get("chat_id_ref") == chat_ref
        ):
            events.append(event)
    return events


def _v6_simple_trace_accepted(event: dict[str, Any]) -> bool:
    diagnostic = event.get("runtime_diagnostic") if isinstance(event.get("runtime_diagnostic"), dict) else {}
    if diagnostic.get("status") != "completed" or diagnostic.get("state_commit") is not True:
        return False
    trace = diagnostic.get("trace") if isinstance(diagnostic.get("trace"), dict) else {}
    stages = trace.get("stages") if isinstance(trace.get("stages"), list) else []
    statuses = {item.get("stage"): item.get("status") for item in stages if isinstance(item, dict)}
    normal = all(statuses.get(stage) == expected for stage, expected in {
        "prompt1": "accepted", "prompt2": "accepted", "state": "accepted", "bot_message": "prepared",
    }.items())
    url_card = trace.get("url_card") if isinstance(trace.get("url_card"), dict) else {}
    direct = (
        url_card.get("status") == "accepted"
        and statuses.get("prompt1") == "not_called"
        and statuses.get("mcp") == "not_called"
        and statuses.get("prompt2") == "accepted"
        and statuses.get("state") == "accepted"
        and statuses.get("bot_message") == "prepared"
    )
    return normal or direct


def _is_query_bot_event(event: dict[str, Any]) -> bool:
    return event.get("role") == "bot" and isinstance(event.get("runtime_diagnostic"), dict)


def evaluate_release_smoke(*, query_result: dict[str, Any], events: list[dict[str, Any]], expected_release: str) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if not query_result.get("ok") or int(query_result.get("http_status") or 0) >= 400:
        failures.append("query_http")
    bot_events = [event for event in events if event.get("role") == "bot"]
    if not bot_events:
        return False, [*failures, "missing_bot_event"]
    latest = bot_events[-1]
    if latest.get("release_id") != expected_release:
        failures.append("journal_release_mismatch")
    if not _v6_simple_trace_accepted(latest):
        failures.append("runtime_not_accepted")
    error_summary = latest.get("error_summary") if isinstance(latest.get("error_summary"), dict) else {}
    if error_summary.get("status") in {"failed", "degraded"}:
        failures.append("runtime_error")
    return not failures, failures


def evaluate_bridge_trace(*, events: list[dict[str, Any]], expected_upstream_ref: str) -> tuple[bool, list[str], dict[str, Any]]:
    traces: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        trace_id = str(event.get("trace_id") or "")
        if trace_id:
            traces.setdefault(trace_id, []).append(event)
    selected_id = ""
    selected: list[dict[str, Any]] = []
    for trace_id, rows in traces.items():
        if any(row.get("stage") == "upstream_request_start" and row.get("upstream_ref") == expected_upstream_ref for row in rows):
            selected_id, selected = trace_id, rows
            break
    if not selected:
        return False, ["bridge_target_not_proven"], {"accepted": False}
    upstream = next((row for row in selected if row.get("stage") == "upstream_response" and int(row.get("http_status") or 0) == 200 and row.get("response_event") == "BOT_MESSAGE"), None)
    guard = next((row for row in selected if row.get("stage") == "egress_guard" and row.get("outcome") == "passed" and row.get("delivery_role", "final") == "final"), None)
    terminal = next((row for row in selected if row.get("stage") == "jivo_response_returned" and int(row.get("http_status") or 0) == 200 and row.get("response_event") == "BOT_MESSAGE" and (row.get("outcome") == "terminal_send_accepted" or (row.get("terminal") is True and row.get("delivery_role") == "final" and row.get("delivery_status") == "sent"))), None)
    failures = []
    if upstream is None:
        failures.append("bridge_upstream_response_missing")
    if guard is None:
        failures.append("bridge_egress_not_proven")
    if terminal is None:
        failures.append("terminal_jivo_send_missing")
    receipt = {
        "accepted": not failures,
        "trace_ref": _bridge_ref(selected_id),
        "target_upstream_verified": True,
        "upstream_http_status": upstream.get("http_status") if upstream else None,
        "egress_guard_passed": guard is not None,
        "terminal_http_status": terminal.get("http_status") if terminal else None,
    }
    return not failures, failures, receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="двушка в мкр Люблино")
    parser.add_argument("--base-url", default=BRIDGE_BASE)
    parser.add_argument("--target", choices=("primary", "isolated-test"), required=True)
    parser.add_argument("--expected-release", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--start-settle-seconds", type=float, default=3.0)
    parser.add_argument("--journal-wait-seconds", type=float, default=15.0)
    parser.add_argument("--require-accepted", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        contract = target_contract(args.target, args.expected_release)
        bridge_base = _bridge_base(args.base_url)
        target_health = _health(contract, args.timeout)
        values = _env()
        bridge_log_paths = _bridge_log_paths(values)
        provider_token = values.get("JIVO_PROVIDER_TOKEN", "").strip()
        bridge_token = values.get("NMBOT_N8N_BRIDGE_TOKEN", "").strip()
        if not provider_token or not bridge_token:
            raise SmokeError("required_token_shape_missing")
        suffix = uuid.uuid4().hex[:12]
        base_payload = {
            "site_id": f"test-only-v6-site-{suffix}",
            "chat_id": f"test-only-v6-chat-{suffix}",
            "client_id": f"test-only-v6-client-{suffix}",
            "agents_online": True,
            "sender": {"id": f"test-only-v6-sender-{suffix}", "name": "Тестовый клиент V6", "has_contacts": False},
            "channel": {"id": f"test-only-v6-channel-{suffix}", "type": "widget"},
        }
        try:
            start = _send(base_url=bridge_base, provider_token=provider_token, bridge_token=bridge_token, text="/start_6", base_payload=base_payload, timeout=args.timeout)
            if not start.get("ok"):
                print(json.dumps({"target": target_health, "start": start, "query": None}, ensure_ascii=False, sort_keys=True))
                return 2
            time.sleep(max(0.0, args.start_settle_seconds))
            journal_offset = _file_offset(contract.journal, root=contract.journal_root)
            bridge_offsets = {path: _file_offset(path, root=path.parent) for path in bridge_log_paths}
            query = _send(base_url=bridge_base, provider_token=provider_token, bridge_token=bridge_token, text=args.query, base_payload=base_payload, timeout=args.timeout)
            events: list[dict[str, Any]] = []
            bridge_events: list[dict[str, Any]] = []
            bridge_receipt: dict[str, Any] = {"accepted": False}
            failures: list[str] = []
            accepted = bool(start.get("ok") and query.get("ok"))
            if args.require_accepted:
                expected_upstream_ref = _safe_url_ref(contract.api_base + "/jivo/" + provider_token)
                deadline = time.monotonic() + max(0.0, args.journal_wait_seconds)
                while time.monotonic() < deadline:
                    events = _read_chat_events(base_payload["chat_id"], journal=contract.journal, offset=journal_offset, root=contract.journal_root)
                    bridge_events = [
                        event
                        for path in bridge_log_paths
                        for event in _read_bridge_events(
                            event_id_ref=str(query.get("event_id_ref") or ""),
                            chat_id=base_payload["chat_id"],
                            offset=bridge_offsets[path],
                            path=path,
                        )
                    ]
                    journal_ready = any(_is_query_bot_event(event) for event in events)
                    bridge_ready, _, bridge_receipt = evaluate_bridge_trace(events=bridge_events, expected_upstream_ref=expected_upstream_ref)
                    if journal_ready and bridge_ready:
                        break
                    time.sleep(0.5)
                observed_release = str(target_health.get("release_id") or "")
                journal_ok, journal_failures = evaluate_release_smoke(query_result=query, events=events, expected_release=observed_release)
                bridge_ok, bridge_failures, bridge_receipt = evaluate_bridge_trace(events=bridge_events, expected_upstream_ref=expected_upstream_ref)
                failures = [*journal_failures, *bridge_failures]
                accepted = journal_ok and bridge_ok
            print(json.dumps({
                "target": target_health,
                "start": start,
                "query": query,
                "query_text_length": len(args.query),
                "journal_event_count": len(events),
                "bridge_event_count": len(bridge_events),
                "bridge_trace": bridge_receipt,
                "release_gate": {"accepted": accepted, "failures": failures},
            }, ensure_ascii=False, sort_keys=True))
            return 0 if accepted and start.get("ok") else 2
        finally:
            pass
    except SmokeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
