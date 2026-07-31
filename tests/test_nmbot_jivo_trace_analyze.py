from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess


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
    assert result["summary"]["legacy_terminal_outcomes"] == 1
    assert result["summary"]["violations"] == 0
    assert result["latency_sec"]["p50"] == 2.0


def test_legacy_trace_is_informational_non_strict_but_insufficient_in_strict_mode(tmp_path: Path):
    rows = [
        {"__line__": 1, "trace_id": "legacy", "stage": "upstream_response"},
        {"__line__": 2, "trace_id": "legacy", "stage": "final_answer"},
    ]
    assert mod.analyze_rows(rows)["summary"]["violations"] == 0
    strict_result = mod.analyze_rows(rows, strict_delivery_lifecycle=True)
    assert strict_result["summary"]["legacy_terminal_outcomes"] == 1
    assert any(item["type"] == "legacy_trace_insufficient_for_strict_delivery_lifecycle" for item in strict_result["violations"])

    path = tmp_path / "legacy.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    proc = subprocess.run(["python3", str(SCRIPT), str(path), "--strict", "--json"], text=True, capture_output=True, check=False)
    assert proc.returncode == 1
    assert "legacy_trace_insufficient_for_strict_delivery_lifecycle" in proc.stdout


def test_detects_duplicate_terminal_when_accepted_async_is_present():
    rows = [
        {"__line__": 1, "trace_id": "t1", "stage": "bridge_request"},
        {"__line__": 2, "trace_id": "t1", "result": "accepted_async"},
        {"__line__": 3, "trace_id": "t1", "stage": "final_answer"},
        {"__line__": 4, "trace_id": "t1", "stage": "handoff"},
    ]
    result = mod.analyze_rows(rows)
    types = {v["type"] for v in result["violations"]}
    assert "duplicate_terminal" in types
    assert result["traces"]["t1"]["accepted_async_seen"] is True


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
    assert result["summary"]["legacy_terminal_outcomes"] == 1
    assert result["summary"]["unfinished"] == 0
    assert result["summary"]["violations"] == 0
    assert result["traces"]["t1"]["accepted_async_seen"] is True
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
    assert result["summary"]["legacy_terminal_outcomes"] == 1
    assert "duplicate_terminal" not in types
    assert "timeout_or_error_with_later_success" not in types


