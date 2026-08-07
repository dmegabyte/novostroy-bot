# NMBot documentation registry

Purpose: this is the primary human registry for `docs/`. Choose one task route,
then open the smallest owner document you need. Labels mean: `current` is active,
`reference` is a contract/map, `advanced` is optional specialist navigation,
`planning` is not implemented truth, and `historical` is dated evidence or legacy.

Canonical owners: runtime versions and selector ownership live in
`NMBOT_RUNTIME_VERSIONS.md` + `NMBOT_RUNTIME_REGISTRY.md`; MCP/schema contract
lives in `NOVOSTROYM_MCP_SCHEMA.md` plus the MCP request/contract docs;
operations live in `NMBOT_RUNBOOK.md` and `JIVO_DIAGNOSTICS.md`; experiments and
model/prompt evidence live in `EXPERIMENTS.md` and `RESPONSE_MODEL_EVAL.md`;
reusable prevention lessons live in `NMBOT_ENGINEERING_LESSONS.md`. Root files
may link or redirect here, but should not become duplicate owner docs.

## 1. Start and understand

- `current` [`CURRENT_ARCHITECTURE.md`](CURRENT_ARCHITECTURE.md) — compact current system map and safe local workflow.
- `current` [`NMBOT_OPERATIONS_MAP.md`](NMBOT_OPERATIONS_MAP.md) — owners, contour boundaries and stop/go lifecycle map.
- `current` [`NMBOT_RUNTIME_VERSIONS.md`](NMBOT_RUNTIME_VERSIONS.md) — V0/V1/V2/V3/V4 separation passport.
- `reference` [`NMBOT_VERSION_BOUNDARY_MAP.md`](NMBOT_VERSION_BOUNDARY_MAP.md) — short ownership and proof boundary map between versions.
- `current` [`NMBOT_RUNTIME_REGISTRY.md`](NMBOT_RUNTIME_REGISTRY.md) — runtime selector and version ownership registry.
- `current` [`DOCUMENTATION_GATE.md`](DOCUMENTATION_GATE.md) — local docs update queue/gate boundaries.
- `current` [`DOCUMENTATION_V1_TZ.md`](DOCUMENTATION_V1_TZ.md) — implemented V1 Lite documentation registry/check contract.
- `current` [`NMBOT_V1_TZ.md`](NMBOT_V1_TZ.md) — implementation contract for the independent Bot V1 runtime; Stages A-C are implemented and reviewed locally, Stage D has not started.
- `reference` [`BOT_ARCHITECTURE.md`](BOT_ARCHITECTURE.md) — full stage, memory, fallback and deploy architecture when compact maps are not enough.
- `reference` [`IDEAL_IRINA_UX.md`](IDEAL_IRINA_UX.md) — UX north star for answer, prompt, routing, MCP/search parsing and operator flow.
- `reference` [`PRODUCT_TZ.md`](PRODUCT_TZ.md) — product context/TZ reference.
- `reference` [`BEST_PRACTICES.md`](BEST_PRACTICES.md) — project best-practice notes.
- `reference` [`NMBOT_ENGINEERING_LESSONS.md`](NMBOT_ENGINEERING_LESSONS.md) — verified bot-development failures and reusable prevention checklists.
- `reference` [`CODEX.md`](CODEX.md) — project codex/reference notes.

## 2. Build and verify

