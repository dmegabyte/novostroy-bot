from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPT_DIR / "nmbot_gateway_client.py"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("nmbot_gateway_client_provider_retry", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def json(self) -> dict[str, Any]:
        return self._payload

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None


class GatewaySession:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results
        self.posts: list[dict[str, Any]] = []
        self.created = 0
        self.closed = False

    def post(self, _url: str, *, json: dict[str, Any], headers: dict[str, Any]) -> FakeResponse:  # noqa: A002
        self.posts.append(json["request_data"])
        self.created += 1
        return FakeResponse({"id": f"task-{self.created}"}, status=201)

    def get(self, url: str, *, headers: dict[str, Any]) -> FakeResponse:
        parts = url.rstrip("/").split("/")
        task_id = parts[-2]
        idx = int(task_id.split("-")[-1]) - 1
        if url.endswith("/status"):
            return FakeResponse({"status": "completed"})
        return FakeResponse({"result": self.results[idx]})


class CreateFailureGatewaySession(GatewaySession):
    def __init__(self, task_payload: dict[str, Any]) -> None:
        super().__init__([])
        self.task_payload = task_payload

    def post(self, _url: str, *, json: dict[str, Any], headers: dict[str, Any]) -> FakeResponse:  # noqa: A002
        self.posts.append(json["request_data"])
        self.created += 1
        return FakeResponse(self.task_payload, status=500)


class TimeoutGatewaySession(GatewaySession):
    def get(self, url: str, *, headers: dict[str, Any]) -> FakeResponse:
        if url.endswith("/status"):
            return FakeResponse({"status": "running"})
        raise AssertionError("timeout scenario must not request a result")


class DelayedGatewaySession(GatewaySession):
    def __init__(self, results: list[dict[str, Any]]) -> None:
        super().__init__(results)
        self.status_calls = 0

    def get(self, url: str, *, headers: dict[str, Any]) -> FakeResponse:
        if url.endswith("/status"):
            self.status_calls += 1
            return FakeResponse({"status": "running" if self.status_calls == 1 else "completed"})
        return super().get(url, headers=headers)


async def _fake_ensure_session(session: GatewaySession) -> GatewaySession:
    return session


def _error_result(error: str) -> dict[str, Any]:
    return {"error": error, "metadata": {"raw_diagnostic": error}}


def _response_result(response: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"response": response, "metadata": metadata or {}}


def test_provider_error_classifier_is_allowlisted_and_ignores_empty_results() -> None:
    assert mod._provider_error_code("Corrupted thought signature") == "corrupted_thought_signature"
    assert mod._provider_error_code("provider returned INVALID_ARGUMENT") == "provider_invalid_argument"
    assert mod._provider_error_code("KeyError: 'choices' while parsing response") == "choices_response_parse"
    assert mod._provider_error_code("Provider returned error") is None
    assert mod._provider_error_code("rate limit 429") is None
    assert mod._provider_error_code("timeout") is None
    assert mod._provider_error_code({"action": "search", "facts": [], "near": []}) is None
    assert mod._provider_error_code("Нет подходящих ЖК по этим условиям") is None


def test_main_search_defaults_to_gpt55_instead_of_deepseek_fallback() -> None:
    assert mod.MAIN_SEARCH_FALLBACK_MODELS == (
        "google/gemini-3.5-flash",
        "openai/gpt-5.5",
    )
    assert mod.PROVIDER_ERROR_RETRY_MODEL == "deepseek/deepseek-v4-flash"


def test_response_payload_to_text_accepts_known_dict_text_keys_only() -> None:
    assert mod._response_payload_to_text({"response": "ответ"}) == "ответ"
    assert mod._response_payload_to_text({"text": "текст"}) == "текст"
    assert mod._response_payload_to_text({"content": "контент"}) == "контент"
    assert mod._response_payload_to_text({"answer": "ответ 2"}) == "ответ 2"
    assert mod._response_payload_to_text({"facts": []}) == ""


def test_gateway_success_metadata_includes_bounded_task_id_without_raw_payload(monkeypatch) -> None:
    async def scenario() -> None:
        session = GatewaySession([_response_result("ok text", {"kept": "yes"})])
        client = mod.OvermindClient()
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))

        text, meta = await client._run_gateway_request_once(
            {"_payload_stage": "conversation_answer", "query": "secret query", "service": "openrouter", "model": "google/gemini-2.5-flash", "system_prompt": "prompt"},
            {},
            5,
        )

        assert text == "ok text"
        assert meta["kept"] == "yes"
        assert meta["_gateway_task_id"] == "task-1"
        assert "request_data" not in meta
        assert "raw_payload" not in meta
        assert "secret query" not in json.dumps(meta, ensure_ascii=False)

    asyncio.run(scenario())


