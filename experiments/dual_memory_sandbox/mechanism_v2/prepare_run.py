#!/usr/bin/env python3
"""Prepare one arm-sliced mechanism-v2 launch workspace.

This module is intentionally preparation-only.  It materializes safe JSON for a
future task-subagent prompt, but it never starts an agent, model, provider,
fixture, scorer, eval, network call or production path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
ARMS = {"B0", "M1", "S1"}
SAFE_TASK_KEYS = {"task_id", "family_id", "task_kind", "public_problem", "route_scope", "public_artifacts", "allowed_check_codes", "forbidden_actions"}
SAFE_PAYLOAD_KEYS = {"payload_id", "task_id", "arm", "task_family", "advice_family", "schedule_role", "entries"}
SAFE_ENTRY_KEYS = {"code", "family", "safe_summary"}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.=-]+$")


class PrepareError(ValueError):
    """Raised when a run cannot be safely prepared."""


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_safe_runs_root(path: Path) -> Path:
    if not path.is_absolute():
        raise PrepareError("runs_root must be an explicit absolute path")
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise PrepareError("runs_root must be outside the immutable mechanism_v2 source tree")
    resolved.mkdir(parents=True, exist_ok=True)
    if not resolved.is_dir():
        raise PrepareError("runs_root is not a directory")
    return resolved


def _validate_payload_lock(experiment: dict[str, Any]) -> Path:
    manifest = experiment.get("advisory_payload_manifest", {})
    if set(manifest) != {"path", "sha256", "immutability"}:
        raise PrepareError("advisory payload manifest must be a closed hash-lock object")
    if manifest.get("path") != "private/advisory_payloads.jsonl":
        raise PrepareError("advisory payload path is not the immutable v2 payload source")
    payload_path = ROOT / manifest["path"]
    if _sha256(payload_path) != manifest.get("sha256"):
        raise PrepareError("advisory payload hash mismatch")
    return payload_path


def _safe_task_slice(task: dict[str, Any], arm: str, allowlist: list[str]) -> dict[str, Any]:
    if set(task) - (SAFE_TASK_KEYS | {"partition", "allowed_advice_codes"}):
        raise PrepareError(f"task {task.get('task_id')} has unsafe keys")
    if task.get("partition") != "holdout":
        raise PrepareError("only holdout tasks can be prepared for B0/M1/S1 runs")
    result = {key: task[key] for key in SAFE_TASK_KEYS}
    result["public_artifacts"] = [
        "data-only holdout card; selected arm is specified in this sliced packet"
        if isinstance(item, str) and "future arm instruction" in item
        else item
        for item in result["public_artifacts"]
    ]
    result["arm"] = arm
    if arm != "B0":
        result["receipt_consulted_advice_codes_allowed"] = list(allowlist)
    return result


def _safe_payload_slice(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != SAFE_PAYLOAD_KEYS:
        raise PrepareError("advisory payload has unsafe keys")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise PrepareError("advisory payload entries must be a list")
    safe_entries = []
    for entry in entries:
        if set(entry) != SAFE_ENTRY_KEYS:
            raise PrepareError("advisory payload entry has unsafe keys")
        safe_entries.append({key: entry[key] for key in ("code", "family", "safe_summary")})
    return {
        "payload_id": payload["payload_id"],
        "arm": payload["arm"],
        "advice_family": payload["advice_family"],
        "schedule_role": payload["schedule_role"],
        "entries": safe_entries,
    }


def build_packet(task_id: str, arm: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if arm not in ARMS:
        raise PrepareError("arm must be exactly one of B0, M1 or S1")

    experiment = _read_json(ROOT / "experiment.json")
    if experiment.get("status") != "PREPARED_NOT_RUN":
        raise PrepareError("mechanism_v2 must remain PREPARED_NOT_RUN")
    boundary = experiment.get("execution_boundary", {})
    if boundary.get("preparation_only") is not True or boundary.get("deny_execution_by_default") is not True:
        raise PrepareError("execution boundary must be preparation-only and deny execution")
    if boundary.get("agent_packet_source") != "arm_sliced_preparer_only":
        raise PrepareError("agent packets must come from the arm-sliced preparer")
    payload_path = _validate_payload_lock(experiment)

    tasks = {row["task_id"]: row for row in _read_jsonl(ROOT / "public" / "tasks.jsonl")}
    task = tasks.get(task_id)
    if not task:
        raise PrepareError("unknown task_id")
    if task_id not in set(experiment.get("holdout_task_ids", [])):
        raise PrepareError("task_id is not a scheduled holdout")

    allow_map = experiment.get("receipt_advice_allowlist", {}).get(task_id)
    if set(allow_map or {}) != ARMS:
        raise PrepareError("missing closed arm receipt allowlist for task")
    current_allowlist = list(allow_map[arm])

    payloads = {(row["task_id"], row["arm"]): row for row in _read_jsonl(payload_path)}
    payload = None
    if arm == "B0":
        if (task_id, "B0") in payloads or current_allowlist:
            raise PrepareError("B0 must have no payload and no advice allowlist")
    else:
        payload = payloads.get((task_id, arm))
        if payload is None:
            raise PrepareError(f"missing advisory payload for {task_id}/{arm}")
        if [entry["code"] for entry in payload["entries"]] != current_allowlist:
            raise PrepareError("payload entries do not match current-arm allowlist")

    safe_task = _safe_task_slice(task, arm, current_allowlist)
    safe_payload = _safe_payload_slice(payload) if payload else None
    packet: dict[str, Any] = {
        "packet_version": "mechanism-v2-agent-packet-1",
        "experiment_id": experiment["experiment_id"],
        "task": safe_task,
        "advisory_payload": safe_payload,
        "receipt_contract": {
            "receipt_version": experiment["receipt_schema"]["receipt_version"],
            "closed_keys": list(experiment["receipt_schema"]["closed_keys"]),
            "consulted_advice_codes_allowed": current_allowlist,
            "selected_check_codes_allowed": list(task["allowed_check_codes"]),
        },
        "preparation_boundary": {
            "status": "PREPARED_NOT_RUN",
            "execution_allowed": False,
            "forbidden_now": list(experiment["boundaries"]["forbidden_now"]),
        },
    }
    manifest: dict[str, Any] = {
        "manifest_version": "mechanism-v2-run-manifest-1",
        "experiment_id": experiment["experiment_id"],
        "task_id": task_id,
        "arm": arm,
        "task_family": task["family_id"],
        "run_identity": f"{task_id}--{arm}",
        "agent_packet": "agent_packet.json",
        "expected_receipt_allowlist": packet["receipt_contract"],
        "source_hashes": {
            "experiment.json": _sha256(ROOT / "experiment.json"),
            "public/tasks.jsonl": _sha256(ROOT / "public" / "tasks.jsonl"),
            "private/advisory_payloads.jsonl": experiment["advisory_payload_manifest"]["sha256"],
            "private/labels.jsonl": _sha256(ROOT / "private" / "labels.jsonl"),
        },
        "slicing_guarantee": "agent_packet contains only the selected task and selected arm payload; full arm map, schedule, labels and other-arm payloads are excluded",
        "execution_allowed": False,
    }
    return packet, manifest


def prepare_workspace(task_id: str, arm: str, runs_root: Path, run_id: str | None = None) -> Path:
    if run_id is None:
        run_id = f"{task_id}--{arm}"
    if not RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise PrepareError("run_id must be a simple safe name")
    root = _ensure_safe_runs_root(runs_root)
    workspace = root / run_id
    if workspace.exists():
        raise PrepareError("run workspace already exists; refusing to overwrite")
    packet, manifest = build_packet(task_id, arm)
    workspace.mkdir(mode=0o700)
    (workspace / "agent_packet.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (workspace / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (workspace / "RECEIPT_SCHEMA.json").write_text(json.dumps(packet["receipt_contract"], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for path in workspace.iterdir():
        path.chmod(0o600)
    workspace.chmod(0o700)
    return workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare one arm-sliced mechanism-v2 workspace; execution is not supported.")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--arm", required=True, choices=sorted(ARMS))
    parser.add_argument("--runs-root", required=True, type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    try:
        workspace = prepare_workspace(args.task_id, args.arm, args.runs_root, args.run_id)
    except PrepareError as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "prepared_not_run", "workspace": str(workspace)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
