import json

from nmbot_v2.contracts import OptionCard, ResponseBrief
from nmbot_v2.response_composer import (
    _build_card_guidance,
    _build_client_priorities,
    _build_decision_signals,
    _build_safe_comparisons,
    _model_facing_brief_payload,
    build_v3_answer_brief_payload,
    load_v3_answer_writer_prompt,
    v3_answer_writer_request_payload,
)


class _Plan:
    requested_facts = ("readiness", "metro")
    facts_needed = ()
    facets = ()
    constraints_delta = {"max_price": 20_000_000, "rooms": 2}


class _State:
    active_topic = "life"
    params = {}


def test_v3_client_first_safe_comparisons_are_literal_and_v2_stays_isolated():
    cards = (
        OptionCard(name="ЖК Север", ready="сдан", metro="Метро — 20 минут пешком", price_min=18_500_000, infrastructure=("парк",)),
        OptionCard(name="ЖК Центр", ready="сдан", metro="Метро — 12 минут пешком", price_min=17_100_000, infrastructure=("сад",)),
        OptionCard(name="ЖК Юг", ready="сдан", metro="Метро — 7 минут пешком", price_min=16_800_000, infrastructure=("школа",)),
    )
    priorities = _build_client_priorities(_State(), _Plan(), "life")
    assert priorities["ranked_criteria"][:4] == ["readiness", "metro", "budget", "rooms"]
    assert priorities["primary_focus"] == "readiness"
    guidance = _build_card_guidance(cards, {"cards": []}, (), priorities)
    assert [item["anchor_fact"] for item in guidance] == ["metro", "metro", "metro"]
    assert all(item["evidence"]["value"] == card.metro for item, card in zip(guidance, cards))
    comparisons, conclusions = _build_safe_comparisons(cards, priorities)
    assert next(item for item in comparisons if item["type"] == "shortest_pedestrian_metro")["winner"] == {"card_name": "ЖК Юг", "metro": "Метро — 7 минут пешком", "minutes": 7}
    assert next(item for item in comparisons if item["type"] == "lowest_project_starting_price")["scope"] == "project_starting_price"
    assert next(item for item in conclusions if item["type"] == "matching_room_budget_fit")["status"] == "unknown"
    brief = ResponseBrief(
        answer_goal="present_search_results",
        canonical_cards=cards,
        client_priorities=priorities,
        safe_comparisons=comparisons,
        allowed_conclusions=conclusions,
        dialogue_progress={"progress_status": "active"},
        selection_scope={"type": "shortlist"},
        card_guidance=({"card_name": "ЖК Север"},),
        decision_signals={"literal_lowest_starting_price": {"card_name": "ЖК Юг"}},
        next_actions={"preferred": "continue_dialogue"},
        cta_policy={"one_question": True},
    )
    v3 = build_v3_answer_brief_payload(brief)
    assert v3["safe_comparisons"] == list(comparisons)
    assert v3["allowed_conclusions"] == list(conclusions)
    v2_payload = _model_facing_brief_payload(brief)
    for field in (
        "client_priorities",
        "safe_comparisons",
        "allowed_conclusions",
        "dialogue_progress",
        "selection_scope",
        "card_guidance",
        "decision_signals",
        "next_actions",
        "cta_policy",
    ):
        assert field not in v2_payload
    request = v3_answer_writer_request_payload(brief)
    assert request["parameters"] == {"temperature": 0.2, "max_tokens": 5000}
    assert json.loads(request["query"].removeprefix("V3_ANSWER_BRIEF=").split("\n", 1)[0])["answer_brief"]["safe_comparisons"]
    prompt = load_v3_answer_writer_prompt()
    for required in ("current_client_request", "client_priorities.ranked_criteria", "safe_comparisons", "allowed_conclusions", "Стартовая цена ЖК", "немедленный переезд", "Не округляй"):
        assert required in prompt


def test_v3_safe_comparisons_require_all_cards_to_have_pedestrian_metro_evidence():
    cards = (
        OptionCard(name="ЖК Север", metro="Метро — 20 минут пешком", price_min=18_500_000),
        OptionCard(name="ЖК Центр", metro="Метро — 12 минут пешком", price_min=17_100_000),
        OptionCard(name="ЖК Юг", metro="Метро рядом", price_min=16_800_000),
    )

    comparisons, conclusions = _build_safe_comparisons(cards, {})

    assert all(item["type"] != "shortest_pedestrian_metro" for item in comparisons)
    assert all(item["type"] != "shortest_pedestrian_metro" for item in conclusions)


def test_v3_safe_comparisons_require_all_cards_to_have_starting_price_evidence():
    cards = (
        OptionCard(name="ЖК Север", metro="Метро — 20 минут пешком", price_min=18_500_000),
        OptionCard(name="ЖК Центр", metro="Метро — 12 минут пешком", price_min=17_100_000),
        OptionCard(name="ЖК Юг", metro="Метро — 7 минут пешком"),
    )

    comparisons, conclusions = _build_safe_comparisons(cards, {})

    assert all(item["type"] != "lowest_project_starting_price" for item in comparisons)
    assert all(item["type"] != "lowest_project_starting_price" for item in conclusions)


def test_v3_decision_signals_require_all_cards_to_have_starting_price_evidence():
    cards = (
        OptionCard(name="ЖК Север", price_min=18_500_000),
        OptionCard(name="ЖК Центр", price_min=17_100_000),
        OptionCard(name="ЖК Юг"),
    )

    signals = _build_decision_signals(cards, {})

    assert "literal_lowest_starting_price" not in signals


def test_v3_decision_signals_select_lowest_price_when_all_cards_are_comparable():
    cards = (
        OptionCard(name="ЖК Север", price_min=18_500_000),
        OptionCard(name="ЖК Центр", price_min=17_100_000),
        OptionCard(name="ЖК Юг", price_min=16_800_000),
    )

    signals = _build_decision_signals(cards, {})

    assert signals["literal_lowest_starting_price"] == {"card_name": "ЖК Юг", "price_min": 16_800_000}
