#!/usr/bin/env python3
"""Local dry-run MemPalace safety gate for project memory.

The current phase keeps MemPalace disabled for selectors and project facts. This
script models the fail-closed mechanics only; it does not import or call any
MemPalace client, network API, runtime, gate or subprocess.
"""
from __future__ import annotations

import argparse
import json
from typing import Any


HEALTH_SCHEMA = "project_memory_mempalace_health.v1"


def check_mempalace_health(project_id: str = "nmbot") -> dict[str, Any]:
    """Return a fail-closed local health gate payload with no side effects."""

    checks = [
        {"name": "integrity", "status": "not_checked_local_dry_run", "pass": False},
        {"name": "vector_availability", "status": "not_checked_local_dry_run", "pass": False},
        {"name": "project_isolation", "status": "not_checked_local_dry_run", "pass": False},
    ]
    return {
        "schema": HEALTH_SCHEMA,
        "ok": False,
        "dry_run": True,
        "project_id": project_id,
        "denied_reason": "mempalace_disabled_until_integrity_vector_isolation_pass",
        "selector_enabled": False,
        "project_fact_source_enabled": False,
        "allowed_after_repair": ["agent_diary", "meta_memory"],
        "checks": checks,
        "mempalace_call_performed": False,
        "write_performed": False,
    }


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run fail-closed MemPalace project-memory health gate.")
    parser.add_argument("--project-id", default="nmbot")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = check_mempalace_health(args.project_id)
    emit(payload, args.json)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
