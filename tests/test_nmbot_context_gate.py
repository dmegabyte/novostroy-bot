from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_context_gate.py"


def load_module():
    spec = importlib.util.spec_from_file_location("nmbot_context_gate_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def make_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    for rel in ("config", "docs", "scripts", "tests", "prompts"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    files = {
        "docs/search.md": "# Search\n\n## Deterministic contract\nRead this first.\n",
        "docs/NMBOT_CONTEXT_PACKS.md": "# Packs\n\n<!-- NMBOT_CONTEXT_PACKS_JSON_START -->\n```json\n{\"schema\":\"nmbot.context_pack.v1\",\"packs\":[{\"id\":\"diagnostics/trace\",\"title\":\"Trace docs\",\"read_first\":[\"docs/search.md\"],\"read_first_anchors\":[{\"path\":\"docs/search.md\",\"anchor\":\"## Deterministic contract\"}],\"docs\":[\"docs/search.md\"],\"files\":[],\"checks\":[\"python3 scripts/nmbot_check.py docs\"],\"boundaries\":[\"local only\"]}]}\n```\n<!-- NMBOT_CONTEXT_PACKS_JSON_END -->\n",
        "scripts/search.py": "def target_symbol():\n    return 'ok'\n\nclass TargetClass:\n    pass\n",
        "tests/test_search.py": "from scripts.search import target_symbol\n\ndef test_target_symbol():\n    assert target_symbol() == 'ok'\n",
        "prompts/search.txt": "# Prompt title\nbody\n",
        "config/nmbot_stage_map.json": json.dumps({
            "schema": "nmbot.stage_map.v1",
            "paths": {"v2.turn.v1": {"stage_ids": ["v2.search"]}},
            "stages": {"v2.search": {"purpose": "Search stage", "owner": "runtime", "source": "scripts/search.py", "source_symbol": "target_symbol", "doc": "docs/search.md", "test": "tests/test_search.py", "prompt": "prompts/search.txt"}},
        }),
    }
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8")
    manifest = {
        "schema": "nmbot.retrieval_sources.v1",
        "sources": [
            {"path": "docs/search.md", "module": "search", "type": "doc", "owner": "docs", "status": "active"},
            {"path": "docs/NMBOT_CONTEXT_PACKS.md", "module": "context", "type": "doc", "owner": "context", "status": "active"},
            {"path": "scripts/search.py", "module": "search", "type": "python", "owner": "search", "status": "active"},
            {"path": "tests/test_search.py", "module": "search", "type": "test", "owner": "tests", "status": "active"},
            {"path": "prompts/search.txt", "module": "search", "type": "prompt", "owner": "prompt", "status": "active"},
        ],
    }
    path = Path("config/nmbot_retrieval_sources.json")
    (root / path).write_text(json.dumps(manifest), encoding="utf-8")
    return root, path


def run_gate(tmp_path: Path, question: str, evidence_type: str, **kwargs):
    mod = load_module()
    root, manifest = make_root(tmp_path)
    defaults = {
        "project_id": "nmbot",
        "evidence_type": evidence_type,
        "definition_of_done": "bounded answer",
        "root": root,
        "manifest_path": manifest,
    }
    defaults.update(kwargs)
    return mod.run_gate(question, **defaults)


def write_intents(root: Path, cards: list[dict[str, object]]) -> Path:
    path = Path("config/nmbot_context_gate_intents.json")
    (root / path).write_text(json.dumps({"schema": "nmbot.context_gate_intents.v1", "cards": cards}, ensure_ascii=False), encoding="utf-8")
    return path


def base_intent_card(**overrides: object) -> dict[str, object]:
    card: dict[str, object] = {
        "id": "stage.search.writer",
        "evidence_type": "stage",
        "match_all": ["писатель", "поиск"],
        "resolver_query": "v2.search",
        "purpose": "Search writer stage navigation",
        "owner_path": "scripts/search.py",
    }
    card.update(overrides)
    return card


def test_stage_source_and_test_stop_owner_contract_and_test(tmp_path: Path) -> None:
    report = run_gate(tmp_path, "v2.search", "stage")

    assert report["route"] == "stage"
    assert report["stop_reason"] == "owner_contract_and_test"
    assert [item["path"] for item in report["context"]] == ["scripts/search.py", "tests/test_search.py"]
    assert report["context"][0]["source_symbol"] == "target_symbol"
    assert report["context"][0]["start_line"] == 1
    assert report["context"][0]["end_line"] == 2
    assert report["context"][1]["start_line"] == 3
    assert report["context"][1]["end_line"] == 4
    assert report["trace"]["schema"] == "bounded-retrieval.v1"
    assert report["trace"]["selected_source_count"] == 2


