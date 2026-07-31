"""Bounded selected-entity capability transport and evidence projection."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Mapping

from .capability_registry import CapabilityRequest
from .contracts import OptionCard
from .evidence_resolver import EvidenceResult, EvidenceStatus, bind_evidence
from .search_contract import V2SearchRequest, build_request_data, load_prompt, parse_strict_json


def build_selected_capability_request(card: OptionCard, request: CapabilityRequest) -> V2SearchRequest:
    """Create a single-object request; identity is supplied twice deliberately."""
    name = str(card.name).strip()
    entity_id = str(request.entity_id)
    need = tuple(request.need)
    return V2SearchRequest(
        search_goal={
            "entity_type": "residential_complex",
            "query_summary": f"Exact selected ЖК «{name}» (id={entity_id}); return only its structured evidence.",
            "explicit_terms": ["selected_capability", "exact_entity_id", entity_id, name, *need],
        },
        requested_hard={}, effective_hard={}, preferences={"format": "selected_evidence"},
        relaxation_audit=[], response_viewpoint="financing", base_viewpoint=None,
        available_fact_fields=list(need), count=1, ignored_preferences=[],
    )


async def fetch_selected_capability(
    card: OptionCard, request: CapabilityRequest, gateway: Any, *, timeout: float | None = None, model: str | None = None
) -> tuple[OptionCard, EvidenceResult, dict[str, Any]]:
    """Fetch once, bind before projection, and return content-free operational metadata."""
    wire = build_request_data(build_selected_capability_request(card, request), prompt=load_prompt(), model=model or "")
    try:
        response = gateway(wire)
        raw, meta = await asyncio.wait_for(response, timeout=max(0.001, float(timeout))) if timeout else await response
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        return card, EvidenceResult(EvidenceStatus.EVIDENCE_EMPTY), _safe_meta("selected_capability_timeout", transport_class="timeout")
    except Exception:
        return card, EvidenceResult(EvidenceStatus.EVIDENCE_EMPTY), _safe_meta("selected_capability_transport", transport_class="transport")
    if not isinstance(meta, Mapping) or meta.get("_safe_fallback") or meta.get("_upstream_error") or meta.get("ok") is False:
        return card, EvidenceResult(EvidenceStatus.EVIDENCE_EMPTY), _safe_meta("selected_capability_provider", transport_class="provider")
    parsed = raw if isinstance(raw, Mapping) else parse_strict_json(str(raw or ""))[0]
    if not isinstance(parsed, Mapping):
        return card, EvidenceResult(EvidenceStatus.EVIDENCE_EMPTY), _safe_meta("selected_capability_parse", parse_class="invalid")
    evidence = bind_evidence(request, parsed)
    status = evidence.status.value
    if evidence.status not in {EvidenceStatus.EVIDENCE_COMPLETE, EvidenceStatus.EVIDENCE_PARTIAL}:
        return card, evidence, _safe_meta(
            "selected_capability_" + ("rejected" if evidence.status == EvidenceStatus.EVIDENCE_REJECTED else "empty"),
            evidence, transport_class="gateway", parse_class="structured",
        )
    data = evidence.data
    projected = replace(
        card,
        mortgage_rate=data.get("min_percent"),
        mortgage_down_payment=data.get("min_fee"),
        mortgage_term=data.get("credit_month"),
        mortgage_terms=_mortgage_terms(data),
    )
    return projected, evidence, _safe_meta("selected_capability_" + status, evidence, transport_class="gateway", parse_class="structured")


def _safe_meta(
    status: str,
    evidence: EvidenceResult | None = None,
    *,
    transport_class: str | None = None,
    parse_class: str | None = None,
) -> dict[str, Any]:
    """Return only bounded operational classes; never transport source content."""
    evidence = evidence or EvidenceResult(EvidenceStatus.EVIDENCE_EMPTY)
    meta: dict[str, Any] = {
        "status": status,
        "evidence_status": evidence.status.value,
        "accepted_rows": max(0, min(int(evidence.accepted_rows), 10)),
        "rejected_rows": max(0, min(int(evidence.rejected_rows), 10)),
        "identity_match": evidence.identity_match if isinstance(evidence.identity_match, bool) else None,
        "active_root": evidence.active_root if isinstance(evidence.active_root, bool) else None,
    }
    if transport_class in {"gateway", "timeout", "transport", "provider"}:
        meta["transport_class"] = transport_class
    if parse_class in {"structured", "invalid"}:
        meta["parse_class"] = parse_class
    return meta


def _mortgage_terms(values: Mapping[str, Any]) -> str:
    parts = []
    if "min_percent" in values:
        parts.append(f"ставка от {values['min_percent']}%")
    if "min_fee" in values:
        parts.append(f"взнос от {_number_text(values['min_fee'])}%")
    if "credit_month" in values:
        parts.append(f"срок до {_number_text(values['credit_month'])} мес.")
    return ", ".join(parts)


def _number_text(value: Any) -> str:
    """Render validated numeric evidence without a cosmetic trailing .0."""
    return f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
