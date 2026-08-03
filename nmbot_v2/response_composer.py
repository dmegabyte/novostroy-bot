from __future__ import annotations

"""Изолированный quality-инструмент для проверки модельной переформулировки.

Боевой V2 runtime не импортирует этот модуль и отвечает через детерминированный
renderer. Публичные функции здесь сохранены для offline quality-gate и тестов.
"""

import json
import os
import re
import inspect
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from .card_normalizer import missing_text
from .constraints import normalize_constraints_delta
from .conversation import OPEN_QUESTION_HANDOFF_TEMPLATE, OPEN_QUESTION_OPERATOR_CONSENT_CTA
from .contracts import ComposedResponse, ExecutableTurn, ExecutionResult, IntentGoal, OptionCard, ResponseBrief, ResponsePlan, SemanticPlan, Stage, StateDelta, TurnPlan, to_jsonable
from .fact_context import fact_availability
from .response import render_response
from .scenario_field_mechanics import build_scenario_context
from .state import ConversationState
from .prompt_provenance import build_prompt_provenance, identity_from_path, sanitize_prompt_provenance


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v2_response_composer.txt"
WRITER_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v2_response_writer.txt"
V3_ANSWER_WRITER_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v3_answer_writer.txt"
FORMATTER_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v2_response_formatter.txt"
INTERNAL_RE = re.compile(
    r"\b(?:mcp|json|regex|traceback|openrouter|gateway|overmind|payload|diagnostics|facts\[|near\[|params|optioncard|enum)\b|```|[{}\[\]]|"
    r"безопасн\w*\s+контекст|без\s+карточек|личн\w*\s+данн\w*|подтвержд[её]нн\w*\s+(?:данн\w*|детал\w*)|"
    r"не\s+буду\s+считать\s+это\s+согласием|текущ\w*\s+контекст|сохран[её]нн\w*\s+данн\w*|сохран[её]нн\w*\s+факт\w*",
    re.I,
)
SENSITIVE_CLAIMS_RE = re.compile(
    r"доходност|доход\w*\s+от\s+(?:аренд|инвест)|окупаем|рост\s+цен|выраст|спрос|ликвидност|"
    r"(?:получен|принос|стабильн)\w*\s+доход|потенциальн\w*\s+арендатор|"
    r"привлекательн\w*\s+для\s+арендатор|быстр\w*\s+сдач\w*\s+в\s+аренд|"
    r"ставк[аи]\s*\d|\b\d+(?:[,.]\d+)?\s*%|гарант",
    re.I,
)
UNSUPPORTED_MARKETING_RE = re.compile(
    r"идеальн\w*\s+(?:жиль|кварт)|счастлив\w*\s+жизн|наслаждаться\s+комфорт|"
    r"широк\w*\s+выбор|найти\s+именно\s+то|комфортн\w*\s+жизн|"
    r"сразу\s+(?:переех|засел|оформ|приступ)|значительн\w*\s+сократ|"
    r"оформ(?:ить|лени\w*)\s+собственност|исключа\w*\s+риск|"
    r"объявлен\w*.{0,80}(?:есть|да[её]т|показыва\w*)\s+(?:выбор|возможност\w*\s+выбрать)",
    re.I,
)


def _unsupported_marketing_code(text: str) -> str | None:
    checks = (
        ("legal_ownership", r"сразу\s+оформ|оформ(?:ить|лени\w*)\s+собственност"),
        ("risk_elimination", r"исключа\w*\s+риск"),
        ("ads_choice", r"объявлен\w*.{0,80}(?:есть|да[её]т|показыва\w*)\s+(?:выбор|возможност\w*\s+выбрать)"),
        ("immediate_move", r"сразу\s+(?:переех|засел|приступ)|быстр\w*\s+(?:переезд|заселен)"),
        ("rental_or_income_inference", r"доход\w*\s+от\s+(?:аренд|инвест)|привлекательн\w*\s+для\s+арендатор|быстр\w*\s+сдач\w*\s+в\s+аренд|стабильн\w*\s+доход"),
        ("readiness_extrapolation", r"скоро\s+будет\s+готов|готов\w*\s+к\s+эксплуатац|ожидани\w*.{0,30}не\s+будет\s+долг"),
        ("empty_marketing", r"идеальн\w*\s+(?:жиль|кварт)|счастлив\w*\s+жизн|наслаждаться\s+комфорт|широк\w*\s+выбор|найти\s+именно\s+то|комфортн\w*\s+жизн|значительн\w*\s+сократ"),
    )
    for code, pattern in checks:
        if re.search(pattern, text, re.I):
            return code
    return None
RESPONSE_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "nmbot_v2_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "intro": {"type": "string"},
                "options": {
                    "type": "array",
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "facts": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["name", "facts", "description"],
                        "additionalProperties": False,
                    },
                },
                "recommendation": {"type": "string"},
                "missing_note": {"type": "string"},
                "final_question": {"type": "string"},
            },
            "required": ["intro", "options", "recommendation", "missing_note", "final_question"],
            "additionalProperties": False,
        },
    },
}
PROVIDER_RETRY_MODEL = "deepseek/deepseek-v4-flash"
_ALLOWLISTED_ERROR_CODES = {
    "empty_response",
    "invalid_json",
    "json_root_must_be_object",
    "response_empty",
    "composer_exception",
    "v2_response_gateway_once_missing",
    "gateway_not_ok",
    "upstream_error",
    "provider_invalid_argument",
    "corrupted_thought_signature",
    "choices_response_parse",
    "response_parse",
    "schema_unsupported",
    "validation_failed",
    "schema_required_field_missing",
    "schema_additional_properties",
    "schema_invalid_options",
    "recipe_card_directive_mismatch",
    "section_question_mark",
    "final_question_empty",
    "writer_empty",
    "formatter_schema_invalid",
    "formatter_card_order_mismatch",
    "formatter_card_count_mismatch",
    "formatter_card_text_empty",
    "formatter_project_name_introduced",
    "formatter_content_mismatch",
    "adapter_invalid_output",
    "adapter_exception",
}
_PROVIDER_CODES = {"provider_invalid_argument", "corrupted_thought_signature", "choices_response_parse", "response_parse"}


@dataclass(frozen=True)
class ComposerAttemptResult:
    status: str
    text: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    error_category: str | None = None
    error_code: str | None = None
    attempts: int = 1
    attempt_summaries: tuple[dict[str, Any], ...] = ()
    semantic_categories: tuple[str, ...] = ()
    prompt_provenance: Mapping[str, Any] | None = None

    def to_meta(self) -> dict[str, Any]:
        reason = None
        if self.status == "fallback":
            reason = "composer_error" if self.error_code == "composer_exception" else "validation_failed"
        meta = {
            "used": self.status in {"primary", "repaired", "provider_retry"},
            "status": self.status,
            "reason": reason,
            "repaired": self.status == "repaired",
            "error_category": self.error_category,
            "error_code": self.error_code,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "attempts": self.attempts,
            "attempt_summaries": [dict(item) for item in self.attempt_summaries],
        }
        if any(str(item.get("stage") or "") in {"writer", "formatter"} for item in self.attempt_summaries):
            meta["pipeline"] = "gemini_json_with_formatter_fallback"
        semantic_diagnostics = _semantic_validation_diagnostics(
            status=self.status,
            error_category=self.error_category,
            errors=self.errors,
            attempt_summaries=self.attempt_summaries,
            semantic_categories=self.semantic_categories,
        )
        if semantic_diagnostics:
            meta["semantic_diagnostics"] = semantic_diagnostics
        provenance = sanitize_prompt_provenance(self.prompt_provenance)
        if provenance:
            meta["prompt_provenance"] = provenance
        return meta


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def load_writer_prompt() -> str:
    return WRITER_PROMPT_PATH.read_text(encoding="utf-8")


def load_v3_answer_writer_prompt() -> str:
    return V3_ANSWER_WRITER_PROMPT_PATH.read_text(encoding="utf-8")


def load_formatter_prompt() -> str:
    return FORMATTER_PROMPT_PATH.read_text(encoding="utf-8")


def _prompt_identity(stage: str, source: str, path: Path, *, usage: str = "invoked") -> dict[str, Any]:
    return identity_from_path(stage, source, path, usage=usage)


def configured_response_prompt_provenance(*, usage: str = "configured", coverage: str = "configured_only") -> dict[str, Any]:
    return build_prompt_provenance(
        [
            _prompt_identity("response_composer", "prompts/v2_response_composer.txt", PROMPT_PATH, usage=usage),
            _prompt_identity("response_writer", "prompts/v2_response_writer.txt", WRITER_PROMPT_PATH, usage=usage),
            _prompt_identity("response_formatter", "prompts/v2_response_formatter.txt", FORMATTER_PROMPT_PATH, usage=usage),
        ],
        coverage=coverage,
    )


def build_response_brief(*, stage: Stage, plan: TurnPlan, execution: ExecutionResult, delta: StateDelta, state: ConversationState, response_plan: ResponsePlan) -> ResponseBrief:
    cards = tuple(response_plan.cards[:2]) if execution.comparison_cards else tuple(response_plan.cards[:3])
    if not cards and execution.search:
        cards = execution.search.shortlist(3)
    if execution.selected and not cards:
        cards = (execution.selected,)
    if not cards and state.visible_options:
        cards = tuple(state.visible_options[:3])
    viewpoint = response_plan.viewpoint or plan.intent or state.active_topic or "life"
    selected_name = state.selected_option_name or plan.selected_option_name
    if stage == Stage.FINANCING_CLARIFICATION and selected_name:
        selected_card = state.find_visible_option(selected_name) or state.selected_enriched
        if selected_card:
            cards = (selected_card,)
    cards = tuple(_project_card_for_viewpoint(card, viewpoint) for card in cards)
    selected_scope = "pair" if execution.comparison_cards else "one" if execution.selected or plan.selected_option_name else str(plan.scope or "all")
    current_scope = "pair" if execution.comparison_cards else "one" if state.selected_option_name else "all" if (cards or state.visible_options) else "unknown"
    claims = list(_allowed_claims(cards, viewpoint))
    active_viewpoint = response_plan.base_viewpoint or (state.active_topic if viewpoint == "financing" else None) or viewpoint
    if active_viewpoint in {"life", "family"}:
        claims.append("Для этого ответа запрещены выводы про арендаторов, арендный доход, спрос, ликвидность и инвестиционную привлекательность.")
    recipe_id, anchor_fact, _legacy_allowed_benefit, forbidden_inferences, cta_template = (
        response_plan.recipe_id,
        response_plan.anchor_fact,
        response_plan.allowed_benefit,
        response_plan.forbidden_inferences,
        response_plan.cta_template,
    )
    primary_scenario = _primary_scenario_for_model(viewpoint, response_plan, state)
    overlay = "financing" if str(viewpoint or "").strip().lower() in {"financing", "mortgage"} else None
    answer_goal = _answer_goal(stage, plan, execution, cards)
    presentation_scope = "pair" if execution.comparison_cards else "selected" if answer_goal in {"recommend_current", "answer_current_fact"} or execution.selected or plan.selected_option_name else "shortlist"
    requested = tuple(dict.fromkeys(str(fact).strip().lower() for fact in (*plan.requested_facts, *plan.facts_needed) if fact))
    client_requested = tuple(dict.fromkeys(str(fact).strip().lower() for fact in plan.requested_facts if fact))
    scenario_context = build_scenario_context(
        cards=cards,
        primary_scenario=primary_scenario,
        facets=tuple(getattr(plan, "facets", ()) or ()),
        overlay=overlay,
        presentation_scope=presentation_scope,
        requested_facts=requested,
    )
    available_facts: tuple[str, ...] = ()
    missing_facts: tuple[str, ...] = ()
    response_policy = ""
    operator_handoff_template = ""
    if _is_current_fact_answer(plan):
        availability = fact_availability(cards, client_requested)
        available_facts = tuple(fact for fact, count in availability.available_counts.items() if count > 0)
        missing_facts = availability.missing_facts
        if missing_facts:
            response_policy = "operator_consent_offer"
            operator_handoff_template = OPEN_QUESTION_HANDOFF_TEMPLATE
            cta_template = OPEN_QUESTION_OPERATOR_CONSENT_CTA
    client_priorities = _build_client_priorities(state, plan, viewpoint)
    dialogue_progress = _build_dialogue_progress(state, plan, missing_facts)
    selection_scope_context = _build_selection_scope(cards, selected_name, state, execution)
    card_guidance = _build_card_guidance(cards, scenario_context, missing_facts, client_priorities)
    decision_signals = _build_decision_signals(cards, scenario_context, client_priorities)
    safe_comparisons, allowed_conclusions = _build_safe_comparisons(cards, client_priorities)
    next_actions = _build_next_actions(response_policy, operator_handoff_template, cta_template, response_plan, state, dialogue_progress)
    cta_policy = _build_cta_policy(response_policy, operator_handoff_template, recipe_id, cta_template, response_plan.final_question, state.already_asked)
    return ResponseBrief(
        answer_goal=answer_goal,
        user_question=str(plan.query_text or ""),
        question_subject=str(plan.resolved_subject or ""),
        requested_facts=client_requested,
        available_facts=available_facts,
        missing_facts=missing_facts,
        response_policy=response_policy,
        operator_handoff_template=operator_handoff_template,
        response_viewpoint=viewpoint,
        base_viewpoint=response_plan.base_viewpoint or (state.active_topic if viewpoint == "financing" else None),
        acknowledgement=response_plan.acknowledgement,
        state_delta_summary=tuple(response_plan.changed_constraints),
        canonical_cards=cards,
        canonical_missing_summary=missing_facts,
        selected_scope=selected_scope,
        current_scope=current_scope,
        allowed_fact_fields=_allowed_fact_fields(cards),
        allowed_claims=tuple(claims),
        recent_safe_context=tuple(dict(x) for x in state.recent_turns[-3:]),
        scenario_context=scenario_context,
        recipe_id=recipe_id,
        anchor_fact=anchor_fact,
        allowed_benefit="",
        forbidden_inferences=forbidden_inferences,
        cta_template=cta_template,
        recipe_cards=_structural_recipe_cards(response_plan.recipe_cards),
        fallback_question=response_plan.final_question.rstrip("?.! ") + "?",
        client_priorities=client_priorities,
        safe_comparisons=safe_comparisons,
        allowed_conclusions=allowed_conclusions,
        dialogue_progress=dialogue_progress,
        selection_scope=selection_scope_context,
        card_guidance=card_guidance,
        decision_signals=decision_signals,
        next_actions=next_actions,
        cta_policy=cta_policy,
    )


