from __future__ import annotations

import importlib.util
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
        {"trace_id": "raw-chat-3", "trace_ref": "safe-t3", "turn_ref": "turn-3", "stage": "upstream_response", "http_status": 200},
        {"trace_id": "raw-chat-3", "trace_ref": "safe-t3", "turn_ref": "turn-3", "stage": "jivo_response_returned", "outcome": "sent", "http_status": 200},
    ])
    write_jsonl(audit, [
        {"trace_ref": "safe-t3", "turn_ref": "turn-3", "intent": "main_search", "search_called": True, "search_result_count": 0, "text": "секретный запрос клиента"},
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
        {"trace_id": "raw-chat-4", "trace_ref": "safe-t4", "stage": "upstream_response", "http_status": 200},
        {"trace_id": "raw-chat-4", "trace_ref": "safe-t4", "stage": "jivo_response_returned", "outcome": "invite_agent", "http_status": 200},
    ])
    write_jsonl(audit, [
        {"trace_ref": "safe-t4", "intent": "phone_captured", "phone_detected": True, "phone_len": 11, "phone_last4": "1234", "phone_ref": "phone_abcd", "phone": "+79991234567", "message": "мой номер +79991234567", "text": "мой номер +79991234567"},
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
