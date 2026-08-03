from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from nmbot_runtime_contract.wire import CONTRACT_VERSION
from nmbot_v3.contracts import V3ContractError
from nmbot_v3.provider_invocation import V3InvocationOperation, V3TransportResponse
from nmbot_v3.writer_adapter import V3_WRITER_GATEWAY_RESULT_MARKER
from scripts.nmbot_v3_host import create_v3_host_app, create_v3_host_app_from_environ
from aiohttp.test_utils import TestClient, TestServer


ROOT = Path(__file__).resolve().parents[1]
_REF = "550e8400-e29b-41d4-a716-446655440000"


def _request(message: str = "Подберите квартиру; token=do-not-leak") -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "runtime_version": "V3",
        "conversation_ref": "conversation:host-test",
        "trace_ref": "trace:host-test",
        "message": message,
        "channel": "api",
        "meta": {},
    }


def _plan() -> dict[str, object]:
    return {
        "schema_version": 3,
        "goal": "new_search",
        "viewpoint": "life",
        "selected_option_name": None,
        "selected_option_ref": None,
        "named_object_reference": None,
        "comparison_option_names": [],
        "comparison_option_refs": [],
        "requested_facts": ["metro"],
        "constraints_delta": {},
        "operator_consent": None,
        "explicit_operator_request": False,
        "followup_outcome": None,
        "clarification": None,
        "confidence": 0.9,
    }


def _evidence() -> dict[str, object]:
    return {
        "facts": [{"name": "ЖК Тест", "canonical_ref": _REF, "fields": {"metro": "Солнцево"}, "is_near": False, "differences": []}],
        "near": [],
        "missing_facts": [],
    }


def _writer_output(*, unsafe: bool = False) -> dict[str, object]:
    return {
        "result_marker": V3_WRITER_GATEWAY_RESULT_MARKER,
        "output": {
            "intro": "Позвоните +7 999 123-45-67" if unsafe else "Проверила подтверждённые данные.",
            "cards": [{"name": "ЖК Тест", "text": "Метро: Солнцево."}],
            "recommendation": "",
            "missing_note": "",
            "final_question": "Рассмотреть этот вариант подробнее?",
        },
    }


