from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_gateway_task_diag.py"
spec = importlib.util.spec_from_file_location("nmbot_gateway_task_diag", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


class FakeUrlopen:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads
        self.requests: list[Any] = []

    def __call__(self, req: Any, timeout: int = 15) -> FakeResponse:
        self.requests.append(req)
        url = req.full_url
        kind = url.rstrip("/").split("/")[-1]
        return FakeResponse(self.payloads[kind])


def test_token_fallback_uses_gateway_poll_token() -> None:
    cfg = mod.load_config(env={"GATEWAY_POLL_TOKEN": "fallback-token"}, repo=Path("/tmp/does-not-exist"))

    assert cfg.token == "fallback-token"
    assert cfg.base_url == "https://overmind.aiaxel.ru"


def test_failed_data_case_maps_to_unknown_downstream_not_mcp_or_provider() -> None:
    result = mod.build_diagnosis(
        "2330033",
        {"task_id": 2330033, "status": "failed"},
        {"task_id": 2330033, "status": "failed", "result": None, "error_message": "'data'", "completed_at": "2026-07-20T00:00:00Z"},
    )

    assert result["error_code"] == "missing_response_field"
    assert result["category"] == "downstream_contract"
    assert result["layer"] == "unknown_downstream"
    assert result["status"] == "failed"
    assert result["result_present"] is False
    assert "error_message" not in json.dumps(result, ensure_ascii=False)
    assert "'data'" not in json.dumps(result, ensure_ascii=False)


def test_provider_and_mcp_layers_require_explicit_evidence() -> None:
    provider = mod.build_diagnosis("1", {"status": "failed"}, {"status": "failed", "error_message": "OpenRouter provider INVALID_ARGUMENT"})
    mcp = mod.build_diagnosis("2", {"status": "failed"}, {"status": "failed", "error_message": "MCP novostroym timeout"})
    unknown = mod.build_diagnosis("3", {"status": "failed"}, {"status": "failed", "error_message": "backend crashed"})

    assert provider["layer"] == "provider"
    assert provider["error_code"] == "explicit_provider_error"
    assert mcp["layer"] == "mcp"
    assert mcp["error_code"] == "explicit_mcp_error"
    assert unknown["layer"] == "unknown_downstream"
    assert unknown["error_code"] == "gateway_task_failed"


def test_output_omits_secret_raw_fields_and_includes_only_schema_keys() -> None:
    result = mod.build_diagnosis(
        "42",
        {"status": "completed", "request_data": {"query": "raw prompt"}, "safe_count": 1},
        {
            "status": "completed",
            "result": {"response": "model text", "metadata": {"token": "secret"}, "safe_shape": True},
            "Authorization": "Bearer secret",
        },
    )
    dumped = json.dumps(result, ensure_ascii=False)

    assert "raw prompt" not in dumped
    assert "model text" not in dumped
    assert "secret" not in dumped
    assert "request_data" not in result["schema"]["status_keys"]
    assert "Authorization" not in result["schema"]["result_keys"]
    assert "safe_count" in result["schema"]["status_keys"]
    assert "safe_shape" in result["schema"]["nested_result_keys"]


def test_event_correlation_returns_only_safe_fields(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bot_error_events-2026-07-20.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-07-20T01:02:03Z",
                "error_type": "gateway_empty_response",
                "stage": "gateway_poll",
                "task_id": "2330034",
                "task_status": "failed",
                "payload_preview": "raw user text and phone +79990000000",
                "token": "secret",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    events = mod.correlate_error_events("2330034", date="2026-07-20", logs_dir=logs)

    assert events == [
        {
            "error_type": "gateway_empty_response",
            "stage": "gateway_poll",
            "ts": "2026-07-20T01:02:03Z",
            "task_status": "failed",
            "matched": True,
        }
    ]
    assert "payload_preview" not in json.dumps(events, ensure_ascii=False)
    assert "79990000000" not in json.dumps(events, ensure_ascii=False)


def test_run_diagnostic_fetches_status_and_result_and_correlates_events(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bot_error_events-2026-07-20.jsonl").write_text(
        '{"task_id":"77","error_type":"gateway_empty_response","stage":"poll","ts":"2026-07-20T02:00:00Z"}\n',
        encoding="utf-8",
    )
    fake = FakeUrlopen(
        {
            "status": {"task_id": "77", "status": "failed"},
            "result": {"task_id": "77", "status": "failed", "result": None, "error_message": "'data'"},
        }
    )

    result = mod.run_diagnostic(
        "77",
        date="2026-07-20",
        repo=tmp_path,
        logs_dir=logs,
        env={"GATEWAY_POLL_TOKEN": "poll-token", "OVERMIND_URL": "https://example.test"},
        urlopen=fake,
    )

    assert result["task_id"] == "77"
    assert result["error_code"] == "missing_response_field"
    assert result["event_correlation"]["matched_count"] == 1
    assert [req.full_url for req in fake.requests] == [
        "https://example.test/api/v1/tasks/api/77/status",
        "https://example.test/api/v1/tasks/api/77/result",
    ]
    assert fake.requests[0].headers["Authorization"] == "Bearer poll-token"
