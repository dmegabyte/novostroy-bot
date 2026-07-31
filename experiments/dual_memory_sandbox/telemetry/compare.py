from __future__ import annotations

from statistics import median
from typing import Any


METRIC_FAMILIES = {
    "opencode_usage": ["wall_ms", "total_tokens", "input_tokens", "output_tokens", "tokens_cache_read", "tokens_cache_write", "cached_tokens", "reasoning_tokens", "model_calls", "estimated_provider_cost"],
    "tooling": ["tool_calls", "failed_tool_calls", "retries"],
    "retrieval": ["bytes_read", "lines_read", "chars_read"],
    "memory": ["memory_hint_count", "memory_hint_chars", "memory_hint_tokens"],
}

PRIMARY_FAMILIES = {"opencode_usage", "tooling"}
REQUIRED_FAMILY_COVERAGE = {
    "opencode_usage": {"wall", "tokens", "model_calls"},
    "tooling": {"tools"},
    "retrieval": {"retrieval"},
    "memory": {"memory"},
}
IDENTITY_KEYS = ["task_fingerprint", "fixture_hash", "actual_agent", "actual_model_identity", "parent_id"]


def _not_eval(reason: str) -> dict[str, Any]:
    return {
        "quality_gate": "not_evaluable",
        "overall_resource_claim": "not_evaluable",
        "reason": reason,
        "metric_families": {family: {"claim": "not_evaluable", "missing": [{"reason": reason}], "metrics": {}} for family in METRIC_FAMILIES},
        "paired_deltas": [],
    }


def _quality_fail(reason: str, quality_transitions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "quality_gate": "fail",
        "overall_resource_claim": "not_reported",
        "reason": reason,
        "quality_transitions": quality_transitions or [],
        "metric_families": {},
        "paired_deltas": [],
    }


def _by_task(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["task_id"]: row for row in rows}


def _duplicate_task_id(rows: list[dict[str, Any]]) -> str | None:
    seen: set[str] = set()
    for row in rows:
        task_id = row.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return "missing task_id"
        if task_id in seen:
            return task_id
        seen.add(task_id)
    return None


def _explicit_quality(row: dict[str, Any], task_id: str) -> dict[str, bool] | str:
    quality = row.get("quality")
    required = ["pass", "false_success", "safety_regression", "budget_regression"]
    if not isinstance(quality, dict):
        return f"explicit scorer quality missing for {task_id}"
    for key in required:
        if type(quality.get(key)) is not bool:
            return f"explicit bool quality field missing for {task_id}: {key}"
    return {key: quality[key] for key in required}


def _family_coverage(row: dict[str, Any], family: str) -> set[str]:
    present = set(row.get("coverage", {}).get("present", []))
    families = row.get("coverage", {}).get("families", {})
    if isinstance(families, dict):
        present.update(families.get(family, {}).get("present", []))
    return present


