from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from urllib import error


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_jivo_client.py"
spec = importlib.util.spec_from_file_location("nmbot_jivo_client", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_request_gate_blocks_network_without_live(monkeypatch, capsys):
    calls = 0

    def forbidden_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("network should not be called")

    answers = iter(["Привет", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(mod, "post_client_message", forbidden_post)

    config = mod.ClientConfig(
        base_url=mod.DEFAULT_BASE_URL,
        live=False,
        max_turns=1,
        timeout=1.0,
        dry_run=None,
    )

    assert mod.run_repl(config, environ={mod.TOKEN_ENV: "secret-token"}) == 0
    out = capsys.readouterr().out
    assert calls == 0
    assert "Offline mode" in out
    assert "secret-token" not in out


def test_dry_run_metadata_does_not_reveal_text_or_token(capsys, monkeypatch):
    monkeypatch.setenv(mod.TOKEN_ENV, "secret-provider-token")

    assert mod.main(["--dry-run", "секретный текст клиента"]) == 0
    first = capsys.readouterr().out
    assert mod.main(["--dry-run", "секретный текст клиента"]) == 0
    second = capsys.readouterr().out

    out = first
    assert first == second
    assert "CLIENT_MESSAGE" in out
    assert "text_length" in out
    assert "секретный текст клиента" not in out
    assert "secret-provider-token" not in out
    assert "test-only-nmbot" not in out
    assert "payload" not in out.lower()


def test_client_message_shape_session_stability_and_unique_event_ids():
    session = mod.make_synthetic_session(seed="abc123def4567890")

    first = mod.build_client_message("Привет", session, now=123)
    second = mod.build_client_message("Ещё вопрос", session, now=124)

    assert first["event"] == "CLIENT_MESSAGE"
    assert first["site_id"] == second["site_id"]
    assert first["chat_id"] == second["chat_id"]
    assert first["client_id"] == second["client_id"]
    assert first["id"] != second["id"]
    assert first["message"] == {"type": "TEXT", "text": "Привет", "timestamp": 123}
    assert first["agents_online"] is True
    assert first["sender"]["has_contacts"] is False
    assert first["channel"]["type"] == "widget"
    assert str(first["site_id"]).startswith("test-only-nmbot-site-")


def test_response_normalizer_bot_message():
    kind, text = mod.normalize_response({"event": "BOT_MESSAGE", "message": {"type": "TEXT", "text": "Ответ бота"}})

    assert kind == "bot"
    assert text == "Ответ бота"


def test_response_normalizer_invite_agent():
    kind, text = mod.normalize_response({"event": "INVITE_AGENT", "client_id": "c", "chat_id": "ch"})

    assert kind == "handoff"
    assert "оператора" in text


def test_http_error_redacts_response_body():
    def failing_open(*args, **kwargs):
        raise error.HTTPError(
            url="http://127.0.0.1:8088/jivo/secret-token",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b"raw body with SECRET"),
        )

    payload = mod.build_client_message("Привет", mod.make_synthetic_session(seed="abc"), event_id="event-1")

    try:
        mod.post_client_message(
            payload,
            base_url=mod.DEFAULT_BASE_URL,
            provider_token="secret-token",
            timeout=1.0,
            opener=failing_open,
        )
    except mod.SafeClientError as exc:
        message = str(exc)
    else:
        raise AssertionError("SafeClientError expected")

    assert "HTTP error 500" in message
    assert "SECRET" not in message
    assert "secret-token" not in message
    assert "raw body" not in message


def test_missing_token_fails_safely():
    try:
        mod.live_request_allowed(True, "")
    except mod.SafeClientError as exc:
        message = str(exc)
    else:
        raise AssertionError("SafeClientError expected")

    assert mod.TOKEN_ENV in message
    assert "no network request" in message
