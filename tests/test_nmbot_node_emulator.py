import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_node_emulator.py"
FIXTURE = ROOT / "tests" / "fixtures" / "nmbot_node_emulator_scenarios.json"

sys.path.insert(0, str(ROOT / "scripts"))
import nmbot_node_emulator as emu  # noqa: E402


def _scenario(sid):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return next(item for item in data["scenarios"] if item["id"] == sid)


def test_each_node_output_schema_and_deterministic_order():
    result1 = emu.run_scenario(_scenario("s03_budget_hard_matching_unit"))
    result2 = emu.run_scenario(_scenario("s03_budget_hard_matching_unit"))
    assert json.dumps(result1, sort_keys=True) == json.dumps(result2, sort_keys=True)
    assert list(result1["nodes"].keys()) == ["planner_adapter", "planner_output", "context_merge", "transition_guard", "search_normalizer", "constraint_validator", "decision_context", "execution_gate", "result"]
    assert result1["nodes"]["planner_adapter"]["format"] == "canonical"
    assert set(result1["nodes"]["planner_output"]).issubset(emu.SAFE_NODE_KEYS["planner_output"])


def test_invalid_enums_and_types_fail_closed():
    scenario = _scenario("s03_budget_hard_matching_unit")
    scenario["planner_output"] = {"action": "launch", "intent": 5, "intent_policy": "guess", "target": "mars", "search_policy": "now", "confidence": "high"}
    scenario["expected_failures"] = ["invalid_planner_output"]
    scenario["expected"]["guarded_action"] = "recover_dialogue"
    scenario["expected"]["search_gate"] = False
    result = emu.run_scenario(scenario)
    assert result["nodes"]["planner_output"]["valid"] is False
    assert result["nodes"]["transition_guard"]["guarded_action"] == "recover_dialogue"
    assert result["nodes"]["transition_guard"]["guarded_search_policy"] == "forbidden"


def test_no_phrase_special_casing_with_neutral_typed_data():
    scenario = _scenario("s02_location_hard_reject")
    scenario["planner_output"]["constraints_patch"]["hard"]["location"] = ["alpha", "beta"]
    scenario["search_fixture"]["options"] = [
        {"option_id": "n1", "label": "N1", "facts": {"location": "gamma"}, "source_ref": "fixture:neutral:n1"},
        {"option_id": "n2", "label": "N2", "facts": {"location": "alpha"}, "source_ref": "fixture:neutral:n2"},
    ]
    scenario["expected"]["counts"] = {"matched": 1, "near_match": 0, "rejected": 1, "unknown": 0}
    scenario["expected"]["option_statuses"] = {"n1": "rejected", "n2": "matched"}
    scenario["expected"]["presenter_option_ids"] = ["n2"]
    scenario["expected"]["rejected_ids_absent"] = ["n1"]
    scenario["expected"]["relaxation_needed"] = False
    result = emu.run_scenario(scenario)
    statuses = {item["option_id"]: item["status"] for item in result["nodes"]["constraint_validator"]["options"]}
    assert statuses == {"n2": "matched", "n1": "rejected"}


def test_hard_constraints_cannot_leak_rejected_options_into_decision_context():
    result = emu.run_scenario(_scenario("s02_location_hard_reject"))
    dctx = result["nodes"]["decision_context"]
    assert dctx["matched"] == []
    assert dctx["near_match"] == []
    assert dctx["rejected_count"] == 1
    assert "neutral_2" not in dctx["source_refs"]


def test_known_field_reask_is_detected_generically():
    result = emu.run_scenario(_scenario("s01_redundant_purpose_clarification"))
    classes = result["nodes"]["result"]["architecture_classes"]
    assert classes == ["intent_loss_redundant_clarification"]
    assert result["nodes"]["result"]["status"] == "emulator_correctly_detected_expected_defect"
    assert result["nodes"]["result"]["passed"] is True


def test_intent_policy_conflict_preserves_state_and_explicit_change_works():
    keep = emu.run_scenario(_scenario("s08_conflicting_intent_keep_state"))
    change = emu.run_scenario(_scenario("s09_explicit_intent_change"))
    assert keep["nodes"]["context_merge"]["known_intent"] == "investment"
    assert change["nodes"]["context_merge"]["known_intent"] == "family"
    assert change["nodes"]["context_merge"]["provenance"]["primary_intent"]["policy"] == "change"


