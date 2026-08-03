"""V2-owned concrete adapter from an injected planner provider to ``SemanticPlannerPort``.

The provider seam deliberately owns sessions, prompts, transport, and model selection.
This module only builds the bounded V2 planner input and compiles its result into
the V2 turn contract, so a private V2 worker never needs the global runtime adapter.
"""
from __future__ import annotations

from dataclasses import replace
import inspect
from typing import Any, Awaitable, Callable, Mapping, Protocol

from .contracts import ExecutableTurn, IntentGoal, IntentPlanV3, SafeTurnContext, SemanticPlan, TurnPlan, to_jsonable
from .fact_context import ALLOWED_FACTS, ALLOWED_SUBJECTS, SUBJECT_FACT_MAP, present_fact_names
from .scenario_recipes import FINANCING_CONSENT_FOLLOWUP, reply_contract_for_pending
from .semantic_planner import DerivedPlannerDecision, SemanticPlannerResult, derive_runtime_decision, normalize_semantic_planner_result
from .state import ConversationState
from .transition import compile_executable_turn_v3


class PlannerProvider(Protocol):
    """Injected model-facing call; it must return a structured planner mapping."""

    def __call__(self, session: Any, /, **planner_kwargs: Any) -> Mapping[str, Any] | Awaitable[Mapping[str, Any]]: ...


SessionProvider = Callable[[], Any | Awaitable[Any]]


class V2SemanticPlannerAdapter:
    """Concrete V2 planner port backed exclusively by injected provider functions."""

    def __init__(
        self,
        *,
        provider: PlannerProvider,
        session_provider: SessionProvider | None = None,
        intent_plan_version: str = "v2",
    ) -> None:
        self._provider = provider
        self._session_provider = session_provider
        self._intent_plan_version = "v3" if str(intent_plan_version).strip().lower() == "v3" else "v2"
        self.last_planner_plan: dict[str, Any] = {}

    async def plan(self, context: SafeTurnContext, state: ConversationState) -> TurnPlan:
        legacy_state = _v2_to_planner_state(state)
        planner_state = _safe_planner_state(legacy_state)
        last_response_text = str(legacy_state.get("last_response_text") or "")[:1200]
        planner_kwargs = {
            "user_text": context.user_text,
            "state": planner_state,
            "last_turn": {"bot_question": last_response_text, "client_answer": context.user_text},
            "last_response_text": last_response_text,
            "search_response_text": "",
            "visible_response_text": _visible_options_context(state),
            "pending_scenario": _pending_scenario_for_planner(state),
            "selected_object": _selected_object_context(state),
            "dialog_focus": to_jsonable(state.dialog_focus),
            "allowed_subjects": list(ALLOWED_SUBJECTS),
            "allowed_facts": list(ALLOWED_FACTS),
            "subject_fact_map": {key: list(value) for key, value in SUBJECT_FACT_MAP.items()},
            "dynamic_fields": _dynamic_fields_context(state),
            "model": None,
        }
        session = await _maybe_await(self._session_provider()) if self._session_provider else None
        provided = await _maybe_await(self._provider(session, **planner_kwargs))
        raw_plan = dict(provided) if isinstance(provided, Mapping) else {}
        self.last_planner_plan = raw_plan
        if self._intent_plan_version == "v3":
            contract_plan = {key: value for key, value in raw_plan.items() if key in IntentPlanV3.__dataclass_fields__}
            executable = compile_executable_turn_v3(contract_plan, state, query_text=context.user_text, allowed_facts=ALLOWED_FACTS)
            self.last_planner_plan.update(executable.trace_metadata)
            return _inherit_selected_scope(executable, state)

        normalized = normalize_semantic_planner_result(raw_plan, available_fact_fields=ALLOWED_FACTS)
        normalized = _keep_transition_accepted_legacy_operation(normalized, raw_plan)
        normalized = _drop_legacy_search_reference(normalized, raw_plan)
        decision = derive_runtime_decision(normalized, planner_state)
        return _inherit_selected_scope(_semantic_plan_from_semantic_result(normalized, decision, raw_plan, query_text=context.user_text), state)


