import asyncio
import json

import pytest

from nmbot_v6.simple_runtime import MULTIPLE_PHONES_TEXT, SimpleRuntime
from nmbot_v6.phone import parse_phone
from nmbot_v6.simple_state import SimpleState
from scripts.nmbot_crm_outbox import LocalCallbackOutbox
from scripts.nmbot_v6_simple_adapter import PHONE_SAVE_FAILURE, PHONE_SUCCESS, run_v6_simple_turn


class Backend:
    def parse(self, candidate, region): return "".join(c for c in candidate if c.isdigit())
    def is_possible_number(self, parsed): return len(parsed) in {10, 11}
    def is_valid_number(self, parsed): return (len(parsed) == 10 and parsed[0] == "9") or (len(parsed) == 11 and parsed[0] in "78" and parsed[1] == "9")
    def format_e164(self, parsed): return "+7" + (parsed[-10:])


class Port:
    def __init__(self): self.calls = []
    async def run(self, payload, *, repair=False): self.calls.append(payload); raise AssertionError("model called")


class Store:
    def __init__(self, envelope=None): self.envelope = envelope or {}; self.saves = []
    async def get(self, key): return self.envelope
    async def save(self, key, value): self.envelope = value; self.saves.append(value)


def test_h144_invalid_bare_fixture_is_not_accepted_as_phone():
    assert parse_phone("70000000001", Backend()).recognized is False
    assert parse_phone("79990000001", Backend()).recognized is True


@pytest.mark.parametrize("phone", ["+7 999 123-45-67", "8 (999) 123-45-67", "9991234567", "79991234567"])
def test_valid_formats_zero_models(phone):
    p1, p2 = Port(), Port()
    out = asyncio.run(SimpleRuntime(p1, p2, phone_backend=Backend()).run(phone, SimpleState()))
    assert out.status == "phone" and not p1.calls and not p2.calls


def test_mixed_phone_terminal_multiple_and_numeric_negative():
    p1, p2 = Port(), Port()
    assert asyncio.run(SimpleRuntime(p1, p2, phone_backend=Backend()).run("+79991234567 и вопрос", SimpleState())).status == "phone"
    multiple = asyncio.run(SimpleRuntime(p1, p2, phone_backend=Backend()).run("+79991234567 или +79881234567", SimpleState()))
    assert multiple.text == MULTIPLE_PHONES_TEXT and "+7" not in multiple.text
    class ReplyPort:
        def __init__(self, value): self.value, self.calls = value, []
        async def run(self, payload, **kwargs):
            from nmbot_v6.simple_gateway import SimpleGatewayResult
            self.calls.append(payload); return SimpleGatewayResult(self.value, "a")
    a, b = ReplyPort({"action": "continue", "facts": [], "near": [], "missing": [], "params": {}}), ReplyPort({"action": "reply", "response": "Принято.", "final_question": ""})
    assert asyncio.run(SimpleRuntime(a, b, phone_backend=Backend()).run("Бюджет 18000000", SimpleState())).status == "completed"


def test_adapter_outbox_queue_duplicate_restart_and_safe_context(tmp_path):
    store = Store({"nmbot_v6": SimpleState(awaiting_phone=True).plain()})
    outbox = LocalCallbackOutbox(tmp_path / "outbox")
    p1, p2 = Port(), Port()
    app = {"state_store": store, "v6_simple_prompt1_port": p1, "v6_simple_prompt2_port": p2, "v6_phone_backend": Backend(), "v6_callback_outbox": outbox}
    kwargs = dict(user_id="chat", message="мой +79991234567 и ЖК А", channel="jivo", meta={"event_id": "event-1"})
    first = asyncio.run(run_v6_simple_turn(app, **kwargs))
    second = asyncio.run(run_v6_simple_turn(app, **kwargs))
    assert first["answer"] == second["answer"] == PHONE_SUCCESS and not p1.calls and not p2.calls
    files = [p for p in (tmp_path / "outbox").glob("*.json")]
    assert len(files) == 1
    record = json.loads(files[0].read_text())
    assert set(record["context"]) == {"runtime", "channel", "dialogue_excerpt"}
    rendered_context = json.dumps(record["context"], ensure_ascii=False)
    assert "79991234567" not in rendered_context and "selected" not in rendered_context and "goal" not in rendered_context
    assert first["handoff_to_operator"] is False and store.envelope["nmbot_v6"]["awaiting_phone"] is False


def test_missing_event_id_uses_private_session_phone_idempotency(tmp_path):
    store = Store({"nmbot_v6": SimpleState(awaiting_phone=True).plain()})
    outbox = LocalCallbackOutbox(tmp_path / "outbox")
    p1, p2 = Port(), Port()
    app = {"state_store": store, "v6_simple_prompt1_port": p1, "v6_simple_prompt2_port": p2, "v6_phone_backend": Backend(), "v6_callback_outbox": outbox}
    kwargs = dict(user_id="chat", message="+79991234567", channel="jivo", meta={})
    first = asyncio.run(run_v6_simple_turn(app, **kwargs))
    second = asyncio.run(run_v6_simple_turn(app, **kwargs))
    assert first["meta"]["outbox_enqueue"] == "queued"
    assert second["meta"]["outbox_enqueue"] == "duplicate"
    assert len(list((tmp_path / "outbox").glob("*.json"))) == 1
    rendered = json.dumps(first["meta"]["v6_trace"], ensure_ascii=False)
    assert "79991234567" not in rendered
    assert [stage["status"] for stage in first["meta"]["v6_trace"]["stages"]] == ["not_called", "not_called", "not_called", "accepted", "prepared"]


def test_outbox_failure_does_not_claim_success():
    class Broken:
        def enqueue(self, **kwargs): raise OSError()
    app = {"state_store": Store(), "v6_simple_prompt1_port": Port(), "v6_simple_prompt2_port": Port(), "v6_phone_backend": Backend(), "v6_callback_outbox": Broken()}
    result = asyncio.run(run_v6_simple_turn(app, user_id="x", message="+79991234567", channel="jivo", meta={"event_id": "e"}))
    assert result["answer"] == PHONE_SAVE_FAILURE and result["ok"] is False