def test_injected_host_composes_successful_turn_with_redacted_identity_safe_transport(tmp_path: Path) -> None:
    async def scenario() -> None:
        class Transport:
            def __init__(self) -> None:
                self.requests = []

            async def invoke(self, request):
                self.requests.append(request)
                if request.operation is V3InvocationOperation.PLANNER:
                    assert request.payload.payload["user_text"].endswith("[redacted-credential]")
                    payload = _plan()
                elif request.operation is V3InvocationOperation.EVIDENCE:
                    assert request.payload.payload["requested_facts"] == ("metro",)
                    payload = _evidence()
                elif request.operation is V3InvocationOperation.WRITER:
                    assert request.payload.to_payload()["schema_version"] == "v3_writer_request_v1"
                    payload = _writer_output()
                else:
                    raise AssertionError("unexpected V3 operation")
                return V3TransportResponse(request.request_id, payload)

        transport = Transport()
        app = create_v3_host_app(
            transport=transport,
            timeout_seconds=1,
            state_path=tmp_path / "state.json",
            journal_path=tmp_path / "journal.jsonl",
            token="host-test-token",
            release_identity="v3-host-test-immutable",
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post("/api/chat", json=_request(), headers={"Authorization": "Bearer host-test-token"})
            body = await response.json()
            assert response.status == 200 and body["ok"] is True and body["runtime_version"] == "V3"
            assert body["client_answer"].startswith("Проверила подтверждённые данные.")
            assert [request.operation for request in transport.requests] == [
                V3InvocationOperation.PLANNER, V3InvocationOperation.EVIDENCE, V3InvocationOperation.WRITER,
            ]
            assert len({request.request_id for request in transport.requests}) == 3
            persisted = (tmp_path / "state.json").read_text(encoding="utf-8")
            assert "do-not-leak" not in persisted
            assert "do-not-leak" not in (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
        finally:
            await client.close()

    asyncio.run(scenario())


def test_private_worker_writer_failures_keep_deterministic_response_and_persist_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        class Transport:
            def __init__(self) -> None:
                self.requests = []
                self.writer_mode = "pii"

            async def invoke(self, request):
                self.requests.append(request)
                if request.operation is V3InvocationOperation.PLANNER:
                    payload = _plan()
                elif request.operation is V3InvocationOperation.EVIDENCE:
                    payload = _evidence()
                elif self.writer_mode == "timeout":
                    await asyncio.sleep(0.02)
                    payload = _writer_output()
                elif self.writer_mode == "error":
                    raise RuntimeError("Authorization: Bearer writer-secret")
                else:
                    payload = _writer_output(unsafe=True)
                return V3TransportResponse(request.request_id, payload)

        transport = Transport()
        state_path = tmp_path / "state.json"
        app = create_v3_host_app(
            transport=transport,
            timeout_seconds=0.001,
            state_path=state_path,
            journal_path=tmp_path / "journal.jsonl",
            token="host-test-token",
            release_identity="v3-host-test-immutable",
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            for revision, mode in enumerate(("pii", "timeout", "error"), start=1):
                transport.writer_mode = mode
                response = await client.post("/api/chat", json=_request(), headers={"Authorization": "Bearer host-test-token"})
                body = await response.json()
                assert response.status == 200 and body["ok"] is True
                assert body["error_code"] is None
                assert body["client_answer"].startswith("Нашла подтверждённые варианты.")
                assert "secret" not in json.dumps(body).casefold()
                stored = json.loads(state_path.read_text(encoding="utf-8"))
                assert stored["conversation:host-test"]["revision"] == revision
            assert [request.operation for request in transport.requests] == [
                V3InvocationOperation.PLANNER, V3InvocationOperation.EVIDENCE, V3InvocationOperation.WRITER,
            ] * 3
        finally:
            await client.close()

    asyncio.run(scenario())


def test_host_rejects_missing_or_invalid_transport_before_app_creation(monkeypatch, tmp_path: Path) -> None:
    import scripts.nmbot_v3_host as host

    monkeypatch.setattr(host, "create_app", lambda **_kwargs: pytest.fail("app must not be created"))
    kwargs = {
        "timeout_seconds": 1,
        "state_path": tmp_path / "state.json",
        "journal_path": tmp_path / "journal.jsonl",
        "token": "host-test-token",
        "release_identity": "v3-host-test-immutable",
    }
    for transport in (None, object()):
        with pytest.raises(V3ContractError, match="invalid_v3_async_transport"):
            create_v3_host_app(transport=transport, **kwargs)  # type: ignore[arg-type]


def test_environment_host_fails_closed_before_app_creation_when_gateway_contract_is_absent(monkeypatch) -> None:
    import scripts.nmbot_v3_host as host

    monkeypatch.setattr(host, "create_v3_host_app", lambda **_kwargs: pytest.fail("app must not be created"))
    with pytest.raises(ValueError, match="missing_v3_gateway_config:NMBOT_V3_GATEWAY_URL"):
        create_v3_host_app_from_environ(environ={})


def test_outer_host_import_closure_excludes_global_api_bridge_router_and_gateway_clients() -> None:
    tree = ast.parse((ROOT / "scripts/nmbot_v3_host.py").read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imports += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = (
        "scripts.nmbot_gateway_client", "scripts.nmbot_api_server", "scripts.nmbot_n8n_bridge_server",
        "scripts.nmbot_version_router", "scripts.nmbot_runtime_adapter", "scripts.nmbot_env_secrets",
        "selector", "jivo", "requests", "httpx", "socket",
    )
    assert not any(name == blocked or name.startswith(blocked + ".") for name in imports for blocked in banned)
