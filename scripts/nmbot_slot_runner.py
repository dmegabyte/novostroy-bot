#!/usr/bin/env python3
"""Run one exact immutable V6 release in a prepared TEST/PROD A/B slot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, NoReturn

try:
    from scripts.nmbot_release_registry import normalize_profile, normalize_slot, validate_release_id
except ImportError:  # direct scripts/ execution
    from nmbot_release_registry import normalize_profile, normalize_slot, validate_release_id


SLOT_SCHEMA = "nmbot.slot_descriptor.v1"
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DESCRIPTOR_KEYS = frozenset({"schema", "profile", "slot", "release_id", "release_root", "manifest_path", "manifest_sha256", "env_file", "data_root", "port"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SlotRunnerError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_release_tree(release_root: Path, *, release_id: str, manifest_path: Path, manifest_sha256: str) -> None:
    if not manifest_path.is_file() or manifest_path.is_symlink() or not SHA256_RE.fullmatch(manifest_sha256):
        raise SlotRunnerError("release manifest identity is invalid")
    if _sha256_file(manifest_path) != manifest_sha256:
        raise SlotRunnerError("release manifest hash mismatch")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SlotRunnerError("release manifest is missing or malformed") from exc
    rows = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "nmbot.atomic_release.v1" or manifest.get("release_id") != release_id or not isinstance(rows, list):
        raise SlotRunnerError("release manifest does not match slot release")
    expected: dict[str, str] = {}
    for row in rows:
        relative = str(row.get("path") or "") if isinstance(row, dict) and set(row) == {"path", "sha256"} else ""
        sha = str(row.get("sha256") or "") if isinstance(row, dict) else ""
        relative_path = Path(relative)
        if not relative or relative_path.is_absolute() or ".." in relative_path.parts or relative in expected or not SHA256_RE.fullmatch(sha):
            raise SlotRunnerError("release manifest file row is invalid")
        expected[relative] = sha
    actual = {path.relative_to(release_root).as_posix() for path in release_root.rglob("*") if path.is_file() or path.is_symlink()}
    if actual != set(expected):
        raise SlotRunnerError("release file set differs from manifest")
    for relative, sha in expected.items():
        path = release_root / relative
        if not path.is_file() or path.is_symlink() or _sha256_file(path) != sha:
            raise SlotRunnerError("release file hash mismatch")


def _absolute_path(value: Any, *, field: str, must_exist: bool) -> Path:
    raw = str(value or "").strip()
    path = Path(raw)
    if not raw or not path.is_absolute() or ".." in path.parts:
        raise SlotRunnerError(f"{field} must be an absolute normalized path")
    if must_exist and (not path.exists() or path.is_symlink()):
        raise SlotRunnerError(f"{field} must exist and must not be a symlink")
    return path


def load_descriptor(path: Path) -> dict[str, Any]:
    descriptor_path = Path(path)
    if not descriptor_path.is_absolute() or descriptor_path.is_symlink():
        raise SlotRunnerError("descriptor path must be absolute and must not be a symlink")
    try:
        raw = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SlotRunnerError("slot descriptor is missing or malformed") from exc
    if not isinstance(raw, dict) or set(raw) != DESCRIPTOR_KEYS or raw.get("schema") != SLOT_SCHEMA:
        raise SlotRunnerError("slot descriptor schema mismatch")
    profile = normalize_profile(raw.get("profile"))
    slot = normalize_slot(raw.get("slot"))
    release_id = validate_release_id(raw.get("release_id"))
    release_root = _absolute_path(raw.get("release_root"), field="release_root", must_exist=True)
    manifest_path = _absolute_path(raw.get("manifest_path"), field="manifest_path", must_exist=True)
    manifest_sha256 = str(raw.get("manifest_sha256") or "")
    env_file = _absolute_path(raw.get("env_file"), field="env_file", must_exist=True)
    if not env_file.is_file():
        raise SlotRunnerError("env_file must be a regular file")
    data_root = _absolute_path(raw.get("data_root"), field="data_root", must_exist=False)
    try:
        port = int(raw.get("port"))
    except (TypeError, ValueError) as exc:
        raise SlotRunnerError("port is invalid") from exc
    if isinstance(raw.get("port"), bool) or not 1024 <= port <= 65535:
        raise SlotRunnerError("port is invalid")
    _verify_release_tree(release_root, release_id=release_id, manifest_path=manifest_path, manifest_sha256=manifest_sha256)
    entrypoint = release_root / "scripts" / "nmbot_api_server.py"
    identity_path = release_root / "release_identity" / "nmbot_release_identity.json"
    if not entrypoint.is_file() or entrypoint.is_symlink():
        raise SlotRunnerError("release entrypoint is missing")
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SlotRunnerError("release identity is missing or malformed") from exc
    if not isinstance(identity, dict) or identity.get("schema") != "nmbot.release_identity.v1" or identity.get("release_id") != release_id:
        raise SlotRunnerError("release identity mismatch")
    return {
        "schema": SLOT_SCHEMA,
        "profile": profile,
        "slot": slot,
        "release_id": release_id,
        "release_root": str(release_root),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "env_file": str(env_file),
        "data_root": str(data_root),
        "port": port,
    }


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SlotRunnerError("env_file cannot be read") from exc
    for line_number, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            raise SlotRunnerError(f"env_file line {line_number} is invalid")
        name, encoded = stripped.split("=", 1)
        name = name.strip()
        if not ENV_NAME_RE.fullmatch(name):
            raise SlotRunnerError(f"env_file line {line_number} has an invalid name")
        try:
            parsed = shlex.split(encoded, comments=True, posix=True)
        except ValueError as exc:
            raise SlotRunnerError(f"env_file line {line_number} has invalid quoting") from exc
        if len(parsed) > 1:
            raise SlotRunnerError(f"env_file line {line_number} must contain one value")
        values[name] = parsed[0] if parsed else ""
    return values


def build_environment(descriptor: Mapping[str, Any], *, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    release_root = Path(str(descriptor["release_root"]))
    data_root = Path(str(descriptor["data_root"]))
    data_root.mkdir(parents=True, exist_ok=True)
    outbox = data_root / "crm_callback_outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    dialogue = data_root / "dialogue"
    dialogue.mkdir(parents=True, exist_ok=True)
    logs = data_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = dict(base_env or os.environ)
    env.update(load_env_file(Path(str(descriptor["env_file"]))))
    env.update({
        "NMBOT_CONTOUR_PROFILE": str(descriptor["profile"]),
        "NMBOT_API_HOST": "127.0.0.1",
        "NMBOT_API_PORT": str(descriptor["port"]),
        "NMBOT_API_STATE_FILE": str(data_root / "nmbot_api_state.json"),
        "NMBOT_RUNTIME_VERSION_FILE": str(data_root / "nmbot_runtime_version.json"),
        "NMBOT_CALLBACK_OUTBOX_DIR": str(outbox),
        "NMBOT_DIALOGUE_JOURNAL": str(dialogue / "dialogue.jsonl"),
        "NMBOT_LOGS_DIR": str(logs),
        "NMBOT_RELEASE_IDENTITY_FILE": str(release_root / "release_identity" / "nmbot_release_identity.json"),
        "PYTHONPATH": f"{release_root}:{release_root / 'scripts'}",
    })
    return env


def run_descriptor(
    descriptor_path: Path,
    *,
    exec_fn: Callable[[str, list[str], Mapping[str, str]], Any] = os.execve,
) -> NoReturn:
    descriptor = load_descriptor(Path(descriptor_path))
    release_root = Path(descriptor["release_root"])
    environment = build_environment(descriptor)
    argv = [
        sys.executable,
        str(release_root / "scripts" / "nmbot_api_server.py"),
        "--host",
        "127.0.0.1",
        "--port",
        str(descriptor["port"]),
    ]
    os.chdir(release_root)
    exec_fn(sys.executable, argv, environment)
    raise SlotRunnerError("slot process exec unexpectedly returned")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", type=Path, required=True)
    args = parser.parse_args()
    try:
        run_descriptor(args.descriptor.expanduser().resolve())
    except SlotRunnerError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
