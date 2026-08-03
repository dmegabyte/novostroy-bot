from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from nmbot_runtime_contract.wire import CONTRACT_VERSION
from nmbot_v3.contracts import IntentPlanV3
from nmbot_v3.evidence_contract import CanonicalCard, EvidenceResult
from nmbot_v3.runtime import run_turn
from nmbot_v3.service import build_turn, create_app
from nmbot_v3.state import V3ConversationState


def request(message: str = "подбери квартиру") -> dict[str, Any]:
    return {"contract_version": CONTRACT_VERSION, "runtime_version": "V3", "conversation_ref": "conversation:123",
            "trace_ref": "trace:12345678", "message": message, "channel": "api", "meta": {}}


class Planner:
    async def plan(self, request):
        assert request.context.visible_option_refs == ()
        return IntentPlanV3(schema_version=3, goal="new_search", viewpoint="life", requested_facts=("metro",))


class BadPlanner:
    async def plan(self, _request):
        raise RuntimeError("provider failure")


class RecordingPlanner:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def plan(self, request):
        self.messages.append(request.user_text.text)
        return IntentPlanV3(schema_version=3, goal="new_search", viewpoint="life", requested_facts=("price",))


class Evidence:
    async def search(self, request):
        return EvidenceResult(facts=(CanonicalCard("ЖК Тест", {fact: "подтверждено" for fact in request.requested_facts}),))


def test_v3_package_wide_import_closure_isolated_and_planner_has_no_generic_fallback() -> None:
    banned = ("nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4", "scripts", "followup_intent_classifier")
    for source_path in Path("nmbot_v3").rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        from_imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(
            name == blocked or name.startswith(blocked + ".")
            for name in imports + from_imports
            for blocked in banned
        ), source_path
        if source_path.name == "runtime.py":
            attributes = [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)]
            names = [node.id for node in ast.walk(tree) if isinstance(node, ast.Name)]
            assert "V3CompositionRoot" in names and "plan_v3" not in attributes


def test_missing_phone_and_invalid_planner_do_not_mutate_or_leak() -> None:
    async def scenario() -> None:
        initial = V3ConversationState.clean().to_dict()
        missing = await build_turn()(request(), initial)
        missing_evidence = await build_turn(planner_port=Planner())(request(), initial)
        phone = await build_turn(planner_port=Planner(), evidence_port=Evidence())(request("номер +7 999 111-22-33"), initial)
        invalid = await build_turn(planner_port=BadPlanner(), evidence_port=Evidence())(request(), initial)
        corrupt = await build_turn(planner_port=Planner(), evidence_port=Evidence())(request(), {})
        assert missing.state is None and missing.response["error_code"] == "missing_v3_planner_port"
        assert missing_evidence.state is None and missing_evidence.response["error_code"] == "missing_v3_evidence_port"
        assert phone.state is None and phone.response["error_code"] == "v3_phone_flow_unmigrated"
        assert invalid.state is None and invalid.response["error_code"] == "v3_composition_rejected"
        assert corrupt.state is None and corrupt.response["error_code"] == "v3_composition_invalid"
        for result in (phone.response, invalid.response):
            assert "+7" not in json.dumps(result, ensure_ascii=False)
            assert "SECRET" not in json.dumps(result, ensure_ascii=False)
        direct = await run_turn("input SECRET", initial, BadPlanner(), Evidence())
        assert direct.state == initial and direct.safe_code == "v3_composition_rejected"
    asyncio.run(scenario())


def test_v3_phone_guard_allows_budgets_and_rejects_actual_phone() -> None:
    async def scenario() -> None:
        planner = RecordingPlanner()
        turn = build_turn(planner_port=planner, evidence_port=Evidence())
        initial = V3ConversationState.clean().to_dict()
        for message in ("Бюджет до 10000000 рублей", "Бюджет до 10 000 000 рублей"):
            result = await turn(request(message), initial)
            assert result.response["ok"] is True
            assert result.response["client_answer"].startswith("Нашла подтверждённые варианты.")
        phone = await turn(request("Мой номер 8 (999) 111-22-33"), initial)
        assert planner.messages == ["Бюджет до 10000000 рублей", "Бюджет до 10 000 000 рублей"]
        assert phone.state is None and phone.response["error_code"] == "v3_phone_flow_unmigrated"
    asyncio.run(scenario())


