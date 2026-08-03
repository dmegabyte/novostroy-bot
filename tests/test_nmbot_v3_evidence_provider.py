from __future__ import annotations

import asyncio
import ast
from pathlib import Path

from nmbot_v3.evidence_contract import EvidenceRequest, EvidenceResult
from nmbot_v3.evidence_provider import (
    V3_EVIDENCE_PROMPT,
    V3InjectedEvidenceSearchAdapter,
    build_v3_evidence_provider_request,
    v3_evidence_response_schema,
)
from nmbot_v3.ports import V3EvidenceSearchPort, V3ProviderError, V3ProviderErrorCode


def _request() -> EvidenceRequest:
    return EvidenceRequest(
        mode="current_options_fact_check",
        requested_facts=("metro", "price_min"),
        hard_constraints={"rooms": [2]},
        current_option_refs=("550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b-41d4-a716-446655440001"),
        count=2,
    )


def _result(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "facts": [{"name": "ЖК Лучи", "canonical_ref": "550e8400-e29b-41d4-a716-446655440000", "fields": {"metro": "Солнцево", "rooms": [2]}, "is_near": False, "differences": []}],
        "near": [{"name": "ЖК Румянцево", "canonical_ref": "550e8400-e29b-41d4-a716-446655440001", "fields": {}, "is_near": True, "differences": ["price_min"]}],
        "missing_facts": [],
    }
    value.update(overrides)
    return value


def test_injected_evidence_adapter_builds_v3_request_and_returns_canonical_result() -> None:
    async def scenario() -> None:
        seen = []

        async def invoke(provider_request):
            seen.append(provider_request)
            return _result()

        result = await V3InjectedEvidenceSearchAdapter(invoke).search(_request())
        assert isinstance(result, EvidenceResult)
        assert tuple(card.name for card in result.facts) == ("ЖК Лучи",)
        assert result.near[0].differences == ("price_min",)
        assert len(seen) == 1
        assert seen[0].prompt == V3_EVIDENCE_PROMPT
        assert seen[0].payload == {
            "mode": "current_options_fact_check",
            "requested_facts": ("metro", "price_min"),
            "hard_constraints": {"rooms": (2,)},
            "exact_name": None,
            "current_option_refs": ("550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b-41d4-a716-446655440001"),
            "excluded_names": (),
            "count": 2,
        }
        seen[0].payload["hard_constraints"]["rooms"] = (3,)
        assert _request().hard_constraints["rooms"] == (2,)
    asyncio.run(scenario())


def test_evidence_adapter_rejects_invalid_json_and_exact_name_order_or_fact_near_violations() -> None:
    async def scenario() -> None:
        async def invalid_json(_provider_request):
            return "```json\n{}\n```"

        async def wrong_order(_provider_request):
            return _result(facts=[{"name": "ЖК Румянцево", "fields": {}, "is_near": False, "differences": []}, {"name": "ЖК Лучи", "fields": {}, "is_near": False, "differences": []}], near=[])

        async def wrong_exact_name(_provider_request):
            return _result(facts=[{"name": "ЖК Другой", "fields": {}, "is_near": False, "differences": []}], near=[])

        async def fact_is_near(_provider_request):
            return _result(facts=[{"name": "ЖК Лучи", "fields": {}, "is_near": True, "differences": ["price_min"]}], near=[])

        named_request = EvidenceRequest(mode="named_object", exact_name="ЖК Лучи", count=1)
        named_result = await V3InjectedEvidenceSearchAdapter(wrong_exact_name).search(named_request)
        assert isinstance(named_result, V3ProviderError)
        assert (named_result.code, named_result.retryable) == (V3ProviderErrorCode.INVALID_RESPONSE, False)

        for invoke in (invalid_json, wrong_order, fact_is_near):
            result = await V3InjectedEvidenceSearchAdapter(invoke).search(_request())
            assert isinstance(result, V3ProviderError)
            assert (result.code, result.retryable) == (V3ProviderErrorCode.INVALID_RESPONSE, False)
    asyncio.run(scenario())


