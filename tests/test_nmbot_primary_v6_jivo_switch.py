from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from base64 import b64encode
from pathlib import Path

import pytest

from scripts import nmbot_primary_v6_jivo_switch as switcher


def test_contract_is_fixed_to_primary_bridge_and_isolated_v6() -> None:
    contract = switcher.TargetContract().as_dict()

    assert contract["primary_bridge"] == switcher.PRIMARY_BRIDGE
    assert contract["current_upstream"] == "http://127.0.0.1:8088"
    assert contract["v6_upstream"] == "http://127.0.0.1:18088"
    assert contract["v6_health"] == "http://127.0.0.1:18088/health"
    assert switcher.PRIMARY_API in contract["do_not_touch"]
    assert switcher.CLIENT_BRIDGE in contract["do_not_touch"]


def test_remote_program_is_syntax_valid_and_never_restarts_protected_units() -> None:
    source = switcher.REMOTE_PROGRAM
    compile(source, "<primary-v6-jivo-switch>", "exec")

    assert f'"restart", EXPECTED["primary_bridge"]' in source
    for unit in (switcher.PRIMARY_API, switcher.CLIENT_API, switcher.CLIENT_BRIDGE):
        assert unit in source
        assert f'"restart", "{unit}"' not in source
        assert f'"stop", "{unit}"' not in source
    assert "switch_backup_hash_mismatch" in source
    assert "rollback_upstream_verify_failed" in source
    assert 'if len(values) > 1:' in source
    assert 'return values[0] if values else EXPECTED["current_upstream"]' in source
    assert 'if count == 0:' in source
    assert 'output.append("NMBOT_BRIDGE_UPSTREAM=" + target + "\\n")' in source
    assert '"--property=Result"' in source
    assert '"--property=ExecMainStatus"' in source
    assert '"primary_bridge_route": route' in source
    assert '"switch_status_exists": status is not None' in source
    assert '"switch_status": status["status"] if status else None' in source
    assert 'if operation == "reconcile":' in source
    assert 'fail("switch_backup_hash_mismatch")' in source
    assert "import time" in source
    assert "for _ in range(20):" in source
    assert "time.sleep(0.25)" in source
    assert 'if backup.read_bytes() != original:' in source
    assert 'fail("switch_backup_mismatch")' in source
    assert 'fail("switch_backup_unsafe")' in source


def test_payload_and_ssh_command_are_fixed_and_redact_host_from_remote_program() -> None:
    payload = switcher.target_payload("preflight", expected_v6_release="v6-test-r1")
    command = switcher._remote_command(payload)

    assert payload["v6_upstream"] == switcher.V6_UPSTREAM
    assert command[:3] == ["ssh", "-p", "1905"]
    assert command[-2] == switcher.HOST
    assert "193.107.155.236" not in command[-1]


@pytest.mark.parametrize("operation", ["bridge", "jivo", "prod", "deploy"])
def test_unknown_operation_is_rejected(operation: str) -> None:
    with pytest.raises(switcher.PrimaryV6SwitchError, match="unsupported operation"):
        switcher.target_payload(operation, expected_v6_release="v6-test-r1")


def test_switch_and_rollback_need_distinct_exact_confirmations() -> None:
    release = "v6-test-r1"
    with pytest.raises(switcher.PrimaryV6SwitchError, match="SWITCH-PRIMARY-BRIDGE-v6-test-r1-TO-V6"):
        switcher._require_confirmation("switch", release, False, "")
    with pytest.raises(switcher.PrimaryV6SwitchError, match="ROLLBACK-PRIMARY-BRIDGE-v6-test-r1-TO-CURRENT"):
        switcher._require_confirmation("rollback", release, False, "")
    with pytest.raises(switcher.PrimaryV6SwitchError, match="RECONCILE-PRIMARY-BRIDGE-v6-test-r1-TO-CURRENT"):
        switcher._require_confirmation("reconcile", release, False, "")

    switcher._require_confirmation("switch", release, True, "SWITCH-PRIMARY-BRIDGE-v6-test-r1-TO-V6")
    switcher._require_confirmation("rollback", release, True, "ROLLBACK-PRIMARY-BRIDGE-v6-test-r1-TO-CURRENT")
    switcher._require_confirmation("reconcile", release, True, "RECONCILE-PRIMARY-BRIDGE-v6-test-r1-TO-CURRENT")