def test_v3_valid_plan_updates_direct_v3_state() -> None:
    async def scenario() -> None:
        result = await run_turn("подбери", None, Planner(), Evidence())
        assert result.safe_code is None and result.state == {"schema_version": "V3", "revision": 1,
            "stage": "answered", "last_action": "respond", "visible_option_refs": [],
            "pending_followup_key": None, "has_pending_action": False}
    asyncio.run(scenario())


def test_v3_runtime_reconstructs_current_options_context_from_its_own_state() -> None:
    class ContextPlanner:
        def __init__(self) -> None:
            self.contexts = []

        async def plan(self, request):
            self.contexts.append(request.context)
            if not request.context.visible_option_refs:
                return IntentPlanV3(schema_version=3, goal="new_search", viewpoint="life", requested_facts=("metro",))
            return IntentPlanV3(schema_version=3, goal="answer_current", viewpoint="life", requested_facts=("metro",))

    class ContextEvidence:
        async def search(self, request):
            refs = request.current_option_refs or (
                "550e8400-e29b-41d4-a716-446655440000",
                "550e8400-e29b-41d4-a716-446655440001",
            )
            names = ("ЖК Лучи", "ЖК Румянцево")
            return EvidenceResult(facts=tuple(
                CanonicalCard(names[index], {"metro": "Солнцево"}, reference)
                for index, reference in enumerate(refs)
            ))

    async def scenario() -> None:
        planner = ContextPlanner()
        first = await run_turn("подбери", None, planner, ContextEvidence())
        second = await run_turn("что с метро?", first.state, planner, ContextEvidence())
        assert first.safe_code is None and second.safe_code is None
        assert planner.contexts[0].visible_option_refs == ()
        assert planner.contexts[1].visible_option_refs == (
            "550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b-41d4-a716-446655440001",
        )
        assert second.state["visible_option_refs"] == [
            "550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b-41d4-a716-446655440001",
        ]
        assert "message" not in second.state and "v2" not in str(second.state).lower()
    asyncio.run(scenario())


def test_v3_http_rejected_composition_preserves_existing_state_and_contract(tmp_path: Path) -> None:
    class ToggleEvidence:
        def __init__(self) -> None:
            self.fail = False

        async def search(self, request):
            if self.fail:
                raise RuntimeError("evidence unavailable")
            return EvidenceResult(facts=(CanonicalCard("ЖК Тест", {fact: "подтверждено" for fact in request.requested_facts}),))

    async def scenario() -> None:
        evidence = ToggleEvidence()
        state_path = tmp_path / "state.json"
        client = TestClient(TestServer(create_app(
            state_path=state_path, journal_path=tmp_path / "journal.jsonl", token="internal",
            release_identity="v3-test-noop", planner_port=Planner(), evidence_port=evidence,
        )))
        await client.start_server()
        try:
            headers = {"Authorization": "Bearer internal"}
            accepted = await client.post("/api/chat", json=request(), headers=headers)
            accepted_body = await accepted.json()
            assert accepted.status == 200 and accepted_body["ok"] is True
            assert set(accepted_body) == {"contract_version", "ok", "runtime_version", "error_code", "diagnostics", "client_answer", "handoff"}
            before = state_path.read_text(encoding="utf-8")
            evidence.fail = True
            rejected = await client.post("/api/chat", json=request("ещё варианты"), headers=headers)
            rejected_body = await rejected.json()
            assert rejected.status == 200 and rejected_body["ok"] is False
            assert rejected_body["error_code"] == "v3_composition_rejected"
            assert state_path.read_text(encoding="utf-8") == before
        finally:
            await client.close()
    asyncio.run(scenario())