def test_current_options_forbids_search_but_explicit_new_search_with_old_visible_options_allowed():
    current = emu.run_scenario(_scenario("s04_current_options_mortgage"))
    explicit = emu.run_scenario(_scenario("s10_visible_options_explicit_new_search_allowed"))
    assert current["nodes"]["execution_gate"]["search"] is False
    assert current["nodes"]["execution_gate"]["uses_preserved_options"] is True
    assert explicit["nodes"]["execution_gate"]["search"] is True
    assert explicit["nodes"]["transition_guard"]["failures"] == []


def test_planner_adapter_auto_current_and_canonical_formats():
    canonical = emu.run_scenario(_scenario("s03_budget_hard_matching_unit"))
    current = emu.run_scenario(_scenario("s12_current_consultation_answer_visible"))
    forced = emu.run_scenario(_scenario("s12_current_consultation_answer_visible"), planner_format="current")
    assert canonical["nodes"]["planner_adapter"]["format"] == "canonical"
    assert current["nodes"]["planner_adapter"]["format"] == "current"
    assert forced["nodes"]["planner_adapter"]["format"] == "current"
    assert current["nodes"]["planner_adapter"]["adapter_source_action"] == "consultation_answer"
    assert current["nodes"]["execution_gate"]["search"] is False


def test_current_new_search_params_delta_only_unknown_with_coverage_gap():
    result = emu.run_scenario(_scenario("s13_current_new_search_params_unknown"))
    gaps = result["nodes"]["planner_adapter"]["coverage_gaps"]
    assert "primary_intent_unavailable" in gaps
    constraints = result["nodes"]["context_merge"]["constraints"]
    assert constraints["hard"] == {"rooms": "2", "max_budget_m": 25}
    assert constraints["preferences"] == {}
    assert constraints["unknown"] == {}
    assert result["nodes"]["decision_context"]["allowed_claims"] == {"any_option": ["rooms", "max_budget_m"]}
    assert result["nodes"]["execution_gate"]["search"] is True
    assert result["nodes"]["constraint_validator"]["summary"]["matched"] == 1


def test_current_budget_and_location_patch_map_to_typed_constraints():
    location = _scenario("s02_location_hard_reject")
    location["planner_format"] = "current"
    location["planner_output"] = {"dialog_action": "new_search", "confidence": 0.9, "params_delta": {"locations": ["Sokol", "Voykovskaya"], "max_price": 18_000_000}, "fallback_used": False}
    location["constraint_schema"] = {"locations": {"fact_field": "location", "operator": "in", "claim": True}, "max_price": {"fact_field": "matching_unit_price_m", "operator": "max", "claim": True}}
    location["search_fixture"]["options"] = [
        {"option_id": "neutral_2", "label": "Neutral 2", "facts": {"location": "Tekstilshchiki", "matching_unit_price_m": 21.5}, "source_ref": "fixture:current:neutral_2"},
        {"option_id": "sokol_1", "label": "Sokol 1", "facts": {"location": "Sokol", "matching_unit_price_m": 17.5}, "source_ref": "fixture:current:sokol_1"},
    ]
    location["expected"] = {"adapter_coverage_gaps": ["primary_intent_unavailable", "constraint_category_untyped"], "guarded_action": "search", "search_gate": True, "counts": {"matched": 1, "near_match": 0, "rejected": 1, "unknown": 0}, "option_statuses": {"neutral_2": "rejected", "sokol_1": "matched"}, "presenter_option_ids": ["sokol_1"], "rejected_ids_absent": ["neutral_2"], "relaxation_needed": False, "do_not_say": []}
    result = emu.run_scenario(location)
    constraints = result["nodes"]["context_merge"]["constraints"]
    assert constraints["hard"] == {"locations": ["Sokol", "Voykovskaya"], "max_price": 18_000_000}
    assert result["nodes"]["transition_guard"]["guarded_action"] == "search"
    assert result["nodes"]["constraint_validator"]["summary"] == {"matched": 1, "near_match": 0, "rejected": 1, "unknown": 0}
    assert result["nodes"]["decision_context"]["allowed_claims"] == {"sokol_1": ["locations", "max_price"]}


def test_current_ask_clarification_does_not_parse_prose_and_is_not_tested():
    result = emu.run_scenario(_scenario("s14_current_ask_clarification_known_purpose_not_tested"))
    assert result["nodes"]["planner_output"]["action"] == "clarify"
    assert result["nodes"]["execution_gate"]["search"] is False
    assert result["nodes"]["transition_guard"]["failures"] == []
    assert "clarification_fields_unavailable" in result["nodes"]["planner_adapter"]["coverage_gaps"]
    assert result["nodes"]["result"]["status"] == "not_tested"
    assert result["nodes"]["result"]["passed"] is True


