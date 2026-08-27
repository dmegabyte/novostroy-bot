from __future__ import annotations

from pathlib import Path

import pytest

from nmbot_core import (
    CoreContractError,
    CoreState,
    PhoneParseResult,
    PrivatePhone,
    Prompt1Action,
    Prompt1Document,
    Prompt2Action,
    Prompt2Document,
)


def test_core_has_no_legacy_runtime_import() -> None:
    package = Path(__file__).parents[1] / "nmbot_core"
    assert "nmbot_v6" not in "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))


def test_state_v2_round_trip_preserves_public_shape() -> None:
    state = CoreState(
        revision=2,
        history=({"role": "user", "text": "Ищу квартиру"}, {"role": "assistant", "text": "Уточните район"}),
        awaiting_phone=True,
        client_turn_count=3,
        pending_offer="specialist_contact",
    )

    assert CoreState.from_mapping(state.plain()).plain() == state.plain()


@pytest.mark.parametrize("factory", [
    lambda: Prompt1Document(action="unknown"),  # type: ignore[arg-type]
    lambda: Prompt2Document(action="unknown", response="Текст", final_question=""),  # type: ignore[arg-type]
])
def test_prompt_actions_reject_unknown_values(factory) -> None:
    with pytest.raises(CoreContractError):
        factory()


def test_prompt_documents_accept_explicit_v6_actions() -> None:
    assert Prompt1Document(action=Prompt1Action.CONTINUE).action is Prompt1Action.CONTINUE
    assert Prompt2Document(action=Prompt2Action.REPLY, response="Ответ", final_question="").action is Prompt2Action.REPLY


def test_private_phone_never_leaks_from_repr_or_safe_projection() -> None:
    phone = PrivatePhone("+79990000000")
    result = PhoneParseResult(True, phone, "recognized")

    assert "+79990000000" not in repr(phone)
    assert result.safe_projection() == {"recognized": True, "code": "recognized"}
