from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cc2_context_manifest.py"


def load_module():
    spec = importlib.util.spec_from_file_location("cc2_context_manifest_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_owner_fixture(root: Path) -> None:
    (root / "projects/cc2").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "PROJECT_MAP.md").write_text("PROJECT_MAP.md:63-80\n", encoding="utf-8")
    (root / "REFERENCE.md").write_text("REFERENCE.md:257-280\nREFERENCE.md:315-317\n", encoding="utf-8")
    (root / "tests/test_cc2_summary_backfill.py").write_text("def test_placeholder(): pass\n", encoding="utf-8")
    (root / "tests/test_diag_cc2_deferred.py").write_text("def test_placeholder(): pass\n", encoding="utf-8")
    (root / "tests/test_cc2_deferred_processor.py").write_text("def test_placeholder(): pass\n", encoding="utf-8")
    (root / "projects/cc2/ingest_server.py").write_text(
        "class Cc2IngestHandler:\n"
        "    def do_GET(self):\n"
        "        return 'not_found'\n"
        "    def do_POST(self):\n"
        "        return 'unauthorized', 'invalid body size'\n"
        "class Cc2IngestServer: pass\n"
        "def main(): pass\n",
        encoding="utf-8",
    )
    (root / "projects/cc2/direct_inbox.py").write_text(
        "def normalize_payload(payload):\n"
        "    raise ValueError('payload must be a JSON object')\n"
        "    raise ValueError(f'missing required field: {field}')\n"
        "class Cc2DirectInbox: pass\n"
        "def main(): pass\n",
        encoding="utf-8",
    )
    (root / "projects/cc2/direct_worker.py").write_text(
        "def process_short_direct():\n    raise RuntimeError('short processing returned empty result')\n"
        "def process_long_direct():\n    raise RuntimeError('long processing did not complete')\n"
        "def send_crm(): pass\n"
        "def write_result_sheets():\n    raise RuntimeError(f'append failed: {sheet}')\n"
        "def all_crm_sends_succeeded(): pass\n"
        "def main():\n"
        "    raise RuntimeError('CRM send failed: ')\n"
        "    error = f'Sheets write failed after all CRM sends succeeded: {sheet_exc}'\n",
        encoding="utf-8",
    )
    (root / "projects/cc2/pipeline.py").write_text(
        "def process_short_call(): pass\n"
        "def process_long_call(): pass\n"
        "def run_short_sequence():\n"
        "    tag = 'недостаточно данных'\n"
        "    tag_description = 'Ошибка парсинга ответа'\n"
        "    tag_description = 'Нет ответа от Overmind'\n"
        "def run_selection_sequence(): pass\n"
        "def _process_long_calls_batch():\n"
        "    main_tag = 'целевой'\n"
        "    selection_tag = 'не подбор'\n",
        encoding="utf-8",
    )


def manifest_triplet(owner_root: Path):
    sources = json.loads((ROOT / "config/cc2_retrieval_sources.json").read_text(encoding="utf-8"))
    stages = json.loads((ROOT / "config/cc2_stage_map.json").read_text(encoding="utf-8"))
    diagnostics = json.loads((ROOT / "config/cc2_diagnostic_codes.json").read_text(encoding="utf-8"))
    root_text = str(owner_root)
    sources["adapter"]["owner_root"] = root_text
    stages["owner_root"] = root_text
    diagnostics["owner_root"] = root_text
    if owner_root != Path("/home/ser/projects/cc-daemons"):
        sources["excluded"]["paths"] = [item for item in sources["excluded"]["paths"] if item != "/tmp/"]
    return sources, stages, diagnostics


def test_checked_in_cc2_manifests_validate_against_owner_sources():
    assert load_module().validate() == ["cc2_retrieval_sources", "cc2_stage_map", "cc2_diagnostic_codes"]


def test_arbitrary_manifest_paths_are_rejected(tmp_path: Path):
    mod = load_module()
    arbitrary = tmp_path / "cc2_retrieval_sources.json"
    arbitrary.write_text("{}", encoding="utf-8")
    with pytest.raises(mod.ManifestError, match="checked-in adapter allowlist"):
        mod.validate([arbitrary, ROOT / "config/cc2_stage_map.json", ROOT / "config/cc2_diagnostic_codes.json"])


def test_root_mismatch_is_rejected(tmp_path: Path):
    mod = load_module()
    write_owner_fixture(tmp_path)
    sources, stages, diagnostics = manifest_triplet(tmp_path)
    stages["owner_root"] = str(tmp_path / "other")
    with pytest.raises(mod.ManifestError, match="owner_root mismatch"):
        mod.validate_manifest_dicts(sources, stages, diagnostics, expected_owner_root=tmp_path)


