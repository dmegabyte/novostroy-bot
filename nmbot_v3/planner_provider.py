"""V3-local IntentPlan provider adapter with an injected invocation boundary.

This module owns the planner prompt and output contract, but deliberately does
not choose, configure, or invoke a real network provider.  A composition root
may inject an async invoker later; invalid output and invocation failures stay
inside the closed ``V3ProviderError`` result surface.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Awaitable, Callable, Mapping

from .contracts import IntentGoalV3, IntentPlanV3, V3_ALLOWED_FACTS, V3ContractError
from .ports import V3PlannerPortResult, V3PlannerRequest, V3ProviderError, V3ProviderErrorCode
from .provider_invocation import V3AsyncTransport, V3InvocationErrorCode, V3InvocationOperation, V3TransportInvoker
from .semantic_planner import validate_intent_plan_v3


V3_INTENT_PLAN_PROMPT = """
Ты — IntentPlan V3 planner текущей реплики клиента для консультанта по новостройкам.
Определи только смысл текущей реплики. Не пиши клиентский ответ, не выбирай
provider/search policy, не меняй state и не придумывай факты о ЖК.

Верни ровно один JSON object без markdown и свободного текста. Поля:
schema_version=3; goal — одно из new_search, refine_search, expand_search,
lookup_object, answer_current, compare_current, recommend_current,
answer_selected, answer_open_question, operator, clarify, resume_pending,
off_topic; viewpoint — family, life, rental, investment, financing или
unchanged. selected_option_ref и comparison_option_refs могут ссылаться только на
exact UUID из visible_option_refs. Эти refs непрозрачны: не выводи их клиенту.
named_object_reference
разрешён только для lookup_object. requested_facts выбирай только из
allowed_facts. constraints_delta содержит только изменения условий текущего
хода. operator_consent указывай только для явного ответа в pending flow;
followup_outcome — только допустимый pending outcome. Для clarify обязателен
один короткий clarification, иначе clarification=null. confidence от 0 до 1.

Не возвращай технические поля, raw payload, контакты, секреты или diagnostics.
""".strip()

_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": (
        "schema_version", "goal", "viewpoint", "selected_option_name", "selected_option_ref",
        "named_object_reference", "comparison_option_names", "comparison_option_refs", "requested_facts",
        "constraints_delta", "operator_consent", "explicit_operator_request",
        "followup_outcome", "clarification", "confidence",
    ),
    "properties": {
        "schema_version": {"const": 3},
        "goal": {"enum": tuple(goal.value for goal in IntentGoalV3)},
        "viewpoint": {"enum": ("family", "life", "rental", "investment", "financing", "unchanged")},
        "selected_option_name": {"type": ("string", "null")},
        "selected_option_ref": {"type": ("string", "null")},
        "named_object_reference": {"type": ("string", "null")},
        "comparison_option_names": {"type": "array", "minItems": 0, "maxItems": 2, "uniqueItems": True},
        "comparison_option_refs": {"type": "array", "minItems": 0, "maxItems": 2, "uniqueItems": True},
        "requested_facts": {"type": "array", "maxItems": 12, "uniqueItems": True},
        "constraints_delta": {"type": "object"},
        "operator_consent": {"type": ("boolean", "null")},
        "explicit_operator_request": {"type": "boolean"},
        "followup_outcome": {"type": ("string", "null")},
        "clarification": {"type": ("string", "null")},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


@dataclass(frozen=True)
class V3PlannerProviderRequest:
    """Safe, provider-agnostic request produced solely from the V3 port DTO."""

    prompt: str
    payload: Mapping[str, Any]
    response_schema: Mapping[str, Any]


V3PlannerInvoker = Callable[[V3PlannerProviderRequest], Awaitable[Mapping[str, Any] | str]]


def v3_intent_plan_response_schema() -> dict[str, Any]:
    """Return a mutable copy so an injected provider cannot mutate V3's contract."""
    return deepcopy(_RESPONSE_SCHEMA)


def build_v3_planner_provider_request(request: V3PlannerRequest) -> V3PlannerProviderRequest:
    """Build the entire provider-facing payload from the closed V3 request DTO."""
    if not isinstance(request, V3PlannerRequest):
        raise V3ContractError("invalid_planner_request")
    context = request.context
    payload = {
        "user_text": request.user_text.text,
        "visible_option_refs": context.visible_option_refs,
        "pending_followup_key": context.pending_followup_key,
        "has_pending_action": context.has_pending_action,
        "allowed_facts": tuple(sorted(V3_ALLOWED_FACTS)),
    }
    return V3PlannerProviderRequest(V3_INTENT_PLAN_PROMPT, payload, v3_intent_plan_response_schema())


class V3InjectedPlannerAdapter:
    """Adapt an injected provider invoker to the Wave 1 ``V3PlannerPort``."""

    def __init__(self, invoke: V3PlannerInvoker | None = None, *, transport: V3AsyncTransport[V3PlannerProviderRequest, Mapping[str, Any] | str] | None = None, timeout_seconds: float = 10.0) -> None:
        if (invoke is None) == (transport is None):
            raise V3ContractError("invalid_v3_planner_invoker")
        self._invoke = invoke
        self._transport_invoker = None if transport is None else V3TransportInvoker(
            transport, V3InvocationOperation.PLANNER, timeout_seconds=timeout_seconds,
        )

    async def plan(self, request: V3PlannerRequest) -> V3PlannerPortResult:
        try:
            provider_request = build_v3_planner_provider_request(request)
            if self._transport_invoker is not None:
                invoked = await self._transport_invoker.invoke(provider_request)
                if not invoked.ok:
                    return _transport_error(invoked.error)
                raw = invoked.payload
            else:
                assert self._invoke is not None
                raw = await self._invoke(provider_request)
        except asyncio.TimeoutError:
            return V3ProviderError(V3ProviderErrorCode.TIMEOUT, retryable=True)
        except V3ContractError:
            return V3ProviderError(V3ProviderErrorCode.INVALID_RESPONSE, retryable=False)
        except Exception:
            return V3ProviderError(V3ProviderErrorCode.UNAVAILABLE, retryable=True)

        plan_input = _parse_provider_plan(raw)
        if plan_input is None:
            return V3ProviderError(V3ProviderErrorCode.INVALID_RESPONSE, retryable=False)
        validation = validate_intent_plan_v3(plan_input, request.context)
        if not validation.ok or validation.plan is None:
            return V3ProviderError(V3ProviderErrorCode.INVALID_RESPONSE, retryable=False)
        return validation.plan


def _parse_provider_plan(raw: Mapping[str, Any] | str) -> Mapping[str, Any] | None:
    """Accept one JSON object only; never retain raw provider output as diagnostics."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _transport_error(error: V3InvocationErrorCode | None) -> V3ProviderError:
    if error is V3InvocationErrorCode.TIMEOUT:
        return V3ProviderError(V3ProviderErrorCode.TIMEOUT, retryable=True)
    if error is V3InvocationErrorCode.INVALID_RESPONSE:
        return V3ProviderError(V3ProviderErrorCode.INVALID_RESPONSE, retryable=False)
    return V3ProviderError(V3ProviderErrorCode.UNAVAILABLE, retryable=True)
