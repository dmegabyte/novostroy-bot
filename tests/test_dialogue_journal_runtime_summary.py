from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPT_DIR / "dialogue_journal.py"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("dialogue_journal_test", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def test_runtime_summary_sanitizer_drops_raw_nested_unknowns(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "readable.log"))
    journal = tmp_path / "dialogue_journal.jsonl"

    event = mod.append_event(
        session_key="session-raw",
        role="bot",
        text="Ответ без секрета",
        runtime_summary={
            "stage": "first_list",
            "action": "search",
            "answer_kind": "search_results",
            "timing_ms": {"planner": 1, "execution": 2, "response": 3, "total": 6, "payload": 777},
            "call_counts": {"planner": 1, "search": 1, "selected_enrichment": 0, "gateway_attempts": 9, "scenario_search": 2, "answer": 9, "provider_retries": 99},
            "state_before": {"param_keys": ["rooms", "phone:+79991234567"], "visible_options_count": 99, "selected_present": False, "pending_followup": "", "active_topic": "family", "raw_params": {"rooms": 2}},
            "state_after": {"param_keys": ["rooms"], "visible_options_count": 3, "selected_present": True, "pending_followup": "financing_consent", "active_topic": "family", "selected_name": "Секретный ЖК"},
            "question_count": 1,
            "final_question_at_end": True,
            "quality_blockers": ["search_without_cards", "raw secret"],
            "grounding_scope": "grounded_true",
            "option_enrichment": {
                "availability_evidence": {
                    "requested": True,
                    "confirmation": "confirmed",
                    "source": "gateway",
                    "gateway_task_id": "task-2386206/unsafe suffix",
                    "inventory_value": 5242,
                    "raw_mcp_text": "секретный MCP payload",
                    "query": "наличие квартир secret",
                },
                "count": 1,
                "items": [{"name": "Секретный ЖК"}],
            },
            "intent_transition": {
                "goal": "compare_current",
                "intent_validation": "failed",
                "validation_error_codes": ["invalid_selected_option_scope", "raw_secret"],
                "transition": {"accepted": False, "error_code": "selected_option_not_in_visible_list", "selected_option_name": "Секретный ЖК"},
                "fallback_used": True,
                "raw_plan": {"query_text": "сравни с томилиским бульваром"},
            },
            "prompt": "raw prompt secret",
            "provider_response": "Секретный ЖК +7 999 123-45-67",
        },
        journal=journal,
    )

    row = json.loads(journal.read_text(encoding="utf-8"))
    assert row == event
    runtime = row["runtime_summary"]
    assert runtime["call_counts"] == {"planner": 1, "search": 1, "selected_enrichment": 0, "gateway_attempts": 5, "scenario_search": 2, "answer": 3}
    assert runtime["state_before"]["param_keys"] == ["rooms"]
    assert runtime["state_before"]["visible_options_count"] == 20
    assert runtime["quality_blockers"] == ["search_without_cards"]
    assert runtime["grounding_scope"] == "canonical_response_plan"
    assert runtime["option_enrichment"] == {
        "availability_evidence": {
            "requested": True,
            "confirmation": "confirmed",
            "source": "gateway",
            "gateway_task_id": "task-2386206_unsafe_suffix",
        }
    }
    assert runtime["intent_transition"] == {
        "goal": "compare_current",
        "intent_validation": "failed",
        "validation_error_codes": ["invalid_selected_option_scope"],
        "transition": {"accepted": False, "error_code": "selected_option_not_in_visible_list"},
        "fallback_used": True,
    }
    assert set(runtime["option_enrichment"]) == {"availability_evidence"}
    assert set(runtime["option_enrichment"]["availability_evidence"]) == {"requested", "confirmation", "source", "gateway_task_id"}
    dumped = json.dumps(row, ensure_ascii=False)
    for forbidden in ["raw prompt", "provider_response", "Секретный ЖК", "+7 999", "phone:+", "grounded_true", "provider_retries", "raw_params", "5242", "raw_mcp", "query", "items", "raw_secret", "raw_plan", "selected_option_name", "томилиским"]:
        assert forbidden not in dumped


