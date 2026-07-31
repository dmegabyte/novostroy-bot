from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_retrieval.py"
BENCHMARK = ROOT / "config" / "nmbot_retrieval_benchmark.json"
BENCHMARK_V2 = ROOT / "config" / "nmbot_retrieval_benchmark_v2.json"
SOURCE_CARDS = ROOT / "config" / "nmbot_retrieval_source_cards.json"


def load_module():
    spec = importlib.util.spec_from_file_location("nmbot_retrieval_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def make_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "scripts").mkdir()
    (root / "prompts").mkdir()
    (root / "tests").mkdir()
    (root / "config" / "nmbot_stage_map.json").write_text(json.dumps({"schema": "nmbot.stage_map.v1", "stages": {"v2.search": {"source": "scripts/search.py", "doc": "docs/search.md", "test": "tests/test_search.py", "prompt": "prompts/search.txt"}}}), encoding="utf-8")
    files = {
        "docs/search.md": "# Search\nfinance disclaimer first list " * 40 + "\n\n## Jivo\nJivo bridge notes",
        "docs/runtime.md": "# Runtime\nruntime selector and response path",
        "scripts/search.py": "def finance_disclaimer():\n    return 'finance disclaimer first list'\n\nclass Runtime:\n    pass\n",
        "prompts/search.txt": "finance disclaimer prompt section",
        "tests/test_search.py": "def test_jivo_runtime():\n    assert 'jivo runtime'\n",
        "AGENTS.md": "# Agent\nlocal retrieval before grep read",
    }
    for rel, text in files.items():
        (root / rel).write_text(text, encoding="utf-8")
    manifest = {
        "schema": "nmbot.retrieval_sources.v1",
        "sources": [
            {"path": "docs/search.md", "module": "search", "type": "doc", "owner": "docs", "status": "active", "stage_id": "v2.search"},
            {"path": "docs/runtime.md", "module": "runtime", "type": "doc", "owner": "docs", "status": "active"},
            {"path": "scripts/search.py", "module": "search", "type": "python", "owner": "search", "status": "active", "stage_id": "v2.search"},
            {"path": "prompts/search.txt", "module": "search", "type": "prompt", "owner": "prompt", "status": "active", "stage_id": "v2.search"},
            {"path": "tests/test_search.py", "module": "search", "type": "test", "owner": "tests", "status": "active", "stage_id": "v2.search"},
            {"path": "AGENTS.md", "module": "root", "type": "doc", "owner": "agent", "status": "active"},
        ],
    }
    manifest_path = root / "config" / "nmbot_retrieval_sources.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return root, Path("config/nmbot_retrieval_sources.json")


def write_source_cards(root: Path, cards: list[dict]) -> Path:
    path = root / "config" / "nmbot_retrieval_source_cards.json"
    path.write_text(json.dumps({"schema": "nmbot.retrieval_source_cards.v1", "cards": cards}), encoding="utf-8")
    return Path("config/nmbot_retrieval_source_cards.json")


def sample_source_card(path: str = "docs/search.md") -> dict:
    return {
        "path": path,
        "purpose": "Навигация по поиску и finance disclaimer для разработчика.",
        "concepts": ["поиск", "finance disclaimer", "Jivo"],
        "owns": [path],
        "entry_points": [path],
        "tests": ["tests/test_search.py"],
    }


def test_manifest_rejects_forbidden_archive_release_log_paths(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)
    data = json.loads((root / manifest_path).read_text(encoding="utf-8"))
    data["sources"][0]["path"] = "docs/archive/old.md"
    (root / "docs" / "archive").mkdir()
    (root / "docs" / "archive" / "old.md").write_text("old", encoding="utf-8")
    (root / manifest_path).write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(mod.RetrievalError, match="forbidden retrieval source path"):
        mod.load_manifest(manifest_path, root=root)


def test_manifest_validates_stage_link_against_stage_map(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)
    data = json.loads((root / manifest_path).read_text(encoding="utf-8"))
    data["sources"][0]["path"] = "docs/runtime.md"
    (root / manifest_path).write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(mod.RetrievalError, match="does not agree with stage map"):
        mod.load_manifest(manifest_path, root=root)


def test_manifest_normalizes_singular_and_multi_stage_ids(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)
    stage_map = json.loads((root / "config" / "nmbot_stage_map.json").read_text(encoding="utf-8"))
    stage_map["stages"]["v2.search.alias"] = {"source": "scripts/search.py"}
    (root / "config" / "nmbot_stage_map.json").write_text(json.dumps(stage_map), encoding="utf-8")
    data = json.loads((root / manifest_path).read_text(encoding="utf-8"))
    data["sources"][2]["stage_ids"] = ["v2.search.alias", "v2.search"]
    data["sources"][2].pop("stage_id")
    (root / manifest_path).write_text(json.dumps(data), encoding="utf-8")

    manifest = mod.load_manifest(manifest_path, root=root)
    by_path = {item["path"]: item for item in manifest["sources"]}

    assert by_path["docs/search.md"]["stage_ids"] == ["v2.search"]
    assert "stage_id" not in by_path["docs/search.md"]
    assert by_path["scripts/search.py"]["stage_ids"] == ["v2.search", "v2.search.alias"]
    script_chunk = next(chunk for chunk in mod.chunk_manifest(manifest, root=root) if chunk["metadata"]["path"] == "scripts/search.py")
    assert script_chunk["metadata"]["stage_ids"] == ["v2.search", "v2.search.alias"]


def test_current_manifest_covers_stage_map_refs_and_mandatory_owner_sources() -> None:
    mod = load_module()
    manifest = mod.load_manifest(Path("config/nmbot_retrieval_sources.json"), root=ROOT)
    manifest_by_path = {item["path"]: item for item in manifest["sources"]}
    stage_map = json.loads((ROOT / "config" / "nmbot_stage_map.json").read_text(encoding="utf-8"))
    expected_stage_ids_by_path: dict[str, set[str]] = {}
    for stage_id, stage in stage_map["stages"].items():
        for key in ("source", "doc", "test", "prompt"):
            path = stage.get(key)
            if isinstance(path, str):
                expected_stage_ids_by_path.setdefault(path, set()).add(stage_id)

    assert sorted(set(expected_stage_ids_by_path) - set(manifest_by_path)) == []
    for path, expected_stage_ids in sorted(expected_stage_ids_by_path.items()):
        assert manifest_by_path[path].get("stage_ids") == sorted(expected_stage_ids), path
    for path in (
        "scripts/nmbot_retrieval.py",
        "nmbot_v2/execution_path.py",
        "scripts/nmbot_test_agent.py",
        "nmbot_v2/quality.py",
        "tests/test_nmbot_v2_quality.py",
        "docs/NMBOT_RUNTIME_REGISTRY.md",
        "docs/EXPERIMENTS.md",
        "docs/IDEAL_IRINA_UX.md",
    ):
        assert path in manifest_by_path


def test_current_manifest_includes_protocol_doc_and_fits_declared_cap() -> None:
    mod = load_module()
    manifest = mod.load_manifest(Path("config/nmbot_retrieval_sources.json"), root=ROOT)
    manifest_by_path = {item["path"]: item for item in manifest["sources"]}

    assert "docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md" in manifest_by_path
    assert manifest_by_path["docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md"]["status"] == "active"
    assert len(manifest["sources"]) <= mod.MAX_MANIFEST_SOURCES


def test_frozen_retrieval_benchmark_v1_is_unchanged_predeclared_owner_cases() -> None:
    data = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    manifest = load_module().load_manifest(Path("config/nmbot_retrieval_sources.json"), root=ROOT)
    manifest_paths = {item["path"] for item in manifest["sources"]}
    cases = data["cases"]

    assert data["schema"] == "nmbot.retrieval_benchmark.v1"
    assert data["frozen_before_ranking_changes"] is True
    assert len(cases) == 20
    assert {case["category"] for case in cases} == {"natural_ru", "technical", "negative"}
    assert all(set(case) <= {"id", "category", "query", "expected_paths", "expect_abstain"} for case in cases)
    for case in cases:
        assert set(case["expected_paths"]) <= manifest_paths


def test_frozen_retrieval_benchmark_v2_is_new_predeclared_holdout() -> None:
    data = json.loads(BENCHMARK_V2.read_text(encoding="utf-8"))
    manifest = load_module().load_manifest(Path("config/nmbot_retrieval_sources.json"), root=ROOT)
    manifest_paths = {item["path"] for item in manifest["sources"]}
    cases = data["cases"]

    assert data["schema"] == "nmbot.retrieval_benchmark.v2"
    assert data["frozen_before_fts_release_tuning"] is True
    assert 12 <= len(cases) <= 20
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["query"] for case in cases}) == len(cases)
    assert {case["category"] for case in cases} == {"natural_ru", "technical", "negative"}
    assert sum(case["category"] == "negative" for case in cases) >= 2
    for case in cases:
        assert set(case) <= {"id", "category", "query", "expected_paths", "expect_abstain"}
        assert set(case["expected_paths"]) <= manifest_paths
        assert bool(case.get("expect_abstain")) == (case["category"] == "negative")


