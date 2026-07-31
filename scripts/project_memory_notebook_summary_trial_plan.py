#!/usr/bin/env python3
"""Build a deterministic local-only NotebookLM summary trial plan.

This manual pre-migration gate consumes only the sanitized pre-migration
manifest plus a separate one-record authorization file. It never reads record
bodies, titles, local source storage, NotebookLM clients, network APIs or
subprocesses, and it never performs a NotebookLM write.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path("/tmp/opencode/nmbot_notebook_pre_migration_manifest_v2.json")

PLAN_SCHEMA = "project_memory_notebook_summary_trial_plan.v1"
AUTH_SCHEMA_V1 = "project_memory_notebook_summary_trial_authorization.v1"
AUTH_SCHEMA_V2 = "project_memory_notebook_summary_trial_authorization.v2"
AUTH_SCHEMA_V3 = "project_memory_notebook_summary_trial_authorization.v3"
ACTIVE_AUTH_SCHEMAS = {AUTH_SCHEMA_V2, AUTH_SCHEMA_V3}
MANIFEST_SCHEMA = "project_memory_pre_migration_manifest.v1"
AUTHORIZATION_TYPE = "manual_notebooklm_summary_only_pre_migration_trial"
PLAN_STATUS = "draft_for_human_summary_review"
SELECTED_DISPOSITION = "selected_for_summary_plan"
CANONICAL_DESTINATION_DECISIONS = {
    "canonical_notebook_from_registry_or_selected_tree",
    "canonical_notebook_from_registry_resolver",
}
OWNER = "ser"
ROLLBACK_OWNER = "ser"
DESTINATION_POLICY = "canonical_only"
ROLLBACK_SCOPE = "routing_only_no_data_deletion"

SAFE_FLAGS = {
    "read_only": True,
    "write_performed": False,
    "notebook_mutation_performed": False,
    "automatic_routing_changed": False,
    "production_verified": False,
    "requires_owner_confirmation": True,
}

OUTPUT_FLAGS = {
    **SAFE_FLAGS,
    "migration_performed": False,
    "data_deletion_authorized": False,
    "data_migration_authorized": False,
    "notebook_write_authorized": False,
    "notebook_write_performed": False,
}

BANNED_KEYS = {"title", "body", "content", "raw", "transcript", "log", "path", "secret", "secrets"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _base_payload() -> dict[str, Any]:
    return {
        "schema": PLAN_SCHEMA,
        "ok": False,
        "plan_status": PLAN_STATUS,
        "writes_unapproved": True,
        "execution_blocked": True,
        "rollback_scope": ROLLBACK_SCOPE,
        "destination_policy": DESTINATION_POLICY,
        "candidate_count": 0,
        **OUTPUT_FLAGS,
        "errors": [],
    }


def _error(code: str, message: str = "summary trial plan failed closed") -> dict[str, Any]:
    payload = _base_payload()
    payload["denied_reason"] = code
    payload["errors"] = [{"code": code, "message": message}]
    return payload


def _ensure_no_unsafe_keys(value: Any, marker: str = "$", *, allow_ref_notebook: bool = True) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in BANNED_KEYS:
                raise ValueError(f"unsafe_key:{marker}.{key}")
            _ensure_no_unsafe_keys(child, f"{marker}.{key}", allow_ref_notebook=allow_ref_notebook)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _ensure_no_unsafe_keys(child, f"{marker}[{index}]", allow_ref_notebook=allow_ref_notebook)


def _validate_flags(payload: dict[str, Any], prefix: str, flags: dict[str, bool]) -> None:
    for key, expected in flags.items():
        if payload.get(key) is not expected:
            raise ValueError(f"{prefix}_flag_mismatch:{key}")


def _record_ref(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("record_ref_not_object")
    ref = {key: value.get(key) for key in ("notebook", "kind", "id")}
    if not all(isinstance(ref[key], str) and ref[key] for key in ref):
        raise ValueError("record_ref_invalid")
    return {"notebook": str(ref["notebook"]), "kind": str(ref["kind"]), "id": str(ref["id"])}


def _same_ref(left: dict[str, str], right: dict[str, str]) -> bool:
    return all(left[key] == right[key] for key in ("notebook", "kind", "id"))


def _validate_authorization(auth: dict[str, Any]) -> tuple[dict[str, str], str | None, str | None, str]:
    if set(auth) != {
        "schema",
        "authorization_type",
        "owner",
        "rollback_owner",
        "read_only",
        "write_performed",
        "notebook_mutation_performed",
        "automatic_routing_changed",
        "production_verified",
        "migration_performed",
        "execution_blocked",
        "requires_owner_confirmation",
        "scope",
        "destination_policy",
        "rollback_scope",
        "data_deletion_authorized",
        "data_migration_authorized",
        "notebook_write_authorized",
        "notebook_write_requires",
        "summary_approval",
        "does_not_authorize_notebook_write",
    }:
        raise ValueError("authorization_keys_invalid")
    auth_schema = auth.get("schema")
    if auth_schema == AUTH_SCHEMA_V1:
        raise ValueError("authorization_schema_obsolete")
    if auth_schema not in ACTIVE_AUTH_SCHEMAS:
        raise ValueError("authorization_schema_invalid")
    if auth.get("authorization_type") != AUTHORIZATION_TYPE:
        raise ValueError("authorization_type_invalid")
    if auth.get("owner") != OWNER or auth.get("rollback_owner") != ROLLBACK_OWNER:
        raise ValueError("authorization_owner_mismatch")
    _validate_flags(auth, "authorization", {**SAFE_FLAGS, "migration_performed": False})
    if auth.get("execution_blocked") is not True:
        raise ValueError("authorization_execution_blocked_mismatch")
    if auth.get("destination_policy") != DESTINATION_POLICY:
        raise ValueError("authorization_destination_policy_mismatch")
    if auth.get("rollback_scope") != ROLLBACK_SCOPE:
        raise ValueError("authorization_rollback_scope_mismatch")
    for key in ("data_deletion_authorized", "data_migration_authorized", "notebook_write_authorized"):
        if auth.get(key) is not False:
            raise ValueError(f"authorization_forbidden:{key}")
    if auth.get("does_not_authorize_notebook_write") is not True:
        raise ValueError("authorization_write_boundary_missing")
    if auth.get("notebook_write_requires") != "explicit_per_record_summary_approval":
        raise ValueError("authorization_write_requirement_mismatch")
    approval = auth.get("summary_approval")
    if not isinstance(approval, dict) or set(approval) != {"approved", "approved_record_ref"}:
        raise ValueError("authorization_summary_approval_invalid")
    if approval.get("approved") is not False or approval.get("approved_record_ref") is not None:
        raise ValueError("authorization_summary_approval_must_be_unapproved")
    scope = auth.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("authorization_scope_invalid")
    if scope.get("maximum_selected_records") != 1:
        raise ValueError("authorization_scope_must_be_exactly_one")
    if scope.get("allowed_disposition") != SELECTED_DISPOSITION:
        raise ValueError("authorization_scope_disposition_invalid")
    allowed_scope_keys = {"maximum_selected_records", "allowed_disposition", "selected_record_ref", "exact_metadata_sha256"}
    if auth_schema == AUTH_SCHEMA_V3:
        allowed_scope_keys.add("exact_target_canonical_notebook")
    if set(scope) != allowed_scope_keys:
        raise ValueError("authorization_scope_invalid")
    exact_sha = scope.get("exact_metadata_sha256")
    if not isinstance(exact_sha, str) or not SHA256_RE.match(exact_sha):
        raise ValueError("authorization_metadata_sha_invalid")
    exact_target = None
    if auth_schema == AUTH_SCHEMA_V3:
        exact_target = scope.get("exact_target_canonical_notebook")
        if not isinstance(exact_target, str) or not exact_target:
            raise ValueError("authorization_target_invalid")
    return _record_ref(scope.get("selected_record_ref")), exact_sha, exact_target, auth_schema


def _validate_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("manifest_schema_invalid")
    if manifest.get("ok") is not True:
        raise ValueError("manifest_not_ok")
    _validate_flags(manifest, "manifest", {**SAFE_FLAGS, "migration_performed": False})
    if manifest.get("execution_blocked") is not True:
        raise ValueError("manifest_execution_blocked_mismatch")
    gate = manifest.get("execution_gate")
    if not isinstance(gate, dict):
        raise ValueError("manifest_execution_gate_missing")
    if gate.get("human_owner") != "TBD" or gate.get("rollback_owner") != "TBD":
        raise ValueError("manifest_generic_owner_must_remain_tbd")
    if gate.get("data_migration_authorized") is not False or gate.get("notebook_write_authorized") is not False:
        raise ValueError("manifest_write_gate_mismatch")
    records = manifest.get("records")
    if not isinstance(records, list) or manifest.get("record_count") != len(records):
        raise ValueError("manifest_record_count_mismatch")
    selected = [row for row in records if isinstance(row, dict) and row.get("disposition") == SELECTED_DISPOSITION]
    if manifest.get("selected_count") != len(selected):
        raise ValueError("manifest_selected_count_mismatch")
    if manifest.get("disposition_counts", {}).get(SELECTED_DISPOSITION) != len(selected):
        raise ValueError("manifest_selected_disposition_count_mismatch")
    return selected


def build_trial_plan(manifest: dict[str, Any], authorization: dict[str, Any], *, manifest_sha256: str, authorization_sha256: str) -> dict[str, Any]:
    try:
        _ensure_no_unsafe_keys(authorization)
        _ensure_no_unsafe_keys(manifest)
        target_ref, authorized_metadata_sha, authorized_target, auth_schema = _validate_authorization(authorization)
        selected = _validate_manifest(manifest)
        matches = []
        for row in selected:
            ref = _record_ref(row.get("record_ref"))
            if _same_ref(ref, target_ref):
                matches.append(row)
        if len(matches) != 1:
            raise ValueError("eligible_record_count_not_one")
        row = matches[0]
        target = row.get("target_canonical_notebook")
        if not isinstance(target, str) or not target:
            raise ValueError("eligible_record_missing_canonical_target")
        if authorized_target is not None and target != authorized_target:
            raise ValueError("eligible_record_target_not_authorized")
        if row.get("target_ownership_decision") not in CANONICAL_DESTINATION_DECISIONS:
            raise ValueError("eligible_record_not_canonical_destination")
        metadata_sha = row.get("metadata_sha256")
        if not isinstance(metadata_sha, str) or not metadata_sha:
            raise ValueError("eligible_record_metadata_sha_missing")
        if authorized_metadata_sha is not None and metadata_sha != authorized_metadata_sha:
            raise ValueError("eligible_record_metadata_sha_not_authorized")
        evidence_refs = row.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not all(isinstance(item, str) and item for item in evidence_refs):
            raise ValueError("eligible_record_evidence_refs_invalid")
        provenance = row.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError("eligible_record_provenance_invalid")
    except ValueError as exc:
        return _error(str(exc))

    payload = _base_payload()
    payload.update(
        {
            "ok": True,
            "candidate_count": 1,
            "authorization": {
                "schema": auth_schema,
                "authorization_sha256": authorization_sha256,
                "owner": OWNER,
                "rollback_owner": ROLLBACK_OWNER,
                "maximum_selected_records": 1,
                "notebook_write_authorized": False,
                "does_not_authorize_notebook_write": True,
            },
            "input_manifest": {
                "schema": MANIFEST_SCHEMA,
                "manifest_sha256": manifest_sha256,
                "selected_count": len(selected),
            },
            "trial_plan": {
                "plan_status": PLAN_STATUS,
                "record_ref": target_ref,
                "metadata_sha256": metadata_sha,
                "target_canonical_notebook": target,
                "destination_policy": DESTINATION_POLICY,
                "rollback_scope": ROLLBACK_SCOPE,
                "evidence_refs": list(evidence_refs),
                "provenance": {
                    **provenance,
                    "source_manifest_sha256": manifest_sha256,
                    "authorization_sha256": authorization_sha256,
                },
                "human_summary_review_required": True,
                "notebook_write_authorized": False,
            },
            "errors": [],
        }
    )
    try:
        _ensure_no_unsafe_keys(payload)
    except ValueError as exc:
        return _error(str(exc), "summary trial plan output blocked unsafe field")
    return payload


def emit(payload: dict[str, Any], output: str | None, pretty: bool) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a local no-write NotebookLM summary trial plan for exactly one authorized record.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="sanitized pre-migration manifest JSON path")
    parser.add_argument("--authorization", required=True, help="one-record summary trial authorization JSON path")
    parser.add_argument("--output", default=None, help="optional local JSON output path; default is stdout")
    parser.add_argument("--json", action="store_true", help="pretty JSON output")
    args = parser.parse_args(argv)

    try:
        payload = build_trial_plan(
            _read_json(args.manifest),
            _read_json(args.authorization),
            manifest_sha256=_file_sha256(args.manifest),
            authorization_sha256=_file_sha256(args.authorization),
        )
    except (OSError, json.JSONDecodeError):
        payload = _error("input_read_failed", "summary trial plan input could not be read")
    emit(payload, args.output, args.json)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
