# NMBot current architecture — compact entry

Purpose: this is the short current entry document for developers. It is a map to
owner files and checks, not a replacement for full contracts and not proof of live
production behavior. In short: this guide is not proof of live production behavior.

## Boundaries

- Current facts come from owner docs, source files and focused local checks.
- Live production claims require fresh, explicitly authorized VPS/Jivo evidence;
  docs, local files, memory and stale logs do not prove what users see now.
- Do not use this file to change runtime, prompts, model/provider settings,
  release bundles, deploy state or customer-facing behavior.
- If a full stage, memory, fallback or deploy contract is needed, open
  `docs/BOT_ARCHITECTURE.md` after this map.

## Current Jivo contour

- Compact persistent map: `AGENTS.md`.
- Operations owner map: `docs/NMBOT_OPERATIONS_MAP.md`.
- External callback/Jivo contracts: `docs/NMBOT_EXTERNAL_CONTRACTS.md`.
- Runtime selector/version ownership: `docs/NMBOT_RUNTIME_REGISTRY.md` and
  `docs/NMBOT_RUNTIME_VERSIONS.md`.
- Short version ownership boundary map: `docs/NMBOT_VERSION_BOUNDARY_MAP.md`.
- Current client-facing contour is Jivo. Inbound is Jivo `CLIENT_MESSAGE` through
  the bridge to the private API; outbound terminal delivery is `BOT_MESSAGE`.
  `INVITE_AGENT` is only for live-operator handoff. Source owner summary lives in
  `AGENTS.md` and operations details live in `docs/NMBOT_OPERATIONS_MAP.md`.
- The approved but not implemented PROD live dialogue monitor is specified in
  `docs/NMBOT_LIVE_MONITOR_PLAN.md`. It is a separate read-only observation
  surface; it does not replace Jivo, own replies, or prove current production
  behavior.

## Safe local workflow

Use this lightweight path before opening the full architecture file:

Project context retrieval, NotebookLM isolation, cross-project dependency rules
and STOP-2 route selection are owned by
`docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md`. This file only links that contract;
it does not duplicate it.
The planning-only multi-project implementation roadmap is
`docs/MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md`; it is not runtime or
production behavior.
The passive Phase 0/1 registry foundation is local-only:
`python3 scripts/nmbot.py memory-registry --project-id nmbot --json` validates
`config/project_memory_registry.json` and returns only bounded route IDs/refs or
stable denials. It does not read source bodies, call notebooks/memory tools,
invoke gates, or change runtime.
The passive Phase 2/3/4 outcome foundation is also local-only:
`python3 scripts/nmbot.py memory-outcomes --validate --json` validates
`data/project_memory_outcomes.jsonl`; `--append --outcome <repo-json> --json`
adds only strict `privacy_safe_outcome.v1` metadata for routable NMBot; and
`--hints ... --json` always returns `hints_disabled_by_policy` with no behavior
hints. The store has no real sample records by default and is not a selector.
The multi-project roadmap currently has only mechanical Phase 0/1 validation and
dry-run/no-call/no-write Phase 5/6/7 mechanics; Phase 8-13 are blocked pending a
confirmed non-NMBot local owner/source/docs/check scope, fresh real developer
shadow tasks and a human governance owner.

