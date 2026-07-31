#!/usr/bin/env python3
"""Validate MPN static retrieval evidence without importing MPN runtime code."""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from project_adapter_core import AdapterError, ProjectAdapter, load_adapter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCES = PROJECT_ROOT / "config" / "mpn_retrieval_sources.json"
DEFAULT_STAGES = PROJECT_ROOT / "config" / "mpn_stage_map.json"
DEFAULT_DIAGNOSTICS = PROJECT_ROOT / "config" / "mpn_diagnostic_codes.json"
DEFAULT_DEPENDENCY_CARD = PROJECT_ROOT / "config" / "mpn_dependency_card.json"
MPN_PROJECT_ID = "mpn"
DEPENDENCY_PROJECT_ID = "cc-daemons"
EXPECTED_DEPENDENCY_SCHEMA = "project_dependency_card.v1"
ALLOWED_DEPENDENCY_QUERY_TYPES = {"contract", "interface"}
ALLOWED_ADAPTER_STATUSES = {
    "validating_local_developer_routing_only",
    "pilot_ready_local_developer_routing_only",
}
DIAGNOSTIC_CODE_RE = re.compile(r"^(?=.{4,80}$)[a-z][a-z0-9_]*(?::[a-z0-9_]+)?$")


class ManifestError(Exception):
    """A manifest failed static validation."""


class ExclusionPolicy(NamedTuple):
    owner_root: Path
    rel_files: frozenset[str]
    rel_dirs: frozenset[str]
    rel_globs: frozenset[str]
    absolute_files: frozenset[Path]
    absolute_dirs: frozenset[Path]


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover
        raise ManifestError(f"{path}: cannot read JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: top-level JSON must be an object")
    return data


def _load_adapter_required(project_id: str) -> ProjectAdapter:
    try:
        return load_adapter(project_id)
    except AdapterError as exc:
        raise ManifestError(
            f"{project_id} adapter root must be code-level allowlisted in project_adapter_core before MPN validation can pass: {exc}"
        ) from exc


def _mpn_adapter() -> ProjectAdapter:
    return _load_adapter_required(MPN_PROJECT_ID)


def _dependency_adapter() -> ProjectAdapter:
    return _load_adapter_required(DEPENDENCY_PROJECT_ID)


def _adapter_manifest_paths(adapter: ProjectAdapter) -> tuple[Path, Path, Path, Path]:
    if adapter.manifest_path is None or adapter.stage_map_path is None or adapter.diagnostics_path is None:
        raise ManifestError("mpn adapter manifest paths are not fully configured")
    return (
        Path(adapter.manifest_path).resolve(),
        Path(adapter.stage_map_path).resolve(),
        Path(adapter.diagnostics_path).resolve(),
        DEFAULT_DEPENDENCY_CARD.resolve(),
    )


def _assert_allowlisted_manifest_paths(paths: Iterable[Path], adapter: ProjectAdapter) -> tuple[Path, Path, Path, Path]:
    actual = tuple(Path(path).resolve() for path in paths)
    expected = _adapter_manifest_paths(adapter)
    if actual != expected:
        raise ManifestError("mpn manifest paths must match the checked-in adapter allowlist exactly")
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
    expected = expected_owner_root.resolve(strict=True)
    if root_path != expected:
        raise ManifestError(f"owner_root must match mpn adapter root exactly: {root_path}")
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


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(f"{label}: expected list")
    return value


def build_exclusion_policy(manifest: dict[str, Any], owner_root: Path) -> ExclusionPolicy:
    excluded = manifest.get("excluded") if isinstance(manifest.get("excluded"), dict) else {}
    paths = require_list(excluded.get("paths", []), "sources.excluded.paths")
    rel_files: set[str] = set()
    rel_dirs: set[str] = set()
    rel_globs: set[str] = set()
    absolute_files: set[Path] = set()
    absolute_dirs: set[Path] = set()
    resolved_owner = owner_root.resolve(strict=True)
    for index, raw_item in enumerate(paths):
        raw = str(raw_item or "").strip()
        if not raw:
            raise ManifestError(f"sources.excluded.paths[{index}]: empty exclusion")
        is_dir = raw.replace("\\", "/").endswith("/")
        if any(ch in raw for ch in "*?[]") and not Path(raw).is_absolute():
            rel_globs.add(_normalize_owner_rel(raw.rstrip("/"), label=f"sources.excluded.paths[{index}]"))
            continue
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
    return ExclusionPolicy(resolved_owner, frozenset(rel_files), frozenset(rel_dirs), frozenset(rel_globs), frozenset(absolute_files), frozenset(absolute_dirs))


