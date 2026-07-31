#!/usr/bin/env python3
"""Universal static developer navigation for allowlisted projects."""
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

from project_adapter_core import AdapterError, ProjectAdapter, load_adapter, load_nmbot_navigation, load_project_manifest_validator, safe_join


MAX_RESULTS = 3
RAW_FALLBACK_LIMIT = 12
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DIAGNOSTIC_CODE_RE = re.compile(r"^(?=.{4,80}$)[a-z][a-z0-9_]*(?::[a-z0-9_]+)?$")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")
DOC_WORDS = {"doc", "docs", "documentation", "contract", "runbook", "context", "док", "доки", "документация", "контракт", "ранбук", "архитектура"}
UNRELATED_MARKERS = {"погода", "борщ", "рецепт", "weather", "recipe"}
DIAGNOSTIC_ROLE_PRIORITY = {"emit": 0, "declare": 1, "reference": 2}


class NavigationError(ValueError):
    """Human-readable navigation error."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_digest(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _read_text(adapter: ProjectAdapter, rel_path: str) -> str:
    try:
        return safe_join(adapter.root, rel_path).read_text(encoding="utf-8")
    except AdapterError as exc:
        raise NavigationError(str(exc)) from exc


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        raise NavigationError("adapter JSON path is not configured")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise NavigationError(f"cannot read adapter JSON: {path}") from exc
    if not isinstance(data, dict):
        raise NavigationError(f"adapter JSON must be object: {path}")
    return data


def _ast_symbol_span(adapter: ProjectAdapter, rel_path: str, symbol: str) -> tuple[int, int, str]:
    if not IDENT_RE.fullmatch(symbol):
        raise NavigationError(f"source symbol must be a Python identifier: {symbol}")
    text = _read_text(adapter, rel_path)
    try:
        tree = ast.parse(text, filename=rel_path)
    except SyntaxError as exc:
        raise NavigationError(f"AST parse failed: {rel_path}:{exc.lineno}") from exc
    matches = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol]
    if not matches:
        raise NavigationError(f"source_symbol_missing:{rel_path}:{symbol}")
    matches.sort(key=lambda node: (int(getattr(node, "lineno", 1)), int(getattr(node, "end_lineno", getattr(node, "lineno", 1)))))
    node = matches[0]
    start = int(getattr(node, "lineno", 1))
    end = int(getattr(node, "end_lineno", start))
    return start, end, node.__class__.__name__


def _line_count(adapter: ProjectAdapter, rel_path: str) -> int:
    return max(1, len(_read_text(adapter, rel_path).splitlines()))


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-zА-Яа-я0-9_./:-]{2,}", text.lower())


def _identifier_tokens(text: str) -> list[str]:
    return re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", text)


def _canonical_diagnostic_code(value: str) -> str | None:
    candidate = value[:-1] if value.endswith(":") else value
    return candidate if DIAGNOSTIC_CODE_RE.fullmatch(candidate) else None


def _test_body_references_symbol(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, symbol: str) -> bool:
    for stmt in node.body:
        for child in ast.walk(stmt):
            if isinstance(child, ast.Name) and child.id == symbol:
                return True
            if isinstance(child, ast.Attribute) and child.attr == symbol:
                return True
    return False


def focused_test_span(adapter: ProjectAdapter, rel_path: str, symbol: str | None) -> tuple[int, int] | None:
    if not symbol:
        return None
    text = _read_text(adapter, rel_path)
    try:
        tree = ast.parse(text, filename=rel_path)
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


def _active_paths(sources: dict[str, Any]) -> set[str]:
    paths = {str(item.get("path")) for item in sources.get("active_sources", []) if isinstance(item, dict)}
    paths.update(str(item.get("path")) for item in sources.get("docs", []) if isinstance(item, dict))
    paths.update(str(item) for item in sources.get("focused_tests", []))
    return {p for p in paths if p}


def _find_related_test(symbol: str, source_path: str, active: set[str], adapter: ProjectAdapter) -> str | None:
    stem = Path(source_path).stem
    candidates = sorted(p for p in active if p.endswith(".py") and Path(p).parts and Path(p).parts[0] == "tests")
    candidates.sort(key=lambda p: (0 if stem in Path(p).stem else 1, p))
    for rel in candidates:
        text = _read_text(adapter, rel)
        if symbol in text or stem in Path(rel).stem:
            return rel
    return None


def _symbol_records(adapter: ProjectAdapter, sources: dict[str, Any], active: set[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in sources.get("active_sources", []):
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "")
        if not rel.endswith(".py"):
            continue
        text = _read_text(adapter, rel)
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:
            raise NavigationError(f"AST parse failed: {rel}:{exc.lineno}") from exc
        allowed_symbols = {str(s) for s in item.get("symbols", [])}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or node.name not in allowed_symbols:
                continue
            start = int(getattr(node, "lineno", 1))
            end = int(getattr(node, "end_lineno", start))
            records.append({
                "kind": "symbol", "symbol": node.name, "symbol_type": node.__class__.__name__, "path": rel,
                "start_line": start, "end_line": end, "owner": rel, "related_test": _find_related_test(node.name, rel, active, adapter),
                "source_hash": sha256_text(text), "terms": f"{node.name} {rel} {item.get('stage','')} {adapter.project_id}",
            })
    return records


def _stage_records(adapter: ProjectAdapter, stages: dict[str, Any], active: set[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for stage in stages.get("stages", []):
        if not isinstance(stage, dict):
            continue
        stage_id = str(stage.get("id") or "")
        source = str(stage.get("owner_source") or "")
        symbols = [str(s) for s in stage.get("owner_symbols", [])]
        if not stage_id or source not in active or not symbols:
            raise NavigationError(f"stage source is not active: {stage_id}:{source}")
        source_symbol = symbols[0]
        start, end, symbol_type = _ast_symbol_span(adapter, source, source_symbol)
        text = _read_text(adapter, source)
        base = {
            "kind": "stage", "stage_id": stage_id, "path": source, "stage_source_path": source,
            "start_line": start, "end_line": end, "owner": source, "purpose": stage.get("label"),
            "source_symbol": source_symbol, "source_symbol_type": symbol_type, "source_hash": sha256_text(text),
            "terms": f"{stage_id} {stage.get('label','')} {source} {' '.join(symbols)} {adapter.project_id}",
            "report_only": bool(stage.get("report_only")),
        }
        records.append({**base, "stage_field": "source"})
        for test in stage.get("test_refs", []):
            test_rel = str(test)
            if test_rel not in active:
                raise NavigationError(f"stage test is not active: {stage_id}:{test_rel}")
            test_text = _read_text(adapter, test_rel)
            records.append({**base, "stage_field": "test", "path": test_rel, "start_line": 1, "end_line": _line_count(adapter, test_rel), "source_hash": sha256_text(test_text), "terms": f"{stage_id} test {test_rel} {source_symbol} {adapter.project_id}"})
    return records


def _diag_records(adapter: ProjectAdapter, diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in diagnostics.get("diagnostics", []):
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        source = str(item.get("owner_source") or "")
        symbol = str(item.get("owner_symbol") or "")
        if not DIAGNOSTIC_CODE_RE.fullmatch(code):
            raise NavigationError(f"invalid diagnostic code: {code}")
        start, end, symbol_type = _ast_symbol_span(adapter, source, symbol)
        text = _read_text(adapter, source)
        literal = str(item.get("literal") or code)
        records.append({
            "kind": "diagnostic_code", "code": code, "diagnostic_role": "declare", "symbol": symbol,
            "symbol_type": symbol_type, "path": source, "start_line": start, "end_line": end,
            "owner": source, "source_role": "source", "source_hash": sha256_text(text),
            "literal": literal, "match": str(item.get("match") or "exact"),
            "terms": f"diagnostic_code {code} {symbol} {source} {adapter.project_id}",
        })
    return records


def _doc_records(adapter: ProjectAdapter, sources: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in sources.get("docs", []):
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "")
        text = _read_text(adapter, rel)
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not HEADING_RE.match(stripped):
                continue
            records.append({
                "kind": "doc_anchor", "path": rel, "start_line": line_no, "end_line": line_no,
                "anchor": stripped, "anchor_type": "heading", "owner": rel, "source_hash": sha256_text(text),
                "terms": f"{stripped} {rel} {adapter.project_id}",
            })
    return records


def _target_spec(record: dict[str, Any]) -> dict[str, str] | None:
    kind = record.get("kind")
    path = str(record.get("path") or "")
    if kind == "stage":
        return {"target_kind": "stage", "target": str(record["stage_id"]), "target_owner": str(record["stage_source_path"]), "owner_path": str(record["stage_source_path"])}
    if kind in {"symbol", "diagnostic_code"}:
        return {"target_kind": "symbol", "target": str(record.get("symbol")), "target_owner": path, "owner_path": path}
    if kind == "doc_anchor":
        return {"target_kind": "docs", "target": str(record.get("anchor")), "target_owner": path, "owner_path": path}
    return None


def _result(record: dict[str, Any], *, fallback: bool = False, score: int | None = None) -> dict[str, Any]:
    out = {"kind": record["kind"], "path": record["path"], "start_line": record["start_line"], "end_line": record["end_line"]}
    spec = _target_spec(record)
    if spec:
        out["target_spec"] = spec
    for key in ("stage_id", "stage_field", "source_symbol", "source_symbol_type", "symbol", "symbol_type", "related_test", "anchor", "anchor_type", "owner", "code", "source_role", "diagnostic_role", "report_only"):
        if record.get(key) is not None and record.get(key) != "":
            out[key] = record[key]
    if fallback or record.get("kind") == "diagnostic_code":
        out["candidate_only"] = True
    if score is not None:
        out["score"] = score
    return out


def _validator_paths(adapter: ProjectAdapter) -> list[Path | None]:
    paths: list[Path | None] = [adapter.manifest_path, adapter.stage_map_path, adapter.diagnostics_path]
    if adapter.project_id == "mpn":
        paths.append(Path(__file__).resolve().parents[1] / "config" / "mpn_dependency_card.json")
    return paths


def _validate_project_adapter(adapter: ProjectAdapter) -> Any:
    validator = load_project_manifest_validator(adapter.project_id)
    try:
        validator.validate(_validator_paths(adapter))
    except Exception as exc:
        raise NavigationError(f"{adapter.project_id} static validator failed: {exc}") from exc
    return validator


def build_registry(project_id: str) -> dict[str, Any]:
    adapter = load_adapter(project_id)
    if adapter.project_id == "nmbot":
        nav = load_nmbot_navigation()
        registry = nav.build_registry(root=adapter.root, manifest_path=adapter.manifest_path)
        return {**registry, "project_id": "nmbot", "adapter": adapter}
    validator = _validate_project_adapter(adapter)
    sources = _load_json(adapter.manifest_path)
    stages = _load_json(adapter.stage_map_path)
    diagnostics = _load_json(adapter.diagnostics_path)
    exclusions = validator.build_exclusion_policy(sources, adapter.root)
    active = _active_paths(sources)
    for rel in active:
        if validator._is_rel_excluded(exclusions, rel):
            raise NavigationError(f"{adapter.project_id} active path is excluded: {rel}")
        safe_join(adapter.root, rel)
    records = _stage_records(adapter, stages, active) + _symbol_records(adapter, sources, active) + _diag_records(adapter, diagnostics) + _doc_records(adapter, sources)
    for record in records:
        rel = str(record.get("path") or "")
        owner = str(record.get("owner") or "")
        if validator._is_rel_excluded(exclusions, rel) or (owner and validator._is_rel_excluded(exclusions, owner)):
            raise NavigationError(f"{adapter.project_id} record path is excluded: {rel}")
    for record in records:
        record["id"] = _json_digest({k: v for k, v in record.items() if k != "id"})[:24]
    return {"records": records, "fingerprint": _json_digest({"project_id": adapter.project_id, "records": records}), "active_paths": active, "sources": sources, "project_id": adapter.project_id, "adapter": adapter}


def _score(query: str, record: dict[str, Any]) -> tuple[int, int]:
    q = set(_tokens(query))
    terms = set(_tokens(" ".join(str(record.get(k, "")) for k in ("terms", "path", "symbol", "stage_id", "anchor", "code"))))
    overlap = len(q & terms)
    exact = 0
    qlow = query.lower()
    for key in ("symbol", "stage_id", "anchor", "path", "code"):
        value = str(record.get(key) or "").lower()
        if value and value in qlow:
            exact += 5
    return exact + overlap, exact


def _distinct_path_results(records: Iterable[dict[str, Any]], *, fallback: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        path = str(record["path"])
        if path in seen:
            continue
        seen.add(path)
        out.append(_result(record, fallback=fallback))
        if len(out) >= MAX_RESULTS:
            break
    return out


def _base_report(project_id: str, query: str, registry: dict[str, Any], *, route: str, reason: str, fallback: bool, results: list[dict[str, Any]], next_action: str) -> dict[str, Any]:
    return {
        "schema": "project.navigation.v1", "project_id": project_id, "query": query, "route": route, "reason": reason,
        "abstain": not results, "fallback": fallback, "results": results[:MAX_RESULTS], "next_action": next_action,
        "registry_fingerprint": registry["fingerprint"], "local_read_only": True, "candidates_are_not_evidence": True, "production_proof": False,
    }


def _stage_dispatch(query: str, project_id: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    chosen = query.strip()
    stages = [r for r in registry["records"] if r.get("kind") == "stage" and r.get("stage_id") == chosen]
    if not stages:
        return None
    ordered = sorted(stages, key=lambda r: ({"source": 0, "test": 1, "doc": 2, "prompt": 3}.get(str(r.get("stage_field")), 9), str(r["path"])))
    return _base_report(project_id, query, registry, route="stage", reason=f"exact stage_id: {chosen}", fallback=False, results=_distinct_path_results(ordered), next_action="read exact stage target before making claims")


def _symbol_dispatch(query: str, project_id: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    symbols = {r["symbol"] for r in registry["records"] if r.get("kind") == "symbol"}
    chosen = next((token for token in _identifier_tokens(query) if token in symbols), None)
    if not chosen:
        return None
    matches = [r for r in registry["records"] if r.get("kind") == "symbol" and r.get("symbol") == chosen]
    matches.sort(key=lambda r: (r["path"], r["start_line"]))
    return _base_report(project_id, query, registry, route="ast", reason=f"exact Python identifier: {chosen}", fallback=False, results=[_result(r) for r in matches[:MAX_RESULTS]], next_action="read exact symbol target and optional focused test before making claims")


def _diagnostic_dispatch(query: str, project_id: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    codes = {r["code"] for r in registry["records"] if r.get("kind") == "diagnostic_code"}
    chosen = None
    for token in _tokens(query):
        if not DIAGNOSTIC_CODE_RE.fullmatch(token):
            continue
        base = token.split(":", 1)[0]
        if token in codes:
            chosen = token
            break
        if base in codes:
            chosen = base
            break
    if not chosen:
        return None
    matches = [r for r in registry["records"] if r.get("kind") == "diagnostic_code" and r.get("code") == chosen]
    matches.sort(key=lambda r: (0 if r.get("source_role") == "source" else 1, DIAGNOSTIC_ROLE_PRIORITY.get(str(r.get("diagnostic_role")), 9), int(r["end_line"]) - int(r["start_line"]), r["path"], r["symbol"]))
    return _base_report(project_id, query, registry, route="diagnostic", reason=f"exact diagnostic code: {chosen}", fallback=False, results=[_result(r) for r in matches[:MAX_RESULTS]], next_action="candidate-only: strict context gate may read returned symbol target")


def _docs_dispatch(query: str, project_id: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    if not (set(_tokens(query)) & DOC_WORDS):
        return None
    scored = [(sum(_score(query, r)), r) for r in registry["records"] if r.get("kind") == "doc_anchor"]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda sr: (-sr[0], sr[1]["path"], sr[1]["start_line"]))
    return _base_report(project_id, query, registry, route="docs", reason="explicit documentation/contract/context wording", fallback=False, results=[_result(r, score=s) for s, r in scored[:MAX_RESULTS]], next_action="read returned anchors before making claims")


def _fallback_dispatch(query: str, project_id: str, registry: dict[str, Any]) -> dict[str, Any]:
    tokens = [t for t in _tokens(query) if t not in UNRELATED_MARKERS]
    if not tokens or set(_tokens(query)) & UNRELATED_MARKERS:
        return _base_report(project_id, query, registry, route="mixed", reason="no safe deterministic route", fallback=True, results=[], next_action="abstain")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE nav USING fts5(terms, path, kind, tokenize='unicode61')")
    rows_by_id: dict[int, dict[str, Any]] = {}
    try:
        for rowid, record in enumerate([r for r in registry["records"] if r.get("kind") != "diagnostic_code"], start=1):
            conn.execute("INSERT INTO nav(rowid, terms, path, kind) VALUES (?, ?, ?, ?)", (rowid, str(record.get("terms", "")), record["path"], record["kind"]))
            rows_by_id[rowid] = record
        conn.commit()
        fts_query = " OR ".join('"' + token.replace('"', '""') + '"' for token in dict.fromkeys(tokens))
        rows = conn.execute("SELECT rowid FROM nav WHERE nav MATCH ? ORDER BY bm25(nav) ASC, path ASC LIMIT ?", (fts_query, RAW_FALLBACK_LIMIT)).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    selected = _distinct_path_results((rows_by_id[int(rowid)] for (rowid,) in rows), fallback=True)
    return _base_report(project_id, query, registry, route="mixed", reason="bounded mixed FTS fallback; candidate-only", fallback=True, results=selected, next_action="select_then_read" if selected else "abstain")


def navigate(query: str, *, project_id: str) -> dict[str, Any]:
    if not str(query).strip():
        raise NavigationError("query is required")
    if project_id == "nmbot":
        adapter = load_adapter("nmbot")
        nav = load_nmbot_navigation()
        report = nav.navigate(query, root=adapter.root, manifest_path=adapter.manifest_path)
        report = dict(report)
        report["project_id"] = "nmbot"
        return report
    registry = build_registry(project_id)
    for dispatcher in (_stage_dispatch, _symbol_dispatch, _diagnostic_dispatch, _docs_dispatch):
        report = dispatcher(query, project_id, registry)
        if report is not None:
            return report
    return _fallback_dispatch(query, project_id, registry)


def render_human(report: dict[str, Any]) -> str:
    lines = [f"Project navigate [{report.get('project_id')}]: route={report['route']}" + (" (abstain)" if report.get("abstain") else ""), f"Reason: {report['reason']}"]
    for index, item in enumerate(report.get("results", []), start=1):
        label = item.get("symbol") or item.get("stage_id") or item.get("anchor") or item.get("code") or item["kind"]
        lines.append(f"{index}. {item['path']}:{item['start_line']}-{item['end_line']} [{item['kind']}] {label}")
    lines.append(f"Next: {report['next_action']}")
    lines.append("Boundary: local developer navigation only; candidates are not evidence or production proof.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Universal allowlisted project navigation")
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--project-id", required=True, choices=("nmbot", "qapairs", "cc2", "mpn"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--human", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.json and args.human:
        parser.error("--json and --human are mutually exclusive")
    try:
        if args.validate_only:
            registry = build_registry(args.project_id)
            payload = {"schema": "project.navigation.v1", "project_id": args.project_id, "valid": True, "record_count": len(registry["records"]), "registry_fingerprint": registry["fingerprint"]}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) if args.json else f"project navigation registry valid: {payload['record_count']} records")
            return 0
        report = navigate(args.query, project_id=args.project_id)
    except (NavigationError, AdapterError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) if args.json else render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
