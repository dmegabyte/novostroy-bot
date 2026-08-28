from __future__ import annotations

import pytest

from nmbot_core import (
    CoreContractError,
    Prompt1Action,
    Prompt2Action,
    build_prompt1_input,
    build_prompt2_input,
    parse_prompt1,
    parse_prompt2,
)


def test_prompt1_continue_preserves_only_allowed_material_and_bounds_projection() -> None:
    document = parse_prompt1({
        "action": "continue",
        "facts": [{"name": "ЖК А"}, {"name": "ЖК Б"}],
        "near": [{"name": "ЖК В"}, {"name": "ЖК Г"}],
        "missing": ["срок сдачи"],
        "params": {"rooms": 2, "finance_preference": "mortgage_details"},
        "ambiguity": None,
    })

    assert document.action is Prompt1Action.CONTINUE
    assert build_prompt2_input("двушка", [], document, offer_specialist_now=False)["property_material"] == {
        "facts": [{"name": "ЖК А"}, {"name": "ЖК Б"}],
        "near": [{"name": "ЖК В"}],
        "params": {"rooms": 2, "finance_preference": "mortgage_details"},
    }


def test_prompt1_clarify_and_request_phone_have_closed_variants() -> None:
    clarify = parse_prompt1({
        "action": "clarify",
        "params": {"purpose": "life"},
        "ambiguity": {"parameter": "rooms", "reason_code": "multiple_interpretations"},
    })
    assert clarify.plain()["ambiguity"] == {"parameter": "rooms", "reason_code": "multiple_interpretations"}
    assert parse_prompt1({"action": "request_phone"}).action is Prompt1Action.REQUEST_PHONE
    with pytest.raises(CoreContractError, match="invalid_prompt1_variant_shape"):
        parse_prompt1({"action": "request_phone", "params": {}})


@pytest.mark.parametrize("raw", [
    {"action": "continue", "facts": [{"guessed": "x"}], "near": [], "missing": [], "params": {}, "ambiguity": None},
    {"action": "continue", "facts": [{"name": "+7 999 123-45-67"}], "near": [], "missing": [], "params": {}, "ambiguity": None},
    {"action": "continue", "facts": [], "near": [], "missing": [], "params": {"scenario": "x"}, "ambiguity": None},
])
def test_prompt1_rejects_guesses_private_data_and_internal_keys(raw: dict[str, object]) -> None:
    with pytest.raises(CoreContractError):
        parse_prompt1(raw)


def test_prompt2_contract_blocks_phone_flow_and_requires_one_safe_reply() -> None:
    assert parse_prompt2({"action": "reply", "response": "Ответ", "final_question": ""}).action is Prompt2Action.REPLY
    assert parse_prompt2({"action": "request_phone", "response": "", "final_question": ""}).action is Prompt2Action.REQUEST_PHONE
    with pytest.raises(CoreContractError, match="prompt2_cannot_request_phone"):
        parse_prompt2({"action": "request_phone", "response": "", "final_question": ""}, allow_request_phone=False)
    with pytest.raises(CoreContractError, match="privacy_violation"):
        parse_prompt2({"action": "reply", "response": "+7 999 123-45-67", "final_question": ""})


def test_prompt_inputs_are_explicit_and_do_not_append_current_message_to_history() -> None:
    history = [{"role": "user", "text": "старое"}, {"role": "assistant", "text": "ответ"}]
    assert build_prompt1_input("текущее", history, pending_offer="specialist_contact")["dialogue_policy"] == {"pending_offer": "specialist_contact"}
    assert "текущее" not in [item["text"] for item in history]
