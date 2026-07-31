#!/usr/bin/env python3
"""Fail-closed preflight for the isolated client-production API/bridge units."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


ROOT = Path("/home/neiro/novostroy-bot-client-production")
SUPPORTED = {"V0", "V2", "V3"}
REQUIRED_SECRETS = {"JIVO_PROVIDER_ID", "JIVO_PROVIDER_TOKEN", "NMBOT_API_TOKEN", "NMBOT_N8N_BRIDGE_TOKEN"}
PATH_KEYS = {
    "NMBOT_API_STATE_FILE",
    "NMBOT_RUNTIME_VERSION_FILE",
    "NMBOT_CALLBACK_OUTBOX_DIR",
    "NMBOT_DIALOGUE_JOURNAL",
    "NMBOT_READABLE_DIALOGUE_JOURNAL",
    "NMBOT_PLANNER_TRACE_FILE",
    "NMBOT_PLANNER_TRACE_DIR",
    "NMBOT_BRIDGE_STRUCTURED_LOG",
    "NMBOT_RELEASE_IDENTITY_FILE",
}
REQUIRED_MANAGER_REWRITER_MODES = {
    "NMBOT_V2_MANAGER_REWRITER_MODE": "off",
    "NMBOT_V3_MANAGER_REWRITER_MODE": "publish",
}


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return {**values, **{key: value for key, value in os.environ.items() if key in values or key.startswith("NMBOT_") or key.startswith("JIVO_")}}


def _fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def _non_placeholder(value: str) -> bool:
    normalized = str(value or "").strip()
    return bool(normalized) and "PLACEHOLDER" not in normalized.upper()


def _normalize_mode(value: str) -> str:
    return str(value or "").strip().lower()


def _require_under_root(raw: str, key: str) -> Path:
    if not raw:
        _fail(f"{key} missing")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        _fail(f"{key} must be absolute")
    try:
        path.relative_to(ROOT)
    except ValueError:
        _fail(f"{key} must be under {ROOT}")
    return path


def _atomic_write_selector(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": version}, fh, ensure_ascii=False, separators=(",", ":"))
            fh.write("\n")
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _validate_selector(path: Path, *, bootstrap: bool) -> None:
    if not path.exists():
        if not bootstrap:
            _fail("runtime selector missing")
        _atomic_write_selector(path, "V3")
        return
    if path.stat().st_size <= 0:
        _fail("runtime selector is empty")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _fail("runtime selector is malformed")
    if not isinstance(payload, dict) or str(payload.get("version") or "").strip().upper() not in SUPPORTED:
        _fail("runtime selector version must be V0/V2/V3")


def validate(env: dict[str, str], *, bootstrap_selector: bool = True) -> None:
    if env.get("NMBOT_CONTOUR_PROFILE", "").strip() != "client_production":
        _fail("NMBOT_CONTOUR_PROFILE must be exactly client_production")
    if env.get("NMBOT_API_HOST", "").strip() != "127.0.0.1" or env.get("NMBOT_API_PORT", "").strip() != "8188":
        _fail("API bind must be 127.0.0.1:8188")
    if env.get("NMBOT_N8N_BRIDGE_PORT", "").strip() != "8193":
        _fail("bridge port must be 8193")
    if env.get("NMBOT_BRIDGE_UPSTREAM", "").strip() != "http://127.0.0.1:8188":
        _fail("bridge upstream must be http://127.0.0.1:8188")
    for key, expected in REQUIRED_MANAGER_REWRITER_MODES.items():
        if _normalize_mode(env.get(key, "")) != expected:
            _fail(f"{key} must be exactly {expected}")
    for key in sorted(REQUIRED_SECRETS):
        if not _non_placeholder(env.get(key, "")):
            _fail(f"{key} is missing or placeholder")
    paths = {key: _require_under_root(env.get(key, ""), key) for key in PATH_KEYS if key in env}
    selector = paths.get("NMBOT_RUNTIME_VERSION_FILE")
    if selector is None:
        _fail("NMBOT_RUNTIME_VERSION_FILE missing")
    _validate_selector(selector, bootstrap=bootstrap_selector)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=str(ROOT / ".env.client-production"))
    parser.add_argument("--no-bootstrap-selector", action="store_true")
    args = parser.parse_args(argv)
    env_path = Path(args.env).expanduser()
    if not env_path.exists():
        _fail("env file missing")
    validate(_load_env(env_path), bootstrap_selector=not args.no_bootstrap_selector)
    print("OK: client-production preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
