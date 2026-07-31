#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from time import monotonic
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nmbot_v1.contracts import V1Error, V1IntentPlan  # noqa: E402
from nmbot_v1.provider_adapters import PLANNER_PAYLOAD_STAGE, V1GatewayPlannerPort  # noqa: E402
from nmbot_v1.prompt_provenance import identity_from_path  # noqa: E402


MODEL = "openai/gpt-5.5"
PROMPT_SOURCE = "prompts/candidates/v1_unified_planner_gpt55_experiment_v1.txt"
PROMPT_PATH = ROOT / PROMPT_SOURCE
EXPECTED_PLAN = {
    "goal": "fact_check",
    "constraints_delta": {"hard": {}, "preferences": {}},
    "requested_facts": ["mortgage_terms"],
    "operator_intent": "none",
    "selected_option_ref": None,
    "selected_lot_ref": None,
}
REGRESSION_INPUT = {
    "schema_version": 1,
    "safe_user_text": "а семеукнаая ипотека возможна?",
    "state": {
        "stage": "current_options",
        "hard_constraints": {"rooms": 2, "viewpoint": "family"},
        "preferences": {"family": True},
        "visible_option_refs": ["option_1", "option_2", "option_3"],
        "selected_project_ref": None,
    },
}
REQUIRED_PROMPT_MARKERS = (
    "Purpose:",
    "Inputs:",
    "Output schema:",
    "Priority rules:",
    "Forbidden claims:",
    "Owner layer:",
    "Validation:",
    "mortgage_terms",
)


def prompt_identity() -> dict[str, Any]:
    item = identity_from_path("v1.planner.gpt55_experiment", PROMPT_SOURCE, PROMPT_PATH, usage="invoked")
    return {"source": item["source"], "sha256": item["sha256"], "prompt_id": item["prompt_id"]}


def validate_candidate_prompt() -> dict[str, Any]:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    missing = [marker for marker in REQUIRED_PROMPT_MARKERS if marker not in text]
    return {"ok": not missing, "missing_markers": missing[:10]}


def expected_plan_check(plan: V1IntentPlan) -> dict[str, Any]:
    data = plan.to_dict()
    checks = {
        "goal": data.get("goal") == EXPECTED_PLAN["goal"],
        "constraints_delta": data.get("constraints_delta") == EXPECTED_PLAN["constraints_delta"],
        "requested_facts": data.get("requested_facts") == EXPECTED_PLAN["requested_facts"],
        "operator_intent": data.get("operator_intent") == EXPECTED_PLAN["operator_intent"],
        "selected_option_ref": data.get("selected_option_ref") is None,
        "selected_lot_ref": data.get("selected_lot_ref") is None,
    }
    return {"ok": all(checks.values()), "checks": checks}


def safe_base_report(mode: str) -> dict[str, Any]:
    return {
        "schema": "nmbot.v1.gpt55_planner_probe.v1",
        "mode": mode,
        "status": "prepared",
        "model": {"service": "openrouter", "id": MODEL},
        "prompt": prompt_identity(),
        "prompt_contract": validate_candidate_prompt(),
        "regression": {
            "input_case": "current_shortlist_typo_family_mortgage",
            "user_text_sha256_known": True,
            "expected": EXPECTED_PLAN,
        },
        "provider_called": False,
        "contract_result": {"status": "not_run"},
        "expected_plan_check": {"status": "not_run"},
    }


class _PayloadCaptureGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], dict[str, Any], int]] = []

    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
        self.calls.append((request_data, headers, timeout))
        raise AssertionError("dry-run must not call provider")


def dry_run_report() -> tuple[int, dict[str, Any]]:
    report = safe_base_report("dry-run")
    port = V1GatewayPlannerPort(_PayloadCaptureGateway(), prompt_path=PROMPT_PATH, model=MODEL)
    payload = port.request_payload(REGRESSION_INPUT)
    report["request_contract"] = {
        "payload_stage": payload.get("_payload_stage"),
        "model": payload.get("model"),
        "has_mcp_servers": "mcp_servers" in payload,
        "prompt_attached": isinstance(payload.get("system_prompt"), str),
        "query_marker": str(payload.get("query") or "").startswith("V1_PLANNER_INPUT="),
    }
    report["status"] = "passed" if report["prompt_contract"]["ok"] and report["request_contract"]["model"] == MODEL and not report["request_contract"]["has_mcp_servers"] else "failed"
    return (0 if report["status"] == "passed" else 2), _bounded_report(report)


async def probe_with_gateway(gateway: Any) -> tuple[int, dict[str, Any]]:
    report = safe_base_report("run")
    port = V1GatewayPlannerPort(gateway, prompt_path=PROMPT_PATH, model=MODEL)
    start = monotonic()
    try:
        plan = await port.plan(REGRESSION_INPUT)
    except V1Error as exc:
        report["status"] = "failed"
        report["provider_called"] = True
        report["contract_result"] = {"status": "failed", "error_code": str(exc)[:80]}
        report["duration_ms"] = int((monotonic() - start) * 1000)
        return 2, _bounded_report(report)
    report["provider_called"] = True
    report["duration_ms"] = int((monotonic() - start) * 1000)
    report["contract_result"] = {"status": "passed", "plan": plan.to_dict()}
    report["expected_plan_check"] = expected_plan_check(plan)
    report["status"] = "passed" if report["prompt_contract"]["ok"] and report["expected_plan_check"]["ok"] else "failed"
    return (0 if report["status"] == "passed" else 1), _bounded_report(report)


async def run_provider_report() -> tuple[int, dict[str, Any]]:
    try:
        from scripts.nmbot_gateway_client import OvermindClient  # type: ignore
    except ImportError:  # pragma: no cover
        from nmbot_gateway_client import OvermindClient  # type: ignore

    client = OvermindClient()
    try:
        return await probe_with_gateway(client)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            await close()


def _bounded_report(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if isinstance(value, Mapping):
        return {str(k)[:80]: _bounded_report(v, depth=depth + 1) for k, v in list(value.items())[:80]}
    if isinstance(value, list):
        return [_bounded_report(v, depth=depth + 1) for v in value[:80]]
    if isinstance(value, tuple):
        return [_bounded_report(v, depth=depth + 1) for v in value[:80]]
    if isinstance(value, str):
        return value[:1200]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:200]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local isolated V1 GPT-5.5 planner experiment probe")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("dry-run", help="Build safe request metadata without provider call")
    sub.add_parser("run", help="Make exactly one V1 planner gateway call with openai/gpt-5.5")
    args = parser.parse_args(argv)
    if args.command == "dry-run":
        code, report = dry_run_report()
    else:
        code, report = asyncio.run(run_provider_report())
    text = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for secret in (os.getenv("OPENROUTER_API_KEY"), os.getenv("OVERMIND_TOKEN"), os.getenv("GATEWAY_POLL_TOKEN")):
        if secret and secret in text:
            raise SystemExit(3)
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
