#!/usr/bin/env python3
"""Fail-closed classifier for safe NotebookLM inventory metadata.

The classifier consumes only a prebuilt safe inventory JSON and explicit evidence
rules. It never calls NotebookLM/MCP clients, network APIs, subprocesses, runtime
code or source storage writers, and it never emits titles, bodies, raw logs,
transcripts or secret values.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


MANIFEST_SCHEMA = "project_memory_notebook_classification_manifest.v1"
TREE_SCHEMA = "project_memory_tree.v1"
EVIDENCE_SCHEMA = "project_memory_classification_evidence.v1"
INVENTORY_SCHEMA = "project_memory_notebook_inventory.v1"
EVIDENCE_GENERATOR = "project_memory_notebook_evidence.py"
SCANNER_CONTRACT = "body_sha_verified_exact_owner_path.v1"

DEFAULT_TREE = Path(__file__).resolve().parents[1] / "config" / "project_memory_tree.json"
SAFE_FLAGS = {
    "read_only": True,
    "write_performed": False,
    "notebook_mutation_performed": False,
    "automatic_routing_changed": False,
    "production_verified": False,
    "requires_owner_confirmation": True,
}
EVIDENCE_SCAN_FLAGS = {
    **SAFE_FLAGS,
    "content_printed": False,
    "title_or_notebook_name_matching_used": False,
    "matched_text_printed": False,
    "sensitive_match_details_printed": False,
    "body_scanned_internal_only": True,
}
ALLOWED_NODE_CLASSES = {"ecosystem", "project", "subproject", "shared"}
ALLOWED_RULE_LIFECYCLES = {"historical_not_current", "production_historical_unverified"}
ALLOWED_RULE_CONFIDENCE = {"explicit"}
BANNED_OUTPUT_KEYS = {"title", "body", "content", "note", "raw", "transcript", "log"}
EVIDENCE_REF_RE = re.compile(r"^(?:/[^:]+|[A-Za-z0-9_.-][A-Za-z0-9_./-]*):\d+-\d+(?:,\d+-\d+)*$")
PLACEHOLDER_REF_PREFIXES = ("user_context:", "memory:", "model_assertion:", "fixture:", "placeholder:")


def _read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _sha(record: dict[str, Any]) -> str:
    value = record.get("metadata_sha256", record.get("content_sha256"))
    return value if isinstance(value, str) else ""


def _record_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (str(record.get("notebook", "")), str(record.get("kind", "")), str(record.get("id", "")))


def _rule_key(rule: dict[str, Any]) -> tuple[str, str, str]:
    return (str(rule.get("notebook", "")), str(rule.get("kind", rule.get("record_kind", ""))), str(rule.get("record_id", "")))


def _base_manifest() -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "ok": False,
        **SAFE_FLAGS,
        "record_count": 0,
        "classified_count": 0,
        "unresolved_count": 0,
        "sensitive_exclusion_count": 0,
        "records": [],
        "errors": [],
    }


def _error(code: str, message: str) -> dict[str, Any]:
    payload = _base_manifest()
    payload["denied_reason"] = code
    payload["errors"] = [{"code": code, "message": message}]
    return payload


def _ensure_no_leaky_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in BANNED_OUTPUT_KEYS:
                raise ValueError(f"prohibited_output_key:{path}.{key}")
            _ensure_no_leaky_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _ensure_no_leaky_keys(child, f"{path}[{index}]")


def _validate_evidence_refs(evidence_refs: Any) -> None:
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise ValueError("evidence_refs_required")
    for ref in evidence_refs:
        if not isinstance(ref, str) or not ref:
            raise ValueError("invalid_evidence_ref")
        lowered = ref.lower()
        if lowered.startswith(PLACEHOLDER_REF_PREFIXES):
            raise ValueError("placeholder_evidence_ref_forbidden")
        if not EVIDENCE_REF_RE.match(ref):
            raise ValueError("evidence_ref_must_be_source_line_range")


def validate_tree(tree: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if tree.get("schema") != TREE_SCHEMA:
        raise ValueError("invalid_tree_schema")
    for key, expected in SAFE_FLAGS.items():
        if tree.get(key) is not expected:
            raise ValueError(f"tree_flag_mismatch:{key}")
    if tree.get("human_owner") != "TBD" or tree.get("rollback_owner") != "TBD":
        raise ValueError("tree_owner_must_remain_tbd")
    nodes = tree.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("tree_nodes_missing")
    by_path: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("tree_node_not_object")
        path = node.get("path")
        node_class = node.get("class")
        if not isinstance(path, str) or not path.startswith("ecosystem/"):
            raise ValueError("invalid_node_path")
        if path in by_path:
            raise ValueError("duplicate_node_path")
        if node_class not in ALLOWED_NODE_CLASSES:
            raise ValueError("invalid_node_class")
        _validate_evidence_refs(node.get("evidence_refs"))
        by_path[path] = node
    return by_path


def _validate_inventory(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    if inventory.get("schema") != INVENTORY_SCHEMA:
        raise ValueError("invalid_inventory_schema")
    if inventory.get("ok") is not True:
        raise ValueError("inventory_not_ok")
    if inventory.get("read_only") is not True or inventory.get("write_performed") is not False:
        raise ValueError("inventory_safety_flags_invalid")
    records = inventory.get("records")
    if not isinstance(records, list):
        raise ValueError("inventory_records_missing")
    if not isinstance(inventory.get("record_count"), int) or inventory.get("record_count") < 0 or inventory.get("record_count") != len(records):
        raise ValueError("inventory_record_count_mismatch")
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("inventory_record_not_object")
        forbidden = BANNED_OUTPUT_KEYS.intersection(str(item).lower() for item in record)
        if forbidden:
            raise ValueError("inventory_record_contains_unsafe_key")
        key = _record_key(record)
        if not all(key) or not _sha(record):
            raise ValueError("inventory_record_missing_identity_or_sha")
        if key in seen:
            raise ValueError("duplicate_inventory_record")
        seen.add(key)
    return records


def _nonnegative_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"evidence_counter_invalid:{name}")
    return value


def _validate_evidence_map_contract(evidence_map: dict[str, Any], inventory_record_count: int) -> list[dict[str, Any]]:
    if evidence_map.get("schema") != EVIDENCE_SCHEMA:
        raise ValueError("invalid_evidence_schema")
    if evidence_map.get("ok") is not True:
        raise ValueError("evidence_not_ok")
    for key, expected in EVIDENCE_SCAN_FLAGS.items():
        if evidence_map.get(key) is not expected:
            raise ValueError(f"evidence_flag_mismatch:{key}")
    if evidence_map.get("generator") != EVIDENCE_GENERATOR:
        raise ValueError("evidence_generator_mismatch")
    if evidence_map.get("scanner_contract") != SCANNER_CONTRACT:
        raise ValueError("evidence_scanner_contract_mismatch")
    if evidence_map.get("inventory_schema") != INVENTORY_SCHEMA:
        raise ValueError("evidence_inventory_schema_mismatch")
    inventory_count = _nonnegative_int(evidence_map.get("inventory_record_count"), "inventory_record_count")
    record_count = _nonnegative_int(evidence_map.get("record_count"), "record_count")
    rule_count = _nonnegative_int(evidence_map.get("rule_count"), "rule_count")
    unresolved_count = _nonnegative_int(evidence_map.get("unresolved_count"), "unresolved_count")
    sensitive_exclusion_count = _nonnegative_int(evidence_map.get("sensitive_exclusion_count"), "sensitive_exclusion_count")
    sensitive_unresolved_count = _nonnegative_int(evidence_map.get("sensitive_unresolved_count"), "sensitive_unresolved_count")
    ambiguous_count = _nonnegative_int(evidence_map.get("ambiguous_count"), "ambiguous_count")
    no_match_count = _nonnegative_int(evidence_map.get("no_match_count"), "no_match_count")
    rules = evidence_map.get("rules")
    if not isinstance(rules, list):
        raise ValueError("evidence_rules_not_list")
    if inventory_count != inventory_record_count or record_count != inventory_record_count:
        raise ValueError("evidence_inventory_record_count_mismatch")
    if rule_count != len(rules):
        raise ValueError("evidence_rule_count_mismatch")
    if unresolved_count != record_count - rule_count:
        raise ValueError("evidence_unresolved_count_mismatch")
    if rule_count > record_count or sensitive_exclusion_count > rule_count or sensitive_unresolved_count > unresolved_count:
        raise ValueError("evidence_counter_logically_inconsistent")
    if ambiguous_count + no_match_count > unresolved_count:
        raise ValueError("evidence_unresolved_breakdown_inconsistent")
    summary = evidence_map.get("unresolved_summary")
    if not isinstance(summary, dict):
        raise ValueError("evidence_unresolved_summary_missing")
    if summary.get("no_match") != no_match_count or summary.get("ambiguous_multiple_owners") != ambiguous_count or summary.get("sensitive_without_unique_owner") != sensitive_unresolved_count:
        raise ValueError("evidence_unresolved_summary_mismatch")
    return rules


def _load_rules(evidence_map: dict[str, Any] | None, nodes: dict[str, dict[str, Any]], inventory_record_count: int) -> dict[tuple[str, str, str], dict[str, Any]]:
    if evidence_map is None:
        return {}
    rules = _validate_evidence_map_contract(evidence_map, inventory_record_count)
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("evidence_rule_not_object")
        key = _rule_key(rule)
        if not all(key):
            raise ValueError("rule_missing_record_identity")
        if key in by_key:
            raise ValueError("duplicate_evidence_rule")
        owner_path = rule.get("owner_path")
        if owner_path not in nodes:
            raise ValueError("invalid_rule_owner_path")
        if rule.get("node_class") != nodes[owner_path]["class"]:
            raise ValueError("rule_node_class_mismatch")
        if rule.get("lifecycle") not in ALLOWED_RULE_LIFECYCLES:
            raise ValueError("rule_lifecycle_not_allowed")
        if rule.get("confidence") not in ALLOWED_RULE_CONFIDENCE:
            raise ValueError("rule_confidence_not_explicit")
        if not isinstance(rule.get("metadata_sha256"), str) or not rule["metadata_sha256"]:
            raise ValueError("rule_metadata_sha_required")
        _validate_evidence_refs(rule.get("evidence_refs"))
        if rule.get("sensitive_exclusion") is not None and not isinstance(rule.get("sensitive_exclusion"), bool):
            raise ValueError("rule_sensitive_exclusion_must_be_bool")
        by_key[key] = rule
    return by_key


def classify_inventory(inventory: dict[str, Any], tree: dict[str, Any], evidence_map: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a stable, privacy-safe classification manifest."""

    try:
        nodes = validate_tree(tree)
        records = _validate_inventory(inventory)
        rules = _load_rules(evidence_map, nodes, len(records))
    except ValueError as exc:
        return _error(str(exc), "classification input failed closed")

    output_records: list[dict[str, Any]] = []
    classified = 0
    sensitive = 0
    for record in records:
        key = _record_key(record)
        rule = rules.get(key)
        item = {
            "record_ref": {"notebook": key[0], "kind": key[1], "id": key[2]},
            "metadata_sha256": _sha(record),
            "owner_path": None,
            "node_class": "unresolved",
            "lifecycle": "unresolved",
            "confidence": "unresolved",
            "evidence_refs": [],
            "sensitive_exclusion": False,
        }
        if rule is not None and rule["metadata_sha256"] == _sha(record):
            item.update(
                {
                    "owner_path": rule["owner_path"],
                    "node_class": rule["node_class"],
                    "lifecycle": rule["lifecycle"],
                    "confidence": "explicit",
                    "evidence_refs": list(rule["evidence_refs"]),
                    "sensitive_exclusion": bool(rule.get("sensitive_exclusion", False)),
                }
            )
            classified += 1
            if item["sensitive_exclusion"]:
                sensitive += 1
        output_records.append(item)

    output_records.sort(key=lambda item: (item["record_ref"]["notebook"], item["record_ref"]["kind"], item["record_ref"]["id"]))
    payload = _base_manifest()
    payload.update(
        {
            "ok": True,
            "record_count": len(output_records),
            "classified_count": classified,
            "unresolved_count": len(output_records) - classified,
            "sensitive_exclusion_count": sensitive,
            "records": output_records,
            "errors": [],
        }
    )
    try:
        _ensure_no_leaky_keys(payload)
    except ValueError as exc:
        return _error(str(exc), "classification output blocked unsafe field")
    return payload


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed classifier for safe NotebookLM inventory metadata.")
    parser.add_argument("--inventory", required=True, help="safe inventory JSON path")
    parser.add_argument("--tree", default=str(DEFAULT_TREE), help="project memory tree JSON path")
    parser.add_argument("--evidence-map", default=None, help="explicit classification evidence JSON path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        inventory = _read_json(args.inventory)
        tree = _read_json(args.tree)
        evidence = _read_json(args.evidence_map) if args.evidence_map else None
        payload = classify_inventory(inventory, tree, evidence)
    except (OSError, json.JSONDecodeError):
        payload = _error("input_read_failed", "classification input could not be read")
    emit(payload, args.json)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
