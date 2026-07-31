#!/usr/bin/env python3
"""Read-only project memory registry validator and resolver.

This module is intentionally stdlib-only and passive. It validates a local JSON
registry and returns bounded project routes; it never reads source bodies, runs
checks, imports runtime/gate/memory clients, writes notebooks, or calls network.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = Path("config/project_memory_registry.json")
REGISTRY_SCHEMA = "project_registry.v1"
RESOLUTION_SCHEMA = "project_registry_resolution.v1"
VALIDATION_SCHEMA = "project_registry_validation.v1"

PROJECT_KEYS = {
    "project_id",
    "status",
    "canonical_notebook",
    "owner",
    "rollback_owner",
    "docs_refs",
    "registry_refs",
    "route_resolvers",
    "local_checks",
    "legacy_notebooks_excluded",
    "shared_notebooks",
    "write_policy",
    "allowed_dependency_projects",
}
LEGACY_PROJECT_KEYS = (PROJECT_KEYS - {"owner", "rollback_owner"}) | {"owner_tbd", "rollback_owner_tbd"}
TOP_KEYS = {"schema", "projects"}
ROUTABLE_STATUSES = {"active", "pilot_ready"}
PENDING_STATUS = "pending_owner_confirmation"
VALIDATING_STATUS = "validating"
NON_ROUTABLE_DENIALS = {
    PENDING_STATUS: "project_not_routable_pending_owner_confirmation",
    VALIDATING_STATUS: "project_not_routable_validating",
}
ALL_STATUSES = ROUTABLE_STATUSES | {PENDING_STATUS, VALIDATING_STATUS}
SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+ -]{0,127}$")
BROAD_LEGACY_VALUES = {"*", "all", "ALL", "cc", "shared", "default"}
NMBOT_REQUIRED_REFS = {
    "docs_refs": {
        "docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md",
        "docs/MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md",
    },
    "registry_refs": {
        "config/nmbot_retrieval_sources.json",
        "config/nmbot_context_gate_intents.json",
    },
    "route_resolvers": {
        "scripts/nmbot_navigation.py",
        "scripts/nmbot_context_gate.py",
    },
    "local_checks": {"python3 scripts/nmbot_check.py docs"},
}


def _contained_repo_path(raw: str) -> Path | None:
    if not isinstance(raw, str) or not raw or raw.startswith("/"):
        return None
    candidate = (ROOT / raw).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError:
        return None
    return candidate


def _registry_path(path: str | Path | None) -> Path:
    raw = str(DEFAULT_REGISTRY if path is None else path)
    resolved = _contained_repo_path(raw)
    if resolved is None:
        raise ValueError("registry path must be repo-relative and contained")
    return resolved


def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load registry JSON from a repo-contained relative path."""

    registry_path = _registry_path(path)
    with registry_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("registry JSON must be an object")
    return data


def _add(errors: list[dict[str, str]], code: str, message: str) -> None:
    errors.append({"code": code, "message": message})


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _is_project_row(row: dict[str, Any]) -> bool:
    return set(row) in (PROJECT_KEYS, LEGACY_PROJECT_KEYS)


def _owner_fields(row: dict[str, Any]) -> tuple[Any, Any]:
    if "owner" in row and "rollback_owner" in row:
        return row["owner"], row["rollback_owner"]
    return row.get("owner_tbd"), row.get("rollback_owner_tbd")


def _is_owner_value(value: Any) -> bool:
    return isinstance(value, str) and (value == "TBD" or (value == value.strip() and bool(IDENTITY_RE.match(value))))


def _owners_confirmed(row: dict[str, Any]) -> bool:
    owner, rollback_owner = _owner_fields(row)
    return isinstance(owner, str) and isinstance(rollback_owner, str) and owner != "TBD" and rollback_owner != "TBD"


def _validate_refs(row: dict[str, Any], project_id: str, errors: list[dict[str, str]]) -> None:
    for field in ("docs_refs", "registry_refs", "route_resolvers"):
        for ref in row[field]:
            path = _contained_repo_path(ref)
            if path is None:
                _add(errors, "unsafe_ref", f"{project_id}.{field} must stay repo-relative: {ref}")
            elif not path.exists():
                _add(errors, "missing_ref", f"{project_id}.{field} does not exist: {ref}")
    if project_id == "nmbot":
        for field, required in NMBOT_REQUIRED_REFS.items():
            missing = sorted(required - set(row[field]))
            if missing:
                _add(errors, "missing_nmbot_ref", f"nmbot.{field} missing required refs: {missing}")


