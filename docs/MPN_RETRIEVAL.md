# MPN local retrieval adapter

This package is pilot-ready static evidence for local developer routing only. It does not run MPN, cc-daemons, CRM, Sheets, VPS, eval, or network operations.

Anchors:

- Primary project: `project_id=mpn`, canonical notebook `mpn`, owner root `/home/ser/projects/mpn-daemon`, owner/rollback owner `ser/ser`.
- Registry status: `pilot_ready`; retrieval status: `pilot_ready_local_developer_routing_only`; manifest `validated=true`.
- Primary source evidence: `/home/ser/projects/mpn-daemon/mpn_local_pipeline.py:1228-1491` for local stages `process_summary`, `process_tags`, `fetch_operator_tags_for_crm_guard`, `send_to_crm`, `main`.
- Route resolvers: `scripts/project_navigate.py`, `scripts/project_context_gate.py`.
- One-hop dependency rule: `docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md:156-182` allows only the explicit `cc-daemons` dependency card with `max_depth=1`, `max_records=2`, and no transitive traversal.
- Dependency card: `config/mpn_dependency_card.json` exposes only two cc-daemons interface records: direct ingest/inbox and direct worker orchestration.
- Acceptance metrics: 8/8 positive exact owner+symbol, 20/20 negative abstain, false selections 0, max selected sources/lines/chars 1/80/3540, 8 honest clipped budget stops, cross-project leakage 0, unsafe claims 0.

Static validation:

```bash
python3 scripts/mpn_context_manifest.py
```

The validator accepts the checked-in local manifests and fails closed on path overrides. The dependency is validator-only; it does not auto-index a second root, does not broaden to whole-repo `cc-daemons`, and does not allow transitive traversal.

Excluded by design: `.env`, secret-bearing config bodies, logs, archives, SQLite/runtime data, CRM/Sheets/network/apply/eval/VPS operations, arbitrary second-root traversal, production proof claims, NotebookLM migration/write authorization, and adaptive behavior.
