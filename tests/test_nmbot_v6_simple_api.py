import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

import pytest

from scripts.nmbot_api_server import create_app


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


def test_api_reset_uses_v6_namespace():
    async def run():
        async with TestClient(TestServer(create_app())) as client:
            response = await client.post("/api/reset", json={"user_id": "v6-test"})
            assert response.status == 200
            assert (await response.json())["runtime_version"] == "V6"

    asyncio.run(run())


@pytest.mark.parametrize(
    ("profile", "expected_prefix"),
    [("TEST", "[TEST] "), ("PROD", "Здравствуйте!")],
)
def test_profile_controls_only_greeting(monkeypatch, profile, expected_prefix):
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", profile)

    async def run():
        async with TestClient(TestServer(create_app())) as client:
            response = await client.post("/api/chat", json={"user_id": "profile", "message": "/start"})
            payload = await response.json()
            assert response.status == 200
            assert payload["answer"].startswith(expected_prefix)
            assert payload["meta"]["profile"] == profile

    asyncio.run(run())


def test_invalid_profile_fails_closed(monkeypatch):
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_" + "production")
    with pytest.raises(RuntimeError, match="exactly TEST or PROD"):
        create_app()
