#!/usr/bin/env python3
"""Minimal HTTP/Jivo API wrapper for nmbot.

MVP goals:
- expose a small internal `POST /api/chat` endpoint;
- expose Jivo Bot API webhook `POST /jivo/{provider_token}`;
- keep Telegram runtime untouched;
- run without secrets in `--smoke` mode.

The first version intentionally reuses the existing Overmind search/chat client,
but does not try to fully emulate Telegram's large handler. It is a foundation
for integration tests and Jivo wiring.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import difflib
import hashlib
import hmac
import json
import os
import re
import sys
import time
import traceback
import unicodedata
import uuid
from dataclasses import dataclass
from contextlib import asynccontextmanager
from collections import OrderedDict
import importlib.util
from pathlib import Path
from typing import Any

from aiohttp import web

# Direct production execution starts with ``scripts/`` first on ``sys.path``.
# Keep that precedence so stale/untracked root-level script copies can never
# shadow the canonical runtime adapters.  The repository root is needed only
# for ``nmbot_v2`` and root-level project modules.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from nmbot_gateway_client import (
    OvermindClient,
    SAFE_UPSTREAM_ERROR_TEXT,
    SEARCH_MODEL,
    CHAT_MODEL,
    _log_error_event,
)
from nmbot_crm_outbox import CallbackOutboxResult, LocalCallbackOutbox, build_callback_lead_context
from dialogue_journal import append_event as append_journal_event
from nmbot_v1.execution_path import append_jivo_api_prepare as append_v1_jivo_api_prepare, sanitize_execution_path as sanitize_v1_execution_path
from nmbot_v1.provider_adapters import V1GatewayOneModelResponsePort, V1GatewayPlannerPort, V1GatewaySearchPort
from nmbot_v4.provider_adapter import V4GatewayOnePromptPort
from nmbot_v6.simple_gateway import DirectTransport, SimpleGateway
from nmbot_v6.simple_state import SimpleState
from nmbot_v2.execution_path import append_jivo_api_prepare, sanitize_execution_path
from nmbot_release_identity import current_release_id
from nmbot_egress_policy import SAFE_CLIENT_FALLBACK_TEXT, guard_jivo_event, is_client_production
from nmbot_planner_context import (
    append_safe_planner_trace as _neutral_append_safe_planner_trace,
    dialog_planner_state_payload as _neutral_dialog_planner_state_payload,
    safe_planner_state as _neutral_safe_planner_state,
    safe_turn_context as _neutral_safe_turn_context,
)
from nmbot_runtime_adapter import _canonical_v0_envelope, _canonical_v1_envelope, _canonical_v4_envelope, _merge_runtime_namespace_envelope, run_runtime_turn
from nmbot_v0.field_contract import V0_PRESENTATION_TRACE_FIELDS
from nmbot_v1.state import V1ConversationState
from nmbot_v2.contracts import OptionCard
from nmbot_v2.state import ConversationState


_LEGACY_CHAT_MODULE: Any | None = None
_SAFE_TRACE_REF_RE = re.compile(r"^trace_[0-9a-f]{12}$")


def _safe_bridge_trace_ref(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = uuid.UUID(text)
    except (TypeError, ValueError, AttributeError):
        return None
    if str(parsed) != text.lower():
        return None
    return "trace_" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def _validated_trace_ref(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if _SAFE_TRACE_REF_RE.fullmatch(text) else None


import followup_intent_classifier  # noqa: E402
from search_profiles import select_search_profile  # noqa: E402

DEFAULT_STATE_FILE = REPO_ROOT / "data" / "nmbot_api_state.json"
DEFAULT_RUNTIME_VERSION_FILE = REPO_ROOT / "data" / "nmbot_runtime_version.json"
DEFAULT_CALLBACK_OUTBOX_DIR = REPO_ROOT / "data" / "crm_callback_outbox"
JIVO_DEDUP_TTL_SEC = 10 * 60
JIVO_DEDUP_MAX_ENTRIES = 1024
TURN_ACTIONS = {"search", "clarify", "operator_contact", "recover_dialogue", "answer_current_options", "capture_contact", "off_topic"}
TURN_TARGETS = {"new_search", "current_options", "none", "operator"}
TURN_SEARCH_POLICIES = {"required", "forbidden"}
CANONICAL_ACTIONS = {"search", "answer_current_options", "recover_dialogue", "operator_contact", "clarify", "off_topic"}
CANONICAL_INTENTS = {"investment", "rental", "family", "life", "mortgage", "unknown"}
CANONICAL_INTENT_POLICIES = {"keep", "set", "change"}
CANONICAL_SCOPES = {"one", "all", "unknown"}
CANONICAL_TARGETS = {"new_search", "current_options", "none", "operator"}
CANONICAL_SEARCH_POLICIES = {"required", "forbidden", "allowed"}
CANONICAL_OPERATOR_CONSENTS = {"none", "ask", "granted", "refused"}
ROUTER_PROFILES_ENABLED = os.getenv("NMBOT_ROUTER_PROFILES", "0").strip().lower() in {"1", "true", "yes", "on"}
CANONICAL_SEARCH_PROFILES = {"generic", "family", "investment", "mortgage", "none"}
CANONICAL_CONSTRAINT_CATEGORIES = {"hard", "preferences", "unknown"}
CANONICAL_PLAN_KEYS = {
    "action", "intent", "intent_policy", "target", "search_policy",
    "constraints_patch", "facets", "search_profile", "missing_fields", "clarification_fields", "scope",
}
CANONICAL_PARAM_ALLOWLIST = {
    "location", "locations", "district", "districts", "metro", "near_metro",
    "rooms", "room_type", "max_price", "max_budget_m", "min_price",
    "area_min_m2", "area_max_m2", "finishing", "renovation", "ready",
    "stage", "ready_quarter", "delivery_visible", "project_ready_secondary",
    "property_metro", "schools", "kindergartens", "parks", "shops",
    "family_infrastructure", "purpose", "scenario", "topic", "mortgage",
    "discount", "installment", "payment_by_installments",
}
CANONICAL_PARAM_CATEGORY_ORDER = ("unknown", "preferences", "hard")
SEARCH_HARD_CONSTRAINT_ALLOWLIST = {
    "location", "locations", "district", "districts", "metro", "near_metro",
    "rooms", "room_type", "max_price", "max_budget_m", "min_price",
    "area_min_m2", "area_max_m2", "finishing", "renovation", "ready", "stage",
    "purpose", "mortgage", "mortgage_type",
}


def _load_legacy_chat_module() -> Any:
    """Load canonical scripts/chat_tester_bot.py only for explicit legacy V1 work."""

    global _LEGACY_CHAT_MODULE
    if _LEGACY_CHAT_MODULE is not None:
        return _LEGACY_CHAT_MODULE
    legacy_path = Path(__file__).resolve().parent / "chat_tester_bot.py"
    module_name = "_nmbot_legacy_chat_tester_bot"
    spec = importlib.util.spec_from_file_location(module_name, legacy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load legacy chat_tester_bot from {legacy_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _LEGACY_CHAT_MODULE = module
    return module


def _legacy_func(name: str) -> Any:
    module = _load_legacy_chat_module()
    return getattr(module, name)


def _default_state() -> dict[str, Any]:
    return {
        "search_model": SEARCH_MODEL,
        "chat_model": CHAT_MODEL,
        "mcp": True,
        "params": {},
        "last_result": {},
        "last_options": [],
        "enriched_options": {},
        "visible_options": [],
        "selected_option": None,
        "turns_after_results": 0,
        "last_search_response": {},
        "asked_questions": [],
        "last_buttons": [],
        "dialog_window": [],
        "last_bot_question": "",
        "last_offer_type": "",
        "last_answer_kind": "",
        "active_task": {},
        "active_scenario": {},
        "selected_option_card_shown_count": 0,
    }


def _canonical_reset_state() -> dict[str, Any]:
    """Возвращает пустое состояние V2 для нового диалога Jivo."""
    return {"nmbot_v2": ConversationState().to_dict()}


def _canonical_v6_envelope() -> dict[str, Any]:
    return {"nmbot_v6": SimpleState().plain()}


def _canonical_reset_state_for_version(version: str) -> dict[str, Any]:
    normalized = _normalize_runtime_version(version)
    if normalized == "V0":
        return _canonical_v0_envelope()
    if normalized == "V1":
        return _canonical_v1_envelope(V1ConversationState.clean())
    if normalized == "V4":
        return _canonical_v4_envelope()
    if normalized == "V6":
        return _canonical_v6_envelope()
    return _canonical_reset_state()


def _reset_active_namespace_envelope(existing: dict[str, Any] | None, version: str) -> dict[str, Any]:
    return _merge_runtime_namespace_envelope(existing, _canonical_reset_state_for_version(version))


def _strip_markdown(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("```"):
        first_nl = value.find("\n")
        if first_nl > 0:
            value = value[first_nl + 1 :]
        if value.endswith("```"):
            value = value[:-3].rstrip()
    return value


def _format_numbered_list_spacing(text: str) -> str:
    if not text:
        return text
    lines = text.splitlines()
    out: list[str] = []
    seen_list_item = False
    for line in lines:
        stripped = line.strip()
        is_item = bool(re.match(r"^\s*(?:\d+\.|[-•*])\s+", line))
        is_question = stripped.endswith("?")
        if is_item and out and out[-1] != "":
            out.append("")
        if is_question and seen_list_item and out and out[-1] != "":
            out.append("")
        out.append(line)
        seen_list_item = seen_list_item or is_item
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _prepare_response_text(text: str) -> str:
    return _legacy_func("_prepare_response_text")(text)


def _append_dialog_turn(state: dict[str, Any], role: str, text: str, limit: int = 6) -> None:
    return _legacy_func("_append_dialog_turn")(state, role, text, limit)


def _extract_last_question(text: str) -> str:
    for line in reversed([line.strip() for line in str(text or "").splitlines() if line.strip()]):
        if "?" in line:
            return line
    return ""


def _remember_bot_response(state: dict[str, Any], text: str, *, offer_type: str = "", answer_kind: str = "") -> None:
    return _legacy_func("_remember_bot_response")(state, text, offer_type=offer_type, answer_kind=answer_kind)


_CLASS_AS_VALUE_RE = re.compile(r"^(comfort|business|premium|elite|комфорт|бизнес|премиум|элитн\w*)$", re.I)


def _looks_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip().lower()
        return text in {"", "н/д", "нет", "none", "null", "unknown", "не указано", "неизвестно"}
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def _safe_option_payload(option: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(option, dict):
        return {}
    allowed = {
        "idx", "name", "location", "price", "price_min", "price_range", "area", "area_range",
        "finishing", "ready", "developer", "metro", "property_metro", "why_close",
        "schools", "kindergartens", "parks", "shops", "infrastructure", "family_infrastructure",
        "house", "houses_info", "stage", "ready_quarter",
        "mortgage", "mortgage_calc", "discount", "payment_by_installments",
        "ads", "counter_novos", "apartment_types", "why_family", "why_investment", "why_rental",
        "rooms", "transport", "class", "property_class", "clinics", "yards", "egrn_top_novos",
        "stat_price", "price_history", "services",
    }
    safe: dict[str, Any] = {}
    for key, value in option.items():
        if key not in allowed or _looks_missing(value):
            continue
        if key == "developer" and _CLASS_AS_VALUE_RE.search(str(value).strip()):
            continue
        safe[key] = _safe_nested_value(value)
    return {k: v for k, v in safe.items() if v not in (None, "", [], {})}


def _last_dialog_user_text(state: dict[str, Any]) -> str:
    for turn in reversed(state.get("dialog_window") or []):
        if isinstance(turn, dict) and turn.get("role") == "user":
            return str(turn.get("text") or "")
    return ""


def _active_task(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    task = state.get("active_task")
    return dict(task) if isinstance(task, dict) else {}


def _active_scenario(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    scenario = state.get("active_scenario")
    return dict(scenario) if isinstance(scenario, dict) else {}


def _is_short_positive_answer(text: str) -> bool:
    compact = re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е")).strip()
    return bool(re.fullmatch(r"(да|ага|угу|ок|окей|хорошо|давай|хочу|можно|готов|готова|конечно)", compact))


def _dialog_planner_expected_action_class(
    *,
    bot_question: str,
    client_answer: str,
    offer_type: str,
    answer_kind: str,
    selected_option: dict[str, Any],
) -> str:
    if not _is_short_positive_answer(client_answer):
        return ""
    if not isinstance(selected_option, dict) or not str(selected_option.get("name") or "").strip():
        return ""
    question = re.sub(r"\s+", " ", str(bot_question or "").lower().replace("ё", "е")).strip()
    selected_offer = offer_type in {"selected_option_details", "operator_for_selected"} or answer_kind in {
        "selected_option_details",
        "default_selected_option",
        "selected_option_financing_manager_offer",
    }
    live_check_question = bool(re.search(
        r"доступн|услови|приобрести|покупк|брон|корпус|этаж|квартир|актуаль|ипот|первоначальн|взнос|рассроч|скидк|акци|налич|показ",
        question,
    ))
    return "operator_live_check" if selected_offer and live_check_question else ""


def _dialog_planner_last_turn_payload(state: dict[str, Any]) -> dict[str, Any]:
    selected = state.get("selected_option") if isinstance(state.get("selected_option"), dict) else {}
    bot_question = str(state.get("last_bot_question") or "")
    client_answer = _last_dialog_user_text(state)
    offer_type = str(state.get("last_offer_type") or "")
    answer_kind = str(state.get("last_answer_kind") or "")
    selected_name = str(selected.get("name") or "") if isinstance(selected, dict) else ""
    return {
        "bot_question": bot_question,
        "client_answer": client_answer,
        "offer_type": offer_type,
        "answer_kind": answer_kind,
        "selected_option": selected_name,
        "expected_action_class": _dialog_planner_expected_action_class(
            bot_question=bot_question,
            client_answer=client_answer,
            offer_type=offer_type,
            answer_kind=answer_kind,
            selected_option=selected,
        ),
    }


_SCENARIO_CONTEXT_HINTS: dict[str, dict[str, Any]] = {
    "family": {"client_need_label": "квартира для семьи", "scenario_reasons": ["schools", "kindergartens", "parks", "family_infrastructure"], "answer_angles": ["ежедневное удобство семьи", "детская инфраструктура", "среда рядом с домом"]},
    "investment": {"client_need_label": "покупка как инвестиция", "scenario_reasons": ["entry_price", "deadline", "location", "liquidity_context"], "answer_angles": ["порог входа", "срок готовности", "понятные факторы спроса"]},
    "rental": {"client_need_label": "квартира под аренду", "scenario_reasons": ["metro", "finishing", "readiness", "area"], "answer_angles": ["быстрый запуск аренды", "транспорт", "ремонт и готовность"]},
    "self_use": {"client_need_label": "квартира для себя", "scenario_reasons": ["location", "transport", "readiness", "finishing"], "answer_angles": ["удобство жизни", "срок и формат покупки"]},
}


def _neutral_primary_scenario(user_text: str, state: dict[str, Any]) -> str:
    params = state.get("params") if isinstance(state.get("params"), dict) else {}
    active = _active_scenario(state)
    for candidate in (active.get("key"), params.get("purpose")):
        value = str(candidate or "").strip().lower()
        if value in _SCENARIO_CONTEXT_HINTS:
            return value
    text = str(user_text or "").lower().replace("ё", "е")
    if "аренд" in text:
        return "rental"
    if "инвест" in text:
        return "investment"
    if "сем" in text or "реб" in text:
        return "family"
    return "self_use"


def _has_mortgage_signal(text: str, params: dict[str, Any]) -> bool:
    mortgage_type = str(params.get("mortgage_type") or "").strip().lower()
    facets = params.get("facets") if isinstance(params.get("facets"), list) else []
    if mortgage_type or any(str(item).strip().lower() == "mortgage" for item in facets):
        return True
    return any(token in text for token in ("ипот", "льготн", "господдерж", "семейную ипот", "семейная ипот", "маткапитал", "первонач", "первый взнос", "ставк", "рассроч", "скидк", "платеж", "платёж"))


def _extract_conversation_followup_signals(user_text: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", (user_text or "").lower().replace("ё", "е")).strip()
    params = state.get("params") if isinstance(state, dict) and isinstance(state.get("params"), dict) else {}
    options = (state.get("visible_options") or state.get("last_options") or []) if isinstance(state, dict) else []
    topic = "general"
    subtopic = ""
    mortgage_type = ""
    if "аренд" in text:
        topic = "rental"
    if _has_mortgage_signal(text, params):
        topic = "financing"
        if "семейн" in text and "ипот" in text:
            subtopic = mortgage_type = "family_mortgage"
        elif "первонач" in text or "первый взнос" in text:
            subtopic = "down_payment"
    target_scope = "all_current_options" if re.search(r"\b(все|оба|обе|они|эти)\b", text) and options else ""
    return {
        "topic": topic,
        "subtopic_hint": subtopic,
        "target_scope": target_scope,
        "mortgage_type": mortgage_type or str(params.get("mortgage_type") or ""),
        "has_enough_data": bool(options),
        "needs_clarification": False,
        "needs_operator": bool(re.search(r"оператор|менеджер|свяж|соедин|позов", text)),
        "next_action": "answer",
        "final_question": "",
        "evidence": ["mortgage_signal"] if topic == "financing" else [],
    }


def _reference_resolution_payload(user_text: str, state: dict[str, Any]) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", (user_text or "").lower().replace("ё", "е")).strip()
    options = state.get("visible_options") or state.get("last_options") or []
    names = [str(o.get("name") or "") for o in options[:5] if isinstance(o, dict) and o.get("name")]
    pronoun_match = re.search(r"\b(они|эти|них|ним|ними|все|оба|обе)\b", text)
    if pronoun_match and names:
        return {"phrase": pronoun_match.group(1), "resolved_to": "current_options", "option_names": names}
    return {"phrase": "", "resolved_to": "", "option_names": names}


def _scenario_context_payload(user_text: str, state: dict[str, Any]) -> dict[str, Any]:
    params = state.get("params") if isinstance(state.get("params"), dict) else {}
    options = state.get("visible_options") or state.get("last_options") or []
    primary = _neutral_primary_scenario(user_text, state)
    hints = dict(_SCENARIO_CONTEXT_HINTS.get(primary) or _SCENARIO_CONTEXT_HINTS["self_use"])
    followup = _extract_conversation_followup_signals(user_text, state)
    facet_request: dict[str, Any] = {}
    if _has_mortgage_signal(str(user_text or "").lower().replace("ё", "е"), params) or str(followup.get("topic") or "") in {"financing", "payment_terms"}:
        mortgage_evidence_keys = {"mortgage", "mortgage_calc", "payment_by_installments", "discount"}
        option_has_mortgage_evidence = any(isinstance(option, dict) and any(not _looks_missing(option.get(key)) for key in mortgage_evidence_keys) for option in options[:5])
        facet_request = {
            "type": "mortgage",
            "target": "current_options" if options else "general_context",
            "user_phrase": user_text,
            "mortgage_type": followup.get("mortgage_type") or params.get("mortgage_type") or "general",
            "evidence_status": "has_mortgage_facts" if option_has_mortgage_evidence else "no_current_mortgage_facts",
            "rule": "mortgage/payment is a facet; it must not replace primary_scenario or rebuild ЖК list",
            "wording_rule": "if evidence_status=no_current_mortgage_facts, do not say all options definitely fit mortgage; answer carefully that mortgage terms can be checked for these options and exact conditions depend on bank/program/object/client",
        }
    action_request: dict[str, Any] = {}
    if re.search(r"оператор|менеджер|свяж|соедин|позов", (user_text or "").lower().replace("ё", "е")):
        action_request = {"type": "operator_handoff", "target": "current_context", "rule": "operator is an action, not a new search"}
    current_options = [_safe_option_payload(option) for option in options[:5] if isinstance(option, dict)]
    return {
        "primary_scenario": primary,
        "client_need": hints.get("client_need_label") or primary,
        "scenario_reasons": hints.get("scenario_reasons") or [],
        "answer_angles": hints.get("answer_angles") or [],
        "current_options": current_options,
        "reference_resolution": _reference_resolution_payload(user_text, state),
        "facet_request": facet_request,
        "action_request": action_request,
        "handoff_context": {
            "client_need": hints.get("client_need_label") or primary,
            "current_option_names": [o.get("name") for o in current_options if o.get("name")],
            "last_fact_requested": facet_request.get("type") or "",
            "task": "prepare_handoff_or_ask_phone" if action_request else "",
        },
        "contract": [
            "keep primary_scenario stable unless user explicitly asks for a new search",
            "if target=current_options, answer about current_options and do not invent a new ЖК list",
            "if mortgage facet has no_current_mortgage_facts, do not promise mortgage availability; offer to check terms for current options",
            "if action_request.type=operator_handoff, do not create a ЖК search; ask for phone or prepare handoff",
        ],
    }


def _dialog_planner_state_payload(state: dict[str, Any]) -> dict[str, Any]:
    return _neutral_dialog_planner_state_payload(state)


def _extract_phone_from_text(raw: Any) -> str:
    return _legacy_func("_extract_phone_from_text")(raw)


def _is_explicit_operator_request(text: str) -> bool:
    return _legacy_func("_is_explicit_operator_request")(text)


def _remember_shown_options(state: dict[str, Any], options: list[dict[str, Any]] | None) -> None:
    return _legacy_func("_remember_shown_options")(state, options)


def _visible_options_from_chat_or_response(chat_meta: dict[str, Any], response_text: str, options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _legacy_func("_visible_options_from_chat_or_response")(chat_meta, response_text, options)


def _refresh_search_state(state: dict[str, Any], search_meta: dict[str, Any]) -> None:
    return _legacy_func("_refresh_search_state")(state, search_meta)


def render_current_options_answer(*args: Any, **kwargs: Any) -> str:
    return _legacy_func("render_current_options_answer")(*args, **kwargs)


def _render_stage_selected_object(*args: Any, **kwargs: Any) -> str:
    return _legacy_func("_render_stage_selected_object")(*args, **kwargs)


async def _get_or_fetch_enriched_option(*args: Any, **kwargs: Any) -> Any:
    return await _legacy_func("_get_or_fetch_enriched_option")(*args, **kwargs)


def _canonical_constraints_empty(constraints_patch: Any) -> bool:
    if not isinstance(constraints_patch, dict):
        return False
    for category in CANONICAL_CONSTRAINT_CATEGORIES:
        fields = constraints_patch.get(category)
        if fields not in ({}, None):
            return False
    return True


def _add_canonical_semantic_errors(plan: dict[str, Any], errors: list[str]) -> None:
    action = str(plan.get("action") or "")
    target = str(plan.get("target") or "")
    search_policy = str(plan.get("search_policy") or "")
    scope = str(plan.get("scope") or "")
    selected = plan.get("selected_option_name")
    intent = str(plan.get("intent") or "")
    intent_policy = str(plan.get("intent_policy") or "")
    dialog_action = str(plan.get("dialog_action") or "")
    search_profile = str(plan.get("search_profile") or "")
    constraints_patch = plan.get("constraints_patch")
    facets = plan.get("facets") if isinstance(plan.get("facets"), dict) else {}

    if action == "search":
        if target != "new_search" or search_policy != "required":
            errors.append("search_requires_new_search_required")
        if scope != "unknown":
            errors.append("search_scope_must_be_unknown")
        if selected is not None:
            errors.append("search_selected_option_must_be_null")
        if intent_policy not in {"set", "change", "keep"}:
            errors.append("search_intent_policy_invalid")
        if search_profile not in {"generic", "family", "investment", "mortgage"}:
            errors.append("search_profile_required_for_search")
        if dialog_action not in PLANNER_SEARCH_ACTIONS:
            errors.append("search_dialog_action_invalid")

    if action == "answer_current_options":
        if target != "current_options" or search_policy != "forbidden":
            errors.append("current_options_requires_current_forbidden")
        if search_profile != "none":
            errors.append("current_options_search_profile_must_be_none")
        if not _canonical_constraints_empty(constraints_patch):
            errors.append("current_options_constraints_must_be_empty")
        if scope not in {"one", "all"}:
            errors.append("current_options_scope_must_be_one_or_all")
        if scope == "one" and selected is None:
            errors.append("current_options_one_selected_required")
        if scope == "all" and selected is not None:
            errors.append("current_options_all_selected_must_be_null")
        mortgage_overlay = bool(facets.get("family_mortgage") or facets.get("mortgage")) and intent == "mortgage"
        if intent_policy == "change" and intent != "rental" and not mortgage_overlay:
            errors.append("current_options_change_only_rental_switch")
        if facets.get("family_mortgage") or facets.get("mortgage"):
            if dialog_action != "consultation_answer" or intent != "mortgage" or intent_policy not in {"keep", "change"} or scope != "all" or selected is not None:
                errors.append("family_mortgage_current_options_semantic_mismatch")

    if action == "operator_contact":
        if target != "operator" or search_policy != "forbidden":
            errors.append("operator_requires_operator_forbidden")
        if search_profile != "none" or not _canonical_constraints_empty(constraints_patch):
            errors.append("operator_must_not_search")
        if selected is not None or scope != "unknown":
            errors.append("operator_scope_selected_invalid")

    if action in {"clarify", "recover_dialogue", "off_topic"}:
        if target != "none" or search_policy != "forbidden":
            errors.append("non_action_requires_none_forbidden")
        if search_profile != "none" or not _canonical_constraints_empty(constraints_patch):
            errors.append("non_action_must_not_search")
PLANNER_SEARCH_ACTIONS = {"new_search", "update_search", "expand_more_options"}
PLANNER_CURRENT_OPTIONS_ACTIONS = {
    "select_option", "compare_options", "recommend_options", "continue_from_memory",
    "consultation_answer", "conversation_answer",
}
PLANNER_RECOVERY_ACTIONS = {"ask_clarification", "clarify_negation", "reject_offer", "reject_operator", "reject_phone", "reject_selected_option", "reject_similar_options"}
PLANNER_MIN_CONFIDENCE = 0.55
CANONICAL_REPAIRABLE_ERRORS = {
    "invalid_action_target_search_policy",
    "current_options_requires_current_forbidden",
    "current_options_search_profile_must_be_none",
    "current_options_constraints_must_be_empty",
    "current_options_scope_must_be_one_or_all",
    "current_options_all_selected_must_be_null",
    "search_requires_new_search_required",
    "search_scope_must_be_unknown",
    "search_selected_option_must_be_null",
    "search_profile_required_for_search",
    "search_dialog_action_invalid",
    "search_new_intent_requires_set",
    "search_changed_intent_requires_change",
    "search_same_intent_policy_invalid",
    "search_intent_policy_invalid",
    "non_action_requires_none_forbidden",
    "non_action_must_not_search",
}
CANONICAL_ADVISORY_RUNTIME_ERRORS = {
    "search_scope_must_be_unknown",
    "search_intent_policy_invalid",
    "search_profile_required_for_search",
    "search_dialog_action_invalid",
    "search_new_intent_requires_set",
    "search_changed_intent_requires_change",
    "search_same_intent_policy_invalid",
    "current_options_search_profile_must_be_none",
    "current_options_constraints_must_be_empty",
    "current_options_all_selected_must_be_null",
    "current_options_change_only_rental_switch",
    "family_mortgage_current_options_semantic_mismatch",
    "operator_must_not_search",
    "operator_scope_selected_invalid",
    "unsupported_constraint",
}
SENSITIVE_KEY_RE = re.compile(r"phone|телефон|contact|client_id|chat_id|site_id|sender|token|secret|raw|payload|dialog_window", re.I)
PHONE_LIKE_RE = re.compile(r"(?:\+?\d[\s()\-.]*){10,15}")
EMAIL_LIKE_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
CONTACT_FLOW_STATES = {"awaiting_contact_name", "awaiting_contact_phone"}
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\- ]{1,60}$")
JIVO_START_GREETING = (
    "Здравствуйте! Меня зовут Ирина, я помогаю подобрать квартиру в Москве и области — "
    "для жизни, инвестиций или сдачи в аренду. Напишите, какой район или метро рассматриваете, "
    "сколько комнат нужно и какой бюджет планируете — я сразу начну подбор."
)
V0_START_GREETING = (
    "Здравствуйте! Меня зовут Валерия, я помогаю подобрать квартиру в Москве и области — "
    "для жизни, инвестиций или сдачи в аренду. Напишите, какой район или метро рассматриваете, "
    "сколько комнат нужно и какой бюджет планируете — я сразу начну подбор."
)
V1_START_GREETING = (
    "Здравствуйте! Меня зовут Татьяна, я помогу безопасно начать подбор квартиры в Москве и области. "
    "Напишите район или метро, сколько комнат нужно и какой бюджет планируете — я соберу первые варианты."
)
RUNTIME_IDENTITIES = {
    "V0": {"name": "Валерия", "start_greeting": V0_START_GREETING, "state_namespace": "nmbot_v0"},
    "V1": {"name": "Татьяна", "start_greeting": V1_START_GREETING, "state_namespace": "nmbot_v1"},
    "V2": {"name": "Ирина", "start_greeting": JIVO_START_GREETING, "state_namespace": "nmbot_v2"},
    "V3": {
        "name": "Светлана",
        "start_greeting": (
            "Здравствуйте! Меня зовут Светлана, я помогаю подобрать квартиру в Москве и области — "
            "для жизни, инвестиций или сдачи в аренду. Напишите, какой район или метро рассматриваете, "
            "сколько комнат нужно и какой бюджет планируете — я сразу начну подбор."
        ),
        "state_namespace": "nmbot_v2",
    },
    "V5": {
        "name": "Светлана",
        "start_greeting": (
            "Здравствуйте! Меня зовут Светлана, я помогаю подобрать квартиру в Москве и области — "
            "для жизни, инвестиций или сдачи в аренду. Напишите, какой район или метро рассматриваете, "
            "сколько комнат нужно и какой бюджет планируете — я сразу начну подбор."
        ),
        "state_namespace": "nmbot_v2",
    },
    "V6": {
        "name": "TBD",
        "start_greeting": (
            "Здравствуйте! Я помогу подобрать квартиру в Москве и области — "
            "для жизни, инвестиций или сдачи в аренду. Напишите, какой район или метро рассматриваете, "
            "сколько комнат нужно и какой бюджет планируете — я сразу начну подбор."
        ),
        "state_namespace": "nmbot_v6",
    },
    "V4": {
        "name": "Марина",
        "start_greeting": (
            "Здравствуйте! Меня зовут Марина, я подберу квартиры в новостройках Москвы и области. "
            "Напишите район или ЖК, сколько комнат нужно и какой бюджет — я сразу предложу подходящие варианты."
        ),
        "state_namespace": "nmbot_v4",
    },
}
SUPPORTED_RUNTIME_VERSIONS = frozenset(RUNTIME_IDENTITIES)


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_runtime_version(value: Any) -> str:
    version = str(value or "").strip().upper()
    return version if version in SUPPORTED_RUNTIME_VERSIONS else "V6"


def _runtime_version_line(version: str) -> str:
    return f"Сейчас активна версия: {_normalize_runtime_version(version)}."


def _jivo_start_greeting(version: str) -> str:
    normalized = _normalize_runtime_version(version)
    identity = RUNTIME_IDENTITIES.get(normalized, RUNTIME_IDENTITIES["V2"])
    return str(identity["start_greeting"]) + "\n\n" + _runtime_version_line(normalized)


def _client_production_bind(host: str, port: int) -> tuple[str, int]:
    if not is_client_production():
        return host, port
    return "127.0.0.1", 8188


def _client_visible_start_greeting(version: str) -> str:
    if not is_client_production():
        return _jivo_start_greeting(version)
    normalized = _normalize_runtime_version(version)
    identity = RUNTIME_IDENTITIES.get(normalized, RUNTIME_IDENTITIES["V2"])
    return str(identity["start_greeting"])


def _now_ts() -> int:
    return int(time.time())


def _json_response(data: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda obj: json.dumps(obj, ensure_ascii=False))


def _safe_nested_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if isinstance(value, str):
        text = value.strip()[:500]
        return "" if PHONE_LIKE_RE.search(text) else text
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return None if len(str(abs(value))) >= 10 else value
    if isinstance(value, float):
        return value
    if isinstance(value, list):
        return [_safe_nested_value(item, depth=depth + 1) for item in value[:8]]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)[:80]
            if SENSITIVE_KEY_RE.search(key_text):
                continue
            cleaned = _safe_nested_value(item, depth=depth + 1)
            if cleaned not in (None, "", [], {}):
                safe[key_text] = cleaned
        return safe
    text = str(value)[:200]
    return "" if PHONE_LIKE_RE.search(text) else text


def _redact_public_dialog_text(value: Any, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()[:limit]
    if not text:
        return ""
    text = EMAIL_LIKE_RE.sub("[email_redacted]", text)
    text = PHONE_LIKE_RE.sub("[phone_redacted]", text)
    text = re.sub(r"\b(?:client|chat|site|sender|token|secret|raw|payload)[_-]?[a-z0-9_-]+\b", "[id_redacted]", text, flags=re.I)
    return text[:limit]


def _safe_dialog_window(state: dict[str, Any], *, limit: int = 6) -> list[dict[str, str]]:
    window = state.get("dialog_window") if isinstance(state.get("dialog_window"), list) else []
    safe: list[dict[str, str]] = []
    for turn in window[-limit:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        if role not in {"user", "bot", "assistant"}:
            continue
        text = _redact_public_dialog_text(turn.get("text"), limit=500)
        if text:
            safe.append({"role": "bot" if role == "assistant" else role, "text": text})
    return safe


@dataclass(frozen=True)
class TurnDecision:
    action: str
    target: str = "none"
    search_policy: str = "required"
    response: str = ""

    @classmethod
    def from_meta(cls, search_meta: dict[str, Any] | None, *, fallback_action: str = "search") -> "TurnDecision":
        meta = search_meta if isinstance(search_meta, dict) else {}
        raw = meta.get("_turn_decision") if isinstance(meta.get("_turn_decision"), dict) else {}
        action = str(raw.get("action") or meta.get("_planner_action") or fallback_action or "search").strip()
        if action not in TURN_ACTIONS:
            action = fallback_action if fallback_action in TURN_ACTIONS else "search"
        target = str(raw.get("target") or "none").strip()
        search_policy = str(raw.get("search_policy") or ("forbidden" if action in {"recover_dialogue", "answer_current_options", "operator_contact", "off_topic"} else "required")).strip()
        if target not in TURN_TARGETS:
            target = "none"
        if search_policy not in TURN_SEARCH_POLICIES:
            search_policy = "required" if action == "search" else "forbidden"
        if action == "answer_current_options" and (target != "current_options" or search_policy != "forbidden"):
            action, target, search_policy = fallback_action, "none", "required"
        if action == "recover_dialogue" and search_policy != "forbidden":
            action, target, search_policy = fallback_action, "none", "required"
        response = str(raw.get("response") or "").strip()[:300]
        return cls(action=action, target=target, search_policy=search_policy, response=response)

    def public(self) -> dict[str, str]:
        return {"action": self.action, "target": self.target, "search_policy": self.search_policy}


@dataclass(frozen=True)
class CurrentOptionsResponseMode:
    mode: str
    reason: str
    scenario: str

    def public(self) -> dict[str, str]:
        return {"mode": self.mode, "reason": self.reason, "scenario": self.scenario}


class JsonStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._data: dict[str, Any] | None = None
        self.last_load_error: str | None = None

    async def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        self.last_load_error = None
        if not self.path.exists():
            self._data = {}
            return self._data
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                self.last_load_error = "state_load_error"
                self._data = {}
        except Exception:
            self.last_load_error = "state_load_error"
            self._data = {}
        return self._data

    async def get(self, user_id: str) -> dict[str, Any]:
        async with self._lock:
            data = await self._load()
            state = data.get(user_id)
            if not isinstance(state, dict):
                state = _canonical_reset_state()
                data[user_id] = state
            return state

    async def save(self, user_id: str, state: dict[str, Any]) -> None:
        async with self._lock:
            data = await self._load()
            data[user_id] = state
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    async def reset(self, user_id: str) -> None:
        async with self._lock:
            data = await self._load()
            data[user_id] = _canonical_reset_state()
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    async def reset_canonical(self, user_id: str) -> None:
        """Сбрасывает Jivo-сессию в canonical V2 namespace."""
        async with self._lock:
            data = await self._load()
            data[user_id] = _canonical_reset_state()
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)


class RuntimeVersionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._version: str | None = None

    async def get(self) -> str:
        async with self._lock:
            return await self._get_unlocked()

    async def set(self, version: str) -> str:
        normalized = _normalize_runtime_version(version)
        async with self._lock:
            self._write_unlocked(normalized)
            return normalized

    def _write_unlocked(self, version: str) -> None:
        self._version = version
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"version": version}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    async def _get_unlocked(self) -> str:
        persisted = None
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                persisted = value.get("version") if isinstance(value, dict) else None
            except Exception:
                persisted = None
        normalized = _supported_runtime_version_or_none(persisted) or "V6"
        if self._version != normalized:
            self._version = normalized
        return normalized


class SessionLockRegistry:
    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @property
    def size(self) -> int:
        return len(self._locks)

    @asynccontextmanager
    async def hold(self, session_key: str):
        async with self._guard:
            entry = self._locks.get(session_key)
            if entry is None:
                lock = asyncio.Lock()
                users = 0
            else:
                lock, users = entry
            self._locks[session_key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            async with self._guard:
                current = self._locks.get(session_key)
                if current is not None and current[0] is lock:
                    remaining = current[1] - 1
                    if remaining <= 0:
                        self._locks.pop(session_key, None)
                    else:
                        self._locks[session_key] = (lock, remaining)


class JivoDedupCache:
    def __init__(self, *, ttl_sec: int = JIVO_DEDUP_TTL_SEC, max_entries: int = JIVO_DEDUP_MAX_ENTRIES) -> None:
        self.ttl_sec = ttl_sec
        self.max_entries = max_entries
        self._items: OrderedDict[tuple[str, str], tuple[float, dict[str, Any], int]] = OrderedDict()

    def _cleanup(self, now: float) -> None:
        expired_before = now - self.ttl_sec
        expired_keys = [key for key, (created_at, _, _) in self._items.items() if created_at < expired_before]
        for key in expired_keys:
            self._items.pop(key, None)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def get(self, session_key: str, event_id: str) -> tuple[dict[str, Any], int] | None:
        now = time.monotonic()
        self._cleanup(now)
        key = (session_key, event_id)
        item = self._items.get(key)
        if item is None:
            return None
        created_at, payload, status = item
        self._items.move_to_end(key)
        return copy.deepcopy(payload), status

    def set(self, session_key: str, event_id: str, payload: dict[str, Any], status: int) -> None:
        now = time.monotonic()
        self._cleanup(now)
        key = (session_key, event_id)
        self._items[key] = (now, copy.deepcopy(payload), status)
        self._items.move_to_end(key)
        self._cleanup(now)


def _api_token_ok(request: web.Request) -> bool:
    token = os.getenv("NMBOT_API_TOKEN", "").strip()
    if not token:
        return True
    auth = request.headers.get("Authorization", "")
    header_token = request.headers.get("X-NMBOT-API-Token", "")
    return auth == f"Bearer {token}" or header_token == token


def _jivo_session_key(payload: dict[str, Any]) -> str:
    site_id = str(payload.get("site_id") or "unknown-site")
    chat_id = str(payload.get("chat_id") or "unknown-chat")
    client_id = str(payload.get("client_id") or "unknown-client")
    return f"jivo:{site_id}:{chat_id}:{client_id}"


def _jivo_event_id(payload: dict[str, Any]) -> str:
    event_id = str(payload.get("id") or "").strip()
    if event_id:
        return event_id
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("id") or "").strip()


def _meta_event_id(meta: dict[str, Any] | None) -> str:
    if not isinstance(meta, dict):
        return ""
    return str(meta.get("event_id") or meta.get("message_id") or "").strip()


def _jivo_meta(payload: dict[str, Any]) -> dict[str, Any]:
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
    return {
        "site_id": payload.get("site_id"),
        "client_id": payload.get("client_id"),
        "chat_id": payload.get("chat_id"),
        "agents_online": payload.get("agents_online"),
        "sender_name": sender.get("name"),
        "sender_url": sender.get("url"),
        "sender_has_contacts": sender.get("has_contacts"),
        "jivo_channel_id": channel.get("id"),
        "jivo_channel_type": channel.get("type"),
        "event_id": _jivo_event_id(payload),
    }


def _safe_contact_name(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    text = re.sub(r"[^A-Za-zА-Яа-яЁё\- ]", "", text).strip(" -")[:60]
    if not text or PHONE_LIKE_RE.search(text) or len(text) < 2:
        return ""
    if not NAME_RE.match(text):
        return ""
    words = [part for part in text.split() if part]
    if len(words) > 3:
        return ""
    return " ".join(words)


def _prefill_contact_name_from_meta(state: dict[str, Any], meta: dict[str, Any] | None) -> str:
    existing = _safe_contact_name(state.get("contact_name"))
    if existing:
        return existing
    meta = meta if isinstance(meta, dict) else {}
    name = _safe_contact_name(meta.get("sender_name"))
    if name:
        state["contact_name"] = name
        state["contact_name_source"] = "jivo_profile"
    return name


def _callback_confirmation(name: str) -> str:
    safe_name = _safe_contact_name(name) or ""
    if safe_name:
        return f"Спасибо, {safe_name}. Заявку на обратный звонок сохранила — специалист свяжется с вами."
    return "Спасибо. Заявку на обратный звонок сохранила — специалист свяжется с вами."


def _ask_contact_name() -> str:
    return "Как я могу к вам обращаться?"


def _ask_contact_phone(name: str = "") -> str:
    safe_name = _safe_contact_name(name)
    if safe_name:
        return f"{safe_name}, пришлите, пожалуйста, номер телефона — сохраню заявку на обратный звонок."
    return "Пришлите, пожалуйста, номер телефона — сохраню заявку на обратный звонок."


def _reset_contact_flow(state: dict[str, Any]) -> None:
    state["contact_flow"] = "normal"
    state["awaiting_phone"] = False
    state.pop("contact_phone_draft_meta", None)


def _scrub_latest_user_phone_turn(state: dict[str, Any]) -> None:
    window = state.get("dialog_window")
    if not isinstance(window, list):
        return
    for turn in reversed(window):
        if isinstance(turn, dict) and turn.get("role") == "user":
            turn["text"] = "[phone_captured_private]"
            return


def _safe_summary_input_from_state(state: dict[str, Any], *, channel: str, meta: dict[str, Any] | None) -> dict[str, Any]:
    return build_callback_lead_context(state, channel=channel, meta=meta or {})


def _public_callback_result(
    *,
    answer: str,
    crm_callback: dict[str, str] | None = None,
    intent: str = "capture_contact",
    awaiting_phone: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "answer": answer,
        "intent": intent,
        "awaiting_phone": awaiting_phone,
        "handoff_to_operator": False,
        "selected_option": None,
        "buttons": [],
        "turn_decision": TurnDecision(action="capture_contact", target="none", search_policy="forbidden").public(),
    }
    if crm_callback is not None:
        result["crm_callback"] = crm_callback
    return result


def _safe_turn_context(text: str, state: dict[str, Any]) -> dict[str, Any]:
    return _neutral_safe_turn_context(text, state)


def _safe_planner_state(text: str, state: dict[str, Any]) -> dict[str, Any]:
    return _neutral_safe_planner_state(text, state)


def _has_current_options(state: dict[str, Any]) -> bool:
    return bool(state.get("visible_options") or state.get("last_options"))


def _has_canonical_plan(plan: dict[str, Any]) -> bool:
    if "canonical_fields_absent" in {str(item) for item in (plan.get("canonical_errors") or [])}:
        return False
    return "action" in plan and any(key in plan for key in CANONICAL_PLAN_KEYS)


def _has_semantic_plan(plan: dict[str, Any]) -> bool:
    return any(key in plan for key in {"goal", "requested_facts", "operation", "constraints_delta", "reference"})


def _ensure_derived_canonical_plan(plan: dict[str, Any], planner_state: dict[str, Any]) -> dict[str, Any]:
    if _has_canonical_plan(plan) or not _has_semantic_plan(plan):
        return plan
    if hasattr(followup_intent_classifier, "_with_canonical_fields"):
        return followup_intent_classifier._with_canonical_fields({}, plan, state=planner_state)
    return plan


INTENT_VALUE_ALIASES = {
    "family": "family",
    "для семьи": "family",
    "семья": "family",
    "семейный": "family",
    "investment": "investment",
    "invest": "investment",
    "инвестиции": "investment",
    "для инвестиций": "investment",
    "rental": "rental",
    "rent": "rental",
    "аренда": "rental",
    "для аренды": "rental",
    "под аренду": "rental",
    "сдача": "rental",
    "под сдачу": "rental",
    "для сдачи": "rental",
    "сдать в аренду": "rental",
    "life": "life",
    "living": "life",
    "для жизни": "life",
    "жизнь": "life",
    "mortgage": "mortgage",
    "ипотека": "mortgage",
    "compare": "unknown",
    "comparison": "unknown",
    "сравнение": "unknown",
    "unknown": "unknown",
}


def _canonical_intent_value(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if text in CANONICAL_INTENTS:
        return text
    return INTENT_VALUE_ALIASES.get(text, "unknown")


def _derive_canonical_primary_intent(
    state: dict[str, Any],
    *,
    planner_plan: dict[str, Any] | None = None,
    search_params: dict[str, Any] | None = None,
) -> str:
    """Return a safe primary intent without reading raw user text.

    Priority is explicit state first, then canonical planner output, then already
    structured search params. Unknown never overwrites a known explicit intent.
    """
    state_intent = _canonical_intent_value(state.get("primary_intent"))
    if state_intent != "unknown":
        return state_intent
    state_params = state.get("params") if isinstance(state.get("params"), dict) else {}
    param_intent = _canonical_intent_value(state_params.get("primary_intent"))
    if param_intent != "unknown":
        return param_intent

    if isinstance(planner_plan, dict):
        planner_intent = _canonical_intent_value(planner_plan.get("intent"))
        if planner_intent != "unknown":
            return planner_intent

    for params in (search_params, state_params):
        if not isinstance(params, dict):
            continue
        for key in ("primary_intent", "purpose", "scenario"):
            mapped = _canonical_intent_value(params.get(key))
            if mapped != "unknown":
                return mapped

    scenario_context = state.get("scenario_context") if isinstance(state.get("scenario_context"), dict) else {}
    active_scenario = state.get("active_scenario") if isinstance(state.get("active_scenario"), dict) else {}
    for source in (scenario_context, active_scenario):
        for key in ("primary_scenario", "purpose", "intent"):
            mapped = _canonical_intent_value(source.get(key))
            if mapped != "unknown":
                return mapped
    return "unknown"


def _persist_primary_intent(state: dict[str, Any], intent: str) -> None:
    canonical = _canonical_intent_value(intent)
    if canonical == "unknown":
        return
    existing = _canonical_intent_value(state.get("primary_intent"))
    if existing != "unknown":
        return
    state["primary_intent"] = canonical


def _planner_scope(plan: dict[str, Any] | None) -> str:
    scope = str((plan or {}).get("scope") or "unknown").strip()
    return scope if scope in CANONICAL_SCOPES else "unknown"


def _apply_explicit_primary_intent_switch(state: dict[str, Any], plan: dict[str, Any] | None) -> None:
    if not isinstance(plan, dict):
        return
    canonical = _canonical_intent_value(plan.get("intent"))
    if canonical == "unknown":
        return
    if str(plan.get("intent_policy") or "").strip() != "change":
        return
    if _safe_float(plan.get("confidence")) < PLANNER_MIN_CONFIDENCE:
        return
    existing = _canonical_intent_value(state.get("primary_intent"))
    if existing == canonical:
        return
    state["primary_intent"] = canonical
    params = state.get("params") if isinstance(state.get("params"), dict) else {}
    state["params"] = {**params, "primary_intent": canonical, "purpose": canonical}
    scenario_context = state.get("scenario_context") if isinstance(state.get("scenario_context"), dict) else {}
    state["scenario_context"] = {**scenario_context, "primary_scenario": canonical, "purpose": canonical}


def _selected_option_resolved_this_turn(state: dict[str, Any]) -> bool:
    pending = state.get("pending_followup") if isinstance(state.get("pending_followup"), dict) else {}
    return pending.get("type") == "selected_option" and isinstance(state.get("selected_option"), dict) and bool(state.get("selected_option"))


def _normalize_option_ref_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    chars: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        chars.append(char if category[0] in {"L", "N"} else " ")
    return " ".join("".join(chars).split())


def _safe_option_name(option: Any) -> str:
    if not isinstance(option, dict):
        return ""
    name = _normalize_option_ref_text(option.get("name"))
    if len(name) < 6 or not any(ch.isalnum() for ch in name):
        return ""
    return name


def _option_ref_tokens(value: str) -> list[str]:
    ignored = {
        "жк", "комплекс", "жилой", "район", "парк", "семейный", "семейная", "семейной", "семейное", "семейные",
        "ипотека", "ипотеки", "ипотеку", "аренда", "аренду", "сдавать", "сдачу", "инвестиции", "инвестиций",
        "жизни", "семьи", "сравни", "сравнить", "вариант", "варианты", "подробнее", "проверь", "давай",
    }
    return [token for token in _normalize_option_ref_text(value).split() if len(token) >= 5 and token not in ignored]


def _fuzzy_current_option_matches(options: list[Any], text: str) -> list[dict[str, Any]]:
    text_tokens = _option_ref_tokens(text)
    if not text_tokens:
        return []
    matches: list[tuple[float, dict[str, Any]]] = []
    seen: set[str] = set()
    for option in options:
        if not isinstance(option, dict):
            continue
        name = _safe_option_name(option)
        if not name or name in seen:
            continue
        seen.add(name)
        name_tokens = _option_ref_tokens(name)
        score = 0.0
        for user_token in text_tokens:
            for name_token in name_tokens:
                if user_token == name_token:
                    continue
                score = max(score, difflib.SequenceMatcher(None, user_token, name_token).ratio())
        if score >= 0.82:
            matches.append((score, option))
    if not matches:
        return []
    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) > 1 and matches[0][0] - matches[1][0] < 0.08:
        return []
    return [matches[0][1]]


def _resolve_exact_current_option_reference(state: dict[str, Any], text: str) -> None:
    """Resolve a unique current option reference before semantic routing.

    Exact full-name matches are accepted. Fuzzy matching is deliberately narrower:
    it only accepts a unique high-confidence typo against a meaningful name token,
    so a bare exact partial like "Семейный" still stays ambiguous unless the user
    names the full visible option.
    """
    normalized_text = _normalize_option_ref_text(text)
    if not normalized_text:
        return
    options = state.get("visible_options") or state.get("last_options") or []
    if not isinstance(options, list):
        return
    seen: set[str] = set()
    matches: list[dict[str, Any]] = []
    padded_text = f" {normalized_text} "
    for option in options:
        name = _safe_option_name(option)
        if not name or name in seen:
            continue
        seen.add(name)
        if f" {name} " in padded_text and isinstance(option, dict):
            matches.append(option)
    if len(matches) == 1:
        state["selected_option"] = copy.deepcopy(matches[0])
        state["pending_followup"] = {"type": "selected_option", "option_name": str(matches[0].get("name") or "")[:120]}
        return
    fuzzy_matches = _fuzzy_current_option_matches(options, text)
    if len(fuzzy_matches) == 1:
        state["selected_option"] = copy.deepcopy(fuzzy_matches[0])
        state["pending_followup"] = {"type": "selected_option", "option_name": str(fuzzy_matches[0].get("name") or "")[:120], "match": "fuzzy"}


def _current_option_allowlist(state: dict[str, Any]) -> list[dict[str, Any]]:
    options = state.get("visible_options") or state.get("last_options") or []
    return [item for item in options if isinstance(item, dict) and str(item.get("name") or "").strip()] if isinstance(options, list) else []


def _find_current_option_by_exact_name(state: dict[str, Any], selected_name: Any) -> dict[str, Any] | None:
    wanted = _normalize_option_ref_text(selected_name)
    if not wanted:
        return None
    for option in _current_option_allowlist(state):
        if _normalize_option_ref_text(option.get("name")) == wanted:
            return option
    return None


def _resolve_current_option_by_number(state: dict[str, Any], text: str) -> dict[str, Any] | None:
    normalized = _normalize_option_ref_text(text)
    ordinal = {
        "1": 1, "первый": 1, "первого": 1, "первая": 1,
        "2": 2, "второй": 2, "второго": 2, "вторая": 2,
        "3": 3, "третий": 3, "третьего": 3, "третья": 3,
        "4": 4, "четвертый": 4, "четвертого": 4, "четвертая": 4,
        "5": 5, "пятый": 5, "пятого": 5, "пятая": 5,
    }
    tokens = normalized.split()
    if len(tokens) > 4:
        return None
    idx = next((ordinal[token] for token in tokens if token in ordinal), None)
    options = _current_option_allowlist(state)
    if idx is None or idx < 1 or idx > len(options):
        return None
    return options[idx - 1]


def _resolve_conservative_current_option_fallback(state: dict[str, Any], text: str) -> bool:
    """Migration shadow path after planner failure only.

    Canonical planner exact selection has precedence. This fallback accepts only
    exact full-name/number or a unique high-confidence typo against meaningful
    option-name tokens; generic business words such as "семейная ипотека" cannot
    select a ЖК.
    """
    option = _resolve_current_option_by_number(state, text)
    if option is None:
        normalized_text = _normalize_option_ref_text(text)
        padded_text = f" {normalized_text} "
        exact = [item for item in _current_option_allowlist(state) if f" {_normalize_option_ref_text(item.get('name'))} " in padded_text]
        if len(exact) == 1:
            option = exact[0]
    if option is None:
        fuzzy_matches = _fuzzy_current_option_matches(_current_option_allowlist(state), text)
        if len(fuzzy_matches) == 1:
            option = fuzzy_matches[0]
    if option is None:
        return False
    state["selected_option"] = copy.deepcopy(option)
    state["pending_followup"] = {"type": "selected_option", "option_name": str(option.get("name") or "")[:120], "match": "shadow_fallback"}
    return True


def _resolve_exact_full_current_option_prefill(state: dict[str, Any], text: str) -> bool:
    """Planner-state prefill for exact full names only; no fuzzy guessing here."""
    normalized_text = _normalize_option_ref_text(text)
    if not normalized_text:
        return False
    padded_text = f" {normalized_text} "
    exact = [item for item in _current_option_allowlist(state) if f" {_normalize_option_ref_text(item.get('name'))} " in padded_text]
    if len(exact) != 1:
        return False
    state["selected_option"] = copy.deepcopy(exact[0])
    state["pending_followup"] = {"type": "selected_option", "option_name": str(exact[0].get("name") or "")[:120], "match": "prefill_exact"}
    return True


def _is_short_affirmation(text: str) -> bool:
    normalized = _normalize_option_ref_text(text)
    return normalized in {"да", "давай", "давайте", "ок", "хорошо", "конечно", "согласен", "согласна"}


def _is_operator_explanation_question(text: str) -> bool:
    normalized = _normalize_option_ref_text(text)
    return bool(
        "оператор" in normalized
        and any(marker in normalized for marker in ("зачем", "почему", "для чего", "что даст", "нужен ли"))
    )


def _contextual_affirmation_decision(state: dict[str, Any], text: str) -> TurnDecision | None:
    if not _is_short_affirmation(text):
        return None
    last_question = str(state.get("last_bot_question") or "").casefold().replace("ё", "е")
    if any(token in last_question for token in ("оператор", "менеджер", "специалист", "позвать", "передать запрос")):
        return TurnDecision(action="operator_contact", target="operator", search_policy="forbidden")
    if state.get("selected_option") or _has_current_options(state):
        return TurnDecision(action="answer_current_options", target="current_options", search_policy="forbidden")
    return None


def _safe_recent_bot_text(state: dict[str, Any], limit: int = 900) -> str:
    window = state.get("dialog_window") if isinstance(state.get("dialog_window"), list) else []
    for turn in reversed(window):
        if isinstance(turn, dict) and turn.get("role") == "bot":
            return str(turn.get("text") or "").strip()[:limit]
    return ""


def _safe_visible_response_text(state: dict[str, Any], limit: int = 700) -> str:
    options = state.get("visible_options") or state.get("last_options") or []
    if not isinstance(options, list):
        return ""
    lines: list[str] = []
    for idx, option in enumerate(options[:5], start=1):
        if not isinstance(option, dict):
            continue
        name = str(option.get("name") or "").strip()
        facts = []
        for key in ("price_range", "price_min", "location", "metro", "ready", "finishing"):
            value = option.get(key)
            if value not in (None, "", [], {}):
                facts.append(f"{key}: {str(value)[:80]}")
        if name:
            lines.append(f"{idx}. {name}" + (" — " + "; ".join(facts[:4]) if facts else ""))
    return "\n".join(lines)[:limit]


def _safe_search_snapshot_from_meta(search_meta: dict[str, Any] | None) -> dict[str, Any]:
    meta = search_meta if isinstance(search_meta, dict) else {}
    raw = meta.get("_response_text")
    payload = None
    if isinstance(raw, str) and raw.strip():
        try:
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                parsed = json.loads(raw[start : end + 1])
                if isinstance(parsed, dict):
                    payload = parsed
        except Exception:
            payload = None
    snapshot: dict[str, Any] = {
        "model": str(meta.get("model") or SEARCH_MODEL)[:80],
        "counts": {
            "facts": int(meta.get("four_layer_matched_count") or meta.get("four_layer_facts_count") or 0),
            "near": int(meta.get("four_layer_near_count") or 0),
        },
    }
    if isinstance(payload, dict):
        for key in ("facts", "near"):
            value = payload.get(key)
            if isinstance(value, list):
                snapshot[key] = _safe_nested_value(value[:5])
                snapshot["counts"][key] = len(value)
        for key in ("params", "missing"):
            value = payload.get(key)
            if value not in (None, "", [], {}):
                snapshot[key] = _safe_nested_value(value)
    return _safe_nested_value(snapshot) or {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_canonical_param_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value if isinstance(value, bool) else None
    if isinstance(value, (int, float)):
        return value if abs(float(value)) <= 100_000_000_000 else None
    if isinstance(value, str):
        text = value.strip()
        if not text or len(text) > 120 or PHONE_LIKE_RE.search(text):
            return None
        return text
    if isinstance(value, list):
        if len(value) > 8:
            return None
        cleaned = [_safe_canonical_param_value(item) for item in value]
        if any(item is None for item in cleaned):
            return None
        return cleaned
    return None


def _canonical_search_constraints_patch(plan: dict[str, Any]) -> dict[str, Any]:
    if not _has_canonical_plan(plan):
        return {}
    if not (
        str(plan.get("action") or "") == "search"
        and str(plan.get("target") or "") == "new_search"
        and str(plan.get("search_policy") or "") == "required"
    ):
        return {}
    constraints_patch = plan.get("constraints_patch") if isinstance(plan.get("constraints_patch"), dict) else {}
    merged: dict[str, Any] = {}
    for category in CANONICAL_PARAM_CATEGORY_ORDER:
        fields = constraints_patch.get(category) if isinstance(constraints_patch.get(category), dict) else {}
        for raw_key, raw_value in fields.items():
            key = str(raw_key).strip()
            if key not in CANONICAL_PARAM_ALLOWLIST or SENSITIVE_KEY_RE.search(key):
                continue
            value = _safe_canonical_param_value(raw_value)
            if value in (None, "", [], {}):
                continue
            merged[key] = value
    return merged


def _params_with_canonical_search_constraints(base_params: dict[str, Any], plan: dict[str, Any], decision: TurnDecision, state: dict[str, Any]) -> dict[str, Any]:
    if decision.action != "search" or decision.search_policy != "required":
        return dict(base_params)
    if not _canonical_runtime_executable(plan, state):
        return dict(base_params)
    patch = _canonical_search_constraints_patch(plan)
    if not patch:
        return dict(base_params)
    return {**base_params, **patch}


def _safe_search_hard_constraint_fields(fields: Any) -> dict[str, Any]:
    if not isinstance(fields, dict):
        return {}
    out: dict[str, Any] = {}
    for raw_key, raw_value in fields.items():
        key = str(raw_key or "").strip()
        if key not in SEARCH_HARD_CONSTRAINT_ALLOWLIST or SENSITIVE_KEY_RE.search(key):
            continue
        value = _safe_canonical_param_value(raw_value)
        if value not in (None, "", [], {}):
            out[key] = value
    return out


def _search_hard_constraints_for_ask(
    *,
    plan: dict[str, Any],
    decision: TurnDecision,
    ask_params: dict[str, Any],
) -> dict[str, Any]:
    """Build safe typed hard constraints for OvermindClient.ask.

    Canonical planner categories are kept when present; for legacy search turns
    we send a conservative flat hard set derived from the already-safe search
    params so old callers keep their behavior while the search contract gets a
    machine-readable exact-match envelope.
    """
    if decision.action != "search" or decision.search_policy != "required":
        return {}
    if _has_canonical_plan(plan):
        constraints_patch = plan.get("constraints_patch") if isinstance(plan.get("constraints_patch"), dict) else {}
        categorized: dict[str, Any] = {}
        for category in ("hard", "preferences", "unknown"):
            cleaned = _safe_search_hard_constraint_fields(constraints_patch.get(category))
            if cleaned:
                categorized[category] = cleaned
        return categorized
    return _safe_search_hard_constraint_fields(ask_params)


def _canonical_known_fields_from_state(state: dict[str, Any]) -> set[str]:
    fields = {str(key) for key, value in (state.get("params") or {}).items() if value not in (None, "", [], {})}
    if fields & {"purpose", "scenario", "primary_intent"}:
        fields.update({"purpose", "primary_intent"})
    scenario_context = state.get("scenario_context") if isinstance(state.get("scenario_context"), dict) else {}
    active_scenario = state.get("active_scenario") if isinstance(state.get("active_scenario"), dict) else {}
    for source in (scenario_context, active_scenario):
        for key in ("primary_scenario", "purpose", "intent"):
            if source.get(key):
                fields.update({"purpose", "primary_intent", str(key)})
    return fields


def _validate_canonical_plan(plan: dict[str, Any], state: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if plan.get("canonical_valid") is False:
        errors.extend(str(item) for item in (plan.get("canonical_errors") or ["canonical_invalid"]))
    if str(plan.get("action") or "") not in CANONICAL_ACTIONS:
        errors.append("invalid_action")
    if str(plan.get("intent") or "") not in CANONICAL_INTENTS:
        errors.append("invalid_intent")
    if str(plan.get("intent_policy") or "") not in CANONICAL_INTENT_POLICIES:
        errors.append("invalid_intent_policy")
    if str(plan.get("target") or "") not in CANONICAL_TARGETS:
        errors.append("invalid_target")
    if str(plan.get("search_policy") or "") not in CANONICAL_SEARCH_POLICIES:
        errors.append("invalid_search_policy")
    if "search_profile" in plan and str(plan.get("search_profile") or "").strip().lower() not in CANONICAL_SEARCH_PROFILES:
        errors.append("invalid_search_profile")
    if "scope" in plan and str(plan.get("scope") or "unknown").strip() not in CANONICAL_SCOPES:
        errors.append("invalid_scope")
    action = str(plan.get("action") or "")
    target = str(plan.get("target") or "")
    search_policy = str(plan.get("search_policy") or "")
    valid_pairs = {
        ("search", "new_search", "required"),
        ("answer_current_options", "current_options", "forbidden"),
        ("operator_contact", "operator", "forbidden"),
        ("operator_contact", "none", "forbidden"),
        ("recover_dialogue", "none", "forbidden"),
        ("clarify", "none", "forbidden"),
        ("off_topic", "none", "forbidden"),
    }
    if action in CANONICAL_ACTIONS and target in CANONICAL_TARGETS and search_policy in CANONICAL_SEARCH_POLICIES and (action, target, search_policy) not in valid_pairs:
        errors.append("invalid_action_target_search_policy")
    dialog_action = str(plan.get("dialog_action") or "")
    if "dialog_action" in plan and dialog_action not in (PLANNER_SEARCH_ACTIONS | PLANNER_CURRENT_OPTIONS_ACTIONS | PLANNER_RECOVERY_ACTIONS | {"operator_live_check", "recommend_options", "conversation_answer", "consultation_answer"}):
        errors.append("invalid_dialog_action")
    operator_contact = plan.get("operator_contact")
    if "operator_contact" in plan:
        if not isinstance(operator_contact, dict):
            errors.append("invalid_operator_contact")
        else:
            if not isinstance(operator_contact.get("requested"), bool):
                errors.append("invalid_operator_contact_requested")
            if str(operator_contact.get("consent") or "none") not in CANONICAL_OPERATOR_CONSENTS:
                errors.append("invalid_operator_contact_consent")
    selected_name = str(plan.get("selected_option_name") or "").strip()
    scope = str(plan.get("scope") or "unknown").strip()
    if selected_name and _find_current_option_by_exact_name(state, selected_name) is None:
        errors.append("selected_option_not_in_current_options")
    if action == "answer_current_options" and scope == "one" and not selected_name and not isinstance(state.get("selected_option"), dict):
        errors.append("selected_option_required_for_scope_one")
    if action == "clarify" and not str(plan.get("clarification_question") or plan.get("clarification") or "").strip():
        errors.append("clarification_required")
    if str(plan.get("intent_policy") or "") in {"set", "change"} and str(plan.get("intent") or "") == "unknown":
        errors.append("intent_policy_requires_known_intent")
    if dialog_action == "select_option" and not selected_name and not isinstance(state.get("selected_option"), dict):
        errors.append("select_option_requires_selected_option_name")
    confidence = plan.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
        errors.append("invalid_confidence")
    constraints_patch = plan.get("constraints_patch")
    if not isinstance(constraints_patch, dict):
        errors.append("invalid_constraints_patch")
    else:
        for category, fields in constraints_patch.items():
            if category not in CANONICAL_CONSTRAINT_CATEGORIES or not isinstance(fields, dict):
                errors.append("invalid_constraints_category")
                break
    if not isinstance(plan.get("facets"), dict):
        errors.append("invalid_facets")
    for field_name in ("missing_fields", "clarification_fields"):
        items = plan.get(field_name)
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            errors.append(f"invalid_{field_name}")
    known_fields = _canonical_known_fields_from_state(state)
    requested_known = sorted(known_fields & (set(plan.get("missing_fields") or []) | set(plan.get("clarification_fields") or [])))
    if requested_known:
        errors.append("known_field_reasked:" + ",".join(requested_known))
    _add_canonical_semantic_errors(plan, errors)
    if str(plan.get("action") or "") == "search":
        current_intent = _derive_canonical_primary_intent(state)
        plan_intent = _canonical_intent_value(plan.get("intent"))
        policy = str(plan.get("intent_policy") or "")
        if current_intent == "unknown":
            if policy != "set":
                errors.append("search_new_intent_requires_set")
        elif plan_intent != "unknown" and plan_intent != current_intent:
            if policy != "change":
                errors.append("search_changed_intent_requires_change")
        else:
            if policy not in {"keep", "set"}:
                errors.append("search_same_intent_policy_invalid")
    return not errors, sorted(set(errors))


def _decision_from_canonical_planner(plan: dict[str, Any], state: dict[str, Any]) -> TurnDecision:
    question = str(plan.get("clarification_question") or "").strip()[:300]
    valid, errors = _validate_canonical_plan(plan, state)
    if errors:
        plan["canonical_validation_errors"] = errors
    blocking_errors, _advisory_errors = _canonical_runtime_blocking_errors(plan, state)
    confidence = _safe_float(plan.get("confidence"))
    if blocking_errors or plan.get("fallback_used") or confidence < PLANNER_MIN_CONFIDENCE:
        return TurnDecision(action="recover_dialogue", target="none", search_policy="forbidden", response=question)
    action = str(plan.get("action") or "").strip()
    target = str(plan.get("target") or "").strip()
    search_policy = str(plan.get("search_policy") or "").strip()
    if action == "operator_contact":
        return TurnDecision(action="operator_contact", target="operator", search_policy="forbidden")
    if action == "off_topic" and target == "none" and search_policy == "forbidden":
        return TurnDecision(action="off_topic", target="none", search_policy="forbidden")
    if action == "answer_current_options" and search_policy == "forbidden":
        selected_name = str(plan.get("selected_option_name") or "").strip()
        selected_option = _find_current_option_by_exact_name(state, selected_name) if selected_name else None
        selected_scope_ok = str(plan.get("dialog_action") or "").strip() == "select_option" and str(plan.get("scope") or "").strip() == "one" and bool(selected_option)
        if (target == "current_options" and _has_current_options(state)) or selected_scope_ok:
            return TurnDecision(action="answer_current_options", target="current_options", search_policy="forbidden")
    if action in {"recover_dialogue", "clarify"}:
        return TurnDecision(action="recover_dialogue", target="none", search_policy="forbidden", response=question)
    if action == "search" and target == "new_search" and search_policy == "required":
        return TurnDecision(action="search", target="new_search", search_policy="required")
    return TurnDecision(action="recover_dialogue", target="none", search_policy="forbidden", response=question)


def _safe_planner_public(plan: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {}
    try:
        confidence = round(float(plan.get("confidence") or 0.0), 3)
    except (TypeError, ValueError):
        confidence = 0.0
    public = {
        "dialog_action": str(plan.get("dialog_action") or "")[:80],
        "confidence": confidence,
        "fallback_used": bool(plan.get("fallback_used")),
    }
    if plan.get("action") is not None:
        public["action"] = str(plan.get("action") or "")[:40]
    if plan.get("intent") is not None:
        public["intent"] = str(plan.get("intent") or "")[:40]
    if plan.get("intent_policy") is not None:
        public["intent_policy"] = str(plan.get("intent_policy") or "")[:20]
    if plan.get("target") is not None:
        public["target"] = str(plan.get("target") or "")[:40]
    if plan.get("search_policy") is not None:
        public["search_policy"] = str(plan.get("search_policy") or "")[:40]
    if plan.get("scope") is not None:
        public["scope"] = _planner_scope(plan)
    errors = plan.get("canonical_validation_errors") or plan.get("canonical_errors") or plan.get("source_canonical_errors") or []
    if isinstance(errors, list):
        safe_errors = sorted({str(item)[:120] for item in errors if str(item).strip()})[:12]
        if safe_errors:
            public["canonical_error_codes"] = safe_errors
    if plan.get("repair_attempted") is not None:
        public["repair_attempted"] = bool(plan.get("repair_attempted"))
    if plan.get("repair_applied") is not None:
        public["repair_applied"] = bool(plan.get("repair_applied"))
    return public


def _planner_diagnostics(plan: dict[str, Any] | None) -> dict[str, Any]:
    public = _safe_planner_public(plan)
    return public if public else {"fallback_used": True}


def _append_safe_planner_trace(*, session_key: str, channel: str, plan: dict[str, Any] | None, decision: TurnDecision, exception_code: str | None = None, user_text: str = "") -> None:
    _neutral_append_safe_planner_trace(
        session_key=session_key,
        channel=channel,
        plan=plan,
        decision=decision,
        exception_code=exception_code,
        user_text=user_text,
    )


def _canonical_error_code(error: str) -> str:
    return str(error or "").split(":", 1)[0]


def _canonical_runtime_blocking_errors(plan: dict[str, Any], state: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Split strict canonical errors into runtime-blocking and advisory-only.

    The canonical validator intentionally remains strict for diagnostics/tests.
    Runtime may still execute a normalized, locally safe action when the only
    issues are semantic/cross-field warnings that do not affect the actual
    executable route. Structural/parser/allowlist failures keep failing closed.
    """
    _valid, errors = _validate_canonical_plan(plan, state)
    if errors:
        plan["canonical_validation_errors"] = errors

    action = str(plan.get("action") or "")
    target = str(plan.get("target") or "")
    search_policy = str(plan.get("search_policy") or "")
    codes = {_canonical_error_code(error) for error in errors}
    advisory = [error for error in errors if _canonical_error_code(error) in CANONICAL_ADVISORY_RUNTIME_ERRORS]
    blocking = [error for error in errors if _canonical_error_code(error) not in CANONICAL_ADVISORY_RUNTIME_ERRORS]

    executable = False
    if action == "search":
        executable = target == "new_search" and search_policy == "required"
    elif action == "answer_current_options":
        executable = target == "current_options" and search_policy == "forbidden" and _has_current_options(state)
    elif action == "operator_contact":
        executable = target in {"operator", "none"} and search_policy == "forbidden"
    elif action == "off_topic":
        executable = target == "none" and search_policy == "forbidden"

    if not executable:
        return errors, []
    if "selected_option_not_in_current_options" in codes:
        return errors, []
    if action == "answer_current_options" and str(plan.get("scope") or "") == "one" and not str(plan.get("selected_option_name") or "").strip() and not isinstance(state.get("selected_option"), dict):
        return errors, []
    return blocking, advisory


