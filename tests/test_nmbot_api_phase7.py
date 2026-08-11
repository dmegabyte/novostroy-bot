from __future__ import annotations

import importlib.util
import asyncio
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPT_DIR / "nmbot_api_server.py"


def load_api_module(name: str = "nmbot_api_server_phase7"):
    sys.path.insert(0, str(SCRIPT_DIR))
    sys.modules.pop("chat_tester_bot", None)
    sys.modules.pop("_nmbot_legacy_chat_tester_bot", None)
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_v2_api_import_does_not_load_chat_tester_bot() -> None:
    load_api_module("nmbot_api_server_phase7_import")

    assert "chat_tester_bot" not in sys.modules
    assert "_nmbot_legacy_chat_tester_bot" not in sys.modules


def test_direct_script_smoke_resolves_repo_packages() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--smoke"],
        cwd=SCRIPT_DIR,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr


def test_neutral_overmind_client_satisfies_v2_adapter_interface() -> None:
    mod = load_api_module("nmbot_api_server_phase7_client")
    client = mod.OvermindClient()

    assert hasattr(client, "ensure_session")
    assert hasattr(client, "_run_gateway_request")
    assert hasattr(client, "close")
    assert not hasattr(client, "ask")


def test_explicit_v1_lazy_path_resolves_canonical_scripts_module() -> None:
    mod = load_api_module("nmbot_api_server_phase7_legacy")

    legacy = mod._load_legacy_chat_module()

    assert Path(legacy.__file__).resolve() == SCRIPT_DIR / "chat_tester_bot.py"
    assert "chat_tester_bot" not in sys.modules
    assert sys.modules["_nmbot_legacy_chat_tester_bot"] is legacy


def test_v2_planner_state_payload_keeps_safe_context_without_legacy_import() -> None:
    mod = load_api_module("nmbot_api_server_phase7_planner_payload")

    payload = mod._dialog_planner_state_payload({
        "params": {"purpose": "rental", "mortgage_type": "family_mortgage"},
        "selected_option": {"name": "ЖК Событие", "developer": "comfort", "price": "от 18 млн", "phone": "+79999999999"},
        "visible_options": [
            {"idx": 1, "name": "ЖК Событие", "metro": "Аминьевская", "mortgage": "есть программы", "raw": {"secret": "x"}},
            {"idx": 2, "name": "ЖК Символ", "price_range": "от 17 млн"},
        ],
        "last_options": [{"name": "ЖК Символ", "services": ["магазины"]}],
        "dialog_window": [
            {"role": "bot", "text": "Хотите проверить условия покупки по ЖК Событие?"},
            {"role": "user", "text": "да"},
        ],
        "last_bot_question": "Хотите проверить условия покупки по ЖК Событие?",
        "last_offer_type": "selected_option_details",
        "last_answer_kind": "selected_option_financing_manager_offer",
        "last_offer": {
            "action": "verify_selected_live_facts",
            "subject_type": "visible_option",
            "subject_name": "ЖК Событие",
            "requested_facts": ["mortgage_terms"],
        },
        "active_task": {"type": "financing"},
        "active_scenario": {"key": "rental", "turn_count": 2},
        "numeric_choice_policy": "reject",
    })

    assert "chat_tester_bot" not in sys.modules
    assert "_nmbot_legacy_chat_tester_bot" not in sys.modules
    assert set(payload) == {
        "params", "selected_option", "visible_options", "last_options", "rejected_option_names",
        "last_bot_question", "last_offer_type", "last_answer_kind", "last_offer", "last_turn", "active_task",
        "active_scenario", "scenario_context", "numeric_choice_policy", "conversation_followup",
    }
    assert payload["selected_option"] == {"name": "ЖК Событие", "price": "от 18 млн"}
    assert payload["visible_options"][0]["name"] == "ЖК Событие"
    assert "raw" not in payload["visible_options"][0]
    assert payload["last_turn"]["expected_action_class"] == "operator_live_check"
    assert payload["last_offer"]["subject_name"] == "ЖК Событие"
    assert payload["last_offer"]["action"] == "verify_selected_live_facts"
    assert payload["scenario_context"]["primary_scenario"] == "rental"
    assert payload["scenario_context"]["facet_request"]["type"] == "mortgage"
    assert payload["conversation_followup"]["mortgage_type"] == "family_mortgage"
    assert payload["numeric_choice_policy"] == "reject"


def test_v1_wrapper_lazy_loads_and_delegates_exact_function(monkeypatch) -> None:
    mod = load_api_module("nmbot_api_server_phase7_wrapper_delegate")
    calls = []

    def fake_legacy_func(name: str):
        calls.append(name)
        def delegated(*args, **kwargs):
            return {"name": name, "args": args, "kwargs": kwargs}
        return delegated

    monkeypatch.setattr(mod, "_legacy_func", fake_legacy_func)

    result = mod._extract_phone_from_text("+7 999 111-22-33")

    assert calls == ["_extract_phone_from_text"]
    assert result["name"] == "_extract_phone_from_text"


def test_json_state_store_new_and_reset_records_are_v2_native(tmp_path) -> None:
    async def scenario() -> None:
        mod = load_api_module("nmbot_api_server_phase7_state_store")
        store = mod.JsonStateStore(tmp_path / "state.json")

        created = await store.get("new-user")
        assert set(created) == {"nmbot_v2"}
        assert isinstance(created["nmbot_v2"], dict)

        await store.save("new-user", {"params": {"location": "legacy"}, "last_bot_question": "old?"})
        await store.reset("new-user")
        reset = await store.get("new-user")
        assert set(reset) == {"nmbot_v2"}
        assert reset["nmbot_v2"].get("params") == {}

    asyncio.run(scenario())
