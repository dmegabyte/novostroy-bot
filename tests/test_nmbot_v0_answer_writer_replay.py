from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import pytest

from scripts import nmbot_v0_answer_writer_replay as replay


IDEAL_RUBRIC = replay.REPO / "data" / "v0_answer_writer_replay" / "ideal_dialogues.v1.jsonl"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_fixture_count_schema_and_evidence_traceability() -> None:
    cases = replay.validate_cases()
    assert len(cases) == replay.EXPECTED_FIXTURE_COUNT == 10
    defect_types = {case["metadata"]["defect_type"] for case in cases}
    assert defect_types == {
        "missing_cards_with_three_facts",
        "single_card_instead_of_three_facts",
        "empty_evidence_invented_project_price_operator",
        "stiff_tone_unsupported_benefit_no_final_question",
        "rental_dry_selling_single_card_count3",
        "family_traceable_benefits_two_cards",
        "rental_three_cards_unsupported_demand_claim",
        "single_exact_card_budget_retention",
        "empty_spb_operator_phone_cta",
        "investment_mortgage_missing_program_boundary",
    }
    assert all(case["metadata"]["synthetic"] is False for case in cases)
    for case in cases:
        assert case["source"]["path"] in {"logs/dialogs-2026-06-25.jsonl", "logs/dialogs-2026-07-05.jsonl"}
        assert case["client_message"] == case["user_text"]
        assert case["old_response_text"]
        raw = case["raw_search_response"]
        for name in case["expectations"].get("required_card_names", []):
            assert name in raw
        material = case["assignment"]["material"]
        for line in material["card_lines"]:
            assert replay._contains_name_alias(line, replay.evidence_names(case))


def test_ideal_dialogue_rubric_covers_fixture_and_is_human_only() -> None:
    cases = replay.validate_cases()
    rubric = _rows(IDEAL_RUBRIC)
    assert len(rubric) == len(cases) == replay.EXPECTED_FIXTURE_COUNT
    by_case = {case["case_id"]: case for case in cases}
    assert {row["case_id"] for row in rubric} == set(by_case)

    expected_dimensions = {
        "grounding",
        "retention",
        "honesty",
        "helpfulness_consultant_tone",
        "one_step_cta",
    }
    for row in rubric:
        case = by_case[row["case_id"]]
        assert row["human_benchmark_only_not_model_input"] is True
        assert row["source_ref"] == f"{case['source']['path']}:{case['source']['line']}#{case['source']['id']}@{case['source']['timestamp']}"
        assert row["proven_input_facts_boundary"]["raw_search_response_only"] is True
        assert row["proven_input_facts_boundary"]["no_external_mcp_refresh"] is True
        assert set(row["quality_scoring_dimensions"]) == expected_dimensions
        assert row["ideal_answer_ru_plain_text"].strip()
        assert row["expected_single_next_step"] == case["assignment"]["material"]["final_question"]
        for forbidden in row["forbidden_claims"]:
            assert forbidden not in row["ideal_answer_ru_plain_text"]


