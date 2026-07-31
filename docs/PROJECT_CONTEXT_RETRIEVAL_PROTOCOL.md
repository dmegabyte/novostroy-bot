# Project context retrieval protocol

Status: authoritative local agent/context protocol for project context retrieval.
The initial independent benchmark is complete and a local NMBot machine-gate
pilot is implemented; its fresh independent holdout is still pending. This
document is the single source of truth for
NotebookLM project isolation, bounded retrieval, route selection, enforcement
traces, documentation taxonomy, migration, acceptance tests and research
evidence. It changes no notebooks, runtime, prompts, registries, providers,
deployments, production state or live behavior by itself.

Aggregate context/navigation experiment results are tracked in
`docs/NMBOT_CONTEXT_WORKFLOW_JOURNAL.md`. Adaptive selector / experience-bank
hypotheses are tracked separately in `docs/NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md`.
That checklist is documentation and experiment planning only; it does not enable
a selector or relax this protocol.

Productionizing the local developer context workflow is tracked in
`docs/NMBOT_CONTEXT_WORKFLOW_PRODUCTION_ROADMAP.md`. That roadmap is local
workflow planning only; it does not change client-facing NMBot runtime,
production VPS behavior or autonomous code editing.

Multi-project implementation sequencing is tracked in
`docs/MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md`. That plan is an
implementation roadmap for local developer workflow only; this document remains
the authoritative contract.

Boundary: the shipped gate is local developer tooling only. It does not enforce
bot runtime, call NotebookLM, inspect production, migrate notebooks or change
prompts/providers/releases. Broader rollout and notebook migration remain future
phases after the revised gate passes a fresh holdout.

Phase 0/1 passive foundation: `config/project_memory_registry.json` and
`scripts/project_memory_registry.py` now provide a fail-closed local registry
validator/resolver. The chain is `project_id -> registry validator -> canonical
notebook / local adapter eligibility -> explicit routable or denied result`.
It emits only IDs/refs (`project_registry_resolution.v1`), reads no source bodies,
calls no NotebookLM/MemPalace/network/runtime/gate, and performs no writes.

Phase 2/3/4 passive foundation: `config/project_memory_policy_bundles.json`,
`config/project_memory_diagnosis_taxonomy.json`, `data/project_memory_outcomes.jsonl`
and `scripts/project_memory_outcomes.py` provide local append-only
`privacy_safe_outcome.v1` plus `privacy_safe_shadow_outcome.v1` validation for
NMBot only. The file contains no real sample outcomes by default. `--hints`
returns `hints_disabled_by_policy` and an empty list, so no adaptive behavior,
selector behavior or runtime behavior is enabled.
Shadow records also enforce semantic invariants: selected IDs must come from the
candidate set, verifier result must match selected/corrected IDs plus outcome
result/gate status, empty candidates cannot have a selection, and appends validate
the existing JSONL plus duplicate IDs under an exclusive local file lock before a
single fsynced line is written.

Current multi-project status: Phase 0/1 have mechanical validation only; Phase
5/6/7 are dry-run/no-call/no-write only. Four local adapters now validate for
developer routing: `nmbot`, `qapairs`, `cc2` and `mpn`. Qapairs is `pilot_ready`
with canonical notebook `cc-daemons`; CC2 is `pilot_ready` with canonical
notebook `cc2`; MPN is `pilot_ready` with canonical notebook `mpn` and only a
validator-only one-hop `cc-daemons` dependency. These routes are local developer
navigation/context only; diagnostic owner selection is not runtime root-cause
proof or production proof, and it grants no NotebookLM migration/write authority
or adaptive behavior. Full Phase 8+ rollout is still blocked until fresh real
developer shadow tasks, multi-project isolation results and a human governance
owner exist. See
`docs/MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md` for the exact blocker
record. Do not claim operational phase exits or project-plan completion from the
passive mechanics.

## 1. Goal and Actual / Contract / Desired

