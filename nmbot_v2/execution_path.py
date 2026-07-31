from __future__ import annotations

import re
from typing import Any

from .contracts import TurnAction


EXECUTION_PATH_SCHEMA = "nmbot.execution_path.v1"
DEFAULT_V2_PATH_ID = "v2.turn.v1"
JIVO_V2_PATH_ID = "jivo.v2.turn.v1"
V2_PLANNER_STAGE_ID = "v2.planner"
V2_TRANSITION_STAGE_ID = "v2.transition"
V2_SEARCH_STAGE_ID = "v2.search"
V2_RESPONSE_PLAN_STAGE_ID = "v2.response_plan"
V2_DETERMINISTIC_RENDER_STAGE_ID = "v2.deterministic_render"
V2_RESPONSE_WRITER_STAGE_ID = "v2.response_writer"
V2_RESPONSE_FORMATTER_STAGE_ID = "v2.response_formatter"
V2_MANAGER_REWRITER_STAGE_ID = "v2.manager_rewriter"
V2_RUNTIME_FINALIZE_STAGE_ID = "v2.runtime_finalize"
JIVO_API_PREPARE_STAGE_ID = "jivo.api.prepare"
V2_EXECUTION_STAGE_IDS = (
    V2_PLANNER_STAGE_ID,
    V2_TRANSITION_STAGE_ID,
    V2_SEARCH_STAGE_ID,
    V2_RESPONSE_PLAN_STAGE_ID,
    V2_DETERMINISTIC_RENDER_STAGE_ID,
    V2_RESPONSE_WRITER_STAGE_ID,
    V2_RESPONSE_FORMATTER_STAGE_ID,
    V2_MANAGER_REWRITER_STAGE_ID,
    V2_RUNTIME_FINALIZE_STAGE_ID,
)
JIVO_V2_EXECUTION_STAGE_IDS = V2_EXECUTION_STAGE_IDS + (JIVO_API_PREPARE_STAGE_ID,)
EXECUTION_PATH_STAGE_IDS = {
    DEFAULT_V2_PATH_ID: V2_EXECUTION_STAGE_IDS,
    JIVO_V2_PATH_ID: JIVO_V2_EXECUTION_STAGE_IDS,
}
ALLOWED_EXECUTION_PATH_IDS = frozenset({DEFAULT_V2_PATH_ID, JIVO_V2_PATH_ID})
ALLOWED_EXECUTION_STAGE_IDS = frozenset(JIVO_V2_EXECUTION_STAGE_IDS)
ALLOWED_EXECUTION_STATUSES = frozenset({"completed", "failed", "fallback", "skipped"})
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,79}$")


def build_v2_execution_path(
    *,
    action: TurnAction,
    transition_ok: bool,
    search_invoked: bool,
    execution_ok: bool,
    execution_error_code: Any,
    response_composer: Any,
    manager_rewriter: Any,
) -> dict[str, Any]:
    """Build bounded invocation evidence for V2 runtime stages.

    The trace carries only ids/statuses and safe error codes. It intentionally
    does not include payloads, text, model/provider names, exception text,
    task ids, token counts or secrets.
    """
    search_status = "completed" if search_invoked and execution_ok else "failed" if search_invoked else "skipped"
    manager_published = _manager_published(manager_rewriter)
    stages = [
        _stage(V2_PLANNER_STAGE_ID, "completed"),
        _stage(
            V2_TRANSITION_STAGE_ID,
            "completed" if transition_ok else "failed",
            error_code=execution_error_code if not transition_ok else None,
        ),
        _stage(
            V2_SEARCH_STAGE_ID,
            search_status,
            error_code=execution_error_code if search_invoked and not execution_ok else None,
        ),
        _stage(V2_RESPONSE_PLAN_STAGE_ID, "completed"),
        _stage(V2_DETERMINISTIC_RENDER_STAGE_ID, "completed"),
        _composer_stage(V2_RESPONSE_WRITER_STAGE_ID, "writer", response_composer, allow_published=not manager_published),
        _composer_stage(V2_RESPONSE_FORMATTER_STAGE_ID, "formatter", response_composer, allow_published=not manager_published),
        _manager_stage(manager_rewriter),
        _stage(V2_RUNTIME_FINALIZE_STAGE_ID, "completed"),
    ]
    return {"schema": EXECUTION_PATH_SCHEMA, "path_id": DEFAULT_V2_PATH_ID, "stages": stages}


