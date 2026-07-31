from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from nmbot_v0.answer_writer import FixedOutput, normalize_fixed_output_for_response_job

FIXTURE = REPO / "data" / "v0_answer_writer_risk" / "cases.v1.jsonl"
EXPECTED_FIXTURE_COUNT = 7
CANDIDATE_PROMPT = REPO / "prompts" / "candidates" / "v0_answer_writer_promptmaster_v6.txt"
DEFAULT_RESULTS = REPO / "tmp" / "v0_answer_writer_risk" / "results.jsonl"
DEFAULT_REPORT = REPO / "tmp" / "v0_answer_writer_risk" / "report.md"
MODEL = "opencode/deepseek-v4-flash-free"
AGENT_NAME = "deepseek-valeria-simulator"
TEMPERATURE = 0.4
MAX_TOKENS = 2000
PAYLOAD_STAGE = "v0_answer_writer_risk_synthetic_contract"

Runner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _rel(path: Path) -> Path:
    return path if path.is_absolute() else REPO / path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception as exc:
            raise ValueError(f"invalid jsonl line {line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"invalid jsonl line {line_no}: not object")
        rows.append(row)
    return rows


def _read_prompt(path: Path) -> str:
    target = _rel(path)
    if not target.exists() or not target.is_file():
        raise ValueError(f"prompt path does not exist: {path}")
    text = target.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"prompt path is empty: {path}")
    return text


def validate_cases(path: Path = FIXTURE) -> list[dict[str, Any]]:
    cases = _read_jsonl(_rel(path))
    errors: list[str] = []
    if len(cases) != EXPECTED_FIXTURE_COUNT:
        errors.append(f"fixture_count={len(cases)} expected={EXPECTED_FIXTURE_COUNT}")
    seen: set[str] = set()
    for idx, case in enumerate(cases, start=1):
        cid = str(case.get("case_id") or "")
        if not cid or cid in seen:
            errors.append(f"case[{idx}] invalid/duplicate case_id")
        seen.add(cid)
        if case.get("synthetic_contract") is not True:
            errors.append(f"{cid}: synthetic_contract must be true")
        for key in ("description", "client_message", "previous_assistant_message", "response_job", "material", "expectations"):
            if key not in case:
                errors.append(f"{cid}: missing {key}")
        if "assignment" in case:
            errors.append(f"{cid}: legacy assignment wrapper is forbidden")
        if "dialogue" in case:
            errors.append(f"{cid}: legacy dialogue.previous_assistant_answer is forbidden")
        response_job = case.get("response_job") if isinstance(case.get("response_job"), Mapping) else {}
        material = case.get("material") if isinstance(case.get("material"), Mapping) else {}
        if not response_job:
            errors.append(f"{cid}: missing response_job")
        for key in ("answer_kind", "scope", "decision_action", "is_continuation"):
            if key not in response_job:
                errors.append(f"{cid}: missing response_job.{key}")
        for key in ("intro", "card_lines", "recommendation", "missing_note", "final_question"):
            if key not in material:
                errors.append(f"{cid}: missing material.{key}")
        if not isinstance(material.get("card_lines", []), list):
            errors.append(f"{cid}: material.card_lines must be list")
        if case.get("adversarial_malformed_payload") not in {True, False}:
            errors.append(f"{cid}: adversarial_malformed_payload must be boolean")
        if case.get("adversarial_malformed_payload") is not True:
            scope = str(response_job.get("scope") or "")
            if scope == "no_cards" and material.get("card_lines"):
                errors.append(f"{cid}: valid no_cards case must not carry card_lines")
            if scope == "one_card" and len(material.get("card_lines", [])) > 1:
                errors.append(f"{cid}: valid one_card case must not carry multiple card_lines")
            if scope == "shortlist" and str(response_job.get("answer_kind") or "") == "search_many" and len(material.get("card_lines", [])) > 3:
                errors.append(f"{cid}: valid shortlist/search_many case must carry at most 3 card_lines")
        expectations = case.get("expectations") if isinstance(case.get("expectations"), Mapping) else {}
        for list_key in ("forbidden_literals", "required_literals"):
            if list_key in expectations and not isinstance(expectations[list_key], list):
                errors.append(f"{cid}: expectations.{list_key} must be list")
        if "required_any_literals" in expectations and not isinstance(expectations["required_any_literals"], list):
            errors.append(f"{cid}: expectations.required_any_literals must be list")
    skip_cases = [c for c in cases if c.get("expectations", {}).get("writer_must_skip") is True]
    if len(skip_cases) != 1:
        errors.append("expected exactly one writer_must_skip case")
    if errors:
        raise ValueError("risk fixture validation failed:\n" + "\n".join(errors))
    return cases


