#!/usr/bin/env python3
"""Read local V6 identity, health snapshot and route files without network I/O."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from scripts.nmbot_release_identity import read_identity
    from scripts.nmbot_release_registry import ReleaseRegistryError, normalize_profile, read_route_file
except ImportError:  # direct scripts/ execution
    from nmbot_release_identity import read_identity
    from nmbot_release_registry import ReleaseRegistryError, normalize_profile, read_route_file


class DiagnosticError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticError("local snapshot is missing or malformed") from exc
    if not isinstance(payload, dict):
        raise DiagnosticError("local snapshot is malformed")
    return payload


def inspect_local(*, identity_path: Path, profile: str, health_path: Path | None = None, route_path: Path | None = None) -> dict[str, Any]:
    expected_profile = normalize_profile(profile)
    identity = read_identity(identity_path)
    release_id = str(identity.get("release_id") or "UNKNOWN")
    if release_id == "UNKNOWN":
        raise DiagnosticError("release identity is unavailable")
    result: dict[str, Any] = {"ok": True, "runtime": "V6", "profile": expected_profile, "release_id": release_id}
    if health_path is not None:
        health = _read_json(health_path)
        if health.get("ok") is not True or health.get("runtime") != "V6" or health.get("profile") != expected_profile or health.get("release_id") != release_id:
            raise DiagnosticError("health snapshot identity mismatch")
        result["health"] = "matched"
    if route_path is not None:
        try:
            route = read_route_file(route_path, expected_profile=expected_profile)
        except ReleaseRegistryError as exc:
            raise DiagnosticError("route snapshot is invalid") from exc
        if route["active"]["release_id"] != release_id:
            raise DiagnosticError("route snapshot identity mismatch")
        result["route"] = {
            "slot": route["active"]["slot"],
            "release_id": route["active"]["release_id"],
            "upstream_ref": hashlib.sha256(route["active"]["upstream"].encode("ascii")).hexdigest()[:16],
        }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--profile", choices=("TEST", "PROD"), required=True)
    parser.add_argument("--health-json", type=Path)
    parser.add_argument("--route", type=Path)
    args = parser.parse_args(argv)
    try:
        result = inspect_local(identity_path=args.identity, profile=args.profile, health_path=args.health_json, route_path=args.route)
    except (DiagnosticError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
