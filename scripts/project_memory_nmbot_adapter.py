#!/usr/bin/env python3
"""NMBot passive project-memory adapter dry-run.

This adapter reads only local registry/policy/outcome metadata and reports the
planned passive shadow mechanics. It never invokes context gates automatically,
changes runtime behavior, writes notebooks, mutates outcome stores, calls network
or imports project runtime modules.
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

from project_memory_outcomes import DEFAULT_STORE, list_outcomes, load_policy, validate_bank_snapshot
from project_memory_registry import load_registry, resolve_project


ADAPTER_SCHEMA = "project_memory_nmbot_adapter.v1"
DEFAULT_POLICY_VERSION = "nmbot-passive-v1"


def _policy_row(policy: dict[str, Any], project_id: str, policy_version: str) -> dict[str, Any] | None:
    for row in policy.get("policies", []):
        if isinstance(row, dict) and row.get("project_id") == project_id and row.get("policy_version") == policy_version:
            return row
    return None


def build_nmbot_adapter_report(
    registry_path: str | Path | None = None,
    policy_path: str | Path | None = None,
    store: str | Path = DEFAULT_STORE,
    policy_version: str = DEFAULT_POLICY_VERSION,
) -> dict[str, Any]:
    """Build a passive shadow report for NMBot without side effects."""

    registry = load_registry(registry_path)
    resolved = resolve_project(registry, "nmbot")
    if not resolved.get("ok"):
        return {
            "schema": ADAPTER_SCHEMA,
            "ok": False,
            "dry_run": True,
            "project_id": "nmbot",
            "denied_reason": resolved.get("denied_reason", "project_not_routable"),
            "behavior_change_performed": False,
            "automatic_gate_invocation": False,
            "write_performed": False,
        }

    policy = load_policy(policy_path or "config/project_memory_policy_bundles.json")
    row = _policy_row(policy, "nmbot", policy_version)
    if row is None:
        return {
            "schema": ADAPTER_SCHEMA,
            "ok": False,
            "dry_run": True,
            "project_id": "nmbot",
            "denied_reason": "policy_version_denied",
            "policy_version": policy_version,
            "behavior_change_performed": False,
            "automatic_gate_invocation": False,
            "write_performed": False,
        }

    listed = list_outcomes("nmbot", store, limit=10_000)
    records = listed.get("records", []) if listed.get("ok") else []
    included = [item["outcome_id"] for item in records if item.get("result") == "passed" and item.get("gate_status") == "pass"]
    excluded = [item["outcome_id"] for item in records if item.get("result") in {"failed", "invalid"}]
    snapshot = {
        "schema": "bank_snapshot.v1",
        "bank_snapshot_id": f"bank:nmbot:{policy_version}:dry-run",
        "project_id": "nmbot",
        "policy_version": policy_version,
        "included_outcome_ids": included,
        "excluded_failed_invalid_ids": excluded,
        "frozen_at": "2026-07-26-local-dry-run",
        "scorer_owner_tbd": "TBD",
    }
    snapshot_validation = validate_bank_snapshot(snapshot)
    return {
        "schema": ADAPTER_SCHEMA,
        "ok": bool(listed.get("ok") and snapshot_validation.get("valid")),
        "dry_run": True,
        "project_id": "nmbot",
        "canonical_notebook": resolved["canonical_notebook"],
        "policy_version": policy_version,
        "policy_delta": row["policy_delta"],
        "bank_snapshot": snapshot,
        "bank_snapshot_validation": snapshot_validation,
        "passive_shadow_only": True,
        "automatic_gate_invocation": False,
        "outcome_store_layer": "Layer A append-only privacy-safe outcomes",
        "outcome_write_performed": False,
        "behavior_change_performed": False,
        "runtime_prompt_provider_model_network_changed": False,
        "context_gate_integration": "not_invoked_by_adapter",
        "record_count_seen": len(records),
    }


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run NMBot passive project-memory adapter.")
    parser.add_argument("--registry", default=None, help="repo-relative project registry path")
    parser.add_argument("--policy", default=None, help="repo-relative policy bundle path")
    parser.add_argument("--store", default=str(DEFAULT_STORE), help="repo-relative outcome JSONL store")
    parser.add_argument("--policy-version", default=DEFAULT_POLICY_VERSION)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = build_nmbot_adapter_report(args.registry, args.policy, args.store, args.policy_version)
    except Exception as exc:
        payload = {
            "schema": ADAPTER_SCHEMA,
            "ok": False,
            "dry_run": True,
            "project_id": "nmbot",
            "denied_reason": "adapter_failed_closed",
            "errors": [{"code": "adapter_failed_closed", "message": str(exc)}],
            "behavior_change_performed": False,
            "automatic_gate_invocation": False,
            "write_performed": False,
        }
    emit(payload, args.json)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
