from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project_memory_notebook_summary_trial_plan.py"
AUTHORIZATION = ROOT / "data" / "notebooklm_summary_trial_authorization_v1.json"
AUTHORIZATION_V2 = ROOT / "data" / "notebooklm_summary_trial_authorization_v2.json"
AUTHORIZATION_V3 = ROOT / "data" / "notebooklm_summary_trial_authorization_v3.json"
MANIFEST = Path("/tmp/opencode/nmbot_notebook_pre_migration_manifest_v2.json")


def load_module():
    spec = importlib.util.spec_from_file_location("project_memory_notebook_summary_trial_plan_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def load_inputs_v1() -> tuple[dict, dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8")), json.loads(AUTHORIZATION.read_text(encoding="utf-8"))


def load_inputs_v2() -> tuple[dict, dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8")), json.loads(AUTHORIZATION_V2.read_text(encoding="utf-8"))


def load_inputs_v3() -> tuple[dict, dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8")), json.loads(AUTHORIZATION_V3.read_text(encoding="utf-8"))


def test_v1_authorization_is_obsolete_and_fails_closed_before_candidate_selection() -> None:
    mod = load_module()
    manifest, authorization = load_inputs_v1()
    payload = mod.build_trial_plan(manifest, authorization, manifest_sha256="manifest-sha", authorization_sha256="auth-sha")

    assert payload["ok"] is False
    assert payload["denied_reason"] == "authorization_schema_obsolete"
    assert payload["candidate_count"] == 0
    assert "trial_plan" not in payload

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
    assert payload["writes_unapproved"] is True
    assert payload["execution_blocked"] is True


def test_real_manifest_builds_v2_no_write_draft_candidate() -> None:
    mod = load_module()
    manifest, authorization = load_inputs_v2()
    payload = mod.build_trial_plan(manifest, authorization, manifest_sha256="manifest-sha", authorization_sha256="auth-v2-sha")

    assert payload["ok"] is True
    assert payload["candidate_count"] == 1
    assert payload["authorization"]["schema"] == "project_memory_notebook_summary_trial_authorization.v2"
    assert payload["authorization"]["authorization_sha256"] == "auth-v2-sha"
    assert payload["trial_plan"]["record_ref"] == authorization["scope"]["selected_record_ref"]
    assert payload["trial_plan"]["target_canonical_notebook"] == "nmbot"
    assert payload["trial_plan"]["metadata_sha256"] == authorization["scope"]["exact_metadata_sha256"]
    assert payload["trial_plan"]["metadata_sha256"] == "edeec9c399199c99f278b99dc161129cecd9f756114c2d1e2ad3ebc3ab1720dc"
    assert payload["trial_plan"]["notebook_write_authorized"] is False


def test_v2_exact_metadata_sha_must_match_manifest_candidate() -> None:
    mod = load_module()
    manifest, authorization = load_inputs_v2()
    authorization["scope"]["exact_metadata_sha256"] = "0" * 64

    payload = mod.build_trial_plan(manifest, authorization, manifest_sha256="manifest-sha", authorization_sha256="auth-v2-sha")

    assert payload["ok"] is False
    assert payload["denied_reason"] == "eligible_record_metadata_sha_not_authorized"
    assert payload["notebook_write_authorized"] is False


def test_real_manifest_builds_v3_exact_ref_sha_target_no_write_candidate() -> None:
    mod = load_module()
    manifest, authorization = load_inputs_v3()
    payload = mod.build_trial_plan(manifest, authorization, manifest_sha256="manifest-sha", authorization_sha256="auth-v3-sha")

    assert payload["ok"] is True
    assert payload["candidate_count"] == 1
    assert payload["authorization"]["schema"] == "project_memory_notebook_summary_trial_authorization.v3"
    assert payload["authorization"]["authorization_sha256"] == "auth-v3-sha"
    assert payload["trial_plan"]["record_ref"] == authorization["scope"]["selected_record_ref"]
    assert payload["trial_plan"]["record_ref"] == {"notebook": "cc-daemons", "kind": "note", "id": "2a7edfa48439"}
    assert payload["trial_plan"]["metadata_sha256"] == authorization["scope"]["exact_metadata_sha256"]
    assert payload["trial_plan"]["metadata_sha256"] == "97c128a47b331834b55130b53be72391eec58ad3d7a6bd5df262c579995330ed"
    assert payload["trial_plan"]["target_canonical_notebook"] == authorization["scope"]["exact_target_canonical_notebook"] == "nmbot"
    assert payload["trial_plan"]["notebook_write_authorized"] is False


def test_v3_exact_target_must_match_manifest_candidate() -> None:
    mod = load_module()
    manifest, authorization = load_inputs_v3()
    authorization["scope"]["exact_target_canonical_notebook"] = "cc-daemons"

    payload = mod.build_trial_plan(manifest, authorization, manifest_sha256="manifest-sha", authorization_sha256="auth-v3-sha")

    assert payload["ok"] is False
    assert payload["denied_reason"] == "eligible_record_target_not_authorized"
    assert payload["notebook_write_authorized"] is False


def test_cli_stdout_contains_no_content_title_raw_path_or_secret_keys() -> None:
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(MANIFEST), "--authorization", str(AUTHORIZATION_V2), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert run.returncode == 0
    lowered = run.stdout.lower()
    for token in ['"title":', '"body":', '"content":', '"raw":', '"transcript":', '"log":', '"path":', 'secret']:
        assert token not in lowered
    assert json.loads(run.stdout)["ok"] is True


def test_cli_with_explicit_stale_v1_authorization_exits_nonzero_and_leaks_no_unsafe_keys() -> None:
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(MANIFEST), "--authorization", str(AUTHORIZATION), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert run.returncode == 2
    lowered = run.stdout.lower()
    for token in ['"title":', '"body":', '"content":', '"raw":', '"transcript":', '"log":', '"path":', 'secret']:
        assert token not in lowered
    payload = json.loads(run.stdout)
    assert payload["ok"] is False
    assert payload["denied_reason"] == "authorization_schema_obsolete"
    assert payload["candidate_count"] == 0


def test_cli_without_authorization_fails_argument_parsing_before_execution() -> None:
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(MANIFEST), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert run.returncode == 2
    assert run.stdout == ""
    assert "--authorization" in run.stderr


def test_bad_owner_fails_closed() -> None:
    mod = load_module()
    manifest, authorization = load_inputs_v2()
    authorization["owner"] = "TBD"

    payload = mod.build_trial_plan(manifest, authorization, manifest_sha256="manifest-sha", authorization_sha256="auth-sha")

    assert payload["ok"] is False
    assert payload["denied_reason"] == "authorization_owner_mismatch"
    assert payload["notebook_write_authorized"] is False


def test_unsafe_authorization_key_fails_closed() -> None:
    mod = load_module()
    manifest, authorization = load_inputs_v2()
    authorization["content"] = "do not allow"

    payload = mod.build_trial_plan(manifest, authorization, manifest_sha256="manifest-sha", authorization_sha256="auth-sha")

    assert payload["ok"] is False
    assert payload["denied_reason"].startswith("unsafe_key:")
    assert payload["notebook_write_authorized"] is False


def test_unsafe_manifest_key_fails_closed() -> None:
    mod = load_module()
    manifest, authorization = load_inputs_v2()
    manifest["records"][0]["raw"] = "do not allow"

    payload = mod.build_trial_plan(manifest, authorization, manifest_sha256="manifest-sha", authorization_sha256="auth-sha")

    assert payload["ok"] is False
    assert payload["denied_reason"].startswith("unsafe_key:")
    assert payload["notebook_write_authorized"] is False


def test_missing_canonical_target_fails_closed() -> None:
    mod = load_module()
    manifest, authorization = load_inputs_v2()
    selected_ref = authorization["scope"]["selected_record_ref"]
    for row in manifest["records"]:
        if row.get("record_ref") == selected_ref:
            row["target_canonical_notebook"] = None

    payload = mod.build_trial_plan(manifest, authorization, manifest_sha256="manifest-sha", authorization_sha256="auth-sha")

    assert payload["ok"] is False
    assert payload["denied_reason"] == "eligible_record_missing_canonical_target"
    assert payload["notebook_write_authorized"] is False


def test_v2_scope_with_legacy_multiple_refs_shape_fails_closed() -> None:
    mod = load_module()
    manifest, authorization = load_inputs_v2()
    selected_refs = [row["record_ref"] for row in manifest["records"] if row.get("disposition") == "selected_for_summary_plan"]
    authorization["scope"]["selected_record_refs"] = selected_refs[:2]

    payload = mod.build_trial_plan(manifest, authorization, manifest_sha256="manifest-sha", authorization_sha256="auth-sha")

    assert payload["ok"] is False
    assert payload["denied_reason"] == "authorization_scope_invalid"
    assert payload["notebook_write_authorized"] is False


def test_manifest_safety_flag_failure_fails_closed() -> None:
    mod = load_module()
    manifest, authorization = load_inputs_v2()
    manifest["write_performed"] = True

    payload = mod.build_trial_plan(manifest, authorization, manifest_sha256="manifest-sha", authorization_sha256="auth-sha")

    assert payload["ok"] is False
    assert payload["denied_reason"] == "manifest_flag_mismatch:write_performed"
    assert payload["notebook_write_authorized"] is False


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
