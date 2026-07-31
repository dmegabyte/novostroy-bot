from __future__ import annotations

import argparse
import json
import subprocess
import threading
from pathlib import Path

import pytest

from scripts import nmbot_v0_answer_writer_risk as risk


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _args(tmp_path: Path, *, dry_run: bool = True, parallelism: int = 10, model: str = risk.MODEL, agent: str = risk.AGENT_NAME) -> argparse.Namespace:
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("candidate prompt", encoding="utf-8")
    return argparse.Namespace(
        fixture=risk.FIXTURE,
        candidate_prompt=candidate,
        model=model,
        agent=agent,
        timeout=1,
        parallelism=parallelism,
        results=tmp_path / "results.jsonl",
        report=tmp_path / "report.md",
        dry_run=dry_run,
    )


def test_risk_fixture_validation_schema_and_contract_markers() -> None:
    cases = risk.validate_cases()
    assert len(cases) == risk.EXPECTED_FIXTURE_COUNT == 7
    assert [case["case_id"] for case in cases] == [
        "risk-001-greeting-no-cards",
        "risk-002-adversarial-stale-lines-no-cards",
        "risk-003-continuation-shortlist-valid",
        "risk-004-selected-object-one-card-valid",
        "risk-005-empty-search-no-options",
        "risk-006-terminal-callback-operator-phone-skip",
        "risk-007-adversarial-stale-second-card-one-card",
    ]
    assert all(case["synthetic_contract"] is True for case in cases)
    assert sum(case["expectations"].get("writer_must_skip") is True for case in cases) == 1
    assert not any("raw_search_response" in case for case in cases)
    assert not any("assignment" in case or "dialogue" in case for case in cases)
    for case in cases:
        assert {"client_message", "previous_assistant_message", "response_job", "material"} <= set(case)
    assert sum(case["adversarial_malformed_payload"] is True for case in cases) == 2


def test_skip_case_does_not_invoke_subprocess_even_on_run(tmp_path: Path) -> None:
    calls = 0

    def runner(command, timeout):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0, stdout="Короткий ответ без вопроса", stderr="")

    args = _args(tmp_path, dry_run=False, parallelism=10)
    risk.replay(args, runner=runner)
    rows = _rows(args.results)
    skip = next(row for row in rows if row["case_id"] == "risk-006-terminal-callback-operator-phone-skip")
    assert skip["status"] == "writer_must_skip"
    assert skip["error"] == ""
    assert skip["meta"]["opencode_invoked"] is False
    assert calls == risk.EXPECTED_FIXTURE_COUNT - 1


def test_dry_run_parallel_writes_all_rows_without_subprocess(tmp_path: Path) -> None:
    def forbidden_runner(command, timeout):  # pragma: no cover - must not run
        raise AssertionError("subprocess called")

    args = _args(tmp_path, dry_run=True, parallelism=10)
    assert risk.replay(args, runner=forbidden_runner) == 0
    rows = _rows(args.results)
    assert len(rows) == risk.EXPECTED_FIXTURE_COUNT
    assert [row["case_id"] for row in rows] == [case["case_id"] for case in risk.validate_cases()]
    assert all(row["dry_run"] is True for row in rows)
    skip = next(row for row in rows if row["case_id"] == "risk-006-terminal-callback-operator-phone-skip")
    assert skip["status"] == "writer_must_skip"
    assert "model_not_called: `true`" in args.report.read_text(encoding="utf-8")


def test_model_and_agent_are_hard_pinned(tmp_path: Path) -> None:
    assert risk.replay(_args(tmp_path, model="openrouter/deepseek")) == 2
    assert risk.replay(_args(tmp_path, agent="other-agent")) == 2


def test_parallel_aggregation_keeps_order_and_runs_all_eligible(tmp_path: Path) -> None:
    lock = threading.Lock()
    calls = 0

    def runner(command, timeout):
        nonlocal calls
        assert command[:6] == ["opencode", "run", "--model", risk.MODEL, "--agent", risk.AGENT_NAME]
        assert "V0_RISK_ASSIGNMENT:" in command[-1]
        with lock:
            calls += 1
            call_no = calls
        if call_no == 1:
            return subprocess.CompletedProcess(command, 7, stdout="", stderr="boom")
        return subprocess.CompletedProcess(command, 0, stdout="Подскажите бюджет?", stderr="")

    args = _args(tmp_path, dry_run=False, parallelism=10)
    assert risk.replay(args, runner=runner) == 1
    rows = _rows(args.results)
    assert calls == risk.EXPECTED_FIXTURE_COUNT - 1
    assert [row["case_id"] for row in rows] == [case["case_id"] for case in risk.validate_cases()]
    assert sum(bool(row["error"]) for row in rows) >= 1
    assert any(row["error"] == "opencode_returncode_7" for row in rows)
    assert args.report.exists()


def test_stale_card_failure_detection() -> None:
    case = next(case for case in risk.validate_cases() if case["case_id"] == "risk-002-adversarial-stale-lines-no-cards")
    result = risk.deterministic_checks(case, "Привет! Есть ЖК Старая Карточка от 9 млн. Что важно?")
    assert result["ok"] is False
    assert result["checks"]["forbidden_literals_absent"] is False
    assert result["checks"]["at_most_expected_questions"] is True


def test_required_literals_are_exact_not_transformed() -> None:
    case = next(case for case in risk.validate_cases() if case["case_id"] == "risk-004-selected-object-one-card-valid")
    assert risk.deterministic_checks(case, "ЖК Первый. Что уточнить?")["ok"] is True
    assert risk.deterministic_checks(case, "Первый жилой комплекс. Что уточнить?")["checks"]["required_literals_present"] is False


def test_assignment_from_case_uses_exact_production_top_level_fields_and_sanitizes_adversarial_cards() -> None:
    stale_no_cards = next(case for case in risk.validate_cases() if case["case_id"] == "risk-002-adversarial-stale-lines-no-cards")
    one_card = next(case for case in risk.validate_cases() if case["case_id"] == "risk-007-adversarial-stale-second-card-one-card")

    no_cards_assignment = risk.assignment_from_case(stale_no_cards)
    one_card_assignment = risk.assignment_from_case(one_card)

    assert set(no_cards_assignment) == {"client_message", "previous_assistant_message", "response_job", "material"}
    assert no_cards_assignment["material"]["card_lines"] == []
    assert one_card_assignment["material"]["card_lines"] == ["ЖК Первый — корпус 1, от 10 млн руб."]
    assert "ЖК Второй" not in json.dumps(one_card_assignment, ensure_ascii=False)


def test_invalid_fixture_without_synthetic_contract_fails(tmp_path: Path) -> None:
    cases = risk.validate_cases()
    cases[0]["synthetic_contract"] = False
    bad_fixture = tmp_path / "bad.jsonl"
    bad_fixture.write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="synthetic_contract must be true"):
        risk.validate_cases(bad_fixture)
