#!/usr/bin/env python3
"""Local-only entrypoint for the pinned V1 runtime worker."""
from __future__ import annotations

import os
from pathlib import Path

from aiohttp import web

from nmbot_runtime_service_host.http import validate_release_identity
from nmbot_v1.service import create_app


def main() -> None:
    token = os.getenv("NMBOT_V1_INTERNAL_TOKEN", "")
    release_identity = validate_release_identity(os.getenv("NMBOT_V1_RELEASE_ID", "").strip())
    app = create_app(state_path=Path(os.getenv("NMBOT_V1_STATE_PATH", "data/nmbot-v1-state.json")),
                      journal_path=Path(os.getenv("NMBOT_V1_JOURNAL_PATH", "logs/nmbot-v1-runtime.jsonl")),
                      token=token, release_identity=release_identity)
    web.run_app(app, host="127.0.0.1", port=int(os.getenv("NMBOT_V1_PORT", "18081")))


if __name__ == "__main__":
    main()