def writer_must_skip(case: Mapping[str, Any]) -> bool:
    response_job = case.get("response_job") if isinstance(case.get("response_job"), Mapping) else {}
    if not isinstance(response_job, Mapping):
        return False
    return str(response_job.get("answer_kind") or "") in {"operator_phone", "operator"} or str(response_job.get("decision_action") or "") == "operator"


def assignment_from_case(case: Mapping[str, Any]) -> dict[str, Any]:
    response_job = dict(case.get("response_job") if isinstance(case.get("response_job"), Mapping) else {})
    material = dict(case.get("material") if isinstance(case.get("material"), Mapping) else {})
    fixed = FixedOutput(
        intro=str(material.get("intro") or ""),
        card_lines=tuple(str(line) for line in material.get("card_lines", []) if str(line).strip()) if isinstance(material.get("card_lines", []), list) else (),
        recommendation=str(material.get("recommendation") or ""),
        missing_note=str(material.get("missing_note") or ""),
        final_question=str(material.get("final_question") or ""),
        deterministic_text="",
    )
    normalized, _errors = normalize_fixed_output_for_response_job(
        fixed,
        response_job,
        selected_option_name=str(response_job.get("selected_option_name") or response_job.get("selected_object") or ""),
    )
    return {
        "client_message": str(case.get("client_message") or ""),
        "previous_assistant_message": str(case.get("previous_assistant_message") or ""),
        "response_job": response_job,
        "material": {
            "intro": normalized.intro,
            "card_lines": list(normalized.card_lines),
            "recommendation": normalized.recommendation,
            "missing_note": normalized.missing_note,
            "final_question": normalized.final_question,
        },
    }


def build_message(prompt_text: str, assignment: Mapping[str, Any]) -> str:
    envelope = {
        "payload_stage": PAYLOAD_STAGE,
        "synthetic_contract": True,
        "no_mcp_data_claimed": True,
        "assignment": dict(assignment),
    }
    payload = json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2)
    return f"VALERIA_PROMPT:\n{prompt_text}\n\nV0_RISK_ASSIGNMENT:\n{payload}\n"


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", str(text or "")).replace("\r\n", "\n").replace("\r", "\n")


def extract_answer(default_output: str) -> str:
    text = strip_ansi(default_output).strip()
    if not text:
        return ""
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.search(r"deepseek-valeria-simulator|agent\s+identity|^agent[: ]|simulator", lines[0], flags=re.IGNORECASE):
        lines = lines[1:]
    answer = "\n".join(lines).strip()
    question_end = answer.find("?")
    if question_end >= 0:
        first = answer[: question_end + 1].strip()
        rest = answer[question_end + 1 :].strip()
        if rest and re.sub(r"\s+", " ", rest) == re.sub(r"\s+", " ", first):
            return first
    return answer


