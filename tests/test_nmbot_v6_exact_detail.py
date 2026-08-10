import asyncio
import json

import pytest

from nmbot_v6.followup import PendingInteractionResolver
from nmbot_v6.gateway import (
    Prompt1Gateway, Prompt1GatewayResult, Prompt2Gateway, TransportResponse,
    V6OvermindTransport,
)
from nmbot_v6.phone import PhoneParseResult
from nmbot_v6.runtime import RuntimeFailureStage, RuntimeStatus, V6Runtime, run_v6
from nmbot_v6.state import PendingInteraction, V6State


def _no_phone(_text, _backend=None):
    return PhoneParseResult(False)


def _output(*, action="search", facts=None, near=None, params=None, response=""):
    return {
        "action": action,
        "target": "current_options" if action == "answer_current_options" else "new_search",
        "search_policy": "forbidden" if action == "answer_current_options" else "required",
        "clarification_question": "",
        "response": response,
        "facts": list(facts or []),
        "near": list(near or []),
        "missing": [],
        "params": dict(params or {}),
    }


def _card(name, ref, **extra):
    return {"name": name, "ref": ref, "location": "Москва", "district": "msk", **extra}


def _result(output):
    trace = None
    if output.get("search_policy") == "required":
        trace = V6OvermindTransport._model_projection_trace(output, {"_gateway_task_id": "task-v6"})
        assert trace is not None
    return Prompt1GatewayResult(output, trace)


class Prompt1Stub:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.states = []
        self.repair_codes = []

    async def run(self, _text, state):
        self.states.append(state)
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return _result(value)

    async def retry(self, _text, state, violation_code):
        self.states.append(state)
        self.repair_codes.append(violation_code)
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return _result(value)


class Prompt2Stub:
    def __init__(self):
        self.calls = []

    async def run(self, user_text, state, plan, evidence):
        self.calls.append((user_text, state, plan, evidence))
        cards = [{"index": 0, "text": "Подробности подтверждены."}] if plan.facts else []
        return json.dumps({"intro": "", "cards": cards, "question": "Что ещё уточнить?"})


def _run(prompt1, state, text):
    prompt2 = Prompt2Stub()
    result = asyncio.run(V6Runtime(prompt1, prompt2, phone_parser=_no_phone).run(text, state))
    return result, prompt2


def _invalid_current_options():
    return _output(action="answer_current_options", response="Ответ из текущих вариантов")


def test_fresh_named_request_repairs_empty_current_options_without_publishing_it():
    exact = _card("Люблинский парк", "complex:lp")
    prompt1 = Prompt1Stub([_invalid_current_options(), _output(
        facts=[exact], params={"count": 1, "search_mode": "named_object"}
    )])

    result, prompt2 = _run(prompt1, V6State(), "Расскажите подробно про Люблинский парк")

    assert result.status is RuntimeStatus.COMPLETED
    assert result.plan.action.value == "search"
    assert result.plan.params["search_mode"] == "named_object"
    assert prompt1.repair_codes == ["current_options_without_stored_cards"]
    assert len(prompt1.states) == 2
    assert len(prompt2.calls) == 1


def test_second_invalid_prompt1_is_contract_violation_not_provider_failure():
    prompt1 = Prompt1Stub([_invalid_current_options(), _invalid_current_options()])

    result, prompt2 = _run(prompt1, V6State(), "Подробнее про Люблинский парк")

    assert result.status is RuntimeStatus.FAILED
    assert result.failure_stage is RuntimeFailureStage.PROMPT1
    assert result.failure_code == "prompt1_contract_violation"
    assert prompt1.repair_codes == ["current_options_without_stored_cards"]
    assert not prompt2.calls
    assert result.meta["trace"]["runtime_summary"]["gateway_attempt_details"] == [
        {"stage": "gateway_attempt", "_payload_stage": "v6_search_agent", "call_attempted": True, "gateway_status": "completed", "parse_status": "ok", "validator_status": "contract_violation"},
        {"stage": "gateway_attempt", "_payload_stage": "v6_search_agent", "call_attempted": True, "gateway_status": "completed", "parse_status": "ok", "validator_status": "contract_violation"},
    ]


