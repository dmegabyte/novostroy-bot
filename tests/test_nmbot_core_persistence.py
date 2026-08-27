from __future__ import annotations

import json
import stat

from nmbot_core import LocalCallbackOutbox, append_event


def test_journal_is_private_opaque_and_v6_only(tmp_path) -> None:
    target = tmp_path / "private" / "dialogue.jsonl"
    row = append_event("jivo:secret", "user", "Позвоните +7 999 123-45-67 или x@example.test", event_id="event-secret", refs={"site_id": "site", "chat_id": "chat", "client_id": "client"}, release_id="v6-canonical-r1", path=target)
    rendered = target.read_text(encoding="utf-8")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(rendered) == row
    assert {"session_ref", "event_ref", "site_ref", "chat_ref", "client_ref", "text_sha256"} <= row.keys()
    for value in ("79991234567", "x@example.test", "event-secret", "jivo:secret"):
        assert value not in rendered


def test_outbox_is_private_idempotent_and_public_result_is_opaque(tmp_path) -> None:
    outbox = LocalCallbackOutbox(tmp_path / "outbox")
    arguments = {"session_key": "chat-secret", "event_id": "event-secret", "normalized_phone": "+79991234567", "context": {"runtime": "V6", "phone": "+79991234567", "selected": "ЖК А"}}
    first, second = outbox.enqueue(**arguments), outbox.enqueue(**arguments)
    assert first.status == "queued" and second.status == "duplicate"
    assert first.public() == {"status": "queued", "lead_ref": first.lead_ref}
    records = list((tmp_path / "outbox").glob("*.json"))
    assert len(records) == 1 and stat.S_IMODE(records[0].stat().st_mode) == 0o600
    private = json.loads(records[0].read_text(encoding="utf-8"))
    assert private["contact"]["phone"] == "+79991234567"
    assert "phone" not in private["context"]