Goal: prevent unrelated project history, broad source expansion and stale status
from entering the working context while still allowing explicit shared-contract
lookups.

Actual:

- Project notebooks already exist, including `nmbot`, `qapairs-daemon`,
  `novostroy-m`, `opencode` and `n8n_audit`.
- Shared or duplicate notebooks also exist, including `cc-daemons` /
  `cc_daemons` and `n8n-audit` / `n8n_audit`.
- Historical post-session routing sometimes wrote project facts to a broad
  shared notebook.
- Local developer navigation already has project docs, source/tests, FTS cards,
  context packs and deterministic routes, but these must remain candidate or
  evidence-specific tools rather than broad context loading.

Contract:

- Current code facts come from project docs, owner source and focused tests.
- Current production facts require fresh, explicitly authorized live evidence.
- NotebookLM stores historical facts and decisions; it is not proof of live
  production or current source behavior.
- A `project_id` is required before project search/write. If it is missing or
  ambiguous, fail closed and ask; never default to a broad notebook.
- Existing notes and legacy docs are not deleted during migration.

Desired:

- Search exactly one canonical project notebook by default.
- Use cross-project context only through an approved one-hop dependency card.
- Select a route before reading sources.
- Load the smallest owner evidence range that can answer the question.
- Emit machine-verifiable traces and explicit stop/denial reasons.

## 2. Canonical ownership proposal

This is a proposed ownership map except for rows already confirmed in the local
registry. Rows marked pending require confirmation before notebooks, routes or
writes are changed.

| Scope | Canonical owner | Status |
|---|---|---|
| NMBot / Irina bot | `nmbot` | Use as canonical for NMBot |
| Qapairs daemon / current Qapairs | `cc-daemons` | local pilot canonical route confirmed for current Qapairs owner root; legacy `qapairs-daemon` excluded |
| Shared cc engine/provider/control | `cc-daemons` | canonical only for genuinely shared engine/provider/ops |
| MPN | `mpn` | local pilot canonical route confirmed for developer routing; only validator-only one-hop `cc-daemons` dependency |
| CC2 | `cc2` | local pilot canonical route confirmed for developer routing |
| n8n audit | `n8n_audit` | canonical; dash-name variants are legacy/stub candidates |
| OpenCode / ЧАТИ config | `opencode` | canonical boundary pending against `rules-v2` |
| Novostroy CRM | `novostroy-m` | boundary pending against `novostroy-ai` |
| Novostroy AI / gateway / Overmind | `novostroy-ai` | boundary pending against NMBot/shared provider infra |
| Call-center quality audit | `call-center-audit` | specialized owner for audit evidence only |
| Knowledge MCP itself | `knowledge-mcp` | specialized owner for tool implementation/operation |

`cc-daemons` is not globally obsolete. It remains the owner for shared daemon
infrastructure, but it is not the default notebook for every project.

## 3. Project route and fail-closed identity

Every project route must be explicit:

```text
project_id: stable slug, required
canonical_notebook: exactly one notebook
shared_notebooks: allowlist, empty by default
legacy_notebooks: never searched automatically
write_policy: canonical_only
source_priority: project docs/source/tests before historical memory
```

If `project_id` cannot be resolved, stop with
`project_unknown — ask before search`. Do not broaden into `cc-daemons`, all
notebooks, all docs or source-wide grep as a substitute for ownership.

Local passive command: `python3 scripts/nmbot.py memory-registry --project-id
nmbot --json`. Qapairs, CC2 and MPN now resolve locally as `pilot_ready` routes
for developer navigation/context only. Qapairs uses canonical notebook
`cc-daemons`; CC2 uses `cc2`; MPN uses `mpn` and only the explicit validator-only
`cc-daemons` dependency card, with no transitive or whole-root indexing. Legacy
notebooks are exclusions only and are never returned as routable notebooks. If a
future row returns to pending ownership, it must fail closed with
`project_not_routable_pending_owner_confirmation` until owner, rollback owner and
route evidence are confirmed.

