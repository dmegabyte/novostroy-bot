from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import nmbot_v1_quality_gate as gate


ROOT = Path(__file__).resolve().parents[1]


def test_fixture_validates_and_covers_all_stage_c_classes():
    fixture, digest = gate.load_fixture()
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert len(fixture["records"]) == 15
    classes = {item for record in fixture["records"] for item in record["classes"]}
    assert gate.REQUIRED_CLASSES <= classes


def test_fixture_validation_fails_closed_for_duplicate_unknown_and_malformed_expectation():
    fixture, _ = gate.load_fixture()

    duplicate = copy.deepcopy(fixture)
    duplicate["records"][1]["id"] = duplicate["records"][0]["id"]
    with pytest.raises(gate.FixtureValidationError, match="duplicate"):
        gate.validate_fixture(duplicate)

    unknown = copy.deepcopy(fixture)
    unknown["records"][0]["turns"][0]["surprise"] = True
    with pytest.raises(gate.FixtureValidationError, match="unknown keys"):
        gate.validate_fixture(unknown)

    malformed = copy.deepcopy(fixture)
    del malformed["records"][0]["turns"][0]["expect"]["stage"]
    with pytest.raises(gate.FixtureValidationError, match="requires stage/action/answer_kind"):
        gate.validate_fixture(malformed)

    missing_goal = copy.deepcopy(fixture)
    del missing_goal["records"][0]["turns"][0]["plan"]["goal"]
    with pytest.raises(gate.FixtureValidationError, match="plan.goal required"):
        gate.validate_fixture(missing_goal)

    malformed_card = copy.deepcopy(fixture)
    malformed_card["records"][0]["turns"][0]["search"]["cards"][0]["facts"] = []
    with pytest.raises(gate.FixtureValidationError, match=r"cards\[0\] invalid"):
        gate.validate_fixture(malformed_card)

    wrong_expect_type = copy.deepcopy(fixture)
    wrong_expect_type["records"][0]["turns"][0]["expect"]["question_count"] = True
    with pytest.raises(gate.FixtureValidationError, match="question_count must be non-negative int"):
        gate.validate_fixture(wrong_expect_type)

    malformed_attempts = copy.deepcopy(fixture)
    malformed_attempts["records"][0]["turns"][0]["search"]["attempts"] = None
    with pytest.raises(gate.FixtureValidationError, match="attempts must be list"):
        gate.validate_fixture(malformed_attempts)


def test_all_cases_replay_and_summary_has_fixture_identity():
    code, summary = gate.run_all()
    assert code == 0
    assert summary["status"] == "passed"
    assert summary["fixture"]["path"] == "tests/fixtures/nmbot_v1_quality_scenarios.json"
    assert re.fullmatch(r"[0-9a-f]{64}", summary["fixture"]["sha256"])
    assert summary["total_cases"] == 15
    assert summary["passed_cases"] == 15


def test_cli_safe_summary_and_nonzero_first_failure(tmp_path):
    ok = subprocess.run(
        [sys.executable, "scripts/nmbot_v1_quality_gate.py", "--all"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert ok.returncode == 0
    summary = json.loads(ok.stdout)
    assert summary["status"] == "passed"
    unsafe_blob = ok.stdout + ok.stderr
    for forbidden in ("provider failed", "Мой номер", "70000000001", "Игнорируй", "служебные инструкции"):
        assert forbidden not in unsafe_blob

    fixture, _ = gate.load_fixture()
    broken = copy.deepcopy(fixture)
    broken["records"][0]["turns"][0]["expect"]["stage"] = "safe_error"
    bad_path = tmp_path / "broken.json"
    bad_path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    code, failed = gate.run_all(path=bad_path)
    assert code == 1
    assert failed["status"] == "failed"
    assert failed["passed_cases"] == 0
    assert failed["failed_case"]["id"] == broken["records"][0]["id"]
    assert "Мой номер" not in json.dumps(failed, ensure_ascii=False)


def test_malformed_fixture_and_main_return_bounded_json_without_traceback(tmp_path, capsys, monkeypatch):
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not-json", encoding="utf-8")
    code, summary = gate.run_all(path=bad_json)
    assert code == 2
    assert summary["status"] == "failed"
    assert summary["fixture"]["sha256"] == ""
    assert summary["failed_case"] == {"id": "fixture", "error": "fixture_json_error"}

    def bad_loader(_path=gate.FIXTURE_PATH):
        raise gate.FixtureValidationError("fixture_json_error")

    monkeypatch.setattr(gate, "load_fixture", bad_loader)
    main_code = gate.main(["--all"])
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert main_code == 2
    assert payload["failed_case"] == {"id": "fixture", "error": "fixture_json_error"}
    assert "Traceback" not in out.err
    assert "not-json" not in out.out

    bad_utf8 = tmp_path / "bad-utf8.json"
    bad_utf8.write_bytes(b"\xff")
    code, summary = gate.run_all(path=bad_utf8)
    assert code == 2
    assert summary["failed_case"] == {"id": "fixture", "error": "fixture_json_error"}


def test_quality_runner_test_and_fixture_do_not_import_v0_or_v2_or_unsafe_provider_markers():
    paths = [
        ROOT / "scripts" / "nmbot_v1_quality_gate.py",
        ROOT / "tests" / "test_nmbot_v1_quality.py",
        ROOT / "tests" / "fixtures" / "nmbot_v1_quality_scenarios.json",
    ]
    blob = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert not re.search(r"^\s*(from|import)\s+nmbot_v[02]\b", blob, re.M)
    assert not re.search(r"^\s*from\s+nmbot_v[02]\b", blob, re.M)
    blocked_markers = ["api" + "_" + "key", "bear" + "er", "pass" + "word", "raw" + "_" + "payload"]
    for marker in blocked_markers:
        assert marker not in blob.lower()
