from __future__ import annotations

import argparse
import json
import subprocess
import threading
from pathlib import Path

from scripts import nmbot_v0_deepseek_proxy_replay as replay


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _args(
    tmp_path: Path,
    *,
    dry_run: bool = True,
    model: str = replay.MODEL,
    agent: str = replay.AGENT_NAME,
    parallelism: int = 1,
    case_id: str | None = None,
) -> argparse.Namespace:
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("candidate prompt", encoding="utf-8")
    return argparse.Namespace(
        model=model,
        agent=agent,
        fixture=replay.FIXTURE,
        candidate_prompt=candidate,
        timeout=1,
        results=tmp_path / "results.jsonl",
        report=tmp_path / "report.md",
        dry_run=dry_run,
        parallelism=parallelism,
        case_id=case_id,
    )


def test_model_and_agent_are_hard_pinned(tmp_path: Path) -> None:
    assert replay.replay(_args(tmp_path, model="openrouter/deepseek")) == 2
    assert replay.replay(_args(tmp_path, agent="other-agent")) == 2


def test_dry_run_writes_ten_metadata_rows_and_never_calls_subprocess(tmp_path: Path) -> None:
    def forbidden_runner(command, timeout):  # pragma: no cover - must not run
        raise AssertionError("subprocess called")

    args = _args(tmp_path, dry_run=True)
    assert replay.replay(args, runner=forbidden_runner) == 0
    rows = _rows(args.results)
    assert len(rows) == replay.EXPECTED_FIXTURE_COUNT == 10
    assert all(row["dry_run"] is True for row in rows)
    assert all(row["agent_name"] == replay.AGENT_NAME for row in rows)
    assert all(row["model"] == replay.MODEL for row in rows)
    assert all(row["meta"]["message_has_valeria_prompt_block"] for row in rows)
    assert all(row["meta"]["message_has_v0_assignment_block"] for row in rows)
    assert "model_not_called: `true`" in args.report.read_text(encoding="utf-8")


def test_case_id_dry_run_selects_exactly_one_existing_case_after_validation(tmp_path: Path) -> None:
    target_case_id = "dialog-2026-07-05-line5-rental-three-facts-near-demand-boundary"

    def forbidden_runner(command, timeout):  # pragma: no cover - must not run
        raise AssertionError("subprocess called")

    args = _args(tmp_path, dry_run=True, case_id=target_case_id)
    assert replay.replay(args, runner=forbidden_runner) == 0
    rows = _rows(args.results)
    assert [row["case_id"] for row in rows] == [target_case_id]
    assert rows[0]["dry_run"] is True
    assert "## dialog-2026-07-05-line5-rental-three-facts-near-demand-boundary" in args.report.read_text(encoding="utf-8")


def test_unknown_case_id_returns_clean_error_without_subprocess(tmp_path: Path, capsys) -> None:
    def forbidden_runner(command, timeout):  # pragma: no cover - must not run
        raise AssertionError("subprocess called")

    args = _args(tmp_path, dry_run=False, case_id="missing-case")
    assert replay.replay(args, runner=forbidden_runner) == 2
    captured = capsys.readouterr()
    assert "unknown case_id: missing-case" in captured.err
    assert not args.results.exists()
    assert not args.report.exists()


def test_opencode_command_uses_named_agent_and_default_format(tmp_path: Path) -> None:
    seen = []

    def runner(command, timeout):
        seen.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

    args = _args(tmp_path, dry_run=False)
    assert replay.replay(args, runner=runner) == 1
    assert len(seen) == 1
    command = seen[0]
    assert command[:5] == ["opencode", "run", "--agent", replay.AGENT_NAME, "--format"]
    assert command[5] == "default"
    assert "VALERIA_PROMPT:" in command[6]
    assert "V0_ASSIGNMENT:" in command[6]


