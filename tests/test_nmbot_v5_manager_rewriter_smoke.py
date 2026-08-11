from __future__ import annotations

import json
from pathlib import Path

from scripts.nmbot_v5_manager_rewriter_smoke import build_request, load_fixture


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "v5_manager_rewriter_smoke.json"


def test_v5_smoke_fixture_keeps_prompt_and_sources_separate() -> None:
    fixture, prompt = load_fixture(FIXTURE)

    assert "Параметры поиска бери только из сообщений пользователя" in prompt
    assert fixture["active_request"]["resolved_user_criteria"] == {"rooms": 2, "location": "Люблино"}
    assert fixture["dialogue_history"][0]["authority"] == "context_only"
    assert fixture["mcp_evidence"]["cards"][0]["card_id"] == "lubinsky-park"
    assert fixture["rewrite_policy"] == {"card_count": 1, "cta_mode": "single_card_followup", "question_limit": 1}


def test_v5_smoke_request_uses_production_gateway_contract() -> None:
    fixture, prompt = load_fixture(FIXTURE)
    request = build_request(fixture, prompt, api_key="test-key")

    assert request["_payload_stage"] == "conversation_answer_manager_rewriter"
    assert request["service"] == "openrouter"
    assert request["model"] == "deepseek/deepseek-v4-flash"
    assert request["external_api_key"] == "test-key"
    assert request["query"].startswith("V5_MANAGER_REWRITER_INPUT=")
    payload = json.loads(request["query"].split("=", 1)[1])
    assert payload["active_request"]["resolved_user_criteria"]["rooms"] == 2
    assert payload["mcp_evidence"]["cards"][0]["rank"] == 1
    assert payload["rewrite_policy"]["card_count"] == 1
