#!/usr/bin/env python3
"""Local deterministic developer navigation for NMBot sources.

This command is read-only and stdlib-only. It builds a fresh in-memory registry
from active retrieval-manifest paths, the stage map, Python AST definitions and
documentation anchors. Results are navigation candidates for grep/read, not
evidence and not production proof.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCHEMA = "nmbot.retrieval_sources.v1"
STAGE_SCHEMA = "nmbot.stage_map.v1"
OUTPUT_SCHEMA = "nmbot.navigation.v1"
DEFAULT_MANIFEST = Path("config/nmbot_retrieval_sources.json")
STAGE_MAP_PATH = Path("config/nmbot_stage_map.json")
CONTEXT_PACKS_PATH = Path("docs/NMBOT_CONTEXT_PACKS.md")
MAX_RESULTS = 3
RAW_FALLBACK_LIMIT = 12
DOC_WORDS = {
    "doc", "docs", "documentation", "document", "contract", "contracts",
    "ux", "runbook", "context", "context-pack", "contextpack", "anchor",
    "док", "доки", "документация", "контракт", "контракты", "ранбук",
    "архитектура", "архитектуры", "якорь", "пакет", "контекст",
}
UNRELATED_MARKERS = {
    "погода", "биткоин", "такси", "музыка", "борщ", "рецепт", "курс",
    "weather", "bitcoin", "taxi", "music", "recipe",
}
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DIAGNOSTIC_CODE_RE = re.compile(r"^(?=.{4,80}$)[a-z][a-z0-9]*_[a-z0-9_]*(?::[a-z0-9_]+)?$")
DIAGNOSTIC_ROLE_PRIORITY = {"emit": 0, "declare": 1, "reference": 2}
STAGE_TOKEN_RE = re.compile(r"\b(?:v2|jivo)\.[A-Za-z0-9_.-]+\b")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")


class NavigationError(ValueError):
    """Human-readable navigation validation/runtime error."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_digest(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _repo_path(path_text: str, *, root: Path) -> Path:
    path = Path(path_text)
    if path.is_absolute() or ".." in path.parts:
        raise NavigationError(f"path must be relative and stay inside repo: {path_text}")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise NavigationError(f"path escapes repo: {path_text}") from exc
    return resolved


def _load_json(path: Path, *, root: Path) -> Any:
    target = _repo_path(path.as_posix(), root=root)
    if not target.is_file():
        raise NavigationError(f"file not found: {path.as_posix()}")
    return json.loads(target.read_text(encoding="utf-8"))


def load_active_manifest(manifest_path: Path = DEFAULT_MANIFEST, *, root: Path = ROOT) -> list[dict[str, Any]]:
    data = _load_json(manifest_path, root=root)
    if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA:
        raise NavigationError(f"manifest schema must be {MANIFEST_SCHEMA}")
    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise NavigationError("manifest sources must be a non-empty list")
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_sources:
        if not isinstance(raw, dict):
            raise NavigationError("each manifest source must be an object")
        item = dict(raw)
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            raise NavigationError("manifest source path must be a non-empty string")
        if path in seen:
            raise NavigationError(f"duplicate manifest source path: {path}")
        seen.add(path)
        if item.get("status") != "active":
            continue
        target = _repo_path(path, root=root)
        if not target.is_file():
            raise NavigationError(f"active manifest path missing: {path}")
        for key in ("module", "type", "owner", "status"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise NavigationError(f"source {path} missing non-empty {key}")
        sources.append(item)
    if not sources:
        raise NavigationError("manifest has no active sources")
    return sources


def _active_path_map(sources: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["path"]: item for item in sources}


def load_stage_map(*, root: Path = ROOT) -> dict[str, Any]:
    data = _load_json(STAGE_MAP_PATH, root=root)
    if not isinstance(data, dict) or data.get("schema") != STAGE_SCHEMA:
        raise NavigationError(f"stage map schema must be {STAGE_SCHEMA}")
    if not isinstance(data.get("stages"), dict) or not isinstance(data.get("paths"), dict):
        raise NavigationError("stage map must contain stages and paths objects")
    return data


def _line_for_text(path: str, needle: str, *, root: Path) -> int:
    lines = _repo_path(path, root=root).read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines, start=1):
        if needle in line:
            return index
    raise NavigationError(f"anchor not found in {path}: {needle}")


def _line_count(path: str, *, root: Path) -> int:
    return max(1, len(_repo_path(path, root=root).read_text(encoding="utf-8").splitlines()))


