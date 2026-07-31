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

try:
    from scripts import nmbot_v0_answer_writer_replay as base
except ImportError:  # pragma: no cover
    import nmbot_v0_answer_writer_replay as base  # type: ignore


REPO = base.REPO
FIXTURE = base.FIXTURE
EXPECTED_FIXTURE_COUNT = base.EXPECTED_FIXTURE_COUNT
CANDIDATE_PROMPT = REPO / "prompts" / "candidates" / "v0_answer_writer_promptmaster_v2.txt"
DEFAULT_RESULTS = REPO / "tmp" / "v0_deepseek_proxy_replay" / "results.jsonl"
DEFAULT_REPORT = REPO / "tmp" / "v0_deepseek_proxy_replay" / "report.md"
MODEL = "opencode/deepseek-v4-flash-free"
AGENT_NAME = "deepseek-valeria-simulator"
AGENT_PATH = Path("/home/ser/.config/opencode/agent/deepseek-valeria-simulator.md")


Runner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _rel(path: Path) -> Path:
    return path if path.is_absolute() else REPO / path


def _read_prompt(path: Path) -> str:
    return base._read_prompt(_rel(path))  # type: ignore[attr-defined]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    base._write_jsonl(_rel(path), rows)  # type: ignore[attr-defined]


def source_ref(case: Mapping[str, Any]) -> str:
    source = case["source"]
    return f"{source['path']}:{source['line']}#{source['id']}@{source['timestamp']}"


def build_message(prompt_text: str, assignment: Mapping[str, Any]) -> str:
    assignment_json = json.dumps(dict(assignment), ensure_ascii=False, sort_keys=True, indent=2)
    return f"VALERIA_PROMPT:\n{prompt_text}\n\nV0_ASSIGNMENT:\n{assignment_json}\n"


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
    # `opencode run --format default` can emit the streamed response and then
    # repeat the same final response. V0 answers have one final question.
    question_end = answer.find("?")
    if question_end >= 0:
        first = answer[: question_end + 1].strip()
        remainder = answer[question_end + 1 :].strip()
        if remainder and re.sub(r"\s+", " ", remainder) == re.sub(r"\s+", " ", first):
            return first
    return answer


def run_opencode(message: str, timeout: int, runner: Runner | None = None) -> subprocess.CompletedProcess[str]:
    command = ["opencode", "run", "--agent", AGENT_NAME, "--format", "default", message]
    if runner is not None:
        return runner(command, timeout)
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)


