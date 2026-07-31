"""Безопасно формирует контекст и трассировку для semantic planner."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # direct script execution keeps scripts/ on sys.path
    from planner_trace import append_event as append_planner_trace_event
except ImportError:  # package-style tests import scripts.*
    from .planner_trace import append_event as append_planner_trace_event  # type: ignore


TURN_ACTIONS = {"search", "clarify", "operator_contact", "recover_dialogue", "answer_current_options", "capture_contact", "off_topic"}
TURN_TARGETS = {"new_search", "current_options", "none", "operator"}
TURN_SEARCH_POLICIES = {"required", "forbidden"}
CANONICAL_INTENTS = {"investment", "rental", "family", "life", "mortgage", "unknown"}
SENSITIVE_KEY_RE = re.compile(r"phone|телефон|contact|client_id|chat_id|site_id|sender|token|secret|raw|payload|dialog_window", re.I)
PHONE_LIKE_RE = re.compile(r"(?:\+?\d[\s()\-.]*){10,15}")
EMAIL_LIKE_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_CLASS_AS_VALUE_RE = re.compile(r"^(comfort|business|premium|elite|комфорт|бизнес|премиум|элитн\w*)$", re.I)

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

_SCENARIO_CONTEXT_HINTS: dict[str, dict[str, Any]] = {
    "family": {"client_need_label": "квартира для семьи", "scenario_reasons": ["schools", "kindergartens", "parks", "family_infrastructure"], "answer_angles": ["ежедневное удобство семьи", "детская инфраструктура", "среда рядом с домом"]},
    "investment": {"client_need_label": "покупка как инвестиция", "scenario_reasons": ["entry_price", "deadline", "location", "liquidity_context"], "answer_angles": ["порог входа", "срок готовности", "понятные факторы спроса"]},
    "rental": {"client_need_label": "квартира под аренду", "scenario_reasons": ["metro", "finishing", "readiness", "area"], "answer_angles": ["быстрый запуск аренды", "транспорт", "ремонт и готовность"]},
    "self_use": {"client_need_label": "квартира для себя", "scenario_reasons": ["location", "transport", "readiness", "finishing"], "answer_angles": ["удобство жизни", "срок и формат покупки"]},
}


@dataclass(frozen=True)
class TurnDecision:
    """Описывает безопасное решение планировщика для текущего хода."""

    action: str
    target: str = "none"
    search_policy: str = "required"
    response: str = ""

    @classmethod
    def from_meta(cls, search_meta: dict[str, Any] | None, *, fallback_action: str = "search") -> "TurnDecision":
        """Восстанавливает решение из метаданных и заменяет неизвестные значения безопасными."""
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
        """Возвращает ограниченную версию решения для трассировки и журналов."""
        return {"action": self.action, "target": self.target, "search_policy": self.search_policy}


def _looks_missing(value: Any) -> bool:
    """Проверяет, означает ли значение отсутствие полезных данных."""
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip().lower()
        return text in {"", "н/д", "нет", "none", "null", "unknown", "не указано", "неизвестно"}
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def safe_nested_value(value: Any, *, depth: int = 0) -> Any:
    """Очищает вложенное значение, ограничивая глубину, размер и чувствительные данные."""
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
        return [safe_nested_value(item, depth=depth + 1) for item in value[:8]]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)[:80]
            if SENSITIVE_KEY_RE.search(key_text):
                continue
            cleaned = safe_nested_value(item, depth=depth + 1)
            if cleaned not in (None, "", [], {}):
                safe[key_text] = cleaned
        return safe
    text = str(value)[:200]
    return "" if PHONE_LIKE_RE.search(text) else text


def safe_option_payload(option: dict[str, Any] | None) -> dict[str, Any]:
    """Оставляет в карточке объекта только разрешённые и безопасные поля."""
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
        safe[key] = safe_nested_value(value)
    return {k: v for k, v in safe.items() if v not in (None, "", [], {})}


def _last_dialog_user_text(state: dict[str, Any]) -> str:
    """Находит последний текст клиента в ограниченном окне диалога."""
    for turn in reversed(state.get("dialog_window") or []):
        if isinstance(turn, dict) and turn.get("role") == "user":
            return str(turn.get("text") or "")
    return ""


def active_task(state: dict[str, Any] | None) -> dict[str, Any]:
    """Возвращает безопасную копию активной задачи диалога."""
    if not isinstance(state, dict):
        return {}
    task = state.get("active_task")
    return dict(task) if isinstance(task, dict) else {}


def active_scenario(state: dict[str, Any] | None) -> dict[str, Any]:
    """Возвращает безопасную копию активного сценария диалога."""
    if not isinstance(state, dict):
        return {}
    scenario = state.get("active_scenario")
    return dict(scenario) if isinstance(scenario, dict) else {}


def _is_short_positive_answer(text: str) -> bool:
    """Проверяет, является ли ответ клиента коротким подтверждением."""
    compact = re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е")).strip()
    return bool(re.fullmatch(r"(да|ага|угу|ок|окей|хорошо|давай|хочу|можно|готов|готова|конечно)", compact))


def _dialog_planner_expected_action_class(*, bot_question: str, client_answer: str, offer_type: str, answer_kind: str, selected_option: dict[str, Any]) -> str:
    """Определяет ожидаемое действие после подтверждения выбранного объекта."""
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


def dialog_planner_last_turn_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Формирует безопасное описание последнего обмена для планировщика."""
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