def build_semantic_planner_adapter(
    *,
    provider: PlannerProvider,
    session_provider: SessionProvider | None = None,
    intent_plan_version: str = "v2",
) -> V2SemanticPlannerAdapter:
    """Factory used by a V2 composition root; no global runtime import occurs."""
    return V2SemanticPlannerAdapter(
        provider=provider,
        session_provider=session_provider,
        intent_plan_version=intent_plan_version,
    )


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _v2_to_planner_state(state: ConversationState) -> dict[str, Any]:
    visible = [_legacy_option(to_jsonable(option)) for option in state.visible_options]
    selected = _legacy_option(to_jsonable(state.selected_enriched)) if state.selected_enriched else ({"name": state.selected_option_name} if state.selected_option_name else {})
    last_response_text = next((str(turn.get("assistant") or "")[:1200] for turn in reversed(state.recent_turns) if turn.get("assistant")), "")
    return {
        "params": dict(state.params),
        "visible_options": visible,
        "last_options": visible,
        "selected_option": selected,
        "pending_followup": {"type": state.pending_followup} if state.pending_followup else {},
        "dialog_window": _dialog_window(state.recent_turns),
        "last_turn": _last_turn(state.recent_turns),
        "last_response_text": last_response_text,
        "last_bot_question": state.last_assistant_question or "",
        "last_answer_kind": state.last_answer_kind or "",
        "comparison_scope_option_names": list(state.comparison_scope_option_names),
        "current_options_scope": "one" if state.selected_option_name else "all" if visible else "unknown",
        "dialog_focus": to_jsonable(state.dialog_focus),
        "selected_object": _selected_object_context(state),
    }


