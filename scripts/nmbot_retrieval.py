#!/usr/bin/env python3
"""Permanent local SQLite FTS card retrieval for NMBot developer navigation.

This tool is intentionally small and stdlib-only. It reads the explicit source
manifest, chunks files deterministically, builds an in-memory SQLite FTS5 table
for this invocation, and returns bounded candidate cards. Cards are navigation
candidates for the current OpenCode session to choose from or abstain; they are
not evidence and never replace grep/read over selected paths.
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
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "nmbot.retrieval_sources.v1"
OUTPUT_SCHEMA = "nmbot.fts_cards.v1"
DEFAULT_MANIFEST = Path("config/nmbot_retrieval_sources.json")
SOURCE_CARDS_SCHEMA = "nmbot.retrieval_source_cards.v1"
DEFAULT_SOURCE_CARDS = Path("config/nmbot_retrieval_source_cards.json")
SUPPORTED_TYPES = {"doc", "python", "prompt", "json", "test", "text"}
SUPPORTED_STATUSES = {"active", "legacy"}
# Keep a small headroom for active local navigation docs without changing FTS behavior.
MAX_MANIFEST_SOURCES = 50
FORBIDDEN_PREFIXES = (
    ".cache/",
    "archive/",
    "docs/archive/",
    "docs/legacy/",
    "release_bundles/",
    "logs/",
    "generated/",
    "reports/",
    "tmp/",
)
FORBIDDEN_PARTS = {"release_bundles", "logs", "__pycache__"}
MAX_CHUNK_CHARS = 4000
RAW_CHUNK_LIMIT = 20
DEFAULT_CARDS = 8
MAX_CARDS = 8
MIN_EXCERPT_CHARS = 500
DEFAULT_EXCERPT_CHARS = 650
MAX_EXCERPT_CHARS = 700
TOTAL_EXCERPT_CAP = 5600
SOURCE_CARD_KEYS = {"path", "purpose", "concepts", "owns", "entry_points", "tests"}
MAX_SOURCE_CARDS = 10
MAX_SOURCE_CARD_PURPOSE_CHARS = 180
MAX_SOURCE_CARD_ITEMS = 8
MAX_SOURCE_CARD_ITEM_CHARS = 120
# Frozen before the permanent route: these are the same neutral field weights
# used by the successful blind FTS -> session benchmark.
BM25_WEIGHTS = {"text": 1.0, "path": 3.0, "module": 2.0, "owner": 2.0, "stage_ids": 4.0}
FALLBACK_ROUTE = "docs_stage_map_then_grep_read"


class RetrievalError(ValueError):
    """Human-readable retrieval validation/runtime error."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_digest(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _repo_path(path_text: str, *, root: Path) -> Path:
    path = Path(path_text)
    if path.is_absolute() or ".." in path.parts:
        raise RetrievalError(f"path must be relative and stay inside repo: {path_text}")
    normalized = path.as_posix()
    if any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        raise RetrievalError(f"forbidden retrieval source path: {path_text}")
    if any(part in FORBIDDEN_PARTS for part in path.parts):
        raise RetrievalError(f"forbidden retrieval source path: {path_text}")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise RetrievalError(f"path escapes repo: {path_text}") from exc
    return resolved


def _load_stage_map(root: Path) -> dict[str, Any]:
    path = root / "config" / "nmbot_stage_map.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _allowed_stage_paths(stage_map: dict[str, Any], stage_id: str) -> set[str]:
    stages = stage_map.get("stages")
    if not isinstance(stages, dict) or stage_id not in stages:
        raise RetrievalError(f"unknown stage_id in retrieval manifest: {stage_id}")
    stage = stages[stage_id]
    allowed = set()
    for key in ("source", "doc", "test", "prompt"):
        value = stage.get(key)
        if isinstance(value, str):
            allowed.add(value)
    return allowed


