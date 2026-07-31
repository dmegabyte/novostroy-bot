#!/usr/bin/env python3
"""Validate CC2 static retrieval evidence without importing CC2 runtime."""

from __future__ import annotations

import ast
import fnmatch
import json
import re
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from project_adapter_core import AdapterError, ProjectAdapter, load_adapter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = "cc2"
CANONICAL_NOTEBOOK = "cc2"
OWNER_ROOT = Path("/home/ser/projects/cc-daemons")
DEFAULT_SOURCES = PROJECT_ROOT / "config" / "cc2_retrieval_sources.json"
DEFAULT_STAGES = PROJECT_ROOT / "config" / "cc2_stage_map.json"
DEFAULT_DIAGNOSTICS = PROJECT_ROOT / "config" / "cc2_diagnostic_codes.json"

FORBIDDEN_ROUTE_TOKENS = (
    "logs/",
    ".env",
    ".bak",
    ".sqlite",
    ".sqlite3",
    "pipeline_config.json",
    "projects/cc2/deferred_processor.py",
    "projects/cc2/daemon.py",
    "crm_status",
    "sheets.py",
    "eval/",
    "/tmp/",
)
FORBIDDEN_OPERATIONS = {
    "apply",
    "repair",
    "backfill",
    "crm_post",
    "google_sheets",
    "network",
    "ssh_vps",
    "vps",
    "eval",
    "runtime_logs",
    "sqlite_runtime",
    "legacy_sheets_path",
}
ALLOWED_ADAPTER_STATUSES = {
    "validating_local_developer_routing_only",
    "pilot_ready_local_developer_routing_only",
}
DIAGNOSTIC_CODE_RE = re.compile(r"^(?=.{4,80}$)[a-z][a-z0-9_]*(?::[a-z0-9_]+)?$")


class ManifestError(Exception):
    """A CC2 manifest failed static validation."""


class ExclusionPolicy(NamedTuple):
    owner_root: Path
    rel_files: frozenset[str]
    rel_dirs: frozenset[str]
    rel_globs: frozenset[str]
    absolute_files: frozenset[Path]
    absolute_dirs: frozenset[Path]
    operations: frozenset[str]


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover
        raise ManifestError(f"{path}: cannot read JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: top-level JSON must be an object")
    return data


def _cc2_adapter() -> ProjectAdapter:
    try:
        return load_adapter(PROJECT_ID)
    except AdapterError as exc:
        raise ManifestError(f"cc2 adapter root must be code-level allowlisted in project_adapter_core: {exc}") from exc


def _adapter_manifest_paths(adapter: ProjectAdapter) -> tuple[Path, Path, Path]:
    if adapter.manifest_path is None or adapter.stage_map_path is None or adapter.diagnostics_path is None:
        raise ManifestError("cc2 adapter manifest paths are not fully configured")
    return (Path(adapter.manifest_path).resolve(), Path(adapter.stage_map_path).resolve(), Path(adapter.diagnostics_path).resolve())


def _default_manifest_paths() -> tuple[Path, Path, Path]:
    return _adapter_manifest_paths(_cc2_adapter())


def _assert_checked_in_paths(paths: Iterable[Path] | None) -> tuple[Path, Path, Path]:
    actual = tuple(Path(path).resolve() for path in (paths or _default_manifest_paths()))
    expected = _default_manifest_paths()
    if actual != expected:
        raise ManifestError("cc2 manifest paths must match the checked-in adapter allowlist exactly")
    return actual


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(f"{label}: expected list")
    return value


def _manifest_owner_root(manifest: dict[str, Any]) -> str | None:
    adapter = manifest.get("adapter") if isinstance(manifest.get("adapter"), dict) else {}
    root = manifest.get("owner_root") or adapter.get("owner_root")
    return str(root) if root else None


