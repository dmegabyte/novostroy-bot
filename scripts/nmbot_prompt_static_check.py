#!/usr/bin/env python3
"""Static prompt-master-style checks for local nmbot presenter prompts.

No model calls, no network, no production access. The checks only compare prompt
text against structural boundaries expected from the presenter layer.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"

REQUIRED_PATTERNS: dict[str, str] = {
    "decision_context_only": r"только\s+`?decision_context`?|единственн\w+\s+источник",
    "no_tools_or_search": r"не\s+(?:вызывай|предлагай).*?(?:mcp|поиск|crm|инструмент)",
    "matched_only": r"только\s+из\s+`?decision_context\.matched`?|вне\s+`?matched`?",
    "allowed_claims": r"allowed_claims",
    "exactly_one_question": r"ровно\s+один\s+вопрос",
    "strict_json": r"строго\s+валидн\w+\s+json",
    "runtime_response": r'"response"',
    "runtime_params": r'"params"',
    "runtime_visible_options": r'"visible_options"',
    "runtime_final_question": r'"final_question"|`final_question`',
}

FORBIDDEN_ROUTING_PATTERNS: dict[str, str] = {
    "route_selection": r"выбер\w+\s+маршрут|routing|route",
    "scenario_selection": r"выбер\w+\s+сценар|scenario",
    "filtering_policy": r"фильтр\w+\s+вариант|filtering|hard\s+constraints.*проверь",
    "mcp_instruction": r"запусти\s+mcp|вызови\s+mcp|mcp_servers",
}

SEARCH_REQUIRED_PATTERNS: dict[str, str] = {
    "mcp_get_flat_info": r"mcp.*get_flat_info|get_flat_info.*mcp",
    "strict_json": r"строг\w+\s+json|только\s+строг\w+\s+json",
    "legacy_shape": r'"action".*"target".*"search_policy".*"facts".*"near".*"missing".*"params"',
    "hard_envelope": r"search_contract_envelope.*constraints\.hard|constraints\.hard.*search_contract_envelope",
    "exact_facts": r"facts\[\].*точн|точн\w+.*facts\[\]",
    "near_why_close": r"near\[\].*why_close|why_close.*near\[\]",
    "location_evidence": r"location.*district.*metro|district.*metro.*location",
    "price_evidence": r"price_min.*price_range|price_range.*price_min",
}

SEARCH_FORBIDDEN_PATTERNS: dict[str, str] = {
    "presenter_customer_prose": r"ответь\s+клиенту|напиши\s+клиенту|текст\s+клиенту|response",
    "operator_routing": r"operator_contact|оператор|менеджер|специалист",
    "recovery_routing": r"recover_dialogue|восстанов\w+\s+диалог|recovery",
    "current_options_routing": r"current_options|текущ\w+\s+вариант",
}


def load_prompt(name_or_path: str) -> str:
    path = Path(name_or_path)
    if not path.is_absolute():
        path = PROMPTS / name_or_path
    if path.suffix == "":
        path = path.with_suffix(".txt")
    return path.read_text(encoding="utf-8")


def evaluate_prompt(candidate: str, baseline: str | None = None) -> dict[str, Any]:
    lowered = candidate.lower()
    required = {
        name: bool(re.search(pattern, lowered, flags=re.I | re.S))
        for name, pattern in REQUIRED_PATTERNS.items()
    }
    forbidden_hits = [
        name
        for name, pattern in FORBIDDEN_ROUTING_PATTERNS.items()
        if re.search(pattern, lowered, flags=re.I | re.S)
    ]
    result: dict[str, Any] = {
        "line_count": len(candidate.splitlines()),
        "char_count": len(candidate),
        "required": required,
        "forbidden_hits": forbidden_hits,
        "ok": all(required.values()) and not forbidden_hits,
    }
    if baseline is not None:
        result["baseline_line_count"] = len(baseline.splitlines())
        result["no_longer_than_baseline"] = result["line_count"] <= result["baseline_line_count"]
        result["ok"] = bool(result["ok"] and result["no_longer_than_baseline"])
    return result


def evaluate_search_prompt(candidate: str) -> dict[str, Any]:
    lowered = candidate.lower()
    required = {
        name: bool(re.search(pattern, lowered, flags=re.I | re.S))
        for name, pattern in SEARCH_REQUIRED_PATTERNS.items()
    }
    forbidden_hits = [
        name
        for name, pattern in SEARCH_FORBIDDEN_PATTERNS.items()
        if re.search(pattern, lowered, flags=re.I | re.S)
    ]
    return {
        "kind": "search",
        "line_count": len(candidate.splitlines()),
        "char_count": len(candidate),
        "required": required,
        "forbidden_hits": forbidden_hits,
        "ok": all(required.values()) and not forbidden_hits,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Static checks for nmbot prompt boundaries")
    parser.add_argument("candidate", help="prompt name/path, e.g. four_layer_presenter_v2")
    parser.add_argument("--baseline", help="optional baseline prompt name/path")
    parser.add_argument("--kind", choices=("presenter", "search"), default="presenter", help="prompt boundary kind")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.kind == "search":
        if args.baseline:
            raise SystemExit("--baseline is only supported for presenter checks")
        report = evaluate_search_prompt(load_prompt(args.candidate))
    else:
        report = evaluate_prompt(
            load_prompt(args.candidate),
            load_prompt(args.baseline) if args.baseline else None,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
