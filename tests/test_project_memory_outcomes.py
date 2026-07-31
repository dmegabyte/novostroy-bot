from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project_memory_outcomes.py"


def load_module():
    spec = importlib.util.spec_from_file_location("project_memory_outcomes_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def base_outcome(n: int = 1) -> dict:
    fp = f"{n:064x}"[-64:]
    return {
        "schema": "privacy_safe_outcome.v1",
        "outcome_id": f"outcome:{n}",
        "case_fingerprint": fp,
        "project_id": "nmbot",
        "policy_version": "nmbot-passive-v1",
        "policy_delta": "passive_local_outcome_store_only",
        "features": {
            "schema": "safe_case_features.v1",
            "case_fingerprint": fp,
            "project_id": "nmbot",
            "policy_version": "nmbot-passive-v1",
            "route": "docs",
            "evidence_type": "docs",
            "target_kind": "docs",
            "candidate_count": n,
            "selected_source_count": 1,
            "lines_loaded": 10,
            "chars_loaded": 1000,
            "verifier_result": "focused_test_passed",
            "no_raw_query_code_log_secret": True,
        },
        "diagnosis_d1_d6": {
            "D1": "ok",
            "D2": "ok",
            "D3": "ok",
            "D4": "ok",
            "D5": "not_applicable",
            "D6": "ok",
        },
        "result": "passed",
        "gate_status": "pass",
        "failure_source": "agent_tool",
        "artifact_ref_id": f"artifact:{n}",
        "created_at": f"2026-07-26T00:00:{n:02d}Z",
    }


def shadow_outcome(n: int = 1) -> dict[str, Any]:
    fp = f"{n:064x}"[-64:]
    cand1 = f"{n + 100:064x}"[-64:]
    cand2 = f"{n + 200:064x}"[-64:]
    return {
        "schema": "privacy_safe_shadow_outcome.v1",
        "outcome_id": f"shadow:{n}",
        "task_fingerprint": fp,
        "project_id": "nmbot",
        "policy_version": "nmbot-passive-v1",
        "policy_delta": "passive_local_outcome_store_only",
        "features": {
            "schema": "safe_shadow_features.v1",
            "task_fingerprint": fp,
            "project_id": "nmbot",
            "policy_version": "nmbot-passive-v1",
            "phase": "shadow",
            "candidate_ids": [cand1, cand2],
            "selected_target_id": cand1,
            "confirmed_or_corrected_target_id": cand1,
            "gate_result": "pass",
            "route": "docs",
            "evidence_type": "docs",
            "stop_reason": "definition_of_done",
            "lines_loaded": 10,
            "chars_loaded": 1000,
            "latency_ms": 25,
            "verifier_result": "confirmed_correct",
            "no_raw_query_code_log_secret": True,
        },
        "diagnosis_d1_d6": {
            "D1": "ok",
            "D2": "ok",
            "D3": "ok",
            "D4": "ok",
            "D5": "not_applicable",
            "D6": "ok",
        },
        "result": "passed",
        "gate_status": "pass",
        "failure_source": "agent_tool",
        "artifact_ref_id": f"artifact:{n}",
        "created_at": f"2026-07-26T00:01:{n:02d}Z",
    }


def assert_invalid_code(mod: Any, outcome: dict[str, Any], code: str) -> None:
    result = mod.validate_outcome(outcome)
    assert result["valid"] is False
    assert code in {item["code"] for item in result["errors"]}


def write_json(path: Path, data: dict) -> str:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path.relative_to(ROOT))


def test_strict_outcome_schema_and_snapshot_helper() -> None:
    mod = load_module()
    assert mod.validate_outcome(base_outcome())["valid"] is True
    bad = base_outcome()
    bad["extra"] = "x"
    result = mod.validate_outcome(bad)
    assert result["valid"] is False
    assert "top_level_keys" in {item["code"] for item in result["errors"]}

    snapshot = {
        "schema": "bank_snapshot.v1",
        "bank_snapshot_id": "snap:1",
        "project_id": "nmbot",
        "policy_version": "nmbot-passive-v1",
        "included_outcome_ids": ["outcome:1"],
        "excluded_failed_invalid_ids": ["outcome:2"],
        "frozen_at": "2026-07-26T00:00:00Z",
        "scorer_owner_tbd": "TBD",
    }
    assert mod.validate_bank_snapshot(snapshot)["valid"] is True


