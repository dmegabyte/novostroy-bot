#!/usr/bin/env python3
"""Read-only four-layer presenter hypothesis for nmbot.

This script intentionally does not touch runtime prompts, production services,
Jivo, Google, CRM, git, evals, or model APIs unless --live is explicitly used.
Default mode is a deterministic local validation of sanitized DecisionContext
fixtures.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DEFAULT_MODEL = "google/gemini-2.5-flash"
STAGE_LABEL = "four_layer_presenter_hypothesis"
TIMEOUT_SECONDS = 60


SYSTEM_PROMPT = """
Ты — presenter layer для локальной гипотезы nmbot.

Планирование, поиск, проверка hard constraints и классификация ограничений уже выполнены.
Тебе разрешено только отрендерить входной DecisionContext в человеческий ответ.

Жёсткие правила:
- Не запускай поиск, MCP, внешние инструменты и не проси дополнительные источники.
- Не делай выводов, инференса и догадок сверх DecisionContext.
- Показывай только option_id из matched.
- Не упоминай поля из do_not_say и связанные с ними claims.
- Если matched пустой, visible_options должен быть пустым.
- Верни строго JSON без markdown и без пояснений вокруг.

Формат:
{
  "response": "plain text без вопроса",
  "params": {},
  "visible_options": [{"name": "точный label из matched", "option_id": "точный option_id из matched"}],
  "final_question": "ровно один короткий вопрос"
}
""".strip()


FIELD_KEYWORDS: dict[str, list[str]] = {
    "liquidity": ["liquidity", "liquid", "ликвид", "ликвидность"],
    "demand": ["demand", "спрос", "востребован"],
    "yield": ["yield", "доход", "доходность", "окупаем"],
    "price": ["price", "цена", "стоимость", "бюджет"],
    "location": ["location", "локац", "район", "метро"],
}


SCENARIOS: dict[str, dict[str, Any]] = {
    "hard_constraints": {
        "matched": [
            {"option_id": "exact_1", "label": "ЖК Северный квартал", "status": "matched"},
        ],
        "near_match": [],
        "rejected_count": 2,
        "unknowns": {},
        "failed_constraints": {
            "reject_1": [{"field": "location", "expected": "Сокол", "actual": "Печатники"}],
            "reject_2": [{"field": "price", "expected": "до 18 млн", "actual": "21 млн"}],
        },
        "allowed_claims": {"exact_1": ["location", "price"]},
        "do_not_say": [],
        "source_refs": {"exact_1": "fixture:four-layer:hard:exact_1"},
        "relaxation_needed": False,
    },
    "unsupported_claim": {
        "matched": [
            {"option_id": "claim_limited_1", "label": "ЖК Береговой корпус", "status": "matched"},
        ],
        "near_match": [],
        "rejected_count": 0,
        "unknowns": {},
        "failed_constraints": {},
        "allowed_claims": {"claim_limited_1": ["location", "price"]},
        "do_not_say": [
            {"option_id": "claim_limited_1", "field": "liquidity"},
            {"option_id": "claim_limited_1", "field": "demand"},
            {"option_id": "claim_limited_1", "field": "yield"},
        ],
        "source_refs": {"claim_limited_1": "fixture:four-layer:unsupported:claim_limited_1"},
        "relaxation_needed": False,
    },
    "no_match": {
        "matched": [],
        "near_match": [],
        "rejected_count": 3,
        "unknowns": {},
        "failed_constraints": {
            "reject_a": [{"field": "price", "expected": "до 12 млн", "actual": "18 млн"}],
        },
        "allowed_claims": {},
        "do_not_say": [],
        "source_refs": {},
        "relaxation_needed": True,
    },
}


def _load_env(path: Path = ENV_PATH) -> None:
    """Load .env into os.environ without printing keys or values."""

    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(cleaned[start:end])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def configured_model() -> str:
    return os.getenv("NMBOT_FOUR_LAYER_MODEL", DEFAULT_MODEL)


def build_request_data(
    decision_context: dict[str, Any],
    *,
    model: str | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    selected_model = model or configured_model()
    return {
        "query": json.dumps(
            {
                "decision_context": decision_context,
                "response_contract": {"shape": "chat_json", "fields": ["response", "params", "visible_options", "final_question"]},
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        "service": "openrouter",
        "model": selected_model,
        "system_prompt": system_prompt or SYSTEM_PROMPT,
        "parameters": {"temperature": 0, "max_tokens": 1200},
        "payload_stage": STAGE_LABEL,
    }


async def _run_gateway_request(request_data: dict[str, Any], timeout: int = TIMEOUT_SECONDS) -> tuple[str, float]:
    token = os.getenv("OVERMIND_TOKEN") or os.getenv("GATEWAY_POLL_TOKEN") or ""
    api_key = os.getenv("OPENROUTER_API_KEY") or ""
    if not token:
        raise SystemExit("OVERMIND_TOKEN/GATEWAY_POLL_TOKEN is missing")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is missing")

    safe_request_data = dict(request_data)
    safe_request_data["external_api_key"] = api_key
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    payload = {
        "agent_name": "gateway-agent",
        "endpoint": "/process",
        "request_data": safe_request_data,
        "timeout_seconds": timeout,
        "max_retries": 0,
    }
    base = os.getenv("OVERMIND_URL", "http://127.0.0.1:8080").rstrip("/")
    started = time.monotonic()
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{base}/api/v1/tasks/api", json=payload, headers=headers) as resp:
            task = await resp.json()
            if resp.status not in (200, 201):
                return "", time.monotonic() - started
        task_id = task.get("id")
        if not task_id:
            return "", time.monotonic() - started
        while time.monotonic() - started < timeout:
            async with session.get(f"{base}/api/v1/tasks/api/{task_id}/status", headers=headers) as resp:
                status_data = await resp.json()
            if status_data.get("status") in {"completed", "failed", "cancelled"}:
                async with session.get(f"{base}/api/v1/tasks/api/{task_id}/result", headers=headers) as resp:
                    result = await resp.json()
                result_obj = result.get("result") or result
                if isinstance(result_obj, dict):
                    return str(result_obj.get("response") or ""), time.monotonic() - started
                return str(result_obj), time.monotonic() - started
            await asyncio.sleep(2)
    return "", time.monotonic() - started


def deterministic_present(decision_context: dict[str, Any]) -> dict[str, Any]:
    options = list(decision_context.get("matched") or [])
    visible_options: list[dict[str, str]] = []
    for option in options[:3]:
        oid = option.get("option_id")
        label = str(option.get("label") or "")
        if oid and label:
            visible_options.append({"name": label, "option_id": str(oid)})

    if visible_options:
        labels = ", ".join(item["name"] for item in visible_options if item.get("name"))
        rejected = int(decision_context.get("rejected_count") or 0)
        message = f"Нашла подходящий вариант: {labels}. Ещё {rejected} вариантов отсеялись по ограничениям."
        question = "Какой вариант разобрать подробнее?"
    else:
        message = "С текущими жёсткими условиями подходящих вариантов не осталось. Можно чуть расширить бюджет или локацию."
        question = "Что готовы смягчить: бюджет или локацию?"
    return {"response": message, "params": {}, "visible_options": visible_options, "final_question": question}


def _allowed_option_ids(decision_context: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for bucket in ("matched", "near_match"):
        for item in decision_context.get(bucket) or []:
            if isinstance(item, dict) and item.get("option_id"):
                ids.add(str(item["option_id"]))
    return ids


def _matched_option_ids(decision_context: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for item in decision_context.get("matched") or []:
        if isinstance(item, dict) and item.get("option_id"):
            ids.add(str(item["option_id"]))
    return ids


def _question_mark_count(value: Any) -> int:
    if isinstance(value, str):
        return value.count("?")
    if isinstance(value, dict):
        return sum(_question_mark_count(v) for k, v in value.items() if k != "final_question")
    if isinstance(value, list):
        return sum(_question_mark_count(v) for v in value)
    return 0


def _count_key(obj: Any, key: str) -> int:
    if isinstance(obj, dict):
        return sum((1 if k == key else 0) + _count_key(v, key) for k, v in obj.items())
    if isinstance(obj, list):
        return sum(_count_key(v, key) for v in obj)
    return 0


def check_invariants(decision_context: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    parsed_obj = parsed if isinstance(parsed, dict) else {}
    response = parsed_obj.get("response")
    is_restricted = isinstance(response, str)
    is_legacy = isinstance(response, dict)

    if not is_restricted and not is_legacy:
        failures.append("parsed_strict_json")
        response = {}

    items: list[Any] = []
    visible_options: list[Any] = []
    if is_legacy:
        items = response.get("items") if isinstance(response.get("items"), list) else []
        if not isinstance(response.get("items"), list):
            failures.append("response_items_list")
    else:
        visible_options = parsed_obj.get("visible_options") if isinstance(parsed_obj.get("visible_options"), list) else []
        if not isinstance(parsed_obj.get("visible_options"), list):
            failures.append("visible_options_list")

    allowed_ids = _allowed_option_ids(decision_context)
    matched_ids = _matched_option_ids(decision_context)
    if is_legacy:
        leaked_ids = [str(item.get("option_id")) for item in items if isinstance(item, dict) and str(item.get("option_id")) not in allowed_ids]
    else:
        leaked_ids = [str(item.get("option_id")) for item in visible_options if isinstance(item, dict) and str(item.get("option_id")) not in matched_ids]
    if leaked_ids:
        failures.append("option_outside_allowed_ids")

    if not matched_ids and (visible_options if is_restricted else items):
        failures.append("no_match_has_items")

    final_question_count = _count_key(parsed, "final_question")
    final_question = parsed_obj.get("final_question") if is_restricted else response.get("final_question") if isinstance(response, dict) else None
    final_question_text = str(final_question or "").strip()
    if final_question_count != 1 or not final_question_text:
        failures.append("exactly_one_final_question")
    elif final_question_text.count("?") != 1:
        failures.append("final_question_one_question_mark")

    response_question_count = _question_mark_count(parsed_obj.get("response"))
    if response_question_count:
        failures.append("response_has_extra_question")

    rendered = json.dumps(parsed, ensure_ascii=False).lower()
    forbidden_hits: list[str] = []
    for rule in decision_context.get("do_not_say") or []:
        field = str((rule or {}).get("field") or "")
        keywords = FIELD_KEYWORDS.get(field, [field])
        for keyword in keywords:
            if keyword and keyword.lower() in rendered:
                forbidden_hits.append(field)
                break
    if forbidden_hits:
        failures.append("forbidden_claim_keyword")

    return {
        "ok": not failures,
        "failures": failures,
        "allowed_option_ids": sorted(allowed_ids),
        "leaked_option_ids": leaked_ids,
        "forbidden_fields_hit": sorted(set(forbidden_hits)),
        "final_question_count": final_question_count,
    }


SENSITIVE_PATTERNS = [
    re.compile(r"\+?\d[\d\s()\-]{7,}\d"),
    re.compile(r"(?i)(secret|token|api[_-]?key|authorization|bearer)[^\s\"']*"),
]


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: redact_sensitive(v) for k, v in value.items() if k not in {"request_headers", "raw_gateway_payload", "raw_response"}}
    if isinstance(value, list):
        return [redact_sensitive(v) for v in value]
    if isinstance(value, str):
        redacted = value
        for pattern in SENSITIVE_PATTERNS:
            redacted = pattern.sub("<redacted>", redacted)
        return redacted
    return value


async def run_scenario(name: str, *, live: bool = False, model: str | None = None) -> dict[str, Any]:
    if name not in SCENARIOS:
        raise SystemExit(f"unknown scenario: {name}")
    _load_env()
    selected_model = model or configured_model()
    decision_context = json.loads(json.dumps(SCENARIOS[name], ensure_ascii=False))
    started = time.monotonic()
    if live:
        raw, elapsed = await _run_gateway_request(build_request_data(decision_context, model=selected_model), TIMEOUT_SECONDS)
        parsed = _json_from_text(raw)
        mode = "live"
    else:
        parsed = deterministic_present(decision_context)
        elapsed = time.monotonic() - started
        mode = "dry_run"
    checks = check_invariants(decision_context, parsed)
    return redact_sensitive(
        {
            "scenario": name,
            "mode": mode,
            "stage": STAGE_LABEL,
            "model": selected_model,
            "elapsed_seconds": round(elapsed, 3),
            "parsed": parsed,
            "invariant_checks": checks,
        }
    )


def render_text(result: dict[str, Any]) -> str:
    checks = result["invariant_checks"]
    status = "OK" if checks["ok"] else "FAIL"
    return "\n".join(
        [
            f"scenario: {result['scenario']}",
            f"mode: {result['mode']}",
            f"stage: {result['stage']}",
            f"model: {result['model']}",
            f"elapsed_seconds: {result['elapsed_seconds']}",
            f"invariants: {status}",
            f"failures: {', '.join(checks['failures']) if checks['failures'] else '-'}",
            json.dumps(result["parsed"], ensure_ascii=False, sort_keys=True),
        ]
    )


async def self_test() -> dict[str, Any]:
    results = [await run_scenario(name, live=False) for name in SCENARIOS]
    return {"ok": all(item["invariant_checks"]["ok"] for item in results), "results": results}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only four-layer presenter hypothesis")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="hard_constraints")
    parser.add_argument("--json", action="store_true", help="print safe JSON result")
    parser.add_argument("--live", action="store_true", help="explicitly allow one gateway/model request")
    parser.add_argument("--self-test", action="store_true", help="run deterministic checks for all fixtures")
    return parser.parse_args(argv)


async def async_main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.self_test:
        result = await self_test()
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if args.json else render_text(result["results"][0]))
        return 0 if result["ok"] else 1
    result = await run_scenario(args.scenario, live=args.live)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if args.json else render_text(result))
    return 1 if args.live and not result["invariant_checks"]["ok"] else 0


def main() -> int:
    return asyncio.run(async_main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
