from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
import inspect
import re
import time
from typing import Any, Mapping

from .constraints import normalize_constraints_delta, topic_from_plan
from .contracts import DialogFocus, ExecutableTurn, ExecutionResult, IntentGoal, OptionCard, PendingAction, SafeTurnContext, SearchResult, SelectedEntity, SemanticPlan, Stage, StateDelta, TurnAction, TurnPlan, TurnResult
from .execution_path import build_v2_execution_path
from .fact_context import answered_facts, fact_availability
from .ports import ConversationPort, JournalPort, ManagerRewriterPort, OperatorPort, ResponseComposerPort, SearchServicePort, SemanticPlannerPort, TracePort
from .pending import pending_delta_for_action
from .prompt_provenance import sanitize_prompt_provenance
from .response import build_final_response_plan, render_response
from .scenario_recipes import FINANCING_CONSENT_FOLLOWUP, SELECTED_LIVE_FACT_CONSENT_FOLLOWUP, resolve_recipe
from .state import ConversationState, apply_state_delta, merge_enriched_card_cache, merge_enriched_card_cache_entries
from .pending_action import confirm_pending_action, pending_action_belongs_to_current_offer
from .capability_registry import CapabilityStatus, compile_capability_request
from .vocabulary import FACT_KEY_SET
from .transition import TransitionDecision, derive_transition


class _NoopTrace:
    def record(self, event: dict) -> None:
        return None


class _NoopJournal:
    def append(self, result: TurnResult) -> None:
        return None