def test_gateway_forensic_log_is_opt_in_private_and_preserves_raw_result(monkeypatch, tmp_path) -> None:
    raw_output = '```json\n{"broken": true,}\n``` trailing'
    result = {"result": {"response": raw_output, "metadata": {"untrusted": "kept only in forensic"}}}
    forensic_dir = tmp_path / "forensic"
    monkeypatch.setattr(mod, "GATEWAY_FORENSIC_LOG_DIR", forensic_dir)
    monkeypatch.delenv("NMBOT_GATEWAY_FORENSIC_LOG_ENABLED", raising=False)

    mod._log_gateway_forensic_result(
        payload_stage="main_search", model="test-model", task_id="task-1", task_status="completed", result=result,
    )
    assert not forensic_dir.exists()

    monkeypatch.setenv("NMBOT_GATEWAY_FORENSIC_LOG_ENABLED", "true")
    mod._log_gateway_forensic_result(
        payload_stage="main_search", model="test-model", task_id="task-1", task_status="completed", result=result,
    )
    paths = list(forensic_dir.glob("gateway-result-*.jsonl"))
    assert len(paths) == 1
    row = json.loads(paths[0].read_text(encoding="utf-8"))
    assert row["raw_gateway_result"] == result
    assert row["raw_gateway_result"]["result"]["response"] == raw_output
    assert stat.S_IMODE(os.stat(forensic_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(paths[0]).st_mode) == 0o600


def test_gateway_forensic_log_rotates_without_truncating_and_cleans_matching_old_files(monkeypatch, tmp_path) -> None:
    forensic_dir = tmp_path / "forensic"
    monkeypatch.setattr(mod, "GATEWAY_FORENSIC_LOG_DIR", forensic_dir)
    monkeypatch.setenv("NMBOT_GATEWAY_FORENSIC_LOG_ENABLED", "1")
    monkeypatch.setenv("NMBOT_GATEWAY_FORENSIC_LOG_MAX_BYTES", "1")
    monkeypatch.setenv("NMBOT_GATEWAY_FORENSIC_LOG_RETENTION_DAYS", "1")
    forensic_dir.mkdir(mode=0o700)
    (forensic_dir / "gateway-result-2000-01-01.jsonl").write_text("old\n", encoding="utf-8")
    (forensic_dir / "not-forensic.jsonl").write_text("keep\n", encoding="utf-8")

    for response in ("first complete response", "second complete response"):
        mod._log_gateway_forensic_result(
            payload_stage="gateway", model="model", task_id="task-1", task_status="completed", result={"response": response},
        )

    rows = [json.loads(path.read_text(encoding="utf-8")) for path in forensic_dir.glob("gateway-result-*.jsonl")]
    assert sorted(row["raw_gateway_result"]["response"] for row in rows) == ["first complete response", "second complete response"]
    assert not (forensic_dir / "gateway-result-2000-01-01.jsonl").exists()
    assert (forensic_dir / "not-forensic.jsonl").exists()


def test_gateway_error_events_use_allowlisted_metadata_without_sensitive_previews(monkeypatch) -> None:
    forbidden_values = [
        "sk-fake-token",
        "+79991234567",
        "secret system prompt",
        "raw model output",
        "private query text",
    ]

    async def run_create_failure() -> tuple[dict[str, Any], dict[str, Any]]:
        session = CreateFailureGatewaySession({
            "id": "task:+79991234567",
            "error": "sk-fake-token +79991234567 secret system prompt private query text",
            "response": "raw model output",
            "metadata": {"raw_diagnostic": "private query text"},
        })
        client = mod.OvermindClient()
        logged: list[dict[str, Any]] = []
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))
        monkeypatch.setattr(mod, "_log_error_event", logged.append)

        _text, meta = await client._run_gateway_request_once(
            {"_payload_stage": "main_search", "query": "private query text", "service": "openrouter", "model": "google/gemini-2.5-flash", "system_prompt": "secret system prompt"},
            {"Authorization": "Bearer sk-fake-token"},
            5,
        )
        return logged[-1], meta

    async def run_result_failure() -> tuple[dict[str, Any], dict[str, Any]]:
        session = GatewaySession([{
            "error": "provider INVALID_ARGUMENT sk-fake-token +79991234567 secret system prompt private query text raw model output",
            "metadata": {"raw_diagnostic": "private query text", "query": "private query text"},
            "response": "raw model output",
        }])
        client = mod.OvermindClient()
        logged: list[dict[str, Any]] = []
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))
        monkeypatch.setattr(mod, "_log_error_event", logged.append)

        _text, meta = await client._run_gateway_request_once(
            {"_payload_stage": "main_search", "query": "private query text", "service": "openrouter", "model": "google/gemini-2.5-flash", "system_prompt": "secret system prompt"},
            {"Authorization": "Bearer sk-fake-token"},
            5,
        )
        return logged[-1], meta

    create_event, create_meta = asyncio.run(run_create_failure())
    result_event, result_meta = asyncio.run(run_result_failure())

    for event, meta in ((create_event, create_meta), (result_event, result_meta)):
        event_dump = json.dumps(event, ensure_ascii=False, sort_keys=True)
        meta_dump = json.dumps(meta, ensure_ascii=False, sort_keys=True)
        for forbidden in forbidden_values:
            assert forbidden not in event_dump
            assert forbidden not in meta_dump
        assert "payload_preview" not in event
        assert "exception" not in event
        assert "exception_type" in event
        assert event["severity"] == "error"
        assert event["stage"] in {"gateway_create_task", "gateway_result"}
        assert event["payload_stage"] == "main_search"
        assert isinstance(event["duration_ms"], int)
        assert set(event) <= {"error_type", "severity", "stage", "payload_stage", "task_id", "status", "duration_ms", "parse_status", "exception_type", "error_code", "payload_type", "payload_key_count", "payload_keys", "payload_item_count", "http_status"}

    assert create_event["http_status"] == 500
    assert result_event["task_id"] == "task-1"
    assert result_event["status"] == "completed"
    assert result_event["parse_status"] == "missing"
    assert result_meta["_provider_error_code"] == "provider_invalid_argument"


