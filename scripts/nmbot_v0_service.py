#!/usr/bin/env python3
"""Local-only entrypoint for the isolated V0 runtime worker."""
from __future__ import annotations

import os
from pathlib import Path

from aiohttp import web

from nmbot_runtime_service_host.http import validate_release_identity
from nmbot_v0.service import create_app


def main() -> None:
    token = os.getenv("NMBOT_V0_INTERNAL_TOKEN", "")
    release_identity = validate_release_identity(os.getenv("NMBOT_V0_RELEASE_ID", "").strip())
    app = create_app(state_path=Path(os.getenv("NMBOT_V0_STATE_PATH", "data/nmbot-v0-state.json")),
                     journal_path=Path(os.getenv("NMBOT_V0_JOURNAL_PATH", "logs/nmbot-v0-runtime.jsonl")),
                     token=token, release_identity=release_identity,
                     total_timeout_seconds=float(os.getenv("NMBOT_V0_TOTAL_TIMEOUT_SECONDS", "20")))
    web.run_app(app, host="127.0.0.1", port=int(os.getenv("NMBOT_V0_PORT", "18080")))


if __name__ == "__main__":
    main()
