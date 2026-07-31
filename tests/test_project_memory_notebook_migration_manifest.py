from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project_memory_notebook_migration_manifest.py"
CLASSIFICATION = Path("/tmp/opencode/nmbot_notebook_classification_v4.json")
REGISTRY = ROOT / "config" / "project_memory_registry.json"
TREE = ROOT / "config" / "project_memory_tree.json"


def load_module():
    spec = importlib.util.spec_from_file_location("project_memory_notebook_migration_manifest_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_real_sanitized_classification_builds_blocked_summary_manifest() -> None:
    mod = load_module()
    payload = mod.build_manifest(
        json.loads(CLASSIFICATION.read_text(encoding="utf-8")),
        json.loads(REGISTRY.read_text(encoding="utf-8")),
        json.loads(TREE.read_text(encoding="utf-8")),
        classification_sha256="classification-sha",
    )

    assert payload["ok"] is True
    assert payload["record_count"] == 364
    assert payload["selected_count"] == 38
    assert payload["held_unresolved_count"] == 323
    assert payload["sensitive_excluded_count"] == 3
    assert payload["execution_blocked"] is True
    assert payload["blocked_reason"] == "blocked_until_separately_authorized_write_plan_and_owner_gate"
    assert payload["migration_performed"] is False
    assert payload["write_performed"] is False
    assert payload["notebook_mutation_performed"] is False
    assert payload["automatic_routing_changed"] is False
    assert payload["execution_gate"]["human_owner"] == "TBD"
    assert payload["execution_gate"]["rollback_owner"] == "TBD"
    assert payload["canonical_decisions"]["n8n_audit_notebook"] == "n8n_audit"
    assert payload["canonical_decisions"]["qapairs"] == "canonical_owner_cc-daemons_all_qapairs_contours_nested_standalone_poller_legacy"
    assert {row["disposition"] for row in payload["records"]} == {
        "selected_for_summary_plan",
        "held_unresolved_no_migration",
        "excluded_sensitive_no_migration",
    }
    assert all(
        row["target_canonical_notebook"]
        for row in payload["records"]
        if row["disposition"] == "selected_for_summary_plan"
    )


def test_explicit_owner_without_registered_canonical_notebook_is_held() -> None:
    mod = load_module()
    payload = mod.build_manifest(
        json.loads(CLASSIFICATION.read_text(encoding="utf-8")),
        json.loads(REGISTRY.read_text(encoding="utf-8")),
        json.loads(TREE.read_text(encoding="utf-8")),
        classification_sha256="classification-sha",
    )

    held_from_explicit_owner = [
        row for row in payload["records"]
        if row["migration_blocker"] == "project_unknown"
    ]
    assert len(held_from_explicit_owner) == 32
    assert all(row["disposition"] == "held_unresolved_no_migration" for row in held_from_explicit_owner)
    assert all(row["target_canonical_notebook"] is None for row in held_from_explicit_owner)


def test_qapairs_records_are_route_eligible_but_still_no_write_authorized() -> None:
    mod = load_module()
    payload = mod.build_manifest(
        json.loads(CLASSIFICATION.read_text(encoding="utf-8")),
        json.loads(REGISTRY.read_text(encoding="utf-8")),
        json.loads(TREE.read_text(encoding="utf-8")),
        classification_sha256="classification-sha",
    )

    qapairs_rows = [row for row in payload["records"] if row["source_owner_path"] and row["source_owner_path"].startswith("ecosystem/cc-daemons/projects/qapairs/")]
    assert len(qapairs_rows) == 12
    assert {row["disposition"] for row in qapairs_rows} == {"selected_for_summary_plan"}
    assert {row["migration_blocker"] for row in qapairs_rows} == {None}
    assert all(row["target_project_id"] == "qapairs" for row in qapairs_rows)
    assert all(row["target_canonical_notebook"] == "cc-daemons" for row in qapairs_rows)
    assert payload["execution_blocked"] is True
    assert payload["execution_gate"]["notebook_write_authorized"] is False


def test_pending_project_with_canonical_notebook_still_cannot_be_selected() -> None:
    mod = load_module()
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for project in registry["projects"]:
        if project["project_id"] == "qapairs":
            project["status"] = "validating"
    classification = {
        "schema": "project_memory_notebook_classification_manifest.v1",
        "ok": True,
        "read_only": True,
        "write_performed": False,
        "notebook_mutation_performed": False,
        "automatic_routing_changed": False,
        "production_verified": False,
        "requires_owner_confirmation": True,
        "record_count": 1,
        "classified_count": 1,
        "sensitive_exclusion_count": 0,
        "unresolved_count": 0,
        "records": [
            {
                "record_ref": {"notebook": "cc-daemons", "kind": "note", "id": "pending-qapairs"},
                "metadata_sha256": "a" * 64,
                "confidence": "explicit",
                "owner_path": "ecosystem/cc-daemons/projects/qapairs/example",
                "sensitive_exclusion": False,
                "lifecycle": "historical",
                "evidence_refs": ["fixture"],
            }
        ],
    }
    payload = mod.build_manifest(
        classification,
        registry,
        json.loads(TREE.read_text(encoding="utf-8")),
        classification_sha256="classification-sha",
    )

    assert payload["ok"] is True
    assert payload["selected_count"] == 0
    assert payload["held_unresolved_count"] == 1
    row = payload["records"][0]
    assert row["disposition"] == "held_unresolved_no_migration"
    assert row["migration_blocker"] == "project_not_routable_validating"
    assert row["target_project_id"] is None
    assert row["target_canonical_notebook"] is None


def test_cli_stdout_contains_no_unsafe_payload_keys_or_secret_words() -> None:
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--classification", str(CLASSIFICATION), "--registry", str(REGISTRY), "--tree", str(TREE), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert run.returncode == 0
    lowered = run.stdout.lower()
    for token in ['"title":', '"body":', '"content":', '"note":', '"raw":', '"transcript":', '"log":', 'secret']:
        assert token not in lowered
    payload = json.loads(run.stdout)
    assert payload["ok"] is True


def test_bad_qapairs_registry_decision_fails_closed() -> None:
    mod = load_module()
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for project in registry["projects"]:
        if project["project_id"] == "qapairs":
            project["canonical_notebook"] = "qapairs-daemon"
    payload = mod.build_manifest(
        json.loads(CLASSIFICATION.read_text(encoding="utf-8")),
        registry,
        json.loads(TREE.read_text(encoding="utf-8")),
        classification_sha256="classification-sha",
    )

    assert payload["ok"] is False
    assert payload["denied_reason"] == "qapairs_registry_canonical_notebook_mismatch"


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
