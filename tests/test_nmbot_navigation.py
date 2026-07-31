from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_navigation.py"
CONTEXT_GATE_SCRIPT = ROOT / "scripts" / "nmbot_context_gate.py"


def load_module():
    spec = importlib.util.spec_from_file_location("nmbot_navigation_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def load_context_gate_module():
    spec = importlib.util.spec_from_file_location("nmbot_context_gate_navigation_test", CONTEXT_GATE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def make_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    for rel in ("config", "docs", "scripts", "tests", "prompts", "nmbot_v2"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    files = {
        "docs/search.md": "# Search\n\n## Deterministic contract\nRead this first.\n",
        "docs/NMBOT_CONTEXT_PACKS.md": "# Packs\n\n<!-- NMBOT_CONTEXT_PACKS_JSON_START -->\n```json\n{\"schema\":\"nmbot.context_pack.v1\",\"packs\":[{\"id\":\"diagnostics/trace\",\"title\":\"Trace docs\",\"read_first\":[\"docs/search.md\"],\"read_first_anchors\":[{\"path\":\"docs/search.md\",\"anchor\":\"## Deterministic contract\"}],\"docs\":[\"docs/search.md\"],\"files\":[],\"checks\":[\"python3 scripts/nmbot_check.py docs\"],\"boundaries\":[\"local only\"]}]}\n```\n<!-- NMBOT_CONTEXT_PACKS_JSON_END -->\n",
        "scripts/search.py": "def target_symbol():\n    return 'ok'\n\nclass TargetClass:\n    pass\n",
        "tests/test_search.py": "from scripts.search import target_symbol\n\ndef test_target_symbol():\n    assert target_symbol() == 'ok'\n",
        "nmbot_test_agent.py": "def local_check():\n    return 'agent_local_probe'\n",
        "nmbot_v2/quality.py": "MODULE_CODE = 'module_level_ignored'\n\ndef assess_quality():\n    '''docstring_ignored_code'''\n    return 'quality_failed_check'\n\ndef consume_quality(errors):\n    return 'ranked_error_code' in errors\n\ndef emit_quality(errors):\n    errors.append('ranked_error_code')\n\ndef emit_dynamic(errors, name):\n    errors.append(f'unknown_complex:{name}')\n\nclass QualityTool:\n    def method_check(self):\n        return 'method_error_code'\n",
        "tests/test_nmbot_v2_quality.py": "from nmbot_v2.quality import assess_quality\n\ndef test_assess_quality():\n    # comment_ignored_code\n    assert assess_quality() == 'quality_failed_check'\n\ndef test_quality_literal():\n    return 'quality_test_failed_check'\n",
        "prompts/search.txt": "# Prompt title\nbody\n",
        "config/nmbot_stage_map.json": json.dumps({
            "schema": "nmbot.stage_map.v1",
            "paths": {"v2.turn.v1": {"stage_ids": ["v2.search"]}},
            "stages": {"v2.search": {"purpose": "Search stage", "owner": "runtime", "source": "scripts/search.py", "source_symbol": "target_symbol", "doc": "docs/search.md", "test": "tests/test_search.py", "prompt": "prompts/search.txt"}},
        }),
    }
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8")
    manifest = {
        "schema": "nmbot.retrieval_sources.v1",
        "sources": [
            {"path": "docs/search.md", "module": "search", "type": "doc", "owner": "docs", "status": "active"},
            {"path": "docs/NMBOT_CONTEXT_PACKS.md", "module": "context", "type": "doc", "owner": "context", "status": "active"},
            {"path": "scripts/search.py", "module": "search", "type": "python", "owner": "search", "status": "active"},
            {"path": "tests/test_search.py", "module": "search", "type": "test", "owner": "tests", "status": "active"},
            {"path": "nmbot_test_agent.py", "module": "diagnostics", "type": "python", "owner": "itself", "status": "active"},
            {"path": "nmbot_v2/quality.py", "module": "quality", "type": "python", "owner": "itself", "status": "active"},
            {"path": "tests/test_nmbot_v2_quality.py", "module": "quality", "type": "test", "owner": "nmbot_v2/quality.py", "status": "active"},
            {"path": "prompts/search.txt", "module": "search", "type": "prompt", "owner": "prompt", "status": "active"},
        ],
    }
    (root / "config" / "nmbot_retrieval_sources.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, Path("config/nmbot_retrieval_sources.json")


def test_exact_stage_lookup_returns_bounded_active_paths(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("v2.search", root=root, manifest_path=manifest)

    assert report["schema"] == "nmbot.navigation.v1"
    assert report["route"] == "stage"
    assert report["fallback"] is False
    assert report["abstain"] is False
    assert [item["path"] for item in report["results"]] == ["scripts/search.py", "docs/search.md", "tests/test_search.py"]
    assert all(item["stage_id"] == "v2.search" for item in report["results"])
    assert report["results"][0]["source_symbol"] == "target_symbol"
    assert report["results"][0]["start_line"] == 1
    assert report["results"][0]["end_line"] == 2
    assert len(report["results"]) <= 3


def test_exact_stage_source_doc_test_share_canonical_target_spec(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("v2.search", root=root, manifest_path=manifest)

    expected = {
        "target_kind": "stage",
        "target": "v2.search",
        "target_owner": "runtime",
        "owner_path": "scripts/search.py",
    }
    assert [item["stage_field"] for item in report["results"]] == ["source", "doc", "test"]
    assert [item["target_spec"] for item in report["results"]] == [expected, expected, expected]


def test_path_id_lookup_uses_stage_map_and_stays_bounded(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("v2.turn.v1", root=root, manifest_path=manifest)

    assert report["route"] == "stage"
    assert report["reason"] == "exact path_id: v2.turn.v1"
    assert len({item["path"] for item in report["results"]}) == len(report["results"]) <= 3


def test_ast_symbol_returns_exact_line_span_and_one_related_test(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("где target_symbol", root=root, manifest_path=manifest)

    assert report["route"] == "ast"
    item = report["results"][0]
    assert item["path"] == "scripts/search.py"
    assert item["symbol"] == "target_symbol"
    assert item["start_line"] == 1
    assert item["end_line"] == 2
    assert item["related_test"] == "tests/test_search.py"
    assert item["target_spec"] == {
        "target_kind": "symbol",
        "target": "target_symbol",
        "target_owner": "scripts/search.py",
        "owner_path": "scripts/search.py",
    }


def test_exact_diagnostic_code_lookup_returns_candidate_symbol_target(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("failed_check quality_failed_check", root=root, manifest_path=manifest)

    assert report["route"] == "diagnostic"
    assert report["reason"] == "exact diagnostic code: quality_failed_check"
    assert report["fallback"] is False
    item = report["results"][0]
    assert item["kind"] == "diagnostic_code"
    assert item["code"] == "quality_failed_check"
    assert item["symbol"] == "assess_quality"
    assert item["path"] == "nmbot_v2/quality.py"
    assert item["source_role"] == "source"
    assert item["candidate_only"] is True
    assert item["target_spec"] == {
        "target_kind": "symbol",
        "target": "assess_quality",
        "target_owner": "nmbot_v2/quality.py",
        "owner_path": "nmbot_v2/quality.py",
    }


def test_diagnostic_code_source_is_prioritized_over_test_literal(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("quality_failed_check", root=root, manifest_path=manifest)

    assert [item["source_role"] for item in report["results"][:2]] == ["source", "test"]
    assert report["results"][0]["path"] == "nmbot_v2/quality.py"
    assert report["results"][1]["path"] == "tests/test_nmbot_v2_quality.py"


def test_diagnostic_code_emitter_is_prioritized_over_references(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("ranked_error_code", root=root, manifest_path=manifest)

    assert report["results"][0]["symbol"] == "emit_quality"
    assert report["results"][0]["diagnostic_role"] == "emit"
    assert any(item["symbol"] == "consume_quality" and item["diagnostic_role"] == "reference" for item in report["results"])


def test_diagnostic_code_indexes_dynamic_fstring_prefix(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("unknown_complex", root=root, manifest_path=manifest)

    assert report["route"] == "diagnostic"
    assert report["results"][0]["code"] == "unknown_complex"
    assert report["results"][0]["symbol"] == "emit_dynamic"
    assert report["results"][0]["diagnostic_role"] == "emit"

    pasted_runtime_code = mod.navigate("unknown_complex:green_hills", root=root, manifest_path=manifest)
    assert pasted_runtime_code["route"] == "diagnostic"
    assert pasted_runtime_code["reason"] == "exact diagnostic code: unknown_complex"
    assert pasted_runtime_code["results"][0]["symbol"] == "emit_dynamic"


def test_diagnostic_code_test_literal_routes_to_test_symbol(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("quality_test_failed_check", root=root, manifest_path=manifest)

    assert report["route"] == "diagnostic"
    assert report["results"][0]["source_role"] == "test"
    assert report["results"][0]["symbol"] == "test_quality_literal"
    assert report["results"][0]["target_spec"]["target_owner"] == "tests/test_nmbot_v2_quality.py"


def test_diagnostic_code_method_names_are_allowed(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("method_error_code", root=root, manifest_path=manifest)

    assert report["route"] == "diagnostic"
    assert report["results"][0]["symbol"] == "method_check"
    assert report["results"][0]["target_spec"]["target"] == "method_check"


def test_diagnostic_code_ignores_comments_docstrings_and_module_constants(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    records = mod.build_registry(root=root, manifest_path=manifest)["records"]
    codes = {item.get("code") for item in records if item.get("kind") == "diagnostic_code"}

    assert "comment_ignored_code" not in codes
    assert "docstring_ignored_code" not in codes
    assert "module_level_ignored" not in codes


def test_diagnostic_code_registry_drift_fails_closed(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    registry = mod.build_registry(root=root, manifest_path=manifest)
    records = json.loads(json.dumps(registry["records"], ensure_ascii=False))
    diagnostic = next(item for item in records if item.get("kind") == "diagnostic_code" and item.get("code") == "quality_failed_check")
    diagnostic["code"] = "quality_missing_check"

    with pytest.raises(mod.NavigationError, match="diagnostic_code_drift"):
        mod.validate_registry(records, root=root, active_paths=registry["active_paths"])


def test_diagnostic_code_results_are_capped_at_three_and_sorted(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    (root / "nmbot_v2/quality.py").write_text(
        "def z_wide():\n"
        "    if True:\n"
        "        return 'shared_error_code'\n\n"
        "def a_narrow():\n"
        "    return 'shared_error_code'\n\n"
        "def b_narrow():\n"
        "    return 'shared_error_code'\n\n"
        "def c_narrow():\n"
        "    return 'shared_error_code'\n",
        encoding="utf-8",
    )

    report = mod.navigate("shared_error_code", root=root, manifest_path=manifest)

    assert report["route"] == "diagnostic"
    assert len(report["results"]) == 3
    assert [item["symbol"] for item in report["results"]] == ["a_narrow", "b_narrow", "c_narrow"]
    assert all(item["candidate_only"] is True for item in report["results"])


def test_docs_anchor_output_uses_existing_heading_or_context_pack_anchor(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("docs Deterministic contract", root=root, manifest_path=manifest)

    assert report["route"] == "docs"
    first = report["results"][0]
    assert first["path"] == "docs/search.md"
    assert first["anchor"] == "## Deterministic contract"
    assert first["start_line"] == 3
    assert first["target_spec"] == {
        "target_kind": "docs",
        "target": "## Deterministic contract",
        "target_owner": "docs/search.md",
        "owner_path": "docs/search.md",
    }


def test_owner_scoped_doc_anchor_resolves_anchor_omitted_from_global_top3(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    for rel in ("docs/a.md", "docs/b.md", "docs/c.md", "docs/zz_owner.md"):
        (root / rel).write_text("# docs owner-target\n", encoding="utf-8")
    data = json.loads((root / manifest).read_text(encoding="utf-8"))
    data["sources"].extend([
        {"path": "docs/a.md", "module": "crowded", "type": "doc", "owner": "docs", "status": "active"},
        {"path": "docs/b.md", "module": "crowded", "type": "doc", "owner": "docs", "status": "active"},
        {"path": "docs/c.md", "module": "crowded", "type": "doc", "owner": "docs", "status": "active"},
        {"path": "docs/zz_owner.md", "module": "owner", "type": "doc", "owner": "docs", "status": "active"},
    ])
    (root / manifest).write_text(json.dumps(data), encoding="utf-8")

    global_report = mod.navigate("docs owner-target", root=root, manifest_path=manifest)
    scoped_report = mod.resolve_doc_anchor("docs owner-target", "docs/zz_owner.md", root=root, manifest_path=manifest)

    assert global_report["route"] == "docs"
    assert "docs/zz_owner.md" not in [item["path"] for item in global_report["results"]]
    assert scoped_report["route"] == "docs"
    assert scoped_report["fallback"] is False
    assert [item["path"] for item in scoped_report["results"]] == ["docs/zz_owner.md"]
    assert scoped_report["results"][0]["anchor"] == "# docs owner-target"


def test_owner_scoped_doc_anchor_rejects_invalid_owner_and_nonpositive_query(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    with pytest.raises(mod.NavigationError, match="path must be relative"):
        mod.resolve_doc_anchor("docs Deterministic contract", "../docs/search.md", root=root, manifest_path=manifest)
    with pytest.raises(mod.NavigationError, match="not active manifest path"):
        mod.resolve_doc_anchor("docs Deterministic contract", "docs/missing.md", root=root, manifest_path=manifest)
    with pytest.raises(mod.NavigationError, match="docs anchor not found"):
        mod.resolve_doc_anchor("zzzzzz", "docs/search.md", root=root, manifest_path=manifest)


def test_mixed_fallback_is_bounded_and_candidate_only(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("search prompt title", root=root, manifest_path=manifest)

    assert report["route"] == "mixed"
    assert report["fallback"] is True
    assert report["next_action"] == "select_then_grep_read"
    assert 1 <= len(report["results"]) <= 3
    assert all(item["candidate_only"] is True for item in report["results"])
    assert all("candidate_id" not in item for item in report["results"])


def test_mixed_jivo_stage_test_result_carries_canonical_stage_target_spec() -> None:
    mod = load_module()

    report = mod.navigate("transport timeout bridge event", root=ROOT)

    first = report["results"][0]
    assert report["route"] == "mixed"
    assert first["kind"] == "stage"
    assert first["stage_id"] == "jivo.bridge.delivery"
    assert first["stage_field"] == "test"
    assert first["path"] == "tests/test_nmbot_n8n_bridge_transport_timeout.py"
    assert first["source_symbol"] == "_post_event_to_jivo"
    assert first["target_spec"] == {
        "target_kind": "stage",
        "target": "jivo.bridge.delivery",
        "target_owner": "scripts/nmbot_n8n_bridge_server.py",
        "owner_path": "scripts/nmbot_n8n_bridge_server.py",
    }


def test_unrelated_query_abstains_without_random_file(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.navigate("какая сегодня погода и рецепт борща", root=root, manifest_path=manifest)

    assert report["route"] == "mixed"
    assert report["fallback"] is True
    assert report["abstain"] is True
    assert report["results"] == []


def test_stage_ref_outside_active_manifest_fails_closed(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    data = json.loads((root / manifest).read_text(encoding="utf-8"))
    data["sources"] = [item for item in data["sources"] if item["path"] != "prompts/search.txt"]
    (root / manifest).write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(mod.NavigationError, match="not active manifest path"):
        mod.navigate("v2.search", root=root, manifest_path=manifest)


def test_target_specs_are_accepted_by_strict_context_gate_targets(tmp_path: Path) -> None:
    nav = load_module()
    gate = load_context_gate_module()
    root, manifest = make_root(tmp_path)

    reports = [
        nav.navigate("v2.search", root=root, manifest_path=manifest),
        nav.navigate("где target_symbol", root=root, manifest_path=manifest),
        nav.navigate("quality_failed_check", root=root, manifest_path=manifest),
        nav.navigate("docs Deterministic contract", root=root, manifest_path=manifest),
    ]

    for report in reports:
        spec = report["results"][0]["target_spec"]
        strict = gate.run_gate(
            "ignored by strict target test",
            project_id="nmbot",
            evidence_type=spec["target_kind"],
            definition_of_done="strict target contract",
            root=root,
            manifest_path=manifest,
            intents_path=None,
            target_kind=spec["target_kind"],
            target=spec["target"],
            target_owner=spec["target_owner"],
        )
        assert strict["abstain"] is False
        assert strict["context"]


def test_no_target_spec_owner_path_points_to_inactive_path() -> None:
    mod = load_module()
    registry = mod.build_registry(root=ROOT)

    for record in registry["records"]:
        result = mod._result(record)
        spec = result.get("target_spec")
        if spec:
            assert spec["owner_path"] in registry["active_paths"]


def test_missing_doc_anchor_and_missing_symbol_are_rejected(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    registry = mod.build_registry(root=root, manifest_path=manifest)
    records = json.loads(json.dumps(registry["records"], ensure_ascii=False))
    docs_record = next(item for item in records if item["kind"] == "doc_anchor")
    docs_record["anchor"] = "## Missing anchor"
    with pytest.raises(mod.NavigationError, match="anchor_missing"):
        mod.validate_registry(records, root=root, active_paths=registry["active_paths"])

    records = json.loads(json.dumps(registry["records"], ensure_ascii=False))
    symbol_record = next(item for item in records if item["kind"] == "symbol")
    symbol_record["symbol"] = "missing_symbol"
    with pytest.raises(mod.NavigationError, match="symbol_missing"):
        mod.validate_registry(records, root=root, active_paths=registry["active_paths"])


def test_stage_source_symbol_missing_or_span_drift_fails_closed(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    stage_map_path = root / "config" / "nmbot_stage_map.json"
    data = json.loads(stage_map_path.read_text(encoding="utf-8"))
    data["stages"]["v2.search"]["source_symbol"] = "missing_symbol"
    stage_map_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(mod.NavigationError, match="source_symbol_missing"):
        mod.navigate("v2.search", root=root, manifest_path=manifest)

    root, manifest = make_root(tmp_path / "drift")
    registry = mod.build_registry(root=root, manifest_path=manifest)
    records = json.loads(json.dumps(registry["records"], ensure_ascii=False))
    stage_source = next(item for item in records if item["kind"] == "stage" and item.get("stage_field") == "source")
    stage_source["start_line"] = 2
    with pytest.raises(mod.NavigationError, match="stage_source_symbol_drift"):
        mod.validate_registry(records, root=root, active_paths=registry["active_paths"])


def test_stage_test_mapping_without_source_symbol_fails_registry_validation(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    (root / "tests/test_search.py").write_text("def test_unrelated():\n    assert True\n", encoding="utf-8")

    with pytest.raises(mod.NavigationError, match="stage_test_symbol_missing:tests/test_search.py:target_symbol"):
        mod.build_registry(root=root, manifest_path=manifest)


def test_stage_test_mapping_with_module_scope_import_only_fails_registry_validation(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    (root / "tests/test_search.py").write_text(
        "from scripts.search import target_symbol\n\n"
        "def test_unrelated():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    with pytest.raises(mod.NavigationError, match="stage_test_symbol_missing:tests/test_search.py:target_symbol"):
        mod.build_registry(root=root, manifest_path=manifest)


def test_stage_test_mapping_with_comment_or_string_symbol_only_fails_registry_validation(tmp_path: Path) -> None:
    mod = load_module()
    for suffix, text in {
        "comment": "def test_comment_only():\n    # target_symbol should not count\n    assert True\n",
        "string": "def test_string_only():\n    assert 'target_symbol'\n",
    }.items():
        root, manifest = make_root(tmp_path / suffix)
        (root / "tests/test_search.py").write_text(text, encoding="utf-8")

        with pytest.raises(mod.NavigationError, match="stage_test_symbol_missing:tests/test_search.py:target_symbol"):
            mod.build_registry(root=root, manifest_path=manifest)


def test_all_real_stage_source_symbols_resolve_in_current_registry() -> None:
    mod = load_module()
    registry = mod.build_registry(root=ROOT)
    stage_sources = [item for item in registry["records"] if item.get("kind") == "stage" and item.get("stage_field") == "source"]

    assert stage_sources
    assert all(item.get("source_symbol") for item in stage_sources)
    assert next(item for item in stage_sources if item["stage_id"] == "v2.search")["start_line"] > 1


def test_current_navigation_script_uses_no_network_model_runtime_or_subprocess_imports() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {"requests", "httpx", "aiohttp", "urllib", "socket", "subprocess", "openai", "anthropic", "ollama", "nmbot_v2", "nmbot_v0"}
    assert not (imported & forbidden)
    assert "sqlite3" in imported


def test_cli_json_and_validate_only_smoke() -> None:
    result = subprocess.run([sys.executable, "scripts/nmbot_navigation.py", "resolve_response_path", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["route"] == "ast"
    assert payload["results"][0]["path"] == "scripts/nmbot_response_path.py"

    validate = subprocess.run([sys.executable, "scripts/nmbot_navigation.py", "--validate-only", "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert validate.returncode == 0
    assert json.loads(validate.stdout)["valid"] is True
