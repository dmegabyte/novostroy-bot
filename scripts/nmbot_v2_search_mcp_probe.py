#!/usr/bin/env python3
"""Read-only V2 MCP search prompt probe harness.

Default static mode performs no network calls.  Live mode is opt-in by passing
--case or --all without --fixture-only; it sends one structured V2 search input
through the same gateway-agent/OpenRouter/MCP path used by the isolated search
E2E harness, then validates only the public JSON contract shape.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from nmbot_v2 import search_contract as core_contract  # noqa: E402
PROMPT_PATH = ROOT / "prompts" / "v2_search_mcp.txt"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "v2_search_mcp_contract.json"
SEARCH_MODEL = "google/gemini-3.1-flash-lite-preview"
MCP_ALIAS = "novostroym"
MCP_TOOL = f"{MCP_ALIAS}/get_flat_info"
DEFAULT_TIMEOUT = 90

COMMON_FACT_FIELDS = core_contract.COMMON_FACT_FIELDS
HARD_EVIDENCE_MAP = core_contract.HARD_EVIDENCE_MAP
DIAGNOSTIC_KEYS = {
    "mcp_tool",
    "response_viewpoint",
    "base_viewpoint",
    "requested_field_priorities",
    "relaxation_audit",
    "ignored_preferences",
    "notes",
}
REGION_CODES = {"msk", "mo", "newmsk"}
DEFAULT_ALLOWED_PREFERENCES = {
    "format",
    "rooms_preference",
    "budget_preference",
    "location_preference",
    "infrastructure_preference",
    "transport_preference",
    "finance_preference",
    "sort_hint",
}
BASE_VIEWPOINTS = {"investment", "rental", "family", "life"}
READY_DIAGNOSTIC_FIELDS = ("ready", "delivered", "state", "status")
MAX_DIAGNOSTIC_VALUE_CHARS = 80
_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
_SAFE_FINISH_REASONS = {
    "stop",
    "length",
    "max_tokens",
    "content_filter",
    "tool_calls",
    "function_call",
    "error",
    "cancelled",
}
_SAFE_USAGE_KEYS = {"input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens"}
_SAFE_METADATA_NUMERIC_KEYS = {"tokens_used", "processing_time", "response_time"}


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _safe_gateway_task_diagnostics(
    *,
    task_id: Any,
    terminal_status: Any,
    result_obj: Any,
) -> dict[str, Any]:
    """Return transport diagnostics without exposing prompt or model text."""
    result = result_obj if isinstance(result_obj, dict) else {}
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    diagnostic: dict[str, Any] = {
        "task_id": str(task_id)[:80] if task_id else None,
        "terminal_status": str(terminal_status) if terminal_status in _TERMINAL_TASK_STATUSES else "unknown",
        "result_keys": sorted(str(key) for key in result)[:20],
        "metadata_keys": sorted(str(key) for key in metadata)[:20],
    }
    for key in ("finish_reason", "finishReason", "stop_reason", "stopReason"):
        value = metadata.get(key, result.get(key))
        if isinstance(value, str) and value in _SAFE_FINISH_REASONS:
            diagnostic["finish_reason"] = value
            break
    usage = metadata.get("usage", result.get("usage"))
    if isinstance(usage, dict):
        safe_usage = {
            str(key): value
            for key, value in usage.items()
            if key in _SAFE_USAGE_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        if safe_usage:
            diagnostic["usage"] = safe_usage
    for key in _SAFE_METADATA_NUMERIC_KEYS:
        value = metadata.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            diagnostic[key] = value
    return diagnostic


def load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def scenarios_by_id(fixture: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    data = fixture or load_fixture()
    return {str(item["id"]): item for item in data.get("scenarios", [])}


def _unique(items: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for item in items:
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if marker not in seen:
            out.append(item)
            seen.add(marker)
    return out


def available_fact_fields(fixture: dict[str, Any], scenario: dict[str, Any]) -> list[str]:
    fields: list[str] = sorted(COMMON_FACT_FIELDS | set(fixture.get("scenario_field_priorities", {}).get(scenario["response_viewpoint"], [])))
    base_viewpoint = scenario.get("base_viewpoint")
    if base_viewpoint:
        fields.extend(fixture.get("scenario_field_priorities", {}).get(base_viewpoint, []))
    fields.extend(scenario.get("expected_field_priorities_include", []))
    fields.extend(scenario.get("also_expected_overlay_preserves", []))
    return [str(item) for item in _unique(fields)]


def structured_input(fixture: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    return {
        "search_goal": scenario["search_goal"],
        "constraints": scenario["constraints"],
        "response_viewpoint": scenario["response_viewpoint"],
        "base_viewpoint": scenario.get("base_viewpoint"),
        "available_fact_fields": available_fact_fields(fixture, scenario),
        "count": int(scenario["count"]),
    }


def build_query(fixture: dict[str, Any], scenario: dict[str, Any]) -> str:
    return core_contract.build_query(
        _request_from_scenario(fixture, scenario),
        output_keys=fixture["output_top_level_keys"],
        forbidden_keys=fixture["forbidden_top_level_keys"],
    )


def build_request_data(fixture: dict[str, Any], scenario: dict[str, Any], *, prompt: str | None = None, model: str = SEARCH_MODEL) -> dict[str, Any]:
    return {
        "_payload_stage": "main_search",
        "query": build_query(fixture, scenario),
        "service": "openrouter",
        "model": model,
        "system_prompt": prompt if prompt is not None else load_prompt(),
        "parameters": {"temperature": 0.1, "max_tokens": int(os.getenv("NMBOT_SEARCH_MAX_TOKENS", "5000"))},
        "mcp_servers": [MCP_ALIAS],
    }


def parse_strict_json(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        return None, [f"invalid_strict_json:{exc.msg}"]
    if not isinstance(data, dict):
        return None, ["json_root_must_be_object"]
    return data, []


def _nested_keys(value: Any, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            name = str(key)
            full = f"{prefix}.{name}" if prefix else name
            keys.add(full)
            keys.update(_nested_keys(nested, full))
    elif isinstance(value, list):
        for nested in value:
            keys.update(_nested_keys(nested, prefix))
    return keys


def _item_id(item: Any) -> str | None:
    return str(item.get("id") or item.get("alias") or item.get("name")) if isinstance(item, dict) and (item.get("id") or item.get("alias") or item.get("name")) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _normalized_region_codes(value: Any) -> set[str]:
    values = value if isinstance(value, list) else [value]
    return {str(item).strip().lower() for item in values if str(item).strip().lower() in REGION_CODES}


def _matches_hard(item: dict[str, Any], field: str, expected: Any) -> bool:
    if field == "max_price":
        values = [_number(item.get(key)) for key in ("min_price", "max_price", "price1", "price2", "price3", "price4", "price_s", "price_n", "price_square")]
        values = [value for value in values if value is not None]
        return bool(values) and min(values) <= float(expected)
    if field == "rooms":
        return core_contract._matches_hard(item, field, expected)
    if field == "location":
        actual = str(item.get("location") or item.get("location_id") or "").lower()
        expected_values = [str(value).lower() for value in (expected if isinstance(expected, list) else [expected])]
        return bool(actual) and any(value in actual or actual in value for value in expected_values)
    if field == "district":
        return item.get("district") == expected
    if field == "ready":
        return core_contract._matches_hard(item, field, expected)
    if field == "finishing":
        return item.get("finishing") == expected
    return field in item and item.get(field) == expected


def _hard_evidence_present(item: dict[str, Any], field: str) -> bool:
    return core_contract.hard_evidence_present(item, field)


def _truncate_diagnostic_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_DIAGNOSTIC_VALUE_CHARS]
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return rendered[:MAX_DIAGNOSTIC_VALUE_CHARS]


def _ready_hard_failure_diagnostics(output: dict[str, Any], errors: list[str]) -> list[dict[str, Any]]:
    facts = output.get("facts") if isinstance(output.get("facts"), list) else []
    diagnostics: list[dict[str, Any]] = []
    for error in errors:
        prefix = "fact_"
        suffix = "_violates_hard:ready"
        if not (error.startswith(prefix) and error.endswith(suffix)):
            continue
        raw_idx = error[len(prefix) : -len(suffix)]
        if not raw_idx.isdigit():
            continue
        idx = int(raw_idx)
        if idx >= len(facts) or not isinstance(facts[idx], dict):
            continue
        fields = {
            key: _truncate_diagnostic_value(facts[idx][key])
            for key in READY_DIAGNOSTIC_FIELDS
            if key in facts[idx]
        }
        diagnostics.append({"error": error, "fact_index": idx, "fields": fields})
    return diagnostics


def _accepted_preference_keys(fixture: dict[str, Any], scenario: dict[str, Any]) -> set[str]:
    allowed = set(fixture.get("allowed_preferences") or DEFAULT_ALLOWED_PREFERENCES)
    return set((scenario["constraints"].get("preferences") or {})) & allowed


def _unknown_preference_keys(fixture: dict[str, Any], scenario: dict[str, Any]) -> set[str]:
    allowed = set(fixture.get("allowed_preferences") or DEFAULT_ALLOWED_PREFERENCES)
    return set((scenario["constraints"].get("preferences") or {})) - allowed


def _validate_assertions(output: dict[str, Any], fixture: dict[str, Any], scenario: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    assertions = set(scenario.get("shape_assertions", []))
    facts = output.get("facts") if isinstance(output.get("facts"), list) else []
    near = output.get("near") if isinstance(output.get("near"), list) else []
    params = output.get("params") if isinstance(output.get("params"), dict) else {}
    diagnostics = output.get("diagnostics") if isinstance(output.get("diagnostics"), dict) else {}
    requested_hard = scenario["constraints"].get("requested_hard") or {}
    effective_hard = scenario["constraints"].get("effective_hard") or {}
    preferences = scenario["constraints"].get("preferences") or {}

    priorities = diagnostics.get("requested_field_priorities")
    if isinstance(priorities, list):
        for field in scenario.get("expected_field_priorities_include", []):
            if field not in priorities:
                errors.append(f"missing_requested_priority:{field}")
        for field in scenario.get("also_expected_overlay_preserves", []):
            if field not in priorities:
                errors.append(f"missing_overlay_priority:{field}")

    if diagnostics.get("base_viewpoint") != scenario.get("base_viewpoint"):
        errors.append("diagnostics_base_viewpoint_mismatch")

    if "facts_preserve_allowed_mcp_fields" in assertions:
        allowed = set(structured_input(fixture, scenario)["available_fact_fields"])
        for idx, item in enumerate(facts):
            if isinstance(item, dict):
                extra = set(item) - allowed
                if extra:
                    errors.append(f"fact_{idx}_has_non_whitelisted_fields:{','.join(sorted(extra))}")

    if "params_only_normalized_constraints" in assertions:
        allowed_params = set(effective_hard) | _accepted_preference_keys(fixture, scenario)
        extra = set(params) - allowed_params
        if extra:
            errors.append(f"params_extra_keys:{','.join(sorted(extra))}")

    if "viewpoint_not_hard_filter" in assertions and ("response_viewpoint" in params or "purpose" in params):
        errors.append("viewpoint_leaked_into_params")

    if "district_is_mcp_region_code" in assertions:
        for container, items in (("facts", facts), ("near", near)):
            for idx, item in enumerate(items):
                if isinstance(item, dict) and "district" in item and not _normalized_region_codes(item["district"]):
                    errors.append(f"{container}_{idx}_district_not_region_code")
        if "district" in params and not _normalized_region_codes(params["district"]):
            errors.append("params_district_not_region_code")

    if "location_is_separate" in assertions and _normalized_region_codes(params.get("district")) and _normalized_region_codes(params.get("location")):
        errors.append("params_location_repeats_district_code")

    if "facts_match_all_hard_constraints" in assertions:
        for idx, item in enumerate(facts):
            if not isinstance(item, dict):
                continue
            for field, expected in effective_hard.items():
                if not _matches_hard(item, field, expected):
                    errors.append(f"fact_{idx}_violates_hard:{field}")

    if {"hard_evidence_required", "effective_hard_controls_exact_matching"} & assertions:
        for idx, item in enumerate(facts):
            if not isinstance(item, dict):
                continue
            for field in set(requested_hard) | set(effective_hard):
                if not _hard_evidence_present(item, field):
                    errors.append(f"fact_{idx}_missing_hard_evidence:{field}")

    if "near_requires_differences_and_why_close" in assertions:
        for idx, item in enumerate(near):
            if not isinstance(item, dict) or not item.get("why_close") or not isinstance(item.get("differences"), list) or not item.get("differences"):
                errors.append(f"near_{idx}_missing_differences_or_why_close")

    if "near_not_mixed_with_facts" in assertions:
        fact_ids = {_item_id(item) for item in facts}
        near_ids = {_item_id(item) for item in near}
        overlap = {item for item in fact_ids & near_ids if item}
        if overlap:
            errors.append("near_duplicates_facts")

    if "relaxation_audit_recorded" in assertions:
        relaxation_audit = diagnostics.get("relaxation_audit")
        if relaxation_audit != scenario["constraints"].get("relaxation_audit"):
            errors.append("relaxation_audit_mismatch")

    if "only_one_hard_constraint_relaxed" in assertions:
        relaxed = diagnostics.get("relaxation_audit") if isinstance(diagnostics.get("relaxation_audit"), list) else []
        if len(relaxed) > 1:
            errors.append("more_than_one_relaxed_constraint")

    if "agent_cannot_decide_relaxation" in assertions:
        if diagnostics.get("relaxation_audit") != scenario["constraints"].get("relaxation_audit"):
            errors.append("agent_changed_relaxation_audit")
        for key, expected in effective_hard.items():
            if params.get(key) != expected:
                errors.append(f"params_not_effective_hard:{key}")

    if "controlled_preferences_only" in assertions:
        unknown = _unknown_preference_keys(fixture, scenario)
        leaked = set(params) & unknown
        if leaked:
            errors.append("unknown_preferences_leaked_into_params:" + ",".join(sorted(leaked)))

    if "unknown_preference_safely_ignored" in assertions:
        unknown = _unknown_preference_keys(fixture, scenario)
        ignored = set(diagnostics.get("ignored_preferences") or []) if isinstance(diagnostics.get("ignored_preferences"), list) else set()
        if unknown - ignored:
            errors.append("unknown_preferences_not_reported:" + ",".join(sorted(unknown - ignored)))
        rendered = json.dumps(output, ensure_ascii=False)
        for key in unknown:
            raw_value = preferences.get(key)
            if raw_value is not None and str(raw_value) in rendered:
                errors.append("unknown_preference_raw_value_leaked")

    if "financing_overlay_not_replacement" in assertions and scenario.get("base_viewpoint"):
        priority_keys = set(priorities or []) if isinstance(priorities, list) else set()
        family_overlay = set(scenario.get("also_expected_overlay_preserves", []))
        if family_overlay and not (priority_keys & family_overlay):
            errors.append("financing_overlay_replaced_base_viewpoint")

    if "financing_base_overlay_explicit" in assertions:
        if scenario.get("response_viewpoint") != "financing" or scenario.get("base_viewpoint") not in BASE_VIEWPOINTS:
            errors.append("financing_base_viewpoint_not_explicit")

    if "no_absence_claim_without_evidence" in assertions:
        forbidden_markers = {"inventory_absent", "no_inventory", "absence_claim"}
        notes = diagnostics.get("notes") if isinstance(diagnostics.get("notes"), list) else []
        missing = output.get("missing") if isinstance(output.get("missing"), list) else []
        rendered_markers = _nested_keys({"notes": notes, "missing": missing})
        rendered_text = json.dumps({"notes": notes, "missing": missing}, ensure_ascii=False).lower()
        if rendered_markers & forbidden_markers or any(marker in rendered_text for marker in forbidden_markers):
            errors.append("absence_claim_without_hard_evidence")

    if "egrn_and_counters_only_if_returned" in assertions:
        for idx, item in enumerate(facts):
            if isinstance(item, dict):
                keys = _nested_keys(item)
                unsupported = {key for key in keys if key.startswith("egrn_contracts.") and "aggregate" not in key}
                if unsupported:
                    errors.append(f"fact_{idx}_has_non_aggregate_egrn_contracts")

    return errors


def validate_output(output: dict[str, Any], fixture: dict[str, Any], scenario: dict[str, Any], *, diagnose: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    allowed = set(fixture["output_top_level_keys"])
    keys = set(output)
    if keys != allowed:
        errors.append("top_level_keys_mismatch")
    forbidden = sorted(keys & set(fixture["forbidden_top_level_keys"]))
    if forbidden:
        errors.append("forbidden_top_level_keys:" + ",".join(forbidden))
    if not isinstance(output.get("facts"), list):
        errors.append("facts_must_be_list")
    if not isinstance(output.get("near"), list):
        errors.append("near_must_be_list")
    if not isinstance(output.get("missing"), list):
        errors.append("missing_must_be_list")
    if not isinstance(output.get("params"), dict):
        errors.append("params_must_be_object")
    if not isinstance(output.get("diagnostics"), dict):
        errors.append("diagnostics_must_be_object")
    diagnostics = output.get("diagnostics") if isinstance(output.get("diagnostics"), dict) else {}
    if set(diagnostics) - DIAGNOSTIC_KEYS:
        errors.append("diagnostics_extra_keys:" + ",".join(sorted(set(diagnostics) - DIAGNOSTIC_KEYS)))
    missing_diag_keys = DIAGNOSTIC_KEYS - set(diagnostics)
    if missing_diag_keys:
        errors.append("diagnostics_missing_keys:" + ",".join(sorted(missing_diag_keys)))
    if diagnostics.get("mcp_tool") != MCP_TOOL:
        errors.append("diagnostics_mcp_tool_mismatch")
    if diagnostics.get("response_viewpoint") != scenario["response_viewpoint"]:
        errors.append("diagnostics_response_viewpoint_mismatch")
    errors.extend(_validate_assertions(output, fixture, scenario))
    result: dict[str, Any] = {
        "ok": not errors,
        "errors": errors,
        "counts": {
            "facts": len(output.get("facts") or []) if isinstance(output.get("facts"), list) else 0,
            "near": len(output.get("near") or []) if isinstance(output.get("near"), list) else 0,
            "missing": len(output.get("missing") or []) if isinstance(output.get("missing"), list) else 0,
        },
    }
    if diagnose:
        ready_diagnostics = _ready_hard_failure_diagnostics(output, errors)
        if ready_diagnostics:
            result["hard_match_diagnostics"] = ready_diagnostics
    return result


def validate_fixture_case(fixture: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    payload = structured_input(fixture, scenario)
    goal = payload.get("search_goal")
    if not isinstance(goal, dict) or not goal.get("entity_type") or not goal.get("query_summary") or not isinstance(goal.get("explicit_terms"), list):
        errors.append("search_goal_shape_invalid")
    if payload["response_viewpoint"] not in fixture.get("scenario_field_priorities", {}):
        errors.append("unknown_response_viewpoint")
    if payload.get("base_viewpoint") is not None and payload.get("base_viewpoint") not in BASE_VIEWPOINTS:
        errors.append("unknown_base_viewpoint")
    if not isinstance(payload["constraints"], dict) or not {"requested_hard", "effective_hard", "preferences", "relaxation_audit"} <= set(payload["constraints"]):
        errors.append("constraints_shape_invalid")
    if not isinstance(payload["constraints"].get("preferences"), dict):
        errors.append("preferences_shape_invalid")
    if not isinstance(payload["constraints"].get("relaxation_audit"), list):
        errors.append("relaxation_audit_shape_invalid")
    if payload["count"] <= 0:
        errors.append("count_must_be_positive")
    for field in scenario.get("expected_field_priorities_include", []):
        if field not in payload["available_fact_fields"]:
            errors.append(f"expected_field_not_available:{field}")
    query = build_query(fixture, scenario)
    if query.count("SEARCH_CONTRACT_ENVELOPE=") != 1 or "Текущие параметры: " not in query or "\nКлиент: " not in query:
        errors.append("query_missing_compact_contract_or_params")
    if "V2_SEARCH_INPUT=" in query or "V2_SEARCH_MCP_CONTRACT=" in query:
        errors.append("query_uses_legacy_v2_input")
    return {"case": scenario["id"], "ok": not errors, "errors": errors, "network": False}


def _request_from_scenario(fixture: dict[str, Any], scenario: dict[str, Any]) -> core_contract.V2SearchRequest:
    constraints = scenario["constraints"]
    return core_contract.V2SearchRequest(
        search_goal=dict(scenario["search_goal"]),
        requested_hard=dict(constraints.get("requested_hard") or {}),
        effective_hard=dict(constraints.get("effective_hard") or {}),
        preferences={k: v for k, v in dict(constraints.get("preferences") or {}).items() if k in set(fixture.get("allowed_preferences") or DEFAULT_ALLOWED_PREFERENCES)},
        relaxation_audit=list(constraints.get("relaxation_audit") or []),
        response_viewpoint=str(scenario["response_viewpoint"]),
        base_viewpoint=scenario.get("base_viewpoint"),
        available_fact_fields=available_fact_fields(fixture, scenario),
        count=int(scenario["count"]),
        ignored_preferences=sorted(_unknown_preference_keys(fixture, scenario)),
    )


# Keep the CLI fixture semantics, but share the production V2 contract envelope,
# strict JSON parser and static request validator instead of duplicating those
# runtime rules in the probe harness.
def structured_input(fixture: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-redef]
    return _request_from_scenario(fixture, scenario).to_payload()


def build_query(fixture: dict[str, Any], scenario: dict[str, Any]) -> str:  # type: ignore[no-redef]
    return core_contract.build_query(
        _request_from_scenario(fixture, scenario),
        output_keys=fixture["output_top_level_keys"],
        forbidden_keys=fixture["forbidden_top_level_keys"],
    )


def build_request_data(fixture: dict[str, Any], scenario: dict[str, Any], *, prompt: str | None = None, model: str = SEARCH_MODEL) -> dict[str, Any]:  # type: ignore[no-redef]
    request = core_contract.build_request_data(_request_from_scenario(fixture, scenario), prompt=prompt if prompt is not None else load_prompt(), model=model)
    request.pop("external_api_key", None)
    return request


def parse_strict_json(text: str) -> tuple[dict[str, Any] | None, list[str]]:  # type: ignore[no-redef]
    return core_contract.parse_strict_json(text)


def validate_fixture_case(fixture: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-redef]
    base = core_contract.validate_fixture_case(_request_from_scenario(fixture, scenario))
    errors = list(base["errors"])
    query = build_query(fixture, scenario)
    for field in scenario.get("expected_field_priorities_include", []):
        if field not in structured_input(fixture, scenario)["available_fact_fields"]:
            errors.append(f"expected_field_not_available:{field}")
    if query.count("SEARCH_CONTRACT_ENVELOPE=") != 1 or "Текущие параметры: " not in query or "\nКлиент: " not in query:
        errors.append("query_missing_compact_contract_or_params")
    if "V2_SEARCH_INPUT=" in query or "V2_SEARCH_MCP_CONTRACT=" in query:
        errors.append("query_uses_legacy_v2_input")
    return {"case": scenario["id"], "ok": not errors, "errors": errors, "network": False}


async def gateway_request(request_data: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
    import aiohttp

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
            terminal_status = status_data.get("status")
            if terminal_status in _TERMINAL_TASK_STATUSES:
                async with session.get(f"{base}/api/v1/tasks/api/{task_id}/result", headers=headers) as resp:
                    result = await resp.json()
                obj = result.get("result") if isinstance(result, dict) else result
                diagnostics = _safe_gateway_task_diagnostics(
                    task_id=task_id,
                    terminal_status=terminal_status,
                    result_obj=obj,
                )
                if isinstance(obj, dict):
                    return str(obj.get("response") or ""), {"ok": not bool(obj.get("error")), "diagnostics": diagnostics}
                return str(obj), {"ok": terminal_status == "completed", "diagnostics": diagnostics}
            await asyncio.sleep(2)
    return "", {"ok": False, "error": "timeout", "diagnostics": {"task_id": str(task_id)[:80], "terminal_status": "poll_timeout"}}


async def run_live_case(case_id: str, *, timeout: int, gateway_func: Any = None, diagnose: bool = False) -> dict[str, Any]:
    fixture = load_fixture()
    scenario = scenarios_by_id(fixture)[case_id]
    load_env()
    started = time.monotonic()
    raw, meta = await (gateway_func or gateway_request)(build_request_data(fixture, scenario), timeout)
    parsed, parse_errors = parse_strict_json(raw)
    if parsed is None:
        validation = {"ok": False, "errors": parse_errors, "counts": {"facts": 0, "near": 0, "missing": 0}}
    else:
        parsed = core_contract.normalize_search_output(parsed, _request_from_scenario(fixture, scenario))
        validation = validate_output(parsed, fixture, scenario, diagnose=diagnose)
    gateway_meta: dict[str, Any] = {"ok": bool(meta.get("ok", False))}
    if isinstance(meta.get("diagnostics"), dict):
        gateway_meta["diagnostics"] = meta["diagnostics"]
    else:
        gateway_meta["metadata_keys"] = meta.get("metadata_keys", [])
    result = {
        "case": case_id,
        "ok": bool(validation["ok"] and meta.get("ok", True)),
        "network": True,
        "model": SEARCH_MODEL,
        "mcp_alias": MCP_ALIAS,
        "counts": validation["counts"],
        "errors": validation["errors"] + ([] if meta.get("ok", True) else ["gateway_not_ok"]),
        "gateway_meta": gateway_meta,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    if diagnose and validation.get("hard_match_diagnostics"):
        result["hard_match_diagnostics"] = validation["hard_match_diagnostics"]
    return result


def _selected_cases(args: argparse.Namespace, fixture: dict[str, Any]) -> list[str]:
    cases = scenarios_by_id(fixture)
    if args.case:
        if args.case not in cases:
            raise SystemExit(f"unknown case: {args.case}")
        return [args.case]
    if args.all or args.fixture_only:
        return list(cases)
    raise SystemExit("pass --fixture-only, --case ID, or --all")


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Read-only V2 MCP search prompt probe harness")
    parser.add_argument("--case", dest="case", help="single fixture scenario id to probe")
    parser.add_argument("--all", action="store_true", help="run all cases sequentially")
    parser.add_argument("--fixture-only", action="store_true", help="validate fixture/input construction only; no network")
    parser.add_argument("--diagnose", action="store_true", help="include bounded local-only hard-match diagnostics in summaries")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    if args.case and args.all:
        raise SystemExit("choose either --case or --all")
    fixture = load_fixture()
    case_ids = _selected_cases(args, fixture)
    if args.fixture_only:
        results = [validate_fixture_case(fixture, scenarios_by_id(fixture)[case_id]) for case_id in case_ids]
    else:
        results = []
        for case_id in case_ids:
            result = await run_live_case(case_id, timeout=args.timeout, diagnose=args.diagnose)
            results.append(result)
            if args.all and not result["ok"]:
                break
    status = {"ok": all(item["ok"] for item in results), "network": not args.fixture_only, "results": results}
    print(json.dumps(status, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
