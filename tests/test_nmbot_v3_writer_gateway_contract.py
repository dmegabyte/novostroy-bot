from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nmbot_v3.presentation import V3WriterBriefInput
from nmbot_v3.provider_invocation import V3TransportResponse
from nmbot_v3.writer_adapter import V3WriterAdapter, V3_WRITER_GATEWAY_RESULT_MARKER, V3WriterGatewayResult


FIXTURE = json.loads(Path("tests/fixtures/v3_writer_gateway_contract.json").read_text(encoding="utf-8"))


def _brief() -> V3WriterBriefInput:
    return V3WriterBriefInput(**FIXTURE["brief"])


def test_writer_gateway_contract_success_and_malformed_result_are_closed() -> None:
    async def scenario() -> None:
        class Transport:
            async def invoke(self, request):
                return V3TransportResponse(request.request_id, V3WriterGatewayResult(
                    FIXTURE["gateway_success"]["result_marker"], FIXTURE["gateway_success"]["output"],
                ))

        class MalformedTransport:
            async def invoke(self, request):
                return V3TransportResponse(request.request_id, FIXTURE["malformed"])

        assert (await V3WriterAdapter(transport=Transport()).write(_brief())).ok is True
        malformed = await V3WriterAdapter(transport=MalformedTransport()).write(_brief())
        assert (malformed.ok, malformed.errors) == (False, ("writer_invalid_output",))
    asyncio.run(scenario())


def test_writer_gateway_contract_timeout_pii_credentials_and_identity_mismatch_fail_closed() -> None:
    async def scenario() -> None:
        class SlowTransport:
            async def invoke(self, request):
                await asyncio.sleep(0.02)
                return V3TransportResponse(request.request_id, FIXTURE["gateway_success"]["output"])

        class FixtureTransport:
            def __init__(self, payload, *, mismatched: bool = False) -> None:
                self.payload, self.mismatched = payload, mismatched

            async def invoke(self, request):
                request_id = "550e8400-e29b-41d4-a716-446655440099" if self.mismatched else request.request_id
                return V3TransportResponse(request_id, self.payload)

        timed_out = await V3WriterAdapter(transport=SlowTransport(), timeout_seconds=0.001).write(_brief())
        mismatched = await V3WriterAdapter(transport=FixtureTransport(FIXTURE["gateway_success"]["output"], mismatched=True)).write(_brief())
        pii = await V3WriterAdapter(transport=FixtureTransport(FIXTURE["pii"])).write(_brief())
        credential = await V3WriterAdapter(transport=FixtureTransport(FIXTURE["credential"])).write(_brief())
        assert timed_out.errors == ("writer_timeout",)
        assert mismatched.errors == ("writer_invalid_output",)
        for result in (pii, credential):
            assert result.errors == ("writer_invalid_output",)
            assert "private" not in result.public_text.casefold()
    asyncio.run(scenario())


def test_writer_gateway_result_rejects_missing_or_mismatched_marker() -> None:
    for envelope in (FIXTURE["gateway_missing_marker"], FIXTURE["gateway_mismatched_marker"]):
        try:
            V3WriterGatewayResult(envelope.get("result_marker"), envelope["output"])
        except Exception as exc:
            assert str(exc) == "invalid_v3_writer_gateway_result_marker"
        else:
            raise AssertionError("unmarked writer result must be rejected")


def test_writer_adapter_roundtrips_only_the_marked_envelope_and_rejects_bare_output() -> None:
    async def scenario() -> None:
        class Writer:
            async def write(self, _request):
                return json.dumps(FIXTURE["gateway_success"], ensure_ascii=False)

        class BareWriter:
            async def write(self, _request):
                return FIXTURE["bare_output"]

        assert (await V3WriterAdapter(Writer()).write(_brief())).ok is True
        rejected = await V3WriterAdapter(BareWriter()).write(_brief())
        assert rejected.errors == ("writer_invalid_output",)

    asyncio.run(scenario())
