from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "chat_tester_bot.py"
SPEC = importlib.util.spec_from_file_location("chat_tester_bot_h041", MODULE_PATH)
assert SPEC and SPEC.loader
bot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bot
SPEC.loader.exec_module(bot)


def symbol_card(**overrides):
    data = {
        "idx": 1,
        "name": "ЖК Символ",
        "location": "Лефортово",
        "developer": "Дон-Строй",
        "class": "business",
        "rooms": "студии, 1, 2, 3, 4 комнаты",
        "apartment_types": "студии; 1-комнатные; 2-комнатные; 3-комнатные; 4-комнатные",
        "price": "17.74-59.85 млн",
        "price_range": "17.74-59.85 млн",
        "finishing": "есть варианты с отделкой и без отделки",
        "ready": "часть корпусов сдана, часть строится",
        "metro": "Римская — 6 минут пешком",
        "schools": "школа на территории; рядом школы",
        "kindergartens": "детские сады на территории",
        "parks": "парк Зелёная река; Лефортовский парк",
        "yards": "дворы без машин; детские площадки",
        "clinics": "аптеки и клиники рядом",
        "shops": "магазины и сервисы на первых этажах",
        "egrn_top_novos": {"contracts": 16383, "mortgages": 5247, "last_deal_date": "2026-06-30"},
        "counter_novos": {"count_ads": 381, "count_discounts": 4},
        "ads": "381 объявление",
    }
    data.update(overrides)
    return data


def h041_multicard_options(scenario: str) -> list[dict]:
    if scenario == "investment":
        return [
            symbol_card(name="ЖК Сделки"),
            symbol_card(name="ЖК Будущий", egrn_top_novos="", counter_novos="", ads="", ready="сдача 2028", price_min=12600000, price="", price_range=""),
            symbol_card(name="ЖК Витрина", egrn_top_novos="", counter_novos={"count_ads": 8, "count_discounts": 2}, ads="", apartment_types="", rooms=""),
        ]
    if scenario == "family":
        return [
            symbol_card(name="ЖК Школа"),
            symbol_card(name="ЖК Парк", schools="", kindergartens="", parks="парк рядом; зелёный двор", yards="двор без машин"),
            symbol_card(name="ЖК Готовый путь", schools="", kindergartens="", parks="", yards="", clinics="", shops="", ready="сдан", metro="Селигерская — 5 минут пешком"),
        ]
    if scenario == "rental":
        return [
            symbol_card(name="ЖК Готовый у метро", ready="сдан", metro="Римская — 6 минут пешком"),
            symbol_card(name="ЖК Компактный", ready="", metro="", apartment_types="студии; 1-комнатные", rooms="", price_min=9900000, price="", price_range=""),
            symbol_card(name="ЖК Подготовка", ready="", metro="", apartment_types="", rooms="", finishing="без отделки", counter_novos="", ads="", egrn_top_novos=""),
        ]
    if scenario == "self_use":
        return [
            symbol_card(name="ЖК Метро каждый день"),
            symbol_card(name="ЖК Готовый парк", metro="", ready="сдан", parks="парк рядом", schools="", kindergartens="", clinics="", shops="", infrastructure=""),
            symbol_card(name="ЖК Сервисы", metro="", ready="", parks="", yards="", schools="", kindergartens="", shops="магазины на первых этажах", clinics="клиника рядом", infrastructure="кафе и сервисы рядом"),
        ]
    raise AssertionError(scenario)


def test_enrichment_registry_drives_query_for_supported_scenarios() -> None:
    scenarios = {"investment", "family", "rental", "self_use"}
    assert scenarios <= set(bot.SCENARIO_ENRICHMENT_PROFILES)

    investment_query = bot._build_option_enrichment_query(symbol_card(), "investment")
    for token in ("egrn_top_novos", "counter_novos", "ads", "apartment_types", "stat_price"):
        assert token in investment_query
    assert "Не добавляй прогнозы доходности" in investment_query

    rental_query = bot._build_option_enrichment_query(symbol_card(), "rental")
    assert "ставки, спроса или окупаемости" in rental_query
    assert "компактные форматы" in rental_query


