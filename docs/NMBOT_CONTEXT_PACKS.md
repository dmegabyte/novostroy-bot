# NMBOT context packs — local navigation manifest

Purpose: this file is a local-only navigation manifest for recurring nmbot development scenarios. It helps quickly print the source context that must be read before work, especially prompt work.

Boundary: this manifest does **not** replace reading the listed source files, does **not** prove production/Jivo behavior, does **not** run commands, does **not** deploy/restart anything, and does **not** call a model, provider, VPS, API, or external system.

Routine navigation should start from current local entrypoints (`python3 scripts/nmbot.py context ...` and `python3 scripts/nmbot.py explain ...`) instead of archive/history files; legacy prompt files are labelled as legacy where they appear.

Brief mode: `python3 scripts/nmbot.py context --pack <pack-id> --brief --human`
prints an explicit initial source limit of 2, read-first targets as `path — anchor`,
one primary local check, boundaries and the expansion rule. Start by opening only
those anchored sections. Expand beyond that only when the current source, check
output or explicit task evidence links to the next doc/file. In other words: the
current source, check output or explicit task evidence must point to the next file.

Bounded materializer: `python3 scripts/nmbot.py context --pack <pack-id> --brief --materialize --max-lines 80 --max-chars 8000` reads only the initial read-first anchors and emits local excerpts under hard total budgets. Defaults are 80 total excerpt lines and 8000 total excerpt characters; accepted maximums are 200 lines and 20000 characters. It does not read bulk docs/files lists and does not execute checks.

The visible tree below is the human source of truth. The fenced JSON block at the end is parsed by `scripts/nmbot_context_pack.py` and must stay synchronized with this tree.

## Tree

- `legacy`
  - `telegram` — explicit opt-in historical Telegram/V1 rollback/debug context.
    - Read first: `docs/legacy/TELEGRAM_LEGACY.md` — `# Telegram legacy contour — historical only`; `scripts/chat_tester_bot.py` — `class OvermindClient`.
    - Required docs: `docs/legacy/TELEGRAM_LEGACY.md`, `docs/NMBOT_RUNTIME_VERSIONS.md`.
    - Prompts/source files: `scripts/chat_tester_bot.py`, `prompts/chat_v1.txt`, `prompts/text_style_v1.txt`, `prompts/search_v1.txt`.
    - Targeted local checks: `python3 scripts/nmbot_check.py docs`.
    - Boundary: explicit legacy rollback/debug context only; not a current Jivo release gate and not default prompt/navigation context.
