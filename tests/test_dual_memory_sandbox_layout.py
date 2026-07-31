from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SANDBOX = PROJECT_ROOT / "experiments" / "dual_memory_sandbox"
CTL_PATH = SANDBOX / "sandbox_ctl.py"


def load_ctl():
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    spec = importlib.util.spec_from_file_location("dual_memory_sandbox_ctl", CTL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sealed_learning_snapshot(ctl):
    snapshot = {
        "schema_version": 1,
        "sealed": True,
        "derived_from_phase": "L",
        "support_task_ids": ctl.LEARNING_TASK_IDS,
        "advisory_patterns": [
            {"advice_code": "input_normalization_advisory", "support_task_ids": ctl.LEARNING_TASK_IDS[:3]},
        ],
        "forbidden_source_task_ids": [],
        "contains_raw_logs": False,
        "contains_raw_prompts": False,
        "contains_private_labels": False,
    }
    snapshot["seal_hash"] = ctl.memory_snapshot_seal_hash(snapshot)
    return snapshot


def test_validate_layout_counts_and_partitions():
    ctl = load_ctl()
    result = ctl.validate_layout()
    assert result["ok"] is True
    assert result["tasks"] == 15
    assert sorted(result["families"].values(), key=lambda x: sorted(x.items())) == [
        {"learning": 3, "holdout": 2},
        {"learning": 3, "holdout": 2},
        {"learning": 3, "holdout": 2},
    ]


def test_public_private_partitions_and_subagent_manifest_contract():
    forbidden = {"expected_answer", "root_cause_code", "hidden_assertions", "success_criteria", "pattern_id", "verifier_id"}
    tasks = read_jsonl(SANDBOX / "public" / "tasks.jsonl")
    manifest = json.loads((SANDBOX / "experiment.json").read_text(encoding="utf-8"))
    assert len(tasks) == 15
    assert sum(1 for row in tasks if row["partition"] == "learning") == 9
    assert sum(1 for row in tasks if row["partition"] == "holdout") == 6
    assert manifest["runtime"] == "current_opencode_task_subagents"
    assert "opencode run" not in json.dumps(manifest).lower()
    assert "docker" not in json.dumps(manifest).lower()
    assert "callback" not in json.dumps(manifest).lower()
    assert "container" not in json.dumps(manifest).lower()
    agent_allowed = json.dumps(manifest["input_sets"]["agent_allowed"])
    scorer_allowed = json.dumps(manifest["input_sets"]["scorer_allowed"])
    assert "private" not in agent_allowed
    assert "private/labels.jsonl" in scorer_allowed
    assert "private/verifiers" in scorer_allowed
    for row in tasks:
        assert forbidden.isdisjoint(row)
        assert row["allowed_paths"] == ["src/subject.py"]
        assert row["fixture_ref"].startswith("public/fixtures/")


def test_prepare_copies_no_private_file_and_no_opencode_launch_profiles(tmp_path, monkeypatch):
    ctl = load_ctl()
    monkeypatch.setattr(ctl, "RUNS_ROOT", tmp_path)
    assert ctl.main(["prepare", "--task-id", "norm-holdout-01", "--run-id", "safe-run-1", "--mode", "baseline", "--phase", "B0"]) == 0
    run_dir = tmp_path / "safe-run-1"
    agent_files = [p.relative_to(run_dir / "agent").as_posix() for p in (run_dir / "agent").rglob("*") if p.is_file()]
    assert "src/subject.py" in agent_files
    assert "task_card.json" in agent_files
    assert all("private" not in item and "labels" not in item for item in agent_files)
    assert all("observer" not in item for item in agent_files)
    assert not (run_dir / "agent" / ".opencode").exists()
    allowlist = json.loads((run_dir / "allowlist_manifest.json").read_text(encoding="utf-8"))
    assert allowlist["private_copied"] is False
    assert allowlist["observer_outside_agent"] is True
    assert allowlist["opencode_launch_profiles_copied"] is False
    metadata = json.loads((run_dir / "observer" / "run_metadata_stub.json").read_text(encoding="utf-8"))
    assert metadata["collector_contract"] == "read_only_opencode_db_aggregates_after_future_task_subagent_session"
    assert metadata["task_api_model_selector"].startswith("not_available_here")
    assert metadata["actual_model_identity"] is None
    assert metadata["coverage"]["not_evaluable_until_session_collect"] is True


def test_prepare_enforces_phase_partitions_and_memory_snapshot(tmp_path, monkeypatch):
    ctl = load_ctl()
    monkeypatch.setattr(ctl, "RUNS_ROOT", tmp_path / "runs")
    assert ctl.main(["prepare", "--task-id", "norm-learn-01", "--run-id", "bad-b0", "--mode", "baseline", "--phase", "B0"]) == 1
    assert ctl.main(["prepare", "--task-id", "norm-holdout-01", "--run-id", "bad-l", "--mode", "learning", "--phase", "L"]) == 1
    assert ctl.main(["prepare", "--task-id", "norm-learn-01", "--run-id", "good-l", "--mode", "learning", "--phase", "L"]) == 0
    assert ctl.main(["prepare", "--task-id", "norm-holdout-01", "--run-id", "bad-memory", "--mode", "memory", "--phase", "M1"]) == 1
    good_snapshot = tmp_path / "good_snapshot.json"
    good_snapshot.write_text(json.dumps(sealed_learning_snapshot(ctl)) + "\n", encoding="utf-8")
    assert ctl.main(["prepare", "--task-id", "norm-holdout-01", "--run-id", "good-memory", "--mode", "memory", "--phase", "M1", "--memory-snapshot", str(good_snapshot)]) == 0
    assert (tmp_path / "runs" / "good-memory" / "agent" / "advisory_memory" / "sealed_learning_memory_snapshot.json").is_file()


def test_runtime_templates_are_empty_and_memory_seal_rejects_bad_fields(tmp_path):
    ctl = load_ctl()
    assert (SANDBOX / "runtime_templates" / "episodes.jsonl").read_text(encoding="utf-8") == ""
    assert (SANDBOX / "runtime_templates" / "patterns.jsonl").read_text(encoding="utf-8") == ""
    malicious = sealed_learning_snapshot(ctl)
    malicious["support_task_ids"] = [{"id": "norm-learn-01", "raw_prompt": "leak"}]
    bad_path = tmp_path / "malicious.json"
    bad_path.write_text(json.dumps(malicious) + "\n", encoding="utf-8")
    try:
        ctl.validate_memory_snapshot(bad_path)
    except ValueError as exc:
        assert "forbidden" in str(exc)
    else:
        raise AssertionError("nested raw/secret-ish snapshot field must fail")


def test_memory_snapshot_advisory_patterns_are_closed_and_need_three_learning_supports(tmp_path):
    ctl = load_ctl()
    valid = sealed_learning_snapshot(ctl)
    valid_path = tmp_path / "valid.json"
    valid_path.write_text(json.dumps(valid) + "\n", encoding="utf-8")
    assert ctl.validate_memory_snapshot(valid_path)["advisory_patterns"][0]["advice_code"] == "input_normalization_advisory"

    malformed = sealed_learning_snapshot(ctl)
    malformed["advisory_patterns"][0]["raw_prompt"] = "leak"
    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text(json.dumps(malformed) + "\n", encoding="utf-8")
    try:
        ctl.validate_memory_snapshot(malformed_path)
    except ValueError as exc:
        assert "forbidden" in str(exc) or "unknown field" in str(exc)
    else:
        raise AssertionError("advisory pattern with raw/unknown content must fail")

    unsupported = sealed_learning_snapshot(ctl)
    unsupported["advisory_patterns"][0]["support_task_ids"] = ctl.LEARNING_TASK_IDS[:2]
    unsupported_path = tmp_path / "unsupported.json"
    unsupported_path.write_text(json.dumps(unsupported) + "\n", encoding="utf-8")
    try:
        ctl.validate_memory_snapshot(unsupported_path)
    except ValueError as exc:
        assert "three distinct learning" in str(exc)
    else:
        raise AssertionError("advisory pattern with fewer than three supports must fail")


def test_tampered_fixture_hash_fails(tmp_path):
    sandbox_copy = tmp_path / "dual_memory_sandbox"
    shutil.copytree(SANDBOX, sandbox_copy, ignore=shutil.ignore_patterns("__pycache__"))
    target = sandbox_copy / "public" / "fixtures" / "norm-learn-01" / "src" / "subject.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    ctl = load_ctl()
    try:
        ctl.validate_layout(root=sandbox_copy, validate_hashes=False)
    except ValueError as exc:
        assert "fixture hash mismatch" in str(exc)
    else:
        raise AssertionError("tampered fixture hash should fail")


def make_db(path: Path, *, model: str = '{"id":"gpt-5.5","providerID":"openai","variant":"default"}', parent: str = "ses_parent", tool_error: bool = True):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE session (id TEXT, parent_id TEXT, agent TEXT, model TEXT, cost REAL, tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER, tokens_cache_read INTEGER, tokens_cache_write INTEGER, time_created INTEGER, time_updated INTEGER)")
    conn.execute("CREATE TABLE part (session_id TEXT, data TEXT)")
    conn.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", ("ses_child", parent, "worker", model, 0.5, 10, 3, 2, 1, 4, 1000, 1250))
    parts = [
        {"type": "tool", "state": {"status": "completed"}},
        {"type": "tool", "state": {"status": "error" if tool_error else "completed"}},
        {"type": "step-finish"},
        {"type": "retry"},
    ]
    conn.executemany("INSERT INTO part VALUES (?,?)", [("ses_child", json.dumps(p)) for p in parts])
    conn.commit()
    conn.close()


def test_session_metrics_success_parent_counts_and_no_raw_leakage(tmp_path):
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    from telemetry.session_metrics import collect_session_metrics
    db = tmp_path / "opencode.sqlite"
    make_db(db, model=json.dumps({"providerID": "openai", "id": "gpt-5.5", "variant": "default", "ignored": {"raw": "nope"}}))
    result = collect_session_metrics(session_id="ses_child", task_id="norm-holdout-01", mode="baseline", phase="B0", expected_parent_id="ses_parent", db_path=db)
    assert result["parent_id"] == "ses_parent"
    assert result["actual_agent"] == "worker"
    assert result["actual_model_identity"] == '{"model_id":"gpt-5.5","provider_id":"openai","variant":"default"}'
    assert result["diagnostics"]["time_created_ms"] == 1000
    assert result["diagnostics"]["time_updated_ms"] == 1250
    assert "time_created_ms" not in result["resources"]
    assert "time_updated_ms" not in result["resources"]
    assert result["resources"]["tokens_cache_read"] == 1
    assert result["resources"]["tokens_cache_write"] == 4
    assert result["resources"]["cached_tokens"] == 5
    assert result["resources"]["total_tokens"] == 15
    assert result["resources"]["tool_calls"] == 2
    assert result["resources"]["failed_tool_calls"] == 1
    assert result["resources"]["model_calls"] == 1
    assert result["resources"]["retries"] == 1
    dumped = json.dumps(result).lower()
    assert "raw" not in dumped and "prompt" not in dumped and "body" not in dumped


def test_session_metrics_parent_mismatch_and_malformed_model_fail(tmp_path):
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    from telemetry.session_metrics import SessionMetricsError, collect_session_metrics
    db = tmp_path / "opencode.sqlite"
    make_db(db, model='{"provider":')
    try:
        collect_session_metrics(session_id="ses_child", task_id="norm-holdout-01", mode="baseline", phase="B0", expected_parent_id="ses_other", db_path=db)
    except SessionMetricsError as exc:
        assert "parent mismatch" in str(exc)
    else:
        raise AssertionError("parent mismatch must fail")
    try:
        collect_session_metrics(session_id="ses_child", task_id="norm-holdout-01", mode="baseline", phase="B0", expected_parent_id="ses_parent", db_path=db)
    except SessionMetricsError as exc:
        assert "malformed model JSON" in str(exc)
    else:
        raise AssertionError("malformed model JSON must fail")


def test_session_metrics_missing_provider_or_plain_string_model_fail(tmp_path):
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    from telemetry.session_metrics import SessionMetricsError, collect_session_metrics
    db = tmp_path / "missing_provider.sqlite"
    make_db(db, model=json.dumps({"id": "gpt-5.5", "variant": "default"}))
    try:
        collect_session_metrics(session_id="ses_child", task_id="norm-holdout-01", mode="baseline", phase="B0", expected_parent_id="ses_parent", db_path=db)
    except SessionMetricsError as exc:
        assert "provider_id" in str(exc)
    else:
        raise AssertionError("missing provider must fail")
    db2 = tmp_path / "plain.sqlite"
    make_db(db2, model="gpt-5.5")
    try:
        collect_session_metrics(session_id="ses_child", task_id="norm-holdout-01", mode="baseline", phase="B0", expected_parent_id="ses_parent", db_path=db2)
    except SessionMetricsError as exc:
        assert "plain string" in str(exc)
    else:
        raise AssertionError("plain model string must fail")


def _summary(task_id: str, mode: str, session_id: str, pass_value: bool = True):
    return {
        "task_id": task_id,
        "mode": mode,
        "phase": "B0" if mode == "baseline" else "M1",
        "session_id": session_id,
        "parent_id": "ses_experiment_parent",
        "fresh_subagent_session": True,
        "actual_agent": "worker",
        "actual_model_identity": '{"model_id":"gpt-5.5","provider_id":"openai","variant":"default"}',
        "task_fingerprint": "tf-" + task_id,
        "fixture_hash": "fh-" + task_id,
        "memory_snapshot_hash": None if mode == "baseline" else "memhash-1",
        "coverage": {"present": ["wall", "tokens", "model_calls", "tools"]},
        "quality": {"pass": pass_value, "false_success": False, "safety_regression": False, "budget_regression": False},
        "diagnostics": {"time_created_ms": 1000, "time_updated_ms": 1100},
        "resources": {"wall_ms": 100, "total_tokens": 1000, "input_tokens": 800, "output_tokens": 100, "tokens_cache_read": 3, "tokens_cache_write": 4, "cached_tokens": 7, "reasoning_tokens": 100, "model_calls": 1, "estimated_provider_cost": 0.01, "tool_calls": 10, "failed_tool_calls": 0, "retries": 0},
    }


def test_compare_subagent_primary_claim_evaluable_and_mismatch_not_evaluable():
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    from telemetry.compare import compare_paired_summaries
    ctl = load_ctl()
    baseline = [_summary(t, "baseline", "ses_b" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory = [_summary(t, "memory", "ses_m" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["quality_gate"] == "pass"
    assert result["overall_resource_claim"] == "evaluable"
    assert all(item["outcome"] == "unchanged_pass" for item in result["quality_transitions"])
    assert result["claim_strength"] == "observational_evidence_not_causal_container_proof"
    assert "time_created_ms" not in result["paired_deltas"][0]["families"]["opencode_usage"]["deltas"]
    memory[0]["actual_model_identity"] = '{"model_id":"gpt-5.5","provider_id":"openai","variant":"other"}'
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["overall_resource_claim"] == "not_evaluable"


def test_compare_mixed_parent_across_pairs_not_evaluable():
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    from telemetry.compare import compare_paired_summaries
    ctl = load_ctl()
    baseline = [_summary(t, "baseline", "ses_b" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory = [_summary(t, "memory", "ses_m" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    baseline[1]["parent_id"] = "ses_other_experiment_parent"
    memory[1]["parent_id"] = "ses_other_experiment_parent"
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["quality_gate"] == "not_evaluable"
    assert result["overall_resource_claim"] == "not_evaluable"
    assert "one experiment-wide parent_id" in result["reason"]


def test_compare_mixed_actual_agent_or_model_across_pairs_not_evaluable():
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    from telemetry.compare import compare_paired_summaries
    ctl = load_ctl()
    baseline = [_summary(t, "baseline", "ses_b" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory = [_summary(t, "memory", "ses_m" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    baseline[1]["actual_agent"] = "other-worker"
    memory[1]["actual_agent"] = "other-worker"
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["quality_gate"] == "not_evaluable"
    assert result["overall_resource_claim"] == "not_evaluable"
    assert "one experiment-wide actual_agent" in result["reason"]

    baseline = [_summary(t, "baseline", "ses_b" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory = [_summary(t, "memory", "ses_m" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    other_model = '{"model_id":"gpt-5.5","provider_id":"openai","variant":"other"}'
    baseline[1]["actual_model_identity"] = other_model
    memory[1]["actual_model_identity"] = other_model
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["quality_gate"] == "not_evaluable"
    assert result["overall_resource_claim"] == "not_evaluable"
    assert "one experiment-wide actual_model_identity" in result["reason"]


def test_compare_missing_quality_duplicate_task_and_memory_contract_rejected():
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    from telemetry.compare import compare_paired_summaries
    ctl = load_ctl()
    baseline = [_summary(t, "baseline", "ses_b" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory = [_summary(t, "memory", "ses_m" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    del baseline[0]["quality"]["pass"]
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["quality_gate"] == "not_evaluable"
    baseline = [_summary(t, "baseline", "ses_b" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    baseline[1]["task_id"] = baseline[0]["task_id"]
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert "duplicate" in result["reason"]
    baseline = [_summary(t, "baseline", "ses_b" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory[0]["memory_snapshot_hash"] = None
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["overall_resource_claim"] == "not_evaluable"


def write_quality(path: Path, task_id: str, **overrides):
    data = {"schema_version": 1, "task_id": task_id, "pass": True, "false_success": False, "safety_regression": False, "budget_regression": False, "scorer_id": "unit_scorer", "sealed": True}
    data.update(overrides)
    path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_collect_session_controlled_path_and_no_arbitrary_output(tmp_path, monkeypatch):
    ctl = load_ctl()
    monkeypatch.setattr(ctl, "RUNS_ROOT", tmp_path / "runs")
    assert not hasattr(ctl.build_parser().parse_args(["collect-session", "--run-dir", str(tmp_path / "runs" / "r1"), "--session-id", "ses_child"]), "output")
    db = tmp_path / "opencode.sqlite"
    make_db(db)
    outside = tmp_path / "outside"
    outside.mkdir()
    assert ctl.main(["collect-session", "--run-dir", str(outside), "--session-id", "ses_child", "--db-path", str(db)]) == 1
    assert ctl.main(["prepare", "--task-id", "norm-holdout-01", "--run-id", "r1", "--mode", "baseline", "--phase", "B0"]) == 0
    run_dir = tmp_path / "runs" / "r1"
    assert ctl.main(["collect-session", "--run-dir", str(run_dir), "--session-id", "ses_child", "--expected-parent-id", "ses_parent", "--db-path", str(db)]) == 0
    assert (run_dir / "observer" / "session_summary.json").is_file()


def test_seal_run_requires_valid_quality_and_merges_session_summary(tmp_path, monkeypatch):
    ctl = load_ctl()
    monkeypatch.setattr(ctl, "RUNS_ROOT", tmp_path / "runs")
    db = tmp_path / "opencode.sqlite"
    make_db(db)
    assert ctl.main(["prepare", "--task-id", "norm-holdout-01", "--run-id", "r2", "--mode", "baseline", "--phase", "B0"]) == 0
    run_dir = tmp_path / "runs" / "r2"
    assert ctl.main(["collect-session", "--run-dir", str(run_dir), "--session-id", "ses_child", "--expected-parent-id", "ses_parent", "--db-path", str(db)]) == 0
    assert ctl.main(["seal-run", "--run-dir", str(run_dir), "--quality-result", str(tmp_path / "missing_quality.json"), "--ack-run-experiment"]) == 1
    bad_quality = write_quality(tmp_path / "bad_quality.json", "norm-holdout-01", **{"pass": "yes"})
    assert ctl.main(["seal-run", "--run-dir", str(run_dir), "--quality-result", str(bad_quality), "--ack-run-experiment"]) == 1
    good_quality = write_quality(tmp_path / "quality.json", "norm-holdout-01")
    assert ctl.main(["seal-run", "--run-dir", str(run_dir), "--quality-result", str(good_quality), "--ack-run-experiment"]) == 0
    summary = json.loads((run_dir / "observer" / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["session_id"] == "ses_child"
    assert summary["quality"]["pass"] is True
    assert summary["memory_snapshot_hash"] is None
    assert "session_summary_sha256" in summary and "quality_result_sha256" in summary
    assert (run_dir / "observer" / "run_seal.json").is_file()


def test_compare_quality_gate_before_resource_claim_and_same_session_rejected():
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    from telemetry.compare import compare_paired_summaries
    ctl = load_ctl()
    baseline = [_summary(t, "baseline", "ses_b" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory = [_summary(t, "memory", "ses_m" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory[0]["quality"]["safety_regression"] = True
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["quality_gate"] == "fail"
    assert result["overall_resource_claim"] == "not_reported"
    memory = [_summary(t, "memory", "ses_m" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory[0]["session_id"] = baseline[0]["session_id"]
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["overall_resource_claim"] == "not_evaluable"


def test_compare_fail_to_pass_is_quality_improvement_and_resource_evaluable_when_all_m1_pass():
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    from telemetry.compare import compare_paired_summaries
    ctl = load_ctl()
    baseline = [_summary(t, "baseline", "ses_b" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory = [_summary(t, "memory", "ses_m" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    baseline[0]["quality"]["pass"] = False
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["quality_gate"] == "pass"
    assert result["overall_resource_claim"] == "evaluable"
    assert result["quality_transitions"][0] == {"task_id": ctl.HOLDOUT_TASK_IDS[0], "baseline_pass": False, "memory_pass": True, "outcome": "improved"}


def test_compare_pass_to_fail_and_memory_false_success_reject_resource_claim():
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    from telemetry.compare import compare_paired_summaries
    ctl = load_ctl()
    baseline = [_summary(t, "baseline", "ses_b" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory = [_summary(t, "memory", "ses_m" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory[0]["quality"]["pass"] = False
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["quality_gate"] == "fail"
    assert result["overall_resource_claim"] == "not_reported"
    assert result["quality_transitions"][0]["outcome"] == "regressed"

    memory = [_summary(t, "memory", "ses_m" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory[0]["quality"]["false_success"] = True
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["quality_gate"] == "fail"
    assert result["overall_resource_claim"] == "not_reported"


def test_compare_still_failed_memory_blocks_resource_claim():
    if str(SANDBOX) not in sys.path:
        sys.path.insert(0, str(SANDBOX))
    from telemetry.compare import compare_paired_summaries
    ctl = load_ctl()
    baseline = [_summary(t, "baseline", "ses_b" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    memory = [_summary(t, "memory", "ses_m" + str(i)) for i, t in enumerate(ctl.HOLDOUT_TASK_IDS)]
    baseline[0]["quality"]["pass"] = False
    memory[0]["quality"]["pass"] = False
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=ctl.HOLDOUT_TASK_IDS)
    assert result["quality_gate"] == "fail"
    assert result["overall_resource_claim"] == "not_reported"
    assert result["quality_transitions"][0]["outcome"] == "still_failed"