def validate_registry(registry: dict[str, Any]) -> dict[str, Any]:
    """Return machine-readable validation result; no side effects."""

    errors: list[dict[str, str]] = []
    if not isinstance(registry, dict):
        _add(errors, "registry_type", "registry must be an object")
        return {"schema": VALIDATION_SCHEMA, "valid": False, "project_count": 0, "errors": errors}
    if set(registry) != TOP_KEYS:
        _add(errors, "top_level_keys", f"top-level keys must be exactly {sorted(TOP_KEYS)}")
    if registry.get("schema") != REGISTRY_SCHEMA:
        _add(errors, "schema", f"schema must be {REGISTRY_SCHEMA}")
    projects = registry.get("projects")
    if not isinstance(projects, list) or not projects:
        _add(errors, "projects", "projects must be a non-empty list")
        projects = []

    seen: set[str] = set()
    known_ids: set[str] = set()
    for index, row in enumerate(projects):
        if not isinstance(row, dict):
            _add(errors, "project_type", f"projects[{index}] must be an object")
            continue
        project_id = row.get("project_id", f"projects[{index}]")
        if not _is_project_row(row):
            _add(errors, "project_keys", f"{project_id} keys must be exactly {sorted(PROJECT_KEYS)}")
            continue
        if isinstance(row["project_id"], str):
            known_ids.add(row["project_id"])

    for index, row in enumerate(projects):
        if not isinstance(row, dict) or not _is_project_row(row):
            continue
        project_id = row["project_id"]
        if not isinstance(project_id, str) or not SLUG_RE.match(project_id):
            _add(errors, "project_id", f"projects[{index}].project_id must be a slug")
            continue
        if project_id in seen:
            _add(errors, "duplicate_project_id", f"duplicate project_id: {project_id}")
        seen.add(project_id)

        if row["status"] not in ALL_STATUSES:
            _add(errors, "status", f"{project_id}.status is unsupported: {row['status']}")
        if not isinstance(row["canonical_notebook"], str) or not SLUG_RE.match(row["canonical_notebook"]):
            _add(errors, "canonical_notebook", f"{project_id}.canonical_notebook must be one non-empty slug")
        owner, rollback_owner = _owner_fields(row)
        if not _is_owner_value(owner) or not _is_owner_value(rollback_owner):
            _add(errors, "owner", f"{project_id} owner fields must be TBD or a non-empty identity")
        for field in ("docs_refs", "registry_refs", "route_resolvers", "local_checks", "legacy_notebooks_excluded", "shared_notebooks", "allowed_dependency_projects"):
            if not _is_str_list(row[field]):
                _add(errors, "list_type", f"{project_id}.{field} must be a list of non-empty strings")
        if row["write_policy"] != "canonical_only":
            _add(errors, "write_policy", f"{project_id}.write_policy must be canonical_only")
        if row["shared_notebooks"]:
            _add(errors, "shared_notebooks", f"{project_id}.shared_notebooks must be empty by default")
        legacy = row["legacy_notebooks_excluded"]
        if any(value in BROAD_LEGACY_VALUES or value == row["canonical_notebook"] for value in legacy):
            _add(errors, "broad_legacy_exclusion", f"{project_id}.legacy_notebooks_excluded contains broad/canonical value")
        if len(set(legacy)) != len(legacy):
            _add(errors, "duplicate_legacy_exclusion", f"{project_id}.legacy_notebooks_excluded has duplicates")
        for dependency in row["allowed_dependency_projects"]:
            if dependency not in known_ids:
                _add(errors, "unknown_dependency_project", f"{project_id} depends on unknown project_id: {dependency}")
            if dependency == project_id:
                _add(errors, "self_dependency_project", f"{project_id} cannot depend on itself")
        for dependency in row["allowed_dependency_projects"]:
            dependency_row = next((candidate for candidate in projects if isinstance(candidate, dict) and candidate.get("project_id") == dependency), None)
            if isinstance(dependency_row, dict) and dependency_row.get("allowed_dependency_projects"):
                _add(errors, "transitive_dependency_project", f"{project_id} dependency must not have transitive dependencies: {dependency}")
        if row["status"] == PENDING_STATUS and any(row[field] for field in ("docs_refs", "registry_refs", "route_resolvers", "local_checks")):
            _add(errors, "pending_refs", f"{project_id} pending rows must keep refs/checks empty")
        if row["status"] in ROUTABLE_STATUSES and project_id != "nmbot" and not _owners_confirmed(row):
            _add(errors, "routable_owner", f"{project_id} routable rows require confirmed owner and rollback_owner")
        if row["status"] in ROUTABLE_STATUSES:
            for field in ("docs_refs", "registry_refs", "route_resolvers", "local_checks"):
                if not row[field]:
                    _add(errors, "routable_refs", f"{project_id}.{field} must be non-empty before routing")
        if project_id == "nmbot" or row["docs_refs"] or row["registry_refs"] or row["route_resolvers"]:
            _validate_refs(row, project_id, errors)

    return {"schema": VALIDATION_SCHEMA, "valid": not errors, "project_count": len(projects), "errors": errors}


