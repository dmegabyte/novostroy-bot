#!/usr/bin/env python3
"""Validate a reviewed summary authorization and emit a local write request.

This is a stdlib-only, local-only planner. It does not call NotebookLM, MCP,
network clients or subprocesses, and it does not write any NotebookLM record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTHORIZATION = ROOT / "data" / "notebooklm_summary_trial_write_authorization_safe_v3.json"

REQUEST_SCHEMA = "project_memory_notebook_summary_trial_write_request.safe_v1"
AUTHORIZATION_TYPE = "manual_notebooklm_single_note_write_after_review"
OWNER = "ser"
TARGET_NOTEBOOK = "nmbot"
DESTINATION_POLICY = "canonical_only"
ALLOWED_OPERATION = "add_note"
WRITE_COUNT = 1

EXACT_AUTHORIZATIONS: dict[str, dict[str, Any]] = {
    "project_memory_notebook_summary_trial_write_authorization.safe_v2": {
        "safe_title": "NMBot V2 local simplification — historical note",
        "source_ref": {"notebook": "cc-daemons", "kind": "note", "id": "20a382a56022"},
        "source_sha256": "edeec9c399199c99f278b99dc161129cecd9f756114c2d1e2ad3ebc3ab1720dc",
        "approved_summary": (
            "Историческая запись от 2026-07-17 фиксирует локальное упрощение NMBot V2: "
            "runtime был сведён к более линейной обработке. Это не подтверждает текущее "
            "состояние кода или продакшена. Изменения оставались локальными, без deploy, "
            "SSH, restart, сети и eval.\n\n"
            "---\n"
            "Provenance: source record cc-daemons/note/20a382a56022; source SHA edeec9...720dc; "
            "status: historical note, not current code or production proof."
        ),
        "approved_summary_sha256": "b0859157a9722c86dac9226ace295ac2d45a5336e41b1b4fced3453870052a6d",
    },
    "project_memory_notebook_summary_trial_write_authorization.safe_v3": {
        "safe_title": "NMBot model-only comparison — historical note",
        "source_ref": {"notebook": "cc-daemons", "kind": "note", "id": "41b55e418687"},
        "source_sha256": "9a51bdf4759c60255ff0fd1d6121acf6adcea1bee38a4dc3b965d2912e8b8d32",
        "approved_summary": (
            "Историческая заметка фиксирует model-only сравнение нескольких моделей на одном "
            "неизменном сценарии без изменений кода, eval или production. Результаты были "
            "неоднородными: встречались слабый либо невалидный формат, пропуск важной семейной "
            "инфраструктуры и неподтверждённые детали. Вывод — нужен более строгий "
            "детерминированный контракт входных данных и recipe.\n\n"
            "---\n"
            "Provenance: source record cc-daemons/note/41b55e418687; source SHA "
            "9a51bdf4759c60255ff0fd1d6121acf6adcea1bee38a4dc3b965d2912e8b8d32; "
            "status: historical note, not current code or production proof."
        ),
        "approved_summary_sha256": "36c4d71c1fb56059ced3539963b2d01dd324af2564f06ceb95830bb0c49f8f0b",
    },
    "project_memory_notebook_summary_trial_write_authorization.safe_v4": {
        "safe_title": "One-model search and answer hypothesis — historical note",
        "source_ref": {"notebook": "cc-daemons", "kind": "note", "id": "531b4257e75b"},
        "source_sha256": "c4bd8dfc38fd5a72b0bebbbc996eb43598b9fc525daf0998d4d9fb9931b74655",
        "approved_summary": (
            "Историческая запись описывает локальную проверку идеи использовать одну модель "
            "одновременно для поиска и финального ответа. Такой подход показал слабое качество "
            "поиска, поэтому предпочтительным остался разделённый вариант: отдельный поиск и "
            "отдельный слой ответа.\n\n"
            "---\n"
            "Provenance: source record cc-daemons/note/531b4257e75b; source SHA "
            "c4bd8dfc38fd5a72b0bebbbc996eb43598b9fc525daf0998d4d9fb9931b74655; "
            "status: historical note, not current code or production proof."
        ),
        "approved_summary_sha256": "bf963b40949722e65a85aa54081522be022388cc11cd469b561c8cf3bf9b5c69",
    },
    "project_memory_notebook_summary_trial_write_authorization.safe_v5": {
        "safe_title": "Optional reasoning layer MVP — historical note",
        "source_ref": {"notebook": "cc-daemons", "kind": "note", "id": "5d2aab92d9f2"},
        "source_sha256": "6b6cf5b4ad62f6426835b3fa9bdc867620ef1ba1f7529334b2aa00df8db0b18a",
        "approved_summary": (
            "Историческая запись описывает локальный опциональный слой для формирования "
            "сравнительной подачи вариантов без изменения базового поведения. Были предусмотрены "
            "защитные проверки и fallback; без явного флага слой оставался выключенным.\n\n"
            "---\n"
            "Provenance: source record cc-daemons/note/5d2aab92d9f2; source SHA "
            "6b6cf5b4ad62f6426835b3fa9bdc867620ef1ba1f7529334b2aa00df8db0b18a; "
            "status: historical note, not current code or production proof."
        ),
        "approved_summary_sha256": "25928cd7a60a0992e6d95a9f8c0d6514a6480d180ce3bf0d960c789332ccc076",
    },
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BANNED_KEYS = {"body", "raw", "transcript", "log", "logs", "path", "storage_root", "secret", "secrets"}
BANNED_CONTENT_MARKERS = ("raw source", "transcript", "storage root", "secret", "password", "token")


def _read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _base_payload() -> dict[str, Any]:
    return {
        "schema": REQUEST_SCHEMA,
        "ok": False,
        "write_count": 0,
        "target_notebook": TARGET_NOTEBOOK,
        "destination_policy": DESTINATION_POLICY,
        "allowed_operation": ALLOWED_OPERATION,
        "notebook_write_performed": False,
        "data_deletion_authorized": False,
        "source_mutation_authorized": False,
        "routing_change_authorized": False,
        "production_claim_authorized": False,
        "errors": [],
    }


def _error(code: str) -> dict[str, Any]:
    payload = _base_payload()
    payload["denied_reason"] = code
    payload["errors"] = [{"code": code, "message": "reviewed summary write request denied"}]
    return payload


def _ensure_no_unsafe_keys(value: Any, marker: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in BANNED_KEYS:
                raise ValueError(f"unsafe_key:{marker}.{key}")
            _ensure_no_unsafe_keys(child, f"{marker}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _ensure_no_unsafe_keys(child, f"{marker}[{index}]")


def _validate_exact_ref(value: Any, expected_ref: dict[str, str]) -> None:
    if not isinstance(value, dict) or set(value) != {"notebook", "kind", "id"}:
        raise ValueError("source_ref_invalid")
    if value != expected_ref:
        raise ValueError("source_ref_mismatch")


def _validate_summary(value: Any, expected_sha: Any, contract: dict[str, Any]) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("summary_invalid")
    lowered = value.lower()
    if any(marker in lowered for marker in BANNED_CONTENT_MARKERS):
        raise ValueError("unsafe_summary_content")
    approved_summary = contract["approved_summary"]
    approved_summary_sha256 = contract["approved_summary_sha256"]
    if value != approved_summary:
        raise ValueError("summary_not_exactly_approved")
    if expected_sha != approved_summary_sha256 or not SHA256_RE.match(str(expected_sha)):
        raise ValueError("summary_sha_mismatch")
    if _sha256_text(value) != approved_summary_sha256:
        raise ValueError("summary_hash_failed")


def validate_and_build_write_request(authorization: dict[str, Any], *, authorization_sha256: str) -> dict[str, Any]:
    try:
        _ensure_no_unsafe_keys(authorization)
        expected_keys = {
            "schema",
            "authorization_type",
            "owner",
            "rollback_owner",
            "target_notebook",
            "destination_policy",
            "allowed_operation",
            "maximum_write_count",
            "write_permitted",
            "write_performed",
            "notebook_write_authorized",
            "notebook_write_performed",
            "data_deletion_authorized",
            "source_mutation_authorized",
            "routing_change_authorized",
            "production_claim_authorized",
            "migration_performed",
            "automatic_routing_changed",
            "production_verified",
            "scope",
            "approved_summary",
            "boundaries",
        }
        if set(authorization) != expected_keys:
            raise ValueError("authorization_keys_invalid")
        auth_schema = authorization.get("schema")
        contract = EXACT_AUTHORIZATIONS.get(str(auth_schema))
        if contract is None:
            raise ValueError("authorization_schema_invalid")
        if authorization.get("authorization_type") != AUTHORIZATION_TYPE:
            raise ValueError("authorization_type_invalid")
        if authorization.get("owner") != OWNER or authorization.get("rollback_owner") != OWNER:
            raise ValueError("authorization_owner_mismatch")
        if authorization.get("target_notebook") != TARGET_NOTEBOOK:
            raise ValueError("target_notebook_mismatch")
        if authorization.get("destination_policy") != DESTINATION_POLICY:
            raise ValueError("destination_policy_mismatch")
        if authorization.get("allowed_operation") != ALLOWED_OPERATION:
            raise ValueError("operation_mismatch")
        if authorization.get("maximum_write_count") != WRITE_COUNT:
            raise ValueError("write_count_mismatch")
        for key in ("write_permitted", "notebook_write_authorized"):
            if authorization.get(key) is not True:
                raise ValueError(f"required_flag_mismatch:{key}")
        for key in (
            "write_performed",
            "notebook_write_performed",
            "data_deletion_authorized",
            "source_mutation_authorized",
            "routing_change_authorized",
            "production_claim_authorized",
            "migration_performed",
            "automatic_routing_changed",
            "production_verified",
        ):
            if authorization.get(key) is not False:
                raise ValueError(f"failure_flag_mismatch:{key}")
        boundaries = authorization.get("boundaries")
        if not isinstance(boundaries, dict) or set(boundaries) != {
            "exactly_one_new_notebooklm_note_only",
            "no_deletion",
            "no_source_mutation",
            "no_routing_change",
            "no_production_claim",
        }:
            raise ValueError("boundaries_invalid")
        if not all(boundaries.get(key) is True for key in boundaries):
            raise ValueError("boundary_flag_mismatch")
        scope = authorization.get("scope")
        if not isinstance(scope, dict) or set(scope) != {
            "explicit_record_count",
            "source_ref",
            "source_sha256",
            "approved_summary_sha256",
        }:
            raise ValueError("scope_invalid")
        if scope.get("explicit_record_count") != WRITE_COUNT:
            raise ValueError("record_count_mismatch")
        _validate_exact_ref(scope.get("source_ref"), contract["source_ref"])
        if scope.get("source_sha256") != contract["source_sha256"] or not SHA256_RE.match(str(scope.get("source_sha256"))):
            raise ValueError("source_sha_mismatch")
        _validate_summary(authorization.get("approved_summary"), scope.get("approved_summary_sha256"), contract)
    except ValueError as exc:
        return _error(str(exc))

    payload = _base_payload()
    payload.update(
        {
            "ok": True,
            "write_count": WRITE_COUNT,
            "write_request": {
                "target_notebook": TARGET_NOTEBOOK,
                "operation": ALLOWED_OPERATION,
                "title": contract["safe_title"],
                "content": contract["approved_summary"],
                "source_ref": dict(contract["source_ref"]),
                "source_sha256": contract["source_sha256"],
                "provenance": {
                    "authorization_schema": auth_schema,
                    "authorization_sha256": authorization_sha256,
                    "approved_summary_sha256": contract["approved_summary_sha256"],
                    "destination_policy": DESTINATION_POLICY,
                    "owner": OWNER,
                    "rollback_owner": OWNER,
                },
            },
            "errors": [],
        }
    )
    return payload


def emit(payload: dict[str, Any], output: str | None, pretty: bool) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a local safe NotebookLM add_note request for the approved summary.")
    parser.add_argument("--authorization", default=str(DEFAULT_AUTHORIZATION), help="reviewed one-note write authorization JSON path")
    parser.add_argument("--output", default=None, help="optional local JSON output path; default is stdout")
    parser.add_argument("--json", action="store_true", help="pretty JSON output")
    args = parser.parse_args(argv)

    try:
        payload = validate_and_build_write_request(_read_json(args.authorization), authorization_sha256=_file_sha256(args.authorization))
    except (OSError, json.JSONDecodeError):
        payload = _error("input_read_failed")
    emit(payload, args.output, args.json)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
