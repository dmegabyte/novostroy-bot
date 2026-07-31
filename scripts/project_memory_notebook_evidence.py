#!/usr/bin/env python3
"""Local-only explicit evidence scanner for historical NotebookLM records.

The scanner consumes a safe inventory JSON, re-reads the matching local note/source
record bodies only inside the current process, and emits classifier evidence rules
without titles, bodies, snippets, matched tokens, raw paths to records, network
calls, subprocesses, runtime imports, source mutations or NotebookLM/MCP writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


EVIDENCE_SCHEMA = "project_memory_classification_evidence.v1"
INVENTORY_SCHEMA = "project_memory_notebook_inventory.v1"
GENERATOR = "project_memory_notebook_evidence.py"
SCANNER_CONTRACT = "body_sha_verified_exact_owner_path.v1"
STORAGE_SCOPE = "local_notebook_store"
BANNED_INVENTORY_RECORD_KEYS = {"title", "body", "content", "note", "raw", "transcript", "log"}
TREE_SCHEMA = "project_memory_tree.v1"
MARKERS_SCHEMA = "project_memory_ownership_markers.v1"
DEFAULT_TREE = Path(__file__).resolve().parents[1] / "config" / "project_memory_tree.json"
DEFAULT_MARKERS = Path(__file__).resolve().parents[1] / "config" / "project_memory_ownership_markers.json"
ALLOWED_NODE_CLASSES = {"ecosystem", "project", "subproject", "shared"}
ALLOWED_LIFECYCLES = {"historical_not_current"}
ALLOWED_MATCH_TYPES = {"exact_token", "regex"}
LOOSE_FORBIDDEN_TOKENS = {"qapairs", "mpn", "nmbot"}
EVIDENCE_REF_RE = re.compile(r"^(?:/[^:]+|[A-Za-z0-9_.-][A-Za-z0-9_./-]*):\d+-\d+(?:,\d+-\d+)*$")
SENSITIVE_RE = re.compile(
    r"(?is)(-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:api[_-]?key|secret|password|passwd|token|bearer|authorization)\b\s*[:=]|\bsk-[A-Za-z0-9_-]{16,}|\b[A-Za-z0-9_/-]{24,}\.[A-Za-z0-9_/-]{16,}\.[A-Za-z0-9_/-]{16,})"
)


def _read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _base_payload(storage_root: str | Path, workspace: str) -> dict[str, Any]:
    return {
        "schema": EVIDENCE_SCHEMA,
        "ok": False,
        "read_only": True,
        "write_performed": False,
        "notebook_mutation_performed": False,
        "automatic_routing_changed": False,
        "production_verified": False,
        "requires_owner_confirmation": True,
        "body_scanned_internal_only": True,
        "content_printed": False,
        "title_or_notebook_name_matching_used": False,
        "matched_text_printed": False,
        "sensitive_match_details_printed": False,
        "generator": GENERATOR,
        "scanner_contract": SCANNER_CONTRACT,
        "storage_scope": STORAGE_SCOPE,
        "workspace": workspace,
        "inventory_schema": INVENTORY_SCHEMA,
        "inventory_record_count": 0,
        "record_count": 0,
        "rule_count": 0,
        "unresolved_count": 0,
        "sensitive_exclusion_count": 0,
        "sensitive_unresolved_count": 0,
        "ambiguous_count": 0,
        "no_match_count": 0,
        "rules": [],
        "unresolved_summary": {"no_match": 0, "ambiguous_multiple_owners": 0, "sensitive_without_unique_owner": 0},
        "errors": [],
    }


def _error(code: str, message: str, storage_root: str | Path = "", workspace: str = "") -> dict[str, Any]:
    payload = _base_payload(storage_root, workspace)
    payload["denied_reason"] = code
    payload["errors"] = [{"code": code, "message": message}]
    return payload


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


def _record_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (str(record.get("notebook", "")), str(record.get("kind", "")), str(record.get("id", "")))


def _record_sha(record: dict[str, Any]) -> str:
    value = record.get("metadata_sha256", record.get("content_sha256"))
    return value if isinstance(value, str) else ""


def _validate_refs(refs: Any) -> list[str]:
    if not isinstance(refs, list) or not refs:
        raise ValueError("evidence_refs_required")
    out: list[str] = []
    for ref in refs:
        if not isinstance(ref, str) or not EVIDENCE_REF_RE.match(ref):
            raise ValueError("invalid_evidence_ref")
        out.append(ref)
    return out


def _validate_tree(tree: dict[str, Any]) -> dict[str, str]:
    if tree.get("schema") != TREE_SCHEMA:
        raise ValueError("invalid_tree_schema")
    nodes = tree.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("tree_nodes_missing")
    by_path: dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("tree_node_not_object")
        path = node.get("path")
        node_class = node.get("class")
        if not isinstance(path, str) or node_class not in ALLOWED_NODE_CLASSES:
            raise ValueError("invalid_tree_node")
        by_path[path] = node_class
    return by_path


def _validate_inventory(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    if inventory.get("schema") != INVENTORY_SCHEMA:
        raise ValueError("invalid_inventory_schema")
    if inventory.get("ok") is not True or inventory.get("read_only") is not True or inventory.get("write_performed") is not False:
        raise ValueError("inventory_safety_flags_invalid")
    records = inventory.get("records")
    if not isinstance(records, list):
        raise ValueError("inventory_records_missing")
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("inventory_record_not_object")
        forbidden = BANNED_INVENTORY_RECORD_KEYS.intersection(str(key).lower() for key in record)
        if forbidden:
            raise ValueError("inventory_record_contains_unsafe_key")
        key = _record_key(record)
        if not all(key) or key[1] not in {"note", "source"} or not _record_sha(record):
            raise ValueError("inventory_record_missing_identity_or_sha")
        if key in seen:
            raise ValueError("duplicate_inventory_record")
        seen.add(key)
    return records


def _compile_markers(config: dict[str, Any], nodes: dict[str, str]) -> list[dict[str, Any]]:
    if config.get("schema") != MARKERS_SCHEMA:
        raise ValueError("invalid_marker_schema")
    markers = config.get("markers")
    if not isinstance(markers, list):
        raise ValueError("markers_missing")
    seen_ids: set[str] = set()
    compiled: list[dict[str, Any]] = []
    for marker in markers:
        if not isinstance(marker, dict):
            raise ValueError("marker_not_object")
        marker_id = marker.get("marker_id")
        match_type = marker.get("match_type")
        token = marker.get("token")
        owner_path = marker.get("owner_path")
        node_class = marker.get("node_class")
        lifecycle = marker.get("lifecycle")
        if not isinstance(marker_id, str) or not marker_id or marker_id in seen_ids:
            raise ValueError("invalid_or_duplicate_marker_id")
        seen_ids.add(marker_id)
        if match_type not in ALLOWED_MATCH_TYPES:
            raise ValueError("invalid_marker_match_type")
        if not isinstance(token, str) or len(token) < 4 or token.lower() in LOOSE_FORBIDDEN_TOKENS:
            raise ValueError("unsafe_marker_token")
        if owner_path not in nodes or node_class != nodes[owner_path]:
            raise ValueError("marker_owner_path_invalid")
        if lifecycle not in ALLOWED_LIFECYCLES:
            raise ValueError("marker_lifecycle_not_allowed")
        refs = _validate_refs(marker.get("evidence_refs"))
        pattern = None
        if match_type == "regex":
            if not token.startswith("^") or not token.endswith("$"):
                raise ValueError("regex_marker_must_be_anchored")
            pattern = re.compile(token)
        compiled.append({**marker, "evidence_refs": refs, "_pattern": pattern})
    return compiled


def _find_record_path(root: Path, workspace: str, record: dict[str, Any]) -> Path:
    notebook, kind, record_id = _record_key(record)
    safe_workspace = _safe_segment(workspace, "workspace")
    safe_notebook = _safe_segment(notebook, "notebook")
    dirname = "notes" if kind == "note" else "sources"
    record_dir = _resolve_inside(root, root / "workspaces" / safe_workspace / "notebooks" / safe_notebook / dirname, "record_path_escapes_root")
    if not record_dir.exists() or not record_dir.is_dir():
        raise ValueError("record_missing")
    matches: list[Path] = []
    for candidate in sorted(record_dir.glob("*.json")):
        safe_path = _resolve_inside(root, candidate, "record_path_escapes_root")
        with safe_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("record_json_not_object")
        if _record_id(kind, data) == record_id:
            matches.append(safe_path)
    if not matches:
        raise ValueError("record_missing")
    if len(matches) > 1:
        raise ValueError("duplicate_record")
    return matches[0]


def _matched_markers(body: str, markers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for marker in markers:
        if marker["match_type"] == "exact_token":
            ok = marker["token"] in body
        else:
            ok = bool(marker["_pattern"].search(body))
        if ok:
            matched.append(marker)
    return matched


def build_evidence_map(
    inventory: dict[str, Any],
    storage_root: str | Path,
    workspace: str,
    tree: dict[str, Any],
    marker_config: dict[str, Any],
) -> dict[str, Any]:
    try:
        requested_root = Path(storage_root).expanduser()
        if not requested_root.exists() or not requested_root.is_dir():
            raise ValueError("storage_root_missing")
        root = requested_root.resolve()
        safe_workspace = _safe_segment(workspace, "workspace")
        nodes = _validate_tree(tree)
        records = _validate_inventory(inventory)
        markers = _compile_markers(marker_config, nodes)
        _resolve_inside(root, root / "workspaces" / safe_workspace, "workspace_path_escapes_root")
    except ValueError as exc:
        return _error(str(exc), "evidence input failed closed", storage_root, workspace)

    payload = _base_payload(root, safe_workspace)
    rules: list[dict[str, Any]] = []
    no_match = ambiguous = sensitive_unresolved = sensitive_rules = 0
    try:
        for record in records:
            path = _find_record_path(root, safe_workspace, record)
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("record_json_not_object")
            body = _body_for_record(record["kind"], data)
            actual_sha = _sha_body(body)
            expected_sha = _record_sha(record)
            if actual_sha != expected_sha:
                raise ValueError("hash_mismatch")
            sensitive = bool(SENSITIVE_RE.search(body))
            matches = _matched_markers(body, markers)
            owner_paths = {marker["owner_path"] for marker in matches}
            if len(owner_paths) != 1:
                if not owner_paths:
                    no_match += 1
                else:
                    ambiguous += 1
                if sensitive:
                    sensitive_unresolved += 1
                continue
            owner_path = next(iter(owner_paths))
            owner_markers = [marker for marker in matches if marker["owner_path"] == owner_path]
            evidence_refs = sorted({ref for marker in owner_markers for ref in marker["evidence_refs"]})
            lifecycle_values = {marker["lifecycle"] for marker in owner_markers}
            if lifecycle_values != {"historical_not_current"}:
                raise ValueError("ambiguous_lifecycle")
            rule = {
                "notebook": record["notebook"],
                "kind": record["kind"],
                "record_id": record["id"],
                "metadata_sha256": expected_sha,
                "owner_path": owner_path,
                "node_class": nodes[owner_path],
                "lifecycle": "historical_not_current",
                "confidence": "explicit",
                "evidence_refs": evidence_refs,
                "sensitive_exclusion": sensitive,
            }
            if sensitive:
                sensitive_rules += 1
            rules.append(rule)
    except json.JSONDecodeError:
        return _error("malformed_json", "record JSON could not be parsed", root, safe_workspace)
    except OSError:
        return _error("record_read_failed", "record could not be read", root, safe_workspace)
    except ValueError as exc:
        return _error(str(exc), "evidence scan failed closed", root, safe_workspace)

    rules.sort(key=lambda item: (item["notebook"], item["kind"], item["record_id"], item["owner_path"]))
    payload.update(
        {
            "ok": True,
            "inventory_record_count": len(records),
            "record_count": len(records),
            "rule_count": len(rules),
            "unresolved_count": len(records) - len(rules),
            "sensitive_exclusion_count": sensitive_rules,
            "sensitive_unresolved_count": sensitive_unresolved,
            "ambiguous_count": ambiguous,
            "no_match_count": no_match,
            "rules": rules,
            "unresolved_summary": {
                "no_match": no_match,
                "ambiguous_multiple_owners": ambiguous,
                "sensitive_without_unique_owner": sensitive_unresolved,
            },
            "errors": [],
        }
    )
    return payload


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local-only explicit evidence scanner for safe NotebookLM inventory records.")
    parser.add_argument("--inventory", required=True, help="safe inventory JSON path")
    parser.add_argument("--storage-root", required=True, help="local notebook storage root")
    parser.add_argument("--workspace", default="default")
    parser.add_argument("--tree", default=str(DEFAULT_TREE))
    parser.add_argument("--markers", default=str(DEFAULT_MARKERS))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        inventory = _read_json(args.inventory)
        tree = _read_json(args.tree)
        markers = _read_json(args.markers)
        payload = build_evidence_map(inventory, args.storage_root, args.workspace, tree, markers)
    except (OSError, json.JSONDecodeError):
        payload = _error("input_read_failed", "evidence input could not be read", args.storage_root, args.workspace)
    emit(payload, args.json)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