def test_privacy_safe_delivery_projection_is_correlated_and_terminal_only_at_delivery():
    ref = "trace_abcdef123456"
    rows = [
        {"__line__": 1, "ts": "2026-07-31T10:00:00Z", "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "bridge_accepted", "outcome": "accepted"},
        {"__line__": 2, "ts": "2026-07-31T10:00:01Z", "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "api_completed", "outcome": "completed", "api_status": 200},
        {"__line__": 3, "ts": "2026-07-31T10:00:02Z", "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_selected", "outcome": "selected", "terminal_event": "BOT_MESSAGE"},
        {"__line__": 4, "ts": "2026-07-31T10:00:03Z", "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "jivo_send_attempted", "outcome": "attempted"},
        {"__line__": 5, "ts": "2026-07-31T10:00:04Z", "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "jivo_response", "outcome": "accepted_by_jivo", "jivo_status": 202},
        {"__line__": 6, "ts": "2026-07-31T10:00:05Z", "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_delivery", "outcome": "terminal_send_accepted", "terminal_event": "BOT_MESSAGE", "client_delivery_status": "client_delivery_unconfirmed"},
    ]
    result = mod.analyze_rows(rows)
    assert result["summary"]["traces"] == 1
    assert result["summary"]["jivo_accepted"] == 1
    assert result["summary"]["client_delivery_confirmed"] == 0
    assert result["summary"]["client_delivery_unconfirmed"] == 1
    assert result["summary"]["unfinished"] == 0
    assert result["traces"][ref]["terminal_kind"] == "terminal_send_accepted"
    assert result["traces"][ref]["terminal_result"] == "client_delivery_unconfirmed"


def test_delivery_v1_async_ack_with_terminal_succeeds_strict_but_alone_is_unfinished(tmp_path: Path):
    ref = "trace_abcdef123459"
    completed_rows = [
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "bridge_accepted", "outcome": "accepted_async"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "api_completed", "outcome": "completed"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_selected", "outcome": "selected"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "jivo_send_attempted", "outcome": "attempted"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "jivo_response", "outcome": "accepted_by_jivo"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_delivery", "outcome": "terminal_send_accepted"},
    ]
    completed_path = tmp_path / "completed.jsonl"
    completed_path.write_text("\n".join(json.dumps(row) for row in completed_rows) + "\n", encoding="utf-8")

    strict = subprocess.run(
        ["python3", str(SCRIPT), str(completed_path), "--strict", "--json"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert strict.returncode == 0, strict.stderr
    completed = json.loads(strict.stdout)
    assert completed["summary"]["violations"] == 0
    assert completed["summary"]["jivo_accepted"] == 1
    assert completed["summary"]["client_delivery_confirmed"] == 0
    assert completed["traces"][ref]["terminal_result"] == "client_delivery_unconfirmed"
    assert completed["traces"][ref]["accepted_async_seen"] is True

    pending = mod.analyze_rows([completed_rows[0] | {"__line__": 1}])
    assert pending["summary"]["unfinished"] == 1
    assert pending["summary"]["client_delivery_unconfirmed"] == 0
    assert pending["traces"][ref]["terminal_kind"] is None
    assert pending["traces"][ref]["terminal_result"] is None
    assert "client_delivery_status" not in pending["traces"][ref]
    assert [violation["type"] for violation in pending["violations"]] == ["missing_terminal"]


def test_delivery_v1_valid_api_failure_and_cancel_exceptions_are_strict_valid():
    ref = "trace_abcdef123457"
    rows = [
        {"__line__": 1, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "bridge_accepted", "outcome": "accepted"},
        {"__line__": 2, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "api_failed", "outcome": "failed"},
        {"__line__": 3, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_delivery", "outcome": "not_sent", "error_class": "api_exception"},
    ]

    result = mod.analyze_rows(rows)

    assert result["summary"]["terminal_failures"] == 1
    assert result["traces"][ref]["terminal_count"] == 1
    assert result["traces"][ref]["terminal_kind"] == "failure"
    assert not any(v["type"] == "duplicate_terminal" for v in result["violations"])

    cancelled = mod.analyze_rows([
        {"__line__": 1, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123460", "stage": "bridge_accepted", "outcome": "accepted"},
        {"__line__": 2, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123460", "stage": "terminal_delivery", "outcome": "not_sent", "error_class": "cancelled"},
    ])
    assert cancelled["summary"]["violations"] == 0
    assert cancelled["summary"]["jivo_accepted"] == 0


def test_delivery_projection_api_failure_fallback_then_terminal_acceptance_is_unconfirmed():
    ref = "trace_abcdef123458"
    rows = [
        {"__line__": 1, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "bridge_accepted", "outcome": "accepted"},
        {"__line__": 2, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "api_failed", "outcome": "failed"},
        {"__line__": 3, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_selected", "outcome": "selected"},
        {"__line__": 4, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "jivo_send_attempted", "outcome": "attempted"},
        {"__line__": 5, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "jivo_response", "outcome": "accepted_by_jivo"},
        {"__line__": 6, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_delivery", "outcome": "terminal_send_accepted"},
    ]

    result = mod.analyze_rows(rows)

    assert result["summary"]["jivo_accepted"] == 1
    assert result["summary"]["client_delivery_unconfirmed"] == 1
    assert result["traces"][ref]["terminal_count"] == 1
    assert result["traces"][ref]["terminal_kind"] == "terminal_send_accepted"
    assert result["traces"][ref]["terminal_result"] == "client_delivery_unconfirmed"
    assert not any(v["type"] in {"duplicate_terminal", "timeout_or_error_with_later_success"} for v in result["violations"])


def test_delivery_v1_incomplete_or_reordered_lifecycle_fails_strict_without_acceptance(tmp_path: Path):
    ref = "trace_abcdef123461"
    incomplete = [
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "bridge_accepted", "outcome": "accepted"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "api_completed", "outcome": "completed"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_delivery", "outcome": "terminal_send_accepted"},
    ]
    reordered = [
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "bridge_accepted", "outcome": "accepted"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_selected", "outcome": "selected"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "api_completed", "outcome": "completed"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "jivo_send_attempted", "outcome": "attempted"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "jivo_response", "outcome": "accepted_by_jivo"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_delivery", "outcome": "terminal_send_accepted"},
    ]
    for rows in (incomplete, reordered):
        result = mod.analyze_rows([row | {"__line__": index + 1} for index, row in enumerate(rows)])
        assert result["summary"]["jivo_accepted"] == 0
        assert result["traces"][ref]["terminal_kind"] is None
        assert result["traces"][ref]["terminal_result"] is None
        assert result["traces"][ref]["delivery_lifecycle_valid"] is False
        assert any(v["type"] == "invalid_delivery_lifecycle" for v in result["violations"])

    path = tmp_path / "incomplete.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in incomplete) + "\n", encoding="utf-8")
    strict = subprocess.run(["python3", str(SCRIPT), str(path), "--strict"], text=True, capture_output=True, check=False)
    assert strict.returncode == 1


def test_delivery_v1_invalid_trace_refs_are_never_grouped_or_emitted():
    raw_trace = "raw-trace-id-must-not-leak"
    rows = [
        {"__line__": 1, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": raw_trace, "stage": "terminal_delivery", "outcome": "terminal_send_accepted"},
        {"__line__": 2, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_ABCDEF123456", "stage": "terminal_delivery", "outcome": "terminal_send_accepted"},
    ]

    result = mod.analyze_rows(rows)

    assert result["summary"]["events"] == 0
    assert result["summary"]["traces"] == 0
    assert raw_trace not in str(result)
