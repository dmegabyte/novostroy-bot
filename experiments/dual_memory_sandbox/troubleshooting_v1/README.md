# Troubleshooting diagnosis scenarios v1

Status: `PREPARED_NOT_RUN`.

This directory contains three static, non-executable diagnosis scenarios for the
dual-memory sandbox. They are meant to test whether an agent can correlate
ordered evidence, state a short natural-language diagnosis summary, and explain
rejected tempting hypotheses without editing code or touching production.

No experiment is authorized by this preparation package. Do not run agents,
models, providers, scorers, evals, network calls, VPS commands, fixtures, or
project runtime code from these materials.

## Scenario set

- `diag-composer-rollout-01`
- `diag-client-text-leak-01`
- `diag-mcp-artifact-01`

## Source anchors used

- `docs/POSTMORTEM_2026-07-22_CONDITIONAL_COMPOSER_ROLLBACK.md:10-18`
- `docs/POSTMORTEM_2026-07-22_CONDITIONAL_COMPOSER_ROLLBACK.md:41-75`
- `docs/POSTMORTEM_2026-07-22_CONDITIONAL_COMPOSER_ROLLBACK.md:77-90`
- `docs/archive/working-history/2026-07-24/findings.md:1511-1528`
- `docs/LLM_SCENARIO_EVAL_RUBRIC.md:40-54`
- Notebook source `ec4b2c314301` only as the historical claim that this MCP echo
  placeholder issue occurred; it is not reproduced here.

## Safety boundaries

Public cards use synthetic/redacted observations and do not include private
labels, raw payloads, private verifier criteria, remediation steps, production
paths to inspect, mutation instructions, or canonical diagnosis/rejection codes.
Candidate answers must be diagnosis-only JSON with `diagnosis_summary`, public
`evidence_ids`, natural-language `rejected_hypotheses`, `confidence`, and a
read-only/static `next_safe_check`.

Private labels retain canonical IDs, required evidence IDs, confidence, pass
criteria, source references, and scorer-contract metadata. Public cards must not
disclose private values or scoring criteria. The stdlib-only static verifier
validates shape, public evidence, confidence, and safe prose; it does not exact
match diagnosis/rejection prose to private aliases and does not execute project
code. A later independent read-only scorer must check semantic correctness
separately and return safe booleans only.
