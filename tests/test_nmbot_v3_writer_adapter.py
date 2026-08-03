from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

from nmbot_v3.presentation import V3WriterBriefInput
from nmbot_v3.writer_adapter import (
    V3WriterAdapter,
    V3_WRITER_GATEWAY_RESULT_MARKER,
    build_v3_structured_writer_request,
)


FIXTURE = json.loads(Path("tests/fixtures/v3_writer_differential_overlap.json").read_text(encoding="utf-8"))


def _brief() -> V3WriterBriefInput:
    return V3WriterBriefInput(**FIXTURE["brief"])


def test_writer_adapter_constructs_closed_redacted_request() -> None:
    source = V3WriterBriefInput(
        client_request="Напишите на a@example.com; token=private",
        answer_goal="present_search_results",
        cards=(),
    )
    request = build_v3_structured_writer_request(source)
    payload = request.to_payload()

    assert payload["schema_version"] == "v3_writer_request_v1"
    assert payload["writer_brief"]["client_request"] == "Напишите на [redacted-email]; [redacted-credential]"
    assert "raw_payload" not in request.to_json()


def test_writer_adapter_accepts_only_valid_closed_structured_output() -> None:
    async def scenario() -> None:
        seen = []

        class Writer:
            async def write(self, request):
                seen.append(request)
                return json.dumps({"result_marker": V3_WRITER_GATEWAY_RESULT_MARKER, "output": FIXTURE["valid_output"]}, ensure_ascii=False)

        result = await V3WriterAdapter(Writer()).write(_brief())
        assert result.ok is True
        assert result.errors == ()
        assert tuple(card.name for card in result.output.cards) == ("ЖК Первый", "ЖК Второй")
        assert seen[0].to_payload()["writer_brief"]["card_names_in_order"] == ["ЖК Первый", "ЖК Второй"]
    asyncio.run(scenario())


def test_writer_adapter_differential_overlap_fixture_falls_back_for_invalid_or_unavailable_writer() -> None:
    async def scenario() -> None:
        class InvalidWriter:
            async def write(self, request):
                return FIXTURE["invalid_output"]

        class UnavailableWriter:
            async def write(self, request):
                raise RuntimeError("token=leaked-provider-detail")

        for writer, expected_error in ((InvalidWriter(), "writer_invalid_output"), (UnavailableWriter(), "writer_unavailable")):
            result = await V3WriterAdapter(writer).write(_brief())
            assert result.ok is False
            assert result.errors == (expected_error,)
            assert result.public_text == FIXTURE["fallback"]
            assert "leaked" not in result.public_text
    asyncio.run(scenario())


def test_writer_adapter_rejects_pii_and_has_no_legacy_or_network_imports() -> None:
    async def scenario() -> None:
        class UnsafeWriter:
            async def write(self, request):
                return {"result_marker": V3_WRITER_GATEWAY_RESULT_MARKER, "output": {
                    **FIXTURE["valid_output"], "intro": "Позвоните +7 999 123-45-67",
                }}

        result = await V3WriterAdapter(UnsafeWriter()).write(_brief())
        assert result.ok is False
        assert result.errors == ("writer_invalid_output",)
    asyncio.run(scenario())

    tree = ast.parse(Path("nmbot_v3/writer_adapter.py").read_text(encoding="utf-8"))
    imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imports += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    banned = ("nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4", "requests", "aiohttp", "http", "socket", "runtime", "service", "selector")
    assert not any(name == blocked or name.startswith(blocked + ".") for name in imports for blocked in banned)
