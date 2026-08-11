"""Code-owned phone bypass; no model participates in recognition."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

try:
    import phonenumbers as _phonenumbers
except ImportError:  # Optional until the artifact explicitly supplies it.
    _phonenumbers = None

_CANDIDATE = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,18}(?!\d)")


class PhoneMetadataBackend(Protocol):
    def parse(self, candidate: str, region: str | None) -> Any: ...
    def is_possible_number(self, parsed: Any) -> bool: ...
    def is_valid_number(self, parsed: Any) -> bool: ...
    def format_e164(self, parsed: Any) -> str: ...


class _LibPhoneNumbersBackend:
    def parse(self, candidate: str, region: str | None) -> Any:
        return _phonenumbers.parse(candidate, region)

    def is_possible_number(self, parsed: Any) -> bool:
        return bool(_phonenumbers.is_possible_number(parsed))

    def is_valid_number(self, parsed: Any) -> bool:
        return bool(_phonenumbers.is_valid_number(parsed))

    def format_e164(self, parsed: Any) -> str:
        return _phonenumbers.format_number(parsed, _phonenumbers.PhoneNumberFormat.E164)


@dataclass(frozen=True, repr=False)
class PrivatePhone:
    """Opaque private value; callers must opt in to reveal it to private storage."""

    _normalized: str

    def __repr__(self) -> str:
        return "PrivatePhone(<redacted>)"

    def reveal_for_private_storage(self) -> str:
        return self._normalized


@dataclass(frozen=True)
class PhoneParseResult:
    recognized: bool
    private_phone: PrivatePhone | None = None
    code: str = "not_found"

    def safe_projection(self) -> dict[str, object]:
        return {"recognized": self.recognized, "code": self.code}


def parse_phone(text: str, backend: PhoneMetadataBackend | None = None) -> PhoneParseResult:
    if not isinstance(text, str) or not text:
        return PhoneParseResult(False, code="not_found")
    selected_backend = backend
    if selected_backend is None:
        if _phonenumbers is None:
            return PhoneParseResult(False, code="dependency_unavailable")
        selected_backend = _LibPhoneNumbersBackend()
    for match in _CANDIDATE.finditer(text):
        candidate = match.group().strip()
        digits = re.sub(r"\D", "", candidate)
        is_international_ru = candidate.startswith("+") and digits.startswith("7") and len(digits) == 11
        is_national_ru = not candidate.startswith("+") and (
            (len(digits) == 11 and digits.startswith("8"))
            or (len(digits) == 11 and digits.startswith("7"))
            or (len(digits) == 10 and digits.startswith("9"))
        )
        if not (is_international_ru or is_national_ru):
            continue
        parse_candidate = "+" + digits if len(digits) == 11 and digits.startswith("7") and not candidate.startswith("+") else candidate
        region = None if parse_candidate.startswith("+") else "RU"
        try:
            parsed = selected_backend.parse(parse_candidate, region)
            if not selected_backend.is_possible_number(parsed):
                continue
            if not selected_backend.is_valid_number(parsed):
                continue
            normalized = selected_backend.format_e164(parsed)
        except Exception as exc:
            invalid_number_error = getattr(_phonenumbers, "NumberParseException", ())
            if invalid_number_error and isinstance(exc, invalid_number_error):
                continue
            return PhoneParseResult(False, code="dependency_unavailable")
        if not re.fullmatch(r"\+[1-9]\d{7,14}", normalized):
            continue
        return PhoneParseResult(True, PrivatePhone(normalized), "recognized")
    return PhoneParseResult(False, code="not_found")
