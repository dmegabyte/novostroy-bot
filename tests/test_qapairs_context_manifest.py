from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import qapairs_context_manifest as manifest  # noqa: E402
import project_navigation_core as navigation  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_fixture_owner(root: Path) -> None:
    (root / "tools").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "PROJECT_MAP.md").write_text("qapairs map", encoding="utf-8")
    (root / "docs" / "qapairs-operational-modes.md").write_text("qapairs modes", encoding="utf-8")
    (root / "tests" / "test_issue_qa_orchestrator.py").write_text("def test_placeholder(): pass\n", encoding="utf-8")
    (root / "tools" / "issue_qa_orchestrator.py").write_text(
        "def verify_pair(pair, transcript, expected_complex=''):\n"
        "    return ['numbers_not_in_transcript']\n\n"
        "def verify_record_pairs(record, max_pairs=3):\n"
        "    return [{'issues': ['too_many_pairs']}]\n",
        encoding="utf-8",
    )


def fixture_manifests(tmp_path: Path, owner: Path) -> tuple[Path, Path, Path]:
    sources = {
        "schema_version": 1,
        "adapter": {"canonical_notebook": "cc-daemons", "owner_root": str(owner)},
        "active_sources": [
            {"path": "tools/issue_qa_orchestrator.py", "symbols": ["verify_pair", "verify_record_pairs"]}
        ],
        "docs": [{"path": "PROJECT_MAP.md"}, {"path": "docs/qapairs-operational-modes.md"}],
        "focused_tests": ["tests/test_issue_qa_orchestrator.py"],
    }
    stages = {
        "schema_version": 1,
        "owner_root": str(owner),
        "stages": [
            {
                "owner_source": "tools/issue_qa_orchestrator.py",
                "owner_symbols": ["verify_pair"],
                "test_refs": ["tests/test_issue_qa_orchestrator.py"],
            }
        ],
    }
    diagnostics = {
        "schema_version": 1,
        "owner_root": str(owner),
        "diagnostics": [
            {
                "code": "numbers_not_in_transcript",
                "owner_source": "tools/issue_qa_orchestrator.py",
                "owner_symbol": "verify_pair",
            },
            {
                "code": "too_many_pairs",
                "owner_source": "tools/issue_qa_orchestrator.py",
                "owner_symbol": "verify_record_pairs",
            },
        ],
    }
    sources_path = tmp_path / "sources.json"
    stages_path = tmp_path / "stages.json"
    diagnostics_path = tmp_path / "diagnostics.json"
    write_json(sources_path, sources)
    write_json(stages_path, stages)
    write_json(diagnostics_path, diagnostics)
    return sources_path, stages_path, diagnostics_path


def real_manifests() -> tuple[dict, dict, dict, Path]:
    adapter = manifest._qapairs_adapter()
    assert adapter.manifest_path is not None
    assert adapter.stage_map_path is not None
    assert adapter.diagnostics_path is not None
    return (
        json.loads(Path(adapter.manifest_path).read_text(encoding="utf-8")),
        json.loads(Path(adapter.stage_map_path).read_text(encoding="utf-8")),
        json.loads(Path(adapter.diagnostics_path).read_text(encoding="utf-8")),
        adapter.root,
    )