def test_chunk_ids_are_deterministic_and_bounded_with_metadata(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)
    manifest = mod.load_manifest(manifest_path, root=root)
    first = mod.chunk_manifest(manifest, root=root)
    second = mod.chunk_manifest(manifest, root=root)

    assert [item["id"] for item in first] == [item["id"] for item in second]
    assert all(len(item["text"]) <= mod.MAX_CHUNK_CHARS for item in first)
    assert {"path", "module", "type", "owner", "status", "stage_ids"}.issubset(first[0]["metadata"])


def test_default_index_omits_legacy_sources_and_opt_in_includes_them(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)
    legacy_path = root / "prompts" / "chat_v1.txt"
    legacy_path.write_text("telegram_rollback_marker historical V1 prompt", encoding="utf-8")
    data = json.loads((root / manifest_path).read_text(encoding="utf-8"))
    data["sources"].append({"path": "prompts/chat_v1.txt", "module": "legacy", "type": "prompt", "owner": "telegram", "status": "legacy"})
    (root / manifest_path).write_text(json.dumps(data), encoding="utf-8")
    manifest = mod.load_manifest(manifest_path, root=root)

    default_chunks = mod.chunk_manifest(manifest, root=root)
    legacy_chunks = mod.chunk_manifest(manifest, root=root, include_legacy_sources=True)
    default_report = mod.search_cards("telegram_rollback_marker", root=root, manifest_path=manifest_path)
    opt_in_report = mod.search_cards("telegram_rollback_marker", root=root, manifest_path=manifest_path, include_legacy_sources=True)

    assert "prompts/chat_v1.txt" not in {chunk["metadata"]["path"] for chunk in default_chunks}
    assert "prompts/chat_v1.txt" in {chunk["metadata"]["path"] for chunk in legacy_chunks}
    assert default_report["indexed_source_statuses"] == ["active"]
    assert default_report["abstain"] is True
    assert opt_in_report["indexed_source_statuses"] == ["active", "legacy"]
    assert [card["path"] for card in opt_in_report["cards"]] == ["prompts/chat_v1.txt"]
    assert opt_in_report["cards"][0]["status"] == "legacy"


