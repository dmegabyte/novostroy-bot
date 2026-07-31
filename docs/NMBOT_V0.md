# NMBot V0 — two-prompt Jivo runtime

NMBot V0 is an independent opt-in two-prompt runtime for Jivo. It is not a step inside the V2 pipeline and must be released, tested and reasoned about through its own contract.

Version separation is canonicalized in `docs/NMBOT_RUNTIME_VERSIONS.md`. Docs do not declare the currently active production runtime: check the live selector before making that claim.

V0 quality reference: `docs/NMBOT_V0_QUALITY_BASELINE.md`.

Client-facing name: **Валерия**. Оно задано в V0 answer prompt и Jivo runtime
identity/greeting; технический идентификатор `V0` не меняется. Scenario/search
prompt клиенту не отвечает.

## Current status

- Runtime: `nmbot_v0/runtime.py`.
- Contracts/state: `nmbot_v0/contracts.py`.
- V0-owned field contract: `nmbot_v0/field_contract.py`.
- V0-owned MCP/card normalization: `nmbot_v0/card_normalizer.py`.
- V0-owned deterministic presentation: `nmbot_v0/presentation.py`.
- Deterministic local harness: `scripts/nmbot_v0_test_harness.py`.
- Focused tests: `tests/test_nmbot_v0_runtime.py` and `tests/test_nmbot_v0_test_harness.py`.
- Jivo runtime selector and real gateway ports: `scripts/nmbot_runtime_adapter.py`.
- Protected selector API and Jivo `/start` greeting: `scripts/nmbot_api_server.py`.

The V0 scenario/search port receives MCP access; the answer layer does not. Scenario/search is loaded from `prompts/v0_scenario_search.txt`. Client wording is produced by the V0 Answer Writer prompt `prompts/v0_answer_writer.txt` when enabled; the deterministic runtime answer remains the fallback.

## Switching V0 and V2

- The global active version is persisted in `data/nmbot_runtime_version.json`; when the file is absent the selector fallback may be `V2`, but that fallback is not proof of the live active version.
- The current process version must be checked through protected `GET /api/runtime-version`. During restart or controlled switching, the live endpoint wins for the running process.
- The protected endpoints are `GET /api/runtime-version` and `POST /api/runtime-version`.
- They require the existing `NMBOT_API_TOKEN` authentication. A Jivo client message cannot change the version.
- `V0` and `V2` state are stored separately under `nmbot_v0` and `nmbot_v2`. A normal turn, API reset or Jivo `/start` resets only the active namespace, preserving the other version's dialog for a later switch-back.
- For a Jivo `/start`, the exact standard welcome is retained and gets one final dynamic line: `Сейчас активна версия: V2.` or `Сейчас активна версия: V0.`
- `/start_0` selects V0 only for the current Jivo session and resets its `nmbot_v0` namespace; `/start_2` does the same for V2. Neither command changes the global selector or another chat.

The Jivo endpoint still returns `BOT_MESSAGE`; the session key remains `jivo:<site_id>:<chat_id>:<client_id>`.

## Two injected ports

`V0TurnProcessor` accepts exactly two ports:

1. `scenario_search(context)` — decides the scenario and, for search turns, returns MCP-shaped search data.
2. `answer(brief)` — receives only the validated brief and writes the client-facing answer. The runtime builds canonical answer material first; the optional V0 Answer Writer turns that material into plain Russian text.

The first port owns scenario/search. The second port must not search or call MCP.

## Validation and limits

- Search output is validated at the V0 contract boundary before it reaches the answer prompt. V0 may use generic immutable V2 data contracts and generic strict search validation utilities, but V0 presentation field selection, card normalization and client-visible card rendering are owned by `nmbot_v0/`.
- The answer prompt receives only allowed, validated cards.
- The visible shortlist is capped at three cards.
- Canonical answer material, selected object, action, state and operator routing remain code-owned. The Answer Writer receives `client_message`, bounded `previous_assistant_message`, `response_job` and validated `material`; it returns plain text only. Empty, overlong, provider-error or unavailable-writer output falls back to the deterministic answer.
- Missing live facts must be handled honestly with the operator phone request:
  `Оставите номер телефона, чтобы оператор проверил это и связался с вами?`
