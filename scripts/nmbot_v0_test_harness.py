#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nmbot_v0 import V0State, V0TurnProcessor
from nmbot_v0.runtime import OPERATOR_PHONE_QUESTION, SELECTED_OBJECT_PRESENTATION_QUESTION
from nmbot_v2.contracts import OptionCard


JsonDict = dict[str, Any]
ScenarioRunner = Callable[[], JsonDict]
FAMILY_SELECTED_CTA = "Проверить подходящие семейные планировки в этом ЖК?"
RENTAL_SELECTED_CTA = "Проверить доступные квартиры для сдачи именно в этом ЖК?"


def _search_decision() -> JsonDict:
    return {
        "decision": {
            "action": "search",
            "viewpoint": "family",
            "active_topic": "family",
            "params": {"max_price": 10_000_000, "rooms": "2"},
            "client_question": "Подбери двушку для семьи до 10 млн",
        },
        "search": {
            "facts": [
                {"name": "ЖК Первый", "location": "Москва", "min_price": 9_800_000, "finishing": "с отделкой", "ready": "сдан", "school": True, "kindergarten": True},
                {"name": "ЖК Второй", "location": "Новая Москва", "min_price": 10_500_000, "finishing": "white box", "park_near": True},
                {"name": "ЖК Третий", "location": "Москва", "min_price": 12_000_000, "ready": "ready"},
                {"name": "ЖК Четвёртый", "location": "Москва", "min_price": 13_000_000},
            ],
            "near": [],
            "missing": [],
            "params": {"max_price": 10_000_000},
        },
    }


def _successful_answer(brief: JsonDict) -> JsonDict:
    allowed = brief["allowed_cards"]
    options = []
    for card in allowed:
        parts = []
        if card.get("location"):
            parts.append(str(card["location"]))
        if card.get("price_min"):
            parts.append(f"от {int(card['price_min']) // 1_000_000} млн")
        if card.get("finishing"):
            parts.append(str(card["finishing"]))
        if card.get("ready"):
            parts.append(str(card["ready"]))
        options.append({"name": card["name"]})
    return {
        "answer_kind": "search_many",
        "scope": "shortlist",
        "intro": "Нашла три понятных варианта.",
        "options": options,
        "recommendation": "Я бы начала с первого: там есть отделка и понятный бюджет.",
        "missing_note": "",
        "final_question": "Какой вариант хотите разобрать подробнее?",
    }


def run_successful_flow() -> JsonDict:
    calls: list[str] = []

    def scenario_search(context: JsonDict) -> JsonDict:
        calls.append("scenario_search")
        text = str(context.get("user_text") or "").casefold()
        if "перв" in text or "жк первый" in text:
            return {
                "decision": {
                    "action": "selected_object",
                    "viewpoint": "family",
                    "selected_option_name": "ЖК Первый",
                    "client_question": "Расскажи про первый вариант",
                }
            }
        return _search_decision()

    def answer(brief: JsonDict) -> JsonDict:
        calls.append("answer")
        if brief["decision"]["action"] == "selected_object":
            card = brief["allowed_cards"][0]
            return {
                "answer_kind": "selected_object",
                "scope": "one_card",
                "intro": f"По {card['name']} в сохранённой подборке есть подтверждённые факты.",
                "options": [{"name": card["name"]}],
                "recommendation": "",
                "missing_note": "",
                "final_question": FAMILY_SELECTED_CTA,
            }
        return _successful_answer(brief)

    processor = V0TurnProcessor(scenario_search=scenario_search, answer=answer)
    first = processor.process("Подбери двушку для семьи до 10 млн", conversation_ref="fixture-success")
    second = processor.process("Расскажи про первый", state=first.state, conversation_ref="fixture-success")

    checks = [
        first.ok,
        second.ok,
        [card.name for card in first.state.visible_options] == ["ЖК Первый", "ЖК Второй", "ЖК Третий"],
        "рядом: школа, детский сад" in first.message,
        "выше указанного бюджета" in first.message,
        FAMILY_SELECTED_CTA in second.message,
        second.state.selected_option_name == "ЖК Первый",
        calls == ["scenario_search", "answer", "scenario_search", "answer"],
    ]
    return _scenario_output("successful_flow", checks, [first, second], calls=calls)


