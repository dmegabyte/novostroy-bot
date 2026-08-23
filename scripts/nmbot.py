#!/usr/bin/env python3
"""Thin nmbot command wrapper.

This wrapper only delegates to existing commands with direct argv. The `diag`
subcommand invokes `bash scripts/nmbot_diag.sh` only when the user explicitly
asks for `diag`; depending on user-supplied arguments, that script may perform
VPS/network diagnostics.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_CHECK_SCOPES = {"docs", "contracts", "v0", "v2", "runtime", "audit", "quality"}
SUPPORTED_CHECK_FLAGS = {"--dry-run", "--json"}
DIAGNOSE_SCHEMA_VERSION = "nmbot.diagnose.v1"
DIAGNOSE_EDIT_PLAN_SCHEMA_VERSION = "nmbot.diagnose.edit_plan.v1"
MUTATION_GATE_SCHEMA_VERSION = "nmbot.mutation_gate.v1"
DIAGNOSE_RECENT_SCHEMA_VERSION = "nmbot.diagnose.recent.v1"
DIAGNOSTIC_ENVELOPE_SCHEMA_VERSION = "nmbot.diagnostic.v1"
DIAGNOSE_TIMELINE_SCHEMA_VERSION = "nmbot.diagnose.timeline.v1"
DIAGNOSE_SUMMARY_SCHEMA_VERSION = "nmbot.diagnose.summary.v1"
DIAGNOSTIC_TOOLS_REGISTRY_SCHEMA_VERSION = "nmbot.diagnostic_tools.v1"
TRACE_RUNTIME_STAGES = {"api_safe_fallback", "operator_handoff", "phone_captured"}
TASK_LAYERS = {"provider", "mcp", "gateway", "unknown_downstream", "none"}
OWNER_CARD_FIELDS = ("owner_source", "owner_symbol", "contract_doc", "focused_test", "next_check")
RECENT_NEXT_COMMAND = "bash scripts/nmbot_diag.sh --logs"
RUNTIME_VERSIONS = {"V0", "V2", "V3"}
EVIDENCE_SCOPES = {"local", "historical", "live", "mixed", "unknown"}
DIAGNOSTIC_TOOL_STATUSES = {"current", "specialized", "legacy"}
NAMESPACE_ALIASES = {
    ("trace", "analyze"): [sys.executable, "scripts/nmbot_jivo_trace_analyze.py"],
    ("trace", "dialogue"): [sys.executable, "scripts/nmbot_jivo_dialogue_diagnose.py"],
    ("dialogue", "report"): [sys.executable, "scripts/nmbot_dialogue_report.py"],
    ("planner", "find"): [sys.executable, "scripts/find_planner_trace.py"],
    ("runtime", "compare"): [sys.executable, "scripts/nmbot_v2_version_compare.py"],
    ("release", "identity"): [sys.executable, "scripts/nmbot_release_identity.py"],
    ("contour", "recon"): [sys.executable, "scripts/nmbot_contour_recon.py"],
    ("architecture", None): [sys.executable, "scripts/nmbot_architecture_preflight.py"],
}


def _run(argv: list[str]) -> int:
    return subprocess.run(argv, cwd=ROOT, check=False).returncode


def _is_safe_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _bounded_error_code(code: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in code.lower())
    return (safe or "unknown")[:64]


def _bounded_human_value(value: Any, *, limit: int = 96) -> str:
    if value is None:
        return "-"
    if not _is_safe_scalar(value):
        return "-"
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return "-"
    return text[:limit]


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _safe_runtime_version(value: Any) -> str:
    if not isinstance(value, str):
        return "UNKNOWN"
    normalized = value.strip().upper()
    return normalized if normalized in RUNTIME_VERSIONS else "UNKNOWN"


def _safe_rel_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.startswith(("/", "~")):
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-")
    if any(ch not in allowed for ch in text):
        return None
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        return None
    return text


def _safe_symbol(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 128:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
    if any(ch not in allowed for ch in text):
        return None
    return text


def _safe_stage(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 64:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
    if any(ch not in allowed for ch in text):
        return None
    return text


def _existing_safe_repo_ref(value: Any) -> str | None:
    rel_path = _safe_rel_path(value)
    if rel_path is None:
        return None
    candidate = ROOT / rel_path
    try:
        candidate.relative_to(ROOT)
    except ValueError:
        return None
    return rel_path if candidate.is_file() else None


def _load_owner_stage_map() -> dict[str, Any] | None:
    path = ROOT / "config" / "nmbot_stage_map.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    stages = parsed.get("stages") if isinstance(parsed, dict) else None
    return stages if isinstance(stages, dict) else None


def _stage_from_task_layer(layer: Any, stages: dict[str, Any]) -> str | None:
    if not isinstance(layer, str) or not layer:
        return None
    if layer in stages and isinstance(stages.get(layer), dict):
        return layer
    matches: list[str] = []
    for stage_id, entry in stages.items():
        if not isinstance(stage_id, str) or not isinstance(entry, dict):
            continue
        for key in ("task_layer", "owner_layer"):
            if entry.get(key) == layer:
                matches.append(stage_id)
                break
    return matches[0] if len(matches) == 1 else None


def _stage_for_owner_card(result: dict[str, Any], stages: dict[str, Any]) -> tuple[str | None, str]:
    stage = result.get("stage")
    if isinstance(stage, str) and stage in stages and isinstance(stages.get(stage), dict):
        return stage, "stage"
    if result.get("kind") == "task":
        mapped_stage = _stage_from_task_layer(result.get("owner_layer"), stages)
        if mapped_stage is not None:
            return mapped_stage, "task_layer"
    return None, "unknown"


def _next_check_for_stage(stage_id: str, entry: dict[str, Any], focused_test: str | None) -> str | None:
    if focused_test is not None and focused_test.startswith("tests/") and focused_test.endswith(".py"):
        return f"python3 -m pytest -q {focused_test}"
    scope = stage_id.split(".", 1)[0]
    if scope in SUPPORTED_CHECK_SCOPES:
        return f"python3 scripts/nmbot_check.py {scope} --dry-run"
    return None


def _with_owner_card(result: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(result)
    for key in OWNER_CARD_FIELDS:
        enriched[key] = None
    enriched["owner_confidence"] = "unknown"
    stages = _load_owner_stage_map()
    if stages is None:
        return enriched
    stage_id, confidence = _stage_for_owner_card(enriched, stages)
    if stage_id is None:
        return enriched
    entry = stages.get(stage_id)
    if not isinstance(entry, dict):
        return enriched
    owner_source = _safe_rel_path(entry.get("source"))
    owner_symbol = _safe_symbol(entry.get("source_symbol"))
    contract_doc = _safe_rel_path(entry.get("doc"))
    focused_test = _safe_rel_path(entry.get("test"))
    enriched.update(
        {
            "owner_source": owner_source,
            "owner_symbol": owner_symbol,
            "contract_doc": contract_doc,
            "focused_test": focused_test,
            "next_check": _next_check_for_stage(stage_id, entry, focused_test),
            "owner_confidence": confidence,
        }
    )
    return enriched


def _safe_problem_code(result: dict[str, Any]) -> str | None:
    for key in ("error_code", "provider_error"):
        value = result.get(key)
        if _is_safe_scalar(value) and value is not None:
            return _bounded_error_code(str(value))
    return None


def _diagnostic_stage_for_plan(result: dict[str, Any]) -> str | None:
    stages = _load_owner_stage_map()
    if stages is None:
        return None
    stage_id, _confidence = _stage_for_owner_card(result, stages)
    return stage_id


def _build_edit_plan(result: dict[str, Any]) -> dict[str, Any]:
    diagnostic_stage = _diagnostic_stage_for_plan(result)
    owner_source = _existing_safe_repo_ref(result.get("owner_source"))
    contract_doc = _existing_safe_repo_ref(result.get("contract_doc"))
    focused_test = _existing_safe_repo_ref(result.get("focused_test"))
    owner_symbol = _safe_symbol(result.get("owner_symbol"))
    next_check = result.get("next_check") if isinstance(result.get("next_check"), str) else None
    ready = all([owner_source, owner_symbol, contract_doc, focused_test, next_check, diagnostic_stage])
    status = "ready" if ready else "blocked"
    return {
        "schema_version": DIAGNOSE_EDIT_PLAN_SCHEMA_VERSION,
        "status": status,
        "diagnostic_stage": diagnostic_stage,
        "problem_code": _safe_problem_code(result),
        "read_first": [item for item in (owner_source, contract_doc, focused_test) if item is not None],
        "suggested_change_surface": [owner_source, focused_test] if ready else [],
        "verification_command": next_check if ready else None,
        "requires_impact_chain": True,
        "documentation_after_verify": True,
        "safety": "manual_edit_only_no_auto_fix",
        "next_action": "read impact chain before editing" if ready else "obtain a confirmed stage or owner before editing",
    }


def _build_mutation_gate(result: dict[str, Any]) -> dict[str, Any]:
    envelope = result.get("diagnostic_envelope") if isinstance(result.get("diagnostic_envelope"), dict) else {}
    edit_plan = result.get("edit_plan") if isinstance(result.get("edit_plan"), dict) else {}
    stage = _safe_stage(result.get("stage"))
    runtime_version = _safe_runtime_version(result.get("runtime_version"))
    evidence_scope = envelope.get("evidence_scope") if envelope.get("evidence_scope") in EVIDENCE_SCOPES else "unknown"
    correlation = envelope.get("correlation") if isinstance(envelope.get("correlation"), dict) else {}
    status = result.get("status") if isinstance(result.get("status"), str) else "unknown"
    confidence = result.get("confidence") if isinstance(result.get("confidence"), str) else "unknown"
    owner_confidence = result.get("owner_confidence") if isinstance(result.get("owner_confidence"), str) else "unknown"
    expected_runtime = stage.split(".", 1)[0].upper() if stage and stage.split(".", 1)[0].upper() in RUNTIME_VERSIONS else None
    runtime_conflict = expected_runtime is not None and runtime_version != "UNKNOWN" and runtime_version != expected_runtime

    reasons: list[str] = []
    if status in {"no_evidence", "diagnostic_failed"}:
        reasons.append("diagnostic_evidence_missing")
    if result.get("kind") != "trace" or not correlation.get("trace_present"):
        reasons.append("explicit_trace_missing")
    if stage is None:
        reasons.append("diagnostic_stage_missing")
    if evidence_scope != "local":
        reasons.append("local_evidence_missing")
    if owner_confidence != "stage" or edit_plan.get("status") != "ready":
        reasons.append("exact_owner_not_proven")
    if confidence != "high":
        reasons.append("trace_confidence_not_high")
    if runtime_version == "UNKNOWN":
        reasons.append("runtime_version_unknown")
    if runtime_conflict:
        reasons.append("runtime_stage_conflict")

    no_evidence = status in {"no_evidence", "diagnostic_failed"} or stage is None or not correlation.get("trace_present")
    if runtime_conflict:
        verdict = "CONFLICT"
    elif no_evidence:
        verdict = "NO_EVIDENCE"
    elif not reasons and status == "reported":
        verdict = "PROVEN"
    else:
        verdict = "PARTIAL"

    surface = edit_plan.get("suggested_change_surface") if isinstance(edit_plan.get("suggested_change_surface"), list) else []
    allowed_paths = [path for path in (_existing_safe_repo_ref(item) for item in surface) if path is not None] if verdict == "PROVEN" else []
    verification_command = edit_plan.get("verification_command") if verdict == "PROVEN" and isinstance(edit_plan.get("verification_command"), str) else None
    if verdict == "PROVEN" and not allowed_paths:
        verdict, allowed_paths, verification_command = "PARTIAL", [], None
        reasons.append("allowed_surface_missing")

    return {
        "schema_version": MUTATION_GATE_SCHEMA_VERSION,
        "verdict": verdict,
        "mutation_scope": "local_source_only" if verdict == "PROVEN" else "none",
        "reason_codes": [_bounded_error_code(reason) for reason in reasons],
        "diagnostic_stage": stage,
        "allowed_paths": allowed_paths,
        "verification_command": verification_command,
        "evidence_scope": evidence_scope,
        "expires_in_seconds": 1800,
        "production_authorized": False,
        "deploy_authorized": False,
    }


def _diagnostic_failed(kind: str, error_code: str) -> dict[str, Any]:
    return {
        "schema_version": DIAGNOSE_SCHEMA_VERSION,
        "kind": kind,
        "status": "diagnostic_failed",
        "owner_layer": "unknown",
        "http_status": None,
        "task_id": None,
        "provider_error": None,
        "parse_status": "not_reported",
        "next_command": "check child diagnostic directly",
        "error_code": _bounded_error_code(error_code),
    }


def _diagnostic_envelope(result: dict[str, Any], *, evidence_scope: str, trace_present: bool = False, task_present: bool = False) -> dict[str, Any]:
    runtime_version = _safe_runtime_version(result.get("runtime_version"))
    runtime_source = "result" if runtime_version != "UNKNOWN" else None
    stage = _safe_stage(result.get("stage"))
    status = result.get("status") if _is_safe_scalar(result.get("status")) else None
    error_code = _safe_problem_code(result)
    owner_layer = result.get("owner_layer") if isinstance(result.get("owner_layer"), str) else None
    duration_ms = _safe_int(result.get("duration_ms"))
    return {
        "schema_version": DIAGNOSTIC_ENVELOPE_SCHEMA_VERSION,
        "evidence_scope": evidence_scope if evidence_scope in EVIDENCE_SCOPES else "unknown",
        "status": status,
        "runtime_version": runtime_version,
        "runtime_version_source": runtime_source,
        "first_failed_stage": stage if status not in {"reported", "ok", "success", "completed"} else None,
        "last_successful_stage": stage if status in {"reported", "ok", "success", "completed"} else None,
        "error_code": error_code,
        "owner_layer": owner_layer,
        "duration_ms": duration_ms,
        "correlation": {"trace_present": bool(trace_present), "task_present": bool(task_present)},
        "safety": {"read_only": True, "raw_output_included": False},
    }


def _add_diagnostic_envelope(result: dict[str, Any], *, evidence_scope: str, trace_present: bool = False, task_present: bool = False) -> dict[str, Any]:
    enriched = dict(result)
    enriched["diagnostic_envelope"] = _diagnostic_envelope(enriched, evidence_scope=evidence_scope, trace_present=trace_present, task_present=task_present)
    return enriched


def _recent_envelope(status: str) -> dict[str, Any]:
    return {
        "schema_version": DIAGNOSTIC_ENVELOPE_SCHEMA_VERSION,
        "evidence_scope": "historical",
        "status": status,
        "runtime_version": "UNKNOWN",
        "runtime_version_source": None,
        "first_failed_stage": None,
        "last_successful_stage": None,
        "error_code": None,
        "owner_layer": None,
        "duration_ms": None,
        "correlation": {"trace_present": False, "task_present": False},
        "safety": {"read_only": True, "raw_output_included": False},
    }


def _diagnose_output_format(args: list[str]) -> tuple[str | None, str | None]:
    human = "--human" in args
    json_output = "--json" in args
    if human and json_output:
        return None, "diagnose output flags are mutually exclusive: --human or --json"
    return "human" if human else "json", None


def _print_diagnose_result(result: dict[str, Any], output_format: str, *, include_plan: bool = False, include_gate: bool = False) -> None:
    result = _with_owner_card(result)
    if include_plan or include_gate:
        result["edit_plan"] = _build_edit_plan(result)
    if include_gate:
        result["mutation_gate"] = _build_mutation_gate(result)
    if output_format == "human":
        fields = [
            ("Статус", result.get("status")),
            ("владелец", result.get("owner_layer")),
            ("HTTP", result.get("http_status")),
            ("task", result.get("task_id")),
            ("provider", result.get("provider_error")),
            ("parse", result.get("parse_status")),
        ]
        if "stage" in result:
            fields.append(("stage", result.get("stage")))
        if "error_code" in result:
            fields.append(("error", result.get("error_code")))
        fields.append(("дальше", result.get("next_command")))
        owner_bits = [
            ("owner_source", result.get("owner_source")),
            ("owner_symbol", result.get("owner_symbol")),
            ("focused_test", result.get("focused_test")),
            ("next_check", result.get("next_check")),
        ]
        for label, value in owner_bits:
            if value is not None:
                fields.append((label, value))
        if include_plan:
            edit_plan = result.get("edit_plan") if isinstance(result.get("edit_plan"), dict) else {}
            surface = edit_plan.get("suggested_change_surface") if isinstance(edit_plan.get("suggested_change_surface"), list) else []
            safe_surface = [item for item in (_safe_rel_path(value) for value in surface) if item is not None]
            fields.extend(
                [
                    ("plan_status", edit_plan.get("status")),
                    ("surface", ",".join(safe_surface) if safe_surface else None),
                    ("verify", edit_plan.get("verification_command")),
                ]
            )
        print(" · ".join(f"{label}: {_bounded_human_value(value)}" for label, value in fields))
        return
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _recent_error_code(event: dict[str, Any]) -> str | None:
    for key in ("error_code", "error_type", "category"):
        value = event.get(key)
        if not _is_safe_scalar(value) or value is None:
            continue
        text = str(value).strip()
        if text:
            return _bounded_error_code(text)
    return None


def _recent_runtime_version(event: dict[str, Any]) -> str:
    value = event.get("runtime_version")
    if not isinstance(value, str):
        return "UNKNOWN"
    normalized = value.strip().upper()
    if normalized in {"V0", "V2", "V3"}:
        return normalized
    return "UNKNOWN"


def _recent_runtime_version_source(runtime_version: str) -> str:
    if runtime_version in {"V0", "V2", "V3"}:
        return "journal_event"
    return "insufficient_event_evidence"


def _recent_runtime_sort_key(runtime_version: str) -> tuple[int, str]:
    order = {"V0": 0, "V2": 1, "V3": 2, "UNKNOWN": 3}
    return (order.get(runtime_version, 99), runtime_version)


def _recent_journal_path(date: str, logs_dir: str) -> Path:
    return _resolve_local_path(str(Path(logs_dir) / f"bot_error_events-{date}.jsonl"))


def _recent_no_evidence(*, date: str, requested_limit: int, scanned_events: int = 0) -> dict[str, Any]:
    result = {
        "schema_version": DIAGNOSE_RECENT_SCHEMA_VERSION,
        "kind": "recent",
        "status": "no_evidence",
        "date": date,
        "requested_limit": requested_limit,
        "scanned_events": scanned_events,
        "actionable_events": 0,
        "runtime_version_scope": "historical_event_evidence_not_current_process",
        "runtime_versions": [],
        "groups": [],
        "next_command": RECENT_NEXT_COMMAND,
    }
    result["diagnostic_envelope"] = _recent_envelope("no_evidence")
    return result


def _recent_group_owner_card(error_code: str, stage: str | None) -> dict[str, Any]:
    enriched = _with_owner_card({"kind": "recent", "error_code": error_code, "stage": stage})
    return {key: enriched.get(key) for key in (*OWNER_CARD_FIELDS, "owner_confidence")}


def _build_recent_diagnose(limit: int, *, date: str, logs_dir: str) -> dict[str, Any]:
    journal_path = _recent_journal_path(date, logs_dir)
    if not journal_path.is_file():
        return _recent_no_evidence(date=date, requested_limit=limit)
    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return _recent_no_evidence(date=date, requested_limit=limit)

    scanned: list[dict[str, Any]] = []
    for line in reversed(lines):
        parsed = _parse_json_object(line)
        if parsed is None:
            continue
        scanned.append(parsed)
        if len(scanned) >= limit:
            break

    grouped: dict[tuple[str, str | None, str], int] = {}
    runtime_counts: dict[str, int] = {}
    actionable_events = 0
    for event in scanned:
        error_code = _recent_error_code(event)
        if error_code is None:
            continue
        actionable_events += 1
        stage = _safe_stage(event.get("stage"))
        runtime_version = _recent_runtime_version(event)
        runtime_counts[runtime_version] = runtime_counts.get(runtime_version, 0) + 1
        key = (error_code, stage, runtime_version)
        grouped[key] = grouped.get(key, 0) + 1

    if actionable_events == 0:
        return _recent_no_evidence(date=date, requested_limit=limit, scanned_events=len(scanned))

    groups: list[dict[str, Any]] = []
    for (error_code, stage, runtime_version), count in sorted(grouped.items(), key=lambda item: (-item[1], item[0][0], item[0][1] or "", item[0][2]))[:limit]:
        group = {
            "error_code": error_code,
            "stage": stage,
            "runtime_version": runtime_version,
            "runtime_version_source": _recent_runtime_version_source(runtime_version),
            "count": count,
        }
        group.update(_recent_group_owner_card(error_code, stage))
        groups.append(group)

    runtime_versions = [
        {"runtime_version": version, "count": runtime_counts[version]}
        for version in sorted(runtime_counts, key=_recent_runtime_sort_key)
    ]

    result = {
        "schema_version": DIAGNOSE_RECENT_SCHEMA_VERSION,
        "kind": "recent",
        "status": "reported",
        "date": date,
        "requested_limit": limit,
        "scanned_events": len(scanned),
        "actionable_events": actionable_events,
        "runtime_version_scope": "historical_event_evidence_not_current_process",
        "runtime_versions": runtime_versions,
        "groups": groups,
        "next_command": RECENT_NEXT_COMMAND,
    }
    result["diagnostic_envelope"] = _recent_envelope("reported")
    return result


def _print_recent_result(result: dict[str, Any], output_format: str) -> None:
    if output_format == "human":
        groups = result.get("groups") if isinstance(result.get("groups"), list) else []
        bits: list[str] = []
        for group in groups[:5]:
            if not isinstance(group, dict):
                continue
            code = _bounded_human_value(group.get("error_code"), limit=64)
            runtime_version = _bounded_human_value(group.get("runtime_version") or "UNKNOWN", limit=16)
            count = group.get("count") if isinstance(group.get("count"), int) else 0
            owner = _bounded_human_value(group.get("owner_symbol") or group.get("owner_source") or "unknown", limit=64)
            bits.append(f"{code} [{runtime_version}] ({count}; owner: {owner})")
        summary = ", ".join(bits) if bits else "нет безопасных повторяющихся ошибок"
        print(f"Последние ошибки: {summary}. Дальше: {RECENT_NEXT_COMMAND}")
        return
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _timeline_step_from_item(item: dict[str, Any], *, kind: str) -> dict[str, Any] | None:
    stage = _safe_stage(item.get("stage") or item.get("stage_id") or item.get("name"))
    if stage is None:
        return None
    raw_status = item.get("status") or item.get("outcome") or item.get("result")
    status = str(raw_status)[:64] if _is_safe_scalar(raw_status) and raw_status is not None else "reported"
    error_code = None
    for key in ("error_code", "provider_error", "category"):
        if _is_safe_scalar(item.get(key)) and item.get(key) is not None:
            error_code = _bounded_error_code(str(item.get(key)))
            break
    owner_layer = item.get("owner_layer") if isinstance(item.get("owner_layer"), str) else (_trace_owner_layer(stage) if kind == "trace" else None)
    step = {
        "stage": stage,
        "status": status,
        "duration_ms": _safe_int(item.get("duration_ms") or item.get("total_ms") or item.get("elapsed_ms")),
        "owner_layer": owner_layer,
        "error_code": error_code,
    }
    parse_status = str(item.get("parse_status") or "").strip()
    if parse_status in {"ok", "invalid_json", "missing"}:
        step["parse_status"] = parse_status
    gateway_task_id = item.get("gateway_task_id")
    if _is_safe_scalar(gateway_task_id) and gateway_task_id is not None:
        step["gateway_task_id"] = str(gateway_task_id)[:80]
    return step


def _status_is_failure(status: Any, error_code: Any = None) -> bool:
    if error_code is not None:
        return True
    if not isinstance(status, str):
        return False
    text = status.lower()
    return any(marker in text for marker in ("fail", "error", "timeout", "missing", "rejected"))


def _build_timeline_from_payload(kind: str, payload: dict[str, Any], normalized: dict[str, Any]) -> dict[str, Any]:
    raw_steps: list[Any] = []
    if kind == "trace":
        traces = payload.get("traces")
        raw_steps = []
        if isinstance(traces, list):
            for trace in traces:
                if not isinstance(trace, dict):
                    continue
                evidence = trace.get("evidence")
                if isinstance(evidence, list):
                    raw_steps.extend(item for item in evidence if isinstance(item, dict))
                else:
                    raw_steps.append(trace)
                actual = trace.get("actual") if isinstance(trace.get("actual"), dict) else {}
                gateway_attempts = actual.get("runtime_gateway_attempts") if isinstance(actual.get("runtime_gateway_attempts"), list) else []
                for attempt in gateway_attempts:
                    if isinstance(attempt, dict):
                        raw_steps.append({**attempt, "stage": attempt.get("stage") or "gateway_attempt", "status": "ok" if attempt.get("ok") is True else "failed" if attempt.get("ok") is False else "reported"})
                if len(raw_steps) >= 25:
                    break
    else:
        for key in ("timeline", "steps", "stages", "scenario"):
            value = payload.get(key)
            if isinstance(value, list):
                raw_steps = value
                break
    steps = []
    for item in raw_steps:
        if isinstance(item, dict):
            step = _timeline_step_from_item(item, kind=kind)
            if step is not None:
                steps.append(step)
        if len(steps) >= 25:
            break
    if not steps:
        fallback_stage = _safe_stage(normalized.get("stage"))
        if fallback_stage is not None:
            steps.append(
                {
                    "stage": fallback_stage,
                    "status": str(normalized.get("status") or "reported")[:64],
                    "duration_ms": _safe_int(normalized.get("duration_ms")),
                    "owner_layer": normalized.get("owner_layer") if isinstance(normalized.get("owner_layer"), str) else None,
                    "error_code": _safe_problem_code(normalized),
                }
            )
    first_failed = None
    last_success = None
    for step in steps:
        if _status_is_failure(step.get("status"), step.get("error_code")):
            if first_failed is None:
                first_failed = step.get("stage")
        else:
            last_success = step.get("stage")
    return {
        "schema_version": DIAGNOSE_TIMELINE_SCHEMA_VERSION,
        "status": "reported" if steps else "no_evidence",
        "evidence_scope": "local" if kind == "trace" else "unknown",
        "steps": steps,
        "first_failed_stage": first_failed,
        "last_successful_stage": last_success,
        "correlation_coverage": {"trace": kind == "trace", "task": kind == "task"},
    }


def _parse_event_timestamp(event: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "ts", "created_at", "time", "datetime"):
        value = event.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        parsed = _parse_json_object(line)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _is_user_turn(event: dict[str, Any]) -> bool:
    for key in ("kind", "event", "role", "direction", "type"):
        value = event.get(key)
        if isinstance(value, str) and value.lower() in {"user", "user_turn", "client", "client_message", "incoming"}:
            return True
    return False


def _is_bot_turn(event: dict[str, Any]) -> bool:
    for key in ("kind", "event", "role", "direction", "type"):
        value = event.get(key)
        if isinstance(value, str) and value.lower() in {"bot", "assistant", "bot_turn", "bot_message", "outgoing"}:
            return True
    return False


def _is_fallback(event: dict[str, Any]) -> bool:
    for key in ("fallback", "is_fallback", "safe_fallback"):
        if event.get(key) is True:
            return True
    stage = event.get("stage")
    if isinstance(stage, str) and "fallback" in stage.lower():
        return True
    answer_kind = event.get("answer_kind")
    if isinstance(answer_kind, str) and answer_kind.strip().lower() == "safe_upstream_fallback":
        return True
    error_summary = event.get("error_summary")
    if isinstance(error_summary, dict):
        for key in ("code", "error_code", "error_type"):
            value = error_summary.get(key)
            if isinstance(value, str) and "fallback" in value.lower()[:128]:
                return True
    for key in ("error_code", "error_type"):
        value = event.get(key)
        if isinstance(value, str) and "fallback" in value.lower()[:128]:
            return True
    return False


def _summary_latency_ms(event: dict[str, Any]) -> int | None:
    runtime_summary = event.get("runtime_summary")
    if isinstance(runtime_summary, dict):
        timing_ms = runtime_summary.get("timing_ms")
        if isinstance(timing_ms, dict):
            actual = _safe_int(timing_ms.get("total"))
            if actual is not None:
                return actual
    direct = _safe_int(event.get("total_ms"))
    if direct is not None:
        return direct
    if not isinstance(runtime_summary, dict):
        return None
    timing = runtime_summary.get("timing")
    if not isinstance(timing, dict):
        return None
    return _safe_int(timing.get("total_ms"))


def _percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int((len(ordered) - 1) * pct)
    return ordered[idx]


def _summary_no_evidence(window: str) -> dict[str, Any]:
    return {
        "schema_version": DIAGNOSE_SUMMARY_SCHEMA_VERSION,
        "status": "no_evidence",
        "window": window,
        "evidence_scope": "historical/local",
        "counts": {"user_turns": 0, "bot_turns": 0, "actionable_errors": 0},
        "error_rate": None,
        "fallback": {"count": 0, "rate": None},
        "latency_ms": {"count": 0, "p50": None, "p95": None, "p99": None},
        "runtime_versions": {"V0": 0, "V2": 0, "V3": 0, "UNKNOWN": 0},
        "saturation": {"status": "unavailable", "reason": "not_present_in_local_journals"},
    }


def _build_summary(window: str, *, date: str, logs_dir: str) -> dict[str, Any]:
    if window != "1h":
        return {"error": "unsupported summary window: only 1h is supported"}
    base = _resolve_local_path(logs_dir)
    rows = []
    for path in (base / "dialogue_journal.jsonl", base / f"bot_error_events-{date}.jsonl"):
        rows.extend(_read_jsonl_objects(path))
    stamped = [(row, _parse_event_timestamp(row)) for row in rows]
    timestamps = [ts for _row, ts in stamped if ts is not None]
    if not timestamps:
        return _summary_no_evidence(window)
    cutoff = max(timestamps) - timedelta(hours=1)
    selected = [row for row, ts in stamped if ts is not None and ts >= cutoff]
    user_turns = sum(1 for row in selected if _is_user_turn(row))
    bot_turns = sum(1 for row in selected if _is_bot_turn(row))
    actionable_errors = sum(1 for row in selected if _recent_error_code(row) is not None)
    fallback_count = sum(1 for row in selected if _is_fallback(row))
    latencies = [_summary_latency_ms(row) for row in selected]
    safe_latencies = [value for value in latencies if value is not None]
    version_counts = {"V0": 0, "V2": 0, "V3": 0, "UNKNOWN": 0}
    for row in selected:
        version_counts[_safe_runtime_version(row.get("runtime_version"))] += 1
    denominator = user_turns if user_turns > 0 else None
    return {
        "schema_version": DIAGNOSE_SUMMARY_SCHEMA_VERSION,
        "status": "reported" if selected else "no_evidence",
        "window": window,
        "evidence_scope": "historical/local",
        "counts": {"user_turns": user_turns, "bot_turns": bot_turns, "actionable_errors": actionable_errors},
        "error_rate": (actionable_errors / denominator) if denominator else None,
        "fallback": {"count": fallback_count, "rate": (fallback_count / bot_turns) if bot_turns > 0 else None},
        "latency_ms": {"count": len(safe_latencies), "p50": _percentile(safe_latencies, 0.50), "p95": _percentile(safe_latencies, 0.95), "p99": _percentile(safe_latencies, 0.99)},
        "runtime_versions": version_counts,
        "saturation": {"status": "unavailable", "reason": "not_present_in_local_journals"},
    }


def _print_summary_result(result: dict[str, Any], output_format: str) -> None:
    if output_format == "human":
        counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
        latency = result.get("latency_ms") if isinstance(result.get("latency_ms"), dict) else {}
        print(
            "Summary 1h: "
            f"status={_bounded_human_value(result.get('status'))} · "
            f"user_turns={_bounded_human_value(counts.get('user_turns'))} · "
            f"bot_turns={_bounded_human_value(counts.get('bot_turns'))} · "
            f"errors={_bounded_human_value(counts.get('actionable_errors'))} · "
            f"latency_p95={_bounded_human_value(latency.get('p95'))}"
        )
        return
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _extract_recent_request(args: list[str]) -> tuple[int | None, str | None, str | None, str | None]:
    recent_limit: int | None = None
    date: str | None = None
    logs_dir: str | None = None
    selector_seen: str | None = None
    plan_seen = False
    idx = 0
    while idx < len(args):
        item = args[idx]
        if item in {"--human", "--json", "--gate"}:
            idx += 1
            continue
        if item == "--timeline":
            idx += 1
            continue
        if item == "--plan":
            if selector_seen == "--recent":
                return None, None, None, "--recent is incompatible with --plan"
            plan_seen = True
            idx += 1
            continue
        if item == "--recent":
            if plan_seen:
                return None, None, None, "--recent is incompatible with --plan"
            if selector_seen is not None:
                return None, None, None, "exactly one selector is required: --trace, --task, --latest, or --recent"
            selector_seen = "--recent"
            if idx + 1 >= len(args):
                return None, None, None, "missing value for --recent"
            value = args[idx + 1]
            if not value.isdecimal():
                return None, None, None, "--recent must be a decimal integer from 1 to 100"
            parsed_limit = int(value)
            if parsed_limit < 1 or parsed_limit > 100:
                return None, None, None, "--recent must be a decimal integer from 1 to 100"
            recent_limit = parsed_limit
            idx += 2
            continue
        if item in {"--trace", "--task", "--latest"}:
            if selector_seen is not None:
                return None, None, None, "exactly one selector is required: --trace, --task, --latest, or --recent"
            selector_seen = item
            if item in {"--trace", "--task"}:
                if idx + 1 >= len(args) or args[idx + 1].startswith("-"):
                    return None, None, None, f"missing value for {item}"
                idx += 2
            else:
                idx += 1
            continue
        if item in {"--date", "--logs-dir"}:
            if idx + 1 >= len(args) or args[idx + 1].startswith("-"):
                return None, None, None, f"missing value for {item}"
            if item == "--date":
                date = args[idx + 1]
            else:
                logs_dir = args[idx + 1]
            idx += 2
            continue
        return None, None, None, f"unsupported diagnose option: {item}"
    if recent_limit is None:
        return None, None, None, None
    return recent_limit, date or _utc_date(), logs_dir or "logs", None


def _extract_summary_request(args: list[str]) -> tuple[str | None, str | None, str | None, str | None]:
    window: str | None = None
    date: str | None = None
    logs_dir: str | None = None
    idx = 0
    incompatible = {"--trace", "--task", "--latest", "--recent", "--plan", "--timeline"}
    while idx < len(args):
        item = args[idx]
        if item in {"--human", "--json"}:
            idx += 1
            continue
        if item == "--summary":
            if window is not None:
                return None, None, None, "--summary may be specified only once"
            if idx + 1 >= len(args) or args[idx + 1].startswith("-"):
                return None, None, None, "missing value for --summary"
            window = args[idx + 1]
            if window != "1h":
                return None, None, None, "unsupported summary window: only 1h is supported"
            idx += 2
            continue
        if item in {"--date", "--logs-dir"}:
            if idx + 1 >= len(args) or args[idx + 1].startswith("-"):
                return None, None, None, f"missing value for {item}"
            if item == "--date":
                date = args[idx + 1]
            else:
                logs_dir = args[idx + 1]
            idx += 2
            continue
        if item in incompatible:
            return None, None, None, "--summary is incompatible with selectors, --recent, --plan, and --timeline"
        return None, None, None, f"unsupported diagnose option: {item}"
    if window is None:
        return None, None, None, None
    return window, date or _utc_date(), logs_dir or "logs", None


def _timeline_requested(args: list[str]) -> bool:
    return "--timeline" in args


def _validate_timeline_request(args: list[str]) -> str | None:
    if not _timeline_requested(args):
        return None
    if args.count("--timeline") > 1:
        return "--timeline may be specified only once"
    if "--recent" in args:
        return "--timeline is incompatible with --recent"
    if "--plan" in args:
        return "--timeline is incompatible with --plan"
    selectors = [item for item in args if item in {"--trace", "--task", "--latest"}]
    if len(selectors) > 1:
        return "--timeline requires exactly one selector or implicit latest"
    return None


def _validate_gate_request(args: list[str]) -> str | None:
    if "--json" not in args or "--human" in args:
        return "--gate requires JSON output via --json"
    if any(item in args for item in {"--latest", "--recent", "--summary", "--timeline", "--task"}):
        return "--gate requires only an explicit --trace TRACE_ID selector"
    if args.count("--trace") != 1:
        return "--gate requires exactly one explicit --trace TRACE_ID selector"
    trace_index = args.index("--trace")
    if trace_index + 1 >= len(args) or args[trace_index + 1].startswith("-"):
        return "--gate requires a safe explicit trace ID"
    trace_id = args[trace_index + 1]
    if len(trace_id) > 128 or any(not (ch.isalnum() or ch in "_.:-") for ch in trace_id):
        return "--gate requires a safe explicit trace ID"
    return None


def _trace_owner_layer(stage: Any) -> str:
    if not isinstance(stage, str):
        return "unknown"
    if stage in {"delivery_complete", "delivery_missing", "upstream_missing"} or stage.startswith("bridge_") or stage.startswith("delivery_") or stage.startswith("transport_"):
        return "jivo_bridge"
    if stage in TRACE_RUNTIME_STAGES or stage.startswith("main_search"):
        return "runtime"
    if stage == "upstream_failure":
        return "api_or_upstream"
    return "unknown"


def _trace_http_status(trace: dict[str, Any]) -> int | None:
    values: list[int] = []
    evidence = trace.get("evidence")
    if not isinstance(evidence, list):
        return None
    for item in evidence:
        if not isinstance(item, dict):
            continue
        value = item.get("http_status")
        if isinstance(value, int):
            values.append(value)
    explicit_errors = [value for value in values if value >= 400]
    if explicit_errors:
        return explicit_errors[-1]
    return values[-1] if values else None


def _normalize_trace(payload: dict[str, Any]) -> dict[str, Any]:
    traces = payload.get("traces")
    first_trace = traces[0] if isinstance(traces, list) and traces and isinstance(traces[0], dict) else None
    if not first_trace:
        return {
            "schema_version": DIAGNOSE_SCHEMA_VERSION,
            "kind": "trace",
            "status": "no_evidence",
            "owner_layer": "unknown",
            "http_status": None,
            "task_id": None,
            "provider_error": None,
            "parse_status": "not_reported",
            "next_command": "bash scripts/nmbot_diag.sh --logs",
        }
    stage = first_trace.get("stage")
    result: dict[str, Any] = {
        "schema_version": DIAGNOSE_SCHEMA_VERSION,
        "kind": "trace",
        "status": "reported",
        "owner_layer": _trace_owner_layer(stage),
        "http_status": _trace_http_status(first_trace),
        "task_id": None,
        "provider_error": None,
        "parse_status": "not_reported",
        "next_command": "bash scripts/nmbot_diag.sh --logs",
    }
    for key in ("stage", "outcome", "confidence"):
        value = first_trace.get(key)
        if _is_safe_scalar(value):
            result[key] = value
    runtime_version = _safe_runtime_version(first_trace.get("runtime_version"))
    if runtime_version != "UNKNOWN":
        result["runtime_version"] = runtime_version
    return result


def _trace_no_evidence(*, error_code: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": DIAGNOSE_SCHEMA_VERSION,
        "kind": "trace",
        "status": "no_evidence",
        "owner_layer": "unknown",
        "http_status": None,
        "task_id": None,
        "provider_error": None,
        "parse_status": "not_reported",
        "next_command": "bash scripts/nmbot_diag.sh --logs",
    }
    if error_code:
        result["error_code"] = _bounded_error_code(error_code)
    return result


def _latest_no_evidence(error_code: str) -> dict[str, Any]:
    return {
        "schema_version": DIAGNOSE_SCHEMA_VERSION,
        "kind": "latest",
        "status": "no_evidence",
        "owner_layer": "unknown",
        "http_status": None,
        "task_id": None,
        "provider_error": None,
        "parse_status": "not_reported",
        "next_command": "bash scripts/nmbot_diag.sh --logs",
        "error_code": _bounded_error_code(error_code),
    }


def _normalize_task(payload: dict[str, Any]) -> dict[str, Any]:
    layer = payload.get("layer")
    if layer not in TASK_LAYERS:
        layer = "unknown"
    raw_status = payload.get("status")
    status = raw_status if _is_safe_scalar(raw_status) and raw_status is not None else "reported"
    error_code = payload.get("error_code")
    provider_error = (
        str(error_code)[:64]
        if payload.get("category") == "provider_error" and _is_safe_scalar(error_code) and error_code is not None
        else None
    )
    result: dict[str, Any] = {
        "schema_version": DIAGNOSE_SCHEMA_VERSION,
        "kind": "task",
        "status": status,
        "owner_layer": layer,
        "http_status": None,
        "task_id": str(payload.get("task_id")) if _is_safe_scalar(payload.get("task_id")) and payload.get("task_id") is not None else None,
        "provider_error": provider_error,
        "parse_status": "not_reported",
        "next_command": "python3 scripts/nmbot_gateway_task_diag.py TASK_ID --scenario --json",
    }
    if _is_safe_scalar(error_code) and error_code is not None:
        result["error_code"] = str(error_code)
    stage = payload.get("stage")
    if _is_safe_scalar(stage) and stage is not None:
        result["stage"] = str(stage)
    return result


def _utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _resolve_local_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def _diagnostic_identifier(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _actionable_id_from_event(event: dict[str, Any]) -> tuple[str, str] | None:
    for key in ("task_id", "gateway_task_id"):
        value = _diagnostic_identifier(event.get(key))
        if value is not None:
            return "task", value
    nested_task = event.get("task")
    if isinstance(nested_task, dict):
        for key in ("id", "task_id"):
            value = _diagnostic_identifier(nested_task.get(key))
            if value is not None:
                return "task", value
    trace_id = _diagnostic_identifier(event.get("trace_id"))
    if trace_id is not None:
        return "trace", trace_id
    return None


def _latest_actionable_from_journal(journal_path: Path) -> tuple[str | None, str | None, str | None]:
    if not journal_path.is_file():
        return None, None, "error_log_missing"
    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, None, "error_log_missing"
    for line in reversed(lines):
        parsed = _parse_json_object(line)
        if parsed is None:
            continue
        actionable = _actionable_id_from_event(parsed)
        if actionable is not None:
            return actionable[0], actionable[1], None
    return None, None, "actionable_event_missing"


def _build_diagnose_argv(args: list[str]) -> tuple[str | None, list[str] | None, str | None, bool]:
    selector_kind: str | None = None
    selector_value: str | None = None
    date: str | None = None
    logs_dir: str | None = None
    selected_latest = False
    idx = 0
    while idx < len(args):
        item = args[idx]
        if item in {"--human", "--json", "--plan", "--timeline", "--gate"}:
            idx += 1
            continue
        if item == "--latest":
            if selector_kind is not None:
                return None, None, "exactly one selector is required: --trace, --task, or --latest", selected_latest
            selector_kind, selector_value = "latest", "latest"
            selected_latest = True
            idx += 1
            continue
        if item in {"--trace", "--task", "--date", "--logs-dir"}:
            if idx + 1 >= len(args) or args[idx + 1].startswith("-"):
                return None, None, f"missing value for {item}", selected_latest
            value = args[idx + 1]
            if item in {"--trace", "--task"}:
                kind = item[2:]
                if selector_kind is not None:
                    return None, None, "exactly one selector is required: --trace, --task, or --latest", selected_latest
                selector_kind, selector_value = kind, value
            elif item == "--date":
                date = value
            else:
                logs_dir = value
            idx += 2
            continue
        return None, None, f"unsupported diagnose option: {item}", selected_latest
    if selector_kind is None or selector_value is None:
        selector_kind, selector_value = "latest", "latest"
        selected_latest = True
    if selector_kind == "latest":
        selected_latest = True
        selected_date = date or _utc_date()
        selected_logs_dir = logs_dir or "logs"
        journal_path = _resolve_local_path(str(Path(selected_logs_dir) / f"bot_error_events-{selected_date}.jsonl"))
        latest_kind, latest_value, latest_error = _latest_actionable_from_journal(journal_path)
        if latest_error:
            return "latest", None, latest_error, selected_latest
        assert latest_kind is not None and latest_value is not None
        selector_kind, selector_value = latest_kind, latest_value
        if latest_kind == "task":
            date = selected_date
            logs_dir = selected_logs_dir
    if selector_kind == "trace":
        base_logs = Path(logs_dir) if logs_dir else Path("logs")
        return selector_kind, [
            sys.executable,
            "scripts/nmbot_jivo_dialogue_diagnose.py",
            str(base_logs / "n8n_bridge_structured.jsonl"),
            "--audit-log",
            str(base_logs / "dialogue_journal.jsonl"),
            "--trace",
            selector_value,
            "--json",
        ], None, selected_latest
    child_argv = [sys.executable, "scripts/nmbot_gateway_task_diag.py", selector_value, "--json"]
    if date is not None:
        child_argv.extend(["--date", date])
    if logs_dir is not None:
        child_argv.extend(["--logs-dir", logs_dir])
    return selector_kind, child_argv, None, selected_latest


def _run_diagnose(args: list[str]) -> int:
    include_gate = "--gate" in args
    include_plan = "--plan" in args or include_gate
    output_format, output_error = _diagnose_output_format(args)
    if output_error:
        print(f"ERROR: {output_error}", file=sys.stderr)
        return 2
    assert output_format is not None
    if include_gate:
        gate_error = _validate_gate_request(args)
        if gate_error:
            print(f"ERROR: {gate_error}", file=sys.stderr)
            return 2
    if "--summary" in args:
        window, summary_date, summary_logs_dir, summary_error = _extract_summary_request(args)
        if summary_error:
            print(f"ERROR: {summary_error}", file=sys.stderr)
            return 2
        assert window is not None and summary_date is not None and summary_logs_dir is not None
        result = _build_summary(window, date=summary_date, logs_dir=summary_logs_dir)
        if "error" in result:
            print(f"ERROR: {result['error']}", file=sys.stderr)
            return 2
        _print_summary_result(result, output_format)
        return 0
    timeline_error = _validate_timeline_request(args)
    if timeline_error:
        print(f"ERROR: {timeline_error}", file=sys.stderr)
        return 2
    recent_limit, recent_date, recent_logs_dir, recent_error = _extract_recent_request(args)
    if recent_error:
        print(f"ERROR: {recent_error}", file=sys.stderr)
        return 2
    if recent_limit is not None:
        assert recent_date is not None and recent_logs_dir is not None
        result = _build_recent_diagnose(recent_limit, date=recent_date, logs_dir=recent_logs_dir)
        _print_recent_result(result, output_format)
        return 0
    kind, child_argv, error, selected_latest = _build_diagnose_argv(args)
    if error:
        if kind == "latest" and error in {"error_log_missing", "actionable_event_missing", "bridge_log_missing"}:
            result = _add_diagnostic_envelope(_latest_no_evidence(error), evidence_scope="historical")
            if _timeline_requested(args):
                result["timeline"] = {
                    "schema_version": DIAGNOSE_TIMELINE_SCHEMA_VERSION,
                    "status": "no_evidence",
                    "evidence_scope": "historical",
                    "steps": [],
                    "first_failed_stage": None,
                    "last_successful_stage": None,
                    "correlation_coverage": {"trace": False, "task": False},
                }
            _print_diagnose_result(result, output_format, include_plan=include_plan, include_gate=include_gate)
            return 0
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    assert kind is not None and child_argv is not None
    if kind == "trace":
        bridge_log = Path(child_argv[2])
        if not bridge_log.is_absolute():
            bridge_log = ROOT / bridge_log
        if not bridge_log.is_file():
            no_evidence = _latest_no_evidence("bridge_log_missing") if selected_latest else _trace_no_evidence(error_code="bridge_log_missing")
            no_evidence = _add_diagnostic_envelope(no_evidence, evidence_scope="historical" if selected_latest else "local", trace_present=kind == "trace")
            if _timeline_requested(args):
                no_evidence["timeline"] = {
                    "schema_version": DIAGNOSE_TIMELINE_SCHEMA_VERSION,
                    "status": "no_evidence",
                    "evidence_scope": "local",
                    "steps": [],
                    "first_failed_stage": None,
                    "last_successful_stage": None,
                    "correlation_coverage": {"trace": kind == "trace", "task": False},
                }
            _print_diagnose_result(no_evidence, output_format, include_plan=include_plan, include_gate=include_gate)
            return 0
    completed = subprocess.run(child_argv, cwd=ROOT, check=False, capture_output=True, text=True)
    parsed = _parse_json_object(completed.stdout or "")
    if parsed is None and completed.returncode != 0:
        parsed = _parse_json_object(completed.stderr or "")
    if parsed is None:
        result = _add_diagnostic_envelope(_diagnostic_failed(kind, "child_json_parse_error"), evidence_scope="unknown", trace_present=kind == "trace", task_present=kind == "task")
        _print_diagnose_result(result, output_format, include_plan=include_plan, include_gate=include_gate)
        return completed.returncode if completed.returncode != 0 else 3
    result = _normalize_trace(parsed) if kind == "trace" else _normalize_task(parsed)
    result = _add_diagnostic_envelope(result, evidence_scope="local" if kind == "trace" else "unknown", trace_present=kind == "trace", task_present=kind == "task")
    if _timeline_requested(args):
        result["timeline"] = _build_timeline_from_payload(kind, parsed, result)
    _print_diagnose_result(result, output_format, include_plan=include_plan, include_gate=include_gate)
    return completed.returncode


def _validate_check_args(args: list[str]) -> str | None:
    for item in args:
        if item.startswith("-"):
            if item not in SUPPORTED_CHECK_FLAGS:
                return f"unsupported check option: {item}"
            continue
        if item not in SUPPORTED_CHECK_SCOPES:
            return f"unknown check scope: {item}"
    return None


def _build_explain_argv(args: list[str]) -> tuple[list[str] | None, str | None]:
    return [sys.executable, "scripts/nmbot_response_path.py", *args], None


def _load_tools_registry() -> tuple[dict[str, Any] | None, str | None]:
    path = ROOT / "config" / "nmbot_diagnostic_tools.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None, "diagnostic tools registry is missing or malformed"
    if not isinstance(parsed, dict) or parsed.get("schema_version") != DIAGNOSTIC_TOOLS_REGISTRY_SCHEMA_VERSION:
        return None, "diagnostic tools registry schema mismatch"
    tools = parsed.get("tools")
    if not isinstance(tools, list) or not tools:
        return None, "diagnostic tools registry must contain tools"
    names = set()
    required = {"name", "status", "path", "purpose", "evidence_scope", "network", "side_effects", "canonical_wrapper", "replacement", "notes"}
    for item in tools:
        if not isinstance(item, dict) or not required.issubset(item):
            return None, "diagnostic tools registry contains invalid tool rows"
        name = item.get("name")
        if not isinstance(name, str) or not name or name in names:
            return None, "diagnostic tools registry contains invalid or duplicate names"
        names.add(name)
        status = item.get("status")
        if status not in DIAGNOSTIC_TOOL_STATUSES:
            return None, "diagnostic tools registry contains invalid status"
        if _existing_safe_repo_ref(item.get("path")) is None:
            return None, "diagnostic tools registry contains invalid path"
        if item.get("evidence_scope") not in EVIDENCE_SCOPES:
            return None, "diagnostic tools registry contains invalid evidence_scope"
        if not isinstance(item.get("network"), bool) or not isinstance(item.get("side_effects"), bool):
            return None, "diagnostic tools registry network/side_effects must be boolean"
        for key in ("canonical_wrapper", "replacement", "notes"):
            if not _is_safe_scalar(item.get(key)):
                return None, "diagnostic tools registry metadata fields must be scalar or null"
        if status == "legacy" and item.get("canonical_wrapper") is not None:
            return None, "diagnostic tools registry legacy rows must not have canonical_wrapper"
    return parsed, None


def _run_tools(args: list[str]) -> int:
    output_format, output_error = _diagnose_output_format(args)
    if output_error:
        print(f"ERROR: {output_error}", file=sys.stderr)
        return 2
    for item in args:
        if item not in {"--human", "--json"}:
            print(f"ERROR: unsupported tools option: {item}", file=sys.stderr)
            return 2
    registry, error = _load_tools_registry()
    if error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    assert registry is not None and output_format is not None
    if output_format == "human":
        for tool in registry["tools"]:
            wrapper = tool.get("canonical_wrapper") or "-"
            legacy = " legacy" if tool.get("status") == "legacy" else ""
            print(f"{tool['name']}: {tool['status']}{legacy}; scope={tool['evidence_scope']}; network={tool['network']}; wrapper={wrapper}")
        return 0
    print(json.dumps(registry, ensure_ascii=False, sort_keys=True))
    return 0


def _run_namespace(command: str, rest: list[str]) -> int | None:
    if command == "architecture":
        return _run([*NAMESPACE_ALIASES[("architecture", None)], *rest])
    if command not in {"trace", "dialogue", "planner", "runtime", "release", "contour"}:
        return None
    if not rest:
        print(f"ERROR: missing {command} subcommand", file=sys.stderr)
        return 2
    subcommand, subrest = rest[0], rest[1:]
    base = NAMESPACE_ALIASES.get((command, subcommand))
    if base is None:
        print(f"ERROR: unknown {command} subcommand: {subcommand}", file=sys.stderr)
        return 2
    if command == "release" and subcommand == "identity":
        if not subrest:
            print("ERROR: missing release identity subcommand: read or show", file=sys.stderr)
            return 2
        identity_command, identity_rest = subrest[0], subrest[1:]
        if identity_command not in {"read", "show"}:
            print(f"ERROR: unknown or unsafe release identity subcommand: {identity_command}", file=sys.stderr)
            return 2
        if identity_rest:
            print("ERROR: unsupported release identity arguments for wrapper alias", file=sys.stderr)
            return 2
    return _run([*base, *subrest])


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("usage: nmbot.py {check|audit|preflight|diag|diagnose|tools|trace|dialogue|planner|runtime|release|contour|architecture|context|retrieve|navigate|context-gate|docs-gate|memory-registry|memory-outcomes|experiment|recipes|explain} [args...]\n")
        print("Thin wrapper: delegates by direct argv and preserves exit codes.")
        print("context prints local-only documentation context packs and never runs checks.")
        print("retrieve delegates to scripts/nmbot_retrieval.py for local SQLite FTS candidate cards; optional --source-cards adds navigation context only, for example: retrieve \"finance disclaimer first list\" --term runtime --json.")
        print("navigate delegates to scripts/nmbot_navigation.py for local deterministic stage/symbol/docs navigation; fallback results are candidate-only.")
        print("context-gate delegates direct argv to scripts/nmbot_context_gate.py for local STOP-2 route/trace enforcement; it does not call notebooks, network or production.")
        print("docs-gate delegates direct argv to scripts/project_documentation_gate.py for local fail-closed documentation update queues; it never edits docs or calls notebooks.")
        print("memory-registry delegates direct argv to scripts/project_memory_registry.py for passive project identity validation/resolution; it does not read sources or call memory tools.")
        print("memory-outcomes delegates direct argv to scripts/project_memory_outcomes.py for passive append-only privacy-safe outcome metadata; hints are disabled by policy.")
        print("experiment delegates direct argv to scripts/nmbot_experiment.py for local, offline experiment records and checks.")
        print("explain resolves a local/read-only response path via scripts/nmbot_response_path.py, for example: explain, explain --version v2 --json, or explain --path-id jivo.v2.turn.v1 --json.")
        print("recipes overlap runs the explicit local Ollama recipe-overlap analysis command.")
        print("recipes pair RECIPE_A RECIPE_B prints a local-only deterministic Markdown/JSON overlap card.")
        print("recipes explain RECIPE_A RECIPE_B prints a local-only deterministic review card from the pair report.")
        print("diag delegates to bash scripts/nmbot_diag.sh only when explicitly invoked; user args may select VPS/network mode.")
        print("diagnose [--trace TRACE_ID|--task TASK_ID|--latest|--recent N|--summary 1h] [--timeline] [--plan] [--gate] [--json|--human] runs safe diagnostics; --gate requires an explicit local trace and JSON output.")
        print("tools [--json|--human] reads local config/nmbot_diagnostic_tools.json and marks legacy diagnostic tools without executing them.")
        print("trace/dialogue/planner/runtime/release/contour/architecture expose allowlisted direct-argv diagnostic aliases only; release identity is restricted to read/show and contour recon requires --contour.")
        return 0 if args and args[0] in {"-h", "--help"} else 2

    command, rest = args[0], args[1:]
    if command == "check":
        error = _validate_check_args(rest)
        if error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        return _run([sys.executable, "scripts/nmbot_check.py", *rest])
    if command == "audit":
        return _run([sys.executable, "scripts/nmbot_project_audit.py", *rest])
    if command == "preflight":
        return _run([sys.executable, "scripts/nmbot_release_preflight.py", *rest])
    if command == "diag":
        return _run(["bash", "scripts/nmbot_diag.sh", *rest])
    if command == "diagnose":
        return _run_diagnose(rest)
    if command == "tools":
        return _run_tools(rest)
    namespace_result = _run_namespace(command, rest)
    if namespace_result is not None:
        return namespace_result
    if command == "context":
        return _run([sys.executable, "scripts/nmbot_context_pack.py", *rest])
    if command == "retrieve":
        return _run([sys.executable, "scripts/nmbot_retrieval.py", *rest])
    if command == "navigate":
        return _run([sys.executable, "scripts/nmbot_navigation.py", *rest])
    if command == "context-gate":
        return _run([sys.executable, "scripts/nmbot_context_gate.py", *rest])
    if command == "docs-gate":
        return _run([sys.executable, "scripts/project_documentation_gate.py", *rest])
    if command == "memory-registry":
        return _run([sys.executable, "scripts/project_memory_registry.py", *rest])
    if command == "memory-outcomes":
        return _run([sys.executable, "scripts/project_memory_outcomes.py", *rest])
    if command == "experiment":
        return _run([sys.executable, "scripts/nmbot_experiment.py", *rest])
    if command == "explain":
        explain_argv, error = _build_explain_argv(rest)
        if error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        assert explain_argv is not None
        return _run(explain_argv)
    if command == "recipes":
        if not rest:
            print("ERROR: missing recipes subcommand: overlap, pair, or explain", file=sys.stderr)
            return 2
        subcommand, subrest = rest[0], rest[1:]
        if subcommand == "overlap":
            return _run([sys.executable, "scripts/nmbot_recipe_overlap.py", *subrest])
        if subcommand == "pair":
            if len(subrest) < 2 or subrest[0].startswith("-") or subrest[1].startswith("-"):
                print("ERROR: usage: nmbot.py recipes pair RECIPE_A RECIPE_B [--human|--json]", file=sys.stderr)
                return 2
            return _run([sys.executable, "scripts/nmbot_recipe_overlap.py", "--pair", subrest[0], subrest[1], *subrest[2:]])
        if subcommand == "explain":
            if len(subrest) < 2 or subrest[0].startswith("-") or subrest[1].startswith("-"):
                print("ERROR: usage: nmbot.py recipes explain RECIPE_A RECIPE_B [--human|--json]", file=sys.stderr)
                return 2
            return _run([sys.executable, "scripts/nmbot_recipe_overlap.py", "--explain", subrest[0], subrest[1], *subrest[2:]])
        print(f"ERROR: unknown recipes subcommand: {subcommand}", file=sys.stderr)
        return 2
    print(f"ERROR: unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
