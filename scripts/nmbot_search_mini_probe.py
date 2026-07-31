#!/usr/bin/env python3
"""Small safe search-only probes for the novostroym MCP gateway path."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import nmbot_four_layer_e2e as e2e


PROBES: dict[str, dict[str, Any]] = {
    "baseline": {
        "text": "Покажи варианты новостроек.",
        "hard": {},
        "preferences": {},
    },
    "budget": {
        "text": "Ищу квартиру с бюджетом до 30 миллионов рублей.",
        "hard": {"price": 30_000_000},
        "preferences": {},
    },
    "family": {
        "text": "Ищем квартиру для семьи с двумя детьми, важны школы и детские сады.",
        "hard": {},
        "preferences": {"rooms": 3},
    },
    "family_budget": {
        "text": "Ищем квартиру для семьи с двумя детьми: три комнаты, школы и детские сады, бюджет до 30 миллионов рублей.",
        "hard": {"price": 30_000_000},
        "preferences": {"rooms": 3},
    },
    "family_needs_budget": {
        "text": "Ищем квартиру для семьи с двумя детьми: три комнаты, школы и детские сады, бюджет до 30 миллионов рублей.",
        "hard": {"price": 30_000_000},
        "preferences": {"rooms": 3, "need": ["schools", "kindergartens", "infrastructure_family"]},
    },
    "location_budget": {
        "text": "Ищу квартиру в Москве с бюджетом до 30 миллионов рублей.",
        "hard": {"location": ["Москва"], "price": 30_000_000},
        "preferences": {},
    },
}

SAFE_EVIDENCE_KEYS = {
    "location": {"location", "district", "metro", "address"},
    "price": {"price_min", "from_price", "min_price", "price_range"},
    "family": {
        "family_infrastructure", "infrastructure", "infrastructure_family",
        "school", "schools", "kindergarten", "kindergartens",
        "park_near", "water_near", "yard_without_cars", "children_ground",
        "sports_ground", "security", "ecology_rating",
    },
}


def _recursive_key_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            names.add(str(key))
            names.update(_recursive_key_names(nested))
    elif isinstance(value, list):
        for nested in value:
            names.update(_recursive_key_names(nested))
    return names


def _plan(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "search",
        "target": "new_search",
        "search_policy": "required",
        "constraints_patch": {
            "hard": dict(probe.get("hard") or {}),
            "preferences": dict(probe.get("preferences") or {}),
            "unknown": {},
        },
    }


def safe_result(name: str, data: dict[str, Any], elapsed: float) -> dict[str, Any]:
    facts = data.get("facts") if isinstance(data.get("facts"), list) else []
    near = data.get("near") if isinstance(data.get("near"), list) else []
    coverage = {kind: 0 for kind in SAFE_EVIDENCE_KEYS}
    fact_keys: set[str] = set()
    for item in facts:
        if not isinstance(item, dict):
            continue
        keys = _recursive_key_names(item)
        fact_keys.update(keys & set().union(*SAFE_EVIDENCE_KEYS.values()))
        for kind, expected in SAFE_EVIDENCE_KEYS.items():
            if keys & expected:
                coverage[kind] += 1
    return {
        "probe": name,
        "counts": {"facts": len(facts), "near": len(near)},
        "classification": e2e.structured_search_diagnostic(data)["classification"],
        "evidence_coverage": coverage,
        "fact_key_names": sorted(fact_keys),
        "elapsed_seconds": round(elapsed, 3),
    }


async def run_probe(name: str, *, prompt: str, profile: str | None, timeout: int) -> dict[str, Any]:
    probe = PROBES[name]
    system_prompt = e2e.load_search_prompt(prompt)
    loop = asyncio.get_running_loop()
    started = loop.time()
    data = await e2e.live_search(
        probe["text"],
        _plan(probe),
        probe,
        timeout,
        system_prompt=system_prompt,
        search_profile=profile,
    )
    return safe_result(name, data, loop.time() - started)


async def async_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", choices=sorted(PROBES), required=True)
    parser.add_argument("--prompt", choices=("search_v1", "four_layer_search_v2"), default="search_v1")
    parser.add_argument("--profile", choices=("family", "investment", "mortgage"))
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"ok": True, "network": False, "probe": args.probe}, ensure_ascii=False, sort_keys=True))
        return 0
    e2e.load_env()
    result = await run_probe(args.probe, prompt=args.prompt, profile=args.profile, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["counts"]["facts"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
