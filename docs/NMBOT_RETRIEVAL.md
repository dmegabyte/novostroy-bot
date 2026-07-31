# NMBot FTS retrieval — bounded developer navigation

Purpose: find a small project-scoped candidate set before `grep`/`read`. This is
not bot runtime, customer-answer logic, model routing, or production proof.

## Deterministic navigate command

For known narrow targets, use `python3 scripts/nmbot.py navigate "QUERY" --json`
before broad FTS. The wrapper delegates direct argv to
`scripts/nmbot_navigation.py`; the command is local/read-only, stdlib-only,
builds no persistent cache, and does not call models, providers, network, VPS,
subprocesses or runtime/customer code.

Dispatch is intentionally small:

| Query shape | Route | Output contract |
|---|---|---|
| exact `v2.*` / `jivo.*` stage_id or path_id | stage map | up to three active source/doc/test/prompt paths |
| exact Python identifier such as `resolve_response_path` | AST | exact definition line span, plus at most one deterministic related test hint |
| exact failed-check or error code such as `formatter_content_mismatch` | diagnostic AST | up to three narrow detector functions, source before test |
| explicit docs/contract/UX/runbook/context-pack wording | docs anchors | existing heading or approved context-pack read-first anchor line |
| everything else | mixed FTS fallback | up to three candidate-only paths, `fallback=true` |

The registry is generated every invocation from active
`config/nmbot_retrieval_sources.json` paths only. Drift guard fails closed when a
stage ref is outside the active manifest, a Python source cannot be parsed, a
symbol span disappears, an approved docs anchor is missing, or a path/hash no
longer matches the generated registry. The output schema is
`nmbot.navigation.v1` with `route`, `reason`, `abstain`, `fallback`, `results`,
`next_action` and `registry_fingerprint`. Results are still navigation
candidates: grep/read the returned path/range before making claims. This is not a
model-quality solution and makes no production behavior claim.
Per-result `target_spec`, when present, is strict gate navigation metadata only;
it is not evidence and does not replace reading the selected source range.
The `diagnostic` route proves where an exact code is emitted or checked. Source
results are ordered by AST role (`emit` before `declare` before `reference`) and
then by narrow span. Dynamic suffixes are bounded: a pasted code such as
`unknown_complex:<value>` may resolve to the registered base `unknown_complex`
only when that exact base exists. It does not by itself prove which runtime stage
caused the bad outcome.

## STOP-2 context gate

`python3 scripts/nmbot.py context-gate` is the local machine-enforced layer after
the current session or `navigate` has selected an exact target. The recommended
form supplies `--target-kind stage|symbol|docs` and `--target`; docs also require
`--target-owner`. In this strict mode the gate does not interpret the natural
question or load intent cards. It resolves only that target, authorizes no more
than two source ranges under 80 lines / 8000 characters, and emits a privacy-safe
`bounded-retrieval.v1` trace. Any clipped target range stops as
`context_budget_reached`; it cannot claim `definition_of_done`. History and production requests are zero-context
handoffs; unapproved foreign-project traversal fails closed. Full contract:
`docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md`.

`config/nmbot_context_gate_intents.json` remains an optional legacy pilot for
repeated exact wordings. It is not the recommended route and must not be treated
as a Russian-language classifier. Pass `--intents PATH` only when that pilot is
explicitly wanted; an absent path disables it. Intent cards are exact
`match_all` mappings to a resolver and active `owner_path`; docs cards resolve
anchors only inside that owner path. A match adds only `intent_card_id` to the
trace and does not add LLM, fuzzy, history, foreign-project or production behavior.

This gate controls context consumption. It does not call NotebookLM, models,
providers, network, VPS or bot runtime and does not prove that selected evidence
answers the question. Its fresh independent quality holdout is pending.

Future adaptive selector / experience-bank ideas are tracked only as a
documentation checklist in `docs/NMBOT_ADAPTIVE_SELECTOR_HYPOTHESES.md`. That
file does not enable target selection behavior; the current route remains exact
target selection followed by the strict gate above.

## Canonical flow

