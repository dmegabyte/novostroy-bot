#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nmbot_v2.contracts import SafeTurnContext, SemanticPlan
from nmbot_v2.search_contract import (
    build_request_data,
    build_search_request,
    load_prompt,
    normalize_search_output,
    parse_strict_json,
    validate_search_output,
)
from nmbot_v2.state import ConversationState
from nmbot_v2.quality import build_quality_profile, evaluate_scenario
from nmbot_v2.search_enrichment import enrich_search_result_top_options
from scripts import nmbot_v2_quality_gate as quality_gate
from scripts.nmbot_v2_search_mcp_probe import gateway_request, load_env


QUERY = "квартира для жизни в Москве до 40 млн"


async def main(prompt_mode: str, canonical_evidence: bool, production_builder: bool, compose: bool) -> None:
    load_env()
    context = SafeTurnContext(conversation_ref="hypothesis", user_text=QUERY)
    plan = SemanticPlan(
        operation="search",
        query_text=QUERY,
        intent="life",
        constraints_delta={"hard": {"location": ["Москва"], "max_price": 40_000_000}},
    )
    request = build_search_request(plan, ConversationState(), context)
    if canonical_evidence:
        request = replace(
            request,
            requested_hard={"location": ["Москва"], "max_price": 40_000_000},
            effective_hard={"district": "msk", "max_price": 40_000_000},
        )
    prompt = load_prompt() if prompt_mode == "v2" else (Path(__file__).resolve().parents[1] / "prompts" / "search_v1.txt").read_text(encoding="utf-8")
    request_data = build_request_data(request, prompt=prompt)
    if not production_builder:
        request_data["query"] = (
            f"NATURAL_SEARCH_QUERY={QUERY}\n"
            "Используй NATURAL_SEARCH_QUERY как естественную формулировку цели при вызове MCP; "
            "строгие границы бери только из строки `Текущие параметры` → effective_hard.\n"
            + request_data["query"]
        )
    started = time.monotonic()
    raw, meta = await gateway_request(request_data, 90)
    parsed, parse_errors = parse_strict_json(raw)
    if parsed is None:
        print(json.dumps({"ok": False, "errors": parse_errors, "gateway_ok": bool(meta.get("ok"))}, ensure_ascii=False))
        return
    normalized = normalize_search_output(parsed, request)
    validation = validate_search_output(normalized, request)
    facts = normalized.get("facts") if isinstance(normalized.get("facts"), list) else []
    output = {
        "ok": bool(validation["ok"] and meta.get("ok", True)),
        "prompt_mode": prompt_mode,
        "canonical_evidence": canonical_evidence,
        "production_builder": production_builder,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "counts": validation["counts"],
        "errors": validation["errors"],
        "facts": [
            {
                "name": str(item.get("name") or item.get("label") or "")[:100],
                "location": item.get("location"),
                "min_price": item.get("min_price") or item.get("price_min"),
            }
            for item in facts[:5]
            if isinstance(item, dict)
        ],
    }
    if compose and output["ok"]:
        base_result = quality_gate.search_result_from_output(normalized)

        async def enrichment_gateway(payload: dict):
            return await gateway_request(payload, 90)

        enriched_result, enrichment_meta = await enrich_search_result_top_options(
            base_result,
            request.response_viewpoint,
            enrichment_gateway,
            base_viewpoint=request.base_viewpoint,
            max_options=3,
            timeout=90,
        )
        response_text, search_result, composer_errors, composer_status, composer_attempts = await quality_gate.compose_case_live(
            "life", normalized, request, timeout=90, result=enriched_result
        )
        report = evaluate_scenario(
            scenario_id="life",
            response_text=response_text,
            search_result=search_result,
            search_output=normalized,
            search_request=request,
            viewpoint="life",
        )
        profile = build_quality_profile(
            dimensions=report.dimensions,
            hard_blockers=report.hard_blockers + (["composer_degraded_fallback"] if composer_status == "fallback" else []),
            search_ok=True,
            evidence="live",
            composer_status=composer_status,
            latency_seconds=round(time.monotonic() - started, 3),
        )
        output.update({
            "composer_status": composer_status,
            "composer_errors": composer_errors,
            "composer_attempts": composer_attempts,
            "enrichment": enrichment_meta,
            "response_text": response_text,
            "quality_score": report.score,
            "quality_verdict": report.verdict,
            "hard_blockers": report.hard_blockers,
            "quality_profile": profile,
        })
        output["ok"] = bool(output["ok"] and report.ok and profile["gate_pass"])
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", choices=("v1", "v2"), default="v2")
    parser.add_argument("--canonical-evidence", action="store_true")
    parser.add_argument("--production-builder", action="store_true")
    parser.add_argument("--compose", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.prompt, args.canonical_evidence, args.production_builder, args.compose))
