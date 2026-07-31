# NMBOT four-layer prompt rebuild — 2026-07-16

Scope: local-only prompt candidate for the four-layer presenter. No model calls, no VPS/Jivo, no `.env`, no deploy.

## Comparison

| Prompt | Responsibility | Size | Known contradiction classes |
|---|---|---:|---|
| `prompts/chat_v1.txt` | Legacy mixed chat prompt: answer writing plus repeated routing/filter/scenario instructions. | 171 lines | Layer mixing: answer prompt can re-interpret search/routing policy and duplicate constraints already owned by code. |
| `prompts/four_layer_presenter_v1.txt` | Restricted presenter using sanitized `DecisionContext`. | 24 lines | Good boundary, but very terse; leaves little guidance for useful natural Russian and client-facing shape. |
| `prompts/four_layer_presenter_v2.txt` | Presenter only: render `decision_context.matched` into client language. Code remains owner of planning, MCP/search, normalization and validation. | 23 lines | Explicitly forbids other-layer work, MCP/search, claims outside `allowed_claims`, and options outside `matched`. |

## Candidate v2 contract

- Input source is only `decision_context`.
- Client-visible options come only from `decision_context.matched`.
- Claims are limited to `allowed_claims` for each `option_id`.
- Output stays runtime-compatible with `_parse_chat_json`: top-level `response`, `params`, `visible_options`.
- The final question is inside the `response` text, because the runtime `response_contract` currently lists only `response`, `params`, `visible_options`.

## Static checks added

`scripts/nmbot_prompt_static_check.py` compares v2 against v1 with deterministic criteria:

- v2 is no longer than v1;
- contains presenter boundaries: `decision_context`, no tools/search, `matched` only, `allowed_claims`, exactly one question, strict JSON, runtime fields;
- does not carry routing/MCP/scenario/filtering responsibilities.

## E2E harness change

`scripts/nmbot_four_layer_e2e.py` now accepts `--presenter-prompt` only together with explicit `--live`. Dry mode remains deterministic and no-network. This lets Chati later run isolated live E2E against `four_layer_presenter_v1` and `four_layer_presenter_v2` without changing runtime `chat_v1` behavior.

## Not proven yet

Model quality is not proven in this session. The candidate only passed local structural and deterministic harness tests. Chati still needs to run the later live isolated E2E/model comparison to see whether v2 improves real answers.
