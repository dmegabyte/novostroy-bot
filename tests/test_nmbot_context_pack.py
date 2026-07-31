from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_context_pack.py"
WRAPPER = ROOT / "scripts" / "nmbot.py"


def load_context_module():
    spec = importlib.util.spec_from_file_location("nmbot_context_pack_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def load_wrapper_module():
    spec = importlib.util.spec_from_file_location("nmbot_wrapper_context_test", WRAPPER)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_list_prints_known_pack_ids() -> None:
    result = subprocess.run([sys.executable, "scripts/nmbot_context_pack.py", "--list"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema"] == "nmbot.context_pack.v1"
    assert payload["local_read_only"] is True
    assert payload["production_proof"] is False
    assert "prompt/rental" in payload["packs"]
    assert "diagnostics/trace" in payload["packs"]
    assert "runtime/fallback" in payload["packs"]
    assert payload["packs"] == sorted(payload["packs"])


def test_known_rental_pack_includes_expected_docs_and_prompt() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "prompt/rental"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    pack = payload["pack"]
    assert "docs/IDEAL_IRINA_UX.md" in pack["docs"]
    assert "docs/PROMPT_ARCHITECTURE.md" in pack["docs"]
    assert "docs/NMBOT_V2_ANSWER_QUALITY_GATE.md" in pack["docs"]
    assert "docs/SCENARIO_FIELD_MECHANICS_MAP.md" in pack["docs"]
    assert "prompts/scenarios/rental_v1.txt" in pack["files"]
    assert pack["read_first"] == ["docs/IDEAL_IRINA_UX.md", "prompts/scenarios/rental_v1.txt"]
    assert payload["production_proof"] is False


def test_prompt_base_omits_telegram_v1_prompts_and_legacy_pack_is_explicit() -> None:
    base_result = subprocess.run(
        [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "prompt/base"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert base_result.returncode == 0
    base_pack = json.loads(base_result.stdout)["pack"]
    assert "prompts/chat_v1.txt" not in base_pack["files"]
    assert "prompts/text_style_v1.txt" not in base_pack["files"]
    assert "prompts/v2_response_writer.txt" in base_pack["files"]
    assert "prompts/v2_response_formatter.txt" in base_pack["files"]
    assert "prompts/v2_search_mcp.txt" in base_pack["files"]

    legacy_result = subprocess.run(
        [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "legacy/telegram"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert legacy_result.returncode == 0
    legacy_pack = json.loads(legacy_result.stdout)["pack"]
    assert legacy_pack["read_first"] == ["docs/legacy/TELEGRAM_LEGACY.md", "scripts/chat_tester_bot.py"]
    assert "scripts/chat_tester_bot.py" in legacy_pack["files"]
    assert "prompts/chat_v1.txt" in legacy_pack["files"]
    assert "prompts/text_style_v1.txt" in legacy_pack["files"]
    assert any("Explicit legacy" in boundary for boundary in legacy_pack["boundaries"])


def test_unknown_pack_is_rejected() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "prompt/unknown"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "unknown context pack" in result.stderr


def test_trace_and_fallback_packs_keep_local_boundaries() -> None:
    for pack_id in ("diagnostics/trace", "runtime/fallback"):
        result = subprocess.run(
            [sys.executable, "scripts/nmbot_context_pack.py", "--pack", pack_id],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["local_read_only"] is True
        assert payload["production_proof"] is False


def test_runtime_v2_and_fallback_use_active_writer_formatter_not_stale_composer() -> None:
    for pack_id in ("runtime/v2", "runtime/fallback"):
        result = subprocess.run(
            [sys.executable, "scripts/nmbot_context_pack.py", "--pack", pack_id],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        files = payload["pack"]["files"]
        assert "prompts/v2_response_writer.txt" in files
        assert "prompts/v2_response_formatter.txt" in files
        assert "nmbot_v2/response_composer.py" in files
        assert "prompts/v2_response_composer.txt" not in files
        assert any("v2_response_composer.txt" in boundary and "legacy" in boundary.lower() for boundary in payload["pack"]["boundaries"])


def test_diagnostics_trace_pack_includes_response_path_resolver_and_stage_map() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "diagnostics/trace"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    pack = payload["pack"]
    assert "scripts/nmbot_response_path.py" in pack["files"]
    assert "config/nmbot_stage_map.json" in pack["files"]
    assert "python3 scripts/nmbot.py explain --path-id jivo.v2.turn.v1 --json" in pack["checks"]


def test_visible_tree_and_json_manifest_stay_synchronized() -> None:
    mod = load_context_module()
    text = (ROOT / "docs" / "NMBOT_CONTEXT_PACKS.md").read_text(encoding="utf-8")
    visible_tree = text.split("## Parsed JSON manifest", 1)[0]
    manifest = mod.parse_manifest_text(text, root=ROOT)

    sections: dict[str, list[str]] = {}
    parent = ""
    current = ""
    for line in visible_tree.splitlines():
        if line.startswith("- `"):
            parent = line.split("`", 2)[1]
            current = ""
        elif line.startswith("  - `"):
            child = line.split("`", 2)[1]
            current = f"{parent}/{child}"
            sections[current] = [line]
        elif current:
            sections[current].append(line)

    assert set(sections) == {pack["id"] for pack in manifest["packs"]}
    for pack in manifest["packs"]:
        section = "\n".join(sections[pack["id"]])
        for value in (*pack["read_first"], *pack["docs"], *pack["files"], *pack["checks"]):
            assert value in section, f"{pack['id']} tree is missing JSON value: {value}"
        for target in pack["read_first_anchors"]:
            assert target["path"] in section, f"{pack['id']} tree is missing anchor path: {target['path']}"
            assert target["anchor"] in section, f"{pack['id']} tree is missing anchor text: {target['anchor']}"


def test_all_context_packs_have_prioritized_read_first_from_existing_docs_or_files() -> None:
    mod = load_context_module()
    text = (ROOT / "docs" / "NMBOT_CONTEXT_PACKS.md").read_text(encoding="utf-8")
    manifest = mod.parse_manifest_text(text, root=ROOT)

    for pack in manifest["packs"]:
        assert 1 <= len(pack["read_first"]) <= 2
        assert len(pack["read_first_anchors"]) == len(pack["read_first"])
        allowed = set(pack["docs"]) | set(pack["files"])
        assert set(pack["read_first"]).issubset(allowed)
        for index, item in enumerate(pack["read_first"]):
            assert (ROOT / item).exists(), f"{pack['id']} read_first does not exist: {item}"
            target = pack["read_first_anchors"][index]
            assert target["path"] == item
            assert target["anchor"] in (ROOT / item).read_text(encoding="utf-8")


def test_context_pack_json_and_human_render_include_read_first() -> None:
    json_result = subprocess.run(
        [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "diagnostics/trace"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert json_result.returncode == 0
    assert json.loads(json_result.stdout)["pack"]["read_first"] == ["docs/JIVO_DIAGNOSTICS.md", "config/nmbot_stage_map.json"]

    human_result = subprocess.run(
        [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "diagnostics/trace", "--human"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert human_result.returncode == 0
    assert "Read first:" in human_result.stdout
    assert "- docs/JIVO_DIAGNOSTICS.md" in human_result.stdout
    assert "- config/nmbot_stage_map.json" in human_result.stdout


def test_brief_json_context_budget_excludes_full_docs_files_and_keeps_safe_base() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "diagnostics/trace", "--brief"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)

    assert payload["schema"] == "nmbot.context_pack.v1"
    assert payload["local_read_only"] is True
    assert payload["production_proof"] is False
    assert payload["pack"] == {"id": "diagnostics/trace", "title": "Jivo trace and first-failure diagnostics context"}
    assert "docs" not in payload["pack"]
    assert "files" not in payload["pack"]

    budget = payload["context_budget"]
    assert budget["initial_source_limit"] == 2
    assert budget["read_first"] == [
        {"path": "docs/JIVO_DIAGNOSTICS.md", "anchor": "# Jivo/nmbot диагностика"},
        {"path": "config/nmbot_stage_map.json", "anchor": "\"schema\": \"nmbot.stage_map.v1\""},
    ]
    assert budget["primary_local_check"] == "python3 scripts/nmbot_check.py docs"
    assert budget["boundaries"]
    assert budget["expansion_rule"] == "open_next_only_when_referenced_by_current_source"
    assert "source_link_criterion" in budget
    assert "docs" not in budget
    assert "files" not in budget
    assert "checks" not in budget


def test_brief_human_context_budget_is_concise_and_omits_full_lists() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "diagnostics/trace", "--brief", "--human"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    text = result.stdout

    assert "Context pack: diagnostics/trace" in text
    assert "Initial source limit: 2" in text
    assert "Read first:" in text
    assert "- docs/JIVO_DIAGNOSTICS.md — # Jivo/nmbot диагностика" in text
    assert "- config/nmbot_stage_map.json — \"schema\": \"nmbot.stage_map.v1\"" in text
    assert "Primary local check:" in text
    assert "Boundary:" in text
    assert "Expansion rule:" in text
    assert "Required docs:" not in text
    assert "Relevant prompts/source files:" not in text
    assert "Targeted local checks to run separately:" not in text
    assert "docs/NMBOT_RUNBOOK.md" not in text
    assert "scripts/nmbot_diag.sh" not in text


def test_brief_rejects_list_and_missing_pack() -> None:
    for args in (["--brief"], ["--list", "--brief"]):
        result = subprocess.run(
            [sys.executable, "scripts/nmbot_context_pack.py", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 2
        assert "--brief" in result.stderr or "--pack is required" in result.stderr


def test_brief_does_not_execute_local_checks(monkeypatch) -> None:
    mod = load_context_module()

    def fail_if_called(*args, **kwargs):  # pragma: no cover - failure path only
        raise AssertionError("brief rendering must not execute checks")

    monkeypatch.setattr(subprocess, "run", fail_if_called)
    manifest = mod.load_manifest(ROOT / "docs" / "NMBOT_CONTEXT_PACKS.md", root=ROOT)
    output = mod.render_pack(manifest, "diagnostics/trace", human=False, brief=True)
    payload = json.loads(output)
    assert payload["context_budget"]["primary_local_check"] == "python3 scripts/nmbot_check.py docs"


def test_materialize_starts_at_exact_anchor_and_stops_markdown_section(tmp_path) -> None:
    mod = load_context_module()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("preface\n# Anchor\nbody\n## Child\nchild body\n# Next\nnext body\n", encoding="utf-8")
    text = """<!-- NMBOT_CONTEXT_PACKS_JSON_START -->
```json
{"schema":"nmbot.context_pack.v1","packs":[{"id":"prompt/base","title":"Base","read_first":["docs/a.md"],"read_first_anchors":[{"path":"docs/a.md","anchor":"# Anchor"}],"docs":["docs/a.md"],"files":[],"checks":["python3 scripts/ok.py"],"boundaries":["local only"]}]}
```
<!-- NMBOT_CONTEXT_PACKS_JSON_END -->"""
    manifest = mod.parse_manifest_text(text, root=tmp_path)
    payload = json.loads(mod.render_pack(manifest, "prompt/base", human=False, brief=True, materialize=True, root=tmp_path))
    source = payload["materialized_sources"][0]

    assert source["start_line"] == 2
    assert source["excerpt"].startswith("# Anchor")
    assert "preface" not in source["excerpt"]
    assert "## Child" in source["excerpt"]
    assert "# Next" not in source["excerpt"]


def test_materialize_total_line_and_char_budgets_and_truncation_flag(tmp_path) -> None:
    mod = load_context_module()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("ANCHOR_A\n" + "a" * 200 + "\nmore\n", encoding="utf-8")
    (tmp_path / "docs" / "b.md").write_text("ANCHOR_B\n" + "b" * 200 + "\nmore\n", encoding="utf-8")
    text = """<!-- NMBOT_CONTEXT_PACKS_JSON_START -->
```json
{"schema":"nmbot.context_pack.v1","packs":[{"id":"prompt/base","title":"Base","read_first":["docs/a.md","docs/b.md"],"read_first_anchors":[{"path":"docs/a.md","anchor":"ANCHOR_A"},{"path":"docs/b.md","anchor":"ANCHOR_B"}],"docs":["docs/a.md","docs/b.md"],"files":[],"checks":["python3 scripts/ok.py"],"boundaries":["local only"]}]}
```
<!-- NMBOT_CONTEXT_PACKS_JSON_END -->"""
    manifest = mod.parse_manifest_text(text, root=tmp_path)
    payload = json.loads(mod.render_pack(manifest, "prompt/base", human=False, brief=True, materialize=True, max_lines=4, max_chars=60, root=tmp_path))
    sources = payload["materialized_sources"]

    assert sum(len(item["excerpt"].splitlines()) for item in sources) <= 4
    assert sum(len(item["excerpt"]) for item in sources) <= 60
    assert all(len(item["excerpt"].splitlines()) <= 2 for item in sources)
    assert [item["path"] for item in sources] == ["docs/a.md", "docs/b.md"]
    assert any(item["truncated"] for item in sources)


def test_materialize_marks_plain_source_truncated_by_forward_window(tmp_path) -> None:
    mod = load_context_module()
    lines = ["ANCHOR", *[f"line-{index}" for index in range(mod.CODE_ANCHOR_WINDOW_LINES + 2)]]
    excerpt, truncated = mod._candidate_excerpt_lines(lines, 0, "ANCHOR", mod.MAX_MATERIALIZE_LINES)

    assert len(excerpt) == mod.CODE_ANCHOR_WINDOW_LINES
    assert truncated is True


def test_materialize_human_prints_bounded_excerpts() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "diagnostics/trace", "--brief", "--human", "--materialize", "--max-lines", "6", "--max-chars", "400"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Materialized sources:" in result.stdout
    assert "Hard total budget: 6 lines / 400 chars" in result.stdout
    assert "docs/JIVO_DIAGNOSTICS.md" in result.stdout
    assert "config/nmbot_stage_map.json" in result.stdout


def test_materialize_cli_rejects_invalid_combinations_and_ranges() -> None:
    cases = [
        ["--materialize"],
        ["--list", "--materialize"],
        ["--pack", "diagnostics/trace", "--materialize"],
        ["--pack", "diagnostics/trace", "--brief", "--max-lines", "0", "--materialize"],
        ["--pack", "diagnostics/trace", "--brief", "--max-chars", "20001", "--materialize"],
        ["--pack", "diagnostics/trace", "--brief", "--max-lines", "6"],
        ["--pack", "diagnostics/trace", "--brief", "--max-lines", "80"],
    ]
    for args in cases:
        result = subprocess.run(
            [sys.executable, "scripts/nmbot_context_pack.py", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 2, args


def test_materialize_does_not_execute_subprocess(monkeypatch) -> None:
    mod = load_context_module()

    def fail_if_called(*args, **kwargs):  # pragma: no cover - failure path only
        raise AssertionError("materialize must not execute subprocesses")

    monkeypatch.setattr(subprocess, "run", fail_if_called)
    manifest = mod.load_manifest(ROOT / "docs" / "NMBOT_CONTEXT_PACKS.md", root=ROOT)
    payload = json.loads(mod.render_pack(manifest, "diagnostics/trace", human=False, brief=True, materialize=True, max_lines=6, max_chars=400))
    assert payload["materialized_sources"]


def test_normal_context_pack_output_unchanged_shape_when_brief_absent() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "diagnostics/trace"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "context_budget" not in payload
    assert set(payload["pack"]) == {"id", "title", "read_first", "docs", "files", "checks", "boundaries"}
    assert payload["pack"]["read_first"] == ["docs/JIVO_DIAGNOSTICS.md", "config/nmbot_stage_map.json"]
    assert "docs/NMBOT_RUNBOOK.md" in payload["pack"]["docs"]
    assert "scripts/nmbot_diag.sh" in payload["pack"]["files"]


def test_active_prompt_stage_pointers_are_in_appropriate_active_context_packs() -> None:
    mod = load_context_module()
    manifest = mod.parse_manifest_text((ROOT / "docs" / "NMBOT_CONTEXT_PACKS.md").read_text(encoding="utf-8"), root=ROOT)
    packs = {pack["id"]: pack for pack in manifest["packs"]}
    registry = json.loads((ROOT / "config" / "nmbot_stage_map.json").read_text(encoding="utf-8"))
    stages = registry["stages"]

    writer = stages["v2.response_writer"]["prompt"]
    formatter = stages["v2.response_formatter"]["prompt"]
    search = stages["v2.search"]["prompt"]

    for pack_id in ("runtime/v2", "runtime/fallback"):
        files = packs[pack_id]["files"]
        assert writer in files
        assert formatter in files
        assert "prompts/v2_response_composer.txt" not in files
    assert search in packs["runtime/v2"]["files"]
    assert search in packs["prompt/search"]["files"]


def test_malformed_and_unsafe_manifest_rejected(tmp_path) -> None:
    mod = load_context_module()
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "docs" / "ok.md").write_text("ok", encoding="utf-8")
    (tmp_path / "scripts" / "ok.py").write_text("print('ok')\n", encoding="utf-8")

    malformed = "<!-- NMBOT_CONTEXT_PACKS_JSON_START -->\n```json\n{\n```\n<!-- NMBOT_CONTEXT_PACKS_JSON_END -->"
    try:
        mod.parse_manifest_text(malformed, root=tmp_path)
    except mod.ContextPackError as exc:
        assert "malformed" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("malformed manifest must fail")

    unsafe = """<!-- NMBOT_CONTEXT_PACKS_JSON_START -->
```json
{"schema":"nmbot.context_pack.v1","packs":[{"id":"prompt/base","title":"Base","read_first":["docs/ok.md"],"docs":["docs/ok.md"],"files":["scripts/ok.py"],"checks":["python3 scripts/ok.py; ssh prod"],"boundaries":["local only"]}]}
```
<!-- NMBOT_CONTEXT_PACKS_JSON_END -->"""
    try:
        mod.parse_manifest_text(unsafe, root=tmp_path)
    except mod.ContextPackError as exc:
        assert "unsafe check" in str(exc) or "forbidden" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unsafe manifest must fail")

    invalid_read_first = """<!-- NMBOT_CONTEXT_PACKS_JSON_START -->
```json
{"schema":"nmbot.context_pack.v1","packs":[{"id":"prompt/base","title":"Base","read_first":["docs/missing.md"],"docs":["docs/ok.md"],"files":["scripts/ok.py"],"checks":["python3 scripts/ok.py"],"boundaries":["local only"]}]}
```
<!-- NMBOT_CONTEXT_PACKS_JSON_END -->"""
    try:
        mod.parse_manifest_text(invalid_read_first, root=tmp_path)
    except mod.ContextPackError as exc:
        assert "read_first" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid read_first manifest must fail")

    missing_read_first = """<!-- NMBOT_CONTEXT_PACKS_JSON_START -->
```json
{"schema":"nmbot.context_pack.v1","packs":[{"id":"prompt/base","title":"Base","docs":["docs/ok.md"],"files":["scripts/ok.py"],"checks":["python3 scripts/ok.py"],"boundaries":["local only"]}]}
```
<!-- NMBOT_CONTEXT_PACKS_JSON_END -->"""
    try:
        mod.parse_manifest_text(missing_read_first, root=tmp_path)
    except mod.ContextPackError as exc:
        assert "read_first" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing read_first manifest must fail")


def test_read_first_anchor_manifest_rejections(tmp_path) -> None:
    mod = load_context_module()
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "docs" / "ok.md").write_text("# Stable anchor\nbody\n", encoding="utf-8")
    (tmp_path / "scripts" / "ok.py").write_text("def stable_anchor():\n    pass\n", encoding="utf-8")

    def manifest(anchor_block: str) -> str:
        return f"""<!-- NMBOT_CONTEXT_PACKS_JSON_START -->
```json
{{"schema":"nmbot.context_pack.v1","packs":[{{"id":"prompt/base","title":"Base","read_first":["docs/ok.md","scripts/ok.py"],{anchor_block}"docs":["docs/ok.md"],"files":["scripts/ok.py"],"checks":["python3 scripts/ok.py"],"boundaries":["local only"]}}]}}
```
<!-- NMBOT_CONTEXT_PACKS_JSON_END -->"""

    mismatch = manifest('"read_first_anchors":[{"path":"scripts/ok.py","anchor":"def stable_anchor("},{"path":"docs/ok.md","anchor":"# Stable anchor"}],')
    try:
        mod.parse_manifest_text(mismatch, root=tmp_path)
    except mod.ContextPackError as exc:
        assert "one-to-one" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("mismatched read_first_anchors manifest must fail")

    empty_anchor = manifest('"read_first_anchors":[{"path":"docs/ok.md","anchor":""},{"path":"scripts/ok.py","anchor":"def stable_anchor("}],')
    try:
        mod.parse_manifest_text(empty_anchor, root=tmp_path)
    except mod.ContextPackError as exc:
        assert "non-empty" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("empty read_first_anchors manifest must fail")

    nonexistent_anchor = manifest('"read_first_anchors":[{"path":"docs/ok.md","anchor":"# Missing"},{"path":"scripts/ok.py","anchor":"def stable_anchor("}],')
    try:
        mod.parse_manifest_text(nonexistent_anchor, root=tmp_path)
    except mod.ContextPackError as exc:
        assert "anchor not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("nonexistent read_first_anchors manifest must fail")


def test_manifest_path_rejects_absolute_and_escape() -> None:
    mod = load_context_module()
    for path in ["/tmp/manifest.md", "../manifest.md"]:
        try:
            mod.resolve_manifest_path(path, root=ROOT)
        except mod.ContextPackError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"manifest path must be rejected: {path}")


def test_wrapper_context_delegates_exact_argv_without_direct_external_actions(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 11)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["context", "--pack", "prompt/rental", "--human"]) == 11
    assert calls == [
        {
            "argv": [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "prompt/rental", "--human"],
            "cwd": mod.ROOT,
            "check": False,
        }
    ]
