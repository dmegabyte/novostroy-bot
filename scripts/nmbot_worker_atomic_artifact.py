#!/usr/bin/env python3
"""Build and verify immutable, local-only worker artifacts for frozen profiles.

This module deliberately implements no deployment, SSH, provider, or network
operation.  Adding a future profile is data-only: define one ``WorkerProfile``
with its reviewed exact closure in ``PROFILES``.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import py_compile
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "nmbot.worker_atomic_artifact.v1"
IDENTITY_SCHEMA_VERSION = "nmbot.worker_release_identity.v1"
ROLLBACK_RECEIPT_SCHEMA_VERSION = "nmbot.worker_rollback_receipt.v1"
MAX_SNAPSHOT_AGE = timedelta(hours=24)
_RELEASE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}")
_HEX_RE = re.compile(r"[0-9a-f]{64}")


class WorkerArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerProfile:
    version: str
    package_dir: str
    python_closure: tuple[str, ...]
    resources: tuple[str, ...]
    entrypoint: str
    unit: str
    env_example: str
    root: str
    service_name: str
    port: int
    release_id_env: str
    import_modules: tuple[str, ...]

    @property
    def allowlist(self) -> tuple[str, ...]:
        return tuple(sorted((*self.python_closure, *self.resources, self.entrypoint, self.unit, self.env_example)))

    def contract(self) -> dict[str, Any]:
        return {
            "version": self.version, "package_dir": self.package_dir,
            "python_closure": list(self.python_closure), "resources": list(self.resources),
            "entrypoint": self.entrypoint, "unit": self.unit, "env_example": self.env_example,
            "root": self.root, "working_directory": f"{self.root}/current",
            "service_name": self.service_name, "port": self.port,
            "release_id_env": self.release_id_env,
        }


_COMMON = (
    "nmbot_runtime_contract/__init__.py", "nmbot_runtime_contract/wire.py",
    "nmbot_runtime_service_host/__init__.py", "nmbot_runtime_service_host/http.py",
)
V0_PROFILE = WorkerProfile(
    version="V0", package_dir="nmbot_v0",
    python_closure=(*_COMMON, "nmbot_v0/__init__.py", "nmbot_v0/card_normalizer.py", "nmbot_v0/contracts.py", "nmbot_v0/field_contract.py", "nmbot_v0/presentation.py", "nmbot_v0/runtime.py", "nmbot_v0/search_contract.py", "nmbot_v0/service.py"),
    resources=("prompts/v0_scenario_search.txt",), entrypoint="scripts/nmbot_v0_service.py",
    unit="deploy/systemd/nmbot-v0-runtime.service", env_example="deploy/systemd/nmbot-v0-runtime.env.example",
    root="/home/neiro/novostroy-bot-v0", service_name="nmbot-v0-runtime.service", port=18080,
    release_id_env="NMBOT_V0_RELEASE_ID",
    import_modules=("nmbot_runtime_contract.wire", "nmbot_runtime_service_host.http", "nmbot_v0.service", "scripts.nmbot_v0_service"),
)
V1_PROFILE = WorkerProfile(
    version="V1", package_dir="nmbot_v1",
    python_closure=(*_COMMON, "nmbot_v1/__init__.py", "nmbot_v1/contracts.py", "nmbot_v1/execution_path.py", "nmbot_v1/one_model_response.py", "nmbot_v1/planner.py", "nmbot_v1/ports.py", "nmbot_v1/prompt_provenance.py", "nmbot_v1/provider_adapters.py", "nmbot_v1/response.py", "nmbot_v1/runtime.py", "nmbot_v1/search.py", "nmbot_v1/search_contract.py", "nmbot_v1/service.py", "nmbot_v1/state.py", "nmbot_v1/transition.py"),
    resources=("prompts/candidates/v1_one_model_gpt55_experiment_v1.txt", "prompts/v1/intent_planner.txt", "prompts/v1/search_mcp.txt"), entrypoint="scripts/nmbot_v1_service.py",
    unit="deploy/systemd/nmbot-v1-runtime.service", env_example="deploy/systemd/nmbot-v1-runtime.env.example",
    root="/home/neiro/novostroy-bot-v1", service_name="nmbot-v1-runtime.service", port=18081,
    release_id_env="NMBOT_V1_RELEASE_ID",
    import_modules=("nmbot_runtime_contract.wire", "nmbot_runtime_service_host.http", "nmbot_v1.provider_adapters", "nmbot_v1.service", "scripts.nmbot_v1_service"),
)
V2_PROFILE = WorkerProfile(
    version="V2", package_dir="nmbot_v2",
    python_closure=(
        *_COMMON,
        "nmbot_v2/__init__.py", "nmbot_v2/card_normalizer.py", "nmbot_v2/capability_registry.py",
        "nmbot_v2/client_text.py", "nmbot_v2/composition.py", "nmbot_v2/constraints.py",
        "nmbot_v2/contracts.py", "nmbot_v2/conversation.py", "nmbot_v2/effective_request.py",
        "nmbot_v2/execution_path.py", "nmbot_v2/fact_context.py", "nmbot_v2/gateway.py",
        "nmbot_v2/local_response_adapters.py", "nmbot_v2/manager_rewriter.py", "nmbot_v2/outer_composition.py",
        "nmbot_v2/pair_comparison.py", "nmbot_v2/pending.py", "nmbot_v2/pending_action.py",
        "nmbot_v2/planner_adapter.py", "nmbot_v2/planner_gateway.py", "nmbot_v2/planner_gateway_contract.py",
        "nmbot_v2/port_factory.py", "nmbot_v2/ports.py", "nmbot_v2/prompt_provenance.py",
        "nmbot_v2/response.py", "nmbot_v2/response_composer.py", "nmbot_v2/runtime.py",
        "nmbot_v2/scenario_field_mechanics.py", "nmbot_v2/scenario_recipes.py", "nmbot_v2/search_adapter.py",
        "nmbot_v2/search_contract.py", "nmbot_v2/search_enrichment.py", "nmbot_v2/semantic_planner.py",
        "nmbot_v2/service.py", "nmbot_v2/state.py", "nmbot_v2/transition.py", "nmbot_v2/vocabulary.py",
        "scripts/nmbot_v2_host.py",
    ),
    resources=(
        "prompts/v2_search_mcp.txt", "prompts/v2_response_composer.txt", "prompts/v2_response_writer.txt",
        "prompts/v3_answer_writer.txt", "prompts/v2_response_formatter.txt", "prompts/v2_manager_rewriter.txt",
    ),
    entrypoint="scripts/nmbot_v2_service.py",
    unit="deploy/systemd/nmbot-v2-runtime.service", env_example="deploy/systemd/nmbot-v2-runtime.env.example",
    root="/home/neiro/novostroy-bot-v2", service_name="nmbot-v2-runtime.service", port=18082,
    release_id_env="NMBOT_V2_RELEASE_ID",
    import_modules=(
        "nmbot_runtime_contract.wire", "nmbot_runtime_service_host.http", "nmbot_v2.outer_composition",
        "nmbot_v2.service", "scripts.nmbot_v2_host", "scripts.nmbot_v2_service",
    ),
)
V3_PROFILE = WorkerProfile(
    version="V3", package_dir="nmbot_v3",
    python_closure=(
        *_COMMON,
        "nmbot_v3/__init__.py", "nmbot_v3/composition.py", "nmbot_v3/contracts.py",
        "nmbot_v3/evidence_contract.py", "nmbot_v3/evidence_provider.py", "nmbot_v3/factory.py",
        "nmbot_v3/gateway_transport.py", "nmbot_v3/orchestration.py", "nmbot_v3/planner_provider.py",
        "nmbot_v3/ports.py", "nmbot_v3/presentation.py", "nmbot_v3/provider_invocation.py",
        "nmbot_v3/renderer.py", "nmbot_v3/runtime.py", "nmbot_v3/semantic_planner.py",
        "nmbot_v3/service.py", "nmbot_v3/state.py", "nmbot_v3/transition.py",
        "nmbot_v3/writer_adapter.py", "scripts/nmbot_v3_host.py",
    ),
    resources=("nmbot_v3/prompts/answer_writer.txt",), entrypoint="scripts/nmbot_v3_service.py",
    unit="deploy/systemd/nmbot-v3-runtime.service", env_example="deploy/systemd/nmbot-v3-runtime.env.example",
    root="/home/neiro/novostroy-bot-v3", service_name="nmbot-v3-runtime.service", port=18083,
    release_id_env="NMBOT_V3_RELEASE_ID",
    import_modules=(
        "nmbot_runtime_contract.wire", "nmbot_runtime_service_host.http", "nmbot_v3.factory",
        "nmbot_v3.gateway_transport", "nmbot_v3.service", "scripts.nmbot_v3_host",
    ),
)
PROFILES = {profile.version: profile for profile in (V0_PROFILE, V1_PROFILE, V2_PROFILE, V3_PROFILE)}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(rows: list[dict[str, Any]]) -> str:
    return _sha256_bytes(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode())


def _profile(version: str) -> WorkerProfile:
    try:
        return PROFILES[version.upper()]
    except (AttributeError, KeyError) as exc:
        raise WorkerArtifactError("unknown_frozen_worker_profile") from exc


def _safe_release_id(release_id: Any) -> str:
    if not isinstance(release_id, str) or not _RELEASE_ID_RE.fullmatch(release_id):
        raise WorkerArtifactError("invalid_worker_release_id")
    return release_id


def _safe_path(path: Any, profile: WorkerProfile) -> str:
    if not isinstance(path, str):
        raise WorkerArtifactError("path_not_in_exact_worker_allowlist")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or str(candidate) not in profile.allowlist:
        raise WorkerArtifactError("path_not_in_exact_worker_allowlist")
    return str(candidate)


def _rows(root: Path, profile: WorkerProfile) -> list[dict[str, Any]]:
    result = []
    for relative in profile.allowlist:
        source = root / relative
        if not source.is_file() or source.is_symlink():
            raise WorkerArtifactError(f"required_worker_artifact_missing:{relative}")
        result.append({"path": relative, "sha256": _sha256_file(source), "size": source.stat().st_size})
    return result


def _archive_name(profile: WorkerProfile, release_id: str) -> str:
    return f"nmbot-{profile.version.lower()}-worker-{release_id}.tar.gz"


def build(*, version: str, release_id: str, out_dir: Path, root: Path = ROOT) -> tuple[Path, Path]:
    """Build a deterministic exact-profile archive and sibling manifest locally."""
    profile, release_id = _profile(version), _safe_release_id(release_id)
    rows = _rows(root, profile)
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / _archive_name(profile, release_id)
    manifest_path = out_dir / f"nmbot-{profile.version.lower()}-worker-{release_id}.manifest.json"
    if archive.exists() or manifest_path.exists():
        raise WorkerArtifactError("refusing_to_overwrite_immutable_worker_artifact")
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as output:
        for row in rows:
            data = (root / row["path"]).read_bytes()
            info = tarfile.TarInfo(row["path"])
            info.size, info.uid, info.gid, info.mtime = len(data), 0, 0, 0
            info.mode = 0o755 if row["path"] == profile.entrypoint else 0o644
            output.addfile(info, io.BytesIO(data))
    manifest = {
        "schema_version": SCHEMA_VERSION, "profile": profile.version, "release_id": release_id,
        "archive_name": archive.name, "archive_sha256": _sha256_file(archive), "files": rows,
        "tree_sha256": _tree_sha256(rows), "profile_contract": profile.contract(),
        "identity": {"schema_version": IDENTITY_SCHEMA_VERSION, "release_id": release_id, "profile": profile.version},
        "execution_policy": {"local_only": True, "network": "forbidden", "remote_execution": "not_implemented"},
    }
    validate_manifest(manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return archive, manifest_path


def validate_manifest(manifest: Any) -> WorkerProfile:
    keys = {"schema_version", "profile", "release_id", "archive_name", "archive_sha256", "files", "tree_sha256", "profile_contract", "identity", "execution_policy"}
    if not isinstance(manifest, dict) or set(manifest) != keys:
        raise WorkerArtifactError("worker_manifest_schema_mismatch")
    profile = _profile(manifest.get("profile"))
    release_id = _safe_release_id(manifest.get("release_id"))
    if manifest["schema_version"] != SCHEMA_VERSION or manifest["archive_name"] != _archive_name(profile, release_id) or not isinstance(manifest["archive_sha256"], str) or not _HEX_RE.fullmatch(manifest["archive_sha256"]):
        raise WorkerArtifactError("invalid_worker_archive_identity")
    if manifest["profile_contract"] != profile.contract() or manifest["identity"] != {"schema_version": IDENTITY_SCHEMA_VERSION, "release_id": release_id, "profile": profile.version}:
        raise WorkerArtifactError("worker_profile_contract_mismatch")
    if manifest["execution_policy"] != {"local_only": True, "network": "forbidden", "remote_execution": "not_implemented"}:
        raise WorkerArtifactError("invalid_worker_execution_policy")
    if not isinstance(manifest["files"], list) or len(manifest["files"]) != len(profile.allowlist):
        raise WorkerArtifactError("worker_allowlist_file_count_mismatch")
    paths = []
    for row in manifest["files"]:
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "size"}:
            raise WorkerArtifactError("invalid_worker_file_row")
        paths.append(_safe_path(row["path"], profile))
        if not isinstance(row["sha256"], str) or not _HEX_RE.fullmatch(row["sha256"]) or type(row["size"]) is not int or row["size"] < 0:
            raise WorkerArtifactError("invalid_worker_file_record")
    if paths != list(profile.allowlist) or manifest["tree_sha256"] != _tree_sha256(manifest["files"]):
        raise WorkerArtifactError("worker_exact_allowlist_or_tree_identity_mismatch")
    return profile


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkerArtifactError("invalid_worker_manifest_json") from exc
    validate_manifest(manifest)
    return manifest


def verify_archive(archive: Path, manifest: dict[str, Any]) -> WorkerProfile:
    profile = validate_manifest(manifest)
    try:
        archive_sha256 = _sha256_file(archive)
    except OSError as exc:
        raise WorkerArtifactError("worker_archive_identity_mismatch") from exc
    if archive.name != manifest["archive_name"] or archive_sha256 != manifest["archive_sha256"]:
        raise WorkerArtifactError("worker_archive_identity_mismatch")
    actual: dict[str, dict[str, Any]] = {}
    try:
        with tarfile.open(archive, "r:gz") as source:
            for member in source.getmembers():
                if not member.isfile() or member.name in actual:
                    raise WorkerArtifactError("unsafe_worker_archive_member")
                relative = _safe_path(member.name, profile)
                body = source.extractfile(member)
                if body is None:
                    raise WorkerArtifactError("unreadable_worker_archive_member")
                data = body.read()
                actual[relative] = {"path": relative, "sha256": _sha256_bytes(data), "size": len(data)}
    except (OSError, tarfile.TarError) as exc:
        raise WorkerArtifactError("invalid_worker_archive") from exc
    if actual != {row["path"]: row for row in manifest["files"]}:
        raise WorkerArtifactError("worker_archive_allowlist_or_hash_mismatch")
    return profile


def _safe_extract(archive: Path, destination: Path, profile: WorkerProfile) -> None:
    with tarfile.open(archive, "r:gz") as source:
        for member in source.getmembers():
            relative = _safe_path(member.name, profile)
            if not member.isfile():
                raise WorkerArtifactError("unsafe_worker_archive_member")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            body = source.extractfile(member)
            if body is None:
                raise WorkerArtifactError("unreadable_worker_archive_member")
            target.write_bytes(body.read())


def local_preflight(*, archive: Path, manifest_path: Path) -> str:
    manifest = load_manifest(manifest_path)
    profile = verify_archive(archive, manifest)
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory)
        _safe_extract(archive, destination, profile)
        for asset in profile.resources:
            if not (destination / asset).read_text(encoding="utf-8").strip():
                raise WorkerArtifactError(f"worker_runtime_asset_unreadable:{asset}")
        for index, relative in enumerate(profile.python_closure + (profile.entrypoint,)):
            py_compile.compile(str(destination / relative), cfile=str(destination / f".compiled-{index}.pyc"), doraise=True)
        code = "import importlib,json,sys\nfor name in json.loads(sys.argv[1]): importlib.import_module(name)\nprint('import=ok')\n"
        result = subprocess.run([sys.executable, "-c", code, json.dumps(profile.import_modules)], cwd=destination, text=True, capture_output=True, check=False)
        if result.returncode != 0 or "import=ok" not in result.stdout:
            raise WorkerArtifactError("worker_import_closure_failed:" + (result.stdout + result.stderr)[-1000:])
    return f"worker_preflight=ok profile={profile.version} release_id={manifest['release_id']} files={len(profile.allowlist)} network=forbidden\n"


def _require_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HEX_RE.fullmatch(value):
        raise WorkerArtifactError(f"{label}_invalid")
    return value


def _fresh(value: Any, label: str, now: datetime) -> None:
    if not isinstance(value, str):
        raise WorkerArtifactError(f"{label}_missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkerArtifactError(f"{label}_invalid") from exc
    if parsed.tzinfo is None or now - parsed.astimezone(timezone.utc) > MAX_SNAPSHOT_AGE or parsed > now + timedelta(minutes=5):
        raise WorkerArtifactError(f"{label}_not_fresh")


def _load_bound_json(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise WorkerArtifactError(f"invalid_{label}_json") from exc
    if not isinstance(value, dict):
        raise WorkerArtifactError(f"invalid_{label}_json")
    return raw, value


def validate_execution_gates(*, archive: Path, manifest: dict[str, Any], snapshot_provenance: Any,
                             snapshot_manifest_path: Path, worktree_provenance_path: Path,
                             rollback_identity: Any, rollback_receipt_path: Path, approval: Any,
                             action: str, now: datetime | None = None) -> None:
    profile = verify_archive(archive, manifest)
    if action not in {"release", "shadow", "cutover"}:
        raise WorkerArtifactError("invalid_worker_execution_action")
    now = now or datetime.now(timezone.utc)
    snapshot_keys = {"snapshot_id", "manifest_sha256", "contour", "captured_at_utc", "worktree_provenance_sha256"}
    if not isinstance(snapshot_provenance, dict) or set(snapshot_provenance) != snapshot_keys or not isinstance(snapshot_provenance["snapshot_id"], str) or not snapshot_provenance["snapshot_id"]:
        raise WorkerArtifactError("snapshot_provenance_required")
    _require_hash(snapshot_provenance["manifest_sha256"], "snapshot_manifest_sha256")
    _require_hash(snapshot_provenance["worktree_provenance_sha256"], "worktree_provenance_sha256")
    snapshot_raw, snapshot_manifest = _load_bound_json(snapshot_manifest_path, "snapshot_manifest")
    if _sha256_bytes(snapshot_raw) != snapshot_provenance["manifest_sha256"]:
        raise WorkerArtifactError("snapshot_manifest_identity_mismatch")
    if (snapshot_manifest.get("snapshot_id") != snapshot_provenance["snapshot_id"]
            or snapshot_manifest.get("contour") != snapshot_provenance["contour"]
            or snapshot_manifest.get("created_at_utc") != snapshot_provenance["captured_at_utc"]):
        raise WorkerArtifactError("snapshot_manifest_provenance_mismatch")
    _fresh(snapshot_manifest["created_at_utc"], "snapshot_manifest", now)
    worktree_raw, worktree_provenance = _load_bound_json(worktree_provenance_path, "worktree_provenance")
    if _sha256_bytes(worktree_raw) != snapshot_provenance["worktree_provenance_sha256"]:
        raise WorkerArtifactError("worktree_provenance_identity_mismatch")
    if (worktree_provenance.get("snapshot_id") != snapshot_provenance["snapshot_id"]
            or worktree_provenance.get("snapshot_manifest_sha256") != snapshot_provenance["manifest_sha256"]):
        raise WorkerArtifactError("worktree_provenance_snapshot_mismatch")
    rollback_keys = {"previous_release_id", "previous_archive_sha256", "previous_release_identity_sha256", "previous_current_target", "captured_at_utc"}
    if not isinstance(rollback_identity, dict) or set(rollback_identity) != rollback_keys:
        raise WorkerArtifactError("immutable_rollback_identity_required")
    previous = _safe_release_id(rollback_identity["previous_release_id"])
    _require_hash(rollback_identity["previous_archive_sha256"], "previous_archive_sha256")
    _require_hash(rollback_identity["previous_release_identity_sha256"], "previous_release_identity_sha256")
    if rollback_identity["previous_current_target"] != f"releases/{previous}":
        raise WorkerArtifactError("immutable_rollback_target_required")
    receipt_raw, rollback_receipt = _load_bound_json(rollback_receipt_path, "rollback_receipt")
    if _sha256_bytes(receipt_raw) != rollback_identity["previous_release_identity_sha256"]:
        raise WorkerArtifactError("rollback_receipt_identity_mismatch")
    expected_receipt = {
        "schema_version": ROLLBACK_RECEIPT_SCHEMA_VERSION,
        "profile": profile.version,
        "previous_release_id": previous,
        "previous_archive_sha256": rollback_identity["previous_archive_sha256"],
        "previous_current_target": rollback_identity["previous_current_target"],
        "captured_at_utc": rollback_identity["captured_at_utc"],
    }
    if rollback_receipt != expected_receipt:
        raise WorkerArtifactError("rollback_receipt_provenance_mismatch")
    _fresh(rollback_receipt["captured_at_utc"], "rollback_receipt", now)
    approval_keys = {"approval_id", "approved_at_utc", "profile", "release_id", "archive_sha256", "tree_sha256", "actions"}
    if not isinstance(approval, dict) or set(approval) != approval_keys or not isinstance(approval["approval_id"], str) or not approval["approval_id"]:
        raise WorkerArtifactError("explicit_worker_approval_required")
    if approval["profile"] != profile.version or approval["release_id"] != manifest["release_id"] or approval["archive_sha256"] != manifest["archive_sha256"] or approval["tree_sha256"] != manifest["tree_sha256"] or not isinstance(approval["actions"], list) or action not in approval["actions"]:
        raise WorkerArtifactError("explicit_worker_approval_required")
    _fresh(approval["approved_at_utc"], "approval", now)
    if snapshot_provenance["contour"] != profile.version.lower():
        raise WorkerArtifactError("profile_snapshot_contour_mismatch")


def render_execution_plan(**kwargs: Any) -> str:
    validate_execution_gates(**kwargs)
    manifest, rollback, action = kwargs["manifest"], kwargs["rollback_identity"], kwargs["action"]
    profile = _profile(manifest["profile"])
    return "\n".join(("plan=worker_artifact_local_only", f"action={action}", f"profile={profile.version}", f"release_id={manifest['release_id']}", f"root={profile.root}", f"service={profile.service_name}", f"port={profile.port}", "network=forbidden", "remote_execution=not_implemented", f"rollback_release_id={rollback['previous_release_id']}")) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local-only immutable NMBot worker artifacts")
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build"); build_parser.add_argument("--profile", required=True); build_parser.add_argument("--release-id", required=True); build_parser.add_argument("--out-dir", type=Path, required=True)
    preflight = commands.add_parser("preflight"); preflight.add_argument("--archive", type=Path, required=True); preflight.add_argument("--manifest", type=Path, required=True)
    for action in ("release", "shadow", "cutover"):
        command = commands.add_parser(action); command.add_argument("--archive", type=Path, required=True); command.add_argument("--manifest", type=Path, required=True); command.add_argument("--snapshot-provenance", type=Path, required=True); command.add_argument("--snapshot-manifest", type=Path, required=True); command.add_argument("--worktree-provenance", type=Path, required=True); command.add_argument("--rollback-identity", type=Path, required=True); command.add_argument("--rollback-receipt", type=Path, required=True); command.add_argument("--approval", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            archive, manifest = build(version=args.profile, release_id=args.release_id, out_dir=args.out_dir); print(json.dumps({"archive": str(archive), "manifest": str(manifest)}, sort_keys=True))
        elif args.command == "preflight": print(local_preflight(archive=args.archive, manifest_path=args.manifest), end="")
        else:
            read = lambda path: json.loads(path.read_text(encoding="utf-8"))
            print(render_execution_plan(archive=args.archive, manifest=load_manifest(args.manifest), snapshot_provenance=read(args.snapshot_provenance), snapshot_manifest_path=args.snapshot_manifest, worktree_provenance_path=args.worktree_provenance, rollback_identity=read(args.rollback_identity), rollback_receipt_path=args.rollback_receipt, approval=read(args.approval), action=args.command), end="")
    except WorkerArtifactError as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
