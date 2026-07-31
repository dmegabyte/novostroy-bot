#!/usr/bin/env python3
"""Universal strict STOP-2 context gate for allowlisted projects."""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from project_adapter_core import AdapterError, ProjectAdapter, load_adapter, safe_join
import project_navigation_core as navigation


SCHEMA = "project.context_gate.v1"
TRACE_SCHEMA = "bounded-retrieval.v1"
EVIDENCE_TYPES = {"stage", "symbol", "docs"}
STRICT_TARGET_KINDS = {"stage", "symbol", "docs"}


class GateError(ValueError):
    """Human-readable gate error."""


def _norm_path(path: str) -> str:
    return Path(path).as_posix().lstrip("./")


def _normalize_block_pattern(pattern: str, *, root: Path) -> str:
    raw = str(pattern or "").strip()
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise GateError(f"do_not_open pattern must be relative and stay inside adapter root: {pattern}")
    prefix: list[str] = []
    for part in Path(raw).parts:
        if any(ch in part for ch in "*?["):
            break
        prefix.append(part)
    if prefix:
        try:
            safe_join(root, str(Path(*prefix)), must_exist=False)
        except AdapterError as exc:
            raise GateError(str(exc)) from exc
    return _norm_path(raw)


def _is_blocked(path: str, patterns: Iterable[str]) -> bool:
    normalized = _norm_path(path)
    return any(normalized == pattern or fnmatch.fnmatchcase(normalized, pattern) for pattern in patterns)


def _read_text(adapter: ProjectAdapter, rel_path: str) -> str:
    try:
        return safe_join(adapter.root, rel_path).read_text(encoding="utf-8")
    except AdapterError as exc:
        raise GateError(str(exc)) from exc


def _line_count(adapter: ProjectAdapter, rel_path: str) -> int:
    return max(1, len(_read_text(adapter, rel_path).splitlines()))


def _range_for(adapter: ProjectAdapter, item: dict[str, Any], *, max_lines: int) -> tuple[int, int, bool]:
    total = _line_count(adapter, str(item["path"]))
    start = max(1, int(item.get("start_line") or 1))
    raw_end = max(start, int(item.get("end_line") or start))
    available_end = min(total, raw_end)
    end = min(available_end, start + max_lines - 1)
    return start, end, end < available_end


def _range_stats(adapter: ProjectAdapter, path: str, start: int, end: int) -> tuple[int, int]:
    lines = _read_text(adapter, path).splitlines()[start - 1:end]
    return len(lines), len("\n".join(lines))


def _source_id(item: dict[str, Any], start: int, end: int) -> str:
    label = item.get("stage_id") or item.get("symbol") or item.get("anchor") or item.get("kind") or "source"
    return f"{item['path']}:{start}-{end}:{label}"


def _select_context(items: Iterable[dict[str, Any]], *, adapter: ProjectAdapter, max_sources: int, max_lines: int, max_chars: int, do_not_open: Iterable[str]) -> tuple[list[dict[str, Any]], str]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    used_lines = 0
    used_chars = 0
    budget = "within_budget"
    for item in items:
        path = _norm_path(str(item["path"]))
        if path in seen or _is_blocked(path, do_not_open):
            continue
        start, end, truncated = _range_for(adapter, {**item, "path": path}, max_lines=max_lines)
        if truncated:
            budget = "context_budget_reached"
        lines, chars = _range_stats(adapter, path, start, end)
        if selected and (used_lines + lines > max_lines or used_chars + chars > max_chars):
            budget = "context_budget_reached"
            break
        if not selected and (lines > max_lines or chars > max_chars):
            budget = "context_budget_reached"
            while lines > 1 and (lines > max_lines or chars > max_chars):
                end -= 1
                lines, chars = _range_stats(adapter, path, start, end)
            if lines > max_lines or chars > max_chars:
                break
        entry = {
            "id": _source_id({**item, "path": path}, start, end), "path": path, "start_line": start, "end_line": end,
            "lines": lines, "characters": chars, "role": item.get("stage_field") or item.get("kind"),
            "evidence": not item.get("candidate_only", False),
        }
        for key in ("stage_id", "source_symbol", "symbol", "anchor", "related_test", "report_only"):
            if item.get(key) is not None and item.get(key) != "":
                entry[key] = item[key]
        selected.append(entry)
        seen.add(path)
        used_lines += lines
        used_chars += chars
        if len(selected) >= max_sources:
            break
    return selected, budget


