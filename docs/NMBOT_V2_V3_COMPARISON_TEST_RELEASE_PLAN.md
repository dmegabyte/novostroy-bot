# NMBot V2/V3 — план реализации, проверки и TEST-релиза сравнения

Статус: **implementation/release plan only**. Это не отчёт о готовом релизе, не deploy receipt и не доказательство live-поведения.

Дата: 2026-07-30.

## A. Цель, scope и границы

Цель — довести локальный V2/V3-пакет до безопасного TEST-сценария сравнения видимых ЖК: обычное «сравни их» сравнивает весь видимый shortlist, а явное «A с B» сравнивает ровно пару, без подмены третьим вариантом и без выдуманных фактов.

Границы:

- Только **TEST**. Production, client-production, bridge, V0, V1, V4, модели, provider config, `.env`, CRM и eval находятся вне scope.
- V3 владеет `IntentPlanV3`, переходом и writer projection, но намеренно переиспользует V2 engine/state/cards/search/deterministic fallback. Это прямо зафиксировано в version boundary map: `docs/NMBOT_VERSION_BOUNDARY_MAP.md:20-26`.
- Локальные тесты, локальные документы и старые логи не дают права говорить «в TEST/production уже работает». Live/release claim возможен только после свежего TEST snapshot, reviewed artifact, deploy approval и correlated Jivo/API evidence; runbook отдельно запрещает подменять live proof локальным preflight: `docs/NMBOT_RUNBOOK.md:120-136,157-175,198-244`.
- UX-цель остаётся Ириной-консультантом: до трёх вариантов, подтверждённые факты, честные missing facts и один следующий вопрос (`docs/IDEAL_IRINA_UX.md:25-45`). Выбранный объект должен обслуживаться из структурированной памяти/точечного enrichment, а не широким новым поиском (`docs/IDEAL_IRINA_UX.md:133-163`).

## B. Что уже реализовано локально, но ещё не является единым reviewed/deployed пакетом

Ниже — локальная картина по текущему V2/V3-пакету. Эти пункты **не означают**, что пакет уже был reviewed как единая цепочка или deployed.

### Уже есть в локальном коде/проверках

- Selected enrichment поддерживает `lot_hard`: сейчас source-proven поле — `rooms`. Exact request остаётся `count=1`, strict JSON, identity validation, сохранение `id`/`status` у лотов и отказ от broad/near promotion. Якоря: `nmbot_v2/search_enrichment.py:38-189`, lessons `docs/NMBOT_ENGINEERING_LESSONS.md:252-332`.
- Current source-proven room evidence относится к `LotExample`, а не к complex-level `OptionCard`; поэтому lot constraints должны валидироваться по lot axis, не ломая broad-search constraints. Якоря: `docs/NMBOT_ENGINEERING_LESSONS.md:288-332`, funnel `docs/NMBOT_SELECTED_ZHK_LOT_FUNNEL.md:21-55`.
- Cache key для selected enriched card должен учитывать `facts_needed` и `lot_hard`, чтобы не переиспользовать карточку, отфильтрованную под другой lot-scope. Это следует из prevention checklist: `docs/NMBOT_ENGINEERING_LESSONS.md:310-319`.
- Exact repair bounded: повтор делается только для parse/contract, сохраняет тот же ЖК, те же requested facts и hard/lot_hard; timeout/provider/empty/identity mismatch не превращаются в blind retry. Якорь: `nmbot_v2/search_enrichment.py:84-145,148-189`.
- Generic technical fallback заменён на существующую operator consent/contact flow, без слепого top-level retry над уже существующими provider/main-search recovery. Граница описана в lessons: `docs/NMBOT_ENGINEERING_LESSONS.md:303-309`.
- V3 `intent_transition` trace сделан privacy-safe: можно видеть goal/status/allowlisted validation codes/fallback, но нельзя писать raw plan, raw query, имена, constraints, contacts. Якоря: `docs/NMBOT_ENGINEERING_LESSONS.md:372-402`.
- Историческая compare-регрессия: `compare_current + named_object_reference` раньше отклонялась до MCP как `invalid_named_reference_scope`. Локальная временная нормализация теперь принимает named reference только если он точно видим, очищает object fields и оставляет `scope=all`; external name остаётся rejected. Якоря: `nmbot_v2/semantic_planner.py:154-184`, `docs/NMBOT_ENGINEERING_LESSONS.md:334-370`.
- D.4 pair-specific executor реализован локально отдельным owner-модулем `nmbot_v2/pair_comparison.py`, без подключения к runtime/adapter/presenter/API/Jivo. Он доказывает arbitrary pair selection, cache matrix, bounded exact `count=1` fetch и safe metadata, но **не доказывает** presenter input или клиентский pair answer.

