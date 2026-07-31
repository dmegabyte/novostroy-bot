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
PROJECT_ROOT = ROOT.parents[1]
MATRIX_PATH = ROOT / "answer_composer_matrix.json"
REPORT_PATH = ROOT / "answer_composer_matrix_report.json"
MD_REPORT_PATH = PROJECT_ROOT / "reports" / "FIELD_SALES_REGISTRY_ANSWER_COMPOSER_MATRIX_20260721.md"

CASE_KEYS = {
    "case_id",
    "scenario",
    "input",
    "candidate",
    "expected_valid",
    "expected_used_field_ids",
    "expected_used_combination_ids",
}
FORBIDDEN_KEY_RE = re.compile(r"(?:phone|email|contact|seller|callback|prompt|model|payload|user_text|raw|source_field)", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?\d[\s().-]*){10,}")
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
INTERNAL_TERM_RE = re.compile(r"\b(?:MCP|JSON|payload|diagnostics|registry|field_id|source_field|evidence|canonical|prompt|model|schema|trace|OptionCard|enum|карточк\w*|данн\w*|контекст\w*|подтвержд[её]н\w*)\b", re.IGNORECASE)


def _load_simulator():
    spec = importlib.util.spec_from_file_location("answer_composer_simulator", ROOT / "answer_composer_simulator.py")
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("answer_composer_simulator loader is missing")
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def assert_no_forbidden_content(value: Any, *, allow_internal_result_keys: bool = False, check_internal_terms: bool = True) -> None:
    allowed_keys = {"case_id", "valid", "errors", "text", "manual_review_required", "metadata", "checks", "used_field_ids", "used_combination_ids"}
    for item in _walk(value):
        if isinstance(item, str):
            if EMAIL_RE.search(item) or PHONE_RE.search(item) or URL_RE.search(item):
                raise ValueError("PII/contact/url-like content is forbidden")
            if check_internal_terms and INTERNAL_TERM_RE.search(item):
                raise ValueError("internal term leak is forbidden")
        elif not allow_internal_result_keys or str(item) not in allowed_keys:
            if FORBIDDEN_KEY_RE.search(str(item)):
                raise ValueError("forbidden raw/internal key is forbidden")


def _final_question_count(text: str) -> int:
    return text.count("?")


def generate_report(matrix: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    simulator = _load_simulator()
    cases = matrix if matrix is not None else _load_json(MATRIX_PATH)
    if not isinstance(cases, list):
        raise ValueError("matrix root must be a list")
    report: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != CASE_KEYS:
            raise ValueError("matrix case shape mismatch")
        assert_no_forbidden_content(case["input"], check_internal_terms=False)
        assert_no_forbidden_content(case["candidate"])
        result = simulator.simulate(case["input"], case["candidate"])
        checks = {
            "expected_valid": result["valid"] is case["expected_valid"],
            "manual_review_required": result.get("manual_review_required") is True,
            "used_field_ids": result.get("metadata", {}).get("used_field_ids") == case["expected_used_field_ids"],
            "used_combination_ids": result.get("metadata", {}).get("used_combination_ids") == case["expected_used_combination_ids"],
            "one_final_question": result.get("valid") is True and _final_question_count(result.get("text", "")) == 1 and result.get("text", "").rstrip().endswith(case["candidate"]["final_question"]),
        }
        entry = {
            "case_id": case["case_id"],
            "valid": result["valid"],
            "errors": result["errors"],
            "text": result["text"],
            "manual_review_required": result["manual_review_required"],
            "metadata": result["metadata"],
            "checks": checks,
        }
        assert_no_forbidden_content(entry, allow_internal_result_keys=True)
        report.append(entry)
    return report


def markdown_report(report: list[dict[str, Any]]) -> str:
    descriptions = {
        "family": "Семейный baseline: школа, детский сад и площадка без обещаний мест или качества.",
        "financing": "Бюджетный сценарий: ставка, первоначальный взнос, рассрочка и цена без обещания одобрения.",
        "parking": "Паркинг: цена и число мест как буквальные ориентиры без брони или гарантии места.",
        "investment": "Инвестиционный сценарий: только буквальные счётчики ЕГРН и витрины без прогноза.",
        "lot": "Конкретный лот: цена, площадь, этаж, комнатность, отделка и статус без обещаний доступности.",
    }
    lines = [
        "# Field Sales Registry Answer Composer Matrix — 2026-07-21",
        "",
        "Offline composer-only matrix. Все кейсы synthetic и PII-free; модель, сеть, MCP, SSH, eval и runtime не вызываются.",
        "",
        "## Summary",
        "",
        f"- Cases: {len(report)}",
        f"- Valid: {sum(1 for item in report if item['valid'])}/{len(report)}",
        "- Manual review: required for every case",
        "",
    ]
    for item in report:
        scenario = item["case_id"].replace("answer_composer_", "")
        lines.extend([
            f"## {item['case_id']}",
            "",
            descriptions.get(scenario, "Synthetic composer scenario."),
            "",
            "Client-visible text:",
            "",
            item["text"],
            "",
            "Safety note: текст собран только через offline simulator; `manual_review_required=true` остаётся обязательным.",
            "",
        ])
    lines.extend([
        "## Source refs",
        "",
        "- `field_sales_registry/v1/answer_composer_simulator.py`",
        "- `field_sales_registry/v1/run_answer_composer_matrix.py`",
        "- `field_sales_registry/v1/answer_composer_matrix.json`",
        "- `field_sales_registry/v1/answer_composer_matrix_report.json`",
        "- `field_sales_registry/v1/answer_composer_prompt.md`",
        "- `field_sales_registry/v1/structured_finance_schema.json`",
        "- `docs/IDEAL_IRINA_UX.md`",
        "- `docs/PROMPT_ARCHITECTURE.md`",
        "",
    ])
    text = "\n".join(lines)
    if EMAIL_RE.search(text) or PHONE_RE.search(text) or URL_RE.search(text):
        raise ValueError("markdown report contains contact/url-like content")
    return text


def validate_or_raise(report: list[dict[str, Any]], matrix: list[dict[str, Any]]) -> None:
    by_case = {item["case_id"]: item for item in report}
    if len(report) != len(matrix) or len(by_case) != len(matrix):
        raise ValueError("report case count mismatch")
    for case in matrix:
        item = by_case[case["case_id"]]
        if case["expected_valid"] and not item["valid"]:
            raise ValueError(f"{case['case_id']}: expected valid but simulator failed with {item['errors']}")
        if item["manual_review_required"] is not True:
            raise ValueError(f"{case['case_id']}: manual review must be required")
        if not all(item["checks"].values()):
            raise ValueError(f"{case['case_id']}: matrix checks failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline Answer Composer scenario matrix.")
    parser.add_argument("--write", action="store_true", help="Regenerate committed JSON and Markdown reports")
    args = parser.parse_args(argv)
    matrix = _load_json(MATRIX_PATH)
    report = generate_report(matrix)
    validate_or_raise(report, matrix)
    stored = _load_json(REPORT_PATH) if REPORT_PATH.exists() else None
    if args.write:
        _dump_json(REPORT_PATH, report)
        MD_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        MD_REPORT_PATH.write_text(markdown_report(report), encoding="utf-8")
    elif stored != report:
        print("FAIL answer_composer_matrix_report.json does not match regeneration", file=sys.stderr)
        return 1
    print(f"OK cases={len(report)} valid={sum(1 for item in report if item['valid'])} manual_review=required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
