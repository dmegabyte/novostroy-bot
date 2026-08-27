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
    project_url_card_for_prompt2,
)
from .phone import PhoneParseResult, PrivatePhone, parse_phone
from .gateway import DirectTransport, GatewayResult, PromptGateway, ToolTrace
from .runtime import CoreRuntime, RuntimeResult
from .state import CoreState, SCHEMA_VERSION
from .journal import JournalError, append_event
from .outbox import LocalCallbackOutbox, OutboxResult
from .url_card import UrlCardError, extract_novostroy_url, fetch_card, parse_html_card, validate_source_url

__all__ = [
    "CoreContractError",
    "CoreRuntime",
    "DirectTransport",
    "GatewayResult",
    "JournalError",
    "LocalCallbackOutbox",
    "Ambiguity",
    "CoreState",
    "PhoneParseResult",
    "PrivatePhone",
    "Prompt1Action",
    "Prompt1Document",
    "Prompt2Action",
    "Prompt2Document",
    "PromptGateway",
    "OutboxResult",
    "RuntimeResult",
    "SCHEMA_VERSION",
    "TerminalResponse",
    "TurnInput",
    "ToolTrace",
    "UrlCardError",
    "build_prompt1_input",
    "build_prompt2_input",
    "append_event",
    "parse_prompt1",
    "parse_prompt2",
    "parse_phone",
    "project_url_card_for_prompt2",
    "extract_novostroy_url",
    "fetch_card",
    "parse_html_card",
    "validate_source_url",
]
