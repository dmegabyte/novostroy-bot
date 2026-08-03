from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

from nmbot_v3.contracts import IntentPlanV3, V3PlannerContext
from nmbot_v3.evidence_contract import EvidenceRequest, EvidenceResult
from nmbot_v3.evidence_provider import V3InjectedEvidenceSearchAdapter
from nmbot_v3.planner_provider import V3InjectedPlannerAdapter
from nmbot_v3.ports import V3PlannerRequest, V3ProviderError, V3ProviderErrorCode, V3RedactedText
from nmbot_v3.provider_invocation import (
    V3InvocationErrorCode,
    V3InvocationOperation,
    V3TransportInvoker,
    V3TransportResponse,
)
from nmbot_v3.presentation import V3WriterBriefInput
from nmbot_v3.writer_adapter import V3WriterAdapter, V3_WRITER_GATEWAY_RESULT_MARKER


_FIXTURE = json.loads(Path("tests/fixtures/v3_writer_differential_overlap.json").read_text(encoding="utf-8"))
_REF = "550e8400-e29b-41d4-a716-446655440000"


def _planner_request() -> V3PlannerRequest:
    return V3PlannerRequest(V3RedactedText("Сравни вариант; token=private"), V3PlannerContext((_REF,)))


def _plan() -> dict[str, object]:
    return {
        "schema_version": 3, "goal": "answer_current", "viewpoint": "unchanged",
        "selected_option_name": None, "selected_option_ref": None, "named_object_reference": None,
        "comparison_option_names": [], "comparison_option_refs": [], "requested_facts": [],
        "constraints_delta": {}, "operator_consent": None, "explicit_operator_request": False,
        "followup_outcome": None, "clarification": None, "confidence": 0.9,
    }


def _evidence_request() -> EvidenceRequest:
    return EvidenceRequest(mode="current_options_fact_check", requested_facts=("metro",), current_option_refs=(_REF,), count=1)


def _evidence_result() -> dict[str, object]:
    return {
        "facts": [{"name": "ЖК Лучи", "canonical_ref": _REF, "fields": {"metro": "Солнцево"}, "is_near": False, "differences": []}],
        "near": [], "missing_facts": [],
    }


def test_transport_invoker_enforces_identity_timeout_and_redacts_failures() -> None:
    async def scenario() -> None:
        seen = []

        class EchoTransport:
            async def invoke(self, request):
                seen.append(request)
                return V3TransportResponse(request.request_id, {"ok": True})

        result = await V3TransportInvoker(EchoTransport(), V3InvocationOperation.PLANNER, timeout_seconds=1).invoke({"safe": True})
        assert result.ok is True and result.payload == {"ok": True}
        assert seen[0].operation is V3InvocationOperation.PLANNER
        assert len(seen[0].request_id) == 36

        class WrongIdentityTransport:
            async def invoke(self, request):
                return V3TransportResponse("00000000-0000-0000-0000-000000000000", "ignored")

        class SecretFailureTransport:
            async def invoke(self, request):
                raise RuntimeError("Authorization: Bearer top-secret")

        class EmptyResponseTransport:
            async def invoke(self, request):
                return V3TransportResponse(request.request_id, None)

        class SlowTransport:
            async def invoke(self, request):
                await asyncio.sleep(0.02)
                return V3TransportResponse(request.request_id, "late")

        wrong = await V3TransportInvoker(WrongIdentityTransport(), V3InvocationOperation.EVIDENCE, timeout_seconds=1).invoke("safe")
        failed = await V3TransportInvoker(SecretFailureTransport(), V3InvocationOperation.EVIDENCE, timeout_seconds=1).invoke("safe")
        empty = await V3TransportInvoker(EmptyResponseTransport(), V3InvocationOperation.EVIDENCE, timeout_seconds=1).invoke("safe")
        timed_out = await V3TransportInvoker(SlowTransport(), V3InvocationOperation.EVIDENCE, timeout_seconds=0.001).invoke("safe")
        assert wrong.error is V3InvocationErrorCode.INVALID_RESPONSE
        assert failed.error is V3InvocationErrorCode.UNAVAILABLE
        assert empty.error is V3InvocationErrorCode.INVALID_RESPONSE
        assert timed_out.error is V3InvocationErrorCode.TIMEOUT
        assert "secret" not in repr(failed).casefold()
    asyncio.run(scenario())


def test_v3_adapters_can_inject_the_local_transport_without_legacy_gateway_policy() -> None:
    async def scenario() -> None:
        seen = []

        class PlannerTransport:
            async def invoke(self, request):
                seen.append(request)
                assert request.operation is V3InvocationOperation.PLANNER
                assert request.payload.payload["user_text"].endswith("[redacted-credential]")
                return V3TransportResponse(request.request_id, _plan())

        class EvidenceTransport:
            async def invoke(self, request):
                seen.append(request)
                assert request.operation is V3InvocationOperation.EVIDENCE
                assert request.payload.payload["current_option_refs"] == (_REF,)
                return V3TransportResponse(request.request_id, _evidence_result())

        class WriterTransport:
            async def invoke(self, request):
                seen.append(request)
                assert request.operation is V3InvocationOperation.WRITER
                assert request.payload.to_payload()["schema_version"] == "v3_writer_request_v1"
                return V3TransportResponse(request.request_id, {
                    "result_marker": V3_WRITER_GATEWAY_RESULT_MARKER, "output": _FIXTURE["valid_output"],
                })

        planned = await V3InjectedPlannerAdapter(transport=PlannerTransport()).plan(_planner_request())
        evidence = await V3InjectedEvidenceSearchAdapter(transport=EvidenceTransport()).search(_evidence_request())
        written = await V3WriterAdapter(transport=WriterTransport()).write(V3WriterBriefInput(**_FIXTURE["brief"]))
        assert isinstance(planned, IntentPlanV3)
        assert isinstance(evidence, EvidenceResult)
        assert written.ok is True
        assert len({request.request_id for request in seen}) == 3

        class MismatchedWriterTransport:
            async def invoke(self, request):
                return V3TransportResponse("wrong-request-id", {
                    "result_marker": V3_WRITER_GATEWAY_RESULT_MARKER, "output": _FIXTURE["valid_output"],
                })

        rejected = await V3WriterAdapter(transport=MismatchedWriterTransport()).write(V3WriterBriefInput(**_FIXTURE["brief"]))
        assert rejected.errors == ("writer_invalid_output",)

        class TimedOutPlannerTransport:
            async def invoke(self, request):
                await asyncio.sleep(0.02)
                return V3TransportResponse(request.request_id, _plan())

        timed_out = await V3InjectedPlannerAdapter(transport=TimedOutPlannerTransport(), timeout_seconds=0.001).plan(_planner_request())
        assert isinstance(timed_out, V3ProviderError)
        assert (timed_out.code, timed_out.retryable) == (V3ProviderErrorCode.TIMEOUT, True)
    asyncio.run(scenario())


def test_v3_transport_module_import_closure_stays_local_and_has_no_network_client() -> None:
    tree = ast.parse(Path("nmbot_v3/provider_invocation.py").read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imports += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = ("aiohttp", "requests", "http", "socket", "os", "nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4", "scripts")
    assert not any(name == blocked or name.startswith(blocked + ".") for name in imports for blocked in banned)
