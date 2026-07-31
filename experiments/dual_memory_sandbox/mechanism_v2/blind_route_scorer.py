#!/usr/bin/env python3
"""Blind deterministic route-quality scorer for sealed mechanism-v2 artifacts.

The scorer reads private labels only from the fixed internal labels path.  The
assessment input intentionally excludes arm and receipt advice, so arm cannot
affect route quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from route_safety import route_summary_leaks_arm_identity


ROOT = Path(__file__).resolve().parent
LABELS_PATH = ROOT / "private" / "labels.jsonl"
ASSESSMENT_KEYS = {"task_id", "selected_check_codes", "route_summary"}
SEALED_KEYS = {"schema_version", "status", "fresh_session_id", "task_id", "arm", "run_identity", "agent_result", "source_hashes", "binding", "diagnostics", "execution_allowed"}
SCORE_KEYS = {"schema_version", "status", "task_id", "candidate", "sealed_result_sha256", "private_labels_sha256", "quality_pass", "safe_route_summary", "selected_expected_route", "scorer_blind_to_arm", "private_expected_values_disclosed"}
SAFE_SESSION_ID = re.compile(r"^ses_[A-Za-z0-9_-]{1,96}$")
FORBIDDEN = ("private", "expected", "label", "thought", "prompt", "raw code", "tool output", "log")


class ScoreError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_json_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assessment_from_sealed(sealed: dict[str, Any]) -> dict[str, Any]:
    if set(sealed) != SEALED_KEYS:
        raise ScoreError("sealed artifact must use the exact closed schema")
    if sealed.get("schema_version") != 1 or sealed.get("status") != "sealed_route_only_result" or sealed.get("execution_allowed") is not False:
        raise ScoreError("sealed artifact has unsafe status/schema")
    if not SAFE_SESSION_ID.fullmatch(str(sealed.get("fresh_session_id", ""))):
        raise ScoreError("sealed artifact has unsafe session id")
    result = sealed.get("agent_result")
    if not isinstance(result, dict):
        raise ScoreError("sealed artifact misses agent_result")
    if result.get("task_id") != sealed.get("task_id") or result.get("arm") != sealed.get("arm"):
        raise ScoreError("sealed result identity mismatch")
    assessment = {
        "task_id": result.get("task_id"),
        "selected_check_codes": result.get("selected_check_codes"),
        "route_summary": result.get("route_summary"),
    }
    if set(assessment) != ASSESSMENT_KEYS:
        raise ScoreError("internal assessment shape mismatch")
    return assessment


def validate_score_artifact(score: dict[str, Any], *, sealed: dict[str, Any], sealed_hash: str) -> None:
    if set(score) != SCORE_KEYS:
        raise ScoreError("blind score must use the exact closed schema")
    if score.get("schema_version") != 1 or score.get("status") != "blind_route_score":
        raise ScoreError("blind score status/schema mismatch")
    if score.get("task_id") != sealed.get("task_id"):
        raise ScoreError("blind score task binding mismatch")
    if score.get("sealed_result_sha256") != sealed_hash:
        raise ScoreError("blind score sealed_result hash binding mismatch")
    labels_hash = sha256_json_file(LABELS_PATH)
    if score.get("private_labels_sha256") != labels_hash or score.get("private_labels_sha256") != sealed.get("source_hashes", {}).get("private/labels.jsonl"):
        raise ScoreError("blind score private label provenance mismatch")
    candidate = score.get("candidate")
    binding = sealed.get("binding", {})
    if not isinstance(candidate, dict) or set(candidate) != {"session_id", "candidate_sha256", "run_manifest_sha256"}:
        raise ScoreError("blind score candidate binding schema mismatch")
    if candidate.get("session_id") != sealed.get("fresh_session_id"):
        raise ScoreError("blind score session binding mismatch")
    if candidate.get("candidate_sha256") != binding.get("candidate_sha256") or candidate.get("run_manifest_sha256") != binding.get("run_manifest_sha256"):
        raise ScoreError("blind score candidate/source binding mismatch")
    for key in ("quality_pass", "safe_route_summary", "selected_expected_route", "scorer_blind_to_arm", "private_expected_values_disclosed"):
        if not isinstance(score.get(key), bool):
            raise ScoreError(f"blind score {key} must be boolean")
    if score.get("scorer_blind_to_arm") is not True or score.get("private_expected_values_disclosed") is not False:
        raise ScoreError("blind score must remain blind and non-disclosing")


def score_sealed(sealed_path: Path) -> dict[str, Any]:
    sealed_path = sealed_path.resolve()
    sealed_hash = sha256_json_file(sealed_path)
    sealed = _read_json(sealed_path)
    assessment = _assessment_from_sealed(sealed)
    labels = {row["task_id"]: row for row in _read_jsonl(LABELS_PATH)}
    label = labels.get(assessment["task_id"])
    if not label:
        raise ScoreError("missing private label for task")
    selected = assessment.get("selected_check_codes")
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        raise ScoreError("selected_check_codes must be strings")
    summary = assessment.get("route_summary")
    safe_summary = isinstance(summary, str) and bool(summary.strip()) and not any(token in summary.lower() for token in FORBIDDEN) and not route_summary_leaks_arm_identity(summary)
    expected = set(label.get("private_expected_check_codes", []))
    exact_route = set(selected) == expected and len(selected) == len(set(selected))
    quality_pass = bool(safe_summary and exact_route)
    score = {
        "schema_version": 1,
        "status": "blind_route_score",
        "task_id": assessment["task_id"],
        "candidate": {
            "session_id": sealed["fresh_session_id"],
            "candidate_sha256": sealed["binding"]["candidate_sha256"],
            "run_manifest_sha256": sealed["binding"]["run_manifest_sha256"],
        },
        "sealed_result_sha256": sealed_hash,
        "private_labels_sha256": sha256_json_file(LABELS_PATH),
        "quality_pass": quality_pass,
        "safe_route_summary": bool(safe_summary),
        "selected_expected_route": bool(exact_route),
        "scorer_blind_to_arm": True,
        "private_expected_values_disclosed": False,
    }
    validate_score_artifact(score, sealed=sealed, sealed_hash=sealed_hash)
    return score


def write_score(sealed_path: Path) -> dict[str, Any]:
    score = score_sealed(sealed_path)
    out = sealed_path.resolve().parent / "blind_score.json"
    out.write_text(json.dumps(score, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return score


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Blind route scorer; no agents or fixtures executed.")
    parser.add_argument("--sealed", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        score = write_score(args.sealed)
        print(json.dumps(score, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