def test_trace_ref_and_gateway_attempt_details_are_allowlisted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "readable.log"))
    journal = tmp_path / "dialogue_journal.jsonl"
    raw_uuid = "123e4567-e89b-12d3-a456-426614174000"

    valid = mod.append_event(
        session_key="session-trace",
        role="bot",
        text="Ответ",
        meta={"trace_ref": "trace_abcdef123456"},
        runtime_summary={
            "stage": "first_list",
            "action": "search",
            "gateway_attempt_details": [
                {"stage": "gateway_attempt", "model": "google/gemini", "ok": True, "empty": False, "safe": False, "gateway_task_id": "task-1", "duration_ms": 42, "parse_status": "ok", "raw": "secret"}
            ],
        },
        journal=journal,
    )
    invalid = mod.append_event(session_key="session-trace", role="bot", text="Ответ", meta={"trace_ref": raw_uuid}, journal=journal)

    assert valid["trace_ref"] == "trace_abcdef123456"
    assert "trace_ref" not in invalid
    attempts = valid["runtime_summary"]["gateway_attempt_details"]
    assert attempts == [{"stage": "gateway_attempt", "model": "google_gemini", "ok": True, "empty": False, "safe": False, "gateway_task_id": "task-1", "duration_ms": 42, "parse_status": "ok"}]
    dumped = journal.read_text(encoding="utf-8")
    assert raw_uuid not in dumped
    assert "secret" not in dumped


def test_runtime_version_sanitizer_persists_only_supported_uppercase(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "readable.log"))
    journal = tmp_path / "dialogue_journal.jsonl"

    valid = mod.append_event(session_key="s1", role="user", text="hi", runtime_version=" v2 ", journal=journal)
    invalid = mod.append_event(session_key="s1", role="bot", text="hi", runtime_version="V9", journal=journal)
    missing = mod.append_event(session_key="s1", role="bot", text="hi", journal=journal)

    assert valid["runtime_version"] == "V2"
    assert "runtime_version" not in invalid
    assert "runtime_version" not in missing


def test_response_model_sanitizer_persists_only_bounded_status_and_reason(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "readable.log"))
    journal = tmp_path / "dialogue_journal.jsonl"

    valid = mod.append_event(
        session_key="s1",
        role="bot",
        text="Published answer",
        response_model={"mode": "publish", "status": "valid", "model": "openai/gpt-5.5", "published": True, "candidate_sha256": "a" * 64, "candidate_chars": 42},
        journal=journal,
    )
    fallback = mod.append_event(
        session_key="s1",
        role="bot",
        text="Fallback answer",
        response_model={"mode": "publish", "status": "fallback", "model": "openai/gpt-5.5", "published": False, "reason": "one_model_validation_failed:money_not_grounded:123 млн SECRET"},
        journal=journal,
    )
    invalid = mod.append_event(
        session_key="s1",
        role="bot",
        text="Fallback answer",
        response_model={"mode": "publish", "status": "fallback", "published": False, "reason": "RuntimeError SECRET +7 999"},
        journal=journal,
    )

    assert valid["response_model"] == {"mode": "publish", "status": "valid", "published": True, "model": "openai/gpt-5.5"}
    assert fallback["response_model"] == {"mode": "publish", "status": "fallback", "published": False, "model": "openai/gpt-5.5", "reason": "one_model_validation_failed:money_not_grounded"}
    assert "response_model" not in invalid
    dumped = journal.read_text(encoding="utf-8")
    for forbidden in ["candidate_sha256", "candidate_chars", "123 млн", "SECRET", "+7 999"]:
        assert forbidden not in dumped


def test_release_id_sanitizer_persists_safe_token_or_unknown_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "readable.log"))
    journal = tmp_path / "dialogue_journal.jsonl"

    valid = mod.append_event(session_key="s1", role="user", text="hi", release_id=" rel_2026.07-22 ", journal=journal)
    unknown = mod.append_event(session_key="s1", role="bot", text="hi", release_id="UNKNOWN", journal=journal)
    invalid = mod.append_event(session_key="s1", role="bot", text="hi", release_id="../prod", journal=journal)

    assert valid["release_id"] == "rel_2026.07-22"
    assert unknown["release_id"] == "UNKNOWN"
    assert "release_id" not in invalid


