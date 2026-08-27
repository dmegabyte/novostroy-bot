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
    parse_phone,
)


class PhoneBackend:
    def parse(self, candidate, region):
        return "".join(char for char in candidate if char.isdigit())

    def is_possible_number(self, parsed):
        return len(parsed) in {10, 11}

    def is_valid_number(self, parsed):
        return (len(parsed) == 10 and parsed.startswith("9")) or (len(parsed) == 11 and parsed[:2] in {"79", "89"})

    def format_e164(self, parsed):
        return "+7" + parsed[-10:]


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


@pytest.mark.parametrize("value", ["+7 999 123-45-67", "8 (999) 123-45-67", "9991234567", "79991234567"])
def test_core_phone_parser_accepts_only_valid_russian_formats(value: str) -> None:
    result = parse_phone(value, PhoneBackend())

    assert result.recognized is True
    assert result.safe_projection() == {"recognized": True, "code": "recognized"}
    assert result.private_phone is not None
    assert result.private_phone.reveal_for_private_storage() == "+79991234567"


def test_core_phone_parser_rejects_numeric_non_phone() -> None:
    assert parse_phone("Бюджет 18000000", PhoneBackend()).safe_projection() == {"recognized": False, "code": "not_found"}


def test_state_rejects_phone_in_history_and_transitions_privately() -> None:
    with pytest.raises(CoreContractError, match="invalid_history_text"):
        CoreState(history=({"role": "user", "text": "+79991234567"}, {"role": "assistant", "text": "Спасибо"}))

    state = CoreState().accepted("Ищу квартиру", "Какой район?", awaiting_phone=True, pending_offer="specialist_contact")
    assert state.revision == 1 and state.awaiting_phone is True
    assert state.phone_accepted().plain() == {
        "schema_version": 2,
        "revision": 2,
        "history": [{"role": "user", "text": "Ищу квартиру"}, {"role": "assistant", "text": "Какой район?"}],
        "awaiting_phone": False,
        "client_turn_count": 2,
        "pending_offer": "none",
    }
