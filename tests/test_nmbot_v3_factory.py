from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from nmbot_v3.contracts import IntentPlanV3, V3ContractError, V3PlannerContext
from nmbot_v3.evidence_contract import EvidenceRequest, EvidenceResult
from nmbot_v3.factory import build_v3_adapter_factory
from nmbot_v3.ports import V3PlannerRequest, V3ProviderError, V3ProviderErrorCode, V3RedactedText
from nmbot_v3.provider_invocation import V3InvocationOperation, V3TransportResponse
from nmbot_v3.presentation import V3WriterBriefInput
from nmbot_v3.writer_adapter import V3_WRITER_GATEWAY_RESULT_MARKER


_REF = "550e8400-e29b-41d4-a716-446655440000"
_FIXTURE = json.loads(Path("tests/fixtures/v3_writer_differential_overlap.json").read_text(encoding="utf-8"))


def _plan() -> dict[str, object]:
    return {
        "schema_version": 3, "goal": "answer_current", "viewpoint": "unchanged",
        "selected_option_name": None, "selected_option_ref": None, "named_object_reference": None,
        "comparison_option_names": [], "comparison_option_refs": [], "requested_facts": [],
        "constraints_delta": {}, "operator_consent": None, "explicit_operator_request": False,
        "followup_outcome": None, "clarification": None, "confidence": 0.9,
    }


def _evidence() -> dict[str, object]:
    return {
        "facts": [{"name": "ЖК Лучи", "canonical_ref": _REF, "fields": {"metro": "Солнцево"}, "is_near": False, "differences": []}],
        "near": [], "missing_facts": [],
    }


def test_v3_factory_dispatches_operations_over_one_transport_instance() -> None:
    async def scenario() -> None:
        class Transport:
            def __init__(self) -> None:
                self.requests = []

            async def invoke(self, request):
                self.requests.append(request)
                responses = {
                    V3InvocationOperation.PLANNER: _plan(),
                    V3InvocationOperation.EVIDENCE: _evidence(),
                    V3InvocationOperation.WRITER: {
                        "result_marker": V3_WRITER_GATEWAY_RESULT_MARKER, "output": _FIXTURE["valid_output"],
                    },
                }
                return V3TransportResponse(request.request_id, responses[request.operation])

        transport = Transport()
        adapters = build_v3_adapter_factory(transport, timeout_seconds=1)
        assert adapters.transport is transport
        assert adapters.planner._transport_invoker._transport is transport
        assert adapters.evidence._transport_invoker._transport is transport
        assert adapters.writer._transport_invoker._transport is transport

        planned = await adapters.planner.plan(V3PlannerRequest(V3RedactedText("Сравни"), V3PlannerContext((_REF,))))
        evidence = await adapters.evidence.search(EvidenceRequest("current_options_fact_check", ("metro",), current_option_refs=(_REF,), count=1))
        written = await adapters.writer.write(V3WriterBriefInput(**_FIXTURE["brief"]))

        assert isinstance(planned, IntentPlanV3)
        assert isinstance(evidence, EvidenceResult)
        assert written.ok is True
        assert [request.operation for request in transport.requests] == [
            V3InvocationOperation.PLANNER,
            V3InvocationOperation.EVIDENCE,
            V3InvocationOperation.WRITER,
        ]

    asyncio.run(scenario())


def test_v3_factory_rejects_missing_or_invalid_transport_and_unbounded_timeout() -> None:
    class Transport:
        async def invoke(self, request):
            return V3TransportResponse(request.request_id, {})

    for value in (None, object()):
        with pytest.raises(V3ContractError, match="invalid_v3_async_transport"):
            build_v3_adapter_factory(value, timeout_seconds=1)  # type: ignore[arg-type]
    for value in (False, 0, -1, 121, float("inf"), float("nan"), "1"):
        with pytest.raises(V3ContractError, match="invalid_v3_transport_timeout"):
            build_v3_adapter_factory(Transport(), timeout_seconds=value)  # type: ignore[arg-type]


def test_v3_factory_preserves_timeout_and_error_redaction() -> None:
    async def scenario() -> None:
        class SlowTransport:
            async def invoke(self, request):
                await asyncio.sleep(0.02)
                return V3TransportResponse(request.request_id, _plan())

        class SecretFailureTransport:
            async def invoke(self, _request):
                raise RuntimeError("Authorization: Bearer top-secret")

        timed_out = await build_v3_adapter_factory(SlowTransport(), timeout_seconds=0.001).planner.plan(
            V3PlannerRequest(V3RedactedText("Покажи варианты"), V3PlannerContext())
        )
        result = await build_v3_adapter_factory(SecretFailureTransport(), timeout_seconds=1).planner.plan(
            V3PlannerRequest(V3RedactedText("Покажи варианты"), V3PlannerContext())
        )
        assert isinstance(timed_out, V3ProviderError)
        assert (timed_out.code, timed_out.retryable) == (V3ProviderErrorCode.TIMEOUT, True)
        assert isinstance(result, V3ProviderError)
        assert (result.code, result.retryable) == (V3ProviderErrorCode.UNAVAILABLE, True)
        assert "secret" not in repr(result).casefold()

    asyncio.run(scenario())


def test_v3_factory_import_closure_has_no_runtime_or_network_dependencies() -> None:
    tree = ast.parse(Path("nmbot_v3/factory.py").read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imports += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = (
        "aiohttp", "requests", "http", "socket", "os", "overmind", "scripts", "nmbot_runtime",
        "nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4",
    )
    assert not any(name == blocked or name.startswith(blocked + ".") for name in imports for blocked in banned)
