from __future__ import annotations

from typing import Any, Mapping

from .contracts import V1IntentPlan
from .state import V1ConversationState, redact_phone


def build_planner_input(user_text: str, state: V1ConversationState) -> dict[str, Any]:
    return {"schema_version": 1, "safe_user_text": redact_phone(user_text) or "", "state": {"stage": state.stage.value, "hard_constraints": dict(state.hard_constraints), "preferences": dict(state.preferences), "visible_option_refs": [v.get("ref") for v in state.visible_options], "selected_project_ref": (state.selected_project or {}).get("ref")}}


def coerce_plan(raw: V1IntentPlan | Mapping[str, Any]) -> V1IntentPlan:
    if isinstance(raw, V1IntentPlan):
        return raw
    return V1IntentPlan.from_dict(raw)
