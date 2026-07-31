from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project_memory_notebook_summary_draft_generate.py"


def load_module():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("project_memory_notebook_summary_draft_generate_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    mod.EXPECTED_CANDIDATE_COUNT = 3
    mod.EXPECTED_SAFE_COUNT = 2
    mod.EXPECTED_BLOCKED_COUNT = 1
    return mod


def sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def record_path(root: Path, ref: dict[str, str]) -> Path:
    dirname = "notes" if ref["kind"] == "note" else "sources"
    return root / "workspaces" / "default" / "notebooks" / ref["notebook"] / dirname / f"{ref['id']}.json"


def write_record(root: Path, ref: dict[str, str], body: str, *, title: str = "DO-NOT-LEAK-TITLE") -> None:
    key = "note_id" if ref["kind"] == "note" else "source_id"
    body_key = "note" if ref["kind"] == "note" else "content"
    write_json(record_path(root, ref), {key: ref["id"], "title": title, body_key: body})


REF1 = {"notebook": "cc-daemons", "kind": "note", "id": "41b55e418687"}
REF2 = {"notebook": "cc-daemons", "kind": "note", "id": "41c9ca464ecd"}
REF3 = {"notebook": "cc-daemons", "kind": "note", "id": "47c595bdbc69"}


def manifest_for(body1: str, body2: str, body3: str) -> dict[str, object]:
    return {
        "ok": True,
        "read_only": True,
        "execution_blocked": True,
        "migration_performed": False,
        "notebook_mutation_performed": False,
        "automatic_routing_changed": False,
        "production_verified": False,
        "record_count": 602,
        "held_unresolved_count": 556,
        "disposition_counts": {"selected_for_summary_plan": 3},
        "records": [
            {"disposition": "selected_for_summary_plan", "record_ref": REF1, "metadata_sha256": sha(body1), "target_canonical_notebook": "nmbot", "provenance": {"record_metadata_sha256_verified": True}},
            {"disposition": "selected_for_summary_plan", "record_ref": REF2, "metadata_sha256": sha(body2), "target_canonical_notebook": "cc-daemons", "provenance": {"record_metadata_sha256_verified": True}},
            {"disposition": "selected_for_summary_plan", "record_ref": REF3, "metadata_sha256": sha(body3), "target_canonical_notebook": "nmbot", "provenance": {"record_metadata_sha256_verified": True}},
        ],
    }


def no_write_auth_for(manifest_path: Path, selected: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "project_memory_notebook_summary_batch_sanitize_authorization.v4",
        "authorization_type": "manual_notebooklm_summary_only_pre_migration_batch_sanitize",
        "owner": "ser",
        "rollback_owner": "ser",
        "manifest": {"sha256": file_sha(manifest_path), "schema_expectations": {"ok": True, "read_only": True, "execution_blocked": True, "migration_performed": False, "notebook_mutation_performed": False, "automatic_routing_changed": False, "production_verified": False}},
        "scope": {"candidate_count": 3, "allowed_disposition": "selected_for_summary_plan", "excluded_record_ids": [], "selected_records_sha256": hashlib.sha256(json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(), "selected_records": selected},
        "destination_policy": "canonical_only",
        "read_only": True,
        "requires_owner_confirmation": True,
        "execution_blocked": True,
        "write_performed": False,
        "notebook_mutation_performed": False,
        "automatic_routing_changed": False,
        "production_verified": False,
        "migration_performed": False,
        "data_deletion_authorized": False,
        "data_migration_authorized": False,
        "notebook_write_authorized": False,
        "notebook_write_performed": False,
        "routing_write_authorized": False,
        "delete_authorized": False,
        "does_not_authorize_notebook_write": True,
        "does_not_authorize_migration": True,
        "does_not_authorize_routing": True,
        "does_not_authorize_deletion": True,
        "summary_approval": {"approved": False, "approved_record_refs": []},
        "allowed_safe_decision": "safe_for_human_summary_draft",
        "summary_draft_generation_authorized": False,
        "continue_on_policy_block": True,
        "stop_on_integrity_failure": True,
    }


def draft_auth_for(manifest_path: Path, no_write_auth_path: Path, report_path: Path) -> dict[str, object]:
    return {
        "schema": "project_memory_notebook_summary_draft_generation_authorization.v1",
        "authorization_type": "manual_human_review_summary_draft_generation_only",
        "owner": "ser",
        "rollback_owner": "ser",
        "manifest": {"sha256": file_sha(manifest_path)},
        "no_write_sanitize_authorization": {"schema": "project_memory_notebook_summary_batch_sanitize_authorization.v4", "sha256": file_sha(no_write_auth_path)},
        "sanitizer_report": {"schema": "project_memory_notebook_summary_batch_sanitize.v1", "sha256": file_sha(report_path), "expected_aggregates": {"ok": True, "read_only": True, "execution_blocked": True, "manifest_sha256_verified": True, "authorization_exact_list_verified": True, "metadata_queue_prepared": True, "candidate_count": 3, "processed_count": 3, "safe_count": 2, "blocked_count": 1, "integrity_failure_count": 0, "summary_drafts_generated": False, "notebook_write_authorized": False, "notebook_write_performed": False, "notebook_mutation_performed": False, "migration_performed": False, "automatic_routing_changed": False, "routing_write_authorized": False, "delete_authorized": False, "data_deletion_authorized": False, "data_migration_authorized": False, "production_verified": False}},
        "scope": {"permitted_decision": "safe_for_human_summary_draft", "authorized_candidate_count": 3, "authorized_safe_record_count": 2},
        "permissions": {"summary_draft_generation_authorized": True, "human_review_required_before_any_write": True, "write_performed": False, "notebook_write_authorized": False, "notebook_write_performed": False, "notebook_mutation_performed": False, "migration_authorized": False, "migration_performed": False, "data_deletion_authorized": False, "delete_authorized": False, "routing_write_authorized": False, "automatic_routing_changed": False, "source_mutation_authorized": False, "production_verified": False},
    }


def fixture(tmp_path: Path):
    body1 = "NMBot retrieval context safe historical note."
    body2 = "Qapairs safe historical note."
    body3 = "Transcript contains customer phone +79990000000 and api_key = DO-NOT-LEAK-SECRET-FIXTURE."
    manifest_path = tmp_path / "manifest.json"
    manifest = manifest_for(body1, body2, body3)
    write_json(manifest_path, manifest)
    selected = [
        {"record_ref": REF1, "metadata_sha256": sha(body1), "target_canonical_notebook": "nmbot"},
        {"record_ref": REF2, "metadata_sha256": sha(body2), "target_canonical_notebook": "cc-daemons"},
        {"record_ref": REF3, "metadata_sha256": sha(body3), "target_canonical_notebook": "nmbot"},
    ]
    auth_path = tmp_path / "no_write_auth.json"
    no_write_auth = no_write_auth_for(manifest_path, selected)
    write_json(auth_path, no_write_auth)
    report = {"schema": "project_memory_notebook_summary_batch_sanitize.v1", "ok": True, "read_only": True, "execution_blocked": True, "manifest_sha256_verified": True, "authorization_exact_list_verified": True, "metadata_queue_prepared": True, "candidate_count": 3, "processed_count": 3, "safe_count": 2, "blocked_count": 1, "integrity_failure_count": 0, "summary_drafts_generated": False, "notebook_write_authorized": False, "notebook_write_performed": False, "notebook_mutation_performed": False, "migration_performed": False, "automatic_routing_changed": False, "routing_write_authorized": False, "delete_authorized": False, "data_deletion_authorized": False, "data_migration_authorized": False, "production_verified": False, "processed_records": []}
    report_path = tmp_path / "report.json"
    write_json(report_path, report)
    draft_auth_path = tmp_path / "draft_auth.json"
    draft_auth = draft_auth_for(manifest_path, auth_path, report_path)
    write_json(draft_auth_path, draft_auth)
    root = tmp_path / "storage"
    write_record(root, REF1, body1)
    write_record(root, REF2, body2)
    write_record(root, REF3, body3)
    (root / "workspaces" / "default" / "notebooks" / "nmbot" / "notes").mkdir(parents=True, exist_ok=True)
    return manifest_path, auth_path, report_path, draft_auth_path, root


def configure_hashes(mod, manifest_path: Path, auth_path: Path, report_path: Path) -> None:
    mod.EXPECTED_MANIFEST_SHA256 = file_sha(manifest_path)
    mod.EXPECTED_NO_WRITE_AUTH_SHA256 = file_sha(auth_path)
    mod.EXPECTED_SANITIZER_REPORT_SHA256 = file_sha(report_path)
    mod.EXPECTED_DRAFT_AUTH_SHA256 = file_sha(report_path.parent / "draft_auth.json")
    batch_mod = sys.modules.get("project_memory_notebook_summary_batch_sanitize")
    if batch_mod is not None:
        batch_mod.V4_MANIFEST_SHA256 = file_sha(manifest_path)
        batch_mod.V4_SELECTED_RECORDS_SHA256 = json.loads(auth_path.read_text(encoding="utf-8"))["scope"]["selected_records_sha256"]
        batch_mod.V4_MANIFEST_SELECTED_COUNT = 3
        batch_mod.V4_MANIFEST_RECORD_COUNT = 602
        batch_mod.V4_MANIFEST_HELD_UNRESOLVED_COUNT = 556
        batch_mod.V4_EXPECTED_COUNT = 3


def assert_blocked_payload_is_sanitized(payload: dict[str, object], combined_output: str, tmp_path: Path, root: Path, extra_forbidden: list[str] | None = None) -> None:
    assert payload["ok"] is False
    assert payload["errors"] == [{"code": "manifest_authorization_or_report_invalid", "message": "draft generation failed closed"}]
    forbidden = [
        "Traceback",
        "AttributeError",
        str(tmp_path),
        str(root),
        sha("NMBot retrieval context safe historical note."),
        "NMBot retrieval context safe historical note.",
        "Qapairs safe historical note.",
        "Transcript contains",
        "DO-NOT-LEAK-TITLE",
        "DO-NOT-LEAK-SECRET-FIXTURE",
        "api_key",
    ]
    if extra_forbidden:
        forbidden.extend(extra_forbidden)
    assert not any(token in combined_output for token in forbidden)
    assert payload["no_write_confirmation"]["notebook_write_performed"] is False


def test_draft_authorization_sha_mismatch_fails_closed_before_storage_read(tmp_path: Path) -> None:
    mod = load_module()
    manifest_path, auth_path, report_path, draft_auth_path, root = fixture(tmp_path)
    configure_hashes(mod, manifest_path, auth_path, report_path)
    valid_draft_auth_sha = file_sha(draft_auth_path)
    tampered_draft_auth = json.loads(draft_auth_path.read_text(encoding="utf-8"))
    tampered_draft_auth["scope"]["authorized_safe_record_count"] = 2

    def fail_if_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("storage must not be read")

    mod._read_exact_record_body = fail_if_read
    payload = mod.generate_drafts(
        manifest=json.loads(manifest_path.read_text(encoding="utf-8")),
        no_write_auth=json.loads(auth_path.read_text(encoding="utf-8")),
        sanitizer_report=json.loads(report_path.read_text(encoding="utf-8")),
        draft_auth=tampered_draft_auth,
        manifest_sha256=file_sha(manifest_path),
        no_write_auth_sha256=file_sha(auth_path),
        sanitizer_report_sha256=file_sha(report_path),
        draft_auth_sha256=sha("not the reviewed checked-in draft authorization"),
        storage_root=root,
    )

    assert_blocked_payload_is_sanitized(
        payload,
        json.dumps(payload, ensure_ascii=False),
        tmp_path,
        root,
        [valid_draft_auth_sha, file_sha(manifest_path), file_sha(auth_path), file_sha(report_path), file_sha(draft_auth_path)],
    )


def test_draft_authorization_valid_expected_sha_reaches_storage_read(tmp_path: Path) -> None:
    mod = load_module()
    manifest_path, auth_path, report_path, draft_auth_path, root = fixture(tmp_path)
    configure_hashes(mod, manifest_path, auth_path, report_path)
    read_attempted = False

    def stop_after_authorization(*args: object, **kwargs: object) -> str:
        nonlocal read_attempted
        read_attempted = True
        raise ValueError("stop after auth")

    mod._read_exact_record_body = stop_after_authorization
    payload = mod.generate_drafts(
        manifest=json.loads(manifest_path.read_text(encoding="utf-8")),
        no_write_auth=json.loads(auth_path.read_text(encoding="utf-8")),
        sanitizer_report=json.loads(report_path.read_text(encoding="utf-8")),
        draft_auth=json.loads(draft_auth_path.read_text(encoding="utf-8")),
        manifest_sha256=file_sha(manifest_path),
        no_write_auth_sha256=file_sha(auth_path),
        sanitizer_report_sha256=file_sha(report_path),
        draft_auth_sha256=file_sha(draft_auth_path),
        storage_root=root,
    )

    assert read_attempted is True
    assert payload["errors"][0]["code"] == "record_missing_malformed_or_hash_mismatch"


def test_generates_only_safe_non_in_place_draft_and_no_write(tmp_path: Path) -> None:
    mod = load_module()
    manifest_path, auth_path, report_path, draft_auth_path, root = fixture(tmp_path)
    configure_hashes(mod, manifest_path, auth_path, report_path)

    payload = mod.generate_drafts(
        manifest=json.loads(manifest_path.read_text(encoding="utf-8")),
        no_write_auth=json.loads(auth_path.read_text(encoding="utf-8")),
        sanitizer_report=json.loads(report_path.read_text(encoding="utf-8")),
        draft_auth=json.loads(draft_auth_path.read_text(encoding="utf-8")),
        manifest_sha256=file_sha(manifest_path),
        no_write_auth_sha256=file_sha(auth_path),
        sanitizer_report_sha256=file_sha(report_path),
        draft_auth_sha256=file_sha(draft_auth_path),
        storage_root=root,
    )

    assert payload["ok"] is True
    assert payload["safe_count"] == 2
    assert payload["draft_count"] == 1
    assert payload["in_place_count"] == 1
    assert payload["drafts"][0]["record_ref"] == REF1
    assert payload["in_place"][0]["record_ref"] == REF2
    assert payload["no_write_confirmation"]["notebook_write_performed"] is False
    combined = json.dumps(payload, ensure_ascii=False)
    assert "DO-NOT-LEAK" not in combined
    assert "api_key" not in combined.lower()
    assert "Transcript contains" not in combined
    assert "DO-NOT-LEAK-TITLE" not in combined
    assert str(root) not in combined


def test_exact_duplicate_in_target_blocks_draft_with_safe_note_id(tmp_path: Path) -> None:
    mod = load_module()
    manifest_path, auth_path, report_path, draft_auth_path, root = fixture(tmp_path)
    write_record(root, {"notebook": "nmbot", "kind": "note", "id": "existing-safe-id"}, "NMBot retrieval context safe historical note.")
    configure_hashes(mod, manifest_path, auth_path, report_path)

    payload = mod.generate_drafts(
        manifest=json.loads(manifest_path.read_text(encoding="utf-8")),
        no_write_auth=json.loads(auth_path.read_text(encoding="utf-8")),
        sanitizer_report=json.loads(report_path.read_text(encoding="utf-8")),
        draft_auth=json.loads(draft_auth_path.read_text(encoding="utf-8")),
        manifest_sha256=file_sha(manifest_path),
        no_write_auth_sha256=file_sha(auth_path),
        sanitizer_report_sha256=file_sha(report_path),
        draft_auth_sha256=file_sha(draft_auth_path),
        storage_root=root,
    )

    assert payload["ok"] is True
    assert payload["draft_count"] == 0
    assert payload["dedupe_count"] == 1
    assert payload["dedupe_existing"][0]["existing_note_ids"] == ["existing-safe-id"]


def test_report_aggregate_mismatch_fails_before_storage_read(tmp_path: Path) -> None:
    mod = load_module()
    manifest_path, auth_path, report_path, draft_auth_path, root = fixture(tmp_path)
    configure_hashes(mod, manifest_path, auth_path, report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["safe_count"] = 1

    def fail_if_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("storage must not be read")

    mod._read_exact_record_body = fail_if_read
    payload = mod.generate_drafts(
        manifest=json.loads(manifest_path.read_text(encoding="utf-8")),
        no_write_auth=json.loads(auth_path.read_text(encoding="utf-8")),
        sanitizer_report=report,
        draft_auth=json.loads(draft_auth_path.read_text(encoding="utf-8")),
        manifest_sha256=file_sha(manifest_path),
        no_write_auth_sha256=file_sha(auth_path),
        sanitizer_report_sha256=file_sha(report_path),
        draft_auth_sha256=file_sha(draft_auth_path),
        storage_root=root,
    )

    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == "manifest_authorization_or_report_invalid"


@pytest.mark.parametrize("field", ["manifest", "no_write_auth", "sanitizer_report", "draft_auth"])
def test_generate_drafts_blocks_non_dict_inputs_without_traceback_or_leaks(tmp_path: Path, field: str) -> None:
    mod = load_module()
    manifest_path, auth_path, report_path, draft_auth_path, root = fixture(tmp_path)
    configure_hashes(mod, manifest_path, auth_path, report_path)
    inputs: dict[str, object] = {
        "manifest": json.loads(manifest_path.read_text(encoding="utf-8")),
        "no_write_auth": json.loads(auth_path.read_text(encoding="utf-8")),
        "sanitizer_report": json.loads(report_path.read_text(encoding="utf-8")),
        "draft_auth": json.loads(draft_auth_path.read_text(encoding="utf-8")),
    }
    inputs[field] = [inputs[field]]

    def fail_if_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("storage must not be read")

    mod._read_exact_record_body = fail_if_read
    payload = mod.generate_drafts(
        manifest=inputs["manifest"],
        no_write_auth=inputs["no_write_auth"],
        sanitizer_report=inputs["sanitizer_report"],
        draft_auth=inputs["draft_auth"],
        manifest_sha256=file_sha(manifest_path),
        no_write_auth_sha256=file_sha(auth_path),
        sanitizer_report_sha256=file_sha(report_path),
        draft_auth_sha256=file_sha(draft_auth_path),
        storage_root=root,
    )

    assert_blocked_payload_is_sanitized(payload, json.dumps(payload, ensure_ascii=False), tmp_path, root)


@pytest.mark.parametrize(
    "path_name",
    [
        "manifest_path",
        "report_path",
        "draft_auth_path",
    ],
)
def test_cli_blocks_non_dict_manifest_report_or_draft_auth_without_traceback_or_leaks(tmp_path: Path, path_name: str) -> None:
    mod = load_module()
    manifest_path, auth_path, report_path, draft_auth_path, root = fixture(tmp_path)
    configure_hashes(mod, manifest_path, auth_path, report_path)
    paths = {
        "manifest_path": manifest_path,
        "report_path": report_path,
        "draft_auth_path": draft_auth_path,
    }
    target_path = paths[path_name]
    write_json(target_path, [json.loads(target_path.read_text(encoding="utf-8"))])
    output_path = tmp_path / f"{path_name}_draft_output.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(manifest_path),
            "--no-write-authorization",
            str(auth_path),
            "--sanitizer-report",
            str(report_path),
            "--draft-authorization",
            str(draft_auth_path),
            "--storage-root",
            str(root),
            "--output",
            str(output_path),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert json.loads(result.stdout) == payload
    assert_blocked_payload_is_sanitized(
        payload,
        result.stdout + result.stderr + output_path.read_text(encoding="utf-8"),
        tmp_path,
        root,
        [file_sha(manifest_path), file_sha(auth_path), file_sha(report_path), file_sha(draft_auth_path)],
    )


def test_cli_blocks_non_dict_no_write_authorization_without_traceback_or_leaks(tmp_path: Path) -> None:
    mod = load_module()
    manifest_path, auth_path, report_path, draft_auth_path, root = fixture(tmp_path)
    configure_hashes(mod, manifest_path, auth_path, report_path)
    write_json(auth_path, [json.loads(auth_path.read_text(encoding="utf-8"))])
    draft_auth = json.loads(draft_auth_path.read_text(encoding="utf-8"))
    draft_auth["no_write_sanitize_authorization"]["sha256"] = file_sha(auth_path)
    write_json(draft_auth_path, draft_auth)
    output_path = tmp_path / "draft_output.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(manifest_path),
            "--no-write-authorization",
            str(auth_path),
            "--sanitizer-report",
            str(report_path),
            "--draft-authorization",
            str(draft_auth_path),
            "--storage-root",
            str(root),
            "--output",
            str(output_path),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["errors"] == [{"code": "manifest_authorization_or_report_invalid", "message": "draft generation failed closed"}]
    assert json.loads(result.stdout) == payload
    combined = result.stdout + result.stderr + output_path.read_text(encoding="utf-8")
    forbidden = [
        "Traceback",
        "AttributeError",
        str(tmp_path),
        str(root),
        file_sha(auth_path),
        sha("NMBot retrieval context safe historical note."),
        "NMBot retrieval context safe historical note.",
        "Qapairs safe historical note.",
        "Transcript contains",
        "DO-NOT-LEAK-TITLE",
        "DO-NOT-LEAK-SECRET-FIXTURE",
        "api_key",
    ]
    assert not any(token in combined for token in forbidden)
    assert payload["no_write_confirmation"]["notebook_write_performed"] is False


def test_no_dangerous_imports() -> None:
    banned = [
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import urllib",
        "from urllib",
        "import socket",
        "from socket",
        "import notebooklm",
        "from notebooklm",
        "import mempalace",
        "from mempalace",
    ]
    source = SCRIPT.read_text(encoding="utf-8").lower()
    assert not any(token in source for token in banned)
