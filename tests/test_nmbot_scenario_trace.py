from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest


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


class _TaskUrlopen:
    def __init__(self, payloads: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.payloads = payloads
        self.urls: list[str] = []

    def __call__(self, request: Any, timeout: int = 15) -> Any:
        url = request.full_url
        self.urls.append(url)
        task_id, kind = url.rstrip("/").split("/")[-2:]
        if (task_id, kind) not in self.payloads:
            raise mod.urllib.error.HTTPError(url, 404, "missing", {}, None)
        return _Response(self.payloads[(task_id, kind)])


class _Response:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    def read(self) -> bytes:
        return json.dumps(self.value).encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


def test_evidence_chain_matches_bounded_child_and_redacts_raw_gateway_content(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "dialogue_journal.jsonl",
        [
            {"ts": "2026-07-20T10:00:00Z", "role": "user", "meta": {"trace_ref": "trace_x"}, "text": "Ищу квартиру"},
            {"ts": "2026-07-20T10:00:10Z", "role": "bot", "meta": {"trace_ref": "trace_x"}, "release_id": "release-1", "answer_kind": "search_answer", "text": "Цена 10 млн, рядом метро", "runtime_summary": {"action": "search", "stage": "main_search", "call_counts": {"gateway_attempts": 2}, "gateway_attempt_details": [{"gateway_task_id": "100"}]}},
        ],
    )
    fake = _TaskUrlopen({
        ("100", "status"): {"status": "completed"},
        ("100", "result"): {"status": "completed", "result": {"cards": [{"id": "a1", "name": "ЖК Тест", "price": 10000000, "finishing": 1, "url": "https://secret.test"}]}},
        ("101", "result"): {"completed_at": "2026-07-20T10:00:09Z", "result": {"cards": [{"id": "a1", "school": "есть", "contacts": "+79990000000"}]}},
    })
    report = mod.build_evidence_chain_report("trace_x", date="2026-07-20", logs_dir=tmp_path, config=mod.GatewayConfig("https://example.test", "token"), urlopen=fake)
    dumped = json.dumps(report, ensure_ascii=False)

    assert report["schema_version"] == "nmbot.evidence_chain.v1"
    assert report["accepted_child_tasks"][0]["task_id"] == "101"
    assert report["cards"][0]["fields_added_by_child"] == {"school": "есть"}
    assert any(item["category"] == "dropped_by_normalizer" and item["field"] == "finishing" for item in report["candidate_conflicts"])
    assert "secret.test" not in dumped and "79990000000" not in dumped and "token" not in dumped
    assert len([url for url in fake.urls if url.endswith("/result")]) <= 7


def test_evidence_chain_missing_child_is_candidate_not_failure(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "dialogue_journal.jsonl", [{"ts": "2026-07-20T10:00:10Z", "role": "bot", "trace_ref": "trace_missing", "text": "Цена есть", "runtime_summary": {"stage": "main_search", "call_counts": {"gateway_attempts": 2}, "gateway_attempt_details": [{"gateway_task_id": "200"}]}}])
    fake = _TaskUrlopen({("200", "status"): {"status": "completed"}, ("200", "result"): {"status": "completed", "result": {"cards": [{"id": "a1", "price": 1}]}}})
    report = mod.build_evidence_chain_report("trace_missing", date="2026-07-20", logs_dir=tmp_path, config=mod.GatewayConfig("https://example.test", "token"), urlopen=fake)

    assert report["accepted_child_tasks"] == []
    assert any(item["category"] == "missing_lineage" for item in report["candidate_conflicts"])
    assert report["first_divergence"]["owner"] == "search_enrichment"


def test_evidence_chain_parses_result_response_json_and_scopes_claims_per_card(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "dialogue_journal.jsonl",
        [
            {"ts": "2026-07-20T10:00:00Z", "role": "user", "trace_ref": "trace_response_string", "text": "Двушка для семьи"},
            {
                "ts": "2026-07-20T10:00:10Z",
                "role": "bot",
                "trace_ref": "trace_response_string",
                "answer_kind": "main_search",
                "text": "1. ЖК «Первый». Рядом школа.\n2. ЖК «Второй». Здесь двор без машин.",
                "runtime_summary": {
                    "stage": "first_list",
                    "action": "search",
                    "call_counts": {"gateway_attempts": 2},
                    "gateway_attempt_details": [{"gateway_task_id": "300"}],
                },
            },
        ],
    )
    primary_response = json.dumps({
        "facts": [
            {"id": 1, "name": "Первый", "min_price": 10, "delivered": 1},
            {"id": 2, "name": "Второй", "min_price": 20, "delivered": 1},
        ]
    }, ensure_ascii=False)
    first_child = json.dumps({"facts": [{"id": 1, "name": "Первый", "school": 1}]}, ensure_ascii=False)
    second_child = json.dumps({"facts": [{"id": 2, "name": "Второй", "yard_without_cars": 1}]}, ensure_ascii=False)
    fake = _TaskUrlopen({
        ("300", "status"): {"status": "completed"},
        ("300", "result"): {"status": "completed", "result": {"response": primary_response}},
        ("301", "result"): {"completed_at": "2026-07-20T10:00:08Z", "result": {"response": first_child}},
        ("302", "result"): {"completed_at": "2026-07-20T10:00:09Z", "result": {"response": second_child}},
    })

    report = mod.build_evidence_chain_report(
        "trace_response_string",
        date="2026-07-20",
        logs_dir=tmp_path,
        config=mod.GatewayConfig("https://example.test", "token"),
        urlopen=fake,
    )

    assert report["lineage_coverage"]["primary_cards"] == 2
    assert [item["task_id"] for item in report["accepted_child_tasks"]] == ["301", "302"]
    by_ref = {str(card["card_ref"]): card for card in report["cards"]}
    assert by_ref["1"]["public_claim_fields"] == {"school": True}
    assert by_ref["2"]["public_claim_fields"] == {"yard_without_cars": True}
    assert not any(item["category"] == "unsupported_public_claim" for item in report["candidate_conflicts"])


def _evidence_chain_identity_report(
    tmp_path: Path,
    *,
    primary_cards: list[dict[str, Any]],
    child_card: dict[str, Any],
    child_timestamp: Any = "2026-07-20T10:00:09Z",
) -> dict[str, Any]:
    _write_jsonl(
        tmp_path / "dialogue_journal.jsonl",
        [
            {"ts": "2026-07-20T10:00:00Z", "role": "user", "trace_ref": "trace_identity", "text": "Ищу квартиру"},
            {"ts": "2026-07-20T10:00:10Z", "role": "bot", "trace_ref": "trace_identity", "answer_kind": "main_search", "text": "Нашла варианты", "runtime_summary": {"stage": "first_list", "call_counts": {"gateway_attempts": 2}, "gateway_attempt_details": [{"gateway_task_id": "400"}]}},
        ],
    )
    child_result: dict[str, Any] = {"result": {"cards": [child_card]}}
    if child_timestamp is not None:
        child_result["completed_at"] = child_timestamp
    fake = _TaskUrlopen({
        ("400", "status"): {"status": "completed"},
        ("400", "result"): {"status": "completed", "result": {"cards": primary_cards}},
        ("401", "result"): child_result,
    })
    return mod.build_evidence_chain_report(
        "trace_identity",
        date="2026-07-20",
        logs_dir=tmp_path,
        config=mod.GatewayConfig("https://example.test", "token"),
        urlopen=fake,
    )


@pytest.mark.parametrize("child_timestamp", [None, "not-a-timestamp"])
def test_evidence_chain_rejects_child_without_parseable_timestamp(tmp_path: Path, child_timestamp: Any) -> None:
    report = _evidence_chain_identity_report(
        tmp_path,
        primary_cards=[{"id": "a1", "name": "Первый"}],
        child_card={"id": "a1", "name": "Первый", "school": 1},
        child_timestamp=child_timestamp,
    )

    assert report["accepted_child_tasks"] == []


def test_evidence_chain_rejects_conflicting_explicit_id_even_when_name_matches(tmp_path: Path) -> None:
    report = _evidence_chain_identity_report(
        tmp_path,
        primary_cards=[{"id": "a1", "name": "Первый"}],
        child_card={"id": "other", "name": "Первый", "school": 1},
    )

    assert report["accepted_child_tasks"] == []


def test_evidence_chain_rejects_duplicate_name_without_id(tmp_path: Path) -> None:
    report = _evidence_chain_identity_report(
        tmp_path,
        primary_cards=[{"id": "a1", "name": "Одинаковый"}, {"id": "a2", "name": "Одинаковый"}],
        child_card={"name": "Одинаковый", "school": 1},
    )

    assert report["accepted_child_tasks"] == []


def test_evidence_chain_redacts_callback_public_response_with_bare_name(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "dialogue_journal.jsonl",
        [
            {"ts": "2026-07-20T10:00:00Z", "role": "user", "trace_ref": "trace_callback", "text": "+79990000000"},
            {"ts": "2026-07-20T10:00:01Z", "role": "bot", "trace_ref": "trace_callback", "answer_kind": "callback_queued", "text": "Приняла, Денис.", "runtime_summary": {}},
        ],
    )

    report = mod.build_evidence_chain_report(
        "trace_callback",
        date="2026-07-20",
        logs_dir=tmp_path,
        config=mod.GatewayConfig("https://example.test", "token"),
        urlopen=_TaskUrlopen({}),
    )
    dumped = json.dumps(report, ensure_ascii=False)

    assert report["public_response"] == "[contact redacted]"
    assert "Денис" not in dumped
