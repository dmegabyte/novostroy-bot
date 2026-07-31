#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import nmbot_v2_quality_gate as gate
from scripts import nmbot_v2_search_mcp_probe as probe


async def main() -> None:
    probe.load_env()
    case_id = "life"
    scenario = gate.scenario_map()[case_id]
    request = gate.request_from_contract(scenario)
    record = {str(item["id"]): item for item in gate.quality_records()}[case_id]
    _, _, normalized = gate.render_case(case_id, record, request)

    observed: list[dict[str, object]] = []

    async def plain_json_gateway(payload: dict, timeout: int):
        safe = dict(payload)
        params = dict(safe.get("parameters") or {})
        params.pop("response_format", None)
        params.pop("provider", None)
        safe["parameters"] = params
        raw, meta = await probe.gateway_request(safe, timeout)
        stripped = str(raw or "").lstrip()
        observed.append({
            "length": len(str(raw or "")),
            "starts_object": stripped.startswith("{"),
            "starts_code_fence": stripped.startswith("```"),
            "contains_json_fence": "```json" in stripped[:20].lower(),
        })
        return raw, meta

    text, _, errors, status, attempts = await gate.compose_case_live(
        case_id,
        normalized,
        request,
        composer_func=plain_json_gateway,
        timeout=90,
    )
    print(json.dumps({
        "ok": status in {"primary", "repaired", "provider_retry"},
        "status": status,
        "errors": errors,
        "attempts": attempts,
        "observed_shapes": observed,
        "response_length": len(text),
        "question_count": text.count("?"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
