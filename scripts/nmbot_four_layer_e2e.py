#!/usr/bin/env python3
"""Local-only isolated four-layer E2E harness for nmbot.

Default mode is deterministic and performs no network calls.  Live mode is
explicitly opt-in with --live and still runs one scenario through isolated
planner -> search_mcp -> validator -> presenter stages without importing or
mutating the production runtime.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from nmbot_node_emulator import constraint_validator, decision_context, search_normalizer  # noqa: E402
from nmbot_four_layer_hypothesis import (  # noqa: E402
    _json_from_text,
    build_request_data,
    check_invariants,
    deterministic_present,
)
from search_profiles import safe_search_profile_payload  # noqa: E402


SEARCH_MODEL = "google/gemini-3.1-flash-lite-preview"
PRESENTER_MODEL = "google/gemini-2.5-flash"
DEFAULT_TIMEOUT = 60
SEARCH_MODEL_CANDIDATES = (
    SEARCH_MODEL,
    "google/gemini-3.5-flash",
    "deepseek/deepseek-v3.2",
    "deepseek/deepseek-v4-flash",
)

SENSITIVE_KEY_RE = re.compile(r"phone|телефон|client|chat|site|sender|token|secret|api[_-]?key|authorization|bearer|headers|raw|prompt|request", re.I)
PHONE_RE = re.compile(r"\+?\d[\d\s()\-.]{7,}\d")
URL_TOKEN_RE = re.compile(r"(?i)(token|secret|api[_-]?key|authorization|bearer)\S*")

CANONICAL_PLAN_KEYS = {"action", "target", "search_policy", "constraints_patch", "facets", "missing_fields", "clarification_fields"}
LEGACY_SEARCH_ACTIONS = {"new_search", "update_search", "expand_more_options"}
LEGACY_RECOVERY_ACTIONS = {"ask_clarification", "clarify_negation", "reject_offer", "reject_operator", "reject_phone", "reject_selected_option", "reject_similar_options"}
MIN_CONFIDENCE = 0.55
SAFE_SEARCH_TOP_LEVEL_KEYS = {"facts", "near", "_meta", "meta", "metadata", "status"}

SCHEMA: dict[str, dict[str, Any]] = {
    "location": {"operator": "in", "fact_field": "location", "claim": True, "insufficient_if_absent": True},
    "price": {"operator": "max", "fact_field": "price_min", "claim": True, "insufficient_if_absent": True},
    "liquidity": {"operator": "known", "fact_field": "liquidity", "claim": True},
    "demand": {"operator": "known", "fact_field": "demand", "claim": True},
    "yield": {"operator": "known", "fact_field": "yield", "claim": True},
}

SCENARIOS: dict[str, dict[str, Any]] = {
    "exact_budget": {
        "user_text": "Ищу квартиру в Москве с бюджетом до 30 миллионов рублей.",
        "planner": {
            "action": "search", "intent": "life", "intent_policy": "set", "target": "new_search", "search_policy": "required",
            "confidence": 0.9, "constraints_patch": {"hard": {"price": 30_000_000}, "preferences": {}, "unknown": {}},
            "facets": {}, "missing_fields": [], "clarification_fields": [], "fallback_used": False, "canonical_valid": True, "canonical_errors": [],
        },
        "search": {"facts": [
            {"id": "exact_budget_1", "name": "ЖК Бюджетный", "location": "Москва", "price_min": 28_000_000},
            {"id": "reject_budget_2", "name": "ЖК Выше лимита", "location": "Москва", "price_min": 32_000_000},
        ], "near": []},
        "expected_status": "ok",
    },
    "exact_budget_20": {
        "user_text": "Ищу квартиру в Москве с бюджетом до 20 миллионов рублей.",
        "planner": {
            "action": "search", "intent": "life", "intent_policy": "set", "target": "new_search", "search_policy": "required",
            "confidence": 0.9, "constraints_patch": {"hard": {"price": 20_000_000}, "preferences": {}, "unknown": {}},
            "facets": {}, "missing_fields": [], "clarification_fields": [], "fallback_used": False, "canonical_valid": True, "canonical_errors": [],
        },
        "search": {"facts": [
            {"id": "synthetic_exact_budget_20_match", "name": "ЖК Синтетический до двадцати", "location": "Москва", "price_min": 19_800_000},
            {"id": "synthetic_exact_budget_20_reject", "name": "ЖК Синтетический выше лимита", "location": "Москва", "price_min": 20_500_000},
        ], "near": []},
        "expected_status": "ok",
    },
    "family_search": {
        "user_text": "Ищем в Москве квартиру для семьи с двумя детьми: три комнаты, рядом школы, бюджет до 30 миллионов.",
        "planner": {
            "action": "search", "intent": "family", "intent_policy": "set", "target": "new_search", "search_policy": "required",
            "confidence": 0.9, "constraints_patch": {"hard": {"price": 30_000_000}, "preferences": {"rooms": 3}, "unknown": {}},
            "facets": {}, "missing_fields": [], "clarification_fields": [], "fallback_used": False, "canonical_valid": True, "canonical_errors": [],
        },
        "search": {"facts": [
            {"id": "family_exact", "name": "ЖК Семейный", "location": "Москва", "price_min": 29_000_000},
            {"id": "family_reject", "name": "ЖК Выше семейного бюджета", "location": "Москва", "price_min": 31_000_000},
        ], "near": []},
        "expected_status": "ok",
    },
    "family_progressive": {
        "user_text": "Ищем квартиру для семьи с двумя детьми: три комнаты, важны школы и детские сады, бюджет до 30 миллионов рублей.",
        "planner": {
            "action": "search", "intent": "family", "intent_policy": "set", "target": "new_search", "search_policy": "required",
            "confidence": 0.9, "constraints_patch": {"hard": {"price": 30_000_000}, "preferences": {"rooms": 3}, "unknown": {}},
            "facets": {}, "missing_fields": [], "clarification_fields": [], "fallback_used": False, "canonical_valid": True, "canonical_errors": [],
        },
        "search": {"facts": [
            {"id": "family_progressive_exact", "name": "ЖК Семейный", "location": "Москва", "price_min": 29_000_000},
            {"id": "family_progressive_reject", "name": "ЖК Выше бюджета", "location": "Москва", "price_min": 31_000_000},
        ], "near": []},
        "expected_status": "ok",
    },
    "hard_constraints": {
        "user_text": "Нужна квартира на Соколе до 18 миллионов.",
        "planner": {
            "action": "search", "intent": "life", "intent_policy": "set", "target": "new_search", "search_policy": "required",
            "confidence": 0.9, "constraints_patch": {"hard": {"location": ["Сокол"], "price": 18_000_000}, "preferences": {}, "unknown": {}},
            "facets": {}, "missing_fields": [], "clarification_fields": [], "fallback_used": False, "canonical_valid": True, "canonical_errors": [],
        },
        "search": {"facts": [
            {"id": "exact_1", "name": "ЖК Северный квартал", "location": "Сокол", "price_min": 17_500_000},
            {"id": "reject_location", "name": "ЖК Южный парк", "location": "Печатники", "price_min": 16_900_000},
            {"id": "reject_budget", "name": "ЖК Дорогой", "location": "Сокол", "price_min": 21_000_000},
        ], "near": [{"id": "near_ignored", "name": "ЖК Почти", "location": "Аэропорт", "price_min": 18_500_000}]},
        "expected_status": "ok",
    },
    "unsupported_claim": {
        "user_text": "Нужно для инвестиций у метро до 20 миллионов.",
        "planner": {
            "action": "search", "intent": "investment", "intent_policy": "set", "target": "new_search", "search_policy": "required",
            "confidence": 0.9, "constraints_patch": {"hard": {"location": ["Сокол"], "price": 20_000_000}, "preferences": {}, "unknown": {}},
            "facets": {}, "missing_fields": [], "clarification_fields": [], "fallback_used": False, "canonical_valid": True, "canonical_errors": [],
        },
        "search": {"facts": [{"id": "claim_limited_1", "name": "ЖК Береговой корпус", "location": "Сокол", "price_min": 19_000_000}], "near": []},
        "expected_status": "ok",
    },
    "no_match": {
        "user_text": "Хочу на Соколе до 12 миллионов.",
        "planner": {
            "action": "search", "intent": "life", "intent_policy": "set", "target": "new_search", "search_policy": "required",
            "confidence": 0.9, "constraints_patch": {"hard": {"location": ["Сокол"], "price": 12_000_000}, "preferences": {}, "unknown": {}},
            "facets": {}, "missing_fields": [], "clarification_fields": [], "fallback_used": False, "canonical_valid": True, "canonical_errors": [],
        },
        "search": {"facts": [
            {"id": "reject_a", "name": "ЖК Выше бюджета", "location": "Сокол", "price_min": 18_000_000},
            {"id": "reject_b", "name": "ЖК Другая локация", "location": "Печатники", "price_min": 11_000_000},
        ], "near": []},
        "expected_status": "ok",
    },
}


PlannerFunc = Callable[[str, dict[str, Any], int], Awaitable[dict[str, Any]]]
SearchFunc = Callable[[str, dict[str, Any], dict[str, Any], int], Awaitable[dict[str, Any]]]
PresenterFunc = Callable[[dict[str, Any], int], Awaitable[dict[str, Any]]]


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_search_prompt(name_or_path: str | None = None) -> str:
    if name_or_path:
        path = Path(name_or_path)
        if not path.is_absolute():
            path = ROOT / "prompts" / name_or_path
        if path.suffix == "":
            path = path.with_suffix(".txt")
        if not path.exists():
            raise FileNotFoundError(f"search prompt not found: {name_or_path}")
        return path.read_text(encoding="utf-8")
    for name in ("search_v1.txt", "search_v1"):
        path = ROOT / "prompts" / name
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError("search prompt not found")


def load_presenter_prompt(name_or_path: str) -> str:
    path = Path(name_or_path)
    if not path.is_absolute():
        path = ROOT / "prompts" / name_or_path
    if path.suffix == "":
        path = path.with_suffix(".txt")
    if not path.exists():
        raise FileNotFoundError(f"presenter prompt not found: {name_or_path}")
    return path.read_text(encoding="utf-8")


def compose_search_prompt_with_profile(base_prompt: str, profile: Any = None) -> str:
    """Append allowlisted MCP-evidence overlays to an isolated search prompt."""
    selection = safe_search_profile_payload(profile)
    if selection is None or not selection.overlays:
        return base_prompt
    blocks: list[str] = []
    for overlay in selection.overlays:
        path = ROOT / "prompts" / f"four_layer_search_profile_{overlay}_v1.txt"
        if path.exists():
            blocks.append(path.read_text(encoding="utf-8").rstrip())
    return base_prompt if not blocks else f"{base_prompt.rstrip()}\n\n## MCP search profile overlays\n" + "\n\n".join(blocks)


def safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 10:
        return "<redacted>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)[:80]
            if SENSITIVE_KEY_RE.search(key_text):
                continue
            out[key_text] = safe_value(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [safe_value(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return URL_TOKEN_RE.sub("<redacted>", PHONE_RE.sub("<redacted>", value))[:500]
    return value


def safe_plan_public(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": str(plan.get("action") or plan.get("dialog_action") or "")[:80],
        "target": str(plan.get("target") or "")[:80],
        "search_policy": str(plan.get("search_policy") or "")[:80],
        "confidence": round(float(plan.get("confidence") or 0.0), 3),
        "fallback_used": bool(plan.get("fallback_used")),
    }


def planner_allows_search(plan: dict[str, Any]) -> tuple[bool, dict[str, str]]:
    confidence = float(plan.get("confidence") or 0.0)
    if plan.get("fallback_used") or confidence < MIN_CONFIDENCE:
        return False, {"action": "recover_dialogue", "target": "none", "search_policy": "forbidden"}
    if any(key in plan for key in CANONICAL_PLAN_KEYS) and "canonical_fields_absent" not in set(map(str, plan.get("canonical_errors") or [])):
        action = str(plan.get("action") or "")
        target = str(plan.get("target") or "")
        search_policy = str(plan.get("search_policy") or "")
        allowed = action == "search" and target == "new_search" and search_policy == "required" and plan.get("canonical_valid") is not False
        return allowed, {"action": action or "recover_dialogue", "target": target or "none", "search_policy": search_policy or "forbidden"}
    legacy_action = str(plan.get("dialog_action") or "")
    if legacy_action in LEGACY_SEARCH_ACTIONS:
        return True, {"action": "search", "target": "new_search", "search_policy": "required"}
    if legacy_action in LEGACY_RECOVERY_ACTIONS:
        return False, {"action": "recover_dialogue", "target": "none", "search_policy": "forbidden"}
    return False, {"action": "recover_dialogue", "target": "none", "search_policy": "forbidden"}


def constraints_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    patch = plan.get("constraints_patch") if isinstance(plan.get("constraints_patch"), dict) else {}
    hard = patch.get("hard") if isinstance(patch.get("hard"), dict) else {}
    prefs = patch.get("preferences") if isinstance(patch.get("preferences"), dict) else {}
    out = {"hard": {}, "preferences": {}}
    for source, dest in ((hard, out["hard"]), (prefs, out["preferences"])):
        if "location" in source:
            loc = source["location"]
            dest["location"] = loc if isinstance(loc, list) else [loc]
        price = source.get("price", source.get("max_price", source.get("max_budget_m")))
        if isinstance(price, (int, float)) and not isinstance(price, bool):
            dest["price"] = float(price)

    params_delta = plan.get("params_delta")
    if isinstance(params_delta, dict):
        legacy_locations: list[str] = []
        for key in ("location", "locations", "district", "districts", "metro"):
            value = params_delta.get(key)
            if isinstance(value, str) and value.strip():
                legacy_locations.append(value.strip())
            elif isinstance(value, list):
                legacy_locations.extend(item.strip() for item in value if isinstance(item, str) and item.strip())
        if legacy_locations and "location" not in out["hard"]:
            out["hard"]["location"] = legacy_locations

        legacy_price = None
        for key in ("max_price", "max_budget_m"):
            value = params_delta.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                legacy_price = float(value)
                break
        price_obj = params_delta.get("price")
        if legacy_price is None and isinstance(price_obj, dict):
            value = price_obj.get("max")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                legacy_price = float(value)
        if legacy_price is not None and "price" not in out["hard"]:
            out["hard"]["price"] = legacy_price
    return out


def structured_price_min(value: Any) -> float | None:
    """Normalize the lower bound of a structured MCP price field.

    The search contract permits `price_min`, `price_range`, `price`, and `cost`.
    This parser is fact normalization only: it never consumes client prose or a
    presenter response.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value >= 0 else None
    if not isinstance(value, str) or len(value) > 120:
        return None
    text = value.lower().replace(",", ".")
    numbers = re.findall(r"\d+(?:\.\d+)?", text.replace(" ", ""))
    if not numbers:
        return None
    try:
        number = float(numbers[0])
    except ValueError:
        return None
    if "млн" in text or number < 1000:
        number *= 1_000_000
    return number if number >= 0 else None


