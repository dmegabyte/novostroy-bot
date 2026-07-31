#!/usr/bin/env python3
"""Compare live NMBot V2 source and runtime versions across VPS contours.

Usage: python3 scripts/nmbot_v2_version_compare.py
The command is read-only: it only calculates remote SHA-256 hashes and calls
the protected loopback runtime-version endpoint on each contour.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Any


HOST = "neiro@193.107.155.236"
PORT = "1905"


@dataclass(frozen=True)
class Contour:
    name: str
    root: str
    env_file: str
    api_port: int


CONTOURS = (
    Contour("test", "/home/neiro/novostroy-bot", ".env", 8088),
    Contour("client-production", "/home/neiro/novostroy-bot-client-production", ".env.client-production", 8188),
)


REMOTE_CHECK = r'''
import hashlib, json, sys, urllib.request
from pathlib import Path

cfg = json.loads(sys.argv[1])
root = Path(cfg["root"])
env_path = root / cfg["env_file"]

def fail(message):
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    raise SystemExit(1)

if not env_path.is_file():
    fail("runtime env file is missing")

token = ""
for raw in env_path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        if key.strip() == "NMBOT_API_TOKEN":
            token = value.strip().strip('"').strip("'")
            break
if not token:
    fail("NMBOT_API_TOKEN is missing")

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

files = {}
v2_dir = root / "nmbot_v2"
if not v2_dir.is_dir():
    fail("nmbot_v2 source directory is missing")
for path in sorted(v2_dir.glob("*.py")):
    files[str(path.relative_to(root))] = digest(path)
for path in sorted((root / "prompts").glob("v2_*.txt")):
    files[str(path.relative_to(root))] = digest(path)

url = "http://127.0.0.1:%d/api/runtime-version" % int(cfg["api_port"])
request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
try:
    with urllib.request.urlopen(request, timeout=5) as response:
        runtime = json.loads(response.read().decode("utf-8"))
except Exception as exc:
    fail("runtime-version request failed: " + type(exc).__name__)
if not isinstance(runtime, dict) or not runtime.get("ok"):
    fail("runtime-version returned an invalid response")
print(json.dumps({"ok": True, "runtime_version": runtime.get("runtime_version"), "files": files}, ensure_ascii=False, sort_keys=True))
'''


def run_contour(contour: Contour) -> dict[str, Any]:
    config = json.dumps({"root": contour.root, "env_file": contour.env_file, "api_port": contour.api_port})
    proc = subprocess.run(
        ["ssh", "-p", PORT, "-o", "BatchMode=yes", HOST, "python3 - " + shlex.quote(config)],
        input=REMOTE_CHECK,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        payload = {"ok": False, "error": "remote check returned no JSON", "detail": proc.stderr.strip()[-200:]}
    if proc.returncode != 0 and payload.get("ok"):
        payload = {"ok": False, "error": "remote check failed"}
    return payload


def compare(test: dict[str, Any], client_production: dict[str, Any]) -> dict[str, Any]:
    if not test.get("ok") or not client_production.get("ok"):
        return {"match": False, "reason": "contour_check_failed", "different_files": []}
    test_files = test.get("files", {})
    prod_files = client_production.get("files", {})
    paths = sorted(set(test_files) | set(prod_files))
    different = [path for path in paths if test_files.get(path) != prod_files.get(path)]
    runtime_match = test.get("runtime_version") == client_production.get("runtime_version")
    return {
        "match": not different and runtime_match,
        "source_match": not different,
        "runtime_match": runtime_match,
        "test_runtime_version": test.get("runtime_version"),
        "client_production_runtime_version": client_production.get("runtime_version"),
        "different_files": different,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="include per-file hashes for both contours")
    args = parser.parse_args(argv)
    results = {contour.name: run_contour(contour) for contour in CONTOURS}
    summary = compare(results["test"], results["client-production"])
    payload: dict[str, Any] = {"ok": summary["match"], "comparison": summary}
    if args.verbose:
        payload["contours"] = results
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if summary["match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
