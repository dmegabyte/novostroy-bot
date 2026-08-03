from __future__ import annotations

import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import nmbot_worker_atomic_artifact as worker


ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _gates(tmp_path: Path, manifest: dict) -> tuple[dict, dict, dict, dict[str, Path]]:
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    snapshot_manifest = _write_json(tmp_path / "snapshot.json", {"snapshot_id": "local-snapshot-001", "contour": manifest["profile"].lower(), "created_at_utc": stamp})
    snapshot = {"snapshot_id": "local-snapshot-001", "manifest_sha256": worker._sha256_file(snapshot_manifest), "contour": manifest["profile"].lower(), "captured_at_utc": stamp}
    worktree = _write_json(tmp_path / "worktree.json", {"snapshot_id": snapshot["snapshot_id"], "snapshot_manifest_sha256": snapshot["manifest_sha256"]})
    snapshot["worktree_provenance_sha256"] = worker._sha256_file(worktree)
    rollback_receipt = _write_json(tmp_path / "rollback.json", {"schema_version": worker.ROLLBACK_RECEIPT_SCHEMA_VERSION, "profile": manifest["profile"], "previous_release_id": "previous-001", "previous_archive_sha256": "c" * 64, "previous_current_target": "releases/previous-001", "captured_at_utc": stamp})
    rollback = {"previous_release_id": "previous-001", "previous_archive_sha256": "c" * 64, "previous_release_identity_sha256": worker._sha256_file(rollback_receipt), "previous_current_target": "releases/previous-001", "captured_at_utc": stamp}
    approval = {"approval_id": "local-approval-001", "approved_at_utc": stamp, "profile": manifest["profile"], "release_id": manifest["release_id"], "archive_sha256": manifest["archive_sha256"], "tree_sha256": manifest["tree_sha256"], "actions": ["release", "shadow", "cutover"]}
    return snapshot, rollback, approval, {"snapshot_manifest_path": snapshot_manifest, "worktree_provenance_path": worktree, "rollback_receipt_path": rollback_receipt}


def _plan_args(archive: Path, manifest: dict, snapshot: dict, rollback: dict, approval: dict, paths: dict[str, Path], action: str) -> dict:
    return {"archive": archive, "manifest": manifest, "snapshot_provenance": snapshot, "rollback_identity": rollback, "approval": approval, "action": action, **paths}


@pytest.mark.parametrize("version", ["V0", "V1", "V2", "V3"])
def test_frozen_profile_build_has_exact_closure_and_preflight(tmp_path: Path, version: str) -> None:
    archive, manifest_path = worker.build(version=version, release_id=f"{version.lower()}-worker-001", out_dir=tmp_path, root=ROOT)
    manifest, profile = worker.load_manifest(manifest_path), worker.PROFILES[version]
    assert [row["path"] for row in manifest["files"]] == list(profile.allowlist)
    assert manifest["profile_contract"] == profile.contract()
    assert manifest["profile_contract"]["release_id_env"] == f"NMBOT_{version}_RELEASE_ID"
    assert worker.local_preflight(archive=archive, manifest_path=manifest_path).startswith(f"worker_preflight=ok profile={version}")


def test_v0_excludes_obsolete_writer_and_other_versions(tmp_path: Path) -> None:
    _, path = worker.build(version="V0", release_id="v0-exact-001", out_dir=tmp_path, root=ROOT)
    paths = {row["path"] for row in worker.load_manifest(path)["files"]}
    assert "nmbot_v0/answer_writer.py" not in paths
    assert not any(path.startswith(("nmbot_v1/", "nmbot_v2/", "scripts/nmbot_api", "scripts/nmbot_version_router", "scripts/nmbot_runtime_adapter")) for path in paths)


def test_v1_includes_actual_provider_prompt_assets(tmp_path: Path) -> None:
    _, path = worker.build(version="V1", release_id="v1-assets-001", out_dir=tmp_path, root=ROOT)
    paths = {row["path"] for row in worker.load_manifest(path)["files"]}
    assert set(worker.V1_PROFILE.resources) <= paths


