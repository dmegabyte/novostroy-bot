# NMBot archive index

Purpose: this index points to historical records that are intentionally no longer
loaded as root working context. The archive preserves old append-only planning
evidence while the root files stay compact for current sessions.

## Working-history archive

- Directory: `docs/archive/working-history/2026-07-24/`.
- Archived full plan: `docs/archive/working-history/2026-07-24/task_plan.md`.
- Archived findings/evidence: `docs/archive/working-history/2026-07-24/findings.md`.
- Archived progress log: `docs/archive/working-history/2026-07-24/progress.md`.

These files were moved from the project root with their original filenames. They
are historical snapshots: old statuses, commands and conclusions must be read as
dated evidence, not as current production state.

## Release-candidate archive

- Directory: `docs/archive/release-candidates/2026-07-24/manager_rewriter/`.
- Contents: the complete manager-rewriter candidate overlay with manifest,
  candidate hashes, and identity `2026-07-24.v2v3-phone-first-callback.12`.
- Status: historical candidate evidence; not an active release source or deploy
  target. The former `release_bundles/manager_rewriter/` path contains only a
  compatibility redirect.
- Consumer boundary: `scripts/nmbot_manager_rewriter_release.py` and its
  release tests intentionally resolve the archived owner path.

## How to cite archived entries

1. Open the relevant archived file.
2. Cite the archive path and the section title, for example:
   `docs/archive/working-history/2026-07-24/findings.md`, section
   `External Field Sales Registry — initial decision, 2026-07-21`.
3. If current behavior matters, verify against current owner docs/source/tests and
   fresh explicitly authorized VPS/Jivo evidence when production status is in
   scope.

## Safety policy

- Do not delete archived planning records during cleanup or context-reduction
  work.
- Do not rewrite archived historical content to make old statuses look current.
- If a rollback of planning context is needed, restore from the dated archive
  path instead of reconstructing from memory or generated summaries.
- New current-session notes belong in the compact root `task_plan.md`,
  `findings.md` and `progress.md`; old evidence belongs in the archive.
