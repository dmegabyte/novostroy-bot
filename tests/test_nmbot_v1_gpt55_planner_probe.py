from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts import nmbot_v1_gpt55_planner_probe as probe


ROOT = Path(__file__).resolve().parents[1]


class FakeGateway:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[dict[str, Any], dict[str, Any], int]] = []

    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
        self.calls.append((request_data, headers, timeout))
        return self.text, {}


def _valid_regression_plan() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "goal": "fact_check",
        "viewpoint": "buyer",
        "constraints_delta": {"hard": {}, "preferences": {}},
        "selected_option_ref": None,
        "selected_lot_ref": None,
        "requested_facts": ["mortgage_terms"],
        "operator_intent": "none",
        "clarification": None,
        "contact_name": None,
        "contact_phone": None,
        "confidence": 0.93,
    }


def test_candidate_prompt_contract_is_source_backed_and_hash_only() -> None:
    contract = probe.validate_candidate_prompt()
    identity = probe.prompt_identity()

    assert contract == {"ok": True, "missing_markers": []}
    assert identity["source"] == probe.PROMPT_SOURCE
    assert len(identity["sha256"]) == 64
    assert "Purpose:" not in json.dumps(identity, ensure_ascii=False)
    assert '`operator_intent`: строго один из `none`, `request`, `accept`, `decline`' in probe.PROMPT_PATH.read_text(encoding="utf-8")


def test_fake_gateway_exact_regression_and_model_pin() -> None:
    gateway = FakeGateway(json.dumps(_valid_regression_plan(), ensure_ascii=False))

    code, report = asyncio.run(probe.probe_with_gateway(gateway))

    assert code == 0
    assert len(gateway.calls) == 1
    payload = gateway.calls[0][0]
    assert payload["_payload_stage"] == probe.PLANNER_PAYLOAD_STAGE
    assert payload["model"] == probe.MODEL
    assert "mcp_servers" not in payload
    assert report["model"] == {"service": "openrouter", "id": "openai/gpt-5.5"}
    assert report["contract_result"]["status"] == "passed"
    assert report["contract_result"]["plan"]["goal"] == "fact_check"
    assert report["contract_result"]["plan"]["requested_facts"] == ["mortgage_terms"]
    assert report["contract_result"]["plan"]["constraints_delta"] == {"hard": {}, "preferences": {}}
    assert report["expected_plan_check"]["ok"] is True


def test_cli_dry_run_has_no_provider_call_and_safe_report(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "SECRET_SHOULD_NOT_LEAK")

    result = subprocess.run(
        [sys.executable, "scripts/nmbot_v1_gpt55_planner_probe.py", "dry-run"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["mode"] == "dry-run"
    assert report["status"] == "passed"
    assert report["provider_called"] is False
    assert report["request_contract"]["model"] == "openai/gpt-5.5"
    assert report["request_contract"]["payload_stage"] == probe.PLANNER_PAYLOAD_STAGE
    assert report["request_contract"]["has_mcp_servers"] is False
    assert "SECRET_SHOULD_NOT_LEAK" not in result.stdout + result.stderr
    assert "system_prompt" not in result.stdout
    assert "external_api_key" not in result.stdout


def test_malformed_or_unknown_output_fails_closed_without_raw_provider_data() -> None:
    bad = _valid_regression_plan()
    bad["endpoint"] = "/process"
    gateway = FakeGateway(json.dumps(bad, ensure_ascii=False))

    code, report = asyncio.run(probe.probe_with_gateway(gateway))

    assert code == 2
    assert report["status"] == "failed"
    assert report["contract_result"] == {"status": "failed", "error_code": "unknown fields: endpoint"}
    assert "endpoint" in report["contract_result"]["error_code"]
    assert "/process" not in json.dumps(report, ensure_ascii=False)

    malformed = FakeGateway("not-json provider prose")
    code, report = asyncio.run(probe.probe_with_gateway(malformed))
    assert code == 2
    assert report["contract_result"] == {"status": "failed", "error_code": "invalid_json"}
    assert "not-json provider prose" not in json.dumps(report, ensure_ascii=False)
