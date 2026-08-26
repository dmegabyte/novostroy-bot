#!/usr/bin/env python3
"""Deploy one immutable V6 API release to an isolated, loopback-only TEST.

This owner deliberately has no bridge, connected Jivo ingress, CRM, n8n, public
listener or PROD route.  Remote mutations require an exact confirmation.  The
first rollback is simply stopping the isolated transient unit; existing live
services are checked before and after every start/stop operation and are never
restarted here.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from scripts.nmbot_release_control import InspectedArtifact, inspect_artifact
    from scripts.nmbot_release_registry import validate_git_sha, validate_release_id
except ImportError:  # direct scripts/ execution
    from nmbot_release_control import InspectedArtifact, inspect_artifact
    from nmbot_release_registry import validate_git_sha, validate_release_id


SCHEMA = "nmbot.v6_isolated_test_api.v1"
HOST = "neiro@193.107.155.236"
SSH_PORT = "1905"
TEST_ROOT = "/home/neiro/.local/state/nmbot-v6-clean-test"
TEST_UNIT = "nmbot-v6-clean-test.service"
TEST_PORT = 18088
PRIMARY_ENV_FILE = "/home/neiro/novostroy-bot/.env"
PROTECTED_ROOTS = (
    "/home/neiro/novostroy-bot",
    "/home/neiro/novostroy-bot-client-production",
)
PROTECTED_SERVICES = (
    "novostroy-bot-api.service",
    "novostroy-bot-n8n-bridge.service",
    "novostroy-bot-client-production-api.service",
    "novostroy-bot-client-production-n8n-bridge.service",
)
PROTECTED_HEALTH = (
    "http://127.0.0.1:8088/health",
    "http://127.0.0.1:8093/health",
    "http://127.0.0.1:8188/health",
    "http://127.0.0.1:8193/health",
)
COPIED_ENV_KEYS = frozenset(
    {
        "OVERMIND_URL",
        "OVERMIND_TOKEN",
        "GATEWAY_POLL_TOKEN",
        "NMBOT_OPENROUTER_EXCLUDE_REASONING",
        "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED",
        "NMBOT_MAIN_SEARCH_FALLBACK_MODELS",
    }
)
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=8")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


class IsolatedTestError(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetContract:
    host: str = HOST
    ssh_port: str = SSH_PORT
    root: str = TEST_ROOT
    unit: str = TEST_UNIT
    api_port: int = TEST_PORT

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "target_kind": "isolated_api_test",
            "host": self.host,
            "ssh_port": self.ssh_port,
            "root": self.root,
            "unit": self.unit,
            "api_port": self.api_port,
            "bind": "127.0.0.1",
            "profile": "TEST",
            "bridge": "absent",
            "jivo_ingress": "disconnected",
            "crm_delivery": "disabled",
            "rollback": "stop_isolated_unit",
            "protected_roots": list(PROTECTED_ROOTS),
            "protected_services": list(PROTECTED_SERVICES),
        }


REMOTE_PROGRAM = r'''
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath

cfg = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))

EXPECTED = {
    "schema": "nmbot.v6_isolated_test_api.v1",
    "root": "/home/neiro/.local/state/nmbot-v6-clean-test",
    "unit": "nmbot-v6-clean-test.service",
    "port": 18088,
    "primary_env": "/home/neiro/novostroy-bot/.env",
    "protected_services": [
        "novostroy-bot-api.service",
        "novostroy-bot-n8n-bridge.service",
        "novostroy-bot-client-production-api.service",
        "novostroy-bot-client-production-n8n-bridge.service",
    ],
    "protected_health": [
        "http://127.0.0.1:8088/health",
        "http://127.0.0.1:8093/health",
        "http://127.0.0.1:8188/health",
        "http://127.0.0.1:8193/health",
    ],
}
SAFE_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SAFE_SHA = re.compile(r"^[0-9a-f]{64}$")
COPIED_KEYS = {
    "OVERMIND_URL",
    "OVERMIND_TOKEN",
    "GATEWAY_POLL_TOKEN",
    "NMBOT_OPENROUTER_EXCLUDE_REASONING",
    "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED",
    "NMBOT_MAIN_SEARCH_FALLBACK_MODELS",
}

def emit(payload, code=0):
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    raise SystemExit(code)

def fail(reason):
    emit({"ok": False, "schema": EXPECTED["schema"], "error": reason}, 2)

if not isinstance(cfg, dict) or cfg.get("schema") != EXPECTED["schema"]:
    fail("payload_schema_mismatch")
for key in ("root", "unit", "port", "primary_env", "protected_services", "protected_health"):
    if cfg.get(key) != EXPECTED[key]:
        fail("target_contract_mismatch")
operation = cfg.get("operation")
if operation not in {"preflight", "prepare_upload", "activate", "recon", "stop"}:
    fail("operation_not_allowed")

root = Path(EXPECTED["root"])
unit = EXPECTED["unit"]
port = EXPECTED["port"]

def run(args, timeout=30):
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    except Exception:
        fail("remote_command_failed")

def unit_state(name):
    result = run([
        "systemctl", "--user", "show", name,
        "--property=LoadState", "--property=ActiveState", "--property=SubState",
        "--property=MainPID", "--property=ActiveEnterTimestampMonotonic",
    ])
    values = {}
    for raw in result.stdout.splitlines():
        if "=" in raw:
            key, value = raw.split("=", 1)
            values[key] = value
    return {
        "loaded": values.get("LoadState") == "loaded",
        "active": values.get("ActiveState") == "active",
        "running": values.get("SubState") == "running",
        "pid": values.get("MainPID") or "0",
        "active_since": values.get("ActiveEnterTimestampMonotonic") or "0",
    }

def get_health(url, expected_release=None):
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read(65536).decode("utf-8"))
            status = response.status
    except Exception:
        return {"ok": False}
    result = {
        "ok": status == 200 and isinstance(payload, dict) and payload.get("ok") is True,
        "runtime": payload.get("runtime") if isinstance(payload, dict) else None,
        "profile": payload.get("profile") if isinstance(payload, dict) else None,
        "release_id": payload.get("release_id") if isinstance(payload, dict) else None,
    }
    if expected_release is not None:
        result["identity_ok"] = (
            result["ok"]
            and result["runtime"] == "V6"
            and result["profile"] == "TEST"
            and result["release_id"] == expected_release
        )
    return result

def protected_snapshot():
    services = {name: unit_state(name) for name in EXPECTED["protected_services"]}
    health = {url: get_health(url)["ok"] for url in EXPECTED["protected_health"]}
    healthy = all(item["loaded"] and item["active"] and item["running"] and item["pid"] != "0" for item in services.values()) and all(health.values())
    return {"healthy": healthy, "services": services, "health": health}

def same_protected(before, after):
    if not before.get("healthy") or not after.get("healthy"):
        return False
    for name in EXPECTED["protected_services"]:
        left, right = before["services"][name], after["services"][name]
        if left["pid"] != right["pid"] or left["active_since"] != right["active_since"]:
            return False
    return True

def safe_existing_directory(path, label):
    if not path.exists():
        return
    try:
        mode = path.lstat().st_mode
    except OSError:
        fail(label + "_invalid")
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode) or path.stat().st_uid != os.getuid():
        fail(label + "_invalid")

def require_fixed_parents():
    for path in (Path("/home/neiro"), Path("/home/neiro/.local"), Path("/home/neiro/.local/state")):
        safe_existing_directory(path, "test_parent")
        if not path.exists():
            fail("test_parent_missing")

def ensure_private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe_existing_directory(path, "test_directory")
    path.chmod(0o700)

def port_is_free():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()

def base_preflight(require_free_port=True):
    protected = protected_snapshot()
    if not protected["healthy"]:
        fail("protected_service_unhealthy")
    require_fixed_parents()
    safe_existing_directory(root, "test_root")
    test_state = unit_state(unit)
    if test_state["active"]:
        fail("isolated_test_already_active")
    if require_free_port and not port_is_free():
        fail("isolated_test_port_in_use")
    return protected

def safe_relative(value):
    raw = str(value or "").replace(os.sep, "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or str(path) != raw:
        fail("artifact_path_unsafe")
    return raw

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def write_atomic(path, data, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def manifest_contract(manifest_path, archive_path, release_id):
    if not manifest_path.is_file() or manifest_path.is_symlink() or not archive_path.is_file() or archive_path.is_symlink():
        fail("artifact_upload_missing")
    if sha256_file(manifest_path) != cfg.get("manifest_sha256") or sha256_file(archive_path) != cfg.get("archive_sha256"):
        fail("artifact_upload_hash_mismatch")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        fail("artifact_manifest_malformed")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "nmbot.atomic_release.v1" or manifest.get("scope") != "api" or manifest.get("profile") != "v6-only" or manifest.get("release_id") != release_id:
        fail("artifact_manifest_contract_mismatch")
    if manifest.get("archive_sha256") != cfg.get("archive_sha256"):
        fail("artifact_archive_identity_mismatch")
    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows:
        fail("artifact_file_list_invalid")
    expected = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            fail("artifact_file_row_invalid")
        relative = safe_relative(row.get("path"))
        digest = str(row.get("sha256") or "")
        if relative in expected or not SAFE_SHA.fullmatch(digest):
            fail("artifact_file_row_invalid")
        expected[relative] = digest
    return expected

def extract_release(archive_path, release_dir, expected):
    releases = release_dir.parent
    ensure_private_directory(releases)
    if release_dir.exists():
        fail("release_already_exists")
    temporary = Path(tempfile.mkdtemp(prefix="." + release_dir.name + ".", dir=str(releases)))
    try:
        with tarfile.open(archive_path, "r:gz") as bundle:
            members = bundle.getmembers()
            if len(members) != len(expected):
                fail("artifact_member_set_mismatch")
            seen = set()
            for member in members:
                relative = safe_relative(member.name)
                if relative in seen or relative not in expected or not member.isfile() or member.issym() or member.islnk():
                    fail("artifact_member_unsafe")
                source = bundle.extractfile(member)
                if source is None:
                    fail("artifact_member_unreadable")
                payload = source.read(10 * 1024 * 1024 + 1)
                if len(payload) != member.size or len(payload) > 10 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != expected[relative]:
                    fail("artifact_member_hash_mismatch")
                target = temporary / relative
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                target.write_bytes(payload)
                target.chmod(0o755 if relative.startswith("scripts/") and relative.endswith(".py") else 0o600 if relative.startswith("release_identity/") else 0o644)
                seen.add(relative)
        if seen != set(expected):
            fail("artifact_member_set_mismatch")
        os.replace(temporary, release_dir)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)

def projected_environment(release_id, release_dir):
    source = Path(EXPECTED["primary_env"])
    if not source.is_file() or source.is_symlink():
        fail("gateway_source_env_missing")
    selected = {}
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except Exception:
        fail("gateway_source_env_unreadable")
    for raw in lines:
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in COPIED_KEYS:
            selected[key] = value
    if not (selected.get("OVERMIND_TOKEN", "").strip() or selected.get("GATEWAY_POLL_TOKEN", "").strip()):
        fail("gateway_token_missing")
    data_root = root / "data" / release_id
    fixed = {
        "NMBOT_CONTOUR_PROFILE": "TEST",
        "NMBOT_API_HOST": "127.0.0.1",
        "NMBOT_API_PORT": str(port),
        "NMBOT_API_STATE_FILE": str(data_root / "nmbot_api_state.json"),
        "NMBOT_RUNTIME_VERSION_FILE": str(data_root / "nmbot_runtime_version.json"),
        "NMBOT_CALLBACK_OUTBOX_DIR": str(data_root / "crm_callback_outbox"),
        "NMBOT_DIALOGUE_JOURNAL": str(data_root / "dialogue" / "dialogue.jsonl"),
        "NMBOT_LOGS_DIR": str(data_root / "logs"),
        "NMBOT_RELEASE_IDENTITY_FILE": str(release_dir / "release_identity" / "nmbot_release_identity.json"),
        "NMBOT_API_TOKEN": "",
        "NMBOT_N8N_BRIDGE_TOKEN": "",
        "JIVO_PROVIDER_ID": "",
        "JIVO_PROVIDER_TOKEN": "",
        "NMBOT_CALLBACK_CRM_CONTROL_FILE": "",
        "NMBOT_CALLBACK_CRM_ENDPOINT": "",
        "NMBOT_GATEWAY_FORENSIC_LOG_ENABLED": "0",
        "PYTHONPATH": str(release_dir) + ":" + str(release_dir / "scripts"),
    }
    payload_lines = [key + "=" + selected[key] for key in sorted(selected)]
    payload_lines.extend(key + "=" + value for key, value in sorted(fixed.items()))
    env_path = root / "config" / (release_id + ".env")
    if env_path.exists():
        fail("test_env_already_exists")
    write_atomic(env_path, ("\n".join(payload_lines) + "\n").encode("utf-8"), 0o600)
    return env_path

def wait_test_health(release_id, timeout=30):
    deadline = time.monotonic() + timeout
    last = {"ok": False}
    while time.monotonic() < deadline:
        last = get_health("http://127.0.0.1:" + str(port) + "/health", expected_release=release_id)
        if last.get("identity_ok") is True:
            return last
        time.sleep(0.2)
    return last

def stop_test_unit():
    run(["systemctl", "--user", "stop", unit])
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not unit_state(unit)["active"]:
            return
        time.sleep(0.1)
    fail("isolated_test_stop_timeout")

if operation == "preflight":
    protected = base_preflight()
    emit({"ok": True, "schema": EXPECTED["schema"], "operation": operation, "target": {"root": str(root), "unit": unit, "port": port}, "protected_services_healthy": protected["healthy"], "mutation": False})

if operation == "recon":
    protected = protected_snapshot()
    state = unit_state(unit)
    health = get_health("http://127.0.0.1:" + str(port) + "/health") if state["active"] else {"ok": False}
    emit({"ok": protected["healthy"], "schema": EXPECTED["schema"], "operation": operation, "test": {"unit": state, "health": health}, "protected_services_healthy": protected["healthy"], "mutation": False})

release_id = str(cfg.get("release_id") or "")
if not SAFE_RELEASE.fullmatch(release_id):
    fail("release_id_invalid")

if operation == "prepare_upload":
    protected = base_preflight()
    ensure_private_directory(root)
    ensure_private_directory(root / ".staging")
    staging = root / ".staging" / release_id
    if staging.exists():
        fail("upload_staging_exists")
    staging.mkdir(mode=0o700)
    emit({"ok": True, "schema": EXPECTED["schema"], "operation": operation, "upload_dir": str(staging), "protected_services_healthy": protected["healthy"], "mutation": True})

if operation == "activate":
    protected_before = base_preflight()
    staging = root / ".staging" / release_id
    safe_existing_directory(staging, "upload_staging")
    manifest_path = staging / "manifest.json"
    archive_path = staging / "archive.tar.gz"
    expected = manifest_contract(manifest_path, archive_path, release_id)
    release_dir = root / "releases" / release_id
    extract_release(archive_path, release_dir, expected)
    ensure_private_directory(root / "artifacts")
    artifact_dir = root / "artifacts" / release_id
    if artifact_dir.exists():
        fail("artifact_already_exists")
    os.replace(staging, artifact_dir)
    env_path = projected_environment(release_id, release_dir)
    command = [
        "systemd-run", "--user", "--unit=" + unit, "--collect", "--no-block",
        "--service-type=simple", "--working-directory=" + str(release_dir),
        "--property=EnvironmentFile=" + str(env_path),
        "--property=Restart=on-failure", "--property=RestartSec=2s", "--property=TimeoutStopSec=10s",
        "/usr/bin/python3", str(release_dir / "scripts" / "nmbot_api_server.py"),
        "--host", "127.0.0.1", "--port", str(port),
    ]
    started = run(command)
    if started.returncode != 0:
        fail("isolated_test_start_failed")
    try:
        health = wait_test_health(release_id)
        if health.get("identity_ok") is not True:
            fail("isolated_test_health_failed")
        protected_after = protected_snapshot()
        if not same_protected(protected_before, protected_after):
            fail("protected_service_changed")
        receipt = {
            "schema": EXPECTED["schema"],
            "status": "active",
            "release_id": release_id,
            "unit": unit,
            "port": port,
            "manifest_sha256": cfg.get("manifest_sha256"),
            "archive_sha256": cfg.get("archive_sha256"),
            "source_git_sha": cfg.get("source_git_sha"),
        }
        write_atomic(root / "status.json", (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8"), 0o600)
    except SystemExit:
        run(["systemctl", "--user", "stop", unit])
        raise
    except BaseException:
        run(["systemctl", "--user", "stop", unit])
        fail("isolated_test_activation_failed")
    emit({"ok": True, "schema": EXPECTED["schema"], "operation": operation, "release_id": release_id, "test_health": "verified", "protected_services_unchanged": True, "rollback": "stop_isolated_unit", "mutation": True})

if operation == "stop":
    protected_before = protected_snapshot()
    if not protected_before["healthy"]:
        fail("protected_service_unhealthy")
    status_path = root / "status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        fail("isolated_test_status_missing")
    if status.get("release_id") != release_id or status.get("status") != "active":
        fail("isolated_test_release_mismatch")
    stop_test_unit()
    protected_after = protected_snapshot()
    if not same_protected(protected_before, protected_after):
        fail("protected_service_changed")
    status["status"] = "stopped"
    write_atomic(status_path, (json.dumps(status, sort_keys=True) + "\n").encode("utf-8"), 0o600)
    emit({"ok": True, "schema": EXPECTED["schema"], "operation": operation, "release_id": release_id, "test_active": False, "protected_services_unchanged": True, "mutation": True})
'''


def target_payload(operation: str, **fields: Any) -> dict[str, Any]:
    if operation not in {"preflight", "prepare_upload", "activate", "recon", "stop"}:
        raise IsolatedTestError("unsupported operation")
    return {
        "schema": SCHEMA,
        "operation": operation,
        "root": TEST_ROOT,
        "unit": TEST_UNIT,
        "port": TEST_PORT,
        "primary_env": PRIMARY_ENV_FILE,
        "protected_services": list(PROTECTED_SERVICES),
        "protected_health": list(PROTECTED_HEALTH),
        **fields,
    }


def _remote_command(payload: Mapping[str, Any]) -> list[str]:
    program = base64.b64encode(REMOTE_PROGRAM.encode("utf-8")).decode("ascii")
    encoded_payload = base64.b64encode(json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")).decode("ascii")
    bootstrap = "import base64,sys;code=base64.b64decode(sys.argv[1]).decode('utf-8');payload=sys.argv[2];sys.argv=[sys.argv[0],payload];exec(code)"
    remote = "python3 -c " + shlex.quote(bootstrap) + " " + shlex.quote(program) + " " + shlex.quote(encoded_payload)
    return ["ssh", "-p", SSH_PORT, *SSH_OPTIONS, HOST, remote]


def _safe_remote_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError as exc:
        raise IsolatedTestError("isolated TEST returned a malformed receipt") from exc
    if result.returncode != 0 or not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("schema") != SCHEMA:
        reason = payload.get("error") if isinstance(payload, dict) else "remote_operation_failed"
        raise IsolatedTestError(f"isolated TEST operation failed: {reason}")
    return payload


class RemoteClient:
    def __init__(self, *, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self._run = run

    def operation(self, payload: Mapping[str, Any], *, timeout: int = 120) -> dict[str, Any]:
        result = self._run(_remote_command(payload), text=True, capture_output=True, timeout=timeout, check=False)
        return _safe_remote_result(result)

    def upload(self, source: Path, destination: str) -> None:
        if not source.is_file() or source.is_symlink() or not destination.startswith(TEST_ROOT + "/.staging/"):
            raise IsolatedTestError("unsafe isolated TEST upload")
        result = self._run(
            ["scp", "-P", SSH_PORT, *SSH_OPTIONS, str(source), f"{HOST}:{destination}"],
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise IsolatedTestError("isolated TEST upload failed")


def _artifact_fields(artifact: InspectedArtifact, expected_git_sha: str) -> dict[str, str]:
    expected = validate_git_sha(expected_git_sha)
    if not expected or not GIT_SHA_RE.fullmatch(expected) or artifact.source_git_sha != expected:
        raise IsolatedTestError("artifact Git SHA does not match the confirmed candidate")
    return {
        "release_id": validate_release_id(artifact.release_id),
        "manifest_sha256": artifact.manifest_sha256,
        "archive_sha256": artifact.archive_sha256,
        "source_git_sha": artifact.source_git_sha,
    }


def _require_deploy_confirmation(*, release_id: str, apply: bool, confirmation: str) -> None:
    expected = f"DEPLOY-{release_id}-TO-ISOLATED-TEST"
    if not apply or confirmation != expected:
        raise IsolatedTestError(f"deploy confirmation must be exactly {expected}")


def _require_stop_confirmation(*, release_id: str, apply: bool, confirmation: str) -> None:
    expected = f"STOP-{release_id}-ISOLATED-TEST"
    if not apply or confirmation != expected:
        raise IsolatedTestError(f"stop confirmation must be exactly {expected}")


def deploy(
    *,
    manifest: Path,
    archive: Path,
    expected_git_sha: str,
    apply: bool,
    confirmation: str,
    client: RemoteClient,
    inspector: Callable[[Path, Path], InspectedArtifact] = inspect_artifact,
) -> dict[str, Any]:
    artifact = inspector(Path(manifest), Path(archive))
    fields = _artifact_fields(artifact, expected_git_sha)
    _require_deploy_confirmation(release_id=fields["release_id"], apply=apply, confirmation=confirmation)
    prepared = client.operation(target_payload("prepare_upload", release_id=fields["release_id"]))
    upload_dir = str(prepared.get("upload_dir") or "")
    expected_upload_dir = f"{TEST_ROOT}/.staging/{fields['release_id']}"
    if upload_dir != expected_upload_dir:
        raise IsolatedTestError("isolated TEST upload receipt mismatch")
    client.upload(Path(manifest), upload_dir + "/manifest.json")
    client.upload(Path(archive), upload_dir + "/archive.tar.gz")
    return client.operation(target_payload("activate", **fields), timeout=180)


def stop(*, release_id: str, apply: bool, confirmation: str, client: RemoteClient) -> dict[str, Any]:
    rid = validate_release_id(release_id)
    _require_stop_confirmation(release_id=rid, apply=apply, confirmation=confirmation)
    return client.operation(target_payload("stop", release_id=rid), timeout=60)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    commands.add_parser("preflight")
    commands.add_parser("recon")
    deploy_parser = commands.add_parser("deploy")
    deploy_parser.add_argument("--manifest", type=Path, required=True)
    deploy_parser.add_argument("--archive", type=Path, required=True)
    deploy_parser.add_argument("--expected-git-sha", required=True)
    deploy_parser.add_argument("--apply", action="store_true")
    deploy_parser.add_argument("--confirm", default="")
    stop_parser = commands.add_parser("stop")
    stop_parser.add_argument("--release-id", required=True)
    stop_parser.add_argument("--apply", action="store_true")
    stop_parser.add_argument("--confirm", default="")
    return parser


def main(argv: list[str] | None = None, *, client: RemoteClient | None = None) -> int:
    args = _parser().parse_args(argv)
    remote = client or RemoteClient()
    try:
        if args.command == "plan":
            result = TargetContract().as_dict()
        elif args.command in {"preflight", "recon"}:
            result = remote.operation(target_payload(args.command))
        elif args.command == "deploy":
            result = deploy(
                manifest=args.manifest,
                archive=args.archive,
                expected_git_sha=args.expected_git_sha,
                apply=args.apply,
                confirmation=args.confirm,
                client=remote,
            )
        else:
            result = stop(release_id=args.release_id, apply=args.apply, confirmation=args.confirm, client=remote)
    except (OSError, ValueError, IsolatedTestError) as exc:
        _parser().error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
