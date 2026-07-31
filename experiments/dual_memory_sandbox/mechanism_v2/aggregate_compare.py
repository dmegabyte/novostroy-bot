#!/usr/bin/env python3
"""Aggregate sealed mechanism-v2 route-only runs and safe session metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
LABELS_PATH = ROOT / "private" / "labels.jsonl"
ARMS = ["B0", "M1", "S1"]
SAFE_SESSION_ID = re.compile(r"^ses_[A-Za-z0-9_-]{1,96}$")
SAFE_TEXT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+=-]{0,160}$")
METRIC_KEYS = {"schema_version", "source", "task_id", "mode", "phase", "session_id", "parent_id", "fresh_subagent_session", "actual_agent", "actual_model_identity", "diagnostics", "resources", "coverage"}
RESOURCE_KEYS = {"wall_ms", "input_tokens", "output_tokens", "reasoning_tokens", "tokens_cache_read", "tokens_cache_write", "cached_tokens", "total_tokens", "estimated_provider_cost", "model_calls", "tool_calls", "failed_tool_calls", "retries"}
DIAGNOSTIC_KEYS = {"time_created_ms", "time_updated_ms"}
SCORE_KEYS = {"schema_version", "status", "task_id", "candidate", "sealed_result_sha256", "private_labels_sha256", "quality_pass", "safe_route_summary", "selected_expected_route", "scorer_blind_to_arm", "private_expected_values_disclosed"}


class AggregateError(ValueError):
    pass


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _experiment() -> dict[str, Any]:
    return _read_json(ROOT / "experiment.json")


def _expected_pairs(experiment: dict[str, Any]) -> set[tuple[str, str]]:
    return {(task_id, arm) for task_id in experiment["holdout_task_ids"] for arm in ARMS}


def _load_artifacts(runs_root: Path) -> list[dict[str, Any]]:
    sealed_paths = sorted(runs_root.resolve().glob("*/sealed_result.json"))
    rows = []
    for sealed_path in sealed_paths:
        sealed = _read_json(sealed_path)
        score_path = sealed_path.parent / "blind_score.json"
        if not score_path.is_file():
            raise AggregateError(f"missing blind_score.json for {sealed_path.parent.name}")
        score = _read_json(score_path)
        rows.append({"sealed": sealed, "score": score, "sealed_hash": _sha256(sealed_path)})
    return rows


def _safe_nonempty(value: Any, regex: re.Pattern[str], name: str) -> str:
    if not isinstance(value, str) or not value.strip() or not regex.fullmatch(value):
        raise AggregateError(f"missing or unsafe {name}")
    return value


def _nonnegative_number(value: Any, name: str) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise AggregateError(f"missing or malformed required numeric resource: {name}")
    return value


def _validate_score(score: dict[str, Any], sealed: dict[str, Any], sealed_hash: str) -> None:
    if set(score) != SCORE_KEYS:
        raise AggregateError("blind score must use closed schema")
    if score.get("schema_version") != 1 or score.get("status") != "blind_route_score":
        raise AggregateError("blind score status/schema mismatch")
    if score.get("task_id") != sealed.get("task_id") or score.get("sealed_result_sha256") != sealed_hash:
        raise AggregateError("blind score does not bind to sealed task/hash")
    labels_hash = _sha256(LABELS_PATH)
    if score.get("private_labels_sha256") != labels_hash or score.get("private_labels_sha256") != sealed.get("source_hashes", {}).get("private/labels.jsonl"):
        raise AggregateError("blind score private label provenance mismatch")
    candidate = score.get("candidate")
    binding = sealed.get("binding", {})
    if not isinstance(candidate, dict) or set(candidate) != {"session_id", "candidate_sha256", "run_manifest_sha256"}:
        raise AggregateError("blind score candidate binding schema mismatch")
    if candidate.get("session_id") != sealed.get("fresh_session_id"):
        raise AggregateError("blind score session binding mismatch")
    if candidate.get("candidate_sha256") != binding.get("candidate_sha256") or candidate.get("run_manifest_sha256") != binding.get("run_manifest_sha256"):
        raise AggregateError("blind score candidate/source binding mismatch")
    for key in ("quality_pass", "safe_route_summary", "selected_expected_route", "scorer_blind_to_arm", "private_expected_values_disclosed"):
        if not isinstance(score.get(key), bool):
            raise AggregateError(f"blind score {key} must be boolean")
    if score.get("scorer_blind_to_arm") is not True or score.get("private_expected_values_disclosed") is not False:
        raise AggregateError("blind score must remain blind and non-disclosing")


def _validate_metric(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) != METRIC_KEYS:
        raise AggregateError("metric must use closed normalized schema")
    if row.get("schema_version") != 1 or row.get("source") != "opencode_db_normalized_aggregates":
        raise AggregateError("metrics must be normalized OpenCode DB aggregates")
    _safe_nonempty(row.get("session_id"), SAFE_SESSION_ID, "metric session_id")
    _safe_nonempty(row.get("parent_id"), SAFE_SESSION_ID, "metric parent_id")
    _safe_nonempty(row.get("task_id"), SAFE_TEXT_ID, "metric task_id")
    if row.get("phase") not in set(ARMS):
        raise AggregateError("metric phase must be B0/M1/S1")
    if row.get("mode") not in {"baseline", "memory", "sham"}:
        raise AggregateError("metric mode must be baseline/memory/sham")
    if row.get("fresh_subagent_session") is not True:
        raise AggregateError("metric must identify a fresh subagent session")
    _safe_nonempty(row.get("actual_agent"), SAFE_TEXT_ID, "actual_agent")
    actual_model = row.get("actual_model_identity")
    if not isinstance(actual_model, str) or not actual_model.strip():
        raise AggregateError("missing actual_model_identity")
    try:
        model_obj = json.loads(actual_model)
    except json.JSONDecodeError as exc:
        raise AggregateError("actual_model_identity must be canonical JSON") from exc
    if not isinstance(model_obj, dict) or set(model_obj) != {"model_id", "provider_id", "variant"}:
        raise AggregateError("actual_model_identity must contain model_id/provider_id/variant only")
    for key, value in model_obj.items():
        _safe_nonempty(value, SAFE_TEXT_ID, f"actual_model_identity.{key}")
    diagnostics = row.get("diagnostics")
    if not isinstance(diagnostics, dict) or set(diagnostics) != DIAGNOSTIC_KEYS:
        raise AggregateError("metric diagnostics must be closed")
    _nonnegative_number(diagnostics.get("time_created_ms"), "time_created_ms")
    _nonnegative_number(diagnostics.get("time_updated_ms"), "time_updated_ms")
    resources = row.get("resources")
    if not isinstance(resources, dict) or set(resources) != RESOURCE_KEYS:
        raise AggregateError("metric resources must contain the full required closed schema")
    for key in RESOURCE_KEYS:
        _nonnegative_number(resources.get(key), key)
    coverage = row.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != {"status", "present", "missing"}:
        raise AggregateError("metric coverage must be explicit and closed")
    if coverage.get("status") != "complete" or coverage.get("missing") != []:
        raise AggregateError("metric coverage not_evaluable: missing required coverage")
    if not isinstance(coverage.get("present"), list):
        raise AggregateError("metric coverage present must be a list")
    return row


def _metrics_by_session(metrics_path: Path) -> dict[str, dict[str, Any]]:
    raw = _read_json(metrics_path.resolve())
    rows = raw.get("sessions", raw) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise AggregateError("metrics JSON must be a list or {'sessions': [...]} object")
    result = {}
    for row in rows:
        row = _validate_metric(row)
        sid = row.get("session_id")
        if sid in result:
            raise AggregateError("duplicate metric session_id")
        result[sid] = row
    return result


def _sum_mean(values: list[float | int]) -> dict[str, float | int | None]:
    if not values:
        return {"sum": None, "mean": None}
    return {"sum": sum(values), "mean": statistics.mean(values)}


def aggregate(runs_root: Path, metrics_path: Path) -> dict[str, Any]:
    experiment = _experiment()
    artifacts = _load_artifacts(runs_root)
    metrics = _metrics_by_session(metrics_path)
    expected = _expected_pairs(experiment)
    seen_pairs: set[tuple[str, str]] = set()
    session_ids: list[str] = []
    joined: list[dict[str, Any]] = []
    allow = experiment["receipt_advice_allowlist"]

    if len(artifacts) != 18:
        raise AggregateError("aggregate requires exactly all 18 sealed run summaries")
    for item in artifacts:
        sealed = item["sealed"]
        _safe_nonempty(sealed.get("fresh_session_id"), SAFE_SESSION_ID, "sealed session_id")
        _validate_score(item["score"], sealed, item["sealed_hash"])
        result = sealed.get("agent_result", {})
        pair = (sealed.get("task_id"), sealed.get("arm"))
        if pair in seen_pairs:
            raise AggregateError("duplicate task/arm sealed result")
        seen_pairs.add(pair)
        if pair not in expected:
            raise AggregateError("unexpected task/arm sealed result")
        receipt = result.get("receipt", {})
        if receipt.get("consulted_advice_codes") != allow[pair[0]][pair[1]]:
            raise AggregateError("receipt advice coverage mismatch")
        if pair[1] == "B0" and receipt.get("consulted_advice_codes") != []:
            raise AggregateError("B0 receipt must have empty advice")
        sid = sealed.get("fresh_session_id")
        if sid not in metrics:
            raise AggregateError("missing metric coverage for sealed session")
        metric = metrics[sid]
        if metric.get("task_id") != pair[0] or metric.get("phase") != pair[1]:
            raise AggregateError("metric task/arm coverage mismatch")
        if metric.get("session_id") != sid:
            raise AggregateError("metric session does not match sealed session")
        session_ids.append(sid)
        joined.append({"task_id": pair[0], "arm": pair[1], "score": item["score"], "metric": metric, "receipt": receipt})

    if seen_pairs != expected:
        raise AggregateError("missing task/arm cohort coverage")
    if len(session_ids) != len(set(session_ids)):
        raise AggregateError("duplicate sealed session ids")
    label_hashes = {row["score"].get("private_labels_sha256") for row in joined}
    if label_hashes != {_sha256(LABELS_PATH)}:
        raise AggregateError("aggregate private label provenance mismatch")
    parents = {row["metric"].get("parent_id") for row in joined}
    agents = {row["metric"].get("actual_agent") for row in joined}
    models = {row["metric"].get("actual_model_identity") for row in joined}
    if len(parents) != 1 or len(agents) != 1 or len(models) != 1:
        raise AggregateError("all 18 runs must share common parent, agent and model identity")

    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in joined:
        by_arm[row["arm"]].append(row)
    arm_metrics: dict[str, Any] = {}
    for arm in ARMS:
        rows = by_arm[arm]
        resources = [row["metric"].get("resources", {}) for row in rows]
        arm_metrics[arm] = {
            "runs": len(rows),
            "quality_passes": sum(1 for row in rows if row["score"].get("quality_pass") is True),
            "quality_rate": sum(1 for row in rows if row["score"].get("quality_pass") is True) / len(rows),
            "wall_ms": _sum_mean([res["wall_ms"] for res in resources]),
            "total_tokens": _sum_mean([res["total_tokens"] for res in resources]),
            "tool_calls": _sum_mean([res["tool_calls"] for res in resources]),
            "failed_tool_calls": _sum_mean([res["failed_tool_calls"] for res in resources]),
        }

    def beats(left: str, right: str) -> bool:
        lq = arm_metrics[left]["quality_rate"]
        rq = arm_metrics[right]["quality_rate"]
        if lq <= rq:
            return False
        return arm_metrics[left]["wall_ms"]["mean"] < arm_metrics[right]["wall_ms"]["mean"]

    receipt_relevant = all(row["receipt"].get("consulted_advice_codes") == allow[row["task_id"]]["M1"] for row in by_arm["M1"])
    mechanism_evaluable = receipt_relevant and beats("M1", "B0") and beats("M1", "S1")
    return {
        "schema_version": 1,
        "status": "aggregated_observational_only",
        "claim": "observational_only_no_causal_claim",
        "cohort": {"sealed_runs": 18, "holdouts": 6, "arms": ARMS},
        "private_labels_sha256": next(iter(label_hashes)),
        "common_identity": {"parent_id": next(iter(parents)), "actual_agent": next(iter(agents)), "actual_model_identity": next(iter(models))},
        "arms": arm_metrics,
        "comparisons": {"M1_vs_B0": {"m1_beats_control_quality_before_resource": beats("M1", "B0")}, "M1_vs_S1": {"m1_beats_control_quality_before_resource": beats("M1", "S1")}},
        "mechanism_condition": "mechanism_evidence_observed" if mechanism_evaluable else "mechanism_not_evaluable",
        "caveats": ["task API cannot lock/pin actual model or seed", "receipts are self-report route declarations", "quality is evaluated before resources", "missing coverage is not_evaluable, never zero"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate mechanism-v2 sealed runs; no causal claim.")
    parser.add_argument("--runs-root", required=True, type=Path)
    parser.add_argument("--metrics-json", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        result = aggregate(args.runs_root, args.metrics_json)
        text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        print(json.dumps({"status": result["status"], "mechanism_condition": result["mechanism_condition"], "claim": result["claim"]}, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "not_evaluable", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
