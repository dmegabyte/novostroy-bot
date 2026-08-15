from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import tarfile
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import scripts.nmbot_atomic_release as rel


ROOT = Path(__file__).resolve().parents[1]


class FakeRemote:
    def __init__(self, *, fail: str | None = None, migrated: bool = True, remote_root: str = "/remote", previous: str = "old", release_exists: bool = False, pythonpath: bool = False, execstart_pythonpath: bool = False, systemd_identity_override: str | None = None, systemd_state_override: str | None = None, rollback_fail: bool = False, cleanup_fail: bool = False, env_files: str | None = None, exec_start_pre: str = "", exec_extra_args: str = "", bootstrap_current_symlink: bool = False, bootstrap_current_state: str = "absent", inactive_fail: bool = False) -> None:
        self.fail = fail
        self.migrated = migrated
        self.remote_root = remote_root
        self.previous = previous
        self.release_exists = release_exists
        self.pythonpath = pythonpath
        self.execstart_pythonpath = execstart_pythonpath
        self.systemd_identity_override = systemd_identity_override
        self.systemd_state_override = systemd_state_override
        self.rollback_fail = rollback_fail
        self.cleanup_fail = cleanup_fail
        self.env_files = env_files
        self.exec_start_pre = exec_start_pre
        self.exec_extra_args = exec_extra_args
        self.bootstrap_current_symlink = bootstrap_current_symlink
        self.bootstrap_current_state = bootstrap_current_state
        self.inactive_fail = inactive_fail
        self.commands: list[str] = []
        self.uploads: list[tuple[Path, str]] = []
        self.health_failed = False

    def run(self, command: str, *, input_text: str | None = None):
        self.commands.append(command)
        if "api unit FragmentPath is outside expected user systemd location" in command:
            if self.bootstrap_current_symlink:
                return subprocess.CompletedProcess([], 1, stdout=json.dumps({"ok": False, "error": "current is already an atomic release symlink"}) + "\n", stderr="")
            if self.bootstrap_current_state != "absent":
                return subprocess.CompletedProcess([], 1, stdout=json.dumps({"ok": False, "error": "current path already exists; first migration requires absent current"}) + "\n", stderr="")
            return subprocess.CompletedProcess([], 0, stdout=json.dumps({"ok": True, "unit_path": "/home/neiro/.config/systemd/user/novostroy-bot-api.service", "current_state": "absent"}) + "\n", stderr="")
        if command.startswith("systemctl --user show novostroy-bot-api.service"):
            root = f"{self.remote_root}/current" if self.migrated else self.remote_root
            script = f"{root}/scripts/nmbot_api_server.py"
            env = "PYTHONPATH=/remote" if self.pythonpath else ""
            if self.systemd_identity_override == "Environment":
                env = "NMBOT_RELEASE_IDENTITY_FILE=/tmp/fake.json"
            if self.systemd_state_override == "Environment":
                env = "NMBOT_API_STATE_FILE=/tmp/state.json"
            env_files = self.env_files if self.env_files is not None else f"{self.remote_root}/current/.env"
            exec_pre = self.exec_start_pre
            if self.systemd_identity_override == "EnvironmentFiles":
                env_files = f"{self.remote_root}/current/.env NMBOT_RELEASE_IDENTITY_FILE=/tmp/fake.json"
            if self.systemd_identity_override == "ExecStartPre":
                exec_pre = "/usr/bin/env NMBOT_RELEASE_IDENTITY_FILE=/tmp/fake.json /bin/true"
            if self.execstart_pythonpath:
                return subprocess.CompletedProcess([], 0, stdout=f"WorkingDirectory={root}\nExecStart={{ path=/usr/bin/env ; argv[]=/usr/bin/env PYTHONPATH=/tmp /usr/bin/python3 {script} ; }}\nEnvironmentFiles={env_files}\nEnvironment=\nExecStartPre={exec_pre}\n", stderr="")
            if self.systemd_identity_override == "ExecStart":
                return subprocess.CompletedProcess([], 0, stdout=f"WorkingDirectory={root}\nExecStart={{ path=/usr/bin/env ; argv[]=/usr/bin/env NMBOT_RELEASE_IDENTITY_FILE=/tmp/fake.json /usr/bin/python3 {script} ; }}\nEnvironmentFiles={env_files}\nEnvironment={env}\nExecStartPre={exec_pre}\n", stderr="")
            return subprocess.CompletedProcess([], 0, stdout=f"WorkingDirectory={root}\nExecStart={{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 {script}{self.exec_extra_args} ; }}\nEnvironmentFiles={env_files}\nEnvironment={env}\nExecStartPre={exec_pre}\n", stderr="")
        if "previous_id" in command and "current is not a release symlink" in command:
            if self.release_exists:
                return subprocess.CompletedProcess([], 0, stdout=json.dumps({"ok": True, "previous_id": self.previous, "release_exists": True}) + "\n", stderr="")
            if not self.previous:
                return subprocess.CompletedProcess([], 1, stdout=json.dumps({"ok": False, "error": "current is not a release symlink"}) + "\n", stderr="")
            if "/evil" in self.previous or ".." in self.previous:
                return subprocess.CompletedProcess([], 1, stdout=json.dumps({"ok": False, "error": "current symlink target is not a safe release"}) + "\n", stderr="")
            return subprocess.CompletedProcess([], 0, stdout=json.dumps({"ok": True, "previous_id": self.previous, "release_exists": False}) + "\n", stderr="")
        if self.rollback_fail and ".current.rollback.tmp" in command:
            return subprocess.CompletedProcess([], 1, stdout="", stderr="rollback-boom")
        if self.rollback_fail and 'shutil.copy2(backup/"api-unit.service", unit)' in command:
            return subprocess.CompletedProcess([], 1, stdout="", stderr="rollback-boom")
        if self.cleanup_fail and command == f"rmdir {self.remote_root}/.release_lock":
            return subprocess.CompletedProcess([], 1, stdout="", stderr="cleanup-boom")
        if "systemctl" in command and "--user" in command and "is-active" in command:
            if self.inactive_fail:
                return subprocess.CompletedProcess([], 1, stdout=json.dumps({"ok": False, "error": "api still active"}) + "\n", stderr="")
            return subprocess.CompletedProcess([], 0, stdout=json.dumps({"ok": True, "state": "inactive"}) + "\n", stderr="")
        if self.fail and self.fail in command:
            if self.fail == "urllib.request" and not self.health_failed:
                self.health_failed = True
                return subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
            if self.fail == "urllib.request":
                return subprocess.CompletedProcess([], 0, stdout="ok\n", stderr="")
            return subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
        if "unit.name+\".bootstrap." in command and "os.replace" in command:
            self.migrated = True
        return subprocess.CompletedProcess([], 0, stdout="ok\n", stderr="")

    def upload(self, local: Path, remote_path: str):
        self.uploads.append((local, remote_path))
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")


class HelperOverlayRemote(FakeRemote):
    snapshot_serial = 0

    def __init__(self, *, fail_publish: bool = False, fail_stage: bool = False, fail_capture: bool = False, migrated: bool = True, remote_root: str = rel.DEFAULT_REMOTE_ROOT) -> None:
        super().__init__(migrated=migrated, remote_root=remote_root)
        self.fail_publish = fail_publish
        self.fail_stage = fail_stage
        self.fail_capture = fail_capture
        self.snapshot_calls = 0

    def run_binary(self, command: str):
        self.commands.append(command)
        self.snapshot_calls += 1
        if self.fail_capture:
            return subprocess.CompletedProcess([], 2, stdout=b"", stderr=b"capture failed\n")
        manifest = _minimal_snapshot_manifest()
        type(self).snapshot_serial += 1
        manifest["snapshot_id"] = f"vps-source-helper-{os.getpid()}-{type(self).snapshot_serial}"
        manifest["created_at_utc"] = rel.datetime.now(rel.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return subprocess.CompletedProcess([], 0, stdout=_snapshot_tar_bytes(manifest), stderr=b"")

    def run(self, command: str, *, input_text: str | None = None):
        if '"operation":"live_api_helper_overlay"' in command:
            self.commands.append(command)
            payload = json.loads(__import__("shlex").split(command)[-1])
            if self.fail_publish and payload["mode"] == "publish":
                return subprocess.CompletedProcess([], 2, stdout='{"ok":false,"error":"helper publish failed; backup restored=true"}\n', stderr="")
            if self.fail_stage and payload["mode"] == "stage":
                return subprocess.CompletedProcess([], 2, stdout='{"ok":false,"error":"staged helper hash mismatch"}\n', stderr="")
            return subprocess.CompletedProcess([], 0, stdout=json.dumps({"ok": True, "operation": "live_api_helper_overlay", "release_id": payload["release_id"], "destination": payload["destination"], "sha256": payload["expected_sha256"], "backup": "/private/backup"}) + "\n", stderr="")
        return super().run(command, input_text=input_text)


class FakeBinaryRemote:
    def __init__(self, root: Path, *, fail: bool = False) -> None:
        self.root = root
        self.fail = fail
        self.commands: list[str] = []

    def run_binary(self, command: str):
        self.commands.append(command)
        if self.fail:
            return subprocess.CompletedProcess([], 2, stdout=b"", stderr=b"SECRET_TOKEN=super-secret-value\n")
        words = __import__("shlex").split(command)
        payload = json.loads(words[-1])
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for relpath in payload["paths"]:
                source = self.root / relpath
                if not source.exists() or source.is_symlink() or not source.is_file():
                    return subprocess.CompletedProcess([], 2, stdout=b"", stderr=f"requested path is not safe regular file: {relpath}\n".encode())
                info = tf.gettarinfo(str(source), arcname=relpath)
                with source.open("rb") as fh:
                    tf.addfile(info, fh)
        return subprocess.CompletedProcess([], 0, stdout=buf.getvalue(), stderr=b"")


class StdoutLeakingBinaryRemote:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run_binary(self, command: str):
        self.commands.append(command)
        return subprocess.CompletedProcess([], 9, stdout=b"PROMPT_BODY=do not leak\nAPI_TOKEN=stdout-secret-value\n", stderr=b"NMBOT_API_TOKEN=stderr-secret-value\n")


class RacingBinaryRemote(FakeBinaryRemote):
    def __init__(self, root: Path, competitor: Path, *, broken_symlink: bool = False) -> None:
        super().__init__(root)
        self.competitor = competitor
        self.broken_symlink = broken_symlink

    def run_binary(self, command: str):
        proc = super().run_binary(command)
        self.competitor.parent.mkdir(parents=True, exist_ok=True)
        if self.broken_symlink:
            self.competitor.symlink_to(self.competitor.parent / "missing-competitor")
        else:
            self.competitor.write_bytes(b"competitor")
        return proc


class LocalBinaryCommandRemote:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run_binary(self, command: str):
        self.commands.append(command)
        return subprocess.run(command, shell=True, capture_output=True, check=False)


def _copy_contract_tree(dest: Path) -> None:
    for relpath in rel._contract_capture_paths(ROOT):
        target = dest / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relpath).read_bytes())


def _tree_inventory(root: Path) -> dict[str, tuple[str, int, int | None, str | None]]:
    inventory: dict[str, tuple[str, int, int | None, str | None]] = {}
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        st = path.lstat()
        relpath = path.relative_to(root).as_posix()
        mode = st.st_mode & 0o7777
        mtime_ns = st.st_mtime_ns
        if path.is_symlink():
            inventory[relpath] = ("symlink", mode, mtime_ns, os.readlink(path))
        elif path.is_dir():
            inventory[relpath] = ("dir", mode, mtime_ns, None)
        elif path.is_file():
            inventory[relpath] = ("file", mode, mtime_ns, rel._sha256_file(path))
        else:
            inventory[relpath] = ("other", mode, mtime_ns, None)
    return inventory


def _valid_recon_payload() -> dict:
    required = set(rel.CONFIG_REQUIREMENTS["required_secret_names"]) | set(rel.CONFIG_REQUIREMENTS["required_setting_names"]) | set(rel.CONFIG_REQUIREMENTS["required_mode_names"])
    return {
        "ok": True,
        "remote_root": rel.DEFAULT_REMOTE_ROOT,
        "paths": {
            "current": f"{rel.DEFAULT_REMOTE_ROOT}/current",
            "env_file": f"{rel.DEFAULT_REMOTE_ROOT}/.env",
            "identity_file": f"{rel.DEFAULT_REMOTE_ROOT}/{rel.IDENTITY_EXTERNAL}",
        },
        "systemd": {
            "show_ok": True,
            "working_directory_is_canonical": True,
            "execstart_mentions_current_api": True,
            "environment_file_canonical": True,
            "exec_start_pre_empty": True,
            "has_environment_inline": False,
        },
        "env_names": {name: {"present": True, "nonempty": True} for name in sorted(required)},
        "canonical_api": {"NMBOT_API_HOST": True, "NMBOT_API_PORT": True},
        "modes": {"NMBOT_V2_MANAGER_REWRITER_MODE": True, "NMBOT_V3_MANAGER_REWRITER_MODE": True},
        "current": {"is_symlink": True, "target_name": "REL-current"},
        "identity": {"exists": True, "schema_ok": True, "release_id_present": True, "release_id": "REL-current", "tracked_hashes_shape_ok": True},
        "health": {"reachable": True, "ok": True, "jivo_token_configured": True, "api_token_configured": True},
    }


def _tar_bytes(names: list[str], *, payload: bytes = b"print('ok')\n") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name in names:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


class BridgeBinaryRemote:
    def __init__(self, root: Path, *, current_release: str | None = None, fail: bool = False, api_current_release: str | None = None) -> None:
        self.root = root
        self.current_release = current_release
        self.api_current_release = api_current_release
        self.fail = fail
        self.commands: list[str] = []

    def run_binary(self, command: str):
        self.commands.append(command)
        if self.fail:
            return subprocess.CompletedProcess([], 2, stdout=b"NMBOT_BRIDGE_TOKEN=secret\n", stderr=b"API_TOKEN=secret\n")
        payload = json.loads(__import__("shlex").split(command)[-1])
        source = self.root
        source_mode = "first_migration_canonical"
        active = ""
        api_active = ""
        if self.current_release:
            active = self.current_release
            source = self.root / rel.BRIDGE_RELEASES / active
            source_mode = "bridge_current"
        rows = []
        for relpath in payload["allowlist"]:
            scope = "bridge_current" if self.current_release else "bridge_canonical"
            path = source / relpath
            if not self.current_release and relpath == rel.BRIDGE_ENTRYPOINT.replace("nmbot_n8n_bridge_server.py", "nmbot_egress_policy.py") and not path.exists() and self.api_current_release:
                scope = "api_current"
                api_active = self.api_current_release
                source_mode = "first_migration_mixed"
                path = self.root / "releases" / self.api_current_release / relpath
            if not path.exists() or path.is_symlink() or not path.is_file():
                return subprocess.CompletedProcess([], 2, stdout=b"", stderr=f"requested path is not safe regular file: {relpath}\n".encode())
            data = path.read_bytes()
            rows.append({"path": relpath, "sha256": rel._sha256_bytes(data), "size": len(data), "mode": 0o755, "source_scope": scope, "data": data})
        sid = "bridge-source-20260728-000000-" + rel._sha256_bytes(str(self.root).encode("utf-8"))[:12]
        manifest = {
            "schema_version": rel.BRIDGE_SNAPSHOT_SCHEMA_VERSION,
            "snapshot_id": sid,
            "created_at_utc": "2026-07-28T00:00:00Z",
            "source_host": rel.AUTHORIZED_DEPLOY_HOST,
            "remote_root": rel.DEFAULT_REMOTE_ROOT,
            "source_mode": source_mode,
            "active_release_id": active,
            "api_current_release_id": api_active,
            "policy": {"allowlist": list(rel.BRIDGE_ALLOWED_FILES), "exclude_env_data_logs_units": True, "reject_symlinks": True},
            "files": [{k: r[k] for k in ("path", "sha256", "size", "mode", "source_scope")} for r in rows],
            "tar_members": [rel.SNAPSHOT_MANIFEST_NAME] + [rel.SNAPSHOT_SOURCE_PREFIX + r["path"] for r in rows],
        }
        mb = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(rel.SNAPSHOT_MANIFEST_NAME)
            info.size = len(mb)
            tf.addfile(info, io.BytesIO(mb))
            for row in rows:
                info = tarfile.TarInfo(rel.SNAPSHOT_SOURCE_PREFIX + row["path"])
                info.size = row["size"]
                info.mode = 0o755
                tf.addfile(info, io.BytesIO(row["data"]))
        return subprocess.CompletedProcess([], 0, stdout=buf.getvalue(), stderr=b"")


class BridgeRemote:
    def __init__(self, *, fail_after: str | None = None, fail_guard: bool = False) -> None:
        self.fail_after = fail_after
        self.fail_guard = fail_guard
        self.commands: list[str] = []
        self.uploads: list[tuple[Path, str]] = []

    def run(self, command: str, *, input_text: str | None = None):
        self.commands.append(command)
        if self.fail_guard and "bridge baseline hash mismatch" in command:
            return subprocess.CompletedProcess([], 1, stdout=json.dumps({"ok": False, "error": "bridge baseline hash mismatch"}) + "\n", stderr="")
        if self.fail_after and self.fail_after in command:
            return subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
        if "previous_state" in command and "bridge baseline hash mismatch" in command:
            return subprocess.CompletedProcess([], 0, stdout=json.dumps({"ok": True, "unit_path": rel.BRIDGE_UNIT_PATH, "previous_state": "absent", "previous_target": "", "previous_working_directory": rel.DEFAULT_REMOTE_ROOT, "previous_exec_argv": ["/usr/bin/python3", f"{rel.DEFAULT_REMOTE_ROOT}/{rel.BRIDGE_ENTRYPOINT}", "--host", "0.0.0.0", "--port", "8093"], "previous_environment_file": f"{rel.DEFAULT_REMOTE_ROOT}/.env", "previous_inline_environment": rel.BRIDGE_INLINE_ENVIRONMENT, "previous_fragment_path": rel.BRIDGE_UNIT_PATH}) + "\n", stderr="")
        if "remote bridge recon" not in command and "active_manifest" in command:
            payload = {
                "ok": True,
                "remote_root": rel.DEFAULT_REMOTE_ROOT,
                "service": rel.BRIDGE_SERVICE,
                "unit": {"fragment_path": rel.BRIDGE_UNIT_PATH, "environment_file": f"{rel.DEFAULT_REMOTE_ROOT}/.env", "expected_execstart": f"/usr/bin/python3 {rel.DEFAULT_REMOTE_ROOT}/{rel.BRIDGE_ENTRYPOINT} --host 0.0.0.0 --port 8093", "expected_working_directory": rel.DEFAULT_REMOTE_ROOT},
                "systemd": {"fragment_path_ok": True, "environment_file_canonical": True, "inline_environment_expected": True, "active": True, "main_pid_present": True, "execstart_expected": True, "working_directory_expected": True},
                "bridge_current": {"state": "absent", "target_name": "", "safe_release_symlink": True},
                "health": {"reachable": True, "ok": True},
                "active_manifest": {"exists": False, "schema_ok": False, "release_id": "", "tracked_hashes_match": False},
            }
            return subprocess.CompletedProcess([], 0, stdout=json.dumps(payload) + "\n", stderr="")
        return subprocess.CompletedProcess([], 0, stdout=json.dumps({"ok": True}) + "\n", stderr="")

    def upload(self, local: Path, remote_path: str):
        self.uploads.append((local, remote_path))
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")


def _copy_bridge_sources(dest: Path) -> None:
    for relpath in rel.BRIDGE_ALLOWED_FILES:
        target = dest / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relpath).read_bytes())
        target.chmod(0o755)


def _build_bridge_artifact(tmp_path: Path, *, release_id: str = "bridge-rel-001") -> tuple[rel.Artifact, str]:
    source = tmp_path / "remote"
    _copy_bridge_sources(source)
    base = Path("/tmp/opencode") / "nmbot-bridge-tests" / tmp_path.name
    snap = rel.snapshot_vps_bridge_source(remote=BridgeBinaryRemote(source), out_dir=base / "snaps")
    work = rel.prepare_bridge_worktree(snapshot_dir=Path(snap["snapshot_dir"]), out_dir=base / "work")
    artifact = rel.build_bridge_from_worktree(worktree_dir=Path(work["worktree_dir"]), release_id=release_id, out_dir=tmp_path / "out")
    return artifact, snap["manifest_sha256"]


def _build_bridge_artifact_with_candidate_change(tmp_path: Path, *, release_id: str = "bridge-rel-001") -> tuple[rel.Artifact, str]:
    source = tmp_path / "remote"
    _copy_bridge_sources(source)
    base = Path("/tmp/opencode") / "nmbot-bridge-tests" / (tmp_path.name + "-changed")
    snap = rel.snapshot_vps_bridge_source(remote=BridgeBinaryRemote(source), out_dir=base / "snaps")
    work = rel.prepare_bridge_worktree(snapshot_dir=Path(snap["snapshot_dir"]), out_dir=base / "work")
    changed = Path(work["worktree_dir"]) / "source" / rel.BRIDGE_ENTRYPOINT
    changed.write_text(changed.read_text(encoding="utf-8") + "\n# candidate-only reviewed change\n", encoding="utf-8")
    artifact = rel.build_bridge_from_worktree(worktree_dir=Path(work["worktree_dir"]), release_id=release_id, out_dir=tmp_path / "out")
    return artifact, snap["manifest_sha256"]


def _run_bridge_guard_generated(command: str, tmp_path: Path, *, root: Path, working_directory: str, exec_script: str, env_files: str | None = None, inline_environment: str | None = None) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "systemctl"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "print('FragmentPath=' + os.environ['FAKE_FRAGMENT'])\n"
        "print('EnvironmentFiles=' + os.environ['FAKE_ENVFILES'])\n"
        "print('WorkingDirectory=' + os.environ['FAKE_WD'])\n"
        "print('ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 ' + os.environ['FAKE_SCRIPT'] + ' --host 0.0.0.0 --port 8093 ; }')\n"
        "print('ExecStartPre=')\n"
        "print('Environment=' + os.environ.get('FAKE_INLINE_ENV', ''))\n"
        "print('DropInPaths=')\n"
        "print('ActiveState=active')\n"
        "print('SubState=running')\n"
        "print('MainPID=123')\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
        "FAKE_FRAGMENT": rel.BRIDGE_UNIT_PATH,
        "FAKE_ENVFILES": env_files or f"{rel.DEFAULT_REMOTE_ROOT}/.env (ignore_errors=no)",
        "FAKE_WD": working_directory,
        "FAKE_SCRIPT": exec_script,
        "FAKE_INLINE_ENV": rel.BRIDGE_INLINE_ENVIRONMENT if inline_environment is None else inline_environment,
    })
    return subprocess.run(command, shell=True, text=True, capture_output=True, check=False, env=env, cwd=root)


def _snapshot_tar_bytes(manifest: dict, payloads: dict[str, bytes] | None = None, *, extra: str | None = None, duplicate: bool = False, mode_delta: int = 0) -> bytes:
    payloads = payloads or {item["path"]: b"print('ok')\n" for item in manifest["files"]}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        body = rel._canonical_snapshot_manifest_bytes(manifest)
        info = tarfile.TarInfo(rel.SNAPSHOT_MANIFEST_NAME)
        info.size = len(body)
        info.mode = 0o644
        tf.addfile(info, io.BytesIO(body))
        for item in manifest["files"]:
            data = payloads[item["path"]]
            info = tarfile.TarInfo(rel.SNAPSHOT_SOURCE_PREFIX + item["path"])
            info.size = len(data)
            info.mode = item["mode"] + mode_delta
            tf.addfile(info, io.BytesIO(data))
            if duplicate:
                tf.addfile(info, io.BytesIO(data))
        if extra:
            info = tarfile.TarInfo(extra)
            data = b"extra\n"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _minimal_snapshot_manifest(payload: bytes = b"print('ok')\n") -> dict:
    files = [{"path": "scripts/nmbot_api_server.py", "sha256": rel._sha256_bytes(payload), "size": len(payload), "mode": 0o755}]
    return {
        "schema_version": rel.SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": "vps-source-test-123",
        "created_at_utc": "2026-07-24T00:00:00Z",
        "source_host": rel.AUTHORIZED_DEPLOY_HOST,
        "remote_root": rel.DEFAULT_REMOTE_ROOT,
        "contour": rel.DEFAULT_SNAPSHOT_CONTOUR,
        "policy": {"roots": list(rel.SNAPSHOT_ROOTS), "root_files": list(rel.SNAPSHOT_ROOT_FILES), "runtime_suffixes": list(rel.RUNTIME_SUFFIXES), "exclude_secret_like": True, "exclude_hidden": True, "exclude_deploy_control_scripts": True},
        "files": files,
        "tar_members": [rel.SNAPSHOT_MANIFEST_NAME, rel.SNAPSHOT_SOURCE_PREFIX + files[0]["path"]],
    }


def _rewrite_identity_artifact(artifact: rel.Artifact, tmp_path: Path, identity: dict) -> tuple[Path, Path]:
    archive = tmp_path / artifact.archive.name
    manifest = json.loads(artifact.manifest.read_text(encoding="utf-8"))
    identity_payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    with tarfile.open(archive, "w:gz") as out, tarfile.open(artifact.archive, "r:gz") as src:
        for member in src.getmembers():
            data = identity_payload if member.name == rel.IDENTITY_IN_RELEASE else src.extractfile(member).read()
            info = tarfile.TarInfo(member.name)
            info.size = len(data)
            info.mode = member.mode
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            out.addfile(info, io.BytesIO(data))
    for item in manifest["files"]:
        if item["path"] == rel.IDENTITY_IN_RELEASE:
            item["sha256"] = rel._sha256_bytes(identity_payload)
    manifest["archive_sha256"] = rel._sha256_file(archive)
    manifest_path = tmp_path / artifact.manifest.name
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return archive, manifest_path