def test_scenario_outputs_use_present_facts_and_avoid_unsupported_claims() -> None:
    forbidden = (
        "доходност", "окупаем", "рост цен", "ликвид", "легко сдать", "быстро сдать",
        "высокий спрос", "хороший застройщик", "проверяем", "подтвержд", "mcp", "json", "internal",
        "сравнивала", "карточк", "это не прогноз", "важность простая", "роль:", "факты:",
    )
    expectations = {
        "investment": ("для инвестиций", "договоры — 16 383", "активность покупателей"),
        "family": ("для семейной жизни", "На территории комплекса есть школа", "меньше времени"),
        "rental": ("для покупки под сдачу", "готовый вариант с удобной дорогой", "без ожидания стройки"),
        "self_use": ("для комфортной жизни", "Римская — 6 минут пешком", "экономить время"),
    }
    for scenario, required in expectations.items():
        text = bot._render_stage_first_list([symbol_card()], scenario)
        lowered = text.lower().replace("ё", "е")
        assert text.count("?") == 1
        assert text.rstrip().endswith("?")
        assert text.rstrip().endswith("Рассказать подробнее о доступных квартирах и актуальных ценах в этом ЖК?")
        assert "Подобрать в этом ЖК" not in text
        for phrase in required:
            assert phrase in text
        assert "Важность простая:" not in text
        assert not any(word in lowered for word in forbidden), text


def test_investment_intro_spacing_and_price_sentence_are_client_ready() -> None:
    intro = "Подобрала три варианта для инвестиций. В первую очередь обращала внимание на цену входа."
    assert bot._format_paragraph_spacing(intro).startswith("Подобрала три варианта для инвестиций.")
    assert "варианта\n\nдля инвестиций" not in bot._format_paragraph_spacing(intro).lower()

    benefit = bot._stage_option_benefit(
        {"name": "ЖК Цена", "location": "Москва", "price": "от 16,10 до 41,90 млн рублей"},
        "investment",
        {"role_invest_entry"},
    )
    assert benefit.startswith("Цены от 16,10 до 41,90 млн рублей показывает порог входа")


def test_multicard_renderer_uses_distinct_fact_to_benefit_utps() -> None:
    options = h041_multicard_options("investment")
    text = bot._render_stage_first_list(options, "investment")

    assert "ЖК Сделки" in text and "активность покупателей" in text
    assert "ЖК Будущий" in text and "сдача запланирована на 2028 год" in text and "нужно ждать сдачу" in text
    assert "ЖК Витрина" in text and "текущим выбором квартир" in text and "доступный выбор" in text
    assert text.count("активность покупателей") == 1
    assert text.count("доступный выбор") == 1
    assert text.count("?") == 1
    assert text.rstrip().endswith("Какой из этих ЖК вас заинтересовал больше всего — рассказать о нём подробнее?")


def test_multicard_scenarios_have_max_three_distinct_safe_roles_and_one_question() -> None:
    forbidden = (
        "доходност", "окупаем", "рост цен", "ликвид", "легко сдать", "быстро сдать",
        "высокий спрос", "хороший застройщик", "mcp", "json", "internal",
    )
    expected_roles = {
        "investment": ("фактической историей сделок", "горизонтом строительства", "текущим выбором квартир"),
        "family": ("семейная инфраструктура", "зелёная среда", "готовность и удобная ежедневная дорога"),
        "rental": ("готовый вариант с удобной дорогой", "компактный формат", "заранее учесть подготовку"),
        "self_use": ("городская повседневность", "прогулочными местами", "квартальная инфраструктура"),
    }
    for scenario, roles in expected_roles.items():
        text = bot._render_stage_first_list(h041_multicard_options(scenario), scenario)
        lowered = text.lower().replace("ё", "е")
        card_count = len(re.findall(r"(?m)^\d+\. ", text))
        assert card_count <= 3
        assert card_count == 3
        assert text.count("?") == 1
        assert text.rstrip().endswith("?")
        assert not any(word in lowered for word in forbidden), text
        for role in roles:
            assert role in text