### Известная локальная verification-картина

Счётчики ниже могут пересекаться между наборами и не являются one full-suite total:

- lot/recovery/safe trace suites из prior session summary: 158 profile, 36 recovery/operator/lot, 36 trace;
- latest compare focused: 27 passed;
- enrichment file: 25 passed;
- V3 compare regression: 26 passed;
- `py_compile` passed;
- docs check: 21 PASS / 0 FAIL, при существующем независимом WARN по `state_version_cas`.

Эта локальная evidence полезна как starting point, но не заменяет fresh TEST source snapshot, exact candidate inventory, review и первый correlated TEST Jivo/API proof.

## C. Product decision и target UX

Семантическая политика:

- «сравни их», «сравни варианты», «что лучше из этих» = сравнить весь видимый shortlist. Это сохраняет текущий current-options contract и не придумывает пару из общего запроса.
- Явное «A с B» или выбранный A + «сравни с B» = сравнить ровно два canonical visible options, если B видим.
- External B, которого нет среди visible options, нельзя молча заменять ближайшим видимым. Нужно либо bounded exact lookup по замороженному контракту, либо clarification. В обоих случаях silent substitution запрещён.

Pair answer:

- сравнивать только confirmed same-axis fields: цена с ценой, локация с локацией, отделка с отделкой, готовность с готовностью, lot facts только если оба/один вариант действительно enriched;
- не выбирать «лучший» безусловно;
- задать ровно один следующий вопрос;
- явно сказать, какие факты отсутствуют;
- не добавлять lot/ads comparison по умолчанию, если пользователь не просил lot-level comparison. Lot funnel разрешает lot comparison только после selected lot evidence и без destructive mutation: `docs/NMBOT_SELECTED_ZHK_LOT_FUNNEL.md:21-55`.

## D. План реализации и impact chain

### 1. Freeze additive pair contract — implemented locally for D.1

Сначала заморозить контракт, потом писать код.

Локально реализовано только contract/transition groundwork: `comparison_option_names: tuple[str, str] = ()` добавлено в `IntentPlanV3` и `ExecutableTurn`, не добавлено в `SemanticPlan`. Future executor остаётся владельцем projection. Не перегружать `selected_enriched`: это cache/enriched card, а не pair intent. Текущие typed anchors: `nmbot_v2/contracts.py:94-174,331-368`.

Инварианты pair contract:

- пустой список означает, что pair intent не задан; непустое поле содержит ровно два имени;
- оба distinct после per-item whitespace trim;
- для current pair оба должны быть visible members;
- порядок стабильный: сначала выбранный/упомянутый A, затем B; для «A с B» — порядок фразы;
- никаких contacts/raw user text/raw plan/model payload;
- external name не превращается в visible pair без exact lookup/clarification по замороженному правилу.

### 2. Prompt/schema/parser — только после frozen contract

Обновлять V3 prompt/schema/parser можно только после freeze. Это будущая implementation session, и prompt edits должны идти через `prompt-quality-guardian` / PromptMaster, а не через ad-hoc правку. В этой документационной задаче prompt files не меняются.

### 3. Planner/validator transition — implemented locally for D.3 groundwork

- `compare_current` без pair fields остаётся generic-all.
- `comparison_option_names` разрешён только для `compare_current`; для других goal возвращается `invalid_comparison_options_scope`.
- оба exact names должны находиться через `state.find_visible_option`; иначе safe validation code `comparison_option_not_visible` без имён в trace.
- pair field конфликтует с legacy `selected_option_name` или `named_object_reference` и возвращает `comparison_option_fields_conflict`; runtime не выбирает молча одну из двух репрезентаций.
- legacy `selected_option_name + named visible reference` без pair field намеренно оставлен временным workaround: object fields очищаются, `scope=all` сохраняется.

