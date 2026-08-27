"""Canonical V6 application core.

This package deliberately owns no version selector and imports no legacy runtime.
"""

from .contract import (
    CoreContractError,
    Prompt1Action,
    Prompt1Document,
    Prompt2Action,
    Prompt2Document,
    TerminalResponse,
    TurnInput,
)
from .phone import PhoneParseResult, PrivatePhone
from .state import CoreState, SCHEMA_VERSION

__all__ = [
    "CoreContractError",
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
]