@dataclass
class TurnProcessor:
    planner: SemanticPlannerPort
    search_service: SearchServicePort | None = None
    conversation: ConversationPort | None = None
    operator: OperatorPort | None = None
    journal: JournalPort | None = None
    trace: TracePort | None = None
    response_composer: ResponseComposerPort | None = None
    response_composer_mode: str = "off"
    manager_rewriter: ManagerRewriterPort | None = None
    manager_rewriter_mode: str = "off"

    def process(self, context: SafeTurnContext, state: ConversationState | None = None) -> TurnResult:
        return _run_async_core_from_sync(lambda: self.process_async(context, state))

    async def process_async(self, context: SafeTurnContext, state: ConversationState | None = None) -> TurnResult:
        started_at = time.monotonic()
        state = state or ConversationState()
        journal = self.journal or _NoopJournal()
        trace = self.trace or _NoopTrace()
        plan = await _maybe_await(self.planner.plan(context, state))
        planner_done_at = time.monotonic()
        decision = _decision_from_plan(plan, state)
        await _maybe_await(trace.record(dict({"event": "transition", "stage": decision.stage.value, "action": decision.action.value, "ok": decision.accepted})))

        search_invoked = decision.action == TurnAction.SEARCH and not decision.error_code and self.search_service is not None
        execution = await self._execute_async(decision.action, plan, state, decision.error_code, context)
        execution_done_at = time.monotonic()
        delta = self._delta_for(decision.action, plan, execution, state)
        accepted_state = decision.accepted and execution.ok
        response_plan = build_final_response_plan(stage=decision.stage, plan=plan, execution=execution, delta=delta if accepted_state else StateDelta(), state=state)
        deterministic_text = render_response(response_plan)
        response_text = deterministic_text
        response_meta = {"used": False, "reason": "deterministic_renderer"}
        manager_rewriter_meta = {"used": False, "reason": "off"}
        mode = _response_composer_mode(self.response_composer_mode)
        if self.response_composer and mode in {"shadow", "publish"}:
            response_meta = await self._compose_response_once(
                mode=mode,
                stage=decision.stage,
                plan=plan,
                execution=execution,
                delta=delta if accepted_state else StateDelta(),
                state=state,
                response_plan=response_plan,
                deterministic_text=deterministic_text,
            )
            if mode == "publish" and response_meta.get("used") and response_meta.get("published") and response_meta.get("text"):
                response_text = str(response_meta.pop("text"))
            else:
                response_meta.pop("text", None)
        manager_mode = _response_composer_mode(self.manager_rewriter_mode)
        if self.manager_rewriter and manager_mode in {"shadow", "publish"} and decision.action != TurnAction.RESET:
            manager_rewriter_meta = await self._rewrite_manager_once(
                mode=manager_mode,
                stage=decision.stage,
                plan=plan,
                execution=execution,
                delta=delta if accepted_state else StateDelta(),
                state=state,
                response_plan=response_plan,
                prepared_answer=response_text,
                context=context,
            )
            if manager_mode == "publish" and manager_rewriter_meta.get("used") and manager_rewriter_meta.get("published") and manager_rewriter_meta.get("text"):
                response_text = str(manager_rewriter_meta.pop("text"))
            else:
                manager_rewriter_meta.pop("text", None)
        elif self.manager_rewriter and manager_mode in {"shadow", "publish"}:
            manager_rewriter_meta = {
                "mode": manager_mode,
                "used": False,
                "published": False,
                "status": "skipped",
                "reason": "reset_turn",
            }
        response_text = _temporary_strip_repeated_finance_unknown_sentence(response_text)
        response_done_at = time.monotonic()
        if accepted_state:
            delta = self._finalize_delta(delta, context, response_text, response_plan.answer_kind)
        new_state = apply_state_delta(state, delta, accepted=accepted_state)
        timing_ms = {
            "planner": round((planner_done_at - started_at) * 1000),
            "execution": round((execution_done_at - planner_done_at) * 1000),
            "response": round((response_done_at - execution_done_at) * 1000),
            "total": round((response_done_at - started_at) * 1000),
        }
        runtime_summary = _runtime_summary(
            stage=decision.stage,
            action=decision.action,
            plan=plan,
            answer_kind=response_plan.answer_kind,
            timing_ms=timing_ms,
            state_before=state,
            state_after=new_state,
            execution=execution,
            response_text=response_text,
        )
        _attach_response_model_usage(runtime_summary, response_meta=response_meta, manager_rewriter_meta=manager_rewriter_meta)
        execution_path = build_v2_execution_path(
            action=decision.action,
            transition_ok=decision.accepted,
            search_invoked=search_invoked,
            execution_ok=execution.ok,
            execution_error_code=execution.error_code,
            response_composer=response_meta,
            manager_rewriter=manager_rewriter_meta,
        )
        result = TurnResult(
            context=context,
            semantic_plan=plan,
            stage=decision.stage,
            action=decision.action,
            execution=execution,
            state_delta=delta if accepted_state else StateDelta(),
            response_plan=response_plan,
            response_text=response_text,
            state=new_state.to_dict(),
            trace={
                "accepted_state": accepted_state,
                "error_code": execution.error_code,
                "retry_count": execution.retry_count,
                "attempts": list(execution.attempts),
                "timing_ms": timing_ms,
                "response_composer": response_meta,
                "manager_rewriter": manager_rewriter_meta,
                "runtime_summary": runtime_summary,
                "execution_path": execution_path,
            },
        )
        await _maybe_await(trace.record(dict({"event": "finalized", "stage": decision.stage.value, "action": decision.action.value, "answer_kind": response_plan.answer_kind})))
        await _maybe_await(journal.append(deepcopy(result)))
        return result

    async def _compose_response_once(
        self,
        *,
        mode: str,
        stage: Stage,
        plan: TurnPlan,
        execution: ExecutionResult,
        delta: StateDelta,
        state: ConversationState,
        response_plan: Any,
        deterministic_text: str,
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            from .response_composer import build_response_brief, is_one_shot_composer_eligible

            brief = build_response_brief(stage=stage, plan=plan, execution=execution, delta=delta, state=state, response_plan=response_plan)
            if not is_one_shot_composer_eligible(brief):
                return _runtime_response_composer_meta(
                    {"used": False, "status": "fallback", "reason": "ineligible_response_goal", "error_code": "ineligible_response_goal"},
                    mode=mode,
                    published=False,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                )
            result = await _maybe_await(self.response_composer.compose_response(brief, fallback_text=deterministic_text))  # type: ignore[union-attr]
            meta = result.to_meta() if hasattr(result, "to_meta") else {}
            used = bool(meta.get("used")) and bool(getattr(result, "text", ""))
            out = _runtime_response_composer_meta(meta, mode=mode, published=bool(mode == "publish" and used), elapsed_ms=round((time.monotonic() - started) * 1000))
            if used:
                out["text"] = str(getattr(result, "text"))
            return out
        except Exception as exc:
            return {
                "mode": mode,
                "used": False,
                "published": False,
                "status": "fallback",
                "reason": "composer_error",
                "error_code": _safe_execution_error_code(exc),
                "attempts": 1,
                "elapsed_ms": _bounded_int(round((time.monotonic() - started) * 1000), 0, 10 * 60 * 1000),
                "attempt_summaries": (),
            }

    async def _rewrite_manager_once(
        self,
        *,
        mode: str,
        stage: Stage,
        plan: TurnPlan,
        execution: ExecutionResult,
        delta: StateDelta,
        state: ConversationState,
        response_plan: Any,
        prepared_answer: str,
        context: SafeTurnContext,
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            from .manager_rewriter import rewrite_manager_answer_async
            from .response_composer import build_response_brief

            brief = build_response_brief(stage=stage, plan=plan, execution=execution, delta=delta, state=state, response_plan=response_plan)
            transcript = _manager_rewriter_transcript(state, context.user_text)
            result = await rewrite_manager_answer_async(
                transcript=transcript,
                current_question=context.user_text,
                prepared_answer=prepared_answer,
                brief=brief,
                rewriter=self.manager_rewriter,
            )
            meta = result.to_meta() if hasattr(result, "to_meta") else {}
            used = bool(meta.get("used")) and bool(getattr(result, "text", ""))
            out = _runtime_response_composer_meta(meta, mode=mode, published=bool(mode == "publish" and used), elapsed_ms=round((time.monotonic() - started) * 1000))
            if used:
                out["text"] = str(getattr(result, "text"))
            return out
        except Exception as exc:
            return {
                "mode": mode,
                "used": False,
                "published": False,
                "status": "fallback",
                "reason": "rewriter_error",
                "error_code": _safe_execution_error_code(exc),
                "attempts": 1,
                "elapsed_ms": _bounded_int(round((time.monotonic() - started) * 1000), 0, 10 * 60 * 1000),
                "attempt_summaries": (),
            }

    async def _execute_async(self, action: TurnAction, plan: TurnPlan, state: ConversationState, error_code: str | None, context: SafeTurnContext) -> ExecutionResult:
        if error_code:
            return ExecutionResult(ok=False, error_code=error_code)
        try:
            if action == TurnAction.RESET:
                return ExecutionResult(ok=True, message="reset")
            if action == TurnAction.SEARCH:
                if not self.search_service:
                    return ExecutionResult(ok=False, error_code="search_service_missing")
                search_method = self.search_service.search
                try:
                    search_params = list(inspect.signature(search_method).parameters)
                except (TypeError, ValueError):
                    search_params = []
                if len(search_params) >= 3:
                    result = await _maybe_await(search_method(plan, state, context))
                else:
                    result = await _maybe_await(search_method(plan, state))
                attempts = tuple(getattr(self.search_service, "last_attempts", ()))
                return ExecutionResult(ok=True, search=result, attempts=attempts, retry_count=max(0, len(attempts) - 1))
            if action == TurnAction.ANSWER_SELECTED_OPTION:
                selected = state.find_visible_option(plan.selected_option_name)
                is_capability_accept = state.pending_followup == FINANCING_CONSENT_FOLLOWUP and plan.followup_outcome == "accept" and pending_action_belongs_to_current_offer(state)
                if is_capability_accept:
                    action_state = confirm_pending_action(state, state.pending_action.idempotency_key)
                    request = compile_capability_request(action_state.state)
                    if not action_state.execute or selected is None or not self.search_service or not hasattr(self.search_service, "verify_selected_capability"):
                        return ExecutionResult(ok=True, selected=selected, error_code="selected_capability_prerequisite", bridge_status="selected_capability_prerequisite", attempts=(_capability_attempt(request, "selected_capability_prerequisite"),))
                    if request.status != CapabilityStatus.READY:
                        status = "selected_capability_" + request.status.value
                        return ExecutionResult(ok=True, selected=selected, error_code=status, bridge_status=status, attempts=(_capability_attempt(request, status),))
                    if (
                        selected.entity_type != request.entity_type
                        or selected.entity_id is None
                        or str(selected.entity_id) != str(request.entity_id)
                    ):
                        canonical_selected = state.selected_enriched or state.find_visible_option(state.selected_option_name)
                        return ExecutionResult(ok=True, selected=canonical_selected, error_code="selected_capability_entity_mismatch", bridge_status="selected_capability_entity_mismatch", attempts=(_capability_attempt(request, "selected_capability_entity_mismatch"),))
                    card, evidence, meta = await _maybe_await(self.search_service.verify_selected_capability(selected, request))
                    status = str(meta.get("status") or "selected_capability_empty")
                    capability_meta = dict(meta) if isinstance(meta, Mapping) else {}
                    capability_meta.setdefault("evidence_status", getattr(getattr(evidence, "status", None), "value", None))
                    capability_meta.setdefault("identity_match", getattr(evidence, "identity_match", None))
                    capability_meta.setdefault("active_root", getattr(evidence, "active_root", None))
                    return ExecutionResult(ok=True, selected=card, fresh_facts=("mortgage_terms",) if status.endswith(("evidence_complete", "evidence_partial")) else (), error_code=None if status.endswith(("evidence_complete", "evidence_partial")) else status, bridge_status=status, attempts=(_capability_attempt(request, status, capability_meta, request_count=1),))
                fresh_facts: tuple[str, ...] = ()
                if selected and self.search_service:
                    try:
                        selected = await _maybe_await(self.search_service.enrich_selected(selected, state, plan))
                        fresh_facts = _safe_fresh_facts(getattr(self.search_service, "last_fresh_facts", ()))
                        enrichment_attempt = getattr(self.search_service, "last_enrichment_trace", None)
                        attempts = (dict(enrichment_attempt),) if isinstance(enrichment_attempt, dict) and enrichment_attempt else ()
                        enrichment_error = str(getattr(self.search_service, "last_enrichment_error_code", "") or "") or None
                    except Exception as exc:
                        return ExecutionResult(ok=True, selected=selected, error_code=f"selected_enrichment_{exc.__class__.__name__}")
                    return ExecutionResult(ok=True, selected=selected, fresh_facts=fresh_facts, error_code=enrichment_error, attempts=attempts)
                return ExecutionResult(ok=True, selected=selected, fresh_facts=fresh_facts)
            if action == TurnAction.ANSWER_OFF_TOPIC:
                return ExecutionResult(ok=True, message="off_topic")
            if action in {TurnAction.ANSWER_FROM_CURRENT_OPTIONS, TurnAction.CLARIFY_FINANCING, TurnAction.CLARIFY_SELECTED_LIVE_FACT, TurnAction.FREEFORM}:
                if action == TurnAction.ANSWER_FROM_CURRENT_OPTIONS and isinstance(plan, ExecutableTurn) and plan.comparison_option_names:
                    if not self.search_service or not hasattr(self.search_service, "enrich_pair"):
                        return ExecutionResult(ok=True, message="Поняла. Отвечу по текущему подбору.")
                    try:
                        pair = await _maybe_await(self.search_service.enrich_pair(plan, state))  # type: ignore[attr-defined]
                    except Exception as exc:
                        return ExecutionResult(ok=True, message="Поняла. Отвечу по текущему подбору.", error_code=f"pair_enrichment_{exc.__class__.__name__}")
                    cards = tuple(getattr(pair, "ordered_cards", ()) or ())
                    metadata = _safe_pair_metadata(getattr(pair, "metadata", {}) if pair is not None else {})
                    error_status = _safe_pair_status(getattr(pair, "error_status", None) if pair is not None else None)
                    if len(cards) != 2:
                        return ExecutionResult(ok=True, message="Поняла. Отвечу по текущему подбору.", error_code=error_status or "pair_enrichment_malformed")
                    if error_status:
                        metadata = {**metadata, "error_status": error_status}
                    attempts = tuple(item for item in getattr(pair, "attempts", ()) if isinstance(item, dict))
                    return ExecutionResult(
                        ok=True,
                        comparison_cards=cards,
                        comparison_cache_additions=tuple(getattr(pair, "cache_additions", ()) or ()),
                        comparison_metadata=metadata,
                        error_code=error_status,
                        attempts=attempts,
                    )
                if self.conversation:
                    return await _maybe_await(self.conversation.answer(plan, state))
                if action == TurnAction.ANSWER_FROM_CURRENT_OPTIONS and _is_current_fact_answer(plan):
                    from .conversation import build_native_conversation_answer

                    return ExecutionResult(ok=True, message=build_native_conversation_answer(plan, state, context.user_text))
                return ExecutionResult(ok=True, message="Поняла. Отвечу по текущему подбору.")
            if action in {TurnAction.OFFER_OPERATOR, TurnAction.ACCEPT_OPERATOR}:
                if self.operator:
                    return await _maybe_await(self.operator.prepare(plan, state))
                return ExecutionResult(ok=True, message="operator")
            if action == TurnAction.DECLINE_OPERATOR:
                return ExecutionResult(ok=True, message="operator declined")
        except Exception as exc:  # provider/transport failure boundary; no state mutation happens after this
            return ExecutionResult(ok=False, error_code=_safe_execution_error_code(exc))
        return ExecutionResult(ok=False, error_code="unsupported_action")

    def _delta_for(self, action: TurnAction, plan: TurnPlan, execution: ExecutionResult, state: ConversationState) -> StateDelta:
        if not execution.ok:
            return StateDelta()
        if action == TurnAction.RESET:
            return StateDelta(reset=True)
        if action == TurnAction.ANSWER_OFF_TOPIC:
            return StateDelta(clear_fields=("pending_followup", "comparison_scope_option_names"))
        if action == TurnAction.SEARCH and execution.search:
            options = execution.search.shortlist(3)
            merged_params: dict[str, Any] = normalize_constraints_delta(execution.search.params)
            merged_params.update(normalize_constraints_delta(plan.constraints_delta))
            # A provider may echo all accumulated constraints. The delta is
            # only the change made on this turn, so refinement copy remains
            # truthful and state avoids needless rewrites.
            params_update = {
                key: value
                for key, value in merged_params.items()
                if state.params.get(key) != value
            }
            if _is_lookup_object(plan):
                selected = options[0] if options else None
                entity = SelectedEntity(selected.entity_type, selected.entity_id, selected.name) if selected and selected.entity_id is not None and selected.entity_type else None
                return StateDelta(
                    params_update=params_update,
                    visible_options=(selected,) if selected else (),
                    previous_options=state.visible_options,
                    last_search=execution.search,
                    selected_option_name=selected.name if selected else None,
                    selected_enriched=selected,
                    selected_entity=entity,
                    active_topic=topic_from_plan(plan.intent, merged_params) or state.active_topic,
                    dialog_focus=_dialog_focus_for_selected(plan, selected, state) if selected else state.dialog_focus,
                    clear_fields=("retry_search", "comparison_scope_option_names", "pending_action"),
                )
            return StateDelta(
                params_update=params_update,
                visible_options=options,
                previous_options=state.visible_options,
                last_search=execution.search,
                active_topic=topic_from_plan(plan.intent, merged_params) or state.active_topic,
                dialog_focus=DialogFocus(),
                enriched_card_cache=merge_enriched_card_cache_entries(
                    state.enriched_card_cache,
                    tuple(getattr(self.search_service, "last_shortlist_cache_entries", ())),
                ) if tuple(getattr(self.search_service, "last_shortlist_cache_entries", ())) else None,
                clear_fields=("retry_search", "comparison_scope_option_names", "selected_entity", "pending_action"),
            )
        if action == TurnAction.ANSWER_SELECTED_OPTION:
            params_update = normalize_constraints_delta(plan.constraints_delta)
            topic = topic_from_plan(plan.intent, params_update) or state.active_topic
            requested = {str(item).strip().lower() for item in (*plan.requested_facts, *plan.facts_needed)}
            selected_financing = "mortgage_terms" in requested or str(plan.intent or "").strip().lower() in {"mortgage", "financing"}
            entity = SelectedEntity(execution.selected.entity_type, execution.selected.entity_id, execution.selected.name) if execution.selected and execution.selected.entity_id is not None and execution.selected.entity_type else state.selected_entity
            pending_action = state.pending_action
            is_offer = selected_financing and entity is not None and execution.bridge_status is None
            if is_offer:
                key = f"verify-mortgage-{str(entity.entity_id)}"
                pending_action = PendingAction("verify_selected_facts", ("mortgage_terms",), entity.entity_type, entity.entity_id, "pending", key)
            clear = ["comparison_scope_option_names"]
            if execution.bridge_status:
                if pending_action:
                    # Confirmation is intentionally local to execution so a
                    # provider failure cannot expose an intermediate state.
                    # Persist the terminal status atomically with its result.
                    pending_action = replace(pending_action, status="completed" if execution.error_code is None else "cancelled")
            interrupted_contact = state.pending_followup if state.pending_followup in {"contact_name", "contact_phone"} else None
            capability_failed = bool(execution.bridge_status and execution.error_code)
            if execution.bridge_status and not capability_failed:
                clear.append("pending_followup")
            pending = (
                SELECTED_LIVE_FACT_CONSENT_FOLLOWUP
                if capability_failed
                else None if execution.bridge_status else interrupted_contact or (FINANCING_CONSENT_FOLLOWUP if selected_financing else _selected_live_fact_pending(plan, execution))
            )
            selected_enrichment_failure = bool(str(execution.error_code or "").startswith("selected_enrichment_") and pending == SELECTED_LIVE_FACT_CONSENT_FOLLOWUP)
            return StateDelta(
                selected_option_name=state.selected_option_name if execution.error_code == "selected_capability_entity_mismatch" else plan.selected_option_name,
                selected_enriched=execution.selected,
                selected_entity=entity,
                pending_action=pending_action,
                enriched_card_cache=merge_enriched_card_cache(
                    state.enriched_card_cache,
                    getattr(self.search_service, "last_enriched_cache_entry", None),
                ) if getattr(self.search_service, "last_enriched_cache_entry", None) is not None else None,
                params_update=params_update,
                active_topic="financing" if selected_financing else topic,
                pending_followup=pending,
                replaces_pending_offer=pending is not None,
                operator_offered=True if (selected_enrichment_failure or capability_failed) else False,
                contact_consent=False if (selected_enrichment_failure or capability_failed) else None,
                dialog_focus=_dialog_focus_for_selected(plan, execution.selected, state, execution.fresh_facts),
                clear_fields=tuple(clear),
            )
        if action == TurnAction.CLARIFY_SELECTED_LIVE_FACT:
            return StateDelta(pending_followup=SELECTED_LIVE_FACT_CONSENT_FOLLOWUP)
        if action == TurnAction.CLARIFY_FINANCING:
            params_update = normalize_constraints_delta(plan.constraints_delta)
            if plan.clarification:
                return StateDelta(params_update=params_update, active_topic="financing")
            return StateDelta(params_update=params_update, active_topic="financing", pending_followup=FINANCING_CONSENT_FOLLOWUP, replaces_pending_offer=True)
        if action == TurnAction.ANSWER_FROM_CURRENT_OPTIONS:
            params_update = normalize_constraints_delta(plan.constraints_delta)
            topic = topic_from_plan(plan.intent, params_update) or state.active_topic
            pair_cache_additions = tuple(execution.comparison_cache_additions or ())
            comparison_scope = _successful_explicit_pair(plan, execution, state)
            scope_clear_fields = () if comparison_scope else ("comparison_scope_option_names",)
            if pair_cache_additions:
                return StateDelta(
                    params_update=params_update,
                    active_topic=topic,
                    dialog_focus=_dialog_focus_for_current(plan, state),
                    enriched_card_cache=merge_enriched_card_cache_entries(state.enriched_card_cache, pair_cache_additions),
                    comparison_scope_option_names=comparison_scope,
                    clear_fields=scope_clear_fields,
                )
            if _is_current_options_financing_consent(plan, state):
                return StateDelta(
                    params_update=params_update,
                    active_topic="financing",
                    dialog_focus=_dialog_focus_for_current(plan, state),
                    pending_followup=FINANCING_CONSENT_FOLLOWUP,
                    replaces_pending_offer=True,
                    comparison_scope_option_names=comparison_scope,
                    clear_fields=scope_clear_fields,
                )
            if _is_current_fact_answer(plan) and _open_question_missing_facts(plan, state):
                return StateDelta(
                    params_update=params_update,
                    active_topic=topic,
                    dialog_focus=_dialog_focus_for_current(plan, state),
                    operator_offered=True,
                    pending_followup=SELECTED_LIVE_FACT_CONSENT_FOLLOWUP,
                    replaces_pending_offer=True,
                    contact_consent=False,
                    comparison_scope_option_names=comparison_scope,
                    clear_fields=scope_clear_fields,
                )
            return StateDelta(
                params_update=params_update,
                active_topic=topic,
                dialog_focus=_dialog_focus_for_current(plan, state),
                comparison_scope_option_names=comparison_scope,
                clear_fields=scope_clear_fields,
            )
        if action == TurnAction.OFFER_OPERATOR:
            return pending_delta_for_action(action, state)
        if action == TurnAction.ACCEPT_OPERATOR:
            return pending_delta_for_action(action, state)
        if action == TurnAction.DECLINE_OPERATOR:
            return pending_delta_for_action(action, state)
        return StateDelta()

    def _finalize_delta(self, delta: StateDelta, context: SafeTurnContext, response_text: str, answer_kind: str) -> StateDelta:
        question = response_text.split("?")[-2].split("\n")[-1].strip() + "?" if "?" in response_text else None
        return replace(
            delta,
            append_recent_turn={"user": context.user_text, "assistant": response_text},
            append_dialogue_turn={"user": context.user_text, "assistant": response_text},
            last_assistant_question=question,
            last_answer_kind=answer_kind,
            already_asked_add=(question,) if question else (),
            answered_add=(answer_kind,),
        )

async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _run_async_core_from_sync(factory: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    raise RuntimeError("TurnProcessor.process() cannot run inside an active event loop; use await process_async(...) instead")


def _safe_execution_error_code(exc: Exception) -> str:
    """Expose a bounded stage code for trace diagnostics, never raw provider text."""
    if isinstance(exc, RuntimeError):
        code = str(exc).split(":", 1)[0]
        if code in {
            "v2_search_gateway_not_ok",
            "v2_search_parse_failed",
            "v2_search_contract_invalid",
            "v2_search_retry_exhausted",
            "v2_low_level_gateway_missing",
        }:
            return code
    if isinstance(exc, TimeoutError):
        return "execution_timeout"
    return exc.__class__.__name__


def _response_composer_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"off", "shadow", "publish"} else "off"


_TEMP_FINANCE_UNKNOWN_SENTENCE_RE = re.compile(
    r"(?is)(?P<prefix>^|(?<=[.!?…])\s+|\n+\s*)"
    r"(?:к\s+сожалению,\s+)?"
    r"(?:"
    r"у\s+меня\s+нет\s+информации\s+о\s+финансовой\s+стороне\s+этих\s+предложений"
    r"|к\s+сожалению,\s+у\s+меня\s+нет\s+информации\s+о\s+финансовой\s+стороне\s+вопроса"
    r"|у\s+меня\s+нет\s+информации\s+по\s+финансовым\s+условиям"
    r"|у\s+меня\s+нет\s+информации\s+о\s+финансовых\s+условиях"
    r")"
    r"[ \t]*(?:\.(?=\s|$)|(?=$|\n))"
)


def _temporary_strip_repeated_finance_unknown_sentence(text: str) -> str:
    """Temporary cleanup for standalone finance-info model placeholders."""
    cleaned = str(text or "")
    while True:
        updated = _TEMP_FINANCE_UNKNOWN_SENTENCE_RE.sub(lambda m: m.group("prefix"), cleaned)
        if updated == cleaned:
            break
        cleaned = updated
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.!?…])", r"\1", cleaned)
    return cleaned.strip()


