"""Local, strict persistence for the internal runtime-version selector.

This module deliberately depends only on the standard library and the wire
contract.  Runtime implementations must neither import nor own selector state.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .wire import SUPPORTED_RUNTIME_VERSIONS


SELECTOR_SCHEMA_VERSION = "nmbot.runtime-selector.v1"
_SELECTOR_FIELDS = frozenset({"schema_version", "runtime_version"})


class SelectorUnavailable(RuntimeError):
    """Selector state is absent or invalid; callers must fail closed."""


class InvalidRuntimeVersion(ValueError):
    """Raised when a requested selector version is not an exact supported version."""

    def __init__(self) -> None:
        super().__init__("invalid_runtime_version")


def _validate_selector(payload: Any) -> str:
    if not isinstance(payload, dict) or set(payload) != _SELECTOR_FIELDS:
        raise SelectorUnavailable()
    if payload.get("schema_version") != SELECTOR_SCHEMA_VERSION:
        raise SelectorUnavailable()
    version = payload.get("runtime_version")
    if not isinstance(version, str) or version not in SUPPORTED_RUNTIME_VERSIONS:
        raise SelectorUnavailable()
    return version


class SelectorStore:
    """Process-local locked, atomically persisted selector state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        async with self._lock:
            try:
                with self.path.open("r", encoding="utf-8") as state_file:
                    return _validate_selector(json.load(state_file))
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise SelectorUnavailable() from exc

    async def set(self, version: str) -> str:
        if not isinstance(version, str) or version not in SUPPORTED_RUNTIME_VERSIONS:
            raise InvalidRuntimeVersion()
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                    json.dump(
                        {"schema_version": SELECTOR_SCHEMA_VERSION, "runtime_version": version},
                        temporary,
                        separators=(",", ":"),
                    )
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, self.path)
                self._fsync_directory()
            except BaseException:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
                raise
            return version

    def _fsync_directory(self) -> None:
        """Persist the replacement directory entry where the platform supports it."""
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(self.path.parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