Текущий temporary normalization anchor: `nmbot_v2/semantic_planner.py:154-184`.

Локальная evidence для D.1/D.3: `PYTHONPATH=. python3 -m pytest tests/test_intent_plan_v3_validation.py tests/test_intent_plan_v3_transition.py tests/test_followup_canonical_contract.py -q` → `83 passed in 0.32s`; `py_compile` changed V3 modules passed. Это local contract proof, не TEST/VPS/deploy claim.

### 4. Pair-specific executor — implemented locally for D.4 owner only

Executor должен выбирать arbitrary pair by canonical names, а не «первые две» карточки.

Алгоритм:

1. взять visible options из state/search по текущему V2 owner contract (`SearchResult.shortlist()` и state visible options: `nmbot_v2/contracts.py:407-441,476-485`);
2. найти обе canonical names;
3. проверить cache sufficiency отдельно для каждой карточки с учётом bounded `facts_needed`; `lot_hard` для pair executor ещё вне D.4 scope;
4. сделать zero/one/two parallel exact enrichment calls;
5. каждый call: bounded exact `count=1`, identity validation, strict JSON, no broad/near promotion, reuse existing exact repair only for parse/contract;
6. не делать blind top-level retry.

Локальная реализация: `nmbot_v2/pair_comparison.py`.

Проверенная D.4 evidence:

- `PYTHONPATH=. python3 -m pytest tests/test_nmbot_v2_pair_comparison.py -q` → `8 passed in 0.17s`;
- покрыто: missing/invalid/non-visible pair fail-closed без gateway calls; arbitrary first+third among three visible cards; zero/one/two cache hits; concurrent two exact requests with `count=1`; two cache additions without state mutation; one/both enrichment failure keep grounded base cards; safe metadata/attempts without names/raw payload; no `lot_examples` requested by default.

Граница D.4 была снята локальной D.5 integration ниже; сам executor по-прежнему не владеет response/presenter policy, API/Jivo, prompt/schema, TEST/VPS/deploy/config/env/eval.

### 5. ExecutionResult/ResponsePlan/Presenter chain — implemented locally for D.5

Нужно добавить `ExecutionResult`/`ResponsePlan` comparison cards или минимальный эквивалент, чтобы composer получил ровно pair. Сейчас composer берёт `response_plan.cards[:3]`, fallback к `execution.search.shortlist(3)`, затем selected/state visible options (`nmbot_v2/response_composer.py:201-265`). Для pair это опасно: третья карточка может вернуться через обычный fallback.

Требования:

- presenter получает normalized safe `OptionCard` objects, never raw MCP response;
- third visible card исключён;
- state не мутируется разрушительно: generic visible shortlist остаётся для дальнейшего диалога;
- selected/enriched cache обновляется только по своему contract, а pair output — отдельная per-turn проекция;
- safe trace пишет aggregated counts/status only, без names/raw payload.

Локальная D.5 реализация добавляет additive per-turn `comparison_cards`, cache additions и safe aggregate metadata в `ExecutionResult`. Runtime вызывает pair executor только при compiled V3 pair; generic compare без pair сохраняет прежний путь. `ResponsePlan` и composer priority берут ровно две pair cards, не заменяя `visible_options`/`selected_enriched` и не позволяя третьей карточке вернуться fallback-ом.

Проверенная local evidence:

- `PYTHONPATH=. python3 -m pytest tests/test_nmbot_v2_pair_projection.py tests/test_nmbot_v2_pair_comparison.py tests/test_nmbot_v2_runtime.py tests/test_nmbot_runtime_adapter.py -q` → `293 passed in 3.82s`;
- `PYTHONPATH=. python3 -m pytest tests/test_nmbot_v2_contracts.py tests/test_nmbot_v2_pair_comparison.py tests/test_nmbot_v2_pair_projection.py tests/test_intent_plan_v3_validation.py tests/test_intent_plan_v3_transition.py tests/test_followup_canonical_contract.py -q` → `171 passed in 0.55s`;
- `PYTHONPATH=. python3 -m pytest tests/test_nmbot_api_jivo_p1.py -q` → `73 passed in 1.83s`.
- После full integration audit закрыты два trace gaps: adapter allowlist сохраняет
  четыре safe pair validation categories, а public `runtime_summary` публикует
  только bounded aggregate `pair_comparison` status/counts — без имён, query,
  raw payload или contacts. Focused sanitizer → `4 passed`; pair/runtime/adapter
  batch → `177 passed`; `py_compile scripts/nmbot_runtime_adapter.py` passed.
  Focused re-review → PASS, critical/high findings отсутствуют.

