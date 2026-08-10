"""Regression checks for ref-less V6 shortlist cards."""

import asyncio
import json

from nmbot_v6.gateway import Prompt1GatewayResult, V6OvermindTransport
from nmbot_v6.phone import PhoneParseResult
from nmbot_v6.prompt1_contract import parse_prompt1
from nmbot_v6.provider import TrustedMcpEnvelope
from nmbot_v6.runtime import RuntimeStatus, V6Runtime
from nmbot_v6.state import V6State, evolve_completed_state


CARDS = (
    {"name": "Бусиновский парк", "location": "Москва", "district": "msk"},
    {"name": "Лосиноостровский парк", "location": "Москва", "district": "msk"},
    {"name": "Мичуринский парк", "location": "Москва", "district": "msk"},
)


def _search_plan():
    return parse_prompt1({
        "action": "search", "target": "new_search", "search_policy": "required",
        "clarification_question": "", "response": "", "facts": list(CARDS),
        "near": [], "missing": [], "params": {"district": "msk", "purpose": "family"},
    })


def _shortlist_state() -> V6State:
    evidence = TrustedMcpEnvelope(
        task_ref="task-slot-regression", actual_server="novostroym",
        actual_tool="get_flat_info", call_count=1,
        safe_facts={"facts": list(CARDS), "near": []},
        effective_constraints={"district": "msk", "purpose": "family"},
        visible_refs=(), evidence_source="gateway_model_mcp_projection",
    )
    return evolve_completed_state(V6State(), _search_plan(), evidence, question_goal="choose_complex")


class Prompt1Stub:
    async def run(self, _text, _state):
        output = {
            "action": "clarify", "target": "none", "search_policy": "required",
            "clarification_question": "Какой ЖК выбрать?", "response": "",
            "facts": [], "near": [], "missing": [],
            "params": {"district": "msk", "purpose": "family"},
        }
        trace = V6OvermindTransport._model_projection_trace(
            output, {"_gateway_task_id": "task-slot-followup"}
        )
        assert trace is not None
        return Prompt1GatewayResult(output, trace)


class Prompt2Stub:
    async def run(self, _text, _state, _plan, _evidence):
        return json.dumps({
            "intro": "", "cards": [],
            "question": "Какой из вариантов хотите рассмотреть подробнее?",
        })


def _no_phone(_text, _backend=None):
    return PhoneParseResult(False)


def test_three_ref_less_cards_receive_bounded_slot_refs() -> None:
    state = _shortlist_state()
    assert state.pending_interaction is not None
    assert state.pending_interaction.subject_refs == ("card:0", "card:1", "card:2")


def test_bare_yes_after_ref_less_shortlist_clarifies_without_mcp() -> None:
    result = asyncio.run(
        V6Runtime(Prompt1Stub(), Prompt2Stub(), phone_parser=_no_phone).run("да", _shortlist_state())
    )
    assert result.status is RuntimeStatus.COMPLETED
    assert result.plan is not None and result.plan.action.value == "recover_dialogue"
    assert result.plan.search_policy.value == "forbidden"
    assert result.evidence is not None and result.evidence.call_count == 0
