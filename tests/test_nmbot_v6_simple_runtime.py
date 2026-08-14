import asyncio

import pytest

from nmbot_v6.simple_gateway import DirectTransport, SimpleGateway, SimpleGatewayResult, SimpleToolTrace
from nmbot_v6.simple_runtime import CLARIFICATION_FAILURE, PHONE_QUESTION, SPECIALIST_CTA, SPECIALIST_OFFER_ON_FAILURE, SimpleRuntime
from nmbot_v6.simple_state import SimpleState


class Port:
    def __init__(self, outputs): self.outputs, self.calls = list(outputs), []
    async def run(self, payload, *, repair=False):
        self.calls.append((payload, repair))
        value = self.outputs.pop(0)
        if isinstance(value, Exception): raise value
        return value


def result(output, trace=None):
    if isinstance(output, dict) and "facts" in output:
        output = {**output, "ambiguity": output.get("ambiguity")}
    return SimpleGatewayResult(output, "attempt-a", trace)


def run(p1, p2, state=SimpleState(), text="Хочу квартиру"):
    return asyncio.run(SimpleRuntime(p1, p2, phone_backend=NoPhoneBackend()).run(text, state))


class NoPhoneBackend:
    def parse(self, candidate, region): raise ValueError("not a phone")
    def is_possible_number(self, parsed): return False
    def is_valid_number(self, parsed): return False
    def format_e164(self, parsed): return ""


def test_empty_p1_reaches_p2():
    p1, p2 = Port([result({"action": "continue", "facts": [], "near": [], "missing": ["бюджет"], "params": {}})]), Port([result({"action": "reply", "response": "Уточните бюджет.", "final_question": ""})])
    outcome = run(p1, p2)
    assert outcome.status == "completed" and len(p2.calls) == 1 and outcome.model_calls == 2


def test_p1_transport_failure_remembers_fixed_offer_without_p2_or_phone_request():
    p1, p2 = Port([RuntimeError("raw private output")]), Port([])
    outcome = run(p1, p2)
    assert outcome.text == SPECIALIST_OFFER_ON_FAILURE and not p2.calls
    assert outcome.failure_stage == "prompt1" and outcome.error_code == "transport_failure"
    assert outcome.state.revision == 1 and outcome.request_phone is False
    assert outcome.state.history[-1]["text"] == SPECIALIST_OFFER_ON_FAILURE
    assert outcome.state.pending_offer == "specialist_contact"
    assert outcome.state.client_turn_count == 3


def test_fixed_failure_offer_is_not_repeated_on_later_reply():
    first = run(Port([RuntimeError("failure")]), Port([]))
    p1 = Port([result({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}, "ambiguity": None})])
    p2 = Port([result({"action": "reply", "response": "Продолжаю подбор.", "final_question": "Какой район?"})])
    later = run(p1, p2, first.state, "Продолжим")
    assert SPECIALIST_CTA not in later.text
    assert later.text == "Продолжаю подбор.\n\nКакой район?"


def test_p2_parse_failure_returns_fixed_specialist_offer():
    p1 = Port([result({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}})])
    p2 = Port([result("bad"), result("bad")])
    outcome = run(p1, p2)
    assert outcome.status == "safe_failure" and outcome.text == SPECIALIST_OFFER_ON_FAILURE
    assert len(p1.calls) == 1 and len(p2.calls) == 2 and outcome.model_calls == 3
    assert outcome.failure_stage == "prompt2" and outcome.error_code == "invalid_json" and outcome.state.revision == 1
    assert outcome.text != PHONE_QUESTION


def test_p1_invalid_output_is_terminal_safe_offer_without_p2_or_phone_request():
    p2 = Port([result({"action": "request_phone", "response": "", "final_question": ""})])
    outcome = run(Port([result("bad")]), p2)
    assert outcome.text == SPECIALIST_OFFER_ON_FAILURE and not p2.calls
    assert outcome.failure_stage == "prompt1" and outcome.error_code == "invalid_json"
    assert outcome.request_phone is False and outcome.state.revision == 1