Local passive outcome commands: `python3 scripts/nmbot.py memory-outcomes
--validate --json`, `python3 scripts/nmbot.py memory-outcomes --append --outcome
data/example.json --json`, `python3 scripts/nmbot.py memory-outcomes --list
--project-id nmbot --json`, `python3 scripts/nmbot.py memory-outcomes --summary
--json`, and `python3 scripts/nmbot.py memory-outcomes --hints --project-id nmbot
--policy-version nmbot-passive-v1 --route docs --evidence-type docs --json`.
Appends require routable NMBot, matching policy, D1-D6 taxonomy and privacy-safe
features. Qapairs/unknown projects fail closed.

## 4. Explicit one-hop dependency cards

Foreign project context is closed by default. It opens only through a dependency
card named by the current task, source or owner contract.

```text
scope_id: shared interface identifier
owner_project: project that owns the interface
consumer_project: project asking the question
canonical_notebook: owner notebook
contract_ref: exact owner contract/card
reason: why this consumer may read it
allowed_query_types: contract/interface only
max_depth: 1
max_records: 2
```

Limits:

- default foreign project notebooks: 0;
- max approved dependency notebooks: 1;
- automatic graph depth: 1;
- max records from the dependency: 2;
- transitive traversal: disabled;
- cycles: stop with the visited-project set;
- load only the shared interface/contract card, not incidents, raw history or
  implementation details from the foreign project.

## 5. STOP-2 bounded retrieval envelope

These numeric limits are starting hypotheses that require a benchmark. They are
not universal truths and must not be tuned on observed scoring sets.

```text
active project: 1
primary route: 1
candidate cards inspected: max 5
primary sources selected: max 2
linked expansion: max 1
full files loaded by default: 0
initial excerpt budget: 80 lines / 8000 characters total
symbol task: definition + one consumer + one test
same search repeated: forbidden without new evidence
```

Cards, search scores and result summaries are not evidence. Evidence begins only
after a selected owner source range, symbol span or contract anchor is read.

## 6. Stage 0 retrieval contract

Before searching or reading, record:

```text
project_id
question
expected_evidence_type: stage | symbol | current-source | docs | history | production | ambiguous
definition_of_done
do_not_open
context_budget
```

If `project_id` or evidence type is unknown, ask one short question. Do not run
multiple routes in parallel to compensate for ambiguity.

## 7. Route selection table

Select before read. Choose exactly one primary route:

| Evidence need | Route | Current source rule |
|---|---|---|
| Known stage/path ID | stage map / stage resolver | selected stage card plus owner range |
| Known symbol | AST/symbol navigation | definition, one consumer, one test |
| Current code behavior | owner source + focused test | source/test beat docs/history |
| Product or architecture contract | project docs anchor/context pack | read named owner anchor |
| Historical decision | canonical project NotebookLM | history only, not live/current proof |
| Current production state | fresh authorized production check | docs/local files are insufficient |
| Ambiguous broad question | bounded project-local candidate search | max five candidates, then select or abstain |

Fallback is allowed only when the selected route returns no usable candidate.

Canonical machine route values are:

```text
stage | ast | current_source | docs | canonical_notebook_handoff |
fresh_authorized_production_handoff | clarify_evidence_type |
deep_audit_handoff | fail_closed_cross_project |
approved_one_hop_dependency | bounded_fallback
```

## 8. Expansion gate, stop reasons and drift guard

Open one extra source only when the current source exposes a missing dependency:

```text
missing_fact
next_source
link_evidence
expected_resolution
approved_dependency_card
remaining_budget
```

Expansion is denied when the source is merely possibly useful, not linked, from
an unapproved foreign project, repeats the same search, exceeds depth one,
exhausts budget or changes topic.

Mandatory `stop_reason` values:

