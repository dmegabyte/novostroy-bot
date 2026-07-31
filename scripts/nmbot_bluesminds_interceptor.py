#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

try:
    from scripts.bluesminds_answer_interceptor import ENV_ENABLED, config_status
    from scripts.nmbot_env_secrets import set_key
except ImportError:  # pragma: no cover - direct scripts/ execution fallback
    from bluesminds_answer_interceptor import ENV_ENABLED, config_status  # type: ignore
    from nmbot_env_secrets import set_key  # type: ignore


def _load_safe_env(path: Path) -> dict[str, str]:
    env = dict(os.environ)
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        env[key] = value
    return env


def main() -> None:
    parser = argparse.ArgumentParser(description="Toggle safe Bluesminds response-writer interceptor")
    parser.add_argument("command", choices=("on", "off", "status"))
    parser.add_argument("--env", default=".env", help="dotenv file path")
    args = parser.parse_args()

    env_path = Path(args.env).expanduser()
    if args.command == "on":
        status = set_key(env_path, ENV_ENABLED, "enabled")
        print(f"OK: interceptor enabled ({status} {ENV_ENABLED} in {args.env})")
        return
    if args.command == "off":
        status = set_key(env_path, ENV_ENABLED, "off")
        print(f"OK: interceptor disabled ({status} {ENV_ENABLED} in {args.env})")
        return

    safe_status = config_status(_load_safe_env(env_path))
    print(f"enabled={str(safe_status['enabled']).lower()}")
    print(f"model={safe_status['model']}")
    print(f"timeout={safe_status['timeout']}")


if __name__ == "__main__":
    main()