def _is_rel_excluded(policy: ExclusionPolicy, rel_path: str) -> bool:
    normalized = _normalize_owner_rel(rel_path, label="routeable ref")
    if normalized in policy.rel_files:
        return True
    if any(fnmatch.fnmatchcase(normalized, pattern) for pattern in policy.rel_globs):
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
        raise ManifestError(f"{label}: path is excluded by mpn retrieval policy: {rel_path}")


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


def symbol_exists(tree: ast.AST, symbol: str) -> bool:
    if find_symbol(tree, symbol) is not None:
        return True
    return any(isinstance(node, ast.ClassDef) and any(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == symbol for child in node.body) for node in ast.walk(tree))


def string_literals(node: ast.AST) -> set[str]:
    values: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            values.add(child.value)
        elif isinstance(child, ast.JoinedStr):
            static_parts = [part.value for part in child.values if isinstance(part, ast.Constant) and isinstance(part.value, str)]
            values.update(static_parts)
            if len(static_parts) == len(child.values):
                values.add("".join(static_parts))
    return values


def subtree_for_symbol(tree: ast.AST, symbol: str) -> ast.AST:
    node = find_symbol(tree, symbol)
    if node is None:
        raise ManifestError(f"missing symbol {symbol}")
    return node


def validate_sources(manifest: dict[str, Any], owner_root: Path, exclusions: ExclusionPolicy) -> None:
    adapter = manifest.get("adapter")
    if not isinstance(adapter, dict) or adapter.get("canonical_notebook") != "mpn":
        raise ManifestError("sources: adapter.canonical_notebook must be mpn")
    if adapter.get("status") not in ALLOWED_ADAPTER_STATUSES:
        raise ManifestError("sources: adapter.status must be a local developer validating/pilot status")
    for index, item in enumerate(require_list(manifest.get("active_sources"), "sources.active_sources")):
        if not isinstance(item, dict):
            raise ManifestError(f"sources.active_sources[{index}]: expected object")
        rel_path = str(item.get("path") or "")
        ensure_not_excluded(exclusions, rel_path, label=f"sources.active_sources[{index}]")
        path = ensure_owner_relative(owner_root, rel_path, label=f"sources.active_sources[{index}]")
        if path.suffix == ".py":
            tree = parse_ast(path)
            for symbol in require_list(item.get("symbols"), f"sources.active_sources[{index}].symbols"):
                if not symbol_exists(tree, str(symbol)):
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
            if not symbol_exists(tree, str(symbol)):
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
        if not DIAGNOSTIC_CODE_RE.fullmatch(code):
            raise ManifestError(f"diagnostics.diagnostics[{index}]: code must be safe lowercase ASCII machine id: {code}")
        ensure_not_excluded(exclusions, source_rel, label=f"diagnostics.diagnostics[{index}].owner_source")
        source_path = ensure_owner_relative(owner_root, source_rel, label=f"diagnostics.diagnostics[{index}].owner_source")
        tree = parse_ast(source_path)
        node = find_symbol(tree, symbol)
        if node is None:
            raise ManifestError(f"{source_rel}: missing diagnostic owner symbol {symbol}")
        literal = str(diagnostic.get("literal") or code)
        match = str(diagnostic.get("match") or "exact")
        literals = string_literals(node)
        if match == "exact":
            found = literal in literals
        elif match in {"prefix", "prefix_literal"}:
            found = any(lit.startswith(literal) or literal.startswith(lit) for lit in literals)
        else:
            raise ManifestError(f"diagnostics.diagnostics[{index}]: unsupported match mode: {match}")
        if not found:
            raise ManifestError(f"{source_rel}:{symbol}: diagnostic literal not found: {literal}")