def typed_options_from_search(search_json: dict[str, Any]) -> dict[str, Any]:
    """Map structured search facts only; near alternatives are intentionally ignored."""
    options: list[dict[str, Any]] = []
    for idx, item in enumerate(search_json.get("facts") if isinstance(search_json.get("facts"), list) else []):
        if not isinstance(item, dict):
            continue
        facts: dict[str, Any] = {}
        loc = item.get("location") or item.get("district") or item.get("metro") or item.get("near_metro")
        if isinstance(loc, list) and loc and all(isinstance(x, str) for x in loc):
            facts["location"] = loc[0]
        elif isinstance(loc, str) and loc.strip():
            facts["location"] = loc.strip()
        price = item.get("price_min", item.get("from_price", item.get("min_price")))
        # `price_range` is part of the structured search contract. Generic
        # `price`/`cost` strings can mean an upper bound, monthly payment, or
        # marketing copy, so they remain unknown unless a numeric min field is
        # supplied explicitly.
        if price is None:
            price = item.get("price_range")
        normalized_price = structured_price_min(price)
        if normalized_price is not None:
            facts["price_min"] = normalized_price
        for claim in ("liquidity", "demand", "yield"):
            if item.get(claim) not in (None, "", [], {}):
                facts[claim] = item.get(claim)
        option_id = str(item.get("id") or item.get("option_id") or f"fact_{idx + 1}")[:80]
        label = str(item.get("name") or item.get("title") or item.get("label") or option_id)[:120]
        options.append({"option_id": option_id, "label": label, "facts": facts, "source_ref": f"search:facts:{idx + 1}"})
    return {"options": options}


