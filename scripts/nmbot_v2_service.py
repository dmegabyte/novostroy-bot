#!/usr/bin/env python3
"""Compatibility entrypoint delegating to the isolated V2 host."""
from __future__ import annotations

def main() -> None:
    """Delegate all config, composition, validation, and bind ownership."""
    from scripts.nmbot_v2_host import main as host_main
    host_main()


if __name__ == "__main__":
    main()
