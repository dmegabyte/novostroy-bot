#!/usr/bin/env python3
"""Fail-closed feature-flag switcher for the fixed TEST contour only.

Examples::

    python3 scripts/nmbot_test_feature_flags.py --status
    python3 scripts/nmbot_test_feature_flags.py --set NMBOT_BROAD_INVENTORY_GATE_ENABLED=on --dry-run
    python3 scripts/nmbot_test_feature_flags.py --set NMBOT_BROAD_INVENTORY_GATE_ENABLED=off --confirm

The script never prints dotenv values. Mutations use the remote dotenv helper,
make one backup, and restart only the TEST API service.
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
ALLOWED_KEYS = frozenset(
    {
        "NMBOT_BROAD_INVENTORY_GATE_ENABLED",
        "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED",
        "NMBOT_OPENROUTER_EXCLUDE_REASONING",
    }
)
BOOLEAN_VALUES = {"on": "1", "off": "0"}


class FeatureFlagError(RuntimeError):
    pass


def validate_assignment(raw: str) -> tuple[str, str]:
    """Return an allowlisted key and its canonical dotenv boolean value."""
    if raw.count("=") != 1:
        raise FeatureFlagError("--set must be exactly KEY=on or KEY=off")
    key, value = raw.split("=", 1)
    if key not in ALLOWED_KEYS:
        raise FeatureFlagError("--set key is not an allowed TEST feature flag")
    try:
        return key, BOOLEAN_VALUES[value]
    except KeyError as exc:
        raise FeatureFlagError("--set value must be exactly on or off") from exc


def validate_assignments(raw_assignments: list[str]) -> list[tuple[str, str]]:
    assignments = [validate_assignment(raw) for raw in raw_assignments]
    keys = [key for key, _ in assignments]
    if len(set(keys)) != len(keys):
        raise FeatureFlagError("each TEST feature flag may be set only once per invocation")
    return assignments


def _remote_status_script() -> str:
    keys = repr(sorted(ALLOWED_KEYS))
    return f'''
from pathlib import Path
import json
import subprocess
import urllib.request

root = Path({TEST_ROOT!r})
values = {{}}
for raw in (root / ".env").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

def get_json(url):
    request = urllib.request.Request(url, headers={{"Authorization": f"Bearer {{values.get('NMBOT_API_TOKEN', '')}}"}})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {{}}

service = subprocess.run(
    ["systemctl", "--user", "is-active", {TEST_SERVICE!r}],
    capture_output=True,
    text=True,
    check=False,
)
print(json.dumps({{
    "feature_flags": {{key: values.get(key, "").strip().lower() in {{"1", "true", "yes", "on"}} for key in {keys}}},
    "runtime_v5": get_json("http://127.0.0.1:8088/api/runtime-version").get("runtime_version") == "V5",
    "health_ok": get_json("http://127.0.0.1:8088/health").get("ok") is True,
    "service_active": service.returncode == 0 and service.stdout.strip() == "active",
}}, ensure_ascii=False))
'''


def _encoded_remote_python(source: str) -> str:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    return "python3 -c " + shlex.quote("import base64; exec(base64.b64decode(" + repr(encoded) + ").decode('utf-8'))")


def build_status_command() -> str:
    return _encoded_remote_python(_remote_status_script())


def build_set_command(assignments: list[tuple[str, str]], backup_name: str) -> str:
    if not assignments:
        raise FeatureFlagError("at least one --set assignment is required")
    if any(key not in ALLOWED_KEYS or value not in {"0", "1"} for key, value in assignments):
        raise FeatureFlagError("invalid TEST feature-flag assignment")
    root = shlex.quote(TEST_ROOT)
    backup = shlex.quote(f"{TEST_ROOT}/backups/{backup_name}")
    helper = shlex.quote(f"{TEST_ROOT}/scripts/nmbot_env_secrets.py")
    env_file = shlex.quote(f"{TEST_ROOT}/.env")
    updates = " ".join(
        f"python3 {helper} --env {env_file} --key {shlex.quote(key)} --value {shlex.quote(value)};"
        for key, value in assignments
    )
    return (
        "set -eu; "
        f"mkdir -p {root}/backups; "
        f"cp -p {env_file} {backup}; "
        f"{updates} "
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
        raise FeatureFlagError("remote TEST status check failed")
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FeatureFlagError("remote TEST status returned invalid JSON") from exc
    if not isinstance(status, dict):
        raise FeatureFlagError("remote TEST status returned invalid object")
    return status


def render_status(status: dict[str, Any]) -> dict[str, Any]:
    """Render a bounded public status without exposing remote dotenv values."""
    flags = status.get("feature_flags")
    if not isinstance(flags, dict):
        flags = {}
    return {
        "feature_flags": {
            key: "enabled" if flags.get(key) is True else "disabled"
            for key in sorted(ALLOWED_KEYS)
        },
        "health": "healthy" if status.get("health_ok") is True else "unhealthy",
        "service": "active" if status.get("service_active") is True else "inactive",
        "runtime": "V5" if status.get("runtime_v5") is True else "not_v5",
    }


def _post_change_is_valid(status: dict[str, Any], assignments: list[tuple[str, str]]) -> bool:
    flags = status.get("feature_flags")
    if not isinstance(flags, dict):
        return False
    return (
        status.get("runtime_v5") is True
        and status.get("health_ok") is True
        and status.get("service_active") is True
        and all(flags.get(key) is (value == "1") for key, value in assignments)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Switch allowlisted feature flags on TEST only")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="show bounded TEST status")
    group.add_argument("--set", action="append", metavar="KEY=VALUE", help="set one allowlisted flag to on or off")
    parser.add_argument("--dry-run", action="store_true", help="show planned mutation without remote write")
    parser.add_argument("--confirm", action="store_true", help="allow remote config mutation")
    args = parser.parse_args()

    try:
        if args.status:
            if args.dry_run or args.confirm:
                raise FeatureFlagError("--status cannot be combined with --dry-run or --confirm")
            print(json.dumps(render_status(read_status()), ensure_ascii=False, indent=2))
            return 0

        assignments = validate_assignments(args.set or [])
        changed_keys = [key for key, _ in assignments]
        if args.dry_run:
            print(json.dumps({"status": "dry_run", "changed_keys": changed_keys}, ensure_ascii=False, indent=2))
            return 0
        if not args.confirm:
            raise FeatureFlagError("TEST feature-flag mutation requires --confirm; use --dry-run first")

        before = read_status()
        if before.get("runtime_v5") is not True:
            raise FeatureFlagError("TEST runtime is not V5; refusing feature-flag mutation")
        backup_name = f"test-feature-flags-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.env"
        result = run_remote(build_set_command(assignments, backup_name))
        if result.returncode != 0:
            raise FeatureFlagError("remote TEST feature-flag update/restart failed")
        after = read_status()
        if not _post_change_is_valid(after, assignments):
            raise FeatureFlagError("post-change TEST status did not satisfy flag/health/service/runtime contract")
        print(json.dumps({
            "status": "updated",
            "changed_keys": changed_keys,
            "before": render_status(before),
            "after": render_status(after),
        }, ensure_ascii=False, indent=2))
        return 0
    except FeatureFlagError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