def load_manifest(path: Path | None = None, *, root: Path = ROOT) -> dict[str, Any]:
    manifest_path = _repo_path(str(path or DEFAULT_MANIFEST), root=root)
    if not manifest_path.exists():
        raise RetrievalError(f"manifest not found: {path or DEFAULT_MANIFEST}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RetrievalError(f"manifest schema must be {SCHEMA}")
    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list) or not (1 <= len(raw_sources) <= MAX_MANIFEST_SOURCES):
        raise RetrievalError(f"manifest sources must contain 1..{MAX_MANIFEST_SOURCES} entries")
    stage_map = _load_stage_map(root)
    seen: set[str] = set()
    sources: list[dict[str, Any]] = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            raise RetrievalError("each retrieval source must be an object")
        item = dict(raw)
        path_text = item.get("path")
        if not isinstance(path_text, str) or not path_text.strip():
            raise RetrievalError("source path must be a non-empty string")
        if path_text in seen:
            raise RetrievalError(f"duplicate source path: {path_text}")
        seen.add(path_text)
        resolved = _repo_path(path_text, root=root)
        if not resolved.exists() or not resolved.is_file():
            raise RetrievalError(f"source path does not exist: {path_text}")
        for key in ("module", "type", "owner", "status"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise RetrievalError(f"source {path_text} missing non-empty {key}")
        if item["type"] not in SUPPORTED_TYPES:
            raise RetrievalError(f"source {path_text} has unsupported type: {item['type']}")
        if item["status"] not in SUPPORTED_STATUSES:
            raise RetrievalError(f"source {path_text} has unsupported status: {item['status']}")
        stage_id = item.get("stage_id")
        raw_stage_ids = item.get("stage_ids")
        if stage_id is not None and raw_stage_ids is not None:
            raise RetrievalError(f"source {path_text} must not specify both stage_id and stage_ids")
        if stage_id is not None:
            if not isinstance(stage_id, str) or not stage_id.strip():
                raise RetrievalError(f"source {path_text} stage_id must be a string")
            raw_stage_ids = [stage_id]
            item.pop("stage_id", None)
        if raw_stage_ids is not None:
            if not isinstance(raw_stage_ids, list) or not raw_stage_ids:
                raise RetrievalError(f"source {path_text} stage_ids must be a non-empty list")
            stage_ids: list[str] = []
            for raw_stage_id in raw_stage_ids:
                if not isinstance(raw_stage_id, str) or not raw_stage_id.strip():
                    raise RetrievalError(f"source {path_text} stage_ids must contain non-empty strings")
                stage_ids.append(raw_stage_id)
            if len(stage_ids) != len(set(stage_ids)):
                raise RetrievalError(f"source {path_text} stage_ids must be unique")
            stage_ids = sorted(stage_ids)
            for normalized_stage_id in stage_ids:
                if path_text not in _allowed_stage_paths(stage_map, normalized_stage_id):
                    raise RetrievalError(f"source {path_text} does not agree with stage map for {normalized_stage_id}")
            item["stage_ids"] = stage_ids
        sources.append(item)
    return {"schema": SCHEMA, "sources": sources}


def _bounded_source_card_string(value: Any, *, field: str, path_text: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RetrievalError(f"source card {path_text} {field} must be a non-empty string")
    if len(value) > limit:
        raise RetrievalError(f"source card {path_text} {field} is too long")
    return value


def _bounded_source_card_list(value: Any, *, field: str, path_text: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_SOURCE_CARD_ITEMS:
        raise RetrievalError(f"source card {path_text} {field} must be a list with at most {MAX_SOURCE_CARD_ITEMS} items")
    result: list[str] = []
    for item in value:
        result.append(_bounded_source_card_string(item, field=field, path_text=path_text, limit=MAX_SOURCE_CARD_ITEM_CHARS))
    if len(result) != len(set(result)):
        raise RetrievalError(f"source card {path_text} {field} must not contain duplicates")
    return result


def load_source_card_registry(path: Path | None = None, *, manifest: dict[str, Any], root: Path = ROOT) -> dict[str, dict[str, Any]]:
    registry_path = _repo_path(str(path or DEFAULT_SOURCE_CARDS), root=root)
    if not registry_path.exists():
        raise RetrievalError(f"source-card registry not found: {path or DEFAULT_SOURCE_CARDS}")
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SOURCE_CARDS_SCHEMA:
        raise RetrievalError(f"source-card registry schema must be {SOURCE_CARDS_SCHEMA}")
    raw_cards = data.get("cards")
    if not isinstance(raw_cards, list) or not (1 <= len(raw_cards) <= MAX_SOURCE_CARDS):
        raise RetrievalError(f"source-card registry cards must contain 1..{MAX_SOURCE_CARDS} entries")
    active_manifest_paths = {item["path"] for item in manifest["sources"] if item.get("status") == "active"}
    all_manifest_paths = {item["path"] for item in manifest["sources"]}
    seen: set[str] = set()
    cards_by_path: dict[str, dict[str, Any]] = {}
    for raw in raw_cards:
        if not isinstance(raw, dict):
            raise RetrievalError("each source card must be an object")
        if set(raw) != SOURCE_CARD_KEYS:
            raise RetrievalError(f"source card keys must be exactly {sorted(SOURCE_CARD_KEYS)}")
        path_text = _bounded_source_card_string(raw.get("path"), field="path", path_text="<unknown>", limit=MAX_SOURCE_CARD_ITEM_CHARS)
        if path_text in seen:
            raise RetrievalError(f"duplicate source-card path: {path_text}")
        seen.add(path_text)
        _repo_path(path_text, root=root)
        if path_text not in all_manifest_paths:
            raise RetrievalError(f"source-card path is not in retrieval manifest: {path_text}")
        if path_text not in active_manifest_paths:
            raise RetrievalError(f"source-card path is not active in retrieval manifest: {path_text}")
        card = {
            "path": path_text,
            "purpose": _bounded_source_card_string(raw["purpose"], field="purpose", path_text=path_text, limit=MAX_SOURCE_CARD_PURPOSE_CHARS),
            "concepts": _bounded_source_card_list(raw["concepts"], field="concepts", path_text=path_text),
            "owns": _bounded_source_card_list(raw["owns"], field="owns", path_text=path_text),
            "entry_points": _bounded_source_card_list(raw["entry_points"], field="entry_points", path_text=path_text),
            "tests": _bounded_source_card_list(raw["tests"], field="tests", path_text=path_text),
        }
        for test_path in card["tests"]:
            resolved_test = _repo_path(test_path, root=root)
            if not resolved_test.exists() or not resolved_test.is_file():
                raise RetrievalError(f"source card {path_text} test path does not exist: {test_path}")
        cards_by_path[path_text] = card
    return cards_by_path


def compute_source_digest(manifest: dict[str, Any], *, root: Path = ROOT) -> str:
    pieces = []
    for item in manifest["sources"]:
        path = _repo_path(item["path"], root=root)
        pieces.append({"source": item, "file_sha256": sha256_text(path.read_text(encoding="utf-8"))})
    return _json_digest({"schema": manifest["schema"], "sources": pieces})


def _bounded_windows(text: str, *, start_line: int, max_chars: int = MAX_CHUNK_CHARS) -> Iterable[tuple[str, int, int]]:
    lines = text.splitlines()
    current: list[str] = []
    current_start = start_line
    for idx, line in enumerate(lines, start=start_line):
        if len(line) > max_chars:
            if current:
                yield "\n".join(current).strip(), current_start, idx - 1
                current = []
            for offset in range(0, len(line), max_chars):
                yield line[offset : offset + max_chars].strip(), idx, idx
            current_start = idx + 1
            continue
        candidate = "\n".join([*current, line]) if current else line
        if current and len(candidate) > max_chars:
            yield "\n".join(current).strip(), current_start, idx - 1
            current = [line]
            current_start = idx
        else:
            current.append(line)
    if current:
        yield "\n".join(current).strip(), current_start, start_line + len(lines) - 1


def _markdown_sections(text: str) -> list[tuple[str, int, int]]:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(r"^#{1,6}\s+", line)]
    if not starts:
        return list(_bounded_windows(text, start_line=1))
    if starts[0] != 0:
        starts.insert(0, 0)
    sections: list[tuple[str, int, int]] = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        section = "\n".join(lines[start:end]).strip()
        if section:
            sections.extend(_bounded_windows(section, start_line=start + 1))
    return sections


def _python_sections(text: str) -> list[tuple[str, int, int]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return list(_bounded_windows(text, start_line=1))
    lines = text.splitlines()
    spans: list[tuple[int, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            decorator_lines = [getattr(decorator, "lineno", getattr(node, "lineno", 1)) for decorator in getattr(node, "decorator_list", [])]
            start = min([getattr(node, "lineno", 1), *decorator_lines])
            end = getattr(node, "end_lineno", start)
            spans.append((start, end))
    spans.sort()
    sections: list[tuple[str, int, int]] = []
    previous_end = 0
    for start, end in spans:
        gap = "\n".join(lines[previous_end : start - 1])
        if gap.strip():
            sections.extend(_bounded_windows(gap, start_line=previous_end + 1))
        chunk = "\n".join(lines[start - 1 : end]).strip()
        if chunk:
            sections.extend(_bounded_windows(chunk, start_line=start))
        previous_end = max(previous_end, end)
    if spans:
        trailing = "\n".join(lines[previous_end:])
        if trailing.strip():
            sections.extend(_bounded_windows(trailing, start_line=previous_end + 1))
    if not sections:
        sections = list(_bounded_windows(text, start_line=1))
    return sections


def _text_sections(text: str) -> list[tuple[str, int, int]]:
    parts: list[tuple[str, int, int]] = []
    lines = text.splitlines()
    start = 1
    buf: list[str] = []
    for idx, line in enumerate(lines, start=1):
        if not line.strip() and buf:
            parts.extend(_bounded_windows("\n".join(buf).strip(), start_line=start))
            buf = []
            start = idx + 1
        elif line.strip():
            if not buf:
                start = idx
            buf.append(line)
    if buf:
        parts.extend(_bounded_windows("\n".join(buf).strip(), start_line=start))
    return parts or list(_bounded_windows(text, start_line=1))


def chunk_source(source: dict[str, Any], *, root: Path = ROOT) -> list[dict[str, Any]]:
    path = _repo_path(source["path"], root=root)
    text = path.read_text(encoding="utf-8")
    typ = source["type"]
    if typ in {"doc"} and path.suffix.lower() in {".md", ".markdown"}:
        sections = _markdown_sections(text)
    elif typ == "python" or path.suffix == ".py":
        sections = _python_sections(text)
    else:
        sections = _text_sections(text)
    chunks: list[dict[str, Any]] = []
    for index, (chunk_text, start_line, end_line) in enumerate(sections):
        chunk_text = chunk_text.strip()
        if not chunk_text:
            continue
        metadata = {key: source[key] for key in ("path", "module", "type", "owner", "status")}
        metadata["stage_ids"] = list(source.get("stage_ids", []))
        text_hash = sha256_text(chunk_text)
        stable = _json_digest({"path": source["path"], "index": index, "start_line": start_line, "end_line": end_line, "text_hash": text_hash, "metadata": metadata})[:24]
        chunks.append({"id": stable, "metadata": metadata, "start_line": start_line, "end_line": end_line, "text": chunk_text, "text_hash": text_hash, "chunk_hash": _json_digest({"text_hash": text_hash, "metadata": metadata})})
    return chunks


def _indexable_sources(manifest: dict[str, Any], *, include_legacy_sources: bool = False) -> list[dict[str, Any]]:
    if include_legacy_sources:
        return list(manifest["sources"])
    return [source for source in manifest["sources"] if source.get("status") == "active"]


def chunk_manifest(manifest: dict[str, Any], *, root: Path = ROOT, include_legacy_sources: bool = False) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for source in _indexable_sources(manifest, include_legacy_sources=include_legacy_sources):
        chunks.extend(chunk_source(source, root=root))
    return chunks


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-zА-Яа-я0-9_]{2,}", text.lower())


def _quote_fts(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def build_fts_query(query: str, *, terms: list[str] | None = None, phrases: list[str] | None = None) -> str:
    pieces: list[str] = []
    seen: set[str] = set()
    for token in _tokens(query):
        if token not in seen:
            seen.add(token)
            pieces.append(_quote_fts(token))
    for term in terms or []:
        for token in _tokens(term):
            key = f"term:{token}"
            if key not in seen:
                seen.add(key)
                pieces.append(_quote_fts(token))
    for phrase in phrases or []:
        normalized = " ".join(_tokens(phrase))
        if normalized:
            key = f"phrase:{normalized}"
            if key not in seen:
                seen.add(key)
                pieces.append(_quote_fts(normalized))
    return " OR ".join(pieces)


def _open_fts(chunks: list[dict[str, Any]]) -> tuple[sqlite3.Connection, dict[int, dict[str, Any]]]:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE cards USING fts5(text, path, module, owner, stage_ids, tokenize='unicode61')")
    by_rowid: dict[int, dict[str, Any]] = {}
    for rowid, chunk in enumerate(chunks, start=1):
        meta = chunk["metadata"]
        stage_ids = " ".join(meta.get("stage_ids", []))
        conn.execute(
            "INSERT INTO cards(rowid, text, path, module, owner, stage_ids) VALUES (?, ?, ?, ?, ?, ?)",
            (rowid, chunk["text"], meta["path"], meta["module"], meta["owner"], stage_ids),
        )
        by_rowid[rowid] = chunk
    conn.commit()
    return conn, by_rowid


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "… [truncated]"
    return text[: max(0, limit - len(marker))].rstrip() + marker


def search_cards(query: str, *, root: Path = ROOT, manifest_path: Path = DEFAULT_MANIFEST, terms: list[str] | None = None, phrases: list[str] | None = None, cards: int = DEFAULT_CARDS, excerpt_chars: int = DEFAULT_EXCERPT_CHARS, source_cards: bool = False, source_cards_path: Path = DEFAULT_SOURCE_CARDS, include_legacy_sources: bool = False) -> dict[str, Any]:
    if not 1 <= cards <= MAX_CARDS:
        raise RetrievalError(f"--cards must be between 1 and {MAX_CARDS}")
    if not MIN_EXCERPT_CHARS <= excerpt_chars <= MAX_EXCERPT_CHARS:
        raise RetrievalError(f"--excerpt-chars must be between {MIN_EXCERPT_CHARS} and {MAX_EXCERPT_CHARS}")
    manifest = load_manifest(manifest_path, root=root)
    source_cards_by_path = load_source_card_registry(source_cards_path, manifest=manifest, root=root) if source_cards else {}
    chunks = chunk_manifest(manifest, root=root, include_legacy_sources=include_legacy_sources)
    fts_query = build_fts_query(query or "", terms=terms, phrases=phrases)
    base = {
        "schema": OUTPUT_SCHEMA,
        "query": query or "",
        "expansion": {"terms": list(terms or []), "phrases": list(phrases or [])},
        "requires_session_rerank": True,
        "cards_are_candidates_not_evidence": True,
        "fallback_route": FALLBACK_ROUTE,
        "raw_chunk_limit": RAW_CHUNK_LIMIT,
        "cards_limit": cards,
        "excerpt_chars": excerpt_chars,
        "aggregate_excerpt_cap": TOTAL_EXCERPT_CAP,
        "bm25_weights": BM25_WEIGHTS,
        "source_digest": compute_source_digest(manifest, root=root),
        "indexed_source_statuses": ["active", "legacy"] if include_legacy_sources else ["active"],
    }
    if source_cards:
        base["source_cards_enabled"] = True
        base["source_cards_schema"] = SOURCE_CARDS_SCHEMA
    if not fts_query:
        return {**base, "abstain": True, "card_count": 0, "total_excerpt_chars": 0, "cards": [], "next_step": "No lexical FTS query terms. Use normal docs/stage-map → grep → read; do not invent evidence."}
    conn, by_rowid = _open_fts(chunks)
    try:
        rows = conn.execute(
            "SELECT rowid, bm25(cards, ?, ?, ?, ?, ?) AS rank FROM cards WHERE cards MATCH ? ORDER BY rank ASC, path ASC, rowid ASC LIMIT ?",
            (BM25_WEIGHTS["text"], BM25_WEIGHTS["path"], BM25_WEIGHTS["module"], BM25_WEIGHTS["owner"], BM25_WEIGHTS["stage_ids"], fts_query, RAW_CHUNK_LIMIT),
        ).fetchall()
    except sqlite3.Error as exc:
        raise RetrievalError(f"safe FTS query failed: {exc}") from exc
    finally:
        conn.close()
    selected: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    total = 0
    for rowid, rank in rows:
        if len(selected) >= cards:
            break
        chunk = by_rowid[int(rowid)]
        meta = chunk["metadata"]
        path = meta["path"]
        if path in seen_paths:
            continue
        remaining = TOTAL_EXCERPT_CAP - total
        if remaining <= 0:
            break
        excerpt = _excerpt(chunk["text"], min(excerpt_chars, remaining))
        total += len(excerpt)
        seen_paths.add(path)
        card = {
                "candidate_id": "c" + _json_digest({"chunk_id": chunk["id"], "query": fts_query})[:12],
                "path": path,
                "line_range": [chunk["start_line"], chunk["end_line"]],
                "module": meta["module"],
                "type": meta["type"],
                "owner": meta["owner"],
                "status": meta["status"],
                "stage_ids": list(meta.get("stage_ids", [])),
                "excerpt": excerpt,
                "fts_score": round(float(rank), 6),
            }
        if path in source_cards_by_path:
            card["source_card"] = source_cards_by_path[path]
        selected.append(card)
    abstain = not selected
    return {**base, "abstain": abstain, "card_count": len(selected), "total_excerpt_chars": total, "cards": selected, "next_step": ("No lexical match. Use normal docs/stage-map → grep → read; do not invent evidence." if abstain else "Current OpenCode session must choose 0..4 candidate cards or abstain, then grep/read selected paths before treating anything as evidence.")}


def render_human(report: dict[str, Any]) -> str:
    lines = [f"NMBot FTS cards: {report.get('card_count', 0)} candidates; session rerank required", "Cards are candidates, not evidence."]
    if report.get("abstain"):
        lines.append("Abstain: use normal docs/stage-map → grep → read; do not invent evidence.")
    for index, item in enumerate(report.get("cards", []), start=1):
        start, end = item["line_range"]
        lines.append(f"\n{index}. {item['candidate_id']} {item['path']}:{start}-{end} [{item['module']}/{item['type']}/{item['owner']}/{item['status']}] fts={item['fts_score']}")
        if item.get("stage_ids"):
            lines.append(f"   stages: {', '.join(item['stage_ids'])}")
        lines.append(item["excerpt"])
    lines.append(f"\nNext: {report.get('next_step')}")
    return "\n".join(lines)


def _bounded_int(flag: str, low: int, high: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{flag} must be an integer") from exc
        if not low <= parsed <= high:
            raise argparse.ArgumentTypeError(f"{flag} must be between {low} and {high}")
        return parsed
    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query local NMBot SQLite FTS candidate cards")
    parser.add_argument("query", nargs="?", default="", help="developer navigation query text")
    parser.add_argument("--json", action="store_true", help="print JSON output")
    parser.add_argument("--human", action="store_true", help="print human-readable output (default)")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="relative manifest path")
    parser.add_argument("--term", action="append", default=[], help="repeatable neutral expansion term supplied by the session")
    parser.add_argument("--phrase", action="append", default=[], help="repeatable neutral expansion phrase supplied by the session")
    parser.add_argument("--cards", type=_bounded_int("--cards", 1, MAX_CARDS), default=DEFAULT_CARDS, help=f"candidate card count, 1..{MAX_CARDS}")
    parser.add_argument("--excerpt-chars", type=_bounded_int("--excerpt-chars", MIN_EXCERPT_CHARS, MAX_EXCERPT_CHARS), default=DEFAULT_EXCERPT_CHARS, help=f"chars per card excerpt, {MIN_EXCERPT_CHARS}..{MAX_EXCERPT_CHARS}")
    parser.add_argument("--source-cards", action="store_true", help="attach opt-in verified navigation source-card context without changing FTS ranking")
    parser.add_argument("--source-card-registry", default=str(DEFAULT_SOURCE_CARDS), help="relative source-card registry path")
    parser.add_argument("--include-legacy-sources", action="store_true", help="explicit opt-in: include manifest sources marked legacy in the local FTS index")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.json and args.human:
        parser.error("--json and --human are mutually exclusive")
    try:
        report = search_cards(args.query, root=ROOT, manifest_path=Path(args.manifest), terms=args.term, phrases=args.phrase, cards=args.cards, excerpt_chars=args.excerpt_chars, source_cards=args.source_cards, source_cards_path=Path(args.source_card_registry), include_legacy_sources=args.include_legacy_sources)
    except RetrievalError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
