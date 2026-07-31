#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nmbot_v2.contracts import SafeTurnContext, SemanticPlan
from nmbot_v2.search_contract import (
    V2SearchRequest,
    build_query,
    build_request_data,
    build_search_request,
    load_prompt,
    normalize_search_output,
    parse_strict_json,
    validate_search_output,
)
from nmbot_v2.state import ConversationState
from scripts.nmbot_v2_search_mcp_probe import gateway_request, load_env


QUERY = "квартира для жизни в Москве до 40 млн"
VARIANTS = ("v1_old", "v2_old", "minimal_old", "v2_contract", "minimal_contract")

MINIMAL_PROMPT = """Ты — search-agent MCP novostroym. Обязательно вызови novostroym/get_flat_info по запросу клиента и ограничениям. Не отвечай по памяти. Верни только строгий JSON с ключами facts, near, missing, params, diagnostics. facts — подходящие ЖК из MCP; near — только альтернативы; missing — только недостающие поля. Не придумывай факты и не пиши клиентский ответ. Сохраняй только поля из available_fact_fields, если они переданы."""


def request_contract() -> V2SearchRequest:
    context = SafeTurnContext(conversation_ref="matrix", user_text=QUERY)
    plan = SemanticPlan(
        operation="search",
        query_text=QUERY,
        intent="life",
        constraints_delta={"hard": {"location": ["Москва"], "max_price": 40_000_000}},
    )
    request = build_search_request(plan, ConversationState(), context)
    return replace(
        request,
        requested_hard={"location": ["Москва"], "max_price": 40_000_000},
        effective_hard={"district": "msk", "max_price": 40_000_000},
    )


def old_query(request: V2SearchRequest) -> str:
    envelope = {
        "contract": "search_hard_constraints_v1",
        "exact_match_policy": {
            "facts": "only objects satisfying every hard constraint",
            "near": "alternatives only, never replacements for facts",
        },
        "constraints": {"hard": request.effective_hard, "preferences": request.preferences},
    }
    return (
        "SEARCH_CONTRACT_ENVELOPE=" + json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n"
        "facts[] содержат только точные совпадения; near[] — только помеченные альтернативы.\n\n"
        "Текущие параметры: " + json.dumps(request.effective_hard, ensure_ascii=False) + "\n"
        "Клиент: " + QUERY
    )


def contract_query(request: V2SearchRequest) -> str:
    return (
        "NATURAL_SEARCH_QUERY=" + QUERY + "\n"
        "Используй NATURAL_SEARCH_QUERY как цель вызова MCP; строгие границы бери только из effective_hard.\n"
        + build_query(request)
    )


def request_data(variant: str) -> tuple[dict, V2SearchRequest]:
    request = request_contract()
    if variant.startswith("v1_"):
        prompt = (ROOT / "prompts" / "search_v1.txt").read_text(encoding="utf-8")
    elif variant.startswith("v2_"):
        prompt = load_prompt()
    else:
        prompt = MINIMAL_PROMPT
    data = build_request_data(request, prompt=prompt)
    data["query"] = old_query(request) if variant.endswith("_old") else contract_query(request)
    return data, request


async def run_variant(variant: str) -> dict:
    data, request = request_data(variant)
    started = time.monotonic()
    raw, meta = await gateway_request(data, 90)
    parsed, parse_errors = parse_strict_json(raw)
    if parsed is None:
        return {"variant": variant, "ok": False, "elapsed_seconds": round(time.monotonic() - started, 3), "counts": {"facts": 0, "near": 0}, "errors": parse_errors, "facts": []}
    normalized = normalize_search_output(parsed, request)
    validation = validate_search_output(normalized, request)
    facts = normalized.get("facts") if isinstance(normalized.get("facts"), list) else []
    return {
        "variant": variant,
        "ok": bool(validation["ok"] and meta.get("ok", True)),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "counts": validation["counts"],
        "errors": validation["errors"],
        "facts": [
            {
                "name": str(item.get("name") or item.get("label") or "")[:100],
                "district": item.get("district"),
                "location": item.get("location"),
                "min_price": item.get("min_price") or item.get("price_min"),
            }
            for item in facts[:5]
            if isinstance(item, dict)
        ],
    }


async def main(variants: list[str]) -> None:
    load_env()
    for variant in variants:
        result = await run_variant(variant)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=VARIANTS, action="append")
    args = parser.parse_args()
    asyncio.run(main(args.variant or list(VARIANTS)))
