from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPT_DIR / "nmbot_jivo_dialogue_diagnose.py"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("nmbot_jivo_dialogue_diagnose", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def write_jsonl(path: Path, rows: list[dict[str, object]] | list[str]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            if isinstance(row, str):
                fh.write(row + "\n")
            else:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def test_complete_bridge_chain_is_delivery_complete_and_strict_passes(tmp_path: Path):
    log = tmp_path / "bridge.jsonl"
    write_jsonl(log, [
        {"trace_id": "raw-chat-1", "trace_ref": "safe-t1", "stage": "jivo_response_returned", "outcome": "accepted_async", "http_status": 200},
        {"trace_id": "raw-chat-1", "trace_ref": "safe-t1", "stage": "upstream_response", "http_status": 200},
        {"trace_id": "raw-chat-1", "trace_ref": "safe-t1", "stage": "jivo_response_returned", "outcome": "sent", "http_status": 200},
    ])
    proc = subprocess.run([sys.executable, str(SCRIPT), str(log), "--json", "--strict"], text=True, capture_output=True, check=False)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["traces"][0]["stage"] == "delivery_complete"
    assert data["summary"]["coverage_gaps"] == 1
    assert "raw-chat-1" not in proc.stdout


def test_delivery_v1_terminal_acceptance_is_not_client_delivery_success(tmp_path: Path):
    log = tmp_path / "delivery.jsonl"
    write_jsonl(log, [
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123456", "stage": "bridge_accepted", "outcome": "accepted"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123456", "stage": "api_completed", "outcome": "completed", "api_status": 200},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123456", "stage": "terminal_selected", "outcome": "selected"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123456", "stage": "jivo_send_attempted", "outcome": "attempted"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123456", "stage": "jivo_response", "outcome": "accepted_by_jivo", "jivo_status": 202},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123456", "stage": "terminal_delivery", "outcome": "terminal_send_accepted", "client_delivery_status": "client_delivery_unconfirmed"},
    ])

    proc = subprocess.run([sys.executable, str(SCRIPT), str(log), "--json", "--strict"], text=True, capture_output=True, check=False)

    assert proc.returncode == 0
    trace = json.loads(proc.stdout)["traces"][0]
    assert trace["stage"] == "jivo_accepted"
    assert trace["outcome"] == "terminal_send_accepted_client_delivery_unconfirmed"
    assert trace["actual"]["terminal_kind"] == "terminal_send_accepted"
    assert trace["actual"]["client_delivery_status"] == "client_delivery_unconfirmed"
    assert "delivery_complete" not in proc.stdout


def test_delivery_v1_invalid_trace_refs_are_dropped_before_grouping():
    result = mod.diagnose_rows([
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "not-a-canonical-ref", "stage": "terminal_delivery", "outcome": "terminal_send_accepted"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123456-extra", "stage": "terminal_delivery", "outcome": "terminal_send_accepted"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123456", "stage": "bridge_accepted", "outcome": "accepted"},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123456", "stage": "api_completed", "outcome": "completed", "api_status": 200},
        {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123456", "stage": "terminal_delivery", "outcome": "terminal_send_accepted"},
    ])

    assert result["summary"] == {
        "traces": 1,
        "events": 3,
        "malformed_lines": 0,
        "audit_malformed_lines": 0,
        "strict_failures": 1,
        "coverage_gaps": 0,
    }
    assert [trace["trace_ref"] for trace in result["traces"]] == ["trace_abcdef123456"]
    assert result["traces"][0]["stage"] == "delivery_lifecycle_invalid"
    assert result["traces"][0]["actual"]["terminal_kind"] is None
    assert result["coverage_gaps"] == []


