from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project_memory_notebook_summary_batch_sanitize.py"


def load_module():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("project_memory_notebook_summary_batch_sanitize_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    mod.EXPECTED_COUNT = 2
    mod.V2_EXPECTED_COUNT = 2
    mod.V3_EXPECTED_COUNT = 2
    mod.V4_EXPECTED_COUNT = 2
    return mod


def sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def record_path(root: Path, ref: dict[str, str]) -> Path:
    dirname = "notes" if ref["kind"] == "note" else "sources"
    return root / "workspaces" / "default" / "notebooks" / ref["notebook"] / dirname / f"{ref['id']}.json"


def write_record(root: Path, ref: dict[str, str], body: str, *, title: str = "DO-NOT-LEAK-TITLE") -> None:
    key = "note_id" if ref["kind"] == "note" else "source_id"
    body_key = "note" if ref["kind"] == "note" else "content"
    write_json(record_path(root, ref), {key: ref["id"], "title": title, body_key: body})


REF1 = {"notebook": "cc-daemons", "kind": "note", "id": "41b55e418687"}
REF2 = {"notebook": "cc-daemons", "kind": "note", "id": "41c9ca464ecd"}
REF3 = {"notebook": "cc-daemons", "kind": "note", "id": "41f4c8962e73"}
REF4 = {"notebook": "cc-daemons", "kind": "note", "id": "47c595bdbc69"}
EXCLUDED = [
    {"notebook": "cc-daemons", "kind": "note", "id": "0f2c83dd6879"},
    {"notebook": "cc-daemons", "kind": "note", "id": "20a382a56022"},
    {"notebook": "cc-daemons", "kind": "note", "id": "2a7edfa48439"},
]
V2_EXCLUDED = [*EXCLUDED, REF1, REF2]
V3_EXCLUDED = [*V2_EXCLUDED, REF3]


def manifest_for(body1: str, body2: str) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for ref in EXCLUDED:
        records.append(
            {
                "disposition": "selected_for_summary_plan",
                "record_ref": ref,
                "metadata_sha256": "0" * 64,
                "target_canonical_notebook": "nmbot",
                "provenance": {"record_metadata_sha256_verified": True},
            }
        )
    records.extend(
        [
            {
                "disposition": "selected_for_summary_plan",
                "record_ref": REF1,
                "metadata_sha256": sha(body1),
                "target_canonical_notebook": "nmbot",
                "provenance": {"record_metadata_sha256_verified": True},
            },
            {
                "disposition": "selected_for_summary_plan",
                "record_ref": REF2,
                "metadata_sha256": sha(body2),
                "target_canonical_notebook": "cc-daemons",
                "provenance": {"record_metadata_sha256_verified": True},
            },
        ]
    )
    return {
        "ok": True,
        "read_only": True,
        "execution_blocked": True,
        "migration_performed": False,
        "notebook_mutation_performed": False,
        "automatic_routing_changed": False,
        "production_verified": False,
        "record_count": 364,
        "held_unresolved_count": 323,
        "disposition_counts": {"selected_for_summary_plan": 38},
        "records": records,
    }


def authorization_for(manifest_path: Path, body1: str, body2: str) -> dict[str, object]:
    return {
        "schema": "project_memory_notebook_summary_batch_sanitize_authorization.v1",
        "authorization_type": "manual_notebooklm_summary_only_pre_migration_batch_sanitize",
        "owner": "ser",
        "rollback_owner": "ser",
        "manifest": {
            "sha256": file_sha(manifest_path),
            "schema_expectations": {
                "ok": True,
                "read_only": True,
                "execution_blocked": True,
                "migration_performed": False,
                "notebook_mutation_performed": False,
                "automatic_routing_changed": False,
                "production_verified": False,
            },
        },
        "scope": {
            "candidate_count": 2,
            "allowed_disposition": "selected_for_summary_plan",
            "excluded_record_ids": ["0f2c83dd6879", "20a382a56022", "2a7edfa48439"],
            "selected_records": [
                {"record_ref": REF1, "metadata_sha256": sha(body1), "target_canonical_notebook": "nmbot"},
                {"record_ref": REF2, "metadata_sha256": sha(body2), "target_canonical_notebook": "cc-daemons"},
            ],
        },
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
    }


def manifest_v2_for(body3: str, body4: str) -> dict[str, object]:
    manifest = manifest_for("Already processed safe summary draft.", "Blocked customer data.")
    manifest["records"].extend(
        [
            {
                "disposition": "selected_for_summary_plan",
                "record_ref": REF3,
                "metadata_sha256": sha(body3),
                "target_canonical_notebook": "cc-daemons",
                "provenance": {"record_metadata_sha256_verified": True},
            },
            {
                "disposition": "selected_for_summary_plan",
                "record_ref": REF4,
                "metadata_sha256": sha(body4),
                "target_canonical_notebook": "nmbot",
                "provenance": {"record_metadata_sha256_verified": True},
            },
        ]
    )
    return manifest


def authorization_v2_for(manifest_path: Path, body3: str, body4: str) -> dict[str, object]:
    auth = authorization_for(manifest_path, body3, body4)
    auth["schema"] = "project_memory_notebook_summary_batch_sanitize_authorization.v2"
    auth["scope"]["excluded_record_ids"] = [item["id"] for item in V2_EXCLUDED]
    auth["scope"]["selected_records"] = [
        {"record_ref": REF3, "metadata_sha256": sha(body3), "target_canonical_notebook": "cc-daemons"},
        {"record_ref": REF4, "metadata_sha256": sha(body4), "target_canonical_notebook": "nmbot"},
    ]
    return auth


def manifest_v3_for(body3: str, body4: str, body5: str) -> dict[str, object]:
    manifest = manifest_v2_for(body3, body4)
    manifest["records"].extend(
        [
            {
                "disposition": "selected_for_summary_plan",
                "record_ref": {"notebook": "cc-daemons", "kind": "note", "id": "48816222b18a"},
                "metadata_sha256": sha(body5),
                "target_canonical_notebook": "nmbot",
                "provenance": {"record_metadata_sha256_verified": True},
            },
        ]
    )
    return manifest


def authorization_v3_for(manifest_path: Path, body4: str, body5: str) -> dict[str, object]:
    ref5 = {"notebook": "cc-daemons", "kind": "note", "id": "48816222b18a"}
    auth = authorization_for(manifest_path, body4, body5)
    auth["schema"] = "project_memory_notebook_summary_batch_sanitize_authorization.v3"
    auth["scope"]["excluded_record_ids"] = [item["id"] for item in V3_EXCLUDED]
    auth["scope"]["selected_records"] = [
        {"record_ref": REF4, "metadata_sha256": sha(body4), "target_canonical_notebook": "nmbot"},
        {"record_ref": ref5, "metadata_sha256": sha(body5), "target_canonical_notebook": "nmbot"},
    ]
    auth["continue_on_policy_block"] = True
    auth["stop_on_integrity_failure"] = True
    return auth


def manifest_v4_for(body1: str, body2: str) -> dict[str, object]:
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
        "disposition_counts": {"selected_for_summary_plan": 2},
        "records": [
            {
                "disposition": "selected_for_summary_plan",
                "record_ref": REF1,
                "metadata_sha256": sha(body1),
                "target_canonical_notebook": "nmbot",
                "provenance": {"record_metadata_sha256_verified": True},
            },
            {
                "disposition": "selected_for_summary_plan",
                "record_ref": REF2,
                "metadata_sha256": sha(body2),
                "target_canonical_notebook": "nmbot",
                "provenance": {"record_metadata_sha256_verified": True},
            },
        ],
    }


