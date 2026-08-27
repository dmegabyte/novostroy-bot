from __future__ import annotations

import asyncio

from nmbot_core import CoreRuntime, CoreState, GatewayResult, Prompt1Action, ToolTrace


class PhoneBackend:
    def parse(self, candidate, region): return candidate
    def is_possible_number(self, parsed): return True
    def is_valid_number(self, parsed): return True
    def format_e164(self, parsed): return "+79991234567"


class Port:
    def __init__(self, *outputs): self.outputs, self.calls = list(outputs), []
    async def run(self, payload, *, repair=False):
        self.calls.append((payload, repair))
        value = self.outputs.pop(0)
        if isinstance(value, Exception): raise value
        return GatewayResult(value, "attempt-1")


def run(p1, p2, text="Хочу квартиру", state=CoreState()):
    return asyncio.run(CoreRuntime(p1, p2, phone_backend=PhoneBackend()).run(text, state))


def p1(action="continue", **extra):
    return {"action": action, "facts": [], "near": [], "missing": [], "params": {}, "ambiguity": None, **extra} if action == "continue" else {"action": action}


def reply(text="Ответ", question=""):
    return {"action": "reply", "response": text, "final_question": question}


def test_normal_turn_uses_prompt_pair_and_safe_trace():
    outcome = run(Port(p1()), Port(reply("Есть варианты.", "Какой район?")))
    assert outcome.status == "completed" and outcome.text == "Есть варианты.\n\nКакой район?"
    assert outcome.model_calls == 2 and outcome.mcp_call_count == 0


def test_phone_and_contact_bypass_models():
    p1_port, p2_port = Port(), Port()
    phone = run(p1_port, p2_port, "+7 999 123-45-67")
    contact = run(p1_port, p2_port, "Позовите оператора")
    assert phone.status == "phone" and phone.private_phone is not None
    assert contact.request_phone and contact.model_calls == 0 and not p1_port.calls and not p2_port.calls


def test_prompt1_failure_is_fixed_specialist_offer():
    outcome = run(Port(RuntimeError("transport")), Port())
    assert outcome.status == "safe_failure" and outcome.failure_stage == "prompt1"
    assert outcome.state.pending_offer == "specialist_contact" and outcome.request_phone is False


def test_prompt2_phone_is_repaired_then_cannot_enter_phone_flow():
    outcome = run(Port(p1()), Port({"action": "request_phone", "response": "", "final_question": ""}, reply("Продолжаю")))
    assert outcome.text == "Продолжаю" and not outcome.request_phone and outcome.model_calls == 3


def test_third_turn_has_single_specialist_cta_and_clarification_does_not_advance():
    third = run(Port(p1()), Port(reply("Варианты", "Другая тема?")), state=CoreState(client_turn_count=2))
    assert third.text.endswith("специалист проверил актуальные варианты по вашему запросу?")
    assert third.text.count("?") == 1 and third.state.pending_offer == "specialist_contact"
    clarify = {"action": "clarify", "params": {}, "ambiguity": {"parameter": "rooms", "reason_code": "multiple_interpretations"}}
    result = run(Port(clarify), Port(reply("Уточните", "Сколько комнат?")), state=CoreState(client_turn_count=2))
    assert result.state.client_turn_count == 2 and result.text == "Уточните\n\nСколько комнат?"
