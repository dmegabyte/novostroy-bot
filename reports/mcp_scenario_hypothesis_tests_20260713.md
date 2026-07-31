# MCP scenario hypothesis tests — 2026-07-13

Goal: проверить гипотезы перед большим runtime-патчем, не меняя боевой bot runtime и production prompts.

User constraint: сначала прогнать гипотезы на тестах, а не делать большой патч сразу.

## Scope

Проверялись 4 риска из финального отчёта:

1. `selected_details`: выбранный ЖК с полным досье не должен обрабатываться как yes/no `fact_check`.
2. `family + mortgage`: если MCP вернул `mortgage_calc`, ответ не должен терять ипотечные факты.
3. `investment/rental`: ответы не должны обещать доходность, аренду, окупаемость или рост цены без отдельной проверки.
4. `installment/discount`: рассрочка и скидки остаются finance facet и должны проговариваться только по verified facts.

## Simulator-only coverage added

Changed only test/simulator file:

- `scripts/nmbot_scenario_sim.py`

Added/verified simulator profiles and cases:

- `selected_details` — dossier for selected `Мичуринский парк` based on raw audit #7;
- `mortgage` — family mortgage facts from MCP card, including banks, 6%, down payment and boundary;
- `installment` — verified installment/discount facts and live-check boundary.

Runtime bot files were not changed.

## Commands run

```bash
python3 -m py_compile scripts/nmbot_scenario_sim.py
python3 scripts/nmbot_scenario_sim.py --scenario selected_details
python3 scripts/nmbot_scenario_sim.py --scenario mortgage
python3 scripts/nmbot_scenario_sim.py --scenario installment
python3 scripts/nmbot_scenario_sim.py --scenario investment
python3 scripts/nmbot_scenario_sim.py --scenario rental
python3 scripts/nmbot_scenario_sim.py --scenario fact_check
python3 scripts/nmbot_scenario_sim.py --all
python3 scripts/nmbot_test_agent.py --suite ux_e2e
```

## Results

- `selected_details`: passed 1/1, overall 100/100, warnings none.
- `mortgage`: passed 1/1, overall 100/100, warnings none.
- `installment`: passed 1/1, overall 100/100, warnings none.
- `investment`: passed 1/1, overall 100/100, warnings none.
- `rental`: passed 1/1, overall 100/100, warnings none.
- `fact_check`: passed 1/1, overall 100/100, warnings none.
- `--all`: passed 14/14, failed 0, avg completeness 100, avg beauty 100, avg overall 100.
- `ux_e2e`: passed 29/29.

## Hypothesis verdicts

### H1 — Split selected details from fact_check

Verdict: confirmed.

Raw audit #7 is a selected ЖК dossier, not a one-field yes/no check. Simulator case proves expected answer shape: price, area, metro, readiness, finishing, developer, infrastructure and house info, with live-detail boundary.

Patch implication: add runtime routing/answer path for `selected_details` / `complex_dossier`; do not overload simple `fact_check`.

### H2 — family + mortgage must not lose mortgage_calc

Verdict: confirmed.

Raw audit #12 showed MCP returned `mortgage_calc`, banks, 6%, min fee and finance facts, while old audit answer mainly talked about family infrastructure. New simulator `mortgage` case catches this loss.

Patch implication: composed `family + mortgage` should preserve family primary purpose but require finance block when `facets:[mortgage]` and `mortgage_calc` are present.

### H3 — investment/rental enrichment is safe only with strict boundaries

Verdict: confirmed.

Simulator investment/rental are green and do not promise yield, rent rate, payback or price growth. Raw audits #15/#16 show basic facts are usable, while richer signals may need extra MCP enrichment.

Patch implication: enrich request profiles with `ads`, `property_metro`, `discount`, `payment_by_installments`, readiness/finishing and compact-area evidence when available; keep no-yield/no-rent-promise guard.

### H4 — installment/discount should stay finance facet

Verdict: confirmed.

Simulator `installment` passes with direct verified facts: 18-month installment, discount/benefit and live-check boundary. Existing `ux_e2e` payment playbook also passes.

Patch implication: keep as finance facet; do not create broad standalone search unless user asks only payment terms.

## What to patch later

Recommended runtime patch remains hypothesis-backed, not yet applied:

1. Add route/answer contract for `selected_details`.
2. Strengthen composed `family + mortgage` answer layer so `mortgage_calc` is verbalized when present.
3. Enrich investment/rental MCP request profiles, without yield/rent invention.
4. Keep installment/discount as finance facet.
5. Convert these simulator checks into regression tests around runtime routing/answer behavior.

## Deployment note

No prod deploy needed for this hypothesis pass: only simulator/report files were changed.