def test_h108_material_without_trace_reaches_p2_literal():
    material = {"action": "continue", "facts": [{"name": "ЖК А", "price_min": 10, "ads": [{"rooms": 2, "price": 10}]}], "near": [{"name": "ЖК Б", "is_near": True, "why_close": "рядом", "differences": ["цена"]}], "missing": [], "params": {"purpose": "life", "min_price": 10}}
    p1, p2 = Port([result(material)]), Port([result({"action": "reply", "response": "От 10.", "final_question": ""})])
    outcome = run(p1, p2)
    assert outcome.status == "completed" and p2.calls[0][0]["property_material"] == {key: material[key] for key in ("facts", "near", "params")}
    assert outcome.tool_observation == "unavailable" and outcome.material_status == "accepted_nonempty"


@pytest.mark.parametrize("value", [True, -1, 4, "1", None])
def test_tool_trace_rejects_unbounded_or_untyped_call_count(value):
    with pytest.raises(ValueError):
        SimpleToolTrace("attempt-a", "novostroym", "get_flat_info", value)


@pytest.mark.parametrize("raw_trace", [
    {"actual_server": "novostroym", "actual_tool": "get_flat_info", "call_count": "1"},
    {"actual_server": "other", "actual_tool": "get_flat_info", "call_count": 1},
    {"actual_server": "novostroym", "actual_tool": "get_flat_info", "call_count": 1, "raw": {}},
])
def test_direct_transport_ignores_malformed_optional_transport_trace(raw_trace):
    class Client:
        async def _run_gateway_request_once(self, payload, headers, timeout):
            return ({"cards": [], "missing": []}, {"_gateway_task_id": "attempt-a", "v6_tool_trace": raw_trace})
    assert asyncio.run(DirectTransport(Client()).complete({})).tool_trace is None


@pytest.mark.parametrize("generic_metadata", [
    {"diagnostics": {"mcp_calls": 1}},
    {"mcp_server": "novostroym", "mcp_tool": "get_flat_info", "call_count": 1},
    {"prompt_metadata": {"v6_tool_trace": {"actual_server": "novostroym", "actual_tool": "get_flat_info", "call_count": 1}}},
])
def test_generic_gateway_metadata_cannot_prove_mcp_provenance(generic_metadata):
    card = {"action": "continue", "facts": [{"name": "ЖК А", "price_min": 10}], "near": [], "missing": [], "params": {}, "ambiguity": None}

    class Client:
        async def _run_gateway_request_once(self, payload, headers, timeout):
            return card, {"_gateway_task_id": "attempt-a", **generic_metadata}

    prompt1 = SimpleGateway(DirectTransport(Client()), "prompt1")
    p2 = Port([result({"action": "reply", "response": "Да.", "final_question": ""})])
    outcome = run(prompt1, p2)
    assert outcome.status == "completed" and outcome.tool_observation == "unavailable"


@pytest.mark.parametrize("message", ["Позовите специалиста", "Позовите специалиста и сколько стоит?"])
def test_direct_specialist_request_is_owned_by_p1_and_skips_p2(message):
    p2 = Port([])
    outcome = run(Port([result({"action": "request_phone", "facts": [], "near": [], "missing": [], "params": {}})]), p2, text=message)
    assert outcome.text == PHONE_QUESTION and outcome.request_phone is True
    assert outcome.state.awaiting_phone is True and outcome.state.pending_offer == "none" and not p2.calls


def test_p2_cannot_enter_phone_flow_when_p1_continues():
    p1 = Port([result({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}})])
    p2 = Port([result({"action": "request_phone", "response": "", "final_question": ""}), result({"action": "reply", "response": "Продолжаю поиск.", "final_question": ""})])
    outcome = run(p1, p2, text="Да")
    assert outcome.text == "Продолжаю поиск." and outcome.state.awaiting_phone is False
    assert len(p2.calls) == 2 and p2.calls[1][1] is True


