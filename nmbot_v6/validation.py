"""Small fail-closed validators for V6 runtime boundary values."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import ContractError
from .provider import (
    TRUSTED_MCP_SERVER,
    TRUSTED_MCP_TOOL,
    TrustedMcpEnvelope,
    _OPAQUE_REF,
    _contains_model_evidence_key,
)

_INTEGER_CONSTRAINT_KEYS = frozenset({
    "rooms", "floor", "count", "min_price", "max_price",
})


def validate_trusted_envelope(
    envelope: TrustedMcpEnvelope,
    *,
    search_required: bool,
    requested_tool: str | None,
) -> None:
    if not isinstance(envelope, TrustedMcpEnvelope):
        raise ContractError("trusted MCP envelope has the wrong type")
    if envelope.evidence_source not in {"transport_trace", "gateway_model_mcp_projection"}:
        raise ContractError("unknown MCP evidence source")
    if envelope.call_count == 0:
        if search_required or any((envelope.task_ref, envelope.actual_server, envelope.actual_tool,
                                   envelope.safe_facts, envelope.effective_constraints,
                                   envelope.visible_refs)):
            raise ContractError("zero-call envelope must be empty and non-search")
        return
    if not isinstance(envelope.task_ref, str) or not _OPAQUE_REF.fullmatch(envelope.task_ref):
        raise ContractError("task_ref must be an opaque reference")
    if envelope.actual_server != TRUSTED_MCP_SERVER:
        raise ContractError("trusted server does not match the required server")
    if not isinstance(envelope.actual_tool, str) or not envelope.actual_tool:
        raise ContractError("actual_tool must be non-empty")
    if isinstance(envelope.call_count, bool) or not isinstance(envelope.call_count, int) \
            or envelope.call_count < 1:
        raise ContractError("call_count must be a positive integer")
    if _contains_model_evidence_key(envelope.safe_facts):
        raise ContractError("model-authored evidence fields are forbidden")
    if any(not isinstance(ref, str) or not _OPAQUE_REF.fullmatch(ref) for ref in envelope.visible_refs):
        raise ContractError("visible refs must be opaque references")
    if envelope.actual_tool != requested_tool or envelope.actual_tool != TRUSTED_MCP_TOOL:
        raise ContractError("trusted tool does not match the requested tool")


def validate_publication_precondition(
    plan: Any,
    envelope: TrustedMcpEnvelope | None,
    exact_detail: Mapping[str, Any] | None = None,
) -> None:
    """Require transport-owned MCP proof for every search-required result."""

    from .prompt1_contract import Prompt1Result, SearchPolicy

    if not isinstance(plan, Prompt1Result):
        raise ContractError("Prompt 1 result has the wrong type")
    if plan.search_policy is SearchPolicy.REQUIRED:
        if not isinstance(envelope, TrustedMcpEnvelope):
            raise ContractError("search publication requires trusted MCP evidence")
        validate_trusted_envelope(
            envelope,
            search_required=True,
            requested_tool="get_flat_info",
        )
        _validate_card_identities(plan, envelope)
        if exact_detail is not None:
            _validate_exact_detail(plan, envelope, exact_detail)
    elif envelope is not None and not isinstance(envelope, TrustedMcpEnvelope):
        raise ContractError("publication evidence has the wrong type")


def validate_prompt1_state(plan: Any, state: Mapping[str, Any]) -> None:
    """Reject state-only answers that have no state-owned cards to answer from."""

    from .prompt1_contract import Prompt1Result, SearchAction

    if not isinstance(plan, Prompt1Result) or not isinstance(state, Mapping):
        raise ContractError("Prompt 1 state validation inputs are invalid")
    cards = state.get("current_cards")
    if plan.action is SearchAction.ANSWER_CURRENT_OPTIONS \
            and (not isinstance(cards, (list, tuple)) or not cards):
        raise ContractError("answer_current_options requires stored current cards")


def _validate_exact_detail(
    plan: Any,
    envelope: TrustedMcpEnvelope,
    scope: Mapping[str, Any],
) -> None:
    from .prompt1_contract import SearchAction, SearchPolicy

    name = scope.get("canonical_name")
    subject_ref = scope.get("subject_ref")
    if plan.action is not SearchAction.SEARCH or plan.search_policy is not SearchPolicy.REQUIRED:
        raise ContractError("exact detail requires a search action")
    if plan.params.get("search_mode") != "named_object":
        raise ContractError("exact detail requires named_object search mode")
    if len(plan.facts) != 1 or plan.near:
        raise ContractError("exact detail requires exactly one object and no alternatives")
    fact = plan.facts[0]
    if not isinstance(name, str) or fact.get("name") != name:
        raise ContractError("exact detail canonical name does not match")
    if isinstance(subject_ref, str) and not subject_ref.startswith("card:"):
        trusted = _trusted_cards(envelope.safe_facts, require_refs=True)
        if not any(card.get("name") == name and subject_ref in _card_refs(card) for card in trusted):
            raise ContractError("exact detail canonical reference does not match")
    constraints = scope.get("lot_constraints")
    if isinstance(constraints, Mapping):
        for key, value in constraints.items():
            actual = envelope.effective_constraints.get(key)
            if _canonical_constraint_value(key, actual) != _canonical_constraint_value(key, value):
                raise ContractError("exact detail lot constraints were not preserved")


def _canonical_constraint_value(key: Any, value: Any) -> Any:
    """Normalize bounded integer wire strings only for typed numeric constraints."""

    if key not in _INTEGER_CONSTRAINT_KEYS or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isascii() and text.isdigit() and 0 < len(text) <= 12:
            return int(text)
    return value


def _validate_card_identities(plan: Any, envelope: TrustedMcpEnvelope) -> None:
    observed = envelope.evidence_source == "gateway_model_mcp_projection"
    trusted_cards = _trusted_cards(envelope.safe_facts, require_refs=not observed)
    for card in (*plan.facts, *plan.near):
        name = card.get("name")
        card_refs = _card_refs(card)
        if not isinstance(name, str) or not name or _has_invalid_card_ref(card):
            raise ContractError("search card identity is absent from trusted MCP evidence")
        for trusted in trusted_cards:
            if trusted.get("name") != name:
                continue
            trusted_refs = _card_refs(trusted)
            if card_refs and not all(ref in trusted_refs for ref in card_refs):
                continue
            if all(
                _contains_field_value(trusted, key, value)
                or (
                    key in {"ref", "id", "object_id", "option_ref"}
                    and value in trusted_refs
                )
                for key, value in card.items()
            ):
                break
        else:
            raise ContractError("search card fields are absent from trusted MCP evidence")


def _trusted_cards(value: Any, *, require_refs: bool = True, depth: int = 0, budget: list[int] | None = None) -> list[Mapping[str, Any]]:
    """Collect bounded transport-owned cards with an explicit name/ref binding."""

    budget = [256] if budget is None else budget
    if depth > 8 or budget[0] <= 0:
        return []
    budget[0] -= 1
    result: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        if isinstance(value.get("name"), str) and (not require_refs or _card_refs(value)):
            result.append(value)
        for item in list(value.values())[:64]:
            result.extend(_trusted_cards(item, require_refs=require_refs, depth=depth + 1, budget=budget))
    elif type(value) in (tuple, list):
        for item in list(value)[:40]:
            result.extend(_trusted_cards(item, require_refs=require_refs, depth=depth + 1, budget=budget))
    return result


def _card_refs(card: Mapping[str, Any]) -> set[str]:
    return {
        value
        for key in ("ref", "id", "object_id", "option_ref")
        if isinstance((value := card.get(key)), str) and _OPAQUE_REF.fullmatch(value)
    }


def _has_invalid_card_ref(card: Mapping[str, Any]) -> bool:
    return any(
        not isinstance(card[key], str) or not _OPAQUE_REF.fullmatch(card[key])
        for key in ("ref", "id", "object_id", "option_ref")
        if key in card
    )


def _contains_field_value(
    value: Any,
    field: str,
    expected: Any,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
) -> bool:
    """Find one exact bounded field/value pair inside a bound trusted card."""

    budget = [128] if budget is None else budget
    if depth > 4 or budget[0] <= 0:
        return False
    budget[0] -= 1
    if isinstance(value, Mapping):
        if field in value:
            return _same_value(expected, value[field], depth=depth)
        return any(
            _contains_field_value(item, field, expected, depth=depth + 1, budget=budget)
            for item in list(value.values())[:64]
            if isinstance(item, Mapping) or type(item) in (tuple, list)
        )
    if type(value) in (tuple, list):
        return any(
            _contains_field_value(item, field, expected, depth=depth + 1, budget=budget)
            for item in list(value)[:40]
        )
    return False


def _same_value(expected: Any, trusted: Any, *, depth: int) -> bool:
    if depth > 4 or type(expected) is not type(trusted):
        return False
    if isinstance(expected, Mapping):
        return len(expected) <= 32 and all(
            key in trusted and _same_value(item, trusted[key], depth=depth + 1)
            for key, item in expected.items()
        )
    if type(expected) in (tuple, list):
        return len(expected) == len(trusted) <= 40 and all(
            _same_value(left, right, depth=depth + 1)
            for left, right in zip(expected, trusted)
        )
    return expected == trusted


def validate_runtime_result(result: Any) -> None:
    from .runtime import RuntimeStatus

    if result.status is RuntimeStatus.COMPLETED:
        if not isinstance(result.text, str) or not result.text or result.private_phone is not None:
            raise ContractError("completed runtime result is inconsistent")
    elif result.status is RuntimeStatus.PHONE_BYPASS:
        if result.text is not None or result.private_phone is None:
            raise ContractError("phone bypass result is inconsistent")
    elif result.status is RuntimeStatus.FAILED:
        if result.text is not None or result.private_phone is not None or not result.failure_code:
            raise ContractError("failed runtime result is inconsistent")
    else:
        raise ContractError("unknown runtime status")