def test_v2_shadow_record_append_validate_summary_and_fsync(monkeypatch) -> None:
    mod = load_module()
    calls: list[int] = []
    monkeypatch.setattr(mod.os, "fsync", lambda fd: calls.append(fd))
    store = ROOT / "data" / "test_project_memory_shadow.jsonl"
    outcome_path = ROOT / "data" / "test_project_memory_shadow.json"
    store.unlink(missing_ok=True)
    try:
        rel = write_json(outcome_path, shadow_outcome(1))
        appended = mod.append_outcome(rel, store.relative_to(ROOT))
        assert appended["appended"] is True
        assert calls
        validation = mod.validate_store(store.relative_to(ROOT))
        assert validation["valid"] is True
        assert validation["record_count"] == 1
        listed = mod.list_outcomes("nmbot", store.relative_to(ROOT))
        assert listed["records"][0]["schema"] == "privacy_safe_shadow_outcome.v1"
        assert listed["records"][0]["fingerprint"] == shadow_outcome(1)["task_fingerprint"]
        payload = mod.summary(store.relative_to(ROOT))
        assert payload["ok"] is True
        assert payload["counts"]["phase"] == {"shadow": 1}
        assert payload["counts"]["verifier_result"] == {"confirmed_correct": 1}
        assert payload["counts"]["gate_status"] == {"pass": 1}
        assert payload["max_lines_loaded"] == 10
        assert payload["max_chars_loaded"] == 1000
        assert payload["correction_count"] == 0
    finally:
        store.unlink(missing_ok=True)
        outcome_path.unlink(missing_ok=True)


def test_v2_shadow_rejects_privacy_hash_budget_enum_and_gate_errors() -> None:
    mod = load_module()

    forbidden_fields = ["raw_question", "source_path", "raw_code", "raw_log", "body", "payload", "transcript", "label", "secret"]
    for field in forbidden_fields:
        bad = shadow_outcome()
        bad["features"][field] = "not echoed"
        result = mod.validate_outcome(bad)
        assert result["valid"] is False
        assert "forbidden_field" in {item["code"] for item in result["errors"]}
        assert "not echoed" not in json.dumps(result)

    cases: list[tuple[str, dict[str, Any], str]] = []
    bad_hash = shadow_outcome(); bad_hash["task_fingerprint"] = "A" * 64; bad_hash["features"]["task_fingerprint"] = "A" * 64
    cases.append(("task hash", bad_hash, "task_fingerprint"))
    bad_candidate = shadow_outcome(); bad_candidate["features"]["candidate_ids"] = ["abc"]
    cases.append(("candidate hash", bad_candidate, "candidate_ids_hash"))
    dup_candidate = shadow_outcome(); dup_candidate["features"]["candidate_ids"] = [f"{1:064x}", f"{1:064x}"]
    cases.append(("candidate dup", dup_candidate, "candidate_ids_unique"))
    many_candidates = shadow_outcome(); many_candidates["features"]["candidate_ids"] = [f"{i:064x}" for i in range(9)]
    cases.append(("candidate max", many_candidates, "candidate_ids_max"))
    too_many_lines = shadow_outcome(); too_many_lines["features"]["lines_loaded"] = 81
    cases.append(("line budget", too_many_lines, "lines_budget"))
    too_many_chars = shadow_outcome(); too_many_chars["features"]["chars_loaded"] = 8001
    cases.append(("char budget", too_many_chars, "chars_budget"))
    bad_phase = shadow_outcome(); bad_phase["features"]["phase"] = "warn"
    cases.append(("phase", bad_phase, "phase_not_allowed"))
    bad_verifier = shadow_outcome(); bad_verifier["features"]["verifier_result"] = "focused_test_passed"
    cases.append(("verifier", bad_verifier, "verifier_result"))
    bad_stop = shadow_outcome(); bad_stop["features"]["stop_reason"] = "done"
    cases.append(("stop", bad_stop, "stop_reason"))
    bad_gate = shadow_outcome(); bad_gate["features"]["gate_result"] = "denied"
    cases.append(("gate", bad_gate, "gate_status_inconsistent"))
    missing_confirmed = shadow_outcome(); missing_confirmed["features"]["confirmed_or_corrected_target_id"] = None
    cases.append(("confirmed required", missing_confirmed, "confirmed_target_required"))

    for _name, outcome, expected_code in cases:
        result = mod.validate_outcome(outcome)
        assert result["valid"] is False
        assert expected_code in {item["code"] for item in result["errors"]}


