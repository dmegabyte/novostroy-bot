from __future__ import annotations

import re
from typing import Any


EXECUTION_PATH_SCHEMA = "nmbot.execution_path.v1"
DEFAULT_V1_PATH_ID = "v1.turn.v1"
JIVO_V1_PATH_ID = "jivo.v1.turn.v1"
V1_PLANNER_STAGE_ID = "v1.planner"
V1_TRANSITION_STAGE_ID = "v1.transition"
V1_SEARCH_STAGE_ID = "v1.search"
V1_RESPONSE_PLAN_STAGE_ID = "v1.response_plan"
V1_DETERMINISTIC_RENDER_STAGE_ID = "v1.deterministic_render"
V1_PRESENTER_STAGE_ID = "v1.presenter"
V1_RUNTIME_FINALIZE_STAGE_ID = "v1.runtime_finalize"
JIVO_API_PREPARE_STAGE_ID = "jivo.api.prepare"
V1_EXECUTION_STAGE_IDS = (
    V1_PLANNER_STAGE_ID,
    V1_TRANSITION_STAGE_ID,
    V1_SEARCH_STAGE_ID,
    V1_RESPONSE_PLAN_STAGE_ID,
    V1_DETERMINISTIC_RENDER_STAGE_ID,
    V1_PRESENTER_STAGE_ID,
    V1_RUNTIME_FINALIZE_STAGE_ID,
)
JIVO_V1_EXECUTION_STAGE_IDS = V1_EXECUTION_STAGE_IDS + (JIVO_API_PREPARE_STAGE_ID,)
EXECUTION_PATH_STAGE_IDS = {
    DEFAULT_V1_PATH_ID: V1_EXECUTION_STAGE_IDS,
    JIVO_V1_PATH_ID: JIVO_V1_EXECUTION_STAGE_IDS,
}
ALLOWED_EXECUTION_PATH_IDS = frozenset(EXECUTION_PATH_STAGE_IDS)
ALLOWED_EXECUTION_STATUSES = frozenset({"completed", "failed", "fallback", "skipped"})
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,79}$")


def build_v1_execution_path(
    *,
    planner_status: str = "skipped",
    transition_status: str = "skipped",
    search_status: str = "skipped",
    response_plan_status: str = "skipped",
    deterministic_render_status: str = "skipped",
    presenter_status: str = "skipped",
    runtime_finalize_status: str = "completed",
    error_stage: str | None = None,
    error_code: Any = None,
) -> dict[str, Any]:
    return {
        "schema": EXECUTION_PATH_SCHEMA,
        "path_id": DEFAULT_V1_PATH_ID,
        "stages": [
            _stage(V1_PLANNER_STAGE_ID, planner_status, error_code=error_code if error_stage == V1_PLANNER_STAGE_ID else None),
            _stage(V1_TRANSITION_STAGE_ID, transition_status, error_code=error_code if error_stage == V1_TRANSITION_STAGE_ID else None),
            _stage(V1_SEARCH_STAGE_ID, search_status, error_code=error_code if error_stage == V1_SEARCH_STAGE_ID else None),
            _stage(V1_RESPONSE_PLAN_STAGE_ID, response_plan_status, error_code=error_code if error_stage == V1_RESPONSE_PLAN_STAGE_ID else None),
            _stage(V1_DETERMINISTIC_RENDER_STAGE_ID, deterministic_render_status, error_code=error_code if error_stage == V1_DETERMINISTIC_RENDER_STAGE_ID else None),
            _stage(V1_PRESENTER_STAGE_ID, presenter_status, error_code=error_code if error_stage == V1_PRESENTER_STAGE_ID else None),
            _stage(V1_RUNTIME_FINALIZE_STAGE_ID, runtime_finalize_status, error_code=error_code if error_stage == V1_RUNTIME_FINALIZE_STAGE_ID else None),
        ],
    }


def append_jivo_api_prepare(execution_path: Any) -> dict[str, Any] | None:
    safe = sanitize_execution_path(execution_path)
    if not safe:
        return None
    stages = list(safe.get("stages") or [])
    stages.append(_stage(JIVO_API_PREPARE_STAGE_ID, "completed"))
    safe["path_id"] = JIVO_V1_PATH_ID
    safe["stages"] = stages[:16]
    return sanitize_execution_path(safe)


def sanitize_execution_path(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("schema") != EXECUTION_PATH_SCHEMA:
        return None
    path_id = _safe_id(value.get("path_id"))
    if path_id not in ALLOWED_EXECUTION_PATH_IDS:
        return None
    expected_stage_ids = EXECUTION_PATH_STAGE_IDS[path_id]
    raw_stages = value.get("stages") if isinstance(value.get("stages"), list) else []
    stages: list[dict[str, Any]] = []
    seen: set[str] = set()
    last_position = -1
    for item in raw_stages[:16]:
        if not isinstance(item, dict):
            continue
        stage_id = _safe_id(item.get("stage_id"))
        status = str(item.get("status") or "").strip().lower()
        if not stage_id or stage_id not in expected_stage_ids or stage_id in seen or status not in ALLOWED_EXECUTION_STATUSES:
            continue
        position = expected_stage_ids.index(stage_id)
        if position <= last_position:
            continue
        out = {"stage_id": stage_id, "status": status}
        error_code = _safe_error_code(item.get("error_code"))
        if error_code:
            out["error_code"] = error_code
        stages.append(out)
        seen.add(stage_id)
        last_position = position
    if not stages:
        return None
    return {"schema": EXECUTION_PATH_SCHEMA, "path_id": path_id, "stages": stages}


def _stage(stage_id: str, status: str, *, error_code: Any = None) -> dict[str, Any]:
    out = {"stage_id": stage_id, "status": status if status in ALLOWED_EXECUTION_STATUSES else "failed"}
    safe_error = _safe_error_code(error_code)
    if safe_error:
        out["error_code"] = safe_error
    return out


def _safe_id(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if _SAFE_ID_RE.fullmatch(text) else None


def _safe_error_code(value: Any) -> str | None:
    text = str(value or "").split(":", 1)[0].strip().lower()
    if not text:
        return None
    safe = re.sub(r"[^a-z0-9_.:-]", "_", text)[:80].strip("_")
    return safe or None