- `reference` [`NMBOT_EXTERNAL_CONTRACTS.md`](NMBOT_EXTERNAL_CONTRACTS.md) — external callback/Jivo contracts.
- `reference` [`MCP_APARTMENT_REQUEST_RULES_LLM.md`](MCP_APARTMENT_REQUEST_RULES_LLM.md) — LLM quick start for MCP apartment requests.
- `reference` [`MCP_APARTMENT_REQUEST_RULES.md`](MCP_APARTMENT_REQUEST_RULES.md) — full MCP apartment request contract.
- `reference` [`NOVOSTROYM_MCP_SCHEMA.md`](NOVOSTROYM_MCP_SCHEMA.md) — **канонический справочник базы `novostroym`: таблицы, связи, статусы, SQL-шаблоны и operational notes**.
- `reference` [`MCP_APARTMENT_CONTRACT_GOLDENS.md`](MCP_APARTMENT_CONTRACT_GOLDENS.md) — golden MCP request/validator examples.
- `reference` [`SCENARIO_MCP_CONTRACT.md`](SCENARIO_MCP_CONTRACT.md) — scenario → MCP card → answer contract.
- `reference` [`LLM_SCENARIO_EVAL_RUBRIC.md`](LLM_SCENARIO_EVAL_RUBRIC.md) — scenario evaluation rubric and request/response separation.
- `reference` [`PROMPT_ARCHITECTURE.md`](PROMPT_ARCHITECTURE.md) — prompt architecture.
- `reference` [`NMBOT_PROMPT_PROVENANCE.md`](NMBOT_PROMPT_PROVENANCE.md) — prompt provenance.
- `reference` [`CARD_PRESENTATION_RULE.md`](CARD_PRESENTATION_RULE.md) — card presentation rule.
- `reference` [`IRINA_FIRST_REPLY_GUIDE.md`](IRINA_FIRST_REPLY_GUIDE.md) — Irina first-reply behavior guide.
- `reference` [`IRINA_DIALOGUE_MAP_V1.md`](IRINA_DIALOGUE_MAP_V1.md) — Irina dialogue map.
- `reference` [`SCENARIO_FIELD_MECHANICS_MAP.md`](SCENARIO_FIELD_MECHANICS_MAP.md) — scenario field mechanics map.
- `reference` [`NMBOT_SELECTED_ZHK_LOT_FUNNEL.md`](NMBOT_SELECTED_ZHK_LOT_FUNNEL.md) — selected ЖК / lot funnel notes.
- `reference` [`NMBOT_V2_SCENARIO_RECIPES.md`](NMBOT_V2_SCENARIO_RECIPES.md) — V2 scenario recipes.
- `reference` [`NMBOT_V2_MCP_PROMPT_BUILD_RULES.md`](NMBOT_V2_MCP_PROMPT_BUILD_RULES.md) — V2 MCP prompt build rules.
- `reference` [`NMBOT_V2_ANSWER_QUALITY_GATE.md`](NMBOT_V2_ANSWER_QUALITY_GATE.md) — V2 answer quality gate notes.
- `advanced` [`PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md`](PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md) — project context retrieval, NotebookLM isolation and STOP-2 contract.
- `advanced` [`NMBOT_CONTEXT_PACKS.md`](NMBOT_CONTEXT_PACKS.md) — local read-first context packs by task.
- `advanced` [`NMBOT_RETRIEVAL.md`](NMBOT_RETRIEVAL.md) — bounded local FTS retrieval/navigation owner doc.
- `advanced` [`BOUNDED_RETRIEVAL_PROTOCOL.md`](BOUNDED_RETRIEVAL_PROTOCOL.md) — STOP-2 bounded retrieval protocol.
- `advanced` [`NMBOT_DEVELOPER_BASELINE.md`](NMBOT_DEVELOPER_BASELINE.md) — local developer baseline and check timing.
- `advanced` [`MARSHRUT_K_DOKAZATELSTVU.md`](MARSHRUT_K_DOKAZATELSTVU.md) — route-to-evidence methodology.

## 3. Operate and release

