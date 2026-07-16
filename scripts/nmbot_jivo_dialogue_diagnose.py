#!/usr/bin/env python3
"""Safe local dialogue diagnosis for nmbot/Jivo bridge traces.

Read-only by design: this tool reads local JSONL files, never calls Jivo/API/LLM,
and only prints allowlisted diagnostic fields.  It intentionally does not expose
raw message text, phones, URLs, tokens, payloads, client ids, or chat ids.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import nmbot_jivo_trace_analyze as trace_analyze  # noqa: E402


SENSITIVE_KEY_PARTS = (
    "payload",
    "text",
    "token",
    "authorization",
    "url",
    "body",
    "message",
    "phone",
    "client_id",
    "clientid",
    "chat_id",
    "chatid",
)

BRIDGE_EVENT_ALLOWLIST = {
    "line",
    "ts",
    "stage",
    "event",
    "status",
    "result",
    "outcome",
    "kind",
    "http_status",
    "status_code",
    "ok",
    "terminal",
    "is_terminal",
    "trace_ref",
    "turn_ref",
    "event_ref",
    "latency_ms",
    "latency_sec",
}

AUDIT_ALLOWLIST = {
    "trace_ref",
    "turn_ref",
    "session_ref",
    "ts",
    "timestamp",
    "message_len_bucket",
    "message_length_bucket",
    "message_length",
    "phone_detected",
    "phone_len",
    "phone_last4",
    "phone_ref",
    "intent",
    "search_called",
    "search_result_count",
    "result_count",
    "handoff",
    "handoff_to_operator",
    "terminal_event",
    "latency_ms",
    "latency_sec",
    "stage",
    "outcome",
    "desired",
}

CONTRACTS = {
    "bridge_transport": "accepted_async is only a webhook acknowledgement; a trace needs a later terminal Jivo delivery event.",
    "api_run_chat": "run_chat returns intents main_search, operator_request, phone_captured, or safe_upstream_fallback.",
    "jivo_adapter": "Jivo adapter returns BOT_MESSAGE or INVITE_AGENT from sanitized API result.",
    "privacy": "No raw client wording, full phone, payload, token, URL, body, client id, or chat id may be printed.",
    "diagnosis": "Do not call a bug without Actual + Contract + Desired; otherwise report evidence or coverage gap.",
}


def _parse_ts(value: Any) -> datetime | None:
    return trace_analyze._parse_ts(value)  # type: ignore[attr-defined]


def _ts_of(row: dict[str, Any]) -> datetime | None:
    return trace_analyze.ts_of(row)


def _stage_of(row: dict[str, Any]) -> str:
    return trace_analyze.stage_of(row)


def _haystack(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("stage", "event", "event_name", "name", "action", "result", "status", "type", "outcome", "kind", "intent", "terminal_event"):
        value = row.get(key)
        if isinstance(value, (str, int, float, bool)):
            parts.append(str(value).lower())
    return " ".join(parts)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in {"phone_detected", "phone_len", "phone_last4", "phone_ref"}:
        return False
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, str):
        return value[:120]
    return None


def _anon_ref(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _first_present(row: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _safe_trace_ref(trace_id: str, events: list[dict[str, Any]]) -> str:
    for row in events:
        value = _first_present(row, ("trace_ref", "safe_trace_ref"))
        if isinstance(value, (str, int, float, bool)) and not _is_sensitive_key("trace_ref"):
            return str(value)[:80]
    return _anon_ref("trace", trace_id)


def _safe_turn_refs(events: list[dict[str, Any]]) -> set[str]:
    refs: set[str] = set()
    for row in events:
        value = _first_present(row, ("turn_ref", "dialog_ref", "event_ref"))
        if isinstance(value, (str, int, float, bool)):
            refs.add(str(value)[:80])
    return refs


def _sanitize_bridge_event(row: dict[str, Any]) -> dict[str, Any]:
    event: dict[str, Any] = {"line": row.get("__line__"), "stage": _stage_of(row)}
    ts = _ts_of(row)
    if ts:
        event["ts"] = ts.isoformat()
    for key in sorted(BRIDGE_EVENT_ALLOWLIST):
        if key in {"line", "ts", "stage"} or key not in row or _is_sensitive_key(key):
            continue
        value = _safe_scalar(row.get(key))
        if value is not None:
            event[key] = value
    return {k: event[k] for k in sorted(event)}


def _sanitize_audit_record(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in sorted(AUDIT_ALLOWLIST):
        if key not in row or _is_sensitive_key(key):
            continue
        value = _safe_scalar(row.get(key))
        if value is not None:
            out[key] = value
    return out


def read_audit_jsonl(path: Path | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path is None:
        return [], []
    rows, malformed = trace_analyze.read_jsonl(path)
    return [_sanitize_audit_record(row) for row in rows], malformed


def _http_status(row: dict[str, Any]) -> int | None:
    value = _first_present(row, ("http_status", "status_code", "code"))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_failureish(row: dict[str, Any]) -> bool:
    hay = _haystack(row)
    status = _http_status(row)
    if status is not None and status >= 400:
        return True
    if row.get("ok") is False:
        return True
    return any(marker in hay for marker in ("error", "failed", "failure", "timeout", "unauthorized", "forbidden"))


def _is_success_http(row: dict[str, Any]) -> bool:
    status = _http_status(row)
    return status is not None and 200 <= status < 300


def _matching_audit_records(
    audit_rows: list[dict[str, Any]],
    *,
    trace_ref: str,
    turn_refs: set[str],
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for row in audit_rows:
        if row.get("trace_ref") == trace_ref:
            matched.append(row)
            continue
        if turn_refs and row.get("turn_ref") in turn_refs:
            matched.append(row)
    return matched


def _audit_stage(audit_rows: list[dict[str, Any]]) -> str | None:
    for row in audit_rows:
        intent = str(row.get("intent") or "").lower()
        terminal = str(row.get("terminal_event") or row.get("outcome") or "").lower()
        result_count = row.get("search_result_count", row.get("result_count"))
        if intent == "safe_upstream_fallback":
            return "api_safe_fallback"
        if intent == "operator_request" or row.get("handoff_to_operator") is True or row.get("handoff") is True:
            return "operator_handoff"
        if intent == "phone_captured" or row.get("phone_detected") is True:
            return "phone_captured"
        if "chat_closed" in terminal:
            return "chat_closed"
        if intent == "main_search":
            if "clarify" in terminal or result_count == 0:
                return "main_search_clarify"
            return "main_search"
    return None


def _classify(events: list[dict[str, Any]], audit_rows: list[dict[str, Any]]) -> tuple[str, str, str, str, bool]:
    terminals = [(idx, trace_analyze.terminal_kind(row), row) for idx, row in enumerate(events) if trace_analyze.terminal_kind(row)]
    upstream = [row for row in events if trace_analyze.is_upstream_response(row)]
    delivery_rows = [row for row in events if _stage_of(row) == "jivo_response_returned" or "jivo" in _haystack(row)]
    has_chat_closed = any("chat_closed" in _haystack(row) for row in events) or any("chat_closed" in str(row.get("terminal_event", "")).lower() for row in audit_rows)

    audit_stage = _audit_stage(audit_rows)
    upstream_fail = any(_is_failureish(row) for row in upstream)
    delivery_fail = any(_is_failureish(row) and ("jivo" in _haystack(row) or _stage_of(row) == "jivo_response_returned") for row in delivery_rows)
    terminal_failure = bool(terminals and terminals[-1][1] == "failure")
    has_terminal_success = bool(terminals and terminals[-1][1] == "success")
    upstream_success = any(_is_success_http(row) or not _is_failureish(row) for row in upstream)

    if has_chat_closed:
        return "chat_closed", "non_client_answer_terminal", "high", "No client answer expected; keep closed-chat noise separate from answer failures.", False
    if delivery_fail:
        return "transport_auth_or_http_failure", "transport_failed", "high", "Check bridge/Jivo delivery HTTP status and auth configuration from safe logs.", True
    if upstream_fail or terminal_failure:
        return "upstream_failure", "upstream_failed", "high", "Check API/run_chat upstream error path using sanitized server logs.", True
    if audit_stage in {"api_safe_fallback", "main_search_clarify", "main_search", "operator_handoff", "phone_captured"}:
        return audit_stage, "completed_with_audit", "high", "Use audit stage to inspect the named layer; do not infer raw dialogue content.", False
    if has_terminal_success:
        if not upstream:
            return "upstream_missing", "delivered_without_upstream_evidence", "medium", "Add or inspect safe upstream event evidence before judging API/run_chat.", False
        if upstream_success:
            return "delivery_complete", "bridge_to_jivo_complete", "high", "If the visible answer was wrong, collect sanitized per-turn audit evidence next.", False
    if upstream and not terminals:
        return "delivery_missing", "upstream_seen_but_no_terminal_delivery", "high", "Check bridge final Jivo POST/delivery event.", True
    if not upstream and not terminals:
        return "coverage_gap", "insufficient_bridge_evidence", "low", "Need upstream and terminal bridge events or sanitized audit correlation.", True
    return "unknown", "not_enough_safe_evidence", "low", "Gather safe bridge terminal and audit records for this trace.", False


def diagnose_rows(
    rows: list[dict[str, Any]],
    malformed: list[dict[str, Any]] | None = None,
    *,
    audit_rows: list[dict[str, Any]] | None = None,
    audit_malformed: list[dict[str, Any]] | None = None,
    trace_filter: str | None = None,
) -> dict[str, Any]:
    malformed = malformed or []
    audit_rows = audit_rows or []
    audit_malformed = audit_malformed or []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        trace_id = trace_analyze.trace_id_of(row)
        if trace_filter and trace_id != trace_filter:
            continue
        grouped[trace_id].append(row)

    traces: list[dict[str, Any]] = []
    strict_failures: list[dict[str, Any]] = []
    for item in malformed:
        strict_failures.append({"line": item.get("line"), "stage": "malformed_input", "outcome": item.get("error")})
    for item in audit_malformed:
        strict_failures.append({"line": item.get("line"), "stage": "malformed_audit_input", "outcome": item.get("error")})
    coverage_gaps: list[dict[str, Any]] = []
    for trace_id in sorted(grouped):
        events = sorted(grouped[trace_id], key=lambda r: (_ts_of(r) or datetime.min.replace(tzinfo=timezone.utc), int(r.get("__line__") or 0)))
        trace_ref = _safe_trace_ref(trace_id, events)
        turn_refs = _safe_turn_refs(events)
        matched_audit = _matching_audit_records(audit_rows, trace_ref=trace_ref, turn_refs=turn_refs)
        stage, outcome, confidence, next_check, strict_failure = _classify(events, matched_audit)
        terminal_success = any(trace_analyze.terminal_kind(row) == "success" for row in events)
        if terminal_success and not matched_audit:
            coverage_gaps.append({"trace_ref": trace_ref, "gap": "missing_sanitized_turn_audit"})
        first_ts = next((_ts_of(row) for row in events if _ts_of(row)), None)
        last_ts = next((_ts_of(row) for row in reversed(events) if _ts_of(row)), None)
        actual = {
            "bridge_events": len(events),
            "terminal_kind": next((trace_analyze.terminal_kind(row) for row in reversed(events) if trace_analyze.terminal_kind(row)), None),
            "upstream_events": sum(1 for row in events if trace_analyze.is_upstream_response(row)),
            "accepted_async_seen": any(trace_analyze.is_accepted_async(row) for row in events),
            "audit_events": len(matched_audit),
        }
        if first_ts and last_ts:
            actual["latency_sec"] = round((last_ts - first_ts).total_seconds(), 3)
        desired = next((row.get("desired") for row in matched_audit if row.get("desired") not in (None, "")), "unknown_needs_confirmation")
        report = {
            "trace_ref": trace_ref,
            "stage": stage,
            "outcome": outcome,
            "confidence": confidence,
            "actual": actual,
            "contract": CONTRACTS,
            "desired": desired,
            "next_check": next_check,
            "evidence": [_sanitize_bridge_event(row) for row in events],
            "audit": matched_audit,
        }
        traces.append(report)
        if strict_failure:
            strict_failures.append({"trace_ref": trace_ref, "stage": stage, "outcome": outcome})

    return {
        "summary": {
            "traces": len(traces),
            "events": sum(len(v) for v in grouped.values()),
            "malformed_lines": len(malformed),
            "audit_malformed_lines": len(audit_malformed),
            "strict_failures": len(strict_failures),
            "coverage_gaps": len(coverage_gaps),
        },
        "traces": traces,
        "coverage_gaps": coverage_gaps,
        "strict_failures": strict_failures,
    }


def print_human(result: dict[str, Any]) -> None:
    s = result["summary"]
    print("nmbot/Jivo dialogue diagnosis")
    print(f"Summary: traces={s['traces']} events={s['events']} strict_failures={s['strict_failures']} coverage_gaps={s['coverage_gaps']} malformed={s['malformed_lines'] + s['audit_malformed_lines']}")
    for trace in result["traces"]:
        actual = trace["actual"]
        print(f"\nTrace {trace['trace_ref']}: {trace['stage']} / {trace['outcome']} ({trace['confidence']})")
        print(f"  Actual: terminal={actual.get('terminal_kind')} upstream_events={actual.get('upstream_events')} audit_events={actual.get('audit_events')} accepted_async={actual.get('accepted_async_seen')} latency_sec={actual.get('latency_sec')}")
        print(f"  Contract: {CONTRACTS['bridge_transport']}")
        print(f"  Desired: {trace['desired']}")
        print(f"  Next: {trace['next_check']}")
        if actual.get("terminal_kind") == "success" and not trace.get("audit"):
            print("  Coverage gap: terminal bridge delivery exists, but no matching sanitized audit event was supplied.")


def self_test() -> int:
    rows = [
        {"__line__": 1, "trace_id": "raw-chat-id-must-not-print", "trace_ref": "safe-t1", "stage": "jivo_response_returned", "outcome": "accepted_async", "http_status": 200},
        {"__line__": 2, "trace_id": "raw-chat-id-must-not-print", "trace_ref": "safe-t1", "stage": "upstream_response", "http_status": 200},
        {"__line__": 3, "trace_id": "raw-chat-id-must-not-print", "trace_ref": "safe-t1", "stage": "jivo_response_returned", "outcome": "sent", "http_status": 200},
    ]
    result = diagnose_rows(rows, audit_rows=[{"trace_ref": "safe-t1", "intent": "main_search", "search_called": True, "search_result_count": 3}])
    dumped = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert result["traces"][0]["stage"] == "main_search"
    assert "raw-chat-id-must-not-print" not in dumped
    print("self-test ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose local nmbot/Jivo dialogue traces without exposing raw private data.")
    parser.add_argument("log_path", nargs="?", type=Path, help="Local bridge JSONL path")
    parser.add_argument("--audit-log", type=Path, help="Optional sanitized per-turn JSONL path")
    parser.add_argument("--trace", help="Filter by raw trace id from input; raw value is never printed")
    parser.add_argument("--last", type=int, help="Use only last N valid bridge records")
    parser.add_argument("--json", action="store_true", help="Print deterministic JSON")
    parser.add_argument("--strict", action="store_true", help="Exit 1 only for true transport/invariant failures")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.log_path:
        parser.error("log_path is required unless --self-test is used")

    rows, malformed = trace_analyze.read_jsonl(args.log_path, args.last)
    audit_rows, audit_malformed = read_audit_jsonl(args.audit_log)
    result = diagnose_rows(rows, malformed, audit_rows=audit_rows, audit_malformed=audit_malformed, trace_filter=args.trace)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(result)
    return 1 if args.strict and result["summary"]["strict_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