def test_response_composer_attempt_diagnostic_sanitizer_is_allowlisted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "readable.log"))
    journal = tmp_path / "dialogue_journal.jsonl"

    event = mod.append_event(
        session_key="session-composer",
        role="bot",
        text="Ответ без секрета",
        response_composer={
            "composer_used": False,
            "fallback_reason": "validation_failed",
            "validation_stage": "semantic",
            "validation_codes": ["invalid_json", "raw secret"],
            "attempts": 1,
            "attempt_diagnostic": {
                "raw_type": "string",
                "raw_length": 999_999,
                "starts_object": True,
                "starts_fence": False,
                "ends_object": False,
                "gateway_task_id": "task-2386206/with unsafe suffix",
                "raw_text": "secret model text",
                "query": "secret query",
            },
            "semantic_diagnostics": [
                {"stage": "writer", "categories": ["numeric_not_in_canonical", "sensitive_claim", "secret"], "matched_text": "99 млн", "position": 8},
                {"stage": "other", "categories": ["numeric_not_in_canonical"]},
            ],
        },
        journal=journal,
    )

    diagnostic = event["response_composer"]["attempt_diagnostic"]
    assert diagnostic == {
        "raw_type": "string",
        "raw_length": 200_000,
        "starts_object": True,
        "starts_fence": False,
        "ends_object": False,
    }
    assert event["response_composer"]["semantic_diagnostics"] == [
        {"stage": "writer", "categories": ["numeric_not_in_canonical", "sensitive_claim"]}
    ]
    dumped = json.dumps(event, ensure_ascii=False)
    for forbidden in ("secret", "raw_text", "query", "99", "position", "gateway_task_id"):
        assert forbidden not in dumped


def test_prompt_provenance_persists_strict_schema_and_drops_secret_fields(tmp_path: Path, monkeypatch) -> None:
    from nmbot_v2.prompt_provenance import build_prompt_provenance, identity_from_text

    monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "readable.log"))
    journal = tmp_path / "dialogue_journal.jsonl"
    provenance = build_prompt_provenance([
        identity_from_text("search", "prompts/v2_search_mcp.txt", "prompt text"),
    ])
    provenance["prompts"][0]["prompt_body"] = "secret body"
    event = mod.append_event(
        session_key="session-provenance",
        role="bot",
        text="Ответ",
        prompt_provenance=provenance,
        journal=journal,
    )

    row = json.loads(journal.read_text(encoding="utf-8"))
    assert row == event
    assert row["prompt_provenance"]["schema"] == "nmbot.prompt_provenance.v1"
    assert row["prompt_provenance"]["prompts"][0]["source"] == "prompts/v2_search_mcp.txt"
    dumped = json.dumps(row, ensure_ascii=False)
    assert "prompt text" not in dumped
    assert "secret body" not in dumped

    invalid = mod.append_event(
        session_key="session-provenance",
        role="bot",
        text="Ответ",
        prompt_provenance={"schema": "wrong", "prompt_body": "secret"},
        journal=journal,
    )
    assert "prompt_provenance" not in invalid


def test_execution_path_is_optional_and_sanitized_for_old_and_new_rows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "readable.log"))
    journal = tmp_path / "dialogue_journal.jsonl"

    old = mod.append_event(session_key="session-old", role="bot", text="Ответ", journal=journal)
    new = mod.append_event(
        session_key="session-new",
        role="bot",
        text="Ответ",
        execution_path={
            "schema": "nmbot.execution_path.v1",
            "path_id": "jivo.v2.turn.v1",
            "stages": [
                {"stage_id": "v2.runtime_finalize", "status": "completed", "payload": "secret"},
                {"stage_id": "jivo.api.prepare", "status": "completed"},
                {"stage_id": "jivo.bridge.delivery", "status": "sent", "token": "secret"},
            ],
        },
        journal=journal,
    )

    assert "execution_path" not in old
    assert new["execution_path"] == {
        "schema": "nmbot.execution_path.v1",
        "path_id": "jivo.v2.turn.v1",
        "stages": [
            {"stage_id": "v2.runtime_finalize", "status": "completed"},
            {"stage_id": "jivo.api.prepare", "status": "completed"},
        ],
    }
    assert "secret" not in journal.read_text(encoding="utf-8")


