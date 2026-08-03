from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from nmbot_v2.contracts import SafeTurnContext, SearchResult
from nmbot_v2.port_factory import V2PortFactoryConfig, build_v2_turn_processor
from nmbot_v2.planner_adapter import V2SemanticPlannerAdapter
from nmbot_v2.state import ConversationState


class CompleteSearchService:
    def search(self, _plan, _state, _context=None):
        return SearchResult.from_dict({"facts": []})

    def enrich_selected(self, option, _state, _plan):
        return option

    def enrich_pair(self, _turn, _state):
        return None


def _planner(_session, **_kwargs):
    return {"operation": "current_options", "scope": "one"}


def _session():
    return object()


def _executor(_request):
    return ""


def _config(**overrides):
    values = {
        "planner_provider": _planner,
        "planner_session_provider": _session,
        "search_service": CompleteSearchService(),
        "response_executor": _executor,
    }
    values.update(overrides)
    return V2PortFactoryConfig(**values)


def test_factory_composes_typed_v2_ports_from_injected_dependencies() -> None:
    config = _config()

    processor = build_v2_turn_processor(config)

    assert isinstance(processor.planner, V2SemanticPlannerAdapter)
    assert processor.search_service is config.search_service
    assert processor.response_composer is not None
    assert processor.manager_rewriter is not None
    assert processor.response_composer_mode == "off"
    assert processor.manager_rewriter_mode == "off"


def test_factory_preserves_selected_scope_via_v2_planner_adapter() -> None:
    def selected_scope_planner(_session, **_kwargs):
        return {"operation": "current_options", "refers_to_existing_objects": True, "response_viewpoint": "financing"}

    processor = build_v2_turn_processor(_config(planner_provider=selected_scope_planner))
    state = ConversationState.from_dict({
        "visible_options": [{"name": "ЖК Первый"}],
        "selected_option_name": "ЖК Первый",
    })

    plan = asyncio.run(processor.planner.plan(SafeTurnContext(conversation_ref="test", user_text="расскажи подробнее"), state))

    assert plan.selected_option_name == "ЖК Первый"
    assert plan.scope == "one"


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"planner_provider": None}, "v2_planner_provider_required"),
        ({"planner_session_provider": None}, "v2_planner_session_provider_required"),
        ({"search_service": None}, "v2_search_service_required"),
        ({"response_executor": None}, "v2_response_executor_required"),
    ],
)
def test_factory_fails_closed_when_required_injected_dependency_is_missing(overrides, error) -> None:
    with pytest.raises(TypeError, match=f"^{error}$"):
        build_v2_turn_processor(_config(**overrides))


def test_factory_rejects_narrow_search_adapter_without_enrichment_and_pair_ports() -> None:
    class NarrowSearchAdapter:
        async def search(self, _request):
            return None

    with pytest.raises(TypeError, match="^v2_search_service_incomplete$"):
        build_v2_turn_processor(_config(search_service=NarrowSearchAdapter()))


@pytest.mark.parametrize("mode", ("off", "shadow", "publish"))
def test_factory_passes_composer_modes_to_runtime(mode: str) -> None:
    processor = build_v2_turn_processor(_config(response_composer_mode=mode, manager_rewriter_mode=mode))

    assert processor.response_composer_mode == mode
    assert processor.manager_rewriter_mode == mode


def test_factory_rejects_unknown_composer_modes() -> None:
    with pytest.raises(ValueError, match="^v2_response_composer_mode_invalid$"):
        build_v2_turn_processor(_config(response_composer_mode="enabled"))
    with pytest.raises(ValueError, match="^v2_manager_rewriter_mode_invalid$"):
        build_v2_turn_processor(_config(manager_rewriter_mode="enabled"))


def test_factory_import_closure_excludes_transport_and_other_runtime_versions() -> None:
    source = Path("nmbot_v2/port_factory.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imported += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = ("os", "dotenv", "requests", "aiohttp", "scripts", "nmbot_runtime_adapter", "nmbot_v0", "nmbot_v1", "nmbot_v3", "nmbot_v4")
    assert not any(name == blocked or name.startswith(blocked + ".") for name in imported for blocked in banned)
