from __future__ import annotations

import asyncio
import ast
import hashlib
from datetime import datetime, timezone
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from aiohttp import web
from nmbot_v0.contracts import V0Answer, V0State, V0TurnResult
from nmbot_v0.field_contract import v0_presentation_search_fields
from nmbot_v2.contracts import ExecutableTurn, IntentGoal, OptionCard, ResponseBrief, ResponsePlan, SafeTurnContext, SearchResult, SemanticPlan, Stage, StateDelta, TurnAction
from nmbot_v2.semantic_planner import derive_runtime_decision, normalize_semantic_planner_result
from nmbot_v2.pending import PendingKind
from nmbot_v2.state import ConversationState, EnrichedCardCacheEntry, apply_state_delta, enriched_card_identity
from nmbot_v2.runtime import TurnProcessor, _last_offer_for_response
import nmbot_v2.manager_rewriter as manager_rewriter_mod
import scripts.nmbot_runtime_adapter as runtime_adapter_mod
from scripts.nmbot_runtime_adapter import _OvermindSearchAdapter, _SemanticPlannerAdapter, _canonical_v0_envelope, _inherit_selected_scope, _legacy_to_v2_state, _pending_scenario_for_planner, _queue_v2_callback_result, _safe_response_composer_trace, _semantic_plan_from_planner, _semantic_plan_from_semantic_result, _v2_to_planner_legacy_state, run_runtime_turn


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
SCRIPT = SCRIPT_DIR / "nmbot_api_server.py"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("nmbot_api_server", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["nmbot_api_server"] = mod
spec.loader.exec_module(mod)


def test_last_offer_preserves_subject_and_action_for_followup() -> None:
    selected = OptionCard(name="ЖК «Люблинский парк»")
    state = ConversationState(visible_options=(selected,))
    plan = SemanticPlan(
        operation="answer_open_question",
        requested_facts=("apartment_types", "ads"),
        facts_needed=("apartment_types", "ads"),
    )
    response_plan = ResponsePlan(
        acknowledgement="",
        recipe_id="selected_live_fact_check",
        answer_kind="selected_live_fact_check",
    )

    offer = _last_offer_for_response(
        stage=Stage.SELECTED_OBJECT,
        plan=plan,
        response_plan=response_plan,
        delta=StateDelta(selected_option_name=selected.name),
        state=state,
        question="Показать планировки?",
    )

    assert offer == {
        "action": "verify_selected_live_facts",
        "subject_type": "visible_option",
        "subject_name": "ЖК «Люблинский парк»",
        "requested_facts": ["apartment_types", "ads"],
        "scope": "one",
        "question": "Показать планировки?",
        "recipe_id": "selected_live_fact_check",
    }


def test_last_offer_does_not_guess_subject_for_current_options() -> None:
    state = ConversationState(visible_options=(OptionCard(name="ЖК Один"), OptionCard(name="ЖК Два")))
    plan = SemanticPlan(operation="answer_open_question")
    response_plan = ResponsePlan(acknowledgement="", recipe_id="repeat_current_options", answer_kind="answer_open_question")

    offer = _last_offer_for_response(
        stage=Stage.CURRENT_OPTIONS,
        plan=plan,
        response_plan=response_plan,
        delta=StateDelta(),
        state=state,
        question="Какой ЖК разобрать подробнее?",
    )

    assert offer["action"] == "choose_option"
    assert offer["subject_name"] == ""
    assert offer["scope"] == "all"


def test_last_offer_cannot_reuse_subject_after_reset_or_new_search() -> None:
    selected = OptionCard(name="Старый ЖК")
    state = ConversationState(
        selected_option_name=selected.name,
        selected_enriched=selected,
        visible_options=(selected,),
        last_offer={"subject_name": selected.name, "action": "show_selected_details"},
    )
    plan = SemanticPlan(operation="answer_open_question")
    response_plan = ResponsePlan(acknowledgement="", recipe_id="repeat_current_options", answer_kind="answer_open_question")

    reset_offer = _last_offer_for_response(
        stage=Stage.FIRST_LIST,
        plan=plan,
        response_plan=response_plan,
        delta=StateDelta(reset=True),
        state=state,
        question="Что подобрать дальше?",
    )
    search_offer = _last_offer_for_response(
        stage=Stage.REFINEMENT,
        plan=plan,
        response_plan=response_plan,
        delta=StateDelta(clear_fields=("selected_option_name", "selected_enriched", "last_offer")),
        state=state,
        question="Какой вариант разобрать подробнее?",
    )

    assert reset_offer["subject_name"] == ""
    assert search_offer["subject_name"] == ""


def test_failure_context_clear_removes_stale_offer() -> None:
    selected = OptionCard(name="Старый ЖК")
    state = ConversationState(
        selected_option_name=selected.name,
        selected_enriched=selected,
        pending_followup="selected_live_fact_consent",
        last_offer={"subject_name": selected.name, "action": "show_selected_details"},
    )

    cleared = apply_state_delta(
        state,
        StateDelta(clear_fields=("selected_option_name", "selected_enriched", "pending_followup", "last_offer")),
    )

    assert cleared.selected_option_name is None
    assert cleared.selected_enriched is None
    assert cleared.pending_followup is None
    assert cleared.last_offer == {}


def test_search_failure_public_result_and_saved_state_clear_stale_selected_context(monkeypatch) -> None:
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"operation": "refine_search", "constraints_delta": {"hard": {"max_price": 17_000_000}}, "confidence": 1.0})
        initial = mod._default_state()
        initial["selected_option"] = {"name": "Старый ЖК", "price_range": "от 15 млн"}
        initial["visible_options"] = [{"name": "Старый ЖК", "price_range": "от 15 млн"}]
        initial["last_offer"] = {"subject_name": "Старый ЖК", "action": "show_selected_details"}
        app = make_app(initial, client=FakeClient(fail=True))

        result = await mod.run_chat(app, user_id="u", message="до 17", channel="jivo", meta={})

        saved = app["state_store"].states["u"]["nmbot_v2"]
        assert result["selected_option"] is None
        assert saved.get("selected_option_name") is None
        assert saved.get("last_offer", {}) == {}

    asyncio.run(scenario())


def test_nonrecoverable_search_failure_clears_stale_context_without_operator_pending(monkeypatch) -> None:
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        monkeypatch.setattr(sys.modules["nmbot_runtime_adapter"], "_is_v2_terminal_operator_offer", lambda _answer: False)
        patch_planner(monkeypatch, {"operation": "refine_search", "constraints_delta": {"hard": {"max_price": 17_000_000}}, "confidence": 1.0})
        initial = mod._default_state()
        initial["selected_option"] = {"name": "Старый ЖК", "price_range": "от 15 млн"}
        initial["visible_options"] = [{"name": "Старый ЖК", "price_range": "от 15 млн"}]
        initial["last_offer"] = {"subject_name": "Старый ЖК", "action": "show_selected_details"}
        app = make_app(initial, client=FakeClient(fail=True))

        result = await mod.run_chat(app, user_id="u", message="до 17", channel="jivo", meta={})

        saved = app["state_store"].states["u"]["nmbot_v2"]
        assert result["selected_option"] is None
        assert saved.get("selected_option_name") is None
        assert saved.get("last_offer", {}) == {}
        assert saved.get("pending_followup") is None

    asyncio.run(scenario())


def test_v0_gateway_redaction_keeps_building_numbers_but_hides_phone() -> None:
    building_text = "Корпуса 2.1-2.4 сдаются в 4 кв. 2025 г., корпуса 3.1-3.6 — во 2 кв. 2027 г."

    assert runtime_adapter_mod._redact(building_text) == building_text
    assert runtime_adapter_mod._redact("Телефон +7 999 123-45-67") == "Телефон [redacted-contact]"


def test_safe_trace_allows_bounded_execution_path_without_payloads() -> None:
    class LocalPlanner:
        def plan(self, context, state):
            return SemanticPlan(operation="search")

    class LocalSearch:
        def search(self, plan, state):
            return SearchResult.from_dict({"facts": [{"name": "Лучи"}]})

    turn = TurnProcessor(planner=LocalPlanner(), search_service=LocalSearch()).process(SafeTurnContext(conversation_ref="c", user_text="найди"))
    turn.trace["execution_path"]["stages"][0]["payload"] = "secret"

    safe = runtime_adapter_mod._safe_trace(turn)

    assert safe["execution_path"]["path_id"] == "v2.turn.v1"
    assert safe["execution_path"]["stages"][0] == {"stage_id": "v2.planner", "status": "completed"}
    assert "secret" not in json.dumps(safe, ensure_ascii=False)


def test_api_journal_projection_marks_jivo_prepare_boundary() -> None:
    result = {
        "meta": {
            "trace": {
                "execution_path": {
                    "schema": "nmbot.execution_path.v1",
                    "path_id": "v2.turn.v1",
                    "stages": [{"stage_id": "v2.runtime_finalize", "status": "completed"}],
                }
            }
        }
    }

    projected = mod._journal_execution_path(result, jivo_prepare=True)

    assert projected["path_id"] == "jivo.v2.turn.v1"
    assert projected["stages"][-1] == {"stage_id": "jivo.api.prepare", "status": "completed"}


def test_attempts_from_meta_preserves_safe_main_search_gateway_details() -> None:
    attempts = runtime_adapter_mod._attempts_from_meta({
        "_main_search_attempt": {"stage": "gateway_attempt", "model": "google/gemini", "ok": False, "empty": True, "safe": False, "gateway_task_id": "task-1", "duration_ms": 12, "parse_status": "ok", "raw": "secret"},
        "_search_fallback_attempts": [
            {"model": "deepseek/model", "ok": True, "empty": False, "safe": False, "gateway_task_id": "task-2", "duration_ms": 34, "parse_status": "ok", "query": "secret"}
        ],
    })

    assert attempts == (
        {"stage": "gateway_attempt", "model": "google_gemini", "ok": False, "empty": True, "safe": False, "gateway_task_id": "task-1", "duration_ms": 12, "parse_status": "ok"},
        {"stage": "gateway_attempt", "model": "deepseek_model", "ok": True, "empty": False, "safe": False, "gateway_task_id": "task-2", "duration_ms": 34, "parse_status": "ok"},
    )
    assert "secret" not in json.dumps(attempts, ensure_ascii=False)


def test_safe_runtime_summary_trace_preserves_gateway_attempt_details_only() -> None:
    summary = runtime_adapter_mod._safe_runtime_summary_trace({
        "stage": "first_list",
        "action": "search",
        "gateway_attempt_details": [
            {"stage": "gateway_attempt", "model": "google/gemini", "ok": True, "gateway_task_id": "task-1", "duration_ms": 55, "parse_status": "ok", "payload": "secret"}
        ],
    })

    assert summary["gateway_attempt_details"] == [{"stage": "gateway_attempt", "model": "google_gemini", "ok": True, "gateway_task_id": "task-1", "duration_ms": 55, "parse_status": "ok"}]
    assert "secret" not in json.dumps(summary, ensure_ascii=False)


def test_safe_runtime_summary_trace_preserves_only_availability_evidence_allowlist() -> None:
    summary = runtime_adapter_mod._safe_runtime_summary_trace({
        "stage": "current_options",
        "action": "answer_selected_option",
        "option_enrichment": {
            "availability_evidence": {
                "requested": True,
                "confirmation": "confirmed",
                "source": "gateway",
                "gateway_task_id": "task-2386206/unsafe suffix",
                "inventory_value": 5242,
                "raw_mcp_text": "секретный сырой ответ",
                "query": "наличие квартир secret",
            },
            "count": 1,
            "items": [{"name": "Секретный ЖК"}],
        },
    })

    assert summary["option_enrichment"] == {
        "availability_evidence": {
            "requested": True,
            "confirmation": "confirmed",
            "source": "gateway",
            "gateway_task_id": "task-2386206_unsafe_suffix",
        }
    }
    assert set(summary["option_enrichment"]) == {"availability_evidence"}
    assert set(summary["option_enrichment"]["availability_evidence"]) == {"requested", "confirmation", "source", "gateway_task_id"}


def test_safe_runtime_summary_trace_preserves_intent_transition_allowlist_only() -> None:
    summary = runtime_adapter_mod._safe_runtime_summary_trace({
        "stage": "current_options",
        "action": "answer_from_current_options",
        "intent_transition": {
            "goal": "compare_current",
            "intent_validation": "accepted",
            "validation_error_codes": ["invalid_selected_option_scope", "comparison_option_not_visible", "raw_secret"],
            "transition": {"accepted": True, "error_code": "raw_secret", "selected_option_name": "Томилинский"},
            "fallback_used": False,
            "raw_plan": {"query_text": "сравни с томилиским бульваром"},
        },
    })

    assert summary["intent_transition"] == {
        "goal": "compare_current",
        "intent_validation": "accepted",
        "validation_error_codes": ["invalid_selected_option_scope", "comparison_option_not_visible"],
        "transition": {"accepted": True, "error_code": None},
        "fallback_used": False,
    }
    dumped = json.dumps(summary, ensure_ascii=False)
    for forbidden in ["raw_secret", "selected_option_name", "Томилинский", "query_text", "томилиским"]:
        assert forbidden not in dumped
    dumped = json.dumps(summary, ensure_ascii=False).lower()
    for forbidden in ("5242", "raw_mcp", "query", "секрет", "secret", "items"):
        assert forbidden not in dumped


def test_safe_runtime_summary_trace_preserves_pair_aggregate_only() -> None:
    summary = runtime_adapter_mod._safe_runtime_summary_trace({
        "stage": "current_options",
        "action": "answer_from_current_options",
        "pair_comparison": {
            "status": "partial_enrichment_failed",
            "requested_count": 2,
            "resolved_count": 2,
            "cache_hit_count": 1,
            "fetch_count": 1,
            "applied_count": 1,
            "failure_count": 1,
            "requested_fact_count": 3,
            "names": ["Левел Лесной", "Томилинский бульвар"],
            "raw_payload": "secret",
            "query": "сравни с Томилинским",
        },
    })

    assert summary["pair_comparison"] == {
        "status": "partial_enrichment_failed",
        "requested_count": 2,
        "resolved_count": 2,
        "cache_hit_count": 1,
        "fetch_count": 1,
        "applied_count": 1,
        "failure_count": 1,
        "requested_fact_count": 3,
    }
    dumped = json.dumps(summary, ensure_ascii=False)
    for forbidden in ("Левел", "Томилинский", "raw_payload", "secret", "query"):
        assert forbidden not in dumped


def test_safe_enrichment_trace_keeps_only_availability_evidence_diagnostics() -> None:
    trace = runtime_adapter_mod._safe_enrichment_trace({
        "enabled": True,
        "applied": True,
        "count": 1,
        "applied_count": 1,
        "requested_facts": ["apartment_inventory", "phone"],
        "fresh_facts": [],
        "outcome": "applied",
        "availability_evidence": {
            "requested": True,
            "confirmation": "not_confirmed",
            "source": "gateway",
            "gateway_task_id": "task-2386206/unsafe suffix",
            "raw_mcp_text": "секретный сырой ответ",
            "inventory_value": 5242,
            "query": "наличие квартир secret",
        },
        "items": [{"idx": 1, "applied": True, "source": "fetch", "skipped": "secret token"}],
        "raw_cards": [{"name": "Секретный ЖК"}],
    })

    assert trace["availability_evidence"] == {
        "requested": True,
        "confirmation": "not_confirmed",
        "source": "gateway",
        "gateway_task_id": "task-2386206_unsafe_suffix",
    }
    dumped = json.dumps(trace, ensure_ascii=False)
    assert "секрет" not in dumped.lower()
    assert "5242" not in dumped
    assert "raw_mcp_text" not in dumped
    assert "query" not in dumped
    assert "token" not in dumped.lower()


def test_selected_availability_evidence_reports_confirmed_only_from_fresh_facts() -> None:
    trace = runtime_adapter_mod._selected_availability_evidence_trace(
        requested_facts=("apartment_inventory",),
        fresh_facts=("apartment_inventory",),
        meta={"source": "cache"},
    )

    assert trace == {"requested": True, "confirmation": "confirmed", "source": "cache"}


class FakeStore:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.states: dict[str, dict[str, Any]] = {"u": dict(initial or mod._default_state())}
        self.saved: list[tuple[str, dict[str, Any]]] = []

    async def get(self, user_id: str) -> dict[str, Any]:
        return self.states.setdefault(user_id, mod._default_state())

    async def save(self, user_id: str, state: dict[str, Any]) -> None:
        self.states[user_id] = dict(state)
        self.saved.append((user_id, dict(state)))


class FakeRuntimeVersionStore:
    def __init__(self, version: str = "V2") -> None:
        self.version = version

    async def get(self) -> str:
        return self.version

    async def set(self, version: str) -> str:
        self.version = version
        return self.version


