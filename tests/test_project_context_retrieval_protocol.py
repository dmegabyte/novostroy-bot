from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIFIED = ROOT / "docs" / "PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md"
ADAPTIVE = ROOT / "docs" / "NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md"
ADAPTIVE_JOURNAL = ROOT / "docs" / "NMBOT_ADAPTIVE_SELECTOR_JOURNAL.md"
CONTEXT_WORKFLOW_JOURNAL = ROOT / "docs" / "NMBOT_CONTEXT_WORKFLOW_JOURNAL.md"
CONTEXT_WORKFLOW_ROADMAP = ROOT / "docs" / "NMBOT_CONTEXT_WORKFLOW_PRODUCTION_ROADMAP.md"
MULTI_PROJECT_PLAN = ROOT / "docs" / "MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_unified_protocol_exists_and_is_authoritative() -> None:
    assert UNIFIED.exists()
    text = UNIFIED.read_text(encoding="utf-8")
    flat = " ".join(text.split())

    assert "Status: authoritative local agent/context protocol" in text
    assert "initial independent benchmark is complete" in text
    assert "local NMBot machine-gate pilot is implemented" in flat
    assert "single source of truth" in text
    assert "changes no notebooks, runtime, prompts, registries" in text
    assert "revised gate passes a fresh holdout" in flat


def test_legacy_protocol_docs_are_short_compatibility_pointers() -> None:
    for path in (
        "docs/NOTEBOOKLM_PROJECT_ISOLATION_PLAN.md",
        "docs/BOUNDED_RETRIEVAL_PROTOCOL.md",
    ):
        text = _read(path)
        assert "Status: compatibility pointer" in text
        assert "docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md" in text
        assert len(text.splitlines()) <= 25
        assert "intentionally short" in text


def test_unified_protocol_contains_required_contract_markers() -> None:
    text = UNIFIED.read_text(encoding="utf-8")
    required = [
        "Goal and Actual / Contract / Desired",
        "Canonical ownership proposal",
        "fail-closed identity",
        "Explicit one-hop dependency cards",
        "STOP-2 bounded retrieval envelope",
        "starting hypotheses that require a benchmark",
        "Stage 0 retrieval contract",
        "Route selection table",
        "Expansion gate, stop reasons and drift guard",
        "bounded-retrieval.v1",
        "Documentation taxonomy and metadata",
        "Diátaxis",
        "Search and retrieval best practices",
        "Migration, safety, rollback and governance",
        "Acceptance-test matrix",
        "Hypothesis and benchmark contract for next phase",
        "Initial hypothesis result",
        "Local machine-gate pilot",
        "explicit-target STOP-2",
        "What developers get now",
        "Natural-language target selection remains supervised",
        "GitHub Code Search syntax",
        "Sourcegraph query syntax",
        "Anthropic Contextual Retrieval",
        "Azure AI Search hybrid search",
    ]
    flat_text = " ".join(text.split())
    for marker in required:
        assert marker in flat_text

    for owner in ("NMBot", "Qapairs daemon", "Shared cc engine", "MPN", "CC2", "n8n audit"):
        assert owner in text
    for limit in ("max 5", "max 2", "80 lines / 8000 characters", "definition + one consumer + one test"):
        assert limit in text


def test_initial_benchmark_result_records_split_verdict_and_artifacts() -> None:
    text = UNIFIED.read_text(encoding="utf-8")
    required = [
        "resource-control hypothesis is supported",
        "fresh local synthetic\n30-case set",
        "total characters -66.61%",
        "lines\n-65.99%",
        "sources -58.21%",
        "positive case hit unchanged at 57.89%",
        "Recall@2\nimproved 34.38% -> 46.88%",
        "harmful early stop 0%",
        "cross-project leakage 0",
        "handoffs 7/7",
        "revise before implementation",
        "Exact route correctness was 36.67%",
        "accepted `stop_reason` correctness was 0/30",
        "one false positive-case abstention",
        "not predeclared pass gates",
        "`pass` only under the narrow predeclared criteria",
        "Do not tune on this observed set",
        "canonical route\nand stop-reason enums",
        "fresh holdout",
        "not production or\ngeneralization evidence",
        "does not authorize NotebookLM migration",
        "/tmp/opencode/project_context_stop2_20260725/report_v2.md",
        "/tmp/opencode/project_context_stop2_20260725/score_v2.json",
        "first invalid run is preserved as audit evidence",
        "v2 fixed generic source dedupe before scoring",
    ]
    for marker in required:
        assert marker in text