def run_missing_fact() -> JsonDict:
    def scenario_search(_context: JsonDict) -> JsonDict:
        return {
            "decision": {
                "action": "operator",
                "viewpoint": "life",
                "client_question": "Сколько стоит паркинг?",
                "requested_facts": ["parking_price"],
                "response_policy": "operator_phone_request",
            },
            "search": {"facts": [], "near": [], "missing": ["parking_price"], "params": {}},
        }

    def answer(brief: JsonDict) -> JsonDict:
        return {
            "answer_kind": "operator",
            "scope": "operator_phone",
            "intro": "Стоимость места пока не указана, поэтому цену не назову.",
            "options": [],
            "recommendation": "",
            "missing_note": "Оператор сможет проверить цену паркинга по актуальным данным.",
            "final_question": brief["decision"]["operator_handoff_template"],
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Сколько стоит паркинг?", conversation_ref="fixture-missing")
    checks = [result.ok, result.answer is not None, result.answer.final_question == OPERATOR_PHONE_QUESTION, OPERATOR_PHONE_QUESTION in result.message]
    return _scenario_output("missing_fact", checks, [result])


def run_unknown_card() -> JsonDict:
    initial = V0State(visible_options=(OptionCard(name="ЖК Свой", location="Москва"),), active_topic="life")

    def scenario_search(_context: JsonDict) -> JsonDict:
        return {"decision": {"action": "current_options", "viewpoint": "life"}}

    def answer(_brief: JsonDict) -> JsonDict:
        return {
            "answer_kind": "search_many",
            "scope": "shortlist",
            "intro": "Вот вариант.",
            "options": [{"name": "ЖК Чужой"}],
            "recommendation": "",
            "missing_note": "",
            "final_question": "Разобрать подробнее?",
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("А что по ним?", state=initial, conversation_ref="fixture-unknown-card")
    checks = [
        result.ok is False,
        result.error_code == "invalid_answer_output",
        result.state is initial,
        result.state.visible_options == initial.visible_options,
        OPERATOR_PHONE_QUESTION in result.message,
    ]
    return _scenario_output("unknown_card", checks, [result])


def run_rental_third_typo_accept() -> JsonDict:
    calls: list[str] = []

    def scenario_search(context: JsonDict) -> JsonDict:
        calls.append(str(context.get("user_text") or ""))
        text = str(context.get("user_text") or "").casefold()
        if "трет" in text:
            return {"decision": {"action": "selected_object", "viewpoint": "rental", "active_topic": "rental", "selected_option_name": "ЖК Третий"}, "search": {}}
        if "хчоу" in text:
            return {"decision": {"action": "current_options", "viewpoint": "rental", "followup_outcome": "accept", "confirmed_action": "check_selected_availability", "confirmed_subject": "ЖК Третий"}, "search": {}}
        return {
            "decision": {"action": "search", "viewpoint": "rental", "active_topic": "rental", "params": {"budget": "30 млн"}},
            "search": {"facts": [
                {"name": "ЖК Первый", "location": "Москва", "min_price": 25_000_000},
                {"name": "ЖК Второй", "location": "Москва", "min_price": 27_000_000},
                {"name": "ЖК Третий", "location": "Москва", "min_price": 29_000_000},
            ], "near": [], "missing": [], "params": {"budget": "30 млн"}},
        }

    def answer(brief: JsonDict) -> JsonDict:
        scope = brief["decision"]["expected_scope"]
        return {
            "answer_kind": brief["decision"]["expected_answer_kind"],
            "scope": scope,
            "intro": "runtime replaces this",
            "options": [{"name": card["name"]} for card in brief["allowed_cards"]] if scope in {"shortlist", "one_card"} else [],
            "recommendation": "",
            "missing_note": "",
            "final_question": brief["decision"]["cta_template"],
        }

    processor = V0TurnProcessor(scenario_search=scenario_search, answer=answer)
    first = processor.process("под сдачу что то есть, у меня на руках 30 млн", conversation_ref="fixture-rental-typo")
    second = processor.process("третий", state=first.state, conversation_ref="fixture-rental-typo")
    third = processor.process("хчоу", state=second.state, conversation_ref="fixture-rental-typo")
    checks = [
        first.ok,
        second.ok,
        third.ok,
        RENTAL_SELECTED_CTA in second.message,
        second.state.pending_action == "check_selected_availability",
        third.answer is not None and third.answer.final_question == OPERATOR_PHONE_QUESTION,
        "ЖК «Третий» для последующей сдачи" in third.message,
        "Нашла три" not in third.message,
    ]
    return _scenario_output("rental_third_typo_accept", checks, [first, second, third], calls=calls)


def _names_only_answer(brief: JsonDict) -> JsonDict:
    scope = brief["decision"]["expected_scope"]
    return {
        "answer_kind": brief["decision"]["expected_answer_kind"],
        "scope": scope,
        "intro": "runtime replaces this",
        "options": [{"name": card["name"]} for card in brief["allowed_cards"]] if scope in {"shortlist", "one_card"} else [],
        "recommendation": "",
        "missing_note": "",
        "final_question": brief["decision"]["cta_template"],
    }


def run_ready_near_only() -> JsonDict:
    def scenario_search(_context: JsonDict) -> JsonDict:
        return {
            "decision": {"action": "search", "viewpoint": "life", "active_topic": "life", "params": {"delivered": True}},
            "search": {
                "facts": [{"name": "ЖК Будущий", "location": "Москва", "min_price": 9_000_000, "ready": "2028"}],
                "near": [{"name": "ЖК Альтернатива", "location": "Москва", "min_price": 9_500_000, "ready": "2027", "is_near": True, "why_close": "срок другой"}],
                "missing": [],
                "params": {"delivered": True},
            },
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("Нужен сданный дом", conversation_ref="fixture-ready-near")
    checks = [
        result.ok,
        [card.name for card in result.state.visible_options] == ["ЖК Альтернатива"],
        result.state.visible_options[0].is_near if result.state.visible_options else False,
        "Точного совпадения" in result.message or "Точных совпадений" in result.message,
        "ЖК «Будущий»" not in result.message,
    ]
    return _scenario_output("ready_near_only", checks, [result])


def run_financing_check_all() -> JsonDict:
    calls: list[str] = []

    def scenario_search(context: JsonDict) -> JsonDict:
        calls.append(str(context.get("user_text") or ""))
        if len(calls) == 1:
            return {
                "decision": {"action": "search", "viewpoint": "life", "active_topic": "life", "params": {"financing": True, "down_payment": 0, "max_price": 12_000_000}, "requested_facts": ["mortgage_terms"]},
                "search": {"facts": [{"name": "ЖК Первый", "location": "Москва", "min_price": 10_000_000}, {"name": "ЖК Второй", "location": "Москва", "min_price": 11_000_000}], "near": [], "missing": [], "params": {"max_price": 12_000_000}},
            }
        return {"decision": {"action": "current_options", "viewpoint": "financing", "followup_outcome": "accept", "confirmed_action": "check_current_options_financing", "confirmed_subject": "all_current_options"}, "search": {}}

    processor = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer)
    first = processor.process("Ипотека без первоначального взноса", conversation_ref="fixture-financing-all")
    second = processor.process("проверьте по всем вариантам", state=first.state, conversation_ref="fixture-financing-all")
    checks = [
        first.ok,
        second.ok,
        first.state.pending_action == "check_current_options_financing",
        "без первоначального взноса" in first.message,
        second.answer is not None and second.answer.final_question == OPERATOR_PHONE_QUESTION,
        "по всем текущим вариантам" in second.message,
        "Нашла" not in second.message,
    ]
    return _scenario_output("financing_check_all", checks, [first, second], calls=calls)


def run_named_first_turn_exact() -> JsonDict:
    def scenario_search(_context: JsonDict) -> JsonDict:
        return {
            "decision": {"action": "selected_object", "viewpoint": "life", "active_topic": "life", "selected_option_name": "ЖК Точный", "requested_facts": ["apartment_price", "finishing", "readiness"]},
            "search": {"facts": [{"name": "ЖК Точный", "location": "Москва", "min_price": 8_000_000, "finishing": "с отделкой", "delivered": True}], "near": [], "missing": [], "params": {}},
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("Расскажите про ЖК Точный", conversation_ref="fixture-named-exact")
    checks = [
        result.ok,
        result.state.selected_option_name == "ЖК Точный",
        [card.name for card in result.state.visible_options] == ["ЖК Точный"],
        "цены от 8 000 000 ₽" in result.message,
        "с отделкой" in result.message,
        "дом сдан" in result.message,
    ]
    return _scenario_output("named_first_turn_exact", checks, [result])


def run_current_options_cheapest() -> JsonDict:
    state = V0State(
        visible_options=(OptionCard(name="ЖК Дорогой", location="Москва", price_min=15_000_000), OptionCard(name="ЖК Дешёвый", location="Москва", price_min=9_000_000), OptionCard(name="ЖК Средний", location="Москва", price_min=12_000_000)),
        active_topic="life",
    )

    def scenario_search(_context: JsonDict) -> JsonDict:
        return {"decision": {"action": "current_options", "viewpoint": "life", "comparison_metric": "price_min"}, "search": {}}

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("Какой самый дешёвый?", state=state, conversation_ref="fixture-cheapest")
    checks = [
        result.ok,
        len(result.answer.options) == 1 if result.answer else False,
        result.answer.options[0]["name"] == "ЖК Дешёвый" if result.answer and result.answer.options else False,
        "ЖК «Дешёвый»" in result.message,
        "ЖК «Дорогой»" not in result.message,
        [card.name for card in result.state.visible_options] == ["ЖК Дорогой", "ЖК Дешёвый", "ЖК Средний"],
    ]
    return _scenario_output("current_options_cheapest", checks, [result])


SCENARIOS: dict[str, ScenarioRunner] = {
    "successful_flow": run_successful_flow,
    "missing_fact": run_missing_fact,
    "unknown_card": run_unknown_card,
    "rental_third_typo_accept": run_rental_third_typo_accept,
    "ready_near_only": run_ready_near_only,
    "financing_check_all": run_financing_check_all,
    "named_first_turn_exact": run_named_first_turn_exact,
    "current_options_cheapest": run_current_options_cheapest,
}


def _scenario_output(name: str, checks: list[bool], turns: list[Any], *, calls: list[str] | None = None) -> JsonDict:
    return {
        "scenario": name,
        "ok": all(checks),
        "checks": checks,
        "calls": calls or [],
        "turns": [_turn_to_dict(turn) for turn in turns],
    }


def _turn_to_dict(turn: Any) -> JsonDict:
    return {
        "ok": bool(turn.ok),
        "error_code": turn.error_code,
        "message": turn.message,
        "state": _state_to_dict(turn.state),
        "diagnostics": _jsonable(turn.diagnostics),
    }


def _state_to_dict(state: V0State) -> JsonDict:
    return {
        "params": dict(state.params),
        "visible_options": [_jsonable(card) for card in state.visible_options],
        "selected_option_name": state.selected_option_name,
        "active_topic": state.active_topic,
        "has_greeted": state.has_greeted,
        "last_answer_kind": state.last_answer_kind,
        "last_assistant_question": state.last_assistant_question,
        "previous_assistant_message": state.previous_assistant_message,
        "answered_facts": list(state.answered_facts),
        "pending_action": state.pending_action,
        "pending_subject": state.pending_subject,
        "pending_topic": state.pending_topic,
    }


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items() if item not in (None, "", (), [], {})}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _render_readable(results: list[JsonDict]) -> str:
    lines = ["NMBot V0 local deterministic harness"]
    for result in results:
        icon = "OK" if result["ok"] else "FAIL"
        lines.append(f"\n[{icon}] {result['scenario']}")
        for index, turn in enumerate(result["turns"], start=1):
            status = "ok" if turn["ok"] else f"fallback:{turn['error_code']}"
            lines.append(f"  turn {index}: {status}")
            lines.append("  " + str(turn["message"]).replace("\n", "\n  "))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local deterministic NMBot V0 fixture scenarios.")
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all", help="Fixture scenario to run.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of a readable transcript.")
    args = parser.parse_args(argv)

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    results = [SCENARIOS[name]() for name in names]
    if args.json:
        print(json.dumps({"ok": all(item["ok"] for item in results), "results": results}, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render_readable(results))
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