- `definition_of_done`
- `two_primary_sources_agree`
- `owner_contract_and_test`
- `no_candidate_answers`
- `expansion_exhausted`
- `context_budget_reached`
- `source_conflict_requires_decision`
- `topic_changed_follow_up`
- `deep_audit_required`

Topic drift guard: record the new topic as `follow_up`, finish or stop the
current task, and start a new retrieval contract only after confirmation.

Deep-audit exception: audits, inventories and migrations may use a larger named
scope and artifact, but only as an explicit separate mode with a timebox, source
budget, no production changes and a stop after the first contract/tool failure.

## 9. Machine enforcement trace and denial reasons

Tools should emit `bounded-retrieval.v1` traces with counts and stable IDs only:

```json
{
  "schema": "bounded-retrieval.v1",
  "project_id": "nmbot",
  "route": "stage",
  "candidate_ids": ["stage:v2.search"],
  "selected_source_ids": ["docs:NMBOT_RETRIEVAL#stage"],
  "candidate_count": 1,
  "selected_source_count": 1,
  "expansion_hops": 0,
  "cross_project_notebooks": 0,
  "lines_loaded": 54,
  "characters_loaded": 4200,
  "stop_reason": "definition_of_done"
}
```

The trace must not include document bodies, secrets, raw logs or user payloads.
`stop_reason` is mandatory.

Denial reasons:

- `project_unknown — ask before search`
- `route_ambiguous — choose evidence type`
- `source_not_linked — expansion denied`
- `cross_project_dependency_not_allowed`
- `depth_limit_reached`
- `context_budget_reached`
- `topic_changed — create follow-up`
- `repeat_search_forbidden_without_new_evidence`

## 10. Documentation taxonomy and metadata

Use Diátaxis categories:

- tutorial: learning path;
- how-to: task procedure;
- reference: concise contract/API/source-of-truth facts;
- explanation: rationale and background.

Agent-first docs should prefer concise Reference/How-to pages with stable
anchors, clear owner boundaries and machine-checkable metadata:

```text
project_id
doc_type
owner
status
source_of_truth
applies_to
entry_points
tests
last_verified
supersedes
```

Drift checks should verify metadata presence, linked target existence, superseded
doc pointers, status freshness, route discoverability and compact root planning
files. Comments belong only on non-obvious invariants, not as duplicated prose
contracts. Shared tool interface cards should name owner, consumers, allowed
queries, inputs/outputs, failure modes, tests and rollback owner.

## 11. Search and retrieval best practices

- Prefer scoped project/path/language/symbol/content routes.
- Use cards/results only for selection; read the selected owner range for
  evidence.
- Do not assume contextual enrichment, hybrid search or reranking improves this
  corpus without an own-corpus benchmark.
- Do not expand source cards, bibliographies or comments into mass file reads.
- No full files by default; use anchored ranges and exact symbol spans.
- A stale live-status note must hand off to authorized live checks rather than
  answer as current fact.

## 12. Migration, safety, rollback and governance

Phases:

1. Inventory notebooks and freeze proposed ownership.
2. Stop new contamination by requiring `project_id` and canonical writes.
3. Summarize mixed history; do not bulk-copy raw notes.
4. Freeze legacy notebooks from automatic routing.
5. Run isolation, dependency and context-budget acceptance tests.
6. Govern with periodic inventory and owner metadata checks.

Safety:

- no deletion in the initial migration;
- no secrets, raw logs or full transcripts in migration artifacts;
- no stale live status treated as current production proof;
- ambiguous notes stay unresolved;
- rollback is routing-only: restore the previous registry while original notebooks
  remain intact.

## 13. Acceptance-test matrix

The protocol is ready only when tests cover:

| Case | Expected result |
|---|---|
| Project isolation positive | owned result appears or honest abstention |
| Project isolation negative | no foreign top-five result |
| Current code handoff | source/test required, NotebookLM not proof |
| Stale production handoff | fresh authorized live check required |
| Dependency depth | one approved dependency, depth one, max two records |
| Symbol exact line | definition plus one consumer plus one test at most |
| Context budget | max five candidates, two primary sources, 80 lines / 8000 chars initial |
| Topic drift | creates follow-up instead of expanding current task |
| Repeat search | blocked without new evidence |
| Deep audit | separate explicit audit contract and artifact |