def test_shadow_semantic_invariants_positive_and_negative() -> None:
    mod = load_module()
    assert mod.validate_outcome(shadow_outcome(10))["valid"] is True

    corrected = shadow_outcome(11)
    corrected["features"]["confirmed_or_corrected_target_id"] = corrected["features"]["candidate_ids"][1]
    corrected["features"]["verifier_result"] = "corrected_target"
    corrected["result"] = "failed"
    corrected["gate_status"] = "fail_closed"
    corrected["features"]["gate_result"] = "fail_closed"
    assert mod.validate_outcome(corrected)["valid"] is True

    abstained = shadow_outcome(12)
    abstained["features"]["candidate_ids"] = []
    abstained["features"]["selected_target_id"] = None
    abstained["features"]["confirmed_or_corrected_target_id"] = None
    abstained["features"]["verifier_result"] = "abstained_no_support"
    abstained["result"] = "stopped"
    abstained["gate_status"] = "not_run"
    abstained["features"]["gate_result"] = "not_run"
    assert mod.validate_outcome(abstained)["valid"] is True

    invalid = shadow_outcome(13)
    invalid["features"]["confirmed_or_corrected_target_id"] = None
    invalid["features"]["verifier_result"] = "invalid_target"
    invalid["result"] = "invalid"
    invalid["gate_status"] = "denied"
    invalid["features"]["gate_result"] = "denied"
    assert mod.validate_outcome(invalid)["valid"] is True

    not_verified = shadow_outcome(14)
    not_verified["features"]["confirmed_or_corrected_target_id"] = None
    not_verified["features"]["verifier_result"] = "not_verified"
    not_verified["result"] = "stopped"
    not_verified["gate_status"] = "denied"
    not_verified["features"]["gate_result"] = "denied"
    assert mod.validate_outcome(not_verified)["valid"] is True

    bad_selected = shadow_outcome(15)
    bad_selected["features"]["selected_target_id"] = f"{999:064x}"[-64:]
    assert_invalid_code(mod, bad_selected, "selected_target_not_candidate")

    empty_selected = shadow_outcome(16)
    empty_selected["features"]["candidate_ids"] = []
    assert_invalid_code(mod, empty_selected, "empty_candidates_selected")

    bad_confirmed = shadow_outcome(17)
    bad_confirmed["features"]["confirmed_or_corrected_target_id"] = bad_confirmed["features"]["candidate_ids"][1]
    assert_invalid_code(mod, bad_confirmed, "confirmed_correct_invariant")

    bad_corrected = corrected.copy(); bad_corrected["features"] = corrected["features"].copy()
    bad_corrected["result"] = "passed"
    assert_invalid_code(mod, bad_corrected, "corrected_target_invariant")

    bad_abstained = abstained.copy(); bad_abstained["features"] = abstained["features"].copy()
    bad_abstained["gate_status"] = "denied"; bad_abstained["features"]["gate_result"] = "denied"
    assert_invalid_code(mod, bad_abstained, "abstained_no_support_invariant")

    bad_invalid = invalid.copy(); bad_invalid["features"] = invalid["features"].copy()
    bad_invalid["gate_status"] = "pass"; bad_invalid["features"]["gate_result"] = "pass"
    assert_invalid_code(mod, bad_invalid, "invalid_target_invariant")

    bad_not_verified = not_verified.copy(); bad_not_verified["features"] = not_verified["features"].copy()
    bad_not_verified["result"] = "failed"
    assert_invalid_code(mod, bad_not_verified, "not_verified_invariant")


