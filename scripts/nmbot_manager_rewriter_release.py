#!/usr/bin/env python3
"""Safe one-step release manager for the V2/V3 manager rewriter bundle.

Default command is local-only ``preflight``. The deploy path is intentionally
written as a small state machine with dependency injection, so tests can prove
the command order without real SSH/SCP/systemctl/network calls.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import py_compile
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DIR = ROOT / "docs" / "archive" / "release-candidates" / "2026-07-24" / "manager_rewriter"
DEFAULT_MANIFEST = BUNDLE_DIR / "manifest.json"
DEFAULT_CANDIDATES = BUNDLE_DIR / "candidates"
DEFAULT_HOST = "neiro@193.107.155.236"
DEFAULT_PORT = "1905"
IDENTITY_RELATIVE_PATH = "data/nmbot_release_identity.json"
IDENTITY_SCHEMA = "nmbot.release_identity.v1"
SECRET_PATTERNS = (
    re.compile(r"(?i)(token|secret|password|api[_-]?key)\s*[:=]\s*[^\s]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
)


class ReleaseError(RuntimeError):
    pass


class Remote(Protocol):
    def run(self, command: str, *, input_text: str | None = None) -> subprocess.CompletedProcess[str]: ...
    def upload(self, local: Path, remote_path: str) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True)
class Target:
    path: str
    kind: str
    expected_remote_base_sha256: str | None
    candidate_sha256: str


class SshRemote:
    def __init__(self, *, host: str = DEFAULT_HOST, port: str = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port

    def run(self, command: str, *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["ssh", "-p", self.port, "-o", "BatchMode=yes", self.host, command], input=input_text, text=True, capture_output=True, check=False)

    def upload(self, local: Path, remote_path: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["scp", "-P", self.port, str(local), f"{self.host}:{remote_path}"], text=True, capture_output=True, check=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_rel(path: str) -> str:
    rel = str(path or "").strip()
    parts = Path(rel).parts
    if not rel or Path(rel).is_absolute() or ".." in parts:
        raise ReleaseError(f"unsafe target path: {path!r}")
    return rel


def sanitize(text: str) -> str:
    out = str(text or "")
    for pattern in SECRET_PATTERNS:
        out = pattern.sub(lambda m: m.group(0).split("=", 1)[0].split(":", 1)[0] + "=[redacted]", out)
    return out


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "nmbot.manager_rewriter_release.v2":
        raise ReleaseError("unsupported manifest schema")
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ReleaseError("manifest targets must be a non-empty list")
    seen: set[str] = set()
    for item in targets:
        if not isinstance(item, dict):
            raise ReleaseError("manifest target must be an object")
        rel = safe_rel(str(item.get("path") or ""))
        if rel in seen:
            raise ReleaseError(f"duplicate target: {rel}")
        seen.add(rel)
        if item.get("kind") not in {"add", "modify"}:
            raise ReleaseError(f"invalid target kind for {rel}")
        digest = item.get("candidate_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ReleaseError(f"invalid candidate hash for {rel}")
        base = item.get("expected_remote_base_sha256")
        if base is not None and (not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{64}", base)):
            raise ReleaseError(f"invalid base hash for {rel}")
    if data.get("service") != "novostroy-bot-api.service":
        raise ReleaseError("manifest must restart only novostroy-bot-api.service")
    if "novostroy-bot-n8n-bridge.service" not in data.get("forbidden_services", []):
        raise ReleaseError("manifest must explicitly forbid bridge restart")
    runtime_modes = data.get("runtime_modes")
    if runtime_modes != {"V2": "off", "V3": "publish"}:
        raise ReleaseError("manifest runtime_modes must be exactly V2=off and V3=publish")
    return data


def targets_from_manifest(manifest: dict[str, Any]) -> tuple[Target, ...]:
    return tuple(Target(path=safe_rel(item["path"]), kind=item["kind"], expected_remote_base_sha256=item.get("expected_remote_base_sha256"), candidate_sha256=item["candidate_sha256"]) for item in manifest["targets"])


def verify_candidate_hashes(targets: tuple[Target, ...], candidates_dir: Path = DEFAULT_CANDIDATES) -> list[str]:
    lines: list[str] = []
    for target in targets:
        path = candidates_dir / target.path
        if not path.is_file():
            raise ReleaseError(f"candidate missing: {target.path}")
        actual = sha256_file(path)
        if actual != target.candidate_sha256:
            raise ReleaseError(f"candidate hash mismatch: {target.path}")
        lines.append(f"candidate {target.path} sha256:{actual}")
    return lines


def _target_hashes(targets: tuple[Target, ...]) -> dict[str, str]:
    return {target.path: target.candidate_sha256 for target in targets}


def validate_candidate_identity(targets: tuple[Target, ...], candidates_dir: Path = DEFAULT_CANDIDATES) -> str:
    identity_target_paths = {target.path for target in targets if target.path == IDENTITY_RELATIVE_PATH}
    if identity_target_paths != {IDENTITY_RELATIVE_PATH}:
        raise ReleaseError("manifest must include candidate release identity target")
    identity_path = candidates_dir / IDENTITY_RELATIVE_PATH
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError("candidate identity is missing or invalid JSON") from exc
    if not isinstance(identity, dict) or identity.get("schema") != IDENTITY_SCHEMA:
        raise ReleaseError("candidate identity has unsupported schema")
    release_id = str(identity.get("release_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", release_id):
        raise ReleaseError("candidate identity has unsafe release_id")
    tracked = identity.get("tracked_files")
    if not isinstance(tracked, list):
        raise ReleaseError("candidate identity tracked_files must be a list")
    expected = {path: digest for path, digest in _target_hashes(targets).items() if path != IDENTITY_RELATIVE_PATH}
    actual: dict[str, str] = {}
    for item in tracked:
        if not isinstance(item, dict):
            raise ReleaseError("candidate identity tracked file must be an object")
        path = safe_rel(str(item.get("path") or ""))
        digest = item.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ReleaseError(f"candidate identity invalid hash: {path}")
        if path in actual:
            raise ReleaseError(f"candidate identity duplicate tracked file: {path}")
        actual[path] = digest
    if set(actual) != set(expected):
        raise ReleaseError("candidate identity tracked path set does not match bundle targets")
    for path, expected_hash in expected.items():
        if actual[path] != expected_hash:
            raise ReleaseError(f"candidate identity hash mismatch: {path}")
    return release_id


def local_compile_and_import(targets: tuple[Target, ...], candidates_dir: Path = DEFAULT_CANDIDATES) -> list[str]:
    py_files = [candidates_dir / t.path for t in targets if t.path.endswith(".py")]
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        for index, path in enumerate(py_files):
            py_compile.compile(str(path), cfile=str(tmp_root / f"candidate-{index}.pyc"), doraise=True)
        importable = ["nmbot_v2.manager_rewriter", "nmbot_v2.runtime", "nmbot_v2.state", "nmbot_v2.ports", "scripts.nmbot_runtime_adapter"]
        # Copy local packages, then overlay release candidates. That makes the
        # import smoke exercise candidate modules while reusing untouched project
        # dependencies, without mutating the working tree or using the network.
        shutil.copytree(ROOT / "nmbot_v2", tmp_root / "nmbot_v2")
        shutil.copytree(ROOT / "nmbot_v0", tmp_root / "nmbot_v0")
        shutil.copytree(ROOT / "scripts", tmp_root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "followup_intent_classifier.py", tmp_root / "followup_intent_classifier.py")
        for target in targets:
            src = candidates_dir / target.path
            dst = tmp_root / target.path
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
        code = "import " + ", ".join(importable) + "; print('import=ok')"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(tmp_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_root, env=env, text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            raise ReleaseError(sanitize((proc.stdout + proc.stderr)[-2000:]))
    return [f"py_compile files={len(py_files)}", "import_compat modules=5"]


def render_plan(manifest: dict[str, Any], targets: tuple[Target, ...]) -> str:
    lines = [
        "plan=manager_rewriter_release",
        "local_only_preflight=true",
        f"remote_root={manifest['remote_root']}",
        "restart=novostroy-bot-api.service only",
        "bridge_restart=false",
        "runtime_modes=V2:off,V3:publish",
        "publish_after=remote py_compile/import smoke",
        "rollback=automatic on first deploy error",
        "targets:",
    ]
    for target in targets:
        base = target.expected_remote_base_sha256 or "missing"
        lines.append(f"  {target.kind} {target.path} base={base} candidate={target.candidate_sha256}")
    return "\n".join(lines) + "\n"


def preflight(*, manifest_path: Path = DEFAULT_MANIFEST, candidates_dir: Path = DEFAULT_CANDIDATES) -> str:
    manifest = load_manifest(manifest_path)
    targets = targets_from_manifest(manifest)
    lines = [render_plan(manifest, targets).rstrip()]
    lines.extend(verify_candidate_hashes(targets, candidates_dir))
    lines.append(f"candidate_identity release_id={validate_candidate_identity(targets, candidates_dir)}")
    lines.extend(local_compile_and_import(targets, candidates_dir))
    return sanitize("\n".join(lines) + "\n")


def remote_json(remote: Remote, command: str) -> dict[str, Any]:
    proc = remote.run(command)
    if proc.returncode != 0:
        raise ReleaseError(sanitize((proc.stdout + proc.stderr)[-2000:]))
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ReleaseError("remote returned invalid JSON") from exc


def remote_probe_script(manifest: dict[str, Any], targets: tuple[Target, ...]) -> str:
    return "python3 - <<'PY'\n" + f"""
