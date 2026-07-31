#!/usr/bin/env python3
"""Fail-closed local sanitizer for the one authorized NotebookLM summary trial.

The command reads a no-write trial plan plus one local storage root, opens only
the already authorized record internally, verifies its body SHA-256, and emits a
sanitized allow/block decision for future human summary drafting. It never writes
NotebookLM/source-storage/routing artifacts and never prints record content,
titles, storage paths, snippets, matched terms, secrets, transcripts or logs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA = "project_memory_notebook_summary_trial_sanitize.v1"
PLAN_SCHEMA = "project_memory_notebook_summary_trial_plan.v1"
AUTH_SCHEMA_V1 = "project_memory_notebook_summary_trial_authorization.v1"
AUTH_SCHEMA_V2 = "project_memory_notebook_summary_trial_authorization.v2"
AUTH_SCHEMA_V3 = "project_memory_notebook_summary_trial_authorization.v3"
PLAN_STATUS = "draft_for_human_summary_review"
DESTINATION_POLICY = "canonical_only"
ROLLBACK_SCOPE = "routing_only_no_data_deletion"
AUTHORIZED_REF = {"notebook": "cc-daemons", "kind": "note", "id": "0f2c83dd6879"}
EXPECTED_PLAN_SHA256 = "8d88b29be3a37ddb8b05738b3cbed4385f70fcd98bbf8de9bc8fa2c3e7875c2a"
AUTHORIZATION_TYPE = "manual_notebooklm_summary_only_pre_migration_trial"
OWNER = "ser"
SELECTED_DISPOSITION = "selected_for_summary_plan"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

FALSE_FLAGS = {
    "write_performed": False,
    "notebook_mutation_performed": False,
    "automatic_routing_changed": False,
    "migration_performed": False,
    "data_deletion_authorized": False,
    "data_migration_authorized": False,
    "notebook_write_authorized": False,
    "notebook_write_performed": False,
    "production_verified": False,
}
TRUE_FLAGS = {
    "read_only": True,
    "requires_owner_confirmation": True,
    "execution_blocked": True,
}

SENSITIVE_RE = re.compile(
    r"(?i)(api[_-]?key|authorization\s*[:=]|bearer\s+[a-z0-9._~+/=-]{8,}|password\s*[:=]|token\s*[:=]|secret\s*[:=]|private[_-]?key)"
)
RAW_LOG_RE = re.compile(r"(?im)(^\s*(traceback|exception|error|warn|debug|info)\b|\b(stdout|stderr|stack trace)\b)")
TRANSCRIPT_RE = re.compile(r"(?im)(^\s*(operator|agent|client|customer|user|assistant|bot)\s*[:：]|\btranscript\b|\bcall recording\b)")
CUSTOMER_DATA_RE = re.compile(r"(?i)(\+?\d[\d\s().-]{8,}\d|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}|\b(?:client|customer|phone|email)\s*[:=])")


def _base_payload(selected_record_ref: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "ok": False,
        "decision": "blocked_sensitive_or_uncertain",
        "candidate_count": 0,
        "selected_record_ref": dict(selected_record_ref or AUTHORIZED_REF),
        "metadata_sha256": None,
        "record_metadata_sha256_verified": False,
        "assessment": {
            "contains_sensitive_indicator": False,
            "contains_raw_log_indicator": False,
            "contains_transcript_indicator": False,
            "contains_customer_data_indicator": False,
            "body_bytes": 0,
            "indicator_count": 0,
            "uncertain": True,
        },
        "plan_status": PLAN_STATUS,
        "destination_policy": DESTINATION_POLICY,
        "rollback_scope": ROLLBACK_SCOPE,
        "writes_unapproved": True,
        "notebook_write_remains_unauthorized": True,
        "human_summary_review_only": True,
        **TRUE_FLAGS,
        **FALSE_FLAGS,
        "errors": [],
    }


def _blocked(code: str, selected_record_ref: dict[str, str] | None = None) -> dict[str, Any]:
    payload = _base_payload(selected_record_ref)
    payload["denied_reason"] = code
    payload["errors"] = [{"code": code, "message": "sanitizer failed closed"}]
    return payload


def _safe_segment(value: str, label: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"invalid_{label}")
    return value


def _resolve_inside(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("storage_boundary_violation") from exc
    return resolved


def _body_for_record(kind: str, data: dict[str, Any]) -> str:
    value = data.get("note") if kind == "note" else data.get("content")
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _record_id(kind: str, data: dict[str, Any]) -> str:
    value = data.get("note_id") if kind == "note" else data.get("source_id")
    return "" if value is None else str(value)


def _sha_body(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_ref(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("authorization_record_ref_invalid")
    ref = {key: value.get(key) for key in ("notebook", "kind", "id")}
    if not all(isinstance(ref[key], str) and ref[key] for key in ref):
        raise ValueError("authorization_record_ref_invalid")
    return {"notebook": str(ref["notebook"]), "kind": str(ref["kind"]), "id": str(ref["id"])}


def _validate_flag(payload: dict[str, Any], key: str, expected: bool) -> None:
    if payload.get(key) is not expected:
        raise ValueError(f"plan_flag_mismatch:{key}")


def _validate_authorization(authorization: dict[str, Any]) -> tuple[dict[str, str], str, str | None, str]:
    if authorization.get("schema") not in {AUTH_SCHEMA_V1, AUTH_SCHEMA_V2, AUTH_SCHEMA_V3}:
        raise ValueError("authorization_schema_invalid")
    if authorization.get("authorization_type") != AUTHORIZATION_TYPE:
        raise ValueError("authorization_type_invalid")
    if authorization.get("owner") != OWNER or authorization.get("rollback_owner") != OWNER:
        raise ValueError("authorization_owner_mismatch")
    for key, expected in {**TRUE_FLAGS, **FALSE_FLAGS}.items():
        if key in {"notebook_write_performed"}:
            continue
        if authorization.get(key) is not expected:
            raise ValueError(f"authorization_flag_mismatch:{key}")
    if authorization.get("destination_policy") != DESTINATION_POLICY or authorization.get("rollback_scope") != ROLLBACK_SCOPE:
        raise ValueError("authorization_boundary_invalid")
    if authorization.get("notebook_write_requires") != "explicit_per_record_summary_approval":
        raise ValueError("authorization_write_requirement_invalid")
    if authorization.get("does_not_authorize_notebook_write") is not True:
        raise ValueError("authorization_write_boundary_invalid")
    approval = authorization.get("summary_approval")
    if not isinstance(approval, dict) or approval.get("approved") is not False or approval.get("approved_record_ref") is not None:
        raise ValueError("authorization_summary_approval_invalid")
    scope = authorization.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("authorization_scope_invalid")
    if scope.get("maximum_selected_records") != 1 or scope.get("allowed_disposition") != SELECTED_DISPOSITION:
        raise ValueError("authorization_scope_invalid")
    if authorization.get("schema") == AUTH_SCHEMA_V1:
        refs = scope.get("selected_record_refs")
        if not isinstance(refs, list) or len(refs) != 1:
            raise ValueError("authorization_scope_invalid")
        ref = _record_ref(refs[0])
        if ref != AUTHORIZED_REF:
            raise ValueError("authorization_v1_candidate_not_preserved")
        return ref, EXPECTED_PLAN_SHA256, None, AUTH_SCHEMA_V1
    ref = _record_ref(scope.get("selected_record_ref"))
    metadata_sha256 = scope.get("exact_metadata_sha256")
    if not isinstance(metadata_sha256, str) or not SHA256_RE.match(metadata_sha256):
        raise ValueError("authorization_metadata_sha_invalid")
    exact_target = None
    if authorization.get("schema") == AUTH_SCHEMA_V3:
        exact_target = scope.get("exact_target_canonical_notebook")
        if not isinstance(exact_target, str) or not exact_target:
            raise ValueError("authorization_target_invalid")
    return ref, metadata_sha256, exact_target, authorization["schema"]


def _validate_plan(plan: dict[str, Any], authorization: dict[str, Any], *, authorization_sha256: str) -> tuple[dict[str, str], str]:
    authorized_ref, authorized_metadata_sha256, authorized_target, auth_schema = _validate_authorization(authorization)
    if plan.get("schema") != PLAN_SCHEMA or plan.get("ok") is not True:
        raise ValueError("plan_not_authorized_no_write_candidate")
    if plan.get("candidate_count") != 1:
        raise ValueError("candidate_count_not_one")
    if plan.get("plan_status") != PLAN_STATUS:
        raise ValueError("plan_status_invalid")
    if plan.get("destination_policy") != DESTINATION_POLICY or plan.get("rollback_scope") != ROLLBACK_SCOPE:
        raise ValueError("plan_boundary_invalid")
    for key, expected in {**TRUE_FLAGS, **FALSE_FLAGS}.items():
        _validate_flag(plan, key, expected)
    if plan.get("writes_unapproved") is not True:
        raise ValueError("plan_writes_must_remain_unapproved")
    auth = plan.get("authorization")
    if not isinstance(auth, dict) or auth.get("notebook_write_authorized") is not False or auth.get("does_not_authorize_notebook_write") is not True:
        raise ValueError("authorization_write_boundary_invalid")
    if auth.get("schema") != auth_schema:
        raise ValueError("authorization_schema_binding_mismatch")
    if auth.get("authorization_sha256") != authorization_sha256:
        raise ValueError("authorization_sha256_binding_mismatch")
    trial = plan.get("trial_plan")
    if not isinstance(trial, dict):
        raise ValueError("trial_plan_missing")
    if trial.get("plan_status") != PLAN_STATUS or trial.get("destination_policy") != DESTINATION_POLICY or trial.get("rollback_scope") != ROLLBACK_SCOPE:
        raise ValueError("trial_plan_boundary_invalid")
    if trial.get("notebook_write_authorized") is not False or trial.get("human_summary_review_required") is not True:
        raise ValueError("trial_plan_write_boundary_invalid")
    target = trial.get("target_canonical_notebook")
    if not isinstance(target, str) or not target:
        raise ValueError("trial_plan_target_invalid")
    if authorized_target is not None and target != authorized_target:
        raise ValueError("trial_plan_target_not_authorized")
    ref = trial.get("record_ref")
    if ref != authorized_ref:
        raise ValueError("unsupported_candidate")
    metadata_sha256 = trial.get("metadata_sha256")
    if not isinstance(metadata_sha256, str) or not SHA256_RE.match(metadata_sha256):
        raise ValueError("metadata_sha256_invalid")
    if metadata_sha256 != authorized_metadata_sha256:
        raise ValueError("metadata_sha256_not_authorized")
    return dict(authorized_ref), metadata_sha256


def _find_record_body(storage_root: str | Path, workspace: str, ref: dict[str, str]) -> str:
    root = Path(storage_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("storage_root_unavailable")
    safe_workspace = _safe_segment(workspace, "workspace")
    safe_notebook = _safe_segment(ref["notebook"], "notebook")
    dirname = "notes" if ref["kind"] == "note" else "sources"
    record_dir = _resolve_inside(root, root / "workspaces" / safe_workspace / "notebooks" / safe_notebook / dirname)
    if not record_dir.exists() or not record_dir.is_dir():
        raise ValueError("record_missing")
    matches: list[str] = []
    for candidate in sorted(record_dir.glob("*.json")):
        safe_path = _resolve_inside(root, candidate)
        with safe_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("record_json_not_object")
        if _record_id(ref["kind"], data) == ref["id"]:
            matches.append(_body_for_record(ref["kind"], data))
    if len(matches) != 1:
        raise ValueError("record_missing_or_duplicate")
    return matches[0]


def _assess_body(body: str) -> dict[str, Any]:
    sensitive = bool(SENSITIVE_RE.search(body))
    raw_log = bool(RAW_LOG_RE.search(body))
    transcript = bool(TRANSCRIPT_RE.search(body))
    customer_data = bool(CUSTOMER_DATA_RE.search(body))
    indicators = [sensitive, raw_log, transcript, customer_data]
    return {
        "contains_sensitive_indicator": sensitive,
        "contains_raw_log_indicator": raw_log,
        "contains_transcript_indicator": transcript,
        "contains_customer_data_indicator": customer_data,
        "body_bytes": len(body.encode("utf-8")),
        "indicator_count": sum(1 for item in indicators if item),
        "uncertain": not body.strip(),
    }


def sanitize_trial(plan: dict[str, Any], authorization: dict[str, Any], *, authorization_sha256: str, storage_root: str | Path, workspace: str = "default") -> dict[str, Any]:
    try:
        ref, expected_sha256 = _validate_plan(plan, authorization, authorization_sha256=authorization_sha256)
        body = _find_record_body(storage_root, workspace, ref)
        body_sha = _sha_body(body)
        if body_sha != expected_sha256:
            raise ValueError("metadata_sha256_mismatch")
        assessment = _assess_body(body)
        if assessment["indicator_count"] or assessment["uncertain"]:
            payload = _blocked("sensitive_or_uncertain_content", ref)
            payload["candidate_count"] = 1
            payload["record_metadata_sha256_verified"] = True
            payload["assessment"] = assessment
            return payload
        payload = _base_payload(ref)
        payload.update(
            {
                "ok": True,
                "decision": "safe_for_human_summary_draft",
                "candidate_count": 1,
                "metadata_sha256": expected_sha256,
                "record_metadata_sha256_verified": True,
                "assessment": assessment,
                "errors": [],
            }
        )
        return payload
    except (OSError, json.JSONDecodeError, ValueError):
        return _blocked("malformed_unknown_or_hash_mismatch")


def emit(payload: dict[str, Any], pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sanitize exactly one local no-write NotebookLM summary trial candidate.")
    parser.add_argument("--plan", required=True, help="no-write summary trial plan JSON")
    parser.add_argument("--authorization", required=True, help="one-record no-write summary trial authorization JSON")
    parser.add_argument("--storage-root", required=True, help="local storage root; never printed")
    parser.add_argument("--workspace", default="default", help="local workspace name")
    parser.add_argument("--json", action="store_true", help="pretty JSON output")
    args = parser.parse_args(argv)
    try:
        with Path(args.plan).open("r", encoding="utf-8") as handle:
            plan = json.load(handle)
        with Path(args.authorization).open("r", encoding="utf-8") as handle:
            authorization = json.load(handle)
        if not isinstance(plan, dict):
            raise ValueError("plan_json_not_object")
        if not isinstance(authorization, dict):
            raise ValueError("authorization_json_not_object")
        payload = sanitize_trial(
            plan,
            authorization,
            authorization_sha256=_file_sha256(args.authorization),
            storage_root=args.storage_root,
            workspace=args.workspace,
        )
    except (OSError, json.JSONDecodeError, ValueError):
        payload = _blocked("input_read_failed")
    emit(payload, args.json)
    return 0 if payload.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
