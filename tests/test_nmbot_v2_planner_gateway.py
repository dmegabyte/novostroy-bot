from __future__ import annotations

import asyncio
import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest

from nmbot_v2.contracts import OptionCard, SafeTurnContext
from nmbot_v2.planner_gateway import (
    V2GatewaySemanticPlannerAdapter,
    V2PlannerGatewayError,
    V2PlannerGatewayErrorCode,
    V2PlannerGatewayResponse,
    build_v2_planner_gateway_request,
)
from nmbot_v2.planner_gateway_contract import (
    V2_PLANNER_GATEWAY_MARKER,
    V2_PLANNER_GATEWAY_MODEL_CONFIG_KEY,
    V2_PLANNER_GATEWAY_TIMEOUT_CONFIG_KEY,
    require_v2_planner_gateway_contract,
)
from nmbot_v2.state import ConversationState


_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "nmbot_v2_planner_gateway.json").read_text(encoding="utf-8"))


class FakeGateway:
    def __init__(self, response: V2PlannerGatewayResponse) -> None:
        self.response = response
        self.calls: list[tuple[dict[str, object], float | None]] = []

    async def invoke(self, request_data, *, timeout_seconds=None):
        self.calls.append((dict(request_data), timeout_seconds))
        return self.response


def _state() -> ConversationState:
    return ConversationState(visible_options=(OptionCard(name="ЖК Лучи", metro="Солнцево"),), selected_option_name="ЖК Лучи")


def test_gateway_adapter_success_uses_versioned_v2_payload_and_does_not_mutate_state() -> None:
    async def scenario() -> None:
        gateway = FakeGateway(V2PlannerGatewayResponse(text=json.dumps(_FIXTURE["success"])))
        adapter = V2GatewaySemanticPlannerAdapter(gateway, model="v2-planner-test", timeout_seconds=7)
        state = _state()
        before = deepcopy(state)

        plan = await adapter.plan(SafeTurnContext("local", "Какое метро у Лучей?"), state)

        assert plan.operation == "select_option"
        assert plan.selected_option_name == "ЖК Лучи"
        assert state == before
        assert len(gateway.calls) == 1
        payload, timeout = gateway.calls[0]
        assert timeout == 7
        assert payload["marker"] == V2_PLANNER_GATEWAY_MARKER
        assert payload["schema_version"] == 1
        assert payload["model"] == "v2-planner-test"
        assert payload["planner_context"]["state"]["visible_options"] == [{"name": "ЖК Лучи", "metro": "Солнцево"}]
        assert "params" not in payload["planner_context"]["state"]
    asyncio.run(scenario())


def test_gateway_adapter_fails_before_runtime_for_malformed_result_and_timeout() -> None:
    async def scenario() -> None:
        state = _state()
        before = deepcopy(state)
        malformed = V2GatewaySemanticPlannerAdapter(FakeGateway(V2PlannerGatewayResponse(text=json.dumps(_FIXTURE["malformed"]))), model="v2-test")
        timed_out = V2GatewaySemanticPlannerAdapter(FakeGateway(V2PlannerGatewayResponse(error_code="v2_gateway_timeout")), model="v2-test")

        for adapter, expected in ((malformed, V2PlannerGatewayErrorCode.INVALID_RESPONSE), (timed_out, V2PlannerGatewayErrorCode.TIMEOUT)):
            with pytest.raises(V2PlannerGatewayError) as caught:
                await adapter.plan(SafeTurnContext("local", "Проверка"), state)
            assert caught.value.code is expected
            assert "secret" not in repr(caught.value).lower()
            assert state == before
    asyncio.run(scenario())


def test_gateway_request_redacts_pii_and_credentials_and_result_rejects_credentials() -> None:
    request = build_v2_planner_gateway_request(
        model="v2-test",
        planner_kwargs={
            "user_text": "мой телефон +79990000000, token=do-not-leak",
            "state": {"params": {"phone": "+79990000000"}, "visible_options": [{"name": "ЖК Лучи", "contact": "x"}]},
            "last_turn": {"bot_question": "ok", "client_answer": "password=not-for-output"},
            "selected_object": {"canonical_name": "ЖК Лучи", "present_fact_fields": ["metro"]},
            "allowed_subjects": ["metro"], "allowed_facts": ["metro"], "subject_fact_map": {"metro": ["metro"]},
        },
    )
    rendered = repr(request.to_gateway_payload())
    assert "do-not-leak" not in rendered and "not-for-output" not in rendered and "+79990000000" not in rendered
    assert "params" not in rendered and "contact" not in rendered

    bad = deepcopy(_FIXTURE["success"])
    bad["reason"] = "token=provider-secret"
    async def scenario() -> None:
        adapter = V2GatewaySemanticPlannerAdapter(FakeGateway(V2PlannerGatewayResponse(text=json.dumps(bad))), model="v2-test")
        with pytest.raises(V2PlannerGatewayError) as caught:
            await adapter.plan(SafeTurnContext("local", "проверка"), _state())
        assert caught.value.code is V2PlannerGatewayErrorCode.INVALID_RESPONSE
        assert "provider-secret" not in repr(caught.value)
    asyncio.run(scenario())


def test_gateway_contract_record_and_import_closure_are_v2_owned() -> None:
    contract = require_v2_planner_gateway_contract()
    assert contract.proven
    assert contract.model_config_key == V2_PLANNER_GATEWAY_MODEL_CONFIG_KEY
    assert contract.timeout_config_key == V2_PLANNER_GATEWAY_TIMEOUT_CONFIG_KEY
    assert contract.remaining_gaps == ("production_gateway_wire_compatibility_requires_separate_live_evidence",)

    tree = ast.parse(Path("nmbot_v2/planner_gateway.py").read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imports += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = ("aiohttp", "requests", "http", "socket", "scripts", "nmbot_v0", "nmbot_v1", "nmbot_v3", "nmbot_v4")
    assert not any(name == blocked or name.startswith(blocked + ".") for name in imports for blocked in banned)