def test_evidence_adapter_normalizes_missing_facts_in_request_order() -> None:
    async def scenario() -> None:
        async def invoke(_provider_request):
            return _result(
                facts=[{"name": "ЖК Лучи", "canonical_ref": "550e8400-e29b-41d4-a716-446655440000", "fields": {"metro": "Солнцево", "rooms": [2]}, "is_near": False, "differences": []}],
                near=[],
                missing_facts=[],
            )

        result = await V3InjectedEvidenceSearchAdapter(invoke).search(_request())
        assert isinstance(result, EvidenceResult)
        assert result.missing_facts == ("price_min",)
    asyncio.run(scenario())


def test_evidence_adapter_rejects_unsafe_availability_and_never_promotes_ads_count() -> None:
    async def scenario() -> None:
        request = EvidenceRequest(
            mode="current_options_fact_check",
            requested_facts=("apartment_inventory",),
            current_option_refs=("550e8400-e29b-41d4-a716-446655440000",),
            count=1,
        )

        async def ads_only(_provider_request):
            return {
                "facts": [{"name": "ЖК Лучи", "canonical_ref": "550e8400-e29b-41d4-a716-446655440000", "fields": {"ads_count": 99}, "is_near": False, "differences": []}],
                "near": [],
                "missing_facts": [],
            }

        async def private_inventory(_provider_request):
            return {
                "facts": [{"name": "ЖК Лучи", "canonical_ref": "550e8400-e29b-41d4-a716-446655440000", "fields": {"apartment_inventory": "+7 999 123-45-67"}, "is_near": False, "differences": []}],
                "near": [],
                "missing_facts": [],
            }

        normalized = await V3InjectedEvidenceSearchAdapter(ads_only).search(request)
        rejected = await V3InjectedEvidenceSearchAdapter(private_inventory).search(request)
        assert isinstance(normalized, EvidenceResult)
        assert normalized.missing_facts == ("apartment_inventory",)
        assert isinstance(rejected, V3ProviderError)
        assert (rejected.code, rejected.retryable) == (V3ProviderErrorCode.INVALID_RESPONSE, False)
        assert "999" not in repr(rejected)
    asyncio.run(scenario())


def test_evidence_adapter_fails_closed_and_does_not_expose_provider_secrets() -> None:
    async def scenario() -> None:
        async def unavailable(_provider_request):
            raise RuntimeError("Authorization: Bearer top-secret-token")

        async def timed_out(_provider_request):
            raise TimeoutError("secret gateway detail")

        for invoke, code in ((unavailable, V3ProviderErrorCode.UNAVAILABLE), (timed_out, V3ProviderErrorCode.TIMEOUT)):
            result = await V3InjectedEvidenceSearchAdapter(invoke).search(_request())
            assert isinstance(result, V3ProviderError)
            assert (result.code, result.retryable) == (code, True)
            assert "secret" not in repr(result).casefold()
            assert "authorization" not in repr(result).casefold()
    asyncio.run(scenario())


def test_evidence_provider_port_schema_and_import_closure_are_v3_local() -> None:
    assert getattr(V3EvidenceSearchPort.search, "__annotations__")["return"] == "V3EvidenceSearchPortResult"
    first = v3_evidence_response_schema()
    first["properties"]["facts"]["maxItems"] = 99
    assert v3_evidence_response_schema()["properties"]["facts"]["maxItems"] == 3
    assert build_v3_evidence_provider_request(_request()).payload["count"] == 2

    tree = ast.parse(Path("nmbot_v3/evidence_provider.py").read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imports += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = ("aiohttp", "requests", "http", "socket", "gateway", "mcp", "scripts", "nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4")
    assert not any(name == blocked or name.startswith(blocked + ".") for name in imports for blocked in banned)
