from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nmbot_v3.contracts import ExecutableTurnV3, IntentGoalV3, V3PlannerContext, V3SemanticAction, V3SemanticStage
from nmbot_v3.ports import (
    V3PlannerPort,
    V3PlannerRequest,
    V3EvidenceSearchPort,
    V3ProviderError,
    V3ProviderErrorCode,
    V3RedactedText,
    V3SearchHit,
    V3SearchPort,
    V3SearchRequest,
    V3SearchResult,
    V3WriterPort,
    V3WriterRequest,
    V3WriterResult,
)


def turn() -> ExecutableTurnV3:
    return ExecutableTurnV3(IntentGoalV3.NEW_SEARCH, V3SemanticStage.FIRST_LIST, V3SemanticAction.SEARCH)


def test_provider_port_dtos_are_closed_and_redact_boundary_text() -> None:
    text = V3RedactedText("Пишите a@example.com, +7 999 111-22-33; token=secret-value")
    assert text.text == "Пишите [redacted-email], [redacted-contact]; [redacted-credential]"

    request = V3PlannerRequest(text, V3PlannerContext())
    hit = V3SearchHit("source:42", V3RedactedText("ЖК Лучи"), V3RedactedText("от 10 млн"))
    result = V3SearchResult((hit,))
    assert request.context.visible_option_refs == ()
    executable_turn = turn()
    assert V3SearchRequest(executable_turn).turn is executable_turn  # constructor rejects unrelated data, not a copy
    assert V3WriterRequest(executable_turn, result).search_result == result
    assert V3WriterResult(V3RedactedText("Готово")).answer.text == "Готово"
    assert V3ProviderError(V3ProviderErrorCode.TIMEOUT, True).retryable is True
    with pytest.raises(ValueError, match="invalid_search_reference"):
        V3SearchHit("unsafe ref", V3RedactedText("title"), V3RedactedText("summary"))
    with pytest.raises(ValueError, match="invalid_redacted_text"):
        V3RedactedText(" ")


def test_phone_redaction_scope_is_russian_numbers_only() -> None:
    assert V3RedactedText("Телефон 8 (999) 111-22-33").text == "Телефон [redacted-contact]"
    assert V3RedactedText("Reference +1 202 555 0100").text == "Reference +1 202 555 0100"


def test_provider_ports_are_async_structural_contracts() -> None:
    assert getattr(V3PlannerPort.plan, "__annotations__")["return"] == "V3PlannerPortResult"
    assert getattr(V3SearchPort.search, "__annotations__")["return"] == "V3SearchPortResult"
    assert getattr(V3EvidenceSearchPort.search, "__annotations__")["return"] == "V3EvidenceSearchPortResult"
    assert getattr(V3WriterPort.write, "__annotations__")["return"] == "V3WriterPortResult"


def test_v3_ast_import_closure_keeps_provider_boundary_version_isolated() -> None:
    package = Path("nmbot_v3")
    banned = ("nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4", "scripts", "followup_intent_classifier")
    imports: set[str] = set()
    for source_path in package.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imports.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        imports.update(node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
    assert not any(import_name == name or import_name.startswith(name + ".") for import_name in imports for name in banned)

    ports_tree = ast.parse((package / "ports.py").read_text(encoding="utf-8"))
    ports_imports = {node.module or "" for node in ast.walk(ports_tree) if isinstance(node, ast.ImportFrom)}
    assert ports_imports == {"__future__", "dataclasses", "enum", "typing", "contracts", "evidence_contract"}
