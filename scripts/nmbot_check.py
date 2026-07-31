#!/usr/bin/env python3
"""Thin local read-only dispatcher for nmbot fast checks."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests" / "nmbot_check_manifest.yaml"
SUPPORTED_SCOPES = {"docs", "contracts", "v0", "v1", "v2", "runtime", "audit", "quality"}
DOCS_ROUTE_HEADINGS = (
    "## 1. Start and understand",
    "## 2. Build and verify",
    "## 3. Operate and release",
    "## 4. Decisions and history",
)
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FORBIDDEN_TOKENS = {
    "bash",
    "sh",
    "ssh",
    "scp",
    "curl",
    "wget",
    "nmbot_release.py",
    "deploy",
    "systemctl",
    "promptfoo",
    "eval",
}
ALLOWED_PYTHON_MODULES = {"py_compile", "pytest"}
ALLOWED_LOCAL_SCRIPTS = {
    "scripts/nmbot_architecture_preflight.py",
    "scripts/nmbot_check.py",
    "scripts/nmbot_project_audit.py",
    "scripts/nmbot_v1_quality_gate.py",
    "scripts/nmbot_v2_quality_gate.py",
}


class ManifestError(ValueError):
    pass


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest: {path}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest must be JSON-compatible YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be an object")
    return data


def _repo_relative(path: str) -> Path:
    value = Path(path)
    if value.is_absolute() or ".." in value.parts:
        raise ManifestError(f"unsafe path: {path}")
    return ROOT / value


def _has_forbidden_token(argv: list[str]) -> str | None:
    for item in argv:
        folded = item.casefold()
        parts = {folded, Path(folded).name}
        if parts & FORBIDDEN_TOKENS:
            return item
        if any(mark in item for mark in (";", "&&", "||", "`", "$(", "|", ">", "<")):
            return item
    return None


def _validate_python_command(argv: list[str]) -> None:
    if len(argv) < 2:
        raise ManifestError("python command is incomplete")
    if argv[1] == "-m":
        if len(argv) < 3 or argv[2] not in ALLOWED_PYTHON_MODULES:
            raise ManifestError(f"python module is not allowlisted: {argv[2] if len(argv) > 2 else ''}")
        if argv[2] == "pytest" and not any(arg.startswith("tests/test_") for arg in argv[3:]):
            raise ManifestError("pytest command must target explicit test files")
        if argv[2] == "py_compile":
            for target in argv[3:]:
                if target.startswith("-"):
                    continue
                path = _repo_relative(target)
                if path.suffix != ".py":
                    raise ManifestError(f"py_compile target is not python: {target}")
        return
    script = argv[1]
    if script not in ALLOWED_LOCAL_SCRIPTS:
        raise ManifestError(f"local script is not allowlisted: {script}")
    _repo_relative(script)
    if script == "scripts/nmbot_v1_quality_gate.py" and argv[2:] != ["--all"]:
        raise ManifestError("V1 quality gate is allowlisted only as offline fixture replay: --all")
    if script == "scripts/nmbot_v2_quality_gate.py" and argv[2:] != ["--all"]:
        raise ManifestError("quality gate is allowlisted only as offline fixture replay: --all")


def _normalize_command(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ManifestError("command entry must be an object")
    name = str(raw.get("name") or "").strip()
    argv = raw.get("argv")
    if not name or not isinstance(argv, list) or not all(isinstance(x, str) and x for x in argv):
        raise ManifestError("command requires name and non-empty argv list")
    forbidden = _has_forbidden_token(argv)
    if forbidden:
        raise ManifestError(f"unsafe command token: {forbidden}")
    if argv[0] != "{python}":
        raise ManifestError("only {python} commands are accepted")
    _validate_python_command(argv)
    return {
        "name": name,
        "argv": [sys.executable, *argv[1:]],
        "evidence": raw.get("evidence", []),
        "time": raw.get("time", "unknown"),
        "cost": raw.get("cost", "none"),
        "network": raw.get("network", "none"),
        "side_effects": raw.get("side_effects", "none"),
    }


def load_and_validate_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, list[dict[str, Any]]]:
    data = _load_manifest(path)
    scopes = data.get("scopes")
    if not isinstance(scopes, dict):
        raise ManifestError("manifest.scopes must be an object")
    unknown = set(scopes) - SUPPORTED_SCOPES
    missing = SUPPORTED_SCOPES - set(scopes)
    if unknown:
        raise ManifestError(f"unsupported scope in manifest: {', '.join(sorted(unknown))}")
    if missing:
        raise ManifestError(f"missing scope in manifest: {', '.join(sorted(missing))}")
    normalized: dict[str, list[dict[str, Any]]] = {}
    for scope, spec in scopes.items():
        if not isinstance(spec, dict):
            raise ManifestError(f"scope must be an object: {scope}")
        commands = spec.get("commands")
        if not isinstance(commands, list) or not commands:
            raise ManifestError(f"scope has no commands: {scope}")
        normalized[scope] = [_normalize_command(command) for command in commands]
    return normalized


def verify_docs() -> int:
    required = {
        "docs/NMBOT_OPERATIONS_MAP.md": ["source + live markers determine runtime", "TBD"],
        "docs/NMBOT_RUNTIME_REGISTRY.md": ["needs_live_verification", "runtime_version_override"],
        "docs/NMBOT_EXTERNAL_CONTRACTS.md": ["Jivo", "callback outbox"],
        "docs/NMBOT_RUNBOOK.md": ["bot not responding", "nmbot_diag.sh --logs"],
        "docs/NMBOT_COMMAND_MIGRATION.md": ["nmbot check", "deferred"],
        "docs/NMBOT_DEVELOPER_BASELINE.md": ["nmbot_check_benchmark.py", "p50", "p95", "no network"],
        "tests/nmbot_check_manifest.yaml": ["\"docs\"", "\"contracts\"", "\"runtime\"", "\"audit\"", "\"quality\""],
    }
    failures: list[str] = []
    for relative, markers in required.items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing:{relative}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in markers:
            if marker not in text:
                failures.append(f"missing marker {marker!r} in {relative}")
    failures.extend(validate_documentation_structure(ROOT))
    if failures:
        for item in failures:
            print(f"FAIL docs: {item}")
        return 1
    print("PASS docs: static documentation markers and structure present")
    return 0


def _inside_root(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _project_relative_target(root: Path, raw: str, *, base_dir: Path) -> Path | None:
    target = raw.strip().strip("<>")
    if not target or target.startswith("#"):
        return None
    parsed = urlsplit(target)
    scheme = parsed.scheme.casefold()
    if scheme in {"http", "https", "mailto"} or parsed.netloc:
        return None
    if scheme:
        raise ValueError(f"unsupported link scheme {scheme!r}")
    without_fragment = urlunsplit(("", "", parsed.path, "", ""))
    if not without_fragment:
        return None
    value = Path(without_fragment)
    if value.is_absolute():
        return value
    return base_dir / value


def _validate_docs_readme(root: Path) -> list[str]:
    failures: list[str] = []
    path = root / "docs" / "README.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ["missing:docs/README.md"]

    numbered_headings = [line.strip() for line in text.splitlines() if re.match(r"^##\s+\d+\.", line)]
    for heading in DOCS_ROUTE_HEADINGS:
        count = numbered_headings.count(heading)
        if count != 1:
            failures.append(f"docs/README.md route heading {heading!r} count={count}")
    for heading in numbered_headings:
        if heading not in DOCS_ROUTE_HEADINGS:
            failures.append(f"docs/README.md unexpected numbered route heading {heading!r}")

    for match in MARKDOWN_LINK_RE.finditer(text):
        raw_target = match.group(1)
        try:
            target = _project_relative_target(root, raw_target, base_dir=path.parent)
        except ValueError as exc:
            failures.append(f"docs/README.md {exc}: {raw_target}")
            continue
        if target is None:
            continue
        if not _inside_root(target, root):
            failures.append(f"docs/README.md link escapes project root: {raw_target}")
            continue
        if not target.exists():
            failures.append(f"docs/README.md missing local link target: {raw_target}")
    return failures


def _iter_owner_targets(projects: dict[str, Any]) -> list[tuple[str, str, Any]]:
    targets: list[tuple[str, str, Any]] = []
    for project_id, owners in projects.items():
        if not isinstance(owners, dict):
            targets.append((str(project_id), "<project>", owners))
            continue
        for owner_key, target in owners.items():
            targets.append((str(project_id), str(owner_key), target))
    return targets


def _validate_documentation_owners(root: Path) -> list[str]:
    failures: list[str] = []
    path = root / "config" / "project_documentation_owners.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"config/project_documentation_owners.json cannot parse: {exc}"]
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return ["config/project_documentation_owners.json projects must be an object"]
    nmbot = projects.get("nmbot")
    if not isinstance(nmbot, dict) or not nmbot:
        failures.append("config/project_documentation_owners.json projects.nmbot must be a non-empty object")
    for project_id, owner_key, target in _iter_owner_targets(projects):
        label = f"config/project_documentation_owners.json projects.{project_id}.{owner_key}"
        if not isinstance(target, str) or not target.strip():
            failures.append(f"{label} target must be a non-empty string")
            continue
        value = Path(target)
        if value.is_absolute() or ".." in value.parts:
            failures.append(f"{label} rejects absolute or escaping path: {target}")
            continue
        resolved = root / value
        if not _inside_root(resolved, root):
            failures.append(f"{label} escapes project root: {target}")
            continue
        if not resolved.is_file():
            failures.append(f"{label} missing owner target: {target}")
    return failures


def validate_documentation_structure(root: Path = ROOT) -> list[str]:
    """Return deterministic offline docs registry/owner failures."""
    root = root.resolve(strict=False)
    return [*_validate_docs_readme(root), *_validate_documentation_owners(root)]


def run_scopes(scopes: list[str], *, manifest_path: Path, dry_run: bool) -> tuple[int, list[dict[str, Any]]]:
    manifest = load_and_validate_manifest(manifest_path)
    unknown = [scope for scope in scopes if scope not in manifest]
    if unknown:
        raise ManifestError(f"unknown scope: {', '.join(unknown)}")
    results: list[dict[str, Any]] = []
    exit_code = 0
    for scope in scopes:
        for command in manifest[scope]:
            record = {"scope": scope, "name": command["name"], "argv": command["argv"], "status": "skipped" if dry_run else "run"}
            if dry_run:
                print(f"SKIPPED {scope}:{command['name']} — dry-run")
                results.append(record)
                continue
            print(f"RUN {scope}:{command['name']}")
            proc = subprocess.run(command["argv"], cwd=ROOT, text=True, capture_output=True, check=False)
            record.update({"returncode": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]})
            if proc.stdout:
                print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
            if proc.stderr:
                print(proc.stderr, end="" if proc.stderr.endswith("\n") else "\n", file=sys.stderr)
            if proc.returncode == 0:
                record["status"] = "passed"
                print(f"PASSED {scope}:{command['name']}")
            else:
                record["status"] = "failed"
                exit_code = proc.returncode or 1
                print(f"FAILED {scope}:{command['name']} rc={proc.returncode}")
                results.append(record)
                return exit_code, results
            results.append(record)
    return exit_code, results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local read-only nmbot fast checks from manifest.")
    parser.add_argument("scopes", nargs="*", help="docs/contracts/v0/v1/v2/runtime/audit/quality")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--internal-verify-docs", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.internal_verify_docs:
        return verify_docs()
    selected = args.scopes or ["docs"]
    try:
        code, results = run_scopes(selected, manifest_path=args.manifest, dry_run=args.dry_run)
    except ManifestError as exc:
        if args.json:
            print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"status": "passed" if code == 0 else "failed", "results": results}, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