## 14. Hypothesis and benchmark contract for next phase

Scoring is a separate independent phase. Compare baseline existing navigation
against the STOP-2-gated workflow on fresh independent cases.

Benchmark rules:

- at least 24 fresh cases;
- separate query and label files;
- lock outputs before scoring;
- run a first-case smoke and stop on first failure;
- no tuning on observed sets;
- record metrics file with recall@K, exact symbol/line, cross-project leakage,
  false positive/abstention, files/lines/chars loaded, latency and harmful early
  stop;
- vendor research findings are hypotheses until project corpus results prove
  them.

## 15. Initial hypothesis result

The resource-control hypothesis is supported on the fresh local synthetic
30-case set under the predeclared criteria: total characters -66.61%, lines
-65.99%, sources -58.21%; positive case hit unchanged at 57.89%; Recall@2
improved 34.38% -> 46.88%; harmful early stop 0%; cross-project leakage 0;
fresh production, canonical history and approved dependency handoffs 7/7; budget
and depth gates passed.

The complete machine-enforcement protocol is **revise before implementation**.
Exact route correctness was 36.67%, accepted `stop_reason` correctness was 0/30,
there was one false positive-case abstention, and absolute docs/ambiguous
retrieval remained weak. These were not predeclared pass gates, so the original
scorer says `pass` only under the narrow predeclared criteria.

Do not tune on this observed set. The next gate revision needs canonical route
and stop-reason enums, then a fresh holdout. This benchmark is not production or
generalization evidence and does not authorize NotebookLM migration, runtime
enforcement, prompt/config changes, release or deployment.

Artifacts: `/tmp/opencode/project_context_stop2_20260725/report_v2.md` and
`/tmp/opencode/project_context_stop2_20260725/score_v2.json`, with supporting
locked queries, labels, outputs and generation notes in the same artifact root.
The first invalid run is preserved as audit evidence because of duplicate source
paths; v2 fixed generic source dedupe before scoring.

## 16. Local machine-gate pilot

The local implementation is `scripts/nmbot_context_gate.py`, exposed without
wrapper-side argument rewriting as `python3 scripts/nmbot.py context-gate`.
Stage 0 requires `--project-id`, `--evidence-type` and
`--definition-of-done`. The command delegates to deterministic navigation at
most once, deduplicates authorized source paths, applies `do_not_open` patterns,
and enforces at most two sources, 80 lines and 8000 characters. It emits
`bounded-retrieval.v1` using only the canonical route and stop-reason enums.

History and production requests return zero-context handoffs. Foreign project
requests fail closed unless an explicit bounded dependency card authorizes one
contract at depth one. Fallback paths remain candidate-only and are not evidence.
The trace contains stable IDs and counts, not the question, source body, payload,
logs or secrets.

The recommended executor mode is explicit-target STOP-2: the session/navigation
selects an exact stage ID, Python symbol or owner-scoped docs anchor, then calls
the gate with `--target-kind` and `--target` (plus `--target-owner` for docs).
For strict `--target-kind stage`, `--target` must be one concrete `stage_id`;
path IDs such as `v2.turn.v1` are navigation-only and must be resolved to an
individual stage before the gate is invoked.
The gate does not interpret the natural question in this mode; it only validates
the target, resolves the exact span, enforces the budget and writes the trace.

`config/nmbot_context_gate_intents.json` is a separate optional legacy pilot for
a small set of approved recurring developer wordings. It uses schema
`nmbot.context_gate_intents.v1`; a card declares exact `match_all` terms, an
evidence type, a resolver query and an active `owner_path`. A matched card only
changes the local resolver query; it emits only `intent_card_id` in the trace and
does not expose the original wording. It is not a natural-language classifier or
the recommended executor route.

