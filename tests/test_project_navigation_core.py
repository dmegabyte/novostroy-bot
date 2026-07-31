from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NAV_SCRIPT = ROOT / "scripts" / "project_navigate.py"
NAV_CORE = ROOT / "scripts" / "project_navigation_core.py"
GATE_SCRIPT = ROOT / "scripts" / "project_context_gate.py"
GATE_CORE = ROOT / "scripts" / "project_context_gate_core.py"
ADAPTER_CORE = ROOT / "scripts" / "project_adapter_core.py"


def load_nav():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("project_navigation_core_test", NAV_CORE)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def load_gate():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("project_context_gate_core_test", GATE_CORE)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def load_adapter():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("project_adapter_core_test", ADAPTER_CORE)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_adapter_only_allows_known_project_ids_and_rejects_path_escapes(tmp_path: Path) -> None:
    adapter = load_adapter()
    root = tmp_path / "root"
    root.mkdir()
    (root / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "outside.py").write_text("x = 2\n", encoding="utf-8")
    (root / "link.py").symlink_to(tmp_path / "outside.py")

    assert adapter.load_adapter("nmbot").project_id == "nmbot"
    assert adapter.load_adapter("qapairs").canonical_notebook == "cc-daemons"
    assert adapter.load_adapter("cc-daemons").canonical_notebook == "cc-daemons"
    assert adapter.load_adapter("cc2").canonical_notebook == "cc2"
    assert adapter.load_adapter("cc2").root == adapter.load_adapter("qapairs").root
    assert adapter.load_adapter("cc2").manifest_path == ROOT / "config/cc2_retrieval_sources.json"
    assert adapter.load_adapter("cc2").stage_map_path == ROOT / "config/cc2_stage_map.json"
    assert adapter.load_adapter("cc2").diagnostics_path == ROOT / "config/cc2_diagnostic_codes.json"
    assert adapter.load_adapter("mpn").canonical_notebook == "mpn"
    assert adapter.load_adapter("mpn").manifest_path == ROOT / "config/mpn_retrieval_sources.json"
    assert adapter.load_adapter("mpn").stage_map_path == ROOT / "config/mpn_stage_map.json"
    assert adapter.load_adapter("mpn").diagnostics_path == ROOT / "config/mpn_diagnostic_codes.json"
    assert adapter._ADAPTERS["mpn"].root == Path("/home/ser/projects/mpn-daemon")
    assert adapter.load_adapter("mpn").root.is_dir()
    with pytest.raises(adapter.AdapterError, match="unsupported project_id"):
        adapter.load_adapter("/tmp/whatever")
    with pytest.raises(adapter.AdapterError, match="path must be relative"):
        adapter.safe_join(root, "/tmp/outside.py")
    with pytest.raises(adapter.AdapterError, match="path must be relative"):
        adapter.safe_join(root, "../outside.py")
    with pytest.raises(adapter.AdapterError, match="escapes adapter root"):
        adapter.safe_join(root, "link.py")


