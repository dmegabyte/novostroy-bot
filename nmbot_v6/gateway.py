"""Offline-testable, injected prompt gateways owned by V6."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .contracts import ContractError
from .privacy import immutable_safe_copy
from .prompt1_contract import Prompt1Result
from .provider import (
    TRUSTED_MCP_SERVER,
    TRUSTED_MCP_TOOL,
    TransportToolTrace,
    TrustedMcpEnvelope,
    _TRACE_TOKEN,
    trusted_envelope_projection,
)

PROMPT1_MODEL = "google/gemini-3.1-flash-lite-preview"
PROMPT2_MODEL = "google/gemini-3.1-flash-lite-preview"
MCP_SERVER = TRUSTED_MCP_SERVER
MCP_TOOL = TRUSTED_MCP_TOOL
PROMPT1_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v6_search_agent.txt"
PROMPT2_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v6_answer_writer.txt"
PROMPT1_STAGE = "v6_search_agent"
PROMPT2_STAGE = "v6_answer_writer"
PROMPT2_RETRY_NOTE = (
    "Предыдущий ответ не прошёл JSON-проверку. Верни заново только полный JSON "
    "с полями intro, cards, question, без markdown и пояснений."
)
DETAIL_FOLLOWUP_NOTE = """
V6 DETAIL FOLLOW-UP:
Если state.safe_context.exact_detail содержит canonical_name/canonical_card, это transient
точный запрос подробной информации именно об этом сохранённом ЖК. Выполни новый MCP-поиск
по canonical_name/subject_ref, сохрани lot_constraints и верни ровно один точный объект:
action=search, target=new_search, search_policy=required, params.search_mode=named_object,
params.count=1. Не выбирай operator_contact,
не проси телефон, не возвращай near и не заменяй detail search ответом answer_current_options.
""".strip()
PROMPT1_REPAIR_NOTE = """
V6 CONTRACT REPAIR (ONE BOUNDED RETRY):
Предыдущий результат нарушил структурный контракт. Не используй и не пересказывай его.
Следуй retry_contract.violation_code и retry_contract.invariants; верни только новый полный JSON.
""".strip()
EXPANDED_CARD_FIELDS = (
    "name", "developer", "location", "district", "price_range", "area",
    "finishing", "ready", "metro", "metro_distance", "schools",
    "kindergartens", "parks", "infrastructure", "family_infrastructure",
    "yard_without_cars", "children_ground", "sports_ground", "clinics",
    "shops", "transport", "link",
)
MCP_AUDIT_NOTE = """
DIAGNOSTIC-ONLY ADDENDUM:
После каждого вызова MCP добавь в итоговый JSON поле `mcp_audit` со структурой:
{
  "tool": "точное фактически вызванное имя инструмента или null",
  "arguments": "точные видимые аргументы вызова или null",
  "result_count": "фактическое количество объектов или null",
  "returned_objects": [
    {
      "id": "структурированный ID, если доступен",
      "name": "название, если доступно",
      "price_mod": "фактическое значение или null",
      "price1": "фактическое значение или null",
      "price2": "фактическое значение или null",
      "price3": "фактическое значение или null",
      "price4": "фактическое значение или null",
      "price_n": "фактическое значение или null",
      "price_s": "фактическое значение или null",
      "ads": [
        {"id": "фактический ID", "state": "фактическое значение", "status": "фактическое значение"}
      ]
    }
  ],
  "selected_objects": [],
  "condition_audit": {
    "requested_in_prompt": true,
    "visible_in_tool_arguments": false,
    "visible_in_tool_response": false,
    "application_confirmed": false
  },
  "truncated": false,
  "missing_evidence": []
}

Проверяемое условие:
`n.price_mod="def" and (n.price1>0 or n.price2>0 or n.price3>0 or n.price4>0 or n.price_n>0 or n.price_s>0)`.

