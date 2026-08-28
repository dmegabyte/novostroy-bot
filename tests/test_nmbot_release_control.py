from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

import pytest

from scripts import nmbot_atomic_release as atomic
from scripts import nmbot_release_control as control
from scripts.nmbot_release_control import ReleaseControlError, ReleaseController


ROOT = Path(__file__).resolve().parents[1]
GIT_SHA = "1" * 40
TREE_SHA = "2" * 40


@pytest.fixture(autouse=True)
def fixed_source_provenance(monkeypatch):
    unsigned = {"git_sha": GIT_SHA, "git_tree_sha": TREE_SHA, "tree_state": "clean"}
    receipt = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    monkeypatch.setattr(atomic, "_git_source_provenance", lambda _root: {**unsigned, "clean_receipt_sha256": receipt})
    monkeypatch.setattr(atomic, "_git_blob", lambda root, _sha, relative: (Path(root) / relative).read_bytes())


class FakeServiceManager:
    def __init__(self) -> None:
        self.restarted: list[str] = []
        self.stopped: list[str] = []

    def restart(self, instance: str) -> None:
        self.restarted.append(instance)

    def stop(self, instance: str) -> None:
        self.stopped.append(instance)


class FakeHealth:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, upstream: str, *, profile: str, release_id: str) -> str:
        self.calls.append((upstream, profile, release_id))
        return f"health:{profile.lower()}-{release_id}"


@pytest.fixture
def artifacts(tmp_path: Path):
    out = tmp_path / "build"
    first = atomic.build(
        release_id="v6-r41",
        out_dir=out,
        root=ROOT,
        profile=atomic.V6_ONLY_PROFILE,
    )
    second = atomic.build(
        release_id="v6-r42",
        out_dir=out,
        root=ROOT,
        profile=atomic.V6_ONLY_PROFILE,
    )
    return first, second


def _install(controller: ReleaseController, artifact) -> dict:
    return controller.install_artifact(
        manifest=artifact.manifest,
        archive=artifact.archive,
    )


