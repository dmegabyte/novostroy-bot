from __future__ import annotations

from nmbot_v2.contracts import IntentGoal, OptionCard
from nmbot_v2.fact_context import ALLOWED_FACTS, fact_availability, evidence_sufficient, present_fact_names, present_fact_names_for_cards


def test_parks_is_allowlisted_but_not_inferred_from_object_name() -> None:
    card = OptionCard(name="Мичуринский парк", infrastructure=())

    assert "parks" in ALLOWED_FACTS
    assert "parks" not in present_fact_names(card)


def test_parks_is_grounded_by_canonical_infrastructure_tokens() -> None:
    cards = (
        OptionCard(name="ЖК Лесной", infrastructure=("лесопарк рядом",)),
        OptionCard(name="ЖК Речной", infrastructure=("набережная и вода",)),
        OptionCard(name="ЖК Школьный", infrastructure=("школа",)),
    )

    assert present_fact_names_for_cards(cards)[0][-1] == "parks"
    assert "parks" in present_fact_names_for_cards(cards)[1]
    assert "parks" not in present_fact_names_for_cards(cards)[2]


def test_fact_availability_counts_per_requested_fact() -> None:
    availability = fact_availability(
        [
            OptionCard(name="А", infrastructure=("парк рядом",)),
            OptionCard(name="Б", infrastructure=("школа",)),
            OptionCard(name="В", infrastructure=("green zone",)),
        ],
        ["parks", "schools", "parks", "secret"],
    )

    assert availability.available_counts == {"parks": 2, "schools": 1}
    assert availability.missing_facts == ()
    assert len(availability.present_by_card) == 3


def test_evidence_sufficient_thresholds_for_current_goals() -> None:
    one_park = [
        OptionCard(name="А", infrastructure=("парк рядом",)),
        OptionCard(name="Б"),
    ]
    two_parks = [
        OptionCard(name="А", infrastructure=("парк рядом",)),
        OptionCard(name="Б", infrastructure=("лес рядом",)),
    ]

    assert evidence_sufficient(IntentGoal.ANSWER_CURRENT, one_park, ["parks"])
    assert not evidence_sufficient(IntentGoal.COMPARE_CURRENT, one_park, ["parks"])
    assert not evidence_sufficient("recommend_current", one_park, ["parks"])
    assert evidence_sufficient(IntentGoal.COMPARE_CURRENT, two_parks, ["parks"])
    assert evidence_sufficient(IntentGoal.RECOMMEND_CURRENT, two_parks, ["parks"])


def test_evidence_sufficient_without_requested_facts_uses_safe_comparative_fields() -> None:
    one_safe = [OptionCard(name="А", price_min=10_000_000), OptionCard(name="Б")]
    two_safe = [OptionCard(name="А", price_min=10_000_000), OptionCard(name="Б", metro="Сокол")]

    assert evidence_sufficient(IntentGoal.ANSWER_CURRENT, one_safe, [])
    assert not evidence_sufficient(IntentGoal.RECOMMEND_CURRENT, one_safe, [])
    assert evidence_sufficient(IntentGoal.RECOMMEND_CURRENT, two_safe, [])
