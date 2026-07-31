from __future__ import annotations

import asyncio
import copy
from collections import OrderedDict
from datetime import datetime, timezone
import json
import os
import re
import time
from dataclasses import replace
from functools import lru_cache
from inspect import signature
from pathlib import Path
from typing import Any

from nmbot_v2.card_normalizer import normalize_card, normalize_search_result
from nmbot_v2.contracts import ExecutableTurn, ExecutionResult, IntentGoal, IntentPlanV3, OptionCard, RetrySearchContext, SafeTurnContext, SearchResult, SemanticPlan, StateDelta, TurnAction, TurnPlan, TurnResult, to_jsonable
from nmbot_v2.conversation import build_native_conversation_answer
from nmbot_v2.fact_context import ALLOWED_FACTS, ALLOWED_SUBJECTS, SUBJECT_FACT_MAP, answered_facts, present_fact_names, split_requested_facts
from nmbot_v2.runtime import TurnProcessor
from nmbot_v2.manager_rewriter import manager_rewriter_request_payload as build_manager_rewriter_payload, parse_manager_rewriter_text
from nmbot_v2.response_composer import compose_response_one_shot_async, request_payload as build_response_composer_payload
from nmbot_v2.pending import is_pending_contact_name, is_pending_contact_phone
from nmbot_v2.prompt_provenance import build_prompt_provenance, identity_from_text, merge_prompt_provenance, sanitize_prompt_provenance
from nmbot_v2.scenario_recipes import FINANCING_CONSENT_FOLLOWUP, SELECTED_LIVE_FACT_CONSENT_FOLLOWUP, reply_contract_for_pending
from nmbot_v2.search_enrichment import enrich_search_result_top_options, fetch_enriched_option_v2, merge_option_cards
from nmbot_v2.search_contract import ALLOWED_PREFERENCES, HARD_KEYS, SEARCH_MODEL, build_request_data as build_v2_search_request_data
from nmbot_v2.search_contract import MCP_ALIAS, available_fact_fields, build_candidate_retrieval_request, build_search_request, is_candidate_retrieval_request, load_prompt as load_v2_search_prompt, normalize_search_output, parse_strict_json, validate_search_output
from nmbot_v2.semantic_planner import DerivedPlannerDecision, SemanticPlannerResult, derive_runtime_decision, normalize_semantic_planner_result, validate_intent_plan_v3
from nmbot_v2.state import ConversationState, EnrichedCardCacheEntry, apply_state_delta, enriched_cache_entry_is_fresh, enriched_card_identity
from nmbot_v2.transition import TransitionDecision, compile_executable_turn_v3, derive_transition_v3
from nmbot_v0 import V0State, V0TurnProcessor
from nmbot_v0.field_contract import V0_PRESENTATION_FIELD_GROUPS, V0_PRESENTATION_TRACE_FIELDS, v0_presentation_search_fields
from scripts.nmbot_card_reformatter import build_reformat_plan


REPO_ROOT = Path(__file__).resolve().parents[1]
V0_SCENARIO_PROMPT_PATH = REPO_ROOT / "prompts" / "v0_scenario_search.txt"
V0_ANSWER_PROMPT_PATH = REPO_ROOT / "prompts" / "v0_answer.txt"

try:
    import followup_intent_classifier
except ImportError:  # pragma: no cover - package-style fallback
    from . import followup_intent_classifier  # type: ignore

try:
    from nmbot_planner_context import TurnDecision, append_safe_planner_trace, safe_planner_state
except ImportError:  # pragma: no cover - package-style fallback
    from .nmbot_planner_context import TurnDecision, append_safe_planner_trace, safe_planner_state  # type: ignore


SAFE_V2_ERROR_TEXT = "Не получилось обновить подбор. Оставлю прежние условия как есть, чтобы ничего не потерять. Попробовать ещё раз по тем же условиям?"
SCENARIO_NEED_FACETS = {"family", "rental", "investment", "life", "financing"}
SCENARIO_NEED_ALIASES = {"mortgage": "financing", "finance": "financing"}
SUPPORTED_RUNTIME_VERSIONS = frozenset({"V0", "V2", "V3"})


def _card_reformatter_mode() -> str:
    return "shadow" if str(os.getenv("NMBOT_CARD_REFORMATTER_MODE") or "").strip().lower() == "shadow" else "off"


def _card_reformatter_shadow(cards: Any, scenario: Any = "life") -> dict[str, Any] | None:
    if _card_reformatter_mode() != "shadow" or not isinstance(cards, (list, tuple)) or not cards:
        return None
    safe_cards = [to_jsonable(card) for card in cards[:3] if isinstance(card, OptionCard)]
    if not safe_cards:
        return None
    plan = build_reformat_plan({"facts": safe_cards, "near": [], "missing": [], "params": {}}, str(scenario or "life"))
    return {
        "mode": "shadow",
        "classification": str(plan.get("classification") or "unknown")[:32],
        "card_count": min(len(plan.get("cards") or []), 3),
        "cards": [
            {
                "idx": int(item.get("idx") or index + 1),
                "name": str(item.get("name") or "")[:180],
                "mandatory_text": str(item.get("mandatory_text") or "")[:500],
                "card_mode": str(item.get("mode") or "facts")[:20],
                "anchor_fact": str(item.get("anchor_fact") or "")[:80],
            }
            for index, item in enumerate(plan.get("cards") or [])
            if isinstance(item, dict)
        ][:3],
    }


