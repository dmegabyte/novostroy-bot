#!/usr/bin/env python3
"""Static read-only project audit for nmbot simplification mapping.

The report is source-only evidence. It never proves production behavior and
never labels files as unused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "nmbot.project_audit.v1"
SCRIPT_VERSION = "2026-07-22.static-read-only"

SCAN_PATTERNS = ("scripts/**/*.py", "prompts/**/*")
REFERENCE_PATTERNS = ("docs/**/*", "tests/**/*")
CHECK_HARNESS_REFERENCE_PATHS = ("scripts/nmbot_test_agent.py",)
EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "htmlcov",
}
EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_FILENAMES = {
    ".env",
    ".env.local",
    ".env.prod",
    ".env.production",
    ".env.staging",
    ".env.dev",
    ".env.test",
}
EXCLUDED_OUTPUT_MARKERS = ("nmbot_project_audit", "project_audit_report", "audit-output", "audit_output")


class AuditError(ValueError):
    pass


def _safe_root(path: Path) -> Path:
    root = path.resolve()
    if not root.is_dir():
        raise AuditError(f"root is not a directory: {path}")
    return root


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_excluded(root: Path, path: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    parts = rel.parts
    if any(part in EXCLUDED_DIR_NAMES for part in parts[:-1]):
        return True
    name = path.name
    if name in EXCLUDED_FILENAMES or name.startswith(".env"):
        return True
    if path.suffix in EXCLUDED_FILE_SUFFIXES:
        return True
    rel_text = rel.as_posix().casefold()
    if any(marker in rel_text for marker in EXCLUDED_OUTPUT_MARKERS):
        return True
    return False


def _regular_files(root: Path, patterns: Iterable[str]) -> list[Path]:
    paths: set[Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file() and not _is_excluded(root, path):
                paths.add(path)
    return sorted(paths, key=lambda item: _relative(root, item))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _category(relative: str) -> str:
    if relative.startswith("scripts/") and relative.endswith(".py"):
        return "script_py"
    if relative.startswith("prompts/"):
        return "prompt"
    return "other"


def collect_reference_index(root: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for path in _regular_files(root, REFERENCE_PATTERNS):
        rel = _relative(root, path)
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".pdf"}:
            continue
        records.append({"path": rel, "text": _read_text(path)})
    for relative in CHECK_HARNESS_REFERENCE_PATHS:
        path = root / relative
        if path.is_file() and not _is_excluded(root, path):
            records.append({"path": relative, "text": _read_text(path)})
    return records


def _references_for(relative: str, basename: str, reference_index: list[dict[str, str]]) -> dict[str, list[str]]:
    needles = {relative, basename}
    docs: list[str] = []
    tests: list[str] = []
    checks: list[str] = []
    for source in reference_index:
        text = source["text"]
        if not any(needle and needle in text for needle in needles):
            continue
        target = (
            docs
            if source["path"].startswith("docs/")
            else tests
            if source["path"].startswith("tests/")
            else checks
            if source["path"] in CHECK_HARNESS_REFERENCE_PATHS
            else None
        )
        if target is not None:
            target.append(source["path"])
    return {
        "doc_references": sorted(set(docs)),
        "test_references": sorted(set(tests)),
        "check_references": sorted(set(checks)),
    }


def build_report(root: Path) -> dict[str, Any]:
    root = _safe_root(root)
    references = collect_reference_index(root)
    inventory: list[dict[str, Any]] = []
    by_sha: dict[str, list[str]] = {}

    for path in _regular_files(root, SCAN_PATTERNS):
        rel = _relative(root, path)
        digest = sha256_file(path)
        refs = _references_for(rel, path.name, references)
        record: dict[str, Any] = {
            "path": rel,
            "kind": _category(rel),
            "size_bytes": path.stat().st_size,
            "sha256": digest,
            "references": refs,
        }
        inventory.append(record)
        by_sha.setdefault(digest, []).append(rel)

    duplicate_groups = [
        {"sha256": digest, "paths": sorted(paths)}
        for digest, paths in sorted(by_sha.items())
        if len(paths) > 1
    ]
    candidates: list[dict[str, Any]] = []
    for record in inventory:
        reasons: list[str] = []
        if not record["references"]["test_references"] and not record["references"]["check_references"]:
            reasons.append("no_test_reference")
        if not record["references"]["doc_references"]:
            reasons.append("no_doc_reference")
        if reasons:
            candidates.append({
                "path": record["path"],
                "label": "unreferenced_candidate",
                "review_status": "needs_review",
                "reasons": reasons,
                "note": "Static textual reference candidate only; not a deletion claim.",
            })

    counts_by_kind: dict[str, int] = {}
    for record in inventory:
        counts_by_kind[record["kind"]] = counts_by_kind.get(record["kind"], 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "script_version": SCRIPT_VERSION,
        "repo_root": str(root),
        "policy": {
            "read_only": True,
            "network": "forbidden",
            "subprocess": "forbidden",
            "runtime_imports": "forbidden",
            "production_claims": "forbidden",
            "deletion_claims": "forbidden",
            "env_values": "never_read",
        },
        "scan": {
            "include_patterns": list(SCAN_PATTERNS),
            "reference_patterns": list(REFERENCE_PATTERNS),
            "check_harness_reference_paths": list(CHECK_HARNESS_REFERENCE_PATHS),
            "excluded_dirs": sorted(EXCLUDED_DIR_NAMES),
        },
        "inventory": {
            "counts": {"total": len(inventory), "by_kind": dict(sorted(counts_by_kind.items()))},
            "records": inventory,
        },
        "duplicate_groups": duplicate_groups,
        "coverage_candidates": candidates,
        "boundary_note": "Source-only static mapping. Candidates require human/live evidence review before any runtime simplification.",
    }


def render_human(report: dict[str, Any]) -> str:
    lines = [
        f"schema: {report['schema_version']}",
        f"repo: {report['repo_root']}",
        f"inventory: {report['inventory']['counts']['total']} files",
        f"duplicate groups: {len(report['duplicate_groups'])}",
        f"coverage candidates: {len(report['coverage_candidates'])}",
        "boundary: source-only candidates, never deletion/prod proof",
    ]
    for item in report["coverage_candidates"][:20]:
        lines.append(f"  {item['path']}: {item['label']} / {item['review_status']} ({', '.join(item['reasons'])})")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only static nmbot project audit report.")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root; intended for isolated test fixtures")
    parser.add_argument("--human", action="store_true", help="print compact human summary instead of JSON")
    parser.add_argument("--json", action="store_true", help="print JSON output (default)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = build_report(args.root)
    except AuditError as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": "failed", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    if args.human:
        print(render_human(report), end="")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