def test_local_gate_status_and_canonical_enums_are_documented() -> None:
    text = UNIFIED.read_text(encoding="utf-8")
    flat = " ".join(text.split())
    for marker in (
        "scripts/nmbot_context_gate.py",
        "python3 scripts/nmbot.py context-gate",
        "bounded-retrieval.v1",
        "fresh independent holdout",
        "candidate-only",
        "stage | ast | current_source | docs",
        "approved_one_hop_dependency | bounded_fallback",
        "--target-kind",
        "not a natural-language classifier",
        "strict executor holdout is complete",
        "context_budget_reached",
    ):
        assert marker in flat


def test_passive_project_memory_registry_boundary_is_documented() -> None:
    unified = UNIFIED.read_text(encoding="utf-8")
    architecture = _read("docs/CURRENT_ARCHITECTURE.md")
    agents = _read("AGENTS.md")
    plan = MULTI_PROJECT_PLAN.read_text(encoding="utf-8")
    flat = " ".join((unified + "\n" + architecture + "\n" + agents + "\n" + plan).split())

    for marker in (
        "config/project_memory_registry.json",
        "scripts/project_memory_registry.py",
        "python3 scripts/nmbot.py memory-registry --project-id nmbot --json",
        "project_registry_resolution.v1",
        "project_unknown",
        "project_not_routable_pending_owner_confirmation",
        "Qapairs",
        "TBD",
    ):
        assert marker in flat
    for boundary in ("reads no source bodies", "calls no NotebookLM/MemPalace/network/runtime/gate", "performs no writes"):
        assert boundary in flat


def test_passive_project_memory_outcome_core_is_documented_without_behavior() -> None:
    unified = UNIFIED.read_text(encoding="utf-8")
    architecture = _read("docs/CURRENT_ARCHITECTURE.md")
    agents = _read("AGENTS.md")
    plan = MULTI_PROJECT_PLAN.read_text(encoding="utf-8")
    flat = " ".join((unified + "\n" + architecture + "\n" + agents + "\n" + plan).split())

    for marker in (
        "config/project_memory_policy_bundles.json",
        "config/project_memory_diagnosis_taxonomy.json",
        "data/project_memory_outcomes.jsonl",
        "scripts/project_memory_outcomes.py",
        "python3 scripts/nmbot.py memory-outcomes --validate --json",
        "privacy_safe_outcome.v1",
        "safe_case_features.v1",
        "d1_d6_taxonomy.v1",
        "hints_disabled_by_policy",
        "no real sample records",
        "no adaptive behavior",
    ):
        assert marker in flat
    assert "No behavior hints, adaptive behavior or Phase 5 work is enabled" in flat


def test_multi_project_passive_core_and_blockers_are_documented_truthfully() -> None:
    plan = MULTI_PROJECT_PLAN.read_text(encoding="utf-8")
    unified = UNIFIED.read_text(encoding="utf-8")
    current = _read("docs/CURRENT_ARCHITECTURE.md")
    combined = " ".join((plan + "\n" + unified + "\n" + current).split())
    lower = combined.lower()

    for marker in (
        "Current execution status and blockers",
        "Phase 0/1 have mechanical validation only",
        "project_memory_registry.json` resolves routable `nmbot`",
        "pilot-ready `qapairs`, `cc2` and `mpn`",
        "NMBot owner and rollback-owner fields remain the literal value `TBD`",
        "outcome store `data/project_memory_outcomes.jsonl` is empty",
        "`--hints` returns `hints_disabled_by_policy`",
        "Phase 5 has a safely executed bounded selected-set trial outcome",
        "Global Phase 5 exit is still blocked",
        "Phase 6 MemPalace recovery and local health verification passed",
        "Phase 7 NMBot adapter is dry-run only",
        "no actual fresh shadow tasks or real shadow outcomes",
        "three eligible heterogeneous local projects",
        "`qapairs`, `cc2` and `mpn` are pilot-ready for local developer navigation/context only",
        "n8n audit, opencode and novostroy candidates are ownership-map proposals",
        "without a confirmed local owner source, docs scope and check chain",
        "Phase 9-13 remain blocked",
        "Do not claim this project plan complete",
    ):
        assert marker in combined

    for phase in range(9, 14):
        assert f"Phase {phase}" in plan
        phase_start = plan.index(f"### Phase {phase}")
        next_start = plan.find("\n### Phase", phase_start + 1)
        section = plan[phase_start:] if next_start == -1 else plan[phase_start:next_start]
        assert "Status: blocked, not implemented" in section or "Status: blocked" in section

    phase8_start = plan.index("### Phase 8")
    phase8_next = plan.find("\n### Phase", phase8_start + 1)
    phase8 = plan[phase8_start:] if phase8_next == -1 else plan[phase8_start:phase8_next]
    assert "Status: partial local pilot" in phase8

    assert "temporary user delegation" in lower
    assert "does not assign a permanent operational owner" in lower
    assert "operational phase exits are not claimed" in lower
    assert "project plan is not complete" in lower or "project plan complete" in lower


