from __future__ import annotations

import asyncio
import ast
import hashlib
import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_n8n_bridge_server.py"
spec = importlib.util.spec_from_file_location("nmbot_n8n_bridge_server", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def test_bridge_upstream_uses_raw_uuid_header_and_safe_ref_hash_boundary() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    send_func = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_send_to_jivo_after_bot")
    header_assignment_seen = False
    for node in ast.walk(send_func):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "X-NMBOT-Trace-ID" and isinstance(value, ast.Name) and value.id == "trace_id":
                    header_assignment_seen = True
    assert header_assignment_seen

    raw_uuid = "123e4567-e89b-12d3-a456-426614174000"
    assert mod._safe_trace_ref(raw_uuid) == "trace_" + hashlib.sha256(raw_uuid.encode("utf-8")).hexdigest()[:12]


def test_slow_upstream_sends_status_then_returns_final() -> None:
    async def scenario() -> None:
        events: list[str] = []

        async def upstream() -> bytes:
            await asyncio.sleep(0.03)
            events.append("final_ready")
            return b"final"

        async def send_status() -> None:
            events.append("status")

        result, outcome, status_sent = await mod._await_upstream_with_status(
            upstream(),
            status_timeout=0.01,
            hard_timeout=0.1,
            send_status=send_status,
        )

        assert result == b"final"
        assert outcome == "upstream_after_status"
        assert status_sent is True
        assert events == ["status", "final_ready"]

    asyncio.run(scenario())


def test_repeating_status_updates_continue_until_final() -> None:
    async def scenario() -> None:
        events: list[str] = []

        async def upstream() -> bytes:
            await asyncio.sleep(0.047)
            events.append("final_ready")
            return b"final"

        async def send_status() -> None:
            events.append("status")

        result, outcome, status_sent = await mod._await_upstream_with_status(
            upstream(),
            status_timeout=0.01,
            hard_timeout=0.1,
            send_status=send_status,
            repeat_interval=0.01,
        )

        assert result == b"final"
        assert outcome == "upstream_after_status"
        assert status_sent is True
        assert events == ["status", "status", "status", "status", "final_ready"]

    asyncio.run(scenario())


def test_hard_timeout_cancels_upstream_cleanly() -> None:
    async def scenario() -> None:
        events: list[str] = []

        async def upstream() -> bytes:
            try:
                await asyncio.sleep(1)
                return b"never"
            finally:
                events.append("cancelled")

        async def send_status() -> None:
            events.append("status")

        result, outcome, status_sent = await mod._await_upstream_with_status(
            upstream(),
            status_timeout=0.01,
            hard_timeout=0.02,
            send_status=send_status,
        )

        assert result is None
        assert outcome == "hard_timeout"
        assert status_sent is True
        assert events == ["status", "cancelled"]

    asyncio.run(scenario())


def test_fast_result_emits_no_status() -> None:
    async def scenario() -> None:
        events: list[str] = []

        async def upstream() -> bytes:
            return b"fast"

        async def send_status() -> None:
            events.append("status")

        result, outcome, status_sent = await mod._await_upstream_with_status(
            upstream(),
            status_timeout=0.1,
            hard_timeout=0.2,
            send_status=send_status,
        )

        assert result == b"fast"
        assert outcome == "upstream"
        assert status_sent is False
        assert events == []

    asyncio.run(scenario())


def test_status_callback_failure_does_not_cancel_final() -> None:
    async def scenario() -> None:
        events: list[str] = []

        async def upstream() -> bytes:
            await asyncio.sleep(0.03)
            events.append("final_ready")
            return b"final"

        async def send_status() -> None:
            events.append("status_failed")
            raise RuntimeError("status post failed")

        result, outcome, status_sent = await mod._await_upstream_with_status(
            upstream(),
            status_timeout=0.01,
            hard_timeout=0.1,
            send_status=send_status,
        )

        assert result == b"final"
        assert outcome == "upstream_after_status"
        assert status_sent is False
        assert events == ["status_failed", "final_ready"]

    asyncio.run(scenario())


def test_timeout_config_validation(monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_BRIDGE_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("NMBOT_BRIDGE_HARD_TIMEOUT_SECONDS", "5")
    assert mod._bridge_timeout_config() == (90.0, 600.0)

    monkeypatch.setenv("NMBOT_BRIDGE_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("NMBOT_BRIDGE_HARD_TIMEOUT_SECONDS", "2.4")
    assert mod._bridge_timeout_config() == (2.5, 600.0)

    monkeypatch.setenv("NMBOT_BRIDGE_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("NMBOT_BRIDGE_HARD_TIMEOUT_SECONDS", "3")
    assert mod._bridge_timeout_config() == (2.0, 3.0)


def test_repeating_status_config_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("NMBOT_BRIDGE_STATUS_UPDATES_ENABLED", raising=False)
    monkeypatch.delenv("NMBOT_BRIDGE_STATUS_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("NMBOT_BRIDGE_STATUS_TEMPLATES", raising=False)

    enabled, interval, templates = mod._bridge_status_updates_config()

    assert enabled is False
    assert interval == 3.0
    assert templates == mod.DEFAULT_STATUS_UPDATE_TEMPLATES


def test_repeating_status_config_accepts_custom_templates(monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_BRIDGE_STATUS_UPDATES_ENABLED", "on")
    monkeypatch.setenv("NMBOT_BRIDGE_STATUS_INTERVAL_SECONDS", "3")
    monkeypatch.setenv("NMBOT_BRIDGE_STATUS_TEMPLATES", "Первый статус | Второй статус")

    enabled, interval, templates = mod._bridge_status_updates_config()

    assert enabled is True
    assert interval == 3.0
    assert templates == ("Первый статус", "Второй статус")


def test_status_delivery_is_logged_nonterminal(monkeypatch) -> None:
    async def scenario() -> None:
        rows: list[tuple[str, dict]] = []

        class Response:
            status = 200

            async def read(self) -> bytes:
                return b"{}"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class Session:
            def post(self, *_args, **_kwargs):
                return Response()

        monkeypatch.setenv("JIVO_PROVIDER_ID", "safe-test-provider")
        monkeypatch.setattr(mod, "_log_structured", lambda _trace_id, stage, **fields: rows.append((stage, fields)))

        status, error = await mod._post_event_to_jivo(
            Session(),
            "safe-test-token",
            b'{"event":"BOT_MESSAGE"}',
            "safe-test-trace",
            {},
            delivery_role="status",
        )

        assert (status, error) == (200, None)
        returned = [fields for stage, fields in rows if stage == "jivo_response_returned"]
        assert returned[-1]["outcome"] == "status_sent"
        assert returned[-1]["delivery_role"] == "status"
        assert returned[-1]["delivery_status"] == "status_sent"
        assert returned[-1]["terminal"] is False

    asyncio.run(scenario())


def test_final_delivery_is_logged_as_terminal_with_delivery_status(monkeypatch) -> None:
    async def scenario() -> None:
        rows: list[tuple[str, dict]] = []

        class Response:
            status = 200

            async def read(self) -> bytes:
                return b"{}"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class Session:
            def post(self, *_args, **_kwargs):
                return Response()

        monkeypatch.setenv("JIVO_PROVIDER_ID", "safe-test-provider")
        monkeypatch.setattr(mod, "_log_structured", lambda _trace_id, stage, **fields: rows.append((stage, fields)))

        status, error = await mod._post_event_to_jivo(
            Session(),
            "safe-test-token",
            b'{"event":"BOT_MESSAGE"}',
            "safe-test-trace",
            {},
            delivery_role="final",
        )

        assert (status, error) == (200, None)
        returned = [fields for stage, fields in rows if stage == "jivo_response_returned"]
        assert returned[-1]["outcome"] == "sent"
        assert returned[-1]["delivery_role"] == "final"
        assert returned[-1]["delivery_status"] == "sent"
        assert returned[-1]["terminal"] is True

    asyncio.run(scenario())


def test_bridge_error_journal_event_is_opaque_and_correlated(monkeypatch) -> None:
    rows: list[dict] = []
    monkeypatch.setattr(mod, "append_journal_event", lambda **kwargs: rows.append(kwargs))

    mod._append_bridge_error_to_journal(
        b'{"id":"event-1","site_id":"site-1","chat_id":"chat-1","client_id":"client-1","message":{"text":"secret"}}',
        code="bridge_delivery_error",
        stage="bridge_delivery",
        fallback=False,
    )

    assert rows == [{
        "session_key": "jivo:site-1:chat-1:client-1",
        "role": "system",
        "event_type": "delivery_error",
        "event_id": "event-1",
        "meta": {"site_id": "site-1", "chat_id": "chat-1", "client_id": "client-1"},
        "error_summary": {
            "status": "failed",
            "codes": ["bridge_delivery_error"],
            "stages": ["bridge_delivery"],
            "fallback": False,
        },
        "source": "bridge",
    }]


def test_bridge_error_journal_ignores_unknown_code_and_malformed_body(monkeypatch) -> None:
    rows: list[dict] = []
    monkeypatch.setattr(mod, "append_journal_event", lambda **kwargs: rows.append(kwargs))

    mod._append_bridge_error_to_journal(b"not-json", code="bridge_delivery_error", stage="bridge_delivery", fallback=False)
    mod._append_bridge_error_to_journal(b"{}", code="raw_secret_error", stage="bridge_delivery", fallback=False)

    assert rows == []


def test_safe_refs_and_jivo_errors_do_not_include_token_suffix_or_remote_message() -> None:
    ref = mod._safe_ref("secret-provider-token")
    assert ref.startswith("sha256:")
    assert "last4" not in ref and "token" not in ref
    err = mod._safe_jivo_error(b'{"error":{"code":"bad-token","message":"remote secret fragment"}}')
    assert err == "jivo_error:bad-token"
    assert "remote" not in err and "secret" not in err


def test_bridge_rejects_provider_token_before_claim_or_task(monkeypatch) -> None:
    async def scenario() -> None:
        app = mod.create_app()
        request = type("Request", (), {})()
        request.app = app
        request.headers = {"X-NMBOT-Bridge-Token": "bridge"}
        request.match_info = {"provider_token": "wrong"}
        async def read() -> bytes:
            return b'{"event":"CLIENT_MESSAGE","id":"e1","chat_id":"c","client_id":"u","message":{"text":"hi"}}'
        request.read = read
        monkeypatch.setenv("NMBOT_N8N_BRIDGE_TOKEN", "bridge")
        monkeypatch.setenv("JIVO_PROVIDER_TOKEN", "right")
        monkeypatch.setattr(mod, "_claim_dispatch_event", lambda *_args: (_ for _ in ()).throw(AssertionError("claim must not run")))
        monkeypatch.setattr(mod, "_track_task", lambda *_args: (_ for _ in ()).throw(AssertionError("task must not be created")))
        response = await mod.handle_proxy(request)
        assert response.status == 401
        assert json.loads(response.text)["error"] == "unauthorized"
        assert len(app[mod.APP_TASKS_KEY]) == 0

    asyncio.run(scenario())


def test_bridge_tracks_and_cleans_async_tasks(monkeypatch) -> None:
    async def scenario() -> None:
        app = mod.create_app()
        rows: list[tuple[str, dict]] = []
        monkeypatch.setattr(mod, "_log_structured", lambda _trace_id, stage, **fields: rows.append((stage, fields)))

        async def sleeper() -> None:
            await asyncio.sleep(10)

        task = mod._track_task(app, sleeper(), "trace", {})
        assert task in app[mod.APP_TASKS_KEY]
        await mod._cleanup_tasks(app)
        assert len(app[mod.APP_TASKS_KEY]) == 0
        assert any(stage == "task_cleanup" for stage, _fields in rows)

    asyncio.run(scenario())


def test_hard_timeout_final_fallback_is_sent_even_after_status(monkeypatch) -> None:
    async def scenario() -> None:
        posts: list[dict] = []
        logs: list[tuple[str, dict]] = []

        class Response:
            status = 200
            async def read(self) -> bytes: return b"{}"
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return None

        class SlowResponse(Response):
            async def read(self) -> bytes:
                await asyncio.sleep(1)
                return b"never"

        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return None
            def post(self, url, *, data, **_kwargs):
                posts.append({"url": url, "event": json.loads(data.decode("utf-8")).get("event")})
                if url.startswith("http://127.0.0.1"):
                    return SlowResponse()
                return Response()

        monkeypatch.setenv("JIVO_PROVIDER_ID", "provider")
        monkeypatch.setenv("NMBOT_API_TOKEN", "api")
        monkeypatch.setenv("NMBOT_BRIDGE_TIMEOUT_SECONDS", "0.01")
        monkeypatch.setenv("NMBOT_BRIDGE_HARD_TIMEOUT_SECONDS", "0.02")
        monkeypatch.setattr(mod, "ClientSession", lambda timeout=None: Session())
        monkeypatch.setattr(mod, "_log_structured", lambda _trace_id, stage, **fields: logs.append((stage, fields)))
        body = b'{"event":"CLIENT_MESSAGE","id":"e1","site_id":"s","chat_id":"c","client_id":"u","message":{"text":"hi"}}'
        trace = mod._request_trace(body)
        event_key = mod._event_key(trace)
        mod.LATEST_CHAT_EVENTS[event_key] = "e1"
        await mod._send_to_jivo_after_bot("provider-token", body, trace, event_key, "e1", "trace")
        final_posts = [item for item in posts if item["url"].startswith("https://bot.jivosite.com")]
        assert len(final_posts) == 2
        assert final_posts[-1]["event"] == "BOT_MESSAGE"
        terminal = [fields for stage, fields in logs if stage == "jivo_response_returned" and fields.get("delivery_role") == "final"]
        assert terminal and terminal[-1]["terminal"] is True

    asyncio.run(scenario())


def _write_route(path: Path, *, profile: str = "TEST", slot: str = "A", release_id: str = "v6-r42", port: int = 18088) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "nmbot.active_route.v1",
                "profile": profile,
                "revision": 1,
                "active": {
                    "slot": slot,
                    "release_id": release_id,
                    "upstream": f"http://127.0.0.1:{port}",
                },
                "previous": None,
                "switched_at": "2026-08-25T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )


def test_bridge_static_route_is_loopback_only_migration_mode(monkeypatch) -> None:
    monkeypatch.delenv("NMBOT_ACTIVE_ROUTE_FILE", raising=False)
    monkeypatch.delenv("NMBOT_CONTOUR_PROFILE", raising=False)
    monkeypatch.setenv("NMBOT_BRIDGE_UPSTREAM", "http://127.0.0.1:18088")

    assert mod._resolve_bridge_route() == {
        "mode": "static",
        "profile": "TEST",
        "slot": None,
        "release_id": None,
        "upstream": "http://127.0.0.1:18088",
    }

    monkeypatch.setenv("NMBOT_BRIDGE_UPSTREAM", "https://example.test:18088")
    try:
        mod._resolve_bridge_route()
    except mod.BridgeRouteError as exc:
        assert exc.code == "static_upstream_invalid"
    else:
        raise AssertionError("remote bridge upstream must fail closed")


def test_bridge_dynamic_route_requires_exact_profile(tmp_path: Path, monkeypatch) -> None:
    route_path = tmp_path / "active-route.json"
    _write_route(route_path, profile="TEST", slot="B", release_id="v6-r43", port=18089)
    monkeypatch.setenv("NMBOT_ACTIVE_ROUTE_FILE", str(route_path))
    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "TEST")

    assert mod._resolve_bridge_route() == {
        "mode": "dynamic",
        "profile": "TEST",
        "slot": "B",
        "release_id": "v6-r43",
        "upstream": "http://127.0.0.1:18089",
    }

    monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "PROD")
    try:
        mod._resolve_bridge_route()
    except mod.BridgeRouteError as exc:
        assert exc.code == "route_file_invalid"
    else:
        raise AssertionError("a TEST route must never be consumed by PROD")


def test_bridge_health_reports_safe_dynamic_route_identity(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        route_path = tmp_path / "active-route.json"
        _write_route(route_path, profile="PROD", slot="A", release_id="v6-r44", port=18090)
        monkeypatch.setenv("NMBOT_ACTIVE_ROUTE_FILE", str(route_path))
        monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "PROD")

        response = await mod.handle_health(None)
        payload = json.loads(response.text)

        assert response.status == 200
        assert payload["route"]["mode"] == "dynamic"
        assert payload["route"]["profile"] == "PROD"
        assert payload["route"]["slot"] == "A"
        assert payload["route"]["release_id"] == "v6-r44"
        assert payload["route"]["upstream_ref"].startswith("http://127.0.0.1:18090#sha256:")
        assert str(route_path) not in response.text

    asyncio.run(scenario())


def test_malformed_dynamic_route_sends_terminal_jivo_fallback(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        posts: list[dict] = []
        journal_rows: list[dict] = []

        class Response:
            status = 200

            async def read(self) -> bytes:
                return b"{}"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def post(self, url, *, data, **_kwargs):
                posts.append({"url": url, "payload": json.loads(data.decode("utf-8"))})
                return Response()

        route_path = tmp_path / "active-route.json"
        route_path.write_text('{"schema":"broken"}', encoding="utf-8")
        monkeypatch.setenv("NMBOT_ACTIVE_ROUTE_FILE", str(route_path))
        monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "TEST")
        monkeypatch.setenv("JIVO_PROVIDER_ID", "provider")
        monkeypatch.setattr(mod, "ClientSession", lambda timeout=None: Session())
        monkeypatch.setattr(mod, "_log_structured", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(mod, "append_journal_event", lambda **kwargs: journal_rows.append(kwargs))

        body = b'{"event":"CLIENT_MESSAGE","id":"e-route","site_id":"s","chat_id":"c","client_id":"u","message":{"text":"private"}}'
        trace = mod._request_trace(body)
        event_key = mod._event_key(trace)
        mod.LATEST_CHAT_EVENTS[event_key] = "e-route"

        await mod._send_to_jivo_after_bot("provider-token", body, trace, event_key, "e-route", "trace-route")

        assert len(posts) == 1
        assert posts[0]["url"].startswith("https://bot.jivosite.com/")
        assert posts[0]["payload"]["event"] == "BOT_MESSAGE"
        assert any(row["error_summary"]["codes"] == ["bridge_route_unavailable"] for row in journal_rows)
        assert all("private" not in json.dumps(row, ensure_ascii=False) for row in journal_rows)

    asyncio.run(scenario())