def test_reply_publishes_final_question_and_records_exact_published_text():
    answer = {"action": "reply", "response": "Есть варианты.", "final_question": "Какой район?"}
    outcome = run(Port([result({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}})]), Port([result(answer)]))
    assert outcome.text == "Есть варианты.\n\nКакой район?"
    assert outcome.state.history[-1]["text"] == outcome.text


def test_awaiting_phone_does_not_route_ordinary_text():
    state = SimpleState(awaiting_phone=True)
    p1, p2 = Port([result({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}})]), Port([result({"action": "reply", "response": "Есть отделка.", "final_question": ""})])
    outcome = run(p1, p2, state, "А отделка есть?")
    assert p1.calls and p2.calls and outcome.state.awaiting_phone is False


def test_history_bounds_oldest_complete_pairs_and_third_fixture_visible():
    state = SimpleState()
    for i in range(7): state = state.accepted(f"u{i}", f"a{i}", awaiting_phone=False)
    assert len(state.history) == 12 and state.history[0]["text"] == "u1"
    p1, p2 = Port([result({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}})]), Port([result({"action": "reply", "response": "Ответ и предложение специалиста.", "final_question": ""})])
    run(p1, p2, state, "Ещё вопрос")
    assert len(p2.calls[0][0]["dialogue_history"]) == 12


def test_third_client_turn_policy_is_typed_once_and_pending_clears_on_turn_four():
    p1 = Port([result({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}}) for _ in range(4)])
    p2 = Port([result({"action": "reply", "response": f"Ответ {i}.", "final_question": ""}) for i in range(1, 5)])
    state = SimpleState()
    outcomes = []
    for i in range(1, 5):
        outcome = run(p1, p2, state, f"Вопрос {i}")
        outcomes.append(outcome)
        state = outcome.state

    policies = [call[0]["dialogue_policy"] for call in p2.calls]
    assert policies == [
        {"offer_specialist_now": False}, {"offer_specialist_now": False},
        {"offer_specialist_now": True}, {"offer_specialist_now": False},
    ]
    assert all(SPECIALIST_CTA not in outcome.text for outcome in outcomes[:2])
    assert outcomes[2].text == f"Ответ 3.\n\n{SPECIALIST_CTA}"
    assert outcomes[2].text.count("?") == 1
    assert outcomes[2].state.client_turn_count == 3
    assert outcomes[2].state.pending_offer == "specialist_contact"
    assert p1.calls[3][0]["dialogue_policy"] == {"pending_offer": "specialist_contact"}
    assert SPECIALIST_CTA not in outcomes[3].text
    assert outcomes[3].state.client_turn_count == 4 and outcomes[3].state.pending_offer == "none"


def test_third_turn_replaces_unrelated_p2_question_with_sole_specialist_cta():
    state = SimpleState(client_turn_count=2)
    p1 = Port([result({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}})])
    p2 = Port([result({"action": "reply", "response": "Есть подходящие варианты.", "final_question": "Какой район рассматриваете?"})])
    outcome = run(p1, p2, state, "Покажите варианты")
    assert outcome.text == f"Есть подходящие варианты.\n\n{SPECIALIST_CTA}"
    assert "Какой район" not in outcome.text and outcome.text.count("?") == 1
    assert outcome.state.client_turn_count == 3
    assert outcome.state.pending_offer == "specialist_contact"


