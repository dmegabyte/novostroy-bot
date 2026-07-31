from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Any, Mapping

from .contracts import IntentGoal, IntentPlanV3
from .fact_context import ALLOWED_FACTS, ALLOWED_SUBJECTS, FOCUS_ACTIONS, normalize_facts as normalize_semantic_facts, normalize_focus_action, normalize_subject
from .scenario_recipes import reply_contract_for_pending
from .state import ConversationState


UNKNOWN = "unknown"
BOOL_UNKNOWN = "unknown"

CANONICAL_INTENTS = {"investment", "rental", "family", "life", "mortgage", "unknown"}
SCENARIO_ALIASES = {"financing": "mortgage", "finance": "mortgage"}
SCENARIO_NEEDS = ("family", "rental", "investment", "life", "financing")
SCENARIO_NEED_ALIASES = {"mortgage": "financing", "finance": "financing"}
RESPONSE_VIEWPOINTS = {"investment", "rental", "family", "life", "financing", "unchanged"}
DOMAIN_RELATIONS = {"in_domain", "off_topic", "unknown"}
VIEWPOINT_TO_INTENT = {"financing": "mortgage"}
CANONICAL_CONSTRAINT_CATEGORIES = {"hard", "preferences", "unknown"}
SEMANTIC_PARAM_ALLOWLIST = {
    "location", "locations", "district", "districts", "metro", "near_metro",
    "rooms", "room_type", "max_price", "max_budget_m", "min_price", "down_payment",
    "area_min_m2", "area_max_m2", "finishing", "renovation", "ready",
    "stage", "ready_quarter", "delivery_visible", "project_ready_secondary",
    "property_metro", "purpose", "schools", "kindergartens", "parks", "shops",
    "family_infrastructure", "discount", "installment", "payment_by_installments",
}
SEMANTIC_PARAM_ALIASES = {
    "budget_max": "max_price",
    "price_max": "max_price",
    "max_budget": "max_price",
    "budget": "max_price",
    "room_count": "rooms",
    "rooms_count": "rooms",
    "location_name": "location",
    "locations_name": "location",
    "district_name": "district",
    "metro_name": "metro",
    "initial_payment": "down_payment",
}
SEMANTIC_PREFERENCE_ALIASES = {
    "finance_preference": "finance_preference",
    "mortgage": "finance_preference",
    "financing": "finance_preference",
    "finance": "finance_preference",
}
SENSITIVE_KEY_RE = re.compile(r"phone|телефон|contact|client_id|chat_id|site_id|sender|token|secret|raw|payload|dialog_window", re.I)


@dataclass(frozen=True)
class SemanticPlannerResult:
    user_goal: str = ""
    refers_to_existing_objects: bool | str = BOOL_UNKNOWN
    requests_new_objects: bool | str = BOOL_UNKNOWN
    selected_reference: str | int | float | None = None
    named_object_reference: str | None = None
    requested_comparison: tuple[str, ...] = ()
    scenario_needs: tuple[str, ...] = ()
    response_viewpoint: str = "unchanged"
    scenario_change: str | None = None
    constraints_delta: dict[str, Any] = field(default_factory=lambda: {"hard": {}, "preferences": {}, "unknown": {}})
    requires_enrichment: bool = False
    resolved_subject: str | None = None
    resolved_intent: str | None = None
    requested_facts: tuple[str, ...] = ()
    facts_needed: tuple[str, ...] = ()
    focus_action: str = "keep"
    domain_relation: str = "unknown"
    clarification: str | None = None
    confidence: float = 0.0
    reason: str = ""
    errors: tuple[str, ...] = ()
    raw_legacy_operation: str | None = None


@dataclass(frozen=True)
class DerivedPlannerDecision:
    action: str
    target: str
    search_policy: str
    scope: str
    selected_option_name: str | None
    needs_search: bool
    needs_enrichment: bool
    context_source: str
    dialog_action: str
    search_profile: str
    intent: str
    intent_policy: str
    constraints_patch: dict[str, Any]
    facts_needed: tuple[str, ...]
    requested_facts: tuple[str, ...]
    resolved_subject: str | None
    resolved_intent: str | None
    focus_action: str
    clarification: str
    confidence: float
    reason: str
    errors: tuple[str, ...] = ()
    domain_relation: str = "unknown"


@dataclass(frozen=True)
class IntentPlanValidation:
    ok: bool
    plan: IntentPlanV3 | None
    errors: tuple[str, ...]
    repairable: bool


