from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nmbot_v2.search_contract import (
    V2SearchRequest,
    build_query,
    normalize_search_output,
    validate_search_output,
)


REQUEST_SCHEMA = ROOT / "schemas" / "v2_search_mcp_request.schema.json"
RESPONSE_SCHEMA = ROOT / "schemas" / "v2_search_mcp_response.schema.json"
FIXTURE = ROOT / "tests" / "fixtures" / "v2_search_mcp_contract.json"
GOLDENS = ROOT / "docs" / "MCP_APARTMENT_CONTRACT_GOLDENS.md"
ALLOWED_PREFERENCES = {
    "format",
    "rooms_preference",
    "budget_preference",
    "location_preference",
    "infrastructure_preference",
    "transport_preference",
    "finance_preference",
    "sort_hint",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def request_validator() -> Draft202012Validator:
    schema = _load_json(REQUEST_SCHEMA)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture(scope="module")
def response_validator() -> Draft202012Validator:
    schema = _load_json(RESPONSE_SCHEMA)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _valid_request(**overrides):
    payload = {
        "search_goal": {
            "entity_type": "new_building_flat",
            "query_summary": "synthetic apartment search",
            "explicit_terms": [],
        },
        "constraints": {
            "requested_hard": {},
            "effective_hard": {},
            "preferences": {},
            "relaxation_audit": [],
            "lot_hard": {},
        },
        "response_viewpoint": "life",
        "base_viewpoint": None,
        "available_fact_fields": ["id", "name", "location"],
        "count": 3,
        "excluded_names": [],
        "search_mode": "broad",
        "current_option_names": [],
        "facts_needed": [],
        "lot_hard": {},
        "required_evidence_fields": [],
    }
    payload.update(overrides)
    return payload


def _valid_response(**overrides):
    payload = {
        "facts": [{"id": "synthetic-1", "name": "ЖК Синтетический", "location": "Тестовая локация"}],
        "near": [
            {
                "id": "near-1",
                "name": "ЖК Почти",
                "is_near": True,
                "why_close": "другая локация",
                "differences": ["location"],
            }
        ],
        "missing": ["school", "kindergarten", "park_near", {"field": "parking", "reason_code": "requested_but_unconfirmed"}],
        "params": {},
        "diagnostics": {
            "mcp_tool": "novostroym/get_flat_info",
            "response_viewpoint": "life",
            "base_viewpoint": None,
            "requested_field_priorities": ["location", "school"],
            "relaxation_audit": [],
            "ignored_preferences": [],
            "notes": [],
        },
    }
    payload.update(overrides)
    return payload


def _assert_valid(validator: Draft202012Validator, payload: dict) -> None:
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    assert errors == [], [error.message for error in errors]


def _assert_invalid(validator: Draft202012Validator, payload: dict) -> None:
    assert list(validator.iter_errors(payload))


def test_contract_artifact_schemas_parse_as_json() -> None:
    _load_json(REQUEST_SCHEMA)
    _load_json(RESPONSE_SCHEMA)


def test_minimal_valid_request_and_response(request_validator, response_validator) -> None:
    _assert_valid(request_validator, _valid_request())
    _assert_valid(response_validator, _valid_response())


def test_request_count_zero_is_invalid(request_validator) -> None:
    _assert_invalid(request_validator, _valid_request(count=0))


@pytest.mark.parametrize(
    "near_item",
    [
        {"id": "near-1", "name": "ЖК Почти", "is_near": True, "differences": ["location"]},
        {"id": "near-1", "name": "ЖК Почти", "is_near": True, "why_close": "другая локация"},
        {"id": "near-1", "name": "ЖК Почти", "why_close": "другая локация", "differences": ["location"]},
    ],
)
def test_near_requires_marker_why_close_and_differences(response_validator, near_item) -> None:
    _assert_invalid(response_validator, _valid_response(near=[near_item]))


def test_facts_do_not_allow_true_near_marker(response_validator) -> None:
    _assert_invalid(response_validator, _valid_response(facts=[{"id": "fact-1", "name": "ЖК Exact", "is_near": True}]))
    _assert_valid(response_validator, _valid_response(facts=[{"id": "fact-1", "name": "ЖК Exact", "is_near": False}]))


def test_response_rejects_extra_top_level_key(response_validator) -> None:
    _assert_invalid(response_validator, _valid_response(response="forbidden model prose"))


def test_missing_is_limited_to_known_fields_or_safe_categories(response_validator) -> None:
    _assert_valid(response_validator, _valid_response(missing=["school", "kindergarten", "park_near", "parking", "requested_but_unavailable"]))
    _assert_invalid(response_validator, _valid_response(missing=["inventory_absent"]))


def test_diagnostics_notes_are_capped_at_five(response_validator) -> None:
    response = _valid_response()
    response["diagnostics"] = {**response["diagnostics"], "notes": ["n1", "n2", "n3", "n4", "n5", "n6"]}
    _assert_invalid(response_validator, response)


def test_synthetic_fixture_request_scenarios_match_request_schema(request_validator) -> None:
    fixture = _load_json(FIXTURE)
    for scenario in fixture["scenarios"]:
        constraints = scenario["constraints"]
        executable_constraints = {
            **constraints,
            "preferences": {key: value for key, value in constraints["preferences"].items() if key in ALLOWED_PREFERENCES},
            "lot_hard": dict(constraints.get("lot_hard") or {}),
        }
        payload = _valid_request(
            search_goal=scenario["search_goal"],
            constraints=executable_constraints,
            response_viewpoint=scenario["response_viewpoint"],
            base_viewpoint=scenario["base_viewpoint"],
            count=scenario["count"],
        )
        _assert_valid(request_validator, payload)


def test_synthetic_response_goldens_match_response_schema(response_validator) -> None:
    text = GOLDENS.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", text, flags=re.S)

    assert blocks
    for block in blocks:
        payload = json.loads(block)
        _assert_valid(response_validator, payload)


def test_executable_request_to_normalized_response_contract_round_trip(request_validator, response_validator) -> None:
    request = V2SearchRequest(
        search_goal={
            "entity_type": "new_building_flat",
            "query_summary": "synthetic two-room search",
            "explicit_terms": ["rooms"],
        },
        requested_hard={"rooms": [2]},
        effective_hard={"rooms": [2]},
        response_viewpoint="life",
        base_viewpoint=None,
        available_fact_fields=["id", "name", "rooms", "location"],
        count=3,
    )

    envelope = json.loads(build_query(request).split("\n", 1)[0].split("=", 1)[1])
    _assert_valid(request_validator, request.to_payload())
    assert envelope["search_mode"] == "broad"
    assert envelope["mcp_tool"] == "novostroym/get_flat_info"
    assert envelope["hard_evidence_requirements"]["rooms"] == ["rooms", "apartment_types.rooms", "ads.rooms"]

    normalized = normalize_search_output(
        {
            "facts": [{"id": "exact-1", "name": "ЖК Точный", "rooms": [2], "location": "Тест"}],
            "near": [{"id": "near-1", "name": "ЖК Почти", "rooms": [1], "location": "Тест"}],
            "missing": [],
            "params": {},
            "diagnostics": {},
        },
        request,
    )
    _assert_valid(response_validator, normalized)
    runtime_validation = validate_search_output(normalized, request)
    assert runtime_validation["status"] == "valid", runtime_validation
    assert normalized["near"][0]["is_near"] is True
    assert normalized["near"][0]["differences"] == ["rooms"]