def authorization_v4_for(manifest_path: Path, body1: str, body2: str) -> dict[str, object]:
    selected = [
        {"record_ref": REF1, "metadata_sha256": sha(body1), "target_canonical_notebook": "nmbot"},
        {"record_ref": REF2, "metadata_sha256": sha(body2), "target_canonical_notebook": "nmbot"},
    ]
    auth = authorization_for(manifest_path, body1, body2)
    auth["schema"] = "project_memory_notebook_summary_batch_sanitize_authorization.v4"
    auth["scope"]["excluded_record_ids"] = []
    auth["scope"]["selected_records"] = selected
    auth["scope"]["selected_records_sha256"] = hashlib.sha256(json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    auth["continue_on_policy_block"] = True
    auth["stop_on_integrity_failure"] = True
    return auth


def configure_v4_test_policy(mod, manifest_path: Path, auth: dict[str, object]) -> None:
    mod.V4_MANIFEST_SHA256 = file_sha(manifest_path)
    mod.V4_SELECTED_RECORDS_SHA256 = auth["scope"]["selected_records_sha256"]
    mod.V4_MANIFEST_RECORD_COUNT = 602
    mod.V4_MANIFEST_HELD_UNRESOLVED_COUNT = 556
    mod.V4_MANIFEST_SELECTED_COUNT = 2
    mod.V4_EXCLUDED_IDS = set()


def test_success_processes_all_in_order_with_no_write_flags(tmp_path: Path) -> None:
    mod = load_module()
    body1 = "Safe historical summary candidate one."
    body2 = "Safe historical summary candidate two."
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_for(body1, body2))
    auth = authorization_for(manifest_path, body1, body2)
    root = tmp_path / "storage"
    write_record(root, REF1, body1)
    write_record(root, REF2, body2)

    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), auth, manifest_sha256=file_sha(manifest_path), storage_root=root)

    assert payload["ok"] is True
    assert payload["candidate_count"] == 2
    assert payload["authorized_candidate_count"] == 2
    assert payload["processed_count"] == 2
    assert [item["record_ref"] for item in payload["processed_records"]] == [REF1, REF2]
    assert all(item["decision"] == "safe_for_human_summary_draft" for item in payload["processed_records"])
    assert payload["notebook_write_authorized"] is False
    assert payload["summary_drafts_generated"] is False


