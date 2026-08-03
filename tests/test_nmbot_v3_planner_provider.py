from __future__ import annotations

import asyncio
import ast
from pathlib import Path

from nmbot_v3.contracts import IntentPlanV3, V3PlannerContext
from nmbot_v3.planner_provider import (
    V3InjectedPlannerAdapter,
    V3_INTENT_PLAN_PROMPT,
    build_v3_planner_provider_request,
    v3_intent_plan_response_schema,
)
from nmbot_v3.ports import V3PlannerRequest, V3ProviderError, V3ProviderErrorCode, V3RedactedText


def _request() -> V3PlannerRequest:
    return V3PlannerRequest(V3RedactedText("Сравни текущие варианты; token=do-not-leak"), V3PlannerContext(("550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b-41d4-a716-446655440001")))


def _plan(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": 3,
        "goal": "compare_current",
        "viewpoint": "unchanged",
        "selected_option_name": None,
        "selected_option_ref": None,
        "named_object_reference": None,
        "comparison_option_names": [],
        "comparison_option_refs": ["550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b-41d4-a716-446655440001"],
        "requested_facts": [],
        "constraints_delta": {},
        "operator_consent": None,
        "explicit_operator_request": False,
        "followup_outcome": None,
        "clarification": None,
        "confidence": 0.9,
    }
    result.update(overrides)
    return result


def test_v3_injected_adapter_builds_local_contract_and_returns_validated_plan() -> None:
    async def scenario() -> None:
        seen = []

        async def invoke(provider_request):
            seen.append(provider_request)
            return _plan()

        result = await V3InjectedPlannerAdapter(invoke).plan(_request())
        assert isinstance(result, IntentPlanV3)
        assert result.comparison_option_refs == ("550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b-41d4-a716-446655440001")
        assert len(seen) == 1
        assert seen[0].prompt == V3_INTENT_PLAN_PROMPT
        assert seen[0].payload == {
            "user_text": "Сравни текущие варианты; [redacted-credential]",
            "visible_option_refs": ("550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b-41d4-a716-446655440001"),
            "pending_followup_key": None,
            "has_pending_action": False,
            "allowed_facts": tuple(sorted(seen[0].payload["allowed_facts"])),
        }
        assert "token" not in repr(seen[0]) and "do-not-leak" not in repr(seen[0])
    asyncio.run(scenario())


def test_v3_injected_adapter_fails_closed_without_raw_provider_diagnostics() -> None:
    async def scenario() -> None:
        async def invalid(_provider_request):
            return _plan(comparison_option_names=["неизвестный ЖК", "ещё один ЖК"])

        async def unavailable(_provider_request):
            raise RuntimeError("token=provider-secret")

        async def timed_out(_provider_request):
            raise TimeoutError("provider-secret")

        for invoke, code, retryable in (
            (invalid, V3ProviderErrorCode.INVALID_RESPONSE, False),
            (unavailable, V3ProviderErrorCode.UNAVAILABLE, True),
            (timed_out, V3ProviderErrorCode.TIMEOUT, True),
        ):
            result = await V3InjectedPlannerAdapter(invoke).plan(_request())
            assert isinstance(result, V3ProviderError)
            assert (result.code, result.retryable) == (code, retryable)
            assert "secret" not in repr(result) and "token" not in repr(result)
    asyncio.run(scenario())


def test_v3_local_schema_is_mutation_safe_and_no_provider_runtime_is_imported() -> None:
    first = v3_intent_plan_response_schema()
    first["properties"]["goal"]["enum"] = ["unsafe"]
    second = v3_intent_plan_response_schema()
    assert second["properties"]["goal"]["enum"] != ["unsafe"]
    request = build_v3_planner_provider_request(_request())
    assert request.payload["user_text"].endswith("[redacted-credential]")

    tree = ast.parse(Path("nmbot_v3/planner_provider.py").read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imports += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = ("aiohttp", "os", "scripts", "followup_intent_classifier", "nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4")
    assert not any(name == blocked or name.startswith(blocked + ".") for name in imports for blocked in banned)
