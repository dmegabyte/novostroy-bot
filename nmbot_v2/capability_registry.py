"""Closed compilation of confirmed selected-entity evidence requests."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from .state import ConversationState
from .pending_action import pending_action_belongs_to_current_offer
from .search_contract import COMMON_FACT_FIELDS
from .vocabulary import FACT_KEYS


class CapabilityStatus(str, Enum):
    READY = "ready"
    CAPABILITY_MISSING = "capability_missing"
    PREREQUISITE_MISSING = "prerequisite_missing"


@dataclass(frozen=True)
class CapabilitySpec:
    fact_key: str
    need: tuple[str, ...]
    entity_type: str = "residential_complex"
    identity: str = "selected_entity_id"
    evidence_policy: str = "unsupported"
    executable: bool = False
    required_root_fields: tuple[str, ...] = ()
    required_evidence_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.fact_key not in FACT_KEYS:
            raise ValueError("unknown capability fact key")
        object.__setattr__(self, "need", tuple(dict.fromkeys(str(field).strip() for field in self.need if str(field).strip())))
        object.__setattr__(self, "required_root_fields", _closed_evidence_fields(self.required_root_fields))
        object.__setattr__(self, "required_evidence_fields", _closed_evidence_fields(self.required_evidence_fields))


@dataclass(frozen=True)
class CapabilityRequest:
    status: CapabilityStatus
    fact_keys: tuple[str, ...]
    entity_type: str | None = None
    entity_id: str | int | None = None
    need: tuple[str, ...] = ()
    evidence_policies: tuple[str, ...] = ()
    reason: str | None = None
    required_root_fields: tuple[str, ...] = ()
    required_evidence_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(fact not in FACT_KEYS for fact in self.fact_keys):
            raise ValueError("unknown capability fact key")
        object.__setattr__(self, "need", tuple(dict.fromkeys(str(field).strip() for field in self.need if str(field).strip())))
        object.__setattr__(self, "required_root_fields", _closed_evidence_fields(self.required_root_fields))
        object.__setattr__(self, "required_evidence_fields", _closed_evidence_fields(self.required_evidence_fields))


_FINANCE_NEED: Final[tuple[str, ...]] = ("mortgage_calc", "mortgage", "discount", "payment_by_installments", "price")
_UNSUPPORTED_NEED: Final[tuple[str, ...]] = ()


def _closed_evidence_fields(fields: tuple[str, ...]) -> tuple[str, ...]:
    """Accept only schema-owned MCP fields; never carry model-provided names."""
    normalized = tuple(dict.fromkeys(str(field).strip() for field in fields if str(field).strip()))
    if any(field not in COMMON_FACT_FIELDS for field in normalized):
        raise ValueError("unknown capability evidence field")
    return normalized

# Every vocabulary key has a spec.  Only a linked mortgage_calc for the selected
# ЖК is executable in this foundation; the rest fail explicitly at the boundary.
CAPABILITY_REGISTRY: Final[dict[str, CapabilitySpec]] = {
    fact: CapabilitySpec(fact, _UNSUPPORTED_NEED) for fact in FACT_KEYS
}
CAPABILITY_REGISTRY.update({
    "mortgage_terms": CapabilitySpec(
        "mortgage_terms", _FINANCE_NEED,
        evidence_policy="mortgage_calc_selected_active", executable=True,
        required_root_fields=("id", "state"),
        required_evidence_fields=("mortgage_calc", "mortgage", "discount", "payment_by_installments"),
    ),
    "installment_terms": CapabilitySpec("installment_terms", _FINANCE_NEED, evidence_policy="installment_company_link_required"),
    "discounts": CapabilitySpec("discounts", _FINANCE_NEED, evidence_policy="discount_seller_link_required"),
    "layouts": CapabilitySpec("layouts", ("apartment_types", "ads"), evidence_policy="layout_identity_contract_missing"),
})


def compile_capability_request(state: ConversationState) -> CapabilityRequest:
    """Compile only confirmed, selected, closed-vocabulary requests; never call MCP."""
    action = state.pending_action
    selected = state.selected_entity
    if not action or action.status != "confirmed":
        return CapabilityRequest(CapabilityStatus.PREREQUISITE_MISSING, (), reason="confirmed_pending_action_required")
    if not pending_action_belongs_to_current_offer(state):
        return CapabilityRequest(CapabilityStatus.PREREQUISITE_MISSING, action.fact_keys, reason="current_offer_required")
    if not selected or selected.entity_type != action.entity_type or str(selected.entity_id) != str(action.entity_id):
        return CapabilityRequest(CapabilityStatus.PREREQUISITE_MISSING, action.fact_keys, reason="selected_entity_required")
    specs = tuple(CAPABILITY_REGISTRY.get(fact) for fact in action.fact_keys)
    if any(spec is None for spec in specs):  # Defensive if a malformed object bypassed its contract.
        return CapabilityRequest(CapabilityStatus.CAPABILITY_MISSING, action.fact_keys, reason="unknown_fact_key")
    if not all(spec.executable for spec in specs):
        return CapabilityRequest(CapabilityStatus.CAPABILITY_MISSING, action.fact_keys, entity_type=selected.entity_type, entity_id=selected.entity_id, reason="evidence_policy_not_implemented")
    need = tuple(dict.fromkeys(field for spec in specs for field in spec.need))
    policies = tuple(dict.fromkeys(spec.evidence_policy for spec in specs))
    root_fields = tuple(dict.fromkeys(field for spec in specs for field in spec.required_root_fields))
    evidence_fields = tuple(dict.fromkeys(field for spec in specs for field in spec.required_evidence_fields))
    return CapabilityRequest(
        CapabilityStatus.READY, action.fact_keys, selected.entity_type, selected.entity_id, need, policies,
        required_root_fields=root_fields, required_evidence_fields=evidence_fields,
    )