def _ast_symbol_span(path: str, symbol: str, *, root: Path) -> tuple[int, int, str]:
    if not IDENT_RE.fullmatch(symbol):
        raise NavigationError(f"source_symbol must be a Python identifier in {path}: {symbol}")
    text = _repo_path(path, root=root).read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as exc:
        raise NavigationError(f"AST parse failed for stage source {path}: line {exc.lineno}") from exc
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol
    ]
    if not matches:
        raise NavigationError(f"source_symbol_missing:{path}:{symbol}")
    matches.sort(key=lambda node: (int(getattr(node, "lineno", 1)), int(getattr(node, "end_lineno", getattr(node, "lineno", 1)))))
    node = matches[0]
    start = int(getattr(node, "lineno", 1))
    end = int(getattr(node, "end_lineno", start))
    return start, end, node.__class__.__name__


def _expand_path_id(path_id: str, stage_map: dict[str, Any], *, stack: tuple[str, ...] = ()) -> list[str]:
    paths = stage_map["paths"]
    path = paths.get(path_id)
    if not isinstance(path, dict):
        raise NavigationError(f"unknown path_id: {path_id}")
    if path_id in stack:
        raise NavigationError(f"stage path cycle: {' -> '.join(stack + (path_id,))}")
    stage_ids: list[str] = []
    parent = path.get("extends")
    if isinstance(parent, str) and parent:
        stage_ids.extend(_expand_path_id(parent, stage_map, stack=stack + (path_id,)))
    for stage_id in path.get("stage_ids", []):
        if not isinstance(stage_id, str) or stage_id not in stage_map["stages"]:
            raise NavigationError(f"path {path_id} references unknown stage_id: {stage_id}")
        stage_ids.append(stage_id)
    if len(stage_ids) != len(set(stage_ids)):
        raise NavigationError(f"duplicate stage_ids in path {path_id}")
    return stage_ids


def _stage_records(sources: list[dict[str, Any]], *, root: Path) -> list[dict[str, Any]]:
    active = _active_path_map(sources)
    stage_map = load_stage_map(root=root)
    records: list[dict[str, Any]] = []
    for path_id in sorted(stage_map["paths"]):
        _expand_path_id(path_id, stage_map)
    for stage_id, stage in sorted(stage_map["stages"].items()):
        if not isinstance(stage, dict):
            raise NavigationError(f"stage {stage_id} must be an object")
        stage_source_path = stage.get("source")
        if not isinstance(stage_source_path, str) or not stage_source_path:
            raise NavigationError(f"stage {stage_id} source must be an active manifest path")
        if stage_source_path not in active:
            raise NavigationError(f"stage {stage_id} source is not active manifest path: {stage_source_path}")
        for field in ("source", "doc", "test", "prompt"):
            path = stage.get(field)
            if not isinstance(path, str) or not path:
                continue
            if path not in active:
                raise NavigationError(f"stage {stage_id} {field} is not active manifest path: {path}")
            target = _repo_path(path, root=root)
            source_symbol = stage.get("source_symbol")
            start_line = 1
            end_line = _line_count(path, root=root)
            symbol_type = None
            if isinstance(source_symbol, str) and source_symbol.strip():
                if field == "source":
                    start_line, end_line, symbol_type = _ast_symbol_span(path, source_symbol.strip(), root=root)
                else:
                    source_symbol = source_symbol.strip()
            elif field == "source":
                source_symbol = None
            records.append({
                "kind": "stage",
                "stage_id": stage_id,
                "stage_field": field,
                "path": path,
                "stage_source_path": stage_source_path,
                "start_line": start_line,
                "end_line": end_line,
                "owner": stage.get("owner") or active[path].get("owner"),
                "purpose": stage.get("purpose"),
                "payload_stage": stage.get("payload_stage"),
                "source_symbol": source_symbol,
                "source_symbol_type": symbol_type,
                "source_hash": sha256_text(target.read_text(encoding="utf-8")),
                "terms": f"{stage_id} {field} {path} {stage.get('purpose','')} {stage.get('payload_stage','')} {stage.get('owner','')}",
            })
    return records


def _find_related_test(symbol: str, source_path: str, active: dict[str, dict[str, Any]], *, root: Path) -> str | None:
    stem = Path(source_path).stem
    candidates = [p for p, meta in active.items() if meta.get("type") == "test" and p.endswith(".py")]
    candidates.sort(key=lambda p: (0 if stem in Path(p).stem else 1, p))
    for path in candidates:
        text = _repo_path(path, root=root).read_text(encoding="utf-8")
        if symbol in text or stem in Path(path).stem:
            return path
    return None


