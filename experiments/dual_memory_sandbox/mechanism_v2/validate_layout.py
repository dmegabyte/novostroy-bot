#!/usr/bin/env python3
"""Static layout validator for dual memory mechanism-v2.

This validator only reads local design files and writes hash_manifest.json when
the closed layout is valid. It never executes tasks, fixtures, models, scorers,
network calls or production code.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FAMILIES = {"normalize", "cache", "boundary"}
ARMS = {"B0", "M1", "S1"}
TASK_KEYS = {
    "task_id",
    "partition",
    "family_id",
    "task_kind",
    "public_problem",
    "public_artifacts",
    "allowed_check_codes",
    "allowed_advice_codes",
    "forbidden_actions",
}
HOLDOUT_TASK_KEYS = TASK_KEYS | {"route_scope"}
ADVICE_PAYLOAD_KEYS = {
    "payload_id",
    "task_id",
    "arm",
    "task_family",
    "advice_family",
    "schedule_role",
    "entries",
}
ADVICE_ENTRY_KEYS = {"code", "family", "safe_summary"}
LABEL_KEYS = {
    "task_id",
    "family_id",
    "expected_family_route",
    "quality_label_placeholder",
    "blind_scorer_notes",
    "private_expected_check_codes",
    "private_source",
}
RECEIPT_KEYS = {"task_id", "arm", "consulted_advice_codes", "selected_check_codes", "receipt_version"}
AGENT_RESULT_KEYS = {"task_id", "arm", "selected_check_codes", "route_summary", "receipt"}
ORCHESTRATION_CONTRACT_KEYS = {"schema_version", "status", "total_future_runs", "sequence", "first_item_gate", "parent_session_capture", "subagent_prompt_template", "metrics_capture", "no_run_confirmation"}
EXECUTION_BOUNDARY_KEYS = {
    "preparation_only",
    "deny_execution_by_default",
    "agent_packet_source",
    "runs_root_required",
    "workspace_fresh_required",
    "agent_packet_forbidden",
    "agent_packet_files",
}
PUBLIC_FORBIDDEN = {
    "expected_answer",
    "hidden_assertions",
    "root_cause_code",
    "quality_label_placeholder",
    "private_expected_check_codes",
    "safe_synthetic_holdout_placeholder",
    "safe_synthetic_support_only",
    "old fixture",
    "raw old",
}
ROUTE_SCOPE_FORBIDDEN = PUBLIC_FORBIDDEN | {
    "expected",
    "answer",
    "assertion",
    "assertions",
    "label",
    "labels",
    "private",
    "hidden",
    "adv-",
    "check-",
    "selected_expected_route",
    "expected_family_route",
    "private_expected_check_codes",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def public_text() -> str:
    """Return agent-facing public task text only for private-label leak checks."""
    chunks: list[str] = []
    for path in sorted((ROOT / "public").rglob("*")):
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks).lower()


def manifest() -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.name == "hash_manifest.json":
            continue
        result[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate() -> tuple[list[str], dict[str, str]]:
    errors: list[str] = []
    experiment = read_json(ROOT / "experiment.json")
    tasks = read_jsonl(ROOT / "public" / "tasks.jsonl")
    labels = read_jsonl(ROOT / "private" / "labels.jsonl")
    advice_payloads = read_jsonl(ROOT / "private" / "advisory_payloads.jsonl")

    if experiment.get("status") != "PREPARED_NOT_RUN":
        errors.append("experiment status must be PREPARED_NOT_RUN")
    if experiment.get("claim_boundary", {}).get("causal_claim_allowed") is not False:
        errors.append("causal claim must be explicitly disallowed")
    if set(experiment.get("arms", [])) != ARMS:
        errors.append("arms must be exactly B0/M1/S1")
    if set(experiment.get("families", [])) != FAMILIES:
        errors.append("families must be exactly normalize/cache/boundary")

    task_ids = [row.get("task_id") for row in tasks]
    label_ids = [row.get("task_id") for row in labels]
    if len(tasks) != 15 or len(set(task_ids)) != 15:
        errors.append("public tasks must contain 15 unique rows")
    if len(labels) != 15 or set(label_ids) != set(task_ids):
        errors.append("private labels must match public task ids exactly")
    if set(experiment.get("holdout_task_ids", [])) != {t for t in task_ids if "-holdout-" in str(t)}:
        errors.append("holdout ids in experiment must match public holdouts")
    if set(experiment.get("learning_task_ids", [])) != {t for t in task_ids if "-learn-" in str(t)}:
        errors.append("learning ids in experiment must match public learning supports")

    family_counts: dict[str, Counter[str]] = {family: Counter() for family in FAMILIES}
    advice_catalog = experiment.get("advice_catalog", {})
    check_catalog = experiment.get("check_catalog", {})
    receipt_allowlist = experiment.get("receipt_advice_allowlist", {})
    for family in FAMILIES:
        if set(advice_catalog.get(family, [])) == set() or set(check_catalog.get(family, [])) == set():
            errors.append(f"missing advice/check catalog for {family}")

    for row in tasks:
        tid = row.get("task_id")
        family = row.get("family_id")
        if row.get("partition") == "holdout":
            if set(row) != HOLDOUT_TASK_KEYS:
                errors.append(f"task {tid} has non-closed keys")
        elif set(row) != TASK_KEYS:
            errors.append(f"task {tid} has non-closed keys")
        if family not in FAMILIES:
            errors.append(f"task {tid} has bad family")
            continue
        family_counts[family][row.get("partition")] += 1
        expected_partition = "learning" if "-learn-" in str(tid) else "holdout"
        if row.get("partition") != expected_partition:
            errors.append(f"task {tid} partition does not match id")
        if row.get("task_kind") not in {"support", "holdout"}:
            errors.append(f"task {tid} bad task_kind")
        route_scope = row.get("route_scope")
        if row.get("partition") == "holdout":
            if not isinstance(route_scope, str) or len(route_scope.strip()) < 40:
                errors.append(f"task {tid} holdout route_scope must be nonempty public prose")
            else:
                lowered_scope = route_scope.lower()
                if "in scope" not in lowered_scope or "out of scope" not in lowered_scope:
                    errors.append(f"task {tid} route_scope must state in-scope and out-of-scope dimensions")
                for token in ROUTE_SCOPE_FORBIDDEN:
                    if token in lowered_scope:
                        errors.append(f"task {tid} route_scope leaks forbidden/private term: {token}")
        elif route_scope is not None:
            errors.append(f"task {tid} learning card must not define route_scope")
        allowed_advice = row.get("allowed_advice_codes")
        if row.get("partition") == "learning":
            if set(allowed_advice or []) != set(advice_catalog.get(family, [])):
                errors.append(f"task {tid} learning advice codes do not match family catalog")
        else:
            if set((allowed_advice or {}).keys()) != ARMS:
                errors.append(f"task {tid} holdout advice contract must be arm-specific")
            elif allowed_advice != receipt_allowlist.get(tid):
                errors.append(f"task {tid} holdout advice contract must match schedule-derived receipt allowlist")
        if set(row.get("allowed_check_codes", [])) != set(check_catalog.get(family, [])):
            errors.append(f"task {tid} check codes do not match family catalog")
        forbidden = "\n".join(row.get("forbidden_actions", [])).lower()
        for phrase in ("no agent/model/provider/scorer/eval run", "no fixture execution", "no network/vps/production access", "no private labels"):
            if phrase not in forbidden:
                errors.append(f"task {tid} missing boundary phrase {phrase}")

    for family, counts in family_counts.items():
        if counts != Counter({"learning": 3, "holdout": 2}):
            errors.append(f"family {family} must have 3 learning and 2 holdout tasks, got {dict(counts)}")

    for label in labels:
        tid = label.get("task_id")
        family = label.get("family_id")
        if set(label) != LABEL_KEYS:
            errors.append(f"label {tid} has non-closed keys")
        if label.get("private_source") != "safe_synthetic_placeholder":
            errors.append(f"label {tid} must be safe synthetic placeholder")
        if label.get("expected_family_route") != family:
            errors.append(f"label {tid} route/family mismatch")
        if not set(label.get("private_expected_check_codes", [])).issubset(set(check_catalog.get(family, []))):
            errors.append(f"label {tid} uses check outside family catalog")

    schedule = experiment.get("counterbalanced_schedule", [])
    if len(schedule) != 6:
        errors.append("schedule must contain six holdout rows")
    schedule_ids = [row.get("task_id") for row in schedule]
    if set(schedule_ids) != set(experiment.get("holdout_task_ids", [])):
        errors.append("schedule must cover every holdout exactly once")
    position_counts = Counter()
    for row in schedule:
        tid = row.get("task_id")
        family = row.get("family_id")
        order = row.get("arm_order", [])
        if set(order) != ARMS or len(order) != 3:
            errors.append(f"schedule {tid} must include each arm exactly once")
        for position, arm in enumerate(order):
            position_counts[(position, arm)] += 1
        if row.get("m1_advice_family") != family:
            errors.append(f"schedule {tid} M1 advice family must match holdout family")
        if row.get("s1_sham_family") == family or row.get("s1_sham_family") not in FAMILIES:
            errors.append(f"schedule {tid} sham family must be unrelated and valid")
        if set(advice_catalog.get(row.get("m1_advice_family"), [])) & set(advice_catalog.get(row.get("s1_sham_family"), [])):
            errors.append(f"schedule {tid} relevant and sham advice vocabularies must be disjoint")
        expected_allowlist = {
            "B0": [],
            "M1": advice_catalog.get(row.get("m1_advice_family"), []),
            "S1": advice_catalog.get(row.get("s1_sham_family"), []),
        }
        if receipt_allowlist.get(tid) != expected_allowlist:
            errors.append(f"schedule {tid} receipt allowlist must derive exactly from arm families")
    for position in range(3):
        counts = {arm: position_counts[(position, arm)] for arm in ARMS}
        if sorted(counts.values()) != [2, 2, 2]:
            errors.append(f"arm order is not counterbalanced at position {position}: {counts}")

    payload_hash_contract = experiment.get("advisory_payload_manifest", {})
    if payload_hash_contract.get("path") != "private/advisory_payloads.jsonl":
        errors.append("advisory payload manifest path mismatch")
    else:
        actual_payload_hash = sha256_file(ROOT / payload_hash_contract["path"])
        if payload_hash_contract.get("sha256") != actual_payload_hash:
            errors.append("advisory payload manifest hash does not match experiment contract")

    schedule_by_task = {row.get("task_id"): row for row in schedule}
    payload_by_pair: dict[tuple[str, str], dict] = {}
    if len(advice_payloads) != 12:
        errors.append("advisory payload manifest must contain one M1 and one S1 row for each holdout")
    for payload in advice_payloads:
        pid = payload.get("payload_id")
        tid = payload.get("task_id")
        arm = payload.get("arm")
        task_family = payload.get("task_family")
        advice_family = payload.get("advice_family")
        entries = payload.get("entries", [])
        if set(payload) != ADVICE_PAYLOAD_KEYS:
            errors.append(f"payload {pid} has non-closed keys")
        if arm not in {"M1", "S1"}:
            errors.append(f"payload {pid} must be M1 or S1 only; B0 has no payload")
        if (tid, arm) in payload_by_pair:
            errors.append(f"payload duplicate for {tid}/{arm}")
        payload_by_pair[(tid, arm)] = payload
        sched = schedule_by_task.get(tid)
        if not sched:
            errors.append(f"payload {pid} references unscheduled holdout")
            continue
        if task_family != sched.get("family_id"):
            errors.append(f"payload {pid} task family mismatch")
        expected_family = sched.get("m1_advice_family") if arm == "M1" else sched.get("s1_sham_family")
        expected_role = "relevant" if arm == "M1" else "scheduled_unrelated_sham"
        if advice_family != expected_family:
            errors.append(f"payload {pid} advice family mismatch")
        if payload.get("schedule_role") != expected_role:
            errors.append(f"payload {pid} schedule role mismatch")
        if set(code.get("code") for code in entries) != set(receipt_allowlist.get(tid, {}).get(arm, [])):
            errors.append(f"payload {pid} entries must equal arm receipt allowlist")
        if len(entries) != 3:
            errors.append(f"payload {pid} must contain exactly three safe code entries")
        for entry in entries:
            if set(entry) != ADVICE_ENTRY_KEYS:
                errors.append(f"payload {pid} entry has non-closed keys")
            if entry.get("family") != advice_family:
                errors.append(f"payload {pid} entry family mismatch")
            if not entry.get("safe_summary") or any(token in entry.get("safe_summary", "").lower() for token in ("prompt", "thought", "code", "log", "label", "outcome")):
                errors.append(f"payload {pid} entry summary must be safe and non-outcome text")

    for tid, sched in schedule_by_task.items():
        if (tid, "B0") in payload_by_pair:
            errors.append(f"B0 must not have advisory payload for {tid}")
        m1 = payload_by_pair.get((tid, "M1"))
        s1 = payload_by_pair.get((tid, "S1"))
        if not m1 or not s1:
            errors.append(f"missing M1/S1 payload pair for {tid}")
            continue
        if m1.get("advice_family") == s1.get("advice_family"):
            errors.append(f"M1/S1 advice families must be disjoint for {tid}")
        m1_len = sum(len(entry.get("code", "")) for entry in m1.get("entries", []))
        s1_len = sum(len(entry.get("code", "")) for entry in s1.get("entries", []))
        if abs(m1_len - s1_len) > 12:
            errors.append(f"M1/S1 code-length parity outside bound for {tid}: {m1_len} vs {s1_len}")

    receipt = experiment.get("receipt_schema", {})
    if set(receipt.get("closed_keys", [])) != RECEIPT_KEYS:
        errors.append("receipt schema must be closed to the five safe keys")
    if receipt.get("receipt_version") != "mechanism-v2-receipt-1":
        errors.append("receipt version mismatch")
    agent_result_schema = experiment.get("agent_result_schema", {})
    if set(agent_result_schema.get("closed_keys", [])) != AGENT_RESULT_KEYS:
        errors.append("agent result schema must be closed to route-only keys")
    if set(agent_result_schema.get("receipt_keys", [])) != RECEIPT_KEYS:
        errors.append("agent result schema must embed exactly the five safe receipt keys")
    required_agent_forbidden = {"edits", "remediation", "code", "prompt", "hidden_reasoning", "thought", "logs", "tool_args", "tool_output", "private_labels", "outcome", "expected_answer"}
    if set(agent_result_schema.get("forbidden", [])) != required_agent_forbidden:
        errors.append("agent result schema must forbid edits/code/prompt/thought/log/tool/private/outcome fields")
    if experiment.get("outcomes", {}).get("no_composite_metric") is not True:
        errors.append("composite metric must be disabled")
    if experiment.get("outcomes", {}).get("quality_scorer_blind_to_arm") is not True:
        errors.append("quality scorer must be blind to arm")
    if experiment.get("boundaries", {}).get("static_only_now") is not True:
        errors.append("static_only_now boundary must be true")
    if experiment.get("boundaries", {}).get("first_infrastructure_failure") != "stop":
        errors.append("first infrastructure failure must stop")
    for module_name in ("seal_result.py", "blind_route_scorer.py", "aggregate_compare.py", "metrics_collector_v2.py", "orchestration_contract.py", "route_safety.py"):
        text = (ROOT / module_name).read_text(encoding="utf-8")
        if "subprocess" in text or "requests" in text or "socket" in text:
            errors.append(f"{module_name} must remain stdlib static/no-process/no-network")
    test_text = (ROOT / "test_pipeline.py").read_text(encoding="utf-8")
    if "subprocess" in test_text or "requests" in test_text or "socket" in test_text:
        errors.append("test_pipeline.py must use static in-process checks only")

    execution_boundary = experiment.get("execution_boundary", {})
    if set(execution_boundary) != EXECUTION_BOUNDARY_KEYS:
        errors.append("execution boundary must be a closed hard slicing contract")
    if execution_boundary.get("preparation_only") is not True:
        errors.append("execution boundary must remain preparation-only")
    if execution_boundary.get("deny_execution_by_default") is not True:
        errors.append("execution boundary must deny execution by default")
    if execution_boundary.get("agent_packet_source") != "arm_sliced_preparer_only":
        errors.append("agent packet source must be the arm-sliced preparer only")
    if execution_boundary.get("runs_root_required") is not True or execution_boundary.get("workspace_fresh_required") is not True:
        errors.append("run preparation must require explicit runs root and fresh workspace")
    required_forbidden = {"private_labels", "other_arm_payloads", "full_arm_map", "schedule", "hidden_outcomes", "raw_prompt", "thought", "log", "code"}
    if set(execution_boundary.get("agent_packet_forbidden", [])) != required_forbidden:
        errors.append("agent packet forbidden data set must block labels, schedule, raw artifacts and other arms")
    if set(execution_boundary.get("agent_packet_files", [])) != {"agent_packet.json", "run_manifest.json", "RECEIPT_SCHEMA.json"}:
        errors.append("agent packet files must be exactly the safe preparation artifacts")

    orchestration_contract = read_json(ROOT / "orchestration_contract.json")
    if set(orchestration_contract) != ORCHESTRATION_CONTRACT_KEYS:
        errors.append("orchestration contract must be closed")
    if orchestration_contract.get("status") != "contract_only_not_executable" or orchestration_contract.get("total_future_runs") != 18:
        errors.append("orchestration contract must describe exactly 18 future non-executed runs")
    expected_sequence = [
        "prepare_workspace",
        "launch_normal_task_subagent_with_agent_packet_only",
        "capture_parent_and_fresh_session_id",
        "read_only_db_metrics_capture",
        "seal_candidate_with_verified_new_session_id",
        "blind_score_bound_to_sealed_hash",
        "aggregate_only_after_full_coverage",
    ]
    if orchestration_contract.get("sequence") != expected_sequence:
        errors.append("orchestration contract sequence must be prepare→subagent→metrics→seal→score→aggregate")
    prompt_contract = orchestration_contract.get("subagent_prompt_template", {})
    if prompt_contract.get("closed_inputs") != ["agent_packet_json"]:
        errors.append("orchestration prompt must accept only the current agent packet")
    prompt_text = prompt_contract.get("text", "")
    if "{{agent_packet_json}}" not in prompt_text or "AGENT_PACKET_JSON_START" not in prompt_text:
        errors.append("orchestration prompt must include only the current packet placeholder")
    for token in ("private_labels", "other_arm_payloads", "own_outcome", "other_arm_data", "blind_score", "aggregate"):
        if token in prompt_text:
            errors.append(f"orchestration prompt text leaks forbidden token: {token}")
    metrics_capture = orchestration_contract.get("metrics_capture", {})
    if metrics_capture.get("module") != "metrics_collector_v2.py" or metrics_capture.get("read_only_db_path_required") is not True:
        errors.append("orchestration contract must require read-only v2 metrics collector")
    first_gate = orchestration_contract.get("first_item_gate", {})
    if first_gate.get("enabled") is not True or first_gate.get("stop_before_batch_if_first_packet_receipt_score_or_metric_contract_fails") is not True:
        errors.append("orchestration contract must include first item gate")

    pub = public_text()
    for token in PUBLIC_FORBIDDEN:
        if token in pub:
            errors.append(f"public text leaks forbidden token: {token}")
    for label in labels:
        for value in (label.get("quality_label_placeholder", ""), " ".join(label.get("private_expected_check_codes", []))):
            if value and value.lower() in pub:
                errors.append("public text leaks private label value")

    return errors, manifest()


def main() -> int:
    errors, hashes = validate()
    if errors:
        print(json.dumps({"status": "fail", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    (ROOT / "hash_manifest.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "tasks": 15, "holdouts": 6, "learning": 9, "scheduled_runs": 18, "manifest_files": len(hashes)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
