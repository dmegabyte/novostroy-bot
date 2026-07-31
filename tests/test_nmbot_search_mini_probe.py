from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import nmbot_search_mini_probe as probe


def test_safe_result_reports_only_counts_and_evidence_keys() -> None:
    result = probe.safe_result(
        "family",
        {
            "facts": [{"id": "secret-id", "name": "Secret name", "price_min": 1, "details": {"infrastructure_family": {"schools": ["private"]}}}],
            "near": [{"token": "must-not-leak"}],
        },
        1.25,
    )
    rendered = str(result)
    assert result["counts"] == {"facts": 1, "near": 1}
    assert result["evidence_coverage"]["price"] == 1
    assert result["evidence_coverage"]["family"] == 1
    assert "infrastructure_family" in result["fact_key_names"]
    assert "secret-id" not in rendered
    assert "Secret name" not in rendered
    assert "must-not-leak" not in rendered


def test_probe_builds_typed_constraints() -> None:
    plan = probe._plan(probe.PROBES["location_budget"])
    assert plan["constraints_patch"]["hard"] == {"location": ["Москва"], "price": 30_000_000}


def test_run_probe_uses_existing_live_search(monkeypatch) -> None:
    seen = {}

    async def fake_live_search(text, plan, scenario, timeout, **kwargs):
        seen.update({"text": text, "plan": plan, "scenario": scenario, "timeout": timeout, **kwargs})
        return {"facts": [{"price_range": "safe"}], "near": []}

    monkeypatch.setattr(probe.e2e, "live_search", fake_live_search)
    monkeypatch.setattr(probe.e2e, "load_search_prompt", lambda name: "PROMPT")
    result = asyncio.run(probe.run_probe("budget", prompt="search_v1", profile=None, timeout=7))
    assert result["counts"]["facts"] == 1
    assert seen["system_prompt"] == "PROMPT"
    assert seen["plan"]["constraints_patch"]["hard"]["price"] == 30_000_000