def _symbol_records(sources: list[dict[str, Any]], *, root: Path) -> list[dict[str, Any]]:
    active = _active_path_map(sources)
    records: list[dict[str, Any]] = []
    for item in sources:
        path = item["path"]
        if item.get("type") != "python" or not path.endswith(".py"):
            continue
        text = _repo_path(path, root=root).read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            raise NavigationError(f"AST parse failed for active Python source {path}: line {exc.lineno}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            start = int(getattr(node, "lineno", 1))
            end = int(getattr(node, "end_lineno", start))
            symbol = str(node.name)
            records.append({
                "kind": "symbol",
                "symbol": symbol,
                "symbol_type": node.__class__.__name__,
                "path": path,
                "start_line": start,
                "end_line": end,
                "owner": item.get("owner"),
                "related_test": _find_related_test(symbol, path, active, root=root),
                "source_hash": sha256_text(text),
                "terms": f"{symbol} {path} {item.get('module','')} {item.get('owner','')}",
            })
    return records


def _is_docstring_expr(node: ast.AST, parent: ast.AST | None) -> bool:
    if not isinstance(node, ast.Expr) or not isinstance(getattr(node, "value", None), ast.Constant):
        return False
    if not isinstance(getattr(node.value, "value", None), str):
        return False
    body = getattr(parent, "body", None)
    return isinstance(body, list) and bool(body) and body[0] is node


def _inside_docstring_expr(node: ast.AST, stack: list[ast.AST]) -> bool:
    parent = stack[-1] if stack else None
    grandparent = stack[-2] if len(stack) >= 2 else None
    return bool(parent is not None and _is_docstring_expr(parent, grandparent))


def _canonical_diagnostic_code(value: str) -> str | None:
    candidate = value[:-1] if value.endswith(":") else value
    return candidate if DIAGNOSTIC_CODE_RE.fullmatch(candidate) else None


def _diagnostic_literal_role(stack: list[ast.AST]) -> str:
    """Classify an exact code occurrence without guessing runtime causality."""
    for ancestor in reversed(stack):
        if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            break
        if isinstance(ancestor, ast.Call):
            func = ancestor.func
            name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else "")
            if name in {"append", "add", "extend"}:
                return "emit"
        if isinstance(ancestor, ast.Dict):
            return "declare"
    return "reference"


