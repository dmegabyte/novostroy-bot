"""Small helpers for immutable, phone-safe public values."""

from __future__ import annotations

import math
import re
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import ContractError

PHONEISH = re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)")
EMAILISH = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])")
_PRIVATE_KEY = re.compile(r"phone|telephone|mobile|contact|callback|pii", re.IGNORECASE)
_FORBIDDEN_PROVENANCE_KEY = re.compile(
    r"model|assistant|prompt|metadata|mcp[_-]?servers", re.IGNORECASE
)
_NUMERIC_PARAM_RANGES = {
    "rooms": (0, 20),
    "floor": (1, 300),
    "count": (1, 20),
    "min_price": (0, 1_000_000_000_000),
    "max_price": (0, 1_000_000_000_000),
}
NUMERIC_PARAM_FIELDS = frozenset(_NUMERIC_PARAM_RANGES)


def immutable_safe_copy(
    value: Any,
    *,
    allowed_numeric_fields: frozenset[str] = frozenset(),
) -> Any:
    """Copy JSON-like public data, rejecting PII and model provenance."""
    return _immutable_safe_copy(value, allowed_numeric_fields=allowed_numeric_fields)


def _immutable_safe_copy(
    value: Any,
    *,
    allowed_numeric_fields: frozenset[str] = frozenset(),
    numeric_param_field: str | None = None,
) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ContractError("public data contains an invalid number")
        if numeric_param_field is not None:
            low, high = _NUMERIC_PARAM_RANGES[numeric_param_field]
            if not low <= value <= high:
                raise ContractError("public data contains an invalid numeric parameter")
            return value
        absolute = abs(value)
        if isinstance(value, int):
            phone_like = 1_000_000_000 <= absolute <= 999_999_999_999_999
        else:
            phone_like = 10 <= len(str(absolute).replace(".", "")) <= 15
        if phone_like:
            raise ContractError("public data contains a phone-like number")
        return value
    if isinstance(value, str):
        if PHONEISH.search(value):
            raise ContractError("public data contains a phone number")
        if EMAILISH.search(value):
            raise ContractError("public data contains an email address")
        return value
    if type(value) in (list, tuple):
        return tuple(_immutable_safe_copy(item) for item in value)
    if isinstance(value, Mapping):
        if any(
            not isinstance(key, str)
            or _PRIVATE_KEY.search(key)
            or _FORBIDDEN_PROVENANCE_KEY.search(key)
            or PHONEISH.search(key)
            or EMAILISH.search(key)
            for key in value
        ):
            raise ContractError("public data contains a private or invalid key")
        return MappingProxyType({
            key: _immutable_safe_copy(
                item,
                numeric_param_field=(
                    key if key in allowed_numeric_fields and key in _NUMERIC_PARAM_RANGES else None
                ),
            )
            for key, item in value.items()
        })
    raise ContractError("public data must contain only JSON-like values")
