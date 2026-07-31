from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from aiohttp import web


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


api = _load("nmbot_api_server_client_prod_test", SCRIPT_DIR / "nmbot_api_server.py")
bridge = _load("nmbot_n8n_bridge_server_client_prod_test", SCRIPT_DIR / "nmbot_n8n_bridge_server.py")
policy = _load("nmbot_egress_policy_client_prod_test", SCRIPT_DIR / "nmbot_egress_policy.py")
cli = _load("nmbot_client_production_runtime_test", SCRIPT_DIR / "nmbot_client_production_runtime.py")
preflight = _load("nmbot_client_production_preflight_test", SCRIPT_DIR / "nmbot_client_production_preflight.py")


class FakeStore:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}

    async def get(self, user_id: str) -> dict[str, Any]:
        return self.states.setdefault(user_id, api._canonical_reset_state())

    async def save(self, user_id: str, state: dict[str, Any]) -> None:
        self.states[user_id] = dict(state)


class FakeRuntimeVersionStore:
    def __init__(self, version: str = "V2") -> None:
        self.version = version

    async def get(self) -> str:
        return self.version

    async def set(self, version: str) -> str:
        self.version = api._normalize_runtime_version(version)
        return self.version


def make_app(tmp_path: Path | None = None) -> web.Application:
    app = web.Application()
    app["state_store"] = FakeStore()
    app["runtime_version_store"] = FakeRuntimeVersionStore()
    app["crm_callback_outbox"] = api.LocalCallbackOutbox(tmp_path / "outbox" if tmp_path else Path("/tmp/nmbot-client-prod-test-outbox"))
    app["jivo_session_locks"] = api.SessionLockRegistry()
    app["jivo_dedup_cache"] = api.JivoDedupCache(ttl_sec=60, max_entries=32)
    return app


def payload(text: str, *, event_id: str = "event-1") -> dict[str, Any]:
    return {
        "event": "CLIENT_MESSAGE",
        "id": event_id,
        "site_id": "site1",
        "client_id": "client1",
        "chat_id": "chat1",
        "agents_online": True,
        "message": {"type": "TEXT", "text": text},
    }


def valid_preflight_env(root: Path) -> dict[str, str]:
    return {
        "NMBOT_CONTOUR_PROFILE": "client_production",
        "JIVO_PROVIDER_ID": "provider",
        "JIVO_PROVIDER_TOKEN": "provider-token",
        "NMBOT_API_TOKEN": "api-token",
        "NMBOT_N8N_BRIDGE_TOKEN": "bridge-token",
        "NMBOT_API_HOST": "127.0.0.1",
        "NMBOT_API_PORT": "8188",
        "NMBOT_N8N_BRIDGE_PORT": "8193",
        "NMBOT_BRIDGE_UPSTREAM": "http://127.0.0.1:8188",
        "NMBOT_V2_MANAGER_REWRITER_MODE": "off",
        "NMBOT_V3_MANAGER_REWRITER_MODE": "publish",
        "NMBOT_RUNTIME_VERSION_FILE": str(root / "data/runtime_selector.json"),
        "NMBOT_API_STATE_FILE": str(root / "data/api_state.json"),
    }


