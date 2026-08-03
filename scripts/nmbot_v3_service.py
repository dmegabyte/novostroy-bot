#!/usr/bin/env python3
"""Executable entrypoint for the outer, local-only V3 host boundary."""
from scripts.nmbot_v3_host import main as _main


def main() -> None:
    return _main()


if __name__ == "__main__":
    main()