def owner_root_from(*manifests: dict[str, Any], expected_owner_root: Path = OWNER_ROOT) -> Path:
    roots = [root for manifest in manifests if (root := _manifest_owner_root(manifest))]
    if not roots:
        raise ManifestError("owner_root is required")
    if len(set(roots)) != 1:
        raise ManifestError(f"owner_root mismatch: {sorted(set(roots))}")
    try:
        root_path = Path(roots[0]).resolve(strict=True)
        expected = expected_owner_root.resolve(strict=True)
    except OSError as exc:
        raise ManifestError(f"owner_root does not exist or is not a directory: {roots[0]}") from exc
    if root_path != expected:
        raise ManifestError(f"owner_root must match cc2 adapter root exactly: {root_path}")
    return root_path


def _normalize_owner_rel(rel_path: str, *, label: str) -> str:
    raw = str(rel_path or "").strip().replace("\\", "/")
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ManifestError(f"{label}: path must be owner-root-relative and non-escaping: {rel_path!r}")
    return "/".join(path.parts)


def ensure_owner_relative(owner_root: Path, rel_path: str, *, label: str) -> Path:
    normalized = _normalize_owner_rel(rel_path, label=label)
    candidate = (owner_root / normalized).resolve(strict=True)
    try:
        candidate.relative_to(owner_root.resolve(strict=True))
    except ValueError as exc:
        raise ManifestError(f"{label}: path escapes owner_root: {rel_path}") from exc
    if not candidate.exists():
        raise ManifestError(f"{label}: path does not exist: {rel_path}")
    return candidate


def build_exclusion_policy(manifest: dict[str, Any], owner_root: Path) -> ExclusionPolicy:
    excluded = manifest.get("excluded") if isinstance(manifest.get("excluded"), dict) else {}
    rel_files: set[str] = set()
    rel_dirs: set[str] = set()
    rel_globs: set[str] = set()
    absolute_files: set[Path] = set()
    absolute_dirs: set[Path] = set()
    resolved_owner = owner_root.resolve(strict=True)
    for index, raw_item in enumerate(require_list(excluded.get("paths", []), "sources.excluded.paths")):
        raw = str(raw_item or "").strip().replace("\\", "/")
        if not raw:
            raise ManifestError(f"sources.excluded.paths[{index}]: empty exclusion")
        is_dir = raw.endswith("/")
        if "*" in raw or "?" in raw:
            rel_globs.add(raw)
            continue
        raw_path = Path(raw.rstrip("/"))
        if raw_path.is_absolute():
            resolved = raw_path.resolve(strict=False)
            try:
                rel = resolved.relative_to(resolved_owner)
                normalized = "/".join(rel.parts)
            except ValueError:
                (absolute_dirs if is_dir or resolved.is_dir() else absolute_files).add(resolved)
                continue
        else:
            normalized = _normalize_owner_rel(raw.rstrip("/"), label=f"sources.excluded.paths[{index}]")
        (rel_dirs if is_dir else rel_files).add(normalized)
    operations = {str(item).strip() for item in require_list(excluded.get("operations", []), "sources.excluded.operations") if str(item).strip()}
    return ExclusionPolicy(resolved_owner, frozenset(rel_files), frozenset(rel_dirs), frozenset(rel_globs), frozenset(absolute_files), frozenset(absolute_dirs), frozenset(operations))


def _is_rel_excluded(policy: ExclusionPolicy, rel_path: str) -> bool:
    normalized = _normalize_owner_rel(rel_path, label="routable ref")
    if normalized in policy.rel_files or any(fnmatch.fnmatch(normalized, pattern) for pattern in policy.rel_globs):
        return True
    parts = Path(normalized).parts
    for excluded_dir in policy.rel_dirs:
        excluded_parts = Path(excluded_dir).parts
        if parts[: len(excluded_parts)] == excluded_parts:
            return True
    candidate = (policy.owner_root / normalized).resolve(strict=False)
    if candidate in policy.absolute_files:
        return True
    for absolute_dir in policy.absolute_dirs:
        try:
            candidate.relative_to(absolute_dir)
            return True
        except ValueError:
            continue
    return False


