# V0 production dialogue audit — 2026-07-21

## Scope

- Source: production `logs/dialogue_journal.jsonl` plus
  `logs/dialogue_runtime_versions_backfill.jsonl`.
- Included only rows with proven `runtime_version=V0`; `UNKNOWN` was not inferred
  as V0.
- 98 proven V0 rows, 27 session hashes.
- Three records are eventless bot-only lifecycle/debug rows. Excluding those from
  paired-dialogue counts leaves 95 rows across 24 sessions.
- 17 complete start-only sessions and 7 meaningful sessions were reviewed.

## Meaningful sessions

| Session suffix | Time UTC | Result | Evidence |
|---|---|---|---|
| `92973872bded` | 13:28–15:12 | Partial | Family search returned three sparse cards (`1100`). Later rental search and selected third option worked (`1180`, `1184`), but typo acceptance `хчоу` replayed the shortlist (`1186`). |
| `c277000ebe6f` | 15:35 | Partial / user stopped | Phone was accepted and the bot asked for a name (`1210`); no next user turn exists. |
| `dbfe7730081e` | 15:35 | Pass | Phone → name request → `Иван` → `callback_queued` confirmation (`1213–1216`). |
| `6f53fab60d46` | 15:36 | Pass | Rental search returned three grounded cards, budget annotations and one selection question (`1225–1226`). |
| `87f17d4dcfa5` | 15:38–15:39 | Pass | Rental search → third option → selected-object card and one availability question (`1235–1238`). |
| `adac6b9759bd` | 15:56–15:59 | Fail, historical | Upstream/OpenRouter failure produced `runtime_config_error`; later turns fell back to operator (`1261–1266`). Error journal confirms gateway error, timeout and malformed answer. |
| `b59e87825f7b` | 16:17–16:24 | Partial, historical | Search and third-option selection worked (`1285–1288`). First `хчоу` reached a safe phone request but did not name the selected ЖК (`1290`); repeated `хчоу` ended in `runtime_config_error` (`1292`). Error journal confirms option-name mismatch and later gateway failure. |

Result distribution: 3 pass, 3 partial, 1 historical fail.

## Historical failures

- `15:57:47Z`: OpenRouter/gateway response error (`choices`) →
  `runtime_config_error`.
- `15:59:37Z`: gateway timeout and malformed answer output.
- `16:06:32Z`: `/start_0` handler exception in V0 state serialization; this is
  the unmatched user-only start event.
- `16:17:52Z`: `answer_option_names_mismatch` on `хчоу`.
- `16:24:38Z`: scenario-search gateway error (`NoneType.get`) →
  `runtime_config_error`.

All listed failures predate the final V0 isolation deployment at approximately
16:59 UTC. The later API smoke recorded in `docs/archive/working-history/2026-07-24/progress.md` completed rental search
→ third option → typo acceptance → operator phone request.

## Cross-dialogue discrepancies

### Client-visible wording

Multiple historical responses contain internal wording such as `карточки`,
`по проверенным данным`, `непроверенным данным`, `бот настроен некорректно` and
`диалоговое состояние`. This conflicts with `docs/IDEAL_IRINA_UX.md:159-164`,
which requires ordinary human wording without internal data/system language.

Resolved for current V0 deterministic output: the post-fix search starts with
`Нашла три подходящих варианта` and uses `эти варианты`; search, selected and
operator replies passed the forbidden-word regression and live check.

### V0 runtime diagnostics

Several visible search/selection responses end with one question, but their
`runtime_summary` reports `question_count=0` and
`final_question_at_end=false`. The same summaries report zero search/gateway
calls and empty state around visible searches. This is a diagnostics
discrepancy; client output itself still contains the expected question.

Resolved after the audit: fresh production session `v0-fix-chat-20260721`
records `scenario_search=1`, `answer=1`, `gateway_attempts=2`, correct
before/after option and selected/pending state, `question_count=1`,
`final_question_at_end=true` and no blockers for search, selection and operator
turns.

### Client-facing name

Resolved product decision: V0 client-facing name is **Валерия**. Historical V0
lifecycle greetings that used `Ирина` are audit evidence of the old mismatch, not
the current V0 identity. The canonical version table is
`docs/NMBOT_RUNTIME_VERSIONS.md`.

### Rental relevance

Historical rental results are grounded and budget-aware, but mostly explain
entry price, readiness and location. They do not invent yield or demand; this is
a relevance/presentation gap, not a grounding failure.

## Current-state boundary

- API and n8n bridge are active.
- A fresh post-deploy V0 Jivo E2E was completed at `18:20–18:21 UTC`, session
  suffix `efb2d8922181`: `/start_0` → rental search up to 30m → `третий` →
  `хчоу`.
- All eight canonical rows have direct `runtime_version=V0`. Search returned
  three options, selection stayed on `Мичуринский парк`, typo acceptance retained
  the rental subject, requested a phone and did not replay the shortlist.
- No new error event appeared after any of the four turns; API and bridge stayed
  active. Operational Jivo routing/state/response flow therefore passes.
- One post-deploy V0 error at `17:19:35Z` correlates with the unsupported
  `/start_2` attempt through `/api/chat`, not the Jivo per-session command path.
- A second post-fix Jivo E2E removed the client-wording gap and confirmed real
  V0 call/state/question diagnostics in the canonical journal.
- Formal Google Sheet/MCP-quality publication is blocked: canonical Jivo logs do
  not retain the raw MCP request/response required by
  `docs/LLM_SCENARIO_EVAL_RUBRIC.md:791-811`. No MCP payload was reconstructed or
  invented.

## Evidence limitations

- The journal has no raw MCP response, so visible prices/facts cannot be
  independently revalidated against source payloads in this audit.
- 1099 historical rows remain `UNKNOWN` and were not counted as V0.
- Contact `callback_queued` proves conversational acceptance, not final Google
  Sheets delivery.
