from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import mpn_context_manifest as manifest  # noqa: E402
from project_adapter_core import ProjectAdapter  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_mpn_owner(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "mpn_local_pipeline.py").write_text(
        "def read_summary_prompt():\n    return 'prompt'\n\n"
        "def build_tags_prompt(tag_catalog, summary, complex_name=''):\n    return 'prompt'\n\n"
        "def validate_tags(tags):\n    return tags, 0\n\n"
        "def fetch_operator_tags_for_crm_guard(call_id, call_type):\n    return '', ''\n\n"
        "def send_to_crm(call_id, call_type, client_name, classification, tags):\n    return {'crm': True}\n\n"
        "def main():\n"
        "    print('summarized')\n"
        "    print('Пустая суммаризация')\n"
        "    print('Ошибка суммаризации')\n"
        "    print('Ошибка тегирования')\n"
        "    return {'low_quality': 'TRUE', 'crm': 'TRUE', 'json_crm': '{}'}\n",
        encoding="utf-8",
    )


def test_diagnostic_literals_are_limited_to_declared_owner_symbol_subtree(tmp_path: Path) -> None:
    owner = tmp_path / "mpn"
    owner.mkdir(parents=True)
    (owner / "mpn_local_pipeline.py").write_text(
        "def other():\n    return 'only_outside'\n\n"
        "def main():\n    return 'inside_literal'\n",
        encoding="utf-8",
    )
    sources = {"excluded": {"paths": [], "operations": []}}
    diagnostics = {"diagnostics": [{"code": "only_outside", "owner_source": "mpn_local_pipeline.py", "owner_symbol": "main"}]}
    exclusions = manifest.build_exclusion_policy(sources, owner)
    with pytest.raises(manifest.ManifestError, match="diagnostic literal not found"):
        manifest.validate_diagnostics(diagnostics, owner, exclusions)


def test_diagnostic_literals_support_conservative_static_fstring_parts(tmp_path: Path) -> None:
    owner = tmp_path / "mpn"
    owner.mkdir(parents=True)
    (owner / "mpn_local_pipeline.py").write_text(
        "def main():\n    return f'Gateway mode: {mode}'\n",
        encoding="utf-8",
    )
    sources = {"excluded": {"paths": [], "operations": []}}
    diagnostics = {"diagnostics": [{"code": "gateway_credentials_missing", "literal": "Gateway mode: ", "match": "prefix_literal", "owner_source": "mpn_local_pipeline.py", "owner_symbol": "main"}]}
    exclusions = manifest.build_exclusion_policy(sources, owner)
    manifest.validate_diagnostics(diagnostics, owner, exclusions)


def make_cc_owner(root: Path) -> None:
    (root / "projects" / "mpn").mkdir(parents=True)
    (root / "projects" / "mpn" / "ingest_server.py").write_text(
        "class MpnIngestHandler:\n"
        "    def do_GET(self):\n        return 'not_found'\n"
        "    def do_POST(self):\n        return 'unauthorized invalid body size'\n",
        encoding="utf-8",
    )
    (root / "projects" / "mpn" / "direct_inbox.py").write_text(
        "def normalize_payload(payload):\n"
        "    raise ValueError('payload must be a JSON object')\n"
        "class MpnDirectInbox:\n"
        "    def enqueue(self):\n        return 'missing required field'\n",
        encoding="utf-8",
    )
    (root / "projects" / "mpn" / "direct_worker.py").write_text(
        "def payload_to_sheet_row(payload):\n    return {}\n"
        "def mirror_to_sheet(payload, config):\n    raise RuntimeError('sheet append failed')\n"
        "def run_pipeline(config, minutes, limit):\n    raise RuntimeError('pipeline exit=1')\n"
        "def main(argv=None):\n    return 0\n",
        encoding="utf-8",
    )


