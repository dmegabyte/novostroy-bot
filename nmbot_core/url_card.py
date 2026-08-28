"""Safe, bounded extraction of public novostroy-m.ru apartment cards.

This module is deliberately independent from the dialog runtime.  It validates
the source and any redirect before fetching, bounds the response, and returns
only values observed in public HTML.  Runtime integration is a separate owner.
"""

from __future__ import annotations

import html as html_lib
import re
from html.parser import HTMLParser
from typing import Any, Callable
from urllib import error, parse, request


ALLOWED_HOSTS = frozenset({"novostroy-m.ru", "www.novostroy-m.ru"})
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_BYTES = 2_000_000
SCHEMA_VERSION = "nmbot.url_card.v1"
USER_AGENT = "Mozilla/5.0 (compatible; nmbot-url-card/1.0)"
_URL_RE = re.compile(r"https://(?:www\.)?novostroy-m\.ru/[^\s<>'\"`]+", re.IGNORECASE)
_TRAILING_URL_CHARS = ".,;:!?)]}"
_MONEY_RE = re.compile(r"(?<!\d)(?:\d[\d\s\u00a0]{2,}\d|\d{4,})\s*(?:₽|руб(?:\.|лей)?)", re.IGNORECASE)
_HEADER_RE = re.compile(
    r"(?P<object_type>студия|[1-5]\s*-\s*комн\.?\s*(?:квартира)?|[1-5]\s*комнатная\s+квартира)"
    r"\s*,\s*(?P<area>[\d\s]+(?:[.,]\d+)?)\s*(?:м\s*(?:2|²)|кв\.?\s*м)"
    r"\s*,\s*(?P<floor>\d+)\s*этаж",
    re.IGNORECASE,
)