def test_first_failure_stops_and_writes_partial_outputs(tmp_path: Path) -> None:
    calls = 0

    def runner(command, timeout):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0, stdout="deepseek-valeria-simulator\n", stderr="")

    args = _args(tmp_path, dry_run=False)
    assert replay.replay(args, runner=runner) == 1
    assert calls == 1
    rows = _rows(args.results)
    assert len(rows) == 1
    assert rows[0]["error"] == "empty_extracted_answer"
    assert args.report.exists()


def test_parallel_run_keeps_order_and_aggregates_failures(tmp_path: Path, monkeypatch) -> None:
    lock = threading.Lock()
    calls = 0

    def runner(command, timeout):
        nonlocal calls
        with lock:
            calls += 1
            call_no = calls
        if call_no == 1:
            return subprocess.CompletedProcess(command, 7, stdout="parallel answer", stderr="boom")
        return subprocess.CompletedProcess(command, 0, stdout="parallel answer", stderr="")

    monkeypatch.setattr(
        replay.base,
        "deterministic_checks",
        lambda case, answer: {"ok": True, "checks": {"mock": True}, "response_chars": len(answer)},
    )

    args = _args(tmp_path, dry_run=False, parallelism=10)
    assert replay.replay(args, runner=runner) == 1
    assert calls == replay.EXPECTED_FIXTURE_COUNT == 10
    rows = _rows(args.results)
    expected_case_ids = [case["case_id"] for case in replay.base.validate_cases(replay.FIXTURE)]
    assert [row["case_id"] for row in rows] == expected_case_ids
    assert sum(bool(row["error"]) for row in rows) == 1
    assert any(row["error"] == "opencode_returncode_7" for row in rows)
    assert args.report.exists()


def test_case_id_parallel_run_invokes_only_selected_case(tmp_path: Path, monkeypatch) -> None:
    target_case_id = "dialog-2026-07-05-line5-rental-three-facts-near-demand-boundary"
    calls = 0

    def runner(command, timeout):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0, stdout="selected answer", stderr="")

    monkeypatch.setattr(
        replay.base,
        "deterministic_checks",
        lambda case, answer: {"ok": True, "checks": {"mock": True}, "response_chars": len(answer)},
    )

    args = _args(tmp_path, dry_run=False, parallelism=10, case_id=target_case_id)
    assert replay.replay(args, runner=runner) == 0
    assert calls == 1
    rows = _rows(args.results)
    assert [row["case_id"] for row in rows] == [target_case_id]
    assert rows[0]["extracted_output"] == "selected answer"


def test_extract_answer_strips_ansi_and_single_opencode_banner() -> None:
    raw = "\x1b[32mdeepseek-valeria-simulator\x1b[0m\nЗдравствуйте!\nВот ответ клиенту.\n"
    assert replay.extract_answer(raw) == "Здравствуйте!\nВот ответ клиенту."


def test_extract_answer_deduplicates_stream_and_final_copy() -> None:
    raw = (
        "> deepseek-valeria-simulator · deepseek-v4-flash-free\n"
        "Есть вариант. Какой бюджет важен?\n"
        "Есть вариант. Какой бюджет важен?\n"
    )
    assert replay.extract_answer(raw) == "Есть вариант. Какой бюджет важен?"


def test_pass_checks_are_recorded(tmp_path: Path) -> None:
    answer = (
        "Нашла варианты в Москве: ЖК «Лучи» от 10 578 848 руб., "
        "ЖК «Южные Сады» от 11 399 922 руб. и Бусиновский парк от 11 782 394 руб. "
        "Какой ЖК хотите рассмотреть подробнее?"
    )

    def runner(command, timeout):
        return subprocess.CompletedProcess(command, 0, stdout=f"agent identity\n{answer}", stderr="")

    args = _args(tmp_path, dry_run=False)
    assert replay.replay(args, runner=runner) == 1
    rows = _rows(args.results)
    assert rows[0]["deterministic_checks"]["ok"] is True
    assert rows[0]["extracted_output"] == answer
    assert rows[1]["error"] == "deterministic_validation_failed"
