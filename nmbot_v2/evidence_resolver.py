"""Evidence binding for compiled capabilities; no network or response rendering."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Mapping

from .capability_registry import CapabilityRequest, CapabilityStatus


class EvidenceStatus(str, Enum):
    EVIDENCE_COMPLETE = "evidence_complete"
    EVIDENCE_PARTIAL = "evidence_partial"
    EVIDENCE_EMPTY = "evidence_empty"
    EVIDENCE_REJECTED = "evidence_rejected"
    CAPABILITY_MISSING = "capability_missing"
    PREREQUISITE_MISSING = "prerequisite_missing"


@dataclass(frozen=True)
class EvidenceResult:
    status: EvidenceStatus
    values: tuple[tuple[str, Any], ...] = ()
    missing_fields: tuple[str, ...] = ()
    accepted_rows: int = 0
    rejected_rows: int = 0
    # Binder-owned, content-free identity verdicts.  None means the payload did
    # not contain enough structural evidence to make that particular claim.
    identity_match: bool | None = None
    active_root: bool | None = None

    @property
    def data(self) -> dict[str, Any]:
        return dict(self.values)


_TERM_FIELDS = ("min_percent", "min_fee", "credit_month")


def bind_evidence(request: CapabilityRequest, raw_evidence: Mapping[str, Any] | Any) -> EvidenceResult:
    if request.status == CapabilityStatus.CAPABILITY_MISSING:
        return EvidenceResult(EvidenceStatus.CAPABILITY_MISSING)
    if request.status != CapabilityStatus.READY or request.entity_id is None:
        return EvidenceResult(EvidenceStatus.PREREQUISITE_MISSING)
    if request.evidence_policies == ("mortgage_calc_selected_active",):
        return _bind_selected_mortgage_terms(request, raw_evidence)
    return EvidenceResult(EvidenceStatus.CAPABILITY_MISSING)


def _bind_selected_mortgage_terms(request: CapabilityRequest, raw_evidence: Mapping[str, Any] | Any) -> EvidenceResult:
    source = raw_evidence if isinstance(raw_evidence, Mapping) else {}
    # Selected capabilities accept only a structured selected-root wrapper.
    # Top-level sections can belong to a different ЖК, so never inspect them.
    facts = _rows(source.get("facts"))
    same_identity = [fact for fact in facts if str(fact.get("id")) == str(request.entity_id)]
    matching = [fact for fact in same_identity if _active(fact)]
    if len(matching) != 1:
        identity_match = True if same_identity else (False if facts else None)
        active_root = False if len(same_identity) == 1 and not matching else None
        return EvidenceResult(
            EvidenceStatus.EVIDENCE_REJECTED,
            rejected_rows=len(facts),
            identity_match=identity_match,
            active_root=active_root,
        )
    source = matching[0]
    rows = _rows(source.get("mortgage_calc"))
    mortgages = {str(row.get("id")): row for row in _rows(source.get("mortgage")) if _active(row)}
    accepted: list[dict[str, Any]] = []
    rejected = 0
    for row in rows:
        if str(row.get("novos_id")) != str(request.entity_id) or not _active(row):
            rejected += 1
            continue
        mortgage_id = row.get("mortgage_id")
        # When a calc claims a program relation, its program must itself be active.
        if mortgage_id not in (None, "") and str(mortgage_id) not in mortgages:
            rejected += 1
            continue
        validators = {
            "min_percent": _verified_percent,
            "min_fee": _verified_percent,
            "credit_month": _verified_credit_month,
        }
        values = {field: row[field] for field in _TERM_FIELDS if validators[field](row.get(field))}
        if not values and any(field in row for field in _TERM_FIELDS):
            rejected += 1
            continue
        if values:
            accepted.append(values)
    if not accepted:
        return EvidenceResult(
            EvidenceStatus.EVIDENCE_REJECTED if rejected else EvidenceStatus.EVIDENCE_EMPTY,
            accepted_rows=0,
            rejected_rows=rejected,
            identity_match=True,
            active_root=True,
        )
    # A result intentionally contains only the contract's three public numeric fields.
    merged: dict[str, Any] = {}
    for field in _TERM_FIELDS:
        values = [row[field] for row in accepted if field in row]
        if values:
            merged[field] = values[0]
    missing = tuple(field for field in _TERM_FIELDS if field not in merged)
    status = EvidenceStatus.EVIDENCE_COMPLETE if not missing else EvidenceStatus.EVIDENCE_PARTIAL
    return EvidenceResult(status, tuple(merged.items()), missing, len(accepted), rejected, True, True)


def _rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _active(row: Mapping[str, Any]) -> bool:
    return str(row.get("state")) == "2"


def _verified_percent(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 100
    )


def _verified_credit_month(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and float(value).is_integer()
        and 1 <= value <= 1200
    )