def test_operator_contact_has_terminal_safe_gateway_summary():
    prompt1 = Prompt1Stub([{
        **_output(), "action": "operator_contact", "target": "none", "search_policy": "forbidden", "clarification_question": "Оставить номер для звонка?",
    }])

    result, prompt2 = _run(prompt1, V6State(), "Позовите оператора")

    assert result.status is RuntimeStatus.COMPLETED
    assert not prompt2.calls
    assert result.meta["trace"]["runtime_summary"] == {
        "stage": "v6_runtime",
        "action": "operator_contact",
        "call_counts": {"gateway_attempts": 1},
        "gateway_attempt_details": [
            {"stage": "gateway_attempt", "_payload_stage": "v6_search_agent", "call_attempted": True, "gateway_status": "completed", "parse_status": "ok", "validator_status": "ok"},
        ],
    }


def test_async_runtime_retains_only_safe_v6_gateway_attempt_summary():
    class FakeTransport:
        def __init__(self, responses):
            self.responses = list(responses)
            self.payloads = []

        async def complete(self, payload):
            self.payloads.append(payload)
            return self.responses.pop(0)

    output = _output(facts=[_card("Локальный ЖК", "complex:local")])
    trace = V6OvermindTransport._model_projection_trace(output, {"_gateway_task_id": "raw-task-id"})
    assert trace is not None
    prompt1_transport = FakeTransport([TransportResponse(output, trace)])
    prompt2_transport = FakeTransport([TransportResponse(json.dumps({
        "intro": "", "cards": [{"index": 0, "text": "Подробности подтверждены."}],
        "question": "Что ещё уточнить?",
    }))])

    result = asyncio.run(V6Runtime(
        Prompt1Gateway(prompt1_transport), Prompt2Gateway(prompt2_transport), phone_parser=_no_phone,
    ).run("Покажите локальный ЖК", V6State()))

    summary = result.meta["trace"]["runtime_summary"]
    assert result.status is RuntimeStatus.COMPLETED
    assert result.evidence.task_ref == "raw-task-id"
    assert summary["call_counts"] == {"gateway_attempts": 2}
    assert summary["gateway_attempt_details"] == [
        {"stage": "gateway_attempt", "_payload_stage": "v6_search_agent", "call_attempted": True, "gateway_status": "completed", "gateway_task_id_present": True, "parse_status": "ok", "validator_status": "ok"},
        {"stage": "gateway_attempt", "_payload_stage": "v6_answer_writer", "call_attempted": True, "gateway_status": "completed", "parse_status": "ok", "validator_status": "ok"},
    ]
    dumped = json.dumps(summary, ensure_ascii=False)
    for forbidden in ("raw-task-id", "Покажите локальный ЖК", "V6_SEARCH_INPUT", "V6_ANSWER_INPUT", "Локальный ЖК"):
        assert forbidden not in dumped
    assert [payload["_payload_stage"] for payload in prompt1_transport.payloads + prompt2_transport.payloads] == [
        "v6_search_agent", "v6_answer_writer",
    ]


