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
import os
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Any


# TEST identity is deliberately unconfigured by default. Supplying a host is
# insufficient: an explicit profile and release marker make accidental writes
# to the production contour fail closed before SSH is attempted.
TEST_HOST = os.getenv("NMBOT_TEST_HOST", "")
TEST_PORT = os.getenv("NMBOT_TEST_PORT", "")
TEST_API_PORT = os.getenv("NMBOT_TEST_API_PORT", "")
TEST_ROOT = os.getenv("NMBOT_TEST_ROOT", "")
TEST_SERVICE = os.getenv("NMBOT_TEST_SERVICE", "")
TEST_RELEASE_MARKER = os.getenv("NMBOT_TEST_RELEASE_MARKER", "")
TEST_PROFILE = os.getenv("NMBOT_TEST_PROFILE", "")
TEST_IDENTITY_FILE = os.getenv("NMBOT_TEST_IDENTITY_FILE", "")
PRODUCTION_HOST = "neiro@193.107.155.236"
PRODUCTION_ROOT = "/home/neiro/novostroy-bot"
PRODUCTION_SERVICE = "novostroy-bot-api.service"
ALLOWED_KEYS = frozenset(
    {
        "NMBOT_BROAD_INVENTORY_GATE_ENABLED",
        "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED",
        "NMBOT_OPENROUTER_EXCLUDE_REASONING",
    }
)
BOOLEAN_VALUES = {"on": "1", "off": "0"}
EFFECTIVE_DEFAULTS = {
    "NMBOT_BROAD_INVENTORY_GATE_ENABLED": True,
    "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED": True,
    "NMBOT_OPENROUTER_EXCLUDE_REASONING": False,
}


class FeatureFlagError(RuntimeError):
    pass


def validate_test_identity() -> None:
    required = (TEST_HOST, TEST_PORT, TEST_API_PORT, TEST_ROOT, TEST_SERVICE, TEST_RELEASE_MARKER, TEST_PROFILE, TEST_IDENTITY_FILE)
    if not all(str(value or "").strip() for value in required):
        raise FeatureFlagError("TEST identity is not fully configured")
    if TEST_HOST == PRODUCTION_HOST or TEST_ROOT == PRODUCTION_ROOT or TEST_SERVICE == PRODUCTION_SERVICE:
        raise FeatureFlagError("TEST identity matches production contour")


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


def _remote_status_script(expected_assignments: list[tuple[str, str]] | None = None) -> str:
    keys = repr(sorted(ALLOWED_KEYS))
    defaults = repr(EFFECTIVE_DEFAULTS)
    expected = repr(dict(expected_assignments or []))
    return f'''
from pathlib import Path
import json
import subprocess
import urllib.request

root = Path({TEST_ROOT!r})
identity_file = Path({TEST_IDENTITY_FILE!r})
defaults = {defaults}
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
try:
    identity = json.loads(identity_file.read_text(encoding="utf-8"))
except Exception:
    identity = {{}}
test_identity_ok = (
    isinstance(identity, dict)
    and identity.get("profile") == {TEST_PROFILE!r}
    and identity.get("release_marker") == {TEST_RELEASE_MARKER!r}
)
payload = {{
    "feature_flags": {{key: (values[key].strip().lower() in {{"1", "true", "yes", "on"}}) if key in values else defaults[key] for key in {keys}}},
    "runtime_v6": get_json("http://127.0.0.1:{TEST_API_PORT}/api/runtime-version").get("runtime_version") == "V6",
    "health_ok": get_json("http://127.0.0.1:{TEST_API_PORT}/health").get("ok") is True,
    "service_active": service.returncode == 0 and service.stdout.strip() == "active",
    "test_identity_ok": test_identity_ok,
}}
print(json.dumps(payload, ensure_ascii=False))
expected = {expected}
if expected and not (
    payload["runtime_v6"]
    and payload["health_ok"]
    and payload["service_active"]
    and payload["test_identity_ok"]
    and all(payload["feature_flags"].get(key) is (value == "1") for key, value in expected.items())
):
    raise SystemExit("TEST post-change validation failed")
'''


def _encoded_remote_python(source: str) -> str:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    return "python3 -c " + shlex.quote("import base64; exec(base64.b64decode(" + repr(encoded) + ").decode('utf-8'))")


def _remote_identity_check_script() -> str:
    """Return a silent, fail-closed TEST identity verifier for locked writes."""
    return f'''
import json
from pathlib import Path

try:
    identity = json.loads(Path({TEST_IDENTITY_FILE!r}).read_text(encoding="utf-8"))
except Exception:
    identity = {{}}
if not (
    isinstance(identity, dict)
    and identity.get("profile") == {TEST_PROFILE!r}
    and identity.get("release_marker") == {TEST_RELEASE_MARKER!r}
):
    raise SystemExit("TEST identity verification failed")
'''


def _identity_check_command() -> str:
    return _encoded_remote_python(_remote_identity_check_script())


def _remote_post_change_check_script(assignments: list[tuple[str, str]]) -> str:
    """Return the remote status/contract check run inside the locked transaction."""
    return _remote_status_script(assignments)