def test_current_intent_registry_is_exact_local_pilot_set() -> None:
    registry = json.loads(_read("config/nmbot_context_gate_intents.json"))
    manifest = json.loads(_read("config/nmbot_retrieval_sources.json"))
    active_paths = {item["path"] for item in manifest["sources"] if item["status"] == "active"}
    cards = registry["cards"]

    assert registry["schema"] == "nmbot.context_gate_intents.v1"
    assert len(cards) == 10
    assert {frozenset(card) for card in cards} == {frozenset({"id", "evidence_type", "match_all", "resolver_query", "purpose", "owner_path"})}
    assert len({card["id"] for card in cards}) == len(cards)
    assert {card["id"] for card in cards} == {
        "current.jivo-session-key",
        "docs.first-shortlist-ux",
        "docs.jivo-waiting-status",
        "docs.rollback-selector-boundary",
        "docs.runtime-version-ownership",
        "docs.stop2-envelope",
        "stage.jivo-api-bot-message",
        "stage.jivo-bridge-event",
        "stage.response-formatter-payload",
        "stage.response-writer-request",
    }
    assert all(card["owner_path"] in active_paths for card in cards)
    assert all(card["owner_path"] in active_paths for card in cards if card["evidence_type"] == "docs")
    assert all(isinstance(term, str) and term.strip() for card in cards for term in card["match_all"])


def test_adaptive_selector_hypotheses_doc_is_documentation_only() -> None:
    assert ADAPTIVE.exists()
    text = ADAPTIVE.read_text(encoding="utf-8")
    flat = " ".join(text.split()).lower()

    for marker in (
        "authoritative documentation-only checklist",
        "This file enables no behavior",
        "changes no runtime, gate, selector, model",
        "MemoHarness §2 and Appendix A",
        "results do not transfer",
        "Strict gate route and stop enums",
        "Only a separate scorer may change any hypothesis status from `[ ]` to `[x]`",
        "Rollout decision matrix",
    ):
        assert marker in text
    assert "stop-2 budgets: max two selected sources, 80 lines and 8000 characters" in flat
    assert "19/22 target recall and 16/22 full gate paths" in flat

    forbidden_claims = (
        "enabled in runtime",
        "enabled by default",
        "production behavior is changed",
        "provider is changed",
        "selector is active",
        "auto-pilot",
        "autopilot",
    )
    assert not any(claim in flat for claim in forbidden_claims)


def test_adaptive_selector_h1_h4_have_close_gates() -> None:
    text = ADAPTIVE.read_text(encoding="utf-8")
    for hypothesis in ("H1", "H2", "H3", "H4"):
        start = text.index(f"### {hypothesis}")
        next_start = text.find("\n### H", start + 1)
        section = text[start:] if next_start == -1 else text[start:next_start]
        if hypothesis == "H1":
            assert (
                "- Status: [ ] pending." in section
                or "- Status: [x] failed on clean H1-v3;" in section
            )
        elif hypothesis == "H2":
            assert "- Status: [x] passed on H1-v3 structural evidence only" in section
        elif hypothesis == "H3":
            assert "- Status: [x] NOT_EVALUABLE" in section
            assert "H3-v1 lacked multiple candidates" in section
            assert "H3-v4\nselector view leaked labels (`labels_not_read=false`)" in section
        else:
            assert "- Status: [x] blocked" in section
            assert "Approved patterns: none" in text
        assert "- Baseline:" in section
        assert "- Variant:" in section
        assert "- Metrics:" in section
        assert "- Hard pass:" in section
        assert "- Hard fail:" in section

    assert ">=3 independent" in text
    assert "separate fresh holdout" in text
    assert "success plus failure/anti-example" in text
    assert "technical isolation boundary" in text
    assert "non-blind\ndeterministic rules test" in text