def _remove_artifact_file_and_rewrite_identity(artifact: rel.Artifact, tmp_path: Path, missing: str) -> tuple[Path, Path]:
    manifest = json.loads(artifact.manifest.read_text(encoding="utf-8"))
    extract = tmp_path / f"without-{Path(missing).stem}"
    rel.safe_extract(artifact.archive, extract)
    (extract / missing).unlink()
    manifest["files"] = [item for item in manifest["files"] if item["path"] != missing]
    identity_payload = json.dumps({
        "schema": "nmbot.release_identity.v1",
        "release_id": manifest["release_id"],
        "generated_at": "deterministic-build-clock-not-recorded",
        "tracked_files": [item for item in manifest["files"] if item["path"] != rel.IDENTITY_IN_RELEASE],
    }, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    (extract / rel.IDENTITY_IN_RELEASE).write_bytes(identity_payload)
    for item in manifest["files"]:
        if item["path"] == rel.IDENTITY_IN_RELEASE:
            item["sha256"] = rel._sha256_bytes(identity_payload)
    archive = tmp_path / artifact.archive.name
    with tarfile.open(archive, "w:gz") as tf:
        for item in manifest["files"]:
            path = extract / item["path"]
            info = tarfile.TarInfo(item["path"])
            data = path.read_bytes()
            info.size = len(data)
            info.mode = 0o755 if item["path"].startswith("scripts/") and item["path"].endswith(".py") else 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
    manifest["archive_sha256"] = rel._sha256_file(archive)
    manifest_path = tmp_path / f"missing-{Path(missing).stem}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return archive, manifest_path


def _with_valid_source_provenance(artifact: rel.Artifact, tmp_path: Path, *, snapshot_sha: str = "a" * 64) -> Path:
    manifest = json.loads(artifact.manifest.read_text(encoding="utf-8"))
    manifest["source_provenance"] = {
        "present": True,
        "source_snapshot_id": "vps-source-test-123",
        "source_snapshot_manifest_sha256": snapshot_sha,
        "source_host": rel.AUTHORIZED_DEPLOY_HOST,
        "remote_root": rel.DEFAULT_REMOTE_ROOT,
        "contour": rel.DEFAULT_SNAPSHOT_CONTOUR,
        "worktree_source_tree_sha256": "b" * 64,
        "worktree_source_manifest_sha256": "c" * 64,
        "worktree_provenance_sha256": "d" * 64,
    }
    path = tmp_path / (artifact.manifest.stem + ".provenance.json")
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def test_build_excludes_secrets_runtime_noise_and_manifest_has_names_only(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-atomic-test", out_dir=tmp_path)
    manifest = rel.load_manifest(artifact.manifest)
    paths = {item["path"] for item in manifest["files"]}

    assert ".env" not in paths
    assert not any(path.startswith(("logs/", "data/", "release_bundles/", "eval/", "results/", "reports/")) for path in paths)
    assert "scripts/nmbot_api_server.py" in paths
    assert "scripts/nmbot_n8n_bridge_server.py" not in paths
    assert "scripts/nmbot_callback_sheet_worker.py" not in paths
    assert "nmbot_v1" in rel.RUNTIME_DIRS
    assert "nmbot_v1" in rel.SNAPSHOT_ROOTS
    assert "nmbot_v4" in rel.RUNTIME_DIRS
    assert "nmbot_v4" in rel.SNAPSHOT_ROOTS
    assert "nmbot_v1/runtime.py" in paths
    assert "nmbot_v1/state.py" in paths
    assert "nmbot_v1/contracts.py" in paths
    assert "nmbot_v1/ports.py" in paths
    assert "nmbot_v1/search_contract.py" in paths
    assert "nmbot_v1/search.py" in paths
    assert "nmbot_v1/response.py" in paths
    assert "nmbot_v4/runtime.py" in paths
    assert "nmbot_v1/execution_path.py" in paths
    encoded_requirements = json.dumps(manifest["config_schema_requirements"], ensure_ascii=False, sort_keys=True)
    assert "JIVO_PROVIDER_TOKEN" in encoded_requirements
    assert "=" not in encoded_requirements
    assert manifest["import_modules"] == list(rel.IMPORT_MODULES)
    assert manifest["config_schema_requirements"] == rel.CONFIG_REQUIREMENTS
    assert ".env.client-production" not in manifest["config_schema_requirements"]["external_runtime_paths"]
    assert ".env.client-production" not in manifest["external_runtime_strategy"]
    assert manifest["source_provenance"] == {"present": False}


def test_v1_import_contract_tracks_existing_stable_modules_without_placeholders() -> None:
    expected = tuple(
        module for module in rel.NMBOT_V1_IMPORT_MODULE_CANDIDATES
        if (ROOT / (module.replace(".", "/") + ".py")).is_file()
    )

    assert rel.NMBOT_V1_IMPORT_MODULES == expected
    assert "nmbot_v1.runtime" in rel.IMPORT_MODULES
    assert "nmbot_v1.state" in rel.IMPORT_MODULES
    assert "nmbot_v1.contracts" in rel.IMPORT_MODULES
    assert "nmbot_v1.ports" in rel.IMPORT_MODULES
    assert "nmbot_v1.search_contract" in rel.IMPORT_MODULES
    assert "nmbot_v1.search" in rel.IMPORT_MODULES
    assert "nmbot_v1.response" in rel.IMPORT_MODULES
    assert "nmbot_v1.execution_path" in rel.IMPORT_MODULES


def test_v1_prompt_files_are_packaged_when_present_without_content_assertions(tmp_path: Path) -> None:
    root = tmp_path / "source-with-v1-prompt"
    _copy_contract_tree(root)
    prompt = root / "prompts" / "v1" / "release_prompt.txt"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("V1 prompt fixture only; contents are not inspected.\n", encoding="utf-8")

    artifact = rel.build(release_id="REL-v1-prompt", out_dir=tmp_path / "out", root=root)
    paths = {item["path"] for item in rel.load_manifest(artifact.manifest)["files"]}

    assert "prompts/v1/release_prompt.txt" in paths


def test_required_candidate_prompt_is_exactly_allowlisted_for_api_startup(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-required-candidate", out_dir=tmp_path)
    paths = {item["path"] for item in rel.load_manifest(artifact.manifest)["files"]}

    assert rel.REQUIRED_RUNTIME_RESOURCE_PATHS <= paths
    assert "prompts/candidates/v1_unified_planner_gpt55_experiment_v1.txt" not in paths
    assert "prompts/candidates/v0_answer_writer_promptmaster_v10.txt" not in paths


def test_missing_required_candidate_prompt_fails_build_and_local_preflight(tmp_path: Path) -> None:
    root = tmp_path / "source-without-required-candidate"
    _copy_contract_tree(root)
    required = next(iter(rel.REQUIRED_RUNTIME_RESOURCE_PATHS))
    (root / required).unlink()

    with pytest.raises(rel.ReleaseError, match="required runtime resource missing from release"):
        rel.build(release_id="REL-missing-required-candidate", out_dir=tmp_path / "missing-build", root=root)

    artifact = rel.build(release_id="REL-required-candidate-regression", out_dir=tmp_path / "complete")
    broken_archive, broken_manifest = _remove_artifact_file_and_rewrite_identity(artifact, tmp_path, required)
    with pytest.raises(rel.ReleaseError, match="required runtime resource missing from release"):
        rel.local_preflight(archive=broken_archive, manifest_path=broken_manifest)


def test_local_preflight_starts_extracted_api_without_provider_requests(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-extracted-create-app", out_dir=tmp_path)

    result = rel.local_preflight(archive=artifact.archive, manifest_path=artifact.manifest)

    assert result.startswith("preflight=ok")
    assert "startup=create_app" in result


def test_secret_filter_rejects_database_filenames_and_json_yaml_secret_assignments(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _copy_contract_tree(root)
    bad_files = [
        ("prompts/database.json", "{}\n"),
        ("prompts/app.sqlite3", "sqlite bytes\n"),
        ("prompts/settings.yaml", "api_token: quoted-secret-value\n"),
        ("prompts/settings.json", '"api_key" = "quoted-secret-value"\n'),
        ("prompts/passwords.yml", "safe: text\n"),
    ]
    for idx, (relative, content) in enumerate(bad_files):
        candidate = root / relative
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(content, encoding="utf-8")
        try:
            rel.build(release_id=f"secret-filter-{idx}", out_dir=tmp_path / f"out-{idx}", root=root)
        except rel.ReleaseError as exc:
            assert "secret-like" in str(exc)
            assert "quoted-secret-value" not in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"secret-like file/content must fail: {relative}")
        candidate.unlink()


def test_manifest_and_archive_tamper_detection(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-tamper-test", out_dir=tmp_path)
    rel.verify_archive_against_manifest(artifact.archive, rel.load_manifest(artifact.manifest))
    data = artifact.archive.read_bytes()
    artifact.archive.write_bytes(data + b"tamper")
    try:
        rel.verify_archive_against_manifest(artifact.archive, rel.load_manifest(artifact.manifest))
    except rel.ReleaseError as exc:
        assert "archive sha256 mismatch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("tampered archive must fail")


def test_build_is_deterministic_for_same_release_id(tmp_path: Path) -> None:
    first = rel.build(release_id="REL-deterministic", out_dir=tmp_path / "one")
    second = rel.build(release_id="REL-deterministic", out_dir=tmp_path / "two")

    assert rel._sha256_file(first.archive) == rel._sha256_file(second.archive)
    left = json.loads(first.manifest.read_text(encoding="utf-8"))
    right = json.loads(second.manifest.read_text(encoding="utf-8"))
    assert left == right


def test_local_preflight_from_extracted_tree_catches_missing_dependency(tmp_path: Path, monkeypatch) -> None:
    artifact = rel.build(release_id="REL-import-test", out_dir=tmp_path)
    extract = tmp_path / "extract"
    extract.mkdir()
    rel.safe_extract(artifact.archive, extract)
    (extract / "nmbot_v2" / "response_composer.py").unlink()
    broken = tmp_path / "nmbot-REL-import-test.tar.gz"
    with tarfile.open(broken, "w:gz") as tf:
        for path in sorted(extract.rglob("*")):
            if path.is_file():
                tf.add(path, arcname=path.relative_to(extract).as_posix())
    manifest = rel.load_manifest(artifact.manifest)
    manifest["archive_sha256"] = rel._sha256_file(broken)
    manifest["files"] = [item for item in manifest["files"] if item["path"] != "nmbot_v2/response_composer.py"]
    broken_manifest = tmp_path / "broken.manifest.json"
    broken_manifest.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    fake_external = tmp_path / "external"
    (fake_external / "nmbot_v2").mkdir(parents=True)
    (fake_external / "nmbot_v2" / "__init__.py").write_text("", encoding="utf-8")
    (fake_external / "nmbot_v2" / "response_composer.py").write_text("FAKE = True\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(fake_external))

    try:
        rel.local_preflight(archive=broken, manifest_path=broken_manifest)
    except rel.ReleaseError as exc:
        assert "response_composer" in str(exc) or "No module" in str(exc) or "release identity tracked_files mismatch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing import dependency must fail")


def test_local_preflight_requires_full_remote_preflight_source_closure(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-preflight-closure", out_dir=tmp_path / "complete")
    ok = rel.local_preflight(archive=artifact.archive, manifest_path=artifact.manifest)
    assert ok.startswith("preflight=ok")
    artifact_paths = {item["path"] for item in rel.load_manifest(artifact.manifest)["files"]}
    local_python_paths = {path for path in artifact_paths if path.endswith(".py")}
    v0_writer_paths = {
        "scripts/gateway_v0_answer_writer.py",
        "scripts/bluesminds_v0_answer_writer.py",
    }
    optional_api_paths = v0_writer_paths | {"scripts/nmbot_v6_journal.py", "scripts/nmbot_v6_simple_adapter.py"}
    adapter_source = (ROOT / "scripts" / "nmbot_runtime_adapter.py").read_text(encoding="utf-8")
    assert "gateway_v0_answer_writer" in adapter_source
    assert "bluesminds_v0_answer_writer" in adapter_source
    assert set(rel.REMOTE_PREFLIGHT_PY_FILES) <= local_python_paths
    v1_python_paths = {path for path in local_python_paths if path.startswith("nmbot_v1/")}
    assert v1_python_paths
    assert v1_python_paths <= set(rel._remote_preflight_py_files(artifact_paths))
    assert rel.OPTIONAL_API_RUNTIME_SCRIPT_FILES == optional_api_paths
    assert v0_writer_paths <= rel.API_RUNTIME_SCRIPT_FILES
    assert optional_api_paths.isdisjoint(rel.REMOTE_PREFLIGHT_PY_FILES)
    assert v0_writer_paths <= artifact_paths
    assert "scripts/nmbot_v6_simple_adapter.py" in rel.API_RUNTIME_SCRIPT_FILES
    assert "scripts/nmbot_v6_simple_adapter.py" not in artifact_paths
    assert f"py_compile={len(local_python_paths)}" in ok
    assert "scripts/nmbot_egress_policy.py" in artifact_paths
    assert "scripts/chat_tester_bot.py" not in artifact_paths
    assert "scripts/nmbot_env_secrets.py" not in artifact_paths
    assert "scripts/nmbot_atomic_release.py" not in artifact_paths

    remote_command = rel._remote_preflight_command("/remote/releases/REL-preflight-closure")
    for required_path in rel.REMOTE_PREFLIGHT_PY_FILES:
        assert required_path in remote_command
    assert "nmbot_v1" in remote_command
    assert "rglob(\"*.py\")" in remote_command
    for writer_path in v0_writer_paths:
        assert writer_path not in remote_command

    broken_archive, broken_manifest = _remove_artifact_file_and_rewrite_identity(artifact, tmp_path, "scripts/nmbot_egress_policy.py")
    try:
        rel.local_preflight(archive=broken_archive, manifest_path=broken_manifest)
    except rel.ReleaseError as exc:
        assert "remote preflight source missing" in str(exc)
        assert "scripts/nmbot_egress_policy.py" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("artifact missing remote preflight source must fail locally")

    root = tmp_path / "missing-worktree-source"
    _copy_contract_tree(root)
    (root / "scripts" / "nmbot_egress_policy.py").unlink()
    try:
        rel.build(release_id="REL-missing-closure", out_dir=tmp_path / "missing-build", root=root)
    except rel.ReleaseError as exc:
        assert "remote preflight source missing" in str(exc)
        assert "scripts/nmbot_egress_policy.py" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("build from source missing remote preflight file must fail")


def test_v6_only_remote_preflight_renders_exact_profile_contract() -> None:
    command = rel._remote_preflight_command(
        "/remote/releases/REL-v6-only",
        list(rel.V6_ONLY_IMPORT_MODULES),
        list(rel.V6_ONLY_PREFLIGHT_PY_FILES),
        profile=rel.V6_ONLY_PROFILE,
    )
    payload = json.loads(shlex.split(command)[-1])

    assert payload == {
        "profile": rel.V6_ONLY_PROFILE,
        "modules": list(rel.V6_ONLY_IMPORT_MODULES),
        "compile_files": list(rel.V6_ONLY_PREFLIGHT_PY_FILES),
        "required_dependencies": list(rel.V6_ONLY_REQUIRED_DEPENDENCIES),
    }
    assert set(payload["compile_files"]) == {path for path in rel.V6_ONLY_RUNTIME_FILES if path.endswith(".py")}
    assert not any(path.startswith(("nmbot_v0/", "nmbot_v1/", "nmbot_v2/", "nmbot_v4/")) for path in payload["compile_files"])

    with pytest.raises(rel.ReleaseError, match="exactly match its allowlist"):
        rel._remote_preflight_command(
            "/remote/releases/REL-v6-only",
            list(rel.V6_ONLY_IMPORT_MODULES),
            list(rel.V6_ONLY_PREFLIGHT_PY_FILES) + ["scripts/unknown.py"],
            profile=rel.V6_ONLY_PROFILE,
        )
    with pytest.raises(rel.ReleaseError, match="profile contract"):
        rel._remote_preflight_command(
            "/remote/releases/REL-v6-only",
            list(rel.IMPORT_MODULES),
            profile=rel.V6_ONLY_PROFILE,
        )


def test_default_remote_preflight_payload_remains_unprofiled() -> None:
    command = rel._remote_preflight_command("/remote/releases/REL-default")
    payload = json.loads(shlex.split(command)[-1])

    assert payload == {
        "modules": list(rel.IMPORT_MODULES),
        "compile_files": list(rel.REMOTE_PREFLIGHT_PY_FILES),
    }
    assert "profile" not in payload
    assert "required_dependencies" not in payload


def test_v6_only_remote_preflight_fails_when_required_dependency_is_absent(tmp_path: Path, monkeypatch) -> None:
    release_root = tmp_path / "release"
    for relative in rel.V6_ONLY_PREFLIGHT_PY_FILES:
        path = release_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# isolated preflight fixture\n", encoding="utf-8")
    missing_dependency = "nmbot_phase2_intentionally_missing_dependency"
    monkeypatch.setattr(rel, "V6_ONLY_REQUIRED_DEPENDENCIES", (missing_dependency,))
    command = rel._remote_preflight_command(
        str(release_root),
        list(rel.V6_ONLY_IMPORT_MODULES),
        list(rel.V6_ONLY_PREFLIGHT_PY_FILES),
        profile=rel.V6_ONLY_PROFILE,
    )

    proc = subprocess.run(command, shell=True, text=True, capture_output=True, check=False)

    assert proc.returncode != 0
    assert missing_dependency in proc.stderr


def test_optional_v0_writer_absent_when_adapter_imports_only_gateway_preflights(tmp_path: Path) -> None:
    root = tmp_path / "gateway-only-source"
    _copy_contract_tree(root)
    adapter = """
from scripts import gateway_v0_answer_writer

def _canonical_v0_envelope(*args, **kwargs):
    return {}

def _canonical_v1_envelope(*args, **kwargs):
    return {}

def _canonical_v4_envelope(*args, **kwargs):
    return {}

def _merge_runtime_namespace_envelope(*args, **kwargs):
    return {}

async def run_runtime_turn(*args, **kwargs):
    return {}
"""
    (root / "scripts" / "nmbot_runtime_adapter.py").write_text(adapter, encoding="utf-8")
    (root / "scripts" / "bluesminds_v0_answer_writer.py").unlink()

    artifact = rel.build(release_id="REL-gateway-only", out_dir=tmp_path / "gateway-only-bundle", root=root)
    manifest = rel.load_manifest(artifact.manifest)
    paths = {item["path"] for item in manifest["files"]}

    assert "scripts/gateway_v0_answer_writer.py" in paths
    assert "scripts/bluesminds_v0_answer_writer.py" not in paths
    assert rel.local_preflight(archive=artifact.archive, manifest_path=artifact.manifest).startswith("preflight=ok")


def test_missing_optional_v0_writer_imported_by_adapter_cannot_pass_preflight(tmp_path: Path) -> None:
    root = tmp_path / "missing-gateway-source"
    _copy_contract_tree(root)
    adapter = """
from scripts import gateway_v0_answer_writer

def _canonical_v0_envelope(*args, **kwargs):
    return {}

def _canonical_v1_envelope(*args, **kwargs):
    return {}

def _merge_runtime_namespace_envelope(*args, **kwargs):
    return {}

async def run_runtime_turn(*args, **kwargs):
    return {}
"""
    (root / "scripts" / "nmbot_runtime_adapter.py").write_text(adapter, encoding="utf-8")
    (root / "scripts" / "gateway_v0_answer_writer.py").unlink()

    artifact = rel.build(release_id="REL-missing-gateway", out_dir=tmp_path / "missing-gateway-bundle", root=root)
    paths = {item["path"] for item in rel.load_manifest(artifact.manifest)["files"]}
    assert "scripts/gateway_v0_answer_writer.py" not in paths

    try:
        rel.local_preflight(archive=artifact.archive, manifest_path=artifact.manifest)
    except rel.ReleaseError as exc:
        assert "gateway_v0_answer_writer" in str(exc) or "ImportError" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing imported optional writer dependency must fail import smoke")


def test_local_preflight_rejects_malformed_but_self_consistent_identity(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-identity-bad", out_dir=tmp_path / "src")
    manifest = rel.load_manifest(artifact.manifest)
    identity = {
        "schema": "nmbot.release_identity.v1",
        "release_id": manifest["release_id"],
        "generated_at": "bad generated at with spaces",
        "tracked_files": [item for item in manifest["files"] if item["path"] != rel.IDENTITY_IN_RELEASE],
    }
    archive, manifest_path = _rewrite_identity_artifact(artifact, tmp_path, identity)

    try:
        rel.local_preflight(archive=archive, manifest_path=manifest_path)
    except rel.ReleaseError as exc:
        assert "generated_at" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("self-consistent malformed identity must fail")


def test_tar_path_traversal_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("../evil.py")
        payload = b"print('bad')\n"
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    try:
        rel.safe_extract(archive, tmp_path / "out")
    except rel.ReleaseError as exc:
        assert "unsafe relative path" in str(exc) or "traversal" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("path traversal must fail")


def test_deploy_requires_confirm_and_matching_release_id(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-deploy-test", out_dir=tmp_path)
    fake = FakeRemote()
    try:
        rel.deploy(release_id="REL-deploy-test", archive=artifact.archive, manifest_path=artifact.manifest, confirm=False, remote=fake)
    except rel.ReleaseError as exc:
        assert "requires --confirm" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("deploy without confirm must fail")
    assert fake.commands == []

    try:
        rel.deploy(release_id="REL-wrong", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=fake)
    except rel.ReleaseError as exc:
        assert "does not match manifest" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("wrong release id must fail")
    assert fake.commands == []


def test_deploy_command_order_switch_after_preflight_and_never_restarts_bridge(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-order-test", out_dir=tmp_path)
    fake = FakeRemote()
    out = rel.deploy(release_id="REL-order-test", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=fake, remote_root="/remote")
    joined = "\n".join(fake.commands)

    preflight_index = next(i for i, c in enumerate(fake.commands) if "py_compile.compile" in c and "PYTHONDONTWRITEBYTECODE=1" in c)
    stop_index = next(i for i, c in enumerate(fake.commands) if c == "systemctl --user stop novostroy-bot-api.service")
    inactive_index = next(i for i, c in enumerate(fake.commands) if "is-active" in c)
    switch_index = next(i for i, c in enumerate(fake.commands) if "mv -Tf" in c and "/current" in c)
    identity_index = next(i for i, c in enumerate(fake.commands) if "data/nmbot_release_identity.json.REL-order-test.tmp" in c)
    start_index = next(i for i, c in enumerate(fake.commands) if c == "systemctl --user start novostroy-bot-api.service")
    health_index = next(i for i, c in enumerate(fake.commands) if "nmbot_release_identity.py" in c and "urllib.request" in c)

    assert out.startswith("deploy=ok")
    assert fake.uploads and len(fake.uploads) == 2
    assert fake.commands[0].startswith("systemctl --user show novostroy-bot-api.service")
    assert "--property=" not in fake.commands[0]
    assert preflight_index < stop_index < inactive_index < switch_index < identity_index < start_index < health_index
    assert "http://127.0.0.1:8088/health" in fake.commands[health_index]
    assert "8188" not in fake.commands[health_index]
    assert "systemctl --user restart novostroy-bot-n8n-bridge.service" not in joined
    assert "systemctl --user stop novostroy-bot-n8n-bridge.service" not in joined
    assert "systemctl --user start novostroy-bot-n8n-bridge.service" not in joined
    assert "ln -sfn /remote/.env.client-production" not in joined


def test_rollback_on_restart_or_health_failure_after_switch(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-rollback-test", out_dir=tmp_path)
    fake = FakeRemote(fail="urllib.request")
    try:
        rel.deploy(release_id="REL-rollback-test", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=fake, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "boom" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("health failure must fail")
    joined = "\n".join(fake.commands)
    assert "ln -sfn releases/old /remote/.current.rollback.tmp" in joined
    assert "mv -Tf /remote/.current.rollback.tmp /remote/current" in joined
    assert joined.count("systemctl --user stop novostroy-bot-api.service") == 2
    assert joined.count("systemctl --user start novostroy-bot-api.service") == 2
    assert "systemctl --user restart novostroy-bot-n8n-bridge.service" not in joined


def test_plan_states_migration_not_done_and_current_not_assumed_symlinked() -> None:
    text = rel.render_plan(release_id="REL-plan-test", remote_root="/remote")
    assert "production_migrated=required_not_assumed" in text
    assert "migration_prerequisite=live systemd unit must already be migrated before deploy" in text
    assert "migration_required_working_directory=/remote/current" in text
    assert "migration_required_execstart_contains=/remote/current/scripts/nmbot_api_server.py" in text
    assert "health=http://127.0.0.1:8088/health" in text
    assert "8188" not in text
    assert "current symlink" in text
    assert "forbidden_restart=novostroy-bot-n8n-bridge.service" in text
    assert ".env.client-production" not in text


def test_deploy_refuses_nonmigrated_unit_before_any_remote_write_or_upload(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-migration-guard", out_dir=tmp_path)
    fake = FakeRemote(migrated=False, remote_root="/remote")

    try:
        rel.deploy(release_id="REL-migration-guard", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=fake, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "remote unit is not migrated" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("nonmigrated unit must fail before remote writes")

    assert fake.uploads == []
    assert len(fake.commands) == 1
    assert fake.commands[0].startswith("systemctl --user show novostroy-bot-api.service")
    assert not any("mkdir" in command or "ln -sfn" in command or "mv -Tf" in command or "rm -rf" in command for command in fake.commands)


def test_systemd_exact_contract_rejects_extra_envfiles_execpre_and_exec_args(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-systemd-exact", out_dir=tmp_path)

    cases = [
        (FakeRemote(env_files="/remote/current/.env /remote/current/.env.client-production"), "EnvironmentFiles"),
        (FakeRemote(exec_start_pre="/bin/true"), "ExecStartPre"),
        (FakeRemote(exec_extra_args=" --host 0.0.0.0"), "ExecStart"),
    ]
    for fake, expected in cases:
        try:
            rel.deploy(release_id="REL-systemd-exact", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=fake, remote_root="/remote")
        except rel.ReleaseError as exc:
            assert expected in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"bad systemd contract must fail: {expected}")
        assert fake.uploads == []
        assert not any(command.startswith("mkdir") for command in fake.commands)

    external_env = FakeRemote(env_files="/remote/.env")
    rel.deploy(release_id="REL-systemd-exact", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=external_env, remote_root="/remote")
    assert external_env.uploads


def test_systemd_execstart_path_must_match_argv_interpreter() -> None:
    valid = "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 /remote/current/scripts/nmbot_api_server.py ; }"
    invalid = "{ path=/bin/false ; argv[]=/usr/bin/python3 /remote/current/scripts/nmbot_api_server.py ; }"

    assert rel._extract_systemd_path(valid) == "/usr/bin/python3"
    try:
        rel._extract_systemd_path(invalid)
        executable = rel._extract_systemd_path(invalid)
        argv = rel._extract_systemd_argv(invalid)
        if executable not in rel.APPROVED_EXECSTART_INTERPRETERS or argv[0] != executable:
            raise rel.ReleaseError("remote unit ExecStart must be approved python interpreter plus API server only")
    except rel.ReleaseError:
        pass
    else:  # pragma: no cover
        raise AssertionError("mismatched systemd path/argv must fail")


def test_remote_preflight_has_import_smoke_without_tautological_archive_sha() -> None:
    command = rel._remote_preflight_command("/remote/releases/REL")

    assert "py_compile.compile" in command
    assert "PYTHONDONTWRITEBYTECODE=1" in command
    assert "TemporaryDirectory" in command
    assert "nmbot_v1" in command
    assert "compile_files=sorted(set(compile_files)|set(v1_py_files()))" in command
    assert "release file set/hash changed during preflight" in command
    assert "PYTHONPATH=/remote/releases/REL:/remote/releases/REL/scripts" in command
    assert "archive_sha" not in command


def test_remote_guard_requires_canonical_api_env_and_bridge_cannot_override() -> None:
    manifest = {"config_schema_requirements": rel.CONFIG_REQUIREMENTS}
    command = rel._remote_guard_command("/remote", manifest)

    assert "canonical API env mismatch" in command
    assert "bridge env must not define API-owned required fields" in command
    assert "empty env values" in command
    assert "mode env mismatch" in command
    assert "NMBOT_API_HOST" in command and "127.0.0.1" in command
    assert "NMBOT_API_PORT" in command and "8088" in command
    assert "can never satisfy a missing API requirement" in command
    assert "client_production" in command


def test_remote_existence_probes_use_lexists_for_broken_symlink_immutability() -> None:
    manifest = {
        "archive_name": "nmbot-REL-lexists.tar.gz",
        "archive_sha256": "0" * 64,
        "release_id": "REL-lexists",
        "files": [
            {"path": "scripts/nmbot_api_server.py", "sha256": "1" * 64},
            {"path": rel.IDENTITY_IN_RELEASE, "sha256": "2" * 64},
        ],
    }
    previous = rel._previous_state_probe_command("/remote", "REL-lexists")
    extract = rel._remote_extract_command("/remote/stage", "/remote/releases/REL-lexists", manifest)

    assert "os.path.lexists(target)" in previous
    assert "os.path.lexists(dest)" in extract
    assert "os.path.lexists(tmp)" in extract
    assert "release identity tracked_files mismatch" in extract


def test_generated_previous_state_probe_is_valid_python() -> None:
    command = rel._previous_state_probe_command("/remote", "REL-probe-syntax")
    marker = "python3 -c "
    assert command.startswith(marker)
    words = __import__("shlex").split(command)
    code = words[2]
    compile(code, "<previous-state-probe>", "exec")


def _run_generated(command: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, shell=True, cwd=cwd, text=True, capture_output=True, check=False)


def _write_canonical_env(root: Path, *, overrides: dict[str, str] | None = None, bridge: dict[str, str] | None = None, omit_bridge: bool = False) -> None:
    values = {
        "JIVO_PROVIDER_ID": "provider",
        "JIVO_PROVIDER_TOKEN": "jivo-token",
        "NMBOT_API_TOKEN": "api-token",
        "NMBOT_CONTOUR_PROFILE": "api_production",
        "NMBOT_API_HOST": "127.0.0.1",
        "NMBOT_API_PORT": "8088",
        "NMBOT_API_STATE_FILE": str(root / "data" / "nmbot_api_state.json"),
        "NMBOT_CALLBACK_OUTBOX_DIR": str(root / "data" / "private" / "callback-outbox"),
        "NMBOT_RELEASE_IDENTITY_FILE": str(root / "data" / "nmbot_release_identity.json"),
        "NMBOT_RUNTIME_VERSION_FILE": str(root / "data" / "nmbot_runtime_version.json"),
        "NMBOT_V2_MANAGER_REWRITER_MODE": "off",
        "NMBOT_V3_MANAGER_REWRITER_MODE": "publish",
    }
    values.update(overrides or {})
    (root / "data" / "private" / "callback-outbox").mkdir(parents=True, exist_ok=True)
    (root / "data" / "nmbot_release_identity.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "data" / "nmbot_release_identity.json").write_text('{"release_id":"test"}\n', encoding="utf-8")
    (root / ".env").write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    bridge_path = root / ".env.client-production"
    if omit_bridge:
        if bridge_path.exists() or bridge_path.is_symlink():
            bridge_path.unlink()
    else:
        bridge_path.write_text("".join(f"{k}={v}\n" for k, v in (bridge or {"BRIDGE_ONLY": "1"}).items()), encoding="utf-8")


def test_generated_remote_guard_executes_env_ownership_nonempty_and_modes(tmp_path: Path) -> None:
    root = tmp_path / "remote"
    for name in ("data", "logs", "backups"):
        (root / name).mkdir(parents=True)
    (root / "current").mkdir()
    manifest = {"config_schema_requirements": rel.CONFIG_REQUIREMENTS}

    _write_canonical_env(root)
    ok = _run_generated(rel._remote_guard_command(str(root), manifest))
    assert ok.returncode == 0, ok.stderr + ok.stdout
    assert json.loads(ok.stdout.strip().splitlines()[-1]) == {"ok": True}

    _write_canonical_env(root, omit_bridge=True)
    absent_bridge = _run_generated(rel._remote_guard_command(str(root), manifest))
    assert absent_bridge.returncode == 0, absent_bridge.stderr + absent_bridge.stdout

    cases = [
        ({"NMBOT_API_TOKEN": ""}, None, "empty env values"),
        ({}, {"NMBOT_API_TOKEN": "bridge-token"}, "bridge env must not define API-owned required fields"),
        ({"NMBOT_V2_MANAGER_REWRITER_MODE": "publish"}, None, "mode env mismatch"),
        ({"NMBOT_V3_MANAGER_REWRITER_MODE": "off"}, None, "mode env mismatch"),
        ({"NMBOT_CONTOUR_PROFILE": "client_production"}, None, "contour profile"),
    ]
    for overrides, bridge, expected in cases:
        _write_canonical_env(root, overrides=overrides, bridge=bridge)
        proc = _run_generated(rel._remote_guard_command(str(root), manifest))
        assert proc.returncode != 0
        assert expected in proc.stdout

    # Bridge/client env cannot satisfy a missing canonical API-owned field.
    _write_canonical_env(root, bridge={"NMBOT_API_TOKEN": "bridge-token"})
    lines = [line for line in (root / ".env").read_text(encoding="utf-8").splitlines() if not line.startswith("NMBOT_API_TOKEN=")]
    (root / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    proc = _run_generated(rel._remote_guard_command(str(root), manifest))
    assert proc.returncode != 0
    assert "missing env names" in proc.stdout

    _write_canonical_env(root, omit_bridge=True)
    (root / ".env.client-production").symlink_to(root / ".env")
    proc = _run_generated(rel._remote_guard_command(str(root), manifest))
    assert proc.returncode != 0
    assert "optional bridge env" in proc.stdout
    (root / ".env.client-production").unlink()


def test_generated_remote_guard_identity_path_requires_lexical_external_data_path_with_current_data_symlink(tmp_path: Path) -> None:
    root = tmp_path / "remote"
    release = root / "releases" / "REL"
    for name in ("data", "logs", "backups"):
        (root / name).mkdir(parents=True)
    release.mkdir(parents=True)
    (release / "data").symlink_to(root / "data")
    (root / "current").symlink_to(release)
    manifest = {"config_schema_requirements": rel.CONFIG_REQUIREMENTS}

    _write_canonical_env(root, omit_bridge=True)
    ok = _run_generated(rel._remote_guard_command(str(root), manifest))
    assert ok.returncode == 0, ok.stderr + ok.stdout
    assert json.loads(ok.stdout.strip().splitlines()[-1]) == {"ok": True}

    identity = root / "data" / "nmbot_release_identity.json"
    for kind in ("missing", "directory", "symlink"):
        if identity.exists() or identity.is_symlink():
            identity.unlink() if identity.is_file() or identity.is_symlink() else __import__("shutil").rmtree(identity)
        if kind == "directory":
            identity.mkdir()
        elif kind == "symlink":
            identity.symlink_to(tmp_path / "outside-release-identity.json")
        proc = _run_generated(rel._remote_guard_command(str(root), manifest))
        assert proc.returncode != 0, kind
        assert "release identity file missing or invalid" in proc.stdout
    if identity.exists() or identity.is_symlink():
        identity.unlink() if identity.is_file() or identity.is_symlink() else __import__("shutil").rmtree(identity)
    identity.write_text('{"release_id":"test"}\n', encoding="utf-8")

    bad_identity_values = [
        "data/nmbot_release_identity.json",
        str(root / "current" / "data" / "nmbot_release_identity.json"),
        str(release / "data" / "nmbot_release_identity.json"),
    ]
    for value in bad_identity_values:
        _write_canonical_env(root, overrides={"NMBOT_RELEASE_IDENTITY_FILE": value}, omit_bridge=True)
        proc = _run_generated(rel._remote_guard_command(str(root), manifest))
        assert proc.returncode != 0, value
        assert "env path must use fixed external data path: NMBOT_RELEASE_IDENTITY_FILE" in proc.stdout

    _write_canonical_env(
        root,
        overrides={"NMBOT_CALLBACK_OUTBOX_DIR": str(root / "current" / "data" / "private" / "callback-outbox")},
        omit_bridge=True,
    )
    proc = _run_generated(rel._remote_guard_command(str(root), manifest))
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_generated_bootstrap_guard_accepts_absent_optional_bridge_env_and_rejects_present_api_owned_field(tmp_path: Path) -> None:
    root = tmp_path / "bootstrap-remote"
    for name in ("data", "logs", "backups"):
        (root / name).mkdir(parents=True)
    manifest = {"config_schema_requirements": rel.CONFIG_REQUIREMENTS}

    _write_canonical_env(root, omit_bridge=True)
    # First-migration exception: these keys may be absent before bootstrap adds them.
    optional = {"NMBOT_CONTOUR_PROFILE", "NMBOT_RELEASE_IDENTITY_FILE", "NMBOT_RUNTIME_VERSION_FILE"}
    lines = [line for line in (root / ".env").read_text(encoding="utf-8").splitlines() if line.split("=", 1)[0] not in optional]
    (root / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok = _run_generated(rel._bootstrap_guard_command(str(root), manifest))
    assert ok.returncode == 0, ok.stderr + ok.stdout

    _write_canonical_env(root, omit_bridge=True)
    (root / ".env.client-production").write_text("NMBOT_API_TOKEN=bridge-token\n", encoding="utf-8")
    proc = _run_generated(rel._bootstrap_guard_command(str(root), manifest))
    assert proc.returncode != 0
    assert "bridge env must not define API-owned required fields" in proc.stdout

    (root / ".env.client-production").unlink()
    (root / ".env.client-production").symlink_to(root / ".env")
    proc = _run_generated(rel._bootstrap_guard_command(str(root), manifest))
    assert proc.returncode != 0
    assert "optional bridge env" in proc.stdout
    (root / ".env.client-production").unlink()


def test_generated_guards_require_existing_canonical_callback_outbox_directory(tmp_path: Path) -> None:
    for guard_name, guard_factory in (
        ("ordinary", rel._remote_guard_command),
        ("bootstrap", rel._bootstrap_guard_command),
    ):
        root = tmp_path / f"callback-{guard_name}"
        for name in ("data", "logs", "backups"):
            (root / name).mkdir(parents=True)
        if guard_name == "ordinary":
            (root / "current").mkdir()
        manifest = {"config_schema_requirements": rel.CONFIG_REQUIREMENTS}

        _write_canonical_env(root, omit_bridge=True)
        if guard_name == "bootstrap":
            optional = {"NMBOT_CONTOUR_PROFILE", "NMBOT_RELEASE_IDENTITY_FILE", "NMBOT_RUNTIME_VERSION_FILE"}
            lines = [line for line in (root / ".env").read_text(encoding="utf-8").splitlines() if line.split("=", 1)[0] not in optional]
            (root / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok = _run_generated(guard_factory(str(root), manifest))
        assert ok.returncode == 0, ok.stderr + ok.stdout

        queue = root / "data" / "private" / "callback-outbox"
        for kind in ("missing", "file", "symlink"):
            if queue.exists() or queue.is_symlink():
                queue.unlink() if queue.is_file() or queue.is_symlink() else __import__("shutil").rmtree(queue)
            if kind == "file":
                queue.parent.mkdir(parents=True, exist_ok=True)
                queue.write_text("not a dir\n", encoding="utf-8")
            elif kind == "symlink":
                target = root / "data" / "private" / f"target-{guard_name}"
                target.mkdir(parents=True, exist_ok=True)
                queue.symlink_to(target)
            proc = _run_generated(guard_factory(str(root), manifest))
            assert proc.returncode != 0, kind
            assert "callback outbox directory missing or invalid" in proc.stdout

        if queue.exists() or queue.is_symlink():
            queue.unlink() if queue.is_file() or queue.is_symlink() else __import__("shutil").rmtree(queue)


def test_bootstrap_callback_outbox_guard_failure_refuses_before_lock_backup_upload(tmp_path: Path) -> None:
    artifact = rel.build(release_id="baseline-callback-guard", out_dir=tmp_path)
    manifest = _with_valid_source_provenance(artifact, tmp_path)
    fake = FakeRemote(remote_root=rel.DEFAULT_REMOTE_ROOT, fail="callback outbox directory missing or invalid")

    try:
        rel.bootstrap_apply(release_id="baseline-callback-guard", archive=artifact.archive, manifest_path=manifest, confirm=True, remote=fake, source_snapshot_manifest_sha256="a" * 64)
    except rel.ReleaseError as exc:
        assert "boom" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("callback outbox guard failure must fail before writes")

    joined = "\n".join(fake.commands)
    assert fake.uploads == []
    assert "callback outbox directory missing or invalid" in joined
    assert "mkdir /home/neiro/novostroy-bot/.release_lock" not in joined
    assert "nmbot.bootstrap_backup.v1" not in joined
    assert "systemctl --user stop novostroy-bot-api.service" not in joined


def test_generated_remote_preflight_executes_without_pyc_mutation(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-remote-preflight", out_dir=tmp_path / "bundle")
    release_dir = tmp_path / "release"
    rel.safe_extract(artifact.archive, release_dir)

    before = {p.relative_to(release_dir).as_posix() for p in release_dir.rglob("*") if p.is_file()}
    proc = _run_generated(rel._remote_preflight_command(str(release_dir)))
    after = {p.relative_to(release_dir).as_posix() for p in release_dir.rglob("*") if p.is_file()}

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert json.loads(proc.stdout.strip().splitlines()[-1])["ok"] is True
    assert before == after
    assert not list(release_dir.rglob("__pycache__"))
    assert not list(release_dir.rglob("*.pyc"))


def test_generated_previous_state_probe_executes_and_rejects_bad_generated_at(tmp_path: Path) -> None:
    root = tmp_path / "remote"
    prev = root / "releases" / "old"
    (prev / "scripts").mkdir(parents=True)
    tracked = prev / "scripts" / "app.py"
    tracked.write_text("print('ok')\n", encoding="utf-8")
    for name in rel.CONFIG_REQUIREMENTS["external_runtime_paths"]:
        target = root / name
        if "." in Path(name).name:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x=1\n", encoding="utf-8")
        else:
            target.mkdir(parents=True, exist_ok=True)
        (prev / name).symlink_to(target)
    identity_path = prev / rel.IDENTITY_IN_RELEASE
    identity_path.parent.mkdir(parents=True)
    identity = {
        "schema": "nmbot.release_identity.v1",
        "release_id": "old",
        "generated_at": "2026-07-24T00:00:00Z",
        "tracked_files": [{"path": "scripts/app.py", "sha256": rel._sha256_file(tracked)}],
    }
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    (root / "current").symlink_to(prev)
    pycache = prev / "scripts" / "__pycache__"
    pycache.mkdir()
    (pycache / "app.cpython-312.pyc").write_bytes(b"runtime generated cache")

    ok = _run_generated(rel._previous_state_probe_command(str(root), "REL-new"))
    assert ok.returncode == 0, ok.stderr + ok.stdout
    assert json.loads(ok.stdout.strip().splitlines()[-1])["previous_id"] == "old"

    extra = prev / "scripts" / "extra.py"
    extra.write_text("print('extra')\n", encoding="utf-8")
    bad_extra = _run_generated(rel._previous_state_probe_command(str(root), "REL-new"))
    assert bad_extra.returncode != 0
    assert "previous release tracked file set mismatch" in bad_extra.stdout
    extra.unlink()

    identity["generated_at"] = "bad generated at with spaces"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    bad = _run_generated(rel._previous_state_probe_command(str(root), "REL-new"))
    assert bad.returncode != 0
    assert "generated_at" in bad.stdout


class _HealthHandler(BaseHTTPRequestHandler):
    payload = {"ok": True, "jivo_token_configured": True, "api_token_configured": True}

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps(type(self).payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


def test_generated_health_command_executes_and_requires_config_flags(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "remote"
    script = root / "current" / "scripts" / "nmbot_release_identity.py"
    script.parent.mkdir(parents=True)
    script.write_text("import os\nprint('REL-health')\n", encoding="utf-8")
    identity = root / rel.IDENTITY_EXTERNAL
    identity.parent.mkdir(parents=True)
    identity.write_text("{}\n", encoding="utf-8")
    httpd = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(rel, "API_HEALTH_URL", f"http://127.0.0.1:{httpd.server_port}/health")
    try:
        _HealthHandler.payload = {"ok": True, "jivo_token_configured": True, "api_token_configured": True}
        ok = _run_generated(rel._health_and_identity_command(str(root), "REL-health"))
        assert ok.returncode == 0, ok.stderr + ok.stdout
        assert json.loads(ok.stdout.strip().splitlines()[-1]) == {"ok": True, "release_id": "REL-health"}

        for payload in (
            {"ok": False, "jivo_token_configured": True, "api_token_configured": True},
            {"ok": True, "jivo_token_configured": False, "api_token_configured": True},
            {"ok": True, "jivo_token_configured": True, "api_token_configured": False},
        ):
            _HealthHandler.payload = payload
            bad = _run_generated(rel._health_and_identity_command(str(root), "REL-health"))
            assert bad.returncode != 0
            assert "health config proof failed" in bad.stdout
    finally:
        httpd.shutdown()
        thread.join(timeout=2)


def test_migration_plan_is_local_only_and_mentions_required_current_path() -> None:
    text = rel.render_migration_plan(remote_root="/remote")

    assert "scope=local-only" in text
    assert "required_working_directory=/remote/current" in text
    assert "required_execstart_contains=/remote/current/scripts/nmbot_api_server.py" in text
    assert "required_health_after_owner_migration=http://127.0.0.1:8088/health" in text
    assert "8188" not in text


def test_manifest_rejects_unknown_keys_and_malicious_import_module(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-schema-guard", out_dir=tmp_path)
    manifest = rel.load_manifest(artifact.manifest)
    manifest["extra"] = "nope"
    path = tmp_path / "extra.manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    try:
        rel.load_manifest(path)
    except rel.ReleaseError as exc:
        assert "keys mismatch" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown manifest key must fail")

    manifest.pop("extra")
    manifest["import_modules"] = ["scripts.attacker_controlled"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    try:
        rel.load_manifest(path)
    except rel.ReleaseError as exc:
        assert "import_modules must exactly match" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("malicious import module must fail")

    manifest["import_modules"] = list(rel.IMPORT_MODULES)
    manifest["config_schema_requirements"] = []
    path.write_text(json.dumps(manifest), encoding="utf-8")
    try:
        rel.load_manifest(path)
    except rel.ReleaseError as exc:
        assert "wrong type" in str(exc) or "config requirements" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("empty config requirements bypass must fail")


def test_manifest_and_build_selection_reject_attacker_script(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "attacker_controlled.py").write_text("print('owned')\n", encoding="utf-8")
    assert rel.iter_snapshot_files(root) == []

    artifact = rel.build(release_id="REL-attacker-script", out_dir=tmp_path / "bundle")
    manifest = rel.load_manifest(artifact.manifest)
    manifest["files"].append({"path": "scripts/attacker_controlled.py", "sha256": "0" * 64})
    path = tmp_path / "attacker.manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    try:
        rel.load_manifest(path)
    except rel.ReleaseError as exc:
        assert "excluded file in manifest" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("attacker-controlled script must fail manifest validation")


def test_dialogue_exporter_files_are_exact_opt_in_allowlist(tmp_path: Path) -> None:
    default_artifact = rel.build(release_id="REL-default-no-exporter", out_dir=tmp_path / "default")
    default_paths = {item["path"] for item in rel.load_manifest(default_artifact.manifest)["files"]}
    assert default_paths.isdisjoint(rel.NMBOT_DIALOGUE_EXPORTER_FILES)

    artifact = rel.build(release_id="REL-dialogue-exporter", out_dir=tmp_path / "exporter", include_dialogue_exporter=True)
    manifest = rel.load_manifest(artifact.manifest)
    paths = {item["path"] for item in manifest["files"]}
    assert rel.NMBOT_DIALOGUE_EXPORTER_FILES.issubset(paths)
    assert rel._manifest_has_dialogue_exporter(manifest) is True
    assert rel.local_preflight(archive=artifact.archive, manifest_path=artifact.manifest).startswith("preflight=ok")

    partial = dict(manifest)
    partial["files"] = [item for item in manifest["files"] if item["path"] != rel.NMBOT_DIALOGUE_EXPORTER_TIMER_TEMPLATE]
    partial_path = tmp_path / "partial.manifest.json"
    partial_path.write_text(json.dumps(partial), encoding="utf-8")
    with pytest.raises(rel.ReleaseError, match="exact allowlisted"):
        rel.load_manifest(partial_path)

    malicious = dict(default_artifact.manifest_data)
    malicious["files"] = [
        *malicious["files"],
        {"path": "deploy/systemd/evil.service", "sha256": "0" * 64},
    ]
    malicious_path = tmp_path / "malicious.manifest.json"
    malicious_path.write_text(json.dumps(malicious, sort_keys=True), encoding="utf-8")
    with pytest.raises(rel.ReleaseError, match="excluded file"):
        rel.load_manifest(malicious_path)


def test_dialogue_exporter_deploy_installs_fixed_paths_and_rolls_back_on_failure(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-dialogue-deploy", out_dir=tmp_path / "bundle", include_dialogue_exporter=True)
    fake = FakeRemote(remote_root=rel.DEFAULT_REMOTE_ROOT)

    rel.deploy(release_id="REL-dialogue-deploy", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=fake, remote_root=rel.DEFAULT_REMOTE_ROOT)

    joined = "\n".join(fake.commands)
    assert rel.NMBOT_DIALOGUE_EXPORTER_REMOTE_SCRIPT in joined
    assert rel.NMBOT_DIALOGUE_EXPORTER_REMOTE_SERVICE in joined
    assert rel.NMBOT_DIALOGUE_EXPORTER_REMOTE_TIMER in joined
    assert "enable" in joined and "--now" in joined and rel.NMBOT_DIALOGUE_EXPORTER_TIMER_UNIT in joined
    backup_index = next(i for i, command in enumerate(fake.commands) if "nmbot.dialogue_exporter_backup.v1" in command)
    switch_index = next(i for i, command in enumerate(fake.commands) if ".current.REL-dialogue-deploy.tmp" in command)
    install_index = next(i for i, command in enumerate(fake.commands) if "dialogue exporter source hash mismatch" in command)
    assert backup_index < switch_index < install_index

    failing = FakeRemote(remote_root=rel.DEFAULT_REMOTE_ROOT, fail='"--now"')
    with pytest.raises(rel.ReleaseError):
        rel.deploy(release_id="REL-dialogue-deploy", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=failing, remote_root=rel.DEFAULT_REMOTE_ROOT)
    failed_joined = "\n".join(failing.commands)
    assert "dialogue exporter rollback metadata" in failed_joined
    assert "ln -sfn releases/old" in failed_joined


def test_snapshot_policy_allows_explicit_release_identity_script_only(tmp_path: Path, monkeypatch) -> None:
    remote_root = tmp_path / "synthetic-remote"
    _copy_contract_tree(remote_root)
    identity = remote_root / "scripts" / "nmbot_release_identity.py"
    identity.write_text("def ok():\n    return True\n", encoding="utf-8")
    (remote_root / "scripts" / "nmbot_release_candidate.py").write_text("print('no')\n", encoding="utf-8")
    (remote_root / "scripts" / "deploy_candidate.py").write_text("print('no')\n", encoding="utf-8")
    monkeypatch.setattr(rel, "DEFAULT_REMOTE_ROOT", str(remote_root))

    assert rel._is_allowed_runtime_file("scripts/nmbot_release_identity.py") is True
    assert rel._is_allowed_runtime_file("scripts/nmbot_release_candidate.py") is False
    assert rel._is_allowed_runtime_file("scripts/deploy_candidate.py") is False

    proc = subprocess.run(rel._snapshot_vps_source_command(remote_root=str(remote_root)), shell=True, capture_output=True, check=False)

    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    manifest = rel._extract_snapshot_tar(proc.stdout, tmp_path / "snapshot")
    paths = {item["path"] for item in manifest["files"]}
    assert "scripts/nmbot_release_identity.py" in paths
    assert "scripts/nmbot_release_candidate.py" not in paths
    assert "scripts/deploy_candidate.py" not in paths


def test_snapshot_rejects_allowed_secret_like_file_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "prompts").mkdir(parents=True)
    (root / "prompts" / "api_key.txt").write_text("not-a-real-secret", encoding="utf-8")
    try:
        rel.iter_snapshot_files(root)
    except rel.ReleaseError as exc:
        assert "secret-like filename" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("secret-like allowed file must fail")

    (root / "prompts" / "api_key.txt").unlink()
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "link.py").symlink_to("/tmp/target.py")
    try:
        rel.iter_snapshot_files(root)
    except rel.ReleaseError as exc:
        assert "non-regular file" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("symlink must fail")


def test_existing_release_and_no_previous_refuse_before_upload(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-existing", out_dir=tmp_path)
    existing = FakeRemote(release_exists=True)
    try:
        rel.deploy(release_id="REL-existing", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=existing, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "already exists" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("existing release id must fail")
    assert existing.uploads == []
    assert "mkdir /remote/.release_lock" in existing.commands
    assert not any(command.startswith("mkdir -p") for command in existing.commands)

    no_previous = FakeRemote(previous="")
    try:
        rel.deploy(release_id="REL-existing", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=no_previous, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "current is not a release symlink" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing previous current must fail")
    assert no_previous.uploads == []


def test_systemd_pythonpath_and_external_guard_fail_before_writes(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-guard", out_dir=tmp_path)
    bad_unit = FakeRemote(pythonpath=True)
    try:
        rel.deploy(release_id="REL-guard", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=bad_unit, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "PYTHONPATH" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("PYTHONPATH unit skew must fail")
    assert bad_unit.uploads == []

    bad_execstart = FakeRemote(execstart_pythonpath=True)
    try:
        rel.deploy(release_id="REL-guard", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=bad_execstart, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "PYTHONPATH" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("PYTHONPATH in ExecStart must fail")
    assert bad_execstart.uploads == []

    bad_external = FakeRemote(fail="missing env names")
    try:
        rel.deploy(release_id="REL-guard", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=bad_external, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "boom" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("external guard failure must fail before upload")
    assert bad_external.uploads == []
    assert not any(command.startswith("mkdir") for command in bad_external.commands)

    bad_identity_path = FakeRemote(fail="env path outside fixed external data path: ")
    try:
        rel.deploy(release_id="REL-guard", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=bad_identity_path, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "boom" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("wrong identity/config path must fail before upload")
    assert bad_identity_path.uploads == []

    for placement in ("ExecStart", "Environment", "EnvironmentFiles", "ExecStartPre"):
        bad_identity_override = FakeRemote(systemd_identity_override=placement)
        try:
            rel.deploy(release_id="REL-guard", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=bad_identity_override, remote_root="/remote")
        except rel.ReleaseError as exc:
            expected = "ExecStartPre" if placement == "ExecStartPre" else "NMBOT_RELEASE_IDENTITY_FILE"
            assert expected in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"identity override in {placement} must fail before upload")
        assert bad_identity_override.uploads == []

    bad_state_override = FakeRemote(systemd_state_override="Environment")
    try:
        rel.deploy(release_id="REL-guard", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=bad_state_override, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "NMBOT_API_STATE_FILE" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("state path override must fail before upload")
    assert bad_state_override.uploads == []


def test_identity_publish_safe_extract_and_rollback_failure_preserves_deploy_error(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-identity", out_dir=tmp_path)
    fake = FakeRemote()
    rel.deploy(release_id="REL-identity", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=fake, remote_root="/remote")
    joined = "\n".join(fake.commands)
    assert "release_identity/nmbot_release_identity.json" in joined
    assert "data/nmbot_release_identity.json.REL-identity.tmp" in joined
    switch_index = next(i for i, command in enumerate(fake.commands) if ".current.REL-identity.tmp" in command)
    identity_index = next(i for i, command in enumerate(fake.commands) if "data/nmbot_release_identity.json.REL-identity.tmp" in command)
    start_index = next(i for i, command in enumerate(fake.commands) if command == "systemctl --user start novostroy-bot-api.service")
    assert switch_index < identity_index < start_index
    assert "tar -xzf" not in joined
    assert any("unexpected tar member" in command and "tarfile.open" in command for command in fake.commands)
    lock_index = next(i for i, command in enumerate(fake.commands) if command == "mkdir /remote/.release_lock")
    probe_index = next(i for i, command in enumerate(fake.commands) if "previous_id" in command)
    upload_index = next(i for i, command in enumerate(fake.commands) if command.startswith("mkdir -p /remote/.release_staging"))
    cleanup_index = next(i for i, command in enumerate(fake.commands) if command == "rmdir /remote/.release_lock")
    assert lock_index < probe_index < upload_index < cleanup_index

    failing = FakeRemote(fail="urllib.request", rollback_fail=True)
    try:
        rel.deploy(release_id="REL-identity", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=failing, remote_root="/remote")
    except rel.ReleaseError as exc:
        text = str(exc)
        assert "deploy failed" in text and "boom" in text and "rollback failed" in text and "rollback-boom" in text
    else:  # pragma: no cover
        raise AssertionError("rollback failure must preserve deploy error")


def test_rollback_restores_previous_release_identity_before_restart(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-identity-rollback", out_dir=tmp_path)
    fake = FakeRemote(fail="urllib.request")

    try:
        rel.deploy(
            release_id="REL-identity-rollback",
            archive=artifact.archive,
            manifest_path=artifact.manifest,
            confirm=True,
            remote=fake,
            remote_root="/remote",
        )
    except rel.ReleaseError:
        pass
    else:  # pragma: no cover
        raise AssertionError("health failure must trigger rollback")

    rollback_switch = next(i for i, command in enumerate(fake.commands) if ".current.rollback.tmp" in command)
    old_identity = next(i for i, command in enumerate(fake.commands) if "/releases/old/release_identity/nmbot_release_identity.json" in command)
    starts = [i for i, command in enumerate(fake.commands) if command == "systemctl --user start novostroy-bot-api.service"]
    rollback_stop = [i for i, command in enumerate(fake.commands) if command == "systemctl --user stop novostroy-bot-api.service"][-1]
    rollback_verify = [i for i, command in enumerate(fake.commands) if "nmbot_release_identity.py" in command and "urllib.request" in command][-1]
    assert rollback_stop < rollback_switch < old_identity < starts[-1] < rollback_verify


def test_malicious_previous_state_refuses_before_upload(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-prev-guard", out_dir=tmp_path)
    fake = FakeRemote(previous="/evil/current")

    try:
        rel.deploy(release_id="REL-prev-guard", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=fake, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "current symlink target" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("malicious previous symlink must fail")

    assert fake.uploads == []
    assert "mkdir /remote/.release_lock" in fake.commands
    assert not any(command.startswith("mkdir -p") for command in fake.commands)


def test_release_lock_contention_fails_before_upload_and_success_cleans_up(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-lock", out_dir=tmp_path)
    locked = FakeRemote(fail="mkdir /remote/.release_lock")

    try:
        rel.deploy(release_id="REL-lock", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=locked, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "boom" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("lock contention must fail")

    assert locked.uploads == []
    assert "rmdir /remote/.release_lock" not in locked.commands

    ok = FakeRemote()
    rel.deploy(release_id="REL-lock", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=ok, remote_root="/remote")
    assert ok.commands.count("mkdir /remote/.release_lock") == 1
    assert ok.commands[-1] == "rmdir /remote/.release_lock"

    cleanup = FakeRemote(cleanup_fail=True)
    try:
        rel.deploy(release_id="REL-lock", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=cleanup, remote_root="/remote")
    except rel.ReleaseError as exc:
        text = str(exc)
        assert "deploy completed but release lock cleanup failed" in text and "cleanup-boom" in text
    else:  # pragma: no cover
        raise AssertionError("cleanup failure after success must not report deploy=ok")

    failed_and_cleanup = FakeRemote(fail="mkdir -p /remote/.release_staging", cleanup_fail=True)
    try:
        rel.deploy(release_id="REL-lock", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=failed_and_cleanup, remote_root="/remote")
    except rel.ReleaseError as exc:
        text = str(exc)
        assert "boom" in text and "release lock cleanup failed" in text and "cleanup-boom" in text
    else:  # pragma: no cover
        raise AssertionError("cleanup failure after deploy failure must preserve both errors")


def test_ambiguous_switch_failure_triggers_rollback_attempt(tmp_path: Path) -> None:
    artifact = rel.build(release_id="REL-switch-fail", out_dir=tmp_path)
    fake = FakeRemote(fail=".current.REL-switch-fail.tmp")

    try:
        rel.deploy(release_id="REL-switch-fail", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=fake, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "boom" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("ambiguous switch failure must fail")

    joined = "\n".join(fake.commands)
    assert "ln -sfn releases/old /remote/.current.rollback.tmp" in joined
    assert "rmdir /remote/.release_lock" in joined


def test_health_identity_command_requires_expected_release_and_cli_host_port() -> None:
    command = rel._health_and_identity_command("/remote", "REL-health")
    assert "nmbot_release_identity.py" in command
    assert "REL-health" in command
    assert "NMBOT_RELEASE_IDENTITY_FILE" in command
    assert "/remote/data/nmbot_release_identity.json" in command
    assert "/remote/current/data/nmbot_release_identity.json" not in command
    assert "release identity mismatch" in command
    try:
        rel.SshRemote(host="other@193.107.155.236", port="1905")
    except rel.ReleaseError as exc:
        assert "host/port" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("wrong host must fail")
    try:
        rel.SshRemote(host=rel.AUTHORIZED_DEPLOY_HOST, port="22")
    except rel.ReleaseError as exc:
        assert "host/port" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("wrong port must fail")


def test_recon_command_is_readonly_and_returns_names_only() -> None:
    command = rel._readonly_recon_command()
    forbidden = re.compile(r"\b(mkdir|ln|mv|cp|rm|restart|start|stop|enable|disable)\b")
    assert not forbidden.search(command)
    assert "systemctl" in command and "show" in command
    assert "NMBOT_API_TOKEN" in command
    assert "print(json.dumps" in command

    fake = FakeRemote()
    payload = _valid_recon_payload()
    fake.run = lambda command, input_text=None: subprocess.CompletedProcess([], 0, stdout=json.dumps(payload) + "\n", stderr="")  # type: ignore[method-assign]
    assert rel.recon(fake)["env_names"]["NMBOT_API_TOKEN"] == {"present": True, "nonempty": True}


def test_recon_rejects_extra_fields_types_paths_and_sanitizes_failure() -> None:
    fake = FakeRemote()
    for mutate in (
        lambda p: p.update({"prompt_body": "leak"}),
        lambda p: p["env_names"].update({"EXTRA_TOKEN": {"present": True, "nonempty": True}}),
        lambda p: p["env_names"].__setitem__("NMBOT_API_TOKEN", {"present": "yes", "nonempty": True}),
        lambda p: p["paths"].__setitem__("env_file", "/tmp/.env"),
        lambda p: p["current"].__setitem__("target_name", "../../secret"),
        lambda p: p["identity"].__setitem__("release_id", "../../secret"),
        lambda p: p["identity"].__setitem__("release_id", True),
        lambda p: p["health"].__setitem__("api_token_configured", "true"),
    ):
        payload = _valid_recon_payload()
        mutate(payload)
        fake.run = lambda command, input_text=None, payload=payload: subprocess.CompletedProcess([], 0, stdout=json.dumps(payload) + "\n", stderr="")  # type: ignore[method-assign]
        try:
            rel.recon(fake)
        except rel.ReleaseError:
            pass
        else:  # pragma: no cover
            raise AssertionError("malformed recon payload must fail")

    fake.run = lambda command, input_text=None: subprocess.CompletedProcess([], 1, stdout="PROMPT_BODY=stdout-secret\n", stderr="NMBOT_API_TOKEN=stderr-secret\n")  # type: ignore[method-assign]
    try:
        rel.recon(fake)
    except rel.ReleaseError as exc:
        text = str(exc)
        assert "stdout-secret" not in text and "stderr-secret" not in text and "NMBOT_API_TOKEN" not in text
        assert "remote recon failed with exit=1" in text
    else:  # pragma: no cover
        raise AssertionError("failed recon must fail safely")


def test_test_release_recon_requires_identity_release_id_match() -> None:
    payload = _valid_recon_payload()
    payload["current"]["target_name"] = "REL-current"
    rel._assert_test_release_recon(recon_data=payload, release_id="REL-current")

    payload["identity"]["release_id"] = "REL-other"
    with pytest.raises(rel.ReleaseError, match="identity release_id"):
        rel._assert_test_release_recon(recon_data=payload, release_id="REL-current")


def test_capture_command_rejects_non_contract_paths_before_remote_command() -> None:
    paths = rel._contract_capture_paths(ROOT)
    for bad in (paths + [".env"], list(reversed(paths)), paths[:-1], paths + [paths[0]]):
        try:
            rel._capture_baseline_command(bad)
        except rel.ReleaseError as exc:
            assert "capture paths" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("non-exact capture path set must fail before command")


def test_capture_extract_requires_exact_member_set_and_rejects_duplicates_links_and_secrets(tmp_path: Path) -> None:
    expected = rel._contract_capture_paths(ROOT)
    for names in (expected[:-1], expected + ["scripts/extra.py"], expected + [expected[0]]):
        try:
            rel._extract_capture_tar(_tar_bytes(names), tmp_path / "out", expected)
        except rel.ReleaseError as exc:
            assert "member set" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("bad captured member set must fail")

    link_buf = io.BytesIO()
    with tarfile.open(fileobj=link_buf, mode="w") as tf:
        for idx, name in enumerate(expected):
            info = tarfile.TarInfo(name)
            if idx == 0:
                info.type = tarfile.SYMTYPE
                info.linkname = "/tmp/evil"
                info.size = 0
                tf.addfile(info)
            else:
                payload = (ROOT / name).read_bytes()
                info.size = len(payload)
                tf.addfile(info, io.BytesIO(payload))
    try:
        rel._extract_capture_tar(link_buf.getvalue(), tmp_path / "link", expected)
    except rel.ReleaseError as exc:
        assert "unsafe captured member" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("link member must fail")


def test_capture_failure_never_leaks_stdout_token_or_final_files(tmp_path: Path, monkeypatch) -> None:
    out = Path("/tmp/opencode") / "nmbot-test-capture-fail-no-files"
    if out.exists():
        import shutil
        shutil.rmtree(out)
    try:
        rel.capture_baseline(remote=StdoutLeakingBinaryRemote(), out_dir=out, release_id="baseline-leak")
    except rel.ReleaseError as exc:
        text = str(exc)
        assert "stdout-secret" not in text and "stderr-secret" not in text and "API_TOKEN" not in text and "PROMPT_BODY" not in text
    else:  # pragma: no cover
        raise AssertionError("binary failure must fail")

    remote_root = tmp_path / "remote"
    _copy_contract_tree(remote_root)
    monkeypatch.setattr(rel, "local_preflight", lambda **kwargs: (_ for _ in ()).throw(rel.ReleaseError("preflight failed")))
    try:
        rel.capture_baseline(remote=FakeBinaryRemote(remote_root), out_dir=out, release_id="baseline-preflight-fail")
    except rel.ReleaseError as exc:
        assert "preflight failed" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("failed preflight must fail capture")
    assert not os.path.lexists(out / "baseline-preflight-fail")


def test_binary_error_sanitizer_redacts_full_secret_assignment_values() -> None:
    proc = subprocess.CompletedProcess(
        [],
        7,
        stdout=b"NMBOT_API_TOKEN=stdout must never appear\n",
        stderr=(
            b'API_KEY="alpha beta gamma"\n'
            b"PASSWORD: hunter2\n"
            b"NMBOT_API_TOKEN=abc def\n"
            b"safe diagnostic line\n"
        ),
    )

    text = rel._sanitized_binary_error(proc)

    assert "remote binary command failed with exit=7" in text
    assert "safe diagnostic line" in text
    for leaked in ("stdout", "alpha", "beta", "gamma", "hunter2", "abc", "def", "API_KEY", "PASSWORD", "NMBOT_API_TOKEN"):
        assert leaked not in text


def test_bootstrap_out_dir_rejects_lexical_traversal_and_symlink_components(tmp_path: Path) -> None:
    allowed = Path("/tmp/opencode") / "nmbot-test-allowed-dir"
    project_allowed = rel.ROOT / "release_bundles" / "bootstrap" / "nmbot-test-allowed-dir"

    assert rel._allowed_bootstrap_out_dir(allowed) == allowed.resolve(strict=False)
    assert rel._allowed_bootstrap_out_dir(project_allowed) == project_allowed.resolve(strict=False)

    for bad in (Path("/tmp/opencode/../escape"), Path("/tmp/opencode/a/../../escape")):
        try:
            rel._allowed_bootstrap_out_dir(bad)
        except rel.ReleaseError as exc:
            assert "parent traversal" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"lexical traversal must fail: {bad}")

    symlink_parent = Path("/tmp/opencode") / f"nmbot-test-symlink-component-{tmp_path.name}"
    if symlink_parent.exists() or symlink_parent.is_symlink():
        symlink_parent.unlink() if symlink_parent.is_symlink() else __import__("shutil").rmtree(symlink_parent)
    symlink_parent.parent.mkdir(parents=True, exist_ok=True)
    symlink_parent.symlink_to(tmp_path)
    try:
        rel._allowed_bootstrap_out_dir(symlink_parent / "child")
    except rel.ReleaseError as exc:
        assert "symlink component" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("symlink component must fail")
    finally:
        if symlink_parent.is_symlink():
            symlink_parent.unlink()


def test_capture_final_release_path_competitors_survive_unchanged(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote"
    _copy_contract_tree(remote_root)
    out = Path("/tmp/opencode") / f"nmbot-test-capture-competitors-{tmp_path.name}"
    if out.exists() or out.is_symlink():
        import shutil
        out.unlink() if out.is_symlink() else shutil.rmtree(out)
    out.mkdir(parents=True)

    cases: list[tuple[str, str]] = [
        ("empty-dir", "dir"),
        ("nonempty-dir", "nonempty_dir"),
        ("file", "file"),
        ("symlink", "symlink"),
        ("broken-symlink", "broken_symlink"),
    ]
    for release_id, kind in cases:
        final = out / release_id
        if kind == "dir":
            final.mkdir()
        elif kind == "nonempty_dir":
            final.mkdir()
            (final / "keep.txt").write_text("competitor", encoding="utf-8")
        elif kind == "file":
            final.write_bytes(b"competitor-file")
        elif kind == "symlink":
            target = out / f"{release_id}-target"
            target.mkdir()
            final.symlink_to(target)
        elif kind == "broken_symlink":
            final.symlink_to(out / f"{release_id}-missing")
        before = _tree_inventory(out)

        try:
            rel.capture_baseline(remote=FakeBinaryRemote(remote_root), out_dir=out, release_id=release_id)
        except rel.ReleaseError as exc:
            assert "overwrite" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"existing final {kind} must fail")

        assert _tree_inventory(out) == before

    symlink_out = Path("/tmp/opencode") / "nmbot-test-capture-out-symlink"
    if symlink_out.exists() or symlink_out.is_symlink():
        symlink_out.unlink() if symlink_out.is_symlink() else __import__("shutil").rmtree(symlink_out)
    symlink_out.symlink_to(tmp_path)
    try:
        rel.capture_baseline(remote=FakeBinaryRemote(remote_root), out_dir=symlink_out, release_id="baseline-symlink-out")
    except rel.ReleaseError as exc:
        assert "symlink" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("symlink out dir must fail")


def test_capture_publish_race_no_clobber_preserves_competitor_and_publishes_none(tmp_path: Path, monkeypatch) -> None:
    remote_root = tmp_path / "remote"
    _copy_contract_tree(remote_root)
    out = Path("/tmp/opencode") / f"nmbot-test-race-{tmp_path.name}"
    if out.exists() or out.is_symlink():
        out.unlink() if out.is_symlink() else __import__("shutil").rmtree(out)
    original_rename = rel._rename_noreplace

    def rename_with_competitor(src: Path, dest: Path) -> None:
        dest.mkdir(parents=True)
        (dest / "keep.txt").write_text("competitor", encoding="utf-8")
        original_rename(src, dest)

    release_id = "baseline-race-dir"
    final = out / release_id
    monkeypatch.setattr(rel, "_rename_noreplace", rename_with_competitor)
    try:
        rel.capture_baseline(remote=FakeBinaryRemote(remote_root), out_dir=out, release_id=release_id)
    except rel.ReleaseError as exc:
        assert "overwrite" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("release directory publish race must fail")
    assert (final / "keep.txt").read_text(encoding="utf-8") == "competitor"
    assert len(list(final.iterdir())) == 1


def test_rename_noreplace_publishes_directory_once_and_refuses_empty_dir_replacement(tmp_path: Path) -> None:
    src = tmp_path / "src-release"
    src.mkdir()
    (src / "archive.tar.gz").write_bytes(b"archive")
    (src / "manifest.json").write_text("{}\n", encoding="utf-8")
    dst = tmp_path / "final-release"

    rel._rename_noreplace(src, dst)

    assert not src.exists()
    assert (dst / "archive.tar.gz").read_bytes() == b"archive"
    assert (dst / "manifest.json").read_text(encoding="utf-8") == "{}\n"

    next_src = tmp_path / "next-release"
    next_src.mkdir()
    try:
        rel._rename_noreplace(next_src, dst)
    except rel.ReleaseError as exc:
        assert "overwrite" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("rename_noreplace must not replace an existing directory")
    assert dst.is_dir()
    assert next_src.exists()


def test_generated_capture_command_executes_readonly_against_synthetic_root(tmp_path: Path, monkeypatch) -> None:
    remote_root = tmp_path / "synthetic-remote"
    _copy_contract_tree(remote_root)
    (remote_root / "data").mkdir(exist_ok=True)
    (remote_root / "logs").mkdir(exist_ok=True)
    external = tmp_path / "external-target"
    external.mkdir()
    (remote_root / "data" / "external-link").symlink_to(external)
    os.chmod(remote_root / "scripts", 0o755)
    before = _tree_inventory(remote_root)
    monkeypatch.setattr(rel, "DEFAULT_REMOTE_ROOT", str(remote_root))
    command = rel._capture_baseline_command(rel._contract_capture_paths(ROOT), remote_root=str(remote_root))
    proc = subprocess.run(command, shell=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    after = _tree_inventory(remote_root)
    assert before == after
    rel._extract_capture_tar(proc.stdout, tmp_path / "extract", rel._contract_capture_paths(ROOT))


def test_capture_baseline_mocked_binary_remote_builds_exact_preflighted_artifact(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote"
    _copy_contract_tree(remote_root)
    out = Path("/tmp/opencode") / "nmbot-test-capture"
    if out.exists():
        import shutil
        shutil.rmtree(out)
    fake = FakeBinaryRemote(remote_root)

    artifact = rel.capture_baseline(remote=fake, out_dir=out, release_id="baseline-test")

    manifest = rel.load_manifest(artifact.manifest)
    assert artifact.archive.exists() and artifact.manifest.exists()
    assert artifact.archive.parent == out / "baseline-test"
    assert artifact.manifest.parent == out / "baseline-test"
    assert manifest["release_id"] == "baseline-test"
    assert len(manifest["files"]) == len(rel._contract_capture_paths(ROOT)) + 1
    assert rel.local_preflight(archive=artifact.archive, manifest_path=artifact.manifest).startswith("preflight=ok")
    forbidden = re.compile(r"\b(mkdir|ln|mv|cp|rm|restart|start|stop|enable|disable)\b")
    assert fake.commands and not forbidden.search(fake.commands[0])


def test_snapshot_vps_source_generated_command_is_readonly_and_policy_based(tmp_path: Path, monkeypatch) -> None:
    remote_root = tmp_path / "synthetic-remote"
    _copy_contract_tree(remote_root)
    (remote_root / ".env").write_text("NMBOT_API_TOKEN=secret-value\n", encoding="utf-8")
    (remote_root / "data").mkdir(exist_ok=True)
    (remote_root / "logs").mkdir(exist_ok=True)
    (remote_root / "scripts" / "deploy_secret.py").write_text("print('no')\n", encoding="utf-8")
    before = _tree_inventory(remote_root)
    monkeypatch.setattr(rel, "DEFAULT_REMOTE_ROOT", str(remote_root))

    command = rel._snapshot_vps_source_command(remote_root=str(remote_root))
    forbidden = re.compile(r"\b(mkdir|ln|mv|cp|rm|restart|start|stop|enable|disable)\b")
    assert not forbidden.search(command)
    assert "paths" not in json.loads(__import__("shlex").split(command)[-1])
    proc = subprocess.run(command, shell=True, capture_output=True, check=False)

    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    assert _tree_inventory(remote_root) == before
    staging = tmp_path / "snapshot"
    manifest = rel._extract_snapshot_tar(proc.stdout, staging)
    paths = {item["path"] for item in manifest["files"]}
    assert "scripts/nmbot_api_server.py" in paths
    assert ".env" not in paths
    assert "scripts/deploy_secret.py" not in paths
    assert not any(path.startswith(("data/", "logs/", "tests/", "release_bundles/")) for path in paths)
    assert (staging / rel.SNAPSHOT_MANIFEST_NAME).is_file()


def test_snapshot_after_migration_reads_safe_current_release_but_keeps_canonical_root(tmp_path: Path, monkeypatch) -> None:
    remote_root = tmp_path / "synthetic-remote"
    release_root = remote_root / "releases" / "REL-current-safe"
    _copy_contract_tree(remote_root)
    _copy_contract_tree(release_root)
    canonical_api = remote_root / "scripts" / "nmbot_api_server.py"
    release_api = release_root / "scripts" / "nmbot_api_server.py"
    canonical_api.write_text("print('canonical stale api')\n", encoding="utf-8")
    release_api.write_text("print('current release api')\n", encoding="utf-8")
    (remote_root / "scripts" / "nmbot_egress_policy.py").unlink()
    (remote_root / "scripts" / "nmbot_release_identity.py").unlink()
    (remote_root / "current").symlink_to(release_root)
    before = _tree_inventory(remote_root)
    monkeypatch.setattr(rel, "DEFAULT_REMOTE_ROOT", str(remote_root))

    command = rel._snapshot_vps_source_command(remote_root=str(remote_root))
    proc = subprocess.run(command, shell=True, capture_output=True, check=False)

    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    assert _tree_inventory(remote_root) == before
    staging = tmp_path / "snapshot-current"
    manifest = rel._extract_snapshot_tar(proc.stdout, staging)
    paths = {item["path"] for item in manifest["files"]}
    rows = {item["path"]: item for item in manifest["files"]}
    assert manifest["remote_root"] == str(remote_root)
    assert "scripts/nmbot_egress_policy.py" in paths
    assert "scripts/nmbot_release_identity.py" in paths
    assert rows["scripts/nmbot_api_server.py"]["sha256"] == rel._sha256_file(release_api)
    assert rows["scripts/nmbot_api_server.py"]["sha256"] != rel._sha256_file(canonical_api)

    outside = tmp_path / "outside-release"
    _copy_contract_tree(outside)
    (remote_root / "current").unlink()
    (remote_root / "current").symlink_to(outside)
    bad = subprocess.run(command, shell=True, capture_output=True, check=False)
    assert bad.returncode != 0
    assert b"outside releases" in bad.stderr


def test_snapshot_collector_keeps_realistic_api_entrypoint_env_references(tmp_path: Path, monkeypatch) -> None:
    remote_root = tmp_path / "synthetic-remote"
    _copy_contract_tree(remote_root)
    api = remote_root / "scripts" / "nmbot_api_server.py"
    api.write_text(
        "import os\n"
        "token = os.getenv(\"NMBOT_API_TOKEN\", \"\")\n"
        "header_token = headers.get(\"X-NMBOT-API-Token\")\n"
        "def ok():\n"
        "    return token or header_token\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rel, "DEFAULT_REMOTE_ROOT", str(remote_root))

    proc = subprocess.run(rel._snapshot_vps_source_command(remote_root=str(remote_root)), shell=True, capture_output=True, check=False)

    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    manifest = rel._extract_snapshot_tar(proc.stdout, tmp_path / "snapshot")
    paths = {item["path"] for item in manifest["files"]}
    assert "scripts/nmbot_api_server.py" in paths


def test_snapshot_and_capture_reject_real_python_secret_literals_without_echo(tmp_path: Path, monkeypatch) -> None:
    cases = {
        "literal": 'API_TOKEN = "verysecretvalue123"\n',
        "private-key": 'PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\\nabc"\n',
        "bearer": 'AUTH_HEADER = "Bearer abcdefghijklmnopqrstuvwxyz123456"\n',
    }
    for name, body in cases.items():
        remote_root = tmp_path / f"remote-{name}"
        _copy_contract_tree(remote_root)
        (remote_root / "scripts" / "nmbot_api_server.py").write_text(body, encoding="utf-8")
        monkeypatch.setattr(rel, "DEFAULT_REMOTE_ROOT", str(remote_root))

        proc = subprocess.run(rel._snapshot_vps_source_command(remote_root=str(remote_root)), shell=True, capture_output=True, check=False)
        assert proc.returncode == 0, proc.stderr.decode(errors="replace")
        manifest = rel._extract_snapshot_tar(proc.stdout, tmp_path / f"snapshot-{name}")
        assert "scripts/nmbot_api_server.py" not in {item["path"] for item in manifest["files"]}

        expected = rel._contract_capture_paths(ROOT)
        archive = _tar_bytes(expected)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as src:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as out:
                for member in src.getmembers():
                    payload = body.encode("utf-8") if member.name == "scripts/nmbot_api_server.py" else src.extractfile(member).read()
                    info = tarfile.TarInfo(member.name)
                    info.size = len(payload)
                    info.mode = member.mode
                    out.addfile(info, io.BytesIO(payload))
        try:
            rel._extract_capture_tar(buf.getvalue(), tmp_path / f"capture-secret-{name}", expected)
        except rel.ReleaseError as exc:
            text = str(exc)
            assert "secret-like content" in text or "preflight" in text
            assert "verysecretvalue123" not in text and "abcdefghijklmnopqrstuvwxyz" not in text and "PRIVATE KEY" not in text
        else:  # pragma: no cover
            raise AssertionError(f"capture must reject python secret literal: {name}")


def test_capture_baseline_keeps_realistic_api_entrypoint_env_references(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote"
    _copy_contract_tree(remote_root)
    out = Path("/tmp/opencode") / f"nmbot-test-capture-envrefs-{tmp_path.name}"
    if out.exists() or out.is_symlink():
        out.unlink() if out.is_symlink() else __import__("shutil").rmtree(out)

    artifact = rel.capture_baseline(remote=FakeBinaryRemote(remote_root), out_dir=out, release_id="baseline-envrefs")

    manifest = rel.load_manifest(artifact.manifest)
    assert "scripts/nmbot_api_server.py" in {item["path"] for item in manifest["files"]}


def test_snapshot_contour_profiles_are_pinned_and_cli_defaults_to_test() -> None:
    default_args = rel.parse_args(["snapshot-vps-source"])
    compare_args = rel.parse_args(["compare-snapshot", "--snapshot-dir", "/tmp/opencode/snap"])
    assert default_args.contour == "test"
    assert compare_args.contour == "test"

    test_command = rel._snapshot_vps_source_command()
    prod_command = rel._snapshot_vps_source_command(contour="client-production")
    assert rel.DEFAULT_REMOTE_ROOT in test_command
    assert rel.CLIENT_PRODUCTION_REMOTE_ROOT in prod_command

    for bad in ("production", "", "../client-production"):
        try:
            rel._snapshot_vps_source_command(contour=bad)
        except rel.ReleaseError as exc:
            assert "contour" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("arbitrary snapshot contour must fail")

    for contour, remote_root in (("test", rel.CLIENT_PRODUCTION_REMOTE_ROOT), ("client-production", rel.DEFAULT_REMOTE_ROOT), ("test", "/tmp/evil")):
        try:
            rel._snapshot_vps_source_command(remote_root=remote_root, contour=contour)
        except rel.ReleaseError as exc:
            assert "contour/root" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("arbitrary snapshot root/profile pairing must fail")


def test_live_api_helper_overlay_has_exact_single_file_policy() -> None:
    assert rel._is_allowed_live_api_helper_overlay_file("scripts/nmbot_env_secrets.py")
    assert not rel._is_allowed_live_api_helper_overlay_file("scripts/nmbot_api_server.py")
    assert not rel._is_allowed_live_api_helper_overlay_file(".env")
    assert rel._validate_live_api_helper_overlay_paths(["scripts/nmbot_env_secrets.py"]) == ["scripts/nmbot_env_secrets.py"]
    for paths in ([], ["scripts/nmbot_api_server.py"], ["scripts/nmbot_env_secrets.py", "scripts/nmbot_env_secrets.py"]):
        with pytest.raises(rel.ReleaseError, match="exactly one fixed helper path"):
            rel._validate_live_api_helper_overlay_paths(paths)


def test_live_api_helper_overlay_parent_components_reject_symlinks_and_allow_real_directories(tmp_path: Path) -> None:
    root = tmp_path / "root"
    scripts = root / "scripts"
    staging = root / ".live_api_helper_overlay_staging"
    release_staging = staging / "live-helper-001"
    backups = root / "backups"
    for path in (scripts, release_staging, backups):
        path.mkdir(parents=True)

    rel._validate_live_api_helper_overlay_parent_components(root, "live-helper-001", require_release_staging=True)

    scripts.rmdir()
    scripts.symlink_to(tmp_path / "outside-scripts", target_is_directory=True)
    with pytest.raises(rel.ReleaseError, match="helper destination parent must be a real non-symlink directory"):
        rel._validate_live_api_helper_overlay_parent_components(root, "live-helper-001", require_release_staging=True)

    scripts.unlink()
    scripts.mkdir()
    release_staging.rmdir()
    staging.rmdir()
    staging.symlink_to(tmp_path / "outside-staging", target_is_directory=True)
    with pytest.raises(rel.ReleaseError, match="overlay staging parent must be a real non-symlink directory"):
        rel._validate_live_api_helper_overlay_parent_components(root, "live-helper-001", require_release_staging=True)

    staging.unlink()
    release_staging.mkdir(parents=True)
    release_staging.rmdir()
    release_staging.symlink_to(tmp_path / "outside-release-staging", target_is_directory=True)
    with pytest.raises(rel.ReleaseError, match="overlay release staging parent must be a real non-symlink directory"):
        rel._validate_live_api_helper_overlay_parent_components(root, "live-helper-001", require_release_staging=True)


def test_live_api_helper_overlay_rejects_wrong_target_before_source_capture() -> None:
    remote = HelperOverlayRemote()
    kwargs = {"release_id": "client-helper-001", "confirm": True, "remote": remote}
    for changes, text in (
        ({"confirm": False}, "--confirm"),
        ({"host": "evil@example.test"}, "not authorized"),
        ({"port": "22"}, "not authorized"),
    ):
        with pytest.raises(rel.ReleaseError, match=text):
            rel.live_api_helper_overlay(**(kwargs | changes))
    assert remote.commands == []


def test_live_api_helper_overlay_rejects_bad_execstart_before_source_capture_or_upload() -> None:
    non_migrated = HelperOverlayRemote(migrated=False)
    with pytest.raises(rel.ReleaseError, match="strict active API WorkingDirectory/ExecStart contract"):
        rel.live_api_helper_overlay(release_id="live-helper-001", confirm=True, remote=non_migrated)
    assert len(non_migrated.commands) == 1
    assert non_migrated.commands[0].startswith("systemctl --user show novostroy-bot-api.service")
    assert non_migrated.uploads == []


def test_live_api_helper_overlay_strict_unit_guard_rejects_extra_or_reordered_argv_and_wrong_directory() -> None:
    expected = f"{rel.DEFAULT_REMOTE_ROOT}/current/scripts/nmbot_api_server.py"
    cases = (
        (rel.DEFAULT_REMOTE_ROOT + "/current", f"/usr/bin/python3 {expected} --hidden"),
        (rel.DEFAULT_REMOTE_ROOT + "/current", f"/usr/bin/python3 {expected} {expected}"),
        (rel.DEFAULT_REMOTE_ROOT + "/current", f"/usr/bin/python3 /tmp/other.py {expected}"),
        (rel.DEFAULT_REMOTE_ROOT, f"/usr/bin/python3 {expected}"),
    )

    for working_directory, argv in cases:
        class MalformedUnitRemote:
            def __init__(self) -> None:
                self.commands: list[str] = []

            def run(self, command: str, *, input_text: str | None = None):
                self.commands.append(command)
                return subprocess.CompletedProcess([], 0, stdout=(
                    f"WorkingDirectory={working_directory}\n"
                    f"ExecStart={{ path=/usr/bin/python3 ; argv[]={argv} ; }}\n"
                    f"EnvironmentFiles={rel.DEFAULT_REMOTE_ROOT}/current/.env\n"
                    "Environment=\nExecStartPre=\n"
                ), stderr="")

            def run_binary(self, command: str):  # pragma: no cover - strict guard must stop first
                raise AssertionError("source capture must not run after strict unit rejection")

        remote = MalformedUnitRemote()
        with pytest.raises(rel.ReleaseError, match="strict active API WorkingDirectory/ExecStart contract"):
            rel.live_api_helper_overlay(release_id="live-helper-001", confirm=True, remote=remote)
        assert len(remote.commands) == 1


def test_live_api_helper_overlay_capture_failure_stops_before_lock_or_staging() -> None:
    remote = HelperOverlayRemote(fail_capture=True)
    with pytest.raises(rel.ReleaseError, match="remote binary command failed"):
        rel.live_api_helper_overlay(release_id="live-helper-001", confirm=True, remote=remote)
    assert len(remote.commands) == 2
    assert remote.commands[0].startswith("systemctl --user show novostroy-bot-api.service")
    assert '"mode": "acquire-lock"' not in "\n".join(remote.commands)
    assert '"mode": "prepare"' not in "\n".join(remote.commands)


def test_live_api_helper_overlay_is_narrow_atomic_and_leaves_services_untouched(tmp_path: Path) -> None:
    remote = HelperOverlayRemote()
    result = rel.live_api_helper_overlay(release_id="live-helper-001", confirm=True, remote=remote)

    assert result["ok"] is True
    assert result["destination"] == rel.LIVE_API_HELPER_OVERLAY_DESTINATION
    assert result["source_snapshot_id"].startswith("vps-source-helper-")
    assert remote.uploads == []
    commands = "\n".join(remote.commands)
    assert remote.commands[0].startswith("systemctl --user show novostroy-bot-api.service")
    assert remote.snapshot_calls == 1
    assert rel.LIVE_API_HELPER_OVERLAY_LOCK in commands
    assert "hashlib.sha256" in commands and "backup" in commands and "os.replace" in commands
    assert "rollback_tmp" in commands and "backup restored" in commands
    assert "os.open(\"/\"" in commands
    assert "O_NOFOLLOW" in commands and "dir_fd=" in commands
    assert "src_dir_fd=scriptsfd,dst_dir_fd=scriptsfd" in commands
    assert '"mode": "prepare"' in commands and '"mode": "acquire-lock"' in commands
    assert '"mode": "stage"' in commands
    assert "mkdir " + rel.LIVE_API_HELPER_OVERLAY_LOCK not in commands
    stage_command = next(command for command in remote.commands if '"mode": "stage"' in command)
    assert "scp" not in stage_command
    assert "base64.b64decode" in stage_command and 'validate=True' in stage_command
    assert 'write_new(releasefd,"nmbot_env_secrets.py",source,0o755,"staged helper")' in stage_command
    assert 'open_dir(stagingfd,cfg["release_id"],"overlay release staging parent")' in stage_command
    stage_payload = json.loads(__import__("shlex").split(stage_command)[-1])
    assert hashlib.sha256(base64.b64decode(stage_payload["staged_data_b64"], validate=True)).hexdigest() == stage_payload["expected_sha256"]
    publish_command = next(command for command in remote.commands if '"operation":"live_api_helper_overlay"' in command)
    assert "systemctl" not in publish_command and ".env" not in publish_command and "/current" not in publish_command


def test_live_api_helper_overlay_failure_still_cleans_lock_and_has_remote_rollback(tmp_path: Path) -> None:
    remote = HelperOverlayRemote(fail_publish=True)
    with pytest.raises(rel.ReleaseError, match="live-api-helper-overlay publish failed"):
        rel.live_api_helper_overlay(release_id="live-helper-001", confirm=True, remote=remote)
    commands = "\n".join(remote.commands)
    assert '"mode": "cleanup"' in commands
    publish_command = next(command for command in remote.commands if '"operation":"live_api_helper_overlay"' in command)
    assert "if replaced:" in publish_command and "os.replace(rollback_tmp,\"nmbot_env_secrets.py\",src_dir_fd=scriptsfd,dst_dir_fd=scriptsfd)" in publish_command


def _run_local_helper_overlay_publish(tmp_path: Path, *, force_rollback_failure: bool = False) -> tuple[subprocess.CompletedProcess[str], Path, bytes]:
    """Execute the emitted remote writer against an isolated filesystem root."""
    root = tmp_path / "novostroy-bot"
    scripts = root / "scripts"
    staging = root / ".live_api_helper_overlay_staging" / "live-helper-001"
    for directory in (scripts, root / "backups", staging, root / ".live_api_helper_overlay_lock"):
        directory.mkdir(parents=True, exist_ok=True)
    original = b"original helper\n"
    replacement = b"replacement helper\n"
    helper = scripts / "nmbot_env_secrets.py"
    helper.write_bytes(original)
    helper.chmod(0o755)
    (staging / "nmbot_env_secrets.py").write_bytes(replacement)

    command = rel._live_api_helper_overlay_command(
        release_id="live-helper-001",
        expected_sha256=hashlib.sha256(replacement).hexdigest(),
        staging_file=f"{rel.LIVE_API_HELPER_OVERLAY_STAGING}/live-helper-001/nmbot_env_secrets.py",
    )
    _, _, code, payload_text = shlex.split(command)
    local_root = str(root)
    code = code.replace(rel.DEFAULT_REMOTE_ROOT, local_root)
    fixed_traversal = '''basefd=require_dir(os.open("/", os.O_RDONLY|DIRECTORY|NOFOLLOW),"trusted filesystem root")
    homefd=open_dir(basefd,"home","fixed root parent")
    neirofd=open_dir(homefd,"neiro","fixed root parent")
    rootfd=open_dir(neirofd,"novostroy-bot","fixed root")'''
    isolated_traversal = '''basefd=require_dir(os.open("/", os.O_RDONLY|DIRECTORY|NOFOLLOW),"trusted filesystem root")
    homefd=neirofd=None
    rootfd=require_dir(os.open(cfg["root"], os.O_RDONLY|DIRECTORY|NOFOLLOW),"fixed root")'''
    assert fixed_traversal in code
    code = code.replace(fixed_traversal, isolated_traversal)
    # This is the emitted writer with its post-replace assertion forced to fail.
    # It proves that a failure raised after os.replace follows the rollback path.
    assertion = 'if digest(read_regular(scriptsfd,"nmbot_env_secrets.py","published helper")) != cfg["expected_sha256"]: raise PublishValidationError("published helper hash mismatch")'
    assert assertion in code
    code = code.replace(assertion, 'raise PublishValidationError("forced post-replace failure")')
    if force_rollback_failure:
        rollback = 'os.replace(rollback_tmp,"nmbot_env_secrets.py",src_dir_fd=scriptsfd,dst_dir_fd=scriptsfd); os.fsync(scriptsfd)'
        assert rollback in code
        code = code.replace(rollback, 'raise OSError("forced rollback failure")')
    payload_text = payload_text.replace(rel.DEFAULT_REMOTE_ROOT, local_root)
    result = subprocess.run(["python3", "-c", code, payload_text], text=True, capture_output=True, check=False)
    return result, helper, original


def test_emitted_live_helper_writer_rolls_back_post_replace_failure(tmp_path: Path) -> None:
    result, helper, original = _run_local_helper_overlay_publish(tmp_path)

    assert result.returncode == 2
    assert helper.read_bytes() == original
    assert json.loads(result.stdout)["error"].endswith("backup restored=true")


def test_emitted_live_helper_writer_does_not_claim_failed_rollback_was_restored(tmp_path: Path) -> None:
    result, helper, original = _run_local_helper_overlay_publish(tmp_path, force_rollback_failure=True)

    assert result.returncode == 2
    assert helper.read_bytes() != original
    assert json.loads(result.stdout)["error"].endswith("backup restored=false")


def test_live_api_helper_overlay_stage_failure_performs_no_scp_upload_and_cleans_lock(tmp_path: Path) -> None:
    remote = HelperOverlayRemote(fail_stage=True)

    with pytest.raises(rel.ReleaseError, match="descriptor-confined staging failed"):
        rel.live_api_helper_overlay(release_id="live-helper-001", confirm=True, remote=remote)

    assert remote.uploads == []
    commands = "\n".join(remote.commands)
    assert '"mode": "stage"' in commands and '"mode": "cleanup"' in commands
    assert '"mode": "publish"' not in commands


def test_live_api_helper_overlay_command_binds_every_write_to_verified_directory_fds() -> None:
    command = rel._live_api_helper_overlay_command(
        release_id="live-helper-001",
        expected_sha256="a" * 64,
        staging_file=f"{rel.LIVE_API_HELPER_OVERLAY_STAGING}/live-helper-001/nmbot_env_secrets.py",
    )

    # The helper traverses from an immutable filesystem FD.  No validated
    # absolute pathname is later passed to mkdir/open/replace/unlink/rmdir.
    assert 'os.open("/", os.O_RDONLY|DIRECTORY|NOFOLLOW)' in command
    assert 'open_dir(basefd,"home"' in command
    assert 'open_dir(homefd,"neiro"' in command
    assert 'open_dir(neirofd,"novostroy-bot"' in command
    assert "os.mkdir(name, 0o700, dir_fd=parent)" in command
    assert 'if not existing_ok: fail(label+" already exists")' in command
    assert "os.open(name, os.O_RDONLY|DIRECTORY|NOFOLLOW, dir_fd=parent)" in command
    assert "os.replace(tmp,\"nmbot_env_secrets.py\",src_dir_fd=scriptsfd,dst_dir_fd=scriptsfd)" in command
    assert "os.unlink(\"nmbot_env_secrets.py\", dir_fd=releasefd)" in command
    assert "os.rmdir(cfg[\"release_id\"], dir_fd=stagingfd)" in command


def test_live_api_helper_overlay_cli_has_no_overlay_or_root_override_and_legacy_route_is_rejected() -> None:
    args = rel.parse_args([
        "live-api-helper-overlay", "--release-id", "live-helper-001",
        "--host", rel.AUTHORIZED_DEPLOY_HOST, "--confirm",
    ])
    assert not hasattr(args, "overlay") and not hasattr(args, "remote_root") and not hasattr(args, "contour") and not hasattr(args, "snapshot_dir") and not hasattr(args, "source_snapshot_manifest_sha256")
    with pytest.raises(SystemExit):
        rel.parse_args(["live-api-helper-overlay", "--release-id", "live-helper-001", "--snapshot-dir", "/tmp/opencode/snap", "--host", rel.AUTHORIZED_DEPLOY_HOST, "--confirm"])
    with pytest.raises(SystemExit):
        rel.parse_args(["client-production-helper-overlay"])


def test_snapshot_manifest_validation_rejects_contour_root_mismatch() -> None:
    manifest = _minimal_snapshot_manifest()
    manifest["remote_root"] = rel.CLIENT_PRODUCTION_REMOTE_ROOT
    try:
        rel._validate_snapshot_manifest_data(manifest)
    except rel.ReleaseError as exc:
        assert "contour/root" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("test contour with production root must fail")

    manifest["contour"] = "client-production"
    assert rel._validate_snapshot_manifest_data(manifest)["remote_root"] == rel.CLIENT_PRODUCTION_REMOTE_ROOT


def test_snapshot_remote_collector_skips_hidden_paths_before_strict_validation() -> None:
    command = rel._snapshot_vps_source_command(contour="test")
    assert 'if any(part.startswith(".") for part in pathlib.PurePosixPath(raw_rel).parts): continue' in command
    assert 'rel=safe_rel(raw_rel)' in command


def test_snapshot_tar_manifest_strictly_rejects_extra_duplicate_hash_size_mode_and_links(tmp_path: Path) -> None:
    payload = b"print('ok')\n"
    manifest = _minimal_snapshot_manifest(payload)
    rel._extract_snapshot_tar(_snapshot_tar_bytes(manifest, {"scripts/nmbot_api_server.py": payload}), tmp_path / "ok")

    cases: list[tuple[dict, bytes, str]] = []
    bad = json.loads(json.dumps(manifest))
    bad["tar_members"] = bad["tar_members"] + ["source/scripts/extra.py"]
    cases.append((bad, _snapshot_tar_bytes(bad, {"scripts/nmbot_api_server.py": payload}, extra="source/scripts/extra.py"), "member"))
    cases.append((manifest, _snapshot_tar_bytes(manifest, {"scripts/nmbot_api_server.py": payload}, duplicate=True), "match"))
    bad_hash = json.loads(json.dumps(manifest))
    bad_hash["files"][0]["sha256"] = "0" * 64
    cases.append((bad_hash, _snapshot_tar_bytes(bad_hash, {"scripts/nmbot_api_server.py": payload}), "hash"))
    bad_size = json.loads(json.dumps(manifest))
    bad_size["files"][0]["size"] += 1
    cases.append((bad_size, _snapshot_tar_bytes(bad_size, {"scripts/nmbot_api_server.py": payload}), "metadata"))
    bad_bool_size = json.loads(json.dumps(manifest))
    bad_bool_size["files"][0]["size"] = True
    cases.append((bad_bool_size, _snapshot_tar_bytes(bad_bool_size, {"scripts/nmbot_api_server.py": payload}), "size"))
    bad_snapshot_id = json.loads(json.dumps(manifest))
    bad_snapshot_id["snapshot_id"] = 123
    cases.append((bad_snapshot_id, _snapshot_tar_bytes(bad_snapshot_id, {"scripts/nmbot_api_server.py": payload}), "snapshot_id"))
    bad_policy_bool = json.loads(json.dumps(manifest))
    bad_policy_bool["policy"]["exclude_secret_like"] = 1
    cases.append((bad_policy_bool, _snapshot_tar_bytes(bad_policy_bool, {"scripts/nmbot_api_server.py": payload}), "booleans"))
    unsorted_manifest = _minimal_snapshot_manifest(payload)
    extra_payload = b"print('x')\n"
    unsorted_manifest["files"].append({"path": "followup_intent_classifier.py", "sha256": rel._sha256_bytes(extra_payload), "size": len(extra_payload), "mode": 0o644})
    unsorted_manifest["tar_members"] = [rel.SNAPSHOT_MANIFEST_NAME] + [rel.SNAPSHOT_SOURCE_PREFIX + item["path"] for item in unsorted_manifest["files"]]
    cases.append((unsorted_manifest, _snapshot_tar_bytes(unsorted_manifest, {"scripts/nmbot_api_server.py": payload, "followup_intent_classifier.py": extra_payload}), "sorted"))
    too_many = json.loads(json.dumps(manifest))
    too_many["files"] = [{"path": "scripts/nmbot_api_server.py", "sha256": rel._sha256_bytes(payload), "size": len(payload), "mode": 0o755} for _ in range(rel.MAX_FILES + 1)]
    try:
        rel._validate_snapshot_manifest_data(too_many)
    except rel.ReleaseError as exc:
        assert "count" in str(exc) or "duplicate" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("oversized manifest must fail")
    cases.append((manifest, _snapshot_tar_bytes(manifest, {"scripts/nmbot_api_server.py": payload}, mode_delta=1), "metadata"))

    link_buf = io.BytesIO()
    with tarfile.open(fileobj=link_buf, mode="w") as tf:
        body = rel._canonical_snapshot_manifest_bytes(manifest)
        info = tarfile.TarInfo(rel.SNAPSHOT_MANIFEST_NAME); info.size = len(body); tf.addfile(info, io.BytesIO(body))
        link = tarfile.TarInfo(rel.SNAPSHOT_SOURCE_PREFIX + "scripts/nmbot_api_server.py"); link.type = tarfile.SYMTYPE; link.linkname = "/tmp/evil"; tf.addfile(link)
    cases.append((manifest, link_buf.getvalue(), "unsafe"))

    for _, payload_bytes, expected in cases:
        try:
            rel._extract_snapshot_tar(payload_bytes, tmp_path / f"bad-{expected}-{len(list(tmp_path.iterdir()))}")
        except rel.ReleaseError as exc:
            assert expected in str(exc) or "match" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"tampered snapshot must fail: {expected}")


def test_generated_snapshot_descriptor_reader_never_captures_raced_symlink_target(tmp_path: Path, monkeypatch) -> None:
    remote_root = tmp_path / "racy-remote"
    _copy_contract_tree(remote_root)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("API_TOKEN=outside-secret-value\n", encoding="utf-8")
    target = remote_root / "scripts" / "nmbot_api_server.py"
    original = target.read_bytes()
    stop = threading.Event()

    def racer() -> None:
        while not stop.is_set():
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            try:
                target.symlink_to(outside)
            except FileExistsError:
                pass
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            try:
                target.write_bytes(original)
            except FileExistsError:
                pass

    monkeypatch.setattr(rel, "DEFAULT_REMOTE_ROOT", str(remote_root))
    thread = threading.Thread(target=racer)
    thread.start()
    try:
        proc = subprocess.run(rel._snapshot_vps_source_command(remote_root=str(remote_root)), shell=True, capture_output=True, check=False)
    finally:
        stop.set()
        thread.join(timeout=5)
    if proc.returncode == 0:
        assert b"outside-secret-value" not in proc.stdout
        manifest = rel._extract_snapshot_tar(proc.stdout, tmp_path / "racy-snapshot")
        rows = {item["path"]: item for item in manifest["files"]}
        if "scripts/nmbot_api_server.py" in rows:
            assert rows["scripts/nmbot_api_server.py"]["sha256"] == rel._sha256_bytes(original)
    else:
        assert b"outside-secret-value" not in proc.stdout + proc.stderr


def test_snapshot_publish_worktree_no_clobber_and_compare_metadata_only(tmp_path: Path, monkeypatch) -> None:
    remote_root = tmp_path / "synthetic-remote"
    _copy_contract_tree(remote_root)
    monkeypatch.setattr(rel, "DEFAULT_REMOTE_ROOT", str(remote_root))
    out = Path("/tmp/opencode") / f"nmbot-test-source-snapshot-{tmp_path.name}"
    work_out = Path("/tmp/opencode") / f"nmbot-test-worktree-{tmp_path.name}"
    for path in (out, work_out):
        if path.exists() or path.is_symlink():
            path.unlink() if path.is_symlink() else __import__("shutil").rmtree(path)

    result = rel.snapshot_vps_source(remote=LocalBinaryCommandRemote(), out_dir=out)
    snapshot_dir = Path(result["snapshot_dir"])
    assert (snapshot_dir / "source" / "scripts" / "nmbot_api_server.py").is_file()
    assert (snapshot_dir / "snapshot.tar").is_file()

    race_out = Path("/tmp/opencode") / f"nmbot-test-source-snapshot-race-{tmp_path.name}"
    if race_out.exists() or race_out.is_symlink():
        race_out.unlink() if race_out.is_symlink() else __import__("shutil").rmtree(race_out)
    original_rename = rel._rename_noreplace

    def rename_with_competitor(src: Path, dest: Path) -> None:
        dest.mkdir(parents=True)
        (dest / "keep.txt").write_text("competitor", encoding="utf-8")
        original_rename(src, dest)

    monkeypatch.setattr(rel, "_rename_noreplace", rename_with_competitor)
    try:
        rel.snapshot_vps_source(remote=LocalBinaryCommandRemote(), out_dir=race_out)
    except rel.ReleaseError as exc:
        assert "overwrite" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("snapshot publish race must fail")
    final_dirs = [p for p in race_out.iterdir() if p.is_dir()]
    assert len(final_dirs) == 1 and (final_dirs[0] / "keep.txt").read_text(encoding="utf-8") == "competitor"
    monkeypatch.setattr(rel, "_rename_noreplace", original_rename)

    work = rel.prepare_worktree(snapshot_dir=snapshot_dir, out_dir=work_out)
    worktree_dir = Path(work["worktree_dir"])
    provenance = json.loads((worktree_dir / "snapshot-provenance.json").read_text(encoding="utf-8"))
    assert provenance["schema"] == rel.WORKTREE_PROVENANCE_SCHEMA
    assert provenance["snapshot_id"] == result["snapshot_id"]
    assert provenance["snapshot_manifest_sha256"] == result["manifest_sha256"]
    assert provenance["source_host"] == rel.AUTHORIZED_DEPLOY_HOST
    assert provenance["remote_root"] == str(remote_root)
    assert re.fullmatch(r"[0-9a-f]{64}", provenance["source_tree_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", provenance["source_manifest_sha256"])
    assert not any(p.name.startswith(".env") or p.parts[-1] in {"data", "logs"} for p in worktree_dir.rglob("*"))
    try:
        rel.prepare_worktree(snapshot_dir=snapshot_dir, out_dir=work_out)
    except rel.ReleaseError as exc:
        assert "overwrite" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("worktree publish must not clobber")

    compare = rel.compare_snapshot(snapshot_dir=snapshot_dir, project_root=ROOT)
    assert set(compare) == {"schema", "snapshot_id", "snapshot_manifest_sha256", "contour", "remote_root", "project_root", "added", "missing", "changed"}
    assert all(set(row) <= {"path", "snapshot_sha256", "project_sha256"} for group in ("added", "missing", "changed") for row in compare[group])
    encoded = json.dumps(compare, ensure_ascii=False)
    assert "print(" not in encoded and "def " not in encoded
    try:
        rel.compare_snapshot(snapshot_dir=snapshot_dir, project_root=tmp_path)
    except rel.ReleaseError as exc:
        assert "project-root" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("compare must not read arbitrary project roots")


def test_prepare_worktree_rejects_symlink_mutation_after_verify_without_copying_external(tmp_path: Path, monkeypatch) -> None:
    remote_root = tmp_path / "synthetic-remote"
    _copy_contract_tree(remote_root)
    monkeypatch.setattr(rel, "DEFAULT_REMOTE_ROOT", str(remote_root))
    out = Path("/tmp/opencode") / f"nmbot-test-symlink-race-{tmp_path.name}"
    work_out = Path("/tmp/opencode") / f"nmbot-test-symlink-work-{tmp_path.name}"
    for path in (out, work_out):
        if path.exists() or path.is_symlink():
            path.unlink() if path.is_symlink() else __import__("shutil").rmtree(path)
    result = rel.snapshot_vps_source(remote=LocalBinaryCommandRemote(), out_dir=out)
    snapshot_dir = Path(result["snapshot_dir"])
    victim = snapshot_dir / "source" / "scripts" / "nmbot_api_server.py"
    external = tmp_path / "external.py"
    external.write_text("API_TOKEN=outside-secret-value\n", encoding="utf-8")
    original_reader = rel._read_file_openat_no_follow
    state = {"done": False}

    def mutate_after_first_verify(root: Path, relative: str, expected: dict) -> tuple[bytes, int]:
        data = original_reader(root, relative, expected)
        if not state["done"] and relative == "scripts/nmbot_api_server.py":
            state["done"] = True
            victim.unlink()
            victim.symlink_to(external)
        return data

    monkeypatch.setattr(rel, "_read_file_openat_no_follow", mutate_after_first_verify)
    try:
        rel.prepare_worktree(snapshot_dir=snapshot_dir, out_dir=work_out)
    except (rel.ReleaseError, OSError):
        pass
    else:  # pragma: no cover
        raise AssertionError("symlink mutation after verify must not prepare worktree")
    assert not any(p.is_file() and b"outside-secret-value" in p.read_bytes() for p in work_out.rglob("*") if not p.is_symlink())


def test_build_from_worktree_provenance_and_deploy_hash_guard(tmp_path: Path, monkeypatch) -> None:
    remote_root = tmp_path / "synthetic-remote"
    _copy_contract_tree(remote_root)
    monkeypatch.setattr(rel, "DEFAULT_REMOTE_ROOT", str(remote_root))
    snap_out = Path("/tmp/opencode") / f"nmbot-test-linked-snapshot-{tmp_path.name}"
    work_out = Path("/tmp/opencode") / f"nmbot-test-linked-work-{tmp_path.name}"
    for path in (snap_out, work_out):
        if path.exists() or path.is_symlink():
            path.unlink() if path.is_symlink() else __import__("shutil").rmtree(path)
    snapshot = rel.snapshot_vps_source(remote=LocalBinaryCommandRemote(), out_dir=snap_out)
    work = rel.prepare_worktree(snapshot_dir=Path(snapshot["snapshot_dir"]), out_dir=work_out)
    worktree_dir = Path(work["worktree_dir"])
    baseline_provenance = json.loads((worktree_dir / "snapshot-provenance.json").read_text(encoding="utf-8"))
    candidate_source = worktree_dir / "source" / "scripts" / "nmbot_api_server.py"
    candidate_source.write_text(candidate_source.read_text(encoding="utf-8") + "\n# candidate source edit allowed after prepare-worktree\n", encoding="utf-8")
    current_rows = rel._worktree_source_rows(worktree_dir / "source")
    current_tree_sha = rel._tree_hash_from_records(current_rows)
    current_manifest_sha = rel._source_manifest_sha(current_rows)
    assert current_tree_sha != baseline_provenance["source_tree_sha256"]
    assert current_manifest_sha != baseline_provenance["source_manifest_sha256"]
    artifact = rel.build_from_worktree(worktree_dir=Path(work["worktree_dir"]), release_id="linked-flow", out_dir=tmp_path / "artifact")
    manifest = rel.load_manifest(artifact.manifest)
    provenance = manifest["source_provenance"]
    assert provenance["present"] is True
    assert provenance["source_snapshot_id"] == snapshot["snapshot_id"]
    assert provenance["source_snapshot_manifest_sha256"] == snapshot["manifest_sha256"]
    assert provenance["source_snapshot_id"] == baseline_provenance["snapshot_id"]
    assert provenance["source_snapshot_manifest_sha256"] == baseline_provenance["snapshot_manifest_sha256"]
    assert provenance["worktree_source_tree_sha256"] == current_tree_sha
    assert provenance["worktree_source_manifest_sha256"] == current_manifest_sha
    fake = FakeRemote(remote_root="/remote")
    assert rel.deploy(release_id="linked-flow", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=fake, remote_root="/remote", source_snapshot_manifest_sha256=snapshot["manifest_sha256"]).startswith("deploy=ok")
    assert fake.uploads

    absent = rel.build(release_id="absent-prov", out_dir=tmp_path / "absent")
    fake_absent = FakeRemote(remote_root="/remote")
    try:
        rel.deploy(release_id="absent-prov", archive=absent.archive, manifest_path=absent.manifest, confirm=True, remote=fake_absent, remote_root="/remote", source_snapshot_manifest_sha256=snapshot["manifest_sha256"])
    except rel.ReleaseError as exc:
        assert "provenance" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("deploy with supplied snapshot hash must reject absent provenance")
    assert fake_absent.commands == [] and fake_absent.uploads == []

    fake_mismatch = FakeRemote(remote_root="/remote")
    try:
        rel.deploy(release_id="linked-flow", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=fake_mismatch, remote_root="/remote", source_snapshot_manifest_sha256="0" * 64)
    except rel.ReleaseError as exc:
        assert "does not match" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("deploy must reject arbitrary snapshot hash mismatch")
    assert fake_mismatch.commands == [] and fake_mismatch.uploads == []

    unsafe_worktree = Path(work["worktree_dir"])
    unsafe_source = unsafe_worktree / "source" / "scripts" / "nmbot_api_server.py"
    unsafe_source.unlink()
    unsafe_source.symlink_to(tmp_path / "outside-secret-like.py")
    try:
        rel.build_from_worktree(worktree_dir=unsafe_worktree, release_id="unsafe-worktree", out_dir=tmp_path / "unsafe-artifact")
    except rel.ReleaseError as exc:
        assert "non-regular" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unsafe prepared worktree mutation must fail before build")


def test_test_release_parser_accepts_repeated_overlays_confirm_and_authorized_defaults() -> None:
    args = rel.parse_args([
        "test-release",
        "--release-id", "REL-test-one",
        "--overlay", "scripts/nmbot_api_server.py",
        "--overlay", "nmbot_v2/runtime.py",
        "--confirm",
    ])

    assert args.command == "test-release"
    assert args.release_id == "REL-test-one"
    assert args.overlay == ["scripts/nmbot_api_server.py", "nmbot_v2/runtime.py"]
    assert args.confirm is True
    assert args.host == rel.AUTHORIZED_DEPLOY_HOST
    assert args.port == rel.AUTHORIZED_DEPLOY_PORT


def test_test_release_overlay_paths_are_canonicalized_independent_of_cli_order() -> None:
    assert rel._validate_overlay_path_list([
        "scripts/nmbot_api_server.py",
        "nmbot_v2/runtime.py",
    ]) == ["nmbot_v2/runtime.py", "scripts/nmbot_api_server.py"]


def test_test_release_overlay_policy_allows_v4_runtime_files() -> None:
    assert rel._validate_overlay_path_list([
        "nmbot_v4/runtime.py",
        "nmbot_v4/response_validator.py",
    ]) == ["nmbot_v4/response_validator.py", "nmbot_v4/runtime.py"]


def test_test_release_overlay_policy_allows_only_env_helper_from_api_deny_list() -> None:
    assert rel._validate_overlay_path_list([
        "scripts/nmbot_env_secrets.py",
        "scripts/nmbot_api_server.py",
    ]) == ["scripts/nmbot_api_server.py", "scripts/nmbot_env_secrets.py"]

    for forbidden in ("scripts/nmbot_n8n_bridge_server.py", "scripts/nmbot_callback_sheet_worker.py"):
        with pytest.raises(rel.ReleaseError, match="safe runtime"):
            rel._validate_overlay_path_list([forbidden])


def _prepared_api_worktree_with_stale_overlay(tmp_path: Path, monkeypatch, *, stale: bool = True) -> tuple[dict, dict, Path]:
    remote_root = tmp_path / "synthetic-remote"
    _copy_contract_tree(remote_root)
    if stale:
        api = remote_root / "scripts" / "nmbot_api_server.py"
        api.write_text(api.read_text(encoding="utf-8") + "\n# stale live snapshot copy\n", encoding="utf-8")
    monkeypatch.setattr(rel, "DEFAULT_REMOTE_ROOT", str(remote_root))
    base = Path("/tmp/opencode") / f"nmbot-test-release-overlay-{tmp_path.name}-{int(stale)}"
    if base.exists() or base.is_symlink():
        base.unlink() if base.is_symlink() else __import__("shutil").rmtree(base)
    snap = rel.snapshot_vps_source(remote=LocalBinaryCommandRemote(), out_dir=base / "snapshots")
    work = rel.prepare_worktree(snapshot_dir=Path(snap["snapshot_dir"]), out_dir=base / "worktrees")
    return snap, work, base


def _local_overlay_root_with_additions(tmp_path: Path, additions: dict[str, bytes]) -> Path:
    local_root = tmp_path / "local-overlay-root"
    _copy_contract_tree(local_root)
    for relpath, payload in additions.items():
        target = local_root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return local_root


def _overlay_temp_debris(source_dir: Path, relpath: str) -> list[Path]:
    target = source_dir / relpath
    return sorted(target.parent.glob(f".{target.name}.test-release.*.tmp"))


def test_test_release_overlay_helper_exact_one_changed_file_no_added_missing(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)

    diff = rel.apply_test_release_overlay(worktree_dir=Path(work["worktree_dir"]), overlays=["scripts/nmbot_api_server.py"])

    assert diff["added"] == []
    assert diff["missing"] == []
    assert [item["path"] for item in diff["changed"]] == ["scripts/nmbot_api_server.py"]
    assert diff["overlay_paths"] == ["scripts/nmbot_api_server.py"]


def test_test_release_overlay_adds_new_allowlisted_runtime_files_with_hashes_and_modes(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    additions = {
        "nmbot_v1/new_module.py": b"VALUE = 'runtime add ok'\n",
        "prompts/v1/new_prompt.txt": "Новый тестовый промпт без секретов.\n".encode("utf-8"),
    }
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, additions))

    diff = rel.apply_test_release_overlay(
        worktree_dir=Path(work["worktree_dir"]),
        overlays=["prompts/v1/new_prompt.txt", "nmbot_v1/new_module.py"],
    )

    assert diff["missing"] == []
    assert diff["changed"] == []
    assert [item["path"] for item in diff["added"]] == ["nmbot_v1/new_module.py", "prompts/v1/new_prompt.txt"]
    assert diff["overlay_paths"] == ["nmbot_v1/new_module.py", "prompts/v1/new_prompt.txt"]
    source = Path(work["worktree_dir"]) / "source"
    for relpath, payload in additions.items():
        target = source / relpath
        assert target.read_bytes() == payload
        assert (target.stat().st_mode & 0o777) == 0o644
        added = next(item for item in diff["added"] if item["path"] == relpath)
        assert added["worktree_sha256"] == rel._sha256_bytes(payload)


def test_test_release_overlay_can_add_env_helper_to_test_artifact_only(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)

    diff = rel.apply_test_release_overlay(
        worktree_dir=Path(work["worktree_dir"]),
        overlays=["scripts/nmbot_env_secrets.py"],
    )
    artifact = rel.build_from_worktree(
        worktree_dir=Path(work["worktree_dir"]),
        release_id="REL-test-env-helper",
        out_dir=tmp_path / "artifact",
        test_api_overlay_paths=frozenset({"scripts/nmbot_env_secrets.py"}),
    )
    manifest = rel.load_manifest(artifact.manifest)
    paths = {item["path"] for item in manifest["files"]}

    assert diff["changed"] == []
    assert [item["path"] for item in diff["added"]] == ["scripts/nmbot_env_secrets.py"]
    assert "scripts/nmbot_env_secrets.py" in paths
    assert "scripts/nmbot_n8n_bridge_server.py" not in paths
    assert "scripts/nmbot_callback_sheet_worker.py" not in paths
    assert rel.local_preflight(archive=artifact.archive, manifest_path=artifact.manifest).startswith("preflight=ok")


def test_test_release_overlay_env_helper_still_rejects_literal_secret_content(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    local_root = _local_overlay_root_with_additions(
        tmp_path,
        {"scripts/nmbot_env_secrets.py": b"NMBOT_API_TOKEN = 'sk-secret-literal-value-12345'\n"},
    )
    monkeypatch.setattr(rel, "ROOT", local_root)

    with pytest.raises(rel.ReleaseError, match="secret-like content"):
        rel.apply_test_release_overlay(worktree_dir=Path(work["worktree_dir"]), overlays=["scripts/nmbot_env_secrets.py"])


def test_test_release_overlay_mixes_existing_replace_and_new_add(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    addition = {"nmbot_v1/nested/new_module.py": b"def marker():\n    return 'added'\n"}
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, addition))

    diff = rel.apply_test_release_overlay(
        worktree_dir=Path(work["worktree_dir"]),
        overlays=["scripts/nmbot_api_server.py", "nmbot_v1/nested/new_module.py"],
    )

    assert diff["missing"] == []
    assert [item["path"] for item in diff["added"]] == ["nmbot_v1/nested/new_module.py"]
    assert [item["path"] for item in diff["changed"]] == ["scripts/nmbot_api_server.py"]
    assert diff["overlay_paths"] == ["nmbot_v1/nested/new_module.py", "scripts/nmbot_api_server.py"]


def test_test_release_overlay_rejects_unlisted_extra_added_file_after_overlay(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {"nmbot_v1/new_module.py": b"VALUE = 'ok'\n"}))
    real_replace = rel._replace_overlay_dest_no_follow

    def replace_and_add_extra(**kwargs):
        real_replace(**kwargs)
        extra = Path(work["worktree_dir"]) / "source" / "prompts" / "v1" / "unlisted.txt"
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("unlisted but otherwise safe\n", encoding="utf-8")

    monkeypatch.setattr(rel, "_replace_overlay_dest_no_follow", replace_and_add_extra)

    with pytest.raises(rel.ReleaseError, match="exact diff"):
        rel.apply_test_release_overlay(worktree_dir=Path(work["worktree_dir"]), overlays=["nmbot_v1/new_module.py"])


def test_test_release_overlay_rejects_duplicate_unsafe_missing_unchanged_and_unlisted_mutation(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    worktree_dir = Path(work["worktree_dir"])
    with pytest.raises(rel.ReleaseError, match="duplicate"):
        rel.apply_test_release_overlay(worktree_dir=worktree_dir, overlays=["scripts/nmbot_api_server.py", "scripts/nmbot_api_server.py"])
    with pytest.raises(rel.ReleaseError, match="safe runtime"):
        rel.apply_test_release_overlay(worktree_dir=worktree_dir, overlays=[".env"])
    with pytest.raises(rel.ReleaseError, match="source must already"):
        rel.apply_test_release_overlay(worktree_dir=worktree_dir, overlays=["prompts/no-such-file.txt"])

    _, unchanged_work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch, stale=False)
    with pytest.raises(rel.ReleaseError, match="exact diff"):
        rel.apply_test_release_overlay(worktree_dir=Path(unchanged_work["worktree_dir"]), overlays=["scripts/nmbot_api_server.py"])

    _, mutated_work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    mutated_dir = Path(mutated_work["worktree_dir"])
    extra = mutated_dir / "source" / "nmbot_v2" / "runtime.py"
    extra.write_text(extra.read_text(encoding="utf-8") + "\n# unlisted mutation before overlay\n", encoding="utf-8")
    with pytest.raises(rel.ReleaseError, match="provenance before overlay"):
        rel.apply_test_release_overlay(worktree_dir=mutated_dir, overlays=["scripts/nmbot_api_server.py"])

    symlink_root = tmp_path / "symlink-root"
    (symlink_root / "scripts").mkdir(parents=True)
    (symlink_root / "scripts" / "nmbot_api_server.py").symlink_to(ROOT / "scripts" / "nmbot_api_server.py")
    monkeypatch.setattr(rel, "ROOT", symlink_root)
    with pytest.raises(rel.ReleaseError, match="safe regular file"):
        rel._validate_overlay_path_list(["scripts/nmbot_api_server.py"])


def test_test_release_overlay_hardened_replace_rejects_source_and_destination_symlinks(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    worktree_dir = Path(work["worktree_dir"])
    source_dir = worktree_dir / "source"
    data, expected = rel._read_overlay_source_no_follow("scripts/nmbot_api_server.py")
    destination = worktree_dir / "source" / "scripts" / "nmbot_api_server.py"
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside must not be followed')\n", encoding="utf-8")
    destination.unlink()
    destination.symlink_to(outside)

    with pytest.raises(rel.ReleaseError, match="destination is not regular"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel="scripts/nmbot_api_server.py", data=data, expected=expected)

    assert destination.is_symlink()
    assert outside.read_text(encoding="utf-8") == "print('outside must not be followed')\n"

    symlink_root = tmp_path / "symlink-source-root"
    _copy_contract_tree(symlink_root)
    (symlink_root / "scripts" / "nmbot_api_server.py").unlink()
    (symlink_root / "scripts" / "nmbot_api_server.py").symlink_to(ROOT / "scripts" / "nmbot_api_server.py")
    monkeypatch.setattr(rel, "ROOT", symlink_root)
    with pytest.raises(rel.ReleaseError, match="safe regular file"):
        rel._read_overlay_source_no_follow("scripts/nmbot_api_server.py")


def test_test_release_overlay_add_rejects_symlink_parent_and_destination(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {"prompts/v1/new_prompt.txt": b"safe prompt text\n"}))
    source_dir = Path(work["worktree_dir"]) / "source"
    data, expected = rel._read_overlay_source_no_follow("prompts/v1/new_prompt.txt")
    outside_dir = tmp_path / "outside-prompts"
    outside_dir.mkdir()
    if (source_dir / "prompts" / "v1").exists():
        __import__("shutil").rmtree(source_dir / "prompts" / "v1")
    (source_dir / "prompts" / "v1").symlink_to(outside_dir)

    with pytest.raises(rel.ReleaseError, match="directory component invalid"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel="prompts/v1/new_prompt.txt", data=data, expected=expected, must_exist=False)

    (source_dir / "prompts" / "v1").unlink()
    (source_dir / "prompts" / "v1").mkdir()
    outside_file = tmp_path / "outside-prompt.txt"
    outside_file.write_text("outside must not change\n", encoding="utf-8")
    (source_dir / "prompts" / "v1" / "new_prompt.txt").symlink_to(outside_file)

    with pytest.raises(rel.ReleaseError, match="already exists for add"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel="prompts/v1/new_prompt.txt", data=data, expected=expected, must_exist=False)
    assert outside_file.read_text(encoding="utf-8") == "outside must not change\n"


def test_test_release_overlay_add_rejects_race_created_destination(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {"nmbot_v1/new_module.py": b"VALUE = 'safe'\n"}))
    source_dir = Path(work["worktree_dir"]) / "source"
    data, expected = rel._read_overlay_source_no_follow("nmbot_v1/new_module.py")
    real_stat = rel.os.stat
    raced = False

    def stat_and_race(path, *args, **kwargs):
        nonlocal raced
        try:
            return real_stat(path, *args, **kwargs)
        except FileNotFoundError:
            if path == "new_module.py" and not raced:
                raced = True
                (source_dir / "nmbot_v1" / "new_module.py").write_text("raced\n", encoding="utf-8")
            raise

    monkeypatch.setattr(rel.os, "stat", stat_and_race)

    with pytest.raises(rel.ReleaseError, match="already exists for add"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel="nmbot_v1/new_module.py", data=data, expected=expected, must_exist=False)
    assert (source_dir / "nmbot_v1" / "new_module.py").read_text(encoding="utf-8") == "raced\n"


def test_test_release_overlay_add_write_failure_removes_owned_partial_file(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    relpath = "nmbot_v1/write_cleanup.py"
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {relpath: b"VALUE = 'write cleanup'\n"}))
    source_dir = Path(work["worktree_dir"]) / "source"
    data, expected = rel._read_overlay_source_no_follow(relpath)
    real_write = rel.os.write
    failed = False

    def partial_then_fail(fd: int, payload) -> int:
        nonlocal failed
        if not failed:
            failed = True
            return real_write(fd, payload[:3])
        raise OSError("write boom")

    monkeypatch.setattr(rel.os, "write", partial_then_fail)

    with pytest.raises(rel.ReleaseError, match="write failed"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel=relpath, data=data, expected=expected, must_exist=False)
    assert not (source_dir / relpath).exists()
    assert _overlay_temp_debris(source_dir, relpath) == []


def test_test_release_overlay_add_fsync_failure_removes_owned_file(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    relpath = "nmbot_v1/fsync_cleanup.py"
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {relpath: b"VALUE = 'fsync cleanup'\n"}))
    source_dir = Path(work["worktree_dir"]) / "source"
    data, expected = rel._read_overlay_source_no_follow(relpath)
    real_fsync = rel.os.fsync
    failed = False

    def fail_first_fsync(fd: int) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("fsync boom")
        real_fsync(fd)

    monkeypatch.setattr(rel.os, "fsync", fail_first_fsync)

    with pytest.raises(rel.ReleaseError, match="fsync failed"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel=relpath, data=data, expected=expected, must_exist=False)
    assert not (source_dir / relpath).exists()
    assert _overlay_temp_debris(source_dir, relpath) == []


def test_test_release_overlay_add_parent_fsync_failure_leaves_verified_final_without_temp(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    relpath = "nmbot_v1/parent_fsync_cleanup.py"
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {relpath: b"VALUE = 'parent fsync cleanup'\n"}))
    source_dir = Path(work["worktree_dir"]) / "source"
    data, expected = rel._read_overlay_source_no_follow(relpath)
    real_fsync = rel.os.fsync
    calls = 0

    def fail_second_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("parent fsync boom")
        real_fsync(fd)

    monkeypatch.setattr(rel.os, "fsync", fail_second_fsync)

    with pytest.raises(rel.ReleaseError, match="parent fsync failed after add"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel=relpath, data=data, expected=expected, must_exist=False)
    destination = source_dir / relpath
    assert destination.read_bytes() == data
    assert (destination.stat().st_mode & 0o777) == 0o644
    assert _overlay_temp_debris(source_dir, relpath) == []


def test_test_release_overlay_add_verification_read_failure_removes_owned_file(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    relpath = "nmbot_v1/read_cleanup.py"
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {relpath: b"VALUE = 'read cleanup'\n"}))
    source_dir = Path(work["worktree_dir"]) / "source"
    data, expected = rel._read_overlay_source_no_follow(relpath)

    def fail_verification_read(fd: int, size: int) -> bytes:
        raise OSError("read boom")

    monkeypatch.setattr(rel.os, "read", fail_verification_read)

    with pytest.raises(rel.ReleaseError, match="read failed after add"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel=relpath, data=data, expected=expected, must_exist=False)
    assert not (source_dir / relpath).exists()
    assert _overlay_temp_debris(source_dir, relpath) == []


def test_test_release_overlay_add_verification_hash_failure_removes_owned_file(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    relpath = "nmbot_v1/hash_cleanup.py"
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {relpath: b"VALUE = 'hash cleanup'\n"}))
    source_dir = Path(work["worktree_dir"]) / "source"
    data, expected = rel._read_overlay_source_no_follow(relpath)
    real_sha = rel._sha256_bytes
    calls = 0

    def bad_verify_sha(payload: bytes) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_sha(payload)
        return "0" * 64

    monkeypatch.setattr(rel, "_sha256_bytes", bad_verify_sha)

    with pytest.raises(rel.ReleaseError, match="hash mismatch after add"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel=relpath, data=data, expected=expected, must_exist=False)
    assert not (source_dir / relpath).exists()
    assert _overlay_temp_debris(source_dir, relpath) == []


def test_test_release_overlay_add_publish_race_preserves_foreign_final_and_removes_temp(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    relpath = "nmbot_v1/race_replace_cleanup.py"
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {relpath: b"VALUE = 'owned'\n"}))
    source_dir = Path(work["worktree_dir"]) / "source"
    destination = source_dir / relpath
    data, expected = rel._read_overlay_source_no_follow(relpath)
    real_link = rel.os.link
    replaced = False

    def link_race(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
        nonlocal replaced
        if not replaced:
            replaced = True
            destination.write_text("foreign replacement\n", encoding="utf-8")
            raise FileExistsError("publish race")
        return real_link(src, dst, dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(rel.os, "link", link_race)

    with pytest.raises(rel.ReleaseError, match="already exists for add"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel=relpath, data=data, expected=expected, must_exist=False)
    assert destination.read_text(encoding="utf-8") == "foreign replacement\n"
    assert _overlay_temp_debris(source_dir, relpath) == []


def test_test_release_overlay_add_publish_uses_verified_proc_fd_link(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    relpath = "nmbot_v1/proc_fd_publish.py"
    payload = b"VALUE = 'proc fd publish'\n"
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {relpath: payload}))
    source_dir = Path(work["worktree_dir"]) / "source"
    data, expected = rel._read_overlay_source_no_follow(relpath)
    real_link = rel.os.link
    real_open = rel.os.open
    calls: list[tuple[str, str, bool, int | None]] = []
    open_calls: list[tuple[str, int, int, int | None]] = []

    def record_link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
        calls.append((src, dst, follow_symlinks, src_dir_fd))
        return real_link(src, dst, dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks)

    def record_open(path, flags, mode=0o777, *, dir_fd=None):
        open_calls.append((str(path), flags, mode, dir_fd))
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(rel.os, "open", record_open)
    monkeypatch.setattr(rel.os, "link", record_link)

    rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel=relpath, data=data, expected=expected, must_exist=False)

    assert (source_dir / relpath).read_bytes() == payload
    assert len(calls) == 1
    src, dst, follow, src_dir_fd = calls[0]
    assert re.fullmatch(r"/proc/self/fd/\d+", src)
    assert dst == "proc_fd_publish.py"
    assert follow is True
    assert src_dir_fd is None
    unnamed_opens = [(path, flags, mode, dir_fd) for path, flags, mode, dir_fd in open_calls if path == "." and flags & rel.os.O_TMPFILE]
    assert len(unnamed_opens) == 1
    _, flags, mode, dir_fd = unnamed_opens[0]
    assert flags & rel.os.O_RDWR
    assert flags & rel.os.O_TMPFILE
    nofollow = getattr(rel.os, "O_NOFOLLOW", 0)
    assert (flags & nofollow) == nofollow
    assert mode == 0o644
    assert dir_fd is not None
    assert _overlay_temp_debris(source_dir, relpath) == []


def test_test_release_overlay_add_same_inode_mutation_before_publish_fails_final_hash(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    relpath = "nmbot_v1/same_inode_publish_race.py"
    payload = b"VALUE = 'verified before publish'\n"
    mutated_payload = b"VALUE = 'mutated before publish!'\n"
    assert len(mutated_payload) == len(payload)
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {relpath: payload}))
    source_dir = Path(work["worktree_dir"]) / "source"
    destination = source_dir / relpath
    foreign = source_dir / "nmbot_v1" / "foreign_must_survive.py"
    foreign.write_text("foreign file must not be deleted\n", encoding="utf-8")
    data, expected = rel._read_overlay_source_no_follow(relpath)
    real_link = rel.os.link
    mutated = False

    def mutate_same_inode_then_link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
        nonlocal mutated
        if not mutated:
            mutated = True
            assert re.fullmatch(r"/proc/self/fd/\d+", src)
            with open(src, "r+b", buffering=0) as fh:
                fh.seek(0)
                fh.write(mutated_payload)
                fh.truncate()
        return real_link(src, dst, dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(rel.os, "link", mutate_same_inode_then_link)

    with pytest.raises(rel.ReleaseError, match="hash mismatch after add publish"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel=relpath, data=data, expected=expected, must_exist=False)

    assert destination.read_bytes() == mutated_payload
    assert foreign.read_text(encoding="utf-8") == "foreign file must not be deleted\n"
    assert _overlay_temp_debris(source_dir, relpath) == []


def test_test_release_overlay_add_has_no_named_temp_mutation_seam(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    relpath = "nmbot_v1/no_named_temp_publish.py"
    payload = b"VALUE = 'verified bytes'\n"
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {relpath: payload}))
    source_dir = Path(work["worktree_dir"]) / "source"
    destination = source_dir / relpath
    data, expected = rel._read_overlay_source_no_follow(relpath)
    real_link = rel.os.link

    def assert_no_named_temp_then_link(src, dst, *, src_dir_fd=None, dst_dir_fd=None, follow_symlinks=True):
        assert _overlay_temp_debris(source_dir, relpath) == []
        assert re.fullmatch(r"/proc/self/fd/\d+", src)
        return real_link(src, dst, dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(rel.os, "link", assert_no_named_temp_then_link)

    rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel=relpath, data=data, expected=expected, must_exist=False)
    assert destination.read_bytes() == payload
    assert _overlay_temp_debris(source_dir, relpath) == []


def test_test_release_overlay_apply_rejects_overlay_hash_mismatch_after_helper_success(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    additions = {"nmbot_v1/apply_hash_guard.py": b"VALUE = 'expected overlay bytes'\n"}
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, additions))
    real_replace = rel._replace_overlay_dest_no_follow

    def replace_then_mutate(**kwargs):
        real_replace(**kwargs)
        target = Path(work["worktree_dir"]) / "source" / kwargs["rel"]
        target.write_text("VALUE = 'mutated after helper success'\n", encoding="utf-8")

    monkeypatch.setattr(rel, "_replace_overlay_dest_no_follow", replace_then_mutate)

    with pytest.raises(rel.ReleaseError, match="final hash/mode/size mismatch"):
        rel.apply_test_release_overlay(worktree_dir=Path(work["worktree_dir"]), overlays=["nmbot_v1/apply_hash_guard.py"])


def test_test_release_overlay_add_proc_fd_publish_failure_leaves_final_absent(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    relpath = "nmbot_v1/proc_fd_unavailable.py"
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {relpath: b"VALUE = 'proc unavailable'\n"}))
    source_dir = Path(work["worktree_dir"]) / "source"
    data, expected = rel._read_overlay_source_no_follow(relpath)
    real_stat = rel.os.stat

    def fail_proc_stat(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("/proc/self/fd/"):
            raise OSError("proc unavailable")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(rel.os, "stat", fail_proc_stat)

    with pytest.raises(rel.ReleaseError, match="proc fd unavailable"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel=relpath, data=data, expected=expected, must_exist=False)
    assert not (source_dir / relpath).exists()
    assert _overlay_temp_debris(source_dir, relpath) == []


def test_test_release_overlay_add_o_tmpfile_unavailable_fails_closed_before_publish(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    relpath = "nmbot_v1/otmpfile_unavailable.py"
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {relpath: b"VALUE = 'no named fallback'\n"}))
    source_dir = Path(work["worktree_dir"]) / "source"
    destination = source_dir / relpath
    data, expected = rel._read_overlay_source_no_follow(relpath)
    real_open = rel.os.open

    def fail_unnamed_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == "." and flags & rel.os.O_TMPFILE:
            raise OSError(rel.errno.EOPNOTSUPP, "O_TMPFILE unsupported")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(rel.os, "open", fail_unnamed_open)

    with pytest.raises(rel.ReleaseError, match="unnamed temp unavailable"):
        rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel=relpath, data=data, expected=expected, must_exist=False)
    assert not destination.exists()
    assert _overlay_temp_debris(source_dir, relpath) == []


def test_test_release_overlay_add_parent_mkdir_race_is_bounded_and_validated(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    relpath = "nmbot_v1/raced_parent/new_module.py"
    monkeypatch.setattr(rel, "ROOT", _local_overlay_root_with_additions(tmp_path, {relpath: b"VALUE = 'parent race'\n"}))
    source_dir = Path(work["worktree_dir"]) / "source"
    data, expected = rel._read_overlay_source_no_follow(relpath)
    real_mkdir = rel.os.mkdir
    raced = False

    def mkdir_race(path, mode=0o777, *, dir_fd=None):
        nonlocal raced
        if path == "raced_parent" and not raced:
            raced = True
            real_mkdir(path, mode, dir_fd=dir_fd)
            raise FileExistsError("mkdir race")
        return real_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(rel.os, "mkdir", mkdir_race)

    rel._replace_overlay_dest_no_follow(source_dir=source_dir, rel=relpath, data=data, expected=expected, must_exist=False)
    assert (source_dir / relpath).read_bytes() == data


def test_test_release_overlay_add_rejects_unsafe_non_runtime_and_secret_like_sources(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    local_root = _local_overlay_root_with_additions(
        tmp_path,
        {
            "tests/not_runtime.py": b"print('not runtime')\n",
            "nmbot_v1/secret_payload.py": b"API_TOKEN = 'super-secret-value'\n",
        },
    )
    monkeypatch.setattr(rel, "ROOT", local_root)

    with pytest.raises(rel.ReleaseError, match="safe runtime"):
        rel.apply_test_release_overlay(worktree_dir=Path(work["worktree_dir"]), overlays=["tests/not_runtime.py"])
    with pytest.raises(rel.ReleaseError, match="secret-like"):
        rel.apply_test_release_overlay(worktree_dir=Path(work["worktree_dir"]), overlays=["nmbot_v1/secret_payload.py"])


def test_test_release_overlay_hardened_replace_handles_partial_writes(tmp_path: Path, monkeypatch) -> None:
    _, work, _ = _prepared_api_worktree_with_stale_overlay(tmp_path, monkeypatch)
    source_dir = Path(work["worktree_dir"]) / "source"
    data, expected = rel._read_overlay_source_no_follow("scripts/nmbot_api_server.py")
    real_write = rel.os.write

    def partial_write(fd: int, payload) -> int:
        return real_write(fd, payload[: max(1, len(payload) // 2)])

    monkeypatch.setattr(rel.os, "write", partial_write)
    rel._replace_overlay_dest_no_follow(
        source_dir=source_dir,
        rel="scripts/nmbot_api_server.py",
        data=data,
        expected=expected,
    )

    destination = source_dir / "scripts" / "nmbot_api_server.py"
    assert destination.read_bytes() == data


def test_test_release_orchestration_order_and_snapshot_hash_passed_to_deploy(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    remote = object()
    artifact = rel.Artifact(archive=tmp_path / "nmbot-REL-flow.tar.gz", manifest=tmp_path / "nmbot-REL-flow.manifest.json", manifest_data={"release_id": "REL-flow", "archive_sha256": "a" * 64})
    artifact.archive.write_bytes(b"archive-placeholder")
    artifact.manifest.write_text('{"manifest":"placeholder"}\n', encoding="utf-8")

    def fake_snapshot(**kwargs):
        calls.append("snapshot")
        assert kwargs["remote"] is remote
        assert kwargs["contour"] == rel.DEFAULT_SNAPSHOT_CONTOUR
        return {"snapshot_id": "vps-source-test-flow", "snapshot_dir": str(tmp_path / "snap"), "manifest": str(tmp_path / "snap" / rel.SNAPSHOT_MANIFEST_NAME), "manifest_sha256": "1" * 64, "contour": rel.DEFAULT_SNAPSHOT_CONTOUR, "remote_root": rel.DEFAULT_REMOTE_ROOT, "files": 1}

    def fake_prepare(**kwargs):
        calls.append("prepare")
        assert str(kwargs["out_dir"]).endswith("worktrees")
        return {"worktree_dir": str(tmp_path / "work"), "snapshot_id": "vps-source-test-flow", "snapshot_manifest_sha256": "1" * 64, "source_tree_sha256": "2" * 64, "source_manifest_sha256": "3" * 64, "files": 1}

    def fake_overlay(**kwargs):
        calls.append("overlay")
        assert kwargs["overlays"] == ["scripts/nmbot_api_server.py"]
        return {"added": [], "missing": [], "changed": [{"path": "scripts/nmbot_api_server.py", "snapshot_sha256": "x", "worktree_sha256": "y"}], "overlay_paths": ["scripts/nmbot_api_server.py"]}

    def fake_build(**kwargs):
        calls.append("build")
        assert kwargs["release_id"] == "REL-flow"
        return artifact

    def fake_preflight(**kwargs):
        calls.append("preflight")
        return "preflight=ok release_id=REL-flow\n"

    def fake_deploy(**kwargs):
        calls.append("deploy")
        assert kwargs["source_snapshot_manifest_sha256"] == "1" * 64
        return "deploy=ok release_id=REL-flow\n"

    def fake_recon(remote_arg):
        calls.append("recon")
        payload = _valid_recon_payload()
        payload["current"]["target_name"] = "REL-flow"
        payload["identity"]["release_id"] = "REL-flow"
        return payload

    monkeypatch.setattr(rel, "snapshot_vps_source", fake_snapshot)
    monkeypatch.setattr(rel, "prepare_worktree", fake_prepare)
    monkeypatch.setattr(rel, "apply_test_release_overlay", fake_overlay)
    monkeypatch.setattr(rel, "build_from_worktree", fake_build)
    monkeypatch.setattr(rel, "local_preflight", fake_preflight)
    monkeypatch.setattr(rel, "deploy", fake_deploy)
    monkeypatch.setattr(rel, "recon", fake_recon)

    out_dir = Path("/tmp/opencode") / f"nmbot-test-release-flow-{tmp_path.parent.name}-{tmp_path.name}"
    if out_dir.exists() or out_dir.is_symlink():
        out_dir.unlink() if out_dir.is_symlink() else __import__("shutil").rmtree(out_dir)
    result = rel._test_release_with_remote(release_id="REL-flow", overlays=["scripts/nmbot_api_server.py"], out_dir=out_dir, confirm=True, remote=remote)

    assert calls == ["snapshot", "prepare", "overlay", "build", "preflight", "deploy", "recon"]
    assert result["release_id"] == "REL-flow"
    assert result["snapshot"]["manifest_sha256"] == "1" * 64
    assert result["current_target"] == "REL-flow"
    assert result["overlay_mode"] == "manual"
    assert result["selected_overlay_paths"] == ["scripts/nmbot_api_server.py"]


def test_test_release_auto_overlays_are_selected_after_its_fresh_snapshot(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    remote = object()
    artifact = rel.Artifact(archive=tmp_path / "archive.tar.gz", manifest=tmp_path / "manifest.json", manifest_data={"release_id": "REL-auto-flow", "archive_sha256": "a" * 64})
    artifact.archive.write_bytes(b"archive")
    artifact.manifest.write_text("{}\n", encoding="utf-8")
    snapshot = {"snapshot_id": "vps-source-auto", "snapshot_dir": str(tmp_path / "snap"), "manifest": str(tmp_path / "snap" / rel.SNAPSHOT_MANIFEST_NAME), "manifest_sha256": "1" * 64, "contour": rel.DEFAULT_SNAPSHOT_CONTOUR, "remote_root": rel.DEFAULT_REMOTE_ROOT, "files": 1}
    monkeypatch.setattr(rel, "snapshot_vps_source", lambda **kwargs: calls.append("snapshot") or snapshot)
    monkeypatch.setattr(rel, "_select_auto_test_release_overlay_paths", lambda **kwargs: calls.append("select") or ["scripts/nmbot_api_server.py"])
    monkeypatch.setattr(rel, "prepare_worktree", lambda **kwargs: calls.append("prepare") or {"worktree_dir": str(tmp_path / "work")})
    monkeypatch.setattr(rel, "apply_test_release_overlay", lambda **kwargs: calls.append("overlay") or {"added": [], "missing": [], "changed": [{"path": "scripts/nmbot_api_server.py"}], "overlay_paths": ["scripts/nmbot_api_server.py"]})
    monkeypatch.setattr(rel, "build_from_worktree", lambda **kwargs: calls.append("build") or artifact)
    monkeypatch.setattr(rel, "local_preflight", lambda **kwargs: calls.append("preflight") or "preflight=ok\n")
    monkeypatch.setattr(rel, "deploy", lambda **kwargs: calls.append("deploy") or "deploy=ok\n")
    payload = _valid_recon_payload()
    payload["current"]["target_name"] = "REL-auto-flow"
    payload["identity"]["release_id"] = "REL-auto-flow"
    monkeypatch.setattr(rel, "recon", lambda _: calls.append("recon") or payload)
    out_dir = Path("/tmp/opencode") / f"nmbot-test-release-auto-{tmp_path.name}"
    if out_dir.exists() or out_dir.is_symlink():
        out_dir.unlink() if out_dir.is_symlink() else __import__("shutil").rmtree(out_dir)

    result = rel._test_release_with_remote(release_id="REL-auto-flow", overlays=[], auto_overlays=True, out_dir=out_dir, confirm=True, remote=remote)

    assert calls == ["snapshot", "select", "prepare", "overlay", "build", "preflight", "deploy", "recon"]
    assert result["overlay_mode"] == "auto"
    assert result["selected_overlay_paths"] == ["scripts/nmbot_api_server.py"]


def test_test_release_first_failure_stops_later_steps(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(rel, "snapshot_vps_source", lambda **kwargs: calls.append("snapshot") or {"snapshot_id": "vps-source-test-flow", "snapshot_dir": str(tmp_path / "snap"), "manifest": str(tmp_path / "snap" / rel.SNAPSHOT_MANIFEST_NAME), "manifest_sha256": "1" * 64, "contour": rel.DEFAULT_SNAPSHOT_CONTOUR, "remote_root": rel.DEFAULT_REMOTE_ROOT, "files": 1})

    def fail_prepare(**kwargs):
        calls.append("prepare")
        raise rel.ReleaseError("prepare failed")

    monkeypatch.setattr(rel, "prepare_worktree", fail_prepare)
    monkeypatch.setattr(rel, "build_from_worktree", lambda **kwargs: calls.append("build"))
    monkeypatch.setattr(rel, "deploy", lambda **kwargs: calls.append("deploy"))
    monkeypatch.setattr(rel, "recon", lambda remote: calls.append("recon"))
    out_dir = Path("/tmp/opencode") / f"nmbot-test-release-stop-{tmp_path.parent.name}-{tmp_path.name}"
    if out_dir.exists() or out_dir.is_symlink():
        out_dir.unlink() if out_dir.is_symlink() else __import__("shutil").rmtree(out_dir)
    with pytest.raises(rel.ReleaseError, match="prepare failed"):
        rel._test_release_with_remote(release_id="REL-stop", overlays=["scripts/nmbot_api_server.py"], out_dir=out_dir, confirm=True, remote=object())
    assert calls == ["snapshot", "prepare"]


def test_test_release_unauthorized_or_missing_confirm_fails_before_remote_call(tmp_path: Path, monkeypatch) -> None:
    called = {"snapshot": False}
    monkeypatch.setattr(rel, "snapshot_vps_source", lambda **kwargs: called.__setitem__("snapshot", True))
    out = Path("/tmp/opencode") / f"nmbot-test-release-auth-{tmp_path.parent.name}-{tmp_path.name}"
    if out.exists() or out.is_symlink():
        out.unlink() if out.is_symlink() else __import__("shutil").rmtree(out)

    with pytest.raises(rel.ReleaseError, match="requires --confirm"):
        rel.test_release(release_id="REL-auth", overlays=["scripts/nmbot_api_server.py"], out_dir=out, confirm=False)
    assert called["snapshot"] is False

    with pytest.raises(rel.ReleaseError, match="not authorized"):
        rel.test_release(release_id="REL-auth", overlays=["scripts/nmbot_api_server.py"], out_dir=out, confirm=True, host="evil@example.com")
    assert called["snapshot"] is False

    with pytest.raises(TypeError):
        rel.test_release(release_id="REL-auth", overlays=["scripts/nmbot_api_server.py"], out_dir=out, confirm=True, remote=object())  # type: ignore[call-arg]
    assert called["snapshot"] is False

    rc = rel.main(["test-release", "--release-id", "REL-auth", "--overlay", "scripts/nmbot_api_server.py", "--host", "evil@example.com", "--confirm", "--out-dir", str(out)])
    assert rc == 1
    assert called["snapshot"] is False


def test_public_test_release_binds_authorized_ssh_remote(tmp_path: Path, monkeypatch) -> None:
    constructed: list[tuple[str, str]] = []
    helper_calls: list[object] = []

    class BoundRemote:
        def __init__(self, *, host: str, port: str) -> None:
            constructed.append((host, port))

    def fake_helper(**kwargs):
        helper_calls.append(kwargs["remote"])
        return {"ok": True, "release_id": kwargs["release_id"]}

    monkeypatch.setattr(rel, "SshRemote", BoundRemote)
    monkeypatch.setattr(rel, "_test_release_with_remote", fake_helper)
    out = Path("/tmp/opencode") / f"nmbot-test-release-public-{tmp_path.parent.name}-{tmp_path.name}"
    if out.exists() or out.is_symlink():
        out.unlink() if out.is_symlink() else __import__("shutil").rmtree(out)

    result = rel.test_release(release_id="REL-public", overlays=["scripts/nmbot_api_server.py"], out_dir=out, confirm=True)

    assert result == {"ok": True, "release_id": "REL-public"}
    assert constructed == [(rel.AUTHORIZED_DEPLOY_HOST, rel.AUTHORIZED_DEPLOY_PORT)]
    assert len(helper_calls) == 1 and isinstance(helper_calls[0], BoundRemote)

def test_capture_baseline_rejects_missing_symlink_secret_and_sanitizes_errors(tmp_path: Path) -> None:
    remote_root = tmp_path / "remote"
    _copy_contract_tree(remote_root)
    out_base = Path("/tmp/opencode") / f"nmbot-test-bad-{tmp_path.name}"

    target = remote_root / "scripts" / "nmbot_api_server.py"
    target.unlink()
    target.symlink_to("/tmp/evil.py")
    try:
        rel.capture_baseline(remote=FakeBinaryRemote(remote_root), out_dir=out_base / "symlink", release_id="baseline-bad-link")
    except rel.ReleaseError as exc:
        assert "safe regular file" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("symlink capture must fail")

    remote_root = tmp_path / "remote-secret"
    _copy_contract_tree(remote_root)
    (remote_root / "prompts" / "chat_v1.txt").write_text("API_SECRET_TOKEN=verysecretvalue123\n", encoding="utf-8")
    try:
        rel.capture_baseline(remote=FakeBinaryRemote(remote_root), out_dir=out_base / "secret", release_id="baseline-bad-secret")
    except rel.ReleaseError as exc:
        assert "secret-like content" in str(exc)
        assert "verysecretvalue123" not in str(exc)
    else:  # pragma: no cover
        raise AssertionError("secret-like capture must fail")

    try:
        rel.capture_baseline(remote=FakeBinaryRemote(remote_root, fail=True), out_dir=out_base / "error", release_id="baseline-bad-error")
    except rel.ReleaseError as exc:
        assert "super-secret-value" not in str(exc)
    else:  # pragma: no cover
        raise AssertionError("remote failure must fail")


def test_bootstrap_plan_writes_only_local_plan_unit_env_and_flags_false(tmp_path: Path) -> None:
    baseline = rel.build(release_id="baseline-plan", out_dir=tmp_path / "baseline")
    candidate = rel.build(release_id="candidate-plan", out_dir=tmp_path / "candidate")
    out = Path("/tmp/opencode") / "nmbot-test-bootstrap-plan"
    if out.exists():
        import shutil
        shutil.rmtree(out)

    plan = rel.bootstrap_plan(
        baseline_archive=baseline.archive,
        baseline_manifest=baseline.manifest,
        candidate_archive=candidate.archive,
        candidate_manifest=candidate.manifest,
        out_dir=out,
    )

    assert plan["remote_writes_performed"] is False
    assert plan["cutover_authorized"] is False
    assert plan["apply_script_generated"] is False
    assert ".env.client-production" not in json.dumps(plan, ensure_ascii=False)
    unit = (out / "novostroy-bot-api.service.candidate").read_text(encoding="utf-8")
    assert "WorkingDirectory=/home/neiro/novostroy-bot/current" in unit
    assert unit.count("EnvironmentFile=") == 1
    assert "Environment=" not in unit
    assert "PYTHONPATH" not in unit
    assert "ExecStartPre" not in unit
    assert "ExecStart=/usr/bin/python3 /home/neiro/novostroy-bot/current/scripts/nmbot_api_server.py" in unit
    env = (out / "nmbot_api_env_additions.nonsecret.env").read_text(encoding="utf-8")
    assert env.splitlines() == [
        "NMBOT_CONTOUR_PROFILE=api_production",
        "NMBOT_RELEASE_IDENTITY_FILE=/home/neiro/novostroy-bot/data/nmbot_release_identity.json",
        "NMBOT_RUNTIME_VERSION_FILE=/home/neiro/novostroy-bot/data/nmbot_runtime_version.json",
    ]
    assert "TOKEN" not in env and "SECRET" not in env and "PASSWORD" not in env
    assert not any(p.suffix == ".sh" for p in out.iterdir())


def test_bootstrap_plan_rejects_external_output_and_deploy_still_refuses_first_migration(tmp_path: Path) -> None:
    baseline = rel.build(release_id="baseline-reject", out_dir=tmp_path / "baseline")
    candidate = rel.build(release_id="candidate-reject", out_dir=tmp_path / "candidate")
    try:
        rel.bootstrap_plan(
            baseline_archive=baseline.archive,
            baseline_manifest=baseline.manifest,
            candidate_archive=candidate.archive,
            candidate_manifest=candidate.manifest,
            out_dir=tmp_path / "outside",
        )
    except rel.ReleaseError as exc:
        assert "bootstrap output directory" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("external bootstrap out dir must fail")

    fake = FakeRemote(previous="")
    try:
        rel.deploy(release_id="candidate-reject", archive=candidate.archive, manifest_path=candidate.manifest, confirm=True, remote=fake, remote_root="/remote")
    except rel.ReleaseError as exc:
        assert "current is not a release symlink" in str(exc) or "first migration is not implemented" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("first migration must still be refused by deploy")
    assert fake.uploads == []


def test_bootstrap_apply_requires_confirm_provenance_and_cli_has_no_candidate(tmp_path: Path) -> None:
    artifact = rel.build(release_id="baseline-apply-guard", out_dir=tmp_path / "baseline")
    manifest = _with_valid_source_provenance(artifact, tmp_path)
    fake = FakeRemote(remote_root=rel.DEFAULT_REMOTE_ROOT)

    try:
        rel.bootstrap_apply(release_id="baseline-apply-guard", archive=artifact.archive, manifest_path=manifest, confirm=False, remote=fake, source_snapshot_manifest_sha256="a" * 64)
    except rel.ReleaseError as exc:
        assert "requires --confirm" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("bootstrap-apply without confirm must fail")
    assert fake.commands == [] and fake.uploads == []

    absent = rel.build(release_id="baseline-no-prov", out_dir=tmp_path / "absent")
    try:
        rel.bootstrap_apply(release_id="baseline-no-prov", archive=absent.archive, manifest_path=absent.manifest, confirm=True, remote=fake, source_snapshot_manifest_sha256="a" * 64)
    except rel.ReleaseError as exc:
        assert "provenance" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("bootstrap-apply must require provenance")
    assert fake.commands == [] and fake.uploads == []

    args = rel.parse_args([
        "bootstrap-apply",
        "--release-id", "baseline-apply-guard",
        "--baseline-archive", str(artifact.archive),
        "--baseline-manifest", str(manifest),
        "--host", rel.AUTHORIZED_DEPLOY_HOST,
        "--source-snapshot-manifest-sha256", "a" * 64,
        "--confirm",
    ])
    assert args.command == "bootstrap-apply"
    assert not hasattr(args, "candidate_archive") and not hasattr(args, "candidate_manifest")


def test_bootstrap_apply_refuses_already_migrated_before_writes(tmp_path: Path) -> None:
    artifact = rel.build(release_id="baseline-already", out_dir=tmp_path)
    manifest = _with_valid_source_provenance(artifact, tmp_path)
    fake = FakeRemote(remote_root=rel.DEFAULT_REMOTE_ROOT, bootstrap_current_symlink=True)

    try:
        rel.bootstrap_apply(release_id="baseline-already", archive=artifact.archive, manifest_path=manifest, confirm=True, remote=fake, source_snapshot_manifest_sha256="a" * 64)
    except rel.ReleaseError as exc:
        assert "current is already" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("already migrated current symlink must fail")

    assert fake.uploads == []
    assert len(fake.commands) == 1
    assert not any(command.startswith("mkdir") or "os.replace" in command for command in fake.commands)


def test_bootstrap_apply_refuses_existing_current_directory_or_file_before_writes(tmp_path: Path) -> None:
    artifact = rel.build(release_id="baseline-current-exists", out_dir=tmp_path)
    manifest = _with_valid_source_provenance(artifact, tmp_path)

    for state in ("directory", "file"):
        fake = FakeRemote(remote_root=rel.DEFAULT_REMOTE_ROOT, bootstrap_current_state=state)
        try:
            rel.bootstrap_apply(release_id="baseline-current-exists", archive=artifact.archive, manifest_path=manifest, confirm=True, remote=fake, source_snapshot_manifest_sha256="a" * 64)
        except rel.ReleaseError as exc:
            assert "current path already exists" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"existing current {state} must fail before writes")

        assert fake.uploads == []
        assert len(fake.commands) == 1
        assert not any(command.startswith("mkdir") or "nmbot.bootstrap_backup.v1" in command or "os.replace" in command for command in fake.commands)


def test_bootstrap_rollback_proves_inactive_before_restore_and_cleans_only_exact_temp_link() -> None:
    command = rel._bootstrap_rollback_command(rel.DEFAULT_REMOTE_ROOT, "baseline-rollback-order", "/home/neiro/.config/systemd/user/novostroy-bot-api.service")

    stop = command.index('["systemctl","--user","stop","novostroy-bot-api.service"]')
    inactive = command.index('run(["systemctl","--user","stop","novostroy-bot-api.service"])\nprove_inactive()') + len('run(["systemctl","--user","stop","novostroy-bot-api.service"])\n')
    restore_unit = command.index('shutil.copy2(backup/"api-unit.service", unit)')
    tmp_path = command.index('tmp=pathlib.Path(cfg["tmp_current"])')
    tmp_cleanup = command.index('if tmp.is_symlink() and os.readlink(tmp) == "releases/"+cfg["rid"]: tmp.unlink()')
    reload = command.index('["systemctl","--user","daemon-reload"]')

    assert stop < inactive < restore_unit < tmp_path < tmp_cleanup < reload
    assert "api still active during bootstrap rollback" in command
    assert "shutil.rmtree" not in command and "rm -rf" not in command
    assert "releases/baseline-rollback-order" not in command


def test_bootstrap_apply_conflicting_env_rolls_back_and_keeps_secrets_out(tmp_path: Path) -> None:
    artifact = rel.build(release_id="baseline-env-conflict", out_dir=tmp_path)
    manifest = _with_valid_source_provenance(artifact, tmp_path)
    fake = FakeRemote(remote_root=rel.DEFAULT_REMOTE_ROOT, fail="conflicting existing bootstrap env assignment")

    try:
        rel.bootstrap_apply(release_id="baseline-env-conflict", archive=artifact.archive, manifest_path=manifest, confirm=True, remote=fake, source_snapshot_manifest_sha256="a" * 64)
    except rel.ReleaseError as exc:
        text = str(exc)
        assert "boom" in text
        assert "secret" not in text.lower() and "token" not in text.lower()
    else:  # pragma: no cover
        raise AssertionError("env conflict must fail and rollback")

    joined = "\n".join(fake.commands)
    assert "nmbot.bootstrap_backup.v1" in joined
    assert "root.env" in joined and "api-unit.service" in joined
    assert "rmdir /home/neiro/novostroy-bot/.release_lock" in joined
    assert len(fake.uploads) == 2


def test_bootstrap_guard_failures_refuse_before_lock_backup_or_upload(tmp_path: Path) -> None:
    artifact = rel.build(release_id="baseline-guard-fail", out_dir=tmp_path)
    manifest = _with_valid_source_provenance(artifact, tmp_path)
    cases = [
        "missing env names",
        "mode env mismatch",
        "canonical API env mismatch",
        "bridge env must not define API-owned required fields",
        "bootstrap env mismatch",
    ]
    for marker in cases:
        fake = FakeRemote(remote_root=rel.DEFAULT_REMOTE_ROOT, fail=marker)
        try:
            rel.bootstrap_apply(release_id="baseline-guard-fail", archive=artifact.archive, manifest_path=manifest, confirm=True, remote=fake, source_snapshot_manifest_sha256="a" * 64)
        except rel.ReleaseError as exc:
            assert "boom" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"bootstrap guard failure must fail before writes: {marker}")

        joined = "\n".join(fake.commands)
        assert fake.uploads == []
        assert "bootstrap_additions" in joined
        assert "mkdir /home/neiro/novostroy-bot/.release_lock" not in joined
        assert "nmbot.bootstrap_backup.v1" not in joined
        assert "conflicting existing bootstrap env assignment" not in joined
        assert "unit.name+\".bootstrap." not in joined
        assert "systemctl --user stop novostroy-bot-api.service" not in joined


def test_bootstrap_apply_happy_path_ordering_and_no_bridge_restart(tmp_path: Path) -> None:
    artifact = rel.build(release_id="baseline-happy", out_dir=tmp_path)
    manifest = _with_valid_source_provenance(artifact, tmp_path)
    fake = FakeRemote(remote_root=rel.DEFAULT_REMOTE_ROOT, migrated=False)

    out = rel.bootstrap_apply(release_id="baseline-happy", archive=artifact.archive, manifest_path=manifest, confirm=True, remote=fake, source_snapshot_manifest_sha256="a" * 64)
    joined = "\n".join(fake.commands)

    pre = next(i for i, c in enumerate(fake.commands) if "api unit FragmentPath" in c)
    guard = next(i for i, c in enumerate(fake.commands) if "bootstrap_additions" in c)
    lock = next(i for i, c in enumerate(fake.commands) if c == "mkdir /home/neiro/novostroy-bot/.release_lock")
    backup = next(i for i, c in enumerate(fake.commands) if "nmbot.bootstrap_backup.v1" in c)
    extract = next(i for i, c in enumerate(fake.commands) if "unexpected tar member" in c)
    symlink = next(i for i, c in enumerate(fake.commands) if "ln -sfn /home/neiro/novostroy-bot/.env" in c)
    preflight = next(i for i, c in enumerate(fake.commands) if "py_compile.compile" in c and "PYTHONDONTWRITEBYTECODE=1" in c)
    stop = next(i for i, c in enumerate(fake.commands) if c == "systemctl --user stop novostroy-bot-api.service")
    inactive = next(i for i, c in enumerate(fake.commands) if "is-active" in c)
    env_update = next(i for i, c in enumerate(fake.commands) if "conflicting existing bootstrap env assignment" in c)
    unit_replace = next(i for i, c in enumerate(fake.commands) if "unit.name+\".bootstrap." in c and "os.replace" in c)
    reload = next(i for i, c in enumerate(fake.commands) if c == "systemctl --user daemon-reload")
    switch = next(i for i, c in enumerate(fake.commands) if ".current.baseline-happy.tmp" in c)
    health = next(i for i, c in enumerate(fake.commands) if "nmbot_release_identity.py" in c and "baseline-happy" in c and "urllib.request" in c)
    final_unit = [i for i, c in enumerate(fake.commands) if c.startswith("systemctl --user show novostroy-bot-api.service")][-1]

    assert out.startswith("bootstrap-apply=ok release_id=baseline-happy")
    assert pre < guard < lock < backup < extract < symlink < preflight < stop < inactive < env_update < unit_replace < reload < switch < health < final_unit
    assert len(fake.uploads) == 2
    assert "prompts/candidates/v1_one_model_gpt55_experiment_v1.txt" in joined
    assert "prompts/candidates/v0_answer_writer_promptmaster_v10.txt" not in joined
    assert "systemctl --user restart novostroy-bot-n8n-bridge.service" not in joined
    assert "systemctl --user stop novostroy-bot-n8n-bridge.service" not in joined
    assert "systemctl --user start novostroy-bot-n8n-bridge.service" not in joined
    assert "ln -sfn /home/neiro/novostroy-bot/.env.client-production" not in joined


def test_bootstrap_apply_inactive_failure_does_not_mutate_config_current_or_identity(tmp_path: Path) -> None:
    artifact = rel.build(release_id="baseline-inactive-fail", out_dir=tmp_path)
    manifest = _with_valid_source_provenance(artifact, tmp_path)
    fake = FakeRemote(remote_root=rel.DEFAULT_REMOTE_ROOT, migrated=False, inactive_fail=True)

    try:
        rel.bootstrap_apply(release_id="baseline-inactive-fail", archive=artifact.archive, manifest_path=manifest, confirm=True, remote=fake, source_snapshot_manifest_sha256="a" * 64)
    except rel.ReleaseError as exc:
        assert "api still active" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("inactive proof failure must fail")

    joined = "\n".join(fake.commands)
    assert len(fake.uploads) == 2
    assert "systemctl --user stop novostroy-bot-api.service" in joined
    assert "is-active" in joined
    assert "conflicting existing bootstrap env assignment" not in joined
    assert "unit.name+\".bootstrap." not in joined
    assert "systemctl --user daemon-reload" not in joined
    assert ".current.baseline-inactive-fail.tmp" not in joined
    assert "data/nmbot_release_identity.json.baseline-inactive-fail.tmp" not in joined
    assert "nmbot.bootstrap_backup.v1" in joined
    assert joined.count("nmbot.bootstrap_backup.v1") == 1
    assert "rmdir /home/neiro/novostroy-bot/.release_lock" in joined


def test_bootstrap_apply_health_failure_rolls_back_and_rollback_failure_is_combined(tmp_path: Path) -> None:
    artifact = rel.build(release_id="baseline-health-fail", out_dir=tmp_path)
    manifest = _with_valid_source_provenance(artifact, tmp_path)
    fake = FakeRemote(remote_root=rel.DEFAULT_REMOTE_ROOT, migrated=False, fail="urllib.request")

    try:
        rel.bootstrap_apply(release_id="baseline-health-fail", archive=artifact.archive, manifest_path=manifest, confirm=True, remote=fake, source_snapshot_manifest_sha256="a" * 64)
    except rel.ReleaseError as exc:
        assert "boom" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("health failure must fail")
    joined = "\n".join(fake.commands)
    assert "nmbot.bootstrap_backup.v1" in joined
    assert "external-identity.json" in joined
    assert "rmdir /home/neiro/novostroy-bot/.release_lock" in joined

    failing_rollback = FakeRemote(remote_root=rel.DEFAULT_REMOTE_ROOT, migrated=False, fail="urllib.request", rollback_fail=True)
    try:
        rel.bootstrap_apply(release_id="baseline-health-fail", archive=artifact.archive, manifest_path=manifest, confirm=True, remote=failing_rollback, source_snapshot_manifest_sha256="a" * 64)
    except rel.ReleaseError as exc:
        text = str(exc)
        assert "bootstrap-apply failed" in text and "rollback failed" in text and "rollback-boom" in text
    else:  # pragma: no cover
        raise AssertionError("rollback failure must preserve bootstrap error")


def test_bridge_snapshot_first_migration_exact_allowlist(tmp_path: Path) -> None:
    source = tmp_path / "remote"
    _copy_bridge_sources(source)
    (source / "scripts" / "extra.py").write_text("print('extra')\n", encoding="utf-8")
    result = rel.snapshot_vps_bridge_source(remote=BridgeBinaryRemote(source), out_dir=Path("/tmp/opencode") / "nmbot-bridge-tests" / tmp_path.name / "snaps")
    manifest = rel.verify_bridge_snapshot_dir(Path(result["snapshot_dir"]))
    assert result["source_mode"] == "first_migration_canonical"
    assert [row["path"] for row in manifest["files"]] == list(rel.BRIDGE_ALLOWED_FILES)
    assert not (Path(result["snapshot_dir"]) / "source" / "scripts" / "extra.py").exists()


def test_bridge_snapshot_current_release_resolves_safe_symlink(tmp_path: Path) -> None:
    source = tmp_path / "remote"
    release_dir = source / rel.BRIDGE_RELEASES / "bridge-old-001"
    _copy_bridge_sources(release_dir)
    result = rel.snapshot_vps_bridge_source(remote=BridgeBinaryRemote(source, current_release="bridge-old-001"), out_dir=Path("/tmp/opencode") / "nmbot-bridge-tests" / tmp_path.name / "snaps")
    manifest = rel.verify_bridge_snapshot_dir(Path(result["snapshot_dir"]))
    assert manifest["source_mode"] == "bridge_current"
    assert manifest["active_release_id"] == "bridge-old-001"
    assert {row["source_scope"] for row in manifest["files"]} == {"bridge_current"}


def test_bridge_snapshot_rejects_symlink_attack(tmp_path: Path) -> None:
    source = tmp_path / "remote"
    _copy_bridge_sources(source)
    (source / rel.BRIDGE_ENTRYPOINT).unlink()
    (source / rel.BRIDGE_ENTRYPOINT).symlink_to(ROOT / rel.BRIDGE_ENTRYPOINT)
    with pytest.raises(rel.ReleaseError, match="remote binary command failed"):
        rel.snapshot_vps_bridge_source(remote=BridgeBinaryRemote(source), out_dir=Path("/tmp/opencode") / "nmbot-bridge-tests" / tmp_path.name / "snaps")


def test_bridge_prepare_build_preserves_snapshot_provenance_and_preflight(tmp_path: Path) -> None:
    artifact, snapshot_sha = _build_bridge_artifact(tmp_path)
    manifest = rel.load_bridge_manifest(artifact.manifest)
    assert manifest["scope"] == "bridge"
    assert manifest["service"] == rel.BRIDGE_SERVICE
    assert manifest["forbidden_services"] == [rel.API_SERVICE, rel.WORKER_SERVICE]
    assert manifest["source_provenance"]["source_snapshot_manifest_sha256"] == snapshot_sha
    assert rel.bridge_preflight(archive=artifact.archive, manifest_path=artifact.manifest).startswith("bridge-preflight=ok")


def test_bridge_preflight_rejects_archive_tampering(tmp_path: Path) -> None:
    artifact, _ = _build_bridge_artifact(tmp_path)
    artifact.archive.write_bytes(artifact.archive.read_bytes() + b"tamper")
    with pytest.raises(rel.ReleaseError, match="sha256"):
        rel.bridge_preflight(archive=artifact.archive, manifest_path=artifact.manifest)


def test_bridge_manifest_rejects_extra_file(tmp_path: Path) -> None:
    artifact, _ = _build_bridge_artifact(tmp_path)
    manifest = json.loads(artifact.manifest.read_text(encoding="utf-8"))
    manifest["files"].append({"path": "scripts/extra.py", "sha256": "0" * 64, "size": 1, "mode": 0o755})
    bad = tmp_path / "bad.manifest.json"
    bad.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(rel.ReleaseError, match="bridge manifest"):
        rel.load_bridge_manifest(bad)


def test_bridge_deploy_rejects_before_write_on_baseline_mismatch(tmp_path: Path) -> None:
    artifact, snapshot_sha = _build_bridge_artifact(tmp_path)
    remote = BridgeRemote(fail_guard=True)
    with pytest.raises(rel.ReleaseError, match="baseline hash mismatch"):
        rel.bridge_deploy(release_id="bridge-rel-001", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=remote, source_snapshot_manifest_sha256=snapshot_sha)
    assert remote.uploads == []
    assert not any("mkdir /home/neiro/novostroy-bot/.bridge_release_lock" in c for c in remote.commands)


def test_bridge_deploy_success_order_restarts_bridge_only(tmp_path: Path) -> None:
    artifact, snapshot_sha = _build_bridge_artifact(tmp_path)
    remote = BridgeRemote()
    result = rel.bridge_deploy(release_id="bridge-rel-001", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=remote, source_snapshot_manifest_sha256=snapshot_sha)
    joined = "\n".join(remote.commands)
    assert result == "bridge-deploy=ok release_id=bridge-rel-001\n"
    assert len(remote.uploads) == 2
    assert f"systemctl --user restart {rel.BRIDGE_SERVICE}" in joined
    assert f"systemctl --user restart {rel.API_SERVICE}" not in joined
    assert f"systemctl --user restart {rel.WORKER_SERVICE}" not in joined
    assert f"systemctl --user stop {rel.API_SERVICE}" not in joined
    assert f"systemctl --user stop {rel.WORKER_SERVICE}" not in joined
    assert joined.index(".bridge-current.bridge-rel-001.tmp") < joined.index("systemctl --user restart")


def test_bridge_deploy_rolls_back_after_health_failure(tmp_path: Path) -> None:
    artifact, snapshot_sha = _build_bridge_artifact(tmp_path)
    remote = BridgeRemote(fail_after="urllib.request")
    with pytest.raises(rel.ReleaseError):
        rel.bridge_deploy(release_id="bridge-rel-001", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=remote, source_snapshot_manifest_sha256=snapshot_sha)
    joined = "\n".join(remote.commands)
    assert "bridge-current.rollback.tmp" in joined
    assert f"restart {rel.BRIDGE_SERVICE}" in joined
    assert f"systemctl --user restart {rel.API_SERVICE}" not in joined
    assert f"systemctl --user restart {rel.WORKER_SERVICE}" not in joined
    assert f"systemctl --user stop {rel.API_SERVICE}" not in joined
    assert f"systemctl --user stop {rel.WORKER_SERVICE}" not in joined


def test_bridge_recon_validates_shape_and_does_not_print_secrets() -> None:
    remote = BridgeRemote()
    payload = rel.bridge_recon(remote)
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert payload["service"] == rel.BRIDGE_SERVICE
    assert "TOKEN=" not in rendered and "EnvironmentFile=" not in rendered
    assert rel.BRIDGE_INLINE_ENVIRONMENT not in rendered
    assert payload["systemd"]["inline_environment_expected"] is True
    assert "expected_inline_environment" not in payload["unit"]


def test_bridge_candidate_change_with_unchanged_live_snapshot_baseline_deploys(tmp_path: Path) -> None:
    artifact, snapshot_sha = _build_bridge_artifact_with_candidate_change(tmp_path)
    manifest = rel.load_bridge_manifest(artifact.manifest)
    baseline = {item["path"]: item["sha256"] for item in manifest["source_provenance"]["baseline_files"]}
    candidate = {item["path"]: item["sha256"] for item in manifest["files"]}
    assert baseline[rel.BRIDGE_ENTRYPOINT] != candidate[rel.BRIDGE_ENTRYPOINT]
    remote = BridgeRemote()
    out = rel.bridge_deploy(release_id="bridge-rel-001", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=remote, source_snapshot_manifest_sha256=snapshot_sha)
    assert out.startswith("bridge-deploy=ok")
    assert len(remote.uploads) == 2


def test_bridge_forged_or_mismatched_baseline_provenance_rejected_locally(tmp_path: Path) -> None:
    artifact, _ = _build_bridge_artifact(tmp_path)
    manifest = json.loads(artifact.manifest.read_text(encoding="utf-8"))
    manifest["source_provenance"]["baseline_files"][0]["sha256"] = "0" * 64
    bad = tmp_path / "forged.manifest.json"
    bad.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(rel.ReleaseError, match="baseline hash"):
        rel.load_bridge_manifest(bad)
    manifest = json.loads(artifact.manifest.read_text(encoding="utf-8"))
    manifest["source_provenance"]["baseline_files"][0]["path"] = "scripts/extra.py"
    bad.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(rel.ReleaseError, match="baseline"):
        rel.load_bridge_manifest(bad)


def test_bridge_unit_drift_rejects_before_upload_or_lock(tmp_path: Path) -> None:
    artifact, snapshot_sha = _build_bridge_artifact(tmp_path)
    for marker in (
        "bridge unit ExecStart mismatch",
        "bridge unit WorkingDirectory mismatch",
        "bridge unit EnvironmentFile mismatch",
        "bridge unit inline Environment mismatch",
        "bridge unit drop-in/fragment mismatch",
    ):
        remote = BridgeRemote(fail_after=marker)
        with pytest.raises(rel.ReleaseError, match="boom"):
            rel.bridge_deploy(release_id="bridge-rel-001", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=remote, source_snapshot_manifest_sha256=snapshot_sha)
        assert remote.uploads == []
        assert not any(c == "mkdir /home/neiro/novostroy-bot/.bridge_release_lock" for c in remote.commands)


def test_bridge_recon_ok_false_on_unhealthy_or_drift() -> None:
    payload = {
        "ok": False,
        "remote_root": rel.DEFAULT_REMOTE_ROOT,
        "service": rel.BRIDGE_SERVICE,
        "unit": {"fragment_path": rel.BRIDGE_UNIT_PATH, "environment_file": f"{rel.DEFAULT_REMOTE_ROOT}/.env", "expected_execstart": f"/usr/bin/python3 {rel.DEFAULT_REMOTE_ROOT}/{rel.BRIDGE_ENTRYPOINT} --host 0.0.0.0 --port 8093", "expected_working_directory": rel.DEFAULT_REMOTE_ROOT},
        "systemd": {"fragment_path_ok": True, "environment_file_canonical": True, "inline_environment_expected": True, "active": True, "main_pid_present": True, "execstart_expected": True, "working_directory_expected": True},
        "bridge_current": {"state": "absent", "target_name": "", "safe_release_symlink": True},
        "health": {"reachable": True, "ok": False},
        "active_manifest": {"exists": False, "schema_ok": False, "release_id": "", "tracked_hashes_match": False},
    }
    remote = BridgeRemote()
    remote.run = lambda command, input_text=None: subprocess.CompletedProcess([], 0, stdout=json.dumps(payload) + "\n", stderr="")  # type: ignore[method-assign]
    assert rel.bridge_recon(remote)["ok"] is False
    payload["ok"] = True
    with pytest.raises(rel.ReleaseError, match="ok/status"):
        rel.bridge_recon(remote)


def test_bridge_recon_inline_environment_boolean_participates_in_ok_contract() -> None:
    payload = {
        "ok": False,
        "remote_root": rel.DEFAULT_REMOTE_ROOT,
        "service": rel.BRIDGE_SERVICE,
        "unit": {"fragment_path": rel.BRIDGE_UNIT_PATH, "environment_file": f"{rel.DEFAULT_REMOTE_ROOT}/.env", "expected_execstart": f"/usr/bin/python3 {rel.DEFAULT_REMOTE_ROOT}/{rel.BRIDGE_ENTRYPOINT} --host 0.0.0.0 --port 8093", "expected_working_directory": rel.DEFAULT_REMOTE_ROOT},
        "systemd": {"fragment_path_ok": True, "environment_file_canonical": True, "inline_environment_expected": False, "active": True, "main_pid_present": True, "execstart_expected": True, "working_directory_expected": True},
        "bridge_current": {"state": "absent", "target_name": "", "safe_release_symlink": True},
        "health": {"reachable": True, "ok": True},
        "active_manifest": {"exists": False, "schema_ok": False, "release_id": "", "tracked_hashes_match": False},
    }
    remote = BridgeRemote()
    remote.run = lambda command, input_text=None: subprocess.CompletedProcess([], 0, stdout=json.dumps(payload) + "\n", stderr="")  # type: ignore[method-assign]
    assert rel.bridge_recon(remote)["ok"] is False
    payload["ok"] = True
    with pytest.raises(rel.ReleaseError, match="ok/status"):
        rel.bridge_recon(remote)


def test_bridge_rollback_verification_failures_are_reported(tmp_path: Path) -> None:
    artifact, snapshot_sha = _build_bridge_artifact(tmp_path)
    for marker in ("rollback health failed", "rollback current should be absent", "rollback current target mismatch", "rollback unit exec mismatch", "rollback unit inline env mismatch"):
        remote = BridgeRemote(fail_after="urllib.request")
        def run(command: str, *, input_text: str | None = None, marker=marker, original=remote.run):
            if marker in command:
                remote.commands.append(command)
                return subprocess.CompletedProcess([], 1, stdout="", stderr="rollback-proof-boom")
            return original(command, input_text=input_text)
        remote.run = run  # type: ignore[method-assign]
        with pytest.raises(rel.ReleaseError) as exc:
            rel.bridge_deploy(release_id="bridge-rel-001", archive=artifact.archive, manifest_path=artifact.manifest, confirm=True, remote=remote, source_snapshot_manifest_sha256=snapshot_sha)
        assert "rollback failed" in str(exc.value)


def test_bridge_rollback_command_verifies_previous_first_migration_and_migrated_state() -> None:
    command = rel._bridge_rollback_command(rel.DEFAULT_REMOTE_ROOT, "bridge-rel-001", rel.BRIDGE_UNIT_PATH)
    assert "rollback current should be absent" in command
    assert "rollback current target mismatch" in command
    assert "rollback unit working directory mismatch" in command
    assert "rollback unit inline env mismatch" in command
    assert "rollback unit exec mismatch" in command
    assert "rollback bridge is not active/running with pid" in command
    assert "rollback health failed" in command
    assert "previous_target" in command and "previous_exec_argv" in command


def test_bridge_remote_extract_still_verifies_candidate_hashes(tmp_path: Path) -> None:
    artifact, _ = _build_bridge_artifact_with_candidate_change(tmp_path)
    manifest = rel.load_bridge_manifest(artifact.manifest)
    command = rel._bridge_remote_extract_command("/tmp/staging", "/tmp/release", manifest)
    assert "bridge extracted hash mismatch" in command
    assert "bridge archive member set mismatch" in command


def test_bridge_backup_metadata_persists_exact_guard_fields_and_rejects_old_shape() -> None:
    old_shape = {"ok": True, "unit_path": rel.BRIDGE_UNIT_PATH, "previous_state": "absent", "previous_target": ""}
    with pytest.raises(rel.ReleaseError, match="guard state schema"):
        rel._bridge_backup_command(rel.DEFAULT_REMOTE_ROOT, "bridge-rel-001", rel.BRIDGE_UNIT_PATH, old_shape)
    first = {"ok": True, "unit_path": rel.BRIDGE_UNIT_PATH, "previous_state": "absent", "previous_target": "", "previous_working_directory": rel.DEFAULT_REMOTE_ROOT, "previous_exec_argv": ["/usr/bin/python3", f"{rel.DEFAULT_REMOTE_ROOT}/{rel.BRIDGE_ENTRYPOINT}", "--host", "0.0.0.0", "--port", "8093"], "previous_environment_file": f"{rel.DEFAULT_REMOTE_ROOT}/.env", "previous_inline_environment": rel.BRIDGE_INLINE_ENVIRONMENT, "previous_fragment_path": rel.BRIDGE_UNIT_PATH}
    migrated = {"ok": True, "unit_path": rel.BRIDGE_UNIT_PATH, "previous_state": "symlink", "previous_target": "bridge-releases/old-bridge-001", "previous_working_directory": f"{rel.DEFAULT_REMOTE_ROOT}/{rel.BRIDGE_CURRENT}", "previous_exec_argv": ["/usr/bin/python3", f"{rel.DEFAULT_REMOTE_ROOT}/{rel.BRIDGE_CURRENT}/{rel.BRIDGE_ENTRYPOINT}", "--host", "0.0.0.0", "--port", "8093"], "previous_environment_file": f"{rel.DEFAULT_REMOTE_ROOT}/.env", "previous_inline_environment": rel.BRIDGE_INLINE_ENVIRONMENT, "previous_fragment_path": rel.BRIDGE_UNIT_PATH}
    for state in (first, migrated):
        command = rel._bridge_backup_command(rel.DEFAULT_REMOTE_ROOT, "bridge-rel-001", rel.BRIDGE_UNIT_PATH, state)
        assert "previous_fragment_path" in command
        assert "previous_working_directory" in command
        assert "previous_environment_file" in command
        assert "previous_inline_environment" in command
        assert "previous_exec_argv" in command
        assert "bridge-unit.service" in command
        assert "TOKEN=" not in command and "SECRET=" not in command
    missing_inline = dict(first)
    missing_inline.pop("previous_inline_environment")
    with pytest.raises(rel.ReleaseError, match="guard state schema"):
        rel._bridge_backup_command(rel.DEFAULT_REMOTE_ROOT, "bridge-rel-001", rel.BRIDGE_UNIT_PATH, missing_inline)
    wrong_inline = dict(first)
    wrong_inline["previous_inline_environment"] = "PYTHONUNBUFFERED=0"
    with pytest.raises(rel.ReleaseError, match="guard state contract"):
        rel._bridge_backup_command(rel.DEFAULT_REMOTE_ROOT, "bridge-rel-001", rel.BRIDGE_UNIT_PATH, wrong_inline)


def test_bridge_rollback_rejects_missing_or_malformed_backup_metadata() -> None:
    command = rel._bridge_rollback_command(rel.DEFAULT_REMOTE_ROOT, "bridge-rel-001", rel.BRIDGE_UNIT_PATH)
    assert "bridge rollback metadata schema invalid" in command
    assert "bridge rollback metadata malformed" in command
    assert "previous_fragment_path" in command
    assert "previous_working_directory" in command
    assert "previous_environment_file" in command
    assert "previous_inline_environment" in command
    assert "previous_exec_argv" in command


def test_bridge_recon_execstart_exact_argv_rejects_extra_args() -> None:
    command = rel._readonly_bridge_recon_command()
    assert "argv_from_execstart(exec_start)==expected_argv" in command
    assert "expected_exec in exec_start" not in command
    payload = {
        "ok": False,
        "remote_root": rel.DEFAULT_REMOTE_ROOT,
        "service": rel.BRIDGE_SERVICE,
        "unit": {"fragment_path": rel.BRIDGE_UNIT_PATH, "environment_file": f"{rel.DEFAULT_REMOTE_ROOT}/.env", "expected_execstart": f"/usr/bin/python3 {rel.DEFAULT_REMOTE_ROOT}/{rel.BRIDGE_ENTRYPOINT} --host 0.0.0.0 --port 8093", "expected_working_directory": rel.DEFAULT_REMOTE_ROOT},
        "systemd": {"fragment_path_ok": True, "environment_file_canonical": True, "inline_environment_expected": True, "active": True, "main_pid_present": True, "execstart_expected": False, "working_directory_expected": True},
        "bridge_current": {"state": "absent", "target_name": "", "safe_release_symlink": True},
        "health": {"reachable": True, "ok": True},
        "active_manifest": {"exists": False, "schema_ok": False, "release_id": "", "tracked_hashes_match": False},
    }
    remote = BridgeRemote()
    remote.run = lambda command, input_text=None: subprocess.CompletedProcess([], 0, stdout=json.dumps(payload) + "\n", stderr="")  # type: ignore[method-assign]
    assert rel.bridge_recon(remote)["ok"] is False


def test_bridge_fixed_unit_template_owned_fields_and_no_secrets() -> None:
    command = rel._bridge_unit_replace_command(rel.BRIDGE_UNIT_PATH)
    assert f"WorkingDirectory={rel.DEFAULT_REMOTE_ROOT}/{rel.BRIDGE_CURRENT}" in command
    assert f"EnvironmentFile={rel.DEFAULT_REMOTE_ROOT}/.env" in command
    assert f"Environment={rel.BRIDGE_INLINE_ENVIRONMENT}" in command
    assert f"ExecStart=/usr/bin/python3 {rel.DEFAULT_REMOTE_ROOT}/{rel.BRIDGE_CURRENT}/{rel.BRIDGE_ENTRYPOINT} --host 0.0.0.0 --port 8093" in command
    assert "Restart=always" in command and "RestartSec=5" in command
    assert "TOKEN=" not in command and "SECRET=" not in command and "PASSWORD=" not in command


def test_bridge_generated_guard_requires_exact_inline_environment(tmp_path: Path) -> None:
    artifact, snapshot_sha = _build_bridge_artifact(tmp_path)
    manifest = rel.load_bridge_manifest(artifact.manifest)
    root = tmp_path / "remote"
    command = rel._bridge_remote_guard_command(str(root), manifest, snapshot_sha)
    ok = _run_bridge_guard_generated(command, tmp_path, root=root, working_directory=str(root), exec_script=str(root / rel.BRIDGE_ENTRYPOINT))
    assert ok.returncode == 0, ok.stderr + ok.stdout
    for inline in ("", "PYTHONUNBUFFERED=0", "PYTHONUNBUFFERED=1 OTHER=1"):
        bad = _run_bridge_guard_generated(command, tmp_path, root=root, working_directory=str(root), exec_script=str(root / rel.BRIDGE_ENTRYPOINT), inline_environment=inline)
        assert bad.returncode != 0
        assert "bridge unit inline Environment mismatch" in (bad.stdout + bad.stderr)


def test_bridge_systemd_environment_file_parser_accepts_only_known_exact_forms(tmp_path: Path) -> None:
    env = f"{rel.DEFAULT_REMOTE_ROOT}/.env"
    accepted = [
        env,
        f"  {env}   ",
        f"{env} (ignore_errors=no)",
        f"{{ path={env} ; ignore_errors=no ; }}",
        f"{{   path={env}   ;   ignore_errors=no   ;   }}",
    ]
    rejected = [
        "",
        f"{env} (ignore_errors=yes)",
        f"{{ path={env} ; ignore_errors=yes ; }}",
        f"{env} {env}",
        f"{env} EXTRA=1",
        f"{env} (ignore_errors=no) extra",
        f"/tmp/.env (ignore_errors=no)",
        f"{{ path=/tmp/.env ; ignore_errors=no ; }}",
    ]
    for raw in accepted:
        assert rel._systemd_env_file_is_exact(raw, env), raw
    for raw in rejected:
        assert not rel._systemd_env_file_is_exact(raw, env), raw
    artifact, snapshot_sha = _build_bridge_artifact(tmp_path)
    recon_command = rel._readonly_bridge_recon_command()
    guard_command = rel._bridge_remote_guard_command(rel.DEFAULT_REMOTE_ROOT, rel.load_bridge_manifest(artifact.manifest), snapshot_sha)
    assert "ignore_errors=no" in recon_command
    assert "ignore_errors=no" in guard_command


def test_bridge_recon_ok_true_for_observed_first_migration_env_metadata() -> None:
    payload = {
        "ok": True,
        "remote_root": rel.DEFAULT_REMOTE_ROOT,
        "service": rel.BRIDGE_SERVICE,
        "unit": {"fragment_path": rel.BRIDGE_UNIT_PATH, "environment_file": f"{rel.DEFAULT_REMOTE_ROOT}/.env", "expected_execstart": f"/usr/bin/python3 {rel.DEFAULT_REMOTE_ROOT}/{rel.BRIDGE_ENTRYPOINT} --host 0.0.0.0 --port 8093", "expected_working_directory": rel.DEFAULT_REMOTE_ROOT},
        "systemd": {"fragment_path_ok": True, "environment_file_canonical": True, "inline_environment_expected": True, "active": True, "main_pid_present": True, "execstart_expected": True, "working_directory_expected": True},
        "bridge_current": {"state": "absent", "target_name": "", "safe_release_symlink": True},
        "health": {"reachable": True, "ok": True},
        "active_manifest": {"exists": False, "schema_ok": False, "release_id": "", "tracked_hashes_match": False},
    }
    remote = BridgeRemote()
    remote.run = lambda command, input_text=None: subprocess.CompletedProcess([], 0, stdout=json.dumps(payload) + "\n", stderr="")  # type: ignore[method-assign]
    assert rel.bridge_recon(remote)["ok"] is True


def test_bridge_snapshot_observed_first_migration_uses_api_current_for_missing_egress(tmp_path: Path) -> None:
    source = tmp_path / "remote"
    _copy_bridge_sources(source)
    (source / "scripts" / "nmbot_egress_policy.py").unlink()
    api_release = source / "releases" / "api-rel-001"
    (api_release / "scripts").mkdir(parents=True)
    (api_release / "scripts" / "nmbot_egress_policy.py").write_bytes((ROOT / "scripts" / "nmbot_egress_policy.py").read_bytes())
    result = rel.snapshot_vps_bridge_source(remote=BridgeBinaryRemote(source, api_current_release="api-rel-001"), out_dir=Path("/tmp/opencode") / "nmbot-bridge-tests" / tmp_path.name / "snaps")
    manifest = rel.verify_bridge_snapshot_dir(Path(result["snapshot_dir"]))
    assert manifest["source_mode"] == "first_migration_mixed"
    assert manifest["api_current_release_id"] == "api-rel-001"
    scopes = {row["path"]: row["source_scope"] for row in manifest["files"]}
    assert scopes["scripts/nmbot_n8n_bridge_server.py"] == "bridge_canonical"
    assert scopes["scripts/dialogue_journal.py"] == "bridge_canonical"
    assert scopes["scripts/nmbot_egress_policy.py"] == "api_current"


def test_bridge_snapshot_first_migration_uses_canonical_egress_when_present(tmp_path: Path) -> None:
    source = tmp_path / "remote"
    _copy_bridge_sources(source)
    result = rel.snapshot_vps_bridge_source(remote=BridgeBinaryRemote(source, api_current_release="api-rel-ignored"), out_dir=Path("/tmp/opencode") / "nmbot-bridge-tests" / tmp_path.name / "snaps")
    manifest = rel.verify_bridge_snapshot_dir(Path(result["snapshot_dir"]))
    assert manifest["source_mode"] == "first_migration_canonical"
    assert manifest["api_current_release_id"] == ""
    assert {row["source_scope"] for row in manifest["files"]} == {"bridge_canonical"}


def test_bridge_snapshot_rejects_missing_api_current_and_no_fallback_for_bridge_or_journal(tmp_path: Path) -> None:
    source = tmp_path / "remote"
    _copy_bridge_sources(source)
    (source / "scripts" / "nmbot_egress_policy.py").unlink()
    with pytest.raises(rel.ReleaseError):
        rel.snapshot_vps_bridge_source(remote=BridgeBinaryRemote(source), out_dir=Path("/tmp/opencode") / "nmbot-bridge-tests" / tmp_path.name / "missing-api")
    source2 = tmp_path / "remote2"
    _copy_bridge_sources(source2)
    (source2 / rel.BRIDGE_ENTRYPOINT).unlink()
    api_release = source2 / "releases" / "api-rel-001"
    _copy_bridge_sources(api_release)
    with pytest.raises(rel.ReleaseError):
        rel.snapshot_vps_bridge_source(remote=BridgeBinaryRemote(source2, api_current_release="api-rel-001"), out_dir=Path("/tmp/opencode") / "nmbot-bridge-tests" / tmp_path.name / "missing-bridge")


def test_bridge_deploy_guard_uses_recorded_api_current_release_id(tmp_path: Path) -> None:
    source = tmp_path / "remote-mixed"
    _copy_bridge_sources(source)
    (source / "scripts" / "nmbot_egress_policy.py").unlink()
    api_release = source / "releases" / "api-rel-001"
    (api_release / "scripts").mkdir(parents=True)
    (api_release / "scripts" / "nmbot_egress_policy.py").write_bytes((ROOT / "scripts" / "nmbot_egress_policy.py").read_bytes())
    base = Path("/tmp/opencode") / "nmbot-bridge-tests" / (tmp_path.name + "-mixed-deploy")
    snap = rel.snapshot_vps_bridge_source(remote=BridgeBinaryRemote(source, api_current_release="api-rel-001"), out_dir=base / "snaps")
    work = rel.prepare_bridge_worktree(snapshot_dir=Path(snap["snapshot_dir"]), out_dir=base / "work")
    artifact = rel.build_bridge_from_worktree(worktree_dir=Path(work["worktree_dir"]), release_id="bridge-rel-mixed", out_dir=tmp_path / "out-mixed")
    loaded = rel.load_bridge_manifest(artifact.manifest)
    snapshot_sha = snap["manifest_sha256"]
    command = rel._bridge_remote_guard_command(rel.DEFAULT_REMOTE_ROOT, loaded, snapshot_sha)
    assert "api current release id mismatch" in command
    assert "api_current baseline invalid" in command


def test_bridge_origin_forgery_rejected_by_provenance_validation(tmp_path: Path) -> None:
    artifact, _ = _build_bridge_artifact(tmp_path)
    manifest = json.loads(artifact.manifest.read_text(encoding="utf-8"))
    manifest["source_provenance"]["baseline_files"][0]["source_scope"] = "api_current"
    manifest["source_provenance"]["baseline_files_sha256"] = rel._bridge_file_rows_hash(manifest["source_provenance"]["baseline_files"])
    bad = tmp_path / "origin-forged.manifest.json"
    bad.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(rel.ReleaseError, match="provenance"):
        rel.load_bridge_manifest(bad)


def test_bridge_generated_guard_accepts_mixed_first_migration_and_rejects_stale_mode(tmp_path: Path) -> None:
    root = tmp_path / "guard-root"
    _copy_bridge_sources(root)
    (root / "scripts" / "nmbot_egress_policy.py").unlink()
    api_release = root / "releases" / "api-rel-001"
    (api_release / "scripts").mkdir(parents=True)
    (api_release / "scripts" / "nmbot_egress_policy.py").write_bytes((ROOT / "scripts" / "nmbot_egress_policy.py").read_bytes())
    (root / "current").symlink_to("releases/api-rel-001")
    base = Path("/tmp/opencode") / "nmbot-bridge-tests" / (tmp_path.name + "-guard-mixed")
    snap = rel.snapshot_vps_bridge_source(remote=BridgeBinaryRemote(root, api_current_release="api-rel-001"), out_dir=base / "snaps")
    work = rel.prepare_bridge_worktree(snapshot_dir=Path(snap["snapshot_dir"]), out_dir=base / "work")
    artifact = rel.build_bridge_from_worktree(worktree_dir=Path(work["worktree_dir"]), release_id="bridge-rel-mixed", out_dir=tmp_path / "out-guard-mixed")
    manifest = rel.load_bridge_manifest(artifact.manifest)
    command = rel._bridge_remote_guard_command(str(root), manifest, snap["manifest_sha256"])
    ok = _run_bridge_guard_generated(command, tmp_path, root=root, working_directory=str(root), exec_script=str(root / rel.BRIDGE_ENTRYPOINT))
    assert ok.returncode == 0, ok.stderr + ok.stdout
    stale = json.loads(artifact.manifest.read_text(encoding="utf-8"))
    stale["source_provenance"]["source_mode"] = "current_release"
    command = rel._bridge_remote_guard_command(str(root), stale, snap["manifest_sha256"])
    bad = _run_bridge_guard_generated(command, tmp_path, root=root, working_directory=str(root), exec_script=str(root / rel.BRIDGE_ENTRYPOINT))
    assert bad.returncode != 0
    assert "bridge first migration provenance mismatch" in (bad.stdout + bad.stderr)


def test_bridge_generated_guard_accepts_migrated_bridge_current_and_rejects_stale_mode(tmp_path: Path) -> None:
    root = tmp_path / "guard-root-current"
    release_dir = root / rel.BRIDGE_RELEASES / "bridge-old-001"
    _copy_bridge_sources(release_dir)
    (root / rel.BRIDGE_CURRENT).parent.mkdir(parents=True, exist_ok=True)
    (root / rel.BRIDGE_CURRENT).symlink_to(f"{rel.BRIDGE_RELEASES}/bridge-old-001")
    base = Path("/tmp/opencode") / "nmbot-bridge-tests" / (tmp_path.name + "-guard-current")
    snap = rel.snapshot_vps_bridge_source(remote=BridgeBinaryRemote(root, current_release="bridge-old-001"), out_dir=base / "snaps")
    work = rel.prepare_bridge_worktree(snapshot_dir=Path(snap["snapshot_dir"]), out_dir=base / "work")
    artifact = rel.build_bridge_from_worktree(worktree_dir=Path(work["worktree_dir"]), release_id="bridge-rel-current", out_dir=tmp_path / "out-guard-current")
    manifest = rel.load_bridge_manifest(artifact.manifest)
    command = rel._bridge_remote_guard_command(str(root), manifest, snap["manifest_sha256"])
    ok = _run_bridge_guard_generated(command, tmp_path, root=root, working_directory=str(root / rel.BRIDGE_CURRENT), exec_script=str(root / rel.BRIDGE_CURRENT / rel.BRIDGE_ENTRYPOINT))
    assert ok.returncode == 0, ok.stderr + ok.stdout
    stale = json.loads(artifact.manifest.read_text(encoding="utf-8"))
    stale["source_provenance"]["source_mode"] = "current_release"
    command = rel._bridge_remote_guard_command(str(root), stale, snap["manifest_sha256"])
    bad = _run_bridge_guard_generated(command, tmp_path, root=root, working_directory=str(root / rel.BRIDGE_CURRENT), exec_script=str(root / rel.BRIDGE_CURRENT / rel.BRIDGE_ENTRYPOINT))
    assert bad.returncode != 0
    assert "bridge-current provenance mismatch" in (bad.stdout + bad.stderr)
