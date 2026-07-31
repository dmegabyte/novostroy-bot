#!/usr/bin/env python3
"""Read-only static preflight for nmbot/Jivo architecture invariants."""
from __future__ import annotations

import argparse
import ast
import json
import re
from types import SimpleNamespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CANONICAL_FILES = [
    "docs/BOT_ARCHITECTURE.md",
    "docs/LLM_DECISION_ARCHITECTURE_TZ.md",
    "docs/JIVO_BOT_API_INTEGRATION_PLAN.md",
    "scripts/chat_tester_bot.py",
    "scripts/nmbot_api_server.py",
]

# These are deliberately conjunctive.  A generic word such as ``version`` or
# a global asyncio.Lock must not be reported as proof of a chat-level
# concurrency/state contract.
MARKER_REQUIREMENTS = {
    "structured_trace": [[r"structured"], [r"trace_id"], [r"jsonl"]],
    "event_id_jivo_correlation": [[r"event[_-]?id", r"eventId"], [r"jivo", r"correlation"]],
    "idempotency_dedup": [[r"idempot", r"dedup"], [r"event[_-]?id", r"duplicate", r"replay"]],
    "per_chat_lock_serialization": [[r"asyncio\.Lock", r"Lock\("], [r"session[_ -]?key", r"per[_-]?session", r"per[_-]?chat"], [r"serialize", r"queue", r"locks?\.hold", r"lock"]],
    "state_version_cas": [[r"state[_ -]?version", r"versioned_state", r"CAS", r"compare.*swap"], [r"expected[_ -]?version", r"if[_ -]?version", r"optimistic[_ -]?lock", r"conditional[_ -]?write"]],
    "terminal_outcome": [[r"terminal", r"final[_ -]?answer", r"explicit_failure"], [r"BOT_MESSAGE", r"INVITE_AGENT"], [r"outcome", r"status"]],
    "accepted_async_fast_fallback": [[r"accepted_async"], [r"fallback", r"async[_ -]?send", r"background"]],
    "delivery_status": [[r"delivery[_ -]?status", r"delivered", r"delivery_receipt"], [r"BOT_MESSAGE", r"INVITE_AGENT"], [r"status", r"outcome"]],
}

PROD_SOURCE_GLOBS = ("*.py", "scripts/*.py", "*.sh", "scripts/*.sh")
DIAGNOSTIC_SOURCE_NAMES = {
    "scripts/nmbot_jivo_trace_analyze.py",
    "scripts/nmbot_jivo_audit.sh",
    "scripts/nmbot_architecture_preflight.py",
}

ACCEPTED_ASYNC_FINAL_DELIVERY_REQUIREMENTS = [
    [r"accepted_async"],
    [r"_post_event_to_jivo"],
    [r"delivery_role\s*=\s*[\"']final[\"']", r"delivery_role[^\n]+final"],
    [r"BOT_MESSAGE", r"INVITE_AGENT"],
    [r"terminal_send_accepted", r"delivery_status\s*=\s*delivery_status"],
]

REGISTRY_REL_PATH = "config/nmbot_stage_map.json"
REGISTRY_SCHEMA = "nmbot.stage_map.v1"
EXECUTION_STATUS_CONTRACT = {"completed", "failed", "fallback", "skipped"}


@dataclass
class Ref:
    file: str
    line: int
    snippet: str

    def as_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "snippet": self.snippet[:160]}


def iter_source_files(repo: Path) -> list[Path]:
    files: set[Path] = set()
    for pattern in PROD_SOURCE_GLOBS:
        for path in repo.glob(pattern):
            try:
                rel = path.relative_to(repo).as_posix()
            except ValueError:
                rel = path.as_posix()
            if path.is_file() and "__pycache__" not in path.parts and rel not in DIAGNOSTIC_SOURCE_NAMES:
                files.add(path)
    return sorted(files)


def find_refs(repo: Path, patterns: list[str], include_docs: bool = True) -> list[Ref]:
    refs: list[Ref] = []
    search_files = iter_source_files(repo)
    if include_docs:
        search_files += sorted((repo / "docs").glob("*.md")) if (repo / "docs").exists() else []
    regexes = [re.compile(p, re.IGNORECASE) for p in patterns]
    for path in search_files:
        rel = path.relative_to(repo).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for idx, line in enumerate(lines, 1):
            if any(rx.search(line) for rx in regexes):
                refs.append(Ref(rel, idx, line.strip()))
                if len(refs) >= 20:
                    return refs
    return refs