def _primary_scenario_for_model(viewpoint: str, response_plan: ResponsePlan, state: ConversationState) -> str:
    raw = str(viewpoint or "").strip().lower()
    if raw in {"financing", "mortgage"}:
        raw = str(response_plan.base_viewpoint or state.active_topic or "life").strip().lower()
    return raw if raw in {"life", "family", "investment", "rental"} else "life"


def _structural_recipe_cards(items: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    out: list[dict[str, Any]] = []
    for item in items or ():
        out.append(
            {
                "card_name": str(item.get("card_name") or ""),
                "anchor_fact": str(item.get("anchor_fact") or ""),
                "card_mode": str(item.get("card_mode") or "bounded"),
            }
        )
    return tuple(out)


def _project_card_for_viewpoint(card: OptionCard, viewpoint: str) -> OptionCard:
    """Expose only facts relevant to the requested customer viewpoint.

    The canonical card remains complete in state. This projection keeps the
    prose model from treating technical counters as universal sales benefits.
    """

    updates: dict[str, Any] = {}
    if viewpoint != "investment":
        updates.update({"ads_count": None, "sales_count": None, "sales_date": None})
    if viewpoint != "financing":
        updates["discount"] = None
    return replace(card, **updates) if updates else card


def parse_composer_json(text: str | Mapping[str, Any]) -> tuple[ComposedResponse | None, list[str]]:
    if isinstance(text, Mapping):
        data = dict(text)
        schema_errors = _schema_errors(data)
        if schema_errors:
            return None, schema_errors
        composed = ComposedResponse.from_dict(data)
        return composed, []
    raw = str(text or "").strip()
    if not raw or raw.casefold() in {"none", "null"}:
        return None, ["empty_response"]
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.I | re.S)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, [f"invalid_json:{exc.msg}"]
    if not isinstance(data, dict):
        return None, ["json_root_must_be_object"]
    schema_errors = _schema_errors(data)
    if schema_errors:
        return None, schema_errors
    response = ComposedResponse.from_dict(data)
    return response, []


def validate_composed_response(composed: ComposedResponse, brief: ResponseBrief) -> list[str]:
    text = assemble_composed_response(composed, brief)
    errors: list[str] = []
    allowed_names = [card.name for card in brief.canonical_cards[:3]]
    directive_names = [str(item.get("card_name") or "") for item in brief.recipe_cards]
    shown_names = [option.name for option in composed.options]
    if len(shown_names) > 3:
        errors.append("too_many_cards")
    if any(name not in allowed_names for name in shown_names):
        errors.append("option_name_not_allowed")
    if shown_names != allowed_names[: len(shown_names)]:
        errors.append("option_order_mismatch")
    if directive_names and directive_names[: len(shown_names)] != shown_names:
        errors.append("recipe_card_directive_mismatch")
    for idx, option in enumerate(composed.options):
        if not option.name.strip() or not option.facts.strip() or not option.description.strip():
            errors.append("empty_option_section")
        if idx < len(brief.canonical_cards):
            card = brief.canonical_cards[idx]
            facts_text = option.facts.casefold().replace("ё", "е")
            if card.location and not _text_contains_fact(option.facts, card.location):
                errors.append("required_location_missing")
            if (card.price or card.price_min is not None) and not re.search(r"(?:цен|млн|руб|₽)", facts_text):
                errors.append("required_price_missing")
            if brief.response_viewpoint == "life" and card.ads_count is not None and re.search(r"объявлен|витрин", facts_text):
                if any((card.metro, card.ready, card.finishing, card.infrastructure, card.location, card.price, card.price_min)):
                    errors.append("irrelevant_ads_for_life")
            scenario_error = _scenario_description_error(option.description, card, brief.response_viewpoint, brief.base_viewpoint)
            if scenario_error:
                errors.append(scenario_error)
    if not composed.intro.strip():
        errors.append("intro_empty")
    if brief.answer_goal == "recommend_current" and not composed.recommendation.strip():
        errors.append("recommendation_required")
    if _missing_note_required(brief) and not composed.missing_note.strip():
        errors.append("missing_note_required")
    if brief.operator_handoff_template and composed.missing_note.strip() != brief.operator_handoff_template.strip():
        errors.append("operator_handoff_template_mismatch")
    if brief.response_viewpoint == "financing" and any(str(item).strip().lower() == "mortgage_terms" for item in brief.missing_facts):
        if not re.search(r"финанс|ипот|оплат|ставк|услов", composed.missing_note, re.I):
            errors.append("financing_missing_note_required")
    if not composed.final_question.strip():
        errors.append("final_question_empty")
    if brief.cta_template and composed.final_question.rstrip("?.! ") + "?" != brief.cta_template:
        errors.append("recipe_cta_mismatch")
    if brief.recipe_id in {"selected_financing", "current_options_financing"}:
        if re.search(r"как к вам обращаться|номер(?:\s+телефон)?|оставьте\s+(?:номер|телефон)", composed.final_question, re.I):
            errors.append("contact_before_financing_consent")
        if brief.recipe_id == "selected_financing" and len(brief.canonical_cards) != 1:
            errors.append("selected_financing_card_scope_invalid")
    if any("?" in part for part in _non_question_sections(composed)):
        errors.append("section_question_mark")
    if composed.final_question.count("?") != 1 or not composed.final_question.rstrip().endswith("?"):
        errors.append("question_count_not_one")
    unknown_names = [name for name in re.findall(r"ЖК\s+[«\"]([^»\"\n]+)[»\"]", text) if not _matches_allowed_name(name, allowed_names)]
    if unknown_names:
        errors.append("unknown_option_name")
    if _unknown_numbers(text, brief):
        errors.append("unknown_number_or_sensitive_claim")
    if INTERNAL_RE.search(text):
        errors.append("internal_or_raw_wire_leak")
    if SENSITIVE_CLAIMS_RE.search(text) and not _claim_allowed_by_brief(text, brief):
        errors.append("unsupported_sensitive_claim")
    marketing_code = _unsupported_marketing_code(text)
    if marketing_code or UNSUPPORTED_MARKETING_RE.search(text):
        errors.append("unsupported_marketing_claim:" + (marketing_code or "other"))
    if text.count("?") != 1:
        errors.append("question_count_not_one")
    if "?" in text and not text.rstrip().endswith("?"):
        errors.append("final_question_not_at_end")
    if composed.final_question and not text.rstrip().endswith(composed.final_question.rstrip("?.! ") + "?"):
        errors.append("final_question_contract_mismatch")
    if _requires_context_acknowledgement(brief) and not _has_context_acknowledgement(composed.intro, brief.acknowledgement):
        errors.append("missing_context_acknowledgement")
    if _has_duplicate_answer(text):
        errors.append("duplicate_answer")
    if _has_repeated_identical_benefit(text):
        errors.append("repeated_identical_benefit")
    return list(dict.fromkeys(errors))


def _is_current_financing_selection_first(brief: ResponseBrief | None) -> bool:
    return bool(
        brief
        and brief.recipe_id == "current_options_financing"
        and brief.response_viewpoint == "financing"
        and brief.cta_template == "По какому ЖК проверить условия ипотеки?"
    )


def _merge_intro_missing_note(intro: str, missing_note: str) -> str:
    intro = _normalize_text(intro)
    missing_note = _normalize_text(missing_note)
    if not intro:
        return missing_note
    if not missing_note or _name_token(missing_note) in _name_token(intro):
        return intro
    return f"{intro} {missing_note}"


def assemble_composed_response(composed: ComposedResponse, brief: ResponseBrief | None = None) -> str:
    parts: list[str] = []
    missing_note = _normalize_text(composed.missing_note)
    merge_missing_into_intro = _is_current_financing_selection_first(brief)
    intro = _merge_intro_missing_note(composed.intro, missing_note) if merge_missing_into_intro else _normalize_text(composed.intro)
    if intro:
        parts.append(intro)
    for idx, option in enumerate(composed.options[:3], start=1):
        card_lines = [f"{idx}. {option.name.strip()}"]
        if option.facts.strip():
            card_lines.append(option.facts.strip())
        if option.description.strip():
            card_lines.append(option.description.strip())
        parts.append("\n".join(card_lines))
    if composed.recommendation.strip():
        parts.append(composed.recommendation.strip())
    if missing_note and not merge_missing_into_intro:
        parts.append(missing_note)
    question = composed.final_question.strip().rstrip("?.! ") + "?" if composed.final_question.strip() else ""
    if question:
        parts.append(question)
    return "\n\n".join(parts).strip()


def request_payload(brief: ResponseBrief, *, prompt: str | None = None, model: str = "google/gemini-2.5-flash", repair_errors: tuple[str, ...] = ()) -> dict[str, Any]:
    payload = {"brief": _model_facing_brief_payload(brief)}
    if repair_errors:
        payload["repair_validation_errors"] = list(repair_errors)
        payload["repair_instructions"] = _repair_instructions(repair_errors)
    request = {
        "_payload_stage": "conversation_answer",
        "query": "V2_RESPONSE_BRIEF=" + json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\nВерни только строгий JSON ответа.",
        "service": "openrouter",
        "model": model,
        "system_prompt": prompt if prompt is not None else load_prompt(),
        "parameters": {
            "temperature": 0.25,
            "max_tokens": 1800,
        },
    }
    return request


def writer_request_payload(brief: ResponseBrief, *, prompt: str | None = None, model: str = "google/gemini-2.5-flash") -> dict[str, Any]:
    payload = {"brief": _model_facing_brief_payload(brief)}
    request = {
        "_payload_stage": "conversation_answer_writer",
        "query": "V2_RESPONSE_BRIEF=" + json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\nВерни только компактный JSON по схеме intro/cards/recommendation/missing_note/final_question без Markdown.",
        "service": "openrouter",
        "model": model,
        "system_prompt": prompt if prompt is not None else load_writer_prompt(),
        "parameters": {"temperature": 0.25, "max_tokens": 1800},
    }
    return request


def v3_answer_writer_prompt_identity(*, usage: str = "invoked") -> dict[str, Any]:
    return _prompt_identity("response_writer", "prompts/v3_answer_writer.txt", V3_ANSWER_WRITER_PROMPT_PATH, usage=usage)


def v3_answer_writer_request_payload(brief: ResponseBrief, *, prompt: str | None = None, model: str = "google/gemini-2.5-flash") -> dict[str, Any]:
    payload = {"answer_brief": build_v3_answer_brief_payload(brief)}
    request = {
        "_payload_stage": "conversation_answer_writer",
        "query": "V3_ANSWER_BRIEF=" + json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\nВерни только компактный JSON по схеме intro/cards/recommendation/missing_note/final_question без Markdown.",
        "service": "openrouter",
        "model": model,
        "system_prompt": prompt if prompt is not None else load_v3_answer_writer_prompt(),
        "parameters": {"temperature": 0.2, "max_tokens": 5000},
    }
    return request


