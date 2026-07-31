# NMBOT prompt provenance

Prompt provenance links an answer or a quality run to the exact prompt texts
used or configured at that boundary. It stores identities only; prompt bodies,
model payloads, provider responses and secrets are forbidden.

## Identity contract

- Schema: `nmbot.prompt_provenance.v1`.
- Each prompt has `stage`, safe repository-relative `source`, exact UTF-8
  `sha256`, short `prompt_id=p_<12 hex>` and `usage`.
- A sorted canonical prompt list produces `set_sha256` and
  `prompt_set_id=ps_<12 hex>`. Ordering and `usage` do not change this identity.
- `coverage=complete` means the set describes prompt stages actually invoked at
  that boundary. `configured_only` means a deterministic/offline check records
  the configured prompt bundle but did not call those models. `partial` is
  incomplete evidence.

The SHA-256 identity is authoritative. Human labels such as `P###` remain
optional descriptions and must not replace the hash.

## Dialogue linkage

Planner, search and response-composer identities are collected in the runtime
trace. The API copies the sanitized set only to the normal terminal bot row in
`logs/dialogue_journal.jsonl`; existing hashed session/event references link it
to the corresponding client message. Stages that were not called do not receive
invented identities.

`scripts/nmbot_dialogue_report.py` shows `prompt_set_id` and compact prompt IDs
for each terminal turn. Old rows remain `UNKNOWN`; exact prompt bytes cannot be
proven retroactively.

## Repeated quality runs

Every `scripts/nmbot_v2_quality_gate.py` invocation emits
`nmbot.quality_run.v1` metadata with unique UUID `run_id`, UTC `started_at`,
`release_id`, fixture SHA-256 values, prompt provenance and per-case
`prompt_set_id`.

- Offline fixture replay records the full configured set with
  `coverage=configured_only`; this is contract evidence, not model output.
- Live mode records only the search and one-shot response-composer prompts that
  this runner actually invokes, with `coverage=complete`.
- `--report` additionally writes a mode-`0600` JSON artifact under
  `logs/quality_runs/`; without it, no durable artifact is created.

Comparisons must use both `run_id` and `prompt_set_id`: one prompt set can have
many runs, while different sets must not be merged into one version bucket.

## Release boundary

Release identity tracks the search prompt plus response writer, formatter and
one-shot composer prompt files. A matching `release_id` identifies the source
bundle; it does not prove invocation, answer quality or production delivery.
Production evidence still requires a fresh correlated Jivo trace.

Sources: `nmbot_v2/prompt_provenance.py`;
`nmbot_v2/response_composer.py`; `scripts/nmbot_runtime_adapter.py`;
`scripts/dialogue_journal.py`; `scripts/nmbot_api_server.py`;
`scripts/nmbot_dialogue_report.py`; `scripts/nmbot_v2_quality_gate.py`.