def find_runtime_requirement_refs(repo: Path, requirements: list[list[str]], *, window: int = 12) -> list[Ref]:
    """Return evidence only when requirements occur near each other in one runtime file."""
    refs: list[Ref] = []
    regexes = [[re.compile(p, re.IGNORECASE) for p in group] for group in requirements]
    for path in iter_source_files(repo):
        rel = path.relative_to(repo).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        group_matches = [
            [Ref(rel, idx, line.strip()) for idx, line in enumerate(lines, 1) if any(rx.search(line) for rx in group)]
            for group in regexes
        ]
        if any(not matches for matches in group_matches):
            continue
        for anchor in group_matches[0]:
            nearby = [
                next((match for match in matches if abs(match.line - anchor.line) <= window), None)
                for matches in group_matches[1:]
            ]
            if all(match is not None for match in nearby):
                refs.extend([anchor, *nearby])
                break
        if refs and refs[-1].file == rel:
            if len(refs) >= 20:
                return refs[:20]
    return refs


def find_accepted_async_delivery_evidence(repo: Path, *, window: int = 600) -> tuple[list[Ref], list[Ref]]:
    """Return accepted_async refs and linked final-delivery evidence in one source file.

    `accepted_async` is allowed only as a fast webhook acknowledgement when the
    same runtime bridge source also contains the later Bot-Provider -> Jivo final
    delivery path.  This is intentionally read-only/static evidence: it does not
    execute the bridge, call Jivo, or infer production health.
    """
    accepted_refs: list[Ref] = []
    linked_refs: list[Ref] = []
    regexes = [[re.compile(p, re.IGNORECASE) for p in group] for group in ACCEPTED_ASYNC_FINAL_DELIVERY_REQUIREMENTS]
    for path in iter_source_files(repo):
        rel = path.relative_to(repo).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        group_matches = [
            [Ref(rel, idx, line.strip()) for idx, line in enumerate(lines, 1) if any(rx.search(line) for rx in group)]
            for group in regexes
        ]
        file_accepted = group_matches[0]
        accepted_refs.extend(file_accepted)
        if not file_accepted or any(not matches for matches in group_matches[1:]):
            continue
        for anchor in file_accepted:
            final_role = next((match for match in group_matches[2] if abs(match.line - anchor.line) <= window), None)
            if final_role is None:
                continue
            post_call = next(
                (
                    match
                    for match in group_matches[1]
                    if abs(match.line - anchor.line) <= window and match.line <= final_role.line
                ),
                None,
            )
            event_marker = next((match for match in group_matches[3] if abs(match.line - anchor.line) <= window), None)
            sent_outcome = next(
                (
                    match
                    for match in group_matches[4]
                    if abs(match.line - anchor.line) <= window and match.line >= final_role.line
                ),
                None,
            )
            if post_call is not None and event_marker is not None and sent_outcome is not None:
                linked_refs.extend([anchor, post_call, final_role, event_marker, sent_outcome])
                break
        if linked_refs and linked_refs[-1].file == rel:
            break
    return accepted_refs[:20], linked_refs[:20]


