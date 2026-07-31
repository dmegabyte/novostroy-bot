# Runtime memory schemas — templates only

The JSONL files in this directory are intentionally empty. They are runtime
templates, not outcomes.

## Layer A episode record

Allowed fields:

- `schema_version`: integer.
- `episode_id`: opaque stable ID.
- `project_id`: `nmbot`.
- `task_fingerprint`: hash of the public task card and public fixture tree.
- `family_id`: public family identifier.
- `partition`: learning or holdout.
- `route_code`: compact route/tool code, not raw prompt or source.
- `tool_error_codes`: bounded list of compact error codes.
- `fix_summary_code`: controlled vocabulary summary, not code text.
- `verification_result`: pass, fail, or blocked.
- `latency_ms`: integer.
- `tool_call_count`: integer.
- `repeated_error_count`: integer.
- `resource_aggregate`: safe numeric per-task aggregate containing wall_ms,
  OpenCode-normalized token/cost fields, wall time, tool/model call counts,
  failed tool calls and retries where covered by the read-only DB collector.
- `error_signature_counts`: compact bounded error-code counters only.
- `telemetry_coverage`: present/missing coverage flags. Missing instrumentation
  means not_evaluable for relevant comparisons, never zero. Retrieval and memory
  hint metrics are optional for the primary paired subagent pilot claim.

Forbidden fields: raw prompt, raw code, raw logs, labels, hidden assertions,
secrets, customer data, transcript bodies.

## Sealed learning memory snapshot

Allowed top-level fields are closed:

- `schema_version`: integer, currently `1`.
- `sealed`: boolean, must be `true`.
- `derived_from_phase`: `L` only.
- `support_task_ids`: exactly the nine manifest learning task IDs.
- `advisory_patterns`: closed list of safe advisory pattern entries. Each entry
  contains only `advice_code` and `support_task_ids`.
- `forbidden_source_task_ids`: safe source IDs only; no holdout IDs.
- `contains_raw_logs`, `contains_raw_prompts`, `contains_private_labels`: booleans,
  all `false`.
- `seal_hash`: SHA-256 of the canonical JSON safe payload excluding `seal_hash`,
  sorted keys and compact separators.

Unknown fields are rejected at the snapshot top level and inside each
`advisory_patterns` entry. Forbidden key names are checked recursively before
copying into an agent view, including labels, expected answers, hidden assertions,
holdout outcomes, raw prompt/code/body/log/private/secrets and equivalent nested
key names.

### Snapshot advisory pattern entry

Allowed fields are closed:

- `advice_code`: safe controlled ID only. No prose, raw code, prompt, log,
  expected answer, hidden assertion or private label text.
- `support_task_ids`: at least three distinct manifest learning task IDs. Holdout
  IDs and duplicate supports are rejected.

The sealed hash is deterministic over the canonical safe payload, including
`advisory_patterns`, excluding only `seal_hash`.

## Layer B advisory pattern record

Allowed fields:

- `schema_version`: integer.
- `pattern_id`: private pattern ID promoted only after scoring.
- `project_id`: `nmbot`.
- `support_task_ids`: at least three independent successful learning task IDs.
- `holdout_evidence_ids`: separate holdout scorer evidence IDs.
- `promotion_threshold`: integer, minimum 3.
- `advice_code`: compact advisory code, never a raw patch or private assertion.
- `scorer_id`: separate scorer identity.
- `support_ids`: learning-only scorer/episode IDs, no raw logs.
- `aggregate_observed_savings`: only after separate scorer/holdout approval;
  paired aggregate numbers only, no task bodies or private labels.

Promotion rules: at least three independent successful supports, separate fresh
holdout evidence, separate scorer, and advisory-only use.
