#!/usr/bin/env python3
"""Unified nmbot Jivo deploy and version tool.

Examples:
    python3 scripts/nmbot_release.py status
    python3 scripts/nmbot_release.py deploy
    python3 scripts/nmbot_release.py deploy scripts/nmbot_api_server.py

The tool prints hashes and service metadata, never secret values.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

from nmbot_release_identity import create_identity_manifest, write_identity_manifest


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILES = tuple(sorted((
    "followup_intent_classifier.py",
    "prompts/v2_response_composer.txt",
    "prompts/v2_response_formatter.txt",
    "prompts/v2_response_writer.txt",
    "prompts/v2_manager_rewriter.txt",
    "prompts/v2_search_mcp.txt",
    "search_profiles.py",
    "scripts/nmbot_api_server.py",
    "scripts/nmbot_crm_outbox.py",
    "scripts/nmbot_gateway_client.py",
    "scripts/nmbot_diag.sh",
    "scripts/nmbot_jivo_dialogue_diagnose.py",
    "scripts/nmbot_n8n_bridge_server.py",
    "scripts/nmbot_jivo_trace_analyze.py",
    "scripts/nmbot_planner_context.py",
    "scripts/nmbot_release.py",
    "scripts/nmbot_release_identity.py",
    "scripts/nmbot_runtime_adapter.py",
    "scripts/dialogue_journal.py",
    "scripts/nmbot_dialogue_report.py",
    "data/nmbot_release_identity.json",
    *(str(path.relative_to(ROOT)) for path in (ROOT / "nmbot_v2").glob("*.py")),
)))
HOST = "neiro@193.107.155.236"
PORT = "1905"
REMOTE_ROOT = "/home/neiro/novostroy-bot"
SERVICES = ("novostroy-bot-api.service", "novostroy-bot-n8n-bridge.service")
IDENTITY_DEPLOY_FILES = ("scripts/nmbot_release_identity.py", "data/nmbot_release_identity.json")
LEGACY_DEPLOY_DISABLED_MESSAGE = (
    "legacy partial production deploy is disabled; use snapshot-first atomic release: "
    "snapshot-vps-source -> compare-snapshot -> prepare-worktree -> build-from-worktree -> deploy"
)
HEALTH_URLS = {
    "api": "http://127.0.0.1:8088/health",
    "bridge": "http://127.0.0.1:8093/health",
}


def run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, input=input_text, text=True, capture_output=True, check=False)


def ssh(command: str) -> subprocess.CompletedProcess[str]:
    return run(["ssh", "-p", PORT, "-o", "BatchMode=yes", HOST, command])


def local_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remote_status(files: tuple[str, ...]) -> dict[str, object]:
    script = f"""
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

root = Path({REMOTE_ROOT!r})
identity_file = root / "data" / "nmbot_release_identity.json"

def read_identity():
    try:
        data = json.loads(identity_file.read_text(encoding="utf-8")) if identity_file.is_file() else None
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    release_id = str(data.get("release_id") or "")
    if not release_id or release_id.startswith("-") or "/" in release_id or "\\\\" in release_id or len(release_id) > 80:
        data = dict(data)
        data["release_id"] = "UNKNOWN"
    return data
services = {list(SERVICES)!r}
health_urls = {HEALTH_URLS!r}
files = {list(files)!r}

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

service_status = {{}}
for service in services:
    props = subprocess.run(
        ["systemctl", "--user", "show", service, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "ActiveEnterTimestamp", "--value"],
        capture_output=True, text=True, check=False,
    )
    service_status[service] = props.stdout.splitlines()