def test_v2_profile_has_exact_private_runtime_closure_and_startability_contract(tmp_path: Path) -> None:
    profile = worker.V2_PROFILE
    assert profile.entrypoint == "scripts/nmbot_v2_service.py"
    assert profile.unit == "deploy/systemd/nmbot-v2-runtime.service"
    assert profile.env_example == "deploy/systemd/nmbot-v2-runtime.env.example"
    assert profile.root == "/home/neiro/novostroy-bot-v2"
    assert profile.service_name == "nmbot-v2-runtime.service"
    assert profile.port == 18082
    assert profile.release_id_env == "NMBOT_V2_RELEASE_ID"
    assert profile.resources == (
        "prompts/v2_search_mcp.txt", "prompts/v2_response_composer.txt", "prompts/v2_response_writer.txt",
        "prompts/v3_answer_writer.txt", "prompts/v2_response_formatter.txt", "prompts/v2_manager_rewriter.txt",
    )
    archive, manifest_path = worker.build(version="V2", release_id="v2-runtime-001", out_dir=tmp_path, root=ROOT)
    manifest = worker.load_manifest(manifest_path)
    paths = {row["path"] for row in manifest["files"]}
    assert set(profile.resources) <= paths
    assert {profile.entrypoint, "scripts/nmbot_v2_host.py", profile.unit, profile.env_example} <= paths
    assert worker.local_preflight(archive=archive, manifest_path=manifest_path).startswith("worker_preflight=ok profile=V2")


def test_v2_exact_allowlist_excludes_other_workers_and_non_runtime_modules(tmp_path: Path) -> None:
    _, manifest_path = worker.build(version="V2", release_id="v2-isolation-001", out_dir=tmp_path, root=ROOT)
    paths = {row["path"] for row in worker.load_manifest(manifest_path)["files"]}
    banned_prefixes = (
        "nmbot_v0/", "nmbot_v1/", "nmbot_v3/", "scripts/nmbot_api", "scripts/nmbot_version_router",
        "scripts/nmbot_runtime_adapter", "scripts/nmbot_n8n_bridge", "scripts/jivo",
    )
    assert not any(path.startswith(banned_prefixes) for path in paths)
    assert not {"nmbot_v2/replay.py", "nmbot_v2/quality.py", "nmbot_v2/selected_capability.py"} & paths
    assert not any(path.endswith(".env") or path == ".env" for path in paths)


def test_v3_profile_matches_overlay_identity_closure_resources_and_isolation(tmp_path: Path) -> None:
    profile = worker.V3_PROFILE
    assert profile.version == "V3"
    assert profile.package_dir == "nmbot_v3"
    assert profile.python_closure == (
        "nmbot_runtime_contract/__init__.py", "nmbot_runtime_contract/wire.py",
        "nmbot_runtime_service_host/__init__.py", "nmbot_runtime_service_host/http.py",
        "nmbot_v3/__init__.py", "nmbot_v3/composition.py", "nmbot_v3/contracts.py",
        "nmbot_v3/evidence_contract.py", "nmbot_v3/evidence_provider.py", "nmbot_v3/factory.py",
        "nmbot_v3/gateway_transport.py", "nmbot_v3/orchestration.py", "nmbot_v3/planner_provider.py",
        "nmbot_v3/ports.py", "nmbot_v3/presentation.py", "nmbot_v3/provider_invocation.py",
        "nmbot_v3/renderer.py", "nmbot_v3/runtime.py", "nmbot_v3/semantic_planner.py",
        "nmbot_v3/service.py", "nmbot_v3/state.py", "nmbot_v3/transition.py",
        "nmbot_v3/writer_adapter.py", "scripts/nmbot_v3_host.py",
    )
    assert profile.resources == ("nmbot_v3/prompts/answer_writer.txt",)
    assert (profile.entrypoint, profile.unit, profile.env_example, profile.root, profile.service_name, profile.port, profile.release_id_env) == (
        "scripts/nmbot_v3_service.py", "deploy/systemd/nmbot-v3-runtime.service",
        "deploy/systemd/nmbot-v3-runtime.env.example", "/home/neiro/novostroy-bot-v3",
        "nmbot-v3-runtime.service", 18083, "NMBOT_V3_RELEASE_ID",
    )
    archive, manifest_path = worker.build(version="V3", release_id="v3-isolation-001", out_dir=tmp_path, root=ROOT)
    manifest = worker.load_manifest(manifest_path)
    paths = {row["path"] for row in manifest["files"]}
    assert {profile.entrypoint, "scripts/nmbot_v3_host.py", *profile.resources, profile.unit, profile.env_example} <= paths
    assert not any(path.startswith(("nmbot_v0/", "nmbot_v1/", "nmbot_v2/", "nmbot_v4/", "config/", "prompts/")) for path in paths)
    assert not any(path.startswith(("scripts/nmbot_api", "scripts/nmbot_version_router", "scripts/nmbot_runtime_adapter", "scripts/nmbot_n8n_bridge", "scripts/jivo")) for path in paths)
    assert not any(path.endswith(".env") or path == ".env" for path in paths)
    assert worker.local_preflight(archive=archive, manifest_path=manifest_path).startswith("worker_preflight=ok profile=V3")


