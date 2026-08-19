from __future__ import annotations

import importlib.util
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = ROOT / "docs" / "archive" / "working-history" / "2026-07-24"


def _load_context_pack_module():
    script = ROOT / "scripts" / "nmbot_context_pack.py"
    spec = importlib.util.spec_from_file_location("nmbot_context_pack_current_arch_test", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_current_architecture_and_archive_index_exist_and_link_to_existing_targets() -> None:
    current = ROOT / "docs" / "CURRENT_ARCHITECTURE.md"
    archive_index = ROOT / "docs" / "ARCHIVE_INDEX.md"

    assert current.exists()
    assert archive_index.exists()

    text = current.read_text(encoding="utf-8")
    assert "not proof of live production behavior" in text
    assert "docs/BOT_ARCHITECTURE.md" in text
    assert "docs/ARCHIVE_INDEX.md" in text

    expected_links = {
        "AGENTS.md",
        "docs/NMBOT_CONTEXT_PACKS.md",
        "docs/NMBOT_RETRIEVAL.md",
        "docs/NMBOT_OPERATIONS_MAP.md",
        "docs/NMBOT_RUNBOOK.md",
        "docs/BOT_ARCHITECTURE.md",
        "docs/ARCHIVE_INDEX.md",
        "config/nmbot_stage_map.json",
        "scripts/nmbot_response_path.py",
        "scripts/nmbot_retrieval.py",
    }
    for link in expected_links:
        assert (ROOT / link).exists(), f"missing current architecture link target: {link}"

    archived_text = archive_index.read_text(encoding="utf-8")
    assert "docs/archive/working-history/2026-07-24/" in archived_text
    assert "Do not delete archived planning records" in archived_text


def test_root_working_files_are_compact_indexes_not_archived_history_copies() -> None:
    archive_path = "docs/archive/working-history/2026-07-24/"
    old_headers = {
        "task_plan.md": "HISTORICAL/APPEND-ONLY PLAN",
        "findings.md": "HISTORICAL/APPEND-ONLY SNAPSHOT",
        "progress.md": "HISTORICAL/APPEND-ONLY SNAPSHOT",
    }

    for filename, old_header in old_headers.items():
        path = ROOT / filename
        text = path.read_text(encoding="utf-8")
        assert archive_path in text
        assert old_header not in text
        assert len(text.splitlines()) <= 120
        assert path.stat().st_size < 12_000


def test_current_task_capsule_is_compact_and_package3_confirmed() -> None:
    text = (ROOT / "task_plan.md").read_text(encoding="utf-8")
    capsule = text.split("## Current task capsule — Package 6 bounded context materializer", 1)[1].split("## 2026-07-24 — Package 5", 1)[0]

    assert "Status: [implemented locally; awaiting user confirmation]." in capsule
    for field in ("Goal:", "Actual / Contract / Desired:", "Paths, max 5:", "Verification:", "Stop condition:"):
        assert field in capsule
    assert capsule.count("`") // 2 <= 6
    assert "hard total line/character budgets" in capsule

    package5 = text.split("## 2026-07-24 — Package 5", 1)[1].split("## 2026-07-24 — Package 4", 1)[0]
    assert "Status: [implemented locally; awaiting user confirmation]." in package5
    package4 = text.split("## 2026-07-24 — Package 4", 1)[1].split("## 2026-07-24 — Package 3", 1)[0]
    assert "Status: [implemented locally; awaiting user confirmation]." in package4
    assert "Status: [confirmed by user]." in text.split("## 2026-07-24 — Package 3", 1)[1]
    assert "User confirmation of Package 3" in text
    assert "awaiting user confirmation" not in text.split("## 2026-07-24 — Package 3", 1)[1].split("## Recent carried-forward state", 1)[0]


def test_brief_context_rule_is_documented_in_current_entrypoints() -> None:
    current = (ROOT / "docs" / "CURRENT_ARCHITECTURE.md").read_text(encoding="utf-8")
    packs = (ROOT / "docs" / "NMBOT_CONTEXT_PACKS.md").read_text(encoding="utf-8")

    for text in (current, packs):
        assert "--brief --human" in text
        assert "initial source limit" in text
        assert "path — anchor" in text
        assert "current source" in text
        assert "explicit task evidence" in text


def test_materialized_context_rule_is_documented_in_current_entrypoints() -> None:
    current = (ROOT / "docs" / "CURRENT_ARCHITECTURE.md").read_text(encoding="utf-8")
    packs = (ROOT / "docs" / "NMBOT_CONTEXT_PACKS.md").read_text(encoding="utf-8")

    for text in (current, packs):
        assert "--brief --materialize --max-lines 80 --max-chars 8000" in text
        assert "hard total" in text
        assert "read-first anchors" in text
        assert "does not execute checks" in text or "reads only the initial read-first anchors" in text


def test_fts_session_retrieval_flow_is_documented_in_current_entrypoints() -> None:
    current = (ROOT / "docs" / "CURRENT_ARCHITECTURE.md").read_text(encoding="utf-8")
    retrieval = (ROOT / "docs" / "NMBOT_RETRIEVAL.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    for text in (current, retrieval):
        assert "SQLite FTS" in text
        assert "eight" in text or "восьми" in text
        assert "zero to four" in text
        assert "grep" in text and "read" in text
        assert "source-card" in text or "source cards" in text
        assert "not evidence" in text
        assert "Ollama is not" in text or "calls no model" in text
        assert "not production proof" in text or "not proof" in text
    assert "Local deterministic navigate / FTS cards before grep/read | `docs/NMBOT_RETRIEVAL.md`" in agents
    assert "Ollama is not a retrieval fallback" in agents


def test_navigation_command_is_documented_as_local_candidate_only() -> None:
    current = (ROOT / "docs" / "CURRENT_ARCHITECTURE.md").read_text(encoding="utf-8")
    retrieval = (ROOT / "docs" / "NMBOT_RETRIEVAL.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    for text in (current, retrieval, agents):
        assert "navigate" in text
    assert "nmbot.navigation.v1" in retrieval
    assert "fallback=true" in retrieval
    assert "candidate-only" in retrieval
    assert "calls no model" in current or "does not call models" in retrieval
    assert "not a\nmodel-quality solution" in retrieval


def test_context_gate_is_documented_as_local_bounded_enforcement() -> None:
    current = (ROOT / "docs" / "CURRENT_ARCHITECTURE.md").read_text(encoding="utf-8")
    retrieval = (ROOT / "docs" / "NMBOT_RETRIEVAL.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    for text in (current, retrieval, agents):
        assert "context-gate" in text
    assert "bounded-retrieval.v1" in current
    assert "80 lines / 8000 characters" in retrieval
    assert "not bot-runtime or production enforcement" in " ".join(current.split())


def test_package6_status_is_documented_without_erasing_pending_confirmations() -> None:
    plan = (ROOT / "task_plan.md").read_text(encoding="utf-8")
    findings = (ROOT / "findings.md").read_text(encoding="utf-8")
    progress = (ROOT / "progress.md").read_text(encoding="utf-8")

    for text in (plan, findings, progress):
        assert "Package 6" in text
        assert "Package 5" in text
        assert "implemented locally" in text
        assert "awaiting user confirmation" in text

    assert "Package 2 stage lookup/read-first context packs: implemented locally; awaiting" in plan
    assert "Package 4" in plan and "awaiting user confirmation" in plan.split("## 2026-07-24 — Package 4", 1)[1].split("## 2026-07-24 — Package 3", 1)[0]


def test_archived_working_history_preserves_expected_old_markers() -> None:
    archived_files = {
        "task_plan.md": "Package 2 stage lookup and read-first context packs",
        "findings.md": "External Field Sales Registry — initial decision, 2026-07-21",
        "progress.md": "Package 2 stage lookup and read-first packs",
    }

    for filename, marker in archived_files.items():
        path = ARCHIVE_DIR / filename
        assert path.exists(), f"missing archived working-history file: {path}"
        text = path.read_text(encoding="utf-8")
        assert marker in text
        assert len(text.splitlines()) > 500


def test_known_external_historical_reference_points_to_archive() -> None:
    readme = (ROOT / "field_sales_registry" / "v1" / "README.md").read_text(encoding="utf-8")

    assert "docs/archive/working-history/2026-07-24/findings.md" in readme
    assert "External Field Sales Registry — initial decision, 2026-07-21" in readme
    assert not re.search(r"Источник решения: `findings\.md`", readme)


def test_context_packs_prioritize_current_architecture_before_full_contract() -> None:
    mod = _load_context_pack_module()
    manifest = mod.parse_manifest_text((ROOT / "docs" / "NMBOT_CONTEXT_PACKS.md").read_text(encoding="utf-8"), root=ROOT)
    packs = {pack["id"]: pack for pack in manifest["packs"]}

    for pack_id in ("prompt/base", "runtime/fallback"):
        pack = packs[pack_id]
        assert pack["read_first"][0] == "docs/CURRENT_ARCHITECTURE.md"
        assert "docs/CURRENT_ARCHITECTURE.md" in pack["docs"]
        assert "docs/BOT_ARCHITECTURE.md" in pack["docs"]


def test_agents_routes_include_current_architecture_and_archive_index() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "Current high-level system map | `docs/CURRENT_ARCHITECTURE.md`" in text
    assert "Historical planning records and old evidence | `docs/ARCHIVE_INDEX.md`" in text


def test_external_test_target_identity_gate_is_fail_closed() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "NMBOT_RUNBOOK.md").read_text(encoding="utf-8")

    assert "### Test target identity gate" in agents
    assert "before the first paid or external call" in agents
    assert "active TEST release returned by" in agents
    assert "An ad-hoc VPS `/tmp` runner is forbidden" in agents

    assert "#### Mandatory target identity preflight" in runbook
    assert "active_test_release:" in runbook
    assert "candidate_commit:" in runbook
    assert "decision: proceed | stop_mismatch" in runbook
    assert "This stop happens before file transfer and before provider/MCP/Jivo calls." in runbook