def _test_body_references_symbol(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, symbol: str) -> bool:
    for stmt in node.body:
        for child in ast.walk(stmt):
            if isinstance(child, ast.Name) and child.id == symbol:
                return True
            if isinstance(child, ast.Attribute) and child.attr == symbol:
                return True
    return False


def _focused_test_range(adapter: ProjectAdapter, path: str, symbol: str | None) -> tuple[int, int] | None:
    if not symbol:
        return None
    text = _read_text(adapter, path)
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError:
        return None
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        name = str(getattr(node, "name", ""))
        if (name.startswith("test_") or name.startswith("Test")) and _test_body_references_symbol(node, symbol):
            start = int(getattr(node, "lineno", 1))
            end = int(getattr(node, "end_lineno", start))
            spans.append((start, end))
    return min(spans, key=lambda span: (span[1] - span[0], span[0])) if spans else None


def _stage_context_items(results: list[dict[str, Any]], *, adapter: ProjectAdapter) -> list[dict[str, Any]]:
    priority = {"source": 0, "test": 1, "doc": 2, "prompt": 3}
    items: list[dict[str, Any]] = []
    for item in sorted(results, key=lambda i: (priority.get(str(i.get("stage_field")), 9), i["path"])):
        current = dict(item)
        if current.get("stage_field") == "test":
            span = _focused_test_range(adapter, str(current["path"]), str(current.get("source_symbol") or ""))
            if span:
                current["start_line"], current["end_line"] = span
        items.append(current)
    return items


def _symbol_items(results: list[dict[str, Any]], *, adapter: ProjectAdapter) -> list[dict[str, Any]]:
    if not results:
        return []
    first = dict(results[0])
    items = [first]
    if first.get("related_test"):
        span = _focused_test_range(adapter, str(first["related_test"]), str(first.get("symbol") or ""))
        if span:
            start, end = span
            items.append({"kind": "related_test", "path": first["related_test"], "start_line": start, "end_line": end, "symbol": first.get("symbol"), "stage_field": "test"})
    return items


def _make_trace(project_id: str, route: str, context: list[dict[str, Any]], stop: str) -> dict[str, Any]:
    return {
        "schema": TRACE_SCHEMA, "project_id": project_id, "route": route,
        "candidate_ids": [item["id"] for item in context], "selected_source_ids": [item["id"] for item in context],
        "candidate_count": len(context), "selected_source_count": len(context), "expansion_hops": 0,
        "cross_project_notebooks": 0, "lines_loaded": sum(int(i.get("lines", 0)) for i in context),
        "characters_loaded": sum(int(i.get("characters", 0)) for i in context), "stop_reason": stop,
    }


def _base_report(project_id: str, route: str, stop: str, *, context: list[dict[str, Any]], definition_of_done: str, budget_status: str = "within_budget", denial: str | None = None) -> dict[str, Any]:
    report = {
        "schema": SCHEMA, "project_id": project_id, "route": route, "stop_reason": stop, "abstain": not context,
        "context": context, "candidates": [], "definition_of_done": definition_of_done, "budget_status": budget_status,
        "local_read_only": True, "production_proof": False, "trace": _make_trace(project_id, route, context, stop),
    }
    if denial:
        report["denial"] = denial
    return report


def _empty(project_id: str, route: str, definition_of_done: str, denial: str) -> dict[str, Any]:
    return _base_report(project_id, route, "no_candidate_answers", context=[], definition_of_done=definition_of_done, denial=denial)


