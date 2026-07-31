# Jivo live regression — 2026-07-16

## Scope

Manual browser checks in the Jivo widget only. Scenarios were sent one at a time. After each turn the visible DOM and sanitized bridge diagnosis were checked. No phone, token, payload, or full client/chat identifiers are recorded here.

## Gate

Production units before the run:

- `novostroy-bot-api.service`: active
- `novostroy-bot-n8n-bridge.service`: active
- `nmbot-callback-sheet-worker.service`: active

## Scenario 1 — family, hard budget and family facts

**Input class:** Moscow family search; three rooms; maximum budget; schools; reasonable delivery date.

**Observed:** The bot returned three family-oriented options and mentioned schools, kindergartens, finishing, and delivery context. The visible DOM contained the final answer. It did not repeat a generic fallback.

**Transport:** safe trace `trace_95e27aeda9bb`; upstream HTTP 200; final Jivo delivery HTTP 200; end-to-end bridge delivery completed. The diagnosis output reported no strict failure.

**Result:** `pass_with_coverage_gap`.

**Concern:** Prices were not shown in the visible answer, so the client could not verify the stated budget against each option. This is not a transport failure, but budget grounding remains only partially observable in the answer.

## Scenario 2 — investment, compact format, metro and demand

**Input class:** one-room investment/rental search; Moscow; metro; price of entry; confirmed demand factors; no promised yield.

**Observed:** The bot returned three one-room options, included entry prices and compact-area facts, and did not promise a numeric yield. The visible DOM contained the final answer.

**Transport:** safe trace `trace_0850dcba47d7`; final Jivo delivery HTTP 200. Diagnosis reported a coverage gap because the inspected trace lacked an upstream event, so transport delivery was observed but upstream provenance was incomplete.

**Result:** `fail — unsupported_claims`.

**Reason:** The answer stated that a project’s large offer volume “ensures liquidity”. The current investment contract permits `why_investment` only from confirmed facts (`docs/SCENARIO_MCP_CONTRACT.md`), and does not permit turning offer volume into a liquidity guarantee. This is a grounding/presenter-layer failure, not a Jivo transport failure.

## Scenario 3 — hard location and budget

**Input class:** only Sokol or Voykovskaya; one room; maximum budget; commute constraint.

**Observed:** The bot said the options were within budget, but returned a project labelled outside the requested locations and showed project price ranges extending well above the maximum. The final answer was visible in the widget and delivered with HTTP 200.

**Transport:** safe trace `trace_9018f16618e6`; final Jivo delivery HTTP 200. The diagnosis output showed a coverage gap because the upstream event was not present in the inspected structured slice.

**Result:** `fail — hard_constraint_violation`.

**Reason:** Hard location and budget constraints were not enforced at presentation level. This is the same systemic class as the earlier investment/location live caveat, not a phrase-specific error.

## Scenario 4 — large family layout, no exact match

**Input class:** two-room/euro-three; minimum area; large kitchen-living room; budget cap; finishing preference.

**Observed:** The bot honestly reported no exact options with finishing and proposed relaxing finishing, geography, or budget. It did not fabricate a shortlist.

**Transport:** safe trace `trace_3aeb3149397d`; final Jivo delivery HTTP 200. The diagnosis output showed a coverage gap because the upstream event was not present in the inspected structured slice.

**Result:** `pass — honest_no_match`.

## Scenario 5 — family mortgage follow-up over current options

**Input class:** family shortlist followed by a request to assess family-mortgage suitability without rebuilding the list.

**Observed:** The bot answered about the already displayed options, preserved the context, did not send a new shortlist, and avoided promising a specific rate or approval. It correctly stated that exact conditions depend on the bank, program, and client situation.

**Transport:** safe trace `trace_61182adc2116`; final Jivo delivery HTTP 200. The diagnosis output showed a coverage gap because the upstream event was not present in the inspected structured slice.

**Result:** `pass — current_options_follow_up`.

## Current conclusion

All five planned manual scenarios were completed after explicit permission to continue following the first failure. Jivo delivery was visible in the browser for every completed turn, but the bot is not regression-green. Open classes:

1. investment unsupported claims / evidence-to-language validation;
2. hard location and budget constraints still leak into the presented shortlist;
3. budget evidence is not always visible enough to the client in a family shortlist;
4. missing sanitized per-turn audit in the bridge diagnosis;
5. metro versus administrative-district normalization remains an open live caveat.

Sources: `docs/SCENARIO_MCP_CONTRACT.md`, `docs/LLM_DECISION_ARCHITECTURE_TZ.md:525-563`, `docs/JIVO_DIAGNOSTICS.md:73-78`.