def test_pending_accept_runs_exact_detail_pipeline_with_saved_constraints():
    card = _card("Люблинский парк", "complex:lp", rooms=2, price_max=18_000_000)
    state = V6State(
        revision=4,
        option_refs=("complex:lp",),
        current_cards=(card,),
        pending_interaction=PendingInteraction(
            "offer", "learn_about_complex", "show_stored_details", "clear_pending",
            ("complex:lp",), 4,
        ),
    )
    prompt1 = Prompt1Stub([_output(
        facts=[card], params={
            "rooms": 2, "max_price": 18_000_000, "count": 1,
            "search_mode": "named_object",
        }
    )])

    result, prompt2 = _run(prompt1, state, "да")

    detail = prompt1.states[0]["safe_context"]["exact_detail"]
    assert result.status is RuntimeStatus.COMPLETED
    assert result.evidence.call_count >= 1
    assert detail["canonical_name"] == "Люблинский парк"
    assert detail["subject_ref"] == "complex:lp"
    assert detail["lot_constraints"] == {"rooms": 2, "max_price": 18_000_000}
    assert len(result.plan.facts) == 1
    assert not result.plan.near
    assert result.text != "Выбрала этот вариант. Что хотите узнать о нём?"
    assert len(prompt2.calls) == 1


def test_select_second_of_three_binds_second_canonical_card_and_runs_detail_pipeline():
    cards = (
        _card("Первый", "complex:first"),
        _card("Второй", "complex:second"),
        _card("Третий", "complex:third"),
    )
    state = V6State(
        revision=2,
        option_refs=tuple(card["ref"] for card in cards),
        current_cards=cards,
        pending_interaction=PendingInteraction(
            "selection", "choose_complex", "normal_prompt1", "clear_pending",
            tuple(card["ref"] for card in cards), 2,
        ),
    )
    prompt1 = Prompt1Stub([_output(
        facts=[cards[1]], params={"count": 1, "search_mode": "named_object"}
    )])

    result, prompt2 = _run(prompt1, state, "второй")

    detail = prompt1.states[0]["safe_context"]["exact_detail"]
    assert result.status is RuntimeStatus.COMPLETED
    assert detail["canonical_name"] == "Второй"
    assert detail["subject_ref"] == "complex:second"
    assert prompt1.states[0]["selected_option_ref"] == "complex:second"
    assert len(prompt2.calls) == 1


def test_only_unresolved_followups_remain_normal_prompt1_input():
    card = _card("Люблинский парк", "complex:lp")
    state = V6State(
        revision=1,
        option_refs=("complex:lp",),
        current_cards=(card,),
        pending_interaction=PendingInteraction(
            "offer", "learn_about_complex", "show_stored_details", "clear_pending",
            ("complex:lp",), 1,
        ),
    )
    for text in ("что именно?",):
        prompt1 = Prompt1Stub([_output(action="answer_current_options", response="По карточке")])
        result, _ = _run(prompt1, state, text)
        assert result.status is RuntimeStatus.COMPLETED
        assert "exact_detail" not in prompt1.states[0]["safe_context"]

    prompt1 = Prompt1Stub([_output(facts=[card], params={"count": 1})])
    result, _ = _run(prompt1, state, "да, но до 18 млн")
    assert result.status is RuntimeStatus.COMPLETED
    assert prompt1.states[0]["safe_context"]["exact_detail"]["subject_ref"] == "complex:lp"


def test_answer_current_options_is_allowed_with_nonempty_cards():
    state = V6State(current_cards=(_card("ЖК", "complex:one"),))
    prompt1 = Prompt1Stub([_output(action="answer_current_options", response="По сохранённой карточке")])

    result, _ = _run(prompt1, state, "А этот вариант с отделкой?")

    assert result.status is RuntimeStatus.COMPLETED
    assert result.plan.action.value == "answer_current_options"


@pytest.mark.parametrize(
    "params,near",
    [
        ({"count": 2, "search_mode": "named_object"}, []),
        (
            {"count": 1, "search_mode": "named_object"},
            [{
                "name": "Похожий ЖК", "ref": "complex:near", "location": "Москва",
                "district": "msk", "price_range": "от 17 млн", "finishing": "есть",
                "why_close": "отличие: другой ЖК",
            }],
        ),
    ],
)
def test_invalid_named_object_shape_fails_after_one_bounded_repair(params, near):
    card = _card("Люблинский парк", "complex:lp")
    invalid = _output(facts=[card], near=near, params=params)
    prompt1 = Prompt1Stub([invalid, invalid])

    result, prompt2 = _run(prompt1, V6State(), "Подробнее про Люблинский парк")

    assert result.status is RuntimeStatus.FAILED
    assert result.failure_code == "prompt1_contract_violation"
    assert prompt1.repair_codes == ["invalid_prompt1_contract"]
    assert not prompt2.calls