Это local integration evidence only: planner prompt ещё не выдаёт pair field из естественного текста, и TEST/VPS не проверялись.

Локальная prompt/schema wiring завершена после D.5: V3 JSON schema требует
`comparison_option_names`; prompt distinguishes generic-all, explicit visible
A/B and selected A + visible B. Deterministic mocks cover the route to the
two-card response, но live model behavior по естественной фразе всё ещё требует
отдельного TEST smoke.

### 6. Failure matrix

| Случай | Поведение |
|---|---|
| Один enrichment failed | Использовать grounded cached/base card для этой стороны, явно отметить missing facts. |
| Оба enrichment technical failed | D.5 сохраняет две grounded base cards и честную caveat без blind retry; pair-specific operator handoff остаётся отдельным product decision. |
| Identity mismatch | Не публиковать enriched card; clarification или base-card-only answer с честным missing. |
| Ambiguous duplicate names | Clarification. |
| Missing external name | Bounded exact lookup или clarification по frozen contract. |
| External name похож на visible | Silent substitution запрещён. |
| Lot facts отсутствуют | Не сравнивать лоты; сказать, что по лотам данных нет/не запрошены. |

### 7. Docs/lessons after implementation

После кода обновить lessons/runbook/status только по факту пройденных проверок. Не закрывать задачи без user confirmation.

## E. Test matrix и gates

### Test matrix

- Contract tests для `comparison_option_names`: shape, exactly two, distinct, stable order, no unknown fields.
- Transition tests: selected A + visible B → pair; generic «сравни их» → all; external B → lookup/clarify; duplicate/ambiguous → clarify/fail closed.
- Arbitrary-pair regression: three visible options, selected first + third; output содержит first+third и исключает second.
- Generic-all regression: «сравни их» по трём visible options остаётся all-visible shortlist.
- External reference regression: name outside visible не accepted pair и не substituted.
- Zero/one/two enrichment calls based on cache sufficiency.
- Strict exact requests: `count=1`, exact canonical name, bounded facts, same `lot_hard`, identity validation.
- Third-card exclusion in `ExecutionResult`/`ResponsePlan`/composer brief.
- One/two technical failure behavior.
- Missing fields: same-axis comparison only, explicit missing facts.
- No lots/ads by default unless lot-level comparison requested.
- Privacy trace: no names/raw plan/query/constraints/contacts.
- State immutability: visible shortlist preserved; no destructive mutation.
- API/Jivo adapter integration: version marker, safe journal, terminal `BOT_MESSAGE` in TEST after deploy approval.

### Local sequence

1. Focused first: contract/parser/transition pair tests.
2. Owner gates: V3 transition + V2 executor/enrichment/composer tests.
3. Broad adapter/API tests after owner gates pass.
4. `py_compile` for changed Python files.
5. `python3 scripts/nmbot_check.py docs` after docs updates.
6. No promptfoo/eval.

First-Failure rule: если первый focused check падает, остановиться, классифицировать слой (`contract`, `planner`, `executor`, `composer`, `adapter`, `external/unknown`) и не запускать широкий batch вслепую.

### Stop/go acceptance checklist before review

- [x] Pair contract frozen and documented locally for `IntentPlanV3`/`ExecutableTurn` only.
- [ ] Generic-all compare still works.
- [x] Arbitrary selected+third pair validates/compiles locally as typed data; executor/presenter behavior is future scope.
- [x] Third card excluded from local pair `ResponsePlan` and composer brief.
- [x] Non-visible pair names fail closed at V3 validation/transition trace; exact lookup/clarification is future scope.
- [x] Local pair enrichment calls are exact `count=1`, bounded and identity-checked.
- [x] Local pair validation/cache/enrichment failure matrix is covered; pair-specific operator handoff remains deferred.
- [x] Trace privacy covered for new validation categories.
- [x] Local pair state immutability/cache-addition behavior is covered.
- [x] Pair validation categories and aggregate pair trace are safely observable
  through the public runtime summary without user/object payload leakage.
