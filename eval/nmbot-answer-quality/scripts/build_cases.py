#!/usr/bin/env python3
"""Build promptfoo test cases from nmbot prepared live-run rows JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[3]
EVAL_ROOT = Path(__file__).resolve().parents[1]
VERSIONED_DIR = EVAL_ROOT / "tests" / "versions"


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def build_cases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    for row in rows:
        case = str(row.get("case") or "unknown")
        version = str(row.get("version") or "unknown")
        pm = row.get("prompt_master_verdict")
        if isinstance(pm, str):
            try:
                pm = json.loads(pm)
            except Exception:
                pm = {}
        if not isinstance(pm, dict):
            pm = {}
        pm = dict(pm)
        pm["scenario"] = case
        tests.append(
            {
                "description": f"{version} / {case}",
                "vars": {
                    "version": version,
                    "case": case,
                    "scenario": str(row.get("scenario") or case or pm.get("scenario") or "unknown"),
                    "query": str(row.get("query") or row.get("command") or ""),
                    "mcp": str(row.get("mcp") or row.get("search") or ""),
                    "response": str(row.get("response") or ""),
                    "warnings": str(row.get("warnings") or ""),
                    "facts_count": int(row.get("facts_count") or 0),
                    "visible_count": int(row.get("visible_count") or 0),
                    "facts_names": str(row.get("facts_names") or ""),
                    "visible_names": str(row.get("visible_names") or ""),
                    "evaluation": json.dumps(pm, ensure_ascii=False),
                    "score": int(pm.get("score") or 0) if isinstance(pm.get("score"), (int, float)) else 0,
                    "prompt_master_verdict": json.dumps(pm, ensure_ascii=False),
                },
                "metadata": {
                    "case": case,
                    "version": version,
                },
            }
        )
    return tests


def _write_yaml(path: Path, tests: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(tests, fh, allow_unicode=True, sort_keys=False, width=120)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build promptfoo cases from live-run rows JSONL")
    parser.add_argument(
        "rows_jsonl",
        nargs="?",
        default=str(ROOT / "logs/live_model_run_2026-07-04_rerun_115218.rows.v2.jsonl"),
        help="Prepared rows JSONL from scripts/live_run_table_validator.py",
    )
    parser.add_argument(
        "--out",
        default=str(EVAL_ROOT / "tests/live-run-cases.yaml"),
        help="Output promptfoo tests YAML",
    )
    parser.add_argument(
        "--archive-dir",
        default=str(VERSIONED_DIR),
        help="Directory for versioned promptfoo test sets",
    )
    args = parser.parse_args()

    rows_path = Path(args.rows_jsonl)
    out_path = Path(args.out)
    archive_dir = Path(args.archive_dir)
    rows = _read_rows(rows_path)
    tests = build_cases(rows)
    _write_yaml(out_path, tests)

    by_version: dict[str, list[dict[str, Any]]] = {}
    for test in tests:
        version = str(test.get("metadata", {}).get("version") or "unknown")
        by_version.setdefault(version, []).append(test)

    archive_dir.mkdir(parents=True, exist_ok=True)
    for version, version_tests in by_version.items():
        archive_path = archive_dir / f"live-run-cases.{version}.yaml"
        _write_yaml(archive_path, version_tests)

    versions = ", ".join(sorted(by_version.keys()))
    print(f"BUILT promptfoo cases: rows={len(tests)} versions={versions} out={out_path} archive_dir={archive_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
