"""Compatibility entrypoint for the V6-only API shell."""
from __future__ import annotations

try:
    from scripts.nmbot_v6_api import *  # noqa: F401,F403
    from scripts.nmbot_v6_api import main
except ModuleNotFoundError:  # direct execution: python scripts/nmbot_api_server.py
    from nmbot_v6_api import *  # type: ignore # noqa: F401,F403
    from nmbot_v6_api import main  # type: ignore


if __name__ == "__main__":
    main()