def test_v3_tamper_and_missing_writer_prompt_are_refused(tmp_path: Path) -> None:
    archive, manifest_path = worker.build(version="V3", release_id="v3-tamper-001", out_dir=tmp_path, root=ROOT)
    manifest = worker.load_manifest(manifest_path)
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(worker.WorkerArtifactError, match="worker_archive_identity_mismatch"):
        worker.verify_archive(archive, manifest)

    source = tmp_path / "source"; source.mkdir()
    for relative in worker.V3_PROFILE.allowlist:
        target = source / relative; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    (source / "nmbot_v3/prompts/answer_writer.txt").unlink()
    with pytest.raises(worker.WorkerArtifactError, match="required_worker_artifact_missing"):
        worker.build(version="V3", release_id="v3-missing-001", out_dir=tmp_path / "missing-out", root=source)


def test_v3_safe_extraction_refuses_path_traversal_before_writing(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo("../outside.py")
        member.size = 1
        import io
        output.addfile(member, io.BytesIO(b"x"))

    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(worker.WorkerArtifactError, match="path_not_in_exact_worker_allowlist"):
        worker._safe_extract(archive, destination, worker.V3_PROFILE)
    assert not (tmp_path / "outside.py").exists()


def test_archive_tampering_and_overwrite_are_refused(tmp_path: Path) -> None:
    archive, manifest_path = worker.build(version="V0", release_id="v0-tamper-001", out_dir=tmp_path, root=ROOT)
    manifest = worker.load_manifest(manifest_path)
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(worker.WorkerArtifactError, match="worker_archive_identity_mismatch"):
        worker.verify_archive(archive, manifest)
    with pytest.raises(worker.WorkerArtifactError, match="refusing_to_overwrite"):
        worker.build(version="V0", release_id="v0-tamper-001", out_dir=tmp_path, root=ROOT)


def test_missing_asset_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source"; source.mkdir()
    for relative in worker.V0_PROFILE.allowlist:
        target = source / relative; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    (source / "prompts/v0_scenario_search.txt").unlink()
    with pytest.raises(worker.WorkerArtifactError, match="required_worker_artifact_missing"):
        worker.build(version="V0", release_id="v0-missing-001", out_dir=tmp_path / "out", root=source)


@pytest.mark.parametrize("action", ["release", "shadow", "cutover"])
def test_plans_stop_without_bound_rollback_and_approval(tmp_path: Path, action: str) -> None:
    archive, path = worker.build(version="V1", release_id="v1-gates-001", out_dir=tmp_path, root=ROOT)
    manifest = worker.load_manifest(path); snapshot, rollback, approval, paths = _gates(tmp_path, manifest)
    with pytest.raises(worker.WorkerArtifactError):
        worker.render_execution_plan(**_plan_args(archive, manifest, None, None, None, paths, action))
    approval["tree_sha256"] = "e" * 64
    with pytest.raises(worker.WorkerArtifactError, match="explicit_worker_approval_required"):
        worker.render_execution_plan(**_plan_args(archive, manifest, snapshot, rollback, approval, paths, action))
    approval["tree_sha256"] = manifest["tree_sha256"]
    approval["profile"] = "V0"
    with pytest.raises(worker.WorkerArtifactError, match="explicit_worker_approval_required"):
        worker.render_execution_plan(**_plan_args(archive, manifest, snapshot, rollback, approval, paths, action))
    approval["profile"] = manifest["profile"]
    rollback["previous_current_target"] = "current"
    with pytest.raises(worker.WorkerArtifactError, match="immutable_rollback_target_required"):
        worker.render_execution_plan(**_plan_args(archive, manifest, snapshot, rollback, approval, paths, action))


def test_archive_members_are_exact_and_no_network_api_or_router_files(tmp_path: Path) -> None:
    archive, manifest_path = worker.build(version="V1", release_id="v1-members-001", out_dir=tmp_path, root=ROOT)
    manifest = worker.load_manifest(manifest_path)
    with tarfile.open(archive, "r:gz") as artifact:
        assert artifact.getnames() == [row["path"] for row in manifest["files"]]
    snapshot, rollback, approval, paths = _gates(tmp_path, manifest)
    plan = worker.render_execution_plan(**_plan_args(archive, manifest, snapshot, rollback, approval, paths, "shadow"))
    assert "network=forbidden" in plan and "remote_execution=not_implemented" in plan


@pytest.mark.parametrize("case", ["missing", "forged_name", "tampered"])
def test_execution_gate_refuses_missing_forged_or_tampered_candidate_archive(tmp_path: Path, case: str) -> None:
    archive, manifest_path = worker.build(version="V0", release_id="v0-gate-archive-001", out_dir=tmp_path, root=ROOT)
    manifest = worker.load_manifest(manifest_path); snapshot, rollback, approval, paths = _gates(tmp_path, manifest)
    candidate = archive
    if case == "missing":
        archive.unlink()
    elif case == "forged_name":
        candidate = tmp_path / "forged.tar.gz"; candidate.write_bytes(archive.read_bytes())
    else:
        archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(worker.WorkerArtifactError, match="worker_archive_identity_mismatch"):
        worker.render_execution_plan(**_plan_args(candidate, manifest, snapshot, rollback, approval, paths, "release"))


@pytest.mark.parametrize("case", ["hash", "identity"])
def test_execution_gate_binds_snapshot_manifest_bytes_and_identity(tmp_path: Path, case: str) -> None:
    archive, manifest_path = worker.build(version="V1", release_id="v1-gate-snapshot-001", out_dir=tmp_path, root=ROOT)
    manifest = worker.load_manifest(manifest_path); snapshot, rollback, approval, paths = _gates(tmp_path, manifest)
    if case == "hash":
        snapshot["manifest_sha256"] = "0" * 64
    else:
        _write_json(paths["snapshot_manifest_path"], {"snapshot_id": snapshot["snapshot_id"], "contour": "wrong", "created_at_utc": snapshot["captured_at_utc"]})
        snapshot["manifest_sha256"] = worker._sha256_file(paths["snapshot_manifest_path"])
    expected = "snapshot_manifest_identity_mismatch" if case == "hash" else "snapshot_manifest_provenance_mismatch"
    with pytest.raises(worker.WorkerArtifactError, match=expected):
        worker.render_execution_plan(**_plan_args(archive, manifest, snapshot, rollback, approval, paths, "release"))


@pytest.mark.parametrize("case", ["hash", "link"])
def test_execution_gate_binds_worktree_provenance_bytes_and_snapshot_link(tmp_path: Path, case: str) -> None:
    archive, manifest_path = worker.build(version="V2", release_id="v2-gate-worktree-001", out_dir=tmp_path, root=ROOT)
    manifest = worker.load_manifest(manifest_path); snapshot, rollback, approval, paths = _gates(tmp_path, manifest)
    if case == "hash":
        snapshot["worktree_provenance_sha256"] = "0" * 64
    else:
        _write_json(paths["worktree_provenance_path"], {"snapshot_id": "other-snapshot", "snapshot_manifest_sha256": snapshot["manifest_sha256"]})
        snapshot["worktree_provenance_sha256"] = worker._sha256_file(paths["worktree_provenance_path"])
    expected = "worktree_provenance_identity_mismatch" if case == "hash" else "worktree_provenance_snapshot_mismatch"
    with pytest.raises(worker.WorkerArtifactError, match=expected):
        worker.render_execution_plan(**_plan_args(archive, manifest, snapshot, rollback, approval, paths, "cutover"))


@pytest.mark.parametrize("field,value", [
    ("schema_version", "wrong"), ("profile", "V0"), ("previous_release_id", "other-001"), ("previous_archive_sha256", "0" * 64),
    ("previous_current_target", "current"),
])
def test_execution_gate_binds_versioned_immutable_rollback_receipt(tmp_path: Path, field: str, value: str) -> None:
    archive, manifest_path = worker.build(version="V3", release_id="v3-gate-rollback-001", out_dir=tmp_path, root=ROOT)
    manifest = worker.load_manifest(manifest_path); snapshot, rollback, approval, paths = _gates(tmp_path, manifest)
    receipt = json.loads(paths["rollback_receipt_path"].read_text(encoding="utf-8")); receipt[field] = value
    _write_json(paths["rollback_receipt_path"], receipt)
    rollback["previous_release_identity_sha256"] = worker._sha256_file(paths["rollback_receipt_path"])
    with pytest.raises(worker.WorkerArtifactError, match="rollback_receipt_provenance_mismatch"):
        worker.render_execution_plan(**_plan_args(archive, manifest, snapshot, rollback, approval, paths, "shadow"))


def test_execution_gate_refuses_rollback_receipt_hash_mismatch(tmp_path: Path) -> None:
    archive, manifest_path = worker.build(version="V3", release_id="v3-gate-receipt-hash-001", out_dir=tmp_path, root=ROOT)
    manifest = worker.load_manifest(manifest_path); snapshot, rollback, approval, paths = _gates(tmp_path, manifest)
    rollback["previous_release_identity_sha256"] = "0" * 64
    with pytest.raises(worker.WorkerArtifactError, match="rollback_receipt_identity_mismatch"):
        worker.render_execution_plan(**_plan_args(archive, manifest, snapshot, rollback, approval, paths, "shadow"))
