#!/usr/bin/env python3
"""Fail-closed local-only sanitizer for authorized NotebookLM summary batch candidates.

This command validates a sanitized pre-migration manifest and an exact no-write
batch authorization before opening local storage. It then reads authorized
records one-by-one in manifest order, verifies each body SHA-256, applies the
same safety assessment rules as the one-record trial sanitizer, and stops at the
    first blocked/hash/error result for v1/v2. For v3/v4, expected policy blocks
    are recorded and the batch continues, while integrity/tool failures still
    stop immediately. It never writes, migrates, routes, deletes, or
prints titles, bodies, snippets, paths, matched terms, secrets, raw logs, or
transcripts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from project_memory_notebook_summary_trial_sanitize import (
    _assess_body,
    _body_for_record,
    _record_id,
    _resolve_inside,
    _safe_segment,
    _sha_body,
)


SCHEMA = "project_memory_notebook_summary_batch_sanitize.v1"
AUTH_SCHEMA = "project_memory_notebook_summary_batch_sanitize_authorization.v1"
AUTH_SCHEMA_V2 = "project_memory_notebook_summary_batch_sanitize_authorization.v2"
AUTH_SCHEMA_V3 = "project_memory_notebook_summary_batch_sanitize_authorization.v3"
AUTH_SCHEMA_V4 = "project_memory_notebook_summary_batch_sanitize_authorization.v4"
AUTHORIZATION_TYPE = "manual_notebooklm_summary_only_pre_migration_batch_sanitize"
SELECTED_DISPOSITION = "selected_for_summary_plan"
DESTINATION_POLICY = "canonical_only"
OWNER = "ser"
EXPECTED_COUNT = 35
EXCLUDED_IDS = {"0f2c83dd6879", "20a382a56022", "2a7edfa48439"}
V2_EXPECTED_COUNT = 33
V2_EXCLUDED_IDS = EXCLUDED_IDS | {"41b55e418687", "41c9ca464ecd"}
V3_EXPECTED_COUNT = 32
V3_EXCLUDED_IDS = V2_EXCLUDED_IDS | {"41f4c8962e73"}
V4_EXPECTED_COUNT = 42
V4_EXCLUDED_IDS: set[str] = set()
V4_MANIFEST_SHA256 = "0ee513d98213b3418c29cc269fec9c959a686a50172b66ca405c11442ad4f2ae"
V4_SELECTED_RECORDS_SHA256 = "ff56fdc0211b0bf018bd4d3fb6f0aa70cd3918d29b1622aa0d114ecfe3567615"
V4_MANIFEST_RECORD_COUNT = 602
V4_MANIFEST_HELD_UNRESOLVED_COUNT = 556
V4_MANIFEST_SELECTED_COUNT = 42
SAFE_DECISION = "safe_for_human_summary_draft"
BLOCKED_DECISION = "blocked_sensitive_or_uncertain"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

TRUE_FLAGS = {"read_only": True, "requires_owner_confirmation": True, "execution_blocked": True}
FALSE_FLAGS = {
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
}


def _authorization_policy(schema: Any) -> dict[str, Any]:
    if schema == AUTH_SCHEMA:
        return {"schema": AUTH_SCHEMA, "expected_count": EXPECTED_COUNT, "excluded_ids": set(EXCLUDED_IDS), "continue_on_policy_block": False, "manifest_record_count": 364, "manifest_held_unresolved_count": 323, "manifest_selected_count": 38, "aggregate_only": False}
    if schema == AUTH_SCHEMA_V2:
        return {"schema": AUTH_SCHEMA_V2, "expected_count": V2_EXPECTED_COUNT, "excluded_ids": set(V2_EXCLUDED_IDS), "continue_on_policy_block": False, "manifest_record_count": 364, "manifest_held_unresolved_count": 323, "manifest_selected_count": 38, "aggregate_only": False}
    if schema == AUTH_SCHEMA_V3:
        return {"schema": AUTH_SCHEMA_V3, "expected_count": V3_EXPECTED_COUNT, "excluded_ids": set(V3_EXCLUDED_IDS), "continue_on_policy_block": True, "manifest_record_count": 364, "manifest_held_unresolved_count": 323, "manifest_selected_count": 38, "aggregate_only": False}
    if schema == AUTH_SCHEMA_V4:
        return {"schema": AUTH_SCHEMA_V4, "expected_count": V4_EXPECTED_COUNT, "excluded_ids": set(V4_EXCLUDED_IDS), "continue_on_policy_block": True, "manifest_sha256": V4_MANIFEST_SHA256, "selected_records_sha256": V4_SELECTED_RECORDS_SHA256, "manifest_record_count": V4_MANIFEST_RECORD_COUNT, "manifest_held_unresolved_count": V4_MANIFEST_HELD_UNRESOLVED_COUNT, "manifest_selected_count": V4_MANIFEST_SELECTED_COUNT, "aggregate_only": True}
    raise ValueError("authorization_schema_invalid")


def _base_payload(*, authorized_candidate_count: int = EXPECTED_COUNT) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "ok": False,
        "decision": "blocked_sensitive_or_uncertain",
        "candidate_count": 0,
        "authorized_candidate_count": authorized_candidate_count,
        "processed_count": 0,
        "safe_count": 0,
        "blocked_count": 0,
        "integrity_failure_count": 0,
        "stopped_early": False,
        "stop_reason": None,
        "processed_records": [],
        "manifest_sha256_verified": False,
        "authorization_exact_list_verified": False,
        "metadata_queue_prepared": False,
        "notebook_write_authorized": False,
        "notebook_write_remains_unauthorized": True,
        "summary_drafts_generated": False,
        "human_summary_review_only": True,
        "destination_policy": DESTINATION_POLICY,
        **TRUE_FLAGS,
        **FALSE_FLAGS,
        "errors": [],
    }


def _blocked(
    code: str,
    *,
    processed: list[dict[str, Any]] | None = None,
    candidate_count: int = 0,
    queue_verified: bool = False,
    authorized_candidate_count: int = EXPECTED_COUNT,
    aggregate_only: bool = False,
) -> dict[str, Any]:
    processed_items = processed or []
    payload = _base_payload(authorized_candidate_count=authorized_candidate_count)
    payload.update(
        {
            "candidate_count": candidate_count,
            "processed_count": len(processed_items),
            "safe_count": sum(1 for item in processed_items if item.get("decision") == SAFE_DECISION),
            "blocked_count": sum(1 for item in processed_items if item.get("decision") == BLOCKED_DECISION),
            "integrity_failure_count": 1 if code == "record_missing_malformed_or_hash_mismatch" else 0,
            "stopped_early": True,
            "stop_reason": code,
            "processed_records": [] if aggregate_only else processed_items,
            "manifest_sha256_verified": queue_verified,
            "authorization_exact_list_verified": queue_verified,
            "metadata_queue_prepared": queue_verified,
            "errors": [{"code": code, "message": "batch sanitizer failed closed"}],
        }
    )
    return payload


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_ref(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("record_ref_invalid")
    ref = {key: value.get(key) for key in ("notebook", "kind", "id")}
    if not all(isinstance(ref[key], str) and ref[key] for key in ref):
        raise ValueError("record_ref_invalid")
    if ref["kind"] not in {"note", "source"}:
        raise ValueError("record_kind_invalid")
    return {"notebook": str(ref["notebook"]), "kind": str(ref["kind"]), "id": str(ref["id"])}


def _candidate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("candidate_invalid")
    ref = _record_ref(value.get("record_ref"))
    metadata_sha256 = value.get("metadata_sha256")
    target = value.get("target_canonical_notebook")
    if not isinstance(metadata_sha256, str) or not SHA256_RE.match(metadata_sha256):
        raise ValueError("candidate_metadata_sha_invalid")
    if not isinstance(target, str) or not target:
        raise ValueError("candidate_target_invalid")
    return {"record_ref": ref, "metadata_sha256": metadata_sha256, "target_canonical_notebook": target}


def _validate_authorization(authorization: dict[str, Any]) -> list[dict[str, Any]]:
    policy = _authorization_policy(authorization.get("schema"))
    expected_count = int(policy["expected_count"])
    excluded_ids = set(policy["excluded_ids"])
    if authorization.get("authorization_type") != AUTHORIZATION_TYPE:
        raise ValueError("authorization_schema_invalid")
    if authorization.get("owner") != OWNER or authorization.get("rollback_owner") != OWNER:
        raise ValueError("authorization_owner_mismatch")
    if authorization.get("destination_policy") != DESTINATION_POLICY:
        raise ValueError("authorization_destination_invalid")
    for key, expected in {**TRUE_FLAGS, **FALSE_FLAGS}.items():
        if authorization.get(key) is not expected:
            raise ValueError(f"authorization_flag_mismatch:{key}")
    for key in (
        "does_not_authorize_notebook_write",
        "does_not_authorize_migration",
        "does_not_authorize_routing",
        "does_not_authorize_deletion",
    ):
        if authorization.get(key) is not True:
            raise ValueError(f"authorization_boundary_mismatch:{key}")
    if authorization.get("allowed_safe_decision") != SAFE_DECISION:
        raise ValueError("authorization_decision_invalid")
    if authorization.get("summary_draft_generation_authorized") is not False:
        raise ValueError("authorization_summary_generation_invalid")
    continue_on_policy_block = authorization.get("continue_on_policy_block")
    stop_on_integrity_failure = authorization.get("stop_on_integrity_failure")
    if policy.get("continue_on_policy_block") is True:
        if continue_on_policy_block is not True or stop_on_integrity_failure is not True:
            raise ValueError("authorization_v3_control_flags_invalid")
    elif continue_on_policy_block is not None or stop_on_integrity_failure is not None:
        raise ValueError("authorization_control_flags_invalid")
    approval = authorization.get("summary_approval")
    if not isinstance(approval, dict) or approval.get("approved") is not False or approval.get("approved_record_refs") != []:
        raise ValueError("authorization_summary_approval_invalid")
    scope = authorization.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("authorization_scope_invalid")
    if scope.get("candidate_count") != expected_count or scope.get("allowed_disposition") != SELECTED_DISPOSITION:
        raise ValueError("authorization_scope_invalid")
    if set(scope.get("excluded_record_ids", [])) != excluded_ids:
        raise ValueError("authorization_exclusions_invalid")
    records = scope.get("selected_records")
    if not isinstance(records, list) or len(records) != expected_count:
        raise ValueError("authorization_count_invalid")
    candidates = [_candidate(item) for item in records]
    if len({tuple(item["record_ref"].values()) for item in candidates}) != expected_count:
        raise ValueError("authorization_duplicate_ref")
    if any(item["record_ref"]["id"] in excluded_ids for item in candidates):
        raise ValueError("authorization_excluded_id_present")
    manifest_auth = authorization.get("manifest")
    if not isinstance(manifest_auth, dict) or not isinstance(manifest_auth.get("sha256"), str):
        raise ValueError("authorization_manifest_binding_invalid")
    if policy.get("manifest_sha256") and manifest_auth.get("sha256") != policy["manifest_sha256"]:
        raise ValueError("authorization_manifest_binding_invalid")
    if policy.get("selected_records_sha256"):
        selected_records_sha256 = scope.get("selected_records_sha256")
        if selected_records_sha256 != policy["selected_records_sha256"]:
            raise ValueError("authorization_selected_records_sha_invalid")
        computed = hashlib.sha256(json.dumps(candidates, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if computed != selected_records_sha256:
            raise ValueError("authorization_selected_records_sha_mismatch")
    return candidates


def _validate_manifest(manifest: dict[str, Any], authorization: dict[str, Any], *, manifest_sha256: str) -> list[dict[str, Any]]:
    authorized = _validate_authorization(authorization)
    policy = _authorization_policy(authorization.get("schema"))
    excluded_ids = set(policy["excluded_ids"])
    manifest_auth = authorization["manifest"]
    if manifest_auth.get("sha256") != manifest_sha256:
        raise ValueError("manifest_sha256_binding_mismatch")
    expectations = manifest_auth.get("schema_expectations")
    if not isinstance(expectations, dict):
        raise ValueError("manifest_expectations_invalid")
    for key, expected in expectations.items():
        if manifest.get(key) is not expected:
            raise ValueError(f"manifest_flag_mismatch:{key}")
    if manifest.get("disposition_counts", {}).get("selected_for_summary_plan") != policy["manifest_selected_count"]:
        raise ValueError("manifest_selected_count_invalid")
    if manifest.get("record_count") != policy["manifest_record_count"] or manifest.get("held_unresolved_count") != policy["manifest_held_unresolved_count"]:
        raise ValueError("manifest_count_invalid")
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValueError("manifest_records_invalid")
    selected: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or record.get("disposition") != SELECTED_DISPOSITION:
            continue
        ref = _record_ref(record.get("record_ref"))
        provenance = record.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("record_metadata_sha256_verified") is not True:
            raise ValueError("manifest_provenance_invalid")
        selected.append(
            {
                "record_ref": ref,
                "metadata_sha256": record.get("metadata_sha256"),
                "target_canonical_notebook": record.get("target_canonical_notebook"),
            }
        )
    remaining = [_candidate(item) for item in selected if item["record_ref"]["id"] not in excluded_ids]
    if remaining != authorized:
        raise ValueError("authorization_exact_list_mismatch")
    return remaining


def _record_path(storage_root: str | Path, workspace: str, ref: dict[str, str]) -> Path:
    root = Path(storage_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("storage_root_unavailable")
    safe_workspace = _safe_segment(workspace, "workspace")
    safe_notebook = _safe_segment(ref["notebook"], "notebook")
    safe_id = _safe_segment(ref["id"], "record_id")
    dirname = "notes" if ref["kind"] == "note" else "sources"
    return _resolve_inside(root, root / "workspaces" / safe_workspace / "notebooks" / safe_notebook / dirname / f"{safe_id}.json")


def _read_exact_record_body(storage_root: str | Path, workspace: str, ref: dict[str, str]) -> str:
    path = _record_path(storage_root, workspace, ref)
    if not path.exists() or not path.is_file():
        raise ValueError("record_missing")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or _record_id(ref["kind"], data) != ref["id"]:
        raise ValueError("record_ref_mismatch")
    return _body_for_record(ref["kind"], data)


def sanitize_batch(manifest: dict[str, Any], authorization: dict[str, Any], *, manifest_sha256: str, storage_root: str | Path, workspace: str = "default") -> dict[str, Any]:
    processed: list[dict[str, Any]] = []
    policy: dict[str, Any] = {"expected_count": EXPECTED_COUNT, "aggregate_only": False, "continue_on_policy_block": False}
    try:
        policy = _authorization_policy(authorization.get("schema"))
        authorized_candidate_count = int(policy["expected_count"])
    except (ValueError, TypeError, AttributeError):
        authorized_candidate_count = EXPECTED_COUNT
    try:
        queue = _validate_manifest(manifest, authorization, manifest_sha256=manifest_sha256)
    except (ValueError, TypeError, AttributeError):
        return _blocked("manifest_or_authorization_invalid", authorized_candidate_count=authorized_candidate_count, aggregate_only=bool(policy.get("aggregate_only", False)))
    continue_on_policy_block = bool(policy.get("continue_on_policy_block"))
    aggregate_only = bool(policy.get("aggregate_only", False))

    for item in queue:
        ref = item["record_ref"]
        safe_item = {
            "record_ref": dict(ref),
            "target_canonical_notebook": item["target_canonical_notebook"],
            "decision": "blocked_sensitive_or_uncertain",
            "record_metadata_sha256_verified": False,
            "contains_sensitive_indicator": False,
            "contains_raw_log_indicator": False,
            "contains_transcript_indicator": False,
            "contains_customer_data_indicator": False,
            "uncertain": True,
        }
        try:
            body = _read_exact_record_body(storage_root, workspace, ref)
            if _sha_body(body) != item["metadata_sha256"]:
                raise ValueError("metadata_sha256_mismatch")
            assessment = _assess_body(body)
            safe_item.update(
                {
                    "record_metadata_sha256_verified": True,
                    "contains_sensitive_indicator": assessment["contains_sensitive_indicator"],
                    "contains_raw_log_indicator": assessment["contains_raw_log_indicator"],
                    "contains_transcript_indicator": assessment["contains_transcript_indicator"],
                    "contains_customer_data_indicator": assessment["contains_customer_data_indicator"],
                    "uncertain": assessment["uncertain"],
                }
            )
            if assessment["indicator_count"] or assessment["uncertain"]:
                processed.append(safe_item)
                if continue_on_policy_block:
                    continue
                return _blocked("sensitive_or_uncertain_content", processed=processed, candidate_count=len(queue), queue_verified=True, authorized_candidate_count=authorized_candidate_count, aggregate_only=aggregate_only)
            safe_item["decision"] = SAFE_DECISION
            processed.append(safe_item)
        except (OSError, json.JSONDecodeError, ValueError):
            processed.append(safe_item)
            return _blocked("record_missing_malformed_or_hash_mismatch", processed=processed, candidate_count=len(queue), queue_verified=True, authorized_candidate_count=authorized_candidate_count, aggregate_only=aggregate_only)

    payload = _base_payload(authorized_candidate_count=authorized_candidate_count)
    payload.update(
        {
            "ok": True,
            "decision": "sanitization_complete" if continue_on_policy_block else SAFE_DECISION,
            "candidate_count": len(queue),
            "processed_count": len(processed),
            "safe_count": sum(1 for item in processed if item.get("decision") == SAFE_DECISION),
            "blocked_count": sum(1 for item in processed if item.get("decision") == BLOCKED_DECISION),
            "integrity_failure_count": 0,
            "stopped_early": False,
            "processed_records": [] if aggregate_only else processed,
            "manifest_sha256_verified": True,
            "authorization_exact_list_verified": True,
            "metadata_queue_prepared": True,
            "errors": [],
        }
    )
    return payload


def emit(payload: dict[str, Any], pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sanitize an exact no-write NotebookLM summary candidate batch from local storage.")
    parser.add_argument("--manifest", required=True, help="sanitized pre-migration manifest JSON")
    parser.add_argument("--authorization", required=True, help="exact no-write batch authorization JSON")
    parser.add_argument("--storage-root", required=True, help="local storage root; never printed")
    parser.add_argument("--workspace", default="default", help="local workspace name")
    parser.add_argument("--json", action="store_true", help="pretty JSON output")
    args = parser.parse_args(argv)
    try:
        with Path(args.manifest).open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        with Path(args.authorization).open("r", encoding="utf-8") as handle:
            authorization = json.load(handle)
        if not isinstance(manifest, dict) or not isinstance(authorization, dict):
            raise ValueError("input_json_not_object")
        payload = sanitize_batch(
            manifest,
            authorization,
            manifest_sha256=_file_sha256(args.manifest),
            storage_root=args.storage_root,
            workspace=args.workspace,
        )
    except (OSError, json.JSONDecodeError, ValueError):
        payload = _blocked("input_read_failed")
    emit(payload, args.json)
    return 0 if payload.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
