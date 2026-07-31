#!/usr/bin/env python3
"""One-shot cycle for live nmbot runs.

Flow:
  1. validate a live-run log with scripts/live_run_table_validator.py
  2. optionally publish prepared rows to Google Sheets

This is the convenience wrapper for the analysis-table workflow.
It never rewrites answers; it only moves prepared rows forward.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, cwd=REPO)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="Path to logs/live_model_run_*.txt")
    parser.add_argument("--version", default="v2", help="Version label written to the table")
    parser.add_argument("--write", action="store_true", help="Publish rows to Google Sheets")
    parser.add_argument("--spreadsheet-id", default="1ljLmkPBNijZqnDpsLzmArbIv-HoeewnfP9t1nj7cws8")
    parser.add_argument("--gid", type=int, default=714718392)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="nmbot-live-cycle-") as tmp:
        jsonl_out = Path(tmp) / f"{args.log.stem}.rows.{args.version}.jsonl"
        _run([
            sys.executable,
            str(REPO / "scripts" / "live_run_table_validator.py"),
            str(args.log),
            "--version",
            args.version,
            "--jsonl-out",
            str(jsonl_out),
        ])

        if not args.write:
            print(f"PREVIEW_ONLY: rows saved to {jsonl_out}")
            return 0

        _run([
            sys.executable,
            str(REPO / "scripts" / "publish_live_run_rows_to_sheet.py"),
            str(jsonl_out),
            "--spreadsheet-id",
            args.spreadsheet_id,
            "--gid",
            str(args.gid),
            "--write",
        ])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