def structured_search_diagnostic(search_json: dict[str, Any]) -> dict[str, Any]:
    """Return counts-only diagnostics for parsed structured search output.

    This intentionally exposes no raw model text, query, payload, headers, tokens,
    or field values from facts/near because live E2E output can be copied around.
    """
    facts_count = len(search_json.get("facts") or []) if isinstance(search_json.get("facts"), list) else 0
    near_count = len(search_json.get("near") or []) if isinstance(search_json.get("near"), list) else 0
    if facts_count == 0 and near_count > 0:
        classification = "exact_facts_absent_with_near"
    elif facts_count == 0 and near_count == 0:
        classification = "no_structured_candidates"
    else:
        classification = "structured_facts_present"
    parsed_top_level_keys = sorted(
        str(key)[:80]
        for key in search_json.keys()
        if str(key) in SAFE_SEARCH_TOP_LEVEL_KEYS and not SENSITIVE_KEY_RE.search(str(key))
    )
    return {
        "counts": {"facts": facts_count, "near": near_count},
        "parsed_top_level_keys": parsed_top_level_keys,
        "classification": classification,
    }


def validate_stage(search_json: dict[str, Any], plan: dict[str, Any]) -> tuple[dict[str, Any], str]:
    typed = typed_options_from_search(search_json)
    normalized = search_normalizer(typed)
    constraints = constraints_from_plan(plan)
    validated = constraint_validator(normalized, constraints, SCHEMA)
    dctx = decision_context(validated, SCHEMA)
    safe_facts_by_id: dict[str, dict[str, Any]] = {}
    for option in typed.get("options") or []:
        if not isinstance(option, dict) or not option.get("option_id"):
            continue
        facts = option.get("facts") if isinstance(option.get("facts"), dict) else {}
        safe_facts = {
            key: facts[key]
            for key in ("location", "price_min")
            if key in facts and isinstance(facts[key], (str, int, float)) and not isinstance(facts[key], bool)
        }
        safe_facts_by_id[str(option["option_id"])] = safe_facts
    for item in dctx.get("matched") or []:
        if isinstance(item, dict):
            item["facts"] = safe_facts_by_id.get(str(item.get("option_id")), {})
    rejected_by_constraint_field: dict[str, int] = {}
    for option in validated.get("options") or []:
        if option.get("status") != "rejected":
            continue
        for failure in option.get("failed") or []:
            field = failure.get("field")
            if isinstance(field, str) and field in SCHEMA:
                rejected_by_constraint_field[field] = rejected_by_constraint_field.get(field, 0) + 1
    visible_ids = {
        str(item.get("option_id"))
        for bucket in ("matched", "near_match")
        for item in (dctx.get(bucket) or [])
        if isinstance(item, dict) and item.get("option_id")
    } | set((dctx.get("unknowns") or {}).keys())
    dctx["failed_constraints"] = {oid: rows for oid, rows in (dctx.get("failed_constraints") or {}).items() if oid in visible_ids}
    dctx["do_not_say"] = [row for row in (dctx.get("do_not_say") or []) if str((row or {}).get("option_id")) in visible_ids]
    dctx["source_refs"] = {oid: ref for oid, ref in (dctx.get("source_refs") or {}).items() if oid in visible_ids}
    status = "ok"
    facts_value = search_json.get("facts") if isinstance(search_json, dict) else None
    if not typed["options"]:
        status = "no_exact_matches" if isinstance(facts_value, list) else "no_structured_facts"
    elif not dctx.get("matched") and validated.get("summary", {}).get("unknown", 0):
        status = "insufficient_structured_facts"
    return {
        "typed_options": typed,
        "normalized": normalized,
        "validated": validated,
        "decision_context": dctx,
        "diagnostic": {"rejected_by_constraint_field": dict(sorted(rejected_by_constraint_field.items()))},
    }, status


