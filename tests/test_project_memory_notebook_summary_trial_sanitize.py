from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project_memory_notebook_summary_trial_sanitize.py"
REF = {"notebook": "cc-daemons", "kind": "note", "id": "0f2c83dd6879"}
AUTH_SHA = "auth-sha"


def load_module():
    spec = importlib.util.spec_from_file_location("project_memory_notebook_summary_trial_sanitize_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record_path(root: Path, ref: dict[str, str] | None = None) -> Path:
    selected = ref or REF
    return root / "workspaces" / "default" / "notebooks" / selected["notebook"] / "notes" / "selected.json"


def make_storage(tmp_path: Path, body: str, *, ref: dict[str, str] | None = None, title: str = "DO-NOT-LEAK-TITLE") -> Path:
    root = tmp_path / "knowledge-mcp-fixture-root"
    selected = ref or REF
    write_json(record_path(root, selected), {"note_id": selected["id"], "title": title, "note": body})
    return root


def authorize_temp_body(mod: object, body: str) -> str:
    expected = sha(body)
    mod.EXPECTED_PLAN_SHA256 = expected
    return expected


def authorization_for(mod: object, *, ref: dict[str, str] | None = None, metadata_sha256: str | None = None, schema: str = "project_memory_notebook_summary_trial_authorization.v1") -> dict[str, object]:
    chosen_ref = ref or dict(REF)
    if schema.endswith(".v1"):
        scope = {
            "maximum_selected_records": 1,
            "allowed_disposition": "selected_for_summary_plan",
            "selected_record_refs": [chosen_ref],
        }
    else:
        scope = {
            "maximum_selected_records": 1,
            "allowed_disposition": "selected_for_summary_plan",
            "selected_record_ref": chosen_ref,
            "exact_metadata_sha256": metadata_sha256 or mod.EXPECTED_PLAN_SHA256,
        }
    return {
        "schema": schema,
        "authorization_type": "manual_notebooklm_summary_only_pre_migration_trial",
        "owner": "ser",
        "rollback_owner": "ser",
        "read_only": True,
        "write_performed": False,
        "notebook_mutation_performed": False,
        "automatic_routing_changed": False,
        "production_verified": False,
        "migration_performed": False,
        "execution_blocked": True,
        "requires_owner_confirmation": True,
        "scope": scope,
        "destination_policy": "canonical_only",
        "rollback_scope": "routing_only_no_data_deletion",
        "data_deletion_authorized": False,
        "data_migration_authorized": False,
        "notebook_write_authorized": False,
        "notebook_write_requires": "explicit_per_record_summary_approval",
        "summary_approval": {"approved": False, "approved_record_ref": None},
        "does_not_authorize_notebook_write": True,
    }


def plan_for(body: str, *, ref: dict[str, str] | None = None, candidate_count: int = 1, metadata_sha256: str | None = None, auth_sha: str = AUTH_SHA, auth_schema: str = "project_memory_notebook_summary_trial_authorization.v1") -> dict[str, object]:
    return {
        "schema": "project_memory_notebook_summary_trial_plan.v1",
        "ok": True,
        "candidate_count": candidate_count,
        "plan_status": "draft_for_human_summary_review",
        "destination_policy": "canonical_only",
        "rollback_scope": "routing_only_no_data_deletion",
        "writes_unapproved": True,
        "read_only": True,
        "requires_owner_confirmation": True,
        "execution_blocked": True,
        "write_performed": False,
        "notebook_mutation_performed": False,
        "automatic_routing_changed": False,
        "migration_performed": False,
        "data_deletion_authorized": False,
        "data_migration_authorized": False,
        "notebook_write_authorized": False,
        "notebook_write_performed": False,
        "production_verified": False,
        "authorization": {
            "schema": auth_schema,
            "authorization_sha256": auth_sha,
            "notebook_write_authorized": False,
            "does_not_authorize_notebook_write": True,
        },
        "trial_plan": {
            "plan_status": "draft_for_human_summary_review",
            "record_ref": ref or dict(REF),
            "metadata_sha256": metadata_sha256 or sha(body),
            "target_canonical_notebook": "nmbot",
            "destination_policy": "canonical_only",
            "rollback_scope": "routing_only_no_data_deletion",
            "human_summary_review_required": True,
            "notebook_write_authorized": False,
        },
    }


def test_safe_record_success_verifies_sha_and_preserves_no_write_flags(tmp_path: Path) -> None:
    mod = load_module()
    body = "Historical migration note for one local summary candidate. No operational data included."
    expected = authorize_temp_body(mod, body)
    root = make_storage(tmp_path, body)

    payload = mod.sanitize_trial(plan_for(body, metadata_sha256=expected), authorization_for(mod), authorization_sha256=AUTH_SHA, storage_root=root)

    assert payload["ok"] is True
    assert payload["decision"] == "safe_for_human_summary_draft"
    assert payload["candidate_count"] == 1
    assert payload["metadata_sha256"] == expected
    assert payload["record_metadata_sha256_verified"] is True
    assert payload["selected_record_ref"] == REF
    assert payload["notebook_write_remains_unauthorized"] is True
    for key in (
        "write_performed",
        "notebook_mutation_performed",
        "migration_performed",
        "data_deletion_authorized",
        "data_migration_authorized",
        "notebook_write_authorized",
        "notebook_write_performed",
        "automatic_routing_changed",
    ):
        assert payload[key] is False
    assert payload["assessment"]["indicator_count"] == 0
    assert payload["assessment"]["uncertain"] is False


def test_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    mod = load_module()
    authorize_temp_body(mod, "other expected body")
    root = make_storage(tmp_path, "Safe local body.")

    payload = mod.sanitize_trial(plan_for("other expected body"), authorization_for(mod), authorization_sha256=AUTH_SHA, storage_root=root)

    assert payload["ok"] is False
    assert payload["decision"] == "blocked_sensitive_or_uncertain"
    assert payload["record_metadata_sha256_verified"] is False
    assert payload["notebook_write_authorized"] is False


def test_sensitive_indicator_blocks_after_sha_success(tmp_path: Path) -> None:
    mod = load_module()
    body = "Migration draft must not pass when api_key = DO-NOT-LEAK-SECRET-FIXTURE appears."
    authorize_temp_body(mod, body)
    root = make_storage(tmp_path, body)

    payload = mod.sanitize_trial(plan_for(body), authorization_for(mod), authorization_sha256=AUTH_SHA, storage_root=root)

    assert payload["ok"] is False
    assert payload["record_metadata_sha256_verified"] is True
    assert payload["assessment"]["contains_sensitive_indicator"] is True
    assert payload["assessment"]["indicator_count"] >= 1


def test_missing_record_and_malformed_record_fail_closed(tmp_path: Path) -> None:
    mod = load_module()
    body = "Safe local body."
    authorize_temp_body(mod, body)
    missing_root = tmp_path / "missing-root"
    assert mod.sanitize_trial(plan_for(body), authorization_for(mod), authorization_sha256=AUTH_SHA, storage_root=missing_root)["ok"] is False

    root = tmp_path / "knowledge-mcp-fixture-root"
    bad_path = record_path(root)
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("{not valid json", encoding="utf-8")
    assert mod.sanitize_trial(plan_for(body), authorization_for(mod), authorization_sha256=AUTH_SHA, storage_root=root)["ok"] is False


def test_wrong_or_multiple_candidate_fails_closed(tmp_path: Path) -> None:
    mod = load_module()
    body = "Safe local body."
    authorize_temp_body(mod, body)
    root = make_storage(tmp_path, body)

    wrong = dict(REF)
    wrong["id"] = "different"
    assert mod.sanitize_trial(plan_for(body, ref=wrong), authorization_for(mod), authorization_sha256=AUTH_SHA, storage_root=root)["ok"] is False
    assert mod.sanitize_trial(plan_for(body, candidate_count=2), authorization_for(mod), authorization_sha256=AUTH_SHA, storage_root=root)["ok"] is False


def test_expected_plan_sha_is_the_only_authorized_metadata_sha() -> None:
    mod = load_module()
    ref, metadata_sha256 = mod._validate_plan(plan_for("fixture", metadata_sha256=mod.EXPECTED_PLAN_SHA256), authorization_for(mod), authorization_sha256=AUTH_SHA)

    assert ref == REF
    assert metadata_sha256 == mod.EXPECTED_PLAN_SHA256


def test_correct_ref_with_alternative_valid_sha_is_rejected_before_storage_read(tmp_path: Path) -> None:
    mod = load_module()
    alternative_sha = "0" * 64
    assert alternative_sha != mod.EXPECTED_PLAN_SHA256

    def fail_if_storage_is_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("storage read must not happen for unauthorized metadata SHA")

    mod._find_record_body = fail_if_storage_is_read
    payload = mod.sanitize_trial(plan_for("fixture", metadata_sha256=alternative_sha), authorization_for(mod), authorization_sha256=AUTH_SHA, storage_root=tmp_path)

    assert payload["ok"] is False
    assert payload["decision"] == "blocked_sensitive_or_uncertain"
    assert payload["metadata_sha256"] is None
    assert payload["record_metadata_sha256_verified"] is False


def test_v2_authorization_hash_ref_and_sha_are_checked_before_storage(tmp_path: Path) -> None:
    mod = load_module()
    body = "Safe local body."
    expected = authorize_temp_body(mod, body)
    v2_schema = "project_memory_notebook_summary_trial_authorization.v2"
    v2_auth = authorization_for(mod, metadata_sha256=expected, schema=v2_schema)

    def fail_if_storage_is_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("storage read must not happen before authorization binding succeeds")

    mod._find_record_body = fail_if_storage_is_read

    wrong_hash = mod.sanitize_trial(
        plan_for(body, metadata_sha256=expected, auth_schema=v2_schema, auth_sha="wrong-auth-sha"),
        v2_auth,
        authorization_sha256=AUTH_SHA,
        storage_root=tmp_path,
    )
    assert wrong_hash["ok"] is False

    wrong_ref = dict(REF)
    wrong_ref["id"] = "20a382a56022"
    wrong_ref_result = mod.sanitize_trial(
        plan_for(body, ref=wrong_ref, metadata_sha256=expected, auth_schema=v2_schema),
        v2_auth,
        authorization_sha256=AUTH_SHA,
        storage_root=tmp_path,
    )
    assert wrong_ref_result["ok"] is False

    wrong_sha_auth = authorization_for(mod, metadata_sha256="0" * 64, schema=v2_schema)
    wrong_sha_result = mod.sanitize_trial(
        plan_for(body, metadata_sha256=expected, auth_schema=v2_schema),
        wrong_sha_auth,
        authorization_sha256=AUTH_SHA,
        storage_root=tmp_path,
    )
    assert wrong_sha_result["ok"] is False
    for payload in (wrong_hash, wrong_ref_result, wrong_sha_result):
        combined = json.dumps(payload, ensure_ascii=False)
        assert not __import__("re").search(r"[0-9a-f]{64}", combined)


def test_v2_success_reports_the_authorized_record_ref(tmp_path: Path) -> None:
    mod = load_module()
    body = "Safe local body."
    v2_ref = {"notebook": "cc-daemons", "kind": "note", "id": "20a382a56022"}
    expected = sha(body)
    root = make_storage(tmp_path, body, ref=v2_ref)
    authorization = authorization_for(
        mod,
        ref=v2_ref,
        metadata_sha256=expected,
        schema="project_memory_notebook_summary_trial_authorization.v2",
    )
    payload = mod.sanitize_trial(
        plan_for(
            body,
            ref=v2_ref,
            metadata_sha256=expected,
            auth_schema="project_memory_notebook_summary_trial_authorization.v2",
        ),
        authorization,
        authorization_sha256=AUTH_SHA,
        storage_root=root,
    )

    assert payload["ok"] is True
    assert payload["selected_record_ref"] == v2_ref


def test_v3_success_reports_exact_authorized_record_ref(tmp_path: Path) -> None:
    mod = load_module()
    body = "Safe local body for v3 summary candidate."
    v3_ref = {"notebook": "cc-daemons", "kind": "note", "id": "2a7edfa48439"}
    expected = sha(body)
    root = make_storage(tmp_path, body, ref=v3_ref)
    authorization = authorization_for(
        mod,
        ref=v3_ref,
        metadata_sha256=expected,
        schema="project_memory_notebook_summary_trial_authorization.v3",
    )
    authorization["scope"]["exact_target_canonical_notebook"] = "nmbot"
    payload = mod.sanitize_trial(
        plan_for(
            body,
            ref=v3_ref,
            metadata_sha256=expected,
            auth_schema="project_memory_notebook_summary_trial_authorization.v3",
        ),
        authorization,
        authorization_sha256=AUTH_SHA,
        storage_root=root,
    )

    assert payload["ok"] is True
    assert payload["selected_record_ref"] == v3_ref
    assert payload["metadata_sha256"] == expected


def test_v3_authorization_hash_ref_sha_and_target_are_checked_before_storage(tmp_path: Path) -> None:
    mod = load_module()
    body = "Safe local body for v3 binding."
    v3_ref = {"notebook": "cc-daemons", "kind": "note", "id": "2a7edfa48439"}
    expected = sha(body)
    v3_schema = "project_memory_notebook_summary_trial_authorization.v3"
    v3_auth = authorization_for(mod, ref=v3_ref, metadata_sha256=expected, schema=v3_schema)
    v3_auth["scope"]["exact_target_canonical_notebook"] = "nmbot"

    def fail_if_storage_is_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("storage read must not happen before v3 authorization binding succeeds")

    mod._find_record_body = fail_if_storage_is_read

    wrong_hash = mod.sanitize_trial(
        plan_for(body, ref=v3_ref, metadata_sha256=expected, auth_schema=v3_schema, auth_sha="wrong-auth-sha"),
        v3_auth,
        authorization_sha256=AUTH_SHA,
        storage_root=tmp_path,
    )
    wrong_ref = dict(v3_ref)
    wrong_ref["id"] = "20a382a56022"
    wrong_ref_result = mod.sanitize_trial(
        plan_for(body, ref=wrong_ref, metadata_sha256=expected, auth_schema=v3_schema),
        v3_auth,
        authorization_sha256=AUTH_SHA,
        storage_root=tmp_path,
    )
    wrong_sha_auth = authorization_for(mod, ref=v3_ref, metadata_sha256="0" * 64, schema=v3_schema)
    wrong_sha_auth["scope"]["exact_target_canonical_notebook"] = "nmbot"
    wrong_sha_result = mod.sanitize_trial(
        plan_for(body, ref=v3_ref, metadata_sha256=expected, auth_schema=v3_schema),
        wrong_sha_auth,
        authorization_sha256=AUTH_SHA,
        storage_root=tmp_path,
    )
    wrong_target_auth = authorization_for(mod, ref=v3_ref, metadata_sha256=expected, schema=v3_schema)
    wrong_target_auth["scope"]["exact_target_canonical_notebook"] = "cc-daemons"
    wrong_target_result = mod.sanitize_trial(
        plan_for(body, ref=v3_ref, metadata_sha256=expected, auth_schema=v3_schema),
        wrong_target_auth,
        authorization_sha256=AUTH_SHA,
        storage_root=tmp_path,
    )

    for payload in (wrong_hash, wrong_ref_result, wrong_sha_result, wrong_target_result):
        assert payload["ok"] is False
        assert payload["record_metadata_sha256_verified"] is False
        combined = json.dumps(payload, ensure_ascii=False)
        assert not __import__("re").search(r"[0-9a-f]{64}", combined)


def test_cli_output_does_not_leak_title_body_root_path_secret_or_detection_pattern(tmp_path: Path) -> None:
    mod = load_module()
    body = "Sensitive marker api_key = DO-NOT-LEAK-SECRET-FIXTURE should be hidden."
    title = "DO-NOT-LEAK-TITLE"
    root = make_storage(tmp_path, body, title=title)
    auth_path = tmp_path / "authorization.json"
    write_json(auth_path, authorization_for(mod))
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, plan_for(body, metadata_sha256=mod.EXPECTED_PLAN_SHA256, auth_sha=file_sha(auth_path)))

    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--plan", str(plan_path), "--authorization", str(auth_path), "--storage-root", str(root), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert run.returncode == 2
    combined = run.stdout + run.stderr
    assert title not in combined
    assert body not in combined
    assert str(root) not in combined
    assert str(record_path(root)) not in combined
    assert "DO-NOT-LEAK-SECRET-FIXTURE" not in combined
    assert "api_key" not in combined.lower()
    payload = json.loads(run.stdout)
    assert payload["decision"] == "blocked_sensitive_or_uncertain"
    assert payload["metadata_sha256"] is None


def test_no_dangerous_imports() -> None:
    banned = [
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import urllib",
        "from urllib",
        "import socket",
        "from socket",
        "import notebooklm",
        "from notebooklm",
        "import mempalace",
        "from mempalace",
        "import nmbot_context_gate",
        "from nmbot_context_gate",
        "import nmbot_runtime",
        "from nmbot_runtime",
    ]
    source = SCRIPT.read_text(encoding="utf-8").lower()
    assert not any(token in source for token in banned)
