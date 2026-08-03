#!/usr/bin/env python3
"""Local-only release evidence preflight for nmbot.

This script intentionally does not import or execute scripts/nmbot_release.py.
It collects static local evidence and can optionally invoke only the local
scripts/nmbot_check.py dispatcher with direct argv and cwd.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path.cwd().absolute()
if not (ROOT / "scripts" / "nmbot_release_preflight.py").is_file():
    ROOT = Path(__file__).absolute().parents[1]
SCHEMA_VERSION = "nmbot.release_preflight.v1"
SCRIPT_VERSION = "2026-07-22.step8-local-only"
DEFAULT_MANIFEST = ROOT / "tests" / "nmbot_check_manifest.yaml"
RELEASE_OWNER_SCOPES = (
    "docs",
    "contracts",
    "v0",
    "v1",
    "v2",
    "v3",
    "runtime",
    "audit",
    "quality",
    "artifact",
    "isolation",
)
SCOPE_ALIASES = {"release": RELEASE_OWNER_SCOPES}
SUPPORTED_SCOPES = {*RELEASE_OWNER_SCOPES, *SCOPE_ALIASES}
DEFAULT_SCOPES = ("docs", "contracts")
DEFAULT_TARGET_FILES = (
    "docs/NMBOT_PROJECT_SIMPLIFICATION_PLAN.md",
    "docs/NMBOT_RUNTIME_REGISTRY.md",
    "docs/NMBOT_COMMAND_MIGRATION.md",
    "docs/NMBOT_RUNBOOK.md",
    "docs/NMBOT_EXTERNAL_CONTRACTS.md",
    "tests/nmbot_check_manifest.yaml",
    "scripts/nmbot_check.py",
    "scripts/nmbot_release_preflight.py",
)


class PreflightError(ValueError):
    pass


def _repo_path(relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise PreflightError(f"unsafe target file: {relative}")
    return ROOT / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_hashes(files: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative in files:
        path = _repo_path(relative)
        record: dict[str, Any] = {"path": relative}
        if path.is_file():
            record.update({"status": "present", "sha256": sha256_file(path)})
        else:
            record.update({"status": "missing", "sha256": None})
        records.append(record)
    return records


def load_manifest_plan(manifest_path: Path, scopes: list[str]) -> dict[str, Any]:
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PreflightError(f"cannot read manifest: {manifest_path}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PreflightError(f"manifest must be JSON-compatible YAML: {exc}") from exc
    manifest_scopes = data.get("scopes")
    if not isinstance(manifest_scopes, dict):
        raise PreflightError("manifest.scopes must be an object")
    unknown = [scope for scope in scopes if scope not in SUPPORTED_SCOPES]
    if unknown:
        raise PreflightError(f"unknown scope: {', '.join(unknown)}")
    planned_scopes = [owner_scope for scope in scopes for owner_scope in SCOPE_ALIASES.get(scope, (scope,))]
    missing = [scope for scope in planned_scopes if scope not in manifest_scopes]
    if missing:
        raise PreflightError(f"scope missing from manifest: {', '.join(missing)}")

    planned: dict[str, Any] = {}
    for scope in planned_scopes:
        spec = manifest_scopes[scope]
        commands = spec.get("commands") if isinstance(spec, dict) else None
        if not isinstance(commands, list):
            raise PreflightError(f"scope has no command list: {scope}")
        planned[scope] = {
            "description": spec.get("description") if isinstance(spec, dict) else None,
            "commands": [
                {
                    "name": command.get("name"),
                    "argv": command.get("argv"),
                    "evidence": command.get("evidence", []),
                    "network": command.get("network", "unknown"),
                    "side_effects": command.get("side_effects", "unknown"),
                }
                for command in commands
                if isinstance(command, dict)
            ],
        }
    return {
        "path": str(manifest_path.relative_to(ROOT) if manifest_path.is_relative_to(ROOT) else manifest_path),
        "status": "passed",
        "selected_scopes": scopes,
        "planned_owner_scopes": planned_scopes,
        "plan": planned,
    }


def run_local_checks(scopes: list[str]) -> dict[str, Any]:
    argv = [sys.executable, "scripts/nmbot_check.py", *scopes, "--json"]
    proc = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, check=False)
    findings: list[str] = []
    # The architecture preflight intentionally reports its static findings in
    # JSON but is non-strict in the fast gate. Preserve that distinction rather
    # than turning an informational return code into a clean evidence claim.
    if '"overall": "FAIL"' in proc.stdout:
        findings.append("architecture_preflight overall=FAIL (non-strict local report)")
    status = "failed" if proc.returncode != 0 else "passed_with_findings" if findings else "passed"
    return {
        "status": status,
        "argv": argv,
        "cwd": str(ROOT),
        "returncode": proc.returncode,
        "stdout": proc.stdout[-12000:],
        "stderr": proc.stderr[-12000:],
        "findings": findings,
    }


def build_evidence(*, scopes: list[str], files: list[str], manifest_path: Path, run_checks: bool) -> dict[str, Any]:
    manifest = load_manifest_plan(manifest_path, scopes)
    hashes = collect_hashes(files)
    checks = run_local_checks(scopes) if run_checks else {"status": "not_run", "reason": "use --run-checks to invoke scripts/nmbot_check.py"}
    local_status = "failed" if checks.get("status") == "failed" or any(item["status"] == "missing" for item in hashes) else checks.get("status", "passed")
    return {
        "schema_version": SCHEMA_VERSION,
        "script_version": SCRIPT_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "repo_root": str(ROOT),
        "policy": {
            "local_only": True,
            "network": "forbidden",
            "external_writes": "forbidden",
            "imports_scripts_nmbot_release_py": False,
            "default_runs_checks": False,
        },
        "target_files": hashes,
        "manifest": manifest,
        "checks": checks,
        "evidence": {
            "local": {"status": local_status},
            "fixture": {"status": checks.get("status", "not_run") if run_checks else "not_run"},
            "vps": {"status": "not_checked"},
            "direct_api": {"status": "not_checked"},
            "jivo": {"status": "incomplete", "reason": "local preflight cannot supply Jivo smoke evidence"},
        },
        "overall": {
            "status": "incomplete",
            "green": False,
            "reason": "Step 8 requires separated local/fixture/VPS/direct API/Jivo evidence; this local-only tool never supplies external evidence.",
        },
    }


def render_human(report: dict[str, Any]) -> str:
    lines = [
        f"schema: {report['schema_version']}",
        f"repo: {report['repo_root']}",
        f"overall: {report['overall']['status']} (green={report['overall']['green']})",
        "evidence:",
    ]
    for key, value in report["evidence"].items():
        lines.append(f"  {key}: {value['status']}")
    lines.append("target files:")
    for item in report["target_files"]:
        suffix = item["sha256"] if item["sha256"] else "missing"
        lines.append(f"  {item['path']}: {item['status']} {suffix}")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect local-only release preflight evidence; never proves production green.")
    parser.add_argument("--scope", action="append", dest="scopes", choices=sorted(SUPPORTED_SCOPES), help="nmbot_check scope to plan/run; may be repeated. Default: docs, contracts")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--target-file", action="append", dest="files", help="repo-relative file to hash; may be repeated")
    parser.add_argument("--run-checks", action="store_true", help="run only scripts/nmbot_check.py with selected local scopes")
    parser.add_argument("--human", action="store_true", help="print compact human output instead of JSON")
    parser.add_argument("--json", action="store_true", help="print JSON output (default)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scopes = args.scopes or list(DEFAULT_SCOPES)
    files = args.files or list(DEFAULT_TARGET_FILES)
    try:
        report = build_evidence(scopes=scopes, files=files, manifest_path=args.manifest, run_checks=args.run_checks)
    except PreflightError as exc:
        error = {"schema_version": SCHEMA_VERSION, "status": "failed", "error": str(exc)}
        print(json.dumps(error, ensure_ascii=False, sort_keys=True))
        return 2
    if args.human:
        print(render_human(report), end="")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
