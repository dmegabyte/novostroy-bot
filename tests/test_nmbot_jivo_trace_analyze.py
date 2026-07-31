from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_jivo_trace_analyze.py"
spec = importlib.util.spec_from_file_location("nmbot_jivo_trace_analyze", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def test_clean_trace_completed_without_violations():
    rows = [
        {"__line__": 1, "ts": "2026-07-16T10:00:00Z", "trace_id": "t1", "stage": "bridge_request"},
        {"__line__": 2, "ts": "2026-07-16T10:00:01Z", "trace_id": "t1", "stage": "upstream_response"},
        {"__line__": 3, "ts": "2026-07-16T10:00:02Z", "trace_id": "t1", "stage": "final_answer"},
    ]
    result = mod.analyze_rows(rows)
    assert result["summary"]["completed"] == 1
    assert result["summary"]["violations"] == 0
    assert result["latency_sec"]["p50"] == 2.0


def test_detects_duplicate_terminal_and_accepted_async():
    rows = [
        {"__line__": 1, "trace_id": "t1", "stage": "bridge_request"},
        {"__line__": 2, "trace_id": "t1", "result": "accepted_async"},
        {"__line__": 3, "trace_id": "t1", "stage": "final_answer"},
        {"__line__": 4, "trace_id": "t1", "stage": "handoff"},
    ]
    result = mod.analyze_rows(rows)
    types = {v["type"] for v in result["violations"]}
    assert "duplicate_terminal" in types
    assert "accepted_async_present" in types


def test_detects_final_before_upstream_and_later_success_after_error():
    rows = [
        {"__line__": 1, "trace_id": "t1", "stage": "final_answer"},
        {"__line__": 2, "trace_id": "t1", "stage": "upstream_response"},
        {"__line__": 3, "trace_id": "t2", "stage": "timeout"},
        {"__line__": 4, "trace_id": "t2", "stage": "final_answer"},
    ]
    result = mod.analyze_rows(rows)
    types = {v["type"] for v in result["violations"]}
    assert "final_before_upstream_response" in types
    assert "timeout_or_error_with_later_success" in types


def test_malformed_lines_are_reported_without_raw_text():
    result = mod.analyze_rows([], [{"line": 7, "error": "bad json"}])
    assert result["summary"]["malformed_lines"] == 1
    assert result["violations"][0] == {"type": "malformed_json_line", "line": 7, "error": "bad json"}


def test_async_ack_is_not_terminal_and_sent_is_terminal():
    rows = [
        {"__line__": 1, "trace_id": "t1", "stage": "request_received", "outcome": "accepted_for_async_processing"},
        {"__line__": 2, "trace_id": "t1", "stage": "jivo_response_returned", "outcome": "accepted_async", "http_status": 200},
        {"__line__": 3, "trace_id": "t1", "stage": "upstream_response", "outcome": "upstream", "http_status": 200},
        {"__line__": 4, "trace_id": "t1", "stage": "jivo_response_returned", "outcome": "sent", "http_status": 200},
    ]
    result = mod.analyze_rows(rows)
    assert result["summary"]["completed"] == 1
    assert result["summary"]["unfinished"] == 0
    assert sum(v["type"] == "accepted_async_present" for v in result["violations"]) == 1
    assert not any(v["type"] == "duplicate_terminal" for v in result["violations"])


def test_status_message_before_final_is_not_duplicate_terminal():
    rows = [
        {"__line__": 1, "trace_id": "t-status", "stage": "jivo_response_returned", "outcome": "accepted_async", "http_status": 200},
        {"__line__": 2, "trace_id": "t-status", "stage": "status_update", "outcome": "fallback_status_sent", "http_status": 200},
        {"__line__": 3, "trace_id": "t-status", "stage": "jivo_response_returned", "outcome": "status_sent", "delivery_role": "status", "http_status": 200},
        {"__line__": 4, "trace_id": "t-status", "stage": "upstream_response", "outcome": "upstream_after_status", "http_status": 200},
        {"__line__": 5, "trace_id": "t-status", "stage": "jivo_response_returned", "outcome": "sent", "delivery_role": "final", "http_status": 200},
    ]
    result = mod.analyze_rows(rows)
    types = {v["type"] for v in result["violations"]}
    assert result["summary"]["completed"] == 1
    assert "duplicate_terminal" not in types
    assert "timeout_or_error_with_later_success" not in types
