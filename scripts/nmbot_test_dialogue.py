"""Generic local TEST-only NMBot Jivo dialogue runner.

The script intentionally keeps the runtime surface tiny: it builds one
synthetic Jivo session, posts it only to the local bridge, and prints a compact
JSON record with opaque event ids/types and boolean checks only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
import urllib.request
from pathlib import Path
from typing import Any, Callable, Literal, NamedTuple


Runtime = Literal["v3", "v4"]
Send = Callable[[str], tuple[str, dict[str, Any]]]
DeliveryLookup = Callable[[str], dict[str, Any] | None]

DEFAULT_QUERY = "Нужна двушка для семьи"
TEST_PHONE = "Мой номер +7 999 000-00-01"
TEST_NAME = "Анна"
LOCAL_JIVO_PREFIX = "http://127.0.0.1:8088/jivo/"
VPS_ENV_PATH = Path("/home/neiro/novostroy-bot/.env")
DEFAULT_DELIVERY_TIMEOUT_SECONDS = 45
MIN_DELIVERY_TIMEOUT_SECONDS = 1
MAX_DELIVERY_TIMEOUT_SECONDS = 300


class RuntimeConfig(NamedTuple):
    runtime: Runtime
    start_command: str
    timeout_seconds: int


def runtime_config(runtime: str) -> RuntimeConfig:
    if runtime == "v3":
        return RuntimeConfig("v3", "/start_3", 180)
    if runtime == "v4":
        return RuntimeConfig("v4", "/start_4", 150)
    raise ValueError(f"unsupported runtime: {runtime}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one local TEST-only NMBot dialogue")
    parser.add_argument("--runtime", choices=("v3", "v4"), required=True)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--check-delivery", action="store_true", default=False)
    parser.add_argument("--delivery-timeout", type=delivery_timeout_arg, default=DEFAULT_DELIVERY_TIMEOUT_SECONDS)
    return parser.parse_args(argv)


def delivery_timeout_arg(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("delivery timeout must be an integer") from exc
    if not MIN_DELIVERY_TIMEOUT_SECONDS <= parsed <= MAX_DELIVERY_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"delivery timeout must be between {MIN_DELIVERY_TIMEOUT_SECONDS} and {MAX_DELIVERY_TIMEOUT_SECONDS} seconds"
        )
    return parsed


def env_value(key: str, env_path: Path = VPS_ENV_PATH) -> str:
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"missing {key}")


def synthetic_base(runtime: Runtime, suffix: str | None = None) -> dict[str, Any]:
    safe_suffix = suffix or uuid.uuid4().hex[:12]
    return {
        "site_id": f"test-only-{runtime}-site-{safe_suffix}",
        "chat_id": f"test-only-{runtime}-chat-{safe_suffix}",
        "client_id": f"test-only-{runtime}-client-{safe_suffix}",
        "agents_online": True,
        "sender": {
            "id": f"test-only-{runtime}-sender-{safe_suffix}",
            "name": f"Тестовый клиент {runtime.upper()}",
            "url": "https://example.invalid/nmbot-jivo-client",
            "has_contacts": False,
        },
        "channel": {"id": f"test-only-{runtime}-channel-{safe_suffix}", "type": "widget"},
    }


def build_payload(base: dict[str, Any], event_id: str, text: str) -> dict[str, Any]:
    return {
        **base,
        "event": "CLIENT_MESSAGE",
        "id": event_id,
        "message": {"type": "TEXT", "text": text, "timestamp": int(time.time())},
    }


def make_local_sender(token: str, runtime: Runtime) -> Send:
    config = runtime_config(runtime)
    base = synthetic_base(config.runtime)

    def send(text: str) -> tuple[str, dict[str, Any]]:
        event_id = str(uuid.uuid4())
        request = urllib.request.Request(
            f"{LOCAL_JIVO_PREFIX}{token}",
            data=json.dumps(build_payload(base, event_id, text), ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            return event_id, json.load(response)

    return send


def message_text(response: dict[str, Any]) -> str:
    return str((response.get("message") or {}).get("text") or "")


def terminal_bot_message(response: dict[str, Any]) -> bool:
    return response.get("event") == "BOT_MESSAGE"


def v3_human_ok(response: dict[str, Any]) -> bool:
    text = message_text(response).strip()
    return terminal_bot_message(response) and bool(text) and not text.startswith("{") and "\\n" not in text


def event_ref(event_id: str) -> str:
    return "event_" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:16]


def safe_delivery_result(record: dict[str, Any] | None, ref: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {
            "delivery_checked": True,
            "delivery_ok": False,
            "found": False,
            "event_ref": ref,
            "lead_ref": "",
            "delivery_status": "",
            "row_ref": "",
        }
    delivery = record.get("sheet_delivery")
    delivery = delivery if isinstance(delivery, dict) else {}
    status = safe_token(delivery.get("status"), limit=80)
    row_ref = safe_token(delivery.get("sheet_row_ref"), limit=80)
    return {
        "delivery_checked": True,
        "delivery_ok": status == "sheet_delivered" and bool(row_ref),
        "found": True,
        "event_ref": ref,
        "lead_ref": safe_token(record.get("lead_ref"), limit=32),
        "delivery_status": status,
        "row_ref": row_ref,
    }


def safe_token(value: object, *, limit: int) -> str:
    parts = str(value or "").strip().split(maxsplit=1)
    token = parts[0] if parts else ""
    return "".join(ch for ch in token if ch.isascii() and (ch.isalnum() or ch in "_-.:/"))[:limit]


def outbox_lookup(outbox: Path) -> DeliveryLookup:
    def lookup(ref: str) -> dict[str, Any] | None:
        for path in outbox.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict) and record.get("event_ref") == ref:
                return record
        return None

    return lookup


def wait_delivery(
    name_event_id: str,
    lookup: DeliveryLookup,
    *,
    timeout_seconds: int = DEFAULT_DELIVERY_TIMEOUT_SECONDS,
    sleep_seconds: float = 3.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    ref = event_ref(name_event_id)
    deadline = monotonic() + timeout_seconds
    while True:
        record = lookup(ref)
        if record is not None:
            return safe_delivery_result(record, ref)
        if monotonic() >= deadline:
            return safe_delivery_result(None, ref)
        sleep(sleep_seconds)


def load_v4_client_ux_checker() -> Callable[[str], dict[str, Any]] | None:
    try:
        from nmbot_v4.client_ux import check_client_ux
    except ImportError:
        return None

    def check(text: str) -> dict[str, Any]:
        return check_client_ux(text, expected_blocks=None, family_query=True)

    return check


def v4_search_metrics(
    response: dict[str, Any],
    checker: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    text = message_text(response)
    check = checker or load_v4_client_ux_checker()
    if check is None:
        return {"search_ok": False, "ux_ok": False, "shortlist_ok": False, "v4_checker_available": False}

    ux = check(text)
    metrics = ux.get("metrics") if isinstance(ux, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    codes = ux.get("codes") if isinstance(ux, dict) else []
    codes = codes if isinstance(codes, list) else ["ux_codes_shape_mismatch"]
    block_count = int(metrics.get("numbered_blocks") or 0)
    shortlist_ok = 1 <= block_count <= 3
    ux_ok = not codes and shortlist_ok
    text_ok = bool(text.strip()) and not text.strip().startswith("{") and "\\n" not in text
    return {
        "search_ok": terminal_bot_message(response) and text_ok and ux_ok,
        "ux_ok": ux_ok,
        "shortlist_ok": shortlist_ok,
        "v4_checker_available": True,
    }


def stage_record(stage: str, event_id: str, response: dict[str, Any], metrics: dict[str, bool]) -> dict[str, Any]:
    return {
        "stage": stage,
        "event_id": event_id,
        "event": response.get("event"),
        "metrics": metrics,
    }


def run_dialogue(
    runtime: Runtime,
    send: Send,
    *,
    query: str = DEFAULT_QUERY,
    v4_checker: Callable[[str], dict[str, Any]] | None = None,
    check_delivery: bool = False,
    delivery_timeout: int = DEFAULT_DELIVERY_TIMEOUT_SECONDS,
    delivery_lookup: DeliveryLookup | None = None,
) -> tuple[int, dict[str, Any]]:
    config = runtime_config(runtime)
    result: dict[str, Any] = {"runtime": runtime, "ok": False, "stages": []}

    start_id, start = send(config.start_command)
    start_ok = terminal_bot_message(start)
    result["stages"].append(stage_record("start", start_id, start, {"terminal_bot_message": start_ok}))
    if not start_ok:
        result["failed_stage"] = "start"
        return 2, result

    search_id, search = send(query)
    if runtime == "v3":
        search_metrics = {"search_ok": v3_human_ok(search)}
    else:
        search_metrics = v4_search_metrics(search, v4_checker)
    result["stages"].append(stage_record("search", search_id, search, search_metrics))
    if not bool(search_metrics.get("search_ok")):
        result["failed_stage"] = "search"
        return 2, result

    phone_id, phone = send(TEST_PHONE)
    phone_ok = v3_human_ok(phone)
    result["stages"].append(stage_record("phone", phone_id, phone, {"human_ok": phone_ok}))
    if not phone_ok:
        result["failed_stage"] = "phone"
        return 2, result

    name_id, name = send(TEST_NAME)
    name_ok = v3_human_ok(name)
    result["stages"].append(stage_record("name", name_id, name, {"human_ok": name_ok}))
    if not name_ok:
        result["failed_stage"] = "name"
        return 2, result

    if check_delivery:
        lookup = delivery_lookup
        if lookup is None:
            outbox = Path(env_value("NMBOT_CALLBACK_OUTBOX_DIR")).expanduser()
            lookup = outbox_lookup(outbox)
        delivery = wait_delivery(name_id, lookup, timeout_seconds=delivery_timeout)
        result["delivery"] = delivery
        if not delivery["delivery_ok"]:
            result["failed_stage"] = "delivery"
            return 2, result

    result["ok"] = True
    return 0, result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runtime = runtime_config(args.runtime).runtime
    token = env_value("JIVO_PROVIDER_TOKEN")
    code, result = run_dialogue(
        runtime,
        make_local_sender(token, runtime),
        query=args.query,
        check_delivery=args.check_delivery,
        delivery_timeout=args.delivery_timeout,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
