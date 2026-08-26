"""Small V6-only HTTP/Jivo API shell.

The V6 runtime is deliberately the only application runtime imported here.
Legacy selector and planner code must not be required for API startup.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.nmbot_gateway_client import OvermindClient
    from scripts.nmbot_crm_outbox import LocalCallbackOutbox
    from scripts.dialogue_journal import append_event
    from scripts.nmbot_release_identity import current_release_id
    from scripts.nmbot_v6_simple_adapter import run_v6_simple_turn
except ImportError:  # direct scripts/ execution
    from nmbot_gateway_client import OvermindClient
    from nmbot_crm_outbox import LocalCallbackOutbox
    from dialogue_journal import append_event
    from nmbot_release_identity import current_release_id
    from nmbot_v6_simple_adapter import run_v6_simple_turn
from nmbot_v6.simple_gateway import DirectTransport, SimpleGateway

DEFAULT_STATE_FILE = ROOT / "data" / "nmbot_api_state.json"
DEFAULT_RUNTIME_FILE = ROOT / "data" / "nmbot_runtime_version.json"
DEFAULT_OUTBOX = ROOT / "data" / "crm_callback_outbox"
V6_GREETING = (
    "Здравствуйте! Я помогу подобрать квартиру в Москве и области — "
    "напишите район или метро, количество комнат и бюджет."
)
CONTOUR_PROFILES = frozenset({"TEST", "PROD"})


def contour_profile(value: Any = None) -> str:
    """Return the explicit behavior profile; missing config fails safe as TEST."""
    raw = value if value is not None else os.getenv("NMBOT_CONTOUR_PROFILE", "TEST")
    profile = str(raw or "").strip().upper()
    if profile not in CONTOUR_PROFILES:
        raise RuntimeError("NMBOT_CONTOUR_PROFILE must be exactly TEST or PROD")
    return profile


def greeting_for_profile(profile: str) -> str:
    normalized = contour_profile(profile)
    return f"[TEST] {V6_GREETING}" if normalized == "TEST" else V6_GREETING


class JsonStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> dict[str, Any]:
        async with self._lock:
            if not self.path.exists():
                return {}
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            value = raw.get(key, {}) if isinstance(raw, dict) else {}
            return dict(value) if isinstance(value, dict) else {}

    async def save(self, key: str, value: Mapping[str, Any]) -> None:
        async with self._lock:
            raw: dict[str, Any] = {}
            if self.path.exists():
                try:
                    loaded = json.loads(self.path.read_text(encoding="utf-8"))
                    raw = dict(loaded) if isinstance(loaded, dict) else {}
                except (OSError, ValueError):
                    raw = {}
            raw[key] = dict(value)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)


class RuntimeVersionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def get(self) -> str:
        return "V6"

    async def set(self, value: str) -> str:
        if str(value or "").upper() != "V6":
            raise ValueError("only V6 is supported")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"runtime_version": "V6"}), encoding="utf-8")
        return "V6"


def _json(value: Any, *, status: int = 200) -> web.Response:
    return web.json_response(value, status=status, dumps=lambda item: json.dumps(item, ensure_ascii=False))


def _authorized(request: web.Request) -> bool:
    expected = os.getenv("NMBOT_API_TOKEN", "").strip()
    return not expected or request.headers.get("Authorization", "") == f"Bearer {expected}"


def _jivo_authorized(request: web.Request) -> bool:
    provider = os.getenv("JIVO_PROVIDER_TOKEN", "").strip()
    requested_provider = str(request.match_info.get("provider_token") or "").strip()
    if provider and requested_provider != provider:
        return False
    bridge = os.getenv("NMBOT_N8N_BRIDGE_TOKEN", "").strip()
    if bridge and request.headers.get("X-NMBOT-Bridge-Token", "") != bridge:
        return False
    return True


def _journal_jivo_event(payload: Mapping[str, Any], *, role: str, text: str = "", answer_kind: str | None = None) -> None:
    try:
        append_event(
            session_key=f"jivo:{payload.get('site_id')}:{payload.get('chat_id')}:{payload.get('client_id')}",
            role=role,
            text=text,
            event_id=str(payload.get("id") or "") or None,
            meta={
                "site_id": payload.get("site_id"),
                "chat_id": payload.get("chat_id"),
                "client_id": payload.get("client_id"),
            },
            answer_kind=answer_kind,
            runtime_version="V6",
        )
    except Exception:
        pass


def _start(text: str) -> bool:
    return str(text or "").strip().lower() in {"/start", "start", "/start_6"}


def _user_id(payload: Mapping[str, Any]) -> str:
    return str(payload.get("user_id") or payload.get("client_id") or "api:anonymous")[:240]


def _jivo_bot_message(payload: Mapping[str, Any], text: str) -> dict[str, Any]:
    return {
        "event": "BOT_MESSAGE",
        "client_id": payload.get("client_id"),
        "chat_id": payload.get("chat_id"),
        "message": {"type": "TEXT", "text": str(text or "")},
    }


def _jivo_invite(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"event": "INVITE_AGENT", "client_id": payload.get("client_id"), "chat_id": payload.get("chat_id")}


async def run_chat(app: web.Application, *, user_id: str, message: str, channel: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return await run_v6_simple_turn(app, user_id=user_id, message=message, channel=channel, meta=meta)


async def handle_health(request: web.Request) -> web.Response:
    return _json({
        "ok": True,
        "service": "nmbot-api",
        "runtime": "V6",
        "profile": request.app["contour_profile"],
        "release_id": request.app["release_id"],
        "api_token_configured": bool(os.getenv("NMBOT_API_TOKEN")),
    })


async def handle_runtime_version(request: web.Request) -> web.Response:
    if request.method == "POST":
        if not _authorized(request):
            return _json({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await request.json()
            version = await request.app["runtime_version_store"].set(payload.get("runtime_version", "V6"))
        except (ValueError, TypeError, json.JSONDecodeError):
            return _json({"ok": False, "error": "only_v6_supported"}, status=400)
        return _json({"ok": True, "runtime_version": version})
    return _json({"ok": True, "runtime_version": "V6"})


async def handle_chat(request: web.Request) -> web.Response:
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, TypeError):
        return _json({"ok": False, "error": "invalid_json"}, status=400)
    user_id = _user_id(payload)
    message = str(payload.get("message") or "")
    if _start(message):
        await request.app["state_store"].save(user_id, {"nmbot_v6": {}})
        return _json({"ok": True, "answer": request.app["v6_greeting"], "meta": {"runtime": "v6", "answer_kind": "start_reset", "profile": request.app["contour_profile"]}})
    result = await run_chat(request.app, user_id=user_id, message=message, channel=str(payload.get("channel") or "api"), meta=payload.get("meta") if isinstance(payload.get("meta"), dict) else {})
    return _json(result, status=200 if result.get("ok") else 502)


async def handle_reset(request: web.Request) -> web.Response:
    if not _authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    payload = await request.json()
    user_id = _user_id(payload)
    await request.app["state_store"].save(user_id, {"nmbot_v6": {}})
    return _json({"ok": True, "runtime_version": "V6"})


async def handle_jivo(request: web.Request) -> web.Response:
    if not _jivo_authorized(request):
        return _json({"ok": False, "error": "unauthorized"}, status=401)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, TypeError):
        return _json({"ok": False, "error": "invalid_json"}, status=400)
    if str(payload.get("event") or "") != "CLIENT_MESSAGE":
        return _json({"ok": True, "ignored": True})
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    text = str(message.get("text") or "")
    _journal_jivo_event(payload, role="user", text=text)
    if _start(text):
        user_id = f"jivo:{payload.get('site_id')}:{payload.get('chat_id')}:{payload.get('client_id')}"
        await request.app["state_store"].save(user_id, {"nmbot_v6": {}})
        greeting = request.app["v6_greeting"]
        _journal_jivo_event(payload, role="bot", text=greeting, answer_kind="start_reset")
        return _json(_jivo_bot_message(payload, greeting))
    result = await run_chat(request.app, user_id=f"jivo:{payload.get('site_id')}:{payload.get('chat_id')}:{payload.get('client_id')}", message=text, channel="jivo", meta={"event_id": payload.get("id"), "agents_online": payload.get("agents_online")})
    if result.get("handoff_to_operator"):
        _journal_jivo_event(payload, role="bot", answer_kind="invite_agent")
        return _json(_jivo_invite(payload))
    _journal_jivo_event(payload, role="bot", text=str(result.get("answer") or ""), answer_kind=str(result.get("intent") or "v6"))
    return _json(_jivo_bot_message(payload, result.get("answer", "")))


async def close_app(app: web.Application) -> None:
    client = app.get("overmind_client")
    close = getattr(client, "close", None)
    if close:
        await close()


def create_app() -> web.Application:
    app = web.Application()
    profile = contour_profile()
    app["contour_profile"] = profile
    app["v6_greeting"] = greeting_for_profile(profile)
    app["release_id"] = current_release_id()
    app["state_store"] = JsonStateStore(Path(os.getenv("NMBOT_API_STATE_FILE", str(DEFAULT_STATE_FILE))).expanduser())
    app["runtime_version_store"] = RuntimeVersionStore(Path(os.getenv("NMBOT_RUNTIME_VERSION_FILE", str(DEFAULT_RUNTIME_FILE))).expanduser())
    app["overmind_client"] = OvermindClient()
    transport = DirectTransport(app["overmind_client"])
    app["v6_simple_prompt1_port"] = SimpleGateway(transport, "prompt1")
    app["v6_simple_prompt2_port"] = SimpleGateway(transport, "prompt2")
    app["v6_callback_outbox"] = LocalCallbackOutbox(Path(os.getenv("NMBOT_CALLBACK_OUTBOX_DIR", str(DEFAULT_OUTBOX))).expanduser())
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/runtime-version", handle_runtime_version)
    app.router.add_post("/api/runtime-version", handle_runtime_version)
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_post("/api/reset", handle_reset)
    app.router.add_post("/jivo/{provider_token}", handle_jivo)
    app.on_cleanup.append(close_app)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="V6-only nmbot API")
    parser.add_argument("--host", default=os.getenv("NMBOT_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("NMBOT_API_PORT", "8088")))
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
