#!/usr/bin/env python3
"""Local fail-closed documentation update gate.

This stdlib-only CLI validates a human-approved documentation update queue and
can produce a safe patch plan. It never edits docs, calls notebooks, imports
runtime modules, runs subprocesses, or touches network/production.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OWNERS = Path("config/project_documentation_owners.json")
DEFAULT_STORE = Path("data/project_documentation_updates.jsonl")

OWNER_SCHEMA = "project_documentation_owners.v1"
EVENT_SCHEMA = "project_documentation_update.v1"
VALIDATION_SCHEMA = "project_documentation_gate_validation.v1"
PLAN_SCHEMA = "project_documentation_patch_plan.v1"
LIST_SCHEMA = "project_documentation_update_list.v1"
RECEIPT_SCHEMA = "project_documentation_verify_receipt.v1"
CAPTURE_SCHEMA = "project_documentation_capture_result.v1"

EVENT_KEYS = {
    "schema",
    "update_id",
    "project_id",
    "topic",
    "change_type",
    "status",
    "fact",
    "evidence_refs",
    "verification",
    "supersedes_anchor",
    "human_approved",
    "docs_write_performed",
    "notebook_write_authorized",
    "runtime_change_authorized",
    "production_claim_authorized",
    "created_at",
}
EVIDENCE_KEYS = {"kind", "ref", "sha256"}
VERIFICATION_KEYS = {"type", "result", "verified_at"}
RECEIPT_KEYS = {
    "schema",
    "receipt_id",
    "project_id",
    "topic",
    "change_type",
    "fact",
    "evidence_refs",
    "verification",
    "supersedes_anchor",
    "created_at",
}
CHANGE_TYPES = {"addition", "correction"}
STATUSES = {"pending", "verified"}
EVIDENCE_KINDS = {"source", "test", "doc", "artifact", "live"}
VERIFICATION_TYPES = {"source_readback", "focused_test", "metadata_sha_readback", "live_production", "manual_doc_correction"}
VERIFICATION_RESULTS = {"passed", "pending", "failed"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/#@+-]{0,199}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
DOC_PATH_RE = re.compile(r"^docs/[A-Za-z0-9_./-]+\.md$")
REF_WITH_ANCHOR_RE = re.compile(r"^(?P<path>[A-Za-z0-9_./-]+)(?::[1-9][0-9]*(?:-[1-9][0-9]*)?|#[A-Za-z0-9_.:-]+)$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SECRETISH_VALUE_RE = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|credential|bearer\s+[a-z0-9]|sk-[a-z0-9]|BEGIN\s+(RSA\s+)?PRIVATE\s+KEY|[A-Za-z0-9_]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})",
    re.IGNORECASE,
)
CUSTOMER_DATA_RE = re.compile(r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|\+?\d[\d\s().-]{8,}\d)", re.IGNORECASE)
RAW_CONTENT_RE = re.compile(r"(raw\s+(request|log|payload|transcript|source)|client_message|bot_message|authorization:|set-cookie:)", re.IGNORECASE)
SOURCE_ROOTS = ("scripts", "nmbot_v0", "nmbot_v2", "nmbot_v3")
RUNTIME_OR_SECRET_PATH_PARTS = {".env", "logs", "backups", "data", "config", "tmp", "secrets", "secret", "keys", "key"}
FORBIDDEN_KEY_PARTS = {
    "request",
    "response",
    "body",
    "payload",
    "transcript",
    "log",
    "source_body",
    "label",
    "secret",
    "password",
    "token",
    "credential",
    "apikey",
    "api_key",
    "env",
}


class GateError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def error(code: str, message: str, line: int | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if line is not None:
        item["line"] = line
    return item


def contained_path(raw: str | Path, *, must_exist: bool = False) -> Path:
    value = str(raw)
    if not value or value.startswith("/") or "\x00" in value:
        raise GateError("path_not_contained", "path must be repo-relative and contained")
    candidate = (ROOT / value).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as exc:
        raise GateError("path_not_contained", "path must be repo-relative and contained") from exc
    if must_exist and not candidate.exists():
        raise GateError("path_missing", "referenced path does not exist")
    return candidate


def _relative_parts(raw: str | Path) -> tuple[str, ...]:
    value = str(raw)
    if not value or value.startswith("/") or "\x00" in value:
        raise GateError("path_not_contained", "path must be repo-relative and contained")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise GateError("path_not_contained", "path must be repo-relative and contained")
    return path.parts


def _reject_symlink_path(raw: str | Path) -> None:
    parts = _relative_parts(raw)
    current = ROOT
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise GateError("path_symlink", "referenced path must not contain symlinks")


def non_symlink_contained_path(raw: str | Path, *, must_exist: bool = False) -> Path:
    _reject_symlink_path(raw)
    return contained_path(raw, must_exist=must_exist)


def validate_store_path(store: str | Path) -> Path:
    parts = _relative_parts(store)
    if len(parts) != 2 or parts[0] != "data":
        raise GateError("store_path", "store path must be a repo-contained data/*.jsonl path")
    if Path(parts[-1]).suffix != ".jsonl":
        raise GateError("store_extension", "store path must end with .jsonl")
    _reject_symlink_path(store)
    candidate = (ROOT / Path(*parts)).resolve()
    data_root = (ROOT / "data").resolve()
    try:
        candidate.relative_to(data_root)
    except ValueError as exc:
        raise GateError("store_path", "store path must stay inside repo data/") from exc
    if candidate.exists() and candidate.is_symlink():
        raise GateError("store_symlink", "store path must not be a symlink")
    return candidate


def load_json_object(path: str | Path) -> dict[str, Any]:
    try:
        with contained_path(path, must_exist=True).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise GateError("malformed_json", "JSON is malformed") from exc
    if not isinstance(data, dict):
        raise GateError("json_object", "JSON must be an object")
    return data


def parse_timestamp(value: Any, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise GateError("timestamp_invalid", "timestamp must be an ISO-8601 UTC string")
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise GateError("timestamp_invalid", "timestamp must be an ISO-8601 UTC string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise GateError("timestamp_invalid", "timestamp must be UTC")
    return parsed


def _looks_like_iso_timestamp(value: str) -> bool:
    try:
        parse_timestamp(value, nullable=False)
        return True
    except GateError:
        return False


def scan_forbidden(value: Any, errors: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                errors.append(error("field_name_type", "field names must be strings"))
                continue
            lowered = key.lower().replace("-", "_")
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                errors.append(error("forbidden_field", "forbidden raw/sensitive field name present"))
            scan_forbidden(child, errors)
    elif isinstance(value, list):
        for child in value:
            scan_forbidden(child, errors)
    elif isinstance(value, str):
        if SECRETISH_VALUE_RE.search(value):
            errors.append(error("secretish_value", "sensitive-looking value present"))
        if not _looks_like_iso_timestamp(value) and CUSTOMER_DATA_RE.search(value):
            errors.append(error("customer_data", "customer-like personal data present"))
        if RAW_CONTENT_RE.search(value):
            errors.append(error("raw_content", "raw logs/requests/payloads/transcripts are not allowed"))
        if value.startswith("/") or "../" in value or value == "..":
            errors.append(error("unsafe_value_path", "absolute paths and traversal are not allowed"))


def validate_owner_map(path: str | Path = DEFAULT_OWNERS) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    try:
        owners = load_json_object(path)
        if owners.get("schema") != OWNER_SCHEMA or not isinstance(owners.get("projects"), dict):
            errors.append(error("owner_schema", f"owner map schema must be {OWNER_SCHEMA}"))
            raise GateError("owner_schema", "invalid owner map")
        seen_targets: set[tuple[str, str]] = set()
        for project_id, topics in owners["projects"].items():
            if not isinstance(project_id, str) or not ID_RE.match(project_id) or not isinstance(topics, dict) or not topics:
                errors.append(error("owner_project", "project ids must map to non-empty topic objects"))
                continue
            for topic, doc in topics.items():
                if not isinstance(topic, str) or not ID_RE.match(topic):
                    errors.append(error("owner_topic", "topic must be a safe id"))
                if not isinstance(doc, str) or not DOC_PATH_RE.match(doc):
                    errors.append(error("owner_doc", "owner doc must be a repo docs markdown path"))
                    continue
                try:
                    resolved = non_symlink_contained_path(doc, must_exist=True)
                    if not resolved.is_file():
                        errors.append(error("owner_doc_file", "owner doc must be an existing non-symlink file"))
                    seen_targets.add((project_id, topic))
                except GateError as exc:
                    errors.append(error(exc.code, str(exc)))
        return {"schema": VALIDATION_SCHEMA, "valid": not errors, "errors": errors, "owner_count": len(seen_targets)}
    except GateError as exc:
        if not errors:
            errors.append(error(exc.code, str(exc)))
        return {"schema": VALIDATION_SCHEMA, "valid": False, "errors": errors, "owner_count": 0}


def resolve_target_doc(project_id: str, topic: str, owners_path: str | Path = DEFAULT_OWNERS) -> str:
    owners = load_json_object(owners_path)
    try:
        target = owners["projects"][project_id][topic]
    except KeyError as exc:
        raise GateError("owner_route_missing", "no canonical owner route for project/topic") from exc
    result = validate_owner_map(owners_path)
    if not result["valid"]:
        raise GateError("owner_map_invalid", "owner map is invalid")
    return target


def _validate_ref(kind: str, ref: Any, sha256: Any, errors: list[dict[str, Any]]) -> None:
    if not isinstance(ref, str) or not ref or len(ref) > 240 or CONTROL_RE.search(ref):
        errors.append(error("evidence_ref", "evidence ref must be a bounded safe string"))
        return
    if kind in {"source", "test", "doc"}:
        match = REF_WITH_ANCHOR_RE.match(ref)
        if not match:
            errors.append(error("evidence_anchor", "source/test/doc evidence refs need repo-relative line or symbol anchors"))
            return
        raw_path = match.group("path")
        path = Path(raw_path)
        parts = path.parts
        if any(part.lower() in RUNTIME_OR_SECRET_PATH_PARTS or part.lower().startswith(".env") for part in parts):
            errors.append(error("evidence_forbidden_path", "evidence refs must not point to secret/runtime artifact paths"))
            return
        if any("secret" in part.lower() or "key" in part.lower() or "token" in part.lower() for part in parts):
            errors.append(error("evidence_forbidden_path", "evidence refs must not point to secret/runtime artifact paths"))
            return
        if kind == "source":
            if not parts or parts[0] not in SOURCE_ROOTS or path.suffix != ".py":
                errors.append(error("evidence_source_path", "source evidence must point to allowed repo Python source paths"))
                return
        elif kind == "test":
            if not parts or parts[0] != "tests" or path.suffix != ".py":
                errors.append(error("evidence_test_path", "test evidence must point to tests/*.py"))
                return
        elif kind == "doc":
            if not parts or parts[0] != "docs" or path.suffix != ".md":
                errors.append(error("evidence_doc_path", "doc evidence must point to docs/*.md"))
                return
        try:
            non_symlink_contained_path(raw_path, must_exist=True)
        except GateError as exc:
            errors.append(error(exc.code, str(exc)))
    elif kind == "artifact":
        if not SAFE_ID_RE.match(ref) or "/" in ref:
            errors.append(error("artifact_ref", "artifact refs must be safe ids"))
        if not isinstance(sha256, str) or not HEX64_RE.match(sha256):
            errors.append(error("artifact_sha", "artifact refs require sha256"))
    elif kind == "live":
        if not SAFE_ID_RE.match(ref) or "/" in ref:
            errors.append(error("live_ref", "live refs must be safe ids"))
        if sha256 is not None:
            errors.append(error("live_sha", "live refs must not include sha256"))


def validate_event(event: dict[str, Any], owners_path: str | Path = DEFAULT_OWNERS) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    scan_forbidden(event, errors)
    if set(event) != EVENT_KEYS:
        errors.append(error("event_keys", "event fields are not exact"))
        return {"schema": VALIDATION_SCHEMA, "valid": False, "errors": errors}
    if event.get("schema") != EVENT_SCHEMA:
        errors.append(error("event_schema", f"schema must be {EVENT_SCHEMA}"))
    for key in ("update_id", "project_id", "topic"):
        if not isinstance(event.get(key), str) or not ID_RE.match(event[key]):
            errors.append(error("safe_id", f"{key} must be a safe id"))
    if event.get("change_type") not in CHANGE_TYPES:
        errors.append(error("change_type", "change_type must be addition or correction"))
    if event.get("status") not in STATUSES:
        errors.append(error("status", "status must be pending or verified"))
    fact = event.get("fact")
    if not isinstance(fact, str) or not fact.strip() or len(fact) > 1200 or CONTROL_RE.search(fact):
        errors.append(error("fact", "fact must be non-empty, bounded, and free of control chars"))
    if event.get("change_type") == "correction":
        anchor = event.get("supersedes_anchor")
        if not isinstance(anchor, str) or not anchor.startswith("#") or not SAFE_ID_RE.match(anchor[1:]):
            errors.append(error("correction_anchor", "corrections require a stable supersedes anchor"))
    elif event.get("supersedes_anchor") is not None:
        errors.append(error("addition_anchor", "additions must have null supersedes_anchor"))
    for flag in ("docs_write_performed", "notebook_write_authorized", "runtime_change_authorized", "production_claim_authorized"):
        if event.get(flag) is not False:
            errors.append(error("no_write_flag", f"{flag} must be false"))
    if event.get("status") == "pending" and event.get("human_approved") is not False:
        errors.append(error("pending_approval", "pending records must not be human approved"))
    elif event.get("status") == "verified" and not isinstance(event.get("human_approved"), bool):
        errors.append(error("approval_type", "human_approved must be boolean"))
    evidence = event.get("evidence_refs")
    if not isinstance(evidence, list) or len(evidence) > 5:
        errors.append(error("evidence_refs", "evidence_refs must be a list of at most 5 items"))
    elif event.get("status") == "verified" and not evidence:
        errors.append(error("verified_requires_evidence", "verified records require at least one evidence ref"))
    else:
        for item in evidence:
            if not isinstance(item, dict) or set(item) != EVIDENCE_KEYS:
                errors.append(error("evidence_keys", "evidence refs must have exact fields"))
                continue
            kind = item.get("kind")
            if kind not in EVIDENCE_KINDS:
                errors.append(error("evidence_kind", "invalid evidence kind"))
                continue
            sha = item.get("sha256")
            if sha is not None and (not isinstance(sha, str) or not HEX64_RE.match(sha)):
                errors.append(error("evidence_sha", "sha256 must be null or lowercase hex64"))
            _validate_ref(kind, item.get("ref"), sha, errors)
    verification = event.get("verification")
    if not isinstance(verification, dict) or set(verification) != VERIFICATION_KEYS:
        errors.append(error("verification_keys", "verification must have exact fields"))
    else:
        if verification.get("type") not in VERIFICATION_TYPES:
            errors.append(error("verification_type", "invalid verification type"))
        if verification.get("result") not in VERIFICATION_RESULTS:
            errors.append(error("verification_result", "invalid verification result"))
        try:
            parse_timestamp(verification.get("verified_at"), nullable=True)
        except GateError as exc:
            errors.append(error(exc.code, str(exc)))
        if event.get("status") == "verified":
            if verification.get("result") != "passed" or verification.get("verified_at") is None:
                errors.append(error("verified_requires_passed", "verified records require passed result and timestamp"))
        if event.get("status") == "pending":
            if verification.get("result") == "passed" or verification.get("verified_at") is not None:
                errors.append(error("pending_verification", "pending records must not claim passed verification"))
    try:
        parse_timestamp(event.get("created_at"))
    except GateError as exc:
        errors.append(error(exc.code, str(exc)))
    if isinstance(event.get("project_id"), str) and isinstance(event.get("topic"), str):
        try:
            resolve_target_doc(event["project_id"], event["topic"], owners_path)
        except GateError as exc:
            errors.append(error(exc.code, str(exc)))
    return {"schema": VALIDATION_SCHEMA, "valid": not errors, "errors": errors}


def event_fingerprint(event: dict[str, Any]) -> str:
    comparable = {key: value for key, value in event.items() if key not in {"update_id", "created_at"}}
    raw = json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def iter_store_records(store: str | Path) -> list[tuple[int, dict[str, Any]]]:
    path = validate_store_path(store)
    records: list[tuple[int, dict[str, Any]]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GateError("malformed_queue", f"queue contains malformed JSON at line {line_no}") from exc
            if not isinstance(obj, dict):
                raise GateError("queue_object", f"queue line {line_no} is not an object")
            records.append((line_no, obj))
    return records


def validate_store(store: str | Path = DEFAULT_STORE, owners_path: str | Path = DEFAULT_OWNERS) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    owner_result = validate_owner_map(owners_path)
    errors.extend(owner_result.get("errors", []))
    ids: set[str] = set()
    fps: set[str] = set()
    count = 0
    try:
        for line_no, event in iter_store_records(store):
            count += 1
            result = validate_event(event, owners_path)
            for item in result["errors"]:
                errors.append(error(item["code"], item["message"], line=line_no))
            update_id = event.get("update_id")
            if isinstance(update_id, str):
                if update_id in ids:
                    errors.append(error("duplicate_update_id", "duplicate update_id", line=line_no))
                ids.add(update_id)
            if result["valid"]:
                fp = event_fingerprint(event)
                if fp in fps:
                    errors.append(error("duplicate_fingerprint", "duplicate event fingerprint", line=line_no))
                fps.add(fp)
    except GateError as exc:
        errors.append(error(exc.code, str(exc)))
    return {"schema": VALIDATION_SCHEMA, "valid": not errors, "errors": errors, "record_count": count}


def append_event(input_path: str | Path, store: str | Path = DEFAULT_STORE, owners_path: str | Path = DEFAULT_OWNERS) -> dict[str, Any]:
    event = load_json_object(input_path)
    return append_event_object(event, store, owners_path)


def append_event_object(event: dict[str, Any], store: str | Path = DEFAULT_STORE, owners_path: str | Path = DEFAULT_OWNERS) -> dict[str, Any]:
    result = validate_event(event, owners_path)
    if not result["valid"]:
        return {"schema": VALIDATION_SCHEMA, "appended": False, "valid": False, "errors": result["errors"]}
    record_count_before = 0
    try:
        store_path = validate_store_path(store)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with store_path.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                ids: set[str] = set()
                fps: set[str] = set()
                for line_no, line in enumerate(fh, start=1):
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise GateError("malformed_queue", f"queue contains malformed JSON at line {line_no}") from exc
                    if not isinstance(obj, dict):
                        raise GateError("queue_object", f"queue line {line_no} is not an object")
                    item_result = validate_event(obj, owners_path)
                    if not item_result["valid"]:
                        raise GateError("queue_invalid", f"queue validation failed at line {line_no}")
                    ids.add(obj["update_id"])
                    fps.add(event_fingerprint(obj))
                    record_count_before += 1
                if event["update_id"] in ids:
                    raise GateError("duplicate_update_id", "duplicate update_id")
                if event_fingerprint(event) in fps:
                    raise GateError("duplicate_fingerprint", "duplicate event fingerprint")
                fh.seek(0, os.SEEK_END)
                fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except GateError as exc:
        return {"schema": VALIDATION_SCHEMA, "appended": False, "valid": False, "errors": [error(exc.code, str(exc))]}
    return {"schema": VALIDATION_SCHEMA, "appended": True, "valid": True, "update_id": event["update_id"], "record_count_before": record_count_before}


def receipt_to_event(receipt: dict[str, Any], owners_path: str | Path = DEFAULT_OWNERS) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    scan_forbidden(receipt, errors)
    if set(receipt) != RECEIPT_KEYS:
        errors.append(error("receipt_keys", "receipt fields are not exact"))
        raise GateError("receipt_invalid", json.dumps({"errors": errors}, ensure_ascii=False))
    if receipt.get("schema") != RECEIPT_SCHEMA:
        errors.append(error("receipt_schema", f"schema must be {RECEIPT_SCHEMA}"))
    if not isinstance(receipt.get("receipt_id"), str) or not ID_RE.match(receipt["receipt_id"]):
        errors.append(error("safe_id", "receipt_id must be a safe id"))
    verification = receipt.get("verification")
    if not isinstance(verification, dict) or set(verification) != VERIFICATION_KEYS:
        errors.append(error("verification_keys", "verification must have exact fields"))
    else:
        result = verification.get("result")
        verified_at = verification.get("verified_at")
        if result == "passed":
            try:
                parse_timestamp(verified_at, nullable=False)
            except GateError:
                errors.append(error("receipt_passed_timestamp", "passed receipts require a valid UTC verified_at"))
        elif result in {"pending", "failed"}:
            if verified_at is not None:
                errors.append(error("receipt_pending_timestamp", "pending or failed receipts must have null verified_at"))
        else:
            errors.append(error("verification_result", "invalid verification result"))
    try:
        parse_timestamp(receipt.get("created_at"))
    except GateError as exc:
        errors.append(error(exc.code, str(exc)))
    if errors:
        raise GateError("receipt_invalid", json.dumps({"errors": errors}, ensure_ascii=False))

    status = "verified" if receipt["verification"]["result"] == "passed" else "pending"
    event = {
        "schema": EVENT_SCHEMA,
        "update_id": receipt["receipt_id"],
        "project_id": receipt["project_id"],
        "topic": receipt["topic"],
        "change_type": receipt["change_type"],
        "status": status,
        "fact": receipt["fact"],
        "evidence_refs": receipt["evidence_refs"],
        "verification": receipt["verification"],
        "supersedes_anchor": receipt["supersedes_anchor"],
        "human_approved": False,
        "docs_write_performed": False,
        "notebook_write_authorized": False,
        "runtime_change_authorized": False,
        "production_claim_authorized": False,
        "created_at": receipt["created_at"],
    }
    event_result = validate_event(event, owners_path)
    if not event_result["valid"]:
        raise GateError("receipt_event_invalid", json.dumps({"errors": event_result["errors"]}, ensure_ascii=False))
    return event


def _gate_error_payload(exc: GateError) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(str(exc))
    except json.JSONDecodeError:
        return [error(exc.code, str(exc))]
    items = parsed.get("errors") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        return [error(exc.code, "documentation gate failed closed")]
    safe_errors = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("code"), str) and isinstance(item.get("message"), str):
            safe_errors.append(error(item["code"], item["message"]))
    return safe_errors or [error(exc.code, "documentation gate failed closed")]


def capture_receipt(input_path: str | Path, store: str | Path = DEFAULT_STORE, owners_path: str | Path = DEFAULT_OWNERS) -> dict[str, Any]:
    try:
        receipt = load_json_object(input_path)
        event = receipt_to_event(receipt, owners_path)
        append_result = append_event_object(event, store, owners_path)
        if not append_result.get("appended"):
            return {"schema": CAPTURE_SCHEMA, "appended": False, "valid": False, "errors": append_result.get("errors", [])}
        target_doc = resolve_target_doc(event["project_id"], event["topic"], owners_path)
        return {
            "schema": CAPTURE_SCHEMA,
            "appended": True,
            "valid": True,
            "receipt_id": event["update_id"],
            "update_id": event["update_id"],
            "status": event["status"],
            "project_id": event["project_id"],
            "topic": event["topic"],
            "target_doc": target_doc,
        }
    except GateError as exc:
        return {"schema": CAPTURE_SCHEMA, "appended": False, "valid": False, "errors": _gate_error_payload(exc)}


def _find_update(update_id: str, store: str | Path) -> dict[str, Any]:
    for _line_no, event in iter_store_records(store):
        if event.get("update_id") == update_id:
            return event
    raise GateError("update_not_found", "update_id not found")


def plan_update(update_id: str, store: str | Path = DEFAULT_STORE, owners_path: str | Path = DEFAULT_OWNERS) -> dict[str, Any]:
    if not ID_RE.match(update_id):
        raise GateError("safe_id", "update_id must be a safe id")
    store_result = validate_store(store, owners_path)
    if not store_result["valid"]:
        return {"schema": PLAN_SCHEMA, "ok": False, "errors": store_result["errors"]}
    event = _find_update(update_id, store)
    denial: list[dict[str, Any]] = []
    if event["status"] != "verified":
        denial.append(error("plan_requires_verified", "planning requires verified status"))
    if event["human_approved"] is not True:
        denial.append(error("plan_requires_approval", "planning requires human approval"))
    if event["verification"]["result"] != "passed" or not event["verification"].get("verified_at"):
        denial.append(error("plan_requires_passed", "planning requires passed verification"))
    if not event["evidence_refs"]:
        denial.append(error("plan_requires_evidence", "planning requires non-empty evidence_refs"))
    for flag in ("docs_write_performed", "notebook_write_authorized", "runtime_change_authorized", "production_claim_authorized"):
        if event[flag] is not False:
            denial.append(error("no_write_flag", f"{flag} must be false"))
    if denial:
        return {"schema": PLAN_SCHEMA, "ok": False, "update_id": update_id, "errors": denial}
    target_doc = resolve_target_doc(event["project_id"], event["topic"], owners_path)
    return {
        "schema": PLAN_SCHEMA,
        "ok": True,
        "update_id": update_id,
        "project_id": event["project_id"],
        "topic": event["topic"],
        "target_doc": target_doc,
        "change_type": event["change_type"],
        "supersedes_anchor": event["supersedes_anchor"],
        "proposed_fact": event["fact"],
        "evidence_refs": event["evidence_refs"],
        "allowed_action": "prepare_human_patch_only",
        "docs_write_performed": False,
        "notebook_write_authorized": False,
        "runtime_change_authorized": False,
        "production_claim_authorized": False,
    }


def list_updates(store: str | Path = DEFAULT_STORE, owners_path: str | Path = DEFAULT_OWNERS) -> dict[str, Any]:
    store_result = validate_store(store, owners_path)
    if not store_result["valid"]:
        return {"schema": LIST_SCHEMA, "ok": False, "errors": store_result["errors"], "records": []}
    records = []
    for _line_no, event in iter_store_records(store):
        target = resolve_target_doc(event["project_id"], event["topic"], owners_path)
        records.append({"update_id": event["update_id"], "status": event["status"], "project_id": event["project_id"], "topic": event["topic"], "target_doc": target})
    return {"schema": LIST_SCHEMA, "ok": True, "count": len(records), "records": records}


def emit(payload: dict[str, Any], as_json: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if as_json else None, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate/enqueue/capture/plan local documentation updates without writing docs.")
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--validate", action="store_true")
    commands.add_argument("--enqueue", action="store_true")
    commands.add_argument("--capture", action="store_true")
    commands.add_argument("--plan", action="store_true")
    commands.add_argument("--list", action="store_true")
    parser.add_argument("--input", help="repo-relative event or receipt JSON path")
    parser.add_argument("--update-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.validate:
            payload = validate_store(DEFAULT_STORE, DEFAULT_OWNERS)
            emit(payload, args.json)
            return 0 if payload["valid"] else 2
        if args.enqueue:
            if not args.input:
                raise GateError("missing_input", "--enqueue requires --input")
            payload = append_event(args.input, DEFAULT_STORE, DEFAULT_OWNERS)
            emit(payload, args.json)
            return 0 if payload.get("appended") else 2
        if args.capture:
            if not args.input:
                raise GateError("missing_input", "--capture requires --input")
            payload = capture_receipt(args.input, DEFAULT_STORE, DEFAULT_OWNERS)
            emit(payload, args.json)
            return 0 if payload.get("appended") else 2
        if args.plan:
            if not args.update_id:
                raise GateError("missing_update_id", "--plan requires --update-id")
            payload = plan_update(args.update_id, DEFAULT_STORE, DEFAULT_OWNERS)
            emit(payload, args.json)
            return 0 if payload.get("ok") else 2
        if args.list:
            payload = list_updates(DEFAULT_STORE, DEFAULT_OWNERS)
            emit(payload, args.json)
            return 0 if payload.get("ok") else 2
        raise GateError("missing_command", "choose --validate, --enqueue, --capture, --plan, or --list")
    except GateError as exc:
        emit({"schema": VALIDATION_SCHEMA, "valid": False, "errors": [error(exc.code, str(exc))]}, args.json)
        return 2
    except Exception:
        emit({"schema": VALIDATION_SCHEMA, "valid": False, "errors": [error("failed_closed", "documentation gate failed closed")]}, args.json)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
