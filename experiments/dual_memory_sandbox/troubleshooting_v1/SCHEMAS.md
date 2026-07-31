# Troubleshooting v1 schemas

All JSON objects are closed: extra keys are invalid.

## Public task record

Required keys: `scenario_id`, `title`, `status`, `scenario_dir`,
`artifact_order`, `prompt`, `answer_contract`, `forbidden_actions`.

## Candidate answer contract

The candidate answer must be one JSON object with exactly these keys:

- `scenario_id`: string.
- `diagnosis_summary`: one short, non-empty natural-language statement of the
  diagnosis.
- `evidence_ids`: list of strings, at least three IDs from ordered public cards.
- `rejected_hypotheses`: list of strings, at least one short natural-language
  statement explaining a rejected tempting hypothesis.
- `confidence`: one of `high`, `medium`, `low`.
- `next_safe_check`: one short string describing a read-only/static check only.

The candidate must not include prose payload dumps, raw payload reconstruction,
source-code excerpts, remediation/fix instructions, network/VPS/project access,
private label claims, private/canonical codes, or raw criteria reconstruction.
No diagnosis or rejection code string is semantically scored in the public
contract.

## Private label record

Required keys: `scenario_id`, `canonical_primary_diagnosis_code`,
`required_evidence_ids`, `minimum_confidence`, `pass_criteria`, `source_refs`,
`scorer_contract`.

The label schema is closed. Labels are not public task material. Static
verification validates closed shape, public evidence IDs, required evidence,
confidence, and safe prose fields only. A separate independent read-only scorer
checks semantic meaning of `diagnosis_summary` and `rejected_hypotheses` against
private pass criteria and returns safe booleans only. Public tasks must not
disclose private label values or private scoring criteria.
