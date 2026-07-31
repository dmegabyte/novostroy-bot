#!/usr/bin/env python3
"""Isolated prompt harness for V2 MCP search prompt experiments.

Default/static mode (`--fixture-only`) performs no network calls. Live mode is
explicit (`--case ID` or `--all` without `--fixture-only`) and runs cases one by
one with first-failure stop for `--all`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Awaitable, Callable


EVAL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_ROOT.parents[1]
PROMPT_PATH = PROJECT_ROOT / "prompts" / "v2_search_mcp.txt"
CASES_PATH = EVAL_ROOT / "cases.json"
DEFAULT_TIMEOUT = 90
MAX_ENRICHMENT_OPTIONS = 5

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nmbot_v2 import search_contract as contract  # noqa: E402
from nmbot_v2.contracts import to_jsonable  # noqa: E402
from nmbot_v2.search_enrichment import validate_with_bounded_enrichment  # noqa: E402
from scripts.nmbot_v2_search_mcp_probe import (  # noqa: E402
    _request_from_scenario,
    gateway_request,
    load_env,
    load_fixture,
    scenarios_by_id,
)


GatewayFunc = Callable[[dict[str, Any], int], Awaitable[tuple[str, dict[str, Any]]]]


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def load_cases() -> list[dict[str, Any]]:
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("cases.json must be a list")
    return data


def cases_by_id(cases: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    loaded = cases if cases is not None else load_cases()
    return {str(item["id"]): item for item in loaded}


def request_for_case(case: dict[str, Any], fixture: dict[str, Any] | None = None) -> contract.V2SearchRequest:
    fixture_data = fixture or load_fixture()
    scenario = scenarios_by_id(fixture_data)[case["fixture_scenario_id"]]
    canonical = _request_from_scenario(fixture_data, scenario)
    search_goal = dict(canonical.search_goal)
    search_goal["query_summary"] = case["query"]
    return replace(canonical, search_goal=search_goal)


def build_request_data_for_case(case: dict[str, Any], *, prompt: str | None = None, fixture: dict[str, Any] | None = None) -> dict[str, Any]:
    data = contract.build_request_data(
        request_for_case(case, fixture),
        prompt=prompt if prompt is not None else load_prompt(),
        model=contract.SEARCH_MODEL,
    )
    data.pop("external_api_key", None)
    return data


def _names(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items[:5]:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("alias") or item.get("label") or "").strip()
            if name:
                names.append(name[:100])
    return names


def _bounded_missing(items: Any) -> list[Any]:
    if not isinstance(items, list):
        return []
    bounded: list[Any] = []
    for item in items[:20]:
        if isinstance(item, (str, int, float, bool)) or item is None:
            bounded.append(item)
        elif isinstance(item, dict):
            bounded.append({str(k)[:80]: str(v)[:160] for k, v in list(item.items())[:8]})
        else:
            bounded.append(str(item)[:160])
    return bounded


def _safe_gateway_meta(meta: dict[str, Any]) -> dict[str, Any]:
    allowed = {"ok", "http_status", "error", "metadata_keys"}
    return {key: meta[key] for key in allowed if key in meta}


def _safe_enrichment_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Keep bounded eval-only enrichment telemetry without raw payloads."""
    allowed_scalars = {
        "enabled",
        "applied",
        "count",
        "applied_count",
        "trigger",
        "recovery",
        "confirmed_count",
        "reason",
        "skipped",
    }
    safe: dict[str, Any] = {
        key: meta[key]
        for key in allowed_scalars
        if key in meta and (isinstance(meta[key], (str, int, float, bool)) or meta[key] is None)
    }
    fields = meta.get("fields")
    if isinstance(fields, list):
        safe["fields"] = [str(item)[:80] for item in fields[:10]]
    items = meta.get("items")
    if isinstance(items, list):
        safe_items: list[dict[str, Any]] = []
        for item in items[:MAX_ENRICHMENT_OPTIONS]:
            if not isinstance(item, dict):
                continue
            safe_item = {
                key: item[key]
                for key in ("idx", "applied", "source", "skipped")
                if key in item and isinstance(item[key], (str, int, bool))
            }
            if safe_item:
                safe_items.append(safe_item)
        safe["items"] = safe_items
    evidence = meta.get("post_validation_evidence")
    if isinstance(evidence, list):
        safe["post_validation_evidence_count"] = min(len(evidence), 3)
    return safe


def validate_fixture_only(case: dict[str, Any], fixture: dict[str, Any] | None = None) -> dict[str, Any]:
    fixture_data = fixture or load_fixture()
    request = request_for_case(case, fixture_data)
    data = build_request_data_for_case(case, fixture=fixture_data)
    errors: list[str] = []
    static = contract.validate_fixture_case(request)
    errors.extend(static["errors"])
    if not PROMPT_PATH.exists():
        errors.append("prompt_path_missing")
    if data.get("_payload_stage") != "main_search":
        errors.append("payload_stage_mismatch")
    if data.get("model") != contract.SEARCH_MODEL:
        errors.append("model_mismatch")
    if data.get("mcp_servers") != [contract.MCP_ALIAS]:
        errors.append("mcp_alias_mismatch")
    if data.get("system_prompt") != load_prompt():
        errors.append("test_prompt_not_loaded")
    if "external_api_key" in data:
        errors.append("secret_key_leaked_into_request_data")
    if case["query"] not in data.get("query", ""):
        errors.append("case_query_missing_from_request")
    return {
        "case": case["id"],
        "query": case["query"],
        "ok": not errors,
        "network": False,
        "counts": {"facts": 0, "near": 0, "missing": 0},
        "errors": errors,
        "fact_names": [],
        "near_names": [],
        "missing": [],
        "gateway_meta": {},
        "elapsed_seconds": 0.0,
    }