health = {{name: json.loads(urllib.request.urlopen(url, timeout=10).read().decode()) for name, url in health_urls.items()}}
print(json.dumps({{
    "services": service_status,
    "hashes": {{path: digest(root / path) if (root / path).is_file() else None for path in files}},
    "health": health,
    "identity": read_identity(),
}}, ensure_ascii=False))
"""
    proc = run(["ssh", "-p", PORT, "-o", "BatchMode=yes", HOST, "python3", "-"], input_text=script)
    if proc.returncode != 0:
        raise RuntimeError((proc.stdout + proc.stderr).strip()[-2000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def backup_command(files: tuple[str, ...]) -> str:
    copies = []
    for relative in files:
        quoted = shlex.quote(relative)
        remote_parent = shlex.quote(str(Path(relative).parent))
        copies.append(
            f"if [ -f {quoted} ]; then mkdir -p \"$dir\"/{remote_parent} && cp {quoted} \"$dir\"/{quoted}; fi"
        )
    return (
        "cd " + shlex.quote(REMOTE_ROOT)
        + " && ts=$(date +%Y%m%d-%H%M%S) && dir=backups/deploy-$ts && mkdir -p \"$dir\" && "
        + " && ".join(copies)
        + " && printf '%s\\n' \"$dir\""
    )


def remote_mkdir_command(relative: str) -> str:
    parent = str(Path(relative).parent)
    return "mkdir -p " + shlex.quote(f"{REMOTE_ROOT}/{parent}")


def print_status(files: tuple[str, ...]) -> None:
    remote = remote_status(files)
    labels = ("MainPID", "ActiveState", "SubState", "ActiveEnterTimestamp")
    for service, service_values in remote["services"].items():
        service_text = ", ".join(f"{label}={value}" for label, value in zip(labels, service_values))
        print(f"remote_service={service}, {service_text}")
    print(f"remote_health={remote['health']}")
    identity = remote.get("identity") if isinstance(remote.get("identity"), dict) else {}
    release_id = identity.get("release_id") if isinstance(identity, dict) else None
    print(f"remote_release_id={release_id if isinstance(release_id, str) else 'UNKNOWN'}")
    print("local_hashes:")
    for relative in files:
        path = ROOT / relative
        print(f"  {relative} sha256:{local_hash(path)}")
    print("remote_hashes:")
    for relative, digest in remote["hashes"].items():
        print(f"  {relative} " + (f"sha256:{digest}" if digest else "missing"))


def deployment_files(files: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*files, *IDENTITY_DEPLOY_FILES)))


def deploy(files: tuple[str, ...], *, release_id: str) -> None:
    raise RuntimeError(LEGACY_DEPLOY_DISABLED_MESSAGE)
    manifest = create_identity_manifest(release_id, tracked_files=files)
    write_identity_manifest(manifest)
    for relative in files:
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(path)

    backup = ssh(backup_command(files))
    if backup.returncode != 0:
        raise RuntimeError((backup.stdout + backup.stderr).strip())
    print(f"backup={backup.stdout.strip().splitlines()[-1]}")

    for relative in files:
        mkdir = ssh(remote_mkdir_command(relative))
        if mkdir.returncode != 0:
            raise RuntimeError((mkdir.stdout + mkdir.stderr).strip())
        destination = f"{HOST}:{REMOTE_ROOT}/{relative}"
        proc = run(["scp", "-P", PORT, str(ROOT / relative), destination])
        if proc.returncode != 0:
            raise RuntimeError((proc.stdout + proc.stderr).strip())

    py_files = tuple(path for path in files if path.endswith(".py"))
    compile_cmd = "cd " + shlex.quote(REMOTE_ROOT) + " && python3 -m py_compile " + " ".join(map(shlex.quote, py_files))
    compiled = ssh(compile_cmd)
    if compiled.returncode != 0:
        raise RuntimeError((compiled.stdout + compiled.stderr).strip())
    restarted = ssh("systemctl --user restart " + " ".join(map(shlex.quote, SERVICES)))
    if restarted.returncode != 0:
        raise RuntimeError((restarted.stdout + restarted.stderr).strip())
    print("restart=ok")
    print_status(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy and inspect the nmbot Jivo runtime")
    parser.add_argument("command", choices=("status", "deploy"))
    parser.add_argument("rest", nargs=argparse.REMAINDER, help="deploy/status options and files")
    args = parser.parse_args(argv)
    release_id = None
    if args.command == "deploy":
        deploy_parser = argparse.ArgumentParser(prog=f"{parser.prog} deploy")
        deploy_parser.add_argument("--release-id", required=True, help="Required immutable release id for deploy; safe token only")
        deploy_parser.add_argument("files", nargs="*", default=list(DEFAULT_FILES), help="runtime files relative to project root")
        deploy_args = deploy_parser.parse_args(args.rest)
        release_id = deploy_args.release_id
        files = deployment_files(tuple(deploy_args.files or DEFAULT_FILES))
    else:
        files = tuple(args.rest or DEFAULT_FILES)
    try:
        if args.command == "status":
            print_status(files)
        else:
            deploy(files, release_id=release_id or "")
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