- [x] Docs/lessons updated with local D.1–D.5 evidence.
- [ ] No eval/provider config/env/CRM/production/client-production/bridge change.
- [ ] User confirms before marking complete or releasing to TEST.

## F. Review и immutable TEST rollout

### Candidate inventory

В этом shadow workspace нет `.git`, поэтому нельзя полагаться на `git diff` для dirty-tree separation. Перед rollout нужно реконструировать exact candidate inventory against fresh TEST VPS source snapshot: `docs/NMBOT_RUNBOOK.md:198-220`.

В отчёте по implementation нужно отдельно показать только файлы текущей задачи, не смешивая их с pre-existing dirty tree.

### Review

После реализации — обычное ревью или, предпочтительно, full integration audit через read-only `code-reviewer`. В review packet передать цель, критерий готовности, exact files, fresh source snapshot/diff fingerprint и уже выполненные проверки. Reviewer ничего не исправляет.

### Local preflight

Перед любым TEST write:

- focused/owner/broad local gates;
- `py_compile`;
- docs check;
- exact overlay list;
- no prompt/config/env/model/provider drift;
- explicit user approval.

### Immutable TEST release route

Использовать только atomic tooling:

```bash
python3 scripts/nmbot_atomic_release.py test-release \
  --release-id REL-ID \
  --overlay <reviewed-runtime-file-1> \
  --overlay <reviewed-runtime-file-2> \
  --out-dir /tmp/opencode/nmbot-test-release-REL-ID \
  --confirm
```

Правила:

- fresh TEST source snapshot;
- isolated worktree;
- exact overlays only for reviewed runtime files;
- fail-closed exact diff;
- immutable release ID/artifact/preflight/deploy/recon;
- docs/tests обычно не runtime overlays, если artifact contract явно не требует их включения;
- точный overlay list составляется только после fresh compare;
- API only; bridge не перезапускать, если evidence не докажет bridge change need;
- никакие manual VPS edits.

Runbook anchors: `docs/NMBOT_RUNBOOK.md:198-244`.

### Post-deploy TEST proof

После deploy:

1. health/identity/runtime V3 proof;
2. ровно один historical synthetic TEST Jivo scenario: selected Level Lesnoy → compare with Tomilinsky Boulevard;
3. использовать unified test runner или approved exact route (`docs/NMBOT_RUNBOOK.md:226-244`);
4. сразу inspect correlated safe trace;
5. остановиться на первой ошибке;
6. требовать terminal `BOT_MESSAGE`;
7. только после первого green добавить generic «сравни их» и partial enrichment failure scenarios.

### Rollback

Критерии rollback:

- нет terminal `BOT_MESSAGE`;
- runtime identity не тот;
- pair presenter включает третью карточку;
- external name silently substituted;
- trace leaking names/raw payload/contacts;
- API health degraded;
- first correlated trace показывает contract/executor/composer failure.

Rollback — только previous immutable release through atomic tooling. Никаких ручных VPS edits.

### Final release receipt fields

- release_id;
- source snapshot id + manifest SHA;
- exact overlay list;
- artifact path/hash;
- local checks with exact commands/results;
- review verdict/session;
- deploy command/result;
- health/identity/runtime proof;
- first Jivo scenario input route and safe trace ref;
- terminal outcome (`BOT_MESSAGE` required);
- known limitations;
- rollback target.

## G. Status table и recommended execution order

### Concrete status table