def manifests(owner: Path, cc_owner: Path) -> tuple[dict, dict, dict, dict]:
    sources = {
        "schema_version": 1,
        "adapter": {"id": "mpn", "canonical_notebook": "mpn", "owner_root": str(owner), "status": "validating_local_developer_routing_only"},
        "active_sources": [{"path": "mpn_local_pipeline.py", "symbols": ["main", "send_to_crm", "fetch_operator_tags_for_crm_guard"]}],
        "docs": [],
        "focused_tests": [],
        "excluded": {"paths": [".env", "logs/", "*.sqlite3", "config/"], "operations": ["crm_post", "google_sheets", "network", "apply"]},
    }
    stages = {"schema_version": 1, "owner_root": str(owner), "stages": [{"owner_source": "mpn_local_pipeline.py", "owner_symbols": ["main"], "test_refs": []}]}
    diagnostics = {"schema_version": 1, "owner_root": str(owner), "diagnostics": [{"code": "summarized", "owner_source": "mpn_local_pipeline.py", "owner_symbol": "main"}]}
    card = {
        "schema": "project_dependency_card.v1",
        "scope_id": "mpn.cc-daemons.direct-ingest.v1",
        "owner_project": "cc-daemons",
        "consumer_project": "mpn",
        "canonical_notebook": "cc-daemons",
        "contract_ref": "docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md:156-182",
        "reason": "one-hop interface",
        "allowed_query_types": ["contract", "interface"],
        "max_depth": 1,
        "max_records": 2,
        "transitive_traversal": False,
        "no_transitive": True,
        "records": [
            {"id": "ingest", "kind": "interface", "owner_root": str(cc_owner), "paths": [
                {"path": "projects/mpn/ingest_server.py", "symbols": ["MpnIngestHandler", "do_GET", "do_POST"], "literals": ["not_found", "unauthorized", "invalid body size"]},
                {"path": "projects/mpn/direct_inbox.py", "symbols": ["normalize_payload", "MpnDirectInbox"], "literals": ["payload must be a JSON object", "missing required field"]},
            ]},
            {"id": "worker", "kind": "interface", "owner_root": str(cc_owner), "paths": [
                {"path": "projects/mpn/direct_worker.py", "symbols": ["payload_to_sheet_row", "mirror_to_sheet", "run_pipeline", "main"], "literals": ["sheet append failed", "pipeline exit="]}
            ]},
        ],
    }
    return sources, stages, diagnostics, card


def test_arbitrary_manifest_paths_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = tmp_path / "mpn"
    cc = tmp_path / "cc-daemons"
    make_mpn_owner(owner)
    make_cc_owner(cc)
    fake = ProjectAdapter("mpn", owner, tmp_path / "allowed-sources.json", tmp_path / "allowed-stages.json", tmp_path / "allowed-diag.json", "mpn", "mpn")
    monkeypatch.setattr(manifest, "_mpn_adapter", lambda: fake)
    with pytest.raises(manifest.ManifestError, match="allowlist exactly"):
        manifest.validate((tmp_path / "sources.json", tmp_path / "stages.json", tmp_path / "diagnostics.json", tmp_path / "card.json"))


def test_owner_root_must_match_allowlisted_mpn_root(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    foreign = tmp_path / "foreign"
    owner.mkdir()
    foreign.mkdir()
    with pytest.raises(manifest.ManifestError, match="adapter root exactly"):
        manifest.owner_root_from({"owner_root": str(foreign)}, expected_owner_root=owner)


def test_abs_traversal_and_symlink_escape_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "safe.py").write_text("x = 1\n", encoding="utf-8")
    (outside / "secret.py").write_text("x = 1\n", encoding="utf-8")
    (root / "escape.py").symlink_to(outside / "secret.py")
    assert manifest.ensure_owner_relative(root, "safe.py", label="safe").name == "safe.py"
    with pytest.raises(manifest.ManifestError, match="owner-root-relative"):
        manifest.ensure_owner_relative(root, str(root / "safe.py"), label="absolute")
    with pytest.raises(manifest.ManifestError, match="escapes owner_root"):
        manifest.ensure_owner_relative(root, "../outside/secret.py", label="traversal")
    with pytest.raises(manifest.ManifestError, match="escapes owner_root"):
        manifest.ensure_owner_relative(root, "escape.py", label="symlink")


