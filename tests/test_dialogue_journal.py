from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from scripts.dialogue_journal import DialogueJournalError, append_event


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
