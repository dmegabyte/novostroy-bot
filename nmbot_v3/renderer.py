"""Deterministic, local-only V3 response planning and rendering.

This owner accepts already validated V3 intent/evidence objects and the closed
presentation DTO.  It neither invokes a writer nor selects a provider: all
public prose is derived from confirmed evidence by deterministic rules.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .contracts import EMAIL_RE, IntentGoalV3, IntentPlanV3, PHONE_RE, V3_ALLOWED_FACTS, V3_PENDING_FOLLOWUP_KEYS
from .evidence_contract import CanonicalCard, EvidenceResult
from .presentation import (
    V3PresentationCard,
    V3WriterBriefInput,
    V3WriterCardOutput,
    V3WriterOutput,
    build_v3_writer_brief,
    validate_v3_writer_output,
)


_INTERNAL_TERM_RE = re.compile(
    r"\b(?:mcp|json|regex|traceback|openrouter|gateway|overmind|payload|"
    r"diagnostics?|optioncard|enum|prompt|api[_ -]?key|token|secret|"
    r"internal(?:\s+field)?|внутренн\w*|пейлоад|диагностик\w*)\b|```|[{}\[\]]",
    re.I,
)
_FIELD_LABELS = {
    "location": "локация", "district": "район", "price": "цена",
    "price_min": "цена от", "price_range": "диапазон цен", "rooms": "комнатность",
    "room_formats": "форматы квартир", "area": "площадь", "ready": "срок готовности",
    "finishing": "отделка", "metro": "метро", "developer": "застройщик",
    "property_class": "класс", "infrastructure": "инфраструктура", "schools": "школы",
    "kindergartens": "детские сады", "parks": "парки", "yards": "дворы",
    "playgrounds": "детские площадки", "clinics": "поликлиники", "sales_count": "продажи",
    "sales_date": "дата продаж", "ads_count": "объявления", "discount": "скидка",
    "parking": "паркинг", "mortgage_terms": "условия ипотеки",
    "apartment_inventory": "актуальное наличие квартир", "name": "название",
}
_SAFE_FALLBACK_TEXT = "Не могу надёжно подтвердить информацию, поэтому не буду гадать."
_SAFE_FALLBACK_QUESTION = "Уточните, пожалуйста, что для вас важнее всего?"


@dataclass(frozen=True)
class V3ResponsePlan:
    """Closed, deterministic public response before transport serialization."""

    intro: str
    cards: tuple[V3WriterCardOutput, ...]
    missing_note: str
    final_question: str
    recommendation: str = ""

    def to_writer_output(self) -> V3WriterOutput:
        return V3WriterOutput(
            intro=self.intro,
            cards=self.cards,
            recommendation=self.recommendation,
            missing_note=self.missing_note,
            final_question=self.final_question,
        )

    @property
    def public_text(self) -> str:
        parts = [self.intro]
        parts.extend(f"{card.name}: {card.text}" for card in self.cards)
        if self.recommendation:
            parts.append(self.recommendation)
        if self.missing_note:
            parts.append(self.missing_note)
        parts.append(self.final_question)
        return "\n\n".join(part for part in parts if part)


@dataclass(frozen=True)
class V3RenderResult:
    """A safe render result; errors are stable internal codes, never provider text."""

    ok: bool
    response: V3ResponsePlan
    errors: tuple[str, ...] = ()


def render_v3_writer_publication(
    output: V3WriterOutput,
    presentation: V3WriterBriefInput,
) -> V3RenderResult:
    """Accept writer prose only when the same mechanical public rules hold.

    The caller may ignore a failed result and retain its already-approved
    deterministic response; this helper never participates in state decisions.
    """
    try:
        errors = validate_v3_writer_output(output, build_v3_writer_brief(presentation))
        response = V3ResponsePlan(
            output.intro, output.cards, output.missing_note, output.final_question,
            output.recommendation,
        )
        if errors or _contains_unsafe_public_text(response.public_text):
            return _fallback(tuple(sorted(set((*errors, "unsafe_writer_publication")))))
        return V3RenderResult(True, response)
    except (TypeError, ValueError):
        return _fallback(("invalid_writer_publication",))


def render_v3_response(
    plan: IntentPlanV3,
    evidence: EvidenceResult,
    presentation: V3WriterBriefInput,
    *,
    pending_followup_key: str | None = None,
) -> V3RenderResult:
    """Render V3-only inputs without I/O, mutation, prose generation, or repair."""

    try:
        errors = _validate_input(plan, evidence, presentation)
        if errors:
            return _fallback(errors)
        if pending_followup_key not in {None, *V3_PENDING_FOLLOWUP_KEYS}:
            return _fallback(("invalid_pending_followup_key",))
        financing_response = _financing_pending_response(plan, pending_followup_key)
        if financing_response is not None:
            return V3RenderResult(True, financing_response)
        cards = tuple((*evidence.facts, *evidence.near))
        output_cards = tuple(
            V3WriterCardOutput(card.name, _render_card(card)) for card in cards
        )
        response = V3ResponsePlan(
            intro=_intro_for(plan, evidence),
            cards=output_cards,
            missing_note=_missing_note(evidence.missing_facts),
            final_question=_final_question(plan, presentation),
        )
        writer_output = response.to_writer_output()
        output_errors = validate_v3_writer_output(
            writer_output, build_v3_writer_brief(presentation)
        )
        if output_errors or _contains_unsafe_public_text(response.public_text):
            return _fallback(tuple(sorted(set((*output_errors, "unsafe_public_output")))))
        return V3RenderResult(True, response)
    except (TypeError, ValueError):
        return _fallback(("invalid_renderer_input",))


def _validate_input(
    plan: IntentPlanV3, evidence: EvidenceResult, presentation: V3WriterBriefInput
) -> tuple[str, ...]:
    if not isinstance(plan, IntentPlanV3):
        return ("invalid_intent_plan",)
    if not isinstance(evidence, EvidenceResult):
        return ("invalid_evidence_result",)
    if not isinstance(presentation, V3WriterBriefInput):
        return ("invalid_presentation_input",)
    cards = tuple((*evidence.facts, *evidence.near))
    if tuple(card.name for card in presentation.cards) != tuple(card.name for card in cards):
        return ("presentation_card_order_or_names_mismatch",)
    if any(card.confirmed_facts != evidence_card.fields for card, evidence_card in zip(presentation.cards, cards)):
        return ("presentation_evidence_mismatch",)
    if plan.goal is IntentGoalV3.LOOKUP_OBJECT:
        if len(cards) > 1 or any(card.name != plan.named_object_reference for card in cards):
            return ("named_evidence_mismatch",)
    if plan.goal is IntentGoalV3.COMPARE_CURRENT and plan.comparison_option_names:
        if tuple(card.name for card in cards) != plan.comparison_option_names:
            return ("current_option_evidence_mismatch",)
    if plan.goal is IntentGoalV3.ANSWER_SELECTED and plan.selected_option_name:
        if len(cards) != 1 or cards[0].name != plan.selected_option_name:
            return ("selected_option_evidence_mismatch",)
    if any(not set(card.fields).issubset(V3_ALLOWED_FACTS) for card in cards):
        return ("unsupported_evidence_fact",)
    source_text = " ".join(
        part for card in cards for part in (card.name, *_flatten_text(card.fields))
    )
    if _contains_unsafe_public_text(source_text):
        return ("unsafe_evidence",)
    if presentation.mandatory_cta and _contains_unsafe_public_text(presentation.mandatory_cta):
        return ("unsafe_mandatory_cta",)
    if plan.goal is IntentGoalV3.CLARIFY and _contains_unsafe_public_text(plan.clarification or ""):
        return ("unsafe_clarification",)
    return ()


def _flatten_text(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return tuple(part for item in value.values() for part in _flatten_text(item))
    if isinstance(value, (tuple, list)):
        return tuple(part for item in value for part in _flatten_text(item))
    return (str(value),)


def _render_card(card: CanonicalCard) -> str:
    facts = [
        f"{_FIELD_LABELS[key]}: {_render_value(value)}"
        for key, value in card.fields.items()
        if key in V3_ALLOWED_FACTS
    ]
    if card.is_near:
        facts.append("близкий вариант: " + ", ".join(card.differences))
    return "; ".join(facts) if facts else "Подтверждённых деталей пока мало."


def _render_value(value: Any) -> str:
    if isinstance(value, Mapping):
        return ", ".join(_render_value(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return ", ".join(_render_value(item) for item in value)
    return str(value)


def _intro_for(plan: IntentPlanV3, evidence: EvidenceResult) -> str:
    if plan.goal is IntentGoalV3.LOOKUP_OBJECT:
        name = plan.named_object_reference
        if not evidence.facts:
            return f"По ЖК «{name}» пока не нашла подтверждённой информации."
        return f"Нашла подтверждённые данные по ЖК «{name}»."
    if plan.goal is IntentGoalV3.COMPARE_CURRENT and evidence.facts:
        return "Сравню только текущие варианты по подтверждённым данным."
    if plan.goal is IntentGoalV3.ANSWER_SELECTED and evidence.facts:
        return f"Вот подтверждённые данные по ЖК «{evidence.facts[0].name}»."
    if evidence.facts:
        return "Нашла подтверждённые варианты."
    if evidence.near:
        return "Точных совпадений пока нет, но есть близкие варианты."
    return "Пока нет подтверждённых вариантов."


def _missing_note(missing_facts: tuple[str, ...]) -> str:
    if not missing_facts:
        return ""
    labels = [_FIELD_LABELS.get(fact) for fact in missing_facts if fact in _FIELD_LABELS]
    if not labels:
        return ""
    return "Пока не подтверждены: " + ", ".join(labels) + "."


def _final_question(plan: IntentPlanV3, presentation: V3WriterBriefInput) -> str:
    raw = presentation.mandatory_cta or (
        plan.clarification if plan.goal is IntentGoalV3.CLARIFY else "Какой вариант хотите рассмотреть подробнее?"
    )
    question = raw.rstrip("?.! ") + "?"
    if question.count("?") != 1:
        raise ValueError("invalid_final_question")
    return question


def _financing_pending_response(
    plan: IntentPlanV3, pending_followup_key: str | None
) -> V3ResponsePlan | None:
    """Render only the closed financing consent loop, with no contact capture."""

    if pending_followup_key != "financing_consent":
        return None
    if plan.followup_outcome == "decline":
        return V3ResponsePlan(
            "Хорошо, не буду передавать запрос по условиям ипотеки.",
            (),
            "",
            "Хотите сузить варианты по бюджету, району или отделке?",
        )
    if plan.followup_outcome in {None, "ask_or_clarify", "unexpected"}:
        return V3ResponsePlan(
            "Условия ипотеки сначала нужно уточнить по выбранному ЖК.",
            (),
            "",
            "Проверить условия по этому ЖК?",
        )
    return None


def _contains_unsafe_public_text(text: str) -> bool:
    return bool(PHONE_RE.search(text) or EMAIL_RE.search(text) or _INTERNAL_TERM_RE.search(text))


def _fallback(errors: tuple[str, ...]) -> V3RenderResult:
    response = V3ResponsePlan(_SAFE_FALLBACK_TEXT, (), "", _SAFE_FALLBACK_QUESTION)
    return V3RenderResult(False, response, tuple(sorted(set(errors))))
