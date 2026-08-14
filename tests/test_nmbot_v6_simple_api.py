import asyncio
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from nmbot_v6.simple_gateway import SimpleGatewayResult
from scripts.nmbot_api_server import (
    JsonStateStore, RuntimeVersionStore, _effective_session_runtime_version,
    _mark_v6_bot_message_returned, _reset_state_for_session_runtime, build_jivo_bot_message,
)
from scripts.nmbot_runtime_adapter import run_runtime_turn
from scripts.nmbot_v6_simple_adapter import run_v6_simple_turn


class Port:
    def __init__(self, output): self.output, self.calls = output, 0
    async def run(self, payload, **kwargs): self.calls += 1; return SimpleGatewayResult(self.output, f"a{self.calls}")


class Store:
    def __init__(self, value=None): self.value, self.saves = value or {}, []
    async def get(self, key): return self.value
    async def save(self, key, value): self.value = value; self.saves.append(value)


def test_bot_message_wire_shape():
    inbound = {"client_id": "client", "chat_id": "chat"}
    event = build_jivo_bot_message(inbound, "Ответ")
    assert event["event"] == "BOT_MESSAGE"
    assert event["client_id"] == "client" and event["chat_id"] == "chat"
    assert event["message"]["text"] == "Ответ"


def test_start_namespace_override_preserves_other_namespaces(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")
    versions = RuntimeVersionStore(tmp_path / "version.json")
    app = {"state_store": store, "runtime_version_store": versions}
    asyncio.run(store.save("s", {"nmbot_v0": {"kept": True}, "nmbot_v6": {"old": True}}))
    assert asyncio.run(_reset_state_for_session_runtime(app, "s", "V6")) == "V6"
    value = asyncio.run(store.get("s"))
    assert value["nmbot_v0"] == {"kept": True}
    assert value["nmbot_v6"]["revision"] == 0
    assert value["runtime_version_override"] == "V6"
    assert asyncio.run(_reset_state_for_session_runtime(app, "s", "V0")) == "V0"
    assert asyncio.run(_effective_session_runtime_version(app, "s")) == "V0"


def test_legacy_runtime_is_not_dispatched_to_simple_v6(monkeypatch):
    called = {"v0": 0}
    async def fake_v0(*args, **kwargs): called["v0"] += 1; return {"ok": True, "meta": {"runtime": "v0"}}
    monkeypatch.setattr("scripts.nmbot_runtime_adapter._run_v0_authoritative", fake_v0)
    class Store:
        async def get(self, key): return {"runtime_version_override": "V0"}
    result = asyncio.run(run_runtime_turn({"state_store": Store()}, user_id="s", message="x", channel="jivo"))
    assert result["meta"]["runtime"] == "v0" and called["v0"] == 1


def test_v1_and_v4_selectors_remain_isolated_from_simple_v6(monkeypatch):
    called = []
    async def fake_v1(*args, **kwargs): called.append("v1"); return {"ok": True, "meta": {"runtime": "v1"}}
    async def fake_v4(*args, **kwargs): called.append("v4"); return {"ok": True, "meta": {"runtime": "v4"}}
    async def forbidden_v6(*args, **kwargs): raise AssertionError("V6 simple dispatched")
    monkeypatch.setattr("scripts.nmbot_runtime_adapter._run_v1_authoritative", fake_v1)
    monkeypatch.setattr("scripts.nmbot_runtime_adapter._run_v4_authoritative", fake_v4)
    monkeypatch.setattr("scripts.nmbot_runtime_adapter.run_v6_simple_turn", forbidden_v6)
    class VersionStore:
        def __init__(self, version): self.version = version
        async def get(self, key): return {"runtime_version_override": self.version}
    for version in ("V1", "V4"):
        result = asyncio.run(run_runtime_turn({"state_store": VersionStore(version)}, user_id="s", message="x", channel="jivo"))
        assert result["meta"]["runtime"] == version.casefold()
    assert called == ["v1", "v4"]


def test_actual_simple_operator_request_prepares_bot_message_without_handoff():
    p1 = Port({"action": "request_phone", "facts": [], "near": [], "missing": [], "params": {}, "ambiguity": None})
    p2 = Port({"action": "request_phone", "response": "", "final_question": ""})
    app = {"state_store": Store(), "v6_simple_prompt1_port": p1, "v6_simple_prompt2_port": p2}
    result = asyncio.run(run_v6_simple_turn(app, user_id="s", message="Позовите специалиста", channel="jivo"))
    event = build_jivo_bot_message({"client_id": "c", "chat_id": "h"}, result["answer"])
    assert event["event"] == "BOT_MESSAGE" and result["handoff_to_operator"] is False
    assert result["answer"] == "На какой номер вам позвонить?"
    assert p1.calls == 1 and p2.calls == 0
    stages = {item["stage"]: item["status"] for item in result["meta"]["v6_trace"]["stages"]}
    assert stages["prompt1"] == "accepted" and stages["prompt2"] == "not_called" and stages["state"] == "accepted"
    _mark_v6_bot_message_returned(result)
    assert result["meta"]["v6_trace"]["stages"][-1]["status"] == "returned"


def test_public_trace_omits_phone_shaped_attempt_reference():
    from scripts.nmbot_v6_simple_adapter import _trace
    trace = _trace("accepted", "not_called", "accepted", "accepted", p1_ref="79991234567", p2_ref="safe-attempt")
    rendered = str(trace)
    assert "79991234567" not in rendered and "safe-attempt" in rendered


def test_h138_safe_fallback_commits_public_offer_without_operator_handoff():
    class SequencePort:
        def __init__(self, values): self.values = list(values)
        async def run(self, payload, **kwargs):
            value = self.values.pop(0)
            if isinstance(value, Exception): raise value
            return SimpleGatewayResult(value, "safe-attempt")
    store = Store()
    app = {
        "state_store": store,
        "v6_simple_prompt1_port": SequencePort([{"action": "continue", "facts": [], "near": [], "missing": [], "params": {"unknown_source_key": "private-value"}, "ambiguity": None}]),
        "v6_simple_prompt2_port": SequencePort([RuntimeError("must not be called")]),
    }
    result = asyncio.run(run_v6_simple_turn(app, user_id="s", message="Обычный вопрос", channel="jivo"))
    rendered = json.dumps(result["meta"], ensure_ascii=False)
    assert result["ok"] is True and result["meta"]["state_commit"] is True and store.saves
    assert result["handoff_to_operator"] is False and result["awaiting_phone"] is False
    assert result["meta"]["v6_trace"]["failure_stage"] == "prompt1"
    assert result["meta"]["v6_trace"]["error_code"] == "invalid_param_key"
    assert result["meta"]["v6_trace"]["error_field"] == "unknown_source_key"
    assert result["meta"]["v6_trace"]["stages"][0]["status"] == "failed"
    assert "private-value" not in rendered and "must not be called" not in rendered and "Обычный вопрос" not in rendered


def test_related_consent_after_saved_safe_offer_is_owned_by_p1():
    offer = "Сейчас не удалось проверить базу по вашему запросу. Хотите, чтобы этот запрос проверил специалист?"
    store = Store({"nmbot_v6": {"schema_version": 2, "revision": 1, "history": [{"role": "user", "text": "двушка"}, {"role": "assistant", "text": offer}], "awaiting_phone": False, "client_turn_count": 1, "pending_offer": "specialist_contact"}})
    p1 = Port({"action": "request_phone", "facts": [], "near": [], "missing": [], "params": {}, "ambiguity": None})
    p2 = Port({"action": "reply", "response": "must not run", "final_question": ""})
    result = asyncio.run(run_v6_simple_turn({"state_store": store, "v6_simple_prompt1_port": p1, "v6_simple_prompt2_port": p2}, user_id="s", message="Да", channel="jivo"))
    assert result["answer"] == "На какой номер вам позвонить?" and p2.calls == 0
    stages = {item["stage"]: item["status"] for item in result["meta"]["v6_trace"]["stages"]}
    assert stages["prompt1"] == "accepted" and stages["prompt2"] == "not_called" and stages["state"] == "accepted"


def test_v6_reset_preserves_v0_v1_v4_namespaces(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")
    versions = RuntimeVersionStore(tmp_path / "version.json")
    app = {"state_store": store, "runtime_version_store": versions}
    kept = {"nmbot_v0": {"v": 0}, "nmbot_v1": {"v": 1}, "nmbot_v4": {"v": 4}}
    asyncio.run(store.save("s", {**kept, "nmbot_v6": {"old": True}}))
    asyncio.run(_reset_state_for_session_runtime(app, "s", "V6"))
    value = asyncio.run(store.get("s"))
    assert {key: value[key] for key in kept} == kept


def test_adapter_has_only_simple_v6_graph_and_preserves_selector_dispatch():
    adapter_path = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_runtime_adapter.py"
    tree = ast.parse(adapter_path.read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    definitions = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert not ({"_run_v6_authoritative", "_isolated_v6_envelope", "_is_phone_consent"} & definitions)
    assert not ({"V6State", "V6Runtime", "RuntimeStatus", "RuntimeFailureStage"} & names)

    dispatcher = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_runtime_turn"
    )
    called = {
        node.func.id for node in ast.walk(dispatcher)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"run_v6_simple_turn", "_run_v0_authoritative", "_run_v1_authoritative", "_run_v4_authoritative"} <= called
