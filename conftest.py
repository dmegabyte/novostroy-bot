"""Collection boundary for the canonical V6-only source tree."""

# These specifications cover the removed V0–V5 runtime graph.  Keeping the
# files in the checkout preserves historical review evidence, but collecting
# them would turn an ordinary V6 test run into an import failure.
collect_ignore = [
    "tests/test_followup_canonical_contract.py",
    "tests/test_four_layer_runtime_contract.py",
    "tests/test_h041_scenario_enrichment.py",
    "tests/test_h043_stateful_rental_and_enrichment.py",
    "tests/test_h054_multiscenario_conversation.py",
    "tests/test_intent_plan_v3_transition.py",
    "tests/test_intent_plan_v3_validation.py",
    "tests/test_inventory_gate.py",
    "tests/test_mcp_contract_artifacts.py",
    "tests/test_nmbot_callback_flow.py",
    "tests/test_nmbot_card_reformatter.py",
    "tests/test_nmbot_dialogue_report.py",
    "tests/test_nmbot_client_production_requested_missing_release.py",
    "tests/test_nmbot_manager_rewriter_cli.py",
    "tests/test_nmbot_manager_rewriter_release.py",
    "tests/test_nmbot_prompt_provenance.py",
    "tests/test_semantic_planner_transition.py",
    "tests/test_search_clarify_contract.py",
    "tests/test_search_hard_constraints_contract.py",
]