def test_third_turn_clarification_has_priority_and_defers_specialist_once():
    clarify = {
        "action": "clarify", "facts": [], "near": [], "missing": [], "params": {},
        "ambiguity": {"parameter": "rooms", "reason_code": "multiple_interpretations"},
    }
    following = {"action": "continue", "facts": [], "near": [], "missing": ["район"], "params": {}}
    p1 = Port([result(clarify), result(following), result(following)])
    p2 = Port([
        result({"action": "reply", "response": "Нужно уточнить число комнат.", "final_question": "Вам нужна одна или две комнаты?"}),
        result({"action": "reply", "response": "Продолжаю подбор.", "final_question": "Какой район рассматриваете?"}),
        result({"action": "reply", "response": "Есть новые параметры.", "final_question": "Нужна отделка?"}),
    ])
    state = SimpleState(client_turn_count=2)

    clarified = run(p1, p2, state, "Уточняемый запрос")
    assert clarified.material_status == "clarification_required"
    assert clarified.text == "Нужно уточнить число комнат.\n\nВам нужна одна или две комнаты?"
    assert SPECIALIST_CTA not in clarified.text
    assert p2.calls[0][0]["ambiguity"] == clarify["ambiguity"]
    assert p2.calls[0][0]["dialogue_policy"] == {"offer_specialist_now": False}
    assert clarified.state.client_turn_count == 2

    offered = run(p1, p2, clarified.state, "Нужны две комнаты")
    assert offered.text == f"Продолжаю подбор.\n\n{SPECIALIST_CTA}"
    assert offered.state.client_turn_count == 3
    assert offered.state.pending_offer == "specialist_contact"

    later = run(p1, p2, offered.state, "Ещё вопрос")
    assert SPECIALIST_CTA not in later.text
    assert later.text == "Есть новые параметры.\n\nНужна отделка?"


def test_clarification_prompt2_failure_uses_neutral_fallback_and_preserves_offer_turn():
    clarify = {
        "action": "clarify", "facts": [], "near": [], "missing": [], "params": {},
        "ambiguity": {"parameter": "max_price", "reason_code": "multiple_interpretations"},
    }
    outcome = run(Port([result(clarify)]), Port([result("bad"), result("bad")]), SimpleState(client_turn_count=2))
    assert outcome.status == "safe_failure" and outcome.text == CLARIFICATION_FAILURE
    assert "баз" not in outcome.text.lower() and outcome.state.pending_offer == "none"
    assert outcome.state.client_turn_count == 2 and outcome.material_status == "clarification_required"


def test_optional_missing_continue_stays_ordinary_empty_material():
    p1 = Port([result({"action": "continue", "facts": [], "near": [], "missing": ["необязательная отделка"], "params": {"rooms": 2}})])
    p2 = Port([result({"action": "reply", "response": "Продолжаю подбор по двум комнатам.", "final_question": "Нужна отделка?"})])
    outcome = run(p1, p2)
    assert outcome.material_status == "accepted_empty"
    assert p2.calls[0][0]["ambiguity"] is None
    assert outcome.text == "Продолжаю подбор по двум комнатам.\n\nНужна отделка?"


def test_pending_specialist_consent_requests_phone_and_skips_p2():
    state = SimpleState(client_turn_count=3, pending_offer="specialist_contact")
    p1 = Port([result({"action": "request_phone", "facts": [], "near": [], "missing": [], "params": {}})])
    p2 = Port([])
    outcome = run(p1, p2, state, "Да")
    assert p1.calls[0][0]["dialogue_policy"] == {"pending_offer": "specialist_contact"}
    assert not p2.calls and outcome.text == PHONE_QUESTION
    assert outcome.state.awaiting_phone is True and outcome.state.pending_offer == "none"


@pytest.mark.parametrize("message", ["Да", "Нет, лучше другой район", "А какие есть варианты?"])
def test_p1_continue_never_requests_phone_for_ordinary_yes_refusal_or_new_question(message):
    state = SimpleState(client_turn_count=4, pending_offer="none")
    p1 = Port([result({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}})])
    p2 = Port([result({"action": "reply", "response": "Продолжаю по вашему запросу.", "final_question": ""})])
    outcome = run(p1, p2, state, message)
    assert outcome.text != PHONE_QUESTION and outcome.state.awaiting_phone is False
