#!/usr/bin/env python3
"""Print local-only nmbot documentation context packs.

This tool parses a deliberately small JSON block embedded in
docs/NMBOT_CONTEXT_PACKS.md. It reports context only: it never executes checks,
imports runtime modules, calls subprocesses, opens network connections, deploys,
or proves production/Jivo state.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path("docs/NMBOT_CONTEXT_PACKS.md")
SCHEMA = "nmbot.context_pack.v1"
START = "<!-- NMBOT_CONTEXT_PACKS_JSON_START -->"
END = "<!-- NMBOT_CONTEXT_PACKS_JSON_END -->"
EXPANSION_RULE = "open_next_only_when_referenced_by_current_source"
INITIAL_SOURCE_LIMIT = 2
DEFAULT_MATERIALIZE_MAX_LINES = 80
DEFAULT_MATERIALIZE_MAX_CHARS = 8000
MAX_MATERIALIZE_LINES = 200
MAX_MATERIALIZE_CHARS = 20000
CODE_ANCHOR_WINDOW_LINES = 40

CHECK_ALLOWED_RE = re.compile(r"^[A-Za-z0-9_./:= -]+$")
CHECK_FORBIDDEN_RE = re.compile(
    r"\b(ssh|scp|rsync|curl|wget|deploy|restart|systemctl|journalctl|openrouter|provider|model|promptfoo|eval|jivo[-_ ]?smoke)\b",
    re.IGNORECASE,
)


class ContextPackError(ValueError):
    """Human-readable manifest/CLI validation error."""


def _repo_path(path_text: str, *, root: Path) -> Path:
    path = Path(path_text)
    if path.is_absolute() or ".." in path.parts:
        raise ContextPackError(f"path must be relative and stay inside repo: {path_text}")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ContextPackError(f"path escapes repo: {path_text}") from exc
    return resolved


def resolve_manifest_path(path_text: str | None, *, root: Path = ROOT) -> Path:
    raw = Path(path_text) if path_text else DEFAULT_MANIFEST
    if raw.is_absolute() or ".." in raw.parts:
        raise ContextPackError("--manifest must be a relative path inside the repository")
    path = _repo_path(str(raw), root=root)
    if not path.exists():
        raise ContextPackError(f"manifest not found: {raw}")
    return path


def _extract_json_block(text: str) -> str:
    try:
        inner = text.split(START, 1)[1].split(END, 1)[0]
    except IndexError as exc:
        raise ContextPackError("manifest JSON markers not found") from exc
    match = re.search(r"```json\s*(.*?)\s*```", inner, re.DOTALL)
    if not match:
        raise ContextPackError("manifest JSON fenced block not found")
    return match.group(1)


def _validate_string_list(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ContextPackError(f"{field} must be a list")
    if not value and not allow_empty:
        raise ContextPackError(f"{field} must not be empty")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ContextPackError(f"{field} entries must be non-empty strings")
        result.append(item)
    return result


def _validate_relative_existing_paths(paths: list[str], field: str, *, root: Path) -> None:
    for item in paths:
        path = _repo_path(item, root=root)
        if not path.exists():
            raise ContextPackError(f"{field} path does not exist: {item}")


def _validate_checks(checks: list[str]) -> None:
    for check in checks:
        if not CHECK_ALLOWED_RE.fullmatch(check):
            raise ContextPackError(f"unsafe check string: {check}")
        if CHECK_FORBIDDEN_RE.search(check):
            raise ContextPackError(f"forbidden external/runtime action in check: {check}")


def _validate_read_first(read_first: list[str], pack_id: str, *, allowed_paths: set[str], root: Path) -> None:
    if not 1 <= len(read_first) <= 2:
        raise ContextPackError(f"pack {pack_id} read_first must contain one or two paths")
    if len(set(read_first)) != len(read_first):
        raise ContextPackError(f"pack {pack_id} read_first must not contain duplicates")
    missing_from_pack = [item for item in read_first if item not in allowed_paths]
    if missing_from_pack:
        raise ContextPackError(f"pack {pack_id} read_first must be chosen from docs/files: {', '.join(missing_from_pack)}")
    _validate_relative_existing_paths(read_first, f"pack {pack_id} read_first", root=root)


def _validate_read_first_anchors(raw: Any, read_first: list[str], pack_id: str, *, root: Path) -> list[dict[str, str]]:
    if raw is None:
        return [{"path": item, "anchor": ""} for item in read_first]
    if not isinstance(raw, list):
        raise ContextPackError(f"pack {pack_id} read_first_anchors must be a list")
    if len(raw) != len(read_first):
        raise ContextPackError(f"pack {pack_id} read_first_anchors must match read_first length")

    anchors: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ContextPackError(f"pack {pack_id} read_first_anchors entries must be objects")
        path = item.get("path")
        anchor = item.get("anchor")
        if path != read_first[index]:
            raise ContextPackError(f"pack {pack_id} read_first_anchors must align one-to-one with read_first")
        if not isinstance(anchor, str) or not anchor.strip():
            raise ContextPackError(f"pack {pack_id} read_first_anchors anchor must be non-empty: {path}")
        key = (path, anchor)
        if key in seen:
            raise ContextPackError(f"pack {pack_id} read_first_anchors must not contain duplicates")
        seen.add(key)
        target = _repo_path(path, root=root)
        text = target.read_text(encoding="utf-8")
        if anchor not in text:
            raise ContextPackError(f"pack {pack_id} read_first_anchors anchor not found in {path}: {anchor}")
        anchors.append({"path": path, "anchor": anchor})
    return anchors


def parse_manifest_text(text: str, *, root: Path = ROOT) -> dict[str, Any]:
    try:
        data = json.loads(_extract_json_block(text))
    except json.JSONDecodeError as exc:
        raise ContextPackError(f"manifest JSON is malformed: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ContextPackError("manifest root must be an object")
    if data.get("schema") != SCHEMA:
        raise ContextPackError(f"manifest schema must be {SCHEMA}")
    packs_raw = data.get("packs")
    if not isinstance(packs_raw, list) or not packs_raw:
        raise ContextPackError("manifest packs must be a non-empty list")

    seen: set[str] = set()
    packs: list[dict[str, Any]] = []
    for raw in packs_raw:
        if not isinstance(raw, dict):
            raise ContextPackError("each pack must be an object")
        pack_id = raw.get("id")
        title = raw.get("title")
        if not isinstance(pack_id, str) or not re.fullmatch(r"[a-z0-9_-]+/[a-z0-9_-]+", pack_id):
            raise ContextPackError(f"invalid pack id: {pack_id!r}")
        if pack_id in seen:
            raise ContextPackError(f"duplicate pack id: {pack_id}")
        seen.add(pack_id)
        if not isinstance(title, str) or not title.strip():
            raise ContextPackError(f"pack {pack_id} title must be a non-empty string")

        docs = _validate_string_list(raw.get("docs"), f"pack {pack_id} docs")
        files = _validate_string_list(raw.get("files"), f"pack {pack_id} files", allow_empty=True)
        read_first = _validate_string_list(raw.get("read_first"), f"pack {pack_id} read_first")
        checks = _validate_string_list(raw.get("checks"), f"pack {pack_id} checks")
        boundaries = _validate_string_list(raw.get("boundaries"), f"pack {pack_id} boundaries")
        _validate_relative_existing_paths(docs, f"pack {pack_id} docs", root=root)
        _validate_relative_existing_paths(files, f"pack {pack_id} files", root=root)
        _validate_read_first(read_first, pack_id, allowed_paths=set(docs) | set(files), root=root)
        read_first_anchors = _validate_read_first_anchors(raw.get("read_first_anchors"), read_first, pack_id, root=root)
        _validate_checks(checks)

        packs.append({"id": pack_id, "title": title, "read_first": read_first, "read_first_anchors": read_first_anchors, "docs": docs, "files": files, "checks": checks, "boundaries": boundaries})

    packs.sort(key=lambda item: item["id"])
    return {"schema": SCHEMA, "packs": packs}


def load_manifest(path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    return parse_manifest_text(path.read_text(encoding="utf-8"), root=root)


def _pack_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {pack["id"]: pack for pack in manifest["packs"]}


def _base_report() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "local_read_only": True,
        "production_proof": False,
        "boundary": "Reports required local context only; does not run checks, deploy, call models/providers/VPS/API, or prove production/Jivo behavior.",
    }


def render_list(manifest: dict[str, Any], *, human: bool) -> str:
    ids = sorted(_pack_map(manifest))
    if human:
        return "Available context packs:\n" + "\n".join(f"- {item}" for item in ids)
    report = _base_report()
    report["packs"] = ids
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


def _brief_context_budget(pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "initial_source_limit": INITIAL_SOURCE_LIMIT,
        "read_first": pack["read_first_anchors"][:INITIAL_SOURCE_LIMIT],
        "primary_local_check": pack["checks"][0],
        "boundaries": pack["boundaries"],
        "expansion_rule": EXPANSION_RULE,
        "source_link_criterion": "Open another doc/file only when the current source, check output, or explicit task evidence references it.",
    }


def _bounded_positive_int(value: str, *, flag: str, upper: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{flag} must be an integer") from exc
    if parsed <= 0 or parsed > upper:
        raise argparse.ArgumentTypeError(f"{flag} must be between 1 and {upper}")
    return parsed


def _max_lines_value(value: str) -> int:
    return _bounded_positive_int(value, flag="--max-lines", upper=MAX_MATERIALIZE_LINES)


def _max_chars_value(value: str) -> int:
    return _bounded_positive_int(value, flag="--max-chars", upper=MAX_MATERIALIZE_CHARS)


def _line_allocation(total: int, index: int, count: int) -> int:
    base, remainder = divmod(total, count)
    return base + (1 if index < remainder else 0)


def _find_anchor_line(lines: list[str], anchor: str) -> int:
    for index, line in enumerate(lines):
        if anchor in line:
            return index
    raise ContextPackError(f"anchor not found while materializing: {anchor}")


def _markdown_heading_level(line: str, anchor: str) -> int | None:
    if line.strip() != anchor.strip():
        return None
    match = re.match(r"^(#{1,6})\s+", line.strip())
    if not match:
        return None
    return len(match.group(1))


def _candidate_excerpt_lines(lines: list[str], start: int, anchor: str, line_budget: int) -> tuple[list[str], bool]:
    heading_level = _markdown_heading_level(lines[start], anchor)
    if heading_level is not None:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            match = re.match(r"^(#{1,6})\s+", lines[index].strip())
            if match and len(match.group(1)) <= heading_level:
                end = index
                break
        candidate = lines[start:end]
    else:
        available = lines[start:]
        candidate = available[: min(CODE_ANCHOR_WINDOW_LINES, line_budget)]
        return candidate, len(available) > len(candidate)
    truncated = len(candidate) > line_budget
    return candidate[:line_budget], truncated


def _fit_excerpt(lines: list[str], char_budget: int) -> tuple[str, bool]:
    excerpt = "\n".join(lines)
    if len(excerpt) <= char_budget:
        return excerpt, False
    marker = "… [truncated]"
    if char_budget >= len(marker):
        return excerpt[: char_budget - len(marker)].rstrip() + marker, True
    return excerpt[:char_budget], True


def materialize_read_first_sources(pack: dict[str, Any], *, root: Path = ROOT, max_lines: int = DEFAULT_MATERIALIZE_MAX_LINES, max_chars: int = DEFAULT_MATERIALIZE_MAX_CHARS) -> list[dict[str, Any]]:
    targets = pack["read_first_anchors"][:INITIAL_SOURCE_LIMIT]
    materialized: list[dict[str, Any]] = []
    count = len(targets)
    for index, target in enumerate(targets):
        line_budget = _line_allocation(max_lines, index, count)
        char_budget = _line_allocation(max_chars, index, count)
        path = _repo_path(target["path"], root=root)
        lines = path.read_text(encoding="utf-8").splitlines()
        start = _find_anchor_line(lines, target["anchor"])
        candidate_lines, line_truncated = _candidate_excerpt_lines(lines, start, target["anchor"], line_budget)
        excerpt, char_truncated = _fit_excerpt(candidate_lines, char_budget)
        excerpt_line_count = 0 if not excerpt else len(excerpt.splitlines())
        materialized.append(
            {
                "path": target["path"],
                "anchor": target["anchor"],
                "start_line": start + 1,
                "end_line": start + excerpt_line_count,
                "excerpt": excerpt,
                "truncated": line_truncated or char_truncated,
            }
        )
    return materialized


def render_pack(manifest: dict[str, Any], pack_id: str, *, human: bool, brief: bool = False, materialize: bool = False, max_lines: int = DEFAULT_MATERIALIZE_MAX_LINES, max_chars: int = DEFAULT_MATERIALIZE_MAX_CHARS, root: Path = ROOT) -> str:
    packs = _pack_map(manifest)
    if pack_id not in packs:
        raise ContextPackError(f"unknown context pack: {pack_id}")
    pack = packs[pack_id]
    if brief:
        budget = _brief_context_budget(pack)
        materialized = materialize_read_first_sources(pack, root=root, max_lines=max_lines, max_chars=max_chars) if materialize else []
        if not human:
            report = _base_report()
            report["pack"] = {"id": pack["id"], "title": pack["title"]}
            report["context_budget"] = budget
            if materialize:
                report["materialized_sources"] = materialized
            return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        lines = [
            f"Context pack: {pack['id']} — {pack['title']}",
            f"Initial source limit: {budget['initial_source_limit']}",
            "Read first:",
            *[f"- {item['path']} — {item['anchor']}" for item in budget["read_first"]],
            "Primary local check:",
            f"- {budget['primary_local_check']}",
            "Boundary:",
            *[f"- {item}" for item in budget["boundaries"]],
            "Expansion rule:",
            f"- {budget['expansion_rule']}: {budget['source_link_criterion']}",
        ]
        if materialize:
            lines.extend([
                "Materialized sources:",
                f"- Hard total budget: {max_lines} lines / {max_chars} chars across excerpts",
            ])
            for item in materialized:
                status = " truncated" if item["truncated"] else ""
                lines.append(f"### {item['path']}:{item['start_line']}-{item['end_line']} — {item['anchor']}{status}")
                lines.append(item["excerpt"])
        return "\n".join(lines)
    if not human:
        report = _base_report()
        report["pack"] = {key: value for key, value in pack.items() if key != "read_first_anchors"}
        return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)

    lines = [
        f"Context pack: {pack['id']} — {pack['title']}",
        "Boundary: local/read-only navigation only; this does not prove production or Jivo behavior.",
        "Read first:",
        *[f"- {item}" for item in pack["read_first"]],
        "Required docs:",
        *[f"- {item}" for item in pack["docs"]],
        "Relevant prompts/source files:",
        *[f"- {item}" for item in pack["files"]],
        "Targeted local checks to run separately:",
        *[f"- {item}" for item in pack["checks"]],
        "Explicit boundaries:",
        *[f"- {item}" for item in pack["boundaries"]],
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print local-only nmbot context packs")
    parser.add_argument("--pack", help="context pack id, for example prompt/rental")
    parser.add_argument("--list", action="store_true", help="list available context pack IDs")
    parser.add_argument("--human", action="store_true", help="print concise human-readable output")
    parser.add_argument("--brief", action="store_true", help="print a compact context budget for one pack")
    parser.add_argument("--materialize", action="store_true", help="with --pack --brief, include bounded local excerpts for read-first anchors")
    parser.add_argument("--max-lines", type=_max_lines_value, help=f"maximum total excerpt lines for --materialize, 1-{MAX_MATERIALIZE_LINES} (default: {DEFAULT_MATERIALIZE_MAX_LINES})")
    parser.add_argument("--max-chars", type=_max_chars_value, help=f"maximum total excerpt characters for --materialize, 1-{MAX_MATERIALIZE_CHARS} (default: {DEFAULT_MATERIALIZE_MAX_CHARS})")
    parser.add_argument("--manifest", help="relative path to Markdown manifest inside repo")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.list and not args.pack:
        parser.error("--pack is required unless --list is used")
    if args.brief and (args.list or not args.pack):
        parser.error("--brief requires --pack and cannot be used with --list")
    if args.materialize and (args.list or not args.pack or not args.brief):
        parser.error("--materialize requires --pack --brief and cannot be used with --list")
    if not args.materialize and (args.max_lines is not None or args.max_chars is not None):
        parser.error("--max-lines and --max-chars require --materialize")
    max_lines = args.max_lines if args.max_lines is not None else DEFAULT_MATERIALIZE_MAX_LINES
    max_chars = args.max_chars if args.max_chars is not None else DEFAULT_MATERIALIZE_MAX_CHARS
    try:
        manifest_path = resolve_manifest_path(args.manifest)
        manifest = load_manifest(manifest_path)
        output = render_list(manifest, human=args.human) if args.list else render_pack(manifest, args.pack, human=args.human, brief=args.brief, materialize=args.materialize, max_lines=max_lines, max_chars=max_chars)
    except ContextPackError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
