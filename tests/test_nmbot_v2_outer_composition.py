from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from nmbot_runtime_contract.wire import CONTRACT_VERSION
from nmbot_v2.contracts import SafeTurnContext, SemanticPlan
from nmbot_v2.gateway import V2GatewayClient, V2GatewayConfig, V2GatewayErrorCode, V2GatewayResult
from nmbot_v2.outer_composition import V2OuterCompositionConfig, build_v2_outer_app, build_v2_outer_turn_processor
from nmbot_v2.state import ConversationState


class FakeGateway:
    def __init__(self, result: V2GatewayResult | None = None) -> None:
        self.calls: list[tuple[dict, float | None]] = []
        self.result = result

    async def invoke(self, request_data, *, timeout_seconds=None):
        self.calls.append((dict(request_data), timeout_seconds))
        if self.result is not None:
            return self.result
        if request_data.get("marker"):
            return V2GatewayResult(text=json.dumps(_planner_search_result()))
        query = str(request_data.get("query") or "")
        envelope = json.loads(query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
        return V2GatewayResult(text=json.dumps({
            "facts": [{"name": "ЖК Тест", "location": "Москва", "min_price": 12_000_000}],
            "near": [], "missing": [], "params": {},
            "diagnostics": {
                "mcp_tool": "novostroym/get_flat_info",
                "response_viewpoint": envelope["response_viewpoint"],
                "base_viewpoint": envelope["base_viewpoint"],
                "requested_field_priorities": envelope["available_fact_fields"][:12],
                "relaxation_audit": [], "ignored_preferences": [], "notes": [],
            },
        }, ensure_ascii=False))


def _config(gateway):
    return V2OuterCompositionConfig(
        gateway=gateway,
        planner_model="v2-planner-test",
        search_timeout_seconds=7,
        enrichment_timeout_seconds=3,
    )


def test_outer_composition_builds_v2_ports_and_maps_search_only_through_typed_contract() -> None:
    gateway = FakeGateway()
    processor = build_v2_outer_turn_processor(_config(gateway))

    turn = asyncio.run(processor.process_async(
        SafeTurnContext(conversation_ref="local", user_text="подбери квартиру"), ConversationState(),
    ))

    assert turn.execution.ok is True
    assert len(gateway.calls) == 2 and gateway.calls[0][1] == 10
    assert gateway.calls[0][0]["marker"] == "nmbot.v2.semantic-planner.gateway.v1"
    payload = gateway.calls[1][0]
    assert payload["_payload_stage"] == "main_search"
    assert "SEARCH_CONTRACT_ENVELOPE=" in payload["query"]
    assert "Authorization" not in json.dumps(payload, ensure_ascii=False)


def test_outer_search_maps_gateway_failure_to_stable_redacted_runtime_error() -> None:
    class PlannerThenTimeout(FakeGateway):
        async def invoke(self, request_data, *, timeout_seconds=None):
            self.calls.append((dict(request_data), timeout_seconds))
            if request_data.get("marker"):
                return V2GatewayResult(text=json.dumps(_planner_search_result()))
            return V2GatewayResult(error_code=V2GatewayErrorCode.TIMEOUT)

    gateway = PlannerThenTimeout()
    processor = build_v2_outer_turn_processor(_config(gateway))

    turn = asyncio.run(processor.process_async(
        SafeTurnContext(conversation_ref="local", user_text="подбери квартиру"), ConversationState(),
    ))

    assert turn.execution.ok is False
    assert turn.execution.error_code == "V2SearchTimeoutError"
    assert "gateway_timeout" not in turn.response_text


def test_outer_composition_has_no_global_runtime_or_legacy_gateway_import() -> None:
    for relative in ("nmbot_v2/gateway.py", "nmbot_v2/outer_composition.py"):
        tree = ast.parse(Path(relative).read_text(encoding="utf-8"))
        imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        imports += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(name == "scripts" or name.startswith("scripts.") or "nmbot_runtime_adapter" in name for name in imports)


def test_outer_composition_requires_explicit_planner_model() -> None:
    gateway = FakeGateway()
    config = V2OuterCompositionConfig(gateway=gateway)
    try:
        build_v2_outer_turn_processor(config)
    except ValueError as exc:
        assert str(exc) == "v2_planner_model_required"
    else:
        raise AssertionError("missing planner provider must fail closed")


class _Response:
    def __init__(self, status, payload) -> None:
        self.status, self._payload = status, payload

    async def json(self, **_kwargs):
        return self._payload


class _RequestContext:
    def __init__(self, response) -> None:
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        return None


class _Session:
    def __init__(self) -> None:
        self.closed = False
        self.requests = []
        self._responses = iter((
            _Response(201, {"id": "task-1"}),
            _Response(200, {"status": "completed"}),
            _Response(200, {"result": {"response": "{\"ok\": true}"}}),
        ))

    def post(self, *args, **kwargs):
        self.requests.append(("post", args, kwargs))
        return _RequestContext(next(self._responses))

    def get(self, *args, **kwargs):
        self.requests.append(("get", args, kwargs))
        return _RequestContext(next(self._responses))

    async def close(self):
        self.closed = True


def test_gateway_maps_task_protocol_once_without_retries_or_secret_in_result() -> None:
    session = _Session()
    client = V2GatewayClient(V2GatewayConfig(base_url="https://gateway.invalid", token="not-for-output"), session=session)

    result = asyncio.run(client.invoke({"_payload_stage": "main_search", "query": "safe"}, timeout_seconds=2))

    assert result.ok and result.text == '{"ok": true}'
    assert [kind for kind, *_ in session.requests] == ["post", "get", "get"]
    assert all(call[2]["headers"] == {"Authorization": "Bearer not-for-output"} for call in session.requests)
    asyncio.run(client.close())
    assert session.closed is False  # injected sessions remain owned by their caller

    owned_session = _Session()
    owned = V2GatewayClient(V2GatewayConfig(base_url="https://gateway.invalid", token="not-for-output"))
    owned._session, owned._owns_session = owned_session, True  # exercise owned-session cleanup without a network client
    asyncio.run(owned.close())
    assert owned_session.closed is True


def _planner_search_result() -> dict:
    return {
        "marker": "nmbot.v2.semantic-planner.gateway.v1", "schema_version": 1,
        "user_goal": "Подобрать квартиру", "refers_to_existing_objects": False,
        "requests_new_objects": True, "selected_reference": None,
        "named_object_reference": None, "requested_comparison": [], "scenario_needs": [],
        "response_viewpoint": "unchanged", "scenario_change": None, "constraints_delta": {},
        "requires_enrichment": False, "resolved_subject": None, "resolved_intent": None,
        "requested_facts": [], "facts_needed": [], "focus_action": "keep",
        "domain_relation": "in_domain", "clarification": None, "confidence": 0.9,
        "reason": "search",
    }


def _request(message="подбери квартиру") -> dict:
    return {"contract_version": CONTRACT_VERSION, "runtime_version": "V2", "conversation_ref": "loopback:1", "trace_ref": "trace:loopback", "message": message, "channel": "test", "meta": {}}


def test_private_worker_loopback_uses_gateway_planner_search_and_safe_noop_failures(tmp_path: Path) -> None:
    async def scenario() -> None:
        gateway = FakeGateway()
        app = build_v2_outer_app(config=_config(gateway), state_path=tmp_path / "state.json", journal_path=tmp_path / "journal.jsonl", token="test-token", release_identity="v2-loopback-immutable")
        client = TestClient(TestServer(app)); await client.start_server()
        try:
            response = await client.post("/api/chat", json=_request(), headers={"Authorization": "Bearer test-token"})
            body = await response.json()
            assert response.status == 200 and body["ok"] is True
            stored = json.loads((tmp_path / "state.json").read_text())
            assert stored["loopback:1"]["visible_options"][0]["name"] == "ЖК Тест"
        finally:
            await client.close()

        for failure in (V2GatewayResult(error_code=V2GatewayErrorCode.TIMEOUT), V2GatewayResult(text="not-json")):
            bad_gateway = FakeGateway(failure)
            app = build_v2_outer_app(config=_config(bad_gateway), state_path=tmp_path / "failed-state.json", journal_path=tmp_path / "failed-journal.jsonl", token="test-token", release_identity="v2-loopback-failure")
            client = TestClient(TestServer(app)); await client.start_server()
            try:
                response = await client.post("/api/chat", json=_request(), headers={"Authorization": "Bearer test-token"})
                body = await response.json()
                assert response.status == 200 and body["ok"] is False
                assert body["error_code"] == "v2_runtime_failure"
                assert not (tmp_path / "failed-state.json").exists()
                assert "timeout" not in body["client_answer"].lower()
            finally:
                await client.close()
    asyncio.run(scenario())


def test_outer_app_owns_and_closes_gateway_created_from_explicit_config(tmp_path: Path) -> None:
    async def scenario() -> None:
        session = _Session()
        app = build_v2_outer_app(
            config=V2OuterCompositionConfig(
                gateway_config=V2GatewayConfig(base_url="https://gateway.invalid", token="test-only-token"),
                gateway_session=session, planner_model="v2-planner-test",
            ),
            state_path=tmp_path / "state.json", journal_path=tmp_path / "journal.jsonl",
            token="test-token", release_identity="v2-owned-session",
        )
        client = TestClient(TestServer(app)); await client.start_server(); await client.close()
        assert session.closed is True
    asyncio.run(scenario())
