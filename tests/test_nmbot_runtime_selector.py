"""Regression tests for the local, schema-versioned internal selector owner."""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from nmbot_runtime_contract.selector import (
    InvalidRuntimeVersion,
    SELECTOR_SCHEMA_VERSION,
    SelectorStore,
    SelectorUnavailable,
)


def test_selector_persists_exact_schema_and_reopens(tmp_path: Path) -> None:
    async def scenario() -> None:
        state = tmp_path / "selector.json"
        await SelectorStore(state).set("V3")
        assert json.loads(state.read_text(encoding="utf-8")) == {
            "schema_version": SELECTOR_SCHEMA_VERSION,
            "runtime_version": "V3",
        }
        assert await SelectorStore(state).get() == "V3"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "contents",
    [
        None,
        "not-json",
        json.dumps({"runtime_version": "V1"}),
        json.dumps({"schema_version": SELECTOR_SCHEMA_VERSION, "runtime_version": "V1", "extra": True}),
        json.dumps({"schema_version": "nmbot.runtime-selector.v0", "runtime_version": "V1"}),
        json.dumps({"schema_version": SELECTOR_SCHEMA_VERSION, "runtime_version": "V4"}),
    ],
    ids=("missing", "malformed", "legacy", "unknown_field", "unknown_schema", "unsupported_version"),
)
def test_selector_fails_closed_for_noncanonical_state(tmp_path: Path, contents: str | None) -> None:
    async def scenario() -> None:
        state = tmp_path / "selector.json"
        if contents is not None:
            state.write_text(contents, encoding="utf-8")
        with pytest.raises(SelectorUnavailable):
            await SelectorStore(state).get()

    asyncio.run(scenario())


def test_selector_fails_closed_when_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        state = tmp_path / "selector.json"
        await SelectorStore(state).set("V1")
        original_open = Path.open

        def denied(path: Path, *args: object, **kwargs: object):
            if path == state:
                raise PermissionError("denied")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", denied)
        with pytest.raises(SelectorUnavailable):
            await SelectorStore(state).get()

    asyncio.run(scenario())


def test_selector_rejects_v4_and_non_exact_versions(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SelectorStore(tmp_path / "selector.json")
        for version in ("V4", "v1", "V1 ", "V10", None):
            with pytest.raises(InvalidRuntimeVersion, match="invalid_runtime_version"):
                await store.set(version)  # type: ignore[arg-type]

    asyncio.run(scenario())


def test_selector_serializes_concurrent_set_and_get(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SelectorStore(tmp_path / "selector.json")
        await store.set("V0")
        writes = [asyncio.create_task(store.set(version)) for version in ("V1", "V2", "V3") * 10]
        reads = [asyncio.create_task(store.get()) for _ in range(30)]
        values = await asyncio.gather(*writes, *reads)
        assert all(value in {"V0", "V1", "V2", "V3"} for value in values)
        assert await SelectorStore(store.path).get() in {"V1", "V2", "V3"}

    asyncio.run(scenario())


def test_selector_has_no_runtime_imports() -> None:
    source = Path("nmbot_runtime_contract/selector.py").read_text(encoding="utf-8")
    modules = [node.names[0].name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import)]
    modules += [node.module or "" for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)]
    assert not any(module.startswith("nmbot_v") or module == "nmbot_runtime_adapter" for module in modules)