import hashlib, json, os, urllib.request, subprocess
from pathlib import Path
root=Path({manifest['remote_root']!r})
targets={ [t.path for t in targets]!r }
mode_keys={{'V2':'NMBOT_V2_MANAGER_REWRITER_MODE','V3':'NMBOT_V3_MANAGER_REWRITER_MODE'}}
def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()
def env_modes():
    env=root/'.env'
    result={{'V2':'off','V3':'off'}}
    if not env.is_file(): return result
    for line in env.read_text(encoding='utf-8').splitlines():
        stripped=line.strip()
        for version, key in mode_keys.items():
            if stripped.startswith(key+'='):
                value=line.split('=',1)[1].strip().strip('"\\'')
                result[version]=value if value in {{'off','shadow','publish'}} else 'off'
    return result
service=subprocess.run(['systemctl','--user','is-active',{manifest['service']!r}],capture_output=True,text=True,check=False).stdout.strip()
try:
    health=json.loads(urllib.request.urlopen({manifest['health_url']!r}, timeout=10).read().decode())
except Exception as exc:
    health={{'ok': False, 'error': exc.__class__.__name__}}
print(json.dumps({{'hashes':{{p:(digest(root/p) if (root/p).is_file() else None) for p in targets}},'runtime_modes':env_modes(),'service_active':service,'health':health}}, ensure_ascii=False))
""" + "\nPY"


def require_strict_green_probe(probe: dict[str, Any], *, context: str) -> None:
    if probe.get("service_active") != "active":
        raise ReleaseError(f"{context}: api service is not active")
    health = probe.get("health")
    if not isinstance(health, dict) or health.get("ok") is not True:
        raise ReleaseError(f"{context}: api health is not strictly ok")


def assert_remote_base(remote: Remote, manifest: dict[str, Any], targets: tuple[Target, ...]) -> dict[str, Any]:
    probe = remote_json(remote, remote_probe_script(manifest, targets))
    require_strict_green_probe(probe, context="remote baseline before write")
    hashes = probe.get("hashes") if isinstance(probe.get("hashes"), dict) else {}
    for target in targets:
        actual = hashes.get(target.path)
        expected = target.expected_remote_base_sha256
        if actual != expected:
            raise ReleaseError(f"remote drift before write: {target.path}")
    return probe


def backup(remote: Remote, manifest: dict[str, Any], targets: tuple[Target, ...], release_id: str) -> str:
    files = [t.path for t in targets] + [".env", "data/nmbot_release_identity.json"]
    quoted_files = " ".join(shlex.quote(x) for x in files)
    cmd = f"cd {shlex.quote(manifest['remote_root'])} && python3 - <<'PY'\n" + f"""
import json, shutil, time
from pathlib import Path
root=Path('.')
backup=root/'backups'/('manager-rewriter-{release_id}-'+time.strftime('%Y%m%d-%H%M%S'))
backup.mkdir(parents=True, exist_ok=False)
files={files!r}
identity={{'release_id':{release_id!r},'files':{{}}}}
for rel in files:
    src=root/rel
    identity['files'][rel]={{'existed':src.is_file()}}
    if src.is_file():
        dst=backup/rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src,dst)