def resolve_project(registry: dict[str, Any], project_id: str) -> dict[str, Any]:
    """Resolve a project route or return a stable fail-closed denial object."""

    validation = validate_registry(registry)
    if not validation["valid"]:
        return {
            "schema": RESOLUTION_SCHEMA,
            "ok": False,
            "denied_reason": "registry_invalid",
            "project_id": project_id,
            "validation": validation,
        }
    for row in registry["projects"]:
        if row["project_id"] == project_id:
            if row["status"] in NON_ROUTABLE_DENIALS:
                return {
                    "schema": RESOLUTION_SCHEMA,
                    "ok": False,
                    "denied_reason": NON_ROUTABLE_DENIALS[row["status"]],
                    "project_id": project_id,
                    "status": row["status"],
                    "canonical_notebook": row["canonical_notebook"],
                    "write_policy": row["write_policy"],
                }
            if row["status"] in ROUTABLE_STATUSES:
                return {
                    "schema": RESOLUTION_SCHEMA,
                    "ok": True,
                    "project_id": project_id,
                    "status": row["status"],
                    "canonical_notebook": row["canonical_notebook"],
                    "write_policy": row["write_policy"],
                    "docs_refs": list(row["docs_refs"]),
                    "registry_refs": list(row["registry_refs"]),
                    "route_resolvers": list(row["route_resolvers"]),
                    "local_checks": list(row["local_checks"]),
                    "allowed_dependency_projects": list(row["allowed_dependency_projects"]),
                    "shared_notebooks": [],
                    "legacy_exclusion_enforced": True,
                }
            break
    return {
        "schema": RESOLUTION_SCHEMA,
        "ok": False,
        "denied_reason": "project_unknown",
        "project_id": project_id,
    }


def _emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if payload.get("schema") == VALIDATION_SCHEMA:
            print("valid" if payload.get("valid") else "invalid")
            for error in payload.get("errors", []):
                print(f"ERROR {error['code']}: {error['message']}")
        else:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _list_payload(registry: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": RESOLUTION_SCHEMA,
        "projects": [
            {
                "project_id": row["project_id"],
                "status": row["status"],
                "canonical_notebook": row["canonical_notebook"],
            }
            for row in registry.get("projects", [])
            if isinstance(row, dict) and {"project_id", "status", "canonical_notebook"} <= set(row)
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate or resolve the local project memory registry.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY), help="repo-relative registry path")
    parser.add_argument("--project-id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args(argv)

    try:
        registry = load_registry(args.registry)
    except Exception as exc:  # fail closed, machine-readable under --json
        payload = {"schema": VALIDATION_SCHEMA, "valid": False, "project_count": 0, "errors": [{"code": "registry_load_failed", "message": str(exc)}]}
        _emit(payload, args.json)
        return 2

    validation = validate_registry(registry)
    if args.validate:
        _emit(validation, args.json)
        if args.list:
            _emit(_list_payload(registry), args.json)
        return 0 if validation["valid"] else 2
    if not validation["valid"]:
        _emit(validation, args.json)
        return 2
    if args.list:
        _emit(_list_payload(registry), args.json)
        return 0
    if not args.project_id:
        payload = {"schema": RESOLUTION_SCHEMA, "ok": False, "denied_reason": "project_id_required"}
        _emit(payload, args.json)
        return 2
    result = resolve_project(registry, args.project_id)
    _emit(result, args.json)
    if result.get("ok") or result.get("denied_reason") in set(NON_ROUTABLE_DENIALS.values()):
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
