from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from nmbot_v3.composition import V3CompositionInput, V3CompositionRoot
from nmbot_v3.contracts import IntentPlanV3, V3PlannerContext
from nmbot_v3.evidence_contract import CanonicalCard, EvidenceResult
from nmbot_v3.ports import V3ProviderError, V3ProviderErrorCode, V3RedactedText
from nmbot_v3.state import V3ConversationState


def _plan() -> IntentPlanV3:
    return IntentPlanV3(schema_version=3, goal="new_search", viewpoint="life", requested_facts=("metro",))


def _turn() -> V3CompositionInput:
    return V3CompositionInput(V3RedactedText("Подберите квартиру; token=hidden"), V3PlannerContext())


def test_composition_runs_injected_ports_and_commits_only_accepted_turn() -> None:
    async def scenario() -> None:
        seen = []

        class Planner:
            async def plan(self, request):
                seen.append(request)
                return _plan()

        class Evidence:
            async def search(self, request):
                seen.append(request)
                return EvidenceResult(facts=(CanonicalCard("ЖК Лучи", {"metro": "Солнцево"}),))

        state = V3ConversationState.clean(3)
        result = await V3CompositionRoot(Planner(), Evidence()).run(state, _turn())
        assert result.ok is True
        assert result.state.revision == 4 and state.revision == 3
        assert seen[0].user_text.text.endswith("[redacted-credential]")
        assert seen[1].requested_facts == ("metro",)
    asyncio.run(scenario())


def test_composition_rejects_missing_error_or_invalid_port_output_without_state_mutation() -> None:
    async def scenario() -> None:
        class PlannerError:
            async def plan(self, request):
                return V3ProviderError(V3ProviderErrorCode.TIMEOUT, True)

        class PlannerInvalid:
            async def plan(self, request):
                return None

        class PlannerGood:
            async def plan(self, request):
                return _plan()

        class EvidenceError:
            async def search(self, request):
                return V3ProviderError(V3ProviderErrorCode.INVALID_RESPONSE, False)

        class EvidenceInvalid:
            async def search(self, request):
                return {"facts": []}

        state = V3ConversationState.clean(8)
        cases = (
            (PlannerError(), EvidenceError(), "planner_timeout"),
            (PlannerInvalid(), EvidenceError(), "invalid_planner_port_result"),
            (PlannerGood(), EvidenceError(), "evidence_invalid_response"),
            (PlannerGood(), EvidenceInvalid(), "invalid_evidence_port_result"),
        )
        for planner, evidence, code in cases:
            result = await V3CompositionRoot(planner, evidence).run(state, _turn())
            assert result.ok is False and result.state is state and result.state_delta.is_empty
            assert result.errors == (code,)
            assert "hidden" not in result.public_response
    asyncio.run(scenario())


def test_composition_import_closure_has_no_runtime_v2_or_network_owner() -> None:
    tree = ast.parse(Path("nmbot_v3/composition.py").read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imports += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = ("nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4", "scripts", "runtime", "service", "selector", "requests", "aiohttp", "http", "socket")
    assert not any(name == blocked or name.startswith(blocked + ".") for name in imports for blocked in banned)