def _manager_rewriter_transcript(state: ConversationState, current_question: str) -> tuple[dict[str, str], ...]:
    turns = tuple(dict(x) for x in state.dialogue_turns)
    return (*turns, {"user": str(current_question or "")[:500], "assistant": ""})


def _runtime_response_composer_meta(meta: dict[str, Any], *, mode: str, published: bool, elapsed_ms: int) -> dict[str, Any]:
    status = str(meta.get("status") or ("primary" if meta.get("used") else "fallback")).strip().lower()
    used = bool(meta.get("used"))
    reason = meta.get("reason") if not used else None
    if not used and not reason:
        reason = "validation_failed"
    out = {
        "mode": mode,
        "used": used,
        "published": bool(published),
        "status": status[:40] or "fallback",
        "reason": str(reason or "")[:80] or None,
        "error_category": meta.get("error_category"),
        "error_code": meta.get("error_code"),
        "errors": list(meta.get("errors") or ())[:6] if isinstance(meta.get("errors"), (list, tuple)) else [],
        "warnings": list(meta.get("warnings") or ())[:6] if isinstance(meta.get("warnings"), (list, tuple)) else [],
        "pipeline": str(meta.get("pipeline") or "")[:40] or None,
        "attempts": _bounded_int(meta.get("attempts", 1), 1, 4),
        "elapsed_ms": _bounded_int(elapsed_ms, 0, 10 * 60 * 1000),
        "attempt_summaries": [dict(item) for item in meta.get("attempt_summaries", ())[:2]] if isinstance(meta.get("attempt_summaries"), (list, tuple)) else [],
    }
    semantic_diagnostics = _safe_semantic_diagnostics(meta.get("semantic_diagnostics"))
    if semantic_diagnostics:
        out["semantic_diagnostics"] = semantic_diagnostics
    provenance = sanitize_prompt_provenance(meta.get("prompt_provenance"))
    if provenance:
        out["prompt_provenance"] = provenance
    provider_meta = meta.get("provider_meta")
    if isinstance(provider_meta, dict):
        safe_provider_meta: dict[str, Any] = {}
        provider = str(provider_meta.get("provider") or "").strip().lower()
        reason_value = str(provider_meta.get("reason") or "").strip().lower()
        if provider in {"bluesminds", "gateway"}:
            safe_provider_meta["provider"] = provider
        if isinstance(provider_meta.get("fallback"), bool):
            safe_provider_meta["fallback"] = bool(provider_meta.get("fallback"))
        if reason_value in {"disabled", "empty", "exception", "none"}:
            safe_provider_meta["reason"] = reason_value
        if safe_provider_meta:
            out["provider_meta"] = safe_provider_meta
    return out


