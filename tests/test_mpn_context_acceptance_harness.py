from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "mpn_context_acceptance_generate.py"
SCORER = ROOT / "scripts" / "mpn_context_acceptance_score.py"
INPUTS = ROOT / "tests" / "fixtures" / "mpn_context_acceptance_inputs_v1.json"
LABELS = ROOT / "tests" / "fixtures" / "mpn_context_acceptance_labels_v1.json"


def test_fixtures_are_label_blind_and_locked_labels_are_separate() -> None:
    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))["cases"]
    labels = json.loads(LABELS.read_text(encoding="utf-8"))["cases"]
    assert len(inputs) == 28
    assert len([row for row in labels if not row.get("abstain")]) == 8
    assert len([row for row in labels if row.get("abstain")]) >= 12
    assert {frozenset(row) for row in inputs} == {frozenset({"case_id", "diagnostic_code"})}
    assert {row["case_id"] for row in inputs} == {row["case_id"] for row in labels}
    assert not any("owner_path" in row or "symbol" in row or "abstain" in row for row in inputs)


def test_generator_source_has_no_label_scorer_or_owner_answers() -> None:
    source = GENERATOR.read_text(encoding="utf-8")
    labels = json.loads(LABELS.read_text(encoding="utf-8"))["cases"]
    assert "mpn_context_acceptance_labels" not in source
    assert "mpn_context_acceptance_score" not in source
    assert "expected" not in source.lower()
    assert "mpn_dependency_card" not in source
    assert "cc-daemons" not in source
    assert "projects/mpn/" not in source
    for row in labels:
        if not row.get("abstain"):
            assert row["owner_path"] not in source


def test_generator_uses_only_inputs_and_navigation_gate_modules() -> None:
    tree = ast.parse(GENERATOR.read_text(encoding="utf-8"))
    constants = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert "mpn" in constants
    assert not any("labels" in value or "score" in value for value in constants)
    assert not any("dependency" in value or "cc-daemons" in value for value in constants)


def test_harness_scripts_have_no_network_model_runtime_or_unbounded_write_imports() -> None:
    forbidden_imports = {
        "requests",
        "httpx",
        "aiohttp",
        "urllib",
        "socket",
        "subprocess",
        "openai",
        "anthropic",
        "ollama",
        "notebooklm",
        "mpn_local_pipeline",
    }
    for script in (GENERATOR, SCORER):
        tree = ast.parse(script.read_text(encoding="utf-8"))
        imported: set[str] = set()
        writes: list[ast.Call] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"write_text", "write_bytes"}:
                writes.append(node)
        assert not (imported & forbidden_imports), script
        assert writes, script


def test_generate_then_score_acceptance_hard_pass(tmp_path: Path) -> None:
    predictions = tmp_path / "predictions.json"
    score = tmp_path / "score.json"
    report = tmp_path / "report.md"
    gen = subprocess.run(
        [sys.executable, str(GENERATOR), "--inputs", str(INPUTS), "--output", str(predictions)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert gen.returncode == 0, gen.stdout + gen.stderr
    generated = json.loads(predictions.read_text(encoding="utf-8"))
    assert generated["project_id"] == "mpn"
    assert generated["privacy"] == "metadata_only_no_source_text"
    assert all("context" not in row and "source_text" not in row and "snippet" not in row for row in generated["predictions"])
    scored = subprocess.run(
        [sys.executable, str(SCORER), "--predictions", str(predictions), "--labels", str(LABELS), "--output", str(score), "--report", str(report)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert scored.returncode == 0, scored.stdout + scored.stderr
    result = json.loads(score.read_text(encoding="utf-8"))
    assert result["hard_pass"] is True
    assert result["metrics"]["positives_exact_owner_symbol"] == 8
    assert result["metrics"]["false_selections"] == 0
    assert result["metrics"]["false_abstentions"] == 0
    assert result["metrics"]["positive_context_budget_reached"] == 8
    assert result["metrics"]["cross_project_notebooks"] == 0
    assert result["metrics"]["unsafe_claims"] == 0
    assert report.read_text(encoding="utf-8").startswith("# MPN context acceptance v1")
