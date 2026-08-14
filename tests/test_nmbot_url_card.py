from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_url_card.py"
spec = importlib.util.spec_from_file_location("nmbot_url_card", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


SOURCE_URL = "https://www.novostroy-m.ru/kvartiry/studiya-v-zhk-pehra-example"


HTML = """
<!doctype html>
<html>
<head>
  <title>Продажа студии 23.16 кв. м, ЖК «Гранель Пехра» – 2 этаж, усл. № 1330</title>
  <link rel="canonical" href="https://www.novostroy-m.ru/kvartiry/studiya-v-zhk-pehra-example">
  <script>var ignored = '4 000 000 ₽';</script>
</head>
<body>
  <h1>студия, 23.16 м2, 2 этаж</h1>
  <div>Срок сдачи 3 квартал 2026 года</div>
  <div>4 791 164 ₽</div>
  <div>Динамика цен</div>
  <div>12 августа 2026</div><div>4 791 164 ₽</div>
  <div>24 июля 2026</div><div>5 943 470 ₽</div>
  <div>206 872 ₽ /м 2</div>
  <div>В ипотеку от 22 462 ₽/мес.</div>
  <div>обновлено 14 августа</div>
  <h2>Описание студии</h2>
  <div>Застройщик</div><div>ГК «Гранель»</div>
  <div>Стадия строительства</div><div>монтаж нижних этажей</div>
  <div>Общая площадь</div><div>23.16 м2</div>
  <div>Этаж</div><div>2 / 16</div>
  <div>Отделка</div><div>нет</div>
  <h2>Расположение студии</h2>
  <div>Локация</div><div>Балашиха городской округ</div><div>Балашиха</div>
  <div>Метро</div><div>Щелковская</div><div>29 мин.</div><div>Новокосино</div><div>34 мин.</div>
  <div>Ж/Д-станция</div><div>Горенки</div><div>20 мин.</div><div>Балашиха</div><div>26 мин.</div>
  <div>Шоссе</div><div>Щёлковское 8 км от МКАД</div>
  <div>Адрес</div><div>ул. Трубецкая, мкр.39, влад.2Б, корп. 8, секция 12</div>
  <div>Способы покупки</div>
</body>
</html>
"""


def test_parse_html_card_extracts_grounded_apartment_fields() -> None:
    result = module.parse_html_card(HTML, SOURCE_URL)
    card = result["card"]

    assert result["schema_version"] == "nmbot.url_card.v1"
    assert card["object_type"] == "студия"
    assert card["complex_name"] == "Гранель Пехра"
    assert card["developer"] == "ГК «Гранель»"
    assert card["area_m2"] == 23.16
    assert card["floor"] == 2
    assert card["floors_total"] == 16
    assert card["price_rub"] == 4_791_164
    assert card["previous_price_rub"] == 5_943_470
    assert len(card["price_history"]) == 2
    assert card["price_per_m2_rub"] == 206_872
    assert card["mortgage_from_rub_per_month"] == 22_462
    assert card["completion"] == "3 квартал 2026 года"
    assert card["address"].endswith("корп. 8, секция 12")
    assert card["building"] == 8
    assert card["section"] == 12
    assert card["metro"] == [
        {"name": "Щелковская", "minutes": 29},
        {"name": "Новокосино", "minutes": 34},
    ]
    assert card["railway_station"] == [
        {"name": "Горенки", "minutes": 20},
        {"name": "Балашиха", "minutes": 26},
    ]
    assert "payment_terms" in result["missing"]
    assert result["derived"]["price_difference_rub"] == 1_152_306
    assert result["derived"]["price_difference_is_not_a_promotion"] is True
    assert result["page_updated"] == "14 августа"


def test_parser_ignores_script_text_and_does_not_invent_missing_terms() -> None:
    result = module.parse_html_card(HTML.replace("<div>Способы покупки</div>", ""), SOURCE_URL)
    card = result["card"]

    assert card["price_rub"] == 4_791_164
    assert card["payment_terms"] is None
    assert card["installment_terms"] is None
    assert card["special_offers"] is None
    assert "4 000 000" not in (result["title"] or "")


def test_validate_source_url_allows_only_public_novostroy_host() -> None:
    assert module.validate_source_url(SOURCE_URL + "#details") == SOURCE_URL

    with pytest.raises(module.UrlCardError) as exc_info:
        module.validate_source_url("https://example.com/apartment")
    assert exc_info.value.code == "unsupported_url"

    with pytest.raises(module.UrlCardError) as exc_info:
        module.validate_source_url("file:///tmp/apartment.html")
    assert exc_info.value.code == "unsupported_url"


def test_url_card_feature_is_on_for_branch_and_has_fail_closed_kill_switch() -> None:
    assert module.url_card_feature_enabled({}) is True
    assert module.url_card_feature_enabled({"NMBOT_V6_URL_CARD_ENABLED": "on"}) is True
    assert module.url_card_feature_enabled({"NMBOT_V6_URL_CARD_ENABLED": "0"}) is False
    assert module.url_card_feature_enabled({"NMBOT_V6_URL_CARD_ENABLED": "unexpected"}) is False


class _FakeResponse:
    status = 200

    def __init__(self, body: bytes, final_url: str = SOURCE_URL) -> None:
        self._body = io.BytesIO(body)
        self._final_url = final_url
        self.headers = {}

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def geturl(self) -> str:
        return self._final_url


def test_fetch_card_uses_injected_opener_and_rejects_external_redirect() -> None:
    calls: list[tuple[str, float]] = []

    def opener(req: object, *, timeout: float) -> _FakeResponse:
        calls.append((getattr(req, "full_url"), timeout))
        return _FakeResponse(HTML.encode("utf-8"))

    result = module.fetch_card(SOURCE_URL, timeout=3.0, opener=opener)
    assert result["card"]["complex_name"] == "Гранель Пехра"
    assert calls == [(SOURCE_URL, 3.0)]

    def redirecting_opener(_req: object, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(HTML.encode("utf-8"), final_url="https://example.com/redirect")

    with pytest.raises(module.UrlCardError) as exc_info:
        module.fetch_card(SOURCE_URL, opener=redirecting_opener)
    assert exc_info.value.code == "unsupported_url"


def test_fetch_card_enforces_response_size() -> None:
    def opener(_req: object, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(b"x" * 20)

    with pytest.raises(module.UrlCardError) as exc_info:
        module.fetch_card(SOURCE_URL, max_bytes=10, opener=opener)
    assert exc_info.value.code == "response_too_large"
