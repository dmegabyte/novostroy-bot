"""Canonical V6 application core.

This package deliberately owns no version selector and imports no legacy runtime.
"""

from .contract import (
    Ambiguity,
    CoreContractError,
    Prompt1Action,
    Prompt1Document,
    Prompt2Action,
    Prompt2Document,
    TerminalResponse,
    TurnInput,
    build_prompt1_input,
    build_prompt2_input,
    parse_prompt1,
    parse_prompt2,
)
from .phone import PhoneParseResult, PrivatePhone, parse_phone
from .state import CoreState, SCHEMA_VERSION

__all__ = [
    "CoreContractError",
    "Ambiguity",
    "CoreState",
    "PhoneParseResult",
    "PrivatePhone",
    "Prompt1Action",
    "Prompt1Document",
    "Prompt2Action",
    "Prompt2Document",
    "SCHEMA_VERSION",
    "TerminalResponse",
    "TurnInput",
    "build_prompt1_input",
    "build_prompt2_input",
    "parse_prompt1",
    "parse_prompt2",
    "parse_phone",
]