def test_root_config_mismatch_fails_closed_against_adapter_root(tmp_path: Path):
    mod = load_module()
    write_owner_fixture(tmp_path)
    sources, stages, diagnostics = manifest_triplet(tmp_path)
    with pytest.raises(mod.ManifestError, match="owner_root must match cc2 adapter root exactly"):
        mod.validate_manifest_dicts(sources, stages, diagnostics)


@pytest.mark.parametrize("bad_path", ["/etc/passwd", "../escape.py"])
def test_absolute_and_traversal_refs_are_rejected(tmp_path: Path, bad_path: str):
    mod = load_module()
    write_owner_fixture(tmp_path)
    sources, stages, diagnostics = manifest_triplet(tmp_path)
    sources["active_sources"][0]["path"] = bad_path
    with pytest.raises(mod.ManifestError, match="owner-root-relative|non-escaping"):
        mod.validate_manifest_dicts(sources, stages, diagnostics, expected_owner_root=tmp_path)


def test_symlink_escape_is_rejected(tmp_path: Path):
    mod = load_module()
    write_owner_fixture(tmp_path)
    outside = tmp_path.parent / "cc2_outside.py"
    outside.write_text("class Cc2IngestHandler: pass\nclass Cc2IngestServer: pass\ndef main(): pass\n", encoding="utf-8")
    escaped = tmp_path / "projects/cc2/escaped.py"
    escaped.symlink_to(outside)
    sources, stages, diagnostics = manifest_triplet(tmp_path)
    sources["active_sources"][0]["path"] = "projects/cc2/escaped.py"
    with pytest.raises(mod.ManifestError, match="escapes owner_root"):
        mod.validate_manifest_dicts(sources, stages, diagnostics, expected_owner_root=tmp_path)


def test_missing_symbol_and_missing_literal_are_rejected(tmp_path: Path):
    mod = load_module()
    write_owner_fixture(tmp_path)
    sources, stages, diagnostics = manifest_triplet(tmp_path)
    broken_sources = copy.deepcopy(sources)
    broken_sources["active_sources"][0]["symbols"] = ["NoSuchSymbol"]
    with pytest.raises(mod.ManifestError, match="missing symbol NoSuchSymbol"):
        mod.validate_manifest_dicts(broken_sources, stages, diagnostics, expected_owner_root=tmp_path)
    broken_diagnostics = copy.deepcopy(diagnostics)
    broken_diagnostics["diagnostics"][0]["literal"] = "no_such_literal"
    with pytest.raises(mod.ManifestError, match="diagnostic literal not found"):
        mod.validate_manifest_dicts(sources, stages, broken_diagnostics, expected_owner_root=tmp_path)
    invalid_code = copy.deepcopy(diagnostics)
    invalid_code["diagnostics"][0]["code"] = "Ошибка парсинга ответа"
    with pytest.raises(mod.ManifestError, match="safe lowercase ASCII"):
        mod.validate_manifest_dicts(sources, stages, invalid_code, expected_owner_root=tmp_path)


def test_exclusions_apply_to_every_routable_ref(tmp_path: Path):
    mod = load_module()
    write_owner_fixture(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/runtime.py").write_text("def main(): pass\n", encoding="utf-8")
    sources, stages, diagnostics = manifest_triplet(tmp_path)
    stages["stages"][0]["owner_source"] = "logs/runtime.py"
    with pytest.raises(mod.ManifestError, match="excluded|forbidden runtime"):
        mod.validate_manifest_dicts(sources, stages, diagnostics, expected_owner_root=tmp_path)


def test_exclusions_apply_to_focused_tests(tmp_path: Path):
    mod = load_module()
    write_owner_fixture(tmp_path)
    sources, stages, diagnostics = manifest_triplet(tmp_path)
    sources["focused_tests"] = ["logs/runtime_test.py"]
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/runtime_test.py").write_text("def test_placeholder(): pass\n", encoding="utf-8")
    with pytest.raises(mod.ManifestError, match="excluded|forbidden runtime"):
        mod.validate_manifest_dicts(sources, stages, diagnostics, expected_owner_root=tmp_path)


def test_required_operation_exclusions_are_checked(tmp_path: Path):
    mod = load_module()
    write_owner_fixture(tmp_path)
    sources, stages, diagnostics = manifest_triplet(tmp_path)
    sources["excluded"]["operations"].remove("eval")
    with pytest.raises(mod.ManifestError, match="missing required exclusions"):
        mod.validate_manifest_dicts(sources, stages, diagnostics, expected_owner_root=tmp_path)
