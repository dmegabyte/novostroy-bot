#!/usr/bin/env python3
"""Guarded publisher for the isolated `/deep-chat-echo/` static directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import tarfile
import tempfile
from pathlib import Path


HOST = "neiro@193.107.155.236"
PORT = "1905"
PUBLIC_ROOT = "/home/neiro/public"
SLUG = "deep-chat-echo"


class ReleaseError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def validate_artifact(path: Path) -> dict:
    manifest_path = path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError("invalid static artifact manifest") from exc
    if manifest.get("schema") != "nmbot.static_echo.v1" or not isinstance(manifest.get("release_id"), str):
        raise ReleaseError("unexpected static artifact identity")
    expected = manifest.get("files")
    if not isinstance(expected, list) or not expected:
        raise ReleaseError("static artifact has no files")
    expected_paths: set[str] = set()
    for entry in expected:
        relative = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
            raise ReleaseError("unsafe static artifact path")
        expected_paths.add(relative)
        candidate = path / relative
        if not candidate.is_file() or sha256(candidate) != entry.get("sha256"):
            raise ReleaseError("static artifact hash mismatch")
    actual_paths = {str(item.relative_to(path)) for item in path.rglob("*") if item.is_file()}
    if actual_paths != expected_paths | {"manifest.json"}:
        raise ReleaseError("static artifact contains unexpected files")
    return manifest


def create_archive(artifact: Path) -> Path:
    archive = artifact.parent / f"{artifact.name}.tar.gz"
    if archive.exists():
        raise ReleaseError("archive destination already exists")
    with tarfile.open(archive, "x:gz") as tar:
        for path in sorted(artifact.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=str(path.relative_to(artifact)), recursive=False)
    return archive


def run_ssh(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["ssh", "-p", PORT, HOST, command], text=True, capture_output=True, check=False)


def recon() -> dict:
    command = (
        "test -d {root} && "
        "status=$(curl --fail --silent --show-error --max-time 5 --output /dev/null --write-out '%{{http_code}}' http://127.0.0.1:8765/) && "
        "case \"$status\" in 2*|3*) printf '{{\"static_root\":true,\"http_ready\":true}}' ;; *) printf '{{\"static_root\":true,\"http_ready\":false}}' ;; esac"
    ).format(root=shlex.quote(PUBLIC_ROOT))
    result = run_ssh(command)
    if result.returncode != 0:
        raise ReleaseError("static host recon failed")
    payload = json.loads(result.stdout)
    if payload != {"static_root": True, "http_ready": True}:
        raise ReleaseError("static host is not HTTP-ready")
    return payload


def deploy(artifact: Path, *, confirm: bool) -> str:
    if not confirm:
        raise ReleaseError("deploy requires --confirm")
    manifest = validate_artifact(artifact)
    archive = create_archive(artifact)
    release_id = manifest["release_id"]
    remote_base = f"{PUBLIC_ROOT}/.nmbot-static-releases/{SLUG}"
    remote_release = f"{remote_base}/{release_id}"
    try:
        remote_check = " && ".join((f"mkdir -p {shlex.quote(remote_base)}", f"test ! -e {shlex.quote(remote_release)}", f"test ! -L {shlex.quote(remote_release)}"))
        if run_ssh(remote_check).returncode != 0:
            raise ReleaseError("remote static release destination already exists or is unavailable")
        remote_archive = f"{remote_base}/.{release_id}.tar.gz"
        copy = subprocess.run(["scp", "-P", PORT, str(archive), f"{HOST}:{remote_archive}"], text=True, capture_output=True, check=False)
        if copy.returncode != 0:
            raise ReleaseError("static artifact upload failed")
        publish = " && ".join((f"mkdir {shlex.quote(remote_release)}", f"tar -xzf {shlex.quote(remote_archive)} -C {shlex.quote(remote_release)}", f"rm -f {shlex.quote(remote_archive)}", f"ln -sfn {shlex.quote('.nmbot-static-releases/' + SLUG + '/' + release_id)} {shlex.quote(PUBLIC_ROOT + '/.' + SLUG + '.next')}", f"mv -Tf {shlex.quote(PUBLIC_ROOT + '/.' + SLUG + '.next')} {shlex.quote(PUBLIC_ROOT + '/' + SLUG)}", f"test -r {shlex.quote(PUBLIC_ROOT + '/' + SLUG + '/index.html')}"))
        if run_ssh(publish).returncode != 0:
            raise ReleaseError("static publish failed; inspect remote artifact before retrying")
    finally:
        archive.unlink(missing_ok=True)
    return release_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy one isolated NMBot static echo artifact")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("recon")
    deploy_parser = sub.add_parser("deploy")
    deploy_parser.add_argument("--artifact", type=Path, required=True)
    deploy_parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if args.command == "recon":
        print(json.dumps(recon(), ensure_ascii=False))
    else:
        print(json.dumps({"deploy":"ok", "release_id":deploy(args.artifact, confirm=args.confirm)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
