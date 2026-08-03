from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Awaitable, Callable

from aiohttp import web

from nmbot_runtime_contract.wire import (
    SUPPORTED_RUNTIME_VERSIONS,
    WireContractError,
    validate_chat_response,
    validate_worker_chat_request,
    validate_worker_reset_request,
)


_SAFE_RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_RESERVED_RELEASE_IDENTITIES = frozenset({"local-v1", "replace-with-immutable-release-id"})
_LOCAL_RELEASE_PLACEHOLDER_RE = re.compile(r"local-v\d+", re.IGNORECASE)
_MAX_ACTIVE_CONVERSATION_LOCKS = 1024


@dataclass(frozen=True)
class ServiceTurn:
    response: dict[str, Any]
    state: dict[str, Any] | None = None


class AtomicStateStore:
    """A worker-private, direct conversation-ref -> state mapping."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def get(self, conversation_ref: str) -> dict[str, Any] | None:
        async with self._lock:
            return self._read().get(conversation_ref)

    async def put(self, conversation_ref: str, state: dict[str, Any]) -> None:
        async with self._lock:
            data = self._read()
            data[conversation_ref] = state
            self._write(data)

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, dict) for k, v in raw.items()):
                raise StateUnavailable("invalid_state_shape")
            return raw
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise StateUnavailable("state_unavailable") from exc
    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


class StateUnavailable(RuntimeError):
    pass


class ConversationLockUnavailable(RuntimeError):
    pass


def validate_release_identity(release_identity: str) -> str:
    """Require a safe, immutable release ID rather than a local placeholder."""
    if not isinstance(release_identity, str) or not _SAFE_RELEASE_ID_RE.fullmatch(release_identity):
        raise ValueError("invalid_release_identity")
    if release_identity.casefold() in _RESERVED_RELEASE_IDENTITIES or _LOCAL_RELEASE_PLACEHOLDER_RE.fullmatch(release_identity):
        raise ValueError("invalid_release_identity")
    return release_identity


@dataclass
class _ConversationLockEntry:
    lock: asyncio.Lock
    users: int = 0


class ConversationLockRegistry:
    """Per-conversation locks with no idle entries and a hard active-key cap."""

    def __init__(self, *, max_entries: int = _MAX_ACTIVE_CONVERSATION_LOCKS) -> None:
        self._max_entries = max_entries
        self._entries: dict[str, _ConversationLockEntry] = {}
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, conversation_ref: str):
        async with self._guard:
            entry = self._entries.get(conversation_ref)
            if entry is None:
                if len(self._entries) >= self._max_entries:
                    raise ConversationLockUnavailable("conversation_lock_capacity")
                entry = _ConversationLockEntry(asyncio.Lock())
                self._entries[conversation_ref] = entry
            entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            async with self._guard:
                entry.users -= 1
                if entry.users == 0 and self._entries.get(conversation_ref) is entry:
                    del self._entries[conversation_ref]


class SafeJournal:
    """Best-effort append-only journal; it intentionally never receives input/payloads."""

    _FIELDS = frozenset({"runtime_version", "event", "ok", "code", "stage", "action", "elapsed_ms"})

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, event: dict[str, Any]) -> None:
        try:
            safe = {key: value for key, value in event.items() if key in self._FIELDS and isinstance(value, (str, bool, int))}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(safe, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass


def create_app(*, runtime_version: str, token: str, release_identity: str, state_path: Path,
               journal_path: Path, turn: Callable[[dict[str, Any], dict[str, Any] | None], Awaitable[ServiceTurn]],
               reset: Callable[[], dict[str, Any]]) -> web.Application:
    if not token:
        raise ValueError("internal_token_required")
    if not isinstance(runtime_version, str) or runtime_version not in SUPPORTED_RUNTIME_VERSIONS:
        raise ValueError("invalid_runtime_version")
    validate_release_identity(release_identity)
    store = AtomicStateStore(state_path)
    conversation_locks = ConversationLockRegistry()
    journal = SafeJournal(journal_path)
    app = web.Application()

    def authorized(request: web.Request) -> bool:
        supplied = request.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, "Bearer " + token)

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "runtime_version": runtime_version, "release_identity": release_identity})

    async def chat(request: web.Request) -> web.Response:
        if not authorized(request):
            return web.json_response({"ok": False, "error_code": "unauthorized"}, status=401)
        try:
            payload = validate_worker_chat_request(await request.json())
        except (WireContractError, ValueError, json.JSONDecodeError):
            return web.json_response({"ok": False, "error_code": "invalid_wire"}, status=400)
        if payload["runtime_version"] != runtime_version:
            return web.json_response({"ok": False, "error_code": "runtime_version_mismatch"}, status=400)
        started = monotonic()
        try:
            async with conversation_locks.acquire(payload["conversation_ref"]):
                try:
                    before = await store.get(payload["conversation_ref"])
                except StateUnavailable:
                    return web.json_response({"ok": False, "error_code": "state_unavailable"}, status=503)
                result = await turn(payload, before)
                response = validate_chat_response(result.response, expected_version=runtime_version)
                if result.state is not None:
                    await store.put(payload["conversation_ref"], result.state)
        except (Exception, WireContractError):
            return web.json_response({"ok": False, "error_code": "runtime_failure"}, status=503)
        journal.write({"runtime_version": runtime_version, "event": "chat", "ok": response["ok"],
                       "code": response["error_code"] or "ok", "elapsed_ms": int((monotonic() - started) * 1000)})
        return web.json_response(response)

    async def reset_handler(request: web.Request) -> web.Response:
        if not authorized(request):
            return web.json_response({"ok": False, "error_code": "unauthorized"}, status=401)
        try:
            payload = validate_worker_reset_request(await request.json())
        except (WireContractError, ValueError, json.JSONDecodeError):
            return web.json_response({"ok": False, "error_code": "invalid_wire"}, status=400)
        if payload["runtime_version"] != runtime_version:
            return web.json_response({"ok": False, "error_code": "runtime_version_mismatch"}, status=400)
        try:
            async with conversation_locks.acquire(payload["conversation_ref"]):
                await store.put(payload["conversation_ref"], reset())
        except Exception:
            return web.json_response({"ok": False, "error_code": "runtime_failure"}, status=503)
        response = {"contract_version": payload["contract_version"], "ok": True, "runtime_version": runtime_version,
                    "reset": True, "error_code": None, "diagnostics": {"code": "reset"}}
        journal.write({"runtime_version": runtime_version, "event": "reset", "ok": True, "code": "reset"})
        return web.json_response(response)

    app.router.add_get("/health", health)
    app.router.add_post("/api/chat", chat)
    app.router.add_post("/api/reset", reset_handler)
    return app
