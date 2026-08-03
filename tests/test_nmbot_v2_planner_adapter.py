from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nmbot_v2.contracts import ExecutableTurn, IntentGoal, OptionCard, SafeTurnContext, Stage, TurnAction
from nmbot_v2.planner_adapter import build_semantic_planner_adapter
from nmbot_v2.state import ConversationState


def _v3_plan(**changes: Any) -> dict[str, Any]:
    plan = {
        "schema_version": 3, "goal": "answer_selected", "viewpoint": "life",
        "selected_option_name": "ЖК Лучи", "named_object_reference": None,
        "comparison_option_names": [], "requested_facts": ["metro"], "constraints_delta": {},
        "operator_consent": None, "explicit_operator_request": False,
        "followup_outcome": None, "clarification": None, "confidence": 0.9,
    }
    return plan | changes


def test_v3_adapter_uses_injected_session_and_compiles_typed_turn() -> None:
    captured: dict[str, Any] = {}

    async def session_provider() -> str:
        return "session-1"

    async def provider(session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(session=session, kwargs=kwargs)
        return _v3_plan()

    adapter = build_semantic_planner_adapter(provider=provider, session_provider=session_provider, intent_plan_version="v3")
    state = ConversationState(visible_options=(OptionCard(name="ЖК Лучи", metro="Солнцево"),))
    result = asyncio.run(adapter.plan(SafeTurnContext("u", "что с метро у лучей"), state))

    assert isinstance(result, ExecutableTurn)
    assert result.goal == IntentGoal.ANSWER_SELECTED
    assert result.stage == Stage.SELECTED_OBJECT
    assert result.action == TurnAction.ANSWER_SELECTED_OPTION
    assert result.query_text == "что с метро у лучей"
    assert captured["session"] == "session-1"
    assert captured["kwargs"]["visible_response_text"] == "Текущие варианты: ЖК Лучи"
    assert captured["kwargs"]["selected_object"]["canonical_name"] == "ЖК Лучи"
    assert adapter.last_planner_plan["goal"] == "answer_selected"


def test_v2_adapter_preserves_selected_scope_without_global_runtime_import() -> None:
    async def provider(_session: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"operation": "current_options", "refers_to_existing_objects": True, "response_viewpoint": "financing"}

    adapter = build_semantic_planner_adapter(provider=provider)
    state = ConversationState(visible_options=(OptionCard(name="ЖК Лучи"),), selected_option_name="ЖК Лучи")
    result = asyncio.run(adapter.plan(SafeTurnContext("u", "а ипотека?"), state))

    assert result.operation == "financing"
    assert result.selected_option_name == "ЖК Лучи"
    assert result.scope == "one"


def test_factory_import_closure_has_no_global_runtime_adapter_dependency() -> None:
    import nmbot_v2.planner_adapter as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "scripts.nmbot_runtime_adapter" not in source


def test_fresh_factory_import_does_not_load_global_runtime_adapter() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import nmbot_v2.planner_adapter; print('scripts.nmbot_runtime_adapter' in sys.modules)"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "False"
