#!/usr/bin/env python3
"""Read-only safe metadata inventory for local notebook storage.

This script only reads the local JSON store and emits metadata plus hashes. It
does not migrate, classify, route, call notebook/MCP clients, use subprocesses,
or print note/source bodies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


INVENTORY_SCHEMA = "project_memory_notebook_inventory.v1"
DEFAULT_STORAGE_ROOT = "/home/ser/.codex/memories/knowledge-mcp"
STORAGE_SCOPE = "local_notebook_store"


def _base_payload(storage_root: str | Path, workspace: str, notebooks: list[str] | None) -> dict[str, Any]:
    return {
        "schema": INVENTORY_SCHEMA,
        "ok": False,
        "read_only": True,
        "write_performed": False,
        "content_printed": False,
        "migration_performed": False,
        "automatic_routing_changed": False,
        "requires_owner_confirmation": True,
        "storage_scope": STORAGE_SCOPE,
        "workspace": workspace,
        "notebook_filter": list(notebooks or []),
        "record_count": 0,
        "records": [],
    }


def _safe_segment(value: str, label: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"invalid_{label}")
    return value


def _resolve_inside(root: Path, candidate: Path, code: str) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(code) from exc
    return resolved


def _error_payload(storage_root: str | Path, workspace: str, notebooks: list[str] | None, code: str, message: str) -> dict[str, Any]:
    payload = _base_payload(storage_root, workspace, notebooks)
    payload["denied_reason"] = code
    payload["errors"] = [{"code": code, "message": message}]
    return payload


def _body_for_record(kind: str, data: dict[str, Any]) -> str:
    value = data.get("note") if kind == "note" else data.get("content")
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _record_metadata(kind: str, notebook: str, data: dict[str, Any]) -> dict[str, Any]:
    body = _body_for_record(kind, data)
    body_bytes = body.encode("utf-8")
    record_id = data.get("note_id") if kind == "note" else data.get("source_id")
    return {
        "kind": kind,
        "notebook": notebook,
        "id": "" if record_id is None else str(record_id),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "content_sha256": hashlib.sha256(body_bytes).hexdigest(),
        "body_bytes": len(body_bytes),
    }


def inventory_notebook_storage(storage_root: str | Path = DEFAULT_STORAGE_ROOT, workspace: str = "default", notebooks: list[str] | None = None) -> dict[str, Any]:
    """Return a read-only inventory payload for the requested local store."""

    try:
        safe_workspace = _safe_segment(workspace, "workspace")
        safe_notebooks = [_safe_segment(item, "notebook") for item in (notebooks or [])]
        requested_root = Path(storage_root).expanduser()
        if not requested_root.exists():
            return _error_payload(storage_root, workspace, notebooks, "storage_root_missing", "storage root does not exist")
        root = requested_root.resolve()
        if not root.is_dir():
            return _error_payload(root, workspace, notebooks, "storage_root_not_directory", "storage root is not a directory")
        workspace_dir = _resolve_inside(root, root / "workspaces" / safe_workspace, "workspace_path_escapes_root")
        notebooks_dir = _resolve_inside(root, workspace_dir / "notebooks", "notebooks_path_escapes_root")
    except ValueError as exc:
        return _error_payload(storage_root, workspace, notebooks, str(exc), "requested workspace or notebook is invalid")

    payload = _base_payload(root, safe_workspace, safe_notebooks)
    if not workspace_dir.exists() or not workspace_dir.is_dir():
        payload["denied_reason"] = "workspace_missing"
        payload["errors"] = [{"code": "workspace_missing", "message": "workspace does not exist under storage root"}]
        return payload
    if not notebooks_dir.exists() or not notebooks_dir.is_dir():
        payload["denied_reason"] = "notebooks_dir_missing"
        payload["errors"] = [{"code": "notebooks_dir_missing", "message": "notebooks directory does not exist under workspace"}]
        return payload

    notebook_names = safe_notebooks
    if not notebook_names:
        notebook_names = sorted(item.name for item in notebooks_dir.iterdir() if item.is_dir())

    records: list[dict[str, Any]] = []
    try:
        for notebook in notebook_names:
            notebook_dir = _resolve_inside(root, notebooks_dir / notebook, "notebook_path_escapes_root")
            if not notebook_dir.exists() or not notebook_dir.is_dir():
                payload["denied_reason"] = "notebook_missing"
                payload["errors"] = [{"code": "notebook_missing", "message": f"notebook not found: {notebook}"}]
                return payload
            for kind, dirname in (("note", "notes"), ("source", "sources")):
                record_dir = _resolve_inside(root, notebook_dir / dirname, "record_path_escapes_root")
                if not record_dir.exists():
                    continue
                if not record_dir.is_dir():
                    payload["denied_reason"] = "record_dir_not_directory"
                    payload["errors"] = [{"code": "record_dir_not_directory", "message": f"record directory is not a directory: {notebook}/{dirname}"}]
                    return payload
                for path in sorted(record_dir.glob("*.json")):
                    safe_path = _resolve_inside(root, path, "record_path_escapes_root")
                    with safe_path.open("r", encoding="utf-8") as handle:
                        data = json.load(handle)
                    if not isinstance(data, dict):
                        raise ValueError("record_json_not_object")
                    records.append(_record_metadata(kind, notebook, data))
    except json.JSONDecodeError:
        return _error_payload(root, safe_workspace, safe_notebooks, "malformed_json", "record JSON could not be parsed")
    except ValueError as exc:
        return _error_payload(root, safe_workspace, safe_notebooks, str(exc), "record inventory failed safely")
    except OSError:
        return _error_payload(root, safe_workspace, safe_notebooks, "record_read_failed", "record could not be read")

    records.sort(key=lambda item: (item["notebook"], item["kind"], item["id"]))
    payload["ok"] = True
    payload["record_count"] = len(records)
    payload["records"] = records
    return payload


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only safe metadata inventory for local notebook storage.")
    parser.add_argument("--storage-root", default=DEFAULT_STORAGE_ROOT)
    parser.add_argument("--workspace", default="default")
    parser.add_argument("--notebook", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = inventory_notebook_storage(args.storage_root, args.workspace, args.notebook)
    emit(payload, args.json)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
