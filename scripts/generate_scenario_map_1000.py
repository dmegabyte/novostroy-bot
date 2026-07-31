#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DATA = ROOT / "data"


INTENTS = [
    ("search", "search", "find / shortlist"),
    ("refine_search", "refine_search", "tighten filters"),
    ("explain_selection", "explain_selection", "justify choices"),
    ("fact_check", "fact_check", "verify specific fact"),
    ("repeat_search", "repeat_search", "show more without repeats"),
    ("compare_options", "compare_options", "compare options"),
    ("mortgage", "mortgage", "finance and payment"),
    ("operator", "operator", "handoff to human"),
    ("objection", "objection", "recover from rejection"),
    ("off_topic", "off_topic", "boundary / unrelated"),
]


FOCUSES = [
    (
        "budget",
        "бюджету (например, до 8 млн / 12 млн / 15 млн)",
        "какой потолок цены",
        "budget, max_price, price_range",
    ),
    (
        "rooms",
        "комнатности (студия / 1к / 2к / 3к / 4к)",
        "сколько комнат нужно",
        "rooms, layout, family size",
    ),
    (
        "geography",
        "локации (район, город, округ, область)",
        "какой район или город нужен",
        "district, city, region, move radius",
    ),
    (
        "metro",
        "метро и транспорту (пешком, МЦД, автобус, выезд)",
        "сколько минут до метро допустимо",
        "metro, walk_minutes, transport",
    ),
    (
        "finishing",
        "отделке (с отделкой, без отделки, white box)",
        "какой формат отделки нужен",
        "finishing, renovation, key_ready",
    ),
    (
        "readiness",
        "сроку сдачи (сдан, котлован, ключи в 2027/2028)",
        "какой год сдачи подходит",
        "ready, delivery_year, construction_stage",
    ),
    (
        "family",
        "семейному сценарию (школа, сад, двор без машин, парк)",
        "что важнее для семьи",
        "schools, kindergartens, yard_without_cars, parks",
    ),
    (
        "investment",
        "инвестициям (ликвидность, рост цены, перепродажа)",
        "ликвидность, рост цены или доходность",
        "investment, yield, resale, liquidity",
    ),
    (
        "rent",
        "аренде (долгосрок, посуточно, для студентов)",
        "долгосрок или посуточно",
        "rental, occupancy, tenant profile",
    ),
    (
        "legal",
        "документам и статусу (ДДУ, эскроу, апартаменты, прописка)",
        "какие документы и статус важны",
        "DДУ, escrow, apartment status, registration",
    ),
]


STAGES = [
    ("first_touch", "С нуля"),
    ("after_shortlist", "После первого списка"),
    ("after_rejection", "Если предыдущие варианты не подошли"),
    ("after_selection", "Если уже выбран один ЖК"),
    ("after_budget_change", "Когда бюджет изменился"),
    ("after_deadline_change", "Когда сдвинулся срок"),
    ("after_mortgage_clue", "Когда всплыла ипотека"),
    ("after_operator_offer", "Когда нужен менеджер"),
    ("after_repeat_request", "Когда просят ещё варианты"),
    ("after_offtopic_boundary", "Когда нужно вернуть разговор к недвижимости"),
]


OFFTOPIC_TOPICS = [
    "анекдот",
    "погоду",
    "рецепт ужина",
    "курс доллара",
    "футбол",
    "музыку",
    "кино",
    "политику",
    "котов",
    "программирование",
]


INTENT_TEMPLATES = {
    "search": "{stage}: подберите варианты по {focus}.",
    "refine_search": "{stage}: уточните фильтры по {focus} и уберите лишнее.",
    "explain_selection": "{stage}: объясните, почему эти ЖК лучше по {focus}.",
    "fact_check": "{stage}: проверьте, есть ли у выбранного ЖК {focus}.",
    "repeat_search": "{stage}: покажите ещё варианты без повторов, сохранив акцент на {focus}.",
    "compare_options": "{stage}: сравните 2–3 варианта именно по {focus}.",
    "mortgage": "{stage}: какие условия доступны по {focus}.",
    "operator": "{stage}: соедините с менеджером, чтобы обсудить {focus}.",
    "objection": "{stage}: если по {focus} не подходит, что можно предложить вместо этого?",
    "off_topic": "{stage}: после обсуждения {focus} пользователь уходит в оффтоп и просит {offtopic}.",
}


CLARIFY_BY_FOCUS = {
    "budget": "Какой потолок цены?",
    "rooms": "Сколько комнат нужно?",
    "geography": "Какой район или город нужен?",
    "metro": "Сколько минут до метро допустимо?",
    "finishing": "Какой формат отделки нужен?",
    "readiness": "Какой год сдачи подходит?",
    "family": "Что важнее для семьи?",
    "investment": "Что важнее: ликвидность, рост цены или доходность?",
    "rent": "Долгосрок или посуточно?",
    "legal": "Какие документы и статус важны?",
}


MCP_HINT_BY_FOCUS = {
    "budget": "budget, max_price, price_range",
    "rooms": "rooms, layout, family_size",
    "geography": "district, city, region",
    "metro": "metro, walk_minutes, transport",
    "finishing": "finishing, renovation",
    "readiness": "ready, delivery_year, construction_stage",
    "family": "schools, kindergartens, yard_without_cars, parks",
    "investment": "investment, yield, resale, liquidity",
    "rent": "rental, occupancy, tenant_profile",
    "legal": "DДУ, escrow, apartment_status, registration",
}


