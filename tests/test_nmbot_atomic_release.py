from __future__ import annotations

import json
import re
import tarfile
from pathlib import Path

import pytest

from scripts import nmbot_atomic_release as atomic
from scripts.nmbot_release_control import ReleaseControlError, inspect_artifact


ROOT = Path(__file__).resolve().parents[1]


def _manifest(result) -> dict:
    return json.loads(result.manifest.read_text(encoding="utf-8"))


def test_v6_api_build_is_deterministic_exact_and_controller_compatible(tmp_path: Path) -> None:
    first = atomic.build(release_id="v6-clean-r1", out_dir=tmp_path / "one", root=ROOT)
    second = atomic.build(release_id="v6-clean-r1", out_dir=tmp_path / "two", root=ROOT)
    expected = set(atomic.V6_API_FILES) | {atomic.RELEASE_IDENTITY_PATH}

    assert first.archive.read_bytes() == second.archive.read_bytes()
    assert first.manifest.read_bytes() == second.manifest.read_bytes()
    manifest = _manifest(first)
    assert {row["path"] for row in manifest["files"]} == expected
    assert manifest["import_modules"] == ["scripts.nmbot_api_server"]
    assert inspect_artifact(first.manifest, first.archive).release_id == "v6-clean-r1"
    with tarfile.open(first.archive, "r:gz") as bundle:
        assert set(bundle.getnames()) == expected
        identity = json.load(bundle.extractfile(atomic.RELEASE_IDENTITY_PATH))
    assert {row["path"] for row in identity["tracked_files"]} == set(atomic.V6_API_FILES)
    runtime_roots = {
        Path(path).parts[0]
        for path in expected
        if re.fullmatch(r"nmbot_v\d+", Path(path).parts[0])
    }
    assert runtime_roots == {"nmbot_v6"}
    assert "scripts/nmbot_release_control.py" not in expected


def test_existing_release_id_is_idempotent_but_never_overwritten(tmp_path: Path) -> None:
    result = atomic.build(release_id="v6-clean-r2", out_dir=tmp_path, root=ROOT)
    assert atomic.build(release_id="v6-clean-r2", out_dir=tmp_path, root=ROOT) == result
    result.archive.write_bytes(result.archive.read_bytes() + b"tampered")
    with pytest.raises(atomic.AtomicReleaseError, match="different bytes"):
        atomic.build(release_id="v6-clean-r2", out_dir=tmp_path, root=ROOT)


def test_bridge_and_worker_profiles_have_exact_small_closures(tmp_path: Path) -> None:
    bridge = atomic.build(release_id="v6-bridge-r1", out_dir=tmp_path / "bridge", root=ROOT, profile=atomic.BRIDGE_PROFILE)
    worker = atomic.build(release_id="v6-worker-r1", out_dir=tmp_path / "worker", root=ROOT, profile=atomic.CALLBACK_WORKER_PROFILE)
    assert {row["path"] for row in _manifest(bridge)["files"]} == set(atomic.BRIDGE_FILES) | {atomic.RELEASE_IDENTITY_PATH}
    assert {row["path"] for row in _manifest(worker)["files"]} == set(atomic.CALLBACK_WORKER_FILES) | {atomic.RELEASE_IDENTITY_PATH}


def test_controller_rejects_manifest_missing_one_exact_runtime_file(tmp_path: Path) -> None:
    result = atomic.build(release_id="v6-clean-r3", out_dir=tmp_path, root=ROOT)
    manifest = _manifest(result)
    manifest["files"] = manifest["files"][:-1]
    result.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReleaseControlError, match="exact V6 API contract"):
        inspect_artifact(result.manifest, result.archive)
