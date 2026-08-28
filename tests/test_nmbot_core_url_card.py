from __future__ import annotations

import io

import pytest

from nmbot_core import CoreContractError, UrlCardError, extract_novostroy_url, fetch_card, project_url_card_for_prompt2, validate_source_url


SOURCE_URL = "https://www.novostroy-m.ru/kvartiry/studiya-v-zhk-example"


class Response:
    def __init__(self, body: bytes, final_url: str = SOURCE_URL) -> None:
        self.body, self.headers, self.status, self._final_url = io.BytesIO(body), {}, 200, final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, amount: int = -1) -> bytes:
        return self.body.read(amount)

    def geturl(self) -> str:
        return self._final_url


def test_extractor_uses_first_explicit_allowed_https_url_only() -> None:
    assert extract_novostroy_url(f"сначала {SOURCE_URL}), затем https://www.novostroy-m.ru/second") == SOURCE_URL
    assert extract_novostroy_url("http://www.novostroy-m.ru/old") is None
    assert extract_novostroy_url("https://example.com/a") is None


@pytest.mark.parametrize("url", ["https://evil.example/a", "https://user:pass@novostroy-m.ru/a", "https://novostroy-m.ru"])
def test_url_validation_rejects_unsafe_or_non_card_sources(url: str) -> None:
    with pytest.raises(UrlCardError):
        validate_source_url(url)


def test_fetch_rejects_unsafe_redirect_and_response_over_limit() -> None:
    with pytest.raises(UrlCardError) as redirected:
        fetch_card(SOURCE_URL, opener=lambda *_args, **_kwargs: Response(b"<html></html>", "https://evil.example/a"))
    assert redirected.value.code == "unsupported_url"
    with pytest.raises(UrlCardError) as oversized:
        fetch_card(SOURCE_URL, max_bytes=5, opener=lambda *_args, **_kwargs: Response(b"123456"))
    assert oversized.value.code == "response_too_large"


def test_card_projection_excludes_transport_metadata_and_requires_non_promotion_marker() -> None:
    raw = {
        "source_url": SOURCE_URL,
        "title": "raw page title",
        "parser": "internal",
        "card": {"complex_name": "ЖК А", "price_rub": 5_000_000, "secret": "no"},
        "missing": ["completion"],
        "derived": {"price_difference_is_not_a_promotion": True, "private": "no"},
    }
    assert project_url_card_for_prompt2(raw) == {
        "card": {"complex_name": "ЖК А", "price_rub": 5_000_000},
        "missing": ["completion"],
        "derived": {"price_difference_is_not_a_promotion": True},
    }
    raw["derived"] = {"price_difference_is_not_a_promotion": False}
    with pytest.raises(CoreContractError, match="invalid_url_card_derived"):
        project_url_card_for_prompt2(raw)