def test_formatter_stage_polls_each_second_without_changing_other_stages(monkeypatch) -> None:
    async def run_stage(stage: str) -> list[float]:
        session = DelayedGatewaySession([_response_result("ok")])
        client = mod.OvermindClient()
        sleeps: list[float] = []
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))

        async def fake_sleep(delay: float) -> None:
            sleeps.append(delay)

        monkeypatch.setattr(mod, "asyncio_sleep", fake_sleep)
        text, _meta = await client._run_gateway_request_once(
            {"_payload_stage": stage, "query": "q", "service": "openrouter", "model": "model", "system_prompt": "prompt"},
            {},
            5,
        )
        assert text == "ok"
        return sleeps

    assert asyncio.run(run_stage("conversation_answer_formatter")) == [1]
    assert asyncio.run(run_stage("conversation_answer_writer")) == [3]


def test_gateway_retries_provider_error_once_with_deepseek_and_preserves_payload(monkeypatch) -> None:
    async def scenario() -> None:
        session = GatewaySession([
            _error_result("OpenRouter provider error: Corrupted thought signature"),
            _response_result("deepseek ok", {"task_meta": "safe"}),
        ])
        client = mod.OvermindClient()
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))

        text, meta = await client._run_gateway_request(
            {
                "_payload_stage": "current_options_chat",
                "query": "тот же запрос",
                "service": "openrouter",
                "model": "google/gemini-2.5-flash",
                "system_prompt": "prompt",
                "parameters": {"temperature": 0.2},
                "reasoning": {"effort": "low"},
                "external_api_key": "test-key",
            },
            {"Authorization": "Bearer test"},
            5,
        )

        assert text == "deepseek ok"
        assert [post["model"] for post in session.posts] == ["google/gemini-2.5-flash", mod.PROVIDER_ERROR_RETRY_MODEL]
        assert session.posts[1]["query"] == "тот же запрос"
        assert session.posts[1]["parameters"] == {"temperature": 0.2}
        assert session.posts[1]["reasoning"] == {"exclude": True}
        assert meta["_provider_retry_attempted"] is True
        assert meta["_provider_retry_success"] is True
        assert meta["_provider_error_code"] == "corrupted_thought_signature"
        assert meta["_gateway_client_impl"].endswith(".OvermindClient")

    asyncio.run(scenario())


