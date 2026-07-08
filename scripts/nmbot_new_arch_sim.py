#!/usr/bin/env python3
"""Мини-симулятор новой архитектуры Ирины.

Цель: посмотреть, как целевая схема

    planner -> decision context -> action resolver -> presenter -> validator

отрабатывает на маленьких сценариях без правки продового бота.

Запуск:
  python3 scripts/nmbot_new_arch_sim.py
  python3 scripts/nmbot_new_arch_sim.py --scenario operator
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from chat_tester_bot import (  # noqa: E402
    _extract_options,
    _format_operator_handoff_for_option,
    _format_options_summary_response,
    _format_option_response,
    _prepare_response_text,
)


SAMPLE_SEARCH_RESPONSE: dict[str, Any] = {
    "facts": [
        {
            "name": "ЖК «Лучи»",
            "location": "Солнцево",
            "price_range": "от 10 591 869 до 31 582 642 руб.",
            "finishing": "с отделкой",
            "metro": "информация уточняется",
            "area": "от 22.5 до 86.5 м²",
            "ready": "2027 г., 2 квартал",
            "link": "jk_luchi",
            "developer": "информация уточняется",
        },
        {
            "name": "Бусиновский парк",
            "location": "Западное Дегунино",
            "price_range": "от 12 103 290 до 36 645 507 руб.",
            "finishing": "с отделкой",
            "metro": "информация уточняется",
            "area": "от 20 до 89.3 м²",
            "ready": "2027 г., 2 квартал",
            "link": "jiloy_kompleks_businovskiy_park",
            "developer": "информация уточняется",
        },
    ],
    "near": [
        {
            "name": "ЖК «Южные Сады»",
            "location": "Южное Бутово",
            "price_range": "от 11 399 922 до 37 921 655 руб.",
            "finishing": "с отделкой",
            "why_close": "отличие: находится на расстоянии 6 км от МКАД",
            "metro": "информация уточняется",
            "area": "от 21.8 до 187.8 м²",
            "ready": "2027 г., 2 квартал",
            "link": "jk_yujnye_sady",
        }
    ],
    "missing": "Не удалось подтвердить точное наличие квартир в продаже в режиме реального времени.",
    "params": {"rooms": "2", "district": "msk", "purpose": "family"},
}

NEAR_ONLY_SEARCH_RESPONSE: dict[str, Any] = {
    "facts": [],
    "near": [
        {
            "name": "ЖК «Южные Сады»",
            "location": "Южное Бутово",
            "price_range": "от 11 399 922 до 37 921 655 руб.",
            "finishing": "с отделкой",
            "why_close": "отличие: находится на расстоянии 6 км от МКАД",
            "metro": "информация уточняется",
            "area": "от 21.8 до 187.8 м²",
            "ready": "2027 г., 2 квартал",
            "link": "jk_yujnye_sady",
        },
        {
            "name": "ЖК «Лучи»",
            "location": "Солнцево",
            "price_range": "от 10 591 869 до 31 582 642 руб.",
            "finishing": "с отделкой",
            "why_close": "отличие: рядом с районом поиска, но не exact match",
            "metro": "информация уточняется",
            "area": "от 22.5 до 86.5 м²",
            "ready": "2027 г., 2 квартал",
            "link": "jk_luchi",
        },
    ],
    "missing": "Точных совпадений нет, но есть близкие варианты поблизости.",
    "params": {"rooms": "2", "district": "msk", "purpose": "family"},
}

EMPTY_SEARCH_RESPONSE: dict[str, Any] = {
    "facts": [],
    "near": [],
    "missing": "По этому району нет подтверждённых новостроек в переданных данных.",
    "params": {"rooms": "2", "district": "unknown", "purpose": "family"},
}


@dataclass
class Scenario:
    name: str
    turns: list[str]
    search_response: dict[str, Any] | None = None


def _load_options(search_response: dict[str, Any]) -> list[dict[str, Any]]:
    raw = json.dumps(search_response, ensure_ascii=False)
    return _extract_options(raw)[:3]


def _load_cases_from_jsonl(path: Path, case_ids: set[str] | None = None) -> list[Scenario]:
    cases: list[Scenario] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        case_id = str(item.get("case_id") or "")
        if case_ids and case_id not in case_ids:
            continue
        search_response_raw = item.get("search_response")
        if isinstance(search_response_raw, str):
            try:
                search_response = json.loads(search_response_raw)
            except json.JSONDecodeError:
                search_response = {"facts": [], "near": [], "missing": search_response_raw, "params": {}}
        else:
            search_response = search_response_raw or {"facts": [], "near": [], "missing": "", "params": {}}

        cases.append(
            Scenario(
                case_id or str(len(cases) + 1),
                [str(item.get("user_text") or "")],
                search_response=search_response,
            )
        )
    return cases


def _search_summary(search_response: dict[str, Any]) -> dict[str, Any]:
    facts = search_response.get("facts") or []
    near = search_response.get("near") or []
    return {
        "facts_count": len(facts),
        "near_count": len(near),
        "has_exact": bool(facts),
        "has_near": bool(near),
        "has_missing": bool(search_response.get("missing")),
    }


def planner_intent(user_text: str, state: dict[str, Any]) -> str:
    text = user_text.lower().replace("ё", "е")
    selected = bool(state.get("selected_option"))
    summary = state.get("search_summary") or {}
    facts_count = int(summary.get("facts_count") or 0)
    near_count = int(summary.get("near_count") or 0)
    has_budget = bool(re.search(r"\b\d+\s*(млн|млн\.|миллион|руб)\b", text))
    has_rooms = bool(re.search(r"(студия\w*|студ\w*|1к\w*|1-к\w*|однуш\w*|2к\w*|2-к\w*|двуш\w*|3к\w*|треш\w*)", text))
    has_location = bool(re.search(r"(котельник\w*|солнцев\w*|бутов\w*|дегунин\w*|отрадн\w*|люберц\w*|мытищ\w*|новокосин\w*|балаших\w*|мкад|москва|мск|московск\w*)", text))
    has_finish = bool(re.search(r"(отделк|с отделкой|без отделк|чистов)", text))
    has_exact_combo = bool(has_rooms and (has_finish or "мкад" in text or has_budget))
    looks_like_real_estate = bool(
        re.search(r"\b(квартир|квартира|жк|дом|новострой|студия|однуш|двуш|треш|комнат|москва|москов|мск|котельник|солнцев|бутово|дегунино|мкад|отделк|бюджет|цена|район|метро)\b", text)
        or has_budget
    )
    if re.search(r"(ипотек|кредит|ставк|банк|первоначальн|взнос|пв|finance)", text):
        return "finance_terms"
    if re.search(r"(анекдот|шутк|юмор|мем|погода|курс доллара|рубероид|одесс|котик|песня|фильм)", text):
        return "off_topic"
    if re.search(r"(спб|санкт[- ]?петербург|питер)", text):
        return "no_results_area"
    if near_count > 0 and facts_count == 0 and re.search(r"(рядом|поблизост|ближе|near|поблизости)", text):
        return "near_only"
    if facts_count == 0 and near_count == 0 and re.search(r"(рядом|поблизост|ближе|near)", text):
        return "no_results_area"
    if re.search(r"\b(позови|оператор|менеджер|звон|связ[ьи]|контакт|номер)\b", text):
        return "operator_request"
    if re.search(r"\b(почему|зачем).*(называ|назван)|как.*называ", text):
        return "real_estate_related_unknown"
    if re.search(r"рубероид|одесс|бургер|погода|курс доллара", text):
        return "off_topic"
    if re.search(r"(сравн|критери|отлич|чем лучше)", text):
        return "comparison_criteria"
    if re.search(r"(без бюджета|пока без бюджета|широк|все варианты|посмотреть широко)", text):
        return "wide_search"
    if looks_like_real_estate and has_exact_combo and facts_count > 0:
        return "exact_match_list"
    if looks_like_real_estate and has_rooms and has_budget and (facts_count > 0 or near_count > 0):
        return "room_budget_list"
    if looks_like_real_estate and has_location and not has_budget and facts_count > 0:
        return "location_list"
    if re.search(r"\b(студия|студ|1к|1-к|2к|2-к|двуш|треш|метро|район|бюджет|солнцево|бутово|дегунино)\b", text):
        return "new_search"
    if re.search(r"(подробнее|расскажи|что по нему|что по этому|что по жк|про него)", text) and selected:
        return "selected_option_details"
    if re.fullmatch(r"\d{1,2}", text.strip()) and state.get("last_options"):
        return "select_option"
    if selected and re.search(r"(дальше|что дальше|интерес|подходит|беру|готов)", text):
        return "operator_request"
    if looks_like_real_estate:
        if facts_count == 0 and near_count > 0:
            return "near_only"
        if facts_count == 0 and near_count == 0:
            return "no_results_area"
        return "new_search"
    if state.get("last_options"):
        return "followup_clarification"
    return "new_search"


def build_decision_context(user_text: str, state: dict[str, Any], intent: str, search_response: dict[str, Any], options: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _search_summary(search_response)
    visible_options = []
    for option in options:
        visible_options.append(
            {
                "name": option.get("name"),
                "source": option.get("source") or ("near" if option.get("why_close") else "facts"),
                "match_status": option.get("match_status") or ("near_only" if option.get("why_close") else "exact"),
                "client_facts": [
                    f"{option.get('location')}" if option.get("location") else "",
                    f"{option.get('price')}" if option.get("price") else "",
                    f"{option.get('finishing')}" if option.get("finishing") else "",
                ],
                "why_close": option.get("why_close") or None,
                "do_not_say": option.get("do_not_say") or ([] if not option.get("why_close") else ["точно подходит под бюджет", "это exact match"]),
            }
        )
    visible_options = [item for item in visible_options if item.get("name")]

    if intent == "comparison_criteria":
        recommended_action = "explain_comparison_criteria"
        allowed_actions = ["explain_comparison_criteria"]
        risk_flags = ["must_explain_difference"]
    elif intent == "near_only":
        recommended_action = "render_near_only_response"
        allowed_actions = ["render_near_only_response"]
        risk_flags = ["near_only"]
    elif intent == "no_results_area":
        recommended_action = "render_no_results_area_response"
        allowed_actions = ["render_no_results_area_response"]
        risk_flags = ["no_results_area"]
    elif intent == "finance_terms":
        recommended_action = "render_finance_terms_response"
        allowed_actions = ["render_finance_terms_response"]
        risk_flags = ["finance_terms"]
    elif intent == "wide_search":
        recommended_action = "show_wide_starting_options"
        allowed_actions = ["show_wide_starting_options"]
        risk_flags = ["budget_missing", "wide_search_allowed"]
    elif intent in {"location_list", "exact_match_list", "room_budget_list", "new_search"}:
        recommended_action = "show_first_list"
        allowed_actions = ["show_first_list"]
        risk_flags = [intent]
    elif intent == "operator_request":
        recommended_action = "operator_handoff"
        allowed_actions = ["operator_handoff"]
        risk_flags = ["handoff_requested"]
    elif intent == "selected_option_details":
        recommended_action = "show_selected_details"
        allowed_actions = ["show_selected_details", "operator_handoff"]
        risk_flags = ["selected_option_present"]
    elif intent == "real_estate_related_unknown":
        recommended_action = "render_unknown_question_response"
        allowed_actions = ["render_unknown_question_response", "operator_handoff"]
        risk_flags = ["unknown_real_estate_question"]
    elif intent == "off_topic":
        recommended_action = "render_off_topic_boundary"
        allowed_actions = ["render_off_topic_boundary"]
        risk_flags = ["off_topic"]
    elif intent == "select_option":
        recommended_action = "show_selected_details"
        allowed_actions = ["show_selected_details"]
        risk_flags = ["selected_option"]
    else:
        recommended_action = "render_unclear_clarification"
        allowed_actions = ["render_unclear_clarification"]
        risk_flags = ["unclear"]

    return {
        "user_message": user_text,
        "stage": intent,
        "search_summary": summary,
        "visible_options": visible_options,
        "risk_flags": risk_flags,
        "allowed_actions": allowed_actions,
        "recommended_action": recommended_action,
        "state_snapshot": {
            "selected_option": state.get("selected_option"),
            "last_offer_type": state.get("last_offer_type"),
            "last_answer_kind": state.get("last_answer_kind"),
        },
    }


def resolve_action(intent: str, context: dict[str, Any], state: dict[str, Any]) -> str:
    allowed = set(context.get("allowed_actions") or [])
    if intent == "operator_request" and state.get("selected_option"):
        return "operator_handoff"
    if intent == "operator_request":
        return "operator_handoff"
    if intent == "selected_option_details":
        return "show_selected_details"
    if intent == "real_estate_related_unknown":
        return "render_unknown_question_response"
    if intent == "off_topic":
        return "render_off_topic_boundary"
    if intent == "comparison_criteria":
        return "explain_comparison_criteria"
    if intent == "near_only":
        return "render_near_only_response"
    if intent == "no_results_area":
        return "render_no_results_area_response"
    if intent == "finance_terms":
        return "render_finance_terms_response"
    if intent == "wide_search":
        return "show_wide_starting_options"
    if intent in {"location_list", "exact_match_list", "room_budget_list", "new_search"}:
        return "show_first_list"
    if intent == "select_option":
        return "show_selected_details"
    if "render_unclear_clarification" in allowed:
        return "render_unclear_clarification"
    return context.get("recommended_action") or "render_unclear_clarification"


def present(action: str, context: dict[str, Any], state: dict[str, Any]) -> str:
    options = context.get("visible_options") or []
    selected = state.get("selected_option") if isinstance(state.get("selected_option"), dict) else None
    intent = context.get("stage")
    intro_map = {
        "location_list": "По этому району вижу такие варианты:",
        "exact_match_list": "Нашла варианты, которые ближе всего к вашему запросу:",
        "room_budget_list": "По комнатности и бюджету вижу такие варианты:",
        "new_search": "Нашла несколько вариантов по текущим данным:",
    }
    if action == "show_first_list":
        return _format_options_summary_response(options, intro_map.get(str(intent), "Нашла несколько вариантов по текущим данным"), "Какой ЖК хотите рассмотреть подробнее?")
    if action == "show_wide_starting_options":
        return _format_options_summary_response(options, "Можно смотреть широко без бюджета", "Какой ЖК хотите рассмотреть подробнее?")
    if action == "show_selected_details" and selected:
        return _prepare_response_text(_format_option_response(selected, state.get("purpose") or context.get("purpose")))
    if action == "operator_handoff":
        option = selected or (options[0] if options else {})
        if isinstance(option, dict) and option.get("name"):
            return _prepare_response_text(_format_operator_handoff_for_option(option))
        return "Да, можно связаться с оператором. Напишите номер для связи — передам ваш запрос и текущий контекст."
    if action == "explain_comparison_criteria":
        names = ", ".join((opt.get("name") or "").strip() for opt in options if opt.get("name"))
        return (
            f"Сравнивал по тем полям, которые реально есть в данных: цена, локация, отделка и срок."
            f" Сейчас в фокусе: {names or 'текущие варианты'}."
        )
    if action == "render_near_only_response":
        first = options[0] if options else {}
        why_close = str(first.get("why_close") or "есть близкий вариант поблизости").strip()
        return (
            "Прямых совпадений нет, но рядом вижу близкие варианты. "
            f"Например, {first.get('name') or 'один из вариантов'} — {why_close}."
        )
    if action == "render_no_results_area_response":
        return (
            "По этому району сейчас не вижу подтверждённых новостроек в переданных данных. "
            "Могу посмотреть близкие районы или варианты поблизости."
        )
    if action == "render_finance_terms_response":
        return (
            "По ипотеке я не буду придумывать правила банка. "
            "Могу только помочь посмотреть варианты и отдельно проверить, какие условия есть в данных."
        )
    if action == "render_unknown_question_response":
        return (
            "В данных по ЖК я не вижу ответа на этот вопрос. "
            "Могу показать, что известно по самому проекту: локацию, цену, отделку и срок."
        )
    if action == "render_off_topic_boundary":
        return "Я помогаю только с новостройками Москвы и МО. Если хотите, вернёмся к подбору."
    if action == "render_unclear_clarification":
        return "Не совсем понял. Напишите район, бюджет, комнатность или название ЖК."
    return "Симулятор не знает, как красиво показать это действие."


def validator(action: str, text: str, context: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    lowered = text.lower()
    if "{" in text or "}" in text:
        warnings.append("raw_json_leak")
    if action == "operator_handoff" and "оператор" not in lowered:
        warnings.append("handoff_missing_operator_reference")
    if action == "explain_comparison_criteria" and "цена" not in lowered:
        warnings.append("missing_comparison_criteria")
    if action == "render_unknown_question_response" and "не вижу ответа" not in lowered:
        warnings.append("missing_honest_unknown_boundary")
    if action == "render_finance_terms_response" and "придумывать" not in lowered:
        warnings.append("missing_finance_boundary")
    if action == "render_near_only_response" and "рядом" not in lowered:
        warnings.append("missing_near_only_boundary")
    if action == "render_off_topic_boundary" and "новостройк" not in lowered:
        warnings.append("missing_offtopic_boundary")
    if action == "render_no_results_area_response" and "близкие районы" not in lowered:
        warnings.append("missing_no_results_boundary")
    if action == "show_selected_details" and (selected := context.get("state_snapshot", {}).get("selected_option")):
        if isinstance(selected, dict) and selected.get("name") and selected.get("name") not in text:
            warnings.append("selected_option_not_named")
    return {"passed": not warnings, "warnings": warnings}


def run_scenario(name: str, turns: list[str], search_response: dict[str, Any]) -> dict[str, Any]:
    options = _load_options(search_response)
    state: dict[str, Any] = {
        "last_options": options,
        "selected_option": None,
        "last_offer_type": "choose_option",
        "last_answer_kind": "options_summary",
        "purpose": search_response.get("params", {}).get("purpose"),
        "search_summary": _search_summary(search_response),
    }

    print(f"=== SCENARIO: {name} ===")
    print(_format_options_summary_response(options, "Нашла несколько вариантов по текущим данным", "Какой ЖК хотите рассмотреть подробнее?"))
    print()

    turns_out: list[dict[str, Any]] = []
    for turn in turns:
        intent = planner_intent(turn, state)
        if intent == "select_option" and options:
            state["selected_option"] = options[0]
            state["last_offer_type"] = "selected_option"
            state["last_answer_kind"] = "selected_option_card"
        context = build_decision_context(turn, state, intent, search_response, options)
        action = resolve_action(intent, context, state)
        text = present(action, context, state)
        check = validator(action, text, context)

        print(f"USER: {turn}")
        print(f"PLANNER: {intent}")
        print(f"ACTION: {action}")
        print(text)
        print(f"VALIDATOR: {'PASS' if check['passed'] else 'FAIL'}")
        if check["warnings"]:
            print("WARNINGS:", ", ".join(check["warnings"]))
        print()

        turns_out.append(
            {
                "turn": turn,
                "intent": intent,
                "action": action,
                "validator": check,
                "context": context,
                "response": text,
            }
        )

    summary = {
        "scenario": name,
        "turns": len(turns_out),
        "passed": sum(1 for item in turns_out if item["validator"]["passed"]),
        "failed": sum(1 for item in turns_out if not item["validator"]["passed"]),
    }
    print("SUMMARY:", json.dumps(summary, ensure_ascii=False))
    print()
    return {"summary": summary, "turns": turns_out}


def scenarios() -> list[Scenario]:
    return [
        Scenario("first_list", ["двушка в Солнцево"]),
        Scenario("comparison_criteria", ["1", "по каким критериям сравнивал?"]),
        Scenario("operator_handoff", ["1", "позови оператора"]),
        Scenario("unknown_question", ["1", "а почему он так называется?"]),
        Scenario("off_topic", ["1", "что за рубероид в одессе?"]),
        Scenario("wide_search", ["я пока без бюджета посмотрю"]),
        Scenario("near_only", ["что есть рядом?"], NEAR_ONLY_SEARCH_RESPONSE),
        Scenario("finance_terms", ["а по ипотеке что?"], SAMPLE_SEARCH_RESPONSE),
        Scenario("no_results_area", ["что есть рядом?"], EMPTY_SEARCH_RESPONSE),
        Scenario("mixed_dirty", ["позови оператора и что за рубероид в одессе?"], SAMPLE_SEARCH_RESPONSE),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini simulator for the new decision architecture hypothesis")
    parser.add_argument("--scenario", action="append", dest="scenario_names", help="Run only named scenario(s)")
    parser.add_argument("--cases-jsonl", help="Run real questions from a cases.jsonl file")
    parser.add_argument("--case-id", action="append", dest="case_ids", help="Case id to run from cases.jsonl; can be repeated")
    args = parser.parse_args()

    selected = {name for name in (args.scenario_names or [])}
    if args.cases_jsonl:
        all_scenarios = _load_cases_from_jsonl(Path(args.cases_jsonl), set(args.case_ids or []))
    else:
        all_scenarios = scenarios()
        if selected:
            all_scenarios = [scenario for scenario in all_scenarios if scenario.name in selected]

    if not all_scenarios:
        raise SystemExit("No scenarios selected")

    report = [run_scenario(scenario.name, scenario.turns, scenario.search_response or SAMPLE_SEARCH_RESPONSE) for scenario in all_scenarios]
    final = {
        "scenarios": len(report),
        "turns": sum(item["summary"]["turns"] for item in report),
        "passed": sum(item["summary"]["passed"] for item in report),
        "failed": sum(item["summary"]["failed"] for item in report),
    }
    print("FINAL:", json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main()