- The runtime keeps `answer_kind`, `scope`, CTA and operator routing code-owned. The enabled Answer Writer returns only plain text and cannot change those decisions.
- V0 state remembers whether the dialogue is continuing, the last answer kind,
  the last assistant question, the previous successfully published assistant
  message (bounded to 2,000 characters) and already presented fact names. On
  the next turn prompt 1 receives that message with the current `user_text` and
  structured state only to understand references and short continuations.
  Selected option, visible options, active topic and pending action remain
  authoritative structured fields; the previous message is not MCP evidence.
  Older stored V0 state remains compatible; V0 and V2 namespaces still coexist.
- Client cards are rendered deterministically from typed, allowlisted
  `OptionCard` fields. Free-form descriptions and model recommendations do not
  enter the client message. Exact integer prices are preserved without
  rounding.
- The Answer Writer receives validated material rather than raw MCP output. Its
  plain-text candidate may be published only as wording; the deterministic
  material remains the fallback and the runtime continues to own cards, facts,
  prices, CTA and routing.
- The first scenario/search prompt receives an ordered V0 field contract rather
  than an alphabetical slice of the broad V2 allowlist. Identity and price come
  first, followed by location, rooms/area, finishing/readiness, metro/developer
  and family/life infrastructure fields. Missing optional fields do not reject a
  safe card; all useful fields actually returned by MCP should be preserved.
- Successful search turns expose a privacy-safe field-boundary trace containing
  only allowlisted field names before and after normalization. The trace never
  stores values, project names, user text, contacts, raw payloads or secrets.
- Client-visible deterministic V0 prose must not mention internal `карточки`,
  `проверенные/непроверенные данные`, saved-data containers, bot configuration or
  dialogue state. It speaks about suitable options, confirmed characteristics
  and retries in ordinary customer language.
- V0 `runtime_summary` records real `scenario_search` and `answer` call counts,
  derived gateway/search counts, safe state before/after, actual question count,
  final-question position and bounded quality blockers. State diagnostics expose
  only parameter keys, option count, selected/pending flags and active topic.
- A selected-object follow-up never greets again and never asks the client to
  select an option that is already selected. If the saved card contains only
  one useful fact, the response honestly marks the remaining characteristics
  as unconfirmed.
- V0 uses its own `nmbot_v0/presentation.py` grounded-card renderer. Shortlist
  cards are numbered, include a rich typed headline and receive at most one
  distinct benefit derived only from present `OptionCard` facts and the active
  viewpoint. V0 does not import `nmbot_v2.response` or V2 private response
  helpers.
- For family cards V0 keeps the same grounded `family → life` fallback principle
  inside its own presentation module, so cards with the same readiness fact still
  receive distinct, non-invented angles such as readiness and location rather
  than repeating one phrase.
- Readiness wire values `ready=true/1` are normalized to `сдан`; numeric
  `state/status` enums remain hidden. ISO year-month readiness is rendered in
  natural Russian, and property-class enums such as `comfort` are never shown as
  developer names.
- V0 keeps its own accumulated budget and adds an explicit deterministic budget
  boundary to the rendered shortlist. A card above the stored maximum is marked
  as such and is never described as fitting the budget.
- For a shortlist of two or three options, V0 derives a comparison context only
  from the normalized `OptionCard` tuple. Identical confirmed values and common
  infrastructure labels are stated once before the cards and are suppressed from
  individual headlines, so a shared school, readiness, location or price is not
  presented as a unique advantage.
- A card-specific grounded non-price fact keeps priority, for example an exclusive
  school, finishing, readiness or park. If no such fact remains, V0 assigns an
  honest deterministic comparison role from confirmed `price_min`: lowest entry,
  middle price with the exact nearest ruble delta, or highest price with a neutral
  location condition. Equal-price options do not get an invented winner and are
  explicitly described as having no separate confirmed advantage.
