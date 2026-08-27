from __future__ import annotations

import json
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "v6_product_scenarios.json"
EXPECTED_IDS = {
    "new_search",
    "refine_search",
    "selected_property",
    "finance_consultation",
    "direct_specialist",
}


def test_synthetic_product_scenarios_cover_exactly_the_v6_contract() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "nmbot.v6_product_scenarios.v1"
    assert payload["kind"] == "synthetic"
    assert payload["privacy"] == "messages are authored test inputs, not retained customer dialogue"
    assert {case["id"] for case in payload["scenarios"]} == EXPECTED_IDS
    assert len(payload["scenarios"]) == 5


def test_finance_case_preserves_consent_and_fact_boundaries() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    finance = next(case for case in payload["scenarios"] if case["id"] == "finance_consultation")

    assert finance["acceptance"] == {
        "search_required": False,
        "terminal": "phone_question_after_consent",
        "finance_preference": "mortgage_details",
        "params_min_fee_forbidden": True,
        "phone_before_consent_forbidden": True,
    }
