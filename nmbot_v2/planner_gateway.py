"""V2-owned, injectable semantic-planner gateway boundary.

It builds a bounded request from the existing V2 planner-adapter semantics,
requires a marker/versioned semantic result, and exposes redacted stable errors.
The module never creates a network client or imports a global runtime adapter.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import json
import re
from typing import Any, Mapping, Protocol

from .contracts import SafeTurnContext, TurnPlan
from .planner_adapter import SessionProvider, V2SemanticPlannerAdapter
from .planner_gateway_contract import V2_PLANNER_GATEWAY_MARKER, V2_PLANNER_GATEWAY_SCHEMA_VERSION
from .state import ConversationState


V2_SEMANTIC_PLANNER_PROMPT = """
Ты — V2 semantic planner текущей реплики клиента для консультанта по новостройкам.
Верни только один JSON object по response_schema. Определи смысл реплики, но не
пиши ответ клиенту, не выбирай транспорт/модель, не вызывай поиск и не меняй
состояние. Используй только разрешённые subjects, facts и поля контекста. Не
возвращай контакты, секреты, raw payload, diagnostics или технический текст.
""".strip()

_SENSITIVE_KEY = re.compile(r"(?:token|secret|password|api[_-]?key|credential|authorization|phone|телефон|contact|chat[_-]?id|client[_-]?id|sender|raw|payload)", re.I)
_CREDENTIAL_VALUE = re.compile(r"\b(?:token|secret|password|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+", re.I)
_PII_VALUE = re.compile(r"(?:\+?\d[\d\s()\-]{8,}\d|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b)")
_MAX_TEXT = 1000
_MAX_OPTIONS = 5
_MAX_LIST = 12


class V2PlannerGatewayErrorCode(str, Enum):
    TIMEOUT = "v2_planner_gateway_timeout"
    UNAVAILABLE = "v2_planner_gateway_unavailable"
    INVALID_RESPONSE = "v2_planner_gateway_invalid_response"


class V2PlannerGatewayError(RuntimeError):
    """Stable, deliberately message-free gateway failure for callers."""

    def __init__(self, code: V2PlannerGatewayErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class V2PlannerGatewayResponse:
    text: str = ""
    error_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_code is None and bool(self.text.strip())


class V2PlannerGatewayClient(Protocol):
    async def invoke(self, request_data: Mapping[str, Any], *, timeout_seconds: float | None = None) -> Any: ...


@dataclass(frozen=True)
class V2PlannerGatewayRequest:
    marker: str
    schema_version: int
    model: str
    prompt: str
    planner_context: Mapping[str, Any]
    response_schema: Mapping[str, Any]

    def to_gateway_payload(self) -> dict[str, Any]:
        return {
            "marker": self.marker,
            "schema_version": self.schema_version,
            "model": self.model,
            "prompt": self.prompt,
            "planner_context": deepcopy(dict(self.planner_context)),
            "response_schema": deepcopy(dict(self.response_schema)),
        }


def v2_semantic_planner_response_schema() -> dict[str, Any]:
    """Return a fresh, strict schema for the V2 semantic planner result envelope."""

    semantic_properties = {
        "user_goal": {"type": "string", "maxLength": 400},
        "refers_to_existing_objects": {"type": ("boolean", "string")},
        "requests_new_objects": {"type": ("boolean", "string")},
        "selected_reference": {"type": ("string", "number", "null")},
        "named_object_reference": {"type": ("string", "null"), "maxLength": 100},
        "requested_comparison": {"type": "array", "maxItems": 8},
        "scenario_needs": {"type": "array", "maxItems": 5},
        "response_viewpoint": {"enum": ("family", "life", "rental", "investment", "financing", "unchanged")},
        "scenario_change": {"type": ("string", "null")},
        "constraints_delta": {"type": "object"},
        "requires_enrichment": {"type": "boolean"},
        "resolved_subject": {"type": ("string", "null")},
        "resolved_intent": {"type": ("string", "null")},
        "requested_facts": {"type": "array", "maxItems": 12, "uniqueItems": True},
        "facts_needed": {"type": "array", "maxItems": 12, "uniqueItems": True},
        "focus_action": {"type": "string", "maxLength": 80},
        "domain_relation": {"enum": ("in_domain", "off_topic", "unknown")},
        "clarification": {"type": ("string", "null"), "maxLength": 300},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 400},
    }
    return {
        "type": "object", "additionalProperties": False,
        "required": ("marker", "schema_version", *semantic_properties.keys()),
        "properties": {
            "marker": {"const": V2_PLANNER_GATEWAY_MARKER},
            "schema_version": {"const": V2_PLANNER_GATEWAY_SCHEMA_VERSION},
            **semantic_properties,
        },
    }


def build_v2_planner_gateway_request(*, planner_kwargs: Mapping[str, Any], model: str) -> V2PlannerGatewayRequest:
    """Project only the bounded, redacted subset of existing adapter semantics."""

    safe_model = str(model or "").strip()
    if not safe_model or len(safe_model) > 160:
        raise ValueError("v2_planner_model_required")
    context = {
        "user_text": _safe_text(planner_kwargs.get("user_text")),
        "state": _safe_state(planner_kwargs.get("state")),
        "last_turn": _safe_mapping(planner_kwargs.get("last_turn"), allowed={"bot_question", "client_answer"}),
        "last_response_text": _safe_text(planner_kwargs.get("last_response_text")),
        "visible_response_text": _safe_text(planner_kwargs.get("visible_response_text"), maximum=600),
        "pending_scenario": _safe_pending(planner_kwargs.get("pending_scenario")),
        "selected_object": _safe_mapping(planner_kwargs.get("selected_object"), allowed={"canonical_name", "present_fact_fields"}),
        "dialog_focus": _safe_mapping(planner_kwargs.get("dialog_focus")),
        "allowed_subjects": _safe_string_list(planner_kwargs.get("allowed_subjects")),
        "allowed_facts": _safe_string_list(planner_kwargs.get("allowed_facts")),
        "subject_fact_map": _safe_subject_fact_map(planner_kwargs.get("subject_fact_map")),
        "dynamic_fields": _safe_string_list(planner_kwargs.get("dynamic_fields")),
    }
    return V2PlannerGatewayRequest(V2_PLANNER_GATEWAY_MARKER, V2_PLANNER_GATEWAY_SCHEMA_VERSION, safe_model, V2_SEMANTIC_PLANNER_PROMPT, context, v2_semantic_planner_response_schema())


class V2GatewaySemanticPlannerAdapter:
    """Inject a V2 gateway client into the existing V2 semantic planner adapter."""

    def __init__(self, gateway: V2PlannerGatewayClient, *, model: str, timeout_seconds: float = 10.0, session_provider: SessionProvider | None = None, intent_plan_version: str = "v2") -> None:
        self._gateway = gateway
        self._model = str(model or "").strip()
        if not self._model:
            raise ValueError("v2_planner_model_required")
        self._timeout_seconds = _bounded_timeout(timeout_seconds)
        self._semantic_adapter = V2SemanticPlannerAdapter(provider=self._invoke, session_provider=session_provider, intent_plan_version=intent_plan_version)

    @property
    def last_planner_plan(self) -> dict[str, Any]:
        return self._semantic_adapter.last_planner_plan

    async def plan(self, context: SafeTurnContext, state: ConversationState) -> TurnPlan:
        return await self._semantic_adapter.plan(context, state)

    async def _invoke(self, _session: Any, /, **planner_kwargs: Any) -> Mapping[str, Any]:
        request = build_v2_planner_gateway_request(planner_kwargs=planner_kwargs, model=self._model)
        try:
            response = await self._gateway.invoke(request.to_gateway_payload(), timeout_seconds=self._timeout_seconds)
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise V2PlannerGatewayError(V2PlannerGatewayErrorCode.TIMEOUT) from None
        except Exception as exc:
            raise V2PlannerGatewayError(V2PlannerGatewayErrorCode.UNAVAILABLE) from None
        if not bool(getattr(response, "ok", False)):
            code = str(getattr(response, "error_code", ""))
            raise V2PlannerGatewayError(V2PlannerGatewayErrorCode.TIMEOUT if "timeout" in code else V2PlannerGatewayErrorCode.UNAVAILABLE)
        return _parse_result(getattr(response, "text", ""))


def _parse_result(raw: Any) -> Mapping[str, Any]:
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else None
    except (TypeError, ValueError):
        parsed = None
    schema = v2_semantic_planner_response_schema()
    required = set(schema["required"])
    properties = set(schema["properties"])
    if not isinstance(parsed, Mapping) or set(parsed) != required or not _valid_envelope(parsed):
        raise V2PlannerGatewayError(V2PlannerGatewayErrorCode.INVALID_RESPONSE)
    return {key: deepcopy(value) for key, value in parsed.items() if key not in {"marker", "schema_version"}}


def _valid_envelope(value: Mapping[str, Any]) -> bool:
    if value.get("marker") != V2_PLANNER_GATEWAY_MARKER or value.get("schema_version") != V2_PLANNER_GATEWAY_SCHEMA_VERSION:
        return False
    if not isinstance(value.get("confidence"), (int, float)) or isinstance(value.get("confidence"), bool) or not 0 <= float(value["confidence"]) <= 1:
        return False
    if value.get("response_viewpoint") not in {"family", "life", "rental", "investment", "financing", "unchanged"}:
        return False
    if value.get("domain_relation") not in {"in_domain", "off_topic", "unknown"}:
        return False
    return all(not _contains_sensitive(item) for key, item in value.items() if key not in {"marker", "schema_version"})


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_SENSITIVE_KEY.search(str(key)) or _contains_sensitive(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive(item) for item in value)
    return bool(isinstance(value, str) and (_CREDENTIAL_VALUE.search(value) or _PII_VALUE.search(value)))


def _safe_text(value: Any, *, maximum: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())[:maximum]
    text = _CREDENTIAL_VALUE.sub("[redacted-credential]", text)
    return _PII_VALUE.sub("[redacted-pii]", text)


def _safe_string_list(value: Any, *, maximum: int = _MAX_LIST) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return list(dict.fromkeys(_safe_text(item, maximum=120) for item in value if _safe_text(item, maximum=120) and not _SENSITIVE_KEY.search(str(item))))[:maximum]


def _safe_mapping(value: Any, *, allowed: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        name = str(key)
        if _SENSITIVE_KEY.search(name) or (allowed is not None and name not in allowed):
            continue
        if isinstance(item, Mapping):
            result[name] = _safe_mapping(item)
        elif isinstance(item, (list, tuple, set)):
            result[name] = _safe_string_list(item)
        elif item is None or isinstance(item, (str, int, float, bool)):
            result[name] = _safe_text(item, maximum=300) if isinstance(item, str) else item
    return result


def _safe_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    allowed = {"primary_intent", "known_fields", "selected_option", "visible_options", "last_options", "last_bot_question", "last_answer_kind", "last_turn", "pending_followup", "comparison_scope_option_names", "current_options_scope"}
    state = _safe_mapping(value, allowed=allowed)
    for key in ("visible_options", "last_options"):
        items = value.get(key)
        if isinstance(items, list):
            state[key] = [_safe_mapping(item, allowed={"name", "location", "district", "rooms", "price", "price_min", "metro", "ready", "finishing"}) for item in items[:_MAX_OPTIONS] if isinstance(item, Mapping)]
    return state


def _safe_pending(value: Any) -> dict[str, Any] | None:
    safe = _safe_mapping(value, allowed={"id", "allowed_reply_outcomes", "context"})
    return safe or None


def _safe_subject_fact_map(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _safe_string_list(item) for key, item in list(value.items())[:_MAX_LIST] if not _SENSITIVE_KEY.search(str(key))}


def _bounded_timeout(value: float) -> float:
    try:
        return min(120.0, max(0.1, float(value)))
    except (TypeError, ValueError):
        return 10.0
