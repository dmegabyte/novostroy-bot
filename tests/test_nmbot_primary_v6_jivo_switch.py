from __future__ import annotations

import json
import subprocess

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

    switcher._require_confirmation("switch", release, True, "SWITCH-PRIMARY-BRIDGE-v6-test-r1-TO-V6")
    switcher._require_confirmation("rollback", release, True, "ROLLBACK-PRIMARY-BRIDGE-v6-test-r1-TO-CURRENT")


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


def test_remote_errors_do_not_leak_stderr() -> None:
    result = subprocess.CompletedProcess(
        args=[], returncode=2,
        stdout=json.dumps({"ok": False, "schema": switcher.SCHEMA, "error": "switch_failed_rolled_back"}) + "\n",
        stderr="secret remote output",
    )
    with pytest.raises(switcher.PrimaryV6SwitchError, match="switch_failed_rolled_back") as raised:
        switcher._safe_remote_result(result)
    assert "secret remote output" not in str(raised.value)
