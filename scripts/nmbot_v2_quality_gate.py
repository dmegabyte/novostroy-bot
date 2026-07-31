#!/usr/bin/env python3
"""V2 search+answer quality harness.

No network, no model calls, no MCP calls.  The script feeds deterministic MCP-like
fixture outputs through the V2 search contract normalizer, converts them to
SearchResult/OptionCard, renders the current response layer, and evaluates the
customer-facing copy with nmbot_v2.quality.

Live mode is read-only and opt-in via ``--live``.  It makes one gateway/MCP/model
call per selected case, validates the strict JSON search contract, renders the
same V2 answer layer, then runs the same deterministic quality evaluator.  Safe
stdout intentionally excludes raw gateway payloads, credentials, task ids and
provider raw responses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from nmbot_v2.card_normalizer import normalize_card, normalize_search_result  # noqa: E402
from nmbot_v2.contracts import ExecutionResult, SearchResult, SemanticPlan, Stage, StateDelta, to_jsonable  # noqa: E402
from nmbot_v2.quality import QualityReport, build_quality_profile, evaluate_scenario, report_table  # noqa: E402
from nmbot_v2.response import build_final_response_plan, render_response  # noqa: E402
from nmbot_v2.prompt_provenance import build_prompt_provenance, identity_from_path, merge_prompt_provenance  # noqa: E402
from nmbot_v2.response_composer import PROMPT_PATH as RESPONSE_COMPOSER_PROMPT, PROVIDER_RETRY_MODEL, assemble_composed_response, build_response_brief, compose_response_async, configured_response_prompt_provenance, parse_composer_json, request_payload as build_response_payload  # noqa: E402
from nmbot_v2.search_enrichment import enrich_search_result_top_options, validate_with_bounded_enrichment  # noqa: E402
from nmbot_v2.search_contract import V2SearchRequest, available_fact_fields, normalize_search_output, parse_strict_json, validate_search_output  # noqa: E402
from nmbot_v2.state import ConversationState  # noqa: E402
import nmbot_v2_search_mcp_probe as search_probe  # noqa: E402
from nmbot_release_identity import current_release_id  # noqa: E402


CONTRACT_FIXTURE = ROOT / "tests" / "fixtures" / "v2_search_mcp_contract.json"
QUALITY_FIXTURE = ROOT / "tests" / "fixtures" / "nmbot_v2_quality_scenarios.json"
SEARCH_PROMPT = ROOT / "prompts" / "v2_search_mcp.txt"
QUALITY_RUN_SCHEMA = "nmbot.quality_run.v1"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def file_identity(path: Path) -> dict[str, str]:
    import hashlib

    return {"source": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def quality_prompt_provenance(*, live: bool) -> dict[str, Any]:
    if live:
        return merge_prompt_provenance(
            build_prompt_provenance([identity_from_path("search", "prompts/v2_search_mcp.txt", SEARCH_PROMPT)], coverage="complete"),
            build_prompt_provenance([identity_from_path("response_composer", "prompts/v2_response_composer.txt", RESPONSE_COMPOSER_PROMPT)], coverage="complete"),
            coverage="complete",
        ) or build_prompt_provenance([], coverage="partial")
    return merge_prompt_provenance(
        build_prompt_provenance([identity_from_path("search", "prompts/v2_search_mcp.txt", SEARCH_PROMPT, usage="configured")], coverage="configured_only"),
        configured_response_prompt_provenance(usage="configured", coverage="configured_only"),
        coverage="configured_only",
    ) or build_prompt_provenance([], coverage="partial")


def scenario_map() -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in load_json(CONTRACT_FIXTURE).get("scenarios", [])}


def quality_records() -> list[dict[str, Any]]:
    return list(load_json(QUALITY_FIXTURE).get("records", []))


def request_from_contract(scenario: Mapping[str, Any]) -> V2SearchRequest:
    constraints = scenario.get("constraints") if isinstance(scenario.get("constraints"), Mapping) else {}
    preferences = dict(constraints.get("preferences") or {})
    allowed = {
        "format", "rooms_preference", "budget_preference", "location_preference",
        "infrastructure_preference", "transport_preference", "finance_preference", "sort_hint",
    }
    accepted_preferences = {k: v for k, v in preferences.items() if k in allowed}
    ignored = sorted(set(preferences) - allowed)
    viewpoint = str(scenario.get("response_viewpoint") or "life")
    base_viewpoint = scenario.get("base_viewpoint")
    extra = list(scenario.get("expected_field_priorities_include") or []) + list(scenario.get("also_expected_overlay_preserves") or [])
    return V2SearchRequest(
        search_goal=dict(scenario.get("search_goal") or {}),
        requested_hard=dict(constraints.get("requested_hard") or {}),
        effective_hard=dict(constraints.get("effective_hard") or {}),
        preferences=accepted_preferences,
        relaxation_audit=list(constraints.get("relaxation_audit") or []),
        response_viewpoint=viewpoint,
        base_viewpoint=str(base_viewpoint) if base_viewpoint else None,
        available_fact_fields=available_fact_fields(viewpoint, str(base_viewpoint) if base_viewpoint else None, extra),
        count=int(scenario.get("count") or 3),
        ignored_preferences=ignored,
    )


def _price_text(item: Mapping[str, Any]) -> tuple[str | None, int | None]:
    if item.get("price"):
        return str(item["price"]), None
    for key in ("min_price", "novos.min_price", "price1", "price_s", "price_n"):
        value = item.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return None, int(value)
    return None, None


def _infrastructure(item: Mapping[str, Any]) -> list[str]:
    names = {
        "school": "школа",
        "kindergarten": "детский сад",
        "park_near": "парк",
        "water_near": "вода рядом",
        "yard_without_cars": "двор без машин",
        "children_ground": "детская площадка",
        "sports_ground": "спортивная площадка",
        "security": "охрана",
        "territory": "благоустроенная территория",
        "parking": "паркинг",
    }
    return [label for key, label in names.items() if item.get(key)]


def _room_formats(item: Mapping[str, Any]) -> list[str]:
    raw = item.get("rooms") or item.get("apartment_types") or item.get("ads") or []
    if isinstance(raw, str):
        return [part.strip() for part in raw.replace(",", "|").split("|") if part.strip()][:3]
    if isinstance(raw, (int, float)):
        return [str(int(raw))]
    if isinstance(raw, list):
        out: list[str] = []
        for value in raw:
            if isinstance(value, Mapping) and value.get("rooms") is not None:
                out.append(str(value.get("rooms")))
            elif value is not None and not isinstance(value, Mapping):
                out.append(str(value))
        return out[:3]
    return []


def _card_item(item: Mapping[str, Any], *, is_near: bool = False) -> dict[str, Any]:
    return to_jsonable(normalize_card(item, is_near=is_near))


def _first_nested(item: Mapping[str, Any], key: str) -> str | None:
    for container in ("ads", "apartment_types"):
        values = item.get(container)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, Mapping) and value.get(key) is not None:
                    return str(value.get(key))
    return None


def search_result_from_output(output: Mapping[str, Any]) -> SearchResult:
    return normalize_search_result(output)


def render_case(case_id: str, record: Mapping[str, Any], request: V2SearchRequest) -> tuple[str, SearchResult, dict[str, Any]]:
    raw = dict(record.get("search_output") or {})
    normalized = normalize_search_output(raw, request)
    result = search_result_from_output(normalized)
    plan = SemanticPlan(operation="search", intent=request.response_viewpoint, facets=["mortgage"] if request.response_viewpoint == "financing" else [])
    response_plan = build_final_response_plan(
        stage=Stage.FIRST_LIST,
        plan=plan,
        execution=ExecutionResult(ok=True, search=result),
        delta=StateDelta(),
        state=ConversationState(active_topic=request.base_viewpoint or request.response_viewpoint),
    )
    return render_response(response_plan), result, normalized


async def compose_case_live(case_id: str, normalized: Mapping[str, Any], request: V2SearchRequest, *, composer_func: Any = None, timeout: int = 90, result: SearchResult | None = None) -> tuple[str, SearchResult, list[str], str, list[dict[str, Any]]]:
    result = result or search_result_from_output(normalized)
    plan = SemanticPlan(operation="search", intent=request.response_viewpoint, facets=["mortgage"] if request.response_viewpoint == "financing" else [])
    response_plan = build_final_response_plan(
        stage=Stage.FIRST_LIST,
        plan=plan,
        execution=ExecutionResult(ok=True, search=result),
        delta=StateDelta(),
        state=ConversationState(active_topic=request.base_viewpoint or request.response_viewpoint),
    )
    brief = build_response_brief(stage=Stage.FIRST_LIST, plan=plan, execution=ExecutionResult(ok=True, search=result), delta=StateDelta(), state=ConversationState(active_topic=request.base_viewpoint or request.response_viewpoint), response_plan=response_plan)
    fallback = render_response(response_plan)
    gateway = composer_func or search_probe.gateway_request
    candidate_text = ""

    async def attempt(attempt_brief, *, repair_errors=(), model="google/gemini-2.5-flash"):
        nonlocal candidate_text
        raw, meta = await gateway(build_response_payload(attempt_brief, repair_errors=repair_errors, model=model), timeout)
        parsed, _ = parse_composer_json(raw)
        if parsed:
            candidate_text = assemble_composed_response(parsed)
        return raw, meta

    composed = await compose_response_async(brief, fallback_text=fallback, composer=attempt, provider_retry_model=PROVIDER_RETRY_MODEL)
    attempts = list(composed.to_meta().get("attempt_summaries") or [])
    if candidate_text and attempts:
        attempts[-1]["candidate_text"] = candidate_text
    return composed.text, result, list(composed.errors), composed.status, attempts


def run_case(case_id: str) -> dict[str, Any]:
    contract_cases = scenario_map()
    records = {str(item["id"]): item for item in quality_records()}
    if case_id not in contract_cases or case_id not in records:
        raise SystemExit(f"unknown case: {case_id}")
    request = request_from_contract(contract_cases[case_id])
    response_text, result, normalized = render_case(case_id, records[case_id], request)
    report = evaluate_scenario(
        scenario_id=case_id,
        response_text=response_text,
        search_result=result,
        # The final SearchResult has already passed post-enrichment validation.
        # Re-validating the pre-recovery wire payload would resurrect a hard
        # evidence error that recovery has already resolved or classified.
        search_output=None,
        search_request=request,
        viewpoint=request.response_viewpoint,
        base_viewpoint=request.base_viewpoint,
    )
    profile = build_quality_profile(
        dimensions=report.dimensions,
        hard_blockers=report.hard_blockers,
        search_ok=not report.search_mcp.startswith("FAIL"),
        evidence="offline",
        composer_status="primary",
    )
    report_dict = report.to_dict()
    report_dict["quality_profile"] = profile
    return {"case": case_id, "ok": report.ok, "score": report.score, "quality_profile": profile, "report": report_dict, "response_text": response_text}


def _safe_counts(counts: Mapping[str, Any] | None = None) -> dict[str, int]:
    source = counts or {}
    return {"facts": int(source.get("facts") or 0), "near": int(source.get("near") or 0), "missing": int(source.get("missing") or 0)}


def _failure_result(case_id: str, *, started: float, layer: str, blockers: list[str], errors: list[str] | None = None) -> dict[str, Any]:
    elapsed = round(time.monotonic() - started, 3)
    profile = build_quality_profile(
        dimensions={"facts": 0, "completeness": 0, "beauty": 0, "scenario_fit": 0, "dialogue": 0},
        hard_blockers=blockers,
        search_ok=False,
        search_errors=list(errors or blockers),
        evidence="live",
        composer_status="fallback" if "composer_degraded_fallback" in blockers else "primary",
        latency_seconds=elapsed,
    )
    report = QualityReport(
        scenario=case_id,
        ok=False,
        score=0,
        verdict="FAIL",
        layer_to_fix=layer,
        search_mcp="FAIL: " + ", ".join((errors or blockers)[:3]),
        card="FAIL: no_search_result",
        response="FAIL: " + ", ".join(blockers[:4]),
        dimensions={"facts": 0, "completeness": 0, "beauty": 0, "scenario_fit": 0, "dialogue": 0},
        hard_blockers=blockers,
        issues=list(errors or blockers),
        quality_profile=profile,
    )
    return {
        "case": case_id,
        "ok": False,
        "elapsed": elapsed,
        "counts": _safe_counts(),
        "score": 0,
        "verdict": "FAIL",
        "hard_blockers": list(blockers),
        "layer_to_fix": layer,
        "response_text": "",
        "report": report.to_dict(),
        "quality_profile": profile,
        "errors": list(errors or blockers),
    }


async def run_live_case(case_id: str, *, timeout: int = search_probe.DEFAULT_TIMEOUT, gateway_func: Any = None, composer_func: Any = None) -> dict[str, Any]:
    contract_cases = scenario_map()
    records = {str(item["id"]): item for item in quality_records()}
    if case_id not in contract_cases or case_id not in records:
        raise SystemExit(f"unknown case: {case_id}")
    fixture = search_probe.load_fixture()
    probe_cases = search_probe.scenarios_by_id(fixture)
    if case_id not in probe_cases:
        raise SystemExit(f"unknown live case: {case_id}")

    started = time.monotonic()
    request = request_from_contract(contract_cases[case_id])
    search_probe.load_env()
    try:
        raw_text, meta = await (gateway_func or search_probe.gateway_request)(search_probe.build_request_data(fixture, probe_cases[case_id]), timeout)
    except Exception as exc:  # noqa: BLE001 - safe summary only, never raw payload/provider body.
        return _failure_result(case_id, started=started, layer="transport/search", blockers=["gateway_network_failed"], errors=[type(exc).__name__])
    if not isinstance(meta, Mapping) or not meta.get("ok", True):
        return _failure_result(case_id, started=started, layer="transport/search", blockers=["gateway_not_ok"], errors=["gateway_not_ok"])

    parsed, parse_errors = parse_strict_json(raw_text)
    if parsed is None:
        return _failure_result(case_id, started=started, layer="search", blockers=["strict_json_invalid"], errors=parse_errors)
    normalized = normalize_search_output(parsed, request)
    async def enrichment_gateway(request_data: dict[str, Any]) -> tuple[Any, Mapping[str, Any] | None]:
        return await (gateway_func or search_probe.gateway_request)(request_data, timeout)

    result, validation, enrichment_meta = await validate_with_bounded_enrichment(
        normalized,
        request,
        enrichment_gateway,
        max_options=3,
        timeout=timeout,
    )
    counts = _safe_counts(validation.get("counts") if isinstance(validation, Mapping) else None)
    if result is None:
        return _failure_result(
            case_id,
            started=started,
            layer="search",
            blockers=["search_contract_invalid"],
            errors=list(validation.get("errors") or []),
        ) | {"counts": counts, "enrichment": enrichment_meta}
    response_text, result, composer_errors, composer_status, composer_attempts = await compose_case_live(case_id, normalized, request, composer_func=composer_func or gateway_func, timeout=timeout, result=result)
    composer_candidate_text = str(composer_attempts[-1].get("candidate_text") or "") if composer_attempts else ""
    report = evaluate_scenario(
        scenario_id=case_id,
        response_text=response_text,
        search_result=result,
        # Live search has already passed the post-enrichment validator above.
        # The initial wire output may intentionally be invalid on an evidence
        # field that recovery later resolves, so it must not be scored again.
        search_output=None,
        search_request=request,
        viewpoint=request.response_viewpoint,
        base_viewpoint=request.base_viewpoint,
    )
    report_dict = report.to_dict()
    if composer_errors:
        report_dict["ok"] = False
        report_dict["score"] = min(int(report_dict["score"]), 8)
        report_dict["verdict"] = "FAIL"
        report_dict["layer_to_fix"] = "response composer"
        report_dict["response"] = "DEGRADED_FALLBACK"
        report_dict["hard_blockers"] = list(dict.fromkeys(list(report_dict["hard_blockers"]) + ["composer_degraded_fallback"]))
        report_dict["issues"] = list(dict.fromkeys(list(report_dict["issues"]) + list(composer_errors)))
    elapsed = round(time.monotonic() - started, 3)
    profile = build_quality_profile(
        dimensions=dict(report_dict.get("dimensions") or {}),
        hard_blockers=list(report_dict.get("hard_blockers") or []),
        search_ok=not str(report_dict.get("search_mcp", "")).startswith("FAIL"),
        search_errors=list(validation.get("errors") or []),
        evidence="live",
        composer_status=composer_status,
        latency_seconds=elapsed,
    )
    report_dict["quality_profile"] = profile
    return {
        "case": case_id,
        "ok": bool(report_dict["ok"]),
        "elapsed": elapsed,
        "counts": counts,
        "score": int(report_dict["score"]),
        "verdict": str(report_dict["verdict"]),
        "hard_blockers": list(report_dict["hard_blockers"]),
        "layer_to_fix": str(report_dict["layer_to_fix"]),
        "response_text": response_text,
        "report": report_dict,
        "quality_profile": profile,
        "errors": list(report_dict["issues"]),
        "composer_attempts": composer_attempts,
        "composer_candidate_text": composer_candidate_text,
        "enrichment": enrichment_meta,
    }


def _selected(args: argparse.Namespace) -> list[str]:
    records = [str(item["id"]) for item in quality_records()]
    if args.case:
        if args.case not in records:
            raise SystemExit(f"unknown case: {args.case}")
        return [args.case]
    if args.all:
        if getattr(args, "start_after", None):
            if args.start_after not in records:
                raise SystemExit(f"unknown --start-after case: {args.start_after}")
            return records[records.index(args.start_after) + 1 :]
        return records
    raise SystemExit("pass --case ID or --all")


def write_report(results: list[dict[str, Any]], *, live: bool = False) -> Path:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / ("nmbot_v2_live_quality_gate.md" if live else "nmbot_v2_quality_gate.md")
    reports = [QualityReport(**item["report"]) for item in results]
    if live:
        answers = [f"### {item['case']}\n\n```text\n{item['response_text']}\n```" for item in results]
        path.write_text("# NMBOT V2 live quality gate\n\n" + report_table(reports) + "\n\n## Answers\n\n" + "\n\n".join(answers) + "\n", encoding="utf-8")
    else:
        samples = []
        for item in results:
            if item["case"] in {"family", "investment", "rental", "family_financing_overlay", "broad_candidates_later_enrichment"}:
                samples.append(f"### {item['case']}\n\n```text\n{item['response_text']}\n```")
        path.write_text("# NMBOT V2 quality gate\n\n" + report_table(reports) + "\n\n## Samples\n\n" + "\n\n".join(samples) + "\n", encoding="utf-8")
    return path


def write_quality_run_artifact(summary: dict[str, Any], *, started_at: str, run_id: str) -> Path:
    logs = ROOT / "logs" / "quality_runs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9TZ]", "", started_at.replace(":", "").replace("-", ""))[:16]
    path = logs / f"{stamp}-{run_id}.json"
    data = json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(logs))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="V2 answer quality gate")
    parser.add_argument("--case", help="single case id")
    parser.add_argument("--all", action="store_true", help="run all 15 cases")
    parser.add_argument("--live", action="store_true", help="make read-only gateway/MCP/model calls instead of fixture replay")
    parser.add_argument("--continue-on-fail", action="store_true", help="do not stop after first failed case")
    parser.add_argument("--report", action="store_true", help="write human-readable Markdown report to logs/")
    parser.add_argument("--timeout", type=int, default=search_probe.DEFAULT_TIMEOUT)
    parser.add_argument("--start-after", help="with --all, skip cases through this id")
    args = parser.parse_args()
    if args.case and args.all:
        raise SystemExit("choose either --case or --all")
    if args.start_after and not args.all:
        raise SystemExit("--start-after requires --all")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    prompt_provenance = quality_prompt_provenance(live=bool(args.live))
    results: list[dict[str, Any]] = []
    for case_id in _selected(args):
        result = await run_live_case(case_id, timeout=args.timeout) if args.live else run_case(case_id)
        result["run_id"] = run_id
        result["prompt_set_id"] = prompt_provenance["prompt_set_id"]
        results.append(result)
        if not result["ok"] and not args.continue_on_fail:
            break
    report_path = write_report(results, live=args.live) if args.report else None
    summary = {
        "schema": QUALITY_RUN_SCHEMA,
        "run_id": run_id,
        "started_at": started_at,
        "ok": all(item["ok"] for item in results),
        "network": bool(args.live),
        "release_id": current_release_id(),
        "fixtures": {
            "contract": file_identity(CONTRACT_FIXTURE),
            "quality": file_identity(QUALITY_FIXTURE),
        },
        "prompt_provenance": prompt_provenance,
        "count": len(results),
        "failed": [item["case"] for item in results if not item["ok"]],
        "scores": {item["case"]: item["score"] for item in results},
        "results": [
            {
                "case": item["case"],
                "run_id": run_id,
                "prompt_set_id": prompt_provenance["prompt_set_id"],
                "elapsed": item.get("elapsed", 0),
                "counts": item.get("counts", {}),
                "score": item["score"],
                "verdict": item.get("verdict", item["report"].get("verdict")),
                "hard_blockers": item["report"].get("hard_blockers", item.get("hard_blockers", [])),
                "layer_to_fix": item["report"].get("layer_to_fix", item.get("layer_to_fix")),
                "quality_profile": item.get("quality_profile", item["report"].get("quality_profile")),
                "error_codes": [str(code)[:160] for code in item.get("errors", [])[:12]],
                "composer_attempts": item.get("composer_attempts", []),
                "composer_candidate_text": item.get("composer_candidate_text", ""),
                "enrichment": item.get("enrichment", {}),
                "response_text": item["response_text"],
            }
            for item in results
        ],
    }
    if report_path:
        summary["report_path"] = str(report_path.relative_to(ROOT))
        artifact = write_quality_run_artifact(summary, started_at=started_at, run_id=run_id)
        summary["quality_run_artifact"] = str(artifact.relative_to(ROOT))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if summary["ok"] else 1


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
