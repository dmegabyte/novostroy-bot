"""One TEST-only V4 search-to-callback smoke; prints no contact or credentials."""

from __future__ import annotations

import json
import time
import uuid
import urllib.request
from pathlib import Path
from typing import Any

from nmbot_v4.client_ux import check_client_ux


def _env_value(key: str) -> str:
    for line in Path("/home/neiro/novostroy-bot/.env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"missing {key}")


def main() -> None:
    token = _env_value("JIVO_PROVIDER_TOKEN")
    suffix = uuid.uuid4().hex[:12]
    base = {
        "site_id": f"test-only-v4-site-{suffix}",
        "chat_id": f"test-only-v4-chat-{suffix}",
        "client_id": f"test-only-v4-client-{suffix}",
        "agents_online": True,
        "sender": {
            "id": f"test-only-v4-sender-{suffix}",
            "name": "Тестовый клиент V4",
            "url": "https://example.invalid/nmbot-jivo-client",
            "has_contacts": False,
        },
        "channel": {"id": f"test-only-v4-channel-{suffix}", "type": "widget"},
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
        with urllib.request.urlopen(request, timeout=150) as response:
            return event_id, json.load(response)

    start_id, start = send("/start_4")
    search_id, search = send("Нужна двушка для семьи")
    search_text = ((search.get("message") or {}).get("text") or "")
    ux = check_client_ux(search_text, expected_blocks=None, family_query=True)
    block_count = int(ux["metrics"]["numbered_blocks"])
    shortlist_ok = 1 <= block_count <= 3
    ux_codes = [*ux["codes"], *( [] if shortlist_ok else ["shortlist_contract_mismatch"] )]
    search_ok = bool(search_text.strip()) and not ux_codes and not search_text.strip().startswith("{") and "\\n" not in search_text
    if not search_ok:
        print(
            json.dumps(
                {
                    "start_event_id": start_id,
                    "start_event": start.get("event"),
                    "search_event_id": search_id,
                    "search_event": search.get("event"),
                    "search_ok": False,
                    "ux_ok": not ux_codes,
                    "ux_codes": ux_codes,
                    "ux_metrics": ux["metrics"],
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2)

    phone_id, phone = send("Мой номер +7 999 000-00-01")
    phone_text = ((phone.get("message") or {}).get("text") or "")
    phone_human_ok = bool(phone_text.strip()) and not phone_text.strip().startswith("{") and "\\n" not in phone_text
    name_id, name = send("Анна")
    name_text = ((name.get("message") or {}).get("text") or "")
    name_human_ok = bool(name_text.strip()) and not name_text.strip().startswith("{") and "\\n" not in name_text
    print(
        json.dumps(
            {
                "start_event_id": start_id,
                "start_event": start.get("event"),
                "search_event_id": search_id,
                "search_event": search.get("event"),
                "search_ok": True,
                "ux_ok": not ux_codes,
                "ux_metrics": ux["metrics"],
                "phone_event_id": phone_id,
                "phone_event": phone.get("event"),
                "phone_human_ok": phone_human_ok,
                "name_event_id": name_id,
                "name_event": name.get("event"),
                "name_human_ok": name_human_ok,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
