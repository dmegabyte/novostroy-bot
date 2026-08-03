"""Formal V2-owned contract record for the standalone planner gateway.

This is deliberately a contract declaration, not a composition root.  A worker
must inject the gateway adapter explicitly; no legacy prompt, selector, or
global runtime adapter participates in this boundary.
"""
from __future__ import annotations

from dataclasses import dataclass


V2_PLANNER_GATEWAY_MARKER = "nmbot.v2.semantic-planner.gateway.v1"
V2_PLANNER_GATEWAY_SCHEMA_VERSION = 1
V2_PLANNER_GATEWAY_MODEL_CONFIG_KEY = "NMBOT_V2_PLANNER_MODEL"
V2_PLANNER_GATEWAY_TIMEOUT_CONFIG_KEY = "NMBOT_V2_PLANNER_TIMEOUT_SECONDS"


@dataclass(frozen=True)
class V2PlannerGatewayContractRecord:
    """Evidence and ownership boundary for the V2 planner gateway contract."""

    marker: str = V2_PLANNER_GATEWAY_MARKER
    schema_version: int = V2_PLANNER_GATEWAY_SCHEMA_VERSION
    model_config_key: str = V2_PLANNER_GATEWAY_MODEL_CONFIG_KEY
    timeout_config_key: str = V2_PLANNER_GATEWAY_TIMEOUT_CONFIG_KEY
    prompt_owner: str = "nmbot_v2.planner_gateway"
    result_owner: str = "nmbot_v2.semantic_planner"
    lifecycle: str = "one_shot_no_retry_no_fallback"
    proven: bool = True
    remaining_gaps: tuple[str, ...] = (
        "production_gateway_wire_compatibility_requires_separate_live_evidence",
    )


V2_PLANNER_GATEWAY_CONTRACT = V2PlannerGatewayContractRecord()


def require_v2_planner_gateway_contract() -> V2PlannerGatewayContractRecord:
    """Return the approved V2 contract record without selecting a runtime."""

    return V2_PLANNER_GATEWAY_CONTRACT
