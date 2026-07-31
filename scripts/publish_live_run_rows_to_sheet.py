#!/usr/bin/env python3
"""Publish prepared nmbot live-run rows to the Google Sheet analysis table.

Input rows are produced by `scripts/live_run_table_validator.py` as JSONL.
This publisher does not invent or change model/MCP facts: it only maps the
prepared row fields into the current sheet columns.

Default target:
  spreadsheet: 1ljLmkPBNijZqnDpsLzmArbIv-HoeewnfP9t1nj7cws8
  gid: 714718392 (sheet title is resolved through Sheets API)

Auth order:
  1. GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SHEETS_CREDENTIALS / GOOGLE_CREDENTIALS
  2. GOOGLE_APPLICATION_CREDENTIALS
  3. Application Default Credentials
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from nmbot_google_sheets import append_rows, build_sheets_service, resolve_sheet_title


REPO = Path(__file__).resolve().parents[1]
DEFAULT_SPREADSHEET_ID = "1ljLmkPBNijZqnDpsLzmArbIv-HoeewnfP9t1nj7cws8"
DEFAULT_GID = 714718392


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _json_loads_maybe(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _query_from_command(command: str) -> str:
    # Example: python3 scripts/chat_cli.py --timeout 180 Двушка для семьи в Москве
    match = re.search(r"scripts/chat_cli\.py\s+(?:--timeout\s+\d+\s+)?(.+)$", command)
    return match.group(1).strip() if match else command


def _sheet_row(row: dict[str, Any], timestamp: str) -> list[str]:
    prompt_master = _json_loads_maybe(row.get("prompt_master_verdict", ""))
    score = ""
    if isinstance(prompt_master, dict):
        score = prompt_master.get("score", "")
    params = _json_loads_maybe(row.get("params", ""))
    mcp_request = _json_loads_maybe(row.get("mcp_request", ""))
    mcp_response = _json_loads_maybe(row.get("mcp_response", ""))
    response = _json_loads_maybe(row.get("response", ""))
    if isinstance(response, (dict, list)):
        response_cell = json.dumps(response, ensure_ascii=False, indent=2)
    else:
        response_cell = str(response or "")

    mcp_cell = {
        "mcp_contract": mcp_request,
        "mcp_response": mcp_response,
    }
    answer_cell = {
        "response": response_cell,
        "mcp_contract": mcp_request,
        "mcp_response_summary": {
            "facts_count": row.get("facts_count"),
            "near_count": row.get("near_count"),
            "facts_names": row.get("facts_names", ""),
            "visible_names": row.get("visible_names", ""),
        },
        "params": params,
        "visible_options": row.get("visible_names", ""),
        "warnings": row.get("warnings", ""),
        "prompt_master_verdict": prompt_master,
    }
    return [
        timestamp,
        str(row.get("version", "")),
        _query_from_command(str(row.get("command", ""))),
        str(row.get("case", "")),
        json.dumps(mcp_cell, ensure_ascii=False, indent=2),
        json.dumps(answer_cell, ensure_ascii=False, indent=2),
        json.dumps(prompt_master, ensure_ascii=False, indent=2)
        if isinstance(prompt_master, dict)
        else str(prompt_master or ""),
        str(score),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rows_jsonl", type=Path, help="Prepared rows JSONL from live_run_table_validator.py")
    parser.add_argument("--spreadsheet-id", default=DEFAULT_SPREADSHEET_ID)
    parser.add_argument("--gid", type=int, default=DEFAULT_GID)
    parser.add_argument("--write", action="store_true", help="Actually append rows. Default is dry-run.")
    parser.add_argument("--timestamp", default=datetime.now().strftime("%Y-%m-%d %H:%M"))
    args = parser.parse_args(argv)

    rows = _load_jsonl(args.rows_jsonl)
    values = [_sheet_row(row, args.timestamp) for row in rows]

    title = f"gid:{args.gid}"
    target_range = f"{title}!A:H"
    service = None
    if args.write:
        service = build_sheets_service()
        title = resolve_sheet_title(service, spreadsheet_id=args.spreadsheet_id, gid=args.gid)
        target_range = f"'{title}'!A:H"

    print(f"PUBLISH: rows={len(values)} target={title} gid={args.gid} mode={'write' if args.write else 'dry-run'}")
    for value in values:
        print(f"ROW: version={value[1]} case={value[3]} query={value[2]}")

    if not args.write:
        print("DRY_RUN: pass --write to append rows")
        return 0

    result = append_rows(service, spreadsheet_id=args.spreadsheet_id, range_name=target_range, values=values)
    updates = result.get("updates", {})
    print(f"WRITTEN: updatedRange={updates.get('updatedRange')} updatedRows={updates.get('updatedRows')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