def build_status_command() -> str:
    validate_test_identity()
    return _encoded_remote_python(_remote_status_script())


def build_set_command(assignments: list[tuple[str, str]], backup_name: str) -> str:
    validate_test_identity()
    if not assignments:
        raise FeatureFlagError("at least one --set assignment is required")
    if any(key not in ALLOWED_KEYS or value not in {"0", "1"} for key, value in assignments):
        raise FeatureFlagError("invalid TEST feature-flag assignment")
    root = shlex.quote(TEST_ROOT)
    backup = shlex.quote(f"{TEST_ROOT}/backups/{backup_name}")
    helper = shlex.quote(f"{TEST_ROOT}/scripts/nmbot_env_secrets.py")
    env_file = shlex.quote(f"{TEST_ROOT}/.env")
    identity_check = _identity_check_command()
    updates = " ".join(
        f"python3 {helper} --env {env_file} --key {shlex.quote(key)} --value {shlex.quote(value)} "
        "|| { post_hash=$(sha256sum " + env_file + " | awk '{print $1}'); return 1; };"
        for key, value in assignments
    )
    post_change_check = _encoded_remote_python(_remote_post_change_check_script(assignments))
    return (
        "set -Eeuo pipefail; "
        f"mkdir -p {root}/backups; "
        f"exec 9>{root}/backups/.test-feature-flags.lock; flock -n 9; "
        f": test-identity-check; {identity_check}; "
        f"test ! -e {backup}; "
        f"cp -p {env_file} {backup}; "
        "post_hash=''; "
        f"rollback() {{ trap - ERR; if ! : test-identity-check || ! {identity_check} || ! test -n \"$post_hash\" || ! test \"$(sha256sum {env_file} | awk '{{print $1}}')\" = \"$post_hash\"; then printf '%s\\n' TEST_FEATURE_FLAGS_ROLLBACK_FAILED >&2; exit 70; fi; : test-identity-check; {identity_check}; cp -p {backup} {env_file}; systemctl --user restart {shlex.quote(TEST_SERVICE)}; systemctl --user is-active {shlex.quote(TEST_SERVICE)} >/dev/null; printf '%s\\n' TEST_FEATURE_FLAGS_ROLLED_BACK >&2; }}; "
        "trap rollback ERR; "
        f"apply_updates() {{ {updates} post_hash=$(sha256sum {env_file} | awk '{{print $1}}'); }}; "
        f": test-identity-check; {identity_check}; "
        "apply_updates; "
        f"systemctl --user restart {shlex.quote(TEST_SERVICE)}; "
        f"systemctl --user is-active {shlex.quote(TEST_SERVICE)} >/dev/null; "
        f"{post_change_check}; "
        "trap - ERR"
    )


def run_remote(command: str) -> subprocess.CompletedProcess[str]:
    validate_test_identity()
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
    if status.get("test_identity_ok") is not True:
        raise FeatureFlagError("remote TEST identity verification failed")
    return status


def render_status(status: dict[str, Any]) -> dict[str, Any]:
    """Render a bounded public status without exposing remote dotenv values."""
    flags = status.get("feature_flags")
    if not isinstance(flags, dict):
        flags = {}
    return {
        "feature_flags": {
            key: "enabled" if flags.get(key, EFFECTIVE_DEFAULTS[key]) is True else "disabled"
            for key in sorted(ALLOWED_KEYS)
        },
        "health": "healthy" if status.get("health_ok") is True else "unhealthy",
        "service": "active" if status.get("service_active") is True else "inactive",
        "runtime": "V6" if status.get("runtime_v6") is True else "not_v6",
    }


def _post_change_is_valid(status: dict[str, Any], assignments: list[tuple[str, str]]) -> bool:
    flags = status.get("feature_flags")
    if not isinstance(flags, dict):
        return False
    return (
        status.get("runtime_v6") is True
        and status.get("test_identity_ok") is True
        and status.get("health_ok") is True
        and status.get("service_active") is True
        and all(flags.get(key) is (value == "1") for key, value in assignments)
    )


def _backup_name() -> str:
    return f"test-feature-flags-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}.env"


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
        if before.get("runtime_v6") is not True:
            raise FeatureFlagError("TEST runtime is not V6; refusing feature-flag mutation")
        backup_name = _backup_name()
        result = run_remote(build_set_command(assignments, backup_name))
        if result.returncode != 0:
            if "TEST_FEATURE_FLAGS_ROLLBACK_FAILED" in result.stderr:
                raise FeatureFlagError("remote TEST feature-flag transaction failed; rollback failed")
            if "TEST_FEATURE_FLAGS_ROLLED_BACK" in result.stderr:
                raise FeatureFlagError("remote TEST feature-flag transaction failed; rollback restored previous TEST state")
            raise FeatureFlagError("remote TEST feature-flag transaction failed")
        try:
            after = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise FeatureFlagError("remote TEST feature-flag transaction returned invalid JSON") from exc
        if not isinstance(after, dict) or not _post_change_is_valid(after, assignments):
            raise FeatureFlagError("remote TEST feature-flag transaction returned invalid post-change status")
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
