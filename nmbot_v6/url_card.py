#!/usr/bin/env python3
"""Fetch a novostroy-m.ru apartment URL and build a grounded card.

This is a local, explicit URL tool.  It is intentionally separate from the
Jivo runtime: callers can inspect the structured result before wiring it into
any dialog flow.  The parser uses only the public HTML response and never
inventes values that are absent from the page.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
from html.parser import HTMLParser
from typing import Any, Callable, Mapping
from urllib import error, parse, request


ALLOWED_HOSTS = frozenset({"novostroy-m.ru", "www.novostroy-m.ru"})
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_BYTES = 2_000_000
SCHEMA_VERSION = "nmbot.url_card.v1"
# The public site serves a short 403 response to a tool-identifying user agent.
# A normal browser identity is needed for the explicitly requested public page;
# this is not a bypass for authentication or robots controls.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36 nmbot-url-card/1.0"
URL_CARD_FEATURE_ENV = "NMBOT_V6_URL_CARD_ENABLED"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def url_card_feature_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Enable this isolated V6 branch by default, with an explicit kill switch.

    Deploying the branch is the opt-in action.  A present environment value
    must be an accepted true token; false or malformed values fail closed.
    """
    values = os.environ if environ is None else environ
    raw = str(values.get(URL_CARD_FEATURE_ENV, "") or "").strip().lower()
    return True if not raw else raw in _TRUE_VALUES

_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "p",
        "section",
        "tr",
    }
)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})
_MONTHS = (
    "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
)
_MONEY_RE = re.compile(
    r"(?<!\d)(?:\d[\d\s\u00a0]{2,}\d|\d{4,})\s*(?:₽|руб(?:\.|лей)?)",
    re.IGNORECASE,
)
_DATE_MONEY_RE = re.compile(
    rf"(?P<date>\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}})\s+"
    r"(?P<amount>(?:\d[\d\s\u00a0]{2,}\d|\d{4,}))\s*(?:₽|руб(?:\.|лей)?)",
    re.IGNORECASE,
)
_MORTGAGE_RE = re.compile(
    r"ипотек\w*\s+от\s+(?P<amount>(?:\d[\d\s\u00a0]{2,}\d|\d{4,}))\s*(?:₽|руб(?:\.|лей)?)",
    re.IGNORECASE,
)
_PRICE_PER_M2_RE = re.compile(
    r"(?P<amount>(?:\d[\d\s\u00a0]{2,}\d|\d{4,}))\s*(?:₽|руб(?:\.|лей)?)\s*/\s*м\s*(?:2|²)",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(
    r"(?P<object_type>студия|[1-5]\s*-\s*комн\.?\s*(?:квартира)?|[1-5]\s*комнатная\s+квартира)"
    r"\s*,\s*(?P<area>[\d\s]+(?:[.,]\d+)?)\s*(?:м\s*(?:2|²)|кв\.?\s*м)"
    r"\s*,\s*(?P<floor>\d+)\s*этаж",
    re.IGNORECASE,
)
_SOURCE_URL_RE = re.compile(
    r"https://(?:www\.)?novostroy-m\.ru/[^\s<>'\"`]+",
    re.IGNORECASE,
)
_URL_TRAILING_CHARS = ".,;:!?)]}"


class UrlCardError(RuntimeError):
    """Safe, user-facing error from URL validation, fetch, or parsing."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _HtmlTextCollector(HTMLParser):
    """Collect visible block text and page metadata without external deps."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.title = ""
        self.canonical_url = ""
        self._current: list[str] = []
        self._title_parts: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def _flush(self) -> None:
        value = _normalise_text(" ".join(self._current))
        if value:
            self.lines.append(value)
        self._current.clear()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag == "title":
            self._in_title = True
        if tag == "link":
            attrs_map = {str(key).lower(): value or "" for key, value in attrs}
            rel = {part.strip().lower() for part in attrs_map.get("rel", "").split()}
            if "canonical" in rel and attrs_map.get("href"):
                self.canonical_url = attrs_map["href"].strip()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self.title = _normalise_text(" ".join(self._title_parts))
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        self._current.append(data)

    def finish(self) -> None:
        self._flush()
        if not self.title:
            self.title = _normalise_text(" ".join(self._title_parts))


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(value).replace("\u00a0", " ")).strip()


def _normalise_label(value: str) -> str:
    return _normalise_text(value).rstrip(":").strip().casefold()


def _parse_amount(value: str) -> int:
    digits = re.sub(r"\D", "", value)
    if not digits:
        raise ValueError("empty amount")
    return int(digits)


