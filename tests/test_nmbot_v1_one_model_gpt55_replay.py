from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import scripts.nmbot_v1_one_model_gpt55_replay as replay


class FakeGateway:
    def __init__(self, responses: list[str] | None = None, *, explode: bool = False) -> None:
        self.responses = list(responses or [])
        self.explode = explode
        self.calls: list[tuple[dict[str, Any], dict[str, Any], int]] = []

    async def _run_gateway_request(self, request_data, headers, timeout):
        self.calls.append((request_data, headers, timeout))
        if self.explode:
            raise RuntimeError("raw provider secret should not leak")
        return self.responses.pop(0), {"raw": "raw provider secret"}


def _json(response: str, visible=None, next_action: str = "none") -> str:
    return json.dumps({"response": response, "visible_options": visible or [], "next_action": next_action}, ensure_ascii=False)


def test_prompt_markers_hash_model_pin_and_no_mcp_payload() -> None:
    cases = replay.load_cases()
    payload = replay.request_payload(cases[0])
    identity = replay.prompt_identity()

    assert identity["source"] == replay.PROMPT_SOURCE
    assert len(identity["sha256"]) == 64
    assert replay.prompt_contract()["ok"] is True
    assert payload["model"] == "openai/gpt-5.5"
    assert payload["parameters"]["temperature"] == 0.3
    assert payload["query"].startswith("V1_ONE_MODEL_INPUT=")
    assert "mcp_servers" not in payload
    assert replay.SOURCE_FIXTURE == Path(replay.ROOT / "data/v0_answer_writer_replay/cases.v1.jsonl")


def test_fixture_counts_exact_10_real_plus_one_derived_target() -> None:
    cases = replay.load_cases()
    assert sum(case.corpus == "real_v0_replay" for case in cases) == 10
    targets = [case for case in cases if case.corpus == "derived_target_regression"]
    assert len(targets) == 1
    assert "logs/planner_trace-2026-07-29.jsonl:662" in targets[0].source_refs
    assert "docs/BOT_ARCHITECTURE.md:758-773" in targets[0].source_refs


def test_strict_schema_accepts_only_exact_keys_and_enum() -> None:
    case = replay.load_cases()[0]
    valid = {"response": "Есть ЖК «Лучи». Хотите посмотреть подробнее?", "visible_options": [{"name": "ЖК «Лучи»"}], "next_action": "inspect_option"}
    assert replay.validate_answer(valid, case) == []

    with pytest.raises(ValueError, match="wrong_keys"):
        replay._strict_json_object(json.dumps({**valid, "extra": True}, ensure_ascii=False))
    assert "next_action_invalid" in replay.validate_answer({**valid, "next_action": "call_me"}, case)


def test_grounding_rejects_invented_name_price_and_unsupported_mortgage() -> None:
    case = replay.load_cases()[0]
    bad = {"response": "Есть ЖК «Несуществующий» за 123 млн руб. Семейная ипотека возможна?", "visible_options": [], "next_action": "none"}
    errors = replay.validate_answer(bad, case)
    assert any(err.startswith("quoted_name_not_grounded") for err in errors)
    assert any(err.startswith("money_not_grounded") for err in errors)

    target = replay.load_cases("target-regression-2026-07-29-typo-family-mortgage-boundary")[0]
    mortgage = {"response": "Да, семейная ипотека возможна по ЖК «Лучи». Хотите оформить?", "visible_options": [{"name": "ЖК «Лучи»"}], "next_action": "inspect_option"}
    errors = replay.validate_answer(mortgage, target)
    assert "unsupported_mortgage_confirmation" in errors
    assert "mortgage_followup_repeated_cards_without_terms" in errors


def test_grounding_rejects_unquoted_unknown_project_mentions_fail_closed() -> None:
    case = replay.load_cases()[0]

    invented_title = {"response": "1. Горки Парк — цена по запросу. Показать подробнее?", "visible_options": [], "next_action": "inspect_option"}
    errors = replay.validate_answer(invented_title, case)
    assert any(err.startswith("unknown_project_mention:горки парк") for err in errors)

    invented_prefixed = {"response": "ЖК Несуществующий подойдёт под запрос. Показать подробнее?", "visible_options": [], "next_action": "inspect_option"}
    errors = replay.validate_answer(invented_prefixed, case)
    assert any(err.startswith("unknown_project_mention:несуществующий") for err in errors)
    assert "card_like_output_without_visible_options" not in errors

    geography = {"response": "По Санкт-Петербургу подтверждённых данных в базе нет. Рассмотреть Москву или область?", "visible_options": [], "next_action": "clarify_search"}
    assert replay.validate_answer(geography, case) == []


def test_grounding_requires_project_mentions_to_be_visible_when_cards_presented() -> None:
    case = replay.load_cases()[0]
    bad = {
        "response": "Есть ЖК «Лучи» и ещё рядом ЖК «Огни». Проверить подробнее?",
        "visible_options": [{"name": "ЖК «Лучи»"}],
        "next_action": "inspect_option",
    }
    errors = replay.validate_answer(bad, case)
    assert any(err.startswith("unknown_project_mention") or err.startswith("project_mention_not_visible") for err in errors)


def test_target_regression_no_stale_shortlist_boundary_passes() -> None:
    target = replay.load_cases("target-regression-2026-07-29-typo-family-mortgage-boundary")[0]
    answer = {
        "response": "В текущих данных условия по семейной ипотеке не подтверждены. Оператор может проверить их по выбранным или по всем текущим вариантам — проверить?",
        "visible_options": [],
        "next_action": "offer_operator",
    }
    assert replay.validate_answer(answer, target) == []


