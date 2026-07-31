from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import V1Action, V1AnswerKind, deep_freeze, deep_thaw


@dataclass(frozen=True)
class V1ResponsePlan:
    answer_kind: V1AnswerKind
    exact_cards: tuple[Mapping[str, Any], ...] = ()
    near_cards: tuple[Mapping[str, Any], ...] = ()
    missing_facts: tuple[str, ...] = ()
    cta: str = "Уточнить ещё одно условие?"
    operator_eligible: bool = False
    fallback_text: str = "Сейчас не получилось безопасно проверить варианты. Попробуем ещё раз?"
    action: V1Action | None = None
    context: Mapping[str, Any] = None

    def __post_init__(self):
        object.__setattr__(self, "answer_kind", V1AnswerKind.coerce(self.answer_kind))
        object.__setattr__(self, "action", None if self.action is None else V1Action.coerce(self.action))
        exact = tuple(deep_freeze(c) for c in (self.exact_cards or ()))[:3]
        near = () if exact else tuple(deep_freeze(c) for c in (self.near_cards or ()))[:2]
        object.__setattr__(self, "exact_cards", exact)
        object.__setattr__(self, "near_cards", near)
        object.__setattr__(self, "missing_facts", tuple(self.missing_facts or ()))
        object.__setattr__(self, "context", deep_freeze(self.context or {}))

    def to_dict(self) -> dict[str, Any]:
        return {"answer_kind": self.answer_kind.value, "action": None if self.action is None else self.action.value, "exact_cards": [deep_thaw(c) for c in self.exact_cards], "near_cards": [deep_thaw(c) for c in self.near_cards], "missing_facts": list(self.missing_facts), "context": deep_thaw(self.context), "cta": self.cta, "operator_eligible": self.operator_eligible, "fallback_text": self.fallback_text}


def build_response_plan(answer_kind, search_result=None, transition_reason: str | None = None, current_cards=(), action=None, response_context=None) -> V1ResponsePlan:
    if transition_reason:
        return V1ResponsePlan(V1AnswerKind.SAFE_ERROR, action=action, context=response_context, cta="Сформулируете запрос чуть точнее?", fallback_text="Не хочу ошибиться и поэтому уточню. Сформулируете запрос чуть точнее?")
    if search_result and search_result.error_code:
        return V1ResponsePlan(V1AnswerKind.SAFE_ERROR, action=action, context=response_context)
    if search_result:
        from .search_contract import project_public_card
        return V1ResponsePlan(answer_kind, action=action, exact_cards=tuple(project_public_card(c) for c in search_result.exact), near_cards=tuple(project_public_card(c) for c in search_result.near), missing_facts=search_result.missing, context=response_context, cta="Хотите выбрать один из этих вариантов?")
    if current_cards:
        return V1ResponsePlan(answer_kind, action=action, exact_cards=tuple(current_cards), near_cards=(), context=response_context, cta="Хотите выбрать один из этих вариантов?")
    return V1ResponsePlan(answer_kind, action=action, context=response_context, cta="Что делаем дальше?")


def render_response(plan: V1ResponsePlan) -> str:
    if plan.answer_kind == V1AnswerKind.SAFE_ERROR:
        return _one_question(plan.fallback_text, plan.cta)
    if plan.action == V1Action.SELECT_PROJECT:
        name = _card_name(plan.context.get("selected_project"), "этот ЖК")
        return _one_question(f"Выбрала {name}.", "Показать квартиры и лоты в этом ЖК?")
    if plan.action == V1Action.SELECT_LOT:
        name = _card_name(plan.context.get("selected_lot"), "этот лот")
        return _one_question(f"Выбрала {name}.", "Что уточнить по этому лоту?")
    if plan.action == V1Action.FACT_CHECK:
        fact = _requested_fact_phrase(plan.context.get("requested_facts"))
        return _one_question(f"По {fact} сейчас нет подтверждённых актуальных данных. Могу продолжить подбор по другим условиям.", "Уточнить другой параметр?")
    if plan.action == V1Action.OFF_TOPIC:
        return _one_question("Я лучше помогу с новостройками: выбрать район, бюджет или подходящий ЖК.", "Начнём подбор?")
    if plan.action == V1Action.DECLINE_OPERATOR:
        return _one_question("Хорошо, без оператора. Продолжим подбор по вашим условиям.", "Показать варианты?")
    if plan.action == V1Action.OFFER_OPERATOR:
        return _one_question("Могу позвать оператора и передать только то, что вы уже подтвердили.", "Позвать оператора?")
    if plan.action == V1Action.ACCEPT_OPERATOR:
        return _one_question("Хорошо, подготовлю обращение к оператору.", "Как вас зовут?")
    if plan.action == V1Action.CAPTURE_NAME:
        return _one_question("Спасибо. Напишите номер телефона — я сохраню его в скрытом виде.", "Какой номер?")
    if plan.action == V1Action.CAPTURE_PHONE:
        return _one_question("Спасибо, сохранила скрытый номер. Оператору можно будет передать только подтверждённый контекст и скрытый номер.", "Что ещё уточнить?")
    cards = plan.exact_cards or plan.near_cards
    if plan.answer_kind == V1AnswerKind.SEARCH_RESULTS and not cards:
        return _one_question("По этим условиям точных вариантов не нашла. Можно расширить поиск или изменить одно условие.", "Расширить поиск?")
    if cards:
        label = "Нашла точные варианты:" if plan.exact_cards else "Точных совпадений нет, но есть близкие варианты:"
        lines = [label]
        for c in cards:
            facts = c.get("facts", {}) or {}
            suffix = _facts_phrase(facts)
            lines.append(f"— {c.get('name', 'вариант')}{suffix}")
        lines.append(plan.cta)
        return "\n".join(lines)
    if plan.answer_kind == V1AnswerKind.OPERATOR:
        return _one_question("Могу помочь с подбором или безопасно подготовить обращение к оператору.", "Что выбираем?")
    return _one_question("Поняла. Я могу продолжить подбор по сохранённым условиям.", plan.cta)


def _card_name(card: Any, fallback: str) -> str:
    if isinstance(card, Mapping):
        name = card.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return fallback


def _requested_fact_phrase(facts: Any) -> str:
    labels = {"completion": "сроку сдачи", "price": "цене", "rooms": "комнатности", "finishing": "отделке", "ready": "готовности"}
    if isinstance(facts, (list, tuple)) and facts:
        return labels.get(str(facts[0]), "запрошенному факту")
    return "запрошенному факту"


def _facts_phrase(facts: Mapping[str, Any]) -> str:
    bits = []
    for k in ("location", "rooms", "price", "min_price", "finishing", "completion", "ready"):
        if k in facts:
            bits.append(str(facts[k]))
    return (": " + ", ".join(bits)) if bits else ""


def _one_question(prefix: str, cta: str) -> str:
    text = prefix.strip()
    text = re.sub(r"\?+", ".", text) if cta.endswith("?") else text
    return f"{text} {cta}".strip()
