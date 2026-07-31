from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_SCRIPT = ROOT / "scripts" / "project_memory_notebook_route.py"
MEMPALACE_SCRIPT = ROOT / "scripts" / "project_memory_mempalace_health.py"
ADAPTER_SCRIPT = ROOT / "scripts" / "project_memory_nmbot_adapter.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_phase5_notebook_route_nmbot_is_canonical_dry_run_only() -> None:
    mod = load_module(NOTEBOOK_SCRIPT, "project_memory_notebook_route_test")
    payload = mod.resolve_notebook_route("nmbot", "summary-write")
    assert payload["schema"] == "project_memory_notebook_route.v1"
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["canonical_notebook"] == "nmbot"
    assert payload["route"] == "canonical_project_notebook_only"
    assert payload["write_performed"] is False
    assert payload["notebook_call_performed"] is False
    assert payload["automatic_legacy_notebooks"] == []
    assert payload["excluded_legacy_notebooks"] == ["cc-daemons", "cc_daemons"]
    assert payload["current_source_or_prod_proof_allowed"] is False
    assert "history" in payload["history_boundary"]


def test_phase5_notebook_route_qapairs_is_canonical_dry_run_only_and_denies_unknown_bad_operation() -> None:
    mod = load_module(NOTEBOOK_SCRIPT, "project_memory_notebook_route_test_denials")
    qapairs = mod.resolve_notebook_route("qapairs", "search")
    assert qapairs["ok"] is True
    assert qapairs["canonical_notebook"] == "cc-daemons"
    assert qapairs["excluded_legacy_notebooks"] == ["qapairs-daemon"]
    assert qapairs["notebook_call_performed"] is False
    assert qapairs["write_performed"] is False

    unknown = mod.resolve_notebook_route("missing", "search")
    assert unknown["ok"] is False
    assert unknown["denied_reason"] == "project_unknown"

    bad_op = mod.resolve_notebook_route("nmbot", "write")
    assert bad_op["ok"] is False
    assert bad_op["denied_reason"] == "operation_not_allowed"


def test_phase5_cli_is_json_and_never_writes() -> None:
    run = subprocess.run(
        [sys.executable, str(NOTEBOOK_SCRIPT), "--project-id", "nmbot", "--operation", "search", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert run.returncode == 0
    payload = json.loads(run.stdout)
    assert payload["ok"] is True
    assert payload["canonical_notebook"] == "nmbot"
    assert payload["write_performed"] is False


def test_phase6_mempalace_health_fails_closed_without_external_use() -> None:
    mod = load_module(MEMPALACE_SCRIPT, "project_memory_mempalace_health_test")
    payload = mod.check_mempalace_health("nmbot")
    assert payload["schema"] == "project_memory_mempalace_health.v1"
    assert payload["ok"] is False
    assert payload["selector_enabled"] is False
    assert payload["project_fact_source_enabled"] is False
    assert payload["allowed_after_repair"] == ["agent_diary", "meta_memory"]
    assert payload["mempalace_call_performed"] is False
    assert all(item["pass"] is False for item in payload["checks"])

    run = subprocess.run([sys.executable, str(MEMPALACE_SCRIPT), "--project-id", "nmbot", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert run.returncode == 2
    assert json.loads(run.stdout)["denied_reason"] == "mempalace_disabled_until_integrity_vector_isolation_pass"


def test_phase7_nmbot_adapter_is_passive_shadow_with_frozen_bank() -> None:
    mod = load_module(ADAPTER_SCRIPT, "project_memory_nmbot_adapter_test")
    payload = mod.build_nmbot_adapter_report()
    assert payload["schema"] == "project_memory_nmbot_adapter.v1"
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["project_id"] == "nmbot"
    assert payload["policy_version"] == "nmbot-passive-v1"
    assert payload["policy_delta"] == "passive_local_outcome_store_only"
    assert payload["bank_snapshot"]["schema"] == "bank_snapshot.v1"
    assert payload["bank_snapshot"]["scorer_owner_tbd"] == "TBD"
    assert payload["bank_snapshot_validation"]["valid"] is True
    assert payload["passive_shadow_only"] is True
    assert payload["automatic_gate_invocation"] is False
    assert payload["outcome_write_performed"] is False
    assert payload["behavior_change_performed"] is False
    assert payload["runtime_prompt_provider_model_network_changed"] is False
    assert payload["context_gate_integration"] == "not_invoked_by_adapter"


def test_phase7_cli_reports_dry_run_adapter() -> None:
    run = subprocess.run([sys.executable, str(ADAPTER_SCRIPT), "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert run.returncode == 0
    payload = json.loads(run.stdout)
    assert payload["ok"] is True
    assert payload["outcome_write_performed"] is False


def test_phase5_7_sources_have_no_external_runtime_memory_imports() -> None:
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
    for script in (NOTEBOOK_SCRIPT, MEMPALACE_SCRIPT, ADAPTER_SCRIPT):
        source = script.read_text(encoding="utf-8").lower()
        assert not any(token in source for token in banned)
