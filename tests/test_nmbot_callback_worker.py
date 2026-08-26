from __future__ import annotations

from pathlib import Path

import pytest

from scripts.nmbot_callback_sheet_worker import CallbackSheetWorker, _validate_summary_provider
from scripts import nmbot_callback_crm_control as crm_control
from scripts.nmbot_callback_crm import CallbackCRMAdapter
from scripts.nmbot_callback_summary import DeterministicSummaryProvider, build_sanitized_summary_input
from scripts.nmbot_crm_outbox import LocalCallbackOutbox
from scripts.nmbot_google_sheets import ConfigurationError, FakeCallbackSheetAdapter


def test_summary_input_is_redacted_and_deterministic() -> None:
    record = {
        "lead_ref": "cb_private",
        "session_ref": "session_private",
        "contact": {"name": "Анна", "phone": "+79991234567"},
        "summary_input": {
            "params": {"rooms": 2},
            "last_bot_question": "Анна, позвонить на +79991234567?",
        },
    }
    snapshot = build_sanitized_summary_input(record)
    rendered = str(snapshot)
    assert "Анна" not in rendered and "79991234567" not in rendered
    assert DeterministicSummaryProvider().summarize(snapshot)


def test_non_deterministic_summary_provider_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_CALLBACK_SUMMARY_PROVIDER", "remote")
    with pytest.raises(ConfigurationError, match="invalid_callback_summary_provider"):
        _validate_summary_provider()


def test_worker_delivers_private_outbox_once_without_google_network(tmp_path: Path) -> None:
    outbox = LocalCallbackOutbox(tmp_path / "outbox")
    queued = outbox.enqueue_callback(
        session_key="session",
        event_id="event",
        contact_name="Анна",
        normalized_phone="+79991234567",
        context={"runtime": "V6"},
        summary_input={"params": {"rooms": 2}, "last_bot_question": "Уточнить район?"},
        provenance={"runtime_version": "V6", "release_id": "v6-clean-r1", "contour": "TEST"},
    )
    sheet = FakeCallbackSheetAdapter()
    worker = CallbackSheetWorker(
        outbox=outbox,
        summary_provider=DeterministicSummaryProvider(),
        sheet_adapter=sheet,
        crm_adapter=None,
        retry_base_seconds=0,
    )
    first = worker.process_once()
    second = worker.process_once()
    assert queued.status == "queued"
    assert first["status"] == "sheet_delivered"
    assert second["status"] == "idle"
    assert len(sheet.rows) == 1


def test_crm_kill_switch_is_rechecked_immediately_before_send(tmp_path: Path, monkeypatch) -> None:
    control_path = tmp_path / "crm-control.json"
    crm_control.write_control(control_path, contour="PROD", enabled=True)
    monkeypatch.setenv("NMBOT_CALLBACK_CRM_CONTROL_FILE", str(control_path))
    outbox = LocalCallbackOutbox(tmp_path / "outbox")
    outbox.enqueue_callback(
        session_key="prod-session",
        event_id="prod-event",
        contact_name="Анна",
        normalized_phone="+79991234567",
        context={"runtime": "V6"},
        provenance={"runtime_version": "V6", "release_id": "v6-clean-r1", "contour": "PROD"},
    )
    calls: list[str] = []
    adapter = CallbackCRMAdapter(endpoint="https://crm.example/callback", transport=lambda *_args: (calls.append("sent") or (200, b'{"ok":true}')))
    worker = CallbackSheetWorker(
        outbox=outbox,
        summary_provider=DeterministicSummaryProvider(),
        sheet_adapter=FakeCallbackSheetAdapter(),
        crm_adapter=adapter,
    )
    crm_control.write_control(control_path, contour="PROD", enabled=False)

    result = worker._process_crm_once()

    assert result["status"] == "crm_disabled"
    assert calls == []
    records = outbox.iter_due_records(sink="crm")
    assert records == []
