# NotebookLM summary-only migration write plan

## Status

Prepared on 2026-07-26 from the local pre-migration manifest. Updated on
2026-07-27 after a fresh safe inventory showed that the previous 364-record
baseline was incomplete. This document records the plan and aggregate execution
outcome only; it does not authorize any future NotebookLM write by itself.

The current refreshed pre-migration manifest contains 602 historical records:
42 route-eligible records are selected for a future summary plan, 556 are held
with no migration, and 4 are excluded as sensitive. The refreshed baseline is
authoritative for future work; the earlier 364-record inventory remains a
historical snapshot because 238 previously omitted records were discovered.
This is not a write authorization. The execution gate remains blocked until a
separate owner gate and per-record write authorization are approved.

The matching no-write backlog disposition retains 188 records already located in
confirmed canonical notebooks and 9 records in explicit non-project notebooks;
neither group needs copying. It holds 359 mixed or unproven records without
routing and permanently excludes 4 sensitive records from migration. The 42
selected records remain summary candidates only, with no write authorization.

### 2026-07-27 nine-record canonical summary outcome

The user separately approved nine exact, provenance-bound historical summaries
for the canonical `nmbot` notebook. All nine `add_note` operations completed,
and each returned note was verified immediately by metadata-only local inventory
against its authorized full-content SHA-256. The safe ledger is
`data/notebooklm_summary_batch_write_outcome_20260727.json`.

That checked-in ledger consumes the exact nine-note write authorization. Reruns
of `scripts/project_memory_notebook_summary_batch_write_plan.py` must validate
the authorization and ledger, then deny with a consumed/idempotency no-op and
must not emit writable operations.

- New canonical notes: 9; metadata-SHA verification: 9/9.
- No source record was changed or deleted; routing and production claims remain
  unchanged.
- This does not authorize any remaining selected, held, unresolved, or sensitive
  record. They retain their existing disposition and require a new authorization.

### 2026-07-26 bounded selected-set execution outcome

Before the routability fix, the inspected selected candidate set contained 38
records. The user authorized exactly that historical selected set for a bounded
migration run under user-scoped trial/batch owner `ser`. This did not assign or
weaken the generic owner registry semantics: project-level human owner and
rollback owner remain `TBD` outside this explicit run.

Aggregate-only outcome:

- Authorized selected set: 38 records; all 38 were dispositioned.
- Added to the canonical notebook only: 4 unique safe historical summaries.
- Added nmbot note IDs: `08457e4cbd6a`, `13fd5105a908`, `fa2cb21c28a4`,
  `3df80decbd93`.
- Qapairs writes from that historical inspected set: 0. Existing four verified
  NMBot note writes remain valid historical facts and must not be undone or
  duplicated.
- Each added summary had metadata SHA provenance verified one-by-one.
- Expected policy exclusions: 23 records were blocked for sensitive, customer,
  transcript or uncertain indicators and were not written.
- Retained without write: 11 records — 3 likely duplicates, 7 dedupe-uncertain,
  and 1 already present in the canonical notebook.
- Still untouched outside the authorized selected set: the refreshed manifest's
  556 held records and 4 sensitive exclusions. They require fresh disposition;
  no record is eligible for a write merely because it appeared in the earlier
  364-record snapshot.
- No source deletion or source mutation occurred. No routing, production,
  runtime, model, provider, prompt, eval, VPS or MemPalace behavior changed.

This completes only the bounded authorized selected set. It is not a full
historical corpus migration and it does not authorize processing of unresolved,
sensitive, Qapairs historical, or future records. Context routing and historical
migration are separate decisions: Qapairs can be used for local route selection,
but its historical NotebookLM records still require a separate write review.
CC2 and MPN local pilot routing likewise grant no NotebookLM migration or write
authorization.

## Scope

- QApairs canonical owner root: `cc-daemons`; standalone `qapairs-daemon` is a
  legacy poller.
- N8N_AUDIT canonical notebook: `n8n_audit`.
- Only a record with `disposition: selected_for_summary_plan`, a nonempty
  `target_canonical_notebook`, and an `ok=true` project registry resolution can
  enter a proposed summary batch.
- Each original record remains unchanged. There is no deletion, bulk raw copy,
  automatic legacy routing, or production-status claim.

## Preconditions — all required before the first write

1. A named human owner and a named rollback owner approve the batch.
2. A separate write authorization states the exact selected record IDs and target
   canonical notebooks.
3. The source record's notebook/kind/id and metadata SHA-256 still match the
   pre-migration manifest.
4. The proposed summary contains no secret, raw log, full transcript, customer
   data, payload, or stale production status claimed as current.
5. The target notebook is canonical for the target project; legacy notebooks are
   not write targets.

If any precondition fails, stop before writing that record and leave the original
record unchanged.

Content-policy blocks are expected exclusions and do not mean the migration tool
failed. Integrity mismatch, unexpected tool/write/readback failure, wrong target,
or provenance verification failure stops processing at the first failed record.

## Per-record summary procedure

1. Open one approved record only inside the controlled migration process.
2. Verify its identity and SHA-256 against the pre-migration manifest.
3. Draft a short historical summary containing only: decision/fact, date context,
   owner boundary, and source provenance reference. Do not reproduce raw text.
4. Validate the summary against the prohibited-content check in the preconditions.
5. Write the summary only to that record's `target_canonical_notebook`, with its
   provenance and source SHA-256.
6. Immediately read back only safe metadata/provenance and verify the target,
   record count and source-SHA reference.
7. On the first failed write or verification, stop the batch. Rollback is
    routing-only; original notebooks are never deleted.

## Post-batch acceptance

- Every written summary has exactly one approved source reference and metadata
  SHA-256 provenance.
- No held or sensitive record was written.
- Canonical-only route checks pass and legacy notebooks remain excluded from
  automatic routing.
- Current-code questions still hand off to source/tests; production questions
  still require fresh authorized live evidence.

## Inputs

- `/tmp/opencode/nmbot_notebook_pre_migration_manifest_refresh_20260727T000000Z.json`
  — current temporary, local, safe pre-migration worklist: 42 selected,
  556 held, 4 sensitive, 602 total; execution blocked and no write authorization.
- Earlier 364-record manifests are historical execution artifacts only and must
  not be reused as the current migration worklist.
- `/tmp/opencode/nmbot_notebook_backlog_disposition_refresh_20260727T000000Z.json`
  — matching no-write disposition manifest: 188 retained in canonical notebooks,
  9 retained outside this migration programme, 359 held, 4 excluded, 42 selected
  candidates; no notebook mutation or authorization.
- `config/project_memory_registry.json` — canonical route registry.
- `config/project_memory_tree.json` — project ownership decisions.
- `docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md` — migration safety and acceptance
  contract.
