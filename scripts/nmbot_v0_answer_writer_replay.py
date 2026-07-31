from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "data" / "v0_answer_writer_replay" / "cases.v1.jsonl"
EXPECTATION_OVERRIDES = REPO / "data" / "v0_answer_writer_replay" / "expectation_overrides.v1.json"
EXPECTED_FIXTURE_COUNT = 10
BASELINE_PROMPT = REPO / "prompts" / "v0_answer_writer.txt"
DEFAULT_RESULTS = REPO / "tmp" / "v0_answer_writer_replay" / "results.jsonl"
DEFAULT_REPORT = REPO / "tmp" / "v0_answer_writer_replay" / "report.md"
MODEL = "google/gemini-2.5-flash"
TEMPERATURE = 0.4
MAX_TOKENS = 2000
MAX_OUTPUT_CHARS = 1800
PAYLOAD_STAGE = "v0_answer_writer_replay"
INTERNAL_TERM_RE = re.compile(
    r"\b(?:near|card_lines|response_job|material|MCP|scope|answer_kind)\b",
    flags=re.IGNORECASE,
)


def _strip_fence(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE).strip()
        value = re.sub(r"\s*```$", "", value).strip()
    return value


def _load_json_obj(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(_strip_fence(text))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception as exc:  # pragma: no cover - exercised through validate CLI
            raise ValueError(f"invalid jsonl line {line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"invalid jsonl line {line_no}: not object")
        rows.append(row)
    return rows


def _apply_expectation_overrides(cases: list[dict[str, Any]]) -> None:
    try:
        overrides = json.loads(EXPECTATION_OVERRIDES.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"invalid expectation overrides: {exc}") from exc
    if not isinstance(overrides, dict):
        raise ValueError("invalid expectation overrides: not object")
    known_ids = {str(case.get("case_id") or "") for case in cases}
    unknown_ids = sorted(set(overrides) - known_ids)
    if unknown_ids:
        raise ValueError(f"expectation overrides reference unknown cases: {', '.join(unknown_ids)}")
    for case in cases:
        override = overrides.get(str(case.get("case_id") or ""))
        if override is None:
            continue
        if not isinstance(override, dict):
            raise ValueError("invalid expectation override: not object")
        expectations = case.get("expectations")
        if not isinstance(expectations, dict):
            raise ValueError("invalid expectation override target")
        expectations.update(override)


def _rel(path: Path) -> Path:
    return path if path.is_absolute() else REPO / path


def _read_prompt(path: Path) -> str:
    target = _rel(path)
    if not target.exists() or not target.is_file():
        raise ValueError(f"prompt path does not exist: {path}")
    text = target.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"prompt path is empty: {path}")
    return text


def _raw_search_obj(case: Mapping[str, Any]) -> dict[str, Any] | None:
    return _load_json_obj(str(case.get("raw_search_response") or ""))


def _items(raw: Mapping[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return []
    value = raw.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def evidence_names(case: Mapping[str, Any]) -> set[str]:
    raw = _raw_search_obj(case)
    names: set[str] = set()
    for key in ("facts", "near"):
        for item in _items(raw, key):
            name = str(item.get("name") or "").strip()
            if name:
                names.add(name)
    return names


def _norm_name(value: str) -> str:
    value = value.replace("«", "").replace("»", "").replace("ё", "е")
    value = re.sub(r"\s+", " ", value.strip().lower())
    return value


def _name_aliases(value: str) -> set[str]:
    name = _norm_name(value)
    aliases = {name} if name else set()
    for prefix in (
        "жк ",
        "мфк ",
        "апарт-отель ",
        "апарт отель ",
        "жилой комплекс ",
        "жилой квартал ",
        "жилой район ",
        "город-парк ",
        "премиум-квартал ",
        "клубный дом ",
    ):
        if name.startswith(prefix):
            aliases.add(name[len(prefix) :].strip())
    return {alias for alias in aliases if alias}


def evidence_name_aliases(case: Mapping[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for name in evidence_names(case):
        aliases.update(_name_aliases(name))
    return aliases


def _quoted_phrases(text: str) -> set[str]:
    return {m.strip() for m in re.findall(r"[«\"]([^»\"]+)[»\"]", text) if m.strip()}


def _extract_prices(text: str) -> set[str]:
    prices: set[str] = set()
    for m in re.finditer(r"\b\d+(?:[.,]\d+)?\s*(?:млн|руб|₽)", text, flags=re.IGNORECASE):
        prices.add(m.group(0).replace(",", ".").lower())
    return prices


def _client_price_tokens(case: Mapping[str, Any]) -> set[str]:
    """Amounts explicitly supplied by the client may be repeated, not invented."""
    return _extract_prices(str(case.get("client_message") or ""))


def _option_claim_without_evidence(response: str) -> bool:
    patterns = [
        r"\b(?:есть|имеются|нашлись)\s+(?:[^.?！!]{0,40}\s+)?(?:вариант|варианты|жк|мфк|студи|квартир)",
        r"\b(?:наш[её]л|нашла|нашел|подобрал|подобрала|подобрали|подобран[а-я]*)\s+(?:[^.?！!]{0,40}\s+)?(?:вариант|варианты|жк|мфк|студи|квартир)",
        r"\b(?:вариант|варианты|жк|мфк|студи|квартир)[^.?！!]{0,40}\b(?:найден[а-я]*|подобран[а-я]*|имеются)",
    ]
    return any(re.search(pattern, response, flags=re.IGNORECASE) for pattern in patterns)


def _norm_text(value: str) -> str:
    value = value.replace("ё", "е").replace(",", ".").lower()
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _contains_name_alias(text: str, names: set[str]) -> bool:
    norm = _norm_name(text)
    return any(alias and alias in norm for name in names for alias in _name_aliases(name))


def _material_line_trace_errors(case: Mapping[str, Any], line: str) -> list[str]:
    errors: list[str] = []
    raw_text = _evidence_text(case)
    names = evidence_names(case)
    if names and not _contains_name_alias(line, names):
        errors.append(f"material card line is not traceable to evidence name: {line!r}")
    for price in _extract_prices(line):
        if price not in raw_text:
            errors.append(f"material card line price absent from raw evidence: {line!r}")
    raw_norm = _norm_text(raw_text)
    allowed = {
        _norm_text(str(s))
        for s in (case.get("expectations", {}) or {}).get("allowed_presentation_phrases", [])
        if str(s).strip()
    }
    risky_terms = (
        "школ",
        "детск",
        "сад",
        "парк",
        "метро",
        "ипотек",
        "рассроч",
        "скид",
        "ликвид",
        "доход",
        "спрос",
        "семейн",
        "инфраструктур",
        "двор",
        "без машин",
        "хорош",
        "отличн",
        "удобн",
        "бизнес-окруж",
    )
    for chunk in re.split(r"[.;]\s*|\s+—\s+", str(line)):
        chunk_norm = _norm_text(chunk)
        if not chunk_norm or chunk_norm in raw_norm or chunk_norm in allowed:
            continue
        if any(term in chunk_norm for term in risky_terms):
            errors.append(f"material card line benefit is not traceable/allowed: {chunk!r}")
    return errors


def _evidence_text(case: Mapping[str, Any]) -> str:
    return str(case.get("raw_search_response") or "").replace(",", ".").lower()


def _response_text(output: str) -> str:
    obj = _load_json_obj(output)
    if isinstance(obj, dict) and isinstance(obj.get("response"), str):
        return str(obj["response"])
    return str(output or "")


def _required_any_substrings_match(response: str, groups: Any) -> bool:
    if groups is None:
        return True
    if not isinstance(groups, list):
        return False
    for group in groups:
        if not isinstance(group, list):
            return False
        alternatives = [str(item) for item in group if str(item)]
        if not alternatives or not any(item in response for item in alternatives):
            return False
    return True


def _required_substrings_match(response: str, required: Any) -> bool:
    if not isinstance(required, list):
        return False
    return all(str(item) in response for item in required)


def deterministic_checks(case: Mapping[str, Any], output: str) -> dict[str, Any]:
    response = _response_text(output)
    expectations = case.get("expectations") if isinstance(case.get("expectations"), Mapping) else {}
    raw_obj = _raw_search_obj(case)
    raw_text = _evidence_text(case)
    expected_names = evidence_name_aliases(case)
    configured_allowed: set[str] = set()
    for n in expectations.get("allowed_card_names", []):
        configured_allowed.update(_name_aliases(str(n)))
    allowed_names = configured_allowed or expected_names
    quoted = {_norm_name(q) for q in _quoted_phrases(response)}
    output_prices = _extract_prices(response)
    facts = _items(raw_obj, "facts")
    near = _items(raw_obj, "near")
    checks: dict[str, bool] = {}
    checks["non_empty"] = bool(response.strip())
    checks["max_length"] = len(response) <= MAX_OUTPUT_CHARS
    checks["at_most_one_question"] = response.count("?") <= int(expectations.get("max_questions", 1))
    checks["no_unknown_quoted_names"] = not quoted or quoted.issubset(allowed_names)
    client_prices = _client_price_tokens(case)
    checks["no_prices_absent_from_evidence"] = all(
        price in raw_text or price in client_prices for price in output_prices
    )
    required_cards = [str(n) for n in expectations.get("required_card_names", []) if str(n).strip()]
    response_norm = _norm_name(response)
    checks["required_card_retention"] = all(any(alias in response_norm for alias in _name_aliases(name)) for name in required_cards)
    checks["required_substrings"] = _required_substrings_match(response, expectations.get("required_substrings", []))
    checks["required_any_substrings"] = _required_any_substrings_match(response, expectations.get("required_any_substrings"))
    checks["forbidden_substrings"] = not any(str(s).lower() in response.lower() for s in expectations.get("forbidden_substrings", []))
    checks["no_internal_terms"] = not bool(INTERNAL_TERM_RE.search(response))
    no_options = not facts and not near
    if expectations.get("forbid_option_claim_when_no_evidence") or no_options:
        checks["no_option_claim_when_no_evidence"] = not bool(
            no_options
            and _option_claim_without_evidence(response)
        )
    else:
        checks["no_option_claim_when_no_evidence"] = True
    checks["no_unsupported_financing_confirmation"] = not bool(
        expectations.get("forbid_financing_confirmation", True)
        and re.search(r"без\s+первоначальн|нулев[а-я]+\s+первоначальн|ипотек[а-я]+\s+одобрен", response, flags=re.IGNORECASE)
    )
    checks["operator_cta_constraints"] = bool(expectations.get("allow_operator_cta", False)) or not bool(
        re.search(r"оператор|номер\s+телефон|позвон", response, flags=re.IGNORECASE)
    )
    if expectations.get("terminal_callback"):
        checks["no_question_after_terminal_callback"] = "?" not in response
    else:
        checks["no_question_after_terminal_callback"] = True
    return {"ok": all(checks.values()), "checks": checks, "response_chars": len(response)}


def validate_cases(path: Path = FIXTURE) -> list[dict[str, Any]]:
    cases = _read_jsonl(path)
    _apply_expectation_overrides(cases)
    errors: list[str] = []
    if len(cases) != EXPECTED_FIXTURE_COUNT:
        errors.append(f"fixture_count={len(cases)} expected={EXPECTED_FIXTURE_COUNT}")
    seen: set[str] = set()
    for idx, case in enumerate(cases, start=1):
        cid = str(case.get("case_id") or "")
        if not cid or cid in seen:
            errors.append(f"case[{idx}] invalid/duplicate case_id")
        seen.add(cid)
        for key in ("source", "client_message", "user_text", "raw_search_response", "old_response_text", "assignment", "expectations"):
            if key not in case:
                errors.append(f"{cid}: missing {key}")
        source = case.get("source") if isinstance(case.get("source"), Mapping) else {}
        for key in ("path", "line", "id", "timestamp"):
            if key not in source:
                errors.append(f"{cid}: missing source.{key}")
        assignment = case.get("assignment") if isinstance(case.get("assignment"), Mapping) else {}
        material = assignment.get("material") if isinstance(assignment.get("material"), Mapping) else {}
        response_job = assignment.get("response_job") if isinstance(assignment.get("response_job"), Mapping) else {}
        if not response_job:
            errors.append(f"{cid}: missing assignment.response_job")
        for key in ("intro", "card_lines", "recommendation", "missing_note", "final_question"):
            if key not in material:
                errors.append(f"{cid}: missing assignment.material.{key}")
        raw = str(case.get("raw_search_response") or "")
        raw_text = _evidence_text(case)
        names = evidence_names(case)
        metadata = case.get("metadata") if isinstance(case.get("metadata"), Mapping) else {}
        if metadata.get("synthetic") is True or source.get("synthetic") is True:
            errors.append(f"{cid}: synthetic-only fixture entries are not allowed")
        if not (metadata.get("defect_type") or source.get("defect_type")):
            errors.append(f"{cid}: missing defect_type metadata")
        for line in material.get("card_lines", []) if isinstance(material.get("card_lines"), list) else []:
            for err in _material_line_trace_errors(case, str(line)):
                errors.append(f"{cid}: {err}")
        for name in case.get("expectations", {}).get("required_card_names", []):
            if not any(alias in _norm_name(raw) for alias in _name_aliases(str(name))):
                errors.append(f"{cid}: required_card_name absent from raw evidence: {name}")
        for line in material.get("card_lines", []) if isinstance(material.get("card_lines"), list) else []:
            for price in _extract_prices(str(line)):
                if price not in raw_text:
                    errors.append(f"{cid}: material card line price absent from raw evidence: {line!r}")
    if errors:
        raise ValueError("fixture validation failed:\n" + "\n".join(errors))
    return cases


def _select_cases_by_id(cases: Sequence[Mapping[str, Any]], case_id: str | None) -> list[Mapping[str, Any]]:
    selected = list(cases)
    if case_id is None:
        return selected
    filtered = [case for case in selected if case.get("case_id") == case_id]
    if not filtered:
        raise ValueError(f"unknown case_id: {case_id}")
    return filtered


def build_payload(assignment: Mapping[str, Any], prompt_text: str) -> dict[str, Any]:
    return {
        "_payload_stage": PAYLOAD_STAGE,
        "query": "V0_ANSWER_WRITER_INPUT=" + json.dumps(dict(assignment), ensure_ascii=False, sort_keys=True),
        "service": "openrouter",
        "model": MODEL,
        "system_prompt": prompt_text,
        "parameters": {"temperature": TEMPERATURE, "max_tokens": MAX_TOKENS},
    }


async def call_provider(payload: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
    try:
        from scripts.nmbot_gateway_client import OvermindClient  # type: ignore
    except ImportError:  # pragma: no cover
        from nmbot_gateway_client import OvermindClient  # type: ignore

    request_data = dict(payload)
    api_key = os.getenv("OPENROUTER_API_KEY") or ""
    if api_key:
        request_data["external_api_key"] = api_key
    token = os.getenv("OVERMIND_TOKEN") or os.getenv("GATEWAY_POLL_TOKEN") or ""
    headers = {"Authorization": f"Bearer {token}"}
    client = OvermindClient()
    try:
        raw, meta = await client._run_gateway_request_once(request_data, headers, timeout)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            await close()
    safe_meta = {"ok": True, "provider": "gateway", "service": "openrouter", "model": MODEL}
    if isinstance(meta, Mapping):
        task_id = str(meta.get("_gateway_task_id") or "").strip()
        if task_id:
            safe_meta["_gateway_task_id"] = task_id[:80]
        if meta.get("_safe_fallback") or meta.get("_upstream_error"):
            safe_meta.update({"ok": False, "error_code": "gateway_error"})
    return str(raw or ""), safe_meta


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def write_report(path: Path, rows: list[dict[str, Any]], *, dry_run: bool) -> None:
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(str(row.get("case_id")), {})[str(row.get("variant"))] = row
    lines = ["# V0 Answer Writer replay report", "", f"model: `{MODEL}`", f"temperature: `{TEMPERATURE}`", f"model_not_called: `{str(dry_run).lower()}`", ""]
    for case_id, variants in by_case.items():
        any_row = next(iter(variants.values()))
        lines.extend([f"## {case_id}", "", f"source: `{any_row.get('source_ref')}`", "", "| variant | duration_ms | checks_ok | output |", "|---|---:|---|---|"])
        old = str(any_row.get("old_response") or "").replace("|", "\\|").replace("\n", "<br>")
        lines.append(f"| old | 0 | n/a | {old} |")
        for variant in ("baseline", "candidate"):
            row = variants.get(variant)
            if not row:
                continue
            output = str(row.get("output") or row.get("meta", {}).get("note") or "").replace("|", "\\|").replace("\n", "<br>")
            checks = row.get("checks") if isinstance(row.get("checks"), Mapping) else {}
            lines.append(f"| {variant} | {row.get('duration_ms', 0)} | {checks.get('ok')} | {output} |")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


async def _replay_work_item(
    case: Mapping[str, Any],
    variant: str,
    prompt_text: str,
    prompt_path: str,
    *,
    dry_run: bool,
    timeout: int,
    provider: Callable[[dict[str, Any], int], Any],
) -> dict[str, Any]:
    payload = build_payload(case["assignment"], prompt_text)
    started = time.monotonic()
    error = ""
    output = ""
    meta: dict[str, Any] = {}
    checks: dict[str, Any] = {"ok": True, "checks": {}}
    if dry_run:
        meta = {
            "dry_run": True,
            "note": "model_not_called; payload metadata assembled only",
            "payload_stage": payload["_payload_stage"],
            "prompt_chars": len(prompt_text),
            "query_chars": len(str(payload["query"])),
            "has_external_api_key_in_logged_payload": False,
        }
    else:
        try:
            output, meta = await provider(payload, int(timeout))
            if not output.strip():
                error = "empty_response"
            elif isinstance(meta, Mapping) and meta.get("ok") is False:
                error = str(meta.get("error_code") or "provider_error")
            checks = deterministic_checks(case, output)
            if not checks.get("ok") and not error:
                error = "deterministic_validation_failed"
        except TimeoutError:
            error = "timeout"
            meta = {"ok": False, "error_code": "timeout", "model": MODEL}
        except Exception as exc:  # noqa: BLE001 - run must persist safe failure rows
            error = type(exc).__name__
            meta = {"ok": False, "error_code": "exception", "exception_type": type(exc).__name__, "model": MODEL}
    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "case_id": case["case_id"],
        "variant": variant,
        "model": MODEL,
        "temperature": TEMPERATURE,
        "prompt_path": prompt_path,
        "output": output,
        "duration_ms": duration_ms,
        "error": error,
        "meta": meta,
        "checks": checks,
        "old_response": case.get("old_response_text", ""),
        "source_ref": f"{case['source']['path']}:{case['source']['line']}#{case['source']['id']}@{case['source']['timestamp']}",
        "ts": datetime.now(timezone.utc).isoformat(),
        "dry_run": bool(dry_run),
    }


async def _replay_parallel(
    work_items: Sequence[tuple[Mapping[str, Any], str, str, str]],
    *,
    parallelism: int,
    dry_run: bool,
    timeout: int,
    provider: Callable[[dict[str, Any], int], Any],
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(parallelism)
    ordered: list[dict[str, Any] | None] = [None] * len(work_items)

    async def run_one(idx: int, item: tuple[Mapping[str, Any], str, str, str]) -> None:
        case, variant, prompt_text, prompt_path = item
        async with semaphore:
            ordered[idx] = await _replay_work_item(
                case,
                variant,
                prompt_text,
                prompt_path,
                dry_run=dry_run,
                timeout=timeout,
                provider=provider,
            )

    await asyncio.gather(*(run_one(idx, item) for idx, item in enumerate(work_items)))
    return [row for row in ordered if row is not None]


async def replay(args: argparse.Namespace, provider: Callable[[dict[str, Any], int], Any] = call_provider) -> int:
    if args.model != MODEL:
        print(f"model must be exactly {MODEL}", file=sys.stderr)
        return 2
    cases = validate_cases(_rel(args.fixture))
    try:
        cases = _select_cases_by_id(cases, getattr(args, "case_id", None))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    baseline_prompt = _read_prompt(_rel(args.baseline_prompt))
    candidate_prompt = _read_prompt(_rel(args.candidate_prompt))
    variants = [("baseline", baseline_prompt, str(args.baseline_prompt)), ("candidate", candidate_prompt, str(args.candidate_prompt))]
    parallelism = int(getattr(args, "parallelism", 1))
    if parallelism < 1:
        print("parallelism must be >= 1", file=sys.stderr)
        return 2
    rows: list[dict[str, Any]] = []
    work_items = [(case, variant, prompt_text, prompt_path) for case in cases for variant, prompt_text, prompt_path in variants]
    if parallelism == 1:
        for case, variant, prompt_text, prompt_path in work_items:
            row = await _replay_work_item(
                case,
                variant,
                prompt_text,
                prompt_path,
                dry_run=bool(args.dry_run),
                timeout=int(args.timeout),
                provider=provider,
            )
            rows.append(row)
            print(f"{row['case_id']} | {variant} | {MODEL} | error={bool(row['error'])} | dry_run={args.dry_run}")
            if row["error"] and not args.dry_run:
                _write_jsonl(_rel(args.results), rows)
                write_report(_rel(args.report), rows, dry_run=False)
                print(f"stopped_on_first_failure={row['error']} results={_rel(args.results)} report={_rel(args.report)}", file=sys.stderr)
                return 1
    else:
        rows = await _replay_parallel(
            work_items,
            parallelism=parallelism,
            dry_run=bool(args.dry_run),
            timeout=int(args.timeout),
            provider=provider,
        )
        for row in rows:
            print(f"{row['case_id']} | {row['variant']} | {MODEL} | error={bool(row['error'])} | dry_run={args.dry_run}")
    _write_jsonl(_rel(args.results), rows)
    write_report(_rel(args.report), rows, dry_run=bool(args.dry_run))
    errors = [row for row in rows if row.get("error")]
    if errors and not args.dry_run:
        print(f"aggregate_failures={len(errors)} results={_rel(args.results)} report={_rel(args.report)}", file=sys.stderr)
        return 1
    print(f"rows={len(rows)} results={_rel(args.results)} report={_rel(args.report)} model_not_called={bool(args.dry_run)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Local isolated V0 Answer Writer replay harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--fixture", type=Path, default=FIXTURE)

    def add_replay_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--fixture", type=Path, default=FIXTURE)
        target.add_argument("--baseline-prompt", type=Path, default=BASELINE_PROMPT)
        target.add_argument("--candidate-prompt", type=Path, required=True)
        target.add_argument("--model", default=MODEL)
        target.add_argument("--timeout", type=int, default=90)
        target.add_argument("--parallelism", type=int, default=1)
        target.add_argument("--case-id", help="Replay exactly one existing fixture case_id")
        target.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
        target.add_argument("--report", type=Path, default=DEFAULT_REPORT)

    p_dry = sub.add_parser("dry-run")
    add_replay_args(p_dry)
    p_run = sub.add_parser("run")
    add_replay_args(p_run)

    args = p.parse_args(argv)
    if args.cmd == "validate":
        try:
            cases = validate_cases(_rel(args.fixture))
        except Exception as exc:  # noqa: BLE001
            print(str(exc), file=sys.stderr)
            return 1
        print(f"valid fixture records={len(cases)} path={_rel(args.fixture)}")
        return 0
    if args.cmd == "dry-run":
        args.dry_run = True
        return asyncio.run(replay(args))
    if args.cmd == "run":
        args.dry_run = False
        return asyncio.run(replay(args))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