def _parse_decimal(value: str) -> float:
    return float(value.replace(" ", "").replace(",", "."))


def _unique(values: list[int]) -> list[int]:
    return list(dict.fromkeys(values))


def validate_source_url(value: str) -> str:
    """Validate and normalise a public novostroy-m.ru URL.

    The host allow-list protects the reusable fetcher from becoming an open
    SSRF primitive when it is later called from a chat handler.
    """

    raw = str(value or "").strip()
    parsed = parse.urlsplit(raw)
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme.lower() not in {"http", "https"} or host not in ALLOWED_HOSTS:
        raise UrlCardError("unsupported_url", "разрешены только ссылки novostroy-m.ru по HTTP/HTTPS")
    if parsed.username or parsed.password:
        raise UrlCardError("unsafe_url", "ссылка с логином или паролем не поддерживается")
    if not parsed.path:
        raise UrlCardError("unsupported_url", "у ссылки отсутствует путь карточки")
    return parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, ""))


def extract_novostroy_url(message: str) -> str | None:
    """Return the first explicit HTTPS novostroy-m.ru URL in a message.

    The caller owns the decision to invoke the URL-card branch. This helper
    only recognizes the public host allow-list and deliberately does not scan
    dialogue history or inspect arbitrary URLs.
    """

    for match in _SOURCE_URL_RE.finditer(str(message or "")):
        candidate = match.group(0).rstrip(_URL_TRAILING_CHARS)
        try:
            return validate_source_url(candidate)
        except UrlCardError:
            continue
    return None


def _response_charset(response: Any) -> str:
    headers = getattr(response, "headers", None)
    getter = getattr(headers, "get_content_charset", None)
    if callable(getter):
        try:
            charset = getter()
        except (LookupError, TypeError):
            charset = None
        if charset:
            return str(charset)
    return "utf-8"


def _read_limited(response: Any, max_bytes: int) -> bytes:
    if max_bytes < 1:
        raise UrlCardError("invalid_limit", "лимит ответа должен быть положительным")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UrlCardError("response_too_large", "HTML-страница превышает допустимый размер")
        chunks.append(chunk)
    return b"".join(chunks)


