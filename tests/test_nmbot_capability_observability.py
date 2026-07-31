from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from nmbot_v2.capability_registry import CapabilityRequest, CapabilityStatus
from nmbot_v2.evidence_resolver import EvidenceStatus, RootState, bind_evidence
from nmbot_v2.runtime import _capability_outcome_runtime_summary
from nmbot_v2.selected_capability import build_selected_capability_request, fetch_selected_capability
from nmbot_v2.search_contract import build_query
from nmbot_v2.contracts import OptionCard
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dialogue_journal
import nmbot_api_server
import nmbot_runtime_adapter


ROOT = Path(__file__).resolve().parents[1]


def _request_validator() -> Draft202012Validator:
    schema = json.loads((ROOT / "schemas" / "v2_search_mcp_request.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _request() -> CapabilityRequest:
    return CapabilityRequest(
        CapabilityStatus.READY, ("mortgage_terms",), "residential_complex", 42,
        ("mortgage_calc",), ("mortgage_calc_selected_active",),
        required_root_fields=("id", "state"), required_evidence_fields=("mortgage_calc",),
    )


def test_binder_owns_content_free_identity_verdicts() -> None:
    result = bind_evidence(_request(), {"facts": [{"id": 42, "state": 2, "mortgage_calc": [{"novos_id": 42, "state": 2, "min_percent": 6.2}]}]})
    rejected = bind_evidence(_request(), {"facts": [{"id": 43, "state": 2}]})

    assert result.status == EvidenceStatus.EVIDENCE_PARTIAL
    assert (result.identity_match, result.active_root) == (True, True)
    assert result.root_state == RootState.ACTIVE
    assert (rejected.identity_match, rejected.active_root) == (False, None)
    assert rejected.root_state == RootState.UNKNOWN


def test_selected_request_carries_bounded_root_evidence_contract() -> None:
    request = _request()
    wire = build_selected_capability_request(OptionCard(name="Лучи", entity_id=42), request)
    envelope = json.loads(build_query(wire).split("\n", 1)[0].split("=", 1)[1])

    assert wire.required_evidence_fields == ("id", "state", "mortgage_calc")
    assert {"id", "state", "mortgage_calc"} <= set(wire.available_fact_fields)
    assert envelope["required_evidence_fields"] == ["id", "state", "mortgage_calc"]


def test_selected_request_payload_round_trip_preserves_root_evidence_and_rejects_unknown_field() -> None:
    wire = build_selected_capability_request(OptionCard(name="Лучи", entity_id=42), _request())
    payload = wire.to_payload()
    validator = _request_validator()

    assert payload["required_evidence_fields"] == ["id", "state", "mortgage_calc"]
    assert list(validator.iter_errors(payload)) == []

    invalid = {**payload, "required_evidence_fields": ["id", "state", "unknown_mcp_field"]}
    assert list(validator.iter_errors(invalid))
    with pytest.raises(ValueError, match="unknown required evidence field"):
        type(wire)(**{**wire.__dict__, "required_evidence_fields": ("unknown_mcp_field",)})


def test_root_state_distinguishes_missing_inactive_and_active_without_raw_values() -> None:
    missing = bind_evidence(_request(), {"facts": [{"id": 42, "mortgage_calc": []}]})
    inactive = bind_evidence(_request(), {"facts": [{"id": 42, "state": 1, "mortgage_calc": []}]})
    active = bind_evidence(_request(), {"facts": [{"id": 42, "state": 2, "mortgage_calc": [{"novos_id": 42, "state": 2, "min_percent": 6.2}]}]})

    assert (missing.status, missing.root_state, missing.active_root) == (EvidenceStatus.EVIDENCE_REJECTED, RootState.MISSING, False)
    assert (inactive.status, inactive.root_state, inactive.active_root) == (EvidenceStatus.EVIDENCE_REJECTED, RootState.INACTIVE, False)
    assert (active.status, active.root_state, active.active_root) == (EvidenceStatus.EVIDENCE_PARTIAL, RootState.ACTIVE, True)


def test_fetch_returns_only_safe_evidence_metadata() -> None:
    async def gateway(_wire):
        return {"facts": [{"id": 42, "state": 2, "mortgage_calc": [{"novos_id": 42, "state": 2, "min_percent": 6.2}]}]}, {"ok": True, "raw": "secret"}

    _, _, meta = asyncio.run(fetch_selected_capability(OptionCard(name="Secret ЖК", entity_id=42), _request(), gateway))

    assert meta == {"status": "selected_capability_evidence_partial", "evidence_status": "evidence_partial", "accepted_rows": 1, "rejected_rows": 0, "identity_match": True, "active_root": True, "root_state": "active", "transport_class": "gateway", "parse_class": "structured"}
    assert "Secret" not in json.dumps(meta)


def test_runtime_capability_outcome_is_closed_and_omitted_when_not_invoked() -> None:
    outcome = _capability_outcome_runtime_summary([{
        "stage": "selected_capability", "status": "selected_capability_rejected",
        "requested_facts": ["mortgage_terms", "raw_secret", "mortgage_terms"], "request_count": 99,
        "evidence_status": "evidence_rejected", "accepted_rows": -5, "rejected_rows": 999,
        "identity_match": "yes", "active_root": False, "root_state": "inactive", "raw_id": 42, "text": "secret",
    }], None)

    assert outcome == {"requested_facts": ["mortgage_terms"], "status": "selected_capability_rejected", "request_count": 1, "evidence_status": "rejected", "accepted_count": 0, "rejected_count": 10, "identity_match": None, "active_root": False, "root_state": "inactive"}
    assert _capability_outcome_runtime_summary([], None) == {}


def test_all_boundary_sanitizers_independently_drop_capability_payloads() -> None:
    raw = {
        "requested_facts": ["mortgage_terms", "raw_secret", "mortgage_terms"],
        "status": "selected_capability_timeout", "request_count": 9, "evidence_status": "evil",
        "accepted_count": -1, "rejected_count": 999, "identity_match": "true", "active_root": True, "root_state": "raw-secret",
        "transport_class": "timeout", "parse_class": "evil", "entity_id": "42", "raw_text": "secret",
    }
    expected = {"requested_facts": ["mortgage_terms"], "status": "selected_capability_timeout", "request_count": 1, "evidence_status": "unknown", "accepted_count": 0, "rejected_count": 10, "identity_match": None, "active_root": True, "root_state": "unknown", "transport_class": "timeout"}

    summary = {"stage": "current_options", "action": "answer_selected_option", "capability_outcome": raw}
    assert nmbot_runtime_adapter._safe_runtime_summary_trace(summary)["capability_outcome"] == expected
    assert nmbot_api_server._journal_runtime_summary({"meta": {"trace": {"runtime_summary": summary}}})["capability_outcome"] == expected
    assert dialogue_journal._safe_runtime_summary(summary)["capability_outcome"] == expected
    assert "secret" not in json.dumps(expected)