_SAFE_SEMANTIC_DIAGNOSTIC_STAGES = {"writer", "formatter"}
_SAFE_SEMANTIC_DIAGNOSTIC_CATEGORIES = {
    "numeric_not_in_canonical",
    "numeric_price_not_in_canonical",
    "numeric_transit_not_in_canonical",
    "numeric_area_not_in_canonical",
    "numeric_other_not_in_canonical",
    "sensitive_claim",
}


def _safe_semantic_diagnostics(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:2]:
        if not isinstance(item, Mapping) or str(item.get("stage") or "") not in _SAFE_SEMANTIC_DIAGNOSTIC_STAGES:
            continue
        categories = [str(category) for category in item.get("categories", ()) if str(category) in _SAFE_SEMANTIC_DIAGNOSTIC_CATEGORIES] if isinstance(item.get("categories"), (list, tuple)) else []
        if categories:
            out.append({"stage": str(item["stage"]), "categories": list(dict.fromkeys(categories))[:3]})
    return out


def _attach_response_model_usage(runtime_summary: dict[str, Any], *, response_meta: dict[str, Any], manager_rewriter_meta: dict[str, Any]) -> None:
    """Attach safe answer-stage model ids when the response path actually used a model."""
    models: list[str] = []
    for meta in (response_meta, manager_rewriter_meta):
        if not isinstance(meta, dict) or not meta.get("used"):
            continue
        summaries = meta.get("attempt_summaries") if isinstance(meta.get("attempt_summaries"), list) else []
        for item in summaries:
            if not isinstance(item, dict):
                continue
            model = _safe_short(item.get("model"))
            if model and model not in models:
                models.append(model)
    if not models:
        return
    usage = runtime_summary.get("model_usage") if isinstance(runtime_summary.get("model_usage"), dict) else {}
    usage["answer"] = models[:3]
    runtime_summary["model_usage"] = usage


