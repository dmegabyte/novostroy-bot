import pytest

from nmbot_v6.contracts import ContractError
from nmbot_v6.prompt1_contract import parse_prompt1
from nmbot_v6.provider import TrustedMcpEnvelope
from nmbot_v6.validation import _validate_exact_detail


def _plan():
    return parse_prompt1({
        "action": "search",
        "target": "new_search",
        "search_policy": "required",
        "clarification_question": "",
        "response": "",
        "facts": [{"name": "Люблинский парк", "location": "Люблино", "district": "msk"}],
        "near": [],
        "missing": [],
        "params": {"rooms": "2", "count": 1, "search_mode": "named_object"},
    })


def _envelope(rooms):
    return TrustedMcpEnvelope(
        task_ref="gateway-constraint-test",
        actual_server="novostroym",
        actual_tool="get_flat_info",
        call_count=1,
        safe_facts={"facts": [{"name": "Люблинский парк", "location": "Люблино", "district": "msk"}]},
        effective_constraints={"rooms": rooms, "count": 1},
        evidence_source="gateway_model_mcp_projection",
    )


def _scope(rooms):
    return {
        "subject_ref": "card:0",
        "canonical_name": "Люблинский парк",
        "lot_constraints": {"rooms": rooms},
    }


def test_numeric_room_string_matches_canonical_integer() -> None:
    _validate_exact_detail(_plan(), _envelope("2"), _scope(2))


def test_non_numeric_room_text_does_not_match_integer() -> None:
    with pytest.raises(ContractError, match="lot constraints were not preserved"):
        _validate_exact_detail(_plan(), _envelope("двушка"), _scope(2))