def _aggregate(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {"median_delta": median(values), "total_delta": sum(values), "p95_delta": ordered[max(0, int(0.95 * (len(values) - 1)))]}


def _quality_outcome(baseline_pass: bool, memory_pass: bool) -> str:
    if baseline_pass is False and memory_pass is True:
        return "improved"
    if baseline_pass is True and memory_pass is True:
        return "unchanged_pass"
    if baseline_pass is True and memory_pass is False:
        return "regressed"
    return "still_failed"


def _valid_subagent_pair(br: dict[str, Any], mr: dict[str, Any], task_id: str) -> str | None:
    if not br.get("session_id") or not mr.get("session_id") or br.get("session_id") == mr.get("session_id"):
        return f"baseline/memory require different nonempty session_id for {task_id}"
    if br.get("fresh_subagent_session") is not True or mr.get("fresh_subagent_session") is not True:
        return f"fresh_subagent_session missing for {task_id}"
    parent = br.get("parent_id")
    if not isinstance(parent, str) or not parent or parent != mr.get("parent_id"):
        return f"same nonempty experiment parent_id required for {task_id}"
    if not br.get("actual_agent") or not br.get("actual_model_identity"):
        return f"actual agent/model identity missing for {task_id}"
    return None


def _single_nonempty_cohort_value(rows: list[dict[str, Any]], key: str) -> str | None:
    values = [row.get(key) for row in rows]
    if any(not isinstance(value, str) or not value for value in values):
        return f"cohort requires nonempty {key} for all B0/M1 summaries"
    if len(set(values)) != 1:
        return f"cohort requires one experiment-wide {key} across all B0/M1 summaries"
    return None


def compare_paired_summaries(baseline: list[dict[str, Any]], memory: list[dict[str, Any]], *, expected_holdout_ids: list[str]) -> dict[str, Any]:
    if len(expected_holdout_ids) != 6:
        return _not_eval("expected exactly six holdout ids")
    if len(baseline) != 6 or len(memory) != 6:
        return _not_eval("baseline and memory cohorts must each contain exactly six rows")
    dup = _duplicate_task_id(baseline)
    if dup:
        return _not_eval(f"duplicate baseline task_id: {dup}")
    dup = _duplicate_task_id(memory)
    if dup:
        return _not_eval(f"duplicate memory task_id: {dup}")
    all_sessions = [row.get("session_id") for row in baseline + memory]
    if any(not isinstance(value, str) or not value for value in all_sessions) or len(set(all_sessions)) != len(all_sessions):
        return _not_eval("all child session_id values must be nonempty and globally distinct")
    for key in ["parent_id", "actual_agent", "actual_model_identity"]:
        cohort_error = _single_nonempty_cohort_value(baseline + memory, key)
        if cohort_error:
            return _not_eval(cohort_error)
    b = _by_task(baseline)
    m = _by_task(memory)
    if set(b) != set(expected_holdout_ids) or set(m) != set(expected_holdout_ids):
        return _not_eval("baseline/memory task ids do not match the six holdout ids")

    family_status = {family: {"claim": "evaluable", "missing": [], "metrics": {}} for family in METRIC_FAMILIES}
    paired: list[dict[str, Any]] = []
    quality_transitions: list[dict[str, Any]] = []
    metric_values: dict[str, dict[str, list[float]]] = {family: {metric: [] for metric in metrics} for family, metrics in METRIC_FAMILIES.items()}

    for task_id in expected_holdout_ids:
        br = b[task_id]
        mr = m[task_id]
        if br.get("mode") != "baseline" or mr.get("mode") != "memory":
            return _not_eval(f"bad mode pair for {task_id}")
        if br.get("memory_snapshot_hash") is not None:
            return _not_eval(f"baseline memory_snapshot_hash must be null for {task_id}")
        if not isinstance(mr.get("memory_snapshot_hash"), str) or not mr.get("memory_snapshot_hash"):
            return _not_eval(f"memory run requires nonempty memory_snapshot_hash for {task_id}")
        pair_error = _valid_subagent_pair(br, mr, task_id)
        if pair_error:
            return _not_eval(pair_error)
        for key in IDENTITY_KEYS:
            if br.get(key) != mr.get(key):
                return _not_eval(f"identity mismatch for {task_id}: {key}")
        bq = _explicit_quality(br, task_id)
        mq = _explicit_quality(mr, task_id)
        if isinstance(bq, str):
            return _not_eval(bq)
        if isinstance(mq, str):
            return _not_eval(mq)
        if mq.get("false_success") is True or mq.get("safety_regression") is True or mq.get("budget_regression") is True:
            return _quality_fail(f"quality/safety/budget regression for {task_id}", quality_transitions)
        outcome = _quality_outcome(bq["pass"], mq["pass"])
        quality_transitions.append({"task_id": task_id, "baseline_pass": bq["pass"], "memory_pass": mq["pass"], "outcome": outcome})
        if outcome == "regressed":
            return _quality_fail(f"memory quality regression for {task_id}", quality_transitions)

        task_delta = {"task_id": task_id, "families": {}}
        for family, metrics in METRIC_FAMILIES.items():
            covered = _family_coverage(br, family) & _family_coverage(mr, family)
            missing = REQUIRED_FAMILY_COVERAGE[family] - covered
            if missing:
                family_status[family]["claim"] = "not_evaluable"
                family_status[family]["missing"].append({"task_id": task_id, "coverage": sorted(missing)})
                task_delta["families"][family] = {"claim": "not_evaluable", "missing_coverage": sorted(missing), "deltas": {}}
                continue
            deltas: dict[str, float | None] = {}
            for metric in metrics:
                bv = br.get("resources", {}).get(metric)
                mv = mr.get("resources", {}).get(metric)
                if bv is None or mv is None:
                    deltas[metric] = None
                    family_status[family]["claim"] = "not_evaluable"
                    family_status[family]["missing"].append({"task_id": task_id, "metric": metric})
                elif isinstance(bv, (int, float)) and isinstance(mv, (int, float)):
                    delta = mv - bv
                    deltas[metric] = delta
                    metric_values[family][metric].append(delta)
                else:
                    deltas[metric] = None
                    family_status[family]["claim"] = "not_evaluable"
                    family_status[family]["missing"].append({"task_id": task_id, "metric": metric, "reason": "non_numeric"})
            task_delta["families"][family] = {"claim": "evaluable" if family_status[family]["claim"] == "evaluable" else "partially_or_not_evaluable", "deltas": deltas}
        paired.append(task_delta)

    if not all(item["memory_pass"] for item in quality_transitions):
        return _quality_fail("memory run must pass all six holdouts before any resource claim", quality_transitions)

    for family, metrics in metric_values.items():
        for metric, values in metrics.items():
            family_status[family]["metrics"][metric] = _aggregate(values) if len(values) == 6 else None
            if len(values) != 6:
                family_status[family]["claim"] = "not_evaluable"

    overall = "evaluable" if all(family_status[f]["claim"] == "evaluable" for f in PRIMARY_FAMILIES) else "not_evaluable"
    return {
        "quality_gate": "pass",
        "overall_resource_claim": overall,
        "claim_name": "paired subagent pilot resource comparison",
        "claim_strength": "observational_evidence_not_causal_container_proof",
        "n_pairs": 6,
        "n_caveat": "small paired holdout N=6",
        "quality_transitions": quality_transitions,
        "metric_families": family_status,
        "paired_deltas": paired,
    }