def test_deep_audit_and_fail_closed_routes_are_valid_shadow_records() -> None:
    mod = load_module()
    audit = shadow_outcome(20)
    audit["features"]["route"] = "deep_audit_handoff"
    audit["features"]["evidence_type"] = "ambiguous"
    audit["features"]["stop_reason"] = "deep_audit_required"
    audit["features"]["confirmed_or_corrected_target_id"] = None
    audit["features"]["verifier_result"] = "not_verified"
    audit["result"] = "stopped"
    audit["gate_status"] = "denied"
    audit["features"]["gate_result"] = "denied"
    assert mod.validate_outcome(audit)["valid"] is True

    fail_closed = shadow_outcome(21)
    fail_closed["features"]["route"] = "fail_closed_cross_project"
    fail_closed["features"]["evidence_type"] = "production"
    fail_closed["features"]["confirmed_or_corrected_target_id"] = None
    fail_closed["features"]["verifier_result"] = "invalid_target"
    fail_closed["result"] = "stopped"
    fail_closed["gate_status"] = "fail_closed"
    fail_closed["features"]["gate_result"] = "fail_closed"
    assert mod.validate_outcome(fail_closed)["valid"] is True


def test_forbidden_raw_fields_and_secretish_values_are_rejected_without_echo() -> None:
    mod = load_module()
    bad = base_outcome()
    bad["features"]["raw_query"] = "show me api_key=SHOULD_NOT_ECHO"
    result = mod.validate_outcome(bad)
    text = json.dumps(result)
    assert result["valid"] is False
    assert "forbidden_field" in {item["code"] for item in result["errors"]}
    assert "SHOULD_NOT_ECHO" not in text

    secretish = base_outcome()
    secretish["artifact_ref_id"] = "sk-testsecret"
    result2 = mod.validate_outcome(secretish)
    assert result2["valid"] is False
    assert "secretish_value" in {item["code"] for item in result2["errors"]}


def test_v1_privacy_budget_boundaries_are_valid() -> None:
    mod = load_module()
    outcome = base_outcome(1)
    outcome["features"].update(
        {
            "candidate_count": 8,
            "selected_source_count": 2,
            "lines_loaded": 80,
            "chars_loaded": 8000,
        }
    )
    assert mod.validate_outcome(outcome)["valid"] is True


def test_v1_privacy_budget_overages_are_rejected_by_validate_and_append() -> None:
    mod = load_module()
    cases = [
        ("candidate_count", 9, "candidate_count_budget"),
        ("selected_source_count", 3, "selected_source_budget"),
        ("lines_loaded", 81, "lines_budget"),
        ("chars_loaded", 8001, "chars_budget"),
    ]
    store = ROOT / "data" / "test_project_memory_v1_budget.jsonl"
    outcome_path = ROOT / "data" / "test_project_memory_v1_budget.json"
    store.unlink(missing_ok=True)
    try:
        for index, (field, value, expected_code) in enumerate(cases, start=40):
            outcome = base_outcome(index)
            outcome["features"].update(
                {
                    "candidate_count": 8,
                    "selected_source_count": 2,
                    "lines_loaded": 80,
                    "chars_loaded": 8000,
                }
            )
            outcome["features"][field] = value

            result = mod.validate_outcome(outcome)
            assert result["valid"] is False
            assert expected_code in {item["code"] for item in result["errors"]}

            rel = write_json(outcome_path, outcome)
            appended = mod.append_outcome(rel, store.relative_to(ROOT))
            assert appended["appended"] is False
            assert expected_code in {item["code"] for item in appended["errors"]}
    finally:
        store.unlink(missing_ok=True)
        outcome_path.unlink(missing_ok=True)