Docs intent cards are owner-scoped: the anchor resolver may select a heading only
inside the declared active `owner_path`, not from global docs top results. The
registry has no LLM, fuzzy matching, history lookup, foreign-project traversal or
cross-owner docs expansion. It is a pilot navigation shortcut and must be judged
on a fresh holdout before any quality-success claim.

Example local invocation:

```bash
python3 scripts/nmbot.py context-gate "v2.search" \
  --project-id nmbot --evidence-type stage \
  --definition-of-done "owner source and focused test" --json
```

Focused local tests establish contract enforcement, not retrieval quality or
production behavior. A fresh independent holdout after this implementation is
still required; the observed 30-case set in section 15 must not be reused as
independent evidence.

The strict executor holdout is complete at
`/tmp/opencode/nmbot_context_gate_strict_holdout_20260725/`: route, privacy,
budget and fail-closed checks passed for all 30 requests. Twenty-seven targets
were complete; three source symbols exceeded the 80-line envelope. Those three
returned only the first bounded span with `context_budget_reached`, not a false
completion. A caller must treat that stop reason as an explicit continuation
decision, not as permission to infer unread lines. This is local gate evidence,
not production or generalization proof.

## 17. What developers get now

This protocol is a local development workflow, not a client-facing bot feature.
It is ready when a developer or the current session can name an exact target:

```text
stage / symbol / owner docs anchor
→ strict STOP-2 gate
→ at most two source ranges, 80 lines and 8000 characters
→ one focused test or an explicit bounded stop
```

For example, `v2.search` resolves to its owner function and a focused test;
the gate records why those ranges were read and rejects a broad path such as
`v2.turn.v1` as evidence.

For a free Russian question such as “why is the bot talking about money?”, the
system can show bounded candidate cards, but it cannot yet guarantee which owner
file is correct. The current session must select one target (or abstain) before
the gate reads code. This is intentional: reading 80 precise lines from a wrong
file is still a mistake.

The practical improvement is therefore predictable work rather than autonomous
guessing: less irrelevant context, exact source/test ranges after target choice,
and an explicit stop when evidence is incomplete. Natural-language target
selection remains supervised; it is not an autopilot or runtime decision.

## 18. Evidence and official research links

Project evidence:

- `docs/NOTEBOOKLM_PROJECT_ISOLATION_PLAN.md` — original ownership and migration
  proposal.
- `docs/BOUNDED_RETRIEVAL_PROTOCOL.md` — original STOP-2 bounded retrieval draft.
- `AGENTS.md` — compact documentation route and source-of-truth boundaries.
- `docs/CURRENT_ARCHITECTURE.md` — current local navigation entry point.
- `docs/NMBOT_RETRIEVAL.md` and `docs/NMBOT_CONTEXT_PACKS.md` — current local
  candidate/navigation and bounded-context behavior.
- `docs/NMBOT_CONTEXT_WORKFLOW_JOURNAL.md` — cumulative aggregate results journal
  for local context/navigation work, with raw records kept in temporary roots.
- `docs/NMBOT_CONTEXT_WORKFLOW_PRODUCTION_ROADMAP.md` — authoritative checklist
  for productionizing the local developer context workflow only.
- `docs/NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md` — documentation-only checklist for
  future adaptive selector / experience-bank hypotheses.

Official references:

- GitHub Code Search syntax: https://docs.github.com/en/search-github/github-code-search/understanding-github-code-search-syntax
- Sourcegraph query syntax: https://sourcegraph.com/docs/code_search/reference/queries
- Diátaxis documentation framework: https://diataxis.fr/
- Anthropic Contextual Retrieval: https://www.anthropic.com/news/contextual-retrieval
- Azure AI Search hybrid search: https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview

Vendor findings are not project proof. They may inform hypotheses, but project
behavior must be established by this repository's own sources, tests and future
independent benchmark.
