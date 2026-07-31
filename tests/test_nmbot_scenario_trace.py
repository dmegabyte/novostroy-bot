from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_gateway_task_diag.py"
spec = importlib.util.spec_from_file_location("nmbot_gateway_task_diag_scenario", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _ref(raw: str) -> str:
    return mod._hash_session_ref(raw)


def test_complete_trace_order_jivo_planner_gateway_response_composer(tmp_path: Path) -> None:
    session_ref = _ref("session-a")
    _write_jsonl(
        tmp_path / "dialogue_journal.jsonl",
        [
            {"ts": "2026-07-20T10:00:00Z", "role": "user", "session_key_ref": session_ref, "conversation_ref": session_ref, "event_type": "turn", "text": "Хочу квартиру"},
            {
                "ts": "2026-07-20T10:00:03Z",
                "role": "bot",
                "session_key_ref": session_ref,
                "conversation_ref": session_ref,
                "event_type": "turn",
                "text": "Подобрала вариант",
                "answer_kind": "search_answer",
                "offer_type": "flat",
                "response_composer": {"composer_used": True, "fallback_reason": None, "validation_stage": None, "validation_codes": [], "attempts": 1},
            },
        ],
    )
    _write_jsonl(
        tmp_path / "planner_trace-2026-07-20.jsonl",
        [
            {
                "ts": "2026-07-20T10:00:01Z",
                "session_key_ref": session_ref,
                "conversation_ref": session_ref,
                "user_text": "Хочу квартиру",
                "action": "search",
                "intent": "buy",
                "target": "flat",
                "search_policy": "required",
                "canonical_valid": True,
                "final_decision": {"action": "search", "target": "flat", "search_policy": "required"},
                "planner_raw_response": json.dumps({"user_goal": "купить квартиру", "confidence": 0.9}, ensure_ascii=False),
                "raw_response_present": True,
            }
        ],
    )

    report = mod.build_scenario_report(
        session_ref=session_ref,
        date="2026-07-20",
        logs_dir=tmp_path,
        gateway_diagnosis={"task_id": "42", "status": "completed", "completed_at": "2026-07-20T10:00:02Z", "result_present": True, "error_code": "none", "category": "ok", "layer": "none", "event_correlation": {"matched_count": 0, "events": []}},
    )

    assert [event["layer"] for event in report["timeline"]] == ["jivo_input", "planner", "canonical_decision", "gateway", "response", "composer"]
    assert report["verdict"]["classification"] == "ok"


def test_failed_data_task_is_gateway_unknown_downstream_without_mcp_claim(tmp_path: Path) -> None:
    session_ref = _ref("session-b")
    _write_jsonl(tmp_path / "bot_error_events-2026-07-20.jsonl", [{"ts": "2026-07-20T11:00:00Z", "task_id": "2330033", "session_key_ref": session_ref, "error_type": "gateway_task_error", "stage": "poll", "task_status": "failed"}])
    diagnosis = mod.build_diagnosis("2330033", {"status": "failed"}, {"status": "failed", "result": None, "error_message": "'data'"})

    report = mod.build_scenario_report(task_id="2330033", date="2026-07-20", logs_dir=tmp_path, gateway_diagnosis=diagnosis)
    dumped = json.dumps(report, ensure_ascii=False)

    assert report["verdict"] == {"stage": "gateway", "classification": "unknown_downstream", "evidence": {"error_code": "missing_response_field", "category": "downstream_contract"}}
    assert "mcp" not in dumped.lower()
    assert "'data'" not in dumped


def test_planner_semantic_output_is_allowlisted_and_raw_fields_excluded(tmp_path: Path) -> None:
    session_ref = _ref("session-c")
    raw = {
        "user_goal": "подобрать квартиру",
        "selected_reference": "телефон +79990000000",
        "provider_prompt": "SECRET PROMPT",
        "raw_model_contact": "Иван +79990000000",
    }
    _write_jsonl(tmp_path / "planner_trace-2026-07-20.jsonl", [{"ts": "2026-07-20T12:00:00Z", "session_key_ref": session_ref, "conversation_ref": session_ref, "planner_raw_response": json.dumps(raw, ensure_ascii=False), "raw_response_present": True}])

    report = mod.build_scenario_report(session_ref=session_ref, date="2026-07-20", logs_dir=tmp_path)
    planner = next(event for event in report["timeline"] if event["layer"] == "planner")
    dumped = json.dumps(report, ensure_ascii=False)

    assert planner["semantic_output"]["fields"]["user_goal"] == "подобрать квартиру"
    assert planner["semantic_output"]["fields"]["selected_reference"] == "телефон [phone redacted]"
    assert "provider_prompt" not in dumped
    assert "SECRET PROMPT" not in dumped
    assert "raw_model_contact" not in dumped


def test_dialogue_phone_and_name_are_redacted(tmp_path: Path) -> None:
    session_ref = _ref("session-d")
    _write_jsonl(tmp_path / "dialogue_journal.jsonl", [{"ts": "2026-07-20T13:00:00Z", "role": "user", "session_key_ref": session_ref, "conversation_ref": session_ref, "event_type": "turn", "text": "Имя: Иван, телефон +79990000000"}])

    report = mod.build_scenario_report(session_ref=session_ref, date="2026-07-20", logs_dir=tmp_path)
    dumped = json.dumps(report, ensure_ascii=False)

    assert "Иван" not in dumped
    assert "+79990000000" not in dumped
    assert "[name redacted]" in dumped
    assert "[phone redacted]" in dumped


def test_contact_capture_redacts_standalone_name_after_operator_offer(tmp_path: Path) -> None:
    session_ref = _ref("contact-sequence")
    _write_jsonl(
        tmp_path / "dialogue_journal.jsonl",
        [
            {"ts": "2026-07-20T13:00:00Z", "role": "bot", "session_key_ref": session_ref, "conversation_ref": session_ref, "event_type": "turn", "answer_kind": "operator_offer", "text": "Как к вам обращаться?"},
            {"ts": "2026-07-20T13:00:01Z", "role": "user", "session_key_ref": session_ref, "conversation_ref": session_ref, "event_type": "turn", "text": "Денис"},
            {"ts": "2026-07-20T13:00:02Z", "role": "bot", "session_key_ref": session_ref, "conversation_ref": session_ref, "event_type": "turn", "answer_kind": "collect_contact_phone", "text": "Напишите номер"},
            {"ts": "2026-07-20T13:00:03Z", "role": "user", "session_key_ref": session_ref, "conversation_ref": session_ref, "event_type": "turn", "text": "+79990000000"},
            {"ts": "2026-07-20T13:00:04Z", "role": "bot", "session_key_ref": session_ref, "conversation_ref": session_ref, "event_type": "turn", "answer_kind": "callback_queued", "text": "Приняла, Денис."},
        ],
    )

    report = mod.build_scenario_report(session_ref=session_ref, date="2026-07-20", logs_dir=tmp_path)
    dumped = json.dumps(report, ensure_ascii=False)

    assert "Денис" not in dumped
    assert "+79990000000" not in dumped
    assert dumped.count("[contact redacted]") == 4


def test_gateway_failure_outranks_prior_nonfatal_composer_fallback(tmp_path: Path) -> None:
    session_ref = _ref("gateway-priority")
    _write_jsonl(
        tmp_path / "dialogue_journal.jsonl",
        [{"ts": "2026-07-20T13:10:00Z", "role": "bot", "session_key_ref": session_ref, "conversation_ref": session_ref, "event_type": "turn", "text": "Безопасный ответ", "response_composer": {"composer_used": False, "fallback_reason": "validation_failed"}}],
    )
    diagnosis = mod.build_diagnosis("2330033", {"status": "failed"}, {"status": "failed", "result": None, "error_message": "'data'"})

    report = mod.build_scenario_report(session_ref=session_ref, task_id="2330033", date="2026-07-20", logs_dir=tmp_path, gateway_diagnosis=diagnosis)

    assert report["verdict"] == {"stage": "gateway", "classification": "unknown_downstream", "evidence": {"error_code": "missing_response_field", "category": "downstream_contract"}}


def test_session_filter_avoids_unrelated_session_turns(tmp_path: Path) -> None:
    keep = _ref("keep")
    drop = _ref("drop")
    _write_jsonl(
        tmp_path / "dialogue_journal.jsonl",
        [
            {"ts": "2026-07-20T14:00:00Z", "role": "user", "session_key_ref": keep, "conversation_ref": keep, "event_type": "turn", "text": "нужный диалог"},
            {"ts": "2026-07-20T14:00:01Z", "role": "user", "session_key_ref": drop, "conversation_ref": drop, "event_type": "turn", "text": "чужой диалог"},
        ],
    )

    report = mod.build_scenario_report(session_ref=keep, date="2026-07-20", logs_dir=tmp_path)
    dumped = json.dumps(report, ensure_ascii=False)

    assert "нужный диалог" in dumped
    assert "чужой диалог" not in dumped


def test_no_task_query_scenario_mode_works(tmp_path: Path) -> None:
    session_ref = _ref("query-session")
    _write_jsonl(tmp_path / "dialogue_journal.jsonl", [{"ts": "2026-07-20T15:00:00Z", "role": "user", "session_key_ref": session_ref, "conversation_ref": session_ref, "event_type": "turn", "text": "ищу студию у метро"}])

    report = mod.build_scenario_report(query="студию", date="2026-07-20", logs_dir=tmp_path)

    assert report["scenario"]["task_id"] is None
    assert report["scenario"]["query_present"] is True
    assert [event["layer"] for event in report["timeline"]] == ["jivo_input"]
