# MCP scenario improvement progress — 2026-07-13

## Session log

- Started scenario-by-scenario investigation after user approved iterative hypothesis testing.
- User allowed small in-flow fixes if an obvious issue appears during testing.

## Runs

- `family`: `python3 scripts/nmbot_scenario_sim.py --scenario family` → passed 1/1, overall 100, warnings none.
- `ux_e2e`: `python3 scripts/nmbot_test_agent.py --suite ux_e2e` → passed 29/29; family mortgage and payment playbook checks green.
- Added simulator-only coverage in `scripts/nmbot_scenario_sim.py`: profiles/cards/responses/scoring/cases for `mortgage` and `installment`.
- `mortgage`: `python3 scripts/nmbot_scenario_sim.py --scenario mortgage` → passed 1/1, overall 100, warnings none.
- `installment`: `python3 scripts/nmbot_scenario_sim.py --scenario installment` → passed 1/1, overall 100, warnings none.
- `fact_check`: `python3 scripts/nmbot_scenario_sim.py --scenario fact_check` → passed 1/1, overall 100, warnings none; noted separate selected-details coverage gap from audit #7.
- `investment`: `python3 scripts/nmbot_scenario_sim.py --scenario investment` → passed 1/1, overall 100, warnings none.
- `rental`: `python3 scripts/nmbot_scenario_sim.py --scenario rental` → passed 1/1, overall 100, warnings none.
- `all`: `python3 -m py_compile scripts/nmbot_scenario_sim.py && python3 scripts/nmbot_scenario_sim.py --all` → passed 13/13, avg completeness 100, avg beauty 100, avg overall 100, failed 0.
- Added simulator-only `selected_details` coverage based on raw audit #7 for `Мичуринский парк` dossier.
- `selected_details`: `python3 scripts/nmbot_scenario_sim.py --scenario selected_details` → passed 1/1, overall 100, warnings none.
- `all`: after `selected_details`, `python3 scripts/nmbot_scenario_sim.py --all` → passed 14/14, avg completeness 100, avg beauty 100, avg overall 100, failed 0.
- `ux_e2e`: `python3 scripts/nmbot_test_agent.py --suite ux_e2e` → passed 29/29.
- Wrote hypothesis report: `reports/mcp_scenario_hypothesis_tests_20260713.md`.
