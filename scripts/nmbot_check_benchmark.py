#!/usr/bin/env python3
"""Measure the existing local nmbot check gate without adding another runner."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT / "scripts" / "nmbot_check.py"
CONTEXT_QUERIES = Path("config/nmbot_context_benchmark_queries.json")
CONTEXT_LABELS = Path("config/nmbot_context_benchmark_labels.json")
CONTEXT_ARTIFACT_SHA256 = {
    "queries": "ff7ecf16be0c4c846c1fef0194ca4a94856e573f94acbab4cc8be28ddff51801",
    "labels": "8f8a7a2845a709d8d5ebdef90817211ac6b738eecbb97de1b7da8a8f25a8b1a3",
}
SAFE_SCOPES = (
    "docs", "contracts", "v0", "v1", "v2", "v3", "runtime",
    "audit", "quality", "artifact", "isolation", "release",
)
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def percentile_nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("at least one timing sample is required")
    ordered = sorted(float(value) for value in values)
    rank = max(1, min(len(ordered), int((percentile * len(ordered)) + 0.999999999)))
    return ordered[rank - 1]


def _run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )


def benchmark(
    scopes: Sequence[str], *, runs: int, warmup: int,
    runner: Runner = _run_command,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[int, dict[str, Any]]:
    command = [sys.executable, str(CHECK_SCRIPT), *scopes, "--json"]
    samples_ms: list[float] = []
    for index in range(warmup + runs):
        started = clock()
        result = runner(command)
        elapsed_ms = round((clock() - started) * 1000, 3)
        if result.returncode != 0:
            return result.returncode or 1, {
                "status": "failed", "scopes": list(scopes),
                "failed_phase": "warmup" if index < warmup else "measure",
                "failed_run": index + 1, "returncode": result.returncode,
                "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:],
            }
        if index >= warmup:
            samples_ms.append(elapsed_ms)

    return 0, {
        "status": "passed", "mode": "local_read_only",
        "scopes": list(scopes), "runs": runs, "warmup_runs": warmup,
        "samples_ms": samples_ms,
        "timing_ms": {
            "min": min(samples_ms),
            "p50": percentile_nearest_rank(samples_ms, 0.50),
            "p95": percentile_nearest_rank(samples_ms, 0.95),
            "max": max(samples_ms),
        },
        "network": "forbidden_by_nmbot_check_manifest",
        "secrets": "not_required",
        "model_calls": "forbidden_by_nmbot_check_manifest",
        "side_effects": "local_test_temp_files_only",
    }


def _load_pinned_json_object(path: Path, *, artifact: str) -> dict[str, Any]:
    """Load only exact code-pinned artifact bytes; metadata flags are insufficient."""
    raw = path.read_bytes()
    actual_digest = hashlib.sha256(raw).hexdigest()
    expected_digest = CONTEXT_ARTIFACT_SHA256[artifact]
    if actual_digest != expected_digest:
        raise ValueError(
            f"{artifact} artifact SHA-256 mismatch: expected code-pinned "
            f"{expected_digest}, got {actual_digest}"
        )
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"benchmark artifact must be an object: {path}")
    return data


def load_context_benchmark_cases(
    queries_path: Path = ROOT / CONTEXT_QUERIES,
    labels_path: Path = ROOT / CONTEXT_LABELS,
) -> list[dict[str, Any]]:
    """Load the separately frozen query and label artifacts without tuning them."""
    # Verify raw bytes before parsing, routing, or scoring so editable labels
    # cannot alter an otherwise favorable benchmark result.
    queries = _load_pinned_json_object(queries_path, artifact="queries")
    labels = _load_pinned_json_object(labels_path, artifact="labels")
    if queries.get("schema") != "nmbot.context_benchmark_queries.v2" or not queries.get("frozen_before_scoring"):
        raise ValueError("query artifact must be frozen nmbot.context_benchmark_queries.v2")
    if labels.get("schema") != "nmbot.context_benchmark_labels.v2" or not labels.get("frozen_before_scoring"):
        raise ValueError("label artifact must be frozen nmbot.context_benchmark_labels.v2")
    if queries.get("generation") != "independent_before_labels" or labels.get("labeling") != "independent_before_scoring":
        raise ValueError("v2 artifacts must declare separate query generation and labeling")
    raw_queries, raw_labels = queries.get("cases"), labels.get("cases")
    if not isinstance(raw_queries, list) or not isinstance(raw_labels, list) or len(raw_queries) < 24:
        raise ValueError("benchmark requires at least 24 frozen cases")
    query_by_id = {item.get("id"): item for item in raw_queries if isinstance(item, dict) and isinstance(item.get("id"), str)}
    label_by_id = {item.get("id"): item for item in raw_labels if isinstance(item, dict) and isinstance(item.get("id"), str)}
    if len(query_by_id) != len(raw_queries) or set(query_by_id) != set(label_by_id):
        raise ValueError("query and label artifacts must have the same unique case ids")
    cases: list[dict[str, Any]] = []
    for case_id, query in query_by_id.items():
        label = label_by_id[case_id]
        if not isinstance(query.get("query"), str) or not query["query"].strip():
            raise ValueError(f"case {case_id} needs a non-empty query")
        expected_abstain = label.get("expect_abstain") is True
        if not expected_abstain and not isinstance(label.get("expected_owner_path"), str):
            raise ValueError(f"case {case_id} needs a scoring-only expected owner")
        cases.append({"id": case_id, "query": query["query"], "label": label})
    if len({case["query"] for case in cases}) != len(cases):
        raise ValueError("frozen benchmark queries must be independent (unique)")
    return cases


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load benchmark route: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _benchmark_routes(root: Path) -> dict[str, Any]:
    return {
        "navigation": _load_module("nmbot_navigation_benchmark", root / "scripts/nmbot_navigation.py"),
        "retrieval": _load_module("nmbot_retrieval_benchmark", root / "scripts/nmbot_retrieval.py"),
        "gate": _load_module("nmbot_context_gate_benchmark", root / "scripts/nmbot_context_gate.py"),
    }


def _strict_fail_closed() -> dict[str, Any]:
    return {
        "schema": "nmbot.context_gate.v1", "route": "bounded_fallback",
        "stop_reason": "no_candidate_answers", "abstain": True, "context": [], "candidates": [],
        "trace": {"schema": "bounded-retrieval.v1", "project_id": "nmbot", "route": "bounded_fallback", "candidate_ids": [], "selected_source_ids": [], "candidate_count": 0, "selected_source_count": 0, "expansion_hops": 0, "cross_project_notebooks": 0, "lines_loaded": 0, "characters_loaded": 0, "stop_reason": "no_candidate_answers"},
    }


def _selected_target_spec(navigation_report: dict[str, Any]) -> dict[str, Any] | None:
    """Use only a navigation-owned exact selection; ambiguity never reaches the gate."""
    if (
        navigation_report.get("abstain")
        or navigation_report.get("fallback")
        or navigation_report.get("selection_required") is not False
    ):
        return None
    spec = navigation_report.get("selected_target_spec")
    if not isinstance(spec, dict) or spec.get("target_kind") not in {"stage", "symbol", "docs"}:
        return None
    if not isinstance(spec.get("target"), str) or not spec["target"]:
        return None
    if not isinstance(spec.get("target_owner"), str) or not spec["target_owner"]:
        return None
    selected: dict[str, Any] = {"target_kind": spec["target_kind"], "target": spec["target"], "target_owner": spec["target_owner"]}
    if isinstance(spec.get("start_line"), int) and isinstance(spec.get("end_line"), int):
        selected.update(target_start_line=spec["start_line"], target_end_line=spec["end_line"])
    return selected


def _route_outputs(case: dict[str, Any], *, root: Path, routes: dict[str, Any] | None = None, clock: Callable[[], float] = time.perf_counter) -> dict[str, Any]:
    """Run existing owners only; this scorer does not retrieve or rank anything."""
    routes = routes or _benchmark_routes(root)
    navigation, retrieval, gate = routes["navigation"], routes["retrieval"], routes["gate"]
    query = case["query"]
    broad_started = clock()
    broad_navigation = navigation.navigate(query, root=root)
    broad_retrieval = retrieval.search_cards(query, root=root)
    broad_ms = round((clock() - broad_started) * 1000, 3)
    strict_started = clock()
    strict_navigation = navigation.navigate(query, root=root)
    selected = _selected_target_spec(strict_navigation)
    if selected is None:
        strict = _strict_fail_closed()
    else:
        strict = gate.run_gate(
            query, project_id="nmbot", evidence_type=selected["target_kind"],
            definition_of_done="owner source and focused test", root=root,
            target_kind=selected["target_kind"], target=selected["target"],
            target_owner=selected["target_owner"],
            target_start_line=selected.get("target_start_line"), target_end_line=selected.get("target_end_line"),
        )
    strict_ms = round((clock() - strict_started) * 1000, 3)
    return {"navigation": broad_navigation, "retrieval": broad_retrieval, "strict_navigation": strict_navigation, "strict_gate": strict, "strict_selection": selected, "timing_ms": {"broad": broad_ms, "strict_stop_2": strict_ms}}


def _paths_from_broad(outputs: dict[str, Any]) -> set[str]:
    return {
        str(item["path"])
        for report, key in ((outputs["navigation"], "results"), (outputs["retrieval"], "cards"))
        for item in report.get(key, []) if isinstance(item, dict) and isinstance(item.get("path"), str)
    }


def _paths_from_strict(outputs: dict[str, Any]) -> set[str]:
    return {str(item["path"]) for item in outputs["strict_gate"].get("context", []) if isinstance(item, dict) and isinstance(item.get("path"), str)}


def _output_context(report: dict[str, Any], key: str) -> dict[str, int]:
    """Count bounded context exposed by an owner output, not hidden index scans."""
    items = report.get(key, [])
    ranges = 0
    lines = 0
    chars = 0
    files = {str(item.get("path")) for item in items if isinstance(item, dict) and item.get("path")}
    for item in items:
        if not isinstance(item, dict):
            continue
        if "line_range" in item:
            start, end = item["line_range"]
            ranges += 1
            lines += int(end) - int(start) + 1
            chars += len(str(item.get("excerpt", "")))
        elif "start_line" in item and "end_line" in item:
            ranges += 1
            lines += int(item["end_line"]) - int(item["start_line"]) + 1
            chars += int(item.get("characters", 0))
    return {"files": len(files), "ranges": ranges, "lines": lines, "chars": chars}


def _score_locked_case(payload: dict[str, Any]) -> dict[str, Any]:
    label, outputs = payload["label"], payload["outputs"]
    expected_abstain = label.get("expect_abstain") is True
    expected = set(label.get("expected_paths", [label.get("expected_owner_path")])) - {None}
    broad_paths, strict_paths = _paths_from_broad(outputs), _paths_from_strict(outputs)
    navigation_paths = {
        str(item["path"])
        for item in outputs["navigation"].get("results", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    broad_abstain = bool(outputs["navigation"].get("abstain")) and bool(outputs["retrieval"].get("abstain"))
    strict_abstain = bool(outputs["strict_gate"].get("abstain"))
    broad_hit = broad_abstain if expected_abstain else bool(expected & broad_paths)
    strict_hit = strict_abstain if expected_abstain else bool(expected & strict_paths)
    broad_context = _output_context(outputs["navigation"], "results")
    retrieval_context = _output_context(outputs["retrieval"], "cards")
    for key in broad_context:
        broad_context[key] += retrieval_context[key]
    strict_trace = outputs["strict_gate"]["trace"]
    strict_context = {"files": strict_trace["selected_source_count"], "ranges": strict_trace["selected_source_count"], "lines": strict_trace["lines_loaded"], "chars": strict_trace["characters_loaded"]}
    # A budget stop is healthy when it already loaded the expected owner. Harm is an outcome mismatch.
    harmful = not strict_hit
    return {
        "id": payload["id"], "expected_abstain": expected_abstain,
        "broad": {"owner_hit": broad_hit, "abstain": broad_abstain, "output_context": broad_context, "tool_calls_proxy": 2, "rework_proxy": int(not broad_hit), "latency_ms": outputs.get("timing_ms", {}).get("broad", 0.0)},
        "strict_stop_2": {"owner_hit": strict_hit, "abstain": strict_abstain, "selection_required": bool(outputs.get("strict_navigation", {}).get("selection_required")), "candidate_count": len(outputs.get("strict_navigation", {}).get("results", [])), "candidate_owner_hit": strict_abstain if expected_abstain else bool(expected & navigation_paths), "wrong_owner_context_loaded": bool(strict_paths) and not strict_hit, "output_context": strict_context, "stop_reason": outputs["strict_gate"]["stop_reason"], "harmful_early_stop": harmful, "tool_calls_proxy": 2 if outputs.get("strict_selection") else 1, "rework_proxy": int(not strict_hit), "latency_ms": outputs.get("timing_ms", {}).get("strict_stop_2", 0.0)},
    }


def context_benchmark(cases: Sequence[dict[str, Any]], *, output_dir: Path, root: Path = ROOT, clock: Callable[[], float] = time.perf_counter) -> tuple[int, dict[str, Any]]:
    """Lock every verbose route result to disk, then score only those locked files."""
    output_dir.mkdir(parents=True, exist_ok=False)
    routes = _benchmark_routes(root)
    locked: list[Path] = []
    for index, case in enumerate(cases, start=1):
        started = clock()
        try:
            outputs = _route_outputs(case, root=root, routes=routes)
        except Exception as exc:
            return 1, {"status": "failed", "failed_case": case["id"], "failed_index": index, "failure_layer": "route", "error": str(exc), "verbose_output_dir": str(output_dir)}
        payload = {"id": case["id"], "query": case["query"], "label": case["label"], "latency_ms": round((clock() - started) * 1000, 3), "outputs": outputs}
        path = output_dir / f"{index:02d}-{case['id']}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        locked.append(path)
    scores = [_score_locked_case(json.loads(path.read_text(encoding="utf-8"))) for path in locked]
    def aggregate(route: str) -> dict[str, Any]:
        rows = [item[route] for item in scores]
        latencies = [float(row["latency_ms"]) for row in rows]
        return {"owner_hit_rate": round(sum(row["owner_hit"] for row in rows) / len(rows), 4), "recall": round(sum(row["owner_hit"] for row in rows) / len(rows), 4), "tool_calls_proxy_total": sum(row["tool_calls_proxy"] for row in rows), "rework_proxy_total": sum(row["rework_proxy"] for row in rows), "latency_ms": {"min": min(latencies), "p50": percentile_nearest_rank(latencies, .5), "p95": percentile_nearest_rank(latencies, .95), "max": max(latencies)}, "output_context_total": {key: sum(row["output_context"][key] for row in rows) for key in ("files", "ranges", "lines", "chars")}}
    latencies = [json.loads(path.read_text(encoding="utf-8"))["latency_ms"] for path in locked]
    report = {"status": "passed", "schema": "nmbot.context_benchmark.v2", "case_count": len(scores), "comparison": {"broad_navigation_plus_retrieval": aggregate("broad"), "strict_stop_2_context_gate": aggregate("strict_stop_2")}, "comparison_case_latency_ms": {"min": min(latencies), "p50": percentile_nearest_rank(latencies, .5), "p95": percentile_nearest_rank(latencies, .95), "max": max(latencies)}, "supervised_selection": {"selection_required_count": sum(item["strict_stop_2"]["selection_required"] for item in scores), "automatic_exact_count": sum(not item["strict_stop_2"]["selection_required"] for item in scores), "candidate_owner_recall": round(sum(item["strict_stop_2"]["candidate_owner_hit"] for item in scores) / len(scores), 4), "average_candidate_count": round(sum(item["strict_stop_2"]["candidate_count"] for item in scores) / len(scores), 2), "wrong_owner_context_load_count": sum(item["strict_stop_2"]["wrong_owner_context_loaded"] for item in scores)}, "harmful_early_stop_count": sum(item["strict_stop_2"]["harmful_early_stop"] for item in scores), "proxy_definitions": {"tool_calls": "actual top-level calls: broad=navigation+retrieval (2); strict=navigation plus context-gate only after navigation emits one unambiguous selected_target_spec (2), otherwise navigation-only fail-closed (1)", "rework": "1 when the route misses the frozen expected owner or fails the frozen abstain expectation; otherwise 0", "candidate_owner_recall": "scoring-only rate at which the frozen expected owner appears among the bounded navigation candidates (or a frozen negative correctly abstains); labels never select a candidate", "output_context": "bounded files/ranges/lines/chars exposed by existing route output; it intentionally excludes hidden registry/index construction", "harmful_early_stop": "outcome mismatch only: a positive misses its expected owner or abstains, or a negative does not abstain; successful context_budget_reached is not harmful", "wrong_owner_context_load": "strict context was loaded but did not contain the frozen expected owner; supervised ambiguity must keep this at zero"}, "provenance": {"artifact_binding": "exact raw artifact bytes are verified against SHA-256 digests predeclared in code before routing or scoring", "code_pinned_sha256": CONTEXT_ARTIFACT_SHA256}, "scope": "local deterministic benchmark only; no production or generalization claim", "verbose_output_dir": str(output_dir), "locked_output_count": len(locked)}
    (output_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return 0, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark the existing local read-only nmbot gate.")
    parser.add_argument("scopes", nargs="*", metavar="SCOPE")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--context-benchmark", action="store_true", help="compare existing navigation+retrieval with strict STOP-2 using frozen artifacts")
    parser.add_argument("--output-dir", help="new directory for locked verbose context benchmark outputs")
    parser.add_argument("--smoke", action="store_true", help="run only the first frozen context case")
    args = parser.parse_args(argv)
    if any(scope not in SAFE_SCOPES for scope in args.scopes):
        parser.error(f"scopes must be chosen from: {', '.join(SAFE_SCOPES)}")
    if args.runs < 1 or args.runs > 50 or args.warmup < 0 or args.warmup > 10:
        parser.error("--runs must be 1..50 and --warmup must be 0..10")

    if args.context_benchmark:
        if not args.output_dir:
            parser.error("--context-benchmark requires --output-dir so verbose outputs lock before scoring")
        try:
            cases = load_context_benchmark_cases()
            code, report = context_benchmark(cases[:1] if args.smoke else cases, output_dir=Path(args.output_dir))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            code, report = 2, {"status": "failed", "failure_layer": "artifact_or_output", "error": str(exc)}
    else:
        code, report = benchmark(args.scopes or ["docs"], runs=args.runs, warmup=args.warmup)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    elif code == 0:
        timing = report["timing_ms"]
        print(
            f"PASS local gate {' '.join(report['scopes'])}: "
            f"p50={timing['p50']} ms p95={timing['p95']} ms "
            f"({report['runs']} runs, {report['warmup_runs']} warmup)"
        )
    else:
        print(f"FAIL local gate: {report['failed_phase']} run {report['failed_run']}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
