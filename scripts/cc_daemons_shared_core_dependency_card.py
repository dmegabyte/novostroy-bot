#!/usr/bin/env python3
"""Validate the cc-daemons shared-core dependency card statically.

The card is intentionally a bounded dependency contour, not a routable project
adapter. Validation reads only checked-in card metadata and symbol/literal
evidence from the allowlisted cc-daemons root; it never imports cc-daemons
runtime code, calls network, prints source bodies, or writes state.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any, Iterable

from project_adapter_core import AdapterError, load_adapter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CARD = PROJECT_ROOT / "config" / "cc_daemons_shared_core_dependency_card.json"
DEPENDENCY_PROJECT_ID = "cc-daemons"
EXPECTED_SCHEMA = "project_dependency_card.v1"
ALLOWED_QUERY_TYPES = {"contract", "interface"}
ALLOWED_CONSUMERS = {"cc2", "mpn", "qapairs"}
ALLOWED_CORE_PATHS = {"core/daemon_engine.py", "core/logger.py"}
DENIED_CORE_PATHS = {
    "core/crm.py",
    "core/sheets.py",
    "core/vault.py",
    "core/provider_router.py",
    "core/config.py",
    "core/constants.py",
}
DENIED_SYMBOLS = {"CRMSender", "ProviderRouter", "ConfigLoader", "VaultToken"}
REQUIRED_EXCLUDED_PATHS = DENIED_CORE_PATHS | {"configs/", "logs/", ".env"}
REQUIRED_EXCLUDED_OPERATIONS = {"CRM", "Sheets", "Vault", "provider routing", "config secrets", "network", "runtime", "VPS", "apply", "write"}


class CardError(Exception):
    """Dependency card validation failed."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover
        raise CardError(f"{path}: cannot read JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CardError(f"{path}: top-level JSON must be an object")
    return data


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise CardError(f"{label}: expected list")
    return value


def _dependency_root() -> Path:
    try:
        return load_adapter(DEPENDENCY_PROJECT_ID).root
    except AdapterError as exc:
        raise CardError(f"cc-daemons adapter root must be code-level allowlisted and available: {exc}") from exc


def ensure_dependency_relative(owner_root: Path, rel_path: str, *, label: str) -> Path:
    raw = str(rel_path or "").strip().replace("\\", "/")
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise CardError(f"{label}: path must be dependency-root-relative and non-escaping: {rel_path!r}")
    candidate = (owner_root / path).resolve(strict=True)
    try:
        candidate.relative_to(owner_root.resolve(strict=True))
    except ValueError as exc:
        raise CardError(f"{label}: path escapes dependency root: {rel_path}") from exc
    if not candidate.is_file():
        raise CardError(f"{label}: path does not exist: {rel_path}")
    return candidate


def parse_ast(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise CardError(f"{path}: Python syntax error: {exc}") from exc


def symbol_exists(tree: ast.AST, symbol: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == symbol:
                    return True
    return False


def validate_card(card: dict[str, Any], dependency_root: Path) -> list[str]:
    if card.get("schema") != EXPECTED_SCHEMA:
        raise CardError(f"card.schema must be {EXPECTED_SCHEMA}")
    if card.get("owner_project") != DEPENDENCY_PROJECT_ID:
        raise CardError("card.owner_project must be cc-daemons")
    if card.get("canonical_notebook") != "cc-daemons":
        raise CardError("card.canonical_notebook must be cc-daemons")
    consumers = set(str(item) for item in require_list(card.get("consumer_projects"), "card.consumer_projects"))
    if not consumers or consumers - ALLOWED_CONSUMERS:
        raise CardError("card.consumer_projects must be limited to current pilot-ready adapters")
    if set(str(item) for item in require_list(card.get("allowed_query_types"), "card.allowed_query_types")) - ALLOWED_QUERY_TYPES:
        raise CardError("card.allowed_query_types must be contract/interface only")
    if card.get("max_depth") != 1 or card.get("max_records") != 2:
        raise CardError("card requires max_depth=1 and max_records=2")
    if card.get("transitive_traversal") is not False or card.get("no_transitive") is not True:
        raise CardError("card must disable transitive traversal")
    if card.get("source_body_access") is not False:
        raise CardError("card.source_body_access must be false")

    excluded_paths = set(str(item) for item in require_list(card.get("excluded_paths"), "card.excluded_paths"))
    if not REQUIRED_EXCLUDED_PATHS <= excluded_paths:
        raise CardError("card.excluded_paths must explicitly deny runtime/integration-sensitive core modules")
    excluded_interfaces = set(str(item) for item in require_list(card.get("excluded_interfaces"), "card.excluded_interfaces"))
    if not DENIED_SYMBOLS <= excluded_interfaces:
        raise CardError("card.excluded_interfaces must explicitly deny sensitive interfaces")
    excluded_operations = set(str(item) for item in require_list(card.get("excluded_operations"), "card.excluded_operations"))
    if not REQUIRED_EXCLUDED_OPERATIONS <= excluded_operations:
        raise CardError("card.excluded_operations must explicitly deny runtime/integration operations")

    resolved_dependency = dependency_root.resolve(strict=True)
    records = require_list(card.get("records"), "card.records")
    if len(records) > 2:
        raise CardError("card.records exceeds max_records=2")
    seen_paths: set[str] = set()
    for record_index, record in enumerate(records):
        if not isinstance(record, dict):
            raise CardError(f"card.records[{record_index}]: expected object")
        if record.get("kind") != "interface":
            raise CardError(f"card.records[{record_index}]: only interface records are allowed")
        if "dependencies" in record or "transitive" in record:
            raise CardError(f"card.records[{record_index}]: transitive dependency fields are denied")
        if Path(str(record.get("owner_root") or "")).resolve(strict=True) != resolved_dependency:
            raise CardError(f"card.records[{record_index}]: owner_root must match cc-daemons adapter root exactly")
        record_exclusions = set(str(item) for item in require_list(record.get("excluded_operations", []), f"card.records[{record_index}].excluded_operations"))
        if not record_exclusions:
            raise CardError(f"card.records[{record_index}]: excluded_operations must be non-empty")
        for path_index, ref in enumerate(require_list(record.get("paths"), f"card.records[{record_index}].paths")):
            if not isinstance(ref, dict):
                raise CardError(f"card.records[{record_index}].paths[{path_index}]: expected object")
            rel_path = str(ref.get("path") or "")
            if rel_path in DENIED_CORE_PATHS or rel_path in excluded_paths:
                raise CardError(f"{rel_path}: denied runtime/integration-sensitive path")
            if rel_path not in ALLOWED_CORE_PATHS:
                raise CardError(f"{rel_path}: only bounded shared core interface paths are allowed")
            source_path = ensure_dependency_relative(resolved_dependency, rel_path, label=f"card.records[{record_index}].paths[{path_index}]")
            seen_paths.add(rel_path)
            tree = parse_ast(source_path)
            symbols = [str(symbol) for symbol in require_list(ref.get("symbols"), f"card.records[{record_index}].paths[{path_index}].symbols")]
            if not symbols:
                raise CardError(f"{rel_path}: at least one symbol is required")
            if DENIED_SYMBOLS & set(symbols):
                raise CardError(f"{rel_path}: sensitive symbols are denied")
            for symbol in symbols:
                if not symbol_exists(tree, symbol):
                    raise CardError(f"{rel_path}: missing symbol {symbol}")
            source_text = source_path.read_text(encoding="utf-8")
            for literal in require_list(ref.get("literals", []), f"card.records[{record_index}].paths[{path_index}].literals"):
                if str(literal) not in source_text:
                    raise CardError(f"{rel_path}: literal evidence not found: {literal}")
    return [
        f"dependency_root={resolved_dependency}",
        f"consumer_projects={','.join(sorted(consumers))}",
        f"records={len(records)}",
        f"interface_paths={','.join(sorted(seen_paths))}",
    ]


def validate(path: Path | None = None) -> list[str]:
    card_path = DEFAULT_CARD if path is None else path
    if card_path.resolve() != DEFAULT_CARD.resolve():
        raise CardError("card path must match the checked-in shared-core dependency card")
    return validate_card(load_json(card_path), _dependency_root())


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(list(argv) if argv is not None else None)
    try:
        summary = validate()
    except CardError as exc:
        print(f"ERROR: {exc}")
        return 1
    print("cc-daemons shared-core dependency card OK")
    for line in summary:
        print(f"- {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
