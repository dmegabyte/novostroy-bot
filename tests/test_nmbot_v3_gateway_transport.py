from __future__ import annotations

import asyncio
import json

import pytest

from nmbot_v3.evidence_provider import V3EvidenceProviderRequest
from nmbot_v3.gateway_transport import V3GatewayConfigurationError, V3GatewayProtocolError, V3GatewayTaskTransport
from nmbot_v3.planner_provider import V3PlannerProviderRequest
from nmbot_v3.provider_invocation import V3InvocationOperation, V3TransportRequest
from nmbot_v3.presentation import V3WriterBriefInput
from nmbot_v3.writer_adapter import (
    build_v3_structured_writer_request,
    V3_WRITER_GATEWAY_OWNER,
    V3_WRITER_GATEWAY_REQUEST_MARKER,
    V3_WRITER_GATEWAY_RESULT_MARKER,
    V3WriterAdapter,
    V3WriterGatewayResult,
)


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = payload

    async def json(self) -> object:
        return self._payload

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, object, dict[str, str]]] = []
        self.closed = False

    def post(self, url: str, *, json: object, headers: dict[str, str]) -> _Response:
        self.calls.append(("post", url, json, headers))
        return self.responses.pop(0)

    def get(self, url: str, *, headers: dict[str, str]) -> _Response:
        self.calls.append(("get", url, None, headers))
        return self.responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def _environment() -> dict[str, str]:
    return {
        "NMBOT_V3_GATEWAY_URL": "https://gateway.example.test",
        "NMBOT_V3_GATEWAY_TOKEN_ENV": "V3_GATEWAY_TOKEN",
        "NMBOT_V3_PROVIDER_API_KEY_ENV": "V3_PROVIDER_KEY",
        "NMBOT_V3_PLANNER_MODEL": "planner-model",
        "NMBOT_V3_EVIDENCE_MODEL": "evidence-model",
        "NMBOT_V3_WRITER_MODEL": "writer-model",
        "NMBOT_V3_GATEWAY_TIMEOUT_SECONDS": "2",
        "NMBOT_V3_GATEWAY_POLL_INTERVAL_SECONDS": "0.001",
        "V3_GATEWAY_TOKEN": "not-printed-gateway-secret",
        "V3_PROVIDER_KEY": "not-printed-provider-secret",
    }


def test_gateway_transport_maps_proven_planner_contract_and_closes_fake_session() -> None:
    async def scenario() -> None:
        session = _Session([_Response(201, {"id": "task-1"}), _Response(200, {"status": "completed"}), _Response(200, {"result": {"response": '{"goal":"clarify"}'}})])
        transport = V3GatewayTaskTransport.from_environ(environ=_environment(), session_factory=lambda: session)
        request = V3TransportRequest("550e8400-e29b-41d4-a716-446655440000", V3InvocationOperation.PLANNER,
            V3PlannerProviderRequest("planner prompt", {"user_text": "safe"}, {"type": "object"}))
        response = await transport.invoke(request)
        envelope = session.calls[0][2]
        assert response.request_id == request.request_id and response.payload == '{"goal":"clarify"}'
        assert session.calls[0][0] == "post" and session.calls[0][1].endswith("/api/v1/tasks/api")
        assert envelope == {
            "agent_name": "gateway-agent", "endpoint": "/process", "timeout_seconds": 2, "max_retries": 0,
            "request_data": {
                "query": json.dumps({"v3_request": {"user_text": "safe"}, "response_schema": {"type": "object"}}, ensure_ascii=False, separators=(",", ":")),
                "service": "openrouter", "model": "planner-model", "system_prompt": "planner prompt",
                "parameters": {"temperature": 0.0, "max_tokens": 900}, "external_api_key": "not-printed-provider-secret",
            },
        }
        assert session.calls[0][3]["Authorization"] == "Bearer not-printed-gateway-secret"
        await transport.close()
        assert session.closed is True
    asyncio.run(scenario())


def test_gateway_transport_proves_evidence_overlap_and_fails_closed_for_unknown_writer_mapping() -> None:
    async def scenario() -> None:
        session = _Session([_Response(201, {"id": "task-2"}), _Response(200, {"status": "completed"}), _Response(200, {"response": {"facts": [], "near": [], "missing_facts": []}})])
        transport = V3GatewayTaskTransport.from_environ(environ=_environment(), session_factory=lambda: session)
        request = V3TransportRequest("550e8400-e29b-41d4-a716-446655440001", V3InvocationOperation.EVIDENCE,
            V3EvidenceProviderRequest("evidence prompt", {"mode": "broad"}, {"type": "object"}))
        response = await transport.invoke(request)
        assert response.payload == {"facts": [], "near": [], "missing_facts": []}
        assert session.calls[0][2]["request_data"]["model"] == "evidence-model"
    asyncio.run(scenario())