- `prompt`
  - `base` — main prompt contract and shared UX baseline.
    - Read first: `docs/CURRENT_ARCHITECTURE.md` — `# NMBot current architecture — compact entry`; `docs/IDEAL_IRINA_UX.md` — `# Ideal Irina UX — главный UX-контракт nmbot`.
    - Required docs: `docs/CURRENT_ARCHITECTURE.md`, `docs/IDEAL_IRINA_UX.md`, `docs/PROMPT_ARCHITECTURE.md`, `docs/BOT_ARCHITECTURE.md`.
    - Prompts/source files: `prompts/v2_response_writer.txt`, `prompts/v2_response_formatter.txt`, `prompts/v2_search_mcp.txt`.
    - Targeted local checks: `python3 scripts/nmbot_check.py docs`, `python3 scripts/nmbot_prompt_static_check.py`.
    - Boundary: local prompt/docs evidence only; if answer behavior changes, production remains unproved until separately authorized Jivo evidence.
  - `family` — family scenario overlay.
    - Read first: `docs/IDEAL_IRINA_UX.md` — `# Ideal Irina UX — главный UX-контракт nmbot`; `prompts/scenarios/family_v1.txt` — `Ты пишешь только сценарий family.`.
    - Required docs: `docs/IDEAL_IRINA_UX.md`, `docs/PROMPT_ARCHITECTURE.md`.
    - Prompts/source files: `prompts/scenarios/family_v1.txt`.
     - Targeted local checks: `python3 scripts/nmbot_check.py docs`, `python3 -m pytest tests/test_search_profiles.py`.
    - Boundary: local scenario evidence only; no production/Jivo proof.
  - `rental` — rental scenario overlay and answer-quality guardrails.
    - Read first: `docs/IDEAL_IRINA_UX.md` — `# Ideal Irina UX — главный UX-контракт nmbot`; `prompts/scenarios/rental_v1.txt` — `Ты пишешь только сценарий rental.`.
    - Required docs: `docs/IDEAL_IRINA_UX.md`, `docs/PROMPT_ARCHITECTURE.md`, `docs/NMBOT_V2_ANSWER_QUALITY_GATE.md`, `docs/SCENARIO_FIELD_MECHANICS_MAP.md`.
    - Prompts/source files: `prompts/scenarios/rental_v1.txt`.
     - Targeted local checks: `python3 scripts/nmbot_check.py docs`, `python3 -m pytest tests/test_h043_stateful_rental_and_enrichment.py`, `python3 -m pytest tests/test_nmbot_v2_scenario_field_mechanics.py`.
    - Boundary: local rental prompt/field-mechanics evidence only; no Jivo smoke, no model call, no production proof.
  - `investment` — investment scenario overlay.
    - Read first: `docs/IDEAL_IRINA_UX.md` — `# Ideal Irina UX — главный UX-контракт nmbot`; `prompts/scenarios/investment_v1.txt` — `Ты пишешь только сценарий investment.`.
    - Required docs: `docs/IDEAL_IRINA_UX.md`, `docs/PROMPT_ARCHITECTURE.md`.
    - Prompts/source files: `prompts/scenarios/investment_v1.txt`.
     - Targeted local checks: `python3 scripts/nmbot_check.py docs`, `python3 -m pytest tests/test_search_profiles.py`.
    - Boundary: local scenario evidence only; no production/Jivo proof.
  - `search` — search prompt and MCP fact contract.
    - Read first: `docs/SCENARIO_MCP_CONTRACT.md` — `# Scenario → MCP card → Answer contract`; `prompts/v2_search_mcp.txt` — `SEARCH_CONTRACT_ENVELOPE={`.
    - Required docs: `docs/IDEAL_IRINA_UX.md`, `docs/PROMPT_ARCHITECTURE.md`, `docs/SCENARIO_MCP_CONTRACT.md`, `docs/MCP_APARTMENT_REQUEST_RULES.md`, `docs/NOVOSTROYM_MCP_SCHEMA.md`.
    - Prompts/source files: `prompts/search_v1.txt`, `prompts/scenarios/search_v1.txt`, `prompts/v2_search_mcp.txt`.
    - Targeted local checks: `python3 scripts/nmbot_check.py docs`, `python3 scripts/nmbot_check.py v2`.
    - Boundary: local search-contract evidence only; does not call MCP/model/provider and does not prove live Jivo.
- `runtime`
  - `v2` — V2 local runtime contract and active response route map.
    - Read first: `docs/NMBOT_RUNTIME_REGISTRY.md` — `# NMBOT runtime registry — source map and dated evidence`; `scripts/nmbot_response_path.py` — `def resolve_response_path(`.
    - Required docs: `docs/NMBOT_RUNTIME_REGISTRY.md`, `docs/NMBOT_RUNTIME_VERSIONS.md`, `docs/NMBOT_EXTERNAL_CONTRACTS.md`, `docs/NMBOT_RUNBOOK.md`.
    - Prompts/source files: `scripts/nmbot_runtime_adapter.py`, `nmbot_v2/response_composer.py`, `scripts/nmbot_response_path.py`, `prompts/v2_response_writer.txt`, `prompts/v2_response_formatter.txt`, `prompts/v2_search_mcp.txt`.
     - Targeted local checks: `python3 scripts/nmbot_check.py v2`.
     - Boundary: local V2 contract/runtime evidence only; legacy `prompts/v2_response_composer.txt` is not the active V2 response-route prompt here; this pack must not change selector/config and does not prove production.
  - `fallback` — model, retry, active writer/formatter and fallback ownership.
    - Read first: `docs/CURRENT_ARCHITECTURE.md` — `# NMBot current architecture — compact entry`; `docs/RESPONSE_MODEL_EVAL.md` — `# Response model eval — сравнение моделей ответчика`.
    - Required docs: `docs/CURRENT_ARCHITECTURE.md`, `docs/BOT_ARCHITECTURE.md`, `docs/RESPONSE_MODEL_EVAL.md`, `docs/EXPERIMENTS.md`, `docs/NMBOT_RUNBOOK.md`.
    - Prompts/source files: `scripts/nmbot_runtime_adapter.py`, `nmbot_v2/response_composer.py`, `scripts/nmbot_response_path.py`, `prompts/v2_response_writer.txt`, `prompts/v2_response_formatter.txt`.
    - Targeted local checks: `python3 scripts/nmbot_check.py runtime`, `python3 scripts/nmbot_check.py v2`.
    - Boundary: first prove payload stage and Actual/Contract/Desired; legacy `prompts/v2_response_composer.txt` is legacy/alternative context, not active-route proof; no model/provider call, eval, selector/config change or production proof.
