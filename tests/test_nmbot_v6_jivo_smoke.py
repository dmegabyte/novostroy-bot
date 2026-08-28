from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import nmbot_v6_jivo_smoke as smoke


def _bot(**overrides):
    value = {
        "role": "bot",
        "release_id": "v6-test-r1",
        "runtime_diagnostic": {
            "status": "completed",
            "state_commit": True,
            "trace": {"stages": [
                {"stage": "prompt1", "status": "accepted"},
                {"stage": "mcp", "status": "unknown"},
                {"stage": "prompt2", "status": "accepted"},
                {"stage": "state", "status": "accepted"},
                {"stage": "bot_message", "status": "prepared"},
            ]},
        },
        "error_summary": {"status": "ok"},
    }
    value.update(overrides)
    return value


def _write_test_route(path: Path, *, slot: str = "B", release_id: str = "v6-test-r1", port: int = 18089) -> None:
    path.write_text(json.dumps({
        "schema": "nmbot.active_route.v1",
        "profile": "TEST",
        "revision": 1,
        "active": {"slot": slot, "release_id": release_id, "upstream": f"http://127.0.0.1:{port}"},
        "previous": None,
        "switched_at": "2026-08-28T10:15:19Z",
    }), encoding="utf-8")


def test_isolated_target_is_release_bound_and_has_no_free_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    route = tmp_path / "test.json"
    data_root = tmp_path / "data"
    _write_test_route(route)
    monkeypatch.setattr(smoke, "CANONICAL_TEST_ROUTE", route)
    monkeypatch.setattr(smoke, "CANONICAL_TEST_DATA_ROOT", data_root)
    contract = smoke.target_contract("isolated-test", "v6-test-r1")
    assert contract.api_base == "http://127.0.0.1:18089"
    assert contract.profile == "TEST"
    assert contract.journal == data_root / "dialogue/dialogue.jsonl"
    with pytest.raises(smoke.SmokeError, match="expected_release_required"):
        smoke.target_contract("isolated-test", None)
    with pytest.raises(smoke.SmokeError, match="expected_release_invalid"):
        smoke.target_contract("isolated-test", "../../primary")
    with pytest.raises(smoke.SmokeError, match="target_not_allowed"):
        smoke.target_contract("custom-path", "v6-test-r1")
    assert smoke._bridge_base("http://127.0.0.1:8093/") == smoke.BRIDGE_BASE
    with pytest.raises(smoke.SmokeError, match="bridge_base_not_allowed"):
        smoke._bridge_base("https://example.invalid")
    assert smoke._bridge_log_paths({}) == (smoke.BRIDGE_LOG_PATH, smoke.BRIDGE_LOG_DEFAULT)
    assert smoke._bridge_log_paths({"NMBOT_BRIDGE_STRUCTURED_LOG": str(smoke.BRIDGE_LOG_PATH)}) == (smoke.BRIDGE_LOG_PATH, smoke.BRIDGE_LOG_DEFAULT)
    with pytest.raises(smoke.SmokeError, match="bridge_log_path_not_allowed"):
        smoke._bridge_log_paths({"NMBOT_BRIDGE_STRUCTURED_LOG": "/tmp/free-path.jsonl"})


@pytest.mark.parametrize(("slot", "port"), [("A", 18088), ("B", 18089)])
def test_canonical_test_route_accepts_only_controller_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slot: str, port: int,
) -> None:
    route = tmp_path / "test.json"
    _write_test_route(route, slot=slot, port=port)
    monkeypatch.setattr(smoke, "CANONICAL_TEST_ROUTE", route)
    assert smoke.target_contract("isolated-test", "v6-test-r1").api_base == f"http://127.0.0.1:{port}"

    _write_test_route(route, slot=slot, port=19000)
    with pytest.raises(smoke.SmokeError, match="test_route_invalid"):
        smoke.target_contract("isolated-test", "v6-test-r1")

    route.unlink()
    target = tmp_path / "real.json"
    _write_test_route(target, slot=slot, port=port)
    route.symlink_to(target)
    with pytest.raises(smoke.SmokeError, match="test_route_unsafe"):
        smoke.target_contract("isolated-test", "v6-test-r1")


def test_journal_reader_is_offset_bounded_and_rejects_symlink(tmp_path: Path) -> None:
    chat_id = "test-chat"
    journal = tmp_path / "dialogue.jsonl"
    journal.write_text(json.dumps({"chat_ref": "old", "role": "bot"}) + "\n", encoding="utf-8")
    offset = journal.stat().st_size
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"chat_ref": smoke._chat_ref(chat_id), "role": "bot"}) + "\n")
    assert smoke._read_chat_events(chat_id, journal=journal, offset=offset, root=tmp_path) == [
        {"chat_ref": smoke._chat_ref(chat_id), "role": "bot"}
    ]
    assert smoke._chat_ref(chat_id).startswith("chat_") and len(smoke._chat_ref(chat_id)) == 25
    link = tmp_path / "link.jsonl"
    link.symlink_to(journal)
    with pytest.raises(smoke.SmokeError, match="evidence_file_unsafe"):
        smoke._read_chat_events(chat_id, journal=link, root=tmp_path)