def test_gateway_does_not_retry_when_requested_model_is_already_deepseek(monkeypatch) -> None:
    async def scenario() -> None:
        session = GatewaySession([_error_result("provider INVALID_ARGUMENT: missing choices")])
        client = mod.OvermindClient()
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))

        text, meta = await client._run_gateway_request(
            {"query": "q", "service": "openrouter", "model": mod.PROVIDER_ERROR_RETRY_MODEL, "system_prompt": "p"},
            {},
            5,
        )

        assert len(session.posts) == 1
        assert text == mod.SAFE_UPSTREAM_ERROR_TEXT
        assert meta["_provider_retry_attempted"] is False
        assert meta["_provider_retry_model"] == mod.PROVIDER_ERROR_RETRY_MODEL
        assert meta["_gateway_client_impl"].endswith(".OvermindClient")

    asyncio.run(scenario())


def test_gateway_both_provider_attempts_fail_returns_safe_text_without_client_leak(monkeypatch) -> None:
    async def scenario() -> None:
        session = GatewaySession([
            _error_result("OpenRouter error: Corrupted thought signature and choices traceback"),
            _error_result("provider INVALID_ARGUMENT: KeyError 'choices' while parsing response"),
        ])
        client = mod.OvermindClient()
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))

        text, meta = await client._run_gateway_request(
            {"query": "q", "service": "openrouter", "model": "google/gemini-2.5-flash", "system_prompt": "p"},
            {},
            5,
        )

        assert len(session.posts) == 2
        assert text == mod.SAFE_UPSTREAM_ERROR_TEXT
        assert meta["_provider_retry_failed"] is True
        lowered = text.lower()
        for forbidden in ("openrouter", "corrupted thought signature", "choices", "json", "task"):
            assert forbidden not in lowered

    asyncio.run(scenario())