VALID_INTENT_PLAN_VIEWPOINTS = {"family", "life", "rental", "investment", "financing", "unchanged"}
_SELECTED_OPTION_ALLOWED_GOALS = {
    IntentGoal.ANSWER_SELECTED,
    IntentGoal.ANSWER_CURRENT,
    IntentGoal.COMPARE_CURRENT,
    IntentGoal.RECOMMEND_CURRENT,
    IntentGoal.ANSWER_OPEN_QUESTION,
    IntentGoal.OPERATOR,
}
_OPERATOR_CONSENT_ALLOWED_GOALS = {IntentGoal.OPERATOR, IntentGoal.RESUME_PENDING}


def validate_intent_plan_v3(
    raw_or_plan: Any,
    state: ConversationState,
    *,
    allowed_facts: tuple[str, ...] | list[str] | set[str] = ALLOWED_FACTS,
) -> IntentPlanValidation:
    """Validate additive IntentPlanV3 without deriving or changing runtime goals."""

    plan: IntentPlanV3
    if isinstance(raw_or_plan, IntentPlanV3):
        plan = raw_or_plan
    elif isinstance(raw_or_plan, Mapping):
        try:
            plan = IntentPlanV3.from_dict(raw_or_plan)
        except (TypeError, ValueError) as exc:
            code = _intent_plan_parse_error_code(exc)
            return IntentPlanValidation(ok=False, plan=None, errors=(code,), repairable=_is_intent_plan_parse_repairable(code))
    else:
        return IntentPlanValidation(ok=False, plan=None, errors=("invalid_shape",), repairable=True)

    errors: list[str] = []
    allowed_fact_set = {str(item).strip() for item in allowed_facts if str(item).strip()}
    if any(fact not in allowed_fact_set for fact in plan.requested_facts):
        errors.append("invalid_requested_fact")

    if plan.viewpoint not in VALID_INTENT_PLAN_VIEWPOINTS:
        errors.append("invalid_viewpoint")

    if plan.comparison_option_names:
        if plan.goal != IntentGoal.COMPARE_CURRENT:
            errors.append("invalid_comparison_options_scope")
        else:
            if plan.selected_option_name or plan.named_object_reference:
                errors.append("comparison_option_fields_conflict")
            if any(state.find_visible_option(name) is None for name in plan.comparison_option_names):
                errors.append("comparison_option_not_visible")

    if plan.goal == IntentGoal.ANSWER_SELECTED:
        if not plan.selected_option_name or state.find_visible_option(plan.selected_option_name) is None:
            errors.append("selected_option_not_visible")
    elif plan.selected_option_name and plan.goal not in _SELECTED_OPTION_ALLOWED_GOALS:
        errors.append("invalid_selected_option_scope")

    compare_named_reference_in_visible_options = (
        plan.goal == IntentGoal.COMPARE_CURRENT
        and bool(plan.named_object_reference)
        and state.find_visible_option(plan.named_object_reference) is not None
    )

    if plan.goal == IntentGoal.LOOKUP_OBJECT:
        if not plan.named_object_reference:
            errors.append("missing_named_reference")
    elif plan.named_object_reference and not compare_named_reference_in_visible_options:
        errors.append("invalid_named_reference_scope")

    if plan.goal == IntentGoal.CLARIFY:
        if not plan.clarification:
            errors.append("missing_clarification")
    elif plan.clarification:
        errors.append("clarification_on_non_clarify")

    if plan.operator_consent is not None and plan.goal not in _OPERATOR_CONSENT_ALLOWED_GOALS:
        errors.append("invalid_operator_consent_scope")

    if plan.followup_outcome is not None:
        contract = reply_contract_for_pending(state.pending_followup)
        if contract is None:
            errors.append("followup_outcome_without_pending")
        elif plan.followup_outcome not in contract.allowed_outcomes:
            errors.append("followup_outcome_not_allowed")

    stable_errors = tuple(sorted(set(errors)))
    if not stable_errors and compare_named_reference_in_visible_options:
        plan = replace(plan, selected_option_name=None, named_object_reference=None)
    return IntentPlanValidation(ok=not stable_errors, plan=plan, errors=stable_errors, repairable=False)


