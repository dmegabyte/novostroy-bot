from __future__ import annotations

import asyncio
import json

import scripts.gateway_v0_answer_writer as writer
import scripts.nmbot_gateway_client as gateway_client_mod


def test_request_payload_uses_configured_openrouter_model_temperature_tokens_and_external_key(monkeypatch) -> None:
    monkeypatch.setenv(writer.ENV_MODEL, "openai/gpt-5.5")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-openrouter-key")

    payload = writer.request_payload({"client_message": "локальный тест"}, prompt="system")
    status = writer.config_status()

    assert status["model"] == "openai/gpt-5.5"
    assert payload["_payload_stage"] == "conversation_answer_v0_writer_diagnostic"
    assert payload["service"] == "openrouter"
    assert payload["model"] == "openai/gpt-5.5"
    assert payload["parameters"] == {"temperature": 0.4, "max_tokens": 2000}
    assert payload["external_api_key"] == "secret-openrouter-key"


def test_try_write_uses_run_gateway_once_and_safe_metadata(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeClient:
        async def _run_gateway_request_once(self, request_data, headers, timeout):
            calls.append({"request_data": request_data, "headers": headers, "timeout": timeout})
            return "готово plain text", {"_gateway_task_id": "task-safe", "raw": "секретный клиентский текст"}

        async def close(self):
            calls.append({"closed": True})

    monkeypatch.setenv(writer.ENV_MODEL, "openai/gpt-5.5")
    monkeypatch.delenv(writer.ENV_TIMEOUT, raising=False)
    monkeypatch.setenv("GATEWAY_POLL_TOKEN", "poll-token")
    monkeypatch.setattr(gateway_client_mod, "OvermindClient", FakeClient)

    content, meta = asyncio.run(writer.try_write({"client_message": "секретный клиентский текст"}))

    assert content
    request_data = calls[0]["request_data"]
    assert request_data["model"] == "openai/gpt-5.5"
    assert request_data["service"] == "openrouter"
    assert request_data["parameters"] == {"temperature": 0.4, "max_tokens": 2000}
    assert calls[0]["headers"] == {"Authorization": "Bearer poll-token"}
    assert calls[0]["timeout"] == 60
    assert calls[-1] == {"closed": True}
    assert meta["ok"] is True
    assert meta["provider"] == "gateway"
    assert meta["model"] == "openai/gpt-5.5"
    dumped = json.dumps(meta, ensure_ascii=False)
    assert "секретный клиентский текст" not in dumped
    assert "poll-token" not in dumped


def test_try_write_gateway_error_safe_meta_no_payload_or_exception_text(monkeypatch) -> None:
    class ExplodingClient:
        async def _run_gateway_request_once(self, *_args, **_kwargs):
            raise RuntimeError("provider exploded with secret body")

        async def close(self):
            pass

    monkeypatch.setattr(gateway_client_mod, "OvermindClient", ExplodingClient)

    content, meta = asyncio.run(writer.try_write({"client_message": "секретный клиентский текст"}))

    assert content == ""
    assert meta["ok"] is False
    assert meta["error_code"] == "v0_answer_writer_exception"
    dumped = json.dumps(meta, ensure_ascii=False)
    assert "provider exploded" not in dumped
    assert "secret body" not in dumped
    assert "секретный клиентский текст" not in dumped