async def dry_planner(user_text: str, scenario: dict[str, Any], timeout: int) -> dict[str, Any]:
    return json.loads(json.dumps(scenario["planner"], ensure_ascii=False))


async def dry_search(user_text: str, plan: dict[str, Any], scenario: dict[str, Any], timeout: int) -> dict[str, Any]:
    return json.loads(json.dumps(scenario["search"], ensure_ascii=False))


async def dry_presenter(decision_ctx: dict[str, Any], timeout: int) -> dict[str, Any]:
    return deterministic_present(decision_ctx)


async def live_planner(user_text: str, scenario: dict[str, Any], timeout: int) -> dict[str, Any]:
    import followup_intent_classifier

    async with aiohttp.ClientSession() as session:
        return await followup_intent_classifier.plan_dialog_state(session, user_text=user_text, state={}, timeout=timeout)


async def gateway_request(request_data: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
    token = os.getenv("OVERMIND_TOKEN") or os.getenv("GATEWAY_POLL_TOKEN") or ""
    api_key = os.getenv("OPENROUTER_API_KEY") or ""
    if not token or not api_key:
        raise RuntimeError("gateway credentials missing")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    safe_request = dict(request_data)
    safe_request["external_api_key"] = api_key
    payload = {"agent_name": "gateway-agent", "endpoint": "/process", "request_data": safe_request, "timeout_seconds": timeout, "max_retries": 0}
    base = os.getenv("OVERMIND_URL", "https://overmind.aiaxel.ru").rstrip("/")
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{base}/api/v1/tasks/api", json=payload, headers=headers) as resp:
            task = await resp.json()
            if resp.status not in (200, 201):
                return "", {"ok": False, "http_status": resp.status}
        task_id = task.get("id")
        if not task_id:
            return "", {"ok": False, "error": "missing_task_id"}
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            async with session.get(f"{base}/api/v1/tasks/api/{task_id}/status", headers=headers) as resp:
                status_data = await resp.json()
            if status_data.get("status") in {"completed", "failed", "cancelled"}:
                async with session.get(f"{base}/api/v1/tasks/api/{task_id}/result", headers=headers) as resp:
                    result = await resp.json()
                obj = result.get("result") if isinstance(result, dict) else result
                if isinstance(obj, dict):
                    return str(obj.get("response") or ""), {"ok": not bool(obj.get("error")), "metadata_keys": sorted((obj.get("metadata") or {}).keys())[:20]}
                return str(obj), {"ok": True}
            await asyncio.sleep(2)
    return "", {"ok": False, "error": "timeout"}


async def live_search(
    user_text: str,
    plan: dict[str, Any],
    scenario: dict[str, Any],
    timeout: int,
    *,
    model: str = SEARCH_MODEL,
    system_prompt: str | None = None,
    search_profile: Any = None,
) -> dict[str, Any]:
    constraints = constraints_from_plan(plan)
    hard = constraints.get("hard") or {}
    preferences = constraints.get("preferences") or {}
    safe_constraints = {"hard": hard, "preferences": preferences}
    envelope = {
        "contract": "search_hard_constraints_v1",
        "exact_match_policy": {
            "facts": "only objects that satisfy every non_negotiable hard constraint",
            "near": "alternatives only; never use as replacements for facts or primary suitable items",
            "if_no_facts": "return facts=[] and explain closest alternatives only through near[].why_close",
        },
        "constraints": safe_constraints,
    }
    query = (
        f"SEARCH_CONTRACT_ENVELOPE={json.dumps(envelope, ensure_ascii=False, sort_keys=True)}\n"
        "Правило exact-match: constraints.hard являются обязательными и не обсуждаются. "
        "facts[] можно заполнять только точными совпадениями по всем hard-условиям; "
        "near[] — только явно помеченные альтернативы с why_close, не primary shortlist.\n\n"
        f"Текущие параметры: {json.dumps({**hard, **preferences}, ensure_ascii=False)}\n"
        f"Клиент: {user_text}"
    )
    base_prompt = system_prompt if system_prompt is not None else load_search_prompt()
    request_data = {"_payload_stage": "main_search", "query": query, "service": "openrouter", "model": model, "system_prompt": compose_search_prompt_with_profile(base_prompt, search_profile), "parameters": {"temperature": 0.3, "max_tokens": int(os.getenv("NMBOT_SEARCH_MAX_TOKENS", "5000"))}, "mcp_servers": ["novostroym"]}
    raw, meta = await gateway_request(request_data, timeout)
    data = _json_from_text(raw)
    return data if isinstance(data, dict) else {"facts": [], "near": [], "_meta": meta}


async def live_presenter(decision_ctx: dict[str, Any], timeout: int, *, system_prompt: str | None = None) -> dict[str, Any]:
    request_data = build_request_data(
        decision_ctx,
        model=os.getenv("NMBOT_FOUR_LAYER_MODEL", PRESENTER_MODEL),
        system_prompt=system_prompt,
    )
    raw, _meta = await gateway_request(request_data, timeout)
    return _json_from_text(raw)


async def run_scenario(
    name: str,
    *,
    live: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    planner_func: PlannerFunc | None = None,
    search_func: SearchFunc | None = None,
    presenter_func: PresenterFunc | None = None,
    presenter_prompt: str | None = None,
    search_prompt: str | None = None,
    search_model: str | None = None,
    search_profile: str | None = None,
) -> dict[str, Any]:
    if name not in SCENARIOS:
        raise SystemExit(f"unknown scenario: {name}")
    if timeout <= 0:
        raise SystemExit("--timeout must be positive")
    load_env()
    scenario = SCENARIOS[name]
    user_text = scenario["user_text"]
    planner = planner_func or (live_planner if live else dry_planner)
    if search_func is not None:
        search = search_func
    elif live:
        selected_search_model = search_model or SEARCH_MODEL
        selected_search_prompt_text = load_search_prompt(search_prompt) if search_prompt else None
        selected_search_profile = search_profile

        async def search(user_text: str, plan: dict[str, Any], scenario: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
            return await live_search(user_text, plan, scenario, timeout_seconds, model=selected_search_model, system_prompt=selected_search_prompt_text, search_profile=selected_search_profile)
    else:
        search = dry_search
    presenter_prompt_text = load_presenter_prompt(presenter_prompt) if presenter_prompt else None
    if presenter_func is not None:
        presenter = presenter_func
    elif live:
        async def presenter(decision_ctx: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
            return await live_presenter(decision_ctx, timeout_seconds, system_prompt=presenter_prompt_text)
    else:
        presenter = dry_presenter
    timings: dict[str, float] = {}
    stages: dict[str, Any] = {}

    t0 = time.monotonic()
    plan = await planner(user_text, scenario, timeout)
    timings["planner"] = round(time.monotonic() - t0, 3)
    allowed, decision = planner_allows_search(plan)
    stages["planner"] = {"status": "searchable" if allowed else "not_searchable", "decision": decision, "plan": safe_plan_public(plan)}
    if not allowed:
        return safe_value({"scenario": name, "mode": "live" if live else "dry_run", "status": "not_searchable", "stages": stages, "timings": timings, "counts": {"facts": 0, "near": 0, "matched": 0, "rejected": 0}, "invariant_checks": {"ok": True, "failures": []}})

    t0 = time.monotonic()
    search_json = await search(user_text, plan, scenario, timeout)
    timings["search_mcp"] = round(time.monotonic() - t0, 3)
    search_diag = structured_search_diagnostic(search_json)
    facts_count = search_diag["counts"]["facts"]
    near_count = search_diag["counts"]["near"]
    profile_payload = safe_search_profile_payload(search_profile) if live else None
    stages["search_mcp"] = {"status": "ok", "source": "gateway-agent/main_search/mcp:novostroym" if live else "fixture", "model": (search_model or SEARCH_MODEL) if live else "fixture", "profile": profile_payload.public() if profile_payload else None, "counts": {"facts": facts_count, "near": near_count}, "policy": "facts_primary_near_not_exact", "diagnostic": search_diag}

    t0 = time.monotonic()
    validation, vstatus = validate_stage(search_json, plan)
    timings["validator"] = round(time.monotonic() - t0, 3)
    dctx = validation["decision_context"]
    stages["validator"] = {"status": vstatus, "summary": validation["validated"].get("summary"), "diagnostic": validation["diagnostic"], "decision_context": dctx}
    if vstatus in {"no_structured_facts", "insufficient_structured_facts"}:
        return safe_value({"scenario": name, "mode": "live" if live else "dry_run", "status": vstatus, "stages": stages, "timings": timings, "counts": {"facts": facts_count, "near": near_count, **validation["validated"].get("summary", {})}, "invariant_checks": {"ok": True, "failures": []}})

    t0 = time.monotonic()
    rendered = await presenter(dctx, timeout)
    timings["presenter"] = round(time.monotonic() - t0, 3)
    checks = check_invariants(dctx, rendered)
    stages["presenter"] = {"status": "ok" if checks["ok"] else "invariant_failure", "model": PRESENTER_MODEL if live else "deterministic", "candidate": presenter_prompt or ("default_live" if live else "deterministic"), "invariant_checks": checks, "response": rendered}

    status = vstatus if checks["ok"] and vstatus != "ok" else ("ok" if checks["ok"] else "presenter_invariant_failure")
    return safe_value({"scenario": name, "mode": "live" if live else "dry_run", "status": status, "stages": stages, "timings": timings, "counts": {"facts": facts_count, "near": near_count, **validation["validated"].get("summary", {})}, "invariant_checks": checks})


async def self_test() -> dict[str, Any]:
    results = [await run_scenario(name) for name in SCENARIOS]
    return {"ok": all(item.get("status") == SCENARIOS[item["scenario"]]["expected_status"] and item.get("invariant_checks", {}).get("ok") for item in results), "results": results}


def render_text(result: dict[str, Any]) -> str:
    counts = result.get("counts", {})
    checks = result.get("invariant_checks", {})
    return "\n".join([
        f"scenario: {result.get('scenario')}",
        f"mode: {result.get('mode')}",
        f"status: {result.get('status')}",
        f"counts: facts={counts.get('facts', 0)} near={counts.get('near', 0)} matched={counts.get('matched', 0)} rejected={counts.get('rejected', 0)} unknown={counts.get('unknown', 0)}",
        f"timings: {json.dumps(result.get('timings', {}), ensure_ascii=False, sort_keys=True)}",
        f"invariants: {'OK' if checks.get('ok') else 'FAIL'}",
        f"failures: {', '.join(checks.get('failures') or []) if checks.get('failures') else '-'}",
    ])


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local-only isolated nmbot four-layer E2E harness")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="hard_constraints")
    parser.add_argument("--live", action="store_true", help="explicitly allow isolated live planner/search/presenter calls")
    parser.add_argument("--json", action="store_true", help="print redacted JSON result")
    parser.add_argument("--self-test", action="store_true", help="run all deterministic no-network fixtures")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="positive timeout in seconds")
    parser.add_argument("--presenter-prompt", help="optional presenter prompt name/path for explicit --live runs")
    parser.add_argument("--search-prompt", help="optional search prompt name/path for explicit --live runs")
    parser.add_argument("--search-model", choices=SEARCH_MODEL_CANDIDATES, help="approved isolated main_search model candidate; only with --live")
    parser.add_argument("--search-profile", choices=("generic", "family", "investment", "mortgage"), help="allowlisted MCP evidence overlay; only with --live")
    return parser.parse_args(argv)


async def async_main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    if args.presenter_prompt and not args.live:
        raise SystemExit("--presenter-prompt is only used with --live")
    if args.search_prompt and not args.live:
        raise SystemExit("--search-prompt is only used with --live")
    if args.search_model and not args.live:
        raise SystemExit("--search-model is only used with --live")
    if args.search_profile and not args.live:
        raise SystemExit("--search-profile is only used with --live")
    result = await self_test() if args.self_test else await run_scenario(args.scenario, live=args.live, timeout=args.timeout, presenter_prompt=args.presenter_prompt, search_prompt=args.search_prompt, search_model=args.search_model, search_profile=args.search_profile)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if args.json else render_text(result if not args.self_test else result["results"][0]))
    if args.self_test:
        return 0 if result["ok"] else 1
    if args.live and (result.get("status") != "ok" or not result.get("invariant_checks", {}).get("ok")):
        return 1
    return 0


def main() -> int:
    return asyncio.run(async_main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