- `diagnostics`
  - `trace` — Jivo trace, terminal delivery and first-failure triage.
    - Read first: `docs/JIVO_DIAGNOSTICS.md` — `# Jivo/nmbot диагностика`; `config/nmbot_stage_map.json` — `"schema": "nmbot.stage_map.v1"`.
    - Required docs: `docs/JIVO_DIAGNOSTICS.md`, `docs/NMBOT_RUNBOOK.md`, `docs/NMBOT_EXTERNAL_CONTRACTS.md`, `docs/NMBOT_OPERATIONS_MAP.md`.
    - Prompts/source files: `scripts/nmbot_diag.sh`, `scripts/nmbot_jivo_trace_analyze.py`, `scripts/nmbot_jivo_dialogue_diagnose.py`, `scripts/dialogue_journal.py`, `scripts/nmbot_response_path.py`, `config/nmbot_stage_map.json`.
    - Targeted local checks: `python3 scripts/nmbot_check.py docs`, `python3 scripts/nmbot_check.py contracts`, `python3 scripts/nmbot.py explain --path-id jivo.v2.turn.v1 --json`.
    - Boundary: pack is local navigation only; VPS diagnosis is a separate explicit read-only action and old logs do not prove current production state.
- `jivo`
  - `release` — release-prep context for Jivo boundary review.
    - Read first: `docs/NMBOT_RUNBOOK.md` — `# NMBOT runbook — first command routes`; `scripts/nmbot_release_preflight.py` — `SCHEMA_VERSION = "nmbot.release_preflight.v1"`.
    - Required docs: `docs/NMBOT_RUNBOOK.md`, `docs/NMBOT_OPERATIONS_MAP.md`, `docs/IDEAL_IRINA_UX.md`, `docs/NMBOT_EXTERNAL_CONTRACTS.md`.
    - Prompts/source files: `scripts/nmbot_release_preflight.py`, `scripts/nmbot_jivo_trace_analyze.py`, `scripts/nmbot_jivo_dialogue_diagnose.py`.
    - Targeted local checks: `python3 scripts/nmbot_release_preflight.py`, `python3 scripts/nmbot_check.py contracts`.
    - Boundary: local preflight can only say incomplete/needs evidence. VPS/direct-API/Jivo smoke evidence is separate and must be explicitly authorized; never claim green without Jivo smoke.

## Parsed JSON manifest

This block duplicates the tree mapping for the local `context` command. It contains only pack id, title, read-first paths plus verifiable anchors, docs/files, checks, and boundaries; no secrets or runtime values.

