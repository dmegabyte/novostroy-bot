from __future__ import annotations

import asyncio
import json

import scripts.bluesminds_client as bluesminds_client_mod
import scripts.bluesminds_v0_answer_writer as writer


def test_try_write_uses_package_client_default_model_temperature_and_tokens(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeClient:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"choices": [{"message": {"content": "готово plain text"}}]}

    monkeypatch.delenv(writer.ENV_MODEL, raising=False)
    monkeypatch.delenv(writer.ENV_TIMEOUT, raising=False)
    monkeypatch.setattr(bluesminds_client_mod, "BluesmindsClient", FakeClient)

    content, meta = asyncio.run(writer.try_write({"client_message": "локальный тест"}))

    assert content
    assert meta["ok"] is True
    assert meta["model"] == "gpt-5.2-chat"
    assert calls[0]["model"] == "gpt-5.2-chat"
    assert calls[0]["temperature"] == 0.4
    assert calls[0]["max_tokens"] == 700
    assert isinstance(calls[0]["messages"], list)


def test_try_write_empty_response_safe_meta_no_raw_body(monkeypatch) -> None:
    class EmptyClient:
        def chat(self, **_kwargs):
            return {"choices": [{"message": {"content": ""}}], "raw_body": "secret-response-body"}

    monkeypatch.setattr(bluesminds_client_mod, "BluesmindsClient", EmptyClient)

    content, meta = asyncio.run(writer.try_write({"client_message": "секретный клиентский текст"}))

    assert content == ""
    assert meta["ok"] is False
    assert meta["error_code"] == "v0_answer_writer_empty_response"
    dumped = json.dumps(meta, ensure_ascii=False)
    assert "secret-response-body" not in dumped
    assert "секретный клиентский текст" not in dumped


def test_try_write_exception_safe_meta_no_exception_text(monkeypatch) -> None:
    class ExplodingClient:
        def chat(self, **_kwargs):
            raise RuntimeError("provider exploded with secret body")

    monkeypatch.setattr(bluesminds_client_mod, "BluesmindsClient", ExplodingClient)

    content, meta = asyncio.run(writer.try_write({"client_message": "секретный клиентский текст"}))

    assert content == ""
    assert meta["ok"] is False
    assert meta["error_code"] == "v0_answer_writer_exception"
    dumped = json.dumps(meta, ensure_ascii=False)
    assert "provider exploded" not in dumped
    assert "secret body" not in dumped
    assert "секретный клиентский текст" not in dumped