def test_python_sections_cover_decorators_gaps_constants_and_trailing_statements() -> None:
    mod = load_module()
    text = """import functools

TABLE = {
    "a": 1,
}

@functools.lru_cache()
def decorated():
    return TABLE

BETWEEN = "kept"

class Runtime:
    pass

TRAILING = decorated()
"""
    sections = mod._python_sections(text)
    combined = "\n".join(section[0] for section in sections)

    assert "TABLE =" in combined
    assert "@functools.lru_cache()" in combined
    assert "BETWEEN =" in combined
    assert "TRAILING = decorated()" in combined
    assert next(section for section in sections if "def decorated" in section[0])[1] == 7


def test_bounded_windows_hard_splits_single_overlong_line() -> None:
    mod = load_module()
    text = "x" * (mod.MAX_CHUNK_CHARS + 25)

    sections = list(mod._bounded_windows(text, start_line=10))

    assert [len(section[0]) for section in sections] == [mod.MAX_CHUNK_CHARS, 25]
    assert [(section[1], section[2]) for section in sections] == [(10, 10), (10, 10)]


def test_empty_query_abstains_honestly_without_cards(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)

    report = mod.search_cards("", root=root, manifest_path=manifest_path)

    assert report["schema"] == "nmbot.fts_cards.v1"
    assert report["abstain"] is True
    assert report["cards"] == []
    assert report["requires_session_rerank"] is True
    assert report["fallback_route"] == "docs_stage_map_then_grep_read"


