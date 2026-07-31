#!/usr/bin/env python3
"""Build label-blind CC2 navigation/context-gate predictions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import project_context_gate_core as gate
import project_navigation_core as navigation
from project_adapter_core import AdapterError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = PROJECT_ROOT / "tests" / "fixtures" / "cc2_context_acceptance_inputs_v1.json"
DEFAULT_OUTPUT = Path("/tmp/opencode/cc2_context_acceptance_predictions_v1.json")
MAX_SOURCES = 2
MAX_LINES = 80
MAX_CHARS = 8000


def _ensure_tmp_output(path: Path) -> None:
    resolved = path.resolve()
    if not str(resolved).startswith("/tmp/"):
        raise ValueError("predictions output must be under /tmp")
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return
    raise ValueError("predictions output must not be inside the project tree")


def _load_cases(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list):
        raise ValueError("input fixture must contain cases list")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in cases:
        if not isinstance(item, dict):
            raise ValueError("case must be an object")
        case_id = str(item.get("case_id") or "")
        code = str(item.get("diagnostic_code") or "")
        if not case_id or case_id in seen:
            raise ValueError("case_id must be unique and non-empty")
        seen.add(case_id)
        out.append({"case_id": case_id, "diagnostic_code": code})
    return out


def _abstain(case: dict[str, str], *, route: str, reason: str) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "diagnostic_code": case["diagnostic_code"],
        "selected": False,
        "abstain": True,
        "route": route,
        "reason": reason,
        "owner_source": None,
        "owner_symbol": None,
        "budget_status": None,
        "stop_reason": None,
        "selected_source_count": 0,
        "lines_loaded": 0,
        "characters_loaded": 0,
        "local_read_only": True,
        "production_proof": False,
    }


def predict_case(case: dict[str, str]) -> dict[str, Any]:
    code = case["diagnostic_code"]
    try:
        nav = navigation.navigate(code, project_id="cc2")
    except (navigation.NavigationError, AdapterError, ValueError) as exc:
        return _abstain(case, route="error", reason=exc.__class__.__name__)
    results = nav.get("results") if isinstance(nav.get("results"), list) else []
    first = results[0] if results and isinstance(results[0], dict) else {}
    spec = first.get("target_spec") if isinstance(first.get("target_spec"), dict) else None
    if nav.get("route") != "diagnostic" or nav.get("abstain") or first.get("kind") != "diagnostic_code" or not spec:
        return _abstain(case, route=str(nav.get("route") or "mixed"), reason="no_exact_diagnostic_route")
    try:
        ctx = gate.run_gate(
            "label-blind acceptance harness",
            project_id="cc2",
            evidence_type=str(spec["target_kind"]),
            definition_of_done="exact diagnostic owner symbol under strict local budget",
            target_kind=str(spec["target_kind"]),
            target=str(spec["target"]),
            target_owner=str(spec.get("target_owner") or ""),
            max_sources=MAX_SOURCES,
            max_lines=MAX_LINES,
            max_chars=MAX_CHARS,
        )
    except (gate.GateError, AdapterError, navigation.NavigationError, ValueError) as exc:
        return _abstain(case, route="gate_error", reason=exc.__class__.__name__)
    trace = ctx.get("trace") if isinstance(ctx.get("trace"), dict) else {}
    return {
        "case_id": case["case_id"],
        "diagnostic_code": code,
        "selected": True,
        "abstain": False,
        "route": str(nav.get("route")),
        "owner_source": str(spec.get("owner_path") or spec.get("target_owner") or ""),
        "owner_symbol": str(spec.get("target") or ""),
        "budget_status": str(ctx.get("budget_status") or ""),
        "stop_reason": str(ctx.get("stop_reason") or ""),
        "selected_source_count": int(trace.get("selected_source_count") or 0),
        "lines_loaded": int(trace.get("lines_loaded") or 0),
        "characters_loaded": int(trace.get("characters_loaded") or 0),
        "local_read_only": bool(nav.get("local_read_only")) and bool(ctx.get("local_read_only")),
        "production_proof": bool(nav.get("production_proof")) or bool(ctx.get("production_proof")),
    }


def generate(input_path: Path, output_path: Path) -> dict[str, Any]:
    _ensure_tmp_output(output_path)
    predictions = [predict_case(case) for case in _load_cases(input_path)]
    payload = {
        "schema_version": 1,
        "project_id": "cc2",
        "harness": "cc2_context_acceptance_v1",
        "privacy": "metadata_only_no_source_text",
        "budgets": {"max_sources": MAX_SOURCES, "max_lines": MAX_LINES, "max_chars": MAX_CHARS},
        "predictions": predictions,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        payload = generate(args.inputs, args.output)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(args.output), "cases": len(payload["predictions"])}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