def test_install_is_immutable_idempotent_and_detects_release_tampering(tmp_path: Path, artifacts) -> None:
    first, _ = artifacts
    controller = ReleaseController(tmp_path / "control", service_manager=FakeServiceManager(), health_probe=FakeHealth())

    installed = _install(controller, first)
    repeated = _install(controller, first)

    assert installed["release_id"] == "v6-r41"
    assert repeated["artifact_sha256"] == installed["artifact_sha256"]
    assert installed["startup_receipt"].startswith("startup:")
    assert installed["source_git_sha"] == GIT_SHA
    assert installed["source_git_tree_sha"] == TREE_SHA
    assert repeated["startup_receipt"] == installed["startup_receipt"]
    identity = Path(installed["release_root"]) / "release_identity" / "nmbot_release_identity.json"
    assert identity.is_file()

    entrypoint = Path(installed["release_root"]) / "scripts" / "nmbot_api_server.py"
    entrypoint.write_text(entrypoint.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(ReleaseControlError, match="differs from artifact"):
        _install(controller, first)


def test_failed_startup_admission_never_registers_or_installs_release(tmp_path: Path, artifacts) -> None:
    first, _ = artifacts

    def reject_startup(release_root: Path, release_id: str) -> str:
        assert release_root.is_dir()
        assert release_id == "v6-r41"
        raise ReleaseControlError("startup rejected")

    root = tmp_path / "control"
    controller = ReleaseController(
        root,
        service_manager=FakeServiceManager(),
        health_probe=FakeHealth(),
        startup_preflight=reject_startup,
    )

    with pytest.raises(ReleaseControlError, match="startup rejected"):
        _install(controller, first)

    assert controller.registry.list_releases() == []
    assert not (root / "artifacts" / "v6-r41").exists()
    assert not (root / "releases" / "v6-r41").exists()


def test_prepare_activate_and_rollback_keep_previous_slot_warm(tmp_path: Path, artifacts) -> None:
    first, second = artifacts
    services = FakeServiceManager()
    health = FakeHealth()
    controller = ReleaseController(tmp_path / "control", service_manager=services, health_probe=health)
    _install(controller, first)
    _install(controller, second)
    env_file = tmp_path / "contour.env"
    env_file.write_text("# test fixture\n", encoding="utf-8")

    prepared_a = controller.prepare_slot(profile="TEST", release_id="v6-r41", port=18088, env_file=env_file, slot="A")
    activated_a = controller.activate(profile="TEST", slot="A")
    prepared_b = controller.prepare_slot(profile="TEST", release_id="v6-r42", port=18089, env_file=env_file, slot="B")
    activated_b = controller.activate(profile="TEST", slot="B")
    rolled_back = controller.rollback(profile="TEST", reason_code="quality_regression")

    assert prepared_a["status"] == prepared_b["status"] == "ready"
    assert activated_a["active"]["release_id"] == "v6-r41"
    assert activated_b["active"]["release_id"] == "v6-r42"
    assert rolled_back["active"]["release_id"] == "v6-r41"
    assert controller.registry.slot_state(profile="TEST", slot="A")["status"] == "ready"
    assert controller.registry.slot_state(profile="TEST", slot="B")["status"] == "ready"
    assert controller.registry.read_route("PROD", required=False) is None
    assert services.restarted == ["test-a", "test-b"]
    assert services.stopped == []


def test_prepare_failure_marks_slot_failed_and_stops_inactive_process(tmp_path: Path, artifacts) -> None:
    first, _ = artifacts
    services = FakeServiceManager()

    def failing_health(upstream: str, *, profile: str, release_id: str) -> str:
        raise ReleaseControlError("not ready")

    controller = ReleaseController(
        tmp_path / "control",
        service_manager=services,
        health_probe=failing_health,
        sleep=lambda _: time.sleep(0.12),
    )
    _install(controller, first)
    env_file = tmp_path / "contour.env"
    env_file.write_text("", encoding="utf-8")

    with pytest.raises(ReleaseControlError, match="did not become healthy"):
        controller.prepare_slot(
            profile="TEST",
            release_id="v6-r41",
            port=18088,
            env_file=env_file,
            slot="A",
            health_timeout=0.1,
        )

    assert controller.registry.slot_state(profile="TEST", slot="A")["status"] == "failed"
    assert services.restarted == ["test-a"]
    assert services.stopped == ["test-a"]


def test_prepare_rejects_tampered_release_before_start(tmp_path: Path, artifacts) -> None:
    first, _ = artifacts
    services = FakeServiceManager()
    controller = ReleaseController(tmp_path / "control", service_manager=services, health_probe=FakeHealth())
    installed = _install(controller, first)
    entrypoint = Path(installed["release_root"]) / "scripts" / "nmbot_api_server.py"
    entrypoint.write_text("# changed\n", encoding="utf-8")
    env_file = tmp_path / "contour.env"
    env_file.write_text("", encoding="utf-8")

    with pytest.raises(ReleaseControlError, match="differs from artifact"):
        controller.prepare_slot(profile="TEST", release_id="v6-r41", port=18088, env_file=env_file, slot="A")
    assert services.restarted == []


def test_failed_post_switch_check_restores_original_route(tmp_path: Path, artifacts, monkeypatch) -> None:
    first, second = artifacts
    controller = ReleaseController(tmp_path / "control", service_manager=FakeServiceManager(), health_probe=FakeHealth())
    _install(controller, first)
    _install(controller, second)
    env_file = tmp_path / "contour.env"
    env_file.write_text("", encoding="utf-8")
    controller.prepare_slot(profile="TEST", release_id="v6-r41", port=18088, env_file=env_file, slot="A")
    original = controller.activate(profile="TEST", slot="A")
    controller.prepare_slot(profile="TEST", release_id="v6-r42", port=18089, env_file=env_file, slot="B")

    calls = 0

    def fail_after_switch(*, profile: str, target: dict[str, str]) -> str:
        nonlocal calls
        if target["release_id"] == "v6-r42":
            calls += 1
            if calls == 2:
                raise ReleaseControlError("post-switch health failed")
        return "health:ok"

    monkeypatch.setattr(controller, "_assert_healthy", fail_after_switch)
    with pytest.raises(ReleaseControlError, match="post-switch health failed"):
        controller.activate(profile="TEST", slot="B")

    assert controller.registry.read_route("TEST") == original


def test_promotion_copies_exact_artifact_but_never_activates_prod(tmp_path: Path, artifacts) -> None:
    first, _ = artifacts
    source = ReleaseController(tmp_path / "source", service_manager=FakeServiceManager(), health_probe=FakeHealth())
    destination = ReleaseController(tmp_path / "destination", service_manager=FakeServiceManager(), health_probe=FakeHealth())
    _install(source, first)
    source.registry.record_check(
        "v6-r41",
        profile="TEST",
        outcome="passed",
        reason_code="v6_contracts",
        receipt_ref="pytest:passed",
    )
    source.registry.set_quality("v6-r41", verdict="approved", receipt_ref="review:owner")

    promoted = source.promote_to(destination, "v6-r41")

    assert promoted["promotion"] == "synced_not_activated"
    assert destination.registry.show_release("v6-r41")["quality"]["verdict"] == "approved"
    assert destination.registry.read_route("PROD", required=False) is None
    assert source._stored_archive("v6-r41").read_bytes() == destination._stored_archive("v6-r41").read_bytes()


def test_promotion_rejects_unapproved_release(tmp_path: Path, artifacts) -> None:
    first, _ = artifacts
    source = ReleaseController(tmp_path / "source", service_manager=FakeServiceManager(), health_probe=FakeHealth())
    destination = ReleaseController(tmp_path / "destination", service_manager=FakeServiceManager(), health_probe=FakeHealth())
    _install(source, first)

    with pytest.raises(ReleaseControlError, match="approved quality"):
        source.promote_to(destination, "v6-r41")


def test_control_bundle_is_exact_immutable_and_tamper_evident(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(control, "_clean_git_sha", lambda _root: "3" * 40)
    bundle = control.build_control_bundle(source_root=ROOT, out_dir=tmp_path / "bundle")

    inspected = control.inspect_control_bundle(manifest=bundle.manifest, archive=bundle.archive)

    assert inspected.source_git_sha == "3" * 40
    assert tuple(path for path, _sha in inspected.files) == control.CONTROL_BUNDLE_FILES
    assert inspected.archive_sha256 == hashlib.sha256(bundle.archive.read_bytes()).hexdigest()

    bundle.archive.write_bytes(bundle.archive.read_bytes() + b"tampered")
    with pytest.raises(ReleaseControlError, match="archive sha256 mismatch"):
        control.inspect_control_bundle(manifest=bundle.manifest, archive=bundle.archive)


class FakeRemote:
    def __init__(self, receipt: dict) -> None:
        self.receipt = receipt
        self.commands: list[str] = []
        self.uploads: list[tuple[Path, str]] = []

    def run(self, command: str) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        stdout = "" if len(self.commands) == 1 else json.dumps(self.receipt)
        return subprocess.CompletedProcess([], 0, stdout, "")

    def upload(self, local: Path, remote_path: str) -> subprocess.CompletedProcess[str]:
        self.uploads.append((Path(local), remote_path))
        return subprocess.CompletedProcess([], 0, "", "")


def test_remote_bootstrap_installs_only_control_plane(tmp_path: Path, monkeypatch) -> None:
    source_sha = "4" * 40
    monkeypatch.setattr(control, "_clean_git_sha", lambda _root: source_sha)
    bundle = control.build_control_bundle(source_root=ROOT, out_dir=tmp_path / "bundle")
    receipt = {
        "schema": "nmbot.release_control_bootstrap_receipt.v1",
        "ok": True,
        "source_git_sha": source_sha,
        "control_root": "/home/neiro/.local/state/nmbot-v6-release",
        "unit_installed": True,
        "api_touched": False,
        "bridge_touched": False,
    }
    remote = FakeRemote(receipt)

    observed = control.bootstrap_remote(manifest=bundle.manifest, archive=bundle.archive, remote=remote)

    assert observed == receipt
    assert len(remote.uploads) == 2
    bootstrap_command = remote.commands[-1]
    assert "nmbot-v6-slot@.service" in bootstrap_command
    assert "novostroy-bot-api.service" not in bootstrap_command
    assert "novostroy-bot-n8n-bridge.service" not in bootstrap_command


def test_prod_prepare_requires_approved_passing_test_receipt(tmp_path: Path, artifacts) -> None:
    first, _ = artifacts
    controller = ReleaseController(tmp_path / "control", service_manager=FakeServiceManager(), health_probe=FakeHealth())
    _install(controller, first)
    env_file = tmp_path / "prod.env"
    env_file.write_text("", encoding="utf-8")

    with pytest.raises(ReleaseControlError, match="approved quality"):
        controller.prepare_slot(profile="PROD", release_id="v6-r41", port=18090, env_file=env_file, slot="A")

    controller.registry.set_quality("v6-r41", verdict="approved", receipt_ref="review:owner")
    with pytest.raises(ReleaseControlError, match="passing TEST"):
        controller.prepare_slot(profile="PROD", release_id="v6-r41", port=18090, env_file=env_file, slot="A")

    controller.registry.record_check("v6-r41", profile="TEST", outcome="passed", reason_code="strict_smoke", receipt_ref="smoke:passed")
    prepared = controller.prepare_slot(profile="PROD", release_id="v6-r41", port=18090, env_file=env_file, slot="A")
    assert prepared["profile"] == "PROD"
    assert prepared["release_id"] == "v6-r41"