def append_jivo_api_prepare(execution_path: Any) -> dict[str, Any] | None:
    safe = sanitize_execution_path(execution_path)
    if not safe:
        return None
    stages = list(safe.get("stages") or [])
    stages.append(_stage(JIVO_API_PREPARE_STAGE_ID, "completed"))
    safe["path_id"] = JIVO_V2_PATH_ID
    safe["stages"] = stages[:16]
    return safe


def sanitize_execution_path(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("schema") != EXECUTION_PATH_SCHEMA:
        return None
    path_id = _safe_id(value.get("path_id"))
    if path_id not in ALLOWED_EXECUTION_PATH_IDS:
        return None
    raw_stages = value.get("stages") if isinstance(value.get("stages"), list) else []
    expected_stage_ids = EXECUTION_PATH_STAGE_IDS[path_id]
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
        if isinstance(item.get("published"), bool):
            out["published"] = bool(item.get("published"))
        error_code = _safe_error_code(item.get("error_code"))
        if error_code:
            out["error_code"] = error_code
        stages.append(out)
        seen.add(stage_id)
        last_position = position
    if not path_id or not stages:
        return None
    return {"schema": EXECUTION_PATH_SCHEMA, "path_id": path_id, "stages": stages}


def _composer_stage(stage_id: str, attempt_stage: str, response_composer: Any, *, allow_published: bool) -> dict[str, Any]:
    meta = response_composer if isinstance(response_composer, dict) else {}
    summaries = meta.get("attempt_summaries") if isinstance(meta.get("attempt_summaries"), list) else []
    matches = [item for item in summaries if isinstance(item, dict) and str(item.get("stage") or "") == attempt_stage]
    if not matches:
        return _stage(stage_id, "skipped")
    failed = next((item for item in matches if str(item.get("status") or "").lower() != "ok"), None)
    if failed is None:
        published = allow_published and bool(meta.get("published")) and _published_composer_stage(meta, summaries) == attempt_stage
        return _stage(stage_id, "completed", published=published)
    status = "fallback" if str(meta.get("status") or "").lower() == "fallback" or not bool(meta.get("used")) else "failed"
    return _stage(stage_id, status, published=False, error_code=failed.get("error_code"))


def _manager_stage(manager_rewriter: Any) -> dict[str, Any]:
    meta = manager_rewriter if isinstance(manager_rewriter, dict) else {}
    if not bool(meta.get("used")):
        status = "skipped" if str(meta.get("status") or "").lower() == "skipped" or str(meta.get("reason") or "") in {"off", "reset_turn"} else "fallback"
        return _stage(V2_MANAGER_REWRITER_STAGE_ID, status, published=bool(meta.get("published")), error_code=meta.get("error_code"))
    return _stage(V2_MANAGER_REWRITER_STAGE_ID, "completed", published=bool(meta.get("published")))


def _manager_published(manager_rewriter: Any) -> bool:
    meta = manager_rewriter if isinstance(manager_rewriter, dict) else {}
    return bool(meta.get("used")) and bool(meta.get("published"))


def _published_composer_stage(meta: dict[str, Any], summaries: list[Any]) -> str | None:
    if not bool(meta.get("published")):
        return None
    ok_stages = [str(item.get("stage") or "") for item in summaries if isinstance(item, dict) and str(item.get("status") or "").lower() == "ok"]
    if "formatter" in ok_stages:
        return "formatter"
    if "writer" in ok_stages:
        return "writer"
    return None


def _stage(stage_id: str, status: str, *, published: bool | None = None, error_code: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {"stage_id": stage_id, "status": status if status in ALLOWED_EXECUTION_STATUSES else "failed"}
    if published is not None:
        out["published"] = bool(published)
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
