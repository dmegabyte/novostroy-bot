from __future__ import annotations

import asyncio
import json

from nmbot_v6.simple_gateway import SimpleGatewayResult
from nmbot_v6.simple_runtime import URL_CARD_FAILURE_TEXT, SimpleRuntime
from nmbot_v6.simple_state import SimpleState
from scripts.nmbot_url_card import extract_novostroy_url
from scripts.nmbot_v6_simple_adapter import run_v6_simple_turn


SOURCE_URL = "https://www.novostroy-m.ru/kvartiry/studiya-v-zhk-pehra-example"


URL_CARD = {
    "schema_version": "nmbot.url_card.v1",
    "parser": "novostroy_m_apartment_v1",
    "source_url": SOURCE_URL,
    "canonical_url": SOURCE_URL,
    "title": "Продажа студии",
    "card": {
        "object_type": "студия",
        "complex_name": "Гранель Пехра",
        "developer": "ГК «Гранель»",
        "area_m2": 23.16,
        "floor": 2,
        "floors_total": 16,
        "price_rub": 4_791_164,
        "previous_price_rub": 5_943_470,
        "price_history": [
            {"date": "12 августа 2026", "price_rub": 4_791_164},
            {"date": "24 июля 2026", "price_rub": 5_943_470},
        ],
        "price_per_m2_rub": 206_872,
        "mortgage_from_rub_per_month": 22_462,
        "completion": "3 квартал 2026 года",
        "construction_stage": "монтаж нижних этажей",
        "finishing": "нет",
        "location": "Балашиха городской округ",
        "address": "ул. Трубецкая, мкр.39, влад.2Б, корп. 8, секция 12",
        "building": 8,
        "section": 12,
        "metro": [{"name": "Щелковская", "minutes": 29}],
        "railway_station": [{"name": "Горенки", "minutes": 20}],
        "highway": "Щёлковское 8 км от МКАД",
        "listing_number": 1330,
        "payment_terms": None,
        "installment_terms": None,
        "special_offers": None,
    },
    "missing": ["payment_terms", "installment_terms", "special_offers"],
    "derived": {
        "price_difference_rub": 1_152_306,
        "price_difference_is_not_a_promotion": True,
    },
    "page_updated": "14 августа",
}


class NoPhoneBackend:
    def parse(self, candidate, region):
        raise ValueError("not a phone")

    def is_possible_number(self, parsed):
        return False

    def is_valid_number(self, parsed):
        return False

    def format_e164(self, parsed):
        return ""


class Port:
    def __init__(self, output):
        self.output = output
        self.calls = []

    async def run(self, payload, *, repair=False):
        self.calls.append((payload, repair))
        if isinstance(self.output, Exception):
            raise self.output
        return SimpleGatewayResult(self.output, f"prompt2-{len(self.calls)}")


class NeverPort:
    def __init__(self):
        self.calls = []

    async def run(self, payload, *, repair=False):
        self.calls.append((payload, repair))
        raise AssertionError("Prompt 1 must be bypassed")


class Fetcher:
    def __init__(self, value=URL_CARD):
        self.value = value
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def _reply_port():
    return Port({"action": "reply", "response": "Карточка получена.", "final_question": ""})


def test_extractor_accepts_only_first_allowed_https_url():
    message = f"Посмотрите {SOURCE_URL}), затем https://www.novostroy-m.ru/kvartiry/second"
    assert extract_novostroy_url(message) == SOURCE_URL
    assert extract_novostroy_url("https://example.com/apartment") is None
    assert extract_novostroy_url("http://www.novostroy-m.ru/kvartiry/old") is None


