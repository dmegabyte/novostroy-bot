"""One TEST-only V3 search-to-callback smoke; prints no contact or credentials."""

from __future__ import annotations

import json
import time
import uuid
import urllib.request
from pathlib import Path
from typing import Any


def _env_value(key: str) -> str:
    for line in Path("/home/neiro/novostroy-bot/.env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"missing {key}")


def main() -> None:
    token = _env_value("JIVO_PROVIDER_TOKEN")
    suffix = uuid.uuid4().hex[:12]
    base = {
        "site_id": f"test-only-v3-site-{suffix}",
        "chat_id": f"test-only-v3-chat-{suffix}",
        "client_id": f"test-only-v3-client-{suffix}",
        "agents_online": True,
        "sender": {
            "id": f"test-only-v3-sender-{suffix}",
            "name": "Тестовый клиент V3",
            "url": "https://example.invalid/nmbot-jivo-client",
            "has_contacts": False,
        },
        "channel": {"id": f"test-only-v3-channel-{suffix}", "type": "widget"},
    }

    def send(text: str) -> tuple[str, dict[str, Any]]:
        event_id = str(uuid.uuid4())
        payload = {
            **base,
            "event": "CLIENT_MESSAGE",
            "id": event_id,
            "message": {"type": "TEXT", "text": text, "timestamp": int(time.time())},
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:8088/jivo/{token}",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            return event_id, json.load(response)

    start_id, start = send("/start_3")
    search_id, search = send("Нужна двушка для семьи")
    search_text = str((search.get("message") or {}).get("text") or "").strip()
    search_ok = (
        search.get("event") == "BOT_MESSAGE"
        and bool(search_text)
        and not search_text.startswith("{")
        and "\\n" not in search_text
    )
    if not search_ok:
        print(json.dumps({"start_event_id": start_id, "start_event": start.get("event"), "search_event_id": search_id, "search_event": search.get("event"), "search_ok": False}, ensure_ascii=False))
        raise SystemExit(2)

    phone_id, phone = send("Мой номер +7 999 000-00-01")
    name_id, name = send("Анна")
    phone_text = str((phone.get("message") or {}).get("text") or "").strip()
    name_text = str((name.get("message") or {}).get("text") or "").strip()
    print(json.dumps({"start_event_id": start_id, "start_event": start.get("event"), "search_event_id": search_id, "search_event": search.get("event"), "search_ok": True, "phone_event_id": phone_id, "phone_event": phone.get("event"), "phone_human_ok": bool(phone_text) and not phone_text.startswith("{") and "\\n" not in phone_text, "name_event_id": name_id, "name_event": name.get("event"), "name_human_ok": bool(name_text) and not name_text.startswith("{") and "\\n" not in name_text}, ensure_ascii=False))


if __name__ == "__main__":
    main()
