from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from nmbot_core import GatewayResult, create_app, create_app_from_environment


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
    journal_rows = [json.loads(line) for line in rendered.splitlines()]
    diagnostic = journal_rows[-1]["runtime_diagnostic"]
    assert diagnostic["status"] == "completed" and diagnostic["state_commit"] is True
    assert {item["stage"] for item in diagnostic["trace"]["stages"]} == {"prompt1", "mcp", "prompt2", "state", "bot_message"}
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


def test_environment_factory_requires_existing_release_contract(tmp_path, monkeypatch):
    root = tmp_path / "release"; (root / "prompts").mkdir(parents=True)
    (root / "prompts" / "v6_simple_search_agent.txt").write_text("prompt1", encoding="utf-8")
    (root / "prompts" / "v6_simple_answer_writer.txt").write_text("prompt2", encoding="utf-8")
    identity = root / "identity.json"; identity.write_text('{"schema":"nmbot.release_identity.v1","release_id":"v6-canonical-r1"}', encoding="utf-8")
    monkeypatch.setenv("OVERMIND_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("NMBOT_API_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("NMBOT_CALLBACK_OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("NMBOT_RELEASE_IDENTITY_FILE", str(identity))
    app = create_app_from_environment(root)
    assert app["release_id"] == "v6-canonical-r1" and app["profile"] == "TEST"
