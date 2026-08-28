"""Opaque phone boundary; recognition is added later as a deterministic owner."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

try:
    import phonenumbers as _phonenumbers
except ImportError:  # Optional until a release artifact supplies the dependency.
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
    _normalized: str

    def __post_init__(self) -> None:
        if not isinstance(self._normalized, str) or not self._normalized:
            raise ValueError("invalid_private_phone")

    def __repr__(self) -> str:
        return "PrivatePhone(<redacted>)"

    def reveal_for_private_storage(self) -> str:
        return self._normalized


@dataclass(frozen=True)
class PhoneParseResult:
    recognized: bool
    private_phone: PrivatePhone | None = None
    code: str = "not_found"

    def __post_init__(self) -> None:
        if not isinstance(self.recognized, bool) or not isinstance(self.code, str) or not self.code:
            raise ValueError("invalid_phone_result")
        if self.recognized != (self.private_phone is not None):
            raise ValueError("phone_recognition_mismatch")

    def safe_projection(self) -> dict[str, object]:
        return {"recognized": self.recognized, "code": self.code}


def parse_phone(text: str, backend: PhoneMetadataBackend | None = None) -> PhoneParseResult:
    """Recognize one valid Russian phone without ever involving a model."""
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
        international_ru = candidate.startswith("+") and digits.startswith("7") and len(digits) == 11
        national_ru = not candidate.startswith("+") and (
            (len(digits) == 11 and digits.startswith("8"))
            or (len(digits) == 11 and digits.startswith("7"))
            or (len(digits) == 10 and digits.startswith("9"))
        )
        if not (international_ru or national_ru):
            continue
        parse_candidate = "+" + digits if len(digits) == 11 and digits.startswith("7") and not candidate.startswith("+") else candidate
        region = None if parse_candidate.startswith("+") else "RU"
        try:
            parsed = selected_backend.parse(parse_candidate, region)
            if not selected_backend.is_possible_number(parsed) or not selected_backend.is_valid_number(parsed):
                continue
            normalized = selected_backend.format_e164(parsed)
        except Exception as exc:
            parse_error = getattr(_phonenumbers, "NumberParseException", ())
            if parse_error and isinstance(exc, parse_error):
                continue
            return PhoneParseResult(False, code="dependency_unavailable")
        if re.fullmatch(r"\+[1-9]\d{7,14}", normalized):
            return PhoneParseResult(True, PrivatePhone(normalized), "recognized")
    return PhoneParseResult(False, code="not_found")