def test_release_smoke_accepts_current_journal_contract_and_rejects_failure() -> None:
    accepted, failures = smoke.evaluate_release_smoke(
        query_result={"ok": True, "http_status": 200}, events=[_bot()], expected_release="v6-test-r1"
    )
    assert accepted and failures == []
    accepted, failures = smoke.evaluate_release_smoke(
        query_result={"ok": True, "http_status": 200},
        events=[_bot(runtime_diagnostic={"status": "technical_failure", "state_commit": False, "trace": {"stages": []}})],
        expected_release="v6-test-r1",
    )
    assert not accepted and failures == ["runtime_not_accepted"]


def test_release_smoke_accepts_v6_simple_trace() -> None:
    accepted, failures = smoke.evaluate_release_smoke(
        query_result={"ok": True, "http_status": 200},
        events=[_bot()],
        expected_release="v6-test-r1",
    )
    assert accepted and failures == []
    accepted, failures = smoke.evaluate_release_smoke(
        query_result={"ok": True, "http_status": 200},
        events=[_bot(release_id="another-release")],
        expected_release="v6-test-r1",
    )
    assert not accepted and failures == ["journal_release_mismatch"]


def test_bridge_trace_requires_exact_upstream_and_terminal_delivery() -> None:
    upstream = smoke._safe_url_ref("http://127.0.0.1:18089/jivo/private-token")
    common = {"trace_id": "trace-1", "event": "nmbot_jivo_n8n_bridge"}
    events = [
        {**common, "stage": "upstream_request_start", "upstream_ref": upstream},
        {**common, "stage": "upstream_response", "http_status": 200, "response_event": "BOT_MESSAGE"},
        {**common, "stage": "egress_guard", "outcome": "passed", "delivery_role": "final"},
        {**common, "stage": "jivo_response_returned", "http_status": 200, "response_event": "BOT_MESSAGE", "terminal": True, "delivery_role": "final", "delivery_status": "sent"},
    ]
    accepted, failures, receipt = smoke.evaluate_bridge_trace(events=events, expected_upstream_ref=upstream)
    assert accepted and failures == []
    assert receipt == {
        "accepted": True,
        "trace_ref": smoke._bridge_ref("trace-1"),
        "target_upstream_verified": True,
        "upstream_http_status": 200,
        "egress_guard_passed": True,
        "terminal_http_status": 200,
    }
    accepted, failures, _ = smoke.evaluate_bridge_trace(events=events, expected_upstream_ref="wrong")
    assert not accepted and failures == ["bridge_target_not_proven"]
    accepted, failures, _ = smoke.evaluate_bridge_trace(events=events[:-1], expected_upstream_ref=upstream)
    assert not accepted and failures == ["terminal_jivo_send_missing"]


def test_bridge_trace_accepts_legacy_terminal_receipt() -> None:
    upstream = "upstream-ref"
    common = {"trace_id": "trace-legacy", "event": "nmbot_jivo_n8n_bridge"}
    events = [
        {**common, "stage": "upstream_request_start", "upstream_ref": upstream},
        {**common, "stage": "upstream_response", "http_status": 200, "response_event": "BOT_MESSAGE"},
        {**common, "stage": "egress_guard", "outcome": "passed"},
        {**common, "stage": "jivo_response_returned", "http_status": 200, "response_event": "BOT_MESSAGE", "outcome": "terminal_send_accepted"},
    ]
    accepted, failures, _ = smoke.evaluate_bridge_trace(events=events, expected_upstream_ref=upstream)
    assert accepted and failures == []


def test_bridge_reader_accepts_live_client_message_record_without_schema_label(tmp_path: Path) -> None:
    chat_id = "test-chat"
    event_id_ref = smoke._bridge_ref("event-1")
    log = tmp_path / "bridge.jsonl"
    log.write_text(json.dumps({
        "event": "CLIENT_MESSAGE",
        "event_id_ref": event_id_ref,
        "chat_id_ref": smoke._bridge_ref(chat_id),
        "stage": "upstream_request_start",
    }) + "\n", encoding="utf-8")
    assert smoke._read_bridge_events(
        event_id_ref=event_id_ref,
        chat_id=chat_id,
        offset=0,
        path=log,
    )[0]["stage"] == "upstream_request_start"


def test_strict_smoke_wait_default_covers_bounded_terminal_delivery() -> None:
    args = smoke._parser().parse_args(["--target", "isolated-test", "--expected-release", "v6-test-r1"])
    assert args.journal_wait_seconds == 45.0
