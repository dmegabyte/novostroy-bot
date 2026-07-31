#!/usr/bin/env python3
"""Static contract helpers for a future Chati mechanism-v2 orchestrator.

This file validates packet/prompt shape only. It never launches an agent,
scenario, model, provider, eval, network request or fixture.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "orchestration_contract.json"
FORBIDDEN_PROMPT_TOKENS = ("private_labels", "other_arm_payloads", "own_outcome", "other_arm_data", "blind_score", "aggregate", "experiment_json", "counterbalanced_schedule")


class OrchestrationContractError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_contract(contract: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = contract or _read_json(CONTRACT_PATH)
    if set(contract) != {"schema_version", "status", "total_future_runs", "sequence", "first_item_gate", "parent_session_capture", "subagent_prompt_template", "metrics_capture", "no_run_confirmation"}:
        raise OrchestrationContractError("orchestration contract must be closed")
    if contract.get("schema_version") != 1 or contract.get("status") != "contract_only_not_executable" or contract.get("total_future_runs") != 18:
        raise OrchestrationContractError("orchestration contract status/schema/run count mismatch")
    expected_sequence = [
        "prepare_workspace",
        "launch_normal_task_subagent_with_agent_packet_only",
        "capture_parent_and_fresh_session_id",
        "read_only_db_metrics_capture",
        "seal_candidate_with_verified_new_session_id",
        "blind_score_bound_to_sealed_hash",
        "aggregate_only_after_full_coverage",
    ]
    if contract.get("sequence") != expected_sequence:
        raise OrchestrationContractError("orchestration sequence mismatch")
    gate = contract.get("first_item_gate")
    if not isinstance(gate, dict) or gate.get("enabled") is not True or gate.get("stop_before_batch_if_first_packet_receipt_score_or_metric_contract_fails") is not True:
        raise OrchestrationContractError("first item gate must be explicit")
    parent = contract.get("parent_session_capture")
    if not isinstance(parent, dict) or any(parent.get(key) is not True for key in ("required", "common_parent_id_required_for_all_18", "fresh_subagent_session_id_required")):
        raise OrchestrationContractError("parent/session capture contract must be explicit")
    metrics = contract.get("metrics_capture")
    if not isinstance(metrics, dict) or metrics.get("module") != "metrics_collector_v2.py" or metrics.get("read_only_db_path_required") is not True:
        raise OrchestrationContractError("metrics capture contract mismatch")
    prompt = contract.get("subagent_prompt_template")
    if not isinstance(prompt, dict) or prompt.get("closed_inputs") != ["agent_packet_json"]:
        raise OrchestrationContractError("prompt must accept only current agent packet")
    if "{{agent_packet_json}}" not in prompt.get("text", ""):
        raise OrchestrationContractError("prompt template must include the packet placeholder")
    return contract


def render_subagent_prompt(agent_packet: dict[str, Any], contract: dict[str, Any] | None = None) -> str:
    contract = validate_contract(contract)
    packet_text = json.dumps(agent_packet, ensure_ascii=False, sort_keys=True)
    prompt = contract["subagent_prompt_template"]["text"].replace("{{agent_packet_json}}", packet_text)
    outside_packet = prompt.replace(packet_text, "")
    for token in FORBIDDEN_PROMPT_TOKENS:
        if token in outside_packet and token not in contract["subagent_prompt_template"].get("forbidden_inputs", []):
            raise OrchestrationContractError("prompt includes forbidden data outside the current packet")
    return prompt


def validate_workspace_packet(workspace: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    packet_path = workspace / "agent_packet.json"
    manifest_path = workspace / "run_manifest.json"
    if not packet_path.is_file() or not manifest_path.is_file():
        raise OrchestrationContractError("workspace misses prepared packet/manifest")
    packet = _read_json(packet_path)
    manifest = _read_json(manifest_path)
    if manifest.get("agent_packet") != "agent_packet.json" or manifest.get("execution_allowed") is not False:
        raise OrchestrationContractError("workspace manifest is not a prepared no-run packet")
    task = packet.get("task", {})
    if task.get("task_id") != manifest.get("task_id") or task.get("arm") != manifest.get("arm"):
        raise OrchestrationContractError("packet identity mismatch")
    render_subagent_prompt(packet)
    return {"status": "valid_contract_only", "task_id": manifest["task_id"], "arm": manifest["arm"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate mechanism-v2 future orchestration contract only")
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate_workspace_packet(args.workspace) if args.workspace else {"status": validate_contract()["status"]}
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
