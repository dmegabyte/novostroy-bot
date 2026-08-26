#!/usr/bin/env python3
"""Safely route the primary Jivo bridge between its current API and isolated V6 TEST.

This owner changes only ``NMBOT_BRIDGE_UPSTREAM`` in the primary bridge
environment and restarts only the primary bridge unit.  It keeps a private
backup and restores it automatically when the new route does not pass its
technical checks.  A correlated Jivo smoke remains a separate approval.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping


SCHEMA = "nmbot.primary_v6_jivo_switch.v1"
HOST = "neiro@193.107.155.236"
SSH_PORT = "1905"
PRIMARY_ENV = "/home/neiro/novostroy-bot/.env"
SWITCH_ROOT = "/home/neiro/.local/state/nmbot-v6-primary-jivo-switch"
PRIMARY_BRIDGE = "novostroy-bot-n8n-bridge.service"
PRIMARY_API = "novostroy-bot-api.service"
CLIENT_API = "novostroy-bot-client-production-api.service"
CLIENT_BRIDGE = "novostroy-bot-client-production-n8n-bridge.service"
V6_HEALTH = "http://127.0.0.1:18088/health"
PRIMARY_BRIDGE_HEALTH = "http://127.0.0.1:8093/health"
PRIMARY_API_HEALTH = "http://127.0.0.1:8088/health"
CLIENT_API_HEALTH = "http://127.0.0.1:8188/health"
CLIENT_BRIDGE_HEALTH = "http://127.0.0.1:8193/health"
CURRENT_UPSTREAM = "http://127.0.0.1:8088"
V6_UPSTREAM = "http://127.0.0.1:18088"
SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=8")
RELEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class PrimaryV6SwitchError(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetContract:
    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "target_kind": "primary_jivo_bridge_to_isolated_v6_test",
            "host": HOST,
            "primary_env": PRIMARY_ENV,
            "switch_root": SWITCH_ROOT,
            "primary_bridge": PRIMARY_BRIDGE,
            "current_upstream": CURRENT_UPSTREAM,
            "v6_upstream": V6_UPSTREAM,
            "v6_health": V6_HEALTH,
            "rollback": "restore_primary_bridge_backup",
            "do_not_touch": [PRIMARY_API, CLIENT_API, CLIENT_BRIDGE],
            "jivo_smoke": "separate_approval_required",
        }


REMOTE_PROGRAM = r'''
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

cfg = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
EXPECTED = {
    "schema": "nmbot.primary_v6_jivo_switch.v1",
    "primary_env": "/home/neiro/novostroy-bot/.env",
    "switch_root": "/home/neiro/.local/state/nmbot-v6-primary-jivo-switch",
    "primary_bridge": "novostroy-bot-n8n-bridge.service",
    "primary_api": "novostroy-bot-api.service",
    "client_api": "novostroy-bot-client-production-api.service",
    "client_bridge": "novostroy-bot-client-production-n8n-bridge.service",
    "v6_health": "http://127.0.0.1:18088/health",
    "primary_bridge_health": "http://127.0.0.1:8093/health",
    "primary_api_health": "http://127.0.0.1:8088/health",
    "client_api_health": "http://127.0.0.1:8188/health",
    "client_bridge_health": "http://127.0.0.1:8193/health",
    "current_upstream": "http://127.0.0.1:8088",
    "v6_upstream": "http://127.0.0.1:18088",
}
SAFE_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

def emit(payload, code=0):
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    raise SystemExit(code)

def fail(reason):
    emit({"ok": False, "schema": EXPECTED["schema"], "error": reason}, 2)

if not isinstance(cfg, dict) or cfg.get("schema") != EXPECTED["schema"]:
    fail("payload_schema_mismatch")
for key, value in EXPECTED.items():
    if key != "schema" and cfg.get(key) != value:
        fail("target_contract_mismatch")
operation = cfg.get("operation")
if operation not in {"preflight", "recon", "switch", "rollback"}:
    fail("operation_not_allowed")
release_id = str(cfg.get("expected_v6_release") or "")
if not SAFE_RELEASE.fullmatch(release_id):
    fail("release_id_invalid")

env_path = Path(EXPECTED["primary_env"])
root = Path(EXPECTED["switch_root"])
status_path = root / "status.json"

def run(args, timeout=30):
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    except Exception:
        fail("remote_command_failed")

def unit_state(name):
    result = run(["systemctl", "--user", "show", name, "--property=LoadState", "--property=ActiveState", "--property=SubState", "--property=MainPID", "--property=ActiveEnterTimestampMonotonic", "--property=Result", "--property=ExecMainCode", "--property=ExecMainStatus"])
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return {"loaded": values.get("LoadState") == "loaded", "active": values.get("ActiveState") == "active", "running": values.get("SubState") == "running", "pid": values.get("MainPID") or "0", "active_since": values.get("ActiveEnterTimestampMonotonic") or "0", "result": values.get("Result") or "unknown", "exec_main_code": values.get("ExecMainCode") or "unknown", "exec_main_status": values.get("ExecMainStatus") or "unknown"}

def health(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            body = json.loads(response.read(65536).decode("utf-8"))
        return {"ok": response.status == 200 and isinstance(body, dict) and body.get("ok") is True, "runtime": body.get("runtime") if isinstance(body, dict) else None, "profile": body.get("profile") if isinstance(body, dict) else None, "release_id": body.get("release_id") if isinstance(body, dict) else None}
    except Exception:
        return {"ok": False, "runtime": None, "profile": None, "release_id": None}

def is_running(item):
    return item["loaded"] and item["active"] and item["running"] and item["pid"] != "0"

def protected_snapshot():
    services = {name: unit_state(name) for name in (EXPECTED["primary_api"], EXPECTED["client_api"], EXPECTED["client_bridge"])}
    healths = {url: health(url)["ok"] for url in (EXPECTED["primary_api_health"], EXPECTED["client_api_health"], EXPECTED["client_bridge_health"])}
    return {"services": services, "healthy": all(is_running(item) for item in services.values()) and all(healths.values())}

def same_protected(before, after):
    return before["healthy"] and after["healthy"] and before["services"] == after["services"]

def v6_ready():
    item = health(EXPECTED["v6_health"])
    return item["ok"] and item["runtime"] == "V6" and item["profile"] == "TEST" and item["release_id"] == release_id

def bridge_ready():
    return is_running(unit_state(EXPECTED["primary_bridge"])) and health(EXPECTED["primary_bridge_health"])["ok"]

def read_env():
    try:
        details = env_path.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            fail("primary_env_not_regular")
        return env_path.read_bytes()
    except SystemExit:
        raise
    except Exception:
        fail("primary_env_unreadable")

def upstream_from(data):
    try:
        lines = data.decode("utf-8").splitlines()
    except Exception:
        fail("primary_env_invalid")
    values = []
    for line in lines:
        match = re.fullmatch(r"\s*(?:export\s+)?NMBOT_BRIDGE_UPSTREAM\s*=\s*(.*?)\s*", line)
        if match:
            values.append(match.group(1).strip().strip('"').strip("'"))
    if len(values) > 1:
        fail("primary_upstream_not_unique")
    return values[0] if values else EXPECTED["current_upstream"]

def replace_upstream(data, target):
    text = data.decode("utf-8")
    count = 0
    output = []
    for line in text.splitlines(keepends=True):
        if re.fullmatch(r"\s*(?:export\s+)?NMBOT_BRIDGE_UPSTREAM\s*=.*(?:\n)?", line):
            output.append("NMBOT_BRIDGE_UPSTREAM=" + target + "\n")
            count += 1
        else:
            output.append(line)
    if count > 1:
        fail("primary_upstream_not_unique")
    if count == 0:
        if text and not text.endswith("\n"):
            output.append("\n")
        output.append("NMBOT_BRIDGE_UPSTREAM=" + target + "\n")
    return "".join(output).encode("utf-8")

def write_atomic(path, data, mode):
    fd, temp_name = tempfile.mkstemp(prefix=".nmbot-v6-switch-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

def restart_bridge():
    result = run(["systemctl", "--user", "restart", EXPECTED["primary_bridge"]])
    return result.returncode == 0

def backup_path():
    return root / "backups" / (release_id + ".env")

def preflight():
    current = read_env()
    if upstream_from(current) != EXPECTED["current_upstream"]:
        fail("primary_upstream_not_current")
    if not protected_snapshot()["healthy"] or not bridge_ready() or not v6_ready():
        fail("preflight_health_failed")
    if status_path.exists():
        fail("switch_status_already_exists")
    return current

def restore(backup):
    try:
        write_atomic(env_path, backup.read_bytes(), stat.S_IMODE(env_path.stat().st_mode))
    except Exception:
        fail("rollback_backup_restore_failed")
    if not restart_bridge() or not bridge_ready():
        fail("rollback_bridge_restart_failed")
    if upstream_from(read_env()) != EXPECTED["current_upstream"]:
        fail("rollback_upstream_verify_failed")

if operation in {"preflight", "recon"}:
    current = read_env()
    upstream = upstream_from(current)
    route = "current" if upstream == EXPECTED["current_upstream"] else "v6" if upstream == EXPECTED["v6_upstream"] else "other"
    ok = protected_snapshot()["healthy"] and bridge_ready() and v6_ready()
    if operation == "preflight":
        ok = ok and route == "current" and not status_path.exists()
    if not ok:
        fail("preflight_health_failed" if operation == "preflight" else "recon_health_failed")
    bridge = unit_state(EXPECTED["primary_bridge"])
    emit({"ok": True, "schema": EXPECTED["schema"], "operation": operation, "v6_release_id": release_id, "primary_bridge_ready": True, "primary_bridge_route": route, "primary_bridge_systemd": {"result": bridge["result"], "exec_main_code": bridge["exec_main_code"], "exec_main_status": bridge["exec_main_status"]}, "switch_status_exists": status_path.exists(), "protected_services_healthy": True, "mutation": False})

if operation == "switch":
    before = protected_snapshot()
    original = preflight()
    backup = backup_path()
    backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if backup.exists():
        fail("switch_backup_already_exists")
    write_atomic(backup, original, 0o600)
    try:
        write_atomic(env_path, replace_upstream(original, EXPECTED["v6_upstream"]), stat.S_IMODE(env_path.stat().st_mode))
        if upstream_from(read_env()) != EXPECTED["v6_upstream"] or not restart_bridge() or not bridge_ready() or not v6_ready() or not same_protected(before, protected_snapshot()):
            restore(backup)
            fail("switch_failed_rolled_back")
        receipt = {"schema": EXPECTED["schema"], "release_id": release_id, "status": "active", "backup_sha256": hashlib.sha256(original).hexdigest()}
        write_atomic(status_path, (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8"), 0o600)
    except SystemExit:
        raise
    except Exception:
        restore(backup)
        fail("switch_failed_rolled_back")
    emit({"ok": True, "schema": EXPECTED["schema"], "operation": operation, "v6_release_id": release_id, "primary_bridge_route": "v6", "protected_services_unchanged": True, "rollback": "restore_primary_bridge_backup", "mutation": True})

if operation == "rollback":
    before = protected_snapshot()
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        fail("switch_status_missing")
    backup = backup_path()
    if status.get("release_id") != release_id or status.get("status") != "active" or not backup.is_file() or backup.is_symlink():
        fail("switch_status_mismatch")
    try:
        if status.get("backup_sha256") != hashlib.sha256(backup.read_bytes()).hexdigest():
            fail("switch_backup_hash_mismatch")
    except SystemExit:
        raise
    except Exception:
        fail("switch_backup_unreadable")
    restore(backup)
    if not v6_ready() or not same_protected(before, protected_snapshot()):
        fail("rollback_invariant_failed")
    status["status"] = "rolled_back"
    write_atomic(status_path, (json.dumps(status, sort_keys=True) + "\n").encode("utf-8"), 0o600)
    emit({"ok": True, "schema": EXPECTED["schema"], "operation": operation, "v6_release_id": release_id, "primary_bridge_route": "current", "protected_services_unchanged": True, "mutation": True})
'''


def target_payload(operation: str, *, expected_v6_release: str) -> dict[str, str]:
    if operation not in {"preflight", "recon", "switch", "rollback"}:
        raise PrimaryV6SwitchError("unsupported operation")
    if not RELEASE_RE.fullmatch(expected_v6_release):
        raise PrimaryV6SwitchError("invalid V6 release ID")
    return {
        "schema": SCHEMA,
        "operation": operation,
        "expected_v6_release": expected_v6_release,
        "primary_env": PRIMARY_ENV,
        "switch_root": SWITCH_ROOT,
        "primary_bridge": PRIMARY_BRIDGE,
        "primary_api": PRIMARY_API,
        "client_api": CLIENT_API,
        "client_bridge": CLIENT_BRIDGE,
        "v6_health": V6_HEALTH,
        "primary_bridge_health": PRIMARY_BRIDGE_HEALTH,
        "primary_api_health": PRIMARY_API_HEALTH,
        "client_api_health": CLIENT_API_HEALTH,
        "client_bridge_health": CLIENT_BRIDGE_HEALTH,
        "current_upstream": CURRENT_UPSTREAM,
        "v6_upstream": V6_UPSTREAM,
    }


def _remote_command(payload: Mapping[str, Any]) -> list[str]:
    program = base64.b64encode(REMOTE_PROGRAM.encode("utf-8")).decode("ascii")
    encoded = base64.b64encode(json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")).decode("ascii")
    bootstrap = "import base64,sys;code=base64.b64decode(sys.argv[1]).decode('utf-8');sys.argv=[sys.argv[0],sys.argv[2]];exec(code)"
    remote = "python3 -c " + shlex.quote(bootstrap) + " " + shlex.quote(program) + " " + shlex.quote(encoded)
    return ["ssh", "-p", SSH_PORT, *SSH_OPTIONS, HOST, remote]


def _safe_remote_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError as exc:
        raise PrimaryV6SwitchError("primary switch returned a malformed receipt") from exc
    if result.returncode != 0 or not isinstance(payload, dict) or payload.get("ok") is not True or payload.get("schema") != SCHEMA:
        reason = payload.get("error") if isinstance(payload, dict) else "remote_operation_failed"
        raise PrimaryV6SwitchError(f"primary switch operation failed: {reason}")
    return payload


class RemoteClient:
    def __init__(self, *, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self._run = run

    def operation(self, payload: Mapping[str, Any], *, timeout: int = 120) -> dict[str, Any]:
        return _safe_remote_result(self._run(_remote_command(payload), text=True, capture_output=True, timeout=timeout, check=False))


def _require_confirmation(operation: str, release_id: str, apply: bool, confirmation: str) -> None:
    expected = f"{operation.upper()}-PRIMARY-BRIDGE-{release_id}-{'TO-V6' if operation == 'switch' else 'TO-CURRENT'}"
    if not apply or confirmation != expected:
        raise PrimaryV6SwitchError(f"{operation} confirmation must be exactly {expected}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    for name in ("preflight", "recon", "switch", "rollback"):
        child = commands.add_parser(name)
        child.add_argument("--expected-v6-release", required=name != "plan")
        if name in {"switch", "rollback"}:
            child.add_argument("--apply", action="store_true")
            child.add_argument("--confirm", default="")
    return parser


def main(argv: list[str] | None = None, *, client: RemoteClient | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            result = TargetContract().as_dict()
        else:
            payload = target_payload(args.command, expected_v6_release=args.expected_v6_release)
            if args.command in {"switch", "rollback"}:
                _require_confirmation(args.command, args.expected_v6_release, args.apply, args.confirm)
            result = (client or RemoteClient()).operation(payload, timeout=150 if args.command == "switch" else 90)
    except (OSError, ValueError, PrimaryV6SwitchError) as exc:
        _parser().error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