def test_stop_first_blocks_without_reading_later_record(tmp_path: Path) -> None:
    mod = load_module()
    body1 = "Do not pass when api_key = DO-NOT-LEAK-SECRET-FIXTURE appears."
    body2 = "Safe later body must not be read."
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_for(body1, body2))
    auth = authorization_for(manifest_path, body1, body2)
    root = tmp_path / "storage"
    write_record(root, REF1, body1)
    write_record(root, REF2, body2)
    opened: list[str] = []
    original = mod._read_exact_record_body

    def spy(storage_root: Path, workspace: str, ref: dict[str, str]) -> str:
        opened.append(ref["id"])
        return original(storage_root, workspace, ref)

    mod._read_exact_record_body = spy

    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), auth, manifest_sha256=file_sha(manifest_path), storage_root=root)

    assert payload["ok"] is False
    assert payload["stop_reason"] == "sensitive_or_uncertain_content"
    assert payload["manifest_sha256_verified"] is True
    assert payload["authorization_exact_list_verified"] is True
    assert payload["metadata_queue_prepared"] is True
    assert payload["authorized_candidate_count"] == 2
    assert opened == [REF1["id"]]
    combined = json.dumps(payload, ensure_ascii=False)
    assert "DO-NOT-LEAK-SECRET-FIXTURE" not in combined
    assert "api_key" not in combined.lower()


def test_manifest_authorization_mismatch_stops_before_storage_read(tmp_path: Path) -> None:
    mod = load_module()
    body1 = "Safe one."
    body2 = "Safe two."
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_for(body1, body2))
    auth = authorization_for(manifest_path, body1, body2)
    auth["scope"]["selected_records"][1]["target_canonical_notebook"] = "wrong"

    def fail_if_storage_is_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("storage read must not happen before exact auth validation")

    mod._read_exact_record_body = fail_if_storage_is_read
    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), auth, manifest_sha256=file_sha(manifest_path), storage_root=tmp_path)

    assert payload["ok"] is False
    assert payload["processed_count"] == 0
    assert payload["stop_reason"] == "manifest_or_authorization_invalid"


def test_unsupported_authorization_schema_fails_closed_without_traceback_or_leakage(tmp_path: Path) -> None:
    mod = load_module()
    body1 = "Safe one."
    body2 = "Safe two."
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_for(body1, body2))
    auth = authorization_for(manifest_path, body1, body2)
    auth["schema"] = "unsupported.schema.v999"
    auth["secret_fixture"] = "DO-NOT-LEAK-SECRET-FIXTURE"

    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), auth, manifest_sha256=file_sha(manifest_path), storage_root=tmp_path)

    assert payload["ok"] is False
    assert payload["stop_reason"] == "manifest_or_authorization_invalid"
    assert payload["processed_count"] == 0
    assert payload["processed_records"] == []
    combined = json.dumps(payload, ensure_ascii=False)
    assert "unsupported.schema.v999" not in combined
    assert "DO-NOT-LEAK-SECRET-FIXTURE" not in combined
    assert str(tmp_path) not in combined
    assert not __import__("re").search(r"[0-9a-f]{64}", combined)