def test_ready_and_construction_wording_is_evidence_backed() -> None:
    text = bot._render_stage_first_list(h041_multicard_options("investment"), "investment")
    assert "ЖК Будущий" in text
    assert "сдача запланирована на 2028 год" in text
    assert "нужно ждать сдачу" in text

    no_ready_text = bot._render_stage_first_list([symbol_card(name="ЖК Без срока", ready="")], "self_use")
    assert "дом уже сдан" not in no_ready_text
    assert "сдача запланирована" not in no_ready_text


def test_sparse_data_uses_neutral_role_without_unsupported_tradeoff() -> None:
    text = bot._render_stage_first_list([{"name": "ЖК Сухая карточка", "location": "Москва"}], "investment")
    lowered = text.lower().replace("ё", "е")
    assert "вариант для сравнения по доступным фактам" in text
    assert "доход" not in lowered
    assert "сдача запланирована" not in text
    assert "активность покупателей" not in text
    assert text.count("?") == 1 and text.rstrip().endswith("?")


def test_readable_dialogue_fixture_matches_deterministic_renderer() -> None:
    rendered = "\n".join(
        f"## {scenario}\n{bot._render_stage_first_list(h041_multicard_options(scenario), scenario)}\n"
        for scenario in ("investment", "family", "rental", "self_use")
    ).rstrip()
    fixture = (ROOT / "tests" / "fixtures" / "h041_dialogue_outputs.txt").read_text(encoding="utf-8").rstrip()
    assert rendered == fixture


class FakeEnrichmentClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def enrich_option_search(self, query: str, timeout: int):
        self.calls.append(query)
        if "Фейл" in query:
            raise RuntimeError("boom")
        name = "ЖК Символ" if "Символ" in query else "ЖК Третий"
        await asyncio.sleep(0.01)
        return {"facts": [{"name": name, "metro": "Римская — 6 минут пешком", "counter_novos": {"count_ads": 381}}]}, {"ok": True}


def test_top3_enrichment_is_bounded_ordered_cached_and_falls_back_per_item() -> None:
    async def run():
        options = [
            symbol_card(name="ЖК Символ", metro=""),
            symbol_card(name="ЖК Фейл", metro=""),
            symbol_card(name="ЖК Третий", metro=""),
            symbol_card(name="ЖК Четвёртый", metro=""),
        ]
        state: dict = {}
        client = FakeEnrichmentClient()
        enriched, meta = await bot._enrich_top_options_for_first_list(
            client,
            state,
            options,
            "investment",
            total_timeout=0.5,
        )
        assert [item["name"] for item in enriched] == ["ЖК Символ", "ЖК Фейл", "ЖК Третий", "ЖК Четвёртый"]
        assert enriched[0]["metro"] == "Римская — 6 минут пешком"
        assert enriched[1]["metro"] == ""
        assert enriched[2]["counter_novos"] == {"count_ads": 381}
        assert enriched[3]["metro"] == ""
        assert meta["count"] == 3
        assert meta["applied_count"] == 2
        assert len(client.calls) == 3
        assert state["enriched_options"]

        cached_client = FakeEnrichmentClient()
        enriched_again, meta_again = await bot._enrich_top_options_for_first_list(
            cached_client,
            state,
            options[:1],
            "investment",
            total_timeout=0.5,
        )
        assert enriched_again[0]["metro"] == "Римская — 6 минут пешком"
        assert meta_again["items"][0]["source"] == "cache"
        assert cached_client.calls == []

    asyncio.run(run())