def ensure_not_excluded(policy: ExclusionPolicy, rel_path: str, *, label: str) -> None:
    if _is_rel_excluded(policy, rel_path):
        raise ManifestError(f"{label}: path is excluded by cc2 retrieval policy: {rel_path}")
    lowered = str(rel_path).replace("\\", "/").lower()
    if any(token in lowered for token in FORBIDDEN_ROUTE_TOKENS):
        raise ManifestError(f"{label}: forbidden runtime/legacy token in routable ref: {rel_path}")


def parse_ast(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise ManifestError(f"{path}: Python syntax error: {exc}") from exc


def find_symbol(tree: ast.AST, symbol: str) -> ast.AST | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            return node
    return None


def string_literals(node: ast.AST) -> set[str]:
    values: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            values.add(child.value)
        elif isinstance(child, ast.JoinedStr):
            values.update(part.value for part in child.values if isinstance(part, ast.Constant) and isinstance(part.value, str))
    return values


def _assert_adapter(manifest: dict[str, Any]) -> None:
    adapter = manifest.get("adapter")
    if not isinstance(adapter, dict):
        raise ManifestError("sources: adapter object is required")
    if adapter.get("id") != PROJECT_ID:
        raise ManifestError("sources: adapter.id must be cc2")
    if adapter.get("canonical_notebook") != CANONICAL_NOTEBOOK:
        raise ManifestError("sources: adapter.canonical_notebook must be cc2")
    if adapter.get("status") not in ALLOWED_ADAPTER_STATUSES:
        raise ManifestError("sources: adapter.status must be a local developer validating/pilot status")


def _assert_no_forbidden_operations(policy: ExclusionPolicy) -> None:
    missing = FORBIDDEN_OPERATIONS - set(policy.operations)
    if missing:
        raise ManifestError(f"sources.excluded.operations missing required exclusions: {sorted(missing)}")


def validate_sources(manifest: dict[str, Any], owner_root: Path, exclusions: ExclusionPolicy) -> None:
    _assert_adapter(manifest)
    _assert_no_forbidden_operations(exclusions)
    for index, item in enumerate(require_list(manifest.get("active_sources"), "sources.active_sources")):
        if not isinstance(item, dict):
            raise ManifestError(f"sources.active_sources[{index}]: expected object")
        rel_path = str(item.get("path") or "")
        ensure_not_excluded(exclusions, rel_path, label=f"sources.active_sources[{index}]")
        path = ensure_owner_relative(owner_root, rel_path, label=f"sources.active_sources[{index}]")
        if path.suffix == ".py":
            tree = parse_ast(path)
            for symbol in require_list(item.get("symbols"), f"sources.active_sources[{index}].symbols"):
                if find_symbol(tree, str(symbol)) is None:
                    raise ManifestError(f"{rel_path}: missing symbol {symbol}")
    for index, item in enumerate(require_list(manifest.get("docs"), "sources.docs")):
        if not isinstance(item, dict):
            raise ManifestError(f"sources.docs[{index}]: expected object")
        rel_path = str(item.get("path") or "")
        ensure_not_excluded(exclusions, rel_path, label=f"sources.docs[{index}]")
        ensure_owner_relative(owner_root, rel_path, label=f"sources.docs[{index}]")
    for index, rel_path in enumerate(require_list(manifest.get("focused_tests"), "sources.focused_tests")):
        ensure_not_excluded(exclusions, str(rel_path), label=f"sources.focused_tests[{index}]")
        ensure_owner_relative(owner_root, str(rel_path), label=f"sources.focused_tests[{index}]")


def validate_stages(manifest: dict[str, Any], owner_root: Path, exclusions: ExclusionPolicy) -> None:
    if manifest.get("adapter_id") != PROJECT_ID:
        raise ManifestError("stages: adapter_id must be cc2")
    for index, stage in enumerate(require_list(manifest.get("stages"), "stages.stages")):
        if not isinstance(stage, dict):
            raise ManifestError(f"stages.stages[{index}]: expected object")
        source_rel = str(stage.get("owner_source") or "")
        ensure_not_excluded(exclusions, source_rel, label=f"stages.stages[{index}].owner_source")
        source_path = ensure_owner_relative(owner_root, source_rel, label=f"stages.stages[{index}].owner_source")
        tree = parse_ast(source_path)
        for symbol in require_list(stage.get("owner_symbols"), f"stages.stages[{index}].owner_symbols"):
            if find_symbol(tree, str(symbol)) is None:
                raise ManifestError(f"{source_rel}: missing stage symbol {symbol}")
        for test_index, test_ref in enumerate(require_list(stage.get("test_refs", []), f"stages.stages[{index}].test_refs")):
            ensure_not_excluded(exclusions, str(test_ref), label=f"stages.stages[{index}].test_refs[{test_index}]")
            ensure_owner_relative(owner_root, str(test_ref), label=f"stages.stages[{index}].test_refs[{test_index}]")


def validate_diagnostics(manifest: dict[str, Any], owner_root: Path, exclusions: ExclusionPolicy) -> None:
    if manifest.get("adapter_id") != PROJECT_ID:
        raise ManifestError("diagnostics: adapter_id must be cc2")
    for index, diagnostic in enumerate(require_list(manifest.get("diagnostics"), "diagnostics.diagnostics")):
        if not isinstance(diagnostic, dict):
            raise ManifestError(f"diagnostics.diagnostics[{index}]: expected object")
        code = str(diagnostic.get("code") or "")
        source_rel = str(diagnostic.get("owner_source") or "")
        symbol = str(diagnostic.get("owner_symbol") or "")
        if not code or not source_rel or not symbol:
            raise ManifestError(f"diagnostics.diagnostics[{index}]: code, owner_source and owner_symbol are required")
        if not DIAGNOSTIC_CODE_RE.fullmatch(code):
            raise ManifestError(f"diagnostics.diagnostics[{index}]: code must be safe lowercase ASCII machine id: {code}")
        ensure_not_excluded(exclusions, source_rel, label=f"diagnostics.diagnostics[{index}].owner_source")
        source_path = ensure_owner_relative(owner_root, source_rel, label=f"diagnostics.diagnostics[{index}].owner_source")
        node = find_symbol(parse_ast(source_path), symbol)
        if node is None:
            raise ManifestError(f"{source_rel}: missing diagnostic owner symbol {symbol}")
        literals = string_literals(node)
        literal = str(diagnostic.get("literal") or code)
        match = str(diagnostic.get("match") or "exact")
        if match == "exact":
            found = literal in literals
        elif match in {"prefix", "prefix_literal"}:
            found = any(lit.startswith(literal) or literal.startswith(lit) for lit in literals)
        else:
            raise ManifestError(f"diagnostics.diagnostics[{index}]: unsupported match mode: {match}")
        if not found:
            raise ManifestError(f"{source_rel}:{symbol}: diagnostic literal not found: {literal}")


def validate_manifest_dicts(sources: dict[str, Any], stages: dict[str, Any], diagnostics: dict[str, Any], *, expected_owner_root: Path | None = None) -> list[str]:
    if expected_owner_root is None:
        expected_owner_root = _cc2_adapter().root
    owner_root = owner_root_from(sources, stages, diagnostics, expected_owner_root=expected_owner_root)
    exclusions = build_exclusion_policy(sources, owner_root)
    validate_sources(sources, owner_root, exclusions)
    validate_stages(stages, owner_root, exclusions)
    validate_diagnostics(diagnostics, owner_root, exclusions)
    return ["cc2_retrieval_sources", "cc2_stage_map", "cc2_diagnostic_codes"]


def validate(paths: Iterable[Path] | None = None) -> list[str]:
    sources_path, stages_path, diagnostics_path = _assert_checked_in_paths(paths)
    return validate_manifest_dicts(load_json(sources_path), load_json(stages_path), load_json(diagnostics_path), expected_owner_root=_cc2_adapter().root)


def main() -> int:
    print(json.dumps({"ok": True, "project_id": PROJECT_ID, "validated": validate()}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
