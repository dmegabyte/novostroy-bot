#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
MATRIX_PATH = ROOT / "shortlist_composer_matrix.json"
CASE_KEYS = {"case_id", "scenario", "input", "candidate", "expected_valid", "expected_errors", "expected_decision_roles"}
CONTACT_RE = re.compile(r"(?:https?://|www\.|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|(?:\+?\d[\s().-]*){10,})", re.IGNORECASE)
INTERNAL_RE = re.compile(r"\b(?:MCP|JSON|payload|diagnostics|source_field|evidence|canonical|model|schema|trace|OptionCard|enum|карточк\w*|данн\w*|контекст\w*|подтвержд[её]н\w*)\b", re.IGNORECASE)


def _load_composer():
    spec = importlib.util.spec_from_file_location("shortlist_composer_hypothesis", ROOT / "shortlist_composer_hypothesis.py")
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("shortlist composer loader is missing")
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def assert_sanitized(value: Any, *, visible_only: bool = False) -> None:
    for item in _walk(value):
        if isinstance(item, str):
            if CONTACT_RE.search(item):
                raise ValueError("contact/url-like content is forbidden")
            if visible_only and INTERNAL_RE.search(item):
                raise ValueError("internal term leak is forbidden")


def generate_report(matrix: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    composer = _load_composer()
    cases = matrix if matrix is not None else _load_json(MATRIX_PATH)
    if not isinstance(cases, list):
        raise ValueError("matrix root must be a list")
    report: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != CASE_KEYS:
            raise ValueError("matrix case shape mismatch")
        assert_sanitized(case["input"])
        assert_sanitized(case["candidate"], visible_only=True)
        result = composer.simulate(case["input"], case["candidate"])
        checks = {
            "expected_valid": result["valid"] is case["expected_valid"],
            "expected_errors": all(code in result["errors"] for code in case["expected_errors"]),
            "manual_review_required": result.get("manual_review_required") is True,
            "decision_roles": (not result["valid"]) or result.get("metadata", {}).get("decision_roles") == case["expected_decision_roles"],
            "one_final_question": (not result["valid"]) or (result.get("text", "").count("?") == 1 and result.get("text", "").rstrip().endswith(case["candidate"]["final_question"])),
        }
        entry = {"case_id": case["case_id"], "valid": result["valid"], "errors": result["errors"], "text": result["text"], "manual_review_required": result["manual_review_required"], "metadata": result["metadata"], "checks": checks}
        assert_sanitized(entry, visible_only=True)
        report.append(entry)
    return report


def validate_or_raise(report: list[dict[str, Any]], matrix: list[dict[str, Any]]) -> None:
    if len(report) != len(matrix):
        raise ValueError("report case count mismatch")
    for item in report:
        if not all(item["checks"].values()):
            raise ValueError(f"{item['case_id']}: matrix checks failed: {item['checks']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run isolated shortlist composer hypothesis matrix.")
    parser.add_argument("--case", help="Run a single case_id")
    parser.add_argument("--print-model-input", action="store_true", help="Print sanitized model input for --case")
    args = parser.parse_args(argv)
    matrix = _load_json(MATRIX_PATH)
    if args.case:
        matrix = [case for case in matrix if case["case_id"] == args.case]
        if not matrix:
            print("unknown case_id", file=sys.stderr)
            return 2
    if args.print_model_input:
        if len(matrix) != 1:
            print("--print-model-input requires exactly one --case", file=sys.stderr)
            return 2
        composer = _load_composer()
        json.dump(composer.build_model_input(matrix[0]["input"]), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    report = generate_report(matrix)
    validate_or_raise(report, matrix)
    print(f"OK cases={len(report)} valid={sum(1 for item in report if item['valid'])} manual_review=required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
