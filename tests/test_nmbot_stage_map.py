from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_response_path_resolver_returns_compact_v2_lookup() -> None:
    mod = _load_script("nmbot_response_path_test", ROOT / "scripts" / "nmbot_response_path.py")

    result = mod.resolve_response_path("v2")

    assert result["path_id"] == "v2.turn.v1"
    assert "v2.planner" in result["stage_ids"]
    assert "jivo.bridge.delivery" not in result["stage_ids"]
    lookup = {item["stage_id"]: item for item in result["lookup"]}
    assert lookup["v2.search"]["payload_stage"] == "main_search"
    assert lookup["v2.manager_rewriter"]["payload_stage"] == "conversation_answer_manager_rewriter"
    assert lookup["v2.manager_rewriter"]["source"] == "nmbot_v2/manager_rewriter.py"
    dumped = json.dumps(result, ensure_ascii=False)
    assert "secret" not in dumped.lower()
    assert len(dumped) < 6000


def test_response_path_resolver_resolves_trace_path_id_with_inherited_jivo_stage() -> None:
    mod = _load_script("nmbot_response_path_path_id_test", ROOT / "scripts" / "nmbot_response_path.py")

    result = mod.resolve_path_id("jivo.v2.turn.v1")

    assert result["path_id"] == "jivo.v2.turn.v1"
    assert result["stage_ids"][:2] == ["v2.planner", "v2.transition"]
    assert result["stage_ids"][-1] == "jivo.api.prepare"
    lookup = {item["stage_id"]: item for item in result["lookup"]}
    assert lookup["jivo.api.prepare"]["source"] == "scripts/nmbot_api_server.py"


def test_response_path_resolver_returns_single_stage_lookup_without_full_path() -> None:
    mod = _load_script("nmbot_response_path_stage_id_test", ROOT / "scripts" / "nmbot_response_path.py")

    result = mod.resolve_stage_id("v2.search")

    assert result["schema"] == "nmbot.stage_map.v1"
    assert result["stage_id"] == "v2.search"
    assert "path_id" not in result
    assert "stage_ids" not in result
    assert result["lookup"] == {
        "stage_id": "v2.search",
        "purpose": "Conditional main search execution for the search action.",
        "owner": "nmbot_v2/runtime.py",
        "source": "scripts/nmbot_runtime_adapter.py",
        "source_symbol": "search",
        "prompt": "prompts/v2_search_mcp.txt",
        "payload_stage": "main_search",
        "doc": "docs/NMBOT_EXTERNAL_CONTRACTS.md",
        "test": "tests/test_nmbot_v2_search_contract_runtime.py",
    }


