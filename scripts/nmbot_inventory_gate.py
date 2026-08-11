#!/usr/bin/env python3
"""Manage the local broad inventory gate dotenv flag without revealing values."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path


KEY = "NMBOT_BROAD_INVENTORY_GATE_ENABLED"
ON_VALUES = frozenset({"1", "true", "yes", "on"})
OFF_VALUES = frozenset({"0", "false", "no", "off"})


def configured_status(path: Path) -> str:
    """Return only the effective status class, never the dotenv value."""

    value = _read_key(path)
    if value is None:
        return "default"
    normalized = value.strip().lower()
    if normalized in OFF_VALUES:
        return "disabled"
    if normalized in ON_VALUES:
        return "enabled"
    return "default"


def set_enabled(path: Path, enabled: bool, *, dry_run: bool = False) -> str:
    """Safely change only ``KEY`` and return a non-sensitive operation label."""

    rendered = f"{KEY}={'1' if enabled else '0'}"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped and stripped.split("=", 1)[0].strip() == KEY:
            if not replaced:
                output.append(rendered)
            replaced = True
        else:
            output.append(line)
    if not replaced:
        if output and output[-1].strip():
            output.append("")
        output.append(rendered)
    if dry_run:
        return "dry-run"

    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".bak")
    if path.exists():
        shutil.copy2(path, backup)
    else:
        backup.write_text("", encoding="utf-8")
    os.chmod(backup, 0o600)
    _atomic_write(path, "\n".join(output) + "\n")
    return "updated" if replaced else "added"


def _read_key(path: Path) -> str | None:
    if not path.exists():
        return None
    value: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, raw = stripped.split("=", 1)
            if key.strip() == KEY:
                value = _unquote(raw.strip())
    return value


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the local broad inventory gate")
    parser.add_argument("--env-file", default=".env", help="dotenv file path")
    parser.add_argument("--dry-run", action="store_true", help="report enable/disable without writing")
    parser.add_argument("command", choices=("status", "enable", "disable"))
    args = parser.parse_args()
    path = Path(args.env_file).expanduser()
    if args.command == "status":
        print(configured_status(path))
        return
    operation = set_enabled(path, args.command == "enable", dry_run=args.dry_run)
    print(f"{operation} {'enabled' if args.command == 'enable' else 'disabled'}")


if __name__ == "__main__":
    main()