def test_empty_main_search_runs_parallel_fallback_race_and_uses_first_usable(monkeypatch) -> None:
    async def scenario() -> None:
        empty_search = json.dumps({"action": "search", "facts": [], "near": [], "params": {}}, ensure_ascii=False)
        usable_search = json.dumps({"facts": [{"name": "ЖК Зеленоград"}], "near": [], "params": {"location": "Зеленоград"}}, ensure_ascii=False)
        session = GatewaySession([_response_result(empty_search), _response_result(usable_search), _response_result(empty_search), _response_result(empty_search)])
        client = mod.OvermindClient()
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))

        text, meta = await client._run_gateway_request(
            {"_payload_stage": "main_search", "query": "q", "service": "openrouter", "model": "google/gemini-2.5-flash", "system_prompt": "p"},
            {},
            5,
        )

        assert json.loads(text)["facts"][0]["name"] == "ЖК Зеленоград"
        assert len(session.posts) == 4
        assert "_provider_retry_attempted" not in meta
        assert meta["_search_fallback_race"] is True
        assert meta["_search_fallback_model"] == "google/gemini-2.5-flash"
        assert meta["_search_fallback_models"] == ["google/gemini-2.5-flash", *mod.MAIN_SEARCH_FALLBACK_MODELS]
        assert [post["model"] for post in session.posts[1:]] == ["google/gemini-2.5-flash", *mod.MAIN_SEARCH_FALLBACK_MODELS]
        assert meta["_first_main_search_attempt"]["gateway_task_id"] == "task-1"
        assert meta["_first_main_search_attempt"]["parse_status"] == "ok"
        assert isinstance(meta["_first_main_search_attempt"]["duration_ms"], int)
        assert all(attempt.get("parse_status") in {"ok", "invalid_json", "missing"} for attempt in meta["_search_fallback_attempts"])
        assert meta["_gateway_client_impl"].endswith(".OvermindClient")

    asyncio.run(scenario())


def test_main_search_attempt_parse_status_distinguishes_missing_and_invalid(monkeypatch) -> None:
    async def scenario() -> None:
        client = mod.OvermindClient()

        missing_session = GatewaySession([_response_result("")])
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(missing_session))
        text, meta = await client._run_gateway_request_once(
            {"_payload_stage": "main_search", "query": "q", "service": "openrouter", "model": "model", "system_prompt": "p"},
            {},
            5,
        )
        assert text == mod.SAFE_UPSTREAM_ERROR_TEXT
        assert meta["_main_search_attempt"]["parse_status"] == "missing"

        invalid_session = GatewaySession([_response_result("```json\n{}\n```")])
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(invalid_session))
        text, meta = await client._run_gateway_request_once(
            {"_payload_stage": "main_search", "query": "q", "service": "openrouter", "model": "model", "system_prompt": "p"},
            {},
            5,
        )
        assert text == "```json\n{}\n```"
        assert meta["_main_search_attempt"]["parse_status"] == "invalid_json"
        assert "q" not in json.dumps(meta, ensure_ascii=False)

    asyncio.run(scenario())


def test_empty_main_search_recovers_when_primary_retry_is_usable_without_second_turn(monkeypatch) -> None:
    async def scenario() -> None:
        empty_search = json.dumps({"action": "search", "facts": [], "near": [], "params": {}}, ensure_ascii=False)
        usable_search = json.dumps({"facts": [{"name": "ЖК Первый"}], "near": [], "params": {}}, ensure_ascii=False)
        session = GatewaySession([_response_result(empty_search), _response_result(usable_search), _response_result(empty_search), _response_result(empty_search)])
        client = mod.OvermindClient()
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))

        text, meta = await client._run_gateway_request(
            {"_payload_stage": "main_search", "query": "q", "service": "openrouter", "model": "google/gemini-2.5-flash", "system_prompt": "p"},
            {},
            5,
        )

        assert json.loads(text)["facts"][0]["name"] == "ЖК Первый"
        assert [post["model"] for post in session.posts[1:]] == ["google/gemini-2.5-flash", *mod.MAIN_SEARCH_FALLBACK_MODELS]
        assert meta["_search_fallback_model"] == "google/gemini-2.5-flash"
        assert meta["_search_fallback_models"] == ["google/gemini-2.5-flash", *mod.MAIN_SEARCH_FALLBACK_MODELS]
        assert meta["_gateway_client_impl"].endswith(".OvermindClient")

    asyncio.run(scenario())