def _canonical_runtime_executable(plan: dict[str, Any], state: dict[str, Any]) -> bool:
    if not _has_canonical_plan(plan) or bool(plan.get("fallback_used")) or _safe_float(plan.get("confidence")) < PLANNER_MIN_CONFIDENCE:
        return False
    blocking, _advisory = _canonical_runtime_blocking_errors(plan, state)
    return not blocking


def _canonical_repair_allowed(plan: dict[str, Any], state: dict[str, Any], errors: list[str]) -> bool:
    if not _has_canonical_plan(plan):
        return False
    if bool(plan.get("fallback_used")) or _safe_float(plan.get("confidence")) < PLANNER_MIN_CONFIDENCE:
        return False
    if not errors:
        return False
    blocking, advisory = _canonical_runtime_blocking_errors(plan, state)
    if advisory and not blocking:
        return False
    codes = {_canonical_error_code(error) for error in errors}
    if any(code.startswith("missing_required") for code in codes):
        return False
    unsafe = {
        "selected_option_not_in_current_options",
        "selected_option_required_for_scope_one",
        "select_option_requires_selected_option_name",
        "current_options_one_selected_required",
        "invalid_confidence",
        "invalid_constraints_patch",
        "invalid_constraints_category",
        "invalid_facets",
        "invalid_missing_fields",
        "invalid_clarification_fields",
        "invalid_operator_contact",
        "invalid_operator_contact_requested",
        "invalid_operator_contact_consent",
        "intent_policy_requires_known_intent",
        "clarification_required",
    }
    if codes & unsafe:
        return False
    if not codes <= CANONICAL_REPAIRABLE_ERRORS:
        return False
    action = str(plan.get("action") or "")
    if action == "answer_current_options" and not _has_current_options(state):
        return False
    return True


