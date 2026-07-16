#!/usr/bin/env python3
"""Read-only analyzer for nmbot/Jivo structured JSONL traces.

The script is intentionally schema-tolerant: it never calls Jivo/LLM, never
prints payload text/secrets, and treats unknown fields as optional evidence.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SENSITIVE_KEY_PARTS = ("payload", "text", "token", "authorization", "url", "body", "message")
TERMINAL_OK_MARKERS = ("final", "final_answer", "bot_message", "answered", "handoff", "invite_agent")
TERMINAL_BAD_MARKERS = ("explicit_failure", "failure", "failed", "error", "timeout")
ASYNC_ACK_MARKERS = ("accepted_async", "accepted_for_async_processing")
UPSTREAM_MARKERS = ("upstream_response", "nmbot_response", "local_response", "api_response", "llm_response", "gateway_response")


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _first_present(row: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def trace_id_of(row: dict[str, Any]) -> str:
    value = _first_present(row, ("trace_id", "traceId", "request_id", "requestId", "event_id", "eventId", "correlation_id"))
    return str(value) if value not in (None, "") else "__missing_trace_id__"


def stage_of(row: dict[str, Any]) -> str:
    value = _first_present(row, ("stage", "event", "event_name", "name", "action", "result", "status", "type"))
    if isinstance(value, (str, int, float, bool)):
        return str(value)[:80]
    return "unknown"


def ts_of(row: dict[str, Any]) -> datetime | None:
    for key in ("ts", "timestamp", "time", "created_at", "datetime"):
        parsed = _parse_ts(row.get(key))
        if parsed:
            return parsed
    return None


def _haystack(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("stage", "event", "event_name", "name", "action", "result", "status", "type", "outcome", "kind"):
        val = row.get(key)
        if isinstance(val, (str, int, float, bool)):
            parts.append(str(val).lower())
    return " ".join(parts)


def is_accepted_async(row: dict[str, Any]) -> bool:
    hay = _haystack(row)
    return any(marker in hay for marker in ASYNC_ACK_MARKERS)


def is_upstream_response(row: dict[str, Any]) -> bool:
    hay = _haystack(row)
    return any(marker in hay for marker in UPSTREAM_MARKERS) or any(key in row for key in UPSTREAM_MARKERS)


def terminal_kind(row: dict[str, Any]) -> str | None:
    hay = _haystack(row)
    # The bridge uses the same stage (`jivo_response_returned`) for the early
    # webhook acknowledgement and the later final Jivo POST.  The outcome is
    # therefore part of the terminal contract; an async acknowledgement is
    # evidence of the bug, not a terminal result.
    if any(marker in hay for marker in ASYNC_ACK_MARKERS):
        return None
    if any(marker in hay for marker in TERMINAL_BAD_MARKERS):
        return "failure"
    stage = str(row.get("stage") or "").lower()
    outcome = str(row.get("outcome") or "").lower()
    if stage == "jivo_response_returned" and outcome in {"sent", "delivered", "final", "final_answer", "handoff", "invite_agent", "static_smoke"}:
        return "success"
    if stage == "jivo_response_returned" and row.get("http_status") in {200, 201, 202}:
        return "success"
    if any(marker in hay for marker in TERMINAL_OK_MARKERS):
        return "success"
    if row.get("terminal") is True or row.get("is_terminal") is True:
        return "success" if str(row.get("status", "")).lower() not in {"error", "failed", "timeout"} else "failure"
    return None


def is_static_trace(events: list[dict[str, Any]]) -> bool:
    for row in events:
        hay = _haystack(row)
        if row.get("static") is True or row.get("is_static") is True or "static" in hay:
            return True
    return False


def safe_event(row: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {"line": row.get("__line__"), "stage": stage_of(row)}
    ts = ts_of(row)
    if ts:
        safe["ts"] = ts.isoformat()
    for key in ("trace_id", "event_id", "status", "result", "outcome", "kind"):
        if key in row and not any(part in key.lower() for part in SENSITIVE_KEY_PARTS):
            val = row[key]
            if isinstance(val, (str, int, float, bool)):
                safe[key] = str(val)[:120]
    return safe


def read_jsonl(path: Path, last: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                malformed.append({"line": line_no, "error": exc.msg})
                continue
            if not isinstance(value, dict):
                malformed.append({"line": line_no, "error": "JSON value is not an object"})
                continue
            value["__line__"] = line_no
            rows.append(value)
    if last is not None:
        rows = rows[-last:]
    return rows, malformed


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    pos = (len(ordered) - 1) * pct
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return round(ordered[lo] * (1 - frac) + ordered[hi] * frac, 3)


def analyze_rows(rows: list[dict[str, Any]], malformed: list[dict[str, Any]] | None = None, trace_filter: str | None = None) -> dict[str, Any]:
    malformed = malformed or []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        tid = trace_id_of(row)
        if trace_filter and tid != trace_filter:
            continue
        grouped[tid].append(row)

    traces: dict[str, Any] = {}
    violations: list[dict[str, Any]] = []
    latencies: list[float] = []
    stage_sequences = Counter()
    counters = Counter()

    for tid, events in grouped.items():
        events = sorted(events, key=lambda r: (ts_of(r) or datetime.min.replace(tzinfo=timezone.utc), int(r.get("__line__") or 0)))
        stages = [stage_of(e) for e in events]
        stage_sequences[" -> ".join(stages[:12])] += 1
        terminals = [(idx, terminal_kind(e), e) for idx, e in enumerate(events) if terminal_kind(e)]
        upstream_indexes = [idx for idx, e in enumerate(events) if is_upstream_response(e)]
        accepted = [e for e in events if is_accepted_async(e)]
        first_ts = next((ts_of(e) for e in events if ts_of(e)), None)
        last_ts = next((ts_of(e) for e in reversed(events) if ts_of(e)), None)
        latency = round((last_ts - first_ts).total_seconds(), 3) if first_ts and last_ts else None
        if latency is not None:
            latencies.append(latency)

        counters["static" if is_static_trace(events) else "real"] += 1
        if not terminals:
            counters["unfinished"] += 1
            violations.append({"type": "missing_terminal", "trace_id": tid, "evidence": [safe_event(e) for e in events[-3:]]})
        else:
            last_kind = terminals[-1][1]
            counters["completed" if last_kind == "success" else "errors"] += 1
            if last_kind == "failure" and "timeout" in _haystack(terminals[-1][2]):
                counters["timeouts"] += 1
        if len(terminals) > 1:
            violations.append({"type": "duplicate_terminal", "trace_id": tid, "evidence": [safe_event(e) for _, _, e in terminals]})
        if accepted:
            violations.append({"type": "accepted_async_present", "trace_id": tid, "evidence": [safe_event(e) for e in accepted[:3]]})
        for idx, kind, event in terminals:
            if kind == "success" and upstream_indexes and idx < min(upstream_indexes):
                violations.append({"type": "final_before_upstream_response", "trace_id": tid, "evidence": [safe_event(event), safe_event(events[min(upstream_indexes)])]})
                break
        failure_indexes = [idx for idx, kind, _ in terminals if kind == "failure"]
        success_indexes = [idx for idx, kind, _ in terminals if kind == "success"]
        if failure_indexes and success_indexes and min(success_indexes) > min(failure_indexes):
            violations.append({"type": "timeout_or_error_with_later_success", "trace_id": tid, "evidence": [safe_event(events[min(failure_indexes)]), safe_event(events[min(success_indexes)])]})

        traces[tid] = {
            "events": len(events),
            "kind": "static" if is_static_trace(events) else "real",
            "terminal_count": len(terminals),
            "terminal_kind": terminals[-1][1] if terminals else None,
            "latency_sec": latency,
            "stages": stages,
            "lines": [e.get("__line__") for e in events],
        }

    for item in malformed:
        violations.append({"type": "malformed_json_line", "line": item.get("line"), "error": item.get("error")})

    return {
        "summary": {
            "events": sum(len(v) for v in grouped.values()),
            "traces": len(grouped),
            "real_traces": counters["real"],
            "static_traces": counters["static"],
            "completed": counters["completed"],
            "errors": counters["errors"],
            "timeouts": counters["timeouts"],
            "unfinished": counters["unfinished"],
            "malformed_lines": len(malformed),
            "violations": len(violations),
        },
        "latency_sec": {"min": min(latencies) if latencies else None, "p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95), "max": max(latencies) if latencies else None},
        "stage_sequences": [{"sequence": seq, "count": count} for seq, count in stage_sequences.most_common(20)],
        "violations": violations,
        "traces": traces,
    }


def print_human(result: dict[str, Any]) -> None:
    s = result["summary"]
    print("nmbot/Jivo trace analyzer")
    print("Сводка:")
    print(f"  событий: {s['events']}, traces: {s['traces']} (real: {s['real_traces']}, static: {s['static_traces']})")
    print(f"  completed: {s['completed']}, errors: {s['errors']}, timeouts: {s['timeouts']}, unfinished: {s['unfinished']}")
    lat = result["latency_sec"]
    print(f"  latency sec: min={lat['min']} p50={lat['p50']} p95={lat['p95']} max={lat['max']}")
    print("\nStage sequences:")
    for item in result["stage_sequences"][:10]:
        print(f"  {item['count']}× {item['sequence']}")
    print("\nActual evidence:")
    if not result["violations"]:
        print("  Нарушений invariant не найдено.")
        return
    for v in result["violations"][:30]:
        loc = f"trace={v.get('trace_id')}" if v.get("trace_id") else f"line={v.get('line')}"
        print(f"  - {v['type']} ({loc})")
        for ev in v.get("evidence", [])[:4]:
            print(f"      line={ev.get('line')} stage={ev.get('stage')} status={ev.get('status', '')} result={ev.get('result', '')}")


def self_test() -> int:
    rows = [
        {"__line__": 1, "ts": "2026-07-16T10:00:00Z", "trace_id": "ok", "stage": "bridge_request"},
        {"__line__": 2, "ts": "2026-07-16T10:00:01Z", "trace_id": "ok", "stage": "upstream_response"},
        {"__line__": 3, "ts": "2026-07-16T10:00:02Z", "trace_id": "ok", "stage": "final_answer"},
        {"__line__": 4, "trace_id": "bad", "stage": "accepted_async"},
    ]
    result = analyze_rows(rows)
    assert result["summary"]["traces"] == 2
    assert result["summary"]["completed"] == 1
    assert any(v["type"] == "accepted_async_present" for v in result["violations"])
    assert any(v["type"] == "missing_terminal" for v in result["violations"])
    print("self-test ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze nmbot/Jivo structured JSONL traces without printing payloads.")
    parser.add_argument("log_path", nargs="?", type=Path)
    parser.add_argument("--last", type=int, help="Analyze only last N valid JSONL records; malformed line count still uses the full file.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary without raw text.")
    parser.add_argument("--trace", help="Analyze one trace_id only.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 when invariant violations are found.")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.log_path:
        parser.error("log_path is required unless --self-test is used")
    rows, malformed = read_jsonl(args.log_path, args.last)
    result = analyze_rows(rows, malformed, args.trace)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(result)
    return 1 if args.strict and result["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
