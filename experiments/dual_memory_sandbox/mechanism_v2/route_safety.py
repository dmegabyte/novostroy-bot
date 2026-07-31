"""Shared route-summary safety checks for mechanism-v2 artifacts."""

from __future__ import annotations

import re


ARMS = {"B0", "M1", "S1"}
_ARM_ID_FRAGMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])([BMS])(?:[\s_\-:=()\[\]{}]+)?([01])(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def route_summary_leaks_arm_identity(summary: str) -> bool:
    """Return True when prose discloses B0/M1/S1, including split variants.

    The detector only treats standalone arm-like letter/digit pairs as leaks, so
    ordinary words containing these characters are not rejected merely because
    they include a b/m/s followed by a zero/one elsewhere in the word.
    """
    if not isinstance(summary, str):
        return False
    for match in _ARM_ID_FRAGMENT_RE.finditer(summary):
        if f"{match.group(1).upper()}{match.group(2)}" in ARMS:
            return True
    return False
