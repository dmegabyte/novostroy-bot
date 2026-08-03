from __future__ import annotations

import asyncio
import ast
from pathlib import Path

from nmbot_v2.contracts import SearchResult
from nmbot_v2.ports import V2SearchAdapterPort
from nmbot_v2.search_adapter import (
    V2InjectedSearchAdapter,
    V2SearchAdapterErrorCode,
    build_injected_v2_search_adapter,
    build_v2_search_provider_request,
)
from nmbot_v2.search_contract import V2SearchRequest


def _request() -> V2SearchRequest:
    return V2SearchRequest(
        search_goal={"entity_type": "new_building_flat", "query_summary": "двушка до 18 млн", "explicit_terms": ["двушка"]},
        requested_hard={"rooms": [2]},
        effective_hard={"rooms": [2]},
        available_fact_fields=["id", "name", "rooms", "location"],
    )


def _output() -> dict[str, object]:
    return {
        "facts": [{"id": "one", "name": "ЖК Один", "rooms": [2], "location": "Москва"}],
        "near": [],
        "missing": [],
        "params": {"rooms": [2]},
        "diagnostics": {},
    }


def test_injected_v2_search_adapter_builds_typed_query_and_returns_typed_result() -> None:
    async def scenario() -> None:
        seen = []

        async def invoke(provider_request):
            seen.append(provider_request)
            return _output()

        adapter = build_injected_v2_search_adapter(invoke)
        result = await adapter.search(_request())

        assert result.ok
        assert isinstance(result.result, SearchResult)
        assert tuple(card.name for card in result.result.facts) == ("ЖК Один",)
        assert len(seen) == 1
        assert seen[0].request == _request()
        assert "SEARCH_CONTRACT_ENVELOPE=" in seen[0].query
        assert seen[0].payload == _request().to_payload()
        seen[0].payload["search_goal"]["query_summary"] = "changed by provider"
        assert _request().search_goal["query_summary"] == "двушка до 18 млн"

    asyncio.run(scenario())


def test_v2_search_adapter_fails_closed_and_redacts_provider_failures() -> None:
    async def scenario() -> None:
        async def unavailable(_provider_request):
            raise RuntimeError("Authorization: Bearer top-secret-token")

        async def timed_out(_provider_request):
            raise TimeoutError("raw gateway secret")

        async def malformed(_provider_request):
            return {"facts": [{"name": "ЖК", "secret": "must-not-leak"}]}

        for invoke, code, retryable in (
            (unavailable, V2SearchAdapterErrorCode.UNAVAILABLE, True),
            (timed_out, V2SearchAdapterErrorCode.TIMEOUT, True),
            (malformed, V2SearchAdapterErrorCode.INVALID_RESPONSE, False),
        ):
            result = await V2InjectedSearchAdapter(invoke).search(_request())
            assert not result.ok
            assert result.result is None
            assert result.error is not None
            assert (result.error.code, result.error.retryable) == (code, retryable)
            rendered = repr(result).casefold()
            assert "secret" not in rendered
            assert "authorization" not in rendered

    asyncio.run(scenario())


def test_v2_search_adapter_factory_port_and_import_closure_are_v2_local() -> None:
    assert isinstance(build_injected_v2_search_adapter(lambda _request: None), V2InjectedSearchAdapter)
    assert getattr(V2SearchAdapterPort.search, "__annotations__")["return"] == "V2SearchAdapterResult"
    request = build_v2_search_provider_request(_request())
    assert request.payload["count"] == 3

    tree = ast.parse(Path("nmbot_v2/search_adapter.py").read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imports += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = ("aiohttp", "requests", "http", "socket", "gateway", "mcp", "scripts", "nmbot_v0", "nmbot_v1", "nmbot_v3", "nmbot_v4")
    assert not any(name == blocked or name.startswith(blocked + ".") for name in imports for blocked in banned)