class UrlCardError(RuntimeError):
    """A safe failure code from URL validation, fetch, or HTML parsing."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_source_url(value: str) -> str:
    """Return a normalised public apartment URL or fail closed."""

    parsed = parse.urlsplit(str(value or "").strip())
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme.lower() not in {"http", "https"} or host not in ALLOWED_HOSTS:
        raise UrlCardError("unsupported_url", "разрешены только ссылки novostroy-m.ru по HTTP/HTTPS")
    if parsed.username or parsed.password:
        raise UrlCardError("unsafe_url", "ссылка с логином или паролем не поддерживается")
    if not parsed.path:
        raise UrlCardError("unsupported_url", "у ссылки отсутствует путь карточки")
    return parse.urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, ""))


def extract_novostroy_url(message: str) -> str | None:
    """Return the first explicit HTTPS allowlisted URL in this message only."""

    for match in _URL_RE.finditer(str(message or "")):
        candidate = match.group(0).rstrip(_TRAILING_URL_CHARS)
        try:
            normalised = validate_source_url(candidate)
        except UrlCardError:
            continue
        if parse.urlsplit(normalised).scheme == "https":
            return normalised
    return None


def _read_limited(response: Any, maximum: int) -> bytes:
    if maximum < 1:
        raise UrlCardError("invalid_limit", "лимит ответа должен быть положительным")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, maximum - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise UrlCardError("response_too_large", "HTML-страница превышает допустимый размер")
        chunks.append(chunk)
    return b"".join(chunks)


def _charset(response: Any) -> str:
    headers = getattr(response, "headers", None)
    getter = getattr(headers, "get_content_charset", None)
    if callable(getter):
        try:
            value = getter()
        except (LookupError, TypeError):
            value = None
        if value:
            return str(value)
    return "utf-8"


def _read_html(source_url: str, *, timeout: float, maximum: int, opener: Callable[..., Any]) -> tuple[str, str]:
    target = validate_source_url(source_url)
    if not 0.1 <= float(timeout) <= 120:
        raise UrlCardError("invalid_timeout", "таймаут должен быть от 0.1 до 120 секунд")
    req = request.Request(target, headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": USER_AGENT}, method="GET")
    try:
        with opener(req, timeout=float(timeout)) as response:
            status = getattr(response, "status", None)
            if isinstance(status, int) and status >= 400:
                raise UrlCardError("http_error", f"источник вернул HTTP {status}")
            final_url = validate_source_url(str(getattr(response, "geturl", lambda: target)()))
            return _read_limited(response, maximum).decode(_charset(response), errors="replace"), final_url
    except UrlCardError:
        raise
    except error.HTTPError as exc:
        raise UrlCardError("http_error", f"источник вернул HTTP {exc.code}") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise UrlCardError("fetch_failed", "не удалось получить HTML-страницу источника") from exc


class _TextCollector(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template", "svg"})
    _BLOCK = frozenset({"address", "article", "br", "div", "h1", "h2", "h3", "li", "main", "p", "section", "tr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.title = ""
        self._current: list[str] = []
        self._title: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def _flush(self) -> None:
        value = _normalise(" ".join(self._current))
        if value:
            self.lines.append(value)
        self._current.clear()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self._BLOCK:
            self._flush()
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self.title, self._in_title = _normalise(" ".join(self._title)), False
        if tag in self._BLOCK:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        (self._title if self._in_title else self._current).append(data)

    def finish(self) -> None:
        self._flush()
        if not self.title:
            self.title = _normalise(" ".join(self._title))


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(value).replace("\u00a0", " ")).strip()


def _amount(value: str) -> int:
    digits = re.sub(r"\D", "", value)
    if not digits:
        raise ValueError("empty amount")
    return int(digits)


def _label_value(lines: list[str], label: str) -> str | None:
    label_key = label.casefold()
    for index, line in enumerate(lines):
        current = line.rstrip(":").casefold()
        if current == label_key and index + 1 < len(lines):
            return lines[index + 1]
        match = re.match(rf"^{re.escape(label)}\s*[:\-]?\s*(.+)$", line, re.IGNORECASE)
        if match:
            return _normalise(match.group(1))
    return None


def parse_html_card(html: str, source_url: str) -> dict[str, Any]:
    """Produce a source-grounded envelope; missing fields stay explicitly missing."""

    url = validate_source_url(source_url)
    collector = _TextCollector()
    try:
        collector.feed(str(html))
        collector.close()
    except Exception as exc:
        raise UrlCardError("malformed_html", "не удалось разобрать HTML-страницу") from exc
    collector.finish()
    text = _normalise(" ".join((collector.title, *collector.lines)))
    header = _HEADER_RE.search(text)
    prices = [_amount(match.group(0)) for match in _MONEY_RE.finditer(text)]
    complex_match = re.search(r"ЖК\s*[«\"]([^»\"]+)[»\"]", text, re.IGNORECASE)
    area = float(header.group("area").replace(" ", "").replace(",", ".")) if header else None
    floor = int(header.group("floor")) if header else None
    card = {
        "object_type": _normalise(header.group("object_type")).lower() if header else None,
        "complex_name": complex_match.group(1).strip() if complex_match else None,
        "developer": _label_value(collector.lines, "Застройщик"),
        "area_m2": area,
        "floor": floor,
        "price_rub": prices[0] if prices else None,
        "completion": _label_value(collector.lines, "Срок сдачи") or _label_value(collector.lines, "Срок ГК"),
        "address": _label_value(collector.lines, "Адрес"),
        "finishing": _label_value(collector.lines, "Отделка"),
        "location": _label_value(collector.lines, "Локация"),
    }
    required = ("complex_name", "area_m2", "floor", "price_rub", "completion", "address")
    return {
        "schema_version": SCHEMA_VERSION,
        "parser": "novostroy_m_apartment_v1",
        "source_url": url,
        "title": collector.title or None,
        "card": card,
        "missing": [name for name in required if card[name] in (None, "")],
        "derived": {"price_difference_is_not_a_promotion": True},
    }


def fetch_card(source_url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, max_bytes: int = DEFAULT_MAX_BYTES, opener: Callable[..., Any] = request.urlopen) -> dict[str, Any]:
    """Fetch one public allowlisted page and parse it without following unsafe redirects."""

    html, final_url = _read_html(source_url, timeout=timeout, maximum=max_bytes, opener=opener)
    return parse_html_card(html, final_url)