- Literal investment counters remain reference facts only. The comparison fallback
  does not turn `sales_count` or `ads_count` into demand, liquidity, income or
  future-value claims.
- Selected-object presentation uses one grounded acknowledgement, one rich card
  and one topic-specific action CTA, for example `Проверить доступные квартиры
  для сдачи именно в этом ЖК?`; it does not restart the dialogue or combine
  multiple actions in one question.
- Stateful selected follow-ups use one bounded pending action. After a selected
  rental/family/default card, V0 stores `pending_action`, `pending_subject` and
  the topic, then asks one action-specific question rather than an `A or B` CTA.
- Prompt 1 semantically classifies short replies, including typos, as
  `accept`, `decline`, `new_question` or `unclear`; runtime does not use a
  keyword/regex consent router. An acceptance is valid only when the confirmed
  action and exact selected subject match the stored pending state.
- Valid acceptance of `check_selected_availability` never replays the shortlist.
  It preserves the selected ЖК/topic and asks for a phone so an operator can
  check current inventory, areas, finishing and exact price. Decline clears the
  pending action without requesting a phone.
- On a selected-object turn prompt 1 may perform one bounded MCP enrichment for
  the exact canonical selected name. Runtime rejects multiple, near or
  mismatching cards and merges only non-empty validated fields into that one
  visible option.
- Broad-search decisions now feed canonical typed constraints into the search
  validator. The shared V0/V2/V3 search validator is report-only: it records
  missing evidence and hard mismatches but does not hide an identifiable MCP
  card. `delivered` is normalized to `ready=delivered`; room count, readiness,
  finishing, area and geography remain effective hard constraints and
  diagnostics.
  Price limits require price evidence but remain a presentation boundary so V0
  can still show and explicitly label a useful option above budget. If only
  `near` cards survive, the response calls them alternatives instead of exact
  suitable matches.
- Financing is a sticky typed topic. A zero `down_payment` remains valid state,
  V0 does not claim that ordinary price-matched cards support purchase without a
  first payment, and the bounded pending action
  `check_current_options_financing` lets a short acceptance hand all current ЖК
  to the operator without replaying the shortlist.
- A first-turn named-ЖК request may bootstrap one exact validated card even when
  no shortlist exists yet. Requested but absent price, finishing or readiness is
  stated explicitly. Live inventory/booking questions after selection route to
  the operator instead of replaying project-level facts.
- `current_options` with typed `comparison_metric=price_min` renders only the
  lowest confirmed current option while preserving the full shortlist in state.
  Runtime does not infer this operation through a user-text regex.
- A malformed scenario/search JSON response receives exactly one retry with the
  same safe turn data plus a bounded `format_recovery` marker. The malformed
  provider output is never copied into that retry context. Invalid decisions are
  not retried. Search business-contract violations are report-only and continue
  with sanitized identifiable cards. If both parse attempts fail, V0 does not
  call the answer model, does not mutate or save state, and asks the client to
  try the search again without requesting a phone number or operator handoff.
- Any operator-phone response leaves `pending_action=contact_phone`, which is the
  existing code-level callback-capture contract. A decline does not enter phone
  capture.
- Phone-only callback capture may use a safe non-test Jivo profile name when Jivo
  provides `sender.name`; the API passes it as `meta.sender_name`, and the name is
  never derived from phone digits. If the profile name is absent, synthetic/test
  metadata or otherwise fails the name guard, V0 keeps the phone in the private
  callback draft and asks the client how to address them.
- Gateway-context contact redaction requires 10–15 actual digits. It still hides
  phone numbers but no longer destroys project facts such as корпус ranges
  `2.1–2.4` and `3.1–3.6`.

This matches the UX rule from `docs/IDEAL_IRINA_UX.md`: use only MCP facts, show no more than three choices, ask one final question, and do not send the client to the site, developer or office when a live fact is unknown.

### V0 Answer Writer and persona

