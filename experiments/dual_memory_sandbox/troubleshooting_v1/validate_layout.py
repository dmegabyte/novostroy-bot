#!/usr/bin/env python3
"""Static layout validator for troubleshooting_v1."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPECTED_IDS = ["diag-composer-rollout-01", "diag-client-text-leak-01", "diag-mcp-artifact-01"]
TASK_KEYS = {"scenario_id", "title", "status", "scenario_dir", "artifact_order", "prompt", "answer_contract", "forbidden_actions"}
LABEL_KEYS = {
    "scenario_id",
    "canonical_primary_diagnosis_code",
    "required_evidence_ids",
    "minimum_confidence",
    "pass_criteria",
    "source_refs",
    "scorer_contract",
}
PUBLIC_FORBIDDEN = ["deploy", "ssh ", "vps access", "production change", "edit code", "apply_patch", "hidden label", "raw payload", "root cause:"]
ANSWER_KEYS = {"scenario_id", "diagnosis_summary", "evidence_ids", "rejected_hypotheses", "confidence", "next_safe_check"}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def public_text() -> str:
    chunks = []
    for path in sorted((ROOT / "public").rglob("*")):
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def validate() -> tuple[list[str], dict[str, str]]:
    errors: list[str] = []
    tasks = read_jsonl(ROOT / "public" / "tasks.jsonl")
    labels = read_jsonl(ROOT / "private" / "labels.jsonl")
    if [task.get("scenario_id") for task in tasks] != EXPECTED_IDS:
        errors.append("public tasks must contain exactly the expected three IDs")
    if [label.get("scenario_id") for label in labels] != EXPECTED_IDS:
        errors.append("private labels must contain exactly the expected three IDs")
    for task in tasks:
        sid = task.get("scenario_id")
        if set(task) != TASK_KEYS:
            errors.append(f"task {sid} has non-closed keys")
        if task.get("status") != "PREPARED_NOT_RUN":
            errors.append(f"task {sid} status is not PREPARED_NOT_RUN")
        if task.get("answer_contract", {}).get("additionalProperties") is not False:
            errors.append(f"task {sid} answer_contract is not closed")
        contract = task.get("answer_contract", {})
        if set(contract.get("required", [])) != ANSWER_KEYS:
            errors.append(f"task {sid} answer_contract does not require semantic summary/rejection fields")
        if set(contract.get("properties", {})) != ANSWER_KEYS:
            errors.append(f"task {sid} answer_contract properties are not closed to expected answer keys")
        scenario_dir = ROOT / task.get("scenario_dir", "")
        if not scenario_dir.is_dir():
            errors.append(f"missing scenario dir for {sid}")
            continue
        if len(task.get("artifact_order", [])) < 3:
            errors.append(f"scenario {sid} is not multi-step")
        for artifact in task.get("artifact_order", []):
            if not (scenario_dir / artifact).is_file():
                errors.append(f"missing ordered artifact {artifact} for {sid}")
        forbidden = "\n".join(task.get("forbidden_actions", [])).lower()
        for required in ("no code changes", "no production writes", "no network calls or remote host access", "no private labels"):
            if required not in forbidden:
                errors.append(f"task {sid} missing forbidden action: {required}")
    for label in labels:
        sid = label.get("scenario_id")
        if set(label) != LABEL_KEYS:
            errors.append(f"label {sid} has non-closed keys")
        if label.get("minimum_confidence") not in {"high", "medium", "low"}:
            errors.append(f"label {sid} has invalid confidence")
        if len(label.get("required_evidence_ids", [])) < 3:
            errors.append(f"label {sid} requires too little evidence")
        scorer_contract = label.get("scorer_contract")
        if not isinstance(scorer_contract, dict):
            errors.append(f"label {sid} scorer_contract must be an object")
        else:
            for required_key in ("diagnosis_summary", "rejected_hypotheses", "return_shape"):
                if not isinstance(scorer_contract.get(required_key), str) or not scorer_contract.get(required_key, "").strip():
                    errors.append(f"label {sid} scorer_contract missing {required_key}")
    pub = public_text().lower()
    for token in PUBLIC_FORBIDDEN:
        if token in pub:
            errors.append(f"public text contains forbidden token: {token}")
    for label in labels:
        code = label.get("canonical_primary_diagnosis_code", "")
        if code and code.lower() in pub:
            errors.append("public text leaks private primary diagnosis code")
        for criterion in label.get("pass_criteria", []):
            if criterion.lower() in pub:
                errors.append("public text leaks private pass criterion")
    manifest = {}
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            manifest[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return errors, manifest


def main() -> int:
    errors, manifest = validate()
    if errors:
        print(json.dumps({"status": "fail", "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    (ROOT / "hash_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "scenario_count": 3, "manifest_files": len(manifest)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