def test_malformed_non_dict_authorization_fails_closed_without_traceback_or_leakage(tmp_path: Path) -> None:
    mod = load_module()
    body1 = "Safe one."
    body2 = "Safe two."
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_for(body1, body2))

    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), ["DO-NOT-LEAK-SECRET-FIXTURE"], manifest_sha256=file_sha(manifest_path), storage_root=tmp_path)

    assert payload["ok"] is False
    assert payload["stop_reason"] == "manifest_or_authorization_invalid"
    assert payload["processed_count"] == 0
    assert payload["processed_records"] == []
    combined = json.dumps(payload, ensure_ascii=False)
    assert "DO-NOT-LEAK-SECRET-FIXTURE" not in combined
    assert str(tmp_path) not in combined
    assert not __import__("re").search(r"[0-9a-f]{64}", combined)


def test_v2_success_excludes_previous_batch_and_reports_authorized_count(tmp_path: Path) -> None:
    mod = load_module()
    body3 = "Safe remaining v2 candidate one."
    body4 = "Safe remaining v2 candidate two."
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_v2_for(body3, body4))
    auth = authorization_v2_for(manifest_path, body3, body4)
    root = tmp_path / "storage"
    write_record(root, REF3, body3)
    write_record(root, REF4, body4)

    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), auth, manifest_sha256=file_sha(manifest_path), storage_root=root)

    assert payload["ok"] is True
    assert payload["candidate_count"] == 2
    assert payload["authorized_candidate_count"] == 2
    assert [item["record_ref"] for item in payload["processed_records"]] == [REF3, REF4]


def test_v2_still_stop_first_policy_block_without_reading_later_record(tmp_path: Path) -> None:
    mod = load_module()
    body3 = "Transcript with customer phone +79990000000 must block v2."
    body4 = "Safe later v2 body must not be read."
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_v2_for(body3, body4))
    auth = authorization_v2_for(manifest_path, body3, body4)
    root = tmp_path / "storage"
    write_record(root, REF3, body3)
    write_record(root, REF4, body4)
    opened: list[str] = []
    original = mod._read_exact_record_body

    def spy(storage_root: Path, workspace: str, ref: dict[str, str]) -> str:
        opened.append(ref["id"])
        return original(storage_root, workspace, ref)

    mod._read_exact_record_body = spy

    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), auth, manifest_sha256=file_sha(manifest_path), storage_root=root)

    assert payload["ok"] is False
    assert payload["stop_reason"] == "sensitive_or_uncertain_content"
    assert opened == [REF3["id"]]


def test_v3_policy_block_continues_to_later_safe_record_without_leakage(tmp_path: Path) -> None:
    mod = load_module()
    body3 = "Already processed blocked v2 candidate."
    body4 = "Transcript contains customer data and api_key = DO-NOT-LEAK-SECRET-FIXTURE."
    body5 = "Safe later v3 summary candidate."
    ref5 = {"notebook": "cc-daemons", "kind": "note", "id": "48816222b18a"}
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_v3_for(body3, body4, body5))
    auth = authorization_v3_for(manifest_path, body4, body5)
    root = tmp_path / "storage"
    write_record(root, REF4, body4)
    write_record(root, ref5, body5)

    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), auth, manifest_sha256=file_sha(manifest_path), storage_root=root)

    assert payload["ok"] is True
    assert payload["decision"] == "sanitization_complete"
    assert payload["candidate_count"] == 2
    assert payload["processed_count"] == 2
    assert payload["safe_count"] == 1
    assert payload["blocked_count"] == 1
    assert [item["record_ref"] for item in payload["processed_records"]] == [REF4, ref5]
    assert payload["processed_records"][0]["decision"] == "blocked_sensitive_or_uncertain"
    assert payload["processed_records"][1]["decision"] == "safe_for_human_summary_draft"
    assert payload["summary_drafts_generated"] is False
    assert payload["notebook_write_authorized"] is False
    combined = json.dumps(payload, ensure_ascii=False)
    assert "DO-NOT-LEAK-SECRET-FIXTURE" not in combined
    assert "api_key" not in combined.lower()
    assert "Transcript contains" not in combined


