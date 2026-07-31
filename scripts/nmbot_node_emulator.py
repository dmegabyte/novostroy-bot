#!/usr/bin/env python3
"""Local-only read-only node-level emulator for nmbot decision architecture.

Stdlib-only shadow evaluator. It inspects typed fixture/saved JSON node by node
without importing production modules and without touching model/network/Jivo/CRM.
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ACTION_ENUM = {"search", "answer_current_options", "recover_dialogue", "operator_contact", "clarify"}
INTENT_ENUM = {"investment", "family", "life", "mortgage", "compare", "unknown"}
TARGET_ENUM = {"new_search", "current_options", "none", "operator"}
SEARCH_POLICY_ENUM = {"required", "forbidden", "allowed"}
INTENT_POLICY_ENUM = {"keep", "set", "change"}
CONSTRAINT_CATEGORIES = {"hard", "preferences", "unknown"}
CURRENT_PARAM_HARD_FIELDS = {
    "location",
    "locations",
    "district",
    "districts",
    "metro",
    "near_metro",
    "rooms",
    "room_type",
    "max_price",
    "max_budget_m",
    "min_price",
    "area_min_m2",
    "area_max_m2",
    "finishing",
    "renovation",
    "ready",
    "stage",
    "ready_quarter",
    "delivery_visible",
    "project_ready_secondary",
    "property_metro",
    "schools",
    "kindergartens",
    "parks",
    "shops",
    "family_infrastructure",
}
CURRENT_PARAM_PREFERENCE_FIELDS = {"purpose", "scenario", "topic", "mortgage", "discount", "installment", "payment_by_installments"}
OPTION_STATUS_ORDER = {"matched": 0, "near_match": 1, "unknown": 2, "rejected": 3}
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\s()\-]*){10,15}(?!\d)")
SECRET_RE = re.compile(r"(?i)(sk-[A-Za-z0-9_-]{12,}|(?:token|secret|apikey|api_key)[:=][A-Za-z0-9_.-]{8,})")

SAFE_NODE_KEYS = {
    "planner_adapter": {"format", "adapted", "adapter_source_action", "coverage_gaps"},
    "planner_output": {"valid", "errors", "action", "intent", "intent_policy", "target", "search_policy", "confidence", "constraints_patch", "facets", "missing_fields", "clarification_fields"},
    "context_merge": {"known_intent", "constraints", "known_fields", "provenance", "visible_options_count", "expected_target"},
    "transition_guard": {"passed", "failures", "guarded_action", "guarded_target", "guarded_search_policy"},
    "search_normalizer": {"options"},
    "constraint_validator": {"options", "summary", "notes"},
    "decision_context": {"matched", "near_match", "rejected_count", "unknowns", "failed_constraints", "allowed_claims", "do_not_say", "source_refs", "relaxation_needed"},
    "execution_gate": {"planner", "search", "presenter", "uses_preserved_options", "reason"},
    "result": {"passed", "status", "failures", "missing_expected_failures", "unexpected_failures", "expectation_failures", "architecture_classes"},
}
SENSITIVE_KEY_PARTS = ("phone", "token", "auth", "payload", "client_id", "chat_id", "site_id", "visitor_id", "raw_user_text", "user_text")


@dataclass(frozen=True)
class InvariantFailure:
    code: str
    architecture_class: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "architecture_class": self.architecture_class, "detail": self.detail}


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _redact_string(value: str) -> str:
    value = PHONE_RE.sub("<redacted_phone>", value)
    value = SECRET_RE.sub("<redacted_secret>", value)
    return value if len(value) <= 120 else value[:117] + "..."


def _redact_text(value: str) -> str:
    value = PHONE_RE.sub("<redacted_phone>", value)
    return SECRET_RE.sub("<redacted_secret>", value)


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            result[str(key)] = "<redacted>" if _is_sensitive_key(str(key)) else redact_value(item)
        return result
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def safe_node_output(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = SAFE_NODE_KEYS[node_name]
    return {key: redact_value(payload[key]) for key in payload if key in allowed}


def _has_current_options(state: dict[str, Any]) -> bool:
    return bool(state.get("visible_options") or state.get("last_options"))


def _detect_planner_format(raw: Any, requested: str) -> str:
    if requested != "auto":
        return requested
    if isinstance(raw, dict) and "dialog_action" in raw:
        return "current"
    if isinstance(raw, dict) and "action" in raw:
        return "canonical"
    return "canonical"


def _unknown_patch_from_params(params_delta: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(params_delta, dict) or not params_delta:
        return {}, []
    unknown: dict[str, Any] = {}
    for key, value in params_delta.items():
        if value not in (None, "", [], {}):
            unknown[str(key)] = deepcopy(value)
    return unknown, ["constraint_category_untyped"] if unknown else []


def _current_patch_from_params(params_delta: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(params_delta, dict) or not params_delta:
        return {"hard": {}, "preferences": {}, "unknown": {}}, []
    patch = {"hard": {}, "preferences": {}, "unknown": {}}
    gaps: list[str] = []
    for key, value in params_delta.items():
        if value in (None, "", [], {}):
            continue
        field = str(key)
        if field in CURRENT_PARAM_HARD_FIELDS:
            patch["hard"][field] = deepcopy(value)
        elif field in CURRENT_PARAM_PREFERENCE_FIELDS:
            patch["preferences"][field] = deepcopy(value)
        else:
            patch["unknown"][field] = deepcopy(value)
            gaps.append("constraint_category_untyped")
    return patch, sorted(set(gaps))


def planner_adapter(raw: Any, state: dict[str, Any], planner_format: str = "auto") -> dict[str, Any]:
    fmt = _detect_planner_format(raw, planner_format)
    gaps: list[str] = []
    canonical: dict[str, Any]
    source_action = ""
    if fmt == "canonical":
        canonical = deepcopy(raw) if isinstance(raw, dict) else {}
        source_action = str(canonical.get("action") or "")[:80]
        return {"format": fmt, "adapted": isinstance(raw, dict), "adapter_source_action": source_action, "coverage_gaps": [], "canonical_payload": canonical}
    if fmt != "current" or not isinstance(raw, dict):
        return {"format": fmt, "adapted": False, "adapter_source_action": "", "coverage_gaps": ["planner_format_unrecognized"], "canonical_payload": {"action": "recover_dialogue", "intent": "unknown", "intent_policy": "keep", "target": "none", "search_policy": "forbidden", "confidence": 0.0, "constraints_patch": {}, "facets": {}, "missing_fields": [], "clarification_fields": []}}

    source_action = str(raw.get("dialog_action") or "")[:80]
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    params_patch, param_gaps = _current_patch_from_params(raw.get("params_delta"))
    gaps.extend(param_gaps)
    gaps.append("primary_intent_unavailable")
    action = "recover_dialogue"
    target = "none"
    search_policy = "forbidden"
    current_options = _has_current_options(state)
    if raw.get("fallback_used") or confidence < 0.45 or source_action in {"fallback", "unknown", "unsupported"}:
        action, target, search_policy = "recover_dialogue", "none", "forbidden"
    elif source_action in {"new_search", "update_search", "expand_more_options"}:
        action, target, search_policy = "search", "new_search", "required"
    elif source_action in {"consultation_answer", "conversation_answer", "compare_options", "recommend_options", "continue_from_memory", "select_option"}:
        if current_options:
            action, target, search_policy = "answer_current_options", "current_options", "forbidden"
        else:
            action, target, search_policy = "recover_dialogue", "none", "forbidden"
            gaps.append("current_options_context_absent")
    elif source_action in {"ask_clarification", "clarify_negation"}:
        action, target, search_policy = "clarify", "none", "forbidden"
        if raw.get("clarification_question"):
            gaps.append("clarification_fields_unavailable")
    elif source_action == "operator_live_check":
        action, target, search_policy = "operator_contact", "operator", "forbidden"
    elif source_action in {"reject_request", "refuse", "out_of_scope", "not_real_estate"}:
        action, target, search_policy = "recover_dialogue", "none", "forbidden"
    else:
        action, target, search_policy = "recover_dialogue", "none", "forbidden"
        gaps.append("dialog_action_unmapped")
    canonical = {
        "action": action,
        "intent": "unknown",
        "intent_policy": "keep",
        "target": target,
        "search_policy": search_policy,
        "confidence": max(0.0, min(1.0, confidence)),
        "constraints_patch": params_patch,
        "facets": {},
        "missing_fields": [],
        "clarification_fields": [],
    }
    return {"format": fmt, "adapted": True, "adapter_source_action": source_action, "coverage_gaps": sorted(set(gaps)), "canonical_payload": canonical}


def validate_planner_output(raw: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return {"valid": False, "errors": ["planner_output_not_object"], "action": "recover_dialogue", "intent": "unknown", "intent_policy": "keep", "target": "none", "search_policy": "forbidden", "confidence": 0.0, "constraints_patch": {}, "facets": {}, "missing_fields": [], "clarification_fields": []}
    action = raw.get("action")
    intent = raw.get("intent")
    intent_policy = raw.get("intent_policy", "keep")
    target = raw.get("target")
    search_policy = raw.get("search_policy")
    confidence = raw.get("confidence")
    constraints_patch = raw.get("constraints_patch", {})
    facets = raw.get("facets", {})
    missing_fields = raw.get("missing_fields", [])
    clarification_fields = raw.get("clarification_fields", [])
    if action not in ACTION_ENUM:
        errors.append("invalid_action")
    if intent not in INTENT_ENUM:
        errors.append("invalid_intent")
    if intent_policy not in INTENT_POLICY_ENUM:
        errors.append("invalid_intent_policy")
    if target not in TARGET_ENUM:
        errors.append("invalid_target")
    if search_policy not in SEARCH_POLICY_ENUM:
        errors.append("invalid_search_policy")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
        errors.append("invalid_confidence")
        confidence = 0.0
    if not isinstance(constraints_patch, dict):
        errors.append("invalid_constraints_patch")
        constraints_patch = {}
    else:
        for category, fields in constraints_patch.items():
            if category not in CONSTRAINT_CATEGORIES or not isinstance(fields, dict):
                errors.append("invalid_constraints_category")
                break
    if not isinstance(facets, dict):
        errors.append("invalid_facets")
        facets = {}
    if not isinstance(missing_fields, list) or any(not isinstance(item, str) for item in missing_fields):
        errors.append("invalid_missing_fields")
        missing_fields = []
    if not isinstance(clarification_fields, list) or any(not isinstance(item, str) for item in clarification_fields):
        errors.append("invalid_clarification_fields")
        clarification_fields = []
    return {"valid": not errors, "errors": errors, "action": action if action in ACTION_ENUM else "recover_dialogue", "intent": intent if intent in INTENT_ENUM else "unknown", "intent_policy": intent_policy if intent_policy in INTENT_POLICY_ENUM else "keep", "target": target if target in TARGET_ENUM else "none", "search_policy": search_policy if search_policy in SEARCH_POLICY_ENUM else "forbidden", "confidence": float(confidence or 0.0), "constraints_patch": constraints_patch, "facets": facets, "missing_fields": missing_fields, "clarification_fields": clarification_fields}


def _known_fields_from_state(state: dict[str, Any]) -> set[str]:
    fields = set(state.get("known_fields") or [])
    if state.get("known_intent"):
        fields.update({"primary_intent", "purpose"})
    for category in CONSTRAINT_CATEGORIES:
        fields.update((state.get("constraints") or {}).get(category, {}).keys())
    return fields


def context_merge(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    merged = {"known_intent": state.get("known_intent"), "constraints": deepcopy(state.get("constraints") or {"hard": {}, "preferences": {}, "unknown": {}}), "known_fields": sorted(_known_fields_from_state(state)), "provenance": deepcopy(state.get("provenance") or {}), "visible_options": deepcopy(state.get("visible_options") or []), "last_options": deepcopy(state.get("last_options") or []), "expected_target": state.get("expected_target")}
    for category in CONSTRAINT_CATEGORIES:
        merged["constraints"].setdefault(category, {})
    plan_intent = plan.get("intent")
    policy = plan.get("intent_policy", "keep")
    if plan_intent in INTENT_ENUM and plan_intent != "unknown":
        if policy == "set" and not merged["known_intent"]:
            merged["known_intent"] = plan_intent
            merged["provenance"]["primary_intent"] = {"node": "planner_output", "policy": "set"}
        elif policy == "change":
            old = merged.get("known_intent")
            merged["known_intent"] = plan_intent
            merged["provenance"]["primary_intent"] = {"node": "planner_output", "policy": "change", "from": old, "to": plan_intent}
        elif policy == "keep" and not merged["known_intent"]:
            # keep means do not overwrite an existing state value; it may initialize absent state safely.
            merged["known_intent"] = plan_intent
            merged["provenance"]["primary_intent"] = {"node": "planner_output", "policy": "keep_init"}
    if merged.get("known_intent"):
        merged["known_fields"] = sorted(set(merged["known_fields"]) | {"primary_intent", "purpose"})
    patch = plan.get("constraints_patch") if isinstance(plan.get("constraints_patch"), dict) else {}
    for category in ("hard", "preferences", "unknown"):
        for field, value in (patch.get(category) or {}).items():
            if value in (None, "", [], {}):
                continue
            from_category = None
            for other in ("hard", "preferences", "unknown"):
                if other != category and field in merged["constraints"].get(other, {}):
                    from_category = other
                    del merged["constraints"][other][field]
            merged["constraints"][category][field] = deepcopy(value)
            merged["provenance"][field] = {"node": "planner_output", "from_category": from_category, "to_category": category}
            merged["known_fields"] = sorted(set(merged["known_fields"]) | {field})
    return {"known_intent": merged["known_intent"], "constraints": merged["constraints"], "known_fields": merged["known_fields"], "provenance": merged["provenance"], "visible_options_count": len(merged["visible_options"]), "expected_target": merged.get("expected_target"), "_state": merged}


def transition_guard(plan: dict[str, Any], merged_state: dict[str, Any]) -> dict[str, Any]:
    failures: list[InvariantFailure] = []
    valid = bool(plan.get("valid"))
    action = plan.get("action") if valid else "recover_dialogue"
    target = plan.get("target") if valid else "none"
    search_policy = plan.get("search_policy") if valid else "forbidden"
    state = merged_state.get("_state", {})
    if not valid:
        failures.append(InvariantFailure("invalid_planner_output", "invalid_planner_output", "invalid typed planner output fails closed"))
    current_options_scope = action == "answer_current_options" or target == "current_options" or state.get("expected_target") == "current_options"
    if current_options_scope and (action == "search" or search_policy == "required"):
        failures.append(InvariantFailure("current_options_search_forbidden", "current_options_followup_started_new_search", "current-options scoped request must not start search"))
    if action == "recover_dialogue" and (target != "none" or search_policy != "forbidden"):
        failures.append(InvariantFailure("recovery_must_not_route", "recovery_route_leak", "recover_dialogue cannot search/list/operator"))
    if action == "clarify" and (target != "none" or search_policy != "forbidden"):
        failures.append(InvariantFailure("clarify_must_not_route", "clarify_route_leak", "clarify cannot search/list/operator"))
    known_fields = set(state.get("known_fields") or [])
    redundant = sorted(known_fields & (set(plan.get("missing_fields") or []) | set(plan.get("clarification_fields") or [])))
    if redundant:
        failures.append(InvariantFailure("known_field_reasked", "intent_loss_redundant_clarification", "planner asked fields already known: " + ",".join(redundant)))
    if float(plan.get("confidence") or 0.0) < 0.45 and action == "search":
        failures.append(InvariantFailure("low_confidence_search", "low_confidence_unsafe_search", "low confidence must clarify or recover, not search"))
    if failures:
        if not valid or action in {"recover_dialogue", "clarify"} or float(plan.get("confidence") or 0.0) < 0.45:
            action, target, search_policy = "recover_dialogue", "none", "forbidden"
        elif current_options_scope:
            action, target, search_policy = "answer_current_options", "current_options", "forbidden"
    return {"passed": not failures, "failures": [failure.to_dict() for failure in failures], "guarded_action": action, "guarded_target": target, "guarded_search_policy": search_policy}


def search_normalizer(search_fixture: dict[str, Any]) -> dict[str, Any]:
    options = []
    for idx, raw in enumerate(search_fixture.get("options") or []):
        if not isinstance(raw, dict):
            continue
        facts = raw.get("facts") if isinstance(raw.get("facts"), dict) else {}
        safe_id = str(raw.get("option_id") or f"option_{idx + 1}")[:80]
        options.append({"option_id": safe_id, "label": str(raw.get("label") or safe_id)[:120], "facts": redact_value(deepcopy(facts)), "source_ref": str(redact_value(raw.get("source_ref") or f"fixture:{safe_id}"))[:160]})
    options.sort(key=lambda item: item["option_id"])
    return {"options": options}


def _compare(operator: str, actual: Any, expected: Any) -> bool | None:
    if actual is None:
        return None
    if operator == "exact":
        return actual == expected
    if operator == "in":
        return actual in expected if isinstance(expected, list) else False
    if operator == "max":
        return float(actual) <= float(expected)
    if operator == "min":
        return float(actual) >= float(expected)
    if operator == "boolean":
        return bool(actual) is bool(expected)
    if operator == "known":
        return actual not in (None, "", [], {})
    raise ValueError(f"unsupported_operator:{operator}")


def constraint_validator(normalized: dict[str, Any], constraints: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    validated = []
    summary = {"matched": 0, "near_match": 0, "rejected": 0, "unknown": 0}
    notes: list[str] = []
    for option in normalized.get("options") or []:
        passed: list[str] = []
        failed: list[dict[str, Any]] = []
        unknown: list[str] = []
        has_hard_failure = has_preference_failure = has_hard_unknown = False
        for category in ("hard", "preferences"):
            for constraint_field, expected in (constraints.get(category) or {}).items():
                rule = schema.get(constraint_field) or {"operator": "exact", "fact_field": constraint_field}
                operator = rule.get("operator", "exact")
                fact_field = rule.get("fact_field", constraint_field)
                try:
                    ok = _compare(operator, option.get("facts", {}).get(fact_field), expected)
                except (TypeError, ValueError):
                    ok = False
                record = {"field": constraint_field, "fact_field": fact_field, "category": category, "operator": operator, "expected": expected}
                if ok is None:
                    unknown.append(constraint_field)
                    if rule.get("insufficient_if_absent"):
                        notes.append(f"{option['option_id']}:{constraint_field}: aggregate project range is insufficient evidence without {fact_field}")
                    if category == "hard":
                        has_hard_unknown = True
                elif ok:
                    passed.append(constraint_field)
                else:
                    failed.append(record)
                    if category == "hard":
                        has_hard_failure = True
                    else:
                        has_preference_failure = True
        status = "rejected" if has_hard_failure else "unknown" if has_hard_unknown else "near_match" if has_preference_failure else "matched"
        summary[status] += 1
        validated.append({"option_id": option["option_id"], "label": option["label"], "status": status, "passed": passed, "failed": failed, "unknown": unknown, "facts": option["facts"], "source_ref": option["source_ref"]})
    validated.sort(key=lambda item: (OPTION_STATUS_ORDER[item["status"]], item["option_id"]))
    return {"options": validated, "summary": summary, "notes": sorted(set(notes))}


def decision_context(validated: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    matched: list[dict[str, str]] = []
    near: list[dict[str, str]] = []
    rejected_count = 0
    unknowns: dict[str, list[str]] = {}
    failed_constraints: dict[str, list[dict[str, Any]]] = {}
    allowed_claims: dict[str, list[str]] = {}
    do_not_say: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    source_refs: dict[str, str] = {}
    claim_fields = [field for field, rule in schema.items() if rule.get("claim")]
    for option in validated.get("options") or []:
        oid = option["option_id"]
        if option["status"] == "rejected":
            rejected_count += 1
        else:
            public = {"option_id": oid, "label": option["label"], "status": option["status"]}
            if option["status"] == "matched":
                matched.append(public)
            elif option["status"] == "near_match":
                near.append(public)
            elif option["status"] == "unknown":
                unknowns[oid] = list(option.get("unknown") or [])
            source_refs[oid] = option["source_ref"]
        if option.get("failed"):
            failed_constraints[oid] = option["failed"]
        for field in list(option.get("unknown") or []) + claim_fields:
            fact_field = (schema.get(field) or {}).get("fact_field", field)
            missing = option.get("facts", {}).get(fact_field) in (None, "", [], {})
            if field in option.get("unknown", []) or missing:
                marker = (oid, field)
                if marker not in seen:
                    do_not_say.append({"option_id": oid, "field": field})
                    seen.add(marker)
            elif option["status"] != "rejected" and field in set(option.get("passed") or []):
                allowed_claims.setdefault(oid, []).append(field)
    return {"matched": matched, "near_match": near, "rejected_count": rejected_count, "unknowns": unknowns, "failed_constraints": failed_constraints, "allowed_claims": allowed_claims, "do_not_say": do_not_say, "source_refs": source_refs, "relaxation_needed": not matched}


def execution_gate(guard: dict[str, Any], merged_state: dict[str, Any]) -> dict[str, Any]:
    action = guard.get("guarded_action")
    search = action == "search" and guard.get("guarded_search_policy") == "required"
    uses_preserved = action == "answer_current_options"
    return {"planner": True, "search": bool(search), "presenter": action in {"answer_current_options", "recover_dialogue", "search", "clarify"}, "uses_preserved_options": uses_preserved and bool((merged_state.get("_state") or {}).get("visible_options") or (merged_state.get("_state") or {}).get("last_options")), "reason": action}


def _ids(items: list[dict[str, Any]]) -> list[str]:
    return [item["option_id"] for item in items]


def _expectation_failures(nodes: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not expected:
        return ["missing_expected_node_outcomes"]
    guard = nodes["transition_guard"]
    gate = nodes["execution_gate"]
    dctx = nodes["decision_context"]
    summary = nodes["constraint_validator"]["summary"]
    statuses = {item["option_id"]: item["status"] for item in nodes["constraint_validator"]["options"]}
    adapter_gaps = set(nodes.get("planner_adapter", {}).get("coverage_gaps") or [])
    for gap in expected.get("adapter_coverage_gaps", []):
        if gap not in adapter_gaps:
            failures.append(f"missing adapter coverage gap {gap}")
    if "guarded_action" in expected and guard.get("guarded_action") != expected["guarded_action"]:
        failures.append(f"guarded_action expected {expected['guarded_action']} got {guard.get('guarded_action')}")
    if "search_gate" in expected and gate.get("search") is not expected["search_gate"]:
        failures.append(f"search_gate expected {expected['search_gate']} got {gate.get('search')}")
    for key in ("matched", "near_match", "rejected", "unknown"):
        if key in expected.get("counts", {}) and summary.get(key) != expected["counts"][key]:
            failures.append(f"count {key} expected {expected['counts'][key]} got {summary.get(key)}")
    for oid, status in (expected.get("option_statuses") or {}).items():
        if statuses.get(oid) != status:
            failures.append(f"option {oid} expected {status} got {statuses.get(oid)}")
    presenter_ids = _ids(dctx["matched"]) + _ids(dctx["near_match"])
    if "presenter_option_ids" in expected and presenter_ids != expected["presenter_option_ids"]:
        failures.append(f"presenter ids expected {expected['presenter_option_ids']} got {presenter_ids}")
    for oid in expected.get("rejected_ids_absent", []):
        if oid in presenter_ids or oid in dctx.get("source_refs", {}):
            failures.append(f"rejected id leaked into decision_context: {oid}")
    if "relaxation_needed" in expected and dctx.get("relaxation_needed") is not expected["relaxation_needed"]:
        failures.append(f"relaxation_needed expected {expected['relaxation_needed']} got {dctx.get('relaxation_needed')}")
    required_dns = [tuple(item) for item in expected.get("do_not_say", [])]
    actual_dns = {(item["option_id"], item["field"]) for item in dctx.get("do_not_say", [])}
    for oid, field in required_dns:
        if (oid, field) not in actual_dns:
            failures.append(f"missing do_not_say {(oid, field)}")
    return failures


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _result(nodes: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    failures: list[dict[str, str]] = list(nodes["transition_guard"].get("failures") or [])
    gate = nodes["execution_gate"]
    guard = nodes["transition_guard"]
    if guard.get("guarded_action") in {"answer_current_options", "recover_dialogue"} and gate.get("search"):
        failures.append(InvariantFailure("gate_search_leak", "execution_gate_leak", "forbidden action enabled search").to_dict())
    if guard.get("guarded_action") == "search" and not gate.get("search"):
        failures.append(InvariantFailure("required_search_disabled", "execution_gate_wrong", "search action did not enable search").to_dict())
    actual = _dedupe([failure["architecture_class"] for failure in failures])
    expected = set(scenario.get("expected_failures") or [])
    unexpected = [failure for failure in failures if failure["architecture_class"] not in expected and failure["code"] not in expected]
    actual_codes_or_classes = {failure["architecture_class"] for failure in failures} | {failure["code"] for failure in failures}
    missing = sorted(item for item in expected if item not in actual_codes_or_classes)
    expectation_failures = _expectation_failures(nodes, scenario.get("expected") or {})
    passed = not unexpected and not missing and not expectation_failures
    expected_status = scenario.get("expected_status")
    if missing or unexpected or expectation_failures:
        status = "not_tested" if expectation_failures == ["missing_expected_node_outcomes"] else "expectation_failed"
    elif expected:
        status = "emulator_correctly_detected_expected_defect"
    elif expected_status == "not_tested":
        status = "not_tested"
    else:
        status = "architecture_guard_supported"
    if expected_status and status != expected_status:
        expectation_failures.append(f"status expected {expected_status} got {status}")
        status = "expectation_failed"
        passed = False
    return {"passed": passed, "status": status, "failures": failures, "missing_expected_failures": missing, "unexpected_failures": unexpected, "expectation_failures": expectation_failures, "architecture_classes": actual}


def run_scenario(scenario: dict[str, Any], planner_format: str = "auto") -> dict[str, Any]:
    adapter = planner_adapter(scenario.get("planner_output"), scenario.get("state") or {}, scenario.get("planner_format", planner_format))
    plan = validate_planner_output(adapter.get("canonical_payload"))
    merged = context_merge(scenario.get("state") or {}, plan)
    guard = transition_guard(plan, merged)
    normalized = search_normalizer(scenario.get("search_fixture") or {"options": []})
    validated = constraint_validator(normalized, merged.get("constraints") or {}, scenario.get("constraint_schema") or {})
    dctx = decision_context(validated, scenario.get("constraint_schema") or {})
    gate = execution_gate(guard, merged)
    nodes = {"planner_adapter": safe_node_output("planner_adapter", adapter), "planner_output": safe_node_output("planner_output", plan), "context_merge": safe_node_output("context_merge", merged), "transition_guard": safe_node_output("transition_guard", guard), "search_normalizer": safe_node_output("search_normalizer", normalized), "constraint_validator": safe_node_output("constraint_validator", validated), "decision_context": safe_node_output("decision_context", dctx), "execution_gate": safe_node_output("execution_gate", gate)}
    nodes["result"] = safe_node_output("result", _result(nodes, scenario))
    return {"scenario_id": scenario.get("id", "ad_hoc"), "title": scenario.get("title", "ad hoc input"), "shadow_label": "current runtime unchanged; proposed guard result only", "nodes": nodes}


def load_input(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("scenarios"), list):
        return data["scenarios"]
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    raise SystemExit("input JSON must be a scenario object, scenario list, or {scenarios: [...]}")


def _overlay_json(scenario: dict[str, Any], planner_json: str | None, search_json: str | None) -> dict[str, Any]:
    scenario = deepcopy(scenario)
    if planner_json:
        scenario["planner_output"] = json.loads(Path(planner_json).read_text(encoding="utf-8"))
    if search_json:
        data = json.loads(Path(search_json).read_text(encoding="utf-8"))
        scenario["search_fixture"] = data if isinstance(data, dict) and "options" in data else {"options": data if isinstance(data, list) else []}
    return scenario


def render_text(results: list[dict[str, Any]]) -> str:
    lines = ["NMBOT node emulator — local shadow report", ""]
    for result in results:
        lines.append(f"## {result['scenario_id']} — {result['title']}")
        lines.append(result["shadow_label"])
        for node_name, payload in result["nodes"].items():
            lines.append(f"### {node_name}")
            lines.append(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        lines.append("")
    return _redact_text("\n".join(lines))


def render_report(results: list[dict[str, Any]]) -> str:
    total = len(results)
    passed = sum(1 for item in results if item["nodes"]["result"]["passed"])
    lines = ["# NMBOT Node Emulator Results — 2026-07-16", "", "This report was generated locally by `scripts/nmbot_node_emulator.py`. No model, network, Jivo, Google, CRM, VPS, git, or eval call was made.", "", "Source references: `followup_intent_classifier.py:141-155`, `followup_intent_classifier.py:158-162`, `followup_intent_classifier.py:186-220`, `followup_intent_classifier.py:480-543`, plus local shadow emulator `scripts/nmbot_node_emulator.py`.", "", f"Summary: {passed}/{total} scenarios passed expected node outcomes and invariant checks.", "", "## Findings"]
    by_status: dict[str, list[str]] = {"emulator_correctly_detected_expected_defect": [], "architecture_guard_supported": [], "not_tested": [], "expectation_failed": []}
    for result in results:
        rid = result["scenario_id"]
        res = result["nodes"]["result"]
        status = res.get("status", "not_tested")
        classes = res.get("architecture_classes") or []
        by_status.setdefault(status, []).append(f"- `{rid}`: {result['title']}; classes={classes or ['none']}; expectation_failures={res.get('expectation_failures') or []}")
    for status in ("emulator_correctly_detected_expected_defect", "architecture_guard_supported", "not_tested", "expectation_failed"):
        lines.extend(["", f"### {status}"])
        lines.extend(by_status.get(status) or ["- none"])
    lines.extend(["", "## Substantive conclusions", "- The typed node architecture remains promising: canonical planner validation, constraint merge, deterministic fact validation, and presenter-safe assembly can be checked locally.", "- The production-shaped planner adapter is diagnostic only. It does not fix runtime and does not invent normalized fields from prose.", "- Production planner contract gaps are now explicit: guards cannot be fully enforced until planner output includes normalized `clarification_fields`, primary `intent`/`intent_policy`, canonical `target`/`search_policy`, and constraint category/hardness.", "- Current `params_delta` is safely treated as unknown constraints, not hard/preference. Aggregate ЖК price range remains insufficient evidence for room-specific budget match without confirmed `matching_unit_price_m`.", "- Hard constraints still prevent rejected options from leaking into presenter options; unknown evidence remains in `do_not_say`.", "", "## Limitations", "- This is a shadow evaluator. It does not change current runtime behavior.", "- Model semantic quality is testable only by feeding actual saved model JSON through `--planner-json`/`--input`. No model call was made here.", "- The emulator validates typed node contracts and code assembly, not natural-language response quality.", "", "## Scenario details"])
    for result in results:
        lines.append(f"### {result['scenario_id']} — {result['title']}")
        notes = result["nodes"].get("constraint_validator", {}).get("notes") or []
        if notes:
            lines.append("Notes: " + "; ".join(notes))
        lines.append("```json")
        lines.append(json.dumps(result["nodes"]["result"], ensure_ascii=False, indent=2, sort_keys=True))
        lines.append("```")
    return _redact_text("\n".join(lines) + "\n")


def _fixture_path() -> Path:
    return Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "nmbot_node_emulator_scenarios.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local read-only nmbot node-level emulator")
    parser.add_argument("--input", default=str(_fixture_path()), help="scenario JSON file; default is safe built-in fixture")
    parser.add_argument("--scenario", help="run only this scenario id; required with --planner-json/--search-json unless input has one scenario")
    parser.add_argument("--planner-json", help="overlay captured typed planner object onto selected fixture; no model call")
    parser.add_argument("--planner-format", choices=("canonical", "current", "auto"), default="auto", help="planner JSON format; auto detects dialog_action/current or action/canonical")
    parser.add_argument("--search-json", help="overlay captured typed search facts onto selected fixture; no network call")
    parser.add_argument("--json", action="store_true", help="print JSON output")
    parser.add_argument("--report", help="write markdown report to file")
    parser.add_argument("--strict", action="store_true", help="exit non-zero on missing/unexpected failures or expectation mismatches")
    parser.add_argument("--self-test", action="store_true", help="run built-in fixture suite from tests/fixtures")
    args = parser.parse_args(argv)
    input_path = _fixture_path() if args.self_test else Path(args.input)
    scenarios = load_input(input_path)
    if args.scenario:
        scenarios = [scenario for scenario in scenarios if scenario.get("id") == args.scenario]
        if not scenarios:
            raise SystemExit(f"scenario not found: {args.scenario}")
    if (args.planner_json or args.search_json) and len(scenarios) != 1:
        raise SystemExit("--planner-json/--search-json overlays require --scenario or a single-scenario --input; default safe base is the selected fixture scenario")
    scenarios = [_overlay_json(scenario, args.planner_json, args.search_json) for scenario in scenarios]
    results = [run_scenario(scenario, planner_format=args.planner_format) for scenario in scenarios]
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report(results), encoding="utf-8")
    output = json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) if args.json else render_text(results)
    print(_redact_text(output))
    has_failure = any(not result["nodes"]["result"].get("passed") for result in results)
    return 1 if args.strict and has_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