def test_url_card_branch_skips_prompt1_and_sends_bounded_card_to_prompt2():
    p1, p2, fetcher = NeverPort(), _reply_port(), Fetcher()
    runtime = SimpleRuntime(
        p1,
        p2,
        phone_backend=NoPhoneBackend(),
        url_card_fetcher=fetcher,
        url_card_extractor=extract_novostroy_url,
    )

    outcome = asyncio.run(runtime.run(f"Что есть по ссылке {SOURCE_URL}?", SimpleState()))

    assert outcome.status == "completed"
    assert outcome.model_calls == 1
    assert outcome.url_card_status == "accepted"
    assert not p1.calls
    assert fetcher.calls == [SOURCE_URL]
    assert len(p2.calls) == 1 and p2.calls[0][1] is False
    payload = p2.calls[0][0]
    assert payload["current_message"].endswith("?")
    assert payload["property_material"]["facts"] == []
    assert payload["property_material"]["near"] == []
    assert payload["property_material"]["params"] == {}
    url_card = payload["property_material"]["url_card"]
    assert url_card["card"]["complex_name"] == "Гранель Пехра"
    assert url_card["derived"]["price_difference_is_not_a_promotion"] is True
    assert "source_url" not in url_card and "canonical_url" not in url_card
    assert "title" not in url_card and "parser" not in url_card
    assert payload["missing"] == URL_CARD["missing"]
    assert outcome.state.history[-1]["text"] == "Карточка получена."


def test_url_card_fetch_failure_does_not_fall_through_to_prompt1_or_prompt2():
    p1, p2, fetcher = NeverPort(), NeverPort(), Fetcher(OSError("network"))
    runtime = SimpleRuntime(
        p1,
        p2,
        phone_backend=NoPhoneBackend(),
        url_card_fetcher=fetcher,
        url_card_extractor=extract_novostroy_url,
    )

    outcome = asyncio.run(runtime.run(SOURCE_URL, SimpleState()))

    assert outcome.status == "safe_failure"
    assert outcome.failure_stage == "url_card"
    assert outcome.url_card_status == "fetch_failed"
    assert outcome.error_code == "url_card_fetch_failed"
    assert outcome.text == URL_CARD_FAILURE_TEXT
    assert not p1.calls and not p2.calls


def test_external_url_keeps_normal_prompt1_route():
    class ReplyPort:
        def __init__(self, value):
            self.value = value
            self.calls = []

        async def run(self, payload, *, repair=False):
            self.calls.append((payload, repair))
            return SimpleGatewayResult(self.value, "normal-prompt")

    p1 = ReplyPort({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}, "ambiguity": None})
    p2 = ReplyPort({"action": "reply", "response": "Обычный маршрут.", "final_question": ""})
    fetcher = Fetcher()
    runtime = SimpleRuntime(
        p1,
        p2,
        phone_backend=NoPhoneBackend(),
        url_card_fetcher=fetcher,
        url_card_extractor=extract_novostroy_url,
    )

    outcome = asyncio.run(runtime.run("https://example.com/apartment", SimpleState()))

    assert outcome.status == "completed"
    assert len(p1.calls) == 1 and len(p2.calls) == 1
    assert not fetcher.calls


def test_adapter_trace_marks_direct_prompt2_route_without_changing_five_stages():
    class Store:
        def __init__(self):
            self.value, self.saves = {}, []

        async def get(self, key):
            return self.value

        async def save(self, key, value):
            self.value = value
            self.saves.append(value)

    p1, p2, fetcher = NeverPort(), _reply_port(), Fetcher()
    app = {
        "state_store": Store(),
        "v6_simple_prompt1_port": p1,
        "v6_simple_prompt2_port": p2,
        "v6_url_card_fetcher": fetcher,
        "v6_url_card_extractor": extract_novostroy_url,
    }

    result = asyncio.run(run_v6_simple_turn(app, user_id="s", message=SOURCE_URL, channel="jivo"))

    trace = result["meta"]["v6_trace"]
    stages = {item["stage"]: item["status"] for item in trace["stages"]}
    assert stages == {
        "prompt1": "not_called",
        "mcp": "not_called",
        "prompt2": "accepted",
        "state": "accepted",
        "bot_message": "prepared",
    }
    assert trace["url_card"] == {"status": "accepted", "route": "prompt2_direct"}
    assert result["meta"]["model_calls"] == 1
    assert json.dumps(result["meta"], ensure_ascii=False).count(SOURCE_URL) == 0
