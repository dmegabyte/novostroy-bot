#!/usr/bin/env python3
"""Fail-closed, contour-scoped control for callback CRM delivery."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "nmbot.callback_crm_control.v1"
CONTOURS = ("PROD", "TEST")


def read_control(path: Path | str | None, *, contour: str) -> bool:
    if not path or contour not in CONTOURS:
        return False
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return False
    states = data.get("contours") if isinstance(data, dict) and data.get("schema") == SCHEMA else None
    return bool(isinstance(states, dict) and states.get(contour) is True)


def _read_document(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": SCHEMA, "contours": {}}
    except (PermissionError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("invalid_control_file") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("contours"), dict):
        raise RuntimeError("invalid_control_file")
    return data


def write_control(path: Path, *, contour: str, enabled: bool) -> None:
    document = _read_document(path)
    contours = dict(document["contours"])
    contours[contour] = enabled
    document = {"schema": SCHEMA, "contours": contours}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.chmod(tmp_name, 0o600)
            json.dump(document, fh, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Control callback CRM delivery")
    parser.add_argument("--contour", choices=CONTOURS, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    set_parser = sub.add_parser("set")
    set_parser.add_argument("state", choices=("on", "off"))
    set_parser.add_argument("--dry-run", action="store_true")
    set_parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    path_value = str(os.getenv("NMBOT_CALLBACK_CRM_CONTROL_FILE") or "").strip()
    if not path_value:
        print("CRM control: unavailable; effective state off")
        return 2 if args.command == "set" and args.confirm else 0
    path = Path(path_value).expanduser()
    if args.command == "status":
        print(f"CRM callback {args.contour}: {'on' if read_control(path, contour=args.contour) else 'off'}")
        return 0
    desired = args.state == "on"
    if args.dry_run:
        print(f"Dry run: CRM callback {args.contour} would be {'on' if desired else 'off'}")
        return 0
    if not args.confirm:
        print("Refusing mutation without --confirm")
        return 2
    try:
        write_control(path, contour=args.contour, enabled=desired)
    except RuntimeError as exc:
        print(f"CRM control error: {exc}")
        return 2
    print(f"CRM callback {args.contour}: {'on' if desired else 'off'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
