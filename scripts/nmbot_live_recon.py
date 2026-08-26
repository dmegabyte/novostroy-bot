#!/usr/bin/env python3
"""Read one explicit V6 live contour over bounded read-only SSH."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from typing import Any


SCHEMA_VERSION = "nmbot.v6_live_recon.v1"
SAFE_HOST_RE = re.compile(r"^[a-z_][a-z0-9_-]*@[A-Za-z0-9.-]+$")
SAFE_PORT_RE = re.compile(r"^[0-9]{1,5}$")

CONTOURS: dict[str, dict[str, Any]] = {
    "primary": {
        "host": "neiro@193.107.155.236",
        "port": "1905",
        "remote_root": "/home/neiro/novostroy-bot",
        "services": {
            "api": "novostroy-bot-api.service",
            "bridge": "novostroy-bot-n8n-bridge.service",
        },
        "health_urls": {
            "api": "http://127.0.0.1:8088/health",
            "bridge": "http://127.0.0.1:8093/health",
        },
        "traffic_role": "unverified",
    },
    "client-production": {
        "host": "neiro@193.107.155.236",
        "port": "1905",
        "remote_root": "/home/neiro/novostroy-bot-client-production",
        "services": {
            "api": "novostroy-bot-client-production-api.service",
            "bridge": "novostroy-bot-client-production-n8n-bridge.service",
        },
        "health_urls": {
            "api": "http://127.0.0.1:8188/health",
            "bridge": "http://127.0.0.1:8193/health",
        },
        "traffic_role": "unverified",
    },
}


class LiveReconError(ValueError):
    pass


def _validated_spec(contour: str) -> dict[str, Any]:
    try:
        spec = CONTOURS[contour]
    except KeyError as exc:
        raise LiveReconError("unknown live contour") from exc
    host = spec.get("host")
    port = spec.get("port")
    root = spec.get("remote_root")
    services = spec.get("services")
    health_urls = spec.get("health_urls")
    if (
        not isinstance(host, str)
        or not SAFE_HOST_RE.fullmatch(host)
        or not isinstance(port, str)
        or not SAFE_PORT_RE.fullmatch(port)
        or not 1 <= int(port) <= 65535
        or not isinstance(root, str)
        or not root.startswith("/")
        or not isinstance(services, dict)
        or set(services) != {"api", "bridge"}
        or not isinstance(health_urls, dict)
        or set(health_urls) != {"api", "bridge"}
        or spec.get("traffic_role") != "unverified"
    ):
        raise LiveReconError("invalid live contour definition")
    if not all(
        isinstance(value, str)
        and value.endswith(".service")
        and not any(char.isspace() for char in value)
        for value in services.values()
    ):
        raise LiveReconError("invalid live contour service definition")
    if not all(
        isinstance(value, str)
        and re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}/health", value)
        for value in health_urls.values()
    ):
        raise LiveReconError("invalid live contour health definition")
    return spec


def build_remote_command(*, contour: str, spec: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "contour": contour,
            "remote_root": spec["remote_root"],
            "services": spec["services"],
            "health_urls": spec["health_urls"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    program = r'''
import datetime
import json
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request

p = json.loads(sys.argv[1])
root = pathlib.Path(p["remote_root"])
safe_release = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

def unit(name):
    try:
        proc = subprocess.run(
            [
                "systemctl", "--user", "show", name,
                "--property=LoadState", "--property=ActiveState",
                "--property=SubState", "--property=WorkingDirectory",
                "--property=ExecStart", "--no-pager",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return {"query_ok": False, "loaded": False, "active": False, "running": False}
    properties = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            properties[key] = value
    working_directory = properties.get("WorkingDirectory", "")
    exec_start = properties.get("ExecStart", "")
    root_text = str(root).rstrip("/") + "/"
    return {
        "query_ok": proc.returncode == 0,
        "loaded": properties.get("LoadState") == "loaded",
        "active": properties.get("ActiveState") == "active",
        "running": properties.get("SubState") == "running",
        "working_directory_under_root": working_directory == str(root) or working_directory.startswith(root_text),
        "exec_start_under_root": root_text in exec_start,
    }

def safe_release_id(value):
    text = str(value or "").strip()
    return text if safe_release.fullmatch(text) and text not in {".", "..", "UNKNOWN"} else None

def health(url, kind):
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=3) as response:
            status = int(response.status)
            raw = response.read(65537)
        if len(raw) > 65536:
            return {"ok": False, "error_code": "response_too_large"}
        body = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error_code": "http_error", "http_status": int(exc.code)}
    except Exception:
        return {"ok": False, "error_code": "request_failed"}
    if not isinstance(body, dict):
        return {"ok": False, "error_code": "malformed_response", "http_status": status}
    result = {
        "ok": body.get("ok") is True,
        "http_status": status,
        "service": body.get("service") if body.get("service") in {"nmbot-api", "nmbot-n8n-bridge"} else None,
    }
    if kind == "api":
        runtime = str(body.get("runtime") or body.get("runtime_version") or "").strip().upper()
        profile = str(body.get("profile") or "").strip().upper()
        result.update({
            "runtime": runtime if runtime == "V6" else None,
            "profile": profile if profile in {"TEST", "PROD"} else None,
            "release_id": safe_release_id(body.get("release_id")),
            "api_token_configured": body.get("api_token_configured") if isinstance(body.get("api_token_configured"), bool) else None,
        })
    else:
        route = body.get("route") if isinstance(body.get("route"), dict) else {}
        profile = str(route.get("profile") or "").strip().upper()
        slot = str(route.get("slot") or "").strip().upper()
        upstream_ref = str(route.get("upstream_ref") or "").strip().lower()
        result["route"] = {
            "status": route.get("status") if route.get("status") in {"ready", "invalid"} else None,
            "mode": route.get("mode") if route.get("mode") in {"active_route", "static_migration"} else None,
            "profile": profile if profile in {"TEST", "PROD"} else None,
            "slot": slot if slot in {"A", "B"} else None,
            "release_id": safe_release_id(route.get("release_id")),
            "upstream_ref": upstream_ref if re.fullmatch(r"[a-f0-9]{16}", upstream_ref) else None,
        }
    return result

current = root / "current"
identity_release = None
current_target = None
try:
    resolved = current.resolve(strict=True)
    if resolved == root or root in resolved.parents:
        current_target = resolved.name if safe_release.fullmatch(resolved.name) else None
        identity_path = resolved / "release_identity" / "nmbot_release_identity.json"
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        if isinstance(identity, dict) and identity.get("schema") == "nmbot.release_identity.v1":
            identity_release = safe_release_id(identity.get("release_id"))
except Exception:
    pass

services = {key: unit(value) for key, value in p["services"].items()}
health_receipts = {key: health(value, key) for key, value in p["health_urls"].items()}
source_root_verified = all(
    receipt.get("query_ok") is True
    and receipt.get("working_directory_under_root") is True
    and receipt.get("exec_start_under_root") is True
    for receipt in services.values()
)
service_health_ok = all(
    receipt.get("loaded") is True and receipt.get("active") is True and receipt.get("running") is True
    for receipt in services.values()
) and all(receipt.get("ok") is True for receipt in health_receipts.values())

api_health = health_receipts["api"]
bridge_route = health_receipts["bridge"].get("route") or {}
api_release = api_health.get("release_id")
bridge_release = bridge_route.get("release_id")
identity_match = None
if identity_release and api_release:
    identity_match = identity_release == api_release and (bridge_release in {None, identity_release})
profile_match = None
if api_health.get("profile") and bridge_route.get("profile"):
    profile_match = api_health["profile"] == bridge_route["profile"]
v6_contract_verified = (
    source_root_verified
    and api_health.get("runtime") == "V6"
    and api_health.get("profile") in {"TEST", "PROD"}
    and identity_match is True
    and profile_match is True
    and bridge_route.get("status") == "ready"
)

print(json.dumps({
    "schema_version": "nmbot.v6_live_recon.v1",
    "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "contour": p["contour"],
    "traffic_role": "unverified",
    "current": {
        "present": current.exists(),
        "is_symlink": current.is_symlink(),
        "target_release": current_target,
        "identity_release_id": identity_release,
    },
    "services": services,
    "source_root": "verified" if source_root_verified else "unverified",
    "health": health_receipts,
    "service_health": "healthy" if service_health_ok else "unverified",
    "identity_match": identity_match,
    "profile_match": profile_match,
    "v6_contract": "verified" if v6_contract_verified else "unverified",
}, ensure_ascii=False, sort_keys=True))
'''.strip()
    return "python3 -c " + shlex.quote(program) + " " + shlex.quote(payload)


def run_recon(*, contour: str) -> dict[str, Any]:
    spec = _validated_spec(contour)
    command = build_remote_command(contour=contour, spec=spec)
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-p",
                spec["port"],
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=15",
                spec["host"],
                command,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LiveReconError("SSH live recon unavailable") from exc
    if proc.returncode != 0:
        raise LiveReconError("SSH live recon failed")
    if len(proc.stdout.encode("utf-8")) > 65536:
        raise LiveReconError("live recon receipt is too large")
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise LiveReconError("live recon returned malformed JSON") from exc
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != SCHEMA_VERSION
        or result.get("contour") != contour
        or result.get("traffic_role") != "unverified"
    ):
        raise LiveReconError("live recon receipt does not match the selected contour")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contour", required=True, choices=tuple(CONTOURS))
    args = parser.parse_args(argv)
    try:
        result = run_recon(contour=args.contour)
    except LiveReconError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