- `current` [`NMBOT_RUNBOOK.md`](NMBOT_RUNBOOK.md) — first operational commands, local gates, deploy/rollback boundaries.
- `current` **Broad inventory gate** — быстрый маршрут управления фильтром ЖК без подтверждённого продаваемого лота: `scripts/nmbot_inventory_gate.py`; подробности и команды — в [NMBOT_RUNBOOK.md](NMBOT_RUNBOOK.md#broad-inventory-gate).
- `current` **TEST feature flags** — безопасное переключение трёх TEST-флагов без ручного редактирования `.env`: `scripts/nmbot_test_feature_flags.py`; ключи и ограничения — в [NMBOT_RUNBOOK.md](NMBOT_RUNBOOK.md#test-feature-flags).
- `current` [`JIVO_DIAGNOSTICS.md`](JIVO_DIAGNOSTICS.md) — Jivo/API trace, terminal delivery and diagnostics.
- `current` [`NMBOT_RELEASE_IDENTITY.md`](NMBOT_RELEASE_IDENTITY.md) — release identity and source attribution.
- `reference` [`NMBOT_ATOMIC_RELEASES.md`](NMBOT_ATOMIC_RELEASES.md) — atomic release procedure reference.
- `reference` [`NMBOT_ARTIFACT_RETENTION.md`](NMBOT_ARTIFACT_RETENTION.md) — retention and archive-first policy for release/eval artifacts.
- `reference` [`IRINA_UX_RELEASE_CHECKLIST.md`](IRINA_UX_RELEASE_CHECKLIST.md) — Irina UX release checklist.
- `historical` [`CLIENT_PRODUCTION_README.md`](CLIENT_PRODUCTION_README.md) — client-production contour note; verify against current runbook/operations docs before use.

## 4. Decisions and history

- `reference` [`EXPERIMENTS.md`](EXPERIMENTS.md) — owner experiment workflow and prompt/model evidence.
- `planning` [`NMBOT_PROJECT_SIMPLIFICATION_PLAN.md`](NMBOT_PROJECT_SIMPLIFICATION_PLAN.md) — project simplification plan.
- `planning` [`MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md`](MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md) — multi-project memory/context harness plan.
- `planning` [`NMBOT_CONTEXT_WORKFLOW_PRODUCTION_ROADMAP.md`](NMBOT_CONTEXT_WORKFLOW_PRODUCTION_ROADMAP.md) — context workflow production roadmap.
- `planning` [`NMBOT_CONTEXT_WORKFLOW_JOURNAL.md`](NMBOT_CONTEXT_WORKFLOW_JOURNAL.md) — context/navigation workflow journal.
- `planning` [`NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md`](NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md) — adaptive selector hypotheses.
- `planning` [`NMBOT_ADAPTIVE_SELECTOR_JOURNAL.md`](NMBOT_ADAPTIVE_SELECTOR_JOURNAL.md) — adaptive selector journal.
- `planning` [`NOTEBOOKLM_PROJECT_ISOLATION_PLAN.md`](NOTEBOOKLM_PROJECT_ISOLATION_PLAN.md) — NotebookLM isolation plan.
- `planning` [`NOTEBOOKLM_SUMMARY_MIGRATION_WRITE_PLAN.md`](NOTEBOOKLM_SUMMARY_MIGRATION_WRITE_PLAN.md) — summary-only migration write plan.
- `planning` [`JIVO_BOT_API_INTEGRATION_PLAN.md`](JIVO_BOT_API_INTEGRATION_PLAN.md) — Jivo Bot API integration plan.
- `planning` [`JIVO_CALLBACK_SHEETS_IMPLEMENTATION_PLAN.md`](JIVO_CALLBACK_SHEETS_IMPLEMENTATION_PLAN.md) — Jivo callback → Sheets implementation plan.
- `planning` [`NMBOT_INTENT_PLAN_V3_IMPLEMENTATION_PLAN.md`](NMBOT_INTENT_PLAN_V3_IMPLEMENTATION_PLAN.md) — IntentPlan V3 simplification plan.
- `planning` [`RESPONSE_IMPROVEMENT_PLAN.md`](RESPONSE_IMPROVEMENT_PLAN.md) — response improvement plan.
- `planning` [`RESPONSE_MODEL_EVAL.md`](RESPONSE_MODEL_EVAL.md) — response model comparison notes.
- `planning` [`LLM_DECISION_ARCHITECTURE_TZ.md`](LLM_DECISION_ARCHITECTURE_TZ.md) — LLM decision architecture TZ.
- `planning` [`SCENARIO_COMMENT_ENRICHMENT_TZ.md`](SCENARIO_COMMENT_ENRICHMENT_TZ.md) — scenario comment enrichment TZ.
- `planning` [`NMBOT_RECIPE_OVERLAP.md`](NMBOT_RECIPE_OVERLAP.md) — scenario recipe overlap analysis.
- `historical` [`ARCHIVE_INDEX.md`](ARCHIVE_INDEX.md) — index and safety policy for archived historical records.
- `historical` [`legacy/TELEGRAM_LEGACY.md`](legacy/TELEGRAM_LEGACY.md) — Telegram runtime history and rollback/debug boundary.
- `historical` [`CHANGELOG.md`](CHANGELOG.md) — dated project changelog.
- `historical` [`POSTMORTEM_2026-07-22_CONDITIONAL_COMPOSER_ROLLBACK.md`](POSTMORTEM_2026-07-22_CONDITIONAL_COMPOSER_ROLLBACK.md) — dated composer rollout/rollback postmortem.
- `historical` [`NMBOT_COMMAND_MIGRATION.md`](NMBOT_COMMAND_MIGRATION.md) — dated command migration record.
- `historical` [`NMBOT_V0.md`](NMBOT_V0.md) — V0 runtime document; use current runtime docs for current contour decisions.
- `historical` [`NMBOT_V0_QUALITY_BASELINE.md`](NMBOT_V0_QUALITY_BASELINE.md) — V0 quality baseline.
- `historical` [`NMBOT_V2_QUALITY_BASELINE.md`](NMBOT_V2_QUALITY_BASELINE.md) — V2 quality baseline.
- `historical` [`NMBOT_V2_PROJECT_QUALITY_SCORECARD.md`](NMBOT_V2_PROJECT_QUALITY_SCORECARD.md) — V2 project scorecard.
- `historical` [`GOLDEN_DIALOGS.md`](GOLDEN_DIALOGS.md) — historical golden dialogs.
- `historical` [`scenario_map_1000.md`](scenario_map_1000.md) — scenario map snapshot.
- `historical` [`NMBOT_SCENARIO_MODEL_PROBE_20260721.md`](NMBOT_SCENARIO_MODEL_PROBE_20260721.md) — scenario model probe.
- `historical` [`NMBOT_CURRENT_OPTIONS_BATCH_PROBE_20260721.md`](NMBOT_CURRENT_OPTIONS_BATCH_PROBE_20260721.md) — current-options batch probe.
- `historical` [`MCP_TOPIC_COVERAGE_20260713.md`](MCP_TOPIC_COVERAGE_20260713.md) — MCP topic coverage audit.
- `historical` [`reason_layer_hypothesis_conclusions_2026-07-02.md`](reason_layer_hypothesis_conclusions_2026-07-02.md) — reason-layer hypothesis conclusions.
- `historical` [`CC2_RETRIEVAL.md`](CC2_RETRIEVAL.md), [`MPN_RETRIEVAL.md`](MPN_RETRIEVAL.md), [`QAPAIRS_RETRIEVAL.md`](QAPAIRS_RETRIEVAL.md) — local retrieval adapter records for adjacent projects.