def check_stage_registry(repo: Path) -> tuple[list[dict[str, Any]], bool]:
    checks: list[dict[str, Any]] = []
    strict_fail = False
    path = repo / REGISTRY_REL_PATH
    if not path.exists():
        return ([{"name": "stage_registry:exists", "status": "FAIL", "explain": "registry missing", "refs": []}], True)
    checks.append({"name": "stage_registry:exists", "status": "PASS", "explain": "registry found", "refs": [{"file": REGISTRY_REL_PATH, "line": 1, "snippet": "exists"}]})
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (checks + [{"name": "stage_registry:parse", "status": "FAIL", "explain": f"registry parse failed: {exc.__class__.__name__}", "refs": [{"file": REGISTRY_REL_PATH, "line": 1, "snippet": "parse"}]}], True)

    paths = registry.get("paths") if isinstance(registry.get("paths"), dict) else {}
    stages = registry.get("stages") if isinstance(registry.get("stages"), dict) else {}
    active = registry.get("active_by_version") if isinstance(registry.get("active_by_version"), dict) else {}
    schema_ok = registry.get("schema") == REGISTRY_SCHEMA and registry.get("schema_version") == 1 and paths and stages and active.get("v2") == "v2.turn.v1"
    checks.append({"name": "stage_registry:schema", "status": "PASS" if schema_ok else "FAIL", "explain": "schema/version/active v2 ok" if schema_ok else "schema/version/active v2 invalid", "refs": [{"file": REGISTRY_REL_PATH, "line": 1, "snippet": str(registry.get("schema"))[:80]}]})
    strict_fail = strict_fail or not schema_ok

    status_contract = set(registry.get("status_contract") or []) if isinstance(registry.get("status_contract"), list) else set()
    runtime = _load_runtime_execution_path(repo)
    runtime_status_contract = set(getattr(runtime, "ALLOWED_EXECUTION_STATUSES", ())) if runtime else EXECUTION_STATUS_CONTRACT
    status_ok = status_contract == runtime_status_contract == EXECUTION_STATUS_CONTRACT
    checks.append({"name": "stage_registry:status_contract", "status": "PASS" if status_ok else "FAIL", "explain": "bounded execution statuses match runtime contract" if status_ok else "status contract mismatch", "refs": [{"file": REGISTRY_REL_PATH, "line": 1, "snippet": ",".join(sorted(status_contract))}]})
    strict_fail = strict_fail or not status_ok

    runtime_path_ids = set(getattr(runtime, "ALLOWED_EXECUTION_PATH_IDS", ())) if runtime else set()
    runtime_v2_stage_ids = tuple(getattr(runtime, "V2_EXECUTION_STAGE_IDS", ())) if runtime else ()
    runtime_jivo_api_stage_id = str(getattr(runtime, "JIVO_API_PREPARE_STAGE_ID", "")) if runtime else ""

    id_errors: list[str] = []
    for path_id, item in paths.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,79}", str(path_id)):
            id_errors.append(f"bad path_id:{path_id}")
        if not isinstance(item, dict):
            id_errors.append(f"bad path:{path_id}")
            continue
        if item.get("extends") and str(item.get("extends")) not in paths:
            id_errors.append(f"missing extends:{path_id}->{item.get('extends')}")
        raw_stage_ids = item.get("stage_ids") if isinstance(item.get("stage_ids"), list) else []
        local_stage_ids = [str(stage_id) for stage_id in raw_stage_ids]
        dupes = sorted({stage_id for stage_id in local_stage_ids if local_stage_ids.count(stage_id) > 1})
        for stage_id in dupes:
            id_errors.append(f"duplicate stage:{path_id}->{stage_id}")
        for stage_id in item.get("stage_ids") if isinstance(item.get("stage_ids"), list) else []:
            if str(stage_id) not in stages:
                id_errors.append(f"missing stage:{path_id}->{stage_id}")
    id_errors.extend(_extends_cycle_errors(paths))
    for stage_id, item in stages.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,79}", str(stage_id)):
            id_errors.append(f"bad stage_id:{stage_id}")
        if not isinstance(item, dict):
            id_errors.append(f"bad stage:{stage_id}")
            continue
        for key in ("owner", "source", "prompt", "doc", "test"):
            rel = item.get(key)
            if rel and not (repo / str(rel)).exists():
                id_errors.append(f"missing {key}:{stage_id}->{rel}")
    checks.append({"name": "stage_registry:integrity", "status": "PASS" if not id_errors else "FAIL", "explain": "all path/stage references are unique and local paths exist" if not id_errors else "; ".join(id_errors[:6]), "refs": [{"file": REGISTRY_REL_PATH, "line": 1, "snippet": "integrity"}]})
    strict_fail = strict_fail or bool(id_errors)

    symbol_errors = _stage_source_symbol_errors(repo, stages)
    checks.append({"name": "stage_registry:source_symbols", "status": "PASS" if not symbol_errors else "FAIL", "explain": "all stage source_symbol entries resolve to exact AST definitions" if not symbol_errors else "; ".join(symbol_errors[:6]), "refs": [{"file": REGISTRY_REL_PATH, "line": 1, "snippet": "source_symbol"}]})
    strict_fail = strict_fail or bool(symbol_errors)

    test_errors = _stage_focused_test_errors(repo, stages)
    checks.append({"name": "stage_registry:focused_tests", "status": "PASS" if not test_errors else "FAIL", "explain": "all stage test mappings contain the stage source_symbol in a focused test span" if not test_errors else "; ".join(test_errors[:6]), "refs": [{"file": REGISTRY_REL_PATH, "line": 1, "snippet": "focused_tests"}]})
    strict_fail = strict_fail or bool(test_errors)

    runtime_errors: list[str] = []
    if not runtime:
        runtime_errors.append("runtime constants unavailable")
    elif not runtime_path_ids.issubset(set(paths)):
        runtime_errors.append("runtime path ids missing in registry:" + ",".join(sorted(runtime_path_ids - set(paths))))
    v2_path = paths.get("v2.turn.v1") if isinstance(paths.get("v2.turn.v1"), dict) else {}
    if runtime_v2_stage_ids and tuple(v2_path.get("stage_ids") or ()) != runtime_v2_stage_ids:
        runtime_errors.append("v2 stage order differs from runtime constants")
    jivo_path = paths.get("jivo.v2.turn.v1") if isinstance(paths.get("jivo.v2.turn.v1"), dict) else {}
    if runtime_jivo_api_stage_id and jivo_path.get("stage_ids") != [runtime_jivo_api_stage_id]:
        runtime_errors.append("jivo api stage differs from runtime constants")
    checks.append({"name": "stage_registry:runtime_drift", "status": "PASS" if not runtime_errors else "FAIL", "explain": "registry path ids, stage order and statuses match runtime-owned constants" if not runtime_errors else "; ".join(runtime_errors[:6]), "refs": [{"file": "nmbot_v2/execution_path.py", "line": 9, "snippet": "runtime execution path constants"}, {"file": REGISTRY_REL_PATH, "line": 1, "snippet": "runtime drift"}]})
    strict_fail = strict_fail or bool(runtime_errors)
    boundary = paths.get("jivo.bridge.delivery.v1") if isinstance(paths.get("jivo.bridge.delivery.v1"), dict) else {}
    boundary_ok = bool(boundary.get("boundary")) and bool(boundary.get("correlation_limit")) and "jivo.bridge.delivery" in (boundary.get("stage_ids") or [])
    checks.append({"name": "stage_registry:jivo_delivery_boundary", "status": "PASS" if boundary_ok else "FAIL", "explain": "terminal Jivo delivery is represented as separate bridge path with correlation limitation" if boundary_ok else "bridge boundary/correlation limitation missing", "refs": [{"file": REGISTRY_REL_PATH, "line": 1, "snippet": "jivo.bridge.delivery.v1"}]})
    strict_fail = strict_fail or not boundary_ok
    return checks, strict_fail