- The client-facing V0 identity is Валерия: a warm female real-estate consultant. The writer may make wording natural, but must not change facts, selected ЖК, topic, action, operator routing or state.
- TEST writer contract: `google/gemini-3.6-flash` through the gateway, temperature `0.4`, `max_tokens=2000`. A model override must not replace the pinned gateway model.
- The writer returns plain Russian text, not JSON. It answers the client's current meaning first, avoids replaying the shortlist and asks at most one useful question. Harmless off-topic replies may be brief and warm, then return to the preserved real-estate context.
- A vague subjective concern about the current shortlist is an `open_question`/`answer_directly` route, not an operator or phone route. Concrete live-fact requests may still use the operator flow.

### V0 phone funnel

- An operator offer is not yet phone capture. After the client agrees to the operator check, V0 keeps `pending_action=contact_phone` and the first-level code path asks for the actual phone digits.
- Exact and semantic positive consent use the same code-owned message: `Да, всё верно — пришлите, пожалуйста, сам номер телефона цифрами, например +7 999 123-45-67.` The public contract is `intent=collect_contact_phone`, `awaiting_phone=true`, `handoff_to_operator=false`.
- Repeated positive consent repeats the digits request rather than returning to the generic operator offer. A valid phone is the only input that queues the callback and clears the pending phone step. Decline, a meaningful new question or off-topic message goes back to semantic routing while preserving selected ЖК, topic and visible options.
- The exact public phone request is stored as `previous_assistant_message`; no writer/provider call is made for this code-owned capture step.

## V2 restoration boundary

The earlier V0 work accidentally placed V0-only presentation/search-field and
normalization logic into V2 source files. The boundary is corrected with a
compatible rollback: current generic V2 APIs and later V2 behavior stay intact,
while only proven V0-owned additions are removed from V2. The gate checks the
absence of V0 presentation wrappers, V0 readiness/date/developer normalization
and `V0_PRESENTATION_*` field selectors instead of requiring obsolete whole-file
hashes.

V0-specific readiness/developer/date/presentation coverage now lives in V0 tests,
not in V2 regression contracts.

## Local commands

Readable transcript:

```bash
python3 scripts/nmbot_v0_test_harness.py --scenario all
```

Machine-readable output:

```bash
python3 scripts/nmbot_v0_test_harness.py --scenario all --json
```

Single fixtures:

```bash
python3 scripts/nmbot_v0_test_harness.py --scenario successful_flow
python3 scripts/nmbot_v0_test_harness.py --scenario missing_fact
python3 scripts/nmbot_v0_test_harness.py --scenario unknown_card
python3 scripts/nmbot_v0_test_harness.py --scenario ready_near_only
python3 scripts/nmbot_v0_test_harness.py --scenario financing_check_all
python3 scripts/nmbot_v0_test_harness.py --scenario named_first_turn_exact
python3 scripts/nmbot_v0_test_harness.py --scenario current_options_cheapest
```

Focused verification:

```bash
PYTHONPATH=. pytest tests/test_nmbot_v0_runtime.py tests/test_nmbot_v0_test_harness.py
python3 -m compileall nmbot_v0 scripts/nmbot_v0_test_harness.py tests/test_nmbot_v0_test_harness.py
```

## Deployment rule

The deterministic harness remains network-free, secret-free, repeatable and cost-free. The runtime selector affects Jivo, so every deployment requires the Jivo VPS gate: sync the V0 files and both prompt files, compile remotely, restart only the changed API unit, inspect the first Jivo trace, then run a minimal live smoke. Restore the originally active version after an isolated V0 smoke unless the task explicitly says to leave V0 active.

## V0 release gate

V0 has its own gate and must never be used as V2 proof:

```bash
PYTHONPATH=. pytest tests/test_nmbot_v0_runtime.py tests/test_nmbot_v0_test_harness.py
python3 -m compileall nmbot_v0 scripts/nmbot_v0_test_harness.py tests/test_nmbot_v0_test_harness.py
```

For V2 release work, deselect V0 explicitly, for example `PYTHONPATH=. pytest -k 'not v0'`.
