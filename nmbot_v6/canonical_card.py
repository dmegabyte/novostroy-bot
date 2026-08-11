"""Compact, code-owned evidence contract for the V6 answer writer."""

from __future__ import annotations

from typing import Any, Mapping

from .privacy import immutable_safe_copy
from .prompt1_contract import REQUESTED_CLAIMS

_PROJECT_FIELDS = {
    "name": "project_name",
    "developer": "developer",
    "location": "location",
    "district": "district",
    "price_range": "project_price",
    "price_from": "project_price",
    "ready": "project_completion",
    "finishing": "project_finishing",
    "metro": "metro_name",
    "metro_distance": "metro_distance",
}
_DIRECT_CLAIM_FIELDS = {
    "installment": "installment_terms",
    "payment_by_installments": "installment_terms",
    "mortgage": "mortgage_terms",
    "mortgage_calc": "mortgage_terms",
}
_LOT_CONTAINERS = ("ads", "lot_examples", "apartment_types")
_LOT_PRICE_FIELDS = ("price", "room_price", "price_total", "cost")
_NONEMPTY = (None, "", [], ())


def _put(
    canonical: dict[str, Any],
    conflicts: dict[str, list[Mapping[str, Any]]],
    target: str,
    source: str,
    value: Any,
) -> None:
    if value in _NONEMPTY:
        return
    clean = immutable_safe_copy(value)
    if target in conflicts:
        conflicts[target].append({"source": source, "value": clean})
        return
    if target not in canonical:
        canonical[target] = clean
    elif canonical[target] != clean:
        conflicts.setdefault(target, []).append({"source": source, "value": clean})
        canonical.pop(target, None)


def _lot_claims(
    raw: Mapping[str, Any],
    requested_rooms: int | None,
) -> tuple[dict[str, Any], dict[str, list[Mapping[str, Any]]]]:
    claims: dict[str, Any] = {}
    conflicts: dict[str, list[Mapping[str, Any]]] = {}
    matched_lots: list[dict[str, Any]] = []
    for container in _LOT_CONTAINERS:
        rows = raw.get(container)
        if not isinstance(rows, (list, tuple)):
            continue
        for row in rows[:20]:
            if not isinstance(row, Mapping):
                continue
            rooms = row.get("rooms")
            if isinstance(rooms, str) and rooms.strip().isascii() and rooms.strip().isdigit():
                rooms = int(rooms.strip())
            if requested_rooms is not None and rooms != requested_rooms:
                continue
            price = next((row.get(key) for key in _LOT_PRICE_FIELDS if row.get(key) not in _NONEMPTY), None)
            lot: dict[str, Any] = {}
            if rooms is not None:
                lot["rooms"] = rooms
            if price is not None:
                lot["price"] = immutable_safe_copy(price)
            for key in ("state", "status", "completion", "ready", "finishing"):
                if row.get(key) not in _NONEMPTY:
                    lot[key] = immutable_safe_copy(row[key])
            if lot:
                matched_lots.append(lot)
    if matched_lots:
        if any("price" in lot for lot in matched_lots):
            claims["room_price"] = tuple(lot for lot in matched_lots if "price" in lot)
        if any("state" in lot or "status" in lot for lot in matched_lots):
            claims["availability"] = tuple(
                lot for lot in matched_lots if "state" in lot or "status" in lot
            )
        completions = [lot.get("completion", lot.get("ready")) for lot in matched_lots]
        completions = [value for value in completions if value not in _NONEMPTY]
        if completions:
            claims["lot_completion"] = tuple(completions)
        finishings = [lot["finishing"] for lot in matched_lots if lot.get("finishing") not in _NONEMPTY]
        if finishings:
            claims["lot_finishing"] = tuple(finishings)
    return claims, conflicts


def _canonical_card(raw: Mapping[str, Any], requested_rooms: int | None):
    canonical: dict[str, Any] = {}
    conflicts: dict[str, list[Mapping[str, Any]]] = {}
    for source, target in {**_PROJECT_FIELDS, **_DIRECT_CLAIM_FIELDS}.items():
        if source in raw:
            _put(canonical, conflicts, target, source, raw[source])
    lot_claims, lot_conflicts = _lot_claims(raw, requested_rooms)
    for target, value in lot_claims.items():
        _put(canonical, conflicts, target, "lots", value)
    conflicts.update(lot_conflicts)
    return canonical, conflicts


def _evidence_cards(plan: Any, evidence: Any) -> tuple[Mapping[str, Any], ...]:
    safe_facts = getattr(evidence, "safe_facts", None)
    if isinstance(safe_facts, Mapping):
        cards: list[Mapping[str, Any]] = []
        for group in (safe_facts.get("facts"), safe_facts.get("near"), safe_facts.get("cards")):
            if isinstance(group, (list, tuple)):
                cards.extend(item for item in group if isinstance(item, Mapping))
        if cards:
            return tuple(cards[:3])
    return tuple(
        (*getattr(plan, "facts", ()), *getattr(plan, "near", ()))[:3]
    )


def build_answer_contract(plan: Any, evidence: Any, *, question_policy: Mapping[str, Any]):
    requested = tuple(dict.fromkeys(
        claim
        for claim in (
            *getattr(plan, "requested_claims", ()),
            *getattr(plan, "missing", ()),
        )
        if claim in REQUESTED_CLAIMS
    ))
    rooms = getattr(plan, "params", {}).get("rooms")
    requested_rooms = rooms if type(rooms) is int else None
    cards: list[Mapping[str, Any]] = []
    allowed: list[str] = []
    conflicts: dict[str, Any] = {}
    for index, raw in enumerate(_evidence_cards(plan, evidence)):
        canonical, card_conflicts = _canonical_card(raw, requested_rooms)
        for claim in card_conflicts:
            canonical.pop(claim, None)
        cards.append({"index": index, "canonical": canonical})
        for key in canonical:
            if key not in allowed:
                allowed.append(key)
        if card_conflicts:
            conflicts[str(index)] = card_conflicts
    conflicted_claims = {
        claim for card in conflicts.values() for claim in card
    }
    present_requested = set(allowed) & set(requested)
    explicitly_missing = {
        value for value in getattr(plan, "missing", ()) if value in REQUESTED_CLAIMS
    }
    missing = [
        claim for claim in requested
        if claim not in present_requested or claim in explicitly_missing or claim in conflicted_claims
    ]
    allowed = [claim for claim in allowed if claim not in conflicted_claims and claim not in explicitly_missing]
    return immutable_safe_copy({
        "version": 1,
        "cards": cards,
        "allowed_claims": allowed,
        "requested_claims": requested,
        "missing_claims": missing,
        "conflicts": conflicts,
        "next_action": {
            "question_goal": question_policy.get("question_goal"),
            "operator_escalation_required": bool(question_policy.get("operator_escalation_required")),
        },
    })
