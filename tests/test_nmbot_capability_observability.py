from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from nmbot_v2.capability_registry import CapabilityRequest, CapabilityStatus
from nmbot_v2.evidence_resolver import EvidenceStatus, bind_evidence
from nmbot_v2.runtime import _capability_outcome_runtime_summary
from nmbot_v2.selected_capability import fetch_selected_capability
from nmbot_v2.contracts import OptionCard
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dialogue_journal
import nmbot_api_server
import nmbot_runtime_adapter


def _request() -> CapabilityRequest:
    return CapabilityRequest(
        CapabilityStatus.READY, ("mortgage_terms",), "residential_complex", 42,
        ("mortgage_calc",), ("mortgage_calc_selected_active",),
    )


def test_binder_owns_content_free_identity_verdicts() -> None:
    result = bind_evidence(_request(), {"facts": [{"id": 42, "state": 2, "mortgage_calc": [{"novos_id": 42, "state": 2, "min_percent": 6.2}]}]})
    rejected = bind_evidence(_request(), {"facts": [{"id": 43, "state": 2}]})

    assert result.status == EvidenceStatus.EVIDENCE_PARTIAL
    assert (result.identity_match, result.active_root) == (True, True)
    assert (rejected.identity_match, rejected.active_root) == (False, None)


def test_fetch_returns_only_safe_evidence_metadata() -> None:
    async def gateway(_wire):
        return {"facts": [{"id": 42, "state": 2, "mortgage_calc": [{"novos_id": 42, "state": 2, "min_percent": 6.2}]}]}, {"ok": True, "raw": "secret"}

    _, _, meta = asyncio.run(fetch_selected_capability(OptionCard(name="Secret ЖК", entity_id=42), _request(), gateway))

    assert meta == {"status": "selected_capability_evidence_partial", "evidence_status": "evidence_partial", "accepted_rows": 1, "rejected_rows": 0, "identity_match": True, "active_root": True, "transport_class": "gateway", "parse_class": "structured"}
    assert "Secret" not in json.dumps(meta)


def test_runtime_capability_outcome_is_closed_and_omitted_when_not_invoked() -> None:
    outcome = _capability_outcome_runtime_summary([{
        "stage": "selected_capability", "status": "selected_capability_rejected",
        "requested_facts": ["mortgage_terms", "raw_secret", "mortgage_terms"], "request_count": 99,
        "evidence_status": "evidence_rejected", "accepted_rows": -5, "rejected_rows": 999,
        "identity_match": "yes", "active_root": False, "raw_id": 42, "text": "secret",
    }], None)

    assert outcome == {"requested_facts": ["mortgage_terms"], "status": "selected_capability_rejected", "request_count": 1, "evidence_status": "rejected", "accepted_count": 0, "rejected_count": 10, "identity_match": None, "active_root": False}
    assert _capability_outcome_runtime_summary([], None) == {}


def test_all_boundary_sanitizers_independently_drop_capability_payloads() -> None:
    raw = {
        "requested_facts": ["mortgage_terms", "raw_secret", "mortgage_terms"],
        "status": "selected_capability_timeout", "request_count": 9, "evidence_status": "evil",
        "accepted_count": -1, "rejected_count": 999, "identity_match": "true", "active_root": True,
        "transport_class": "timeout", "parse_class": "evil", "entity_id": "42", "raw_text": "secret",
    }
    expected = {"requested_facts": ["mortgage_terms"], "status": "selected_capability_timeout", "request_count": 1, "evidence_status": "unknown", "accepted_count": 0, "rejected_count": 10, "identity_match": None, "active_root": True, "transport_class": "timeout"}

    summary = {"stage": "current_options", "action": "answer_selected_option", "capability_outcome": raw}
    assert nmbot_runtime_adapter._safe_runtime_summary_trace(summary)["capability_outcome"] == expected
    assert nmbot_api_server._journal_runtime_summary({"meta": {"trace": {"runtime_summary": summary}}})["capability_outcome"] == expected
    assert dialogue_journal._safe_runtime_summary(summary)["capability_outcome"] == expected
    assert "secret" not in json.dumps(expected)
