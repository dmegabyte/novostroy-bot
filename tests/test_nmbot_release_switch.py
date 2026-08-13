from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("switch", ROOT / "scripts/nmbot_release_switch.py")
switch = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(switch)


class FakeRunner:
    def __init__(self, code=0, body=None):
        self.code = code
        self.body = body or {"status": "ok", "current": "old"}
        self.commands = []

    def run(self, command):
        self.commands.append(command)
        return subprocess.CompletedProcess([], self.code, json.dumps(self.body) + "\n", "")


def load_remote() -> types.ModuleType:
    compile(switch.REMOTE, "<nmbot-release-switch-remote>", "exec")
    module = types.ModuleType("release_switch_remote")
    module.__dict__["__name__"] = "release_switch_remote"
    exec(compile(switch.REMOTE, "<nmbot-release-switch-remote>", "exec"), module.__dict__)
    return module


def identity(release_id: str, content: bytes, **changes) -> bytes:
    data = {
        "schema": "nmbot.release_identity.v1",
        "release_id": release_id,
        "generated_at": "2026-08-13T12:00:00Z",
        "tracked_files": [{"path": "app.py", "sha256": hashlib.sha256(content).hexdigest()}],
    }
    data.update(changes)
    return json.dumps(data, sort_keys=True).encode()


def artifact_manifest(release_id: str, content: bytes, identity_raw: bytes) -> dict:
    return {
        "schema_version": "nmbot.atomic_release.v1", "scope": "api",
        "release_id": release_id, "created_at_utc": "2026-08-13T12:00:00Z",
        "archive_name": f"nmbot-{release_id}.tar.gz", "archive_sha256": "0" * 64,
        "files": [
            {"path": "app.py", "sha256": hashlib.sha256(content).hexdigest()},
            {"path": "release_identity/nmbot_release_identity.json", "sha256": hashlib.sha256(identity_raw).hexdigest()},
        ],
        "entrypoints": ["scripts/nmbot_api_server.py"], "import_modules": [],
        "service": "novostroy-bot-api.service",
        "forbidden_services": ["novostroy-bot-n8n-bridge.service", "novostroy-bot-worker.service"],
        "config_schema_requirements": {}, "external_runtime_strategy": "symlink",
        "identity_path": "release_identity/nmbot_release_identity.json",
        "source_provenance": {"present": False},
    }


def sandbox(tmp_path: Path, releases=("old", "new")):
    remote = load_remote()
    root = tmp_path / "root"
    release_root = root / "releases"
    data_dir = root / "data"
    release_root.mkdir(parents=True)
    data_dir.mkdir()
    staging_root = root / ".release_staging"
    staging_root.mkdir()
    manifests = {}
    for release_id in releases:
        release_dir = release_root / release_id
        (release_dir / "release_identity").mkdir(parents=True)
        content = (release_id + "\n").encode()
        (release_dir / "app.py").write_bytes(content)
        raw = identity(release_id, content)
        (release_dir / "release_identity/nmbot_release_identity.json").write_bytes(raw)
        staging_dir = staging_root / release_id
        staging_dir.mkdir()
        (staging_dir / f"nmbot-{release_id}.manifest.json").write_text(
            json.dumps(artifact_manifest(release_id, content, raw)))
        manifests[release_id] = raw
    os.symlink("releases/old", root / "current")
    (data_dir / "nmbot_release_identity.json").write_bytes(manifests["old"])

    remote.FIXED_ROOT = str(root)
    remote.ROOT = root
    remote.RELEASES = release_root
    remote.STAGING = staging_root
    remote.CURRENT = root / "current"
    remote.PREVIOUS = root / "previous"
    remote.DATA = data_dir
    remote.EXTERNAL = data_dir / "nmbot_release_identity.json"
    remote.LOCK = root / ".release_lock"
    config = {"root": str(root), "service": remote.FIXED_SERVICE}
    return remote, config, manifests


