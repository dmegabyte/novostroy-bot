# NMBot context/navigation workflow journal

Status: authoritative cumulative results journal for all NMBot local
context/navigation work. This file records aggregate experiment outcomes,
artifact roots, decisions and exclusions only. It changes no runtime, gate,
selector, prompt, model, provider, registry, config, network, VPS, deploy,
production state or eval by itself.

Owner links:

- `docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md` owns the project context retrieval,
  NotebookLM isolation, STOP-2 route, strict gate and privacy protocol.
- `docs/NMBOT_RETRIEVAL.md` owns local deterministic navigate, FTS cards,
  source-card pilot boundaries and candidate-only retrieval behavior.
- `docs/NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md` owns the adaptive selector
  hypothesis checklist.
- `docs/NMBOT_ADAPTIVE_SELECTOR_JOURNAL.md` owns adaptive selector evidence and
  the approved-pattern ledger.
- `docs/NMBOT_CONTEXT_WORKFLOW_PRODUCTION_ROADMAP.md` owns the productionization
  checklist for the local developer context workflow only.

Raw records stay in temporary artifact roots. Do not copy queries, labels, source
bodies, raw card text, raw logs, user payloads or secrets into this journal.

## Decision table

| Area | Decision | Evidence basis | Exclusions |
|---|---|---|---|
| Deterministic navigate plus strict explicit-target gate | Ready to use for local developer work when the session names an exact stage, diagnostic code, symbol or owner docs anchor | Strict holdouts below passed; gate remains local and bounded | Not runtime, production proof, autonomous target choice or permission to infer unread long spans |
| Free Russian target selection | Supervised only | Candidate target recall and full gate path metrics below are useful but incomplete | Not autopilot, not a Russian-language classifier, not deployable routing |
| FTS cards | Candidate-only | FTS helps find small candidate sets before grep/read | Cards are never evidence and never replace selected owner source/test reading |
| Source-card pilot | Candidate-only, supervised | Static source-card and contextual experiments did not prove quality gain | No ranking/index/runtime/prompt/provider/config change justified |
| Strict gate audit fixes | Ready as local gate enforcement for exact targets | Exact source symbols and focused AST test evidence are accepted; wide path IDs are denied | Broad path IDs are navigation-only and must not be treated as evidence |
| Adaptive selector / experience bank | Forbidden for behavior | H1 failed, H2 is structural only, H3 is not evaluable, H4 is blocked | No selector, gate, prompt, runtime, model or provider may consume candidate/rejected patterns |
| Passive shadow telemetry v2 | Mechanics ready for local evidence records only | Schema/validator/summary tests pass for aggregate-only P0/P1 journal fields | Not an independent rollout, not selector activation and not behavior hints |

## Cumulative entries

### 2026-07-25 — Deterministic navigate / strict explicit-target gate — READY LOCAL

- Scope: explicit stage, Python symbol and owner-scoped docs-anchor navigation
  followed by the strict STOP-2 context gate.
- Artifact root: `/tmp/opencode/nmbot_context_gate_strict_holdout_20260725/`.
- Integrity: local documentation/tooling evidence only; aggregate route, privacy,
  budget and fail-closed checks were recorded without copying raw case bodies.
- Metrics: 26/26 valid explicit targets passed; 2/2 invalid targets failed closed;
  four long source spans stopped honestly at the 80-line envelope instead of
  claiming false completion.
- Verdict: ready for local developer use when the current session has selected an
  exact target.
- Next action: treat `context_budget_reached` as a continuation decision and read
  no additional span unless a new explicit contract permits it.
- Exclusions: not production proof, not runtime enforcement, not autonomous target
  selection, and not permission to infer unread lines.

### 2026-07-25 — Free Russian target selection — SUPERVISED ONLY

- Scope: ambiguous/free Russian wording used to choose candidate strict targets
  before the gate.
- Artifact roots: current navigation/gate experiment roots under `/tmp/opencode`,
  including the strict holdout and adaptive selector roots referenced from the
  owner docs.
- Integrity: aggregate metrics only; raw wordings, labels and source bodies are
  intentionally excluded.
- Metrics: 19/22 candidate targets were found; 16/22 full gate paths were correct.
- Verdict: supervised only. The numbers are useful for triage but do not solve
  natural-language routing.
- Next action: the current session must choose one exact target or abstain before
  the strict gate reads evidence.
- Exclusions: no autopilot, no runtime selector, no classifier claim and no broad
  file/context expansion from these metrics.

### 2026-07-24..2026-07-25 — FTS cards and source-card/contextual variants — NO QUALITY GAIN PROVEN

- Scope: local SQLite FTS candidate cards, opt-in source cards and contextual
  source-card/header experiments.
- Artifact roots: `/tmp/opencode/nmbot_source_cards_blind_20260724/`,
  `/tmp/opencode/nmbot_fts_contextual_independent_20260724/` and the local
  retrieval benchmark roots referenced from `docs/NMBOT_RETRIEVAL.md`.
- Integrity: independent scoring summaries and aggregate counts only; no raw
  cards, queries, labels or source bodies are copied here.
- Metrics: source-card blind v3 baseline/card routes both had H@1/H@3 0.300 and
  MRR 0.325. Contextual FTS gained one R@8 path but worsened top-rank/MRR:
  baseline H@1/H@3/R@8 76.923/92.308/78.205%, MRR 83.333%; contextual
  69.231/92.308/82.051%, MRR 78.205%.
- Verdict: no demonstrated quality gain. FTS cards remain candidate-only; source
  cards remain opt-in navigation hints.
- Next action: use cards to narrow before grep/read, then read selected owner
  evidence; do not use source cards to skip evidence.
