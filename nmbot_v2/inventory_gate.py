"""Pure broad-search inventory qualification.

This module deliberately relies only on normalized V2 contracts.  It does not
infer availability from prices, counts, project fields, or blacklist data.
"""

from __future__ import annotations

from typing import Any

from nmbot_v2.contracts import LotExample, OptionCard, SearchResult


def has_eligible_lot(card: OptionCard) -> bool:
    """Return whether a card has one active, in-sale lot with a valid id."""

    return any(_lot_is_eligible(lot) for lot in card.lot_examples)


def project_broad_inventory(result: SearchResult) -> tuple[SearchResult, dict[str, int]]:
    """Keep only broad-search cards backed by an eligible normalized lot.

    The returned trace is aggregate-only and intentionally contains neither
    card data nor card identity.
    """

    facts = tuple(card for card in result.facts if has_eligible_lot(card))
    near = tuple(card for card in result.near if has_eligible_lot(card))
    source_count = len(result.facts) + len(result.near)
    visible_count = len(facts) + len(near)
    return (
        SearchResult(facts=facts, near=near, missing=result.missing, params=result.params, summary=result.summary),
        {
            "source_count": source_count,
            "visible_count": visible_count,
            "excluded_unqualified_count": source_count - visible_count,
        },
    )


def _lot_is_eligible(lot: LotExample) -> bool:
    return _has_valid_id(lot.id) and _is_active_wire_code(lot.state) and _is_active_wire_code(lot.status)


def _has_valid_id(value: Any) -> bool:
    if isinstance(value, bool) or value in (None, ""):
        return False
    if isinstance(value, (int, float)):
        return value > 0
    return bool(str(value).strip()) and str(value).strip() != "0"


def _is_active_wire_code(value: Any) -> bool:
    if isinstance(value, bool) or value in (None, ""):
        return False
    if isinstance(value, (int, float)):
        return value == 2
    return str(value).strip() == "2"
