#!/usr/bin/env python3
"""Switch the version-isolated final manager rewriter modes."""

from __future__ import annotations

import argparse
from pathlib import Path


KEYS = {
    "V2": "NMBOT_V2_MANAGER_REWRITER_MODE",
    "V3": "NMBOT_V3_MANAGER_REWRITER_MODE",
}
VALID = {"off", "shadow", "publish"}


def set_key(path: Path, key: str, value: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    rendered = f"{key}={value}"
    for index, line in enumerate(lines):
        if line.strip().split("=", 1)[0].strip() == key:
            lines[index] = rendered
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return "updated"
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(rendered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "added"


def current(path: Path, key: str) -> str:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(key + "="):
                value = line.split("=", 1)[1].strip().strip('"\'')
                return value if value in VALID else "off"
    return "off"


def _runtime_keys(runtime: str) -> list[tuple[str, str]]:
    selected = runtime.upper()
    if selected == "ALL":
        return list(KEYS.items())
    return [(selected, KEYS[selected])]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["off", "shadow", "publish", "status"])
    parser.add_argument("--env", default=".env")
    parser.add_argument("--runtime", choices=["V2", "V3", "all"], help="runtime to mutate; required for off/shadow/publish")
    args = parser.parse_args()
    path = Path(args.env).expanduser()
    if args.command == "status":
        for version, key in KEYS.items():
            print(f"{version} {key}={current(path, key)}")
        return
    if not args.runtime:
        parser.error("--runtime is required for off/shadow/publish")
    for version, key in _runtime_keys(args.runtime):
        print(f"OK: {version} {set_key(path, key, args.command)} {key}={args.command}")


if __name__ == "__main__":
    main()