def test_failure_source_diagnosis_project_and_policy_denials() -> None:
    mod = load_module()
    bad_source = base_outcome()
    bad_source["failure_source"] = "tool"
    assert "failure_source" in {item["code"] for item in mod.validate_outcome(bad_source)["errors"]}

    bad_diag = base_outcome()
    bad_diag["diagnosis_d1_d6"]["D2"] = "maybe"
    assert "diagnosis_value" in {item["code"] for item in mod.validate_outcome(bad_diag)["errors"]}

    qapairs_without_policy = base_outcome()
    qapairs_without_policy["project_id"] = "qapairs"
    qapairs_without_policy["features"]["project_id"] = "qapairs"
    assert "policy_version_denied" in {item["code"] for item in mod.validate_outcome(qapairs_without_policy)["errors"]}

    mismatch = base_outcome()
    mismatch["policy_version"] = "other"
    mismatch["features"]["policy_version"] = "other"
    assert "policy_version_denied" in {item["code"] for item in mod.validate_outcome(mismatch)["errors"]}


def test_append_validate_and_list_are_bounded_to_five_records() -> None:
    mod = load_module()
    store = ROOT / "data" / "test_project_memory_outcomes.jsonl"
    outcome_path = ROOT / "data" / "test_project_memory_outcome.json"
    store.unlink(missing_ok=True)
    try:
        for n in range(1, 7):
            rel = write_json(outcome_path, base_outcome(n))
            result = mod.append_outcome(rel, store.relative_to(ROOT))
            assert result["appended"] is True
        validation = mod.validate_store(store.relative_to(ROOT))
        assert validation["valid"] is True
        assert validation["record_count"] == 6
        listed = mod.list_outcomes("nmbot", store.relative_to(ROOT))
        assert listed["ok"] is True
        assert listed["count"] == 6
        assert len(listed["records"]) == 5
        assert "features" not in listed["records"][0]
    finally:
        store.unlink(missing_ok=True)
        outcome_path.unlink(missing_ok=True)


