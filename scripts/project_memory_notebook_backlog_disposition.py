#!/usr/bin/env python3
"""Local-only NotebookLM backlog disposition manifest builder.

Consumes only sanitized classification metadata, the already blocked
pre-migration manifest, the project registry and the project memory tree. It
does not read NotebookLM/storage records, does not route, does not call network
or subprocess APIs, and never grants write authorization.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "project_memory_notebook_backlog_disposition.v1"
CLASSIFICATION_SCHEMA = "project_memory_notebook_classification_manifest.v1"
PRE_MIGRATION_SCHEMA = "project_memory_pre_migration_manifest.v1"
REGISTRY_SCHEMA = "project_registry.v1"
TREE_SCHEMA = "project_memory_tree.v1"

SAFE_FLAGS = {
    "read_only": True,
    "write_performed": False,
    "notebook_mutation_performed": False,
    "automatic_routing_changed": False,
    "production_verified": False,
    "requires_owner_confirmation": True,
}
NO_WRITE_FLAGS = {
    "migration_performed": False,
    "execution_blocked": True,
    "data_migration_authorized": False,
    "notebook_write_authorized": False,
}

BANNED_INPUT_KEYS = {"title", "body", "content", "note", "raw", "transcript", "log", "storage_root", "path"}
BANNED_OUTPUT_KEYS = BANNED_INPUT_KEYS | {"owner_path", "source_owner_path", "target_canonical_notebook"}
NONPROJECT_NOTEBOOK_ALLOWLIST = {"english_lesson", "gas-mcp", "knowledge-mcp", "overmind-monitor", "ruview", "call-center-audit"}

DEFAULT_REGISTRY = Path(__file__).resolve().parents[1] / "config" / "project_memory_registry.json"
DEFAULT_TREE = Path(__file__).resolve().parents[1] / "config" / "project_memory_tree.json"


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
        "schema": SCHEMA,
        "ok": False,
        **SAFE_FLAGS,
        **NO_WRITE_FLAGS,
        "record_count": 0,
        "disposition_counts": {},
        "records": [],
        "errors": [],
    }


def _error(code: str) -> dict[str, Any]:
    payload = _base_payload()
    payload["denied_reason"] = code
    payload["errors"] = [{"code": code, "message": "backlog disposition input failed closed"}]
    return payload


def _ensure_no_banned_keys(value: Any, banned: set[str], marker: str = "$", *, allow_owner_path: bool = False) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in banned and not (allow_owner_path and lowered in {"owner_path", "source_owner_path"}):
                raise ValueError(f"prohibited_key:{marker}.{key}")
            _ensure_no_banned_keys(child, banned, f"{marker}.{key}", allow_owner_path=allow_owner_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _ensure_no_banned_keys(child, banned, f"{marker}[{index}]", allow_owner_path=allow_owner_path)


def _validate_safe_flags(payload: dict[str, Any], label: str) -> None:
    for key, expected in SAFE_FLAGS.items():
        if payload.get(key) is not expected:
            raise ValueError(f"{label}_flag_mismatch:{key}")


def _record_key_from_ref(ref: Any) -> tuple[str, str, str]:
    if not isinstance(ref, dict):
        raise ValueError("record_ref_invalid")
    key = (ref.get("notebook"), ref.get("kind"), ref.get("id"))
    if not all(isinstance(item, str) and item for item in key):
        raise ValueError("record_ref_invalid")
    return key  # type: ignore[return-value]


def _safe_ref(ref: dict[str, Any]) -> dict[str, str]:
    notebook, kind, record_id = _record_key_from_ref(ref)
    return {"notebook": notebook, "kind": kind, "id": record_id}


def _validate_classification(payload: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    _ensure_no_banned_keys(payload, BANNED_INPUT_KEYS, allow_owner_path=True)
    if payload.get("schema") != CLASSIFICATION_SCHEMA or payload.get("ok") is not True:
        raise ValueError("invalid_classification_manifest")
    _validate_safe_flags(payload, "classification")
    records = payload.get("records")
    if not isinstance(records, list) or payload.get("record_count") != len(records):
        raise ValueError("classification_record_count_mismatch")
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    explicit = 0
    sensitive = 0
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("classification_record_not_object")
        key = _record_key_from_ref(item.get("record_ref"))
        if key in seen:
            raise ValueError("duplicate_classification_record")
        sha = item.get("metadata_sha256")
        if not isinstance(sha, str) or not sha:
            raise ValueError("classification_record_sha_missing")
        if item.get("sensitive_exclusion") is True:
            sensitive += 1
        elif item.get("confidence") == "explicit" and isinstance(item.get("owner_path"), str):
            explicit += 1
        seen[key] = item
    unresolved = len(records) - explicit - sensitive
    if payload.get("classified_count") != explicit + sensitive:
        raise ValueError("classification_classified_count_mismatch")
    if payload.get("sensitive_exclusion_count") != sensitive or payload.get("unresolved_count") != unresolved:
        raise ValueError("classification_count_mismatch")
    return seen


def _validate_pre_manifest(payload: dict[str, Any]) -> list[dict[str, Any]]:
    _ensure_no_banned_keys(payload, BANNED_INPUT_KEYS, allow_owner_path=True)
    if payload.get("schema") != PRE_MIGRATION_SCHEMA or payload.get("ok") is not True:
        raise ValueError("invalid_pre_migration_manifest")
    _validate_safe_flags(payload, "pre_migration")
    if payload.get("migration_performed") is not False or payload.get("execution_blocked") is not True:
        raise ValueError("pre_migration_execution_flags_invalid")
    gate = payload.get("execution_gate")
    if not isinstance(gate, dict) or gate.get("data_migration_authorized") is not False or gate.get("notebook_write_authorized") is not False:
        raise ValueError("pre_migration_write_gate_invalid")
    records = payload.get("records")
    if not isinstance(records, list) or payload.get("record_count") != len(records):
        raise ValueError("pre_migration_record_count_mismatch")
    selected = sum(1 for item in records if isinstance(item, dict) and item.get("disposition") == "selected_for_summary_plan")
    sensitive = sum(1 for item in records if isinstance(item, dict) and item.get("disposition") == "excluded_sensitive_no_migration")
    held = sum(1 for item in records if isinstance(item, dict) and item.get("disposition") == "held_unresolved_no_migration")
    counts = payload.get("disposition_counts")
    if not isinstance(counts, dict):
        raise ValueError("pre_migration_counts_missing")
    if counts.get("selected_for_summary_plan") != selected or counts.get("excluded_sensitive") != sensitive or counts.get("held_unresolved") != held:
        raise ValueError("pre_migration_counts_mismatch")
    if payload.get("selected_count") != selected or payload.get("sensitive_excluded_count") != sensitive or payload.get("held_unresolved_count") != held:
        raise ValueError("pre_migration_top_counts_mismatch")
    return records


def _canonical_notebooks(registry: dict[str, Any], tree: dict[str, Any]) -> set[str]:
    if registry.get("schema") != REGISTRY_SCHEMA or tree.get("schema") != TREE_SCHEMA:
        raise ValueError("invalid_registry_or_tree_schema")
    _validate_safe_flags(tree, "tree")
    projects = registry.get("projects")
    decisions = tree.get("canonical_decisions")
    if not isinstance(projects, list) or not isinstance(decisions, dict):
        raise ValueError("registry_or_tree_shape_invalid")
    nmbot = [item for item in projects if isinstance(item, dict) and item.get("project_id") == "nmbot"]
    if len(nmbot) != 1 or nmbot[0].get("canonical_notebook") != "nmbot":
        raise ValueError("nmbot_canonical_notebook_missing")
    if decisions.get("n8n_audit_notebook") != "n8n_audit":
        raise ValueError("n8n_audit_canonical_decision_missing")
    return {"nmbot", "n8n_audit"}


def build_disposition(
    classification: dict[str, Any],
    pre_manifest: dict[str, Any],
    registry: dict[str, Any],
    tree: dict[str, Any],
    *,
    classification_sha256: str,
    pre_manifest_sha256: str,
) -> dict[str, Any]:
    try:
        classified_by_key = _validate_classification(classification)
        pre_records = _validate_pre_manifest(pre_manifest)
        canonical = _canonical_notebooks(registry, tree)
        if len(pre_records) != len(classified_by_key):
            raise ValueError("input_record_count_mismatch")
    except ValueError as exc:
        return _error(str(exc))

    selected_keys: set[tuple[str, str, str]] = set()
    pre_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    try:
        for row in pre_records:
            if not isinstance(row, dict):
                raise ValueError("pre_migration_record_not_object")
            key = _record_key_from_ref(row.get("record_ref"))
            if key in pre_by_key:
                raise ValueError("duplicate_pre_migration_record")
            pre_by_key[key] = row
            cls = classified_by_key.get(key)
            if not cls:
                raise ValueError("pre_migration_record_missing_from_classification")
            if row.get("metadata_sha256") != cls.get("metadata_sha256"):
                raise ValueError("record_sha_identity_mismatch")
            provenance = row.get("provenance")
            if not isinstance(provenance, dict) or provenance.get("classification_sha256") != classification_sha256:
                raise ValueError("classification_sha_identity_mismatch")
            if row.get("disposition") == "selected_for_summary_plan":
                selected_keys.add(key)
        if set(pre_by_key) != set(classified_by_key):
            raise ValueError("input_record_identity_set_mismatch")
    except ValueError as exc:
        return _error(str(exc))

    output_records: list[dict[str, Any]] = []
    counts = {
        "excluded_sensitive_no_migration": 0,
        "selected_for_summary_plan": 0,
        "retained_in_canonical_notebook_no_migration": 0,
        "retained_unmanaged_notebook_no_migration": 0,
        "held_unresolved_no_migration": 0,
    }
    for key, item in classified_by_key.items():
        notebook, _kind, _record_id = key
        if item.get("sensitive_exclusion") is True:
            disposition = "excluded_sensitive_no_migration"
            reason = "sensitive_exclusion_from_classification"
        elif key in selected_keys:
            disposition = "selected_for_summary_plan"
            reason = "preserved_from_blocked_pre_migration_manifest"
        elif notebook in canonical:
            disposition = "retained_in_canonical_notebook_no_migration"
            reason = "record_already_in_confirmed_canonical_notebook"
        elif notebook in NONPROJECT_NOTEBOOK_ALLOWLIST:
            disposition = "retained_unmanaged_notebook_no_migration"
            reason = "notebook_in_explicit_nonproject_allowlist"
        else:
            disposition = "held_unresolved_no_migration"
            reason = "no_safe_disposition_without_per_record_authorization"
        counts[disposition] += 1
        output_records.append(
            {
                "record_ref": _safe_ref(item["record_ref"]),
                "metadata_sha256": item["metadata_sha256"],
                "disposition": disposition,
                "reason_code": reason,
                "evidence_refs": list(item.get("evidence_refs") or []),
                "safe_flags": {**SAFE_FLAGS, **NO_WRITE_FLAGS},
            }
        )

    output_records.sort(key=lambda row: (row["record_ref"]["notebook"], row["record_ref"]["kind"], row["record_ref"]["id"]))
    payload = _base_payload()
    payload.update(
        {
            "ok": True,
            "record_count": len(output_records),
            "disposition_counts": counts,
            "input_identities": {
                "classification_schema": CLASSIFICATION_SCHEMA,
                "classification_sha256": classification_sha256,
                "pre_migration_schema": PRE_MIGRATION_SCHEMA,
                "pre_migration_manifest_sha256": pre_manifest_sha256,
                "current_input_identities_verified": True,
            },
            "execution_gate": {
                "data_migration_authorized": False,
                "notebook_write_authorized": False,
                "owner_gate_required": True,
            },
            "records": output_records,
            "errors": [],
        }
    )
    try:
        _ensure_no_banned_keys(payload, BANNED_OUTPUT_KEYS)
    except ValueError as exc:
        return _error(str(exc))
    return payload


def emit(payload: dict[str, Any], output: str | None, pretty: bool) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a safe local NotebookLM backlog disposition manifest.")
    parser.add_argument("--classification", required=True, help="sanitized classification manifest JSON path")
    parser.add_argument("--pre-manifest", required=True, help="blocked pre-migration manifest JSON path")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY), help="project memory registry JSON path")
    parser.add_argument("--tree", default=str(DEFAULT_TREE), help="project memory tree JSON path")
    parser.add_argument("--output", default=None, help="optional caller-provided JSON output path; default is stdout")
    parser.add_argument("--json", action="store_true", help="pretty JSON output")
    args = parser.parse_args(argv)
    try:
        payload = build_disposition(
            _read_json(args.classification),
            _read_json(args.pre_manifest),
            _read_json(args.registry),
            _read_json(args.tree),
            classification_sha256=_file_sha256(args.classification),
            pre_manifest_sha256=_file_sha256(args.pre_manifest),
        )
    except (OSError, json.JSONDecodeError):
        payload = _error("input_read_failed")
    emit(payload, args.output, args.json)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
