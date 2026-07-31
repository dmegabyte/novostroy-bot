#!/usr/bin/env python3
"""Safe local admin CLI for client-production runtime selector."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


EXPECTED_PROFILE = "client_production"
EXPECTED_PORT = "8188"
SUPPORTED = {"V0", "V2", "V3"}


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            value = value.strip().strip('"').strip("'")
            values[key.strip()] = value
    return {**values, **{k: v for k, v in os.environ.items() if k.startswith("NMBOT_") or k == "JIVO_PROVIDER_TOKEN"}}


def _validate_env(env: dict[str, str]) -> tuple[str, str]:
    profile = env.get("NMBOT_CONTOUR_PROFILE", "").strip().lower()
    host = env.get("NMBOT_API_HOST", "127.0.0.1").strip()
    port = env.get("NMBOT_API_PORT", "").strip()
    token = env.get("NMBOT_API_TOKEN", "").strip()
    if profile != EXPECTED_PROFILE:
        raise SystemExit("ERROR: env profile mismatch; expected client_production")
    if host not in {"127.0.0.1", "localhost"} or port != EXPECTED_PORT:
        raise SystemExit("ERROR: refusing non-loopback or unexpected client-production API port")
    if not token or "PLACEHOLDER" in token.upper():
        raise SystemExit("ERROR: NMBOT_API_TOKEN is missing or placeholder")
    return f"http://{host}:{port}/api/runtime-version", token


def _request(url: str, token: str, *, method: str, body: dict[str, str] | None = None) -> dict[str, object]:
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"ERROR: API returned HTTP {exc.code}") from exc
    except Exception as exc:
        raise SystemExit(f"ERROR: API request failed: {type(exc).__name__}") from exc
    return payload if isinstance(payload, dict) else {"ok": False, "error": "non_object_response"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="/home/neiro/novostroy-bot-client-production/.env.client-production")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    set_parser = sub.add_parser("set")
    set_parser.add_argument("version", choices=sorted(SUPPORTED))
    set_parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)

    env = _load_env(Path(args.env).expanduser())
    url, token = _validate_env(env)
    if args.cmd == "set" and not args.confirm:
        raise SystemExit("ERROR: mutation requires --confirm")
    payload = _request(url, token, method="POST", body={"runtime_version": args.version}) if args.cmd == "set" else _request(url, token, method="GET")
    safe = {key: payload.get(key) for key in ("ok", "runtime_version", "previous_runtime_version", "error") if key in payload}
    safe["profile"] = EXPECTED_PROFILE
    safe["api_port"] = EXPECTED_PORT
    print(json.dumps(safe, ensure_ascii=False, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
