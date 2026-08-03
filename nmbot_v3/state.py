from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any, Mapping

from .contracts import SCHEMA_VERSION, V3Action, V3ContractError, V3PlannerContext, V3Stage, _reject_unknown



@dataclass(frozen=True)
class V3ConversationState:
    """Small direct V3 state; it intentionally contains no user text or contact data."""

    schema_version: str = SCHEMA_VERSION
    revision: int = 0
    stage: V3Stage = V3Stage.RESET
    last_action: V3Action | None = None
    visible_option_refs: tuple[str, ...] = ()
    pending_followup_key: str | None = None
    has_pending_action: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise V3ContractError("invalid_schema_version")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or not 0 <= self.revision <= 1_000_000:
            raise V3ContractError("invalid_revision")
        object.__setattr__(self, "stage", V3Stage.coerce(self.stage))
        if self.last_action is not None:
            object.__setattr__(self, "last_action", V3Action.coerce(self.last_action))
        context = V3PlannerContext(
            self.visible_option_refs,
            self.pending_followup_key,
            self.has_pending_action,
        )
        object.__setattr__(self, "visible_option_refs", context.visible_option_refs)
        object.__setattr__(self, "pending_followup_key", context.pending_followup_key)
        object.__setattr__(self, "has_pending_action", context.has_pending_action)

    @classmethod
    def clean(cls, revision: int = 0) -> "V3ConversationState":
        return cls(revision=revision)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "revision": self.revision,
                "stage": self.stage.value, "last_action": None if self.last_action is None else self.last_action.value,
                "visible_option_refs": list(self.visible_option_refs),
                "pending_followup_key": self.pending_followup_key,
                "has_pending_action": self.has_pending_action}

    @property
    def planner_context(self) -> V3PlannerContext:
        """Reconstruct the sole planner context persisted by the V3 worker."""
        return V3PlannerContext(self.visible_option_refs, self.pending_followup_key, self.has_pending_action)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "V3ConversationState":
        expected = {field.name for field in fields(cls)}
        _reject_unknown(data, expected)
        if set(data) != expected:
            raise V3ContractError("invalid_state_fields")
        return cls(**dict(data))


@dataclass(frozen=True)
class V3StateDelta:
    """The complete, explicit V3 persistence change for one accepted turn."""

    revision_increment: int = 0
    stage: V3Stage | None = None
    last_action: V3Action | None = None
    planner_context: V3PlannerContext | None = None

    def __post_init__(self) -> None:
        if type(self.revision_increment) is not int or self.revision_increment not in (0, 1):
            raise V3ContractError("invalid_revision_increment")
        if self.stage is None:
            if self.revision_increment:
                raise V3ContractError("revision_without_state_change")
            if self.last_action is not None:
                raise V3ContractError("action_without_stage")
            if self.planner_context is not None:
                raise V3ContractError("context_without_state_change")
            return
        object.__setattr__(self, "stage", V3Stage.coerce(self.stage))
        if self.last_action is None:
            raise V3ContractError("missing_delta_action")
        object.__setattr__(self, "last_action", V3Action.coerce(self.last_action))
        if self.revision_increment != 1:
            raise V3ContractError("state_change_without_revision")
        if (self.stage, self.last_action) not in {
            (V3Stage.CLARIFY, V3Action.CLARIFY),
            (V3Stage.ANSWERED, V3Action.RESPOND),
        }:
            raise V3ContractError("invalid_delta_stage_action")
        if self.planner_context is not None and not isinstance(self.planner_context, V3PlannerContext):
            raise V3ContractError("invalid_planner_context")

    @property
    def is_empty(self) -> bool:
        return self == V3StateDelta()


def apply_v3_state_delta(
    state: V3ConversationState, delta: V3StateDelta, *, accepted: bool = True
) -> V3ConversationState:
    """Pure V3 reducer: rejected work is a strict no-op, never a migration path."""

    if not isinstance(state, V3ConversationState):
        raise V3ContractError("invalid_v3_state")
    if not isinstance(delta, V3StateDelta):
        raise V3ContractError("invalid_v3_state_delta")
    if not accepted or delta.is_empty:
        return state
    return replace(
        state,
        revision=state.revision + delta.revision_increment,
        stage=delta.stage if delta.stage is not None else state.stage,
        last_action=delta.last_action if delta.last_action is not None else state.last_action,
        visible_option_refs=(delta.planner_context.visible_option_refs if delta.planner_context is not None else state.visible_option_refs),
        pending_followup_key=(delta.planner_context.pending_followup_key if delta.planner_context is not None else state.pending_followup_key),
        has_pending_action=(delta.planner_context.has_pending_action if delta.planner_context is not None else state.has_pending_action),
    )
