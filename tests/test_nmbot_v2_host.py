from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from nmbot_runtime_contract.wire import CONTRACT_VERSION
from nmbot_v2.gateway import V2GatewayResult
from scripts.nmbot_v2_host import (
    V2HostConfigurationError,
    create_v2_host_app_from_environ,
    settings_from_environ,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeSession:
    def __init__(self) -> None:
        self.closed = False
        self.requests: list[tuple[str, tuple, dict]] = []
        self._responses = iter((
            _Response(201, {"id": "task-1"}),
            _Response(200, {"status": "completed"}),
            _Response(200, {"result": {"response": json.dumps(_planner_result())}}),
            _Response(201, {"id": "task-2"}),
            _Response(200, {"status": "completed"}),
            _Response(200, {"result": {"response": json.dumps(_search_result())}}),
        ))

    def post(self, *args, **kwargs):
        self.requests.append(("post", args, kwargs))
        return _RequestContext(next(self._responses))

    def get(self, *args, **kwargs):
        self.requests.append(("get", args, kwargs))
        return _RequestContext(next(self._responses))

    async def close(self) -> None:
        self.closed = True


class _Response:
    def __init__(self, status: int, payload: dict) -> None:
        self.status, self._payload = status, payload

    async def json(self, **_kwargs):
        return self._payload


class _RequestContext:
    def __init__(self, response: _Response) -> None:
        self.response = response

    async def __aenter__(self) -> _Response:
        return self.response

    async def __aexit__(self, *_args) -> None:
        return None


def _environ(tmp_path: Path) -> dict[str, str]:
    return {
        "NMBOT_V2_INTERNAL_TOKEN": "worker-test-token",
        "NMBOT_V2_PORT": "18082",
        "NMBOT_V2_STATE_PATH": str(tmp_path / "state.json"),
        "NMBOT_V2_JOURNAL_PATH": str(tmp_path / "journal.jsonl"),
        "NMBOT_V2_RELEASE_ID": "v2-host-test-immutable",
        "NMBOT_V2_GATEWAY_URL": "https://gateway.invalid",
        "NMBOT_V2_GATEWAY_TOKEN_ENV": "V2_TEST_GATEWAY_TOKEN",
        "V2_TEST_GATEWAY_TOKEN": "gateway-secret-not-for-output",
        "NMBOT_V2_GATEWAY_REQUEST_TIMEOUT_SECONDS": "12",
        "NMBOT_V2_GATEWAY_POLL_INTERVAL_SECONDS": "1",
        "NMBOT_V2_PLANNER_MODEL": "v2-planner-test",
        "NMBOT_V2_PLANNER_TIMEOUT_SECONDS": "8",
        "NMBOT_V2_RESPONSE_COMPOSER_MODE": "off",
        "NMBOT_V2_MANAGER_REWRITER_MODE": "off",
    }


def _planner_result() -> dict:
    return {
        "marker": "nmbot.v2.semantic-planner.gateway.v1", "schema_version": 1,
        "user_goal": "Подобрать квартиру", "refers_to_existing_objects": False,
        "requests_new_objects": True, "selected_reference": None,
        "named_object_reference": None, "requested_comparison": [], "scenario_needs": [],
        "response_viewpoint": "unchanged", "scenario_change": None, "constraints_delta": {},
        "requires_enrichment": False, "resolved_subject": None, "resolved_intent": None,
        "requested_facts": [], "facts_needed": [], "focus_action": "keep",
        "domain_relation": "in_domain", "clarification": None, "confidence": 0.9,
        "reason": "search",
    }


def _search_result() -> dict:
    return {
        "facts": [{"name": "ЖК Тест", "location": "Москва", "min_price": 12_000_000}],
        "near": [], "missing": [], "params": {},
        "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "response_viewpoint": "unchanged", "base_viewpoint": "unchanged", "requested_field_priorities": [], "relaxation_audit": [], "ignored_preferences": [], "notes": []},
    }


def _request() -> dict[str, object]:
    return {"contract_version": CONTRACT_VERSION, "runtime_version": "V2", "conversation_ref": "loopback:1", "trace_ref": "trace:loopback", "message": "подбери квартиру", "channel": "test", "meta": {}}


def test_host_composes_loopback_app_with_fake_session_and_cleans_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        session = FakeSession()
        app = create_v2_host_app_from_environ(environ=_environ(tmp_path), session=session)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post("/api/chat", json=_request(), headers={"Authorization": "Bearer worker-test-token"})
            body = await response.json()
            assert response.status == 200 and body["ok"] is True
            assert len(session.requests) == 6
            assert "gateway-secret-not-for-output" not in json.dumps(body)
            assert "gateway-secret-not-for-output" not in (tmp_path / "state.json").read_text()
            assert "gateway-secret-not-for-output" not in (tmp_path / "journal.jsonl").read_text()
        finally:
            await client.close()
        assert session.closed is True
    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("change", "error"),
    (
        (("NMBOT_V2_GATEWAY_URL", ""), "missing_v2_config:NMBOT_V2_GATEWAY_URL"),
        (("NMBOT_V2_GATEWAY_TOKEN_ENV", ""), "missing_v2_config:NMBOT_V2_GATEWAY_TOKEN_ENV"),
        (("NMBOT_V2_GATEWAY_TOKEN_ENV", "MISSING_CREDENTIAL"), "missing_v2_gateway_credential:MISSING_CREDENTIAL"),
        (("NMBOT_V2_GATEWAY_REQUEST_TIMEOUT_SECONDS", "zero"), "invalid_v2_config:NMBOT_V2_GATEWAY_REQUEST_TIMEOUT_SECONDS"),
        (("NMBOT_V2_PLANNER_MODEL", ""), "missing_v2_config:NMBOT_V2_PLANNER_MODEL"),
        (("NMBOT_V2_RESPONSE_COMPOSER_MODE", "invalid"), "invalid_v2_config:NMBOT_V2_RESPONSE_COMPOSER_MODE"),
        (("NMBOT_V2_RELEASE_ID", "local-v2"), "invalid_release_identity"),
    ),
)
def test_host_configuration_fails_before_app_and_never_exposes_credential(monkeypatch, tmp_path: Path, change, error: str) -> None:
    import scripts.nmbot_v2_host as host

    source = _environ(tmp_path)
    source[change[0]] = change[1]
    monkeypatch.setattr(host, "create_v2_host_app", lambda **_kwargs: pytest.fail("app must not be created"))
    with pytest.raises((V2HostConfigurationError, ValueError), match=error) as raised:
        create_v2_host_app_from_environ(environ=source)
    assert "gateway-secret-not-for-output" not in str(raised.value)


