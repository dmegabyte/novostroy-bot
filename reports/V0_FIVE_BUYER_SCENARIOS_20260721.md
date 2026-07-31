# V0 production buyer matrix — 2026-07-21

## Scope

- Five isolated dialogues were sent through the live Jivo production webhook on
  the VPS, each with a fresh `site_id` / `chat_id` / `client_id` session.
- Every dialogue started with `/start_0`; the returned greeting explicitly
  confirmed `V0`.
- After every user turn the daily production error-event journal was checked.
- Evaluation separates transport/runtime success from the quality of the answer
  seen by a real buyer.
- Raw MCP request/response is not retained in the canonical Jivo journal, so this
  matrix can assess visible relevance, continuity and supported field traces, but
  cannot independently certify source grounding.

## Result

| Scenario | Runtime | Buyer verdict | Main evidence |
|---|---:|---:|---|
| Family: two-room, school and park, up to 18m | Pass | **Partial** | The first shortlist respected the budget but exposed only name, location and price; it did not show two-room, school or park evidence. Selecting option 2 recovered a useful grounded card with school, kindergarten, park, finishing and readiness. |
| Studio for rent, ready house, up to 16m | Pass | **Pass** | All three cards showed studio availability, readiness and budget fit. Selection stayed on `Бусиновский парк`; consent led to a contextual operator phone request for that ЖК and rental purpose. |
| Investment, ready house, up to 20m | Pass | **Fail** | State retained `delivered`, but all three visible options had delivery in 2027–2028 while the bot called them suitable. The direct follow-up asking for the cheapest option and why merely replayed the same shortlist. |
| One-room, no down payment, up to 15m | Pass | **Fail** | The answer used only room count and budget. Financing was lost (`active_topic=life`), no down-payment boundary was stated, and “проверьте по всем вариантам” replayed the shortlist instead of continuing the financing question or handing all options to an operator. |
| Named ЖК: price, finishing, readiness, then two-room inventory | Pass | **Fail** | The bot said it could see details for `Южные Сады` but displayed no price, finishing or readiness at all. The next turn correctly preserved the ЖК and offered an operator for live two-room inventory, but the original factual question remained unanswered. |

Buyer-quality distribution: **1 pass, 1 partial, 3 fail**.

Operational distribution after the CTA correction: **5/5 dialogues completed,
15/15 turns returned `BOT_MESSAGE`, and no new error event appeared**.

## Scenario evidence

### 1. Family — Partial

Buyer:

> Ищу двушку для семьи до 18 миллионов, хочется рядом школу и парк

The first response returned three budget-matched projects, but its field trace had
only `name`, `location` and `price_min` for each card. It therefore gave the buyer
no visible evidence for the requested two-room format, school or park.

After “Второй вариант расскажите подробнее”, the selected-object response for
`Лосиноостровский парк` included finishing, readiness, metro, developer, school,
kindergarten and park, then asked one relevant family-layout question. This
recovery makes the dialogue useful, but not a full pass because the first shortlist
was presented as suitable without showing the requested family criteria.

### 2. Rental — Pass

Buyer:

> Хочу студию под сдачу, бюджет 16 миллионов, желательно готовый дом

The three cards exposed `room_formats`, `ready` and `price_min`; the visible answer
showed studios, completed houses and prices within 16m. “Первый подробнее” stayed
on `Бусиновский парк`, and “Да, проверьте наличие” produced one clear operator
handoff while preserving both selected ЖК and rental context.

### 3. Investment — Fail

Buyer:

> Хочу вложить до 20 миллионов, важен готовый дом и чтобы потом было проще продать

The runtime stored the readiness requirement (`param_keys=[delivered,max_price]`),
but the response called projects due in 2027 and 2028 “подходящими”. That conflicts
with the buyer's explicit ready-house condition. The next question — “Какой из них
самый дешёвый и почему его стоит смотреть?” — was classified as
`current_options` and reproduced the first answer word for word instead of giving
a direct comparison.

### 4. Financing — Fail

Buyer:

> Ищу однокомнатную до 15 миллионов. Первоначального взноса пока нет — можно
> что-то подобрать без него?

The resulting state contained only `max_price` and `rooms`, with
`active_topic=life`. The response discussed three prices but did not acknowledge
the missing down payment, state that financing evidence was unavailable, or offer
a relevant live check. The follow-up “Проверьте тогда по всем вариантам” again
returned `current_options` and repeated the same shortlist.

This conflicts with the documented financing continuation contract in
`docs/IDEAL_IRINA_UX.md:106-115` and the direct-answer/operator routes in
`docs/BOT_ARCHITECTURE.md:261,629-630`.

### 5. Named ЖК — Fail

Buyer:

> Расскажите про ЖК Южные Сады: какие цены, есть ли отделка и дом уже сдан?