(backup/'release_identity.json').write_text(json.dumps(identity,ensure_ascii=False,sort_keys=True),encoding='utf-8')
print(str(backup))
""" + "\nPY"
    proc = remote.run(cmd)
    if proc.returncode != 0:
        raise ReleaseError(sanitize((proc.stdout + proc.stderr)[-2000:]))
    return proc.stdout.strip().splitlines()[-1]


def upload_candidates(remote: Remote, manifest: dict[str, Any], targets: tuple[Target, ...], candidates_dir: Path) -> None:
    root = manifest["remote_root"]
    for target in targets:
        rel = target.path
        mkdir = remote.run("mkdir -p " + shlex.quote(str(Path(root) / Path(rel).parent)))
        if mkdir.returncode != 0:
            raise ReleaseError(sanitize((mkdir.stdout + mkdir.stderr)[-2000:]))
        proc = remote.upload(candidates_dir / rel, str(Path(root) / rel))
        if proc.returncode != 0:
            raise ReleaseError(sanitize((proc.stdout + proc.stderr)[-2000:]))


def remote_compile_import(remote: Remote, manifest: dict[str, Any], targets: tuple[Target, ...]) -> None:
    py_files = [t.path for t in targets if t.path.endswith(".py")]
    compile_cmd = "cd " + shlex.quote(manifest["remote_root"]) + " && python3 -m py_compile " + " ".join(map(shlex.quote, py_files))
    proc = remote.run(compile_cmd)
    if proc.returncode != 0:
        raise ReleaseError(sanitize((proc.stdout + proc.stderr)[-2000:]))
    imports = "import nmbot_v2.manager_rewriter, nmbot_v2.runtime, nmbot_v2.state, nmbot_v2.ports, scripts.nmbot_runtime_adapter; print('import=ok')"
    proc = remote.run("cd " + shlex.quote(manifest["remote_root"]) + " && python3 -c " + shlex.quote(imports))
    if proc.returncode != 0:
        raise ReleaseError(sanitize((proc.stdout + proc.stderr)[-2000:]))


def set_runtime_modes(remote: Remote, manifest: dict[str, Any]) -> None:
    for version, mode in manifest["runtime_modes"].items():
        cmd = "cd " + shlex.quote(manifest["remote_root"]) + " && python3 scripts/nmbot_manager_rewriter.py " + shlex.quote(mode) + " --runtime " + shlex.quote(version) + " --env .env"
        proc = remote.run(cmd)
        if proc.returncode != 0:
            raise ReleaseError(sanitize((proc.stdout + proc.stderr)[-2000:]))


def restart_api(remote: Remote, manifest: dict[str, Any]) -> None:
    service = manifest["service"]
    if service != "novostroy-bot-api.service":
        raise ReleaseError("refusing non-API restart")
    proc = remote.run("systemctl --user restart " + shlex.quote(service))
    if proc.returncode != 0:
        raise ReleaseError(sanitize((proc.stdout + proc.stderr)[-2000:]))


def verify_remote(remote: Remote, manifest: dict[str, Any], targets: tuple[Target, ...]) -> None:
    probe = remote_json(remote, remote_probe_script(manifest, targets))
    if probe.get("runtime_modes") != manifest["runtime_modes"]:
        raise ReleaseError("runtime modes are not V2=off and V3=publish")
    require_strict_green_probe(probe, context="remote verification")
    hashes = probe.get("hashes") if isinstance(probe.get("hashes"), dict) else {}
    for target in targets:
        if hashes.get(target.path) != target.candidate_sha256:
            raise ReleaseError(f"remote target hash mismatch: {target.path}")


def rollback(remote: Remote, manifest: dict[str, Any], targets: tuple[Target, ...], backup_dir: str, pre_write_probe: dict[str, Any]) -> None:
    # Deploy is allowed to start only from an active, strictly healthy API.
    # Therefore rollback verification restores files/env/identity and requires
    # the same active API baseline instead of treating recovery as best-effort.
    files = [t.path for t in targets] + [".env", "data/nmbot_release_identity.json"]
    cmd = "cd " + shlex.quote(manifest["remote_root"]) + " && python3 - <<'PY'\n" + f"""
