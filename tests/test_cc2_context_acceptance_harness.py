from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "cc2_context_acceptance_generate.py"
SCORER = ROOT / "scripts" / "cc2_context_acceptance_score.py"
INPUTS = ROOT / "tests" / "fixtures" / "cc2_context_acceptance_inputs_v1.json"
LABELS = ROOT / "tests" / "fixtures" / "cc2_context_acceptance_labels_v1.json"
DIAGNOSTICS = ROOT / "config" / "cc2_diagnostic_codes.json"


def test_fixtures_are_label_blind_and_labels_match_current_diagnostics() -> None:
    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))["cases"]
    labels = json.loads(LABELS.read_text(encoding="utf-8"))["cases"]
    diagnostics = json.loads(DIAGNOSTICS.read_text(encoding="utf-8"))["diagnostics"]
    positives = [row for row in labels if not row.get("abstain")]
    negatives = [row for row in labels if row.get("abstain")]
    assert len(positives) == len(diagnostics) == 15
    assert len(negatives) >= 15
    assert {frozenset(row) for row in inputs} == {frozenset({"case_id", "diagnostic_code"})}
    assert {row["case_id"] for row in inputs} == {row["case_id"] for row in labels}
    assert not any("owner_source" in row or "owner_symbol" in row or "abstain" in row for row in inputs)
    positive_by_code = {inp["diagnostic_code"]: label for inp, label in zip(inputs, labels) if not label.get("abstain")}
    assert set(positive_by_code) == {row["code"] for row in diagnostics}
    for row in diagnostics:
        label = positive_by_code[row["code"]]
        assert label["owner_source"] == row["owner_source"]
        assert label["owner_symbol"] == row["owner_symbol"]


def test_generator_source_has_no_label_scorer_network_model_or_answer_access() -> None:
    source = GENERATOR.read_text(encoding="utf-8")
    labels = json.loads(LABELS.read_text(encoding="utf-8"))["cases"]
    assert "cc2_context_acceptance_labels" not in source
    assert "cc2_context_acceptance_score" not in source
    assert "expected" not in source.lower()
    for row in labels:
        if not row.get("abstain"):
            assert row["owner_source"] not in source
            if row["owner_symbol"] != "main":
                assert row["owner_symbol"] not in source
    tree = ast.parse(source)
    forbidden_imports = {"requests", "httpx", "aiohttp", "urllib", "socket", "subprocess", "openai", "anthropic", "ollama", "notebooklm"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & forbidden_imports)


def test_harness_scripts_write_only_explicit_outputs_and_no_forbidden_imports() -> None:
    forbidden_imports = {"requests", "httpx", "aiohttp", "urllib", "socket", "openai", "anthropic", "ollama", "notebooklm"}
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
    assert generated["project_id"] == "cc2"
    assert generated["privacy"] == "metadata_only_no_source_text"
    forbidden_keys = {"context", "source_text", "snippet", "body", "title", "raw_log", "secret", "secrets", "source_body"}
    assert all(not (forbidden_keys & set(row)) for row in generated["predictions"])
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
    assert result["metrics"]["positives_exact_owner_symbol"] == 15
    assert result["metrics"]["false_selections"] == 0
    assert result["metrics"]["false_abstentions"] == 0
    assert result["metrics"]["positive_budget_exact"] == 15
    assert result["metrics"]["unsafe_claims"] == 0
    assert report.read_text(encoding="utf-8").startswith("# CC2 context acceptance v1")
