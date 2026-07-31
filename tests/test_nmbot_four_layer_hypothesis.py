from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_four_layer_hypothesis.py"
sys.path.insert(0, str(ROOT / "scripts"))

import nmbot_four_layer_hypothesis as four  # noqa: E402


def test_no_network_without_live(monkeypatch):
    class BoomSession:
        def __init__(self, *args, **kwargs):
            raise AssertionError("network must not be opened without --live")

    monkeypatch.setattr(four.aiohttp, "ClientSession", BoomSession)
    result = asyncio.run(four.run_scenario("hard_constraints", live=False))
    assert result["mode"] == "dry_run"
    assert result["invariant_checks"]["ok"] is True


def test_fixtures_validate_in_dry_run():
    for name in ("hard_constraints", "unsupported_claim", "no_match"):
        result = asyncio.run(four.run_scenario(name, live=False))
        assert result["stage"] == "four_layer_presenter_hypothesis"
        assert result["model"] == "google/gemini-2.5-flash"
        assert result["invariant_checks"]["ok"] is True


def test_checker_catches_leaked_option():
    dctx = four.SCENARIOS["hard_constraints"]
    parsed = {"response": {"message": "Вот вариант", "items": [{"option_id": "reject_1", "label": "Rejected"}], "final_question": "Разобрать?"}}
    checks = four.check_invariants(dctx, parsed)
    assert checks["ok"] is False
    assert "option_outside_allowed_ids" in checks["failures"]
    assert checks["leaked_option_ids"] == ["reject_1"]


def test_checker_catches_forbidden_liquidity_claim():
    dctx = four.SCENARIOS["unsupported_claim"]
    parsed = {
        "response": {
            "message": "ЖК Береговой корпус выглядит ликвидным.",
            "items": [{"option_id": "claim_limited_1", "label": "ЖК Береговой корпус", "claims": ["liquidity"]}],
            "final_question": "Разобрать подробнее?",
        }
    }
    checks = four.check_invariants(dctx, parsed)
    assert checks["ok"] is False
    assert "forbidden_claim_keyword" in checks["failures"]
    assert checks["forbidden_fields_hit"] == ["liquidity"]


def test_checker_accepts_valid_restricted_no_match_and_hard_constraint_response():
    no_match = {"response": "Точных совпадений нет.", "params": {}, "visible_options": [], "final_question": "Что смягчим?"}
    assert four.check_invariants(four.SCENARIOS["no_match"], no_match)["ok"] is True

    hard = {
        "response": "Нашла ЖК Северный квартал.",
        "params": {},
        "visible_options": [{"option_id": "exact_1", "name": "ЖК Северный квартал"}],
        "final_question": "Разобрать подробнее?",
    }
    assert four.check_invariants(four.SCENARIOS["hard_constraints"], hard)["ok"] is True


def test_checker_keeps_legacy_nested_shape_for_old_fixtures():
    no_match = {"response": {"message": "Ничего не подошло.", "items": [], "final_question": "Что смягчим?"}}
    assert four.check_invariants(four.SCENARIOS["no_match"], no_match)["ok"] is True

    hard = {
        "response": {
            "message": "Нашла ЖК Северный квартал.",
            "items": [{"option_id": "exact_1", "label": "ЖК Северный квартал", "claims": ["location", "price"]}],
            "final_question": "Разобрать подробнее?",
        }
    }
    assert four.check_invariants(four.SCENARIOS["hard_constraints"], hard)["ok"] is True


def test_checker_requires_top_level_final_question_for_restricted_shape():
    parsed = {"response": "Нашла ЖК Северный квартал.", "params": {}, "visible_options": [{"option_id": "exact_1", "name": "ЖК Северный квартал"}]}
    checks = four.check_invariants(four.SCENARIOS["hard_constraints"], parsed)
    assert checks["ok"] is False
    assert "exactly_one_final_question" in checks["failures"]


def test_checker_rejects_question_inside_restricted_response():
    parsed = {"response": "Нашла ЖК Северный квартал. Разобрать?", "params": {}, "visible_options": [{"option_id": "exact_1", "name": "ЖК Северный квартал"}], "final_question": "Какой вариант интересен?"}
    checks = four.check_invariants(four.SCENARIOS["hard_constraints"], parsed)
    assert checks["ok"] is False
    assert "response_has_extra_question" in checks["failures"]


def test_safe_output_redacts_injected_secret_and_phone(monkeypatch):
    original = json.loads(json.dumps(four.SCENARIOS["hard_constraints"], ensure_ascii=False))
    sensitive = json.loads(json.dumps(original, ensure_ascii=False))
    sensitive["matched"][0]["debug_note"] = "call +7 999 111-22-33 with secret-token-abc"
    sensitive["source_refs"]["exact_1"] = "fixture has phone 89991112233 and api_keyabcdef"
    monkeypatch.setitem(four.SCENARIOS, "hard_constraints", sensitive)

    result = asyncio.run(four.run_scenario("hard_constraints", live=False))
    dumped = json.dumps(result, ensure_ascii=False)
    assert "+7 999" not in dumped
    assert "89991112233" not in dumped
    assert "secret-token-abc" not in dumped
    assert "api_keyabcdef" not in dumped

    monkeypatch.setitem(four.SCENARIOS, "hard_constraints", original)


def test_cli_json_dry_run_and_self_test_are_safe():
    proc = subprocess.run([sys.executable, str(SCRIPT), "--scenario", "no_match", "--json"], cwd=ROOT, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["mode"] == "dry_run"
    assert data["parsed"]["visible_options"] == []

    self_test = subprocess.run([sys.executable, str(SCRIPT), "--self-test", "--json"], cwd=ROOT, text=True, capture_output=True)
    assert self_test.returncode == 0, self_test.stderr
    assert json.loads(self_test.stdout)["ok"] is True
