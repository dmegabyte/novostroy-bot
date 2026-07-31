from __future__ import annotations

import json

from nmbot_v1.execution_path import (
    EXECUTION_PATH_SCHEMA,
    V1_DETERMINISTIC_RENDER_STAGE_ID,
    V1_PLANNER_STAGE_ID,
    V1_RUNTIME_FINALIZE_STAGE_ID,
    V1_SEARCH_STAGE_ID,
    append_jivo_api_prepare,
    build_v1_execution_path,
    sanitize_execution_path,
)


def test_v1_execution_path_sanitizer_accepts_ordered_bounded_path_and_jivo_append() -> None:
    path = build_v1_execution_path(planner_status="completed", transition_status="completed", search_status="completed", response_plan_status="completed", deterministic_render_status="completed", presenter_status="skipped")
    path["stages"][0]["payload"] = "SECRET"

    safe = sanitize_execution_path(path)
    jivo = append_jivo_api_prepare(path)

    assert safe and safe["path_id"] == "v1.turn.v1"
    assert safe["stages"][0] == {"stage_id": V1_PLANNER_STAGE_ID, "status": "completed"}
    assert jivo and jivo["path_id"] == "jivo.v1.turn.v1"
    assert jivo["stages"][-1] == {"stage_id": "jivo.api.prepare", "status": "completed"}
    assert "SECRET" not in json.dumps(jivo, ensure_ascii=False)


def test_v1_execution_path_sanitizer_rejects_invented_duplicate_out_of_order_and_raw_fields() -> None:
    invented = {"schema": EXECUTION_PATH_SCHEMA, "path_id": "evil.v1.turn.v1", "stages": [{"stage_id": V1_PLANNER_STAGE_ID, "status": "completed"}]}
    assert sanitize_execution_path(invented) is None

    out_of_order_duplicate = {
        "schema": EXECUTION_PATH_SCHEMA,
        "path_id": "v1.turn.v1",
        "stages": [
            {"stage_id": V1_SEARCH_STAGE_ID, "status": "completed", "raw_payload": "SECRET"},
            {"stage_id": V1_PLANNER_STAGE_ID, "status": "completed"},
            {"stage_id": V1_SEARCH_STAGE_ID, "status": "failed", "error_code": "SECRET bad code with spaces"},
            {"stage_id": "v1.invented", "status": "completed"},
            {"stage_id": V1_RUNTIME_FINALIZE_STAGE_ID, "status": "completed"},
        ],
    }

    safe = sanitize_execution_path(out_of_order_duplicate)

    assert safe == {
        "schema": EXECUTION_PATH_SCHEMA,
        "path_id": "v1.turn.v1",
        "stages": [
            {"stage_id": V1_SEARCH_STAGE_ID, "status": "completed"},
            {"stage_id": V1_RUNTIME_FINALIZE_STAGE_ID, "status": "completed"},
        ],
    }
    assert "SECRET" not in json.dumps(safe, ensure_ascii=False)


def test_v1_execution_path_safe_error_code_is_bounded() -> None:
    path = build_v1_execution_path(planner_status="completed", transition_status="completed", search_status="failed", error_stage=V1_SEARCH_STAGE_ID, error_code="bad code with SECRET and spaces " + "x" * 200)
    search = {item["stage_id"]: item for item in sanitize_execution_path(path)["stages"]}[V1_SEARCH_STAGE_ID]

    assert search["error_code"].startswith("bad_code_with_secret_and_spaces")
    assert len(search["error_code"]) <= 80
