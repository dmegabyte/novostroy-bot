#!/usr/bin/env python3
"""Read-only identity receipt for one explicitly selected NMBot contour."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "nmbot_deployment_contours.json"
SCHEMA_VERSION = "nmbot.contour_recon.v1"
REGISTRY_SCHEMA_VERSION = "nmbot.deployment_contours.v1"
SAFE_CONTOUR = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SAFE_PORT = re.compile(r"^[0-9]{1,5}$")


class ReconError(ValueError):
    pass


def _safe_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(ch.isspace() for ch in value):
        raise ReconError(f"invalid {field}")
    return value


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconError(f"cannot read contour registry: {path}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ReconError("unsupported contour registry schema")
    contours = data.get("contours")
    if not isinstance(contours, dict) or not contours:
        raise ReconError("contour registry has no contours")
    validated: dict[str, dict[str, Any]] = {}
    for name, spec in contours.items():
        if not isinstance(name, str) or not SAFE_CONTOUR.fullmatch(name) or not isinstance(spec, dict):
            raise ReconError("invalid contour registry entry")
        host = _safe_text(spec.get("host"), field=f"host for {name}")
        port = _safe_text(spec.get("port"), field=f"port for {name}")
        root = _safe_text(spec.get("remote_root"), field=f"remote_root for {name}")
        env_file = _safe_text(spec.get("environment_file"), field=f"environment_file for {name}")
        services = spec.get("services")
        health_urls = spec.get("health_urls")
        if (not SAFE_PORT.fullmatch(port) or not port.isdigit() or not 1 <= int(port) <= 65535 or
                not host.count("@") == 1 or not root.startswith("/") or not env_file.startswith("/") or
                not isinstance(services, dict) or set(services) not in ({"api", "bridge"}, {"api", "bridge", "callback_worker"}) or
                not isinstance(health_urls, dict) or set(health_urls) != {"api", "bridge"}):
            raise ReconError(f"invalid contour registry entry: {name}")
        for service in services.values():
            if not isinstance(service, str) or not service.endswith(".service") or any(ch.isspace() for ch in service):
                raise ReconError(f"invalid service for {name}")
        for url in health_urls.values():
            if not isinstance(url, str) or not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}/health", url):
                raise ReconError(f"invalid health URL for {name}")
        if spec.get("traffic_role") != "unverified":
            raise ReconError(f"traffic role must remain unverified until a correlated Jivo trace: {name}")
        validated[name] = {
            "host": host,
            "port": port,
            "remote_root": root,
            "environment_file": env_file,
            "services": {key: services[key] for key in ("api", "bridge")},
            "health_urls": dict(health_urls),
            "traffic_role": "unverified",
        }
    return validated


def build_remote_command(*, contour: str, spec: dict[str, Any]) -> str:
    payload = json.dumps({"contour": contour, **spec}, sort_keys=True, separators=(",", ":"))
    program = r'''
import json, pathlib, re, subprocess, sys, urllib.request
p = json.loads(sys.argv[1])
root = pathlib.Path(p["remote_root"])
services = p["services"]
health_urls = p["health_urls"]
def unit(name, *, execution=False, timer=False):
    properties = ["Id", "LoadState", "ActiveState", "SubState", "FragmentPath", "WorkingDirectory", "ExecStart"]
    if execution:
        properties.extend(["Result", "ExecMainCode", "ExecMainStatus", "ActiveEnterTimestamp", "InactiveEnterTimestamp"])
    if timer:
        properties.extend(["Result", "LastTriggerUSec", "NextElapseUSecRealtime"])
    proc = subprocess.run(["systemctl", "--user", "show", name,
        *["--property=" + item for item in properties], "--no-pager"], capture_output=True, text=True, check=False)
    return {"returncode": proc.returncode, "properties": proc.stdout.splitlines()}
def journal_shape(path):
    result = {"path": str(path), "exists": path.is_file(), "lines": 0, "malformed": 0,
              "valid_conversation_ref": 0, "latest_ts": None,
              "by_utc_date": {"2026-08-27": 0, "2026-08-28": 0}}
    if not result["exists"] or path.is_symlink():
        return result
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                result["lines"] += 1
                try:
                    row = json.loads(line)
                except Exception:
                    result["malformed"] += 1
                    continue
                ref = row.get("conversation_ref") if isinstance(row, dict) else None
                if not isinstance(ref, str) or not ref.startswith("sha256:"):
                    continue
                result["valid_conversation_ref"] += 1
                raw_ts = row.get("ts") or row.get("timestamp") or row.get("created_at")
                if isinstance(raw_ts, str):
                    if result["latest_ts"] is None or raw_ts > result["latest_ts"]:
                        result["latest_ts"] = raw_ts[:40]
                    date = raw_ts[:10]
                    if date in result["by_utc_date"]:
                        result["by_utc_date"][date] += 1
    except Exception as exc:
        result["read_error"] = type(exc).__name__
    return result
def env_shape(path):
    values = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.removeprefix("export ").strip()
            value = value.strip().strip("\"'")
            if key in {"NMBOT_DIALOGUE_JOURNAL", "NMBOT_DIALOGUE_EXPORT_SHEET_ID", "NMBOT_DIALOGUE_EXPORT_SHEET_TAB"}:
                values[key] = value
            elif key in {"NMBOT_DIALOGUE_EXPORT_GOOGLE_CREDENTIALS", "NMBOT_CALLBACK_GOOGLE_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SHEETS_CREDENTIALS", "GOOGLE_CREDENTIALS"}:
                values[key] = bool(value)
    except Exception as exc:
        return {"readable": False, "error": type(exc).__name__}
    journal = values.get("NMBOT_DIALOGUE_JOURNAL")
    journal_targets = {
        str(root / "logs" / "dialogue_journal.jsonl"): "fixed_root",
        str(current / "logs" / "dialogue_journal.jsonl"): "api_current",
        str(root / "bridge-current" / "logs" / "dialogue_journal.jsonl"): "bridge_current",
    }
    sheet = values.get("NMBOT_DIALOGUE_EXPORT_SHEET_ID")
    tab = values.get("NMBOT_DIALOGUE_EXPORT_SHEET_TAB")
    credential_keys = [key for key in values if key in {"NMBOT_DIALOGUE_EXPORT_GOOGLE_CREDENTIALS", "NMBOT_CALLBACK_GOOGLE_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SHEETS_CREDENTIALS", "GOOGLE_CREDENTIALS"} and values[key]]
    return {
        "readable": True,
        "journal_target": "default_split" if journal is None else journal_targets.get(journal, "other"),
        "sheet_target": "default_expected" if sheet is None else ("expected" if sheet == "1lE_aDxYGVtsl3SgFE9pZY-7ByxDu6zNkyZ1Qr1F0-EQ" else "other"),
        "tab_target": "default_expected" if tab is None else ("expected" if tab == "Диалоги" else "other"),
        "credentials_configured": bool(credential_keys),
    }
def exporter_log_result():
    proc = subprocess.run(["journalctl", "--user", "--unit=nmbot-dialogue-sheet-export.service",
        "--lines=80", "--no-pager", "--output=cat"], capture_output=True, text=True, check=False)
    text = proc.stdout
    writes = re.findall(r"mode=write\s+tab=\S+\s+total=(\d+)\s+updated=(\d+)\s+appended=(\d+)", text)
    verifies = re.findall(r"verify=ok\s+headers=ok\s+first_id=(present|empty)", text)
    known = [code for code in ("dialogue_sheet_header_mismatch", "dialogue_sheet_readback_header_mismatch",
        "missing_google_credentials", "missing_google_credentials_file", "unsafe_google_credentials_permissions") if code in text]
    return {
        "returncode": proc.returncode,
        "last_write": ({"total": int(writes[-1][0]), "updated": int(writes[-1][1]), "appended": int(writes[-1][2])} if writes else None),
        "last_verify": ({"ok": True, "first_id": verifies[-1]} if verifies else None),
        "known_error_codes": known,
    }
def health(url):
    try:
        return {"ok": True, "body": json.loads(urllib.request.urlopen(url, timeout=3).read().decode("utf-8"))}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
current = root / "current"
identity = current / "release_identity" / "nmbot_release_identity.json"
release_id = None
try:
    release_id = json.loads(identity.read_text(encoding="utf-8")).get("release_id")
except Exception:
    pass
print(json.dumps({
    "schema_version": "nmbot.contour_recon.v1", "contour": p["contour"],
    "remote_root": str(root), "current": str(current.resolve()) if current.exists() else None,
    "release_id": release_id, "services": {key: unit(value) for key, value in services.items()},
    "dialogue_exporter": {
        "service": unit("nmbot-dialogue-sheet-export.service", execution=True),
        "timer": unit("nmbot-dialogue-sheet-export.timer", timer=True),
        "journals": {
            "fixed_root": journal_shape(root / "logs" / "dialogue_journal.jsonl"),
            "api_current": journal_shape(current / "logs" / "dialogue_journal.jsonl"),
            "bridge_current": journal_shape(root / "bridge-current" / "logs" / "dialogue_journal.jsonl"),
        },
        "environment": env_shape(pathlib.Path(p["environment_file"])),
        "last_log_result": exporter_log_result(),
        "script_present": (root / "scripts" / "nmbot_dialogue_sheet_exporter.py").is_file(),
    },
    "health": {key: health(value) for key, value in health_urls.items()},
    "traffic_role": "unverified"
}, ensure_ascii=False, sort_keys=True))
'''.strip()
    return "python3 -c " + shlex.quote(program) + " " + shlex.quote(payload)


def run_recon(*, contour: str, spec: dict[str, Any]) -> dict[str, Any]:
    command = build_remote_command(contour=contour, spec=spec)
    proc = subprocess.run(
        ["ssh", "-p", spec["port"], "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", spec["host"], command],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ReconError((proc.stderr or proc.stdout or "SSH recon failed").strip()[-1000:])
    try:
        result = json.loads(proc.stdout.strip())
    except json.JSONDecodeError as exc:
        raise ReconError("remote recon returned invalid JSON") from exc
    if not isinstance(result, dict) or result.get("schema_version") != SCHEMA_VERSION or result.get("contour") != contour:
        raise ReconError("remote recon receipt does not match selected contour")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only identity receipt for one explicitly selected NMBot contour")
    parser.add_argument("--contour", required=True, help="contour ID from config/nmbot_deployment_contours.json")
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        registry = load_registry(args.registry)
        if args.contour not in registry:
            raise ReconError(f"unknown contour: {args.contour}; choose one of: {', '.join(sorted(registry))}")
        print(json.dumps(run_recon(contour=args.contour, spec=registry[args.contour]), ensure_ascii=False, indent=2, sort_keys=True))
    except ReconError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
