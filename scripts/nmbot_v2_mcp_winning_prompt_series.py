#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nmbot_v2.search_contract import build_request_data, normalize_search_output, parse_strict_json, validate_search_output
from scripts.nmbot_v2_search_mcp_probe import _request_from_scenario, gateway_request, load_env, load_fixture, scenarios_by_id


CLIENT_QUERIES = {
    "family": "двухкомнатная квартира для семьи",
    "family_financing_overlay": "двухкомнатная квартира для семьи в ипотеку",
    "rooms_budget_location": "двухкомнатная квартира на Соколе до 18 млн",
    "ready_finishing": "готовая квартира с отделкой под аренду",
    "district_location_separation": "квартира в Коммунарке, Новая Москва",
}

HARD_EVIDENCE_MAP = {
    "rooms": ["rooms", "apartment_types.rooms", "ads.rooms"],
    "max_price": ["min_price", "price1", "price2", "price3", "price4", "price_s", "price_n", "ads.fullprice"],
    "district": ["district"],
    "location": ["location", "location_id"],
    "ready": ["delivered", "ready", "state", "status"],
    "finishing": ["finishing", "ads.renovation", "house.finishing_list"],
}


def compact_query(request, user_text: str) -> str:
    envelope = {
        "contract": "v2_search_mcp_contract",
        "constraints": {
            "requested_hard": request.requested_hard,
            "effective_hard": request.effective_hard,
            "preferences": request.preferences,
            "relaxation_audit": request.relaxation_audit,
        },
        "response_viewpoint": request.response_viewpoint,
        "base_viewpoint": request.base_viewpoint,
        "available_fact_fields": request.available_fact_fields,
        "hard_evidence_requirements": {
            field: HARD_EVIDENCE_MAP.get(field, [field])
            for field in request.effective_hard
        },
        "count": request.count,
    }
    return (
        "SEARCH_CONTRACT_ENVELOPE=" + json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n"
        "facts[] содержат только точные совпадения по effective_hard; near[] — только явно помеченные альтернативы.\n\n"
        "Перед добавлением объекта в facts запроси и проверь hard_evidence_requirements. "
        "Если evidence для hard-поля не пришёл, объект не является facts: помести его в near или укажи поле в missing.\n"
        "Текущие параметры: " + json.dumps({**request.effective_hard, **request.preferences}, ensure_ascii=False) + "\n"
        "Клиент: " + user_text
    )


async def run(case_id: str) -> dict:
    fixture = load_fixture()
    scenario = scenarios_by_id(fixture)[case_id]
    request = _request_from_scenario(fixture, scenario)
    prompt = (ROOT / "prompts" / "v2_search_mcp.txt").read_text(encoding="utf-8")
    data = build_request_data(request, prompt=prompt)
    data["query"] = compact_query(request, CLIENT_QUERIES[case_id])
    started = time.monotonic()
    raw, meta = await gateway_request(data, 90)
    parsed, parse_errors = parse_strict_json(raw)
    if parsed is None:
        return {"case": case_id, "ok": False, "elapsed_seconds": round(time.monotonic() - started, 3), "counts": {"facts": 0, "near": 0}, "errors": parse_errors, "facts": []}
    normalized = normalize_search_output(parsed, request)
    validation = validate_search_output(normalized, request)
    facts = normalized.get("facts") if isinstance(normalized.get("facts"), list) else []
    return {
        "case": case_id,
        "query": CLIENT_QUERIES[case_id],
        "ok": bool(validation["ok"] and meta.get("ok", True)),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "counts": validation["counts"],
        "errors": validation["errors"],
        "facts": [str(item.get("name") or item.get("label") or "")[:100] for item in facts[:5] if isinstance(item, dict)],
        "wire_diagnostics": [
            {
                "rooms": str(item.get("rooms"))[:120] if item.get("rooms") is not None else None,
                "keys": sorted(str(key) for key in item)[:40],
            }
            for item in facts[:3]
            if isinstance(item, dict)
        ] if not validation["ok"] else [],
    }


async def main(case_ids: list[str]) -> None:
    load_env()
    for case_id in case_ids:
        print(json.dumps(await run(case_id), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=tuple(CLIENT_QUERIES), action="append")
    args = parser.parse_args()
    asyncio.run(main(args.case or list(CLIENT_QUERIES)))
