# Release identity

`runtime=V6` is the behavior contract. `release_id` identifies one immutable build of that
runtime. The artifact manifest hashes every included file and carries an in-release identity
file. The controller verifies archive, manifest, extracted file hashes and isolated startup
before registration. The builder itself requires a clean Git worktree and embeds the exact
commit SHA, tree SHA and deterministic clean-tree receipt in the hashed manifest. Registration
does not accept a separate operator-supplied SHA.

TEST and PROD registry state is independent. Each profile records active and previous A/B
targets. Sync or promotion copies exact bytes and metadata but never activates PROD.

The append-only release journal is SHA-256 chained and stores only bounded receipt/status
metadata. Existing release IDs are never overwritten or automatically deleted.

The A/B controller owns only the V6 API artifact. Bridge and callback-worker source is present,
but their deployment remains blocked until a separate immutable install/rollback owner is
implemented and verified; they are not accepted as A/B artifacts.
