"""Code-owned, closed vocabulary for the current V2 runtime contracts.

These constants deliberately mirror existing normalizer allowlists.  Adding a
word here does not grant a new capability; the corresponding runtime support
must be added separately.
"""
from __future__ import annotations

from typing import Final


FACT_KEYS: Final[tuple[str, ...]] = (
    "parking", "parking_price", "parking_inventory",
    "apartment_price", "apartment_inventory", "mortgage_terms",
    "location", "metro", "schools", "readiness", "finishing", "parks", "developer",
    "lot_examples", "installment_terms", "discounts", "layouts",
)
FACT_KEY_SET: Final[frozenset[str]] = frozenset(FACT_KEYS)
FACT_SUBJECTS: Final[tuple[str, ...]] = (
    "parking", "apartment", "mortgage", "location", "transport", "infrastructure", "readiness", "developer",
)
FOCUS_ACTIONS: Final[tuple[str, ...]] = ("keep", "switch", "clear", "clarify")
DYNAMIC_FACTS: Final[tuple[str, ...]] = ("parking_price", "parking_inventory", "apartment_inventory", "mortgage_terms", "installment_terms", "discounts", "layouts")
SUBJECT_FACT_MAP: Final[dict[str, tuple[str, ...]]] = {
    "parking": ("parking", "parking_price", "parking_inventory"),
    "apartment": ("apartment_price", "apartment_inventory", "finishing"),
    "mortgage": ("mortgage_terms", "installment_terms", "discounts"), "location": ("location",), "transport": ("metro",),
    "infrastructure": ("schools", "parking", "parks"), "readiness": ("readiness",), "developer": ("developer",),
}

CONSTRAINT_KEYS: Final[tuple[str, ...]] = (
    "location", "rooms", "max_price", "min_price", "purpose", "financing", "down_payment", "ready", "finishing", "area_min_m2", "area_max_m2",
)
CONSTRAINT_KEY_SET: Final[frozenset[str]] = frozenset(CONSTRAINT_KEYS)
CONSTRAINT_ALIASES: Final[dict[str, str]] = {
    "budget_max": "max_price", "price_max": "max_price", "max_budget": "max_price", "budget": "max_price",
    "room_count": "rooms", "rooms_count": "rooms", "initial_payment": "down_payment", "finance_preference": "financing",
}
SENSITIVE_CONSTRAINT_KEYS: Final[frozenset[str]] = frozenset({"phone", "token", "secret", "password", "jivo_id", "raw_id", "client_id"})

# Reserved, code-owned metadata. They are not interpreted as capabilities.
CONSTRAINT_OPERATORS: Final[tuple[str, ...]] = ("eq", "min", "max", "in")
CONSTRAINT_UNITS: Final[tuple[str, ...]] = ("rub", "m2", "rooms")
ENTITY_TYPES: Final[tuple[str, ...]] = ("residential_complex",)
PENDING_ACTION_TYPES: Final[tuple[str, ...]] = ("selected_live_fact_check", "verify_selected_facts")
PENDING_ACTION_STATUSES: Final[tuple[str, ...]] = ("pending", "confirmed", "completed", "cancelled")

# Backwards-compatible readable aliases for old and external imports.
ALLOWED_FACTS = FACT_KEYS
ALLOWED_SUBJECTS = FACT_SUBJECTS
CONSTRAINT_ALLOW = CONSTRAINT_KEY_SET
CONSTRAINT_ALIAS_MAP = CONSTRAINT_ALIASES
