from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from nmbot_callback_crm import CRMResult, CallbackCRMAdapter
from nmbot_callback_crm_control import read_control, write_control
from nmbot_callback_sheet_worker import CallbackSheetWorker
from nmbot_crm_outbox import LocalCallbackOutbox
from nmbot_google_sheets import AppendResult, FakeCallbackSheetAdapter


class StaticSummary:
    def summarize(self, snapshot: dict[str, Any]) -> str:
        return "Клиент просит связаться по текущему подбору."


class FakeCRM:
    def __init__(self, result: CRMResult) -> None:
        self.result = result
        self.calls = 0

    def send_callback(self, **kwargs: str) -> CRMResult:
        self.calls += 1
        return self.result


def _record(root: Path) -> dict[str, Any]:
    path = next(root.glob("*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def _enqueue(outbox: LocalCallbackOutbox, event: str = "e") -> str:
    return outbox.enqueue_callback(
        session_key="session",
        event_id=event,
        contact_name="Иван",
        normalized_phone="+79991234567",
        context={"params": {"rooms": 2}},
        provenance={"contour": "TEST", "runtime_version": "V2", "release_id": "rel-1", "experiment_id": ""},
    ).lead_ref


def test_missing_control_is_off_and_adapter_not_called(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "TEST")
    monkeypatch.delenv("NMBOT_CALLBACK_CRM_CONTROL_FILE", raising=False)
    outbox = LocalCallbackOutbox(tmp_path / "outbox")
    _enqueue(outbox)
    crm = FakeCRM(CRMResult(status="ok"))
    worker = CallbackSheetWorker(outbox=outbox, summary_provider=StaticSummary(), sheet_adapter=FakeCallbackSheetAdapter(), crm_adapter=crm, retry_base_seconds=0)

    worker.process_once()

    assert crm.calls == 0
    assert _record(tmp_path / "outbox")["crm_delivery"]["status"] == "disabled"


def test_enabling_applies_only_to_new_leads(tmp_path: Path, monkeypatch) -> None:
    control = tmp_path / "control.json"
    monkeypatch.setenv("NMBOT_CALLBACK_CRM_CONTROL_FILE", str(control))
    outbox = LocalCallbackOutbox(tmp_path / "outbox")
    _enqueue(outbox, "off")
    write_control(control, contour="TEST", enabled=True)
    _enqueue(outbox, "on")
    statuses = {record["event_ref"]: record["crm_delivery"]["status"] for record in [json.loads(p.read_text()) for p in (tmp_path / "outbox").glob("*.json")]}

    assert sorted(statuses.values()) == ["disabled", "pending_summary"]


def test_sheet_failure_does_not_block_crm_and_crm_delivery_does_not_repeat(tmp_path: Path, monkeypatch) -> None:
    control = tmp_path / "control.json"
    write_control(control, contour="TEST", enabled=True)
    monkeypatch.setenv("NMBOT_CALLBACK_CRM_CONTROL_FILE", str(control))
    outbox = LocalCallbackOutbox(tmp_path / "outbox")
    _enqueue(outbox)
    crm = FakeCRM(CRMResult(status="ok", receipt="opaque-1"))
    sheet = FakeCallbackSheetAdapter(fail=AppendResult(status="failed", error_class="sheet_terminal"))
    worker = CallbackSheetWorker(outbox=outbox, summary_provider=StaticSummary(), sheet_adapter=sheet, crm_adapter=crm, max_attempts=1, retry_base_seconds=0)

    assert worker.process_once()["status"] == "failed"
    record = _record(tmp_path / "outbox")
    assert record["sheet_delivery"]["status"] == "failed"
    assert record["crm_delivery"]["status"] == "crm_delivered"
    worker.process_once()
    assert crm.calls == 1


def test_crm_terminal_failure_does_not_block_sheet(tmp_path: Path, monkeypatch) -> None:
    control = tmp_path / "control.json"
    write_control(control, contour="TEST", enabled=True)
    monkeypatch.setenv("NMBOT_CALLBACK_CRM_CONTROL_FILE", str(control))
    outbox = LocalCallbackOutbox(tmp_path / "outbox")
    _enqueue(outbox)
    sheet = FakeCallbackSheetAdapter()
    crm = FakeCRM(CRMResult(status="failed", error_class="crm_http_403"))
    worker = CallbackSheetWorker(outbox=outbox, summary_provider=StaticSummary(), sheet_adapter=sheet, crm_adapter=crm, max_attempts=1, retry_base_seconds=0)

    assert worker.process_once()["status"] == "sheet_delivered"
    record = _record(tmp_path / "outbox")
    assert record["sheet_delivery"]["status"] == "sheet_delivered"
    assert record["crm_delivery"]["status"] == "failed"


def test_timeout_is_uncertain_and_never_retried(tmp_path: Path, monkeypatch) -> None:
    control = tmp_path / "control.json"
    write_control(control, contour="TEST", enabled=True)
    monkeypatch.setenv("NMBOT_CALLBACK_CRM_CONTROL_FILE", str(control))
    outbox = LocalCallbackOutbox(tmp_path / "outbox")
    _enqueue(outbox)
    crm = FakeCRM(CRMResult(status="uncertain", uncertain=True, error_class="crm_transport_uncertain"))
    worker = CallbackSheetWorker(outbox=outbox, summary_provider=StaticSummary(), sheet_adapter=FakeCallbackSheetAdapter(), crm_adapter=crm, retry_base_seconds=0)

    worker.process_once()
    worker.process_once()

    assert crm.calls == 1
    assert _record(tmp_path / "outbox")["crm_delivery"]["status"] == "uncertain"


def test_adapter_allowlist_and_classification_without_network(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def transport(endpoint: str, body: bytes, timeout: float) -> tuple[int, bytes]:
        seen.update(json.loads(body.decode("utf-8")))
        return 200, b'{"ok":true,"receipt":"opaque"}'

    result = CallbackCRMAdapter(endpoint="https://example.invalid/callback", transport=transport).send_callback(phone="+79991234567", name="Иван", summary="Запрос")
    assert result.status == "ok"
    assert set(seen) == {"phone", "name", "request"}
    assert "+79991234567" not in repr(result.public())

    timeout = CallbackCRMAdapter(endpoint="x", transport=lambda *_: (_ for _ in ()).throw(TimeoutError())).send_callback(phone="p", name="n", summary="s")
    assert timeout.uncertain and not timeout.retryable
    retry = CallbackCRMAdapter(endpoint="x", transport=lambda *_: (503, b"{}")).send_callback(phone="p", name="n", summary="s")
    assert retry.retryable and retry.error_class == "crm_http_503"
    terminal = CallbackCRMAdapter(endpoint="x", transport=lambda *_: (401, b"{}")).send_callback(phone="p", name="n", summary="s")
    assert not terminal.retryable and terminal.error_class == "crm_http_401"
    invalid = CallbackCRMAdapter(endpoint="x", transport=lambda *_: (200, b"[]")).send_callback(phone="p", name="n", summary="s")
    assert invalid.error_class == "crm_invalid_response"
    empty_success = CallbackCRMAdapter(endpoint="x", transport=lambda *_: (200, b"")).send_callback(phone="p", name="n", summary="s")
    assert empty_success.status == "ok"
    missing = CallbackCRMAdapter(endpoint="", transport=lambda *_: (_ for _ in ()).throw(AssertionError())).send_callback(phone="p", name="n", summary="s")
    assert missing.error_class == "crm_configuration_error"


def test_old_v2_record_remains_sheet_processable_with_unknown_provenance(tmp_path: Path) -> None:
    outbox = LocalCallbackOutbox(tmp_path / "outbox")
    _enqueue(outbox)
    path = next((tmp_path / "outbox").glob("*.json"))
    record = json.loads(path.read_text())
    record.pop("provenance")
    record.pop("crm_delivery")
    path.write_text(json.dumps(record), encoding="utf-8")
    sheet = FakeCallbackSheetAdapter()

    result = CallbackSheetWorker(outbox=outbox, summary_provider=StaticSummary(), sheet_adapter=sheet).process_once()

    assert result["status"] == "sheet_delivered"
    assert len(sheet.rows[0]) == 4


def test_provenance_is_captured_without_h108_inference(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "unexpected")
    outbox = LocalCallbackOutbox(tmp_path / "outbox")
    outbox.enqueue_callback(session_key="s", event_id="e", contact_name="Иван", normalized_phone="+79991234567", context={}, provenance={"runtime_version": "v3"})
    provenance = _record(tmp_path / "outbox")["provenance"]
    assert provenance == {"contour": "UNKNOWN", "runtime_version": "V3", "release_id": "", "experiment_id": ""}
    assert "H108" not in repr(provenance)


def test_control_cli_requires_contour_and_confirm_and_writes_mode_0600(tmp_path: Path) -> None:
    script = SCRIPT_DIR / "nmbot_callback_crm_control.py"
    env = {**os.environ, "NMBOT_CALLBACK_CRM_CONTROL_FILE": str(tmp_path / "control.json")}
    missing_contour = subprocess.run([sys.executable, str(script), "status"], env=env, capture_output=True, text=True)
    assert missing_contour.returncode != 0
    no_confirm = subprocess.run([sys.executable, str(script), "--contour", "TEST", "set", "on"], env=env, capture_output=True, text=True)
    assert no_confirm.returncode == 2
    dry_run = subprocess.run([sys.executable, str(script), "--contour", "TEST", "set", "on", "--dry-run"], env=env, capture_output=True, text=True)
    assert dry_run.returncode == 0 and not (tmp_path / "control.json").exists()
    applied = subprocess.run([sys.executable, str(script), "--contour", "TEST", "set", "on", "--confirm"], env=env, capture_output=True, text=True)
    assert applied.returncode == 0
    assert read_control(tmp_path / "control.json", contour="TEST") is True
    assert oct(os.stat(tmp_path / "control.json").st_mode & 0o777) == "0o600"