def test_duplicate_outcome_id_is_rejected_on_append_and_validate() -> None:
    mod = load_module()
    store = ROOT / "data" / "test_project_memory_duplicate.jsonl"
    outcome_path = ROOT / "data" / "test_project_memory_duplicate.json"
    store.unlink(missing_ok=True)
    try:
        rel = write_json(outcome_path, shadow_outcome(3))
        assert mod.append_outcome(rel, store.relative_to(ROOT))["appended"] is True
        duplicate = mod.append_outcome(rel, store.relative_to(ROOT))
        assert duplicate["appended"] is False
        assert "duplicate_outcome_id" in {item["code"] for item in duplicate["errors"]}

        store.write_text(
            json.dumps(shadow_outcome(3), separators=(",", ":")) + "\n" + json.dumps(shadow_outcome(3), separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        validation = mod.validate_store(store.relative_to(ROOT))
        assert validation["valid"] is False
        assert any(item["code"] == "duplicate_outcome_id" and item["line"] == 2 for item in validation["errors"])
    finally:
        store.unlink(missing_ok=True)
        outcome_path.unlink(missing_ok=True)


def test_concurrent_append_duplicate_race_writes_exactly_one_record() -> None:
    store = ROOT / "data" / "test_project_memory_concurrent.jsonl"
    outcome_path = ROOT / "data" / "test_project_memory_concurrent.json"
    store.unlink(missing_ok=True)
    try:
        outcome_rel = write_json(outcome_path, shadow_outcome(30))
        store_rel = str(store.relative_to(ROOT))
        cmd = [sys.executable, str(SCRIPT), "--append", "--outcome", outcome_rel, "--store", store_rel, "--json"]
        first = subprocess.Popen(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out1, err1 = first.communicate(timeout=10)
        out2, err2 = second.communicate(timeout=10)
        assert err1 == ""
        assert err2 == ""
        payloads = [json.loads(out1), json.loads(out2)]
        assert sorted(payload.get("appended") for payload in payloads) == [False, True]
        denied = [payload for payload in payloads if not payload.get("appended")][0]
        assert "duplicate_outcome_id" in {item["code"] for item in denied["errors"]}
        rows = [json.loads(line) for line in store.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1
        assert rows[0]["outcome_id"] == "shadow:30"
        validate = subprocess.run([sys.executable, str(SCRIPT), "--validate", "--store", store_rel, "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
        assert validate.returncode == 0
        assert json.loads(validate.stdout)["record_count"] == 1
    finally:
        store.unlink(missing_ok=True)
        outcome_path.unlink(missing_ok=True)


def test_cli_validate_append_hints_and_no_overwrite_command() -> None:
    store = ROOT / "data" / "test_project_memory_cli.jsonl"
    outcome_path = ROOT / "data" / "test_project_memory_cli_outcome.json"
    store.unlink(missing_ok=True)
    try:
        outcome_rel = write_json(outcome_path, base_outcome(8))
        store_rel = str(store.relative_to(ROOT))
        appended = subprocess.run([sys.executable, str(SCRIPT), "--append", "--outcome", outcome_rel, "--store", store_rel, "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
        assert appended.returncode == 0
        assert json.loads(appended.stdout)["appended"] is True

        hints = subprocess.run([sys.executable, str(SCRIPT), "--hints", "--project-id", "nmbot", "--policy-version", "nmbot-passive-v1", "--route", "docs", "--evidence-type", "docs", "--store", store_rel, "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
        payload = json.loads(hints.stdout)
        assert hints.returncode == 0
        assert payload["denied_reason"] == "hints_disabled_by_policy"
        assert payload["hints"] == []
        assert payload["matching_safe_outcome_count"] == 1

        summary = subprocess.run([sys.executable, str(SCRIPT), "--summary", "--store", store_rel, "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
        assert summary.returncode == 0
        summary_payload = json.loads(summary.stdout)
        assert summary_payload["ok"] is True
        assert summary_payload["counts"]["phase"] == {"legacy_v1": 1}

        help_run = subprocess.run([sys.executable, str(SCRIPT), "--help"], cwd=ROOT, text=True, capture_output=True, check=False)
        assert "overwrite" not in help_run.stdout.lower()
        assert "delete" not in help_run.stdout.lower()
    finally:
        store.unlink(missing_ok=True)
        outcome_path.unlink(missing_ok=True)


def test_malformed_jsonl_fails_closed_without_raw_line_content() -> None:
    store = ROOT / "data" / "test_project_memory_bad.jsonl"
    store.write_text('{"raw":"do-not-echo"\n', encoding="utf-8")
    try:
        run = subprocess.run([sys.executable, str(SCRIPT), "--validate", "--store", str(store.relative_to(ROOT)), "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
        assert run.returncode == 2
        payload = json.loads(run.stdout)
        assert payload["errors"][0]["line"] == 1
        assert "do-not-echo" not in run.stdout
        assert "raw" not in run.stdout
    finally:
        store.unlink(missing_ok=True)


def test_corrupt_mixed_v1_v2_store_fails_closed_with_line_only() -> None:
    store = ROOT / "data" / "test_project_memory_mixed_bad.jsonl"
    store.write_text(
        json.dumps(base_outcome(1), separators=(",", ":")) + "\n" +
        json.dumps(shadow_outcome(2), separators=(",", ":")) + "\n" +
        '{"schema":"privacy_safe_shadow_outcome.v1","raw_query":"do-not-echo"}\n',
        encoding="utf-8",
    )
    try:
        run = subprocess.run([sys.executable, str(SCRIPT), "--validate", "--store", str(store.relative_to(ROOT)), "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
        assert run.returncode == 2
        payload = json.loads(run.stdout)
        assert any(item["line"] == 3 for item in payload["errors"])
        assert "do-not-echo" not in run.stdout
        assert "raw_query" not in run.stdout
    finally:
        store.unlink(missing_ok=True)


def test_source_has_no_banned_imports_or_runtime_dependencies() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    banned = [
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import urllib",
        "from urllib",
        "import socket",
        "from socket",
        "import notebooklm",
        "from notebooklm",
        "import mempalace",
        "from mempalace",
        "import nmbot_context_gate",
        "from nmbot_context_gate",
        "import nmbot_runtime",
        "from nmbot_runtime",
    ]
    assert not any(token in source for token in banned)