def _diagnostic_code_records(sources: list[dict[str, Any]], *, root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in sources:
        path = item["path"]
        if item.get("type") not in {"python", "test"} or not path.endswith(".py"):
            continue
        text = _repo_path(path, root=root).read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as exc:
            raise NavigationError(f"AST parse failed for active diagnostic source {path}: line {exc.lineno}") from exc

        stack: list[ast.AST] = []

        def visit(node: ast.AST) -> None:
            parent = stack[-1] if stack else None
            if _is_docstring_expr(node, parent):
                return
            if not _inside_docstring_expr(node, stack):
                value: str | None = None
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    value = node.value
                code = _canonical_diagnostic_code(value) if value else None
                if code:
                    symbol_node = next((ancestor for ancestor in reversed(stack) if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
                    if symbol_node is not None:
                        records.append({
                            "kind": "diagnostic_code",
                            "code": code,
                            "diagnostic_role": _diagnostic_literal_role(stack),
                            "symbol": str(symbol_node.name),
                            "symbol_type": symbol_node.__class__.__name__,
                            "path": path,
                            "start_line": int(getattr(symbol_node, "lineno", 1)),
                            "end_line": int(getattr(symbol_node, "end_lineno", getattr(symbol_node, "lineno", 1))),
                            "owner": item.get("owner"),
                            "source_role": "test" if item.get("type") == "test" else "source",
                            "source_hash": sha256_text(text),
                            "terms": f"diagnostic_code {code} {symbol_node.name} {path} {item.get('module','')} {item.get('owner','')} {item.get('type','')}",
                        })
            stack.append(node)
            for child in ast.iter_child_nodes(node):
                visit(child)
            stack.pop()

        visit(tree)

    dedup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        key = (record["code"], record["path"], record["symbol"])
        current = dedup.get(key)
        if current is None or DIAGNOSTIC_ROLE_PRIORITY[record["diagnostic_role"]] < DIAGNOSTIC_ROLE_PRIORITY[current["diagnostic_role"]]:
            dedup[key] = record
    return [dedup[key] for key in sorted(dedup)]


def _extract_context_pack_anchors(active: dict[str, dict[str, Any]], *, root: Path) -> list[dict[str, Any]]:
    path = _repo_path(CONTEXT_PACKS_PATH.as_posix(), root=root)
    if not path.is_file() or CONTEXT_PACKS_PATH.as_posix() not in active:
        return []
    text = path.read_text(encoding="utf-8")
    try:
        block = text.split("<!-- NMBOT_CONTEXT_PACKS_JSON_START -->", 1)[1].split("<!-- NMBOT_CONTEXT_PACKS_JSON_END -->", 1)[0]
        raw = re.search(r"```json\s*(.*?)\s*```", block, re.DOTALL).group(1)  # type: ignore[union-attr]
        data = json.loads(raw)
    except (IndexError, AttributeError, json.JSONDecodeError) as exc:
        raise NavigationError("context-pack JSON anchors cannot be parsed") from exc
    records: list[dict[str, Any]] = []
    for pack in data.get("packs", []):
        if not isinstance(pack, dict):
            raise NavigationError("context-pack entry must be an object")
        for anchor in pack.get("read_first_anchors", []):
            if not isinstance(anchor, dict):
                raise NavigationError("context-pack read_first_anchors entries must be objects")
            target_path = anchor.get("path")
            anchor_text = anchor.get("anchor")
            if not isinstance(target_path, str) or not isinstance(anchor_text, str) or target_path not in active:
                continue
            line = _line_for_text(target_path, anchor_text, root=root)
            target_text = _repo_path(target_path, root=root).read_text(encoding="utf-8")
            records.append({
                "kind": "doc_anchor",
                "path": target_path,
                "start_line": line,
                "end_line": line,
                "anchor": anchor_text,
                "anchor_type": "context_pack_read_first",
                "pack_id": pack.get("id"),
                "owner": active[target_path].get("owner"),
                "source_hash": sha256_text(target_text),
                "terms": f"{pack.get('id','')} {pack.get('title','')} {anchor_text} {target_path}",
            })
    return records


def _doc_records(sources: list[dict[str, Any]], *, root: Path) -> list[dict[str, Any]]:
    active = _active_path_map(sources)
    records = _extract_context_pack_anchors(active, root=root)
    for item in sources:
        if item.get("type") not in {"doc", "prompt", "json", "text"}:
            continue
        path = item["path"]
        text = _repo_path(path, root=root).read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if HEADING_RE.match(stripped):
                anchor = stripped
            elif item.get("type") == "json" and '"schema"' in stripped:
                anchor = stripped.rstrip(",")
            else:
                continue
            records.append({
                "kind": "doc_anchor",
                "path": path,
                "start_line": line_no,
                "end_line": line_no,
                "anchor": anchor,
                "anchor_type": "heading_or_schema",
                "pack_id": None,
                "owner": item.get("owner"),
                "source_hash": sha256_text(text),
                "terms": f"{anchor} {path} {item.get('module','')} {item.get('owner','')}",
            })
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        dedup[(record["path"], record["anchor"])] = record
    return list(dedup.values())


def build_registry(*, root: Path = ROOT, manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    sources = load_active_manifest(manifest_path, root=root)
    records = _stage_records(sources, root=root) + _symbol_records(sources, root=root) + _diagnostic_code_records(sources, root=root) + _doc_records(sources, root=root)
    for record in records:
        record["id"] = _json_digest({k: v for k, v in record.items() if k != "id"})[:24]
    validate_registry(records, root=root, active_paths=set(_active_path_map(sources)))
    fingerprint = _json_digest({"manifest": manifest_path.as_posix(), "records": records})
    return {"records": records, "fingerprint": fingerprint, "active_paths": set(_active_path_map(sources)), "sources": sources}


def validate_registry(records: list[dict[str, Any]], *, root: Path = ROOT, active_paths: set[str] | None = None) -> None:
    errors: list[str] = []
    if active_paths is None:
        active_paths = set(_active_path_map(load_active_manifest(root=root)))
    stage_map = load_stage_map(root=root)
    by_path_text: dict[str, str] = {}
    by_path_ast: dict[str, ast.AST] = {}
    for record in records:
        path = record.get("path")
        if not isinstance(path, str) or path not in active_paths:
            errors.append(f"path_not_active:{path}")
            continue
        target = _repo_path(path, root=root)
        if not target.is_file():
            errors.append(f"path_missing:{path}")
            continue
        text = by_path_text.setdefault(path, target.read_text(encoding="utf-8"))
        if record.get("source_hash") != sha256_text(text):
            errors.append(f"hash_mismatch:{record.get('kind')}:{path}:{record.get('start_line')}")
        kind = record.get("kind")
        if kind == "stage":
            stage = stage_map["stages"].get(record.get("stage_id"), {})
            if not isinstance(stage, dict) or stage.get(record.get("stage_field")) != path:
                errors.append(f"stage_resolve_failed:{record.get('stage_id')}:{record.get('stage_field')}:{path}")
            source_symbol = stage.get("source_symbol")
            if record.get("stage_field") == "source" and isinstance(source_symbol, str) and source_symbol.strip():
                if path not in by_path_ast:
                    try:
                        by_path_ast[path] = ast.parse(text, filename=path)
                    except SyntaxError as exc:
                        errors.append(f"ast_parse_failed:{path}:{exc.lineno}")
                        continue
                found = any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and node.name == source_symbol.strip()
                    and int(getattr(node, "lineno", -1)) == int(record.get("start_line") or -1)
                    and int(getattr(node, "end_lineno", getattr(node, "lineno", -1))) == int(record.get("end_line") or -1)
                    for node in ast.walk(by_path_ast[path])
                )
                if not found:
                    errors.append(f"stage_source_symbol_drift:{path}:{source_symbol}:{record.get('start_line')}-{record.get('end_line')}")
            if record.get("stage_field") == "test" and isinstance(source_symbol, str) and source_symbol.strip():
                if _focused_test_span(text, source_symbol.strip(), filename=path) is None:
                    errors.append(f"stage_test_symbol_missing:{path}:{source_symbol}")
        elif kind == "symbol":
            if path not in by_path_ast:
                try:
                    by_path_ast[path] = ast.parse(text, filename=path)
                except SyntaxError as exc:
                    errors.append(f"ast_parse_failed:{path}:{exc.lineno}")
                    continue
            found = any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == record.get("symbol")
                and int(getattr(node, "lineno", -1)) == int(record.get("start_line") or -1)
                and int(getattr(node, "end_lineno", getattr(node, "lineno", -1))) == int(record.get("end_line") or -1)
                for node in ast.walk(by_path_ast[path])
            )
            if not found:
                errors.append(f"symbol_missing:{path}:{record.get('symbol')}:{record.get('start_line')}")
        elif kind == "diagnostic_code":
            code = record.get("code")
            symbol = record.get("symbol")
            role = record.get("diagnostic_role")
            if not (isinstance(code, str) and DIAGNOSTIC_CODE_RE.fullmatch(code)):
                errors.append(f"diagnostic_code_invalid:{path}:{code}")
                continue
            if role not in DIAGNOSTIC_ROLE_PRIORITY:
                errors.append(f"diagnostic_role_invalid:{path}:{symbol}:{role}")
                continue
            if path not in by_path_ast:
                try:
                    by_path_ast[path] = ast.parse(text, filename=path)
                except SyntaxError as exc:
                    errors.append(f"ast_parse_failed:{path}:{exc.lineno}")
                    continue
            found = False
            for node in ast.walk(by_path_ast[path]):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name != symbol:
                    continue
                if int(getattr(node, "lineno", -1)) != int(record.get("start_line") or -1):
                    continue
                if int(getattr(node, "end_lineno", getattr(node, "lineno", -1))) != int(record.get("end_line") or -1):
                    continue
                found = _symbol_span_contains_diagnostic_code(node, code)
                break
            if not found:
                errors.append(f"diagnostic_code_drift:{path}:{symbol}:{code}:{record.get('start_line')}-{record.get('end_line')}")
        elif kind == "doc_anchor":
            anchor = record.get("anchor")
            start = int(record.get("start_line") or 0)
            lines = text.splitlines()
            line_ok = isinstance(anchor, str) and 1 <= start <= len(lines) and anchor in lines[start - 1]
            approved = bool(record.get("pack_id")) or bool(isinstance(anchor, str) and (HEADING_RE.match(anchor) or '"schema"' in anchor))
            if not (line_ok and approved):
                errors.append(f"anchor_missing:{path}:{anchor}")
        else:
            errors.append(f"bad_kind:{kind}")
        target_spec = _target_spec(record)
        if isinstance(target_spec, dict):
            owner_path = target_spec.get("owner_path")
            if not isinstance(owner_path, str) or owner_path not in active_paths:
                errors.append(f"target_spec_owner_path_not_active:{record.get('kind')}:{owner_path}")
    if errors:
        raise NavigationError("navigation registry drift: " + "; ".join(errors[:5]))


def _symbol_span_contains_diagnostic_code(symbol_node: ast.FunctionDef | ast.AsyncFunctionDef, code: str) -> bool:
    stack: list[ast.AST] = []

    def visit(node: ast.AST) -> bool:
        parent = stack[-1] if stack else None
        if _is_docstring_expr(node, parent):
            return False
        if _inside_docstring_expr(node, stack):
            return False
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _canonical_diagnostic_code(node.value) == code:
                return True
        stack.append(node)
        try:
            return any(visit(child) for child in ast.iter_child_nodes(node))
        finally:
            stack.pop()

    return visit(symbol_node)


def _focused_test_span(text: str, symbol: str, *, filename: str) -> tuple[int, int] | None:
    try:
        tree = ast.parse(text, filename=filename)
    except SyntaxError:
        tree = None
    spans: list[tuple[int, int]] = []
    if tree is not None:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            name = str(getattr(node, "name", ""))
            if not (name.startswith("test_") or name.startswith("Test")):
                continue
            start = int(getattr(node, "lineno", 1))
            end = int(getattr(node, "end_lineno", start))
            if _test_body_references_symbol(node, symbol):
                spans.append((start, end))
    if spans:
        return min(spans, key=lambda span: (span[1] - span[0], span[0]))
    return None


def _test_body_references_symbol(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, symbol: str) -> bool:
    for stmt in node.body:
        for child in ast.walk(stmt):
            if isinstance(child, ast.Name) and child.id == symbol:
                return True
            if isinstance(child, ast.Attribute) and child.attr == symbol:
                return True
    return False


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-zА-Яа-я0-9_./:-]{2,}", text.lower())


def _identifier_tokens(text: str) -> list[str]:
    return re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", text)


def _score(query: str, record: dict[str, Any]) -> tuple[int, int]:
    q = set(_tokens(query))
    terms = set(_tokens(" ".join(str(record.get(key, "")) for key in ("terms", "path", "symbol", "stage_id", "anchor"))))
    overlap = len(q & terms)
    exact = 0
    qlow = query.lower()
    for key in ("symbol", "stage_id", "anchor", "path"):
        value = str(record.get(key) or "").lower()
        if value and value in qlow:
            exact += 5
    return exact + overlap, exact


def _result(record: dict[str, Any], *, fallback: bool = False, score: int | None = None) -> dict[str, Any]:
    result = {
        "kind": record["kind"],
        "path": record["path"],
        "start_line": record["start_line"],
        "end_line": record["end_line"],
    }
    target_spec = _target_spec(record)
    if target_spec:
        result["target_spec"] = target_spec
    for key in ("stage_id", "stage_field", "source_symbol", "source_symbol_type", "symbol", "symbol_type", "related_test", "anchor", "anchor_type", "pack_id", "owner", "code", "source_role", "diagnostic_role"):
        if record.get(key):
            result[key] = record[key]
    if fallback or record.get("kind") == "diagnostic_code":
        result["candidate_only"] = True
    if score is not None:
        result["score"] = score
    return result


def _target_spec(record: dict[str, Any]) -> dict[str, str] | None:
    kind = record.get("kind")
    path = record.get("path")
    if not isinstance(path, str) or not path:
        return None
    if kind == "stage":
        stage_id = record.get("stage_id")
        owner = record.get("owner")
        owner_path = record.get("stage_source_path")
        if all(isinstance(value, str) and value for value in (stage_id, owner, owner_path)):
            return {"target_kind": "stage", "target": stage_id, "target_owner": owner, "owner_path": owner_path}
        return None
    if kind == "symbol":
        symbol = record.get("symbol")
        if isinstance(symbol, str) and symbol:
            return {"target_kind": "symbol", "target": symbol, "target_owner": path, "owner_path": path}
        return None
    if kind == "diagnostic_code":
        symbol = record.get("symbol")
        if isinstance(symbol, str) and symbol:
            return {"target_kind": "symbol", "target": symbol, "target_owner": path, "owner_path": path}
        return None
    if kind == "doc_anchor":
        anchor = record.get("anchor")
        if isinstance(anchor, str) and anchor:
            return {"target_kind": "docs", "target": anchor, "target_owner": path, "owner_path": path}
    return None


def _distinct_path_results(records: Iterable[dict[str, Any]], *, fallback: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        path = record["path"]
        if path in seen:
            continue
        seen.add(path)
        out.append(_result(record, fallback=fallback))
        if len(out) >= MAX_RESULTS:
            break
    return out


def _stage_dispatch(query: str, registry: dict[str, Any], stage_map: dict[str, Any]) -> dict[str, Any] | None:
    stripped = query.strip()
    ids = [stripped] if stripped else []
    ids.extend(STAGE_TOKEN_RE.findall(query))
    chosen = next((item for item in ids if item in stage_map["paths"] or item in stage_map["stages"]), None)
    if not chosen:
        return None
    records = registry["records"]
    if chosen in stage_map["paths"]:
        stage_ids = _expand_path_id(chosen, stage_map)
        ranked = [r for sid in stage_ids for r in records if r.get("kind") == "stage" and r.get("stage_id") == sid]
        reason = f"exact path_id: {chosen}"
    else:
        ranked = [r for r in records if r.get("kind") == "stage" and r.get("stage_id") == chosen]
        reason = f"exact stage_id: {chosen}"
    return _base_report(query, registry, route="stage", reason=reason, fallback=False, results=_distinct_path_results(ranked), next_action="grep/read returned stage-map paths before making claims")


def _symbol_dispatch(query: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    symbols = {r["symbol"] for r in registry["records"] if r.get("kind") == "symbol"}
    chosen = next((token for token in _identifier_tokens(query) if IDENT_RE.fullmatch(token) and token in symbols), None)
    if not chosen:
        return None
    matches = [r for r in registry["records"] if r.get("kind") == "symbol" and r.get("symbol") == chosen]
    matches.sort(key=lambda r: (r["path"], r["start_line"], r["end_line"]))
    return _base_report(query, registry, route="ast", reason=f"exact Python identifier: {chosen}", fallback=False, results=[_result(r) for r in matches[:MAX_RESULTS]], next_action="grep/read exact symbol range and optional related_test before making claims")


def _diagnostic_dispatch(query: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    codes = {r["code"] for r in registry["records"] if r.get("kind") == "diagnostic_code"}
    chosen = None
    for token in _tokens(query):
        if not DIAGNOSTIC_CODE_RE.fullmatch(token):
            continue
        if token in codes:
            chosen = token
            break
        base = token.split(":", 1)[0]
        if base in codes:
            chosen = base
            break
    if not chosen:
        return None
    matches = [r for r in registry["records"] if r.get("kind") == "diagnostic_code" and r.get("code") == chosen]
    matches.sort(key=lambda r: (
        0 if r.get("source_role") == "source" else 1,
        DIAGNOSTIC_ROLE_PRIORITY.get(str(r.get("diagnostic_role")), 9),
        int(r["end_line"]) - int(r["start_line"]),
        r["path"],
        r["symbol"],
    ))
    return _base_report(query, registry, route="diagnostic", reason=f"exact diagnostic code: {chosen}", fallback=False, results=[_result(r) for r in matches[:MAX_RESULTS]], next_action="candidate-only: strict context-gate may read the returned symbol target before making claims")


def _docs_dispatch(query: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    if not (set(_tokens(query)) & DOC_WORDS):
        return None
    docs = [r for r in registry["records"] if r.get("kind") == "doc_anchor"]
    scored = [(sum(_score(query, r)), r) for r in docs]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda sr: (-sr[0], sr[1]["path"], sr[1]["start_line"], sr[1].get("anchor", "")))
    results = [_result(r, score=s) for s, r in scored[:MAX_RESULTS]]
    return _base_report(query, registry, route="docs", reason="explicit documentation/contract/context/runbook wording", fallback=False, results=results, next_action="grep/read returned anchors before making claims")


def _normalize_active_owner_path(owner_path: str, *, root: Path, active_paths: set[str]) -> str:
    resolved = _repo_path(str(owner_path).strip(), root=root)
    normalized = resolved.relative_to(root.resolve()).as_posix()
    if normalized not in active_paths:
        raise NavigationError(f"owner_path is not active manifest path: {normalized}")
    return normalized


def _owner_doc_score(query: str, record: dict[str, Any], owner_text: str) -> int:
    score = sum(_score(query, record))
    q = set(_tokens(query))
    if q:
        score += len(q & set(_tokens(owner_text)))
    return score


def resolve_doc_anchor(query: str, owner_path: str, *, root: Path = ROOT, manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    """Resolve documentation anchors inside one declared active owner path only."""
    registry = build_registry(root=root, manifest_path=manifest_path)
    normalized_owner = _normalize_active_owner_path(owner_path, root=root, active_paths=registry["active_paths"])
    owner_text = _repo_path(normalized_owner, root=root).read_text(encoding="utf-8")
    docs = [
        r for r in registry["records"]
        if r.get("kind") == "doc_anchor" and r.get("path") == normalized_owner
    ]
    scored = [(_owner_doc_score(query, r, owner_text), r) for r in docs]
    scored = [(s, r) for s, r in scored if s > 0]
    if not scored:
        raise NavigationError(f"docs anchor not found in owner_path: {normalized_owner}")
    scored.sort(key=lambda sr: (-sr[0], sr[1]["path"], sr[1]["start_line"], sr[1].get("anchor", "")))
    results = [_result(r, score=s) for s, r in scored[:MAX_RESULTS]]
    return _base_report(query, registry, route="docs", reason="owner-scoped documentation anchor lookup", fallback=False, results=results, next_action="grep/read returned owner_path anchors before making claims")


def _open_fallback_fts(records: list[dict[str, Any]]) -> tuple[sqlite3.Connection, dict[int, dict[str, Any]]]:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE nav USING fts5(terms, path, kind, tokenize='unicode61')")
    by_rowid: dict[int, dict[str, Any]] = {}
    for rowid, record in enumerate(records, start=1):
        conn.execute("INSERT INTO nav(rowid, terms, path, kind) VALUES (?, ?, ?, ?)", (rowid, str(record.get("terms", "")), record["path"], record["kind"]))
        by_rowid[rowid] = record
    conn.commit()
    return conn, by_rowid


def _quote_fts(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _fallback_dispatch(query: str, registry: dict[str, Any]) -> dict[str, Any]:
    tokens = [token for token in _tokens(query) if token not in UNRELATED_MARKERS]
    if not tokens or set(_tokens(query)) & UNRELATED_MARKERS:
        return _base_report(query, registry, route="mixed", reason="no safe deterministic route and no useful FTS tokens", fallback=True, results=[], next_action="abstain_then_use_docs_stage_map_then_grep_read")
    fts_query = " OR ".join(_quote_fts(token) for token in dict.fromkeys(tokens))
    fallback_records = [record for record in registry["records"] if record.get("kind") != "diagnostic_code"]
    conn, by_rowid = _open_fallback_fts(fallback_records)
    try:
        rows = conn.execute("SELECT rowid, bm25(nav, 1.0, 2.0, 1.0) AS rank FROM nav WHERE nav MATCH ? ORDER BY rank ASC, path ASC, rowid ASC LIMIT ?", (fts_query, RAW_FALLBACK_LIMIT)).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    selected: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for rowid, _rank in rows:
        rec = by_rowid[int(rowid)]
        if rec["path"] in seen_paths:
            continue
        seen_paths.add(rec["path"])
        selected.append(_result(rec, fallback=True))
        if len(selected) >= MAX_RESULTS:
            break
    return _base_report(query, registry, route="mixed", reason="bounded mixed FTS fallback; candidate-only", fallback=True, results=selected, next_action="select_then_grep_read" if selected else "abstain_then_use_docs_stage_map_then_grep_read")


def _base_report(query: str, registry: dict[str, Any], *, route: str, reason: str, fallback: bool, results: list[dict[str, Any]], next_action: str) -> dict[str, Any]:
    return {
        "schema": OUTPUT_SCHEMA,
        "query": query,
        "route": route,
        "reason": reason,
        "abstain": not results,
        "fallback": fallback,
        "results": results[:MAX_RESULTS],
        "next_action": next_action,
        "registry_fingerprint": registry["fingerprint"],
        "local_read_only": True,
        "candidates_are_not_evidence": True,
        "production_proof": False,
    }


def navigate(query: str, *, root: Path = ROOT, manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    registry = build_registry(root=root, manifest_path=manifest_path)
    stage_map = load_stage_map(root=root)
    for dispatcher in (_stage_dispatch,):
        report = dispatcher(query, registry, stage_map)
        if report is not None:
            return report
    report = _symbol_dispatch(query, registry)
    if report is not None:
        return report
    report = _diagnostic_dispatch(query, registry)
    if report is not None:
        return report
    report = _docs_dispatch(query, registry)
    if report is not None:
        return report
    return _fallback_dispatch(query, registry)


def render_human(report: dict[str, Any]) -> str:
    flags = []
    if report.get("fallback"):
        flags.append("fallback/candidate-only")
    if report.get("abstain"):
        flags.append("abstain")
    suffix = f" ({', '.join(flags)})" if flags else ""
    lines = [f"NMBot navigate: route={report['route']}{suffix}", f"Reason: {report['reason']}"]
    for index, item in enumerate(report.get("results", []), start=1):
        label = item.get("symbol") or item.get("stage_id") or item.get("anchor") or item["kind"]
        lines.append(f"{index}. {item['path']}:{item['start_line']}-{item['end_line']} [{item['kind']}] {label}")
        if item.get("related_test"):
            lines.append(f"   related_test: {item['related_test']}")
    lines.append(f"Next: {report['next_action']}")
    lines.append("Boundary: local developer navigation only; candidates are not evidence or production proof.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic local NMBot developer navigation")
    parser.add_argument("query", nargs="?", default="", help="stage_id/path_id/symbol/docs query")
    parser.add_argument("--json", action="store_true", help="print JSON output")
    parser.add_argument("--human", action="store_true", help="print human-readable output (default)")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="relative active retrieval manifest path")
    parser.add_argument("--validate-only", action="store_true", help="build and validate the in-memory registry, then exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.json and args.human:
        parser.error("--json and --human are mutually exclusive")
    try:
        if args.validate_only:
            registry = build_registry(root=ROOT, manifest_path=Path(args.manifest))
            payload = {"schema": OUTPUT_SCHEMA, "valid": True, "record_count": len(registry["records"]), "registry_fingerprint": registry["fingerprint"]}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) if args.json else f"navigation registry valid: {payload['record_count']} records")
            return 0
        report = navigate(args.query, root=ROOT, manifest_path=Path(args.manifest))
    except NavigationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) if args.json else render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
