#!/usr/bin/env python3
"""Safe read-only diagnostics for one NMBot Overmind gateway task.

The CLI fetches the task status/result pair and correlates it with the local
bot error-event journal. It intentionally prints only bounded diagnostic fields:
no prompts, request payloads, model text, contacts, tokens or auth headers.

Exit behavior:
- 0: task diagnostics were fetched and printed, even when the task itself failed;
- 2: local configuration/auth is missing;
- 3: transport/auth/schema failure while fetching/parsing diagnostics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


REPO = Path(__file__).resolve().parent.parent
DEFAULT_OVERMIND_URL = "https://overmind.aiaxel.ru"
SAFE_EVENT_FIELDS = ("error_type", "stage", "ts", "timestamp", "task_status", "session_key_ref", "conversation_ref")
SAFE_TEXT_MAX = 4000
SAFE_QUERY_MAX = 240
DEFAULT_LIMIT = 50
EVIDENCE_CHAIN_SCHEMA = "nmbot.evidence_chain.v1"
EVIDENCE_CHILD_PROBE_LIMIT = 6
EVIDENCE_CHILD_TOLERANCE_SECONDS = 5
EVIDENCE_CONTACT_ANSWER_KINDS = {
    "operator_offer",
    "collect_contact_name",
    "collect_contact_phone",
    "callback_queued",
    "operator_declined",
}
EVIDENCE_CARD_FIELDS = (
    "id", "name", "alias", "title", "price", "price_min", "price_from", "min_price",
    "delivered", "readiness", "ready", "finishing", "area", "min_area", "max_area", "metro", "school",
    "kindergarten", "park", "park_near", "water", "water_near", "yard_without_cars", "district", "location", "type_object",
)
EVIDENCE_NORMALIZED_FIELDS = (
    "name", "entity_id", "location", "price", "price_min", "finishing", "area", "ready",
    "metro", "developer", "property_class", "infrastructure",
)
PLANNER_SEMANTIC_ALLOWLIST = {
    "user_goal",
    "selected_reference",
    "resolved_subject",
    "resolved_intent",
    "requested_facts",
    "facts_needed",
    "domain_relation",
    "focus_action",
    "response_viewpoint",
    "followup_outcome",
    "requires_enrichment",
    "clarification",
    "confidence",
}
PLANNER_SAFE_FIELDS = {
    "ts",
    "session_key_ref",
    "conversation_ref",
    "user_text",
    "user_text_truncated",
    "action",
    "dialog_action",
    "intent",
    "target",
    "search_policy",
    "scope",
    "confidence",
    "canonical_errors",
    "canonical_error_codes",
    "canonical_valid",
    "fallback_used",
    "repair_attempted",
    "repair_applied",
    "final_decision",
    "planner_exception_code",
    "raw_response_present",
    "planner_raw_response_truncated",
}
JOURNAL_SAFE_FIELDS = {
    "ts",
    "role",
    "session_key_ref",
    "conversation_ref",
    "event_type",
    "text",
    "answer_kind",
    "offer_type",
    "response_composer",
}
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?7|8)[\s().-]*\d(?:[\s().-]*\d){9,10}(?!\w)")
_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)
_CONTACT_VALUE_RE = re.compile(r"(?i)\b(?:telegram|телеграм|tg|whatsapp|ватсап|wa)\s*[:=]?\s*@?[A-Z0-9_.-]{3,32}\b")
_NAME_VALUE_RE = re.compile(r"(?i)\b(?:имя|name)\s*[:=]\s*[^,;\n]{1,80}")
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>()]+")
SENSITIVE_KEY_PARTS = (
    "error",
    "auth",
    "authorization",
    "token",
    "secret",
    "key",
    "prompt",
    "request_data",
    "payload",
    "response",
    "content",
    "text",
    "phone",
    "name",
)


class DiagnosticError(RuntimeError):
    """Expected safe diagnostic failure with a bounded public code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GatewayConfig:
    base_url: str
    token: str


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read dotenv values for internal use only; callers must not print values."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_config(*, env: Mapping[str, str] | None = None, repo: Path = REPO) -> GatewayConfig:
    """Load gateway URL/token with the runtime auth precedence.

    Token precedence mirrors the bot runtime: OVERMIND_TOKEN first, then
    GATEWAY_POLL_TOKEN. The default URL is the production Overmind endpoint.
    """
    merged: dict[str, str] = {**_read_dotenv(repo / ".env")}
    merged.update(dict(os.environ if env is None else env))
    token = merged.get("OVERMIND_TOKEN") or merged.get("GATEWAY_POLL_TOKEN") or ""
    base_url = (merged.get("OVERMIND_URL") or DEFAULT_OVERMIND_URL).rstrip("/")
    if not token:
        raise DiagnosticError("missing_gateway_token")
    return GatewayConfig(base_url=base_url, token=token)


