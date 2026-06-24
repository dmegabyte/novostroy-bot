#!/usr/bin/env python3
"""
Multi-turn scenario runner for Novostroy AI answer prompt.

Загружает сценарии из scenarios/*.json, прогоняет каждый через
OpenRouter (gemini-2.5-flash-lite) с историей диалога,
проверяет утверждения на каждом шаге, выводит отчёт.

Использование:
  python3 run_scenarios.py
  python3 run_scenarios.py --verbose
  python3 run_scenarios.py --scenario s2
  python3 run_scenarios.py --json  # JSON output
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
import asyncio

# ── Конфиг ──────────────────────────────────────────────────────

HERE = Path(__file__).parent
SCENARIOS_DIR = HERE / "scenarios"
ANSWER_PROMPT_FILE = HERE / "answer_prompt.txt"

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-2.5-flash-lite"
TEMPERATURE = 0.0
MAX_TOKENS = 600

# ── Assertion engine ────────────────────────────────────────────


def check_assertion(text: str, assertion: dict) -> tuple[bool, str]:
    """Проверить одно утверждение. Возвращает (passed, reason)."""
    atype = assertion["type"]
    value = assertion.get("value", "")

    if atype == "contains":
        ok = value.lower() in text.lower()
        return ok, f"содержит '{value}'" if ok else f"НЕ содержит '{value}'"

    elif atype == "not-contains":
        ok = value.lower() not in text.lower()
        return ok, f"НЕ содержит '{value}'" if ok else f"содержит '{value}' (запрещено)"

    elif atype == "icontains":
        ok = value.lower() in text.lower()
        return ok, f"содержит '{value}'" if ok else f"НЕ содержит '{value}'"

    elif atype == "icontains-any":
        vals = value if isinstance(value, list) else [value]
        found = [v for v in vals if v.lower() in text.lower()]
        ok = len(found) > 0
        if ok:
            return True, f"содержит '{found[0]}'"
        else:
            return False, f"НЕ содержит ни одного из: {vals}"

    elif atype == "not-icontains-any":
        vals = value if isinstance(value, list) else [value]
        found = [v for v in vals if v.lower() in text.lower()]
        ok = len(found) == 0
        if ok:
            return True, f"не содержит ни одного из: {vals}"
        else:
            return False, f"содержит '{found[0]}' (запрещено)"

    elif atype == "regex":
        ok = bool(re.search(value, text))
        return ok, f"regex '{value}' совпал" if ok else f"regex '{value}' не совпал"

    else:
        return False, f"неизвестный тип assertion: {atype}"


def check_all_assertions(text: str, assertions: list[dict]) -> dict:
    """Проверить все утверждения. Вернуть {'passed': bool, 'results': [...]}."""
    results = []
    passed_count = 0
    for a in assertions:
        ok, reason = check_assertion(text, a)
        results.append({
            "assertion": a,
            "passed": ok,
            "reason": reason,
        })
        if ok:
            passed_count += 1
    return {
        "passed": passed_count == len(assertions),
        "passed_count": passed_count,
        "total_count": len(assertions),
        "results": results,
    }


# ── OpenRouter call ─────────────────────────────────────────────


async def call_openrouter(system_prompt: str, user_message: str) -> str | None:
    """Вызвать OpenRouter и вернуть текст ответа."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
    }
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                OPENROUTER_URL, json=payload, headers=headers, timeout=30,
            ) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    print(f"  [API ERROR] {resp.status}: {data.get('error', {}).get('message', str(data))}")
                    return None
                return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  [EXCEPTION] {e}")
        return None


# ── Scenario runner ─────────────────────────────────────────────


async def run_turn(
    system_prompt: str,
    data: str,
    query: str,
    history: list[str],
    turn_idx: int,
    note: str,
    assertions: list[dict],
    verbose: bool = False,
) -> dict:
    """Прогнать один turn сценария."""

    # Формируем контекст как в session.py
    context_parts = [f"Исходные данные:\n{data[:3000]}"]
    if history:
        context_parts.append(f"Уточнения: {'; '.join(history[-5:])}")
    context_parts.append(f"Новый запрос: {query}")
    user_message = "\n".join(context_parts)

    if verbose:
        print(f"\n{'─'*60}")
        print(f"  Turn {turn_idx}: {query}")
        print(f"  History: {' → '.join(history[-3:]) if history else '(none)'}")
        print(f"  Note: {note}")

    # Call API
    answer = await call_openrouter(system_prompt, user_message)
    if answer is None:
        return {
            "turn": turn_idx,
            "query": query,
            "note": note,
            "error": "API call failed",
            "answer": None,
            "assertions": check_all_assertions("", assertions),
        }

    if verbose:
        print(f"\n  Ответ: {answer[:300]}")

    # Check assertions
    result = check_all_assertions(answer, assertions)

    if verbose:
        status = "✅" if result["passed"] else "❌"
        print(f"\n  {status} {result['passed_count']}/{result['total_count']} assertions passed")
        for r in result["results"]:
            icon = "✅" if r["passed"] else "❌"
            print(f"    {icon} {r['reason']}")

    return {
        "turn": turn_idx,
        "query": query,
        "note": note,
        "answer": answer,
        "passed": result["passed"],
        "passed_count": result["passed_count"],
        "total_count": result["total_count"],
        "assertion_results": result["results"],
    }


