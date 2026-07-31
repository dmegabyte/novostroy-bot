from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from . import RUNTIME_VERSION
from .contracts import V1Action, V1AnswerKind, V1Error, V1Stage
from .execution_path import V1_PLANNER_STAGE_ID, V1_SEARCH_STAGE_ID, V1_TRANSITION_STAGE_ID, build_v1_execution_path
from .one_model_response import build_one_model_input, parse_one_model_response, response_model_bypass, validate_one_model_response
from .planner import build_planner_input, coerce_plan
from .prompt_provenance import merge_prompt_provenance
from .response import build_response_plan, render_response
from .search import build_search_request, parse_search_provider_result, safe_search_error
from .search_contract import project_public_card
from .state import V1ConversationState, redact_phone
from .transition import transition


@dataclass(frozen=True)
class V1TurnResult:
    runtime_version: str
    stage: str
    action: str
    answer_kind: str
    response_text: str
    state: dict[str, Any]
    trace: dict[str, Any]


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def run_turn(user_text: str, state_dict: dict[str, Any] | None, planner_port, search_port=None, presenter_port=None, journal_port=None, trace_port=None, presenter_mode: str = "off", response_model_port=None, response_model_mode: str = "off") -> V1TurnResult:
    state = V1ConversationState.from_dict(state_dict) if state_dict else V1ConversationState.clean()
    final_state = state
    safe_code = None
    planner_status = transition_status = search_status = response_plan_status = deterministic_render_status = presenter_status = "skipped"
    error_stage = None
    requested_presenter_mode = presenter_mode if presenter_mode in {"off", "shadow", "publish"} else "off"
    effective_presenter_mode = "shadow" if requested_presenter_mode == "publish" else requested_presenter_mode
    presenter_mode_reason = "presenter_publish_not_enabled_stage_a" if requested_presenter_mode == "publish" else None
    requested_response_model_mode = response_model_mode if response_model_mode in {"off", "shadow", "publish"} else "off"
    response_model_status = "off" if requested_response_model_mode == "off" else "skipped"
    response_model_trace: dict[str, Any] = {"mode": requested_response_model_mode, "status": response_model_status, "published": False}
    try:
        raw_plan = await _maybe_await(planner_port.plan(build_planner_input(user_text, state)))
        planner_status = "completed"
        plan = coerce_plan(raw_plan)
        tr = transition(state, plan)
        transition_status = "completed" if tr.accepted else "failed"
        search_result = None
        if tr.needs_search and not search_port:
            search_result = safe_search_error("missing_search_port")
            final_state = state
            safe_code = "missing_search_port"
            search_status = "failed"
            error_stage = V1_SEARCH_STAGE_ID
        elif tr.needs_search and search_port:
            try:
                request = build_search_request(state, plan)
                raw = await _maybe_await(search_port.search(request))
                search_result = parse_search_provider_result(raw, dict(request.hard_constraints))
                search_status = "completed"
            except V1Error:
                search_result = safe_search_error("search_validation_error")
                safe_code = "search_validation_error"
                search_status = "failed"
                error_stage = V1_SEARCH_STAGE_ID
            except Exception:
                search_result = safe_search_error("search_provider_error")
                safe_code = "search_provider_error"
                search_status = "failed"
                error_stage = V1_SEARCH_STAGE_ID
            if search_result.error_code:
                tr_state = state
            else:
                visible = tuple(project_public_card(c) for c in search_result.exact[:3]) or tuple(project_public_card(c) for c in search_result.near[:2])
                tr_state = V1ConversationState.from_dict({**state.to_dict(), "revision": state.revision + 1, "stage": tr.stage.value, "hard_constraints": dict(request.hard_constraints), "preferences": dict(request.preferences), "active_viewpoint": request.viewpoint, "visible_options": list(visible), "previous_option_refs": list(state.previous_option_refs) + [v["ref"] for v in visible], "last_search_summary": {"exact": len(search_result.exact), "near": len(search_result.near), "missing": list(search_result.missing)}, "pending_action": tr.action.value})
            final_state = tr_state
        else:
            final_state = tr.state if tr.accepted else state
        current_cards = final_state.visible_options if tr.action == V1Action.ANSWER_CURRENT else ()
        response_context = {"selected_project": final_state.selected_project, "selected_lot": final_state.selected_lot, "requested_facts": list(plan.requested_facts)}
        rp = build_response_plan(tr.answer_kind, search_result, tr.reason, current_cards=current_cards, action=tr.action, response_context=response_context)
        response_plan_status = "completed"
        text = render_response(rp)
        deterministic_render_status = "completed"
        if response_model_port and requested_response_model_mode != "off":
            if response_model_bypass(tr.action):
                response_model_trace.update({"status": "skipped", "reason": "terminal_contact_flow_bypass"})
            elif search_result and search_result.error_code:
                response_model_trace.update({"status": "skipped", "reason": "safe_error_bypass"})
            else:
                try:
                    model_input = build_one_model_input(user_text, final_state.to_dict(), rp, text)
                    candidate = _coerce_one_model_candidate(await _maybe_await(response_model_port.present(model_input)), model_input)
                    candidate_text = str(candidate.get("response") or "").strip() if isinstance(candidate, dict) else ""
                    response_model_trace.update({
                        "status": "valid",
                        "model": str(getattr(response_model_port, "model", ""))[:80],
                        "published": requested_response_model_mode == "publish" and bool(candidate_text),
                    })
                    if requested_response_model_mode == "publish" and candidate_text:
                        text = candidate_text
                except V1Error as exc:
                    response_model_trace.update({"status": "fallback", "reason": _safe_response_model_reason(exc), "published": False})
                except Exception:
                    response_model_trace.update({"status": "fallback", "reason": "provider_or_validation_failed", "published": False})
        elif response_model_port:
            response_model_trace.update({"status": "off"})
        if presenter_port and effective_presenter_mode == "shadow":
            try:
                await _maybe_await(presenter_port.present(rp.to_dict(), {"stage": final_state.stage.value}))
                presenter_status = "completed"
            except Exception:
                presenter_status = "fallback"
        elif presenter_port:
            presenter_status = "skipped"
        if search_result and search_result.error_code:
            final_state = state
            stage, action, answer_kind = V1Stage.SAFE_ERROR, V1Action.SAFE_ERROR, V1AnswerKind.SAFE_ERROR
        else:
            stage, action, answer_kind = tr.stage, tr.action, tr.answer_kind
    except Exception:
        stage, action, answer_kind = V1Stage.SAFE_ERROR, V1Action.SAFE_ERROR, V1AnswerKind.SAFE_ERROR
        text = "Сейчас не получилось безопасно обработать запрос. Попробуем ещё раз?"
        final_state = state
        safe_code = "runtime_safe_error"
        if planner_status != "completed":
            planner_status = "failed"
            error_stage = V1_PLANNER_STAGE_ID
        elif transition_status != "completed":
            transition_status = "failed"
            error_stage = V1_TRANSITION_STAGE_ID
    execution_path = build_v1_execution_path(
        planner_status=planner_status,
        transition_status=transition_status,
        search_status=search_status,
        response_plan_status=response_plan_status,
        deterministic_render_status=deterministic_render_status,
        presenter_status=presenter_status,
        runtime_finalize_status="completed",
        error_stage=error_stage,
        error_code=safe_code,
    )
    trace = {"runtime_version": RUNTIME_VERSION, "execution_path": execution_path, "stage": stage.value, "action": action.value, "input_redacted": redact_phone(user_text) != user_text, "presenter_requested_mode": requested_presenter_mode, "presenter_effective_mode": effective_presenter_mode}
    trace["response_model"] = {k: v for k, v in response_model_trace.items() if v is not None}
    prompt_provenance = merge_prompt_provenance(getattr(planner_port, "prompt_provenance", None), getattr(search_port, "prompt_provenance", None) if search_status != "skipped" else None, getattr(response_model_port, "prompt_provenance", None) if response_model_trace.get("status") in {"valid", "fallback"} else None)
    if prompt_provenance:
        trace["prompt_provenance"] = prompt_provenance
    if presenter_mode_reason:
        trace["presenter_mode_reason"] = presenter_mode_reason
    if safe_code:
        trace["safe_code"] = safe_code
    event = {"runtime_version": RUNTIME_VERSION, "stage": stage.value, "action": action.value, "answer_kind": answer_kind.value}
    for port in (trace_port, journal_port):
        if port:
            try:
                await _maybe_await(port.write({**event, "trace": trace} if port is trace_port else event))
            except Exception:
                pass
    return V1TurnResult(RUNTIME_VERSION, stage.value, action.value, answer_kind.value, text, final_state.to_dict(), trace)


def run_turn_sync(*args, **kwargs) -> V1TurnResult:
    import asyncio
    return asyncio.run(run_turn(*args, **kwargs))


def _coerce_one_model_candidate(raw: Any, model_input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return parse_one_model_response(raw, model_input)
    if set(raw) != {"response", "visible_options", "next_action"}:
        raise V1Error("wrong_keys")
    errors = validate_one_model_response(raw, model_input)
    if errors:
        raise V1Error("one_model_validation_failed:" + errors[0])
    return dict(raw)


def _safe_response_model_reason(exc: Exception) -> str:
    text = str(exc or "").strip()
    if text in {"invalid_json", "wrong_keys"}:
        return text
    prefix = "one_model_validation_failed:"
    if text.startswith(prefix):
        code = text[len(prefix):].split(":", 1)[0].strip()
        if code and all(ch.islower() or ch.isdigit() or ch == "_" for ch in code):
            return prefix + code[:80]
    return "provider_or_validation_failed"
