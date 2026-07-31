#!/usr/bin/env python3
"""LLM-first simulator for the nmbot scenario contract.

Canonical rubric: docs/LLM_SCENARIO_EVAL_RUBRIC.md

Contract:
    scenario/orchestrator -> MCP query profile -> compact facts card -> chat LLM

This simulator does not try to infer semantics with regex. The scenario is
selected explicitly, which mirrors the intended architecture:

    model/orchestrator chooses the scenario
    code picks the MCP profile
    MCP/search returns a compact card
    chat LLM turns the card into a human answer

Usage:
    python3 scripts/nmbot_scenario_sim.py --scenario family
    python3 scripts/nmbot_scenario_sim.py --all
    python3 scripts/nmbot_scenario_sim.py --scenario investment
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


@dataclass(frozen=True)
class ScenarioProfile:
    name: str
    purpose: str
    query_tool: str
    required_fields: tuple[str, ...]
    answer_goal: str
    scenario_prompt_path: str | None = None


@dataclass
class ScenarioCase:
    name: str
    user_text: str
    profile_name: str
    search_response: dict[str, Any]


PROFILE_REGISTRY: dict[str, ScenarioProfile] = {
    "family": ScenarioProfile(
        name="family",
        purpose="family",
        query_tool="get_flat_info",
        required_fields=(
            "name",
            "location",
            "district",
            "price_range",
            "rooms",
            "area",
            "finishing",
            "ready",
            "metro",
            "developer",
            "link",
            "family_infrastructure",
        ),
        answer_goal="Show 1-2 verified family facts as a live selling reason.",
        scenario_prompt_path="prompts/scenarios/family_v1.txt",
    ),
    "investment": ScenarioProfile(
        name="investment",
        purpose="investment",
        query_tool="get_flat_info",
        required_fields=(
            "name",
            "location",
            "district",
            "price_range",
            "rooms",
            "area",
            "ready",
            "mortgage",
            "egrn_sales",
            "egrn_position",
            "counter_novos",
            "discount",
            "ads",
            "apartment_types",
            "property_metro",
            "metro",
            "link",
        ),
        answer_goal="Explain investment appeal only through verified finance/sales facts and compare 2-3 options when possible.",
        scenario_prompt_path="prompts/scenarios/investment_v1.txt",
    ),
    "rental": ScenarioProfile(
        name="rental",
        purpose="rental",
        query_tool="get_flat_info",
        required_fields=(
            "name",
            "location",
            "district",
            "price_range",
            "rooms",
            "area",
            "ready",
            "finishing",
            "egrn_sales",
            "counter_novos",
            "ads",
            "apartment_types",
            "metro",
            "link",
        ),
        answer_goal="Explain why the object is convenient to rent out using only verified compactness, readiness, finishing, transport and demand signals.",
        scenario_prompt_path="prompts/scenarios/rental_v1.txt",
    ),
    "mortgage": ScenarioProfile(
        name="mortgage",
        purpose="fact_check",
        query_tool="get_flat_info",
        required_fields=(
            "selected_option_name",
            "price_range",
            "area",
            "mortgage",
            "mortgage_calc",
            "discount",
            "payment_by_installments",
            "link",
        ),
        answer_goal="Answer family-mortgage questions with verified MCP finance facts and keep approval/exact-live-rate boundaries.",
        scenario_prompt_path="prompts/facets/mortgage_v1.txt",
    ),
    "installment": ScenarioProfile(
        name="installment",
        purpose="fact_check",
        query_tool="get_flat_info",
        required_fields=(
            "selected_option_name",
            "price_range",
            "area",
            "payment_by_installments",
            "discount",
            "mortgage_calc",
            "link",
        ),
        answer_goal="Explain verified installment/discount/payment facts without async promises or unsupported bank claims.",
        scenario_prompt_path="prompts/facets/mortgage_v1.txt",
    ),
    "search": ScenarioProfile(
        name="search",
        purpose="own_living",
        query_tool="get_flat_info",
        required_fields=(
            "name",
            "location",
            "district",
            "price_range",
            "rooms",
            "area",
            "finishing",
            "ready",
            "metro",
            "developer",
            "link",
        ),
        answer_goal="Present a clean shortlist with one useful reason per ЖК.",
        scenario_prompt_path="prompts/scenarios/search_v1.txt",
    ),
    "repeat_search": ScenarioProfile(
        name="repeat_search",
        purpose="repeat_search",
        query_tool="get_flat_info",
        required_fields=(
            "name",
            "location",
            "district",
            "price_range",
            "rooms",
            "area",
            "finishing",
            "ready",
            "metro",
            "developer",
            "link",
        ),
        answer_goal="Show fresh options that do not repeat the previous shortlist and explain how they differ.",
        scenario_prompt_path="prompts/scenarios/repeat_search_v1.txt",
    ),
    "explain_selection": ScenarioProfile(
        name="explain_selection",
        purpose="explain_selection",
        query_tool="get_flat_info_if_needed",
        required_fields=(
            "visible_options",
            "selection_reasons",
            "price_range",
            "location",
            "metro",
            "finishing",
            "ready",
            "scenario_facts",
        ),
        answer_goal="Explain why current options were selected using only facts already in the card, or request enrich if reasons are thin.",
        scenario_prompt_path="prompts/scenarios/explain_selection_v1.txt",
    ),
    "fact_check": ScenarioProfile(
        name="fact_check",
        purpose="fact_check",
        query_tool="get_flat_info",
        required_fields=(
            "selected_option_name",
            "checked_field",
            "evidence",
            "missing",
            "link",
        ),
        answer_goal="Confirm or deny a specific claim from MCP/card evidence; never infer unsupported facts.",
        scenario_prompt_path="prompts/scenarios/selected_details_v1.txt",
    ),
    "selected_details": ScenarioProfile(
        name="selected_details",
        purpose="fact_check",
        query_tool="get_flat_info",
        required_fields=(
            "selected_option_name",
            "price_range",
            "area_range",
            "metro",
            "ready",
            "finishing",
            "developer",
            "infrastructure",
            "houses_info",
            "link",
        ),
        answer_goal="Explain a selected ЖК dossier from verified MCP details; do not treat it as a yes/no fact check.",
        scenario_prompt_path="prompts/scenarios/fact_check_v1.txt",
    ),
    "refine_search": ScenarioProfile(
        name="refine_search",
        purpose="refine_search",
        query_tool="get_flat_info",
        required_fields=(
            "merged_params",
            "count",
            "exclude",
            "facts",
            "near",
            "missing_fields",
        ),
        answer_goal="Update search conditions, preserve useful previous context, and separate MCP-checkable filters from gaps.",
        scenario_prompt_path="prompts/scenarios/refine_search_v1.txt",
    ),
    "default": ScenarioProfile(
        name="default",
        purpose="default",
        query_tool="none",
        required_fields=(),
        answer_goal="Ask one short clarifying question and do not search yet.",
        scenario_prompt_path="prompts/scenarios/default_v1.txt",
    ),
    "operator": ScenarioProfile(
        name="operator",
        purpose="operator",
        query_tool="get_flat_info",
        required_fields=("name", "location", "district", "price_range", "rooms", "area", "link"),
        answer_goal="Hand off with current ЖК context, without inventing live details.",
        scenario_prompt_path="prompts/scenarios/operator_v1.txt",
    ),
    "off_topic": ScenarioProfile(
        name="off_topic",
        purpose="off_topic",
        query_tool="none",
        required_fields=(),
        answer_goal="Set a polite boundary and return to novostroym only.",
        scenario_prompt_path="prompts/scenarios/off_topic_v1.txt",
    ),
}

DAILY_REQUEST_BUDGET_USD = 4.00


def _load_openrouter_cost() -> dict[str, float] | None:
    script = REPO / "scripts" / "or_cost.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None

    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    matches = {
        "today": re.search(r"За сегодня:\s*\$\s*([0-9]+(?:\.[0-9]+)?)", text),
        "week": re.search(r"За неделю:\s*\$\s*([0-9]+(?:\.[0-9]+)?)", text),
        "month": re.search(r"За месяц:\s*\$\s*([0-9]+(?:\.[0-9]+)?)", text),
        "total": re.search(r"Всего:\s*\$\s*([0-9]+(?:\.[0-9]+)?)", text),
    }
    if not any(match for match in matches.values()):
        return None
    snapshot: dict[str, float] = {}
    for key, match in matches.items():
        if match:
            snapshot[key] = float(match.group(1))
    return snapshot


def _family_card() -> dict[str, Any]:
    return {
        "facts": [
            {
                "name": "ЖК «Лучи»",
                "location": "Солнцево",
                "district": "msk",
                "price_range": "от 10 591 869 до 31 582 642 руб.",
                "rooms": "2",
                "area": "от 22.5 до 86.5 м²",
                "finishing": "с отделкой",
                "ready": "2027 г., 2 квартал",
                "metro": "Солнцево",
                "developer": "информация уточняется",
                "link": "jk_luchi",
                "family_infrastructure": {
                    "school": True,
                    "kindergarten": True,
                    "park_near": True,
                    "yard_without_cars": True,
                    "children_ground": True,
                },
                "schools": "2 школы",
                "kindergartens": "4 детских сада",
                "parks": "Мещерский парк и Чоботовский лес",
                "yard_without_cars": True,
            },
            {
                "name": "Бусиновский парк",
                "location": "Западное Дегунино",
                "district": "msk",
                "price_range": "от 12 103 290 до 36 645 507 руб.",
                "rooms": "2",
                "area": "от 20 до 89.3 м²",
                "finishing": "с отделкой",
                "ready": "2027 г., 2 квартал",
                "metro": "информация уточняется",
                "developer": "информация уточняется",
                "link": "jiloy_kompleks_businovskiy_park",
                "family_infrastructure": {
                    "school": True,
                    "kindergarten": True,
                    "park_near": True,
                    "yard_without_cars": True,
                    "children_ground": True,
                },
                "schools": "рядом есть школы",
                "kindergartens": "детские сады поблизости",
                "parks": "зелёные зоны рядом",
                "yard_without_cars": True,
            },
            {
                "name": "ЖК «Южные Сады»",
                "location": "Южное Бутово",
                "district": "msk",
                "price_range": "от 11 399 922 до 37 921 655 руб.",
                "rooms": "2",
                "area": "от 21.8 до 187.8 м²",
                "finishing": "с отделкой",
                "ready": "2027 г., 2 квартал",
                "metro": "информация уточняется",
                "developer": "информация уточняется",
                "link": "jk_yujnye_sady",
                "family_infrastructure": {
                    "school": True,
                    "kindergarten": True,
                    "park_near": True,
                    "yard_without_cars": True,
                    "children_ground": True,
                },
                "schools": "рядом есть школы",
                "kindergartens": "детские сады поблизости",
                "parks": "зелёные зоны и парк рядом",
                "yard_without_cars": True,
            },
        ],
        "near": [],
        "missing": "Семейные факты отданы только в подтверждённом виде.",
        "params": {"rooms": "2", "district": "msk", "purpose": "family"},
    }


def _investment_card() -> dict[str, Any]:
    return {
        "facts": [
            {
                "name": "ЖК «Русич Кантемировский»",
                "location": "Царицыно",
                "district": "msk",
                "price_range": "от 10 822 140 до 25 533 402 руб.",
                "rooms": "s",
                "area": "информация уточняется",
                "finishing": "без отделки",
                "ready": "2026 г., 3 квартал",
                "metro": "информация уточняется",
                "developer": "информация уточняется",
                "link": "jk_kavkazskiy_bulvar_512",
                "mortgage": {"min_percent": 8.1, "min_fee": 20},
                "egrn_sales": 142,
                "egrn_position": 9,
            }
            ,
            {
                "name": "ЖК «Лучи»",
                "location": "Солнцево",
                "district": "msk",
                "price_range": "от 10 591 869 до 31 582 642 руб.",
                "rooms": "1",
                "area": "от 22.5 до 38.7 м²",
                "finishing": "с отделкой",
                "ready": "2027 г., 2 квартал",
                "metro": "Солнцево",
                "developer": "информация уточняется",
                "link": "jk_luchi",
                "mortgage": {"min_percent": 7.9, "min_fee": 15},
                "egrn_sales": 98,
                "egrn_position": 11,
                "counter_novos": {"count_ads": 17, "count_discounts": 4},
            },
            {
                "name": "ЖК «Южные Сады»",
                "location": "Южное Бутово",
                "district": "msk",
                "price_range": "от 11 399 922 до 37 921 655 руб.",
                "rooms": "e2",
                "area": "от 32.1 до 61.4 м²",
                "finishing": "с отделкой",
                "ready": "2027 г., 2 квартал",
                "metro": "информация уточняется",
                "developer": "информация уточняется",
                "link": "jk_yujnye_sady",
                "mortgage": {"min_percent": 8.3, "min_fee": 20},
                "egrn_sales": 61,
                "egrn_position": 12,
                "counter_novos": {"count_ads": 11, "count_discounts": 2},
            },
        ],
        "near": [
            {
                "name": "ЖК «Лучи» — студия",
                "rooms": "s",
                "fullprice": "10 591 869 руб.",
                "area": "22.5 м²",
                "renovation": "с отделкой",
            },
            {
                "name": "ЖК «Южные Сады» — евро-двушка",
                "rooms": "e2",
                "fullprice": "14 820 000 руб.",
                "area": "39.8 м²",
                "renovation": "с отделкой",
            },
        ],
        "missing": "Инвестиционные выводы строятся только на фактах карточки.",
        "params": {"rooms": "s", "district": "msk", "purpose": "investment"},
    }


def _rental_card() -> dict[str, Any]:
    return {
        "facts": [
            {
                "name": "ЖК «Лучи»",
                "location": "Солнцево",
                "district": "msk",
                "price_range": "от 10 591 869 до 31 582 642 руб.",
                "rooms": "s",
                "area": "от 22.5 до 38.7 м²",
                "finishing": "с отделкой",
                "ready": "2027 г., 2 квартал",
                "metro": "Солнцево",
                "developer": "информация уточняется",
                "link": "jk_luchi",
                "egrn_sales": 98,
                "egrn_position": 11,
                "counter_novos": {"count_ads": 17, "count_discounts": 4},
            },
            {
                "name": "ЖК «Русич Кантемировский»",
                "location": "Царицыно",
                "district": "msk",
                "price_range": "от 10 822 140 до 25 533 402 руб.",
                "rooms": "s",
                "area": "информация уточняется",
                "finishing": "без отделки",
                "ready": "2026 г., 3 квартал",
                "metro": "информация уточняется",
                "developer": "информация уточняется",
                "link": "jk_kavkazskiy_bulvar_512",
                "egrn_sales": 142,
                "egrn_position": 9,
            },
            {
                "name": "ЖК «Южные Сады»",
                "location": "Южное Бутово",
                "district": "msk",
                "price_range": "от 11 399 922 до 37 921 655 руб.",
                "rooms": "e2",
                "area": "от 32.1 до 61.4 м²",
                "finishing": "с отделкой",
                "ready": "2027 г., 2 квартал",
                "metro": "информация уточняется",
                "developer": "информация уточняется",
                "link": "jk_yujnye_sady",
                "egrn_sales": 61,
                "egrn_position": 12,
                "counter_novos": {"count_ads": 11, "count_discounts": 2},
            },
        ],
        "near": [
            {
                "name": "ЖК «Лучи» — студия",
                "rooms": "s",
                "fullprice": "10 591 869 руб.",
                "area": "22.5 м²",
                "renovation": "с отделкой",
            },
            {
                "name": "ЖК «Южные Сады» — евро-двушка",
                "rooms": "e2",
                "fullprice": "14 820 000 руб.",
                "area": "39.8 м²",
                "renovation": "с отделкой",
            },
        ],
        "missing": "Арендный вывод строится только на подтверждённых фактах карточки.",
        "params": {"rooms": "s", "district": "msk", "purpose": "rental"},
    }


def _mortgage_card() -> dict[str, Any]:
    return {
        "facts": [
            {
                "name": "Мичуринский парк",
                "location": "Очаково-Матвеевское",
                "district": "msk",
                "price_range": "от 14 309 257 до 37 607 220 руб.",
                "area": "от 20.1 до 80.9 м²",
                "finishing": "с отделкой",
                "ready": "2028 г., 3 квартал",
                "metro": "Озёрная, 7 минут",
                "developer": "ПИК",
                "link": "michurinskiy_park",
                "mortgage_calc": [
                    {"bank": "СберБанк", "program": "Семейная ипотека 6%", "min_percent": 6, "min_fee": 50.1, "term_months": 360},
                    {"bank": "ВТБ", "program": "Семейная ипотека 6%", "min_percent": 6, "min_fee": 20.1, "term_months": 360},
                ],
                "mortgage": "есть семейная ипотека по данным карточки",
                "discount": "есть скидки/выгоды от застройщика, размер нужно проверять по квартире",
                "payment_by_installments": "есть рассрочка от застройщика",
            }
        ],
        "near": [],
        "missing": "Одобрение ипотеки, точные live-условия и конкретную квартиру проверяет менеджер/банк.",
        "params": {"purpose": "fact_check", "selected_option_name": "Мичуринский парк", "facets": ["mortgage"], "mortgage_type": "family_mortgage"},
    }


def _installment_card() -> dict[str, Any]:
    card = copy.deepcopy(_mortgage_card())
    fact = card["facts"][0]
    fact["payment_by_installments"] = "рассрочка от ПИК на 18 месяцев"
    fact["discount"] = "выгода до 4,4 млн руб. и скидка до 3,2 млн руб. по акциям"
    card["missing"] = "Точную акцию, график платежей и доступность по квартире нужно проверять перед бронью."
    card["params"] = {"purpose": "fact_check", "selected_option_name": "Мичуринский парк", "facets": ["installment", "discount"]}
    return card


def _search_card() -> dict[str, Any]:
    return {
        "facts": [
            {
                "name": "ЖК «Южные Сады»",
                "location": "Южное Бутово",
                "district": "msk",
                "price_range": "от 11 399 922 до 37 921 655 руб.",
                "rooms": "2",
                "area": "от 21.8 до 187.8 м²",
                "finishing": "с отделкой",
                "ready": "2027 г., 2 квартал",
                "metro": "информация уточняется",
                "developer": "информация уточняется",
                "link": "jk_yujnye_sady",
            },
            {
                "name": "ЖК «Лучи»",
                "location": "Солнцево",
                "district": "msk",
                "price_range": "от 10 591 869 до 31 582 642 руб.",
                "rooms": "2",
                "area": "от 22.5 до 86.5 м²",
                "finishing": "с отделкой",
                "ready": "2027 г., 2 квартал",
                "metro": "Солнцево",
                "developer": "информация уточняется",
                "link": "jk_luchi",
            },
        ],
        "near": [],
        "missing": "Обычный search-profile без семейного акцента.",
        "params": {"rooms": "2", "district": "msk", "purpose": "own_living"},
    }


def _repeat_search_card() -> dict[str, Any]:
    card = _search_card()
    card["params"] = {"rooms": "2", "district": "msk", "purpose": "repeat_search", "exclude_previous": ["ЖК «Южные Сады»"]}
    card["missing"] = "Пользователь просит другие варианты, поэтому shortlist должен быть свежим."
    return card


def _default_card() -> dict[str, Any]:
    return {"facts": [], "near": [], "missing": "Сценарий не распознан, нужен один уточняющий вопрос.", "params": {"purpose": "default"}}


def _explain_selection_card() -> dict[str, Any]:
    card = copy.deepcopy(_family_card())
    card["params"] = {"purpose": "explain_selection", "source_scenario": "family"}
    card["question"] = "почему эти квартиры подходят для детей и собаки с кошкой?"
    return card


def _fact_check_card() -> dict[str, Any]:
    return {
        "facts": [
            {
                "name": "ЖК «Лучи»",
                "location": "Солнцево",
                "checked_field": "windows_two_sides",
                "evidence": None,
                "missing": "В данных нет подтверждения, что окна выходят на две стороны света.",
                "link": "jk_luchi",
            }
        ],
        "near": [],
        "missing": "Проверяемое поле отсутствует в данных.",
        "params": {"purpose": "fact_check", "checked_field": "windows_two_sides"},
    }


def _selected_details_card() -> dict[str, Any]:
    return {
        "facts": [
            {
                "name": "Мичуринский парк",
                "location": "Очаково-Матвеевское",
                "price_range": "от 14,3 млн до 37,6 млн руб.",
                "area_range": "от 20,1 до 80,9 м²",
                "metro": "Озёрная, 7 минут пешком",
                "ready": "3 квартал 2028 г. (активные корпуса)",
                "finishing": "с отделкой",
                "developer": "ПИК",
                "infrastructure": {
                    "schools": True,
                    "kindergartens": True,
                    "parks": ["Очаковский парк", "парк Школьников", "парк Олимпийской деревни"],
                    "shops": True,
                    "yard_without_cars": True,
                    "children_ground": True,
                    "sports_ground": True,
                },
                "houses_info": "В проекте есть сданные корпуса 2022-2023 гг. и строящиеся корпуса со сдачей до 2028 г.",
                "link": "https://www.pik.ru/mpark",
            }
        ],
        "near": [],
        "missing": [],
        "params": {
            "purpose": "fact_check",
            "selected_option_name": "Мичуринский парк",
            "fact_to_check": "details",
            "need": ["prices", "area", "property_metro", "schools", "kindergartens", "parks", "shops", "stage", "ready_quarter", "house"],
        },
    }


def _refine_search_card() -> dict[str, Any]:
    card = copy.deepcopy(_search_card())
    card["params"] = {
        "purpose": "refine_search",
        "rooms": "1",
        "max_price": "12000000",
        "metro_distance_km": 2,
        "count": 3,
        "missing_fields": ["redevelopment_to_two_room", "house_floors_max"],
    }
    card["missing"] = "Перепланировку в двушку и этажность дома нужно проверять по конкретным планировкам, если в данных нет этих полей."
    return card


def _operator_card() -> dict[str, Any]:
    return {
        "facts": [
            {
                "name": "ЖК «Лучи»",
                "location": "Солнцево",
                "district": "msk",
                "price_range": "от 10 591 869 до 31 582 642 руб.",
                "rooms": "2",
                "area": "от 22.5 до 86.5 м²",
                "ready": "2027 г., 2 квартал",
                "link": "jk_luchi",
            }
        ],
        "near": [],
        "missing": "Для live-деталей нужен операторский контур.",
        "params": {"purpose": "operator"},
    }


def _off_topic_card() -> dict[str, Any]:
    return {"facts": [], "near": [], "missing": "Вне тематики недвижимости.", "params": {"purpose": "off_topic"}}


CARD_FACTORIES = {
    "family": _family_card,
    "investment": _investment_card,
    "rental": _rental_card,
    "mortgage": _mortgage_card,
    "installment": _installment_card,
    "search": _search_card,
    "repeat_search": _repeat_search_card,
    "explain_selection": _explain_selection_card,
    "fact_check": _fact_check_card,
    "selected_details": _selected_details_card,
    "refine_search": _refine_search_card,
    "default": _default_card,
    "operator": _operator_card,
    "off_topic": _off_topic_card,
}


def _build_query_profile(profile: ScenarioProfile, user_text: str) -> dict[str, Any]:
    return {
        "tool": profile.query_tool,
        "profile": profile.name,
        "purpose": profile.purpose,
        "user_text": user_text,
        "required_fields": list(profile.required_fields),
        "answer_goal": profile.answer_goal,
    }


def _pick_question(profile: ScenarioProfile) -> str:
    if profile.name == "family":
        return "Какой вариант хочется посмотреть подробнее?"
    if profile.name == "investment":
        return "Хотите посмотреть ещё один вариант для сравнения?"
    if profile.name == "rental":
        return "Какой вариант под сдачу хотите посмотреть подробнее?"
    if profile.name == "mortgage":
        return "Хотите, передам оператору этот ЖК и вопрос по семейной ипотеке?"
    if profile.name == "installment":
        return "Хотите, передам оператору этот ЖК и вопрос по рассрочке и скидке?"
    if profile.name == "repeat_search":
        return "Какой из новых вариантов хотите рассмотреть подробнее?"
    if profile.name == "explain_selection":
        return "Хотите, я сравню эти варианты по бюджету, транспорту или семейной инфраструктуре?"
    if profile.name == "fact_check":
        return "Какой ЖК или параметр проверяем следующим?"
    if profile.name == "selected_details":
        return "Что из этого хотите обсудить подробнее?"
    if profile.name == "refine_search":
        return "Показать варианты по этим условиям или сначала ослабить один фильтр?"
    if profile.name == "default":
        return "Для жизни, под инвестицию или под сдачу?"
    if profile.name == "operator":
        return "Хотите, чтобы оператор проверил актуальные детали по этому ЖК?"
    if profile.name == "off_topic":
        return "Если хотите, могу вернуться к подбору новостройки в Москве или МО."
    return "Какой ЖК хотите рассмотреть подробнее?"


def _investment_accent(text: str) -> str:
    lowered = text.lower()
    if _contains_any(lowered, ("вход", "старт", "порог", "цен")):
        return "entry"
    if _contains_any(lowered, ("ипотек", "взнос", "ставк")):
        return "mortgage"
    if _contains_any(lowered, ("егрн", "сделк", "позици", "спрос")):
        return "demand"
    if _contains_any(lowered, ("готов", "срок", "квартал", "сдач")):
        return "ready"
    if _contains_any(lowered, ("локац", "метро", "район", "перепрод")):
        return "location"
    if _contains_any(lowered, ("скид", "акци", "услов")):
        return "discount"
    return "generic"


def _family_response(card: dict[str, Any]) -> str:
    facts = card.get("facts") or []
    top = facts[:3]
    lines = [
        "Для семьи я бы сравнивала варианты не только по цене, а по тому, чем каждый закрывает повседневную жизнь с ребёнком: школа, сад, прогулки, двор и переезд."
    ]

    for idx, fact in enumerate(top, start=1):
        benefit_parts: list[str] = []
        schools = str(fact.get("schools") or "").strip()
        kindergartens = str(fact.get("kindergartens") or "").strip()
        parks = str(fact.get("parks") or "").strip()
        if schools:
            benefit_parts.append(schools)
        if kindergartens and kindergartens not in benefit_parts:
            benefit_parts.append(kindergartens)
        if parks and parks not in benefit_parts:
            benefit_parts.append(parks)
        if fact.get("yard_without_cars"):
            benefit_parts.append("двор без машин")

        name = fact.get("name") or "ЖК"
        location = fact.get("location") or "локация уточняется"
        price = str(fact.get("price_range") or "цену лучше уточнить").replace("от ", "")
        finishing = str(fact.get("finishing") or "отделку лучше уточнить")
        ready = str(fact.get("ready") or "срок готовности лучше уточнить")
        first_sentence = f"{idx}. {name} в районе {location} — вариант с бюджетом {price}; {finishing}, срок {ready}."

        clean_parks = parks.replace("рядом ", "").strip()
        if idx == 1 and schools and kindergartens:
            second_sentence = f"Его главный плюс — понятная учебная логистика: {schools} и {kindergartens}, поэтому ежедневные маршруты с ребёнком будут проще."
            third_sentence = f"А {clean_parks} добавляют сильный прогулочный сценарий после школы или сада." if clean_parks else "Отделка поможет быстрее переехать без длинного ремонта."
        elif idx == 2 and clean_parks:
            second_sentence = f"Этот вариант я бы выделила за зелёное окружение: {clean_parks}, то есть у семьи будет место для прогулок рядом с домом."
            third_sentence = "Двор без машин добавляет спокойствия, если ребёнок гуляет во дворе."
        elif idx == 3:
            second_sentence = "Здесь акцент скорее на спокойном семейном сценарии: школы и сады рядом, плюс есть зелёные зоны для прогулок."
            third_sentence = "С отделкой такой вариант проще рассматривать для переезда без затяжного ремонта."
        elif schools and kindergartens:
            second_sentence = f"Семье удобно, что рядом есть {schools} и {kindergartens}: ежедневная логистика будет проще."
            third_sentence = "Отделка поможет быстрее переехать без длинного ремонта."
        else:
            second_sentence = "По семейным деталям данных пока мало, поэтому инфраструктуру лучше отдельно уточнить перед решением."
            third_sentence = "Этот вариант стоит сравнить с другими по срокам и окружению."

        line = f"{first_sentence} {second_sentence} {third_sentence}"
        lines.append(line.rstrip(".") + ".")

    lines.append(_pick_question(PROFILE_REGISTRY["family"]))
    return "\n\n".join(lines)


def _investment_response(card: dict[str, Any]) -> str:
    facts = card.get("facts") or []
    top = facts[:3]
    parts = [
        "Для инвестиций я бы сравнивала варианты по входу, спросу и тому, насколько удобно потом держать их в портфеле.",
    ]

    for idx, fact in enumerate(top, start=1):
        name = fact.get("name") or "ЖК"
        location = fact.get("location") or "локация уточняется"
        price = str(fact.get("price_range") or "цену лучше уточнить").replace("от ", "")
        ready = str(fact.get("ready") or "срок готовности лучше уточнить")
        metro = str(fact.get("metro") or "")
        mortgage = fact.get("mortgage") or {}
        counter_novos = fact.get("counter_novos") or {}
        egrn_sales = fact.get("egrn_sales")
        egrn_position = fact.get("egrn_position")

        if idx == 1:
            lead = f"главный плюс — понятный вход по цене {price}" if price else "главный плюс — понятный вход"
            compare = "Если сравнивать с другими вариантами, он сильнее именно по входу и спросу."
            details: list[str] = []
            if mortgage:
                details.append(f"первый взнос {mortgage.get('min_fee')}% и ставка от {mortgage.get('min_percent')}%")
            if egrn_sales is not None:
                details.append(f"по ЕГРН {egrn_sales} сделок и позиция {egrn_position}")
        elif idx == 2:
            lead = f"главный плюс — готовность уже близко: {ready}"
            compare = "Если сравнивать с другими вариантами, он сильнее именно по готовности и живой витрине."
            details = []
            if counter_novos:
                details.append(f"на витрине {counter_novos.get('count_ads')} объявлений и {counter_novos.get('count_discounts')} акций")
            if metro and metro != "информация уточняется":
                details.append(f"метро {metro}")
        else:
            lead = "главный плюс — более спокойный горизонт и удобный формат для портфеля"
            compare = "Если сравнивать с другими вариантами, он сильнее именно по сроку, сделке и цене."
            details = []
            if mortgage:
                details.append(f"ставка от {mortgage.get('min_percent')}% и первый взнос {mortgage.get('min_fee')}%")
            if egrn_sales is not None:
                details.append(f"по ЕГРН {egrn_sales} сделок")
            if counter_novos:
                details.append(f"{counter_novos.get('count_ads')} объявлений и {counter_novos.get('count_discounts')} акций")

        details_text = "; ".join([item for item in details if item]) or "данных для более сильного инвестиционного вывода мало"
        line = f"{idx}. {name} в {location} — {lead}. {compare} Для инвестиций это значит: {details_text}."
        if price:
            line += f" Смотрится как вход с бюджетом {price}."
        parts.append(line.rstrip(".") + ".")

    parts.append(_pick_question(PROFILE_REGISTRY["investment"]))
    return "\n\n".join(parts)


def _rental_response(card: dict[str, Any]) -> str:
    facts = card.get("facts") or []
    top = facts[:3]
    parts = [
        "Если смотреть под сдачу в аренду, я бы выбирала варианты, которые легко показать и легко объяснить будущему арендатору: компактный формат, отделка, метро и быстрый запуск.",
    ]

    for idx, fact in enumerate(top, start=1):
        name = fact.get("name") or "ЖК"
        location = fact.get("location") or "локация уточняется"
        ready = str(fact.get("ready") or "срок готовности лучше уточнить")
        finishing = str(fact.get("finishing") or "отделку лучше уточнить")
        metro = str(fact.get("metro") or "").strip()

        if idx == 1:
            line = (
                f"{idx}. {name} в {location} — самый удобный старт под сдачу: компактный формат, отделка и метро уже на месте. "
                f"Его легко представить арендатору, который хочет заехать без лишних хлопот."
            )
        elif idx == 2:
            line = (
                f"{idx}. {name} в {location} — сильный вариант для быстрого запуска: готовность ближе, вход спокойнее, формат понятный. "
                f"Это вариант для тех, кто не хочет долго ждать и хочет быстрее выйти на рынок."
            )
        else:
            line = (
                f"{idx}. {name} в {location} — более спокойный и широкий формат под арендатора, которому важнее комфорт, чем минимальный метраж. "
                f"Здесь акцент на отделку и более размеренный сценарий проживания."
            )
        parts.append(line.rstrip(".") + ".")

    parts.append(_pick_question(PROFILE_REGISTRY["rental"]))
    return "\n\n".join(parts)


def _mortgage_response(card: dict[str, Any]) -> str:
    fact = (card.get("facts") or [{}])[0]
    calc = fact.get("mortgage_calc") or []
    programs: list[str] = []
    for item in calc[:2]:
        bank = item.get("bank") or "банк"
        program = item.get("program") or "ипотечная программа"
        min_percent = item.get("min_percent")
        min_fee = item.get("min_fee")
        term = item.get("term_months")
        details = f"{bank}: {program}"
        if min_percent is not None:
            details += f", ставка от {min_percent}%"
        if min_fee is not None:
            details += f", первый взнос от {min_fee}%"
        if term:
            details += f", срок до {term} месяцев"
        programs.append(details)

    name = fact.get("name") or "этому ЖК"
    display_name = f"ЖК «{name}»" if "ЖК" not in str(name) else str(name)
    price = str(fact.get("price_range") or "цену нужно уточнить").replace("от ", "")
    area = fact.get("area") or "площади нужно уточнить"
    lead = f"Да, по {display_name} вижу семейную ипотеку: " + "; ".join(programs) + "." if programs else f"По {display_name} ипотечные условия нужно отдельно проверить."
    boundary = card.get("missing") or "Одобрение и точные условия проверяются отдельно."
    return (
        f"{lead}\n\n"
        f"По самому ЖК ориентир такой: бюджет {price}, площади {area}. Это можно обсуждать как ипотечный вариант, но я не обещаю одобрение и не фиксирую live-ставку без проверки банка. {boundary}\n\n"
        f"{_pick_question(PROFILE_REGISTRY['mortgage'])}"
    )


def _installment_response(card: dict[str, Any]) -> str:
    fact = (card.get("facts") or [{}])[0]
    name = fact.get("name") or "этому ЖК"
    display_name = f"ЖК «{name}»" if "ЖК" not in str(name) else str(name)
    installment = fact.get("payment_by_installments") or "рассрочку нужно уточнить"
    discount = fact.get("discount") or "скидку нужно уточнить"
    price = str(fact.get("price_range") or "цену нужно уточнить").replace("от ", "")
    boundary = card.get("missing") or "Точные условия нужно проверить по конкретной квартире."
    return (
        f"По {display_name} сейчас вижу такие условия оплаты: {installment}. Также есть {discount}.\n\n"
        f"По цене ориентир такой: {price}. При этом точную акцию, бронь и график платежей лучше проверять по конкретной квартире. {boundary}\n\n"
        f"{_pick_question(PROFILE_REGISTRY['installment'])}"
    )


def _search_response(card: dict[str, Any]) -> str:
    facts = card.get("facts") or []
    top = facts[:3]
    lines = ["Для жизни я бы смотрела на варианты, где проще переехать и удобно жить каждый день."]

    for idx, fact in enumerate(top, start=1):
        name = fact.get("name") or "ЖК"
        location = fact.get("location") or "локация уточняется"
        price = str(fact.get("price_range") or "цену лучше уточнить").replace("от ", "")
        finishing = str(fact.get("finishing") or "отделку лучше уточнить")
        ready = str(fact.get("ready") or "срок готовности лучше уточнить")
        metro = str(fact.get("metro") or "").strip()
        area = str(fact.get("area") or "").strip()

        current_price = _extract_first_number(price)
        current_area = _extract_max_number(area)
        others = [other for other in top if other is not fact]
        other_prices = [value for value in (_extract_first_number(str(other.get("price_range") or "")) for other in others) if value is not None]
        other_areas = [value for value in (_extract_max_number(str(other.get("area") or "")) for other in others) if value is not None]
        has_metro = bool(metro and metro != "информация уточняется")
        other_metros = [str(other.get("metro") or "").strip() for other in others if str(other.get("metro") or "").strip() and str(other.get("metro") or "").strip() != "информация уточняется"]

        if current_price is not None and other_prices and current_price <= min(other_prices):
            compare_text = "он сильнее по входу"
        elif has_metro and not other_metros:
            compare_text = "он сильнее по транспорту"
        elif current_area is not None and other_areas and current_area >= max(other_areas):
            compare_text = "он сильнее по площади"
        elif idx == 1 and has_metro:
            compare_text = "он сильнее по транспорту"
        else:
            compare_text = "он сильнее по своему сценарию переезда"

        line = (
            f"{idx}. {name} в {location} — спокойный старт: {price}, {finishing}, срок {ready}. "
            f"На фоне других {compare_text} — это проще для жизни."
        )
        lines.append(line)

    lines.append(_pick_question(PROFILE_REGISTRY["search"]))
    return "\n\n".join(lines)


def _repeat_search_response(card: dict[str, Any]) -> str:
    facts = card.get("facts") or []
    top = facts[:3]
    lines = ["Покажу другие варианты, чтобы не повторять прошлый shortlist."]

    for idx, fact in enumerate(top, start=1):
        name = fact.get("name") or "ЖК"
        location = fact.get("location") or "локация уточняется"
        price = str(fact.get("price_range") or "цену лучше уточнить").replace("от ", "")
        finishing = str(fact.get("finishing") or "отделку лучше уточнить")
        ready = str(fact.get("ready") or "срок готовности лучше уточнить")
        metro = str(fact.get("metro") or "").strip()

        if idx == 1:
            compare_text = "свежий старт по цене и отделке"
        elif idx == 2:
            compare_text = "другой район и более спокойный вход"
        else:
            compare_text = "альтернатива с другим сценарием переезда"

        if metro and metro != "информация уточняется":
            compare_text = f"{compare_text}, плюс метро {metro}"

        line = (
            f"{idx}. {name} в {location} — новый вариант для сравнения: {price}, {finishing}, срок {ready}. "
            f"На фоне прошлых вариантов он сильнее как {compare_text}."
        )
        lines.append(line)

    lines.append(_pick_question(PROFILE_REGISTRY["repeat_search"]))
    return "\n\n".join(lines)


def _default_response(_: dict[str, Any]) -> str:
    return (
        "Чтобы не промахнуться, уточню один главный ориентир.\n\n"
        f"{_pick_question(PROFILE_REGISTRY['default'])}"
    )


def _explain_selection_response(card: dict[str, Any]) -> str:
    facts = (card.get("facts") or [])[:3]
    lines = ["Я выбрала эти варианты не по одному признаку, а по тому, как они закрывают ваш запрос: бюджет, район и подтверждённая польза для жизни."]
    for idx, fact in enumerate(facts, start=1):
        name = fact.get("name") or "ЖК"
        schools = str(fact.get("schools") or "").strip()
        kindergartens = str(fact.get("kindergartens") or "").strip()
        parks = str(fact.get("parks") or "").strip()
        if schools or kindergartens or parks or fact.get("yard_without_cars"):
            proof = "; ".join(part for part in (schools, kindergartens, parks, "двор без машин" if fact.get("yard_without_cars") else "") if part)
            reason = f"по нему есть подтверждённая польза: {proof}"
        else:
            reason = "по семейной инфраструктуре данных пока мало, поэтому этот вариант лучше дополнительно проверить перед уверенной рекомендацией"
        lines.append(f"{idx}. {name} — {reason}.")
    lines.append(_pick_question(PROFILE_REGISTRY["explain_selection"]))
    return "\n\n".join(lines)


def _fact_check_response(card: dict[str, Any]) -> str:
    fact = (card.get("facts") or [{}])[0]
    name = fact.get("name") or "этому ЖК"
    missing = fact.get("missing") or card.get("missing") or "пока не вижу подтверждения этого пункта"
    return (
        f"По {name} я бы не подтверждала это как факт: {str(missing).replace('В карточке', 'в данных').replace('в карточке', 'в данных')}\n\n"
        "Если этот параметр принципиален, лучше отдельно проверить его по конкретной квартире или корпусу.\n\n"
        f"{_pick_question(PROFILE_REGISTRY['fact_check'])}"
    )


def _selected_details_response(card: dict[str, Any]) -> str:
    fact = (card.get("facts") or [{}])[0]
    name = fact.get("name") or "выбранному ЖК"
    display_name = f"ЖК «{name}»" if "ЖК" not in str(name) else str(name)
    location = fact.get("location") or "локация уточняется"
    price = fact.get("price_range") or "цену нужно уточнить"
    area = fact.get("area_range") or fact.get("area") or "площади нужно уточнить"
    metro = fact.get("metro") or "метро нужно уточнить"
    ready = fact.get("ready") or "срок готовности нужно уточнить"
    finishing = fact.get("finishing") or "отделку нужно уточнить"
    developer = fact.get("developer") or "застройщика нужно уточнить"
    houses = fact.get("houses_info") or "по корпусам лучше уточнить отдельно"
    infrastructure = fact.get("infrastructure") or {}
    parks = infrastructure.get("parks") or []
    infra_parts = []
    if infrastructure.get("schools"):
        infra_parts.append("школы")
    if infrastructure.get("kindergartens"):
        infra_parts.append("детские сады")
    if infrastructure.get("shops"):
        infra_parts.append("магазины")
    if parks:
        infra_parts.append("парки рядом: " + ", ".join(parks[:3]))
    if infrastructure.get("yard_without_cars"):
        infra_parts.append("двор без машин")
    infra_text = "; ".join(infra_parts) if infra_parts else "по инфраструктуре нужны дополнительные данные"
    return (
        f"По {display_name} удалось собрать нормальную картину."
        f" Я бы смотрела его как вариант в {location}: цена {price}, площади {area}, метро {metro}.\n\n"
        f"По срокам и формату он удобен тем, что уже указан понятный контур проекта: {ready}, {finishing}, застройщик {developer}. По корпусам: {houses}\n\n"
        f"Для жизни здесь сильнее всего выглядит инфраструктура: {infra_text}. Не добавляю наличие конкретной квартиры, бронь или live-условия — это надо проверять отдельно.\n\n"
        f"{_pick_question(PROFILE_REGISTRY['selected_details'])}"
    )


def _refine_search_response(card: dict[str, Any]) -> str:
    facts = (card.get("facts") or [])[:3]
    missing = card.get("missing") or ""
    lines = ["Поняла, сужаю поиск: оставляю только сданные варианты с отделкой, чтобы не тратить время на лишние."]
    if missing:
        lines.append(f"Сразу честно: {missing}")
    for idx, fact in enumerate(facts, start=1):
        name = fact.get("name") or "ЖК"
        location = fact.get("location") or "локация уточняется"
        price = str(fact.get("price_range") or "цену лучше уточнить").replace("от ", "")
        finishing = str(fact.get("finishing") or "")
        ready = str(fact.get("ready") or "")
        why_parts = [part for part in (finishing, ready) if part]
        if idx == 1:
            why_parts.append("если важнее быстрый и понятный вход")
        elif idx == 2:
            why_parts.append("если важнее более спокойный семейный сценарий")
        else:
            why_parts.append("если нужен ещё один вариант для сравнения по бюджету и локации")
        reason = ", ".join(why_parts) if why_parts else "подходит по текущим фильтрам"
        lines.append(f"{idx}. {name} в {location} — {reason}; бюджет {price}.")
    lines.append(_pick_question(PROFILE_REGISTRY["refine_search"]))
    return "\n\n".join(lines)


def _operator_response(card: dict[str, Any]) -> str:
    fact = (card.get("facts") or [{}])[0]
    return (
        f"По {fact.get('name') or 'этому ЖК'} могу передать контекст оператору.\n\n"
        f"Сейчас по этому варианту вижу {fact.get('location', 'локация')} и {fact.get('price_range', 'цена')}, "
        f"но live-детали лучше проверить отдельно.\n\n"
        f"{_pick_question(PROFILE_REGISTRY['operator'])}"
    )


def _off_topic_response(_: dict[str, Any]) -> str:
    return "Я помогаю только с новостройками Москвы и Московской области.\n\nВернёмся к подбору?"


def _compose_response(profile: ScenarioProfile, card: dict[str, Any]) -> str:
    if profile.name == "family":
        return _family_response(card)
    if profile.name == "investment":
        return _investment_response(card)
    if profile.name == "rental":
        return _rental_response(card)
    if profile.name == "mortgage":
        return _mortgage_response(card)
    if profile.name == "installment":
        return _installment_response(card)
    if profile.name == "repeat_search":
        return _repeat_search_response(card)
    if profile.name == "explain_selection":
        return _explain_selection_response(card)
    if profile.name == "fact_check":
        return _fact_check_response(card)
    if profile.name == "selected_details":
        return _selected_details_response(card)
    if profile.name == "refine_search":
        return _refine_search_response(card)
    if profile.name == "default":
        return _default_response(card)
    if profile.name == "operator":
        return _operator_response(card)
    if profile.name == "off_topic":
        return _off_topic_response(card)
    return _search_response(card)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(needle in lowered for needle in needles)


def _extract_first_number(text: str) -> float | None:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text.replace(" ", ""))
    return float(match.group(1)) if match else None


def _extract_max_number(text: str) -> float | None:
    values = [float(match) for match in re.findall(r"([0-9]+(?:\.[0-9]+)?)", text.replace(" ", ""))]
    return max(values) if values else None


def _family_accent(text: str) -> str:
    lowered = text.lower()
    if _contains_any(lowered, ("учебная логистика", "маршруты с ребён")):
        return "education"
    if _contains_any(lowered, ("выделила за зел", "зелёное окружение", "прогулочный сценарий")):
        return "green_walks"
    if _contains_any(lowered, ("переезда без", "затяжного ремонта", "проще рассматривать для переезда")):
        return "move_in"
    if _contains_any(lowered, ("двор без машин", "спокойствия")):
        return "safe_yard"
    if _contains_any(lowered, ("учеб", "школ", "сад", "логист")):
        return "education"
    if _contains_any(lowered, ("парк", "лес", "зелён", "прогул")):
        return "green_walks"
    if _contains_any(lowered, ("двор", "спокой")):
        return "safe_yard"
    if _contains_any(lowered, ("отделк", "переезд", "ремонт")):
        return "move_in"
    if _contains_any(lowered, ("цена", "бюджет", "стоим")):
        return "budget"
    return "generic"


def _score_completeness(profile: ScenarioProfile, card: dict[str, Any], response: str) -> tuple[int, list[str]]:
    score = 100
    notes: list[str] = []
    lowered = response.lower()
    if "regex" in lowered or "mcp" in lowered or "json" in lowered:
        score -= 20
        notes.append("tech_terms_leak")
    if profile.name == "family":
        family = (card.get("facts") or [{}])[0].get("family_infrastructure") or {}
        has_family_fact = bool(family) or any(k in lowered for k in ("школ", "сад", "парк", "двор без машин"))
        if not has_family_fact:
            score -= 35
            notes.append("family_fact_missing")
        if not _contains_any(lowered, ("цена", "от ", "млн", "руб")):
            score -= 15
            notes.append("family_price_missing")
        if not _contains_any(lowered, ("лучш", "полез", "подходит", "можно", "выдел", "главный плюс", "сильн", "выгод", "акцент")):
            score -= 10
            notes.append("family_value_missing")
    elif profile.name == "investment":
        paragraphs = [part for part in response.split("\n\n") if part.strip()]
        if not _contains_any(lowered, ("цена", "вход", "ипотек", "взнос")):
            score -= 25
            notes.append("investment_price_missing")
        if not _contains_any(lowered, ("егрн", "сделок", "позици")):
            score -= 25
            notes.append("investment_sales_missing")
        facts = card.get("facts") or []
        if len(facts) >= 2 and not any(part.strip().startswith(("2.", "3.")) for part in paragraphs):
            score -= 20
            notes.append("investment_shortlist_missing")
        if _contains_any(lowered, ("доходност", "пассивн", "окуп", "гарантир", "заработ")) and not _contains_any(lowered, ("не обещ", "без обещ")):
            score -= 25
            notes.append("investment_profit_promise")
    elif profile.name == "rental":
        paragraphs = [part for part in response.split("\n\n") if part.strip()]
        if not _contains_any(lowered, ("аренд", "под сдач", "сдач", "снимать")):
            score -= 20
            notes.append("rental_context_missing")
        if not _contains_any(lowered, ("компакт", "студ", "однуш", "евро", "метро", "готов", "отделк")):
            score -= 25
            notes.append("rental_signal_missing")
        if _contains_any(lowered, ("егрн", "объявл", "акци", "м²", "метров", "позици")):
            score -= 10
            notes.append("rental_too_technical")
        if len(paragraphs) < 2:
            score -= 10
            notes.append("rental_not_spaced")
        if len(facts := (card.get("facts") or [])) >= 2 and not any(part.strip().startswith(("2.", "3.")) for part in paragraphs):
            score -= 15
            notes.append("rental_shortlist_missing")
        if _contains_any(lowered, ("ставк", "доходност", "окуп", "yield", "прибыл")):
            score -= 25
            notes.append("rental_yield_promise")
    elif profile.name == "mortgage":
        if not _contains_any(lowered, ("семейная ипотек", "ипотек")):
            score -= 25
            notes.append("mortgage_context_missing")
        if not _contains_any(lowered, ("6%", "ставк", "втб", "сбер")):
            score -= 30
            notes.append("mortgage_terms_missing")
        if not _contains_any(lowered, ("первый взнос", "взнос")):
            score -= 20
            notes.append("mortgage_down_payment_missing")
        if _contains_any(lowered, ("одобрят", "точно одобр", "гарантир", "заброниру")) and not _contains_any(lowered, ("не обещ", "без проверки", "провер")):
            score -= 35
            notes.append("mortgage_unsafe_promise")
    elif profile.name == "installment":
        if not _contains_any(lowered, ("рассроч", "18 месяцев", "условия оплаты")):
            score -= 30
            notes.append("installment_fact_missing")
        if not _contains_any(lowered, ("скид", "выгод", "акци")):
            score -= 25
            notes.append("discount_fact_missing")
        if _contains_any(lowered, ("заброниру", "гарантир", "точно будет")) and not _contains_any(lowered, ("не обещ", "провер", "уточн")):
            score -= 35
            notes.append("installment_unsafe_promise")
    elif profile.name == "search":
        if response.count("\n\n") < 1:
            score -= 10
            notes.append("search_not_spaced")
        if not _contains_any(lowered, ("1.", "2.", "вариант")):
            score -= 20
            notes.append("search_no_list")
        if not _contains_any(lowered, ("цена", "отделк", "готов", "метро", "район")):
            score -= 20
            notes.append("search_no_presentation")
        if not _contains_any(lowered, ("на фоне", "сравн", "сильнее", "выигрыв", "в отличие", "по сравнению")):
            score -= 20
            notes.append("search_comparison_missing")
    elif profile.name == "repeat_search":
        if not _contains_any(lowered, ("друг", "нов", "ещё", "еще", "повтор", "другие", "новые")):
            score -= 20
            notes.append("repeat_search_not_fresh")
        if not _contains_any(lowered, ("цена", "отделк", "готов", "метро", "район")):
            score -= 20
            notes.append("repeat_search_no_presentation")
    elif profile.name == "explain_selection":
        if not _contains_any(lowered, ("почему", "выбра", "подбор", "закрывают", "подходит", "польз")):
            score -= 25
            notes.append("explain_selection_no_logic")
        if not _contains_any(lowered, ("школ", "сад", "парк", "двор", "цена", "метро", "отделк", "готов")):
            score -= 25
            notes.append("explain_selection_no_evidence")
    elif profile.name == "fact_check":
        if not _contains_any(lowered, ("не подтверж", "подтверж", "нет подтверждения", "пока не вижу", "лучше отдельно проверить")):
            score -= 30
            notes.append("fact_check_no_verdict")
        if _contains_any(lowered, ("скорее всего", "думаю", "может быть")):
            score -= 25
            notes.append("fact_check_guessing")
    elif profile.name == "selected_details":
        if not _contains_any(lowered, ("удалось собрать", "нормальную картину", "главное", "подробнее", "выбранному", "по жк")):
            score -= 25
            notes.append("selected_details_not_dossier")
        if not _contains_any(lowered, ("цена", "площ", "метро")):
            score -= 25
            notes.append("selected_details_base_facts_missing")
        if not _contains_any(lowered, ("срок", "корпус", "отделк", "застройщик")):
            score -= 20
            notes.append("selected_details_project_facts_missing")
        if not _contains_any(lowered, ("школ", "сад", "парк", "магазин", "двор")):
            score -= 20
            notes.append("selected_details_infrastructure_missing")
        if _contains_any(lowered, ("не подтверждала", "не подтверждаю", "нет подтверждения", "окна")):
            score -= 35
            notes.append("selected_details_misrouted_as_fact_check")
        if _contains_any(lowered, ("заброниру", "точно есть квартира", "гарантир")) and not _contains_any(lowered, ("не добавляю", "проверять", "уточнить")):
            score -= 35
            notes.append("selected_details_unsafe_live_promise")
    elif profile.name == "refine_search":
        if not _contains_any(lowered, ("уточн", "обнов", "услов", "фильтр", "поиск")):
            score -= 20
            notes.append("refine_search_no_update")
        if not _contains_any(lowered, ("не буду обещать", "проверять", "проверить", "честно", "не добавляю", "уточнить")):
            score -= 15
            notes.append("refine_search_no_gap_boundary")
    elif profile.name == "default":
        if response.count("?") != 1:
            score -= 20
            notes.append("default_question_count_not_one")
        if _contains_any(lowered, ("1.", "2.", "жк", "вариант", "shortlist")):
            score -= 25
            notes.append("default_should_not_show_list")
    elif profile.name == "operator":
        if not _contains_any(lowered, ("оператор", "live", "провер")):
            score -= 25
            notes.append("operator_boundary_weak")
    elif profile.name == "off_topic":
        if not _contains_any(lowered, ("новостройк", "москов", "москвы", "московской области")):
            score -= 20
            notes.append("off_topic_boundary_weak")
    score = max(0, min(100, score))
    return score, notes


def _score_beauty(profile: ScenarioProfile, response: str) -> tuple[int, list[str]]:
    score = 100
    notes: list[str] = []
    paragraphs = [part for part in response.split("\n\n") if part.strip()]
    question_count = response.count("?")
    lowered = response.lower()
    if len(paragraphs) < 2:
        score -= 20
        notes.append("too_dense")
    if len(paragraphs) > 5:
        score -= 10
        notes.append("too_fragmented")
    if question_count != 1:
        score -= 15
        notes.append("question_count_not_one")
    max_length = 1200 if profile.name in ("family", "investment", "rental", "explain_selection", "selected_details", "refine_search") else 760 if profile.name in ("mortgage", "installment") else 550
    if len(response) > max_length:
        score -= 10
        notes.append("too_long")
    if _contains_any(lowered, ("regex", "mcp", "json", "в карточке", "карточк", "fact-check", "fact_check", "в базе", "поиск выполнен")):
        score -= 25
        notes.append("tech_terms_leak")
    if profile.name in ("family", "investment", "rental", "search", "selected_details") and not _contains_any(
        lowered,
        ("я бы", "смотрела", "выбирала", "стоит смотреть", "сильнее", "удоб", "подходит", "смотрится", "выдел", "жив", "приятн"),
    ):
        score -= 10
        notes.append("not_salesy_enough")
    if profile.name == "family":
        lowered = response.lower()
        if not _contains_any(lowered, ("для семьи", "семье", "реб", "дет", "сад", "школ", "парк", "двор без машин", "прогул")):
            score -= 20
            notes.append("family_not_warm_enough")
        if "для семьи:" in lowered:
            score -= 25
            notes.append("family_dry_label")
        if "подходит семье:" in lowered:
            score -= 30
            notes.append("family_dry_label")
        if not _contains_any(lowered, ("удоб", "спокой", "прогул", "переезд", "каждый день", "с ребён", "выдел", "сильн", "акцент")):
            score -= 20
            notes.append("family_benefit_not_explained")
        if not _contains_any(lowered, ("я бы", "смотрела", "сначала", "здесь", "подходит")):
            score -= 10
            notes.append("family_not_consultative")
        option_blocks = [part for part in paragraphs if part.strip().startswith(("1.", "2.", "3."))]
        if option_blocks and any(block.count(".") < 3 for block in option_blocks):
            score -= 25
            notes.append("family_card_too_thin")
        if option_blocks and len(set(_family_accent(block) for block in option_blocks)) < len(option_blocks):
            score -= 25
            notes.append("family_accents_not_distinct")
    score = max(0, min(100, score))
    return score, notes


def _assess_response(profile: ScenarioProfile, card: dict[str, Any], response: str) -> dict[str, Any]:
    completeness, completeness_notes = _score_completeness(profile, card, response)
    beauty, beauty_notes = _score_beauty(profile, response)
    warnings = list(dict.fromkeys(completeness_notes + beauty_notes))
    overall = round(completeness * 0.6 + beauty * 0.4)
    return {
        "warnings": warnings,
        "completeness": completeness,
        "beauty": beauty,
        "overall": overall,
    }


def run_case(case: ScenarioCase) -> dict[str, Any]:
    profile = PROFILE_REGISTRY[case.profile_name]
    query_profile = _build_query_profile(profile, case.user_text)
    card = case.search_response
    scenario_prompt = ""
    if profile.scenario_prompt_path:
        prompt_path = REPO / profile.scenario_prompt_path
        if prompt_path.exists():
            scenario_prompt = prompt_path.read_text(encoding="utf-8").strip()
    response = _compose_response(profile, card)
    assessment = _assess_response(profile, card, response)
    return {
        "case": case.name,
        "profile": profile.name,
        "query_profile": query_profile,
        "scenario_prompt": scenario_prompt,
        "card": card,
        "response": response,
        "assessment": assessment,
    }


def _default_cases() -> list[ScenarioCase]:
    return [
        ScenarioCase("family-1", "Двушка для семьи в Москве", "family", _family_card()),
        ScenarioCase("mortgage-1", "Проверь Мичуринский парк под семейную ипотеку", "mortgage", _mortgage_card()),
        ScenarioCase("installment-1", "Какие есть рассрочка и скидки по Мичуринскому парку?", "installment", _installment_card()),
        ScenarioCase("investment-1", "Что взять под инвестиции?", "investment", _investment_card()),
        ScenarioCase("rental-1", "Что под сдачу в аренду?", "rental", _rental_card()),
        ScenarioCase("search-1", "Покажи варианты для жизни", "search", _search_card()),
        ScenarioCase("repeat-search-1", "Покажи другие варианты", "repeat_search", _repeat_search_card()),
        ScenarioCase("explain-selection-1", "Почему эти квартиры подходят для детей и собаки с кошкой?", "explain_selection", _explain_selection_card()),
        ScenarioCase("fact-check-1", "Точно это квартира с окнами на две стороны?", "fact_check", _fact_check_card()),
        ScenarioCase("selected-details-1", "Расскажи подробнее про Мичуринский парк", "selected_details", _selected_details_card()),
        ScenarioCase("refine-search-1", "До 12 млн, 1-комнатная, до 2 км от метро, можно перепланировать в двушку", "refine_search", _refine_search_card()),
        ScenarioCase("default-1", "Нужен что-нибудь нормальное", "default", _default_card()),
        ScenarioCase("operator-1", "Можно проверить актуальные детали по ЖК?", "operator", _operator_card()),
        ScenarioCase("off-topic-1", "Расскажи анекдот", "off_topic", _off_topic_card()),
    ]


def _print_case(result: dict[str, Any]) -> None:
    print(f"=== CASE: {result['case']} | profile={result['profile']} ===")
    print("QUERY PROFILE:")
    print(json.dumps(result["query_profile"], ensure_ascii=False, indent=2))
    print()
    if result.get("scenario_prompt"):
        print("SCENARIO SUBPROMPT:")
        print(result["scenario_prompt"])
        print()
    print("MCP CARD:")
    print(json.dumps(result["card"], ensure_ascii=False, indent=2))
    print()
    print("LLM RESPONSE:")
    print(result["response"])
    assessment = result["assessment"]
    print(
        "QUALITY:",
        f"completeness={assessment['completeness']}/100",
        f"beauty={assessment['beauty']}/100",
        f"overall={assessment['overall']}/100",
    )
    if assessment["warnings"]:
        print("WARNINGS:", ", ".join(assessment["warnings"]))
    else:
        print("WARNINGS: none")
    cost = result.get("cost") or {}
    print(
        "COST:",
        f"today=${cost.get('today', 0.0):.2f}",
        f"week=${cost.get('week', 0.0):.2f}",
        f"month=${cost.get('month', 0.0):.2f}",
        f"total=${cost.get('total', 0.0):.2f}",
        f"daily_budget_left=${max(0.0, DAILY_REQUEST_BUDGET_USD - float(cost.get('today', 0.0))):.2f}",
    )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-first scenario simulator for nmbot")
    parser.add_argument("--scenario", choices=sorted(PROFILE_REGISTRY.keys()), help="Scenario to run")
    parser.add_argument("--all", action="store_true", help="Run all built-in scenarios")
    args = parser.parse_args()

    cases = _default_cases()
    selected = cases if args.all or not args.scenario else [case for case in cases if case.profile_name == args.scenario]

    if not selected:
        print("No cases selected.", file=sys.stderr)
        return 1

    results = [run_case(case) for case in selected]
    cost_snapshot = _load_openrouter_cost()
    for result in results:
        result["cost"] = cost_snapshot or {}
    for result in results:
        _print_case(result)

    passed = sum(1 for result in results if not result["assessment"]["warnings"])
    avg_completeness = round(sum(result["assessment"]["completeness"] for result in results) / len(results))
    avg_beauty = round(sum(result["assessment"]["beauty"] for result in results) / len(results))
    avg_overall = round(sum(result["assessment"]["overall"] for result in results) / len(results))
    total_cost_usd = round(float((cost_snapshot or {}).get("today", 0.0)), 2)
    print(
        f"FINAL: {json.dumps({'cases': len(results), 'passed': passed, 'failed': len(results) - passed, 'avg_completeness': avg_completeness, 'avg_beauty': avg_beauty, 'avg_overall': avg_overall, 'total_cost_usd': total_cost_usd, 'budget_left_usd': round(max(0.0, DAILY_REQUEST_BUDGET_USD - total_cost_usd), 2)}, ensure_ascii=False)}"
    )
    return 0 if passed == len(results) and avg_overall >= 80 else 2


if __name__ == "__main__":
    raise SystemExit(main())