def _decision_from_plan(plan: TurnPlan, state: ConversationState) -> TransitionDecision:
    if isinstance(plan, ExecutableTurn):
        return TransitionDecision(plan.stage, plan.action, accepted=plan.accepted, error_code=plan.error_code)
    return derive_transition(plan, state)


def _is_lookup_object(plan: TurnPlan) -> bool:
    return isinstance(plan, ExecutableTurn) and plan.goal == IntentGoal.LOOKUP_OBJECT or isinstance(plan, SemanticPlan) and plan.operation == "lookup_object"


def _is_current_fact_answer(plan: TurnPlan) -> bool:
    if isinstance(plan, ExecutableTurn):
        return plan.goal in {IntentGoal.ANSWER_CURRENT, IntentGoal.ANSWER_OPEN_QUESTION} and bool(plan.requested_facts or plan.facts_needed)
    return plan.operation == "answer_open_question"


def _dialog_focus_for_selected(plan: TurnPlan, selected: OptionCard | None, state: ConversationState, fresh_facts: tuple[str, ...] = ()) -> DialogFocus:
    action = str(plan.focus_action or "keep")
    if action == "clear":
        return DialogFocus()
    if action == "clarify":
        return state.dialog_focus
    subject = plan.resolved_subject or (state.dialog_focus.subject if action == "keep" else None)
    if not subject and not plan.requested_facts:
        return state.dialog_focus if state.selected_option_name == plan.selected_option_name else DialogFocus()
    return DialogFocus(
        subject=subject,
        last_intent=plan.resolved_intent or plan.intent or state.dialog_focus.last_intent,
        last_requested_facts=tuple(plan.requested_facts),
        last_answered_facts=answered_facts(plan.requested_facts, selected, fresh_facts=fresh_facts),
    )


