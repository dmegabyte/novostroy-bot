#!/usr/bin/env python3
"""Switch the V2/V3 response writer between Gemini and Bluesminds."""

from __future__ import annotations

import argparse
from pathlib import Path


KEY = "NMBOT_RESPONSE_PROVIDER"
VALID = {"gemini", "bluesminds"}


def set_key(path: Path, value: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    rendered = f"{KEY}={value}"
    for index, line in enumerate(lines):
        if line.strip().split("=", 1)[0].strip() == KEY:
            lines[index] = rendered
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return "updated"
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(rendered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "added"


def current(path: Path) -> str:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(KEY + "="):
                value = line.split("=", 1)[1].strip().strip('"\'')
                return value if value in VALID else "gemini"
    return "gemini"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["gemini", "bluesminds", "status"])
    parser.add_argument("--env", default=".env")
    args = parser.parse_args()
    path = Path(args.env).expanduser()
    if args.command == "status":
        print(f"provider={current(path)}")
        return
    print(f"OK: {set_key(path, args.command)} {KEY}={args.command}")


if __name__ == "__main__":
    main()