| Item | Status | Evidence / boundary |
|---|---|---|
| V3 owns IntentPlanV3/writer projection while reusing V2 state/cards/search/fallback | Done | Boundary map `docs/NMBOT_VERSION_BOUNDARY_MAP.md:20-26`. |
| Selected exact enrichment with `lot_hard`, strict JSON, identity/status preservation | Done locally | `nmbot_v2/search_enrichment.py:38-189`; lessons `252-332`. Not release proof. |
| Bounded exact repair parse/contract only | Done locally | `nmbot_v2/search_enrichment.py:84-145`. |
| Operator fallback instead of blind top-level retry | Done locally / needs integrated proof | Lessons `docs/NMBOT_ENGINEERING_LESSONS.md:303-309`. |
| Safe V3 intent transition trace | Done locally | Lessons `372-402`. |
| Temporary compare named-reference visible normalization | Done locally / temporary | `nmbot_v2/semantic_planner.py:154-184`; lessons `334-370`. |
| Pair feasibility through two exact enrichments | TEST proven | Final live pair requested two exact enrichments; one succeeded and one failed while both grounded cards remained visible. |
| Additive pair contract | Done | Frozen on `IntentPlanV3`/`ExecutableTurn`; generic compare keeps an empty pair field. |
| Planner/validator exact pair normalization | Done and TEST proven | Explicit names retain a pair; ungrounded model pair is cleared so generic «сравни их» remains all-visible. |
| Pair executor arbitrary canonical names | Done and TEST proven | Live first+third comparison excluded the second card and preserved three visible options in state. |
| ResponsePlan/ExecutionResult pair projection | Done and TEST proven | Explicit answer contained exactly two cards; subsequent generic answer contained all three. |
| Pair UX answer/failure matrix | Done for TEST | Live partial-enrichment failure returned two grounded cards with one final question and `error_summary.status=ok`. |
| Review as integrated package | Done | Final focused review verdict PASS; no critical/high/medium findings. |
| Immutable TEST rollout | Done | Active TEST release `nmbot-v3-generic-compare-guard-test-20260730-1`; production/client-production/bridge unchanged. |

### Recommended execution order

1. Freeze pair contract and invariants.
2. Add contract/parser/transition tests first.
3. Implement V3 pair parsing/validation without prompt edit if possible; if prompt/schema edit is needed, route through PromptMaster in a separate implementation session.
4. Implement pair executor over arbitrary canonical names and cache sufficiency.
5. Add per-turn pair projection into `ExecutionResult`/`ResponsePlan`/composer path.
6. Cover failure matrix and trace privacy.
7. Run focused owner gates, then broad adapter/API, then `py_compile`, then docs check.
8. Update lessons/status with real implementation evidence.
9. Offer review: ordinary review or full integration audit.
10. After review and explicit approval, take fresh TEST snapshot and build immutable `test-release` candidate with exact overlays only.
11. Deploy to TEST API only.
12. Prove health/identity/runtime V3.
13. Run one historical synthetic selected Level Lesnoy → compare with Tomilinsky Boulevard; inspect correlated trace immediately and stop on first error.
14. Only after first green run generic «сравни их» and partial enrichment failure scenarios.
15. Publish final receipt; do not mark complete until user confirms.

## H. TEST release receipt — 2026-07-30

- Active TEST release: `nmbot-v3-generic-compare-guard-test-20260730-1`.
- Final source snapshot: `vps-source-20260730-164027-42197d4b8133`;
  manifest SHA-256 `241064479bea5c47bd93fd48d816064fb09627981546baecef3dda4348e7e1ed`.
- Final artifact SHA-256:
  `fecbb70edfe03c98da464d6e1de0435b429fdd99f958fa600e39c995960413b2`.
- Final additive overlay after the reviewed pair/telemetry releases:
  `nmbot_v2/semantic_planner.py`, `scripts/nmbot_runtime_adapter.py`.
- Atomic artifact preflight: `101 files`, `70 py_compile`, `16 imports`.
- Recon: current target and release identity match; API reachable/ok; canonical
  systemd guards pass. Bridge PID/timestamp remained unchanged.
- Final synthetic session ref: `2ffaf1263895`.
- Explicit pair: `compare_current_pair`, exactly first+third cards, second card
  excluded, visible state count remained three, terminal `BOT_MESSAGE`.
- Pair aggregate: `status=partial_enrichment_failed`, requested/resolved `2/2`,
  fetch `2`, applied/failure `1/1`; `error_summary.status=ok`, no fallback.
- Generic follow-up «Сравни их»: `answer_kind=compare_current`, all three visible
  cards, no `pair_comparison` metadata, terminal `BOT_MESSAGE`, error status ok.
- Google Sheet exporter receipt: tab `Диалоги`, `appended=8`, readback verify ok.
- Rollback target: `nmbot-v3-pair-api-telemetry-test-20260730-1`.
- Known independent limitation: initial search composer may reject unsafe model
  prose and publish the grounded deterministic fallback; this was observed as
  `composer_validation_failed` and did not affect pair routing or grounding.
