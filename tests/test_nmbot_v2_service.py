from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from nmbot_runtime_contract.wire import CONTRACT_VERSION
from nmbot_v2.contracts import SafeTurnContext, SearchResult, SemanticPlan
from nmbot_v2.composition import build_turn_processor
from nmbot_v2.planner_gateway_contract import (
    V2_PLANNER_GATEWAY_CONTRACT,
    require_v2_planner_gateway_contract,
)
from nmbot_v2.ports import V2RuntimePorts
from nmbot_v2.service import build_turn, create_app
from nmbot_v2.state import ConversationState


def request(message: str = "подбери квартиру") -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "runtime_version": "V2",
        "conversation_ref": "conversation:123",
        "trace_ref": "trace:12345678",
        "message": message,
        "channel": "test",
        "meta": {},
    }


class Planner:
    def plan(self, context: SafeTurnContext, _state: ConversationState) -> SemanticPlan:
        assert context.conversation_ref == "conversation:123"
        return SemanticPlan(operation="search")


class Search:
    def search(self, _plan, _state, _context=None) -> SearchResult:
        return SearchResult.from_dict({"facts": [{"name": "ЖК Тест", "price": "от 10 млн рублей"}]})

    def enrich_selected(self, option, _state, _plan):
        return option

    def enrich_pair(self, _turn, _state):
        return None


def test_v2_service_import_closure_is_worker_only() -> None:
    for relative in ("nmbot_v2/ports.py", "nmbot_v2/composition.py", "nmbot_v2/service.py", "scripts/nmbot_v2_service.py"):
        source = Path(relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        imported += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        banned = ("nmbot_v0", "nmbot_v1", "nmbot_v3", "nmbot_v4", "nmbot_runtime_adapter", "nmbot_api_server")
        assert not any(name == blocked or name.startswith(blocked + ".") for name in imported for blocked in banned), relative


def test_v2_composition_factory_preserves_injected_port_identity() -> None:
    planner, search = Planner(), Search()
    composer = object()
    rewriter = object()

    processor = build_turn_processor(
        V2RuntimePorts(
            planner=planner,
            search_service=search,
            response_composer=composer,
            manager_rewriter=rewriter,
        ),
        response_composer_mode="shadow",
        manager_rewriter_mode="publish",
    )

    assert processor.planner is planner
    assert processor.search_service is search
    assert processor.response_composer is composer
    assert processor.manager_rewriter is rewriter
    assert processor.response_composer_mode == "shadow"
    assert processor.manager_rewriter_mode == "publish"


def test_v2_missing_planner_fails_closed_without_state_mutation() -> None:
    async def scenario() -> None:
        initial = ConversationState(params={"rooms": 2}).to_dict()
        result = await build_turn()(request(), initial)
        assert result.state is None
        assert result.response["error_code"] == "missing_v2_planner_port"
        assert result.response["runtime_version"] == "V2"
    asyncio.run(scenario())


def test_v2_missing_search_port_fails_closed_without_state_mutation() -> None:
    async def scenario() -> None:
        initial = ConversationState(params={"rooms": 2}).to_dict()
        result = await build_turn(planner_port=Planner())(request(), initial)
        assert result.state is None
        assert result.response["error_code"] == "search_service_missing"
        assert result.response["runtime_version"] == "V2"
    asyncio.run(scenario())


def test_v2_http_uses_direct_v2_state_journal_and_closed_wire(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_path, journal_path = tmp_path / "v2-state.json", tmp_path / "v2-journal.jsonl"
        client = TestClient(TestServer(create_app(
            state_path=state_path,
            journal_path=journal_path,
            token="v2-token",
            release_identity="v2-test-immutable",
            planner_port=Planner(),
            search_port=Search(),
        )))
        await client.start_server()
        try:
            headers = {"Authorization": "Bearer v2-token"}
            assert await (await client.get("/health")).json() == {
                "ok": True, "runtime_version": "V2", "release_identity": "v2-test-immutable",
            }
            denied = await client.post("/api/chat", json=request())
            assert denied.status == 401
            wrong = request(); wrong["runtime_version"] = "V3"
            assert (await client.post("/api/chat", json=wrong, headers=headers)).status == 400
            accepted = await client.post("/api/chat", json=request("ищу SECRET"), headers=headers)
            body = await accepted.json()
            assert accepted.status == 200 and body["ok"] is True and body["runtime_version"] == "V2"
            stored = json.loads(state_path.read_text(encoding="utf-8"))
            assert set(stored) == {"conversation:123"}
            assert "nmbot_v" not in json.dumps(stored)
            journal = journal_path.read_text(encoding="utf-8")
            assert "SECRET" not in journal and '"runtime_version":"V2"' in journal
            reset = await client.post("/api/reset", json={key: value for key, value in request().items() if key not in {"message", "channel", "meta"}}, headers=headers)
            assert (await reset.json())["reset"] is True
            assert json.loads(state_path.read_text(encoding="utf-8"))["conversation:123"] == ConversationState().to_dict()
        finally:
            await client.close()
    asyncio.run(scenario())


def test_v2_entrypoint_delegates_to_the_outer_host(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("nmbot_v2_entrypoint_test", Path("scripts/nmbot_v2_service.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import scripts.nmbot_v2_host as host
    called = []
    monkeypatch.setattr(host, "main", lambda: called.append(True))
    module.main()
    assert called == [True]
    assert not hasattr(module, "create_app")


def test_v2_planner_gateway_contract_is_proven() -> None:
    assert V2_PLANNER_GATEWAY_CONTRACT.proven is True
    assert require_v2_planner_gateway_contract() is V2_PLANNER_GATEWAY_CONTRACT