def test_remote_source_compiles_and_loads():
    remote = load_remote()
    assert callable(remote.switch)


def test_release_id_validation():
    assert switch.validate_release_id("v1.2-okay") == "v1.2-okay"
    for bad in ("", "../x", "x/y", "-x", "x;rm", "x" * 81):
        with pytest.raises(ValueError):
            switch.validate_release_id(bad)


def test_fixed_batchmode_target_and_no_forbidden_service_writes():
    fake = FakeRunner()
    switch.execute(op="status", target="old", runner=fake)
    command = fake.commands[0]
    assert "BatchMode=yes" in str(switch.SSHRunner.run.__code__.co_consts)
    assert ".env" not in command
    assert "novostroy-bot-n8n-bridge" not in command
    assert "worker" not in command


def test_status_health_failure_is_error_and_never_stops(tmp_path):
    remote, config, _ = sandbox(tmp_path)
    actions = []
    remote.run_systemctl = lambda action, check=True: actions.append(action)
    remote.health = lambda release_id, wait=True: {"ok": False, "error": "down"}
    with pytest.raises(remote.SwitchError, match="health failed"):
        remote.switch({**config, "op": "status", "target": "old", "confirm": False})
    assert actions == []


def test_pre_cutover_manifest_failure_does_not_stop_or_rollback(tmp_path):
    remote, config, _ = sandbox(tmp_path)
    target = remote.RELEASES / "new/release_identity/nmbot_release_identity.json"
    bad = json.loads(target.read_text())
    bad["tracked_files"][0]["sha256"] = "0" * 64
    target.write_text(json.dumps(bad))
    actions = []
    remote.run_systemctl = lambda action, check=True: actions.append(action)
    with pytest.raises(remote.SwitchError, match="hash mismatch"):
        remote.switch({**config, "op": "switch", "target": "new", "confirm": True})
    assert actions == []
    assert os.readlink(remote.CURRENT) == "releases/old"
    assert not remote.LOCK.exists()


def test_success_returns_normally_without_rollback_and_sets_marker_last(tmp_path):
    remote, config, manifests = sandbox(tmp_path)
    actions = []

    def systemctl(action, check=True):
        actions.append(action)
        stdout = "inactive\n" if action == "is-active" else ""
        return subprocess.CompletedProcess([], 0, stdout, "")

    remote.run_systemctl = systemctl
    remote.health = lambda release_id, wait=True: {"ok": True, "service": "active"}
    result = remote.switch({**config, "op": "switch", "target": "new", "confirm": True})
    assert result["status"] == "ok"
    assert result["rollback"] == {"attempted": False}
    assert actions == ["stop", "is-active", "start"]
    assert os.readlink(remote.CURRENT) == "releases/new"
    assert os.readlink(remote.PREVIOUS) == "releases/old"
    assert remote.EXTERNAL.read_bytes() == manifests["new"]
    assert not remote.LOCK.exists()


def test_switch_uses_atomic_release_lock_name(tmp_path):
    remote, _, _ = sandbox(tmp_path)
    assert remote.LOCK == remote.ROOT / ".release_lock"
    assert ".release_switch_lock" not in switch.REMOTE


def test_lock_is_removed_when_owner_write_fails(tmp_path, monkeypatch):
    remote, _, _ = sandbox(tmp_path)
    original = pathlib.Path.write_text

    def fail_owner(self, *args, **kwargs):
        if self == remote.LOCK / "owner":
            raise OSError("disk failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", fail_owner)
    with pytest.raises(OSError, match="disk failure"):
        remote.acquire_lock()
    assert not remote.LOCK.exists()


def test_lock_is_removed_when_owner_write_raises_file_exists(tmp_path, monkeypatch):
    remote, _, _ = sandbox(tmp_path)

    def fail_owner(self, *args, **kwargs):
        raise FileExistsError("owner collision")

    monkeypatch.setattr(pathlib.Path, "write_text", fail_owner)
    with pytest.raises(FileExistsError, match="owner collision"):
        remote.acquire_lock()
    assert not remote.LOCK.exists()


