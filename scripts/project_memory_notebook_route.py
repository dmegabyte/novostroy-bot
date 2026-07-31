#!/usr/bin/env python3
"""NotebookLM canonical route dry-run for project memory.

This module is intentionally stdlib-only and registry-only. It resolves the
canonical project notebook for a requested operation, but never imports or calls
NotebookLM clients, subprocesses, network APIs, MemPalace, runtime code or gate
code. Summary writes are reported as dry-run intent only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from project_memory_registry import load_registry as load_project_registry
from project_memory_registry import resolve_project


ROUTE_SCHEMA = "project_memory_notebook_route.v1"
ALLOWED_OPERATIONS = {"search", "summary-write"}
HISTORY_BOUNDARY = "sources_history_summaries_only_not_current_source_or_production_proof"


def load_route_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load the local project registry used for dry-run notebook routing."""

    return load_project_registry(path)


def resolve_notebook_route(project_id: str, operation: str, registry: dict[str, Any] | None = None, registry_path: str | Path | None = None) -> dict[str, Any]:
    """Resolve canonical notebook route without performing any external action."""

    if operation not in ALLOWED_OPERATIONS:
        return {
            "schema": ROUTE_SCHEMA,
            "ok": False,
            "dry_run": True,
            "project_id": project_id,
            "operation": operation,
            "denied_reason": "operation_not_allowed",
            "allowed_operations": sorted(ALLOWED_OPERATIONS),
            "notebook_call_performed": False,
            "write_performed": False,
        }

    data = registry if registry is not None else load_route_registry(registry_path)
    resolved = resolve_project(data, project_id)
    if not resolved.get("ok"):
        return {
            "schema": ROUTE_SCHEMA,
            "ok": False,
            "dry_run": True,
            "project_id": project_id,
            "operation": operation,
            "denied_reason": resolved.get("denied_reason", "project_not_routable"),
            "canonical_notebook": resolved.get("canonical_notebook"),
            "notebook_call_performed": False,
            "write_performed": False,
        }

    row = next(item for item in data["projects"] if item["project_id"] == project_id)
    return {
        "schema": ROUTE_SCHEMA,
        "ok": True,
        "dry_run": True,
        "project_id": project_id,
        "operation": operation,
        "canonical_notebook": resolved["canonical_notebook"],
        "write_policy": resolved["write_policy"],
        "route": "canonical_project_notebook_only",
        "legacy_exclusion_enforced": True,
        "excluded_legacy_notebooks": list(row["legacy_notebooks_excluded"]),
        "automatic_legacy_notebooks": [],
        "shared_notebooks": [],
        "history_boundary": HISTORY_BOUNDARY,
        "current_source_or_prod_proof_allowed": False,
        "notebook_call_performed": False,
        "write_performed": False,
    }


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run canonical NotebookLM routing by project_id.")
    parser.add_argument("--registry", default=None, help="repo-relative project registry path")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--operation", required=True, choices=sorted(ALLOWED_OPERATIONS))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        payload = resolve_notebook_route(args.project_id, args.operation, registry_path=args.registry)
    except Exception as exc:
        payload = {
            "schema": ROUTE_SCHEMA,
            "ok": False,
            "dry_run": True,
            "project_id": args.project_id,
            "operation": args.operation,
            "denied_reason": "registry_load_failed",
            "errors": [{"code": "registry_load_failed", "message": str(exc)}],
            "notebook_call_performed": False,
            "write_performed": False,
        }
    emit(payload, args.json)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
