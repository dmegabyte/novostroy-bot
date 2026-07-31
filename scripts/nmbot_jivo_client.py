#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request
from urllib.parse import urljoin


DEFAULT_BASE_URL = "http://127.0.0.1:8088"
DEFAULT_MAX_TURNS = 5
DEFAULT_TIMEOUT_SEC = 90.0
TOKEN_ENV = "JIVO_PROVIDER_TOKEN"


class SafeClientError(Exception):
    """Error whose message is safe to print to a terminal."""


@dataclass(frozen=True)
class SyntheticSession:
    site_id: str
    chat_id: str
    client_id: str
    sender_id: str
    channel_id: str


@dataclass(frozen=True)
class ClientConfig:
    base_url: str
    live: bool
    max_turns: int
    timeout: float
    dry_run: str | None


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def parse_args(argv: list[str] | None = None) -> ClientConfig:
    parser = argparse.ArgumentParser(
        description="Safe local CLI that emulates a synthetic Jivo CLIENT_MESSAGE client."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"API base URL; default: {DEFAULT_BASE_URL}")
    parser.add_argument("--live", action="store_true", help="Actually POST to the API. Without this flag no network call is made.")
    parser.add_argument("--max-turns", type=positive_int, default=DEFAULT_MAX_TURNS, help="Maximum live turns for this run.")
    parser.add_argument("--timeout", type=positive_float, default=DEFAULT_TIMEOUT_SEC, help="HTTP request timeout in seconds.")
    parser.add_argument("--dry-run", metavar="TEXT", help="Build a payload locally and print safe metadata only.")
    args = parser.parse_args(argv)
    return ClientConfig(
        base_url=str(args.base_url).rstrip("/"),
        live=bool(args.live),
        max_turns=args.max_turns,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


def make_synthetic_session(seed: str | None = None) -> SyntheticSession:
    session_id = seed or uuid.uuid4().hex
    short = session_id[:12]
    return SyntheticSession(
        site_id=f"test-only-nmbot-site-{short}",
        chat_id=f"test-only-nmbot-chat-{short}",
        client_id=f"test-only-nmbot-client-{short}",
        sender_id=f"test-only-nmbot-sender-{short}",
        channel_id=f"test-only-nmbot-channel-{short}",
    )


def build_client_message(text: str, session: SyntheticSession, *, event_id: str | None = None, now: int | None = None) -> dict[str, Any]:
    return {
        "event": "CLIENT_MESSAGE",
        "id": event_id or str(uuid.uuid4()),
        "site_id": session.site_id,
        "client_id": session.client_id,
        "chat_id": session.chat_id,
        "agents_online": True,
        "sender": {
            "id": session.sender_id,
            "name": "Synthetic nmbot test client",
            "url": "https://example.invalid/nmbot-jivo-client",
            "has_contacts": False,
        },
        "message": {
            "type": "TEXT",
            "text": text,
            "timestamp": int(now if now is not None else time.time()),
        },
        "channel": {
            "id": session.channel_id,
            "type": "widget",
        },
    }


def _hash_ref(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def safe_payload_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return {
        "event": payload.get("event"),
        "event_id": payload.get("id"),
        "site_ref": _hash_ref(payload.get("site_id")),
        "chat_ref": _hash_ref(payload.get("chat_id")),
        "client_ref": _hash_ref(payload.get("client_id")),
        "message_type": message.get("type"),
        "text_length": len(str(message.get("text") or "")),
    }


def normalize_response(data: dict[str, Any]) -> tuple[str, str]:
    event = str(data.get("event") or "")
    if event == "BOT_MESSAGE":
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        text = str(message.get("text") or "")
        return "bot", text or "[BOT_MESSAGE без текста]"
    if event == "INVITE_AGENT":
        return "handoff", "Бот попросил подключить оператора."
    if data.get("ok") is False:
        return "error", f"Сервер вернул контролируемую ошибку: {str(data.get('error') or 'unknown_error')}"
    return "error", "Сервер вернул неожиданный безопасно скрытый ответ."


def endpoint_url(base_url: str, provider_token: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", f"jivo/{provider_token}")


def live_request_allowed(live: bool, provider_token: str | None) -> str:
    if not live:
        raise SafeClientError("Offline mode: no network request was made. Pass --live to send to the API.")
    token = str(provider_token or "").strip()
    if not token:
        raise SafeClientError(f"Missing {TOKEN_ENV}; no network request was made.")
    return token


def post_client_message(
    payload: dict[str, Any],
    *,
    base_url: str,
    provider_token: str,
    timeout: float,
    opener: Callable[..., Any] = request.urlopen,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        endpoint_url(base_url, provider_token),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(req, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 200))
            raw = resp.read()
    except TimeoutError as exc:
        raise SafeClientError("Network timeout; response body hidden.") from exc
    except error.HTTPError as exc:
        raise SafeClientError(f"HTTP error {exc.code}; response body hidden.") from exc
    except error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError):
            raise SafeClientError("Network timeout; response body hidden.") from exc
        raise SafeClientError("Network error; details hidden.") from exc
    except OSError as exc:
        raise SafeClientError("Network error; details hidden.") from exc
    if status >= 400:
        raise SafeClientError(f"HTTP error {status}; response body hidden.")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SafeClientError("Server returned non-JSON response; body hidden.") from exc
    if not isinstance(parsed, dict):
        raise SafeClientError("Server returned unexpected JSON shape; body hidden.")
    return parsed


def print_dry_run(text: str, session: SyntheticSession) -> int:
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"nmbot-jivo-dry-run:{text}"))
    payload = build_client_message(text, session, event_id=event_id, now=0)
    print(json.dumps(safe_payload_metadata(payload), ensure_ascii=False, sort_keys=True))
    return 0


def run_repl(config: ClientConfig, *, environ: dict[str, str] | None = None) -> int:
    env = environ if environ is not None else os.environ
    session = make_synthetic_session()
    turns_left = config.max_turns
    print("Synthetic Jivo client is ready. Commands: /start, /quit.")
    if not config.live:
        print("Offline mode is ON: messages will not be sent without --live.")
    while True:
        try:
            text = input("client> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0
        if not text:
            continue
        if text == "/quit":
            print("Bye.")
            return 0
        if text == "/start":
            session = make_synthetic_session()
            turns_left = config.max_turns
            print("Started a fresh synthetic test-only session.")
            continue
        if turns_left <= 0:
            print("Turn limit reached; no request was made.")
            return 0
        print(f"Remaining turns before request: {turns_left}")
        try:
            token = live_request_allowed(config.live, env.get(TOKEN_ENV))
            payload = build_client_message(text, session)
            data = post_client_message(payload, base_url=config.base_url, provider_token=token, timeout=config.timeout)
            kind, message = normalize_response(data)
            prefix = "bot" if kind == "bot" else kind
            print(f"{prefix}> {message}")
            turns_left -= 1
        except SafeClientError as exc:
            print(f"error> {exc}")


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    if config.dry_run is not None:
        return print_dry_run(config.dry_run, make_synthetic_session(seed="dry-run-session"))
    return run_repl(config)


if __name__ == "__main__":
    raise SystemExit(main())