def formatter_request_payload(
    writer_text: str,
    brief: ResponseBrief,
    *,
    prompt: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    formatter_model = model or os.getenv("NMBOT_RESPONSE_FORMATTER_MODEL") or "inclusionai/ling-2.6-flash"
    payload = {
        "writer_text": str(writer_text or ""),
        "expected_card_names": [card.name for card in brief.canonical_cards[:3]],
        "final_question": brief.cta_template or brief.fallback_question,
        "exact_cta_required": bool(brief.cta_template),
        "missing_note_required": _missing_note_required(brief),
        "schema": {
            "intro": "string",
            "cards": [{"name": "string", "text": "string"}],
            "recommendation": "string",
            "missing_note": "string",
            "final_question": "string",
        },
    }
    request = {
        "_payload_stage": "conversation_answer_formatter",
        "query": "V2_RESPONSE_FORMATTER_INPUT=" + json.dumps(payload, ensure_ascii=False, sort_keys=True),
        "service": "openrouter",
        "model": formatter_model,
        "system_prompt": prompt if prompt is not None else load_formatter_prompt(),
        "parameters": {"temperature": 0, "max_tokens": 1400},
    }
    return request


def _model_facing_brief_payload(brief: ResponseBrief) -> dict[str, Any]:
    """Serialize a shortlist without exposing alternative prose material.

    The full immutable brief remains available to local validators. For first
    shortlist prose, Gemini gets card identity/order plus the preselected
    scenario_context only, so it cannot pick unrelated OptionCard fields.
    """

    data = to_jsonable(brief)
    for key in ("client_priorities", "safe_comparisons", "allowed_conclusions", "dialogue_progress", "selection_scope", "card_guidance", "decision_signals", "next_actions", "cta_policy"):
        data.pop(key, None)
    scenario_context = data.get("scenario_context")
    if is_one_shot_composer_eligible(brief):
        data["canonical_cards"] = [{"name": card.name} for card in brief.canonical_cards]
        if brief.answer_goal == "present_search_results":
            data["user_question"] = ""
            data["state_delta_summary"] = []
        data["allowed_fact_fields"] = ["name"]
        data["allowed_claims"] = []
    return data


def build_v3_answer_brief_payload(brief: ResponseBrief) -> dict[str, Any]:
    """V3-only readable projection for answer wording.

    Built only from the already validated ResponseBrief. It intentionally avoids
    raw MCP/search payloads and keeps local validators authoritative.
    """

    safe_cards = [_v3_answer_card_payload(card) for card in brief.canonical_cards[:3]]
    return {
        "current_client_request": _redact_safe_text(brief.user_question, limit=500),
        "bounded_dialogue_context": _v3_safe_dialogue_context(brief.recent_safe_context),
        "human_readable_search_constraints": _v3_readable_constraints(brief),
        "canonical_found_cards": safe_cards,
        "confirmed_facts_evidence": _v3_confirmed_evidence(brief, safe_cards),
        "missing_facts": [_redact_safe_text(item, limit=120) for item in brief.missing_facts[:8]],
        "answer_goal": _v3_answer_goal_text(brief),
        **_v3_b_context_payload(brief),
        "schema": {
            "intro": "string",
            "cards": [{"name": "must exactly match canonical_found_cards[].name", "text": "client prose without heading"}],
            "recommendation": "string",
            "missing_note": "string",
            "final_question": "exactly one useful final question, or exact cta_template when provided",
        },
        "hard_rules": {
            "card_names_in_order": [card["name"] for card in safe_cards],
            "max_cards": 3,
            "cta_template": _redact_safe_text(brief.cta_template, limit=180),
            "exact_cta_required": _v3_exact_cta_required(brief),
            "fallback_question": _redact_safe_text(brief.fallback_question, limit=180),
            "operator_handoff_template": _redact_safe_text(brief.operator_handoff_template, limit=300),
            "exactly_one_question_policy": brief.exactly_one_question_policy,
            "allowed_claims": [_redact_safe_text(item, limit=240) for item in brief.allowed_claims[:8]],
            "forbidden_inferences": [_redact_safe_text(item, limit=240) for item in brief.forbidden_inferences[:8]],
        },
    }


def _build_client_priorities(state: ConversationState, plan: TurnPlan, viewpoint: str) -> dict[str, Any]:
    priorities: dict[str, Any] = {"viewpoint": str(viewpoint or "life")}
    if state.active_topic:
        priorities["active_topic"] = str(state.active_topic)
    requested = tuple(dict.fromkeys(str(item).strip().lower() for item in (*plan.requested_facts, *plan.facts_needed) if str(item).strip()))
    if requested:
        priorities["requested_facts"] = list(requested[:8])
    facets = tuple(dict.fromkeys(str(item).strip().lower() for item in getattr(plan, "facets", ()) if str(item).strip()))
    if facets:
        priorities["facets"] = list(facets[:8])
    safe_keys = {"budget", "min_price", "max_price", "price_min", "price_max", "rooms", "room", "district", "location", "metro", "area", "area_min", "area_max", "area_min_m2", "area_max_m2", "finishing", "ready", "deadline", "property_class", "mortgage", "financing", "down_payment", "installment", "parking", "purpose"}
    effective = dict(state.params)
    effective.update(normalize_constraints_delta(plan.constraints_delta))
    params: dict[str, Any] = {}
    for key, value in effective.items():
        normalized = str(key or "").strip().lower()
        cleaned = _sanitize_v3_context_value(value, depth=0)
        if normalized in safe_keys and cleaned not in (None, "", [], {}):
            params[normalized] = cleaned
    if params:
        priorities["confirmed_constraints"] = params
    ranked = _rank_client_criteria(requested, params)
    if ranked:
        priorities["ranked_criteria"] = ranked
        priorities["primary_focus"] = ranked[0]
    return priorities


def _rank_client_criteria(requested: tuple[str, ...], params: Mapping[str, Any]) -> list[str]:
    aliases = {"ready": "readiness", "readiness": "readiness", "готовность": "readiness", "срок сдачи": "readiness", "metro": "metro", "метро": "metro", "transport": "metro", "max_price": "budget", "min_price": "budget", "price_min": "budget", "price_max": "budget", "budget": "budget", "бюджет": "budget", "rooms": "rooms", "room": "rooms", "комнаты": "rooms", "комнатность": "rooms", "location": "location", "district": "location", "район": "location", "area": "area", "area_min": "area", "area_max": "area", "площадь": "area", "finishing": "finishing", "отделка": "finishing"}
    ranked: list[str] = []
    for item in requested:
        criterion = aliases.get(str(item).strip().lower(), str(item).strip().lower())
        if criterion and criterion not in ranked:
            ranked.append(criterion)
    for criterion, keys in (("budget", {"budget", "min_price", "max_price", "price_min", "price_max"}), ("rooms", {"rooms", "room"}), ("metro", {"metro"}), ("location", {"district", "location"}), ("area", {"area", "area_min", "area_max", "area_min_m2", "area_max_m2"}), ("readiness", {"ready", "deadline"}), ("finishing", {"finishing"})):
        if any(key in params for key in keys) and criterion not in ranked:
            ranked.append(criterion)
    return ranked[:8]


def _normalized_dialogue_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _normalized_priority_text(value: Any) -> str:
    tokens = re.findall(r"[a-zа-я0-9]+", _normalized_dialogue_text(value), flags=re.I)
    while tokens and tokens[0] in {"давай", "давайте", "хочу", "нужно", "мне", "пожалуйста", "тогда", "все", "же"}:
        tokens.pop(0)
    return " ".join(tokens)


def _build_dialogue_progress(state: ConversationState, plan: TurnPlan, missing_facts: tuple[str, ...]) -> dict[str, Any]:
    turns = [item for item in state.dialogue_turns if isinstance(item, Mapping) and any(str(item.get(key) or "").strip() for key in ("user", "assistant"))]
    current = _normalized_priority_text(plan.query_text)
    previous = next((_normalized_priority_text(item.get("user")) for item in reversed(turns) if _normalized_priority_text(item.get("user"))), "")
    repeated = bool(current and previous and (current == previous or (min(len(current), len(previous)) >= 6 and (current in previous or previous in current))))
    status = "operator_offered" if state.operator_offered else "unconfirmed_fact_requires_handoff" if missing_facts else "stalled" if repeated else "active"
    result: dict[str, Any] = {"substantive_turn_number": len(turns) + 1, "progress_status": status, "questions_already_asked": list(state.already_asked[-6:])}
    if state.last_assistant_question:
        result["last_assistant_question"] = state.last_assistant_question
    if repeated:
        result["repeated_priority"] = _redact_safe_text(str(plan.query_text or ""), limit=160)
    return result


def _build_selection_scope(cards: tuple[OptionCard, ...], selected_name: str | None, state: ConversationState, execution: ExecutionResult) -> dict[str, Any]:
    names = [card.name for card in cards]
    allowed = {_normalized_dialogue_text(name): name for name in names}
    selected = allowed.get(_normalized_dialogue_text(selected_name)) if selected_name else None
    pair = tuple(card.name for card in execution.comparison_cards[:2]) if len(execution.comparison_cards) >= 2 else state.comparison_scope_option_names
    exact_pair = list(pair) if len(pair) == 2 and all(_normalized_dialogue_text(name) in allowed for name in pair) else []
    result: dict[str, Any] = {"type": "pair" if exact_pair else "selected" if selected else "shortlist", "current_card_names": names}
    if selected:
        result["selected_card"] = selected
    if exact_pair:
        result["persisted_comparison_pair"] = exact_pair
    return result


def _priority_evidence(card: OptionCard, criterion: str) -> Any:
    value = {"readiness": card.ready, "metro": card.metro, "budget": card.price_min, "rooms": card.rooms, "location": card.location or card.district, "area": card.area, "finishing": card.finishing}.get(criterion)
    return value if value not in (None, "") else None


def _evidence_key(value: Any) -> str:
    return _normalized_dialogue_text(value)


def _build_card_guidance(cards: tuple[OptionCard, ...], scenario_context: Mapping[str, Any], missing_facts: tuple[str, ...], client_priorities: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], ...]:
    ranked = (client_priorities or {}).get("ranked_criteria")
    if isinstance(ranked, list):
        for criterion in ranked[:8]:
            values = [_priority_evidence(card, str(criterion)) for card in cards]
            comparable = [value for value in values if value is not None]
            if comparable and len({_evidence_key(value) for value in comparable}) >= 2:
                return tuple({"card_name": card.name, "anchor_fact": str(criterion), "communication_goal": "address_client_priority", "evidence": {"criterion": str(criterion), "value": value}, "missing_facts": list(missing_facts[:8]) if len(cards) == 1 else []} for card, value in zip(cards, values) if value is not None)
    scenario_cards = scenario_context.get("cards") if isinstance(scenario_context.get("cards"), list) else []
    by_name = {_normalized_dialogue_text(item.get("card_name")): item for item in scenario_cards if isinstance(item, Mapping)}
    out: list[dict[str, Any]] = []
    for card in cards:
        item = by_name.get(_normalized_dialogue_text(card.name), {})
        guidance: dict[str, Any] = {"card_name": card.name, "missing_facts": list(missing_facts[:8]) if len(cards) == 1 else []}
        for key in ("anchor_fact", "communication_goal", "allowed_concepts", "evidence"):
            value = _sanitize_v3_context_value(item.get(key), depth=0)
            if value not in (None, "", [], {}):
                guidance["allowed_benefits" if key == "allowed_concepts" else key] = value
        out.append(guidance)
    return tuple(out)


def _build_decision_signals(cards: tuple[OptionCard, ...], scenario_context: Mapping[str, Any], client_priorities: Mapping[str, Any] | None = None) -> dict[str, Any]:
    signals: dict[str, Any] = {}
    priced = [card for card in cards if isinstance(card.price_min, (int, float)) and not isinstance(card.price_min, bool)]
    if cards and len(priced) == len(cards):
        winner = min(priced, key=lambda card: card.price_min)
        signals["literal_lowest_starting_price"] = {"card_name": winner.name, "price_min": winner.price_min}
    anchors = [{key: item[key] for key in ("card_name", "anchor_fact", "allowed_benefits") if key in item} for item in _build_card_guidance(cards, scenario_context, (), client_priorities)]
    if anchors:
        signals["card_anchors"] = anchors
    return signals


def _pedestrian_minutes(value: Any) -> int | None:
    text = str(value or "").strip().casefold().replace("ё", "е")
    if not re.search(r"пеш|ходьб", text):
        return None
    match = re.search(r"\b(\d{1,3})\s*(?:мин(?:ут[аы]?|\.)?)\b", text)
    return int(match.group(1)) if match else None


def _has_matching_room_price_evidence(cards: tuple[OptionCard, ...], constraints: Mapping[str, Any]) -> bool:
    wanted = _evidence_key(constraints.get("rooms", constraints.get("room")))
    if not wanted:
        return False
    for card in cards:
        if any(_evidence_key(lot.rooms) == wanted and isinstance(lot.full_price, (int, float)) and not isinstance(lot.full_price, bool) for lot in card.lot_examples):
            return True
        for item in card.room_prices:
            price = item.get("full_price", item.get("price", item.get("price_min"))) if isinstance(item, Mapping) else None
            if isinstance(item, Mapping) and _evidence_key(item.get("rooms", item.get("room"))) == wanted and isinstance(price, (int, float)) and not isinstance(price, bool):
                return True
    return False


