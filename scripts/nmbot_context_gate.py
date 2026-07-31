#!/usr/bin/env python3
"""Local STOP-2 context gate for NMBot developer retrieval.

This command is stdlib-only and read-only. It validates an explicit Stage 0
request, chooses one bounded local route or a handoff/denial route, calls
``nmbot_navigation`` at most once, and emits a privacy-safe
``bounded-retrieval.v1`` trace with counts/IDs only.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path("config/nmbot_retrieval_sources.json")
DEFAULT_INTENTS = Path("config/nmbot_context_gate_intents.json")
SCHEMA = "nmbot.context_gate.v1"
TRACE_SCHEMA = "bounded-retrieval.v1"
INTENT_SCHEMA = "nmbot.context_gate_intents.v1"
LOCAL_PROJECT = "nmbot"
LOCAL_NOTEBOOK = "nmbot"

EVIDENCE_TYPES = {
    "stage", "symbol", "current-source", "docs", "history", "production", "ambiguous",
}
ROUTES = {
    "stage", "ast", "current_source", "docs", "canonical_notebook_handoff",
    "fresh_authorized_production_handoff", "clarify_evidence_type",
    "deep_audit_handoff", "fail_closed_cross_project", "approved_one_hop_dependency",
    "bounded_fallback",
}
STOP_REASONS = {
    "definition_of_done", "two_primary_sources_agree", "owner_contract_and_test",
    "no_candidate_answers", "expansion_exhausted", "context_budget_reached",
    "source_conflict_requires_decision", "topic_changed_follow_up", "deep_audit_required",
}
FOREIGN_PROJECTS = {
    "qapairs", "qapairs-daemon", "mpn", "cc2", "novostroy-m", "novostroy-ai",
    "n8n_audit", "n8n-audit", "opencode", "cc-daemons", "cc_daemons",
}
AUDIT_WORDS = {"full", "all", "audit", "inventory", "migration", "полный", "весь", "все", "всё", "полностью", "аудит", "инвентар"}
STAGE_TOKEN_RE = re.compile(r"\b(?:v2|jivo)\.[A-Za-z0-9_.-]+\b")
SAFE_DEP_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,119}$")
SECRETISH_RE = re.compile(r"(?:secret|token|password|passwd|api[_-]?key|credential)", re.IGNORECASE)
INTENT_EVIDENCE_TYPES = {"stage", "symbol", "current-source", "docs"}
INTENT_CARD_KEYS = {"id", "evidence_type", "match_all", "resolver_query", "purpose", "owner_path"}
STRICT_TARGET_KINDS = {"stage", "symbol", "docs"}


class GateError(ValueError):
    """Human-readable CLI validation error."""


def _load_navigation_module():
    path = ROOT / "scripts" / "nmbot_navigation.py"
    spec = importlib.util.spec_from_file_location("nmbot_navigation_gate", path)
    if spec is None or spec.loader is None:
        raise GateError("cannot load nmbot_navigation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _norm_path(path: str) -> str:
    return Path(path).as_posix().lstrip("./")


def _normalize_block_pattern(pattern: str, *, root: Path) -> str:
    raw = str(pattern or "").strip()
    if not raw:
        raise GateError("do_not_open pattern must be non-empty")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise GateError(f"do_not_open pattern must be repo-relative and stay inside repo: {pattern}")
    normalized = _norm_path(raw)
    if normalized in {"", "."}:
        raise GateError("do_not_open pattern must name a repo path")
    # Validate the non-glob prefix without requiring the matched file to exist.
    prefix_parts: list[str] = []
    for part in Path(normalized).parts:
        if any(ch in part for ch in "*?["):
            break
        prefix_parts.append(part)
    if prefix_parts:
        prefix = (root / Path(*prefix_parts)).resolve()
        try:
            prefix.relative_to(root.resolve())
        except ValueError as exc:
            raise GateError(f"do_not_open pattern escapes repo: {pattern}") from exc
    return normalized


def _normalize_block_patterns(patterns: Iterable[str], *, root: Path) -> tuple[str, ...]:
    return tuple(_normalize_block_pattern(item, root=root) for item in patterns)


def _is_blocked(path: str, patterns: Iterable[str]) -> bool:
    normalized = _norm_path(path)
    return any(normalized == pattern or fnmatch.fnmatchcase(normalized, pattern) for pattern in patterns)


def _repo_path(path_text: str, *, root: Path) -> Path:
    path = Path(path_text)
    if path.is_absolute() or ".." in path.parts:
        raise GateError(f"path must be relative and stay inside repo: {path_text}")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise GateError(f"path escapes repo: {path_text}") from exc
    return resolved


def _line_count(path: str, *, root: Path) -> int:
    return max(1, len(_repo_path(path, root=root).read_text(encoding="utf-8").splitlines()))


def _range_for(item: dict[str, Any], *, root: Path, max_lines: int) -> tuple[int, int, bool]:
    total = _line_count(str(item["path"]), root=root)
    start = max(1, int(item.get("start_line") or 1))
    raw_end = max(start, int(item.get("end_line") or start))
    available_end = min(total, raw_end)
    end = min(available_end, start + max_lines - 1)
    return start, end, end < available_end


def _range_stats(path: str, start: int, end: int, *, root: Path) -> tuple[int, int]:
    lines = _repo_path(path, root=root).read_text(encoding="utf-8").splitlines()[start - 1:end]
    return len(lines), len("\n".join(lines))


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-zА-Яа-яЁё0-9_-]{2,}", text.lower()))


def _normalize_intent_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


def _intent_phrase_present(question: str, phrase: str) -> bool:
    normalized_question = _normalize_intent_text(question)
    normalized_phrase = _normalize_intent_text(phrase)
    if not normalized_phrase:
        return False
    if re.search(r"\s", normalized_phrase):
        return normalized_phrase in normalized_question
    return normalized_phrase in set(re.findall(r"[A-Za-zА-Яа-яЁё0-9_.-]+", normalized_question))


def _foreign_mentions(question: str) -> list[str]:
    tokens = _tokens(question.replace("_", "-")) | _tokens(question)
    return sorted(slug for slug in FOREIGN_PROJECTS if slug.lower() in tokens)


def _has_audit_request(question: str) -> bool:
    return bool(_tokens(question) & AUDIT_WORDS)


def _make_trace(project_id: str, route: str, candidates: list[str], selected: list[str], *, lines: int, chars: int, stop_reason: str, cross_project: int = 0, expansion_hops: int = 0) -> dict[str, Any]:
    if route not in ROUTES:
        raise GateError(f"unsupported route: {route}")
    if stop_reason not in STOP_REASONS:
        raise GateError(f"unsupported stop_reason: {stop_reason}")
    return {
        "schema": TRACE_SCHEMA,
        "project_id": project_id,
        "route": route,
        "candidate_ids": candidates,
        "selected_source_ids": selected,
        "candidate_count": len(candidates),
        "selected_source_count": len(selected),
        "expansion_hops": expansion_hops,
        "cross_project_notebooks": cross_project,
        "lines_loaded": lines,
        "characters_loaded": chars,
        "stop_reason": stop_reason,
    }


def _base_report(project_id: str, route: str, stop_reason: str, *, abstain: bool, context: list[dict[str, Any]] | None = None, candidates: list[dict[str, Any]] | None = None, handoff: dict[str, Any] | None = None, denial: str | None = None, follow_up: str | None = None, definition_of_done: str = "", budget_status: str = "within_budget", cross_project: int = 0) -> dict[str, Any]:
    if route not in ROUTES:
        raise GateError(f"unsupported route: {route}")
    if stop_reason not in STOP_REASONS:
        raise GateError(f"unsupported stop_reason: {stop_reason}")
    context = context or []
    candidates = candidates or []
    lines = sum(int(item.get("lines", 0)) for item in context)
    chars = sum(int(item.get("characters", 0)) for item in context)
    selected_ids = [item["id"] for item in context]
    candidate_ids = [item["id"] for item in candidates] or selected_ids
    report = {
        "schema": SCHEMA,
        "project_id": project_id,
        "route": route,
        "stop_reason": stop_reason,
        "abstain": abstain,
        "context": context,
        "candidates": candidates,
        "definition_of_done": definition_of_done,
        "budget_status": budget_status,
        "local_read_only": True,
        "production_proof": False,
        "trace": _make_trace(project_id, route, candidate_ids, selected_ids, lines=lines, chars=chars, stop_reason=stop_reason, cross_project=cross_project),
    }
    if handoff:
        report["handoff"] = handoff
    if denial:
        report["denial"] = denial
    if follow_up:
        report["follow_up"] = follow_up
    return report


def _source_id(item: dict[str, Any], start: int, end: int) -> str:
    label = item.get("stage_id") or item.get("symbol") or item.get("anchor") or item.get("contract_ref") or item.get("kind") or "source"
    return f"{item['path']}:{start}-{end}:{label}"


def _select_context(items: Iterable[dict[str, Any]], *, root: Path, max_sources: int, max_lines: int, max_chars: int, do_not_open: Iterable[str]) -> tuple[list[dict[str, Any]], str]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    used_lines = 0
    used_chars = 0
    budget_status = "within_budget"
    for item in items:
        path = _norm_path(str(item["path"]))
        if _is_blocked(path, do_not_open) or path in seen:
            continue
        start, end, range_truncated = _range_for(item, root=root, max_lines=max_lines)
        if range_truncated:
            budget_status = "context_budget_reached"
        lines, chars = _range_stats(path, start, end, root=root)
        if selected and (used_lines + lines > max_lines or used_chars + chars > max_chars):
            budget_status = "context_budget_reached"
            break
        if not selected and (lines > max_lines or chars > max_chars):
            budget_status = "context_budget_reached"
            while lines > 1 and (lines > max_lines or chars > max_chars):
                end -= 1
                lines, chars = _range_stats(path, start, end, root=root)
            if lines > max_lines or chars > max_chars:
                break
        entry = {
            "id": _source_id({**item, "path": path}, start, end),
            "path": path,
            "start_line": start,
            "end_line": end,
            "lines": lines,
            "characters": chars,
            "role": item.get("stage_field") or item.get("kind"),
            "evidence": not item.get("candidate_only", False),
        }
        for key in ("stage_id", "source_symbol", "symbol", "anchor", "related_test"):
            if item.get(key):
                entry[key] = item[key]
        selected.append(entry)
        seen.add(path)
        used_lines += lines
        used_chars += chars
        if len(selected) >= max_sources:
            break
    return selected, budget_status


def _candidate_list(items: Iterable[dict[str, Any]], *, max_sources: int, do_not_open: Iterable[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        path = _norm_path(str(item["path"]))
        if _is_blocked(path, do_not_open) or path in seen:
            continue
        seen.add(path)
        start = int(item.get("start_line") or 1)
        end = int(item.get("end_line") or start)
        out.append({
            "id": _source_id({**item, "path": path}, start, end),
            "path": path,
            "start_line": start,
            "end_line": end,
            "candidate_only": True,
        })
        if len(out) >= max_sources:
            break
    return out


def _ordered_stage_items(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {"source": 0, "test": 1, "doc": 2, "prompt": 3}
    return sorted(results, key=lambda item: (priority.get(str(item.get("stage_field")), 9), item["path"]))


def _focused_test_range(path: str, symbol: str | None, *, root: Path) -> tuple[int, int] | None:
    text = _repo_path(path, root=root).read_text(encoding="utf-8")
    needle = str(symbol or "")
    if needle:
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError:
            tree = None
        spans: list[tuple[int, int]] = []
        if tree is not None:
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                name = str(getattr(node, "name", ""))
                is_test = name.startswith("test_") or name.startswith("Test")
                if not is_test:
                    continue
                start = int(getattr(node, "lineno", 1))
                end = int(getattr(node, "end_lineno", start))
                if _test_body_references_symbol(node, needle):
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


def _symbol_items(results: list[dict[str, Any]], *, root: Path) -> list[dict[str, Any]]:
    if not results:
        return []
    first = dict(results[0])
    items = [first]
    if first.get("related_test"):
        test_path = str(first["related_test"])
        span = _focused_test_range(test_path, first.get("symbol"), root=root)
        if span is not None:
            start, end = span
            items.append({"kind": "related_test", "path": test_path, "start_line": start, "end_line": end, "symbol": first.get("symbol"), "stage_field": "test"})
    return items


def _stage_context_items(results: list[dict[str, Any]], *, root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _ordered_stage_items(results):
        current = dict(item)
        source_symbol = current.get("source_symbol")
        if current.get("stage_field") == "test" and isinstance(source_symbol, str) and source_symbol:
            span = _focused_test_range(str(current["path"]), source_symbol, root=root)
            if span is None:
                continue
            start, end = span
            current["start_line"] = start
            current["end_line"] = end
        items.append(current)
    return items


def _current_source_items(nav_report: dict[str, Any]) -> list[dict[str, Any]]:
    results = list(nav_report.get("results") or [])
    if nav_report.get("route") == "ast" and results:
        return results
    exact_path = next((item for item in results if not item.get("candidate_only") and item.get("path")), None)
    return [exact_path] if exact_path else []


def _approved_dependency_report(project_id: str, dep: dict[str, Any], definition_of_done: str) -> dict[str, Any]:
    contract_ref = dep["contract_ref"]
    context = [{
        "id": f"dependency:{dep['owner_project']}:{contract_ref}",
        "contract_ref": contract_ref,
        "owner_project": dep["owner_project"],
        "consumer_project": dep["consumer_project"],
        "canonical_notebook": dep["canonical_notebook"],
        "max_depth": dep["max_depth"],
        "max_records": dep["max_records"],
        "evidence": False,
    }]
    report = _base_report(project_id, "approved_one_hop_dependency", "expansion_exhausted", abstain=True, context=[], definition_of_done=definition_of_done, handoff={"project_id": dep["owner_project"], "canonical_notebook": dep["canonical_notebook"], "contract_ref": contract_ref, "max_depth": 1, "max_records": dep["max_records"]}, cross_project=1)
    report["dependency"] = context[0]
    report["trace"] = _make_trace(project_id, "approved_one_hop_dependency", [context[0]["id"]], [], lines=0, chars=0, stop_reason="expansion_exhausted", cross_project=1, expansion_hops=1)
    return report


def validate_dependency(dep: dict[str, Any] | None) -> dict[str, Any] | None:
    if not dep:
        return None
    allowed = {"scope_id", "owner_project", "consumer_project", "canonical_notebook", "contract_ref", "max_depth", "max_records", "reason", "allowed_query_types"}
    extra = sorted(set(dep) - allowed)
    if extra:
        raise GateError("approved dependency contains unsupported fields: " + ", ".join(extra))
    required = ("scope_id", "owner_project", "consumer_project", "canonical_notebook", "contract_ref")
    missing = [key for key in required if not str(dep.get(key) or "").strip()]
    if missing:
        raise GateError("approved dependency missing required fields: " + ", ".join(missing))
    dep = dict(dep)
    for key in ("scope_id", "owner_project", "consumer_project", "canonical_notebook", "contract_ref"):
        value = dep[key]
        if not isinstance(value, str):
            raise GateError(f"approved dependency {key} must be a string")
        dep[key] = value.strip()
        if not _is_safe_dependency_ref(dep[key]):
            raise GateError(f"approved dependency {key} must be a compact safe ref")
    if "reason" in dep:
        if not isinstance(dep["reason"], str) or len(dep["reason"].strip()) > 240:
            raise GateError("approved dependency reason must be a bounded string")
        dep["reason"] = dep["reason"].strip()
    if "allowed_query_types" in dep:
        query_types = dep["allowed_query_types"]
        if not isinstance(query_types, list) or len(query_types) > 8:
            raise GateError("approved dependency allowed_query_types must be a bounded list")
        clean_query_types = []
        for item in query_types:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 40 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", item.strip()):
                raise GateError("approved dependency allowed_query_types entries must be compact strings")
            clean_query_types.append(item.strip())
        dep["allowed_query_types"] = clean_query_types
    dep["max_depth"] = int(dep.get("max_depth", 1))
    dep["max_records"] = int(dep.get("max_records", 2))
    if dep["consumer_project"] != LOCAL_PROJECT:
        raise GateError("approved dependency consumer_project must be nmbot")
    if dep["max_depth"] != 1:
        raise GateError("approved dependency max_depth must be 1")
    if not (1 <= dep["max_records"] <= 2):
        raise GateError("approved dependency max_records must be 1..2")
    if dep["owner_project"] == LOCAL_PROJECT or dep["owner_project"] not in FOREIGN_PROJECTS:
        raise GateError("approved dependency owner_project must be known foreign project")
    return dep


def _is_safe_dependency_ref(value: str) -> bool:
    if not value or len(value) > 120:
        return False
    if "://" in value or SECRETISH_RE.search(value):
        return False
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return False
    return bool(SAFE_DEP_REF_RE.fullmatch(value))


def _load_intent_payload(path: Path, *, root: Path) -> dict[str, Any] | None:
    target = _repo_path(path.as_posix(), root=root)
    if not target.exists():
        return None
    if not target.is_file():
        raise GateError(f"intent registry path is not a file: {path.as_posix()}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GateError("intent registry JSON is invalid") from exc
    if not isinstance(data, dict) or data.get("schema") != INTENT_SCHEMA:
        raise GateError(f"intent registry schema must be {INTENT_SCHEMA}")
    cards = data.get("cards")
    if not isinstance(cards, list) or len(cards) > 30:
        raise GateError("intent registry cards must be a list of at most 30 cards")
    return data


def _active_paths_from_navigation(navigation: Any, *, root: Path, manifest_path: Path) -> set[str]:
    return {str(item["path"]) for item in navigation.load_active_manifest(manifest_path, root=root)}


def _card_ast_symbol_exists(owner_path: str, symbol: str, *, root: Path) -> bool:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
        return False
    text = _repo_path(owner_path, root=root).read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=owner_path)
    except SyntaxError as exc:
        raise GateError(f"intent owner_path AST parse failed: {owner_path}:{exc.lineno}") from exc
    return any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol for node in ast.walk(tree))


def _validate_intent_card(card: Any, *, navigation: Any, root: Path, manifest_path: Path, active_paths: set[str], stage_map: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(card, dict):
        raise GateError("intent card must be an object")
    extra = sorted(set(card) - INTENT_CARD_KEYS)
    missing = sorted(INTENT_CARD_KEYS - set(card))
    if extra or missing:
        raise GateError("intent card keys must be exact: " + ", ".join(sorted(INTENT_CARD_KEYS)))
    clean: dict[str, Any] = {}
    for key in ("id", "evidence_type", "resolver_query", "purpose", "owner_path"):
        value = card[key]
        if not isinstance(value, str) or not value.strip():
            raise GateError(f"intent card {key} must be a non-empty string")
        value = value.strip()
        if len(value) > 240 or SECRETISH_RE.search(value):
            raise GateError(f"intent card {key} must be bounded and non-secret")
        clean[key] = value
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", clean["id"]):
        raise GateError("intent card id must be a compact safe id")
    if clean["evidence_type"] not in INTENT_EVIDENCE_TYPES:
        raise GateError("intent card evidence_type must be stage, symbol, current-source or docs")
    owner_path = _norm_path(clean["owner_path"])
    if owner_path not in active_paths:
        raise GateError(f"intent card owner_path is not active manifest path: {owner_path}")
    clean["owner_path"] = owner_path
    match_all = card["match_all"]
    if not isinstance(match_all, list) or not (1 <= len(match_all) <= 5):
        raise GateError("intent card match_all must contain 1..5 terms")
    clean_terms: list[str] = []
    for item in match_all:
        if not isinstance(item, str) or not _normalize_intent_text(item):
            raise GateError("intent card match_all entries must be non-empty strings")
        term = _normalize_intent_text(item)
        if len(term) > 80 or SECRETISH_RE.search(term):
            raise GateError("intent card match_all entries must be bounded and non-secret")
        clean_terms.append(term)
    clean["match_all"] = clean_terms
    resolver_query = clean["resolver_query"]
    evidence_type = clean["evidence_type"]
    if evidence_type == "stage":
        if resolver_query not in stage_map["stages"] and resolver_query not in stage_map["paths"]:
            raise GateError(f"intent card stage resolver_query is not in stage map: {resolver_query}")
    elif evidence_type in {"symbol", "current-source"}:
        if not _card_ast_symbol_exists(owner_path, resolver_query, root=root):
            raise GateError(f"intent card resolver_query symbol_missing:{owner_path}:{resolver_query}")
    elif evidence_type == "docs":
        try:
            nav = navigation.resolve_doc_anchor(resolver_query, owner_path, root=root, manifest_path=manifest_path)
        except Exception as exc:  # navigation exposes its own ValueError subclass
            raise GateError(f"intent card docs resolver_query found no active anchor in owner_path: {owner_path}") from exc
        if nav.get("route") != "docs" or not any(_norm_path(str(item.get("path", ""))) == owner_path for item in nav.get("results") or []):
            raise GateError(f"intent card docs resolver_query found no active anchor in owner_path: {owner_path}")
    return clean


def _load_intent_cards(*, navigation: Any, root: Path, manifest_path: Path, intents_path: Path | None, evidence_type: str) -> list[dict[str, Any]]:
    if intents_path is None:
        return []
    payload = _load_intent_payload(intents_path, root=root)
    if payload is None:
        return []
    active_paths = _active_paths_from_navigation(navigation, root=root, manifest_path=manifest_path)
    stage_map = navigation.load_stage_map(root=root)
    cards = [
        _validate_intent_card(card, navigation=navigation, root=root, manifest_path=manifest_path, active_paths=active_paths, stage_map=stage_map)
        for card in payload["cards"]
        if isinstance(card, dict) and card.get("evidence_type") == evidence_type
    ]
    ids = [card["id"] for card in cards]
    if len(ids) != len(set(ids)):
        raise GateError("intent card ids must be unique")
    return cards


def _match_intent_card(question: str, evidence_type: str, cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [card for card in cards if card["evidence_type"] == evidence_type and all(_intent_phrase_present(question, term) for term in card["match_all"])]
    if not matches:
        return None
    matches.sort(key=lambda card: (-len(card["match_all"]), card["id"]))
    return matches[0]


def _apply_intent_card(report: dict[str, Any], card: dict[str, Any] | None) -> dict[str, Any]:
    if not card:
        return report
    report["intent_card_id"] = card["id"]
    report["trace"]["intent_card_id"] = card["id"]
    return report


def _empty_strict_report(project_id: str, route: str, definition_of_done: str, *, candidates: list[dict[str, Any]] | None = None, denial: str | None = None) -> dict[str, Any]:
    return _base_report(project_id, route, "no_candidate_answers", abstain=True, candidates=candidates or [], definition_of_done=definition_of_done, denial=denial)


def _strict_stage_report(target: str, *, navigation: Any, project_id: str, definition_of_done: str, do_not: Iterable[str], root: Path, manifest_path: Path, max_sources: int, max_lines: int, max_chars: int) -> dict[str, Any]:
    stage_map = navigation.load_stage_map(root=root)
    if target in stage_map["paths"]:
        return _empty_strict_report(project_id, "stage", definition_of_done, denial="strict_stage_path_target_too_broad")
    if target not in stage_map["stages"]:
        return _empty_strict_report(project_id, "stage", definition_of_done, denial="strict_stage_target_not_found")
    nav = navigation.navigate(target, root=root, manifest_path=manifest_path)
    if nav.get("route") != "stage":
        return _empty_strict_report(project_id, "stage", definition_of_done, denial="strict_stage_target_not_found")
    context, budget = _select_context(_stage_context_items(list(nav.get("results") or []), root=root), root=root, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars, do_not_open=do_not)
    roles = {item.get("role") for item in context}
    stop = "context_budget_reached" if budget == "context_budget_reached" else ("owner_contract_and_test" if {"source", "test"} <= roles else "definition_of_done")
    return _base_report(project_id, "stage", stop, abstain=not context, context=context, definition_of_done=definition_of_done, budget_status=budget)


def _strict_symbol_report(target: str, owner_path: str | None, *, navigation: Any, project_id: str, definition_of_done: str, do_not: Iterable[str], root: Path, manifest_path: Path, max_sources: int, max_lines: int, max_chars: int) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target):
        raise GateError("strict symbol target must be a Python identifier")
    registry = navigation.build_registry(root=root, manifest_path=manifest_path)
    matches = [r for r in registry["records"] if r.get("kind") == "symbol" and r.get("symbol") == target]
    if owner_path:
        owner = _norm_path(str(owner_path).strip())
        if owner not in registry["active_paths"]:
            return _empty_strict_report(project_id, "ast", definition_of_done, denial="strict_symbol_owner_not_active")
        matches = [r for r in matches if _norm_path(str(r.get("path"))) == owner]
    if not matches:
        return _empty_strict_report(project_id, "ast", definition_of_done, denial="strict_symbol_target_not_found")
    if len(matches) > 1:
        return _empty_strict_report(project_id, "ast", definition_of_done, candidates=_candidate_list(matches, max_sources=max_sources, do_not_open=do_not), denial="strict_symbol_target_ambiguous")
    context, budget = _select_context(_symbol_items([matches[0]], root=root), root=root, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars, do_not_open=do_not)
    roles = {item.get("role") for item in context}
    stop = "context_budget_reached" if budget == "context_budget_reached" else ("owner_contract_and_test" if {"symbol", "test"} <= roles else "definition_of_done")
    return _base_report(project_id, "ast", stop, abstain=not context, context=context, definition_of_done=definition_of_done, budget_status=budget)


def _strict_docs_report(target: str, owner_path: str | None, *, navigation: Any, project_id: str, definition_of_done: str, do_not: Iterable[str], root: Path, manifest_path: Path, max_sources: int, max_lines: int, max_chars: int) -> dict[str, Any]:
    if not owner_path:
        raise GateError("strict docs target requires --target-owner")
    owner = _norm_path(str(owner_path).strip())
    registry = navigation.build_registry(root=root, manifest_path=manifest_path)
    if owner not in registry["active_paths"]:
        return _empty_strict_report(project_id, "docs", definition_of_done, denial="strict_docs_owner_not_active")
    exact = [r for r in registry["records"] if r.get("kind") == "doc_anchor" and _norm_path(str(r.get("path"))) == owner and str(r.get("anchor")) == target]
    if exact:
        nav_results = exact[:1]
    elif target.lstrip().startswith("#"):
        return _empty_strict_report(project_id, "docs", definition_of_done, denial="strict_docs_anchor_not_found")
    else:
        try:
            nav = navigation.resolve_doc_anchor(target, owner, root=root, manifest_path=manifest_path)
        except Exception:
            return _empty_strict_report(project_id, "docs", definition_of_done, denial="strict_docs_anchor_not_found")
        nav_results = [r for r in list(nav.get("results") or []) if _norm_path(str(r.get("path"))) == owner]
    if not nav_results:
        return _empty_strict_report(project_id, "docs", definition_of_done, denial="strict_docs_anchor_not_found")
    context, budget = _select_context(nav_results, root=root, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars, do_not_open=do_not)
    stop = "context_budget_reached" if budget == "context_budget_reached" else ("definition_of_done" if context else "no_candidate_answers")
    return _base_report(project_id, "docs", stop, abstain=not context, context=context, definition_of_done=definition_of_done, budget_status=budget)


def _strict_target_report(*, target_kind: str, target: str, target_owner: str | None, project_id: str, evidence_type: str, definition_of_done: str, do_not: Iterable[str], root: Path, manifest_path: Path, max_sources: int, max_lines: int, max_chars: int) -> dict[str, Any]:
    if target_kind not in STRICT_TARGET_KINDS:
        raise GateError("strict target_kind must be stage, symbol or docs")
    if evidence_type != target_kind:
        raise GateError("strict target_kind must match --evidence-type")
    if not target.strip():
        raise GateError("strict target is required")
    navigation = _load_navigation_module()
    if target_kind == "stage":
        return _strict_stage_report(target.strip(), navigation=navigation, project_id=project_id, definition_of_done=definition_of_done, do_not=do_not, root=root, manifest_path=manifest_path, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars)
    if target_kind == "symbol":
        return _strict_symbol_report(target.strip(), target_owner, navigation=navigation, project_id=project_id, definition_of_done=definition_of_done, do_not=do_not, root=root, manifest_path=manifest_path, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars)
    return _strict_docs_report(target.strip(), target_owner, navigation=navigation, project_id=project_id, definition_of_done=definition_of_done, do_not=do_not, root=root, manifest_path=manifest_path, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars)


def run_gate(question: str, *, project_id: str, evidence_type: str, definition_of_done: str, do_not_open: Iterable[str] = (), max_sources: int = 2, max_lines: int = 80, max_chars: int = 8000, dependency: dict[str, Any] | None = None, root: Path = ROOT, manifest_path: Path = DEFAULT_MANIFEST, intents_path: Path | None = DEFAULT_INTENTS, target_kind: str | None = None, target: str | None = None, target_owner: str | None = None) -> dict[str, Any]:
    if project_id != LOCAL_PROJECT:
        raise GateError("only local project_id nmbot is supported")
    if evidence_type not in EVIDENCE_TYPES:
        raise GateError("unsupported evidence type")
    if not question.strip():
        raise GateError("question is required")
    if not definition_of_done.strip():
        raise GateError("definition_of_done is required")
    if not (1 <= max_sources <= 2 and 1 <= max_lines <= 80 and 1 <= max_chars <= 8000):
        raise GateError("budgets must be max-sources 1..2, max-lines 1..80, max-chars 1..8000")

    do_not = _normalize_block_patterns(do_not_open, root=root)
    dep = validate_dependency(dependency)
    if dep:
        return _approved_dependency_report(project_id, dep, definition_of_done)

    if target_kind is not None or target is not None or target_owner is not None:
        if target_kind is None or target is None:
            raise GateError("strict mode requires both --target-kind and --target")
        return _strict_target_report(target_kind=target_kind, target=target, target_owner=target_owner, project_id=project_id, evidence_type=evidence_type, definition_of_done=definition_of_done, do_not=do_not, root=root, manifest_path=manifest_path, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars)

    foreign = _foreign_mentions(question)
    if foreign:
        return _base_report(project_id, "fail_closed_cross_project", "no_candidate_answers", abstain=True, definition_of_done=definition_of_done, denial="cross_project_dependency_not_allowed", handoff={"mentions": foreign, "required": "exact approved dependency card"})

    if evidence_type == "history":
        return _base_report(project_id, "canonical_notebook_handoff", "definition_of_done", abstain=True, definition_of_done=definition_of_done, handoff={"project_id": LOCAL_PROJECT, "canonical_notebook": LOCAL_NOTEBOOK, "reason": "history requires canonical notebook; gate does not call NotebookLM"})
    if evidence_type == "production":
        return _base_report(project_id, "fresh_authorized_production_handoff", "definition_of_done", abstain=True, definition_of_done=definition_of_done, handoff={"required": "fresh explicitly authorized production check", "reason": "local files are not production proof"})
    if evidence_type == "ambiguous" and _has_audit_request(question):
        return _base_report(project_id, "deep_audit_handoff", "deep_audit_required", abstain=True, definition_of_done=definition_of_done, handoff={"required": "separate deep-audit contract with named scope/artifact"})
    if evidence_type == "ambiguous" and not STAGE_TOKEN_RE.search(question):
        return _base_report(project_id, "clarify_evidence_type", "no_candidate_answers", abstain=True, definition_of_done=definition_of_done, denial="route_ambiguous — choose evidence type")

    navigation = _load_navigation_module()
    intent_card = _match_intent_card(question, evidence_type, _load_intent_cards(navigation=navigation, root=root, manifest_path=manifest_path, intents_path=intents_path, evidence_type=evidence_type))
    nav_query = str(intent_card["resolver_query"] if intent_card else question)
    try:
        if intent_card and evidence_type == "docs":
            nav = navigation.resolve_doc_anchor(nav_query, intent_card["owner_path"], root=root, manifest_path=manifest_path)
        else:
            nav = navigation.navigate(nav_query, root=root, manifest_path=manifest_path)
    except Exception as exc:  # navigation exposes its own ValueError subclass
        raise GateError(str(exc)) from exc

    nav_route = nav.get("route")
    nav_results = list(nav.get("results") or [])

    if evidence_type == "ambiguous" and nav_route == "stage":
        context, budget = _select_context(_stage_context_items(nav_results, root=root), root=root, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars, do_not_open=do_not)
        stop = "context_budget_reached" if budget == "context_budget_reached" else "topic_changed_follow_up"
        return _apply_intent_card(_base_report(project_id, "stage", stop, abstain=not context, context=context, definition_of_done=definition_of_done, budget_status=budget, follow_up="unrelated remainder must start a new retrieval contract"), intent_card)

    if evidence_type == "stage":
        if nav_route != "stage":
            return _apply_intent_card(_base_report(project_id, "bounded_fallback", "no_candidate_answers", abstain=True, definition_of_done=definition_of_done, candidates=_candidate_list(nav_results, max_sources=max_sources, do_not_open=do_not)), intent_card)
        context, budget = _select_context(_stage_context_items(nav_results, root=root), root=root, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars, do_not_open=do_not)
        roles = {item.get("role") for item in context}
        stop = "context_budget_reached" if budget == "context_budget_reached" else ("owner_contract_and_test" if {"source", "test"} <= roles else "definition_of_done")
        return _apply_intent_card(_base_report(project_id, "stage", stop, abstain=not context, context=context, definition_of_done=definition_of_done, budget_status=budget), intent_card)

    if evidence_type == "symbol":
        if nav_route != "ast":
            return _apply_intent_card(_base_report(project_id, "bounded_fallback", "no_candidate_answers", abstain=True, definition_of_done=definition_of_done, candidates=_candidate_list(nav_results, max_sources=max_sources, do_not_open=do_not)), intent_card)
        context, budget = _select_context(_symbol_items(nav_results, root=root), root=root, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars, do_not_open=do_not)
        roles = {item.get("role") for item in context}
        stop = "context_budget_reached" if budget == "context_budget_reached" else ("owner_contract_and_test" if {"symbol", "test"} <= roles or "test" in roles else "definition_of_done")
        return _apply_intent_card(_base_report(project_id, "ast", stop, abstain=not context, context=context, definition_of_done=definition_of_done, budget_status=budget), intent_card)

    if evidence_type == "current-source":
        context, budget = _select_context(_symbol_items(_current_source_items(nav), root=root), root=root, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars, do_not_open=do_not)
        if context:
            roles = {item.get("role") for item in context}
            stop = "context_budget_reached" if budget == "context_budget_reached" else ("owner_contract_and_test" if {"symbol", "test"} <= roles or "test" in roles else "definition_of_done")
            return _apply_intent_card(_base_report(project_id, "current_source", stop, abstain=False, context=context, definition_of_done=definition_of_done, budget_status=budget), intent_card)
        return _apply_intent_card(_base_report(project_id, "bounded_fallback", "no_candidate_answers", abstain=True, definition_of_done=definition_of_done, candidates=_candidate_list(nav_results, max_sources=max_sources, do_not_open=do_not)), intent_card)

    if evidence_type == "docs":
        if nav_route != "docs":
            return _apply_intent_card(_base_report(project_id, "docs", "no_candidate_answers", abstain=True, definition_of_done=definition_of_done), intent_card)
        context, budget = _select_context(nav_results, root=root, max_sources=max_sources, max_lines=max_lines, max_chars=max_chars, do_not_open=do_not)
        stop = "context_budget_reached" if budget == "context_budget_reached" else ("definition_of_done" if context else "no_candidate_answers")
        return _apply_intent_card(_base_report(project_id, "docs", stop, abstain=not context, context=context, definition_of_done=definition_of_done, budget_status=budget), intent_card)

    candidates = _candidate_list(nav_results, max_sources=max_sources, do_not_open=do_not)
    return _apply_intent_card(_base_report(project_id, "bounded_fallback", "no_candidate_answers", abstain=True, candidates=candidates, definition_of_done=definition_of_done), intent_card)


def render_human(report: dict[str, Any]) -> str:
    flags = []
    if report.get("abstain"):
        flags.append("abstain")
    suffix = f" ({', '.join(flags)})" if flags else ""
    lines = [f"NMBot context-gate: route={report['route']} stop={report['stop_reason']}{suffix}"]
    for item in report.get("context", []):
        label = item.get("symbol") or item.get("stage_id") or item.get("anchor") or item.get("role")
        lines.append(f"- {item['path']}:{item['start_line']}-{item['end_line']} [{label}]")
    for item in report.get("candidates", []):
        lines.append(f"- candidate-only: {item['path']}:{item['start_line']}-{item['end_line']}")
    if report.get("handoff"):
        lines.append("Handoff: " + json.dumps(report["handoff"], ensure_ascii=False, sort_keys=True))
    if report.get("denial"):
        lines.append(f"Denial: {report['denial']}")
    if report.get("follow_up"):
        lines.append(f"Follow-up: {report['follow_up']}")
    lines.append("Trace: " + json.dumps(report["trace"], ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


def _dependency_from_args(args: argparse.Namespace) -> dict[str, Any] | None:
    dep: dict[str, Any] = {}
    if args.approved_dependency_json:
        try:
            raw = json.loads(args.approved_dependency_json)
        except json.JSONDecodeError as exc:
            raise GateError("approved dependency JSON is invalid") from exc
        if not isinstance(raw, dict):
            raise GateError("approved dependency JSON must be an object")
        dep.update(raw)
    for src, key in (
        (args.dependency_scope_id, "scope_id"),
        (args.dependency_owner_project, "owner_project"),
        (args.dependency_consumer_project, "consumer_project"),
        (args.dependency_canonical_notebook, "canonical_notebook"),
        (args.dependency_contract_ref, "contract_ref"),
    ):
        if src is not None:
            dep[key] = src
    if args.dependency_max_depth is not None:
        dep["max_depth"] = args.dependency_max_depth
    if args.dependency_max_records is not None:
        dep["max_records"] = args.dependency_max_records
    return dep or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local STOP-2 NMBot context gate")
    parser.add_argument("question")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--evidence-type", required=True, choices=sorted(EVIDENCE_TYPES))
    parser.add_argument("--definition-of-done", required=True)
    parser.add_argument("--do-not-open", action="append", default=[])
    parser.add_argument("--max-sources", type=int, default=2)
    parser.add_argument("--max-lines", type=int, default=80)
    parser.add_argument("--max-chars", type=int, default=8000)
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true")
    out.add_argument("--human", action="store_true")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--intents", default=str(DEFAULT_INTENTS), help="optional local intent registry path; absent file means disabled")
    parser.add_argument("--target-kind", choices=sorted(STRICT_TARGET_KINDS), help="strict explicit-target mode; must match --evidence-type")
    parser.add_argument("--target", help="strict exact target: stage_id/path_id, Python identifier, or docs anchor/query")
    parser.add_argument("--target-owner", help="strict owner path for symbol disambiguation or required docs owner")
    parser.add_argument("--approved-dependency-json")
    parser.add_argument("--dependency-scope-id")
    parser.add_argument("--dependency-owner-project")
    parser.add_argument("--dependency-consumer-project")
    parser.add_argument("--dependency-canonical-notebook")
    parser.add_argument("--dependency-contract-ref")
    parser.add_argument("--dependency-max-depth", type=int)
    parser.add_argument("--dependency-max-records", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = run_gate(
            args.question,
            project_id=args.project_id,
            evidence_type=args.evidence_type,
            definition_of_done=args.definition_of_done,
            do_not_open=args.do_not_open,
            max_sources=args.max_sources,
            max_lines=args.max_lines,
            max_chars=args.max_chars,
            dependency=_dependency_from_args(args),
            root=ROOT,
            manifest_path=Path(args.manifest),
            intents_path=Path(args.intents) if args.intents else None,
            target_kind=args.target_kind,
            target=args.target,
            target_owner=args.target_owner,
        )
    except GateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) if args.json else render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