class FakeClient:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def operation(self, payload, *, timeout=120):
        self.payloads.append(dict(payload))
        return {"ok": True, "schema": switcher.SCHEMA, "operation": payload["operation"]}


def test_main_sends_only_confirmed_fixed_switch_payload() -> None:
    client = FakeClient()
    assert switcher.main([
        "switch", "--expected-v6-release", "v6-test-r1", "--apply",
        "--confirm", "SWITCH-PRIMARY-BRIDGE-v6-test-r1-TO-V6",
    ], client=client) == 0

    assert client.payloads == [switcher.target_payload("switch", expected_v6_release="v6-test-r1")]


def test_main_sends_only_confirmed_fixed_reconcile_payload() -> None:
    client = FakeClient()
    assert switcher.main([
        "reconcile", "--expected-v6-release", "v6-test-r1", "--apply",
        "--confirm", "RECONCILE-PRIMARY-BRIDGE-v6-test-r1-TO-CURRENT",
    ], client=client) == 0

    assert client.payloads == [switcher.target_payload("reconcile", expected_v6_release="v6-test-r1")]


class _HealthyResponse:
    status = 200

    def __init__(self, release_id: str) -> None:
        self._release_id = release_id

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return json.dumps({
            "ok": True,
            "runtime": "V6",
            "profile": "TEST",
            "release_id": self._release_id,
        }).encode("utf-8")


def _run_local_reconcile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    upstream: str = "http://127.0.0.1:8088",
    backup_sha256: str | None = None,
) -> tuple[int, dict[str, object], Path]:
    env_path = tmp_path / "primary.env"
    root = tmp_path / "switch-state"
    backup = root / "backups" / "v6-old-test.env"
    backup.parent.mkdir(parents=True)
    original = b"NMBOT_BRIDGE_UPSTREAM=http://127.0.0.1:8088\n"
    env_path.write_text(f"NMBOT_BRIDGE_UPSTREAM={upstream}\n", encoding="utf-8")
    backup.write_bytes(original)
    recorded_hash = backup_sha256 or hashlib.sha256(original).hexdigest()
    status_path = root / "status.json"
    status_path.write_text(json.dumps({
        "release_id": "v6-old-test",
        "status": "active",
        "backup_sha256": recorded_hash,
    }), encoding="utf-8")

    def fake_run(_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=123\n",
        )

    import urllib.request

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _HealthyResponse("v6-test-r1"))
    source = switcher.REMOTE_PROGRAM.replace(switcher.PRIMARY_ENV, str(env_path)).replace(switcher.SWITCH_ROOT, str(root))
    payload = switcher.target_payload("reconcile", expected_v6_release="v6-test-r1")
    payload["primary_env"] = str(env_path)
    payload["switch_root"] = str(root)
    encoded = b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    output: list[str] = []
    monkeypatch.setattr("builtins.print", lambda value: output.append(value))
    monkeypatch.setattr(sys, "argv", ["remote", encoded])
    with pytest.raises(SystemExit) as exited:
        exec(compile(source, "<local-reconcile>", "exec"), {})
    return exited.value.code, json.loads(output[-1]), status_path


def test_reconcile_marks_only_verified_stale_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, receipt, status_path = _run_local_reconcile(monkeypatch, tmp_path)

    assert code == 0
    assert receipt["ok"] is True
    assert receipt["primary_bridge_route"] == "current"
    assert receipt["switch_status"] == "reconciled"
    assert json.loads(status_path.read_text(encoding="utf-8"))["status"] == "reconciled"


def test_reconcile_refuses_noncurrent_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, receipt, _status_path = _run_local_reconcile(
        monkeypatch, tmp_path, upstream="http://127.0.0.1:18088",
    )

    assert code == 2
    assert receipt["error"] == "primary_upstream_not_current"


def test_reconcile_refuses_backup_hash_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, receipt, _status_path = _run_local_reconcile(monkeypatch, tmp_path, backup_sha256="0" * 64)

    assert code == 2
    assert receipt["error"] == "switch_backup_hash_mismatch"


def test_remote_errors_do_not_leak_stderr() -> None:
    result = subprocess.CompletedProcess(
        args=[], returncode=2,
        stdout=json.dumps({"ok": False, "schema": switcher.SCHEMA, "error": "switch_failed_rolled_back"}) + "\n",
        stderr="secret remote output",
    )
    with pytest.raises(switcher.PrimaryV6SwitchError, match="switch_failed_rolled_back") as raised:
        switcher._safe_remote_result(result)
    assert "secret remote output" not in str(raised.value)