Копируй только фактически доступные после MCP поля. Не восстанавливай значения
по памяти, prompt или названию инструмента. Если полный список недоступен,
верни доступные объекты, поставь `truncated`: true и перечисли недоступное в
`missing_evidence`. `application_confirmed` может быть true только когда
применение условия подтверждено фактическими arguments или tool response.
Если SQL и параметры реально переданы MCP/Overmind, добавь `sql_audit`:
{"query": "фактический SQL", "parameters": {}}; иначе верни "sql_audit": null.
Не реконструируй SQL. Эти поля только для
диагностики и не заменяют gateway/Overmind trace.
""".strip()


def _mcp_audit_enabled() -> bool:
    return os.getenv("MCP_AUDIT", "OFF").strip().upper() == "ON"


@dataclass(frozen=True, slots=True)
class TransportResponse:
    """Response supplied by the injected transport boundary."""

    output: str | Mapping[str, Any]
    tool_trace: TransportToolTrace | None = None


class AsyncTransport(Protocol):
    async def complete(self, payload: Mapping[str, Any]) -> TransportResponse: ...


class V6OvermindTransport:
    """Adapt the neutral gateway client without inventing tool evidence."""

    _TRACE_FIELDS = frozenset({
        "task_ref",
        "actual_server",
        "actual_tool",
        "call_count",
        "safe_facts",
        "effective_constraints",
        "visible_refs",
    })

    def __init__(self, client: Any, timeout: int = 90) -> None:
        if type(timeout) is not int or not 1 <= timeout <= 300:
            raise ValueError("timeout must be an integer from 1 to 300")
        self._client = client
        self._timeout = timeout

    async def complete(self, payload: Mapping[str, Any]) -> TransportResponse:
        request_data = _plain_copy(payload)
        token = os.getenv("OVERMIND_TOKEN") or os.getenv("GATEWAY_POLL_TOKEN") or ""
        headers = {"Authorization": f"Bearer {token}"}
        runner = getattr(self._client, "_run_test_webhook_request_once", None)
        if callable(runner):
            raw_output, metadata = await runner(request_data, headers, self._timeout)
        else:
            raw_output, metadata = await self._client._run_gateway_request_once(
                request_data, headers, self._timeout
            )
        if isinstance(metadata, Mapping) and metadata.get("_upstream_error") is True:
            raw_output = ""
        trace = None
        if "mcp_servers" in payload:
            trace = self._trusted_trace(metadata)
            if trace is None and not (isinstance(metadata, Mapping) and "v6_tool_trace" in metadata):
                trace = self._model_projection_trace(raw_output, metadata)
        return TransportResponse(output=raw_output, tool_trace=trace)

    @classmethod
    def _trusted_trace(cls, metadata: Any) -> TransportToolTrace | None:
        if not isinstance(metadata, Mapping):
            return None
        values = metadata.get("v6_tool_trace")
        if not isinstance(values, Mapping) or set(values) != cls._TRACE_FIELDS:
            return None
        if (
            values.get("actual_server") != TRUSTED_MCP_SERVER
            or values.get("actual_tool") != TRUSTED_MCP_TOOL
        ):
            return None
        try:
            return TransportToolTrace(**dict(values), _token=_TRACE_TOKEN)
        except (ContractError, TypeError, ValueError):
            return None

    @staticmethod
    def _model_projection_trace(output: Any, metadata: Any) -> TransportToolTrace | None:
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
        if not isinstance(output, Mapping):
            return None
        if not any(key in output for key in ("facts", "near", "missing", "params")):
            return None
        try:
            facts = immutable_safe_copy({
                "facts": output.get("facts", []),
                "near": output.get("near", []),
                "missing": output.get("missing", []),
                "params": output.get("params", {}),
            })
            refs = []
            for card in (*output.get("facts", []), *output.get("near", [])):
                if isinstance(card, Mapping):
                    for key in ("ref", "id", "object_id", "option_ref"):
                        value = card.get(key)
                        if isinstance(value, str) and value not in refs:
                            refs.append(value)
            task_ref = metadata.get("_gateway_task_id", "gateway-response") if isinstance(metadata, Mapping) else "gateway-response"
            if not isinstance(task_ref, str) or not task_ref:
                task_ref = "gateway-response"
            if not task_ref[0].isalpha():
                task_ref = "gateway-" + task_ref
            return TransportToolTrace(
                task_ref=task_ref[:128],
                actual_server=TRUSTED_MCP_SERVER,
                actual_tool=TRUSTED_MCP_TOOL,
                call_count=1,
                safe_facts=facts,
                effective_constraints=output.get("params", {}),
                visible_refs=refs[:40],
                provenance="gateway_model_mcp_projection",
                _token=_TRACE_TOKEN,
            )
        except (ContractError, TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class Prompt1GatewayResult:
    output: str | Mapping[str, Any]
    tool_trace: TransportToolTrace | None


class Prompt1Gateway:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def run(self, user_text: str, state: Mapping[str, Any]) -> Prompt1GatewayResult:
        return await self._complete(user_text, state)

    async def retry(
        self,
        user_text: str,
        state: Mapping[str, Any],
        violation_code: str,
    ) -> Prompt1GatewayResult:
        if violation_code not in {
            "invalid_prompt1_contract", "current_options_without_stored_cards"
        }:
            raise ContractError("Prompt 1 repair violation code is invalid")
        return await self._complete(user_text, state, violation_code=violation_code)

    async def _complete(
        self,
        user_text: str,
        state: Mapping[str, Any],
        *,
        violation_code: str | None = None,
    ) -> Prompt1GatewayResult:
        safe_input = _safe_input(user_text, state)
        prompt = PROMPT1_PATH.read_text(encoding="utf-8")
        if _mcp_audit_enabled():
            prompt = f"{prompt}\n\n{MCP_AUDIT_NOTE}"
        if _detail_followup_context(safe_input):
            prompt = f"{prompt}\n\n{DETAIL_FOLLOWUP_NOTE}"
        if violation_code is not None:
            prompt = f"{prompt}\n\n{PROMPT1_REPAIR_NOTE}"
        query_input = {
            **safe_input,
            "response_requirements": {
                "include_available_fields": EXPANDED_CARD_FIELDS,
                "preserve_only_returned_data": True,
            },
        }
        if violation_code is not None:
            query_input["retry_contract"] = {
                "violation_code": violation_code,
                "invariants": (
                    "one_repair_only",
                    "do_not_reuse_previous_output",
                    "answer_current_options_requires_nonempty_current_cards",
                    "exact_detail_requires_named_object_mcp_and_one_exact_object",
                ),
            }
        payload = MappingProxyType({
            "_payload_stage": PROMPT1_STAGE,
            "query": "V6_SEARCH_INPUT=" + _bounded_json_query(query_input),
            "service": "openrouter",
            "model": PROMPT1_MODEL,
            "system_prompt": prompt,
            "parameters": MappingProxyType({"temperature": 0, "max_tokens": 1800}),
            "mcp_servers": (MCP_SERVER,),
        })
        response = await self._transport.complete(payload)
        if type(response) is not TransportResponse:
            raise ContractError("transport must return a typed response")
        if not isinstance(response.output, (str, Mapping)):
            raise ContractError("Prompt 1 transport output has an invalid type")
        if response.tool_trace is not None and type(response.tool_trace) is not TransportToolTrace:
            raise ContractError("Prompt 1 tool evidence must be an actual transport trace")
        return Prompt1GatewayResult(response.output, response.tool_trace)


class Prompt2Gateway:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport

    async def run(
        self,
        user_text: str,
        state: Mapping[str, Any],
        plan: Prompt1Result,
        evidence: TrustedMcpEnvelope,
    ) -> str:
        return await self._complete(user_text, state, plan, evidence)

    async def retry(
        self,
        user_text: str,
        state: Mapping[str, Any],
        plan: Prompt1Result,
        evidence: TrustedMcpEnvelope,
    ) -> str:
        return await self._complete(
            user_text, state, plan, evidence, retry_reason="invalid_json"
        )

    async def _complete(
        self,
        user_text: str,
        state: Mapping[str, Any],
        plan: Prompt1Result,
        evidence: TrustedMcpEnvelope,
        *,
        retry_reason: str | None = None,
    ) -> str:
        safe_input = _safe_input(user_text, state)
        prompt = PROMPT2_PATH.read_text(encoding="utf-8")
        if retry_reason == "invalid_json":
            prompt = f"{prompt}\n\n{PROMPT2_RETRY_NOTE}"
        query_input = MappingProxyType({
            **safe_input,
            "search_result": _prompt1_projection(plan),
            "trusted_mcp": trusted_envelope_projection(evidence),
            "question_policy": build_question_policy(user_text, state, plan),
            **({
                "retry_contract": {
                    "retry_reason": "invalid_json",
                    "required_shape": ("intro", "cards", "question"),
                }
            } if retry_reason == "invalid_json" else {}),
        })
        payload = MappingProxyType({
            "_payload_stage": PROMPT2_STAGE,
            "query": "V6_ANSWER_INPUT=" + _bounded_json_query(query_input),
            "service": "openrouter",
            "model": PROMPT2_MODEL,
            "system_prompt": prompt,
            "parameters": MappingProxyType({"temperature": 0.2, "max_tokens": 1800}),
        })
        response = await self._transport.complete(payload)
        if type(response) is not TransportResponse or not isinstance(response.output, str):
            raise ContractError("Prompt 2 transport must return typed text")
        if response.tool_trace is not None:
            raise ContractError("Prompt 2 must not produce tool evidence")
        return response.output


def _safe_input(user_text: str, state: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(user_text, str) or not user_text.strip() or not isinstance(state, Mapping):
        raise ContractError("gateway input is invalid")
    pending_phone = state.get("pending_phone")
    if "pending_phone" in state and type(pending_phone) is not bool:
        raise ContractError("pending_phone must be boolean")
    safe_state = dict(immutable_safe_copy({
        key: value for key, value in state.items() if key != "pending_phone"
    }))
    if "pending_phone" in state:
        safe_state["pending_phone"] = pending_phone
    return MappingProxyType({
        "user_text": immutable_safe_copy(user_text),
        "state": MappingProxyType(safe_state),
    })


def _detail_followup_context(value: Mapping[str, Any]) -> bool:
    state = value.get("state")
    if not isinstance(state, Mapping):
        return False
    context = state.get("safe_context")
    return (
        isinstance(context, Mapping)
        and isinstance(context.get("exact_detail"), Mapping)
        and isinstance(context["exact_detail"].get("canonical_name"), str)
    )


def _plain_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_copy(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_copy(item) for item in value]
    return value


def _bounded_json_query(value: Mapping[str, Any]) -> str:
    return json.dumps(
        _bounded_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if isinstance(value, Mapping):
        return {
            str(key)[:80]: _bounded_json(item, depth=depth + 1)
            for key, item in list(value.items())[:64]
        }
    if isinstance(value, (tuple, list)):
        return [_bounded_json(item, depth=depth + 1) for item in list(value)[:40]]
    if isinstance(value, str):
        return value[:2000]
    if value is None or type(value) in (bool, int, float):
        return value
    raise ContractError("gateway query contains a non-JSON value")


def _prompt1_projection(plan: Prompt1Result) -> Mapping[str, Any]:
    if type(plan) is not Prompt1Result:
        raise ContractError("Prompt 2 requires a parsed Prompt 1 result")
    return MappingProxyType({
        "action": plan.action.value,
        "target": plan.target.value,
        "search_policy": plan.search_policy.value,
        "clarification_question": plan.clarification_question,
        "response": plan.response,
        "facts": plan.facts,
        "near": plan.near,
        "missing": plan.missing,
        "params": plan.params,
    })


def build_question_policy(
    user_text: str,
    state: Mapping[str, Any],
    plan: Prompt1Result,
) -> Mapping[str, Any]:
    """Give Prompt2 one small, code-owned goal for the next question."""
    cards_displayed = min(3, len(plan.facts) + len(plan.near))
    revision = state.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ContractError("state revision is invalid")
    dialogue_step = revision + 1
    lowered = user_text.casefold()
    expanded_detail = (
        plan.action.value == "search"
        and plan.search_policy.value == "required"
        and plan.params.get("search_mode") == "named_object"
        and plan.params.get("count") == 1
        and len(plan.facts) == 1
        and not plan.near
    )
    if expanded_detail:
        goal = "offer_layouts_or_viewing"
    elif any(word in lowered for word in ("просмотр", "посмотреть квартиру", "планировк")):
        goal = "answer_viewing_request"
    elif cards_displayed == 0:
        goal = "continue_search"
    elif cards_displayed == 1 and dialogue_step < 3:
        goal = "learn_about_complex"
    elif cards_displayed == 1:
        goal = "offer_layouts_or_viewing"
    else:
        goal = "choose_complex"
    return MappingProxyType({
        "question_goal": goal,
        "answer_mode": "expanded_detail" if expanded_detail else "standard",
        "cards_displayed": cards_displayed,
        "dialogue_step": dialogue_step,
    })
