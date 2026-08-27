#!/usr/bin/env python3
"""Deterministic V6-only artifact builder; no deploy or remote operations."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


SCHEMA = "nmbot.atomic_release.v1"
IDENTITY_SCHEMA = "nmbot.release_identity.v1"
RELEASE_IDENTITY_PATH = "release_identity/nmbot_release_identity.json"
V6_ONLY_PROFILE = "v6-only"
SAFE_RELEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")

V6_API_FILES = (
    "requirements.txt",
    "nmbot_v6/__init__.py",
    "nmbot_v6/phone.py",
    "nmbot_v6/simple_contract.py",
    "nmbot_v6/simple_gateway.py",
    "nmbot_v6/simple_runtime.py",
    "nmbot_v6/simple_state.py",
    "nmbot_v6/url_card.py",
    "prompts/v6_simple_answer_writer.txt",
    "prompts/v6_simple_search_agent.txt",
    "scripts/__init__.py",
    "scripts/dialogue_journal.py",
    "scripts/nmbot_api_server.py",
    "scripts/nmbot_callback_crm_control.py",
    "scripts/nmbot_crm_outbox.py",
    "scripts/nmbot_gateway_client.py",
    "scripts/nmbot_release_identity.py",
    "scripts/nmbot_v6_jivo_smoke.py",
    "scripts/nmbot_v6_api.py",
    "scripts/nmbot_v6_simple_adapter.py",
)
@dataclass(frozen=True)
class BuildResult:
    manifest: Path
    archive: Path


class AtomicReleaseError(ValueError):
    pass


def _validate_release_id(value: Any) -> str:
    release_id = str(value or "").strip()
    if not SAFE_RELEASE_RE.fullmatch(release_id) or release_id in {".", "..", "UNKNOWN"}:
        raise AtomicReleaseError("unsafe release_id")
    return release_id


def _safe_relative(value: str) -> str:
    raw = str(value).replace(os.sep, "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or str(path) != raw:
        raise AtomicReleaseError("unsafe artifact path")
    return raw


def _profile_contract(profile: str) -> tuple[str, tuple[str, ...], list[str]]:
    contracts = {
        V6_ONLY_PROFILE: ("api", V6_API_FILES, ["scripts.nmbot_api_server"]),
    }
    try:
        return contracts[profile]
    except KeyError as exc:
        raise AtomicReleaseError("unknown V6 artifact profile") from exc


def _git_source_provenance(root: Path) -> dict[str, str]:
    def run(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args],
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AtomicReleaseError("Git source provenance check failed") from exc
        if result.returncode != 0:
            raise AtomicReleaseError("Git source provenance check failed")
        return result.stdout.strip()

    git_sha = run("rev-parse", "--verify", "HEAD").lower()
    tree_sha = run("rev-parse", "--verify", "HEAD^{tree}").lower()
    if not GIT_SHA_RE.fullmatch(git_sha) or not GIT_SHA_RE.fullmatch(tree_sha):
        raise AtomicReleaseError("Git source identity is invalid")
    if run("status", "--porcelain=v1", "--untracked-files=all"):
        raise AtomicReleaseError("source tree must be clean before artifact build")
    unsigned = {"git_sha": git_sha, "git_tree_sha": tree_sha, "tree_state": "clean"}
    receipt = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {**unsigned, "clean_receipt_sha256": receipt}


def _git_blob(root: Path, git_sha: str, relative: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{git_sha}:{relative}"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AtomicReleaseError("Git source blob verification failed") from exc
    if result.returncode != 0:
        raise AtomicReleaseError(f"allowlisted file is not present in source Git tree: {relative}")
    return result.stdout


def _source_payload(root: Path, relative: str) -> bytes:
    safe = _safe_relative(relative)
    path = root / safe
    cursor = root
    for part in PurePosixPath(safe).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise AtomicReleaseError(f"missing or unsafe source file: {safe}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AtomicReleaseError(f"missing or unsafe source file: {safe}") from exc
    if root not in resolved.parents or not resolved.is_file():
        raise AtomicReleaseError(f"missing or unsafe source file: {safe}")
    return resolved.read_bytes()


def _identity_payload(release_id: str, source_rows: list[dict[str, str]]) -> bytes:
    identity = {
        "schema": IDENTITY_SCHEMA,
        "release_id": release_id,
        "generated_at": "1970-01-01T00:00:00Z",
        "tracked_files": source_rows,
    }
    return (json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _tar_bytes(files: Mapping[str, bytes]) -> bytes:
    raw_tar = io.BytesIO()
    with tarfile.open(fileobj=raw_tar, mode="w", format=tarfile.USTAR_FORMAT) as bundle:
        for relative in sorted(files):
            payload = files[relative]
            info = tarfile.TarInfo(relative)
            info.size = len(payload)
            info.mode = 0o755 if relative.startswith("scripts/") and relative.endswith(".py") else 0o600 if relative.startswith("release_identity/") else 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            bundle.addfile(info, io.BytesIO(payload))
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0, compresslevel=9) as zipper:
        zipper.write(raw_tar.getvalue())
    return compressed.getvalue()


def _atomic_write(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build(*, release_id: str, out_dir: Path, root: Path, profile: str = V6_ONLY_PROFILE) -> BuildResult:
    rid = _validate_release_id(release_id)
    source_input = Path(root).expanduser()
    destination_input = Path(out_dir).expanduser()
    if not source_input.is_dir() or source_input.is_symlink() or (destination_input.exists() and destination_input.is_symlink()):
        raise AtomicReleaseError("source/output root is invalid")
    source_root = source_input.resolve()
    destination = destination_input.resolve()
    scope, allowed_files, import_modules = _profile_contract(profile)
    source_provenance = _git_source_provenance(source_root)
    if len(set(allowed_files)) != len(allowed_files):
        raise AtomicReleaseError("artifact allowlist contains duplicates")

    payloads: dict[str, bytes] = {}
    for relative in allowed_files:
        payload = _source_payload(source_root, relative)
        if payload != _git_blob(source_root, source_provenance["git_sha"], relative):
            raise AtomicReleaseError(f"allowlisted file differs from source Git tree: {relative}")
        payloads[relative] = payload
    if _git_source_provenance(source_root) != source_provenance:
        raise AtomicReleaseError("Git source changed while artifact was being built")
    source_rows = [
        {"path": relative, "sha256": hashlib.sha256(payloads[relative]).hexdigest()}
        for relative in sorted(payloads)
    ]
    payloads[RELEASE_IDENTITY_PATH] = _identity_payload(rid, source_rows)
    rows = [
        {"path": relative, "sha256": hashlib.sha256(payloads[relative]).hexdigest()}
        for relative in sorted(payloads)
    ]
    archive_payload = _tar_bytes(payloads)
    archive_name = f"nmbot-{rid}.tar.gz"
    manifest = {
        "schema_version": SCHEMA,
        "scope": scope,
        "profile": profile,
        "release_id": rid,
        "archive_name": archive_name,
        "archive_sha256": hashlib.sha256(archive_payload).hexdigest(),
        "import_modules": import_modules,
        "source_provenance": source_provenance,
        "files": rows,
    }
    manifest_payload = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    archive_path = destination / archive_name
    manifest_path = destination / f"nmbot-{rid}.manifest.json"
    existing = (archive_path.exists(), manifest_path.exists())
    if any(existing):
        if not all(existing) or archive_path.is_symlink() or manifest_path.is_symlink():
            raise AtomicReleaseError("release output already exists in an incomplete or unsafe state")
        if archive_path.read_bytes() != archive_payload or manifest_path.read_bytes() != manifest_payload:
            raise AtomicReleaseError("release output already exists with different bytes")
        return BuildResult(manifest=manifest_path, archive=archive_path)
    _atomic_write(archive_path, archive_payload)
    _atomic_write(manifest_path, manifest_payload)
    return BuildResult(manifest=manifest_path, archive=archive_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--profile", choices=(V6_ONLY_PROFILE,), default=V6_ONLY_PROFILE)
    args = parser.parse_args(argv)
    try:
        result = build(release_id=args.release_id, out_dir=args.out_dir, root=args.root, profile=args.profile)
    except (OSError, AtomicReleaseError) as exc:
        parser.error(str(exc))
    print(json.dumps({"manifest": str(result.manifest), "archive": str(result.archive)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