Visible answer:

> По Южные Сады сейчас вижу такие детали. Проверить актуальные квартиры в этом ЖК?

No details followed that sentence. The journal recorded `selected_object` with
`selected_present=true`, zero visible options and no field trace. On the next turn,
“А двухкомнатные сейчас есть?”, the bot correctly used `operator`, preserved
`Южные Сады` and asked for a phone number to check inventory, area, finishing and
exact price. The inventory boundary is safe, but it does not repair the empty
answer to the first project-level question.

This falls short of the selected-object response contract in
`docs/IDEAL_IRINA_UX.md:119-157`.

## Runtime defect found before the final matrix

The first family attempt exposed
`invalid_answer_output / answer_final_question_mismatch`: the answer model's
`final_question` could disagree with the runtime-owned CTA. The contract was fixed
so the runtime owns the final CTA completely, then deployed from backup
`backups/v0-runtime-owned-cta-20260721-184631`.

Post-fix evidence:

- focused V0 suite: **39 passed**;
- all five final buyer dialogues completed without new error events;
- latest production error remained `2026-07-21T18:43:48.048894+00:00`, before the
  CTA deployment; errors after the deployment timestamp: **0**;
- `novostroy-bot-api.service`: active;
- `novostroy-bot-n8n-bridge.service`: active;
- `/api/runtime-version`: `V0`.

## Product conclusion

V0 is operationally stable for this matrix, but it is not yet reliable as a
general buyer consultant. The strongest path is the ready-rental flow. The
remaining failures form three concrete behavior classes:

1. hard readiness requirements can survive in state but still be contradicted by
   visible options;
2. financing signals can disappear before planning and follow-up handling;
3. selected-object rendering can claim that details exist while publishing none.

A fourth, lower-severity issue is direct follow-up handling: comparison questions
can replay the shortlist instead of answering the requested comparison.

No behavioral fix for these four classes was made as part of this buyer-only
matrix.

## Post-audit remediation — 2026-07-21

The verdict above remains the historical result of the original five-dialogue
matrix. After the user approved remediation, all four behavior classes were fixed
and deployed:

1. readiness and room constraints now enter the typed search validator; future
   delivery cards cannot be presented as exact matches for a delivered-house
   request, and near-only results are labelled as alternatives;
2. zero-down-payment financing persists as the active topic, states the missing
   evidence boundary and supports check-all operator consent without replay;
3. a first-turn named ЖК can bootstrap one exact validated card, absent requested
   project facts are stated honestly, and current inventory questions route to
   the operator;
4. `comparison_metric=price_min` returns one directly identified cheapest current
   option while retaining the full shortlist in state.

Two additional live findings were resolved during the first-failure loop:

- building/corpus ranges such as `3.1–3.6` no longer trigger contact redaction;
- malformed scenario JSON receives one bounded retry; decision/search contract
  failures are still fail-closed and are not retried.

Verification:

- local affected gate: **65 passed**; deterministic harness: **8/8 scenarios**;
- remote V0 runtime gate after the final patch: **46 passed**; remote harness:
  **8/8 scenarios**;
- production ready-house, financing check-all, named-ЖК inventory and cheapest
  comparison flows all returned `BOT_MESSAGE` with no quality blockers;
- after the final bounded-retry deployment no new error event appeared; the last
  event remains the pre-fix malformed scenario output at
  `2026-07-21T19:35:37.888801+00:00`;
- API and Jivo bridge are active; protected selector reports `V0`.

Production evidence artifacts:

- `/tmp/v0_fix_ready.json`
- `/tmp/v0_fix_financing.json`
- `/tmp/v0_fix_named3.json`
- `/tmp/v0_fix_cheapest2.json`

Backups:

- `backups/v0-buyer-contracts-20260721-191852`
- `backups/v0-named-inventory-hotfix-20260721-192857`
- `backups/v0-building-number-redaction-20260721-193340`
- `backups/v0-scenario-json-retry-20260721-193921`

## Evidence files

- `/tmp/v0_buyer_family_rerun.json` on VPS
- `/tmp/v0_buyer_rental.json` on VPS
- `/tmp/v0_buyer_invest.json` on VPS
- `/tmp/v0_buyer_mortgage.json` on VPS
- `/tmp/v0_buyer_named.json` on VPS
- `/tmp/v0_five_scenarios_journal.json` on VPS
- production `logs/dialogue_journal.jsonl`
- production `logs/bot_error_events-2026-07-21.jsonl`

Formal Google Sheet/MCP-quality publication remains blocked by the evidence rule
in `docs/LLM_SCENARIO_EVAL_RUBRIC.md:790-837`: the canonical Jivo journal does not
contain raw MCP request/response, and none was reconstructed.
