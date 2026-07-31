#!/usr/bin/env python3
"""Local post-repair MemPalace verification harness for project memory.

This script is intentionally separate from project_memory_mempalace_health.py.
The health gate remains dry-run/fail-closed. This verifier is a manual,
local-only operator check for after an external repair has completed: it reads
SQLite databases in read-only mode and invokes the local MemPalace CLI through
an injectable runner. It never enables selectors or project facts.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence


VERIFY_SCHEMA = "project_memory_mempalace_verify.v1"
DEFAULT_PALACE_PATH = Path("/home/ser/opencode-memory")
DEFAULT_PROJECT_ID = "nmbot"
DEFAULT_QUERY = "NMBot context workflow"
PRIMARY_DB = "chroma.sqlite3"
OPTIONAL_DBS = ("knowledge_graph.sqlite3",)


class RunnerResult:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


Runner = Callable[[Sequence[str], Path | None, int], RunnerResult]


def subprocess_runner(args: Sequence[str], cwd: Path | None, timeout: int) -> RunnerResult:
    """Run a local command without shell expansion."""

    completed = subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    return RunnerResult(completed.returncode, completed.stdout, completed.stderr)


def _check_sqlite_database(db_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": db_path.name,
        "path": str(db_path),
        "present": db_path.exists(),
        "required": db_path.name == PRIMARY_DB,
        "checks": [],
        "pass": False,
    }
    if not db_path.exists():
        result["status"] = "missing"
        return result
    if not db_path.is_file():
        result["status"] = "not_a_file"
        return result

    try:
        connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            for pragma in ("quick_check", "integrity_check"):
                rows = [row[0] for row in connection.execute(f"PRAGMA {pragma}").fetchall()]
                passed = bool(rows) and all(str(row).lower() == "ok" for row in rows)
                result["checks"].append({"name": pragma, "rows": rows, "pass": passed})
        finally:
            connection.close()
    except Exception as exc:  # pragma: no cover - exact sqlite exceptions vary
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    result["pass"] = all(item["pass"] for item in result["checks"])
    result["status"] = "ok" if result["pass"] else "failed"
    return result


def _run_cli_step(
    *,
    name: str,
    args: Sequence[str],
    runner: Runner,
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    try:
        completed = runner(args, cwd, timeout)
        return {
            "name": name,
            "args": list(args),
            "returncode": completed.returncode,
            "pass": completed.returncode == 0,
            "stdout_preview": completed.stdout[:1000],
            "stderr_preview": completed.stderr[:1000],
        }
    except Exception as exc:  # pragma: no cover - defensive for real CLI failures
        return {
            "name": name,
            "args": list(args),
            "returncode": None,
            "pass": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def verify_mempalace_post_repair(
    palace_path: str | Path = DEFAULT_PALACE_PATH,
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    query: str = DEFAULT_QUERY,
    cli: str = "mempalace",
    runner: Runner = subprocess_runner,
    timeout: int = 30,
) -> dict[str, Any]:
    """Return a structured local post-repair verification payload.

    Success means only that manual local checks passed. The payload deliberately
    keeps selector/project-fact activation disabled.
    """

    raw_path = Path(palace_path).expanduser()
    payload: dict[str, Any] = {
        "schema": VERIFY_SCHEMA,
        "ok": False,
        "mode": "local_post_repair_verification_only",
        "project_id": project_id,
        "palace_path": str(raw_path),
        "selector_enabled": False,
        "project_fact_source_enabled": False,
        "allowed_after_success": ["agent_diary", "meta_memory"],
        "behavior_activation_permitted": False,
        "write_performed": False,
        "external_network_permitted": False,
        "checks": {"path": {}, "sqlite": [], "cli": []},
    }

    try:
        resolved_path = raw_path.resolve(strict=True)
    except FileNotFoundError:
        payload["checks"]["path"] = {"exists": False, "is_dir": False, "pass": False}
        return payload

    path_check = {"exists": True, "is_dir": resolved_path.is_dir(), "path": str(resolved_path)}
    path_check["pass"] = bool(path_check["is_dir"])
    payload["palace_path"] = str(resolved_path)
    payload["checks"]["path"] = path_check
    if not path_check["pass"]:
        return payload

    database_names = (PRIMARY_DB, *OPTIONAL_DBS)
    sqlite_results = [_check_sqlite_database(resolved_path / name) for name in database_names]
    payload["checks"]["sqlite"] = sqlite_results

    cli_steps = [
        ("status", [cli, "--palace", str(resolved_path), "status"]),
        ("repair-status", [cli, "--palace", str(resolved_path), "repair-status"]),
        ("semantic_search", [cli, "--palace", str(resolved_path), "search", query, "--wing", project_id]),
        ("wake_up", [cli, "--palace", str(resolved_path), "wake-up", "--wing", project_id]),
    ]
    payload["checks"]["cli"] = [
        _run_cli_step(name=name, args=args, runner=runner, cwd=resolved_path, timeout=timeout) for name, args in cli_steps
    ]

    required_sqlite_ok = all(item["pass"] for item in sqlite_results if item["required"] or item["present"])
    primary_present = any(item["name"] == PRIMARY_DB and item["present"] and item["pass"] for item in sqlite_results)
    cli_ok = all(item["pass"] for item in payload["checks"]["cli"])
    payload["ok"] = bool(path_check["pass"] and primary_present and required_sqlite_ok and cli_ok)
    if not payload["ok"]:
        payload["denied_reason"] = "post_repair_verification_failed"
    return payload


def emit(payload: dict[str, Any], pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local-only post-repair MemPalace verifier; does not enable behavior.")
    parser.add_argument("--palace", default=str(DEFAULT_PALACE_PATH), help="MemPalace storage path to verify")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--cli", default="mempalace", help="Local MemPalace CLI executable")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--json", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args(argv)

    payload = verify_mempalace_post_repair(
        args.palace,
        project_id=args.project_id,
        query=args.query,
        cli=args.cli,
        timeout=args.timeout,
    )
    emit(payload, args.json)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
