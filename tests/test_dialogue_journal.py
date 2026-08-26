from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from scripts.dialogue_journal import DialogueJournalError, append_event
from scripts import nmbot_v6_api as api_module


def test_journal_is_private_opaque_and_attributes_v6(tmp_path: Path) -> None:
    target = tmp_path / "private" / "dialogue.jsonl"
    row = append_event(
        "jivo:site:chat:client",
        "user",
        "Позвоните +7 999 123-45-67 или client@example.test",
        event_id="event-secret",
        meta={"site_id": "site-secret", "chat_id": "chat-secret", "client_id": "client-secret"},
        answer_kind="reply",
        runtime_version="V6",
        path=target,
    )

    rendered = target.read_text(encoding="utf-8")
    persisted = json.loads(rendered)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert row == persisted
    assert persisted["runtime_version"] == "V6"
    assert persisted["text_chars"] > 0 and len(persisted["text_sha256"]) == 64
    assert {"session_ref", "event_ref", "site_ref", "chat_ref", "client_ref"} <= persisted.keys()
    for secret in ("79991234567", "client@example.test", "site-secret", "chat-secret", "client-secret", "event-secret"):
        assert secret not in rendered


def test_bridge_error_shape_is_bounded_and_foreign_runtime_rejected(tmp_path: Path) -> None:
    target = tmp_path / "journal.jsonl"
    row = append_event(
        "opaque-session",
        "system",
        event_type="delivery_error",
        error_summary={
            "status": "failed",
            "codes": ["bridge_route_unavailable"],
            "stages": ["bridge_upstream"],
            "fallback": True,
        },
        source="bridge",
        path=target,
    )
    assert row["error_summary"] == {
        "status": "failed",
        "codes": ["bridge_route_unavailable"],
        "stages": ["bridge_upstream"],
        "fallback": True,
    }
    with pytest.raises(DialogueJournalError, match="exactly V6"):
        append_event("session", "system", runtime_version=f"V{6 + 1}", path=target)


def test_runtime_diagnostic_keeps_stages_but_discards_raw_fields(tmp_path: Path) -> None:
    target = tmp_path / "journal.jsonl"
    raw_phone = "+7 999 123-45-67"
    row = append_event(
        "opaque-session",
        "bot",
        text="Технический ответ",
        runtime_diagnostic={
            "status": "technical_failure",
            "failure_stage": "state",
            "error_code": "state_save_failure",
            "error_field": "private.payload.field",
            "state_commit": False,
            "model_calls": 2,
            "raw_dialogue": f"Позвоните {raw_phone}",
            "v6_trace": {
                "schema_version": 1,
                "stages": [
                    {"stage": "prompt1", "status": "accepted", "attempt_ref": f"task:{raw_phone}"},
                    {"stage": "mcp", "status": "unknown"},
                    {"stage": "state", "status": "failed"},
                    {"stage": "invented", "status": "accepted", "raw": "secret"},
                ],
            },
        },
        path=target,
    )

    diagnostic = row["runtime_diagnostic"]
    assert diagnostic["status"] == "technical_failure"
    assert diagnostic["failure_stage"] == "state"
    assert diagnostic["error_code"] == "state_save_failure"
    assert diagnostic["state_commit"] is False
    assert diagnostic["model_calls"] == 2
    assert diagnostic["error_field_ref"].startswith("field_")
    assert [item["stage"] for item in diagnostic["trace"]["stages"]] == ["prompt1", "mcp", "state"]
    assert "attempt_ref" not in diagnostic["trace"]["stages"][0]
    rendered = target.read_text(encoding="utf-8")
    assert "raw_dialogue" not in rendered
    assert raw_phone not in rendered
    assert "private.payload.field" not in rendered


def test_jivo_result_persists_runtime_diagnostic_without_raw_dialogue(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "dialogue.jsonl"
    monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(target))
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "TEST")

    async def failed_turn(app, *, user_id, message, channel, meta):
        return {
            "ok": False,
            "answer": "Технический ответ",
            "intent": "v6",
            "awaiting_phone": False,
            "handoff_to_operator": False,
            "meta": {
                "runtime": "v6",
                "status": "technical_failure",
                "failure_stage": "state",
                "error_code": "state_save_failure",
                "v6_trace": {
                    "schema_version": 1,
                    "stages": [
                        {"stage": "prompt1", "status": "accepted", "attempt_ref": "task-123"},
                        {"stage": "mcp", "status": "unknown"},
                        {"stage": "prompt2", "status": "accepted", "attempt_ref": "task-456"},
                        {"stage": "state", "status": "failed"},
                        {"stage": "bot_message", "status": "prepared"},
                    ],
                },
            },
        }

    monkeypatch.setattr(api_module, "run_v6_simple_turn", failed_turn)

    async def run() -> None:
        async with TestClient(TestServer(api_module.create_app())) as client:
            response = await client.post(
                "/jivo/provider",
                json={
                    "event": "CLIENT_MESSAGE",
                    "id": "event-1",
                    "site_id": "site-1",
                    "chat_id": "chat-1",
                    "client_id": "client-1",
                    "message": {"type": "TEXT", "text": "Сырой синтетический запрос"},
                },
            )
            assert response.status == 200

    asyncio.run(run())
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    diagnostic = rows[1]["runtime_diagnostic"]
    assert diagnostic["status"] == "technical_failure"
    assert diagnostic["failure_stage"] == "state"
    assert diagnostic["error_code"] == "state_save_failure"
    assert diagnostic["awaiting_phone"] is False
    assert diagnostic["handoff_to_operator"] is False
    assert diagnostic["trace"]["stages"][0]["attempt_ref"] == "task-123"
    assert diagnostic["trace"]["stages"][2]["attempt_ref"] == "task-456"
    assert [item["status"] for item in diagnostic["trace"]["stages"]] == [
        "accepted", "unknown", "accepted", "failed", "prepared",
    ]
    rendered = target.read_text(encoding="utf-8")
    assert "Сырой синтетический запрос" not in rendered
    assert "Технический ответ" not in rendered