def test_v3_first_sha_failure_stops_and_proves_later_record_unread(tmp_path: Path) -> None:
    mod = load_module()
    body3 = "Already processed blocked v2 candidate."
    body4 = "Stored body differs from authorized metadata hash."
    body5 = "Later safe v3 body must not be read after integrity failure."
    ref5 = {"notebook": "cc-daemons", "kind": "note", "id": "48816222b18a"}
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_v3_for(body3, "different authorized body", body5))
    auth = authorization_v3_for(manifest_path, "different authorized body", body5)
    root = tmp_path / "storage"
    write_record(root, REF4, body4)
    write_record(root, ref5, body5)
    opened: list[str] = []
    original = mod._read_exact_record_body

    def spy(storage_root: Path, workspace: str, ref: dict[str, str]) -> str:
        opened.append(ref["id"])
        return original(storage_root, workspace, ref)

    mod._read_exact_record_body = spy

    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), auth, manifest_sha256=file_sha(manifest_path), storage_root=root)

    assert payload["ok"] is False
    assert payload["stop_reason"] == "record_missing_malformed_or_hash_mismatch"
    assert payload["processed_count"] == 1
    assert opened == [REF4["id"]]
    assert ref5["id"] not in json.dumps(payload, ensure_ascii=False)


def test_v3_missing_and_malformed_stop_before_later_record(tmp_path: Path) -> None:
    mod = load_module()
    body3 = "Already processed blocked v2 candidate."
    body4 = "Missing first v3 body."
    body5 = "Later safe v3 body must not be read."
    ref5 = {"notebook": "cc-daemons", "kind": "note", "id": "48816222b18a"}
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_v3_for(body3, body4, body5))
    auth = authorization_v3_for(manifest_path, body4, body5)
    root = tmp_path / "storage"
    write_record(root, ref5, body5)
    opened: list[str] = []
    original = mod._read_exact_record_body

    def spy(storage_root: Path, workspace: str, ref: dict[str, str]) -> str:
        opened.append(ref["id"])
        return original(storage_root, workspace, ref)

    mod._read_exact_record_body = spy

    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), auth, manifest_sha256=file_sha(manifest_path), storage_root=root)

    assert payload["ok"] is False
    assert payload["stop_reason"] == "record_missing_malformed_or_hash_mismatch"
    assert opened == [REF4["id"]]

    write_json(record_path(root, REF4), {"note_id": REF4["id"], "note": body4})
    record_path(root, REF4).write_text("{malformed", encoding="utf-8")
    opened.clear()
    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), auth, manifest_sha256=file_sha(manifest_path), storage_root=root)

    assert payload["ok"] is False
    assert payload["stop_reason"] == "record_missing_malformed_or_hash_mismatch"
    assert opened == [REF4["id"]]


def test_v4_policy_block_continues_aggregate_only_without_leakage(tmp_path: Path) -> None:
    mod = load_module()
    body1 = "Transcript contains customer phone +79990000000 and api_key = DO-NOT-LEAK-SECRET-FIXTURE."
    body2 = "Safe later v4 summary candidate."
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_v4_for(body1, body2))
    auth = authorization_v4_for(manifest_path, body1, body2)
    configure_v4_test_policy(mod, manifest_path, auth)
    root = tmp_path / "storage"
    write_record(root, REF1, body1)
    write_record(root, REF2, body2)

    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), auth, manifest_sha256=file_sha(manifest_path), storage_root=root)

    assert payload["ok"] is True
    assert payload["decision"] == "sanitization_complete"
    assert payload["candidate_count"] == 2
    assert payload["processed_count"] == 2
    assert payload["safe_count"] == 1
    assert payload["blocked_count"] == 1
    assert payload["integrity_failure_count"] == 0
    assert payload["processed_records"] == []
    assert payload["notebook_write_authorized"] is False
    assert payload["summary_drafts_generated"] is False
    combined = json.dumps(payload, ensure_ascii=False)
    assert "DO-NOT-LEAK-SECRET-FIXTURE" not in combined
    assert "api_key" not in combined.lower()
    assert "Transcript contains" not in combined


def test_v4_first_integrity_failure_stops_before_later_record(tmp_path: Path) -> None:
    mod = load_module()
    body1 = "Stored body differs from authorized metadata hash."
    body2 = "Later safe v4 body must not be read after integrity failure."
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_v4_for("different authorized body", body2))
    auth = authorization_v4_for(manifest_path, "different authorized body", body2)
    configure_v4_test_policy(mod, manifest_path, auth)
    root = tmp_path / "storage"
    write_record(root, REF1, body1)
    write_record(root, REF2, body2)
    opened: list[str] = []
    original = mod._read_exact_record_body

    def spy(storage_root: Path, workspace: str, ref: dict[str, str]) -> str:
        opened.append(ref["id"])
        return original(storage_root, workspace, ref)

    mod._read_exact_record_body = spy

    payload = mod.sanitize_batch(json.loads(manifest_path.read_text()), auth, manifest_sha256=file_sha(manifest_path), storage_root=root)

    assert payload["ok"] is False
    assert payload["stop_reason"] == "record_missing_malformed_or_hash_mismatch"
    assert payload["processed_count"] == 1
    assert payload["integrity_failure_count"] == 1
    assert payload["processed_records"] == []
    assert opened == [REF1["id"]]