def test_exact_detail_without_named_object_is_forced_to_exact_scope():
    card = _card("Люблинский парк", "complex:lp")
    state = V6State(
        revision=1,
        option_refs=("complex:lp",),
        current_cards=(card,),
        pending_interaction=PendingInteraction(
            "offer", "learn_about_complex", "show_stored_details", "clear_pending",
            ("complex:lp",), 1,
        ),
    )
    prompt1 = Prompt1Stub([_output(facts=[card], params={"count": 1})])

    result, prompt2 = _run(prompt1, state, "да")

    assert result.status is RuntimeStatus.COMPLETED
    assert result.plan.params["search_mode"] == "named_object"
    assert result.plan.params["count"] == 1
    assert len(prompt2.calls) == 1


def test_broad_one_fact_with_near_remains_valid():
    fact = _card("Точный ЖК", "complex:exact")
    near = {
        "name": "Похожий ЖК", "ref": "complex:near", "location": "Москва",
        "district": "msk", "price_range": "от 17 млн", "finishing": "есть",
        "why_close": "отличие: бюджет выше",
    }
    prompt1 = Prompt1Stub([_output(
        facts=[fact], near=[near], params={"count": 2, "search_mode": "broad"}
    )])

    result, prompt2 = _run(prompt1, V6State(), "Подберите варианты")

    assert result.status is RuntimeStatus.COMPLETED
    assert len(result.plan.facts) == 1
    assert len(result.plan.near) == 1
    assert len(prompt2.calls) == 1


def test_sync_mcp_provider_exception_remains_provider_failure():
    class BrokenMcp:
        def execute(self, _request):
            raise RuntimeError("transport down")

    card = _card("ЖК", "complex:one")
    result = run_v6(
        "Подберите ЖК",
        V6State(),
        prompt1=lambda _text, _state: _output(
            facts=[card], params={"count": 1, "search_mode": "broad"}
        ),
        mcp=BrokenMcp(),
        prompt2=lambda *_args: "not called",
        phone_parser=_no_phone,
    )

    assert result.status is RuntimeStatus.FAILED
    assert result.failure_stage is RuntimeFailureStage.MCP
    assert result.failure_code == "provider_failure"


def test_true_prompt1_provider_exception_remains_provider_failure_without_retry():
    prompt1 = Prompt1Stub([RuntimeError("transport down")])

    result, prompt2 = _run(prompt1, V6State(), "Найдите квартиру")

    assert result.status is RuntimeStatus.FAILED
    assert result.failure_code == "provider_failure"
    assert result.failure_stage is RuntimeFailureStage.PROMPT1
    assert prompt1.repair_codes == []
    assert not prompt2.calls


def test_old_state_without_pending_interaction_still_loads():
    card = _card("Люблинский парк", "complex:lp")
    state = V6State.from_mapping({
        "revision": 3, "option_refs": ["complex:lp"], "current_cards": [card],
    })
    assert state.revision == 3
    assert state.pending_interaction is None
    assert PendingInteractionResolver().resolve("да", state).kind.value == "unresolved"

    prompt1 = Prompt1Stub([_output(action="answer_current_options", response="По сохранённой карточке")])
    result, prompt2 = _run(prompt1, state, "да")

    assert result.status is RuntimeStatus.COMPLETED
    assert result.plan.action.value == "answer_current_options"
    assert "exact_detail" not in prompt1.states[0]["safe_context"]
    assert len(prompt2.calls) == 1
