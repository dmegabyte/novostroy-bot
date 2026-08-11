"""Code-owned semantic view of V6 search evidence for Prompt 2."""

from __future__ import annotations

from typing import Any, Mapping

from .privacy import immutable_safe_copy

_FIELD_MAP = {
    "name": "project_name", "developer": "developer", "location": "location",
    "district": "district", "price_range": "project_price", "price_from": "project_price",
    "ready": "project_completion", "finishing": "project_finishing",
    "metro": "metro_name", "metro_distance": "metro_distance",
    "ads": "lots", "lot_examples": "lots", "installment": "installment_terms",
    "mortgage": "mortgage_terms",
}


def _canonical_card(raw: Mapping[str, Any]):
    canonical, conflicts, unmapped_keys = {}, {}, []
    for source_name, value in raw.items():
        target = _FIELD_MAP.get(str(source_name))
        if target is None or (target == "lots" and not isinstance(value, (list, tuple))):
            unmapped_keys.append(str(source_name))
            continue
        if target not in canonical:
            canonical[target] = immutable_safe_copy(value)
        elif canonical[target] != value:
            conflicts.setdefault(target, []).append({"source": str(source_name), "value": immutable_safe_copy(value)})
    return canonical, conflicts, unmapped_keys


def build_answer_contract(plan: Any, evidence: Any, *, question_policy: Mapping[str, Any]):
    """Build Prompt2's semantic view; unmapped values are not exposed."""
    cards, allowed, conflicts = [], [], {}
    for raw in getattr(plan, "facts", ()):
        if not isinstance(raw, Mapping):
            continue
        canonical, card_conflicts, card_unmapped = _canonical_card(raw)
        for conflicted_key in card_conflicts:
            canonical.pop(conflicted_key, None)
        cards.append({"canonical": canonical, "conflicts": card_conflicts})
        for key, value in canonical.items():
            if value not in (None, "", [], ()) and key not in allowed:
                allowed.append(key)
        conflicts.update(card_conflicts)
    return immutable_safe_copy({
        "version": 1,
        "cards": cards,
        "allowed_claims": allowed,
        "requested_claims": [str(value) for value in getattr(plan, "requested_claims", ()) if value],
        "missing_claims": [str(value) for value in getattr(plan, "missing", ()) if value],
        "conflicts": conflicts,
        "next_action": {
            "question_goal": question_policy.get("question_goal"),
            "operator_escalation_required": bool(question_policy.get("operator_escalation_required")),
        },
    })
