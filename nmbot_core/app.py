"""Thin V6-only HTTP/Jivo shell around the canonical core."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from aiohttp import web

from .journal import append_event
from .outbox import LocalCallbackOutbox
from .runtime import CoreRuntime, PHONE_QUESTION, TECHNICAL_TEXT
from .state import CoreState


V6_GREETING = "Здравствуйте! Я помогу подобрать квартиру в Москве и области — напишите район или метро, количество комнат и бюджет."
CANONICAL_STATE_KEY = "core"


class JsonStateStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._sessions: dict[str, asyncio.Lock] = {}

    async def get(self, key: str) -> dict[str, Any]:
        async with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, ValueError):
                data = {}
            value = data.get(key, {}) if isinstance(data, dict) else {}
            return dict(value) if isinstance(value, dict) else {}

    async def save(self, key: str, value: Mapping[str, Any]) -> None:
        async with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, ValueError):
                data = {}
            data[str(key)] = dict(value)
            descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent))
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False, sort_keys=True)
                    handle.flush(); os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)

    async def session_lock(self, key: str):
        return self._sessions.setdefault(str(key), asyncio.Lock())


def _profile(value: str) -> str:
    profile = str(value or "TEST").upper()
    if profile not in {"TEST", "PROD"}: raise RuntimeError("profile must be TEST or PROD")
    return profile


def _reply(result: dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(result, status=status, dumps=lambda value: json.dumps(value, ensure_ascii=False))


def _start(value: str) -> bool:
    return str(value).strip().lower() in {"/start", "start", "/start_6"}


def _jivo_bot(payload: Mapping[str, Any], text: str) -> dict[str, Any]:
    return {"event": "BOT_MESSAGE", "client_id": payload.get("client_id"), "chat_id": payload.get("chat_id"), "message": {"type": "TEXT", "text": text}}


async def _turn(app: web.Application, user_id: str, message: str, channel: str, meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    lock = await app["state_store"].session_lock(user_id)
    async with lock:
        envelope = await app["state_store"].get(user_id)
        raw_state = envelope.get(CANONICAL_STATE_KEY)
        state = CoreState.from_mapping(raw_state) if isinstance(raw_state, Mapping) and raw_state else CoreState()
        result = await app["runtime"].run(message, state)
        if result.status == "phone" and result.private_phone:
            try:
                queued = app["outbox"].enqueue(session_key=user_id, event_id=str((meta or {}).get("event_id") or ""), normalized_phone=result.private_phone.reveal_for_private_storage(), context={"runtime": "v6", "channel": channel, "dialogue_excerpt": list(state.history[-6:])})
            except Exception:
                return {"ok": False, "answer": "Не удалось сохранить номер. Пожалуйста, отправьте его ещё раз.", "intent": "v6", "awaiting_phone": True, "meta": {"runtime": "v6", "status": "outbox_failure"}}
            next_state = state.phone_accepted()
            await app["state_store"].save(user_id, {**envelope, CANONICAL_STATE_KEY: next_state.plain()})
            return {"ok": True, "answer": "Спасибо, специалист свяжется с вами.", "intent": "v6", "awaiting_phone": False, "meta": {"runtime": "v6", "status": "phone_accepted", "outbox_enqueue": queued.status, "state_commit": True}}
        if result.status in {"completed", "safe_failure"}:
            await app["state_store"].save(user_id, {**envelope, CANONICAL_STATE_KEY: result.state.plain()})
        return {"ok": result.status in {"completed", "safe_failure", "multiple_phones", "invalid_phone"}, "answer": result.text or TECHNICAL_TEXT, "intent": "v6", "awaiting_phone": result.state.awaiting_phone, "handoff_to_operator": False, "buttons": [], "meta": {"runtime": "v6", "status": result.status, "state_commit": result.status in {"completed", "safe_failure"}, "model_calls": result.model_calls, "url_card_status": result.url_card_status}}


def create_app(*, prompt1: Any, prompt2: Any, state_path: Path | str, outbox_path: Path | str, journal_path: Path | str, profile: str = "TEST", release_id: str = "") -> web.Application:
    app = web.Application()
    profile = _profile(profile)
    app.update({"profile": profile, "release_id": release_id, "state_store": JsonStateStore(state_path), "outbox": LocalCallbackOutbox(outbox_path), "journal_path": Path(journal_path), "runtime": CoreRuntime(prompt1, prompt2)})

    async def health(request: web.Request) -> web.Response:
        return _reply({"ok": True, "service": "nmbot-api", "runtime": "V6", "profile": profile, "release_id": release_id})

    async def chat(request: web.Request) -> web.Response:
        try: payload = await request.json()
        except (json.JSONDecodeError, TypeError): return _reply({"ok": False, "error": "invalid_json"}, 400)
        message, user_id = str(payload.get("message") or ""), str(payload.get("user_id") or "api:anonymous")[:240]
        if _start(message):
            await app["state_store"].save(user_id, {CANONICAL_STATE_KEY: CoreState().plain()})
            return _reply({"ok": True, "answer": ("[TEST] " if profile == "TEST" else "") + V6_GREETING, "meta": {"runtime": "v6", "status": "start_reset"}})
        return _reply(await _turn(app, user_id, message, "api", payload.get("meta") if isinstance(payload.get("meta"), dict) else {}))

    async def jivo(request: web.Request) -> web.Response:
        try: payload = await request.json()
        except (json.JSONDecodeError, TypeError): return _reply({"ok": False, "error": "invalid_json"}, 400)
        if payload.get("event") != "CLIENT_MESSAGE": return _reply({"ok": True, "ignored": True})
        text = str((payload.get("message") or {}).get("text") or "")
        key = f"jivo:{payload.get('site_id')}:{payload.get('chat_id')}:{payload.get('client_id')}"
        append_event(key, "user", text, event_id=str(payload.get("id") or ""), refs=payload, path=app["journal_path"], release_id=release_id)
        result = await _turn(app, key, text, "jivo", {"event_id": payload.get("id")})
        append_event(key, "bot", result["answer"], event_id=str(payload.get("id") or ""), refs=payload, path=app["journal_path"], release_id=release_id)
        return _reply(_jivo_bot(payload, result["answer"]))

    app.router.add_get("/health", health); app.router.add_post("/api/chat", chat); app.router.add_post("/jivo/{provider_token}", jivo)
    return app