def _read_html(
    source_url: str,
    *,
    timeout: float,
    max_bytes: int,
    opener: Callable[..., Any],
) -> tuple[str, str]:
    target = validate_source_url(source_url)
    if not 0.1 <= float(timeout) <= 120.0:
        raise UrlCardError("invalid_timeout", "таймаут должен быть от 0.1 до 120 секунд")
    req = request.Request(
        target,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with opener(req, timeout=float(timeout)) as response:
            status = getattr(response, "status", None)
            if isinstance(status, int) and status >= 400:
                raise UrlCardError("http_error", f"источник вернул HTTP {status}")
            final_url = validate_source_url(str(getattr(response, "geturl", lambda: target)()))
            raw = _read_limited(response, max_bytes)
            text = raw.decode(_response_charset(response), errors="replace")
            return text, final_url
    except UrlCardError:
        raise
    except error.HTTPError as exc:
        raise UrlCardError("http_error", f"источник вернул HTTP {exc.code}") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise UrlCardError("fetch_failed", "не удалось получить HTML-страницу источника") from exc


def _find_label_index(lines: list[str], label: str, *, start: int = 0) -> int:
    target = _normalise_label(label)
    for index in range(max(0, start), len(lines)):
        if _normalise_label(lines[index]) == target:
            return index
    return -1


def _line_value(line: str, label: str) -> str | None:
    match = re.match(rf"^{re.escape(label)}\s*[:\-]?\s*(.+)$", line, re.IGNORECASE)
    if not match:
        return None
    value = _normalise_text(match.group(1))
    return value or None


def _value_after_label(
    lines: list[str],
    label: str,
    *,
    start: int = 0,
    stop_labels: tuple[str, ...] = (),
) -> str | None:
    index = _find_label_index(lines, label, start=start)
    if index < 0:
        prefix = _normalise_label(label)
        for candidate in lines[start:]:
            value = _line_value(candidate, label)
            if value:
                return value
            if _normalise_label(candidate).startswith(prefix + " "):
                return candidate[len(label) :].strip()
        return None
    stop = {_normalise_label(item) for item in stop_labels}
    for candidate in lines[index + 1 :]:
        normalised = _normalise_label(candidate)
        if not normalised:
            continue
        if normalised in stop:
            return None
        return candidate
    return None


def _section_start(lines: list[str], heading: str) -> int:
    return _find_label_index(lines, heading)


def _transport_pairs(
    lines: list[str],
    label: str,
    *,
    start: int,
    stop_labels: tuple[str, ...],
) -> list[dict[str, Any]]:
    index = _find_label_index(lines, label, start=start)
    if index < 0:
        return []
    stop = {_normalise_label(item) for item in stop_labels}
    pairs: list[dict[str, Any]] = []
    pending_name: str | None = None
    for candidate in lines[index + 1 :]:
        normalised = _normalise_label(candidate)
        if normalised in stop:
            break
        if not normalised:
            continue
        match = re.fullmatch(r"(.+?)\s+(\d+)\s*мин\.?", candidate, re.IGNORECASE)
        if match:
            name = _normalise_text(match.group(1))
            minutes = int(match.group(2))
            if name:
                pairs.append({"name": name, "minutes": minutes})
            pending_name = None
            continue
        minute_only = re.fullmatch(r"(\d+)\s*мин\.?", candidate, re.IGNORECASE)
        if minute_only and pending_name:
            pairs.append({"name": pending_name, "minutes": int(minute_only.group(1))})
            pending_name = None
            continue
        pending_name = candidate
    return pairs


def _money_values(text: str) -> list[int]:
    return _unique([_parse_amount(match.group(0)) for match in _MONEY_RE.finditer(text)])


def _price_history(text: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for match in _DATE_MONEY_RE.finditer(text):
        result.append(
            {
                "date": _normalise_text(match.group("date")),
                "price_rub": _parse_amount(match.group("amount")),
            }
        )
    return result


def parse_html_card(html: str, source_url: str) -> dict[str, Any]:
    """Parse one apartment page into a stable, JSON-compatible card."""

    validated_url = validate_source_url(source_url)
    collector = _HtmlTextCollector()
    try:
        collector.feed(str(html))
        collector.close()
    except Exception as exc:  # HTMLParser may fail on malformed external HTML.
        raise UrlCardError("malformed_html", "не удалось разобрать HTML-страницу") from exc
    collector.finish()

    lines = collector.lines
    # The page title contains stable identity fields (the ЖК name and often
    # the listing number), while visible body lines contain the detailed
    # attributes.  Keep both sources in the searchable text, but continue to
    # exclude script/style content through the collector.
    text = _normalise_text(" ".join(part for part in (collector.title, *lines) if part))
    header = _HEADER_RE.search(text)
    complex_match = re.search(r"ЖК\s*[«\"]([^»\"]+)[»\"]", text, re.IGNORECASE)
    # Similar apartments and their own price graphs are rendered below the
    # current card.  Keep the price history scoped to the first card section;
    # otherwise a valid parser would silently mix other listings into this
    # apartment's history.
    price_section_end = len(lines)
    for heading in ("Описание студии", "Описание квартиры", "Похожие квартиры", "Еще квартиры"):
        heading_index = _section_start(lines, heading)
        if heading_index >= 0:
            price_section_end = min(price_section_end, heading_index)
    price_text = _normalise_text(" ".join(lines[:price_section_end]))
    history = _price_history(price_text)
    all_prices = _money_values(price_text)
    current_price = history[0]["price_rub"] if history else (all_prices[0] if all_prices else None)
    previous_price = history[1]["price_rub"] if len(history) > 1 else None

    location_start = _section_start(lines, "Расположение студии")
    if location_start < 0:
        location_start = _section_start(lines, "Расположение квартиры")
    address = _value_after_label(lines, "Адрес", start=max(0, location_start))
    location = _value_after_label(lines, "Локация", start=max(0, location_start))
    developer = _value_after_label(lines, "Застройщик")
    completion = _value_after_label(lines, "Срок сдачи")
    if not completion:
        completion = _value_after_label(lines, "Срок ГК")
    construction_stage = _value_after_label(lines, "Стадия строительства")
    finishing = _value_after_label(lines, "Отделка")
    # Floor is part of the apartment description, which appears before the
    # location section on the source page.  Do not scope this lookup to the
    # location heading or the total-floor value is lost.
    floor_value = _value_after_label(lines, "Этаж")
    floors_total: int | None = None
    floor_from_label: int | None = None
    if floor_value:
        floor_match = re.search(r"(\d+)\s*/\s*(\d+)", floor_value)
        if floor_match:
            floor_from_label = int(floor_match.group(1))
            floors_total = int(floor_match.group(2))

    address_match = re.search(r"корп\.?\s*(\d+)", address or "", re.IGNORECASE)
    section_match = re.search(r"секц(?:ия|\.?)\s*(\d+)", address or "", re.IGNORECASE)
    listing_match = re.search(r"(?:усл\.\s*)?№\s*(\d+)", collector.title or text, re.IGNORECASE)
    mortgage_match = _MORTGAGE_RE.search(text)
    price_per_m2_match = _PRICE_PER_M2_RE.search(text)
    updated_match = re.search(
        rf"обновлено\s+(?P<date>\d{{1,2}}\s+(?:{_MONTHS})(?:\s+\d{{4}})?)",
        text,
        re.IGNORECASE,
    )

    metro = _transport_pairs(
        lines,
        "Метро",
        start=max(0, location_start),
        stop_labels=("Ж/Д-станция", "Шоссе", "Дом", "Адрес"),
    )
    railway = _transport_pairs(
        lines,
        "Ж/Д-станция",
        start=max(0, location_start),
        stop_labels=("Шоссе", "Дом", "Адрес"),
    )
    highway = _value_after_label(lines, "Шоссе", start=max(0, location_start), stop_labels=("Дом", "Адрес"))

    area_m2: float | None = None
    floor: int | None = None
    object_type: str | None = None
    if header:
        object_type = _normalise_text(header.group("object_type")).lower()
        area_m2 = _parse_decimal(header.group("area"))
        floor = int(header.group("floor"))
    if area_m2 is None:
        area_value = _value_after_label(lines, "Общая площадь")
        if area_value:
            area_match = re.search(r"[\d\s]+(?:[.,]\d+)?", area_value)
            if area_match:
                area_m2 = _parse_decimal(area_match.group(0))
    if floor is None:
        floor = floor_from_label
    if floors_total is None and header and floor_value:
        total_match = re.search(r"/\s*(\d+)", floor_value)
        if total_match:
            floors_total = int(total_match.group(1))

    canonical_url = validated_url
    if collector.canonical_url:
        try:
            canonical_url = validate_source_url(parse.urljoin(validated_url, collector.canonical_url))
        except UrlCardError:
            canonical_url = validated_url

    card: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "parser": "novostroy_m_apartment_v1",
        "source_url": validated_url,
        "canonical_url": canonical_url,
        "title": collector.title or None,
        "card": {
            "object_type": object_type,
            "complex_name": complex_match.group(1).strip() if complex_match else None,
            "developer": developer,
            "area_m2": area_m2,
            "floor": floor,
            "floors_total": floors_total,
            "price_rub": current_price,
            "previous_price_rub": previous_price,
            "price_history": history,
            "price_per_m2_rub": _parse_amount(price_per_m2_match.group("amount")) if price_per_m2_match else None,
            "mortgage_from_rub_per_month": _parse_amount(mortgage_match.group("amount")) if mortgage_match else None,
            "completion": completion,
            "construction_stage": construction_stage,
            "finishing": finishing,
            "location": location,
            "address": address,
            "building": int(address_match.group(1)) if address_match else None,
            "section": int(section_match.group(1)) if section_match else None,
            "metro": metro,
            "railway_station": railway,
            "highway": highway,
            "listing_number": int(listing_match.group(1)) if listing_match else None,
            "payment_terms": None,
            "installment_terms": None,
            "special_offers": None,
        },
    }
    required_fields = {
        "complex_name": card["card"]["complex_name"],
        "area_m2": area_m2,
        "floor": floor,
        "price_rub": current_price,
        "completion": completion,
        "address": address,
    }
    optional_fields = {
        "payment_terms": card["card"]["payment_terms"],
        "installment_terms": card["card"]["installment_terms"],
        "special_offers": card["card"]["special_offers"],
    }
    card["missing"] = [name for name, value in {**required_fields, **optional_fields}.items() if value in (None, "", [])]
    card["derived"] = {
        "price_difference_rub": (
            previous_price - current_price
            if isinstance(previous_price, int) and isinstance(current_price, int)
            else None
        ),
        "price_difference_is_not_a_promotion": True,
    }
    if updated_match:
        card["page_updated"] = _normalise_text(updated_match.group("date"))
    return card


def fetch_card(
    source_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    opener: Callable[..., Any] = request.urlopen,
) -> dict[str, Any]:
    """Fetch and parse a public apartment page."""

    html, final_url = _read_html(
        source_url,
        timeout=timeout,
        max_bytes=max_bytes,
        opener=opener,
    )
    return parse_html_card(html, final_url)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Получить структурированную карточку квартиры по URL")
    parser.add_argument("url", help="ссылка на карточку novostroy-m.ru")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--compact", action="store_true", help="вывести JSON в одну строку")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = fetch_card(args.url, timeout=args.timeout, max_bytes=args.max_bytes)
    except UrlCardError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": exc.message}}, ensure_ascii=False), file=sys.stderr)
        return 2
    indent = None if args.compact else 2
    print(json.dumps(result, ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
