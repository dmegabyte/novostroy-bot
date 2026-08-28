from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from nmbot_core import GatewayResult, create_app


class Port:
    def __init__(self, *results): self.results = list(results)
    async def run(self, payload, *, repair=False): return GatewayResult(self.results.pop(0), "attempt-1")


def test_core_api_health_start_chat_and_jivo_journal(tmp_path):
    p1 = Port({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}, "ambiguity": None}, {"action": "continue", "facts": [], "near": [], "missing": [], "params": {}, "ambiguity": None})
    p2 = Port({"action": "reply", "response": "Ответ.", "final_question": ""}, {"action": "reply", "response": "Ответ Jivo.", "final_question": ""})
    app = create_app(prompt1=p1, prompt2=p2, state_path=tmp_path / "state.json", outbox_path=tmp_path / "outbox", journal_path=tmp_path / "journal.jsonl", release_id="v6-canonical-r1")
    async def run():
        async with TestClient(TestServer(app)) as client:
            assert (await (await client.get("/health")).json())["runtime"] == "V6"
            assert (await (await client.post("/api/chat", json={"user_id": "a", "message": "/start"})).json())["ok"] is True
            assert (await (await client.post("/api/chat", json={"user_id": "a", "message": "двушка"})).json())["answer"] == "Ответ."
            response = await client.post("/jivo/provider", json={"event": "CLIENT_MESSAGE", "id": "e1", "site_id": "s", "chat_id": "c", "client_id": "u", "message": {"text": "студия"}})
            assert (await response.json())["event"] == "BOT_MESSAGE"
    asyncio.run(run())
    rendered = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
    assert "студия" not in rendered and len(rendered.splitlines()) == 2
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert set(state["a"]) == {"core"}


def test_core_api_requires_configured_tokens_and_uses_file_session_locks(tmp_path):
    p1 = Port({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}, "ambiguity": None})
    p2 = Port({"action": "reply", "response": "Ответ.", "final_question": ""})
    app = create_app(prompt1=p1, prompt2=p2, state_path=tmp_path / "state.json", outbox_path=tmp_path / "outbox", journal_path=tmp_path / "journal.jsonl", profile="PROD", api_token="api-token", provider_token="provider", bridge_token="bridge")
    async def run():
        async with TestClient(TestServer(app)) as client:
            assert (await (await client.post("/api/chat", json={"message": "двушка"})).json())["error"] == "unauthorized"
            assert (await (await client.post("/api/reset", headers={"Authorization": "Bearer api-token"}, json={"user_id": "a"})).json())["ok"] is True
            assert (await (await client.post("/jivo/provider", headers={"X-NMBOT-Bridge-Token": "wrong"}, json={"event": "CLIENT_MESSAGE"})).json())["error"] == "unauthorized"
            assert (await (await client.post("/jivo/provider", headers={"X-NMBOT-Bridge-Token": "bridge"}, json={"event": "IGNORE"})).json())["ignored"] is True
    asyncio.run(run())
