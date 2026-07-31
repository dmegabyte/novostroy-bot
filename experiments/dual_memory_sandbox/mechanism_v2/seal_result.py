#!/usr/bin/env python3
"""Validate and seal one future mechanism-v2 route-only agent result.

This module never executes agents, models, fixtures, tools or network calls.  It
only validates a JSON candidate against an already prepared arm-sliced workspace
and writes contained JSON artifacts under that workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from route_safety import route_summary_leaks_arm_identity


ROOT = Path(__file__).resolve().parent
ARMS = {"B0", "M1", "S1"}
RESULT_KEYS = {"task_id", "arm", "selected_check_codes", "route_summary", "receipt"}
RECEIPT_KEYS = {"task_id", "arm", "consulted_advice_codes", "selected_check_codes", "receipt_version"}
FORBIDDEN_TEXT = (
    "hidden_reasoning",
    "raw_prompt",
    "raw code",
    "raw_code",
    "tool output",
    "tool_output",
    "private label",
    "private_labels",
    "expected answer",
    "expected_answer",
    "provider_packet",
    "production_data",
    "thought",
    "log output",
)
SAFE_SESSION_ID = re.compile(r"^ses_[A-Za-z0-9_-]{1,96}$")


class SealError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write_new_json(path: Path, obj: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    data = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError as exc:
            raise SealError("sealed_result.json already exists; refusing to overwrite") from exc
        dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if tmp.exists():
            tmp.unlink()


def _workspace_file(workspace: Path, name: str) -> Path:
    root = workspace.resolve()
    target = (root / name).resolve()
    if root != target.parent:
        raise SealError("artifact path escapes run workspace")
    return target


def _closed_str_list(value: Any, *, name: str, allowed: set[str]) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SealError(f"{name} must be a list of strings")
    if len(value) != len(set(value)):
        raise SealError(f"{name} must not contain duplicates")
    if not set(value).issubset(allowed):
        raise SealError(f"{name} contains code outside the current run allowlist")
    return list(value)


def _validate_source_hashes(manifest: dict[str, Any]) -> None:
    expected = manifest.get("source_hashes")
    if not isinstance(expected, dict):
        raise SealError("run manifest misses source hashes")
    experiment = _read_json(ROOT / "experiment.json")
    payload_manifest = experiment.get("advisory_payload_manifest", {})
    if set(payload_manifest) != {"path", "sha256", "immutability"} or payload_manifest.get("path") != "private/advisory_payloads.jsonl":
        raise SealError("experiment advisory payload manifest is not a closed hash lock")
    actual_payload_hash = _sha256(ROOT / "private" / "advisory_payloads.jsonl")
    if payload_manifest.get("sha256") != actual_payload_hash:
        raise SealError("advisory payload artifact drift from experiment manifest")
    actual = {
        "experiment.json": _sha256(ROOT / "experiment.json"),
        "public/tasks.jsonl": _sha256(ROOT / "public" / "tasks.jsonl"),
        "private/advisory_payloads.jsonl": actual_payload_hash,
        "private/labels.jsonl": _sha256(ROOT / "private" / "labels.jsonl"),
    }
    if expected != actual:
        raise SealError("immutable source hash binding mismatch")


def validate_agent_result(candidate: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    if set(candidate) != RESULT_KEYS:
        raise SealError("agent result must use the exact closed route-only schema")
    task_id = manifest.get("task_id")
    arm = manifest.get("arm")
    if candidate.get("task_id") != task_id or candidate.get("arm") != arm or arm not in ARMS:
        raise SealError("candidate task_id/arm does not match prepared run manifest")

    contract = manifest.get("expected_receipt_allowlist", {})
    selected_allowed = set(contract.get("selected_check_codes_allowed", []))
    advice_allowed = set(contract.get("consulted_advice_codes_allowed", []))
    selected = _closed_str_list(candidate.get("selected_check_codes"), name="selected_check_codes", allowed=selected_allowed)
    if not selected:
        raise SealError("selected_check_codes must not be empty")

    summary = candidate.get("route_summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 600:
        raise SealError("route_summary must be brief non-empty natural language")
    lowered = summary.lower()
    if any(token in lowered for token in FORBIDDEN_TEXT):
        raise SealError("route_summary contains forbidden content reference")
    if route_summary_leaks_arm_identity(summary):
        raise SealError("route_summary must not leak arm identity")

    receipt = candidate.get("receipt")
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        raise SealError("receipt must use exactly the existing five safe keys")
    if receipt.get("task_id") != task_id or receipt.get("arm") != arm:
        raise SealError("receipt identity mismatch")
    if receipt.get("receipt_version") != contract.get("receipt_version"):
        raise SealError("receipt version mismatch")
    consulted = _closed_str_list(receipt.get("consulted_advice_codes"), name="consulted_advice_codes", allowed=advice_allowed)
    receipt_selected = _closed_str_list(receipt.get("selected_check_codes"), name="receipt.selected_check_codes", allowed=selected_allowed)
    if receipt_selected != selected:
        raise SealError("receipt selected_check_codes must exactly equal top-level selected_check_codes")
    if consulted != list(contract.get("consulted_advice_codes_allowed", [])):
        raise SealError("receipt consulted_advice_codes must exactly match the scheduled current-arm allowlist")
    return candidate


def seal_candidate(workspace: Path, candidate_path: Path, session_id: str) -> dict[str, Any]:
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise SealError("workspace does not exist")
    if not SAFE_SESSION_ID.fullmatch(session_id):
        raise SealError("session_id must be a safe fresh OpenCode session id")
    manifest = _read_json(_workspace_file(workspace, "run_manifest.json"))
    sealed_path = _workspace_file(workspace, "sealed_result.json")
    if sealed_path.exists():
        raise SealError("sealed_result.json already exists; refusing to overwrite")
    _validate_source_hashes(manifest)
    manifest_sha256 = _sha256(_workspace_file(workspace, "run_manifest.json"))
    candidate_resolved = candidate_path.resolve()
    candidate = _read_json(candidate_resolved)
    validated = validate_agent_result(candidate, manifest)
    now_ms = int(time.time() * 1000)
    sealed = {
        "schema_version": 1,
        "status": "sealed_route_only_result",
        "fresh_session_id": session_id,
        "task_id": manifest["task_id"],
        "arm": manifest["arm"],
        "run_identity": manifest["run_identity"],
        "agent_result": validated,
        "source_hashes": manifest["source_hashes"],
        "binding": {
            "candidate_sha256": _sha256(candidate_resolved),
            "run_manifest_sha256": manifest_sha256,
            "source_hashes": manifest["source_hashes"],
        },
        "diagnostics": {"sealed_at_ms": now_ms},
        "execution_allowed": False,
    }
    _atomic_write_new_json(sealed_path, sealed)
    return sealed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seal one route-only mechanism-v2 JSON result; no execution.")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args(argv)
    try:
        sealed = seal_candidate(args.workspace, args.candidate, args.session_id)
        print(json.dumps({"status": sealed["status"], "task_id": sealed["task_id"], "arm": sealed["arm"]}, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
