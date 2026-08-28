from __future__ import annotations

import json
import hashlib
import re
import tarfile
from pathlib import Path

import pytest

from scripts import nmbot_atomic_release as atomic
from scripts.nmbot_release_control import ReleaseControlError, inspect_artifact


ROOT = Path(__file__).resolve().parents[1]
GIT_SHA = "1" * 40
TREE_SHA = "2" * 40


def _provenance() -> dict[str, str]:
    unsigned = {"git_sha": GIT_SHA, "git_tree_sha": TREE_SHA, "tree_state": "clean"}
    receipt = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {**unsigned, "clean_receipt_sha256": receipt}


@pytest.fixture(autouse=True)
def fixed_source_provenance(monkeypatch):
    monkeypatch.setattr(atomic, "_git_source_provenance", lambda _root: _provenance())
    monkeypatch.setattr(atomic, "_git_blob", lambda root, _sha, relative: (Path(root) / relative).read_bytes())


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
    assert manifest["source_provenance"] == _provenance()
    inspected = inspect_artifact(first.manifest, first.archive)
    assert inspected.release_id == "v6-clean-r1"
    assert inspected.source_git_sha == GIT_SHA
    with tarfile.open(first.archive, "r:gz") as bundle:
        assert set(bundle.getnames()) == expected
        identity = json.load(bundle.extractfile(atomic.RELEASE_IDENTITY_PATH))
    assert {row["path"] for row in identity["tracked_files"]} == set(atomic.V6_API_FILES)
    runtime_roots = {Path(path).parts[0] for path in expected if Path(path).parts[0] == "nmbot_core"}
    assert runtime_roots == {"nmbot_core"}
    assert "scripts/nmbot_release_control.py" not in expected
    assert "scripts/nmbot_v6_jivo_smoke.py" in expected
    assert "nmbot_core/app.py" in expected


def test_existing_release_id_is_idempotent_but_never_overwritten(tmp_path: Path) -> None:
    result = atomic.build(release_id="v6-clean-r2", out_dir=tmp_path, root=ROOT)
    assert atomic.build(release_id="v6-clean-r2", out_dir=tmp_path, root=ROOT) == result
    result.archive.write_bytes(result.archive.read_bytes() + b"tampered")
    with pytest.raises(atomic.AtomicReleaseError, match="different bytes"):
        atomic.build(release_id="v6-clean-r2", out_dir=tmp_path, root=ROOT)


def test_builder_rejects_non_api_component_profiles(tmp_path: Path) -> None:
    for profile in ("v6-bridge", "v6-callback-worker"):
        with pytest.raises(atomic.AtomicReleaseError, match="unknown V6 artifact profile"):
            atomic.build(release_id="v6-component-r1", out_dir=tmp_path / profile, root=ROOT, profile=profile)


def test_controller_rejects_forged_clean_tree_receipt(tmp_path: Path) -> None:
    result = atomic.build(release_id="v6-clean-r4", out_dir=tmp_path, root=ROOT)
    manifest = _manifest(result)
    manifest["source_provenance"]["clean_receipt_sha256"] = "0" * 64
    result.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReleaseControlError, match="clean-tree receipt"):
        inspect_artifact(result.manifest, result.archive)


def test_builder_rejects_bytes_that_differ_from_pinned_git_tree(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(atomic, "_git_blob", lambda _root, _sha, _relative: b"different")
    with pytest.raises(atomic.AtomicReleaseError, match="differs from source Git tree"):
        atomic.build(release_id="v6-clean-r5", out_dir=tmp_path, root=ROOT)


def test_controller_rejects_manifest_missing_one_exact_runtime_file(tmp_path: Path) -> None:
    result = atomic.build(release_id="v6-clean-r3", out_dir=tmp_path, root=ROOT)
    manifest = _manifest(result)
    manifest["files"] = manifest["files"][:-1]
    result.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ReleaseControlError, match="exact V6 API contract"):
        inspect_artifact(result.manifest, result.archive)