def _build_safe_comparisons(cards: tuple[OptionCard, ...], client_priorities: Mapping[str, Any]) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    comparisons: list[dict[str, Any]] = []
    conclusions: list[dict[str, Any]] = []
    ready = [str(card.ready).strip() for card in cards if str(card.ready or "").strip()]
    if cards and len(ready) == len(cards) and len({_evidence_key(value) for value in ready}) == 1:
        comparisons.append({"type": "shared_readiness", "card_names": [card.name for card in cards], "ready": ready[0]})
        conclusions.append({"type": "shared_readiness", "conclusion": f"Все показанные ЖК имеют статус готовности «{ready[0]}».", "forbidden": ["keys", "immediate_move", "no_wait", "specific_apartment_readiness"]})
    routes = [{"card_name": card.name, "metro": str(card.metro).strip(), "minutes": minutes} for card in cards if (minutes := _pedestrian_minutes(card.metro)) is not None]
    if cards and len(routes) == len(cards):
        winner = min(routes, key=lambda route: route["minutes"])
        comparisons.append({"type": "shortest_pedestrian_metro", "routes": routes, "winner": dict(winner)})
        conclusions.append({"type": "shortest_pedestrian_metro", "conclusion": f"Самый короткий подтверждённый пеший маршрут среди показанных вариантов — {winner['minutes']} минут в {winner['card_name']}.", "forbidden": ["rounded_minutes", "approximate_minutes"]})
    priced = [card for card in cards if isinstance(card.price_min, (int, float)) and not isinstance(card.price_min, bool)]
    if cards and len(priced) == len(cards):
        winner = min(priced, key=lambda card: card.price_min)
        comparisons.append({"type": "lowest_project_starting_price", "prices": [{"card_name": card.name, "price_min": card.price_min} for card in priced], "winner": {"card_name": winner.name, "price_min": winner.price_min}, "scope": "project_starting_price"})
        conclusions.append({"type": "lowest_project_starting_price", "conclusion": f"Самая низкая стартовая цена ЖК среди показанных вариантов — у {winner.name}.", "scope": "project_starting_price", "limitations": ["does_not_prove_requested_room_availability", "does_not_prove_requested_room_price", "does_not_prove_budget_fit"]})
    constraints = client_priorities.get("confirmed_constraints")
    if isinstance(constraints, Mapping) and (any(key in constraints for key in ("max_price", "budget")) or any(key in constraints for key in ("rooms", "room"))) and not _has_matching_room_price_evidence(cards, constraints):
        conclusions.append({"type": "matching_room_budget_fit", "status": "unknown", "reason": "no_room_specific_matching_unit_price_evidence", "forbidden_conclusion": "all_options_fit_budget"})
    return tuple(comparisons[:4]), tuple(conclusions[:4])


def _build_next_actions(response_policy: str, operator_template: str, cta_template: str, response_plan: ResponsePlan, state: ConversationState, dialogue_progress: Mapping[str, Any]) -> dict[str, Any]:
    stalled = dialogue_progress.get("progress_status") == "stalled"
    preferred = "offer_operator" if operator_template or response_policy == "operator_consent_offer" or (stalled and not state.operator_offered) else _redact_safe_text(cta_template or response_plan.final_question, limit=180) if cta_template or response_plan.final_question else "continue_dialogue"
    return {"preferred": preferred, "operator_already_offered": bool(state.operator_offered), "cta": cta_template or response_plan.final_question}


def _build_cta_policy(response_policy: str, operator_handoff_template: str, recipe_id: str, cta_template: str, fallback_question: str, already_asked: tuple[str, ...]) -> dict[str, Any]:
    return {"exact_required": bool(operator_handoff_template or response_policy == "operator_consent_offer" or recipe_id in {"selected_financing", "current_options_financing"}), "exact_cta": cta_template, "fallback_question": fallback_question.rstrip("?.! ") + "?", "one_question": True, "must_advance": True, "do_not_repeat_already_asked": list(already_asked[-6:])}


def _v3_b_context_payload(brief: ResponseBrief) -> dict[str, Any]:
    cta_policy = dict(brief.cta_policy)
    cta_policy["exact_required"] = _v3_exact_cta_required(brief)
    clean = lambda value: _sanitize_v3_context_value(value, depth=-2)
    return {"client_priorities": clean(brief.client_priorities) or {}, "safe_comparisons": clean(brief.safe_comparisons) or [], "allowed_conclusions": clean(brief.allowed_conclusions) or [], "dialogue_progress": clean(brief.dialogue_progress) or {}, "selection_scope": clean(brief.selection_scope) or {}, "card_guidance": clean(brief.card_guidance) or [], "decision_signals": clean(brief.decision_signals) or {}, "next_actions": clean(brief.next_actions) or {}, "cta_policy": clean(cta_policy) or {}}


def _v3_answer_card_payload(card: OptionCard) -> dict[str, Any]:
    fields: list[tuple[str, Any]] = [
        ("location", card.location),
        ("district", card.district),
        ("metro", card.metro),
        ("price", card.price),
        ("price_min", _format_v3_money(card.price_min) if card.price_min is not None else None),
        ("rooms", _format_v3_rooms(card.rooms)),
        ("area", card.area),
        ("finishing", card.finishing),
        ("ready", card.ready),
        ("infrastructure", _bounded_text_list(card.infrastructure, limit=5)),
        ("daily_services", _bounded_text_list(card.daily_services, limit=4)),
        ("transport_access", _bounded_text_list(card.transport_access, limit=4)),
        ("property_class", card.property_class),
        ("parking", card.parking),
        ("apartment_inventory", card.apartment_inventory),
        ("mortgage_terms", card.mortgage_terms),
        ("discount", card.discount),
        ("ads_count", card.ads_count),
        ("sales_count", card.sales_count),
        ("sales_date", card.sales_date),
        ("why_close", card.why_close),
    ]
    facts = {key: _redact_safe_value(value) for key, value in fields if value not in (None, "", [], {}, ())}
    return {"name": _redact_safe_text(card.name, limit=160), "facts": facts}


def _v3_safe_dialogue_context(items: tuple[Mapping[str, Any], ...]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in items[-3:]:
        if not isinstance(item, Mapping):
            continue
        safe: dict[str, str] = {}
        for key in ("user", "assistant"):
            text = _redact_safe_text(str(item.get(key) or ""), limit=280)
            if text:
                safe[key] = text
        if safe:
            out.append(safe)
    return out


def _v3_readable_constraints(brief: ResponseBrief) -> list[str]:
    phrases: list[str] = [_redact_safe_text(item, limit=160) for item in brief.state_delta_summary if str(item or "").strip()]
    scenario_context = brief.scenario_context if isinstance(brief.scenario_context, Mapping) else {}
    for key in ("search_principle", "request_summary", "communication_goal"):
        text = _redact_safe_text(str(scenario_context.get(key) or ""), limit=240)
        if text:
            phrases.append(text)
    if not phrases and brief.requested_facts:
        phrases.append("Клиент просит проверить: " + ", ".join(_redact_safe_text(item, limit=80) for item in brief.requested_facts[:6]))
    return list(dict.fromkeys(item for item in phrases if item))[:8]


def _v3_confirmed_evidence(brief: ResponseBrief, safe_cards: list[dict[str, Any]]) -> dict[str, Any]:
    scenario_context = brief.scenario_context if isinstance(brief.scenario_context, Mapping) else {}
    evidence: dict[str, Any] = {
        "available_facts": [_redact_safe_text(item, limit=100) for item in brief.available_facts[:8]],
        "card_fact_fields": list(brief.allowed_fact_fields[:16]),
        "cards": safe_cards,
    }
    for key in ("shared_facts", "base_facts", "anchor_fact", "evidence", "detail_facts"):
        value = _sanitize_v3_context_value(scenario_context.get(key), depth=0)
        if value not in (None, "", [], {}):
            evidence[key] = value
    if brief.anchor_fact:
        evidence["answer_anchor_fact"] = _redact_safe_text(brief.anchor_fact, limit=180)
    return evidence


def _v3_answer_goal_text(brief: ResponseBrief) -> dict[str, str]:
    goal_map = {
        "present_search_results": "Показать найденные варианты и помочь клиенту выбрать, что смотреть подробнее.",
        "recommend_current": "Дать аккуратную рекомендацию по текущим подтверждённым вариантам.",
        "answer_selected": "Ответить по выбранному ЖК только на подтверждённых фактах.",
        "answer_selected_option_from_confirmed_card": "Ответить по выбранному ЖК из уже подтверждённой карточки.",
    }
    return {
        "code": str(brief.answer_goal or ""),
        "client_goal": goal_map.get(str(brief.answer_goal or ""), "Ответить коротко и безопасно по текущему контексту."),
        "viewpoint": str(brief.response_viewpoint or "life"),
        "scope": str(brief.selected_scope or brief.current_scope or "unknown"),
    }


def _sanitize_v3_context_value(value: Any, *, depth: int) -> Any:
    if depth > 2:
        return None
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key or "").strip()
            if not key_text or _unsafe_v3_key(key_text):
                continue
            cleaned = _sanitize_v3_context_value(nested, depth=depth + 1)
            if cleaned not in (None, "", [], {}):
                safe[key_text[:80]] = cleaned
        return safe
    if isinstance(value, (list, tuple)):
        out = []
        for item in value[:8]:
            cleaned = _sanitize_v3_context_value(item, depth=depth + 1)
            if cleaned not in (None, "", [], {}):
                out.append(cleaned)
        return out
    return _redact_safe_value(value)


def _unsafe_v3_key(key: str) -> bool:
    return bool(re.search(r"phone|телефон|contact|email|token|secret|api[_-]?key|payload|mcp|raw|debug|trace|headers?", key, re.I))


def _redact_safe_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple)):
        return [_redact_safe_value(item) for item in value[:8] if item not in (None, "", [], {}, ())]
    return _redact_safe_text(str(value or ""), limit=240)


def _redact_safe_text(text: str, *, limit: int) -> str:
    value = str(text or "").strip()
    value = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[контакт скрыт]", value)
    value = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[контакт скрыт]", value, flags=re.I)
    value = re.sub(r"\b(?:mcp|raw[_ -]?payload|payload|api[_ -]?key|token|secret|traceback|headers?)\b", "[внутреннее скрыто]", value, flags=re.I)
    value = " ".join(value.split())
    return value[:limit].rstrip()


def _bounded_text_list(items: tuple[Any, ...], *, limit: int) -> list[str]:
    return [_redact_safe_text(str(item), limit=120) for item in items[:limit] if str(item or "").strip()]


def _format_v3_money(value: int | float | None) -> str | None:
    if value is None:
        return None
    if value >= 1_000_000:
        rendered = f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"от {rendered} млн ₽"
    return f"{int(value):,} ₽".replace(",", " ")


def _format_v3_rooms(value: Any) -> str | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    return {1: "однокомнатная квартира", 2: "двухкомнатная квартира", 3: "трёхкомнатная квартира", 4: "четырёхкомнатная квартира", "studio": "студия", "студия": "студия"}.get(value, str(value))


def is_one_shot_composer_eligible(brief: ResponseBrief) -> bool:
    """True only for identity-narrowed scenario-context-only answer payloads."""

    allowed_goals = {"present_search_results", "recommend_current", "answer_selected", "answer_selected_option_from_confirmed_card"}
    scenario_context = brief.scenario_context if isinstance(brief.scenario_context, Mapping) else {}
    return brief.answer_goal in allowed_goals and scenario_context.get("content_source") == "scenario_context_only"


def _repair_instructions(errors: tuple[str, ...]) -> list[str]:
    """Translate validator codes into bounded semantic correction rules."""
    instructions: list[str] = []
    for error in errors:
        code = str(error or "")
        if code.startswith("unsupported_marketing_claim:immediate_move"):
            instructions.append("Убери обещания быстрого переезда, заселения, ремонта или перехода к сделке; готовность означает только отсутствие ожидания окончания строительства.")
        elif code.startswith("unsupported_marketing_claim:"):
            instructions.append("Убери рекламный вывод и оставь только буквальную практическую пользу подтверждённого факта.")
        elif code == "unsupported_sensitive_claim":
            instructions.append("Убери выводы о спросе, доходности, ликвидности, росте цены, результате сделки или доступности квартиры, если соответствующего поля нет в карточке.")
        elif code in {"required_location_missing", "required_price_missing"}:
            instructions.append("Верни в facts обязательные подтверждённые локацию и цену карточки.")
        elif code in {"unknown_number_or_sensitive_claim", "internal_or_raw_wire_leak"}:
            instructions.append("Используй только значения из canonical_cards и не показывай внутренние названия полей.")
        elif code in {"missing_note_required", "financing_missing_note_required"}:
            instructions.append("Добавь короткую честную оговорку по canonical_missing_summary без внутренних терминов.")
        elif code == "repeated_identical_benefit":
            instructions.append("Выбери для карточек разные подтверждённые акценты, не повторяя одну и ту же пользу.")
        elif code == "scenario_fact_benefit_missing":
            instructions.append("Свяжи description с подтверждённым фактом текущего viewpoint: life — ежедневный маршрут, ожидание стройки, подготовка квартиры или бытовая инфраструктура; family — школа, сад, двор, прогулки или семейный бюджет; rental — формат, подготовка квартиры, срок или маршрут без выводов о спросе и доходе; investment — порог входа, срок, подготовка, ЕГРН или буквальный счётчик объявлений.")
        elif code == "scenario_viewpoint_mismatch":
            instructions.append("Убери пользу из другого сценария и объясни карточку только с позиции response_viewpoint/base_viewpoint и подтверждённых фактов.")
    return list(dict.fromkeys(instructions))[:6]