import json, shutil
from pathlib import Path
root=Path('.')
backup=Path({backup_dir!r})
files={files!r}
identity_file=backup/'release_identity.json'
identity=json.loads(identity_file.read_text(encoding='utf-8')) if identity_file.is_file() else {{'files':{{}}}}
for rel in files:
    dst=root/rel
    existed=bool(identity.get('files',{{}}).get(rel,{{}}).get('existed'))
    src=backup/rel
    if existed and src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src,dst)
    elif dst.exists():
        dst.unlink()
print('rollback=ok')
""" + "\nPY"
    proc = remote.run(cmd)
    if proc.returncode != 0:
        raise ReleaseError(sanitize((proc.stdout + proc.stderr)[-2000:]))
    restart_api(remote, manifest)
    probe = remote_json(remote, remote_probe_script(manifest, targets))
    require_strict_green_probe(probe, context="rollback verification")
    expected_hashes = pre_write_probe.get("hashes") if isinstance(pre_write_probe.get("hashes"), dict) else {}
    actual_hashes = probe.get("hashes") if isinstance(probe.get("hashes"), dict) else {}
    for target in targets:
        if actual_hashes.get(target.path) != expected_hashes.get(target.path):
            raise ReleaseError(f"rollback verification hash mismatch: {target.path}")
    if probe.get("runtime_modes") != pre_write_probe.get("runtime_modes"):
        raise ReleaseError("rollback verification runtime modes mismatch")


def deploy(*, release_id: str, confirm: bool, remote: Remote | None = None, manifest_path: Path = DEFAULT_MANIFEST, candidates_dir: Path = DEFAULT_CANDIDATES) -> str:
    if not confirm:
        raise ReleaseError("deploy requires --confirm")
    if not release_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", release_id):
        raise ReleaseError("deploy requires safe --release-id")
    manifest = load_manifest(manifest_path)
    targets = targets_from_manifest(manifest)
    verify_candidate_hashes(targets, candidates_dir)
    candidate_release_id = validate_candidate_identity(targets, candidates_dir)
    if release_id != candidate_release_id:
        raise ReleaseError("deploy release_id does not match candidate identity")
    remote = remote or SshRemote()
    backup_dir = ""
    pre_write_probe: dict[str, Any] | None = None
    try:
        pre_write_probe = assert_remote_base(remote, manifest, targets)
        backup_dir = backup(remote, manifest, targets, release_id)
        upload_candidates(remote, manifest, targets, candidates_dir)
        remote_compile_import(remote, manifest, targets)
        set_runtime_modes(remote, manifest)
        restart_api(remote, manifest)
        verify_remote(remote, manifest, targets)
    except Exception as deploy_exc:
        if backup_dir:
            try:
                rollback(remote, manifest, targets, backup_dir, pre_write_probe or {})
            except Exception as rollback_exc:
                raise ReleaseError(f"deploy failed: {sanitize(str(deploy_exc))}; rollback failed: {sanitize(str(rollback_exc))}") from deploy_exc
        raise
    return sanitize(f"deploy=ok release_id={release_id} backup={backup_dir}\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local preflight and guarded deploy for manager rewriter release bundle")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("preflight")
    deploy_parser = sub.add_parser("deploy")
    deploy_parser.add_argument("--release-id", required=True)
    deploy_parser.add_argument("--confirm", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = args.command or "preflight"
    try:
        if command == "preflight":
            print(preflight(), end="")
        elif command == "deploy":
            print(deploy(release_id=args.release_id, confirm=args.confirm), end="")
        else:
            raise ReleaseError(f"unknown command: {command}")
    except Exception as exc:
        print("ERROR: " + sanitize(str(exc)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
