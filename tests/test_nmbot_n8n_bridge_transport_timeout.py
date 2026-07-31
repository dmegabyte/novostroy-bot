from __future__ import annotations

import asyncio
import ast
import hashlib
import importlib.util
import json
import subprocess
import time
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_n8n_bridge_server.py"
TRACE_ANALYZER = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_jivo_trace_analyze.py"
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
        delivery_rows: list[tuple[str, str]] = []

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
        monkeypatch.setattr(mod, "_append_delivery_trace", lambda _trace_id, stage, outcome, **_fields: delivery_rows.append((stage, outcome)))

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
        assert returned[-1]["outcome"] == "status_send_accepted"
        assert returned[-1]["delivery_role"] == "status"
        assert returned[-1]["delivery_status"] == "status_send_accepted"
        assert returned[-1]["terminal"] is False
        assert delivery_rows == []

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
        assert returned[-1]["outcome"] == "terminal_send_accepted"
        assert returned[-1]["delivery_role"] == "final"
        assert returned[-1]["delivery_status"] == "terminal_send_accepted"
        assert returned[-1]["client_delivery_status"] == "client_delivery_unconfirmed"
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


def test_duplicate_callback_returns_original_trace_ref_and_creates_no_second_task(monkeypatch) -> None:
    async def scenario() -> None:
        mod.DISPATCHED_CHAT_EVENTS.clear()
        app = mod.create_app()
        created_tasks: list[str] = []
        lifecycle_trace_ids: list[str] = []
        delivery_trace_ids: list[str] = []
        body = b'{"event":"CLIENT_MESSAGE","id":"same-event","site_id":"s","chat_id":"c","client_id":"u","message":{"text":"private request"}}'

        def request():
            value = type("Request", (), {})()
            value.app = app
            value.headers = {"X-NMBOT-Bridge-Token": "bridge"}
            value.match_info = {"provider_token": "provider"}

            async def read() -> bytes:
                return body

            value.read = read
            return value

        def track_task(_app, coro, trace_id, _trace):
            created_tasks.append(trace_id)
            lifecycle_trace_ids.append(trace_id)
            coro.close()

        monkeypatch.setenv("NMBOT_N8N_BRIDGE_TOKEN", "bridge")
        monkeypatch.setenv("JIVO_PROVIDER_TOKEN", "provider")
        monkeypatch.setattr(mod, "_track_task", track_task)
        monkeypatch.setattr(mod, "_append_delivery_trace", lambda trace_id, *_args, **_kwargs: delivery_trace_ids.append(trace_id))
        monkeypatch.setattr(mod, "_log_structured", lambda *_args, **_kwargs: None)

        first = await mod.handle_proxy(request())
        second = await mod.handle_proxy(request())
        first_ref = json.loads(first.text)["trace_ref"]
        second_ref = json.loads(second.text)["trace_ref"]

        assert first_ref == mod._safe_trace_ref(lifecycle_trace_ids[0])
        assert first_ref == mod._safe_trace_ref(delivery_trace_ids[0])
        assert second_ref == first_ref
        assert len(created_tasks) == 1

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