def _decode_json_response(resp: Any) -> dict[str, Any]:
    raw = resp.read()
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    try:
        payload = json.loads(text or "{}")
    except Exception as exc:  # noqa: BLE001 - converted to bounded public code.
        raise DiagnosticError(f"json_parse_failed:{type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise DiagnosticError("unexpected_json_shape")
    return payload


def fetch_task_json(
    config: GatewayConfig,
    task_id: str,
    kind: str,
    *,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    timeout: int = 15,
) -> dict[str, Any]:
    if kind not in {"status", "result"}:
        raise ValueError(f"unsupported task endpoint: {kind}")
    url = f"{config.base_url}/api/v1/tasks/api/{task_id}/{kind}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {config.token}", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - configured internal diagnostic endpoint.
            return _decode_json_response(resp)
    except urllib.error.HTTPError as exc:
        raise DiagnosticError(f"http_{exc.code}:{kind}") from exc
    except DiagnosticError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to bounded public code.
        raise DiagnosticError(f"transport_failed:{type(exc).__name__}:{kind}") from exc


def _json_loads(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _safe_text(value: Any, *, limit: int = SAFE_TEXT_MAX) -> str:
    text = str(value or "")
    text = _PHONE_RE.sub("[phone redacted]", text)
    text = _EMAIL_RE.sub("[email redacted]", text)
    text = _CONTACT_VALUE_RE.sub("[contact redacted]", text)
    text = _NAME_VALUE_RE.sub("[name redacted]", text)
    text = _URL_RE.sub("[link redacted]", text)
    return text[:limit]


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_or_none(value: Any) -> str | None:
    parsed = _parse_ts(value)
    if parsed is None:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _hash_session_ref(value: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith("sha256:"):
        return raw
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_jsonl(path: Path, *, limit: int = 0) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        row = _json_loads(line.strip())
        if not row:
            continue
        rows.append(row)
        if limit and len(rows) >= limit:
            break
    return rows


def _in_window(row: dict[str, Any], *, from_ts: datetime | None, to_ts: datetime | None) -> bool:
    ts = _parse_ts(row.get("ts") or row.get("timestamp") or row.get("completed_at"))
    if ts is None:
        return from_ts is None and to_ts is None
    if from_ts and ts < from_ts:
        return False
    if to_ts and ts > to_ts:
        return False
    return True


def _matches_session(row: dict[str, Any], session_ref: str | None) -> bool:
    if not session_ref:
        return True
    return row.get("session_key_ref") == session_ref or row.get("conversation_ref") == session_ref


def _matches_query(row: dict[str, Any], query: str | None, fields: tuple[str, ...]) -> bool:
    if not query:
        return True
    needle = query[:SAFE_QUERY_MAX].casefold()
    haystack = "\n".join(str(row.get(field) or "") for field in fields).casefold()
    return needle in haystack


def filter_rows(
    rows: list[dict[str, Any]],
    *,
    session_ref: str | None = None,
    query: str | None = None,
    query_fields: tuple[str, ...] = ("text", "user_text"),
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not _matches_session(row, session_ref):
            continue
        if not _in_window(row, from_ts=from_ts, to_ts=to_ts):
            continue
        if not _matches_query(row, query, query_fields):
            continue
        out.append(row)
        if limit and len(out) >= limit:
            break
    return out


def _safe_schema_keys(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    keys: list[str] = []
    for key in sorted(str(k) for k in value.keys()):
        lowered = key.lower()
        if any(part in lowered for part in SENSITIVE_KEY_PARTS):
            continue
        keys.append(key[:64])
        if len(keys) >= 20:
            break
    return keys


def _safe_event(row: dict[str, Any]) -> dict[str, Any]:
    event: dict[str, Any] = {}
    for field in SAFE_EVENT_FIELDS:
        if field in row and row[field] is not None:
            value = row[field]
            if isinstance(value, (str, int, float, bool)):
                event[field] = str(value)[:120]
    event["matched"] = True
    return event


def correlate_error_events(task_id: str, *, date: str, logs_dir: Path) -> list[dict[str, Any]]:
    path = logs_dir / f"bot_error_events-{date}.jsonl"
    if not path.exists():
        return []
    matches: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        row = _json_loads(line.strip())
        if not row:
            continue
        candidate_ids = {str(row.get("task_id") or ""), str(row.get("gateway_task_id") or "")}
        task = row.get("task")
        if isinstance(task, dict):
            candidate_ids.add(str(task.get("id") or task.get("task_id") or ""))
        if task_id in candidate_ids:
            matches.append(_safe_event(row))
            if len(matches) >= 5:
                break
    return matches


def correlate_error_event_rows(task_id: str, *, date: str, logs_dir: Path) -> list[dict[str, Any]]:
    path = logs_dir / f"bot_error_events-{date}.jsonl"
    rows = load_jsonl(path)
    matches: list[dict[str, Any]] = []
    for row in rows:
        candidate_ids = {str(row.get("task_id") or ""), str(row.get("gateway_task_id") or "")}
        task = row.get("task")
        if isinstance(task, dict):
            candidate_ids.add(str(task.get("id") or task.get("task_id") or ""))
        if task_id in candidate_ids:
            matches.append(row)
    return matches


def _derive_session_ref(rows: list[dict[str, Any]]) -> str | None:
    refs = []
    for row in rows:
        for key in ("session_key_ref", "conversation_ref"):
            value = row.get(key)
            if isinstance(value, str) and value.startswith("sha256:"):
                refs.append(value)
    unique = sorted(set(refs))
    return unique[0] if len(unique) == 1 else None


def _derive_window(rows: list[dict[str, Any]], *, pad_seconds: int = 180) -> tuple[datetime | None, datetime | None]:
    stamps = [_parse_ts(row.get("ts") or row.get("timestamp") or row.get("completed_at")) for row in rows]
    stamps = [stamp for stamp in stamps if stamp is not None]
    if not stamps:
        return None, None
    return min(stamps) - timedelta(seconds=pad_seconds), max(stamps) + timedelta(seconds=pad_seconds)


def _sanitize_scalar(value: Any, *, limit: int = 500) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return _safe_text(value, limit=limit)


def _sanitize_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return "[truncated]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key in sorted(value.keys(), key=str)[:20]:
            lowered = str(key).lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                continue
            out[str(key)[:64]] = _sanitize_json_value(value[key], depth=depth + 1)
        return out
    if isinstance(value, list):
        return [_sanitize_json_value(item, depth=depth + 1) for item in value[:20]]
    return _sanitize_scalar(value)


def safe_planner_semantic(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("planner_raw_response")
    if not isinstance(raw, str) or not raw.strip():
        return {"raw_response_present": bool(row.get("raw_response_present")), "parsed": False, "fields": {}}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {"raw_response_present": True, "parsed": False, "fields": {}}
    if not isinstance(parsed, dict):
        return {"raw_response_present": True, "parsed": False, "fields": {}}
    fields = {
        key: _sanitize_json_value(parsed[key])
        for key in sorted(PLANNER_SEMANTIC_ALLOWLIST)
        if key in parsed
    }
    return {"raw_response_present": True, "parsed": True, "fields": fields}


def safe_planner_summary(row: dict[str, Any]) -> dict[str, Any]:
    summary = {key: row[key] for key in PLANNER_SAFE_FIELDS if key in row and key != "planner_raw_response"}
    if "user_text" in summary:
        summary["user_text"] = _safe_text(summary["user_text"])
    summary["semantic_output"] = safe_planner_semantic(row)
    return summary


def safe_dialogue_summary(row: dict[str, Any], *, redact_contact_turn: bool = False) -> dict[str, Any]:
    summary = {key: row[key] for key in JOURNAL_SAFE_FIELDS if key in row}
    if "text" in summary:
        summary["text"] = "[contact redacted]" if redact_contact_turn else _safe_text(summary["text"])
        summary["text_truncated"] = len(str(row.get("text") or "")) > SAFE_TEXT_MAX
    return summary


def _event(ts: Any, layer: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"ts": _iso_or_none(ts) or str(ts or ""), "layer": layer, **payload}


def build_timeline(
    *,
    dialogue_rows: list[dict[str, Any]],
    planner_rows: list[dict[str, Any]],
    gateway_diagnosis: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    # A name may be a bare word, so regex-only redaction is insufficient.
    # Use the journal's answer-kind boundary and omit contact capture turns.
    contact_capture_active = False
    for row in dialogue_rows:
        role = str(row.get("role") or "")
        if role == "user":
            safe = safe_dialogue_summary(row, redact_contact_turn=contact_capture_active)
            timeline.append(_event(row.get("ts"), "jivo_input", safe))
        elif role == "bot":
            safe = safe_dialogue_summary(row, redact_contact_turn=contact_capture_active)
            timeline.append(_event(row.get("ts"), "response", safe))
            if isinstance(row.get("response_composer"), dict):
                timeline.append(_event(row.get("ts"), "composer", {"response_composer": safe.get("response_composer")}))
            answer_kind = str(row.get("answer_kind") or "")
            if answer_kind in {"operator_offer", "collect_contact_name", "collect_contact_phone"}:
                contact_capture_active = True
            elif answer_kind in {"callback_queued", "operator_declined"}:
                contact_capture_active = False
    for row in planner_rows:
        safe = safe_planner_summary(row)
        timeline.append(_event(row.get("ts"), "planner", safe))
        if "final_decision" in row or "canonical_errors" in row or "canonical_error_codes" in row:
            timeline.append(
                _event(
                    row.get("ts"),
                    "canonical_decision",
                    {
                        "session_key_ref": row.get("session_key_ref"),
                        "conversation_ref": row.get("conversation_ref"),
                        "final_decision": row.get("final_decision"),
                        "canonical_valid": row.get("canonical_valid"),
                        "canonical_error_codes": row.get("canonical_error_codes") or [],
                    },
                )
            )
    if gateway_diagnosis:
        timeline.append(
            _event(
                gateway_diagnosis.get("completed_at"),
                "gateway",
                {
                    "task_id": gateway_diagnosis.get("task_id"),
                    "status": gateway_diagnosis.get("status"),
                    "result_present": gateway_diagnosis.get("result_present"),
                    "error_code": gateway_diagnosis.get("error_code"),
                    "category": gateway_diagnosis.get("category"),
                    "downstream_layer": gateway_diagnosis.get("layer"),
                    "event_correlation": gateway_diagnosis.get("event_correlation"),
                },
            )
        )
    layer_order = {"jivo_input": 0, "planner": 1, "canonical_decision": 2, "gateway": 3, "response": 4, "composer": 5}
    return sorted(timeline, key=lambda item: (_parse_ts(item.get("ts")) or datetime.max.replace(tzinfo=timezone.utc), layer_order.get(str(item.get("layer")), 99)))


def build_verdict(timeline: list[dict[str, Any]], gateway_diagnosis: dict[str, Any] | None = None) -> dict[str, Any]:
    # An explicit failed task is the most concrete execution failure.
    for event in timeline:
        if event.get("layer") == "gateway" and event.get("status") == "failed":
            return {
                "stage": "gateway",
                "classification": event.get("downstream_layer") or "unknown",
                "evidence": {"error_code": event.get("error_code"), "category": event.get("category")},
            }

    # A canonical rejection matters only when no later concrete gateway failure
    # exists. Some canonical diagnostics are advisory while a final decision
    # still executes.
    for event in timeline:
        if event.get("layer") == "planner" and event.get("canonical_valid") is False:
            return {"stage": "planner", "classification": "canonical_invalid", "evidence": {"canonical_error_codes": event.get("canonical_error_codes") or []}}

    for event in timeline:
        layer = event.get("layer")
        if layer == "composer":
            composer = event.get("response_composer") if isinstance(event.get("response_composer"), dict) else {}
            if composer and composer.get("composer_used") is False:
                return {"stage": "composer", "classification": "composer_fallback", "evidence": {"fallback_reason": composer.get("fallback_reason")}}
    if gateway_diagnosis and gateway_diagnosis.get("status") in {"completed", "success", "succeeded"}:
        return {"stage": "none", "classification": "ok", "evidence": {"task_id": gateway_diagnosis.get("task_id")}}
    return {"stage": "unknown", "classification": "unknown", "evidence": {}}


def _planner_path(logs_dir: Path, date: str) -> Path:
    return logs_dir / f"planner_trace-{date}.jsonl"


def _dialogue_path(logs_dir: Path) -> Path:
    return logs_dir / "dialogue_journal.jsonl"


def build_scenario_report(
    *,
    task_id: str | None = None,
    session_ref: str | None = None,
    query: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    date: str,
    logs_dir: Path,
    gateway_diagnosis: dict[str, Any] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    if not task_id and not session_ref and not query:
        raise DiagnosticError("scenario_requires_task_session_or_query")
    task_rows = correlate_error_event_rows(task_id, date=date, logs_dir=logs_dir) if task_id else []
    effective_session_ref = session_ref or _derive_session_ref(task_rows)
    derived_from_ts, derived_to_ts = _derive_window(task_rows)
    effective_from = from_ts or derived_from_ts
    effective_to = to_ts or derived_to_ts
    dialogue_rows = filter_rows(
        load_jsonl(_dialogue_path(logs_dir)),
        session_ref=effective_session_ref,
        query=query,
        query_fields=("text",),
        from_ts=effective_from,
        to_ts=effective_to,
        limit=limit,
    )
    planner_rows = filter_rows(
        load_jsonl(_planner_path(logs_dir, date)),
        session_ref=effective_session_ref,
        query=query,
        query_fields=("user_text", "planner_raw_response"),
        from_ts=effective_from,
        to_ts=effective_to,
        limit=limit,
    )
    timeline = build_timeline(dialogue_rows=dialogue_rows, planner_rows=planner_rows, gateway_diagnosis=gateway_diagnosis)
    return {
        "scenario": {
            "task_id": str(task_id) if task_id else None,
            "session_ref": effective_session_ref,
            "time_window": {"from": _iso_or_none(effective_from), "to": _iso_or_none(effective_to)},
            "query_present": bool(query),
            "task_event_count": len(task_rows),
            "note": "Read-only trace. Raw secrets, prompts, payloads, contacts and arbitrary model text are excluded.",
        },
        "timeline": timeline[:limit],
        "verdict": build_verdict(timeline, gateway_diagnosis=gateway_diagnosis),
    }


def _flatten_public_evidence(payload: dict[str, Any]) -> str:
    """Collect diagnostic text internally for classification; never print it."""
    parts: list[str] = []
    for key in ("error", "error_message", "message", "detail", "status"):
        value = payload.get(key)
        if isinstance(value, str):
            parts.append(value)
    result = payload.get("result")
    if isinstance(result, dict):
        for key in ("error", "error_message", "message", "detail", "provider", "model"):
            value = result.get(key)
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts).lower()


def classify_task(status_payload: dict[str, Any], result_payload: dict[str, Any]) -> dict[str, str]:
    raw_error = result_payload.get("error_message") or status_payload.get("error_message")
    evidence = _flatten_public_evidence({**status_payload, **result_payload})
    status = str(result_payload.get("status") or status_payload.get("status") or "unknown").lower()

    if raw_error == "'data'":
        return {
            "error_code": "missing_response_field",
            "category": "downstream_contract",
            "layer": "unknown_downstream",
        }
    if "mcp" in evidence or "novostroym" in evidence:
        return {"error_code": "explicit_mcp_error", "category": "mcp_error", "layer": "mcp"}
    if any(marker in evidence for marker in ("openrouter", "provider", "invalid_argument", "gemini", "model")):
        return {"error_code": "explicit_provider_error", "category": "provider_error", "layer": "provider"}
    if status == "failed":
        return {"error_code": "gateway_task_failed", "category": "task_failed", "layer": "unknown_downstream"}
    if status in {"completed", "success", "succeeded"}:
        return {"error_code": "none", "category": "ok", "layer": "none"}
    return {"error_code": "task_not_terminal", "category": "pending_or_unknown", "layer": "gateway"}


def build_diagnosis(
    task_id: str,
    status_payload: dict[str, Any],
    result_payload: dict[str, Any],
    *,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result_value = result_payload.get("result")
    status = result_payload.get("status") or status_payload.get("status") or "unknown"
    completed_at = result_payload.get("completed_at") or status_payload.get("completed_at")
    classification = classify_task(status_payload, result_payload)
    return {
        "task_id": str(task_id),
        "status": str(status),
        "completed_at": completed_at if isinstance(completed_at, str) else None,
        "result_present": result_value is not None,
        **classification,
        "event_correlation": {
            "matched_count": len(events or []),
            "events": events or [],
        },
        "schema": {
            "status_keys": _safe_schema_keys(status_payload),
            "result_keys": _safe_schema_keys(result_payload),
            "nested_result_keys": _safe_schema_keys(result_value),
        },
        "note": "Sensitive diagnostic values are excluded. Layer is explicit-evidence only.",
    }


def run_diagnostic(
    task_id: str,
    *,
    date: str,
    repo: Path = REPO,
    logs_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    config = load_config(env=env, repo=repo)
    status_payload = fetch_task_json(config, task_id, "status", urlopen=urlopen)
    result_payload = fetch_task_json(config, task_id, "result", urlopen=urlopen)
    effective_logs_dir = logs_dir or (repo / "logs")
    events = correlate_error_events(task_id, date=date, logs_dir=effective_logs_dir)
    return build_diagnosis(task_id, status_payload, result_payload, events=events)


def _turn_trace_ref(row: Mapping[str, Any]) -> str | None:
    meta = row.get("meta") if isinstance(row.get("meta"), Mapping) else {}
    value = row.get("trace_ref") or meta.get("trace_ref")
    return str(value).strip() if isinstance(value, str) and value.strip() else None


def _bounded_card(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project only known search-card fields; links and arbitrary result text never cross."""
    return {key: _sanitize_json_value(raw[key]) for key in EVIDENCE_CARD_FIELDS if key in raw}


def _card_key(card: Mapping[str, Any]) -> tuple[str, str] | None:
    for key in ("id", "name", "alias", "title"):
        value = card.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return key, str(value).strip().casefold()
    return None


def _card_tokens(card: Mapping[str, Any]) -> set[str]:
    return {str(card[key]).strip().casefold() for key in ("id", "name", "alias", "title") if isinstance(card.get(key), (str, int)) and str(card[key]).strip()}


def _card_identity_value(card: Mapping[str, Any], field: str) -> str | None:
    value = card.get(field)
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value).strip().casefold()
    return None


def _card_name_tokens(card: Mapping[str, Any]) -> set[str]:
    return {
        value
        for field in ("name", "alias", "title")
        if (value := _card_identity_value(card, field)) is not None
    }


def _matching_primary_index(candidate: Mapping[str, Any], primary_cards: list[Mapping[str, Any]]) -> int | None:
    """Return one unambiguous owner; explicit IDs never fall back to a name."""
    candidate_id = _card_identity_value(candidate, "id")
    candidate_names = _card_name_tokens(candidate)
    if candidate_id is not None:
        id_matches = [
            index
            for index, primary in enumerate(primary_cards)
            if _card_identity_value(primary, "id") == candidate_id
        ]
        if len(id_matches) != 1:
            return None
        primary_names = _card_name_tokens(primary_cards[id_matches[0]])
        if candidate_names and primary_names and not (candidate_names & primary_names):
            return None
        return id_matches[0]
    if not candidate_names:
        return None
    name_matches = [
        index
        for index, primary in enumerate(primary_cards)
        if candidate_names & _card_name_tokens(primary)
    ]
    return name_matches[0] if len(name_matches) == 1 else None


def _extract_cards(value: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    """Find bounded card dictionaries in known result containers, never exposing the container."""
    if depth > 4:
        return []
    if isinstance(value, list):
        cards: list[dict[str, Any]] = []
        for item in value[:20]:
            if isinstance(item, Mapping) and _card_key(item):
                cards.append(dict(item))
            elif isinstance(item, (Mapping, list)):
                cards.extend(_extract_cards(item, depth=depth + 1))
        return cards[:20]
    if not isinstance(value, Mapping):
        return []
    for key in ("facts", "cards", "options", "results", "data", "items", "near"):
        nested = value.get(key)
        if isinstance(nested, list):
            cards = _extract_cards(nested, depth=depth + 1)
            if cards:
                return cards
        if isinstance(nested, Mapping):
            cards = _extract_cards(nested, depth=depth + 1)
            if cards:
                return cards
    return []


def _result_cards(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if isinstance(result, Mapping):
        response = result.get("response")
        if isinstance(response, str) and response.strip():
            try:
                decoded = json.loads(response)
            except (json.JSONDecodeError, TypeError):
                decoded = None
            if isinstance(decoded, (Mapping, list)):
                cards = _extract_cards(decoded)
                if cards:
                    return cards
    return _extract_cards(result)


def _normalized_card(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        from nmbot_v2.card_normalizer import normalize_card
        value = normalize_card(raw)
        data = value.to_dict() if hasattr(value, "to_dict") else dict(value.__dict__)
        return dict(data)
    except Exception:  # optional evidence enrichment must not make diagnosis fail
        return None


def _bounded_normalized_card(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {key: _sanitize_json_value(value[key]) for key in EVIDENCE_NORMALIZED_FIELDS if key in value}


def _claims_from_public_text(text: str) -> dict[str, bool]:
    lowered = text.casefold()
    patterns = {
        "price": r"(?:цен[аы]|стоимост|₽|руб)", "delivered": r"(?:сдан|готов(?:ность|ый|а)?)",
        "finishing": r"отделк", "area": r"(?:площад|м²|кв\.?\s*м)", "metro": r"\bметро\b",
        "school": r"школ", "kindergarten": r"(?:детск\w* сад|садик)", "park": r"парк",
        "water": r"(?:вод(?:а|ы|о[её]м)|река|озер|набережн)", "yard_without_cars": r"двор без машин",
    }
    return {name: bool(re.search(pattern, lowered)) for name, pattern in patterns.items()}


def _claim_segments(text: str, cards: list[Mapping[str, Any]]) -> dict[str, str]:
    """Bound each card to its last named section in a multi-card response."""
    lowered = text.casefold()
    positions: list[tuple[int, str]] = []
    for card in cards:
        key = _card_key(card)
        names = [str(card[field]).strip().casefold() for field in ("name", "alias", "title") if isinstance(card.get(field), str) and str(card[field]).strip()]
        found = max((lowered.rfind(name) for name in names), default=-1)
        if key and found >= 0:
            positions.append((found, key[1]))
    positions.sort()
    segments: dict[str, str] = {}
    for index, (start, token) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(text)
        segments[token] = text[start:end]
    return segments


def _claims_for_card(text: str, raw: Mapping[str, Any], *, card_count: int, segments: Mapping[str, str]) -> dict[str, bool]:
    """Avoid attributing another option's claims to the current card."""
    if card_count <= 1:
        segment = text
    else:
        key = _card_key(raw)
        segment = segments.get(key[1], "") if key else ""
    claim_text = segment
    for field in ("name", "alias", "title"):
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            claim_text = re.sub(re.escape(value), "", claim_text, count=1, flags=re.I)
    claims = _claims_from_public_text(claim_text) if claim_text else {}
    location = raw.get("location")
    if isinstance(location, str) and location.strip():
        words = re.findall(r"[0-9a-zа-яё]+", location.casefold())
        stems = [word[:6] if len(word) > 6 else word for word in words if len(word) >= 4]
        claims["location"] = bool(stems) and all(stem in segment.casefold() for stem in stems)
    return claims


def _evidence_has(card: Mapping[str, Any], normalized: Mapping[str, Any] | None, field: str) -> bool:
    aliases = {
        "delivered": ("delivered", "readiness", "ready"),
        "price": ("price", "price_min", "price_from", "min_price"),
        "area": ("area", "min_area", "max_area"),
        "park": ("park", "park_near"),
        "water": ("water", "water_near"),
    }
    keys = aliases.get(field, (field,))
    return any(card.get(key) not in (None, "", [], {}) for key in keys) or bool(normalized and any(normalized.get(key) not in (None, "", [], {}) for key in keys))


def _attempt_task_ids(summary: Mapping[str, Any]) -> list[str]:
    details = summary.get("gateway_attempt_details")
    if not isinstance(details, list):
        return []
    out: list[str] = []
    for item in details[:5]:
        if isinstance(item, Mapping) and isinstance(item.get("gateway_task_id"), (str, int)):
            task_id = str(item["gateway_task_id"]).strip()
            if task_id and task_id not in out:
                out.append(task_id)
    return out


def _task_timestamp(payload: Mapping[str, Any]) -> datetime | None:
    return _parse_ts(payload.get("completed_at") or payload.get("updated_at") or payload.get("created_at"))


def build_evidence_chain_report(
    trace_ref: str, *, date: str, logs_dir: Path, config: GatewayConfig,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Build a bounded, read-only per-turn evidence chain for one exact trace."""
    rows = [row for row in load_jsonl(_dialogue_path(logs_dir)) if _turn_trace_ref(row) == trace_ref]
    users = [row for row in rows if row.get("role") == "user"]
    bots = [row for row in rows if row.get("role") == "bot"]
    bot = bots[-1] if bots else None
    answer_kind = str((bot or {}).get("answer_kind") or "")[:80] or None
    public_text = (
        "[contact redacted]"
        if answer_kind in EVIDENCE_CONTACT_ANSWER_KINDS
        else _safe_text(bot.get("text"), limit=1200) if bot else ""
    )
    summary = bot.get("runtime_summary") if isinstance(bot, Mapping) and isinstance(bot.get("runtime_summary"), Mapping) else {}
    task_ids = _attempt_task_ids(summary)
    primary: list[dict[str, Any]] = []
    primary_cards: list[dict[str, Any]] = []
    for task_id in task_ids[:5]:
        try:
            status, result = fetch_task_json(config, task_id, "status", urlopen=urlopen), fetch_task_json(config, task_id, "result", urlopen=urlopen)
            diagnosis = build_diagnosis(task_id, status, result)
            cards = _result_cards(result)
            primary_cards.extend(cards)
            primary.append({"task_id": task_id, "status": diagnosis["status"], "result_present": diagnosis["result_present"], "card_count": len(cards)})
        except DiagnosticError as exc:
            primary.append({"task_id": task_id, "status": "unavailable", "error_code": exc.code})
    accepted: list[dict[str, Any]] = []
    candidate_errors: list[str] = []
    bot_ts = _parse_ts(bot.get("ts")) if bot else None
    user_ts = _parse_ts(users[-1].get("ts")) if users else None
    numeric_ids = [int(value) for value in task_ids if value.isdecimal()]
    if numeric_ids and primary_cards and user_ts and bot_ts:
        start = max(numeric_ids)
        for candidate_id in range(start + 1, start + 1 + EVIDENCE_CHILD_PROBE_LIMIT):
            try:
                result = fetch_task_json(config, str(candidate_id), "result", urlopen=urlopen)
            except DiagnosticError as exc:
                candidate_errors.append(exc.code)
                continue
            cards = _result_cards(result)
            completed = _task_timestamp(result)
            if len(cards) != 1 or completed is None:
                continue
            primary_index = _matching_primary_index(cards[0], primary_cards)
            if primary_index is None:
                continue
            if not (user_ts <= completed <= bot_ts + timedelta(seconds=EVIDENCE_CHILD_TOLERANCE_SECONDS)):
                continue
            accepted.append({"task_id": str(candidate_id), "correlation_method": "single_card_primary_key_and_timestamp", "confidence": "high", "primary_index": primary_index, "card": cards[0]})
    child_by_index = {item["primary_index"]: item["card"] for item in accepted}
    claim_segments = _claim_segments(public_text, primary_cards)
    cards_report: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for primary_index, raw in enumerate(primary_cards[:20]):
        child = child_by_index.get(primary_index)
        merged = {**raw, **(child or {})}
        normalized = _normalized_card(merged)
        raw_safe, child_safe = _bounded_card(raw), _bounded_card(child or {})
        added = {key: value for key, value in child_safe.items() if key not in raw_safe or raw_safe[key] != value}
        claims = _claims_for_card(public_text, raw, card_count=len(primary_cards), segments=claim_segments)
        card_claims = {field: value for field, value in claims.items() if value}
        card_id = str(raw_safe.get("id") or raw_safe.get("name") or raw_safe.get("title") or "card")[:128]
        for field in card_claims:
            if not _evidence_has(merged, normalized, field):
                conflicts.append({"category": "unsupported_public_claim", "card": card_id, "field": field, "candidate": True})
        for field in ("price", "delivered", "finishing", "location"):
            if _evidence_has(merged, normalized, field) and not claims.get(field):
                conflicts.append({"category": "available_but_hidden", "card": card_id, "field": field, "candidate": True})
        if normalized:
            for field in ("finishing", "delivered", "area", "metro"):
                if _evidence_has(raw, None, field) and not _evidence_has({}, normalized, field):
                    conflicts.append({"category": "dropped_by_normalizer", "card": card_id, "field": field, "candidate": True})
        cards_report.append({"card_ref": card_id, "raw_fields": raw_safe, "fields_added_by_child": added, "normalized_fields": _bounded_normalized_card(normalized), "public_claim_fields": card_claims})
    broad_expected = int(summary.get("call_counts", {}).get("gateway_attempts", 0) or 0) > 1 or str(summary.get("stage") or "") == "main_search"
    if broad_expected and primary_cards and not accepted:
        conflicts.append({"category": "missing_lineage", "candidate": True, "detail": "expected enrichment could not be proven from bounded child probes"})
    first = conflicts[0] if conflicts else None
    owner = ("card_normalizer" if first and first["category"] == "dropped_by_normalizer" else "search_enrichment" if first and first["category"] == "missing_lineage" else "scenario_recipes/response" if first else "runtime/journal observability" if not task_ids else "MCP/search")
    return {
        "schema_version": EVIDENCE_CHAIN_SCHEMA, "trace_ref": trace_ref, "trace_present": bool(rows),
        "release_id": str((bot or {}).get("release_id") or "")[:128] or None,
        "turn_timestamps": {"user": _iso_or_none(users[-1].get("ts")) if users else None, "bot": _iso_or_none(bot.get("ts")) if bot else None},
        "answer_kind": answer_kind,
        "action": str(summary.get("action") or "")[:80] or None, "stage": str(summary.get("stage") or "")[:80] or None,
        "public_response": public_text, "primary_tasks": primary, "accepted_child_tasks": [{k: v for k, v in item.items() if k not in {"card", "primary_index"}} for item in accepted],
        "cards": cards_report, "candidate_conflicts": conflicts[:40], "first_divergence": {"candidate": first, "owner": owner} if first else None,
        "lineage_coverage": {"primary_task_ids": len(task_ids), "primary_cards": len(primary_cards), "accepted_children": len(accepted), "child_probe_limit": EVIDENCE_CHILD_PROBE_LIMIT, "candidate_probe_error_count": len(candidate_errors)},
        "durations": {"gateway_attempts": summary.get("call_counts", {}).get("gateway_attempts"), "timing_ms": _sanitize_json_value(summary.get("timing_ms", {}))},
        "safe_next_check": {"owner": owner, "action": "inspect bounded evidence or add journal child task ids; no replay or write"},
        "note": "Read-only bounded evidence chain. Raw gateway responses, URLs, contacts, prompts and payloads are excluded.",
    }


def run_evidence_chain(trace_ref: str, *, date: str, repo: Path = REPO, logs_dir: Path | None = None, env: Mapping[str, str] | None = None, urlopen: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    return build_evidence_chain_report(trace_ref, date=date, logs_dir=logs_dir or (repo / "logs"), config=load_config(env=env, repo=repo), urlopen=urlopen)


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _print_human(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe Overmind gateway task diagnostic for NMBot")
    parser.add_argument("task_id", nargs="?")
    parser.add_argument("--date", default=_today_utc(), help="UTC log date for bot_error_events correlation, YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="Print bounded JSON diagnosis. Human mode is also JSON for copy/paste safety.")
    parser.add_argument("--scenario", action="store_true", help="Print safe scenario timeline report instead of task-only diagnosis")
    parser.add_argument("--evidence-chain", action="store_true", help="Build bounded read-only evidence chain for --trace-ref")
    parser.add_argument("--trace-ref", help="Exact dialogue_journal trace_ref for --evidence-chain")
    parser.add_argument("--session-ref", help="Exact sha256 session/conversation ref, or raw local ref to hash before matching")
    parser.add_argument("--query", help="Bounded substring filter over already-redacted journal/trace text")
    parser.add_argument("--from", dest="from_ts", help="UTC ISO timestamp lower bound")
    parser.add_argument("--to", dest="to_ts", help="UTC ISO timestamp upper bound")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Maximum rows/events to read per local journal")
    parser.add_argument("--repo", default=str(REPO), help=argparse.SUPPRESS)
    parser.add_argument("--logs-dir", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        repo = Path(args.repo).expanduser().resolve()
        logs_dir = Path(args.logs_dir).expanduser().resolve() if args.logs_dir else None
        effective_logs_dir = logs_dir or (repo / "logs")
        if args.evidence_chain:
            if not args.trace_ref:
                raise DiagnosticError("evidence_chain_requires_trace_ref")
            if args.task_id or args.scenario or args.session_ref or args.query or args.from_ts or args.to_ts:
                raise DiagnosticError("evidence_chain_incompatible_options")
            result = run_evidence_chain(args.trace_ref, date=args.date, repo=repo, logs_dir=effective_logs_dir)
            _print_human(result)
            return 0
        scenario_mode = bool(args.scenario or args.session_ref or args.query or args.from_ts or args.to_ts or not args.task_id)
        if scenario_mode:
            from_ts = _parse_ts(args.from_ts) if args.from_ts else None
            to_ts = _parse_ts(args.to_ts) if args.to_ts else None
            if args.from_ts and from_ts is None:
                raise DiagnosticError("invalid_from_timestamp")
            if args.to_ts and to_ts is None:
                raise DiagnosticError("invalid_to_timestamp")
            session_ref = _hash_session_ref(args.session_ref) if args.session_ref else None
            gateway_diagnosis = None
            if args.task_id:
                gateway_diagnosis = run_diagnostic(args.task_id, date=args.date, repo=repo, logs_dir=effective_logs_dir)
            result = build_scenario_report(
                task_id=args.task_id,
                session_ref=session_ref,
                query=args.query[:SAFE_QUERY_MAX] if args.query else None,
                from_ts=from_ts,
                to_ts=to_ts,
                date=args.date,
                logs_dir=effective_logs_dir,
                gateway_diagnosis=gateway_diagnosis,
                limit=max(1, min(int(args.limit or DEFAULT_LIMIT), 500)),
            )
        else:
            result = run_diagnostic(args.task_id, date=args.date, repo=repo, logs_dir=logs_dir)
    except DiagnosticError as exc:
        safe_error = {"status": "diagnostic_failed", "error_code": exc.code, "note": "No secret values are included."}
        print(json.dumps(safe_error, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2 if exc.code == "missing_gateway_token" or exc.code.startswith("evidence_chain_") else 3
    _print_human(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
