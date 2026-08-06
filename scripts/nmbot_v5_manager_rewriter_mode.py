#!/usr/bin/env python3
"""Fail-closed V5 manager-rewriter mode switch for the TEST contour.

Examples::

    python3 scripts/nmbot_v5_manager_rewriter_mode.py --status
    python3 scripts/nmbot_v5_manager_rewriter_mode.py --mode shadow --confirm
    python3 scripts/nmbot_v5_manager_rewriter_mode.py --mode publish --confirm
    python3 scripts/nmbot_v5_manager_rewriter_mode.py --mode off --confirm

The target is intentionally fixed to the TEST contour. The script uses the
existing remote dotenv helper, backs up `.env`, restarts only the API unit, and
returns bounded health/runtime status. It never prints dotenv values.
"""

from __future__ import annotations

import argparse
import base64
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any


TEST_HOST = "neiro@193.107.155.236"
TEST_PORT = "1905"
TEST_ROOT = "/home/neiro/novostroy-bot"
TEST_SERVICE = "novostroy-bot-api.service"
ALLOWED_MODES = frozenset({"off", "shadow", "publish"})


class ModeSwitchError(RuntimeError):
    pass


def validate_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode not in ALLOWED_MODES:
        raise ModeSwitchError("mode must be one of: off, shadow, publish")
    return mode


def _remote_status_script() -> str:
    return r'''
from pathlib import Path
import json
import subprocess
import urllib.request

root = Path("/home/neiro/novostroy-bot")
values = {}
for raw in (root / ".env").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

def get_json(url):
    token = values.get("NMBOT_API_TOKEN", "")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {"ok": False}

service = subprocess.run(
    ["systemctl", "--user", "is-active", "novostroy-bot-api.service"],
    capture_output=True,
    text=True,
    check=False,
)
mode = values.get("NMBOT_V5_MANAGER_REWRITER_MODE") or values.get("NMBOT_MANAGER_REWRITER_MODE") or "off"
print(json.dumps({
    "contour": "test",
    "mode": mode if mode in {"off", "shadow", "publish"} else "invalid",
    "runtime": get_json("http://127.0.0.1:8088/api/runtime-version").get("runtime_version"),
    "health_ok": get_json("http://127.0.0.1:8088/health").get("ok") is True,
    "service_active": service.returncode == 0 and service.stdout.strip() == "active",
}, ensure_ascii=False))
'''


def _encoded_remote_python(source: str) -> str:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    return "python3 -c " + shlex.quote("import base64; exec(base64.b64decode(" + repr(encoded) + ").decode('utf-8'))")


def build_status_command() -> str:
    return _encoded_remote_python(_remote_status_script())


def build_set_command(mode: str, backup_name: str) -> str:
    selected = validate_mode(mode)
    root = shlex.quote(TEST_ROOT)
    backup = shlex.quote(f"{TEST_ROOT}/backups/{backup_name}")
    helper = shlex.quote(f"{TEST_ROOT}/scripts/nmbot_env_secrets.py")
    env_file = shlex.quote(f"{TEST_ROOT}/.env")
    return (
        "set -eu; "
        f"mkdir -p {root}/backups; "
        f"cp -p {env_file} {backup}; "
        f"python3 {helper} --env {env_file} --key NMBOT_MANAGER_REWRITER_MODE --value {shlex.quote(selected)}; "
        f"systemctl --user restart {shlex.quote(TEST_SERVICE)}; "
        f"systemctl --user is-active {shlex.quote(TEST_SERVICE)}"
    )


def run_remote(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", "-p", TEST_PORT, "-o", "BatchMode=yes", TEST_HOST, command],
        capture_output=True,
        text=True,
        check=False,
    )


def read_status() -> dict[str, Any]:
    result = run_remote(build_status_command())
    if result.returncode != 0:
        raise ModeSwitchError("remote status check failed")
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ModeSwitchError("remote status returned invalid JSON") from exc
    if not isinstance(status, dict):
        raise ModeSwitchError("remote status returned invalid object")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Switch V5 manager-rewriter mode on TEST only")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="show bounded TEST status")
    group.add_argument("--mode", choices=sorted(ALLOWED_MODES), help="new mode")
    parser.add_argument("--confirm", action="store_true", help="allow remote config mutation")
    parser.add_argument("--dry-run", action="store_true", help="show planned mutation without SSH write")
    args = parser.parse_args()

    try:
        if args.status:
            print(json.dumps(read_status(), ensure_ascii=False, indent=2))
            return 0
        mode = validate_mode(args.mode)
        if args.dry_run:
            backup_name = f"v5-manager-rewriter-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.env"
            print(json.dumps({
                "status": "dry_run",
                "contour": "test",
                "host": TEST_HOST,
                "mode": mode,
                "service": TEST_SERVICE,
                "backup": f"{TEST_ROOT}/backups/{backup_name}",
                "would_restart_only": TEST_SERVICE,
            }, ensure_ascii=False, indent=2))
            return 0
        if not args.confirm:
            raise ModeSwitchError("mode mutation requires --confirm; use --dry-run first")
        before = read_status()
        if before.get("runtime") != "V5":
            raise ModeSwitchError("TEST runtime is not V5; refusing mode mutation")
        backup_name = f"v5-manager-rewriter-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.env"
        result = run_remote(build_set_command(mode, backup_name))
        if result.returncode != 0:
            raise ModeSwitchError("remote mode update/restart failed")
        after = read_status()
        if after.get("mode") != mode or not after.get("health_ok") or not after.get("service_active"):
            raise ModeSwitchError("post-change TEST status did not satisfy mode/health/service contract")
        print(json.dumps({"status": "updated", "before": before, "after": after, "backup": f"{TEST_ROOT}/backups/{backup_name}"}, ensure_ascii=False, indent=2))
        return 0
    except ModeSwitchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
