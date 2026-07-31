from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from aiohttp import web


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
SCRIPT = SCRIPT_DIR / "nmbot_api_server.py"
sys.path.insert(0, str(SCRIPT_DIR))
import nmbot_egress_policy as egress_policy
spec = importlib.util.spec_from_file_location("nmbot_api_server_v4_selector_test", SCRIPT)
api = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["nmbot_api_server_v4_selector_test"] = api
spec.loader.exec_module(api)


class Store:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.states = {"u": dict(initial or {})}
        self.saved: list[tuple[str, dict[str, Any]]] = []

    async def get(self, user_id: str) -> dict[str, Any]:
        return self.states.setdefault(user_id, {})

    async def save(self, user_id: str, state: dict[str, Any]) -> None:
        self.states[user_id] = dict(state)
        self.saved.append((user_id, dict(state)))


class RuntimeVersionStore:
    def __init__(self, version: str = "V2") -> None:
        self.version = version

    async def get(self) -> str:
        return self.version


def make_app(initial: dict[str, Any] | None = None, *, active: str = "V2") -> web.Application:
    app = web.Application()
    app["state_store"] = Store(initial)
    app["runtime_version_store"] = RuntimeVersionStore(active)
    return app


def test_v4_start_command_and_identity_are_local_selector_only() -> None:
    assert api._normalize_runtime_version("V4") == "V4"
    assert api._is_start_command("/start_4") is True
    assert api._start_command_version("/start_4") == "V4"
    assert api.RUNTIME_IDENTITIES["V4"]["state_namespace"] == "nmbot_v4"
    assert api._start_command_version("/start") is None
    greeting = api.RUNTIME_IDENTITIES["V4"]["start_greeting"]
    assert "формате V4" not in greeting
    assert "JSON" not in greeting
    assert "API" not in greeting


def test_v4_egress_policy_blocks_version_markers_and_preserves_start_4_rule() -> None:
    decorated = egress_policy.sanitize_client_text("Здравствуйте\nСейчас активна версия: V4.", profile="client_production")
    assert decorated.blocked is False
    assert "V4" not in decorated.text

    marker = egress_policy.sanitize_client_text("Технический маркер V4", profile="client_production")
    assert marker.blocked is True
    assert marker.blocker_code == "runtime_version_marker"

    start = egress_policy.sanitize_client_text("/start_4", profile="client_production")
    assert start.blocked is True
    assert start.blocker_code == "start_version_marker"


def test_v4_session_reset_preserves_v0_v1_v2_and_resets_only_v4_outside_production() -> None:
    async def scenario() -> None:
        initial = {
            "nmbot_v0": {"keep": "v0"},
            "nmbot_v1": {"keep": "v1"},
            "nmbot_v2": {"keep": "v2"},
            "nmbot_v4": {"last_valid_ids": [9], "last_message_summary": "old"},
        }
        app = make_app(initial)
        version = await api._reset_state_for_session_runtime(app, "u", "V4")
        assert version == "V4"
        saved = app["state_store"].states["u"]
        assert saved["nmbot_v0"] == {"keep": "v0"}
        assert saved["nmbot_v1"] == {"keep": "v1"}
        assert saved["nmbot_v2"] == {"keep": "v2"}
        assert saved["nmbot_v4"]["last_valid_ids"] == []
        assert saved["runtime_version_override"] == "V4"

    asyncio.run(scenario())


def test_client_production_session_override_is_removed_and_active_runtime_wins(monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app({"runtime_version_override": "V4", "nmbot_v4": {"last_valid_ids": [1]}}, active="V2")
        monkeypatch.setattr(api, "is_client_production", lambda: True)
        version = await api._effective_session_runtime_version(app, "u")
        assert version == "V2"
        assert "runtime_version_override" not in app["state_store"].states["u"]
        assert app["state_store"].saved[-1][1] == {"nmbot_v4": {"last_valid_ids": [1]}}

    asyncio.run(scenario())


def test_v4_config_exception_returns_strict_json_without_changing_v1(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("NMBOT_CONTOUR_PROFILE", "client_production")
        v4_app = web.Application()
        v4_app["runtime_version_store"] = RuntimeVersionStore("V4")
        v4_result = await api.run_runtime_turn(v4_app, user_id="u", message="Подбери", channel="jivo")
        parsed = json.loads(v4_result["answer"])
        assert set(parsed) == {"data", "message"}
        assert parsed["data"] == []
        assert v4_result["answer_kind"] == "v4_strict_json"
        assert v4_result["meta"]["runtime"] == "v4"
        assert v4_result["meta"]["call_count"] == 0

        v1_app = web.Application()
        v1_app["runtime_version_store"] = RuntimeVersionStore("V1")
        v1_result = await api.run_runtime_turn(v1_app, user_id="u", message="Подбери", channel="jivo")
        assert v1_result["answer"] == "Сейчас не получилось продолжить подбор. Попробуйте написать ещё раз — я не буду менять условия, чтобы ничего не сбить."
        assert v1_result["meta"]["runtime"] == "v1"
        assert v1_result.get("answer_kind") != "v4_strict_json"

    asyncio.run(scenario())