def _load_runtime_execution_path(repo: Path) -> Any | None:
    module_path = repo / "nmbot_v2" / "execution_path.py"
    if not module_path.exists():
        return None
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        values: dict[str, Any] = {}
        wanted = {
            "ALLOWED_EXECUTION_STATUSES",
            "ALLOWED_EXECUTION_PATH_IDS",
            "V2_EXECUTION_STAGE_IDS",
            "JIVO_API_PREPARE_STAGE_ID",
        }
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if not names:
                continue
            value = _literal_runtime_constant(node.value, values)
            for name in names:
                values[name] = value
        return SimpleNamespace(**{name: values.get(name) for name in wanted})
    except Exception:
        return None


def _stage_source_symbol_errors(repo: Path, stages: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    ast_cache: dict[str, ast.AST] = {}
    for stage_id, item in stages.items():
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        if not isinstance(source, str) or not source:
            continue
        symbol = item.get("source_symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            errors.append(f"missing source_symbol:{stage_id}")
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol.strip()):
            errors.append(f"bad source_symbol:{stage_id}->{symbol}")
            continue
        path = repo / source
        if not path.exists():
            continue
        try:
            tree = ast_cache[source]
        except KeyError:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=source)
            except SyntaxError as exc:
                errors.append(f"source_symbol_ast_parse:{stage_id}->{source}:{exc.lineno}")
                continue
            ast_cache[source] = tree
        found = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol.strip()
            for node in ast.walk(tree)
        )
        if not found:
            errors.append(f"missing source_symbol:{stage_id}->{source}:{symbol}")
    return errors


