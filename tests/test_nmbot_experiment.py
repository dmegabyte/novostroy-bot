from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_experiment.py"


def load_module(name: str = "nmbot_experiment_test"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def profile(case_id: str = "not-run") -> dict:
    return {
        "schema": "nmbot.development.v1",
        "version": 1,
        "capabilities": ["prompt_candidate", "parameter_overlay", "focused_check", "full_check"],
        "check_scope": "v2",
        "prompt_check": {"kind": "presenter"},
        "parameter_profile": {
            "schema": "nmbot.parameter_profile.v1",
            "version": 1,
            "parameters": {
                "model": {
                    "type": "string",
                    "default": "google/gemini-2.5-flash",
                    "max_length": 160,
                    "pattern": "[A-Za-z0-9_.:/@+~-]+",
                }
            },
        },
        "case_set": {"id": case_id, "fingerprint": "not-run"},
    }


def make_repo(tmp_path: Path, *, stage_id: str = "v2.writer", case_id: str = "not-run") -> tuple[Path, Path]:
    for directory in ("config", "src", "docs", "tests", "prompts", "scripts"):
        (tmp_path / directory).mkdir(exist_ok=True)
    (tmp_path / "src" / "writer.py").write_text("def writer_request_payload():\n    return {}\n", encoding="utf-8")
    (tmp_path / "docs" / "contract.md").write_text("contract\n", encoding="utf-8")
    (tmp_path / "tests" / "test_writer.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / "prompts" / "writer.txt").write_text("baseline prompt\n", encoding="utf-8")
    registry = {
        "schema": "nmbot.stage_map.v1",
        "schema_version": 1,
        "active_by_version": {"v2": "v2.path.v1"},
        "paths": {"v2.path.v1": {"stage_ids": [stage_id]}},
        "stages": {
            stage_id: {
                "source": "src/writer.py",
                "source_symbol": "writer_request_payload",
                "prompt": "prompts/writer.txt",
                "payload_stage": "conversation_answer_writer",
                "doc": "docs/contract.md",
                "test": "tests/test_writer.py",
                "development": profile(case_id),
            }
        },
    }
    stage_map = tmp_path / "config" / "stage.json"
    stage_map.write_text(json.dumps(registry), encoding="utf-8")
    return stage_map, tmp_path / "store"


def cli_args(root: Path, stage_map: Path, store: Path, *items: str) -> list[str]:
    return [*items, "--root", str(root), "--stage-map", str(stage_map), "--store-dir", str(store)]


def start_one(mod, root: Path, stage_map: Path, store: Path, experiment_id: str = "exp-one", **extra):
    values = dict(
        root=str(root), stage_map=str(stage_map), store_dir=str(store), stage="v2.writer", title="candidate",
        prompt_file=None, prompt=None, param=[], id=experiment_id, hypothesis="H1", prompt_version="P1", model_version="M1", json=True,
    )
    values.update(extra)
    return mod.start(SimpleNamespace(**values))


def command_args(root: Path, stage_map: Path, store: Path, experiment_id: str = "exp-one", **extra):
    values = dict(root=str(root), stage_map=str(stage_map), store_dir=str(store), id=experiment_id, json=True)
    values.update(extra)
    return SimpleNamespace(**values)


def test_current_profile_is_declared_only_for_writer() -> None:
    registry = json.loads((ROOT / "config" / "nmbot_stage_map.json").read_text(encoding="utf-8"))
    developed = {key: row["development"] for key, row in registry["stages"].items() if "development" in row}
    assert set(developed) == {"v2.response_writer"}
    current = developed["v2.response_writer"]
    assert current["schema"] == "nmbot.development.v1"
    assert current["parameter_profile"]["parameters"] == {
        "model": {"type": "string", "default": "google/gemini-2.5-flash", "max_length": 160, "pattern": "[A-Za-z0-9_.:/@+~-]+"}
    }


def test_start_is_isolated_and_does_not_write_raw_prompt_metadata(tmp_path) -> None:
    mod = load_module("experiment_start_test")
    stage_map, store = make_repo(tmp_path)
    candidate = tmp_path / "prompts" / "candidate.txt"
    candidate.write_text("changed prompt with private prose\n", encoding="utf-8")
    baseline_before = (tmp_path / "prompts" / "writer.txt").read_text(encoding="utf-8")

    result = start_one(mod, tmp_path, stage_map, store, prompt_file=str(candidate), param=["model=openai/gpt-5"])

    directory = store / "exp-one"
    assert (tmp_path / "prompts" / "writer.txt").read_text(encoding="utf-8") == baseline_before
    assert (directory / "candidate_prompt.txt").read_text(encoding="utf-8") == candidate.read_text(encoding="utf-8")
    assert json.loads((directory / "parameter_overlay.json").read_text())["model"] == "openai/gpt-5"
    dumped = json.dumps(result, ensure_ascii=False)
    assert "changed prompt with private prose" not in dumped
    assert result["hashes"]["candidate"].startswith("sha256:")


def test_unknown_stage_and_invalid_param_fail_before_subprocess(tmp_path, monkeypatch) -> None:
    mod = load_module("experiment_validation_test")
    stage_map, store = make_repo(tmp_path)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: pytest.fail("subprocess must not run"))
    with pytest.raises(mod.ExperimentError, match="unknown stage"):
        start_one(mod, tmp_path, stage_map, store, stage="missing")
    with pytest.raises(mod.ExperimentError, match="unknown parameter"):
        start_one(mod, tmp_path, stage_map, store, param=["temperature=1"])
    assert not store.exists()


def test_check_dry_run_lists_actions_without_subprocess(tmp_path, monkeypatch) -> None:
    mod = load_module("experiment_dry_run_test")
    stage_map, store = make_repo(tmp_path)
    start_one(mod, tmp_path, stage_map, store)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: pytest.fail("subprocess must not run"))

    code, result = mod.check(command_args(tmp_path, stage_map, store, dry_run=True, full=True))

    assert code == 0
    assert [item["kind"] for item in result["actions"]] == ["prompt_static", "focused", "full"]
    assert all(item["status"] == "planned" for item in result["summaries"])


