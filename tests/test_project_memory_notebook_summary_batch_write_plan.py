from __future__ import annotations

import ast
import contextlib
import copy
import io
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project_memory_notebook_summary_batch_write_plan.py"
WRITE_AUTHORIZATION = ROOT / "data" / "notebooklm_summary_batch_write_authorization_safe_v1.json"
V4_NO_WRITE_AUTHORIZATION = ROOT / "data" / "notebooklm_summary_batch_sanitize_authorization_v4.json"
DRAFT_AUTHORIZATION = ROOT / "data" / "notebooklm_summary_draft_generation_authorization_v1.json"
OUTCOME_LEDGER = ROOT / "data" / "notebooklm_summary_batch_write_outcome_20260727.json"

EXPECTED_SOURCE_IDS = [
    "63fe2a82d388",
    "6b009e73dc4f",
    "780baff13512",
    "7c55cb6a2e02",
    "8598e0403dcc",
    "9fa4704474e3",
    "a8ddd44552de",
    "f502ffe22e53",
    "fe717093b1e9",
]

USE_DEFAULT_LEDGER = object()


def load_module():
    spec = importlib.util.spec_from_file_location("project_memory_notebook_summary_batch_write_plan_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build(write_auth: dict | None = None, v4_auth: dict | None = None, draft_auth: dict | None = None) -> dict:
    mod = load_module()
    return mod.validate_and_build_batch_write_plan(
        write_auth or load_json(WRITE_AUTHORIZATION),
        v4_auth or load_json(V4_NO_WRITE_AUTHORIZATION),
        draft_auth or load_json(DRAFT_AUTHORIZATION),
        write_auth_sha256="write-auth-sha",
        v4_no_write_auth_sha256=mod.EXPECTED_V4_NO_WRITE_AUTH_SHA256,
        draft_auth_sha256=mod.EXPECTED_DRAFT_AUTH_SHA256,
    )


def consumed(write_auth: dict | None = None, outcome_ledger: object = USE_DEFAULT_LEDGER, write_auth_sha256: str | None = None) -> dict:
    mod = load_module()
    return mod.validate_consumed_or_build_batch_write_plan(
        write_auth or load_json(WRITE_AUTHORIZATION),
        load_json(V4_NO_WRITE_AUTHORIZATION),
        load_json(DRAFT_AUTHORIZATION),
        outcome_ledger=load_json(OUTCOME_LEDGER) if outcome_ledger is USE_DEFAULT_LEDGER else outcome_ledger,
        write_auth_sha256=write_auth_sha256 or mod.EXPECTED_WRITE_AUTHORIZATION_SHA256,
        v4_no_write_auth_sha256=mod.EXPECTED_V4_NO_WRITE_AUTH_SHA256,
        draft_auth_sha256=mod.EXPECTED_DRAFT_AUTH_SHA256,
    )


def test_batch_authorization_emits_nine_safe_aggregate_operations() -> None:
    payload = build()

    assert payload["ok"] is True
    assert payload["write_count"] == 9
    assert payload["target_notebook"] == "nmbot"
    assert payload["allowed_operation"] == "add_note"
    assert payload["notebook_write_authorized"] is True
    assert payload["notebook_write_performed"] is False
    assert payload["data_deletion_authorized"] is False
    assert payload["source_mutation_authorized"] is False
    assert payload["routing_change_authorized"] is False
    assert payload["production_claim_authorized"] is False
    assert payload["manifest_sha256_verified"] is True
    assert payload["v4_no_write_authorization_sha256_verified"] is True
    assert payload["draft_authorization_sha256_verified"] is True
    assert [item["source_ref"]["id"] for item in payload["operations"]] == EXPECTED_SOURCE_IDS
    assert all(item["operation"] == "add_note" for item in payload["operations"])
    assert all(item["target_notebook"] == "nmbot" for item in payload["operations"])
    assert all(set(item) == {"operation_id", "operation", "target_notebook", "source_ref", "source_sha256", "title_sha256", "content_sha256"} for item in payload["operations"])


def test_default_cli_is_consumed_noop_and_outputs_no_operations_or_bodies() -> None:
    mod = load_module()
    stdout = io.StringIO()

    with contextlib.redirect_stdout(stdout):
        return_code = mod.main(["--json"])

    assert return_code == 2
    payload = json.loads(stdout.getvalue())
    assert payload["ok"] is False
    assert payload["decision"] == "batch_already_applied"
    assert payload["idempotency_blocked"] is True
    assert payload["notebook_write_authorized"] is False
    assert payload["write_count"] == 0
    assert payload["operations"] == []
    assert payload["outcome_ledger_verified"] is True
    lowered = stdout.getvalue().lower()
    for forbidden in (
        "историческая заметка про",
        "историческая запись описывает",
        "nmbot v0: локальный тестовый контур",
        "raw source",
        "storage root",
        "transcript",
        "secret",
    ):
        assert forbidden not in lowered


def test_exact_valid_ledger_blocks_authorized_batch() -> None:
    payload = consumed()

    assert payload["ok"] is False
    assert payload["decision"] == "batch_already_applied"
    assert payload["idempotency_blocked"] is True
    assert payload["write_count"] == 0
    assert payload["operations"] == []


def test_checked_in_ledger_content_is_exact_and_valid() -> None:
    mod = load_module()
    ledger = load_json(OUTCOME_LEDGER)

    mod.validate_outcome_ledger(ledger, write_auth_sha256=mod.EXPECTED_WRITE_AUTHORIZATION_SHA256)
    assert set(ledger) == {
        "schema",
        "workspace",
        "target_notebook",
        "write_authorization_sha256",
        "write_count",
        "all_metadata_sha256_verified",
        "source_mutation_performed",
        "data_deletion_performed",
        "automatic_routing_changed",
        "production_claim_made",
        "records",
    }
    assert [record["source_id"] for record in ledger["records"]] == EXPECTED_SOURCE_IDS
    assert len({record["note_id"] for record in ledger["records"]}) == 9
    assert all(record["metadata_verified"] is True for record in ledger["records"])


@pytest.mark.parametrize(
    ("ledger", "reason"),
    [
        (None, "outcome_ledger_not_dict"),
        ([{"records": []}], "outcome_ledger_not_dict"),
        ({"schema": "bad"}, "outcome_ledger_keys_invalid"),
    ],
)
def test_missing_malformed_or_non_dict_ledger_fails_closed(ledger: object, reason: str) -> None:
    payload = consumed(outcome_ledger=ledger)

    assert payload["ok"] is False
    assert payload["denied_reason"] == reason
    assert payload["notebook_write_authorized"] is False
    assert payload["write_count"] == 0
    assert payload["operations"] == []


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda d: d.__setitem__("write_count", 8), "outcome_ledger_count_or_metadata_invalid"),
        (lambda d: d.__setitem__("source_mutation_performed", True), "outcome_ledger_forbidden_flag:source_mutation_performed"),
        (lambda d: d.__setitem__("data_deletion_performed", True), "outcome_ledger_forbidden_flag:data_deletion_performed"),
        (lambda d: d.__setitem__("automatic_routing_changed", True), "outcome_ledger_forbidden_flag:automatic_routing_changed"),
        (lambda d: d.__setitem__("production_claim_made", True), "outcome_ledger_forbidden_flag:production_claim_made"),
        (lambda d: d["records"].pop(), "outcome_ledger_record_count_invalid"),
        (lambda d: d["records"][0].__setitem__("source_id", "bad-source"), "outcome_ledger_source_id_mismatch"),
        (lambda d: d["records"][0].__setitem__("content_sha256", "0" * 64), "outcome_ledger_content_sha_mismatch"),
        (lambda d: d["records"][0].__setitem__("note_id", d["records"][1]["note_id"]), "outcome_ledger_duplicate_note_id"),
        (lambda d: d["records"][0].__setitem__("note_id", "unsafe-note-id"), "outcome_ledger_note_id_unsafe"),
        (lambda d: d["records"][0].__setitem__("metadata_verified", False), "outcome_ledger_metadata_not_verified"),
    ],
)
def test_wrong_ledger_values_fail_closed(mutate, reason: str) -> None:
    ledger = copy.deepcopy(load_json(OUTCOME_LEDGER))
    mutate(ledger)

    payload = consumed(outcome_ledger=ledger)

    assert payload["ok"] is False
    assert payload["denied_reason"] == reason
    assert payload["notebook_write_authorized"] is False
    assert payload["write_count"] == 0
    assert payload["operations"] == []


