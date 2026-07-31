from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import nmbot_prompt_static_check as check  # noqa: E402


def test_v2_prompt_matches_prompt_master_structural_boundary() -> None:
    v1 = check.load_prompt("four_layer_presenter_v1")
    v2 = check.load_prompt("four_layer_presenter_v2")
    report = check.evaluate_prompt(v2, baseline=v1)

    assert report["ok"] is True
    assert report["no_longer_than_baseline"] is True
    assert all(report["required"].values())
    assert report["forbidden_hits"] == []


def test_static_checker_cli_compares_v2_to_v1_without_network() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "nmbot_prompt_static_check.py"), "four_layer_presenter_v2", "--baseline", "four_layer_presenter_v1"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["ok"] is True


def test_search_v2_prompt_matches_search_boundary() -> None:
    report = check.evaluate_search_prompt(check.load_prompt("four_layer_search_v2"))

    assert report["ok"] is True
    assert all(report["required"].values())
    assert report["forbidden_hits"] == []


def test_static_checker_cli_checks_search_candidate_without_network() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "nmbot_prompt_static_check.py"), "four_layer_search_v2", "--kind", "search"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["ok"] is True
