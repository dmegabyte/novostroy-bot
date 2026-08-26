from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import nmbot_test_api_deploy as deployer


def test_target_is_loopback_only_and_disjoint_from_live_contours() -> None:
    contract = deployer.TargetContract().as_dict()

    assert contract["bind"] == "127.0.0.1"
    assert contract["profile"] == "TEST"
    assert contract["bridge"] == "absent"
    assert contract["jivo_ingress"] == "disconnected"
    assert contract["crm_delivery"] == "disabled"
    assert contract["rollback"] == "stop_isolated_unit"
    assert deployer.TEST_ROOT not in deployer.PROTECTED_ROOTS
    assert all(not deployer.TEST_ROOT.startswith(root + "/") for root in deployer.PROTECTED_ROOTS)
    assert deployer.TEST_UNIT not in deployer.PROTECTED_SERVICES
    assert deployer.TEST_PORT not in {8088, 8093, 8188, 8193}


def test_remote_owner_never_restarts_or_stops_protected_services() -> None:
    source = deployer.REMOTE_PROGRAM

    compile(source, "<nmbot-v6-isolated-test-remote>", "exec")
    assert '["systemctl", "--user", "stop", unit]' in source
    assert "systemd-run" in source
    for service in deployer.PROTECTED_SERVICES:
        assert service in source
        assert f'"restart", "{service}"' not in source
        assert f'"stop", "{service}"' not in source
    assert "JIVO_PROVIDER_TOKEN" not in deployer.COPIED_ENV_KEYS
    assert "NMBOT_N8N_BRIDGE_TOKEN" not in deployer.COPIED_ENV_KEYS
    assert "NMBOT_CALLBACK_CRM_ENDPOINT" not in deployer.COPIED_ENV_KEYS
    assert "OPENROUTER_API_KEY" in deployer.COPIED_ENV_KEYS
    assert "openrouter_key_missing" in source


def test_preflight_payload_is_read_only_and_fixed() -> None:
    payload = deployer.target_payload("preflight")
    command = deployer._remote_command(payload)

    assert payload["root"] == deployer.TEST_ROOT
    assert payload["protected_services"] == list(deployer.PROTECTED_SERVICES)
    assert payload["protected_health"] == list(deployer.PROTECTED_HEALTH)
    assert command[:3] == ["ssh", "-p", "1905"]
    assert command[-2] == deployer.HOST
    assert "193.107.155.236" not in command[-1]


@pytest.mark.parametrize("operation", ["bridge", "jivo", "crm", "prod"])
def test_unknown_operation_is_rejected(operation: str) -> None:
    with pytest.raises(deployer.IsolatedTestError, match="unsupported operation"):
        deployer.target_payload(operation)


def test_deploy_and_stop_require_exact_separate_confirmations() -> None:
    release_id = "v6-clean-test-r1"
    with pytest.raises(deployer.IsolatedTestError, match="DEPLOY-v6-clean-test-r1-TO-ISOLATED-TEST"):
        deployer._require_deploy_confirmation(release_id=release_id, apply=False, confirmation="")
    with pytest.raises(deployer.IsolatedTestError, match="STOP-v6-clean-test-r1-ISOLATED-TEST"):
        deployer._require_stop_confirmation(release_id=release_id, apply=False, confirmation="")

    deployer._require_deploy_confirmation(
        release_id=release_id,
        apply=True,
        confirmation="DEPLOY-v6-clean-test-r1-TO-ISOLATED-TEST",
    )
    deployer._require_stop_confirmation(
        release_id=release_id,
        apply=True,
        confirmation="STOP-v6-clean-test-r1-ISOLATED-TEST",
    )


class FakeClient:
    def __init__(self) -> None:
        self.operations: list[dict[str, object]] = []
        self.uploads: list[tuple[Path, str]] = []

    def operation(self, payload, *, timeout=120):
        self.operations.append(dict(payload))
        if payload["operation"] == "prepare_upload":
            return {
                "ok": True,
                "schema": deployer.SCHEMA,
                "upload_dir": f"{deployer.TEST_ROOT}/.staging/{payload['release_id']}",
            }
        return {"ok": True, "schema": deployer.SCHEMA, "operation": payload["operation"]}

    def upload(self, source: Path, destination: str) -> None:
        self.uploads.append((Path(source), destination))


def _artifact(tmp_path: Path, *, git_sha: str = "1" * 40):
    manifest = tmp_path / "manifest.json"
    archive = tmp_path / "archive.tar.gz"
    manifest.write_text("{}", encoding="utf-8")
    archive.write_bytes(b"archive")
    return deployer.InspectedArtifact(
        release_id="v6-clean-test-r1",
        archive=archive,
        manifest=manifest,
        archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        prompt_sha256="2" * 64,
        source_git_sha=git_sha,
        source_git_tree_sha="3" * 40,
        source_clean_receipt_sha256="4" * 64,
        files=(),
    )


def test_deploy_uploads_only_after_artifact_sha_and_confirmation(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    client = FakeClient()

    result = deployer.deploy(
        manifest=artifact.manifest,
        archive=artifact.archive,
        expected_git_sha="1" * 40,
        apply=True,
        confirmation="DEPLOY-v6-clean-test-r1-TO-ISOLATED-TEST",
        client=client,
        inspector=lambda manifest, archive: artifact,
    )

    assert result["operation"] == "activate"
    assert [item["operation"] for item in client.operations] == ["prepare_upload", "activate"]
    assert client.uploads == [
        (artifact.manifest, f"{deployer.TEST_ROOT}/.staging/v6-clean-test-r1/manifest.json"),
        (artifact.archive, f"{deployer.TEST_ROOT}/.staging/v6-clean-test-r1/archive.tar.gz"),
    ]
    activate = client.operations[-1]
    assert activate["source_git_sha"] == "1" * 40
    assert activate["archive_sha256"] == artifact.archive_sha256
    assert activate["manifest_sha256"] == artifact.manifest_sha256


def test_deploy_rejects_mismatched_candidate_before_remote_call(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    client = FakeClient()

    with pytest.raises(deployer.IsolatedTestError, match="Git SHA"):
        deployer.deploy(
            manifest=artifact.manifest,
            archive=artifact.archive,
            expected_git_sha="5" * 40,
            apply=True,
            confirmation="DEPLOY-v6-clean-test-r1-TO-ISOLATED-TEST",
            client=client,
            inspector=lambda manifest, archive: artifact,
        )

    assert client.operations == []
    assert client.uploads == []


def test_remote_error_receipt_is_sanitized() -> None:
    result = subprocess.CompletedProcess(
        args=[],
        returncode=2,
        stdout=json.dumps({"ok": False, "schema": deployer.SCHEMA, "error": "isolated_test_port_in_use"}) + "\n",
        stderr="secret-bearing remote output",
    )

    with pytest.raises(deployer.IsolatedTestError, match="isolated_test_port_in_use") as raised:
        deployer._safe_remote_result(result)
    assert "secret-bearing" not in str(raised.value)
