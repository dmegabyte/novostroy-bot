#!/usr/bin/env python3
"""Passive privacy-safe project memory outcome store.

Stdlib-only local CLI. It validates and appends privacy-safe outcome metadata;
it never calls runtime, gates, notebooks, MemPalace, network or subprocesses.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from project_memory_registry import load_registry, resolve_project


DEFAULT_STORE = Path("data/project_memory_outcomes.jsonl")
DEFAULT_POLICY = Path("config/project_memory_policy_bundles.json")
DEFAULT_TAXONOMY = Path("config/project_memory_diagnosis_taxonomy.json")

OUTCOME_SCHEMA = "privacy_safe_outcome.v1"
FEATURES_SCHEMA = "safe_case_features.v1"
SHADOW_OUTCOME_SCHEMA = "privacy_safe_shadow_outcome.v1"
SHADOW_FEATURES_SCHEMA = "safe_shadow_features.v1"
SNAPSHOT_SCHEMA = "bank_snapshot.v1"
VALIDATION_SCHEMA = "privacy_safe_outcome_validation.v1"
HINTS_SCHEMA = "project_memory_hints.v1"
LIST_SCHEMA = "project_memory_outcome_list.v1"
SUMMARY_SCHEMA = "project_memory_outcome_summary.v1"

TOP_KEYS = {
    "schema",
    "outcome_id",
    "case_fingerprint",
    "project_id",
    "policy_version",
    "policy_delta",
    "features",
    "diagnosis_d1_d6",
    "result",
    "gate_status",
    "failure_source",
    "artifact_ref_id",
    "created_at",
}
FEATURE_KEYS = {
    "schema",
    "case_fingerprint",
    "project_id",
    "policy_version",
    "route",
    "evidence_type",
    "target_kind",
    "candidate_count",
    "selected_source_count",
    "lines_loaded",
    "chars_loaded",
    "verifier_result",
    "no_raw_query_code_log_secret",
}
SHADOW_TOP_KEYS = {
    "schema",
    "outcome_id",
    "task_fingerprint",
    "project_id",
    "policy_version",
    "policy_delta",
    "features",
    "diagnosis_d1_d6",
    "result",
    "gate_status",
    "failure_source",
    "artifact_ref_id",
    "created_at",
}
SHADOW_FEATURE_KEYS = {
    "schema",
    "task_fingerprint",
    "project_id",
    "policy_version",
    "phase",
    "candidate_ids",
    "selected_target_id",
    "confirmed_or_corrected_target_id",
    "gate_result",
    "route",
    "evidence_type",
    "stop_reason",
    "lines_loaded",
    "chars_loaded",
    "latency_ms",
    "verifier_result",
    "no_raw_query_code_log_secret",
}
DIAG_KEYS = {"D1", "D2", "D3", "D4", "D5", "D6"}
RESULTS = {"passed", "failed", "stopped", "invalid"}
GATE_STATUS = {"pass", "fail_closed", "denied", "not_run"}
SHADOW_VERIFIER_RESULTS = {"confirmed_correct", "corrected_target", "abstained_no_support", "invalid_target", "not_verified"}
STOP_REASONS = {
    "definition_of_done",
    "two_primary_sources_agree",
    "owner_contract_and_test",
    "no_candidate_answers",
    "expansion_exhausted",
    "context_budget_reached",
    "source_conflict_requires_decision",
    "topic_changed_follow_up",
    "deep_audit_required",
}
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
FORBIDDEN_KEY_PARTS = {
    "query",
    "question",
    "path",
    "text",
    "body",
    "code",
    "log",
    "payload",
    "transcript",
    "label",
    "secret",
    "password",
    "token",
    "credential",
    "apikey",
    "api_key",
    "env",
}
SAFE_EXCEPTION_KEYS = {"no_raw_query_code_log_secret"}
SECRETISH_VALUE_RE = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|credential|bearer\s+[a-z0-9]|sk-[a-z0-9]|BEGIN\s+(RSA\s+)?PRIVATE\s+KEY)",
    re.IGNORECASE,
)


def contained_path(raw: str | Path) -> Path:
    value = str(raw)
    if not value or value.startswith("/"):
        raise ValueError("path must be repo-relative and contained")
    candidate = (ROOT / value).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("path must be repo-relative and contained") from exc
    return candidate


def load_json(path: str | Path) -> dict[str, Any]:
    with contained_path(path).open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("JSON must be an object")
    return data


def load_policy(path: str | Path = DEFAULT_POLICY) -> dict[str, Any]:
    policy = load_json(path)
    if policy.get("schema") != "policy_bundle.v1" or not isinstance(policy.get("policies"), list):
        raise ValueError("policy bundle must use schema policy_bundle.v1")
    return policy


def load_taxonomy(path: str | Path = DEFAULT_TAXONOMY) -> dict[str, Any]:
    taxonomy = load_json(path)
    if taxonomy.get("schema") != "d1_d6_taxonomy.v1":
        raise ValueError("taxonomy must use schema d1_d6_taxonomy.v1")
    return taxonomy


def error(code: str, message: str, line: int | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if line is not None:
        item["line"] = line
    return item


def _forbidden_scan(value: Any, errors: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                errors.append(error("field_name_type", "field names must be strings"))
                continue
            lowered = key.lower()
            compact = lowered.replace("-", "_")
            if key not in SAFE_EXCEPTION_KEYS and any(part in lowered or part in compact for part in FORBIDDEN_KEY_PARTS):
                errors.append(error("forbidden_field", "forbidden raw/sensitive field name present"))
            _forbidden_scan(child, errors)
    elif isinstance(value, list):
        for child in value:
            _forbidden_scan(child, errors)
    elif isinstance(value, str) and SECRETISH_VALUE_RE.search(value):
        errors.append(error("secretish_value", "sensitive-looking value present"))


def _policy_for(policy: dict[str, Any], project_id: str, version: str) -> dict[str, Any] | None:
    for row in policy.get("policies", []):
        if isinstance(row, dict) and row.get("project_id") == project_id and row.get("policy_version") == version:
            return row
    return None


def _privacy_budget(policy_row: dict[str, Any]) -> dict[str, Any]:
    budget = policy_row.get("privacy_budget")
    return budget if isinstance(budget, dict) else {}


def _validate_features(features: Any, outcome: dict[str, Any], policy_row: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    if not isinstance(features, dict):
        errors.append(error("features_type", "features must be an object"))
        return
    if set(features) != FEATURE_KEYS:
        errors.append(error("features_keys", "features fields are not exact"))
        return
    if features.get("schema") != FEATURES_SCHEMA:
        errors.append(error("features_schema", f"features.schema must be {FEATURES_SCHEMA}"))
    for key in ("case_fingerprint", "project_id", "policy_version"):
        if features.get(key) != outcome.get(key):
            errors.append(error("features_consistency", f"features.{key} must match outcome"))
    for key in ("candidate_count", "selected_source_count", "lines_loaded", "chars_loaded"):
        if not isinstance(features.get(key), int) or features[key] < 0:
            errors.append(error("non_negative_count", f"features.{key} must be a non-negative integer"))

    budget = _privacy_budget(policy_row)
    max_candidates = budget.get("max_candidate_ids", 8)
    max_selected_sources = budget.get("max_selected_source_count", 2)
    max_lines = budget.get("max_lines_loaded", 80)
    max_chars = budget.get("max_chars_loaded", 8000)
    if isinstance(features.get("candidate_count"), int) and features["candidate_count"] > max_candidates:
        errors.append(error("candidate_count_budget", "features.candidate_count exceeds policy budget"))
    if isinstance(features.get("selected_source_count"), int) and features["selected_source_count"] > max_selected_sources:
        errors.append(error("selected_source_budget", "features.selected_source_count exceeds policy budget"))
    if isinstance(features.get("lines_loaded"), int) and features["lines_loaded"] > max_lines:
        errors.append(error("lines_budget", "features.lines_loaded exceeds policy budget"))
    if isinstance(features.get("chars_loaded"), int) and features["chars_loaded"] > max_chars:
        errors.append(error("chars_budget", "features.chars_loaded exceeds policy budget"))

    if features.get("no_raw_query_code_log_secret") is not True:
        errors.append(error("privacy_flag", "features.no_raw_query_code_log_secret must be true"))
    if features.get("route") not in policy_row.get("allowed_routes", []):
        errors.append(error("route_not_allowed", "route is not allowed by policy"))
    if features.get("evidence_type") not in policy_row.get("allowed_evidence_types", []):
        errors.append(error("evidence_type_not_allowed", "evidence_type is not allowed by policy"))
    if features.get("target_kind") not in policy_row.get("allowed_target_kinds", []):
        errors.append(error("target_kind_not_allowed", "target_kind is not allowed by policy"))
    if not isinstance(features.get("verifier_result"), str) or not features["verifier_result"]:
        errors.append(error("verifier_result", "features.verifier_result must be a non-empty string"))


def _validate_shadow_features(features: Any, outcome: dict[str, Any], policy_row: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    if not isinstance(features, dict):
        errors.append(error("features_type", "features must be an object"))
        return
    if set(features) != SHADOW_FEATURE_KEYS:
        errors.append(error("features_keys", "features fields are not exact"))
        return
    if features.get("schema") != SHADOW_FEATURES_SCHEMA:
        errors.append(error("features_schema", f"features.schema must be {SHADOW_FEATURES_SCHEMA}"))
    for key in ("task_fingerprint", "project_id", "policy_version"):
        if features.get(key) != outcome.get(key):
            errors.append(error("features_consistency", f"features.{key} must match outcome"))
    for key in ("lines_loaded", "chars_loaded", "latency_ms"):
        if not isinstance(features.get(key), int) or features[key] < 0:
            errors.append(error("non_negative_count", f"features.{key} must be a non-negative integer"))

    budget = _privacy_budget(policy_row)
    max_lines = budget.get("max_lines_loaded", 80)
    max_chars = budget.get("max_chars_loaded", 8000)
    if isinstance(features.get("lines_loaded"), int) and features["lines_loaded"] > max_lines:
        errors.append(error("lines_budget", "features.lines_loaded exceeds policy budget"))
    if isinstance(features.get("chars_loaded"), int) and features["chars_loaded"] > max_chars:
        errors.append(error("chars_budget", "features.chars_loaded exceeds policy budget"))

    if features.get("no_raw_query_code_log_secret") is not True:
        errors.append(error("privacy_flag", "features.no_raw_query_code_log_secret must be true"))
    if features.get("phase") not in policy_row.get("allowed_phases", []):
        errors.append(error("phase_not_allowed", "phase is not allowed by policy"))
    if features.get("route") not in policy_row.get("allowed_routes", []):
        errors.append(error("route_not_allowed", "route is not allowed by policy"))
    if features.get("evidence_type") not in policy_row.get("allowed_evidence_types", []):
        errors.append(error("evidence_type_not_allowed", "evidence_type is not allowed by policy"))
    if features.get("gate_result") not in GATE_STATUS:
        errors.append(error("gate_result", "features.gate_result is not allowed"))
    if features.get("gate_result") != outcome.get("gate_status"):
        errors.append(error("gate_status_inconsistent", "features.gate_result must match outcome.gate_status"))
    if features.get("verifier_result") not in SHADOW_VERIFIER_RESULTS:
        errors.append(error("verifier_result", "features.verifier_result is not allowed"))
    if features.get("stop_reason") not in STOP_REASONS:
        errors.append(error("stop_reason", "features.stop_reason is not allowed"))

    candidate_ids = features.get("candidate_ids")
    if not isinstance(candidate_ids, list):
        errors.append(error("candidate_ids", "features.candidate_ids must be a list"))
    else:
        if len(candidate_ids) > 8:
            errors.append(error("candidate_ids_max", "features.candidate_ids must contain at most 8 IDs"))
        if len(candidate_ids) != len(set(candidate_ids)):
            errors.append(error("candidate_ids_unique", "features.candidate_ids must be unique"))
        if not all(isinstance(item, str) and HEX64_RE.match(item) for item in candidate_ids):
            errors.append(error("candidate_ids_hash", "features.candidate_ids must be lowercase sha256 hex"))
    for key in ("selected_target_id", "confirmed_or_corrected_target_id"):
        value = features.get(key)
        if value is not None and (not isinstance(value, str) or not HEX64_RE.match(value)):
            errors.append(error("target_id_hash", f"features.{key} must be null or lowercase sha256 hex"))
    if features.get("verifier_result") in {"confirmed_correct", "corrected_target"} and features.get("confirmed_or_corrected_target_id") is None:
        errors.append(error("confirmed_target_required", "confirmed/corrected verifier results require confirmed_or_corrected_target_id"))

    selected = features.get("selected_target_id")
    corrected = features.get("confirmed_or_corrected_target_id")
    verifier = features.get("verifier_result")
    result = outcome.get("result")
    gate_status = outcome.get("gate_status")
    if isinstance(candidate_ids, list):
        if selected is not None and selected not in candidate_ids:
            errors.append(error("selected_target_not_candidate", "selected_target_id must be one of candidate_ids"))
        if not candidate_ids and selected is not None:
            errors.append(error("empty_candidates_selected", "selected_target_id must be null when candidate_ids is empty"))
    if verifier == "confirmed_correct":
        if selected is None or corrected is None or selected != corrected or result != "passed":
            errors.append(error("confirmed_correct_invariant", "confirmed_correct requires selected==corrected and result=passed"))
    elif verifier == "corrected_target":
        if corrected is None or selected == corrected or result not in {"failed", "stopped"}:
            errors.append(error("corrected_target_invariant", "corrected_target requires corrected target, changed/absent selection and failed/stopped result"))
    elif verifier == "abstained_no_support":
        if selected is not None or corrected is not None or result != "stopped" or gate_status != "not_run":
            errors.append(error("abstained_no_support_invariant", "abstained_no_support requires no targets, result=stopped and gate_status=not_run"))
    elif verifier == "invalid_target":
        if corrected is not None or result not in {"invalid", "stopped"} or gate_status not in {"fail_closed", "denied"}:
            errors.append(error("invalid_target_invariant", "invalid_target requires corrected=null, invalid/stopped result and fail_closed/denied gate"))
    elif verifier == "not_verified":
        if result != "stopped" or corrected is not None:
            errors.append(error("not_verified_invariant", "not_verified requires result=stopped and corrected=null"))


def _validate_diagnosis(value: Any, taxonomy: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    allowed_values = set(taxonomy.get("allowed_dimension_values", []))
    if not isinstance(value, dict) or set(value) != DIAG_KEYS:
        errors.append(error("diagnosis_keys", "diagnosis_d1_d6 must contain exactly D1..D6"))
        return
    for key, item in value.items():
        if item not in allowed_values:
            errors.append(error("diagnosis_value", f"{key} is not in taxonomy allowed values"))


def _validate_v1_outcome(outcome: dict[str, Any], policy: dict[str, Any] | None = None, taxonomy: dict[str, Any] | None = None) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if not isinstance(outcome, dict):
        return {"schema": VALIDATION_SCHEMA, "valid": False, "errors": [error("outcome_type", "outcome must be an object")]}
    _forbidden_scan(outcome, errors)
    if set(outcome) != TOP_KEYS:
        errors.append(error("top_level_keys", "outcome fields are not exact"))
    if outcome.get("schema") != OUTCOME_SCHEMA:
        errors.append(error("schema", f"schema must be {OUTCOME_SCHEMA}"))
    for key in ("outcome_id", "project_id", "policy_version", "policy_delta", "artifact_ref_id", "created_at"):
        if not isinstance(outcome.get(key), str) or not outcome[key] or not ID_RE.match(outcome[key]):
            errors.append(error("id_field", f"{key} must be a bounded safe identifier"))
    if not isinstance(outcome.get("case_fingerprint"), str) or not HEX64_RE.match(outcome["case_fingerprint"]):
        errors.append(error("case_fingerprint", "case_fingerprint must be lowercase sha256 hex"))

    registry_resolution = resolve_project(load_registry(), str(outcome.get("project_id", "")))
    if not registry_resolution.get("ok"):
        errors.append(error("project_not_routable", registry_resolution.get("denied_reason", "project_not_routable")))

    policy = policy or load_policy()
    taxonomy = taxonomy or load_taxonomy()
    policy_row = _policy_for(policy, str(outcome.get("project_id", "")), str(outcome.get("policy_version", "")))
    if policy_row is None:
        errors.append(error("policy_version_denied", "policy_version is not allowed for project"))
        policy_row = {"allowed_routes": [], "allowed_evidence_types": [], "allowed_target_kinds": []}
    elif outcome.get("policy_delta") != policy_row.get("policy_delta"):
        errors.append(error("policy_delta_mismatch", "policy_delta must match policy bundle"))

    _validate_features(outcome.get("features"), outcome, policy_row, errors)
    _validate_diagnosis(outcome.get("diagnosis_d1_d6"), taxonomy, errors)
    if outcome.get("failure_source") not in taxonomy.get("allowed_failure_source", []):
        errors.append(error("failure_source", "failure_source is not allowed by taxonomy"))
    if outcome.get("result") not in RESULTS:
        errors.append(error("result", "result is not allowed"))
    if outcome.get("gate_status") not in GATE_STATUS:
        errors.append(error("gate_status", "gate_status is not allowed"))
    return {"schema": VALIDATION_SCHEMA, "valid": not errors, "errors": errors}


def _validate_shadow_outcome(outcome: dict[str, Any], policy: dict[str, Any] | None = None, taxonomy: dict[str, Any] | None = None) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if not isinstance(outcome, dict):
        return {"schema": VALIDATION_SCHEMA, "valid": False, "errors": [error("outcome_type", "outcome must be an object")]}
    _forbidden_scan(outcome, errors)
    if set(outcome) != SHADOW_TOP_KEYS:
        errors.append(error("top_level_keys", "outcome fields are not exact"))
    if outcome.get("schema") != SHADOW_OUTCOME_SCHEMA:
        errors.append(error("schema", f"schema must be {SHADOW_OUTCOME_SCHEMA}"))
    for key in ("outcome_id", "project_id", "policy_version", "policy_delta", "artifact_ref_id", "created_at"):
        if not isinstance(outcome.get(key), str) or not outcome[key] or not ID_RE.match(outcome[key]):
            errors.append(error("id_field", f"{key} must be a bounded safe identifier"))
    if not isinstance(outcome.get("task_fingerprint"), str) or not HEX64_RE.match(outcome["task_fingerprint"]):
        errors.append(error("task_fingerprint", "task_fingerprint must be lowercase sha256 hex"))

    registry_resolution = resolve_project(load_registry(), str(outcome.get("project_id", "")))
    if not registry_resolution.get("ok"):
        errors.append(error("project_not_routable", registry_resolution.get("denied_reason", "project_not_routable")))

    policy = policy or load_policy()
    taxonomy = taxonomy or load_taxonomy()
    policy_row = _policy_for(policy, str(outcome.get("project_id", "")), str(outcome.get("policy_version", "")))
    if policy_row is None:
        errors.append(error("policy_version_denied", "policy_version is not allowed for project"))
        policy_row = {"allowed_routes": [], "allowed_evidence_types": [], "allowed_phases": [], "privacy_budget": {}}
    elif outcome.get("policy_delta") != policy_row.get("policy_delta"):
        errors.append(error("policy_delta_mismatch", "policy_delta must match policy bundle"))

    _validate_shadow_features(outcome.get("features"), outcome, policy_row, errors)
    _validate_diagnosis(outcome.get("diagnosis_d1_d6"), taxonomy, errors)
    if outcome.get("failure_source") not in taxonomy.get("allowed_failure_source", []):
        errors.append(error("failure_source", "failure_source is not allowed by taxonomy"))
    if outcome.get("result") not in RESULTS:
        errors.append(error("result", "result is not allowed"))
    if outcome.get("gate_status") not in GATE_STATUS:
        errors.append(error("gate_status", "gate_status is not allowed"))
    return {"schema": VALIDATION_SCHEMA, "valid": not errors, "errors": errors}


def validate_outcome(outcome: dict[str, Any], policy: dict[str, Any] | None = None, taxonomy: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(outcome, dict):
        return {"schema": VALIDATION_SCHEMA, "valid": False, "errors": [error("outcome_type", "outcome must be an object")]}
    if outcome.get("schema") == SHADOW_OUTCOME_SCHEMA:
        return _validate_shadow_outcome(outcome, policy, taxonomy)
    return _validate_v1_outcome(outcome, policy, taxonomy)


def _outcome_fingerprint(obj: dict[str, Any]) -> str | None:
    value = obj.get("task_fingerprint", obj.get("case_fingerprint"))
    return value if isinstance(value, str) else None


def _existing_outcome_ids(store: str | Path = DEFAULT_STORE) -> set[str]:
    path = contained_path(store)
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            obj = json.loads(line)
            outcome_id = obj.get("outcome_id") if isinstance(obj, dict) else None
            if isinstance(outcome_id, str):
                ids.add(outcome_id)
    return ids


def _validate_store_stream(fh: Any) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    count = 0
    policy = load_policy()
    taxonomy = load_taxonomy()
    seen_outcome_ids: set[str] = set()
    fh.seek(0)
    for line_no, line in enumerate(fh, start=1):
        if line in {"\n", "\r\n", ""} or not line.strip():
            errors.append(error("blank_line", "blank lines are forbidden", line_no))
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            errors.append(error("jsonl_malformed", "malformed JSONL line", line_no))
            continue
        result = validate_outcome(obj, policy, taxonomy)
        if not result["valid"]:
            errors.append(error("record_invalid", "invalid outcome record", line_no))
        outcome_id = obj.get("outcome_id") if isinstance(obj, dict) else None
        if isinstance(outcome_id, str):
            if outcome_id in seen_outcome_ids:
                errors.append(error("duplicate_outcome_id", "duplicate outcome_id", line_no))
            seen_outcome_ids.add(outcome_id)
        count += 1
    return {"schema": VALIDATION_SCHEMA, "valid": not errors, "record_count": count, "errors": errors, "outcome_ids": seen_outcome_ids}


def validate_store(store: str | Path = DEFAULT_STORE) -> dict[str, Any]:
    path = contained_path(store)
    if not path.exists():
        return {"schema": VALIDATION_SCHEMA, "valid": True, "record_count": 0, "errors": []}
    with path.open("r", encoding="utf-8") as fh:
        result = _validate_store_stream(fh)
    return {key: value for key, value in result.items() if key != "outcome_ids"}


def append_outcome(outcome_path: str | Path, store: str | Path = DEFAULT_STORE) -> dict[str, Any]:
    outcome = load_json(outcome_path)
    result = validate_outcome(outcome)
    if not result["valid"]:
        return {"schema": VALIDATION_SCHEMA, "appended": False, "valid": False, "errors": result["errors"]}
    line = json.dumps(outcome, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    path = contained_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
    with os.fdopen(fd, "a+", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            return {"schema": VALIDATION_SCHEMA, "appended": False, "valid": False, "errors": [error("lock_failed", "exclusive append lock could not be acquired")]}
        try:
            store_result = _validate_store_stream(fh)
            if not store_result["valid"]:
                return {"schema": VALIDATION_SCHEMA, "appended": False, "valid": False, "errors": store_result["errors"]}
            if outcome["outcome_id"] in store_result["outcome_ids"]:
                return {"schema": VALIDATION_SCHEMA, "appended": False, "valid": False, "errors": [error("duplicate_outcome_id", "duplicate outcome_id")]}
            fh.seek(0, os.SEEK_END)
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
            record_count_before = store_result["record_count"]
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return {
        "schema": VALIDATION_SCHEMA,
        "appended": True,
        "valid": True,
        "outcome_id": outcome["outcome_id"],
        "project_id": outcome["project_id"],
        "record_count_before": record_count_before,
    }


def list_outcomes(project_id: str, store: str | Path = DEFAULT_STORE, limit: int = 5) -> dict[str, Any]:
    store_result = validate_store(store)
    if not store_result["valid"]:
        return {"schema": LIST_SCHEMA, "ok": False, "errors": store_result["errors"], "records": []}
    records: list[dict[str, Any]] = []
    path = contained_path(store)
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                obj = json.loads(line)
                if obj.get("project_id") != project_id:
                    continue
                features = obj.get("features", {})
                records.append(
                    {
                        "outcome_id": obj["outcome_id"],
                        "fingerprint": _outcome_fingerprint(obj),
                        "project_id": obj["project_id"],
                        "policy_version": obj["policy_version"],
                        "schema": obj["schema"],
                        "phase": features.get("phase"),
                        "route": features.get("route"),
                        "evidence_type": features.get("evidence_type"),
                        "result": obj["result"],
                        "gate_status": obj["gate_status"],
                        "failure_source": obj["failure_source"],
                    }
                )
    return {"schema": LIST_SCHEMA, "ok": True, "project_id": project_id, "count": len(records), "records": records[:limit]}


def summary(store: str | Path = DEFAULT_STORE) -> dict[str, Any]:
    store_result = validate_store(store)
    if not store_result["valid"]:
        return {"schema": SUMMARY_SCHEMA, "ok": False, "errors": store_result["errors"]}
    aggregates = {
        "phase": {},
        "result": {},
        "verifier_result": {},
        "gate_status": {},
    }
    max_lines = 0
    max_chars = 0
    correction_count = 0
    path = contained_path(store)
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                obj = json.loads(line)
                features = obj.get("features", {})
                phase = features.get("phase") or "legacy_v1"
                verifier = features.get("verifier_result") or "unknown"
                for key, value in (
                    ("phase", phase),
                    ("result", obj.get("result")),
                    ("verifier_result", verifier),
                    ("gate_status", obj.get("gate_status")),
                ):
                    bucket = aggregates[key]
                    bucket[value] = bucket.get(value, 0) + 1
                max_lines = max(max_lines, int(features.get("lines_loaded", 0) or 0))
                max_chars = max(max_chars, int(features.get("chars_loaded", 0) or 0))
                if features.get("verifier_result") == "corrected_target":
                    correction_count += 1
    return {
        "schema": SUMMARY_SCHEMA,
        "ok": True,
        "record_count": store_result["record_count"],
        "counts": aggregates,
        "max_lines_loaded": max_lines,
        "max_chars_loaded": max_chars,
        "correction_count": correction_count,
    }


def hints(project_id: str, policy_version: str, route: str, evidence_type: str, store: str | Path = DEFAULT_STORE) -> dict[str, Any]:
    listed = list_outcomes(project_id, store, limit=10_000)
    count = 0
    if listed.get("ok"):
        for record in listed["records"]:
            if record["policy_version"] == policy_version and record["route"] == route and record["evidence_type"] == evidence_type:
                count += 1
    return {
        "schema": HINTS_SCHEMA,
        "ok": True,
        "project_id": project_id,
        "policy_version": policy_version,
        "route": route,
        "evidence_type": evidence_type,
        "denied_reason": "hints_disabled_by_policy",
        "matching_safe_outcome_count": count,
        "hints": [],
    }


def validate_bank_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    keys = {"schema", "bank_snapshot_id", "project_id", "policy_version", "included_outcome_ids", "excluded_failed_invalid_ids", "frozen_at", "scorer_owner_tbd"}
    errors: list[dict[str, Any]] = []
    _forbidden_scan(snapshot, errors)
    if set(snapshot) != keys:
        errors.append(error("snapshot_keys", "snapshot fields are not exact"))
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        errors.append(error("snapshot_schema", f"schema must be {SNAPSHOT_SCHEMA}"))
    if snapshot.get("scorer_owner_tbd") != "TBD":
        errors.append(error("snapshot_owner", "scorer_owner_tbd must be literal TBD"))
    for key in ("included_outcome_ids", "excluded_failed_invalid_ids"):
        if not isinstance(snapshot.get(key), list) or not all(isinstance(item, str) and ID_RE.match(item) for item in snapshot.get(key, [])):
            errors.append(error("snapshot_id_list", f"{key} must be a list of safe IDs"))
    return {"schema": VALIDATION_SCHEMA, "valid": not errors, "errors": errors}


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append/list passive privacy-safe project memory outcomes.")
    parser.add_argument("--store", default=str(DEFAULT_STORE), help="repo-relative JSONL store")
    parser.add_argument("--outcome", help="repo-relative outcome JSON path")
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--hints", action="store_true")
    parser.add_argument("--project-id")
    parser.add_argument("--policy-version")
    parser.add_argument("--route")
    parser.add_argument("--evidence-type")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.append:
            if not args.outcome:
                raise ValueError("--append requires --outcome")
            payload = append_outcome(args.outcome, args.store)
            emit(payload, args.json)
            return 0 if payload.get("appended") else 2
        if args.validate:
            payload = validate_store(args.store)
            emit(payload, args.json)
            return 0 if payload["valid"] else 2
        if args.list:
            if not args.project_id:
                raise ValueError("--list requires --project-id")
            payload = list_outcomes(args.project_id, args.store)
            emit(payload, args.json)
            return 0 if payload.get("ok") else 2
        if args.summary:
            payload = summary(args.store)
            emit(payload, args.json)
            return 0 if payload.get("ok") else 2
        if args.hints:
            for name in ("project_id", "policy_version", "route", "evidence_type"):
                if not getattr(args, name):
                    raise ValueError("--hints requires --project-id, --policy-version, --route and --evidence-type")
            payload = hints(args.project_id, args.policy_version, args.route, args.evidence_type, args.store)
            emit(payload, args.json)
            return 0
        raise ValueError("choose --append, --validate, --list, --summary or --hints")
    except Exception as exc:
        payload = {"schema": VALIDATION_SCHEMA, "valid": False, "errors": [error("failed_closed", str(exc))]}
        emit(payload, args.json)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