def _scenario_description_error(description: str, card: OptionCard, viewpoint: str, base_viewpoint: str | None) -> str | None:
    text = str(description or "").casefold().replace("ё", "е")
    vp = base_viewpoint if viewpoint == "financing" and base_viewpoint else viewpoint
    if vp == "life" and re.search(r"арендатор|доход|инвестиц|окупаем|ликвидност|спрос", text):
        return "scenario_viewpoint_mismatch"
    if vp == "family" and re.search(r"арендатор|доход|окупаем|ликвидност|спрос", text):
        return "scenario_viewpoint_mismatch"
    has_relevant_evidence = {
        "investment": any((card.price, card.price_min is not None, card.ready, card.finishing, card.sales_count is not None, card.ads_count is not None)),
        "rental": any((card.room_formats, card.area, card.finishing, card.ready, card.metro, card.location)),
        "family": any((card.infrastructure, card.price, card.price_min is not None, card.ready, card.finishing)),
        "life": any((card.metro, card.location, card.ready, card.finishing, card.infrastructure, card.price, card.price_min is not None)),
    }.get(vp, False)
    if not has_relevant_evidence:
        return None
    anchors = {
        "investment": (
            ((card.price is not None or card.price_min is not None) and bool(re.search(r"цен|бюджет|порог|вход", text)))
            or (bool(card.ready) and bool(re.search(r"срок|горизонт|ожидан|строительств", text)))
            or (bool(card.finishing) and bool(re.search(r"отделк|подготов|ремонт", text)))
            or (card.sales_count is not None and bool(re.search(r"егрн|продаж|сдел", text)))
            or (card.ads_count is not None and bool(re.search(r"объявлен|витрин|счетчик", text)))
        ),
        "rental": (
            (bool(card.room_formats or card.area) and bool(re.search(r"формат|площад|планиров|бюджет", text)))
            or (bool(card.finishing) and bool(re.search(r"отделк|подготов|ремонт|меблиров", text)))
            or (bool(card.ready) and bool(re.search(r"срок|ожидан|строительств", text)))
            or (bool(card.metro or card.location) and bool(re.search(r"метро|маршрут|дорог|поезд|локац|располож", text)))
        ),
        "family": (
            (bool(card.infrastructure) and bool(re.search(r"школ|сад|двор|дет|парк|прогул|инфраструктур|маршрут", text)))
            or ((card.price is not None or card.price_min is not None) and bool(re.search(r"цен|бюджет", text)))
            or (bool(card.ready) and bool(re.search(r"срок|ожидан|строительств|переезд", text)))
            or (bool(card.finishing) and bool(re.search(r"отделк|ремонт|подготов", text)))
        ),
        "life": (
            (bool(card.metro or card.location) and bool(re.search(r"метро|маршрут|дорог|поезд|локац|располож", text)))
            or (bool(card.ready) and bool(re.search(r"срок|ожидан|строительств|ключ", text)))
            or (bool(card.finishing) and bool(re.search(r"отделк|ремонт|подготов", text)))
            or (bool(card.infrastructure) and bool(re.search(r"инфраструктур|быт|магазин|парк|прогул", text)))
            or ((card.price is not None or card.price_min is not None) and bool(re.search(r"цен|бюджет", text)))
        ),
    }
    grounded = anchors.get(vp, True)
    return None if grounded else "scenario_fact_benefit_missing"


def compose_response_sync(
    brief: ResponseBrief,
    *,
    fallback_text: str,
    composer: Any,
    primary_model: str = "google/gemini-2.5-flash",
    provider_retry_model: str = PROVIDER_RETRY_MODEL,
) -> ComposerAttemptResult:
    """Выполнить одну модельную попытку в offline quality-сценарии."""
    provenance = build_prompt_provenance([_prompt_identity("response_composer", "prompts/v2_response_composer.txt", PROMPT_PATH)], coverage="complete")
    return replace(_run_compose_flow_sync(brief, fallback_text=fallback_text, composer=composer, primary_model=primary_model, provider_retry_model=provider_retry_model), prompt_provenance=provenance)


async def compose_response_async(
    brief: ResponseBrief,
    *,
    fallback_text: str,
    composer: Any,
    primary_model: str = "google/gemini-2.5-flash",
    provider_retry_model: str = PROVIDER_RETRY_MODEL,
) -> ComposerAttemptResult:
    """Асинхронный entrypoint только для offline quality-сценариев."""
    provenance = build_prompt_provenance([_prompt_identity("response_composer", "prompts/v2_response_composer.txt", PROMPT_PATH)], coverage="complete")
    return replace(await _run_compose_flow_async(brief, fallback_text=fallback_text, composer=composer, primary_model=primary_model, provider_retry_model=provider_retry_model), prompt_provenance=provenance)


async def compose_response_writer_formatter_async(
    brief: ResponseBrief,
    *,
    fallback_text: str,
    writer: Any,
    formatter: Any,
    writer_model: str = "google/gemini-2.5-flash",
    formatter_model: str | None = None,
    writer_prompt_identity: Mapping[str, Any] | None = None,
    validation_mode: str = "v2",
) -> ComposerAttemptResult:
    """Production conditional composer: Gemini simple JSON, Ling repair fallback.

    Exactly one Gemini call. Ling is called only when Gemini returned complete
    but mechanically invalid/unparseable content that can be normalized without
    rewriting. Transport/provider/empty Gemini failures go straight to the
    deterministic fallback. No provider retry.
    """

    stage_summaries: list[dict[str, Any]] = []
    provenance_items: list[dict[str, Any]] = [dict(writer_prompt_identity) if isinstance(writer_prompt_identity, Mapping) else _prompt_identity("response_writer", "prompts/v2_response_writer.txt", WRITER_PROMPT_PATH)]

    def with_provenance(result: ComposerAttemptResult) -> ComposerAttemptResult:
        return replace(result, prompt_provenance=build_prompt_provenance(provenance_items, coverage="complete"))
    started = time.monotonic()
    try:
        writer_result = _invoke_stage(writer, brief, writer_text=None, model=writer_model)
        if inspect.isawaitable(writer_result):
            writer_result = await writer_result
    except Exception:
        writer_result = "", {"ok": False, "error_code": "composer_exception", "_upstream_error": True}
    writer_raw, writer_meta = _split_attempt_result(writer_result)
    writer_text = str(writer_raw or "").strip() if isinstance(writer_raw, str) else ""
    writer_code = _stage_error_code(writer_raw, writer_meta, empty_code="writer_empty")
    stage_summaries.append(_stage_summary("writer", writer_model, started, writer_meta, writer_code))
    if writer_code or not writer_text:
        failed = ComposerAttemptResult(
            status="failed",
            text="",
            errors=(writer_code or "writer_empty",),
            error_category=_error_category(writer_code or "writer_empty"),
            error_code=writer_code or "writer_empty",
            attempt_summaries=tuple(stage_summaries),
        )
        return with_provenance(_fallback_result(fallback_text, failed))

    primary_formatted, primary_parse_errors = parse_formatter_json(writer_raw)
    if primary_formatted and not _missing_note_required(brief) and str(primary_formatted.get("missing_note") or "").strip():
        primary_formatted = dict(primary_formatted)
        primary_formatted["missing_note"] = ""
    primary_errors = _validate_writer_formatter_response(primary_formatted, brief, writer_text="", preserve_source=False, validation_mode=validation_mode) if primary_formatted else primary_parse_errors
    if primary_formatted and not primary_errors:
        return with_provenance(ComposerAttemptResult(
            status="primary",
            text=assemble_formatted_response(primary_formatted, brief),
            attempts=1,
            attempt_summaries=tuple(stage_summaries),
        ))

    if not _raw_has_recoverable_customer_content(writer_raw, brief):
        code = _safe_error_code((primary_errors or ["validation_failed"])[0].split(":", 1)[0])
        failed = ComposerAttemptResult(
            status="failed",
            text="",
            errors=tuple(primary_errors or (code,)),
            error_category=_error_category(code),
            error_code=code,
            attempt_summaries=tuple(stage_summaries),
            semantic_categories=_unknown_number_categories(assemble_formatted_response(primary_formatted, brief), brief) if primary_formatted and "unknown_number_or_sensitive_claim" in primary_errors else (),
        )
        return with_provenance(_fallback_result(fallback_text, failed))

    fmt_model = formatter_model or os.getenv("NMBOT_RESPONSE_FORMATTER_MODEL") or "inclusionai/ling-2.6-flash"
    provenance_items.append(_prompt_identity("response_formatter", "prompts/v2_response_formatter.txt", FORMATTER_PROMPT_PATH))
    fmt_started = time.monotonic()
    try:
        fmt_result = _invoke_stage(formatter, brief, writer_text=writer_text, model=fmt_model)
        if inspect.isawaitable(fmt_result):
            fmt_result = await fmt_result
    except Exception:
        fmt_result = "", {"ok": False, "error_code": "composer_exception", "_upstream_error": True}
    fmt_raw, fmt_meta = _split_attempt_result(fmt_result)
    fmt_code = _stage_error_code(fmt_raw, fmt_meta, empty_code="empty_response")
    formatted, parse_errors = (None, [fmt_code]) if fmt_code else parse_formatter_json(fmt_raw)
    if formatted and not _missing_note_required(brief) and str(formatted.get("missing_note") or "").strip():
        formatted = dict(formatted)
        formatted["missing_note"] = ""
    mechanical_errors = _validate_writer_formatter_response(formatted, brief, writer_text=writer_text, preserve_source=True, validation_mode=validation_mode) if formatted else parse_errors
    stage_summaries.append(_stage_summary("formatter", fmt_model, fmt_started, fmt_meta, (mechanical_errors or [None])[0]))
    if not formatted or mechanical_errors:
        code = _safe_error_code((mechanical_errors or ["validation_failed"])[0].split(":", 1)[0])
        failed = ComposerAttemptResult(
            status="failed",
            text="",
            errors=tuple(mechanical_errors or (code,)),
            error_category=_error_category(code),
            error_code=code,
            attempts=2,
            attempt_summaries=tuple(stage_summaries),
            semantic_categories=_unknown_number_categories(assemble_formatted_response(formatted, brief), brief, writer_text=writer_text) if formatted and "unknown_number_or_sensitive_claim" in mechanical_errors else (),
        )
        return with_provenance(replace(_fallback_result(fallback_text, failed), attempts=2))
    return with_provenance(ComposerAttemptResult(
        status="repaired",
        text=assemble_formatted_response(formatted, brief),
        attempts=2,
        attempt_summaries=tuple(stage_summaries),
    ))


async def compose_response_one_shot_async(
    brief: ResponseBrief,
    *,
    fallback_text: str,
    composer: Any,
    primary_model: str = "google/gemini-2.5-flash",
) -> ComposerAttemptResult:
    """Compose with one same-prompt retry before deterministic fallback.

    Semantic validator output is retained as warnings only. Publishability is
    decided by strict parse/schema plus small mechanical response-contract checks.
    """
    provenance = build_prompt_provenance([_prompt_identity("response_composer", "prompts/v2_response_composer.txt", PROMPT_PATH)], coverage="complete")
    raw_result = await _call_composer_async(composer, brief, repair_errors=(), model=primary_model)
    first = _evaluate_one_shot_raw_attempt(raw_result, brief, attempt_kind="primary", model=primary_model)
    if first.status == "primary":
        return replace(first, prompt_provenance=provenance)

    # Retry the canonical response prompt unchanged. This is deliberately not
    # a formatter/repair pass: the model receives the same brief and the same
    # prompt contract, while the deterministic renderer remains the final
    # safety net if the second attempt also fails.
    retry_raw = await _call_composer_async(composer, brief, repair_errors=(), model=primary_model)
    retry = _evaluate_one_shot_raw_attempt(retry_raw, brief, attempt_kind="same_prompt_retry", model=primary_model)
    combined_summaries = (*first.attempt_summaries, *retry.attempt_summaries)
    if retry.status == "primary":
        return replace(retry, status="provider_retry", attempts=2, attempt_summaries=combined_summaries, prompt_provenance=provenance)
    return replace(_fallback_result(
        fallback_text,
        replace(first, attempts=2, attempt_summaries=combined_summaries),
        attempts=2,
    ), prompt_provenance=provenance)