def run_gate(question: str, *, project_id: str, evidence_type: str, definition_of_done: str, do_not_open: Iterable[str] = (), max_sources: int = 2, max_lines: int = 80, max_chars: int = 8000, target_kind: str | None = None, target: str | None = None, target_owner: str | None = None) -> dict[str, Any]:
    if evidence_type not in EVIDENCE_TYPES:
        raise GateError("unsupported evidence type")
    if target_kind not in STRICT_TARGET_KINDS or not target:
        raise GateError("project context gate requires exact --target-kind and --target")
    if target_kind != evidence_type:
        raise GateError("strict target_kind must match --evidence-type")
    if not definition_of_done.strip():
        raise GateError("definition_of_done is required")
    if not (1 <= max_sources <= 2 and 1 <= max_lines <= 80 and 1 <= max_chars <= 8000):
        raise GateError("budgets must be max-sources 1..2, max-lines 1..80, max-chars 1..8000")
    adapter = load_adapter(project_id)
    do_not = tuple(_normalize_block_pattern(item, root=adapter.root) for item in do_not_open)
    registry = navigation.build_registry(project_id)
    records = list(registry["records"])
    if target_kind == "stage":
        matches = [r for r in records if r.get("kind") == "stage" and r.get("stage_id") == target]
        if not matches:
            return _empty(project_id, "stage", definition_of_done, "strict_stage_target_not_found")
        context, budget = _select_context(_stage_context_items([navigation._result(r) for r in matches], adapter=adapter), adapter=adapter, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars, do_not_open=do_not)
        roles = {item.get("role") for item in context}
        stop = "context_budget_reached" if budget == "context_budget_reached" else ("owner_contract_and_test" if {"source", "test"} <= roles else "definition_of_done")
        return _base_report(project_id, "stage", stop, context=context, definition_of_done=definition_of_done, budget_status=budget)
    if target_kind == "symbol":
        owner = _norm_path(str(target_owner or ""))
        matches = [r for r in records if r.get("kind") == "symbol" and r.get("symbol") == target]
        if owner:
            if owner not in registry["active_paths"]:
                return _empty(project_id, "ast", definition_of_done, "strict_symbol_owner_not_active")
            matches = [r for r in matches if _norm_path(str(r.get("path"))) == owner]
        if not matches:
            return _empty(project_id, "ast", definition_of_done, "strict_symbol_target_not_found")
        if len(matches) > 1:
            return _empty(project_id, "ast", definition_of_done, "strict_symbol_target_ambiguous")
        context, budget = _select_context(_symbol_items([navigation._result(matches[0])], adapter=adapter), adapter=adapter, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars, do_not_open=do_not)
        roles = {item.get("role") for item in context}
        stop = "context_budget_reached" if budget == "context_budget_reached" else ("owner_contract_and_test" if {"symbol", "test"} <= roles or "test" in roles else "definition_of_done")
        return _base_report(project_id, "ast", stop, context=context, definition_of_done=definition_of_done, budget_status=budget)
    owner = _norm_path(str(target_owner or ""))
    if not owner:
        raise GateError("strict docs target requires --target-owner")
    if owner not in registry["active_paths"]:
        return _empty(project_id, "docs", definition_of_done, "strict_docs_owner_not_active")
    matches = [r for r in records if r.get("kind") == "doc_anchor" and _norm_path(str(r.get("path"))) == owner and str(r.get("anchor")) == target]
    if not matches:
        return _empty(project_id, "docs", definition_of_done, "strict_docs_anchor_not_found")
    context, budget = _select_context([navigation._result(matches[0])], adapter=adapter, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars, do_not_open=do_not)
    stop = "context_budget_reached" if budget == "context_budget_reached" else ("definition_of_done" if context else "no_candidate_answers")
    return _base_report(project_id, "docs", stop, context=context, definition_of_done=definition_of_done, budget_status=budget)


def render_human(report: dict[str, Any]) -> str:
    lines = [f"Project context-gate [{report['project_id']}]: route={report['route']} stop={report['stop_reason']}" + (" (abstain)" if report.get("abstain") else "")]
    for item in report.get("context", []):
        label = item.get("symbol") or item.get("stage_id") or item.get("anchor") or item.get("role")
        lines.append(f"- {item['path']}:{item['start_line']}-{item['end_line']} [{label}]")
    if report.get("denial"):
        lines.append(f"Denial: {report['denial']}")
    lines.append("Trace: " + json.dumps(report["trace"], ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Universal strict STOP-2 context gate")
    parser.add_argument("question")
    parser.add_argument("--project-id", required=True, choices=("nmbot", "qapairs", "cc2", "mpn"))
    parser.add_argument("--evidence-type", required=True, choices=sorted(EVIDENCE_TYPES))
    parser.add_argument("--definition-of-done", required=True)
    parser.add_argument("--do-not-open", action="append", default=[])
    parser.add_argument("--max-sources", type=int, default=2)
    parser.add_argument("--max-lines", type=int, default=80)
    parser.add_argument("--max-chars", type=int, default=8000)
    parser.add_argument("--target-kind", choices=sorted(STRICT_TARGET_KINDS), required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--target-owner")
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true")
    out.add_argument("--human", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = run_gate(args.question, project_id=args.project_id, evidence_type=args.evidence_type, definition_of_done=args.definition_of_done, do_not_open=args.do_not_open, max_sources=args.max_sources, max_lines=args.max_lines, max_chars=args.max_chars, target_kind=args.target_kind, target=args.target, target_owner=args.target_owner)
    except (GateError, AdapterError, navigation.NavigationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) if args.json else render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