def test_adaptive_selector_dual_memory_journal_is_documentation_only() -> None:
    assert ADAPTIVE_JOURNAL.exists()
    journal = ADAPTIVE_JOURNAL.read_text(encoding="utf-8")
    hypotheses = ADAPTIVE.read_text(encoding="utf-8")
    flat = " ".join(journal.split())

    for marker in (
        "Status: authoritative persistent documentation-only journal",
        "Layer A — Evidence journal",
        "Layer B — General pattern ledger",
        "H1-v2 — INVALID",
        "label digests were absent",
        "search actor saw holdout cards",
        "never reuse this run's candidate",
        "H1-v3 — VALID FAIL",
        "adaptive target recall baseline 18/22",
        "adaptive target recall 19/22",
        "full strict hits 6/22",
        "false\n  target rate lower than baseline",
        "H2 on H1-v3 — VALID STRUCTURAL PASS",
        "one compact pattern",
        "enables no adaptive selector behavior",
        "H3-v1..v4 — NOT_EVALUABLE",
        "H3-v1 lacked multiple candidates",
        "H3-v4 selector view leaked labels (`labels_not_read=false`)",
        "No score/quality claim",
        "H4 — BLOCKED",
        "no H3-approved patterns exist, and H1 failed",
        "Approved patterns: none.",
        "No selector, gate, prompt, runtime component",
        "consume candidate or rejected patterns",
        "technical isolation boundary",
        "non-blind\ndeterministic rules test",
        "Raw case records live only in `/tmp/opencode` experiment roots",
        "never stores queries, labels, bodies, secrets",
    ):
        assert marker in journal

    assert "docs/NMBOT_ADAPTIVE_SELECTOR_JOURNAL.md" in hypotheses
    assert "append-only evidence summaries and the separately approved general pattern ledger" in " ".join(hypotheses.split())
    assert "No STOP-2 budget regression" in flat
    assert "at least three independent supporting cases" in flat
    assert "separate fresh holdout" in flat
    assert "separate scorer approval specifically for H4" in flat
    assert "No score/quality claim may be made" in journal


def test_context_workflow_journal_is_authoritative_aggregate_only() -> None:
    assert CONTEXT_WORKFLOW_JOURNAL.exists()
    journal = CONTEXT_WORKFLOW_JOURNAL.read_text(encoding="utf-8")
    protocol = UNIFIED.read_text(encoding="utf-8")
    adaptive_journal = ADAPTIVE_JOURNAL.read_text(encoding="utf-8")
    flat = " ".join(journal.split())

    for marker in (
        "Status: authoritative cumulative results journal",
        "aggregate experiment outcomes",
        "artifact roots, decisions and exclusions only",
        "Decision table",
        "Ready to use for local developer work",
        "Supervised only",
        "Candidate-only",
        "Forbidden for behavior",
        "26/26 valid explicit targets passed",
        "2/2 invalid targets failed closed",
        "four long source spans stopped honestly",
        "19/22 candidate targets were found",
        "16/22 full gate paths were correct",
        "source-card blind v3 baseline/card routes both had H@1/H@3 0.300",
        "Contextual FTS gained one R@8 path but worsened top-rank/MRR",
        "exact source symbols are resolved as source spans",
        "focused AST test evidence is accepted",
        "wide path IDs are denied as evidence",
        "H1-v3 valid fail",
        "H2 valid structural pass only",
        "H3 not evaluable",
        "H4 blocked",
        "Approved general patterns: none",
        "Exact diagnostic owner navigation — READY LOCAL",
        "unknown_complex:<value>",
        "Corrections append a new dated correction entry",
        "Raw records stay in temporary roots",
    ):
        assert marker in journal

    for marker in (
        "9/9 proven diagnostic owners selected exactly",
        "21/21 ambiguous or verified-no-failure cases abstained",
        "false selections and false abstentions 0",
        "false completions 0",
    ):
        assert marker in flat

    for owner_link in (
        "docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md",
        "docs/NMBOT_RETRIEVAL.md",
        "docs/NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md",
        "docs/NMBOT_ADAPTIVE_SELECTOR_JOURNAL.md",
    ):
        assert owner_link in journal

    assert "queries, labels, source\nbodies" in journal
    assert "raw records stay in temporary roots" in flat.lower()
    assert "docs/NMBOT_CONTEXT_WORKFLOW_JOURNAL.md" in protocol
    assert "docs/NMBOT_CONTEXT_WORKFLOW_JOURNAL.md" in adaptive_journal