def test_v3_http_auth_version_state_reset_journal_and_release(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_path, journal_path = tmp_path / "state.json", tmp_path / "journal.jsonl"
        client = TestClient(TestServer(create_app(state_path=state_path, journal_path=journal_path, token="internal",
            release_identity="v3-test-release", planner_port=Planner(), evidence_port=Evidence())))
        await client.start_server()
        headers = {"Authorization": "Bearer internal"}
        try:
            assert (await (await client.get("/health")).json())["release_identity"] == "v3-test-release"
            unauthorized = await client.post("/api/chat", json=request())
            assert unauthorized.status == 401
            wrong = request(); wrong["runtime_version"] = "V2"
            assert (await client.post("/api/chat", json=wrong, headers=headers)).status == 400
            response = await client.post("/api/chat", json=request("SECRET"), headers=headers)
            assert response.status == 200 and (await response.json())["runtime_version"] == "V3"
            stored = json.loads(state_path.read_text(encoding="utf-8"))
            assert stored["conversation:123"]["schema_version"] == "V3" and stored["conversation:123"]["revision"] == 1
            reset_payload = {key: value for key, value in request().items() if key not in {"message", "channel", "meta"}}
            assert (await (await client.post("/api/reset", json=reset_payload, headers=headers)).json())["reset"] is True
            assert json.loads(state_path.read_text(encoding="utf-8"))["conversation:123"] == V3ConversationState.clean().to_dict()
            journal = journal_path.read_text(encoding="utf-8")
            assert "SECRET" not in journal and "internal" not in journal and '"runtime_version":"V3"' in journal
        finally:
            await client.close()
    asyncio.run(scenario())


def test_private_v3_worker_uses_injected_ports_and_persists_only_v3_state(tmp_path: Path) -> None:
    class RecordingPrivatePlanner:
        def __init__(self) -> None:
            self.calls = 0

        async def plan(self, _request):
            self.calls += 1
            return IntentPlanV3(schema_version=3, goal="new_search", viewpoint="life", requested_facts=("metro",))

    async def scenario() -> None:
        planner = RecordingPrivatePlanner()
        worker = TestServer(create_app(
            state_path=tmp_path / "v3-private-state.json",
            journal_path=tmp_path / "v3-private-journal.jsonl",
            token="v3-private-token",
            release_identity="v3-private-worker-test",
            planner_port=planner,
            evidence_port=Evidence(),
        ))
        client = TestClient(worker)
        await client.start_server()
        try:
            forwarded = await client.post("/api/chat", json=request(), headers={"Authorization": "Bearer v3-private-token"})
            assert forwarded.status == 200
            assert (await forwarded.json())["runtime_version"] == "V3"
            assert planner.calls == 1
            stored = json.loads((tmp_path / "v3-private-state.json").read_text(encoding="utf-8"))
            assert stored["conversation:123"]["schema_version"] == "V3"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_v3_entrypoint_rejects_missing_or_placeholder_release_before_binding(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("nmbot_v3_entrypoint_test", Path("scripts/nmbot_v3_service.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for value in (None, "", "local-v1", "local-v3", "local-v42", "replace-with-immutable-release-id", "REPLACE-WITH-IMMUTABLE-RELEASE-ID"):
        if value is None:
            monkeypatch.delenv("NMBOT_V3_RELEASE_ID", raising=False)
        else:
            monkeypatch.setenv("NMBOT_V3_RELEASE_ID", value)
        with __import__("pytest").raises(ValueError, match="invalid_release_identity"):
            module.main()
    monkeypatch.setenv("NMBOT_V3_RELEASE_ID", "v3-20260801-immutable")
    with __import__("pytest").raises(ValueError, match="missing_v3_gateway_config:NMBOT_V3_GATEWAY_URL"):
        module.main()
