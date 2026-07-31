# NMBot artifact retention policy

## Purpose and boundary

This policy classifies local release and evaluation artifacts so cleanup does
not remove rollback, provenance, regression, or historical evidence. It does
not prove a live release, production readiness, or an artifact's external
backup status.

Before any move or deletion, check the artifact owner, manifest/README, local
backlinks, and the relevant tests. A missing direct import is not proof that an
artifact is unused.

## Classification

| Class | Includes | Default action | Required evidence before change |
|---|---|---|---|
| `immutable_keep` | `release_bundles/atomic_full/` release archives and `release_bundles/bootstrap/` source snapshots | Keep in place | Explicit owner-approved retention exception, manifest/backlink review, and replacement provenance |
| `candidate_archive_first` | Candidate overlays such as `release_bundles/manager_rewriter/` and prepared-only bundles | Do not delete; classify and archive only after owner decision | Manifest status, test/backlink scan, experiment or changelog disposition, target archive path |
| `active_eval_keep` | `eval/nmbot-answer-quality/`, `eval/nmbot-search-prompt/`, their current configs, runners, fixtures, and tests | Keep | Owner confirms retirement and local tests/config consumers are removed or redirected |
| `versioned_eval_history` | Dated cases under eval version directories | Retain as dated evidence; archive-first only | Link to current suite, generation provenance, and owner-approved retention horizon |
| `generated_disposable` | `__pycache__/` and `.pyc` generated locally | May be removed locally | Confirm it is not tracked and has no runtime/release retention role |
| `unknown` | Logs, reports, `tmp/`, ad-hoc probes, or anything without a clear owner | Keep and investigate | Owner, backlink, and retention decision |

### Recorded archive decision

The manager-rewriter candidate bundle was owner-approved for archive-first on
2026-07-30. Its complete dated unit is retained at
`docs/archive/release-candidates/2026-07-24/manager_rewriter/`; the old
`release_bundles/manager_rewriter/` path is a compatibility redirect only. The
release helper and direct tests intentionally use the archived owner path.

## Release provenance rule

Immutable archives and bootstrap snapshots retain release IDs, hashes, source
provenance, and rollback evidence. They are not ordinary build output. Never
delete, rewrite, or move them as a generic disk cleanup action. The release
contract remains the owner of their creation and verification.

## Evaluation rule

Eval directories are opt-in developer tooling, not default retrieval context.
Do not run an eval merely to classify it. A dated case set can be historical
without being disposable: it may be the before/after evidence for an experiment.

## Archive workflow

1. Build a read-only retention table for the concrete paths.
2. Verify manifest/README, tests, docs, and lexical backlinks.
3. Record the owner decision and target archive location in a dated note.
4. Move only the approved artifact group; keep a redirect or index entry.
5. Run the relevant local documentation and consumer checks.

Deletion is a separate, explicit decision after archival and a second backlink
check. This policy does not authorize deletion by itself.

## Source anchors

- Immutable identity and provenance: `NMBOT_ATOMIC_RELEASES.md`, sections
  "Embedded release identity contract" and "Cutover consistency contract".
- Working-history retention discipline: `ARCHIVE_INDEX.md`, "Safety policy".
- Experiment evidence requirements: `EXPERIMENTS.md`, "Цель" and
  "Идентификаторы".
- Current audit: `ses_04d2456a8ffetlOwMzBu4voxOr`.
