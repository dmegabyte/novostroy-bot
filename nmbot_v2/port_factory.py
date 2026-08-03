"""V2-owned factory for an explicitly injected runtime port set.

This is a composition boundary, not a transport boundary: callers supply the
planner provider/session, complete search service, and response executor.
It intentionally chooses no environment, gateway, selector, or legacy adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .composition import build_turn_processor
from .local_response_adapters import V2ResponseExecutor, build_local_response_adapter_ports
from .planner_adapter import PlannerProvider, SessionProvider, build_semantic_planner_adapter
from .ports import SearchServicePort, SemanticPlannerPort, V2RuntimePorts
from .runtime import TurnProcessor


_COMPOSER_MODES = frozenset({"off", "shadow", "publish"})


@dataclass(frozen=True)
class V2PortFactoryConfig:
    """All dependencies a V2 runtime needs from its owning composition root."""

    planner_provider: PlannerProvider | None = None
    planner_session_provider: SessionProvider | None = None
    planner_port: SemanticPlannerPort | None = None
    search_service: SearchServicePort | None = None
    response_executor: V2ResponseExecutor | None = None
    response_composer_mode: str = "off"
    manager_rewriter_mode: str = "off"
    intent_plan_version: str = "v2"
    writer_model: str = "google/gemini-2.5-flash"
    formatter_model: str = "inclusionai/ling-2.6-flash"
    manager_rewriter_model: str = "google/gemini-2.5-flash"


def build_v2_turn_processor(config: V2PortFactoryConfig) -> TurnProcessor:
    """Build a V2 processor from complete, caller-owned injected dependencies.

    A narrow ``V2SearchAdapterPort`` cannot stand in for ``search_service``:
    selected-option and pair enrichment are runtime capabilities, too.  Invalid
    or absent dependencies raise before a processor is returned, avoiding a
    partially wired runtime.
    """
    if not isinstance(config, V2PortFactoryConfig):
        raise TypeError("v2_port_factory_config_required")
    if config.planner_port is not None:
        if config.planner_provider is not None or config.planner_session_provider is not None:
            raise TypeError("v2_planner_source_ambiguous")
        if not callable(getattr(config.planner_port, "plan", None)):
            raise TypeError("v2_planner_port_required")
        planner = config.planner_port
    else:
        _require_callable(config.planner_provider, "v2_planner_provider_required")
        _require_callable(config.planner_session_provider, "v2_planner_session_provider_required")
        planner = build_semantic_planner_adapter(
            provider=config.planner_provider,
            session_provider=config.planner_session_provider,
            intent_plan_version=config.intent_plan_version,
        )
    _require_complete_search_service(config.search_service)
    _require_callable(config.response_executor, "v2_response_executor_required")
    response_composer_mode = _normalize_mode(config.response_composer_mode, "v2_response_composer_mode_invalid")
    manager_rewriter_mode = _normalize_mode(config.manager_rewriter_mode, "v2_manager_rewriter_mode_invalid")

    response_ports = build_local_response_adapter_ports(
        config.response_executor,
        writer_model=config.writer_model,
        formatter_model=config.formatter_model,
        manager_rewriter_model=config.manager_rewriter_model,
    )
    return build_turn_processor(
        V2RuntimePorts(
            planner=planner,
            search_service=config.search_service,
            response_composer=response_ports.response_composer,
            manager_rewriter=response_ports.manager_rewriter,
        ),
        response_composer_mode=response_composer_mode,
        manager_rewriter_mode=manager_rewriter_mode,
    )


def _require_callable(value: Any, error_code: str) -> None:
    if not callable(value):
        raise TypeError(error_code)


def _require_complete_search_service(value: Any) -> None:
    if value is None:
        raise TypeError("v2_search_service_required")
    missing = tuple(name for name in ("search", "enrich_selected", "enrich_pair") if not callable(getattr(value, name, None)))
    if missing:
        raise TypeError("v2_search_service_incomplete")


def _normalize_mode(value: Any, error_code: str) -> str:
    mode = str(value or "").strip().lower()
    if mode not in _COMPOSER_MODES:
        raise ValueError(error_code)
    return mode