def test_cancelled_outbound_delivery_records_one_safe_terminal_and_reraises(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        upstream_cancelled = asyncio.Event()

        class SlowResponse:
            status = 200

            async def read(self) -> bytes:
                started.set()
                try:
                    await asyncio.sleep(60)
                finally:
                    upstream_cancelled.set()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def post(self, *_args, **_kwargs):
                return SlowResponse()

        monkeypatch.setattr(mod, "ClientSession", lambda timeout=None: Session())
        monkeypatch.setattr(mod, "DELIVERY_TRACE_PATH", tmp_path / "delivery.jsonl")
        body = b'{"event":"CLIENT_MESSAGE","id":"event-secret","site_id":"site-secret","chat_id":"chat-secret","client_id":"client-secret","message":{"text":"private request"}}'
        trace = mod._request_trace(body)
        event_key = mod._event_key(trace)
        mod.LATEST_CHAT_EVENTS[event_key] = "event-secret"
        task = asyncio.create_task(
            mod._send_to_jivo_after_bot("provider-token", body, trace, event_key, "event-secret", "raw-trace")
        )
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("outbound cancellation must be re-raised")
        await asyncio.wait_for(upstream_cancelled.wait(), timeout=0.1)

        rows = [json.loads(line) for line in (tmp_path / "delivery.jsonl").read_text(encoding="utf-8").splitlines()]
        terminal = [row for row in rows if row["stage"] == "terminal_delivery"]
        assert len(terminal) == 1
        assert terminal[0]["outcome"] == "not_sent"
        assert terminal[0]["error_class"] == "cancelled"
        assert set(terminal[0]) == {
            "schema", "ts", "trace_ref", "stage", "outcome", "terminal_event", "client_delivery_status", "error_class",
            "api_status", "jivo_status", "api_latency_ms", "jivo_latency_ms", "e2e_latency_ms",
        }
        dumped = json.dumps(rows, ensure_ascii=False)
        for forbidden in ("raw-trace", "private request", "event-secret", "site-secret", "chat-secret", "client-secret", "provider-token"):
            assert forbidden not in dumped

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


def test_upstream_exception_after_sent_status_emits_one_closed_terminal_trace(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        class StatusResponse:
            status = 200

            async def read(self) -> bytes:
                return b"{}"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class FailingUpstreamResponse(StatusResponse):
            async def read(self) -> bytes:
                await asyncio.sleep(0.02)
                raise RuntimeError("private upstream failure")

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def post(self, url, **_kwargs):
                return FailingUpstreamResponse() if url.startswith("http://127.0.0.1") else StatusResponse()

        monkeypatch.setenv("JIVO_PROVIDER_ID", "provider")
        monkeypatch.setenv("JIVO_API_ENDPOINT_BASE", "https://bot.jivosite.com/webhooks")
        monkeypatch.setenv("NMBOT_BRIDGE_TIMEOUT_SECONDS", "0.01")
        monkeypatch.setenv("NMBOT_BRIDGE_HARD_TIMEOUT_SECONDS", "0.1")
        monkeypatch.setattr(mod, "ClientSession", lambda timeout=None: Session())
        monkeypatch.setattr(mod, "DELIVERY_TRACE_PATH", tmp_path / "delivery.jsonl")
        body = b'{"event":"CLIENT_MESSAGE","id":"e1","site_id":"s","chat_id":"c","client_id":"u","message":{"text":"private request"}}'
        trace = mod._request_trace(body)
        event_key = mod._event_key(trace)
        mod.LATEST_CHAT_EVENTS[event_key] = "e1"

        await mod._send_to_jivo_after_bot("provider-token", body, trace, event_key, "e1", "raw-trace", request_started=time.monotonic())

        rows = [json.loads(line) for line in (tmp_path / "delivery.jsonl").read_text(encoding="utf-8").splitlines()]
        terminal = [row for row in rows if row["stage"] == "terminal_delivery"]
        assert len(terminal) == 1
        assert terminal[0]["outcome"] == "not_sent"
        assert terminal[0]["terminal_event"] == "NONE"
        assert terminal[0]["error_class"] == "api_exception"
        assert "private upstream failure" not in json.dumps(rows, ensure_ascii=False)

    asyncio.run(scenario())


def test_status_update_then_final_delivery_projection_passes_strict_lifecycle(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        jivo_posts: list[str] = []

        class Response:
            status = 202

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
                if url.startswith("http://127.0.0.1"):
                    return type("ApiResponse", (Response,), {
                        "status": 200,
                        "read": lambda self: _delayed_async_bytes(b'{"event":"BOT_MESSAGE","message":{"text":"private response"}}'),
                    })()
                jivo_posts.append(json.loads(data.decode("utf-8"))["event"])
                return Response()

        monkeypatch.setenv("JIVO_PROVIDER_ID", "provider")
        monkeypatch.setenv("NMBOT_BRIDGE_TIMEOUT_SECONDS", "0.005")
        monkeypatch.setenv("NMBOT_BRIDGE_HARD_TIMEOUT_SECONDS", "1")
        monkeypatch.setenv("NMBOT_BRIDGE_STATUS_UPDATES_ENABLED", "on")
        monkeypatch.setenv("NMBOT_BRIDGE_STATUS_INTERVAL_SECONDS", "0.005")
        monkeypatch.setattr(mod, "ClientSession", lambda timeout=None: Session())
        monkeypatch.setattr(mod, "DELIVERY_TRACE_PATH", tmp_path / "delivery.jsonl")
        raw_trace = "123e4567-e89b-12d3-a456-426614174000"
        body = b'{"event":"CLIENT_MESSAGE","id":"event-secret","site_id":"site-secret","chat_id":"chat-secret","client_id":"client-secret","message":{"text":"private request"}}'
        trace = mod._request_trace(body)
        event_key = mod._event_key(trace)
        mod.LATEST_CHAT_EVENTS[event_key] = "event-secret"
        mod._append_delivery_trace(raw_trace, "bridge_accepted", "accepted", e2e_latency_ms=0)
        await mod._send_to_jivo_after_bot("provider-token", body, trace, event_key, "event-secret", raw_trace, request_started=time.monotonic())

        rows = [json.loads(line) for line in (tmp_path / "delivery.jsonl").read_text(encoding="utf-8").splitlines()]
        # At least one status send is preserved before the final BOT_MESSAGE;
        # the precise count varies with the repeat interval and scheduler.
        assert len(jivo_posts) >= 2
        assert all(event == "BOT_MESSAGE" for event in jivo_posts)
        assert [row["stage"] for row in rows] == [
            "bridge_accepted", "api_completed", "terminal_selected", "jivo_send_attempted", "jivo_response", "terminal_delivery",
        ]
        assert all(row["trace_ref"] == mod._safe_trace_ref(raw_trace) for row in rows)
        assert rows[-1]["outcome"] == "terminal_send_accepted"
        assert rows[-1]["terminal_event"] == "BOT_MESSAGE"
        assert rows[-1]["jivo_status"] == 202
        assert set(rows[-1]) == {
            "schema", "ts", "trace_ref", "stage", "outcome", "terminal_event", "client_delivery_status", "error_class",
            "api_status", "jivo_status", "api_latency_ms", "jivo_latency_ms", "e2e_latency_ms",
        }
        dumped = json.dumps(rows, ensure_ascii=False)
        for forbidden in (raw_trace, "private request", "private response", "event-secret", "site-secret", "chat-secret", "client-secret", "provider-token"):
            assert forbidden not in dumped

        strict = subprocess.run(
            ["python3", str(TRACE_ANALYZER), str(tmp_path / "delivery.jsonl"), "--strict", "--json"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert strict.returncode == 0, strict.stderr
        assert json.loads(strict.stdout)["summary"]["violations"] == 0

    async def _delayed_async_bytes(value: bytes) -> bytes:
        await asyncio.sleep(0.02)
        return value

    asyncio.run(scenario())


def test_delivery_projection_write_failure_is_nonfatal(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
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

        blocked_path = tmp_path / "not-a-file"
        blocked_path.mkdir()
        monkeypatch.setenv("JIVO_PROVIDER_ID", "provider")
        monkeypatch.setattr(mod, "DELIVERY_TRACE_PATH", blocked_path)
        status, error = await mod._post_event_to_jivo(Session(), "token", b'{"event":"BOT_MESSAGE"}', "raw-trace", {})
        assert (status, error) == (200, None)

    asyncio.run(scenario())
