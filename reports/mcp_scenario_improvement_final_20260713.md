# MCP scenario improvement final report — 2026-07-13

Goal: пройти сценарии из `docs/MCP_TOPIC_COVERAGE_20260713.md` пошагово, без большого runtime-патча, проверить гипотезы и собрать план будущего изменения.

## What was checked

Sources:

- `docs/MCP_TOPIC_COVERAGE_20260713.md`
- `reports/mcp_topic_coverage_queries_20260713.md`
- `reports/mcp_topic_coverage_results_20260713.md`
- raw audit files in `reports/mcp_topic_coverage_raw_20260713/`
- `scripts/nmbot_scenario_sim.py`
- `scripts/nmbot_test_agent.py --suite ux_e2e`

Scenarios checked:

1. Family / infrastructure
2. Mortgage / family mortgage
3. Installment / discount / payment terms
4. Fact-check selected ЖК
5. Investment / rental

## Final verification

```bash
python3 -m py_compile scripts/nmbot_scenario_sim.py
python3 scripts/nmbot_scenario_sim.py --all
python3 scripts/nmbot_test_agent.py --suite ux_e2e
```

Result:

- cases: 14
- passed: 14
- failed: 0
- average completeness: 100/100
- average beauty: 100/100
- average overall: 100/100
- ux_e2e: 29/29 pass

## In-flow fixes made

Only simulator/reporting was changed. Runtime bot code and production prompts were not changed.

Changed file:

- `scripts/nmbot_scenario_sim.py`

Added simulator coverage:

- `mortgage` profile/card/response/scoring/default case;
- `installment` profile/card/response/scoring/default case;
- `selected_details` profile/card/response/scoring/default case;
- finance-safe length allowance for `mortgage` and `installment` answers.

Reason: raw MCP audit showed that finance facts can be present in MCP but lost by the old family+mortgage/fact_check answer path, and that selected ЖК dossier can be conflated with yes/no `fact_check`. The simulator now has dedicated cases that catch both classes of loss.

## Findings by scenario

### 1. Family / infrastructure

Status: green.

Current simulator path already uses family facts as human reasons: schools, kindergartens, parks, yard without cars, children infrastructure. It does not invent unsupported facts and asks one final question.

Patch needed now: no.

### 2. Mortgage / family mortgage

Status: partly green, with one important coverage gap found and covered by simulator.

Evidence:

- `ux_e2e` passed 29/29, including family-mortgage follow-up and selected-option mortgage checks.
- Raw audit #11 verbalized mortgage facts correctly.
- Raw audit #12 returned `mortgage_calc`, banks, 6%, min fee, discount/installment for `Мичуринский парк`, but the old client answer mostly talked about family infrastructure.

Conclusion: MCP can provide finance facts; answer layer must not let family wording swallow mortgage facts.

Patch hypothesis for big patch:

- make `family + mortgage` a first-class composed scenario in routing/answer layer;
- keep `purpose=family` when family is primary, but require finance block when `facets:[mortgage]` and `mortgage_calc` are present;
- answer must mention only verified program/rate/down-payment/term and always keep approval/live-rate boundary.

### 3. Installment / discount / payment terms

Status: green after simulator coverage was added.

Raw audit #14 already produced a good answer: installment for 18 months, discounts/benefits, price range, and live-check boundary.

Patch hypothesis for big patch:

- keep payment terms as a financing facet;
- do not make async promises like “уточню потом”;
- if `payment_by_installments` or `discount` exists, verbalize it directly and then offer operator/live-check for exact schedule or availability.

### 4. Fact-check selected ЖК

Status: green for true yes/no fact-check, but selected ЖК dossier needs a separate scenario.

Existing simulator `fact_check` correctly handles unsupported facts: it says there is no confirmation and does not guess.

Raw audit #7 is different: it is not “confirm one fact”, but a full selected ЖК dossier for `Мичуринский парк` with price, area, metro, readiness, finishing, infrastructure and house info.

Patch hypothesis for big patch:

- split `fact_check` into two paths:
  - `fact_check`: confirm/deny one concrete assertion;
  - `selected_details` / `complex_dossier`: explain a chosen ЖК using verified dossier facts.

Hypothesis test status: confirmed by simulator-only `selected_details` case; see `reports/mcp_scenario_hypothesis_tests_20260713.md`.

### 5. Investment / rental

Status: safe and green, but evidence enrichment is the main improvement area.

Simulator investment and rental scenarios passed 100/100 and do not promise yield, payback, rent rate or price growth.

Raw audits #15 and #16 confirm basic facts are available, but richer evidence can be thin: exact discounts/installments, property_metro, ads/demand and rental demand may need additional request/enrichment.

Patch hypothesis for big patch:

- enrich MCP request profiles for investment/rental with `ads`, `property_metro`, `discount`, `payment_by_installments`, readiness/finishing and compact-area evidence when available;
- keep strict answer boundary: no yield/rent/profit claims unless separately verified.

## Recommended big-patch order

1. Add/route dedicated `selected_details` scenario for selected ЖК dossier.
2. Strengthen composed `family + mortgage` answer contract so finance facts are verbalized when present.
3. Enrich investment/rental MCP profiles with stronger evidence fields.
4. Keep installment/payment playbook as a financing facet, not a standalone search scenario unless user asks only about payment terms.
5. Add regression tests for:
   - family+mortgage does not lose `mortgage_calc`;
   - selected ЖК dossier is not treated as yes/no fact_check;
   - investment/rental do not invent yield/rent/payback;
   - installment answer mentions verified installment/discount and keeps live-check boundary.

## Deployment note

No production deploy is needed for this report because only simulator/report files were changed. Runtime bot files and prompts were not changed in this pass.

## Follow-up hypothesis gate

Before any runtime big patch, hypotheses were additionally checked in `reports/mcp_scenario_hypothesis_tests_20260713.md`.

Result: simulator `--all` passed 14/14 and `ux_e2e` passed 29/29.