@pytest.mark.parametrize(
    "gateway_url",
    (
        "not-a-url",
        "ftp://gateway.invalid",
        "https:///missing-host",
        "https://user:password@gateway.invalid",
        "https://gateway.invalid/v2?mode=test",
        "https://gateway.invalid/v2#section",
        "https://gateway.invalid:99999",
    ),
)
def test_host_rejects_malformed_gateway_url_before_app_without_credential_output(monkeypatch, tmp_path: Path, gateway_url: str) -> None:
    import scripts.nmbot_v2_host as host

    source = _environ(tmp_path)
    source["NMBOT_V2_GATEWAY_URL"] = gateway_url
    monkeypatch.setattr(host, "create_v2_host_app", lambda **_kwargs: pytest.fail("app must not be created"))
    with pytest.raises(V2HostConfigurationError, match=r"^invalid_v2_config:NMBOT_V2_GATEWAY_URL$") as raised:
        create_v2_host_app_from_environ(environ=source)
    assert gateway_url not in str(raised.value)
    assert "gateway-secret-not-for-output" not in str(raised.value)


@pytest.mark.parametrize("gateway_url", ("http://127.0.0.1:8080/v2", "https://localhost/v2"))
def test_host_accepts_valid_loopback_gateway_urls(tmp_path: Path, gateway_url: str) -> None:
    source = _environ(tmp_path)
    source["NMBOT_V2_GATEWAY_URL"] = gateway_url

    settings = settings_from_environ(environ=source)

    assert settings.gateway_config.base_url == gateway_url


def test_service_entrypoint_delegates_host_bind_ownership(monkeypatch) -> None:
    import scripts.nmbot_v2_service as service
    import scripts.nmbot_v2_host as host

    called: list[bool] = []
    monkeypatch.setattr(host, "main", lambda: called.append(True))
    service.main()
    assert called == [True]


def test_host_import_closure_excludes_global_adapter_selector_and_jivo() -> None:
    tree = ast.parse((ROOT / "scripts/nmbot_v2_host.py").read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imports += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = ("scripts.nmbot_api_server", "scripts.nmbot_n8n_bridge_server", "scripts.nmbot_runtime_adapter", "selector", "jivo", "requests", "httpx", "socket")
    assert not any(name == blocked or name.startswith(blocked + ".") for name in imports for blocked in banned)
