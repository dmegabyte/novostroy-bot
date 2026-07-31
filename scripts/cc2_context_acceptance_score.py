#!/usr/bin/env python3
"""Score CC2 context acceptance predictions against locked labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREDICTIONS = Path("/tmp/opencode/cc2_context_acceptance_predictions_v1.json")
DEFAULT_LABELS = PROJECT_ROOT / "tests" / "fixtures" / "cc2_context_acceptance_labels_v1.json"
DEFAULT_OUTPUT = Path("/tmp/opencode/cc2_context_acceptance_score_v1.json")
DEFAULT_REPORT = Path("/tmp/opencode/cc2_context_acceptance_report_v1.md")
MAX_SOURCES = 2
MAX_LINES = 80
MAX_CHARS = 8000
GOOD_BUDGET = {"within_budget", "context_budget_reached"}
FORBIDDEN_PREDICTION_KEYS = {"context", "source_text", "snippet", "body", "title", "raw_log", "secret", "secrets", "source_body"}


def _load_cases(path: Path, key: str) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get(key) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"{path}: {key} list is required")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("case_id"):
            raise ValueError(f"{path}: invalid case row")
        case_id = str(row["case_id"])
        if case_id in by_id:
            raise ValueError(f"{path}: duplicate case_id {case_id}")
        by_id[case_id] = row
    return by_id


def score(predictions_path: Path, labels_path: Path) -> dict[str, Any]:
    predictions = _load_cases(predictions_path, "predictions")
    labels = _load_cases(labels_path, "cases")
    failures: list[str] = []
    if set(predictions) != set(labels):
        failures.append("case_id_set_mismatch")
    positives = [cid for cid, row in labels.items() if not bool(row.get("abstain"))]
    negatives = [cid for cid, row in labels.items() if bool(row.get("abstain"))]
    exact_positive = 0
    negative_abstain = 0
    false_selections = 0
    false_abstentions = 0
    budget_ok = 0
    budget_exact = 0
    max_sources_seen = 0
    max_lines_seen = 0
    max_chars_seen = 0
    unsafe_claims = 0
    clipped_long_symbols_honest = 0
    leak_free_predictions = 0
    for cid, label in labels.items():
        pred = predictions.get(cid, {})
        if FORBIDDEN_PREDICTION_KEYS & set(pred):
            failures.append(f"unsafe_prediction_key:{cid}")
        else:
            leak_free_predictions += 1
        pred_abstain = bool(pred.get("abstain"))
        if bool(label.get("abstain")):
            if pred_abstain:
                negative_abstain += 1
            else:
                false_selections += 1
                failures.append(f"false_selection:{cid}")
            continue
        if pred_abstain:
            false_abstentions += 1
            failures.append(f"false_abstention:{cid}")
            continue
        if pred.get("owner_source") == label.get("owner_source") and pred.get("owner_symbol") == label.get("owner_symbol"):
            exact_positive += 1
        else:
            failures.append(f"owner_or_symbol_mismatch:{cid}")
        if pred.get("budget_status") in GOOD_BUDGET:
            budget_ok += 1
        else:
            failures.append(f"budget_status_bad:{cid}")
        if pred.get("budget_status") == label.get("budget_status"):
            budget_exact += 1
        else:
            failures.append(f"budget_status_mismatch:{cid}")
        max_sources_seen = max(max_sources_seen, int(pred.get("selected_source_count") or 0))
        max_lines_seen = max(max_lines_seen, int(pred.get("lines_loaded") or 0))
        max_chars_seen = max(max_chars_seen, int(pred.get("characters_loaded") or 0))
        if label.get("budget_status") == "context_budget_reached" and pred.get("budget_status") == "context_budget_reached" and pred.get("stop_reason") == "context_budget_reached":
            clipped_long_symbols_honest += 1
        if not pred.get("local_read_only") or pred.get("production_proof"):
            unsafe_claims += 1
            failures.append(f"unsafe_claim:{cid}")
    if max_sources_seen > MAX_SOURCES:
        failures.append("max_sources_exceeded")
    if max_lines_seen > MAX_LINES:
        failures.append("max_lines_exceeded")
    if max_chars_seen > MAX_CHARS:
        failures.append("max_chars_exceeded")
    expected_clipped = sum(1 for row in labels.values() if row.get("budget_status") == "context_budget_reached")
    metrics = {
        "positives_total": len(positives),
        "positives_exact_owner_symbol": exact_positive,
        "negatives_total": len(negatives),
        "negatives_abstained": negative_abstain,
        "false_selections": false_selections,
        "false_abstentions": false_abstentions,
        "positive_budget_ok": budget_ok,
        "positive_budget_exact": budget_exact,
        "clipped_long_symbols_honest": clipped_long_symbols_honest,
        "max_sources_seen": max_sources_seen,
        "max_lines_seen": max_lines_seen,
        "max_chars_seen": max_chars_seen,
        "unsafe_claims": unsafe_claims,
        "leak_free_predictions": leak_free_predictions,
    }
    hard_pass = (
        exact_positive == len(positives)
        and negative_abstain == len(negatives)
        and false_selections == 0
        and false_abstentions == 0
        and budget_ok == len(positives)
        and budget_exact == len(positives)
        and clipped_long_symbols_honest == expected_clipped
        and max_sources_seen <= MAX_SOURCES
        and max_lines_seen <= MAX_LINES
        and max_chars_seen <= MAX_CHARS
        and unsafe_claims == 0
        and leak_free_predictions == len(labels)
        and not failures
    )
    return {"schema_version": 1, "hard_pass": hard_pass, "metrics": metrics, "failures": failures}


def render_report(result: dict[str, Any], *, predictions_path: Path, labels_path: Path, score_path: Path) -> str:
    metrics = result["metrics"]
    lines = [
        "# CC2 context acceptance v1",
        "",
        f"Hard pass: `{result['hard_pass']}`",
        "",
        "## Artifacts",
        f"- Predictions: `{predictions_path}`",
        f"- Locked labels: `{labels_path}`",
        f"- Score JSON: `{score_path}`",
        "",
        "## Metrics",
    ]
    for key, value in metrics.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    lines.append("## Failures")
    lines.extend(f"- `{item}`" for item in result.get("failures", []))
    if not result.get("failures"):
        lines.append("- none")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    result = score(args.predictions, args.labels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.write_text(render_report(result, predictions_path=args.predictions, labels_path=args.labels, score_path=args.output), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "report": str(args.report), "hard_pass": result["hard_pass"]}, ensure_ascii=False, sort_keys=True))
    return 0 if result["hard_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