def test_checked_in_v2_authorization_is_exact_remaining_v1_queue() -> None:
    v1 = json.loads((ROOT / "data" / "notebooklm_summary_batch_sanitize_authorization_v1.json").read_text(encoding="utf-8"))
    v2 = json.loads((ROOT / "data" / "notebooklm_summary_batch_sanitize_authorization_v2.json").read_text(encoding="utf-8"))
    v1_ids = [item["record_ref"]["id"] for item in v1["scope"]["selected_records"]]
    v2_ids = [item["record_ref"]["id"] for item in v2["scope"]["selected_records"]]
    forbidden_ids = {"0f2c83dd6879", "20a382a56022", "2a7edfa48439", "41b55e418687", "41c9ca464ecd"}

    assert v2["schema"] == "project_memory_notebook_summary_batch_sanitize_authorization.v2"
    assert v2["owner"] == "ser"
    assert v2["rollback_owner"] == "ser"
    assert v2["manifest"]["sha256"] == "d374d1a4456a05947a56415fdb3494c59282350eb7f470667d831d135749a2d6"
    assert v2["scope"]["candidate_count"] == 33
    assert len(v2["scope"]["selected_records"]) == 33
    assert v2_ids == v1_ids[2:]
    assert set(v2["scope"]["excluded_record_ids"]) == forbidden_ids
    assert not forbidden_ids.intersection(v2_ids)
    for key in (
        "write_performed",
        "notebook_mutation_performed",
        "automatic_routing_changed",
        "production_verified",
        "migration_performed",
        "data_deletion_authorized",
        "data_migration_authorized",
        "notebook_write_authorized",
        "notebook_write_performed",
        "routing_write_authorized",
        "delete_authorized",
        "summary_draft_generation_authorized",
    ):
        assert v2[key] is False
    combined = json.dumps(v2, ensure_ascii=False).lower()
    assert all(token not in combined for token in ("title", "body", "content", "path", "root", "secret"))


def test_checked_in_v3_authorization_is_exact_remaining_v2_queue_with_continue_flags() -> None:
    v2 = json.loads((ROOT / "data" / "notebooklm_summary_batch_sanitize_authorization_v2.json").read_text(encoding="utf-8"))
    v3 = json.loads((ROOT / "data" / "notebooklm_summary_batch_sanitize_authorization_v3.json").read_text(encoding="utf-8"))
    v2_ids = [item["record_ref"]["id"] for item in v2["scope"]["selected_records"]]
    v3_ids = [item["record_ref"]["id"] for item in v3["scope"]["selected_records"]]
    forbidden_ids = {"0f2c83dd6879", "20a382a56022", "2a7edfa48439", "41b55e418687", "41c9ca464ecd", "41f4c8962e73"}

    assert v3["schema"] == "project_memory_notebook_summary_batch_sanitize_authorization.v3"
    assert v3["owner"] == "ser"
    assert v3["rollback_owner"] == "ser"
    assert v3["manifest"]["sha256"] == "d374d1a4456a05947a56415fdb3494c59282350eb7f470667d831d135749a2d6"
    assert v3["scope"]["candidate_count"] == 32
    assert len(v3["scope"]["selected_records"]) == 32
    assert v3_ids == v2_ids[1:]
    assert set(v3["scope"]["excluded_record_ids"]) == forbidden_ids
    assert not forbidden_ids.intersection(v3_ids)
    assert v3["continue_on_policy_block"] is True
    assert v3["stop_on_integrity_failure"] is True
    for key in (
        "write_performed",
        "notebook_mutation_performed",
        "automatic_routing_changed",
        "production_verified",
        "migration_performed",
        "data_deletion_authorized",
        "data_migration_authorized",
        "notebook_write_authorized",
        "notebook_write_performed",
        "routing_write_authorized",
        "delete_authorized",
        "summary_draft_generation_authorized",
    ):
        assert v3[key] is False
    combined = json.dumps(v3, ensure_ascii=False).lower()
    assert all(token not in combined for token in ("title", "body", "content", "path", "root", "secret"))


