from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import scripts.bluesminds_answer_interceptor as interceptor


def test_disabled_by_default() -> None:
    assert interceptor.is_enabled({}) is False
    raw, meta = asyncio.run(interceptor.try_answer({"system_prompt": "s", "query": "q"}))
    assert raw == ""
    assert meta == {"ok": False, "error_code": "bluesminds_interceptor_disabled", "_interceptor_skipped": True}


def test_config_status_safe_values_only() -> None:
    status = interceptor.config_status(
        {
            "NMBOT_BLUESMINDS_INTERCEPTOR": "yes",
            "NMBOT_BLUESMINDS_MODEL": "custom-model",
            "NMBOT_BLUESMINDS_TIMEOUT": "17",
            "BLUESMINDS_API_KEY": "must-not-leak",
        }
    )
    assert status == {"enabled": True, "model": "custom-model", "timeout": 17}


def test_mocked_client_success_exact_messages_and_model(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeBluesmindsClient:
        def __init__(self) -> None:
            calls.append({"constructed": True})

        def chat(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "готовый ответ"}}]}

    fake_module = types.SimpleNamespace(BluesmindsClient=FakeBluesmindsClient)
    monkeypatch.setitem(sys.modules, "bluesminds_client", fake_module)
    monkeypatch.setenv("NMBOT_BLUESMINDS_INTERCEPTOR", "enabled")
    monkeypatch.setenv("NMBOT_BLUESMINDS_MODEL", "gpt-test")
    monkeypatch.setenv("NMBOT_BLUESMINDS_TIMEOUT", "33")

    raw, meta = asyncio.run(interceptor.try_answer({"system_prompt": "system text", "query": "user text", "external_api_key": "nope"}))

    assert raw == "готовый ответ"
    assert calls[0] == {"constructed": True}
    assert calls[1] == {
        "model": "gpt-test",
        "messages": [{"role": "system", "content": "system text"}, {"role": "user", "content": "user text"}],
        "temperature": 0.25,
        "max_tokens": 1800,
    }
    assert meta == {"ok": True, "_gateway_client_impl": "bluesminds_interceptor", "_fallback_used": True, "model": "gpt-test"}


def test_exception_metadata_is_safe(monkeypatch) -> None:
    class ExplodingClient:
        def __init__(self) -> None:
            pass

        def chat(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("secret-key-value should never be returned")

    monkeypatch.setitem(sys.modules, "bluesminds_client", types.SimpleNamespace(BluesmindsClient=ExplodingClient))
    monkeypatch.setenv("NMBOT_BLUESMINDS_INTERCEPTOR", "on")
    raw, meta = asyncio.run(interceptor.try_answer({"system_prompt": "s", "query": "q"}))
    assert raw == ""
    assert meta["error_code"] == "bluesminds_interceptor_exception"
    assert meta["_upstream_error"] is True
    assert "secret" not in str(meta).lower()
