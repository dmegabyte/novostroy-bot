#!/usr/bin/env python3
"""Validate Qapairs static retrieval evidence without importing Qapairs code."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from project_adapter_core import AdapterError, ProjectAdapter, load_adapter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = PROJECT_ROOT / "config" / "qapairs_retrieval_sources.json"
DEFAULT_STAGES = PROJECT_ROOT / "config" / "qapairs_stage_map.json"
DEFAULT_DIAGNOSTICS = PROJECT_ROOT / "config" / "qapairs_diagnostic_codes.json"
LEGACY_ACTIVE_TOKENS = ("qapairs-daemon", "projects/qapairs/daemon", "projects/qapairs/poller")
QAPAIRS_PROJECT_ID = "qapairs"


class ManifestError(Exception):
    """A manifest failed static validation."""


class ExclusionPolicy(NamedTuple):
    owner_root: Path
    rel_files: frozenset[str]
    rel_dirs: frozenset[str]
    absolute_files: frozenset[Path]
    absolute_dirs: frozenset[Path]


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - exact JSON parser message is not part of contract
        raise ManifestError(f"{path}: cannot read JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: top-level JSON must be an object")
    return data


def _qapairs_adapter() -> ProjectAdapter:
    try:
        return load_adapter(QAPAIRS_PROJECT_ID)
    except AdapterError as exc:
        raise ManifestError(str(exc)) from exc


def _adapter_manifest_paths(adapter: ProjectAdapter) -> tuple[Path, Path, Path]:
    if adapter.manifest_path is None or adapter.stage_map_path is None or adapter.diagnostics_path is None:
        raise ManifestError("qapairs adapter manifest paths are not fully configured")
    return (Path(adapter.manifest_path).resolve(), Path(adapter.stage_map_path).resolve(), Path(adapter.diagnostics_path).resolve())


def _assert_allowlisted_manifest_paths(paths: Iterable[Path], adapter: ProjectAdapter) -> tuple[Path, Path, Path]:
    actual = tuple(Path(path).resolve() for path in paths)
    expected = _adapter_manifest_paths(adapter)
    if actual != expected:
        raise ManifestError("qapairs manifest paths must match the checked-in adapter allowlist exactly")
    return actual


def owner_root_from(*manifests: dict[str, Any], expected_owner_root: Path) -> Path:
    roots = []
    for manifest in manifests:
        adapter = manifest.get("adapter") if isinstance(manifest.get("adapter"), dict) else {}
        root = manifest.get("owner_root") or adapter.get("owner_root")
        if root:
            roots.append(str(root))
    if not roots:
        raise ManifestError("owner_root is required")
    if len(set(roots)) != 1:
        raise ManifestError(f"owner_root mismatch: {sorted(set(roots))}")
    try:
        root_path = Path(roots[0]).resolve(strict=True)
    except OSError as exc:
        raise ManifestError(f"owner_root does not exist or is not a directory: {roots[0]}") from exc
    if not root_path.exists() or not root_path.is_dir():
        raise ManifestError(f"owner_root does not exist or is not a directory: {root_path}")
    expected = expected_owner_root.resolve(strict=True)
    if root_path != expected:
        raise ManifestError(f"owner_root must match qapairs adapter root exactly: {root_path}")
    return root_path


def ensure_owner_relative(owner_root: Path, rel_path: str, *, label: str) -> Path:
    if not rel_path or Path(rel_path).is_absolute():
        raise ManifestError(f"{label}: path must be owner-root-relative: {rel_path!r}")
    candidate = (owner_root / rel_path).resolve(strict=True)
    try:
        candidate.relative_to(owner_root.resolve(strict=True))
    except ValueError as exc:
        raise ManifestError(f"{label}: path escapes owner_root: {rel_path}") from exc
    if not candidate.exists():
        raise ManifestError(f"{label}: path does not exist: {rel_path}")
    return candidate


def _normalize_owner_rel(rel_path: str, *, label: str) -> str:
    raw = str(rel_path or "").strip().replace("\\", "/")
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ManifestError(f"{label}: path must be owner-root-relative and non-escaping: {rel_path!r}")
    return "/".join(path.parts)


def build_exclusion_policy(manifest: dict[str, Any], owner_root: Path) -> ExclusionPolicy:
    excluded = manifest.get("excluded") if isinstance(manifest.get("excluded"), dict) else {}
    paths = require_list(excluded.get("paths", []), "sources.excluded.paths")
    rel_files: set[str] = set()
    rel_dirs: set[str] = set()
    absolute_files: set[Path] = set()
    absolute_dirs: set[Path] = set()
    resolved_owner = owner_root.resolve(strict=True)
    for index, raw_item in enumerate(paths):
        raw = str(raw_item or "").strip()
        if not raw:
            raise ManifestError(f"sources.excluded.paths[{index}]: empty exclusion")
        is_dir = raw.replace("\\", "/").endswith("/")
        raw_path = Path(raw)
        if raw_path.is_absolute():
            resolved = raw_path.resolve(strict=False)
            try:
                rel = resolved.relative_to(resolved_owner)
            except ValueError:
                (absolute_dirs if is_dir or resolved.is_dir() else absolute_files).add(resolved)
                continue
            normalized = "/".join(rel.parts)
        else:
            normalized = _normalize_owner_rel(raw.rstrip("/"), label=f"sources.excluded.paths[{index}]")
        if is_dir:
            rel_dirs.add(normalized)
        else:
            rel_files.add(normalized)
    return ExclusionPolicy(resolved_owner, frozenset(rel_files), frozenset(rel_dirs), frozenset(absolute_files), frozenset(absolute_dirs))


def _is_rel_excluded(policy: ExclusionPolicy, rel_path: str) -> bool:
    normalized = _normalize_owner_rel(rel_path, label="routeable ref")
    if normalized in policy.rel_files:
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
        raise ManifestError(f"{label}: path is excluded by qapairs retrieval policy: {rel_path}")


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
    return values


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(f"{label}: expected list")
    return value


def validate_sources(manifest: dict[str, Any], owner_root: Path, exclusions: ExclusionPolicy) -> None:
    adapter = manifest.get("adapter")
    if not isinstance(adapter, dict) or adapter.get("canonical_notebook") != "cc-daemons":
        raise ManifestError("sources: adapter.canonical_notebook must be cc-daemons")
    active_sources = require_list(manifest.get("active_sources"), "sources.active_sources")
    for index, item in enumerate(active_sources):
        if not isinstance(item, dict):
            raise ManifestError(f"sources.active_sources[{index}]: expected object")
        rel_path = str(item.get("path") or "")
        if any(token in rel_path for token in LEGACY_ACTIVE_TOKENS):
            raise ManifestError(f"sources.active_sources[{index}]: legacy path in active source: {rel_path}")
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
        for test_index, test_ref in enumerate(require_list(stage.get("test_refs"), f"stages.stages[{index}].test_refs")):
            ensure_not_excluded(exclusions, str(test_ref), label=f"stages.stages[{index}].test_refs[{test_index}]")
            ensure_owner_relative(owner_root, str(test_ref), label=f"stages.stages[{index}].test_refs[{test_index}]")


def validate_diagnostics(manifest: dict[str, Any], owner_root: Path, exclusions: ExclusionPolicy) -> None:
    for index, diagnostic in enumerate(require_list(manifest.get("diagnostics"), "diagnostics.diagnostics")):
        if not isinstance(diagnostic, dict):
            raise ManifestError(f"diagnostics.diagnostics[{index}]: expected object")
        code = str(diagnostic.get("code") or "")
        source_rel = str(diagnostic.get("owner_source") or "")
        symbol = str(diagnostic.get("owner_symbol") or "")
        if not code or not source_rel or not symbol:
            raise ManifestError(f"diagnostics.diagnostics[{index}]: code, owner_source and owner_symbol are required")
        ensure_not_excluded(exclusions, source_rel, label=f"diagnostics.diagnostics[{index}].owner_source")
        source_path = ensure_owner_relative(owner_root, source_rel, label=f"diagnostics.diagnostics[{index}].owner_source")
        node = find_symbol(parse_ast(source_path), symbol)
        if node is None:
            raise ManifestError(f"{source_rel}: missing diagnostic owner symbol {symbol}")
        if code not in string_literals(node):
            raise ManifestError(f"{source_rel}:{symbol}: diagnostic literal not found: {code}")


def validate(paths: Iterable[Path] | None = None) -> list[str]:
    adapter = _qapairs_adapter()
    sources_path, stages_path, diagnostics_path = _assert_allowlisted_manifest_paths(paths or _adapter_manifest_paths(adapter), adapter)
    sources = load_json(sources_path)
    stages = load_json(stages_path)
    diagnostics = load_json(diagnostics_path)
    owner_root = owner_root_from(sources, stages, diagnostics, expected_owner_root=adapter.root)
    exclusions = build_exclusion_policy(sources, owner_root)
    validate_sources(sources, owner_root, exclusions)
    validate_stages(stages, owner_root, exclusions)
    validate_diagnostics(diagnostics, owner_root, exclusions)
    return [
        f"owner_root={owner_root}",
        f"active_sources={len(sources.get('active_sources') or [])}",
        f"stages={len(stages.get('stages') or [])}",
        f"diagnostics={len(diagnostics.get('diagnostics') or [])}",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        summary = validate()
    except ManifestError as exc:
        print(f"ERROR: {exc}")
        return 1
    print("Qapairs context manifest OK")
    for line in summary:
        print(f"- {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