def _evaluate_one_shot_raw_attempt(result: Any, brief: ResponseBrief, *, attempt_kind: str, model: str) -> ComposerAttemptResult:
    raw, meta = _split_attempt_result(result)
    if isinstance(meta, Mapping):
        if meta.get("_safe_fallback") or meta.get("_upstream_error") or meta.get("ok") is False:
            raw_code = meta.get("_provider_error_code") or meta.get("error_code")
            code = _safe_error_code(raw_code) if raw_code else ("gateway_not_ok" if meta.get("ok") is False else "upstream_error")
            category = "provider" if code in _PROVIDER_CODES else "transport"
            summary = _attempt_summary(attempt_kind, model, raw, meta, category, code)
            return ComposerAttemptResult(status="failed", text="", errors=(code,), error_category=category, error_code=code, attempt_summaries=(summary,))
    composed, errors = parse_composer_json(raw)
    if not composed or errors:
        code = _safe_error_code((errors or ["validation_failed"])[0].split(":", 1)[0])
        category = _error_category(code)
        summary = _attempt_summary(attempt_kind, model, raw, meta, category, code)
        return ComposerAttemptResult(status="failed", text="", errors=tuple(errors or (code,)), error_category=category, error_code=code, attempt_summaries=(summary,))
    if composed.missing_note.strip() and not _missing_note_required(brief):
        composed = replace(composed, missing_note="")
    mechanical_errors = validate_mechanical_composed_response(composed, brief)
    warnings = tuple(validate_composed_response(composed, brief))
    summary = _attempt_summary(attempt_kind, model, raw, meta, None if not mechanical_errors else "schema", None if not mechanical_errors else mechanical_errors[0])
    if warnings:
        summary["validation_warnings"] = list(warnings[:6])
    if mechanical_errors:
        return ComposerAttemptResult(status="failed", text="", errors=tuple(mechanical_errors), warnings=warnings, error_category="schema", error_code=mechanical_errors[0], attempt_summaries=(summary,))
    return ComposerAttemptResult(status="primary", text=assemble_composed_response(composed, brief), warnings=warnings, attempt_summaries=(summary,))


def validate_mechanical_composed_response(composed: ComposedResponse, brief: ResponseBrief) -> list[str]:
    errors: list[str] = []
    text = assemble_composed_response(composed, brief)
    allowed_names = [card.name for card in brief.canonical_cards[:3]]
    directive_names = [str(item.get("card_name") or "") for item in brief.recipe_cards]
    shown_names = [option.name for option in composed.options]
    if len(shown_names) > 3:
        errors.append("too_many_cards")
    if any(name not in allowed_names for name in shown_names):
        errors.append("option_name_not_allowed")
    if shown_names != allowed_names[: len(shown_names)]:
        errors.append("option_order_mismatch")
    if directive_names and directive_names[: len(shown_names)] != shown_names:
        errors.append("recipe_card_directive_mismatch")
    for option in composed.options:
        if not option.name.strip() or not option.facts.strip() or not option.description.strip():
            errors.append("empty_option_section")
    if not composed.intro.strip():
        errors.append("intro_empty")
    if brief.answer_goal == "recommend_current" and not composed.recommendation.strip():
        errors.append("recommendation_required")
    if _missing_note_required(brief) and not composed.missing_note.strip():
        errors.append("missing_note_required")
    if brief.operator_handoff_template and composed.missing_note.strip() != brief.operator_handoff_template.strip():
        errors.append("operator_handoff_template_mismatch")
    if not composed.final_question.strip():
        errors.append("final_question_empty")
    if brief.cta_template and composed.final_question.rstrip("?.! ") + "?" != brief.cta_template:
        errors.append("recipe_cta_mismatch")
    if any("?" in part for part in _non_question_sections(composed)):
        errors.append("section_question_mark")
    if composed.final_question.count("?") != 1 or not composed.final_question.rstrip().endswith("?"):
        errors.append("question_count_not_one")
    if text.count("?") != 1:
        errors.append("question_count_not_one")
    if "?" in text and not text.rstrip().endswith("?"):
        errors.append("final_question_not_at_end")
    if composed.final_question and not text.rstrip().endswith(composed.final_question.rstrip("?.! ") + "?"):
        errors.append("final_question_contract_mismatch")
    return list(dict.fromkeys(errors))


def parse_formatter_json(text: str | Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    if isinstance(text, Mapping):
        data = dict(text)
    else:
        raw = str(text or "").strip()
        if not raw or raw.casefold() in {"none", "null"}:
            return None, ["empty_response"]
        fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.I | re.S)
        if fenced:
            raw = fenced.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, [f"invalid_json:{exc.msg}"]
    errors = _formatter_schema_errors(data)
    return (None, errors) if errors else (data, [])


def _formatter_schema_errors(data: Mapping[str, Any]) -> list[str]:
    if not isinstance(data, Mapping):
        return ["json_root_must_be_object"]
    required = {"intro", "cards", "recommendation", "missing_note", "final_question"}
    if set(data.keys()) != required:
        return ["formatter_schema_invalid"]
    if not all(isinstance(data.get(key), str) for key in ("intro", "recommendation", "missing_note", "final_question")):
        return ["formatter_schema_invalid"]
    cards = data.get("cards")
    if not isinstance(cards, list) or len(cards) > 3:
        return ["formatter_schema_invalid"]
    for card in cards:
        if not isinstance(card, Mapping) or set(card.keys()) != {"name", "text"}:
            return ["formatter_schema_invalid"]
        if not isinstance(card.get("name"), str) or not isinstance(card.get("text"), str):
            return ["formatter_schema_invalid"]
    return []


def validate_formatter_response(formatted: Mapping[str, Any] | None, brief: ResponseBrief, *, writer_text: str, preserve_source: bool = True) -> list[str]:
    if not isinstance(formatted, Mapping):
        return ["formatter_schema_invalid"]
    errors: list[str] = []
    expected_names = [card.name for card in brief.canonical_cards[:3]]
    cards = formatted.get("cards") if isinstance(formatted.get("cards"), list) else []
    shown_names = [str(card.get("name") or "") for card in cards if isinstance(card, Mapping)]
    if len(cards) != len(expected_names):
        errors.append("formatter_card_count_mismatch")
    if shown_names != expected_names:
        errors.append("formatter_card_order_mismatch")
    for card in cards:
        if not isinstance(card, Mapping) or not str(card.get("text") or "").strip():
            errors.append("formatter_card_text_empty")
    if not str(formatted.get("intro") or "").strip():
        errors.append("intro_empty")
    if _missing_note_required(brief) and not str(formatted.get("missing_note") or "").strip():
        errors.append("missing_note_required")
    if brief.operator_handoff_template and str(formatted.get("missing_note") or "").strip() != brief.operator_handoff_template.strip():
        errors.append("operator_handoff_template_mismatch")
    final_question = str(formatted.get("final_question") or "").strip()
    if not final_question:
        errors.append("final_question_empty")
    if brief.cta_template and final_question.rstrip("?.! ") + "?" != brief.cta_template:
        errors.append("recipe_cta_mismatch")
    if final_question.count("?") != 1 or not final_question.rstrip().endswith("?"):
        errors.append("question_count_not_one")
    non_question = [str(formatted.get(key) or "") for key in ("intro", "recommendation", "missing_note")]
    non_question.extend(str(card.get("text") or "") for card in cards if isinstance(card, Mapping))
    if any("?" in part for part in non_question):
        errors.append("section_question_mark")
    assembled = assemble_formatted_response(formatted, brief)
    if assembled.count("?") != 1:
        errors.append("question_count_not_one")
    if _unknown_numbers_against_sources(assembled, writer_text, brief):
        errors.append("unknown_number_or_sensitive_claim")
    if _formatter_introduced_project_names(assembled, writer_text, expected_names):
        errors.append("formatter_project_name_introduced")
    if preserve_source and _formatter_content_mismatch(writer_text, assembled):
        errors.append("formatter_content_mismatch")
    return list(dict.fromkeys(errors))


def _validate_writer_formatter_response(formatted: Mapping[str, Any] | None, brief: ResponseBrief, *, writer_text: str, preserve_source: bool, validation_mode: str) -> list[str]:
    if str(validation_mode or "v2").strip().lower() == "v3":
        return validate_v3_formatter_response(formatted, brief, writer_text=writer_text, preserve_source=preserve_source)
    return validate_formatter_response(formatted, brief, writer_text=writer_text, preserve_source=preserve_source)


def validate_v3_formatter_response(formatted: Mapping[str, Any] | None, brief: ResponseBrief, *, writer_text: str, preserve_source: bool = True) -> list[str]:
    """V3 publish gate: hard safety without V2 scenario/recipe presentation mandates."""

    if not isinstance(formatted, Mapping):
        return ["formatter_schema_invalid"]
    errors: list[str] = []
    expected_names = [card.name for card in brief.canonical_cards[:3]]
    cards = formatted.get("cards") if isinstance(formatted.get("cards"), list) else []
    shown_names = [str(card.get("name") or "") for card in cards if isinstance(card, Mapping)]
    if len(cards) != len(expected_names):
        errors.append("formatter_card_count_mismatch")
    if shown_names != expected_names:
        errors.append("formatter_card_order_mismatch")
    for idx, card_item in enumerate(cards):
        if not isinstance(card_item, Mapping) or not str(card_item.get("text") or "").strip():
            errors.append("formatter_card_text_empty")
            continue
        text = str(card_item.get("text") or "")
        if idx < len(brief.canonical_cards):
            source_card = brief.canonical_cards[idx]
            if source_card.location and not _text_contains_fact(text, source_card.location):
                errors.append("required_location_missing")
            if (source_card.price or source_card.price_min is not None) and not re.search(r"(?:цен|млн|руб|₽)", text.casefold().replace("ё", "е")):
                errors.append("required_price_missing")
    if not str(formatted.get("intro") or "").strip():
        errors.append("intro_empty")
    missing_note = str(formatted.get("missing_note") or "").strip()
    if _missing_note_required(brief) and not missing_note:
        errors.append("missing_note_required")
    if brief.operator_handoff_template and missing_note != brief.operator_handoff_template.strip():
        errors.append("operator_handoff_template_mismatch")
    if brief.response_viewpoint == "financing" and any(str(item).strip().lower() == "mortgage_terms" for item in brief.missing_facts):
        if not re.search(r"финанс|ипот|оплат|ставк|услов", missing_note, re.I):
            errors.append("financing_missing_note_required")
    final_question = str(formatted.get("final_question") or "").strip()
    if not final_question:
        errors.append("final_question_empty")
    if _v3_exact_cta_required(brief) and brief.cta_template and final_question.rstrip("?.! ") + "?" != brief.cta_template:
        errors.append("recipe_cta_mismatch")
    if brief.recipe_id in {"selected_financing", "current_options_financing"}:
        if re.search(r"как к вам обращаться|номер(?:\s+телефон)?|оставьте\s+(?:номер|телефон)", final_question, re.I):
            errors.append("contact_before_financing_consent")
        if brief.recipe_id == "selected_financing" and len(brief.canonical_cards) != 1:
            errors.append("selected_financing_card_scope_invalid")
    if final_question.count("?") != 1 or not final_question.rstrip().endswith("?"):
        errors.append("question_count_not_one")
    non_question = [str(formatted.get(key) or "") for key in ("intro", "recommendation", "missing_note")]
    non_question.extend(str(card.get("text") or "") for card in cards if isinstance(card, Mapping))
    if any("?" in part for part in non_question):
        errors.append("section_question_mark")
    assembled = assemble_formatted_response(formatted, brief)
    if assembled.count("?") != 1:
        errors.append("question_count_not_one")
    if "?" in assembled and not assembled.rstrip().endswith(final_question.rstrip("?.! ") + "?"):
        errors.append("final_question_contract_mismatch")
    unknown_names = [name for name in re.findall(r"ЖК\s+[«\"]([^»\"\n]+)[»\"]", assembled) if not _matches_allowed_name(name, expected_names)]
    if unknown_names:
        errors.append("unknown_option_name")
    if _unknown_numbers_against_sources(assembled, writer_text, brief):
        errors.append("unknown_number_or_sensitive_claim")
    if _formatter_introduced_project_names(assembled, writer_text, expected_names):
        errors.append("formatter_project_name_introduced")
    if INTERNAL_RE.search(assembled):
        errors.append("internal_or_raw_wire_leak")
    if SENSITIVE_CLAIMS_RE.search(assembled) and not _claim_allowed_by_brief(assembled, brief):
        errors.append("unsupported_sensitive_claim")
    marketing_code = _unsupported_marketing_code(assembled)
    if marketing_code or UNSUPPORTED_MARKETING_RE.search(assembled):
        errors.append("unsupported_marketing_claim:" + (marketing_code or "other"))
    if preserve_source and _formatter_content_mismatch(writer_text, assembled):
        errors.append("formatter_content_mismatch")
    return list(dict.fromkeys(errors))


def _v3_exact_cta_required(brief: ResponseBrief) -> bool:
    return bool(
        brief.operator_handoff_template
        or brief.response_policy == "operator_consent_offer"
        or brief.recipe_id in {"selected_financing", "current_options_financing"}
    )


