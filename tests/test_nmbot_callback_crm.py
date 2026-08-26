from __future__ import annotations

import json
from pathlib import Path

from scripts import nmbot_callback_crm_control as control
from scripts import nmbot_crm_outbox as outbox_module
from scripts.nmbot_callback_crm import CallbackCRMAdapter
from scripts.nmbot_crm_outbox import LocalCallbackOutbox, build_callback_provenance, normalize_contour


def test_profiles_are_exact_and_test_is_hard_crm_off(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "control.json"
    control.write_control(path, contour="TEST", enabled=True)
    control.write_control(path, contour="PROD", enabled=True)
    monkeypatch.setenv("NMBOT_CALLBACK_CRM_CONTROL_FILE", str(path))

    assert normalize_contour("TEST") == "TEST"
    assert normalize_contour("PROD") == "PROD"
    assert normalize_contour("PROD" + "UCTION") == "UNKNOWN"
    assert outbox_module._crm_enabled_for_contour("TEST") is False
    assert outbox_module._crm_enabled_for_contour("UNKNOWN") is False
    assert outbox_module._crm_enabled_for_contour("PROD") is True


def test_outbox_captures_exact_v6_provenance_and_profile_delivery(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "control.json"
    control.write_control(path, contour="PROD", enabled=True)
    monkeypatch.setenv("NMBOT_CALLBACK_CRM_CONTROL_FILE", str(path))
    outbox = LocalCallbackOutbox(tmp_path / "outbox")

    for profile, event in (("TEST", "event-test"), ("PROD", "event-prod")):
        outbox.enqueue_callback(
            session_key=profile,
            event_id=event,
            contact_name="Анна",
            normalized_phone="+79991234567",
            context={"runtime": "V6"},
            provenance={"runtime_version": "V6", "release_id": "v6-clean-r1", "contour": profile},
        )
    records = [json.loads(path.read_text(encoding="utf-8")) for path in (tmp_path / "outbox").glob("*.json")]
    by_profile = {record["provenance"]["contour"]: record for record in records}
    assert by_profile["TEST"]["crm_delivery"]["status"] == "disabled"
    assert by_profile["PROD"]["crm_delivery"]["status"] == "pending_summary"
    assert {record["provenance"]["runtime_version"] for record in records} == {"V6"}
    assert build_callback_provenance(runtime_version="V6", release_id="v6-clean-r1", contour="TEST")["release_id"] == "v6-clean-r1"


def test_crm_adapter_classifies_success_retry_and_uncertain() -> None:
    ok = CallbackCRMAdapter(endpoint="https://crm.example/callback", transport=lambda *_: (200, b'{"ok":true,"receipt":"r-1"}'))
    retry = CallbackCRMAdapter(endpoint="https://crm.example/callback", transport=lambda *_: (503, b""))

    def uncertain(*_args):
        raise TimeoutError()

    assert ok.send_callback(phone="+79991234567", name="Анна", summary="Заявка").receipt == "r-1"
    assert retry.send_callback(phone="+79991234567", name="Анна", summary="Заявка").retryable is True
    assert CallbackCRMAdapter(endpoint="https://crm.example/callback", transport=uncertain).send_callback(phone="x", name="", summary="").uncertain is True
