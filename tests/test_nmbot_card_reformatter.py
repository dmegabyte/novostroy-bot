from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.nmbot_card_reformatter import (
    ROOT,
    assemble_reformatted_answer,
    build_reformat_plan,
    parse_response,
    run_corpus,
)


def test_mandatory_text_naturalizes_price_location_ready_finishing_without_finishing() -> None:
    response = {
        "facts": [
            {"name": "ЖК Тест", "location": "Сокол", "min_price": 14200000, "ready": "сдан", "finishing": "без отделки"}
        ]
    }

    plan = build_reformat_plan(response, "life")

    text = plan["cards"][0]["mandatory_text"]
    assert "Локация — Сокол." in text
    assert "Квартиры в проекте — от 14,2 млн ₽." in text
    assert "Дом сдан." in text
    assert "Квартиры передаются без отделки." in text
    assert "ЖК Тест" not in text


def test_family_anchor_rotation_with_plural_aliases_from_safe_adapter() -> None:
    fixture = json.loads((ROOT / "logs/sim_fixture_four_layer_family_cards.json").read_text(encoding="utf-8"))

    plan = build_reformat_plan(fixture, "family")

    anchors = [card["anchor_fact"] for card in plan["cards"]]
    assert "parks" in anchors
    assert "schools" in anchors
    assert "daily_services" in anchors


def test_benefit_model_input_is_narrow_and_excludes_identity_price_url_full_card() -> None:
    response = {
        "facts": [
            {
                "name": "ЖК Секретный",
                "location": "Котельники",
                "min_price": 11000000,
                "school": True,
                "link": "https://example.invalid/card",
                "raw_payload": {"x": 1},
            }
        ]
    }

    plan = build_reformat_plan(response, "family")
    model_input = plan["benefit_model_input"]
    blob = json.dumps(model_input, ensure_ascii=False)

    assert set(model_input[0]) == {"idx", "evidence", "communication_goal", "allowed_concepts", "forbidden_meanings"}
    assert "ЖК Секретный" not in blob
    assert "Котельники" not in blob
    assert "11000000" not in blob
    assert "http" not in blob
    assert "raw_payload" not in blob


def test_assembly_preserves_exact_names_mandatory_facts_and_one_question() -> None:
    response = {"facts": [{"name": "ЖК «Лучи»", "location": "Солнцево", "price_min": 12000000, "ready": "2027", "finishing": "с отделкой"}]}
    plan = build_reformat_plan(response, "life")

    answer = assemble_reformatted_answer(plan, [{"idx": 1, "benefit": "Подойдёт как спокойный ориентир для сравнения."}], "Показать детали?")

    assert "ЖК «Лучи»" in answer
    assert "Локация — Солнцево." in answer
    assert "Квартиры в проекте — от 12 млн ₽." in answer
    assert "Срок готовности — 2027." in answer
    assert "Предусмотрена отделка." in answer
    assert "Подойдёт как спокойный ориентир для сравнения." in answer
    assert "\n\nПоказать детали?" in answer
    assert answer.count("?") == 1
    assert answer.rstrip().endswith("Показать детали?")


def test_benefit_indices_are_human_one_based() -> None:
    plan = build_reformat_plan(
        {"facts": [{"name": "Первый", "school": True}, {"name": "Второй", "park_near": True}]},
        "family",
    )

    assert [card["idx"] for card in plan["cards"]] == [1, 2]
    assert [item["idx"] for item in plan["benefit_model_input"]] == [1, 2]


def test_nested_legacy_infrastructure_aliases_reach_family_anchors() -> None:
    plan = build_reformat_plan(
        {
            "facts": [
                {"name": "Школы", "infrastructure": {"schools": True, "kindergartens": True}},
                {"name": "Парк", "infrastructure": {"parks": True}},
                {"name": "Сервисы", "infrastructure": {"shops": True, "services": True}},
            ]
        },
        "family",
    )

    assert [card["anchor_fact"] for card in plan["cards"]] == ["schools", "parks", "daily_services"]


@pytest.mark.parametrize(
    ("raw", "classification"),
    [
        ("{not json", "parse_error"),
        ("```json\n{\"facts\":[{\"name\":\"А\"}]}\n```", "normalized"),
        ("Вот ответ\n{\"facts\":[{\"name\":\"Б\"}],\"params\":{}}\nспасибо", "normalized"),
        ({"facts": ["нет карточек"], "missing": []}, "non_card_facts"),
        ({"facts": [], "near": []}, "empty"),
    ],
)
def test_parse_legacy_malformed_string_facts_and_empty(raw: object, classification: str) -> None:
    assert parse_response(raw).classification == classification


def test_corpus_manifest_loads_safe_sources_deduplicates_and_does_not_leak() -> None:
    report = run_corpus(ROOT / "tests/fixtures/nmbot_card_reformatter_corpus.json")

    source_ids = {item["id"] for item in report["source_files"]}
    assert source_ids == {
        "quality_scenarios",
        "dialogue_replay",
        "sim_four_layer_family_cards",
        "sim_policy_standard",
        "sim_policy_no_data_yasenevo",
        "sim_yuzhnye_sady_formatting",
        "sim_warshavskie_vorota",
        "field_sales_coverage",
        "live_scenario_pipeline_vps2",
        "response_eval_cases",
    }
    assert report["extracted_count"] >= report["unique_count"] > 0
    assert report["duplicate_count"] == report["extracted_count"] - report["unique_count"]
    assert report["classification_counts"]["normalized"] > 0
    assert report["normalized_facts_total"] > 0
    assert report["mandatory_floor_checks"]["checked"] > 0
    blob = json.dumps(report, ensure_ascii=False)
    assert "user_text" not in blob
    assert "original_response" not in blob
    assert not re.search(r"https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,}", blob)
    assert not re.search(r"(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}", blob)


def test_no_network_or_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def fail_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket.socket, "connect", fail_connect, raising=False)
    plan = build_reformat_plan({"facts": [{"name": "А", "location": "Б"}]}, "life")
    assert plan["classification"] == "normalized"
