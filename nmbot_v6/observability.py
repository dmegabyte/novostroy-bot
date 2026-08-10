"""Privacy-safe V6 turn trace for local and operational diagnostics."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .provider import TrustedMcpEnvelope
from .prompt1_contract import Prompt1Result
from .state import V6State

_SECRET_KEY = re.compile(r"phone|email|token|secret|password|authorization|prompt", re.I)
_PHONEISH = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)")
_EMAILISH = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_SECRETISH_VALUE = re.compile(
    r"(?:bearer\s+\S+|(?:api[_-]?key|token|secret|password|authorization|prompt|query|response)\s*[:=]|(?:sk|pk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]{16,})",
    re.I,
)
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_PRICE_KEYS = {"price", "price_min", "price_max", "price_range", "min_price", "max_price"}
_SAFE_FACT_KEYS = frozenset({
    "name", "ref", "id", "object_id", "option_ref", "district", "location",
    "price", "price_min", "price_max", "price_range", "finishing", "metro",
    "area", "rooms", "ready", "developer", "link", "infrastructure",
    "family_infrastructure", "schools", "kindergartens", "parks", "shops",
    "clinics", "yards", "transport", "why_family", "why_close", "count",
    "result_count",
})
_SAFE_PROJECTION_CONTAINERS = frozenset({"cards", "facts", "near", "results", "options", "data"})
_SAFE_CONSTRAINT_KEYS = frozenset({
    "rooms", "min_price", "max_price", "district", "floor", "has_renovation",
    "count", "purpose", "facets", "mortgage_type",
})
_SAFE_ACTIONS = frozenset({"search", "clarify", "operator_contact", "recover_dialogue", "answer_current_options"})
_SAFE_TARGETS = frozenset({"new_search", "current_options", "none"})
_SAFE_SEARCH_POLICIES = frozenset({"required", "forbidden"})
_SAFE_FAILURE_CODES = frozenset({
    "invalid_input", "phone_dependency_unavailable", "provider_failure",
    "mode_off", "missing_v6_ports", "missing_state_store", "invalid_v6_state",
    "shadow_phone_bypass", "missing_callback_outbox", "callback_enqueue_failed",
    "callback_not_queued", "unexpected_phone_bypass", "v6_runtime_failed",
    "state_save_failed", "shadow_only",
})
_SAFE_FAILURE_STAGES = frozenset({"input", "phone", "prompt1", "mcp", "prompt2", "state", "unknown"})
_SAFE_PROMPT_STATUSES = frozenset({"not_called", "accepted", "failed", "unknown"})
_SAFE_MCP_STATUSES = frozenset({"not_called", "accepted", "failed", "unknown"})
_SAFE_BOT_STATUSES = frozenset({"not_sent", "prepared", "returned", "unknown"})
_SAFE_FOLLOWUP_STATUSES = frozenset({"not_present", "unresolved", "resolved", "replaced"})
_SAFE_FOLLOWUP_ACTIONS = frozenset({"accept", "reject", "select", "normal_prompt1", "unknown"})


def build_turn_trace(*, before: V6State, after: V6State,
                     plan: Prompt1Result | None = None,
                     envelope: TrustedMcpEnvelope | None = None,
                      prompt1_status: str = "not_called",
                      mcp_status: str | None = None,
                      prompt2_status: str = "not_called",
                      bot_message_status: str = "not_sent",
                      failure_code: str | None = None,
                      failure_stage: str | None = None) -> dict[str, Any]:
    followup = _followup_trace(before, after)
    if followup["status"] == "resolved":
        prompt1_status = "not_called"
        prompt2_status = "not_called"
        mcp_status = "not_called"
    effective_mcp_status = mcp_status or (
        "accepted" if envelope is not None and envelope.call_count else "not_called"
    )
    stages: list[dict[str, Any]] = [
        {"stage": "user", "owner": "client", "status": "received"},
        {"stage": "prompt1", "owner": "model", "model": "google/gemini-3.1-flash-lite-preview", "status": _safe_status(prompt1_status, _SAFE_PROMPT_STATUSES)},
    ]
    if plan is not None and plan.mcp_audit is not None:
        stages[-1]["prompt1_mcp_audit"] = _safe_audit(plan.mcp_audit)
    mcp_stage: dict[str, Any] = {
        "stage": "mcp", "owner": "transport",
        "status": _safe_status(effective_mcp_status, _SAFE_MCP_STATUSES),
    }
    if envelope is not None:
        mcp_stage.update({
            "server": "novostroym" if envelope.actual_server == "novostroym" else "unknown",
            "tool": "get_flat_info" if envelope.actual_tool == "get_flat_info" else "unknown",
            "evidence_source": envelope.evidence_source,
            "task_ref": _safe_ref(envelope.task_ref),
            "call_count": _bounded_count(envelope.call_count),
            "effective_constraints": _safe_value(envelope.effective_constraints, allowed_keys=_SAFE_CONSTRAINT_KEYS),
            "safe_projection": _safe_value(
                envelope.safe_facts,
                allowed_keys=_SAFE_FACT_KEYS | _SAFE_PROJECTION_CONTAINERS,
            ),
            "visible_refs": [_safe_ref(ref) for ref in envelope.visible_refs if _safe_ref(ref)][:3],
        })
    stages.append(mcp_stage)
    stages.extend([
        {"stage": "prompt2", "owner": "model", "model": "google/gemini-3.1-flash-lite-preview", "status": _safe_status(prompt2_status, _SAFE_PROMPT_STATUSES)},
        {"stage": "bot_message", "owner": "jivo", "status": _safe_status(bot_message_status, _SAFE_BOT_STATUSES)},
    ])
    result: dict[str, Any] = {"schema_version": 1, "stages": stages,
                              "state": {"before_revision": before.revision, "after_revision": after.revision}}
    result["followup"] = followup
    if plan is not None:
        result["plan"] = {
            "action": _safe_enum(plan.action.value, _SAFE_ACTIONS),
            "target": _safe_enum(plan.target.value, _SAFE_TARGETS),
            "search_policy": _safe_enum(plan.search_policy.value, _SAFE_SEARCH_POLICIES),
        }
    if failure_code:
        result["failure_code"] = _safe_code(failure_code)
    if failure_stage:
        result["failure_stage"] = _safe_enum(failure_stage, _SAFE_FAILURE_STAGES)
    return result


def _followup_trace(before: V6State, after: V6State) -> dict[str, str]:
    if before.pending_interaction is None:
        return {"status": "not_present", "action": "unknown"}
    action = str(after.safe_context.get("last_followup_action") or "unknown")
    if action in _SAFE_FOLLOWUP_ACTIONS and action != "unknown":
        return {"status": "resolved", "action": action}
    if before.revision == after.revision:
        return {"status": "unresolved", "action": "unknown"}
    return {"status": "replaced", "action": "normal_prompt1"}


def _safe_value(
    value: Any,
    *,
    key: str = "",
    depth: int = 0,
    allowed_keys: frozenset[str] | None = None,
) -> Any:
    if depth > 4 or _SECRET_KEY.search(key):
        return "[redacted]"
    if isinstance(value, Mapping):
        allowed = allowed_keys or (_SAFE_FACT_KEYS | _SAFE_PROJECTION_CONTAINERS | _SAFE_CONSTRAINT_KEYS)
        return {
            str(k): _safe_value(v, key=str(k), depth=depth + 1, allowed_keys=allowed)
            for k, v in list(value.items())[:40]
            if str(k) in allowed and not _SECRET_KEY.search(str(k))
        }
    if isinstance(value, (list, tuple)):
        if key and key not in _SAFE_PROJECTION_CONTAINERS and key not in {
            "facets", "infrastructure", "family_infrastructure", "schools",
            "kindergartens", "parks", "shops", "clinics", "yards", "transport",
        }:
            return "[redacted]"
        return [_safe_value(item, depth=depth + 1, allowed_keys=allowed_keys) for item in list(value)[:10]]
    if isinstance(value, str):
        return "[redacted]" if (
            _PHONEISH.search(value) or _EMAILISH.search(value) or _SECRETISH_VALUE.search(value)
        ) else value[:500]
    if isinstance(value, (bool, int, float)) or value is None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            digits = str(abs(value)).split(".", 1)[0]
            if len(digits) in range(10, 16) and key not in _PRICE_KEYS:
                return "[redacted]"
        return value
    return "[redacted]"


def _safe_ref(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if _SAFE_REF.fullmatch(text) and not text.isdigit() and not _SECRETISH_VALUE.search(text) else None


def _safe_audit(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result = {}
    for key in ("tool", "arguments", "sql_audit"):
        item = value.get(key)
        if key == "sql_audit" and isinstance(item, Mapping):
            result[key] = {
                "query": _safe_value(item.get("query"), key="audit_value"),
                "parameters": _safe_audit_parameters(item.get("parameters", {})),
            }
        else:
            result[key] = _safe_value(item, key="audit_value") if item is not None else None
    result["result_count"] = value.get("result_count")
    objects = []
    returned = value.get("returned_objects", [])
    for item in list(returned)[:20] if isinstance(returned, (list, tuple)) else []:
        if not isinstance(item, Mapping):
            continue
        clean = _safe_value(item, key="card", allowed_keys=frozenset({"id", "name", "price_mod", "price1", "price2", "price3", "price4", "price_n", "price_s"}))
        clean["ads"] = [_safe_value(ad, key="card", allowed_keys=frozenset({"id", "state", "status"})) for ad in item.get("ads", [])[:20] if isinstance(ad, Mapping)]
        objects.append(clean)
    result["returned_objects"] = objects
    result["selected_objects"] = _safe_value(value.get("selected_objects", []), key="cards")
    result["condition_audit"] = {
        key: value.get("condition_audit", {}).get(key) is True
        for key in ("requested_in_prompt", "visible_in_tool_arguments", "visible_in_tool_response", "application_confirmed")
    }
    result["truncated"] = value.get("truncated") is True
    result["missing_evidence"] = _safe_value(value.get("missing_evidence", []), key="cards")
    return result


def _safe_audit_parameters(value: Any, depth: int = 0) -> Any:
    if depth > 3 or not isinstance(value, Mapping):
        return {}
    result = {}
    for key, item in list(value.items())[:32]:
        key = str(key)
        if _SECRET_KEY.search(key):
            continue
        if isinstance(item, Mapping):
            result[key] = _safe_audit_parameters(item, depth + 1)
        elif isinstance(item, (list, tuple)):
            result[key] = [_safe_audit_parameters(v, depth + 1) if isinstance(v, Mapping) else _safe_value(v, key=key) for v in list(item)[:20]]
        else:
            result[key] = _safe_value(item, key=key)
    return result


def _bounded_count(value: Any) -> int:
    return 1 if value == 1 and not isinstance(value, bool) else 0


def _safe_code(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in _SAFE_FAILURE_CODES else "unknown"


def _safe_enum(value: Any, allowed: frozenset[str]) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else "unknown"


def _safe_status(value: Any, allowed: frozenset[str]) -> str:
    return _safe_enum(value, allowed)