async def run_case(case: dict[str, Any], *, timeout: int, gateway_func: GatewayFunc | None = None) -> dict[str, Any]:
    fixture = load_fixture()
    request = request_for_case(case, fixture)
    data = build_request_data_for_case(case, fixture=fixture)
    started = time.monotonic()
    raw, meta = await (gateway_func or gateway_request)(data, timeout)
    gateway_ok = bool(meta.get("ok", True))
    parsed, parse_errors = contract.parse_strict_json(raw)
    enrichment_meta: dict[str, Any] = {"enabled": False, "applied": False, "reason": "not_invoked"}
    if parsed is None:
        validation = {"ok": False, "errors": parse_errors, "counts": {"facts": 0, "near": 0, "missing": 0}}
        normalized: dict[str, Any] = {"facts": [], "near": [], "missing": []}
        enrichment_meta["reason"] = "initial_parse_failed"
    else:
        normalized = contract.normalize_search_output(parsed, request)
        if gateway_ok:
            selected_gateway = gateway_func or gateway_request

            async def enrichment_gateway(request_data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
                return await selected_gateway(request_data, timeout)

            enriched_result, validation, enrichment_meta = await validate_with_bounded_enrichment(
                normalized,
                request,
                enrichment_gateway,
                max_options=MAX_ENRICHMENT_OPTIONS,
                timeout=timeout,
            )
            if enriched_result is not None:
                normalized = to_jsonable(enriched_result)
                normalized.setdefault("diagnostics", {})
        else:
            validation = contract.validate_search_output(normalized, request)
            enrichment_meta["reason"] = "initial_gateway_failed"
    contract_ok = bool(validation.get("ok") and gateway_ok)
    candidate_count = int(validation.get("counts", {}).get("facts", 0)) + int(validation.get("counts", {}).get("near", 0))
    min_candidates = int(case.get("min_candidates", 0))
    found_options = candidate_count >= min_candidates
    errors = list(validation.get("errors") or [])
    if not gateway_ok:
        errors.append("gateway_not_ok")
    if contract_ok and not found_options:
        errors.append(f"insufficient_candidates:{candidate_count}<{min_candidates}")
    return {
        "case": case["id"],
        "query": case["query"],
        "ok": bool(contract_ok and found_options),
        "contract_ok": contract_ok,
        "found_options": found_options,
        "network": True,
        "counts": validation.get("counts", {"facts": 0, "near": 0, "missing": 0}),
        "errors": errors,
        "fact_names": _names(normalized.get("facts")),
        "near_names": _names(normalized.get("near")),
        "missing": _bounded_missing(normalized.get("missing")),
        "gateway_meta": _safe_gateway_meta(meta),
        "enrichment": _safe_enrichment_meta(enrichment_meta),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def selected_cases(args: argparse.Namespace, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = cases_by_id(cases)
    if args.case and args.all:
        raise SystemExit("choose either --case or --all")
    if args.case:
        if args.case not in indexed:
            raise SystemExit(f"unknown case: {args.case}")
        return [indexed[args.case]]
    if args.all or args.fixture_only:
        return cases
    raise SystemExit("pass --fixture-only, --case ID, or --all")


async def async_main(argv: list[str] | None = None, *, gateway_func: GatewayFunc | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated V2 MCP search prompt test harness")
    parser.add_argument("--fixture-only", action="store_true", help="static validation only; makes no network calls")
    parser.add_argument("--case", help="single case id")
    parser.add_argument("--all", action="store_true", help="run all cases sequentially, stopping on first failure")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--output", help="optional path for bounded JSON result")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")

    cases = load_cases()
    chosen = selected_cases(args, cases)
    fixture = load_fixture()
    results: list[dict[str, Any]] = []
    if args.fixture_only:
        for case in chosen:
            result = validate_fixture_only(case, fixture)
            results.append(result)
            if args.all and not result["ok"]:
                break
    else:
        load_env()
        for case in chosen:
            result = await run_case(case, timeout=args.timeout, gateway_func=gateway_func)
            results.append(result)
            if args.all and not result["ok"]:
                break

    status = {
        "ok": all(item["ok"] for item in results),
        "network": not args.fixture_only,
        "prompt_path": str(PROMPT_PATH.relative_to(PROJECT_ROOT)),
        "model": contract.SEARCH_MODEL,
        "mcp_alias": contract.MCP_ALIAS,
        "payload_stage": "main_search",
        "results": results,
    }
    rendered = json.dumps(status, ensure_ascii=False, sort_keys=True, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