def test_context_workflow_production_roadmap_is_local_only_checklist() -> None:
    assert CONTEXT_WORKFLOW_ROADMAP.exists()
    roadmap = CONTEXT_WORKFLOW_ROADMAP.read_text(encoding="utf-8")
    protocol = UNIFIED.read_text(encoding="utf-8")
    journal = CONTEXT_WORKFLOW_JOURNAL.read_text(encoding="utf-8")
    flat = " ".join(roadmap.split())

    for marker in (
        "authoritative checklist for productionizing the local developer context workflow only",
        "not an NMBot client-facing runtime feature",
        "not production VPS behavior",
        "not permission for autonomous code edits",
        "26/26` valid explicit targets passed",
        "2/2` invalid targets failed closed",
        "19/22` candidate targets were found",
        "16/22` full gate paths were correct",
        "target recall improved `18/22 -> 19/22`",
        "full strict hits stayed `6/22`",
        "P0 — Freeze contracts and instrumentation",
        "P1 — Shadow rollout on fresh real developer tasks",
        "at least `30` fresh real developer tasks",
        "Do not invoke the gate automatically",
        "P2 — Warn mode with confirmation",
        "Selector may propose exactly one target with confidence and a short reason",
        "P3 — Enforce local workflow only after thresholds pass",
        "P4 — Optional adaptive learning after sandbox and H-gates",
        "Target recall: at least `95%`",
        "Full target-to-gate correctness: at least `90%`",
        "False completion: exactly `0`",
        "Invalid/fail-close behavior: `100%`",
        "Budget/privacy compliance: `100%`",
        "Confidence bands: calibrated on fresh tasks",
        "at least three independent supporting cases",
        "at least one relevant anti-example",
        "a fresh holdout separate from the support cases",
        "approval by a scorer separate from the selector/pattern author",
        "Only the approved ledger may influence selector behavior",
        "Failed, invalid, candidate and rejected outcomes remain useful diagnostics",
        "task_fingerprint",
        "candidate_ids",
        "selected_target",
        "confirmed_or_corrected_target",
        "gate_result",
        "lines_loaded",
        "chars_loaded",
        "latency_ms",
        "verifier_result",
        "Rollback must be one flag or command path back to the current manual",
        "Shadow, warn and enforce phases stop on the first false completion",
        "The official MemoHarness preprint",
        "hypothesis inspiration only",
    ):
        assert marker in flat

    forbidden = (
        "query text",
        "task body",
        "source bodies",
        "raw logs",
        "payloads",
        "user text",
        "secrets",
    )
    assert "must not include query\ntext" in roadmap
    for marker in forbidden:
        assert marker in flat

    assert "docs/NMBOT_CONTEXT_WORKFLOW_PRODUCTION_ROADMAP.md" in protocol
    assert "docs/NMBOT_CONTEXT_WORKFLOW_PRODUCTION_ROADMAP.md" in journal


def test_entrypoints_route_to_unified_protocol_without_contract_duplication() -> None:
    agents = _read("AGENTS.md")
    current = _read("docs/CURRENT_ARCHITECTURE.md")

    assert "Project context retrieval, NotebookLM isolation, STOP-2 route contract | `docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md`" in agents
    assert "Multi-project memory/context integration plan | `docs/MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md`" in agents
    assert "docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md" in current
    assert "docs/MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md" in current
    assert "it does not duplicate it" in current
    assert "docs/NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md" in UNIFIED.read_text(encoding="utf-8")
    assert "docs/NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md" in _read("docs/NMBOT_RETRIEVAL.md")


