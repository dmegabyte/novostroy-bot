import asyncio
import json
import multiprocessing
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

import pytest

from nmbot_v6.simple_state import SimpleState
from scripts.nmbot_api_server import JsonStateStore, create_app
from scripts import nmbot_v6_api as api_module


def _save_state_process(path: str, start) -> None:
    async def update() -> None:
        store = JsonStateStore(Path(path))
        async with store.session_lock("shared-user"):
            current = await store.get("shared-user")
            await asyncio.sleep(0.1)
            await store.save("shared-user", {"revision": int(current.get("revision") or 0) + 1})

    start.wait(5)
    asyncio.run(update())


def test_api_registers_only_v6_routes():
    app = create_app()
    routes = {str(resource) for resource in app.router.resources()}
    assert any("/health" in route for route in routes)
    assert any("/api/chat" in route for route in routes)
    assert any("/jivo/{provider_token}" in route for route in routes)


def test_health_and_runtime_are_v6_only(monkeypatch):
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "TEST")
    async def run():
        async with TestClient(TestServer(create_app())) as client:
            health = await client.get("/health")
            version = await client.get("/api/runtime-version")
            assert health.status == 200
            assert (await health.json())["runtime"] == "V6"
            assert (await health.json())["profile"] == "TEST"
            assert (await version.json())["runtime_version"] == "V6"

    asyncio.run(run())


def test_health_reports_exact_release_identity(monkeypatch, tmp_path):
    identity = tmp_path / "identity.json"
    identity.write_text(
        json.dumps({"schema": "nmbot.release_identity.v1", "release_id": "v6-r42"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NMBOT_RELEASE_IDENTITY_FILE", str(identity))

    async def run():
        async with TestClient(TestServer(create_app())) as client:
            response = await client.get("/health")
            assert response.status == 200
            assert (await response.json())["release_id"] == "v6-r42"

    asyncio.run(run())


def test_runtime_version_rejects_any_non_v6_selector():
    foreign_runtime = "V" + str(6 - 1)
    async def run():
        async with TestClient(TestServer(create_app())) as client:
            response = await client.post("/api/runtime-version", json={"runtime_version": foreign_runtime})
            assert response.status == 400
            assert (await response.json())["error"] == "only_v6_supported"

    asyncio.run(run())


def test_start_and_reset_persist_valid_v6_state(monkeypatch, tmp_path):
    monkeypatch.setenv("NMBOT_DIALOGUE_JOURNAL", str(tmp_path / "dialogue.jsonl"))

    async def run():
        app = create_app()
        async with TestClient(TestServer(app)) as client:
            response = await client.post("/api/reset", json={"user_id": "v6-test"})
            assert response.status == 200
            assert (await response.json())["runtime_version"] == "V6"
            api_start = await client.post("/api/chat", json={"user_id": "api-start", "message": "/start"})
            assert api_start.status == 200
            jivo_start = await client.post(
                "/jivo/provider",
                json={
                    "event": "CLIENT_MESSAGE",
                    "id": "event-start",
                    "site_id": "site",
                    "chat_id": "chat",
                    "client_id": "client",
                    "message": {"text": "/start"},
                },
            )
            assert jivo_start.status == 200

            for user_id in ("v6-test", "api-start", "jivo:site:chat:client"):
                envelope = await app["state_store"].get(user_id)
                assert SimpleState.from_mapping(envelope["nmbot_v6"]) == SimpleState()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("profile", "expected_prefix"),
    [("TEST", "[TEST] "), ("PROD", "Здравствуйте!")],
)
def test_profile_controls_only_greeting(monkeypatch, profile, expected_prefix):
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", profile)
    if profile == "PROD":
        monkeypatch.setenv("NMBOT_API_TOKEN", "test-api-token")

    async def run():
        async with TestClient(TestServer(create_app())) as client:
            headers = {"Authorization": "Bearer test-api-token"} if profile == "PROD" else {}
            response = await client.post("/api/chat", json={"user_id": "profile", "message": "/start"}, headers=headers)
            payload = await response.json()
            assert response.status == 200
            assert payload["answer"].startswith(expected_prefix)
            assert payload["meta"]["profile"] == profile

    asyncio.run(run())


def test_invalid_profile_fails_closed(monkeypatch):
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_" + "production")
    with pytest.raises(RuntimeError, match="exactly TEST or PROD"):
        create_app()


def test_prod_routes_fail_closed_when_tokens_are_missing(monkeypatch):
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "PROD")
    for key in ("NMBOT_API_TOKEN", "NMBOT_N8N_BRIDGE_TOKEN", "JIVO_PROVIDER_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    async def run():
        async with TestClient(TestServer(create_app())) as client:
            chat = await client.post("/api/chat", json={"user_id": "prod", "message": "/start"})
            jivo = await client.post("/jivo/provider", json={"event": "CLIENT_MESSAGE"})
            assert chat.status == 401
            assert jivo.status == 401

    asyncio.run(run())


def test_state_store_preserves_concurrent_process_updates(tmp_path):
    path = tmp_path / "state.json"
    context = multiprocessing.get_context("fork")
    start = context.Event()
    workers = [
        context.Process(target=_save_state_process, args=(str(path), start)),
        context.Process(target=_save_state_process, args=(str(path), start)),
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    assert json.loads(path.read_text(encoding="utf-8")) == {"shared-user": {"revision": 2}}


def test_reset_waits_for_inflight_turn_and_remains_final(monkeypatch):
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "TEST")
    started = asyncio.Event()
    finish = asyncio.Event()

    async def slow_turn(app, *, user_id, message, channel, meta):
        started.set()
        await finish.wait()
        await app["state_store"].save(user_id, {"nmbot_v6": {"turn": "stale"}})
        return {"ok": True, "answer": "done"}

    monkeypatch.setattr(api_module, "run_v6_simple_turn", slow_turn)

    async def run():
        app = create_app()
        async with TestClient(TestServer(app)) as client:
            turn = asyncio.create_task(api_module.run_chat(app, user_id="same-user", message="question", channel="api"))
            await started.wait()
            reset = asyncio.create_task(client.post("/api/reset", json={"user_id": "same-user"}))
            await asyncio.sleep(0.05)
            assert not reset.done()
            finish.set()
            await turn
            response = await reset
            assert response.status == 200
            envelope = await app["state_store"].get("same-user")
            assert SimpleState.from_mapping(envelope["nmbot_v6"]) == SimpleState()

    asyncio.run(run())