def test_dry_run_preserves_absent_previous_marker(tmp_path):
    remote, config, _ = sandbox(tmp_path)
    result = remote.switch({**config, "op": "switch", "target": "new", "confirm": False})
    assert result == {
        "status": "dry_run", "previous": "old", "target": "new",
        "current": "old", "previous_marker": None,
    }
    assert not remote.PREVIOUS.exists()


def test_failed_cutover_rolls_back_without_changing_previous_marker(tmp_path):
    remote, config, manifests = sandbox(tmp_path, releases=("old", "new", "older"))
    os.symlink("releases/older", remote.PREVIOUS)
    actions = []

    def systemctl(action, check=True):
        actions.append(action)
        stdout = "inactive\n" if action == "is-active" else ""
        return subprocess.CompletedProcess([], 0, stdout, "")

    remote.run_systemctl = systemctl
    remote.health = lambda release_id, wait=True: (
        {"ok": False, "error": "target down"} if release_id == "new"
        else {"ok": True, "service": "active"}
    )
    result = remote.switch({**config, "op": "switch", "target": "new", "confirm": True})
    assert result["status"] == "error"
    assert result["rollback"] == {"attempted": True, "ok": True,
                                  "health": {"ok": True, "service": "active"}}
    assert result["current"] == "old"
    assert result["previous_marker"] == "releases/older"
    assert os.readlink(remote.CURRENT) == "releases/old"
    assert os.readlink(remote.PREVIOUS) == "releases/older"
    assert remote.EXTERNAL.read_bytes() == manifests["old"]
    assert actions == ["stop", "is-active", "start", "stop", "is-active", "start"]


def test_stop_failure_does_not_cut_over_identity_or_current(tmp_path):
    remote, config, manifests = sandbox(tmp_path)
    os.symlink("releases/new", remote.PREVIOUS)

    def systemctl(action, check=True):
        if action == "stop":
            raise subprocess.CalledProcessError(1, "systemctl stop")
        raise AssertionError(action)

    remote.run_systemctl = systemctl
    with pytest.raises(subprocess.CalledProcessError):
        remote.switch({**config, "op": "rollback", "target": None, "confirm": True})
    assert os.readlink(remote.CURRENT) == "releases/old"
    assert remote.EXTERNAL.read_bytes() == manifests["old"]
    assert not remote.LOCK.exists()


def test_rollback_failure_never_claims_current_or_success(tmp_path):
    remote, config, _ = sandbox(tmp_path)
    calls = 0

    def systemctl(action, check=True):
        nonlocal calls
        calls += 1
        if calls >= 4:
            raise subprocess.TimeoutExpired("systemctl", 8)
        stdout = "inactive\n" if action == "is-active" else ""
        return subprocess.CompletedProcess([], 0, stdout, "")

    remote.run_systemctl = systemctl
    remote.health = lambda release_id, wait=True: {"ok": False, "error": "down"}
    result = remote.switch({**config, "op": "switch", "target": "new", "confirm": True})
    assert result["status"] == "error"
    assert result["current"] is None
    assert result["rollback"]["attempted"] is True
    assert result["rollback"]["ok"] is False
    assert result["rollback"]["error"] == "TimeoutExpired"


def test_manifest_contract_rejects_extra_fields_duplicates_and_symlinks(tmp_path):
    remote, _, _ = sandbox(tmp_path)
    manifest_path = remote.RELEASES / "new/release_identity/nmbot_release_identity.json"
    data = json.loads(manifest_path.read_text())
    data["extra"] = True
    manifest_path.write_text(json.dumps(data))
    with pytest.raises(remote.SwitchError, match="schema fields"):
        remote.release("new")

    content = (remote.RELEASES / "new/app.py").read_bytes()
    duplicate = json.loads(identity("new", content))
    duplicate["tracked_files"].append(dict(duplicate["tracked_files"][0]))
    manifest_path.write_text(json.dumps(duplicate))
    with pytest.raises(remote.SwitchError, match="value invalid"):
        remote.release("new")

    manifest_path.write_bytes(identity("new", content))
    identity_dir = manifest_path.parent
    real_dir = identity_dir.with_name("identity-real")
    identity_dir.rename(real_dir)
    os.symlink(real_dir.name, identity_dir)
    with pytest.raises(remote.SwitchError, match="directory missing or symlinked"):
        remote.release("new")


