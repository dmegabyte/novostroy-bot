#!/usr/bin/env python3
"""Read-only diagnostic report for a single Jivo dialogue session.

Sources:
  - logs/dialogue_journal.jsonl (canonical dialogue journal)
  - logs/planner_trace-YYYY-MM-DD.jsonl (planner trace)

The script intentionally prints only redacted text and opaque references.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from nmbot_v1.prompt_provenance import sanitize_prompt_provenance as sanitize_v1_prompt_provenance
from nmbot_v2.prompt_provenance import sanitize_prompt_provenance
from nmbot_v2.execution_path import sanitize_execution_path
from nmbot_v1.execution_path import sanitize_execution_path as sanitize_v1_execution_path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = ROOT / "logs"
PROD_HOST = "neiro@193.107.155.236"
PROD_PORT = "1905"
PROD_LOG_DIR = "/home/neiro/novostroy-bot/logs"
NEARBY_WINDOW_SECONDS = 5 * 60
PLANNER_NEAREST_SECONDS = 10 * 60
DEFAULT_RUNTIME_BACKFILL_NAME = "dialogue_runtime_versions_backfill.jsonl"
VALID_RUNTIME_VERSIONS = {"V0", "V1", "V2", "V3"}
SAFE_RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\s()\-]*){10,}\d(?!\w)")
TOKEN_RE = re.compile(r"\b(?:Bearer\s+)?[A-Za-z0-9_\-]{24,}\.[A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]{8,})?\b")
LONG_ID_RE = re.compile(r"\b(?:jivo:[^\s,;]+|[a-f0-9]{24,}|\d{10,})\b", re.I)
AUTH_RE = re.compile(r"Authorization\s*[:=]\s*[^\s,;]+", re.I)

SAFE_RUNTIME_KEYS = {"stage", "action", "answer_kind", "quality_blockers", "call_counts", "grounding_scope"}


def sanitize_any_execution_path(value: Any) -> dict[str, Any] | None:
    return sanitize_v1_execution_path(value) or sanitize_execution_path(value)


def sanitize_any_prompt_provenance(value: Any) -> dict[str, Any] | None:
    return sanitize_v1_prompt_provenance(value) or sanitize_prompt_provenance(value)
SAFE_STATE_KEYS = {"pending_followup", "selected_present", "visible_options_count"}
SAFE_ERROR_SUMMARY_STAGES = {"runtime", "composer", "search_validation", "jivo_handler", "bridge_upstream", "bridge_delivery"}
SAFE_ERROR_SUMMARY_CODES = {
    "runtime_failure", "jivo_handler_exception", "search_validation_error", "composer_error",
    "composer_validation_failed", "runtime_error", "question_count_not_one",
    "final_question_not_at_end", "search_without_cards", "enrichment_error",
    "bridge_hard_timeout", "bridge_upstream_exception", "bridge_status_delivery_error",
    "bridge_delivery_error", "bridge_async_exception",
    "empty_response", "invalid_json", "json_root_must_be_object", "schema_required_field_missing",
    "schema_additional_properties", "schema_invalid_options", "too_many_cards", "option_name_not_allowed",
    "option_order_mismatch", "empty_option_section", "required_location_missing", "required_price_missing",
    "scenario_fact_benefit_missing", "scenario_viewpoint_mismatch", "intro_empty", "missing_note_required",
    "financing_missing_note_required", "final_question_empty", "recipe_cta_mismatch",
    "contact_before_financing_consent", "selected_financing_card_scope_invalid", "section_question_mark",
    "final_question_contract_mismatch", "missing_context_acknowledgement", "duplicate_answer",
    "repeated_identical_benefit", "unknown_option_name", "unknown_number_or_sensitive_claim",
    "internal_or_raw_wire_leak", "unsupported_sensitive_claim", "unsupported_marketing_claim",
}
SAFE_PLANNER_RAW_KEYS = {
    "goal",
    "viewpoint",
    "selected_option_name",
    "requested_facts",
    "constraints_delta",
    "operator_consent",
    "confidence",
}


class ReportError(Exception):
    pass


def parse_ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def fmt_ts(value: Any) -> str:
    parsed = parse_ts(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else str(value or "")


def date_of(value: Any) -> str | None:
    parsed = parse_ts(value)
    return parsed.date().isoformat() if parsed else None


def hash_ref(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.startswith("sha256:"):
        return text
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def ref_suffix(ref: str | None) -> str:
    if not ref:
        return "unknown"
    safe = hash_ref(ref)
    return safe[-12:]


def group_ref(row: dict[str, Any], line_no: int) -> str:
    for key in ("conversation_ref", "session_key_ref"):
        if row.get(key):
            return hash_ref(row[key])
    for key in ("conversation_id", "session_key"):
        if row.get(key):
            return hash_ref(row[key])
    return hash_ref(f"line:{line_no}")


def ref_candidates(row: dict[str, Any], line_no: int) -> set[str]:
    refs: set[str] = {group_ref(row, line_no)}
    for key in ("conversation_ref", "session_key_ref", "conversation_id", "session_key"):
        if row.get(key):
            refs.add(hash_ref(row[key]))
    return refs


def redact(text: Any) -> str:
    out = "" if text is None else str(text)
    out = AUTH_RE.sub("Authorization: [redacted]", out)
    out = EMAIL_RE.sub("[email redacted]", out)
    out = PHONE_RE.sub("[phone redacted]", out)
    out = TOKEN_RE.sub("[token redacted]", out)
    out = LONG_ID_RE.sub("[id redacted]", out)
    return out


def safe_json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def sanitize_error_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    status = str(value.get("status") or "").strip().lower()
    if status not in {"ok", "degraded", "failed"}:
        return None
    codes = [
        str(code)
        for code in (value.get("codes") if isinstance(value.get("codes"), list) else [])
        if str(code) in SAFE_ERROR_SUMMARY_CODES
    ]
    stages = [
        str(stage)
        for stage in (value.get("stages") if isinstance(value.get("stages"), list) else [])
        if str(stage) in SAFE_ERROR_SUMMARY_STAGES
    ]
    if status == "ok" and (codes or stages or bool(value.get("fallback"))):
        return None
    if status in {"degraded", "failed"} and not codes:
        return None
    return {
        "status": status,
        "codes": list(dict.fromkeys(codes))[:8],
        "stages": list(dict.fromkeys(stages))[:4],
        "fallback": bool(value.get("fallback")),
    }


def load_jsonl(path: Path, *, required: bool = True) -> list[dict[str, Any]]:
    if not path.exists():
        if required:
            raise ReportError(f"Файл не найден: {path}")
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    row["_line_no"] = line_no
                    row["_path"] = str(path)
                    rows.append(row)
    except OSError as exc:
        raise ReportError(f"Не удалось прочитать {path}: {exc}") from exc
    return rows


def normalize_runtime_version(value: Any) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in VALID_RUNTIME_VERSIONS else None


def runtime_backfill_path(journal_path: Path, args: argparse.Namespace) -> Path:
    explicit = getattr(args, "runtime_backfill", None)
    if explicit:
        return Path(explicit)
    return journal_path.with_name(DEFAULT_RUNTIME_BACKFILL_NAME)


def load_runtime_backfill(path: Path) -> dict[int, dict[str, str]]:
    if not path.exists():
        return {}
    rows: dict[int, dict[str, str]] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                try:
                    line_no = int(row.get("line_no"))
                except (TypeError, ValueError):
                    continue
                version = normalize_runtime_version(row.get("runtime_version"))
                source = str(row.get("source") or "").strip() or "insufficient_history"
                if version:
                    rows[line_no] = {"runtime_version": version, "runtime_version_source": source}
                elif str(row.get("runtime_version") or "").strip().upper() == "UNKNOWN":
                    rows[line_no] = {"runtime_version": "UNKNOWN", "runtime_version_source": source}
    except OSError:
        return {}
    return rows


def row_runtime_version(row: dict[str, Any], backfill: dict[int, dict[str, str]]) -> tuple[str, str]:
    direct = normalize_runtime_version(row.get("runtime_version"))
    if direct:
        return direct, "existing"
    line_no = int(row.get("_line_no") or 0)
    filled = backfill.get(line_no)
    if filled:
        return filled["runtime_version"], filled["runtime_version_source"]
    return "UNKNOWN", "insufficient_history"


def row_release_id(row: dict[str, Any]) -> str:
    text = str(row.get("release_id") or "").strip()
    if text == "UNKNOWN":
        return text
    if text in {"", ".", ".."} or text.startswith("-") or "/" in text or "\\" in text:
        return "UNKNOWN"
    return text if SAFE_RELEASE_ID_RE.fullmatch(text) else "UNKNOWN"


def canonical_search_blob(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in rows:
        parts.append(str(row.get("text") or ""))
        parts.append(str(row.get("answer_kind") or ""))
        if isinstance(row.get("runtime_summary"), dict):
            parts.append(safe_json_text(sanitize_runtime(row["runtime_summary"])))
    return "\n".join(parts).lower()


def sanitize_runtime(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in SAFE_RUNTIME_KEYS:
        if key in value:
            out[key] = value[key]
    for state_name in ("state_before", "state_after"):
        state = value.get(state_name)
        if isinstance(state, dict):
            out[state_name] = {k: state.get(k) for k in SAFE_STATE_KEYS if k in state}
    return out


def summarize_runtime(value: Any) -> dict[str, Any]:
    rt = sanitize_runtime(value)
    summary: dict[str, Any] = {}
    for key in ("stage", "action", "answer_kind", "quality_blockers", "call_counts"):
        if key in rt:
            summary[key] = rt[key]
    before = rt.get("state_before") if isinstance(rt.get("state_before"), dict) else {}
    after = rt.get("state_after") if isinstance(rt.get("state_after"), dict) else {}
    if before or after:
        summary["pending_followup"] = {"before": before.get("pending_followup"), "after": after.get("pending_followup")}
        for key in ("selected_present", "visible_options_count"):
            if key in after:
                summary[key] = after.get(key)
    return summary


def parse_maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def sanitize_planner(row: dict[str, Any]) -> dict[str, Any]:
    raw = parse_maybe_json(row.get("planner_raw_response"))
    safe_raw: dict[str, Any] = {}
    if isinstance(raw, dict):
        safe_raw = {k: raw.get(k) for k in SAFE_PLANNER_RAW_KEYS if k in raw}
    for key in SAFE_PLANNER_RAW_KEYS:
        if key in row and key not in safe_raw:
            safe_raw[key] = row.get(key)
    return {
        "ts": fmt_ts(row.get("ts")),
        "user_text": redact(row.get("user_text")),
        "action": row.get("action"),
        "target": row.get("target"),
        "search_policy": row.get("search_policy"),
        "final_decision": row.get("final_decision") if isinstance(row.get("final_decision"), dict) else {},
        "planner_raw_response": safe_raw,
        "validation_errors": row.get("canonical_errors") or row.get("validation_errors") or [],
        "canonical_error_codes": row.get("canonical_error_codes") or [],
        "confidence": row.get("confidence"),
    }


def read_planner_rows(log_dir: Path, date: str | None, selected_refs: set[str]) -> list[dict[str, Any]]:
    paths = [log_dir / f"planner_trace-{date}.jsonl"] if date else sorted(log_dir.glob("planner_trace-*.jsonl"))
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(load_jsonl(path, required=False))
    matched = [r for r in rows if ref_candidates(r, int(r.get("_line_no") or 0)) & selected_refs]
    if not matched and date:
        for path in sorted(log_dir.glob("planner_trace-*.jsonl")):
            if path.name == f"planner_trace-{date}.jsonl":
                continue
            for row in load_jsonl(path, required=False):
                if ref_candidates(row, int(row.get("_line_no") or 0)) & selected_refs:
                    matched.append(row)
    matched.sort(key=lambda r: parse_ts(r.get("ts")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    return matched


def nearest_planner(turn: dict[str, Any], planners: list[dict[str, Any]]) -> dict[str, Any] | None:
    turn_ts = parse_ts(turn.get("ts"))
    if not turn_ts:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for planner in planners:
        p_ts = parse_ts(planner.get("ts"))
        if not p_ts:
            continue
        delta = abs((turn_ts - p_ts).total_seconds())
        if delta <= PLANNER_NEAREST_SECONDS and (best is None or delta < best[0]):
            best = (delta, planner)
    return best[1] if best else None


def selected_groups(
    groups: dict[str, list[dict[str, Any]]], queries: list[str], any_query: bool, session_ref: str | None
) -> list[tuple[str, list[dict[str, Any]]]]:
    if session_ref:
        wanted = hash_ref(session_ref)
        return [(ref, rows) for ref, rows in groups.items() if ref == wanted or ref.endswith(session_ref) or ref_suffix(ref) == session_ref]
    out: list[tuple[str, list[dict[str, Any]]]] = []
    lowered = [q.lower() for q in queries]
    for ref, rows in groups.items():
        blob = canonical_search_blob(rows)
        checks = [q in blob for q in lowered]
        if lowered:
            ok = any(checks) if any_query else all(checks)
        else:
            ok = True
        if ok:
            out.append((ref, rows))
    return out


def other_sessions_nearby(selected_ref: str, selected_rows: list[dict[str, Any]], all_groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    times = [parse_ts(r.get("ts")) for r in selected_rows]
    times = [t for t in times if t]
    if not times:
        return []
    start = min(times) - dt.timedelta(seconds=NEARBY_WINDOW_SECONDS)
    end = max(times) + dt.timedelta(seconds=NEARBY_WINDOW_SECONDS)
    date_set = {t.date().isoformat() for t in times}
    nearby: list[dict[str, Any]] = []
    for ref, rows in all_groups.items():
        if ref == selected_ref:
            continue
        row_times = [parse_ts(r.get("ts")) for r in rows]
        row_times = [t for t in row_times if t and t.date().isoformat() in date_set and start <= t <= end]
        if row_times:
            nearby.append({"session_ref_hash": ref_suffix(ref), "event_count": len(row_times), "time_range": [row_times[0].isoformat().replace("+00:00", "Z"), row_times[-1].isoformat().replace("+00:00", "Z")]})
    return sorted(nearby, key=lambda x: x["time_range"][0])


def planner_requested_facts(planner: dict[str, Any] | None) -> list[str]:
    if not planner:
        return []
    raw = sanitize_planner(planner).get("planner_raw_response", {})
    facts = raw.get("requested_facts") if isinstance(raw, dict) else None
    if isinstance(facts, list):
        return [str(x) for x in facts]
    if isinstance(facts, str):
        return [facts]
    return []


def build_findings(session_rows: list[dict[str, Any]], planners: list[dict[str, Any]], nearby: list[dict[str, Any]]) -> list[str]:
    findings: list[str] = []
    if nearby:
        findings.append("different_session_keys_nearby")
    if not planners:
        findings.append("missing_planner_trace")
    phone_seen = False
    quality_seen = False
    runtime_error_seen = False
    operator_mismatch = False
    last_planner: dict[str, Any] | None = None
    for row in sorted(session_rows, key=lambda r: parse_ts(r.get("ts")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)):
        text = str(row.get("text") or "")
        if PHONE_RE.search(text) or "[phone redacted]" in text.lower():
            phone_seen = True
        rt = row.get("runtime_summary")
        if isinstance(rt, dict):
            blockers = rt.get("quality_blockers") or []
            if blockers:
                quality_seen = True
            if "runtime_error" in blockers or rt.get("stage") == "runtime_error":
                runtime_error_seen = True
        near = nearest_planner(row, planners)
        if near:
            last_planner = near
        if row.get("role") == "bot" and "ипотек" in text.lower():
            requested = {x.lower() for x in planner_requested_facts(last_planner)}
            if last_planner and not ({"mortgage", "mortgage_terms"} & requested):
                operator_mismatch = True
    if runtime_error_seen:
        findings.append("runtime_error")
    if quality_seen:
        findings.append("quality_blockers")
    if operator_mismatch:
        findings.append("operator_topic_mismatch")
    if phone_seen:
        findings.append("phone_redacted")
    return findings


def build_report(log_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    journal_path = log_dir / "dialogue_journal.jsonl"
    journal_rows = load_jsonl(journal_path)
    backfill_path = runtime_backfill_path(journal_path, args)
    runtime_backfill = load_runtime_backfill(backfill_path)
    if args.date:
        journal_rows = [r for r in journal_rows if date_of(r.get("ts")) == args.date]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in journal_rows:
        groups.setdefault(group_ref(row, int(row.get("_line_no") or 0)), []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: parse_ts(r.get("ts")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    matches = selected_groups(groups, args.q, args.any, args.session)
    if not matches:
        return {"reports": [], "source": {"journal": str(journal_path), "log_dir": str(log_dir), "runtime_backfill": str(backfill_path) if backfill_path.exists() else None}, "exit_code": 1}
    matches.sort(key=lambda item: parse_ts(item[1][-1].get("ts")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    reports = []
    for ref, rows in matches[: args.limit]:
        selected_refs = set().union(*(ref_candidates(r, int(r.get("_line_no") or 0)) for r in rows))
        planner_date = args.date or dt.datetime.now(dt.timezone.utc).date().isoformat()
        planners = read_planner_rows(log_dir, planner_date, selected_refs)
        nearby = other_sessions_nearby(ref, rows, groups)
        times = [parse_ts(r.get("ts")) for r in rows]
        times = [t for t in times if t]
        timeline: list[dict[str, Any]] = []
        for idx, row in enumerate(rows, 1):
            near = nearest_planner(row, planners)
            runtime_version, runtime_version_source = row_runtime_version(row, runtime_backfill)
            item = {
                "n": idx,
                "ts": fmt_ts(row.get("ts")),
                "role": row.get("role"),
                "runtime_version": runtime_version,
                "runtime_version_source": runtime_version_source,
                "release_id": row_release_id(row),
                "text": redact(row.get("text")) if args.show_text else "[скрыто; включите --show-text]",
                "answer_kind": row.get("answer_kind"),
                "runtime_summary": summarize_runtime(row.get("runtime_summary")),
                "prompt_provenance": sanitize_any_prompt_provenance(row.get("prompt_provenance")),
                "execution_path": sanitize_any_execution_path(row.get("execution_path")),
                "error_summary": sanitize_error_summary(row.get("error_summary")),
                "planner_nearest": sanitize_planner(near) if near else None,
            }
            timeline.append(item)
        reports.append(
            {
                "session_ref_hash": ref_suffix(ref),
                "time_range": [times[0].isoformat().replace("+00:00", "Z"), times[-1].isoformat().replace("+00:00", "Z")] if times else [],
                "event_count": len(rows),
                "other_sessions_nearby": nearby,
                "timeline": timeline,
                "planner_trace": [sanitize_planner(p) for p in planners],
                "findings": build_findings(rows, planners, nearby),
            }
        )
    return {"reports": reports, "source": {"journal": str(journal_path), "log_dir": str(log_dir), "runtime_backfill": str(backfill_path) if backfill_path.exists() else None}, "exit_code": 0}


def print_human(report: dict[str, Any]) -> None:
    if not report.get("reports"):
        print("Диалог не найден")
        return
    print("# Пошаговый отчёт диалога")
    print(f"Источник: {report.get('source', {}).get('log_dir')}")
    for r_i, item in enumerate(report["reports"], 1):
        print(f"\n## Отчёт {r_i}")
        print(f"Сессия: session_ref_hash={item['session_ref_hash']}")
        print(f"Время UTC: {' — '.join(item.get('time_range') or [])}")
        print(f"Событий: {item['event_count']}")
        print("Другие сессии рядом:")
        if item["other_sessions_nearby"]:
            for other in item["other_sessions_nearby"]:
                print(f"- session_ref_hash={other['session_ref_hash']}, событий={other['event_count']}, время={' — '.join(other['time_range'])}")
        else:
            print("- не найдены")
        print("\n### Таймлайн")
        for turn in item["timeline"]:
            print(f"{turn['n']}. {turn['ts']} · {turn.get('role') or 'unknown'} · runtime_version={turn.get('runtime_version') or 'UNKNOWN'} · runtime_version_source={turn.get('runtime_version_source') or 'insufficient_history'} · release_id={turn.get('release_id') or 'UNKNOWN'} · answer_kind={turn.get('answer_kind') or '-'}")
            print(f"   Текст: {turn['text']}")
            if turn.get("runtime_summary"):
                print(f"   Runtime: {safe_json_text(turn['runtime_summary'])}")
            if turn.get("prompt_provenance"):
                print(f"   Prompts: {_format_prompt_provenance(turn['prompt_provenance'])}")
            if turn.get("execution_path"):
                print(f"   Execution path: {safe_json_text(turn['execution_path'])}")
            if turn.get("error_summary"):
                print(f"   Errors: {safe_json_text(turn['error_summary'])}")
            if turn.get("planner_nearest"):
                p = turn["planner_nearest"]
                final = p.get("final_decision") or {}
                print(
                    "   Ближайший planner: "
                    f"{p.get('ts')} · action={p.get('action')} · target={p.get('target')} · "
                    f"search_policy={p.get('search_policy')} · final={safe_json_text(final)}"
                )
                raw = p.get("planner_raw_response") or {}
                if raw:
                    print(f"   Planner safe fields: {safe_json_text(raw)}")
                if p.get("validation_errors") or p.get("canonical_error_codes"):
                    print(f"   Валидация: errors={safe_json_text(p.get('validation_errors'))}, codes={safe_json_text(p.get('canonical_error_codes'))}")
        print("\n### Findings")
        if item["findings"]:
            for finding in item["findings"]:
                print(f"- {finding}")
        else:
            print("- явных детерминированных находок нет")


def _format_prompt_provenance(value: dict[str, Any]) -> str:
    safe = sanitize_any_prompt_provenance(value)
    if not safe:
        return "UNKNOWN"
    prefix = str(safe.get("set_sha256") or "")[:12]
    parts = []
    for prompt in safe.get("prompts") or []:
        parts.append(f"{prompt.get('stage')}={prompt.get('prompt_id')}/{prompt.get('source')}")
    return f"{safe.get('prompt_set_id')} sha={prefix} coverage={safe.get('coverage')} " + "; ".join(parts)


def run_prod(args: argparse.Namespace) -> int:
    remote_args = ["python3", "-", "--log-dir", PROD_LOG_DIR, "--limit", str(args.limit)]
    if args.date:
        remote_args += ["--date", args.date]
    if args.session:
        remote_args += ["--session", args.session]
    for q in args.q:
        remote_args += ["--q", q]
    if args.any:
        remote_args.append("--any")
    if args.json:
        remote_args.append("--json")
    if args.show_text:
        remote_args.append("--show-text")
    if getattr(args, "runtime_backfill", None):
        remote_args += ["--runtime-backfill", str(args.runtime_backfill)]
    cmd = ["ssh", "-p", PROD_PORT, PROD_HOST, " ".join(shlex.quote(x) for x in remote_args)]
    script = Path(__file__).read_text(encoding="utf-8")
    completed = subprocess.run(cmd, input=script, text=True)
    return completed.returncode


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Безопасный read-only отчёт по dialogue_journal и planner_trace.")
    parser.add_argument("--prod", action="store_true", help="Запустить такой же отчёт на VPS в /home/neiro/novostroy-bot/logs через safe self-SSH.")
    parser.add_argument("--q", action="append", default=[], help="Поисковая фраза; можно повторять. По умолчанию все фразы должны найтись в одной сессии.")
    parser.add_argument("--any", action="store_true", help="Достаточно совпадения любой --q фразы.")
    parser.add_argument("--date", help="Дата UTC YYYY-MM-DD. Если указана, ограничивает journal; для planner по умолчанию берётся текущая UTC-дата.")
    parser.add_argument("--session", help="Opaque session ref/hash suffix, чтобы обойти поиск по фразам.")
    parser.add_argument("--limit", type=int, default=1, help="Сколько сессионных отчётов вывести.")
    parser.add_argument("--json", action="store_true", help="Машинный JSON вместо человекочитаемого вывода.")
    parser.add_argument("--show-text", action="store_true", help="Показать текст turn после редактирования телефонов/email/token/id.")
    parser.add_argument("--runtime-backfill", type=Path, help="Sidecar JSONL with safe runtime_version backfill keyed by journal line_no.")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.limit < 1:
        print("Ошибка: --limit должен быть больше нуля", file=sys.stderr)
        return 2
    if args.prod:
        return run_prod(args)
    try:
        report = build_report(args.log_dir, args)
    except ReportError as exc:
        print(f"Ошибка входных файлов: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)
    return int(report.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
