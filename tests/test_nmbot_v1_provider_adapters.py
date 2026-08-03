from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

import nmbot_v1.provider_adapters as provider_adapters
from nmbot_v1.contracts import V1Error
from nmbot_v1.one_model_response import validate_one_model_response
from nmbot_v1.provider_adapters import (
    MCP_ALIAS,
    PLANNER_MODEL,
    PLANNER_PAYLOAD_STAGE,
    SEARCH_MODEL,
    SEARCH_PAYLOAD_STAGE,
    ONE_MODEL_RESPONSE_PAYLOAD_STAGE,
    V1GatewayOneModelResponsePort,
    V1GatewayPlannerPort,
    V1GatewaySearchPort,
)
from nmbot_v1.search_contract import V1SearchRequest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
SCRIPT = SCRIPT_DIR / "nmbot_api_server.py"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("nmbot_api_server_v1_provider_test", SCRIPT)
api = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["nmbot_api_server_v1_provider_test"] = api
spec.loader.exec_module(api)


def test_v1_prompts_declare_required_owner_contract_sections() -> None:
    required = (
        "Purpose:",
        "Inputs:",
        "Output schema:",
        "Priority rules:",
        "Forbidden claims:",
        "Owner layer:",
        "Validation:",
    )
    for relative in ("prompts/v1/intent_planner.txt", "prompts/v1/search_mcp.txt"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        positions = [text.index(section) for section in required]
        assert positions == sorted(positions)
    planner_text = (ROOT / "prompts/v1/intent_planner.txt").read_text(encoding="utf-8")
    assert '`"schema_version": 1`' in planner_text
    assert "не пропускай его" in planner_text


class FakeGateway:
    def __init__(self, text: str, meta: dict[str, Any] | None = None) -> None:
        self.text = text
        self.meta = meta or {}
        self.calls: list[tuple[dict[str, Any], dict[str, Any], int]] = []

    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
        self.calls.append((request_data, headers, timeout))
        return self.text, self.meta


class SequenceGateway:
    def __init__(self, responses: list[tuple[str, dict[str, Any]]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[dict[str, Any], dict[str, Any], int]] = []

    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
        self.calls.append((request_data, headers, timeout))
        return self.responses.pop(0)


def test_v1_planner_request_and_strict_parse(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "SECRET_KEY")
    gateway = FakeGateway("```json\n" + json.dumps({
        "schema_version": 1,
        "goal": "search",
        "viewpoint": "buyer",
        "constraints_delta": {"hard": {"location": "Москва"}, "preferences": {}},
        "selected_option_ref": None,
        "selected_lot_ref": None,
        "requested_facts": [],
        "operator_intent": "none",
        "clarification": None,
        "contact_name": None,
        "contact_phone": None,
        "confidence": 0.9,
    }, ensure_ascii=False) + "\n```")
    port = V1GatewayPlannerPort(gateway)

    plan = asyncio.run(port.plan({"schema_version": 1, "safe_user_text": "ищу"}))

    payload, headers, timeout = gateway.calls[0]
    assert payload["_payload_stage"] == PLANNER_PAYLOAD_STAGE
    assert payload["model"] == PLANNER_MODEL
    assert payload["parameters"]["max_tokens"] == 1200
    assert "V1_PLANNER_INPUT=" in payload["query"]
    assert payload["external_api_key"] == "SECRET_KEY"
    assert headers["Authorization"].startswith("Bearer ")
    assert timeout > 0
    assert plan.goal.value == "search"
    dumped = json.dumps({"plan": plan.to_dict(), "provenance": port.prompt_provenance}, ensure_ascii=False)
    assert "SECRET_KEY" not in dumped
    assert "system_prompt" not in dumped
    assert port.prompt_provenance["prompts"][0]["source"] == "prompts/v1/intent_planner.txt"


def test_v1_search_request_mcp_alias_attempt_sanitization_and_no_leak(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    gateway = FakeGateway(json.dumps({
        "schema_version": 1,
        "cards": [{"ref": "p1", "name": "ЖК Первый", "facts": {"raw_payload": "SECRET"}, "evidence": {"location": "Москва", "max_price": 10}}],
        "attempts": [{"status": "ok", "model": SEARCH_MODEL, "duration_ms": 123, "token": "SECRET", "raw": "RAW"}],
    }, ensure_ascii=False))
    port = V1GatewaySearchPort(gateway)

    result = asyncio.run(port.search(V1SearchRequest(hard_constraints={"location": "Москва", "max_price": 12})))

    payload = gateway.calls[0][0]
    assert payload["_payload_stage"] == SEARCH_PAYLOAD_STAGE
    assert payload["model"] == SEARCH_MODEL
    assert payload["parameters"]["max_tokens"] == 3500
    assert payload["mcp_servers"] == [MCP_ALIAS]
    assert "external_api_key" not in payload
    assert result == {
        "schema_version": 1,
        "cards": [{"ref": "p1", "name": "ЖК Первый", "facts": {"raw_payload": "SECRET"}, "evidence": {"location": "Москва", "max_price": 10}}],
        "attempts": [{"status": "ok", "model": SEARCH_MODEL, "duration_ms": 123}],
    }
    assert "SECRET" not in json.dumps({"attempts": result["attempts"], "provenance": port.prompt_provenance}, ensure_ascii=False)
    assert port.prompt_provenance["prompts"][0]["source"] == "prompts/v1/search_mcp.txt"


def test_v1_search_retries_invalid_json_once_without_copying_raw_output() -> None:
    valid = json.dumps({"schema_version": 1, "cards": [], "attempts": []})
    gateway = SequenceGateway([('{"schema_version":1,"cards":[', {}), (valid, {})])

    result = asyncio.run(V1GatewaySearchPort(gateway).search(V1SearchRequest()))

    assert len(gateway.calls) == 2
    assert "FORMAT_RECOVERY=" in gateway.calls[1][0]["query"]
    assert '{"schema_version":1,"cards":[' not in gateway.calls[1][0]["query"]
    assert result["cards"] == []
    assert result["attempts"] == [{"status": "failed", "code": "invalid_json", "model": SEARCH_MODEL}]

    always_bad = SequenceGateway([("{", {}), ("{", {})])
    with pytest.raises(V1Error, match="invalid_json"):
        asyncio.run(V1GatewaySearchPort(always_bad).search(V1SearchRequest()))
    assert len(always_bad.calls) == 2


def test_v1_search_retry_uses_remaining_deadline_and_stops_when_exhausted(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(provider_adapters, "monotonic", lambda: clock[0])
    monkeypatch.setenv("NMBOT_V1_SEARCH_TIMEOUT", "10")

    class AdvancingGateway(SequenceGateway):
        def __init__(self, responses: list[tuple[str, dict[str, Any]]], advance: float) -> None:
            super().__init__(responses)
            self.advance = advance

        async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
            response = await super()._run_gateway_request(request_data, headers, timeout)
            clock[0] += self.advance
            return response

    valid = json.dumps({"schema_version": 1, "cards": [], "attempts": []})
    gateway = AdvancingGateway([("{", {}), (valid, {})], advance=4)
    asyncio.run(V1GatewaySearchPort(gateway).search(V1SearchRequest()))
    assert [call[2] for call in gateway.calls] == [10, 6]

    clock[0] = 100.0
    exhausted = AdvancingGateway([("{", {}), (valid, {})], advance=10)
    with pytest.raises(V1Error, match="upstream_error"):
        asyncio.run(V1GatewaySearchPort(exhausted).search(V1SearchRequest()))
    assert len(exhausted.calls) == 1

    clock[0] = 100.0
    subsecond = AdvancingGateway([("{", {}), (valid, {})], advance=9.5)
    with pytest.raises(V1Error, match="upstream_error"):
        asyncio.run(V1GatewaySearchPort(subsecond).search(V1SearchRequest()))
    assert len(subsecond.calls) == 1


def test_v1_provider_adapters_fail_closed_on_safe_fallback_invalid_json_and_unknown_fields() -> None:
    with pytest.raises(V1Error, match="safe_fallback"):
        asyncio.run(V1GatewayPlannerPort(FakeGateway("{}", {"_safe_fallback": True})).plan({"schema_version": 1}))

    with pytest.raises(V1Error, match="invalid_json"):
        asyncio.run(V1GatewayPlannerPort(FakeGateway("пояснение\n```json\n{}\n```" )).plan({"schema_version": 1}))

    bad_plan = json.dumps({
        "schema_version": 1,
        "goal": "search",
        "viewpoint": "buyer",
        "constraints_delta": {"hard": {}, "preferences": {}},
        "selected_option_ref": None,
        "selected_lot_ref": None,
        "requested_facts": [],
        "operator_intent": "none",
        "clarification": None,
        "contact_name": None,
        "contact_phone": None,
        "confidence": 1,
        "endpoint": "/process",
    })
    with pytest.raises(V1Error, match="unknown fields"):
        asyncio.run(V1GatewayPlannerPort(FakeGateway(bad_plan)).plan({"schema_version": 1}))

    bad_search = json.dumps({"schema_version": 1, "cards": [], "attempts": [], "raw_payload": {"secret": True}})
    with pytest.raises(V1Error, match="unknown fields"):
        asyncio.run(V1GatewaySearchPort(FakeGateway(bad_search)).search(V1SearchRequest()))


def test_create_app_wires_v1_ports_and_presenter_off(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NMBOT_API_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("NMBOT_RUNTIME_VERSION_FILE", str(tmp_path / "runtime.json"))
    monkeypatch.setenv("NMBOT_CALLBACK_OUTBOX_DIR", str(tmp_path / "outbox"))

    app = api.create_app()

    try:
        assert isinstance(app["v1_planner_port"], V1GatewayPlannerPort)
        assert isinstance(app["v1_search_port"], V1GatewaySearchPort)
        assert isinstance(app["v1_one_model_gpt55_port"], V1GatewayOneModelResponsePort)
        assert app["v1_planner_port"].gateway_client is app["overmind_client"]
        assert app["v1_search_port"].gateway_client is app["overmind_client"]
        assert app["v1_one_model_gpt55_port"].gateway_client is app["overmind_client"]
        assert app["v1_presenter_mode"] == "off"
        assert "v1_presenter_port" not in app
    finally:
        asyncio.run(api.close_client(app))


def test_v1_one_model_response_port_payload_model_pin_and_validation(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "SECRET_KEY")
    gateway = FakeGateway(json.dumps({
        "response": "Есть ЖК Первый. Хотите выбрать этот вариант?",
        "visible_options": [{"name": "ЖК Первый"}],
        "next_action": "inspect_option",
    }, ensure_ascii=False))
    port = V1GatewayOneModelResponsePort(gateway)
    model_input = {
        "client_message": "ищу",
        "state_summary": {"stage": "first_search"},
        "evidence": {"facts": [{"name": "ЖК Первый", "price": 10}], "near": [], "missing": [], "params": {}},
    }

    result = asyncio.run(port.present(model_input))

    payload, _headers, timeout = gateway.calls[0]
    assert payload["_payload_stage"] == ONE_MODEL_RESPONSE_PAYLOAD_STAGE
    assert payload["model"] == "openai/gpt-5.5"
    assert payload["parameters"] == {"temperature": 0.3, "max_tokens": 1800}
    assert payload["query"].startswith("V1_ONE_MODEL_INPUT=")
    assert "mcp_servers" not in payload
    assert timeout > 0
    assert result["next_action"] == "inspect_option"
    dumped = json.dumps({"result": result, "provenance": port.prompt_provenance}, ensure_ascii=False)
    assert "SECRET_KEY" not in dumped

    bad = FakeGateway(json.dumps({
        "response": "Да, семейная ипотека возможна по ЖК Первый. Оформить?",
        "visible_options": [{"name": "ЖК Первый"}],
        "next_action": "inspect_option",
    }, ensure_ascii=False))
    with pytest.raises(V1Error, match="one_model_validation_failed"):
        asyncio.run(V1GatewayOneModelResponsePort(bad).present({**model_input, "client_message": "семейная ипотека"}))


def test_v1_one_model_validator_unquoted_project_mentions_and_visible_options() -> None:
    model_input = {
        "client_message": "ищу",
        "evidence": {"facts": [{"name": "ЖК Первый", "location": "Западное Дегунино", "price": "от 10 млн"}], "near": [], "missing": [], "params": {}},
    }
    grounded = {"response": "1. Первый — от 10 млн, Западное Дегунино. Показать подробнее?", "visible_options": [{"name": "ЖК Первый"}], "next_action": "inspect_option"}
    assert validate_one_model_response(grounded, model_input) == []

    ungrounded = {"response": "1. Горки Парк — цена по запросу. Показать подробнее?", "visible_options": [], "next_action": "inspect_option"}
    assert any(err.startswith("unknown_project_mention:горки парк") for err in validate_one_model_response(ungrounded, model_input))

    mismatch = {"response": "Есть ЖК Первый и ЖК Второй. Какой смотреть?", "visible_options": [{"name": "ЖК Первый"}], "next_action": "inspect_option"}
    errors = validate_one_model_response(mismatch, {"client_message": "ищу", "evidence": {"facts": [{"name": "ЖК Первый"}, {"name": "ЖК Второй"}], "near": [], "missing": [], "params": {}}})
    assert any(err.startswith("project_mention_not_visible:второй") for err in errors)


def test_v1_one_model_candidate_prompt_uses_tatyana_identity() -> None:
    text = (ROOT / "prompts/candidates/v1_one_model_gpt55_experiment_v1.txt").read_text(encoding="utf-8")
    assert "Ты — Татьяна" in text
    assert "Ты — Валерия" not in text
