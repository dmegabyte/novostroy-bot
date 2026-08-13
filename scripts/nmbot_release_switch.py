#!/usr/bin/env python3
"""Switch an already-installed immutable API release on the fixed TEST host."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from typing import Any, Protocol

HOST = "neiro@193.107.155.236"
PORT = "1905"
ROOT = "/home/neiro/novostroy-bot"
SERVICE = "novostroy-bot-api.service"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class Runner(Protocol):
    def run(self, command: str) -> subprocess.CompletedProcess[str]: ...


class SSHRunner:
    def run(self, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["ssh", "-o", "BatchMode=yes", "-p", PORT, HOST, command], text=True, capture_output=True, check=False)


def validate_release_id(value: str) -> str:
    value = str(value or "").strip()
    if not SAFE_ID.fullmatch(value) or value in {".", ".."} or value.startswith("-"):
        raise ValueError("release id must be a safe basename")
    return value


REMOTE = r'''
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.request
import uuid

FIXED_ROOT = "/home/neiro/novostroy-bot"
FIXED_SERVICE = "novostroy-bot-api.service"
ROOT = pathlib.Path(FIXED_ROOT)
RELEASES = ROOT / "releases"
CURRENT = ROOT / "current"
PREVIOUS = ROOT / "previous"
DATA = ROOT / "data"
EXTERNAL = DATA / "nmbot_release_identity.json"
LOCK = ROOT / ".release_switch_lock"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_KEYS = {"schema", "release_id", "generated_at", "tracked_files"}
ROW_KEYS = {"path", "sha256"}
SYSTEMCTL_TIMEOUT = 8
READINESS_SECONDS = 15
HEALTH_TIMEOUT = 2


class SwitchError(RuntimeError):
    pass


def fail(message):
    raise SwitchError(message)


def fixed_paths(config):
    if config.get("root") != FIXED_ROOT or config.get("service") != FIXED_SERVICE:
        fail("fixed root/service contract mismatch")
    if ROOT.is_symlink() or not ROOT.is_dir() or ROOT.resolve() != ROOT:
        fail("fixed root is missing or symlinked")
    if RELEASES.is_symlink() or not RELEASES.is_dir() or RELEASES.resolve() != RELEASES:
        fail("releases directory is missing or symlinked")
    if DATA.is_symlink() or not DATA.is_dir() or DATA.resolve() != DATA:
        fail("identity data directory is missing or symlinked")
    if EXTERNAL.is_symlink():
        fail("external identity must not be a symlink")


def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    path = pathlib.PurePosixPath(value)
    return not path.is_absolute() and all(part not in ("", ".", "..") for part in path.parts)


def manifest(path, release_id, release_dir=None):
    identity_dir = path.parent
    if identity_dir.is_symlink() or not identity_dir.is_dir():
        fail("release identity directory missing or symlinked")
    if path.is_symlink() or not path.is_file():
        fail("identity manifest missing or symlinked")
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("identity manifest invalid")
    if not isinstance(data, dict) or set(data) != MANIFEST_KEYS:
        fail("identity manifest schema fields invalid")
    generated = data.get("generated_at")
    if (data.get("schema") != "nmbot.release_identity.v1" or
            data.get("release_id") != release_id or
            not isinstance(generated, str) or generated != generated.strip() or
            not 1 <= len(generated) <= 128 or any(ord(ch) < 32 or ord(ch) == 127 for ch in generated)):
        fail("identity manifest mismatch")
    rows = data.get("tracked_files")
    if not isinstance(rows, list) or not rows:
        fail("identity manifest tracked files invalid")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            fail("identity manifest tracked file shape invalid")
        relative = row.get("path")
        digest = row.get("sha256")
        if not safe_relative(relative) or relative in seen or not isinstance(digest, str) or not HEX64.fullmatch(digest):
            fail("identity manifest tracked file value invalid")
        seen.add(relative)
        if release_dir is not None:
            tracked = release_dir / pathlib.PurePosixPath(relative)
            try:
                resolved = tracked.resolve(strict=True)
            except (OSError, RuntimeError):
                fail("tracked file missing")
            if tracked.is_symlink() or not tracked.is_file() or resolved != tracked or resolved.parent == release_dir.parent:
                fail("tracked file is unsafe")
            if release_dir not in resolved.parents:
                fail("tracked file escapes release")
            if hashlib.sha256(tracked.read_bytes()).hexdigest() != digest:
                fail("tracked file hash mismatch")
    return raw


def release(release_id):
    if not isinstance(release_id, str) or not SAFE_ID.fullmatch(release_id) or release_id in (".", ".."):
        fail("unsafe release id")
    path = RELEASES / release_id
    if path.is_symlink() or not path.is_dir() or path.resolve() != path or path.parent != RELEASES:
        fail("release directory missing or symlinked")
    manifest(path / "release_identity/nmbot_release_identity.json", release_id, path)
    return path


def marker(marker_path, missing_message):
    if not marker_path.is_symlink():
        fail(missing_message)
    link = os.readlink(marker_path)
    name = pathlib.PurePosixPath(link).name
    if link != "releases/" + name or not SAFE_ID.fullmatch(name):
        fail("release marker is not an exact release link")
    path = release(name)
    if marker_path.resolve() != path:
        fail("release marker target mismatch")
    return name, path


def current():
    release_id, path = marker(CURRENT, "current is not an exact release symlink")
    local = manifest(path / "release_identity/nmbot_release_identity.json", release_id, path)
    external = manifest(EXTERNAL, release_id)
    if external != local:
        fail("external identity bytes do not match current release")
    return release_id, path


def run_systemctl(action, check=True):
    return subprocess.run(
        ["systemctl", "--user", action, FIXED_SERVICE], text=True,
        capture_output=True, check=check, timeout=SYSTEMCTL_TIMEOUT,
    )


def health(release_id, wait=True):
    deadline = time.monotonic() + (READINESS_SECONDS if wait else 0)
    last = "health/service"
    while True:
        try:
            response = urllib.request.urlopen("http://127.0.0.1:8088/health", timeout=HEALTH_TIMEOUT)
            data = json.loads(response.read().decode("utf-8"))
            state = run_systemctl("is-active", check=False).stdout.strip()
            active, _ = current()
            if data.get("ok") is True and state == "active" and active == release_id:
                return {"ok": True, "service": state}
            last = "health/service/identity"
        except Exception as exc:
            last = type(exc).__name__
        if time.monotonic() >= deadline:
            return {"ok": False, "error": last}
        time.sleep(1)


def atomic_link(path, release_id):
    tmp = ROOT / ("." + path.name + ".switch." + uuid.uuid4().hex + ".tmp")
    try:
        os.symlink("releases/" + release_id, tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def prepare_identity(raw):
    fixed_paths({"root": FIXED_ROOT, "service": FIXED_SERVICE})
    tmp = EXTERNAL.with_name(".nmbot_release_identity.switch." + uuid.uuid4().hex + ".tmp")
    tmp.write_bytes(raw)
    os.chmod(tmp, 0o600)
    if tmp.is_symlink() or tmp.read_bytes() != raw:
        tmp.unlink(missing_ok=True)
        fail("staged external identity verification failed")
    return tmp


def replace_identity(tmp):
    fixed_paths({"root": FIXED_ROOT, "service": FIXED_SERVICE})
    os.replace(tmp, EXTERNAL)


def restore_identity(raw):
    tmp = prepare_identity(raw)
    try:
        replace_identity(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def acquire_lock():
    owner = uuid.uuid4().hex
    try:
        LOCK.mkdir()
    except FileExistsError:
        fail("release switch lock already exists")
    try:
        (LOCK / "owner").write_text(owner, encoding="ascii")
    except Exception:
        (LOCK / "owner").unlink(missing_ok=True)
        LOCK.rmdir()
        raise
    return owner


def release_lock(owner):
    try:
        owner_path = LOCK / "owner"
        if owner_path.read_text(encoding="ascii") == owner:
            owner_path.unlink()
            LOCK.rmdir()
    except OSError:
        pass


def switch(config):
    fixed_paths(config)
    operation = config.get("op")
    target = config.get("target")
    previous_id, _ = current()
    if operation == "status":
        release(target)
        if target != previous_id:
            fail("requested release is not current")
        result = health(previous_id, wait=False)
        if not result.get("ok"):
            fail("current release health failed")
        return {"status": "ok", "current": previous_id, "target": target, "health": result}
    if operation == "rollback":
        target, _ = marker(PREVIOUS, "no unambiguous previous release; use --switch-to")
    elif operation != "switch":
        fail("unsupported operation")
    target_dir = release(target)
    if target == previous_id:
        fail("target is already current")
    if PREVIOUS.is_symlink():
        marker_id, _ = marker(PREVIOUS, "previous marker is invalid")
        marker_before = "releases/" + marker_id
    elif PREVIOUS.exists():
        fail("previous marker is not a symlink")
    else:
        marker_before = None
    if not config.get("confirm"):
        return {"status": "dry_run", "previous": previous_id, "target": target,
                "current": previous_id, "previous_marker": marker_before}

    owner = acquire_lock()
    staged = None
    cutover_started = False
    backup = None
    try:
        previous_id, _ = current()
        target_dir = release(target)
        if target == previous_id:
            fail("target is already current")
        backup = EXTERNAL.read_bytes()
        target_raw = manifest(target_dir / "release_identity/nmbot_release_identity.json", target, target_dir)
        staged = prepare_identity(target_raw)
        if staged.read_bytes() != target_raw:
            fail("target identity staging mismatch")

        cutover_started = True
        run_systemctl("stop")
        state = run_systemctl("is-active", check=False).stdout.strip()
        if state not in ("inactive", "failed"):
            fail("api did not stop")
        atomic_link(CURRENT, target)
        replace_identity(staged)
        staged = None
        if EXTERNAL.read_bytes() != target_raw:
            fail("external identity bytes do not match target release")
        run_systemctl("start")
        result = health(target)
        if not result.get("ok"):
            fail("target health failed")
        atomic_link(PREVIOUS, previous_id)
        return {"status": "ok", "previous": previous_id, "target": target,
                "current": target, "previous_marker": "releases/" + previous_id,
                "rollback": {"attempted": False}, "health": result}
    except Exception as exc:
        if not cutover_started:
            raise
        rollback = {"attempted": True, "ok": False}
        try:
            run_systemctl("stop", check=False)
            atomic_link(CURRENT, previous_id)
            restore_identity(backup)
            run_systemctl("start")
            rollback["health"] = health(previous_id)
            rollback["ok"] = bool(rollback["health"].get("ok"))
        except Exception as rollback_exc:
            rollback["error"] = type(rollback_exc).__name__
        return {"status": "error", "error": str(exc), "previous": previous_id,
                "target": target, "current": previous_id if rollback["ok"] else None,
                "previous_marker": marker_before, "rollback": rollback}
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
        release_lock(owner)


def main():
    try:
        config = json.loads(sys.argv[1])
        result = switch(config)
    except Exception as exc:
        result = {"status": "error", "error": str(exc), "current": None}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") in ("ok", "dry_run") else 2


if __name__ == "__main__":
    raise SystemExit(main())
'''


def remote_command(op: str, target: str | None, confirm: bool) -> str:
    payload = json.dumps({"root": ROOT, "service": SERVICE, "op": op, "target": target, "confirm": confirm}, sort_keys=True)
    return "python3 -c " + shlex.quote(REMOTE) + " " + shlex.quote(payload)


def execute(*, op: str, target: str | None = None, confirm: bool = False, runner: Runner | None = None) -> dict[str, Any]:
    if target is not None:
        target = validate_release_id(target)
    proc = (runner or SSHRunner()).run(remote_command(op, target, confirm))
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        result = {"status": "error", "error": "remote returned no structured result"}
    if proc.returncode and result.get("status") != "error": result = {"status": "error", "error": "remote command failed"}
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Switch fixed TEST API immutable releases.")
    g = p.add_mutually_exclusive_group(required=True); g.add_argument("--status", action="store_true"); g.add_argument("--switch-to"); g.add_argument("--rollback", action="store_true")
    p.add_argument("--release-id", help="Required with --status")
    p.add_argument("--confirm", action="store_true")
    a = p.parse_args(argv)
    if a.status and not a.release_id: p.error("--status requires --release-id ID")
    if a.release_id and not a.status: p.error("--release-id is only for --status")
    try: result = execute(op="status" if a.status else ("rollback" if a.rollback else "switch"), target=a.release_id if a.status else a.switch_to, confirm=a.confirm)
    except ValueError as exc: result = {"status":"error","error":str(exc)}
    print(json.dumps(result, sort_keys=True)); return 0 if result.get("status") in {"ok","dry_run"} else 2


if __name__ == "__main__": raise SystemExit(main())