def test_qapairs_validate_only_and_exact_stage_route() -> None:
    result = subprocess.run([sys.executable, str(NAV_SCRIPT), "--project-id", "qapairs", "--validate-only", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    validation = json.loads(result.stdout)
    assert validation["valid"] is True
    assert validation["project_id"] == "qapairs"

    route = subprocess.run([sys.executable, str(NAV_SCRIPT), "v1_pair_verifier", "--project-id", "qapairs", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert route.returncode == 0, route.stderr
    payload = json.loads(route.stdout)
    assert payload["project_id"] == "qapairs"
    assert payload["local_read_only"] is True
    assert payload["production_proof"] is False
    assert payload["route"] == "stage"
    assert payload["results"][0]["path"] == "tools/issue_qa_orchestrator.py"
    assert payload["results"][0]["source_symbol"] == "verify_pair"
    assert payload["results"][0]["target_spec"] == {
        "target_kind": "stage",
        "target": "v1_pair_verifier",
        "target_owner": "tools/issue_qa_orchestrator.py",
        "owner_path": "tools/issue_qa_orchestrator.py",
    }


def test_qapairs_all_expected_diagnostic_codes_map_to_config_owner_symbols() -> None:
    nav = load_nav()
    expected = json.loads((ROOT / "config" / "qapairs_diagnostic_codes.json").read_text(encoding="utf-8"))["diagnostics"]
    assert len(expected) == 12

    for item in expected:
        report = nav.navigate(item["code"] + ":dynamic_suffix", project_id="qapairs")
        assert report["route"] == "diagnostic"
        first = report["results"][0]
        assert first["code"] == item["code"]
        assert first["path"] == item["owner_source"]
        assert first["symbol"] == item["owner_symbol"]
        assert first["candidate_only"] is True
        assert first["target_spec"] == {
            "target_kind": "symbol",
            "target": item["owner_symbol"],
            "target_owner": item["owner_source"],
            "owner_path": item["owner_source"],
        }


def test_qapairs_invalid_diagnostic_code_safely_abstains() -> None:
    nav = load_nav()
    report = nav.navigate("totally_missing_code_zzz", project_id="qapairs")
    assert report["route"] == "mixed"
    assert report["abstain"] is True or all(item.get("candidate_only") for item in report["results"])
    assert report["route"] != "diagnostic"


def test_qapairs_strict_gate_uses_navigation_target_and_hard_budgets() -> None:
    nav = load_nav()
    gate = load_gate()
    spec = nav.navigate("numbers_not_in_transcript", project_id="qapairs")["results"][0]["target_spec"]

    report = gate.run_gate(
        "ignored natural question",
        project_id="qapairs",
        evidence_type=spec["target_kind"],
        definition_of_done="exact owner symbol",
        target_kind=spec["target_kind"],
        target=spec["target"],
        target_owner=spec["target_owner"],
        max_sources=2,
        max_lines=80,
        max_chars=8000,
    )

    assert report["schema"] == "project.context_gate.v1"
    assert report["route"] == "ast"
    assert report["context"][0]["path"] == "tools/issue_qa_orchestrator.py"
    assert report["context"][0]["symbol"] == "verify_pair"
    assert report["trace"]["selected_source_count"] <= 2
    assert report["trace"]["lines_loaded"] <= 80
    assert report["trace"]["characters_loaded"] <= 8000


def test_qapairs_clipped_symbol_span_reports_context_budget_reached() -> None:
    gate = load_gate()
    report = gate.run_gate(
        "ignored",
        project_id="qapairs",
        evidence_type="symbol",
        definition_of_done="clip long symbol honestly",
        target_kind="symbol",
        target="verify_pair",
        target_owner="tools/issue_qa_orchestrator.py",
        max_lines=5,
        max_chars=8000,
    )

    assert report["context"][0]["end_line"] == report["context"][0]["start_line"] + 4
    assert report["budget_status"] == "context_budget_reached"
    assert report["stop_reason"] == "context_budget_reached"
    assert report["trace"]["lines_loaded"] == 5


@pytest.mark.parametrize(
    ("project_id", "stage_id", "owner_path", "source_symbol"),
    [
        ("cc2", "direct_inbox_enqueue", "projects/cc2/direct_inbox.py", "normalize_payload"),
        ("mpn", "process_summary", "mpn_local_pipeline.py", "main"),
    ],
)
def test_cc2_mpn_validate_only_and_exact_stage_route(project_id: str, stage_id: str, owner_path: str, source_symbol: str) -> None:
    result = subprocess.run([sys.executable, str(NAV_SCRIPT), "--project-id", project_id, "--validate-only", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    validation = json.loads(result.stdout)
    assert validation["valid"] is True
    assert validation["project_id"] == project_id

    route = subprocess.run([sys.executable, str(NAV_SCRIPT), stage_id, "--project-id", project_id, "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert route.returncode == 0, route.stderr
    payload = json.loads(route.stdout)
    assert payload["project_id"] == project_id
    assert payload["route"] == "stage"
    assert payload["results"][0]["path"] == owner_path
    assert payload["results"][0]["source_symbol"] == source_symbol
    assert payload["results"][0]["target_spec"] == {
        "target_kind": "stage",
        "target": stage_id,
        "target_owner": owner_path,
        "owner_path": owner_path,
    }


@pytest.mark.parametrize("project_id", ["cc2", "mpn"])
def test_cc2_mpn_all_configured_diagnostic_codes_map_to_owner_symbols(project_id: str) -> None:
    nav = load_nav()
    expected = json.loads((ROOT / "config" / f"{project_id}_diagnostic_codes.json").read_text(encoding="utf-8"))["diagnostics"]
    for item in expected:
        report = nav.navigate(item["code"] + ":dynamic_suffix", project_id=project_id)
        assert report["route"] == "diagnostic"
        first = report["results"][0]
        assert first["code"] == item["code"]
        assert first["path"] == item["owner_source"]
        assert first["symbol"] == item["owner_symbol"]
        assert first["candidate_only"] is True
        assert first["target_spec"] == {
            "target_kind": "symbol",
            "target": item["owner_symbol"],
            "target_owner": item["owner_source"],
            "owner_path": item["owner_source"],
        }


@pytest.mark.parametrize("project_id", ["cc2", "mpn"])
def test_cc2_mpn_unknown_diagnostics_do_not_take_diagnostic_route(project_id: str) -> None:
    nav = load_nav()
    report = nav.navigate("unknown_external_literal_zzz", project_id=project_id)
    assert report["route"] != "diagnostic"
    assert report["abstain"] is True or all(item.get("candidate_only") for item in report["results"])


def test_cc2_mpn_strict_gate_accepts_navigation_symbol_targets_and_budgets() -> None:
    nav = load_nav()
    gate = load_gate()
    for project_id, query in (("cc2", "payload_not_json_object"), ("mpn", "summary_saved")):
        spec = nav.navigate(query, project_id=project_id)["results"][0]["target_spec"]
        report = gate.run_gate(
            "ignored",
            project_id=project_id,
            evidence_type=spec["target_kind"],
            definition_of_done="exact owner symbol",
            target_kind=spec["target_kind"],
            target=spec["target"],
            target_owner=spec["target_owner"],
            max_sources=2,
            max_lines=80,
            max_chars=8000,
        )
        assert report["route"] == "ast"
        assert report["trace"]["selected_source_count"] <= 2
        assert report["trace"]["lines_loaded"] <= 80
        assert report["trace"]["characters_loaded"] <= 8000
        assert report["context"][0]["path"] == spec["target_owner"]


def test_project_navigation_nmbot_parity_with_existing_navigation() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import nmbot_navigation

    nav = load_nav()
    old = nmbot_navigation.navigate("resolve_response_path", root=ROOT)
    new = nav.navigate("resolve_response_path", project_id="nmbot")
    assert new["schema"] == old["schema"]
    assert new["route"] == old["route"]
    assert new["results"] == old["results"]
    assert new["project_id"] == "nmbot"


def test_new_project_scripts_import_no_network_runtime_subprocess_or_writes() -> None:
    forbidden_imports = {"requests", "httpx", "aiohttp", "urllib", "socket", "subprocess", "openai", "anthropic", "ollama", "nmbot_v2", "nmbot_v0", "notebooklm"}
    forbidden_calls = {"write_text", "write_bytes", "open"}
    for script in (NAV_CORE, GATE_CORE, ADAPTER_CORE):
        tree = ast.parse(script.read_text(encoding="utf-8"))
        imported = set()
        called_attrs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    called_attrs.add(func.attr)
                elif isinstance(func, ast.Name):
                    called_attrs.add(func.id)
        assert not (imported & forbidden_imports), script
        assert not (called_attrs & forbidden_calls), script


def test_project_context_gate_cli_smoke() -> None:
    nav = load_nav()
    spec = nav.navigate("v2_structured_issues", project_id="qapairs")["results"][0]["target_spec"]
    result = subprocess.run(
        [
            sys.executable,
            str(GATE_SCRIPT),
            "ignored",
            "--project-id",
            "qapairs",
            "--evidence-type",
            spec["target_kind"],
            "--target-kind",
            spec["target_kind"],
            "--target",
            spec["target"],
            "--target-owner",
            spec["target_owner"],
            "--definition-of-done",
            "owner source and focused test",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["project_id"] == "qapairs"
