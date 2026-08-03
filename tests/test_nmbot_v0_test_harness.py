from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_v0_test_harness.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, text=True, capture_output=True, check=False)


def test_v0_harness_successful_flow_is_stateful_and_scenario_only() -> None:
    proc = _run("--scenario", "successful_flow", "--json")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    result = payload["results"][0]

    assert result["ok"] is True
    assert result["calls"] == ["scenario_search", "scenario_search"]
    assert [card["name"] for card in result["turns"][0]["state"]["visible_options"]] == ["ЖК Первый", "ЖК Второй", "ЖК Третий"]
    assert result["turns"][1]["state"]["selected_option_name"] == "ЖК Первый"
    assert "Какой вариант хотите разобрать подробнее?" not in result["turns"][1]["message"]
    assert "Проверить подходящие семейные планировки в этом ЖК?" in result["turns"][1]["message"]
    assert not result["turns"][1]["message"].casefold().startswith("здравствуйте")


def test_v0_harness_missing_fact_requests_operator_phone() -> None:
    proc = _run("--scenario", "missing_fact", "--json")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    turn = payload["results"][0]["turns"][0]

    assert turn["ok"] is True
    assert "Оставите номер телефона, чтобы оператор проверил это и связался с вами?" in turn["message"]


def test_v0_harness_unknown_card_keeps_canonical_state_and_card() -> None:
    proc = _run("--scenario", "unknown_card", "--json")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    turn = payload["results"][0]["turns"][0]

    assert turn["ok"] is True
    assert turn["error_code"] is None
    assert [card["name"] for card in turn["state"]["visible_options"]] == ["ЖК Свой"]


def test_v0_harness_readable_all_scenarios() -> None:
    proc = _run("--scenario", "all")

    assert proc.returncode == 0, proc.stderr
    assert "NMBot V0 local deterministic harness" in proc.stdout
    assert "successful_flow" in proc.stdout
    assert "missing_fact" in proc.stdout
    assert "unknown_card" in proc.stdout
    assert "rental_third_typo_accept" in proc.stdout


def test_v0_harness_rental_third_typo_accept_transcript() -> None:
    proc = _run("--scenario", "rental_third_typo_accept", "--json")

    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)["results"][0]

    assert result["ok"] is True
    assert len(result["turns"]) == 3
    assert "Проверить доступные квартиры для сдачи именно в этом ЖК?" in result["turns"][1]["message"]
    assert "ЖК «Третий» для последующей сдачи" in result["turns"][2]["message"]
    assert "Нашла три" not in result["turns"][2]["message"]
