# Qapairs retrieval adapter contract

Status: READY LOCAL/PILOT for local developer context navigation only.

This is a local developer-only evidence package for the Qapairs adapter. It is
route-eligible through the local project registry and generic navigation/context
gate, but it is not production proof and it does not activate runtime, CRM,
Google Sheets, VPS, NotebookLM writes, or autonomous routing.

## Current owner

- Owner root: `/home/ser/projects/cc-daemons`.
- Canonical notebook: `cc-daemons`.
- Current V1 source is the issue-log autonomous contour: `tools/issue_qa_autonomous.py`, `tools/issue_qa_orchestrator.py`, and `tools/issue_qa_quality_gate.py`.
- Current V2 source is report-only: `tools/qapairs_gemma_facts_to_pairs.py`.

Source references: `/home/ser/projects/cc-daemons/PROJECT_MAP.md:69-82`, `/home/ser/projects/cc-daemons/docs/qapairs-operational-modes.md:34-66`, and `/home/ser/projects/cc-daemons/tools/qapairs_gemma_facts_to_pairs.py:2-10`.

## Legacy is not canonical

Do not treat `/home/ser/projects/qapairs-daemon` or `cc-daemons/projects/qapairs` as the current canonical owner. They may be useful historical context, but they are not the owner root for this adapter package.

## What this package proves

The JSON files in `config/` are candidate retrieval evidence. They prove only that exact files, symbols, tests, docs, and diagnostic literals exist in the owner tree at validation time.

They do not prove runtime root cause, production state, CRM state, Google Sheets state, or VPS state.

Verified local acceptance artifacts:

- `/tmp/opencode/qapairs_context_acceptance_predictions_v1.json`
- `/tmp/opencode/qapairs_context_acceptance_score_v1.json`
- `/tmp/opencode/qapairs_context_acceptance_report_v1.md`

Acceptance aggregate: `hard_pass=true`; positives `12/12` exact owner+symbol;
negatives `14/14` abstain; false selections `0`; false abstentions `0`;
positive budget `12/12`; maximum selected sources `2`, lines `80`, characters
`4314`; unsafe claims `0`. This proves local context routing quality on the
locked acceptance set only, not production behavior.

## Safe local checks only

Allowed checks:

- `python3 scripts/qapairs_context_manifest.py`
- `python3 -m py_compile scripts/qapairs_context_manifest.py`
- `python3 scripts/qapairs_context_acceptance_generate.py --inputs tests/fixtures/qapairs_context_acceptance_inputs_v1.json --output /tmp/opencode/qapairs_context_acceptance_predictions_v1.json`
- `python3 scripts/qapairs_context_acceptance_score.py --predictions /tmp/opencode/qapairs_context_acceptance_predictions_v1.json --labels tests/fixtures/qapairs_context_acceptance_labels_v1.json --output /tmp/opencode/qapairs_context_acceptance_score_v1.json --report /tmp/opencode/qapairs_context_acceptance_report_v1.md`
- focused tests for `tests/test_qapairs_context_manifest.py`
- focused tests for `tests/test_qapairs_context_acceptance_harness.py` and
  `tests/test_project_navigation_core.py`

Forbidden from this adapter package:

- CRM calls or CRM-send commands.
- Google Sheets reads/writes.
- Network calls, SSH, or VPS checks.
- Eval runs.
- Runtime log parsing used as a current production claim.
- Any production/runtime activation or NotebookLM write. Local developer
  navigation/context-gate routing is allowed only through
  `scripts/project_navigate.py` and `scripts/project_context_gate.py`.

If a user asks for current production Qapairs status, this package is not enough. Use the project runbook/live checks outside this static adapter task.