def write_report(path: Path, rows: list[dict[str, Any]], *, dry_run: bool) -> None:
    lines = [
        "# V0 DeepSeek Valeria proxy replay report",
        "",
        f"model: `{MODEL}`",
        f"agent: `{AGENT_NAME}`",
        f"model_not_called: `{str(dry_run).lower()}`",
        "",
    ]
    for row in rows:
        old = str(row.get("old_response") or "").replace("|", "\\|").replace("\n", "<br>")
        answer = str(row.get("extracted_output") or row.get("meta", {}).get("note") or "").replace("|", "\\|").replace("\n", "<br>")
        checks = row.get("deterministic_checks") if isinstance(row.get("deterministic_checks"), Mapping) else {}
        lines.extend(
            [
                f"## {row.get('case_id')}",
                "",
                f"source: `{row.get('source_ref')}`",
                f"candidate: `{row.get('candidate_path')}`",
                "",
                "| answer | duration_ms | checks_ok | text |",
                "|---|---:|---|---|",
                f"| old | 0 | n/a | {old} |",
                f"| deepseek | {row.get('duration_ms', 0)} | {checks.get('ok')} | {answer} |",
                "",
            ]
        )
    target = _rel(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")


def _base_row(case: Mapping[str, Any], candidate_path: Path, *, dry_run: bool) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "source_ref": source_ref(case),
        "candidate_path": str(candidate_path),
        "agent_name": AGENT_NAME,
        "agent_path": str(AGENT_PATH),
        "model": MODEL,
        "dry_run": dry_run,
        "old_response": case.get("old_response_text", ""),
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def _select_cases_by_id(cases: Sequence[Mapping[str, Any]], case_id: str | None) -> list[Mapping[str, Any]]:
    selected = list(cases)
    if case_id is None:
        return selected
    filtered = [case for case in selected if case.get("case_id") == case_id]
    if not filtered:
        raise ValueError(f"unknown case_id: {case_id}")
    return filtered


def _replay_case(
    case: Mapping[str, Any],
    *,
    prompt_text: str,
    candidate_prompt: Path,
    dry_run: bool,
    timeout: int,
    runner: Runner | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    message = build_message(prompt_text, case["assignment"])
    row = _base_row(case, candidate_prompt, dry_run=dry_run)
    row.update({"duration_ms": 0, "error": "", "extracted_output": "", "deterministic_checks": {"ok": True, "checks": {}}})
    if dry_run:
        row["meta"] = {
            "note": "model_not_called; opencode subprocess not invoked",
            "prompt_chars": len(prompt_text),
            "assignment_chars": len(json.dumps(case["assignment"], ensure_ascii=False, sort_keys=True)),
            "message_has_valeria_prompt_block": "VALERIA_PROMPT:" in message,
            "message_has_v0_assignment_block": "V0_ASSIGNMENT:" in message,
        }
    else:
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
            checks = base.deterministic_checks(case, answer)
            row["deterministic_checks"] = checks
            if not checks.get("ok") and not row["error"]:
                row["error"] = "deterministic_validation_failed"
        except subprocess.TimeoutExpired:
            row["error"] = "timeout"
            row["returncode"] = None
            row["deterministic_checks"] = {"ok": False, "checks": {"timeout": False}}
        except Exception as exc:  # noqa: BLE001 - failures must be persisted safely
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
    cases = base.validate_cases(_rel(args.fixture))
    try:
        cases = _select_cases_by_id(cases, getattr(args, "case_id", None))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    prompt_path = _rel(args.candidate_prompt)
    prompt_text = _read_prompt(prompt_path)
    parallelism = int(getattr(args, "parallelism", 1))
    if parallelism < 1:
        print("parallelism must be >= 1", file=sys.stderr)
        return 2
    rows: list[dict[str, Any]] = []
    if parallelism == 1:
        for case in cases:
            row = _replay_case(
                case,
                prompt_text=prompt_text,
                candidate_prompt=args.candidate_prompt,
                dry_run=bool(args.dry_run),
                timeout=int(args.timeout),
                runner=runner,
            )
            rows.append(row)
            print(f"{row['case_id']} | {MODEL} | agent={AGENT_NAME} | error={bool(row['error'])} | dry_run={args.dry_run}")
            if row["error"] and not args.dry_run:
                _write_jsonl(args.results, rows)
                write_report(args.report, rows, dry_run=False)
                print(f"stopped_on_first_failure={row['error']} results={_rel(args.results)} report={_rel(args.report)}", file=sys.stderr)
                return 1
    else:
        ordered: list[dict[str, Any] | None] = [None] * len(cases)
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as executor:
            futures = {
                executor.submit(
                    _replay_case,
                    case,
                    prompt_text=prompt_text,
                    candidate_prompt=args.candidate_prompt,
                    dry_run=bool(args.dry_run),
                    timeout=int(args.timeout),
                    runner=runner,
                ): idx
                for idx, case in enumerate(cases)
            }
            for future in concurrent.futures.as_completed(futures):
                ordered[futures[future]] = future.result()
        rows = [row for row in ordered if row is not None]
        for row in rows:
            print(f"{row['case_id']} | {MODEL} | agent={AGENT_NAME} | error={bool(row['error'])} | dry_run={args.dry_run}")
    _write_jsonl(args.results, rows)
    write_report(args.report, rows, dry_run=bool(args.dry_run))
    errors = [row for row in rows if row.get("error")]
    if errors and not args.dry_run:
        print(f"aggregate_failures={len(errors)} results={_rel(args.results)} report={_rel(args.report)}", file=sys.stderr)
        return 1
    print(f"rows={len(rows)} results={_rel(args.results)} report={_rel(args.report)} model_not_called={bool(args.dry_run)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local isolated DeepSeek Valeria proxy replay harness")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--fixture", type=Path, default=FIXTURE)
    p_validate.add_argument("--candidate-prompt", type=Path, default=CANDIDATE_PROMPT)

    def add_replay_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--fixture", type=Path, default=FIXTURE)
        target.add_argument("--candidate-prompt", type=Path, default=CANDIDATE_PROMPT)
        target.add_argument("--model", default=MODEL)
        target.add_argument("--agent", default=AGENT_NAME)
        target.add_argument("--timeout", type=int, default=120)
        target.add_argument("--parallelism", type=int, default=1)
        target.add_argument("--case-id", help="Replay exactly one existing fixture case_id")
        target.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
        target.add_argument("--report", type=Path, default=DEFAULT_REPORT)

    p_dry = sub.add_parser("dry-run")
    add_replay_args(p_dry)
    p_run = sub.add_parser("run")
    add_replay_args(p_run)

    args = parser.parse_args(argv)
    if args.cmd == "validate":
        try:
            cases = base.validate_cases(_rel(args.fixture))
            _read_prompt(_rel(args.candidate_prompt))
        except Exception as exc:  # noqa: BLE001
            print(str(exc), file=sys.stderr)
            return 1
        print(f"valid fixture records={len(cases)} path={_rel(args.fixture)} candidate={_rel(args.candidate_prompt)}")
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