def test_health_requires_both_config_flags_and_exact_identities(tmp_path, monkeypatch):
    remote, _, _ = sandbox(tmp_path)
    remote.run_systemctl = lambda action, check=True: subprocess.CompletedProcess([], 0, "active\n", "")

    class Response:
        def __init__(self, body):
            self.body = body

        def read(self):
            return json.dumps(self.body).encode()

    good = {"ok": True, "jivo_token_configured": True, "api_token_configured": True}
    monkeypatch.setattr(remote.urllib.request, "urlopen", lambda *a, **k: Response(good))
    assert remote.health("old", wait=False)["ok"] is True
    for missing in ("jivo_token_configured", "api_token_configured"):
        body = dict(good)
        body[missing] = False
        monkeypatch.setattr(remote.urllib.request, "urlopen", lambda *a, body=body, **k: Response(body))
        assert remote.health("old", wait=False)["ok"] is False


def test_release_rejects_incomplete_identity_and_missing_artifact_manifest(tmp_path):
    remote, _, _ = sandbox(tmp_path)
    release_dir = remote.RELEASES / "new"
    identity_path = release_dir / "release_identity/nmbot_release_identity.json"
    partial = json.loads(identity_path.read_text())
    partial["tracked_files"] = []
    partial_raw = json.dumps(partial, sort_keys=True).encode()
    identity_path.write_bytes(partial_raw)
    artifact_path = remote.STAGING / "new/nmbot-new.manifest.json"
    artifact = json.loads(artifact_path.read_text())
    artifact["files"][-1]["sha256"] = hashlib.sha256(partial_raw).hexdigest()
    artifact_path.write_text(json.dumps(artifact))
    with pytest.raises(remote.SwitchError, match="tracked files invalid|tracked_files mismatch"):
        remote.release("new")

    identity_path.write_bytes(identity("new", b"new\n"))
    artifact_path.unlink()
    with pytest.raises(remote.SwitchError, match="artifact manifest missing"):
        remote.release("new")


def test_lock_cleanup_failure_overrides_success_and_preserves_result(tmp_path, monkeypatch):
    remote, config, _ = sandbox(tmp_path)

    def systemctl(action, check=True):
        stdout = "inactive\n" if action == "is-active" else ""
        return subprocess.CompletedProcess([], 0, stdout, "")

    remote.run_systemctl = systemctl
    remote.health = lambda release_id, wait=True: {"ok": True, "service": "active"}
    original_rmdir = pathlib.Path.rmdir

    def fail_lock_cleanup(self):
        if self == remote.LOCK:
            raise OSError("cleanup denied")
        return original_rmdir(self)

    monkeypatch.setattr(pathlib.Path, "rmdir", fail_lock_cleanup)
    result = remote.switch({**config, "op": "switch", "target": "new", "confirm": True})
    assert result["status"] == "error"
    assert result["original_result"]["status"] == "ok"
    assert result["cleanup_failure"] == {
        "step": "release_lock", "error": "OSError", "detail": "cleanup denied",
    }
    assert remote.LOCK.exists()


def test_remote_error_result_and_cli_exit_contract():
    fake = FakeRunner(2, {"status": "error", "error": "target health failed"})
    assert switch.execute(op="switch", target="new", confirm=True, runner=fake)["status"] == "error"
    assert "stderr" not in switch.execute.__code__.co_names
