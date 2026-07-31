# MCP scenario improvement plan — 2026-07-13

Goal: пройти ключевые сценарии из `docs/MCP_TOPIC_COVERAGE_20260713.md` без большого патча сразу.

Method:

1. Run current simulator/runtime checks for one scenario.
2. Inspect routing/query profile/MCP facts/answer quality.
3. If a small safe fix is obvious, patch it immediately and rerun.
4. Record evidence and patch hypothesis.
5. After all scenarios, produce one final report and a consolidated big-patch plan.

Scenarios:

- [x] Family / infrastructure
- [x] Mortgage / family mortgage
- [x] Fact-check selected ЖК
- [x] Investment / rental
- [x] Installment / discount / payment terms

Sources:

- `docs/MCP_TOPIC_COVERAGE_20260713.md`
- `reports/mcp_topic_coverage_queries_20260713.md`
- `reports/mcp_topic_coverage_results_20260713.md`
- `scripts/nmbot_scenario_sim.py`
- `scripts/nmbot_mcp_only_sim.py`

## Final decision log

- Family / infrastructure: current scenario simulator is green; no immediate patch needed.
- Mortgage / family mortgage: raw MCP has finance facts; old audit answer #12 lost them, so added simulator coverage. New `mortgage` scenario passes 100/100.
- Installment / discount: added simulator coverage from audit #14. New `installment` scenario passes 100/100.
- Fact-check selected ЖК: existing gap-check simulator passes 100/100; audit #7 shows selected ЖК dossier is a different scenario and should be split into a dedicated selected-details scenario in the consolidated patch.
- Investment / rental: simulator paths pass 100/100; raw audit confirms safe basic facts, but richer signals like `ads`/`property_metro` are missing or require additional request, so big patch should improve evidence enrichment rather than invent yield/rent.
