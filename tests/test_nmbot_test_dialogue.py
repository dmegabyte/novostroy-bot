from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_test_dialogue.py"
spec = importlib.util.spec_from_file_location("nmbot_test_dialogue", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["nmbot_test_dialogue"] = mod
spec.loader.exec_module(mod)


def bot(text: str = "Готово") -> dict[str, Any]:
    return {"event": "BOT_MESSAGE", "message": {"text": text}}


def invite(text: str = "") -> dict[str, Any]:
    return {"event": "INVITE_AGENT", "message": {"text": text}}


class Sender:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.texts: list[str] = []

    def __call__(self, text: str) -> tuple[str, dict[str, Any]]:
        self.texts.append(text)
        return f"event-{len(self.texts)}", self.responses.pop(0)


def test_argument_parsing_and_runtime_mapping() -> None:
    args = mod.parse_args(["--runtime", "v4", "--query", "семейная двушка"])

    assert args.runtime == "v4"
    assert args.query == "семейная двушка"
    assert args.check_delivery is False
    assert args.delivery_timeout == 45
    assert mod.runtime_config("v3").start_command == "/start_3"
    assert mod.runtime_config("v4").start_command == "/start_4"


def test_delivery_argument_validation() -> None:
    args = mod.parse_args(["--runtime", "v3", "--check-delivery", "--delivery-timeout", "12"])

    assert args.check_delivery is True
    assert args.delivery_timeout == 12
    for argv in (["--runtime", "v3", "--delivery-timeout", "0"], ["--runtime", "v3", "--delivery-timeout", "301"], ["--runtime", "v3", "--delivery-timeout", "soon"]):
        try:
            mod.parse_args(list(argv))
        except SystemExit as exc:
            assert exc.code == 2
        else:  # pragma: no cover - defensive branch
            raise AssertionError(f"accepted invalid argv: {argv}")


def test_output_shape_has_no_contact_or_sensitive_text() -> None:
    sender = Sender([bot("Привет"), bot("1. ЖК Тайный\nЦена"), bot("Спасибо за телефон"), bot("Спасибо, Анна")])

    code, result = mod.run_dialogue(
        "v4",
        sender,
        query="секретный клиентский запрос",
        v4_checker=lambda _text: {"codes": [], "metrics": {"numbered_blocks": 1}},
    )
    output = json.dumps(result, ensure_ascii=False)

    assert code == 0
    assert result["ok"] is True
    for forbidden in [
        "секретный клиентский запрос",
        "+7 999",
        "Анна",
        "Спасибо за телефон",
        "ЖК Тайный",
    ]:
        assert forbidden not in output
    assert "message" not in keys_of(result)
    assert "text" not in keys_of(result)


def keys_of(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = set(value)
        for nested in value.values():
            found.update(keys_of(nested))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for nested in value:
            found.update(keys_of(nested))
        return found
    return set()


def test_v3_success_and_failure_gating() -> None:
    ok_sender = Sender([bot("Привет"), bot("Нашёл варианты"), bot("Телефон принят"), bot("Имя принято")])
    ok_code, ok_result = mod.run_dialogue("v3", ok_sender)

    fail_sender = Sender([bot("Привет"), bot('{"data":[1]}')])
    fail_code, fail_result = mod.run_dialogue("v3", fail_sender)

    assert ok_code == 0
    assert ok_result["ok"] is True
    assert fail_code == 2
    assert fail_result["failed_stage"] == "search"
    assert fail_result["stages"][1]["metrics"] == {"search_ok": False}


def test_v4_success_and_failure_gating() -> None:
    ok_sender = Sender([bot("Привет"), bot("1. Вариант"), bot("Телефон принят"), bot("Имя принято")])
    ok_code, ok_result = mod.run_dialogue(
        "v4",
        ok_sender,
        v4_checker=lambda _text: {"codes": [], "metrics": {"numbered_blocks": 1}},
    )

    fail_sender = Sender([bot("Привет"), bot("1. Вариант")])
    fail_code, fail_result = mod.run_dialogue(
        "v4",
        fail_sender,
        v4_checker=lambda _text: {"codes": ["bad"], "metrics": {"numbered_blocks": 1}},
    )

    assert ok_code == 0
    assert ok_result["stages"][1]["metrics"]["ux_ok"] is True
    assert fail_code == 2
    assert fail_result["failed_stage"] == "search"
    assert fail_result["stages"][1]["metrics"]["search_ok"] is False


def test_first_failure_stops_before_phone_and_name() -> None:
    sender = Sender([bot("Привет"), invite("operator")])

    code, result = mod.run_dialogue("v3", sender)

    assert code == 2
    assert result["failed_stage"] == "search"
    assert sender.texts == ["/start_3", mod.DEFAULT_QUERY]
    assert len(result["stages"]) == 2


def test_start_failure_stops_immediately() -> None:
    sender = Sender([invite("operator")])

    code, result = mod.run_dialogue("v4", sender, v4_checker=lambda _text: {})

    assert code == 2
    assert result["failed_stage"] == "start"
    assert sender.texts == ["/start_4"]


def test_default_skips_delivery_lookup() -> None:
    sender = Sender([bot("Привет"), bot("Нашёл варианты"), bot("Телефон принят"), bot("Имя принято")])

    def lookup(_ref: str) -> dict[str, Any] | None:
        raise AssertionError("delivery lookup must be opt-in")

    code, result = mod.run_dialogue("v3", sender, delivery_lookup=lookup)

    assert code == 0
    assert result["ok"] is True
    assert "delivery" not in result


def test_opt_in_delivery_success_uses_name_event_ref() -> None:
    sender = Sender([bot("Привет"), bot("Нашёл варианты"), bot("Телефон принят"), bot("Имя принято")])
    seen_refs: list[str] = []

    def lookup(ref: str) -> dict[str, Any] | None:
        seen_refs.append(ref)
        assert ref == mod.event_ref("event-4")
        return {
            "event_ref": ref,
            "lead_ref": "lead_abcdefghijklmnopqrstuvwxyz0123456789",
            "phone": "+79990000001",
            "name": "Анна",
            "payload": {"message": "секрет"},
            "sheet_delivery": {"status": "sheet_delivered", "sheet_row_ref": "row_123"},
        }

    code, result = mod.run_dialogue("v3", sender, check_delivery=True, delivery_lookup=lookup, delivery_timeout=1)
    output = json.dumps(result, ensure_ascii=False)

    assert code == 0
    assert result["ok"] is True
    assert seen_refs == [mod.event_ref("event-4")]
    assert result["delivery"] == {
        "delivery_checked": True,
        "delivery_ok": True,
        "found": True,
        "event_ref": mod.event_ref("event-4"),
        "lead_ref": "lead_abcdefghijklmnopqrstuvwxyz0",
        "delivery_status": "sheet_delivered",
        "row_ref": "row_123",
    }
    for forbidden in ["+7999", "Анна", "секрет", "payload"]:
        assert forbidden not in output
    assert "message" not in keys_of(result)
    assert "phone" not in keys_of(result)
    assert "payload" not in keys_of(result)


def test_delivery_failure_is_compact_and_safe() -> None:
    sender = Sender([bot("Привет"), bot("Нашёл варианты"), bot("Телефон принят"), bot("Имя принято")])

    def lookup(ref: str) -> dict[str, Any] | None:
        return {
            "event_ref": ref,
            "lead_ref": "lead_safe",
            "phone": "+79990000001",
            "sheet_delivery": {"status": "sheet_error raw secret +7999 Анна", "sheet_row_ref": ""},
        }

    code, result = mod.run_dialogue("v3", sender, check_delivery=True, delivery_lookup=lookup, delivery_timeout=1)
    output = json.dumps(result, ensure_ascii=False)

    assert code == 2
    assert result["ok"] is False
    assert result["failed_stage"] == "delivery"
    assert result["delivery"]["delivery_checked"] is True
    assert result["delivery"]["delivery_ok"] is False
    assert result["delivery"]["delivery_status"] == "sheet_error"
    assert result["delivery"]["row_ref"] == ""
    assert "phone" not in keys_of(result)
    assert "sheet_delivery" not in keys_of(result)
    assert "+79990000001" not in output
    assert "raw secret" not in output
    assert "Анна" not in output


def test_delivery_timeout_result_is_safe() -> None:
    calls: list[str] = []
    ticks = iter([0.0, 2.0])

    def lookup(ref: str) -> dict[str, Any] | None:
        calls.append(ref)
        return None

    result = mod.wait_delivery(
        "name-event-secret",
        lookup,
        timeout_seconds=1,
        sleep_seconds=0,
        monotonic=lambda: next(ticks),
        sleep=lambda _seconds: None,
    )
    output = json.dumps(result, ensure_ascii=False)

    assert calls == [mod.event_ref("name-event-secret")]
    assert result == {
        "delivery_checked": True,
        "delivery_ok": False,
        "found": False,
        "event_ref": mod.event_ref("name-event-secret"),
        "lead_ref": "",
        "delivery_status": "",
        "row_ref": "",
    }
    assert "name-event-secret" not in output
