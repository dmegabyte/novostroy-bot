"""Canonical V6 API entrypoint for an immutable release artifact."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from aiohttp import web

from nmbot_core.app import create_app_from_environment


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("NMBOT_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("NMBOT_API_PORT", "8088")))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    web.run_app(create_app_from_environment(root), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