def validate_dependency_card(card: dict[str, Any], dependency_root: Path) -> None:
    if card.get("schema") != EXPECTED_DEPENDENCY_SCHEMA:
        raise ManifestError(f"dependency_card: schema must be {EXPECTED_DEPENDENCY_SCHEMA}")
    if card.get("owner_project") != DEPENDENCY_PROJECT_ID or card.get("consumer_project") != MPN_PROJECT_ID:
        raise ManifestError("dependency_card: owner_project must be cc-daemons and consumer_project must be mpn")
    if card.get("canonical_notebook") != "cc-daemons":
        raise ManifestError("dependency_card: canonical_notebook must be cc-daemons")
    if set(require_list(card.get("allowed_query_types"), "dependency_card.allowed_query_types")) - ALLOWED_DEPENDENCY_QUERY_TYPES:
        raise ManifestError("dependency_card: allowed_query_types must be contract/interface only")
    if card.get("max_depth") != 1 or card.get("max_records") != 2:
        raise ManifestError("dependency_card: max_depth=1 and max_records=2 are required")
    if card.get("transitive_traversal") is not False or card.get("no_transitive") is not True:
        raise ManifestError("dependency_card: transitive traversal must be disabled")
    records = require_list(card.get("records"), "dependency_card.records")
    if len(records) > 2:
        raise ManifestError("dependency_card: max_records=2 exceeded")
    resolved_dependency = dependency_root.resolve(strict=True)
    for record_index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ManifestError(f"dependency_card.records[{record_index}]: expected object")
        if record.get("kind") != "interface":
            raise ManifestError(f"dependency_card.records[{record_index}]: only interface records are allowed")
        if "dependencies" in record or "transitive" in record:
            raise ManifestError(f"dependency_card.records[{record_index}]: transitive dependency fields are denied")
        if Path(str(record.get("owner_root") or "")).resolve(strict=True) != resolved_dependency:
            raise ManifestError(f"dependency_card.records[{record_index}]: owner_root must match cc-daemons adapter root exactly")
        for path_index, ref in enumerate(require_list(record.get("paths"), f"dependency_card.records[{record_index}].paths")):
            if not isinstance(ref, dict):
                raise ManifestError(f"dependency_card.records[{record_index}].paths[{path_index}]: expected object")
            rel_path = str(ref.get("path") or "")
            if Path(rel_path).is_absolute() or ".." in Path(rel_path).parts:
                raise ManifestError(f"dependency_card.records[{record_index}].paths[{path_index}]: path must be dependency-root-relative")
            if not rel_path.startswith("projects/mpn/"):
                raise ManifestError(f"dependency_card.records[{record_index}].paths[{path_index}]: only projects/mpn interface refs are allowed")
            source_path = ensure_owner_relative(resolved_dependency, rel_path, label=f"dependency_card.records[{record_index}].paths[{path_index}]")
            tree = parse_ast(source_path)
            for symbol in require_list(ref.get("symbols"), f"dependency_card.records[{record_index}].paths[{path_index}].symbols"):
                if not symbol_exists(tree, str(symbol)):
                    raise ManifestError(f"{rel_path}: missing dependency symbol {symbol}")
            source_text = source_path.read_text(encoding="utf-8")
            for literal in require_list(ref.get("literals", []), f"dependency_card.records[{record_index}].paths[{path_index}].literals"):
                if str(literal) not in source_text:
                    raise ManifestError(f"{rel_path}: dependency literal not found: {literal}")


def validate(paths: Iterable[Path] | None = None) -> list[str]:
    adapter = _mpn_adapter()
    dependency_adapter = _dependency_adapter()
    sources_path, stages_path, diagnostics_path, dependency_card_path = _assert_allowlisted_manifest_paths(
        paths or _adapter_manifest_paths(adapter), adapter
    )
    sources = load_json(sources_path)
    stages = load_json(stages_path)
    diagnostics = load_json(diagnostics_path)
    dependency_card = load_json(dependency_card_path)
    owner_root = owner_root_from(sources, stages, diagnostics, expected_owner_root=adapter.root)
    exclusions = build_exclusion_policy(sources, owner_root)
    validate_sources(sources, owner_root, exclusions)
    validate_stages(stages, owner_root, exclusions)
    validate_diagnostics(diagnostics, owner_root, exclusions)
    validate_dependency_card(dependency_card, dependency_adapter.root)
    return [
        f"owner_root={owner_root}",
        f"dependency_root={dependency_adapter.root}",
        f"active_sources={len(sources.get('active_sources') or [])}",
        f"stages={len(stages.get('stages') or [])}",
        f"diagnostics={len(diagnostics.get('diagnostics') or [])}",
        f"dependency_records={len(dependency_card.get('records') or [])}",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        summary = validate()
    except ManifestError as exc:
        print(f"ERROR: {exc}")
        return 1
    print("MPN context manifest OK")
    for line in summary:
        print(f"- {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
