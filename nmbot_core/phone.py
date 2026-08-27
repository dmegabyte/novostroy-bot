"""Opaque phone boundary; recognition is added later as a deterministic owner."""

from __future__ import annotations

from dataclasses import dataclass


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
