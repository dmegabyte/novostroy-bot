from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from nmbot_runtime_contract.wire import CONTRACT_VERSION
from nmbot_v0.service import build_turn, create_app


def _payload(message: str = "Подбери квартиру") -> dict[str, str]:
    return {"contract_version": CONTRACT_VERSION, "runtime_version": "V0", "conversation_ref": "conversation-0001", "trace_ref": "trace-0001", "message": message, "channel": "test"}


def _scenario(_context):
    return {"decision": {"action": "search", "viewpoint": "life", "params": {}}, "search": {"facts": [{"name": "ЖК Тест", "location": "Москва", "min_price": 10_000_000}], "near": [], "missing": [], "params": {}}}


def test_v0_service_success_persists_direct_v0_state_and_reset(tmp_path: Path) -> None:
    async def run() -> None:
        app = create_app(state_path=tmp_path / "v0-state.json", journal_path=tmp_path / "v0.jsonl", token="token", release_identity="v0-test-20260801", scenario_port=_scenario)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post("/api/chat", json=_payload(), headers={"Authorization": "Bearer token"})
            body = await response.json()
            assert response.status == 200 and body["ok"] is True and body["runtime_version"] == "V0"
            raw_state = (tmp_path / "v0-state.json").read_text(encoding="utf-8")
            assert '"visible_options"' in raw_state and '"nmbot_v' not in raw_state
            reset = await client.post("/api/reset", json={key: value for key, value in _payload().items() if key not in {"message", "channel"}}, headers={"Authorization": "Bearer token"})
            assert (await reset.json())["reset"] is True
            assert '"visible_options":[]' in (tmp_path / "v0-state.json").read_text(encoding="utf-8")
            journal = (tmp_path / "v0.jsonl").read_text(encoding="utf-8")
            assert "conversation-0001" not in journal and '"runtime_version":"V0"' in journal
        finally:
            await client.close()
    asyncio.run(run())


def test_v0_service_missing_gateway_and_phone_fail_closed_without_state_mutation() -> None:
    async def run() -> None:
        missing = await build_turn()( _payload(), None)
        phone = await build_turn(scenario_port=_scenario)(_payload("Мой номер +79991234567"), None)
        assert missing.response["error_code"] == "missing_v0_scenario_gateway" and missing.state is None
        assert phone.response["error_code"] == "v0_phone_flow_unmigrated" and phone.state is None
    asyncio.run(run())


def test_v0_service_total_deadline_and_router_auth(tmp_path: Path) -> None:
    async def slow(_context):
        await asyncio.sleep(0.05)
        return _scenario(_context)

    async def run() -> None:
        app = create_app(state_path=tmp_path / "state.json", journal_path=tmp_path / "journal.jsonl", token="token", release_identity="v0-test-20260801", scenario_port=slow, total_timeout_seconds=0.01)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            denied = await client.post("/api/chat", json=_payload())
            assert denied.status == 401
            timed = await client.post("/api/chat", json=_payload(), headers={"Authorization": "Bearer token"})
            assert (await timed.json())["error_code"] == "v0_scenario_timeout"
            assert not (tmp_path / "state.json").exists()
        finally:
            await client.close()
    asyncio.run(run())


def test_v0_static_import_closure_has_no_other_runtime() -> None:
    root = Path(__file__).resolve().parents[1] / "nmbot_v0"
    forbidden = ("nmbot_v1", "nmbot_v2", "nmbot_v3", "scripts.nmbot_runtime_adapter", "scripts.nmbot_api_server")
    assert all(not any(token in path.read_text(encoding="utf-8") for token in forbidden) for path in root.glob("*.py"))
