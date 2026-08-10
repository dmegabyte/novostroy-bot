import asyncio
import json

from nmbot_v6.gateway import MCP_TOOL, PROMPT2_PATH, Prompt2Gateway, TransportResponse, TransportToolTrace, _TRACE_TOKEN, build_question_policy
from nmbot_v6.prompt1_contract import parse_prompt1
from nmbot_v6.provider import TRUSTED_MCP_SERVER, build_trusted_envelope


def _plan(*, search_mode=None, count=1, facts=1, near=0):
    params = {"count": count}
    if search_mode is not None:
        params["search_mode"] = search_mode
    fact_cards = [
        {"name": f"ЖК {index}", "location": "Москва", "district": "msk"}
        for index in range(facts)
    ]
    near_cards = [
        {
            "name": f"Рядом {index}",
            "location": "Москва",
            "district": "msk",
            "price_range": "не указано",
            "finishing": "не указано",
            "why_close": "отличие: другой проект",
        }
        for index in range(near)
    ]
    return parse_prompt1({
        "action": "search",
        "target": "new_search",
        "search_policy": "required",
        "clarification_question": "",
        "response": "",
        "facts": fact_cards,
        "near": near_cards,
        "missing": [],
        "params": params,
    })


def test_named_object_uses_expanded_answer_mode_on_first_turn():
    policy = build_question_policy(
        "Расскажи подробно про ЖК Люблинский парк",
        {"revision": 0},
        _plan(search_mode="named_object"),
    )

    assert policy == {
        "question_goal": "offer_layouts_or_viewing",
        "answer_mode": "expanded_detail",
        "cards_displayed": 1,
        "dialogue_step": 1,
    }


def test_single_broad_card_keeps_existing_first_turn_goal():
    policy = build_question_policy(
        "двушка в Люблино",
        {"revision": 0},
        _plan(search_mode="broad", near=1),
    )

    assert policy["answer_mode"] == "standard"
    assert policy["question_goal"] == "choose_complex"


def test_answer_prompt_has_grounded_expanded_contract():
    prompt = PROMPT2_PATH.read_text(encoding="utf-8")

    assert 'answer_mode="expanded_detail"' in prompt
    assert "price_range" in prompt and "area" in prompt and "ready" in prompt
    assert "не спрашивай «Хотите узнать подробнее?»" in prompt


def test_prompt2_gateway_sends_expanded_detail_contract_at_transport_boundary():
    class FakeTransport:
        def __init__(self):
            self.payloads = []

        async def complete(self, payload):
            self.payloads.append(payload)
            return TransportResponse('{"intro":"","cards":[],"question":"Показать планировки?"}')

    plan = _plan(search_mode="named_object")
    trace = TransportToolTrace(
        task_ref="task-v6", actual_server=TRUSTED_MCP_SERVER, actual_tool=MCP_TOOL,
        call_count=1, safe_facts={"facts": [{"name": "ЖК 0", "ref": "complex:lp"}]},
        effective_constraints={"rooms": 2}, visible_refs=("complex:lp",), _token=_TRACE_TOKEN,
    )
    evidence = build_trusted_envelope(search_required=True, requested_tool=MCP_TOOL, trace=trace)
    transport = FakeTransport()

    output = asyncio.run(Prompt2Gateway(transport).run(
        "Расскажи подробно про ЖК 0", {"revision": 0}, plan, evidence,
    ))

    assert json.loads(output)["question"] == "Показать планировки?"
    assert len(transport.payloads) == 1
    payload = transport.payloads[0]
    assert payload["_payload_stage"] == "v6_answer_writer"
    query = json.loads(payload["query"].removeprefix("V6_ANSWER_INPUT="))
    assert query["question_policy"] == {
        "question_goal": "offer_layouts_or_viewing", "answer_mode": "expanded_detail",
        "cards_displayed": 1, "dialogue_step": 1,
    }
    assert query["trusted_mcp"] == {
        "task_ref": "task-v6", "actual_server": TRUSTED_MCP_SERVER,
        "actual_tool": MCP_TOOL, "call_count": 1,
        "safe_facts": {"facts": [{"name": "ЖК 0", "ref": "complex:lp"}]},
        "effective_constraints": {"rooms": 2}, "visible_refs": ["complex:lp"],
        "evidence_source": "transport_trace",
    }
    assert "tools" not in payload
    assert "mcp_servers" not in payload
    assert 'Если `answer_mode="expanded_detail"`' in payload["system_prompt"]
    assert "не спрашивай «Хотите узнать подробнее?»" in payload["system_prompt"]
