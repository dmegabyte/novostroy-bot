#!/usr/bin/env python3
"""Safe local release identity manifest for nmbot.

This module is stdlib-only and never shells out or reads secret values.  Normal
read/show paths are read-only; manifest creation happens only with --write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IDENTITY_FILE = ROOT / "data" / "nmbot_release_identity.json"
IDENTITY_FILE_ENV = "NMBOT_RELEASE_IDENTITY_FILE"
SCHEMA = "nmbot.release_identity.v1"
UNKNOWN_RELEASE_ID = "UNKNOWN"
SAFE_RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
DEFAULT_TRACKED_FILES = tuple(sorted((
    "followup_intent_classifier.py",
    "prompts/v2_response_composer.txt",
    "prompts/v2_response_formatter.txt",
    "prompts/v2_response_writer.txt",
    "prompts/v2_manager_rewriter.txt",
    "prompts/v5_manager_rewriter.txt",
    "prompts/v2_search_mcp.txt",
    "search_profiles.py",
    "scripts/dialogue_journal.py",
    "scripts/nmbot_api_server.py",
    "scripts/nmbot_crm_outbox.py",
    "scripts/nmbot_dialogue_report.py",
    "scripts/nmbot_gateway_client.py",
    "scripts/nmbot_planner_context.py",
    "scripts/nmbot_release_identity.py",
    "scripts/nmbot_runtime_adapter.py",
    *(str(path.relative_to(ROOT)) for path in (ROOT / "nmbot_v2").glob("*.py")),
)))


class ReleaseIdentityError(ValueError):
    pass


def is_safe_release_id(value: Any) -> bool:
    text = str(value or "").strip()
    if text in {"", ".", "..", UNKNOWN_RELEASE_ID}:
        return text == UNKNOWN_RELEASE_ID
    if text.startswith("-") or "/" in text or "\\" in text:
        return False
    return bool(SAFE_RELEASE_ID_RE.fullmatch(text))


def validate_release_id(value: Any) -> str:
    text = str(value or "").strip()
    if text == UNKNOWN_RELEASE_ID:
        return text
    if not is_safe_release_id(text):
        raise ReleaseIdentityError("unsafe release_id; use 1-80 letters/digits/dot/underscore/hyphen, not a path or shell token")
    return text


def identity_path() -> Path:
    raw = os.getenv(IDENTITY_FILE_ENV)
    path = Path(raw).expanduser() if raw else DEFAULT_IDENTITY_FILE
    if "\x00" in str(path):
        raise ReleaseIdentityError("unsafe identity path")
    return path


def _safe_relative_file(value: str) -> str:
    text = str(value).strip().replace("\\", "/")
    if not text or text.startswith("/") or "\x00" in text:
        raise ReleaseIdentityError(f"unsafe tracked file path: {value!r}")
    parts = Path(text).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ReleaseIdentityError(f"unsafe tracked file path: {value!r}")
    return text


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_identity_manifest(
    release_id: str,
    *,
    tracked_files: Iterable[str] = DEFAULT_TRACKED_FILES,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    safe_release_id = validate_release_id(release_id)
    if safe_release_id == UNKNOWN_RELEASE_ID:
        raise ReleaseIdentityError("release_id UNKNOWN is reserved for missing/malformed runtime identity")
    files: list[dict[str, str]] = []
    seen: set[str] = set()
    identity_relative = str(DEFAULT_IDENTITY_FILE.relative_to(ROOT))
    for raw in tracked_files:
        relative = _safe_relative_file(raw)
        if relative == identity_relative or relative in seen:
            continue
        seen.add(relative)
        path = ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        files.append({"path": relative, "sha256": sha256_file(path)})
    stamp = generated_at or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return {
        "schema": SCHEMA,
        "release_id": safe_release_id,
        "generated_at": stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tracked_files": sorted(files, key=lambda item: item["path"]),
    }


def write_identity_manifest(manifest: dict[str, Any], path: Path | None = None) -> Path:
    target = path or identity_path()
    if target.exists() and target.is_dir():
        raise ReleaseIdentityError("identity path points to a directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return target


def read_identity(path: Path | None = None) -> dict[str, Any]:
    target = path or identity_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ReleaseIdentityError):
        return {"schema": SCHEMA, "release_id": UNKNOWN_RELEASE_ID, "source": str(target)}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "release_id": UNKNOWN_RELEASE_ID, "source": str(target)}
    release_id = str(data.get("release_id") or "").strip()
    if not is_safe_release_id(release_id) or release_id == UNKNOWN_RELEASE_ID:
        return {"schema": SCHEMA, "release_id": UNKNOWN_RELEASE_ID, "source": str(target)}
    return {"schema": SCHEMA, "release_id": release_id, "source": str(target)}


def current_release_id() -> str:
    return str(read_identity().get("release_id") or UNKNOWN_RELEASE_ID)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read or explicitly create the local nmbot release identity manifest.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("read", help="Print the current safe release_id; read-only")
    sub.add_parser("show", help="Print the safe identity summary as JSON; read-only")
    create = sub.add_parser("create", help="Create/update the identity manifest only with --write")
    create.add_argument("--release-id", required=True)
    create.add_argument("--write", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    command = args.command or "read"
    try:
        if command == "read":
            print(current_release_id())
            return 0
        if command == "show":
            print(json.dumps(read_identity(), ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        manifest = create_identity_manifest(args.release_id)
        if not args.write:
            print("ERROR: create requires --write", file=sys.stderr)
            return 2
        target = write_identity_manifest(manifest)
        print(f"identity={target}")
        print(json.dumps(read_identity(target), ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ReleaseIdentityError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
