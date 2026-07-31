# Documentation update gate

This gate is local-only and fail-closed. The CLI always records candidate
documentation facts in the checked-in queue path
`data/project_documentation_updates.jsonl`, resolves the one canonical owner
document from the checked-in `config/project_documentation_owners.json`, and can
print a human patch plan. Callers may supply only the input event JSON path for
enqueue, or only a repo-relative VERIFY receipt path for capture; the CLI does
not accept owner-map or queue-path overrides. It never
edits docs, calls NotebookLM, changes runtime, or claims production state.

Flow:

1. VERIFY result is captured either as a strict full queue event, or as a
   strict `project_documentation_verify_receipt.v1` receipt.
2. `python3 scripts/nmbot.py docs-gate --enqueue --input path/to/event.json`
   validates and appends the event.
   Or, after VERIFY, `python3 scripts/nmbot.py docs-gate --capture --input
   path/to/receipt.json` validates the receipt, derives the canonical queue
   event, resolves the target document from checked-in owners, and atomically
   appends only that event. Capture output contains safe metadata only:
   receipt/update id, status, project/topic, canonical target doc, and whether
   the append happened. It never prints the fact or evidence bodies and never
   prints a patch plan.
   A technically verified event or passed receipt must contain passed
   verification, timestamp and evidence, but it may wait in the queue for human
   approval. Pending or failed receipts stay pending and must have no
   verification timestamp.
3. `python3 scripts/nmbot.py docs-gate --plan --update-id ID` routes it to the
   canonical owner doc and prints a patch plan only when the event is verified,
   approved by a human, has passed verification, has evidence, and all write or
   runtime authorization flags are false.
4. A human performs an explicit docs edit separately.
5. Static checks run separately.
6. A NotebookLM note is written only when separately authorized; this script
   stops before that step.

Useful commands:

```bash
python3 scripts/nmbot.py docs-gate --validate --json
python3 scripts/nmbot.py docs-gate --enqueue --input tmp/doc_update.json --json
python3 scripts/nmbot.py docs-gate --capture --input tmp/verify_receipt.json --json
python3 scripts/nmbot.py docs-gate --plan --update-id update-001 --json
python3 scripts/nmbot.py docs-gate --list --json
```

Receipt schema: exact fields only — `schema`, `receipt_id`, `project_id`,
`topic`, `change_type`, `fact`, `evidence_refs`, `verification`,
`supersedes_anchor`, and `created_at`. `receipt_id` becomes `update_id`.
Receipts cannot supply human approval, target documents, or any write,
NotebookLM, runtime, or production authorization flags. Capture always sets
those flags to false, so capture does not approve, publish, edit docs, write
NotebookLM notes, run subprocesses, touch network, or call production.

Boundaries: no target document override is accepted from callers, source/test/doc
evidence must use repo-relative refs with line or symbol anchors, artifacts need
SHA-256, and raw requests, logs, payloads, transcripts, labels, secrets, customer
data, absolute paths, and traversal are rejected.