def test_multi_project_memory_harness_plan_is_planning_only_and_complete() -> None:
    assert MULTI_PROJECT_PLAN.exists()
    plan = MULTI_PROJECT_PLAN.read_text(encoding="utf-8")
    protocol = UNIFIED.read_text(encoding="utf-8")
    roadmap = CONTEXT_WORKFLOW_ROADMAP.read_text(encoding="utf-8")
    agents = _read("AGENTS.md")
    current = _read("docs/CURRENT_ARCHITECTURE.md")
    flat = " ".join(plan.split())
    lower = flat.lower()

    for marker in (
        "planning-only implementation roadmap",
        "local developer workflow",
        "changes no client runtime",
        "does not include adaptive behavior",
        "Goal, Actual / Contract / Desired",
        "Target architecture and tool roles",
        "Universal core, adapters and shared dependencies",
        "Project docs / registries: source of truth",
        "Durable typed outcome store: Layer A",
        "NotebookLM: project history and summaries only",
        "MemPalace: agent diary and meta-memory only after repair",
        "`compress`: current conversation context only",
        "`memory_search`: recovery hint only",
        "`navigate`, FTS and strict gate: evidence execution",
        "NMBot NotebookLM sources are currently empty",
        "MemPalace was repaired from healthy SQLite after an HNSW index failure",
        "`/tmp` is volatile",
        "project_registry.v1",
        "policy_bundle.v1",
        "policy_version",
        "policy_delta",
        "safe_case_features.v1",
        "d1_d6_taxonomy.v1",
        "privacy_safe_outcome.v1",
        "bank_snapshot_id",
        "dependency_card.v1",
        "approved_pattern_record.v1",
        "Forbidden storage fields",
        "raw query",
        "raw source code",
        "raw log",
        "secret",
        "Do not start a phase until the previous phase exit gate",
        "Phase 0 — Freeze scope, owners and rollback",
        "Phase 13 — Operations, governance, retention and rollback",
        "Testing matrix",
        "Correctness and fail-close beat token/context cost",
        "pilot thresholds, not universal truths",
        "target recall >=95%",
        "full target-to-gate >=90%",
        "false completion 0",
        "invalid/fail-close 100%",
        "budget/privacy 100%",
        "passive foundation -> shadow -> warn -> enforce",
        "Rollback returns to project docs, navigate, confirmed target and strict gate",
        "Definition of Done",
        "Master checklist by phase",
        "Risks and non-goals",
        "MemoHarness mapping",
        "automatic D1-D6 edits",
        "semantic similarity retrieval as selector",
        "paper constants",
        "cache cost assumptions",
        "full trajectories and raw traces",
        "https://arxiv.org/html/2607.14159v1",
        "sections 2.2-2.6",
        "Limitations",
    ):
        assert marker in flat

    for phase in range(14):
        assert f"Phase {phase}" in plan
    for section_marker in (
        "Purpose:",
        "Dependencies:",
        "Todos:",
        "- [ ]",
        "Deliverables:",
        "Expected result:",
        "Verification:",
        "Stop conditions:",
        "Exit gate:",
    ):
        assert plan.count(section_marker) >= 14

    assert "H1 as failed" in plan
    assert "H3 as not evaluable" in plan
    assert "H4 as blocked" in plan
    assert "adaptive remains off" in lower
    assert "Qapairs, CC2 and MPN" in plan

    assert "docs/MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md" in protocol
    assert "docs/MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md" in roadmap
    assert "Multi-project memory/context integration plan | `docs/MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md`" in agents
    assert "docs/MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md" in current


def test_root_planning_files_stay_compact_and_preserve_pending_statuses() -> None:
    for path in ("task_plan.md", "findings.md", "progress.md"):
        text = _read(path)
        flat = " ".join(text.split())
        assert "docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md" in text
        assert "implemented locally" in text
        assert "hypothesis tested" in text or "strict explicit-target executor" in text
        assert "NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md" in text
        assert "H1" in text and ("pending" in flat or "[x] failed" in flat or "VALID FAIL" in flat)
        assert "H3" in text and ("NOT_EVALUABLE" in text or "not_evaluable" in flat)
        assert "Approved" in text and "none" in flat
        assert "awaiting user confirmation" in text
        assert "/tmp/opencode/project_context_stop2_20260725/" in text
        assert "revise before implementation" in flat
        assert "Qapairs" in text
        assert len(text.splitlines()) <= 120
