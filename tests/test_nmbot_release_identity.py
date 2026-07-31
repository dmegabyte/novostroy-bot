from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_release_identity.py"


def load_identity_module():
    spec = importlib.util.spec_from_file_location("nmbot_release_identity_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_safe_release_id_validation() -> None:
    mod = load_identity_module()

    assert mod.validate_release_id("release_2026.07-22") == "release_2026.07-22"
    for unsafe in ("", "../prod", "prod;rm", "-prod", "prod/id", "x" * 81):
        try:
            mod.validate_release_id(unsafe)
        except mod.ReleaseIdentityError:
            pass
        else:  # pragma: no cover - explicit failure branch
            raise AssertionError(f"unsafe id accepted: {unsafe!r}")


def test_manifest_is_deterministic_and_excludes_identity_json() -> None:
    mod = load_identity_module()
    stamp = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    tracked = ["scripts/nmbot_release_identity.py", "data/nmbot_release_identity.json"]

    first = mod.create_identity_manifest("rel-1", tracked_files=tracked, generated_at=stamp)
    second = mod.create_identity_manifest("rel-1", tracked_files=reversed(tracked), generated_at=stamp)

    assert first == second
    assert first["schema"] == "nmbot.release_identity.v1"
    assert first["release_id"] == "rel-1"
    assert first["generated_at"] == "2026-07-22T12:00:00Z"
    assert [item["path"] for item in first["tracked_files"]] == ["scripts/nmbot_release_identity.py"]
    assert len(first["tracked_files"][0]["sha256"]) == 64


def test_write_read_and_unknown_for_missing_or_malformed(tmp_path: Path, monkeypatch) -> None:
    mod = load_identity_module()
    target = tmp_path / "identity.json"
    monkeypatch.setenv("NMBOT_RELEASE_IDENTITY_FILE", str(target))

    assert mod.current_release_id() == "UNKNOWN"
    manifest = mod.create_identity_manifest(
        "rel-local",
        tracked_files=["scripts/nmbot_release_identity.py"],
        generated_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )
    mod.write_identity_manifest(manifest)
    assert mod.current_release_id() == "rel-local"

    target.write_text('{"schema":"bad","release_id":"../unsafe"}', encoding="utf-8")
    assert mod.current_release_id() == "UNKNOWN"


def test_cli_create_requires_write_and_uses_override(tmp_path: Path) -> None:
    target = tmp_path / "identity.json"
    env = {**dict(), **{"NMBOT_RELEASE_IDENTITY_FILE": str(target)}}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "create", "--release-id", "rel-cli"],
        cwd=ROOT,
        env={**__import__("os").environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 2
    assert not target.exists()

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "create", "--release-id", "rel-cli", "--write"],
        cwd=ROOT,
        env={**__import__("os").environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert json.loads(target.read_text(encoding="utf-8"))["release_id"] == "rel-cli"
