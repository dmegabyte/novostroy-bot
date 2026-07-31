#!/usr/bin/env python3
"""Build a safe local-only plan for the approved nine NotebookLM add_note writes.

The planner validates the fresh v4 sanitize authorization, the draft-generation
authorization, and a separate human-approved write authorization. It never calls
NotebookLM, MCP, network clients or subprocesses, and it does not write records.
Output is intentionally aggregate-only: operation ids, source refs and hashes,
not note bodies or raw source text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WRITE_AUTHORIZATION = ROOT / "data" / "notebooklm_summary_batch_write_authorization_safe_v1.json"
DEFAULT_V4_NO_WRITE_AUTHORIZATION = ROOT / "data" / "notebooklm_summary_batch_sanitize_authorization_v4.json"
DEFAULT_DRAFT_AUTHORIZATION = ROOT / "data" / "notebooklm_summary_draft_generation_authorization_v1.json"
DEFAULT_OUTCOME_LEDGER = ROOT / "data" / "notebooklm_summary_batch_write_outcome_20260727.json"

SCHEMA = "project_memory_notebook_summary_batch_write_plan.safe_v1"
WRITE_AUTH_SCHEMA = "project_memory_notebook_summary_batch_write_authorization.safe_v1"
V4_NO_WRITE_AUTH_SCHEMA = "project_memory_notebook_summary_batch_sanitize_authorization.v4"
DRAFT_AUTH_SCHEMA = "project_memory_notebook_summary_draft_generation_authorization.v1"
OUTCOME_SCHEMA = "project_memory_notebook_summary_batch_write_outcome.v1"
AUTHORIZATION_TYPE = "manual_notebooklm_nine_note_write_after_review"
OWNER = "ser"
WORKSPACE = "default"
TARGET_NOTEBOOK = "nmbot"
DESTINATION_POLICY = "canonical_only"
ALLOWED_OPERATION = "add_note"
WRITE_COUNT = 9
USER_APPROVAL_MARKER = "User explicitly approved exactly nine canonical nmbot historical summary writes"
EXPECTED_MANIFEST_SHA256 = "0ee513d98213b3418c29cc269fec9c959a686a50172b66ca405c11442ad4f2ae"
EXPECTED_WRITE_AUTHORIZATION_SHA256 = "1c69d53641609cab55a63ed41ef9efc8772ae0e4f3a38ad466cdcb135fd335d0"
EXPECTED_V4_NO_WRITE_AUTH_SHA256 = "167c130f3934d2af2e1d1e1da0cbafa6ec7848778f570e8cfcc23974b3cedcd0"
EXPECTED_DRAFT_AUTH_SHA256 = "85feebac85647f24d3ae0ede67e14d7bdb963b669e85ea302bb758be022016c1"

AUTHORIZED_OPERATION_IDS = [
    "nmbot-historical-63fe2a82d388",
    "nmbot-historical-6b009e73dc4f",
    "nmbot-historical-780baff13512",
    "nmbot-historical-7c55cb6a2e02",
    "nmbot-historical-8598e0403dcc",
    "nmbot-historical-9fa4704474e3",
    "nmbot-historical-a8ddd44552de",
    "nmbot-historical-f502ffe22e53",
    "nmbot-historical-fe717093b1e9",
]

EXPECTED_CONTRACTS: dict[str, dict[str, str]] = {
    "nmbot-historical-63fe2a82d388": {
        "source_id": "63fe2a82d388",
        "title_sha256": "dca3c44e40826a795937e98137bb2f260660c6b10075d84575dfdb7eb0dd99da",
        "content_sha256": "07a5b8f3fbd76d394a754ae2794a8d6e315400e0059bf14a2661529bb6d90e66",
    },
    "nmbot-historical-6b009e73dc4f": {
        "source_id": "6b009e73dc4f",
        "title_sha256": "21010eec5da2b6ad051830cc5444ced42d00e1a95dd9835028e25375ccb74590",
        "content_sha256": "e0e8d96c8daa6bbf899eb12ffd2d1b6dee9e586e95b66b99629caf44c144219d",
    },
    "nmbot-historical-780baff13512": {
        "source_id": "780baff13512",
        "title_sha256": "e7ab98b80e136755fb11a461cac3c6a69bc3bd09f4ae5d16480cefca064cbebb",
        "content_sha256": "1aab20191eb8bb1b5911d866e0968096474cd44223507e8ab378caf641f94485",
    },
    "nmbot-historical-7c55cb6a2e02": {
        "source_id": "7c55cb6a2e02",
        "title_sha256": "4337012def0de76434bc4233e065dc3ea2579756fccf80e99735564167ea292d",
        "content_sha256": "a4b5f724e0e539336ec80f06cbb3d6147c2ee49e85ffd95d9fe356e0e5fb72ce",
    },
    "nmbot-historical-8598e0403dcc": {
        "source_id": "8598e0403dcc",
        "title_sha256": "9da7717aba36ed534e8c9dee3551ae4a42d9779c97cef7623270cd568dbe363a",
        "content_sha256": "1c391a6aff2cd1aa1c2f72395a67783b5abf472e3c6cda413c7d51b088ff8694",
    },
    "nmbot-historical-9fa4704474e3": {
        "source_id": "9fa4704474e3",
        "title_sha256": "5aa2175158dfefceb5b8052c1135c7a8ecf45a73910fa50a3544e3e2627ba80a",
        "content_sha256": "c6924fd44447df32b9d4927d24222844f9cd0a48e6c47c1adf037e9ee4389e53",
    },
    "nmbot-historical-a8ddd44552de": {
        "source_id": "a8ddd44552de",
        "title_sha256": "852dc900372a062fa4dbfa5caa44a1cedb6e13050807c2f479fe40215d65dbdb",
        "content_sha256": "8324a253091cc9274c3665f12a0a2e7b757c475fbe578732fa88afec0dfc2820",
    },
    "nmbot-historical-f502ffe22e53": {
        "source_id": "f502ffe22e53",
        "title_sha256": "e91f2e7404bb089783ef61d9660e0371beb15219e401da312f6677f35a78a10f",
        "content_sha256": "80ba29a2b7feb15b008d4ae260077496eac1c4d37df7ac5b3275590caa973488",
    },
    "nmbot-historical-fe717093b1e9": {
        "source_id": "fe717093b1e9",
        "title_sha256": "3284b8d4a677967d9bece6d768e6c1a234b06e5360371d4b5cde52f48926e855",
        "content_sha256": "ed963935d9620d50b738a0931cbcdbbc4c51d90365a837a1fe369cd7b4d6555d",
    },
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_NOTE_ID_RE = re.compile(r"^[0-9a-f]{12}$")
FORBIDDEN_OUTPUT_RE = re.compile(r"(?i)(raw source|transcript|storage root|secret|password|token|authorization\s*[:=]|bearer\s+[a-z0-9._~+/=-]{8,}|api[_-]?key|traceback|stdout|stderr|stack trace)")


def _load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _base_payload() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "ok": False,
        "decision": "blocked_or_failed_closed",
        "write_count": 0,
        "target_notebook": TARGET_NOTEBOOK,
        "destination_policy": DESTINATION_POLICY,
        "allowed_operation": ALLOWED_OPERATION,
        "notebook_write_authorized": False,
        "notebook_write_performed": False,
        "data_deletion_authorized": False,
        "source_mutation_authorized": False,
        "routing_change_authorized": False,
        "production_claim_authorized": False,
        "manifest_sha256_verified": False,
        "v4_no_write_authorization_sha256_verified": False,
        "draft_authorization_sha256_verified": False,
        "authorization_sha256": None,
        "outcome_ledger_verified": False,
        "idempotency_blocked": False,
        "operations": [],
        "errors": [],
    }


def _error(code: str) -> dict[str, Any]:
    payload = _base_payload()
    payload["denied_reason"] = code
    payload["errors"] = [{"code": code, "message": "nine-note write plan denied"}]
    return payload


def _consumed_payload(*, authorization_sha256: str) -> dict[str, Any]:
    payload = _base_payload()
    payload.update(
        {
            "decision": "batch_already_applied",
            "denied_reason": "batch_already_applied",
            "idempotency_blocked": True,
            "notebook_write_authorized": False,
            "authorization_sha256": authorization_sha256,
            "manifest_sha256_verified": True,
            "v4_no_write_authorization_sha256_verified": True,
            "draft_authorization_sha256_verified": True,
            "outcome_ledger_verified": True,
            "errors": [
                {
                    "code": "batch_already_applied",
                    "message": "nine-note NotebookLM batch authorization is already consumed; no writes are authorized",
                }
            ],
        }
    )
    return payload


def _source_ref(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"notebook", "kind", "id"}:
        raise ValueError("source_ref_invalid")
    if not all(isinstance(value[key], str) and value[key] for key in value):
        raise ValueError("source_ref_invalid")
    if value["kind"] != "note":
        raise ValueError("source_kind_invalid")
    return {"notebook": value["notebook"], "kind": value["kind"], "id": value["id"]}


def _v4_sources(v4_auth: dict[str, Any], *, v4_sha256: str) -> dict[str, dict[str, str]]:
    if v4_sha256 != EXPECTED_V4_NO_WRITE_AUTH_SHA256 or v4_auth.get("schema") != V4_NO_WRITE_AUTH_SCHEMA:
        raise ValueError("v4_no_write_authorization_invalid")
    manifest = v4_auth.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("sha256") != EXPECTED_MANIFEST_SHA256:
        raise ValueError("manifest_binding_invalid")
    scope = v4_auth.get("scope")
    records = scope.get("selected_records") if isinstance(scope, dict) else None
    if not isinstance(records, list):
        raise ValueError("v4_selected_records_invalid")
    sources: dict[str, dict[str, str]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        ref = _source_ref(item.get("record_ref"))
        sha = item.get("metadata_sha256")
        target = item.get("target_canonical_notebook")
        if isinstance(sha, str) and SHA256_RE.match(sha) and target == TARGET_NOTEBOOK:
            sources[ref["id"]] = {"source_ref": ref, "source_sha256": sha}  # type: ignore[dict-item]
    return sources


def _validate_draft_auth(draft_auth: dict[str, Any], *, draft_sha256: str, v4_sha256: str) -> None:
    if draft_sha256 != EXPECTED_DRAFT_AUTH_SHA256 or draft_auth.get("schema") != DRAFT_AUTH_SCHEMA:
        raise ValueError("draft_authorization_invalid")
    manifest = draft_auth.get("manifest")
    no_write = draft_auth.get("no_write_sanitize_authorization")
    permissions = draft_auth.get("permissions")
    if not isinstance(manifest, dict) or manifest.get("sha256") != EXPECTED_MANIFEST_SHA256:
        raise ValueError("draft_manifest_binding_invalid")
    if not isinstance(no_write, dict) or no_write.get("sha256") != v4_sha256:
        raise ValueError("draft_no_write_binding_invalid")
    if not isinstance(permissions, dict) or permissions.get("notebook_write_authorized") is not False or permissions.get("notebook_write_performed") is not False:
        raise ValueError("draft_write_boundary_invalid")


def _validate_footer(content: str, ref: dict[str, str], source_sha256: str) -> None:
    expected_footer = f"Provenance: source record {ref['notebook']}/{ref['kind']}/{ref['id']}; source SHA {source_sha256}; status: historical note, not current code or production proof."
    if not content.endswith("\n\n---\n" + expected_footer):
        raise ValueError("provenance_footer_mismatch")
    if "not current code or production proof" not in content or "historical note" not in content:
        raise ValueError("historical_status_missing")


def _validate_operation(value: Any, expected_id: str, v4_sources: dict[str, dict[str, str]]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("operation_invalid")
    required = {"operation_id", "operation", "target_notebook", "title", "content", "source_ref", "source_sha256", "title_sha256", "content_sha256"}
    if set(value) != required:
        raise ValueError("operation_keys_invalid")
    if value.get("operation_id") != expected_id or value.get("operation") != ALLOWED_OPERATION or value.get("target_notebook") != TARGET_NOTEBOOK:
        raise ValueError("operation_identity_mismatch")
    contract = EXPECTED_CONTRACTS[expected_id]
    ref = _source_ref(value.get("source_ref"))
    if ref["id"] != contract["source_id"]:
        raise ValueError("source_ref_mismatch")
    v4 = v4_sources.get(ref["id"])
    if not v4 or v4["source_ref"] != ref or v4["source_sha256"] != value.get("source_sha256"):
        raise ValueError("source_not_bound_to_v4_authorization")
    if value.get("title_sha256") != contract["title_sha256"] or _sha256_text(str(value.get("title"))) != contract["title_sha256"]:
        raise ValueError("title_sha_mismatch")
    if value.get("content_sha256") != contract["content_sha256"] or _sha256_text(str(value.get("content"))) != contract["content_sha256"]:
        raise ValueError("content_sha_mismatch")
    _validate_footer(str(value.get("content")), ref, str(value.get("source_sha256")))
    return {
        "operation_id": expected_id,
        "operation": ALLOWED_OPERATION,
        "target_notebook": TARGET_NOTEBOOK,
        "source_ref": ref,
        "source_sha256": value["source_sha256"],
        "title_sha256": contract["title_sha256"],
        "content_sha256": contract["content_sha256"],
    }


def validate_outcome_ledger(outcome_ledger: Any, *, write_auth_sha256: str) -> None:
    expected_keys = {
        "schema",
        "workspace",
        "target_notebook",
        "write_authorization_sha256",
        "write_count",
        "all_metadata_sha256_verified",
        "source_mutation_performed",
        "data_deletion_performed",
        "automatic_routing_changed",
        "production_claim_made",
        "records",
    }
    if not isinstance(outcome_ledger, dict):
        raise ValueError("outcome_ledger_not_dict")
    if set(outcome_ledger) != expected_keys:
        raise ValueError("outcome_ledger_keys_invalid")
    if outcome_ledger.get("schema") != OUTCOME_SCHEMA:
        raise ValueError("outcome_ledger_schema_invalid")
    if outcome_ledger.get("workspace") != WORKSPACE or outcome_ledger.get("target_notebook") != TARGET_NOTEBOOK:
        raise ValueError("outcome_ledger_destination_invalid")
    if outcome_ledger.get("write_authorization_sha256") != EXPECTED_WRITE_AUTHORIZATION_SHA256 or write_auth_sha256 != EXPECTED_WRITE_AUTHORIZATION_SHA256:
        raise ValueError("outcome_ledger_authorization_sha_mismatch")
    if outcome_ledger.get("write_count") != WRITE_COUNT or outcome_ledger.get("all_metadata_sha256_verified") is not True:
        raise ValueError("outcome_ledger_count_or_metadata_invalid")
    for key in ("source_mutation_performed", "data_deletion_performed", "automatic_routing_changed", "production_claim_made"):
        if outcome_ledger.get(key) is not False:
            raise ValueError(f"outcome_ledger_forbidden_flag:{key}")
    records = outcome_ledger.get("records")
    if not isinstance(records, list) or len(records) != WRITE_COUNT:
        raise ValueError("outcome_ledger_record_count_invalid")
    seen_note_ids: set[str] = set()
    seen_source_ids: set[str] = set()
    expected_source_ids = [EXPECTED_CONTRACTS[operation_id]["source_id"] for operation_id in AUTHORIZED_OPERATION_IDS]
    for record, operation_id, expected_source_id in zip(records, AUTHORIZED_OPERATION_IDS, expected_source_ids, strict=True):
        if not isinstance(record, dict) or set(record) != {"source_id", "note_id", "content_sha256", "metadata_verified"}:
            raise ValueError("outcome_ledger_record_keys_invalid")
        note_id = record.get("note_id")
        source_id = record.get("source_id")
        content_sha256 = record.get("content_sha256")
        if source_id != expected_source_id:
            raise ValueError("outcome_ledger_source_id_mismatch")
        if source_id in seen_source_ids:
            raise ValueError("outcome_ledger_duplicate_source_id")
        seen_source_ids.add(source_id)
        if not isinstance(note_id, str) or not SAFE_NOTE_ID_RE.match(note_id):
            raise ValueError("outcome_ledger_note_id_unsafe")
        if note_id in seen_note_ids:
            raise ValueError("outcome_ledger_duplicate_note_id")
        seen_note_ids.add(note_id)
        if content_sha256 != EXPECTED_CONTRACTS[operation_id]["content_sha256"]:
            raise ValueError("outcome_ledger_content_sha_mismatch")
        if record.get("metadata_verified") is not True:
            raise ValueError("outcome_ledger_metadata_not_verified")
    if seen_source_ids != set(expected_source_ids) or len(seen_note_ids) != WRITE_COUNT:
        raise ValueError("outcome_ledger_uniqueness_invalid")


def validate_and_build_batch_write_plan(
    write_auth: dict[str, Any],
    v4_no_write_auth: dict[str, Any],
    draft_auth: dict[str, Any],
    *,
    write_auth_sha256: str,
    v4_no_write_auth_sha256: str,
    draft_auth_sha256: str,
) -> dict[str, Any]:
    try:
        v4_sources = _v4_sources(v4_no_write_auth, v4_sha256=v4_no_write_auth_sha256)
        _validate_draft_auth(draft_auth, draft_sha256=draft_auth_sha256, v4_sha256=v4_no_write_auth_sha256)
        expected_keys = {
            "schema",
            "authorization_type",
            "owner",
            "rollback_owner",
            "user_approval",
            "target_notebook",
            "destination_policy",
            "allowed_operation",
            "maximum_write_count",
            "write_permitted",
            "write_performed",
            "notebook_write_authorized",
            "notebook_write_performed",
            "data_deletion_authorized",
            "source_mutation_authorized",
            "routing_change_authorized",
            "production_claim_authorized",
            "migration_performed",
            "automatic_routing_changed",
            "production_verified",
            "manifest",
            "v4_no_write_authorization",
            "draft_authorization",
            "boundaries",
            "operations",
        }
        if set(write_auth) != expected_keys:
            raise ValueError("authorization_keys_invalid")
        if write_auth.get("schema") != WRITE_AUTH_SCHEMA or write_auth.get("authorization_type") != AUTHORIZATION_TYPE:
            raise ValueError("authorization_schema_invalid")
        if write_auth.get("owner") != OWNER or write_auth.get("rollback_owner") != OWNER:
            raise ValueError("authorization_owner_invalid")
        approval = write_auth.get("user_approval")
        if not isinstance(approval, dict) or approval.get("approved") is not True or approval.get("marker") != USER_APPROVAL_MARKER or approval.get("approved_write_count") != WRITE_COUNT:
            raise ValueError("explicit_user_approval_missing")
        if write_auth.get("target_notebook") != TARGET_NOTEBOOK or write_auth.get("destination_policy") != DESTINATION_POLICY or write_auth.get("allowed_operation") != ALLOWED_OPERATION:
            raise ValueError("authorization_destination_invalid")
        if write_auth.get("maximum_write_count") != WRITE_COUNT:
            raise ValueError("write_count_mismatch")
        for key in ("write_permitted", "notebook_write_authorized"):
            if write_auth.get(key) is not True:
                raise ValueError(f"required_flag_mismatch:{key}")
        for key in (
            "write_performed",
            "notebook_write_performed",
            "data_deletion_authorized",
            "source_mutation_authorized",
            "routing_change_authorized",
            "production_claim_authorized",
            "migration_performed",
            "automatic_routing_changed",
            "production_verified",
        ):
            if write_auth.get(key) is not False:
                raise ValueError(f"forbidden_flag_mismatch:{key}")
        manifest = write_auth.get("manifest")
        v4_binding = write_auth.get("v4_no_write_authorization")
        draft_binding = write_auth.get("draft_authorization")
        if not isinstance(manifest, dict) or manifest.get("sha256") != EXPECTED_MANIFEST_SHA256:
            raise ValueError("manifest_binding_invalid")
        if not isinstance(v4_binding, dict) or v4_binding.get("schema") != V4_NO_WRITE_AUTH_SCHEMA or v4_binding.get("sha256") != v4_no_write_auth_sha256:
            raise ValueError("v4_authorization_binding_invalid")
        if not isinstance(draft_binding, dict) or draft_binding.get("schema") != DRAFT_AUTH_SCHEMA or draft_binding.get("sha256") != draft_auth_sha256:
            raise ValueError("draft_authorization_binding_invalid")
        boundaries = write_auth.get("boundaries")
        if not isinstance(boundaries, dict) or set(boundaries) != {"exactly_nine_new_notebooklm_notes_only", "no_deletion", "no_source_mutation", "no_routing_change", "no_production_claim", "no_raw_source_text"}:
            raise ValueError("boundaries_invalid")
        if not all(boundaries.get(key) is True for key in boundaries):
            raise ValueError("boundary_flag_mismatch")
        operations = write_auth.get("operations")
        if not isinstance(operations, list) or len(operations) != WRITE_COUNT:
            raise ValueError("operation_count_invalid")
        safe_operations = [_validate_operation(item, expected_id, v4_sources) for item, expected_id in zip(operations, AUTHORIZED_OPERATION_IDS, strict=True)]
        if len({item["operation_id"] for item in safe_operations}) != WRITE_COUNT:
            raise ValueError("duplicate_operation_id")
    except (ValueError, TypeError, KeyError) as exc:
        return _error(str(exc) or "authorization_or_binding_invalid")
    except Exception as exc:  # fail closed for zip(strict=True) and malformed inputs
        return _error(str(exc) or "authorization_or_binding_invalid")

    payload = _base_payload()
    payload.update(
        {
            "ok": True,
            "decision": "ready_for_manual_notebooklm_add_note_execution_no_writes_performed",
            "write_count": WRITE_COUNT,
            "notebook_write_authorized": True,
            "manifest_sha256_verified": True,
            "v4_no_write_authorization_sha256_verified": True,
            "draft_authorization_sha256_verified": True,
            "authorization_sha256": write_auth_sha256,
            "operations": safe_operations,
            "errors": [],
        }
    )
    if FORBIDDEN_OUTPUT_RE.search(json.dumps(payload, ensure_ascii=False, sort_keys=True)):
        return _error("unsafe_output_filter_failed")
    return payload


def validate_consumed_or_build_batch_write_plan(
    write_auth: dict[str, Any],
    v4_no_write_auth: dict[str, Any],
    draft_auth: dict[str, Any],
    *,
    outcome_ledger: Any,
    write_auth_sha256: str,
    v4_no_write_auth_sha256: str,
    draft_auth_sha256: str,
) -> dict[str, Any]:
    authorization_payload = validate_and_build_batch_write_plan(
        write_auth,
        v4_no_write_auth,
        draft_auth,
        write_auth_sha256=write_auth_sha256,
        v4_no_write_auth_sha256=v4_no_write_auth_sha256,
        draft_auth_sha256=draft_auth_sha256,
    )
    if authorization_payload.get("ok") is not True:
        return authorization_payload
    try:
        validate_outcome_ledger(outcome_ledger, write_auth_sha256=write_auth_sha256)
    except (ValueError, TypeError, KeyError) as exc:
        return _error(str(exc) or "outcome_ledger_invalid")
    except Exception as exc:  # fail closed for malformed ledgers and zip(strict=True)
        return _error(str(exc) or "outcome_ledger_invalid")
    return _consumed_payload(authorization_sha256=write_auth_sha256)


def emit(payload: dict[str, Any], output: str | None, pretty: bool) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate that the approved nine NotebookLM add_note operations are already consumed; never emits writable operations.")
    parser.add_argument("--output", default=None, help="optional local JSON output path; default is stdout")
    parser.add_argument("--json", action="store_true", help="pretty JSON output")
    args = parser.parse_args(argv)
    try:
        payload = validate_consumed_or_build_batch_write_plan(
            _load_json(DEFAULT_WRITE_AUTHORIZATION),
            _load_json(DEFAULT_V4_NO_WRITE_AUTHORIZATION),
            _load_json(DEFAULT_DRAFT_AUTHORIZATION),
            outcome_ledger=_load_json(DEFAULT_OUTCOME_LEDGER),
            write_auth_sha256=_file_sha256(DEFAULT_WRITE_AUTHORIZATION),
            v4_no_write_auth_sha256=_file_sha256(DEFAULT_V4_NO_WRITE_AUTHORIZATION),
            draft_auth_sha256=_file_sha256(DEFAULT_DRAFT_AUTHORIZATION),
        )
    except (OSError, json.JSONDecodeError):
        payload = _error("input_read_failed")
    emit(payload, args.output, args.json)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