- Exclusions: no ranking/index change, no runtime/prompt/provider/config change,
  no production claim and no evidence claim from cards alone.

### 2026-07-25 — Strict gate audit fixes — READY LOCAL FOR EXACT TARGETS

- Scope: gate and deterministic navigation audit after earlier route/stop issues.
- Artifact root: `/tmp/opencode/nmbot_context_gate_strict_holdout_20260725/`.
- Integrity: focused local test evidence and aggregate audit outcome only.
- Evidence: exact source symbols are resolved as source spans; focused AST test evidence is accepted
  for symbol work; wide path IDs are denied as evidence and
  must be resolved to exact stage targets first.
- Verdict: strict gate behavior is acceptable for exact local targets.
- Next action: keep wide path IDs as navigation-only IDs and require explicit
  stage/symbol/docs targets at the gate boundary.
- Exclusions: no free-form source expansion, no broad path ID evidence, no
  runtime/config/gate behavior change from this journal entry.

### 2026-07-25 — Adaptive selector H1/H2/H3/H4 — FORBIDDEN FOR BEHAVIOR

- Scope: adaptive selector / experience-bank hypotheses only.
- Artifact roots: `/tmp/opencode/nmbot_adaptive_selector_h1_v2_20260725/`,
  `/tmp/opencode/nmbot_adaptive_selector_h1_v3_20260725/`,
  `/tmp/opencode/nmbot_adaptive_selector_h3_20260725/`,
  `/tmp/opencode/nmbot_adaptive_selector_h3_v2_20260725/`,
  `/tmp/opencode/nmbot_adaptive_selector_h3_v3_20260725/` and
  `/tmp/opencode/nmbot_adaptive_selector_h3_v4_20260725/`.
- Integrity: see `docs/NMBOT_ADAPTIVE_SELECTOR_JOURNAL.md` for append-only
  adaptive evidence summaries.
- Results: H1-v2 invalid; H1-v3 valid fail because target recall improved
  18/22->19/22 but full strict hits stayed 6/22; H2 valid structural pass only;
  H3 not evaluable; H4 blocked.
- Verdict: forbidden for behavior. Approved general patterns: none.
- Next action: only resume with a technical isolation boundary or explicit
  non-blind deterministic re-scope, as recorded in the adaptive journal.
- Exclusions: no selector, gate, prompt, runtime component, model policy or
  provider policy may consume candidate or rejected adaptive patterns.

### 2026-07-26 — Passive shadow telemetry v2 — MECHANICS READY, P1 ZERO TASKS

- Scope: local append-only telemetry schema and CLI summary for future P0/P1
  evidence records.
- Integrity: aggregate diagnostics only; no raw question, query, path, source body,
  code, log, payload, transcript, label or secret is stored.
- Diagnostic A/B, not independent: B1 exact diagnostic improved 4%→40%, recall
  28%→68%, false 76%→48%, max candidates 8. B2 rejected exact stayed 8%, recall
  28%, false 76%.
- Verdict: mechanics ready for passive shadow records; P1 still has zero fresh real
  developer tasks.
- Exclusions: not selector activation, not adaptive behavior, not behavior hints,
  not runtime/gate/prompt/model/provider/network/deploy proof.

### 2026-07-26 — Passive journal hardening — STORE STILL EMPTY

- Scope: production-quality local validation mechanics for the passive outcome
  journal only.
- Integrity: semantic shadow invariants and exclusive locked append were added to
  prevent invalid verifier/result combinations and duplicate races without storing
  raw task, source, log, payload, label or secret content.
- Verdict: validator/append mechanics are stricter; this is not a phase exit and
  does not collect fresh P1 tasks.
- Exclusions: no selector, hints, runtime, gate, prompt, model, provider, network,
  deploy or production behavior is enabled. The permanent store remains empty.

### 2026-07-26 — Exact diagnostic owner navigation — READY LOCAL

- Scope: map an exact failed-check or error code to the narrow active Python
  function that emits, declares or references it, then read that target through
  the strict STOP-2 gate.
- Artifact root: `/tmp/opencode/nmbot_p1_diagnostic_owner_v4_20260726/`.
- Integrity: the v2 acceptance locked candidates, selections and gate outputs
  before a separate scorer opened labels. Raw case bodies, labels and source
  excerpts remain outside this journal.
- Metrics: 9/9 proven diagnostic owners selected exactly; 21/21 ambiguous or
  verified-no-failure cases abstained; false selections and false abstentions 0;
  four full gate reads plus five honest `context_budget_reached` partial reads;
  budget/privacy 100%; false completions 0.
- Fixes proven: canonical current owner is `scripts/nmbot_test_agent.py`; producer
  occurrences rank before declarations/references; a dynamic code such as
  `unknown_complex:<value>` resolves to its exact `unknown_complex` detector;
  clipped target spans cannot report `definition_of_done`.
- Verdict: ready for local developer diagnosis of exact codes. It proves the
  detector/check owner, not the runtime stage that caused a bad outcome.
- Exclusions: no adaptive-memory activation, no automatic natural-language root
  cause selection, no client runtime, no VPS/deploy and no production claim. The
  permanent passive outcome store remains empty.

## Append-only write rules

1. Every completed context/navigation experiment gets a new dated journal entry
   with integrity status, aggregate metrics, verdict and next action.
2. Corrections append a new dated correction entry. Do not overwrite or silently
   reinterpret older verdicts.
3. Store only aggregate metrics, artifact roots, decisions and exclusions here.
4. Raw records stay in temporary roots such as `/tmp/opencode/...`; do not copy
   queries, labels, source bodies, raw cards, secrets, logs or payloads into docs.
5. A journal entry is documentation evidence only. It never changes runtime,
   config, gate behavior, prompts, providers, models, deployments or production.