def _safe_planner_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded V2 planner context without depending on ``scripts``."""
    selected = state.get("selected_option") if isinstance(state.get("selected_option"), Mapping) else {}
    params = state.get("params") if isinstance(state.get("params"), Mapping) else {}
    primary_intent = str(params.get("primary_intent") or params.get("purpose") or "unknown").strip().lower() or "unknown"
    known_fields = {str(key) for key, value in params.items() if value not in (None, "", [], {})}
    if primary_intent != "unknown":
        known_fields.update({"primary_intent", "purpose"})
    return {
        "params": to_jsonable(dict(params)),
        "primary_intent": primary_intent,
        "known_fields": sorted(known_fields),
        "selected_option": to_jsonable(dict(selected)),
        "visible_options": to_jsonable(state.get("visible_options") or []),
        "last_options": to_jsonable(state.get("last_options") or []),
        "last_bot_question": str(state.get("last_bot_question") or "")[:1000],
        "last_answer_kind": str(state.get("last_answer_kind") or "")[:120],
        "last_turn": to_jsonable(state.get("last_turn") or {}),
        "pending_followup": to_jsonable(state.get("pending_followup") or {}),
        "comparison_scope_option_names": to_jsonable(state.get("comparison_scope_option_names") or []),
        "current_options_scope": str(state.get("current_options_scope") or "unknown"),
    }


def _legacy_option(data: Mapping[str, Any]) -> dict[str, Any]:
    option = {
        "name": str(data.get("name") or "Вариант").strip(),
        "location": data.get("location") or data.get("district"),
        "price": data.get("price"), "price_min": data.get("price_min"),
        "rooms": data.get("rooms"), "finishing": data.get("finishing"),
        "area": data.get("area"), "ready": data.get("ready"), "metro": data.get("metro"),
        "developer": data.get("developer"), "property_class": data.get("property_class"),
        "infrastructure": data.get("infrastructure") or (), "ads_count": data.get("ads_count"),
        "sales_count": data.get("sales_count"), "sales_date": data.get("sales_date"),
        "discount": data.get("discount"), "parking": data.get("parking"),
        "parking_price": data.get("parking_price"), "parking_inventory": data.get("parking_inventory"),
        "apartment_inventory": data.get("apartment_inventory"), "mortgage_terms": data.get("mortgage_terms"),
        "room_formats": data.get("room_formats") or (), "lot_examples": data.get("lot_examples"),
        "why_close": data.get("why_close"), "is_near": bool(data.get("is_near", False)),
    }
    return {key: value for key, value in option.items() if value not in (None, "", ())}


def _dialog_window(turns: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    window: list[dict[str, str]] = []
    for turn in turns[-6:]:
        if turn.get("user"):
            window.append({"role": "user", "text": str(turn["user"])[:500]})
        if turn.get("assistant"):
            window.append({"role": "bot", "text": str(turn["assistant"])[:1000]})
    return window[-6:]


def _last_turn(turns: tuple[dict[str, str], ...]) -> dict[str, str]:
    for turn in reversed(turns):
        user, assistant = str(turn.get("user") or "")[:500], str(turn.get("assistant") or "")[:1000]
        if user or assistant:
            return {"bot_question": assistant, "client_answer": user}
    return {"bot_question": "", "client_answer": ""}


def _visible_options_context(state: ConversationState) -> str:
    names = [str(option.name or "").strip() for option in state.visible_options[:5]]
    return f"Текущие варианты: {'; '.join(name for name in names if name)}"[:600] if any(names) else ""


def _selected_object_context(state: ConversationState) -> dict[str, Any]:
    selected = state.selected_enriched or state.find_visible_option(state.selected_option_name)
    if selected is None and len(state.visible_options) == 1:
        selected = state.visible_options[0]
    return {"canonical_name": selected.name, "present_fact_fields": list(present_fact_names(selected))} if selected else {}


def _dynamic_fields_context(state: ConversationState) -> list[str]:
    selected = state.selected_enriched or state.find_visible_option(state.selected_option_name)
    if selected is None and len(state.visible_options) == 1:
        selected = state.visible_options[0]
    present = set(present_fact_names(selected))
    return [fact for fact in ALLOWED_FACTS if fact not in present]


def _pending_scenario_for_planner(state: ConversationState) -> dict[str, Any] | None:
    contract = reply_contract_for_pending(state.pending_followup)
    if contract is None:
        return None
    selected = str(state.selected_option_name or (state.selected_enriched.name if state.selected_enriched else "") or "").strip()
    planner_context = dict(contract.planner_context)
    scope_policy = str(planner_context.pop("scope_policy", "selected_or_current"))
    has_selected = bool(selected and state.find_visible_option(selected))
    scope = "one" if has_selected else "one" if scope_policy == "selected_required" else "all" if state.visible_options else "unknown"
    if not has_selected:
        selected = ""
    context = {**planner_context, "scope": scope}
    if planner_context.get("requested_facts_policy") == "dialog_focus":
        context["requested_facts"] = [fact for fact in state.dialog_focus.last_requested_facts if fact]
    elif planner_context.get("requested_facts_policy") == "pending_action" and state.pending_action:
        context["requested_facts"] = list(state.pending_action.fact_keys)
    elif isinstance(planner_context.get("default_requested_facts"), (list, tuple)):
        context["requested_facts"] = list(planner_context["default_requested_facts"])
    if scope == "one" and selected:
        context["selected_option_name"] = selected
    return {"id": contract.id, "allowed_reply_outcomes": list(contract.allowed_outcomes), "context": context}


def _semantic_plan_from_semantic_result(semantic: SemanticPlannerResult, decision: DerivedPlannerDecision, raw_plan: dict[str, Any], *, query_text: str) -> SemanticPlan:
    operation = _operation_from_decision(decision, raw_plan)
    requested = tuple(fact for fact in decision.requested_facts if fact in ALLOWED_FACTS)
    needed = tuple(fact for fact in decision.facts_needed if fact in ALLOWED_FACTS and (not requested or fact in requested))
    return SemanticPlan(
        operation=operation, query_text=query_text or None, intent=decision.intent or semantic.scenario_change or None,
        constraints_delta=decision.constraints_patch if isinstance(decision.constraints_patch, dict) else {},
        reference=str(semantic.named_object_reference or semantic.selected_reference or "") or None,
        selected_option_name=decision.selected_option_name, scope=decision.scope,
        operator_consent=_operator_consent(raw_plan), explicit_operator_request=bool(raw_plan.get("explicit_operator_request")),
        operator_reason=str(raw_plan.get("operator_reason") or decision.reason or "") or None,
        followup_outcome=str(raw_plan.get("followup_outcome") or "") or None,
        resolved_subject=decision.resolved_subject if decision.resolved_subject in ALLOWED_SUBJECTS else None,
        resolved_intent=decision.resolved_intent, requested_facts=requested, facts_needed=needed,
        requires_enrichment=bool(decision.needs_enrichment or needed), focus_action=decision.focus_action,
        domain_relation=semantic.domain_relation, confidence=decision.confidence, clarification=decision.clarification or None,
        facets=list(dict.fromkeys((*semantic.scenario_needs, *semantic.requested_comparison)))[:12],
        fresh_search=semantic.requests_new_objects is True,
    )


def _operation_from_decision(decision: DerivedPlannerDecision, raw_plan: Mapping[str, Any]) -> str:
    raw = str(raw_plan.get("operation") or "").strip()
    if "invalid_operation" in decision.errors:
        return "__invalid_semantic_operation__"
    if raw in {"search", "new_search", "refine_search", "expand_more"} or decision.action == "search": return "search"
    if raw == "answer_open_question": return raw
    if raw == "off_topic" or decision.action == "off_topic": return "off_topic"
    if raw in {"financing", "clarify_financing"} and decision.action == "answer_current_options": return raw
    if decision.action == "lookup_object": return "lookup_object"
    if decision.action == "operator_contact": return "operator"
    if decision.action == "answer_current_options": return "select_option" if decision.dialog_action == "select_option" and decision.selected_option_name else "current_options"
    return "freeform"


def _operator_consent(raw_plan: Mapping[str, Any]) -> bool | None:
    if isinstance(raw_plan.get("operator_consent"), bool):
        return raw_plan["operator_consent"]
    consent = str((raw_plan.get("operator_contact") or {}).get("consent") or "").strip().lower() if isinstance(raw_plan.get("operator_contact"), Mapping) else ""
    return True if consent == "granted" else False if consent == "refused" else None


def _keep_transition_accepted_legacy_operation(semantic: SemanticPlannerResult, raw_plan: Mapping[str, Any]) -> SemanticPlannerResult:
    raw = str(raw_plan.get("operation") or "").strip()
    if raw not in {"financing", "clarify_financing", "compare_current", "compare_options", "answer_open_question", "off_topic"} or "invalid_operation" not in semantic.errors:
        return semantic
    operation = "current_options" if raw in {"financing", "clarify_financing", "compare_current", "compare_options"} else raw
    return replace(semantic, raw_legacy_operation=operation, errors=tuple(error for error in semantic.errors if error != "invalid_operation"))


def _drop_legacy_search_reference(semantic: SemanticPlannerResult, raw_plan: Mapping[str, Any]) -> SemanticPlannerResult:
    if str(raw_plan.get("operation") or "").strip() in {"search", "new_search", "refine_search", "expand_more"} and "selected_reference" not in raw_plan:
        return replace(semantic, selected_reference=None)
    return semantic


def _inherit_selected_scope(plan: TurnPlan, state: ConversationState) -> TurnPlan:
    selected = str(state.selected_option_name or "").strip()
    action = state.pending_action
    if state.pending_followup == FINANCING_CONSENT_FOLLOWUP and action and action.status == "pending":
        selected = str(state.selected_entity.display_name if state.selected_entity else selected).strip()
        facts = tuple(action.fact_keys)
        if isinstance(plan, ExecutableTurn):
            return replace(plan, selected_option_name=selected or plan.selected_option_name, reference=selected or plan.reference, scope="one", requested_facts=facts, facts_needed=facts, intent="mortgage")
        return replace(plan, operation="select_option", selected_option_name=selected or plan.selected_option_name, reference=selected or plan.reference, scope="one", requested_facts=facts, facts_needed=facts, intent="mortgage")
    if not selected or not state.find_visible_option(selected):
        return plan
    if isinstance(plan, ExecutableTurn):
        if plan.scope != "all" and plan.goal in {IntentGoal.ANSWER_CURRENT, IntentGoal.COMPARE_CURRENT, IntentGoal.RECOMMEND_CURRENT, IntentGoal.ANSWER_OPEN_QUESTION, IntentGoal.OPERATOR}:
            return replace(plan, selected_option_name=plan.selected_option_name or selected, reference=plan.reference or selected, scope="one")
        return plan
    financing = str(plan.intent or "").lower() in {"mortgage", "financing"} or "mortgage" in {str(facet).lower() for facet in plan.facets}
    if (plan.scope == "all" and not financing) or plan.operation not in {"current_options", "answer_current_options", "financing", "clarify_financing", "freeform", "conversation"}:
        return plan
    return replace(plan, operation="financing" if financing else "select_option", selected_option_name=selected, reference=plan.reference or selected, scope="one")
