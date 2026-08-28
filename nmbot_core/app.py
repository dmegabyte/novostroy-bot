"""Thin V6-only HTTP/Jivo shell around the canonical core."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import hmac
import json
import os
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping

from aiohttp import web

from .journal import append_event
from .outbox import LocalCallbackOutbox
from .runtime import CoreRuntime, PHONE_QUESTION, TECHNICAL_TEXT
from .state import CoreState
from .gateway import build_prompt_pair


V6_GREETING = "Здравствуйте! Я помогу подобрать квартиру в Москве и области — напишите район или метро, количество комнат и бюджет."
CANONICAL_STATE_KEY = "core"
_RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class JsonStateStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._sessions: dict[str, asyncio.Lock] = {}
        self._session_lock_dir = self.path.parent / f".{self.path.name}.sessions"
        self._session_lock_dir.mkdir(mode=0o700, exist_ok=True)

    def _locked_file(self) -> int:
        descriptor = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor

    def _locked_session(self, key: str) -> int:
        token = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
        descriptor = os.open(self._session_lock_dir / f"{token}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor

    async def get(self, key: str) -> dict[str, Any]:
        async with self._lock:
            descriptor = self._locked_file()
            try:
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except (FileNotFoundError, OSError, ValueError):
                    data = {}
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            value = data.get(key, {}) if isinstance(data, dict) else {}
            return dict(value) if isinstance(value, dict) else {}

    async def save(self, key: str, value: Mapping[str, Any]) -> None:
        async with self._lock:
            lock_descriptor = self._locked_file()
            try:
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
            finally:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
                os.close(lock_descriptor)

    @asynccontextmanager
    async def session_lock(self, key: str):
        token = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
        local_lock = self._sessions.setdefault(token, asyncio.Lock())
        async with local_lock:
            descriptor = await asyncio.to_thread(self._locked_session, key)
            try:
                yield
            finally:
                await asyncio.to_thread(fcntl.flock, descriptor, fcntl.LOCK_UN)
                os.close(descriptor)


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


def _authorized(request: web.Request) -> bool:
    expected = request.app["api_token"]
    if request.app["profile"] == "PROD" and not expected:
        return False
    return not expected or hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {expected}")


def _jivo_authorized(request: web.Request) -> bool:
    provider, bridge = request.app["provider_token"], request.app["bridge_token"]
    if request.app["profile"] == "PROD" and (not provider or not bridge):
        return False
    return ((not provider or hmac.compare_digest(str(request.match_info.get("provider_token") or ""), provider)) and (not bridge or hmac.compare_digest(request.headers.get("X-NMBOT-Bridge-Token", ""), bridge)))


def _required_env(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise RuntimeError(f"missing_configuration:{key}")
    return value


def _release_id_from_environment() -> str:
    target = Path(_required_env("NMBOT_RELEASE_IDENTITY_FILE"))
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("invalid_release_identity") from exc
    release_id = str(payload.get("release_id") or "").strip() if isinstance(payload, Mapping) and payload.get("schema") == "nmbot.release_identity.v1" else ""
    if not _RELEASE_ID.fullmatch(release_id):
        raise RuntimeError("invalid_release_identity")
    return release_id


async def _turn(app: web.Application, user_id: str, message: str, channel: str, meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    async with app["state_store"].session_lock(user_id):
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


def create_app(*, prompt1: Any, prompt2: Any, state_path: Path | str, outbox_path: Path | str, journal_path: Path | str, profile: str = "TEST", release_id: str = "", api_token: str = "", provider_token: str = "", bridge_token: str = "") -> web.Application:
    app = web.Application()
    profile = _profile(profile)
    app.update({"profile": profile, "release_id": release_id, "api_token": str(api_token), "provider_token": str(provider_token), "bridge_token": str(bridge_token), "state_store": JsonStateStore(state_path), "outbox": LocalCallbackOutbox(outbox_path), "journal_path": Path(journal_path), "runtime": CoreRuntime(prompt1, prompt2)})

    async def health(request: web.Request) -> web.Response:
        return _reply({"ok": True, "service": "nmbot-api", "runtime": "V6", "profile": profile, "release_id": release_id})

    async def chat(request: web.Request) -> web.Response:
        if not _authorized(request): return _reply({"ok": False, "error": "unauthorized"}, 401)
        try: payload = await request.json()
        except (json.JSONDecodeError, TypeError): return _reply({"ok": False, "error": "invalid_json"}, 400)
        message, user_id = str(payload.get("message") or ""), str(payload.get("user_id") or "api:anonymous")[:240]
        if _start(message):
            async with app["state_store"].session_lock(user_id):
                await app["state_store"].save(user_id, {CANONICAL_STATE_KEY: CoreState().plain()})
            return _reply({"ok": True, "answer": ("[TEST] " if profile == "TEST" else "") + V6_GREETING, "meta": {"runtime": "v6", "status": "start_reset"}})
        return _reply(await _turn(app, user_id, message, "api", payload.get("meta") if isinstance(payload.get("meta"), dict) else {}))

    async def reset(request: web.Request) -> web.Response:
        if not _authorized(request): return _reply({"ok": False, "error": "unauthorized"}, 401)
        try: payload = await request.json()
        except (json.JSONDecodeError, TypeError): return _reply({"ok": False, "error": "invalid_json"}, 400)
        user_id = str(payload.get("user_id") or "api:anonymous")[:240]
        async with app["state_store"].session_lock(user_id):
            await app["state_store"].save(user_id, {CANONICAL_STATE_KEY: CoreState().plain()})
        return _reply({"ok": True, "runtime_version": "V6"})

    async def jivo(request: web.Request) -> web.Response:
        if not _jivo_authorized(request): return _reply({"ok": False, "error": "unauthorized"}, 401)
        try: payload = await request.json()
        except (json.JSONDecodeError, TypeError): return _reply({"ok": False, "error": "invalid_json"}, 400)
        if payload.get("event") != "CLIENT_MESSAGE": return _reply({"ok": True, "ignored": True})
        text = str((payload.get("message") or {}).get("text") or "")
        key = f"jivo:{payload.get('site_id')}:{payload.get('chat_id')}:{payload.get('client_id')}"
        append_event(key, "user", text, event_id=str(payload.get("id") or ""), refs=payload, path=app["journal_path"], release_id=release_id)
        result = await _turn(app, key, text, "jivo", {"event_id": payload.get("id")})
        append_event(key, "bot", result["answer"], event_id=str(payload.get("id") or ""), refs=payload, path=app["journal_path"], release_id=release_id)
        return _reply(_jivo_bot(payload, result["answer"]))

    app.router.add_get("/health", health); app.router.add_post("/api/chat", chat); app.router.add_post("/api/reset", reset); app.router.add_post("/jivo/{provider_token}", jivo)
    return app


def create_app_from_environment(root: Path | str) -> web.Application:
    """Build the canonical API only from the existing release env contract."""
    release_root = Path(root)
    prompt1_path = release_root / "prompts" / "v6_simple_search_agent.txt"
    prompt2_path = release_root / "prompts" / "v6_simple_answer_writer.txt"
    try:
        system_prompt1, system_prompt2 = prompt1_path.read_text(encoding="utf-8"), prompt2_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("canonical_prompt_missing") from exc
    prompt1, prompt2 = build_prompt_pair(base_url=_required_env("OVERMIND_URL"), system_prompt1=system_prompt1, system_prompt2=system_prompt2)
    return create_app(
        prompt1=prompt1,
        prompt2=prompt2,
        state_path=_required_env("NMBOT_API_STATE_FILE"),
        outbox_path=_required_env("NMBOT_CALLBACK_OUTBOX_DIR"),
        journal_path=_required_env("NMBOT_DIALOGUE_JOURNAL"),
        profile=os.getenv("NMBOT_CONTOUR_PROFILE", "TEST"),
        release_id=_release_id_from_environment(),
        api_token=os.getenv("NMBOT_API_TOKEN", ""),
        provider_token=os.getenv("JIVO_PROVIDER_TOKEN", ""),
        bridge_token=os.getenv("NMBOT_N8N_BRIDGE_TOKEN", ""),
    )