def test_malformed_main_search_runs_strict_fallback_and_rejects_fenced_fallback(monkeypatch) -> None:
    async def scenario() -> None:
        malformed = '```json\n{"facts": [{"name": "ЖК Фенс"}], "near": [], "params": {}}\n``` trailing'
        fenced_fallback = '```json\n{"facts": [{"name": "ЖК Не должен пройти"}], "near": [], "params": {}}\n```'
        usable_search = json.dumps({"facts": [{"name": "ЖК Строгий"}], "near": [], "params": {}}, ensure_ascii=False)
        session = GatewaySession([_response_result(malformed), _response_result(fenced_fallback), _response_result(usable_search), _response_result(fenced_fallback)])
        client = mod.OvermindClient()
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))

        text, meta = await client._run_gateway_request(
            {"_payload_stage": "main_search", "query": "q", "service": "openrouter", "model": "google/gemini-2.5-flash", "system_prompt": "p"},
            {},
            5,
        )

        assert json.loads(text)["facts"][0]["name"] == "ЖК Строгий"
        assert len(session.posts) == 4
        assert meta["_search_fallback_race"] is True
        assert meta["_first_attempt_malformed_search"] is True
        assert [attempt["ok"] for attempt in meta["_search_fallback_attempts"]].count(True) == 1

    asyncio.run(scenario())


def test_truncated_main_search_is_rejected_while_complete_fallback_succeeds(monkeypatch) -> None:
    async def scenario() -> None:
        truncated = '{"facts": [{"name": "ЖК Оборванный", "location": "Москва"}'
        complete_fallback = json.dumps({"facts": [{"name": "ЖК Полный"}], "near": [], "params": {}}, ensure_ascii=False)
        session = GatewaySession([
            _response_result(truncated),
            _response_result(complete_fallback),
            _response_result(truncated),
            _response_result(truncated),
        ])
        client = mod.OvermindClient()
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))

        assert mod._is_malformed_main_search_result(truncated) is True
        assert mod._is_usable_main_search_result(truncated) is False
        assert mod._is_usable_main_search_result(complete_fallback) is True

        text, meta = await client._run_gateway_request(
            {"_payload_stage": "main_search", "query": "q", "service": "openrouter", "model": "google/gemini-2.5-flash", "system_prompt": "p"},
            {},
            5,
        )

        assert json.loads(text)["facts"][0]["name"] == "ЖК Полный"
        assert meta["_search_fallback_race"] is True
        assert meta["_first_attempt_malformed_search"] is True

    asyncio.run(scenario())


def test_main_search_fallback_race_models_are_ordered_and_deduplicated(monkeypatch) -> None:
    async def scenario() -> None:
        empty_search = json.dumps({"action": "search", "facts": [], "near": [], "params": {}}, ensure_ascii=False)
        usable_search = json.dumps({"facts": [{"name": "ЖК Дедуп"}], "near": [], "params": {}}, ensure_ascii=False)
        monkeypatch.setattr(mod, "MAIN_SEARCH_FALLBACK_MODELS", ("google/gemini-2.5-flash", "", "deepseek/deepseek-v4-flash"))
        session = GatewaySession([_response_result(empty_search), _response_result(empty_search), _response_result(usable_search)])
        client = mod.OvermindClient()
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))

        text, meta = await client._run_gateway_request(
            {"_payload_stage": "main_search", "query": "q", "service": "openrouter", "model": "google/gemini-2.5-flash", "system_prompt": "p"},
            {},
            5,
        )

        assert json.loads(text)["facts"][0]["name"] == "ЖК Дедуп"
        assert [post["model"] for post in session.posts[1:]] == ["google/gemini-2.5-flash", "deepseek/deepseek-v4-flash"]
        assert meta["_search_fallback_models"] == ["google/gemini-2.5-flash", "deepseek/deepseek-v4-flash"]

    asyncio.run(scenario())


def test_empty_non_search_payload_does_not_trigger_main_search_fallback(monkeypatch) -> None:
    async def scenario() -> None:
        empty_search = json.dumps({"facts": [], "near": []}, ensure_ascii=False)
        session = GatewaySession([_response_result(empty_search)])
        client = mod.OvermindClient()
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))

        text, meta = await client._run_gateway_request(
            {"_payload_stage": "current_options_chat", "query": "q", "service": "openrouter", "model": "google/gemini-2.5-flash", "system_prompt": "p"},
            {},
            5,
        )

        assert json.loads(text)["facts"] == []
        assert len(session.posts) == 1
        assert "_search_fallback_race" not in meta

    asyncio.run(scenario())


