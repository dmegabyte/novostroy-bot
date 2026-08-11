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
import re
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
    "conversation_ref",
    "session_key_ref",
    "event_id_ref",
    "ts",
    "timestamp",
    "answer_kind",
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

RUNTIME_QUALITY_BLOCKERS = {
    "runtime_error",
    "question_count_not_one",
    "final_question_not_at_end",
    "search_without_cards",
    "enrichment_error",
}

V6_PAYLOAD_STAGES = frozenset({"v6_search_agent", "v6_answer_writer"})
V6_PARSE_STATUSES = frozenset({"ok", "invalid_json", "missing"})
V6_VALIDATOR_STATUSES = frozenset({"ok", "contract_violation", "missing"})

CANONICAL_TRACE_REF_RE = re.compile(r"^trace_[0-9a-f]{12}$")

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


def _canonical_trace_ref(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if CANONICAL_TRACE_REF_RE.fullmatch(text) else None


def _trace_ref_for_row(row: dict[str, Any]) -> str | None:
    supplied = _canonical_trace_ref(_first_present(row, ("trace_ref", "safe_trace_ref")))
    if supplied:
        return supplied
    raw_trace_id = _first_present(row, ("trace_id", "traceId", "id"))
    if raw_trace_id not in (None, ""):
        return _anon_ref("trace", raw_trace_id)
    return None


def _first_present(row: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _safe_trace_ref(trace_id: str, events: list[dict[str, Any]]) -> str:
    for row in events:
        value = _canonical_trace_ref(_first_present(row, ("trace_ref", "safe_trace_ref")))
        if value:
            return value
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
        if key == "trace_ref":
            value = _trace_ref_for_row(row)
            if value is not None:
                event[key] = value
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
        if key == "trace_ref":
            value = _canonical_trace_ref(row.get(key))
            if value is not None:
                out[key] = value
            continue
        value = _safe_scalar(row.get(key))
        if value is not None:
            out[key] = value
    runtime_summary = _sanitize_runtime_summary(row.get("runtime_summary"))
    if runtime_summary:
        out["runtime_summary"] = runtime_summary
    return out


def _sanitize_runtime_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    stage = _safe_token(value.get("stage"))
    action = _safe_token(value.get("action"))
    if not stage or not action:
        return {}
    return {
        "stage": stage,
        "action": action,
        "answer_kind": _safe_token(value.get("answer_kind")),
        "call_counts": _safe_call_counts(value.get("call_counts")),
        "state_before": _safe_runtime_state(value.get("state_before")),
        "state_after": _safe_runtime_state(value.get("state_after")),
        "timing_ms": _safe_timing(value.get("timing_ms")),
        "question_count": _bounded_int(value.get("question_count"), 0, 20),
        "final_question_at_end": bool(value.get("final_question_at_end")),
        "quality_blockers": [str(item) for item in (value.get("quality_blockers") if isinstance(value.get("quality_blockers"), list) else []) if str(item) in RUNTIME_QUALITY_BLOCKERS][:5],
        "grounding_scope": "canonical_response_plan" if value.get("grounding_scope") == "canonical_response_plan" else None,
        "gateway_attempt_details": _safe_gateway_attempt_details(value.get("gateway_attempt_details")),
    }


def _safe_gateway_attempt_details(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        attempt: dict[str, Any] = {}
        if _safe_token(item.get("stage")) == "gateway_attempt":
            attempt["stage"] = "gateway_attempt"
        model = _safe_token(item.get("model"))
        if model:
            attempt["model"] = model
        for key in ("ok", "empty", "safe"):
            if isinstance(item.get(key), bool):
                attempt[key] = bool(item.get(key))
        task_id = _safe_token(item.get("gateway_task_id"))
        if task_id:
            attempt["gateway_task_id_present"] = True
        payload_stage = _safe_token(item.get("_payload_stage"))
        if payload_stage in V6_PAYLOAD_STAGES:
            attempt["payload_stage_present"] = True
            attempt["stage_owner"] = (
                "prompt1_contract" if payload_stage == "v6_search_agent" else "prompt2_contract"
            )
        provider_status = _bounded_optional_int(item.get("provider_status_code"), 100, 599)
        if provider_status is not None:
            attempt["provider_status_code"] = provider_status
        attempt["duration_ms"] = _bounded_int(item.get("duration_ms"), 0, 10 * 60 * 1000)
        parse_status = str(item.get("parse_status") or "").strip()
        if parse_status in V6_PARSE_STATUSES:
            attempt["parse_status"] = parse_status
        validator_status = str(item.get("validator_status") or "").strip()
        if validator_status in V6_VALIDATOR_STATUSES:
            attempt["validator_status"] = validator_status
        if attempt:
            out.append(attempt)
    return out


def _safe_token(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    safe = "".join(ch if ch.isalnum() or ch in "_.:-" else "_" for ch in text)
    return safe[:80]


def _bounded_int(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(number, high))


def _bounded_optional_int(value: Any, low: int, high: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def _safe_timing(value: Any) -> dict[str, int]:
    timing = value if isinstance(value, dict) else {}
    return {key: _bounded_int(timing.get(key), 0, 10 * 60 * 1000) for key in ("planner", "execution", "response", "total")}


def _safe_call_counts(value: Any) -> dict[str, int]:
    counts = value if isinstance(value, dict) else {}
    return {
        "planner": _bounded_int(counts.get("planner"), 0, 3),
        "search": _bounded_int(counts.get("search"), 0, 1),
        "selected_enrichment": _bounded_int(counts.get("selected_enrichment"), 0, 1),
        "gateway_attempts": _bounded_int(counts.get("gateway_attempts"), 0, 5),
    }


def _safe_runtime_state(value: Any) -> dict[str, Any]:
    state = value if isinstance(value, dict) else {}
    raw_keys = state.get("param_keys") if isinstance(state.get("param_keys"), list) else []
    return {
        "param_keys": sorted(dict.fromkeys(key for key in (_safe_param_key(item) for item in raw_keys) if key))[:20],
        "visible_options_count": _bounded_int(state.get("visible_options_count"), 0, 20),
        "selected_present": bool(state.get("selected_present")),
        "pending_followup": _safe_token(state.get("pending_followup")),
        "active_topic": _safe_token(state.get("active_topic")),
    }


def _safe_param_key(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if any(part in text for part in ("phone", "тел", "email", "client", "chat", "token", "secret", "+7", "7999")):
        return None
    return _safe_token(text)


def read_audit_jsonl(path: Path | None, last: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path is None:
        return [], []
    rows, malformed = trace_analyze.read_jsonl(path, last)
    return [_sanitize_audit_record(row) for row in rows], malformed


def diagnose_audit_only(audit_rows: list[dict[str, Any]], audit_malformed: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    audit_malformed = audit_malformed or []
    turns: list[dict[str, Any]] = []
    call_totals = {"planner": 0, "search": 0, "selected_enrichment": 0, "gateway_attempts": 0}
    blocker_totals: dict[str, int] = {key: 0 for key in sorted(RUNTIME_QUALITY_BLOCKERS)}
    timings: dict[str, list[int]] = {key: [] for key in ("planner", "execution", "response", "total")}

    for row in audit_rows:
        summary = row.get("runtime_summary") if isinstance(row.get("runtime_summary"), dict) else None
        if not summary:
            continue
        actual = _runtime_actual([row])
        refs = {
            key: row.get(key)
            for key in ("conversation_ref", "session_key_ref", "event_id_ref", "trace_ref", "turn_ref", "session_ref")
            if isinstance(row.get(key), str) and row.get(key)
        }
        turn = {
            "refs": refs,
            "ts": row.get("ts") or row.get("timestamp"),
            "answer_kind": _safe_token(row.get("answer_kind")),
            "actual": actual,
        }
        turns.append(turn)
        counts = actual.get("runtime_call_counts") if isinstance(actual.get("runtime_call_counts"), dict) else {}
        for key in call_totals:
            call_totals[key] += _bounded_int(counts.get(key), 0, 5 if key == "gateway_attempts" else 3)
        timing = actual.get("runtime_timing_ms") if isinstance(actual.get("runtime_timing_ms"), dict) else {}
        for key in timings:
            value = timing.get(key)
            if isinstance(value, int):
                timings[key].append(value)
        for blocker in actual.get("runtime_quality_blockers") if isinstance(actual.get("runtime_quality_blockers"), list) else []:
            if str(blocker) in blocker_totals:
                blocker_totals[str(blocker)] += 1

    return {
        "mode": "audit_only",
        "summary": {
            "audit_records": len(audit_rows),
            "turns": len(turns),
            "audit_malformed_lines": len(audit_malformed),
            "call_totals": call_totals,
            "blocker_totals": {key: value for key, value in blocker_totals.items() if value},
            "timing_ms": {key: _percentiles(values) for key, values in timings.items() if values},
        },
        "turns": turns,
    }


def _percentiles(values: list[int]) -> dict[str, int]:
    ordered = sorted(values)
    return {"p50": _nearest_rank(ordered, 0.50), "p95": _nearest_rank(ordered, 0.95)}


def _nearest_rank(ordered: list[int], percentile: float) -> int:
    if not ordered:
        return 0
    index = max(0, min(len(ordered) - 1, int((len(ordered) * percentile + 0.999999) - 1)))
    return ordered[index]


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


def _runtime_actual(audit_rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = next((row.get("runtime_summary") for row in audit_rows if isinstance(row.get("runtime_summary"), dict)), None)
    if not isinstance(summary, dict):
        return {}
    attempts = summary.get("gateway_attempt_details") if isinstance(summary.get("gateway_attempt_details"), list) else []
    actual = {
        "runtime_stage": summary.get("stage"),
        "runtime_action": summary.get("action"),
        "runtime_answer_kind": summary.get("answer_kind"),
        "runtime_call_counts": summary.get("call_counts"),
        "runtime_state_before": summary.get("state_before"),
        "runtime_state_after": summary.get("state_after"),
        "runtime_timing_ms": summary.get("timing_ms"),
        "runtime_question_count": summary.get("question_count"),
        "runtime_final_question_at_end": summary.get("final_question_at_end"),
        "runtime_quality_blockers": summary.get("quality_blockers"),
        "runtime_grounding_scope": summary.get("grounding_scope"),
        "runtime_gateway_attempts": attempts,
    }
    receipt = _v6_stage_receipt(attempts)
    if receipt:
        actual["runtime_v6_stage_receipt"] = receipt
    return actual


def _v6_stage_receipt(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose only bounded V6 ownership evidence, never payload or task contents."""
    if not attempts:
        return {}
    latest = attempts[-1]
    if not isinstance(latest, dict):
        return {}
    receipt: dict[str, Any] = {}
    for key in ("payload_stage_present", "gateway_task_id_present"):
        if latest.get(key) is True:
            receipt[key] = True
    if isinstance(latest.get("provider_status_code"), int):
        receipt["provider_status_code"] = latest["provider_status_code"]
    for key in ("parse_status", "validator_status"):
        if latest.get(key) in (V6_PARSE_STATUSES if key == "parse_status" else V6_VALIDATOR_STATUSES):
            receipt[key] = latest[key]
    if latest.get("provider_status_code", 0) >= 400:
        receipt["next_owner"] = "provider"
    elif latest.get("parse_status") in {"invalid_json", "missing"} or latest.get("validator_status") == "contract_violation":
        receipt["next_owner"] = latest.get("stage_owner", "v6_runtime")
    elif receipt:
        receipt["next_owner"] = "v6_runtime"
    return receipt


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
        actual.update(_runtime_actual(matched_audit))
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
    if result.get("mode") == "audit_only":
        print_audit_only_human(result)
        return
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


def print_audit_only_human(result: dict[str, Any]) -> None:
    s = result["summary"]
    print("nmbot/Jivo audit-only runtime diagnosis")
    print(f"Summary: audit_records={s['audit_records']} turns={s['turns']} malformed={s['audit_malformed_lines']}")
    print(f"Calls: {json.dumps(s.get('call_totals', {}), ensure_ascii=False, sort_keys=True)}")
    print(f"Blockers: {json.dumps(s.get('blocker_totals', {}), ensure_ascii=False, sort_keys=True)}")
    print(f"Timing ms: {json.dumps(s.get('timing_ms', {}), ensure_ascii=False, sort_keys=True)}")
    for idx, turn in enumerate(result.get("turns", []), 1):
        actual = turn.get("actual") if isinstance(turn.get("actual"), dict) else {}
        refs = turn.get("refs") if isinstance(turn.get("refs"), dict) else {}
        print(f"\nTurn {idx}: stage={actual.get('runtime_stage')} action={actual.get('runtime_action')} answer_kind={actual.get('runtime_answer_kind')}")
        print(f"  Refs: {json.dumps(refs, ensure_ascii=False, sort_keys=True)}")
        print(f"  Calls: {json.dumps(actual.get('runtime_call_counts') or {}, ensure_ascii=False, sort_keys=True)}")
        print(f"  Quality: blockers={actual.get('runtime_quality_blockers') or []} grounding_scope={actual.get('runtime_grounding_scope')}")
        print(f"  Questions: count={actual.get('runtime_question_count')} final_at_end={actual.get('runtime_final_question_at_end')}")


def self_test() -> int:
    rows = [
        {"__line__": 1, "trace_id": "raw-chat-id-must-not-print", "trace_ref": "trace_abcdef123456", "stage": "jivo_response_returned", "outcome": "accepted_async", "http_status": 200},
        {"__line__": 2, "trace_id": "raw-chat-id-must-not-print", "trace_ref": "trace_abcdef123456", "stage": "upstream_response", "http_status": 200},
        {"__line__": 3, "trace_id": "raw-chat-id-must-not-print", "trace_ref": "trace_abcdef123456", "stage": "jivo_response_returned", "outcome": "sent", "http_status": 200},
    ]
    result = diagnose_rows(rows, audit_rows=[{"trace_ref": "trace_abcdef123456", "intent": "main_search", "search_called": True, "search_result_count": 3}])
    dumped = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert result["traces"][0]["stage"] == "main_search"
    assert "raw-chat-id-must-not-print" not in dumped
    print("self-test ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose local nmbot/Jivo dialogue traces without exposing raw private data.")
    parser.add_argument("log_path", nargs="?", type=Path, help="Local bridge JSONL path")
    parser.add_argument("--audit-log", type=Path, help="Optional sanitized per-turn JSONL path")
    parser.add_argument("--audit-only", action="store_true", help="Analyze sanitized audit log runtime_summary rows without bridge correlation")
    parser.add_argument("--trace", help="Filter by raw trace id from input; raw value is never printed")
    parser.add_argument("--last", type=int, help="Use only last N valid bridge or audit records")
    parser.add_argument("--json", action="store_true", help="Print deterministic JSON")
    parser.add_argument("--strict", action="store_true", help="Exit 1 only for true transport/invariant failures")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.audit_only:
        if not args.audit_log:
            parser.error("--audit-only requires --audit-log")
        audit_rows, audit_malformed = read_audit_jsonl(args.audit_log, args.last)
        result = diagnose_audit_only(audit_rows, audit_malformed)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print_human(result)
        return 0
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
