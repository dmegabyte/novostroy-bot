# NMBOT Node Emulator Results — 2026-07-16

This report was generated locally by `scripts/nmbot_node_emulator.py`. No model, network, Jivo, Google, CRM, VPS, git, or eval call was made.

Source references: `followup_intent_classifier.py:141-155`, `followup_intent_classifier.py:158-162`, `followup_intent_classifier.py:186-220`, `followup_intent_classifier.py:480-543`, plus local shadow emulator `scripts/nmbot_node_emulator.py`.

Summary: 15/15 scenarios passed expected node outcomes and invariant checks.

## Findings

### emulator_correctly_detected_expected_defect
- `s01_redundant_purpose_clarification`: known investment purpose but planner asks primary_intent/purpose again; classes=['intent_loss_redundant_clarification']; expectation_failures=[]

### architecture_guard_supported
- `s02_location_hard_reject`: hard location restriction rejects outside facts; classes=['none']; expectation_failures=[]
- `s03_budget_hard_matching_unit`: hard max budget uses confirmed matching_unit_price_m; classes=['none']; expectation_failures=[]
- `s04_current_options_mortgage`: mortgage question over current options forbids search; classes=['none']; expectation_failures=[]
- `s05_meaningless_recovery`: meaningless input recovers without search or shortlist; classes=['none']; expectation_failures=[]
- `s06_area_no_match`: good no-match due area minimum requires relaxation; classes=['none']; expectation_failures=[]
- `s07_unknown_claim_absent`: unsupported claim field absent goes to do_not_say; classes=['none']; expectation_failures=[]
- `s08_conflicting_intent_keep_state`: conflicting planner intent with keep policy preserves state intent; classes=['none']; expectation_failures=[]
- `s09_explicit_intent_change`: explicit intent_policy change records provenance and changes state intent; classes=['none']; expectation_failures=[]
- `s10_visible_options_explicit_new_search_allowed`: old visible options do not block explicit new search; classes=['none']; expectation_failures=[]
- `s11_budget_aggregate_range_unknown`: project-wide price range is insufficient for room-specific budget match; classes=['none']; expectation_failures=[]
- `s12_current_consultation_answer_visible`: current production consultation_answer with visible options maps to current-options answer; classes=['none']; expectation_failures=[]
- `s13_current_new_search_params_unknown`: current production new_search keeps params_delta as unknown constraints; classes=['none']; expectation_failures=[]
- `s15_preference_to_hard_migration`: constraint category transition removes field from preference when hard patch arrives; classes=['none']; expectation_failures=[]

### not_tested
- `s14_current_ask_clarification_known_purpose_not_tested`: current ask_clarification cannot prove redundant known purpose without normalized fields; classes=['none']; expectation_failures=[]

### expectation_failed
- none

## Substantive conclusions
- The typed node architecture remains promising: canonical planner validation, constraint merge, deterministic fact validation, and presenter-safe assembly can be checked locally.
- The production-shaped planner adapter is diagnostic only. It does not fix runtime and does not invent normalized fields from prose.
- Production planner contract gaps are now explicit: guards cannot be fully enforced until planner output includes normalized `clarification_fields`, primary `intent`/`intent_policy`, canonical `target`/`search_policy`, and constraint category/hardness.
- Current `params_delta` is safely treated as unknown constraints, not hard/preference. Aggregate ЖК price range remains insufficient evidence for room-specific budget match without confirmed `matching_unit_price_m`.
- Hard constraints still prevent rejected options from leaking into presenter options; unknown evidence remains in `do_not_say`.

## Limitations
- This is a shadow evaluator. It does not change current runtime behavior.
- Model semantic quality is testable only by feeding actual saved model JSON through `--planner-json`/`--input`. No model call was made here.
- The emulator validates typed node contracts and code assembly, not natural-language response quality.

## Scenario details
### s01_redundant_purpose_clarification — known investment purpose but planner asks primary_intent/purpose again
```json
{
  "architecture_classes": [
    "intent_loss_redundant_clarification"
  ],
  "expectation_failures": [],
  "failures": [
    {
      "architecture_class": "intent_loss_redundant_clarification",
      "code": "known_field_reasked",
      "detail": "planner asked fields already known: primary_intent,purpose"
    }
  ],
  "missing_expected_failures": [],
  "passed": true,
  "status": "emulator_correctly_detected_expected_defect",
  "unexpected_failures": []
}
```
### s02_location_hard_reject — hard location restriction rejects outside facts
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
### s03_budget_hard_matching_unit — hard max budget uses confirmed matching_unit_price_m
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
### s04_current_options_mortgage — mortgage question over current options forbids search
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
### s05_meaningless_recovery — meaningless input recovers without search or shortlist
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
### s06_area_no_match — good no-match due area minimum requires relaxation
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
### s07_unknown_claim_absent — unsupported claim field absent goes to do_not_say
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
### s08_conflicting_intent_keep_state — conflicting planner intent with keep policy preserves state intent
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
### s09_explicit_intent_change — explicit intent_policy change records provenance and changes state intent
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
### s10_visible_options_explicit_new_search_allowed — old visible options do not block explicit new search
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
### s11_budget_aggregate_range_unknown — project-wide price range is insufficient for room-specific budget match
Notes: aggregate_only:max_budget_m: aggregate project range is insufficient evidence without matching_unit_price_m
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
### s12_current_consultation_answer_visible — current production consultation_answer with visible options maps to current-options answer
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
### s13_current_new_search_params_unknown — current production new_search keeps params_delta as unknown constraints
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
### s14_current_ask_clarification_known_purpose_not_tested — current ask_clarification cannot prove redundant known purpose without normalized fields
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "not_tested",
  "unexpected_failures": []
}
```
### s15_preference_to_hard_migration — constraint category transition removes field from preference when hard patch arrives
```json
{
  "architecture_classes": [],
  "expectation_failures": [],
  "failures": [],
  "missing_expected_failures": [],
  "passed": true,
  "status": "architecture_guard_supported",
  "unexpected_failures": []
}
```