def _stage_focused_test_errors(repo: Path, stages: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for stage_id, item in stages.items():
        if not isinstance(item, dict):
            continue
        test = item.get("test")
        symbol = item.get("source_symbol")
        if not isinstance(test, str) or not test or not isinstance(symbol, str) or not symbol.strip():
            continue
        path = repo / test
        if not path.exists():
            continue
        if _focused_test_span(path, symbol.strip()) is None:
            errors.append(f"focused_test_missing_symbol:{stage_id}->{test}:{symbol.strip()}")
    return errors


def _focused_test_span(path: Path, symbol: str) -> tuple[int, int] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        tree = ast.parse(text, filename=path.as_posix())
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


def _literal_runtime_constant(node: ast.AST, values: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, ast.Tuple):
        return tuple(_literal_runtime_constant(item, values) for item in node.elts)
    if isinstance(node, ast.List):
        return [_literal_runtime_constant(item, values) for item in node.elts]
    if isinstance(node, ast.Set):
        return {_literal_runtime_constant(item, values) for item in node.elts}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_runtime_constant(node.left, values)
        right = _literal_runtime_constant(node.right, values)
        return left + right if isinstance(left, tuple) and isinstance(right, tuple) else None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frozenset" and node.args:
        value = _literal_runtime_constant(node.args[0], values)
        return frozenset(value) if value is not None else None
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _extends_cycle_errors(paths: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for path_id in paths:
        seen: list[str] = []
        current = path_id
        while current:
            if current in seen:
                errors.append(f"extends cycle:{'->'.join(seen + [current])}")
                break
            seen.append(current)
            item = paths.get(current) if isinstance(paths.get(current), dict) else {}
            current = str(item.get("extends") or "")
    return sorted(set(errors))


def check(repo: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    strict_fail = False

    for rel in CANONICAL_FILES:
        exists = (repo / rel).exists()
        status = "PASS" if exists else "FAIL"
        strict_fail = strict_fail or not exists
        checks.append({"name": f"canonical_file:{rel}", "status": status, "explain": "найден" if exists else "канонический файл отсутствует", "refs": [{"file": rel, "line": 1, "snippet": "exists"}] if exists else []})

    registry_checks, registry_fail = check_stage_registry(repo)
    checks.extend(registry_checks)
    strict_fail = strict_fail or registry_fail

    for name, requirements in MARKER_REQUIREMENTS.items():
        refs = find_runtime_requirement_refs(repo, requirements)
        checks.append({
            "name": name,
            "status": "PASS" if refs else "WARN",
            "explain": "связанный runtime-контекст найден" if refs else "связанный runtime-контекст не найден; отдельные слова не считаются доказательством",
            "refs": [r.as_dict() for r in refs[:8]],
        })

    accepted_prod_refs, linked_delivery_refs = find_accepted_async_delivery_evidence(repo)
    if linked_delivery_refs:
        checks.append({"name": "accepted_async_production_path", "status": "PASS", "explain": "accepted_async связан в runtime bridge с последующей final delivery отправкой в Jivo", "refs": [r.as_dict() for r in linked_delivery_refs[:8]]})
    elif accepted_prod_refs:
        strict_fail = True
        checks.append({"name": "accepted_async_production_path", "status": "FAIL", "explain": "accepted_async найден, но связанная final delivery отправка в том же runtime bridge не доказана статически", "refs": [r.as_dict() for r in accepted_prod_refs[:8]]})
    else:
        checks.append({"name": "accepted_async_production_path", "status": "PASS", "explain": "в source-файлах accepted_async не найден", "refs": []})

    counts = {status: sum(1 for c in checks if c["status"] == status) for status in ("PASS", "WARN", "FAIL")}
    overall = "FAIL" if counts["FAIL"] else ("WARN" if counts["WARN"] else "PASS")
    return {"repo": str(repo), "overall": overall, "strict_fail": strict_fail, "summary": counts, "checks": checks}


def print_human(result: dict[str, Any]) -> None:
    print("nmbot architecture preflight")
    print(f"Repo: {result['repo']}")
    print(f"Итог: {result['overall']} | PASS={result['summary']['PASS']} WARN={result['summary']['WARN']} FAIL={result['summary']['FAIL']}")
    for item in result["checks"]:
        print(f"\n[{item['status']}] {item['name']}: {item['explain']}")
        for ref in item.get("refs", [])[:4]:
            print(f"  - {ref['file']}:{ref['line']} — {ref['snippet']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Static read-only architecture preflight for nmbot/Jivo.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).parent.parent)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    result = check(args.repo.absolute())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(result)
    return 1 if args.strict and result["strict_fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
