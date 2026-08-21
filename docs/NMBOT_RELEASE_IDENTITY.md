# NMBOT release identity

`runtime_version` and `release_id` answer different questions:

- `runtime_version` (`V0`, `V2`, `V3`) identifies the engine selected for this
  particular dialogue turn;
- `release_id` identifies the immutable source bundle that produced the turn.

`prompt_set_id` is narrower: it identifies the exact prompt texts configured or
actually invoked at a dialogue or quality boundary. Its contract is in
`docs/NMBOT_PROMPT_PROVENANCE.md`. A release can contain several prompt files,
while one turn may invoke only a subset.

New Jivo journal rows record both fields. `nmbot_dialogue_report.py` shows them
on every timeline row, so a client answer can be connected to its runtime and
to the release manifest used for a rollback decision.

## Manifest and identifiers

`data/nmbot_release_identity.json` has schema
`nmbot.release_identity.v1`, a safe `release_id`, creation timestamp and SHA256
hashes of the tracked source files. The checked-in `local-unreleased` identity
is only a local source baseline; it is not evidence of a deployed release.

Read the current local identity without writing anything:

```bash
python3 scripts/nmbot_release_identity.py show
```

Creating an identity writes a local file and is intentionally explicit:

```bash
python3 scripts/nmbot_release_identity.py create \
  --release-id 2026-07-22.v2-financing-copy.1 --write
```

Use a new immutable identifier for every deploy. Do not reuse an identifier for
changed hashes.

## Deploy and rollback boundary

An approved immutable release must carry an identifier. The release helper is
invoked only after a source snapshot, comparison, isolated worktree and local
artifact preflight:

```bash
python3 scripts/nmbot_atomic_release.py deploy \
  --release-id 2026-07-22.v2-financing-copy.1 \
  --archive <artifact.tar.gz> --manifest <artifact.manifest.json> \
  --host neiro@193.107.155.236 --source-snapshot-manifest-sha256 <sha256> --confirm
```

This command uses SSH, SCP and service restart; it requires an explicit release
stop/go and is not run by local checks.

Before a rollback, collect fresh read-only VPS/Jivo evidence, locate the affected
`release_id` in `nmbot_dialogue_report.py`, compare the manifest hashes with the
corresponding release backup, then obtain rollback approval. `release_id` makes
the source bundle traceable; it does not prove a healthy deploy, Jivo delivery,
or response quality.

Sources: `scripts/nmbot_release_identity.py`; `scripts/dialogue_journal.py`;
`scripts/nmbot_dialogue_report.py`; `scripts/nmbot_atomic_release.py`;
`nmbot_v2/prompt_provenance.py`.