def test_no_lexical_match_abstains_honestly_without_cards(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)

    report = mod.search_cards("unrelated agronomy cucumbers", root=root, manifest_path=manifest_path)

    assert report["abstain"] is True
    assert report["card_count"] == 0
    assert report["cards"] == []
    assert "do not invent evidence" in report["next_step"]


def test_schema_max8_bounds_aggregate_cap_and_metadata_boundaries(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)

    report = mod.search_cards("finance disclaimer first list jivo runtime", root=root, manifest_path=manifest_path, cards=8, excerpt_chars=700)

    assert report["schema"] == "nmbot.fts_cards.v1"
    assert report["card_count"] <= 8
    assert report["total_excerpt_chars"] <= 5600
    assert report["abstain"] is False
    assert report["cards_are_candidates_not_evidence"] is True
    assert report["bm25_weights"] == {"text": 1.0, "path": 3.0, "module": 2.0, "owner": 2.0, "stage_ids": 4.0}
    for card in report["cards"]:
        assert set(card) == {"candidate_id", "path", "line_range", "module", "type", "owner", "status", "stage_ids", "excerpt", "fts_score"}
        assert 0 < len(card["excerpt"]) <= 700
        assert not card["path"].startswith(("docs/archive/", "release_bundles/", "logs/"))


def test_default_output_has_no_source_card_fields(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)
    write_source_cards(root, [sample_source_card()])

    report = mod.search_cards("finance disclaimer first list", root=root, manifest_path=manifest_path, cards=4)

    assert "source_cards_enabled" not in report
    assert "source_cards_schema" not in report
    assert report["card_count"] > 0
    assert all("source_card" not in card for card in report["cards"])


def test_source_cards_flag_attaches_verified_registry_cards(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)
    registry_path = write_source_cards(root, [sample_source_card()])

    report = mod.search_cards("finance disclaimer first list", root=root, manifest_path=manifest_path, cards=4, source_cards=True, source_cards_path=registry_path)

    assert report["source_cards_enabled"] is True
    assert report["source_cards_schema"] == "nmbot.retrieval_source_cards.v1"
    card = next(item for item in report["cards"] if item["path"] == "docs/search.md")
    assert card["source_card"] == sample_source_card()


def test_source_cards_do_not_change_fts_ranking_or_candidate_input(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)
    registry_path = write_source_cards(root, [sample_source_card()])

    baseline = mod.search_cards("finance disclaimer first list jivo runtime", root=root, manifest_path=manifest_path, cards=6)
    enriched = mod.search_cards("finance disclaimer first list jivo runtime", root=root, manifest_path=manifest_path, cards=6, source_cards=True, source_cards_path=registry_path)
    stripped = dict(enriched)
    stripped.pop("source_cards_enabled")
    stripped.pop("source_cards_schema")
    stripped["cards"] = [{key: value for key, value in card.items() if key != "source_card"} for card in enriched["cards"]]

    assert stripped == baseline


def test_source_card_registry_rejects_invalid_schema_unknown_inactive_duplicate_and_malformed(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)
    manifest = mod.load_manifest(manifest_path, root=root)
    registry_path = root / "config" / "nmbot_retrieval_source_cards.json"

    registry_path.write_text(json.dumps({"schema": "wrong", "cards": [sample_source_card()]}), encoding="utf-8")
    with pytest.raises(mod.RetrievalError, match="schema"):
        mod.load_source_card_registry(Path("config/nmbot_retrieval_source_cards.json"), manifest=manifest, root=root)

    registry_path.write_text(json.dumps({"schema": "nmbot.retrieval_source_cards.v1", "cards": [sample_source_card("docs/missing.md")]}), encoding="utf-8")
    with pytest.raises(mod.RetrievalError, match="not in retrieval manifest"):
        mod.load_source_card_registry(Path("config/nmbot_retrieval_source_cards.json"), manifest=manifest, root=root)

    inactive_manifest = json.loads((root / manifest_path).read_text(encoding="utf-8"))
    inactive_manifest["sources"][0]["status"] = "legacy"
    (root / manifest_path).write_text(json.dumps(inactive_manifest), encoding="utf-8")
    inactive = mod.load_manifest(manifest_path, root=root)
    registry_path.write_text(json.dumps({"schema": "nmbot.retrieval_source_cards.v1", "cards": [sample_source_card()]}), encoding="utf-8")
    with pytest.raises(mod.RetrievalError, match="not active"):
        mod.load_source_card_registry(Path("config/nmbot_retrieval_source_cards.json"), manifest=inactive, root=root)

    active = mod.load_manifest(manifest_path, root=root)
    active["sources"][0]["status"] = "active"
    registry_path.write_text(json.dumps({"schema": "nmbot.retrieval_source_cards.v1", "cards": [sample_source_card(), sample_source_card()]}), encoding="utf-8")
    with pytest.raises(mod.RetrievalError, match="duplicate"):
        mod.load_source_card_registry(Path("config/nmbot_retrieval_source_cards.json"), manifest=active, root=root)

    malformed = sample_source_card()
    malformed["extra"] = "nope"
    registry_path.write_text(json.dumps({"schema": "nmbot.retrieval_source_cards.v1", "cards": [malformed]}), encoding="utf-8")
    with pytest.raises(mod.RetrievalError, match="keys must be exactly"):
        mod.load_source_card_registry(Path("config/nmbot_retrieval_source_cards.json"), manifest=active, root=root)


