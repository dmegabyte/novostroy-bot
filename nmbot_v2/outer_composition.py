"""V2-only outer composition for a private, independently owned worker.

Planner invocation remains an explicit injected dependency: no documented V2
planner gateway request exists in this package, so this module refuses to infer
one from a legacy runtime adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from .contracts import ExecutableTurn, OptionCard, SafeTurnContext, SearchResult, TurnPlan
from .gateway import V2GatewayClient, V2GatewayErrorCode
from .local_response_adapters import V2ResponseAdapterOutput
from .pair_comparison import execute_pair_comparison
from .planner_gateway import V2GatewaySemanticPlannerAdapter
from .port_factory import V2PortFactoryConfig, build_v2_turn_processor
from .gateway import V2GatewayConfig
from .search_adapter import V2SearchProviderRequest, build_injected_v2_search_adapter
from .search_contract import build_candidate_retrieval_request, build_request_data, build_search_request, load_prompt
from .search_enrichment import fetch_enriched_option_v2
from .state import ConversationState


class V2SearchTimeoutError(RuntimeError):
    """Redacted stable failure type for a bounded V2 search call."""


class V2SearchUnavailableError(RuntimeError):
    """Redacted stable failure type for any other V2 search gateway failure."""


@dataclass(frozen=True)
class V2OuterCompositionConfig:
    """Explicit V2-only composition inputs.

    ``gateway_config`` creates the V2 client at this boundary.  Tests and an
    embedding host may instead inject ``gateway``; injected clients stay owned
    by that caller.  No environment is read here.
    """
    gateway_config: V2GatewayConfig | None = None
    gateway_session: Any | None = None
    gateway_session_owned: bool = True
    gateway: V2GatewayClient | None = None
    planner_model: str = ""
    planner_timeout_seconds: float = 10.0
    response_composer_mode: str = "off"
    manager_rewriter_mode: str = "off"
    intent_plan_version: str = "v2"
    writer_model: str = "google/gemini-2.5-flash"
    formatter_model: str = "inclusionai/ling-2.6-flash"
    manager_rewriter_model: str = "google/gemini-2.5-flash"
    search_model: str = "google/gemini-3.1-flash-lite-preview"
    search_timeout_seconds: float = 30.0
    enrichment_timeout_seconds: float = 20.0


class V2GatewaySearchService:
    """Complete V2 search port backed by only typed V2 requests and gateway calls."""

    def __init__(self, gateway: V2GatewayClient, *, search_model: str, search_timeout_seconds: float, enrichment_timeout_seconds: float) -> None:
        self._gateway = gateway
        self._search_model = search_model
        self._search_timeout_seconds = _bounded_timeout(search_timeout_seconds)
        self._enrichment_timeout_seconds = _bounded_timeout(enrichment_timeout_seconds)
        self.last_enrichment_error_code: str | None = None
        self.last_enrichment_trace: dict[str, Any] = {}
        self.last_fresh_facts: tuple[str, ...] = ()
        self.last_pair_comparison_metadata: dict[str, Any] = {}

    async def search(self, plan: TurnPlan, state: ConversationState, context: SafeTurnContext | None = None) -> SearchResult:
        safe_context = context or SafeTurnContext(conversation_ref="local", user_text=str(plan.query_text or ""))
        request = build_search_request(plan, state, safe_context)
        adapter = build_injected_v2_search_adapter(self._invoke_search)
        result = await adapter.search(build_candidate_retrieval_request(request))
        if not result.ok or result.result is None:
            if result.error and result.error.code.value == "timeout":
                raise V2SearchTimeoutError()
            raise V2SearchUnavailableError()
        return result.result

    async def enrich_selected(self, option: OptionCard, state: ConversationState, plan: TurnPlan) -> OptionCard:
        self.last_enrichment_error_code = None
        self.last_enrichment_trace, self.last_fresh_facts = {}, ()
        requested = tuple(dict.fromkeys((*plan.requested_facts, *plan.facts_needed)))
        if not requested:
            return option
        enriched, meta = await fetch_enriched_option_v2(
            option, plan.intent or state.active_topic, self._invoke_request_data,
            timeout=self._enrichment_timeout_seconds, model=self._search_model, facts_needed=requested,
        )
        applied = bool(meta.get("applied"))
        skipped = str(meta.get("skipped") or "")
        if skipped in {"timeout", "provider"}:
            self.last_enrichment_error_code = "selected_enrichment_timeout" if skipped == "timeout" else "selected_enrichment_unavailable"
        self.last_fresh_facts = requested if applied else ()
        self.last_enrichment_trace = {"stage": "v2_option_enrichment", "enabled": True, "applied": applied, "outcome": "applied" if applied else (skipped or "unavailable")}
        return enriched

    async def enrich_pair(self, turn: ExecutableTurn, state: ConversationState) -> Any:
        result = await execute_pair_comparison(
            turn, state, self._invoke_request_data, viewpoint=turn.intent or turn.viewpoint or state.active_topic,
            facts_needed=turn.facts_needed or turn.requested_facts, timeout=self._enrichment_timeout_seconds,
            model=self._search_model,
        )
        self.last_pair_comparison_metadata = dict(result.metadata)
        return result

    async def _invoke_search(self, request: V2SearchProviderRequest) -> Mapping[str, Any] | str:
        response = await self._gateway.invoke(
            build_request_data(request.request, prompt=load_prompt(), model=self._search_model), timeout_seconds=self._search_timeout_seconds,
        )
        if not response.ok:
            raise _gateway_exception(response.error_code)
        return response.text

    async def _invoke_request_data(self, request_data: dict[str, Any]) -> tuple[Any, Mapping[str, Any] | None]:
        response = await self._gateway.invoke(request_data, timeout_seconds=self._enrichment_timeout_seconds)
        if not response.ok:
            return "", {"ok": False, "_upstream_error": True, "error_code": response.error_code.value if response.error_code else "v2_gateway_unavailable"}
        return response.text, {"ok": True}


def build_v2_outer_turn_processor(config: V2OuterCompositionConfig):
    """Construct the independent V2 processor from V2-owned adapters only."""
    if not isinstance(config, V2OuterCompositionConfig):
        raise TypeError("v2_outer_composition_config_required")
    gateway, _owned = _resolve_gateway(config)
    return _build_processor(config, gateway)


def build_v2_outer_app(*, config: V2OuterCompositionConfig, state_path, journal_path, token: str, release_identity: str):
    """Build a private worker and close only the gateway client this root owns."""
    gateway, owned = _resolve_gateway(config)
    processor = _build_processor(config, gateway)
    from .service import create_app

    app = create_app(
        state_path=state_path, journal_path=journal_path, token=token, release_identity=release_identity,
        planner_port=processor.planner, search_port=processor.search_service,
        response_composer_port=processor.response_composer, response_composer_mode=processor.response_composer_mode,
        manager_rewriter_port=processor.manager_rewriter, manager_rewriter_mode=processor.manager_rewriter_mode,
    )
    if owned:
        async def close_gateway(_app) -> None:
            await gateway.close()
            if config.gateway_session_owned and config.gateway_session is not None and not bool(getattr(config.gateway_session, "closed", False)):
                close = getattr(config.gateway_session, "close", None)
                if callable(close):
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
        app.on_cleanup.append(close_gateway)
    return app


def _build_processor(config: V2OuterCompositionConfig, gateway: V2GatewayClient):
    planner = V2GatewaySemanticPlannerAdapter(gateway, model=config.planner_model, timeout_seconds=config.planner_timeout_seconds)
    search_service = V2GatewaySearchService(gateway, search_model=config.search_model, search_timeout_seconds=config.search_timeout_seconds, enrichment_timeout_seconds=config.enrichment_timeout_seconds)
    return build_v2_turn_processor(V2PortFactoryConfig(
        planner_port=planner, search_service=search_service, response_executor=_response_executor(gateway),
        response_composer_mode=config.response_composer_mode, manager_rewriter_mode=config.manager_rewriter_mode,
        intent_plan_version=config.intent_plan_version, writer_model=config.writer_model,
        formatter_model=config.formatter_model, manager_rewriter_model=config.manager_rewriter_model,
    ))


def _resolve_gateway(config: V2OuterCompositionConfig) -> tuple[V2GatewayClient, bool]:
    if config.gateway is not None:
        if config.gateway_config is not None or config.gateway_session is not None:
            raise ValueError("v2_gateway_source_ambiguous")
        if not callable(getattr(config.gateway, "invoke", None)):
            raise TypeError("v2_gateway_required")
        return config.gateway, False
    if config.gateway_config is None:
        raise ValueError("v2_gateway_config_required")
    return V2GatewayClient(config.gateway_config, session=config.gateway_session), True


def _response_executor(gateway: V2GatewayClient) -> Callable[[Any], Awaitable[V2ResponseAdapterOutput]]:
    async def execute(request: Any) -> V2ResponseAdapterOutput:
        from .manager_rewriter import manager_rewriter_request_payload
        from .response_composer import formatter_request_payload, writer_request_payload
        if request.stage == "writer":
            payload = writer_request_payload(request.brief, model=request.model)
        elif request.stage == "formatter":
            payload = formatter_request_payload(request.writer_text, request.brief, model=request.model)
        elif request.stage == "manager_rewriter":
            payload = manager_rewriter_request_payload(transcript=request.transcript, current_question=request.current_question, prepared_answer=request.prepared_answer, brief=request.brief, model=request.model)
        else:
            return V2ResponseAdapterOutput(meta={"ok": False, "error_code": "adapter_invalid_output"})
        response = await gateway.invoke(payload)
        if not response.ok:
            return V2ResponseAdapterOutput(meta={"ok": False, "error_code": "upstream_error"})
        return V2ResponseAdapterOutput(raw=response.text, meta={"ok": True})
    return execute


def _bounded_timeout(value: float) -> float:
    try:
        return min(120.0, max(0.1, float(value)))
    except (TypeError, ValueError):
        return 20.0


def _gateway_exception(code: V2GatewayErrorCode | None) -> Exception:
    return TimeoutError("v2_gateway_timeout") if code == V2GatewayErrorCode.TIMEOUT else RuntimeError("v2_gateway_unavailable")