def run_opencode(message: str, timeout: int, runner: Runner | None = None) -> subprocess.CompletedProcess[str]:
    command = ["opencode", "run", "--model", MODEL, "--agent", AGENT_NAME, "--format", "default", message]
    if runner is not None:
        return runner(command, timeout)
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def deterministic_checks(case: Mapping[str, Any], output: str) -> dict[str, Any]:
    expectations = case.get("expectations") if isinstance(case.get("expectations"), Mapping) else {}
    text = str(output or "")
    checks: dict[str, bool] = {}
    checks["non_empty"] = bool(text.strip())
    max_questions = int(expectations.get("max_questions", 1))
    checks["at_most_expected_questions"] = text.count("?") <= max_questions
    forbidden = [str(item) for item in expectations.get("forbidden_literals", []) if str(item)]
    required = [str(item) for item in expectations.get("required_literals", []) if str(item)]
    checks["forbidden_literals_absent"] = not any(item in text for item in forbidden)
    checks["required_literals_present"] = all(item in text for item in required)
    groups = expectations.get("required_any_literals", [])
    if isinstance(groups, list):
        checks["required_any_literals_present"] = all(
            isinstance(group, list) and any(str(item) in text for item in group if str(item))
            for group in groups
        )
    else:
        checks["required_any_literals_present"] = False
    if expectations.get("writer_must_skip") is True:
        checks["writer_must_skip"] = False
    return {"ok": all(checks.values()), "checks": checks, "response_chars": len(text)}


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    target = _rel(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def write_report(path: Path, rows: list[dict[str, Any]], *, dry_run: bool) -> None:
    lines = [
        "# V0 Answer Writer risk suite report",
        "",
        f"model: `{MODEL}`",
        f"agent: `{AGENT_NAME}`",
        f"synthetic_contract: `true`",
        f"no_mcp_data_claimed: `true`",
        f"model_not_called: `{str(dry_run).lower()}`",
        "",
        "| case | status | duration_ms | checks_ok | error | output |",
        "|---|---|---:|---|---|---|",
    ]
    for row in rows:
        output = str(row.get("extracted_output") or row.get("meta", {}).get("note") or "").replace("|", "\\|").replace("\n", "<br>")
        checks = row.get("deterministic_checks") if isinstance(row.get("deterministic_checks"), Mapping) else {}
        lines.append(f"| {row.get('case_id')} | {row.get('status')} | {row.get('duration_ms', 0)} | {checks.get('ok')} | {row.get('error', '')} | {output} |")
    target = _rel(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")


def _base_row(case: Mapping[str, Any], candidate_prompt: Path, *, dry_run: bool) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "description": case.get("description", ""),
        "synthetic_contract": True,
        "no_mcp_data_claimed": True,
        "candidate_path": str(candidate_prompt),
        "agent_name": AGENT_NAME,
        "model": MODEL,
        "dry_run": dry_run,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def _run_case(case: Mapping[str, Any], *, prompt_text: str, candidate_prompt: Path, dry_run: bool, timeout: int, runner: Runner | None = None) -> dict[str, Any]:
    started = time.monotonic()
    assignment = assignment_from_case(case)
    message = build_message(prompt_text, assignment)
    row = _base_row(case, candidate_prompt, dry_run=dry_run)
    row.update({"duration_ms": 0, "error": "", "status": "pending", "extracted_output": "", "deterministic_checks": {"ok": True, "checks": {}}})
    if writer_must_skip(case):
        row["status"] = "writer_must_skip"
        row["meta"] = {"note": "writer skipped by response_job operator/operator_phone contract; subprocess not invoked", "opencode_invoked": False}
    elif dry_run:
        row["status"] = "dry_run"
        row["meta"] = {
            "note": "model_not_called; opencode subprocess not invoked",
            "opencode_invoked": False,
            "prompt_chars": len(prompt_text),
            "assignment_chars": len(json.dumps(assignment, ensure_ascii=False, sort_keys=True)),
            "message_has_valeria_prompt_block": "VALERIA_PROMPT:" in message,
            "message_has_risk_assignment_block": "V0_RISK_ASSIGNMENT:" in message,
        }
    else:
        row["status"] = "model_called"
        try:
            completed = run_opencode(message, timeout, runner=runner)
            row["returncode"] = completed.returncode
            row["stderr_tail"] = strip_ansi(completed.stderr)[-1000:]
            answer = extract_answer(completed.stdout)
            row["extracted_output"] = answer
            if completed.returncode != 0:
                row["error"] = f"opencode_returncode_{completed.returncode}"
            elif not answer.strip():
                row["error"] = "empty_extracted_answer"
            checks = deterministic_checks(case, answer)
            row["deterministic_checks"] = checks
            if not checks.get("ok") and not row["error"]:
                row["error"] = "deterministic_validation_failed"
        except subprocess.TimeoutExpired:
            row["error"] = "timeout"
            row["returncode"] = None
            row["deterministic_checks"] = {"ok": False, "checks": {"timeout": False}}
        except Exception as exc:
            row["error"] = type(exc).__name__
            row["returncode"] = None
            row["deterministic_checks"] = {"ok": False, "checks": {"exception": False}}
    row["duration_ms"] = int((time.monotonic() - started) * 1000)
    return row


def replay(args: argparse.Namespace, runner: Runner | None = None) -> int:
    if args.model != MODEL:
        print(f"model must be exactly {MODEL}", file=sys.stderr)
        return 2
    if args.agent != AGENT_NAME:
        print(f"agent must be exactly {AGENT_NAME}", file=sys.stderr)
        return 2
    parallelism = int(getattr(args, "parallelism", 10))
    if parallelism < 1:
        print("parallelism must be >= 1", file=sys.stderr)
        return 2
    cases = validate_cases(_rel(args.fixture))
    prompt_path = _rel(args.candidate_prompt)
    prompt_text = _read_prompt(prompt_path)
    ordered: list[dict[str, Any] | None] = [None] * len(cases)
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {
            executor.submit(_run_case, case, prompt_text=prompt_text, candidate_prompt=args.candidate_prompt, dry_run=bool(args.dry_run), timeout=int(args.timeout), runner=runner): idx
            for idx, case in enumerate(cases)
        }
        for future in concurrent.futures.as_completed(futures):
            ordered[futures[future]] = future.result()
    rows = [row for row in ordered if row is not None]
    for row in rows:
        print(f"{row['case_id']} | {MODEL} | agent={AGENT_NAME} | status={row['status']} | error={bool(row['error'])} | dry_run={args.dry_run}")
    _write_jsonl(args.results, rows)
    write_report(args.report, rows, dry_run=bool(args.dry_run))
    errors = [row for row in rows if row.get("error")]
    if errors and not args.dry_run:
        print(f"aggregate_failures={len(errors)} results={_rel(args.results)} report={_rel(args.report)}", file=sys.stderr)
        return 1
    print(f"rows={len(rows)} results={_rel(args.results)} report={_rel(args.report)} model_not_called={bool(args.dry_run)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic risk suite for current V0 answer writer prompt candidate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--fixture", type=Path, default=FIXTURE)
    p_validate.add_argument("--candidate-prompt", type=Path, default=CANDIDATE_PROMPT)

    def add_run_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--fixture", type=Path, default=FIXTURE)
        target.add_argument("--candidate-prompt", type=Path, default=CANDIDATE_PROMPT)
        target.add_argument("--model", default=MODEL)
        target.add_argument("--agent", default=AGENT_NAME)
        target.add_argument("--timeout", type=int, default=120)
        target.add_argument("--parallelism", type=int, default=10)
        target.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
        target.add_argument("--report", type=Path, default=DEFAULT_REPORT)

    p_dry = sub.add_parser("dry-run")
    add_run_args(p_dry)
    p_run = sub.add_parser("run")
    add_run_args(p_run)
    args = parser.parse_args(argv)
    if args.cmd == "validate":
        try:
            cases = validate_cases(_rel(args.fixture))
            _read_prompt(_rel(args.candidate_prompt))
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"valid risk fixture records={len(cases)} path={_rel(args.fixture)} candidate={_rel(args.candidate_prompt)}")
        return 0
    if args.cmd == "dry-run":
        args.dry_run = True
        return replay(args)
    if args.cmd == "run":
        args.dry_run = False
        return replay(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