def test_dry_run_does_not_call_provider(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("candidate prompt", encoding="utf-8")
    results = tmp_path / "results.jsonl"
    report = tmp_path / "report.md"

    async def forbidden_provider(payload: dict, timeout: int):  # pragma: no cover - must not run
        raise AssertionError("provider called in dry-run")

    args = argparse.Namespace(
        model=replay.MODEL,
        fixture=replay.FIXTURE,
        baseline_prompt=replay.BASELINE_PROMPT,
        candidate_prompt=candidate,
        timeout=1,
        results=results,
        report=report,
        dry_run=True,
    )
    assert asyncio.run(replay.replay(args, provider=forbidden_provider)) == 0
    rows = _rows(results)
    assert len(rows) == replay.EXPECTED_FIXTURE_COUNT * 2
    assert all(row["dry_run"] for row in rows)
    assert all(row["meta"]["note"].startswith("model_not_called") for row in rows)
    assert "model_not_called: `true`" in report.read_text(encoding="utf-8")


def test_case_id_dry_run_selects_one_case_after_fixture_validation(tmp_path: Path) -> None:
    cases = replay.validate_cases()
    selected = cases[1]
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("candidate prompt", encoding="utf-8")
    results = tmp_path / "results.jsonl"
    report = tmp_path / "report.md"

    async def forbidden_provider(payload: dict, timeout: int):  # pragma: no cover - must not run
        raise AssertionError("provider called in dry-run")

    args = argparse.Namespace(
        model=replay.MODEL,
        fixture=replay.FIXTURE,
        baseline_prompt=replay.BASELINE_PROMPT,
        candidate_prompt=candidate,
        timeout=1,
        parallelism=1,
        case_id=selected["case_id"],
        results=results,
        report=report,
        dry_run=True,
    )

    assert asyncio.run(replay.replay(args, provider=forbidden_provider)) == 0
    rows = _rows(results)
    assert [(row["case_id"], row["variant"]) for row in rows] == [
        (selected["case_id"], "baseline"),
        (selected["case_id"], "candidate"),
    ]
    assert all(row["dry_run"] for row in rows)
    assert f"## {selected['case_id']}" in report.read_text(encoding="utf-8")


def test_case_id_unknown_returns_2_before_provider_or_output_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    calls = 0

    async def provider(payload: dict, timeout: int):  # pragma: no cover - must not run
        nonlocal calls
        calls += 1
        return "answer", {"ok": True}

    args = argparse.Namespace(
        model=replay.MODEL,
        fixture=replay.FIXTURE,
        baseline_prompt=tmp_path / "missing-baseline.txt",
        candidate_prompt=tmp_path / "missing-candidate.txt",
        timeout=1,
        parallelism=1,
        case_id="missing-case-id",
        results=tmp_path / "results.jsonl",
        report=tmp_path / "report.md",
        dry_run=False,
    )

    assert asyncio.run(replay.replay(args, provider=provider)) == 2
    captured = capsys.readouterr()
    assert "unknown case_id: missing-case-id" in captured.err
    assert calls == 0
    assert not args.results.exists()
    assert not args.report.exists()


def test_hard_model_pin(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("candidate prompt", encoding="utf-8")
    args = argparse.Namespace(
        model="google/gemini-3.5-flash",
        fixture=replay.FIXTURE,
        baseline_prompt=replay.BASELINE_PROMPT,
        candidate_prompt=candidate,
        timeout=1,
        results=tmp_path / "results.jsonl",
        report=tmp_path / "report.md",
        dry_run=True,
    )
    assert asyncio.run(replay.replay(args)) == 2


def test_parallel_dry_run_preserves_fixture_and_variant_order(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("candidate prompt", encoding="utf-8")
    args = argparse.Namespace(
        model=replay.MODEL,
        fixture=replay.FIXTURE,
        baseline_prompt=replay.BASELINE_PROMPT,
        candidate_prompt=candidate,
        timeout=1,
        parallelism=10,
        results=tmp_path / "results.jsonl",
        report=tmp_path / "report.md",
        dry_run=True,
    )

    assert asyncio.run(replay.replay(args)) == 0
    rows = _rows(args.results)
    expected = [(case["case_id"], variant) for case in replay.validate_cases() for variant in ("baseline", "candidate")]
    assert [(row["case_id"], row["variant"]) for row in rows] == expected
    assert all(row["dry_run"] for row in rows)
    assert all(row["model"] == replay.MODEL for row in rows)


def test_case_id_parallel_run_calls_baseline_then_candidate_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cases = replay.validate_cases()
    selected = cases[3]
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("candidate prompt", encoding="utf-8")
    calls = 0

    async def provider(payload: dict, timeout: int):
        nonlocal calls
        idx = calls
        calls += 1
        await asyncio.sleep(0)
        return f"answer-{idx}", {"ok": True, "idx": idx}

    monkeypatch.setattr(replay, "deterministic_checks", lambda case, output: {"ok": True, "checks": {}})
    args = argparse.Namespace(
        model=replay.MODEL,
        fixture=replay.FIXTURE,
        baseline_prompt=replay.BASELINE_PROMPT,
        candidate_prompt=candidate,
        timeout=1,
        parallelism=2,
        case_id=selected["case_id"],
        results=tmp_path / "results.jsonl",
        report=tmp_path / "report.md",
        dry_run=False,
    )

    assert asyncio.run(replay.replay(args, provider=provider)) == 0
    rows = _rows(args.results)
    assert [(row["case_id"], row["variant"]) for row in rows] == [
        (selected["case_id"], "baseline"),
        (selected["case_id"], "candidate"),
    ]
    assert [row["output"] for row in rows] == ["answer-0", "answer-1"]
    assert calls == 2


def test_parallel_run_aggregates_successes_in_deterministic_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("candidate prompt", encoding="utf-8")
    starts = 0
    active = 0
    max_active = 0

    async def provider(payload: dict, timeout: int):
        nonlocal starts, active, max_active
        idx = starts
        starts += 1
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.001 * (20 - idx))
        active -= 1
        return f"answer-{idx}", {"ok": True, "idx": idx}

    monkeypatch.setattr(replay, "deterministic_checks", lambda case, output: {"ok": True, "checks": {}})
    args = argparse.Namespace(
        model=replay.MODEL,
        fixture=replay.FIXTURE,
        baseline_prompt=replay.BASELINE_PROMPT,
        candidate_prompt=candidate,
        timeout=1,
        parallelism=5,
        results=tmp_path / "results.jsonl",
        report=tmp_path / "report.md",
        dry_run=False,
    )

    assert asyncio.run(replay.replay(args, provider=provider)) == 0
    rows = _rows(args.results)
    expected = [(case["case_id"], variant) for case in replay.validate_cases() for variant in ("baseline", "candidate")]
    assert [(row["case_id"], row["variant"]) for row in rows] == expected
    assert [row["output"] for row in rows] == [f"answer-{idx}" for idx in range(len(rows))]
    assert starts == len(rows) == replay.EXPECTED_FIXTURE_COUNT * 2
    assert max_active > 1


def test_parallel_run_collects_all_rows_before_error_return(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("candidate prompt", encoding="utf-8")
    calls = 0

    async def failing_provider(payload: dict, timeout: int):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return "", {"ok": False, "error_code": "stub_failure"}

    args = argparse.Namespace(
        model=replay.MODEL,
        fixture=replay.FIXTURE,
        baseline_prompt=replay.BASELINE_PROMPT,
        candidate_prompt=candidate,
        timeout=1,
        parallelism=4,
        results=tmp_path / "results.jsonl",
        report=tmp_path / "report.md",
        dry_run=False,
    )

    assert asyncio.run(replay.replay(args, provider=failing_provider)) == 1
    rows = _rows(args.results)
    assert calls == replay.EXPECTED_FIXTURE_COUNT * 2
    assert len(rows) == replay.EXPECTED_FIXTURE_COUNT * 2
    assert all(row["error"] == "empty_response" for row in rows)


def test_first_failure_stops_run(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.txt"
    candidate.write_text("candidate prompt", encoding="utf-8")
    calls = 0

    async def failing_provider(payload: dict, timeout: int):
        nonlocal calls
        calls += 1
        return "", {"ok": False, "error_code": "stub_failure"}

    args = argparse.Namespace(
        model=replay.MODEL,
        fixture=replay.FIXTURE,
        baseline_prompt=replay.BASELINE_PROMPT,
        candidate_prompt=candidate,
        timeout=1,
        results=tmp_path / "results.jsonl",
        report=tmp_path / "report.md",
        dry_run=False,
    )
    assert asyncio.run(replay.replay(args, provider=failing_provider)) == 1
    assert calls == 1
    rows = _rows(args.results)
    assert len(rows) == 1
    assert rows[0]["error"] == "empty_response"


def test_deterministic_factual_checks_catch_inventions() -> None:
    case = replay.validate_cases()[2]
    bad = "В МО есть студии, например ЖК «Горки Парк» от 4.9 млн. Передать оператору?"
    result = replay.deterministic_checks(case, bad)
    assert result["ok"] is False
    checks = result["checks"]
    assert checks["no_unknown_quoted_names"] is False
    assert checks["no_prices_absent_from_evidence"] is False
    assert checks["no_option_claim_when_no_evidence"] is False
    assert checks["operator_cta_constraints"] is False


def test_internal_route_term_near_variant_fails() -> None:
    case = next(case for case in replay.validate_cases() if case["metadata"]["defect_type"] == "stiff_tone_unsupported_benefit_no_final_question")
    output = "Есть near-вариант: ЖК «Лучи» в Солнцево, 4 км от МКАД, от 10.58 млн руб. Какая площадь важна?"
    result = replay.deterministic_checks(case, output)
    assert result["ok"] is False
    assert result["checks"]["no_internal_terms"] is False


def test_natural_wording_without_internal_terms_passes() -> None:
    case = next(case for case in replay.validate_cases() if case["metadata"]["defect_type"] == "stiff_tone_unsupported_benefit_no_final_question")
    output = "Есть ЖК «Лучи» в Солнцево, 4 км от МКАД, от 10.58 млн руб. Какая площадь важна?"
    result = replay.deterministic_checks(case, output)
    assert result["ok"] is True
    assert result["checks"]["no_internal_terms"] is True


def test_client_budget_can_be_repeated_but_not_replaced_with_new_price() -> None:
    case = replay.validate_cases()[2]
    honest = "Студий в Санкт-Петербурге до 5 млн сейчас нет. Рассмотреть другие регионы?"
    invented = "Студий в Санкт-Петербурге до 4.9 млн сейчас нет. Рассмотреть другие регионы?"
    assert replay.deterministic_checks(case, honest)["checks"]["no_prices_absent_from_evidence"] is True
    assert replay.deterministic_checks(case, invented)["checks"]["no_prices_absent_from_evidence"] is False


def test_aliases_pass_for_known_project_names() -> None:
    cases = replay.validate_cases()
    luchi_case = next(case for case in cases if case["metadata"]["defect_type"] == "stiff_tone_unsupported_benefit_no_final_question")
    assert replay.deterministic_checks(luchi_case, "Есть ЖК «Лучи» в Солнцево, 4 км от МКАД, от 10.58 млн руб. Какая площадь важна?")["ok"] is True
    assert replay.deterministic_checks(luchi_case, "Есть «Лучи» в Солнцево, 4 км от МКАД, от 10.58 млн руб. Какая площадь важна?")["ok"] is True


def test_required_any_substrings_accepts_grammatical_alternatives() -> None:
    case = next(case for case in replay.validate_cases() if case["case_id"].startswith("case-0008"))
    for location_phrase in ("в Москве", "в районах Москвы"):
        output = (
            f"Нашла варианты {location_phrase}: ЖК «Лучи» от 10 578 848 руб., "
            "ЖК «Южные Сады» от 11 399 922 руб. и Бусиновский парк от 11 782 394 руб. "
            "Какой ЖК хотите рассмотреть подробнее?"
        )
        result = replay.deterministic_checks(case, output)
        assert result["ok"] is True
        assert result["checks"]["required_any_substrings"] is True


def test_required_any_substrings_fails_without_listed_alternative() -> None:
    case = next(case for case in replay.validate_cases() if case["case_id"].startswith("case-0008"))
    case = json.loads(json.dumps(case, ensure_ascii=False))
    case["expectations"]["required_any_substrings"] = [["Москва", "Москве", "Москвы"]]
    output = (
        "Нашла варианты: ЖК «Лучи» от 10 578 848 руб., "
        "ЖК «Южные Сады» от 11 399 922 руб. и Бусиновский парк от 11 782 394 руб. "
        "Какой ЖК хотите рассмотреть подробнее?"
    )
    result = replay.deterministic_checks(case, output)
    assert result["ok"] is False
    assert result["checks"]["required_any_substrings"] is False


def test_required_substrings_remains_literal() -> None:
    case = next(case for case in replay.validate_cases() if case["case_id"].startswith("case-0008"))
    case = json.loads(json.dumps(case, ensure_ascii=False))
    case["expectations"]["required_substrings"] = ["Москва"]
    case["expectations"].pop("required_any_substrings", None)
    output = (
        "Нашла варианты в Москве: ЖК «Лучи» от 10 578 848 руб., "
        "ЖК «Южные Сады» от 11 399 922 руб. и Бусиновский парк от 11 782 394 руб. "
        "Какой ЖК хотите рассмотреть подробнее?"
    )
    result = replay.deterministic_checks(case, output)
    assert result["ok"] is False
    assert result["checks"]["required_substrings"] is False


def test_case_0018_keeps_literal_geographic_expectations() -> None:
    case = replay.validate_cases()[2]
    assert case["expectations"]["required_substrings"] == [
        "Санкт-Петербург",
        "Москва",
        "Московская область",
    ]
    assert "required_any_substrings" not in case["expectations"]


def test_unknown_quoted_project_fails() -> None:
    case = next(case for case in replay.validate_cases() if case["metadata"]["defect_type"] == "stiff_tone_unsupported_benefit_no_final_question")
    result = replay.deterministic_checks(case, "Есть ЖК «Горки Парк» в Солнцево. Какая площадь важна?")
    assert result["ok"] is False
    assert result["checks"]["no_unknown_quoted_names"] is False


def test_no_evidence_honest_other_options_question_passes() -> None:
    case = next(case for case in replay.validate_cases() if case["metadata"]["defect_type"] == "empty_evidence_invented_project_price_operator")
    result = replay.deterministic_checks(
        case,
        "По Санкт-Петербург объектов нет. Доступны регионы: Москва, Новая Москва и Московская область. Рассмотреть другие варианты?",
    )
    assert result["ok"] is True
    assert result["checks"]["no_option_claim_when_no_evidence"] is True


def test_no_evidence_false_picked_options_fails() -> None:
    case = next(case for case in replay.validate_cases() if case["metadata"]["defect_type"] == "empty_evidence_invented_project_price_operator")
    result = replay.deterministic_checks(case, "По Санкт-Петербургу объектов нет, но я подобрала варианты в Москве. Рассмотреть?")
    assert result["ok"] is False
    assert result["checks"]["no_option_claim_when_no_evidence"] is False


def test_fixture_material_invented_price_validation_fails(tmp_path: Path) -> None:
    cases = replay.validate_cases()
    bad_cases = json.loads(json.dumps(cases, ensure_ascii=False))
    bad_cases[0]["assignment"]["material"]["card_lines"][0] = "ЖК «Лучи» — Солнцево, от 999 млн руб."
    bad_fixture = tmp_path / "bad.jsonl"
    bad_fixture.write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in bad_cases) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="price absent from raw evidence"):
        replay.validate_cases(bad_fixture)


def test_report_shape(tmp_path: Path) -> None:
    rows = [
        {
            "case_id": "case-x",
            "variant": "baseline",
            "duration_ms": 12,
            "checks": {"ok": True},
            "output": "baseline answer",
            "old_response": "old answer",
            "source_ref": "data/response_eval/cases.jsonl:1#H@test",
        },
        {
            "case_id": "case-x",
            "variant": "candidate",
            "duration_ms": 13,
            "checks": {"ok": False},
            "output": "candidate answer",
            "old_response": "old answer",
            "source_ref": "data/response_eval/cases.jsonl:1#H@test",
        },
    ]
    report = tmp_path / "report.md"
    replay.write_report(report, rows, dry_run=False)
    text = report.read_text(encoding="utf-8")
    assert "# V0 Answer Writer replay report" in text
    assert "| old |" in text
    assert "| baseline | 12 | True | baseline answer |" in text
    assert "| candidate | 13 | False | candidate answer |" in text