def test_execution_path_sanitizer_rejects_invented_path_and_stage_ids(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "readable.log"))
    journal = tmp_path / "dialogue_journal.jsonl"

    invented_path = mod.append_event(
        session_key="session-invented-path",
        role="bot",
        text="Ответ",
        execution_path={
            "schema": "nmbot.execution_path.v1",
            "path_id": "evil.v2.turn.v1",
            "stages": [{"stage_id": "v2.runtime_finalize", "status": "completed"}],
        },
        journal=journal,
    )
    invented_stage = mod.append_event(
        session_key="session-invented-stage",
        role="bot",
        text="Ответ",
        execution_path={
            "schema": "nmbot.execution_path.v1",
            "path_id": "jivo.v2.turn.v1",
            "stages": [
                {"stage_id": "v2.runtime_finalize", "status": "completed"},
                {"stage_id": "jivo.bridge.delivery", "status": "completed", "token": "secret"},
                {"stage_id": "evil.stage", "status": "completed", "token": "secret"},
            ],
        },
        journal=journal,
    )

    assert "execution_path" not in invented_path
    assert invented_stage["execution_path"] == {
        "schema": "nmbot.execution_path.v1",
        "path_id": "jivo.v2.turn.v1",
        "stages": [{"stage_id": "v2.runtime_finalize", "status": "completed"}],
    }
    assert "secret" not in journal.read_text(encoding="utf-8")


def test_execution_path_sanitizer_enforces_path_membership_order_and_uniqueness(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "readable.log"))
    row = mod.append_event(
        session_key="session-stage-order",
        role="bot",
        text="Ответ",
        execution_path={
            "schema": "nmbot.execution_path.v1",
            "path_id": "v2.turn.v1",
            "stages": [
                {"stage_id": "v2.search", "status": "completed"},
                {"stage_id": "v2.planner", "status": "completed"},
                {"stage_id": "v2.search", "status": "failed"},
                {"stage_id": "jivo.api.prepare", "status": "completed"},
                {"stage_id": "v2.runtime_finalize", "status": "completed"},
            ],
        },
        journal=tmp_path / "dialogue_journal.jsonl",
    )

    assert row["execution_path"]["stages"] == [
        {"stage_id": "v2.search", "status": "completed"},
        {"stage_id": "v2.runtime_finalize", "status": "completed"},
    ]


def test_error_summary_is_allowlisted_and_records_clean_terminal_turn(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_READABLE_DIALOGUE_JOURNAL", str(tmp_path / "readable.log"))
    journal = tmp_path / "dialogue_journal.jsonl"

    clean = mod.append_event(
        session_key="session-clean",
        role="bot",
        text="Ответ",
        error_summary={"status": "ok", "codes": [], "stages": [], "fallback": False, "raw": "secret"},
        journal=journal,
    )
    degraded = mod.append_event(
        session_key="session-degraded",
        role="bot",
        text="Ответ",
        error_summary={
            "status": "degraded",
            "codes": ["composer_validation_failed", "invalid_json", "client_phone_7999"],
            "stages": ["composer", "raw_payload"],
            "fallback": True,
            "exception": "secret query",
        },
        journal=journal,
    )

    assert clean["error_summary"] == {"status": "ok", "codes": [], "stages": [], "fallback": False}
    assert degraded["error_summary"] == {
        "status": "degraded",
        "codes": ["composer_validation_failed", "invalid_json"],
        "stages": ["composer"],
        "fallback": True,
    }
    dumped = journal.read_text(encoding="utf-8")
    assert "secret" not in dumped
    assert "7999" not in dumped