def test_check_stops_on_first_failure_and_drops_child_output(tmp_path, monkeypatch) -> None:
    mod = load_module("experiment_failure_test")
    stage_map, store = make_repo(tmp_path)
    start_one(mod, tmp_path, stage_map, store)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 9, stdout="TOP SECRET", stderr="token=secret")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    code, result = mod.check(command_args(tmp_path, stage_map, store, dry_run=False, full=True))

    assert code == 1 and len(calls) == 1
    saved = (store / "exp-one" / "check.json").read_text(encoding="utf-8")
    assert "TOP SECRET" not in saved and "token=secret" not in saved
    assert result["summaries"] == [{"kind": "prompt_static", "status": "failed", "returncode": 9}]


def test_registry_evolution_is_declarative_and_old_experiment_becomes_orphaned(tmp_path) -> None:
    mod = load_module("experiment_evolution_test")
    stage_map, store = make_repo(tmp_path)
    start_one(mod, tmp_path, stage_map, store)
    registry = json.loads(stage_map.read_text(encoding="utf-8"))
    row = registry["stages"].pop("v2.writer")
    registry["stages"]["v2.writer.renamed"] = row
    registry["paths"]["v2.path.v1"]["stage_ids"] = ["v2.writer.renamed"]
    stage_map.write_text(json.dumps(registry), encoding="utf-8")

    result = mod.diff(command_args(tmp_path, stage_map, store))

    assert result["status"] == "orphaned"
    assert result["orphan_reasons"] == ["stage_removed"]


def test_ambiguous_active_path_is_blocked_before_store_write(tmp_path) -> None:
    mod = load_module("experiment_ambiguous_test")
    stage_map, store = make_repo(tmp_path)
    registry = json.loads(stage_map.read_text(encoding="utf-8"))
    registry["paths"]["v2.other.v1"] = {"stage_ids": ["v2.writer"]}
    registry["active_by_version"]["other"] = "v2.other.v1"
    stage_map.write_text(json.dumps(registry), encoding="utf-8")

    with pytest.raises(mod.ExperimentError, match="ambiguous"):
        start_one(mod, tmp_path, stage_map, store)
    assert not store.exists()


def test_report_contains_no_raw_content_and_compare_refuses_incompatible_receipts(tmp_path) -> None:
    mod = load_module("experiment_receipt_test")
    stage_map, store = make_repo(tmp_path)
    candidate = tmp_path / "prompts" / "candidate.txt"
    candidate.write_text("Authorization: Bearer very-secret-token\n", encoding="utf-8")
    start_one(mod, tmp_path, stage_map, store, experiment_id="left", prompt_file=str(candidate))
    start_one(mod, tmp_path, stage_map, store, experiment_id="right")
    for experiment_id in ("left", "right"):
        mod.report(command_args(tmp_path, stage_map, store, experiment_id))
    right_path = store / "right" / "workflow_receipt.json"
    right = json.loads(right_path.read_text(encoding="utf-8"))
    right["case_set"]["id"] = "other-cases"
    right_path.write_text(json.dumps(right), encoding="utf-8")

    receipt_text = (store / "left" / "workflow_receipt.json").read_text(encoding="utf-8")
    assert "very-secret-token" not in receipt_text
    result = mod.compare(SimpleNamespace(root=str(tmp_path), stage_map=str(stage_map), store_dir=str(store), left="left", right="right", json=True))
    assert result["status"] == "incomparable"
    assert "mismatch:case_set.id" in result["reasons"]


def test_diff_redacts_credentials(tmp_path) -> None:
    mod = load_module("experiment_redaction_test")
    stage_map, store = make_repo(tmp_path)
    candidate = tmp_path / "prompts" / "candidate.txt"
    candidate.write_text("api_key=sk-abcdefghijklmnopqrstuvwxyz\n", encoding="utf-8")
    start_one(mod, tmp_path, stage_map, store, prompt_file=str(candidate))
    result = mod.diff(command_args(tmp_path, stage_map, store))
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in result["prompt_diff"]
    assert "[REDACTED]" in result["prompt_diff"]


def test_wrapper_preserves_experiment_argv_and_returncode(monkeypatch) -> None:
    wrapper_path = ROOT / "scripts" / "nmbot.py"
    spec = importlib.util.spec_from_file_location("nmbot_wrapper_experiment_test", wrapper_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    calls = []

    def fake_run(argv, cwd, check):
        calls.append((argv, cwd, check))
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["experiment", "diff", "H1", "--json"]) == 7
    assert calls == [([sys.executable, "scripts/nmbot_experiment.py", "diff", "H1", "--json"], mod.ROOT, False)]
