#!/usr/bin/env python3
"""Control immutable V6 releases and TEST/PROD A/B slots.

Read-only commands are safe by default. Commands that copy artifacts, start a
slot, or switch a route require both ``--apply`` and an exact confirmation.
The controller never rebuilds an existing release ID.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping

try:
    from scripts.nmbot_atomic_release import RELEASE_IDENTITY_PATH, V6_API_FILES, V6_ONLY_PROFILE
    from scripts.nmbot_release_registry import (
        ReleaseRegistry,
        ReleaseRegistryError,
        normalize_profile,
        normalize_slot,
        read_route_file,
        validate_git_sha,
        validate_release_id,
        validate_upstream,
    )
    from scripts.nmbot_slot_runner import SLOT_SCHEMA
except ImportError:  # direct scripts/ execution
    from nmbot_atomic_release import RELEASE_IDENTITY_PATH, V6_API_FILES, V6_ONLY_PROFILE
    from nmbot_release_registry import (
        ReleaseRegistry,
        ReleaseRegistryError,
        normalize_profile,
        normalize_slot,
        read_route_file,
        validate_git_sha,
        validate_release_id,
        validate_upstream,
    )
    from nmbot_slot_runner import SLOT_SCHEMA


ATOMIC_SCHEMA = "nmbot.atomic_release.v1"
V6_IMPORT_MODULES = ["scripts.nmbot_api_server"]
REQUIRED_PROMPTS = frozenset({"prompts/v6_simple_answer_writer.txt", "prompts/v6_simple_search_agent.txt"})
MAX_FILES = 2000
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
DEFAULT_ROOT = Path.home() / ".local" / "state" / "nmbot-v6-release"
IDENTITY_IN_RELEASE = RELEASE_IDENTITY_PATH
CANONICAL_RUNTIME_PACKAGE = "nmbot_core"
CONTROL_BUNDLE_SCHEMA = "nmbot.release_control_bundle.v1"
CONTROL_BUNDLE_FILES = (
    "deploy/systemd/nmbot-v6-slot@.service",
    "scripts/nmbot_atomic_release.py",
    "scripts/nmbot_release_control.py",
    "scripts/nmbot_release_registry.py",
    "scripts/nmbot_slot_runner.py",
)
CONTROL_ROOT = ".local/state/nmbot-v6-release"
CONTROL_UNIT = ".config/systemd/user/nmbot-v6-slot@.service"
AUTHORIZED_HOST = "neiro@193.107.155.236"
AUTHORIZED_PORT = "1905"
REMOTE_HOME = "/home/neiro"
REMOTE_CONTROLLER = f"{REMOTE_HOME}/{CONTROL_ROOT}/tools/current/scripts/nmbot_release_control.py"
PRIMARY_GATEWAY_ENV = Path(f"{REMOTE_HOME}/novostroy-bot/.env")
TEST_SLOT_PORTS = {"A": 18088, "B": 18089}
SLOT_ENV_KEYS = (
    "GATEWAY_POLL_TOKEN",
    "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED",
    "NMBOT_MAIN_SEARCH_FALLBACK_MODELS",
    "NMBOT_OPENROUTER_EXCLUDE_REASONING",
    "OPENROUTER_API_KEY",
    "OVERMIND_TOKEN",
    "OVERMIND_URL",
)


class ReleaseControlError(RuntimeError):
    pass


@dataclass(frozen=True)
class InspectedArtifact:
    release_id: str
    archive: Path
    manifest: Path
    archive_sha256: str
    manifest_sha256: str
    prompt_sha256: str
    source_git_sha: str
    source_git_tree_sha: str
    source_clean_receipt_sha256: str
    files: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ControlBundle:
    source_git_sha: str
    archive: Path
    manifest: Path
    archive_sha256: str
    files: tuple[tuple[str, str], ...]


class SshRemote:
    def __init__(self, *, host: str, port: str) -> None:
        if host != AUTHORIZED_HOST or str(port) != AUTHORIZED_PORT:
            raise ReleaseControlError("remote target is not the authorized primary host")
        self.host = host
        self.port = str(port)

    def run(self, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["ssh", "-p", self.port, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", self.host, command],
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )

    def upload(self, local: Path, remote_path: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["scp", "-P", self.port, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", str(local), f"{self.host}:{remote_path}"],
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: Any) -> str:
    raw = str(value or "").replace(os.sep, "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or str(path) != raw:
        raise ReleaseControlError("artifact contains an unsafe path")
    return raw


def _atomic_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _clean_git_sha(source_root: Path) -> str:
    root = Path(source_root).resolve()
    if not root.is_dir() or root.is_symlink():
        raise ReleaseControlError("control bundle source root is invalid")
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseControlError("control bundle Git identity is unavailable") from exc
    sha = validate_git_sha(head.stdout.strip()) if head.returncode == 0 else ""
    if not sha or status.returncode != 0 or status.stdout.strip():
        raise ReleaseControlError("control bundle requires an exact clean Git source")
    return sha


def build_control_bundle(*, source_root: Path, out_dir: Path) -> ControlBundle:
    root = Path(source_root).resolve()
    source_git_sha = _clean_git_sha(root)
    rows: list[tuple[str, str]] = []
    payloads: dict[str, bytes] = {}
    for relative in CONTROL_BUNDLE_FILES:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise ReleaseControlError(f"control bundle source missing: {relative}")
        payload = path.read_bytes()
        if len(payload) > MAX_FILE_BYTES:
            raise ReleaseControlError(f"control bundle source too large: {relative}")
        payloads[relative] = payload
        rows.append((relative, hashlib.sha256(payload).hexdigest()))
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f"nmbot-release-control-{source_git_sha[:12]}.tar.gz"
    manifest_path = out / f"nmbot-release-control-{source_git_sha[:12]}.manifest.json"
    if os.path.lexists(archive) or os.path.lexists(manifest_path):
        raise ReleaseControlError("refusing to overwrite immutable control bundle")
    with archive.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as bundle:
        for relative, _digest in rows:
            payload = payloads[relative]
            info = tarfile.TarInfo(relative)
            info.size = len(payload)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o644
            bundle.addfile(info, io.BytesIO(payload))
    archive_sha256 = _sha256_file(archive)
    manifest = {
        "schema": CONTROL_BUNDLE_SCHEMA,
        "source_git_sha": source_git_sha,
        "archive_name": archive.name,
        "archive_sha256": archive_sha256,
        "files": [{"path": path, "sha256": digest} for path, digest in rows],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return ControlBundle(source_git_sha, archive, manifest_path, archive_sha256, tuple(rows))


def inspect_control_bundle(*, manifest: Path, archive: Path) -> ControlBundle:
    manifest_path = Path(manifest)
    archive_path = Path(archive)
    if not manifest_path.is_file() or manifest_path.is_symlink() or not archive_path.is_file() or archive_path.is_symlink():
        raise ReleaseControlError("control bundle files must be regular non-symlink files")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseControlError("control bundle manifest is malformed") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema", "source_git_sha", "archive_name", "archive_sha256", "files"} or raw.get("schema") != CONTROL_BUNDLE_SCHEMA:
        raise ReleaseControlError("control bundle manifest schema mismatch")
    source_git_sha = validate_git_sha(raw.get("source_git_sha"))
    if not source_git_sha or raw.get("archive_name") != archive_path.name:
        raise ReleaseControlError("control bundle identity mismatch")
    archive_sha256 = str(raw.get("archive_sha256") or "")
    if len(archive_sha256) != 64 or archive_sha256 != _sha256_file(archive_path):
        raise ReleaseControlError("control bundle archive sha256 mismatch")
    rows = raw.get("files")
    if not isinstance(rows, list):
        raise ReleaseControlError("control bundle file list is invalid")
    files: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ReleaseControlError("control bundle file row is invalid")
        path = _safe_relative(row["path"])
        digest = str(row["sha256"] or "")
        if len(digest) != 64:
            raise ReleaseControlError("control bundle file hash is invalid")
        files.append((path, digest))
    if tuple(path for path, _digest in files) != CONTROL_BUNDLE_FILES:
        raise ReleaseControlError("control bundle file set is not exact")
    expected = dict(files)
    with tarfile.open(archive_path, "r:gz") as bundle:
        members = bundle.getmembers()
        if [member.name for member in members] != list(CONTROL_BUNDLE_FILES):
            raise ReleaseControlError("control bundle tar member set is not exact")
        for member in members:
            source = bundle.extractfile(member)
            payload = source.read(MAX_FILE_BYTES + 1) if source is not None and member.isfile() and not member.issym() and not member.islnk() else b""
            if len(payload) != member.size or hashlib.sha256(payload).hexdigest() != expected[member.name]:
                raise ReleaseControlError("control bundle tar member hash mismatch")
    return ControlBundle(source_git_sha, archive_path, manifest_path, archive_sha256, tuple(files))


def _bootstrap_remote_command(bundle: ControlBundle, *, remote_archive: str, remote_manifest: str) -> str:
    config = {
        "schema": CONTROL_BUNDLE_SCHEMA,
        "source_git_sha": bundle.source_git_sha,
        "archive": remote_archive,
        "manifest": remote_manifest,
        "archive_sha256": bundle.archive_sha256,
        "manifest_sha256": _sha256_file(bundle.manifest),
        "files": [{"path": path, "sha256": digest} for path, digest in bundle.files],
        "control_root": f"{REMOTE_HOME}/{CONTROL_ROOT}",
        "unit_path": f"{REMOTE_HOME}/{CONTROL_UNIT}",
    }
    program = r'''
import hashlib, json, os, pathlib, shutil, subprocess, sys, tarfile, tempfile
cfg=json.loads(sys.argv[1]); root=pathlib.Path(cfg["control_root"]); tools=root/"tools"; final=tools/cfg["source_git_sha"]
unit=pathlib.Path(cfg["unit_path"]); archive=pathlib.Path(cfg["archive"]); manifest_path=pathlib.Path(cfg["manifest"])
def fail(message): raise RuntimeError(message)
def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""): h.update(chunk)
    return h.hexdigest()
def regular(path): return path.is_file() and not path.is_symlink()
def atomic_bytes(path, payload, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix="."+path.name+".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd,"wb") as handle: handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.chmod(tmp, mode); os.replace(tmp,path)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
def run(args, **kwargs):
    proc=subprocess.run(args, text=True, capture_output=True, timeout=30, **kwargs)
    if proc.returncode != 0: fail("bootstrap command failed")
    return proc
if cfg.get("schema") != "nmbot.release_control_bundle.v1": fail("bootstrap schema mismatch")
if not regular(archive) or not regular(manifest_path): fail("bootstrap inputs invalid")
if digest(archive) != cfg["archive_sha256"] or digest(manifest_path) != cfg["manifest_sha256"]: fail("bootstrap input hash mismatch")
manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest != {"schema":cfg["schema"],"source_git_sha":cfg["source_git_sha"],"archive_name":archive.name,"archive_sha256":cfg["archive_sha256"],"files":cfg["files"]}: fail("bootstrap manifest mismatch")
for path in (root, tools, unit.parent):
    if path.exists() and path.is_symlink(): fail("bootstrap destination is a symlink")
    path.mkdir(parents=True, exist_ok=True)
expected={row["path"]:row["sha256"] for row in cfg["files"]}
created=False
if final.exists():
    if final.is_symlink() or not final.is_dir(): fail("installed tools path invalid")
else:
    staging=pathlib.Path(tempfile.mkdtemp(prefix=".bootstrap-", dir=str(tools)))
    try:
        with tarfile.open(archive,"r:gz") as bundle:
            members=bundle.getmembers()
            if [m.name for m in members] != list(expected): fail("bootstrap tar members mismatch")
            for member in members:
                if not member.isfile() or member.issym() or member.islnk() or member.size > 10*1024*1024: fail("bootstrap tar member invalid")
                source=bundle.extractfile(member); payload=source.read(10*1024*1024+1) if source else b""
                if len(payload) != member.size or hashlib.sha256(payload).hexdigest() != expected[member.name]: fail("bootstrap tar member hash mismatch")
                target=staging/member.name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(payload); target.chmod(0o644)
        (staging/"control-manifest.json").write_bytes(manifest_path.read_bytes()); (staging/"control-manifest.json").chmod(0o600)
        os.replace(staging,final); created=True
    finally:
        if staging.exists(): shutil.rmtree(staging,ignore_errors=True)
for rel,sha in expected.items():
    path=final/rel
    if not regular(path) or digest(path) != sha: fail("installed tools hash mismatch")
unit_source=final/"deploy/systemd/nmbot-v6-slot@.service"
old_unit=unit.read_bytes() if regular(unit) else None
current=tools/"current"; old_current=os.readlink(current) if current.is_symlink() else None
backup=root/"bootstrap-backups"/cfg["source_git_sha"]; backup.mkdir(parents=True,exist_ok=True)
if old_unit is not None and not (backup/"slot-unit.service").exists(): atomic_bytes(backup/"slot-unit.service",old_unit,0o600)
try:
    atomic_bytes(unit,unit_source.read_bytes(),0o644)
    temp=tools/(".current."+cfg["source_git_sha"])
    try: temp.unlink()
    except FileNotFoundError: pass
    os.symlink(cfg["source_git_sha"],temp); os.replace(temp,current)
    run(["systemctl","--user","daemon-reload"])
    env=dict(os.environ); env["PYTHONPATH"]=str(final)
    status=run([sys.executable,"-B",str(final/"scripts/nmbot_release_control.py"),"--root",str(root),"status"],cwd=final,env=env)
    payload=json.loads(status.stdout)
    if payload.get("runtime_version") != "V6": fail("controller status invalid")
except Exception:
    if old_unit is None:
        try: unit.unlink()
        except FileNotFoundError: pass
    else: atomic_bytes(unit,old_unit,0o644)
    try: current.unlink()
    except FileNotFoundError: pass
    if old_current is not None: os.symlink(old_current,current)
    try: subprocess.run(["systemctl","--user","daemon-reload"],timeout=30,check=False)
    except Exception: pass
    if created: shutil.rmtree(final,ignore_errors=True)
    raise
print(json.dumps({"schema":"nmbot.release_control_bootstrap_receipt.v1","ok":True,"source_git_sha":cfg["source_git_sha"],"control_root":str(root),"unit_installed":True,"api_touched":False,"bridge_touched":False},sort_keys=True,separators=(",",":")))
'''.strip()
    return "python3 -c " + shlex.quote(program) + " " + shlex.quote(json.dumps(config, sort_keys=True, separators=(",", ":")))


def bootstrap_remote(*, manifest: Path, archive: Path, remote: Any) -> dict[str, Any]:
    bundle = inspect_control_bundle(manifest=manifest, archive=archive)
    incoming = f"{REMOTE_HOME}/{CONTROL_ROOT}/incoming/{bundle.source_git_sha}"
    create = remote.run("umask 077; mkdir -p " + shlex.quote(incoming))
    if create.returncode != 0:
        raise ReleaseControlError("remote bootstrap staging cannot be created")
    remote_archive = f"{incoming}/{bundle.archive.name}"
    remote_manifest = f"{incoming}/{bundle.manifest.name}"
    for local, remote_path in ((bundle.archive, remote_archive), (bundle.manifest, remote_manifest)):
        uploaded = remote.upload(local, remote_path)
        if uploaded.returncode != 0:
            raise ReleaseControlError("remote bootstrap upload failed")
    result = remote.run(_bootstrap_remote_command(bundle, remote_archive=remote_archive, remote_manifest=remote_manifest))
    if result.returncode != 0:
        failure = hashlib.sha256((result.stdout + result.stderr).encode("utf-8", "replace")).hexdigest()[:24]
        raise ReleaseControlError(f"remote bootstrap failed (bootstrap-failed:{failure})")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    try:
        receipt = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError as exc:
        raise ReleaseControlError("remote bootstrap receipt is malformed") from exc
    expected = {
        "schema": "nmbot.release_control_bootstrap_receipt.v1",
        "ok": True,
        "source_git_sha": bundle.source_git_sha,
        "control_root": f"{REMOTE_HOME}/{CONTROL_ROOT}",
        "unit_installed": True,
        "api_touched": False,
        "bridge_touched": False,
    }
    if receipt != expected:
        raise ReleaseControlError("remote bootstrap receipt does not match request")
    return receipt


def _last_json_line(output: str, *, error: str) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError as exc:
        raise ReleaseControlError(error) from exc
    if not isinstance(payload, dict):
        raise ReleaseControlError(error)
    return payload


def deploy_test_remote(*, manifest: Path, archive: Path, remote: Any) -> dict[str, Any]:
    """Upload one verified archive and prepare only its isolated TEST slot."""
    inspected = inspect_artifact(manifest, archive)
    incoming = f"{REMOTE_HOME}/{CONTROL_ROOT}/incoming/{inspected.release_id}"
    create = remote.run("umask 077; mkdir -p " + shlex.quote(incoming))
    if create.returncode != 0:
        raise ReleaseControlError("remote TEST staging cannot be created")
    remote_manifest = f"{incoming}/manifest.json"
    remote_archive = f"{incoming}/{inspected.archive.name}"
    for local, remote_path in ((inspected.manifest, remote_manifest), (inspected.archive, remote_archive)):
        uploaded = remote.upload(local, remote_path)
        if uploaded.returncode != 0:
            raise ReleaseControlError("remote TEST artifact upload failed")
    command = " ".join(
        (
            "python3",
            shlex.quote(REMOTE_CONTROLLER),
            "prepare-test",
            "--manifest", shlex.quote(remote_manifest),
            "--archive", shlex.quote(remote_archive),
            "--apply",
            "--confirm", shlex.quote(f"TEST:{inspected.release_id}"),
        )
    )
    result = remote.run(command)
    if result.returncode != 0:
        failure = hashlib.sha256((result.stdout + result.stderr).encode("utf-8", "replace")).hexdigest()[:24]
        raise ReleaseControlError(f"remote TEST preparation failed (test-prepare-failed:{failure})")
    receipt = _last_json_line(result.stdout, error="remote TEST receipt is malformed")
    route = receipt.get("route")
    active = route.get("active") if isinstance(route, dict) else None
    if receipt.get("release_id") != inspected.release_id or receipt.get("profile") != "TEST" or not isinstance(active, dict) or active.get("release_id") != inspected.release_id:
        raise ReleaseControlError("remote TEST receipt does not match artifact")
    return receipt


def inspect_artifact(manifest_path: Path, archive_path: Path) -> InspectedArtifact:
    manifest_path = Path(manifest_path)
    archive_path = Path(archive_path)
    if not manifest_path.is_file() or manifest_path.is_symlink() or not archive_path.is_file() or archive_path.is_symlink():
        raise ReleaseControlError("artifact manifest/archive must be regular non-symlink files")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseControlError("artifact manifest is malformed") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != ATOMIC_SCHEMA
        or manifest.get("scope") != "api"
        or manifest.get("profile") != V6_ONLY_PROFILE
    ):
        raise ReleaseControlError("artifact manifest is not an API atomic release")
    release_id = validate_release_id(manifest.get("release_id"))
    if manifest.get("archive_name") != archive_path.name or archive_path.name != f"nmbot-{release_id}.tar.gz":
        raise ReleaseControlError("artifact archive name does not match release_id")
    expected_archive_sha = str(manifest.get("archive_sha256") or "").lower()
    actual_archive_sha = _sha256_file(archive_path)
    if len(expected_archive_sha) != 64 or expected_archive_sha != actual_archive_sha:
        raise ReleaseControlError("artifact archive sha256 mismatch")
    if manifest.get("import_modules") != V6_IMPORT_MODULES:
        raise ReleaseControlError("artifact is not the V6-only API profile")
    provenance = manifest.get("source_provenance")
    if not isinstance(provenance, dict) or set(provenance) != {"git_sha", "git_tree_sha", "tree_state", "clean_receipt_sha256"}:
        raise ReleaseControlError("artifact source provenance is missing or malformed")
    source_git_sha = validate_git_sha(provenance.get("git_sha"))
    source_git_tree_sha = validate_git_sha(provenance.get("git_tree_sha"))
    if not source_git_sha or not source_git_tree_sha or provenance.get("tree_state") != "clean":
        raise ReleaseControlError("artifact source provenance is not a clean Git tree")
    unsigned_provenance = {"git_sha": source_git_sha, "git_tree_sha": source_git_tree_sha, "tree_state": "clean"}
    expected_clean_receipt = hashlib.sha256(json.dumps(unsigned_provenance, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if provenance.get("clean_receipt_sha256") != expected_clean_receipt:
        raise ReleaseControlError("artifact clean-tree receipt is invalid")
    rows = manifest.get("files")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        raise ReleaseControlError("artifact file list is invalid")
    files: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ReleaseControlError("artifact file row is invalid")
        relative = _safe_relative(row["path"])
        digest = str(row["sha256"] or "").lower()
        if relative in seen or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ReleaseControlError("artifact file identity is invalid")
        seen.add(relative)
        files.append((relative, digest))
    expected_files = set(V6_API_FILES) | {RELEASE_IDENTITY_PATH}
    if seen != expected_files:
        raise ReleaseControlError("artifact file set is not the exact V6 API contract")
    if not any(relative.startswith(CANONICAL_RUNTIME_PACKAGE + "/") for relative in seen):
        raise ReleaseControlError("artifact is missing the canonical runtime package")
    prompt_rows = [{"path": path, "sha256": digest} for path, digest in files if path in REQUIRED_PROMPTS]
    prompt_sha = hashlib.sha256(json.dumps(prompt_rows, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return InspectedArtifact(
        release_id=release_id,
        archive=archive_path,
        manifest=manifest_path,
        archive_sha256=actual_archive_sha,
        manifest_sha256=_sha256_file(manifest_path),
        prompt_sha256=prompt_sha,
        source_git_sha=source_git_sha,
        source_git_tree_sha=source_git_tree_sha,
        source_clean_receipt_sha256=expected_clean_receipt,
        files=tuple(files),
    )


def _extract_verified(artifact: InspectedArtifact, destination: Path) -> None:
    expected = dict(artifact.files)
    total = 0
    seen: set[str] = set()
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(artifact.archive, "r:gz") as bundle:
            members = bundle.getmembers()
            if len(members) != len(expected):
                raise ReleaseControlError("artifact tar member set does not match manifest")
            for member in members:
                relative = _safe_relative(member.name)
                if relative in seen or relative not in expected or not member.isfile() or member.issym() or member.islnk():
                    raise ReleaseControlError("artifact tar member is unsafe or unexpected")
                if member.size < 0 or member.size > MAX_FILE_BYTES:
                    raise ReleaseControlError("artifact file exceeds size limit")
                total += member.size
                if total > MAX_TOTAL_BYTES:
                    raise ReleaseControlError("artifact exceeds total size limit")
                source = bundle.extractfile(member)
                if source is None:
                    raise ReleaseControlError("artifact file cannot be read")
                payload = source.read(MAX_FILE_BYTES + 1)
                if len(payload) != member.size or len(payload) > MAX_FILE_BYTES or hashlib.sha256(payload).hexdigest() != expected[relative]:
                    raise ReleaseControlError("artifact file hash mismatch")
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                target.chmod(0o600 if relative.startswith("release_identity/") else 0o755 if relative.startswith("scripts/") and relative.endswith(".py") else 0o644)
                seen.add(relative)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    if seen != set(expected):
        shutil.rmtree(destination, ignore_errors=True)
        raise ReleaseControlError("artifact tar member set does not match manifest")


def _verify_installed(release_root: Path, files: tuple[tuple[str, str], ...]) -> None:
    expected = {relative: digest for relative, digest in files}
    actual = {
        path.relative_to(release_root).as_posix()
        for path in release_root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != set(expected):
        raise ReleaseControlError("installed immutable release file set differs from artifact")
    for relative, digest in expected.items():
        path = release_root / relative
        if not path.is_file() or path.is_symlink() or _sha256_file(path) != digest:
            raise ReleaseControlError("installed immutable release differs from artifact")


def preflight_extracted_release(release_root: Path, release_id: str) -> str:
    root = Path(release_root).resolve()
    rid = validate_release_id(release_id)
    identity_path = root / IDENTITY_IN_RELEASE
    if not root.is_dir() or root.is_symlink() or not identity_path.is_file() or identity_path.is_symlink():
        raise ReleaseControlError("extracted release startup inputs are invalid")
    code = r'''
import asyncio
import importlib
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
release_id = sys.argv[2]

clean_path = []
for entry in sys.path:
    if not entry:
        continue
    try:
        if Path(entry).resolve() == root:
            continue
    except (OSError, RuntimeError):
        pass
    clean_path.append(entry)
sys.path[:] = [str(root), *clean_path]

api = importlib.import_module("scripts.nmbot_api_server")
app = api.create_app_from_environment(root)
if app.get("profile") != "TEST" or app.get("release_id") != release_id:
    raise RuntimeError("startup identity mismatch")
routes = {(route.method, getattr(route.resource, "canonical", "")) for route in app.router.routes()}
required = {
    ("GET", "/health"),
    ("POST", "/api/chat"),
    ("POST", "/api/reset"),
    ("POST", "/jivo/{provider_token}"),
}
if not required.issubset(routes):
    raise RuntimeError("required V6 routes missing")
for name, module in tuple(sys.modules.items()):
    if not (name.startswith("scripts.") or name == "nmbot_core" or name.startswith("nmbot_core.")):
        continue
    origin = getattr(module, "__file__", None)
    if origin is None:
        continue
    resolved = Path(origin).resolve()
    if resolved != root and root not in resolved.parents:
        raise RuntimeError("runtime module escaped extracted release")
print(json.dumps({
    "ok": True,
    "profile": "TEST",
    "release_id": release_id,
    "routes": sorted(f"{method} {path}" for method, path in required),
}, sort_keys=True, separators=(",", ":")))
'''
    with tempfile.TemporaryDirectory(prefix="nmbot-v6-startup-") as scratch:
        scratch_root = Path(scratch)
        env = {
            "HOME": str(Path.home()),
            "PATH": os.environ.get("PATH", os.defpath),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "NMBOT_CONTOUR_PROFILE": "TEST",
            "NMBOT_RELEASE_IDENTITY_FILE": str(identity_path),
            "NMBOT_API_STATE_FILE": str(scratch_root / "state.json"),
            "NMBOT_RUNTIME_VERSION_FILE": str(scratch_root / "runtime.json"),
            "NMBOT_CALLBACK_OUTBOX_DIR": str(scratch_root / "outbox"),
            "NMBOT_DIALOGUE_JOURNAL": str(scratch_root / "dialogue.jsonl"),
            "NMBOT_READABLE_DIALOGUE_JOURNAL": str(scratch_root / "dialogue.txt"),
            "NMBOT_CALLBACK_CRM_CONTROL_FILE": "",
            "NMBOT_API_TOKEN": "",
            "NMBOT_N8N_BRIDGE_TOKEN": "",
            "JIVO_PROVIDER_ID": "",
            "JIVO_PROVIDER_TOKEN": "",
            "OPENROUTER_API_KEY": "",
            "OVERMIND_TOKEN": "",
            "GATEWAY_POLL_TOKEN": "",
            "OVERMIND_URL": "http://127.0.0.1:1",
        }
        try:
            result = subprocess.run(
                [sys.executable, "-B", "-c", code, str(root), rid],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            receipt = hashlib.sha256(type(exc).__name__.encode("ascii")).hexdigest()[:24]
            raise ReleaseControlError(f"extracted release startup preflight failed (startup-failed:{receipt})") from exc
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1]) if result.returncode == 0 and lines else None
    except json.JSONDecodeError:
        payload = None
    expected_routes = sorted(("GET /health", "POST /api/chat", "POST /api/reset", "POST /jivo/{provider_token}"))
    if payload != {"ok": True, "profile": "TEST", "release_id": rid, "routes": expected_routes}:
        failure = hashlib.sha256((result.stdout + result.stderr).encode("utf-8", "replace")).hexdigest()[:24]
        raise ReleaseControlError(f"extracted release startup preflight failed (startup-failed:{failure})")
    receipt_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "startup:" + hashlib.sha256(receipt_payload).hexdigest()[:24]


class SystemdUserServiceManager:
    def _run(self, action: str, instance: str) -> None:
        unit = f"nmbot-v6-slot@{instance}.service"
        result = subprocess.run(
            ["systemctl", "--user", action, unit],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise ReleaseControlError(f"slot service {action} failed")

    def restart(self, instance: str) -> None:
        self._run("restart", instance)

    def stop(self, instance: str) -> None:
        self._run("stop", instance)


def probe_health(upstream: str, *, profile: str, release_id: str, timeout: float = 2.0) -> str:
    url = validate_upstream(upstream).rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload_bytes = response.read(64 * 1024)
            status = response.status
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise ReleaseControlError("slot health request failed") from exc
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseControlError("slot health response is malformed") from exc
    if status != 200 or not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("runtime") != "V6":
        raise ReleaseControlError("slot health response is not ready")
    if payload.get("profile") != normalize_profile(profile) or payload.get("release_id") != validate_release_id(release_id):
        raise ReleaseControlError("slot health identity mismatch")
    return "health:" + hashlib.sha256(payload_bytes).hexdigest()[:24]


class ReleaseController:
    def __init__(
        self,
        root: Path,
        *,
        service_manager: Any | None = None,
        health_probe: Callable[..., str] = probe_health,
        startup_preflight: Callable[[Path, str], str] = preflight_extracted_release,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        if self.root.exists() and self.root.is_symlink():
            raise ReleaseControlError("release control root must not be a symlink")
        self.registry = ReleaseRegistry(self.root)
        self.artifacts_dir = self.root / "artifacts"
        self.releases_dir = self.root / "releases"
        self.slots_dir = self.root / "slots"
        self.unit_env_dir = self.root / "unit-env"
        self.profiles_dir = self.root / "profiles"
        self.lock_path = self.root / ".control.lock"
        self.service_manager = service_manager or SystemdUserServiceManager()
        self.health_probe = health_probe
        self.startup_preflight = startup_preflight
        self.sleep = sleep

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _stored_manifest(self, release_id: str) -> Path:
        return self.artifacts_dir / validate_release_id(release_id) / "manifest.json"

    def _stored_archive(self, release_id: str) -> Path:
        manifest = json.loads(self._stored_manifest(release_id).read_text(encoding="utf-8"))
        return self.artifacts_dir / validate_release_id(release_id) / str(manifest["archive_name"])

    def project_slot_environment(self, *, profile: str, source_env: Path) -> dict[str, Any]:
        """Copy only gateway settings into a private profile environment file."""
        normalized_profile = normalize_profile(profile)
        source = Path(source_env).expanduser().resolve()
        if not source.is_file() or source.is_symlink():
            raise ReleaseControlError("source env file must be a regular non-symlink file")
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ReleaseControlError("source env file cannot be read") from exc
        selected: dict[str, str] = {}
        for raw in lines:
            line = raw.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in SLOT_ENV_KEYS:
                selected[key] = value
        if not (selected.get("OVERMIND_TOKEN", "").strip() or selected.get("GATEWAY_POLL_TOKEN", "").strip()):
            raise ReleaseControlError("slot environment has no gateway token")
        if not selected.get("OPENROUTER_API_KEY", "").strip():
            raise ReleaseControlError("slot environment has no model API key")
        target = self.profiles_dir / normalized_profile.lower() / "gateway.env"
        _atomic_text(target, "".join(f"{key}={selected[key]}\n" for key in SLOT_ENV_KEYS if key in selected))
        return {"profile": normalized_profile, "env_file": str(target), "keys": sorted(selected)}

    def prepare_test(self, *, manifest: Path, archive: Path) -> dict[str, Any]:
        """Install and activate one archive in a loopback-only TEST slot."""
        inspected = inspect_artifact(manifest, archive)
        selected_slot = self._choose_slot("TEST", None)
        installed = self.install_artifact(manifest=manifest, archive=archive)
        environment = self.project_slot_environment(profile="TEST", source_env=PRIMARY_GATEWAY_ENV)
        prepared = self.prepare_slot(
            profile="TEST",
            release_id=inspected.release_id,
            port=TEST_SLOT_PORTS[selected_slot],
            env_file=Path(environment["env_file"]),
            slot=selected_slot,
        )
        route = self.activate(profile="TEST", slot=selected_slot, reason_code="canonical_test_prepare")
        return {
            "release_id": inspected.release_id,
            "profile": "TEST",
            "slot": selected_slot,
            "environment_keys": environment["keys"],
            "prepared": prepared,
            "route": route,
            "source_git_sha": installed["source_git_sha"],
        }

    def install_artifact(self, *, manifest: Path, archive: Path) -> dict[str, Any]:
        inspected = inspect_artifact(manifest, archive)
        with self._locked():
            artifact_dir = self.artifacts_dir / inspected.release_id
            release_dir = self.releases_dir / inspected.release_id
            temp_release: Path | None = None
            if release_dir.exists():
                _verify_installed(release_dir, inspected.files)
                preflight_root = release_dir
            else:
                self.releases_dir.mkdir(parents=True, exist_ok=True)
                temp_release = Path(tempfile.mkdtemp(prefix=f".{inspected.release_id}.", dir=str(self.releases_dir)))
                temp_release.rmdir()
                _extract_verified(inspected, temp_release)
                preflight_root = temp_release
            created_artifact = False
            created_release = False
            try:
                startup_receipt = self.startup_preflight(preflight_root, inspected.release_id)
                if not isinstance(startup_receipt, str) or not startup_receipt.startswith("startup:"):
                    raise ReleaseControlError("extracted release startup receipt is invalid")
                if artifact_dir.exists():
                    stored_manifest = artifact_dir / "manifest.json"
                    stored_archive = artifact_dir / inspected.archive.name
                    if not stored_manifest.is_file() or not stored_archive.is_file() or _sha256_file(stored_manifest) != inspected.manifest_sha256 or _sha256_file(stored_archive) != inspected.archive_sha256:
                        raise ReleaseControlError("release_id already has different stored artifact bytes")
                else:
                    self.artifacts_dir.mkdir(parents=True, exist_ok=True)
                    temp_artifact = Path(tempfile.mkdtemp(prefix=f".{inspected.release_id}.", dir=str(self.artifacts_dir)))
                    try:
                        shutil.copyfile(inspected.manifest, temp_artifact / "manifest.json")
                        shutil.copyfile(inspected.archive, temp_artifact / inspected.archive.name)
                        (temp_artifact / "manifest.json").chmod(0o600)
                        (temp_artifact / inspected.archive.name).chmod(0o600)
                        os.replace(temp_artifact, artifact_dir)
                        created_artifact = True
                    finally:
                        if temp_artifact.exists():
                            shutil.rmtree(temp_artifact, ignore_errors=True)
                if temp_release is not None:
                    os.replace(temp_release, release_dir)
                    temp_release = None
                    created_release = True
                record = self.registry.register_release(
                    release_id=inspected.release_id,
                    artifact_sha256=inspected.archive_sha256,
                    manifest_sha256=inspected.manifest_sha256,
                    source_git_sha=inspected.source_git_sha,
                    prompt_sha256=inspected.prompt_sha256,
                )
                return {
                    **record,
                    "artifact_dir": str(artifact_dir),
                    "release_root": str(release_dir),
                    "startup_receipt": startup_receipt,
                    "source_git_tree_sha": inspected.source_git_tree_sha,
                    "source_clean_receipt_sha256": inspected.source_clean_receipt_sha256,
                }
            except Exception:
                if created_release:
                    shutil.rmtree(release_dir, ignore_errors=True)
                if created_artifact:
                    shutil.rmtree(artifact_dir, ignore_errors=True)
                raise
            finally:
                if temp_release is not None and temp_release.exists():
                    shutil.rmtree(temp_release, ignore_errors=True)

    def sync_to(self, destination: "ReleaseController", release_id: str) -> dict[str, Any]:
        source = self.registry.show_release(release_id)
        return destination.install_artifact(
            manifest=self._stored_manifest(release_id),
            archive=self._stored_archive(release_id),
        )

    def promote_to(self, destination: "ReleaseController", release_id: str) -> dict[str, Any]:
        source = self.registry.show_release(release_id)
        if source.get("quality", {}).get("verdict") != "approved":
            raise ReleaseControlError("promotion requires approved quality")
        check = source.get("last_check")
        if not isinstance(check, dict) or check.get("profile") != "TEST" or check.get("outcome") != "passed":
            raise ReleaseControlError("promotion requires a passing TEST check")
        synced = self.sync_to(destination, release_id)
        receipt = str(source["quality"]["receipt_ref"])
        destination.registry.set_quality(release_id, verdict="approved", receipt_ref=receipt)
        return {**synced, "promotion": "synced_not_activated"}

    def _choose_slot(self, profile: str, requested: str | None) -> str:
        route = self.registry.read_route(profile, required=False)
        active_slot = route["active"]["slot"] if route else None
        slot = normalize_slot(requested) if requested else ("B" if active_slot == "A" else "A")
        if slot == active_slot:
            raise ReleaseControlError("cannot prepare the currently active slot")
        return slot

    def _descriptor_path(self, profile: str, slot: str) -> Path:
        return self.slots_dir / f"{profile.lower()}-{slot.lower()}.json"

    @staticmethod
    def _instance(profile: str, slot: str) -> str:
        return f"{profile.lower()}-{slot.lower()}"

    def _wait_health(self, upstream: str, *, profile: str, release_id: str, timeout: float) -> str:
        deadline = time.monotonic() + max(0.1, timeout)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return self.health_probe(upstream, profile=profile, release_id=release_id)
            except Exception as exc:
                last_error = exc
                self.sleep(0.1)
        raise ReleaseControlError("slot did not become healthy before deadline") from last_error

    def prepare_slot(
        self,
        *,
        profile: str,
        release_id: str,
        port: int,
        env_file: Path,
        slot: str | None = None,
        health_timeout: float = 30.0,
    ) -> dict[str, Any]:
        normalized_profile = normalize_profile(profile)
        rid = validate_release_id(release_id)
        selected_slot = self._choose_slot(normalized_profile, slot)
        try:
            numeric_port = int(port)
        except (TypeError, ValueError) as exc:
            raise ReleaseControlError("slot port is invalid") from exc
        upstream = validate_upstream(f"http://127.0.0.1:{numeric_port}")
        environment_path = Path(env_file).expanduser().resolve()
        if not environment_path.is_file() or environment_path.is_symlink():
            raise ReleaseControlError("contour env file must be a regular non-symlink file")
        release = self.registry.show_release(rid)
        if normalized_profile == "PROD":
            quality = release.get("quality")
            check = release.get("last_check")
            if not isinstance(quality, dict) or quality.get("verdict") != "approved":
                raise ReleaseControlError("PROD preparation requires approved quality")
            if not isinstance(check, dict) or check.get("profile") != "TEST" or check.get("outcome") != "passed":
                raise ReleaseControlError("PROD preparation requires a passing TEST check")
        release_root = self.releases_dir / rid
        if not release_root.is_dir() or release_root.is_symlink():
            raise ReleaseControlError("immutable release is not installed")
        inspected = inspect_artifact(self._stored_manifest(rid), self._stored_archive(rid))
        if inspected.archive_sha256 != release.get("artifact_sha256") or inspected.manifest_sha256 != release.get("manifest_sha256"):
            raise ReleaseControlError("registered release provenance differs from stored artifact")
        _verify_installed(release_root, inspected.files)
        instance = self._instance(normalized_profile, selected_slot)
        descriptor_path = self._descriptor_path(normalized_profile, selected_slot)
        data_root = self.profiles_dir / normalized_profile.lower() / "data"
        descriptor = {
            "schema": SLOT_SCHEMA,
            "profile": normalized_profile,
            "slot": selected_slot,
            "release_id": rid,
            "release_root": str(release_root),
            "manifest_path": str(self._stored_manifest(rid)),
            "manifest_sha256": inspected.manifest_sha256,
            "env_file": str(environment_path),
            "data_root": str(data_root),
            "port": numeric_port,
        }
        started = False
        self.registry.begin_slot_prepare(profile=normalized_profile, slot=selected_slot, release_id=rid, upstream=upstream)
        try:
            _atomic_text(descriptor_path, json.dumps(descriptor, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            runner = Path(__file__).resolve().with_name("nmbot_slot_runner.py")
            unit_env = self.unit_env_dir / f"{instance}.env"
            _atomic_text(unit_env, f"NMBOT_SLOT_RUNNER={runner}\nNMBOT_SLOT_DESCRIPTOR={descriptor_path}\n")
            self.service_manager.restart(instance)
            started = True
            receipt = self._wait_health(upstream, profile=normalized_profile, release_id=rid, timeout=health_timeout)
            slot_state = self.registry.prepare_slot(
                profile=normalized_profile,
                slot=selected_slot,
                release_id=rid,
                upstream=upstream,
                health_receipt_ref=receipt,
            )
        except Exception as exc:
            failure_ref = "health-failed:" + hashlib.sha256(type(exc).__name__.encode("ascii")).hexdigest()[:16]
            try:
                self.registry.fail_slot_prepare(
                    profile=normalized_profile,
                    slot=selected_slot,
                    release_id=rid,
                    reason_code="health_failed",
                    receipt_ref=failure_ref,
                )
            finally:
                if started:
                    try:
                        self.service_manager.stop(instance)
                    except Exception:
                        pass
            raise
        return {**slot_state, "profile": normalized_profile, "slot": selected_slot, "instance": instance, "descriptor": str(descriptor_path), "source_git_sha": release["source_git_sha"]}

    def _assert_healthy(self, *, profile: str, target: Mapping[str, str]) -> str:
        return self._wait_health(target["upstream"], profile=profile, release_id=target["release_id"], timeout=5.0)

    def activate(self, *, profile: str, slot: str, reason_code: str = "manual") -> dict[str, Any]:
        normalized_profile = normalize_profile(profile)
        normalized_slot = normalize_slot(slot)
        state = self.registry.slot_state(profile=normalized_profile, slot=normalized_slot)
        if state.get("status") != "ready":
            raise ReleaseControlError("slot is not ready")
        target = {"slot": normalized_slot, "release_id": state["release_id"], "upstream": state["upstream"]}
        self._assert_healthy(profile=normalized_profile, target=target)

        def post_switch(route: dict[str, Any]) -> None:
            observed = read_route_file(self.registry.route_path(normalized_profile), expected_profile=normalized_profile)
            if observed["active"] != route["active"]:
                raise ReleaseControlError("route post-check identity mismatch")
            self._assert_healthy(profile=normalized_profile, target=route["active"])

        return self.registry.activate(profile=normalized_profile, slot=normalized_slot, reason_code=reason_code, post_switch_check=post_switch)

    def rollback(self, *, profile: str, reason_code: str = "manual_rollback") -> dict[str, Any]:
        normalized_profile = normalize_profile(profile)
        current = self.registry.read_route(normalized_profile, required=True)
        assert current is not None
        previous = current.get("previous")
        if not previous:
            raise ReleaseControlError("no previous release is available for rollback")
        self._assert_healthy(profile=normalized_profile, target=previous)

        def post_switch(route: dict[str, Any]) -> None:
            observed = read_route_file(self.registry.route_path(normalized_profile), expected_profile=normalized_profile)
            if observed["active"] != route["active"]:
                raise ReleaseControlError("route post-check identity mismatch")
            self._assert_healthy(profile=normalized_profile, target=route["active"])

        return self.registry.rollback(profile=normalized_profile, reason_code=reason_code, post_switch_check=post_switch)

    def status(self) -> dict[str, Any]:
        profiles: dict[str, Any] = {}
        for profile in ("TEST", "PROD"):
            profiles[profile] = {
                "route": self.registry.read_route(profile, required=False),
                "slots": {slot: self.registry.slot_state(profile=profile, slot=slot) for slot in ("A", "B")},
            }
        return {"runtime_version": "V6", "profiles": profiles}


def _require_apply(args: argparse.Namespace, expected_confirmation: str) -> None:
    if not getattr(args, "apply", False):
        raise ReleaseControlError("mutation requires --apply")
    if str(getattr(args, "confirm", "")) != expected_confirmation:
        raise ReleaseControlError(f"confirmation must be exactly {expected_confirmation}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(os.getenv("NMBOT_RELEASE_CONTROL_ROOT", str(DEFAULT_ROOT))))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    show = commands.add_parser("show"); show.add_argument("release_id")
    commands.add_parser("status")
    commands.add_parser("journal")
    project_env = commands.add_parser("project-env")
    project_env.add_argument("--profile", choices=("TEST", "PROD"), required=True)
    project_env.add_argument("--source-env", type=Path, required=True)
    project_env.add_argument("--apply", action="store_true")
    project_env.add_argument("--confirm", required=True)
    prepare_test = commands.add_parser("prepare-test")
    prepare_test.add_argument("--manifest", type=Path, required=True)
    prepare_test.add_argument("--archive", type=Path, required=True)
    prepare_test.add_argument("--apply", action="store_true")
    prepare_test.add_argument("--confirm", required=True)
    deploy_test = commands.add_parser("deploy-test")
    deploy_test.add_argument("--manifest", type=Path, required=True)
    deploy_test.add_argument("--archive", type=Path, required=True)
    deploy_test.add_argument("--host", default=AUTHORIZED_HOST)
    deploy_test.add_argument("--port", default=AUTHORIZED_PORT)
    deploy_test.add_argument("--apply", action="store_true")
    deploy_test.add_argument("--confirm", required=True)
    bundle = commands.add_parser("bundle-control")
    bundle.add_argument("--source-root", type=Path, required=True); bundle.add_argument("--out-dir", type=Path, required=True)
    bootstrap = commands.add_parser("bootstrap-control")
    bootstrap.add_argument("--manifest", type=Path, required=True); bootstrap.add_argument("--archive", type=Path, required=True); bootstrap.add_argument("--host", default=AUTHORIZED_HOST); bootstrap.add_argument("--port", default=AUTHORIZED_PORT); bootstrap.add_argument("--apply", action="store_true"); bootstrap.add_argument("--confirm", required=True)
    register = commands.add_parser("register")
    register.add_argument("--manifest", type=Path, required=True); register.add_argument("--archive", type=Path, required=True); register.add_argument("--apply", action="store_true"); register.add_argument("--confirm", required=True)
    quality = commands.add_parser("quality")
    quality.add_argument("release_id"); quality.add_argument("--verdict", choices=("approved", "rejected"), required=True); quality.add_argument("--receipt-ref", required=True); quality.add_argument("--apply", action="store_true"); quality.add_argument("--confirm", required=True)
    check = commands.add_parser("check")
    check.add_argument("release_id"); check.add_argument("--profile", choices=("TEST", "PROD"), required=True); check.add_argument("--outcome", choices=("passed", "failed"), required=True); check.add_argument("--reason-code", required=True); check.add_argument("--receipt-ref", required=True); check.add_argument("--apply", action="store_true"); check.add_argument("--confirm", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("release_id"); prepare.add_argument("--profile", choices=("TEST", "PROD"), required=True); prepare.add_argument("--slot", choices=("A", "B")); prepare.add_argument("--port", type=int, required=True); prepare.add_argument("--env-file", type=Path, required=True); prepare.add_argument("--apply", action="store_true"); prepare.add_argument("--confirm", required=True)
    activate = commands.add_parser("activate")
    activate.add_argument("--profile", choices=("TEST", "PROD"), required=True); activate.add_argument("--slot", choices=("A", "B"), required=True); activate.add_argument("--reason-code", default="manual"); activate.add_argument("--apply", action="store_true"); activate.add_argument("--confirm", required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--profile", choices=("TEST", "PROD"), required=True); rollback.add_argument("--reason-code", default="manual_rollback"); rollback.add_argument("--apply", action="store_true"); rollback.add_argument("--confirm", required=True)
    for name in ("sync", "promote"):
        command = commands.add_parser(name); command.add_argument("release_id"); command.add_argument("--destination-root", type=Path, required=True); command.add_argument("--apply", action="store_true"); command.add_argument("--confirm", required=True)
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    controller = ReleaseController(args.root)
    try:
        if args.command == "list": result: Any = controller.registry.list_releases()
        elif args.command == "show": result = controller.registry.show_release(args.release_id)
        elif args.command == "status": result = controller.status()
        elif args.command == "journal": result = controller.registry.journal_events()
        elif args.command == "project-env":
            _require_apply(args, f"ENV:{args.profile}")
            result = controller.project_slot_environment(profile=args.profile, source_env=args.source_env)
        elif args.command == "prepare-test":
            inspected = inspect_artifact(args.manifest, args.archive)
            _require_apply(args, f"TEST:{inspected.release_id}")
            result = controller.prepare_test(manifest=args.manifest, archive=args.archive)
        elif args.command == "deploy-test":
            inspected = inspect_artifact(args.manifest, args.archive)
            _require_apply(args, f"TEST:{inspected.release_id}")
            result = deploy_test_remote(manifest=args.manifest, archive=args.archive, remote=SshRemote(host=args.host, port=args.port))
        elif args.command == "bundle-control":
            bundle = build_control_bundle(source_root=args.source_root, out_dir=args.out_dir); result = {"schema": CONTROL_BUNDLE_SCHEMA, "source_git_sha": bundle.source_git_sha, "archive": str(bundle.archive), "manifest": str(bundle.manifest), "archive_sha256": bundle.archive_sha256, "files": len(bundle.files)}
        elif args.command == "bootstrap-control":
            bundle = inspect_control_bundle(manifest=args.manifest, archive=args.archive); _require_apply(args, f"BOOTSTRAP:{bundle.source_git_sha}"); result = bootstrap_remote(manifest=args.manifest, archive=args.archive, remote=SshRemote(host=args.host, port=args.port))
        elif args.command == "register":
            inspected = inspect_artifact(args.manifest, args.archive); _require_apply(args, inspected.release_id); result = controller.install_artifact(manifest=args.manifest, archive=args.archive)
        elif args.command == "quality":
            _require_apply(args, args.release_id); result = controller.registry.set_quality(args.release_id, verdict=args.verdict, receipt_ref=args.receipt_ref)
        elif args.command == "check":
            _require_apply(args, f"{args.profile}:{args.release_id}"); result = controller.registry.record_check(args.release_id, profile=args.profile, outcome=args.outcome, reason_code=args.reason_code, receipt_ref=args.receipt_ref)
        elif args.command == "prepare":
            _require_apply(args, f"{args.profile}:{args.release_id}"); result = controller.prepare_slot(profile=args.profile, release_id=args.release_id, port=args.port, env_file=args.env_file, slot=args.slot)
        elif args.command == "activate":
            state = controller.registry.slot_state(profile=args.profile, slot=args.slot); expected = f"{args.profile}:{state.get('release_id')}"; _require_apply(args, expected); result = controller.activate(profile=args.profile, slot=args.slot, reason_code=args.reason_code)
        elif args.command == "rollback":
            route = controller.registry.read_route(args.profile, required=True); assert route is not None; previous = route.get("previous") or {}; expected = f"{args.profile}:{previous.get('release_id')}"; _require_apply(args, expected); result = controller.rollback(profile=args.profile, reason_code=args.reason_code)
        elif args.command in {"sync", "promote"}:
            _require_apply(args, args.release_id); destination = ReleaseController(args.destination_root); result = controller.promote_to(destination, args.release_id) if args.command == "promote" else controller.sync_to(destination, args.release_id)
        else:  # pragma: no cover
            raise ReleaseControlError("unknown command")
    except (ReleaseControlError, ReleaseRegistryError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