<!-- NMBOT_CONTEXT_PACKS_JSON_START -->
```json
{
  "schema": "nmbot.context_pack.v1",
  "packs": [
    {
      "id": "legacy/telegram",
      "title": "Telegram/V1 legacy rollback and debug context",
      "read_first": ["docs/legacy/TELEGRAM_LEGACY.md", "scripts/chat_tester_bot.py"],
      "read_first_anchors": [{"path": "docs/legacy/TELEGRAM_LEGACY.md", "anchor": "# Telegram legacy contour — historical only"}, {"path": "scripts/chat_tester_bot.py", "anchor": "class OvermindClient"}],
      "docs": ["docs/legacy/TELEGRAM_LEGACY.md", "docs/NMBOT_RUNTIME_VERSIONS.md"],
      "files": ["scripts/chat_tester_bot.py", "prompts/chat_v1.txt", "prompts/text_style_v1.txt", "prompts/search_v1.txt"],
      "checks": ["python3 scripts/nmbot_check.py docs"],
      "boundaries": ["Explicit legacy rollback/debug context only", "Not a current Jivo release gate", "Not default prompt/navigation context", "Does not prove production or Jivo behavior"]
    },
    {
      "id": "prompt/base",
      "title": "Prompt base contract",
      "read_first": ["docs/CURRENT_ARCHITECTURE.md", "docs/IDEAL_IRINA_UX.md"],
      "read_first_anchors": [{"path": "docs/CURRENT_ARCHITECTURE.md", "anchor": "# NMBot current architecture — compact entry"}, {"path": "docs/IDEAL_IRINA_UX.md", "anchor": "# Ideal Irina UX — главный UX-контракт nmbot"}],
      "docs": ["docs/CURRENT_ARCHITECTURE.md", "docs/IDEAL_IRINA_UX.md", "docs/PROMPT_ARCHITECTURE.md", "docs/BOT_ARCHITECTURE.md"],
      "files": ["prompts/v2_response_writer.txt", "prompts/v2_response_formatter.txt", "prompts/v2_search_mcp.txt"],
      "checks": ["python3 scripts/nmbot_check.py docs", "python3 scripts/nmbot_prompt_static_check.py"],
      "boundaries": ["Local prompt/docs evidence only", "Does not prove production or Jivo behavior", "If answer behavior changes, separately authorized Jivo evidence is still required"]
    },
    {
      "id": "prompt/family",
      "title": "Family scenario prompt context",
      "read_first": ["docs/IDEAL_IRINA_UX.md", "prompts/scenarios/family_v1.txt"],
      "read_first_anchors": [{"path": "docs/IDEAL_IRINA_UX.md", "anchor": "# Ideal Irina UX — главный UX-контракт nmbot"}, {"path": "prompts/scenarios/family_v1.txt", "anchor": "Ты пишешь только сценарий family."}],
      "docs": ["docs/IDEAL_IRINA_UX.md", "docs/PROMPT_ARCHITECTURE.md"],
      "files": ["prompts/scenarios/family_v1.txt"],
       "checks": ["python3 scripts/nmbot_check.py docs", "python3 -m pytest tests/test_search_profiles.py"],
      "boundaries": ["Local scenario evidence only", "Does not prove production or Jivo behavior"]
    },
    {
      "id": "prompt/rental",
      "title": "Rental scenario prompt context",
      "read_first": ["docs/IDEAL_IRINA_UX.md", "prompts/scenarios/rental_v1.txt"],
      "read_first_anchors": [{"path": "docs/IDEAL_IRINA_UX.md", "anchor": "# Ideal Irina UX — главный UX-контракт nmbot"}, {"path": "prompts/scenarios/rental_v1.txt", "anchor": "Ты пишешь только сценарий rental."}],
      "docs": ["docs/IDEAL_IRINA_UX.md", "docs/PROMPT_ARCHITECTURE.md", "docs/NMBOT_V2_ANSWER_QUALITY_GATE.md", "docs/SCENARIO_FIELD_MECHANICS_MAP.md"],
      "files": ["prompts/scenarios/rental_v1.txt"],
       "checks": ["python3 scripts/nmbot_check.py docs", "python3 -m pytest tests/test_h043_stateful_rental_and_enrichment.py", "python3 -m pytest tests/test_nmbot_v2_scenario_field_mechanics.py"],
      "boundaries": ["Local rental prompt/field-mechanics evidence only", "No Jivo smoke, model call, or production proof"]
    },
    {
      "id": "prompt/investment",
      "title": "Investment scenario prompt context",
      "read_first": ["docs/IDEAL_IRINA_UX.md", "prompts/scenarios/investment_v1.txt"],
      "read_first_anchors": [{"path": "docs/IDEAL_IRINA_UX.md", "anchor": "# Ideal Irina UX — главный UX-контракт nmbot"}, {"path": "prompts/scenarios/investment_v1.txt", "anchor": "Ты пишешь только сценарий investment."}],
      "docs": ["docs/IDEAL_IRINA_UX.md", "docs/PROMPT_ARCHITECTURE.md"],
      "files": ["prompts/scenarios/investment_v1.txt"],
       "checks": ["python3 scripts/nmbot_check.py docs", "python3 -m pytest tests/test_search_profiles.py"],
      "boundaries": ["Local scenario evidence only", "Does not prove production or Jivo behavior"]
    },
    {
      "id": "prompt/search",
      "title": "Search prompt and MCP fact contract",
      "read_first": ["docs/SCENARIO_MCP_CONTRACT.md", "prompts/v2_search_mcp.txt"],
      "read_first_anchors": [{"path": "docs/SCENARIO_MCP_CONTRACT.md", "anchor": "# Scenario → MCP card → Answer contract"}, {"path": "prompts/v2_search_mcp.txt", "anchor": "SEARCH_CONTRACT_ENVELOPE={"}],
      "docs": ["docs/IDEAL_IRINA_UX.md", "docs/PROMPT_ARCHITECTURE.md", "docs/SCENARIO_MCP_CONTRACT.md", "docs/MCP_APARTMENT_REQUEST_RULES.md", "docs/NOVOSTROYM_MCP_SCHEMA.md"],
      "files": ["prompts/search_v1.txt", "prompts/scenarios/search_v1.txt", "prompts/v2_search_mcp.txt"],
      "checks": ["python3 scripts/nmbot_check.py docs", "python3 scripts/nmbot_check.py v2"],
      "boundaries": ["Local search-contract evidence only", "Does not call MCP/model/provider", "Does not prove live Jivo"]
    },
    {
      "id": "runtime/v2",
      "title": "V2 runtime contract context",
      "read_first": ["docs/NMBOT_RUNTIME_REGISTRY.md", "scripts/nmbot_response_path.py"],
      "read_first_anchors": [{"path": "docs/NMBOT_RUNTIME_REGISTRY.md", "anchor": "# NMBOT runtime registry — source map and dated evidence"}, {"path": "scripts/nmbot_response_path.py", "anchor": "def resolve_response_path("}],
      "docs": ["docs/NMBOT_RUNTIME_REGISTRY.md", "docs/NMBOT_RUNTIME_VERSIONS.md", "docs/NMBOT_EXTERNAL_CONTRACTS.md", "docs/NMBOT_RUNBOOK.md"],
      "files": ["scripts/nmbot_runtime_adapter.py", "nmbot_v2/response_composer.py", "scripts/nmbot_response_path.py", "prompts/v2_response_writer.txt", "prompts/v2_response_formatter.txt", "prompts/v2_search_mcp.txt"],
      "checks": ["python3 scripts/nmbot_check.py v2"],
      "boundaries": ["Local V2 contract/runtime evidence only", "Legacy prompts/v2_response_composer.txt is not the active V2 response-route prompt in this pack", "Must not change selector or production config", "Does not prove production"]
    },
    {
      "id": "runtime/fallback",
      "title": "Model and fallback ownership context",
      "read_first": ["docs/CURRENT_ARCHITECTURE.md", "docs/RESPONSE_MODEL_EVAL.md"],
      "read_first_anchors": [{"path": "docs/CURRENT_ARCHITECTURE.md", "anchor": "# NMBot current architecture — compact entry"}, {"path": "docs/RESPONSE_MODEL_EVAL.md", "anchor": "# Response model eval — сравнение моделей ответчика"}],
      "docs": ["docs/CURRENT_ARCHITECTURE.md", "docs/BOT_ARCHITECTURE.md", "docs/RESPONSE_MODEL_EVAL.md", "docs/EXPERIMENTS.md", "docs/NMBOT_RUNBOOK.md"],
      "files": ["scripts/nmbot_runtime_adapter.py", "nmbot_v2/response_composer.py", "scripts/nmbot_response_path.py", "prompts/v2_response_writer.txt", "prompts/v2_response_formatter.txt"],
      "checks": ["python3 scripts/nmbot_check.py runtime", "python3 scripts/nmbot_check.py v2"],
      "boundaries": ["Prove payload stage and Actual Contract Desired before behavior changes", "Legacy prompts/v2_response_composer.txt is legacy alternative context, not live mode proof", "No model or provider call and no eval", "Must not change selector or production config", "Does not prove production"]
    },
    {
      "id": "diagnostics/trace",
      "title": "Jivo trace and first-failure diagnostics context",
      "read_first": ["docs/JIVO_DIAGNOSTICS.md", "config/nmbot_stage_map.json"],
      "read_first_anchors": [{"path": "docs/JIVO_DIAGNOSTICS.md", "anchor": "# Jivo/nmbot диагностика"}, {"path": "config/nmbot_stage_map.json", "anchor": "\"schema\": \"nmbot.stage_map.v1\""}],
      "docs": ["docs/JIVO_DIAGNOSTICS.md", "docs/NMBOT_RUNBOOK.md", "docs/NMBOT_EXTERNAL_CONTRACTS.md", "docs/NMBOT_OPERATIONS_MAP.md"],
      "files": ["scripts/nmbot_diag.sh", "scripts/nmbot_jivo_trace_analyze.py", "scripts/nmbot_jivo_dialogue_diagnose.py", "scripts/dialogue_journal.py", "scripts/nmbot_response_path.py", "config/nmbot_stage_map.json"],
      "checks": ["python3 scripts/nmbot_check.py docs", "python3 scripts/nmbot_check.py contracts", "python3 scripts/nmbot.py explain --path-id jivo.v2.turn.v1 --json"],
      "boundaries": ["Local navigation only", "VPS diagnostics require a separate explicit read-only action", "Old logs do not prove current production state", "Does not deploy restart or prove production"]
    },
    {
      "id": "jivo/release",
      "title": "Jivo release-prep context",
      "read_first": ["docs/NMBOT_RUNBOOK.md", "scripts/nmbot_release_preflight.py"],
      "read_first_anchors": [{"path": "docs/NMBOT_RUNBOOK.md", "anchor": "# NMBOT runbook — first command routes"}, {"path": "scripts/nmbot_release_preflight.py", "anchor": "SCHEMA_VERSION = \"nmbot.release_preflight.v1\""}],
      "docs": ["docs/NMBOT_RUNBOOK.md", "docs/NMBOT_OPERATIONS_MAP.md", "docs/IDEAL_IRINA_UX.md", "docs/NMBOT_EXTERNAL_CONTRACTS.md"],
      "files": ["scripts/nmbot_release_preflight.py", "scripts/nmbot_jivo_trace_analyze.py", "scripts/nmbot_jivo_dialogue_diagnose.py"],
      "checks": ["python3 scripts/nmbot_release_preflight.py", "python3 scripts/nmbot_check.py contracts"],
      "boundaries": ["Local preflight can only say incomplete/needs evidence", "VPS/direct-API/Jivo smoke evidence is separate and must be explicitly authorized", "Never claim green without Jivo smoke"]
    }
  ]
}
```
<!-- NMBOT_CONTEXT_PACKS_JSON_END -->

Source anchors: `docs/CURRENT_ARCHITECTURE.md`; `docs/PROMPT_ARCHITECTURE.md:7-12,84-117`; `docs/IDEAL_IRINA_UX.md:9-23`; `docs/BOT_ARCHITECTURE.md:290-307`; `docs/NMBOT_RUNBOOK.md:21-54,66-78`.