def test_strict_stage_exact_target_ignores_question_and_intents(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    bad_intents = write_intents(root, [base_intent_card(match_all=["qapairs", "секрет"], resolver_query="v2.search")])

    report = mod.run_gate(
        "qapairs секрет натуральный вопрос не должен влиять",
        project_id="nmbot",
        evidence_type="stage",
        definition_of_done="owner source and focused test",
        root=root,
        manifest_path=manifest,
        intents_path=bad_intents,
        target_kind="stage",
        target="v2.search",
    )

    assert report["route"] == "stage"
    assert report["stop_reason"] == "owner_contract_and_test"
    assert [item["path"] for item in report["context"]] == ["scripts/search.py", "tests/test_search.py"]
    assert report["context"][0]["source_symbol"] == "target_symbol"
    assert "intent_card_id" not in report
    assert "intent_card_id" not in report["trace"]
    trace_text = json.dumps(report["trace"], ensure_ascii=False)
    assert "qapairs" not in trace_text
    assert "секрет" not in trace_text


def test_strict_stage_rejects_path_id_as_too_broad(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    report = mod.run_gate(
        "ignored",
        project_id="nmbot",
        evidence_type="stage",
        definition_of_done="one exact stage only",
        root=root,
        manifest_path=manifest,
        target_kind="stage",
        target="v2.turn.v1",
    )

    assert report["route"] == "stage"
    assert report["abstain"] is True
    assert report["context"] == []
    assert report["candidates"] == []
    assert report["denial"] == "strict_stage_path_target_too_broad"
    assert report["stop_reason"] == "no_candidate_answers"


def test_strict_symbol_exact_target_and_ambiguity_requires_owner(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    (root / "scripts/other.py").write_text("def target_symbol():\n    return 'other'\n", encoding="utf-8")
    data = json.loads((root / manifest).read_text(encoding="utf-8"))
    data["sources"].append({"path": "scripts/other.py", "module": "other", "type": "python", "owner": "other", "status": "active"})
    (root / manifest).write_text(json.dumps(data), encoding="utf-8")

    ambiguous = mod.run_gate("любая фраза", project_id="nmbot", evidence_type="symbol", definition_of_done="exact symbol", root=root, manifest_path=manifest, target_kind="symbol", target="target_symbol")
    scoped = mod.run_gate("любая фраза", project_id="nmbot", evidence_type="symbol", definition_of_done="exact symbol", root=root, manifest_path=manifest, target_kind="symbol", target="target_symbol", target_owner="scripts/search.py")

    assert ambiguous["abstain"] is True
    assert ambiguous["denial"] == "strict_symbol_target_ambiguous"
    assert ambiguous["context"] == []
    assert scoped["route"] == "ast"
    assert [item["path"] for item in scoped["context"]] == ["scripts/search.py", "tests/test_search.py"]


def test_strict_docs_exact_owner_anchor_and_missing_fail_closed(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    ok = mod.run_gate("неважный вопрос", project_id="nmbot", evidence_type="docs", definition_of_done="exact docs", root=root, manifest_path=manifest, target_kind="docs", target="## Deterministic contract", target_owner="docs/search.md")
    wrong_owner = mod.run_gate("неважный вопрос", project_id="nmbot", evidence_type="docs", definition_of_done="exact docs", root=root, manifest_path=manifest, target_kind="docs", target="## Deterministic contract", target_owner="docs/missing.md")
    missing_anchor = mod.run_gate("неважный вопрос", project_id="nmbot", evidence_type="docs", definition_of_done="exact docs", root=root, manifest_path=manifest, target_kind="docs", target="## Missing", target_owner="docs/search.md")

    assert ok["route"] == "docs"
    assert ok["context"][0]["path"] == "docs/search.md"
    assert ok["context"][0]["anchor"] == "## Deterministic contract"
    assert wrong_owner["context"] == []
    assert wrong_owner["denial"] == "strict_docs_owner_not_active"
    assert missing_anchor["context"] == []
    assert missing_anchor["denial"] == "strict_docs_anchor_not_found"


def test_overlong_single_line_selects_zero_context_and_budget_stop(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    (root / "scripts/search.py").write_text("def target_symbol(x='" + ("A" * 200) + "'):\n    return x\n", encoding="utf-8")

    report = mod.run_gate(
        "v2.search",
        project_id="nmbot",
        evidence_type="stage",
        definition_of_done="bounded answer",
        root=root,
        manifest_path=manifest,
        max_sources=2,
        max_lines=5,
        max_chars=20,
    )

    assert report["context"] == []
    assert report["stop_reason"] == "context_budget_reached"
    assert report["budget_status"] == "context_budget_reached"
    assert report["trace"]["selected_source_count"] == 0
    assert report["trace"]["lines_loaded"] <= 5
    assert report["trace"]["characters_loaded"] <= 20


def test_long_symbol_span_is_an_honest_partial_budget_stop(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    body = "\n".join(f"    value_{index} = {index}" for index in range(1, 12))
    (root / "scripts/search.py").write_text(
        f"def target_symbol():\n{body}\n    return value_11\n",
        encoding="utf-8",
    )

    report = mod.run_gate(
        "ignored by strict executor",
        project_id="nmbot",
        evidence_type="symbol",
        definition_of_done="read exact symbol",
        root=root,
        manifest_path=manifest,
        target_kind="symbol",
        target="target_symbol",
        target_owner="scripts/search.py",
        max_lines=5,
        max_chars=8000,
    )

    assert report["context"][0]["start_line"] == 1
    assert report["context"][0]["end_line"] == 5
    assert report["budget_status"] == "context_budget_reached"
    assert report["stop_reason"] == "context_budget_reached"
    assert report["trace"]["lines_loaded"] == 5


def test_symbol_phrase_requires_explicit_candidate_selection(tmp_path: Path) -> None:
    report = run_gate(tmp_path, "где target_symbol", "symbol")

    assert report["route"] == "selection_required"
    assert report["abstain"] is True
    assert report["context"] == []
    assert report["candidates"][0]["candidate_id"] == "c1"
    assert report["candidates"][0]["target_spec"]["target"] == "target_symbol"


def test_symbol_related_test_without_symbol_is_omitted_not_line_one(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    (root / "scripts/other.py").write_text("def unrelated_symbol():\n    return True\n", encoding="utf-8")
    (root / "tests/test_other.py").write_text("def test_unrelated():\n    assert True\n", encoding="utf-8")
    data = json.loads((root / manifest).read_text(encoding="utf-8"))
    data["sources"].extend([
        {"path": "scripts/other.py", "module": "other", "type": "python", "owner": "other", "status": "active"},
        {"path": "tests/test_other.py", "module": "other", "type": "test", "owner": "tests", "status": "active"},
    ])
    (root / manifest).write_text(json.dumps(data), encoding="utf-8")

    report = mod.run_gate("unrelated_symbol", project_id="nmbot", evidence_type="symbol", definition_of_done="bounded", root=root, manifest_path=manifest)

    assert report["route"] == "ast"
    assert report["stop_reason"] == "definition_of_done"
    assert [item["path"] for item in report["context"]] == ["scripts/other.py"]
    assert "tests/test_other.py" not in [item["path"] for item in report["context"]]


def test_current_source_phrase_requires_explicit_candidate_selection(tmp_path: Path) -> None:
    report = run_gate(tmp_path, "где target_symbol", "current-source", max_sources=2)

    assert report["route"] == "selection_required"
    assert report["context"] == []
    assert report["candidates"][0]["candidate_id"] == "c1"


def test_related_test_range_is_narrowest_nested_span_not_line_one(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    (root / "tests/test_search.py").write_text(
        "from scripts.search import target_symbol\n\n"
        "def test_other():\n"
        "    assert True\n\n"
        "class TestSearch:\n"
        "    def test_target_symbol_nested(self):\n"
        "        assert target_symbol() == 'ok'\n"
        "        assert True\n",
        encoding="utf-8",
    )

    report = mod.run_gate("target_symbol", project_id="nmbot", evidence_type="symbol", definition_of_done="bounded", root=root, manifest_path=manifest)

    test_item = report["context"][1]
    assert test_item["path"] == "tests/test_search.py"
    assert test_item["start_line"] == 7
    assert test_item["end_line"] == 9


def test_stage_related_test_range_is_focused_by_source_symbol_not_line_one(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    (root / "tests/test_search.py").write_text(
        "from scripts.search import target_symbol\n\n"
        "def test_other():\n"
        "    assert True\n\n"
        "def test_target_symbol_stage():\n"
        "    assert target_symbol() == 'ok'\n",
        encoding="utf-8",
    )

    report = mod.run_gate("v2.search", project_id="nmbot", evidence_type="stage", definition_of_done="bounded", root=root, manifest_path=manifest)

    test_item = report["context"][1]
    assert test_item["path"] == "tests/test_search.py"
    assert test_item["start_line"] == 6
    assert test_item["end_line"] == 7


def test_stage_test_without_source_symbol_is_omitted_not_line_one(tmp_path: Path) -> None:
    mod = load_module()
    root, _manifest = make_root(tmp_path)
    (root / "tests/test_search.py").write_text("def test_unrelated_stage():\n    assert True\n", encoding="utf-8")
    items = mod._stage_context_items([
        {"kind": "stage", "stage_id": "v2.search", "stage_field": "source", "path": "scripts/search.py", "start_line": 1, "end_line": 2, "source_symbol": "target_symbol"},
        {"kind": "stage", "stage_id": "v2.search", "stage_field": "test", "path": "tests/test_search.py", "start_line": 1, "end_line": 2, "source_symbol": "target_symbol"},
    ], root=root)

    assert [item["path"] for item in items] == ["scripts/search.py"]


def test_stage_test_module_scope_import_only_is_omitted_from_context(tmp_path: Path) -> None:
    mod = load_module()
    root, _manifest = make_root(tmp_path)
    (root / "tests/test_search.py").write_text(
        "from scripts.search import target_symbol\n\n"
        "def test_unrelated_stage():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    items = mod._stage_context_items([
        {"kind": "stage", "stage_id": "v2.search", "stage_field": "source", "path": "scripts/search.py", "start_line": 1, "end_line": 2, "source_symbol": "target_symbol"},
        {"kind": "stage", "stage_id": "v2.search", "stage_field": "test", "path": "tests/test_search.py", "start_line": 1, "end_line": 4, "source_symbol": "target_symbol"},
    ], root=root)

    context, _budget = mod._select_context(items, root=root, max_sources=2, max_lines=80, max_chars=8000, do_not_open=())
    report = mod._base_report("nmbot", "stage", "definition_of_done", abstain=False, context=context, definition_of_done="source-only")

    assert report["stop_reason"] == "definition_of_done"
    assert [item["path"] for item in report["context"]] == ["scripts/search.py"]
    assert "tests/test_search.py" not in [item["path"] for item in report["context"]]


def test_stage_test_comment_or_string_symbol_only_is_omitted_from_context(tmp_path: Path) -> None:
    mod = load_module()
    root, _manifest = make_root(tmp_path)
    base_items = [
        {"kind": "stage", "stage_id": "v2.search", "stage_field": "source", "path": "scripts/search.py", "start_line": 1, "end_line": 2, "source_symbol": "target_symbol"},
        {"kind": "stage", "stage_id": "v2.search", "stage_field": "test", "path": "tests/test_search.py", "start_line": 1, "end_line": 4, "source_symbol": "target_symbol"},
    ]
    for text in (
        "def test_comment_only():\n    # target_symbol should not count\n    assert True\n",
        "def test_string_only():\n    assert 'target_symbol'\n",
    ):
        (root / "tests/test_search.py").write_text(text, encoding="utf-8")

        items = mod._stage_context_items(base_items, root=root)

        assert [item["path"] for item in items] == ["scripts/search.py"]


def test_docs_route_requires_explicit_anchor_selection(tmp_path: Path) -> None:
    report = run_gate(tmp_path, "docs Deterministic contract", "docs")

    assert report["route"] == "selection_required"
    assert report["context"] == []
    assert len(report["candidates"]) <= 5
    assert report["candidates"][0]["path"] == "docs/search.md"


def test_history_and_production_are_zero_context_handoffs(tmp_path: Path) -> None:
    history = run_gate(tmp_path, "old decision", "history")
    production = run_gate(tmp_path, "current live status", "production")

    assert history["route"] == "canonical_notebook_handoff"
    assert production["route"] == "fresh_authorized_production_handoff"
    for report in (history, production):
        assert report["abstain"] is True
        assert report["context"] == []
        assert report["trace"]["lines_loaded"] == 0
        assert report["trace"]["characters_loaded"] == 0


def test_ambiguous_clarifies_without_navigation_stage_drift_and_deep_audit(tmp_path: Path) -> None:
    clarify = run_gate(tmp_path, "что смотреть дальше", "ambiguous")
    stage = run_gate(tmp_path, "v2.search и какая погода", "ambiguous")
    audit = run_gate(tmp_path, "full inventory of everything", "ambiguous")

    assert clarify["route"] == "clarify_evidence_type"
    assert clarify["trace"]["candidate_count"] == 0
    assert stage["route"] == "selection_required"
    assert stage["context"] == []
    assert audit["route"] == "deep_audit_handoff"
    assert audit["stop_reason"] == "deep_audit_required"


def test_russian_audit_markers_include_all_variants(tmp_path: Path) -> None:
    for word in ("все", "всё", "полностью"):
        report = run_gate(tmp_path, f"проверь {word} маршруты", "ambiguous")
        assert report["route"] == "deep_audit_handoff"
        assert report["stop_reason"] == "deep_audit_required"


def test_foreign_mentions_fail_closed_and_approved_dependency_is_one_hop(tmp_path: Path) -> None:
    denied = run_gate(tmp_path, "check qapairs contract", "docs")
    approved = run_gate(
        tmp_path,
        "check qapairs contract",
        "docs",
        dependency={
            "scope_id": "faq-upload",
            "owner_project": "qapairs-daemon",
            "consumer_project": "nmbot",
            "canonical_notebook": "qapairs-daemon",
            "contract_ref": "contracts/faq-upload",
            "max_depth": 1,
            "max_records": 2,
        },
    )

    assert denied["route"] == "fail_closed_cross_project"
    assert denied["context"] == []
    assert approved["route"] == "approved_one_hop_dependency"
    assert approved["stop_reason"] == "expansion_exhausted"
    assert approved["trace"]["cross_project_notebooks"] == 1
    assert approved["trace"]["selected_source_count"] == 0


def test_do_not_open_budgets_dedupe_and_fallback_candidate_only(tmp_path: Path) -> None:
    blocked = run_gate(tmp_path, "v2.search", "stage", do_not_open=["scripts/search.py"], max_sources=2)
    budgeted = run_gate(tmp_path, "v2.search", "stage", max_sources=1, max_lines=1, max_chars=20)
    fallback = run_gate(tmp_path, "search prompt title", "current-source")

    assert "scripts/search.py" not in [item["path"] for item in blocked["context"]]
    assert len(blocked["context"]) == len({item["path"] for item in blocked["context"]})
    assert budgeted["trace"]["selected_source_count"] == 1
    assert budgeted["trace"]["lines_loaded"] <= 1
    assert fallback["route"] == "selection_required"
    assert all(item["candidate_only"] is True for item in fallback["candidates"])
    assert len(fallback["candidates"]) <= 5


def test_candidate_metadata_keeps_same_path_distinct_targets(tmp_path: Path) -> None:
    mod = load_module()
    items = [
        {"candidate_id": "c1", "path": "scripts/same.py", "start_line": 1, "end_line": 2, "target_spec": {"target_kind": "symbol", "target": "same", "target_owner": "scripts/same.py", "owner_path": "scripts/same.py"}},
        {"candidate_id": "c2", "path": "scripts/same.py", "start_line": 5, "end_line": 6, "target_spec": {"target_kind": "symbol", "target": "same", "target_owner": "scripts/same.py", "owner_path": "scripts/same.py"}},
    ]

    candidates = mod._candidate_list(items, max_sources=5, do_not_open=[])

    assert [item["candidate_id"] for item in candidates] == ["c1", "c2"]
    assert [item["start_line"] for item in candidates] == [1, 5]


def test_do_not_open_glob_blocks_tests_and_rejects_unsafe_pattern(tmp_path: Path) -> None:
    blocked = run_gate(tmp_path, "v2.search", "stage", do_not_open=["tests/*"], max_sources=2)

    assert "tests/test_search.py" not in [item["path"] for item in blocked["context"]]
    assert [item["path"] for item in blocked["context"]] == ["scripts/search.py", "docs/search.md"]

    mod = load_module()
    root, manifest = make_root(tmp_path)
    with pytest.raises(mod.GateError, match="do_not_open pattern"):
        mod.run_gate("v2.search", project_id="nmbot", evidence_type="stage", definition_of_done="done", do_not_open=["../tests/*"], root=root, manifest_path=manifest)


def test_intent_registry_schema_and_invalid_cards_fail_closed(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    bad_path = Path("config/nmbot_context_gate_intents.json")
    (root / bad_path).write_text(json.dumps({"schema": "wrong", "cards": []}), encoding="utf-8")
    with pytest.raises(mod.GateError, match="intent registry schema"):
        mod.run_gate("писатель поиск", project_id="nmbot", evidence_type="stage", definition_of_done="done", root=root, manifest_path=manifest, intents_path=bad_path)

    write_intents(root, [base_intent_card(extra="nope")])
    with pytest.raises(mod.GateError, match="intent card keys"):
        mod.run_gate("писатель поиск", project_id="nmbot", evidence_type="stage", definition_of_done="done", root=root, manifest_path=manifest, intents_path=bad_path)

    write_intents(root, [base_intent_card(owner_path="missing.py")])
    with pytest.raises(mod.GateError, match="owner_path is not active"):
        mod.run_gate("писатель поиск", project_id="nmbot", evidence_type="stage", definition_of_done="done", root=root, manifest_path=manifest, intents_path=bad_path)

    write_intents(root, [base_intent_card(evidence_type="symbol", resolver_query="missing_symbol")])
    with pytest.raises(mod.GateError, match="symbol_missing"):
        mod.run_gate("писатель поиск", project_id="nmbot", evidence_type="symbol", definition_of_done="done", root=root, manifest_path=manifest, intents_path=bad_path)


def test_intent_stage_natural_russian_terms_resolve_canonical_stage(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    intents = write_intents(root, [base_intent_card(match_all=["ПИСАТЕЛЬ", "поиск"], resolver_query="v2.search")])

    report = mod.run_gate("где живёт писатель поиск", project_id="nmbot", evidence_type="stage", definition_of_done="done", root=root, manifest_path=manifest, intents_path=intents)

    assert report["route"] == "stage"
    assert report["intent_card_id"] == "stage.search.writer"
    assert report["trace"]["intent_card_id"] == "stage.search.writer"
    assert report["context"][0]["path"] == "scripts/search.py"
    assert "где живёт" not in json.dumps(report, ensure_ascii=False)


def test_intent_exact_evidence_type_isolation_and_no_match_preserves_behavior(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    intents = write_intents(root, [base_intent_card(match_all=["писатель", "поиск"])])

    isolated = mod.run_gate("писатель поиск", project_id="nmbot", evidence_type="docs", definition_of_done="done", root=root, manifest_path=manifest, intents_path=intents)
    plain = mod.run_gate("писатель поиск", project_id="nmbot", evidence_type="docs", definition_of_done="done", root=root, manifest_path=manifest, intents_path=None)
    no_match = mod.run_gate("совсем другой вопрос", project_id="nmbot", evidence_type="stage", definition_of_done="done", root=root, manifest_path=manifest, intents_path=intents)
    no_match_plain = mod.run_gate("совсем другой вопрос", project_id="nmbot", evidence_type="stage", definition_of_done="done", root=root, manifest_path=manifest, intents_path=None)

    assert "intent_card_id" not in isolated
    assert isolated["route"] == plain["route"]
    assert no_match["route"] == no_match_plain["route"]
    assert no_match["trace"] == no_match_plain["trace"]


def test_intent_deterministic_tie_more_terms_then_id(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    intents = write_intents(root, [
        base_intent_card(id="z.short", match_all=["поиск"], resolver_query="v2.turn.v1"),
        base_intent_card(id="b.long", match_all=["писатель", "поиск"], resolver_query="v2.search"),
        base_intent_card(id="a.long", match_all=["писатель", "поиск"], resolver_query="v2.search"),
    ])

    report = mod.run_gate("писатель поиск", project_id="nmbot", evidence_type="stage", definition_of_done="done", root=root, manifest_path=manifest, intents_path=intents)

    assert report["intent_card_id"] == "a.long"


def test_intent_symbol_current_source_and_docs_resolution(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    intents = write_intents(root, [
        base_intent_card(id="symbol.search.target", evidence_type="symbol", match_all=["точная", "функция"], resolver_query="target_symbol", owner_path="scripts/search.py"),
        base_intent_card(id="current.search.target", evidence_type="current-source", match_all=["текущий", "колбек"], resolver_query="target_symbol", owner_path="scripts/search.py"),
        base_intent_card(id="docs.search.contract", evidence_type="docs", match_all=["договор", "поиск"], resolver_query="docs Deterministic contract", owner_path="docs/search.md"),
    ])

    symbol = mod.run_gate("где точная функция", project_id="nmbot", evidence_type="symbol", definition_of_done="done", root=root, manifest_path=manifest, intents_path=intents)
    current = mod.run_gate("покажи текущий колбек", project_id="nmbot", evidence_type="current-source", definition_of_done="done", root=root, manifest_path=manifest, intents_path=intents)
    docs = mod.run_gate("где договор про поиск", project_id="nmbot", evidence_type="docs", definition_of_done="done", root=root, manifest_path=manifest, intents_path=intents)

    assert symbol["route"] == "ast"
    assert current["route"] == "current_source"
    assert docs["route"] == "selection_required"
    assert symbol["intent_card_id"] == "symbol.search.target"
    assert current["intent_card_id"] == "current.search.target"
    assert docs["intent_card_id"] == "docs.search.contract"


def test_docs_intent_owner_scoped_anchor_can_be_outside_global_top3(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    for rel in ("docs/a.md", "docs/b.md", "docs/c.md", "docs/zz_owner.md"):
        (root / rel).write_text("# docs owner-target\n", encoding="utf-8")
    data = json.loads((root / manifest).read_text(encoding="utf-8"))
    data["sources"].extend([
        {"path": "docs/a.md", "module": "crowded", "type": "doc", "owner": "docs", "status": "active"},
        {"path": "docs/b.md", "module": "crowded", "type": "doc", "owner": "docs", "status": "active"},
        {"path": "docs/c.md", "module": "crowded", "type": "doc", "owner": "docs", "status": "active"},
        {"path": "docs/zz_owner.md", "module": "owner", "type": "doc", "owner": "docs", "status": "active"},
    ])
    (root / manifest).write_text(json.dumps(data), encoding="utf-8")
    intents = write_intents(root, [base_intent_card(
        id="docs.owner.scoped",
        evidence_type="docs",
        match_all=["внешний", "контракт"],
        resolver_query="docs owner-target",
        owner_path="docs/zz_owner.md",
    )])

    report = mod.run_gate("где внешний контракт", project_id="nmbot", evidence_type="docs", definition_of_done="done", root=root, manifest_path=manifest, intents_path=intents)

    assert report["route"] == "selection_required"
    assert report["intent_card_id"] == "docs.owner.scoped"
    assert report["trace"]["intent_card_id"] == "docs.owner.scoped"
    assert report["context"] == []
    assert [item["path"] for item in report["candidates"]] == ["docs/zz_owner.md"]


def test_docs_intent_nonpositive_owner_anchor_is_rejected(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    intents = write_intents(root, [base_intent_card(
        id="docs.owner.missing",
        evidence_type="docs",
        match_all=["внешний", "контракт"],
        resolver_query="zzzzzz",
        owner_path="docs/search.md",
    )])

    with pytest.raises(mod.GateError, match="found no active anchor in owner_path"):
        mod.run_gate("где внешний контракт", project_id="nmbot", evidence_type="docs", definition_of_done="done", root=root, manifest_path=manifest, intents_path=intents)


def test_intent_do_not_open_still_blocks_and_trace_is_privacy_safe(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    intents = write_intents(root, [base_intent_card(match_all=["секретный", "поиск"])])

    report = mod.run_gate("секретный поиск SECRET_TOKEN_SHOULD_NOT_APPEAR", project_id="nmbot", evidence_type="stage", definition_of_done="done", do_not_open=["scripts/search.py"], root=root, manifest_path=manifest, intents_path=intents)

    assert report["intent_card_id"] == "stage.search.writer"
    assert "scripts/search.py" not in [item["path"] for item in report["context"]]
    trace_text = json.dumps(report["trace"], ensure_ascii=False)
    assert "SECRET_TOKEN_SHOULD_NOT_APPEAR" not in trace_text
    assert "секретный поиск" not in trace_text
    assert set(report["trace"]) == {"schema", "project_id", "route", "candidate_ids", "selected_source_ids", "candidate_count", "selected_source_count", "expansion_hops", "cross_project_notebooks", "lines_loaded", "characters_loaded", "stop_reason", "intent_card_id"}


def test_invalid_cli_and_dependency_are_rejected(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)

    with pytest.raises(mod.GateError, match="only local project_id"):
        mod.run_gate("v2.search", project_id="cc-daemons", evidence_type="stage", definition_of_done="done", root=root, manifest_path=manifest)
    with pytest.raises(mod.GateError, match="budgets"):
        mod.run_gate("v2.search", project_id="nmbot", evidence_type="stage", definition_of_done="done", max_sources=3, root=root, manifest_path=manifest)
    with pytest.raises(mod.GateError, match="max_depth"):
        mod.run_gate("contract", project_id="nmbot", evidence_type="docs", definition_of_done="done", dependency={"scope_id": "x", "owner_project": "qapairs", "consumer_project": "nmbot", "canonical_notebook": "qapairs", "contract_ref": "c", "max_depth": 2}, root=root, manifest_path=manifest)
    with pytest.raises(mod.GateError, match="strict mode requires"):
        mod.run_gate("v2.search", project_id="nmbot", evidence_type="stage", definition_of_done="done", root=root, manifest_path=manifest, target_kind="stage")
    with pytest.raises(mod.GateError, match="must match"):
        mod.run_gate("v2.search", project_id="nmbot", evidence_type="docs", definition_of_done="done", root=root, manifest_path=manifest, target_kind="stage", target="v2.search")
    with pytest.raises(mod.GateError, match="requires --target-owner"):
        mod.run_gate("docs", project_id="nmbot", evidence_type="docs", definition_of_done="done", root=root, manifest_path=manifest, target_kind="docs", target="## Deterministic contract")


def test_strict_cli_real_stage_and_validation() -> None:
    ok = subprocess.run(
        [sys.executable, "scripts/nmbot_context_gate.py", "ignored natural question", "--project-id", "nmbot", "--evidence-type", "stage", "--target-kind", "stage", "--target", "v2.search", "--definition-of-done", "owner source and focused test", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    bad = subprocess.run(
        [sys.executable, "scripts/nmbot_context_gate.py", "ignored", "--project-id", "nmbot", "--evidence-type", "docs", "--target-kind", "stage", "--target", "v2.search", "--definition-of-done", "done", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["route"] == "stage"
    assert bad.returncode == 2
    assert "must match" in bad.stderr


def test_dependency_rejects_extra_unknown_owner_and_unsafe_oversized_refs(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest = make_root(tmp_path)
    base = {"scope_id": "faq-upload", "owner_project": "qapairs", "consumer_project": "nmbot", "canonical_notebook": "qapairs", "contract_ref": "contracts/faq-upload"}

    for field in ("body", "history"):
        with pytest.raises(mod.GateError, match="unsupported fields"):
            mod.run_gate("contract", project_id="nmbot", evidence_type="docs", definition_of_done="done", dependency={**base, field: "nope"}, root=root, manifest_path=manifest)
    with pytest.raises(mod.GateError, match="known foreign"):
        mod.run_gate("contract", project_id="nmbot", evidence_type="docs", definition_of_done="done", dependency={**base, "owner_project": "unknown-owner"}, root=root, manifest_path=manifest)
    for unsafe in ("../escape", "/abs/ref", "https://example.test/contract", "secret-token", "x" * 121):
        with pytest.raises(mod.GateError, match="compact safe ref"):
            mod.run_gate("contract", project_id="nmbot", evidence_type="docs", definition_of_done="done", dependency={**base, "contract_ref": unsafe}, root=root, manifest_path=manifest)
    with pytest.raises(mod.GateError, match="allowed_query_types"):
        mod.run_gate("contract", project_id="nmbot", evidence_type="docs", definition_of_done="done", dependency={**base, "allowed_query_types": ["ok", "bad query"]}, root=root, manifest_path=manifest)


def test_trace_privacy_contains_ids_and_counts_not_query_or_bodies(tmp_path: Path) -> None:
    secretish = "v2.search SECRET_TOKEN_SHOULD_NOT_APPEAR"
    report = run_gate(tmp_path, secretish, "stage")
    trace_text = json.dumps(report["trace"], ensure_ascii=False)

    assert "SECRET_TOKEN_SHOULD_NOT_APPEAR" not in trace_text
    assert "Read this first" not in trace_text
    assert "query" not in report["trace"]
    assert set(report["trace"]) == {"schema", "project_id", "route", "candidate_ids", "selected_source_ids", "candidate_count", "selected_source_count", "expansion_hops", "cross_project_notebooks", "lines_loaded", "characters_loaded", "stop_reason"}


def test_context_gate_uses_no_network_model_runtime_or_subprocess_imports() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {"requests", "httpx", "aiohttp", "urllib", "socket", "subprocess", "openai", "anthropic", "ollama", "nmbot_v2", "nmbot_v0", "notebooklm"}
    assert not (imported & forbidden)


def test_cli_json_smoke_real_repo() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/nmbot_context_gate.py", "resolve_response_path", "--project-id", "nmbot", "--evidence-type", "symbol", "--definition-of-done", "exact symbol plus test", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == "nmbot.context_gate.v1"
    assert payload["route"] == "ast"
    assert payload["trace"]["schema"] == "bounded-retrieval.v1"


def test_cli_real_stage_v2_search_uses_exact_source_symbol_and_focused_test() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/nmbot_context_gate.py",
            "v2.search",
            "--project-id",
            "nmbot",
            "--evidence-type",
            "stage",
            "--definition-of-done",
            "owner source and focused test",
            "--max-sources",
            "2",
            "--max-lines",
            "80",
            "--max-chars",
            "8000",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["route"] == "stage"
    assert payload["stop_reason"] == "owner_contract_and_test"
    assert payload["trace"]["selected_source_count"] <= 2
    assert payload["trace"]["lines_loaded"] <= 80
    assert payload["trace"]["characters_loaded"] <= 8000
    assert payload["context"][0]["path"] == "scripts/nmbot_runtime_adapter.py"
    assert payload["context"][0]["source_symbol"] == "search"
    assert payload["context"][0]["start_line"] > 1
    assert payload["context"][0]["end_line"] >= payload["context"][0]["start_line"]
    assert payload["context"][1]["path"] == "tests/test_nmbot_v2_search_contract_runtime.py"
    assert payload["context"][1]["start_line"] > 1