class FakeClient:
    def __init__(self, *, fail: bool = False, options: list[dict[str, Any]] | None = None, enriched: dict[str, Any] | None = None, bad_diagnostics: bool = False, composer_provider_error: bool = False) -> None:
        self.fail = fail
        self.options = options or [{"name": "Лучи", "price_range": "от 12 млн рублей", "location": "Москва"}]
        self.enriched = enriched
        self.bad_diagnostics = bad_diagnostics
        self.composer_provider_error = composer_provider_error
        self.ask_calls = 0
        self.gateway_calls = 0
        self.gateway_once_calls = 0
        self.gateway_payloads: list[dict[str, Any]] = []
        self.gateway_once_payloads: list[dict[str, Any]] = []
        self.ask_queries: list[str] = []
        self.enrich_calls = 0
        self.explain_calls = 0
        self.explain_payloads: list[dict[str, Any]] = []
        self.composer_calls = 0

    async def ensure_session(self):
        return object()

    async def ask(self, *args: Any, **kwargs: Any):
        self.ask_calls += 1
        self.ask_queries.append(str(args[0] if args else kwargs.get("message") or kwargs.get("query") or ""))
        if self.fail:
            raise RuntimeError("provider down")
        payload = json.dumps({"facts": self.options, "near": [], "missing": []}, ensure_ascii=False)
        return "Нашла варианты", {"location": "Москва"}, {"_response_text": payload}, {"_visible_options": [{"name": item["name"]} for item in self.options]}

    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        self.gateway_calls += 1
        self.gateway_payloads.append(request_data)
        if self.fail:
            return "", {"_safe_fallback": True, "_upstream_error": True}
        if request_data.get("_payload_stage") == "conversation_answer":
            self.composer_calls += 1
            query = str(request_data.get("query") or "")
            input_part = query.split("V2_RESPONSE_BRIEF=", 1)[1].split("\n", 1)[0] if "V2_RESPONSE_BRIEF=" in query else "{}"
            payload = json.loads(input_part)
            brief = payload.get("brief", {}) if isinstance(payload.get("brief"), dict) else {}
            cards = brief.get("canonical_cards") or []
            names = [str(item.get("name")) for item in cards if isinstance(item, dict) and item.get("name")]
            name = names[0] if names else "вариант"
            first = cards[0] if cards and isinstance(cards[0], dict) else {}
            developer = f", застройщик {first.get('developer')}" if first.get("developer") else ""
            location = str(first.get("location") or "").strip()
            price = first.get("price") or first.get("price_min")
            fact_parts = []
            if location:
                fact_parts.append(location)
            if isinstance(price, (int, float)):
                fact_parts.append(f"цены от {price / 1_000_000:g} млн рублей".replace(".", ","))
            elif price:
                fact_parts.append(str(price))
            fact_line = ", ".join(fact_parts) or f"ЖК «{name}» показан по подтверждённым данным"
            acknowledgement = str(brief.get("acknowledgement") or "").strip()
            prefix = acknowledgement.split("\n\n", 1)[0].strip() if acknowledgement else ""
            final_question = str(brief.get("fallback_question") or "Хотите рассмотреть его подробнее?").strip()
            return json.dumps(
                {
                    "intro": prefix or "Нашла вариант по подтверждённым данным.",
                    "options": [{"name": name, "facts": f"{fact_line}{developer}.", "description": "Эти факты помогают спокойно сравнить вариант с другими предложениями."}] if names else [],
                    "recommendation": "",
                    "missing_note": "",
                    "final_question": final_question,
                },
                ensure_ascii=False,
            ), {"ok": True}
        query = str(request_data.get("query") or "")
        params_part = query.split("Текущие параметры: ", 1)[1].split("\n", 1)[0] if "Текущие параметры: " in query else "{}"
        envelope_part = query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0] if "SEARCH_CONTRACT_ENVELOPE=" in query else "{}"
        payload = json.loads(params_part)
        envelope = json.loads(envelope_part)
        constraints = {
            "effective_hard": payload.get("effective_hard") if isinstance(payload.get("effective_hard"), dict) else {},
            "relaxation_audit": payload.get("relaxation_audit") if isinstance(payload.get("relaxation_audit"), list) else [],
        }
        diagnostics = {
            "mcp_tool": "novostroym/get_flat_info",
            "response_viewpoint": envelope.get("response_viewpoint"),
            "base_viewpoint": envelope.get("base_viewpoint"),
            "requested_field_priorities": list(envelope.get("available_fact_fields") or [])[:12],
            "relaxation_audit": list(constraints.get("relaxation_audit") or []),
            "ignored_preferences": [],
            "notes": [],
        }
        if self.bad_diagnostics:
            diagnostics = {"mcp_tool": "wrong/tool", "requested_field_priorities": []}
        facts = []
        for item in self.options:
            fact = dict(item)
            if "price_range" in fact and "min_price" not in fact:
                fact["min_price"] = 12_000_000
                fact.pop("price_range", None)
            effective_hard = constraints.get("effective_hard") or {}
            if isinstance(effective_hard, dict) and effective_hard.get("district") and "district" not in fact:
                fact["district"] = effective_hard["district"]
            facts.append(fact)
        output = {"facts": facts, "near": [], "missing": [], "params": dict(constraints.get("effective_hard") or {}), "diagnostics": diagnostics}
        return json.dumps(output, ensure_ascii=False), {"ok": True}

    async def _run_gateway_request_once(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        self.gateway_once_calls += 1
        self.gateway_once_payloads.append(request_data)
        if request_data.get("_payload_stage") == "conversation_answer_writer":
            self.composer_calls += 1
            if self.composer_provider_error:
                return "", {"_provider_error_code": "provider_invalid_argument", "ok": False}
            query = str(request_data.get("query") or "")
            if "V3_ANSWER_BRIEF=" in query:
                input_part = query.split("V3_ANSWER_BRIEF=", 1)[1].split("\n", 1)[0]
                payload = json.loads(input_part)
                brief = payload.get("answer_brief", {}) if isinstance(payload.get("answer_brief"), dict) else {}
                cards = brief.get("canonical_found_cards") or []
                names = [str(item.get("name")) for item in cards if isinstance(item, dict) and item.get("name")]
                hard_rules = brief.get("hard_rules") if isinstance(brief.get("hard_rules"), dict) else {}
                final_question = str(hard_rules.get("cta_template") or hard_rules.get("fallback_question") or "Хотите рассмотреть его подробнее?").strip()
                first = cards[0] if cards and isinstance(cards[0], dict) else {}
                facts = first.get("facts") if isinstance(first.get("facts"), dict) else {}
                location = str(facts.get("location") or "").strip()
                price = str(facts.get("price") or facts.get("price_min") or "").strip()
                fact_line = ", ".join(part for part in (location, f"цена {price}" if price else "") if part) or "Показан по подтверждённым данным"
            else:
                input_part = query.split("V2_RESPONSE_BRIEF=", 1)[1].split("\n", 1)[0] if "V2_RESPONSE_BRIEF=" in query else "{}"
                payload = json.loads(input_part)
                brief = payload.get("brief", {}) if isinstance(payload.get("brief"), dict) else {}
                cards = brief.get("canonical_cards") or []
                names = [str(item.get("name")) for item in cards if isinstance(item, dict) and item.get("name")]
                final_question = str(brief.get("fallback_question") or "Хотите рассмотреть его подробнее?").strip()
                fact_line = "Показан по подтверждённым данным"
            name = names[0] if names else "вариант"
            return json.dumps(
                {
                    "intro": "Нашла вариант по вашим условиям.",
                    "cards": [{"name": name, "text": f"{fact_line}. Эти факты помогают спокойно сравнить вариант с другими предложениями."}] if names else [],
                    "recommendation": "",
                    "missing_note": "",
                    "final_question": final_question,
                },
                ensure_ascii=False,
            ), {"ok": True}
        if request_data.get("_payload_stage") == "conversation_answer_formatter":
            query = str(request_data.get("query") or "")
            input_part = query.split("V2_RESPONSE_FORMATTER_INPUT=", 1)[1] if "V2_RESPONSE_FORMATTER_INPUT=" in query else "{}"
            payload = json.loads(input_part)
            names = [str(item) for item in payload.get("expected_card_names", []) if item]
            name = names[0] if names else "вариант"
            final_question = str(payload.get("final_question") or "Хотите рассмотреть его подробнее?").strip()
            return json.dumps(
                {
                    "intro": "Нашла вариант по вашим условиям.",
                    "cards": [{"name": name, "text": "Москва, цена от 12 млн рублей. Эти факты помогают спокойно сравнить вариант с другими предложениями."}] if names else [],
                    "recommendation": "",
                    "missing_note": "",
                    "final_question": final_question,
                },
                ensure_ascii=False,
            ), {"ok": True}
        return await self._run_gateway_request(request_data, headers, timeout)

    async def fetch_enriched_option(self, *_args: Any, **_kwargs: Any):
        self.enrich_calls += 1
        return self.enriched or {"name": "Лучи", "price_range": "от 12 млн рублей", "developer": "ПИК"}, {"ok": True}

    async def explain_consultation_followup(self, **kwargs: Any):
        self.explain_calls += 1
        self.explain_payloads.append(kwargs)
        return "Отвечаю по текущим вариантам без нового поиска.", {"ok": True}


class LowLevelOnlyClient:
    def __init__(self, *, options: list[dict[str, Any]] | None = None) -> None:
        self.inner = FakeClient(options=options)

    @property
    def ask_calls(self) -> int:
        return self.inner.ask_calls

    @property
    def gateway_calls(self) -> int:
        return self.inner.gateway_calls

    @property
    def composer_calls(self) -> int:
        return self.inner.composer_calls

    @property
    def gateway_payloads(self) -> list[dict[str, Any]]:
        return self.inner.gateway_payloads

    async def ensure_session(self):
        return await self.inner.ensure_session()

    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        return await self.inner._run_gateway_request(request_data, headers, timeout)


class SequenceGatewayClient:
    def __init__(self, responses: list[dict[str, Any] | Exception | str]) -> None:
        self.responses = list(responses)
        self.gateway_payloads: list[dict[str, Any]] = []

    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        self.gateway_payloads.append(request_data)
        response = self.responses.pop(0) if self.responses else {"facts": [], "near": []}
        if isinstance(response, Exception):
            raise response
        if isinstance(response, str):
            return response, {"ok": True, "attempts": [{"provider": "fake", "idx": len(self.gateway_payloads)}]}
        if response.get("safe_fallback"):
            return "", {"_safe_fallback": True, "_upstream_error": True, "attempts": [{"provider": "fake", "idx": len(self.gateway_payloads)}]}
        output = _valid_search_output_for_request(request_data, facts=response.get("facts") or [], near=response.get("near") or [])
        return json.dumps(output, ensure_ascii=False), {"ok": True, "attempts": [{"provider": "fake", "idx": len(self.gateway_payloads)}]}


class V0GatewayClient:
    def __init__(self, *, malformed_scenario: bool = False) -> None:
        self.gateway_payloads: list[dict[str, Any]] = []
        self.malformed_scenario = malformed_scenario

    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        self.gateway_payloads.append(request_data)
        stage = request_data.get("_payload_stage")
        if stage == "nmbot_v0_scenario_search":
            if self.malformed_scenario:
                return "not-json", {"ok": True}
            return json.dumps(
                {
                    "decision": {"action": "search", "viewpoint": "life", "params": {"rooms": 2}, "active_topic": "life"},
                    "search": {
                        "facts": [
                            {"name": "ЖК Первый", "location": "Москва", "min_price": 10000000},
                            {"name": "ЖК Второй", "location": "Москва", "min_price": 11000000},
                            {"name": "ЖК Третий", "location": "Москва", "min_price": 12000000},
                        ],
                        "near": [],
                        "missing": [],
                        "params": {},
                    },
                },
                ensure_ascii=False,
            ), {"ok": True}
        if stage == "nmbot_v0_answer":
            query = str(request_data.get("query") or "")
            brief_raw = query.split("V0_ANSWER_BRIEF=", 1)[1].split("\n", 1)[0]
            brief = json.loads(brief_raw)
            options = [{"name": item["name"]} for item in brief["allowed_cards"]]
            decision = brief["decision"]
            return json.dumps(
                {
                    "answer_kind": decision["expected_answer_kind"],
                    "scope": decision["expected_scope"],
                    "intro": "Этот текст runtime заменит детерминированным.",
                    "options": options,
                    "recommendation": "",
                    "missing_note": "",
                    "final_question": decision["cta_template"],
                },
                ensure_ascii=False,
            ), {"ok": True}
        raise AssertionError(f"unexpected stage {stage}")


class V0EmptyThenRecoveredGatewayClient(V0GatewayClient):
    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        self.gateway_payloads.append(request_data)
        stage = request_data.get("_payload_stage")
        if stage == "nmbot_v0_scenario_search":
            query = str(request_data.get("query") or "")
            context = json.loads(query.split("V0_SCENARIO_SEARCH_CONTEXT=", 1)[1].split("\n", 1)[0])
            if "search_recovery" not in context:
                return json.dumps(
                    {
                        "decision": {"action": "search", "viewpoint": "life", "params": {"budget": "до 12 млн"}, "active_topic": "life"},
                        "search": {"facts": [], "near": [], "missing": [], "params": {}},
                    },
                    ensure_ascii=False,
                ), {"ok": True}
            assert context["search_recovery"]["reason"] == "valid_empty_search"
            assert context["search_recovery"]["attempt"] == 1
            return json.dumps(
                {
                    "decision": {"action": "operator", "viewpoint": "life"},
                    "search": {"facts": [{"name": "ЖК Recovery", "location": "Москва", "min_price": 11_000_000}], "near": [], "missing": [], "params": {}},
                },
                ensure_ascii=False,
            ), {"ok": True}
        if stage == "nmbot_v0_answer":
            query = str(request_data.get("query") or "")
            brief_raw = query.split("V0_ANSWER_BRIEF=", 1)[1].split("\n", 1)[0]
            brief = json.loads(brief_raw)
            decision = brief["decision"]
            return json.dumps(
                {
                    "answer_kind": decision["expected_answer_kind"],
                    "scope": decision["expected_scope"],
                    "intro": "runtime replaces",
                    "options": [{"name": item["name"]} for item in brief["allowed_cards"]],
                    "recommendation": "",
                    "missing_note": "",
                    "final_question": decision["cta_template"],
                },
                ensure_ascii=False,
            ), {"ok": True}
        raise AssertionError(stage)


class V0PendingPhoneQuestionClient(V0GatewayClient):
    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        self.gateway_payloads.append(request_data)
        stage = request_data.get("_payload_stage")
        if stage == "nmbot_v0_scenario_search":
            query = str(request_data.get("query") or "")
            context = json.loads(query.split("V0_SCENARIO_SEARCH_CONTEXT=", 1)[1].split("\n", 1)[0])
            assert context["user_text"] == "Номер пока не хочу оставлять. Просто скажите: без оператора вы отделку проверить не можете?"
            assert context["state"]["pending_action"] == "contact_phone"
            assert context["state"]["selected_option_name"] == "Мичуринский парк"
            assert context["state"]["active_topic"] == "family"
            return json.dumps(
                {
                    "decision": {
                        "action": "selected_object",
                        "viewpoint": "family",
                        "active_topic": "family",
                        "selected_option_name": "Мичуринский парк",
                        "requested_facts": ["finishing"],
                        "followup_outcome": "new_question",
                    },
                    "search": {"facts": [{"name": "Мичуринский парк", "location": "Москва", "finishing": "с отделкой", "min_price": 18_000_000}], "near": [], "missing": [], "params": {}},
                },
                ensure_ascii=False,
            ), {"ok": True}
        if stage == "nmbot_v0_answer":
            query = str(request_data.get("query") or "")
            brief = json.loads(query.split("V0_ANSWER_BRIEF=", 1)[1].split("\n", 1)[0])
            return json.dumps({"answer_kind": brief["decision"]["expected_answer_kind"], "scope": brief["decision"]["expected_scope"], "intro": "runtime replaces", "options": [{"name": item["name"]} for item in brief["allowed_cards"]], "recommendation": "", "missing_note": "", "final_question": brief["decision"]["cta_template"]}, ensure_ascii=False), {"ok": True}
        raise AssertionError(stage)


class V0PendingPhoneDeclineOperatorClient(V0GatewayClient):
    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        self.gateway_payloads.append(request_data)
        stage = request_data.get("_payload_stage")
        if stage == "nmbot_v0_scenario_search":
            query = str(request_data.get("query") or "")
            context = json.loads(query.split("V0_SCENARIO_SEARCH_CONTEXT=", 1)[1].split("\n", 1)[0])
            assert context["state"]["pending_action"] == "contact_phone"
            assert context["state"]["selected_option_name"] == "Мичуринский парк"
            assert context["state"]["active_topic"] == "family"
            return json.dumps(
                {
                    "decision": {
                        "action": "operator",
                        "viewpoint": "family",
                        "active_topic": "family",
                        "selected_option_name": "Мичуринский парк",
                        "response_policy": "operator_phone_request",
                        "followup_outcome": "decline",
                    },
                    "search": {},
                },
                ensure_ascii=False,
            ), {"ok": True}
        if stage == "nmbot_v0_answer":
            query = str(request_data.get("query") or "")
            brief = json.loads(query.split("V0_ANSWER_BRIEF=", 1)[1].split("\n", 1)[0])
            assert brief["decision"]["action"] == "open_question"
            assert brief["decision"]["response_policy"] == "answer_directly"
            assert brief["decision"]["expected_scope"] != "operator_phone"
            return json.dumps({"answer_kind": brief["decision"]["expected_answer_kind"], "scope": brief["decision"]["expected_scope"], "intro": "runtime replaces", "options": [], "recommendation": "", "missing_note": "", "final_question": brief["decision"]["cta_template"]}, ensure_ascii=False), {"ok": True}
        raise AssertionError(stage)


class V0PendingPhoneSemanticAcceptClient(V0GatewayClient):
    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        self.gateway_payloads.append(request_data)
        stage = request_data.get("_payload_stage")
        if stage == "nmbot_v0_scenario_search":
            query = str(request_data.get("query") or "")
            context = json.loads(query.split("V0_SCENARIO_SEARCH_CONTEXT=", 1)[1].split("\n", 1)[0])
            assert context["user_text"] == "да, всё верно"
            assert context["state"]["pending_action"] == "contact_phone"
            assert context["state"]["selected_option_name"] == "Мичуринский парк"
            assert context["state"]["active_topic"] == "family"
            return json.dumps(
                {
                    "decision": {
                        "action": "current_options",
                        "viewpoint": "family",
                        "active_topic": "family",
                        "selected_option_name": "Мичуринский парк",
                        "followup_outcome": "accept",
                    },
                    "search": {},
                },
                ensure_ascii=False,
            ), {"ok": True}
        raise AssertionError(stage)


class V0PendingPhoneOffTopicClient(V0GatewayClient):
    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        self.gateway_payloads.append(request_data)
        stage = request_data.get("_payload_stage")
        if stage == "nmbot_v0_scenario_search":
            query = str(request_data.get("query") or "")
            context = json.loads(query.split("V0_SCENARIO_SEARCH_CONTEXT=", 1)[1].split("\n", 1)[0])
            assert context["user_text"] == "а как сварить пельмени?"
            assert context["state"]["pending_action"] == "contact_phone"
            assert context["state"]["selected_option_name"] == "Мичуринский парк"
            assert context["state"]["active_topic"] == "family"
            return json.dumps(
                {
                    "decision": {"action": "off_topic", "viewpoint": "family", "active_topic": "family", "followup_outcome": "new_question"},
                    "search": {"facts": [], "near": [], "missing": [], "params": {}},
                },
                ensure_ascii=False,
            ), {"ok": True}
        if stage == "nmbot_v0_answer":
            query = str(request_data.get("query") or "")
            brief = json.loads(query.split("V0_ANSWER_BRIEF=", 1)[1].split("\n", 1)[0])
            return json.dumps({"answer_kind": brief["decision"]["expected_answer_kind"], "scope": brief["decision"]["expected_scope"], "intro": "runtime replaces", "options": [], "recommendation": "", "missing_note": "", "final_question": brief["decision"]["cta_template"]}, ensure_ascii=False), {"ok": True}
        raise AssertionError(stage)


def _request_parts(request_data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    query = str(request_data.get("query") or "")
    envelope = json.loads(query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])
    params = json.loads(query.split("Текущие параметры: ", 1)[1].split("\n", 1)[0])
    return envelope, params


def test_response_composer_primary_success_does_not_call_bluesminds_interceptor(monkeypatch) -> None:
    class PrimarySuccessClient:
        async def _run_gateway_request_once(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            assert request_data["_payload_stage"] == "conversation_answer_writer"
            return "primary writer text", {"ok": True}

    async def fake_compose(brief: Any, *, fallback_text: str, writer: Any, formatter: Any) -> tuple[Any, dict[str, Any]]:
        return await writer(brief, model="primary-model")

    def payload_builder(_brief: Any, *, model: str) -> dict[str, Any]:
        return {"_payload_stage": "conversation_answer_writer", "system_prompt": "system", "query": "query", "model": model}

    monkeypatch.setattr(runtime_adapter_mod, "build_response_writer_payload", payload_builder)
    monkeypatch.setattr(runtime_adapter_mod, "compose_response_writer_formatter_async", fake_compose)
    monkeypatch.setattr(runtime_adapter_mod.bluesminds_answer_interceptor, "is_enabled", lambda: True)

    async def forbidden_try_answer(_payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        raise AssertionError("interceptor must not run after primary success")

    monkeypatch.setattr(runtime_adapter_mod.bluesminds_answer_interceptor, "try_answer", forbidden_try_answer)
    result = asyncio.run(runtime_adapter_mod._ResponseComposerAdapter({"overmind_client": PrimarySuccessClient()}).compose_response(object(), fallback_text="fallback"))
    assert result == ("primary writer text", {"ok": True})


def test_response_composer_primary_failure_uses_bluesminds_when_enabled(monkeypatch) -> None:
    class PrimaryFailureClient:
        async def _run_gateway_request_once(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            return "", {"ok": False, "_upstream_error": True, "error_code": "primary_failed"}

    def payload_builder(_brief: Any, *, model: str) -> dict[str, Any]:
        return {"_payload_stage": "conversation_answer_writer", "system_prompt": "system", "query": "query", "model": model}

    async def fake_compose(brief: Any, *, fallback_text: str, writer: Any, formatter: Any) -> tuple[Any, dict[str, Any]]:
        return await writer(brief, model="primary-model")

    async def fake_try_answer(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        assert payload == {"_payload_stage": "conversation_answer_writer", "system_prompt": "system", "query": "query", "model": "primary-model"}
        return "bluesminds text", {"ok": True, "_gateway_client_impl": "bluesminds_interceptor", "_fallback_used": True, "model": "gpt-test"}

    monkeypatch.setattr(runtime_adapter_mod, "build_response_writer_payload", payload_builder)
    monkeypatch.setattr(runtime_adapter_mod, "compose_response_writer_formatter_async", fake_compose)
    monkeypatch.setattr(runtime_adapter_mod.bluesminds_answer_interceptor, "is_enabled", lambda: True)
    monkeypatch.setattr(runtime_adapter_mod.bluesminds_answer_interceptor, "try_answer", fake_try_answer)

    result = asyncio.run(runtime_adapter_mod._ResponseComposerAdapter({"overmind_client": PrimaryFailureClient()}).compose_response(object(), fallback_text="fallback"))
    assert result == (
        "bluesminds text",
        {"ok": True, "_gateway_client_impl": "bluesminds_interceptor", "_fallback_used": True, "model": "gpt-test", "_interceptor_after_primary_failure": True},
    )


def test_response_composer_primary_failure_disabled_preserves_original_meta(monkeypatch) -> None:
    primary_meta = {"ok": False, "_upstream_error": True, "error_code": "primary_failed"}

    class PrimaryFailureClient:
        async def _run_gateway_request_once(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            return "", primary_meta

    async def fake_compose(brief: Any, *, fallback_text: str, writer: Any, formatter: Any) -> tuple[Any, dict[str, Any]]:
        return await writer(brief, model="primary-model")

    def payload_builder(_brief: Any, *, model: str) -> dict[str, Any]:
        return {"_payload_stage": "conversation_answer_writer", "system_prompt": "system", "query": "query", "model": model}

    monkeypatch.setattr(runtime_adapter_mod, "build_response_writer_payload", payload_builder)
    monkeypatch.setattr(runtime_adapter_mod, "compose_response_writer_formatter_async", fake_compose)
    monkeypatch.setattr(runtime_adapter_mod.bluesminds_answer_interceptor, "is_enabled", lambda: False)
    result = asyncio.run(runtime_adapter_mod._ResponseComposerAdapter({"overmind_client": PrimaryFailureClient()}).compose_response(object(), fallback_text="fallback"))
    assert result == ("", primary_meta)


def test_v3_response_composer_adapter_uses_v3_writer_payload_and_provenance(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class PrimarySuccessClient:
        async def _run_gateway_request_once(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            captured["payload"] = request_data
            return "primary writer text", {"ok": True}

    async def fake_compose(brief: Any, *, fallback_text: str, writer: Any, formatter: Any, writer_prompt_identity: dict[str, Any] | None = None, validation_mode: str = "v2") -> tuple[Any, dict[str, Any]]:
        captured["prompt_identity"] = writer_prompt_identity
        captured["validation_mode"] = validation_mode
        return await writer(brief, model="primary-model")

    monkeypatch.setattr(runtime_adapter_mod, "compose_response_writer_formatter_async", fake_compose)
    monkeypatch.setattr(runtime_adapter_mod.bluesminds_answer_interceptor, "is_enabled", lambda: False)

    brief = ResponseBrief(
        answer_goal="present_search_results",
        user_question="найди двушку",
        canonical_cards=(OptionCard(name="ЖК «Первый»", rooms=2, location="Центр", price_min=12_000_000),),
        scenario_context={"content_source": "scenario_context_only"},
        cta_template="Какой вариант хотите рассмотреть подробнее?",
        fallback_question="Какой вариант хотите рассмотреть подробнее?",
    )
    result = asyncio.run(runtime_adapter_mod._ResponseComposerAdapter({"overmind_client": PrimarySuccessClient()}, runtime_version="v3").compose_response(brief, fallback_text="fallback"))
    assert result == ("primary writer text", {"ok": True})
    assert captured["payload"]["query"].startswith("V3_ANSWER_BRIEF=")
    assert "V2_RESPONSE_BRIEF" not in captured["payload"]["query"]
    assert captured["prompt_identity"]["source"] == "prompts/v3_answer_writer.txt"
    assert captured["validation_mode"] == "v3"


def test_manager_rewriter_bluesminds_disabled_uses_gateway(monkeypatch) -> None:
    class GatewayClient:
        calls = 0

        async def _run_gateway_request_once(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            self.calls += 1
            assert request_data["_payload_stage"] == "conversation_answer_manager_rewriter"
            return "gateway rewrite", {"ok": True}

    monkeypatch.setattr(runtime_adapter_mod.bluesminds_manager_rewriter, "is_enabled", lambda: False)
    gateway = GatewayClient()
    rewriter = runtime_adapter_mod._ManagerRewriterAdapter({"overmind_client": gateway}, runtime_version="v3")
    result = asyncio.run(rewriter.rewrite_manager_answer(transcript=(), current_question="q", prepared_answer="a", brief=object()))
    assert result == {"text": "gateway rewrite", "meta": {"provider": "gateway", "fallback": True, "reason": "disabled"}}
    assert gateway.calls == 1


def test_v3_manager_rewriter_bluesminds_primary_skips_gateway(monkeypatch) -> None:
    class GatewayClient:
        async def _run_gateway_request_once(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            raise AssertionError("gateway fallback must not run after Bluesminds success")

    async def fake_try_rewrite(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        assert payload["_payload_stage"] == "conversation_answer_manager_rewriter"
        return "bluesminds rewrite", {"ok": True, "model": "safe-model"}

    monkeypatch.setattr(runtime_adapter_mod.bluesminds_manager_rewriter, "is_enabled", lambda: True)
    monkeypatch.setattr(runtime_adapter_mod.bluesminds_manager_rewriter, "try_rewrite", fake_try_rewrite)
    rewriter = runtime_adapter_mod._ManagerRewriterAdapter({"overmind_client": GatewayClient()}, runtime_version="v3")
    result = asyncio.run(rewriter.rewrite_manager_answer(transcript=(), current_question="q", prepared_answer="a", brief=object()))
    assert result == {"text": "bluesminds rewrite", "meta": {"provider": "bluesminds", "fallback": False, "reason": "none"}}


def test_v3_manager_rewriter_bluesminds_error_or_empty_uses_gateway(monkeypatch) -> None:
    class GatewayClient:
        calls = 0

        async def _run_gateway_request_once(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            self.calls += 1
            return "gateway fallback", {"ok": True}

    async def fake_try_rewrite(_payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return "", {"ok": False, "error_code": "bluesminds_manager_rewriter_exception", "_upstream_error": True}

    monkeypatch.setattr(runtime_adapter_mod.bluesminds_manager_rewriter, "is_enabled", lambda: True)
    monkeypatch.setattr(runtime_adapter_mod.bluesminds_manager_rewriter, "try_rewrite", fake_try_rewrite)
    gateway = GatewayClient()
    rewriter = runtime_adapter_mod._ManagerRewriterAdapter({"overmind_client": gateway}, runtime_version="v3")
    result = asyncio.run(rewriter.rewrite_manager_answer(transcript=(), current_question="q", prepared_answer="a", brief=object()))
    assert result == {"text": "gateway fallback", "meta": {"provider": "gateway", "fallback": True, "reason": "exception"}}
    assert gateway.calls == 1


def test_v3_manager_rewriter_bluesminds_empty_uses_gateway_marker(monkeypatch) -> None:
    class GatewayClient:
        async def _run_gateway_request_once(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            return "gateway fallback", {"ok": True}

    async def fake_try_rewrite(_payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return "", {"ok": True, "model": "must-not-leak"}

    monkeypatch.setattr(runtime_adapter_mod.bluesminds_manager_rewriter, "is_enabled", lambda: True)
    monkeypatch.setattr(runtime_adapter_mod.bluesminds_manager_rewriter, "try_rewrite", fake_try_rewrite)
    rewriter = runtime_adapter_mod._ManagerRewriterAdapter({"overmind_client": GatewayClient()}, runtime_version="v3")

    result = asyncio.run(rewriter.rewrite_manager_answer(transcript=(), current_question="q", prepared_answer="a", brief=object()))

    assert result == {"text": "gateway fallback", "meta": {"provider": "gateway", "fallback": True, "reason": "empty"}}


def test_v2_and_v0_manager_rewriter_do_not_use_bluesminds(monkeypatch) -> None:
    class GatewayClient:
        calls = 0

        async def _run_gateway_request_once(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            self.calls += 1
            return "gateway", {"ok": True}

    async def forbidden_try_rewrite(_payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        raise AssertionError("Bluesminds is V3 manager-rewriter only")

    monkeypatch.setattr(runtime_adapter_mod.bluesminds_manager_rewriter, "is_enabled", lambda: True)
    monkeypatch.setattr(runtime_adapter_mod.bluesminds_manager_rewriter, "try_rewrite", forbidden_try_rewrite)
    for runtime_version in ("v2", "v0"):
        gateway = GatewayClient()
        rewriter = runtime_adapter_mod._ManagerRewriterAdapter({"overmind_client": gateway}, runtime_version=runtime_version)
        assert asyncio.run(rewriter.rewrite_manager_answer(transcript=(), current_question="q", prepared_answer="a", brief=object())) == "gateway"
        assert gateway.calls == 1


def test_manager_rewriter_bluesminds_safe_metadata_has_no_prompt_or_secrets() -> None:
    meta = runtime_adapter_mod.bluesminds_manager_rewriter.config_status(
        {
            "NMBOT_V3_MANAGER_REWRITER_BLUESMINDS_ENABLED": "1",
            "NMBOT_V3_MANAGER_REWRITER_BLUESMINDS_MODEL": "safe-model",
            "NMBOT_V3_MANAGER_REWRITER_BLUESMINDS_TIMEOUT": "7",
            "OPENROUTER_API_KEY": "secret",
        }
    )
    assert meta == {"enabled": True, "model": "safe-model", "timeout": 7}
    assert "system_prompt" not in meta
    assert "query" not in meta
    assert "OPENROUTER_API_KEY" not in meta


def test_manager_rewriter_bluesminds_client_import_resolves_from_scripts_without_network(monkeypatch) -> None:
    monkeypatch.delenv("BLUESMINDS_API_KEY", raising=False)
    scripts_dir = str(ROOT / "scripts")
    monkeypatch.syspath_prepend(scripts_dir)
    sys.modules.pop("bluesminds_client", None)

    from bluesminds_client import BluesmindsClient  # type: ignore

    module = sys.modules["bluesminds_client"]
    assert Path(module.__file__).resolve() == ROOT / "scripts" / "bluesminds_client.py"
    assert BluesmindsClient.__name__ == "BluesmindsClient"


def test_manager_rewriter_bluesminds_result_metadata_has_no_prompt_or_secrets(monkeypatch) -> None:
    class FakeBluesmindsClient:
        def chat(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["messages"][0]["content"] == "SECRET SYSTEM PROMPT"
            assert kwargs["messages"][1]["content"] == "SECRET QUERY BODY"
            return {"choices": [{"message": {"content": "safe rewrite"}}]}

    class FakeModule:
        BluesmindsClient = FakeBluesmindsClient

    monkeypatch.setitem(sys.modules, "bluesminds_client", FakeModule())
    monkeypatch.setenv("NMBOT_V3_MANAGER_REWRITER_BLUESMINDS_ENABLED", "1")
    monkeypatch.setenv("NMBOT_V3_MANAGER_REWRITER_BLUESMINDS_MODEL", "safe-model")
    monkeypatch.setenv("BLUESMINDS_API_KEY", "secret-token")
    text, meta = asyncio.run(
        runtime_adapter_mod.bluesminds_manager_rewriter.try_rewrite(
            {"system_prompt": "SECRET SYSTEM PROMPT", "query": "SECRET QUERY BODY"}
        )
    )
    assert text == "safe rewrite"
    assert meta == {"ok": True, "_gateway_client_impl": "bluesminds_manager_rewriter", "_primary_provider": True, "model": "safe-model"}
    assert "SECRET SYSTEM PROMPT" not in str(meta)
    assert "SECRET QUERY BODY" not in str(meta)
    assert "secret-token" not in str(meta)


def test_manager_rewriter_structured_provider_meta_is_allowlisted() -> None:
    class Rewriter:
        def rewrite_manager_answer(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "text": "safe rewrite",
                "meta": {
                    "provider": "bluesminds",
                    "fallback": False,
                    "reason": "none",
                    "model": "secret-model",
                    "query": "secret prompt",
                    "external_api_key": "secret-token",
                },
            }

    result = asyncio.run(
        manager_rewriter_mod.rewrite_manager_answer_async(
            transcript=(),
            current_question="secret question",
            prepared_answer="secret prepared",
            brief=object(),
            rewriter=Rewriter(),
        )
    )
    meta = result.to_meta()

    assert result.text == "safe rewrite"
    assert meta["provider_meta"] == {"provider": "bluesminds", "fallback": False, "reason": "none"}
    dumped = json.dumps(meta, ensure_ascii=False)
    assert "secret" not in dumped
    assert "model" not in dumped


def test_manager_rewriter_trace_keeps_provider_marker_without_leaking_raw_fields() -> None:
    trace = _safe_response_composer_trace(
        {
            "used": True,
            "status": "primary",
            "attempts": 1,
            "provider_meta": {
                "provider": "gateway",
                "fallback": True,
                "reason": "disabled",
                "query": "secret prompt",
                "model": "secret-model",
            },
        }
    )

    assert trace["provider_meta"] == {"provider": "gateway", "fallback": True, "reason": "disabled"}
    dumped = json.dumps(trace, ensure_ascii=False)
    assert "secret" not in dumped
    assert "model" not in dumped


def _valid_search_output_for_request(request_data: dict[str, Any], *, facts: list[dict[str, Any]], near: list[dict[str, Any]]) -> dict[str, Any]:
    envelope, params = _request_parts(request_data)
    effective_hard = params.get("effective_hard") if isinstance(params.get("effective_hard"), dict) else {}
    diagnostics = {
        "mcp_tool": "novostroym/get_flat_info",
        "response_viewpoint": envelope.get("response_viewpoint"),
        "base_viewpoint": envelope.get("base_viewpoint"),
        "requested_field_priorities": list(envelope.get("available_fact_fields") or [])[:12],
        "relaxation_audit": list(params.get("relaxation_audit") or []),
        "ignored_preferences": [],
        "notes": [],
    }

    def card(item: dict[str, Any]) -> dict[str, Any]:
        out = dict(item)
        out.setdefault("min_price", 12_000_000)
        if effective_hard.get("district"):
            out.setdefault("district", effective_hard["district"])
        if effective_hard.get("rooms"):
            out.setdefault("rooms", effective_hard["rooms"])
        return out

    return {"facts": [card(item) for item in facts], "near": [card(item) for item in near], "missing": [], "params": dict(effective_hard), "diagnostics": diagnostics}


def _payload_count_and_excluded(request_data: dict[str, Any]) -> tuple[int, list[str]]:
    envelope, params = _request_parts(request_data)
    return int(envelope.get("count") or 0), list(params.get("excluded_names") or [])


def make_app(initial: dict[str, Any] | None = None, *, client: FakeClient | None = None) -> web.Application:
    app = web.Application()
    app["state_store"] = FakeStore(initial)
    app["overmind_client"] = client or FakeClient()
    app["crm_callback_outbox"] = mod.LocalCallbackOutbox(Path("/tmp/nmbot-runtime-adapter-test-outbox"))
    app["jivo_session_locks"] = mod.SessionLockRegistry()
    app["jivo_dedup_cache"] = mod.JivoDedupCache(ttl_sec=60, max_entries=32)
    return app


def _callback_records(outbox_root: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(outbox_root.glob("*.json"))]


def test_runtime_selector_defaults_to_v2_and_v0_is_explicit(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[str] = []

        async def fake_v2(app, *, user_id, message, channel, meta):
            calls.append("v2")
            return {"ok": True, "answer": "V2 ok", "meta": {"runtime": "v2"}}

        monkeypatch.setattr(runtime_adapter_mod, "_run_v2_authoritative", fake_v2)

        v2_app = make_app(client=V0GatewayClient())
        result_v2 = await run_runtime_turn(v2_app, user_id="u", message="Привет", channel="jivo")
        assert result_v2["answer"] == "V2 ok"
        assert calls == ["v2"]
        assert v2_app["overmind_client"].gateway_payloads == []

        v0_client = V0GatewayClient()
        v0_app = make_app(client=v0_client)
        v0_app["runtime_version_store"] = FakeRuntimeVersionStore("V0")
        result_v0 = await run_runtime_turn(v0_app, user_id="u", message="Подбери двушку", channel="jivo")
        assert result_v0["ok"] is True
        assert result_v0["meta"]["runtime"] == "v0"
        assert [payload["_payload_stage"] for payload in v0_client.gateway_payloads] == ["nmbot_v0_scenario_search"]
        assert v0_client.gateway_payloads[0]["system_prompt"] == (Path(__file__).resolve().parents[1] / "prompts" / "v0_scenario_search.txt").read_text(encoding="utf-8").strip()
        metadata_raw = str(v0_client.gateway_payloads[0]["query"]).split("V0_RUNTIME_METADATA=", 1)[1].split("\n", 1)[0]
        fields = json.loads(metadata_raw)["search_field_contract"]["fields"]
        assert "new_building_class" in fields
        assert "building_type" in fields
        assert "nmbot_v0" in v0_app["state_store"].states["u"]
        assert "nmbot_v2" not in v0_app["state_store"].states["u"]

    asyncio.run(scenario())


def test_v0_runtime_metadata_uses_ordered_presentation_contract_not_alphabetical_slice() -> None:
    metadata = runtime_adapter_mod._v0_runtime_metadata()
    fields = metadata["search_field_contract"]["fields"]

    assert fields == v0_presentation_search_fields()
    assert fields[:8] == ["name", "alias", "min_price", "max_price", "novos.min_price", "novos.max_price", "ads.fullprice", "ads.price"]
    assert {"ads", "house", "apartment_types"}.issubset(fields)
    assert "location" in fields
    assert "rooms" in fields
    assert "finishing" in fields
    assert "ready" in fields
    assert "new_building_class" in fields
    assert "building_type" in fields
    assert "property_metro" in fields
    assert "developer" in fields
    assert "school" in fields
    assert "kindergarten" in fields
    assert fields != sorted(fields)[: len(fields)]
    assert metadata["available_fact_fields_by_viewpoint"]["family"] == fields
    assert metadata["search_field_contract"]["limit"] == len(fields)


def test_v0_scenario_gateway_payload_keeps_previous_message_to_2000_and_redacts_contact() -> None:
    async def scenario() -> None:
        client = V0GatewayClient()
        ports = runtime_adapter_mod._V0GatewayPorts({"overmind_client": client})
        previous = "Начало +7 999 123-45-67 " + ("п" * 520) + " ФРАГМЕНТ_ПОСЛЕ_500 " + ("х" * 1800)

        await ports.scenario_search(
            {
                "user_text": "у" * 2100,
                "state": {"previous_assistant_message": previous, "client_id": "raw-client"},
            }
        )

        query = str(client.gateway_payloads[0]["query"])
        context_raw = query.split("V0_SCENARIO_SEARCH_CONTEXT=", 1)[1].split("\n", 1)[0]
        context = json.loads(context_raw)
        persisted = context["state"]["previous_assistant_message"]

        assert len(context["user_text"]) == 2000
        assert len(persisted) == 2000
        assert "ФРАГМЕНТ_ПОСЛЕ_500" in persisted
        assert "+7 999" not in persisted
        assert "9991234567" not in persisted
        assert "[redacted-contact]" in persisted
        assert "client_id" not in context["state"]

    asyncio.run(scenario())


def test_v0_pending_phone_meaningful_question_reaches_scenario_and_preserves_selected_family_context() -> None:
    async def scenario() -> None:
        initial = _canonical_v0_envelope(
            runtime_adapter_mod.V0State(
                visible_options=(OptionCard(name="Мичуринский парк", price_min=18_000_000),),
                selected_option_name="Мичуринский парк",
                active_topic="family",
                pending_action="contact_phone",
                pending_subject="Мичуринский парк",
                pending_topic="family",
            )
        )
        client = V0PendingPhoneQuestionClient()
        app = make_app(initial=initial, client=client)
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(
            app,
            user_id="u",
            message="Номер пока не хочу оставлять. Просто скажите: без оператора вы отделку проверить не можете?",
            channel="jivo",
            meta={"event_id": "pending-phone-question"},
        )

        assert result["ok"] is True
        assert [payload["_payload_stage"] for payload in client.gateway_payloads] == ["nmbot_v0_scenario_search"]
        state = app["state_store"].states["u"]["nmbot_v0"]
        assert state["selected_option_name"] == "Мичуринский парк"
        assert state["active_topic"] == "family"
        assert state["pending_action"] != "contact_phone"
        assert state["pending_topic"] == "family"

    asyncio.run(scenario())


def test_v0_pending_phone_positive_consent_asks_for_digits_without_scenario_or_callback() -> None:
    async def scenario() -> None:
        initial_state = runtime_adapter_mod.V0State(
            visible_options=(OptionCard(name="Мичуринский парк", price_min=18_000_000),),
            selected_option_name="Мичуринский парк",
            active_topic="family",
            previous_assistant_message="Оставите номер телефона, чтобы оператор проверил это?",
            pending_action="contact_phone",
            pending_subject="Мичуринский парк",
            pending_topic="family",
        )
        initial = _canonical_v0_envelope(initial_state)
        client = V0GatewayClient()
        app = make_app(initial=initial, client=client)
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(app, user_id="u", message="да", channel="jivo", meta={"event_id": "v0-phone-yes"})

        expected = runtime_adapter_mod.V0_CONTACT_PHONE_CONSENT_MESSAGE
        assert client.gateway_payloads == []
        assert result["intent"] == "collect_contact_phone"
        assert result["answer"] == expected
        assert result["awaiting_phone"] is True
        assert "crm_callback" not in result
        state = app["state_store"].states["u"]["nmbot_v0"]
        assert state["pending_action"] == "contact_phone"
        assert state["pending_subject"] == "Мичуринский парк"
        assert state["pending_topic"] == "family"
        assert state["selected_option_name"] == "Мичуринский парк"
        assert state["active_topic"] == "family"
        assert [card["name"] for card in state["visible_options"]] == ["Мичуринский парк"]
        assert state["previous_assistant_message"] == expected

    asyncio.run(scenario())


def test_v0_pending_phone_repeated_positive_consent_keeps_asking_for_actual_digits() -> None:
    async def scenario() -> None:
        initial = _canonical_v0_envelope(
            runtime_adapter_mod.V0State(
                visible_options=(OptionCard(name="Мичуринский парк", price_min=18_000_000),),
                selected_option_name="Мичуринский парк",
                active_topic="family",
                previous_assistant_message=runtime_adapter_mod.V0_CONTACT_PHONE_CONSENT_MESSAGE,
                pending_action="contact_phone",
                pending_subject="Мичуринский парк",
                pending_topic="family",
            )
        )
        client = V0GatewayClient()
        app = make_app(initial=initial, client=client)
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(app, user_id="u", message="давайте", channel="jivo", meta={"event_id": "v0-phone-yes-again"})

        assert client.gateway_payloads == []
        assert result["answer"] == runtime_adapter_mod.V0_CONTACT_PHONE_CONSENT_MESSAGE
        assert "оставите номер" not in result["answer"].lower()
        assert result["awaiting_phone"] is True
        state = app["state_store"].states["u"]["nmbot_v0"]
        assert state["pending_action"] == "contact_phone"
        assert state["previous_assistant_message"] == runtime_adapter_mod.V0_CONTACT_PHONE_CONSENT_MESSAGE

    asyncio.run(scenario())


def test_v0_pending_phone_non_exact_semantic_accept_asks_for_digits_without_answer_writer(monkeypatch) -> None:
    async def scenario() -> None:
        initial = _canonical_v0_envelope(
            runtime_adapter_mod.V0State(
                visible_options=(OptionCard(name="Мичуринский парк", price_min=18_000_000),),
                selected_option_name="Мичуринский парк",
                active_topic="family",
                previous_assistant_message="Оставите номер телефона, чтобы оператор проверил это?",
                pending_action="contact_phone",
                pending_subject="Мичуринский парк",
                pending_topic="family",
            )
        )
        client = V0PendingPhoneSemanticAcceptClient()
        app = make_app(initial=initial, client=client)
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")
        monkeypatch.setenv("NMBOT_V0_ANSWER_WRITER_MODE", "shadow")

        async def forbidden_writer(_assignment: dict[str, Any]):
            raise AssertionError("operator phone request must not call V0 answer writer")

        monkeypatch.setattr(runtime_adapter_mod.bluesminds_v0_answer_writer, "try_write", forbidden_writer)

        result = await run_runtime_turn(app, user_id="u", message="да, всё верно", channel="jivo", meta={"event_id": "v0-phone-semantic-yes"})

        expected = runtime_adapter_mod.V0_CONTACT_PHONE_CONSENT_MESSAGE
        assert [payload["_payload_stage"] for payload in client.gateway_payloads] == ["nmbot_v0_scenario_search"]
        assert result["ok"] is True
        assert result["intent"] == "collect_contact_phone"
        assert result["awaiting_phone"] is True
        assert result["handoff_to_operator"] is False
        assert result["answer"] == expected
        assert result["answer"] == runtime_adapter_mod.V0_CONTACT_PHONE_DIGITS_REQUEST
        assert "оставите номер" not in result["answer"].lower()
        trace = result["meta"]["trace"]
        assert trace["call_counts"] == {"scenario_search": 1, "answer": 0}
        assert trace["decision_action"] == "operator"
        assert trace["answer_writer"]["used"] is False
        assert trace["answer_writer"]["reason"] in {"operator_action", "operator_phone_scope"}
        state = app["state_store"].states["u"]["nmbot_v0"]
        assert state["pending_action"] == "contact_phone"
        assert state["pending_subject"] == "Мичуринский парк"
        assert state["pending_topic"] == "family"
        assert state["selected_option_name"] == "Мичуринский парк"
        assert state["active_topic"] == "family"
        assert [card["name"] for card in state["visible_options"]] == ["Мичуринский парк"]
        assert state["previous_assistant_message"] == expected

    asyncio.run(scenario())


def test_v0_pending_phone_off_topic_question_reaches_scenario_and_retains_context() -> None:
    async def scenario() -> None:
        initial = _canonical_v0_envelope(
            runtime_adapter_mod.V0State(
                visible_options=(OptionCard(name="Мичуринский парк", price_min=18_000_000),),
                selected_option_name="Мичуринский парк",
                active_topic="family",
                previous_assistant_message="Оставите номер телефона, чтобы оператор проверил это?",
                pending_action="contact_phone",
                pending_subject="Мичуринский парк",
                pending_topic="family",
            )
        )
        client = V0PendingPhoneOffTopicClient()
        app = make_app(initial=initial, client=client)
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(app, user_id="u", message="а как сварить пельмени?", channel="jivo", meta={"event_id": "pending-phone-offtopic"})

        assert result["ok"] is True
        assert [payload["_payload_stage"] for payload in client.gateway_payloads] == ["nmbot_v0_scenario_search"]
        assert result["intent"] == "off_topic"
        state = app["state_store"].states["u"]["nmbot_v0"]
        assert state["selected_option_name"] == "Мичуринский парк"
        assert state["active_topic"] == "family"
        assert [card["name"] for card in state["visible_options"]] == ["Мичуринский парк"]

    asyncio.run(scenario())


def test_v0_pending_phone_decline_operator_decision_clears_phone_without_handoff() -> None:
    async def scenario() -> None:
        initial = _canonical_v0_envelope(
            runtime_adapter_mod.V0State(
                visible_options=(OptionCard(name="Мичуринский парк", price_min=18_000_000),),
                selected_option_name="Мичуринский парк",
                active_topic="family",
                previous_assistant_message="Оставите номер телефона, чтобы оператор проверил это?",
                pending_action="contact_phone",
                pending_subject="Мичуринский парк",
                pending_topic="family",
            )
        )
        client = V0PendingPhoneDeclineOperatorClient()
        app = make_app(initial=initial, client=client)
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(
            app,
            user_id="u",
            message="нет, телефон не оставлю",
            channel="jivo",
            meta={"event_id": "pending-phone-decline"},
        )

        assert result["ok"] is True
        assert result["meta"]["runtime"] == "v0"
        assert [payload["_payload_stage"] for payload in client.gateway_payloads] == ["nmbot_v0_scenario_search"]
        assert not re.search(r"телефон|номер|оператор|менеджер", result["answer"], re.IGNORECASE)
        state = app["state_store"].states["u"]["nmbot_v0"]
        assert state["selected_option_name"] == "Мичуринский парк"
        assert state["active_topic"] == "family"
        assert [card["name"] for card in state["visible_options"]] == ["Мичуринский парк"]
        assert [card["price_min"] for card in state["visible_options"]] == [18_000_000]
        assert state["pending_action"] is None
        assert state["pending_subject"] is None
        assert state["pending_topic"] is None

    asyncio.run(scenario())


def test_session_runtime_override_wins_over_global_selector(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[str] = []

        async def fake_v0(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("v0")
            return {"ok": True, "answer": "V0", "meta": {"runtime": "v0"}}

        async def fake_v2(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("v2")
            return {"ok": True, "answer": "V2", "meta": {"runtime": "v2"}}

        monkeypatch.setattr(runtime_adapter_mod, "_run_v0_authoritative", fake_v0)
        monkeypatch.setattr(runtime_adapter_mod, "_run_v2_authoritative", fake_v2)
        app = make_app()
        app["runtime_version_store"] = FakeRuntimeVersionStore("V2")
        app["state_store"].states["u"] = {"runtime_version_override": "V0"}

        first = await run_runtime_turn(app, user_id="u", message="тест", channel="jivo")
        assert first["meta"]["runtime"] == "v0"
        app["state_store"].states["u"]["runtime_version_override"] = "V2"
        second = await run_runtime_turn(app, user_id="u", message="тест", channel="jivo")
        assert second["meta"]["runtime"] == "v2"
        assert calls == ["v0", "v2"]

    asyncio.run(scenario())


def test_v3_session_runtime_reuses_v2_engine_and_decorates_metadata(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[str] = []

        async def fake_v2(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("v2")
            return {"ok": True, "answer": "V2 semantic answer", "meta": {"runtime": "v2", "trace": {"stage": "fake"}}}

        monkeypatch.setattr(runtime_adapter_mod, "_run_v2_authoritative", fake_v2)
        app = make_app()
        app["state_store"].states["u"] = {"runtime_version_override": "V3", "nmbot_v2": {"params": {"rooms": 2}}}

        result = await run_runtime_turn(app, user_id="u", message="тест", channel="jivo")

        assert calls == ["v2"]
        assert result["answer"] == "V2 semantic answer"
        assert result["meta"]["runtime"] == "v3"
        assert result["meta"]["engine"] == "v2"
        assert result["meta"]["trace"] == {"stage": "fake"}

    asyncio.run(scenario())


def test_global_v2_v0_v2_switch_preserves_independent_namespaces(monkeypatch) -> None:
    async def scenario() -> None:
        patch_planner(monkeypatch, {"operation": "current_options", "intent": "life", "scope": "all", "confidence": 1.0})
        initial = {
            "nmbot_v2": {"params": {"rooms": 2}, "visible_options": [{"name": "Лучи", "price": "от 12 млн рублей"}]},
            "nmbot_v0": {"params": {"rooms": 1}, "visible_options": [{"name": "V0 Старый"}]},
        }
        app = make_app(initial=initial, client=V0GatewayClient())
        app["runtime_version_store"] = FakeRuntimeVersionStore("V2")

        result_v2_first = await run_runtime_turn(app, user_id="u", message="Что есть сейчас?", channel="jivo")
        assert result_v2_first["ok"] is True
        assert app["state_store"].states["u"]["nmbot_v0"]["params"] == {"rooms": 1}
        assert app["state_store"].states["u"]["nmbot_v2"]["params"] == {"rooms": 2}

        await app["runtime_version_store"].set("V0")
        result_v0 = await run_runtime_turn(app, user_id="u", message="Подбери двушку", channel="jivo")
        assert result_v0["ok"] is True
        assert app["state_store"].states["u"]["nmbot_v2"]["params"] == {"rooms": 2}
        assert app["state_store"].states["u"]["nmbot_v0"]["params"] == {"rooms": 2}

        await app["runtime_version_store"].set("V2")
        result_v2_second = await run_runtime_turn(app, user_id="u", message="А сейчас?", channel="jivo")
        assert result_v2_second["ok"] is True
        assert app["state_store"].states["u"]["nmbot_v2"]["params"] == {"rooms": 2}
        assert app["state_store"].states["u"]["nmbot_v0"]["params"] == {"rooms": 2}

    asyncio.run(scenario())


def test_v0_malformed_gateway_output_falls_back_without_state_mutation() -> None:
    async def scenario() -> None:
        initial = _canonical_v0_envelope()
        initial["nmbot_v0"]["params"] = {"rooms": 2}
        client = V0GatewayClient(malformed_scenario=True)
        app = make_app(initial=initial, client=client)
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(app, user_id="u", message="Привет", channel="jivo")

        assert result["ok"] is False
        assert result["error_type"] == "malformed_scenario_output"
        assert "попробуйте" in result["answer"].lower()
        assert not re.search(r"телефон|оператор|менеджер", result["answer"], re.IGNORECASE)
        assert result["awaiting_phone"] is False
        assert result["handoff_to_operator"] is False
        assert result["meta"]["trace"]["validation_errors"] == ["invalid_strict_json"]
        assert result["meta"]["trace"]["error_code"] == "malformed_scenario_output"
        dumped = json.dumps(result["meta"]["trace"], ensure_ascii=False)
        assert "not-json" not in dumped
        assert "Привет" not in dumped
        assert app["state_store"].saved == []
        assert app["state_store"].states["u"] == initial
        assert [payload["_payload_stage"] for payload in client.gateway_payloads] == ["nmbot_v0_scenario_search", "nmbot_v0_scenario_search"]
        contexts = [json.loads(str(payload["query"]).split("V0_SCENARIO_SEARCH_CONTEXT=", 1)[1].split("\n", 1)[0]) for payload in client.gateway_payloads]
        assert "format_recovery" not in contexts[0]
        assert contexts[1]["format_recovery"] == {"strict_json_only": True, "reason": "previous_output_invalid_strict_json"}
        assert "not-json" not in json.dumps(contexts[1], ensure_ascii=False)

    asyncio.run(scenario())


def test_v0_success_trace_exposes_only_field_names_not_values() -> None:
    async def scenario() -> None:
        client = V0GatewayClient()
        app = make_app(client=client)
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(app, user_id="u", message="нужнва кавртира для семьи пв 10 млн", channel="jivo")

        assert result["ok"] is True
        trace = result["meta"]["trace"]["field_trace"]["cards"][0]
        assert set(trace["raw_fields"]) == {"name", "location", "min_price"}
        assert set(trace["normalized_fields"]) == {"name", "location", "price_min"}
        assert result["meta"]["trace"]["runtime_summary"]["field_trace"] == result["meta"]["trace"]["field_trace"]
        dumped = json.dumps(result["meta"]["trace"], ensure_ascii=False)
        for forbidden in ["ЖК Первый", "Москва", "10000000", "нужнва"]:
            assert forbidden not in dumped

    asyncio.run(scenario())


def test_v0_search_runtime_summary_has_truthful_counts_state_and_questions() -> None:
    async def scenario() -> None:
        client = V0GatewayClient()
        app = make_app(client=client)
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(app, user_id="u", message="Подбери двушку", channel="jivo")

        summary = result["meta"]["trace"]["runtime_summary"]
        assert summary["stage"] == "v0_turn"
        assert summary["action"] == "search"
        assert summary["answer_kind"] == "search_many"
        assert summary["call_counts"] == {"planner": 1, "search": 1, "selected_enrichment": 0, "gateway_attempts": 1, "scenario_search": 1, "answer": 0}
        assert summary["state_before"] == {"param_keys": [], "visible_options_count": 0, "selected_present": False, "pending_followup": None, "active_topic": None}
        assert summary["state_after"] == {"param_keys": ["rooms"], "visible_options_count": 3, "selected_present": False, "pending_followup": None, "active_topic": "life"}
        assert summary["question_count"] == 1
        assert summary["final_question_at_end"] is True
        assert summary["quality_blockers"] == []
        assert summary["grounding_scope"] == "canonical_response_plan"

    asyncio.run(scenario())


def test_v0_safe_call_counts_search_reflects_scenario_attempts() -> None:
    assert runtime_adapter_mod._safe_v0_call_counts({"scenario_search": 1, "answer": 0}, decision_action="search")["search"] == 1
    assert runtime_adapter_mod._safe_v0_call_counts({"scenario_search": 2, "answer": 0}, decision_action="search")["search"] == 2
    assert runtime_adapter_mod._safe_v0_call_counts({"scenario_search": 2, "answer": 0}, decision_action="selected_object")["search"] == 0


def test_v0_recovered_search_runtime_summary_counts_two_attempts_and_uses_recovered_field_trace() -> None:
    async def scenario() -> None:
        client = V0EmptyThenRecoveredGatewayClient()
        app = make_app(client=client)
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(app, user_id="u", message="Подбери до 12 млн", channel="jivo")

        assert result["ok"] is True
        trace = result["meta"]["trace"]
        summary = trace["runtime_summary"]
        assert trace["call_counts"] == {"scenario_search": 2, "answer": 0}
        assert summary["call_counts"] == {"planner": 2, "search": 2, "selected_enrichment": 0, "gateway_attempts": 2, "scenario_search": 2, "answer": 0}
        assert [payload["_payload_stage"] for payload in client.gateway_payloads] == ["nmbot_v0_scenario_search", "nmbot_v0_scenario_search"]
        field_trace = trace["field_trace"]
        assert summary["field_trace"] == field_trace
        assert field_trace["cards"]
        assert set(field_trace["cards"][0]["raw_fields"]) == {"location", "min_price", "name"}
        assert "initial_field_trace" not in trace

    asyncio.run(scenario())


def test_v0_selected_turn_runtime_summary_preserves_selected_and_pending_state() -> None:
    class SelectedV0Client(V0GatewayClient):
        async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            self.gateway_payloads.append(request_data)
            stage = request_data.get("_payload_stage")
            if stage == "nmbot_v0_scenario_search":
                query = str(request_data.get("query") or "")
                if '"visible_options"' in query and "перв" in query.casefold():
                    return json.dumps({"decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Первый", "active_topic": "life"}, "search": {}}, ensure_ascii=False), {"ok": True}
                return await super()._run_gateway_request(request_data, headers, timeout)
            if stage == "nmbot_v0_answer":
                query = str(request_data.get("query") or "")
                brief = json.loads(query.split("V0_ANSWER_BRIEF=", 1)[1].split("\n", 1)[0])
                return json.dumps({"answer_kind": brief["decision"]["expected_answer_kind"], "scope": brief["decision"]["expected_scope"], "intro": "runtime replaces", "options": [{"name": item["name"]} for item in brief["allowed_cards"]], "recommendation": "", "missing_note": "", "final_question": brief["decision"]["cta_template"]}, ensure_ascii=False), {"ok": True}
            raise AssertionError(stage)

    async def scenario() -> None:
        client = SelectedV0Client()
        app = make_app(client=client)
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")
        first = await run_runtime_turn(app, user_id="u", message="Подбери двушку", channel="jivo")
        assert first["ok"] is True

        selected = await run_runtime_turn(app, user_id="u", message="первый", channel="jivo")

        summary = selected["meta"]["trace"]["runtime_summary"]
        assert summary["action"] == "selected_object"
        assert summary["call_counts"] == {"planner": 1, "search": 0, "selected_enrichment": 0, "gateway_attempts": 1, "scenario_search": 1, "answer": 0}
        assert summary["state_before"]["visible_options_count"] == 3
        assert summary["state_before"]["selected_present"] is False
        assert summary["state_before"]["active_topic"] == "life"
        assert summary["state_after"]["selected_present"] is True
        assert summary["state_after"]["pending_followup"] == "check_selected_availability"
        assert summary["state_after"]["active_topic"] == "life"
        assert summary["question_count"] == 1
        assert summary["final_question_at_end"] is True
        assert summary["quality_blockers"] == []

    asyncio.run(scenario())


def test_v0_error_runtime_summary_marks_runtime_error() -> None:
    async def scenario() -> None:
        initial = _canonical_v0_envelope()
        client = V0GatewayClient(malformed_scenario=True)
        app = make_app(initial=initial, client=client)
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(app, user_id="u", message="Привет", channel="jivo")

        summary = result["meta"]["trace"]["runtime_summary"]
        assert result["ok"] is False
        assert summary["quality_blockers"] == ["runtime_error"]
        assert summary["call_counts"] == {"planner": 2, "search": 0, "selected_enrichment": 0, "gateway_attempts": 2, "scenario_search": 2, "answer": 0}
        assert summary["state_before"] == summary["state_after"]

    asyncio.run(scenario())


def test_v0_answer_writer_off_makes_no_provider_call_and_keeps_state(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[dict[str, Any]] = []

        async def fake_writer(assignment: dict[str, Any]):
            calls.append(assignment)
            return "Новый текст", {"ok": True, "model": "fake"}

        monkeypatch.delenv("NMBOT_V0_ANSWER_WRITER_MODE", raising=False)
        monkeypatch.delenv("NMBOT_V0_ANSWER_WRITER_PROVIDER", raising=False)
        monkeypatch.setattr(runtime_adapter_mod.bluesminds_v0_answer_writer, "try_write", fake_writer)
        app = make_app(client=V0GatewayClient())
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(app, user_id="u", message="Подбери двушку", channel="jivo")

        assert calls == []
        assert result["answer"] == app["state_store"].states["u"]["nmbot_v0"]["previous_assistant_message"]
        assert result["meta"]["trace"]["answer_writer"]["mode"] == "off"
        assert result["meta"]["trace"]["answer_writer"]["provider"] == "bluesminds"
        assert result["meta"]["trace"]["answer_writer"]["used"] is False

    asyncio.run(scenario())


def test_v0_answer_writer_shadow_keeps_public_answer_and_stores_safe_candidate_metadata(monkeypatch) -> None:
    async def scenario() -> None:
        assignments: list[dict[str, Any]] = []

        async def fake_writer(assignment: dict[str, Any]):
            assert set(assignment) == {"client_message", "previous_assistant_message", "response_job", "material"}
            assignments.append(assignment)
            material = assignment["material"]
            answer = "Поняла, покажу коротко и без лишнего: " + " ".join(line for line in material["card_lines"] if line) + " " + material["final_question"]
            return answer, {"ok": True, "model": "fake"}

        monkeypatch.setenv("NMBOT_V0_ANSWER_WRITER_MODE", "shadow")
        monkeypatch.setattr(runtime_adapter_mod.bluesminds_v0_answer_writer, "try_write", fake_writer)
        app = make_app(client=V0GatewayClient())
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(app, user_id="u", message="Подбери двушку", channel="jivo")

        trace = result["meta"]["trace"]["answer_writer"]
        assert trace["mode"] == "shadow"
        assert trace["used"] is True
        assert trace["published"] is False
        assert trace["status"] == "valid"
        material = assignments[0]["material"]
        expected_candidate = "Поняла, покажу коротко и без лишнего: " + " ".join(line for line in material["card_lines"] if line) + " " + material["final_question"]
        assert "candidate" not in trace
        assert trace["candidate_chars"] == len(expected_candidate)
        assert trace["candidate_sha256"] == hashlib.sha256(expected_candidate.encode("utf-8")).hexdigest()
        assert re.fullmatch(r"[0-9a-f]{64}", trace["candidate_sha256"])
        provenance = trace["prompt_provenance"]
        assert provenance["schema"] == "nmbot.prompt_provenance.v1"
        assert provenance["coverage"] == "complete"
        assert provenance["prompt_set_id"].startswith("ps_")
        assert provenance["prompts"][0]["stage"] == "v0.answer_writer"
        assert provenance["prompts"][0]["source"] == "prompts/v0_answer_writer.txt"
        assert provenance["prompts"][0]["usage"] == "invoked"
        dumped_provenance = json.dumps(provenance, ensure_ascii=False)
        assert "client_message" not in dumped_provenance
        assert "Подбери" not in dumped_provenance
        assert "presentation-only" not in dumped_provenance
        assert result["answer"] == app["state_store"].states["u"]["nmbot_v0"]["previous_assistant_message"]
        assert "Поняла, покажу коротко" not in result["answer"]
        dumped_trace = json.dumps(trace, ensure_ascii=False)
        assert "Поняла, покажу коротко" not in dumped_trace
        assert "card_lines" not in dumped_trace
        assert "Подбери" not in dumped_trace

    asyncio.run(scenario())


def test_v0_answer_writer_publish_valid_candidate_is_public_and_persisted(monkeypatch) -> None:
    async def scenario() -> None:
        assignments: list[dict[str, Any]] = []

        async def fake_writer(assignment: dict[str, Any]):
            assignments.append(assignment)
            material = assignment["material"]
            answer = "Поняла, собрала варианты в спокойном формате: " + " ".join(line for line in material["card_lines"] if line) + " " + material["final_question"]
            return answer, {"ok": True, "model": "fake"}

        monkeypatch.setenv("NMBOT_V0_ANSWER_WRITER_MODE", "publish")
        monkeypatch.setattr(runtime_adapter_mod.bluesminds_v0_answer_writer, "try_write", fake_writer)
        app = make_app(client=V0GatewayClient())
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(app, user_id="u", message="Подбери двушку", channel="jivo")

        assert result["answer"].startswith("Поняла, собрала варианты")
        material = assignments[0]["material"]
        retained = [line for line in material["card_lines"] if line]
        for line in retained:
            assert result["answer"].count(line) == 1
        assert all(result["answer"].index(left) < result["answer"].index(right) for left, right in zip(retained, retained[1:]))
        assert result["answer"].endswith(material["final_question"])
        assert result["answer"] == app["state_store"].states["u"]["nmbot_v0"]["previous_assistant_message"]
        assert result["meta"]["trace"]["answer_writer"]["published"] is True
        trace = result["meta"]["trace"]["answer_writer"]
        assert "candidate" not in trace
        assert trace["candidate_chars"] == len(result["answer"])
        assert trace["candidate_sha256"] == hashlib.sha256(result["answer"].encode("utf-8")).hexdigest()

    asyncio.run(scenario())


def test_v0_answer_writer_assignment_response_job_safe_semantic_fields_and_publish_persistence(monkeypatch) -> None:
    async def scenario() -> None:
        assignments: list[dict[str, Any]] = []

        async def fake_writer(assignment: dict[str, Any]):
            assignments.append(assignment)
            return "Живой финальный ответ.", {"ok": True, "model": "fake"}

        monkeypatch.setenv("NMBOT_V0_ANSWER_WRITER_PROVIDER", "bluesminds")
        monkeypatch.setattr(runtime_adapter_mod.bluesminds_v0_answer_writer, "try_write", fake_writer)
        fixed_answer = V0Answer(
            answer_kind="selected_object_card",
            scope="selected_object",
            intro="Уточню по выбранному ЖК.",
            options=({"lines": ("ЖК Первый: Москва, цены от 10 млн рублей.",)},),
            recommendation="Можно отдельно проверить плотность и окружение.",
            missing_note="По высотности сейчас нет данных.",
            final_question="Проверить окружение и двор?",
        )
        result = V0TurnResult(
            ok=True,
            state=V0State(selected_option_name="ЖК из state", previous_assistant_message="старый ответ"),
            answer=fixed_answer,
            message=fixed_answer.text(),
        )
        decision = {
            "action": "selected_object",
            "viewpoint": "life",
            "client_question": "Они не слишком похожи на человейники?" + "x" * 600,
            "response_policy": "answer_directly" + "y" * 200,
            "requested_facts": ["density", "courtyard"] + ["extra"] * 30,
            "selected_option_name": "ЖК Первый",
            "raw_payload": {"trace": "must_not_leak"},
        }
        trace: dict[str, Any] = {}

        answer, published_state = await runtime_adapter_mod._maybe_apply_v0_answer_writer(
            mode="publish",
            text="Не знаю, если честно. Они не слишком похожи на человейники?",
            previous_assistant_message="Вот варианты из прошлого ответа.",
            result=result,
            answer=fixed_answer.text(),
            decision=decision,
            decision_action="selected_object",
            trace=trace,
        )

        assert answer == "Живой финальный ответ."
        assert published_state.previous_assistant_message == "Живой финальный ответ."
        assignment = assignments[0]
        assert set(assignment) == {"client_message", "previous_assistant_message", "response_job", "material"}
        assert "deterministic_draft" not in assignment["material"]
        response_job = assignment["response_job"]
        assert response_job["answer_kind"] == "selected_object_card"
        assert response_job["scope"] == "selected_object"
        assert response_job["decision_action"] == "selected_object"
        assert response_job["viewpoint"] == "life"
        assert response_job["is_continuation"] is True
        assert response_job["client_question"] == decision["client_question"][:500]
        assert response_job["response_policy"] == decision["response_policy"][:120]
        assert response_job["requested_facts"] == ["density", "courtyard"] + ["extra"] * 18
        assert response_job["selected_option_name"] == "ЖК Первый"
        assert response_job["allowed_next_action"] == "Проверить окружение и двор?"
        assert "raw_payload" not in response_job
        assert "trace" not in json.dumps(assignment, ensure_ascii=False)
        assert trace["answer_writer"]["published"] is True

    asyncio.run(scenario())


def test_v0_answer_writer_explicit_gateway_provider_is_used_and_traced(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[dict[str, Any]] = []

        async def bluesminds_writer(_assignment: dict[str, Any]):
            raise AssertionError("default provider must not be used when gateway is explicit")

        async def gateway_writer(assignment: dict[str, Any]):
            calls.append(assignment)
            material = assignment["material"]
            answer = "Поняла, собрала варианты в спокойном формате: " + " ".join(line for line in material["card_lines"] if line) + " " + material["final_question"]
            return answer, {"ok": True, "provider": "gateway", "model": "google/gemini-2.5-flash"}

        monkeypatch.setenv("NMBOT_V0_ANSWER_WRITER_MODE", "publish")
        monkeypatch.setenv("NMBOT_V0_ANSWER_WRITER_PROVIDER", "gateway")
        monkeypatch.setattr(runtime_adapter_mod.bluesminds_v0_answer_writer, "try_write", bluesminds_writer)
        monkeypatch.setattr(runtime_adapter_mod.gateway_v0_answer_writer, "try_write", gateway_writer)
        app = make_app(client=V0GatewayClient())
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")

        result = await run_runtime_turn(app, user_id="u", message="Подбери двушку", channel="jivo")

        trace = result["meta"]["trace"]["answer_writer"]
        assert calls
        assert trace["provider"] == "gateway"
        assert trace["model"] == "google/gemini-2.5-flash"
        assert trace["published"] is True
        assert result["answer"] == app["state_store"].states["u"]["nmbot_v0"]["previous_assistant_message"]

    asyncio.run(scenario())


def test_v0_answer_writer_provider_empty_or_too_long_falls_back_to_deterministic(monkeypatch) -> None:
    async def scenario() -> None:
        async def provider_error(_assignment: dict[str, Any]):
            return "", {"ok": False, "error_code": "fake_timeout"}

        monkeypatch.setenv("NMBOT_V0_ANSWER_WRITER_MODE", "publish")
        monkeypatch.setattr(runtime_adapter_mod.bluesminds_v0_answer_writer, "try_write", provider_error)
        app = make_app(client=V0GatewayClient())
        app["runtime_version_store"] = FakeRuntimeVersionStore("V0")
        first = await run_runtime_turn(app, user_id="u", message="Подбери двушку", channel="jivo")
        assert first["answer"] == app["state_store"].states["u"]["nmbot_v0"]["previous_assistant_message"]
        assert first["meta"]["trace"]["answer_writer"]["reason"] == "provider_not_ok"

        async def empty(_assignment: dict[str, Any]):
            return "   \n ", {"ok": True}

        monkeypatch.setattr(runtime_adapter_mod.bluesminds_v0_answer_writer, "try_write", empty)
        second = await run_runtime_turn(app, user_id="u2", message="Подбери двушку", channel="jivo")
        assert second["answer"] == app["state_store"].states["u2"]["nmbot_v0"]["previous_assistant_message"]
        assert second["meta"]["trace"]["answer_writer"]["reason"] == "validation_failed"
        assert "empty_candidate" in second["meta"]["trace"]["answer_writer"]["validation_errors"]

        async def too_long(_assignment: dict[str, Any]):
            return "а" * 1801, {"ok": True}

        monkeypatch.setattr(runtime_adapter_mod.bluesminds_v0_answer_writer, "try_write", too_long)
        third = await run_runtime_turn(app, user_id="u3", message="Подбери двушку", channel="jivo")
        assert third["answer"] == app["state_store"].states["u3"]["nmbot_v0"]["previous_assistant_message"]
        assert third["meta"]["trace"]["answer_writer"]["reason"] == "validation_failed"
        assert "candidate_too_long" in third["meta"]["trace"]["answer_writer"]["validation_errors"]

    asyncio.run(scenario())


def test_v0_answer_writer_filters_one_card_material_and_rejects_removed_card_candidate(monkeypatch) -> None:
    async def scenario() -> None:
        assignments: list[dict[str, Any]] = []

        async def fake_writer(assignment: dict[str, Any]):
            assignments.append(assignment)
            return "ЖК Первый подходит. ЖК Второй тоже можно посмотреть. Проверить актуальные квартиры в этом ЖК?", {"ok": True, "model": "fake"}

        monkeypatch.setenv("NMBOT_V0_ANSWER_WRITER_PROVIDER", "bluesminds")
        monkeypatch.setattr(runtime_adapter_mod.bluesminds_v0_answer_writer, "try_write", fake_writer)
        malformed_answer = V0Answer(
            answer_kind="selected_object",
            scope="one_card",
            intro="По выбранному варианту.",
            options=(
                {"lines": ("ЖК Первый — корпус 1, от 10 млн рублей.",)},
                {"lines": ("ЖК Второй — старая карточка, от 14 млн рублей.",)},
            ),
            final_question="Проверить актуальные квартиры в этом ЖК?",
        )
        safe_deterministic = "ЖК Первый — по выбранному варианту. Проверить актуальные квартиры в этом ЖК?"
        result = V0TurnResult(
            ok=True,
            state=V0State(selected_option_name="ЖК Первый"),
            answer=malformed_answer,
            message=safe_deterministic,
        )
        trace: dict[str, Any] = {}

        answer, published_state = await runtime_adapter_mod._maybe_apply_v0_answer_writer(
            mode="publish",
            text="Расскажите про первый вариант",
            previous_assistant_message="Вот варианты.",
            result=result,
            answer=safe_deterministic,
            decision={"action": "selected_object", "selected_option_name": "ЖК Первый", "viewpoint": "life"},
            decision_action="selected_object",
            trace=trace,
        )

        assert assignments[0]["material"]["card_lines"] == ["ЖК Первый — корпус 1, от 10 млн рублей."]
        assert answer == safe_deterministic
        assert published_state == result.state
        assert trace["answer_writer"]["status"] == "fallback"
        assert trace["answer_writer"]["reason"] == "validation_failed"
        assert "candidate_mentions_disallowed_card" in trace["answer_writer"]["validation_errors"]

    asyncio.run(scenario())


def test_v0_answer_writer_material_normalization_error_skips_provider_and_candidate(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[dict[str, Any]] = []

        async def forbidden_writer(assignment: dict[str, Any]):
            calls.append(assignment)
            return "Живой финальный ответ.", {"ok": True, "model": "fake"}

        monkeypatch.setenv("NMBOT_V0_ANSWER_WRITER_PROVIDER", "bluesminds")
        monkeypatch.setattr(runtime_adapter_mod.bluesminds_v0_answer_writer, "try_write", forbidden_writer)
        malformed_answer = V0Answer(
            answer_kind="selected_object",
            scope="one_card",
            intro="По выбранному варианту.",
            options=(
                {"lines": ("ЖК Первый — корпус 1, от 10 млн рублей.",)},
                {"lines": ("ЖК Второй — старая карточка, от 14 млн рублей.",)},
            ),
            final_question="Проверить актуальные квартиры в этом ЖК?",
        )
        safe_deterministic = "ЖК Первый — по выбранному варианту. Проверить актуальные квартиры в этом ЖК?"
        result = V0TurnResult(
            ok=True,
            state=V0State(selected_option_name="ЖК Первый"),
            answer=malformed_answer,
            message=safe_deterministic,
        )
        trace: dict[str, Any] = {}

        answer, published_state = await runtime_adapter_mod._maybe_apply_v0_answer_writer(
            mode="publish",
            text="Расскажите про первый вариант",
            previous_assistant_message="Вот варианты.",
            result=result,
            answer=safe_deterministic,
            decision={"action": "selected_object", "selected_option_name": "ЖК Третий", "viewpoint": "life"},
            decision_action="selected_object",
            trace=trace,
        )

        assert calls == []
        assert answer == safe_deterministic
        assert published_state == result.state
        writer_trace = trace["answer_writer"]
        assert writer_trace["used"] is False
        assert writer_trace["published"] is False
        assert writer_trace["status"] == "fallback"
        assert writer_trace["reason"] == "material_normalization_failed"
        assert writer_trace["material_normalization_errors"] == ["one_card_selection_failed_closed"]
        assert "candidate_sha256" not in writer_trace

    asyncio.run(scenario())


def test_v0_answer_writer_operator_and_runtime_error_are_ineligible(monkeypatch) -> None:
    class OperatorPhoneClient(V0GatewayClient):
        async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            self.gateway_payloads.append(request_data)
            stage = request_data.get("_payload_stage")
            if stage == "nmbot_v0_scenario_search":
                return json.dumps({"decision": {"action": "operator", "viewpoint": "life", "response_policy": "operator_phone_request"}, "search": {}}, ensure_ascii=False), {"ok": True}
            if stage == "nmbot_v0_answer":
                query = str(request_data.get("query") or "")
                brief = json.loads(query.split("V0_ANSWER_BRIEF=", 1)[1].split("\n", 1)[0])
                return json.dumps({"answer_kind": brief["decision"]["expected_answer_kind"], "scope": brief["decision"]["expected_scope"], "intro": "runtime replaces", "options": [], "recommendation": "", "missing_note": "", "final_question": brief["decision"]["cta_template"]}, ensure_ascii=False), {"ok": True}
            raise AssertionError(stage)

    async def scenario() -> None:
        calls: list[dict[str, Any]] = []

        async def fake_writer(assignment: dict[str, Any]):
            calls.append(assignment)
            return "{}", {"ok": True}

        monkeypatch.setenv("NMBOT_V0_ANSWER_WRITER_MODE", "publish")
        monkeypatch.setattr(runtime_adapter_mod.bluesminds_v0_answer_writer, "try_write", fake_writer)

        malformed_app = make_app(client=V0GatewayClient(malformed_scenario=True))
        malformed_app["runtime_version_store"] = FakeRuntimeVersionStore("V0")
        malformed = await run_runtime_turn(malformed_app, user_id="u", message="Привет", channel="jivo")
        assert malformed["ok"] is False
        assert malformed["meta"]["trace"]["answer_writer"]["reason"] == "result_not_ok"

        operator_app = make_app(client=OperatorPhoneClient())
        operator_app["runtime_version_store"] = FakeRuntimeVersionStore("V0")
        operator_result = await run_runtime_turn(operator_app, user_id="u", message="Позовите оператора", channel="jivo")
        assert operator_result["ok"] is True
        assert operator_result["meta"]["trace"]["answer_writer"]["reason"] in {"operator_action", "operator_phone_scope"}
        assert calls == []

    asyncio.run(scenario())


def test_v0_runtime_config_error_client_text_has_no_internal_wording() -> None:
    result = runtime_adapter_mod._config_error("v0_runtime_exception", detail="KeyError")
    forbidden = re.compile(r"карточк|проверенн.*данн|непроверенн.*данн|сохран[ёе]нн.*данн|бот настроен|диалогов.*состояни", re.IGNORECASE)
    assert forbidden.search(result["answer"]) is None


def test_underfilled_broad_search_supplements_exact_cards_and_runtime_renders_three() -> None:
    async def scenario() -> None:
        client = SequenceGatewayClient([
            {"facts": [{"name": "ЖК Первый"}]},
            {"facts": [{"name": "ЖК Второй"}, {"name": "ЖК Третий"}]},
        ])
        adapter = _OvermindSearchAdapter({"overmind_client": client}, user_text="двушки в Прикубанском")
        plan = SemanticPlan(operation="search", constraints_delta={"hard": {"district": "Прикубанский", "rooms": 2}})

        result = await adapter.search(plan, ConversationState(), SafeTurnContext("u", "двушки в Прикубанском"))

        assert [card.name for card in result.shortlist(3)] == ["ЖК Первый", "ЖК Второй", "ЖК Третий"]
        # Two broad-search calls (primary + supplemental) plus exact full-card
        # enrichment for each of the three visible shortlist options.
        assert len(client.gateway_payloads) == 5
        primary_count, primary_excluded = _payload_count_and_excluded(client.gateway_payloads[0])
        supplemental_count, supplemental_excluded = _payload_count_and_excluded(client.gateway_payloads[1])
        assert primary_count == 3
        assert supplemental_count == 2
        assert primary_excluded == []
        assert "ЖК Первый" in supplemental_excluded
        _, primary_params = _request_parts(client.gateway_payloads[0])
        _, supplemental_params = _request_parts(client.gateway_payloads[1])
        assert supplemental_params["effective_hard"] == primary_params["effective_hard"] == {"district": "Прикубанский", "rooms": 2}
        assert adapter.last_attempts[-2] == {"stage": "underfilled_search_fill", "status": "filled", "requested": 2, "added": 2}
        assert adapter.last_attempts[-1]["stage"] == "shortlist_top_options_enrichment"
        assert adapter.last_attempts[-1]["count"] == 3

        turn = await TurnProcessor(planner=type("P", (), {"plan": lambda self, context, state: plan})(), search_service=type("S", (), {"search": lambda self, plan, state: result})()).process_async(SafeTurnContext("u", "двушки"))
        assert [item["name"] for item in turn.state["visible_options"]] == ["ЖК Первый", "ЖК Второй", "ЖК Третий"]
        assert all(name in turn.response_text for name in ["Первый", "Второй", "Третий"])

    asyncio.run(scenario())


def test_candidate_first_location_search_sends_broad_payload_and_moves_bad_location_facts_to_near() -> None:
    async def scenario() -> None:
        client = SequenceGatewayClient([
            {"facts": [
                {"name": "ЖК Зеленый", "location": "Зеленоград", "rooms": [2], "min_price": 12_000_000},
                {"name": "ЖК Мимо", "location": "Химки", "rooms": [2], "min_price": 12_000_000},
                {"name": "ЖК Без локации", "rooms": [2], "min_price": 12_000_000},
            ]},
        ])
        adapter = _OvermindSearchAdapter({"overmind_client": client}, user_text="В Зеленограде есть двушки?")
        plan = SemanticPlan(operation="search", constraints_delta={"hard": {"location": ["Зеленоград"], "rooms": [2]}})

        result = await adapter.search(plan, ConversationState(), SafeTurnContext("u", "В Зеленограде есть двушки?"))

        assert [card.name for card in result.facts] == ["ЖК Зеленый"]
        assert [card.name for card in result.near] == ["ЖК Мимо", "ЖК Без локации"]
        assert [card.name for card in result.shortlist(3)] == ["ЖК Зеленый"]
        assert [card.is_near for card in result.shortlist(3)] == [False]
        assert [card.why_close for card in result.near] == ["локация отличается от запроса", "локация не подтверждена"]
        assert result.params == {"location": ["Зеленоград"], "rooms": [2]}
        _, sent_params = _request_parts(client.gateway_payloads[0])
        assert sent_params["effective_hard"] == {"rooms": [2]}
        assert sent_params["requested_hard"] == {"rooms": [2]}
        marker = [item for item in adapter.last_attempts if item.get("stage") == "candidate_first_retrieval"][0]
        assert marker["enabled"] is True
        assert marker["field"] == "location"
        assert marker["strict_validation_counts"]["facts"] == 3
        report = [item for item in adapter.last_attempts if item.get("stage") == "search_validation_report"][0]
        assert report["status"] == "invalid"
        assert report["counts"]["facts"] == 3
        assert "fact_1_violates_hard" in report["errors"]
        assert "fact_2_missing_hard_evidence" in report["errors"]

        turn = await TurnProcessor(planner=type("P", (), {"plan": lambda self, context, state: plan})(), search_service=type("S", (), {"search": lambda self, plan, state: result})()).process_async(SafeTurnContext("u", "В Зеленограде есть двушки?"))
        assert [item["name"] for item in turn.state["visible_options"]] == ["ЖК Зеленый"]
        assert "ЖК Мимо" not in turn.response_text
        assert "ЖК Без локации" not in turn.response_text

    asyncio.run(scenario())


def test_candidate_first_underfilled_supplement_keeps_location_separation_and_exclusions() -> None:
    async def scenario() -> None:
        client = SequenceGatewayClient([
            {"facts": [{"name": "ЖК Первый", "location": "Зеленоград", "rooms": [2]}]},
            {"facts": [{"name": "ЖК Второй", "location": "Зеленоград", "rooms": [2]}, {"name": "ЖК Первый", "location": "Зеленоград", "rooms": [2]}]},
        ])
        adapter = _OvermindSearchAdapter({"overmind_client": client}, user_text="В Зеленограде есть двушки?")
        plan = SemanticPlan(operation="search", constraints_delta={"hard": {"location": ["Зеленоград"], "rooms": [2]}})

        result = await adapter.search(plan, ConversationState(), SafeTurnContext("u", "В Зеленограде есть двушки?"))

        assert [card.name for card in result.shortlist(3)] == ["ЖК Первый", "ЖК Второй"]
        # Primary + supplemental broad search, then exact full-card requests
        # for the two cards actually present in the shortlist.
        assert len(client.gateway_payloads) == 4
        _, primary_params = _request_parts(client.gateway_payloads[0])
        _, supplemental_params = _request_parts(client.gateway_payloads[1])
        assert primary_params["effective_hard"] == {"rooms": [2]}
        assert supplemental_params["effective_hard"] == {"rooms": [2]}
        assert "ЖК Первый" in supplemental_params["excluded_names"]
        assert result.params == {"location": ["Зеленоград"], "rooms": [2]}

    asyncio.run(scenario())


def test_underfilled_broad_search_supplements_near_cards_without_losing_near_marker() -> None:
    async def scenario() -> None:
        client = SequenceGatewayClient([
            {"facts": [{"name": "ЖК Точный", "rooms": [2]}]},
            {"near": [{"name": "ЖК Почти один", "rooms": [1], "why_close": "другая комнатность"}, {"name": "ЖК Почти два", "rooms": [3], "why_close": "другая комнатность"}]},
        ])
        adapter = _OvermindSearchAdapter({"overmind_client": client}, user_text="семейные варианты")
        plan = SemanticPlan(operation="search", constraints_delta={"hard": {"rooms": 2}})

        result = await adapter.search(plan, ConversationState(), SafeTurnContext("u", "семейные варианты"))

        cards = result.shortlist(3)
        assert [card.name for card in result.facts] == ["ЖК Точный"]
        assert result.near == ()
        assert [card.name for card in cards] == ["ЖК Точный"]
        assert [card.is_near for card in cards] == [False]

        turn = await TurnProcessor(planner=type("P", (), {"plan": lambda self, context, state: plan})(), search_service=type("S", (), {"search": lambda self, plan, state: result})()).process_async(SafeTurnContext("u", "семейные варианты"))
        assert [item["name"] for item in turn.state["visible_options"]] == ["ЖК Точный"]
        assert "ЖК Почти" not in turn.response_text
        assert "ближайшие варианты" not in turn.response_text.casefold()

    asyncio.run(scenario())


def test_underfilled_supplemental_failure_keeps_original_result() -> None:
    async def scenario() -> None:
        client = SequenceGatewayClient([
            {"facts": [{"name": "ЖК Один"}]},
            {"safe_fallback": True},
        ])
        adapter = _OvermindSearchAdapter({"overmind_client": client}, user_text="найди")

        result = await adapter.search(SemanticPlan(operation="search"), ConversationState(), SafeTurnContext("u", "найди"))

        assert [card.name for card in result.shortlist(3)] == ["ЖК Один"]
        assert len(client.gateway_payloads) == 3
        assert adapter.last_attempts[-2]["stage"] == "underfilled_search_fill"
        assert adapter.last_attempts[-2]["status"] == "failed"
        assert adapter.last_attempts[-1]["stage"] == "shortlist_top_options_enrichment"
        turn = await TurnProcessor(planner=type("P", (), {"plan": lambda self, context, state: SemanticPlan(operation="search")})(), search_service=type("S", (), {"search": lambda self, plan, state: result})()).process_async(SafeTurnContext("u", "найди"))
        assert [item["name"] for item in turn.state["visible_options"]] == ["ЖК Один"]
        assert "Один" in turn.response_text

    asyncio.run(scenario())


def test_underfilled_duplicate_supplemental_keeps_original_result() -> None:
    async def scenario() -> None:
        client = SequenceGatewayClient([
            {"facts": [{"name": "ЖК Один"}]},
            {"near": [{"name": "ЖК Один"}]},
        ])
        adapter = _OvermindSearchAdapter({"overmind_client": client}, user_text="найди")

        result = await adapter.search(SemanticPlan(operation="search"), ConversationState(), SafeTurnContext("u", "найди"))

        assert [card.name for card in result.shortlist(3)] == ["ЖК Один"]
        assert len(client.gateway_payloads) == 3

    asyncio.run(scenario())


def test_underfilled_does_not_supplement_when_primary_or_named_lookup_is_full_enough() -> None:
    async def scenario() -> None:
        broad_client = SequenceGatewayClient([{"facts": [{"name": "ЖК 1"}, {"name": "ЖК 2"}, {"name": "ЖК 3"}]}])
        broad = _OvermindSearchAdapter({"overmind_client": broad_client}, user_text="найди")
        broad_result = await broad.search(SemanticPlan(operation="search"), ConversationState(), SafeTurnContext("u", "найди"))
        assert [card.name for card in broad_result.shortlist(3)] == ["ЖК 1", "ЖК 2", "ЖК 3"]
        assert len(broad_client.gateway_payloads) == 4

        named_client = SequenceGatewayClient([{"facts": [{"name": "ЖК Лучи"}]}])
        named = _OvermindSearchAdapter({"overmind_client": named_client}, user_text="Лучи")
        named_result = await named.search(SemanticPlan(operation="lookup_object", reference="ЖК Лучи"), ConversationState(), SafeTurnContext("u", "Лучи"))
        assert [card.name for card in named_result.shortlist(3)] == ["ЖК Лучи"]
        count, _excluded = _payload_count_and_excluded(named_client.gateway_payloads[0])
        assert count == 1
        assert len(named_client.gateway_payloads) == 2

    asyncio.run(scenario())


def test_callback_confirmation_requires_successful_enqueue_reference() -> None:
    missing_ref = _queue_v2_callback_result(
        {},
        user_id="u",
        channel="jivo",
        meta={"event_id": "evt"},
        state=ConversationState(selected_option_name="Бусиновский парк", active_topic="financing"),
        name="Анна",
        phone="+7 912 000-00-01",
    )

    assert missing_ref["public"]["meta"]["callback_ref"] is None
    assert "передана оператору" not in missing_ref["public"]["answer"]
    assert "Контакт сохранила" in missing_ref["public"]["answer"]

    class SuccessfulOutbox:
        def enqueue_callback(self, **_kwargs: Any) -> Any:
            return type("Result", (), {"lead_ref": "lead-1"})()

    queued = _queue_v2_callback_result(
        {"crm_callback_outbox": SuccessfulOutbox()},
        user_id="u",
        channel="jivo",
        meta={"event_id": "evt"},
        state=ConversationState(selected_option_name="Бусиновский парк", active_topic="financing"),
        name="Анна",
        phone="+7 912 000-00-01",
    )

    assert queued["public"]["meta"]["callback_ref"] == "lead-1"
    assert "передана оператору" in queued["public"]["answer"]


def patch_planner(monkeypatch, plan: dict[str, Any]) -> None:
    async def fake_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return plan

    monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)


def env(monkeypatch, *, version: str | None = None) -> None:
    if version is None:
        monkeypatch.delenv("NMBOT_RUNTIME_VERSION", raising=False)
    else:
        monkeypatch.setenv("NMBOT_RUNTIME_VERSION", version)
    monkeypatch.delenv("NMBOT_V2_SHADOW", raising=False)


def test_runtime_env_cannot_select_v1_and_legacy_callback_is_rejected(monkeypatch):
    async def scenario() -> None:
        calls: list[str] = []

        async def legacy(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("legacy")
            return {"ok": True, "answer": "V1", "intent": "main_search", "handoff_to_operator": False}

        env(monkeypatch, version="v1")
        patch_planner(monkeypatch, {"operation": "current_options", "confidence": 1.0})
        result = await mod.run_runtime_turn(make_app({"nmbot_v2": {"visible_options": [{"name": "Лучи", "price": "от 12 млн рублей"}]}}), user_id="u", message="привет", channel="api", meta={})
        assert result["meta"]["runtime"] == "v2"
        assert "V1" not in result["answer"]

        try:
            await mod.run_runtime_turn(make_app(), user_id="u", message="привет", channel="api", meta={}, legacy_runner=legacy)  # type: ignore[call-arg]
        except TypeError:
            pass
        else:  # pragma: no cover - executable contract must reject the argument
            raise AssertionError("run_runtime_turn accepted legacy_runner")
        assert calls == []

    asyncio.run(scenario())


def test_v2_runtime_static_isolation_has_no_prohibited_v1_business_symbols():
    prohibited = {
        "_run_chat_v1",
        "legacy_runner",
        "NMBOT_RUNTIME_VERSION",
        "chat_tester_bot",
        "nmbot_api_server",
        "_answer_current_options",
        "_render_stage_first_list",
        "_render_stage_selected_object",
        "render_current_options_answer",
    }
    files = [
        Path(__file__).resolve().parents[1] / "scripts" / "nmbot_runtime_adapter.py",
        *(Path(__file__).resolve().parents[1] / "nmbot_v2").glob("*.py"),
    ]
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert not (names | attrs) & prohibited, path
        source = path.read_text(encoding="utf-8")
        assert not any(item in source for item in prohibited), path


def test_api_run_chat_source_does_not_reference_v1_runner():
    path = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_api_server.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    run_chat = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_chat")
    names = {node.id for node in ast.walk(run_chat) if isinstance(node, ast.Name)}
    assert "_run_chat_v1" not in names
    assert "legacy_runner" not in names


def test_runtime_adapter_has_no_api_back_reference():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "nmbot_runtime_adapter.py").read_text(encoding="utf-8")
    assert "_api_module" not in source
    assert "nmbot_api_server" not in source


def test_api_planner_state_matches_neutral_bounded_contract():
    from scripts.nmbot_planner_context import safe_planner_state

    state = {
        "params": {"purpose": "family", "max_price": 25_000_000, "phone": "+7 999 111-22-33"},
        "visible_options": [
            {"name": "Лучи", "price_range": "от 12 млн рублей", "developer": "comfort", "client_id": "secret"},
            {"name": "Дзен", "location": "Москва", "phone": "+7 999 111-22-33"},
        ],
        "dialog_window": [{"role": "user", "text": "семейный вариант"}],
        "last_bot_question": "Позвонить +7 999 111-22-33?",
        "retry_search": {"error_code": "v2_search_gateway_not_ok", "raw_text": "secret"},
        "recovery_count": 1,
    }

    api_payload = mod._safe_planner_state("семейный вариант", state)

    assert api_payload == safe_planner_state("семейный вариант", state)
    dumped = json.dumps(api_payload, ensure_ascii=False)
    assert "+7 999 111-22-33" not in dumped
    assert "client_id" not in dumped
    assert "raw_text" not in dumped
    assert api_payload["primary_intent"] == "family"
    assert "primary_intent" in api_payload["known_fields"]


def test_v2_native_conversation_has_no_internal_or_unsupported_sales_claims():
    source = (Path(__file__).resolve().parents[1] / "nmbot_v2" / "conversation.py").read_text(encoding="utf-8").casefold()
    forbidden = (
        "ликвидност",
        "простоту сдачи",
        "легко сдать",
        "спрос",
        "доходност",
        "окупаемост",
        "рост цен",
        "в карточк",
        "mcp",
        "json",
    )
    assert not any(item in source for item in forbidden)


def test_v2_native_answer_keeps_exactly_one_final_question(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"operation": "current_options", "intent": "rental", "scope": "all", "confidence": 1.0})
        app = make_app()

        result = await mod.run_chat(app, user_id="u", message="а эти под аренду?", channel="jivo", meta={})

        assert result["ok"] is True
        assert result["answer"].count("?") == 1
        assert result["answer"].endswith("?")

    asyncio.run(scenario())


def test_v2_runtime_monkeypatches_prohibited_v1_business_functions_and_still_answers(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        for name in ("_run_chat_v1", "_answer_current_options", "_render_stage_first_list", "_render_stage_selected_object"):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError(name)))
        patch_planner(monkeypatch, {"operation": "current_options", "intent": "rental", "scope": "all", "confidence": 1.0})
        initial = mod._default_state()
        initial["visible_options"] = [{"name": "Лучи", "price_range": "от 12 млн рублей", "location": "Москва"}]
        app = make_app(initial)

        result = await mod.run_chat(app, user_id="u", message="а под аренду?", channel="jivo", meta={})

        assert result["ok"] is True
        assert "под аренду" in result["answer"]

    asyncio.run(scenario())


def test_invalid_runtime_version_env_is_ignored_and_v2_route_stays_active(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="bad")
        patch_planner(monkeypatch, {"operation": "current_options", "intent": "life", "scope": "all", "confidence": 1.0})
        app = make_app({"nmbot_v2": {"visible_options": [{"name": "Лучи", "price": "от 12 млн рублей"}]}})
        result = await mod.run_chat(app, user_id="u", message="привет", channel="api", meta={})

        assert result["ok"] is True
        assert result["meta"]["runtime"] == "v2"
        assert "runtime_config_error" not in json.dumps(result, ensure_ascii=False)
        assert app["state_store"].saved
        assert set(app["state_store"].states["u"]) == {"nmbot_v2"}

    asyncio.run(scenario())


def test_v2_namespace_dict_is_canonical_and_legacy_root_cannot_shadow_it() -> None:
    state = _legacy_to_v2_state(
        {
            "params": {"location": "legacy", "phone": "+7 999 123-45-67"},
            "visible_options": [{"name": "Legacy option", "price_range": "от 99 млн рублей"}],
            "selected_option": {"name": "Legacy selected", "developer": "Legacy dev"},
            "pending_followup": "legacy_followup",
            "dialog_turns": [{"role": "user", "text": "legacy user"}, {"role": "bot", "text": "legacy bot"}],
            "operator_declined": True,
            "nmbot_v2": {
                "params": {"location": "canonical", "phone": "+7 999 123-45-67"},
                "visible_options": [{"name": "Canonical option", "price": "от 12 млн рублей"}],
                "selected_option_name": "Canonical option",
                "selected_enriched": {"name": "Canonical option", "developer": "Canonical dev"},
                "pending_followup": "contact_name",
                "recent_turns": [{"user": "canonical user", "assistant": "canonical bot"}],
                "operator_declined": False,
            },
        }
    )

    assert state.params == {"location": "canonical"}
    assert [option.name for option in state.visible_options] == ["Canonical option"]
    assert state.selected_option_name == "Canonical option"
    assert state.selected_enriched and state.selected_enriched.developer == "Canonical dev"
    assert state.pending_followup == "contact_name"
    assert state.recent_turns == ({"user": "canonical user", "assistant": "canonical bot"},)
    assert state.operator_declined is False


def test_empty_v2_namespace_dict_does_not_import_stale_legacy_root() -> None:
    state = _legacy_to_v2_state(
        {
            "params": {"location": "legacy"},
            "visible_options": [{"name": "Legacy option"}],
            "selected_option": {"name": "Legacy selected"},
            "pending_followup": "contact_phone",
            "operator_declined": True,
            "nmbot_v2": {},
        }
    )

    assert state == ConversationState()


def test_legacy_only_state_still_migrates_to_v2_bounded_reader() -> None:
    state = _legacy_to_v2_state(
        {
            "params": {"location": "Москва", "phone": "+7 999 123-45-67"},
            "visible_options": [{"name": "Лучи", "price_range": "от 12 млн рублей", "location": "Москва"}],
            "selected_option": {"name": "Лучи", "developer": "ПИК"},
            "pending_followup": {"type": "contact_phone", "raw": "+7 999 123-45-67"},
            "dialog_turns": [{"role": "user", "text": "мой телефон +7 999 123-45-67"}, {"role": "bot", "text": "ок"}],
            "operator_declined": True,
        }
    )

    assert state.params == {"location": "Москва"}
    assert [option.name for option in state.visible_options] == ["Лучи"]
    assert state.selected_option_name == "Лучи"
    assert state.selected_enriched and state.selected_enriched.developer == "ПИК"
    assert state.pending_followup == "contact_phone"
    assert "999" not in str(state.recent_turns)
    assert state.operator_declined is True


def test_legacy_contact_flow_migrates_to_v2_without_copying_private_phone() -> None:
    state = _legacy_to_v2_state(
        {
            "contact_flow": "awaiting_contact_phone",
            "awaiting_phone": True,
            "contact_name": "Иван",
            "last_callback_ref": "lead-123",
            "last_phone_meta": {"digits_len": 11, "captured": True},
        }
    )

    assert state.pending_followup == "contact_phone"
    assert state.contact_name == "Иван"
    assert state.callback_ref == "lead-123"
    assert state.contact_consent is True
    assert state.contact_phone_redacted is None
    assert "digits_len" not in str(state.to_dict())


def test_old_contact_name_string_reads_as_offer_until_consent_then_name_capture() -> None:
    old_offer = ConversationState.from_dict({"pending_followup": "contact_name", "contact_consent": False})
    after_consent = ConversationState.from_dict({"pending_followup": "contact_name", "contact_consent": True})

    assert old_offer.pending_followup == "contact_name"
    assert old_offer.pending_state and old_offer.pending_state.kind is PendingKind.OPERATOR_CONSENT
    assert after_consent.pending_state and after_consent.pending_state.kind is PendingKind.CONTACT_NAME


def test_malformed_non_dict_v2_namespace_falls_back_to_legacy_reader() -> None:
    state = _legacy_to_v2_state(
        {
            "params": {"location": "Москва"},
            "visible_options": [{"name": "Лучи", "price_range": "от 12 млн рублей"}],
            "selected_option": {"name": "Лучи"},
            "pending_followup": "contact_name",
            "nmbot_v2": "malformed-old-envelope",
        }
    )

    assert state.params == {"location": "Москва"}
    assert [option.name for option in state.visible_options] == ["Лучи"]
    assert state.selected_option_name == "Лучи"
    assert state.pending_followup == "contact_name"


def test_v2_search_maps_state_and_commits_only_after_success(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"operation": "search", "constraints_delta": {"hard": {"location": "Москва"}}, "confidence": 1.0})
        monkeypatch.setattr(mod, "_ensure_derived_canonical_plan", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("V2 must not build canonical planner dict")))
        app = make_app(client=FakeClient(options=[{"name": "Лучи", "price_range": "от 12 млн рублей", "location": "Москва"}]))

        result = await mod.run_chat(app, user_id="u", message="найди в Москве", channel="jivo", meta={})

        assert result["ok"] is True
        assert result["meta"]["runtime"] == "v2"
        saved = app["state_store"].states["u"]
        assert saved["nmbot_v2"]["visible_options"][0]["name"] == "Лучи"
        assert saved["nmbot_v2"]["params"]["location"] == "Москва"

    asyncio.run(scenario())


def test_v2_search_adapter_normalizes_runtime_owned_diagnostics(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"operation": "search", "intent": "mortgage", "constraints_delta": {"hard": {"rooms": [2]}, "preferences": {"finance_preference": "mortgage_details"}}, "confidence": 1.0})
        initial = mod._default_state()
        initial["primary_intent"] = "family"
        initial["params"] = {"purpose": "family"}
        app = make_app(initial, client=FakeClient(options=[{"name": "Семейный", "rooms": [2], "min_price": 12_000_000, "mortgage_calc": {"payment": 100000}, "school": True}], bad_diagnostics=True))

        result = await mod.run_chat(app, user_id="u", message="а ипотека есть?", channel="jivo", meta={})

        assert result["ok"] is True
        saved = app["state_store"].states["u"]
        assert saved["nmbot_v2"]["visible_options"][0]["name"] == "Семейный"

    asyncio.run(scenario())


def test_v2_search_rebuilds_cards_from_v1_raw_facts_and_visible_order(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        options = [
            {"name": "Первый", "price_range": "от 12 млн рублей", "location": "Москва"},
            {"name": "Второй", "price_range": "от 14 млн рублей", "location": "Москва"},
            {"name": "Третий", "price_range": "от 16 млн рублей", "location": "Москва"},
        ]
        patch_planner(monkeypatch, {"operation": "search", "confidence": 1.0})
        app = make_app(client=FakeClient(options=options))

        result = await mod.run_chat(app, user_id="u", message="квартира под инвестицию", channel="jivo", meta={})

        assert result["ok"] is True
        assert [item["name"] for item in app["state_store"].states["u"]["nmbot_v2"]["visible_options"]] == ["Первый", "Второй", "Третий"]
        assert "Первый" in result["answer"]
        assert "Точно таких вариантов сейчас не вижу" not in result["answer"]
        assert app["overmind_client"].composer_calls == 0

    asyncio.run(scenario())


def test_v2_first_list_enriches_shortlist_cards(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        options = [
            {"name": "Первый", "min_price": 12_000_000, "location": "Москва"},
            {"name": "Второй", "min_price": 14_000_000, "location": "Москва"},
            {"name": "Третий", "min_price": 16_000_000, "location": "Москва"},
        ]
        patch_planner(monkeypatch, {"operation": "search", "intent": "life", "confidence": 1.0})
        client = FakeClient(options=options)
        app = make_app(client=client)

        first = await mod.run_chat(app, user_id="u", message="найди", channel="jivo", meta={})
        saved = app["state_store"].states["u"]["nmbot_v2"]["visible_options"]

        assert first["ok"] is True
        assert [item["name"] for item in saved] == ["Первый", "Второй", "Третий"]
        search_payloads = [payload for payload in client.gateway_payloads if payload.get("_payload_stage") == "main_search"]
        assert len(search_payloads) == 4
        assert client.composer_calls == 0
        assert client.enrich_calls == 0
        assert first["meta"]["trace"]["option_enrichment"] == {}

    asyncio.run(scenario())


def test_v2_search_adapter_reports_missing_hard_evidence_without_demoting(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        client = FakeClient(options=[{"name": "ЖК Без комнат", "min_price": 12_000_000, "location": "Москва"}])
        app = make_app(client=client)
        adapter = _OvermindSearchAdapter(app)
        plan = SemanticPlan(operation="search", constraints_delta={"hard": {"rooms": [2]}})

        result = await adapter.search(plan, ConversationState(), SafeTurnContext("u", "нужна двушка"))

        assert [card.name for card in result.facts] == ["ЖК Без комнат"]
        assert result.near == ()
        assert client.gateway_calls == 3  # primary + fill + exact shortlist card
        report = [item for item in adapter.last_attempts if item.get("stage") == "search_validation_report"][0]
        assert report["status"] == "invalid"
        assert "fact_0_missing_hard_evidence" in report["errors"]

    asyncio.run(scenario())


def test_v2_compare_dialog_action_resolves_from_current_state_without_new_search(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["visible_options"] = [
            {"name": "Первый", "price_range": "от 12 млн рублей", "location": "Москва"},
            {"name": "Второй", "price_range": "от 14 млн рублей", "location": "Москва"},
        ]
        patch_planner(monkeypatch, {"dialog_action": "compare_options", "intent": "rental", "scope": "all", "confidence": 0.95})
        client = FakeClient()
        app = make_app(initial, client=client)

        result = await mod.run_chat(app, user_id="u", message="сравни первый и второй", channel="jivo", meta={})

        assert result["ok"] is True
        assert result["turn_decision"]["action"] == "answer_from_current_options"
        assert client.ask_calls == 0
        search_calls = [payload for payload in client.gateway_payloads if payload.get("_payload_stage") == "main_search"]
        assert search_calls == []
        assert client.composer_calls == 0
        assert [item["name"] for item in app["state_store"].states["u"]["nmbot_v2"]["visible_options"]] == ["Первый", "Второй"]

    asyncio.run(scenario())


def test_semantic_selected_decision_maps_to_selected_operation() -> None:
    plan = _semantic_plan_from_planner({
        "action": "answer_current_options",
        "dialog_action": "select_option",
        "scope": "one",
        "selected_option_name": "Мичуринский парк",
        "requested_facts": ["parking"],
        "facts_needed": [],
        "resolved_subject": "parking",
        "focus_action": "switch",
    }, query_text="Мичуринский парк, там есть парковка?")

    assert plan.operation == "select_option"
    assert plan.selected_option_name == "Мичуринский парк"
    assert plan.scope == "one"
    assert plan.resolved_subject == "parking"
    assert plan.requested_facts == ("parking",)


def test_h054_semantic_result_adapter_merges_needs_and_comparison_facets() -> None:
    raw = {
        "user_goal": "оценить для семьи под аренду и ипотеку",
        "refers_to_existing_objects": True,
        "requests_new_objects": False,
        "scenario_needs": ["family", "rental", "mortgage"],
        "requested_comparison": ["price", "financing"],
        "requested_facts": ["finishing", "metro", "mortgage_terms"],
        "facts_needed": ["finishing", "metro", "mortgage_terms"],
        "response_viewpoint": "family",
        "confidence": 0.95,
    }
    semantic = normalize_semantic_planner_result(raw)
    decision = derive_runtime_decision(semantic, state={"primary_intent": "family", "visible_options": [{"name": "Первый"}]})

    plan = _semantic_plan_from_semantic_result(semantic, decision, raw, query_text="для семьи под аренду и ипотеку")

    assert plan.facets == ["family", "rental", "financing", "price"]
    assert plan.requested_facts == ("finishing", "metro", "mortgage_terms")
    assert plan.facts_needed == ("finishing", "metro", "mortgage_terms")
    assert plan.constraints_delta == {"hard": {}, "preferences": {}, "unknown": {}}


def test_contact_resume_signal_restores_operator_route_only_for_pending_contact() -> None:
    plan = SemanticPlan(operation="current_options", resolved_intent="resume_contact")
    state = ConversationState(
        pending_followup="contact_name",
        selected_option_name="Лучи",
        visible_options=(OptionCard(name="Лучи"),),
    )

    resumed = _inherit_selected_scope(plan, state)

    assert resumed.operation == "operator"
    assert resumed.explicit_operator_request is True
    assert resumed.selected_option_name == "Лучи"


def test_pending_contact_is_visible_to_semantic_planner_with_bounded_resume_outcome() -> None:
    state = ConversationState(
        pending_followup="contact_name",
        selected_option_name="Лучи",
        visible_options=(OptionCard(name="Лучи"),),
    )

    pending = _pending_scenario_for_planner(state)

    assert pending == {
        "id": "operator_consent",
        "allowed_reply_outcomes": ["accept", "decline", "ask_or_clarify", "unexpected"],
        "context": {"scope": "one", "offered_action": "collect_contact_phone", "selected_option_name": "Лучи"},
    }


def test_v2_search_uses_full_v1_visible_cards_when_raw_search_payload_is_hidden(monkeypatch):
    class FullVisibleClient(FakeClient):
        def __init__(self) -> None:
            super().__init__(options=[
                {"name": "Первый", "min_price": 12_000_000, "location": "Москва", "ready": "2027"},
                {"name": "Второй", "min_price": 14_000_000, "location": "Москва", "ready": "2028"},
            ])

    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"operation": "search", "confidence": 1.0})
        app = make_app(client=FullVisibleClient())

        result = await mod.run_chat(app, user_id="u", message="квартира под инвестицию", channel="jivo", meta={})

        assert result["ok"] is True
        assert [item["name"] for item in app["state_store"].states["u"]["nmbot_v2"]["visible_options"]] == ["Первый", "Второй"]
        assert "Первый" in result["answer"]
        assert "Точно таких вариантов сейчас не вижу" not in result["answer"]

    asyncio.run(scenario())


def test_v2_search_uses_exact_original_message_not_intent_or_reference(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"operation": "search", "intent": "investment", "reference": "инвестиция", "confidence": 1.0})
        client = FakeClient()
        app = make_app(client=client)

        result = await mod.run_chat(app, user_id="u", message="двушка в Москве под инвестицию до 60 млн", channel="jivo", meta={})

        assert result["ok"] is True
        assert client.ask_calls == 0
        search_calls = [
            payload
            for payload in client.gateway_payloads
            if payload.get("_payload_stage") == "main_search"
        ]
        assert len(search_calls) == 3
        assert all("двушка в Москве под инвестицию до 60 млн" in call["query"] for call in search_calls[:2])

    asyncio.run(scenario())


def test_v2_failed_initial_search_persists_bounded_retry_context_without_business_mutation(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {
            "operation": "search",
            "intent": "rental",
            "constraints_delta": {
                "hard": {"area_min_m2": 40, "area_max_m2": 50, "max_price": 15_000_000, "phone": "+79991234567"},
                "preferences": {"finance_preference": "cash", "raw_prompt": "secret"},
            },
            "confidence": 1.0,
        })
        app = make_app(client=FakeClient(fail=True))
        result = await mod.run_chat(app, user_id="u", message="аренла 40-50м +79991234567", channel="jivo", meta={})

        assert result["ok"] is False
        assert "Старые условия" not in result["answer"]
        assert result["answer"].rstrip().endswith("Передать оператору запрос?")
        forbidden = ("не получилось обновить", "повторить поиск", "попробовать ещё раз", "попробовать еще раз")
        assert all(text not in result["answer"].casefold() for text in forbidden)
        assert result["meta"]["trace"]["terminal_fallback"] == {"kind": "technical_failure", "operator_offer": True}
        saved = app["state_store"].states["u"]
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"].get("params") in (None, {})
        assert saved["nmbot_v2"].get("visible_options") in (None, [])
        assert saved["nmbot_v2"]["operator_offered"] is True
        assert saved["nmbot_v2"]["pending_followup"] == "selected_live_fact_consent"
        assert saved["nmbot_v2"]["contact_consent"] is False
        retry = saved["nmbot_v2"]["retry_search"]
        assert retry == {
            "viewpoint": "rental",
            "intent": "rental",
            "hard_constraints": {"area_min_m2": 40, "area_max_m2": 50, "max_price": 15_000_000},
            "preferences": {"finance_preference": "cash"},
            "error_code": "v2_search_gateway_not_ok",
            "attempt_kind": "initial",
        }
        serialized = json.dumps(saved["nmbot_v2"], ensure_ascii=False)
        assert "аренла" not in serialized
        assert "+7999" not in serialized
        assert "secret" not in serialized
        assert "provider down" not in serialized

    asyncio.run(scenario())


def test_v3_failed_search_inherits_v2_operator_offer_without_namespace_changes(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v3")
        patch_planner(monkeypatch, {"operation": "search", "intent": "life", "confidence": 1.0})
        initial = mod._default_state()
        initial["runtime_version_override"] = "V3"
        app = make_app(initial, client=FakeClient(fail=True))

        result = await mod.run_chat(app, user_id="u", message="подбери", channel="jivo", meta={})

        assert result["ok"] is False
        assert result["meta"]["runtime"] == "v3"
        assert result["meta"]["engine"] == "v2"
        assert result["answer"].rstrip().endswith("Передать оператору запрос?")
        forbidden = ("не получилось обновить", "повторить поиск", "попробовать ещё раз", "попробовать еще раз")
        assert all(text not in result["answer"].casefold() for text in forbidden)
        saved = app["state_store"].states["u"]
        assert "nmbot_v2" in saved
        assert "nmbot_v3" not in saved
        assert saved["nmbot_v2"]["operator_offered"] is True
        assert saved["nmbot_v2"]["pending_followup"] == "selected_live_fact_consent"

    asyncio.run(scenario())


def test_v2_failed_refresh_search_offers_operator_and_sets_refresh_retry(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["params"] = {"location": "Сокол"}
        initial["visible_options"] = [{"name": "Сокол Парк", "price_range": "от 15 млн"}]
        patch_planner(monkeypatch, {"operation": "refine_search", "constraints_delta": {"hard": {"max_price": 17_000_000}}, "confidence": 1.0})
        app = make_app(initial, client=FakeClient(fail=True))

        result = await mod.run_chat(app, user_id="u", message="до 17", channel="jivo", meta={})

        assert result["ok"] is False
        assert result["answer"].rstrip().endswith("Передать оператору запрос?")
        saved = app["state_store"].states["u"]
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"]["visible_options"][0]["name"] == "Сокол Парк"
        assert saved["nmbot_v2"]["params"] == initial["params"]
        assert saved["nmbot_v2"]["operator_offered"] is True
        assert saved["nmbot_v2"]["pending_followup"] == "selected_live_fact_consent"
        assert saved["nmbot_v2"]["retry_search"]["attempt_kind"] == "refresh"

    asyncio.run(scenario())


def test_v2_successful_search_clears_retry_context(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["nmbot_v2"] = {"retry_search": {"viewpoint": "rental", "intent": "rental", "hard_constraints": {"max_price": 10}, "preferences": {}, "error_code": "v2_search_gateway_not_ok", "attempt_kind": "initial"}}
        patch_planner(monkeypatch, {"operation": "search", "intent": "rental", "confidence": 1.0})
        app = make_app(initial, client=FakeClient(options=[{"name": "Лучи", "min_price": 12_000_000}]))

        result = await mod.run_chat(app, user_id="u", message="повтори", channel="jivo", meta={})

        assert result["ok"] is True
        assert "retry_search" not in app["state_store"].states["u"]["nmbot_v2"]

    asyncio.run(scenario())


def test_v2_search_request_uses_canonical_search_model(monkeypatch):
    async def scenario() -> None:
        from nmbot_v2.search_contract import SEARCH_MODEL

        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"operation": "search", "intent": "rental", "confidence": 1.0})
        client = FakeClient(options=[{"name": "Лучи", "min_price": 12_000_000}])
        result = await mod.run_chat(make_app(client=client), user_id="u", message="под аренду", channel="jivo", meta={})

        search_payloads = [payload for payload in client.gateway_payloads if payload.get("_payload_stage") == "main_search"]
        assert result["ok"] is True
        assert search_payloads
        assert {payload.get("model") for payload in search_payloads} == {SEARCH_MODEL}

    asyncio.run(scenario())


def test_retry_search_reaches_planner_legacy_state_and_is_allowlisted() -> None:
    state = ConversationState.from_dict({
        "retry_search": {
            "viewpoint": "rental",
            "intent": "rental",
            "hard_constraints": {"area_min_m2": 40, "area_max_m2": 50},
            "preferences": {"finance_preference": "cash"},
            "error_code": "v2_search_gateway_not_ok",
            "attempt_kind": "initial",
            "raw_text": "must_drop",
        }
    })

    legacy = _v2_to_planner_legacy_state(state)

    assert legacy["retry_search"] == {
        "viewpoint": "rental",
        "intent": "rental",
        "hard_constraints": {"area_min_m2": 40, "area_max_m2": 50},
        "preferences": {"finance_preference": "cash"},
        "error_code": "v2_search_gateway_not_ok",
        "attempt_kind": "initial",
    }
    assert set(legacy["retry_search"]) == {"viewpoint", "intent", "hard_constraints", "preferences", "error_code", "attempt_kind"}
    planner_state = mod._safe_planner_state("повтори", legacy)
    assert planner_state["retry_search"] == legacy["retry_search"]


def test_v2_unknown_named_object_uses_mcp_and_returns_honest_not_found(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"named_object_reference": "Нет такого", "confidence": 1.0})
        app = make_app()

        result = await mod.run_chat(app, user_id="u", message="Нет такого", channel="jivo", meta={})

        assert result["ok"] is True
        assert "не нашла подтверждённой информации" in result["answer"]
        assert "MCP" not in result["answer"]
        assert "Проверим написание названия" in result["answer"]
        assert "всем этим ЖК" not in result["answer"]
        assert result["answer"].count("?") == 1
        assert app["overmind_client"].gateway_calls == 1
        assert app["state_store"].saved
        assert "retry_search" not in app["state_store"].states["u"].get("nmbot_v2", {})

    asyncio.run(scenario())


def test_v2_named_object_lookup_accepts_only_exact_mcp_card(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {
            "user_goal": "узнать паркинг в названном ЖК",
            "named_object_reference": "Северный берег",
            "resolved_subject": "parking",
            "requested_facts": ["parking"],
            "facts_needed": ["parking"],
            "requires_enrichment": True,
            "response_viewpoint": "life",
            "confidence": 1.0,
        })
        client = FakeClient(options=[
            {"name": "Северный парк", "parking": True},
            {"name": "ЖК «Северный берег»", "parking": True, "location": "Москва"},
        ])
        app = make_app(client=client)

        result = await mod.run_chat(app, user_id="u", message="В ЖК Северный берег есть паркинг?", channel="jivo", meta={})

        assert result["ok"] is True
        assert "Северный берег" in result["answer"]
        assert "паркинг есть" in result["answer"]
        assert "Северный парк" not in result["answer"]
        assert client.gateway_calls == 2
        payload = client.gateway_payloads[0]
        assert '"lookup_mode": "exact_named_object"' in payload["query"]
        assert '"entity_reference": "Северный берег"' in payload["query"]
        saved = app["state_store"].states["u"]["nmbot_v2"]
        assert saved["selected_option_name"] == "ЖК «Северный берег»"
        assert [item["name"] for item in saved["visible_options"]] == ["ЖК «Северный берег»"]

    asyncio.run(scenario())


def test_v2_named_object_lookup_combines_budget_price_and_mortgage(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {
            "user_goal": "проверить, подходит ли названный ЖК",
            "named_object_reference": "Северный берег",
            "resolved_subject": "financing",
            "requested_facts": ["apartment_price", "mortgage_terms"],
            "facts_needed": ["mortgage_terms"],
            "constraints_delta": {
                "hard": {"max_price": 10_000_000},
                "preferences": {"finance_preference": "family_mortgage"},
                "unknown": {},
            },
            "response_viewpoint": "financing",
            "confidence": 1.0,
        })
        client = FakeClient(options=[{
            "name": "Северный берег",
            "min_price": 12_400_000,
            "location": "Москва",
            "ready": "сдан",
        }])
        app = make_app(client=client)

        result = await mod.run_chat(
            app,
            user_id="u",
            message="У меня денег мало, всего 10 млн, планируем семейную ипотеку. ЖК Северный берег подойдёт?",
            channel="jivo",
            meta={},
        )

        assert result["ok"] is True
        assert "не укладывается" in result["answer"]
        assert "12,4 млн" in result["answer"]
        assert "семейной ипотеки" in result["answer"]
        assert "весь бюджет или первоначальный взнос" in result["answer"]
        assert "Проверить условия по этому ЖК?" not in result["answer"]
        assert "Москва" in result["answer"]
        assert result["answer"].count("?") == 1

    asyncio.run(scenario())


def test_v2_malformed_semantic_operation_fails_closed_without_side_effects(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"operation": "malformed_side_effect", "constraints_delta": {"hard": {"location": "Москва"}}, "confidence": 1.0})
        client = FakeClient()
        app = make_app(client=client)

        result = await mod.run_chat(app, user_id="u", message="найди в Москве", channel="jivo", meta={})

        assert result["ok"] is False
        assert result["error_type"] == "malformed_operation"
        assert client.gateway_calls == 0
        assert app["state_store"].saved == []
        assert app["state_store"].states["u"].get("nmbot_v2") in (None, {})

    asyncio.run(scenario())


def test_v2_state_loads_old_state_without_retry_search() -> None:
    assert _legacy_to_v2_state({"nmbot_v2": {"params": {"location": "Москва"}}}).retry_search is None


def test_v2_provider_failure_leaves_legacy_business_state_unchanged(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["params"] = {"location": "Сокол"}
        initial["visible_options"] = [{"name": "Сокол Парк", "price_range": "от 15 млн"}]
        patch_planner(monkeypatch, {"operation": "refine_search", "constraints_delta": {"hard": {"max_price": 17_000_000}}, "confidence": 1.0})
        app = make_app(initial, client=FakeClient(fail=True))
        result = await mod.run_chat(app, user_id="u", message="до 17", channel="jivo", meta={})

        assert result["ok"] is False
        assert result["error"] == "v2_provider_error"
        saved = app["state_store"].states["u"]
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"]["params"] == initial["params"]
        assert saved["nmbot_v2"]["visible_options"][0]["name"] == "Сокол Парк"
        assert saved["nmbot_v2"]["retry_search"]["hard_constraints"] == {"location": "Сокол", "max_price": 17_000_000}
        assert result["answer"].rstrip().endswith("Передать оператору запрос?")
        assert saved["nmbot_v2"]["operator_offered"] is True
        assert saved["nmbot_v2"]["pending_followup"] == "selected_live_fact_consent"
        assert app["overmind_client"].gateway_calls == 1

    asyncio.run(scenario())


def test_v2_provider_failure_operator_offer_accept_opens_contact_flow(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        plans = [
            {"operation": "refine_search", "constraints_delta": {"hard": {"max_price": 17_000_000}}, "confidence": 1.0},
            {"operation": "freeform", "followup_outcome": "accept", "confidence": 1.0},
        ]

        async def fake_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return plans.pop(0)

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)
        initial = mod._default_state()
        initial["params"] = {"location": "Сокол"}
        client = FakeClient(fail=True)
        app = make_app(initial, client=client)

        first = await mod.run_chat(app, user_id="u", message="до 17", channel="jivo", meta={})

        assert first["ok"] is False
        assert first["answer"].rstrip().endswith("Передать оператору запрос?")
        saved = app["state_store"].states["u"]["nmbot_v2"]
        assert saved.get("pending_followup") == "selected_live_fact_consent"
        assert saved.get("operator_offered") is True

        client.fail = False
        second = await mod.run_chat(app, user_id="u", message="да", channel="jivo", meta={})

        assert second["ok"] is True
        assert second["intent"] == "collect_contact_phone"
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "contact_phone"
        assert second["intent"] != "collect_contact_name"
        accepted = app["state_store"].states["u"]["nmbot_v2"]
        assert accepted.get("pending_followup") != "contact_name"

    asyncio.run(scenario())


def test_v2_selected_option_uses_enrich_selected_without_broad_search(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["visible_options"] = [{"name": "Лучи", "price_range": "от 12 млн рублей"}]
        patch_planner(monkeypatch, {"operation": "select_option", "selected_option_name": "Лучи", "confidence": 1.0})
        client = FakeClient(enriched={"name": "Лучи", "price_range": "от 12 млн рублей", "developer": "ПИК"})
        app = make_app(initial, client=client)

        result = await mod.run_chat(app, user_id="u", message="Лучи", channel="jivo", meta={})

        assert result["ok"] is True
        assert client.ask_calls == 0
        assert client.enrich_calls == 1
        assert app["state_store"].states["u"]["nmbot_v2"]["selected_enriched"]["developer"] == "ПИК"
        assert app["state_store"].states["u"]["nmbot_v2"]["enriched_card_cache"][0]["name"] == "Лучи"
        assert "застройщик ПИК" in result["answer"]

    asyncio.run(scenario())


def test_selected_adapter_uses_persistent_dialogue_cache_before_mcp() -> None:
    async def scenario() -> None:
        base = OptionCard(name="Мичуринский парк", location="Очаково-Матвеевское", price_min=12_000_000)
        cached_card = OptionCard(name=base.name, location=base.location, price_min=base.price_min, parking_price="от 1,9 млн рублей")
        entry = EnrichedCardCacheEntry(
            identity=enriched_card_identity(base),
            name=base.name,
            card=cached_card,
            scenario="investment",
            loaded_facts=("parking_price",),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        client = FakeClient(enriched={"name": base.name, "parking_price": "от 2,1 млн рублей"})
        adapter = _OvermindSearchAdapter(make_app(client=client))
        state = ConversationState(visible_options=(base,), selected_option_name=base.name, enriched_card_cache=(entry,))
        plan = SemanticPlan(operation="select_option", intent="investment", selected_option_name=base.name, requested_facts=("parking_price",), facts_needed=("parking_price",))

        enriched = await adapter.enrich_selected(base, state, plan)

        assert enriched.parking_price == "от 1,9 млн рублей"
        assert client.enrich_calls == 0
        assert adapter.last_enrichment_trace["source"] == "state_cache"

    asyncio.run(scenario())


def test_selected_adapter_refresh_request_bypasses_dialogue_cache(monkeypatch) -> None:
    async def scenario() -> None:
        base = OptionCard(name="Мичуринский парк", location="Очаково-Матвеевское", price_min=12_000_000)
        entry = EnrichedCardCacheEntry(
            identity=enriched_card_identity(base), name=base.name,
            card=OptionCard(name=base.name, location=base.location, price_min=base.price_min, parking_price="от 1,9 млн рублей"),
            scenario="investment", loaded_facts=("parking_price",), fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        client = FakeClient(enriched={"name": base.name, "parking_price": "от 2,1 млн рублей"})
        adapter = _OvermindSearchAdapter(make_app(client=client))
        state = ConversationState(visible_options=(base,), selected_option_name=base.name, enriched_card_cache=(entry,))
        plan = SemanticPlan(operation="select_option", query_text="покажи свежую цену паркинга", intent="investment", selected_option_name=base.name, requested_facts=("parking_price",), facts_needed=("parking_price",))

        enriched = await adapter.enrich_selected(base, state, plan)

        assert enriched.parking_price == "от 2,1 млн рублей"
        assert client.enrich_calls == 1

    asyncio.run(scenario())


def test_selected_adapter_stale_dialogue_cache_refreshes_from_mcp(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("NMBOT_V2_ENRICHED_CARD_CACHE_TTL_SECONDS", "60")
        base = OptionCard(name="Мичуринский парк", location="Очаково-Матвеевское", price_min=12_000_000)
        entry = EnrichedCardCacheEntry(
            identity=enriched_card_identity(base), name=base.name,
            card=OptionCard(name=base.name, location=base.location, price_min=base.price_min, parking_price="от 1,9 млн рублей"),
            scenario="investment", loaded_facts=("parking_price",), fetched_at="2000-01-01T00:00:00+00:00",
        )
        client = FakeClient(enriched={"name": base.name, "parking_price": "от 2,1 млн рублей"})
        adapter = _OvermindSearchAdapter(make_app(client=client))
        state = ConversationState(visible_options=(base,), selected_option_name=base.name, enriched_card_cache=(entry,))
        plan = SemanticPlan(operation="select_option", intent="investment", selected_option_name=base.name, requested_facts=("parking_price",), facts_needed=("parking_price",))

        enriched = await adapter.enrich_selected(base, state, plan)

        assert enriched.parking_price == "от 2,1 млн рублей"
        assert client.enrich_calls == 1

    asyncio.run(scenario())


def test_enriched_card_cache_survives_state_roundtrip_separately_from_visible_options() -> None:
    card = OptionCard(name="Кэш ЖК", location="Москва", price_min=10_000_000, sales_count=12)
    entry = EnrichedCardCacheEntry(
        identity=enriched_card_identity(card), name=card.name, card=card,
        scenario="investment", loaded_facts=("sales_count",), fetched_at="2026-07-22T15:50:00+00:00",
    )
    updated = apply_state_delta(ConversationState(), StateDelta(enriched_card_cache=(entry,)))
    restored = ConversationState.from_dict(updated.to_dict())

    assert restored.visible_options == ()
    assert restored.enriched_card_cache[0].name == "Кэш ЖК"
    assert restored.enriched_card_cache[0].card.sales_count == 12

    new_shortlist = apply_state_delta(
        restored,
        StateDelta(visible_options=(OptionCard(name="Новый ЖК", location="Москва"),)),
    )
    assert new_shortlist.visible_options[0].name == "Новый ЖК"
    assert new_shortlist.enriched_card_cache[0].name == "Кэш ЖК"


def test_selected_adapter_sets_bounded_fresh_facts_only_after_exact_applied_enrichment() -> None:
    async def scenario() -> None:
        client = FakeClient(enriched={"name": "Мичуринский парк", "parking_price": "от 1,9 млн рублей", "infrastructure": ["паркинг"]})
        app = make_app(client=client)
        adapter = _OvermindSearchAdapter(app)
        state = ConversationState(visible_options=(OptionCard(name="Мичуринский парк", infrastructure=("паркинг",)),), selected_option_name="Мичуринский парк")
        plan = SemanticPlan(operation="select_option", selected_option_name="Мичуринский парк", requested_facts=("parking_price",), facts_needed=("parking_price",), requires_enrichment=True)

        enriched = await adapter.enrich_selected(state.visible_options[0], state, plan)

        assert enriched.parking_price == "от 1,9 млн рублей"
        assert adapter.last_fresh_facts == ("parking_price",)
        assert client.enrich_calls == 1
        assert client.ask_calls == 0

    asyncio.run(scenario())


def test_selected_adapter_does_not_publish_unproven_model_inventory_as_fresh() -> None:
    async def scenario() -> None:
        class InventoryClient(FakeClient):
            async def fetch_enriched_option(self, *_args: Any, **_kwargs: Any):
                self.enrich_calls += 1
                return {"name": "Мичуринский парк", "apartment_inventory": 5242}, {"ok": True, "_gateway_task_id": "task-2386206/unsafe suffix", "raw_mcp_text": "secret", "query": "secret"}

        client = InventoryClient(enriched={"name": "Мичуринский парк", "apartment_inventory": 5242})
        adapter = _OvermindSearchAdapter(make_app(client=client))
        base = OptionCard(name="Мичуринский парк")
        state = ConversationState(visible_options=(base,), selected_option_name=base.name)
        plan = SemanticPlan(
            operation="answer_open_question",
            selected_option_name=base.name,
            requested_facts=("apartment_inventory",),
            facts_needed=("apartment_inventory",),
            requires_enrichment=True,
        )

        enriched = await adapter.enrich_selected(base, state, plan)

        assert enriched.apartment_inventory == 5242
        assert adapter.last_fresh_facts == ()
        assert adapter.last_enrichment_trace["fresh_facts"] == []
        assert adapter.last_enrichment_trace["availability_evidence"] == {
            "requested": True,
            "confirmation": "not_confirmed",
            "source": "gateway",
            "gateway_task_id": "task-2386206_unsafe_suffix",
        }

        safe_trace = runtime_adapter_mod._safe_enrichment_trace(adapter.last_enrichment_trace)
        assert safe_trace["availability_evidence"] == adapter.last_enrichment_trace["availability_evidence"]
        assert "secret" not in json.dumps(safe_trace, ensure_ascii=False)

    asyncio.run(scenario())


def test_selected_availability_request_maps_to_lot_examples_without_hiding_inventory_fact() -> None:
    assert runtime_adapter_mod._selected_enrichment_facts(("apartment_inventory",), "life") == ("apartment_inventory", "lot_examples")


def test_selected_adapter_confirms_inventory_from_active_lot_examples_only() -> None:
    async def scenario() -> None:
        class LotInventoryClient(FakeClient):
            def __init__(self) -> None:
                super().__init__(enriched={
                    "name": "Мичуринский парк",
                    "apartment_inventory": 5242,
                    "ads": [{"id": 6375479, "rooms": "1", "area": 32.8, "fullprice": 10_318_880, "status": 2}],
                })
                self.enrich_kwargs: list[dict[str, Any]] = []

            async def fetch_enriched_option(self, *_args: Any, **kwargs: Any):
                self.enrich_kwargs.append(kwargs)
                return await super().fetch_enriched_option(*_args, **kwargs)

        client = LotInventoryClient()
        adapter = _OvermindSearchAdapter(make_app(client=client))
        base = OptionCard(name="Мичуринский парк")
        state = ConversationState(visible_options=(base,), selected_option_name=base.name)
        plan = SemanticPlan(
            operation="answer_open_question",
            selected_option_name=base.name,
            requested_facts=("apartment_inventory",),
            facts_needed=("apartment_inventory",),
            requires_enrichment=True,
        )

        enriched = await adapter.enrich_selected(base, state, plan)

        assert client.enrich_kwargs[0]["facts_needed"] == ["apartment_inventory", "lot_examples"]
        assert enriched.apartment_inventory == 5242
        assert len(enriched.lot_examples) == 1
        assert adapter.last_fresh_facts == ("lot_examples", "apartment_inventory")
        assert adapter.last_enrichment_trace["availability_evidence"]["confirmation"] == "confirmed"

    asyncio.run(scenario())


def test_selected_adapter_keeps_inventory_not_confirmed_without_valid_active_lots() -> None:
    async def scenario(enriched_raw: dict[str, Any]) -> tuple[OptionCard, tuple[str, ...], dict[str, Any]]:
        client = FakeClient(enriched=enriched_raw)
        adapter = _OvermindSearchAdapter(make_app(client=client))
        base = OptionCard(name="Мичуринский парк")
        state = ConversationState(visible_options=(base,), selected_option_name=base.name)
        plan = SemanticPlan(
            operation="answer_open_question",
            selected_option_name=base.name,
            requested_facts=("apartment_inventory",),
            facts_needed=("apartment_inventory",),
            requires_enrichment=True,
        )
        enriched = await adapter.enrich_selected(base, state, plan)
        return enriched, adapter.last_fresh_facts, adapter.last_enrichment_trace["availability_evidence"]

    no_lots, no_lots_fresh, no_lots_evidence = asyncio.run(scenario({"name": "Мичуринский парк", "apartment_inventory": 5242}))
    invalid_status, invalid_status_fresh, invalid_status_evidence = asyncio.run(scenario({
        "name": "Мичуринский парк",
        "apartment_inventory": 5242,
        "ads": [{"id": 6375479, "rooms": "1", "area": 32.8, "fullprice": 10_318_880, "status": 1}],
    }))

    assert no_lots.apartment_inventory == 5242
    assert no_lots_fresh == ()
    assert no_lots_evidence["confirmation"] == "not_confirmed"
    assert len(invalid_status.lot_examples) == 1
    assert invalid_status_fresh == ("lot_examples",)
    assert invalid_status_evidence["confirmation"] == "not_confirmed"


def test_selected_adapter_state_cache_does_not_confirm_inventory_without_valid_lots() -> None:
    async def scenario() -> None:
        base = OptionCard(name="Мичуринский парк")
        cached_card = OptionCard.from_dict({
            "name": base.name,
            "apartment_inventory": 5242,
            "lot_examples": [{"id": 6375479, "status": 1, "full_price": 10_318_880}],
        })
        entry = EnrichedCardCacheEntry(
            identity=enriched_card_identity(base),
            name=base.name,
            card=cached_card,
            scenario="self_use",
            loaded_facts=("apartment_inventory", "lot_examples"),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        client = FakeClient(enriched={"name": base.name, "apartment_inventory": 999})
        adapter = _OvermindSearchAdapter(make_app(client=client))
        state = ConversationState(visible_options=(base,), selected_option_name=base.name, enriched_card_cache=(entry,))
        plan = SemanticPlan(
            operation="answer_open_question",
            selected_option_name=base.name,
            requested_facts=("apartment_inventory",),
            facts_needed=("apartment_inventory",),
            requires_enrichment=True,
        )

        enriched = await adapter.enrich_selected(base, state, plan)

        assert client.enrich_calls == 0
        assert enriched.apartment_inventory == 5242
        assert adapter.last_fresh_facts == ("lot_examples",)
        assert adapter.last_enrichment_trace["availability_evidence"]["confirmation"] == "not_confirmed"

    asyncio.run(scenario())


def test_selected_rental_adapter_requests_and_preserves_lot_examples() -> None:
    async def scenario() -> None:
        class LotClient(FakeClient):
            def __init__(self) -> None:
                super().__init__(enriched={
                    "name": "Томилинский бульвар",
                    "location": "другая локация",
                    "min_price": 99_000_000,
                    "ads": [
                        {"id": 6375479, "rooms": "s", "area": 19, "floor": 6, "floors_total": 25, "fullprice": 8_133_900, "renovation": "с отделкой", "status": 2},
                        {"id": 5976219, "rooms": "1", "area": 32.8, "floor": 17, "floors_total": 25, "fullprice": 10_318_880, "renovation": "с отделкой", "status": 2},
                    ],
                    "house": [{"id": 5, "name": "5-8"}],
                })
                self.enrich_kwargs: list[dict[str, Any]] = []

            async def fetch_enriched_option(self, *_args: Any, **kwargs: Any):
                self.enrich_kwargs.append(kwargs)
                return await super().fetch_enriched_option(*_args, **kwargs)

        client = LotClient()
        app = make_app(client=client)
        adapter = _OvermindSearchAdapter(app)
        base = OptionCard(name="Томилинский бульвар", location="Томилино", price_min=7_500_000)
        state = ConversationState(visible_options=(base,), selected_option_name=base.name, active_topic="rental")
        plan = SemanticPlan(operation="select_option", intent="rental", selected_option_name=base.name)

        enriched = await adapter.enrich_selected(base, state, plan)

        assert client.enrich_kwargs[0]["facts_needed"] == ["lot_examples"]
        assert enriched.name == base.name
        assert enriched.location == "Томилино"
        assert enriched.price_min == 7_500_000
        assert len(enriched.lot_examples) == 2
        assert enriched.lot_examples[0].rooms == "студия"
        assert enriched.lot_examples[0].house_name is None

    asyncio.run(scenario())


def test_selected_lot_hard_room_scope_does_not_reuse_filtered_state_cache() -> None:
    async def scenario() -> None:
        class RoomScopedClient(FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.enrich_kwargs: list[dict[str, Any]] = []

            async def fetch_enriched_option(self, *_args: Any, state: Any, scenario: str, facts_needed: list[str], lot_hard: dict[str, Any]):
                self.enrich_calls += 1
                self.enrich_kwargs.append({"state": state, "scenario": scenario, "facts_needed": facts_needed, "lot_hard": lot_hard})
                rooms = str(lot_hard.get("rooms") or "")
                return {
                    "name": "Мичуринский парк",
                    "apartment_inventory": 1,
                    "ads": [{"id": 6375479 + self.enrich_calls, "rooms": rooms, "area": 40, "fullprice": 12_000_000, "status": 2}],
                }, {"ok": True}

        client = RoomScopedClient()
        adapter = _OvermindSearchAdapter(make_app(client=client))
        base = OptionCard(name="Мичуринский парк")
        state = ConversationState(visible_options=(base,), selected_option_name=base.name)
        two_room_plan = SemanticPlan(
            operation="answer_open_question",
            selected_option_name=base.name,
            requested_facts=("apartment_inventory",),
            facts_needed=("apartment_inventory",),
            constraints_delta={"hard": {"rooms": 2}},
            requires_enrichment=True,
        )
        first = await adapter.enrich_selected(base, state, two_room_plan)
        first_entry = adapter.last_enriched_cache_entry
        assert first_entry is not None

        one_room_plan = SemanticPlan(
            operation="answer_open_question",
            selected_option_name=base.name,
            requested_facts=("apartment_inventory",),
            facts_needed=("apartment_inventory",),
            constraints_delta={"hard": {"rooms": 1}},
            requires_enrichment=True,
        )
        state_with_two_room_cache = ConversationState(visible_options=(base,), selected_option_name=base.name, enriched_card_cache=(first_entry,))
        second = await adapter.enrich_selected(base, state_with_two_room_cache, one_room_plan)

        assert client.enrich_calls == 2
        assert client.enrich_kwargs[0]["lot_hard"] == {"rooms": 2}
        assert client.enrich_kwargs[1]["lot_hard"] == {"rooms": 1}
        assert first.lot_examples[0].rooms == "2"
        assert second.lot_examples[0].rooms == "1"
        assert adapter.last_enrichment_trace["items"][0]["source"] == "fetch"

    asyncio.run(scenario())


def test_selected_lot_hard_with_legacy_fetch_signature_uses_low_level_request_path() -> None:
    async def scenario() -> None:
        class LegacyFetchPlusGatewayClient(FakeClient):
            async def fetch_enriched_option(self, *_args: Any, state: Any, scenario: str, facts_needed: list[str]):
                self.enrich_calls += 1
                return {"name": "Мичуринский парк", "apartment_inventory": 999}, {"ok": True}

        client = LegacyFetchPlusGatewayClient(options=[{
            "name": "Мичуринский парк",
            "apartment_inventory": 1,
            "ads": [{"id": 6375481, "rooms": "1", "area": 40, "fullprice": 12_000_000, "status": 2}],
        }])
        adapter = _OvermindSearchAdapter(make_app(client=client))
        base = OptionCard(name="Мичуринский парк")
        state = ConversationState(visible_options=(base,), selected_option_name=base.name)
        plan = SemanticPlan(
            operation="answer_open_question",
            selected_option_name=base.name,
            requested_facts=("apartment_inventory",),
            facts_needed=("apartment_inventory",),
            constraints_delta={"hard": {"rooms": 1}},
            requires_enrichment=True,
        )

        enriched = await adapter.enrich_selected(base, state, plan)
        request_query = client.gateway_payloads[0]["query"]
        envelope = json.loads(request_query.split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0])

        assert client.enrich_calls == 0
        assert client.gateway_calls == 1
        assert envelope["lot_hard"] == {"rooms": 1}
        assert envelope["lot_hard_evidence_requirements"] == {"rooms": ["ads.rooms"]}
        assert '"lot_hard": {"rooms": 1}' in request_query
        assert enriched.lot_examples[0].rooms == "1"
        assert adapter.last_enrichment_trace["items"][0]["source"] == "v2_low_level"

    asyncio.run(scenario())


def test_selected_adapter_identity_mismatch_retains_base_and_no_fresh_facts() -> None:
    async def scenario() -> None:
        client = FakeClient(enriched={"name": "Другой парк", "parking_price": "от 1,9 млн рублей"})
        app = make_app(client=client)
        adapter = _OvermindSearchAdapter(app)
        state = ConversationState(visible_options=(OptionCard(name="Мичуринский парк", parking_price="от 1,8 млн рублей"),), selected_option_name="Мичуринский парк")
        plan = SemanticPlan(operation="select_option", selected_option_name="Мичуринский парк", requested_facts=("parking_price",), facts_needed=("parking_price",), requires_enrichment=True)

        enriched = await adapter.enrich_selected(state.visible_options[0], state, plan)

        assert enriched.parking_price == "от 1,8 млн рублей"
        assert adapter.last_fresh_facts == ()
        assert client.enrich_calls == 1

    asyncio.run(scenario())


def test_selected_adapter_default_timeout_allows_lookup_longer_than_old_point_eight_seconds(monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.delenv("NMBOT_V2_ENRICHMENT_ITEM_TIMEOUT", raising=False)

        class SlowClient(FakeClient):
            async def fetch_enriched_option(self, *_args: Any, **_kwargs: Any):
                self.enrich_calls += 1
                await asyncio.sleep(0.85)
                return {"name": "Мичуринский парк", "parking_price": "от 1,9 млн рублей", "infrastructure": ["паркинг"]}, {"ok": True}

        client = SlowClient()
        adapter = _OvermindSearchAdapter(make_app(client=client))
        state = ConversationState(visible_options=(OptionCard(name="Мичуринский парк"),), selected_option_name="Мичуринский парк")
        plan = SemanticPlan(operation="select_option", selected_option_name="Мичуринский парк", requested_facts=("parking_price",), facts_needed=("parking_price",), requires_enrichment=True)

        enriched = await adapter.enrich_selected(state.visible_options[0], state, plan)

        assert enriched.parking_price == "от 1,9 млн рублей"
        assert adapter.last_enrichment_error_code is None
        assert adapter.last_enrichment_trace["outcome"] == "applied"
        assert adapter.last_enrichment_trace["requested_facts"] == ["parking_price"]
        assert adapter.last_enrichment_trace["fresh_facts"] == ["parking_price"]

    asyncio.run(scenario())


def test_selected_adapter_timeout_is_technical_recovery_not_confirmed_missing(monkeypatch) -> None:
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        monkeypatch.setenv("NMBOT_V2_ENRICHMENT_ITEM_TIMEOUT", "0.2")
        initial = mod._default_state()
        initial["visible_options"] = [{"name": "Левел Павелецкая Сити"}]
        patch_planner(monkeypatch, {
            "operation": "select_option",
            "selected_reference": "Левел Павелецкая Сити",
            "requested_facts": ["parking"],
            "facts_needed": ["parking"],
            "resolved_subject": "parking",
            "requires_enrichment": True,
            "confidence": 1.0,
        })

        class TimeoutClient(FakeClient):
            async def fetch_enriched_option(self, *_args: Any, **_kwargs: Any):
                self.enrich_calls += 1
                await asyncio.sleep(0.3)
                return {"name": "Левел Павелецкая Сити", "infrastructure": ["паркинг"]}, {"ok": True}

        app = make_app(initial, client=TimeoutClient())
        result = await mod.run_chat(app, user_id="u", message="можно купить парковочное место?", channel="jivo", meta={})

        assert result["ok"] is True
        assert "Сейчас не могу надёжно проверить по паркингу" in result["answer"]
        assert result["answer"].endswith("Передать оператору запрос?")
        assert app["state_store"].states["u"]["nmbot_v2"].get("pending_followup") == "selected_live_fact_consent"
        trace = result["meta"]["trace"]["option_enrichment"]
        assert trace["outcome"] == "timeout"
        assert trace["requested_facts"] == ["parking"]
        assert trace["fresh_facts"] == []

    asyncio.run(scenario())


def test_selected_missing_parking_first_consent_opens_contact_without_second_lookup(monkeypatch) -> None:
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        plans = iter((
            {
                "operation": "select_option",
                "selected_reference": "Левел Павелецкая Сити",
                "requested_facts": ["parking"],
                "facts_needed": ["parking"],
                "resolved_subject": "parking",
                "requires_enrichment": True,
                "confidence": 1.0,
            },
            {"operation": "freeform", "followup_outcome": "accept", "confidence": 1.0},
        ))

        async def fake_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return next(plans)

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)
        initial = mod._default_state()
        initial["visible_options"] = [{"name": "Левел Павелецкая Сити"}]
        client = FakeClient(enriched={"name": "Левел Павелецкая Сити", "developer": "Level Group"})
        app = make_app(initial, client=client)

        missing = await mod.run_chat(app, user_id="u", message="можно купить парковочное место?", channel="jivo", meta={})
        assert "Передать оператору запрос по паркингу для ЖК «Левел Павелецкая Сити»?" in missing["answer"]
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "selected_live_fact_consent"
        assert client.enrich_calls == 1

        accepted = await mod.run_chat(app, user_id="u", message="да", channel="jivo", meta={})
        assert accepted["answer"].endswith("На какой номер вам удобно позвонить?")
        assert accepted["intent"] == "collect_contact_phone"
        assert accepted["awaiting_phone"] is True
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "contact_phone"
        assert client.enrich_calls == 1

    asyncio.run(scenario())


def test_v2_selected_without_low_level_enrichment_keeps_base_card_and_skips_v1_helper(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["visible_options"] = [{"name": "Лучи", "price_range": "от 12 млн рублей"}]
        patch_planner(monkeypatch, {"operation": "select_option", "selected_option_name": "Лучи", "confidence": 1.0})

        class ClientWithoutEnrichment:
            async def ensure_session(self):
                return object()

        app = make_app(initial)
        app["overmind_client"] = ClientWithoutEnrichment()

        async def forbidden(*_args: Any, **_kwargs: Any):
            raise AssertionError("V2 must not call the V1 enrichment helper")

        monkeypatch.setattr(mod, "_get_or_fetch_enriched_option", forbidden)
        result = await mod.run_chat(app, user_id="u", message="Лучи", channel="jivo", meta={})

        assert result["ok"] is True
        assert result["selected_option"] == "Лучи"
        assert "12 млн" in result["answer"]

    asyncio.run(scenario())


def test_v2_exact_visible_name_uses_planner_canonical_selection(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["visible_options"] = [{"name": "Полар", "price_range": "от 12 млн рублей", "location": "Москва"}]
        patch_planner(monkeypatch, {
            "selected_reference": "Полар",
            "refers_to_existing_objects": True,
            "requests_new_objects": False,
            "confidence": 1.0,
        })
        client = FakeClient(enriched={"name": "Полар", "price_range": "от 12 млн рублей", "developer": "ПИК"})
        app = make_app(initial, client=client)

        result = await mod.run_chat(app, user_id="u", message="Полар", channel="jivo", meta={})

        assert result["ok"] is True
        assert client.ask_calls == 0
        assert client.enrich_calls == 1
        assert app["state_store"].states["u"]["nmbot_v2"]["selected_option_name"] == "Полар"
        assert "застройщик ПИК" in result["answer"]

    asyncio.run(scenario())


def test_v2_clean_ordinal_uses_planner_canonical_selection(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["visible_options"] = [
            {"name": "Первый дом", "price_range": "от 12 млн рублей", "location": "Москва"},
            {"name": "Второй дом", "price_range": "от 13 млн рублей", "location": "Москва"},
        ]
        patch_planner(monkeypatch, {
            "selected_reference": "1",
            "refers_to_existing_objects": True,
            "requests_new_objects": False,
            "confidence": 1.0,
        })
        client = FakeClient(enriched={"name": "Первый дом", "price_range": "от 12 млн рублей", "developer": "ПИК"})
        app = make_app(initial, client=client)

        result = await mod.run_chat(app, user_id="u", message="первый вариант", channel="jivo", meta={})

        assert result["ok"] is True
        assert client.ask_calls == 0
        assert client.enrich_calls == 1
        assert app["state_store"].states["u"]["nmbot_v2"]["selected_option_name"] == "Первый дом"
        assert "Первый дом" in result["answer"]

    asyncio.run(scenario())


def test_v2_semantic_planner_gets_current_context_and_writes_raw_trace(monkeypatch, tmp_path):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["visible_options"] = [
            {"name": "Бусиновский парк", "price_range": "от 12 млн рублей", "location": "Москва"},
            {"name": "Мичуринский парк", "price_range": "от 14 млн рублей", "location": "Москва"},
        ]
        initial["nmbot_v2"] = {
            "visible_options": [
                {"name": "Бусиновский парк", "price": "от 12 млн рублей", "location": "Москва"},
                {"name": "Мичуринский парк", "price": "от 14 млн рублей", "location": "Москва"},
            ],
            "selected_option_name": "Мичуринский парк",
            "selected_enriched": {"name": "Мичуринский парк", "infrastructure": ["паркинг"]},
            "dialog_focus": {"subject": "parking", "last_requested_facts": ["parking"], "last_answered_facts": ["parking"]},
            "recent_turns": [{
                "user": "квартира для жизни в Москве до 40 млн",
                "assistant": "Показала Бусиновский парк и Мичуринский парк. Какой ЖК посмотреть подробнее?",
            }],
        }
        planner_calls: list[dict[str, Any]] = []

        async def fake_plan(*_args: Any, **kwargs: Any) -> dict[str, Any]:
            planner_calls.append(kwargs)
            return {
                "operation": "select_option",
                "reference": "Мичуринский парк",
                "confidence": 1.0,
                "planner_raw_response": '{"selected_reference":"Мичуринский парк"}',
            }

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)
        monkeypatch.setenv("NMBOT_PLANNER_TRACE_FILE", str(tmp_path / "planner_trace.jsonl"))
        app = make_app(initial, client=FakeClient(enriched={"name": "Мичуринский парк", "price_range": "от 14 млн рублей"}))
        result = await mod.run_chat(app, user_id="u", message="мичурниский парк", channel="jivo", meta={})

        assert "Мичуринский парк" in planner_calls[0]["visible_response_text"]
        assert planner_calls[0]["last_turn"]["bot_question"] == "Показала Бусиновский парк и Мичуринский парк. Какой ЖК посмотреть подробнее?"
        assert planner_calls[0]["last_turn"]["client_answer"] == "мичурниский парк"
        assert planner_calls[0]["last_response_text"] == "Показала Бусиновский парк и Мичуринский парк. Какой ЖК посмотреть подробнее?"
        assert planner_calls[0]["selected_object"] == {"canonical_name": "Мичуринский парк", "present_fact_fields": ["parking", "parks"]}
        assert planner_calls[0]["dialog_focus"]["subject"] == "parking"
        assert "parking_price" in planner_calls[0]["allowed_facts"]
        assert planner_calls[0]["subject_fact_map"]["parking"] == ["parking", "parking_price", "parking_inventory"]
        assert result["selected_option"] == "Мичуринский парк"
        assert "Мичуринский парк" in result["answer"]
        trace = json.loads((tmp_path / "planner_trace.jsonl").read_text(encoding="utf-8"))
        assert trace["user_text"] == "мичурниский парк"
        assert trace["planner_raw_response"] == '{"selected_reference":"Мичуринский парк"}'

    asyncio.run(scenario())


def test_selected_financing_followup_inherits_selected_scope():
    state = ConversationState(
        active_topic="life",
        visible_options=(
            OptionCard(name="Бусиновский парк", location="Западное Дегунино", price_min=12_400_000),
            OptionCard(name="Мичуринский парк", location="Очаково-Матвеевское", price_min=14_300_000),
        ),
        selected_option_name="Бусиновский парк",
        selected_enriched=OptionCard(name="Бусиновский парк", location="Западное Дегунино", price_min=12_400_000),
    )
    plan = SemanticPlan(operation="current_options", intent="mortgage", scope="unknown", facets=["mortgage"])

    inherited = _inherit_selected_scope(plan, state)

    assert inherited.operation == "financing"
    assert inherited.scope == "one"
    assert inherited.selected_option_name == "Бусиновский парк"
    assert inherited.intent == "mortgage"
    assert inherited.operator_reason is None


def test_v2_financing_pending_scenario_payload_is_bounded_and_maps_outcome(monkeypatch):
    async def scenario() -> None:
        captured: list[dict[str, Any]] = []

        async def fake_plan(*_args: Any, **kwargs: Any) -> dict[str, Any]:
            captured.append(kwargs)
            return {"operation": "freeform", "followup_outcome": "accept", "scope": "unknown", "confidence": 1.0}

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)
        state = ConversationState(
            pending_followup="financing_consent",
            selected_option_name="Лучи",
            visible_options=(OptionCard(name="Лучи", price="от 12 млн рублей"), OptionCard(name="Секретный телефон +7 999 123-45-67")),
        )
        semantic = await _SemanticPlannerAdapter(make_app()).plan(SafeTurnContext(conversation_ref="u", user_text="да"), state)

        envelope = captured[0]["pending_scenario"]
        assert envelope == {
            "id": "financing_consent",
            "allowed_reply_outcomes": ["accept", "decline", "ask_or_clarify", "unexpected"],
            "context": {
                "scope": "one",
                "offered_action": "verify_financing_conditions",
                "selected_option_name": "Лучи",
            },
        }
        dumped = json.dumps(envelope, ensure_ascii=False)
        assert "12 млн" not in dumped
        assert "+7" not in dumped
        assert "Как к вам обращаться" not in dumped
        assert semantic.followup_outcome == "accept"
        assert semantic.scope == "one"
        assert semantic.selected_option_name == "Лучи"

    asyncio.run(scenario())


def test_v2_canonical_plan_bypasses_second_semantic_normalization(monkeypatch):
    async def scenario() -> None:
        monkeypatch.delenv("NMBOT_INTENT_PLAN_VERSION", raising=False)

        async def fake_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "action": "operator_contact",
                "dialog_action": "operator_live_check",
                "target": "operator",
                "search_policy": "forbidden",
                "operator_contact": {"requested": True, "consent": "granted"},
                "canonical_valid": True,
                "canonical_errors": [],
                "confidence": 1.0,
            }

        def unexpected_normalization(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("canonical plan was semantically normalized twice")

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)
        monkeypatch.setattr(runtime_adapter_mod, "normalize_semantic_planner_result", unexpected_normalization)
        monkeypatch.setattr(runtime_adapter_mod, "derive_runtime_decision", unexpected_normalization)

        adapter = _SemanticPlannerAdapter(make_app())
        plan = await adapter.plan(
            SafeTurnContext(conversation_ref="u", user_text="давайте"),
            ConversationState(pending_followup="contact_name", operator_offered=True),
        )

        assert plan.operation == "operator"
        assert plan.operator_consent is True
        assert adapter.last_planner_plan["semantic_adapter_route"] == "canonical_direct"

    asyncio.run(scenario())


def test_v2_explicit_new_objects_becomes_ephemeral_fresh_search(monkeypatch):
    async def scenario() -> None:
        async def fake_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "requests_new_objects": True,
                "refers_to_existing_objects": False,
                "intent": "family",
                "confidence": 1.0,
            }

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)
        state = ConversationState(
            params={"rooms": 2, "purpose": "family"},
            visible_options=(OptionCard(name="Бусиновский парк"),),
        )

        semantic = await _SemanticPlannerAdapter(make_app()).plan(
            SafeTurnContext(conversation_ref="u", user_text="покажи другие, этот не повторяй"),
            state,
        )

        assert semantic.operation == "search"
        assert semantic.fresh_search is True

    asyncio.run(scenario())


def test_financing_pending_scenario_all_scope_has_exact_allowlist_and_no_card_text():
    state = ConversationState(pending_followup="financing_consent", visible_options=(OptionCard(name="Лучи", price="от 12 млн"),))

    envelope = _pending_scenario_for_planner(state)

    assert envelope == {
        "id": "financing_consent",
        "allowed_reply_outcomes": ["accept", "decline", "ask_or_clarify", "unexpected"],
        "context": {"scope": "all", "offered_action": "verify_financing_conditions"},
    }
    assert "Лучи" not in json.dumps(envelope, ensure_ascii=False)


def test_v2_current_options_uses_deterministic_renderer_without_broad_search(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["params"] = {"primary_intent": "investment"}
        initial["visible_options"] = [{"name": "Лучи", "price_range": "от 12 млн рублей", "location": "Москва"}]
        patch_planner(monkeypatch, {"operation": "current_options", "intent": "mortgage", "facets": ["mortgage"], "scope": "all", "confidence": 1.0})
        client = FakeClient()
        app = make_app(initial, client=client)

        result = await mod.run_chat(app, user_id="u", message="а семейная ипотека есть?", channel="jivo", meta={})

        assert result["ok"] is True
        assert client.ask_calls == 0
        assert client.explain_calls == 0
        assert client.composer_calls == 0
        assert "Лучи" in result["answer"]
        assert result["meta"]["trace"]["response_composer"] == {
            "composer_used": False,
            "fallback_reason": "deterministic_renderer",
            "validation_stage": None,
            "validation_codes": [],
            "attempts": None,
        }
        assert "V2_RESPONSE_BRIEF" not in json.dumps(result["meta"], ensure_ascii=False)

    asyncio.run(scenario())


def test_v2_response_composer_trace_marker_is_bounded() -> None:
    assert _safe_response_composer_trace({"used": False, "reason": "validation_failed", "error_category": "semantic", "errors": ["schema_invalid_options", "required_price_missing", "raw model output"], "attempts": 2, "prompt": "secret"}) == {
        "composer_used": False,
        "fallback_reason": "validation_failed",
        "validation_stage": "semantic",
        "validation_codes": ["schema_invalid_options", "required_price_missing"],
        "attempts": 2,
    }
    assert _safe_response_composer_trace({"used": False, "reason": "customer phone +7 999 123-45-67"}) == {
        "composer_used": False,
        "fallback_reason": "other",
        "validation_stage": None,
        "validation_codes": [],
        "attempts": None,
    }


def test_v2_response_composer_trace_allows_only_safe_attempt_diagnostic() -> None:
    trace = _safe_response_composer_trace(
        {
            "used": False,
            "reason": "validation_failed",
            "error_category": "semantic",
            "errors": ["invalid_json"],
            "attempts": 1,
            "attempt_summaries": [
                {
                    "raw_type": "string",
                    "raw_length": 123,
                    "starts_object": True,
                    "starts_fence": False,
                    "ends_object": False,
                    "gateway_task_id": "task-2386206",
                    "raw_text": "secret model text",
                    "query": "secret query",
                }
            ],
            "semantic_diagnostics": [
                {"stage": "formatter", "categories": ["numeric_not_in_canonical", "sensitive_claim", "raw secret"], "matched_text": "99 млн", "position": 12},
                {"stage": "raw-stage", "categories": ["numeric_not_in_canonical"]},
            ],
            "prompt": "secret prompt",
        }
    )

    assert trace["attempt_diagnostic"] == {
        "raw_type": "string",
        "raw_length": 123,
        "starts_object": True,
        "starts_fence": False,
        "ends_object": False,
    }
    assert trace["semantic_diagnostics"] == [
        {"stage": "formatter", "categories": ["numeric_not_in_canonical", "sensitive_claim"]}
    ]
    dumped = json.dumps(trace, ensure_ascii=False)
    for forbidden in ("secret", "raw_text", "99", "position", "gateway_task_id"):
        assert forbidden not in dumped


def test_v3_shadow_response_composer_routes_writer_formatter_gateway_calls(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v3")
        monkeypatch.setenv("NMBOT_V3_RESPONSE_COMPOSER_MODE", "shadow")
        initial = mod._default_state()
        initial["runtime_version_override"] = "V3"
        patch_planner(monkeypatch, {"operation": "search", "intent": "life", "confidence": 1.0})
        client = FakeClient()
        app = make_app(initial, client=client)

        result = await mod.run_chat(app, user_id="u", message="найди", channel="jivo", meta={})

        composer_payloads = [item for item in client.gateway_payloads if str(item.get("_payload_stage") or "").startswith("conversation_answer")]
        composer_once_payloads = [item for item in client.gateway_once_payloads if str(item.get("_payload_stage") or "").startswith("conversation_answer")]
        assert result["ok"] is True
        assert client.composer_calls == 1
        assert len(composer_payloads) == 0
        assert [item.get("_payload_stage") for item in composer_once_payloads] == ["conversation_answer_writer"]
        assert client.gateway_once_calls == 1
        assert composer_once_payloads[0]["model"] == "google/gemini-2.5-flash"
        assert "repair_validation_errors" not in composer_once_payloads[0]["query"]
        assert "Лучи" in result["answer"]
        assert result["meta"]["trace"]["response_composer"]["mode"] == "shadow"
        assert result["meta"]["trace"]["response_composer"]["published"] is False
        assert result["meta"]["trace"]["response_composer"]["pipeline"] == "gemini_json_with_formatter_fallback"
        assert result["meta"]["trace"]["response_composer"]["attempts"] == 1
        assert "V2_RESPONSE_BRIEF" not in json.dumps(result["meta"], ensure_ascii=False)

    asyncio.run(scenario())


def test_v3_publish_response_composer_publishes_model_text_and_v2_ignores_env(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setenv("NMBOT_V3_RESPONSE_COMPOSER_MODE", "publish")
        patch_planner(monkeypatch, {"operation": "search", "intent": "life", "confidence": 1.0})
        v3_client = FakeClient()
        v3_initial = mod._default_state()
        v3_initial["runtime_version_override"] = "V3"
        v3_app = make_app(v3_initial, client=v3_client)
        env(monkeypatch, version="v3")
        v3 = await mod.run_chat(v3_app, user_id="u", message="найди", channel="jivo", meta={})

        v2_client = FakeClient()
        v2_initial = mod._default_state()
        v2_initial["runtime_version_override"] = "V2"
        v2_app = make_app(v2_initial, client=v2_client)
        env(monkeypatch, version="v2")
        v2 = await mod.run_chat(v2_app, user_id="u", message="найди", channel="jivo", meta={})

        assert v3_client.composer_calls == 1
        assert [item.get("_payload_stage") for item in v3_client.gateway_payloads].count("conversation_answer_writer") == 0
        assert [item.get("_payload_stage") for item in v3_client.gateway_once_payloads].count("conversation_answer_writer") == 1
        assert [item.get("_payload_stage") for item in v3_client.gateway_once_payloads].count("conversation_answer_formatter") <= 1
        assert v3["meta"]["trace"]["response_composer"]["published"] is True
        assert "Эти факты помогают спокойно сравнить" in v3["answer"]
        assert v3_app["state_store"].states["u"]["nmbot_v2"]["recent_turns"][-1]["assistant"] == v3["answer"]
        assert v2_client.composer_calls == 0
        assert v2["meta"]["trace"]["response_composer"]["composer_used"] is False
        assert v2["meta"]["runtime"] == "v2"

    asyncio.run(scenario())


def test_v3_publish_response_composer_provider_error_does_not_retry_model(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setenv("NMBOT_V3_RESPONSE_COMPOSER_MODE", "publish")
        patch_planner(monkeypatch, {"operation": "search", "intent": "life", "confidence": 1.0})
        initial = mod._default_state()
        initial["runtime_version_override"] = "V3"
        client = FakeClient(composer_provider_error=True)
        app = make_app(initial, client=client)

        result = await mod.run_chat(app, user_id="u", message="найди", channel="jivo", meta={})

        assert result["ok"] is True
        assert client.composer_calls == 1
        assert [item.get("_payload_stage") for item in client.gateway_payloads].count("conversation_answer_writer") == 0
        assert [item.get("_payload_stage") for item in client.gateway_once_payloads].count("conversation_answer_writer") == 1
        assert [item.get("_payload_stage") for item in client.gateway_once_payloads].count("conversation_answer_formatter") == 0
        assert result["meta"]["trace"]["response_composer"]["published"] is False
        assert result["meta"]["trace"]["response_composer"]["validation_stage"] == "provider"
        assert result["meta"]["trace"]["response_composer"]["validation_codes"] == ["provider_invalid_argument"]
        assert "Лучи" in result["answer"]

    asyncio.run(scenario())


def test_v2_explicit_operator_request_opens_explicit_name_capture(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        patch_planner(monkeypatch, {"operation": "operator", "explicit_operator_request": True, "confidence": 1.0})
        app = make_app(initial)

        result = await mod.run_chat(app, user_id="u", message="позови оператора", channel="jivo", meta={"sender_name": "Иван"})

        assert result["ok"] is True
        assert result["intent"] == "operator_offer"
        assert result["awaiting_phone"] is False
        assert "Как к вам обращаться?" in result["answer"]
        assert app["state_store"].states["u"]["nmbot_v2"]["operator_offered"] is True
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "contact_name"
        assert app["state_store"].states["u"].get("contact_flow") in (None, "normal")

    asyncio.run(scenario())


def test_v2_missing_open_question_waits_for_operator_consent_before_callback_flow(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["nmbot_v2"] = {
            "visible_options": [{"name": "Лучи", "price": "от 12 млн рублей"}],
        }
        first_plan = {
            "operation": "answer_open_question",
            "requested_facts": ["apartment_inventory"],
            "facts_needed": ["apartment_inventory"],
            "resolved_subject": "apartment",
            "confidence": 1.0,
        }
        second_plan = {"operation": "freeform", "followup_outcome": "accept", "confidence": 1.0}
        plans = [first_plan, second_plan]

        async def fake_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return plans.pop(0) if plans else second_plan

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)
        client = FakeClient()
        app = make_app(initial, client=client)
        app["crm_callback_outbox"].clear_contact_draft(session_key="u")

        first = await mod.run_chat(app, user_id="u", message="Есть свободные квартиры?", channel="jivo", meta={})

        assert first["ok"] is True
        assert first["intent"] == "answer_open_question"
        assert client.gateway_calls == 0
        assert first["answer"].rstrip().endswith("В текущих данных это не подтверждено. Оператор сможет проверить. Передать оператору запрос?")
        assert "телефон" not in first["answer"].casefold()
        assert "номер" not in first["answer"].casefold()
        assert "застройщик" not in first["answer"].casefold()
        assert "сайт" not in first["answer"].casefold()
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "selected_live_fact_consent"
        assert app["state_store"].states["u"]["nmbot_v2"]["contact_consent"] is False

        second = await mod.run_chat(
            app,
            user_id="u",
            message="да",
            channel="jivo",
            meta={"sender_name": "Synthetic nmbot test client"},
        )

        assert second["intent"] == "collect_contact_phone"
        assert second["awaiting_phone"] is True
        assert second["answer"].rstrip().endswith("На какой номер вам удобно позвонить?")
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "contact_phone"
        assert app["state_store"].states["u"]["nmbot_v2"]["contact_consent"] is True

        third = await mod.run_chat(
            app,
            user_id="u",
            message="+7 999 123-45-67",
            channel="jivo",
            meta={"sender_name": "Synthetic nmbot test client"},
        )

        assert third["intent"] == "callback_queued"
        assert app["state_store"].states["u"]["nmbot_v2"].get("pending_followup") is None
        assert "9991234567" not in json.dumps(app["state_store"].states["u"], ensure_ascii=False)

    asyncio.run(scenario())


def test_unconfirmed_operator_offer_routes_affirmation_to_planner(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = {
            **mod._default_state(),
            "nmbot_v2": {"pending_followup": "contact_name", "operator_offered": True},
        }
        patch_planner(monkeypatch, {"operation": "operator", "operator_consent": True, "confidence": 1.0})
        app = make_app(initial)

        result = await mod.run_runtime_turn(app, user_id="u", message="давайте", channel="jivo", meta={})

        assert result["intent"] == "collect_contact_phone"
        assert result["answer"].rstrip().endswith("На какой номер вам удобно позвонить?")

    asyncio.run(scenario())


def test_v2_phone_input_never_delegates_to_legacy(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"operation": "search", "confidence": 1.0})
        app = make_app()
        calls: list[str] = []

        async def legacy(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("legacy")
            return {"ok": True, "answer": "V1 phone", "intent": "capture_contact"}

        result = await mod.run_runtime_turn(app, user_id="u", message="мой телефон +7 999 123-45-67", channel="jivo", meta={})

        assert calls == []
        assert result["meta"]["runtime"] == "v2"
        assert "V1" not in result["answer"]

    asyncio.run(scenario())


def test_v2_contact_capture_uses_v2_state_and_outbox_not_legacy(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        calls: list[str] = []

        async def legacy(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("legacy")
            return {"ok": True, "answer": "V1 contact", "intent": "collect_contact"}

        app = make_app({**mod._default_state(), "nmbot_v2": {"pending_followup": "contact_phone", "contact_name": "Иван"}})
        result = await mod.run_runtime_turn(app, user_id="u", message="мой номер +7 999 123-45-67", channel="jivo", meta={})
        assert calls == []
        assert result["intent"] == "callback_queued"
        assert app["overmind_client"].ask_calls == 0
        v2 = app["state_store"].states["u"]["nmbot_v2"]
        assert v2["contact_name"] == "Иван"
        assert v2["contact_consent"] is True
        assert "+7 999" not in str(v2)

    asyncio.run(scenario())


def test_v2_operator_consent_anonymous_name_advances_to_phone_then_queues_once(monkeypatch, tmp_path):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        plans = [
            {
                "operation": "answer_open_question",
                "requested_facts": ["apartment_inventory"],
                "facts_needed": ["apartment_inventory"],
                "resolved_subject": "apartment",
                "confidence": 1.0,
            },
            {"operation": "freeform", "followup_outcome": "accept", "confidence": 1.0},
        ]

        async def fake_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            if not plans:
                raise AssertionError("phone-first callback capture must not call planner")
            return plans.pop(0)

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)
        client = FakeClient()
        app = make_app({**mod._default_state(), "nmbot_v2": {"visible_options": [{"name": "Лучи"}]}}, client=client)
        app["crm_callback_outbox"] = mod.LocalCallbackOutbox(tmp_path / "outbox")

        await mod.run_chat(app, user_id="u", message="Есть свободные квартиры?", channel="jivo", meta={})
        consent = await mod.run_chat(app, user_id="u", message="пусть оператор перезвонит", channel="jivo", meta={})
        assert consent["intent"] == "collect_contact_phone"
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "contact_phone"
        assert app["state_store"].states["u"]["nmbot_v2"]["contact_consent"] is True

        queued = await mod.run_chat(app, user_id="u", message="+7 999 123-45-67", channel="jivo", meta={"event_id": "anon-v2", "sender_name": "Synthetic nmbot test client"})
        assert queued["intent"] == "callback_queued"
        assert queued["crm_callback"]["status"] == "queued"
        records = _callback_records(tmp_path / "outbox")
        assert len(records) == 1
        assert records[0]["contact"]["name"] == "Без имени"
        saved = app["state_store"].states["u"]["nmbot_v2"]
        assert saved.get("pending_followup") is None
        assert saved["contact_name"] == "Без имени"
        assert "9991234567" not in json.dumps(saved, ensure_ascii=False)

    asyncio.run(scenario())


def test_v3_legacy_name_capture_uses_shared_contact_runtime(monkeypatch, tmp_path):
    async def scenario() -> None:
        env(monkeypatch, version="v3")
        monkeypatch.setenv("NMBOT_V3_RESPONSE_COMPOSER_MODE", "publish")
        app = make_app({**mod._default_state(), "runtime_version_override": "V3", "nmbot_v2": {"pending_followup": "contact_name", "contact_consent": True}})
        app["crm_callback_outbox"] = mod.LocalCallbackOutbox(tmp_path / "outbox")

        async def fail_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("shared legacy name capture must not call planner")

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fail_plan)
        result = await mod.run_runtime_turn(app, user_id="u", message="Анна", channel="jivo", meta={})

        assert result["intent"] == "collect_contact_phone"
        assert result["meta"]["runtime"] == "v3"
        assert "Анна" in result["answer"]
        v2 = app["state_store"].states["u"]["nmbot_v2"]
        assert v2["contact_name"] == "Анна"
        assert v2["pending_followup"] == "contact_phone"

    asyncio.run(scenario())


def test_v2_proactive_phone_with_safe_profile_queues_without_planner(monkeypatch, tmp_path):
    async def scenario() -> None:
        env(monkeypatch, version="bad")
        app = make_app({"nmbot_v2": {}})
        app["crm_callback_outbox"] = mod.LocalCallbackOutbox(tmp_path / "outbox")

        async def fail_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("proactive contact capture must not call planner")

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fail_plan)
        result = await mod.run_runtime_turn(
            app,
            user_id="u",
            message="мой номер +7 999 123-45-67",
            channel="jivo",
            meta={"event_id": "evt", "sender_name": "Мария", "client_id": "raw-client"},
        )

        assert result["intent"] == "callback_queued"
        assert result["crm_callback"]["status"] == "queued"
        assert result["handoff_to_operator"] is False
        dumped_public = json.dumps(result, ensure_ascii=False)
        assert "+7 999" not in dumped_public and "9991234567" not in dumped_public
        saved = app["state_store"].states["u"]
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"].get("pending_followup") is None
        assert saved["nmbot_v2"].get("contact_consent") is True
        assert "+7 999" not in json.dumps(saved, ensure_ascii=False)
        assert app["overmind_client"].gateway_calls == 0

    asyncio.run(scenario())


def test_v2_proactive_phone_without_safe_profile_saves_private_draft_and_asks_name(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v1")
        app = make_app({"nmbot_v2": {}})

        async def fail_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("phone draft capture must not call planner")

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fail_plan)
        result = await mod.run_runtime_turn(
            app,
            user_id="u",
            message="+7 999 123-45-67",
            channel="jivo",
            meta={"event_id": "evt", "sender_name": "Synthetic nmbot test client"},
        )

        assert result["intent"] == "collect_contact_name"
        assert "Номер сохранила" in result["answer"]
        saved = app["state_store"].states["u"]
        assert saved["nmbot_v2"]["pending_followup"] == "contact_name"
        assert "9991234567" not in json.dumps(saved, ensure_ascii=False)
        assert app["crm_callback_outbox"].load_contact_draft_phone(session_key="u") == "+79991234567"
        assert app["overmind_client"].gateway_calls == 0

    asyncio.run(scenario())


def test_v2_phone_first_draft_queues_after_explicit_name_without_profile_name(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        app = make_app({**mod._default_state(), "nmbot_v2": {"pending_followup": "contact_name", "selected_option_name": "Лучи"}})
        app["crm_callback_outbox"].clear_contact_draft(session_key="u")

        first = await mod.run_runtime_turn(
            app, user_id="u", message="+7 999 123-45-67", channel="jivo", meta={"sender_name": "Synthetic nmbot test client"},
        )
        assert first["intent"] == "collect_contact_name"
        assert "Номер сохранила" in first["answer"]
        assert "999" not in str(app["state_store"].states["u"]["nmbot_v2"])

        queued = await mod.run_runtime_turn(
            app, user_id="u", message="Тест ЧАТИ", channel="jivo", meta={"sender_name": "Synthetic nmbot test client"},
        )
        assert queued["intent"] == "callback_queued"
        assert "Тест ЧАТИ" in queued["answer"]
        assert "Synthetic" not in queued["answer"]
        assert app["state_store"].states["u"]["nmbot_v2"]["contact_name"] == "Тест ЧАТИ"

    asyncio.run(scenario())


def test_v2_property_question_overrides_pending_contact_name_for_current_turn(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = {
            **mod._default_state(),
            "nmbot_v2": {
                "pending_followup": "contact_name",
                "selected_option_name": "Бусиновский парк",
                "visible_options": [
                    {"name": "Бусиновский парк"},
                    {"name": "Лосиноостровский парк", "infrastructure": ["паркинг"]},
                ],
            },
        }
        patch_planner(monkeypatch, {
            "operation": "select_option",
            "selected_reference": "Лосиноостровский парк",
            "requested_facts": ["parking"],
            "resolved_subject": "parking",
            "confidence": 1.0,
        })
        app = make_app(initial)

        result = await mod.run_chat(
            app,
            user_id="u",
            message="А в ЖК Лосиноостровский парк есть платная парковка?",
            channel="jivo",
            meta={},
        )

        assert result["intent"] != "collect_contact_phone"
        assert "паркинг" in result["answer"].casefold()
        v2 = app["state_store"].states["u"]["nmbot_v2"]
        assert v2.get("contact_name") is None
        assert v2["selected_option_name"] == "Лосиноостровский парк"
        assert v2["pending_followup"] == "contact_name"

        resumed = await mod.run_chat(
            app,
            user_id="u",
            message="Вернёмся к звонку, меня зовут Анна",
            channel="jivo",
            meta={},
        )

        assert resumed["intent"] == "collect_contact_phone"
        assert "Анна" in resumed["answer"]
        resumed_v2 = app["state_store"].states["u"]["nmbot_v2"]
        assert resumed_v2["contact_name"] == "Анна"
        assert resumed_v2["pending_followup"] == "contact_phone"

    asyncio.run(scenario())


def test_v2_multiscenario_mortgage_question_preserves_pending_contact_name(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = {
            **mod._default_state(),
            "nmbot_v2": {
                "pending_followup": "contact_name",
                "selected_option_name": "Томилинский бульвар",
                "visible_options": [{"name": "Томилинский бульвар", "ready": "сдан", "rooms": "студии, 1, 2"}],
            },
        }
        patch_planner(monkeypatch, {
            "operation": "select_option",
            "selected_reference": "Томилинский бульвар",
            "scenario_needs": ["family", "rental", "financing"],
            "response_viewpoint": "rental",
            "requested_facts": ["mortgage_terms"],
            "facts_needed": ["mortgage_terms"],
            "requires_enrichment": True,
            "resolved_subject": "mortgage",
            "confidence": 1.0,
        })
        app = make_app(initial)

        result = await mod.run_chat(
            app,
            user_id="u",
            message="А этот ЖК подойдёт для семьи, если позже сдавать квартиру, и что по ипотеке?",
            channel="jivo",
            meta={},
        )

        assert result["ok"] is True
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "contact_name"

    asyncio.run(scenario())


def test_v2_family_financing_contact_then_parking_dialogue_keeps_domain_context(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        plans = iter((
            {
                "operation": "search",
                "intent": "family",
                "constraints_delta": {"hard": {"rooms": 2}, "preferences": {"purpose": "family"}},
                "confidence": 1.0,
            },
            {
                "operation": "select_option",
                "selected_reference": "Бусиновский парк",
                "intent": "mortgage",
                "requested_facts": ["mortgage_terms"],
                "facts_needed": ["mortgage_terms"],
                "confidence": 1.0,
            },
            {
                "operation": "freeform",
                "followup_outcome": "accept",
                "confidence": 1.0,
            },
            {
                "operation": "select_option",
                "selected_reference": "Лосиноостровский парк",
                "requested_facts": ["parking"],
                "resolved_subject": "parking",
                "confidence": 1.0,
            },
        ))

        async def fake_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return next(plans)

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)
        client = FakeClient(
            options=[
                {"name": "Бусиновский парк", "rooms": 2, "min_price": 13_000_000},
                {"name": "Лосиноостровский парк", "rooms": 2, "min_price": 14_000_000, "infrastructure": ["паркинг"]},
                {"name": "Мичуринский парк", "rooms": 2, "min_price": 15_000_000},
            ],
            enriched={"name": "Бусиновский парк", "developer": "ПИК"},
        )
        app = make_app(client=client)

        first = await mod.run_chat(app, user_id="u", message="двушка для семьи", channel="jivo", meta={})
        assert "Искала двухкомнатные квартиры для семьи" in first["answer"]
        assert "нашла три варианта" in first["answer"]

        financing = await mod.run_chat(app, user_id="u", message="А семейная ипотека по Бусиновскому парку подходит?", channel="jivo", meta={})
        assert "Проверить условия по этому ЖК?" in financing["answer"]

        consent = await mod.run_chat(app, user_id="u", message="да", channel="jivo", meta={})
        assert "На какой номер вам удобно позвонить?" in consent["answer"]

        parking = await mod.run_chat(app, user_id="u", message="А в ЖК Лосиноостровский парк есть платная парковка?", channel="jivo", meta={})
        assert parking["intent"] != "collect_contact_phone"
        assert "паркинг" in parking["answer"].casefold()
        v2 = app["state_store"].states["u"]["nmbot_v2"]
        assert v2.get("contact_name") is None
        assert v2["selected_option_name"] == "Лосиноостровский парк"
        assert v2["params"]["rooms"] == 2
        assert v2["params"]["purpose"] == "family"

    asyncio.run(scenario())


def test_v2_operator_explanation_question_stays_v2_conversation(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["visible_options"] = [{"name": "Лучи", "price_range": "от 12 млн рублей"}]
        patch_planner(monkeypatch, {"operation": "current_options", "confidence": 1.0})
        app = make_app(initial)
        calls: list[str] = []

        async def legacy(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("legacy")
            return {"ok": True, "answer": "V1"}

        result = await mod.run_runtime_turn(app, user_id="u", message="зачем оператор?", channel="jivo", meta={})

        assert result["ok"] is True
        assert result["meta"]["runtime"] == "v2"
        assert calls == []
        assert app["overmind_client"].explain_calls == 0
        assert "Отвечаю по текущему списку" in result["answer"]

    asyncio.run(scenario())


def test_normal_v2_search_does_not_call_legacy(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"operation": "search", "constraints_delta": {"hard": {"location": "Москва"}}, "confidence": 1.0})
        app = make_app()
        calls: list[str] = []

        async def legacy(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("legacy")
            return {"ok": True, "answer": "V1"}

        result = await mod.run_runtime_turn(app, user_id="u", message="найди квартиру в Москве", channel="jivo", meta={})

        assert result["ok"] is True
        assert result["meta"]["runtime"] == "v2"
        assert calls == []
        assert app["overmind_client"].ask_calls == 0
        search_calls = [
            payload
            for payload in app["overmind_client"].gateway_payloads
            if payload.get("_payload_stage") == "main_search"
        ]
        assert len(search_calls) == 3  # primary + fill + exact shortlist card; still no V1

    asyncio.run(scenario())


def test_v2_runtime_uses_low_level_main_search_with_shortlist_enrichment(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"operation": "search", "constraints_delta": {"hard": {"location": "Москва"}}, "confidence": 1.0})
        client = LowLevelOnlyClient(options=[{"name": "Лучи", "location": "Москва", "min_price": 12_000_000, "developer": "ПИК"}])
        app = make_app(client=client)

        result = await mod.run_runtime_turn(app, user_id="u", message="найди квартиру в Москве", channel="jivo", meta={})

        assert result["ok"] is True
        assert client.ask_calls == 0
        assert client.gateway_calls == 3  # primary + fill + exact shortlist card
        enrichment_payloads = [p for p in client.gateway_payloads if p.get("_payload_stage") == "main_search" and "full_card" in str(p.get("query"))]
        assert len(enrichment_payloads) == 1
        assert "застройщик ПИК" in result["answer"]

    asyncio.run(scenario())


def test_v2_operator_offer_and_decline_are_safe_without_false_callback(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        app = make_app()
        calls: list[str] = []

        async def legacy(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("legacy")
            return {"ok": True, "answer": "V1"}

        patch_planner(monkeypatch, {"operation": "operator", "confidence": 1.0})
        offer = await mod.run_runtime_turn(app, user_id="u", message="что дальше", channel="jivo", meta={})
        assert offer["ok"] is True
        assert offer["intent"] == "operator_offer"
        assert "Заявку" not in offer["answer"]
        assert "сохранила" not in offer["answer"]

        patch_planner(monkeypatch, {"operation": "operator", "operator_contact": {"consent": "refused"}, "confidence": 1.0})
        declined = await mod.run_runtime_turn(app, user_id="u", message="не надо", channel="jivo", meta={})
        assert declined["ok"] is True
        assert declined["intent"] == "operator_declined"
        assert calls == []
        assert app["state_store"].states["u"]["nmbot_v2"]["operator_declined"] is True

    asyncio.run(scenario())


def test_v2_operator_accept_opens_v2_phone_capture_not_legacy(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        patch_planner(monkeypatch, {"operation": "operator", "operator_contact": {"consent": "granted"}, "confidence": 1.0})
        app = make_app()
        calls: list[str] = []

        async def legacy(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("legacy")
            return {"ok": True, "answer": "V1 accept", "intent": "collect_contact_phone"}

        result = await mod.run_runtime_turn(app, user_id="u", message="да", channel="jivo", meta={})

        assert calls == []
        assert result["intent"] == "collect_contact_phone"
        assert result["awaiting_phone"] is True
        assert "На какой номер вам удобно позвонить?" in result["answer"]
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "contact_phone"

    asyncio.run(scenario())


def test_v2_parking_operator_accept_then_bare_yes_stays_in_phone_capture(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        plans = iter((
            {"operation": "search", "constraints_delta": {"hard": {"location": "Иртышский"}}, "confidence": 1.0},
            {"operation": "select_option", "selected_reference": "2-й Иртышский", "selected_option_name": "2-й Иртышский", "requested_facts": ["parking_price", "parking_inventory"], "facts_needed": ["parking_price", "parking_inventory"], "resolved_subject": "parking", "confidence": 1.0},
            {"operation": "freeform", "followup_outcome": "accept", "confidence": 1.0},
        ))
        planner_calls: list[str] = []

        async def fake_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            planner_calls.append("planner")
            return next(plans)

        monkeypatch.setattr(mod.followup_intent_classifier, "plan_dialog_state", fake_plan)
        app = make_app(
            client=FakeClient(
                options=[
                    {"name": "2-й Иртышский", "location": "Иртышский", "min_price": 12_000_000},
                    {"name": "Лучи", "location": "Москва", "min_price": 13_000_000},
                ],
                enriched={"name": "2-й Иртышский", "location": "Иртышский", "min_price": 12_000_000},
            )
        )

        first = await mod.run_chat(app, user_id="u", message="найди 2-й Иртышский", channel="jivo", meta={})
        assert first["ok"] is True
        selected = await mod.run_chat(app, user_id="u", message="что по цене и наличию паркинга?", channel="jivo", meta={})
        assert selected["ok"] is True
        assert "Передать оператору" in selected["answer"]

        consent = await mod.run_chat(app, user_id="u", message="да", channel="jivo", meta={})
        assert consent["intent"] == "collect_contact_phone"
        assert consent["answer"].count("На какой номер вам удобно позвонить?") == 1
        v2 = app["state_store"].states["u"]["nmbot_v2"]
        assert v2["pending_followup"] == "contact_phone"
        assert v2["contact_consent"] is True

        repeated_yes = await mod.run_chat(app, user_id="u", message="да", channel="jivo", meta={})
        assert repeated_yes["intent"] == "collect_contact_phone"
        assert "номер выглядит неполным" not in repeated_yes["answer"]
        assert "Проверить актуальность" not in repeated_yes["answer"]
        assert "validation_failed" not in json.dumps(repeated_yes, ensure_ascii=False)
        assert len(planner_calls) == 3
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "contact_phone"

    asyncio.run(scenario())


def test_v2_selected_financing_followup_sequence_uses_model_outcome_registry(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["visible_options"] = [{"name": "Лучи", "price_range": "от 12 млн рублей"}]
        initial["selected_option"] = {"name": "Лучи", "price_range": "от 12 млн рублей"}
        initial["nmbot_v2"] = {"selected_option_name": "Лучи", "visible_options": [{"name": "Лучи", "price": "от 12 млн рублей"}]}
        app = make_app(initial)

        patch_planner(monkeypatch, {"operation": "financing", "intent": "mortgage", "scope": "one", "confidence": 1.0})
        offer = await mod.run_chat(app, user_id="u", message="а ипотека по нему есть?", channel="jivo", meta={})
        assert offer["ok"] is True
        assert "Проверить условия по этому ЖК?" in offer["answer"]
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "financing_consent"

        patch_planner(monkeypatch, {"operation": "freeform", "followup_outcome": "accept", "confidence": 1.0})
        accepted = await mod.run_chat(app, user_id="u", message="да", channel="jivo", meta={})
        assert "На какой номер вам удобно позвонить?" in accepted["answer"]
        assert app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "contact_phone"

    asyncio.run(scenario())


def test_v2_financing_decline_and_recovery_do_not_capture_contact_or_reoffer_operator(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["visible_options"] = [{"name": "Лучи", "price_range": "от 12 млн рублей"}]
        initial["nmbot_v2"] = {"pending_followup": "financing_consent", "selected_option_name": "Лучи", "visible_options": initial["visible_options"]}

        decline_app = make_app(initial)
        patch_planner(monkeypatch, {"operation": "freeform", "followup_outcome": "decline", "confidence": 1.0})
        declined = await mod.run_chat(decline_app, user_id="u", message="нет", channel="jivo", meta={})
        assert declined["intent"] == "operator_declined"
        assert "Как к вам обращаться?" not in declined["answer"]
        assert decline_app["state_store"].states["u"]["nmbot_v2"].get("pending_followup") is None
        assert decline_app["state_store"].states["u"]["nmbot_v2"]["operator_declined"] is True

        clarify_app = make_app(initial)
        patch_planner(monkeypatch, {"operation": "freeform", "followup_outcome": "ask_or_clarify", "confidence": 1.0})
        clarified = await mod.run_chat(clarify_app, user_id="u", message="что значит проверить?", channel="jivo", meta={})
        assert "Как к вам обращаться?" not in clarified["answer"]
        assert clarified["answer"].count("Проверить условия по этому ЖК?") == 1
        assert clarify_app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "financing_consent"
        assert clarify_app["state_store"].states["u"]["nmbot_v2"].get("contact_name") is None

        unexpected_app = make_app(initial)
        patch_planner(monkeypatch, {"operation": "freeform", "followup_outcome": "bad", "confidence": 1.0})
        recovered = await mod.run_chat(unexpected_app, user_id="u", message="может потом синий", channel="jivo", meta={})
        assert "Как к вам обращаться?" not in recovered["answer"]
        assert recovered["answer"].count("Проверить условия по этому ЖК?") == 1
        assert unexpected_app["state_store"].states["u"]["nmbot_v2"]["pending_followup"] == "financing_consent"

    asyncio.run(scenario())


def test_v2_state_mapping_keeps_safe_recent_turns_and_redacts_contacts(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["params"] = {"location": "Москва", "phone": "+79991234567", "email": "a@b.ru"}
        initial["dialog_turns"] = [
            {"role": "user", "text": "мой телефон +7 999 123-45-67 и email test@example.com"},
            {"role": "assistant", "text": "Записала"},
        ]
        initial["pending_followup"] = {"type": "selected_option", "raw_id": "secret"}
        patch_planner(monkeypatch, {"operation": "current_options", "intent": "mortgage", "facets": ["mortgage"], "scope": "all", "confidence": 1.0})
        client = FakeClient()
        app = make_app(initial, client=client)

        result = await mod.run_chat(app, user_id="u", message="что по ипотеке?", channel="jivo", meta={})

        assert result["ok"] is True
        v2 = app["state_store"].states["u"]["nmbot_v2"]
        assert v2["params"] == {"location": "Москва"}
        assert v2["pending_followup"] == "selected_option"
        serialized = str(v2["recent_turns"])
        assert "+7 999" not in serialized and "test@example.com" not in serialized
        assert "[redacted-contact]" in serialized and "[redacted-email]" in serialized

    asyncio.run(scenario())


def test_v2_state_mapping_persists_full_manager_dialogue(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v2")
        initial = mod._default_state()
        initial["nmbot_v2"] = {
            "recent_turns": [{"user": "последний", "assistant": "ответ"}],
            "dialogue_turns": [
                {"user": f"вопрос {index}", "assistant": f"ответ {index}"}
                for index in range(8)
            ],
        }
        patch_planner(monkeypatch, {"operation": "current_options", "intent": "life", "scope": "all", "confidence": 1.0})
        app = make_app(initial)

        result = await mod.run_chat(app, user_id="u", message="продолжим", channel="jivo", meta={})

        assert result["ok"] is True
        saved = app["state_store"].states["u"]["nmbot_v2"]
        assert len(saved["dialogue_turns"]) == 9
        assert saved["dialogue_turns"][0]["user"] == "вопрос 0"
        assert saved["dialogue_turns"][-1]["user"] == "продолжим"
        assert len(saved["recent_turns"]) <= 6

    asyncio.run(scenario())


def test_legacy_only_record_is_rewritten_to_canonical_v2_envelope_without_private_fields(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="bad")
        initial = mod._default_state()
        initial["params"] = {"location": "Москва", "phone": "+79991234567", "raw_payload": "secret"}
        initial["visible_options"] = [{"name": "Лучи", "price_range": "от 12 млн рублей", "client_id": "secret"}]
        initial["selected_option"] = {"name": "Лучи", "phone": "+7 999 123-45-67"}
        initial["dialog_turns"] = [{"role": "user", "text": "мой телефон +7 999 123-45-67"}]
        patch_planner(monkeypatch, {"operation": "current_options", "intent": "life", "scope": "all", "confidence": 1.0})
        app = make_app(initial)

        result = await mod.run_chat(app, user_id="u", message="расскажи про варианты", channel="jivo", meta={})

        assert result["ok"] is True
        saved = app["state_store"].states["u"]
        assert set(saved) == {"nmbot_v2"}
        dumped = json.dumps(saved, ensure_ascii=False)
        assert "+7 999" not in dumped
        assert "+7999" not in dumped
        assert "client_id" not in dumped
        assert "raw_payload" not in dumped
        assert saved["nmbot_v2"]["params"] == {"location": "Москва"}

    asyncio.run(scenario())


def test_canonical_v2_record_cannot_be_shadowed_by_stale_root_values(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v1")
        initial = {
            "params": {"location": "Старый район"},
            "visible_options": [{"name": "Старый ЖК", "price_range": "от 99 млн рублей"}],
            "selected_option": {"name": "Старый ЖК"},
            "last_bot_question": "устаревший вопрос?",
            "nmbot_v2": {
                "params": {"location": "Москва"},
                "visible_options": [{"name": "Лучи", "price": "от 12 млн рублей", "location": "Москва"}],
                "selected_option_name": "Лучи",
            },
        }
        patch_planner(monkeypatch, {"operation": "current_options", "intent": "life", "scope": "all", "confidence": 1.0})
        app = make_app(initial)

        result = await mod.run_chat(app, user_id="u", message="что есть сейчас?", channel="jivo", meta={})

        assert result["ok"] is True
        assert "Лучи" in result["answer"]
        assert "Старый ЖК" not in result["answer"]
        saved = app["state_store"].states["u"]
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"]["selected_option_name"] == "Лучи"

    asyncio.run(scenario())


def test_jivo_handler_under_v1_remains_behavior_compatible(monkeypatch):
    async def scenario() -> None:
        env(monkeypatch, version="v1")
        previous = os.environ.get("JIVO_PROVIDER_TOKEN")
        monkeypatch.setenv("JIVO_PROVIDER_TOKEN", "configured")
        app = make_app()

        async def fake_run_chat(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "answer": "Ответ V1", "handoff_to_operator": False, "intent": "main_search"}

        monkeypatch.setattr(mod, "run_chat", fake_run_chat)

        class Req:
            match_info = {"provider_token": "configured"}

            def __init__(self) -> None:
                self.app = app

            async def json(self) -> dict[str, Any]:
                return {"event": "CLIENT_MESSAGE", "site_id": "s", "client_id": "c", "chat_id": "ch", "message": {"type": "TEXT", "text": "привет"}}

        response = await mod.handle_jivo(Req())
        assert response.status == 200
        body = __import__("json").loads(response.body)
        assert body["event"] == "BOT_MESSAGE"
        assert body["message"]["text"] == "Ответ V1"
        if previous is None:
            monkeypatch.delenv("JIVO_PROVIDER_TOKEN", raising=False)

    asyncio.run(scenario())


def test_intent_plan_version_env_routes_to_v2_by_default_and_v3_when_enabled(monkeypatch):
    async def scenario() -> None:
        calls: list[str] = []

        async def fake_v2(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("v2")
            return {"operation": "current_options", "confidence": 0.8}

        async def fake_v3(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("v3")
            return {"schema_version": 3, "goal": "answer_current", "viewpoint": "unchanged", "selected_option_name": None, "named_object_reference": None, "requested_facts": [], "constraints_delta": {}, "operator_consent": None, "explicit_operator_request": False, "clarification": None, "confidence": 0.9}

        monkeypatch.setattr(runtime_adapter_mod.followup_intent_classifier, "plan_dialog_state", fake_v2)
        monkeypatch.setattr(runtime_adapter_mod.followup_intent_classifier, "plan_intent_v3", fake_v3)
        monkeypatch.delenv("NMBOT_INTENT_PLAN_VERSION", raising=False)

        v2_plan = await _SemanticPlannerAdapter(make_app()).plan(SafeTurnContext("u", "что по ним?"), ConversationState(visible_options=(OptionCard(name="Лучи"),)))

        monkeypatch.setenv("NMBOT_INTENT_PLAN_VERSION", "v3")
        v3_plan = await _SemanticPlannerAdapter(make_app()).plan(SafeTurnContext("u", "что по ним?"), ConversationState(visible_options=(OptionCard(name="Лучи"),)))

        assert calls == ["v2", "v3"]
        assert v2_plan.operation == "current_options"
        assert isinstance(v3_plan, ExecutableTurn)
        assert v3_plan.goal is IntentGoal.ANSWER_CURRENT
        assert v3_plan.stage is Stage.CURRENT_OPTIONS
        assert v3_plan.action is TurnAction.ANSWER_FROM_CURRENT_OPTIONS
        assert v3_plan.confidence == 0.9

    asyncio.run(scenario())


def test_intent_plan_v3_maps_lookup_and_preserves_core_fields(monkeypatch):
    async def scenario() -> None:
        async def fake_v3(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"schema_version": 3, "goal": "lookup_object", "viewpoint": "financing", "selected_option_name": None, "named_object_reference": "ЖК Дюна", "requested_facts": ["mortgage_terms"], "constraints_delta": {"hard": {"max_price": 15000000}}, "operator_consent": None, "explicit_operator_request": False, "clarification": None, "confidence": 0.87, "planner_raw_response": "raw-json"}

        monkeypatch.setenv("NMBOT_INTENT_PLAN_VERSION", "v3")
        monkeypatch.setattr(runtime_adapter_mod.followup_intent_classifier, "plan_intent_v3", fake_v3)

        adapter = _SemanticPlannerAdapter(make_app())
        plan = await adapter.plan(SafeTurnContext("u", "что по ЖК Дюна в ипотеку?"), ConversationState())

        assert isinstance(plan, ExecutableTurn)
        assert plan.goal is IntentGoal.LOOKUP_OBJECT
        assert plan.stage is Stage.REFINEMENT
        assert plan.action is TurnAction.SEARCH
        assert plan.reference == "ЖК Дюна"
        assert plan.intent == "mortgage"
        assert plan.facets == ["financing"]
        assert plan.requested_facts == ("mortgage_terms",)
        assert plan.constraints_delta == {"hard": {"max_price": 15000000}}
        assert plan.confidence == 0.87
        assert adapter.last_planner_plan["planner_raw_response"] == "raw-json"
        assert adapter.last_planner_plan["intent_plan_v3_validation"]["ok"] is True
        assert adapter.last_planner_plan["intent_plan_v3_transition"]["accepted"] is True

    asyncio.run(scenario())


def test_intent_plan_v3_invalid_visible_selection_falls_back_without_selection(monkeypatch):
    async def scenario() -> None:
        async def fake_v3(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"schema_version": 3, "goal": "answer_selected", "viewpoint": "unchanged", "selected_option_name": "Неизвестный ЖК", "named_object_reference": None, "requested_facts": [], "constraints_delta": {}, "operator_consent": None, "explicit_operator_request": False, "clarification": None, "confidence": 0.91}

        monkeypatch.setenv("NMBOT_INTENT_PLAN_VERSION", "v3")
        monkeypatch.setattr(runtime_adapter_mod.followup_intent_classifier, "plan_intent_v3", fake_v3)
        adapter = _SemanticPlannerAdapter(make_app())

        plan = await adapter.plan(SafeTurnContext("u", "расскажи про неизвестный"), ConversationState(visible_options=(OptionCard(name="Лучи"),), selected_option_name="Лучи"))

        assert isinstance(plan, ExecutableTurn)
        assert plan.action is TurnAction.SAFE_ERROR
        assert plan.stage is Stage.ERROR
        assert plan.confidence == 0.0
        assert plan.selected_option_name is None
        assert adapter.last_planner_plan["intent_plan_v3_validation"]["ok"] is False
        assert "selected_option_not_visible" in adapter.last_planner_plan["intent_plan_v3_validation"]["errors"]
        assert adapter.last_planner_plan["intent_plan_v3_adapter"]["fallback_used"] is True

    asyncio.run(scenario())


def test_intent_plan_v3_compare_current_visible_pair_keeps_all_scope(monkeypatch):
    async def scenario() -> None:
        async def fake_v3(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {
                "schema_version": 3,
                "goal": "compare_current",
                "viewpoint": "unchanged",
                "selected_option_name": "Левел Лесной",
                "named_object_reference": "Томилинский бульвар",
                "requested_facts": [],
                "constraints_delta": {},
                "operator_consent": None,
                "explicit_operator_request": False,
                "clarification": None,
                "confidence": 1.0,
            }

        monkeypatch.setenv("NMBOT_INTENT_PLAN_VERSION", "v3")
        monkeypatch.setattr(runtime_adapter_mod.followup_intent_classifier, "plan_intent_v3", fake_v3)
        adapter = _SemanticPlannerAdapter(make_app())

        plan = await adapter.plan(
            SafeTurnContext("u", "сравни Левел Лесной и Томилинский бульвар"),
            ConversationState(
                visible_options=(OptionCard(name="Левел Лесной"), OptionCard(name="Томилинский бульвар")),
                selected_option_name="Левел Лесной",
            ),
        )

        assert isinstance(plan, ExecutableTurn)
        assert plan.goal is IntentGoal.COMPARE_CURRENT
        assert plan.stage is Stage.CURRENT_OPTIONS
        assert plan.action is TurnAction.ANSWER_FROM_CURRENT_OPTIONS
        assert plan.scope == "all"
        assert plan.reference is None
        assert plan.selected_option_name is None
        assert plan.named_object_reference is None
        assert adapter.last_planner_plan["intent_plan_v3_validation"] == {"ok": True, "errors": [], "repairable": False}
        assert adapter.last_planner_plan["intent_plan_v3_transition"]["accepted"] is True

    asyncio.run(scenario())


def test_plan_intent_v3_parses_gateway_response_and_sends_safe_payload(monkeypatch):
    import followup_intent_classifier as planner

    class FakeResponse:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def json(self) -> dict[str, Any]:
            return self.payload

    class FakeSession:
        def __init__(self) -> None:
            self.post_payloads: list[dict[str, Any]] = []

        def post(self, _url: str, *, json: dict[str, Any], headers: dict[str, Any]):
            self.post_payloads.append(json)
            return FakeResponse({"id": 7})

        def get(self, url: str, *, headers: dict[str, Any]):
            if url.endswith("/status"):
                return FakeResponse({"status": "completed"})
            raw = "```json\n" + json.dumps({"schema_version": 3, "goal": "answer_current", "viewpoint": "life", "selected_option_name": None, "named_object_reference": None, "requested_facts": ["parks"], "constraints_delta": {}, "operator_consent": None, "explicit_operator_request": False, "clarification": None, "confidence": 0.82}, ensure_ascii=False) + "\n```"
            return FakeResponse({"result": {"response": raw}})

    monkeypatch.setenv("OVERMIND_TOKEN", "token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter")
    session = FakeSession()

    result = asyncio.run(planner.plan_intent_v3(session, user_text="есть парки?", state={"safe": True}, last_turn={"bot_question": "", "client_answer": "есть парки?"}, last_response_text="last", visible_response_text="Лучи", search_response_text="{}", selected_object={"canonical_name": "Лучи"}, dialog_focus={"last_subject": "infrastructure"}, allowed_subjects=["infrastructure"], allowed_facts=["parks"], subject_fact_map={"infrastructure": ["parks"]}, dynamic_fields=["parks"], pending_scenario={"id": "x"}, model="test-model", timeout=3))

    assert result["goal"] == "answer_current"
    assert result["requested_facts"] == ["parks"]
    assert result["fallback_used"] is False
    assert "planner_raw_response" in result
    request_data = session.post_payloads[0]["request_data"]
    payload = json.loads(request_data["query"])
    assert set(payload) == {"user_text", "state", "last_turn", "last_response_text", "visible_response_text", "search_response_text", "selected_object", "dialog_focus", "allowed_subjects", "allowed_facts", "subject_fact_map", "dynamic_fields", "pending_scenario"}
    assert request_data["system_prompt"] == planner.INTENT_PLAN_V3_PROMPT
    assert request_data["json_schema"]["properties"]["goal"]["enum"] == ["new_search", "refine_search", "expand_search", "lookup_object", "answer_current", "compare_current", "recommend_current", "answer_selected", "answer_open_question", "operator", "clarify", "resume_pending", "off_topic"]
    assert request_data["json_schema"]["properties"]["requested_facts"]["items"] == {"type": "string", "enum": ["parks"]}
