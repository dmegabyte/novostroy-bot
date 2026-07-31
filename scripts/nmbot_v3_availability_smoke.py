"""One TEST-only V3 selected-availability trace smoke; no contact or secrets."""

from __future__ import annotations

import json
import time
import uuid
import urllib.request
from pathlib import Path
from typing import Any


def _token() -> str:
    for line in Path("/home/neiro/novostroy-bot/.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("JIVO_PROVIDER_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("missing JIVO_PROVIDER_TOKEN")


def main() -> None:
    suffix = uuid.uuid4().hex[:12]
    base = {
        "site_id": f"test-only-v3-availability-site-{suffix}",
        "chat_id": f"test-only-v3-availability-chat-{suffix}",
        "client_id": f"test-only-v3-availability-client-{suffix}",
        "agents_online": True,
        "sender": {"id": f"test-only-v3-availability-sender-{suffix}", "name": "Тестовый клиент V3", "url": "https://example.invalid/nmbot-jivo-client", "has_contacts": False},
        "channel": {"id": f"test-only-v3-availability-channel-{suffix}", "type": "widget"},
    }
    token = _token()

    def send(text: str) -> tuple[str, dict[str, Any]]:
        event_id = str(uuid.uuid4())
        payload = {**base, "event": "CLIENT_MESSAGE", "id": event_id, "message": {"type": "TEXT", "text": text, "timestamp": int(time.time())}}
        req = urllib.request.Request(f"http://127.0.0.1:8088/jivo/{token}", data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=180) as response:
            return event_id, json.load(response)

    start_id, start = send("/start_3")
    search_id, search = send("Нужна двушка для семьи")
    search_text = str((search.get("message") or {}).get("text") or "").strip()
    if search.get("event") != "BOT_MESSAGE" or not search_text or search_text.startswith("{"):
        print(json.dumps({"start_event_id": start_id, "start_event": start.get("event"), "search_event_id": search_id, "search_event": search.get("event"), "search_ok": False}, ensure_ascii=False))
        raise SystemExit(2)
    select_id, selected = send("третий")
    selected_text = str((selected.get("message") or {}).get("text") or "").strip()
    if selected.get("event") != "BOT_MESSAGE" or not selected_text or selected_text.startswith("{"):
        print(json.dumps({"search_event_id": search_id, "select_event_id": select_id, "select_event": selected.get("event"), "select_ok": False}, ensure_ascii=False))
        raise SystemExit(3)
    availability_id, availability = send("проверим актуальное наличие")
    print(json.dumps({"start_event_id": start_id, "start_event": start.get("event"), "search_event_id": search_id, "search_event": search.get("event"), "select_event_id": select_id, "select_event": selected.get("event"), "availability_event_id": availability_id, "availability_event": availability.get("event")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