async def run_scenario(
    scenario: dict,
    system_prompt: str,
    verbose: bool = False,
) -> dict:
    """Прогнать полный сценарий (все turn'ы)."""
    turns = scenario["turns"]
    data = scenario["data"]
    history: list[str] = []

    turn_results = []
    all_passed = True
    total_assertions = 0
    passed_assertions = 0

    for i, turn in enumerate(turns):
        result = await run_turn(
            system_prompt=system_prompt,
            data=data,
            query=turn["query"],
            history=history,
            turn_idx=i + 1,
            note=turn.get("note", ""),
            assertions=turn["assert"],
            verbose=verbose,
        )
        turn_results.append(result)

        # Добавляем в историю для следующего turn'а
        history.append(turn["query"])
        # Также добавляем ответ модели, если нужно (для полноты контекста)
        # Но в session.py в refine_history кладут только user queries, не ответы.
        # Так что оставляем только query.

        if not result.get("passed", False):
            all_passed = False

        if "passed_count" in result:
            passed_assertions += result["passed_count"]
        if "total_count" in result:
            total_assertions += result["total_count"]

    return {
        "id": scenario["id"],
        "title": scenario["title"],
        "persona": scenario["persona"],
        "total_turns": len(turns),
        "all_passed": all_passed,
        "passed_assertions": passed_assertions,
        "total_assertions": total_assertions,
        "turns": turn_results,
    }


# ── Main ────────────────────────────────────────────────────────


def print_report(results: list[dict], verbose: bool = False):
    """Вывести человекочитаемый отчёт."""
    print("\n" + "=" * 70)
    print("  📋 ОТЧЁТ MULTI-TURN СЦЕНАРИЕВ")
    print("=" * 70)

    total_passed = 0
    total_turns = 0
    total_assertions_pass = 0
    total_assertions_all = 0

    for r in results:
        status = "✅" if r["all_passed"] else "❌"
        print(f"\n  {status} {r['id']}: {r['title']}")
        print(f"     Persona: {r['persona']}")
        print(f"     Turns: {r['total_turns']} | "
              f"Assertions: {r['passed_assertions']}/{r['total_assertions']}")

        for t in r["turns"]:
            if verbose or not t.get("passed", False):
                if t.get("answer") is None and t.get("error"):
                    print(f"       ❌ [{t['turn']}] {t['query']} — {t['error']}")
                    continue
                t_status = "✅" if t.get("passed", False) else "❌"
                print(f"       {t_status} [{t['turn']}] {t['query']}")
                if not t.get("passed", False):
                    for ar in t.get("assertion_results", []):
                        if not ar["passed"]:
                            print(f"           ❌ {ar['reason']}")
                if verbose and t.get("answer"):
                    preview = t["answer"][:200].replace("\n", " ")
                    print(f"           Ответ: {preview}...")

        if r["all_passed"]:
            total_passed += 1
        total_turns += r["total_turns"]
        total_assertions_pass += r["passed_assertions"]
        total_assertions_all += r["total_assertions"]

    print("\n" + "=" * 70)
    print(f"  Итого: {total_passed}/{len(results)} сценариев пройдено")
    print(f"  Всего turn'ов: {total_turns}")
    print(f"  Assertions: {total_assertions_pass}/{total_assertions_all} ({total_assertions_pass * 100 // max(total_assertions_all, 1)}%)")
    print("=" * 70)


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Multi-turn scenario runner")
    parser.add_argument("--verbose", "-v", action="store_true", help="Подробный вывод")
    parser.add_argument("--scenario", "-s", type=str, help="ID сценария (s1, s2...)")
    parser.add_argument("--json", action="store_true", help="Вывод в JSON")
    args = parser.parse_args()

    # Load answer prompt
    if not ANSWER_PROMPT_FILE.exists():
        print(f"ERROR: {ANSWER_PROMPT_FILE} not found")
        sys.exit(1)
    system_prompt = ANSWER_PROMPT_FILE.read_text().strip()

    # Load scenarios
    scenario_files = sorted(SCENARIOS_DIR.glob("s*.json"))
    if not scenario_files:
        print(f"ERROR: no scenario files in {SCENARIOS_DIR}")
        sys.exit(1)

    scenarios = []
    for f in scenario_files:
        with open(f) as fh:
            scenario = json.load(fh)
            if args.scenario and scenario["id"] != args.scenario:
                continue
            scenarios.append(scenario)

    if not scenarios:
        print(f"Scenario '{args.scenario}' not found")
        sys.exit(1)

    print(f"⏺ Загружено {len(scenarios)} сценариев")
    print(f"⏺ Провайдер: {MODEL}")
    print(f"⏺ Температура: {TEMPERATURE}")
    print(f"⏺ Промпт: {ANSWER_PROMPT_FILE}")

    # Run scenarios
    results = []
    for scenario in scenarios:
        print(f"\n{'─'*70}")
        print(f"  ▶ Сценарий {scenario['id']}: {scenario['title']}")
        result = await run_scenario(scenario, system_prompt, verbose=args.verbose)
        results.append(result)

    # Output
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_report(results, verbose=args.verbose)

    # Exit code: 0 if all passed, 1 if any failed
    if not all(r["all_passed"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
