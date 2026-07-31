from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project_memory_registry.py"


def load_module():
    spec = importlib.util.spec_from_file_location("project_memory_registry_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def registry_copy() -> dict:
    return json.loads((ROOT / "config" / "project_memory_registry.json").read_text(encoding="utf-8"))


def write_registry(tmp_name: str, data: dict) -> str:
    path = ROOT / "config" / tmp_name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"config/{tmp_name}"


def test_real_registry_validates_and_nmbot_route_is_bounded() -> None:
    mod = load_module()
    registry = mod.load_registry()
    validation = mod.validate_registry(registry)
    assert validation == {"schema": "project_registry_validation.v1", "valid": True, "project_count": 5, "errors": []}
    assert registry["projects"][0]["owner"] == "TBD"
    assert registry["projects"][0]["rollback_owner"] == "TBD"

    resolved = mod.resolve_project(registry, "nmbot")
    assert resolved["schema"] == "project_registry_resolution.v1"
    assert resolved["ok"] is True
    assert resolved["canonical_notebook"] == "nmbot"
    assert resolved["write_policy"] == "canonical_only"
    assert resolved["route_resolvers"] == ["scripts/nmbot_navigation.py", "scripts/nmbot_context_gate.py"]
    assert "python3 scripts/nmbot_check.py docs" in resolved["local_checks"]
    assert "cc-daemons" not in json.dumps(resolved)
    assert "cc_daemons" not in json.dumps(resolved)
    assert resolved["legacy_exclusion_enforced"] is True


def test_qapairs_pilot_ready_resolves_canonical_local_route() -> None:
    mod = load_module()
    result = mod.resolve_project(registry_copy(), "qapairs")
    assert result["ok"] is True
    assert result["status"] == "pilot_ready"
    assert result["canonical_notebook"] == "cc-daemons"
    assert result["write_policy"] == "canonical_only"
    assert result["docs_refs"] == ["docs/QAPAIRS_RETRIEVAL.md"]
    assert result["registry_refs"] == [
        "config/qapairs_retrieval_sources.json",
        "config/qapairs_stage_map.json",
        "config/qapairs_diagnostic_codes.json",
    ]
    assert result["route_resolvers"] == ["scripts/project_navigate.py", "scripts/project_context_gate.py"]
    assert "qapairs-daemon" not in json.dumps(result)
    assert result["legacy_exclusion_enforced"] is True


def test_unknown_project_fails_closed() -> None:
    mod = load_module()
    result = mod.resolve_project(registry_copy(), "unknown")
    assert result == {
        "schema": "project_registry_resolution.v1",
        "ok": False,
        "denied_reason": "project_unknown",
        "project_id": "unknown",
    }


def test_cc2_mpn_are_pilot_ready_and_cc_daemons_stays_validating() -> None:
    mod = load_module()
    registry = registry_copy()
    rows = {row["project_id"]: row for row in registry["projects"]}

    assert rows["cc-daemons"]["status"] == "validating"
    assert rows["cc-daemons"]["canonical_notebook"] == "cc-daemons"
    assert rows["cc-daemons"]["owner"] == "ser"
    assert rows["cc-daemons"]["rollback_owner"] == "ser"
    assert rows["cc-daemons"]["allowed_dependency_projects"] == []

    assert rows["qapairs"]["status"] == "pilot_ready"
    assert rows["qapairs"]["canonical_notebook"] == "cc-daemons"
    assert rows["qapairs"]["owner"] == "ser"
    assert rows["qapairs"]["rollback_owner"] == "ser"
    assert rows["qapairs"]["allowed_dependency_projects"] == ["cc-daemons"]

    assert rows["cc2"]["status"] == "pilot_ready"
    assert rows["cc2"]["canonical_notebook"] == "cc2"
    assert rows["cc2"]["owner"] == "ser"
    assert rows["cc2"]["rollback_owner"] == "ser"
    assert rows["cc2"]["allowed_dependency_projects"] == ["cc-daemons"]

    assert rows["mpn"]["status"] == "pilot_ready"
    assert rows["mpn"]["canonical_notebook"] == "mpn"
    assert rows["mpn"]["owner"] == "ser"
    assert rows["mpn"]["rollback_owner"] == "ser"
    assert rows["mpn"]["allowed_dependency_projects"] == ["cc-daemons"]

    result = mod.resolve_project(registry, "cc-daemons")
    assert result["ok"] is False
    assert result["denied_reason"] == "project_not_routable_validating"
    assert result["status"] == "validating"

    for project_id in ("cc2", "mpn", "qapairs"):
        result = mod.resolve_project(registry, project_id)
        assert result["ok"] is True
        assert result["status"] == "pilot_ready"
        assert result["allowed_dependency_projects"] == ["cc-daemons"]
        assert result["route_resolvers"] == ["scripts/project_navigate.py", "scripts/project_context_gate.py"]

    for project_id in ("cc2", "mpn"):
        result = mod.resolve_project(registry, project_id)
        assert "python3 -m pytest tests/test_project_memory_registry.py tests/test_project_context_retrieval_protocol.py tests/test_project_navigation_core.py" in result["local_checks"]

    qapairs_result = mod.resolve_project(registry, "qapairs")
    assert "python3 -m pytest tests/test_qapairs_context_manifest.py tests/test_qapairs_context_acceptance_harness.py tests/test_project_navigation_core.py" in qapairs_result["local_checks"]


def assert_invalid(mutator, code: str) -> None:
    mod = load_module()
    data = registry_copy()
    mutator(data)
    result = mod.validate_registry(data)
    assert result["valid"] is False
    assert code in {error["code"] for error in result["errors"]}


def test_duplicate_project_id_invalid() -> None:
    assert_invalid(lambda data: data["projects"].append(dict(data["projects"][0])), "duplicate_project_id")


def test_missing_required_key_invalid() -> None:
    assert_invalid(lambda data: data["projects"][0].pop("write_policy"), "project_keys")


def test_unsafe_and_missing_refs_invalid() -> None:
    assert_invalid(lambda data: data["projects"][0]["docs_refs"].append("../outside.md"), "unsafe_ref")
    assert_invalid(lambda data: data["projects"][0]["docs_refs"].append("docs/NO_SUCH_FILE.md"), "missing_ref")


def test_broad_legacy_exclusion_invalid() -> None:
    assert_invalid(lambda data: data["projects"][0]["legacy_notebooks_excluded"].append("*"), "broad_legacy_exclusion")
    assert_invalid(lambda data: data["projects"][0]["legacy_notebooks_excluded"].append("nmbot"), "broad_legacy_exclusion")


def test_malformed_pending_and_unknown_dependency_invalid() -> None:
    def make_pending_with_refs(data: dict) -> None:
        data["projects"][1]["status"] = "pending_owner_confirmation"
        data["projects"][1]["docs_refs"].append("docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md")

    assert_invalid(make_pending_with_refs, "pending_refs")
    assert_invalid(lambda data: data["projects"][0]["allowed_dependency_projects"].append("missing-project"), "unknown_dependency_project")


def test_dependency_projects_must_not_be_transitive() -> None:
    def make_cc_daemons_transitive(data: dict) -> None:
        rows = {row["project_id"]: row for row in data["projects"]}
        rows["cc-daemons"]["allowed_dependency_projects"] = ["qapairs"]

    assert_invalid(make_cc_daemons_transitive, "transitive_dependency_project")


def test_validating_allows_empty_or_pre_activation_refs_but_still_denies_route() -> None:
    mod = load_module()
    data = registry_copy()
    qapairs = data["projects"][1]
    qapairs["status"] = "validating"
    qapairs["docs_refs"] = ["docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md"]
    qapairs["registry_refs"] = ["config/project_memory_registry.json"]
    qapairs["route_resolvers"] = ["scripts/project_memory_registry.py"]
    qapairs["local_checks"] = ["python3 scripts/project_memory_registry.py --validate --json"]

    assert mod.validate_registry(data)["valid"] is True
    result = mod.resolve_project(data, "qapairs")
    assert result["ok"] is False
    assert result["denied_reason"] == "project_not_routable_validating"


def test_routable_owner_and_refs_rules_keep_nmbot_tbd_compatibility_only() -> None:
    mod = load_module()
    data = registry_copy()
    data["projects"][1]["status"] = "pilot_ready"
    data["projects"][1]["owner"] = "TBD"
    data["projects"][1]["rollback_owner"] = "TBD"
    data["projects"][1]["docs_refs"] = []
    data["projects"][1]["registry_refs"] = []
    data["projects"][1]["route_resolvers"] = []
    data["projects"][1]["local_checks"] = []
    result = mod.validate_registry(data)
    assert result["valid"] is False
    assert {error["code"] for error in result["errors"]} >= {"routable_owner", "routable_refs"}

    nmbot_compat = registry_copy()
    assert nmbot_compat["projects"][0]["status"] == "pilot_ready"
    assert nmbot_compat["projects"][0]["owner"] == "TBD"
    assert mod.validate_registry(nmbot_compat)["valid"] is True


def test_cli_validate_list_resolve_and_denials(tmp_path) -> None:
    validate = subprocess.run([sys.executable, str(SCRIPT), "--validate", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert validate.returncode == 0
    assert json.loads(validate.stdout)["valid"] is True

    listed = subprocess.run([sys.executable, str(SCRIPT), "--list", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert listed.returncode == 0
    list_payload = json.loads(listed.stdout)
    assert list_payload["projects"] == [
        {"project_id": "nmbot", "status": "pilot_ready", "canonical_notebook": "nmbot"},
        {"project_id": "qapairs", "status": "pilot_ready", "canonical_notebook": "cc-daemons"},
        {"project_id": "cc-daemons", "status": "validating", "canonical_notebook": "cc-daemons"},
        {"project_id": "cc2", "status": "pilot_ready", "canonical_notebook": "cc2"},
        {"project_id": "mpn", "status": "pilot_ready", "canonical_notebook": "mpn"},
    ]

    nmbot = subprocess.run([sys.executable, str(SCRIPT), "--project-id", "nmbot", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert nmbot.returncode == 0
    assert json.loads(nmbot.stdout)["ok"] is True

    qapairs = subprocess.run([sys.executable, str(SCRIPT), "--project-id", "qapairs", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert qapairs.returncode == 0
    assert json.loads(qapairs.stdout)["ok"] is True

    mpn = subprocess.run([sys.executable, str(SCRIPT), "--project-id", "mpn", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert mpn.returncode == 0
    assert json.loads(mpn.stdout)["ok"] is True

    unknown = subprocess.run([sys.executable, str(SCRIPT), "--project-id", "missing", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert unknown.returncode == 2
    assert json.loads(unknown.stdout)["denied_reason"] == "project_unknown"


def test_registry_path_must_be_repo_relative_and_contained() -> None:
    run = subprocess.run([sys.executable, str(SCRIPT), "--registry", "/tmp/outside.json", "--validate", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert run.returncode == 2
    assert json.loads(run.stdout)["errors"][0]["code"] == "registry_load_failed"


def test_source_has_no_banned_runtime_network_or_memory_imports() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    banned = [
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import urllib",
        "from urllib",
        "import notebooklm",
        "from notebooklm",
        "import mempalace",
        "from mempalace",
        "import nmbot_context_gate",
        "from nmbot_context_gate",
        "import nmbot_runtime",
        "from nmbot_runtime",
    ]
    assert not any(token in source for token in banned)