def test_arbitrary_manifest_paths_are_rejected(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    make_fixture_owner(owner)
    with pytest.raises(manifest.ManifestError, match="allowlist exactly"):
        manifest.validate(fixture_manifests(tmp_path, owner))


def test_owner_root_must_match_allowlisted_qapairs_root(tmp_path: Path) -> None:
    _, _, _, owner_root = real_manifests()
    foreign = tmp_path / "foreign-owner"
    foreign.mkdir()
    with pytest.raises(manifest.ManifestError, match="adapter root exactly"):
        manifest.owner_root_from({"owner_root": str(foreign)}, expected_owner_root=owner_root)


def test_missing_literal_fails_narrow_symbol(tmp_path: Path) -> None:
    sources, stages, diagnostics, owner_root = real_manifests()
    exclusions = manifest.build_exclusion_policy(sources, owner_root)
    diagnostics["diagnostics"][0]["code"] = "definitely_absent_qapairs_code"
    with pytest.raises(manifest.ManifestError, match="diagnostic literal not found"):
        manifest.validate_diagnostics(diagnostics, owner_root, exclusions)


def test_legacy_active_source_fails(tmp_path: Path) -> None:
    sources, _, _, owner_root = real_manifests()
    exclusions = manifest.build_exclusion_policy(sources, owner_root)
    sources["active_sources"][0]["path"] = "projects/qapairs/daemon.py"
    with pytest.raises(manifest.ManifestError, match="legacy path in active source"):
        manifest.validate_sources(sources, owner_root, exclusions)


def test_absolute_traversal_and_symlink_escape_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.py").write_text("x = 1\n", encoding="utf-8")
    (root / "safe.py").write_text("x = 1\n", encoding="utf-8")
    (root / "escape.py").symlink_to(outside / "secret.py")
    assert manifest.ensure_owner_relative(root, "safe.py", label="safe").name == "safe.py"
    with pytest.raises(manifest.ManifestError, match="owner-root-relative"):
        manifest.ensure_owner_relative(root, str(root / "safe.py"), label="absolute")
    with pytest.raises(manifest.ManifestError, match="escapes owner_root"):
        manifest.ensure_owner_relative(root, "../outside/secret.py", label="traversal")
    with pytest.raises(manifest.ManifestError, match="escapes owner_root"):
        manifest.ensure_owner_relative(root, "escape.py", label="symlink")


@pytest.mark.parametrize(
    ("section", "path_value"),
    [
        ("docs", "PROJECT_MAP.md"),
        ("focused_tests", "tests/test_issue_qa_orchestrator.py"),
        ("stage_owner", "tools/issue_qa_orchestrator.py"),
        ("stage_test", "tests/test_issue_qa_orchestrator.py"),
        ("diagnostic_owner", "tools/issue_qa_orchestrator.py"),
    ],
)
def test_excluded_routeable_refs_are_rejected(section: str, path_value: str) -> None:
    sources, stages, diagnostics, owner_root = real_manifests()
    sources = deepcopy(sources)
    stages = deepcopy(stages)
    diagnostics = deepcopy(diagnostics)
    sources.setdefault("excluded", {}).setdefault("paths", []).append(path_value)
    exclusions = manifest.build_exclusion_policy(sources, owner_root)
    with pytest.raises(manifest.ManifestError, match="path is excluded"):
        if section in {"docs", "focused_tests"}:
            manifest.validate_sources(sources, owner_root, exclusions)
        elif section in {"stage_owner", "stage_test"}:
            manifest.validate_stages(stages, owner_root, exclusions)
        else:
            manifest.validate_diagnostics(diagnostics, owner_root, exclusions)


def test_directory_and_exact_exclusions_do_not_match_similar_safe_names(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    policy = manifest.build_exclusion_policy({"excluded": {"paths": ["logs/", ".env"]}}, root)
    assert manifest._is_rel_excluded(policy, "logs/app.log") is True
    assert manifest._is_rel_excluded(policy, ".env") is True
    assert manifest._is_rel_excluded(policy, "logs_safe/app.log") is False
    assert manifest._is_rel_excluded(policy, ".env.example") is False


def test_navigation_registry_does_not_index_excluded_refs() -> None:
    registry = navigation.build_registry("qapairs")
    sources = registry["sources"]
    exclusions = manifest.build_exclusion_policy(sources, registry["adapter"].root)
    assert not any(manifest._is_rel_excluded(exclusions, path) for path in registry["active_paths"])
    assert not any(manifest._is_rel_excluded(exclusions, str(record["path"])) for record in registry["records"])


def test_real_qapairs_manifest_cli_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "qapairs_context_manifest.py")],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Qapairs context manifest OK" in result.stdout


def test_qapairs_manifest_cli_rejects_override_options() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "qapairs_context_manifest.py"), "--sources", "x.json"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr
