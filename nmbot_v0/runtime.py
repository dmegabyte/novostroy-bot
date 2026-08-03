from __future__ import annotations

from dataclasses import asdict, is_dataclass, replace
import inspect
import re
from typing import Any, Awaitable, Callable, Mapping

from .contracts import JsonDict, OptionCard, V0Answer, V0State, V0TurnResult
from .search_contract import (
    V0SearchRequest,
    normalize_search_output,
    parse_strict_json,
    validate_search_output,
)

from .card_normalizer import normalize_search_result
from .field_contract import V0_PRESENTATION_TRACE_FIELDS, v0_presentation_search_fields
from .presentation import build_shortlist_comparison_context, render_grounded_card_block, render_selected_lot_lines, selected_object_grounded_acknowledgement, shortlist_level_sparse_note


ScenarioSearchPort = Callable[[JsonDict], str | Mapping[str, Any] | Awaitable[str | Mapping[str, Any]]]


ALLOWED_DECISIONS = {"search", "current_options", "selected_object", "open_question", "operator", "off_topic"}
ALLOWED_DECISION_KEYS = {
    "action",
    "viewpoint",
    "params",
    "selected_option_name",
    "active_topic",
    "client_question",
    "requested_facts",
    "response_policy",
    "operator_reason",
    "followup_outcome",
    "confirmed_action",
    "confirmed_subject",
    "comparison_metric",
    "exclude_option_names",
}
ALLOWED_FOLLOWUP_OUTCOMES = {"accept", "decline", "new_question", "unclear"}
ALLOWED_CONFIRMED_ACTIONS = {"check_selected_availability", "check_current_options_financing"}
ALLOWED_COMPARISON_METRICS = {"price_min"}
OPERATOR_PHONE_QUESTION = "Оставите номер телефона, чтобы оператор проверил это и связался с вами?"
V0_CONTACT_PHONE_DIGITS_REQUEST = "Пришлите, пожалуйста, номер телефона."
SCENARIO_FORMAT_RECOVERY_MARKER: JsonDict = {"strict_json_only": True, "reason": "previous_output_invalid_strict_json"}
MALFORMED_SCENARIO_RETRY_MESSAGE = "Не получилось запустить подбор. Попробуйте, пожалуйста, ещё раз."
SELECTED_OBJECT_QUESTION = "Что ещё проверить по этому ЖК?"
SELECTED_OBJECT_PRESENTATION_QUESTION = "Проверить актуальные квартиры в этом ЖК?"
FINANCING_CHECK_ALL_QUESTION = "Проверить условия оплаты по всем этим вариантам?"
HARD_PARAM_KEYS = {"rooms", "max_price", "min_price", "ready", "finishing", "area_min_m2", "area_max_m2", "location", "district"}
SEMANTIC_RELAX_PARAM_KEYS = {"down_payment", "financing", "payment_type", "mortgage", "mortgage_terms", "installment"}
MAX_CONTEXT_TEXT_CHARS = 2000


class V0TurnProcessor:
    """V0 scenario/search processor with deterministic canonical rendering.

    The scenario port is the only executable port.  Canonical rendering owns
    client prose, card identity, facts, and state transitions.  ``answer`` is
    accepted only as an ignored compatibility keyword for an out-of-scope
    legacy adapter; it is not a V0 injection contract.
    """

    def __init__(self, *, scenario_search: ScenarioSearchPort, answer: object | None = None) -> None:
        self._scenario_search = scenario_search

    def process(self, user_text: str, state: V0State | None = None, *, conversation_ref: str = "local") -> V0TurnResult:
        input_state = state or V0State()
        current = input_state
        turn_context = _build_turn_context(str(user_text or ""), current, conversation_ref=conversation_ref)
        scenario_payload, scenario_errors, scenario_calls = _coerce_scenario_with_one_retry(self._scenario_search, turn_context)
        if scenario_errors:
            return _fallback(current, "malformed_scenario_output", scenario_errors)

        decision, decision_errors = _validate_decision(scenario_payload.get("decision"))
        if decision_errors:
            return _fallback(current, "invalid_scenario_decision", decision_errors)

        decision, current, pending_error = _apply_pending_followup(current, decision)
        if pending_error:
            return _fallback(current, "invalid_pending_followup", [pending_error])
        decision = _canonicalize_decision(decision)

        action = str(decision["action"])
        if action == "search":
            decision["params"] = merge_search_params(current.params, decision.get("params", {}))
        viewpoint = _resolved_viewpoint(current, decision, action)
        decision["viewpoint"] = viewpoint
        decision["active_topic"] = viewpoint

        validated_cards: tuple[OptionCard, ...] = ()
        normalized_params: JsonDict = {}
        search_validation: JsonDict = {"skipped": True}
        if action == "search":
            raw_search = scenario_payload.get("search")
            search_result, validation, errors = _validated_search(raw_search, viewpoint=viewpoint, user_text=str(user_text or ""), decision=decision)
            search_validation = validation
            if errors or not validation.get("ok", False):
                return _fallback(current, "invalid_search_output", errors, diagnostics={"search_validation": validation})
            validated_cards = _search_cards_without_shown_options(search_result, decision, current) if search_result is not None else ()
            normalized_params = _state_search_params(search_result.params, decision.get("params", {})) if search_result is not None else {}
            if search_result is not None and not validated_cards:
                if scenario_calls >= 2:
                    search_validation = _merge_search_recovery_validation(search_validation, {"attempted": False, "outcome": "skipped_budget_exhausted"})
                    decision = _with_empty_search_operator_decision(decision)
                else:
                    recovered_cards, recovery_validation = _recover_empty_search_once(
                        self._scenario_search,
                        turn_context,
                        decision,
                        current,
                        viewpoint=viewpoint,
                        user_text=str(user_text or ""),
                    )
                    search_validation = _merge_search_recovery_validation(search_validation, recovery_validation)
                    if recovered_cards:
                        validated_cards = recovered_cards
                    else:
                        decision = _with_empty_search_operator_decision(decision)
        elif action in {"current_options", "selected_object", "open_question", "operator"}:
            validated_cards = _cards_for_existing_scope(current, decision, action)
            if action == "current_options":
                validated_cards = _cards_for_current_options_decision(validated_cards, decision)
            if action == "selected_object":
                validated_cards, current, search_validation = _maybe_enrich_selected_card(
                    current,
                    decision,
                    scenario_payload.get("search"),
                    viewpoint=viewpoint,
                    user_text=str(user_text or ""),
                )
        elif action == "off_topic":
            validated_cards = ()

        answer, answer_errors = _build_canonical_answer(allowed_cards=validated_cards, decision=decision, state=current)
        if answer_errors:
            return _fallback(input_state, "canonical_answer_failure", answer_errors)

        final_message = answer.text()
        next_state = _next_state(current, decision, cards=validated_cards, normalized_params=normalized_params, action=action, answer=answer, published_message=final_message)
        return V0TurnResult(ok=True, state=next_state, answer=answer, message=final_message, diagnostics={"decision": decision, "search_validation": search_validation})

    async def process_async(self, user_text: str, state: V0State | None = None, *, conversation_ref: str = "local") -> V0TurnResult:
        input_state = state or V0State()
        current = input_state
        turn_context = _build_turn_context(str(user_text or ""), current, conversation_ref=conversation_ref)
        scenario_payload, scenario_errors, scenario_calls = await _coerce_scenario_with_one_retry_async(self._scenario_search, turn_context)
        if scenario_errors:
            return _fallback(current, "malformed_scenario_output", scenario_errors)

        decision, decision_errors = _validate_decision(scenario_payload.get("decision"))
        if decision_errors:
            return _fallback(current, "invalid_scenario_decision", decision_errors)

        decision, current, pending_error = _apply_pending_followup(current, decision)
        if pending_error:
            return _fallback(current, "invalid_pending_followup", [pending_error])
        decision = _canonicalize_decision(decision)

        action = str(decision["action"])
        if action == "search":
            decision["params"] = merge_search_params(current.params, decision.get("params", {}))
        viewpoint = _resolved_viewpoint(current, decision, action)
        decision["viewpoint"] = viewpoint
        decision["active_topic"] = viewpoint

        validated_cards: tuple[OptionCard, ...] = ()
        normalized_params: JsonDict = {}
        search_validation: JsonDict = {"skipped": True}
        if action == "search":
            raw_search = scenario_payload.get("search")
            search_result, validation, errors = _validated_search(raw_search, viewpoint=viewpoint, user_text=str(user_text or ""), decision=decision)
            search_validation = validation
            if errors or not validation.get("ok", False):
                return _fallback(current, "invalid_search_output", errors, diagnostics={"search_validation": validation})
            validated_cards = _search_cards_without_shown_options(search_result, decision, current) if search_result is not None else ()
            normalized_params = _state_search_params(search_result.params, decision.get("params", {})) if search_result is not None else {}
            if search_result is not None and not validated_cards:
                if scenario_calls >= 2:
                    search_validation = _merge_search_recovery_validation(search_validation, {"attempted": False, "outcome": "skipped_budget_exhausted"})
                    decision = _with_empty_search_operator_decision(decision)
                else:
                    recovered_cards, recovery_validation = await _recover_empty_search_once_async(
                        self._scenario_search,
                        turn_context,
                        decision,
                        current,
                        viewpoint=viewpoint,
                        user_text=str(user_text or ""),
                    )
                    search_validation = _merge_search_recovery_validation(search_validation, recovery_validation)
                    if recovered_cards:
                        validated_cards = recovered_cards
                    else:
                        decision = _with_empty_search_operator_decision(decision)
        elif action in {"current_options", "selected_object", "open_question", "operator"}:
            validated_cards = _cards_for_existing_scope(current, decision, action)
            if action == "current_options":
                validated_cards = _cards_for_current_options_decision(validated_cards, decision)
            if action == "selected_object":
                validated_cards, current, search_validation = _maybe_enrich_selected_card(
                    current,
                    decision,
                    scenario_payload.get("search"),
                    viewpoint=viewpoint,
                    user_text=str(user_text or ""),
                )
        elif action == "off_topic":
            validated_cards = ()

        answer, answer_errors = _build_canonical_answer(allowed_cards=validated_cards, decision=decision, state=current)
        if answer_errors:
            return _fallback(input_state, "canonical_answer_failure", answer_errors)

        final_message = answer.text()
        next_state = _next_state(current, decision, cards=validated_cards, normalized_params=normalized_params, action=action, answer=answer, published_message=final_message)
        return V0TurnResult(ok=True, state=next_state, answer=answer, message=final_message, diagnostics={"decision": decision, "search_validation": search_validation})


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _coerce_scenario_with_one_retry(port: ScenarioSearchPort, turn_context: JsonDict) -> tuple[JsonDict, list[str], int]:
    payload, errors = _coerce_object(port(turn_context))
    if errors and _scenario_errors_retryable(errors):
        retry_payload, retry_errors = _coerce_object(port(_with_scenario_format_recovery(turn_context)))
        return retry_payload, retry_errors, 2
    return payload, errors, 1


async def _coerce_scenario_with_one_retry_async(port: ScenarioSearchPort, turn_context: JsonDict) -> tuple[JsonDict, list[str], int]:
    payload, errors = _coerce_object(await _maybe_await(port(turn_context)))
    if errors and _scenario_errors_retryable(errors):
        retry_payload, retry_errors = _coerce_object(await _maybe_await(port(_with_scenario_format_recovery(turn_context))))
        return retry_payload, retry_errors, 2
    return payload, errors, 1


def _scenario_errors_retryable(errors: list[str]) -> bool:
    return bool(errors) and all(str(item).startswith("invalid_strict_json") or str(item) == "json_root_must_be_object" for item in errors)


def _with_scenario_format_recovery(turn_context: JsonDict) -> JsonDict:
    retry_context = dict(turn_context)
    retry_context["format_recovery"] = dict(SCENARIO_FORMAT_RECOVERY_MARKER)
    return retry_context


def _recover_empty_search_once(
    port: ScenarioSearchPort,
    turn_context: JsonDict,
    decision: JsonDict,
    state: V0State,
    *,
    viewpoint: str,
    user_text: str,
) -> tuple[tuple[OptionCard, ...], JsonDict]:
    payload, coerce_errors = _coerce_object(port(_with_empty_search_recovery_context(turn_context, decision)))
    return _validate_recovered_search(payload, coerce_errors, decision, state, viewpoint=viewpoint, user_text=user_text)


async def _recover_empty_search_once_async(
    port: ScenarioSearchPort,
    turn_context: JsonDict,
    decision: JsonDict,
    state: V0State,
    *,
    viewpoint: str,
    user_text: str,
) -> tuple[tuple[OptionCard, ...], JsonDict]:
    payload, coerce_errors = _coerce_object(await _maybe_await(port(_with_empty_search_recovery_context(turn_context, decision))))
    return _validate_recovered_search(payload, coerce_errors, decision, state, viewpoint=viewpoint, user_text=user_text)


def _validate_recovered_search(
    payload: JsonDict,
    coerce_errors: list[str],
    decision: JsonDict,
    state: V0State,
    *,
    viewpoint: str,
    user_text: str,
) -> tuple[tuple[OptionCard, ...], JsonDict]:
    if coerce_errors:
        return (), {"attempted": True, "outcome": "invalid", "error_count": len(coerce_errors)}
    search_result, validation, errors = _validated_search(payload.get("search"), viewpoint=viewpoint, user_text=user_text, decision=decision)
    if errors or not validation.get("ok", True):
        return (), {"attempted": True, "outcome": "invalid", "error_count": len(errors) + len(validation.get("errors") or ()), "field_trace": validation.get("field_trace", {})}
    cards = _search_cards_without_shown_options(search_result, decision, state) if search_result is not None else ()
    outcome = "recovered" if cards else "empty"
    return cards, {"attempted": True, "outcome": outcome, "field_trace": validation.get("field_trace", {}), "counts": validation.get("counts", {})}


def _with_empty_search_recovery_context(turn_context: JsonDict, decision: Mapping[str, Any]) -> JsonDict:
    params = _canonical_search_params(decision.get("params", {}))
    retained = {key: value for key, value in params.items() if key in HARD_PARAM_KEYS and value not in (None, "", [], {})}
    relaxed = {key: value for key, value in params.items() if key in SEMANTIC_RELAX_PARAM_KEYS and value not in (None, "", [], {})}
    requested_facts = list(_as_tuple_str(decision.get("requested_facts")))
    if requested_facts:
        relaxed["requested_facts"] = requested_facts[:10]
    recovery_context = dict(turn_context)
    recovery_context["search_recovery"] = {
        "reason": "valid_empty_search",
        "attempt": 1,
        "max_attempts": 1,
        "original_canonical_params": dict(params),
        "retained_hard_params": dict(retained),
        "relaxed_semantic_params": dict(relaxed),
        "rule": "Run exactly one fresh MCP search. Retain hard apartment constraints: rooms, location/district, min/max price, ready, finishing, area bounds. Relax only unsupported or unconfirmed semantic conditions such as down_payment, financing, or optional requested facts. If nothing is safely relaxable, retry the same hard criteria once. Return MCP facts only; no invented cards or prose.",
    }
    return recovery_context


def _merge_search_recovery_validation(original: JsonDict, recovery: JsonDict) -> JsonDict:
    merged = dict(original)
    merged["recovery"] = {key: value for key, value in recovery.items() if key != "field_trace"}
    if recovery.get("field_trace"):
        if recovery.get("outcome") == "recovered":
            merged["initial_field_trace"] = merged.get("field_trace", {})
            merged["field_trace"] = recovery.get("field_trace")
        else:
            merged["recovery_field_trace"] = recovery.get("field_trace")
    return merged


def _with_empty_search_operator_decision(decision: JsonDict) -> JsonDict:
    return {**decision, "_empty_search_operator_phone": True, "response_policy": "operator_phone_request"}


def _bounded_context_text(value: Any) -> str | None:
    text = str(value or "")[:MAX_CONTEXT_TEXT_CHARS]
    return text if text else None


def _build_turn_context(user_text: str, state: V0State, *, conversation_ref: str) -> JsonDict:
    return {
        "contract": "nmbot_v0_two_prompt_turn",
        "conversation_ref": conversation_ref,
        "user_text": _bounded_context_text(user_text) or "",
        "state": {
            "params": _safe_mapping(state.params),
            "visible_options": [_card_to_dict(card) for card in state.visible_options[:3]],
            "selected_option_name": state.selected_option_name,
            "active_topic": state.active_topic,
            "has_greeted": state.has_greeted,
            "last_answer_kind": state.last_answer_kind,
            "last_assistant_question": state.last_assistant_question,
            "previous_assistant_message": _bounded_context_text(state.previous_assistant_message),
            "answered_facts": list(state.answered_facts),
            "pending_action": state.pending_action,
            "pending_subject": state.pending_subject,
            "pending_topic": state.pending_topic,
            "selected_card": _card_to_dict(_cards_for_existing_scope(state, {"selected_option_name": state.selected_option_name}, "selected_object")[0]) if state.visible_options else None,
        },
        "rules": {
            "first_prompt": "scenario_and_mcp_search_only",
            "second_prompt": "answer_from_validated_brief_only",
            "max_visible_options": 3,
            "followup_outcome_schema": "followup_outcome=accept|decline|new_question|unclear|null; confirmed_action=check_selected_availability|check_current_options_financing|null; confirmed_subject=exact selected ЖК|all_current_options|null",
            "pending_followup_policy": "If pending_action exists, resolve short semantic affirmatives/negatives including typos against that pending action; do not use a regex word-list. Substantive new questions are new_question.",
            "selected_object_enrichment": "For selected_object, call MCP for exactly the canonical selected name, count=1, topic-useful facts only. Do not return similar shortlist.",
        },
    }


def _coerce_object(payload: str | Mapping[str, Any]) -> tuple[JsonDict, list[str]]:
    if isinstance(payload, Mapping):
        return dict(payload), []
    data, errors = parse_strict_json(str(payload or ""))
    return (data or {}), errors


def _validate_decision(raw: Any) -> tuple[JsonDict, list[str]]:
    if not isinstance(raw, Mapping):
        return {}, ["decision_must_be_object"]
    unknown = set(raw) - ALLOWED_DECISION_KEYS
    errors = ["decision_unknown_keys:" + ",".join(sorted(unknown))] if unknown else []
    action = str(raw.get("action") or "").strip()
    if action not in ALLOWED_DECISIONS:
        errors.append("decision_action_invalid")
    params = raw.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        errors.append("decision_params_must_be_object")
        params = {}
    decision = {key: raw.get(key) for key in ALLOWED_DECISION_KEYS if key in raw}
    decision["action"] = action
    decision["params"] = _safe_mapping(params)
    outcome = raw.get("followup_outcome")
    if outcome is not None:
        outcome_text = str(outcome or "").strip()
        if outcome_text not in ALLOWED_FOLLOWUP_OUTCOMES:
            errors.append("decision_followup_outcome_invalid")
        decision["followup_outcome"] = outcome_text or None
    confirmed_action = raw.get("confirmed_action")
    if confirmed_action is not None:
        action_text = str(confirmed_action or "").strip()
        if action_text and action_text not in ALLOWED_CONFIRMED_ACTIONS:
            errors.append("decision_confirmed_action_invalid")
        decision["confirmed_action"] = action_text or None
    if raw.get("confirmed_subject") is not None:
        decision["confirmed_subject"] = str(raw.get("confirmed_subject") or "").strip() or None
    excluded = raw.get("exclude_option_names")
    if excluded is not None:
        if not isinstance(excluded, (list, tuple)) or not all(isinstance(item, str) for item in excluded):
            errors.append("decision_exclude_option_names_must_be_string_array")
        else:
            decision["exclude_option_names"] = [str(item).strip() for item in excluded[:3] if str(item).strip()]
    metric = raw.get("comparison_metric")
    if metric is not None:
        metric_text = str(metric or "").strip()
        if metric_text and metric_text not in ALLOWED_COMPARISON_METRICS:
            errors.append("decision_comparison_metric_invalid")
        decision["comparison_metric"] = metric_text or None
    return decision, errors


def _canonicalize_decision(decision: JsonDict) -> JsonDict:
    out = dict(decision)
    if _is_financing_request(out):
        out["viewpoint"] = "financing"
        out["active_topic"] = "financing"
    return out


def _is_financing_request(decision: Mapping[str, Any]) -> bool:
    params = decision.get("params") if isinstance(decision.get("params"), Mapping) else {}
    requested = {str(item).strip().casefold() for item in _as_tuple_str(decision.get("requested_facts"))}
    if "down_payment" in params or params.get("financing") is True:
        return True
    if requested & {"mortgage_terms", "finance", "financing", "payment_terms", "down_payment"}:
        return True
    return False


def _canonical_search_params(value: Any) -> JsonDict:
    source = value if isinstance(value, Mapping) else {}
    out: JsonDict = {}
    aliases = {
        "budget": "max_price",
        "normalized_budget": "max_price",
        "budget_max": "max_price",
        "price_max": "max_price",
        "max_budget": "max_price",
        "room_count": "rooms",
        "rooms_count": "rooms",
        "location_name": "location",
        "delivered": "ready",
    }
    for raw_key, raw_value in source.items():
        key = aliases.get(str(raw_key), str(raw_key))
        value = _canonical_param_value(key, raw_value)
        if value not in (None, "", [], {}):
            out[key] = value
        elif key == "down_payment" and raw_value == 0:
            out[key] = 0
    return out


def _canonical_param_value(key: str, value: Any) -> Any:
    if key in {"max_price", "min_price"}:
        return _extract_money_number(value)
    if key == "ready":
        if value is True or (isinstance(value, (int, float)) and not isinstance(value, bool) and int(value) == 1):
            return "delivered"
        text = str(value or "").strip().casefold().replace("ё", "е")
        if text in {"1", "true", "yes", "delivered", "ready", "сдан", "сдано", "готов", "готово", "готовый", "дом сдан"}:
            return "delivered"
        return value
    if key in {"area_min_m2", "area_max_m2", "down_payment"}:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value) if float(value).is_integer() else float(value)
    if key in {"location", "district"}:
        text = str(value or "").strip()
        if text.casefold().replace("ё", "е") in {"центр", "в центре", "центр москвы"}:
            return "ЦАО"
    return value


def merge_search_params(previous: Any, incoming: Any) -> JsonDict:
    """Keep prior non-empty search constraints and canonically overlay new ones."""

    return {**_canonical_search_params(previous), **_canonical_search_params(incoming)}


def _extract_money_number(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return int(value if value > 1_000_000 else value * 1_000_000)
    text = str(value or "").replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(млн|миллион|million)?", text, re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    return int(number * 1_000_000) if number < 100_000 else int(number)


def _apply_pending_followup(state: V0State, decision: JsonDict) -> tuple[JsonDict, V0State, str | None]:
    pending_action = str(state.pending_action or "").strip()
    if not pending_action:
        return decision, state, None
    if pending_action == "contact_phone":
        outcome = str(decision.get("followup_outcome") or "").strip()
        if outcome == "accept":
            updated = dict(decision)
            updated.update(
                {
                    "action": "operator",
                    "response_policy": "operator_phone_request",
                    "selected_option_name": state.selected_option_name,
                    "active_topic": state.pending_topic or state.active_topic,
                    "viewpoint": state.pending_topic or state.active_topic or decision.get("viewpoint") or "life",
                    "_pending_resolution": "accept_contact_phone",
                }
            )
            return updated, state, None
        if outcome == "decline":
            updated = dict(decision)
            updated.update(
                {
                    "action": "open_question",
                    "response_policy": "answer_directly",
                    "selected_option_name": state.selected_option_name,
                    "active_topic": state.active_topic,
                    "viewpoint": state.active_topic or decision.get("viewpoint") or "life",
                }
            )
            updated["_pending_resolution"] = "decline_contact_phone"
            return updated, state, None
        if outcome == "new_question":
            updated = dict(decision)
            updated["_pending_resolution"] = f"{outcome}_contact_phone"
            return updated, state, None
        return decision, state, None
    outcome = str(decision.get("followup_outcome") or "").strip()
    if outcome == "accept":
        confirmed_action = str(decision.get("confirmed_action") or "").strip()
        if confirmed_action != pending_action:
            return decision, state, "pending_confirmed_action_mismatch"
        expected_subject = _pending_subject_for_validation(state)
        confirmed_subject = str(decision.get("confirmed_subject") or "").strip()
        if not confirmed_subject:
            return decision, state, "pending_confirmed_subject_required"
        if pending_action == "check_current_options_financing":
            subject_ok = confirmed_subject == "all_current_options"
        else:
            subject_ok = _normalize_name(confirmed_subject) == _normalize_name(expected_subject)
        if not subject_ok:
            return decision, state, "pending_confirmed_subject_mismatch"
        if pending_action not in ALLOWED_CONFIRMED_ACTIONS:
            return decision, state, "pending_action_unsupported"
        pending_resolution = f"accepted_{pending_action}"
        accepted = dict(decision)
        accepted.update(
            {
                "action": "operator",
                "response_policy": "operator_phone_request",
                "selected_option_name": expected_subject if pending_action == "check_selected_availability" else state.selected_option_name,
                "confirmed_subject": expected_subject,
                "active_topic": state.pending_topic or state.active_topic,
                "viewpoint": state.pending_topic or state.active_topic or decision.get("viewpoint") or "life",
                "_pending_resolution": pending_resolution,
            }
        )
        return accepted, state, None
    if outcome == "decline":
        if pending_action not in ALLOWED_CONFIRMED_ACTIONS:
            return decision, state, "pending_action_unsupported"
        declined = dict(decision)
        declined.update(
            {
                "action": "open_question",
                "response_policy": "answer_directly",
                "selected_option_name": state.selected_option_name,
                "active_topic": state.active_topic,
                "viewpoint": state.active_topic or decision.get("viewpoint") or "life",
                "_pending_resolution": f"declined_{pending_action}",
            }
        )
        return declined, state, None
    if outcome == "new_question":
        return {**decision, "_pending_resolution": "new_question"}, state, None
    return decision, state, None


def _validated_search(raw_search: Any, *, viewpoint: str, user_text: str, decision: Mapping[str, Any] | None = None):
    if not isinstance(raw_search, Mapping):
        return None, {}, ["search_must_be_object"]
    params = _canonical_search_params((decision or {}).get("params", {}))
    hard = {key: value for key, value in params.items() if key in HARD_PARAM_KEYS and value not in (None, "", [], {})}
    effective = dict(hard)
    request = V0SearchRequest(
        search_goal={"entity_type": "new_building_flat", "query_summary": user_text[:300], "explicit_terms": []},
        requested_hard=dict(hard),
        effective_hard=dict(effective),
        preferences={},
        response_viewpoint=_safe_viewpoint(viewpoint),
        base_viewpoint=None,
        available_fact_fields=v0_presentation_search_fields(),
        count=3,
    )
    normalized = normalize_search_output(_canonicalize_raw_search(raw_search), request)
    validation = validate_search_output(normalized, request)
    result = normalize_search_result(normalized)
    validation = {**validation, "field_trace": _field_boundary_trace(raw_search, result.shortlist(3))}
    return result, validation, []


def _canonicalize_raw_search(raw_search: Mapping[str, Any]) -> JsonDict:
    out = dict(raw_search)
    if isinstance(out.get("params"), Mapping):
        out["params"] = _canonical_search_params(out.get("params"))
    return out


def _state_search_params(normalized_params: Mapping[str, Any], decision_params: Any) -> JsonDict:
    return merge_search_params(normalized_params, decision_params)


def _search_cards_without_shown_options(search_result: Any, decision: Mapping[str, Any], state: V0State) -> tuple[OptionCard, ...]:
    cards = tuple(search_result.facts) if search_result.facts else tuple(search_result.near)
    raw = decision.get("exclude_option_names")
    requested = {_normalize_name(item) for item in raw} if isinstance(raw, (list, tuple)) else set()
    shown = {_normalize_name(card.name) for card in state.visible_options}
    excluded = requested & shown
    return tuple(card for card in cards if _normalize_name(card.name) not in excluded)[:3]


def _maybe_enrich_selected_card(
    state: V0State,
    decision: JsonDict,
    raw_search: Any,
    *,
    viewpoint: str,
    user_text: str,
) -> tuple[tuple[OptionCard, ...], V0State, JsonDict]:
    cards = _cards_for_existing_scope(state, decision, "selected_object")
    selected_name = str(decision.get("selected_option_name") or state.selected_option_name or "").strip()
    if not cards and selected_name and isinstance(raw_search, Mapping):
        return _bootstrap_selected_card(state, decision, raw_search, viewpoint=viewpoint, user_text=user_text)
    if not cards or not isinstance(raw_search, Mapping):
        return cards, state, {"skipped": True}
    selected = cards[0]
    raw_items = [item for key in ("facts", "near") for item in (raw_search.get(key) if isinstance(raw_search.get(key), list) else []) if isinstance(item, Mapping)]
    if len(raw_items) != 1:
        return cards, state, {"skipped": True, "selected_enrichment_rejected": "expected_exactly_one_card"}
    raw_name = str(raw_items[0].get("name") or raw_items[0].get("alias") or "").strip()
    if raw_name and _normalize_name(raw_name) != _normalize_name(selected.name):
        return cards, state, {"skipped": True, "selected_enrichment_rejected": "name_mismatch"}
    request = V0SearchRequest(
        search_goal={"entity_type": "new_building_flat", "query_summary": user_text[:300], "explicit_terms": [selected.name]},
        requested_hard={"name": selected.name},
        effective_hard={"name": selected.name},
        preferences={},
        response_viewpoint=_safe_viewpoint(viewpoint),
        base_viewpoint=None,
        available_fact_fields=v0_presentation_search_fields(),
        count=1,
    )
    normalized = normalize_search_output(dict(raw_search), request)
    validation = validate_search_output(normalized, request)
    if not validation.get("ok", False):
        return cards, state, {**validation, "safe_code": "search_validation_error", "selected_enrichment_rejected": "search_validation_error"}
    result = normalize_search_result(normalized)
    enriched_cards = result.shortlist(1)
    if len(enriched_cards) != 1:
        return cards, state, {**validation, "selected_enrichment_rejected": "no_single_card"}
    enriched = enriched_cards[0]
    if enriched.is_near or _normalize_name(enriched.name) != _normalize_name(selected.name):
        return cards, state, {**validation, "selected_enrichment_rejected": "name_mismatch"}
    merged = _merge_card(selected, enriched)
    visible = tuple(merged if _normalize_name(card.name) == _normalize_name(selected.name) else card for card in state.visible_options[:3])
    new_state = replace(state, visible_options=visible)
    return (merged,), new_state, {**validation, "selected_enrichment": "merged", "field_trace": _field_boundary_trace(raw_search, (merged,))}


def _bootstrap_selected_card(state: V0State, decision: JsonDict, raw_search: Mapping[str, Any], *, viewpoint: str, user_text: str) -> tuple[tuple[OptionCard, ...], V0State, JsonDict]:
    selected_name = str(decision.get("selected_option_name") or "").strip()
    raw_facts = raw_search.get("facts") if isinstance(raw_search.get("facts"), list) else []
    raw_near = raw_search.get("near") if isinstance(raw_search.get("near"), list) else []
    fact_items = [item for item in raw_facts if isinstance(item, Mapping)]
    if len(fact_items) != 1 or raw_near:
        named_state = replace(state, selected_option_name=selected_name) if selected_name and not fact_items and not raw_near else state
        return (), named_state, {"skipped": True, "selected_bootstrap_rejected": "expected_one_exact_fact"}
    raw_name = str(fact_items[0].get("name") or fact_items[0].get("alias") or "").strip()
    if not selected_name or _normalize_name(raw_name) != _normalize_name(selected_name):
        return (), state, {"skipped": True, "selected_bootstrap_rejected": "name_mismatch"}
    request = V0SearchRequest(
        search_goal={"entity_type": "new_building_flat", "query_summary": user_text[:300], "explicit_terms": [selected_name], "entity_reference": selected_name, "lookup_mode": "exact_named_object"},
        requested_hard={},
        effective_hard={},
        preferences={},
        response_viewpoint=_safe_viewpoint(viewpoint),
        base_viewpoint=None,
        available_fact_fields=v0_presentation_search_fields(),
        count=1,
    )
    normalized = normalize_search_output(_canonicalize_raw_search(raw_search), request)
    validation = validate_search_output(normalized, request)
    if not validation.get("ok", False):
        return (), state, {**validation, "safe_code": "search_validation_error", "selected_bootstrap_rejected": "search_validation_error"}
    result = normalize_search_result(normalized)
    cards = result.shortlist(1)
    if len(cards) != 1 or cards[0].is_near or _normalize_name(cards[0].name) != _normalize_name(selected_name):
        return (), state, {**validation, "selected_bootstrap_rejected": "name_mismatch"}
    new_state = replace(state, visible_options=cards, selected_option_name=cards[0].name)
    return cards, new_state, {**validation, "selected_bootstrap": "exact", "field_trace": _field_boundary_trace(raw_search, cards)}


def _merge_card(base: OptionCard, enriched: OptionCard) -> OptionCard:
    data = asdict(base)
    for key, value in asdict(enriched).items():
        if key == "name" or value in (None, "", (), [], {}, False):
            continue
        data[key] = value
    return OptionCard.from_dict(data)


def _field_boundary_trace(raw_search: Mapping[str, Any], cards: tuple[OptionCard, ...]) -> JsonDict:
    raw_cards: list[Mapping[str, Any]] = []
    for key in ("facts", "near"):
        value = raw_search.get(key)
        if isinstance(value, list):
            raw_cards.extend(item for item in value if isinstance(item, Mapping))
        if len(raw_cards) >= 3:
            break
    return {
        "cards": [
            {
                "raw_fields": _safe_field_names(raw),
                "normalized_fields": _safe_field_names(_card_to_dict(card)),
            }
            for raw, card in zip(raw_cards[:3], cards[:3])
        ]
    }


def _safe_field_names(value: Mapping[str, Any]) -> list[str]:
    fields: list[str] = []
    for key, item in value.items():
        name = str(key or "").strip()
        if name not in V0_PRESENTATION_TRACE_FIELDS and name not in OptionCard.__dataclass_fields__:
            continue
        if item in (None, "", (), [], {}, False):
            continue
        fields.append(name)
    return sorted(dict.fromkeys(fields))[:20]


def _cards_for_existing_scope(state: V0State, decision: JsonDict, action: str) -> tuple[OptionCard, ...]:
    if action == "operator":
        return ()
    cards = tuple(state.visible_options[:3])
    if action != "selected_object":
        return cards
    selected = str(decision.get("selected_option_name") or state.selected_option_name or "").strip().casefold()
    if not selected:
        return cards[:1]
    exact = tuple(card for card in cards if card.name.strip().casefold() == selected)
    return exact


def _cards_for_current_options_decision(cards: tuple[OptionCard, ...], decision: Mapping[str, Any]) -> tuple[OptionCard, ...]:
    if str(decision.get("comparison_metric") or "") != "price_min":
        return cards
    priced = [card for card in cards if isinstance(card.price_min, int)]
    if not priced:
        return ()
    return (min(priced, key=lambda card: card.price_min),)


def _build_canonical_answer(*, allowed_cards: tuple[OptionCard, ...], decision: JsonDict, state: V0State, expected: JsonDict | None = None, answer_kind: str | None = None, scope: str | None = None) -> tuple[V0Answer | None, list[str]]:
    try:
        expected = expected or _expected_answer_contract(decision, state=state)
        answer_kind = str(answer_kind if answer_kind is not None else expected["answer_kind"])
        scope = str(scope if scope is not None else expected["scope"])
    except Exception as exc:
        return None, [f"canonical_answer_contract_error:{type(exc).__name__}"]
    rendered_options: tuple[JsonDict, ...] = ()
    comparison_context: JsonDict = {}
    try:
        if expected["scope"] in {"shortlist", "one_card"}:
            viewpoint = _safe_viewpoint(decision.get("viewpoint") or state.active_topic or "life")
            comparison_context = build_shortlist_comparison_context(allowed_cards[:3], viewpoint) if expected["scope"] == "shortlist" else {}
            used_benefits: set[str] = set(comparison_context.get("used_benefit_keys", ()))
            display_params = {**state.params, **_safe_mapping(decision.get("params", {}))}
            rendered_options = tuple(
                _render_card_option(
                    index,
                    card,
                    state=state,
                    params=display_params,
                    selected=expected["answer_kind"] == "selected_object",
                    viewpoint=viewpoint,
                    cards=allowed_cards[:3],
                    used_benefits=used_benefits,
                    comparison_context=comparison_context,
                )
                for index, card in enumerate(allowed_cards[:3], 1)
            )
        deterministic = _deterministic_answer_parts(expected, cards=allowed_cards, decision=decision, state=state)
        if comparison_context.get("summary"):
            deterministic["intro"] = f'{deterministic["intro"]}\n{comparison_context["summary"]}'
        sparse_note = shortlist_level_sparse_note(allowed_cards[:3], comparison_context) if expected["scope"] == "shortlist" else ""
        if sparse_note:
            deterministic["missing_note"] = "\n".join(part for part in (deterministic["missing_note"], sparse_note) if part)
    except Exception as exc:
        return None, [f"canonical_answer_render_error:{type(exc).__name__}"]
    return V0Answer(
        answer_kind=answer_kind,
        scope=scope,
        intro=deterministic["intro"],
        options=rendered_options,
        recommendation=deterministic["recommendation"],
        missing_note=deterministic["missing_note"],
        final_question=expected["final_question"],
    ), []


def _next_state(current: V0State, decision: JsonDict, *, cards: tuple[OptionCard, ...], normalized_params: JsonDict, action: str, answer: V0Answer, published_message: str) -> V0State:
    params = (
        merge_search_params(current.params, merge_search_params(decision.get("params", {}), normalized_params))
        if action == "search"
        else {**current.params, **_safe_mapping(decision.get("params", {})), **_safe_mapping(normalized_params)}
    )
    visible_options = cards[:3] if action == "search" and cards else current.visible_options[:3]
    if action == "selected_object":
        selected = cards[0].name if cards else current.selected_option_name
    else:
        selected = str(decision.get("selected_option_name") or "").strip() or current.selected_option_name
    topic = str(decision.get("active_topic") or decision.get("viewpoint") or "").strip() or current.active_topic
    answered = tuple(dict.fromkeys((*current.answered_facts, *_facts_from_cards(cards))))
    pending_action = current.pending_action
    pending_subject = current.pending_subject
    pending_topic = current.pending_topic
    resolution = str(decision.get("_pending_resolution") or "")
    if resolution in {"accepted_check_selected_availability", "accepted_check_current_options_financing", "declined_check_selected_availability", "declined_check_current_options_financing", "new_question", "decline_contact_phone", "new_question_contact_phone"}:
        pending_action = None
        pending_subject = None
        pending_topic = None
    if action == "selected_object" and cards and selected:
        pending_action = "check_selected_availability"
        pending_subject = selected
        pending_topic = topic
    if action == "search" and _needs_financing_check_all(decision, current) and cards:
        pending_action = "check_current_options_financing"
        pending_subject = "all_current_options"
        pending_topic = "financing"
    if resolution not in {"decline_contact_phone", "new_question_contact_phone"} and (action == "operator" or str(decision.get("response_policy") or "").strip() == "operator_phone_request" or answer.scope == "operator_phone"):
        pending_action = "contact_phone"
        pending_subject = selected
        pending_topic = topic
    return replace(
        current,
        params=params,
        visible_options=tuple(visible_options),
        selected_option_name=selected,
        active_topic=topic,
        has_greeted=True,
        last_answer_kind=answer.answer_kind,
        last_assistant_question=answer.final_question,
        previous_assistant_message=_bounded_context_text(published_message),
        answered_facts=answered,
        pending_action=pending_action,
        pending_subject=pending_subject,
        pending_topic=pending_topic,
    )


def _fallback(state: V0State, code: str, errors: list[str], *, diagnostics: JsonDict | None = None) -> V0TurnResult:
    if code == "malformed_scenario_output":
        answer = V0Answer(
            answer_kind="runtime_retry",
            scope="malformed_scenario_retry",
            intro=MALFORMED_SCENARIO_RETRY_MESSAGE,
        )
        return V0TurnResult(ok=False, state=state, answer=answer, message=answer.text(), error_code=code, diagnostics={"errors": errors, **(diagnostics or {})})
    answer = V0Answer(
        answer_kind="operator",
        scope="operator_phone",
        intro="Сейчас не хочу отвечать наугад.",
        missing_note="Лучше передам вопрос оператору, чтобы всё проверили точно.",
        final_question=OPERATOR_PHONE_QUESTION,
    )
    return V0TurnResult(ok=False, state=state, answer=answer, message=answer.text(), error_code=code, diagnostics={"errors": errors, **(diagnostics or {})})


def _safe_viewpoint(value: Any) -> str:
    text = str(value or "life").strip()
    return text if text in {"investment", "rental", "family", "life", "financing"} else "life"


def _resolved_viewpoint(state: V0State, decision: JsonDict, action: str) -> str:
    if action in {"selected_object", "current_options"} and state.active_topic and decision.get("followup_outcome") != "new_question":
        return _safe_viewpoint(state.active_topic)
    return _safe_viewpoint(decision.get("active_topic") or decision.get("viewpoint") or state.active_topic or "life")


def _safe_mapping(value: Any) -> JsonDict:
    if not isinstance(value, Mapping):
        return {}
    safe: JsonDict = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            safe[str(key)[:80]] = item
    return safe


def _as_tuple_str(value: Any) -> tuple[str, ...]:
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    return tuple(dict.fromkeys(str(item).strip() for item in raw if str(item or "").strip()))


def _expected_answer_contract(decision: JsonDict, *, response_policy: str | None = None, state: V0State | None = None) -> JsonDict:
    action = str(decision.get("action") or "").strip()
    policy = response_policy if response_policy is not None else str(decision.get("response_policy") or "").strip()
    if decision.get("_empty_search_operator_phone") is True:
        return {"answer_kind": "operator", "scope": "operator_phone", "final_question": OPERATOR_PHONE_QUESTION}
    if action == "operator" or policy == "operator_phone_request":
        if decision.get("_pending_resolution") == "accept_contact_phone":
            return {"answer_kind": "operator", "scope": "operator_phone", "final_question": V0_CONTACT_PHONE_DIGITS_REQUEST}
        return {"answer_kind": "operator", "scope": "operator_phone", "final_question": OPERATOR_PHONE_QUESTION}
    if action == "selected_object":
        viewpoint = str(decision.get("viewpoint") or decision.get("active_topic") or (state.active_topic if state else "") or "life")
        return {"answer_kind": "selected_object", "scope": "one_card", "final_question": _selected_object_cta(viewpoint)}
    if action in {"search", "current_options"}:
        if action == "current_options" and str(decision.get("comparison_metric") or "") == "price_min":
            return {"answer_kind": "search_many", "scope": "one_card", "final_question": "Хотите разобрать этот вариант подробнее?"}
        if _needs_financing_check_all(decision, state):
            return {"answer_kind": "search_many", "scope": "shortlist", "final_question": FINANCING_CHECK_ALL_QUESTION}
        return {"answer_kind": "search_many", "scope": "shortlist", "final_question": "Какой вариант хотите разобрать подробнее?"}
    if action == "off_topic":
        return {"answer_kind": "off_topic", "scope": "no_cards", "final_question": "Вернёмся к подбору новостройки?"}
    return {"answer_kind": "open_question", "scope": "dialogue", "final_question": "Что именно хотите уточнить?"}


def _selected_object_cta(viewpoint: str) -> str:
    topic = _safe_viewpoint(viewpoint)
    if topic == "rental":
        return "Проверить доступные квартиры для сдачи именно в этом ЖК?"
    if topic == "family":
        return "Проверить подходящие семейные планировки в этом ЖК?"
    return SELECTED_OBJECT_PRESENTATION_QUESTION


def _deterministic_answer_parts(expected: JsonDict, *, cards: tuple[OptionCard, ...], decision: JsonDict, state: V0State) -> JsonDict:
    kind = str(expected.get("answer_kind") or "")
    if kind == "operator":
        if decision.get("_empty_search_operator_phone") is True:
            return {
                "intro": "По этому запросу сейчас не вернулись подтверждённые варианты.",
                "recommendation": "Не буду придумывать ЖК или квартиры без данных.",
                "missing_note": "Оператор сможет проверить текущую доступность по актуальной базе.",
            }
        if decision.get("_pending_resolution") == "accept_contact_phone":
            return {"intro": "", "recommendation": "", "missing_note": ""}
        if decision.get("_pending_resolution") == "accepted_check_selected_availability":
            return _accepted_availability_parts(decision, state)
        if decision.get("_pending_resolution") == "accepted_check_current_options_financing":
            return _accepted_financing_check_all_parts(decision, state)
        return {
            "intro": "Не буду отвечать наугад.",
            "recommendation": "",
            "missing_note": "Нужно проверить это у оператора по актуальным данным.",
        }
    if decision.get("_pending_resolution") == "declined_check_selected_availability":
        return {"intro": "Хорошо, не будем сейчас проверять наличие по выбранному ЖК.", "recommendation": "", "missing_note": "Могу подобрать другой вариант или уточнить условия по этому ЖК."}
    if decision.get("_pending_resolution") == "declined_check_current_options_financing":
        return {"intro": "Хорошо, не будем сейчас отправлять проверку условий оплаты по всем текущим вариантам.", "recommendation": "", "missing_note": "Могу вместо этого сузить подборку по бюджету, сроку сдачи или конкретному ЖК."}
    if kind == "selected_object":
        name = cards[0].name if cards else str(decision.get("selected_option_name") or "этот ЖК").strip() or "этот ЖК"
        if cards:
            return {"intro": _selected_intro(cards[0], decision), "recommendation": "", "missing_note": _selected_missing_note(cards[0], decision)}
        return {"intro": f"По {_quote_complex_name(name)} сейчас не вижу подтверждённой карточки.", "recommendation": "", "missing_note": "Не буду придумывать детали по выбранному ЖК: лучше проверить его у оператора или сделать точный поиск ещё раз."}
    if kind == "search_many":
        if str(decision.get("comparison_metric") or "") == "price_min":
            if cards and isinstance(cards[0].price_min, int):
                return {"intro": "Из текущих вариантов самый низкий подтверждённый старт по цене вот здесь.", "recommendation": "Сравниваю только по подтверждённой стартовой цене, без догадок по скидкам или одобрению.", "missing_note": ""}
            return {"intro": "По текущим вариантам не вижу подтверждённой стартовой цены для честного сравнения.", "recommendation": "", "missing_note": "Лучше проверить цены у оператора, чем выбирать самый дешёвый вариант наугад."}
        count = min(len(cards), 3)
        all_near = bool(cards) and all(card.is_near for card in cards[:count])
        if all_near:
            intro = _near_only_intro(count)
        elif _needs_financing_check_all(decision, state):
            intro = _financing_boundary_intro(count)
        elif _is_sparse_family_request(decision, cards[:count]):
            intro = _family_sparse_intro(count)
        elif count == 1:
            intro = "По вашему запросу подходит один вариант."
        elif count == 2:
            intro = "По вашему запросу подходят два варианта."
        elif count == 3:
            intro = "По вашему запросу подходят три варианта."
        else:
            intro = "Подходящих вариантов в текущей подборке пока нет."
        requested_boundary = _requested_fact_missing_boundary(decision, cards[:3])
        if requested_boundary:
            intro = f"{requested_boundary} {intro}"
        budget_params = {**state.params, **_safe_mapping(decision.get("params", {}))}
        missing = _financing_missing_note(decision, cards[:3]) if _needs_financing_check_all(decision, state) else ""
        return {"intro": intro, "recommendation": _budget_summary(cards[:3], budget_params), "missing_note": missing}
    if kind == "off_topic":
        return {"intro": "Я Валерия, поэтому по-человечески могу чуть улыбнуться, но лучше помогу с подбором новостройки.", "recommendation": "", "missing_note": ""}
    return {"intro": "Уточню по тому, что уже вижу.", "recommendation": "", "missing_note": ""}


def _render_card_option(index: int, card: OptionCard, *, state: V0State, params: Mapping[str, Any], selected: bool, viewpoint: str, cards: tuple[OptionCard, ...], used_benefits: set[str], comparison_context: Mapping[str, Any] | None = None) -> JsonDict:
    block = render_grounded_card_block(
        index,
        card,
        viewpoint=viewpoint,
        used_benefits=used_benefits,
        cards=cards or tuple(state.visible_options[:3]) or (card,),
        scenario_needs=("family", "life") if viewpoint == "family" else (),
        comparison_context=comparison_context,
    )
    block = _with_exact_integer_price(block, card)
    block = _with_budget_fit(block, card, params)
    lines = [line.rstrip() for line in block.splitlines() if line.strip()]
    if selected and _useful_fact_count(card) <= 1:
        lines.append("по этому варианту пока мало подтверждённых деталей")
    if len(lines) == 1 and _useful_fact_count(card) == 0:
        lines.append("по этому варианту пока мало полезных деталей")
    if selected:
        lines.extend(render_selected_lot_lines(card))
    return {"name": card.name, "lines": tuple(lines)}


def _near_only_intro(count: int) -> str:
    if count == 1:
        return "Точного совпадения по жёстким условиям сейчас не подтвердилось; покажу ближайший вариант как альтернативу."
    return "Точных совпадений по жёстким условиям сейчас не подтвердилось; покажу ближайшие альтернативы."


def _financing_boundary_intro(count: int) -> str:
    if count == 1:
        return "Нашла один вариант по базовым параметрам, но условия покупки без первоначального взноса по нему не подтверждаю."
    if count == 2:
        return "Нашла два варианта по базовым параметрам, но покупку без первоначального взноса по ним не подтверждаю."
    return "Нашла варианты по базовым параметрам, но покупку без первоначального взноса по ним не подтверждаю."


def _family_sparse_intro(count: int) -> str:
    if count == 1:
        return "Нашла один вариант по подтверждённым базовым параметрам; семейные детали по нему пока не подтверждены."
    return "Нашла варианты по подтверждённым базовым параметрам; семейные детали по ним пока не подтверждены."


def _financing_missing_note(decision: Mapping[str, Any], cards: tuple[OptionCard, ...]) -> str:
    if _has_finance_evidence(cards):
        return ""
    params = decision.get("params") if isinstance(decision.get("params"), Mapping) else {}
    if params.get("down_payment") == 0:
        return "В карточках нет подтверждения, что эти ЖК доступны без первоначального взноса. Могу передать все текущие варианты оператору на проверку условий оплаты."
    return "Условия оплаты по этим вариантам нужно проверять отдельно."


def _needs_financing_check_all(decision: Mapping[str, Any], state: V0State | None) -> bool:
    if _safe_viewpoint(decision.get("viewpoint") or decision.get("active_topic") or (state.active_topic if state else "")) != "financing":
        return False
    params = decision.get("params") if isinstance(decision.get("params"), Mapping) else {}
    return params.get("down_payment") == 0 or "down_payment" in params


def _has_finance_evidence(cards: tuple[OptionCard, ...]) -> bool:
    return any(card.mortgage_terms or card.discount for card in cards)


def _requested_fact_missing_boundary(decision: Mapping[str, Any], cards: tuple[OptionCard, ...]) -> str:
    if not cards:
        return ""
    requested = {_normalize_requested_fact(item) for item in _as_tuple_str(decision.get("requested_facts"))}
    missing: list[str] = []
    if "school" in requested and not any(_card_has_school_evidence(card) for card in cards):
        missing.append("школам")
    if "metro" in requested and not any(_card_has_metro_evidence(card) for card in cards):
        missing.append("метро")
    if not missing:
        return ""
    if len(missing) == 1:
        subject = f"по {missing[0]}"
    else:
        subject = "по школам и метро"
    return f"В текущих карточках нет подтверждённой информации {subject}."


def _normalize_requested_fact(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("ё", "е")
    if text in {"school", "schools", "школа", "школы", "family_infrastructure", "family", "kindergarten", "детский_сад"}:
        return "school"
    if text in {"metro", "property_metro", "transport", "transport_access", "метро", "транспорт"}:
        return "metro"
    return text


def _card_has_school_evidence(card: OptionCard) -> bool:
    return any(re.search(r"школ|school", str(item or ""), re.IGNORECASE) for item in card.infrastructure)


def _card_has_metro_evidence(card: OptionCard) -> bool:
    return bool(card.metro or card.transport_access)


def _is_sparse_family_request(decision: Mapping[str, Any], cards: tuple[OptionCard, ...]) -> bool:
    if _safe_viewpoint(decision.get("viewpoint") or decision.get("active_topic") or "") != "family":
        return False
    requested = {str(item).strip().casefold() for item in _as_tuple_str(decision.get("requested_facts"))}
    family_requested = bool(requested & {"schools", "school", "kindergarten", "park", "parks", "family_infrastructure"})
    params = decision.get("params") if isinstance(decision.get("params"), Mapping) else {}
    family_requested = family_requested or "rooms" in params
    if not family_requested or not cards:
        return False
    return not any(card.infrastructure or card.rooms or card.room_formats for card in cards)


def _selected_intro(card: OptionCard, decision: JsonDict) -> str:
    return selected_object_grounded_acknowledgement(card, viewpoint=_safe_viewpoint(decision.get("viewpoint") or "life"))


def _selected_missing_note(card: OptionCard, decision: Mapping[str, Any]) -> str:
    requested = {str(item).strip().casefold() for item in _as_tuple_str(decision.get("requested_facts"))}
    notes: list[str] = []
    if requested & {"readiness", "ready", "delivered"} and not card.ready:
        notes.append("Готовность дома по этому ЖК пока не подтверждена.")
    if requested & {"apartment_price", "price", "price_min", "min_price"} and not (card.price or isinstance(card.price_min, int)):
        notes.append("Цена по этому ЖК пока не подтверждена.")
    if requested & {"finishing", "renovation"} and not card.finishing:
        notes.append("Отделка по этому ЖК пока не подтверждена.")
    if not render_selected_lot_lines(card):
        notes.append("Подтверждённых квартир из объявлений по этому ЖК сейчас не вижу; поэтому не утверждаю, что конкретные квартиры доступны.")
    return " ".join(notes)


def _accepted_availability_parts(decision: JsonDict, state: V0State) -> JsonDict:
    subject = str(decision.get("confirmed_subject") or state.pending_subject or state.selected_option_name or "этом ЖК").strip()
    quoted = _quote_complex_name(subject)
    topic = _safe_viewpoint(decision.get("viewpoint") or state.pending_topic or state.active_topic or "life")
    if topic == "rental":
        intro = f"Хорошо, поняла: хотите проверить актуальные квартиры в {quoted} для последующей сдачи."
    elif topic == "family":
        intro = f"Хорошо, поняла: хотите проверить подходящие семейные планировки в {quoted}."
    else:
        intro = f"Хорошо, поняла: хотите проверить актуальные квартиры в {quoted}."
    return {
        "intro": intro,
        "recommendation": "",
        "missing_note": "Оператор сможет проверить наличие, площади, отделку и точную цену по актуальной базе.",
    }


def _accepted_financing_check_all_parts(decision: JsonDict, state: V0State) -> JsonDict:
    count = len(state.visible_options[:3])
    if count == 1:
        intro = "Хорошо, поняла: хотите проверить условия оплаты без первоначального взноса по текущему варианту."
    else:
        intro = f"Хорошо, поняла: хотите проверить условия оплаты без первоначального взноса по всем текущим вариантам — их сейчас {count}."
    return {
        "intro": intro,
        "recommendation": "",
        "missing_note": "Оператор сможет проверить ипотеку, рассрочку, первоначальный взнос и актуальные программы по каждому ЖК.",
    }


def _quote_complex_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return "этом ЖК"
    if text.casefold().startswith("жк "):
        return "ЖК «" + text[3:].strip(" «»\t") + "»"
    return "«" + text.strip(" «»\t") + "»"


def _pending_subject_for_validation(state: V0State) -> str:
    if state.pending_action == "check_current_options_financing":
        return "all_current_options"
    subject = str(state.pending_subject or state.selected_option_name or "").strip()
    if subject:
        return subject
    return state.visible_options[0].name if state.visible_options else ""


def _normalize_name(value: Any) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"[«»\"'`.,:;!?()\[\]{}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _with_exact_integer_price(text: str, card: OptionCard) -> str:
    if not isinstance(card.price_min, int):
        return text
    exact = f"цены от {_format_rubles(card.price_min)} ₽"
    return re.sub(r"цены от \d+(?:[,.]\d+)?\s*млн рублей", exact, text, count=1, flags=re.IGNORECASE)


def _with_budget_fit(text: str, card: OptionCard, params: Mapping[str, Any]) -> str:
    fit = _budget_fit_text(card, params)
    if not fit:
        return text
    first, *rest = text.splitlines()
    first = first.rstrip(".") + f" ({fit})."
    return "\n".join((first, *rest))


def _useful_fact_count(card: OptionCard) -> int:
    count = 0
    for value in (card.location or card.district, card.price or card.price_min, card.rooms, card.area, card.finishing, card.ready, card.metro, card.developer):
        if value not in (None, "", (), [], {}):
            count += 1
    if card.infrastructure:
        count += 1
    return count


def _budget_summary(cards: tuple[OptionCard, ...], params: Mapping[str, Any]) -> str:
    budget = _extract_budget_number(params)
    if budget is None:
        return ""
    priced = [card for card in cards if isinstance(card.price_min, int)]
    if not priced:
        return f"Бюджет держу в фокусе: до {_format_rubles(budget)} ₽, но у этих вариантов стартовая цена пока не подтверждена."
    fits = [card for card in priced if card.price_min <= budget]
    over = [card for card in priced if card.price_min > budget]
    if fits and over:
        return f"По бюджету до {_format_rubles(budget)} ₽: {len(fits)} из {len(priced)} вариантов в пределах, остальные честно отмечены как выше бюджета."
    if fits:
        return f"По бюджету до {_format_rubles(budget)} ₽ эти варианты проходят по подтверждённой стартовой цене."
    return f"Бюджет до {_format_rubles(budget)} ₽ сохранила, но подтверждённые стартовые цены в этой подборке выше него."


def _price_text(card: OptionCard) -> str:
    if card.price:
        return str(card.price)
    if isinstance(card.price_min, int):
        return f"от {_format_rubles(card.price_min)} ₽"
    return ""


def _format_rubles(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _budget_fit_text(card: OptionCard, params: Mapping[str, Any]) -> str:
    if not isinstance(card.price_min, int):
        return ""
    budget = _extract_budget_number(params)
    if budget is None:
        return ""
    return "вписывается в указанный бюджет" if card.price_min <= budget else "выше указанного бюджета"


def _extract_budget_number(params: Mapping[str, Any]) -> int | None:
    for key in ("budget", "normalized_budget", "price_max", "max_price"):
        value = params.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value if value > 1_000_000 else value * 1_000_000)
        text = str(value or "").replace(",", ".")
        match = re.search(r"(\d+(?:\.\d+)?)\s*(млн|миллион|million)?", text, re.IGNORECASE)
        if match:
            number = float(match.group(1))
            return int(number * 1_000_000) if number < 100_000 else int(number)
    return None


def _facts_from_cards(cards: tuple[OptionCard, ...]) -> tuple[str, ...]:
    facts: list[str] = []
    for card in cards[:3]:
        data = _card_to_dict(card)
        facts.extend(key for key, value in data.items() if key not in {"name", "is_near", "why_close"} and value not in (None, "", (), [], {}, False))
    return tuple(dict.fromkeys(facts))


def _card_to_dict(card: OptionCard) -> JsonDict:
    data = asdict(card) if is_dataclass(card) else dict(card)
    return {key: value for key, value in data.items() if value not in (None, "", (), [], {})}