def test_default_profile_start_command_semantics_unchanged(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.delenv("NMBOT_CONTOUR_PROFILE", raising=False)
        app = make_app()
        response, status = await api.process_jivo_client_message(app, payload("/start_0"))
        assert status == 200
        assert response["message"]["text"].endswith("Сейчас активна версия: V0.")
        assert app["state_store"].states["jivo:site1:chat1:client1"]["runtime_version_override"] == "V0"

    asyncio.run(scenario())


def test_client_production_start_versions_reset_without_override_or_version_line(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_production")
        app = make_app()
        await app["runtime_version_store"].set("V3")
        response, status = await api.process_jivo_client_message(app, payload("/start_0"))
        assert status == 200
        text = response["message"]["text"]
        assert text.startswith("Здравствуйте! Меня зовут Светлана")
        assert "Сейчас активна версия" not in text
        state = app["state_store"].states["jivo:site1:chat1:client1"]
        assert "runtime_version_override" not in state

    asyncio.run(scenario())


def test_client_production_admin_selector_controls_start_greeting(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_production")
        app = make_app()
        await app["runtime_version_store"].set("V0")
        response, _status = await api.process_jivo_client_message(app, payload("/start", event_id="start-v0"))
        assert response["message"]["text"].startswith("Здравствуйте! Меня зовут Валерия")
        assert "Сейчас активна версия" not in response["message"]["text"]

    asyncio.run(scenario())


def test_client_production_ignores_and_removes_stale_override_on_first_turn(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_production")
        app = make_app()
        await app["runtime_version_store"].set("V3")
        session_key = "jivo:site1:chat1:client1"
        app["state_store"].states[session_key] = {"runtime_version_override": "V0", "nmbot_v0": {}}

        async def fake_run_chat(_app, *, user_id, message, channel, meta=None):
            assert user_id == session_key
            return {"ok": True, "answer": "Готово", "intent": "test", "meta": {"runtime": "v3"}}

        monkeypatch.setattr(api, "run_chat", fake_run_chat)
        response, status = await api.process_jivo_client_message(app, payload("Хочу квартиру", event_id="turn-v3"))
        assert status == 200
        assert response["message"]["text"] == "Готово"
        state = app["state_store"].states[session_key]
        assert "runtime_version_override" not in state

    asyncio.run(scenario())


def test_api_bot_message_guard_strips_known_decoration_and_blocks_marker(monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_production")
    clean = api.build_jivo_bot_message({"client_id": "c", "chat_id": "h"}, "ЖК хороший.\n\nСейчас активна версия: V3.")
    assert clean["message"]["text"] == "ЖК хороший."

    blocked = api.build_jivo_bot_message({"client_id": "c", "chat_id": "h"}, "Ответ: V3 через gateway-agent")
    assert blocked["message"]["text"] == policy.SAFE_CLIENT_FALLBACK_TEXT


def test_policy_preserves_normal_russian_prose(monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_production")
    result = policy.sanitize_client_text("Вариант подойдёт для семьи: рядом школа, парк и метро.")
    assert result.text.startswith("Вариант подойдёт")
    assert result.blocked is False


def test_policy_blocks_expanded_technical_and_internal_markers(monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_production")
    blocked = [
        "Ответ собран через gateway agent.",
        "Диагностика response-composer готова.",
        "Откройте http://127.0.0.1:8188/health",
        "Адрес 0.0.0.0 доступен",
        "Лог лежит в /tmp/issue-agent/raw.json",
        json.dumps({"ok": False, "trace": {"task_id": "abc"}, "items": [1] * 200}),
        "IPv6 ::1 loopback",
    ]
    for text in blocked:
        result = policy.sanitize_client_text(text)
        assert result.blocked is True, text
        assert result.text == policy.SAFE_CLIENT_FALLBACK_TEXT


def test_bridge_terminal_guard_blocks_outbound_and_logs_code(monkeypatch) -> None:
    async def scenario() -> None:
        rows: list[tuple[str, dict[str, object]]] = []
        sent: list[dict[str, Any]] = []

        class Response:
            status = 200

            async def read(self) -> bytes:
                return b"{}"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class Session:
            def post(self, _url, *, data, **_kwargs):
                sent.append(json.loads(data.decode("utf-8")))
                return Response()

        monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_production")
        monkeypatch.setenv("JIVO_PROVIDER_ID", "provider")
        monkeypatch.setattr(bridge, "_log_structured", lambda _trace_id, stage, **fields: rows.append((stage, fields)))
        status, error = await bridge._post_event_to_jivo(
            Session(),
            "token",
            json.dumps({"event": "BOT_MESSAGE", "message": {"type": "TEXT", "text": "```json\n{}\n```"}}).encode(),
            "trace",
            {},
        )
        assert status == 200 and error is None
        assert sent[0]["message"]["text"] == policy.SAFE_CLIENT_FALLBACK_TEXT
        assert ("egress_guard", {"response_event": "BOT_MESSAGE", "outcome": "blocked", "blocker_code": "code_fence", "delivery_role": "final"}) in rows

    asyncio.run(scenario())


def test_bridge_guard_failure_fails_closed_in_client_production(monkeypatch) -> None:
    async def scenario() -> None:
        sent: list[dict[str, Any]] = []

        class Response:
            status = 200
            async def read(self) -> bytes: return b"{}"
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return None
        class Session:
            def post(self, _url, *, data, **_kwargs):
                sent.append(json.loads(data.decode("utf-8")))
                return Response()

        monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_production")
        monkeypatch.setenv("JIVO_PROVIDER_ID", "provider")
        monkeypatch.setattr(bridge, "guard_jivo_event", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(bridge, "_log_structured", lambda *_args, **_kwargs: None)
        await bridge._post_event_to_jivo(Session(), "token", b'{"event":"BOT_MESSAGE","message":{"text":"normal"}}', "trace", {})
        assert sent[0]["message"]["text"] == policy.SAFE_CLIENT_FALLBACK_TEXT

    asyncio.run(scenario())


def test_units_and_env_are_isolated_from_legacy_contour() -> None:
    env = (ROOT / "deploy/systemd/novostroy-bot-client-production.env.example").read_text(encoding="utf-8")
    api_unit = (ROOT / "deploy/systemd/novostroy-bot-client-production-api.service").read_text(encoding="utf-8")
    bridge_unit = (ROOT / "deploy/systemd/novostroy-bot-client-production-n8n-bridge.service").read_text(encoding="utf-8")
    combined = env + api_unit + bridge_unit
    assert "NMBOT_CONTOUR_PROFILE=client_production" in env
    assert "/home/neiro/novostroy-bot-client-production" in combined
    assert "NMBOT_API_PORT=8188" in env and "NMBOT_N8N_BRIDGE_PORT=8193" in env
    assert "NMBOT_BRIDGE_UPSTREAM=http://127.0.0.1:8188" in env
    assert "NMBOT_V2_MANAGER_REWRITER_MODE=off" in env
    assert "NMBOT_V3_MANAGER_REWRITER_MODE=publish" in env
    assert "8088" not in env and "8093" not in env
    assert "novostroy-bot-client-production-api.service" in bridge_unit
    assert "Requires=novostroy-bot-client-production-api.service" in bridge_unit
    assert "nmbot_client_production_preflight.py" in api_unit
    assert "nmbot_client_production_preflight.py" in bridge_unit


def test_preflight_bootstraps_missing_selector_but_blocks_empty_or_malformed(tmp_path, monkeypatch) -> None:
    root = tmp_path / "client"
    selector = root / "data/runtime_selector.json"
    monkeypatch.setattr(preflight, "ROOT", root)
    env = valid_preflight_env(root)
    env["NMBOT_RUNTIME_VERSION_FILE"] = str(selector)
    preflight.validate(env)
    assert json.loads(selector.read_text(encoding="utf-8"))["version"] == "V3"
    assert oct(selector.stat().st_mode & 0o777) == "0o600"

    selector.write_text("", encoding="utf-8")
    try:
        preflight.validate(env)
    except SystemExit as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("empty selector must block")

    selector.write_text("not-json", encoding="utf-8")
    try:
        preflight.validate(env)
    except SystemExit as exc:
        assert "malformed" in str(exc)
    else:
        raise AssertionError("malformed selector must block")


def test_preflight_rejects_placeholder_secret_and_paths_outside_root(tmp_path, monkeypatch) -> None:
    root = tmp_path / "client"
    monkeypatch.setattr(preflight, "ROOT", root)
    env = valid_preflight_env(root)
    env["JIVO_PROVIDER_ID"] = "PLACEHOLDER"
    env["NMBOT_RUNTIME_VERSION_FILE"] = str(Path("/tmp/outside.json"))
    try:
        preflight.validate(env)
    except SystemExit as exc:
        assert "PLACEHOLDER" not in str(exc)
        assert "JIVO_PROVIDER_ID" in str(exc)
    else:
        raise AssertionError("placeholder secret must block")

    env["JIVO_PROVIDER_ID"] = "provider"
    try:
        preflight.validate(env)
    except SystemExit as exc:
        assert "under" in str(exc)
    else:
        raise AssertionError("outside mutable path must block")


def test_preflight_requires_client_production_manager_rewriter_modes(tmp_path, monkeypatch) -> None:
    root = tmp_path / "client"
    monkeypatch.setattr(preflight, "ROOT", root)

    env = valid_preflight_env(root)
    env["NMBOT_V2_MANAGER_REWRITER_MODE"] = " OFF "
    env["NMBOT_V3_MANAGER_REWRITER_MODE"] = " PUBLISH "
    preflight.validate(env)

    invalid_cases = [
        ("NMBOT_V3_MANAGER_REWRITER_MODE", "shadow"),
        ("NMBOT_V3_MANAGER_REWRITER_MODE", "off"),
        ("NMBOT_V2_MANAGER_REWRITER_MODE", "publish"),
        ("NMBOT_V3_MANAGER_REWRITER_MODE", ""),
    ]
    for key, value in invalid_cases:
        bad_env = valid_preflight_env(root)
        bad_env[key] = value
        try:
            preflight.validate(bad_env)
        except SystemExit as exc:
            assert key in str(exc)
        else:
            raise AssertionError(f"{key}={value!r} must block")


def test_client_production_startup_ignores_cli_host_port_overrides(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_run_app(_app, *, host, port):
        calls.append((host, port))
        raise SystemExit(0)

    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_production")
    monkeypatch.setattr(api.web, "run_app", fake_run_app)
    monkeypatch.setattr(api, "create_app", lambda: web.Application())
    monkeypatch.setattr(sys, "argv", ["nmbot_api_server.py", "--host", "0.0.0.0", "--port", "9999"])
    try:
        api.main()
    except SystemExit:
        pass

    monkeypatch.setattr(bridge.web, "run_app", fake_run_app)
    monkeypatch.setattr(bridge, "create_app", lambda: web.Application())
    monkeypatch.setattr(sys, "argv", ["nmbot_n8n_bridge_server.py", "--host", "127.0.0.1", "--port", "9999"])
    try:
        bridge.main()
    except SystemExit:
        pass

    assert calls == [("127.0.0.1", 8188), ("0.0.0.0", 8193)]


def test_admin_cli_blocks_mismatch_and_requires_confirmation(tmp_path) -> None:
    bad_env = tmp_path / ".env.client-production"
    bad_env.write_text("NMBOT_CONTOUR_PROFILE=test\nNMBOT_API_HOST=127.0.0.1\nNMBOT_API_PORT=8188\nNMBOT_API_TOKEN=secret\n", encoding="utf-8")
    try:
        cli.main(["--env", str(bad_env), "status"])
    except SystemExit as exc:
        assert "profile mismatch" in str(exc)
    else:
        raise AssertionError("profile mismatch must block")

    good_env = tmp_path / ".env.good"
    good_env.write_text("NMBOT_CONTOUR_PROFILE=client_production\nNMBOT_API_HOST=127.0.0.1\nNMBOT_API_PORT=8188\nNMBOT_API_TOKEN=secret\n", encoding="utf-8")
    try:
        cli.main(["--env", str(good_env), "set", "V3"])
    except SystemExit as exc:
        assert "requires --confirm" in str(exc)
    else:
        raise AssertionError("set without --confirm must block")


def test_admin_cli_status_prints_only_safe_fields(tmp_path, monkeypatch, capsys) -> None:
    env_path = tmp_path / ".env.client-production"
    env_path.write_text("NMBOT_CONTOUR_PROFILE=client_production\nNMBOT_API_HOST=127.0.0.1\nNMBOT_API_PORT=8188\nNMBOT_API_TOKEN=super-secret-token\n", encoding="utf-8")

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def read(self) -> bytes:
            return b'{"ok":true,"runtime_version":"V3","token":"must-not-print"}'

    monkeypatch.setattr(cli.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    assert cli.main(["--env", str(env_path), "status"]) == 0
    out = capsys.readouterr().out
    assert "V3" in out and "client_production" in out and "8188" in out
    assert "super-secret-token" not in out and "must-not-print" not in out and "token" not in out.lower()