def _intent_plan_parse_error_code(exc: BaseException) -> str:
    message = str(exc)
    if "input must be a mapping" in message:
        return "invalid_shape"
    if "unknown IntentPlanV3 fields" in message:
        return "unknown_field"
    if "schema_version" in message:
        return "invalid_schema_version"
    if "invalid IntentPlanV3 goal" in message:
        return "invalid_goal"
    if "viewpoint is required" in message:
        return "missing_viewpoint"
    if "constraints_delta" in message:
        return "invalid_constraints_delta"
    if "operator_consent" in message:
        return "invalid_operator_consent"
    if "explicit_operator_request" in message:
        return "invalid_explicit_operator_request"
    if "followup_outcome" in message:
        return "invalid_followup_outcome"
    if "confidence" in message:
        return "invalid_confidence"
    if "comparison_option_names" in message:
        return "invalid_comparison_option_names"
    return "invalid_schema"


def _is_intent_plan_parse_repairable(code: str) -> bool:
    return code in {
        "invalid_shape",
        "unknown_field",
        "invalid_schema_version",
        "invalid_goal",
        "missing_viewpoint",
        "invalid_constraints_delta",
        "invalid_operator_consent",
        "invalid_explicit_operator_request",
        "invalid_followup_outcome",
        "invalid_confidence",
        "invalid_comparison_option_names",
        "invalid_schema",
    }


def empty_constraints() -> dict[str, Any]:
    return {"hard": {}, "preferences": {}, "unknown": {}}


def safe_constraints_delta_with_errors(value: Any) -> tuple[dict[str, Any], tuple[str, ...]]:
    constraints = empty_constraints()
    errors: list[str] = []
    if not isinstance(value, dict):
        return constraints, ()

    def normalize_fields(fields: Any, *, category: str) -> dict[str, Any]:
        if not isinstance(fields, dict):
            return {}
        out: dict[str, Any] = {}
        for raw_key, raw_value in fields.items():
            key = str(raw_key or "").strip()
            if not key or SENSITIVE_KEY_RE.search(key):
                continue
            preference_key = SEMANTIC_PREFERENCE_ALIASES.get(key)
            if preference_key:
                constraints["preferences"][preference_key] = raw_value
                continue
            key = SEMANTIC_PARAM_ALIASES.get(key, key)
            if key == "purpose" and category != "preferences":
                errors.append("unsupported_constraint:purpose")
                continue
            if key not in SEMANTIC_PARAM_ALLOWLIST:
                errors.append(f"unsupported_constraint:{key}")
                continue
            out[key] = raw_value
        return out

    if any(key in value for key in CANONICAL_CONSTRAINT_CATEGORIES):
        for category, fields in value.items():
            if category in CANONICAL_CONSTRAINT_CATEGORIES and isinstance(fields, dict):
                normalized = normalize_fields(fields, category=category)
                constraints[category].update(normalized)
        return constraints, tuple(sorted(set(errors)))
    constraints["hard"] = normalize_fields(value, category="hard")
    return constraints, tuple(sorted(set(errors)))


def safe_constraints_delta(value: Any) -> dict[str, Any]:
    constraints, _errors = safe_constraints_delta_with_errors(value)
    return constraints


def constraints_have_values(constraints: dict[str, Any]) -> bool:
    return any(bool(constraints.get(category)) for category in CANONICAL_CONSTRAINT_CATEGORIES)


def current_context_constraints(constraints: dict[str, Any]) -> dict[str, Any]:
    """Keep explicit state-only financing facts without applying search filters."""
    hard = constraints.get("hard") if isinstance(constraints.get("hard"), dict) else {}
    down_payment = hard.get("down_payment")
    return {
        "hard": {"down_payment": down_payment} if down_payment is not None else {},
        "preferences": {},
        "unknown": {},
    }


def normalize_bool_unknown(value: Any) -> bool | str:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return BOOL_UNKNOWN