def test_empty_main_search_with_no_usable_fallback_returns_safe_error(monkeypatch) -> None:
    async def scenario() -> None:
        empty_search = json.dumps({"facts": [], "near": [], "params": {"location": "Зеленоград"}}, ensure_ascii=False)
        session = GatewaySession([_response_result(empty_search), _response_result(empty_search), _response_result(empty_search), _response_result(empty_search)])
        client = mod.OvermindClient()
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))
        logged: list[dict[str, Any]] = []
        monkeypatch.setattr(mod, "_log_error_event", logged.append)

        text, meta = await client._run_gateway_request(
            {"_payload_stage": "main_search", "query": "q", "service": "openrouter", "model": "google/gemini-2.5-flash", "system_prompt": "p"},
            {},
            5,
        )

        assert text == mod.SAFE_UPSTREAM_ERROR_TEXT
        assert len(session.posts) == 4
        assert meta["_search_fallback_race"] is True
        assert meta["_fallback_race_no_usable"] is True
        assert meta["_search_fallback_models"] == ["google/gemini-2.5-flash", *mod.MAIN_SEARCH_FALLBACK_MODELS]
        assert len(meta["_search_fallback_attempts"]) == 3
        assert meta["_gateway_client_impl"].endswith(".OvermindClient")
        assert logged[-1]["error_type"] == "main_search_fallback_exhausted"
        assert logged[-1]["stage"] == "main_search_fallback"
        assert logged[-1]["attempted_models"] == ["google/gemini-2.5-flash", *mod.MAIN_SEARCH_FALLBACK_MODELS]
        assert "q" not in json.dumps(logged[-1], ensure_ascii=False)

    asyncio.run(scenario())


def test_main_search_timeout_runs_fallback_race_and_preserves_safe_metadata(monkeypatch) -> None:
    async def scenario() -> None:
        session = TimeoutGatewaySession([])
        client = mod.OvermindClient()
        monkeypatch.setattr(client, "ensure_session", lambda: _fake_ensure_session(session))
        logged: list[dict[str, Any]] = []
        monkeypatch.setattr(mod, "_log_error_event", logged.append)

        def unsafe_task_post(_url: str, *, json: dict[str, Any], headers: dict[str, Any]) -> FakeResponse:  # noqa: A002
            session.posts.append(json["request_data"])
            session.created += 1
            return FakeResponse({"id": "raw phone +7 999 123-45-67 token=secret"}, status=201)

        monkeypatch.setattr(session, "post", unsafe_task_post)

        text, meta = await client._run_gateway_request(
            {"_payload_stage": "main_search", "query": "q", "service": "openrouter", "model": "google/gemini-2.5-flash", "system_prompt": "p"},
            {},
            0,
        )

        assert text == mod.SAFE_UPSTREAM_ERROR_TEXT
        assert len(session.posts) == 4
        assert meta["_search_fallback_race"] is True
        assert meta["_fallback_race_no_usable"] is True
        assert meta["_first_attempt_safe"] is True
        assert len(meta["_search_fallback_attempts"]) == 3
        assert all(attempt["safe"] is True for attempt in meta["_search_fallback_attempts"])
        serialized = json.dumps(logged, ensure_ascii=False)
        assert "999" not in serialized
        assert "secret" not in serialized
        timeout_events = [event for event in logged if event.get("error_type") == "gateway_timeout"]
        assert timeout_events
        assert all("task_id" not in event for event in timeout_events)
        assert all(event.get("parse_status") == "missing" for event in timeout_events)

    asyncio.run(scenario())


def test_safe_upstream_error_text_offers_operator_consent_not_phone() -> None:
    assert "телефон" not in mod.SAFE_UPSTREAM_ERROR_TEXT.casefold()
    assert "номер" not in mod.SAFE_UPSTREAM_ERROR_TEXT.casefold()
    assert mod.SAFE_UPSTREAM_ERROR_TEXT.rstrip().endswith("Передать оператору запрос?")
