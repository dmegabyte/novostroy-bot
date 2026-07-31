#!/usr/bin/env python3
"""Local-only human-review summary draft generator for sanitizer-safe records.

The command revalidates the fresh v4 pre-manifest, the v4 no-write sanitizer
authorization, and the sanitizer aggregate report before opening local storage.
It reads only the exact authorized candidate records internally, verifies body
SHA-256, applies strict content checks, performs exact body-SHA dedupe against
the target canonical notebook, and writes a safe aggregate/draft report.

It never calls MCP, network, subprocesses, routing, source mutation, deletion,
or NotebookLM writes. Output intentionally excludes raw source bodies, titles,
snippets, paths, storage roots, match terms, customer data, transcripts, logs,
and secrets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from project_memory_notebook_summary_batch_sanitize import (
    SAFE_DECISION,
    _read_exact_record_body,
    _validate_manifest,
)
from project_memory_notebook_summary_trial_sanitize import _assess_body, _body_for_record, _record_id, _resolve_inside, _safe_segment, _sha_body


SCHEMA = "project_memory_notebook_summary_draft_generation.v1"
AUTH_SCHEMA = "project_memory_notebook_summary_draft_generation_authorization.v1"
AUTHORIZATION_TYPE = "manual_human_review_summary_draft_generation_only"
OWNER = "ser"
EXPECTED_MANIFEST_SHA256 = "0ee513d98213b3418c29cc269fec9c959a686a50172b66ca405c11442ad4f2ae"
EXPECTED_SANITIZER_REPORT_SHA256 = "d3b451d118ab724edabe607d22454e7766756f338192e51b8dfda9949ec0d15c"
EXPECTED_NO_WRITE_AUTH_SHA256 = "167c130f3934d2af2e1d1e1da0cbafa6ec7848778f570e8cfcc23974b3cedcd0"
EXPECTED_DRAFT_AUTH_SHA256 = "85feebac85647f24d3ae0ede67e14d7bdb963b669e85ea302bb758be022016c1"
EXPECTED_CANDIDATE_COUNT = 42
EXPECTED_SAFE_COUNT = 15
EXPECTED_BLOCKED_COUNT = 27
DESTINATION_POLICY = "canonical_only"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SENSITIVE_OUTPUT_RE = re.compile(
    r"(?i)(api[_-]?key\s*[:=]|authorization\s*[:=]|bearer\s+[a-z0-9._~+/=-]{8,}|password\s*[:=]|token\s*[:=]|secret\s*[:=]|private[_-]?key|traceback|stdout|stderr|stack trace|transcript|call recording|client\s*[:=]|customer\s*[:=]|phone\s*[:=]|email\s*[:=]|[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})"
)
RUSSIAN_RE = re.compile(r"[А-Яа-я]")

TOPIC_RULES: list[tuple[str, tuple[str, ...], str, str]] = [
    ("NMBot retrieval/context", ("nmbot", "retrieval", "context", "search", "memory", "notebook"), "NMBot context note — historical summary draft", "Историческая запись относится к контуру NMBot и описывает работу с проектным контекстом, поиском или памятью. Черновик стоит проверять как справочную историческую заметку, а не как доказательство текущего продакшена."),
    ("NMBot runtime", ("jivo", "runtime", "v2", "v3", "composer", "fallback", "planner"), "NMBot runtime note — historical summary draft", "Историческая запись относится к runtime-слою NMBot: маршрутизации, планированию ответа или режимам компоновщика. Черновик сохраняет только общий смысл и требует human review перед любым переносом."),
    ("project memory governance", ("canonical", "owner", "migration", "routing", "write", "authorization", "policy"), "Project memory governance — historical summary draft", "Историческая запись описывает правила владения проектной памятью, канонический notebook или ограничения на перенос данных. Черновик не утверждает, что перенос разрешён или выполнен."),
    ("qapairs", ("qapairs", "qa pairs", "faq"), "QApairs historical note — no-write review", "Историческая запись относится к QApairs/FAQ-контуру. Так как целевой notebook совпадает с исходным каноническим владельцем, запись оставлена как in-place no-write без migration draft."),
    ("mpn", ("mpn", "crm", "daemon"), "MPN historical note — no-write review", "Историческая запись относится к MPN/CRM daemon-контексту. Черновик безопасен только как общий исторический указатель и не является текущим статусом сервиса."),
    ("opencode", ("opencode", "agent", "mcp"), "Opencode ecosystem note — historical summary draft", "Историческая запись относится к opencode/agent tooling. Черновик передаёт только общий исторический смысл без конфигов, путей, секретов или raw логов."),
]


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _base_payload() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "ok": False,
        "decision": "blocked_or_failed_closed",
        "candidate_count": 0,
        "safe_count": 0,
        "blocked_count": 0,
        "draft_count": 0,
        "in_place_count": 0,
        "dedupe_count": 0,
        "dedupe_uncertain_count": 0,
        "dropped_count": 0,
        "integrity_failure_count": 0,
        "drafts": [],
        "in_place": [],
        "dedupe_existing": [],
        "held_or_blocked_counts": {},
        "no_write_confirmation": {
            "notebook_write_authorized": False,
            "notebook_write_performed": False,
            "notebook_mutation_performed": False,
            "migration_performed": False,
            "automatic_routing_changed": False,
            "routing_write_authorized": False,
            "delete_authorized": False,
            "data_deletion_authorized": False,
            "source_mutation_authorized": False,
            "network_or_mcp_called": False,
            "subprocess_called": False,
        },
        "errors": [],
    }


def _blocked(code: str) -> dict[str, Any]:
    payload = _base_payload()
    payload["errors"] = [{"code": code, "message": "draft generation failed closed"}]
    return payload


def _validate_draft_authorization(auth: dict[str, Any], *, auth_sha256: str, no_write_auth_sha256: str, report_sha256: str) -> None:
    if auth.get("schema") != AUTH_SCHEMA or auth.get("authorization_type") != AUTHORIZATION_TYPE:
        raise ValueError("draft_authorization_schema_invalid")
    if auth.get("owner") != OWNER or auth.get("rollback_owner") != OWNER:
        raise ValueError("draft_authorization_owner_invalid")
    manifest = auth.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("sha256") != EXPECTED_MANIFEST_SHA256:
        raise ValueError("draft_authorization_manifest_binding_invalid")
    sanitize_auth = auth.get("no_write_sanitize_authorization")
    if not isinstance(sanitize_auth, dict) or sanitize_auth.get("sha256") != no_write_auth_sha256 or no_write_auth_sha256 != EXPECTED_NO_WRITE_AUTH_SHA256:
        raise ValueError("draft_authorization_no_write_auth_binding_invalid")
    report = auth.get("sanitizer_report")
    if not isinstance(report, dict) or report.get("sha256") != report_sha256 or report_sha256 != EXPECTED_SANITIZER_REPORT_SHA256:
        raise ValueError("draft_authorization_report_binding_invalid")
    scope = auth.get("scope")
    if not isinstance(scope, dict) or scope.get("authorized_safe_record_count") != EXPECTED_SAFE_COUNT or scope.get("authorized_candidate_count") != EXPECTED_CANDIDATE_COUNT:
        raise ValueError("draft_authorization_scope_invalid")
    permissions = auth.get("permissions")
    if not isinstance(permissions, dict) or permissions.get("summary_draft_generation_authorized") is not True:
        raise ValueError("draft_generation_not_authorized")
    for key in (
        "write_performed",
        "notebook_write_authorized",
        "notebook_write_performed",
        "notebook_mutation_performed",
        "migration_authorized",
        "migration_performed",
        "data_deletion_authorized",
        "delete_authorized",
        "routing_write_authorized",
        "automatic_routing_changed",
        "source_mutation_authorized",
        "production_verified",
    ):
        if permissions.get(key) is not False:
            raise ValueError(f"draft_authorization_forbidden_permission:{key}")
    if auth_sha256 != EXPECTED_DRAFT_AUTH_SHA256:
        raise ValueError("draft_authorization_sha_invalid")


def _validate_sanitizer_report(report: dict[str, Any], draft_auth: dict[str, Any]) -> None:
    if report.get("schema") != "project_memory_notebook_summary_batch_sanitize.v1":
        raise ValueError("sanitizer_report_schema_invalid")
    expected = draft_auth["sanitizer_report"]["expected_aggregates"]
    for key, expected_value in expected.items():
        if report.get(key) != expected_value:
            raise ValueError(f"sanitizer_report_aggregate_mismatch:{key}")
    if report.get("processed_records") != []:
        raise ValueError("sanitizer_report_must_be_aggregate_only")


def _safe_ref(ref: dict[str, str]) -> dict[str, str]:
    return {"notebook": str(ref["notebook"]), "kind": str(ref["kind"]), "id": str(ref["id"])}


def _count(payload: dict[str, Any], key: str) -> None:
    counts = payload.setdefault("held_or_blocked_counts", {})
    counts[key] = int(counts.get(key, 0)) + 1


def _target_note_body_shas(storage_root: str | Path, workspace: str, target_notebook: str) -> dict[str, list[str]]:
    root = Path(storage_root).expanduser().resolve()
    safe_workspace = _safe_segment(workspace, "workspace")
    safe_notebook = _safe_segment(target_notebook, "notebook")
    notes_dir = _resolve_inside(root, root / "workspaces" / safe_workspace / "notebooks" / safe_notebook / "notes")
    if not notes_dir.exists() or not notes_dir.is_dir():
        raise ValueError("target_notes_unavailable")
    mapping: dict[str, list[str]] = {}
    for candidate in sorted(notes_dir.glob("*.json")):
        safe_path = _resolve_inside(root, candidate)
        with safe_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("target_note_malformed")
        note_id = _record_id("note", data)
        if not note_id:
            raise ValueError("target_note_missing_id")
        body = _body_for_record("note", data)
        mapping.setdefault(_sha_body(body), []).append(note_id)
    return mapping


def _make_draft(body: str, target: str) -> tuple[str, str, list[str]]:
    lower = body.lower()
    flags = ["historical_note", "human_review_required", "not_current_production_proof", "no_write"]
    for label, tokens, title, summary in TOPIC_RULES:
        if any(token in lower for token in tokens):
            flags.append(f"topic:{label}")
            return title, summary, flags
    if RUSSIAN_RE.search(body):
        title = f"{target} historical Russian-language note — review draft"
        summary = "Историческая русскоязычная запись относится к проектной памяти целевого notebook. Черновик намеренно оставляет только общий смысл: запись может быть полезна как прошлый контекст, но не подтверждает текущий код, продакшен или разрешение на перенос."
    else:
        title = f"{target} historical note — review draft"
        summary = "Историческая запись относится к проектной памяти целевого notebook. Черновик намеренно оставляет только общий смысл: запись может быть полезна как прошлый контекст, но не подтверждает текущий код, продакшен или разрешение на перенос."
    flags.append("topic:generic")
    return title, summary, flags


def _output_is_safe(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return not SENSITIVE_OUTPUT_RE.search(text)


def generate_drafts(
    *,
    manifest: dict[str, Any],
    no_write_auth: dict[str, Any],
    sanitizer_report: dict[str, Any],
    draft_auth: dict[str, Any],
    manifest_sha256: str,
    no_write_auth_sha256: str,
    sanitizer_report_sha256: str,
    draft_auth_sha256: str,
    storage_root: str | Path,
    workspace: str = "default",
) -> dict[str, Any]:
    try:
        if not all(isinstance(value, dict) for value in (manifest, no_write_auth, sanitizer_report, draft_auth)):
            raise ValueError("input_json_object_invalid")
        if manifest_sha256 != EXPECTED_MANIFEST_SHA256:
            raise ValueError("manifest_sha_invalid")
        _validate_draft_authorization(draft_auth, auth_sha256=draft_auth_sha256, no_write_auth_sha256=no_write_auth_sha256, report_sha256=sanitizer_report_sha256)
        _validate_sanitizer_report(sanitizer_report, draft_auth)
        queue = _validate_manifest(manifest, no_write_auth, manifest_sha256=manifest_sha256)
        if len(queue) != EXPECTED_CANDIDATE_COUNT:
            raise ValueError("candidate_count_invalid")
    except (ValueError, TypeError, KeyError):
        return _blocked("manifest_authorization_or_report_invalid")

    payload = _base_payload()
    payload.update({"candidate_count": len(queue), "blocked_count": EXPECTED_BLOCKED_COUNT})
    target_sha_cache: dict[str, dict[str, list[str]]] = {}
    safe_count = 0
    dropped_count = 0

    for item in queue:
        ref = _safe_ref(item["record_ref"])
        target = item["target_canonical_notebook"]
        record_stub = {"record_ref": ref, "metadata_sha256": item["metadata_sha256"], "target_canonical_notebook": target}
        try:
            body = _read_exact_record_body(storage_root, workspace, ref)
            body_sha = _sha_body(body)
            if body_sha != item["metadata_sha256"]:
                raise ValueError("metadata_sha256_mismatch")
            assessment = _assess_body(body)
        except (OSError, json.JSONDecodeError, ValueError):
            payload["integrity_failure_count"] = 1
            _count(payload, "blocked_integrity_failure")
            payload["errors"] = [{"code": "record_missing_malformed_or_hash_mismatch", "message": "draft generation stopped at first integrity failure"}]
            break

        if assessment["indicator_count"] or assessment["uncertain"]:
            _count(payload, "blocked_sensitive_or_uncertain")
            continue

        safe_count += 1
        if ref["notebook"] == target:
            payload["in_place"].append({**record_stub, "decision": "in_place_no_write", "reason": "source_notebook_already_equals_target_canonical_notebook"})
            continue

        try:
            if target not in target_sha_cache:
                target_sha_cache[target] = _target_note_body_shas(storage_root, workspace, target)
            existing_ids = target_sha_cache[target].get(body_sha, [])
        except (OSError, json.JSONDecodeError, ValueError):
            _count(payload, "dedupe_uncertain_no_draft")
            continue

        if existing_ids:
            payload["dedupe_existing"].append({**record_stub, "decision": "exact_duplicate_no_draft", "existing_note_ids": sorted(existing_ids), "reason": "exact_body_sha_already_exists_in_target"})
            continue

        title, summary, flags = _make_draft(body, target)
        draft = {
            **record_stub,
            "decision": "draft_for_human_review_only",
            "proposed_safe_title": title,
            "summary_draft": summary,
            "provenance": {
                "manifest_sha256": manifest_sha256,
                "sanitizer_report_sha256": sanitizer_report_sha256,
                "source_record_sha256_verified": True,
                "dedupe_check": "exact_body_sha_only; no no-duplicate claim",
                "status": "historical note, not current code or production proof",
            },
            "flags": flags,
        }
        if not _output_is_safe(draft):
            dropped_count += 1
            _count(payload, "draft_output_blocked_by_strict_content_filter")
            continue
        payload["drafts"].append(draft)

    payload["safe_count"] = safe_count
    payload["draft_count"] = len(payload["drafts"])
    payload["in_place_count"] = len(payload["in_place"])
    payload["dedupe_count"] = len(payload["dedupe_existing"])
    payload["dedupe_uncertain_count"] = int(payload.get("held_or_blocked_counts", {}).get("dedupe_uncertain_no_draft", 0))
    payload["dropped_count"] = dropped_count
    if payload["integrity_failure_count"] == 0 and safe_count == EXPECTED_SAFE_COUNT:
        payload["ok"] = True
        payload["decision"] = "draft_generation_complete_no_write"
    elif payload["integrity_failure_count"] == 0:
        payload["errors"] = [{"code": "safe_count_mismatch", "message": "draft generation failed closed"}]
    if not _output_is_safe(payload):
        return _blocked("unsafe_output_filter_failed")
    return payload


def emit(payload: dict[str, Any], pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate local no-write human-review drafts for sanitizer-safe records.")
    parser.add_argument("--manifest", required=True, help="fresh v4 pre-migration manifest JSON")
    parser.add_argument("--no-write-authorization", required=True, help="v4 no-write batch sanitizer authorization JSON")
    parser.add_argument("--sanitizer-report", required=True, help="fresh v4 batch sanitizer aggregate report JSON")
    parser.add_argument("--draft-authorization", required=True, help="explicit human-review draft generation authorization JSON")
    parser.add_argument("--storage-root", required=True, help="local storage root; never printed")
    parser.add_argument("--workspace", default="default", help="local workspace name")
    parser.add_argument("--output", required=True, help="safe JSON report output path")
    parser.add_argument("--json", action="store_true", help="also print pretty JSON output")
    args = parser.parse_args(argv)
    try:
        payload = generate_drafts(
            manifest=_load_json(args.manifest),
            no_write_auth=_load_json(args.no_write_authorization),
            sanitizer_report=_load_json(args.sanitizer_report),
            draft_auth=_load_json(args.draft_authorization),
            manifest_sha256=_file_sha256(args.manifest),
            no_write_auth_sha256=_file_sha256(args.no_write_authorization),
            sanitizer_report_sha256=_file_sha256(args.sanitizer_report),
            draft_auth_sha256=_file_sha256(args.draft_authorization),
            storage_root=args.storage_root,
            workspace=args.workspace,
        )
    except (OSError, json.JSONDecodeError, ValueError):
        payload = _blocked("input_read_failed")
    try:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if SENSITIVE_OUTPUT_RE.search(output_text):
            payload = _blocked("unsafe_output_filter_failed")
            output_text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        output_path.write_text(output_text + "\n", encoding="utf-8")
    except OSError:
        payload = _blocked("output_write_failed")
    if args.json:
        emit(payload, True)
    return 0 if payload.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