def _selected_live_fact_pending(plan: TurnPlan, execution: ExecutionResult) -> str | None:
    from .fact_context import split_requested_facts

    requested = tuple(plan.requested_facts or plan.facts_needed)
    error_code = str(execution.error_code or "")
    if requested and error_code.startswith("selected_enrichment_"):
        return SELECTED_LIVE_FACT_CONSENT_FOLLOWUP
    if not requested:
        return None
    split = split_requested_facts(requested, execution.selected, fresh_facts=execution.fresh_facts)
    if split.missing:
        return SELECTED_LIVE_FACT_CONSENT_FOLLOWUP
    return None


def _is_current_options_financing_consent(plan: TurnPlan, state: ConversationState) -> bool:
    resolved = resolve_recipe(
        stage=Stage.CURRENT_OPTIONS,
        plan=plan,
        state=state,
        cards=tuple(state.visible_options[:3]),
    )
    return resolved.recipe.reply_contract_id == FINANCING_CONSENT_FOLLOWUP


def _dialog_focus_for_current(plan: TurnPlan, state: ConversationState) -> DialogFocus:
    action = str(plan.focus_action or "keep")
    if action == "clear":
        return DialogFocus()
    if action == "clarify":
        return state.dialog_focus
    selected = state.selected_enriched or state.find_visible_option(state.selected_option_name)
    subject = plan.resolved_subject or (state.dialog_focus.subject if action == "keep" else None)
    if not subject and not plan.requested_facts:
        return state.dialog_focus
    return DialogFocus(
        subject=subject,
        last_intent=plan.resolved_intent or plan.intent or state.dialog_focus.last_intent,
        last_requested_facts=tuple(plan.requested_facts),
        last_answered_facts=answered_facts(plan.requested_facts, selected),
    )