def test_checked_in_v4_authorization_is_exact_fresh_42_no_write_and_no_leaks() -> None:
    manifest_path = Path("/tmp/opencode/nmbot_notebook_pre_migration_manifest_refresh_20260727T000000Z.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    v4 = json.loads((ROOT / "data" / "notebooklm_summary_batch_sanitize_authorization_v4.json").read_text(encoding="utf-8"))
    selected = [
        {"record_ref": r["record_ref"], "metadata_sha256": r["metadata_sha256"], "target_canonical_notebook": r["target_canonical_notebook"]}
        for r in manifest["records"]
        if r.get("disposition") == "selected_for_summary_plan"
    ]

    assert v4["schema"] == "project_memory_notebook_summary_batch_sanitize_authorization.v4"
    assert v4["owner"] == "ser"
    assert v4["rollback_owner"] == "ser"
    assert v4["manifest"]["sha256"] == file_sha(manifest_path)
    assert v4["manifest"]["sha256"] == "0ee513d98213b3418c29cc269fec9c959a686a50172b66ca405c11442ad4f2ae"
    assert v4["scope"]["candidate_count"] == 42
    assert len(v4["scope"]["selected_records"]) == 42
    assert v4["scope"]["selected_records"] == selected
    assert v4["scope"]["selected_records_sha256"] == hashlib.sha256(json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert v4["scope"]["excluded_record_ids"] == []
    assert v4["destination_policy"] == "canonical_only"
    assert v4["continue_on_policy_block"] is True
    assert v4["stop_on_integrity_failure"] is True
    for key in (
        "write_performed",
        "notebook_mutation_performed",
        "automatic_routing_changed",
        "production_verified",
        "migration_performed",
        "data_deletion_authorized",
        "data_migration_authorized",
        "notebook_write_authorized",
        "notebook_write_performed",
        "routing_write_authorized",
        "delete_authorized",
        "summary_draft_generation_authorized",
    ):
        assert v4[key] is False
    combined = json.dumps(v4, ensure_ascii=False).lower()
    assert all(token not in combined for token in ("title", "body", "content", "path", "root", "secret"))


def test_checked_in_batch_authorization_fails_closed_against_corrected_v2_manifest() -> None:
    mod = load_module()
    manifest_path = Path("/tmp/opencode/nmbot_notebook_pre_migration_manifest_v2.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    authorization = json.loads((ROOT / "data" / "notebooklm_summary_batch_sanitize_authorization_v3.json").read_text(encoding="utf-8"))

    payload = mod.sanitize_batch(
        manifest,
        authorization,
        manifest_sha256=file_sha(manifest_path),
        storage_root=ROOT,
    )

    assert payload["ok"] is False
    assert payload["stop_reason"] == "manifest_or_authorization_invalid"
    assert payload["processed_count"] == 0
    assert payload["notebook_write_authorized"] is False


def test_cli_output_does_not_leak_title_body_root_path_secret_or_sha_on_failure(tmp_path: Path) -> None:
    body1 = "Sensitive api_key = DO-NOT-LEAK-SECRET-FIXTURE."
    body2 = "Safe later body."
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest_for(body1, body2))
    auth_path = tmp_path / "auth.json"
    write_json(auth_path, authorization_for(manifest_path, body1, body2))
    root = tmp_path / "storage"
    write_record(root, REF1, body1)
    write_record(root, REF2, body2)

    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest_path), "--authorization", str(auth_path), "--storage-root", str(root), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert run.returncode == 2
    combined = run.stdout + run.stderr
    assert body1 not in combined
    assert "DO-NOT-LEAK-TITLE" not in combined
    assert str(root) not in combined
    assert str(record_path(root, REF1)) not in combined
    assert "DO-NOT-LEAK-SECRET-FIXTURE" not in combined
    assert "api_key" not in combined.lower()
    assert not __import__("re").search(r"[0-9a-f]{64}", combined)


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
        "import nmbot_context_gate",
        "from nmbot_context_gate",
        "import nmbot_runtime",
        "from nmbot_runtime",
    ]
    source = SCRIPT.read_text(encoding="utf-8").lower()
    assert not any(token in source for token in banned)