def test_response_path_cli_resolves_single_stage_as_json_without_path_fields() -> None:
    import subprocess

    result = subprocess.run(
        [sys.executable, "scripts/nmbot_response_path.py", "--stage-id", "v2.search", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["stage_id"] == "v2.search"
    assert payload["lookup"]["prompt"] == "prompts/v2_search_mcp.txt"
    assert "path_id" not in payload
    assert "stage_ids" not in payload


def test_response_path_resolver_rejects_unknown_cycle_and_duplicate_stage_ids() -> None:
    mod = _load_script("nmbot_response_path_negative_test", ROOT / "scripts" / "nmbot_response_path.py")
    registry = {
        "schema": "nmbot.stage_map.v1",
        "paths": {
            "cycle.a": {"extends": "cycle.b", "stage_ids": []},
            "cycle.b": {"extends": "cycle.a", "stage_ids": []},
            "dupe": {"stage_ids": ["v2.planner", "v2.planner"]},
        },
        "stages": {"v2.planner": {}},
    }

    try:
        mod.resolve_path_id("missing", registry=registry)
        assert False, "unknown path_id must fail"
    except SystemExit as exc:
        assert "Unknown path_id" in str(exc)
    try:
        mod.resolve_path_id("cycle.a", registry=registry)
        assert False, "cycle must fail"
    except SystemExit as exc:
        assert "Extends cycle" in str(exc)
    try:
        mod.resolve_path_id("dupe", registry=registry)
        assert False, "duplicate stage id must fail"
    except SystemExit as exc:
        assert "Duplicate stage_ids" in str(exc)
    try:
        mod.resolve_stage_id("v2.missing", registry=registry)
        assert False, "unknown stage id must fail"
    except SystemExit as exc:
        assert "Unknown stage_id" in str(exc)


def test_response_path_cli_rejects_ambiguous_path_and_stage_ids() -> None:
    import subprocess

    result = subprocess.run(
        [sys.executable, "scripts/nmbot_response_path.py", "--stage-id", "v2.search", "--path-id", "v2.turn.v1"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Use only one" in result.stderr or "Use only one" in result.stdout


def test_architecture_preflight_validates_stage_registry_integrity() -> None:
    mod = _load_script("nmbot_architecture_preflight_registry_test", ROOT / "scripts" / "nmbot_architecture_preflight.py")

    checks, strict_fail = mod.check_stage_registry(ROOT)
    statuses = {item["name"]: item["status"] for item in checks}

    assert strict_fail is False
    assert statuses["stage_registry:schema"] == "PASS"
    assert statuses["stage_registry:status_contract"] == "PASS"
    assert statuses["stage_registry:integrity"] == "PASS"
    assert statuses["stage_registry:source_symbols"] == "PASS"
    assert statuses["stage_registry:focused_tests"] == "PASS"
    assert statuses["stage_registry:runtime_drift"] == "PASS"
    assert statuses["stage_registry:jivo_delivery_boundary"] == "PASS"


def test_architecture_preflight_detects_runtime_drift_cycles_and_duplicate_stages(tmp_path) -> None:
    mod = _load_script("nmbot_architecture_preflight_registry_negative_test", ROOT / "scripts" / "nmbot_architecture_preflight.py")
    repo = tmp_path
    (repo / "config").mkdir()
    (repo / "nmbot_v2").mkdir()
    (repo / "nmbot_v2" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "nmbot_v2" / "execution_path.py").write_text(
        "ALLOWED_EXECUTION_STATUSES={'completed','failed','fallback','skipped'}\n"
        "ALLOWED_EXECUTION_PATH_IDS={'v2.turn.v1','jivo.v2.turn.v1'}\n"
        "V2_EXECUTION_STAGE_IDS=('v2.planner','v2.search')\n"
        "JIVO_API_PREPARE_STAGE_ID='jivo.api.prepare'\n",
        encoding="utf-8",
    )
    registry = {
        "schema": "nmbot.stage_map.v1",
        "schema_version": 1,
        "active_by_version": {"v2": "v2.turn.v1"},
        "paths": {
            "v2.turn.v1": {"stage_ids": ["v2.planner", "v2.search", "v2.search"]},
            "jivo.v2.turn.v1": {"extends": "v2.turn.v1", "stage_ids": ["jivo.api.prepare"]},
            "cycle.a": {"extends": "cycle.b", "stage_ids": []},
            "cycle.b": {"extends": "cycle.a", "stage_ids": []},
            "jivo.bridge.delivery.v1": {"boundary": "b", "correlation_limit": "c", "stage_ids": ["jivo.bridge.delivery"]},
        },
        "stages": {
            "v2.planner": {},
            "v2.search": {},
            "jivo.api.prepare": {},
            "jivo.bridge.delivery": {},
        },
        "status_contract": ["completed", "failed", "fallback", "skipped"],
    }
    (repo / "config" / "nmbot_stage_map.json").write_text(json.dumps(registry), encoding="utf-8")

    checks, strict_fail = mod.check_stage_registry(repo)
    by_name = {item["name"]: item for item in checks}

    assert strict_fail is True
    assert by_name["stage_registry:integrity"]["status"] == "FAIL"
    assert "duplicate stage" in by_name["stage_registry:integrity"]["explain"]
    assert by_name["stage_registry:runtime_drift"]["status"] == "FAIL"


def test_architecture_preflight_detects_bad_stage_source_symbol(tmp_path) -> None:
    mod = _load_script("nmbot_architecture_preflight_bad_symbol_test", ROOT / "scripts" / "nmbot_architecture_preflight.py")
    repo = tmp_path
    (repo / "config").mkdir()
    (repo / "scripts").mkdir()
    (repo / "nmbot_v2").mkdir()
    (repo / "scripts" / "source.py").write_text("def real_symbol():\n    return True\n", encoding="utf-8")
    (repo / "nmbot_v2" / "execution_path.py").write_text(
        "ALLOWED_EXECUTION_STATUSES={'completed','failed','fallback','skipped'}\n"
        "ALLOWED_EXECUTION_PATH_IDS={'v2.turn.v1'}\n"
        "V2_EXECUTION_STAGE_IDS=('v2.search',)\n"
        "JIVO_API_PREPARE_STAGE_ID='jivo.api.prepare'\n",
        encoding="utf-8",
    )
    registry = {
        "schema": "nmbot.stage_map.v1",
        "schema_version": 1,
        "active_by_version": {"v2": "v2.turn.v1"},
        "paths": {"v2.turn.v1": {"stage_ids": ["v2.search"]}, "jivo.bridge.delivery.v1": {"boundary": "b", "correlation_limit": "c", "stage_ids": ["jivo.bridge.delivery"]}},
        "stages": {
            "v2.search": {"source": "scripts/source.py", "source_symbol": "missing_symbol"},
            "jivo.bridge.delivery": {"source": "scripts/source.py", "source_symbol": "real_symbol"},
        },
        "status_contract": ["completed", "failed", "fallback", "skipped"],
    }
    (repo / "config" / "nmbot_stage_map.json").write_text(json.dumps(registry), encoding="utf-8")

    checks, strict_fail = mod.check_stage_registry(repo)
    by_name = {item["name"]: item for item in checks}

    assert strict_fail is True
    assert by_name["stage_registry:source_symbols"]["status"] == "FAIL"
    assert "missing_symbol" in by_name["stage_registry:source_symbols"]["explain"]


def test_architecture_preflight_detects_stage_test_without_source_symbol(tmp_path) -> None:
    mod = _load_script("nmbot_architecture_preflight_bad_stage_test_mapping", ROOT / "scripts" / "nmbot_architecture_preflight.py")
    repo = tmp_path
    (repo / "config").mkdir()
    (repo / "scripts").mkdir()
    (repo / "tests").mkdir()
    (repo / "nmbot_v2").mkdir()
    (repo / "scripts" / "source.py").write_text("def real_symbol():\n    return True\n", encoding="utf-8")
    (repo / "tests" / "test_source.py").write_text("def test_unrelated():\n    assert True\n", encoding="utf-8")
    (repo / "nmbot_v2" / "execution_path.py").write_text(
        "ALLOWED_EXECUTION_STATUSES={'completed','failed','fallback','skipped'}\n"
        "ALLOWED_EXECUTION_PATH_IDS={'v2.turn.v1'}\n"
        "V2_EXECUTION_STAGE_IDS=('v2.search',)\n"
        "JIVO_API_PREPARE_STAGE_ID='jivo.api.prepare'\n",
        encoding="utf-8",
    )
    registry = {
        "schema": "nmbot.stage_map.v1",
        "schema_version": 1,
        "active_by_version": {"v2": "v2.turn.v1"},
        "paths": {"v2.turn.v1": {"stage_ids": ["v2.search"]}, "jivo.bridge.delivery.v1": {"boundary": "b", "correlation_limit": "c", "stage_ids": ["jivo.bridge.delivery"]}},
        "stages": {
            "v2.search": {"source": "scripts/source.py", "source_symbol": "real_symbol", "test": "tests/test_source.py"},
            "jivo.bridge.delivery": {"source": "scripts/source.py", "source_symbol": "real_symbol"},
        },
        "status_contract": ["completed", "failed", "fallback", "skipped"],
    }
    (repo / "config" / "nmbot_stage_map.json").write_text(json.dumps(registry), encoding="utf-8")

    checks, strict_fail = mod.check_stage_registry(repo)
    by_name = {item["name"]: item for item in checks}

    assert strict_fail is True
    assert by_name["stage_registry:focused_tests"]["status"] == "FAIL"
    assert "focused_test_missing_symbol:v2.search" in by_name["stage_registry:focused_tests"]["explain"]


def test_architecture_preflight_rejects_stage_test_module_scope_import_only(tmp_path) -> None:
    mod = _load_script("nmbot_architecture_preflight_stage_test_import_only", ROOT / "scripts" / "nmbot_architecture_preflight.py")
    repo = tmp_path
    (repo / "config").mkdir()
    (repo / "scripts").mkdir()
    (repo / "tests").mkdir()
    (repo / "nmbot_v2").mkdir()
    (repo / "scripts" / "source.py").write_text("def real_symbol():\n    return True\n", encoding="utf-8")
    (repo / "tests" / "test_source.py").write_text(
        "from scripts.source import real_symbol\n\n"
        "def test_unrelated():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    (repo / "nmbot_v2" / "execution_path.py").write_text(
        "ALLOWED_EXECUTION_STATUSES={'completed','failed','fallback','skipped'}\n"
        "ALLOWED_EXECUTION_PATH_IDS={'v2.turn.v1'}\n"
        "V2_EXECUTION_STAGE_IDS=('v2.search',)\n"
        "JIVO_API_PREPARE_STAGE_ID='jivo.api.prepare'\n",
        encoding="utf-8",
    )
    registry = {
        "schema": "nmbot.stage_map.v1",
        "schema_version": 1,
        "active_by_version": {"v2": "v2.turn.v1"},
        "paths": {"v2.turn.v1": {"stage_ids": ["v2.search"]}, "jivo.bridge.delivery.v1": {"boundary": "b", "correlation_limit": "c", "stage_ids": ["jivo.bridge.delivery"]}},
        "stages": {
            "v2.search": {"source": "scripts/source.py", "source_symbol": "real_symbol", "test": "tests/test_source.py"},
            "jivo.bridge.delivery": {"source": "scripts/source.py", "source_symbol": "real_symbol"},
        },
        "status_contract": ["completed", "failed", "fallback", "skipped"],
    }
    (repo / "config" / "nmbot_stage_map.json").write_text(json.dumps(registry), encoding="utf-8")

    checks, strict_fail = mod.check_stage_registry(repo)
    by_name = {item["name"]: item for item in checks}

    assert strict_fail is True
    assert by_name["stage_registry:focused_tests"]["status"] == "FAIL"
    assert "focused_test_missing_symbol:v2.search->tests/test_source.py:real_symbol" in by_name["stage_registry:focused_tests"]["explain"]


def test_architecture_preflight_rejects_stage_test_comment_or_string_symbol_only(tmp_path) -> None:
    mod = _load_script("nmbot_architecture_preflight_stage_test_text_only", ROOT / "scripts" / "nmbot_architecture_preflight.py")
    for suffix, text in {
        "comment": "def test_comment_only():\n    # real_symbol should not count\n    assert True\n",
        "string": "def test_string_only():\n    assert 'real_symbol'\n",
    }.items():
        repo = tmp_path / suffix
        (repo / "config").mkdir(parents=True)
        (repo / "scripts").mkdir()
        (repo / "tests").mkdir()
        (repo / "nmbot_v2").mkdir()
        (repo / "scripts" / "source.py").write_text("def real_symbol():\n    return True\n", encoding="utf-8")
        (repo / "tests" / "test_source.py").write_text(text, encoding="utf-8")
        (repo / "nmbot_v2" / "execution_path.py").write_text(
            "ALLOWED_EXECUTION_STATUSES={'completed','failed','fallback','skipped'}\n"
            "ALLOWED_EXECUTION_PATH_IDS={'v2.turn.v1'}\n"
            "V2_EXECUTION_STAGE_IDS=('v2.search',)\n"
            "JIVO_API_PREPARE_STAGE_ID='jivo.api.prepare'\n",
            encoding="utf-8",
        )
        registry = {
            "schema": "nmbot.stage_map.v1",
            "schema_version": 1,
            "active_by_version": {"v2": "v2.turn.v1"},
            "paths": {"v2.turn.v1": {"stage_ids": ["v2.search"]}, "jivo.bridge.delivery.v1": {"boundary": "b", "correlation_limit": "c", "stage_ids": ["jivo.bridge.delivery"]}},
            "stages": {
                "v2.search": {"source": "scripts/source.py", "source_symbol": "real_symbol", "test": "tests/test_source.py"},
                "jivo.bridge.delivery": {"source": "scripts/source.py", "source_symbol": "real_symbol"},
            },
            "status_contract": ["completed", "failed", "fallback", "skipped"],
        }
        (repo / "config" / "nmbot_stage_map.json").write_text(json.dumps(registry), encoding="utf-8")

        checks, strict_fail = mod.check_stage_registry(repo)
        by_name = {item["name"]: item for item in checks}

        assert strict_fail is True
        assert by_name["stage_registry:focused_tests"]["status"] == "FAIL"
        assert "focused_test_missing_symbol:v2.search->tests/test_source.py:real_symbol" in by_name["stage_registry:focused_tests"]["explain"]