def test_grounded_location_title_case_is_not_unknown_project() -> None:
    base = replay.load_cases()[0]
    evidence = json.loads(json.dumps(base.evidence, ensure_ascii=False))
    evidence["facts"][0]["location"] = "Западное Дегунино"
    case = replay.Case("location-regression", dict(base.record), evidence, ["in-memory:test"], "test_only")
    answer = {
        "response": "1. ЖК «Лучи» — Западное Дегунино. Показать подробнее?",
        "visible_options": [{"name": "ЖК «Лучи»"}],
        "next_action": "inspect_option",
    }

    assert replay.validate_answer(answer, case) == []


def test_mortgage_followup_sentence_fragment_is_not_project_name() -> None:
    target = replay.load_cases("target-regression-2026-07-29-typo-family-mortgage-boundary")[0]
    answer = {
        "response": "В текущих данных условия по семейной ипотеке не подтверждены. Оператор может проверить по выбранным вариантам или сразу по всем трем — проверить?",
        "visible_options": [],
        "next_action": "offer_operator",
    }

    assert replay.validate_answer(answer, target) == []


def test_phone_bypass_zero_provider_calls_and_simulated_terminal() -> None:
    base = replay.load_cases()[0]
    record = dict(base.record)
    record["case_id"] = "phone-test"
    record["client_message"] = "+7 999 123-45-67"
    case = replay.Case("phone-test", record, base.evidence, ["in-memory:test"], "test_only")
    gateway = FakeGateway([_json("should not be used")])

    row = asyncio.run(replay.run_case(case, gateway=gateway))

    assert gateway.calls == []
    assert row["provider_called"] is False
    assert row["callback_simulated_only"] is True
    assert row["published"] is False
    assert row["checks"]["status"] == "phone_bypass_simulated"
    assert row["result"]["response"] == "Симуляция: валидный номер перехвачен кодом; callback не создавался."
    assert "свяжется" not in row["result"]["response"].lower()
    assert "номер получила" not in row["result"]["response"].lower()


def test_fallback_on_provider_parse_and_validation_without_raw_output() -> None:
    case = replay.load_cases()[0]
    invalid_json = asyncio.run(replay.run_case(case, gateway=FakeGateway(["not json"])))
    assert invalid_json["published"] is False
    assert invalid_json["checks"]["status"] == "fallback"

    exploding = asyncio.run(replay.run_case(case, gateway=FakeGateway(explode=True)))
    dumped = json.dumps(exploding, ensure_ascii=False)
    assert exploding["published"] is False
    assert "raw provider secret" not in dumped

    target = replay.load_cases("target-regression-2026-07-29-typo-family-mortgage-boundary")[0]
    bad = _json("Да, семейная ипотека возможна по ЖК «Лучи». Проверяем?", [{"name": "ЖК «Лучи»"}], "inspect_option")
    row = asyncio.run(replay.run_case(target, gateway=FakeGateway([bad])))
    assert row["published"] is False
    assert row["result"]["next_action"] == "offer_operator"


def test_dry_run_no_provider_and_validate_counts(tmp_path: Path) -> None:
    code, report = replay.validate_command()
    assert code == 0
    assert report["case_counts"] == {"real": 10, "target_regression": 1, "total": 11}

    exit_code = replay.main(["dry-run", "--parallelism", "10", "--out-dir", str(tmp_path)])
    assert exit_code == 0
    rows = [json.loads(line) for line in (tmp_path / "dry_run_results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 11
    assert all(row["provider_called"] is False for row in rows)
    assert (tmp_path / "dry_run_report.md").exists()


def test_parallelism_11_is_allowed_and_preserves_dry_run_order(tmp_path: Path) -> None:
    exit_code = replay.main(["dry-run", "--parallelism", "11", "--out-dir", str(tmp_path)])
    assert exit_code == 0
    rows = [json.loads(line) for line in (tmp_path / "dry_run_results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 11
    assert [row["case_id"] for row in rows] == [case.case_id for case in replay.load_cases()]


def test_parallel_order_and_aggregate_behavior() -> None:
    cases = replay.load_cases()[:3]
    responses = [
        _json("Есть ЖК «Лучи». Проверить подробнее?", [{"name": "ЖК «Лучи»"}], "inspect_option"),
        "not json",
        _json("По Санкт-Петербургу подтверждённых данных в базе нет. Рассмотреть Москву или область?", [], "clarify_search"),
    ]
    gateway = FakeGateway(responses)

    rows = asyncio.run(replay.run_all(cases, parallelism=1, gateway=gateway))

    assert [row["case_id"] for row in rows] == [case.case_id for case in cases]
    assert len(gateway.calls) == 3
    assert rows[1]["published"] is False
    assert rows[2]["published"] is True


def test_request_payload_contains_saved_evidence_marker_not_mcp_or_raw_secret() -> None:
    case = replay.load_cases()[0]
    payload = replay.request_payload(case)
    query_data = json.loads(payload["query"].removeprefix(replay.QUERY_MARKER))

    assert set(query_data) == {"client_message", "previous_assistant_message", "state_summary", "evidence"}
    assert query_data["evidence"] == case.evidence
    assert "mcp_servers" not in payload
    safe_row = replay.dry_row(case)
    assert "raw_search_response" not in json.dumps(safe_row, ensure_ascii=False)