def normalize_scenario(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    text = SCENARIO_ALIASES.get(text, text)
    return text if text in CANONICAL_INTENTS and text != "unknown" else None


def normalize_response_viewpoint(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"mortgage", "finance"}:
        text = "financing"
    if text in {"unknown", "none", "null", ""}:
        return "unchanged"
    return text if text in RESPONSE_VIEWPOINTS else "unchanged"


def normalize_domain_relation(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in DOMAIN_RELATIONS else "unknown"


def response_viewpoint_to_intent(viewpoint: str, current_intent: str) -> str:
    if viewpoint == "unchanged":
        return current_intent if current_intent in CANONICAL_INTENTS else "unknown"
    return VIEWPOINT_TO_INTENT.get(viewpoint, viewpoint)


def normalize_requested_comparison(value: Any) -> tuple[str, ...]:
    if value in (None, "", [], {}):
        return ()
    if isinstance(value, str):
        raw_items = re.split(r"[,/|]", value)
    elif isinstance(value, dict):
        raw_items = [key for key, enabled in value.items() if enabled]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]
    out: list[str] = []
    for item in raw_items:
        text = str(item or "").strip().lower()
        if text and text not in out:
            out.append(text)
    return tuple(out[:8])


def normalize_scenario_needs(value: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if value in (None, "", [], {}, ()): 
        return (), ()
    if isinstance(value, str):
        raw_items = re.split(r"[,/|]", value)
    elif isinstance(value, dict):
        raw_items = [key for key, enabled in value.items() if enabled]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]
    out: list[str] = []
    dropped: list[str] = []
    for item in raw_items:
        text = str(item or "").strip().lower()
        text = SCENARIO_NEED_ALIASES.get(text, text)
        if text in SCENARIO_NEEDS:
            if text not in out:
                out.append(text)
        elif text:
            dropped.append(text)
    return tuple(out[:5]), tuple(dropped)


def normalize_facts(value: Any, available_fact_fields: list[str] | tuple[str, ...] | set[str] | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    allowed = {str(item) for item in (available_fact_fields or []) if str(item).strip()} | set(ALLOWED_FACTS)
    if not allowed:
        allowed = {
            "name", "location", "district", "price", "price_min", "price_range",
            "rooms", "room_formats", "area", "ready", "finishing", "metro",
            "developer", "property_class", "infrastructure", "schools",
            "kindergartens", "parks", "yards", "playgrounds", "clinics",
            "sales_count", "sales_date", "ads_count", "discount",
        }
    raw = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    facts: list[str] = []
    dropped: list[str] = []
    for item in raw:
        fact = str(item or "").strip()
        if not fact:
            continue
        if fact in allowed:
            if fact not in facts:
                facts.append(fact)
        else:
            dropped.append(fact)
    return tuple(facts[:12]), tuple(dropped)


def option_names_from_state(state: dict[str, Any] | None) -> list[str]:
    names: list[str] = []
    if not isinstance(state, dict):
        return names
    for key in ("visible_options", "current_options", "last_options"):
        value = state.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            name = str((item or {}).get("name") if isinstance(item, dict) else "").strip()
            if name and name not in names:
                names.append(name)
    return names


def resolve_option_reference(state: dict[str, Any] | None, reference: Any) -> str | None:
    if reference in (None, "", [], {}):
        return None
    names = option_names_from_state(state)
    if isinstance(reference, (int, float)) and not isinstance(reference, bool):
        idx = int(reference) - 1
        return names[idx] if 0 <= idx < len(names) else None
    text = str(reference).strip()
    if text.isdigit():
        idx = int(text) - 1
        return names[idx] if 0 <= idx < len(names) else None
    folded = text.casefold()
    exact = [name for name in names if name.casefold() == folded]
    if len(exact) == 1:
        return exact[0]
    contains = [name for name in names if folded and (folded in name.casefold() or name.casefold() in folded)]
    if len(contains) == 1:
        return contains[0]
    # Legacy canonical adapter ожидает вернуть текст и валидирует membership
    # своим отдельным слоем. V2 semantic route проверяет membership ниже.
    return text if text else None


def state_primary_intent(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return "unknown"
    for source in (state, state.get("params") if isinstance(state.get("params"), dict) else {}):
        value = str((source or {}).get("primary_intent") or (source or {}).get("active_scenario") or (source or {}).get("purpose") or "").strip()
        if value in CANONICAL_INTENTS and value != "unknown":
            return value
    return "unknown"


def state_has_visible_options(state: dict[str, Any] | None) -> bool:
    return bool(option_names_from_state(state))


def state_has_active_search(state: dict[str, Any] | None) -> bool:
    if not isinstance(state, dict):
        return False
    return bool(state.get("last_search_snapshot") or state_has_visible_options(state) or state.get("params"))


def _safe_named_object_reference(value: Any) -> str | None:
    """Принимает только короткое текстовое название объекта, не номер списка."""

    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().split())
    if not text or len(text) > 100 or text.isdigit():
        return None
    folded = text.casefold().strip("«»\"' ")
    if folded in {"он", "она", "оно", "этот", "эта", "там", "первый", "второй", "третий", "вариант"}:
        return None
    if not re.search(r"[a-zа-яё]", folded, re.I):
        return None
    return text


def normalize_semantic_planner_result(
    data: dict[str, Any],
    *,
    available_fact_fields: list[str] | tuple[str, ...] | set[str] | None = None,
) -> SemanticPlannerResult:
    errors: list[str] = []
    constraints, constraint_errors = safe_constraints_delta_with_errors(data.get("constraints_delta") if "constraints_delta" in data else data.get("constraints_patch"))
    errors.extend(constraint_errors)
    requested_facts = normalize_semantic_facts(data.get("requested_facts"))
    raw_needed = data.get("facts_needed") or data.get("missing_fields")
    facts, dropped_facts = normalize_facts(raw_needed, available_fact_fields)
    facts = tuple(fact for fact in facts if fact in requested_facts) if requested_facts else facts
    errors.extend(f"unsupported_fact:{fact}" for fact in dropped_facts)
    subject = normalize_subject(data.get("resolved_subject") or data.get("subject"))
    if (data.get("resolved_subject") or data.get("subject")) and subject is None:
        errors.append("unsupported_subject")
    focus_action = normalize_focus_action(data.get("focus_action"))
    domain_relation = normalize_domain_relation(data.get("domain_relation"))
    try:
        confidence = float(data.get("confidence") if data.get("confidence") is not None else 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
        errors.append("invalid_confidence")
    confidence = max(0.0, min(1.0, confidence))

    # Backward-compatible ingestion for old local tests/mocks. The model prompt no
    # longer asks for operation/scope/needs_search/context_source.
    legacy_operation = str(data.get("operation") or "").strip() or None
    if legacy_operation and legacy_operation not in {
        "search", "new_search", "refine_search", "expand_more", "current_options", "select_option",
        "answer_open_question", "operator_contact", "operator", "clarify", "recover", "freeform", "conversation",
    }:
        errors.append("invalid_operation")
    selected_reference = data.get("selected_reference", data.get("reference", data.get("selected_option_name")))
    if selected_reference is not None and not isinstance(selected_reference, (str, int, float)):
        errors.append("invalid_selected_reference")
        selected_reference = None
    named_object_reference = _safe_named_object_reference(data.get("named_object_reference"))
    if data.get("named_object_reference") not in (None, "") and named_object_reference is None:
        errors.append("invalid_named_object_reference")
    scenario = normalize_scenario(data.get("scenario_change", data.get("intent", data.get("scenario"))))
    if "response_viewpoint" in data:
        raw_viewpoint = str(data.get("response_viewpoint") or "").strip().lower()
        response_viewpoint = normalize_response_viewpoint(data.get("response_viewpoint"))
        if raw_viewpoint and raw_viewpoint not in RESPONSE_VIEWPOINTS and raw_viewpoint not in {"mortgage", "finance", "unknown", "none", "null"}:
            errors.append("invalid_response_viewpoint")
    else:
        # Backward-compatible ingestion for old local tests/mocks that used
        # scenario_change/intent as the response-topic signal.
        response_viewpoint = normalize_response_viewpoint(scenario)
    refers = normalize_bool_unknown(data.get("refers_to_existing_objects"))
    requests_new = normalize_bool_unknown(data.get("requests_new_objects"))

    if legacy_operation in {"current_options", "select_option"} and refers == BOOL_UNKNOWN:
        refers = True
    if legacy_operation in {"search", "new_search", "refine_search", "expand_more"} and requests_new == BOOL_UNKNOWN:
        requests_new = True
    if legacy_operation == "clarify" and not data.get("clarification"):
        errors.append("clarification_missing")
    scenario_needs, dropped_needs = normalize_scenario_needs(data.get("scenario_needs"))
    errors.extend(f"unsupported_scenario_need:{need}" for need in dropped_needs)

    return SemanticPlannerResult(
        user_goal=str(data.get("user_goal") or data.get("goal") or data.get("reason") or "").strip(),
        refers_to_existing_objects=refers,
        requests_new_objects=requests_new,
        selected_reference=selected_reference,
        named_object_reference=named_object_reference,
        requested_comparison=normalize_requested_comparison(data.get("requested_comparison") or data.get("facets")),
        scenario_needs=scenario_needs,
        response_viewpoint=response_viewpoint,
        scenario_change=scenario,
        constraints_delta=constraints,
        requires_enrichment=bool(data.get("requires_enrichment") or data.get("needs_enrichment") or facts),
        resolved_subject=subject,
        resolved_intent=str(data.get("resolved_intent") or "").strip()[:80] or None,
        requested_facts=requested_facts,
        facts_needed=facts,
        focus_action=focus_action,
        domain_relation=domain_relation,
        clarification=str(data.get("clarification") or data.get("clarification_question") or "").strip() or None,
        confidence=confidence,
        reason=str(data.get("reason") or "").strip(),
        errors=tuple(sorted(set(errors))),
        raw_legacy_operation=legacy_operation,
    )


def derive_runtime_decision(semantic: SemanticPlannerResult, state: dict[str, Any] | None = None) -> DerivedPlannerDecision:
    current_intent = state_primary_intent(state)
    intent = response_viewpoint_to_intent(semantic.response_viewpoint, current_intent)
    if intent not in CANONICAL_INTENTS:
        intent = "unknown"
    if semantic.response_viewpoint == "unchanged" or intent == "unknown":
        intent_policy = "keep"
    elif current_intent == "unknown":
        intent_policy = "set"
    elif intent == current_intent:
        intent_policy = "keep"
    else:
        intent_policy = "change"

    available_names = option_names_from_state(state)
    selected_name = resolve_option_reference(state, semantic.selected_reference)
    selected_in_state = bool(selected_name and selected_name in available_names)
    named_reference = semantic.named_object_reference
    if not named_reference and not selected_in_state and semantic.raw_legacy_operation != "select_option":
        named_reference = _safe_named_object_reference(semantic.selected_reference)
    has_visible = state_has_visible_options(state)
    has_constraints = constraints_have_values(semantic.constraints_delta)
    explicit_new = semantic.requests_new_objects is True
    explicit_existing = semantic.refers_to_existing_objects is True
    legacy = semantic.raw_legacy_operation
    clarification = semantic.clarification or ""
    errors = list(semantic.errors)

    if semantic.domain_relation == "off_topic":
        return DerivedPlannerDecision(
            action="off_topic", target="none", search_policy="forbidden", scope="unknown",
            selected_option_name=None, needs_search=False, needs_enrichment=False, context_source="dialogue",
            dialog_action="conversation_answer", search_profile="none", intent=current_intent if current_intent != "unknown" else intent,
            intent_policy="keep", constraints_patch=empty_constraints(), facts_needed=(), requested_facts=semantic.requested_facts,
            resolved_subject=semantic.resolved_subject, resolved_intent=semantic.resolved_intent, focus_action="keep", clarification=clarification,
            confidence=semantic.confidence, reason=semantic.reason, errors=tuple(errors), domain_relation=semantic.domain_relation,
        )

    if "invalid_operation" in errors:
        errors.append("no_runtime_decision")
        return DerivedPlannerDecision(
            action="recover_dialogue", target="none", search_policy="forbidden", scope="unknown",
            selected_option_name=None, needs_search=False, needs_enrichment=False, context_source="dialogue",
            dialog_action="ask_clarification", search_profile="none", intent=intent, intent_policy="keep",
            constraints_patch=empty_constraints(), facts_needed=(), requested_facts=semantic.requested_facts,
            resolved_subject=semantic.resolved_subject, resolved_intent=semantic.resolved_intent, focus_action=semantic.focus_action, clarification=clarification,
            confidence=semantic.confidence, reason=semantic.reason, errors=tuple(sorted(set(errors))),
        )

    if legacy in {"current_options", "select_option"}:
        has_constraints = False

    if legacy in {"operator_contact", "operator"}:
        return DerivedPlannerDecision(
            action="operator_contact", target="operator", search_policy="forbidden", scope="unknown",
            selected_option_name=None, needs_search=False, needs_enrichment=False, context_source="dialogue",
            dialog_action="operator_live_check", search_profile="none", intent=intent, intent_policy=intent_policy,
            constraints_patch=empty_constraints(), facts_needed=semantic.facts_needed, requested_facts=semantic.requested_facts,
            resolved_subject=semantic.resolved_subject, resolved_intent=semantic.resolved_intent, focus_action=semantic.focus_action, clarification=clarification,
            confidence=semantic.confidence, reason=semantic.reason, errors=tuple(errors),
        )

    if named_reference:
        return DerivedPlannerDecision(
            action="lookup_object", target="named_object", search_policy="required", scope="one",
            selected_option_name=None, needs_search=True, needs_enrichment=False, context_source="named_object_lookup",
            dialog_action="lookup_named_object", search_profile="named_object", intent=intent,
            intent_policy=intent_policy, constraints_patch=semantic.constraints_delta,
            facts_needed=semantic.facts_needed, requested_facts=semantic.requested_facts,
            resolved_subject=semantic.resolved_subject, resolved_intent=semantic.resolved_intent,
            focus_action="switch", clarification=clarification, confidence=semantic.confidence,
            reason=semantic.reason, errors=tuple(errors), domain_relation=semantic.domain_relation,
        )

    if (semantic.selected_reference is not None and not selected_name and has_visible) or (legacy == "clarify"):
        if not clarification:
            clarification = "Уточните, пожалуйста, какой именно вариант вы имеете в виду?"
        return DerivedPlannerDecision(
            action="clarify", target="none", search_policy="forbidden", scope="unknown",
            selected_option_name=None, needs_search=False, needs_enrichment=False, context_source="dialogue",
            dialog_action="ask_clarification", search_profile="none", intent=intent, intent_policy="keep",
            constraints_patch=empty_constraints(), facts_needed=(), requested_facts=semantic.requested_facts,
            resolved_subject=semantic.resolved_subject, resolved_intent=semantic.resolved_intent, focus_action="clarify", clarification=clarification,
            confidence=semantic.confidence, reason=semantic.reason, errors=tuple(errors),
        )

    if selected_name:
        return DerivedPlannerDecision(
            action="answer_current_options", target="current_options", search_policy="forbidden", scope="one",
            selected_option_name=selected_name, needs_search=False, needs_enrichment=semantic.requires_enrichment,
            context_source="current_options", dialog_action="select_option", search_profile="none", intent=intent,
            intent_policy=intent_policy if semantic.response_viewpoint != "unchanged" else "keep",
            constraints_patch=current_context_constraints(semantic.constraints_delta),
            facts_needed=semantic.facts_needed, requested_facts=semantic.requested_facts, resolved_subject=semantic.resolved_subject,
            resolved_intent=semantic.resolved_intent, focus_action=semantic.focus_action, clarification=clarification, confidence=semantic.confidence,
            reason=semantic.reason, errors=tuple(errors),
        )

    search_requested = has_constraints or explicit_new or (legacy in {"search", "new_search", "refine_search", "expand_more"})
    if clarification and not search_requested:
        return DerivedPlannerDecision(
            action="clarify", target="none", search_policy="forbidden", scope="unknown",
            selected_option_name=None, needs_search=False, needs_enrichment=False, context_source="dialogue",
            dialog_action="ask_clarification", search_profile="none", intent=intent, intent_policy="keep",
            constraints_patch=empty_constraints(), facts_needed=(), requested_facts=semantic.requested_facts,
            resolved_subject=semantic.resolved_subject, resolved_intent=semantic.resolved_intent, focus_action="clarify", clarification=clarification,
            confidence=semantic.confidence, reason=semantic.reason, errors=tuple(errors),
        )

    if has_visible and explicit_existing and semantic.requests_new_objects is False:
        dialog_action = "consultation_answer" if intent == "mortgage" else "continue_from_memory"
        return DerivedPlannerDecision(
            action="answer_current_options", target="current_options", search_policy="forbidden", scope="all",
            selected_option_name=None, needs_search=False, needs_enrichment=semantic.requires_enrichment,
            context_source="current_options", dialog_action=dialog_action, search_profile="none", intent=intent,
            intent_policy=intent_policy if semantic.response_viewpoint != "unchanged" else "keep", constraints_patch=empty_constraints(),
            facts_needed=semantic.facts_needed, requested_facts=semantic.requested_facts, resolved_subject=semantic.resolved_subject,
            resolved_intent=semantic.resolved_intent, focus_action=semantic.focus_action, clarification=clarification, confidence=semantic.confidence,
            reason=semantic.reason, errors=tuple(errors),
        )

    if search_requested:
        dialog_action = "update_search" if state_has_active_search(state) else "new_search"
        search_profile = "investment" if intent in {"investment", "rental"} else (intent if intent in {"family", "mortgage"} else "generic")
        return DerivedPlannerDecision(
            action="search", target="new_search", search_policy="required", scope="unknown",
            selected_option_name=None, needs_search=True, needs_enrichment=False, context_source="new_search",
            dialog_action=dialog_action, search_profile=search_profile, intent=intent, intent_policy=intent_policy,
            constraints_patch=semantic.constraints_delta, facts_needed=(), requested_facts=(), resolved_subject=None,
            resolved_intent=semantic.resolved_intent, focus_action="clear", clarification="",
            confidence=semantic.confidence, reason=semantic.reason, errors=tuple(errors),
        )

    if has_visible and (explicit_existing or semantic.requests_new_objects is not True or legacy in {"current_options", "select_option"}):
        dialog_action = "consultation_answer" if intent == "mortgage" else "continue_from_memory"
        return DerivedPlannerDecision(
            action="answer_current_options", target="current_options", search_policy="forbidden", scope="all",
            selected_option_name=None, needs_search=False, needs_enrichment=semantic.requires_enrichment,
            context_source="current_options", dialog_action=dialog_action, search_profile="none", intent=intent,
            intent_policy=intent_policy if semantic.response_viewpoint != "unchanged" else "keep", constraints_patch=empty_constraints(),
            facts_needed=semantic.facts_needed, requested_facts=semantic.requested_facts, resolved_subject=semantic.resolved_subject,
            resolved_intent=semantic.resolved_intent, focus_action=semantic.focus_action, clarification=clarification, confidence=semantic.confidence,
            reason=semantic.reason, errors=tuple(errors),
        )

    errors.append("no_runtime_decision")
    return DerivedPlannerDecision(
        action="recover_dialogue", target="none", search_policy="forbidden", scope="unknown",
        selected_option_name=None, needs_search=False, needs_enrichment=False, context_source="dialogue",
        dialog_action="ask_clarification", search_profile="none", intent=intent, intent_policy="keep",
        constraints_patch=empty_constraints(), facts_needed=(), clarification="", confidence=semantic.confidence,
        reason=semantic.reason, errors=tuple(sorted(set(errors))), requested_facts=semantic.requested_facts,
        resolved_subject=semantic.resolved_subject, resolved_intent=semantic.resolved_intent, focus_action="clarify",
    )


def decision_to_dict(decision: DerivedPlannerDecision) -> dict[str, Any]:
    return {
        "action": decision.action,
        "target": decision.target,
        "search_policy": decision.search_policy,
        "scope": decision.scope,
        "selected_option_name": decision.selected_option_name,
        "needs_search": decision.needs_search,
        "needs_enrichment": decision.needs_enrichment,
        "context_source": decision.context_source,
        "dialog_action": decision.dialog_action,
        "search_profile": decision.search_profile,
        "intent": decision.intent,
        "intent_policy": decision.intent_policy,
        "constraints_patch": decision.constraints_patch,
        "facts_needed": list(decision.facts_needed),
        "requested_facts": list(decision.requested_facts),
        "resolved_subject": decision.resolved_subject,
        "resolved_intent": decision.resolved_intent,
        "focus_action": decision.focus_action,
        "domain_relation": decision.domain_relation,
        "clarification": decision.clarification,
        "confidence": decision.confidence,
        "reason": decision.reason,
        "errors": list(decision.errors),
    }


def semantic_to_dict(semantic: SemanticPlannerResult) -> dict[str, Any]:
    return {
        "user_goal": semantic.user_goal,
        "refers_to_existing_objects": semantic.refers_to_existing_objects,
        "requests_new_objects": semantic.requests_new_objects,
        "selected_reference": semantic.selected_reference,
        "named_object_reference": semantic.named_object_reference,
        "requested_comparison": list(semantic.requested_comparison),
        "scenario_needs": list(semantic.scenario_needs),
        "response_viewpoint": semantic.response_viewpoint,
        "scenario_change": semantic.scenario_change,
        "constraints_delta": semantic.constraints_delta,
        "requires_enrichment": semantic.requires_enrichment,
        "resolved_subject": semantic.resolved_subject,
        "resolved_intent": semantic.resolved_intent,
        "requested_facts": list(semantic.requested_facts),
        "facts_needed": list(semantic.facts_needed),
        "focus_action": semantic.focus_action,
        "domain_relation": semantic.domain_relation,
        "clarification": semantic.clarification,
        "confidence": semantic.confidence,
        "reason": semantic.reason,
        "errors": list(semantic.errors),
    }