def _neutral_primary_scenario(user_text: str, state: dict[str, Any]) -> str:
    """Определяет основной сценарий клиента без обращения к канальному runtime."""
    params = state.get("params") if isinstance(state.get("params"), dict) else {}
    active = active_scenario(state)
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
    """Проверяет наличие в запросе признаков ипотеки или условий оплаты."""
    mortgage_type = str(params.get("mortgage_type") or "").strip().lower()
    facets = params.get("facets") if isinstance(params.get("facets"), list) else []
    if mortgage_type or any(str(item).strip().lower() == "mortgage" for item in facets):
        return True
    return any(token in text for token in ("ипот", "льготн", "господдерж", "семейную ипот", "семейная ипот", "маткапитал", "первонач", "первый взнос", "ставк", "рассроч", "скидк", "платеж", "платёж"))


def extract_conversation_followup_signals(user_text: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Извлекает из текущей реплики тему, область ответа и необходимость оператора."""
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
    """Разрешает ссылки клиента вроде «эти» или «все» на текущие карточки."""
    text = re.sub(r"\s+", " ", (user_text or "").lower().replace("ё", "е")).strip()
    options = state.get("visible_options") or state.get("last_options") or []
    names = [str(o.get("name") or "") for o in options[:5] if isinstance(o, dict) and o.get("name")]
    pronoun_match = re.search(r"\b(они|эти|них|ним|ними|все|оба|обе)\b", text)
    if pronoun_match and names:
        return {"phrase": pronoun_match.group(1), "resolved_to": "current_options", "option_names": names}
    return {"phrase": "", "resolved_to": "", "option_names": names}


def scenario_context_payload(user_text: str, state: dict[str, Any]) -> dict[str, Any]:
    """Собирает сценарный контекст и правила, которые планировщик обязан соблюдать."""
    params = state.get("params") if isinstance(state.get("params"), dict) else {}
    options = state.get("visible_options") or state.get("last_options") or []
    primary = _neutral_primary_scenario(user_text, state)
    hints = dict(_SCENARIO_CONTEXT_HINTS.get(primary) or _SCENARIO_CONTEXT_HINTS["self_use"])
    followup = extract_conversation_followup_signals(user_text, state)
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
    current_options = [safe_option_payload(option) for option in options[:5] if isinstance(option, dict)]
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


def dialog_planner_state_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Строит ограниченный снимок состояния для semantic planner."""
    selected = state.get("selected_option") if isinstance(state.get("selected_option"), dict) else {}
    user_text = _last_dialog_user_text(state)
    return {
        "params": dict(state.get("params") or {}),
        "selected_option": safe_option_payload(selected),
        "visible_options": [safe_option_payload(o) for o in (state.get("visible_options") or [])[:5] if isinstance(o, dict)],
        "last_options": [safe_option_payload(o) for o in (state.get("last_options") or [])[:5] if isinstance(o, dict)],
        "rejected_option_names": [str(x) for x in (state.get("rejected_option_names") or []) if str(x).strip()],
        "last_bot_question": state.get("last_bot_question") or "",
        "last_offer_type": state.get("last_offer_type") or "",
        "last_answer_kind": state.get("last_answer_kind") or "",
        "last_turn": dialog_planner_last_turn_payload(state),
        "active_task": active_task(state),
        "active_scenario": active_scenario(state),
        "scenario_context": scenario_context_payload(user_text, state),
        "numeric_choice_policy": state.get("numeric_choice_policy") or "accept",
        "conversation_followup": extract_conversation_followup_signals(user_text, state),
    }


def canonical_intent_value(value: Any) -> str:
    """Приводит значение намерения к одному из канонических идентификаторов."""
    text = str(value or "").strip().casefold()
    if text in CANONICAL_INTENTS:
        return text
    return INTENT_VALUE_ALIASES.get(text, "unknown")


def derive_canonical_primary_intent(state: dict[str, Any], *, planner_plan: dict[str, Any] | None = None, search_params: dict[str, Any] | None = None) -> str:
    """Определяет намерение по состоянию и доступным безопасным источникам контекста."""
    state_intent = canonical_intent_value(state.get("primary_intent"))
    if state_intent != "unknown":
        return state_intent
    state_params = state.get("params") if isinstance(state.get("params"), dict) else {}
    param_intent = canonical_intent_value(state_params.get("primary_intent"))
    if param_intent != "unknown":
        return param_intent
    if isinstance(planner_plan, dict):
        planner_intent = canonical_intent_value(planner_plan.get("intent"))
        if planner_intent != "unknown":
            return planner_intent
    for params in (search_params, state_params):
        if not isinstance(params, dict):
            continue
        for key in ("primary_intent", "purpose", "scenario"):
            mapped = canonical_intent_value(params.get(key))
            if mapped != "unknown":
                return mapped
    scenario_context = state.get("scenario_context") if isinstance(state.get("scenario_context"), dict) else {}
    active = state.get("active_scenario") if isinstance(state.get("active_scenario"), dict) else {}
    for source in (scenario_context, active):
        for key in ("primary_scenario", "purpose", "intent"):
            mapped = canonical_intent_value(source.get(key))
            if mapped != "unknown":
                return mapped
    return "unknown"


def canonical_known_fields_from_state(state: dict[str, Any]) -> set[str]:
    """Собирает список уже известных планировщику полей без передачи сырых данных."""
    fields = {str(key) for key, value in (state.get("params") or {}).items() if value not in (None, "", [], {})}
    if fields & {"purpose", "scenario", "primary_intent"}:
        fields.update({"purpose", "primary_intent"})
    scenario_context = state.get("scenario_context") if isinstance(state.get("scenario_context"), dict) else {}
    active = state.get("active_scenario") if isinstance(state.get("active_scenario"), dict) else {}
    for source in (scenario_context, active):
        for key in ("primary_scenario", "purpose", "intent"):
            if source.get(key):
                fields.update({"purpose", "primary_intent", str(key)})
    return fields


def safe_planner_state(text: str, state: dict[str, Any]) -> dict[str, Any]:
    """Формирует полный редактированный контекст, который можно передать планировщику."""
    payload = dialog_planner_state_payload(state)
    primary_intent = derive_canonical_primary_intent(state)
    known_fields = canonical_known_fields_from_state(state)
    if primary_intent != "unknown":
        known_fields.update({"primary_intent", "purpose"})
    return {
        "params": safe_nested_value(payload.get("params") or {}),
        "primary_intent": primary_intent,
        "known_fields": sorted(known_fields),
        "selected_option": safe_nested_value(payload.get("selected_option") or {}),
        "visible_options": safe_nested_value(payload.get("visible_options") or []),
        "last_options": safe_nested_value(payload.get("last_options") or []),
        "rejected_option_names": safe_nested_value(payload.get("rejected_option_names") or []),
        "last_bot_question": safe_nested_value(state.get("last_bot_question") or ""),
        "last_offer_type": safe_nested_value(state.get("last_offer_type") or ""),
        "last_answer_kind": safe_nested_value(state.get("last_answer_kind") or ""),
        "last_turn": safe_nested_value(payload.get("last_turn") or {}),
        "active_task": safe_nested_value(payload.get("active_task") or {}),
        "active_scenario": safe_nested_value(payload.get("active_scenario") or {}),
        "scenario_context": safe_nested_value(payload.get("scenario_context") or {}),
        "numeric_choice_policy": safe_nested_value(payload.get("numeric_choice_policy") or "accept"),
        "conversation_followup": safe_nested_value(payload.get("conversation_followup") or {}),
        "last_search_snapshot": safe_nested_value(state.get("last_search_snapshot") or {}),
        "retry_search": safe_nested_value(state.get("retry_search") or {}),
        "pending_followup": safe_nested_value(state.get("pending_followup") or {}),
        "current_options_scope": safe_nested_value(state.get("current_options_scope") or "unknown"),
        "recovery_count": int(state.get("recovery_count") or 0),
    }


def safe_turn_context(text: str, state: dict[str, Any]) -> dict[str, Any]:
    """Формирует минимальный контекст хода и контракт допустимых действий."""
    payload = dialog_planner_state_payload(state)
    return {
        "last_bot_question": state.get("last_bot_question") or "",
        "last_offer_type": state.get("last_offer_type") or "",
        "last_answer_kind": state.get("last_answer_kind") or "",
        "recovery_count": int(state.get("recovery_count") or 0),
        "params": payload.get("params") or {},
        "selected_option": payload.get("selected_option") or {},
        "current_options": payload.get("visible_options") or payload.get("last_options") or [],
        "scenario_context": payload.get("scenario_context") or {},
        "turn_contract": {
            "actions": sorted(TURN_ACTIONS - {"capture_contact"}),
            "targets": sorted(TURN_TARGETS),
            "search_policies": sorted(TURN_SEARCH_POLICIES),
            "rules": [
                "recover_dialogue: no facts/options/operator; one friendly question <=300 chars; search_policy=forbidden",
                "answer_current_options: target=current_options; search_policy=forbidden; do not rebuild visible options",
                "operator_contact: ask for client phone; no new search",
            ],
        },
    }


def append_safe_planner_trace(*, session_key: str, channel: str, plan: dict[str, Any] | None, decision: Any, exception_code: str | None = None, user_text: str = "", path: Path | None = None) -> None:
    """Записывает privacy-safe trace планировщика только для канала Jivo."""
    if channel != "jivo":
        return
    try:
        append_planner_trace_event(
            session_key=session_key,
            plan=plan if isinstance(plan, dict) else {},
            final_decision=decision,
            source="nmbot_api_server",
            exception_code=exception_code,
            user_text=user_text,
            path=path,
        )
    except Exception:
        return