def _accepted_canonical_plan(plan: dict[str, Any], state: dict[str, Any], decision: TurnDecision) -> bool:
    if decision.action == "recover_dialogue":
        return False
    if not _has_canonical_plan(plan) or bool(plan.get("fallback_used")) or _safe_float(plan.get("confidence")) < PLANNER_MIN_CONFIDENCE:
        return False
    return _canonical_runtime_executable(plan, state)


def _safe_canonical_repair_plan(plan: dict[str, Any]) -> dict[str, Any]:
    public = _safe_planner_public(plan)
    safe: dict[str, Any] = {key: public[key] for key in ("action", "dialog_action", "intent", "intent_policy", "target", "search_policy", "scope", "confidence") if key in public}
    for key in ("selected_option_name", "search_profile"):
        if plan.get(key) is not None:
            safe[key] = _safe_nested_value(plan.get(key))
    for key in ("constraints_patch", "facets", "missing_fields", "clarification_fields"):
        if key in plan:
            safe[key] = _safe_nested_value(plan.get(key))
    return safe


def _apply_planner_selection(state: dict[str, Any], plan: dict[str, Any]) -> None:
    if str(plan.get("dialog_action") or "") != "select_option" and str(plan.get("scope") or "") != "one":
        return
    selected_name = str(plan.get("selected_option_name") or "").strip()
    if not selected_name:
        return
    option = _find_current_option_by_exact_name(state, selected_name)
    if option is not None:
        state["selected_option"] = copy.deepcopy(option)
        state["pending_followup"] = {"type": "selected_option", "option_name": str(option.get("name") or "")[:120], "match": "planner_exact"}


