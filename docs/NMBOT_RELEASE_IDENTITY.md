# Release identity

`runtime=V6` is the behavior contract. `release_id` identifies one immutable build of that
runtime. The artifact manifest hashes every included file and carries an in-release identity
file. The controller verifies archive, manifest, extracted file hashes and isolated startup
before registration.

TEST and PROD registry state is independent. Each profile records active and previous A/B
targets. Sync or promotion copies exact bytes and metadata but never activates PROD.

The append-only release journal is SHA-256 chained and stores only bounded receipt/status
metadata. Existing release IDs are never overwritten or automatically deleted.