def test_delivery_v1_incomplete_or_reordered_lifecycle_strict_fails_without_jivo_acceptance(tmp_path: Path):
    ref = "trace_abcdef123462"
    for rows in (
        [
            {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "bridge_accepted", "outcome": "accepted"},
            {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "api_completed", "outcome": "completed"},
            {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_delivery", "outcome": "terminal_send_accepted"},
        ],
        [
            {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "bridge_accepted", "outcome": "accepted"},
            {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_selected", "outcome": "selected"},
            {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "api_completed", "outcome": "completed"},
            {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "jivo_send_attempted", "outcome": "attempted"},
            {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "jivo_response", "outcome": "accepted_by_jivo", "jivo_status": 202},
            {"schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": ref, "stage": "terminal_delivery", "outcome": "terminal_send_accepted"},
        ],
    ):
        log = tmp_path / "delivery.jsonl"
        write_jsonl(log, rows)
        proc = subprocess.run([sys.executable, str(SCRIPT), str(log), "--json", "--strict"], text=True, capture_output=True, check=False)
        data = json.loads(proc.stdout)
        assert proc.returncode == 1
        assert data["traces"][0]["stage"] == "delivery_lifecycle_invalid"
        assert data["traces"][0]["actual"]["terminal_kind"] is None
        assert "jivo_accepted" not in proc.stdout


def test_delivery_v1_jivo_rejection_is_delivery_failure_not_upstream_failure():
    result = mod.diagnose_rows([
        {"__line__": 1, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123463", "stage": "bridge_accepted", "outcome": "accepted"},
        {"__line__": 2, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123463", "stage": "api_completed", "outcome": "completed"},
        {"__line__": 3, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123463", "stage": "terminal_selected", "outcome": "selected"},
        {"__line__": 4, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123463", "stage": "jivo_send_attempted", "outcome": "attempted"},
        {"__line__": 5, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123463", "stage": "jivo_response", "outcome": "rejected_by_jivo", "error_class": "jivo_http_error", "jivo_status": 403},
        {"__line__": 6, "schema": "nmbot.jivo.delivery_trace.v1", "trace_ref": "trace_abcdef123463", "stage": "terminal_delivery", "outcome": "failed", "error_class": "jivo_http_error", "jivo_status": 403},
    ])
    trace = result["traces"][0]
    assert trace["stage"] == "transport_auth_or_http_failure"
    assert trace["outcome"] == "transport_failed"
    assert result["summary"]["strict_failures"] == 1


def test_upstream_explicit_error_without_final_is_strict_failure(tmp_path: Path):
    log = tmp_path / "bridge.jsonl"
    write_jsonl(log, [
        {"trace_id": "raw-chat-2", "trace_ref": "safe-t2", "stage": "jivo_response_returned", "outcome": "accepted_async", "http_status": 200},
        {"trace_id": "raw-chat-2", "trace_ref": "safe-t2", "stage": "upstream_response", "status": "error", "http_status": 502},
    ])
    proc = subprocess.run([sys.executable, str(SCRIPT), str(log), "--json", "--strict"], text=True, capture_output=True, check=False)
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["traces"][0]["stage"] == "upstream_failure"
    assert data["summary"]["strict_failures"] == 1


def test_safe_audit_correlation_turns_completed_trace_into_main_search_clarify_without_raw_text(tmp_path: Path):
    log = tmp_path / "bridge.jsonl"
    audit = tmp_path / "audit.jsonl"
    write_jsonl(log, [
        {"trace_id": "raw-chat-3", "trace_ref": "trace_abcdef123450", "turn_ref": "turn-3", "stage": "upstream_response", "http_status": 200},
        {"trace_id": "raw-chat-3", "trace_ref": "trace_abcdef123450", "turn_ref": "turn-3", "stage": "jivo_response_returned", "outcome": "sent", "http_status": 200},
    ])
    write_jsonl(audit, [
        {"trace_ref": "trace_abcdef123450", "turn_ref": "turn-3", "intent": "main_search", "search_called": True, "search_result_count": 0, "text": "секретный запрос клиента"},
    ])
    result = subprocess.run([sys.executable, str(SCRIPT), str(log), "--audit-log", str(audit), "--json"], text=True, capture_output=True, check=True)
    data = json.loads(result.stdout)
    assert data["traces"][0]["stage"] == "main_search_clarify"
    assert "секретный" not in result.stdout
    assert "text" not in result.stdout


def test_phone_audit_allows_only_safe_phone_fields(tmp_path: Path):
    log = tmp_path / "bridge.jsonl"
    audit = tmp_path / "audit.jsonl"
    write_jsonl(log, [
        {"trace_id": "raw-chat-4", "trace_ref": "trace_abcdef123451", "stage": "upstream_response", "http_status": 200},
        {"trace_id": "raw-chat-4", "trace_ref": "trace_abcdef123451", "stage": "jivo_response_returned", "outcome": "invite_agent", "http_status": 200},
    ])
    write_jsonl(audit, [
        {"trace_ref": "trace_abcdef123451", "intent": "phone_captured", "phone_detected": True, "phone_len": 11, "phone_last4": "1234", "phone_ref": "phone_abcd", "phone": "+79991234567", "message": "мой номер +79991234567", "text": "мой номер +79991234567"},
    ])
    out = subprocess.run([sys.executable, str(SCRIPT), str(log), "--audit-log", str(audit), "--json"], text=True, capture_output=True, check=True).stdout
    data = json.loads(out)
    audit_record = data["traces"][0]["audit"][0]
    assert data["traces"][0]["stage"] == "phone_captured"
    assert audit_record["phone_detected"] is True
    assert audit_record["phone_len"] == 11
    assert audit_record["phone_last4"] == "1234"
    assert audit_record["phone_ref"] == "phone_abcd"
    assert "+79991234567" not in out
    assert "мой номер" not in out


def test_nested_runtime_summary_enriches_actual_without_raw_text_or_card_names(tmp_path: Path):
    log = tmp_path / "bridge.jsonl"
    audit = tmp_path / "audit.jsonl"
    write_jsonl(log, [
        {"trace_id": "raw-chat-5", "trace_ref": "trace_abcdef123452", "turn_ref": "turn-5", "stage": "upstream_response", "http_status": 200},
        {"trace_id": "raw-chat-5", "trace_ref": "trace_abcdef123452", "turn_ref": "turn-5", "stage": "jivo_response_returned", "outcome": "sent", "http_status": 200},
    ])
    write_jsonl(audit, [{
        "trace_ref": "trace_abcdef123452",
        "turn_ref": "turn-5",
        "intent": "main_search",
        "text": "секретный клиентский текст +79991234567",
        "runtime_summary": {
            "stage": "first_list",
            "action": "search",
            "answer_kind": "search_results",
            "call_counts": {"planner": 1, "search": 1, "selected_enrichment": 0, "gateway_attempts": 7, "raw": 99},
            "state_before": {"param_keys": ["rooms", "phone:+79991234567"], "visible_options_count": 0, "selected_present": False, "pending_followup": "", "active_topic": "family", "raw_values": {"rooms": 2}},
            "state_after": {"param_keys": ["rooms"], "visible_options_count": 3, "selected_present": True, "pending_followup": "financing_consent", "active_topic": "family", "selected_name": "Секретный ЖК"},
            "timing_ms": {"planner": 1, "execution": 2, "response": 3, "total": 6},
            "question_count": 1,
            "final_question_at_end": True,
            "quality_blockers": ["search_without_cards", "raw prompt secret"],
            "grounding_scope": "canonical_response_plan",
            "prompt": "raw prompt secret",
            "cards": [{"name": "Секретный ЖК"}],
        },
    }])

    out = subprocess.run([sys.executable, str(SCRIPT), str(log), "--audit-log", str(audit), "--json"], text=True, capture_output=True, check=True).stdout
    data = json.loads(out)
    actual = data["traces"][0]["actual"]
    assert actual["runtime_stage"] == "first_list"
    assert actual["runtime_action"] == "search"
    assert actual["runtime_call_counts"]["gateway_attempts"] == 5
    assert actual["runtime_state_after"]["param_keys"] == ["rooms"]
    assert actual["runtime_grounding_scope"] == "canonical_response_plan"
    for forbidden in ["секретный", "+7999", "Секретный ЖК", "raw prompt", "raw_values", "selected_name"]:
        assert forbidden not in out


def test_runtime_gateway_attempts_are_exposed_safely_for_timeline_join(tmp_path: Path):
    log = tmp_path / "bridge.jsonl"
    audit = tmp_path / "audit.jsonl"
    write_jsonl(log, [
        {"trace_id": "raw-chat-6", "trace_ref": "trace_abcdef123456", "stage": "upstream_response", "http_status": 200},
        {"trace_id": "raw-chat-6", "trace_ref": "trace_abcdef123456", "stage": "jivo_response_returned", "outcome": "sent", "http_status": 200},
    ])
    write_jsonl(audit, [{
        "trace_ref": "trace_abcdef123456",
        "intent": "main_search",
        "runtime_summary": {
            "stage": "first_list",
            "action": "search",
            "gateway_attempt_details": [
                {"stage": "gateway_attempt", "model": "google/gemini", "ok": True, "gateway_task_id": "task-1", "duration_ms": 44, "parse_status": "ok", "raw_response": "secret"}
            ],
        },
    }])

    out = subprocess.run([sys.executable, str(SCRIPT), str(log), "--audit-log", str(audit), "--json"], text=True, capture_output=True, check=True).stdout
    data = json.loads(out)

    attempts = data["traces"][0]["actual"]["runtime_gateway_attempts"]
    assert attempts == [{"stage": "gateway_attempt", "model": "google_gemini", "ok": True, "gateway_task_id": "task-1", "duration_ms": 44, "parse_status": "ok"}]
    assert "raw_response" not in out
    assert "secret" not in out


def test_arbitrary_trace_ref_is_rejected_and_derived_from_raw_trace_id(tmp_path: Path):
    log = tmp_path / "bridge.jsonl"
    raw_trace_id = "client:+79991234567:550e8400-e29b-41d4-a716-446655440000"
    raw_trace_ref = "550e8400-e29b-41d4-a716-446655440000"
    expected = "trace_" + hashlib.sha256(raw_trace_id.encode("utf-8")).hexdigest()[:12]
    write_jsonl(log, [
        {"trace_id": raw_trace_id, "trace_ref": raw_trace_ref, "stage": "upstream_response", "http_status": 200},
        {"trace_id": raw_trace_id, "trace_ref": "+79991234567", "stage": "jivo_response_returned", "outcome": "sent", "http_status": 200},
    ])

    out = subprocess.run([sys.executable, str(SCRIPT), str(log), "--json"], text=True, capture_output=True, check=True).stdout
    data = json.loads(out)

    assert data["traces"][0]["trace_ref"] == expected
    assert all(event.get("trace_ref") == expected for event in data["traces"][0]["evidence"])
    assert raw_trace_ref not in out
    assert raw_trace_id not in out
    assert "+79991234567" not in out


def test_valid_canonical_trace_ref_is_preserved_and_matches_audit(tmp_path: Path):
    log = tmp_path / "bridge.jsonl"
    audit = tmp_path / "audit.jsonl"
    write_jsonl(log, [
        {"trace_id": "raw-chat-canonical", "trace_ref": "trace_abcdef123456", "stage": "upstream_response", "http_status": 200},
        {"trace_id": "raw-chat-canonical", "trace_ref": "trace_abcdef123456", "stage": "jivo_response_returned", "outcome": "sent", "http_status": 200},
    ])
    write_jsonl(audit, [{"trace_ref": "trace_abcdef123456", "intent": "main_search", "search_called": True, "search_result_count": 1}])

    out = subprocess.run([sys.executable, str(SCRIPT), str(log), "--audit-log", str(audit), "--json"], text=True, capture_output=True, check=True).stdout
    data = json.loads(out)

    assert data["traces"][0]["trace_ref"] == "trace_abcdef123456"
    assert data["traces"][0]["stage"] == "main_search"
    assert data["traces"][0]["actual"]["audit_events"] == 1


def _audit_runtime_row(idx: int, *, blocker: str | None = None, total_ms: int = 10) -> dict[str, object]:
    blockers = [blocker] if blocker else []
    return {
        "schema_version": 1,
        "ts": f"2026-07-20T12:00:0{idx}Z",
        "role": "bot",
        "conversation_ref": f"sha256:conversation{idx}",
        "session_key_ref": f"sha256:session{idx}",
        "event_id_ref": f"sha256:event{idx}",
        "answer_kind": "search_results",
        "text": f"сырой текст клиента/бота +7999123456{idx}",
        "runtime_summary": {
            "stage": "first_list" if idx % 2 else "selected_object",
            "action": "search" if idx % 2 else "answer_selected_option",
            "answer_kind": "search_results",
            "call_counts": {"planner": 1, "search": 1 if idx % 2 else 0, "selected_enrichment": 0 if idx % 2 else 1, "gateway_attempts": idx, "provider_retries": 99},
            "state_before": {"param_keys": ["rooms", "phone:+79991234567"], "visible_options_count": idx, "selected_present": False, "active_topic": "family", "raw_values": {"rooms": 2}},
            "state_after": {"param_keys": ["rooms"], "visible_options_count": min(idx + 1, 3), "selected_present": True, "pending_followup": "financing_consent", "active_topic": "family", "selected_name": "Секретный ЖК"},
            "timing_ms": {"planner": idx, "execution": idx + 1, "response": idx + 2, "total": total_ms},
            "question_count": 1,
            "final_question_at_end": True,
            "quality_blockers": blockers + ["raw prompt secret"],
            "grounding_scope": "canonical_response_plan",
            "prompt": "raw prompt secret",
            "cards": [{"name": "Секретный ЖК"}],
        },
    }


def test_audit_only_cli_works_without_bridge_log_and_aggregates_runtime_summary(tmp_path: Path):
    audit = tmp_path / "dialogue_journal.jsonl"
    write_jsonl(audit, [
        {"role": "user", "conversation_ref": "sha256:user", "text": "сырой пользовательский текст"},
        _audit_runtime_row(1, blocker="search_without_cards", total_ms=10),
        _audit_runtime_row(2, blocker="enrichment_error", total_ms=20),
        _audit_runtime_row(3, total_ms=30),
    ])

    out = subprocess.run([sys.executable, str(SCRIPT), "--audit-log", str(audit), "--audit-only", "--json"], text=True, capture_output=True, check=True).stdout
    data = json.loads(out)
    assert data["mode"] == "audit_only"
    assert data["summary"]["audit_records"] == 4
    assert data["summary"]["turns"] == 3
    assert data["summary"]["call_totals"] == {"planner": 3, "search": 2, "selected_enrichment": 1, "gateway_attempts": 6}
    assert data["summary"]["blocker_totals"] == {"enrichment_error": 1, "search_without_cards": 1}
    assert data["summary"]["timing_ms"]["total"] == {"p50": 20, "p95": 30}
    first = data["turns"][0]
    assert first["refs"] == {"conversation_ref": "sha256:conversation1", "event_id_ref": "sha256:event1", "session_key_ref": "sha256:session1"}
    assert first["actual"]["runtime_stage"] == "first_list"
    assert first["actual"]["runtime_grounding_scope"] == "canonical_response_plan"
    for forbidden in ["сырой", "+7999", "Секретный ЖК", "raw prompt", "provider_retries", "raw_values", "selected_name", "text"]:
        assert forbidden not in out


def test_audit_only_last_bounds_audit_records_and_turns(tmp_path: Path):
    audit = tmp_path / "dialogue_journal.jsonl"
    write_jsonl(audit, [_audit_runtime_row(1, total_ms=10), _audit_runtime_row(2, total_ms=20), _audit_runtime_row(3, blocker="runtime_error", total_ms=30)])

    out = subprocess.run([sys.executable, str(SCRIPT), "--audit-log", str(audit), "--audit-only", "--last", "2", "--json"], text=True, capture_output=True, check=True).stdout
    data = json.loads(out)
    assert data["summary"]["audit_records"] == 2
    assert data["summary"]["turns"] == 2
    assert [turn["refs"]["event_id_ref"] for turn in data["turns"]] == ["sha256:event2", "sha256:event3"]
    assert data["summary"]["call_totals"] == {"planner": 2, "search": 1, "selected_enrichment": 1, "gateway_attempts": 5}
    assert data["summary"]["blocker_totals"] == {"runtime_error": 1}


def test_chat_closed_gets_distinct_non_client_answer_classification():
    result = mod.diagnose_rows([
        {"__line__": 1, "trace_id": "closed-raw", "trace_ref": "safe-closed", "stage": "CHAT_CLOSED", "event": "CHAT_CLOSED"},
        {"__line__": 2, "trace_id": "closed-raw", "trace_ref": "safe-closed", "stage": "jivo_response_returned", "outcome": "event_not_sendable", "http_status": 200},
    ])
    assert result["traces"][0]["stage"] == "chat_closed"
    assert result["traces"][0]["outcome"] == "non_client_answer_terminal"
    assert result["summary"]["strict_failures"] == 0


def test_malformed_json_strict_fails(tmp_path: Path):
    log = tmp_path / "bad.jsonl"
    write_jsonl(log, ['{"trace_id": "ok", "stage": "upstream_response"}', '{bad json'])
    proc = subprocess.run([sys.executable, str(SCRIPT), str(log), "--json", "--strict"], text=True, capture_output=True, check=False)
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["summary"]["malformed_lines"] == 1
    assert data["strict_failures"][0]["stage"] == "malformed_input"