def test_dependency_depth_record_cap_and_transitive_denial(tmp_path: Path) -> None:
    owner = tmp_path / "mpn"
    cc = tmp_path / "cc-daemons"
    make_mpn_owner(owner)
    make_cc_owner(cc)
    _, _, _, card = manifests(owner, cc)
    manifest.validate_dependency_card(card, cc)
    too_deep = deepcopy(card)
    too_deep["max_depth"] = 2
    with pytest.raises(manifest.ManifestError, match="max_depth=1"):
        manifest.validate_dependency_card(too_deep, cc)
    too_many = deepcopy(card)
    too_many["records"].append(deepcopy(card["records"][0]))
    with pytest.raises(manifest.ManifestError, match="max_records=2 exceeded"):
        manifest.validate_dependency_card(too_many, cc)
    transitive = deepcopy(card)
    transitive["records"][0]["dependencies"] = ["other"]
    with pytest.raises(manifest.ManifestError, match="transitive dependency fields are denied"):
        manifest.validate_dependency_card(transitive, cc)


def test_missing_symbol_and_literal_fail(tmp_path: Path) -> None:
    owner = tmp_path / "mpn"
    cc = tmp_path / "cc-daemons"
    make_mpn_owner(owner)
    make_cc_owner(cc)
    sources, stages, diagnostics, card = manifests(owner, cc)
    exclusions = manifest.build_exclusion_policy(sources, owner)
    bad_sources = deepcopy(sources)
    bad_sources["active_sources"][0]["symbols"] = ["definitely_missing"]
    with pytest.raises(manifest.ManifestError, match="missing symbol"):
        manifest.validate_sources(bad_sources, owner, exclusions)
    bad_diag = deepcopy(diagnostics)
    bad_diag["diagnostics"][0]["code"] = "definitely_absent_literal"
    with pytest.raises(manifest.ManifestError, match="diagnostic literal not found"):
        manifest.validate_diagnostics(bad_diag, owner, exclusions)
    bad_card = deepcopy(card)
    bad_card["records"][0]["paths"][0]["literals"] = ["definitely_absent_dependency_literal"]
    with pytest.raises(manifest.ManifestError, match="dependency literal not found"):
        manifest.validate_dependency_card(bad_card, cc)


@pytest.mark.parametrize("path_value", ["config/mpn_pipeline_config.json", "logs/app.log", "inbox.sqlite3"])
def test_excluded_refs_are_rejected(path_value: str, tmp_path: Path) -> None:
    owner = tmp_path / "mpn"
    make_mpn_owner(owner)
    (owner / "config").mkdir()
    (owner / "config" / "mpn_pipeline_config.json").write_text("{}", encoding="utf-8")
    (owner / "logs").mkdir()
    (owner / "logs" / "app.log").write_text("log", encoding="utf-8")
    (owner / "inbox.sqlite3").write_text("db", encoding="utf-8")
    cc = tmp_path / "cc-daemons"
    make_cc_owner(cc)
    sources, _, _, _ = manifests(owner, cc)
    exclusions = manifest.build_exclusion_policy(sources, owner)
    with pytest.raises(manifest.ManifestError, match="path is excluded"):
        manifest.ensure_not_excluded(exclusions, path_value, label="ref")


def test_dependency_rejects_non_mpn_and_absolute_refs(tmp_path: Path) -> None:
    owner = tmp_path / "mpn"
    cc = tmp_path / "cc-daemons"
    make_mpn_owner(owner)
    make_cc_owner(cc)
    _, _, _, card = manifests(owner, cc)
    bad = deepcopy(card)
    bad["records"][0]["paths"][0]["path"] = "projects/cc2/pipeline.py"
    with pytest.raises(manifest.ManifestError, match="only projects/mpn interface refs"):
        manifest.validate_dependency_card(bad, cc)
    bad_abs = deepcopy(card)
    bad_abs["records"][0]["paths"][0]["path"] = str(cc / "projects" / "mpn" / "ingest_server.py")
    with pytest.raises(manifest.ManifestError, match="dependency-root-relative"):
        manifest.validate_dependency_card(bad_abs, cc)


def test_cli_rejects_override_options() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "mpn_context_manifest.py"), "--sources", "x.json"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr


def test_real_checked_in_validation_passes_after_shared_integration() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "mpn_context_manifest.py")],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MPN context manifest OK" in result.stdout


def test_adapter_load_fails_closed_when_shared_root_is_unavailable(monkeypatch) -> None:
    def unavailable(_project_id: str):
        raise manifest.AdapterError("root unavailable")

    monkeypatch.setattr(manifest, "load_adapter", unavailable)
    with pytest.raises(manifest.ManifestError, match="code-level allowlisted"):
        manifest._mpn_adapter()