def test_preference_to_hard_category_migration_records_provenance():
    result = emu.run_scenario(_scenario("s15_preference_to_hard_migration"))
    constraints = result["nodes"]["context_merge"]["constraints"]
    assert constraints["preferences"] == {}
    assert constraints["hard"] == {"location": ["Sokol"]}
    assert result["nodes"]["context_merge"]["provenance"]["location"]["from_category"] == "preferences"
    assert result["nodes"]["context_merge"]["provenance"]["location"]["to_category"] == "hard"


def test_unknown_evidence_and_aggregate_budget_cannot_become_allowed_claim():
    claim = emu.run_scenario(_scenario("s07_unknown_claim_absent"))
    assert "delivery_visible" not in claim["nodes"]["decision_context"]["allowed_claims"].get("claim_missing", [])
    assert {"option_id": "claim_missing", "field": "delivery_visible"} in claim["nodes"]["decision_context"]["do_not_say"]
    aggregate = emu.run_scenario(_scenario("s11_budget_aggregate_range_unknown"))
    assert aggregate["nodes"]["constraint_validator"]["summary"]["unknown"] == 1
    assert aggregate["nodes"]["decision_context"]["matched"] == []
    assert {"option_id": "aggregate_only", "field": "max_budget_m"} in aggregate["nodes"]["decision_context"]["do_not_say"]
    assert "insufficient evidence" in aggregate["nodes"]["constraint_validator"]["notes"][0]


def test_strict_nonzero_for_missing_expected_failure_and_expectation_mismatch(tmp_path):
    ok = subprocess.run([sys.executable, str(SCRIPT), "--self-test", "--strict", "--json"], cwd=ROOT, text=True, capture_output=True)
    assert ok.returncode == 0, ok.stderr + ok.stdout
    bad = _scenario("s01_redundant_purpose_clarification")
    bad["expected_failures"] = []
    badfile = tmp_path / "bad.json"
    badfile.write_text(json.dumps(bad), encoding="utf-8")
    fail = subprocess.run([sys.executable, str(SCRIPT), "--input", str(badfile), "--strict", "--json"], cwd=ROOT, text=True, capture_output=True)
    assert fail.returncode == 1
    mismatch = _scenario("s03_budget_hard_matching_unit")
    mismatch["expected"]["counts"]["matched"] = 99
    mismatchfile = tmp_path / "mismatch.json"
    mismatchfile.write_text(json.dumps(mismatch), encoding="utf-8")
    fail2 = subprocess.run([sys.executable, str(SCRIPT), "--input", str(mismatchfile), "--strict", "--json"], cwd=ROOT, text=True, capture_output=True)
    assert fail2.returncode == 1


def test_planner_and_search_json_overlay(tmp_path):
    planner = tmp_path / "planner.json"
    planner.write_text(json.dumps({"action": "recover_dialogue", "intent": "unknown", "intent_policy": "keep", "target": "none", "search_policy": "forbidden", "confidence": 0.2, "constraints_patch": {"hard": {}, "preferences": {}, "unknown": {}}, "facets": {}, "missing_fields": [], "clarification_fields": []}), encoding="utf-8")
    search = tmp_path / "search.json"
    search.write_text(json.dumps({"options": []}), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--scenario", "s05_meaningless_recovery", "--planner-json", str(planner), "--search-json", str(search), "--json"], cwd=ROOT, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert "recover_dialogue" in proc.stdout

    current_planner = tmp_path / "current_planner.json"
    current_planner.write_text(json.dumps({"dialog_action": "new_search", "confidence": 0.9, "params_delta": {"rooms": "2"}, "fallback_used": False}), encoding="utf-8")
    proc2 = subprocess.run([sys.executable, str(SCRIPT), "--scenario", "s13_current_new_search_params_unknown", "--planner-json", str(current_planner), "--planner-format", "auto", "--json"], cwd=ROOT, text=True, capture_output=True)
    assert proc2.returncode == 0, proc2.stderr
    assert '"format": "current"' in proc2.stdout
    assert "constraint_category_untyped" not in proc2.stdout


def test_input_output_redaction_value_level_and_allowlist(tmp_path):
    scenario = _scenario("s03_budget_hard_matching_unit")
    scenario["planner_output"]["unknown_benign"] = {"note": "call me at +7 999 111-22-33 and secret=abcdefghi"}
    scenario["search_fixture"]["options"][0]["facts"]["comment"] = "phone 89991112233"
    result = emu.run_scenario(scenario)
    dumped = json.dumps(result, ensure_ascii=False)
    assert "999 111" not in dumped
    assert "89991112233" not in dumped
    assert "abcdefghi" not in dumped
    assert "<redacted_phone>" in dumped
    report = emu.render_report([result])
    assert "999 111" not in report
    assert "89991112233" not in report