async def run_runtime_turn(
    app: Any,
    *,
    user_id: str,
    message: str,
    channel: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Global V0/V2 local runtime adapter for NMBot API/Jivo turns.

    The public transport boundary remains unchanged: callers invoke this adapter
    with an already-normalized turn. Version selection is server-side only and
    defaults to V2 unless the protected global setting explicitly says V0.
    """
    version = "V2"
    try:
        version = await _session_runtime_version(app, user_id) or await _active_runtime_version(app)
        if version == "V0":
            return await _run_v0_authoritative(app, user_id=user_id, message=message, channel=channel, meta=meta)
        if version == "V3":
            return _decorate_v3_result(await _run_v2_authoritative_for_public_runtime(app, user_id=user_id, message=message, channel=channel, meta=meta, runtime_version="v3", engine_version="v2"))
        return await _run_v2_authoritative(app, user_id=user_id, message=message, channel=channel, meta=meta)
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        return _config_error("v0_runtime_exception" if version == "V0" else "v2_runtime_exception", detail=exc.__class__.__name__)


def _normalize_runtime_version(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in SUPPORTED_RUNTIME_VERSIONS else "V2"


def _decorate_v3_result(result: dict[str, Any]) -> dict[str, Any]:
    public = copy.deepcopy(result) if isinstance(result, dict) else {}
    meta = public.get("meta") if isinstance(public.get("meta"), dict) else {}
    meta = dict(meta)
    meta["runtime"] = "v3"
    meta.setdefault("engine", "v2")
    public["meta"] = meta
    return public


async def _run_v2_authoritative_for_public_runtime(
    app: Any,
    *,
    user_id: str,
    message: str,
    channel: str,
    meta: dict[str, Any] | None,
    runtime_version: str,
    engine_version: str,
) -> dict[str, Any]:
    params = signature(_run_v2_authoritative).parameters
    if "runtime_version" not in params:
        # Test monkeypatch/backward-compatible fallback: public metadata is fixed
        # by _decorate_v3_result, but old replacement functions cannot enqueue.
        return await _run_v2_authoritative(app, user_id=user_id, message=message, channel=channel, meta=meta)
    return await _run_v2_authoritative(
        app,
        user_id=user_id,
        message=message,
        channel=channel,
        meta=meta,
        runtime_version=runtime_version,
        engine_version=engine_version,
    )


def _config_error(code: str, *, detail: str | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"runtime_adapter": {"error": code}}
    if detail is not None:
        meta["runtime_adapter"]["detail"] = detail
    return {
        "ok": False,
        "error": "runtime_config_error",
        "error_code": code,
        "answer": "Сейчас не получилось продолжить подбор. Попробуйте написать ещё раз — я не буду менять условия, чтобы ничего не сбить.",
        "intent": "runtime_config_error",
        "awaiting_phone": False,
        "handoff_to_operator": False,
        "selected_option": None,
        "buttons": [],
        "meta": meta,
    }


def _safe_v0_validation_errors(diagnostics: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(diagnostics, dict):
        return []
    search_validation = diagnostics.get("search_validation") if isinstance(diagnostics.get("search_validation"), dict) else diagnostics
    errors = search_validation.get("errors") if isinstance(search_validation, dict) else None
    if not isinstance(errors, list):
        return []
    safe: list[str] = []
    for error in errors:
        raw_code = str(error or "").split(":", 1)[0].strip()
        code = "validation_error" if re.search(r"\s", raw_code) else raw_code
        token = re.sub(r"[^A-Za-z0-9_.-]", "_", code.encode("ascii", "ignore").decode("ascii"))[:120].strip("_")
        if not token:
            token = "validation_error"
        safe.append(token)
        if len(safe) >= limit:
            break
    return safe


def _safe_v0_search_validation_trace(diagnostics: Any) -> dict[str, Any] | None:
    if not isinstance(diagnostics, dict):
        return None
    search_validation = diagnostics.get("search_validation") if isinstance(diagnostics.get("search_validation"), dict) else {}
    if not search_validation:
        return None
    counts = search_validation.get("counts") if isinstance(search_validation.get("counts"), dict) else {}
    safe = {
        "stage": "search_validation_report",
        "status": _safe_v0_token(search_validation.get("status")) or "unknown",
        "errors": _safe_v0_validation_errors(diagnostics),
        "warnings": _safe_search_codes(search_validation.get("warnings"), limit=12),
        "counts": _safe_search_counts({
            **counts,
            "errors": len(search_validation.get("errors") or []) if isinstance(search_validation.get("errors"), list) else 0,
            "warnings": len(search_validation.get("warnings") or []) if isinstance(search_validation.get("warnings"), list) else 0,
        }),
    }
    if safe["status"] == "valid" and not safe["errors"] and not safe["warnings"]:
        return None
    return safe


def _safe_v0_field_trace(diagnostics: Any, *, max_cards: int = 3, max_fields: int = 20) -> dict[str, Any]:
    if not isinstance(diagnostics, dict):
        return {}
    search_validation = diagnostics.get("search_validation") if isinstance(diagnostics.get("search_validation"), dict) else {}
    trace = search_validation.get("field_trace") if isinstance(search_validation.get("field_trace"), dict) else {}
    cards = trace.get("cards") if isinstance(trace.get("cards"), list) else []

    def safe_field(value: Any) -> str | None:
        text = str(value or "").strip()
        if text not in V0_PRESENTATION_TRACE_FIELDS and text not in OptionCard.__dataclass_fields__:
            return None
        if any(part in text.lower() for part in ("phone", "тел", "email", "client", "chat", "token", "secret", "raw_payload")):
            return None
        return text[:80]

    safe_cards: list[dict[str, list[str]]] = []
    for item in cards[:max_cards]:
        if not isinstance(item, dict):
            continue
        raw = [field for field in (safe_field(x) for x in item.get("raw_fields", []) if isinstance(item.get("raw_fields"), list)) if field]
        normalized = [field for field in (safe_field(x) for x in item.get("normalized_fields", []) if isinstance(item.get("normalized_fields"), list)) if field]
        safe_cards.append({
            "raw_fields": list(dict.fromkeys(raw))[:max_fields],
            "normalized_fields": list(dict.fromkeys(normalized))[:max_fields],
        })
    return {"cards": safe_cards} if safe_cards else {}


def _safe_v0_state_summary(state: V0State) -> dict[str, Any]:
    return {
        "param_keys": sorted(dict.fromkeys(_safe_v0_param_key(key) for key in state.params if _safe_v0_param_key(key)))[:20],
        "visible_options_count": max(0, min(len(state.visible_options), 20)),
        "selected_present": bool(state.selected_option_name),
        "pending_followup": _safe_v0_token(state.pending_action),
        "active_topic": _safe_v0_token(state.active_topic),
    }


def _safe_v0_param_key(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text or any(part in text for part in ("phone", "тел", "email", "client", "chat", "token", "secret", "+7", "7999")):
        return None
    return re.sub(r"[^a-zA-Z0-9_.:-]", "_", text)[:80]


def _safe_v0_token(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return re.sub(r"[^a-zA-Z0-9_.:-]", "_", text)[:80]


def _v0_question_count(text: str) -> int:
    return str(text or "").count("?")


def _v0_final_question_at_end(text: str) -> bool:
    return str(text or "").rstrip().endswith("?")


def _safe_v0_call_counts(raw_counts: Any, *, decision_action: str) -> dict[str, int]:
    counts = raw_counts if isinstance(raw_counts, dict) else {}
    scenario_search = _bounded_int(counts.get("scenario_search"), 0, 3)
    answer = _bounded_int(counts.get("answer"), 0, 3)
    return {
        "planner": scenario_search,
        "search": 1 if decision_action == "search" and scenario_search > 0 else 0,
        "selected_enrichment": 0,
        "gateway_attempts": scenario_search + answer,
        "scenario_search": scenario_search,
        "answer": answer,
    }


def _v0_quality_blockers(*, result: Any, decision_action: str, question_count: int, final_question_at_end: bool) -> list[str]:
    blockers: list[str] = []
    if not getattr(result, "ok", False):
        blockers.append("runtime_error")
    if getattr(result, "ok", False):
        if question_count != 1:
            blockers.append("question_count_not_one")
        if not final_question_at_end:
            blockers.append("final_question_not_at_end")
    state = getattr(result, "state", None)
    if decision_action == "search" and state is not None and not getattr(state, "visible_options", ()):  # terminal handoff paths return before this summary
        blockers.append("search_without_cards")
    return blockers


def _build_v0_runtime_summary(
    *,
    result: Any,
    state_before: V0State,
    state_after: V0State,
    decision_action: str,
    call_counts: dict[str, int],
    field_trace: dict[str, Any],
    final_answer: str,
) -> dict[str, Any]:
    question_count = _v0_question_count(final_answer)
    final_at_end = _v0_final_question_at_end(final_answer)
    summary: dict[str, Any] = {
        "stage": "v0_turn",
        "action": decision_action or "v0_turn",
        "answer_kind": result.answer.answer_kind if getattr(result, "answer", None) else None,
        "call_counts": _safe_v0_call_counts(call_counts, decision_action=decision_action),
        "state_before": _safe_v0_state_summary(state_before),
        "state_after": _safe_v0_state_summary(state_after),
        "question_count": question_count,
        "final_question_at_end": final_at_end,
        "quality_blockers": _v0_quality_blockers(result=result, decision_action=decision_action, question_count=question_count, final_question_at_end=final_at_end),
        "grounding_scope": "canonical_response_plan",
    }
    if field_trace:
        summary["field_trace"] = field_trace
    return summary


async def _active_runtime_version(app: Any) -> str:
    store = app.get("runtime_version_store") if hasattr(app, "get") else None
    if store is not None and hasattr(store, "get"):
        value = store.get()
        if asyncio.iscoroutine(value):
            value = await value
        return _normalize_runtime_version(value)
    return "V2"


async def _session_runtime_version(app: Any, user_id: str) -> str | None:
    store = app.get("state_store") if hasattr(app, "get") else None
    if store is None or not hasattr(store, "get"):
        return None
    envelope = store.get(user_id)
    if asyncio.iscoroutine(envelope):
        envelope = await envelope
    if not isinstance(envelope, dict):
        return None
    value = envelope.get("runtime_version_override")
    normalized = str(value or "").strip().upper()
    return normalized if normalized in SUPPORTED_RUNTIME_VERSIONS else None


async def _run_v0_authoritative(
    app: Any,
    *,
    user_id: str,
    message: str,
    channel: str,
    meta: dict[str, Any] | None,
    commit: bool = True,
) -> dict[str, Any]:
    text = str(message or "").strip()
    if not text:
        return {"ok": False, "error": "empty_message", "answer": "Напишите, что хотите посмотреть, и я подхвачу."}
    store = app["state_store"]
    envelope = copy.deepcopy(await store.get(user_id))
    v0_state = _envelope_to_v0_state(envelope)
    if commit:
        contact_result = _try_capture_contact(
            app,
            user_id=user_id,
            text=text,
            channel=channel,
            meta=meta or {},
            state=v0_state,
            runtime_version="v0",
        )
        if contact_result is not None:
            await store.save(user_id, _merge_runtime_namespace_envelope(envelope, _canonical_v0_envelope(contact_result["state"])))
            return contact_result["public"]
    ports = _V0GatewayPorts(app)
    result = await V0TurnProcessor(scenario_search=ports.scenario_search, answer=ports.answer).process_async(
        text,
        state=v0_state,
        conversation_ref=user_id,
    )
    if result.ok and commit:
        await store.save(user_id, _merge_runtime_namespace_envelope(envelope, _canonical_v0_envelope(result.state)))
    answer = result.message or (result.answer.text() if result.answer else "Сейчас не хочу отвечать наугад.")
    decision = result.diagnostics.get("decision") if isinstance(result.diagnostics, dict) else {}
    decision_action = str(decision.get("action") or "") if isinstance(decision, dict) else ""
    validation_errors = _safe_v0_validation_errors(result.diagnostics)
    field_trace = _safe_v0_field_trace(result.diagnostics)
    search_validation_trace = _safe_v0_search_validation_trace(result.diagnostics)
    trace: dict[str, Any] = {
        "ok": bool(result.ok),
        "error_code": result.error_code,
        "decision_action": decision_action,
        "call_counts": dict(ports.call_counts),
        "validation_errors": validation_errors,
        "runtime_summary": _build_v0_runtime_summary(
            result=result,
            state_before=v0_state,
            state_after=result.state,
            decision_action=decision_action,
            call_counts=dict(ports.call_counts),
            field_trace=field_trace,
            final_answer=answer,
        ),
    }
    shadow = _card_reformatter_shadow(result.state.visible_options, decision.get("viewpoint") or v0_state.active_topic or "life") if result.ok else None
    if shadow:
        trace["card_reformatter_shadow"] = shadow
        trace["runtime_summary"]["card_reformatter_shadow"] = shadow
    if field_trace:
        trace["field_trace"] = field_trace
    if search_validation_trace:
        trace["search_validation"] = search_validation_trace
    return {
        "ok": bool(result.ok),
        **({"error": "v0_runtime_error", "error_type": result.error_code} if not result.ok else {}),
        "answer": answer,
        "intent": str(decision.get("action") or ("safe_upstream_fallback" if not result.ok else "v0_turn")) if isinstance(decision, dict) else "v0_turn",
        "awaiting_phone": False,
        "handoff_to_operator": False,
        "selected_option": result.state.selected_option_name,
        "buttons": [],
        "meta": {
            "channel": channel,
            "runtime": "v0",
            "trace": trace,
        },
    }


class _V0GatewayPorts:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.call_counts = {"scenario_search": 0, "answer": 0}

    async def scenario_search(self, context: dict[str, Any]) -> str:
        self.call_counts["scenario_search"] += 1
        client = self.app["overmind_client"]
        request_data = _build_v0_gateway_request(
            stage="nmbot_v0_scenario_search",
            query=(
                "V0_SCENARIO_SEARCH_CONTEXT=" + json.dumps(_safe_nested(context), ensure_ascii=False, sort_keys=True) +
                "\nV0_RUNTIME_METADATA=" + json.dumps(_v0_runtime_metadata(), ensure_ascii=False, sort_keys=True) +
                "\nКонтекст и runtime metadata выше. Следуй только системному prompt V0 scenario/search."
            ),
            mcp=True,
            max_tokens_env="NMBOT_V0_SCENARIO_MAX_TOKENS",
            max_tokens_default=5000,
        )
        raw, meta = await _run_v2_low_level_gateway(client, request_data, timeout_env="NMBOT_V0_SCENARIO_TIMEOUT")
        if isinstance(meta, dict) and (meta.get("_safe_fallback") or meta.get("_upstream_error")):
            raise RuntimeError("v0_scenario_gateway_not_ok")
        return str(raw or "")

    async def answer(self, brief: dict[str, Any]) -> str:
        self.call_counts["answer"] += 1
        client = self.app["overmind_client"]
        request_data = _build_v0_gateway_request(
            stage="nmbot_v0_answer",
            query=(
                "V0_ANSWER_BRIEF=" + json.dumps(_safe_nested(brief), ensure_ascii=False, sort_keys=True) +
                "\nBrief выше. Следуй только системному prompt V0 answer."
            ),
            mcp=False,
            max_tokens_env="NMBOT_V0_ANSWER_MAX_TOKENS",
            max_tokens_default=1600,
        )
        raw, meta = await _run_v2_low_level_gateway(client, request_data, timeout_env="NMBOT_V0_ANSWER_TIMEOUT")
        if isinstance(meta, dict) and (meta.get("_safe_fallback") or meta.get("_upstream_error")):
            raise RuntimeError("v0_answer_gateway_not_ok")
        return str(raw or "")


def _build_v0_gateway_request(*, stage: str, query: str, mcp: bool, max_tokens_env: str, max_tokens_default: int) -> dict[str, Any]:
    data: dict[str, Any] = {
        "_payload_stage": stage,
        "query": query,
        "service": "openrouter",
        "model": os.getenv("NMBOT_V0_MODEL", SEARCH_MODEL),
        "system_prompt": _v0_system_prompt(stage),
        "parameters": {"temperature": 0.1, "max_tokens": int(os.getenv(max_tokens_env, str(max_tokens_default)))},
    }
    if mcp:
        data["mcp_servers"] = [MCP_ALIAS]
    api_key = os.getenv("OPENROUTER_API_KEY") or ""
    if api_key:
        data["external_api_key"] = api_key
    return data


def _v0_system_prompt(stage: str) -> str:
    if stage == "nmbot_v0_scenario_search":
        return _load_v0_prompt(V0_SCENARIO_PROMPT_PATH)
    if stage == "nmbot_v0_answer":
        return _load_v0_prompt(V0_ANSWER_PROMPT_PATH)
    raise ValueError(f"unknown_v0_stage:{stage}")


@lru_cache(maxsize=2)
def _load_v0_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _v0_runtime_metadata() -> dict[str, Any]:
    presentation_fields = v0_presentation_search_fields()
    return {
        "search_field_contract": {
            "purpose": "v0_card_presentation_and_search",
            "limit": len(presentation_fields),
            "groups": {key: list(value) for key, value in V0_PRESENTATION_FIELD_GROUPS.items()},
            "fields": presentation_fields,
        },
        "available_fact_fields_by_viewpoint": {k: presentation_fields for k in ("life", "family", "investment", "rental", "financing")},
    }


def _merge_runtime_namespace_envelope(existing: dict[str, Any] | None, active: dict[str, Any]) -> dict[str, Any]:
    """Merge one active NMBot namespace without carrying stale legacy root state."""

    merged: dict[str, Any] = {}
    source = existing if isinstance(existing, dict) else {}
    for key in ("nmbot_v0", "nmbot_v2"):
        value = source.get(key)
        if isinstance(value, dict):
            merged[key] = copy.deepcopy(value)
    for key in ("nmbot_v0", "nmbot_v2"):
        value = active.get(key) if isinstance(active, dict) else None
        if isinstance(value, dict):
            merged[key] = copy.deepcopy(value)
    override = active.get("runtime_version_override") if isinstance(active, dict) else None
    if override is None and isinstance(source, dict):
        override = source.get("runtime_version_override")
    normalized_override = str(override or "").strip().upper()
    if normalized_override in SUPPORTED_RUNTIME_VERSIONS:
        merged["runtime_version_override"] = normalized_override
    return merged


def _canonical_v0_envelope(state: V0State | None = None) -> dict[str, Any]:
    state = state or V0State()
    return {"nmbot_v0": _v0_state_to_dict(state)}


def _v0_state_to_dict(state: V0State) -> dict[str, Any]:
    return {
        "params": dict(state.params),
        "visible_options": [to_jsonable(card) for card in state.visible_options[:3]],
        "selected_option_name": state.selected_option_name,
        "active_topic": state.active_topic,
        "has_greeted": bool(state.has_greeted),
        "last_answer_kind": state.last_answer_kind,
        "last_assistant_question": state.last_assistant_question,
        "answered_facts": list(state.answered_facts),
        "pending_action": state.pending_action,
        "pending_subject": state.pending_subject,
        "pending_topic": state.pending_topic,
    }


def _envelope_to_v0_state(envelope: dict[str, Any]) -> V0State:
    raw = envelope.get("nmbot_v0") if isinstance(envelope, dict) else None
    data = raw if isinstance(raw, dict) else {}
    cards_raw = data.get("visible_options") if isinstance(data.get("visible_options"), list) else []
    cards = tuple(OptionCard.from_dict(item) for item in cards_raw[:3] if isinstance(item, dict))
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    return V0State(
        params=dict(params),
        visible_options=cards,
        selected_option_name=str(data.get("selected_option_name") or "").strip() or None,
        active_topic=str(data.get("active_topic") or "").strip() or None,
        has_greeted=bool(data.get("has_greeted")),
        last_answer_kind=str(data.get("last_answer_kind") or "").strip() or None,
        last_assistant_question=str(data.get("last_assistant_question") or "").strip() or None,
        answered_facts=tuple(str(item).strip() for item in data.get("answered_facts", ()) if str(item or "").strip()) if isinstance(data.get("answered_facts", ()), (list, tuple)) else (),
        pending_action=str(data.get("pending_action") or "").strip() or None,
        pending_subject=str(data.get("pending_subject") or "").strip() or None,
        pending_topic=str(data.get("pending_topic") or "").strip() or None,
    )


async def _run_v2_authoritative(
    app: Any,
    *,
    user_id: str,
    message: str,
    channel: str,
    meta: dict[str, Any] | None,
    commit: bool = True,
    runtime_version: str = "v2",
    engine_version: str | None = None,
) -> dict[str, Any]:
    text = str(message or "").strip()
    if not text:
        return {"ok": False, "error": "empty_message", "answer": "Напишите, что хотите посмотреть, и я подхвачу."}

    store = app["state_store"]
    legacy_state = copy.deepcopy(await store.get(user_id))
    working_state = copy.deepcopy(legacy_state)
    v2_state = _legacy_to_v2_state(working_state)
    v2_state = _prefill_selected_visible_option_v2(text, v2_state)
    if commit:
        contact_result = _try_capture_contact(app, user_id=user_id, text=text, channel=channel, meta=meta or {}, state=v2_state, runtime_version=runtime_version, engine_version=engine_version)
        if contact_result is not None:
            new_state = _merge_runtime_namespace_envelope(legacy_state, _canonical_v2_envelope(contact_result["state"]))
            await store.save(user_id, new_state)
            return contact_result["public"]
    context = SafeTurnContext(conversation_ref=user_id, user_text=text, channel=channel, metadata=_safe_meta(meta or {}))
    runtime_key = str(runtime_version).strip().lower()
    composer_mode = _runtime_response_composer_mode(runtime_key)
    manager_rewriter_mode = _runtime_manager_rewriter_mode(runtime_key)
    planner = _SemanticPlannerAdapter(app)
    search = _OvermindSearchAdapter(app, user_text=text)
    composer = _ResponseComposerAdapter(app) if composer_mode in {"shadow", "publish"} else None
    manager_rewriter = _ManagerRewriterAdapter(app) if manager_rewriter_mode in {"shadow", "publish"} else None
    processor = TurnProcessor(
        planner=planner,
        search_service=search,
        conversation=_ConversationAdapter(app, context=context),
        operator=_OperatorAdapter(),
        trace=_TraceAdapter(
            app,
            user_id=user_id,
            channel=channel,
            planner=planner,
            user_text=text,
        ),
        journal=_JournalAdapter(app, user_id=user_id, channel=channel),
        response_composer=composer,
        response_composer_mode=composer_mode,
        manager_rewriter=manager_rewriter,
        manager_rewriter_mode=manager_rewriter_mode,
    )
    result = await processor.process_async(context, v2_state)
    _attach_turn_prompt_provenance(result, planner=planner, search=search)
    if not result.execution.ok:
        answer = result.response_text or _v2_failure_text(result, v2_state)
        failure_state = v2_state
        should_save_failure_state = False
        if answer.rstrip().endswith("Передать оператору запрос?"):
            failure_state = apply_state_delta(
                failure_state,
                StateDelta(
                    operator_offered=True,
                    pending_followup=SELECTED_LIVE_FACT_CONSENT_FOLLOWUP,
                    contact_consent=False,
                ),
            )
            should_save_failure_state = True
        if result.action == TurnAction.SEARCH:
            failure_state = _state_with_failed_search_retry(failure_state, result, answer)
            should_save_failure_state = True
        if commit and should_save_failure_state:
            await store.save(user_id, _merge_runtime_namespace_envelope(legacy_state, _canonical_v2_envelope(failure_state)))
        return {
            "ok": False,
            "error": "v2_provider_error" if result.execution.error_code else "v2_runtime_error",
            "error_type": result.execution.error_code,
            "answer": answer,
            "intent": "safe_upstream_fallback",
            "awaiting_phone": False,
            "handoff_to_operator": False,
            "selected_option": v2_state.selected_option_name,
            "buttons": [],
            "meta": {"channel": channel, "runtime": runtime_version, **({"engine": engine_version} if engine_version else {}), "trace": _safe_trace(result)},
        }
    shadow = _card_reformatter_shadow(result.state.get("visible_options") if isinstance(result.state, dict) else (), result.semantic_plan.intent or v2_state.active_topic or "life")
    if shadow:
        result.trace["card_reformatter_shadow"] = shadow
        if isinstance(result.trace.get("runtime_summary"), dict):
            result.trace["runtime_summary"]["card_reformatter_shadow"] = shadow
    new_state = _merge_runtime_namespace_envelope(legacy_state, _canonical_v2_envelope(result.state))
    if commit:
        await store.save(user_id, new_state)
    public_intent = result.response_plan.answer_kind if result.response_plan.answer_kind != "generic" else _intent_for_action(result)
    awaiting_phone = False
    if is_pending_contact_name(result.state.get("pending_followup"), contact_consent=bool(result.state.get("contact_consent"))):
        public_intent = "collect_contact_name"
    elif result.action == TurnAction.ACCEPT_OPERATOR and is_pending_contact_phone(result.state.get("pending_followup"), contact_consent=bool(result.state.get("contact_consent"))):
        public_intent = "collect_contact_phone"
        awaiting_phone = True
    return {
        "ok": True,
        "answer": result.response_text,
        "intent": public_intent,
        "awaiting_phone": awaiting_phone,
        "handoff_to_operator": False,
        "selected_option": result.state.get("selected_option_name"),
        "buttons": [],
        "turn_decision": {"stage": result.stage.value, "action": result.action.value},
        "meta": {"channel": channel, "runtime": runtime_version, **({"engine": engine_version} if engine_version else {}), "trace": _safe_trace(result)},
    }


class _SemanticPlannerAdapter:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.last_planner_plan: dict[str, Any] = {}
        self.prompt_provenance: dict[str, Any] | None = None

    async def plan(self, context: SafeTurnContext, state: ConversationState) -> TurnPlan:
        client = self.app.get("overmind_client") if hasattr(self.app, "get") else self.app["overmind_client"]
        session = await client.ensure_session() if hasattr(client, "ensure_session") else None
        legacy_state = _v2_to_planner_legacy_state(state)
        planner_state = safe_planner_state(context.user_text, legacy_state)
        last_response_text = str(legacy_state.get("last_response_text") or "")[:1200]
        pending_scenario = _pending_scenario_for_planner(state)
        planner_kwargs = {
            "user_text": context.user_text,
            "state": planner_state,
            "last_turn": {"bot_question": last_response_text, "client_answer": context.user_text},
            "last_response_text": last_response_text,
            "search_response_text": json.dumps(legacy_state.get("last_search_snapshot") or {}, ensure_ascii=False)[:1200],
            "visible_response_text": _visible_options_context(state),
            "pending_scenario": pending_scenario,
            "selected_object": _selected_object_context(state),
            "dialog_focus": to_jsonable(state.dialog_focus),
            "allowed_subjects": list(ALLOWED_SUBJECTS),
            "allowed_facts": list(ALLOWED_FACTS),
            "subject_fact_map": {key: list(value) for key, value in SUBJECT_FACT_MAP.items()},
            "dynamic_fields": _dynamic_fields_context(state),
            "model": str(legacy_state.get("planner_model") or os.getenv("NMBOT_DIALOG_PLANNER_MODEL", "")) or None,
        }
        if _intent_plan_version() == "v3":
            self.prompt_provenance = build_prompt_provenance([
                identity_from_text("semantic_planner", "followup_intent_classifier.py:INTENT_PLAN_V3_PROMPT", followup_intent_classifier.INTENT_PLAN_V3_PROMPT)
            ], coverage="complete")
            plan = await followup_intent_classifier.plan_intent_v3(session, **planner_kwargs)
            raw_plan = dict(plan) if isinstance(plan, dict) else {}
            contract_plan = {key: value for key, value in raw_plan.items() if key in IntentPlanV3.__dataclass_fields__}
            executable = compile_executable_turn_v3(contract_plan, state, query_text=context.user_text, allowed_facts=ALLOWED_FACTS)
            raw_plan.update(executable.trace_metadata)
            self.last_planner_plan = raw_plan
            return _inherit_selected_scope(executable, state)
        self.prompt_provenance = build_prompt_provenance([
            identity_from_text("semantic_planner", "followup_intent_classifier.py:DIALOG_STATE_PLANNER_PROMPT", followup_intent_classifier.DIALOG_STATE_PLANNER_PROMPT)
        ], coverage="complete")
        plan = await followup_intent_classifier.plan_dialog_state(
            session,
            **planner_kwargs,
        )
        self.last_planner_plan = dict(plan) if isinstance(plan, dict) else {}
        normalized = normalize_semantic_planner_result(plan if isinstance(plan, dict) else {}, available_fact_fields=ALLOWED_FACTS)
        normalized = _keep_transition_accepted_legacy_operation(normalized, plan if isinstance(plan, dict) else {})
        normalized = _drop_legacy_search_reference(normalized, plan if isinstance(plan, dict) else {})
        decision = derive_runtime_decision(normalized, planner_state)
        semantic = _semantic_plan_from_semantic_result(normalized, decision, plan if isinstance(plan, dict) else {}, query_text=context.user_text)
        return _inherit_selected_scope(semantic, state)


class _OvermindSearchAdapter:
    def __init__(self, app: Any, *, user_text: str = "") -> None:
        self.app = app
        self.user_text = str(user_text or "").strip()
        self.last_attempts: tuple[dict[str, Any], ...] = ()
        self.last_fresh_facts: tuple[str, ...] = ()
        self.last_enrichment_trace: dict[str, Any] = {}
        self.last_enrichment_error_code: str | None = None
        self.last_enriched_cache_entry: EnrichedCardCacheEntry | None = None
        self.last_shortlist_cache_entries: tuple[EnrichedCardCacheEntry, ...] = ()
        self.prompt_provenance: dict[str, Any] | None = None

    def _record_search_prompt(self, prompt: str) -> None:
        item = identity_from_text("search", "prompts/v2_search_mcp.txt", prompt)
        self.prompt_provenance = merge_prompt_provenance(
            self.prompt_provenance,
            build_prompt_provenance([item], coverage="complete"),
            coverage="complete",
        )

    async def search(self, plan: TurnPlan, state: ConversationState, context: SafeTurnContext | None = None) -> SearchResult:
        client = self.app["overmind_client"]
        safe_context = context or SafeTurnContext(conversation_ref="local", user_text=self.user_text or str(plan.query_text or ""))
        contract = build_search_request(plan, state, safe_context)
        retrieval_contract = build_candidate_retrieval_request(contract)
        prompt = load_v2_search_prompt()
        self._record_search_prompt(prompt)
        result, primary_attempts = await self._search_once(client, retrieval_contract, prompt=prompt, validation_contract=contract)
        self.last_attempts = primary_attempts
        self.last_shortlist_cache_entries = ()
        filled = result.shortlist(limit=contract.count)
        if contract.count > 1 and 0 < len(filled) < contract.count:
            remaining = int(contract.count) - len(filled)
            supplemental_contract = replace(
                contract,
                count=remaining,
                excluded_names=(*contract.excluded_names, *[card.name for card in (*result.facts, *result.near)]),
            )
            supplemental_retrieval_contract = build_candidate_retrieval_request(supplemental_contract)
            try:
                supplemental, supplemental_attempts = await self._search_once(client, supplemental_retrieval_contract, prompt=prompt, validation_contract=supplemental_contract)
                merged = _merge_underfilled_search_result(result, supplemental, limit=int(contract.count))
                added = max(0, len(merged.shortlist(limit=contract.count)) - len(filled))
                status = "filled" if added else "unchanged"
                result = merged if added else result
                self.last_attempts = (*primary_attempts, *supplemental_attempts, _underfilled_attempt(status=status, requested=remaining, added=added))
            except Exception as exc:
                self.last_attempts = (*primary_attempts, _underfilled_attempt(status="failed", requested=remaining, added=0, error_code=_safe_supplemental_error_code(exc)))
        if contract.search_mode == "broad":
            result = await self._enrich_shortlist_top_options(client, result, contract)
        return result

    async def _enrich_shortlist_top_options(self, client: Any, result: SearchResult, contract: Any) -> SearchResult:
        """Fetch exact full cards for up to three visible shortlist options."""
        cards = result.shortlist(limit=min(3, int(contract.count or 3)))
        if not cards:
            return result

        async def gateway(request_data: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
            return await _run_v2_low_level_gateway(client, request_data)

        try:
            enriched, meta = await enrich_search_result_top_options(
                result,
                contract.response_viewpoint,
                gateway,
                base_viewpoint=contract.base_viewpoint,
                max_options=len(cards),
                timeout=max(0.2, _safe_float_env("NMBOT_V2_ENRICHMENT_ITEM_TIMEOUT", 20.0)),
                facts_needed=_shortlist_enrichment_facts(contract.response_viewpoint),
            )
            enriched_cards = enriched.shortlist(limit=len(cards))
            applied_indexes = {
                int(item.get("idx") or 0)
                for item in (meta.get("items") if isinstance(meta.get("items"), list) else [])
                if isinstance(item, dict) and item.get("applied") is True
            }
            self.last_shortlist_cache_entries = tuple(
                _enriched_cache_entry(card, contract.response_viewpoint)
                for index, card in enumerate(enriched_cards, start=1)
                if index in applied_indexes
            )
            self.last_attempts = (*self.last_attempts, {
                "stage": "shortlist_top_options_enrichment",
                "enabled": True,
                "applied": bool(meta.get("applied")),
                "count": int(meta.get("count") or len(cards)),
                "applied_count": int(meta.get("applied_count") or 0),
            })
            return enriched
        except Exception as exc:
            # The broad result is already valid; enrichment must never make it disappear.
            self.last_attempts = (*self.last_attempts, {
                "stage": "shortlist_top_options_enrichment",
                "enabled": True,
                "applied": False,
                "count": len(cards),
                "applied_count": 0,
                "skipped": exc.__class__.__name__,
            })
            return result

    async def _search_once(self, client: Any, contract: Any, *, prompt: str, validation_contract: Any | None = None) -> tuple[SearchResult, tuple[dict[str, Any], ...]]:
        strict_contract = validation_contract or contract
        request_data = build_v2_search_request_data(contract, prompt=prompt, model=SEARCH_MODEL)
        raw, meta = await _run_v2_low_level_gateway(client, request_data)
        attempts = _attempts_from_meta(meta)
        if isinstance(meta, dict) and (meta.get("_safe_fallback") or meta.get("_upstream_error")):
            raise RuntimeError("v2_search_gateway_not_ok")
        parsed, parse_errors = parse_strict_json(raw)
        if parsed is None:
            raise RuntimeError("v2_search_parse_failed:" + ",".join(parse_errors))
        parsed = normalize_search_output(parsed, strict_contract)
        validation = validate_search_output(parsed, strict_contract)
        if is_candidate_retrieval_request(contract):
            attempts = (*attempts, {
                "stage": "candidate_first_retrieval",
                "enabled": True,
                "field": "location",
                "strict_validation_counts": dict(validation.get("counts") or {}),
            })
        if validation.get("status") != "valid" or validation.get("errors") or validation.get("warnings"):
            attempts = (*attempts, _search_validation_report_attempt(validation))
        result = normalize_search_result(parsed)
        result = _enforce_exact_named_search_scope(result, strict_contract)
        if not result.params:
            result = SearchResult(facts=result.facts, near=result.near, missing=result.missing, params=dict(strict_contract.effective_hard), summary=result.summary)
        return result, attempts

    async def enrich_selected(self, option: OptionCard, state: ConversationState, plan: TurnPlan) -> OptionCard:
        self.last_fresh_facts = ()
        self.last_enrichment_trace = {}
        self.last_enrichment_error_code = None
        self.last_enriched_cache_entry = None
        viewpoint = plan.intent or state.active_topic or "self_use"
        base = merge_option_cards(option, state.selected_enriched) if state.selected_enriched and state.selected_enriched.name == option.name else option
        requested = plan.requested_facts or plan.facts_needed
        split = split_requested_facts(requested, base)
        if requested and not split.missing:
            return base
        facts_needed = _selected_enrichment_facts(split.missing or plan.facts_needed, viewpoint)
        force_refresh = _enrichment_refresh_requested(plan)
        cached = _state_enriched_cache_lookup(state, base, viewpoint, facts_needed, force_refresh=force_refresh)
        if cached is not None:
            self.last_enriched_cache_entry = cached
            loaded = {str(item).strip().lower() for item in cached.loaded_facts if str(item).strip()}
            self.last_fresh_facts = tuple(fact for fact in facts_needed if fact in loaded)
            self.last_enrichment_trace = {"stage": "v2_option_enrichment", "applied": True, "source": "state_cache", "facts": list(self.last_fresh_facts)}
            return merge_option_cards(base, cached.card)
        enriched, meta = await _get_or_fetch_v2_enriched_option(self.app, base, state, viewpoint, facts_needed=facts_needed, force_refresh=force_refresh)
        if meta.get("applied") is True and meta.get("source") != "cache":
            present = set(present_fact_names(enriched))
            requested_set = {str(fact).strip().lower() for fact in requested if str(fact).strip().lower() in ALLOWED_FACTS}
            # The gateway currently returns only model text plus service metadata,
            # not the raw MCP tool result.  Therefore a model-produced apartment
            # count has no verifiable provenance and must not become a fresh fact.
            # Keep it missing until the gateway exposes allowlisted MCP evidence.
            self.last_fresh_facts = tuple(
                fact
                for fact in facts_needed
                if fact in requested_set and fact in present and fact != "apartment_inventory"
            )
        if meta.get("applied") is True:
            self.last_enriched_cache_entry = _enriched_cache_entry(
                enriched,
                viewpoint,
                loaded_facts=(*present_fact_names(enriched), *facts_needed),
                fetched_at=meta.get("ts"),
            )
        skipped = str(meta.get("skipped") or "")
        remaining = split_requested_facts(requested, enriched, fresh_facts=self.last_fresh_facts).missing
        outcome = _selected_enrichment_outcome(meta, requested_missing=bool(remaining))
        if outcome in {"timeout", "technical_failure", "unavailable"}:
            self.last_enrichment_error_code = f"selected_enrichment_{outcome}"
        self.last_enrichment_trace = {
            "stage": "v2_option_enrichment",
            "enabled": True,
            "applied": bool(meta.get("applied")),
            "count": 1,
            "applied_count": 1 if meta.get("applied") else 0,
            "requested_facts": list(facts_needed),
            "fresh_facts": list(self.last_fresh_facts),
            "outcome": outcome,
            "items": [{
                "idx": 1,
                "applied": bool(meta.get("applied")),
                "source": str(meta.get("source") or "base"),
                **({"skipped": skipped} if skipped else {}),
            }],
        }
        return enriched


def _adapter_normalized_object_name(value: Any) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"\b(?:жк|жилой комплекс)\b", " ", text)
    return re.sub(r"[^a-zа-я0-9]+", "", text)


def _enforce_exact_named_search_scope(result: SearchResult, contract: Any) -> SearchResult:
    goal = getattr(contract, "search_goal", None)
    if not isinstance(goal, dict) or goal.get("lookup_mode") != "exact_named_object":
        return result
    reference = _adapter_normalized_object_name(goal.get("entity_reference"))
    if not reference:
        return result

    def same(card: OptionCard) -> bool:
        return _adapter_normalized_object_name(card.name) == reference

    facts = tuple(card for card in result.facts if same(card))
    near = tuple(card for card in result.near if same(card))
    if len(facts) == len(result.facts) and len(near) == len(result.near):
        return result
    return SearchResult(facts=facts, near=near, missing=result.missing, params=result.params, summary=result.summary)


def _merge_underfilled_search_result(primary: SearchResult, supplemental: SearchResult, *, limit: int) -> SearchResult:
    fact_keys: set[str] = set()
    facts: list[OptionCard] = []
    for card in (*primary.facts, *supplemental.facts):
        key = _search_result_name_key(card.name)
        if not key or key in fact_keys:
            continue
        fact_keys.add(key)
        facts.append(card)
        if len(facts) >= limit:
            break
    near: list[OptionCard] = []
    near_keys: set[str] = set()
    near_limit = max(0, int(limit) - len(facts))
    if near_limit:
        for card in (*primary.near, *supplemental.near):
            key = _search_result_name_key(card.name)
            if not key or key in fact_keys or key in near_keys:
                continue
            near_keys.add(key)
            near.append(card)
            if len(near) >= near_limit:
                break
    return SearchResult(
        facts=tuple(facts),
        near=tuple(near),
        missing=_dedupe_texts((*primary.missing, *supplemental.missing)),
        params=dict(primary.params),
        summary=primary.summary,
    )


def _search_result_name_key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().replace("ё", "е").split())


def _dedupe_texts(values: tuple[Any, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold().replace("ё", "е")
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return tuple(out)


def _underfilled_attempt(*, status: str, requested: int, added: int, error_code: str | None = None) -> dict[str, Any]:
    marker: dict[str, Any] = {
        "stage": "underfilled_search_fill",
        "status": status if status in {"filled", "unchanged", "failed"} else "failed",
        "requested": max(0, int(requested)),
        "added": max(0, int(added)),
    }
    if error_code:
        marker["error_code"] = error_code
    return marker


def _safe_supplemental_error_code(exc: Exception) -> str:
    text = str(exc)
    if text.startswith("v2_search_gateway_not_ok"):
        return "gateway_not_ok"
    if text.startswith("v2_search_parse_failed"):
        return "parse_failed"
    if text.startswith("v2_search_contract_invalid"):
        return "contract_invalid"
    return exc.__class__.__name__


def _selected_enrichment_outcome(meta: dict[str, Any], *, requested_missing: bool) -> str:
    if meta.get("applied") is True and not requested_missing:
        return "applied"
    if meta.get("applied") is True and requested_missing:
        return "not_found"
    skipped = str(meta.get("skipped") or "").strip().lower()
    if skipped in {"empty_result", "empty_enrichment"}:
        return "not_found"
    if skipped in {"timeout", "timeouterror"}:
        return "timeout"
    if skipped in {"client_missing", "missing_key"}:
        return "unavailable"
    return "technical_failure"

class _ConversationAdapter:
    def __init__(self, app: Any, *, context: SafeTurnContext) -> None:
        self.app = app
        self.context = context

    async def answer(self, plan: TurnPlan, state: ConversationState) -> ExecutionResult:
        return ExecutionResult(ok=True, message=build_native_conversation_answer(plan, state, self.context.user_text), bridge_status="v2_native_conversation")


class _OperatorAdapter:
    async def prepare(self, plan: TurnPlan, state: ConversationState) -> ExecutionResult:
        if plan.operator_consent is True:
            return ExecutionResult(ok=True, message="operator_contact_capture")
        return ExecutionResult(ok=True, message="operator_offer")


class _TraceAdapter:
    def __init__(self, app: Any, *, user_id: str, channel: str, planner: _SemanticPlannerAdapter, user_text: str = "") -> None:
        self.app = app
        self.user_id = user_id
        self.channel = channel
        # План читается в момент записи transition: к этому времени runtime уже
        # завершил единственный вызов planner.
        self.planner = planner
        self.user_text = user_text
        self._planner_trace_written = False

    def record(self, event: dict) -> None:
        bucket = self.app.setdefault("nmbot_v2_trace", []) if hasattr(self.app, "setdefault") else None
        if isinstance(bucket, list):
            bucket.append({"ts": time.time(), "channel": self.channel, "event": _safe_nested(event)})
        self._write_planner_trace(event)

    def _write_planner_trace(self, event: dict) -> None:
        if self.channel != "jivo" or self._planner_trace_written or event.get("event") != "transition":
            return
        try:
            action = _api_action_from_v2_action(str(event.get("action") or ""))
            decision = TurnDecision(action=action, target="current_options" if action == "answer_current_options" else "none", search_policy="required" if action == "search" else "forbidden")
            append_safe_planner_trace(
                session_key=self.user_id,
                channel=self.channel,
                plan=dict(self.planner.last_planner_plan),
                decision=decision,
                user_text=self.user_text,
            )
            self._planner_trace_written = True
        except Exception:
            return


class _JournalAdapter:
    def __init__(self, app: Any, *, user_id: str, channel: str) -> None:
        self.app = app
        self.user_id = user_id
        self.channel = channel

    def append(self, result: TurnResult) -> None:
        bucket = self.app.setdefault("nmbot_v2_journal", []) if hasattr(self.app, "setdefault") else None
        if isinstance(bucket, list):
            bucket.append({"channel": self.channel, "stage": result.stage.value, "action": result.action.value, "ok": result.execution.ok})

def _semantic_plan_from_planner(plan: dict[str, Any], *, query_text: str = "") -> SemanticPlan:
    operation = str(plan.get("operation") or "").strip()
    selected = plan.get("selected_option_name") or plan.get("selected_option") or plan.get("reference")
    if not operation:
        action = str(plan.get("action") or plan.get("dialog_action") or "").strip()
        if action == "answer_current_options" and str(plan.get("dialog_action") or "").strip() == "select_option" and str(plan.get("scope") or "").strip() == "one" and selected:
            action = "select_option"
        operation = {
            "search": "search",
            "new_search": "search",
            "answer_current_options": "current_options",
            "current_options": "current_options",
            "compare_options": "compare_current",
            "compare_current": "compare_current",
            "recommend_options": "current_options",
            "recommend_current": "current_options",
            "answer_open_question": "answer_open_question",
            "operator_contact": "operator",
            "operator_live_check": "operator",
            "off_topic": "off_topic",
            "select_option": "select_option",
            "recover_dialogue": "freeform",
            "clarify": "freeform",
        }.get(action, "freeform")
    constraints = plan.get("constraints_delta") if isinstance(plan.get("constraints_delta"), dict) else plan.get("constraints_patch")
    requested_source = plan.get("requested_facts") if isinstance(plan.get("requested_facts"), list) else []
    needed_source = plan.get("facts_needed") if isinstance(plan.get("facts_needed"), list) else plan.get("missing_fields") if isinstance(plan.get("missing_fields"), list) else []
    requested_facts = tuple(str(x) for x in requested_source if str(x).strip() in ALLOWED_FACTS)
    facts_needed = tuple(str(x) for x in needed_source if str(x).strip() in ALLOWED_FACTS)
    operator_contact = plan.get("operator_contact") if isinstance(plan.get("operator_contact"), dict) else {}
    consent = str(operator_contact.get("consent") or "").strip().lower()
    direct_consent = plan.get("operator_consent")
    return SemanticPlan(
        operation=operation,
        query_text=_redact(query_text) if query_text else None,
        intent=str(plan.get("intent") or "") or None,
        constraints_delta=constraints if isinstance(constraints, dict) else {},
        reference=str(plan.get("reference") or "") or None,
        selected_option_name=str(selected or "") or None,
        scope=str(plan.get("scope") or "") or None,
        operator_consent=bool(direct_consent) if isinstance(direct_consent, bool) else True if consent == "granted" else False if consent == "refused" else None,
        explicit_operator_request=bool(plan.get("explicit_operator_request")),
        operator_reason=str(plan.get("operator_reason") or "") or None,
        followup_outcome=str(plan.get("followup_outcome") or "") or None,
        resolved_subject=str(plan.get("resolved_subject") or "") if str(plan.get("resolved_subject") or "") in ALLOWED_SUBJECTS else None,
        resolved_intent=str(plan.get("resolved_intent") or "") or None,
        requested_facts=requested_facts,
        facts_needed=tuple(fact for fact in facts_needed if not requested_facts or fact in requested_facts),
        requires_enrichment=bool(plan.get("requires_enrichment") or facts_needed),
        focus_action=str(plan.get("focus_action") or "keep") if str(plan.get("focus_action") or "keep") in {"keep", "switch", "clear", "clarify"} else "keep",
        domain_relation=str(plan.get("domain_relation") or "unknown") if str(plan.get("domain_relation") or "unknown") in {"in_domain", "off_topic", "unknown"} else "unknown",
        confidence=_safe_float(plan.get("confidence"), 1.0),
        clarification=str(plan.get("clarification") or plan.get("clarification_question") or "") or None,
        facets=_merge_semantic_facets(
            plan.get("scenario_needs"),
            plan.get("requested_comparison"),
            plan.get("facets"),
        ),
        fresh_search=plan.get("requests_new_objects") is True,
    )


def _semantic_plan_from_semantic_result(
    semantic: SemanticPlannerResult,
    decision: DerivedPlannerDecision,
    raw_plan: dict[str, Any],
    *,
    query_text: str = "",
) -> SemanticPlan:
    """Build the V2 runtime contract without a canonical planner dict round-trip."""

    operation = _operation_from_runtime_decision(decision, raw_plan)
    requested_facts = tuple(fact for fact in decision.requested_facts if fact in ALLOWED_FACTS)
    facts_needed = tuple(fact for fact in decision.facts_needed if fact in ALLOWED_FACTS and (not requested_facts or fact in requested_facts))
    constraints_delta = decision.constraints_patch if isinstance(decision.constraints_patch, dict) else {}
    raw_operation = str(raw_plan.get("operation") or "").strip()
    raw_constraints = raw_plan.get("constraints_delta") if isinstance(raw_plan.get("constraints_delta"), dict) else raw_plan.get("constraints_patch")
    if raw_operation in {"search", "new_search", "refine_search", "expand_more"} and isinstance(raw_constraints, dict) and not any(
        bool(fields) for fields in constraints_delta.values() if isinstance(fields, dict)
    ):
        constraints_delta = raw_constraints
    return SemanticPlan(
        operation=operation,
        query_text=_redact(query_text) if query_text else None,
        intent=decision.intent or semantic.scenario_change or None,
        constraints_delta=constraints_delta,
        reference=str(semantic.named_object_reference or semantic.selected_reference or "") or None,
        selected_option_name=decision.selected_option_name,
        scope=_scope_from_runtime_decision(decision, raw_plan),
        operator_consent=_operator_consent_from_semantic_raw(raw_plan),
        explicit_operator_request=bool(raw_plan.get("explicit_operator_request")),
        operator_reason=str(raw_plan.get("operator_reason") or decision.reason or "") or None,
        followup_outcome=str(raw_plan.get("followup_outcome") or "") or None,
        resolved_subject=decision.resolved_subject if decision.resolved_subject in ALLOWED_SUBJECTS else None,
        resolved_intent=decision.resolved_intent,
        requested_facts=requested_facts,
        facts_needed=facts_needed,
        requires_enrichment=bool(decision.needs_enrichment or facts_needed),
        focus_action=decision.focus_action if decision.focus_action in {"keep", "switch", "clear", "clarify"} else "keep",
        domain_relation=semantic.domain_relation if semantic.domain_relation in {"in_domain", "off_topic", "unknown"} else "unknown",
        confidence=decision.confidence,
        clarification=decision.clarification or None,
        facets=_merge_semantic_facets(semantic.scenario_needs, semantic.requested_comparison),
        fresh_search=semantic.requests_new_objects is True,
    )


def _intent_plan_version() -> str:
    value = str(os.getenv("NMBOT_INTENT_PLAN_VERSION", "v2") or "v2").strip().lower()
    return "v3" if value == "v3" else "v2"


def _safe_semantic_plan_from_intent_v3(*, reason: str, raw_plan: dict[str, Any], clarification: str | None = None, query_text: str = "") -> SemanticPlan:
    raw_plan.setdefault("intent_plan_v3_adapter", {})
    raw_plan["intent_plan_v3_adapter"].update({"fallback_used": True, "reason": reason})
    return SemanticPlan(
        operation="freeform",
        query_text=_redact(query_text) if query_text else None,
        confidence=0.0,
        clarification=clarification or None,
        scope="all",
    )


def _semantic_plan_from_intent_plan_v3(raw_plan: dict[str, Any], state: ConversationState, *, query_text: str = "") -> SemanticPlan:
    contract_plan = {key: value for key, value in raw_plan.items() if key in IntentPlanV3.__dataclass_fields__}
    validation = validate_intent_plan_v3(contract_plan, state, allowed_facts=ALLOWED_FACTS)
    raw_plan["intent_plan_v3_validation"] = {
        "ok": validation.ok,
        "errors": list(validation.errors),
        "repairable": validation.repairable,
    }
    if not validation.ok or validation.plan is None:
        return _safe_semantic_plan_from_intent_v3(
            reason="validation_failed:" + ",".join(validation.errors),
            raw_plan=raw_plan,
            clarification=str(raw_plan.get("clarification") or "").strip() or None,
            query_text=query_text,
        )

    plan = validation.plan
    transition = derive_transition_v3(plan, state)
    raw_plan["intent_plan_v3_transition"] = _transition_decision_to_dict(transition)
    if not transition.accepted:
        return _safe_semantic_plan_from_intent_v3(
            reason="transition_rejected:" + str(transition.error_code or "unknown"),
            raw_plan=raw_plan,
            clarification=plan.clarification,
            query_text=query_text,
        )

    operation = _operation_from_intent_goal_v3(plan.goal)
    requested_facts = tuple(fact for fact in plan.requested_facts if fact in ALLOWED_FACTS)
    viewpoint = str(plan.viewpoint or "unchanged").strip()
    facets = [] if viewpoint == "unchanged" else [viewpoint]
    intent = "mortgage" if viewpoint == "financing" else (viewpoint if viewpoint != "unchanged" else None)
    selected = plan.selected_option_name
    named = plan.named_object_reference
    constraints_delta = plan.constraints_delta if isinstance(plan.constraints_delta, dict) else {}
    return SemanticPlan(
        operation=operation,
        query_text=_redact(query_text) if query_text else None,
        intent=intent,
        constraints_delta=constraints_delta,
        reference=named or selected,
        selected_option_name=selected,
        scope="one" if selected or named else ("all" if plan.goal in {IntentGoal.ANSWER_CURRENT, IntentGoal.COMPARE_CURRENT, IntentGoal.RECOMMEND_CURRENT} else "unknown"),
        operator_consent=plan.operator_consent,
        explicit_operator_request=plan.explicit_operator_request,
        operator_reason="explicit_operator_request" if plan.explicit_operator_request else None,
        followup_outcome="resume_contact" if plan.goal == IntentGoal.RESUME_PENDING else None,
        requested_facts=requested_facts,
        facts_needed=requested_facts,
        requires_enrichment=bool(requested_facts and plan.goal == IntentGoal.ANSWER_SELECTED),
        focus_action="switch" if selected or named else "keep",
        domain_relation="off_topic" if plan.goal == IntentGoal.OFF_TOPIC else "in_domain",
        confidence=plan.confidence,
        clarification=plan.clarification,
        facets=facets,
        fresh_search=plan.goal == IntentGoal.EXPAND_SEARCH,
    )


def _operation_from_intent_goal_v3(goal: IntentGoal) -> str:
    if goal in {IntentGoal.NEW_SEARCH, IntentGoal.REFINE_SEARCH, IntentGoal.EXPAND_SEARCH}:
        return "search"
    if goal == IntentGoal.LOOKUP_OBJECT:
        return "lookup_object"
    if goal == IntentGoal.ANSWER_OPEN_QUESTION:
        return "answer_open_question"
    if goal in {IntentGoal.ANSWER_CURRENT, IntentGoal.COMPARE_CURRENT, IntentGoal.RECOMMEND_CURRENT}:
        return "current_options"
    if goal == IntentGoal.ANSWER_SELECTED:
        return "select_option"
    if goal == IntentGoal.OPERATOR:
        return "operator"
    if goal == IntentGoal.OFF_TOPIC:
        return "off_topic"
    return "freeform"


def _transition_decision_to_dict(decision: TransitionDecision) -> dict[str, Any]:
    return {
        "stage": decision.stage.value,
        "action": decision.action.value,
        "accepted": decision.accepted,
        "error_code": decision.error_code,
    }


def _merge_semantic_facets(*sources: Any) -> list[str]:
    out: list[str] = []
    for source in sources:
        if source in (None, "", [], {}, ()): 
            continue
        raw_items = [key for key, enabled in source.items() if enabled] if isinstance(source, dict) else source if isinstance(source, (list, tuple, set)) else [source]
        for item in raw_items:
            text = str(item or "").strip().lower()
            text = SCENARIO_NEED_ALIASES.get(text, text)
            if text and text not in out:
                out.append(text)
    return out[:12]


def _operation_from_runtime_decision(decision: DerivedPlannerDecision, raw_plan: dict[str, Any] | None = None) -> str:
    if "invalid_operation" in decision.errors:
        return "__invalid_semantic_operation__"
    raw_operation = str((raw_plan or {}).get("operation") or "").strip()
    raw_dialog_action = str((raw_plan or {}).get("dialog_action") or "").strip()
    raw_confidence = _safe_float((raw_plan or {}).get("confidence"), 1.0)
    raw_canonical_errors = (raw_plan or {}).get("canonical_errors")
    if not raw_operation and "action" not in (raw_plan or {}) and raw_dialog_action in {"new_search", "update_search", "expand_more_options"} and not (raw_plan or {}).get("fallback_used") and raw_confidence >= 0.5 and (raw_plan or {}).get("canonical_valid") is not False and not raw_canonical_errors:
        return "search"
    if raw_operation in {"search", "new_search", "refine_search", "expand_more", "current_options", "answer_current_options", "answer_open_question", "off_topic"}:
        if raw_operation in {"search", "new_search", "refine_search", "expand_more"}:
            return "search"
        if raw_operation in {"current_options", "answer_current_options"}:
            return "current_options"
        if raw_operation == "answer_open_question":
            return "answer_open_question"
        return "off_topic"
    if raw_operation in {"financing", "clarify_financing"} and decision.action == "answer_current_options":
        return raw_operation
    if decision.action == "search":
        return "search"
    if decision.action == "lookup_object":
        return "lookup_object"
    if decision.action == "off_topic":
        return "off_topic"
    if decision.action == "operator_contact":
        return "operator"
    if decision.action == "answer_current_options":
        if decision.dialog_action == "select_option" and decision.scope == "one" and decision.selected_option_name:
            return "select_option"
        return "current_options"
    if decision.action in {"clarify", "recover_dialogue"}:
        return "freeform"
    return "__invalid_semantic_operation__"


def _scope_from_runtime_decision(decision: DerivedPlannerDecision, raw_plan: dict[str, Any]) -> str | None:
    raw_operation = str(raw_plan.get("operation") or "").strip()
    raw_scope = str(raw_plan.get("scope") or "").strip()
    if raw_operation in {"financing", "clarify_financing"} and raw_scope in {"one", "all", "unknown"}:
        return raw_scope
    return decision.scope if decision.scope in {"one", "all", "unknown"} else None


def _operator_consent_from_semantic_raw(raw_plan: dict[str, Any]) -> bool | None:
    direct = raw_plan.get("operator_consent")
    if isinstance(direct, bool):
        return direct
    operator_contact = raw_plan.get("operator_contact") if isinstance(raw_plan.get("operator_contact"), dict) else {}
    consent = str(operator_contact.get("consent") or "").strip().lower()
    if consent == "granted":
        return True
    if consent == "refused":
        return False
    return None


def _keep_transition_accepted_legacy_operation(semantic: SemanticPlannerResult, raw_plan: dict[str, Any]) -> SemanticPlannerResult:
    raw_operation = str(raw_plan.get("operation") or "").strip()
    if raw_operation not in {"financing", "clarify_financing", "compare_current", "compare_options", "answer_open_question", "off_topic"}:
        return semantic
    if "invalid_operation" not in semantic.errors:
        return semantic
    errors = tuple(error for error in semantic.errors if error != "invalid_operation")
    legacy_operation = "current_options" if raw_operation in {"financing", "clarify_financing", "compare_current", "compare_options"} else raw_operation
    return replace(semantic, raw_legacy_operation=legacy_operation, errors=errors)


def _drop_legacy_search_reference(semantic: SemanticPlannerResult, raw_plan: dict[str, Any]) -> SemanticPlannerResult:
    raw_operation = str(raw_plan.get("operation") or "").strip()
    has_semantic_selected_reference = "selected_reference" in raw_plan
    if raw_operation not in {"search", "new_search", "refine_search", "expand_more"} or has_semantic_selected_reference:
        return semantic
    if semantic.selected_reference is None:
        return semantic
    return replace(semantic, selected_reference=None)


def _inherit_selected_scope(plan: TurnPlan, state: ConversationState) -> TurnPlan:
    """Keep contextual follow-ups scoped to the selected ЖК.

    The model chooses the meaning/viewpoint. Runtime owns object scope: after
    selection, current-options and financing questions stay on that object
    unless the semantic plan explicitly asks for all current options.
    """
    selected = str(state.selected_option_name or "").strip()
    if isinstance(plan, ExecutableTurn):
        if state.pending_followup in {"contact_name", "contact_phone"} and plan.resolved_intent == "resume_contact":
            return replace(
                plan,
                selected_option_name=plan.selected_option_name or selected or None,
                reference=plan.reference or selected or None,
                scope="one" if selected and state.find_visible_option(selected) else plan.scope,
                explicit_operator_request=True,
            )
        if state.pending_followup == "financing_consent":
            if selected and state.find_visible_option(selected):
                return replace(plan, selected_option_name=plan.selected_option_name or selected, reference=plan.reference or selected, scope="one")
            if state.visible_options:
                return replace(plan, scope=plan.scope if plan.scope == "one" else "all")
            return plan
        if not selected or str(plan.scope or "").strip().lower() == "all" or not state.find_visible_option(selected):
            return plan
        if plan.goal in {IntentGoal.ANSWER_CURRENT, IntentGoal.COMPARE_CURRENT, IntentGoal.RECOMMEND_CURRENT, IntentGoal.ANSWER_OPEN_QUESTION, IntentGoal.OPERATOR}:
            return replace(plan, selected_option_name=plan.selected_option_name or selected, reference=plan.reference or selected, scope="one")
        return plan
    if state.pending_followup in {"contact_name", "contact_phone"} and plan.resolved_intent == "resume_contact":
        return replace(
            plan,
            operation="operator",
            selected_option_name=plan.selected_option_name or selected or None,
            reference=plan.reference or selected or None,
            scope="one" if selected and state.find_visible_option(selected) else plan.scope,
            explicit_operator_request=True,
        )
    if state.pending_followup == "financing_consent":
        if selected and state.find_visible_option(selected):
            return replace(plan, selected_option_name=plan.selected_option_name or selected, reference=plan.reference or selected, scope="one")
        if state.visible_options:
            return replace(plan, scope=plan.scope if plan.scope == "one" else "all")
        return plan
    if not selected or str(plan.scope or "").strip().lower() == "all":
        return plan
    if plan.operation not in {
        "current_options", "answer_current_options", "financing",
        "clarify_financing", "freeform", "conversation",
    }:
        return plan
    if not state.find_visible_option(selected):
        return plan
    is_financing_followup = (
        str(plan.intent or "").strip().lower() in {"mortgage", "financing"}
        or "mortgage" in {str(facet).strip().lower() for facet in plan.facets}
    )
    if is_financing_followup:
        return replace(
            plan,
            operation="financing",
            selected_option_name=selected,
            reference=plan.reference or selected,
            scope="one",
        )
    return replace(
        plan,
        operation="select_option",
        selected_option_name=selected,
        reference=plan.reference or selected,
        scope="one",
    )


def _pending_scenario_for_planner(state: ConversationState) -> dict[str, Any] | None:
    contract = reply_contract_for_pending(state.pending_followup)
    if contract is None:
        return None
    selected = str(state.selected_option_name or (state.selected_enriched.name if state.selected_enriched else "") or "").strip()
    if selected and state.find_visible_option(selected):
        scope = "one"
    else:
        selected = ""
        scope = "all" if state.visible_options else "unknown"
    envelope: dict[str, Any] = {
        "id": contract.id,
        "allowed_reply_outcomes": list(contract.allowed_outcomes),
        "context": {
            "scope": scope,
            "offered_action": dict(contract.planner_context).get("offered_action") or ("verify_financing_conditions" if contract.id == FINANCING_CONSENT_FOLLOWUP else "verify_selected_live_facts"),
        },
    }
    if contract.id == SELECTED_LIVE_FACT_CONSENT_FOLLOWUP:
        requested = state.dialog_focus.last_requested_facts
        envelope["context"]["requested_facts"] = [fact for fact in requested if fact]
    if scope == "one" and selected:
        envelope["context"]["selected_option_name"] = selected
    return envelope


def _exact_visible_option_name(text: str, state: ConversationState) -> str | None:
    def normalize(value: str) -> str:
        value = str(value or "").casefold().replace("ё", "е")
        value = re.sub(r"[«»\"'.,!?():;]+", " ", value)
        value = re.sub(r"\bжк\b", " ", value)
        return " ".join(value.split())

    candidate = normalize(text)
    if not candidate:
        return None
    # Positional selection is deterministic only for a clean standalone
    # reference. Mixed phrases (for example, "1, но дорого") stay with the
    # semantic planner so that extra intent is not discarded.
    ordinal = {
        "1": 0,
        "первый": 0,
        "первый вариант": 0,
        "вариант 1": 0,
        "2": 1,
        "второй": 1,
        "второй вариант": 1,
        "вариант 2": 1,
        "3": 2,
        "третий": 2,
        "третий вариант": 2,
        "вариант 3": 2,
    }
    if candidate in ordinal:
        index = ordinal[candidate]
        return state.visible_options[index].name if index < len(state.visible_options) else None
    for option in state.visible_options:
        if candidate == normalize(option.name):
            return option.name
    return None


def _prefill_selected_visible_option_v2(text: str, state: ConversationState) -> ConversationState:
    selected = _referenced_visible_option_name_v2(text, state)
    if not selected:
        return state
    option = state.find_visible_option(selected)
    if option is None:
        return state
    return replace(state, selected_option_name=selected, selected_enriched=option)


def _referenced_visible_option_name_v2(text: str, state: ConversationState) -> str | None:
    exact = _exact_visible_option_name(text, state)
    if exact:
        return exact

    def normalize(value: str) -> str:
        value = str(value or "").casefold().replace("ё", "е")
        value = re.sub(r"[«»\"'.,!?():;]+", " ", value)
        return " ".join(value.split())

    haystack = f" {normalize(text)} "
    matches: list[str] = []
    for option in state.visible_options:
        name = str(option.name or "").strip()
        if not name:
            continue
        normalized = normalize(name)
        without_prefix = re.sub(r"^жк\s+", "", normalized).strip()
        candidates = {normalized, f"жк {without_prefix}" if without_prefix else normalized}
        if any(f" {candidate} " in haystack for candidate in candidates if candidate):
            matches.append(name)
    if len(matches) == 1:
        return matches[0]
    return None


def _visible_options_context(state: ConversationState) -> str:
    """Give the semantic planner only the bounded, canonical current names."""
    names = [str(option.name or "").strip() for option in state.visible_options[:5]]
    names = [name for name in names if name]
    return f"Текущие варианты: {'; '.join(names)}"[:600] if names else ""


def _selected_object_context(state: ConversationState) -> dict[str, Any]:
    selected = state.selected_enriched or state.find_visible_option(state.selected_option_name)
    if not selected:
        return {}
    return {"canonical_name": selected.name, "present_fact_fields": list(present_fact_names(selected))}


def _dynamic_fields_context(state: ConversationState) -> list[str]:
    selected = state.selected_enriched or state.find_visible_option(state.selected_option_name)
    present = set(present_fact_names(selected))
    return [fact for fact in ALLOWED_FACTS if fact not in present]


def _legacy_to_v2_state(state: dict[str, Any]) -> ConversationState:
    """Читает canonical V2 state или безопасно переносит legacy-поля в V2."""
    namespace = state.get("nmbot_v2")
    if isinstance(namespace, dict):
        base = _safe_v2_state_dict(namespace)
        if "params" in base:
            base["params"] = _safe_params(base.get("params"))
        if "pending_followup" in base:
            base["pending_followup"] = _safe_pending_followup(base.get("pending_followup"))
        if "recent_turns" in base:
            base["recent_turns"] = _safe_recent_turns({}, base)
        if "dialogue_turns" in base and isinstance(base.get("dialogue_turns"), list):
            base["dialogue_turns"] = [
                _safe_turn_pair(item)
                for item in base.get("dialogue_turns", [])
                if isinstance(item, dict)
            ]
        if "operator_declined" in base:
            base["operator_declined"] = bool(base.get("operator_declined"))
        return ConversationState.from_dict(base)

    visible = state.get("visible_options") or state.get("last_options") or []
    selected = state.get("selected_option") if isinstance(state.get("selected_option"), dict) else None
    contact_flow = str(state.get("contact_flow") or "").strip().lower()
    pending_contact = None
    if contact_flow == "awaiting_contact_name":
        pending_contact = "contact_name"
    elif contact_flow == "awaiting_contact_phone" or bool(state.get("awaiting_phone")):
        pending_contact = "contact_phone"
    legacy_contact_name = str(state.get("contact_name") or "").strip()[:80] or None
    legacy_callback_ref = str(state.get("last_callback_ref") or "").strip()[:160] or None
    base = {
        "params": _safe_params(state.get("params", {})),
        "visible_options": [_option_dict_from_legacy(x) for x in visible if isinstance(x, dict)],
        "selected_option_name": (selected or {}).get("name"),
        "selected_enriched": _option_dict_from_legacy(selected) if selected else None,
        "pending_followup": pending_contact or _safe_pending_followup(state.get("pending_followup")),
        "recent_turns": _safe_recent_turns(state, {}),
        "last_assistant_question": _redact(str(state.get("last_bot_question") or ""))[:1000] or None,
        "last_answer_kind": _redact(str(state.get("last_answer_kind") or ""))[:120] or None,
        "operator_declined": bool(state.get("operator_declined")),
        "contact_name": legacy_contact_name,
        "contact_consent": bool(legacy_callback_ref),
        "callback_ref": legacy_callback_ref,
    }
    return ConversationState.from_dict(base)


def _canonical_v2_envelope(v2_state: ConversationState | dict[str, Any]) -> dict[str, Any]:
    raw = v2_state.to_dict() if isinstance(v2_state, ConversationState) else v2_state
    return {"nmbot_v2": _safe_v2_state_dict(copy.deepcopy(raw))}


def _v2_to_planner_legacy_state(state: ConversationState) -> dict[str, Any]:
    visible = [_legacy_dict_from_option(to_jsonable(x)) for x in state.visible_options]
    selected = _legacy_dict_from_option(to_jsonable(state.selected_enriched)) if state.selected_enriched else ({"name": state.selected_option_name} if state.selected_option_name else {})
    last_response_text = ""
    for turn in reversed(state.recent_turns):
        assistant_text = str(turn.get("assistant") or "").strip()
        if assistant_text:
            last_response_text = assistant_text[:1200]
            break
    return {
        "params": dict(state.params),
        "visible_options": visible,
        "last_options": visible,
        "selected_option": selected,
        "pending_followup": {"type": state.pending_followup} if state.pending_followup else {},
        "dialog_window": _dialog_window_from_recent_turns(state.recent_turns),
        "last_turn": _last_turn_from_recent_turns(state.recent_turns),
        "last_response_text": last_response_text,
        "last_bot_question": state.last_assistant_question or "",
        "last_answer_kind": state.last_answer_kind or "",
        "current_options_scope": "one" if state.selected_option_name else "all" if visible else "unknown",
        "dialog_focus": to_jsonable(state.dialog_focus),
        "selected_object": _selected_object_context(state),
        "retry_search": to_jsonable(state.retry_search) if state.retry_search else {},
    }


def _safe_v2_state_dict(data: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "params", "pending_followup", "selected_option_name", "visible_options", "previous_options",
        "last_search", "operator_offered", "operator_declined", "active_topic", "dialog_focus", "selected_enriched", "enriched_card_cache",
        "recent_turns", "dialogue_turns", "last_assistant_question", "last_answer_kind", "already_asked", "answered",
        "contact_name", "contact_phone_redacted", "contact_consent", "callback_ref", "retry_search",
    }
    return {key: _safe_nested(value) for key, value in data.items() if key in allowed}


def _v2_failure_text(result: TurnResult, state: ConversationState) -> str:
    if result.action == TurnAction.SEARCH:
        retry_hard = _retry_context_from_failed_search(state, result).hard_constraints
        if not state.visible_options and not state.params:
            return "Сейчас не смогла получить результаты по этому поиску. Повторить поиск?"
        if state.visible_options:
            return SAFE_V2_ERROR_TEXT
        if state.params or retry_hard:
            return "По запросу не удалось найти информацию. Точный ответ уточнит оператор. В текущих данных это не подтверждено. Передать оператору запрос?"
    return SAFE_V2_ERROR_TEXT


def _state_with_failed_search_retry(state: ConversationState, result: TurnResult, answer: str) -> ConversationState:
    retry = _retry_context_from_failed_search(state, result)
    question = answer.split("?")[-2].split("\n")[-1].strip() + "?" if "?" in answer else None
    recent = (*state.recent_turns, {"user": "", "assistant": _redact(answer)})[-6:]
    return replace(
        state,
        retry_search=retry,
        recent_turns=recent,
        last_assistant_question=question,
        last_answer_kind="safe_upstream_fallback",
    )


def _retry_context_from_failed_search(state: ConversationState, result: TurnResult) -> RetrySearchContext:
    plan = result.semantic_plan
    try:
        contract = build_search_request(plan, state, result.context)
        hard_constraints = {key: value for key, value in contract.effective_hard.items() if key in HARD_KEYS}
        preferences = {key: value for key, value in contract.preferences.items() if key in ALLOWED_PREFERENCES}
        viewpoint = plan.intent if plan.intent in {"investment", "rental", "family", "life"} else contract.response_viewpoint
    except Exception:
        hard_constraints = {}
        preferences = {}
        viewpoint = plan.intent if plan.intent in {"investment", "rental", "family", "life", "financing"} else None
    return RetrySearchContext(
        viewpoint=viewpoint,
        intent=plan.intent,
        hard_constraints=_safe_params(hard_constraints),
        preferences=_safe_params(preferences),
        error_code=result.execution.error_code,
        attempt_kind="refresh" if state.visible_options or state.params else "initial",
    )


def _try_capture_contact(
    app: Any,
    *,
    user_id: str,
    text: str,
    channel: str,
    meta: dict[str, Any],
    state: ConversationState | V0State,
    runtime_version: str,
    engine_version: str | None = None,
) -> dict[str, Any] | None:
    anonymous_contact_name = "Без имени"
    proactive_phone = _extract_phone_v2(text)
    pending_followup = getattr(state, "pending_followup", None)
    pending_action = getattr(state, "pending_action", None)
    contact_consent = bool(getattr(state, "contact_consent", False))
    contact_name = str(getattr(state, "contact_name", "") or "")
    if proactive_phone and pending_followup not in {"contact_phone", "phone_capture", "contact_name"} and _is_proactive_phone_contact_message_v2(text, proactive_phone):
        profile_name = _safe_profile_name_v2(meta)
        if profile_name:
            return _queue_callback_result(app, user_id=user_id, channel=channel, meta=meta, state=state, name=profile_name, phone=proactive_phone, runtime_version=runtime_version, engine_version=engine_version)
        _save_v2_contact_draft(app, user_id=user_id, phone=proactive_phone, event_id=str(meta.get("event_id") or ""))
        next_state = apply_state_delta(state, StateDelta(pending_followup="contact_name")) if isinstance(state, ConversationState) else replace(state, pending_action="contact_name")
        public = _public_callback_result("Номер сохранила. Напишите, пожалуйста, как к вам обращаться.", "collect_contact_name", False, next_state, runtime_version=runtime_version, engine_version=engine_version)
        return {"state": next_state, "public": public}

    draft_phone = _load_v2_contact_draft(app, user_id=user_id)
    pending_phone = is_pending_contact_phone(pending_followup, contact_consent=contact_consent) or pending_followup == "phone_capture" or pending_action == "contact_phone"
    pending_name = (bool(draft_phone) and not pending_phone) or is_pending_contact_name(pending_followup, contact_consent=contact_consent) or pending_followup == "contact_name" or pending_action == "contact_name"
    if not (pending_name or pending_phone):
        return None
    if pending_name:
        # A pending name must not swallow a reply to the preceding operator
        # offer. Let the semantic planner resolve explicit consent/decline.
        if _is_operator_consent_reply_v2(text) and not contact_consent:
            return None
        phone = _extract_phone_v2(text)
        if phone:
            _save_v2_contact_draft(app, user_id=user_id, phone=phone, event_id=str(meta.get("event_id") or ""))
            next_state = state if isinstance(state, ConversationState) else replace(state, pending_action="contact_name")
            public = _public_callback_result("Номер сохранила. Напишите, пожалуйста, как к вам обращаться.", "collect_contact_name", False, next_state, runtime_version=runtime_version, engine_version=engine_version)
            return {"state": next_state, "public": public}
        name = _safe_contact_name_v2(text)
        if not name:
            if contact_consent and _is_operator_consent_reply_v2(text):
                public = _public_callback_result("Не уверена, что правильно поняла имя. Напишите, пожалуйста, как к вам обращаться — например, Иван.", "collect_contact_name", False, state, runtime_version=runtime_version, engine_version=engine_version)
                return {"state": state, "public": public}
            # Содержательный вопрос важнее незавершённой контактной воронки.
            # Локально удерживаем только явную попытку передать контактные данные;
            # любой другой текст должен увидеть semantic planner.
            if _looks_like_contact_attempt_v2(text):
                public = _public_callback_result("Не уверена, что правильно поняла имя. Напишите, пожалуйста, как к вам обращаться — например, Иван.", "collect_contact_name", False, state, runtime_version=runtime_version, engine_version=engine_version)
                return {"state": state, "public": public}
            return None
        if draft_phone:
            return _queue_callback_result(app, user_id=user_id, channel=channel, meta=meta, state=state, name=name, phone=draft_phone, runtime_version=runtime_version, engine_version=engine_version)
        if not isinstance(state, ConversationState):
            return None
        next_state = apply_state_delta(state, StateDelta(contact_name=name, pending_followup="contact_phone"))
        public = _public_callback_result(f"{name}, напишите, пожалуйста, номер телефона для связи.", "collect_contact_phone", True, next_state, runtime_version=runtime_version, engine_version=engine_version)
        return {"state": next_state, "public": public}
    phone = _extract_phone_v2(text)
    if not phone:
        if _is_operator_consent_reply_v2(text):
            return {"state": state, "public": _public_callback_result("На какой номер вам удобно позвонить?", "collect_contact_phone", True, state, runtime_version=runtime_version, engine_version=engine_version)}
        if _looks_like_contact_attempt_v2(text):
            name = contact_name
            answer = f"{name + ', ' if name else ''}номер выглядит неполным или неверным. Напишите, пожалуйста, телефон в формате +7 999 123-45-67."
            return {"state": state, "public": _public_callback_result(answer, "collect_contact_phone", True, state, runtime_version=runtime_version, engine_version=engine_version)}
        return None
    callback_name = contact_name or _safe_profile_name_v2(meta) or anonymous_contact_name
    return _queue_callback_result(app, user_id=user_id, channel=channel, meta=meta, state=state, name=callback_name, phone=phone, runtime_version=runtime_version, engine_version=engine_version)


def _try_capture_v2_contact(app: Any, *, user_id: str, text: str, channel: str, meta: dict[str, Any], state: ConversationState) -> dict[str, Any] | None:
    return _try_capture_contact(app, user_id=user_id, text=text, channel=channel, meta=meta, state=state, runtime_version="v2")


def _queue_v2_callback_result(app: Any, *, user_id: str, channel: str, meta: dict[str, Any], state: ConversationState, name: str, phone: str) -> dict[str, Any]:
    return _queue_callback_result(app, user_id=user_id, channel=channel, meta=meta, state=state, name=name, phone=phone, runtime_version="v2")


def _queue_callback_result(app: Any, *, user_id: str, channel: str, meta: dict[str, Any], state: ConversationState | V0State, name: str, phone: str, runtime_version: str, engine_version: str | None = None) -> dict[str, Any]:
    outbox_result = _enqueue_callback(app, user_id=user_id, channel=channel, meta=meta, state=state, name=name, phone=phone, runtime_version=runtime_version, engine_version=engine_version)
    callback_ref = getattr(outbox_result, "lead_ref", None)
    _clear_v2_contact_draft(app, user_id=user_id)
    if isinstance(state, ConversationState):
        next_state: ConversationState | V0State = apply_state_delta(
            state,
            StateDelta(
                contact_name=name,
                contact_phone_redacted=_redact(phone),
                contact_consent=True,
                callback_ref=callback_ref,
                pending_followup=None,
                clear_fields=("pending_followup",),
            ),
        )
    else:
        next_state = replace(state, pending_action=None, pending_subject=None, pending_topic=None)
    answer = (
        f"Приняла, {name}. Заявка по текущему подбору передана оператору."
        if callback_ref
        else f"Приняла, {name}. Контакт сохранила; оператор сможет продолжить текущий подбор."
    )
    return {"state": next_state, "public": _public_callback_result(answer, "callback_queued", False, next_state, callback_ref=callback_ref, outbox_result=outbox_result, runtime_version=runtime_version, engine_version=engine_version)}


def _callback_outbox(app: Any) -> Any | None:
    return app.get("crm_callback_outbox") if hasattr(app, "get") else None


def _save_v2_contact_draft(app: Any, *, user_id: str, phone: str, event_id: str) -> None:
    outbox = _callback_outbox(app)
    if outbox is not None and hasattr(outbox, "save_contact_draft"):
        outbox.save_contact_draft(session_key=user_id, normalized_phone=phone, event_id=event_id)


def _load_v2_contact_draft(app: Any, *, user_id: str) -> str:
    outbox = _callback_outbox(app)
    if outbox is not None and hasattr(outbox, "load_contact_draft_phone"):
        return str(outbox.load_contact_draft_phone(session_key=user_id) or "")
    return ""


def _clear_v2_contact_draft(app: Any, *, user_id: str) -> None:
    outbox = _callback_outbox(app)
    if outbox is not None and hasattr(outbox, "clear_contact_draft"):
        outbox.clear_contact_draft(session_key=user_id)


def _enqueue_v2_callback(app: Any, *, user_id: str, channel: str, meta: dict[str, Any], state: ConversationState, name: str, phone: str) -> Any | None:
    return _enqueue_callback(app, user_id=user_id, channel=channel, meta=meta, state=state, name=name, phone=phone, runtime_version="v2")


def _enqueue_callback(app: Any, *, user_id: str, channel: str, meta: dict[str, Any], state: ConversationState | V0State, name: str, phone: str, runtime_version: str, engine_version: str | None = None) -> Any | None:
    outbox = _callback_outbox(app)
    if outbox is None or not hasattr(outbox, "enqueue_callback"):
        return None
    context = _build_callback_summary_snapshot(state, runtime_version=runtime_version, channel=channel, meta=meta, engine_version=engine_version)
    return outbox.enqueue_callback(session_key=user_id, event_id=str(meta.get("event_id") or ""), contact_name=name, normalized_phone=_normalize_phone_v2(phone), context=context, summary_input=context)


def _v2_public_callback_result(answer: str, intent: str, awaiting_phone: bool, state: ConversationState, *, callback_ref: str | None = None, outbox_result: Any | None = None) -> dict[str, Any]:
    return _public_callback_result(answer, intent, awaiting_phone, state, callback_ref=callback_ref, outbox_result=outbox_result, runtime_version="v2")


def _public_callback_result(answer: str, intent: str, awaiting_phone: bool, state: ConversationState | V0State, *, callback_ref: str | None = None, outbox_result: Any | None = None, runtime_version: str = "v2", engine_version: str | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"runtime": runtime_version, "callback_ref": callback_ref}
    if engine_version:
        meta["engine"] = engine_version
    public = {
        "ok": True,
        "answer": answer,
        "intent": intent,
        "awaiting_phone": awaiting_phone,
        "handoff_to_operator": False,
        "selected_option": getattr(state, "selected_option_name", None),
        "buttons": [],
        "meta": meta,
    }
    if outbox_result is not None and hasattr(outbox_result, "public"):
        public["crm_callback"] = outbox_result.public()
    return public


def _safe_option_summary(option: Any) -> dict[str, Any]:
    data = to_jsonable(option) if not isinstance(option, dict) else option
    if not isinstance(data, dict):
        return {}
    allowed = {
        "name", "location", "district", "price", "price_range", "price_min", "min_price",
        "area", "rooms", "finishing", "ready", "deadline", "metro", "developer",
        "why_close", "why_family", "why_investment", "why_rental",
    }
    out: dict[str, Any] = {}
    for key, value in data.items():
        if str(key) not in allowed:
            continue
        cleaned = _safe_nested(value)
        if cleaned not in (None, "", [], {}):
            out[str(key)] = cleaned
    return out


def _build_callback_summary_snapshot(state: ConversationState | V0State, *, runtime_version: str, channel: str, meta: dict[str, Any], engine_version: str | None = None) -> dict[str, Any]:
    selected_name = str(getattr(state, "selected_option_name", "") or "").strip()
    selected_obj: dict[str, Any] = {}
    if isinstance(state, ConversationState):
        selected = state.selected_enriched or state.find_visible_option(selected_name)
        selected_obj = _safe_option_summary(selected) if selected else ({"name": selected_name} if selected_name else {})
    else:
        selected = next((opt for opt in state.visible_options if opt.name == selected_name), None)
        selected_obj = _safe_option_summary(selected) if selected else ({"name": selected_name} if selected_name else {})
    options = [_safe_option_summary(option) for option in tuple(getattr(state, "visible_options", ()))[:3]]
    options = [option for option in options if option]
    snapshot: dict[str, Any] = {
        "runtime": runtime_version,
        "channel": _redact(str(channel or ""))[:50],
        "params": _safe_params(getattr(state, "params", {})),
        "selected_option": selected_obj,
        "current_options": options,
        "visible_options": options,
        "last_bot_question": _redact(str(getattr(state, "last_assistant_question", "") or ""))[:300],
        "operator_context": {
            "active_topic": _redact(str(getattr(state, "active_topic", "") or ""))[:80],
            "last_answer_kind": _redact(str(getattr(state, "last_answer_kind", "") or ""))[:80],
        },
        "metadata": {
            "event_id_present": bool(meta.get("event_id")),
            "sender_name_present": bool(meta.get("sender_name")),
        },
    }
    if engine_version:
        snapshot["engine"] = engine_version
    return snapshot


def _extract_phone_v2(text: str) -> str | None:
    raw = str(text or "")
    for match in re.finditer(r"(?<![\d+])\+?\d[\d\s().-]*\d(?!\d)", raw):
        canonical = _canonical_phone_v2(match.group(0))
        if canonical:
            return canonical
    return None


def _normalize_phone_v2(phone: str) -> str:
    return _canonical_phone_v2(phone) or ""


def _canonical_phone_v2(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not 10 <= len(digits) <= 15:
        return None
    if raw.startswith("+"):
        return f"+{digits}"
    if len(digits) == 10 and digits.startswith("9"):
        return f"+7{digits}"
    if len(digits) == 11 and digits.startswith("8"):
        return f"+7{digits[1:]}"
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits}"
    return None


def _safe_profile_name_v2(meta: dict[str, Any]) -> str:
    raw = str((meta if isinstance(meta, dict) else {}).get("sender_name") or "").strip()
    if not raw:
        return ""
    normalized = raw.casefold().replace("ё", "е")
    if "synthetic" in normalized or "test" in normalized or "тест" in normalized or "nmbot" in normalized:
        return ""
    return _safe_contact_name_v2(raw)


def _safe_contact_name_v2(text: str) -> str:
    raw = str(text or "").strip()
    if any(mark in raw for mark in "?!"):
        return ""
    # При активном contact_name разрешаем вернуться к воронке живой фразой
    # вроде «Вернёмся к звонку, меня зовут Анна». Берём только хвост после
    # явного маркера, а не пытаемся считать именем весь пользовательский текст.
    explicit = re.search(r"(?i)(?:^|[,;.]\s*)(?:меня\s+зовут|мо[её]\s+имя)\s+([А-Яа-яA-Za-zЁё\-]+(?:\s+[А-Яа-яA-Za-zЁё\-]+){0,2})\s*$", raw)
    candidate = explicit.group(1) if explicit else raw
    if not re.fullmatch(r"[А-Яа-яA-Za-zЁё\-]+(?:\s+[А-Яа-яA-Za-zЁё\-]+){0,2}", candidate):
        return ""
    words = candidate.split()
    if candidate.casefold() in {"да", "нет", "хорошо", "ладно", "ок", "ага", "конечно", "потом"}:
        return ""
    if len(words) > 1 and not explicit and not all(word[:1].isupper() for word in words):
        return ""
    value = " ".join(words)[:80]
    return value if 2 <= len(value) <= 80 else ""


def _looks_like_contact_attempt_v2(text: str) -> bool:
    raw = str(text or "").strip()
    digits = re.sub(r"\D", "", raw)
    return len(digits) >= 5 or bool(re.search(r"(?i)(?:^|[,;.]\s*)(?:меня\s+зовут|мо[её]\s+имя|телефон|номер)\b", raw))


def _is_proactive_phone_contact_message_v2(text: str, phone: str) -> bool:
    raw = str(text or "").strip()
    without_phone = raw.replace(phone, " ")
    without_phone = re.sub(r"[+\d\s().-]+", " ", without_phone).strip().casefold().replace("ё", "е")
    if not without_phone:
        return True
    if re.search(r"\b(?:мой|мои|номер|телефон|тел|связи|связаться|звон|позвон|перезвон|контакт)\b", without_phone):
        return True
    return False


def _is_operator_consent_reply_v2(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").casefold()).strip(" .,!?")
    return normalized in {
        "да", "давай", "давайте", "ок", "хорошо", "конечно", "согласен", "согласна",
        "нет", "не", "не надо", "не нужно", "нет спасибо", "без оператора", "не хочу",
    }


def _safe_params(params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in params.items():
        k = str(key)
        if any(word in k.lower() for word in ("token", "secret", "phone", "email", "client", "chat_id", "raw", "payload")):
            continue
        cleaned = _safe_nested(value)
        if cleaned not in (None, "", [], {}):
            out[k] = cleaned
    return out


def _safe_pending_followup(value: Any) -> str | None:
    if isinstance(value, str):
        return _redact(value)[:120] or None
    if isinstance(value, dict):
        followup_type = str(value.get("type") or "").strip()
        return _redact(followup_type)[:120] or None
    return None


def _safe_recent_turns(state: dict[str, Any], stored: dict[str, Any]) -> list[dict[str, str]]:
    if isinstance(stored.get("recent_turns"), list):
        return [_safe_turn_pair(x) for x in stored.get("recent_turns", [])[-6:] if isinstance(x, dict)]
    turns = state.get("dialog_turns") if isinstance(state.get("dialog_turns"), list) else state.get("dialog_window") if isinstance(state.get("dialog_window"), list) else []
    pairs: list[dict[str, str]] = []
    pending_user = ""
    for turn in turns[-12:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").lower()
        text = _redact(str(turn.get("text") or "")[:500])
        if role == "user":
            pending_user = text
        elif role in {"assistant", "bot"}:
            pairs.append({"user": pending_user, "assistant": text})
            pending_user = ""
    return pairs[-6:]


def _safe_turn_pair(turn: dict[str, Any]) -> dict[str, str]:
    return {"user": _redact(str(turn.get("user") or "")[:500]), "assistant": _redact(str(turn.get("assistant") or "")[:1000])}


def _dialog_window_from_recent_turns(turns: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    window: list[dict[str, str]] = []
    for turn in turns[-6:]:
        if turn.get("user"):
            window.append({"role": "user", "text": _redact(turn.get("user", ""))})
        if turn.get("assistant"):
            window.append({"role": "bot", "text": _redact(turn.get("assistant", ""))})
    return window[-6:]


def _last_turn_from_recent_turns(turns: tuple[dict[str, str], ...]) -> dict[str, str]:
    for turn in reversed(turns):
        user_text = _redact(str(turn.get("user") or ""))[:500]
        assistant_text = _redact(str(turn.get("assistant") or ""))[:1000]
        if user_text or assistant_text:
            return {
                "bot_question": assistant_text,
                "client_answer": user_text,
            }
    return {"bot_question": "", "client_answer": ""}


def _api_action_from_v2_action(action: str) -> str:
    return {
        "search": "search",
        "answer_selected_option": "answer_current_options",
        "answer_from_current_options": "answer_current_options",
        "clarify_financing": "answer_current_options",
        "freeform": "recover_dialogue",
        "offer_operator": "operator_contact",
        "accept_operator": "operator_contact",
        "decline_operator": "recover_dialogue",
    }.get(action, "recover_dialogue")


def _option_from_legacy(data: dict[str, Any], *, is_near: bool | None = None) -> OptionCard:
    normalized = _option_dict_from_legacy(data)
    if is_near is not None:
        normalized["is_near"] = is_near
    return normalize_card(normalized, is_near=is_near)


def _option_from_v2_fact(data: dict[str, Any], *, is_near: bool | None = None) -> OptionCard:
    return normalize_card(data, is_near=is_near)


def _area_from_v2_fact(raw: dict[str, Any]) -> str | None:
    if raw.get("total_area"):
        return str(raw["total_area"])
    mn, mx = raw.get("square_min"), raw.get("square_max")
    if mn and mx:
        return f"{mn}–{mx} м²"
    if mn:
        return f"от {mn} м²"
    return None


async def _run_v2_low_level_gateway(client: Any, request_data: dict[str, Any], *, timeout_env: str = "NMBOT_V2_SEARCH_TIMEOUT") -> tuple[Any, dict[str, Any]]:
    if not hasattr(client, "_run_gateway_request"):
        raise RuntimeError("v2_low_level_gateway_missing")
    headers = {"Authorization": f"Bearer {os.getenv('OVERMIND_TOKEN') or os.getenv('GATEWAY_POLL_TOKEN') or ''}"}
    default_timeout = "25" if timeout_env == "NMBOT_V2_RESPONSE_TIMEOUT" else os.getenv("NMBOT_REASON_TIMEOUT", "90")
    timeout = int(os.getenv(timeout_env, default_timeout))
    raw, meta = await client._run_gateway_request(request_data, headers, timeout)
    return raw if raw is not None else "", meta if isinstance(meta, dict) else {}


async def _run_v2_response_gateway_once(client: Any, request_data: dict[str, Any], *, timeout_env: str = "NMBOT_V2_RESPONSE_TIMEOUT") -> tuple[Any, dict[str, Any]]:
    if not hasattr(client, "_run_gateway_request_once"):
        return "", {"ok": False, "error_code": "v2_response_gateway_once_missing", "_upstream_error": True, "_safe_fallback": True}
    headers = {"Authorization": f"Bearer {os.getenv('OVERMIND_TOKEN') or os.getenv('GATEWAY_POLL_TOKEN') or ''}"}
    timeout = int(os.getenv(timeout_env, "25"))
    raw, meta = await client._run_gateway_request_once(request_data, headers, timeout)
    return raw if raw is not None else "", meta if isinstance(meta, dict) else {}


class _ResponseComposerAdapter:
    """V3-only one-shot response composer gateway adapter.

    Timeout is separate from search: NMBOT_V2_RESPONSE_TIMEOUT, default 25s.
    The adapter performs one canonical gateway call; no repair or search
    fallback race.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def compose_response(self, brief: Any, *, fallback_text: str) -> Any:
        async def gateway_composer(inner_brief: Any, *, repair_errors: tuple[str, ...] = (), model: str = "google/gemini-2.5-flash") -> tuple[Any, dict[str, Any]]:
            client = self.app.get("overmind_client") if hasattr(self.app, "get") else self.app["overmind_client"]
            payload = build_response_composer_payload(inner_brief, model=model, repair_errors=repair_errors)
            return await _run_v2_response_gateway_once(client, payload)

        return await compose_response_one_shot_async(brief, fallback_text=fallback_text, composer=gateway_composer)


class _ManagerRewriterAdapter:
    """V2/V3 final manager-style rewrite through the single-shot response gateway only."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def rewrite_manager_answer(self, *, transcript: tuple[dict[str, str], ...], current_question: str, prepared_answer: str, brief: Any) -> Any:
        client = self.app.get("overmind_client") if hasattr(self.app, "get") else self.app["overmind_client"]
        payload = build_manager_rewriter_payload(
            transcript=transcript,
            current_question=current_question,
            prepared_answer=prepared_answer,
            brief=brief,
            model=os.getenv("NMBOT_MANAGER_REWRITER_MODEL") or "google/gemini-2.5-flash",
        )
        raw, meta = await _run_v2_response_gateway_once(client, payload, timeout_env="NMBOT_MANAGER_REWRITER_TIMEOUT")
        safe_meta = meta if isinstance(meta, dict) else {}
        if safe_meta.get("_upstream_error") or safe_meta.get("_safe_fallback") or safe_meta.get("safe_fallback") or safe_meta.get("ok") is False:
            return {"text": "", "meta": safe_meta}
        return parse_manager_rewriter_text(raw)


def _v3_response_composer_mode() -> str:
    mode = str(os.getenv("NMBOT_V3_RESPONSE_COMPOSER_MODE") or "").strip().lower()
    return mode if mode in {"off", "shadow", "publish"} else "off"


def _v2_response_composer_mode() -> str:
    mode = str(os.getenv("NMBOT_V2_RESPONSE_COMPOSER_MODE") or "").strip().lower()
    return mode if mode in {"off", "shadow", "publish"} else "off"


def _runtime_response_composer_mode(runtime_version: str) -> str:
    if runtime_version == "v3":
        return _v3_response_composer_mode()
    if runtime_version == "v2":
        return _v2_response_composer_mode()
    return "off"


def _manager_rewriter_mode(key: str) -> str:
    mode = str(os.getenv(key) or "").strip().lower()
    return mode if mode in {"off", "shadow", "publish"} else "off"


def _runtime_manager_rewriter_mode(runtime_version: str) -> str:
    if runtime_version == "v2":
        return _manager_rewriter_mode("NMBOT_V2_MANAGER_REWRITER_MODE")
    if runtime_version == "v3":
        return _manager_rewriter_mode("NMBOT_V3_MANAGER_REWRITER_MODE")
    return "off"


def _attempts_from_meta(meta: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(meta, dict):
        return ()
    attempts = meta.get("attempts") or meta.get("_attempts") or []
    if isinstance(attempts, list):
        return tuple(x for x in attempts if isinstance(x, dict))
    if meta.get("_provider_retry_attempted"):
        return ({"status": "first", "ok": False}, {"status": "retry", "ok": bool(meta.get("_provider_retry_success"))})
    return ()


def _shortlist_enrichment_facts(_viewpoint: str | None) -> tuple[str, ...]:
    return ()


def _enrichment_refresh_requested(plan: TurnPlan) -> bool:
    text = str(getattr(plan, "query_text", "") or "").casefold().replace("ё", "е")
    return any(token in text for token in ("свеж", "обнов", "актуальн"))


def _state_enriched_cache_lookup(state: ConversationState, option: OptionCard, viewpoint: str, facts_needed: tuple[str, ...] | list[str] | None, *, force_refresh: bool) -> EnrichedCardCacheEntry | None:
    if force_refresh:
        return None
    identity = enriched_card_identity(option)
    requested = tuple(str(item).strip().lower() for item in (facts_needed or ()) if str(item).strip())
    for entry in state.enriched_card_cache:
        if entry.identity != identity or entry.scenario != str(viewpoint or "life"):
            continue
        if not enriched_cache_entry_is_fresh(entry, ttl_seconds=_enriched_cache_ttl_seconds()):
            continue
        loaded = {str(item).strip().lower() for item in entry.loaded_facts if str(item).strip()}
        if not any(fact not in loaded for fact in requested):
            return entry
    return None


def _enriched_cache_entry(card: OptionCard, viewpoint: str, *, loaded_facts: tuple[str, ...] | list[str] = (), fetched_at: Any = None) -> EnrichedCardCacheEntry:
    return EnrichedCardCacheEntry(
        identity=enriched_card_identity(card), name=card.name, card=card, scenario=str(viewpoint or "life"),
        loaded_facts=tuple(sorted({
            *[str(item).strip().lower() for item in present_fact_names(card) if str(item).strip()],
            *[str(item).strip().lower() for item in loaded_facts if str(item).strip()],
            *[fact for fact, value in {
                "sales_count": getattr(card, "sales_count", None), "ads_count": getattr(card, "ads_count", None),
                "price_square": getattr(card, "price_square", None), "room_prices": getattr(card, "room_prices", None),
                "lot_examples": getattr(card, "lot_examples", None),
            }.items() if value not in (None, "", (), [], {})],
        })),
        fetched_at=str(fetched_at or datetime.now(timezone.utc).isoformat()),
    )


def _enriched_cache_ttl_seconds() -> int:
    try:
        return max(60, min(int(os.getenv("NMBOT_V2_ENRICHED_CARD_CACHE_TTL_SECONDS", "900") or "900"), 24 * 60 * 60))
    except (TypeError, ValueError):
        return 900


async def _get_or_fetch_v2_enriched_option(app: Any, option: OptionCard, state: ConversationState, viewpoint: str, *, facts_needed: tuple[str, ...] | list[str] | None = None, force_refresh: bool = False) -> tuple[OptionCard, dict[str, Any]]:
    key = _v2_option_enrichment_key(option, viewpoint)
    if not key:
        return option, {"applied": False, "source": "base", "skipped": "missing_key"}
    cache = _v2_enrichment_cache(app)
    cached = cache.get(key)
    if not force_refresh and isinstance(cached, dict) and isinstance(cached.get("option"), dict) and _v2_process_cache_fresh(cached):
        cached_option = normalize_card(cached["option"])
        if not split_requested_facts(facts_needed or (), cached_option).missing:
            cache.move_to_end(key)
            return cached_option, {"applied": True, "source": "cache", "key": key, "ts": cached.get("ts")}
    client = app.get("overmind_client") if hasattr(app, "get") else None
    if client is None or not hasattr(client, "fetch_enriched_option"):
        if client is None:
            return option, {"applied": False, "source": "base", "key": key, "skipped": "client_missing"}

        async def gateway(request_data: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
            return await _run_v2_low_level_gateway(client, request_data)

        enriched, meta = await fetch_enriched_option_v2(
            option,
            viewpoint,
            gateway,
            timeout=max(0.2, _safe_float_env("NMBOT_V2_ENRICHMENT_ITEM_TIMEOUT", 20.0)),
            model=SEARCH_MODEL,
            facts_needed=facts_needed,
        )
        if enriched == option:
            return option, {"applied": False, "source": "base", "key": key, "skipped": str(meta.get("skipped") or "low_level_not_applied")}
        cache[key] = {"option": to_jsonable(enriched), "meta": _safe_nested(meta), "ts": datetime.now(timezone.utc).isoformat()}
        _trim_v2_enrichment_cache(cache)
        return enriched, {"applied": True, "source": "v2_low_level", "key": key, "ts": cache[key]["ts"]}
    try:
        enriched_raw, meta = await asyncio.wait_for(
            client.fetch_enriched_option(to_jsonable(option), state=_v2_to_planner_legacy_state(state), scenario=viewpoint, facts_needed=list(facts_needed or ())),
            timeout=max(0.2, _safe_float_env("NMBOT_V2_ENRICHMENT_ITEM_TIMEOUT", 20.0)),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return option, {"applied": False, "source": "base", "key": key, "skipped": exc.__class__.__name__}
    if isinstance(enriched_raw, dict) and not _v2_enriched_identity_matches(option, enriched_raw):
        return option, {"applied": False, "source": "base", "key": key, "skipped": "identity_mismatch", "meta": _safe_nested(meta)}
    enriched = _merge_v2_option(option, _option_from_legacy(enriched_raw) if isinstance(enriched_raw, dict) else option)
    if enriched == option:
        return option, {"applied": False, "source": "base", "key": key, "skipped": "empty_enrichment", "meta": _safe_nested(meta)}
    cache[key] = {"option": to_jsonable(enriched), "meta": _safe_nested(meta), "ts": datetime.now(timezone.utc).isoformat()}
    _trim_v2_enrichment_cache(cache)
    return enriched, {"applied": True, "source": "fetch", "key": key, "meta": _safe_nested(meta), "ts": cache[key]["ts"]}


def _v2_enrichment_cache(app: Any) -> OrderedDict[str, dict[str, Any]]:
    cache = app.setdefault("nmbot_v2_enrichment_cache", OrderedDict()) if hasattr(app, "setdefault") else OrderedDict()
    if not isinstance(cache, OrderedDict):
        cache = OrderedDict((str(k), v) for k, v in dict(cache).items() if isinstance(v, dict)) if isinstance(cache, dict) else OrderedDict()
        if hasattr(app, "__setitem__"):
            app["nmbot_v2_enrichment_cache"] = cache
    return cache


def _trim_v2_enrichment_cache(cache: OrderedDict[str, dict[str, Any]]) -> None:
    limit = max(8, int(os.getenv("NMBOT_V2_ENRICHMENT_CACHE_MAX", "64") or "64"))
    while len(cache) > limit:
        cache.popitem(last=False)


def _v2_process_cache_fresh(entry: dict[str, Any]) -> bool:
    try:
        fetched = datetime.fromisoformat(str(entry.get("ts") or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return fetched.timestamp() + _enriched_cache_ttl_seconds() >= datetime.now(timezone.utc).timestamp()


def _v2_option_enrichment_key(option: OptionCard, viewpoint: str) -> str:
    name = _compact_v2_key(option.name)
    location = _compact_v2_key(option.location or "")
    price = _compact_v2_key(option.price or option.price_min or "")
    vp = _compact_v2_key(viewpoint or "self_use") or "self_use"
    identity = "|".join(part for part in (name, location, price) if part)
    return f"{identity}::{vp}" if identity else ""


def _selected_enrichment_facts(facts: tuple[str, ...] | list[str] | None, viewpoint: str | None) -> tuple[str, ...]:
    out = [str(item).strip().lower() for item in (facts or ()) if str(item).strip()]
    if str(viewpoint or "").strip().lower() in {"rental", "rent"} and "lot_examples" not in out:
        out.append("lot_examples")
    return tuple(dict.fromkeys(out))


def _compact_v2_key(value: Any) -> str:
    return re.sub(r"[^a-zа-я0-9]+", " ", str(value or "").casefold().replace("ё", "е")).strip()


def _v2_enriched_identity_matches(base: OptionCard, enriched: dict[str, Any]) -> bool:
    enriched_name = _compact_v2_key(enriched.get("name") or enriched.get("title") or enriched.get("label") or "")
    base_name = _compact_v2_key(base.name)
    return not enriched_name or not base_name or enriched_name == base_name or base_name in enriched_name


def _merge_v2_option(base: OptionCard, enriched: OptionCard) -> OptionCard:
    return merge_option_cards(base, enriched)


def _v2_value_missing(value: Any) -> bool:
    if value in (None, "", (), [], {}):
        return True
    text = str(value).strip().casefold()
    return text in {"нет", "none", "null", "не указан", "не указано", "информация отсутствует", "уточняется"} or "не указан" in text or "отсутств" in text or "уточн" in text


def _safe_enrichment_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in meta.items() if k in {"applied", "source", "key", "skipped"}}


def _safe_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _option_dict_from_legacy(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(data.get("name") or data.get("title") or data.get("label") or "Вариант").strip(),
        "location": data.get("location") or data.get("district") or data.get("address"),
        "price": data.get("price") or data.get("price_range") or data.get("price_text"),
        "price_min": data.get("price_min"),
        "rooms": data.get("rooms") or data.get("room_type"),
        "finishing": data.get("finishing") or data.get("renovation"),
        "area": data.get("area"),
        "ready": data.get("ready") or data.get("completion"),
        "metro": data.get("metro") if isinstance(data.get("metro"), str) else None,
        "developer": data.get("developer"),
        "property_class": data.get("property_class") or data.get("class"),
        "infrastructure": tuple(str(x) for x in (data.get("infrastructure") or []) if x) if isinstance(data.get("infrastructure"), list) else (),
        "ads_count": data.get("ads_count"),
        "sales_count": data.get("sales_count"),
        "sales_date": data.get("sales_date"),
        "discount": data.get("discount"),
        "parking": data.get("parking"),
        "parking_price": data.get("parking_price"),
        "parking_inventory": data.get("parking_inventory"),
        "apartment_inventory": data.get("apartment_inventory"),
        "mortgage_terms": data.get("mortgage_terms"),
        "room_formats": tuple(str(x) for x in (data.get("room_formats") or []) if x) if isinstance(data.get("room_formats"), list) else (),
        "ads": data.get("ads"),
        "house": data.get("house"),
        "apartment_types": data.get("apartment_types"),
        "lot_examples": data.get("lot_examples"),
        "why_close": data.get("why_close"),
        "is_near": bool(data.get("is_near", False)),
    }


def _legacy_dict_from_option(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in _option_dict_from_legacy(data).items() if v not in (None, (), "")}


def _normalize_delta(delta: dict[str, Any]) -> dict[str, Any]:
    from nmbot_v2.constraints import normalize_constraints_delta

    return normalize_constraints_delta(delta)


def _hard_constraints(plan: SemanticPlan) -> dict[str, Any] | None:
    if isinstance(plan.constraints_delta, dict) and isinstance(plan.constraints_delta.get("hard"), dict):
        return _normalize_delta({"hard": plan.constraints_delta.get("hard")})
    return None


def _search_payload_from_meta(search_meta: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Extract structured MCP result from the V1 search metadata.

    ``_response_text`` is a legacy internal transport field, never persisted in
    V2 state or shown to a client.  It is only used at this adapter boundary to
    rebuild the typed V2 `SearchResult` that V1 already derived from it.
    """
    meta = search_meta if isinstance(search_meta, dict) else {}
    raw = meta.get("_response_text")
    if not isinstance(raw, str) or not raw.strip():
        return [], [], []
    try:
        start, end = raw.find("{"), raw.rfind("}")
        parsed = json.loads(raw[start : end + 1]) if start >= 0 and end > start else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return [], [], []
    if not isinstance(parsed, dict):
        return [], [], []
    facts = [item for item in parsed.get("facts", []) if isinstance(item, dict)]
    near = [item for item in parsed.get("near", []) if isinstance(item, dict)]
    missing = [str(item) for item in parsed.get("missing", []) if item]
    return facts, near, missing


def _full_visible_options_from_meta(chat_meta: Any) -> list[dict[str, Any]]:
    """Use V1's structured visible cards when raw MCP JSON is unavailable.

    Some enforced V1 paths intentionally do not expose `_response_text`, but
    publish the already fact-safe cards as `_visible_options`.  Name-only
    entries are not enough: they must be resolved against `facts[]` above.
    """
    meta = chat_meta if isinstance(chat_meta, dict) else {}
    raw = meta.get("_visible_options")
    if not isinstance(raw, list):
        return []
    fact_keys = {"location", "price", "price_range", "price_min", "ready", "finishing", "metro", "area", "rooms"}
    options = [item for item in raw[:3] if isinstance(item, dict) and str(item.get("name") or item.get("label") or "").strip() and any(key in item for key in fact_keys)]
    return options


def _intent_for_action(result: TurnResult) -> str:
    return {
        "search": "main_search",
        "answer_selected_option": "answer_current_options",
        "answer_from_current_options": "answer_current_options",
        "clarify_financing": "financing",
        "offer_operator": "operator_offer",
        "decline_operator": "operator_declined",
    }.get(result.action.value, result.action.value)


def _safe_trace(result: TurnResult) -> dict[str, Any]:
    timing = result.trace.get("timing_ms") if isinstance(result.trace.get("timing_ms"), dict) else {}
    attempts = result.trace.get("attempts", []) if isinstance(result.trace.get("attempts", []), list) else []
    enrichment = next((item for item in attempts if isinstance(item, dict) and item.get("stage") == "v2_option_enrichment"), None)
    search_validation = next((item for item in attempts if isinstance(item, dict) and item.get("stage") == "search_validation_report"), None)
    response_composer = result.trace.get("response_composer") if isinstance(result.trace.get("response_composer"), dict) else None
    manager_rewriter = result.trace.get("manager_rewriter") if isinstance(result.trace.get("manager_rewriter"), dict) else None
    prompt_provenance = sanitize_prompt_provenance(result.trace.get("prompt_provenance"))
    safe = {
        "accepted_state": result.trace.get("accepted_state"),
        "error_code": result.trace.get("error_code"),
        "stage": result.stage.value,
        "action": result.action.value,
        "option_enrichment": _safe_enrichment_trace(enrichment),
        "search_validation": _safe_search_validation_trace(search_validation),
        "response_composer": _safe_response_composer_trace(response_composer),
        "manager_rewriter": _safe_response_composer_trace(manager_rewriter),
        "timing_ms": {
            key: int(value)
            for key, value in timing.items()
            if key in {"planner", "execution", "response", "total"} and isinstance(value, (int, float))
        },
    }
    runtime_summary = _safe_runtime_summary_trace(result.trace.get("runtime_summary"))
    if runtime_summary:
        safe["runtime_summary"] = runtime_summary
    if prompt_provenance:
        safe["prompt_provenance"] = prompt_provenance
    return safe


def _attach_turn_prompt_provenance(result: TurnResult, *, planner: Any, search: Any) -> None:
    composer_meta = result.trace.get("response_composer") if isinstance(result.trace.get("response_composer"), dict) else {}
    manager_meta = result.trace.get("manager_rewriter") if isinstance(result.trace.get("manager_rewriter"), dict) else {}
    merged = merge_prompt_provenance(
        getattr(planner, "prompt_provenance", None),
        getattr(search, "prompt_provenance", None),
        composer_meta.get("prompt_provenance") if isinstance(composer_meta, dict) else None,
        manager_meta.get("prompt_provenance") if isinstance(manager_meta, dict) else None,
        coverage="complete",
    )
    if merged:
        result.trace["prompt_provenance"] = merged


def _safe_search_code(value: Any, *, max_len: int = 120) -> str | None:
    raw_code = str(value or "").split(":", 1)[0].strip()
    code = "validation_error" if re.search(r"\s", raw_code) else raw_code
    token = re.sub(r"[^A-Za-z0-9_.-]", "_", code.encode("ascii", "ignore").decode("ascii"))[:max_len].strip("_")
    return token or None


def _safe_search_codes(values: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for item in values:
        code = _safe_search_code(item)
        if code and code not in out:
            out.append(code)
        if len(out) >= limit:
            break
    return out


def _safe_search_counts(value: Any) -> dict[str, int]:
    counts = value if isinstance(value, dict) else {}
    safe: dict[str, int] = {}
    for key in ("facts", "near", "missing", "errors", "warnings"):
        raw = counts.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            safe[key] = max(0, min(int(raw), 1000))
    return safe


def _search_validation_report_attempt(validation: dict[str, Any]) -> dict[str, Any]:
    counts = dict(validation.get("counts") or {}) if isinstance(validation.get("counts"), dict) else {}
    counts.setdefault("errors", len(validation.get("errors") or []) if isinstance(validation.get("errors"), list) else 0)
    counts.setdefault("warnings", len(validation.get("warnings") or []) if isinstance(validation.get("warnings"), list) else 0)
    return {
        "stage": "search_validation_report",
        "status": _safe_search_code(validation.get("status"), max_len=40) or "unknown",
        "errors": _safe_search_codes(validation.get("errors"), limit=12),
        "warnings": _safe_search_codes(validation.get("warnings"), limit=12),
        "counts": _safe_search_counts(counts),
    }


def _safe_search_validation_trace(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    report = {
        "stage": "search_validation_report",
        "status": _safe_search_code(value.get("status"), max_len=40) or "unknown",
        "errors": _safe_search_codes(value.get("errors"), limit=12),
        "warnings": _safe_search_codes(value.get("warnings"), limit=12),
        "counts": _safe_search_counts(value.get("counts")),
    }
    if report["status"] == "valid" and not report["errors"] and not report["warnings"]:
        return None
    return report


_SAFE_RUNTIME_QUALITY_BLOCKERS = {
    "runtime_error",
    "question_count_not_one",
    "final_question_not_at_end",
    "search_without_cards",
    "enrichment_error",
}
_SAFE_RUNTIME_GROUNDING_SCOPES = {"canonical_response_plan"}


def _safe_runtime_summary_trace(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    stage = str(value.get("stage") or "").strip()
    action = str(value.get("action") or "").strip()
    if not stage or not action:
        return {}
    summary: dict[str, Any] = {
        "stage": _bounded_token(stage),
        "action": _bounded_token(action),
        "answer_kind": _bounded_token(value.get("answer_kind")),
        "timing_ms": _safe_timing_ms(value.get("timing_ms")),
        "call_counts": _safe_call_counts(value.get("call_counts")),
        "state_before": _safe_runtime_state_summary(value.get("state_before")),
        "state_after": _safe_runtime_state_summary(value.get("state_after")),
        "question_count": _bounded_int(value.get("question_count"), 0, 20),
        "final_question_at_end": bool(value.get("final_question_at_end")),
        "quality_blockers": [
            str(item)
            for item in (value.get("quality_blockers") if isinstance(value.get("quality_blockers"), list) else [])
            if str(item) in _SAFE_RUNTIME_QUALITY_BLOCKERS
        ][:5],
        "grounding_scope": str(value.get("grounding_scope")) if str(value.get("grounding_scope")) in _SAFE_RUNTIME_GROUNDING_SCOPES else "canonical_response_plan",
    }
    shadow = value.get("card_reformatter_shadow") if isinstance(value.get("card_reformatter_shadow"), dict) else None
    if shadow:
        summary["card_reformatter_shadow"] = {
            "mode": "shadow",
            "classification": _bounded_token(shadow.get("classification")),
            "card_count": _bounded_int(shadow.get("card_count"), 0, 3),
            "cards": [
                {
                    "idx": _bounded_int(item.get("idx"), 1, 3),
                    "name": str(item.get("name") or "")[:180],
                    "mandatory_text": str(item.get("mandatory_text") or "")[:500],
                    "card_mode": _bounded_token(item.get("card_mode")),
                    "anchor_fact": _bounded_token(item.get("anchor_fact")),
                }
                for item in (shadow.get("cards") if isinstance(shadow.get("cards"), list) else [])[:3]
                if isinstance(item, dict)
            ],
        }
    return summary


def _safe_timing_ms(value: Any) -> dict[str, int]:
    timing = value if isinstance(value, dict) else {}
    return {key: _bounded_int(timing.get(key), 0, 10 * 60 * 1000) for key in ("planner", "execution", "response", "total")}


def _safe_call_counts(value: Any) -> dict[str, int]:
    counts = value if isinstance(value, dict) else {}
    return {
        "planner": _bounded_int(counts.get("planner"), 0, 3),
        "search": _bounded_int(counts.get("search"), 0, 1),
        "selected_enrichment": _bounded_int(counts.get("selected_enrichment"), 0, 1),
        "gateway_attempts": _bounded_int(counts.get("gateway_attempts"), 0, 5),
        "scenario_search": _bounded_int(counts.get("scenario_search"), 0, 3),
        "answer": _bounded_int(counts.get("answer"), 0, 3),
    }


def _safe_runtime_state_summary(value: Any) -> dict[str, Any]:
    state = value if isinstance(value, dict) else {}
    raw_keys = state.get("param_keys") if isinstance(state.get("param_keys"), list) else []
    return {
        "param_keys": sorted(dict.fromkeys(key for key in (_safe_param_key(item) for item in raw_keys) if key))[:20],
        "visible_options_count": _bounded_int(state.get("visible_options_count"), 0, 20),
        "selected_present": bool(state.get("selected_present")),
        "pending_followup": _bounded_token(state.get("pending_followup")),
        "active_topic": _bounded_token(state.get("active_topic")),
    }


def _bounded_token(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return re.sub(r"[^a-zA-Z0-9_.:-]", "_", text)[:80]


def _safe_param_key(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if any(part in text for part in ("phone", "тел", "email", "client", "chat", "token", "secret", "+7", "7999")):
        return None
    return _bounded_token(text)


def _bounded_int(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(number, high))


_SAFE_RESPONSE_COMPOSER_FALLBACK_REASONS = {
    "composer_error",
    "validation_failed",
    "composer_missing",
    "not_customer_composer_stage",
    "deterministic_renderer",
    "ineligible_response_goal",
}

_SAFE_RESPONSE_COMPOSER_VALIDATION_CODES = {
    "empty_response",
    "invalid_json",
    "json_root_must_be_object",
    "schema_required_field_missing",
    "schema_additional_properties",
    "schema_invalid_options",
    "too_many_cards",
    "option_name_not_allowed",
    "option_order_mismatch",
    "recipe_card_directive_mismatch",
    "empty_option_section",
    "required_location_missing",
    "required_price_missing",
    "scenario_fact_benefit_missing",
    "scenario_viewpoint_mismatch",
    "intro_empty",
    "missing_note_required",
    "financing_missing_note_required",
    "final_question_empty",
    "recipe_cta_mismatch",
    "contact_before_financing_consent",
    "selected_financing_card_scope_invalid",
    "section_question_mark",
    "question_count_not_one",
    "final_question_not_at_end",
    "final_question_contract_mismatch",
    "missing_context_acknowledgement",
    "duplicate_answer",
    "repeated_identical_benefit",
    "unknown_option_name",
    "unknown_number_or_sensitive_claim",
    "internal_or_raw_wire_leak",
    "unsupported_sensitive_claim",
    "unsupported_marketing_claim",
    "provider_invalid_argument",
    "ineligible_response_goal",
    "v2_response_gateway_once_missing",
}
_SAFE_RESPONSE_COMPOSER_VALIDATION_STAGES = {"provider", "transport", "schema", "semantic"}
_SAFE_RESPONSE_COMPOSER_MODES = {"off", "shadow", "publish"}
_SAFE_RESPONSE_COMPOSER_STATUSES = {"primary", "fallback", "failed"}


def _safe_response_composer_trace(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not meta:
        return {}
    composer_used = bool(meta.get("used"))
    reason = None
    if not composer_used:
        raw_reason = str(meta.get("reason") or "").strip()
        reason = raw_reason if raw_reason in _SAFE_RESPONSE_COMPOSER_FALLBACK_REASONS else "other"
    validation_codes: list[str] = []
    validation_stage = None
    if reason == "validation_failed":
        raw_stage = str(meta.get("error_category") or "").strip()
        if raw_stage in _SAFE_RESPONSE_COMPOSER_VALIDATION_STAGES:
            validation_stage = raw_stage
        for raw_code in meta.get("errors", ()) if isinstance(meta.get("errors"), (list, tuple)) else ():
            code = str(raw_code or "").split(":", 1)[0]
            if code.startswith("unsupported_marketing_claim"):
                code = "unsupported_marketing_claim"
            if code in _SAFE_RESPONSE_COMPOSER_VALIDATION_CODES and code not in validation_codes:
                validation_codes.append(code)
    attempts = meta.get("attempts")
    out = {
        "composer_used": composer_used,
        "fallback_reason": reason,
        "validation_stage": validation_stage,
        "validation_codes": validation_codes[:3],
        "attempts": int(attempts) if isinstance(attempts, int) and 1 <= attempts <= 2 else None,
    }
    if any(key in meta for key in ("mode", "published", "status", "elapsed_ms")):
        mode = str(meta.get("mode") or "").strip().lower()
        status = str(meta.get("status") or "").strip().lower()
        elapsed_ms = meta.get("elapsed_ms")
        out.update(
            {
                "mode": mode if mode in _SAFE_RESPONSE_COMPOSER_MODES else None,
                "published": bool(meta.get("published")),
                "status": status if status in _SAFE_RESPONSE_COMPOSER_STATUSES else None,
                "elapsed_ms": _bounded_int(elapsed_ms, 0, 10 * 60 * 1000) if isinstance(elapsed_ms, (int, float)) else None,
            }
        )
    return out


def _safe_enrichment_trace(enrichment: dict[str, Any] | None) -> dict[str, Any]:
    if not enrichment:
        return {}
    requested = [str(item) for item in enrichment.get("requested_facts", ()) if str(item) in ALLOWED_FACTS]
    fresh = [str(item) for item in enrichment.get("fresh_facts", ()) if str(item) in ALLOWED_FACTS]
    outcome = str(enrichment.get("outcome") or "")
    return {
        "enabled": bool(enrichment.get("enabled")),
        "applied": bool(enrichment.get("applied")),
        "count": int(enrichment.get("count") or 0),
        "applied_count": int(enrichment.get("applied_count") or 0),
        "requested_facts": requested,
        "fresh_facts": fresh,
        "outcome": outcome if outcome in {"applied", "not_found", "timeout", "technical_failure", "unavailable"} else None,
        "items": [
            {k: item.get(k) for k in ("idx", "applied", "source", "skipped") if k in item}
            for item in (enrichment.get("items") if isinstance(enrichment.get("items"), list) else [])[:3]
            if isinstance(item, dict)
        ],
    }


def _safe_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return _safe_nested(meta)


def _safe_nested(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            k = str(key)
            if any(word in k.lower() for word in ("token", "secret", "phone", "client", "chat_id")):
                continue
            out[k] = _safe_nested(item)
        return out
    if isinstance(value, list):
        return [_safe_nested(x) for x in value[:20]]
    if isinstance(value, str):
        return _redact(value[:500])
    return value


def _redact(text: str) -> str:
    text = re.sub(r"(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)", "[redacted-contact]", str(text or ""))
    text = re.sub(r"[\w.+-]+@[\w.-]+", "[redacted-email]", text)
    return text


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
