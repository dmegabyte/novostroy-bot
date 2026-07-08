#!/usr/bin/env python3
"""nmbot_response_model_eval — сравнение chat-моделей на готовых MCP-ответах.

Идея: берём старые записи `logs/dialogs-*.jsonl`, где уже есть `user_text`
и `search_response`, НЕ вызываем MCP заново, а прогоняем только chat-фазу
через текущий `prompts/chat_v1.txt` на нескольких моделях.

Команды:
  python3 scripts/nmbot_response_model_eval.py export --limit 30
  python3 scripts/nmbot_response_model_eval.py run --cases data/response_eval/cases.jsonl --models google/gemini-2.5-flash,openai/gpt-4o-mini
  python3 scripts/nmbot_response_model_eval.py run --cases data/response_eval/cases.jsonl --baseline-prompt prompts/chat_v1.txt --prompt prompts/chat_v1_compact.txt --models google/gemini-2.5-flash --limit 8
  python3 scripts/nmbot_response_model_eval.py run --cases data/response_eval/cases.jsonl --baseline-prompt prompts/chat_v1.txt --prompt prompts/chat_v1.txt --limit 2 --dry-run
  python3 scripts/nmbot_response_model_eval.py score --results data/response_eval/results.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

REPO = Path(__file__).resolve().parent.parent
LOGS = REPO / "logs"
OUT_DIR = REPO / "data" / "response_eval"
DEFAULT_CASES = OUT_DIR / "cases.jsonl"
DEFAULT_RESULTS = OUT_DIR / "results.jsonl"

DEFAULT_MODELS = [
    "google/gemini-2.5-flash",
    "google/gemini-3.1-flash-lite-preview",
    "deepseek/deepseek-v4-flash",
    "anthropic/claude-3-haiku",
    "openai/gpt-4o-mini",
]

MAX_MODELS = 5


def _strip_markdown(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl > 0:
            t = t[first_nl + 1 :]
        if t.endswith("```"):
            t = t[:-3].rstrip()
    return t


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _has_mcp_answer(rec: dict[str, Any]) -> bool:
    search_response = rec.get("search_response")
    return (
        rec.get("kind") == "user_message"
        and isinstance(rec.get("user_text"), str)
        and isinstance(search_response, str)
        and len(search_response.strip()) > 20
        and not rec.get("is_error")
    )


def _case_id(rec: dict[str, Any], index: int) -> str:
    ts = str(rec.get("ts") or "no-ts")
    safe_ts = re.sub(r"[^0-9A-Za-z]+", "-", ts).strip("-")[:40]
    return f"case-{index:04d}-{safe_ts}"


def export_cases(limit: int, source: str, out_path: Path) -> int:
    records: list[dict[str, Any]] = []
    for path in sorted(LOGS.glob("dialogs-*.jsonl")):
        for rec in _read_jsonl(path):
            if not _has_mcp_answer(rec):
                continue
            if source != "all" and rec.get("source") != source:
                continue
            records.append(rec)

    if limit > 0:
        records = records[-limit:]

    cases: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for idx, rec in enumerate(records, start=1):
        key = (rec.get("user_text", ""), rec.get("search_response", ""))
        if key in seen:
            continue
        seen.add(key)
        cases.append(
            {
                "case_id": _case_id(rec, idx),
                "user_text": rec.get("user_text", ""),
                "search_response": rec.get("search_response", ""),
                "original_chat_model": rec.get("chat_model"),
                "original_response_text": rec.get("response_text", ""),
                "source_ts": rec.get("ts"),
                "source_h_id": rec.get("h_id"),
            }
        )

    _write_jsonl(out_path, cases)
    print(f"exported_cases={len(cases)} path={out_path}")
    return 0


def _load_prompt() -> str:
    return (REPO / "prompts" / "chat_v1.txt").read_text(encoding="utf-8").strip()


def _read_prompt(path: Path) -> str:
    target = path if path.is_absolute() else REPO / path
    return target.read_text(encoding="utf-8").strip()


def _prompt_label(path: Path) -> str:
    return str(path if path.is_absolute() else path)


def _prompt_slug(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch == "_")


def _params_from_search_response(search_response: str) -> dict[str, Any]:
    obj = _loads_maybe(search_response)
    params = obj.get("params") if isinstance(obj, dict) else {}
    return params if isinstance(params, dict) else {}


def _load_overlay(relative_path: str) -> str:
    path = REPO / "prompts" / relative_path
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _chat_system_prompt_for_case(base_prompt: str, search_response: str) -> str:
    """Assemble prompt like runtime: base chat prompt + scenario/facet overlays.

    Replay tests must compare prompt variants on the same saved MCP facts without
    losing the runtime split between main prompt and scenario overlays.
    """
    params = _params_from_search_response(search_response)
    scenario_block = "Сценарий не задан: используй общий search/default контекст и не добавляй сценарных выгод."
    purpose = _prompt_slug(params.get("purpose"))
    if purpose:
        overlay = _load_overlay(f"scenarios/{purpose}_v1.txt")
        if overlay:
            scenario_block = overlay
    facet_blocks: list[str] = []
    facets = params.get("facets") if isinstance(params.get("facets"), list) else []
    for facet in facets:
        slug = _prompt_slug(facet)
        overlay = _load_overlay(f"facets/{slug}_v1.txt") if slug else ""
        if overlay:
            facet_blocks.append(overlay)
    facet_block = "\n\n".join(facet_blocks) if facet_blocks else "Facet не задан: не добавляй ипотечные/скидочные/рассрочные claims без фактов."
    prompt = base_prompt
    if "{{SCENARIO_OVERLAY}}" in prompt:
        prompt = prompt.replace("{{SCENARIO_OVERLAY}}", scenario_block)
    else:
        prompt = f"{prompt}\n\n## Сценарный модуль\n{scenario_block}"
    if "{{FACET_OVERLAYS}}" in prompt:
        prompt = prompt.replace("{{FACET_OVERLAYS}}", facet_block)
    else:
        prompt = f"{prompt}\n\n## Дополнительный facet-модуль\n{facet_block}"
    return prompt


async def _create_task(session: aiohttp.ClientSession, request_data: dict[str, Any], timeout: int) -> dict[str, Any]:
    token = _required_env("OVERMIND_TOKEN")
    overmind_url = _env("OVERMIND_URL", "https://overmind.aiaxel.ru").rstrip("/")
    payload = {
        "agent_name": "gateway-agent",
        "endpoint": "/process",
        "request_data": request_data,
        "timeout_seconds": timeout,
        "max_retries": 0,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    async with session.post(f"{overmind_url}/api/v1/tasks/api", json=payload, headers=headers) as resp:
        result = await resp.json()
        if resp.status not in (200, 201):
            raise RuntimeError(f"create task failed: http={resp.status} body={result}")
        return result


async def _poll_task(session: aiohttp.ClientSession, task_id: int, timeout: int) -> dict[str, Any]:
    token = _required_env("OVERMIND_TOKEN")
    overmind_url = _env("OVERMIND_URL", "https://overmind.aiaxel.ru").rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        async with session.get(f"{overmind_url}/api/v1/tasks/api/{task_id}/status", headers=headers) as resp:
            status_data = await resp.json()
        status = status_data.get("status")
        if status in {"completed", "failed", "cancelled"}:
            async with session.get(f"{overmind_url}/api/v1/tasks/api/{task_id}/result", headers=headers) as resp:
                return await resp.json()
        await asyncio.sleep(3)
    raise TimeoutError(f"task {task_id} timeout after {timeout}s")


def _env(name: str, default: str = "") -> str:
    import os

    return os.getenv(name, default)


def _required_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


async def _ask_chat_model(
    session: aiohttp.ClientSession,
    *,
    model: str,
    user_text: str,
    search_response: str,
    system_prompt: str,
    temperature: float,
    timeout: int,
) -> tuple[str, dict[str, Any]]:
    _required_env("OPENROUTER_API_KEY")
    chat_query = f"Запрос клиента: {user_text}\n\nНайденные факты, которыми можно пользоваться:\n{search_response}"
    request_data = {
        "query": chat_query,
        "service": "openrouter",
        "model": model,
        "system_prompt": system_prompt,
        "parameters": {"temperature": temperature, "max_tokens": 5000},
        "external_api_key": _required_env("OPENROUTER_API_KEY"),
    }
    task = await _create_task(session, request_data, timeout)
    task_id = task.get("id")
    if not task_id:
        return "", {"error": "task_id missing", "task": task}
    result = await _poll_task(session, int(task_id), timeout)
    result_obj = result.get("result") or result
    if not isinstance(result_obj, dict):
        return json.dumps(result, ensure_ascii=False), result
    return _strip_markdown(str(result_obj.get("response", ""))), result_obj


def _parse_models(raw: str | None) -> list[str]:
    models = [m.strip() for m in (raw or ",".join(DEFAULT_MODELS)).split(",") if m.strip()]
    if len(models) > MAX_MODELS:
        raise SystemExit(f"Можно максимум {MAX_MODELS} моделей, передано: {len(models)}")
    return models


def _prompt_variants(prompt: Path | None, baseline_prompt: Path | None) -> list[tuple[str, str]]:
    variants: list[tuple[str, str]] = []
    if baseline_prompt:
        variants.append((f"baseline:{_prompt_label(baseline_prompt)}", _read_prompt(baseline_prompt)))
    if prompt:
        variants.append((f"candidate:{_prompt_label(prompt)}", _read_prompt(prompt)))
    if not variants:
        variants.append(("current:prompts/chat_v1.txt", _load_prompt()))
    return variants


async def run_eval(
    cases_path: Path,
    results_path: Path,
    models: list[str],
    limit: int,
    timeout: int,
    temperature: float,
    *,
    prompt: Path | None = None,
    baseline_prompt: Path | None = None,
    dry_run: bool = False,
) -> int:
    cases = _read_jsonl(cases_path)
    if limit > 0:
        cases = cases[:limit]
    if not cases:
        print(f"no cases: {cases_path}", file=sys.stderr)
        return 1

    variants = _prompt_variants(prompt, baseline_prompt)
    rows: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as session:
        for case in cases:
            for prompt_variant, base_prompt in variants:
                system_prompt = _chat_system_prompt_for_case(base_prompt, str(case.get("search_response", "")))
                system_prompt_chars = len(system_prompt)
                for model in models:
                    started = time.monotonic()
                    error = ""
                    output = ""
                    meta: dict[str, Any] = {}
                    if dry_run:
                        # Assembly-only mode: no Overmind/OpenRouter calls.
                        # Keeps the saved response as a harmless placeholder so
                        # JSONL/score output stays readable, but every row is
                        # marked explicitly as dry_run and must not be treated as
                        # a real model comparison.
                        output = str(case.get("original_response_text") or "")
                        meta = {"dry_run": True, "note": "prompt assembled; model was not called"}
                    else:
                        try:
                            output, meta = await _ask_chat_model(
                                session,
                                model=model,
                                user_text=str(case.get("user_text", "")),
                                search_response=str(case.get("search_response", "")),
                                system_prompt=system_prompt,
                                temperature=temperature,
                                timeout=timeout,
                            )
                        except Exception as exc:  # noqa: BLE001 — eval должен писать ошибку в results, а не падать на всей пачке
                            error = str(exc)
                    duration_ms = int((time.monotonic() - started) * 1000)
                    row = {
                        "case_id": case.get("case_id"),
                        "model": model,
                        "prompt_variant": prompt_variant,
                        "system_prompt_chars": system_prompt_chars,
                        "user_text": case.get("user_text"),
                        "search_response": case.get("search_response"),
                        "output": output,
                        "duration_ms": duration_ms,
                        "error": error,
                        "meta": meta,
                        "dry_run": dry_run,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                    row["score"] = score_output(row)
                    rows.append(row)
                    print(
                        f"{case.get('case_id')} | {prompt_variant} | {model} | "
                        f"score={row['score']['score']:.2f} | sys_chars={system_prompt_chars} | "
                        f"error={bool(error)} | dry_run={dry_run}"
                    )

    _write_jsonl(results_path, rows)
    print(f"results={len(rows)} path={results_path}")
    return 0


def _loads_maybe(text: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(_strip_markdown(text))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _extract_response(output: str) -> str:
    obj = _loads_maybe(output)
    if isinstance(obj, dict) and isinstance(obj.get("response"), str):
        return obj["response"]
    return output or ""


def _facts_count(search_response: str) -> int:
    obj = _loads_maybe(search_response)
    if not obj:
        return 0
    facts = obj.get("facts")
    near = obj.get("near")
    return (len(facts) if isinstance(facts, list) else 0) + (len(near) if isinstance(near, list) else 0)


def _known_names(search_response: str) -> set[str]:
    obj = _loads_maybe(search_response)
    names: set[str] = set()
    if not obj:
        return names
    for key in ("facts", "near"):
        items = obj.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.add(item["name"].replace("«", "").replace("»", "").strip().lower())
    return names


def score_output(row: dict[str, Any]) -> dict[str, Any]:
    output = str(row.get("output") or "")
    response = _extract_response(output)
    search_response = str(row.get("search_response") or "")
    facts_count = _facts_count(search_response)

    checks: dict[str, bool] = {}
    checks["no_error"] = not bool(row.get("error"))
    checks["valid_json"] = _loads_maybe(output) is not None
    checks["has_response"] = bool(response.strip())
    checks["no_markdown_fence"] = not output.strip().startswith("```")
    checks["no_html"] = not bool(re.search(r"<[^>]+>", response))
    checks["no_links"] = "novostroy-m.ru" not in response.lower()
    checks["no_banned_greeting"] = not bool(re.search(r"уважаем|дорог", response, re.IGNORECASE))
    checks["no_cliche"] = not bool(re.search(r"с удовольствием|по вашему запросу|к сожалению", response, re.IGNORECASE))
    checks["no_exclamation"] = "!" not in response
    checks["max_three_options"] = len(re.findall(r"(?:^|\n)\s*[1-9][.)]", response)) <= 3
    checks["one_question_max"] = response.count("?") <= 1
    checks["no_early_operator"] = not (facts_count > 0 and "оператор" in response.lower())

    known_names = _known_names(search_response)
    quoted_names = {m.strip().lower() for m in re.findall(r"[«\"]([^»\"]+)[»\"]", response)}
    checks["grounded_names"] = not quoted_names or quoted_names.issubset(known_names)

    passed = sum(1 for ok in checks.values() if ok)
    total = len(checks)
    return {
        "score": passed / total if total else 0.0,
        "passed": passed,
        "total": total,
        "checks": checks,
    }


def score_results(results_path: Path) -> int:
    rows = _read_jsonl(results_path)
    if not rows:
        print(f"no results: {results_path}", file=sys.stderr)
        return 1
    summary: dict[str, list[float]] = defaultdict(list)
    durations: dict[str, list[int]] = defaultdict(list)
    failures: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        score = score_output(row)
        label = f"{row.get('prompt_variant') or 'prompt'} | {row.get('model')}"
        summary[label].append(float(score["score"]))
        duration_ms = row.get("duration_ms")
        if isinstance(duration_ms, int) and duration_ms > 0:
            durations[label].append(duration_ms)
        for name, ok in score["checks"].items():
            if not ok:
                failures[label][name] += 1

    print("prompt_variant_model\tcases\tavg_score\tavg_sec\tmin_sec\tmax_sec\ttop_failures")
    for model, scores in sorted(summary.items(), key=lambda item: sum(item[1]) / len(item[1]), reverse=True):
        avg = sum(scores) / len(scores)
        ds = durations.get(model, [])
        avg_sec = (sum(ds) / len(ds) / 1000) if ds else 0.0
        min_sec = (min(ds) / 1000) if ds else 0.0
        max_sec = (max(ds) / 1000) if ds else 0.0
        top = ", ".join(f"{k}:{v}" for k, v in failures[model].most_common(5)) or "-"
        print(f"{model}\t{len(scores)}\t{avg:.3f}\t{avg_sec:.1f}\t{min_sec:.1f}\t{max_sec:.1f}\t{top}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    p_export = sub.add_parser("export", help="Выгрузить cases из logs/dialogs-*.jsonl")
    p_export.add_argument("--limit", type=int, default=50)
    p_export.add_argument("--source", choices=("cli", "bot", "all"), default="all")
    p_export.add_argument("--out", type=Path, default=DEFAULT_CASES)

    p_run = sub.add_parser("run", help="Прогнать chat prompt на моделях")
    p_run.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    p_run.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    p_run.add_argument("--models", type=str, default=",".join(DEFAULT_MODELS))
    p_run.add_argument("--limit", type=int, default=0)
    p_run.add_argument("--timeout", type=int, default=180)
    p_run.add_argument("--temperature", type=float, default=0.3)
    p_run.add_argument("--prompt", type=Path, help="Candidate chat prompt file for replay")
    p_run.add_argument("--baseline-prompt", type=Path, help="Baseline chat prompt file for side-by-side replay")
    p_run.add_argument("--dry-run", action="store_true", help="Assemble prompts and write metadata without calling any model")

    p_score = sub.add_parser("score", help="Посчитать сводку по results.jsonl")
    p_score.add_argument("--results", type=Path, default=DEFAULT_RESULTS)

    args = p.parse_args()
    if args.cmd == "export":
        return export_cases(args.limit, args.source, args.out)
    if args.cmd == "run":
        return asyncio.run(
            run_eval(
                args.cases,
                args.results,
                _parse_models(args.models),
                args.limit,
                args.timeout,
                args.temperature,
                prompt=args.prompt,
                baseline_prompt=args.baseline_prompt,
                dry_run=args.dry_run,
            )
        )
    if args.cmd == "score":
        return score_results(args.results)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