def assemble_formatted_response(formatted: Mapping[str, Any], brief: ResponseBrief | None = None) -> str:
    parts: list[str] = []
    intro = _normalize_text(str(formatted.get("intro") or ""))
    if intro:
        parts.append(intro)
    cards = formatted.get("cards") if isinstance(formatted.get("cards"), list) else []
    for idx, card in enumerate(cards[:3], start=1):
        if not isinstance(card, Mapping):
            continue
        name = str(card.get("name") or "").strip()
        body = _strip_duplicate_heading(str(card.get("text") or ""), name)
        if name:
            parts.append(f"{idx}. {name}\n{body}".strip())
    for key in ("recommendation", "missing_note"):
        value = _normalize_text(str(formatted.get(key) or ""))
        if value:
            parts.append(value)
    question = str(formatted.get("final_question") or "").strip().rstrip("?.! ")
    if question:
        parts.append(question + "?")
    return "\n\n".join(parts).strip()


def _strip_duplicate_heading(text: str, name: str) -> str:
    body = str(text or "").strip()
    heading = str(name or "").strip()
    if not body or not heading:
        return body
    patterns = (heading, f"{heading}:", f"{heading} —", f"{heading} -")
    for prefix in patterns:
        if body.startswith(prefix):
            return body[len(prefix) :].lstrip(" \n:-—").strip()
    return body


def _unknown_numbers_against_sources(text: str, writer_text: str, brief: ResponseBrief) -> bool:
    return bool(_unknown_number_matches(text, _allowed_number_tokens(brief, writer_text=writer_text)))