def _open_question_missing_facts(plan: TurnPlan, state: ConversationState) -> tuple[str, ...]:
    requested = tuple(dict.fromkeys(str(fact).strip().lower() for fact in (*plan.requested_facts, *plan.facts_needed) if fact))
    if not requested:
        return ()
    selected = state.find_visible_option(plan.selected_option_name) or state.selected_enriched
    cards = (selected,) if selected else tuple(state.visible_options[:3])
    if not cards:
        return requested
    return fact_availability(cards, requested).missing_facts


def _safe_fresh_facts(value: Any) -> tuple[str, ...]:
    from .fact_context import ALLOWED_FACTS
    raw = value if isinstance(value, (list, tuple, set)) else []
    out: list[str] = []
    for item in raw:
        fact = str(item or "").strip().lower()
        if fact in ALLOWED_FACTS and fact not in out:
            out.append(fact)
    return tuple(out)


def _safe_pair_status(value: Any) -> str | None:
    status = str(value or "").strip().lower()
    return status if status in {"partial_enrichment_failed", "all_enrichment_failed", "failed_closed"} else None


def _safe_pair_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("status", "requested_count", "resolved_count", "cache_hit_count", "fetch_count", "applied_count", "failure_count", "requested_fact_count"):
        if key not in value:
            continue
        if key == "status":
            status = str(value.get(key) or "").strip().lower()
            if status in {"ok", "partial_enrichment_failed", "all_enrichment_failed", "failed_closed"}:
                out[key] = status
        else:
            out[key] = _bounded_int(value.get(key), 0, 10)
    return out


_SAFE_SHORT_RE = re.compile(r"[^a-zA-Z0-9_.:-]")


def _runtime_summary(
    *,
    stage: Stage,
    action: TurnAction,
    plan: TurnPlan,
    answer_kind: str,
    timing_ms: dict[str, int],
    state_before: ConversationState,
    state_after: ConversationState,
    execution: ExecutionResult,
    response_text: str,
) -> dict[str, Any]:
    question_count = str(response_text or "").count("?")
    stripped = str(response_text or "").rstrip()
    attempts = [item for item in execution.attempts if isinstance(item, dict)]
    gateway_attempt_details = _gateway_attempt_details(attempts)
    search_empty = action == TurnAction.SEARCH and execution.ok and execution.search is not None and not execution.search.facts and not execution.search.near
    blockers: list[str] = []
    if not execution.ok or execution.error_code in {"unsupported_action", "search_service_missing"}:
        blockers.append("runtime_error")
    if question_count != 1:
        blockers.append("question_count_not_one")
    if question_count and not stripped.endswith("?"):
        blockers.append("final_question_not_at_end")
    if search_empty:
        blockers.append("search_without_cards")
    if str(execution.error_code or "").startswith("selected_enrichment_"):
        blockers.append("enrichment_error")
    summary = {
        "stage": stage.value,
        "action": action.value,
        "answer_kind": _safe_short(answer_kind),
        "timing_ms": {key: _bounded_int(timing_ms.get(key), 0, 10 * 60 * 1000) for key in ("planner", "execution", "response", "total")},
        "call_counts": {
            "planner": 1,
            "search": 1 if action == TurnAction.SEARCH else 0,
            "selected_enrichment": 1 if any(item.get("stage") == "v2_option_enrichment" for item in attempts) or str(execution.error_code or "").startswith("selected_enrichment_") else 0,
            "selected_capability": 1 if any(item.get("stage") == "selected_capability" for item in attempts) or _is_capability_status(execution.bridge_status) else 0,
            "gateway_attempts": min(len(attempts), 5),
        },
        "state_before": _state_summary(state_before),
        "state_after": _state_summary(state_after),
        "question_count": min(question_count, 20),
        "final_question_at_end": bool(question_count == 1 and stripped.endswith("?")),
        "quality_blockers": blockers[:5],
        "grounding_scope": "canonical_response_plan",
    }
    if gateway_attempt_details:
        summary["gateway_attempt_details"] = gateway_attempt_details
    if execution.comparison_cards:
        summary["call_counts"]["pair_enrichment"] = 1
    option_enrichment = _option_enrichment_runtime_summary(attempts)
    if option_enrichment:
        summary["option_enrichment"] = option_enrichment
    capability_outcome = _capability_outcome_runtime_summary(attempts, execution.bridge_status)
    if capability_outcome:
        summary["capability_outcome"] = capability_outcome
    if execution.comparison_metadata:
        summary["pair_comparison"] = _safe_pair_metadata(execution.comparison_metadata)
    model_usage = _model_usage_from_gateway_attempts(attempts)
    if model_usage:
        summary["model_usage"] = model_usage
    if isinstance(plan, ExecutableTurn):
        intent_transition = plan.trace_metadata.get("intent_transition") if isinstance(plan.trace_metadata, dict) else None
        if isinstance(intent_transition, dict):
            summary["intent_transition"] = intent_transition
    return summary


_CAPABILITY_STATUSES = frozenset({
    "selected_capability_prerequisite", "selected_capability_capability_missing", "selected_capability_entity_mismatch",
    "selected_capability_timeout", "selected_capability_transport", "selected_capability_provider", "selected_capability_parse",
    "selected_capability_rejected", "selected_capability_empty", "selected_capability_evidence_partial", "selected_capability_evidence_complete",
})
_CAPABILITY_EVIDENCE_STATUSES = frozenset({"complete", "partial", "empty", "rejected", "capability_missing", "prerequisite_missing", "unknown"})
_CAPABILITY_ROOT_STATES = frozenset({"active", "inactive", "missing", "ambiguous", "unknown"})


def _is_capability_status(value: Any) -> bool:
    return str(value or "") in _CAPABILITY_STATUSES


