# CC2 local retrieval evidence

This package is a pilot-ready local static adapter for developer routing evidence only. It is not a runtime path and does not execute CC2 code, CRM, Sheets, VPS, network or eval.

Anchors:

- Owner root: `/home/ser/projects/cc-daemons`.
- Canonical notebook: `cc2`.
- Registry status: `pilot_ready`; retrieval status: `pilot_ready_local_developer_routing_only`.
- Route resolvers: `scripts/project_navigate.py`, `scripts/project_context_gate.py`.
- Evidence: `PROJECT_MAP.md:63-80`, `REFERENCE.md:257-280`, `REFERENCE.md:315-317`.
- Direct-only chain: `projects/cc2/ingest_server.py` → `projects/cc2/direct_inbox.py` → `projects/cc2/direct_worker.py` → `projects/cc2/pipeline.py`.
- Acceptance metrics: 15/15 positive exact owner+symbol, 17/17 negative abstain, false selections 0, max selected sources/lines/chars 1/80/3515, 4 honest clipped budget stops, unsafe claims 0.
- Boundary: diagnostic owner routing identifies the local owner target; it is not runtime root-cause proof, production proof, NotebookLM migration authorization, or adaptive behavior.

The validator rejects absolute paths, traversal, symlink escapes, arbitrary manifest paths, runtime logs, dotenv files, backups, runtime SQLite, live CRM/Sheets/network/VPS/eval operations, and disconnected legacy Sheets paths.