def test_current_source_card_registry_is_exact_pilot_set_and_valid() -> None:
    mod = load_module()
    manifest = mod.load_manifest(Path("config/nmbot_retrieval_sources.json"), root=ROOT)
    cards = mod.load_source_card_registry(Path("config/nmbot_retrieval_source_cards.json"), manifest=manifest, root=ROOT)

    assert set(cards) == {
        "nmbot_v2/response_composer.py",
        "nmbot_v2/runtime.py",
        "nmbot_v2/search_contract.py",
        "scripts/nmbot_n8n_bridge_server.py",
        "scripts/nmbot_response_path.py",
        "scripts/nmbot_context_pack.py",
        "docs/IDEAL_IRINA_UX.md",
        "docs/JIVO_DIAGNOSTICS.md",
        "docs/NMBOT_CONTEXT_PACKS.md",
        "config/nmbot_stage_map.json",
    }


def test_card_and_excerpt_bounds_are_enforced(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)

    with pytest.raises(mod.RetrievalError, match="--cards"):
        mod.search_cards("finance", root=root, manifest_path=manifest_path, cards=9)
    with pytest.raises(mod.RetrievalError, match="--excerpt-chars"):
        mod.search_cards("finance", root=root, manifest_path=manifest_path, excerpt_chars=499)
    with pytest.raises(mod.RetrievalError, match="--excerpt-chars"):
        mod.search_cards("finance", root=root, manifest_path=manifest_path, excerpt_chars=701)


def test_path_dedupe_uses_raw_20_chunks_not_only_chunk_ids(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)


    report = mod.search_cards("finance disclaimer first list", root=root, manifest_path=manifest_path, cards=8)

    paths = [item["path"] for item in report["cards"]]
    assert paths.count("docs/search.md") == 1
    assert len(paths) == len(set(paths))
    assert report["raw_chunk_limit"] == 20


def test_safe_fts_escaping_and_optional_terms_phrases_are_deterministic(tmp_path: Path) -> None:
    mod = load_module()
    root, manifest_path = make_root(tmp_path)

    first = mod.search_cards('finance OR "broken" NEAR ()', root=root, manifest_path=manifest_path, terms=["runtime"], phrases=["Jivo bridge"], cards=4)
    second = mod.search_cards('finance OR "broken" NEAR ()', root=root, manifest_path=manifest_path, terms=["runtime"], phrases=["Jivo bridge"], cards=4)

    assert first == second
    assert first["expansion"] == {"terms": ["runtime"], "phrases": ["Jivo bridge"]}
    assert first["card_count"] > 0


def test_no_remote_process_store_or_vendor_api_in_retrieval_tool() -> None:
    text = SCRIPT.read_text(encoding="utf-8").lower()

    forbidden = ["url" + "lib", "requests", "http://", "sub" + "process", "ol" + "lama", "emb" + "edding", "emb" + "ed", "vec" + "tor", "prov" + "ider", "default_" + "index", "build_" + "index", "cache " + "index"]
    assert [item for item in forbidden if item in text] == []