def _capability_attempt(request: Any, status: str, meta: Mapping[str, Any] | None = None, *, request_count: int = 0) -> dict[str, Any]:
    source = meta if isinstance(meta, Mapping) else {}
    return {
        "stage": "selected_capability", "status": status, "requested_facts": list(getattr(request, "fact_keys", ()) or ()),
        "request_count": request_count, "evidence_status": source.get("evidence_status"),
        "accepted_rows": source.get("accepted_rows", 0), "rejected_rows": source.get("rejected_rows", 0),
        "identity_match": source.get("identity_match"), "active_root": source.get("active_root"),
        "root_state": source.get("root_state"),
        "transport_class": source.get("transport_class"), "parse_class": source.get("parse_class"),
    }


def _capability_outcome_runtime_summary(attempts: list[dict[str, Any]], bridge_status: Any) -> dict[str, Any]:
    attempt = next((item for item in attempts if item.get("stage") == "selected_capability"), None)
    status = str((attempt or {}).get("status") or bridge_status or "")
    if not _is_capability_status(status):
        return {}
    facts = [str(item) for item in ((attempt or {}).get("requested_facts") or ()) if str(item) in FACT_KEY_SET][:8]
    evidence = str((attempt or {}).get("evidence_status") or "").removeprefix("evidence_")
    if status == "selected_capability_prerequisite": evidence = "prerequisite_missing"
    elif status == "selected_capability_capability_missing": evidence = "capability_missing"
    elif evidence not in _CAPABILITY_EVIDENCE_STATUSES: evidence = "unknown"
    out: dict[str, Any] = {"requested_facts": list(dict.fromkeys(facts)), "status": status, "request_count": _bounded_int((attempt or {}).get("request_count"), 0, 1), "evidence_status": evidence, "accepted_count": _bounded_int((attempt or {}).get("accepted_rows"), 0, 10), "rejected_count": _bounded_int((attempt or {}).get("rejected_rows"), 0, 10), "identity_match": (attempt or {}).get("identity_match") if isinstance((attempt or {}).get("identity_match"), bool) else None, "active_root": (attempt or {}).get("active_root") if isinstance((attempt or {}).get("active_root"), bool) else None, "root_state": (attempt or {}).get("root_state") if (attempt or {}).get("root_state") in _CAPABILITY_ROOT_STATES else "unknown"}
    for key, allowed in (("transport_class", {"gateway", "timeout", "transport", "provider"}), ("parse_class", {"structured", "invalid"})):
        if (attempt or {}).get(key) in allowed:
            out[key] = (attempt or {})[key]
    return out


def _option_enrichment_runtime_summary(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    enrichment = next((item for item in attempts if item.get("stage") == "v2_option_enrichment"), None)
    if not isinstance(enrichment, dict):
        return {}
    evidence = _availability_evidence_runtime_summary(enrichment.get("availability_evidence"))
    return {"availability_evidence": evidence} if evidence else {}


def _availability_evidence_runtime_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    confirmation = str(value.get("confirmation") or "").strip().lower()
    source = str(value.get("source") or "").strip().lower()
    out = {
        "requested": bool(value.get("requested")),
        "confirmation": confirmation if confirmation in {"not_requested", "confirmed", "not_confirmed"} else "not_confirmed",
        "source": source if source in {"gateway", "cache", "base", "unknown"} else "unknown",
    }
    task_id = _safe_short(value.get("gateway_task_id"))
    if task_id:
        out["gateway_task_id"] = task_id
    return out


def _gateway_attempt_details(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in attempts:
        if item.get("stage") != "gateway_attempt":
            continue
        attempt: dict[str, Any] = {"stage": "gateway_attempt"}
        model = _safe_short(item.get("model"))
        if model:
            attempt["model"] = model
        role = str(item.get("model_role") or "").strip().lower()
        if role in {"search", "answer"}:
            attempt["model_role"] = role
        for key in ("ok", "empty", "safe"):
            if isinstance(item.get(key), bool):
                attempt[key] = bool(item.get(key))
        task_id = _safe_short(item.get("gateway_task_id"))
        if task_id:
            attempt["gateway_task_id"] = task_id
        attempt["duration_ms"] = _bounded_int(item.get("duration_ms"), 0, 10 * 60 * 1000)
        parse_status = str(item.get("parse_status") or "").strip()
        if parse_status in {"ok", "invalid_json", "missing"}:
            attempt["parse_status"] = parse_status
        out.append(attempt)
        if len(out) >= 5:
            break
    return out


def _model_usage_from_gateway_attempts(attempts: list[dict[str, Any]]) -> dict[str, list[str]]:
    usage: dict[str, list[str]] = {}
    for item in attempts:
        if item.get("stage") != "gateway_attempt":
            continue
        role = str(item.get("model_role") or "").strip().lower()
        if role not in {"search", "answer"}:
            continue
        model = _safe_short(item.get("model"))
        if not model:
            continue
        bucket = usage.setdefault(role, [])
        if model not in bucket:
            bucket.append(model)
    return {key: value[:3] for key, value in usage.items() if value}


def _state_summary(state: ConversationState) -> dict[str, Any]:
    return {
        "param_keys": sorted(key for key in (_safe_param_key(item) for item in state.params.keys()) if key)[:20],
        "visible_options_count": min(len(state.visible_options), 20),
        "selected_present": bool(state.selected_option_name),
        "pending_followup": _safe_short(state.pending_followup),
        "active_topic": _safe_short(state.active_topic),
    }


def _successful_explicit_pair(
    plan: TurnPlan,
    execution: ExecutionResult,
    state: ConversationState,
) -> tuple[str, str] | None:
    """Persist only the exact visible pair that was actually rendered."""
    if not isinstance(plan, ExecutableTurn) or plan.goal != IntentGoal.COMPARE_CURRENT:
        return None
    names = tuple(plan.comparison_option_names)
    if len(names) != 2 or tuple(card.name for card in execution.comparison_cards) != names:
        return None
    if any(state.find_visible_option(name) is None for name in names):
        return None
    return names  # type: ignore[return-value]


def _safe_short(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return _SAFE_SHORT_RE.sub("_", text)[:80]


def _safe_param_key(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if any(part in text for part in ("phone", "тел", "email", "client", "chat", "token", "secret", "+7", "7999")):
        return None
    return _safe_short(text)


def _bounded_int(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(number, high))