1. Ask the in-memory SQLite FTS index for candidate cards:
   `python3 scripts/nmbot.py retrieve "почему первый список показывает финансовый дисклеймер" --term missing_note --term FIRST_LIST --json`.
   For the local source-card pilot only, add `--source-cards` to attach compact
   navigation context to matching pilot paths.
2. The current OpenCode session reviews the cards semantically and selects zero
   to four. Cards are candidates, not evidence.
3. If suitable cards exist, `grep` only their paths and `read` only the selected
   ranges or directly referenced consumers.
4. If no card is suitable, stop. Continue through the normal project route:
   current docs or stage map → `grep` → `read`. Never name a random file as the
   answer.

The script itself calls no model. Candidate cards are visible to the provider of
the current OpenCode session when that session performs the semantic choice.
Source cards, when enabled, are visible the same way: they are navigation hints,
not evidence, and they do not trigger automatic source reads.

## Deterministic FTS contract

- Source manifest: `config/nmbot_retrieval_sources.json`, schema
  `nmbot.retrieval_sources.v1`.
- Every source has `path`, `module`, `type`, `owner`, `status`; stage-linked
  entries carry normalized `stage_ids` validated against
  `config/nmbot_stage_map.json`; a stale extra stage fails closed rather than
  returning mixed cards.
- Archives, release bundles, logs, generated reports and Telegram legacy are
  excluded.
- Each invocation partitions current sources and creates a temporary in-memory
  SQLite FTS5 table with the `unicode61` tokenizer. No persistent retrieval
  cache is required.
- Ranking uses the frozen blind-experiment BM25 field weights: text 1, path 3,
  module 2, owner 2, stage IDs 4. Optional repeatable `--term` and `--phrase`
  values are neutral technical expansions supplied by the current session.
- Retrieval considers at most 20 raw chunks, deduplicates by source path, and
  emits at most eight cards.
- `--source-cards` validates `config/nmbot_retrieval_source_cards.json` against
  the active manifest, but it does not change chunk text, FTS query input,
  BM25 weights, raw chunk limit, path dedupe, ranking or abstention rules.

## Opt-in source-card pilot

- Registry: `config/nmbot_retrieval_source_cards.json`, schema
  `nmbot.retrieval_source_cards.v1`.
- The registry is a local developer navigation aid for the current OpenCode
  session. It contains compact Russian `purpose`/`concepts` and source-backed
  owner, entry-point and test pointers for the ten pilot paths.
- The loader is strict: exact card keys, no duplicate paths, active manifest paths
  only, bounded text/list sizes, and existing relative test paths.
- Default output remains compatible: no `source_card` fields are emitted unless
  `--source-cards` is passed. With the flag, output adds `source_cards_enabled`
  and a per-card `source_card` only for selected candidate paths present in the
  registry.
- A source card is not evidence and is not a reason to skip `grep`/`read`. The
  current session still selects zero to four candidates or abstains, then reads
  selected sources before making claims.

## Source partitioning and output budget

- Python chunks retain decorators, constants/tables and executable gaps before,
  between and after top-level definitions.
- A source chunk is at most 4000 characters, including hard splitting of one
  overlong line.
- `--cards` is bounded to 1..8, default 8.
- `--excerpt-chars` is bounded to 500..700, default 650.
- Total excerpt text is capped at 5600 characters.
- Output contains candidate ID, path, line range, owner metadata, stage IDs,
  FTS score and bounded excerpt—never full files. Opt-in source cards add only
  compact navigation metadata for matching pilot paths.

## Honest abstention and fallback

An empty lexical match returns `cards=[]`, `abstain=true`, and fallback route
`docs_stage_map_then_grep_read`. A non-empty result still requires the session to
select zero to four cards or abstain. The normal fallback is project docs/stage
map followed by targeted `grep`/`read`; Ollama is not part of this retrieval
route.

## Quality evidence

- `config/nmbot_retrieval_benchmark.json` is the frozen 20-case comparison set.
- `config/nmbot_retrieval_benchmark_v2.json` is a separate held-out set frozen
  before permanent-FTS release tuning.
- The blind prototype evidence is local and not production proof. Do not tune on
  either frozen set and then describe it as independent validation.