def test_gateway_transport_requires_named_nonsecret_config_and_never_guesses_writer_protocol() -> None:
    with pytest.raises(V3GatewayConfigurationError, match="missing_v3_gateway_config:NMBOT_V3_GATEWAY_URL"):
        V3GatewayTaskTransport.from_environ(environ={}, session_factory=lambda: None)
    env = _environment()
    del env["V3_PROVIDER_KEY"]
    with pytest.raises(V3GatewayConfigurationError, match="missing_v3_gateway_credential"):
        V3GatewayTaskTransport.from_environ(environ=env, session_factory=lambda: None)


def test_gateway_transport_verifies_v3_owned_writer_result_envelope_before_wrapping() -> None:
    async def scenario() -> None:
        output = {
            "intro": "Подобрала варианты.", "cards": [], "recommendation": "",
            "missing_note": "", "final_question": "Что показать подробнее?",
        }
        session = _Session([
            _Response(201, {"id": "task-writer-1"}), _Response(200, {"status": "completed"}),
            _Response(200, {"result": {"response": {
                "result_marker": V3_WRITER_GATEWAY_RESULT_MARKER,
                "output": output,
            }}}),
        ])
        transport = V3GatewayTaskTransport.from_environ(environ=_environment(), session_factory=lambda: session)
        writer_request = build_v3_structured_writer_request(V3WriterBriefInput(
            client_request="Покажите варианты", answer_goal="present_search_results",
        ))
        request = V3TransportRequest("550e8400-e29b-41d4-a716-446655440002", V3InvocationOperation.WRITER, writer_request)
        response = await transport.invoke(request)

        gateway_request = session.calls[0][2]["request_data"]
        query = json.loads(gateway_request["query"])
        assert gateway_request["model"] == "writer-model"
        assert "V3WriterBrief" in gateway_request["system_prompt"]
        assert query["v3_request"]["owner"] == V3_WRITER_GATEWAY_OWNER
        assert query["v3_request"]["request_marker"] == V3_WRITER_GATEWAY_REQUEST_MARKER
        schema = query["response_schema"]
        assert schema["required"] == ["result_marker", "output"]
        assert schema["properties"]["result_marker"] == {
            "type": "string", "const": V3_WRITER_GATEWAY_RESULT_MARKER,
        }
        assert schema["properties"]["output"]["properties"]["final_question"] == {"type": "string"}
        assert query["v3_request"]["response_schema"] == schema
        assert isinstance(response.payload, V3WriterGatewayResult)
        assert dict(response.payload.output) == output
    asyncio.run(scenario())


@pytest.mark.parametrize("writer_response", [
    {"result_marker": "nmbot.v3.structured_writer.gateway.result.v0", "output": {}},
    {"output": {}},
    {"result_marker": V3_WRITER_GATEWAY_RESULT_MARKER},
    {"result_marker": V3_WRITER_GATEWAY_RESULT_MARKER, "output": "not-a-mapping"},
])
def test_gateway_transport_rejects_writer_result_without_exact_marked_mapping(writer_response: object) -> None:
    async def scenario() -> None:
        session = _Session([
            _Response(201, {"id": "task-writer-invalid"}), _Response(200, {"status": "completed"}),
            _Response(200, {"result": {"response": writer_response}}),
        ])
        transport = V3GatewayTaskTransport.from_environ(environ=_environment(), session_factory=lambda: session)
        writer_request = build_v3_structured_writer_request(V3WriterBriefInput(
            client_request="Покажите варианты", answer_goal="present_search_results",
        ))
        request = V3TransportRequest("550e8400-e29b-41d4-a716-446655440003", V3InvocationOperation.WRITER, writer_request)
        with pytest.raises(V3GatewayProtocolError, match="v3_gateway_writer_result_invalid"):
            await transport.invoke(request)
    asyncio.run(scenario())


def test_gateway_transport_invalid_writer_envelope_uses_redacted_writer_fallback() -> None:
    async def scenario() -> None:
        session = _Session([
            _Response(201, {"id": "task-writer-fallback"}), _Response(200, {"status": "completed"}),
            _Response(200, {"result": {"response": {"output": {}}}}),
        ])
        transport = V3GatewayTaskTransport.from_environ(environ=_environment(), session_factory=lambda: session)
        result = await V3WriterAdapter(transport=transport).write(V3WriterBriefInput(
            client_request="Покажите варианты", answer_goal="present_search_results",
        ))
        assert (result.ok, result.errors) == (False, ("writer_unavailable",))
        assert "gateway" not in result.public_text.casefold()
    asyncio.run(scenario())