def _decision_from_planner(plan: dict[str, Any] | None, state: dict[str, Any]) -> TurnDecision:
    if not isinstance(plan, dict):
        return TurnDecision(action="recover_dialogue", target="none", search_policy="forbidden")
    if _has_canonical_plan(plan):
        return _decision_from_canonical_planner(plan, state)
    action = str(plan.get("dialog_action") or "").strip()
    try:
        confidence = float(plan.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    question = str(plan.get("clarification_question") or "").strip()[:300]
    if plan.get("fallback_used") or confidence < PLANNER_MIN_CONFIDENCE:
        return TurnDecision(action="recover_dialogue", target="none", search_policy="forbidden", response=question)
    if action == "operator_live_check":
        return TurnDecision(action="operator_contact", target="none", search_policy="forbidden")
    if action in PLANNER_CURRENT_OPTIONS_ACTIONS and _has_current_options(state):
        return TurnDecision(action="answer_current_options", target="current_options", search_policy="forbidden")
    if action in PLANNER_RECOVERY_ACTIONS:
        return TurnDecision(action="recover_dialogue", target="none", search_policy="forbidden", response=question)
    if action in PLANNER_SEARCH_ACTIONS:
        return TurnDecision(action="search", target="new_search", search_policy="required")
    return TurnDecision(action="recover_dialogue", target="none", search_policy="forbidden", response=question)


async def _plan_jivo_turn(client: OvermindClient, *, text: str, state: dict[str, Any]) -> tuple[TurnDecision, dict[str, Any]]:
    contextual = _contextual_affirmation_decision(state, text)
    if contextual is not None:
        return contextual, {"dialog_action": contextual.action, "confidence": 1.0, "fallback_used": False, "contextual_affirmation": True}
    session = await client.ensure_session()
    planner_state = _safe_planner_state(text, state)
    plan = await followup_intent_classifier.plan_dialog_state(
        session,
        user_text=text,
        state=planner_state,
        last_response_text=_safe_recent_bot_text(state),
        search_response_text=json.dumps(state.get("last_search_snapshot") or {}, ensure_ascii=False)[:1200],
        visible_response_text=_safe_visible_response_text(state),
        model=str(state.get("planner_model") or os.getenv("NMBOT_DIALOG_PLANNER_MODEL", "")) or None,
    )
    if isinstance(plan, dict):
        plan = _ensure_derived_canonical_plan(plan, planner_state)
    decision = _decision_from_planner(plan, state)
    if isinstance(plan, dict) and decision.action == "recover_dialogue" and _has_canonical_plan(plan):
        errors = list(plan.get("canonical_validation_errors") or [])
        if not errors:
            _valid, errors = _validate_canonical_plan(plan, state)
            if errors:
                plan["canonical_validation_errors"] = errors
        if _canonical_repair_allowed(plan, state, errors) and hasattr(followup_intent_classifier, "repair_canonical_plan"):
            repaired = await followup_intent_classifier.repair_canonical_plan(
                session,
                original_plan=_safe_canonical_repair_plan(plan),
                allowed_error_codes=sorted({_canonical_error_code(error) for error in errors}),
                state=planner_state,
                model=str(state.get("planner_model") or os.getenv("NMBOT_DIALOG_PLANNER_MODEL", "")) or None,
            )
            if isinstance(repaired, dict):
                repair_valid, repair_errors = _validate_canonical_plan(repaired, state)
                if repair_errors:
                    repaired["canonical_validation_errors"] = repair_errors
                if repair_valid and not repaired.get("fallback_used") and _safe_float(repaired.get("confidence")) >= PLANNER_MIN_CONFIDENCE:
                    if isinstance(plan.get("planner_raw_response"), str) and "planner_raw_response" not in repaired:
                        repaired["planner_raw_response"] = plan.get("planner_raw_response")
                    repaired["repair_attempted"] = True
                    repaired["repair_applied"] = True
                    repaired["repair_source_errors"] = sorted({_canonical_error_code(error) for error in errors})
                    plan = repaired
                    decision = _decision_from_planner(plan, state)
                else:
                    plan["repair_attempted"] = True
                    plan["repair_applied"] = False
                    plan["repair_error_codes"] = sorted({_canonical_error_code(error) for error in repair_errors or [str(repaired.get("reason") or "repair_rejected")]})
    if decision.action == "recover_dialogue" and not decision.response:
        decision = TurnDecision(action="recover_dialogue", target="none", search_policy="forbidden", response=str(plan.get("clarification_question") or "") if isinstance(plan, dict) else "")
    return decision, plan if isinstance(plan, dict) else {}


FINANCING_FACETS = {"mortgage", "down_payment", "payment_terms", "family_mortgage"}
SCENARIO_CURRENT_OPTION_INTENTS = {"investment", "rental"}
OPERATOR_DECLINE_ACTIONS = {"reject_operator", "reject_phone", "reject_offer"}


def _planner_facets(plan: dict[str, Any] | None) -> dict[str, Any]:
    return plan.get("facets") if isinstance(plan, dict) and isinstance(plan.get("facets"), dict) else {}


def _has_financing_facet(plan: dict[str, Any] | None, user_text: str = "") -> bool:
    facets = _planner_facets(plan)
    if any(bool(facets.get(key)) for key in FINANCING_FACETS):
        return True
    intent = _canonical_intent_value(plan.get("intent") if isinstance(plan, dict) else None)
    if intent == "mortgage":
        return True
    text = re.sub(r"\s+", " ", str(user_text or "").casefold().replace("ё", "е"))
    return bool(re.search(r"ипот|первоначальн\w*\s+взнос|\bпв\b|рассрочк|услов(?:ия|иях)?\s+оплат", text))


def _operator_decline_or_pending_question(plan: dict[str, Any] | None, state: dict[str, Any], user_text: str) -> bool:
    pending = state.get("pending_followup") if isinstance(state.get("pending_followup"), dict) else {}
    dialog_action = str((plan or {}).get("dialog_action") or "")
    operator_contact = (plan or {}).get("operator_contact") if isinstance(plan, dict) else None
    consent = str(operator_contact.get("consent") or "") if isinstance(operator_contact, dict) else ""
    text = re.sub(r"\s+", " ", str(user_text or "").casefold().replace("ё", "е")).strip()
    short_decline = text in {"нет", "не", "не надо", "не нужно", "нет спасибо", "без оператора", "не хочу"}
    return dialog_action in OPERATOR_DECLINE_ACTIONS or consent == "refused" or (pending.get("type") == "operator_offer" and (short_decline or "оператор" in text))


def _resolve_current_options_response_mode(
    *,
    state: dict[str, Any],
    decision: TurnDecision,
    dialog_plan: dict[str, Any] | None,
    user_text: str,
) -> CurrentOptionsResponseMode:
    """Bounded priority table for current-options responses."""
    primary = _derive_canonical_primary_intent(state)
    plan_intent = _canonical_intent_value(dialog_plan.get("intent") if isinstance(dialog_plan, dict) else None)
    scenario = plan_intent if plan_intent in SCENARIO_CURRENT_OPTION_INTENTS else primary
    scope = str(state.get("current_options_scope") or _planner_scope(dialog_plan) or "unknown")
    dialog_action = str((dialog_plan or {}).get("dialog_action") or "")
    priority_checks: list[tuple[str, Any]] = [
        ("operator_decline_or_pending_question", lambda: _operator_decline_or_pending_question(dialog_plan, state, user_text)),
        ("selected_or_compare", lambda: scope == "one" or dialog_action in {"select_option", "compare_options"}),
        ("financing_facet", lambda: _has_financing_facet(dialog_plan, user_text)),
        (
            "scenario_only_current_options",
            lambda: decision.action == "answer_current_options"
            and scope == "all"
            and scenario in SCENARIO_CURRENT_OPTION_INTENTS
            and not _has_financing_facet(dialog_plan, user_text),
        ),
    ]
    for reason, predicate in priority_checks:
        if predicate():
            if reason in {"selected_or_compare", "scenario_only_current_options"}:
                return CurrentOptionsResponseMode("deterministic", reason, scenario)
            return CurrentOptionsResponseMode("consultation", reason, scenario)
    return CurrentOptionsResponseMode("consultation", "generic_current_options_followup", scenario)


def _recent_cash_on_hand_max_price_ambiguity(state: dict[str, Any], user_text: str, dialog_plan: dict[str, Any] | None) -> dict[str, Any]:
    if not _has_financing_facet(dialog_plan, user_text):
        return {}
    params = state.get("params") if isinstance(state.get("params"), dict) else {}
    max_price = params.get("max_price") or params.get("max_budget_m") or params.get("budget")
    if max_price in (None, "", [], {}):
        return {}
    current_turn = re.sub(r"\s+", " ", str(user_text or "").casefold().replace("ё", "е")).strip()
    for turn in reversed(_safe_dialog_window(state, limit=6)):
        if turn.get("role") != "user":
            continue
        text = re.sub(r"\s+", " ", str(turn.get("text") or "").casefold().replace("ё", "е")).strip()
        if text == current_turn:
            continue
        if re.search(r"\d", text) and re.search(r"на\s+руках|налич", text):
            return {
                "type": "cash_on_hand_vs_budget",
                "stored_field": "max_price" if params.get("max_price") not in (None, "", [], {}) else "budget",
                "stored_value": _safe_nested_value(max_price),
                "instruction": "Ask exactly one concise confirmation: is this amount the down payment, with the rest planned via mortgage? Do not convert max_price to down_payment until explicit confirmation.",
            }
    return {}


def _safe_current_options_answer_state(
    state: dict[str, Any],
    *,
    user_text: str = "",
    decision: TurnDecision | None = None,
    dialog_plan: dict[str, Any] | None = None,
    response_mode: CurrentOptionsResponseMode | None = None,
) -> dict[str, Any]:
    """Allowlist state for chat-only answers about already visible options.

    This keeps answer_current_options grounded in the current shortlist while
    preventing raw Jivo identifiers, full phones or raw dialog payloads from
    reaching the second LLM call.
    """
    payload = _dialog_planner_state_payload(state)
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    selected = payload.get("selected_option") if isinstance(payload.get("selected_option"), dict) else {}
    options = payload.get("visible_options") or payload.get("last_options") or []
    known_fields = sorted(_canonical_known_fields_from_state(state))
    missing_facts = state.get("missing_facts") or state.get("missing") or []
    if not missing_facts:
        missing_facts = [field for field in ("budget", "location", "rooms") if field not in known_fields and not params.get(field)]
    pending = state.get("pending_followup") if isinstance(state.get("pending_followup"), dict) else {}
    scope = _safe_nested_value(state.get("current_options_scope") or ("one" if selected else "all" if options else "unknown"))
    answer_goal = "answer_current_user_turn_without_new_search"
    ambiguity = _recent_cash_on_hand_max_price_ambiguity(state, user_text, dialog_plan)
    if ambiguity:
        answer_goal = "clarify_cash_on_hand_down_payment_before_financing_answer"
    elif pending.get("type"):
        answer_goal = f"resolve_pending_{str(pending.get('type'))[:60]}"
    if decision and decision.action == "answer_current_options" and not ambiguity:
        answer_goal = "answer_about_current_options_without_new_search"
    already_answered = [item for item in [state.get("last_answer_kind"), state.get("last_offer_type")] if item]
    if selected.get("name"):
        already_answered.append(f"selected_option:{str(selected.get('name'))[:120]}")
    result = {
        "params": _safe_nested_value(payload.get("params") or {}),
        "selected_option": _safe_nested_value(payload.get("selected_option") or {}),
        "visible_options": _safe_nested_value(payload.get("visible_options") or []),
        "last_options": _safe_nested_value(payload.get("last_options") or []),
        "dialog_window": _safe_dialog_window(state, limit=6),
        "last_bot_question": _safe_nested_value(state.get("last_bot_question") or ""),
        "last_offer_type": _safe_nested_value(state.get("last_offer_type") or ""),
        "last_answer_kind": _safe_nested_value(state.get("last_answer_kind") or ""),
        "recovery_count": int(state.get("recovery_count") or 0),
        "scenario_context": _safe_nested_value(payload.get("scenario_context") or {}),
        "active_conversation_topic": _safe_nested_value(state.get("active_conversation_topic") if isinstance(state.get("active_conversation_topic"), dict) else {}),
        "primary_intent": _derive_canonical_primary_intent(state),
        "scope": scope,
        "pending_followup": _safe_nested_value(pending),
        "conversation_context": {
            "current_turn": _redact_public_dialog_text(user_text, limit=500),
            "answer_goal": answer_goal,
            "selected_option_name": _safe_nested_value(selected.get("name") if selected else ""),
            "current_options_scope": scope,
            "response_mode": response_mode.public() if response_mode else {},
            "financing_facet_active": _has_financing_facet(dialog_plan, user_text),
            "ambiguity": _safe_nested_value(ambiguity),
        },
        "already_asked": _safe_nested_value((state.get("asked_questions") or [])[-6:] if isinstance(state.get("asked_questions"), list) else []),
        "already_answered": _safe_nested_value(already_answered[-6:]),
        "client_context": {
            "primary_intent": _derive_canonical_primary_intent(state),
            "constraints": _safe_nested_value(params),
            "preferences": _safe_nested_value(state.get("preferences") if isinstance(state.get("preferences"), dict) else {}),
        },
        "knowledge": {
            "allowed_facts": ["params", "selected_option", "visible_options", "last_options", "scenario_context", "recent_dialog"],
            "missing_facts": _safe_nested_value(missing_facts[:8] if isinstance(missing_facts, list) else []),
        },
        "capabilities": {
            "can_answer_from_current_context": True,
            "can_run_new_search": False,
            "can_handoff_operator": True,
            "can_request_client_phone": True,
            "can_make_background_promises": False,
        },
        "response_policy": {
            "answer_current_turn_first": True,
            "preserve_selected_option_and_intent": True,
            "do_not_repeat_shortlist_by_default": True,
            "do_not_offer_operator_after_rejection": True,
            "no_external_contacts_or_raw_ids": True,
            "no_new_search_on_current_options_path": True,
            "answer_financing_as_financing_query": True,
            "one_question_max": True,
            "no_unsupported_mortgage_rates_approvals_or_availability": True,
        },
    }
    return result


def _fallback_recovery_answer(recovery_count: int) -> str:
    if recovery_count <= 0:
        return "Кажется, я не совсем поняла запрос. Подскажите, вы ищете квартиру для жизни, для семьи или как инвестицию?"
    return "Я могу сориентировать по району, бюджету или цели покупки — например для семьи, для жизни или под инвестицию. С чего начнём?"


def _one_missing_field_question(state: dict[str, Any]) -> str:
    params = state.get("params") if isinstance(state.get("params"), dict) else {}
    if not any(params.get(key) for key in ("max_price", "max_budget_m", "budget")):
        return "Какой бюджет ориентировочно держим?"
    if not any(params.get(key) for key in ("location", "locations", "district", "districts", "metro", "near_metro")):
        return "В каком районе или у какого метро смотреть?"
    if not any(params.get(key) for key in ("rooms", "room_type")):
        return "Сколько комнат нужно?"
    return "Могу продолжить поиск по текущим условиям. Что важнее уточнить: район, бюджет или комнатность?"


def _recovery_answer(response_text: str, recovery_count: int, state: dict[str, Any] | None = None) -> str:
    answer = _prepare_response_text(response_text).strip()
    state = state or {}
    primary = _derive_canonical_primary_intent(state) if isinstance(state, dict) else "unknown"
    if primary != "unknown" and re.search(r"для\s+(?:жизни|семьи|инвестиц)|цель\s+покуп|как\s+инвестиц", answer, re.I):
        if _has_current_options(state):
            label = {
                "investment": "инвестиционный сценарий",
                "rental": "подбор под сдачу в аренду",
                "family": "покупку для семьи",
                "life": "покупку для жизни",
                "mortgage": "ипотечный сценарий",
            }.get(primary, "вашу задачу")
            answer = f"Помню, что смотрим {label}. Могу продолжить по текущим вариантам: рассказать подробнее о выбранном ЖК, сравнить их или позвать оператора проверить актуальные квартиры. Что удобнее?"
        else:
            answer = "Помню задачу. Уточните, пожалуйста, район, метро, комнатность или бюджет — и я продолжу подбор без смены цели покупки."
    if not answer or len(answer) > 300 or "?" not in answer:
        if primary != "unknown":
            answer = "Помню задачу. Уточните, пожалуйста, что именно продолжить: район, бюджет, комнатность или текущие варианты?"
        else:
            answer = _fallback_recovery_answer(recovery_count)
    previous_bot_question = str(state.get("last_bot_question") or state.get("last_response_text") or "").strip() if isinstance(state, dict) else ""
    generic_recovery = {
        _fallback_recovery_answer(0),
        _fallback_recovery_answer(1),
        "Помню задачу. Уточните, пожалуйста, что именно продолжить: район, бюджет, комнатность или текущие варианты?",
    }
    if recovery_count > 0 and (answer == previous_bot_question or answer in generic_recovery):
        answer = _one_missing_field_question(state if isinstance(state, dict) else {})
    return answer[:300]


def _open_question_fact_available(option: dict[str, Any], fact: str) -> bool:
    aliases = {
        "developer": ("developer", "builder", "застройщик"),
        "metro": ("metro", "property_metro", "near_metro"),
        "finishing": ("finishing",),
        "ready": ("ready", "deadline"),
        "infrastructure": ("infrastructure", "schools", "kindergartens"),
    }
    keys = aliases.get(fact, (fact,))
    return any(option.get(key) not in (None, "", [], {}) for key in keys)


async def _answer_current_options(
    client: OvermindClient,
    *,
    user_text: str,
    state: dict[str, Any],
    decision: TurnDecision,
    dialog_plan: dict[str, Any] | None = None,
    fallback_text: str,
) -> tuple[str, dict[str, Any]]:
    if isinstance(dialog_plan, dict) and dialog_plan.get("open_question"):
        requested = [str(item).strip().casefold() for item in (dialog_plan.get("requested_facts") or []) if str(item).strip()]
        options = state.get("visible_options") or state.get("last_options") or []
        selected = state.get("selected_option") if isinstance(state.get("selected_option"), dict) else None
        cards = [selected] if selected else [item for item in options if isinstance(item, dict)]
        missing = [fact for fact in requested if not any(_open_question_fact_available(card, fact) for card in cards)]
        if missing:
            subject = str(dialog_plan.get("resolved_subject") or missing[0]).replace("_", " ").strip()
            state["pending_followup"] = "selected_live_fact_consent"
            state["contact_flow"] = "normal"
            state["awaiting_phone"] = False
            state["operator_offered"] = True
            state["contact_consent"] = False
            return (
                f"По вопросу о {subject} сейчас нет подтверждённой информации. Точный ответ уточнит оператор.\n\n"
                "В текущих данных это не подтверждено. Оператор сможет проверить. Передать оператору запрос?",
                {"renderer": "open_question_operator_consent", "missing_facts": missing},
            )
    mode = _resolve_current_options_response_mode(state=state, decision=decision, dialog_plan=dialog_plan, user_text=user_text)
    primary = mode.scenario if mode.scenario in SCENARIO_CURRENT_OPTION_INTENTS else _derive_canonical_primary_intent(state)
    if mode.mode == "deterministic" and primary in SCENARIO_CURRENT_OPTION_INTENTS and _has_current_options(state):
        safe_state = _safe_current_options_answer_state(state, user_text=user_text, decision=decision, dialog_plan=dialog_plan, response_mode=mode)
        safe_options = safe_state.get("visible_options") or safe_state.get("last_options") or []
        selected = safe_state.get("selected_option") if isinstance(safe_state.get("selected_option"), dict) else None
        scope = str(safe_state.get("scope") or "all")
        enrichment_meta: dict[str, Any] = {"renderer": "deterministic_current_options", "scenario": primary, "response_mode": mode.public()}
        if scope == "one" and selected and selected.get("name"):
            # A selected ЖК needs the second, full-card query. The initial
            # shortlist may only contain the compact facts used for selection.
            selected_option = next(
                (item for item in safe_options if isinstance(item, dict) and item.get("name") == selected.get("name")),
                selected,
            )
            enriched, selected_meta = await _get_or_fetch_enriched_option(client, state, selected_option, primary)
            selected = enriched if isinstance(enriched, dict) else selected_option
            safe_options = [selected]
            enrichment_meta["enrichment"] = selected_meta
            answer = _render_stage_selected_object(selected, primary)
            if answer:
                return _prepare_response_text(answer), enrichment_meta
        answer = render_current_options_answer(
            safe_options if isinstance(safe_options, list) else [],
            primary,
            selected_option=selected,
            scope=scope,
        )
        if answer:
            return _prepare_response_text(answer), enrichment_meta
    if hasattr(client, "explain_consultation_followup"):
        safe_state = _safe_current_options_answer_state(state, user_text=user_text, decision=decision, dialog_plan=dialog_plan, response_mode=mode)
        answer, chat_meta = await client.explain_consultation_followup(
            user_text=user_text,
            state=safe_state,
            dialog_plan=_safe_nested_value(dialog_plan) if isinstance(dialog_plan, dict) else decision.public(),
            chat_model=str(state.get("chat_model") or CHAT_MODEL),
        )
        meta = chat_meta if isinstance(chat_meta, dict) else {}
        return _prepare_response_text(answer), {**meta, "response_mode": mode.public()}
    return _prepare_response_text(fallback_text or "По текущим вариантам точные условия лучше проверять отдельно. Что именно сравнить по этим ЖК?"), {}


def build_jivo_bot_message(payload: dict[str, Any], text: str) -> dict[str, Any]:
    event = {
        "event": "BOT_MESSAGE",
        "client_id": payload.get("client_id"),
        "chat_id": payload.get("chat_id"),
        "message": {
            "type": "TEXT",
            "text": text,
            "timestamp": _now_ts(),
        },
    }
    try:
        guarded, _result = guard_jivo_event(event)
        return guarded
    except Exception:
        event["message"]["text"] = SAFE_CLIENT_FALLBACK_TEXT if is_client_production() else text
        return event


def build_jivo_invite_agent(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": "INVITE_AGENT",
        "client_id": payload.get("client_id"),
        "chat_id": payload.get("chat_id"),
    }


def _is_start_command(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {"/start", "start", "/start_0", "/start_1", "/start_2", "/start_3", "/start_4", "/start_5", "/start_6"}


def _start_command_version(text: str) -> str | None:
    normalized = str(text or "").strip().lower()
    return {"/start_0": "V0", "/start_1": "V1", "/start_2": "V2", "/start_3": "V3", "/start_4": "V4", "/start_5": "V5", "/start_6": "V6"}.get(normalized)


async def run_chat(app: web.Application, *, user_id: str, message: str, channel: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return await run_runtime_turn(app, user_id=user_id, message=message, channel=channel, meta=meta)


async def _active_runtime_version(app: web.Application) -> str:
    return await app["runtime_version_store"].get()


def _supported_runtime_version_or_none(value: Any) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in SUPPORTED_RUNTIME_VERSIONS else None


async def _effective_session_runtime_version(app: web.Application, session_key: str) -> str:
    state = await app["state_store"].get(session_key)
    override = _supported_runtime_version_or_none(state.get("runtime_version_override")) if isinstance(state, dict) else None
    return override or await _active_runtime_version(app)


async def _reset_state_for_active_runtime(app: web.Application, user_id: str) -> str:
    version = await _active_runtime_version(app)
    store: JsonStateStore = app["state_store"]
    existing = await store.get(user_id)
    next_state = _reset_active_namespace_envelope(existing, version)
    next_state.pop("runtime_version_override", None)
    await store.save(user_id, next_state)
    return version


async def _reset_state_for_session_runtime(app: web.Application, user_id: str, version: str) -> str:
    normalized = _normalize_runtime_version(version)
    store: JsonStateStore = app["state_store"]
    existing = await store.get(user_id)
    next_state = _reset_active_namespace_envelope(existing, normalized)
    next_state["runtime_version_override"] = normalized
    await store.save(user_id, next_state)
    return normalized


async def _run_chat_v1(app: web.Application, *, user_id: str, message: str, channel: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    store: JsonStateStore = app["state_store"]
    existing_client = app.get("overmind_client") if hasattr(app, "get") else app["overmind_client"]
    legacy_client = None
    if not hasattr(existing_client, "ask"):
        legacy_client = app.get("legacy_overmind_client") if hasattr(app, "get") else None
        if legacy_client is None:
            legacy_client = _legacy_func("OvermindClient")()
            app["legacy_overmind_client"] = legacy_client
    state = await store.get(user_id)
    text = str(message or "").strip()
    if not text:
        return {"ok": False, "error": "empty_message", "answer": "Напишите, что хотите посмотреть, и я подхвачу."}

    _append_dialog_turn(state, "user", text)
    outbox: LocalCallbackOutbox = app["crm_callback_outbox"]
    event_id = _meta_event_id(meta)
    phone = _extract_phone_from_text(text)
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    profile_or_state_name = _prefill_contact_name_from_meta(state, meta)
    contact_flow = str(state.get("contact_flow") or "normal")

    def enqueue_confirmed_callback(*, contact_name: str, normalized_phone: str) -> CallbackOutboxResult:
        lead_context = build_callback_lead_context(state, channel=channel, meta=meta or {})
        return outbox.enqueue_callback(
            session_key=user_id,
            event_id=event_id,
            contact_name=contact_name,
            normalized_phone=normalized_phone,
            context=lead_context,
            summary_input=_safe_summary_input_from_state(state, channel=channel, meta=meta),
        )

    if phone:
        _scrub_latest_user_phone_turn(state)
        state["last_phone_meta"] = {"digits_len": len(digits), "captured": True}
        state["contact_phone_draft_meta"] = {"digits_len": len(digits), "captured": True}
        name = _safe_contact_name(state.get("contact_name")) or profile_or_state_name
        if not name:
            outbox.save_contact_draft(session_key=user_id, normalized_phone=phone, event_id=event_id)
            state["contact_flow"] = "awaiting_contact_name"
            state["awaiting_phone"] = False
            answer = _ask_contact_name()
            _remember_bot_response(state, answer, offer_type="awaiting_contact_name", answer_kind="callback_collect_name")
            await store.save(user_id, state)
            return _public_callback_result(answer=answer, intent="collect_contact_name", awaiting_phone=False)
        outbox_result = enqueue_confirmed_callback(contact_name=name, normalized_phone=phone)
        state["last_callback_ref"] = outbox_result.lead_ref
        _reset_contact_flow(state)
        outbox.clear_contact_draft(session_key=user_id)
        answer = _callback_confirmation(name)
        _remember_bot_response(state, answer, offer_type="phone_captured", answer_kind="callback_queued")
        await store.save(user_id, state)
        result = _public_callback_result(answer=answer, crm_callback=outbox_result.public(), awaiting_phone=False)
        result["selected_option"] = (state.get("selected_option") or {}).get("name") if isinstance(state.get("selected_option"), dict) else None
        return result

    if contact_flow == "awaiting_contact_name":
        name = _safe_contact_name(text)
        draft_phone = outbox.load_contact_draft_phone(session_key=user_id)
        if name and draft_phone:
            state["contact_name"] = name
            state["contact_name_source"] = "client_message"
            outbox_result = enqueue_confirmed_callback(contact_name=name, normalized_phone=draft_phone)
            state["last_callback_ref"] = outbox_result.lead_ref
            _reset_contact_flow(state)
            outbox.clear_contact_draft(session_key=user_id)
            answer = _callback_confirmation(name)
            _remember_bot_response(state, answer, offer_type="callback_confirmed", answer_kind="callback_queued")
            await store.save(user_id, state)
            return _public_callback_result(answer=answer, crm_callback=outbox_result.public(), awaiting_phone=False)
        if name:
            state["contact_name"] = name
            state["contact_name_source"] = "client_message"
            state["contact_flow"] = "awaiting_contact_phone"
            state["awaiting_phone"] = True
            answer = _ask_contact_phone(name)
            _remember_bot_response(state, answer, offer_type="awaiting_contact_phone", answer_kind="callback_collect_phone")
            await store.save(user_id, state)
            return _public_callback_result(answer=answer, intent="collect_contact_phone", awaiting_phone=True)
        answer = "Не уверена, что правильно поняла имя. Напишите, пожалуйста, как к вам обращаться — например, Иван."
        _remember_bot_response(state, answer, offer_type="awaiting_contact_name", answer_kind="callback_collect_name")
        await store.save(user_id, state)
        return _public_callback_result(answer=answer, intent="collect_contact_name", awaiting_phone=False)

    if contact_flow == "awaiting_contact_phone":
        answer = _ask_contact_phone(profile_or_state_name)
        _remember_bot_response(state, answer, offer_type="awaiting_contact_phone", answer_kind="callback_collect_phone")
        await store.save(user_id, state)
        return _public_callback_result(answer=answer, intent="collect_contact_phone", awaiting_phone=True)

    if _is_explicit_operator_request(text) and not _is_operator_explanation_question(text):
        name = profile_or_state_name
        if name:
            state["contact_flow"] = "awaiting_contact_phone"
            state["awaiting_phone"] = True
            answer = _ask_contact_phone(name)
            intent = "collect_contact_phone"
        else:
            state["contact_flow"] = "awaiting_contact_name"
            state["awaiting_phone"] = False
            answer = _ask_contact_name()
            intent = "collect_contact_name"
        _remember_bot_response(state, answer, offer_type="operator_contact_request", answer_kind="callback_collect_contact")
        await store.save(user_id, state)
        return _public_callback_result(answer=answer, intent=intent, awaiting_phone=bool(state.get("awaiting_phone")))

    client: OvermindClient = legacy_client or existing_client
    try:
        _resolve_exact_full_current_option_prefill(state, text)
        _persist_primary_intent(state, _derive_canonical_primary_intent(state))
        try:
            pre_decision, planner_plan = await _plan_jivo_turn(client, text=text, state=state)
            accepted_plan_for_state = _accepted_canonical_plan(planner_plan, state, pre_decision) if isinstance(planner_plan, dict) else False
            if accepted_plan_for_state:
                _apply_explicit_primary_intent_switch(state, planner_plan)
                if pre_decision.action == "answer_current_options":
                    _apply_planner_selection(state, planner_plan)
            resolved_option_this_turn = _selected_option_resolved_this_turn(state)
            if not resolved_option_this_turn and pre_decision.action == "recover_dialogue":
                resolved_option_this_turn = _resolve_conservative_current_option_fallback(state, text)
            if resolved_option_this_turn and pre_decision.action == "recover_dialogue":
                pre_decision = TurnDecision(action="answer_current_options", target="current_options", search_policy="forbidden")
                forced_scope = _planner_scope(planner_plan)
                planner_plan = {**planner_plan, "forced_current_option_reference": True, "scope": "one" if forced_scope == "unknown" else forced_scope}
                accepted_plan_for_state = False
            if accepted_plan_for_state:
                _persist_primary_intent(state, _derive_canonical_primary_intent(state, planner_plan=planner_plan))
            else:
                _persist_primary_intent(state, _derive_canonical_primary_intent(state))
            state["last_planner_diagnostics"] = _planner_diagnostics(planner_plan)
            _append_safe_planner_trace(session_key=user_id, channel=channel, plan=planner_plan, decision=pre_decision, user_text=text)
        except Exception as planner_exc:
            pre_decision = TurnDecision(action="recover_dialogue", target="none", search_policy="forbidden")
            planner_plan = {"fallback_used": True}
            state["last_planner_diagnostics"] = _planner_diagnostics(planner_plan)
            _append_safe_planner_trace(
                session_key=user_id,
                channel=channel,
                plan=planner_plan,
                decision=pre_decision,
                exception_code=type(planner_exc).__name__,
                user_text=text,
            )
        if pre_decision.search_policy == "forbidden":
            if pre_decision.action == "operator_contact":
                name = _safe_contact_name(state.get("contact_name")) or profile_or_state_name
                if name:
                    state["contact_flow"] = "awaiting_contact_phone"
                    state["awaiting_phone"] = True
                    answer = _ask_contact_phone(name)
                    intent = "collect_contact_phone"
                else:
                    state["contact_flow"] = "awaiting_contact_name"
                    state["awaiting_phone"] = False
                    answer = _ask_contact_name()
                    intent = "collect_contact_name"
                _remember_bot_response(state, answer, offer_type="awaiting_contact", answer_kind="callback_collect_contact")
                await store.save(user_id, state)
                return {
                    "ok": True,
                    "answer": answer,
                    "intent": intent,
                    "awaiting_phone": bool(state.get("awaiting_phone")),
                    "handoff_to_operator": False,
                    "selected_option": (state.get("selected_option") or {}).get("name") if isinstance(state.get("selected_option"), dict) else None,
                    "buttons": [],
                    "turn_decision": pre_decision.public(),
                    "meta": {"channel": channel, "planner": _safe_planner_public(planner_plan)},
                }
            if pre_decision.action == "recover_dialogue":
                recovery_count = int(state.get("recovery_count") or 0)
                answer = _recovery_answer(pre_decision.response, recovery_count, state)
                state["recovery_count"] = recovery_count + 1
                _remember_bot_response(state, answer, offer_type="dialogue_recovery", answer_kind="recover_dialogue")
                await store.save(user_id, state)
                return {
                    "ok": True,
                    "answer": answer,
                    "intent": "recover_dialogue",
                    "awaiting_phone": False,
                    "handoff_to_operator": False,
                    "selected_option": (state.get("selected_option") or {}).get("name") if isinstance(state.get("selected_option"), dict) else None,
                    "buttons": [],
                    "turn_decision": pre_decision.public(),
                    "meta": {"channel": channel, "planner": _safe_planner_public(planner_plan), "recovery_count": state["recovery_count"]},
                }
            if pre_decision.action == "off_topic":
                answer = "Я тут лучше не буду уходить в сторону: моя зона — подбор квартир и новостроек.\n\nВернёмся к подбору квартиры?"
                state["recovery_count"] = 0
                if isinstance(state.get("pending_followup"), dict):
                    state.pop("pending_followup", None)
                _remember_bot_response(state, answer, offer_type="off_topic", answer_kind="off_topic")
                await store.save(user_id, state)
                return {
                    "ok": True,
                    "answer": answer,
                    "intent": "off_topic",
                    "awaiting_phone": False,
                    "handoff_to_operator": False,
                    "selected_option": (state.get("selected_option") or {}).get("name") if isinstance(state.get("selected_option"), dict) else None,
                    "buttons": [],
                    "turn_decision": pre_decision.public(),
                    "meta": {"channel": channel, "planner": _safe_planner_public(planner_plan)},
                }
            if pre_decision.action == "answer_current_options":
                scope = _planner_scope(planner_plan)
                state["current_options_scope"] = scope
                if scope == "all":
                    state.pop("selected_option", None)
                _apply_planner_selection(state, planner_plan)
                previous_visible = copy.deepcopy(state.get("visible_options") or [])
                previous_last = copy.deepcopy(state.get("last_options") or [])
                answer, chat_meta = await _answer_current_options(
                    client,
                    user_text=text,
                    state=state,
                    decision=pre_decision,
                    dialog_plan=planner_plan,
                    fallback_text="",
                )
                state["visible_options"] = previous_visible
                state["last_options"] = previous_last
                state["recovery_count"] = 0
                _remember_bot_response(state, answer, offer_type="current_options_answer", answer_kind="answer_current_options")
                await store.save(user_id, state)
                return {
                    "ok": True,
                    "answer": answer,
                    "intent": "answer_current_options",
                    "awaiting_phone": False,
                    "handoff_to_operator": False,
                    "selected_option": (state.get("selected_option") or {}).get("name") if isinstance(state.get("selected_option"), dict) else None,
                    "buttons": [],
                    "turn_decision": pre_decision.public(),
                    "meta": {"channel": channel, "planner": _safe_planner_public(planner_plan), "chat_meta": _safe_nested_value(chat_meta)},
                }

        ask_params = _params_with_canonical_search_constraints(state.get("params") or {}, planner_plan, pre_decision, state)
        ask_hard_constraints = _search_hard_constraints_for_ask(plan=planner_plan, decision=pre_decision, ask_params=ask_params)
        selected_search_profile = select_search_profile(planner_plan) if ROUTER_PROFILES_ENABLED else None
        ask_search_profile = selected_search_profile.public() if selected_search_profile else None
        if ask_hard_constraints:
            state["last_search_hard_constraints"] = _safe_nested_value(ask_hard_constraints)
        else:
            state.pop("last_search_hard_constraints", None)
        response_text, new_params, search_meta, chat_meta = await client.ask(
            text,
            search_model=str(state.get("search_model") or SEARCH_MODEL),
            chat_model=str(state.get("chat_model") or CHAT_MODEL),
            use_mcp=bool(state.get("mcp", True)),
            params=ask_params,
            dialog_context=_safe_turn_context(text, state),
            hard_constraints=ask_hard_constraints,
            search_profile=ask_search_profile,
        )
        if ask_params != (state.get("params") or {}) and _has_canonical_plan(planner_plan):
            state["params"] = {**ask_params, **(new_params if isinstance(new_params, dict) else {})}
        else:
            state["params"] = new_params or state.get("params") or {}
        _persist_primary_intent(
            state,
            _derive_canonical_primary_intent(
                state,
                planner_plan=planner_plan,
                search_params=state.get("params") if isinstance(state.get("params"), dict) else {},
            ),
        )
        decision = TurnDecision.from_meta(search_meta or {})
        if decision.action == "operator_contact":
            name = _safe_contact_name(state.get("contact_name")) or profile_or_state_name
            if name:
                state["contact_flow"] = "awaiting_contact_phone"
                state["awaiting_phone"] = True
                answer = _ask_contact_phone(name)
                intent = "collect_contact_phone"
            else:
                state["contact_flow"] = "awaiting_contact_name"
                state["awaiting_phone"] = False
                answer = _ask_contact_name()
                intent = "collect_contact_name"
            _remember_bot_response(state, answer, offer_type="awaiting_contact", answer_kind="callback_collect_contact")
            await store.save(user_id, state)
            return {
                "ok": True,
                "answer": answer,
                "intent": intent,
                "awaiting_phone": bool(state.get("awaiting_phone")),
                "handoff_to_operator": False,
                "selected_option": (state.get("selected_option") or {}).get("name") if isinstance(state.get("selected_option"), dict) else None,
                "buttons": [],
                "meta": {"channel": channel},
            }
        if decision.action == "recover_dialogue":
            recovery_count = int(state.get("recovery_count") or 0)
            answer = _recovery_answer(decision.response or response_text, recovery_count, state)
            state["recovery_count"] = recovery_count + 1
            _remember_bot_response(state, answer, offer_type="dialogue_recovery", answer_kind="recover_dialogue")
            await store.save(user_id, state)
            return {
                "ok": True,
                "answer": answer,
                "intent": "recover_dialogue",
                "awaiting_phone": False,
                "handoff_to_operator": False,
                "selected_option": (state.get("selected_option") or {}).get("name") if isinstance(state.get("selected_option"), dict) else None,
                "buttons": [],
                "turn_decision": decision.public(),
                "meta": {"channel": channel, "recovery_count": state["recovery_count"]},
            }
        if decision.action == "answer_current_options":
            scope = _planner_scope(planner_plan)
            state["current_options_scope"] = scope
            if scope == "all":
                state.pop("selected_option", None)
            previous_visible = copy.deepcopy(state.get("visible_options") or [])
            previous_last = copy.deepcopy(state.get("last_options") or [])
            answer, chat_meta = await _answer_current_options(
                client,
                user_text=text,
                state=state,
                decision=decision,
                dialog_plan=planner_plan,
                fallback_text=response_text,
            )
            state["visible_options"] = previous_visible
            state["last_options"] = previous_last
            state["recovery_count"] = 0
            _remember_bot_response(state, answer, offer_type="current_options_answer", answer_kind="answer_current_options")
            await store.save(user_id, state)
            return {
                "ok": True,
                "answer": answer,
                "intent": "answer_current_options",
                "awaiting_phone": False,
                "handoff_to_operator": False,
                "selected_option": (state.get("selected_option") or {}).get("name") if isinstance(state.get("selected_option"), dict) else None,
                "buttons": [],
                "turn_decision": decision.public(),
                "meta": {"channel": channel, "chat_meta": _safe_nested_value(chat_meta)},
            }
        state["recovery_count"] = 0
        _refresh_search_state(state, search_meta or {})
        state["last_search_snapshot"] = _safe_search_snapshot_from_meta(search_meta or {})
        options = _visible_options_from_chat_or_response(chat_meta or {}, response_text, state.get("last_options") or [])
        if options:
            state["visible_options"] = options
            _remember_shown_options(state, options)
            state["pending_followup"] = {"type": "visible_options", "count": len(options)}
        answer = _prepare_response_text(response_text)
        _remember_bot_response(state, answer, offer_type="api_chat", answer_kind="main_search")
        await store.save(user_id, state)
        return {
            "ok": True,
            "answer": answer,
            "intent": "main_search",
            "awaiting_phone": bool(state.get("awaiting_phone")),
            "handoff_to_operator": False,
            "selected_option": (state.get("selected_option") or {}).get("name") if isinstance(state.get("selected_option"), dict) else None,
            "buttons": [],
            "meta": {"channel": channel, "planner": _safe_planner_public(planner_plan)},
        }
    except Exception as exc:
        answer = SAFE_UPSTREAM_ERROR_TEXT
        _remember_bot_response(state, answer, offer_type="api_error", answer_kind="safe_upstream_fallback")
        await store.save(user_id, state)
        return {
            "ok": False,
            "error": "upstream_error",
            "error_type": type(exc).__name__,
            "answer": answer,
            "intent": "safe_upstream_fallback",
            "awaiting_phone": False,
            "handoff_to_operator": False,
            "selected_option": None,
            "buttons": [],
        }


async def handle_health(request: web.Request) -> web.Response:
    return _json_response({
        "ok": True,
        "service": "nmbot-api",
        "jivo_token_configured": bool(os.getenv("JIVO_PROVIDER_TOKEN", "").strip()),
        "api_token_configured": bool(os.getenv("NMBOT_API_TOKEN", "").strip()),
    })


async def handle_api_chat(request: web.Request) -> web.Response:
    if not _api_token_ok(request):
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    payload = await request.json()
    user_id = str(payload.get("user_id") or "api:anonymous")
    message = str(payload.get("message") or "")
    if _is_start_command(message):
        requested = _start_command_version(message)
        version = await _reset_state_for_session_runtime(request.app, user_id, requested) if requested else await _reset_state_for_active_runtime(request.app, user_id)
        return _json_response({
            "ok": True,
            "answer": _client_visible_start_greeting(version),
            "meta": {"runtime": version.casefold(), "answer_kind": "start_reset"},
        })
    result = await run_chat(
        request.app,
        user_id=user_id,
        message=message,
        channel=str(payload.get("channel") or "api"),
        meta=payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
    )
    _log_runtime_failure(result, route="api_chat")
    return _json_response(result, status=200 if result.get("ok") else 502 if result.get("error") == "upstream_error" else 400)


async def handle_api_reset(request: web.Request) -> web.Response:
    if not _api_token_ok(request):
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    payload = await request.json()
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        return _json_response({"ok": False, "error": "missing_user_id"}, status=400)
    version = await _reset_state_for_active_runtime(request.app, user_id)
    return _json_response({"ok": True, "user_id": user_id, "runtime_version": version})


async def handle_api_runtime_version(request: web.Request) -> web.Response:
    if not _api_token_ok(request):
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    return _json_response({"ok": True, "runtime_version": await request.app["runtime_version_store"].get()})


async def handle_api_runtime_version_set(request: web.Request) -> web.Response:
    if not _api_token_ok(request):
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    payload = await request.json()
    raw_version = str(payload.get("runtime_version") or payload.get("version") or "").strip().upper() if isinstance(payload, dict) else ""
    if raw_version not in SUPPORTED_RUNTIME_VERSIONS:
        return _json_response({"ok": False, "error": "unsupported_runtime_version"}, status=400)
    store: RuntimeVersionStore = request.app["runtime_version_store"]
    previous = await store.get()
    current = await store.set(raw_version)
    return _json_response({"ok": True, "runtime_version": current, "previous_runtime_version": previous})


async def handle_jivo(request: web.Request) -> web.Response:
    configured = os.getenv("JIVO_PROVIDER_TOKEN", "").strip()
    token = request.match_info.get("provider_token", "")
    if not configured:
        return _json_response({"ok": False, "error": "jivo_token_not_configured"}, status=503)
    if not hmac.compare_digest(token, configured):
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    request_headers = getattr(request, "headers", {})
    trace_ref = _safe_bridge_trace_ref(request_headers.get("X-NMBOT-Trace-ID") if hasattr(request_headers, "get") else None)

    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return _json_response({"ok": False, "error": "invalid_json"}, status=400)
    if not isinstance(payload, dict):
        return _json_response({"ok": False, "error": "invalid_payload"}, status=400)
    event = str(payload.get("event") or "")
    if event == "CLIENT_MESSAGE":
        payload = _normalize_jivo_payload(payload)
        if payload is None:
            return _json_response({"ok": False, "error": "missing_message_text"}, status=400)
        try:
            response_payload, status = await process_jivo_client_message(request.app, payload, trace_ref=trace_ref)
        except Exception as exc:
            # Jivo's bridge requires a JSON event even when the internal runtime
            # fails. Keep diagnostics free of request text, IDs and tokens.
            _log_error_event({
                "error_type": "jivo_handler_exception",
                "stage": "process_client_message",
                **({"trace_ref": trace_ref} if trace_ref else {}),
                "exception_type": type(exc).__name__,
                "stack": [
                    {
                        "file": Path(frame.filename).name,
                        "line": frame.lineno,
                        "function": frame.name,
                    }
                    for frame in traceback.extract_tb(exc.__traceback__)[-8:]
                ],
            })
            try:
                append_journal_event(
                    session_key=_jivo_session_key(payload),
                    role="bot",
                    text=SAFE_UPSTREAM_ERROR_TEXT,
                    event_type="turn",
                    event_id=_jivo_event_id(payload),
                    meta={**_jivo_meta(payload), **({"trace_ref": trace_ref} if trace_ref else {})},
                    answer_kind="safe_upstream_fallback",
                    error_summary={
                        "status": "failed",
                        "codes": ["jivo_handler_exception"],
                        "stages": ["jivo_handler"],
                        "fallback": True,
                    },
                    runtime_version="V6",
                    release_id=current_release_id(),
                )
            except Exception:
                # Error journaling must never prevent Jivo from receiving its safe fallback.
                pass
            response_payload = build_jivo_bot_message(payload, SAFE_UPSTREAM_ERROR_TEXT)
            status = 200
        return _json_response(response_payload, status=status)
    if event == "AGENT_UNAVAILABLE":
        return _json_response(build_jivo_bot_message(
            payload,
            "Сейчас специалист не на связи. Оставьте, пожалуйста, номер телефона — как только сможет, с вами свяжутся.",
        ))
    if event == "CHAT_CLOSED":
        return _json_response({"ok": True, "event": "CHAT_CLOSED"})
    return _json_response({"ok": True, "ignored_event": event})


def _normalize_jivo_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize Jivo text input and reject empty messages before run_chat.

    Canonical Jivo shape is ``message.text``.  The legacy ``payload.text``
    shape is accepted as a compatibility guard so integration mistakes do not
    become an empty user turn or an opaque upstream fallback.
    """
    message = payload.get("message")
    if isinstance(message, dict):
        text = str(message.get("text") or "").strip()
        if text:
            normalized = dict(payload)
            normalized["message"] = {**message, "text": text}
            return normalized

    legacy = payload.get("payload")
    if isinstance(legacy, dict):
        text = str(legacy.get("text") or "").strip()
        if text:
            normalized = dict(payload)
            normalized["message"] = {
                "type": str(legacy.get("type") or "TEXT").upper(),
                "text": text,
            }
            return normalized
    return None


async def process_jivo_client_message(app: web.Application, payload: dict[str, Any], trace_ref: str | None = None) -> tuple[dict[str, Any], int]:
    session_key = _jivo_session_key(payload)
    event_id = _jivo_event_id(payload)
    locks: SessionLockRegistry = app["jivo_session_locks"]
    dedup: JivoDedupCache = app["jivo_dedup_cache"]
    async with locks.hold(session_key):
        if event_id:
            cached = dedup.get(session_key, event_id)
            if cached is not None:
                return cached
        response_payload, status = await _process_jivo_client_message_uncached(app, payload, session_key, trace_ref=trace_ref)
        if event_id:
            dedup.set(session_key, event_id, response_payload, status)
        return response_payload, status


def _mark_v6_bot_message_returned(result: dict[str, Any]) -> None:
    """Record API return preparation, never an external Jivo delivery receipt."""
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else None
    if not isinstance(meta, dict) or meta.get("runtime") != "v6":
        return
    trace = meta.get("v6_trace")
    stages = trace.get("stages") if isinstance(trace, dict) else None
    if not isinstance(stages, list) or len(stages) != 5:
        return
    bot_stage = stages[4]
    if isinstance(bot_stage, dict) and bot_stage.get("stage") == "bot_message" and bot_stage.get("status") == "prepared":
        bot_stage["status"] = "returned"


def _journal_v6_trace(result: dict[str, Any]) -> dict[str, Any] | None:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else None
    if not isinstance(meta, dict) or meta.get("runtime") != "v6":
        return None
    trace = meta.get("v6_trace")
    return trace if isinstance(trace, dict) else None


async def _process_jivo_client_message_uncached(
    app: web.Application,
    payload: dict[str, Any],
    session_key: str,
    trace_ref: str | None = None,
) -> tuple[dict[str, Any], int]:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    event_id = _jivo_event_id(payload)
    safe_trace_ref = _validated_trace_ref(trace_ref)
    journal_meta = {**_jivo_meta(payload), **({"trace_ref": safe_trace_ref} if safe_trace_ref else {})}
    runtime_version = await _effective_session_runtime_version(app, session_key)
    release_id = current_release_id()
    if str(message.get("type") or "TEXT").upper() != "TEXT":
        answer = "Пока отвечаю только на обычный текст. Напишите словами, и я продолжу."
        append_journal_event(session_key=session_key, role="user", event_type="non_text", event_id=event_id, meta=journal_meta, runtime_version=runtime_version, release_id=release_id)
        append_journal_event(session_key=session_key, role="bot", text=answer, event_type="turn", event_id=event_id, meta=journal_meta, answer_kind="non_text", error_summary=_journal_error_summary({}), runtime_version=runtime_version, release_id=release_id)
        return build_jivo_bot_message(payload, answer), 200
    text = str(message.get("text") or "")
    if _is_start_command(text):
        command_version = _start_command_version(text)
        append_journal_event(session_key=session_key, role="user", text=text, event_type="turn", event_id=event_id, meta=journal_meta, runtime_version=runtime_version, release_id=release_id)
        version = (
            await _reset_state_for_session_runtime(app, session_key, command_version)
            if command_version
            else await _reset_state_for_active_runtime(app, session_key)
        )
        answer = _client_visible_start_greeting(version)
        append_journal_event(session_key=session_key, role="bot", text=answer, event_type="lifecycle", event_id=event_id, meta=journal_meta, answer_kind="start_reset", error_summary=_journal_error_summary({}), runtime_version=version, release_id=release_id)
        return build_jivo_bot_message(
            payload,
            answer,
        ), 200
    append_journal_event(session_key=session_key, role="user", text=text, event_type="turn", event_id=event_id, meta=journal_meta, runtime_version=runtime_version, release_id=release_id)
    result = await run_chat(
        app,
        user_id=session_key,
        message=text,
        channel="jivo",
        meta=journal_meta,
    )
    if safe_trace_ref and isinstance(result, dict):
        result_meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        result["meta"] = {**result_meta, "trace_ref": safe_trace_ref}
    _log_runtime_failure(result, route="jivo")
    _log_search_validation_report(result, runtime_version=runtime_version)
    if result.get("handoff_to_operator") and payload.get("agents_online") is not False:
        append_journal_event(session_key=session_key, role="bot", event_type="handoff", event_id=event_id, meta=journal_meta, answer_kind="invite_agent", runtime_version=runtime_version, release_id=release_id)
        return build_jivo_invite_agent(payload), 200
    answer = _client_visible_runtime_answer(result)
    response_event = build_jivo_bot_message(payload, answer)
    _mark_v6_bot_message_returned(result)
    append_journal_event(
        session_key=session_key,
        role="bot",
        text=answer,
        event_type="turn",
        event_id=event_id,
        meta=journal_meta,
        answer_kind=result.get("intent"),
        offer_type=(result.get("meta") or {}).get("offer_type") if isinstance(result.get("meta"), dict) else None,
        response_composer=_journal_response_composer(result),
        prompt_provenance=_journal_prompt_provenance(result),
        execution_path=_journal_execution_path(result, jivo_prepare=True),
        response_model=_journal_response_model(result),
        error_summary=_journal_error_summary(result),
        runtime_summary=_journal_runtime_summary(result),
        v6_candidate=_journal_v6_candidate(result),
        v6_trace=_journal_v6_trace(result),
        runtime_version=runtime_version,
        release_id=release_id,
    )
    return response_event, 200


def _client_visible_runtime_answer(result: dict[str, Any]) -> str:
    if isinstance(result, dict):
        client_answer = str(result.get("client_answer") or "").strip()
        if client_answer:
            return client_answer
        answer = str(result.get("answer") or "").strip()
        if answer:
            return answer
    return SAFE_UPSTREAM_ERROR_TEXT


def _log_v2_runtime_failure(result: dict[str, Any]) -> None:
    _log_runtime_failure(result, runtime_filter="v2")


def _log_runtime_failure(result: dict[str, Any], *, runtime_filter: str | None = None, route: str | None = None) -> None:
    v6_failure_codes = frozenset({
        "invalid_input", "mode_off", "missing_v6_ports", "missing_state_store",
        "invalid_v6_state", "shadow_phone_bypass", "missing_callback_outbox",
        "callback_enqueue_failed", "callback_not_queued", "phone_dependency_unavailable",
        "unexpected_phone_bypass", "v6_runtime_failed", "shadow_only", "state_save_failed",
        "provider_failure", "mcp_contract_violation",
    })

    v6_failure_stages = frozenset({
        "runtime_execution", "classifier", "canonical_search", "composer", "publication",
    })

    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    trace_ref = _validated_trace_ref(meta.get("trace_ref"))
    runtime = str(meta.get("runtime") or "").strip().lower()
    if runtime_filter is not None and runtime != runtime_filter:
        return
    trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
    if runtime == "v0":
        _log_v0_runtime_failure(result, trace)
        return
    if runtime == "v6":
        failure = trace.get("v6_failure") if isinstance(trace.get("v6_failure"), dict) else {}
        if not failure:
            composer = trace.get("response_composer") if isinstance(trace.get("response_composer"), dict) else {}
            failure = composer.get("v6_failure") if isinstance(composer.get("v6_failure"), dict) else {}
        if not failure and result.get("ok") is False:
            raw_code = meta.get("failure_code") or result.get("error_type")
            if raw_code:
                code = str(raw_code).strip().lower()
                code = code if code in v6_failure_codes else "unknown"
                failure = {"stage": "runtime_execution", "code": code, "call_count": 0}
        if failure:
            raw_stage = str(failure.get("stage") or "").strip().lower()
            stage = raw_stage if raw_stage in v6_failure_stages else "runtime_execution"
            raw_code = str(failure.get("code") or "").strip().lower()
            code = raw_code if raw_code in v6_failure_codes else "unknown"
            try:
                call_count = max(0, min(int(failure.get("call_count") or 0), 2))
            except (TypeError, ValueError, OverflowError):
                call_count = 0
            _log_error_event({
                "error_type": "v6_runtime_failure",
                "stage": stage,
                "error_code": code,
                "call_count": call_count,
                **({"route": route} if route in {"api_chat", "jivo"} else {}),
                **({"trace_ref": trace_ref} if trace_ref else {}),
            })
        return
    if result.get("ok") is not False:
        return
    if runtime != "v2":
        return
    timing = trace.get("timing_ms") if isinstance(trace.get("timing_ms"), dict) else {}

    event = {
        "error_type": "v2_runtime_failure",
        "stage": "runtime_execution",
        **({"trace_ref": trace_ref} if trace_ref else {}),
        "error_code": _safe_report_code(trace.get("error_code") or result.get("error_type")),
        "runtime_stage": _safe_report_code(trace.get("stage")),
        "action": _safe_report_code(trace.get("action")),
        "timing_ms": {
            key: int(value)
            for key, value in timing.items()
            if key in {"planner", "execution", "response", "total"} and isinstance(value, (int, float))
        },
    }
    _log_error_event(event)


def _safe_report_code(value: Any, *, max_len: int = 120) -> str | None:
    raw_code = str(value or "").split(":", 1)[0].strip()
    code = "validation_error" if re.search(r"\s", raw_code) else raw_code
    normalized = re.sub(r"[^a-zA-Z0-9_.-]", "_", code.encode("ascii", "ignore").decode("ascii"))[:max_len].strip("_")
    return normalized or None


def _safe_report_codes(values: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for item in values:
        code = _safe_report_code(item)
        if code and code not in out:
            out.append(code)
        if len(out) >= limit:
            break
    return out


def _safe_report_counts(value: Any) -> dict[str, int]:
    counts = value if isinstance(value, dict) else {}
    out: dict[str, int] = {}
    for key in ("facts", "near", "missing", "errors", "warnings"):
        raw = counts.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            out[key] = max(0, min(int(raw), 1000))
    return out


def _log_search_validation_report(result: dict[str, Any], *, runtime_version: str | None = None) -> None:
    if result.get("ok") is not True:
        return
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    trace_ref = _validated_trace_ref(meta.get("trace_ref"))
    runtime = str(meta.get("runtime") or runtime_version or "").strip().lower()
    if runtime not in {"v0", "v2", "v3"}:
        return
    trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
    report = trace.get("search_validation") if isinstance(trace.get("search_validation"), dict) else {}
    errors = _safe_report_codes(report.get("errors"), limit=12)
    warnings = _safe_report_codes(report.get("warnings"), limit=12)
    if not errors and not warnings:
        legacy_errors = trace.get("validation_errors") if isinstance(trace.get("validation_errors"), list) else []
        errors = _safe_report_codes(legacy_errors, limit=12)
    if not errors and not warnings:
        return
    counts = _safe_report_counts(report.get("counts"))
    counts.setdefault("errors", len(errors))
    counts.setdefault("warnings", len(warnings))
    _log_error_event({
        "error_type": "search_validation_report",
        "stage": "search_validation",
        **({"trace_ref": trace_ref} if trace_ref else {}),
        "runtime": runtime,
        "status": _safe_report_code(report.get("status"), max_len=40) or ("invalid" if errors else "degraded"),
        "errors": errors,
        "warnings": warnings,
        "counts": counts,
    })


def _log_v0_runtime_failure(result: dict[str, Any], trace: dict[str, Any]) -> None:
    def safe_code(value: Any, *, max_len: int = 120) -> str | None:
        normalized = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(value or "").strip())[:max_len].strip("_")
        return normalized or None

    raw_counts = trace.get("call_counts") if isinstance(trace.get("call_counts"), dict) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    trace_ref = _validated_trace_ref(meta.get("trace_ref"))
    call_counts = {
        key: int(value)
        for key, value in raw_counts.items()
        if key in {"scenario_search", "answer"} and isinstance(value, (int, float)) and 0 <= int(value) <= 20
    }
    raw_errors = trace.get("validation_errors") if isinstance(trace.get("validation_errors"), list) else []
    validation_errors: list[str] = []
    for item in raw_errors[:12]:
        raw_code = str(item or "").split(":", 1)[0].strip()
        code = "validation_error" if re.search(r"\s", raw_code) else raw_code
        safe = safe_code(code)
        if safe:
            validation_errors.append(safe)
    event = {
        "error_type": "v0_runtime_failure",
        "stage": "runtime_execution",
        **({"trace_ref": trace_ref} if trace_ref else {}),
        "runtime": "v0",
        "error_code": safe_code(trace.get("error_code") or result.get("error_type"), max_len=80),
        "decision_action": safe_code(trace.get("decision_action"), max_len=80),
        "call_counts": call_counts,
        "validation_errors": validation_errors,
    }
    _log_error_event(event)


def _journal_response_composer(result: dict[str, Any]) -> dict[str, Any] | None:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
    composer = trace.get("response_composer") if isinstance(trace.get("response_composer"), dict) else None
    if not composer:
        return None
    return composer


def _journal_v6_candidate(result: dict[str, Any]) -> dict[str, Any] | None:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
    candidate = trace.get("v6_candidate")
    return candidate if isinstance(candidate, dict) else None


def _journal_prompt_provenance(result: dict[str, Any]) -> dict[str, Any] | None:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
    provenance = trace.get("prompt_provenance") if isinstance(trace.get("prompt_provenance"), dict) else None
    return provenance


def _journal_execution_path(result: dict[str, Any], *, jivo_prepare: bool = False) -> dict[str, Any] | None:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
    execution_path = trace.get("execution_path")
    if str(meta.get("runtime") or "").strip().lower() == "v1":
        return append_v1_jivo_api_prepare(execution_path) if jivo_prepare else sanitize_v1_execution_path(execution_path)
    if jivo_prepare:
        return append_jivo_api_prepare(execution_path)
    return sanitize_execution_path(execution_path)


def _journal_response_model(result: dict[str, Any]) -> dict[str, Any] | None:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    if str(meta.get("runtime") or "").strip().lower() != "v1":
        return None
    trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
    value = trace.get("response_model") if isinstance(trace.get("response_model"), dict) else None
    if not value:
        return None
    mode = str(value.get("mode") or "").strip().lower()
    status = str(value.get("status") or "").strip().lower()
    if mode not in {"shadow", "publish"} or status not in {"valid", "fallback"}:
        return None
    out: dict[str, Any] = {"mode": mode, "status": status, "published": bool(value.get("published"))}
    model = str(value.get("model") or "").strip()
    if model == "openai/gpt-5.5":
        out["model"] = model
    reason = _safe_v1_response_model_reason(value.get("reason"))
    if reason:
        out["reason"] = reason
    if status == "fallback" and not reason:
        return None
    return out


def _safe_v1_response_model_reason(value: Any) -> str | None:
    text = str(value or "").strip()
    if text in {"invalid_json", "wrong_keys", "provider_or_validation_failed"}:
        return text
    prefix = "one_model_validation_failed:"
    if text.startswith(prefix):
        code = text[len(prefix):].split(":", 1)[0].strip()
        if code and re.fullmatch(r"[a-z0-9_]{1,80}", code):
            return prefix + code
    return None


def _journal_error_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Build bounded terminal diagnostics without passing raw exception content."""
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
    composer = trace.get("response_composer") if isinstance(trace.get("response_composer"), dict) else {}
    runtime = trace.get("runtime_summary") if isinstance(trace.get("runtime_summary"), dict) else {}
    codes: list[str] = []
    stages: list[str] = []
    fallback = False

    if result.get("ok") is False:
        codes.append("runtime_failure")
        stages.append("runtime")
        fallback = True

    blockers = runtime.get("quality_blockers") if isinstance(runtime.get("quality_blockers"), list) else []
    safe_blockers = [str(item) for item in blockers if str(item) in _JOURNAL_RUNTIME_QUALITY_BLOCKERS]
    if safe_blockers:
        codes.extend(safe_blockers)
        stages.append("runtime")

    composer_used = composer.get("composer_used")
    fallback_reason = str(composer.get("fallback_reason") or "").strip()
    validation_codes = composer.get("validation_codes") if isinstance(composer.get("validation_codes"), list) else []
    safe_validation = [str(code) for code in validation_codes if str(code) in _JOURNAL_COMPOSER_VALIDATION_CODES]
    if fallback_reason == "composer_error":
        codes.append("composer_error")
        stages.append("composer")
        fallback = True
    if fallback_reason == "validation_failed" or safe_validation:
        codes.append("composer_validation_failed")
        codes.extend(safe_validation)
        stages.append("composer")
        fallback = True

    validation = trace.get("search_validation") if isinstance(trace.get("search_validation"), dict) else {}
    if isinstance(validation.get("errors"), list) and validation.get("errors"):
        codes.append("search_validation_error")
        stages.append("search_validation")

    codes = list(dict.fromkeys(codes))[:8]
    stages = list(dict.fromkeys(stages))[:4]
    return {
        "status": "failed" if result.get("ok") is False else "degraded" if codes else "ok",
        "codes": codes,
        "stages": stages,
        "fallback": fallback,
    }


_JOURNAL_RUNTIME_QUALITY_BLOCKERS = {
    "runtime_error",
    "question_count_not_one",
    "final_question_not_at_end",
    "search_without_cards",
    "enrichment_error",
}
_JOURNAL_COMPOSER_VALIDATION_CODES = {
    "empty_response", "invalid_json", "json_root_must_be_object", "schema_required_field_missing",
    "schema_additional_properties", "schema_invalid_options", "too_many_cards", "option_name_not_allowed",
    "option_order_mismatch", "empty_option_section", "required_location_missing", "required_price_missing",
    "scenario_fact_benefit_missing", "scenario_viewpoint_mismatch", "intro_empty", "missing_note_required",
    "financing_missing_note_required", "final_question_empty", "recipe_cta_mismatch",
    "contact_before_financing_consent", "selected_financing_card_scope_invalid", "section_question_mark",
    "question_count_not_one", "final_question_not_at_end", "final_question_contract_mismatch",
    "missing_context_acknowledgement", "duplicate_answer", "repeated_identical_benefit",
    "unknown_option_name", "unknown_number_or_sensitive_claim", "internal_or_raw_wire_leak",
    "unsupported_sensitive_claim", "unsupported_marketing_claim",
}


def _journal_runtime_summary(result: dict[str, Any]) -> dict[str, Any] | None:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    trace = meta.get("trace") if isinstance(meta.get("trace"), dict) else {}
    summary = trace.get("runtime_summary") if isinstance(trace.get("runtime_summary"), dict) else None
    if summary is None:
        v6_trace = meta.get("v6_trace") if isinstance(meta.get("v6_trace"), dict) else {}
        summary = v6_trace.get("runtime_summary") if isinstance(v6_trace.get("runtime_summary"), dict) else None
    if not summary:
        return None
    stage = _journal_token(summary.get("stage"))
    action = _journal_token(summary.get("action"))
    if not stage or not action:
        return None
    out = {
        "stage": stage,
        "action": action,
        "answer_kind": _journal_token(summary.get("answer_kind")),
        "timing_ms": _journal_timing(summary.get("timing_ms")),
        "call_counts": _journal_counts(summary.get("call_counts")),
        "state_before": _journal_state_summary(summary.get("state_before")),
        "state_after": _journal_state_summary(summary.get("state_after")),
        "question_count": _journal_int(summary.get("question_count"), 0, 20),
        "final_question_at_end": bool(summary.get("final_question_at_end")),
        "quality_blockers": [str(item) for item in (summary.get("quality_blockers") if isinstance(summary.get("quality_blockers"), list) else []) if str(item) in _JOURNAL_RUNTIME_QUALITY_BLOCKERS][:5],
        "grounding_scope": "canonical_response_plan",
    }
    field_trace = _journal_field_trace(summary.get("field_trace"))
    if field_trace:
        out["field_trace"] = field_trace
    gateway_attempt_details = _journal_gateway_attempt_details(summary.get("gateway_attempt_details"))
    if gateway_attempt_details:
        out["gateway_attempt_details"] = gateway_attempt_details
    option_enrichment = _journal_option_enrichment(summary.get("option_enrichment"))
    if option_enrichment:
        out["option_enrichment"] = option_enrichment
    model_usage = _journal_model_usage(summary.get("model_usage"))
    if model_usage:
        out["model_usage"] = model_usage
    intent_transition = _journal_intent_transition(summary.get("intent_transition"))
    if intent_transition:
        out["intent_transition"] = intent_transition
    return out


_JOURNAL_INTENT_GOALS = {
    "new_search",
    "refine_search",
    "expand_search",
    "lookup_object",
    "answer_current",
    "compare_current",
    "recommend_current",
    "answer_selected",
    "answer_open_question",
    "operator",
    "clarify",
    "resume_pending",
    "off_topic",
}
_JOURNAL_INTENT_VALIDATION_ERROR_CODES = {
    "invalid_shape",
    "unknown_field",
    "invalid_schema_version",
    "invalid_goal",
    "missing_viewpoint",
    "invalid_constraints_delta",
    "invalid_operator_consent",
    "invalid_explicit_operator_request",
    "invalid_confidence",
    "invalid_schema",
    "invalid_requested_fact",
    "invalid_viewpoint",
    "selected_option_not_visible",
    "invalid_selected_option_scope",
    "missing_named_reference",
    "invalid_named_reference_scope",
    "missing_clarification",
    "clarification_on_non_clarify",
    "invalid_operator_consent_scope",
}
_JOURNAL_TRANSITION_ERROR_CODES = {
    "selected_option_not_in_visible_list",
    "missing_named_reference",
    "malformed_operation",
}


def _journal_intent_transition(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    goal = str(value.get("goal") or "").strip()
    validation = str(value.get("intent_validation") or "").strip()
    transition = value.get("transition") if isinstance(value.get("transition"), dict) else {}
    error_code = str(transition.get("error_code") or "").strip()
    return {
        "goal": goal if goal in _JOURNAL_INTENT_GOALS else None,
        "intent_validation": validation if validation in {"accepted", "failed"} else "failed",
        "validation_error_codes": [
            str(code)
            for code in (value.get("validation_error_codes") if isinstance(value.get("validation_error_codes"), list) else [])
            if str(code) in _JOURNAL_INTENT_VALIDATION_ERROR_CODES
        ][:8],
        "transition": {
            "accepted": bool(transition.get("accepted")),
            "error_code": error_code if error_code in _JOURNAL_TRANSITION_ERROR_CODES else None,
        },
        "fallback_used": bool(value.get("fallback_used")),
    }


def _journal_option_enrichment(value: Any) -> dict[str, Any]:
    enrichment = value if isinstance(value, dict) else {}
    evidence = _journal_availability_evidence(enrichment.get("availability_evidence"))
    return {"availability_evidence": evidence} if evidence else {}


def _journal_availability_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    confirmation = str(value.get("confirmation") or "").strip().lower()
    source = str(value.get("source") or "").strip().lower()
    out = {
        "requested": bool(value.get("requested")),
        "confirmation": confirmation if confirmation in {"not_requested", "confirmed", "not_confirmed"} else "not_confirmed",
        "source": source if source in {"gateway", "cache", "base", "unknown"} else "unknown",
    }
    task_id = _journal_token(value.get("gateway_task_id"))
    if task_id:
        out["gateway_task_id"] = task_id
    return out


def _journal_model_usage(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for role in ("answer", "search"):
        raw = value.get(role)
        if isinstance(raw, str):
            raw_items = [raw]
        elif isinstance(raw, list):
            raw_items = raw
        else:
            raw_items = []
        models = [model for model in (_journal_token(item) for item in raw_items) if model]
        if models:
            out[role] = list(dict.fromkeys(models))[:3]
    return out


def _journal_gateway_attempt_details(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        attempt: dict[str, Any] = {}
        stage = _journal_token(item.get("stage"))
        if stage == "gateway_attempt":
            attempt["stage"] = stage
        payload_stage = _journal_token(item.get("_payload_stage"))
        if payload_stage:
            attempt["_payload_stage"] = payload_stage
        if payload_stage == "v6_prompt2_question_refiner":
            if isinstance(item.get("call_attempted"), bool):
                attempt["call_attempted"] = bool(item.get("call_attempted"))
            gateway_status = str(item.get("gateway_status") or "").strip()
            if gateway_status in {"completed", "error", "unknown"}:
                attempt["gateway_status"] = gateway_status
            parse_status = str(item.get("parse_status") or "").strip()
            if parse_status in {"ok", "invalid_json", "missing"}:
                attempt["parse_status"] = parse_status
            validator_status = str(item.get("validator_status") or "").strip()
            if validator_status in {"ok", "rejected", "missing"}:
                attempt["validator_status"] = validator_status
            fallback_reason = str(item.get("fallback_reason") or "").strip()
            if fallback_reason in {"none", "transport", "parse", "semantic", "locked", "length"}:
                attempt["fallback_reason"] = fallback_reason
            out.append(attempt)
            continue
        model = _journal_token(item.get("model"))
        if model:
            attempt["model"] = model
        role = str(item.get("model_role") or "").strip().lower()
        if role in {"search", "answer"}:
            attempt["model_role"] = role
        for key in ("ok", "empty", "safe"):
            if isinstance(item.get(key), bool):
                attempt[key] = bool(item.get(key))
        task_id = _journal_token(item.get("gateway_task_id"))
        if task_id:
            attempt["gateway_task_id"] = task_id
        attempt["duration_ms"] = _journal_int(item.get("duration_ms"), 0, 10 * 60 * 1000)
        parse_status = str(item.get("parse_status") or "").strip()
        if parse_status in {"ok", "invalid_json", "missing"}:
            attempt["parse_status"] = parse_status
        gateway_status = str(item.get("gateway_status") or "").strip()
        if gateway_status in {"completed", "timeout", "error", "unknown"}:
            attempt["gateway_status"] = gateway_status
        validator_status = str(item.get("validator_status") or "").strip()
        if validator_status in {"ok", "rejected", "missing"}:
            attempt["validator_status"] = validator_status
        fallback_reason = str(item.get("fallback_reason") or "").strip()
        if fallback_reason in {"none", "transport", "parse", "semantic", "locked", "length"}:
            attempt["fallback_reason"] = fallback_reason
        response_parse = str(item.get("response_parse") or "").strip()
        if response_parse in {"valid_json", "invalid_json", "empty"}:
            attempt["response_parse"] = response_parse
        for key in ("response_chars", "message_chars"):
            if key in item:
                attempt[key] = _journal_int(item.get(key), 0, 20_000)
        if "data_count" in item:
            attempt["data_count"] = _journal_int(item.get("data_count"), 0, 20)
        if isinstance(item.get("call_attempted"), bool):
            attempt["call_attempted"] = bool(item.get("call_attempted"))
        request_shape = item.get("request_shape") if isinstance(item.get("request_shape"), dict) else {}
        safe_shape = {key: bool(request_shape.get(key)) for key in ("family_query", "rooms_mentioned") if isinstance(request_shape.get(key), bool)}
        if safe_shape:
            attempt["request_shape"] = safe_shape
        if attempt:
            out.append(attempt)
    return out


def _journal_field_trace(value: Any) -> dict[str, Any]:
    trace = value if isinstance(value, dict) else {}
    cards = trace.get("cards") if isinstance(trace.get("cards"), list) else []
    allowed = set(V0_PRESENTATION_TRACE_FIELDS) | set(OptionCard.__dataclass_fields__)

    def safe_fields(raw: Any) -> list[str]:
        source = raw if isinstance(raw, list) else []
        out: list[str] = []
        for item in source:
            text = str(item or "").strip()
            if text in allowed and not any(part in text.lower() for part in ("phone", "email", "client", "chat", "token", "secret", "payload")):
                out.append(text[:80])
        return list(dict.fromkeys(out))[:20]

    safe_cards = []
    for item in cards[:3]:
        if isinstance(item, dict):
            safe_cards.append({"raw_fields": safe_fields(item.get("raw_fields")), "normalized_fields": safe_fields(item.get("normalized_fields"))})
    return {"cards": safe_cards} if safe_cards else {}


def _journal_token(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return re.sub(r"[^a-zA-Z0-9_.:-]", "_", text)[:80]


def _journal_int(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(number, high))


def _journal_timing(value: Any) -> dict[str, int]:
    timing = value if isinstance(value, dict) else {}
    return {key: _journal_int(timing.get(key), 0, 10 * 60 * 1000) for key in ("planner", "execution", "response", "total")}


def _journal_counts(value: Any) -> dict[str, int]:
    counts = value if isinstance(value, dict) else {}
    return {
        "planner": _journal_int(counts.get("planner"), 0, 3),
        "search": _journal_int(counts.get("search"), 0, 1),
        "selected_enrichment": _journal_int(counts.get("selected_enrichment"), 0, 1),
        "gateway_attempts": _journal_int(counts.get("gateway_attempts"), 0, 5),
        "scenario_search": _journal_int(counts.get("scenario_search"), 0, 3),
        "answer": _journal_int(counts.get("answer"), 0, 3),
    }


def _journal_state_summary(value: Any) -> dict[str, Any]:
    state = value if isinstance(value, dict) else {}
    raw_keys = state.get("param_keys") if isinstance(state.get("param_keys"), list) else []
    return {
        "param_keys": sorted(dict.fromkeys(key for key in (_journal_param_key(item) for item in raw_keys) if key))[:20],
        "visible_options_count": _journal_int(state.get("visible_options_count"), 0, 20),
        "selected_present": bool(state.get("selected_present")),
        "pending_followup": _journal_token(state.get("pending_followup")),
        "active_topic": _journal_token(state.get("active_topic")),
    }


def _journal_param_key(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if any(part in text for part in ("phone", "тел", "email", "client", "chat", "token", "secret", "+7", "7999")):
        return None
    return _journal_token(text)


async def close_client(app: web.Application) -> None:
    for key in ("overmind_client", "legacy_overmind_client"):
        client = app.get(key) if hasattr(app, "get") else None
        close = getattr(client, "close", None)
        if close is not None:
            await close()


def create_app() -> web.Application:
    app = web.Application()
    state_file = Path(os.getenv("NMBOT_API_STATE_FILE", str(DEFAULT_STATE_FILE))).expanduser()
    runtime_version_file = Path(os.getenv("NMBOT_RUNTIME_VERSION_FILE", str(DEFAULT_RUNTIME_VERSION_FILE))).expanduser()
    app["state_store"] = JsonStateStore(state_file)
    app["runtime_version_store"] = RuntimeVersionStore(runtime_version_file)
    app["overmind_client"] = OvermindClient()
    v6_transport = DirectTransport(app["overmind_client"])
    app["v6_simple_prompt1_port"] = SimpleGateway(v6_transport, "prompt1")
    app["v6_simple_prompt2_port"] = SimpleGateway(v6_transport, "prompt2")
    from nmbot_v6.url_card import extract_novostroy_url, fetch_card, url_card_feature_enabled

    # Deploying the isolated branch is the opt-in action. The environment key
    # remains a fail-closed kill switch, so TEST needs no remote dotenv edit.
    if url_card_feature_enabled():
        app["v6_url_card_fetcher"] = fetch_card
        app["v6_url_card_extractor"] = extract_novostroy_url
    app["v1_planner_port"] = V1GatewayPlannerPort(app["overmind_client"])
    app["v1_search_port"] = V1GatewaySearchPort(app["overmind_client"])
    app["v1_one_model_gpt55_port"] = V1GatewayOneModelResponsePort(app["overmind_client"])
    app["v4_provider_port"] = V4GatewayOnePromptPort(app["overmind_client"])
    app["v1_presenter_mode"] = "off"
    app["crm_callback_outbox"] = LocalCallbackOutbox(Path(os.getenv("NMBOT_CALLBACK_OUTBOX_DIR", str(DEFAULT_CALLBACK_OUTBOX_DIR))).expanduser())
    app["v6_callback_outbox"] = app["crm_callback_outbox"]
    app["jivo_session_locks"] = SessionLockRegistry()
    app["jivo_dedup_cache"] = JivoDedupCache()
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/runtime-version", handle_api_runtime_version)
    app.router.add_post("/api/runtime-version", handle_api_runtime_version_set)
    app.router.add_post("/api/chat", handle_api_chat)
    app.router.add_post("/api/reset", handle_api_reset)
    app.router.add_post("/jivo/{provider_token}", handle_jivo)
    app.on_cleanup.append(close_client)
    return app


async def smoke() -> int:
    payload = {
        "event": "CLIENT_MESSAGE",
        "site_id": "site1",
        "client_id": "client1",
        "chat_id": "chat1",
        "agents_online": True,
        "sender": {"id": "client1", "name": "Test", "has_contacts": False},
        "message": {"type": "TEXT", "text": "Позови оператора", "timestamp": _now_ts()},
        "channel": {"id": "widget1", "type": "widget"},
    }
    assert _jivo_session_key(payload) == "jivo:site1:chat1:client1"
    bot_msg = build_jivo_bot_message(payload, "Тест")
    assert bot_msg["event"] == "BOT_MESSAGE"
    assert bot_msg["message"]["type"] == "TEXT"
    invite = build_jivo_invite_agent(payload)
    assert invite == {"event": "INVITE_AGENT", "client_id": "client1", "chat_id": "chat1"}
    print("OK: nmbot_api_server smoke passed")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal nmbot HTTP/Jivo API server")
    parser.add_argument("--host", default=os.getenv("NMBOT_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("NMBOT_API_PORT", "8088")))
    parser.add_argument("--smoke", action="store_true", help="run local adapter smoke without secrets")
    args = parser.parse_args()
    if args.smoke:
        raise SystemExit(asyncio.run(smoke()))
    host, port = _client_production_bind(args.host, args.port)
    web.run_app(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