def test_wrong_write_authorization_sha_in_ledger_fails_closed() -> None:
    ledger = load_json(OUTCOME_LEDGER)
    ledger["write_authorization_sha256"] = "0" * 64

    payload = consumed(outcome_ledger=ledger)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "outcome_ledger_authorization_sha_mismatch"


def test_tampered_authorization_is_denied_before_consumed_status() -> None:
    auth = load_json(WRITE_AUTHORIZATION)
    auth["user_approval"]["approved"] = False

    payload = consumed(write_auth=auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "explicit_user_approval_missing"
    assert payload["decision"] == "blocked_or_failed_closed"
    assert payload["idempotency_blocked"] is False


def test_tampered_v4_source_sha_is_denied() -> None:
    v4 = load_json(V4_NO_WRITE_AUTHORIZATION)
    for item in v4["scope"]["selected_records"]:
        if item["record_ref"]["id"] == "63fe2a82d388":
            item["metadata_sha256"] = "0" * 64

    payload = build(v4_auth=v4)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "source_not_bound_to_v4_authorization"
    assert payload["write_count"] == 0


def test_duplicate_operation_id_is_denied() -> None:
    auth = load_json(WRITE_AUTHORIZATION)
    auth["operations"][1]["operation_id"] = auth["operations"][0]["operation_id"]

    payload = build(write_auth=auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] in {"operation_identity_mismatch", "duplicate_operation_id"}


def test_unsafe_or_missing_provenance_footer_is_denied() -> None:
    auth = load_json(WRITE_AUTHORIZATION)
    auth["operations"][0]["content"] = auth["operations"][0]["content"].replace(
        "not current code or production proof.",
        "current production proof.",
    )

    payload = build(write_auth=auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "content_sha_mismatch"


def test_missing_explicit_user_approval_is_denied() -> None:
    auth = load_json(WRITE_AUTHORIZATION)
    auth["user_approval"]["approved"] = False

    payload = build(write_auth=auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "explicit_user_approval_missing"


def test_wrong_draft_authorization_sha_is_denied() -> None:
    mod = load_module()
    payload = mod.validate_and_build_batch_write_plan(
        load_json(WRITE_AUTHORIZATION),
        load_json(V4_NO_WRITE_AUTHORIZATION),
        load_json(DRAFT_AUTHORIZATION),
        write_auth_sha256="write-auth-sha",
        v4_no_write_auth_sha256=mod.EXPECTED_V4_NO_WRITE_AUTH_SHA256,
        draft_auth_sha256="0" * 64,
    )

    assert payload["ok"] is False
    assert payload["denied_reason"] == "draft_authorization_invalid"


def test_no_dangerous_imports_or_execution_tools() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imports: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                calls.add(func.attr)
            elif isinstance(func, ast.Name):
                calls.add(func.id)

    assert not ({"socket", "requests", "httpx", "urllib", "subprocess", "notebooklm_mcp", "notebooklm"} & imports)
    assert not ({"system", "popen", "run", "check_call", "check_output", "urlopen"} & calls)
