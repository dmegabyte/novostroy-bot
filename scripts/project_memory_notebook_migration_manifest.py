#!/usr/bin/env python3
"""Local-only pre-migration manifest builder for NotebookLM inventory metadata.

This tool consumes the already sanitized classification manifest only. It never
calls NotebookLM/MCP clients, network APIs, subprocesses, runtime code, source
storage writers or migration writers. Output is summary-only: record refs,
hashes, ownership decisions and dispositions, with no titles, bodies, raw paths,
logs, transcripts or secret values.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from project_memory_registry import resolve_project


MANIFEST_SCHEMA = "project_memory_pre_migration_manifest.v1"
CLASSIFICATION_SCHEMA = "project_memory_notebook_classification_manifest.v1"
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

BANNED_OUTPUT_KEYS = {"title", "body", "content", "note", "raw", "transcript", "log", "path"}
BLOCKED_EXECUTION_REASON = "blocked_until_separately_authorized_write_plan_and_owner_gate"
ROLLBACK_SCOPE = "routing_only_no_data_deletion"

DEFAULT_CLASSIFICATION = Path("/tmp/opencode/nmbot_notebook_classification_v4.json")
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
        "schema": MANIFEST_SCHEMA,
        "ok": False,
        **SAFE_FLAGS,
        "migration_performed": False,
        "execution_blocked": True,
        "blocked_reason": BLOCKED_EXECUTION_REASON,
        "rollback_scope": ROLLBACK_SCOPE,
        "record_count": 0,
        "selected_count": 0,
        "held_unresolved_count": 0,
        "sensitive_excluded_count": 0,
        "disposition_counts": {},
        "records": [],
        "errors": [],
    }


def _error(code: str, message: str) -> dict[str, Any]:
    payload = _base_payload()
    payload["denied_reason"] = code
    payload["errors"] = [{"code": code, "message": message}]
    return payload


def _ensure_no_leaky_keys(value: Any, marker: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in BANNED_OUTPUT_KEYS:
                raise ValueError(f"prohibited_output_key:{marker}.{key}")
            _ensure_no_leaky_keys(child, f"{marker}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _ensure_no_leaky_keys(child, f"{marker}[{index}]")


def _validate_safe_flags(payload: dict[str, Any], prefix: str) -> None:
    for key, expected in SAFE_FLAGS.items():
        if payload.get(key) is not expected:
            raise ValueError(f"{prefix}_flag_mismatch:{key}")


def _registry_projects(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise ValueError("invalid_registry_schema")
    projects = registry.get("projects")
    if not isinstance(projects, list):
        raise ValueError("registry_projects_missing")
    by_id: dict[str, dict[str, Any]] = {}
    for project in projects:
        if not isinstance(project, dict):
            raise ValueError("registry_project_not_object")
        project_id = project.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("registry_project_id_missing")
        owner = project.get("owner", project.get("owner_tbd"))
        rollback_owner = project.get("rollback_owner", project.get("rollback_owner_tbd"))
        if not isinstance(owner, str) or not owner or not isinstance(rollback_owner, str) or not rollback_owner:
            raise ValueError("registry_owner_missing")
        by_id[project_id] = project
    return by_id


def _validate_tree(tree: dict[str, Any]) -> dict[str, Any]:
    if tree.get("schema") != TREE_SCHEMA:
        raise ValueError("invalid_tree_schema")
    _validate_safe_flags(tree, "tree")
    if tree.get("human_owner") != "TBD" or tree.get("rollback_owner") != "TBD":
        raise ValueError("tree_owner_must_remain_tbd")
    decisions = tree.get("canonical_decisions")
    if not isinstance(decisions, dict):
        raise ValueError("tree_canonical_decisions_missing")
    if decisions.get("qapairs") != "canonical_owner_cc-daemons_all_qapairs_contours_nested_standalone_poller_legacy":
        raise ValueError("qapairs_canonical_decision_missing")
    if decisions.get("n8n_audit_notebook") != "n8n_audit":
        raise ValueError("n8n_audit_canonical_decision_missing")
    return decisions


def _validate_classification(classification: dict[str, Any]) -> list[dict[str, Any]]:
    if classification.get("schema") != CLASSIFICATION_SCHEMA:
        raise ValueError("invalid_classification_schema")
    if classification.get("ok") is not True:
        raise ValueError("classification_not_ok")
    _validate_safe_flags(classification, "classification")
    records = classification.get("records")
    if not isinstance(records, list):
        raise ValueError("classification_records_missing")
    if classification.get("record_count") != len(records):
        raise ValueError("classification_record_count_mismatch")
    classified = sum(1 for item in records if item.get("confidence") == "explicit" and item.get("owner_path") and not item.get("sensitive_exclusion"))
    sensitive = sum(1 for item in records if item.get("sensitive_exclusion") is True)
    unresolved = len(records) - classified - sensitive
    if classification.get("classified_count") != classified + sensitive:
        raise ValueError("classification_classified_count_mismatch")
    if classification.get("sensitive_exclusion_count") != sensitive:
        raise ValueError("classification_sensitive_count_mismatch")
    if classification.get("unresolved_count") != unresolved:
        raise ValueError("classification_unresolved_count_mismatch")
    return records


def _project_for_owner(owner_path: str | None) -> str | None:
    if not owner_path:
        return None
    if owner_path == "ecosystem/nmbot" or owner_path.startswith("ecosystem/nmbot/"):
        return "nmbot"
    if owner_path == "ecosystem/cc-daemons" or owner_path.startswith("ecosystem/cc-daemons/projects/qapairs"):
        return "qapairs"
    if owner_path == "ecosystem/N8N_AUDIT" or owner_path.startswith("ecosystem/N8N_AUDIT/"):
        return "n8n_audit"
    if owner_path.startswith("ecosystem/mpn-daemon"):
        return "mpn-daemon"
    if owner_path.startswith("ecosystem/opencode"):
        return "opencode"
    return None


def _routable_project_resolution(project_id: str | None, registry: dict[str, Any]) -> dict[str, Any] | None:
    if not project_id:
        return None
    resolution = resolve_project(registry, project_id)
    if not isinstance(resolution, dict):
        return {"ok": False, "denied_reason": "project_resolution_invalid", "project_id": project_id}
    return resolution


def build_manifest(classification: dict[str, Any], registry: dict[str, Any], tree: dict[str, Any], *, classification_sha256: str) -> dict[str, Any]:
    try:
        projects = _registry_projects(registry)
        decisions = _validate_tree(tree)
        records = _validate_classification(classification)
        if projects.get("qapairs", {}).get("canonical_notebook") != "cc-daemons":
            raise ValueError("qapairs_registry_canonical_notebook_mismatch")
    except ValueError as exc:
        return _error(str(exc), "pre-migration manifest input failed closed")

    output_records: list[dict[str, Any]] = []
    counts = {"selected_for_summary_plan": 0, "held_unresolved": 0, "excluded_sensitive": 0}
    for item in records:
        record_ref = item.get("record_ref")
        if not isinstance(record_ref, dict) or not all(isinstance(record_ref.get(key), str) and record_ref.get(key) for key in ("notebook", "kind", "id")):
            return _error("record_ref_invalid", "pre-migration manifest input failed closed")
        sha = item.get("metadata_sha256")
        if not isinstance(sha, str) or not sha:
            return _error("record_sha_missing", "pre-migration manifest input failed closed")

        sensitive = item.get("sensitive_exclusion") is True
        explicit = item.get("confidence") == "explicit" and isinstance(item.get("owner_path"), str)
        owner_path = item.get("owner_path") if explicit else None
        project_id = _project_for_owner(owner_path)
        resolution = _routable_project_resolution(project_id, registry) if explicit else None
        routable = bool(resolution and resolution.get("ok") is True)
        target_notebook = resolution.get("canonical_notebook") if routable and isinstance(resolution.get("canonical_notebook"), str) else None
        if sensitive:
            disposition = "excluded_sensitive_no_migration"
            counts["excluded_sensitive"] += 1
        elif explicit and routable and target_notebook:
            disposition = "selected_for_summary_plan"
            counts["selected_for_summary_plan"] += 1
        else:
            disposition = "held_unresolved_no_migration"
            counts["held_unresolved"] += 1

        selected = disposition == "selected_for_summary_plan"
        if selected or sensitive or not explicit:
            migration_blocker = None if selected else "not_selected"
        elif resolution and isinstance(resolution.get("denied_reason"), str):
            migration_blocker = resolution["denied_reason"]
        else:
            migration_blocker = "canonical_notebook_unresolved"
        output_records.append(
            {
                "record_ref": {"notebook": record_ref["notebook"], "kind": record_ref["kind"], "id": record_ref["id"]},
                "metadata_sha256": sha,
                "source_owner_path": owner_path,
                "target_project_id": project_id if selected else None,
                "target_canonical_notebook": target_notebook if selected else None,
                "source_ownership_decision": "explicit_owner_path" if explicit else "not_selected",
                "target_ownership_decision": "canonical_notebook_from_registry_resolver" if selected else "blocked_or_not_applicable",
                "migration_blocker": migration_blocker,
                "lifecycle": item.get("lifecycle") if explicit else "not_migrated",
                "disposition": disposition,
                "evidence_refs": list(item.get("evidence_refs") or []),
                "provenance": {
                    "classification_schema": CLASSIFICATION_SCHEMA,
                    "classification_sha256": classification_sha256,
                    "record_metadata_sha256_verified": True,
                },
            }
        )

    output_records.sort(key=lambda row: (row["record_ref"]["notebook"], row["record_ref"]["kind"], row["record_ref"]["id"]))
    payload = _base_payload()
    payload.update(
        {
            "ok": True,
            "record_count": len(output_records),
            "selected_count": counts["selected_for_summary_plan"],
            "held_unresolved_count": counts["held_unresolved"],
            "sensitive_excluded_count": counts["excluded_sensitive"],
            "disposition_counts": counts,
            "canonical_decisions": {
                "qapairs": decisions["qapairs"],
                "n8n_audit_notebook": decisions["n8n_audit_notebook"],
            },
            "execution_gate": {
                "write_plan_required": True,
                "owner_gate_required": True,
                "human_owner": "TBD",
                "rollback_owner": "TBD",
                "data_migration_authorized": False,
                "notebook_write_authorized": False,
            },
            "records": output_records,
            "errors": [],
        }
    )
    try:
        _ensure_no_leaky_keys(payload)
    except ValueError as exc:
        return _error(str(exc), "pre-migration manifest output blocked unsafe field")
    return payload


def emit(payload: dict[str, Any], output: str | None, pretty: bool) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a safe local pre-migration manifest from sanitized NotebookLM classification metadata.")
    parser.add_argument("--classification", default=str(DEFAULT_CLASSIFICATION), help="sanitized classification manifest JSON path")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY), help="project memory registry JSON path")
    parser.add_argument("--tree", default=str(DEFAULT_TREE), help="project memory tree JSON path")
    parser.add_argument("--output", default=None, help="optional caller-provided JSON output path; default is stdout")
    parser.add_argument("--json", action="store_true", help="pretty JSON output")
    args = parser.parse_args(argv)

    try:
        classification = _read_json(args.classification)
        registry = _read_json(args.registry)
        tree = _read_json(args.tree)
        payload = build_manifest(classification, registry, tree, classification_sha256=_file_sha256(args.classification))
    except (OSError, json.JSONDecodeError):
        payload = _error("input_read_failed", "pre-migration manifest input could not be read")
    emit(payload, args.output, args.json)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