1. For broad developer retrieval, run local SQLite FTS before `grep`/`read`, for
   example `python3 scripts/nmbot.py retrieve "finance disclaimer first list"
   --term missing_note --json`. It returns at most eight candidate cards of
   500–700 characters. It calls no model and is not production proof. Details:
   `docs/NMBOT_RETRIEVAL.md`.
   The local-only pilot flag `--source-cards` may attach compact navigation
   context for known pilot paths, but it does not change FTS ranking/input and is
    not evidence.
   For a known narrow target, prefer local deterministic navigation first:
   `python3 scripts/nmbot.py navigate "v2.search" --json` or
   `python3 scripts/nmbot.py navigate "resolve_response_path" --json`. It builds
   an in-memory registry from active manifest paths, stage-map refs, Python AST
    definitions and approved docs anchors, returns at most three paths/ranges and
    calls no model/network/runtime code. Fallback results are candidate-only.
    For a known failed-check or error identifier, pass that exact code to
    `navigate`; the `diagnostic` route returns the narrow detector function as a
    strict-gate-compatible symbol candidate, not an inferred root-cause stage.
    To enforce the STOP-2 envelope after selecting a target, use
     `python3 scripts/nmbot.py context-gate "ignored by strict executor" --project-id nmbot
     --evidence-type stage --target-kind stage --target v2.search
     --definition-of-done "owner source and focused test" --json`.
     The local gate permits at most two sources, 80 lines and 8000 characters and
     emits `bounded-retrieval.v1`; it is not bot-runtime or production enforcement.
     Strict mode resolves only the explicit stage/symbol/docs target. The local
     intent registry is a separate optional legacy pilot, not a natural-language
     router.
2. The current OpenCode session semantically chooses zero to four cards. Cards
   and source cards are not evidence. If none fit, stop and use current docs or
   the stage map, then targeted `grep`/`read`; never substitute a random file. If
   cards fit, `grep` only their paths and `read` only selected ranges/direct
   consumers.
3. For a known owner area, load the smallest matching context pack from
   `docs/NMBOT_CONTEXT_PACKS.md`.
      For a compact budget, use
      `python3 scripts/nmbot.py context --pack <pack-id> --brief --human`.
    Brief mode names an initial source limit of 2 and prints addressable targets as
    `path — anchor`; open those anchored sections first, not the full docs/files
    list.
    When the task needs local excerpts instead of target names, use the bounded
    materializer:
    `python3 scripts/nmbot.py context --pack <pack-id> --brief --materialize --max-lines 80 --max-chars 8000`.
    It reads only the initial read-first anchors and keeps all emitted excerpts
    under the hard total line and character budgets.
4. For response-path questions, use the stage map route in
   `config/nmbot_stage_map.json` through `scripts/nmbot_response_path.py` or the
   wrapper documented in `docs/NMBOT_RUNBOOK.md`.
5. Run only the targeted local check for the owner layer. Full command details and
   production gates stay in `docs/NMBOT_RUNBOOK.md`.
6. Stop at the first failed local check and identify the layer before broadening
     the scope.

Expansion rule: open the next doc/file only when the current source, check output
or explicit task evidence links to it. Do not broaden into full docs/files lists by
default.

## Response diagnostic route

- Stage registry: `config/nmbot_stage_map.json`.
- Resolver/source map: `scripts/nmbot_response_path.py`.
- Diagnostic docs: `docs/JIVO_DIAGNOSTICS.md`.
- Context pack: `diagnostics/trace` in `docs/NMBOT_CONTEXT_PACKS.md`.
- Full architecture fallback: `docs/BOT_ARCHITECTURE.md`.

The stage map helps open one owner/source/prompt/contract/test card instead of
loading the entire historical architecture guide.

## Documentation hierarchy

Open in this order unless the task has a narrower owner:

1. `AGENTS.md` — compact always-on safeguards and current source-of-truth
   boundaries for agents.
2. `docs/README.md` — primary human registry; choose one of its four task routes
   and follow the lifecycle labels.
3. The selected owner document from the registry or `AGENTS.md` route table — use
   that owner for commands, evidence and detailed rules.
4. `docs/BOT_ARCHITECTURE.md` — full stage/memory/fallback/deploy contract only
   when the selected owner says compact maps are not enough.

## Archive policy

- Closed/raw/historical planning records are secondary to current owner docs and
  fresh evidence.
- Old root planning logs live under
  `docs/archive/working-history/2026-07-24/` and are indexed by
  `docs/ARCHIVE_INDEX.md`.
- Keep root `task_plan.md`, `findings.md` and `progress.md` compact. Add current
  decisions there only as short indexes; cite the archive for old evidence.
- Do not delete archived history during context-reduction work. If rollback is
  needed, restore from the dated archive rather than reconstructing from memory.