BOT_GOAL_BY_INTENT = {
    "search": "Понять первичный спрос и показать shortlist.",
    "refine_search": "Сузить shortlist без потери важных фильтров.",
    "explain_selection": "Объяснить пользу уже выбранных ЖК.",
    "fact_check": "Подтвердить один конкретный факт.",
    "repeat_search": "Показать другие варианты без дублей.",
    "compare_options": "Сравнить несколько вариантов по одному критерию.",
    "mortgage": "Отработать вопрос финансирования и условий оплаты.",
    "operator": "Перевести на человека или записать на контакт.",
    "objection": "Снять возражение и продолжить подбор.",
    "off_topic": "Мягко вернуть разговор к недвижимости.",
}


def build_question(intent: str, focus_key: str, focus_text: str, stage_text: str, idx: int) -> str:
    if intent == "off_topic":
        topic = OFFTOPIC_TOPICS[idx % len(OFFTOPIC_TOPICS)]
        return INTENT_TEMPLATES[intent].format(stage=stage_text, focus=focus_text, offtopic=topic)

    if intent == "mortgage":
        base = INTENT_TEMPLATES[intent].format(stage=stage_text, focus=focus_text)
        if focus_key == "budget":
            return base + " Например, сколько будет первый взнос и платёж?"
        if focus_key == "rooms":
            return base + " И что меняется для студии, однушки и двушки?"
        if focus_key == "family":
            return base + " Особенно если семья с детьми и нужен льготный вариант."
        return base + ""

    if intent == "operator":
        base = INTENT_TEMPLATES[intent].format(stage=stage_text, focus=focus_text)
        if focus_key == "legal":
            return base + " Хочу уточнить документы и договор."
        if focus_key == "readiness":
            return base + " Нужен разговор по срокам сдачи и записи на просмотр."
        return base + " Пожалуйста, без лишней переписки, нужен живой человек."

    if intent == "fact_check":
        base = INTENT_TEMPLATES[intent].format(stage=stage_text, focus=focus_text)
        if focus_key == "family":
            return base + " Нужен двор без машин, школа или детский сад?"
        if focus_key == "metro":
            return base + " Точно ли пешком до метро без пересадок?"
        if focus_key == "legal":
            return base + " Точно ли с документами и статусом всё чисто?"
        return base + ""

    return INTENT_TEMPLATES[intent].format(stage=stage_text, focus=focus_text)


def build_clarify(intent: str, focus_key: str) -> str:
    if intent == "off_topic":
        return "Мягко вернуть к теме новостроек."
    return CLARIFY_BY_FOCUS[focus_key]


def build_case(intent_idx: int, focus_idx: int, stage_idx: int, case_id: int) -> dict:
    intent_key, intent_name, intent_desc = INTENTS[intent_idx]
    focus_key, focus_text, clarify_text, mcp_hint = FOCUSES[focus_idx]
    stage_key, stage_text = STAGES[stage_idx]
    question = build_question(intent_key, focus_key, focus_text, stage_text, case_id)
    return {
        "id": case_id,
        "signature": f"{intent_key}.{focus_key}.{stage_key}",
        "intent": intent_key,
        "intent_label": intent_name,
        "intent_desc": intent_desc,
        "focus": focus_key,
        "focus_label": focus_text,
        "stage": stage_key,
        "stage_label": stage_text,
        "user_question": question,
        "bot_goal": BOT_GOAL_BY_INTENT[intent_key],
        "clarify": build_clarify(intent_key, focus_key),
        "mcp_hint": MCP_HINT_BY_FOCUS[focus_key],
    }


def main() -> None:
    cases = []
    seen = set()
    case_id = 1
    for intent_idx in range(len(INTENTS)):
        for focus_idx in range(len(FOCUSES)):
            for stage_idx in range(len(STAGES)):
                case = build_case(intent_idx, focus_idx, stage_idx, case_id)
                if case["user_question"] in seen:
                    raise RuntimeError(f"duplicate question generated: {case['user_question']}")
                seen.add(case["user_question"])
                cases.append(case)
                case_id += 1

    if len(cases) != 1000:
        raise RuntimeError(f"expected 1000 cases, got {len(cases)}")

    DATA.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)

    jsonl_path = DATA / "scenario_map_1000.jsonl"
    md_path = DOCS / "scenario_map_1000.md"

    with jsonl_path.open("w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    lines = []
    lines.append("# Scenario Map 1000")
    lines.append("")
    lines.append("## Source signals")
    lines.append("- Google Sheets: `gid=672284209` (408 rows) and `gid=24673258` (1523 rows), columns `call_type` + `full_text_dialogue`.")
    lines.append("- Project docs/prompts: scenario contract, routing, and live validator rules.")
    lines.append("- The 1000 cases below are a generated expansion grounded in those sources.")
    lines.append("")
    lines.append("## How to read")
    lines.append("- `intent` = what the user is trying to do.")
    lines.append("- `focus` = the topic axis that changes the dialog.")
    lines.append("- `stage` = where the dialog is in the journey.")
    lines.append("- `clarify` = the next question to ask when the bot needs one more detail.")
    lines.append("- `mcp_hint` = the fact family that should be present in search/MCP for this case.")
    lines.append("")

    for intent_key, intent_name, intent_desc in INTENTS:
        lines.append(f"## {intent_name} — {intent_desc}")
        lines.append("")
        lines.append("| ID | focus | stage | user question | bot goal | clarify | mcp_hint |")
        lines.append("|---:|---|---|---|---|---|---|")
        for case in cases:
            if case["intent"] != intent_key:
                continue
            lines.append(
                f"| {case['id']:03d} | {case['focus_label']} | {case['stage_label']} | {case['user_question']} | {case['bot_goal']} | {case['clarify']} | {case['mcp_hint']} |"
            )
        lines.append("")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"WROTE {md_path}")
    print(f"WROTE {jsonl_path}")
    print(f"CASES {len(cases)} unique_questions {len(seen)}")


if __name__ == "__main__":
    main()
