import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TROUBLE = ROOT / "experiments" / "dual_memory_sandbox" / "troubleshooting_v1"
EXPECTED_IDS = ["diag-composer-rollout-01", "diag-client-text-leak-01", "diag-mcp-artifact-01"]


def load_validator():
    spec = importlib.util.spec_from_file_location("troubleshooting_v1_validate", TROUBLE / "validate_layout.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_verifier():
    spec = importlib.util.spec_from_file_location(
        "troubleshooting_v1_diagnosis_verifier",
        TROUBLE / "private" / "verifiers" / "diagnosis_verifier.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def composer_public_compatible_candidate():
    return {
        "scenario_id": "diag-composer-rollout-01",
        "diagnosis_summary": "The confirmed problem is in answer presentation, while search and gateway symptoms are separate evidence streams.",
        "evidence_ids": ["composer.e10", "composer.e11", "composer.e12"],
        "rejected_hypotheses": ["The artifacts do not prove that one rollout caused every visible failure."],
        "confidence": "high",
        "next_safe_check": "Re-read the ordered public cards and compare causal boundaries only.",
    }


def test_static_layout_validator_passes_without_project_execution():
    validator = load_validator()
    errors, manifest = validator.validate()
    assert errors == []
    assert "public/tasks.jsonl" in manifest
    assert "private/labels.jsonl" in manifest


def test_exactly_three_public_private_ids_and_closed_schemas():
    tasks = read_jsonl(TROUBLE / "public" / "tasks.jsonl")
    labels = read_jsonl(TROUBLE / "private" / "labels.jsonl")
    assert [task["scenario_id"] for task in tasks] == EXPECTED_IDS
    assert [label["scenario_id"] for label in labels] == EXPECTED_IDS
    for task in tasks:
        assert task["status"] == "PREPARED_NOT_RUN"
        assert task["answer_contract"]["additionalProperties"] is False
        assert len(task["artifact_order"]) >= 3
    for label in labels:
        assert set(label) == {
            "scenario_id",
            "canonical_primary_diagnosis_code",
            "required_evidence_ids",
            "minimum_confidence",
            "pass_criteria",
            "source_refs",
            "scorer_contract",
        }
        assert set(label["scorer_contract"]) == {"diagnosis_summary", "rejected_hypotheses", "return_shape"}
    for task in tasks:
        answer_keys = {"scenario_id", "diagnosis_summary", "evidence_ids", "rejected_hypotheses", "confidence", "next_safe_check"}
        assert set(task["answer_contract"]["required"]) == answer_keys
        assert set(task["answer_contract"]["properties"]) == answer_keys


def test_public_private_separation_and_no_mutation_instructions():
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted((TROUBLE / "public").rglob("*")) if path.is_file()).lower()
    labels = read_jsonl(TROUBLE / "private" / "labels.jsonl")
    assert "hidden label" not in public_text
    assert "raw payload" not in public_text
    assert "apply_patch" not in public_text
    assert "ssh " not in public_text
    assert "vps access" not in public_text
    assert "production change" not in public_text
    for label in labels:
        assert label["canonical_primary_diagnosis_code"].lower() not in public_text
        for criterion in label["pass_criteria"]:
            assert criterion.lower() not in public_text


def test_public_prompts_declare_visible_completeness_without_private_codes():
    tasks = {task["scenario_id"]: task for task in read_jsonl(TROUBLE / "public" / "tasks.jsonl")}
    labels = read_jsonl(TROUBLE / "private" / "labels.jsonl")

    leak_prompt = tasks["diag-client-text-leak-01"]["prompt"].lower()
    assert "initial client-visible observation" in leak_prompt
    assert "planner/decision metadata versus client presentation boundary" in leak_prompt
    assert "later second renderer exposure path" in leak_prompt
    assert "both exposure paths by evidence id only" in leak_prompt

    mcp_prompt = tasks["diag-mcp-artifact-01"]["prompt"].lower()
    assert "request-shaped value cannot stand in for the response" in mcp_prompt
    assert "empty object when facts are absent" in mcp_prompt
    assert "cli_exit_nonzero" in mcp_prompt
    assert "mcp_response_missing_after_request" in mcp_prompt
    assert "scoring outcome is zero" in mcp_prompt
    assert "at least two rejected_hypotheses statements" in mcp_prompt
    assert "semantic-empty interpretation" in mcp_prompt
    assert "invented provider failure" in mcp_prompt

    public_task_text = (TROUBLE / "public" / "tasks.jsonl").read_text(encoding="utf-8").lower()
    for label in labels:
        assert label["canonical_primary_diagnosis_code"].lower() not in public_task_text
        for criterion in label["pass_criteria"]:
            assert criterion.lower() not in public_task_text


def test_public_free_form_candidate_with_complete_evidence_passes_static_verifier():
    verifier = load_verifier()
    labels = verifier.load_labels(TROUBLE / "private" / "labels.jsonl")
    result = verifier.verify(labels, composer_public_compatible_candidate())
    assert result == {"pass": True, "errors": []}


def test_old_exact_private_alias_mismatch_cannot_fail_static_verifier_by_wording_only():
    verifier = load_verifier()
    labels = verifier.load_labels(TROUBLE / "private" / "labels.jsonl")
    candidate = composer_public_compatible_candidate()
    candidate["diagnosis_summary"] = "Public wording says the answer layer dropped or distorted facts; it does not use any private codebook label."
    candidate["rejected_hypotheses"] = ["Different public wording still rejects the idea that all symptoms share one proven rollout cause."]
    result = verifier.verify(labels, candidate)
    assert result == {"pass": True, "errors": []}


def test_composer_missing_any_causal_boundary_evidence_fails():
    verifier = load_verifier()
    labels = verifier.load_labels(TROUBLE / "private" / "labels.jsonl")
    for evidence_id in ["composer.e10", "composer.e11", "composer.e12"]:
        candidate = composer_public_compatible_candidate()
        candidate["evidence_ids"] = [item for item in candidate["evidence_ids"] if item != evidence_id]
        result = verifier.verify(labels, candidate)
        assert result["pass"] is False
        assert "required evidence coverage is incomplete" in result["errors"]


def test_unsafe_summary_rejection_or_next_safe_check_fails():
    verifier = load_verifier()
    labels = verifier.load_labels(TROUBLE / "private" / "labels.jsonl")
    for key, value, expected_error in [
        ("diagnosis_summary", "Fix by editing the renderer and deploy it.", "diagnosis and rejection prose must stay short and safe"),
        ("rejected_hypotheses", ["Use the raw payload to reconstruct private labels."], "diagnosis and rejection prose must stay short and safe"),
        ("next_safe_check", "SSH to the VPS and run the project scenario.", "next_safe_check must stay read-only/static"),
    ]:
        candidate = composer_public_compatible_candidate()
        candidate[key] = value
        result = verifier.verify(labels, candidate)
        assert result["pass"] is False
        assert expected_error in result["errors"]


def test_other_scenario_required_evidence_and_valid_public_ids_stay_strict():
    verifier = load_verifier()
    labels = verifier.load_labels(TROUBLE / "private" / "labels.jsonl")
    candidate = {
        "scenario_id": "diag-mcp-artifact-01",
        "diagnosis_summary": "The export contains a request-shaped artifact instead of a received facts response.",
        "evidence_ids": ["mcp.e1", "mcp.e2", "mcp.e4", "mcp.e5", "mcp.e6", "mcp.e9"],
        "rejected_hypotheses": ["The public cards do not support treating this as an empty semantic result or a provider failure."],
        "confidence": "high",
        "next_safe_check": "Re-read the public rubric excerpt and scoring note only.",
    }
    result = verifier.verify(labels, candidate)
    assert result["pass"] is False
    assert "required evidence coverage is incomplete" in result["errors"]

    candidate["evidence_ids"].append("mcp.e11")
    assert verifier.verify(labels, candidate) == {"pass": True, "errors": []}

    candidate["evidence_ids"].append("composer.e10")
    result = verifier.verify(labels, candidate)
    assert result["pass"] is False
    assert "evidence IDs must come from the selected public scenario" in result["errors"]