def _semantic_validation_diagnostics(
    *,
    status: str,
    error_category: str | None,
    errors: tuple[str, ...],
    attempt_summaries: tuple[dict[str, Any], ...],
    semantic_categories: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Return an intentionally content-free classification for failed V3 stages."""

    if status != "fallback" or error_category != "semantic":
        return []
    stage = next(
        (
            str(item.get("stage") or "")
            for item in reversed(attempt_summaries)
            if isinstance(item, Mapping) and str(item.get("stage") or "") in {"writer", "formatter"}
        ),
        "",
    )
    if not stage:
        return []
    categories: list[str] = []
    for error in errors:
        code = str(error or "").split(":", 1)[0]
        category = {
            "unsupported_sensitive_claim": "sensitive_claim",
        }.get(code)
        if category and category not in categories:
            categories.append(category)
        if code == "unknown_number_or_sensitive_claim":
            for numeric_category in semantic_categories:
                if numeric_category in _SAFE_NUMERIC_DIAGNOSTIC_CATEGORIES and numeric_category not in categories:
                    categories.append(numeric_category)
    return [{"stage": stage, "categories": categories[:3]}] if categories else []


def _formatter_introduced_project_names(text: str, writer_text: str, expected_names: list[str]) -> bool:
    allowed = list(expected_names)
    writer_token = _name_token(writer_text)
    for raw in re.findall(r"ЖК\s+[«\"]([^»\"\n]+)[»\"]", text):
        if _matches_allowed_name(raw, allowed):
            continue
        if _name_token(raw) and _name_token(raw) in writer_token:
            continue
        return True
    return False


def _formatter_content_mismatch(writer_text: str, assembled_text: str) -> bool:
    """Reject formatter rewrites while allowing only assembler formatting.

    The formatter may only move prose into fixed fields.  The final assembled
    response is allowed to differ from writer text by whitespace, punctuation
    around card headings, and line-start card numbering produced by the local
    assembler.  Digits are otherwise preserved as ordinary factual tokens.
    """

    return _source_content_tokens(writer_text) != _content_tokens(assembled_text)


def _source_content_tokens(text: str) -> tuple[str, ...]:
    formatted, _errors = parse_formatter_json(text)
    if formatted:
        return _content_tokens(assemble_formatted_response(formatted))
    normalized = str(text or "")
    fenced = re.fullmatch(r"```(?:json)?\s*(.*)\s*```", normalized.strip(), flags=re.I | re.S)
    if fenced:
        normalized = fenced.group(1)
    normalized = re.sub(r'"(?:intro|cards|name|text|recommendation|missing_note|final_question)"\s*:', " ", normalized, flags=re.I)
    normalized = re.sub(r"[{}\[\],:]", " ", normalized)
    return _content_tokens(normalized)


def _raw_has_recoverable_customer_content(raw: Any, brief: ResponseBrief) -> bool:
    if not isinstance(raw, str) or not raw.strip():
        return False
    tokens = _source_content_tokens(raw)
    if len(tokens) < 8:
        return False
    raw_name_token = _name_token(raw)
    expected = [card.name for card in brief.canonical_cards[:3]]
    if expected and not all(_name_token(name) in raw_name_token for name in expected):
        return False
    expected_question = (brief.cta_template or brief.fallback_question or "").rstrip("?.! ")
    if expected_question and _name_token(expected_question) not in raw_name_token:
        return False
    return True


def _content_tokens(text: str) -> tuple[str, ...]:
    normalized = str(text or "").casefold().replace("ё", "е")
    normalized = re.sub(r"(?m)^\s*[1-3]\s*[.)]\s+", " ", normalized)
    return tuple(re.findall(r"[0-9a-zа-я]+", normalized, flags=re.I))


def _missing_note_required(brief: ResponseBrief) -> bool:
    """Require customer caveats only for explicit current/open-question gaps."""

    return bool(brief.missing_facts) and bool(brief.operator_handoff_template or brief.response_policy == "operator_consent_offer")


def _run_compose_flow_sync(brief: ResponseBrief, *, fallback_text: str, composer: Any, primary_model: str, provider_retry_model: str) -> ComposerAttemptResult:
    first = _evaluate_raw_attempt(_call_composer_sync(composer, brief, repair_errors=(), model=primary_model), brief, attempt_kind="primary", model=primary_model)
    if first.status == "primary":
        return first
    if first.error_category in {"semantic", "schema"}:
        repaired = _evaluate_raw_attempt(
            _call_composer_sync(composer, brief, repair_errors=first.errors[:6], model=primary_model),
            brief,
            attempt_kind="repair",
            model=primary_model,
        )
        if repaired.status == "primary":
            return replace(repaired, status="repaired", attempts=2, attempt_summaries=first.attempt_summaries + repaired.attempt_summaries)
        return replace(_fallback_result(fallback_text, first), attempts=2, attempt_summaries=first.attempt_summaries + repaired.attempt_summaries)
    return _fallback_result(fallback_text, first)


async def _run_compose_flow_async(brief: ResponseBrief, *, fallback_text: str, composer: Any, primary_model: str, provider_retry_model: str) -> ComposerAttemptResult:
    first = _evaluate_raw_attempt(await _call_composer_async(composer, brief, repair_errors=(), model=primary_model), brief, attempt_kind="primary", model=primary_model)
    if first.status == "primary":
        return first
    if first.error_category in {"semantic", "schema"}:
        repaired = _evaluate_raw_attempt(
            await _call_composer_async(composer, brief, repair_errors=first.errors[:6], model=primary_model),
            brief,
            attempt_kind="repair",
            model=primary_model,
        )
        if repaired.status == "primary":
            return replace(repaired, status="repaired", attempts=2, attempt_summaries=first.attempt_summaries + repaired.attempt_summaries)
        return replace(_fallback_result(fallback_text, first), attempts=2, attempt_summaries=first.attempt_summaries + repaired.attempt_summaries)
    return _fallback_result(fallback_text, first)


def _evaluate_raw_attempt(result: Any, brief: ResponseBrief, *, attempt_kind: str, model: str) -> ComposerAttemptResult:
    raw, meta = _split_attempt_result(result)
    if isinstance(meta, Mapping):
        if meta.get("_safe_fallback") or meta.get("_upstream_error") or meta.get("ok") is False:
            raw_code = meta.get("_provider_error_code") or meta.get("error_code")
            code = _safe_error_code(raw_code) if raw_code else ("gateway_not_ok" if meta.get("ok") is False else "upstream_error")
            category = "provider" if code in _PROVIDER_CODES else "transport"
            summary = _attempt_summary(attempt_kind, model, raw, meta, category, code)
            return ComposerAttemptResult(status="failed", text="", errors=(code,), error_category=category, error_code=code, attempt_summaries=(summary,))
    composed, errors = parse_composer_json(raw)
    if composed and not errors:
        if composed.missing_note.strip() and not _missing_note_required(brief):
            composed = replace(composed, missing_note="")
        warnings = tuple(validate_composed_response(composed, brief))
        summary = _attempt_summary(attempt_kind, model, raw, meta, None, None)
        if warnings:
            summary["validation_warnings"] = list(warnings)
        return ComposerAttemptResult(status="primary", text=assemble_composed_response(composed, brief), warnings=warnings, attempt_summaries=(summary,))
    code = _safe_error_code((errors or ["validation_failed"])[0].split(":", 1)[0])
    category = _error_category(code)
    summary = _attempt_summary(attempt_kind, model, raw, meta, category, code)
    return ComposerAttemptResult(status="failed", text="", errors=tuple(errors or (code,)), error_category=category, error_code=code, attempt_summaries=(summary,))


def _split_attempt_result(result: Any) -> tuple[Any, Mapping[str, Any]]:
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], Mapping):
        return result[0], result[1]
    if isinstance(result, Mapping) and "intro" in result and "options" in result and "final_question" in result:
        return result, {}
    if isinstance(result, Mapping) and ("response" in result or "error" in result):
        if result.get("error"):
            return "", {"ok": False, "error_code": _provider_like_code(result.get("error")) or "upstream_error"}
        return result.get("response", result), result.get("metadata", {}) if isinstance(result.get("metadata"), Mapping) else {}
    return result, {}


def _safe_error_code(code: Any) -> str:
    raw = str(code or "").strip().lower().replace(" ", "_")
    raw = re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")
    if raw.startswith("invalid_json"):
        raw = "invalid_json"
    return raw if raw in _ALLOWLISTED_ERROR_CODES else "validation_failed"


def _schema_errors(data: Mapping[str, Any]) -> list[str]:
    # Keep parsing old offline fixtures/provider responses tolerant. The strict
    # provider schema requests the field; semantic validation makes it
    # mandatory specifically for recommend_current.
    missing = [key for key in ("intro", "options", "missing_note", "final_question") if key not in data]
    if missing:
        return ["schema_required_field_missing"]
    if not all(isinstance(data.get(key), str) for key in ("intro", "missing_note", "final_question")):
        return ["schema_required_field_missing"]
    if "recommendation" in data and not isinstance(data.get("recommendation"), str):
        return ["schema_required_field_missing"]
    options = data.get("options")
    if not isinstance(options, list):
        return ["schema_invalid_options"]
    for option in options:
        if not isinstance(option, Mapping):
            return ["schema_invalid_options"]
        if any(key in option and not isinstance(option.get(key), str) for key in ("name", "facts", "description")):
            return ["schema_invalid_options"]
    return []


def _raw_type(raw: Any) -> str:
    if raw is None or raw == "":
        return "empty"
    if isinstance(raw, str):
        return "string"
    if isinstance(raw, Mapping):
        return "mapping"
    return "other"


def _raw_length(raw: Any) -> int:
    if raw is None:
        return 0
    if isinstance(raw, str):
        return len(raw)
    if isinstance(raw, Mapping):
        try:
            return len(json.dumps(raw, ensure_ascii=False, sort_keys=True))
        except Exception:
            return 0
    return 0


def _safe_gateway_task_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", text)[:80].strip("_") or None


def _attempt_summary(attempt_kind: str, model: str, raw: Any, meta: Mapping[str, Any], error_category: str | None, error_code: str | None) -> dict[str, Any]:
    summary = {
        "attempt_kind": attempt_kind,
        "model": model,
        "gateway_ok": bool(meta.get("ok", True)) if isinstance(meta, Mapping) else True,
        "raw_type": _raw_type(raw),
        "raw_length": _raw_length(raw),
        "error_category": error_category,
        "error_code": _safe_error_code(error_code) if error_code else None,
    }
    if isinstance(raw, str):
        left = raw.lstrip()
        right = raw.rstrip()
        summary.update(
            {
                "starts_object": left.startswith("{"),
                "starts_fence": left.startswith("```"),
                "ends_object": right.endswith("}"),
            }
        )
    if isinstance(meta, Mapping):
        task_id = _safe_gateway_task_id(meta.get("_gateway_task_id"))
        if task_id:
            summary["gateway_task_id"] = task_id
    return summary


def _non_question_sections(composed: ComposedResponse) -> tuple[str, ...]:
    parts = [composed.intro, composed.recommendation, composed.missing_note]
    for option in composed.options:
        parts.extend([option.facts, option.description])
    return tuple(parts)


def _provider_like_code(error: Any) -> str | None:
    text = str(error or "").lower()
    if "invalid_argument" in text and "provider" in text:
        return "provider_invalid_argument"
    if "corrupted thought signature" in text:
        return "corrupted_thought_signature"
    if "choices" in text:
        return "choices_response_parse"
    if "schema" in text or "response_format" in text:
        return "schema_unsupported"
    return None


def _error_category(code: str) -> str:
    if code in _PROVIDER_CODES:
        return "provider"
    if code in {"empty_response", "composer_exception", "gateway_not_ok", "upstream_error", "adapter_invalid_output", "adapter_exception"}:
        return "transport"
    if code == "schema_unsupported" or code == "json_root_must_be_object" or code.startswith("formatter_"):
        return "schema"
    return "semantic"


def _fallback_result(fallback_text: str, first: ComposerAttemptResult, *, attempts: int | None = None) -> ComposerAttemptResult:
    return ComposerAttemptResult(
        status="fallback",
        text=fallback_text,
        errors=first.errors,
        warnings=first.warnings,
        error_category=first.error_category,
        error_code=first.error_code,
        attempts=attempts or first.attempts,
        attempt_summaries=first.attempt_summaries,
        semantic_categories=first.semantic_categories,
    )


def _stage_error_code(raw: Any, meta: Mapping[str, Any], *, empty_code: str) -> str | None:
    if isinstance(meta, Mapping) and (meta.get("_safe_fallback") or meta.get("_upstream_error") or meta.get("ok") is False):
        raw_code = meta.get("_provider_error_code") or meta.get("error_code")
        return _safe_error_code(raw_code) if raw_code else ("gateway_not_ok" if meta.get("ok") is False else "upstream_error")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return empty_code
    return None


def _stage_summary(stage: str, model: str, started: float, meta: Mapping[str, Any], error_code: str | None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "stage": stage,
        "status": "failed" if error_code else "ok",
        "model": model,
        "error_code": _safe_error_code(error_code) if error_code else None,
        "elapsed_ms": _bounded_int(round((time.monotonic() - started) * 1000), 0, 10 * 60 * 1000),
    }
    if isinstance(meta, Mapping):
        task_id = _safe_gateway_task_id(meta.get("_gateway_task_id"))
        if task_id:
            summary["gateway_task_id"] = task_id
    return summary


def _bounded_int(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = low
    return max(low, min(high, number))


def _invoke_stage(stage: Any, brief: ResponseBrief, *, writer_text: str | None, model: str) -> Any:
    if callable(stage):
        fn = stage
    elif writer_text is None and hasattr(stage, "write"):
        fn = stage.write
    elif writer_text is not None and hasattr(stage, "format"):
        fn = stage.format
    elif hasattr(stage, "compose"):
        fn = stage.compose
    else:
        raise TypeError("composer_stage_not_callable")
    kwargs: dict[str, Any] = {"model": model}
    if writer_text is not None:
        kwargs["writer_text"] = writer_text
    try:
        params = inspect.signature(fn).parameters
        kwargs = {key: value for key, value in kwargs.items() if key in params}
    except Exception:
        pass
    return fn(brief, **kwargs)


def _call_composer_sync(composer: Any, brief: ResponseBrief, *, repair_errors: tuple[str, ...], model: str) -> Any:
    try:
        return _invoke_composer(composer, brief, repair_errors=repair_errors, model=model)
    except Exception:
        return "", {"ok": False, "error_code": "composer_exception"}


async def _call_composer_async(composer: Any, brief: ResponseBrief, *, repair_errors: tuple[str, ...], model: str) -> Any:
    try:
        value = _invoke_composer(composer, brief, repair_errors=repair_errors, model=model)
        if inspect.isawaitable(value):
            return await value
        return value
    except Exception:
        return "", {"ok": False, "error_code": "composer_exception"}


def _invoke_composer(composer: Any, brief: ResponseBrief, *, repair_errors: tuple[str, ...], model: str) -> Any:
    fn = composer if callable(composer) and not hasattr(composer, "compose") else composer.compose
    kwargs: dict[str, Any] = {"repair_errors": repair_errors}
    try:
        params = inspect.signature(fn).parameters
        if "model" in params:
            kwargs["model"] = model
    except Exception:
        pass
    return fn(brief, **kwargs)


def _answer_goal(stage: Stage, plan: TurnPlan, execution: ExecutionResult, cards: tuple[OptionCard, ...]) -> str:
    if not execution.ok:
        return "safe_error_without_state_change"
    if stage == Stage.SELECTED_OBJECT:
        return "answer_selected_option_from_confirmed_card"
    if stage in {Stage.FIRST_LIST, Stage.REFINEMENT}:
        return "recommend_current" if cards else "ask_one_clarifying_or_relaxation_question"
    if stage == Stage.CURRENT_OPTIONS:
        if _is_current_fact_answer(plan):
            return "answer_open_question"
        if isinstance(plan, ExecutableTurn):
            return plan.goal.value
        return "answer_about_current_options"
    if stage == Stage.FINANCING_CLARIFICATION:
        return "answer_financing_without_inventing_terms"
    if stage == Stage.OFF_TOPIC:
        return "answer_off_topic_without_search"
    return plan.goal.value if isinstance(plan, ExecutableTurn) else (plan.operation or "answer_user")


def _is_current_fact_answer(plan: TurnPlan) -> bool:
    return (
        isinstance(plan, ExecutableTurn)
        and plan.goal in {IntentGoal.ANSWER_CURRENT, IntentGoal.ANSWER_OPEN_QUESTION}
        and bool(plan.requested_facts or plan.facts_needed)
    ) or (isinstance(plan, SemanticPlan) and plan.operation == "answer_open_question")


def _allowed_fact_fields(cards: tuple[OptionCard, ...]) -> tuple[str, ...]:
    fields: list[str] = []
    for card in cards:
        for key, value in to_jsonable(card).items():
            if key not in {"is_near"} and value not in (None, "", [], {}):
                fields.append(key)
    return tuple(dict.fromkeys(fields))


def _allowed_claims(cards: tuple[OptionCard, ...], viewpoint: str) -> tuple[str, ...]:
    claims = ["Можно объяснять только подтверждённый факт и практическую пользу этого факта."]
    if any(card.sales_count is not None for card in cards):
        claims.append("sales_count можно называть продажами/сделками по ЕГРН.")
    if any(card.ads_count is not None for card in cards):
        claims.append("ads_count можно называть только текущим числом объявлений на витрине; нельзя выводить из него широкий выбор, доступность нужной квартиры, спрос или продажи.")
    claims.append("Нельзя обещать идеальное жильё, счастливую жизнь, наслаждение комфортом или другие эмоциональные результаты, которых нет в фактах.")
    if viewpoint == "financing" and any(card.discount for card in cards):
        claims.append("Можно упоминать только те условия оплаты, которые есть в discount.")
    return tuple(claims)


def _normalize_text(text: str) -> str:
    return str(text or "").strip()


def _names_in_text(text: str, names: list[str]) -> list[str]:
    return [name for name in names if name and _name_token(name) in _name_token(text)]


def _name_token(value: str) -> str:
    value = str(value or "").casefold().replace("ё", "е")
    value = re.sub(r"[«»\"'.,!?():;]+", " ", value)
    value = re.sub(r"\bжк\b", " ", value)
    return " ".join(value.split())


def _text_contains_fact(text: str, fact: str) -> bool:
    """Match factual labels through Russian inflection, not object fuzzy routing."""
    haystack = _name_token(text).split()
    needles = [token for token in _name_token(fact).split() if len(token) >= 4]
    if not needles:
        return _name_token(fact) in _name_token(text)
    matched = 0
    for needle in needles:
        stem = needle[: max(4, len(needle) - 2)]
        if any(token.startswith(stem) for token in haystack):
            matched += 1
    return matched >= max(1, len(needles) - 1)


def _matches_allowed_name(value: str, allowed: list[str]) -> bool:
    token = _name_token(value)
    return any(token and (token == _name_token(name) or token in _name_token(name)) for name in allowed)


_NUMBER_TOKEN_RE = re.compile(r"\d+(?:[\s.,–-]*\d+)*")
_SAFE_NUMERIC_DIAGNOSTIC_CATEGORIES = {
    "numeric_price_not_in_canonical",
    "numeric_transit_not_in_canonical",
    "numeric_area_not_in_canonical",
    "numeric_other_not_in_canonical",
}


def _unknown_numbers(text: str, brief: ResponseBrief) -> bool:
    return bool(_unknown_number_matches(text, _allowed_number_tokens(brief)))


def _unknown_number_categories(text: str, brief: ResponseBrief, *, writer_text: str = "") -> tuple[str, ...]:
    """Classify rejected numeric tokens locally without retaining token data."""

    categories: list[str] = []
    for match in _unknown_number_matches(text, _allowed_number_tokens(brief, writer_text=writer_text)):
        category = _numeric_diagnostic_category(match, text)
        if category not in categories:
            categories.append(category)
    return tuple(categories[:3])


def _unknown_number_matches(text: str, allowed: set[str]) -> tuple[re.Match[str], ...]:
    """Use the validator's canonical digit normalization for every caller."""

    unknown: list[re.Match[str]] = []
    for match in _NUMBER_TOKEN_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if len(digits) < 2 or digits in allowed:
            continue
        # Ordinal card numbers and years/quarters can be rendered from structure.
        if digits in {"1", "2", "3"} or re.fullmatch(r"20\d{2}", digits):
            continue
        unknown.append(match)
    return tuple(unknown)


def _numeric_diagnostic_category(match: re.Match[str], text: str) -> str:
    # A clause is local enough to bind a number to its own label: in
    # "99 млн, до метро 17 минут" the price label must not classify 17.
    left_boundary = max(text.rfind(marker, 0, match.start()) for marker in ",.;!?\n") + 1
    right_candidates = [position for marker in ",.;!?\n" if (position := text.find(marker, match.end())) >= 0]
    right_boundary = min(right_candidates) if right_candidates else len(text)
    context = text[left_boundary:right_boundary].casefold().replace("ё", "е")
    if re.search(r"м²|м2|кв\.?\s*м|площад", context):
        return "numeric_area_not_in_canonical"
    if re.search(r"цен|стоим|млн|руб|₽|бюджет", context):
        return "numeric_price_not_in_canonical"
    if re.search(r"минут|мин\b|метро|пеш|ехать|дорог|маршрут|останов|транспорт", context):
        return "numeric_transit_not_in_canonical"
    return "numeric_other_not_in_canonical"


def _allowed_number_tokens(brief: ResponseBrief, *, writer_text: str = "") -> set[str]:
    out = {"1", "2", "3"}
    for card in brief.canonical_cards:
        raw = json.dumps(to_jsonable(card), ensure_ascii=False)
        for token in _NUMBER_TOKEN_RE.findall(raw):
            digits = re.sub(r"\D", "", token)
            if digits:
                out.add(digits)
                if len(digits) >= 7:
                    # Allow rounded million text such as 12,2 for 12200000.
                    out.add(str(round(int(digits) / 1_000_000, 1)).replace(".", ""))
                    out.add(str(round(int(digits) / 1_000_000)).replace(".", ""))
    for token in _NUMBER_TOKEN_RE.findall(writer_text):
        digits = re.sub(r"\D", "", token)
        if digits:
            out.add(digits)
    return out


def _claim_allowed_by_brief(text: str, brief: ResponseBrief) -> bool:
    lowered = text.casefold()
    if re.search(r"\b\d+(?:[,.]\d+)?\s*%|ставк", lowered):
        return any(card.discount and re.search(r"\b\d+(?:[,.]\d+)?\s*%|ставк", str(card.discount), re.I) for card in brief.canonical_cards)
    # The canonical V2 card currently has no fields proving tenant demand,
    # rental income, liquidity, payback or speed of a deal.  These claims are
    # therefore never enabled by ads/sales/readiness counters.
    return False


def _requires_context_acknowledgement(brief: ResponseBrief) -> bool:
    return brief.answer_goal in {"answer_about_current_options", "answer_financing_without_inventing_terms"}


def _has_context_acknowledgement(text: str, acknowledgement: str) -> bool:
    """Keep conversational follow-ups anchored to the current user request.

    Search-result composition may freely rewrite the short "нашла варианты" intro,
    but follow-ups over already shown options must not sound like a fresh generic
    listing.  A small lexical overlap is enough: it lets the LLM phrase naturally
    while forcing it to acknowledge the active angle such as "под аренду" or
    "по текущему списку".
    """

    haystack = text.casefold().replace("ё", "е")
    source = acknowledgement.casefold().replace("ё", "е")
    anchors = [
        "текущ",
        "без нового поиска",
        "под аренду",
        "для инвестицион",
        "для семьи",
        "для жизни",
        "по ипотек",
        "по оплат",
        "первоначальн",
    ]
    return any(anchor in source and anchor in haystack for anchor in anchors)


def _has_duplicate_answer(text: str) -> bool:
    parts = [re.sub(r"\s+", " ", p.strip()).casefold() for p in re.split(r"\n{2,}", text) if len(p.strip()) > 20]
    return len(parts) != len(set(parts))


def _has_repeated_identical_benefit(text: str) -> bool:
    benefit_lines = []
    for line in text.splitlines():
        clean = re.sub(r"^\s*\d+\.\s*", "", line).strip()
        if len(clean) > 35 and not clean.startswith("ЖК") and "?" not in clean:
            benefit_lines.append(clean.casefold())
    return len(benefit_lines) != len(set(benefit_lines))
